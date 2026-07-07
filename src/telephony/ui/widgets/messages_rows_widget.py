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


class ConversationRowFactory:
    """Factory for creating rows in the conversation list view."""

    @staticmethod
    def setup(factory, list_item):
        """Setup UI widgets for a conversation row."""
        stack = Gtk.Stack()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_start(12)
        row.set_margin_end(12)
        row.set_margin_top(6)
        row.set_margin_bottom(6)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_hexpand(True)

        top_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        lbl_name = Gtk.Label(xalign=0, css_classes=["heading"])
        lbl_name.set_hexpand(True)
        lbl_name.set_ellipsize(Pango.EllipsizeMode.END)

        lbl_date = Gtk.Label(css_classes=["caption", "dim-label"])
        lbl_date.set_halign(Gtk.Align.END)

        top_line.append(lbl_name)
        top_line.append(lbl_date)

        lbl_number = Gtk.Label(xalign=0, css_classes=["tiny-label", "dim-label"])
        lbl_number.set_ellipsize(Pango.EllipsizeMode.END)

        btm_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        lbl_msg = Gtk.Label(xalign=0, css_classes=["caption"])
        lbl_msg.set_hexpand(True)
        lbl_msg.set_ellipsize(Pango.EllipsizeMode.END)
        lbl_msg.set_max_width_chars(30)

        lbl_badge = Gtk.Label(css_classes=["numeric", "badge"])
        lbl_badge.set_visible(False)

        btm_line.append(lbl_msg)
        btm_line.append(lbl_badge)

        vbox.append(top_line)
        vbox.append(lbl_number)
        vbox.append(btm_line)

        btn_open = Gtk.Button(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER)
        btn_open.add_css_class("flat")

        row.append(vbox)
        row.append(btn_open)
        stack.add_named(row, "standard")

        list_item.set_child(stack)
        list_item.widgets = {
            "stack": stack,
            "name": lbl_name, "date": lbl_date, "msg": lbl_msg,
            "badge": lbl_badge, "btn": btn_open, "num": lbl_number
        }

    @staticmethod
    def bind(factory, list_item):
        """Bind data to the conversation row widgets."""
        if list_item.get_child() is None:
            ConversationRowFactory.setup(factory, list_item)

        item = list_item.get_item()
        w = list_item.widgets
        stack = w["stack"]

        stack.set_visible_child_name("standard")

        display_name = item.name if item.name else item.number
        if display_name == item.number and any(c.isdigit() for c in str(item.number)):
            display_name = _("Unknown")

        w["name"].set_text(display_name)
        w["num"].set_text(item.number)

        if "," in str(item.number):
            w["date"].set_text(_("{time} • Group").format(time=item.display_time))
        else:
            w["date"].set_text(item.display_time)

        body_preview = item.last_msg.replace("\n", " ") if item.last_msg else ""
        if item.status == 'draft':
            body_preview = _("Draft: {message}").format(message=body_preview)
            w["msg"].add_css_class("dim-label")
            w["msg"].add_css_class("italic")
        else:
            w["msg"].remove_css_class("dim-label")
            w["msg"].remove_css_class("italic")

        w["msg"].set_text(body_preview)

        if item.unread_count > 0:
            w["badge"].set_text(str(item.unread_count))
            w["badge"].set_visible(True)
            w["name"].add_css_class("accent")
        else:
            w["badge"].set_visible(False)
            w["name"].remove_css_class("accent")

        w["btn"].set_sensitive(False)

    @staticmethod
    def teardown(factory, list_item):
        """Teardown the conversation row widgets."""
        if (list_item.get_child() is not None):
            list_item.widgets = None
        list_item.set_child(None)
