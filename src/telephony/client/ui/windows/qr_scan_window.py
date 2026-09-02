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

import time
from gettext import gettext as _

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, Gst, GLib

from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.vcard_utils import unfold_vcard
from telephony.shared.constants import (VIEWFINDER_START_DELAY_MS, CAPTURE_SHEET_HEIGHT, VIEWFINDER_SINK_WIDTH)
from telephony.client.ui.windows.media_window_base import MediaCaptureWindow

SCAN_PIPELINE = (
    "droidcamsrc camera_device=0 mode=2 ! videoconvert ! "
    "videoflip video-direction=auto ! tee name=split "
    f"split. ! queue leaky=downstream max-size-buffers=2 ! videoscale ! video/x-raw,width={VIEWFINDER_SINK_WIDTH},pixel-aspect-ratio=1/1 ! gtk4paintablesink name=sink "
    "split. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! zbar ! fakesink sync=false"
)
BAD_CODE_TOAST_INTERVAL_SECONDS = 3


class QrScanDialog(MediaCaptureWindow):
    """Camera viewfinder that hands a scanned contact vCard to a callback.

    zbar reports every readable code on the bus, so there is no shutter:
    the first payload that looks like a vCard closes the dialog and is
    handed to on_contact; anything else toasts and scanning continues.
    """

    def __init__(self, on_contact):
        """Build the viewfinder sheet; the pipeline starts shortly after."""
        super().__init__(title=_("Scan contact"))
        self.on_contact = on_contact
        self.pipeline = None
        self.bus = None
        self.bus_handler_id = None
        self.picture = None
        self._found = False
        self._last_bad_toast = 0.0

        self.set_size_request(-1, CAPTURE_SHEET_HEIGHT)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_child(self.toast_overlay)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(content)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        content.append(header)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: self.close() or False))
        header.pack_start(btn_cancel)

        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card_box.add_css_class("preview-round")
        card_box.set_hexpand(True)
        card_box.set_vexpand(True)
        card_box.set_margin_top(10)
        card_box.set_margin_bottom(10)
        card_box.set_margin_start(10)
        card_box.set_margin_end(10)
        card_box.set_overflow(Gtk.Overflow.HIDDEN)

        self.picture = Gtk.Picture()
        self.picture.add_css_class("preview-round")
        self.picture.set_overflow(Gtk.Overflow.HIDDEN)
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

        self.connect("hidden", self._on_closed)
        self._schedule_timeout(VIEWFINDER_START_DELAY_MS, self._start_viewfinder)

    def _start_viewfinder(self):
        """Start the camera pipeline with the zbar decoder branch."""
        try:
            self.pipeline = Gst.parse_launch(SCAN_PIPELINE)
            sink = self.pipeline.get_by_name("sink")
            if sink:
                self.picture.set_paintable(sink.get_property("paintable"))
                self.reveal_on_first_frame(self.picture)
            self.bus, self.bus_handler_id = self._watch_bus(self.pipeline, self._on_message)
            self.pipeline.set_state(Gst.State.PLAYING)
        except Exception as e:
            logger.error(f"[QrScan] Failed to start viewfinder: {e}")
            self.toast_overlay.add_toast(Adw.Toast.new(_("Error: {e}").format(e=e)))
        return False

    def _on_message(self, _bus, message):
        """Watch the bus for zbar detections and pipeline errors."""
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            logger.error(f"[QrScan] Pipeline error: {err} ({dbg})")
            return
        if message.type != Gst.MessageType.ELEMENT:
            return
        struct = message.get_structure()
        if not struct or struct.get_name() != "barcode":
            return
        self._on_symbol(struct.get_string("symbol") or "")

    def _on_symbol(self, symbol):
        """Accept one contact code; other codes toast and scanning goes on."""
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
        self.close()
        GLib.idle_add(lambda: callback(text) or False)

    def _release_bus(self):
        """Detach the signal watch from the current pipeline bus."""
        if not self.bus:
            return
        if self.bus_handler_id:
            self.bus.disconnect(self.bus_handler_id)
        self.bus.remove_signal_watch()
        self.bus = None
        self.bus_handler_id = None

    def _stop_pipeline(self):
        """Stop the camera pipeline and release the bus watch."""
        self._release_bus()
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None

    def _on_closed(self, _dialog):
        """Tear the pipeline down whichever way the sheet goes away."""
        self._cancel_tracked_timeouts()
        self._stop_pipeline()
