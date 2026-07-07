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

if not Gst.is_initialized():
    Gst.init(None)


class CameraVideo(Adw.Window):
    """Camera window for recording videos."""

    def __init__(self, parent_window, on_attach_callback):
        super().__init__(transient_for=parent_window)

        registry = Gst.Registry.get()
        droidvdec = registry.lookup_feature("droidvdec")
        if droidvdec:
            droidvdec.set_rank(Gst.Rank.NONE)
            logger.info("[Camera-Video] Disabled droidvdec to force software decoding.")

        self.on_attach_callback = on_attach_callback
        self.set_modal(True)
        self.set_default_size(360, 600)
        self.set_title(_("Record Video"))

        self.output_path = None
        self.toast_overlay = None

        self.pipeline = None
        self.player = None
        self.is_recording = False
        self.start_time = 0
        self.timer_id = None
        self.max_seconds = 8

        self.retry_count = 0
        self.max_retries = 3

        self._setup_ui()
        self.connect("close-request", self._on_close_request)

        GLib.timeout_add(200, self._start_viewfinder)

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
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_cancel_clicked(b) or False))
        header.pack_start(btn_cancel)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        content.append(self.stack)

        self.page_capture = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card_box.add_css_class("card")
        card_box.set_hexpand(True)
        card_box.set_vexpand(True)
        card_box.set_margin_top(10)
        card_box.set_margin_bottom(10)
        card_box.set_margin_start(10)
        card_box.set_margin_end(10)
        card_box.set_overflow(Gtk.Overflow.HIDDEN)

        self.viewfinder_widget = Gtk.Picture()
        self.viewfinder_widget.set_can_shrink(True)
        self.viewfinder_widget.set_hexpand(True)
        self.viewfinder_widget.set_vexpand(True)
        self.viewfinder_widget.set_content_fit(Gtk.ContentFit.CONTAIN)

        card_box.append(self.viewfinder_widget)
        self.page_capture.append(card_box)

        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ctrl_box.set_halign(Gtk.Align.CENTER)
        ctrl_box.set_margin_top(20)
        ctrl_box.set_margin_bottom(20)

        self.lbl_timer = Gtk.Label(label="00:00 / 00:15")
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
        self.btn_record.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_record_toggle(b) or False))
        ctrl_box.append(self.btn_record)

        self.page_capture.append(ctrl_box)
        self.stack.add_named(self.page_capture, "capture")

        self.page_review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        review_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        review_card.add_css_class("card")
        review_card.set_hexpand(True)
        review_card.set_vexpand(True)
        review_card.set_margin_top(10)
        review_card.set_margin_bottom(10)
        review_card.set_margin_start(10)
        review_card.set_margin_end(10)
        review_card.set_overflow(Gtk.Overflow.HIDDEN)

        self.review_widget = Gtk.Picture()
        self.review_widget.set_can_shrink(True)
        self.review_widget.set_hexpand(True)
        self.review_widget.set_vexpand(True)
        self.review_widget.set_content_fit(Gtk.ContentFit.CONTAIN)

        review_card.append(self.review_widget)
        self.page_review.append(review_card)

        act_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        act_box.set_halign(Gtk.Align.CENTER)
        act_box.set_margin_top(20)
        act_box.set_margin_bottom(20)

        self.btn_play = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.btn_play.add_css_class("circular")
        self.btn_play.add_css_class("suggested-action")
        self.btn_play.set_size_request(60, 60)
        self.btn_play.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_play_toggle(b) or False))

        b_p_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        b_p_box.set_halign(Gtk.Align.CENTER)
        b_p_box.append(self.btn_play)
        act_box.append(b_p_box)

        row_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)

        btn_retake = Gtk.Button(label=_("Retake"))
        btn_retake.add_css_class("pill")
        btn_retake.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_retake_clicked(b) or False))
        row_btns.append(btn_retake)

        btn_attach = Gtk.Button(label=_("Attach Video"))
        btn_attach.add_css_class("pill")
        btn_attach.add_css_class("suggested-action")
        btn_attach.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_attach_clicked(b) or False))
        row_btns.append(btn_attach)

        act_box.append(row_btns)
        self.page_review.append(act_box)
        self.stack.add_named(self.page_review, "review")

    def _update_timer(self):
        """Update recording timer."""
        if not self.is_recording:
            return False

        now = time.time()
        diff = now - self.start_time

        if diff >= self.max_seconds:
            self._stop_recording()
            return False

        secs = int(diff)
        self.lbl_timer.set_label(f"00:{secs:02d} / 00:{self.max_seconds}")
        return True

    def _start_viewfinder(self):
        """Start the camera viewfinder pipeline."""
        self._stop_pipeline()

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

            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_viewfinder_message)

            self.pipeline.set_state(Gst.State.PLAYING)
            return False
        except Exception as e:
            logger.error(f"[Camera-Video] Failed to start viewfinder: {e}")
            self._show_error(f"Camera Error: {e}")
            return False

    def _on_viewfinder_message(self, bus, message):
        """Handle viewfinder messages."""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Video] Viewfinder error: {err} : {debug}")

    def _stop_pipeline(self):
        """Stop the GStreamer pipeline."""
        self.viewfinder_widget.set_paintable(None)
        if self.pipeline:
            logger.debug("[Camera-Video] Stopping pipeline...")
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

    def _on_record_toggle(self, btn):
        """Toggle recording state."""
        if self.is_recording:
            self._stop_recording()
        else:
            self.btn_record.set_sensitive(False)
            self._stop_pipeline()
            self.retry_count = 0
            GLib.timeout_add(500, self._attempt_recording)

    def _attempt_recording(self):
        """Attempt to start the recording pipeline."""
        logger.info(f"[Camera-Video] Attempting recording (Try {self.retry_count + 1}/{self.max_retries})...")
        self._start_recording()
        return False

    def _start_recording(self):
        """Initialize and start the GStreamer recording pipeline."""
        self.output_path = os.path.join(tempfile.gettempdir(), f"video_{int(time.time())}.mkv")

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

            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_record_message)

            self.pipeline.set_state(Gst.State.PLAYING)
            self.is_recording = True
            self.start_time = time.time()
            self.btn_record.set_icon_name("media-playback-stop-symbolic")
            self.btn_record.set_sensitive(True)

            self.timer_id = GLib.timeout_add(100, self._update_timer)

        except Exception as e:
            logger.error(f"[Camera-Video] Failed to start recording: {e}")
            self._handle_record_failure(str(e))

    def _handle_record_failure(self, error_msg):
        """Handle failure with retries."""
        self._stop_pipeline()
        self.is_recording = False

        self.retry_count += 1
        if self.retry_count < self.max_retries:
            logger.warning(f"[Camera-Video] Recording failed, retrying in 1s... ({error_msg})")
            GLib.timeout_add(1000, self._attempt_recording)
        else:
            logger.error(f"[Camera-Video] All retries failed. Last error: {error_msg}")
            self._show_error(_("Failed to record video: {error_msg}").format(error_msg=error_msg))
            self.btn_record.set_sensitive(True)
            self.btn_record.set_icon_name("media-record-symbolic")
            self._restart_viewfinder_safe()

    def _stop_recording(self):
        """Stop the recording pipeline by sending EOS to the file branch."""
        self.is_recording = False
        self.btn_record.set_icon_name("media-record-symbolic")
        self.btn_record.set_sensitive(False)

        if self.timer_id:
            GLib.source_remove(self.timer_id)
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
                GLib.timeout_add(3000, lambda: self._force_stop() if self.pipeline else False)
            else:
                self._force_stop()
        else:
            self._show_review()

    def _on_record_message(self, bus, message):
        """Handle recording messages."""
        t = message.type
        if t == Gst.MessageType.EOS:
            logger.info("[Camera-Video] EOS received, stopping pipeline.")
            self._stop_pipeline()
            self.btn_record.set_sensitive(True)
            GLib.idle_add(self._show_review)
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            msg = f"{err} : {debug}"
            logger.error(f"[Camera-Video] Recording error: {msg}")
            GLib.idle_add(self._handle_record_failure, str(err))

    def _force_stop(self):
        """Force stop the pipeline if EOS times out."""
        logger.warning("[Camera-Video] EOS timeout, forcing stop.")
        self._stop_pipeline()
        self.btn_record.set_sensitive(True)
        self._show_review()
        return False

    def _show_review(self):
        """Show the review page."""
        if os.path.exists(self.output_path):
            size = os.path.getsize(self.output_path)
            logger.info(f"[Camera-Video] Video recorded. Size: {size}")
            if size < 1000:
                self._show_error(_("Recording failed (File empty)."))
                self._on_retake_clicked(None)
                return
            elif size > 300 * 1024:
                self._show_error(_("Large video ({size}KB). Will be compressed on send.").format(size=size // 1024))

        self.stack.set_visible_child_name("review")

    def _on_play_toggle(self, btn):
        """Toggle playback state."""
        if self.player and self.player.get_state(0)[1] == Gst.State.PLAYING:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        """Start video playback."""
        if not self.output_path or not os.path.exists(self.output_path):
            return

        if os.path.getsize(self.output_path) == 0:
            self._show_error(_("Cannot play empty file."))
            return

        self._stop_playback()
        self._stop_pipeline()

        try:
            pipeline_str = f"playbin uri=file://{self.output_path}"
            self.player = Gst.parse_launch(pipeline_str)

            sink = Gst.ElementFactory.make("gtk4paintablesink", "psink")
            self.player.set_property("video-sink", sink)

            paintable = sink.get_property("paintable")
            self.review_widget.set_paintable(paintable)

            bus = self.player.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_player_message)

            self.player.set_state(Gst.State.PLAYING)
            self.btn_play.set_icon_name("media-playback-pause-symbolic")

        except Exception as e:
            logger.error(f"[Camera-Video] Playback failed: {e}")
            self._show_error(str(e))

    def _stop_playback(self):
        """Stop video playback."""
        if self.player:
            self.player.set_state(Gst.State.NULL)
            self.player = None
        self.btn_play.set_icon_name("media-playback-start-symbolic")

    def _on_player_message(self, bus, message):
        """Handle playback messages."""
        t = message.type
        if t == Gst.MessageType.EOS:
            self._stop_playback()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Video] Playback error: {err} : {debug}")
            self._stop_playback()

    def _restart_viewfinder_safe(self):
        """Restart viewfinder and reset UI state."""
        self.btn_record.set_sensitive(True)
        self._start_viewfinder()

    def _on_retake_clicked(self, btn):
        """Handle retake button click."""
        self._stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            try:
                os.remove(self.output_path)
            except Exception as e:
                logger.warning(f"[Camera-Video] Failed to remove temp file: {e}")
        self.output_path = None
        self.lbl_timer.set_label(f"00:00 / 00:{self.max_seconds}")
        self.stack.set_visible_child_name("capture")

        GLib.timeout_add(500, self._restart_viewfinder_safe)

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
        self._stop_pipeline()
        self._stop_playback()
        return False

    def _show_error(self, message):
        """Show an error toast."""
        toast = Adw.Toast.new(message)
        self.toast_overlay.add_toast(toast)
