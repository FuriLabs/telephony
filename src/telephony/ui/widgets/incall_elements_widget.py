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

from gi.repository import Gtk, Pango
from gettext import gettext as _


def create_truncated_label(text, css_classes=[], max_chars=20):
    """Creates a label that automatically ellipses (...) if too long."""
    lbl = Gtk.Label(label=text)
    for c in css_classes:
        lbl.add_css_class(c)
    lbl.set_ellipsize(Pango.EllipsizeMode.END)
    lbl.set_max_width_chars(max_chars)
    return lbl


class DynamicHangupButton(Gtk.Button):
    """Hangup button that changes appearance based on number of active calls."""

    def __init__(self):
        """Initialize the button."""
        super().__init__()
        self._set_icon_mode()
        self.add_css_class("destructive-action")

    def update_mode(self, count):
        """Update button mode based on call count."""
        if count > 1:
            self._set_text_mode()
        else:
            self._set_icon_mode()

    def _set_text_mode(self):
        """Switch to text mode (Hangup All)."""
        self.set_child(Gtk.Label(label=_("Hangup All Calls"), css_classes=["title-4"]))
        self.remove_css_class("circular")
        self.add_css_class("pill")
        self.set_size_request(260, 60)

    def _set_icon_mode(self):
        """Switch to icon mode (Single Hangup)."""
        self.set_child(Gtk.Image.new_from_icon_name("call-stop-symbolic"))
        self.remove_css_class("pill")
        self.add_css_class("circular")
        self.set_size_request(70, 70)
