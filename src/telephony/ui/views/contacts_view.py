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

from ...backend.utils.thread_utils import run_in_background
import hashlib

from gi.repository import Gtk, Adw, Gio, GLib, Pango
from loguru import logger
from gettext import gettext as _

from ...backend.utils.phone_utils import normalize_number
from ..widgets.common_widget import DataLoader, translate_phone_label, present_choice_sheet, add_choice_row
from ...backend.utils.model_utils import ContactItem

DUPLICATE_RECHECK_DELAY_MS = 1500


class ContactsView(Adw.Bin):
    """View displaying the list of contacts."""

    def __init__(self, db, app_window):
        self._refresh_timer = None
        self._dup_timer = None
        self.source_map = None
        """Initialize the ContactsView."""
        super().__init__()
        self.db = db
        self.app_window = app_window
        self.search_timer = None
        self.load_token = 0
        self._duplicates_checked = False
        self.source_map = {}

        self.page_offset = 0
        self.page_limit = 50
        self.is_fetching = False
        self.current_query = ""

        self.calling_enabled = True
        self._live_call_btns = set()

        self.signal_ids = []
        self.signal_ids.append((self.app_window.db, self.app_window.db.connect('contacts-updated', lambda *args: GLib.idle_add(lambda: self.refresh()))))
        if self.app_window.eds:
            self.signal_ids.append((self.app_window.eds, self.app_window.eds.connect('contacts-loaded', lambda *args: GLib.idle_add(self.on_contacts_loaded_signal))))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(spacing=6)

        header.set_margin_start(10)
        header.set_margin_end(10)
        header.set_margin_top(10)
        header.set_margin_bottom(10)

        self.search = Gtk.SearchEntry(placeholder_text=_("Search contacts"), hexpand=True)
        self.search.connect("search-changed", self.on_search_changed)
        header.append(self.search)

        self.btn_add = Gtk.Button(icon_name="list-add-symbolic")
        self.btn_add.connect("clicked", lambda b: GLib.idle_add(lambda: self.app_window.present_edit_contact() or False))
        header.append(self.btn_add)

        if (self.app_window and self.app_window.eds is not None) and not self.app_window.eds.is_ready:
            self.btn_add.set_sensitive(False)

        box.append(header)

        self.model = Gio.ListStore(item_type=ContactItem)
        self.selection = Gtk.SingleSelection(model=self.model)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_row)
        factory.connect("bind", self.bind_row)
        factory.connect("teardown", self.teardown_row)

        self.list_view = Gtk.ListView(model=self.selection, factory=factory)
        self.list_view.connect("activate", lambda lv, pos: self.on_activate(lv, pos))

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_child(self.list_view)
        self.scrolled.set_vexpand(True)

        self.v_adj = self.scrolled.get_vadjustment()
        self.v_adj.connect("value-changed", self.on_scroll_changed)

        self.spinner = Adw.Spinner()
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_size_request(32, 32)
        self.spinner.set_margin_top(12)
        self.spinner.set_margin_bottom(12)
        self.spinner.set_visible(False)

        box.append(self.scrolled)
        box.append(self.spinner)

        self.set_child(box)

        self.connect("map", self.on_map)

        self.refresh()

    def _update_source_map(self):
        """Update local map of source UIDs to names."""
        self.source_map = {}
        if (self.app_window and self.app_window.eds is not None):
            sources = self.app_window.eds.get_sources_info()
            for s in sources:
                self.source_map[s['uid']] = s['name']

    def on_map(self, *args):
        """Handle view being shown."""
        self._update_source_map()
        if self.app_window.eds.is_ready and not self._duplicates_checked:
            self.app_window.enqueue_popup(lambda cb: self.check_duplicates(cb))

    def _update_add_button_sensitivity(self):
        """Update add button sensitivity based on EDS readiness and selected source."""
        if not self.btn_add:
            return

        if not self.app_window.eds.is_ready:
            self.btn_add.set_sensitive(False)
            return

        sources = self.app_window.eds.get_sources_info()
        default_source = next((s for s in sources if s['is_system_default']), None)
        if default_source and default_source['name'] == "Andromeda Contacts":
            self.btn_add.set_sensitive(False)
        else:
            self.btn_add.set_sensitive(True)

    def set_calling_enabled(self, enabled):
        """Grey out the per-row call buttons while no call can be placed."""
        self.calling_enabled = enabled
        for btn in self._live_call_btns:
            btn.set_sensitive(enabled)

    def cleanup(self):
        """Cleanup resources before destruction."""
        if self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
            self._refresh_timer = None

        if self._dup_timer:
            GLib.source_remove(self._dup_timer)
            self._dup_timer = None

        for obj, sig_id in self.signal_ids:
            if obj.handler_is_connected(sig_id):
                obj.disconnect(sig_id)
        self.signal_ids.clear()

    def on_contacts_loaded_signal(self, *args):
        """Handle contacts loaded signal."""
        self._update_add_button_sensitivity()
        self._update_source_map()
        self.refresh()

        if not self.app_window.eds.is_ready:
            return

        if not self._duplicates_checked:
            self.app_window.enqueue_popup(lambda cb: self.check_duplicates(cb))
            return

        self._schedule_duplicate_recheck()

    def _schedule_duplicate_recheck(self):
        """Count the duplicates again once the run of changes settles.

        Contacts can be merged in another window, and the banner here
        would keep offering to resolve conflicts that no longer exist.
        """
        if self._dup_timer:
            GLib.source_remove(self._dup_timer)
        self._dup_timer = GLib.timeout_add(DUPLICATE_RECHECK_DELAY_MS, self._on_duplicate_recheck)

    def _on_duplicate_recheck(self):
        """Run the settled re-count."""
        self._dup_timer = None
        self.check_duplicates()
        return False


    def check_duplicates(self, done_callback=None):
        """Check for contacts sharing the same phone number."""
        resolver_enabled = self.app_window.gsettings_mgr.gsettings.get_boolean("duplicate-resolver-enabled")

        if not resolver_enabled or not self.app_window.eds.is_ready:
            logger.debug("[ContactsView] Skipping duplicate check: EDS not ready or disabled by user")
            if done_callback:
                done_callback()
            return

        self._duplicates_checked = True
        run_in_background(self._check_duplicates_thread, done_callback,)

    def _check_duplicates_thread(self, done_callback):
        """Threaded logic for check_duplicates."""
        try:
            num_map = {}
            snapshot_items = []
            with self.app_window.eds.cache_lock:
                snapshot_items = list(self.app_window.eds.cache.values())

            for data in snapshot_items:
                if not data.get('phones'):
                    continue
                source_uid = data.get('source_uid', "")
                uid = data.get('uid')

                if source_uid in self.app_window.eds.sources and self.app_window.eds.sources[source_uid].get('name') == "Andromeda Contacts":
                    continue

                for p_num, p_lbl in data['phones']:
                    norm = normalize_number(p_num)
                    if not norm:
                        continue

                    key = (norm, source_uid)
                    if key not in num_map:
                        num_map[key] = []

                    exists = False
                    for entry in num_map[key]:
                        if entry['uid'] == uid:
                            exists = True
                            break
                    if not exists:
                        num_map[key].append(data)

            conflicts = []
            identical_dupes = []
            for (num, s_uid), contacts in num_map.items():
                if len(contacts) > 1:
                    unique_contacts = []
                    seen_hashes = {}

                    for c in contacts:
                        h = c.get('vcard_hash')
                        if not h:
                            vcard_str = self.db.get_contact_vcard(c['uid']) or ""
                            h = hashlib.md5(vcard_str.encode('utf-8')).hexdigest()
                            c['vcard_hash'] = h

                        if h and h in seen_hashes:
                            identical_dupes.append(c['uid'])
                        else:
                            if h:
                                seen_hashes[h] = c
                            unique_contacts.append(c)

                    if len(unique_contacts) > 1:
                        conflicts.append((num, unique_contacts))

            if identical_dupes:
                logger.info(f"[ContactsView] Removing {len(identical_dupes)} identical duplicate contacts")
                self.app_window.eds.delete_contacts(identical_dupes)

            GLib.idle_add(self.app_window.update_duplicate_status, conflicts)
            if done_callback:
                GLib.idle_add(done_callback)

        except Exception as e:
            logger.error(f"[ContactsView] Check duplicates error: {e}")
            if done_callback:
                GLib.idle_add(done_callback)

    def on_search_changed(self, entry):
        """Handle search entry text change."""
        if self.search_timer:
            GLib.source_remove(self.search_timer)
        self.search_timer = GLib.timeout_add(200, self.do_search_debounced)

    def do_search_debounced(self):
        """Execute search after debounce."""
        query = self.search.get_text()
        self.refresh(query)
        self.search_timer = None
        return False

    def setup_row(self, factory, list_item):
        """Setup handler for list item factory."""
        box = Gtk.Box(spacing=10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_hexpand(True)

        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        name = Gtk.Label(xalign=0, css_classes=["heading"])
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_hexpand(False)
        name.set_halign(Gtk.Align.START)

        star = Gtk.Image.new_from_icon_name("starred-symbolic")
        star.add_css_class("icon-blue")
        star.set_visible(False)

        name_box.append(name)
        name_box.append(star)

        num_lbl = Gtk.Label(xalign=0, css_classes=["caption", "dim-label"])
        num_lbl.set_halign(Gtk.Align.START)
        num_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        num_lbl.set_visible(False)

        source_lbl = Gtk.Label(xalign=0, css_classes=["tiny-label"])
        source_lbl.set_halign(Gtk.Align.START)
        source_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        source_lbl.set_visible(False)

        vbox.append(name_box)
        vbox.append(num_lbl)
        vbox.append(source_lbl)

        actions = Gtk.Box(spacing=8)
        actions.set_valign(Gtk.Align.CENTER)

        btn_msg = Gtk.Button(icon_name="mail-message-new-symbolic")
        btn_msg.add_css_class("circular")
        btn_msg.add_css_class("secondary-btn")
        btn_msg.set_size_request(34, 34)
        btn_msg.set_valign(Gtk.Align.CENTER)

        btn_edit = Gtk.Button(icon_name="document-edit-symbolic")
        btn_edit.add_css_class("circular")
        btn_edit.add_css_class("secondary-btn")
        btn_edit.set_size_request(34, 34)
        btn_edit.set_valign(Gtk.Align.CENTER)

        btn_call = Gtk.Button(icon_name="call-start-symbolic")
        btn_call.add_css_class("circular")
        btn_call.add_css_class("call-btn-small")
        btn_call.set_size_request(34, 34)
        btn_call.set_valign(Gtk.Align.CENTER)
        btn_call.set_vexpand(False)
        btn_call.set_sensitive(self.calling_enabled)
        self._live_call_btns.add(btn_call)

        ctrl = Gtk.GestureClick(button=3)
        ctrl.connect("pressed", lambda g, n, x, y: GLib.idle_add(lambda: self.on_call_right_click(g, n, x, y) or False))
        btn_call.add_controller(ctrl)

        for btn in [btn_msg, btn_edit, btn_call]:
            btn.handler_id = None
            btn.phones_data = []
            
        actions.append(btn_msg)
        actions.append(btn_edit)
        actions.append(btn_call)

        box.append(vbox)
        box.append(Gtk.Box(hexpand=True))
        box.append(actions)

        list_item.set_child(box)

    def bind_row(self, factory, list_item):
        """Bind handler for list item factory."""
        item = list_item.get_item()
        box = list_item.get_child()
        vbox = box.get_first_child()
        actions = box.get_last_child()

        name_box = vbox.get_first_child()
        name_lbl = name_box.get_first_child()
        star = name_lbl.get_next_sibling()

        num_lbl = name_box.get_next_sibling()
        source_lbl = num_lbl.get_next_sibling()

        btn_msg = actions.get_first_child()
        btn_edit = btn_msg.get_next_sibling()
        btn_call = btn_edit.get_next_sibling()

        if not self.app_window.eds.is_ready:
            btn_edit.set_sensitive(False)
        else:
            btn_edit.set_sensitive(True)

        name_lbl.set_text(item.full_name)
        if item.is_favorite:
            star.set_visible(item.is_favorite)
        else:
            star.set_visible(False)

        phones = item.phone if isinstance(item.phone, list) else []
        if not phones and item.phone:
            phones = [(str(item.phone), "Mobile")]

        if phones:
            p_num = phones[0][0]
            num_lbl.set_text(p_num)
            num_lbl.set_visible(True)
        else:
            num_lbl.set_visible(False)

        if item.source_uid and (self.source_map is not None):
            s_name = self.source_map.get(item.source_uid)
            if s_name:
                source_lbl.set_text(s_name)
                source_lbl.set_visible(True)
            else:
                source_lbl.set_visible(False)
        else:
            source_lbl.set_visible(False)

        for btn in [btn_msg, btn_edit, btn_call]:
            if (btn.handler_id is not None) and btn.handler_is_connected(btn.handler_id):
                btn.disconnect(btn.handler_id)

        btn_call.handler_id = btn_call.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_call_click(b, phones) or False))
        btn_edit.handler_id = btn_edit.connect("clicked", lambda b: GLib.idle_add(lambda: self.edit_contact(b, item) or False))
        btn_msg.handler_id = btn_msg.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_msg_click(b, phones) or False))

        btn_call.phones_data = phones

    def teardown_row(self, factory, list_item):
        """Teardown row widgets."""
        box = list_item.get_child()
        if box is not None:
            actions = box.get_last_child()
            self._live_call_btns.discard(actions.get_last_child())
        list_item.set_child(None)

    def on_call_click(self, btn, phones):
        """Handle call button click."""
        if not phones:
            return
        if len(phones) == 1:
            self.search.set_text("")
            self.app_window.start_call(normalize_number(phones[0][0]))
        else:
            self._show_picker(btn, phones, self.app_window.start_call)

    def on_call_right_click(self, gesture, _n_press, x, y):
        """Handle right click on call button."""
        btn = gesture.get_widget()
        phones = btn.phones_data
        if not phones:
            return

        self.search.set_text("")

        def build(group, sheet):
            for p_num, p_lbl in phones:
                add_choice_row(group, sheet, p_num,
                               lambda n=p_num: self.app_window.start_call(normalize_number(n), hide_id=True),
                               subtitle=translate_phone_label(p_lbl))

        present_choice_sheet(self.app_window, _("Call Anonymous"), build)

    def on_msg_click(self, btn, phones):
        """Handle message button click."""
        if not phones:
            return
        if len(phones) == 1:
            self.search.set_text("")
            self.app_window.present_chat(normalize_number(phones[0][0]))
        else:
            self._show_picker(btn, phones, self.app_window.present_chat)

    def _show_picker(self, parent_btn, phones, callback):
        """Show the phone number choice sheet."""
        def build(group, sheet):
            for number, label in phones:
                add_choice_row(group, sheet, number,
                               lambda n=number: [self.search.set_text(""), callback(normalize_number(n))],
                               subtitle=translate_phone_label(label))

        present_choice_sheet(self.app_window, _("Pick Contact"), build)

    def edit_contact(self, btn, item):
        """Open contact editor."""
        if btn:
            self.search.set_text("")
        uid = item.uid
        cached_data = self.app_window.eds.cache.get(uid)
        if not cached_data:
            return
        source_uid = cached_data.get('source_uid')
        if source_uid and source_uid in self.app_window.eds.sources and self.app_window.eds.sources[source_uid].get('name') == "Andromeda Contacts":
            return
        self.app_window.present_edit_contact(contact_data=cached_data)

    def on_activate(self, listview, pos):
        """Handle row activation."""
        item = self.model.get_item(pos)
        self.edit_contact(None, item)

    def on_scroll_changed(self, adj):
        """Handle scroll position change."""
        if self.is_fetching:
            return
        is_at_bottom = (adj.get_value() + adj.get_page_size()) >= (adj.get_upper() - 800)
        if is_at_bottom:
            self._start_fetch()

    def refresh(self, query="", on_done=None):
        """Reload contact list."""
        if (self._refresh_timer is not None) and self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
        self._refresh_timer = GLib.timeout_add(200, self._do_refresh, query, on_done)

    def _do_refresh(self, query, on_done):
        self._refresh_timer = None
        self.load_token += 1
        self.page_offset = 0
        self.current_query = query
        self._start_fetch(on_done)
        return False

    def _start_fetch(self, on_done=None):
        """Start fetching data in background."""
        current_token = self.load_token
        self.is_fetching = True
        self.spinner.set_visible(True)

        def _bg_fetch():
            rows = self.db.search_contacts(self.current_query, limit=self.page_limit + 1, offset=self.page_offset)

            has_more = False
            if len(rows) > self.page_limit:
                has_more = True
                rows = rows[:self.page_limit]

            self.last_fetch_has_more = has_more
            return rows

        DataLoader.load_data(
            fetch_func=_bg_fetch,
            model_add_func=self.add_chunk,
            model=self.model,
            check_token_func=lambda: self.load_token == current_token,
            on_finish=on_done,
            clear_on_first_chunk=(self.page_offset == 0)
        )

    def add_chunk(self, model, items):
        self.is_fetching = not self.last_fetch_has_more

        real_items_count = 0
        new_items = []

        for r in items:
            real_items_count += 1
            is_fav = r[5] if len(r) > 5 else False
            source_uid = r[6] if len(r) > 6 else None
            new_items.append(ContactItem(r[0], r[1], r[2], r[3], r[4], is_favorite=is_fav, source_uid=source_uid))

        if new_items:
            model.splice(model.get_n_items(), 0, new_items)

        self.page_offset += real_items_count
        self.spinner.set_visible(False)
        return False
