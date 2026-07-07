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

from gi.repository import Gtk, Adw, Gio, GObject, GLib
from gettext import gettext as _


class BlocklistView(Adw.Bin):
    """View for managing blocked numbers."""

    def __init__(self, db, app_window):
        self._refresh_timer = None
        """Initialize the Blocklist View."""
        super().__init__()
        self.db = db
        self.app_window = app_window

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        toolbar = Gtk.Box(spacing=10)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        toolbar.set_margin_top(12)
        toolbar.set_margin_bottom(12)

        lbl = Gtk.Label(label=_("Blocked Numbers"), css_classes=["title-4"])
        lbl.set_hexpand(True)
        lbl.set_xalign(0)

        self._blocklist_sig = self.db.connect('blocklist-updated', lambda *args: GLib.idle_add(self.refresh))
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

        self.btn_add = Gtk.Button(icon_name="list-add-symbolic")
        self.btn_add.add_css_class("suggested-action")
        self.btn_add.connect("clicked", lambda x: self.app_window.present_blocklist_editor())

        if not self.app_window.eds.is_ready:
            self.btn_add.set_sensitive(False)

        toolbar.append(lbl)
        toolbar.append(self.btn_add)
        box.append(toolbar)

        self.store = Gio.ListStore(item_type=BlockedItem)
        self.selection = Gtk.SingleSelection(model=self.store)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_row)
        factory.connect("bind", self.bind_row)
        factory.connect("teardown", self.teardown_row)

        self.list_view = Gtk.ListView(model=self.selection, factory=factory)

        scrolled = Gtk.ScrolledWindow(child=self.list_view)
        scrolled.set_vexpand(True)
        box.append(scrolled)

        self.set_child(box)
        self.refresh()

    def _on_map(self, widget):
        """Reconnect the blocklist listener and catch up after being hidden."""
        if self._blocklist_sig is not None:
            return
        self._blocklist_sig = self.db.connect('blocklist-updated', lambda *args: GLib.idle_add(self.refresh))
        self.refresh()

    def _on_unmap(self, widget):
        """Drop the blocklist listener and pending refresh while hidden."""
        if self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
            self._refresh_timer = None

        if self._blocklist_sig is not None and self.db.handler_is_connected(self._blocklist_sig):
            self.db.disconnect(self._blocklist_sig)
        self._blocklist_sig = None

    def refresh(self):
        """Reload the blocklist from DB."""
        if (self._refresh_timer is not None) and self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
        self._refresh_timer = GLib.timeout_add(200, self._do_refresh)

    def _do_refresh(self):
        self._refresh_timer = None
        self.store.remove_all()
        rows = self.db.get_blocked_numbers()
        new_items = [BlockedItem(r[0], r[1], r[2]) for r in rows]
        self.store.splice(0, 0, new_items)
        return False

    def teardown_row(self, factory, list_item):
        """Teardown row widgets."""
        list_item.set_child(None)

    def setup_row(self, factory, list_item):
        """Setup row widgets."""
        row = Gtk.Box(spacing=12)
        row.set_margin_start(12)
        row.set_margin_end(12)
        row.set_margin_top(8)
        row.set_margin_bottom(8)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        num_lbl = Gtk.Label(xalign=0, css_classes=["heading"])
        note_lbl = Gtk.Label(xalign=0, css_classes=["caption", "dim-label"])
        vbox.append(num_lbl)
        vbox.append(note_lbl)

        del_btn = Gtk.Button(icon_name="user-trash-symbolic")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("circular")
        del_btn.add_css_class("destructive-action")
        del_btn.set_valign(Gtk.Align.CENTER)

        row.append(vbox)
        row.append(Gtk.Box(hexpand=True))
        row.append(del_btn)

        list_item.set_child(row)

    def bind_row(self, factory, list_item):
        """Bind row data."""
        item = list_item.get_item()
        row = list_item.get_child()

        vbox = row.get_first_child()
        del_btn = row.get_last_child()

        num_lbl = vbox.get_first_child()
        note_lbl = vbox.get_last_child()

        num_lbl.set_text(item.number)
        note_lbl.set_text(item.notes if item.notes else _("No notes"))

        if getattr(del_btn, "h", None) is not None:
            del_btn.disconnect(del_btn.h)
        del_btn.h = del_btn.connect("clicked", lambda b: self.confirm_delete_entry(item.id))

        if not self.app_window.eds.is_ready:
            del_btn.set_sensitive(False)

    def confirm_delete_entry(self, entry_id):
        """Confirm before unblocking."""
        def _do_delete():
            self.delete_entry(entry_id)

        self.app_window.confirm_action(_("Unblock Number"), _("Are you sure you want to unblock this number?"), _do_delete)

    def delete_entry(self, entry_id):
        """Delete a blocklist entry."""
        number = None
        for i in range(self.store.get_n_items()):
            item = self.store.get_item(i)
            if item.id == entry_id:
                number = item.number
                break

        self.db.unblock_number(entry_id, number)

        self.app_window.notify_success(_("Unblocked number"))


class BlockedItem(GObject.Object):
    """Model representing a blocked number."""

    def __init__(self, id, number, notes):
        """Initialize the blocked item."""
        super().__init__()
        self.id = id
        self.number = number
        self.notes = notes
