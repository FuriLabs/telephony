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


class CameraPhoto(Adw.Window):
    """Camera window for taking photos."""

    def __init__(self, parent_window, on_attach_callback):
        super().__init__(transient_for=parent_window)
        self.on_attach_callback = on_attach_callback
        self.set_modal(True)
        self.set_default_size(360, 600)
        self.set_title(_("Take Picture"))

        self.output_path = None
        self.temp_capture_path = None

        self.pipeline = None
        self.toast_overlay = None

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

        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        ctrl_box.set_halign(Gtk.Align.CENTER)
        ctrl_box.set_margin_top(20)
        ctrl_box.set_margin_bottom(20)

        self.btn_shutter = Gtk.Button()
        self.btn_shutter.set_icon_name("camera-photo-symbolic")
        self.btn_shutter.set_size_request(80, 80)
        self.btn_shutter.add_css_class("shutter-button")
        self.btn_shutter.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_shutter_clicked(b) or False))
        ctrl_box.append(self.btn_shutter)

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

        self.review_image = Gtk.Picture()
        self.review_image.set_can_shrink(True)
        self.review_image.set_hexpand(True)
        self.review_image.set_vexpand(True)
        self.review_image.set_content_fit(Gtk.ContentFit.CONTAIN)

        review_card.append(self.review_image)
        self.page_review.append(review_card)

        act_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        act_box.set_halign(Gtk.Align.CENTER)
        act_box.set_margin_top(20)
        act_box.set_margin_bottom(20)

        btn_retake = Gtk.Button(label=_("Retake"))
        btn_retake.add_css_class("pill")
        btn_retake.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_retake_clicked(b) or False))
        act_box.append(btn_retake)

        btn_attach = Gtk.Button(label=_("Attach Photo"))
        btn_attach.add_css_class("pill")
        btn_attach.add_css_class("suggested-action")
        btn_attach.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_attach_clicked(b) or False))
        act_box.append(btn_attach)

        self.page_review.append(act_box)
        self.stack.add_named(self.page_review, "review")

    def _start_viewfinder(self):
        """Start the camera viewfinder pipeline."""
        self._stop_pipeline()

        try:
            f = Gst.ElementFactory.find("gtk4paintablesink")
            if not f:
                logger.warning("[Camera-Photo] gtk4paintablesink not found")

            pipeline_str = (
                "droidcamsrc camera_device=0 mode=2 ! videoconvert ! videoflip video-direction=auto ! gtk4paintablesink name=sink"
            )
            logger.info(f"[Camera-Photo] Starting viewfinder: {pipeline_str}")

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
            logger.error(f"[Camera-Photo] Failed to start viewfinder: {e}")
            self._show_error(f"Camera Error: {e}")
            return False

    def _on_viewfinder_message(self, bus, message):
        """Handle viewfinder messages."""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Photo] Viewfinder error: {err} : {debug}")

    def _stop_pipeline(self):
        """Stop the GStreamer pipeline."""
        self.viewfinder_widget.set_paintable(None)
        if self.pipeline:
            logger.debug("[Camera-Photo] Stopping pipeline...")
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None

    def _on_shutter_clicked(self, btn):
        """Handle shutter button click."""
        self.btn_shutter.set_sensitive(False)
        self._stop_pipeline()
        logger.info("[Camera-Photo] Viewfinder stopped, scheduling capture in 500ms...")

        self.retry_count = 0
        GLib.timeout_add(500, self._attempt_capture)

    def _attempt_capture(self):
        """Attempt to start the capture pipeline."""
        logger.info(f"[Camera-Photo] Attempting capture (Try {self.retry_count + 1}/{self.max_retries})...")
        self._capture_frame()
        return False

    def _capture_frame(self):
        """Capture a single frame from the camera."""
        self.temp_capture_path = os.path.join(tempfile.gettempdir(), f"cam_cap_{int(time.time())}.jpg")
        self.frame_count = 0

        pipeline_str = (
            "droidcamsrc camera_device=0 mode=2 ! videoconvert ! videoflip video-direction=auto ! jpegenc ! "
            "appsink name=sink emit-signals=True max-buffers=1 drop=False"
        )

        try:
            self.pipeline = Gst.parse_launch(pipeline_str)

            sink = self.pipeline.get_by_name("sink")
            if sink:
                sink.connect("new-sample", self._on_new_sample)

            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_capture_message)

            self.pipeline.set_state(Gst.State.PLAYING)
            logger.info("[Camera-Photo] Capture pipeline playing...")

        except Exception as e:
            logger.error(f"[Camera-Photo] Capture launch failed: {e}")
            self._handle_capture_failure(str(e))

    def _on_new_sample(self, sink):
        """Handle new sample from appsink."""
        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.ERROR

        self.frame_count += 1
        if self.frame_count < 20:
            return Gst.FlowReturn.OK

        logger.info("[Camera-Photo] Got sample from appsink")
        buf = sample.get_buffer()
        result, map_info = buf.map(Gst.MapFlags.READ)
        if result:
            try:
                with open(self.temp_capture_path, "wb") as f:
                    f.write(map_info.data)
                GLib.idle_add(self._on_capture_done)
            except Exception as e:
                logger.error(f"[Camera-Photo] Failed to save sample: {e}")
                GLib.idle_add(self._show_error, f"Save failed: {e}")
                GLib.idle_add(self._restart_viewfinder_safe)
            finally:
                buf.unmap(map_info)

        return Gst.FlowReturn.OK

    def _on_capture_message(self, bus, message):
        """Handle capture pipeline messages."""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            msg = f"{err} : {debug}"
            logger.error(f"[Camera-Photo] Capture error: {msg}")
            GLib.idle_add(self._handle_capture_failure, str(err))

    def _handle_capture_failure(self, error_msg):
        """Handle failure with retries."""
        self._stop_pipeline()

        self.retry_count += 1
        if self.retry_count < self.max_retries:
            logger.warning(f"[Camera-Photo] Capture failed, retrying in 1s... ({error_msg})")
            GLib.timeout_add(1000, self._attempt_capture)
        else:
            logger.error(f"[Camera-Photo] All retries failed. Last error: {error_msg}")
            self._show_error(f"Failed to take photo: {error_msg}")
            self._restart_viewfinder_safe()

    def _on_capture_done(self):
        """Finalize capture and show review."""
        self._stop_pipeline()
        self.btn_shutter.set_sensitive(True)

        if os.path.exists(self.temp_capture_path):
            self._process_image(self.temp_capture_path)

            self.review_image.set_filename(self.output_path)
            self.stack.set_visible_child_name("review")
        else:
            self._show_error("Captured file not found.")
            self._restart_viewfinder_safe()

        return False

    def _restart_viewfinder_safe(self):
        """Restart viewfinder and reset UI state."""
        self.btn_shutter.set_sensitive(True)
        self._start_viewfinder()

    def _process_image(self, path):
        """Compress image to self.output_path."""
        from PIL import Image

        self.output_path = path
        try:
            img = Image.open(path)
            w, h = img.size
            if w > 800 or h > 800:
                img.thumbnail((800, 800))

            self.output_path = os.path.join(tempfile.gettempdir(), f"photo_{int(time.time())}.jpg")
            img.save(self.output_path, "JPEG", quality=80)

            if path != self.output_path:
                os.remove(path)

        except Exception as e:
            logger.warning(f"[Camera-Photo] Image processing failed: {e}")

    def _on_retake_clicked(self, btn):
        """Handle retake button click."""
        if self.output_path and os.path.exists(self.output_path):
            try:
                os.remove(self.output_path)
            except Exception as e:
                logger.warning(f"[Camera-Photo] Failed to remove temp file: {e}")
        self.output_path = None
        self.stack.set_visible_child_name("capture")
        self._start_viewfinder()

    def _on_attach_clicked(self, btn):
        """Handle attach button click."""
        self._stop_pipeline()
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
        return False

    def _show_error(self, message):
        """Show an error toast."""
        toast = Adw.Toast.new(message)
        self.toast_overlay.add_toast(toast)
