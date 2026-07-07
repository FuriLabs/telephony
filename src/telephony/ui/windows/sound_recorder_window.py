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
from gi.repository import Gtk, Adw, Gst, GLib  # noqa: E402
from loguru import logger  # noqa: E402

if not Gst.is_initialized():
    Gst.init(None)


class SoundRecorder(Adw.Window):
    """A simple sound recorder dialog for capturing voice notes."""

    def __init__(self, parent_window, on_attach_callback):
        super().__init__(transient_for=parent_window)
        self.on_attach_callback = on_attach_callback
        self.set_modal(True)
        self.set_default_size(360, 450)
        self.set_title(_("Voice Message"))

        self.output_path = None
        self.max_seconds = 120

        self.pipeline = None
        self.player = None
        self.toast_overlay = None

        self.is_recording = False
        self.is_playing = False
        self.start_time = 0
        self.timer_id = None

        self._setup_ui()
        self.connect("close-request", self._on_close_request)

    def _setup_ui(self):
        """Build the UI components."""
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

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

        self.lbl_timer = Gtk.Label(label="00:00")
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

        self.lbl_duration = Gtk.Label(label="00:00")
        self.lbl_duration.add_css_class("title-2")
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

    def _update_timer(self):
        """Update recording timer."""
        if not self.is_recording:
            return False

        now = time.time()
        diff = now - self.start_time

        if diff >= self.max_seconds:
            self._stop_recording()
            return False

        mins = int(diff // 60)
        secs = int(diff % 60)
        self.lbl_timer.set_label(f"{mins:02d}:{secs:02d}")
        return True

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

            self.pipeline.set_state(Gst.State.PLAYING)
            self.is_recording = True
            self.start_time = time.time()
            self.btn_record.set_icon_name("media-playback-stop-symbolic")

            self.timer_id = GLib.timeout_add(100, self._update_timer)
            logger.info("Recording started")

        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self._show_error(str(e))

    def _stop_recording(self):
        """Stop the recording pipeline."""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None

        self.is_recording = False
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

        self.btn_record.set_icon_name("media-record-symbolic")

        self.lbl_duration.set_label(self.lbl_timer.get_label())
        self.stack.set_visible_child_name("review")

        if self.output_path and os.path.exists(self.output_path):
            size = os.path.getsize(self.output_path)
            logger.info(f"Recording finished. Size: {size} bytes")
            if size > 600 * 1024:
                self._show_error(_(
                    "Recording is too large for MMS (Max 600KB). "
                    "Please Retake shorter."
                ))

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

            bus = self.player.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_player_message)

            self.player.set_state(Gst.State.PLAYING)
            self.is_playing = True
            self.btn_play.set_icon_name("media-playback-pause-symbolic")
        except Exception as e:
            logger.error(f"Playback failed: {e}")

    def _stop_playback(self):
        """Stop audio playback."""
        if self.player:
            self.player.set_state(Gst.State.NULL)
            self.player = None
        self.is_playing = False
        self.btn_play.set_icon_name("media-playback-start-symbolic")

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
        self.lbl_timer.set_label("00:00")
        self.stack.set_visible_child_name("record")

    def _on_attach_clicked(self, btn):
        """Handle attach button click."""
        self._stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            if self.on_attach_callback:
                self.on_attach_callback(self.output_path)
        GLib.idle_add(lambda: self.close() or False)

    def _on_cancel_clicked(self, btn):
        """Handle cancel button click."""
        GLib.idle_add(lambda: self.close() or False)

    def _on_close_request(self, win):
        """Handle window close request."""
        self._stop_recording()
        self._stop_playback()
        return False

    def _show_error(self, message):
        """Show an error toast."""
        toast = Adw.Toast.new(message)
        self.toast_overlay.add_toast(toast)
