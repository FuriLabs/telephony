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

from gi.repository import Gtk, Adw, GLib
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _

from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.utils.phone_utils import normalize_number
from telephony.client.ui.widgets.common_widget import (populate_contact_search_results, translate_phone_label)


class DndBypassContactsListWindow(Adw.NavigationPage):
    """Settings subpage managing the priority contacts (Notification Override)."""

    def __init__(self, parent, db, eds, kind="calls"):
        self.grp_list = None
        self.search_token = 0
        self._source_map = {}
        run_in_background(self.load_source_map)
        self.kind = kind
        super().__init__(title=_("Notification Overrides"))
        self.gsettings_mgr = db
        self.eds = eds

        self.local_contacts = []
        try:
            if self.kind == "calls":
                current = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts()
            else:
                current = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts_messages()
            if current:
                for c in current:
                    self.local_contacts.append(c.copy())
        except Exception as e:
            logger.error(f"[PriorityContacts] Failed to load initial contacts: {e}")

        view = Adw.ToolbarView()
        self.set_child(view)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)

        btn_save = Gtk.Button(label=_("Save"))
        btn_save.add_css_class("suggested-action")
        btn_save.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_save_clicked(b) or False))
        header.pack_end(btn_save)

        view.add_top_bar(header)

        self.overlay = Adw.ToastOverlay()
        view.set_content(self.overlay)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(main_box)

        search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        search_box.set_margin_top(12)
        search_box.set_margin_bottom(6)

        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Find and add contacts..."))
        search_box.append(self.search_entry)

        self.search_timer = None
        self.connect("unmap", self.on_unmap)

        main_box.append(search_box)

        lbl_info = Gtk.Label(label=_("Contacts in this list will always play notifications at full volume, even if the phone is in Silent or Do Not Disturb mode."), wrap=True)
        lbl_info.add_css_class("dim-label")
        lbl_info.set_margin_start(12)
        lbl_info.set_margin_end(12)
        lbl_info.set_margin_bottom(12)
        lbl_info.set_xalign(0)
        main_box.append(lbl_info)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        main_box.append(self.stack)

        self.page_list = Adw.PreferencesPage()
        self.grp_list = Adw.PreferencesGroup()
        self.page_list.add(self.grp_list)
        self.stack.add_named(self.page_list, "list")

        self.search_results_list = Gtk.ListBox(css_classes=["boxed-list"])
        self.search_results_list.set_margin_start(12)
        self.search_results_list.set_margin_end(12)
        self.search_results_list.set_margin_top(12)
        self.search_results_list.set_margin_bottom(12)
        self.search_results_list.set_selection_mode(Gtk.SelectionMode.NONE)

        scroll_search = Gtk.ScrolledWindow()
        scroll_search.set_child(self.search_results_list)
        self.stack.add_named(scroll_search, "search")

        self.stack.set_visible_child_name("list")

        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_results_list.connect("row-activated", self.on_result_activated)

        self.refresh_list()

    def close(self):
        """Pop this page once; repeat taps during the transition are ignored."""
        nav = self.get_ancestor(Adw.NavigationView)
        if nav and nav.get_visible_page() is self:
            nav.pop()

    def on_save_clicked(self, btn):
        """Handle save button click."""
        if self.kind == "calls":
            self.gsettings_mgr.set_notification_override_dnd_bypass_contacts(self.local_contacts)
        else:
            self.gsettings_mgr.set_notification_override_dnd_bypass_contacts_messages(self.local_contacts)
        GLib.idle_add(lambda: self.close() or False)

    def refresh_list(self):
        """Refresh the list of contacts."""
        if (self.grp_list is not None) and self.grp_list:
            self.page_list.remove(self.grp_list)

        self.grp_list = Adw.PreferencesGroup()
        self.page_list.add(self.grp_list)

        if not self.local_contacts:
            pass

        for c in self.local_contacts:
            self.create_contact_row(c)

    def create_contact_row(self, contact_data):
        """Create a row for a contact."""
        name = contact_data.get("name", "Unknown")
        number = contact_data.get("number", "")

        row = Adw.ActionRow(title=name, subtitle=number)

        btn_del = Gtk.Button(icon_name="user-trash-symbolic")
        btn_del.add_css_class("flat")
        btn_del.add_css_class("circular")
        btn_del.set_tooltip_text("Remove Contact")
        btn_del.connect("clicked", lambda b: self.delete_contact(contact_data))

        row.add_suffix(btn_del)

        self.grp_list.add(row)

    def delete_contact(self, contact_data):
        """Delete a contact from the list."""
        target_num = normalize_number(contact_data.get("number", ""))

        self.local_contacts = [c for c in self.local_contacts if normalize_number(c.get("number", "")) != target_num]

        GLib.idle_add(lambda: self.refresh_list())
        self.overlay.add_toast(Adw.Toast.new("Contact removed (Click Save to commit)"))

    def load_source_map(self):
        """Fetch the address book names once; blocking, call from a worker."""
        names = {}
        for source in self.eds.get_sources_info():
            names[source['uid']] = source['name']
        self._source_map = names

    def on_search_changed(self, entry):
        """Handle search text change."""
        if self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

        self.search_timer = GLib.timeout_add(200, self.perform_search)

    def perform_search(self):
        """Execute search."""
        self.search_timer = None
        query = self.search_entry.get_text().strip()
        if not query:
            self.stack.set_visible_child_name("list")
            return False

        self.stack.set_visible_child_name("search")

        self.search_token += 1
        current_token = self.search_token

        def bg_fetch():
            contacts = self.eds.search_contacts(query, limit=30)
            return contacts

        def on_done(contacts):
            if self.search_token != current_token:
                return False
            self.build_search_results(contacts, query)
            return False

        def bg_task():
            res = bg_fetch()
            GLib.idle_add(lambda: on_done(res))

        run_in_background(bg_task)
        return False

    def is_result_added(self, normalized_number):
        """Return True when the normalized number is already in the local list."""
        for existing in self.local_contacts:
            if normalize_number(existing.get("number", "")) == normalized_number:
                return True
        return False

    def on_unmap(self, widget):
        """Cancel the pending search debounce timer."""
        if self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

    def build_search_results(self, contacts, query):
        """Build search results UI."""
        populate_contact_search_results(
            self.search_results_list,
            contacts,
            self.eds,
            is_added=self.is_result_added,
            on_add=lambda row: self.on_result_activated(None, row),
            translate_label=translate_phone_label,
            unknown_name=_("Unknown"),
            source_map=self._source_map)

    def on_result_activated(self, listbox, row):
        """Handle activation of a search result."""
        if not row.get_sensitive():
            return

        data = getattr(row, "contact_data", None)
        if data is None:
            return
        name = data.get("name")
        raw_number = data.get("number")
        number = normalize_number(raw_number)

        for t in self.local_contacts:
            if normalize_number(t.get("number")) == number:
                self.overlay.add_toast(Adw.Toast.new("Contact already in priority list"))
                return

        new_entry = {"name": name, "number": raw_number}
        self.local_contacts.insert(0, new_entry)

        def update_ui():
            self.search_entry.set_text("")
            self.stack.set_visible_child_name("list")
            self.refresh_list()
            return False

        GLib.idle_add(update_ui)

        self.overlay.add_toast(Adw.Toast.new("Contact added"))
