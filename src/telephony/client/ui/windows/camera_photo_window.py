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

from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.constants import VIEWFINDER_START_DELAY_MS, VIEWFINDER_SINK_WIDTH
from telephony.client.services.flashlight_client import FlashlightClient

PIPELINE_DRAIN_TIMEOUT_NS = 2 * Gst.SECOND
from telephony.client.ui.windows.media_window_base import MediaCaptureWindow
from telephony.client.ui.widgets.common_widget import close_sheet_page

SNAP_TIMEOUT_MS = 4000
MAX_IMAGE_DIMENSION = 800
JPEG_QUALITY = 80


class CameraPhoto(MediaCaptureWindow):
    """Camera window for taking photos."""

    def __init__(self, parent_window, on_attach_callback):
        super().__init__()
        self.request_capture_height(parent_window)
        self.on_attach_callback = on_attach_callback
        self.set_title(_("Take Picture"))

        self.output_path = None
        self.temp_capture_path = None

        self.pipeline = None
        self.camera_device = 0
        self.light_on = False
        self.flashlight = FlashlightClient()
        self.bus = None
        self.bus_handler_id = None
        self.toast_overlay = None

        self._capture_taken = False
        self._snap_timeout_id = None

        self._setup_ui()
        self.connect("hidden", self._on_closed)

        self._schedule_timeout(VIEWFINDER_START_DELAY_MS, self._start_viewfinder)

    def _release_bus(self):
        """Detach the signal watch from the current pipeline bus."""
        if not self.bus:
            return
        if self.bus_handler_id:
            self.bus.disconnect(self.bus_handler_id)
        self.bus.remove_signal_watch()
        self.bus = None
        self.bus_handler_id = None

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

        self.preview_card = card_box
        self.viewfinder_holder = self.letterbox(self.viewfinder_widget)
        card_box.append(self.viewfinder_holder)
        self.page_capture.append(card_box)

        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=30)
        ctrl_box.set_halign(Gtk.Align.CENTER)
        ctrl_box.set_margin_top(20)
        ctrl_box.set_margin_bottom(20)

        self.btn_shutter = Gtk.Button()
        self.btn_shutter.set_icon_name("camera-photo-symbolic")
        self.btn_shutter.set_size_request(80, 80)
        self.btn_shutter.add_css_class("shutter-button")
        self.btn_shutter.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_shutter_clicked(b) or False))
        ctrl_box.append(self.btn_shutter)

        self.btn_light = Gtk.ToggleButton(icon_name=self.light_icon_name(), css_classes=["circular"])
        self.btn_light.set_size_request(48, 48)
        self.btn_light.set_valign(Gtk.Align.CENTER)
        self.btn_light.connect("toggled", lambda b: GLib.idle_add(lambda: self._on_light_toggled(b) or False))
        ctrl_box.prepend(self.btn_light)

        self.btn_flip = Gtk.Button(icon_name="camera-switch-symbolic", css_classes=["circular"])
        self.btn_flip.set_size_request(48, 48)
        self.btn_flip.set_valign(Gtk.Align.CENTER)
        self.btn_flip.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_flip_camera() or False))
        ctrl_box.append(self.btn_flip)

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

        self.review_image = Gtk.Picture()
        self.review_image.add_css_class("preview-round")
        self.review_image.set_overflow(Gtk.Overflow.HIDDEN)
        self.review_image.set_can_shrink(True)
        self.review_image.set_hexpand(True)
        self.review_image.set_vexpand(True)
        self.review_image.set_content_fit(Gtk.ContentFit.CONTAIN)

        review_card.append(self.letterbox(self.review_image))
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

    def _on_flip_camera(self):
        """Switch between the back and the front camera.

        The devices are the Android convention, zero for the back and
        one for the front, and switching restarts the viewfinder since
        the source cannot change camera while it runs. Only the back
        camera has a light, so the torch goes out on the way and its
        button sits disabled on the front, keeping the row still.
        """
        self.btn_flip.set_sensitive(False)
        self.btn_light.set_active(False)
        self.camera_device = 1 - self.camera_device
        self.btn_light.set_sensitive(self.camera_device == 0)
        self._start_viewfinder()

    def _on_light_toggled(self, btn):
        """Drive the torch; the back camera's is the only light."""
        self.light_on = btn.get_active()
        if self.light_on:
            btn.add_css_class("suggested-action")
        else:
            btn.remove_css_class("suggested-action")
        if self.light_on:
            self.flashlight.set_on()
        else:
            self.flashlight.set_off()

    def _light_off(self):
        """Put the torch out; safe from any exit path."""
        self.btn_light.set_active(False)
        self.flashlight.set_off()

    def _start_viewfinder(self):
        """Start the camera viewfinder pipeline."""
        if self._closed:
            return False

        self._stop_pipeline()

        try:
            f = Gst.ElementFactory.find("gtk4paintablesink")
            if not f:
                logger.warning("[Camera-Photo] gtk4paintablesink not found")

            pipeline_str = (
                f"droidcamsrc camera_device={self.camera_device} mode=2 ! tee name=t "
                f"t. ! queue ! videoconvert ! videoflip video-direction=auto ! videoscale ! video/x-raw,width={VIEWFINDER_SINK_WIDTH} ! gtk4paintablesink name=sink "
                f"t. ! valve name=snap drop=true ! queue ! videoconvert ! videoflip video-direction=auto ! jpegenc ! "
                f"appsink name=photosink emit-signals=True max-buffers=1 drop=True async=false sync=false"
            )
            logger.info(f"[Camera-Photo] Starting viewfinder: {pipeline_str}")

            self.pipeline = Gst.parse_launch(pipeline_str)

            sink = self.pipeline.get_by_name("sink")
            if sink:
                paintable = sink.get_property("paintable")
                self.viewfinder_widget.set_paintable(paintable)
                self.reveal_on_first_frame(self.viewfinder_widget)

            photosink = self.pipeline.get_by_name("photosink")
            if photosink:
                photosink.connect("new-sample", self._on_snap_sample)

            self.bus, self.bus_handler_id = self._watch_bus(self.pipeline, self._on_viewfinder_message)

            self.pipeline.set_state(Gst.State.PLAYING)
            return False

        except Exception as e:
            logger.error(f"[Camera-Photo] Failed to start viewfinder: {e}")
            self._show_error(_("Error: {e}").format(e=e))
            return False

    def _on_viewfinder_message(self, bus, message):
        """Handle viewfinder messages."""
        t = message.type
        if (t == Gst.MessageType.STATE_CHANGED and message.src == self.pipeline
                and message.parse_state_changed()[1] == Gst.State.PLAYING):
            self.btn_flip.set_sensitive(True)
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Photo] Viewfinder error: {err} : {debug}")
            self.btn_flip.set_sensitive(True)
            self._stop_pipeline()
            self._show_error(_("Error: {e}").format(e=err))

    def _stop_pipeline(self):
        """Stop the GStreamer pipeline.

        The state change is waited out, because droidcamsrc closes an
        Android camera session slowly, and building the next pipeline
        while the old one still holds the device maps the buffer
        queues of two cameras at once.
        """
        self.viewfinder_widget.set_paintable(None)
        self._release_bus()
        if self.pipeline:
            logger.debug("[Camera-Photo] Stopping pipeline...")
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_state(PIPELINE_DRAIN_TIMEOUT_NS)
            self.pipeline = None

    def _on_shutter_clicked(self, btn):
        """Take the picture from the running stream.

        The viewfinder pipeline carries a second, full-resolution
        branch behind a closed valve, marked async so the pipeline
        does not wait for a preroll buffer the closed valve will never
        deliver, which froze the whole graph on its first frame.
        Taking a picture is opening the valve for one frame rather
        than tearing the camera down,
        opening a capture pipeline, warming it up and tearing that
        down too, which is where the seconds went. The valve costs
        nothing while closed and the camera never blinks.
        """
        self.btn_shutter.set_sensitive(False)
        self.temp_capture_path = os.path.join(self.capture_dir(), f"cam_cap_{int(time.time())}.jpg")
        self._capture_taken = False
        self._snap_timeout_id = self._schedule_timeout(SNAP_TIMEOUT_MS, self._on_snap_timeout)
        valve = self.pipeline.get_by_name("snap") if self.pipeline else None
        if valve is None:
            self._show_error(_("Error: {e}").format(e="no capture branch"))
            self.btn_shutter.set_sensitive(True)
            return
        valve.set_property("drop", False)

    def _on_snap_timeout(self):
        """Give up on a frame that never came."""
        self._snap_timeout_id = None
        if self._capture_taken or self._closed:
            return False
        self._capture_taken = True
        valve = self.pipeline.get_by_name("snap") if self.pipeline else None
        if valve is not None:
            valve.set_property("drop", True)
        self.btn_shutter.set_sensitive(True)
        self._show_error(_("Error: {e}").format(e="no frame"))
        return False

    def _on_snap_sample(self, sink):
        """Keep the first frame through the valve, then close it."""
        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.ERROR
        if self._capture_taken:
            return Gst.FlowReturn.OK
        self._capture_taken = True

        valve = self.pipeline.get_by_name("snap") if self.pipeline else None
        if valve is not None:
            valve.set_property("drop", True)

        buf = sample.get_buffer()
        result, map_info = buf.map(Gst.MapFlags.READ)
        if result:
            try:
                with open(self.temp_capture_path, "wb") as f:
                    f.write(map_info.data)
                GLib.idle_add(self._on_capture_done)
            except Exception as e:
                logger.error(f"[Camera-Photo] Failed to save sample: {e}")
                GLib.idle_add(self._show_error, _("Error: {e}").format(e=e))
            finally:
                buf.unmap(map_info)

        return Gst.FlowReturn.OK

    def _on_capture_done(self):
        """Process the image; the viewfinder keeps running behind it."""
        if self._closed:
            return False

        if self._snap_timeout_id:
            self._cancel_timeout(self._snap_timeout_id)
            self._snap_timeout_id = None

        if not os.path.exists(self.temp_capture_path):
            self.btn_shutter.set_sensitive(True)
            self._show_error(_("File not found."))
            self._restart_viewfinder_safe()
            return False

        run_in_background(
            self._process_image,
            self.temp_capture_path,
            on_complete=self._on_image_processed,
            on_error=self._on_image_process_error,
        )
        return False

    def _on_image_processed(self, output_path):
        """Apply the processed image to the review page."""
        if self._closed:
            self._remove_file_quietly(output_path)
            return

        self.output_path = output_path
        self.btn_shutter.set_sensitive(True)
        self.review_image.set_filename(self.output_path)
        self.stack.set_visible_child_name("review")

    def _on_image_process_error(self, error):
        """Recover from an image processing failure."""
        logger.error(f"[Camera-Photo] Image processing task failed: {error}")
        if self._closed:
            return

        self.btn_shutter.set_sensitive(True)
        self._show_error(_("Error: {e}").format(e=error))
        self._restart_viewfinder_safe()

    def _restart_viewfinder_safe(self):
        """Restart viewfinder and reset UI state."""
        if self._closed:
            return
        self.btn_shutter.set_sensitive(True)
        self._start_viewfinder()

    def _process_image(self, path):
        """Compress the captured image and return the final file path."""
        from PIL import Image

        output_path = path
        try:
            img = Image.open(path)
            w, h = img.size
            if w > MAX_IMAGE_DIMENSION or h > MAX_IMAGE_DIMENSION:
                img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))

            output_path = os.path.join(self.capture_dir(), f"photo_{int(time.time())}.jpg")
            img.save(output_path, "JPEG", quality=JPEG_QUALITY)

            if path != output_path:
                os.remove(path)

        except Exception as e:
            logger.warning(f"[Camera-Photo] Image processing failed: {e}")
            return path

        return output_path

    def _remove_file_quietly(self, path):
        """Delete a temp file, logging failures without raising."""
        if not path or not os.path.exists(path):
            return
        try:
            os.remove(path)
        except Exception as e:
            logger.warning(f"[Camera-Photo] Failed to remove temp file: {e}")

    def _on_retake_clicked(self, btn):
        """Return to the viewfinder, which never stopped running."""
        self._remove_file_quietly(self.output_path)
        self.output_path = None
        self.stack.set_visible_child_name("capture")
        if self.pipeline is None:
            self._start_viewfinder()

    def _on_attach_clicked(self, btn):
        """Handle attach button click."""
        self._stop_pipeline()
        if self.output_path and os.path.exists(self.output_path):
            if self.on_attach_callback:
                self.on_attach_callback(self.output_path)
        GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)

    def _on_closed(self, _dialog):
        """Tear down capture state when the sheet closes."""
        self._closed = True
        self._light_off()
        self._cancel_tracked_timeouts()
        self._stop_pipeline()
        self._remove_file_quietly(self.temp_capture_path)
