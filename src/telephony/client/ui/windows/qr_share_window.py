# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gettext import gettext as _

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from telephony.shared.utils.log_utils import logger

QR_DIALOG_HEIGHT = 440
QR_AREA_SIZE = 260
QR_QUIET_ZONE_MODULES = 2


class QrShareDialog(Adw.NavigationPage):

    def __init__(self, contact_name, vcard_text):
        super().__init__(title=contact_name)
        self.matrix = self.build_matrix(vcard_text)

        self.set_size_request(-1, QR_DIALOG_HEIGHT)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=24, margin_start=24, margin_end=24)

        area = Gtk.DrawingArea(content_width=QR_AREA_SIZE, content_height=QR_AREA_SIZE,
                               hexpand=True, vexpand=True)
        area.set_draw_func(self.draw_qr)
        box.append(area)

        hint = Gtk.Label(label=_("Scan with any phone to add this contact"),
                         wrap=True, justify=Gtk.Justification.CENTER)
        hint.add_css_class("dim-label")
        box.append(hint)

        view.set_content(box)
        self.set_child(view)

    def build_matrix(self, vcard_text):
        try:
            import qrcode
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                               border=QR_QUIET_ZONE_MODULES)
            qr.add_data(vcard_text)
            qr.make(fit=True)
            return qr.get_matrix()
        except Exception as e:
            logger.error(f"[QrShare] Encoding failed: {e}")
            return None

    def draw_qr(self, _area, cr, width, height):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        if not self.matrix:
            return

        count = len(self.matrix)
        module = min(width, height) / count
        off_x = (width - module * count) / 2
        off_y = (height - module * count) / 2

        cr.set_source_rgb(0, 0, 0)
        for y, row in enumerate(self.matrix):
            for x, filled in enumerate(row):
                if filled:
                    cr.rectangle(off_x + x * module, off_y + y * module,
                                 module + 0.5, module + 0.5)
        cr.fill()
