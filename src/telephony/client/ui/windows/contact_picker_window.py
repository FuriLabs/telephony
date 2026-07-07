# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Gtk, Adw, Gio, GLib, Pango
from gettext import gettext as _

from telephony.shared.utils.phone_utils import normalize_number
from telephony.client.ui.widgets.common_widget import (blocked_state_text, DataLoader,
                                                      close_sheet_page)
from telephony.client.utils.model_utils import ContactItem


class ContactPicker(Adw.NavigationPage):

    def __init__(self, eds, parent_window, on_picked, title=None, action_label=None, allow_custom_number=True, return_contact_uid=False, blocked_kind=None):
        self.source_map = None
        """Initialize the Contact Picker."""
        display_title = title if title else _("Pick Contact")
        display_action = action_label if action_label else _("Open")

        super().__init__(title=display_title)
        self.eds = eds
        self.on_picked = on_picked
        self.allow_custom_number = allow_custom_number
        self.return_contact_uid = return_contact_uid
        self.blocked_kind = blocked_kind
        self.blocked_numbers = self.load_blocked_numbers(parent_window)
        self.set_size_request(-1, 500)
        self.load_token = 0
        self.search_timer = None
        self.connect("unmap", self.on_unmap)
        self.source_map = {}
        self.update_source_map()

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False))
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
        self.list_view.connect("activate", lambda lv, pos: self.on_activate(lv, pos))

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.list_view)
        scrolled.set_vexpand(True)
        content.append(scrolled)

        self.set_child(content)
        self.refresh()

    def on_manual_enter(self, entry):
        if self.allow_custom_number:
            text = entry.get_text().strip()
            if text:
                self.on_picked(normalize_number(text))
                GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)

    def on_action_button(self, btn):
        selected = self.selection.get_selected_item()
        if selected:
            self.on_item_chosen(selected)
            return

        text = self.search.get_text().strip()
        if self.allow_custom_number and text:
            self.on_picked(normalize_number(text))
            GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)

    def on_search_changed(self, entry):
        if self.search_timer:
            GLib.source_remove(self.search_timer)
        self.search_timer = GLib.timeout_add(200, self.do_search_debounced)

    def on_unmap(self, widget):
        if self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

    def do_search_debounced(self):
        query = self.search.get_text()
        self.refresh(query)
        self.search_timer = None
        return False

    def update_source_map(self):
        self.source_map = {}
        if self.eds:
            sources = self.eds.get_sources_info()
            for s in sources:
                self.source_map[s["uid"]] = s["name"]

    def refresh(self, query=""):
        self.load_token += 1
        curr = self.load_token
        self.update_source_map()
        DataLoader.load_data(
            fetch_func=lambda: self.eds.search_contacts(query),
            model_add_func=self.add_chunk,
            model=self.model,
            check_token_func=lambda: self.load_token == curr
        )

    def add_chunk(self, model, rows):
        new_items = []
        for r in rows:
            is_fav = r[5] if len(r) > 5 else False
            source_uid = r[6] if len(r) > 6 else None
            new_items.append(ContactItem(r[0], r[1], r[2], r[3], is_favorite=is_fav, source_uid=source_uid))
        model.splice(model.get_n_items(), 0, new_items)
        return False

    def load_blocked_numbers(self, parent_window):
        if not self.blocked_kind:
            return {}
        return {normalize_number(entry["number"]): blocked_state_text(entry)
                for entry in parent_window.db.get_blocked_numbers()
                if entry["block_calls" if self.blocked_kind == "calls" else "block_messages"]}

    def setup_row(self, factory, list_item):
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

        blocked = Gtk.Label(xalign=0, css_classes=["tiny-label", "marker-rejected"])
        blocked.set_visible(False)

        vbox.append(name)
        vbox.append(phone)
        vbox.append(blocked)
        vbox.append(source_lbl)

        box.append(vbox)
        list_item.set_child(box)

    def bind_row(self, factory, list_item):
        item = list_item.get_item()
        box = list_item.get_child()
        vbox = box.get_first_child()
        name_lbl = vbox.get_first_child()
        phone_lbl = name_lbl.get_next_sibling()
        blocked_lbl = phone_lbl.get_next_sibling()
        source_lbl = blocked_lbl.get_next_sibling()

        name_lbl.set_text(item.full_name)
        phones = item.phone if isinstance(item.phone, list) else []
        if not phones and item.phone:
            phones = [(str(item.phone), "Mobile")]
        if len(phones) > 0:
            phone_lbl.set_text(phones[0][0])
        else:
            phone_lbl.set_text(_("No number"))

        said = self.blocked_numbers.get(normalize_number(phones[0][0])) if phones else None
        blocked_lbl.set_text(said or "")
        blocked_lbl.set_visible(bool(said))

        if item.source_uid and (self.source_map is not None):
            s_name = self.source_map.get(item.source_uid)
            if s_name:
                source_lbl.set_text(s_name)
                source_lbl.set_visible(True)
            else:
                source_lbl.set_visible(False)
        else:
            source_lbl.set_visible(False)

    def teardown_row(self, factory, list_item):
        list_item.set_child(None)

    def on_activate(self, listview, pos):
        item = self.model.get_item(pos)
        self.on_item_chosen(item)

    def on_item_chosen(self, item):
        if self.return_contact_uid:
            self.on_picked((item.uid, item.full_name))
            GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)
        else:
            phones = item.phone if isinstance(item.phone, list) else []
            if not phones and item.phone:
                phones = [(str(item.phone), "Mobile")]

            if phones:
                self.on_picked(normalize_number(phones[0][0]))
                GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)
