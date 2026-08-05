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

from gettext import gettext as _

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from telephony.shared.utils.log_utils import logger

QR_DIALOG_WIDTH = 340
QR_DIALOG_HEIGHT = 440
QR_AREA_SIZE = 260
QR_QUIET_ZONE_MODULES = 2


class QrShareDialog(Adw.Dialog):
    """Show one contact as a QR code another phone can scan.

    The caller checks the matrix attribute before presenting: None
    means the encoding failed and there is nothing to show.
    """

    def __init__(self, contact_name, vcard_text):
        """Encode the vCard and build the dialog around it."""
        super().__init__(title=contact_name)
        self.matrix = self._build_matrix(vcard_text)

        self.set_content_width(QR_DIALOG_WIDTH)
        self.set_content_height(QR_DIALOG_HEIGHT)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=24, margin_start=24, margin_end=24)

        area = Gtk.DrawingArea(content_width=QR_AREA_SIZE, content_height=QR_AREA_SIZE,
                               hexpand=True, vexpand=True)
        area.set_draw_func(self._draw_qr)
        box.append(area)

        hint = Gtk.Label(label=_("Scan with any phone to add this contact"),
                         wrap=True, justify=Gtk.Justification.CENTER)
        hint.add_css_class("dim-label")
        box.append(hint)

        view.set_content(box)
        self.set_child(view)

    def _build_matrix(self, vcard_text):
        """Encode the vCard; qrcode is imported only when a share happens."""
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

    def _draw_qr(self, _area, cr, width, height):
        """Draw the module grid on white, which stays white in dark mode.

        Scanners need the printed contrast, so the panel is not themed.
        The half-pixel overdraw keeps hairline gaps from appearing
        between modules at fractional scales.
        """
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
