# Copyright (C) 2026 alaraajavamma aki@urheiluaki.fi
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import time
import tempfile
from gettext import gettext as _

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, Gst, GLib
from loguru import logger

from ...constants import (
    PLAYBACK_PROGRESS_INTERVAL_MS,
    EOS_TIMEOUT_MS,
    PROGRESS_BAR_WIDTH,
)
from .media_window_base import MediaCaptureWindow, _format_duration

MAX_RECORD_SECONDS = 120
RECORD_TIMER_INTERVAL_MS = 1000
MAX_MMS_AUDIO_BYTES = 600 * 1024


def _format_size_limit(size_bytes):
    """Format a byte limit as a short kB or MB label."""
    if size_bytes >= 1024 * 1024:
        mb = size_bytes / (1024 * 1024)
        text = f"{mb:.1f}".rstrip("0").rstrip(".")
        return f"{text} MB"
    return f"{size_bytes // 1024} kB"


class SoundRecorder(MediaCaptureWindow):
    """A simple sound recorder dialog for capturing voice notes."""

    def __init__(self, parent_window, on_attach_callback, max_bytes=MAX_MMS_AUDIO_BYTES):
        super().__init__()
        self.on_attach_callback = on_attach_callback
        self.max_bytes = max_bytes
        self._attached = False
        self.set_modal(True)
        self.set_content_width(360)
        self.set_content_height(450)
        self.set_title(_("Voice Message"))

        self.output_path = None
        self.max_seconds = MAX_RECORD_SECONDS

        self.pipeline = None
        self.bus = None
        self.bus_handler_id = None
        self.player = None
        self.player_bus = None
        self.player_bus_handler_id = None
        self.toast_overlay = None

        self.is_recording = False
        self.is_playing = False
        self.start_time = 0
        self.record_elapsed = 0
        self.timer_id = None
        self.eos_timeout_id = None
        self.progress_timer_id = None

        self._setup_ui()
        self.connect("closed", self._on_closed)

    def _setup_ui(self):
        """Build the UI components."""
        self.toast_overlay = Adw.ToastOverlay()
        self.set_child(self.toast_overlay)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(content)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        content.append(header)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._on_cancel_clicked(b) or False))
        header.pack_start(btn_cancel)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)
        content.append(self.stack)

        page_record = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page_record.set_valign(Gtk.Align.CENTER)
        page_record.set_halign(Gtk.Align.CENTER)
        page_record.set_margin_bottom(40)

        self.lbl_timer = Gtk.Label(label=self._recording_timer_text(0))
        self.lbl_timer.add_css_class("display-1")
        self.lbl_timer.add_css_class("numeric")
        page_record.append(self.lbl_timer)

        lbl_hint = Gtk.Label(label=_("Tap to Record"))
        lbl_hint.add_css_class("dim-label")
        page_record.append(lbl_hint)

        self.btn_record = Gtk.Button()
        self.btn_record.set_icon_name("media-record-symbolic")
        self.btn_record.add_css_class("record-button")
        self.btn_record.add_css_class("circular")
        self.btn_record.set_size_request(80, 80)
        self.btn_record.set_halign(Gtk.Align.CENTER)
        self.btn_record.set_valign(Gtk.Align.CENTER)
        self.btn_record.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._on_record_toggle(b) or False))
        page_record.append(self.btn_record)

        self.stack.add_named(page_record, "record")

        page_review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page_review.set_valign(Gtk.Align.CENTER)
        page_review.set_halign(Gtk.Align.CENTER)
        page_review.set_margin_start(20)
        page_review.set_margin_end(20)

        icon = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
        icon.set_pixel_size(64)
        page_review.append(icon)

        self.lbl_duration = Gtk.Label(label=_format_duration(0))
        self.lbl_duration.add_css_class("title-2")
        self.lbl_duration.add_css_class("numeric")
        page_review.append(self.lbl_duration)

        self.btn_play = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.btn_play.add_css_class("circular")
        self.btn_play.add_css_class("suggested-action")
        self.btn_play.set_size_request(60, 60)
        self.btn_play.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._on_play_toggle(b) or False))

        box_play = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box_play.set_halign(Gtk.Align.CENTER)
        box_play.append(self.btn_play)
        page_review.append(box_play)

        self.lbl_progress = Gtk.Label(label=self._playback_progress_text(0, 0))
        self.lbl_progress.add_css_class("numeric")
        self.lbl_progress.add_css_class("dim-label")
        page_review.append(self.lbl_progress)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_size_request(PROGRESS_BAR_WIDTH, -1)
        self.progress_bar.set_halign(Gtk.Align.CENTER)
        page_review.append(self.progress_bar)

        actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        actions_box.set_margin_top(20)

        btn_attach = Gtk.Button(label=_("Attach Voice Note"))
        btn_attach.add_css_class("pill")
        btn_attach.add_css_class("suggested-action")
        btn_attach.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._on_attach_clicked(b) or False))
        actions_box.append(btn_attach)

        btn_retake = Gtk.Button(label=_("Retake"))
        btn_retake.add_css_class("pill")
        btn_retake.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._on_retake_clicked(b) or False))
        actions_box.append(btn_retake)

        page_review.append(actions_box)

        self.stack.add_named(page_review, "review")

    def _on_record_toggle(self, btn):
        """Toggle recording state."""
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        """Initialize and start the GStreamer recording pipeline."""
        self.output_path = os.path.join(
            tempfile.gettempdir(), f"voice_{int(time.time())}.mp3")

        pipeline_str = (
            "autoaudiosrc ! audioconvert ! audioresample ! "
            f"lamemp3enc target=1 bitrate=32 ! id3v2mux ! "
            f"filesink location={self.output_path}"
        )
        try:
            self.pipeline = Gst.parse_launch(pipeline_str)

            self.bus, self.bus_handler_id = self._watch_bus(
                self.pipeline, self._on_record_message)

            self.pipeline.set_state(Gst.State.PLAYING)
            self.is_recording = True
            self.start_time = time.time()
            self.record_elapsed = 0
            self.btn_record.set_icon_name("media-playback-stop-symbolic")

            self.timer_id = self._schedule_timeout(
                RECORD_TIMER_INTERVAL_MS, self._update_timer)
            logger.info("Recording started")

        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self._show_error(str(e))

    def _stop_recording(self):
        """Stop recording, letting the pipeline flush via EOS before finalizing."""
        was_recording = self.is_recording
        self.is_recording = False

        if self.timer_id:
            self._cancel_timeout(self.timer_id)
            self.timer_id = None

        self.btn_record.set_icon_name("media-record-symbolic")

        if not self.pipeline:
            return

        if was_recording:
            self.record_elapsed = int(time.time() - self.start_time)

        if self._closed:
            self._finalize_recording()
            return

        logger.info("Stopping recording (sending EOS)...")
        self.btn_record.set_sensitive(False)
        self.pipeline.send_event(Gst.Event.new_eos())
        self.eos_timeout_id = self._schedule_timeout(
            EOS_TIMEOUT_MS, self._force_stop_recording)

    def _force_stop_recording(self):
        """Force stop the recording pipeline if EOS times out."""
        logger.warning("Recording EOS timeout, forcing stop.")
        self.eos_timeout_id = None
        self._finalize_recording()
        return False

    def _on_record_message(self, bus, message):
        """Handle recording pipeline bus messages."""
        t = message.type
        if t == Gst.MessageType.EOS:
            logger.info("Recording EOS received, finalizing file.")
            self._finalize_recording()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"Recording error: {err} : {debug}")
            self._finalize_recording()
            if not self._closed:
                self._show_error(str(err))

    def _finalize_recording(self):
        """Tear down the recording pipeline and show the review page."""
        if self.eos_timeout_id:
            self._cancel_timeout(self.eos_timeout_id)
            self.eos_timeout_id = None

        if self.bus:
            if self.bus_handler_id:
                self.bus.disconnect(self.bus_handler_id)
            self.bus.remove_signal_watch()
            self.bus = None
            self.bus_handler_id = None

        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None

        if self._closed:
            return

        self.btn_record.set_sensitive(True)
        self.lbl_duration.set_label(_format_duration(self.record_elapsed))
        self.stack.set_visible_child_name("review")

        if self.output_path and os.path.exists(self.output_path):
            size = os.path.getsize(self.output_path)
            logger.info(f"Recording finished. Size: {size} bytes")
            if size > self.max_bytes:
                limit_text = _format_size_limit(self.max_bytes)
                self._show_error(_(
                    "Recording is too large for MMS (Max {size}). "
                    "Please retake shorter."
                ).format(size=limit_text))

    def _on_play_toggle(self, btn):
        """Toggle playback state."""
        if self.is_playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        """Start audio playback."""
        if not self.output_path or not os.path.exists(self.output_path):
            return

        try:
            self.player = Gst.parse_launch(
                f"playbin uri=file://{self.output_path}")

            self.player_bus, self.player_bus_handler_id = self._watch_bus(
                self.player, self._on_player_message)

            self.player.set_state(Gst.State.PLAYING)
            self.is_playing = True
            self.btn_play.set_icon_name("media-playback-pause-symbolic")

            self.progress_timer_id = self._schedule_timeout(
                PLAYBACK_PROGRESS_INTERVAL_MS, self._update_playback_progress)
        except Exception as e:
            logger.error(f"Playback failed: {e}")

    def _stop_playback(self):
        """Stop audio playback and release playback resources."""
        if self.progress_timer_id:
            self._cancel_timeout(self.progress_timer_id)
            self.progress_timer_id = None
        if self.player_bus:
            if self.player_bus_handler_id:
                self.player_bus.disconnect(self.player_bus_handler_id)
            self.player_bus.remove_signal_watch()
            self.player_bus = None
            self.player_bus_handler_id = None
        if self.player:
            self.player.set_state(Gst.State.NULL)
            self.player = None
        self.is_playing = False
        self.btn_play.set_icon_name("media-playback-start-symbolic")
        self.lbl_progress.set_label(self._playback_progress_text(0, 0))
        self.progress_bar.set_fraction(0.0)

    def _on_player_message(self, bus, message):
        """Handle GStreamer bus messages."""
        t = message.type
        if t == Gst.MessageType.EOS:
            self._stop_playback()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"Playback error: {err} : {debug}")
            self._stop_playback()

    def _on_retake_clicked(self, btn):
        """Handle retake button click."""
        self._stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            try:
                os.remove(self.output_path)
            except Exception as e:
                logger.warning(
                    f"[SoundRecorder] Failed to remove temp file: {e}")
        self.output_path = None
        self.record_elapsed = 0
        self.lbl_timer.set_label(self._recording_timer_text(0))
        self.stack.set_visible_child_name("record")

    def _on_attach_clicked(self, btn):
        """Handle attach button click."""
        self._stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            if self.on_attach_callback:
                self._attached = True
                self.on_attach_callback(self.output_path)
        GLib.idle_add(lambda: self.close() or False)

    def _on_cancel_clicked(self, btn):
        """Handle cancel button click."""
        GLib.idle_add(lambda: self.close() or False)

    def _on_closed(self, _dialog):
        """Tear down capture state when the sheet closes."""
        self._closed = True
        self._cancel_tracked_timeouts()
        self._stop_recording()
        self._stop_playback()
        self._discard_unattached_output()

    def _discard_unattached_output(self):
        """Delete the recorded file when the window closes without attaching."""
        if self._attached or not self.output_path:
            return
        try:
            if os.path.exists(self.output_path):
                os.remove(self.output_path)
        except Exception as e:
            logger.warning(f"[SoundRecorder] Temp file removal failed: {e}")
