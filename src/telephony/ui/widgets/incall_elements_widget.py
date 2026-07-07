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
    """Wide hangup pill whose label follows the number of active calls."""

    def __init__(self):
        """Initialize the button."""
        super().__init__()
        self.add_css_class("destructive-action")
        self.add_css_class("pill")
        self.set_size_request(260, 60)
        self.update_mode(1)

    def update_mode(self, count):
        """Update the label based on the call count."""
        label = _("Hangup All Calls") if count > 1 else _("Hang Up")
        self.set_child(Gtk.Label(label=label, css_classes=["title-4"]))
