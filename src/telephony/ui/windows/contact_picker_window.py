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

from gi.repository import Gtk, Adw, Gio, GLib, Pango
from gettext import gettext as _

from ...backend.utils.phone_utils import normalize_number
from ..widgets.common_widget import DataLoader
from ...backend.utils.model_utils import ContactItem


class ContactPicker(Adw.Window):
    """Window for selecting a contact from the list."""

    def __init__(self, eds, parent_window, on_picked, title=None, action_label=None, allow_custom_number=True, return_contact_uid=False):
        """Initialize the Contact Picker."""
        display_title = title if title else _("Pick Contact")
        display_action = action_label if action_label else _("Open")

        super().__init__(title=display_title, transient_for=parent_window, modal=True)
        self.eds = eds
        self.on_picked = on_picked
        self.allow_custom_number = allow_custom_number
        self.return_contact_uid = return_contact_uid
        self.set_default_size(360, 500)
        self.load_token = 0
        self.search_timer = None
        self.source_map = {}
        self._update_source_map()

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: self.close() or False))
        header.pack_start(btn_cancel)

        self.btn_enter = Gtk.Button(label=display_action)
        self.btn_enter.add_css_class("suggested-action")
        self.btn_enter.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_action_button(b) or False))
        header.pack_end(self.btn_enter)
        content.append(header)

        search_box = Gtk.Box(spacing=6)
        search_box.set_margin_start(10)
        search_box.set_margin_end(10)
        search_box.set_margin_top(6)
        search_box.set_margin_bottom(6)

        self.search = Gtk.SearchEntry(placeholder_text=_("Name or Number"), hexpand=True)
        self.search.connect("search-changed", self.on_search_changed)
        self.search.connect("activate", lambda b: self.on_manual_enter(b))
        search_box.append(self.search)
        content.append(search_box)

        self.model = Gio.ListStore(item_type=ContactItem)
        self.selection = Gtk.SingleSelection(model=self.model)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_row)
        factory.connect("bind", self.bind_row)
        factory.connect("teardown", self.teardown_row)

        self.list_view = Gtk.ListView(model=self.selection, factory=factory)
        self.list_view.connect("activate", lambda b: self.on_activate(b))

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.list_view)
        scrolled.set_vexpand(True)
        content.append(scrolled)

        self.set_content(content)
        self.refresh()

    def on_manual_enter(self, entry):
        """Handle manual entry of a number."""
        if self.allow_custom_number:
            text = entry.get_text().strip()
            if text:
                self.on_picked(normalize_number(text))
                GLib.idle_add(lambda: self.close() or False)

    def on_action_button(self, btn):
        """Handle the main action button click."""
        selected = self.selection.get_selected_item()
        if selected:
            self.on_item_chosen(selected)
            return

        text = self.search.get_text().strip()
        if self.allow_custom_number and text:
            self.on_picked(normalize_number(text))
            GLib.idle_add(lambda: self.close() or False)

    def on_search_changed(self, entry):
        """Handle search text change."""
        if self.search_timer:
            GLib.source_remove(self.search_timer)
        self.search_timer = GLib.timeout_add(200, self.do_search_debounced)

    def do_search_debounced(self):
        """Execute search."""
        query = self.search.get_text()
        self.refresh(query)
        self.search_timer = None
        return False

    def _update_source_map(self):
        """Update local map of source UIDs to names."""
        self.source_map = {}
        if self.eds and hasattr(self.eds, 'get_sources_info'):
            sources = self.eds.get_sources_info()
            for s in sources:
                self.source_map[s['uid']] = s['name']

    def refresh(self, query=""):
        """Reload contacts."""
        self.load_token += 1
        curr = self.load_token
        self._update_source_map()
        DataLoader.load_data(
            fetch_func=lambda: self.eds.search_contacts(query),
            model_add_func=self.add_chunk,
            model=self.model,
            check_token_func=lambda: self.load_token == curr
        )

    def add_chunk(self, model, rows):
        """Add data chunk to model."""
        new_items = []
        for r in rows:
            is_fav = r[5] if len(r) > 5 else False
            source_uid = r[6] if len(r) > 6 else None
            new_items.append(ContactItem(r[0], r[1], r[2], r[3], r[4], is_favorite=is_fav, source_uid=source_uid))
        model.splice(model.get_n_items(), 0, new_items)
        return False

    def setup_row(self, factory, list_item):
        """Setup row widgets."""
        box = Gtk.Box(spacing=10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(xalign=0, css_classes=["heading"])
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(25)
        phone = Gtk.Label(xalign=0, css_classes=["caption", "dim-label"])
        phone.set_ellipsize(Pango.EllipsizeMode.END)

        source_lbl = Gtk.Label(xalign=0, css_classes=["tiny-label"])
        source_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        source_lbl.set_visible(False)

        vbox.append(name)
        vbox.append(phone)
        vbox.append(source_lbl)

        box.append(vbox)
        list_item.set_child(box)

    def bind_row(self, factory, list_item):
        """Bind data to row."""
        item = list_item.get_item()
        box = list_item.get_child()
        vbox = box.get_first_child()
        name_lbl = vbox.get_first_child()
        phone_lbl = name_lbl.get_next_sibling()
        source_lbl = phone_lbl.get_next_sibling()

        name_lbl.set_text(item.full_name)
        phones = item.phone if isinstance(item.phone, list) else []
        if not phones and item.phone:
            phones = [(str(item.phone), "Mobile")]
        if len(phones) > 0:
            phone_lbl.set_text(phones[0][0])
        else:
            phone_lbl.set_text(_("No number"))

        if item.source_uid and hasattr(self, 'source_map'):
            s_name = self.source_map.get(item.source_uid)
            if s_name:
                source_lbl.set_text(s_name)
                source_lbl.set_visible(True)
            else:
                source_lbl.set_visible(False)
        else:
            source_lbl.set_visible(False)

    def teardown_row(self, factory, list_item):
        """Teardown row widgets."""
        list_item.set_child(None)

    def on_activate(self, listview, pos):
        """Handle row activation."""
        item = self.model.get_item(pos)
        self.on_item_chosen(item)

    def on_item_chosen(self, item):
        """Process chosen item."""
        if self.return_contact_uid:
            self.on_picked((item.uid, item.full_name))
            GLib.idle_add(lambda: self.close() or False)
        else:
            phones = item.phone if isinstance(item.phone, list) else []
            if not phones and item.phone:
                phones = [(str(item.phone), "Mobile")]

            if phones:
                self.on_picked(normalize_number(phones[0][0]))
                GLib.idle_add(lambda: self.close() or False)
