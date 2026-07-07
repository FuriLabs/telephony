# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import time
from gettext import gettext as _

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Adw, Gst, GLib

from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.vcard_utils import unfold_vcard
from telephony.shared.constants import VIEWFINDER_START_DELAY_MS, VIEWFINDER_SINK_WIDTH
from telephony.client.services.camera_portal import CameraPortal, BACK_CAMERA
from telephony.client.ui.widgets.common_widget import close_sheet_page
from telephony.client.ui.windows.media_window_base import MediaCaptureWindow

SCAN_PIPELINE_TEMPLATE = (
    "pipewiresrc fd={fd} target-object={serial} ! videoconvert ! "
    "videoflip video-direction=auto ! tee name=split "
    "split. ! queue leaky=downstream max-size-buffers=2 ! videorate drop-only=true ! video/x-raw,framerate=30/1 ! "
    "videoscale ! video/x-raw,width={width},pixel-aspect-ratio=1/1 ! gtk4paintablesink name=sink "
    "split. ! queue leaky=downstream max-size-buffers=1 ! videoscale ! video/x-raw,width={width} ! "
    "videoconvert ! zbar ! fakesink sync=false"
)
BAD_CODE_TOAST_INTERVAL_SECONDS = 3


class QrScanDialog(MediaCaptureWindow):
    """Camera viewfinder that hands a scanned contact vCard to a callback. zbar reports every readable code on the bus, so there is no shutter: the first payload that looks like a vCard closes the dialog and is handed to on_contact; anything else toasts and scanning continues."""

    def __init__(self, parent_window, on_contact):
        super().__init__(title=_("Scan contact"))
        self.request_capture_height(parent_window)
        self.on_contact = on_contact
        self.portal = CameraPortal()
        self.pipeline = None
        self.bus = None
        self.bus_handler_id = None
        self.picture = None
        self._found = False
        self._last_bad_toast = 0.0

        self.toast_overlay = Adw.ToastOverlay()
        self.set_child(self.toast_overlay)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(content)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        content.append(header)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False))
        header.pack_start(btn_cancel)

        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card_box.add_css_class("card")
        card_box.set_hexpand(True)
        card_box.set_vexpand(True)
        card_box.set_margin_top(10)
        card_box.set_margin_bottom(10)
        card_box.set_margin_start(10)
        card_box.set_margin_end(10)
        card_box.set_overflow(Gtk.Overflow.HIDDEN)

        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        card_box.append(self.letterbox(self.picture))
        content.append(card_box)

        hint = Gtk.Label(label=_("Point the camera at a contact QR code"),
                         wrap=True, justify=Gtk.Justification.CENTER,
                         margin_top=20, margin_bottom=20)
        hint.add_css_class("dim-label")
        content.append(hint)

        self.connect("hidden", self.on_closed)
        self.schedule_timeout(VIEWFINDER_START_DELAY_MS, self.start_viewfinder)

    def start_viewfinder(self):
        self.portal.open(self.on_portal_ready)
        return False

    def on_portal_ready(self, devices):
        if self._closed:
            return
        device = self.portal.device_for(BACK_CAMERA)
        fd = self.take_remote_fd(self.portal)
        if not devices or device is None or fd < 0:
            self.toast_overlay.add_toast(
                Adw.Toast.new(_("Error: {e}").format(e="camera unavailable")))
            return
        try:
            self.pipeline = Gst.parse_launch(
                SCAN_PIPELINE_TEMPLATE.format(fd=fd, serial=device.serial,
                                             width=VIEWFINDER_SINK_WIDTH))
            sink = self.pipeline.get_by_name("sink")
            if sink:
                self.picture.set_paintable(sink.get_property("paintable"))
                self.reveal_on_first_frame(self.picture)
            self.bus, self.bus_handler_id = self.watch_bus(self.pipeline, self.on_message)
            self.pipeline.set_state(Gst.State.PLAYING)
        except Exception as e:
            logger.error(f"[QrScan] Failed to start viewfinder: {e}")
            self.toast_overlay.add_toast(Adw.Toast.new(_("Error: {e}").format(e=e)))

    def on_message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            logger.error(f"[QrScan] Pipeline error: {err} ({dbg})")
            return
        if message.type != Gst.MessageType.ELEMENT:
            return
        struct = message.get_structure()
        if not struct or struct.get_name() != "barcode":
            return
        self.on_symbol(struct.get_string("symbol") or "")

    def on_symbol(self, symbol):
        if self._found:
            return

        text = unfold_vcard(symbol.strip())
        if not text.upper().startswith("BEGIN:VCARD"):
            now = time.monotonic()
            if now - self._last_bad_toast >= BAD_CODE_TOAST_INTERVAL_SECONDS:
                self._last_bad_toast = now
                self.toast_overlay.add_toast(Adw.Toast.new(_("This code is not a contact")))
            return

        self._found = True
        callback = self.on_contact
        close_sheet_page(self.get_root())
        GLib.idle_add(lambda: callback(text) or False)

    def release_bus(self):
        if not self.bus:
            return
        if self.bus_handler_id:
            self.bus.disconnect(self.bus_handler_id)
        self.bus.remove_signal_watch()
        self.bus = None
        self.bus_handler_id = None

    def stop_pipeline(self):
        self.release_bus()
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        self.close_remote_fd()

    def on_closed(self, _dialog):
        self._closed = True
        self.cancel_tracked_timeouts()
        self.stop_pipeline()
        self.portal.close()
