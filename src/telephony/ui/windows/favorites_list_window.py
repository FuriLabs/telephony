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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from gettext import gettext as _
from telephony.backend.utils.log_utils import logger

from ...backend.utils.thread_utils import run_in_background
from ..widgets.common_widget import (populate_contact_search_results, translate_phone_label,
                                     present_choice_sheet, add_choice_row)

SPEED_DIAL_SLOTS = list(range(1, 10))
SEARCH_DEBOUNCE_MS = 250


class FavoritesListWindow(Adw.NavigationPage):
    """Settings subpage assigning address book contacts to speed dial slots.

    Slots hold a contact's number picked from the address books, so an
    entry always points at a real contact the same way the custom tone
    lists do.
    """

    def __init__(self, parent, gsettings_mgr, eds):
        """Initialize the favorites page."""
        self.grp_list = None
        super().__init__(title=_("Favorites"))
        self.gsettings_mgr = gsettings_mgr
        self.eds = eds
        self.app_window = parent.main_window
        self.search_timer = None
        self._pending_contact = None

        view = Adw.ToolbarView()
        self.set_child(view)
        view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))

        self.overlay = Adw.ToastOverlay()
        view.set_content(self.overlay)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(main_box)

        search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        search_box.set_margin_top(12)
        search_box.set_margin_bottom(6)
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Find contact to add..."))
        search_box.append(self.search_entry)
        main_box.append(search_box)

        lbl_info = Gtk.Label(
            label=_("Pick a contact to place in a speed dial slot. On the dialpad, "
                    "typing that single digit shows the contact so you can call it."),
            wrap=True, xalign=0, css_classes=["dim-label"])
        lbl_info.set_margin_start(12)
        lbl_info.set_margin_end(12)
        lbl_info.set_margin_bottom(12)
        main_box.append(lbl_info)

        self.stack = Gtk.Stack(vexpand=True)
        main_box.append(self.stack)

        self.page_list = Adw.PreferencesPage()
        self.grp_list = Adw.PreferencesGroup()
        self.page_list.add(self.grp_list)
        self.stack.add_named(self.page_list, "list")

        self.search_results_list = Gtk.ListBox(css_classes=["boxed-list"],
                                               selection_mode=Gtk.SelectionMode.NONE)
        self.search_results_list.set_margin_start(12)
        self.search_results_list.set_margin_end(12)
        self.search_results_list.set_margin_top(12)
        self.search_results_list.set_margin_bottom(12)
        scroll_search = Gtk.ScrolledWindow()
        scroll_search.set_child(self.search_results_list)
        self.stack.add_named(scroll_search, "search")
        self.stack.set_visible_child_name("list")

        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_results_list.connect("row-activated", self._on_result_activated)
        self.connect("map", lambda w: self._refresh_list())
        self.connect("unmap", self._on_unmap)

        self._refresh_list()

    def _on_unmap(self, _widget):
        """Cancel the pending search debounce timer."""
        if self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

    def _favorites(self):
        """Return the configured speed dial entries."""
        return self.gsettings_mgr.get_favorites()

    def _refresh_list(self):
        """Rebuild the slot list from the stored favorites."""
        if self.grp_list is not None:
            self.page_list.remove(self.grp_list)
        self.grp_list = Adw.PreferencesGroup(title=_("Speed Dial"))
        self.page_list.add(self.grp_list)

        favorites = {entry.get("slot"): entry for entry in self._favorites()}
        for slot in SPEED_DIAL_SLOTS:
            entry = favorites.get(slot)
            row = Adw.ActionRow(title=str(slot))
            if entry:
                row.set_subtitle(_("{name} · {number}").format(
                    name=entry.get("name") or _("Unknown"), number=entry.get("number", "")))
                btn_clear = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
                btn_clear.add_css_class("flat")
                btn_clear.add_css_class("circular")
                btn_clear.connect("clicked", lambda b, s=slot: GLib.idle_add(
                    lambda: self._clear_slot(s) or False))
                row.add_suffix(btn_clear)
            else:
                row.set_subtitle(_("Empty"))
            self.grp_list.add(row)

    def _clear_slot(self, slot):
        """Remove the contact assigned to one slot."""
        remaining = [e for e in self._favorites() if e.get("slot") != slot]
        self.gsettings_mgr.set_favorites(remaining)
        self._refresh_list()

    def _on_search_changed(self, entry):
        """Debounce the contact search."""
        if self.search_timer:
            GLib.source_remove(self.search_timer)
        query = entry.get_text().strip()
        if not query:
            self.stack.set_visible_child_name("list")
            return
        self.search_timer = GLib.timeout_add(SEARCH_DEBOUNCE_MS, self._run_search, query)

    def _run_search(self, query):
        """Search the address books for contacts to assign."""
        self.search_timer = None
        self.stack.set_visible_child_name("search")

        def done(results):
            populate_contact_search_results(
                self.search_results_list, results, self.eds,
                is_added=lambda number: False,
                on_add=self._on_result_activated_row,
                translate_label=translate_phone_label,
                unknown_name=_("Unknown"))

        run_in_background(self.eds.search_contacts, query, on_complete=done)
        return False

    def _on_result_activated(self, _listbox, row):
        """Handle activation of a search result row."""
        if row.get_sensitive():
            self._on_result_activated_row(row)

    def _on_result_activated_row(self, row):
        """Ask which slot the picked contact should occupy."""
        data = getattr(row, "contact_data", None)
        if data is None:
            return
        self._pending_contact = data

        def build(group, sheet):
            favorites = {entry.get("slot"): entry for entry in self._favorites()}
            for slot in SPEED_DIAL_SLOTS:
                taken = favorites.get(slot)
                subtitle = taken.get("name") if taken else _("Empty")
                add_choice_row(group, sheet, str(slot),
                               lambda s=slot: self._assign_slot(s), subtitle=subtitle)

        present_choice_sheet(self, _("Speed Dial"), build,
                             description=_("Choose a slot for {name}").format(
                                 name=data.get("name") or _("Unknown")))

    def _assign_slot(self, slot):
        """Store the pending contact in the chosen slot."""
        data = self._pending_contact
        self._pending_contact = None
        if not data:
            return
        entries = [e for e in self._favorites() if e.get("slot") != slot]
        entries.append({"slot": slot, "name": data.get("name", ""), "number": data.get("number", "")})
        entries.sort(key=lambda e: e.get("slot", 0))
        self.gsettings_mgr.set_favorites(entries)
        logger.info(f"[Favorites] Slot {slot} assigned")
        self.search_entry.set_text("")
        self.stack.set_visible_child_name("list")
        self._refresh_list()
        self.overlay.add_toast(Adw.Toast.new(
            _("{name} added to slot {slot}").format(
                name=data.get("name") or _("Unknown"), slot=slot)))
