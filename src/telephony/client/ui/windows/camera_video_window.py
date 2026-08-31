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
from gettext import gettext as _

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, Gst, GLib
from telephony.shared.utils.log_utils import logger

from telephony.shared.constants import (VIEWFINDER_START_DELAY_MS, PLAYBACK_PROGRESS_INTERVAL_MS, EOS_TIMEOUT_MS, PROGRESS_BAR_WIDTH)
from telephony.client.ui.windows.media_window_base import MediaCaptureWindow
from telephony.client.ui.widgets.common_widget import close_sheet_page

RECORD_START_DELAY_MS = 500
RECORD_RETRY_DELAY_MS = 1000
MAX_RECORD_RETRIES = 3
RECORD_TIMER_INTERVAL_MS = 100
MAX_RECORD_SECONDS = 8
MIN_VALID_VIDEO_BYTES = 1000
LARGE_VIDEO_WARN_BYTES = 300 * 1024


class CameraVideo(MediaCaptureWindow):
    """Camera window for recording videos."""

    def __init__(self, parent_window, on_attach_callback):
        super().__init__()
        self.request_capture_height(parent_window)

        registry = Gst.Registry.get()
        droidvdec = registry.lookup_feature("droidvdec")
        if droidvdec:
            droidvdec.set_rank(Gst.Rank.NONE)
            logger.info("[Camera-Video] Disabled droidvdec to force software decoding.")

        self.on_attach_callback = on_attach_callback
        self._attached = False
        self.set_title(_("Record Video"))

        self.output_path = None
        self.toast_overlay = None

        self.pipeline = None
        self.bus = None
        self.bus_handler_id = None
        self.player = None
        self.player_bus = None
        self.player_bus_handler_id = None
        self.is_recording = False
        self.start_time = 0
        self.timer_id = None
        self.eos_timeout_id = None
        self.progress_timer_id = None
        self.max_seconds = MAX_RECORD_SECONDS

        self.retry_count = 0
        self.max_retries = MAX_RECORD_RETRIES

        self.setup_ui()
        self.connect("hidden", self.on_closed)

        self.schedule_timeout(VIEWFINDER_START_DELAY_MS, self.start_viewfinder)

    def setup_ui(self):
        """Build the UI components."""
        self.toast_overlay = Adw.ToastOverlay()
        self.set_child(self.toast_overlay)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(content)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        content.append(header)


        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        content.append(self.stack)

        self.page_capture = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card_box.add_css_class("preview-round")
        card_box.set_hexpand(True)
        card_box.set_vexpand(True)
        card_box.set_margin_top(10)
        card_box.set_margin_bottom(10)
        card_box.set_margin_start(10)
        card_box.set_margin_end(10)
        card_box.set_overflow(Gtk.Overflow.HIDDEN)

        self.viewfinder_widget = Gtk.Picture()
        self.viewfinder_widget.add_css_class("preview-round")
        self.viewfinder_widget.set_overflow(Gtk.Overflow.HIDDEN)
        self.viewfinder_widget.set_can_shrink(True)
        self.viewfinder_widget.set_hexpand(True)
        self.viewfinder_widget.set_vexpand(True)
        self.viewfinder_widget.set_content_fit(Gtk.ContentFit.CONTAIN)

        card_box.append(self.letterbox(self.viewfinder_widget))
        self.page_capture.append(card_box)

        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ctrl_box.set_halign(Gtk.Align.CENTER)
        ctrl_box.set_margin_top(20)
        ctrl_box.set_margin_bottom(20)

        self.lbl_timer = Gtk.Label(label=self.recording_timer_text(0))
        self.lbl_timer.add_css_class("title-2")
        self.lbl_timer.add_css_class("numeric")
        ctrl_box.append(self.lbl_timer)

        self.btn_record = Gtk.Button()
        self.btn_record.set_icon_name("media-record-symbolic")
        self.btn_record.set_size_request(80, 80)
        self.btn_record.add_css_class("video-record-button")
        self.btn_record.add_css_class("circular")
        self.btn_record.set_halign(Gtk.Align.CENTER)
        self.btn_record.set_valign(Gtk.Align.CENTER)
        self.btn_record.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_record_toggle(b) or False))
        ctrl_box.append(self.btn_record)

        self.page_capture.append(ctrl_box)
        self.stack.add_named(self.page_capture, "capture")

        self.page_review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        review_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        review_card.add_css_class("preview-round")
        review_card.set_hexpand(True)
        review_card.set_vexpand(True)
        review_card.set_margin_top(10)
        review_card.set_margin_bottom(10)
        review_card.set_margin_start(10)
        review_card.set_margin_end(10)
        review_card.set_overflow(Gtk.Overflow.HIDDEN)

        self.review_widget = Gtk.Picture()
        self.review_widget.add_css_class("preview-round")
        self.review_widget.set_overflow(Gtk.Overflow.HIDDEN)
        self.review_widget.set_can_shrink(True)
        self.review_widget.set_hexpand(True)
        self.review_widget.set_vexpand(True)
        self.review_widget.set_content_fit(Gtk.ContentFit.CONTAIN)

        review_card.append(self.letterbox(self.review_widget))
        self.page_review.append(review_card)

        act_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        act_box.set_halign(Gtk.Align.CENTER)
        act_box.set_margin_top(20)
        act_box.set_margin_bottom(20)

        self.btn_play = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.btn_play.add_css_class("circular")
        self.btn_play.add_css_class("suggested-action")
        self.btn_play.set_size_request(60, 60)
        self.btn_play.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_play_toggle(b) or False))

        b_p_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        b_p_box.set_halign(Gtk.Align.CENTER)
        b_p_box.append(self.btn_play)
        act_box.append(b_p_box)

        self.lbl_progress = Gtk.Label(label=self.playback_progress_text(0, 0))
        self.lbl_progress.add_css_class("numeric")
        self.lbl_progress.add_css_class("dim-label")
        act_box.append(self.lbl_progress)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_size_request(PROGRESS_BAR_WIDTH, -1)
        self.progress_bar.set_halign(Gtk.Align.CENTER)
        act_box.append(self.progress_bar)

        row_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)

        btn_retake = Gtk.Button(label=_("Retake"))
        btn_retake.add_css_class("pill")
        btn_retake.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_retake_clicked(b) or False))
        row_btns.append(btn_retake)

        btn_attach = Gtk.Button(label=_("Attach Video"))
        btn_attach.add_css_class("pill")
        btn_attach.add_css_class("suggested-action")
        btn_attach.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_attach_clicked(b) or False))
        row_btns.append(btn_attach)

        act_box.append(row_btns)
        self.page_review.append(act_box)
        self.stack.add_named(self.page_review, "review")

    def start_viewfinder(self):
        """Start the camera viewfinder pipeline."""
        if self._closed:
            return False

        self.stop_pipeline()

        try:
            pipeline_str = (
                "droidcamsrc camera_device=0 mode=2 ! videoconvert ! videoflip video-direction=auto ! gtk4paintablesink name=sink"
            )
            logger.info(f"[Camera-Video] Starting viewfinder: {pipeline_str}")

            self.pipeline = Gst.parse_launch(pipeline_str)

            sink = self.pipeline.get_by_name("sink")
            if sink:
                paintable = sink.get_property("paintable")
                self.viewfinder_widget.set_paintable(paintable)
                self.reveal_on_first_frame(self.viewfinder_widget)

            self.bus, self.bus_handler_id = self.watch_bus(self.pipeline, self.on_viewfinder_message)

            self.pipeline.set_state(Gst.State.PLAYING)
            return False
        except Exception as e:
            logger.error(f"[Camera-Video] Failed to start viewfinder: {e}")
            self.show_error(_("Error: {e}").format(e=e))
            return False

    def on_viewfinder_message(self, bus, message):
        """Handle viewfinder messages."""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Video] Viewfinder error: {err} : {debug}")

    def stop_pipeline(self):
        """Stop the GStreamer pipeline and release its bus watch."""
        self.viewfinder_widget.set_paintable(None)
        if self.bus:
            if self.bus_handler_id:
                self.bus.disconnect(self.bus_handler_id)
            self.bus.remove_signal_watch()
            self.bus = None
            self.bus_handler_id = None
        if self.pipeline:
            logger.debug("[Camera-Video] Stopping pipeline...")
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        if self.timer_id:
            self.cancel_timeout(self.timer_id)
            self.timer_id = None

    def on_record_toggle(self, btn):
        """Toggle recording state."""
        if self.is_recording:
            self.stop_recording()
        else:
            self.btn_record.set_sensitive(False)
            self.stop_pipeline()
            self.retry_count = 0
            self.schedule_timeout(RECORD_START_DELAY_MS, self.attempt_recording)

    def attempt_recording(self):
        """Attempt to start the recording pipeline."""
        logger.info(f"[Camera-Video] Attempting recording (Try {self.retry_count + 1}/{self.max_retries})...")
        self.start_recording()
        return False

    def start_recording(self):
        """Initialize and start the GStreamer recording pipeline."""
        self.output_path = os.path.join(self.capture_dir(), f"video_{int(time.time())}.mkv")

        pipeline_str = (
            f"matroskamux name=mux ! filesink location={self.output_path} "
            f"droidcamsrc camera_device=0 mode=2 ! tee name=t "
            f"t. ! queue ! videoconvert ! videoflip video-direction=auto ! gtk4paintablesink name=sink "
            f"t. ! queue name=record_queue ! videoconvert ! videoflip video-direction=auto ! videoscale ! videoconvert ! "
            f"jpegenc ! mux. "
            f"autoaudiosrc ! queue name=audio_queue ! audioconvert ! droidaenc ! mux."
        )

        try:
            logger.info(f"[Camera-Video] Starting recording: {pipeline_str}")
            self.pipeline = Gst.parse_launch(pipeline_str)

            sink = self.pipeline.get_by_name("sink")
            if sink:
                paintable = sink.get_property("paintable")
                self.viewfinder_widget.set_paintable(paintable)
                self.reveal_on_first_frame(self.viewfinder_widget)

            self.bus, self.bus_handler_id = self.watch_bus(self.pipeline, self.on_record_message)

            self.pipeline.set_state(Gst.State.PLAYING)
            self.is_recording = True
            self.start_time = time.time()
            self.btn_record.set_icon_name("media-playback-stop-symbolic")
            self.btn_record.set_sensitive(True)

            self.timer_id = self.schedule_timeout(RECORD_TIMER_INTERVAL_MS, self.update_timer)

        except Exception as e:
            logger.error(f"[Camera-Video] Failed to start recording: {e}")
            self.handle_record_failure(str(e))

    def handle_record_failure(self, error_msg):
        """Handle failure with retries."""
        self.stop_pipeline()
        self.is_recording = False

        if self._closed:
            return

        self.retry_count += 1
        if self.retry_count < self.max_retries:
            logger.warning(f"[Camera-Video] Recording failed, retrying in 1s... ({error_msg})")
            self.schedule_timeout(RECORD_RETRY_DELAY_MS, self.attempt_recording)
        else:
            logger.error(f"[Camera-Video] All retries failed. Last error: {error_msg}")
            self.show_error(_("Failed to record video: {error_msg}").format(error_msg=error_msg))
            self.btn_record.set_sensitive(True)
            self.btn_record.set_icon_name("media-record-symbolic")
            self.restart_viewfinder_safe()

    def stop_recording(self):
        """Stop the recording pipeline by sending EOS to the file branch."""
        self.is_recording = False
        self.btn_record.set_icon_name("media-record-symbolic")
        self.btn_record.set_sensitive(False)

        if self.timer_id:
            self.cancel_timeout(self.timer_id)
            self.timer_id = None

        if self.pipeline:
            logger.info("[Camera-Video] Stopping recording (injecting EOS to queues)...")

            record_queue = self.pipeline.get_by_name("record_queue")
            audio_queue = self.pipeline.get_by_name("audio_queue")

            eos_sent = False

            if record_queue:
                sink_pad = record_queue.get_static_pad("sink")
                if sink_pad:
                    sink_pad.send_event(Gst.Event.new_eos())
                    eos_sent = True

            if audio_queue:
                sink_pad = audio_queue.get_static_pad("sink")
                if sink_pad:
                    sink_pad.send_event(Gst.Event.new_eos())
                    eos_sent = True

            if eos_sent:
                self.eos_timeout_id = self.schedule_timeout(EOS_TIMEOUT_MS, self.force_stop)
            else:
                self.force_stop()
        else:
            self.show_review()

    def on_record_message(self, bus, message):
        """Handle recording messages."""
        t = message.type
        if t == Gst.MessageType.EOS:
            logger.info("[Camera-Video] EOS received, stopping pipeline.")
            if self.eos_timeout_id:
                self.cancel_timeout(self.eos_timeout_id)
                self.eos_timeout_id = None
            self.stop_pipeline()
            if self._closed:
                return
            self.btn_record.set_sensitive(True)
            GLib.idle_add(self.show_review)
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            msg = f"{err} : {debug}"
            logger.error(f"[Camera-Video] Recording error: {msg}")
            GLib.idle_add(self.handle_record_failure, str(err))

    def force_stop(self):
        """Force stop the pipeline if EOS times out."""
        logger.warning("[Camera-Video] EOS timeout, forcing stop.")
        self.eos_timeout_id = None
        self.stop_pipeline()
        if self._closed:
            return False
        self.btn_record.set_sensitive(True)
        self.show_review()
        return False

    def show_review(self):
        """Show the review page."""
        if self._closed:
            return
        if self.output_path and os.path.exists(self.output_path):
            size = os.path.getsize(self.output_path)
            logger.info(f"[Camera-Video] Video recorded. Size: {size}")
            if size < MIN_VALID_VIDEO_BYTES:
                self.show_error(_("Recording failed (File empty)."))
                self.on_retake_clicked(None)
                return
            elif size > LARGE_VIDEO_WARN_BYTES:
                self.show_error(_("Large video ({size}KB). Will be compressed on send.").format(size=size // 1024))

        self.stack.set_visible_child_name("review")

    def on_play_toggle(self, btn):
        """Toggle playback state."""
        if self.player and self.player.get_state(0)[1] == Gst.State.PLAYING:
            self.stop_playback()
        else:
            self.start_playback()

    def start_playback(self):
        """Start video playback."""
        if not self.output_path or not os.path.exists(self.output_path):
            return

        if os.path.getsize(self.output_path) == 0:
            self.show_error(_("Cannot play empty file."))
            return

        self.stop_playback()
        self.stop_pipeline()

        try:
            pipeline_str = f"playbin uri=file://{self.output_path}"
            self.player = Gst.parse_launch(pipeline_str)

            sink = Gst.ElementFactory.make("gtk4paintablesink", "psink")
            self.player.set_property("video-sink", sink)

            paintable = sink.get_property("paintable")
            self.review_widget.set_paintable(paintable)
            self.reveal_on_first_frame(self.review_widget)

            self.player_bus, self.player_bus_handler_id = self.watch_bus(self.player, self.on_player_message)

            self.player.set_state(Gst.State.PLAYING)
            self.btn_play.set_icon_name("media-playback-pause-symbolic")

            self.progress_timer_id = self.schedule_timeout(
                PLAYBACK_PROGRESS_INTERVAL_MS, self.update_playback_progress)

        except Exception as e:
            logger.error(f"[Camera-Video] Playback failed: {e}")
            self.show_error(str(e))

    def stop_playback(self):
        """Stop video playback and release playback resources."""
        if self.progress_timer_id:
            self.cancel_timeout(self.progress_timer_id)
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
        self.review_widget.set_visible(False)
        self.btn_play.set_icon_name("media-playback-start-symbolic")
        self.lbl_progress.set_label(self.playback_progress_text(0, 0))
        self.progress_bar.set_fraction(0.0)

    def on_player_message(self, bus, message):
        """Handle playback messages.

        The end of the video is not the end of the player: tearing it
        down leaves the picture holding a dead paintable, which paints
        black. Rewound and paused it keeps the first frame up as a
        poster, and play starts it again from the top.
        """
        t = message.type
        if t == Gst.MessageType.EOS:
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.player.set_state(Gst.State.PAUSED)
            if self.progress_timer_id:
                self.cancel_timeout(self.progress_timer_id)
                self.progress_timer_id = None
            self.btn_play.set_icon_name("media-playback-start-symbolic")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Video] Playback error: {err} : {debug}")
            self.stop_playback()

    def restart_viewfinder_safe(self):
        """Restart viewfinder and reset UI state."""
        if self._closed:
            return False
        self.btn_record.set_sensitive(True)
        self.start_viewfinder()
        return False

    def on_retake_clicked(self, btn):
        """Handle retake button click."""
        self.stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            try:
                os.remove(self.output_path)
            except Exception as e:
                logger.warning(f"[Camera-Video] Failed to remove temp file: {e}")
        self.output_path = None
        self.lbl_timer.set_label(self.recording_timer_text(0))
        self.stack.set_visible_child_name("capture")

        self.schedule_timeout(RECORD_START_DELAY_MS, self.restart_viewfinder_safe)

    def on_attach_clicked(self, btn):
        """Handle attach button click."""
        self.stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            if self.on_attach_callback:
                self._attached = True
                self.on_attach_callback(self.output_path)
        GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)

    def on_closed(self, _dialog):
        """Tear down capture state when the sheet closes."""
        self._closed = True
        self.cancel_tracked_timeouts()
        self.stop_pipeline()
        self.stop_playback()
        self.discard_unattached_output()

    def discard_unattached_output(self):
        """Delete the recorded file when the window closes without attaching."""
        if self._attached or not self.output_path:
            return
        try:
            if os.path.exists(self.output_path):
                os.remove(self.output_path)
        except Exception as e:
            logger.warning(f"[Camera-Video] Temp file removal failed: {e}")
