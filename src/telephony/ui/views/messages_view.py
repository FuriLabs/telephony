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

from ...backend.utils.datetime_utils import parse_timestamp

from gi.repository import Gtk, Adw, Gio, GLib, Pango
from loguru import logger
from gettext import gettext as _

from ...backend.utils.phone_utils import normalize_number
from ...backend.utils.locale_utils import get_date_format
from ..widgets.messages_rows_widget import ConversationRowFactory
from .chat_view import ChatPage
from ..widgets.common_widget import DataLoader
from ...backend.utils.thread_utils import run_in_background
from ...backend.utils.model_utils import ConversationItem


class MessagesView(Adw.Bin):
    """Main view for displaying the list of conversations and message search."""

    def __init__(self, db, app_window):
        self._refresh_timer = None
        """Initialize the messages view."""
        super().__init__()
        self.db = db
        self.app_window = app_window
        self.nav_view = Adw.NavigationView()
        self.set_child(self.nav_view)

        self.active_chat_number = None
        self.active_chat_page = None
        self.load_token = 0
        self.search_timer = None
        self.search_mode = 'contacts'

        self.page_limit = 50
        self.page_offset = 0
        self.is_fetching = False
        self.last_fetch_has_more = False

        self.selected_recipients = set()
        self._is_programmatic_update = False

        self.db.connect('messages-updated', self._on_messages_updated)
        self.db.connect('blocklist-updated', lambda *args: GLib.idle_add(lambda: self.refresh_list()))
        if self.app_window.eds:
            self.app_window.eds.connect('contacts-loaded', lambda *args: GLib.idle_add(lambda: self.refresh_list()))

        self.main_page = Adw.NavigationPage(tag="main_list")
        self.main_page.set_title(_("Messages"))
        self.setup_list_page()
        self.active_filter = "all"
        self.nav_view.add(self.main_page)

    def update_scheduled_message(self, msg_id):
        """Pass update notification to active chat page if relevant."""
        if self.active_chat_page:
            self.active_chat_page.refresh_message_status(msg_id)

    def _resolve_contact_name(self, contact_map, number):
        """Resolve contact name from map, handling list/string types."""
        norm_num = normalize_number(number)
        val = contact_map.get(norm_num)

        if val:
            if isinstance(val, list):
                try:
                    best = sorted(val, key=lambda x: x[0])[0]
                    return best[1]
                except (IndexError, TypeError):
                    logger.warning(f"[MessagesView] Invalid contact map entry for {number}: {val}")
                    return None
            elif isinstance(val, str):
                return val

        return None

    def cleanup(self):
        """Cleanup resources before destruction."""
        self.load_token += 1
        if self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

        if self.model:
            self.model.remove_all()

    def setup_list_page(self):
        """Setup the main conversation list page UI."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header_box = Gtk.Box(spacing=8)
        header_box.set_margin_start(10)
        header_box.set_margin_end(10)
        header_box.set_margin_top(10)
        header_box.set_margin_bottom(10)

        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search Contacts..."))
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self.on_search_changed)
        header_box.append(self.search_entry)

        self.action_box = Gtk.Box(spacing=6)
        self.action_box.set_visible(False)

        self.btn_cancel = Gtk.Button(icon_name="process-stop-symbolic", label=_("Cancel"), css_classes=["destructive-action"])
        self.btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_cancel_clicked(b) or False))
        self.action_box.append(self.btn_cancel)

        self.btn_compose = Gtk.Button(icon_name="mail-send-symbolic", css_classes=["suggested-action"])
        self.btn_compose.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_compose_clicked(b) or False))
        self.action_box.append(self.btn_compose)

        header_box.append(self.action_box)

        self.toggle_box = Gtk.Box(spacing=4)
        self.btn_contacts = Gtk.Button(icon_name="system-users-symbolic", css_classes=["suggested-action"])
        self.btn_contacts.add_css_class("circular")
        self.btn_contacts.connect("clicked", lambda b: GLib.idle_add(lambda: self.set_search_mode("contacts") or False))
        self.btn_msgs = Gtk.Button(icon_name="mail-unread-symbolic")
        self.btn_msgs.add_css_class("circular")
        self.btn_msgs.connect("clicked", lambda b: GLib.idle_add(lambda: self.set_search_mode("messages") or False))
        self.toggle_box.append(self.btn_contacts)
        self.toggle_box.append(self.btn_msgs)
        header_box.append(self.toggle_box)

        self.btn_filter = Gtk.MenuButton(icon_name="view-more-symbolic")
        self.btn_filter.add_css_class("circular")
        self.setup_filter_popover()
        header_box.append(self.btn_filter)

        box.append(header_box)

        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_vexpand(True)

        self.model = Gio.ListStore(item_type=ConversationItem)
        self.selection = Gtk.SingleSelection(model=self.model)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", ConversationRowFactory.setup)
        factory.connect("bind", self._on_bind_row)

        self.list_view = Gtk.ListView(model=self.selection, factory=factory)
        self.list_view.set_single_click_activate(True)
        self.list_view.connect("activate", lambda lv, pos: self.on_activate_conv(lv, pos))

        self.scrolled = Gtk.ScrolledWindow(child=self.list_view)
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

        self.list_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list_vbox.append(self.scrolled)
        self.list_vbox.append(self.spinner)

        self.content_stack.add_named(self.list_vbox, "conversations")

        self.results_list = Gtk.ListBox(css_classes=["boxed-list"])
        self.results_list.set_margin_start(10)
        self.results_list.set_margin_end(10)
        self.results_list.set_margin_top(10)
        self.results_list.set_margin_bottom(10)
        self.results_list.set_selection_mode(Gtk.SelectionMode.NONE)

        self.content_stack.add_named(Gtk.ScrolledWindow(child=self.results_list), "search_results")

        box.append(self.content_stack)
        self.main_page.set_child(box)
        self.refresh_list()

    def on_filter_click(self, btn, val):
        """Handle filter selection."""
        self.active_filter = val
        GLib.idle_add(lambda: self.filter_popover.popdown() or False)
        self._update_filter_visuals()
        self.refresh_list()

    def _update_filter_visuals(self):
        """Update the filter button icon and state based on active filter."""
        icons = {
            "all": "view-more-symbolic",
            "unread": "mail-unread-symbolic",
            "unknown": "user-not-tracked-symbolic",
            "saved": "contact-new-symbolic",
            "group": "system-users-symbolic",
            "individual": "avatar-default-symbolic",
            "alphabetical": "format-text-direction-ltr-symbolic"
        }
        icon_name = icons.get(self.active_filter, "view-more-symbolic")
        self.btn_filter.set_icon_name(icon_name)

        if self.active_filter == "all":
            self.btn_filter.remove_css_class("suggested-action")
        else:
            self.btn_filter.add_css_class("suggested-action")

    def setup_filter_popover(self):
        """Setup the filter popover for messages."""
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        pop_box.set_margin_top(12)
        pop_box.set_margin_bottom(12)
        pop_box.set_margin_start(12)
        pop_box.set_margin_end(12)
        pop_box.set_size_request(250, -1)

        opts = [
            (_("All Messages"), "all"),
            (_("Unread Messages"), "unread"),
            (_("Unknown Numbers"), "unknown"),
            (_("Saved Contacts"), "saved"),
            (_("Group Messages"), "group"),
            (_("Individual Messages"), "individual"),
            (_("Alphabetical Senders"), "alphabetical")
        ]

        for lbl, val in opts:
            b = Gtk.Button(label=lbl)
            b.set_halign(Gtk.Align.FILL)
            b.connect("clicked", lambda btn, v=val: GLib.idle_add(lambda: self.on_filter_click(btn, v) or False))
            pop_box.append(b)

        self.filter_popover = Gtk.Popover()
        self.filter_popover.set_child(pop_box)
        self.btn_filter.set_popover(self.filter_popover)

    def handle_incoming_ui(self, target_chat_id, body, attachments=[], msg_sender=None):
        """Handle incoming message updates for the UI."""
        self.refresh_list()

        actual_sender = msg_sender if msg_sender else target_chat_id

        if self.active_chat_number and self.active_chat_page:
            norm_target = normalize_number(target_chat_id) if "," not in target_chat_id else target_chat_id

            is_match = False

            if isinstance(self.active_chat_number, list):
                active_id = ",".join(sorted([normalize_number(n) for n in self.active_chat_number]))
                if norm_target == active_id:
                    is_match = True

            else:
                norm_active = normalize_number(self.active_chat_number) if "," not in str(self.active_chat_number) else self.active_chat_number
                if norm_target == norm_active:
                    is_match = True

            if is_match:
                is_focused = self.app_window.is_active()
                self.active_chat_page.inject_incoming(body, attachments=attachments, is_window_active=is_focused, sender=actual_sender)
                return is_focused

        return False

    def check_and_clear_notification(self, number):
        """Clear notifications for the given number."""
        try:
            if (self.app_window and self.app_window.app is not None) and (self.app_window.app is not None):
                self.app_window.app.clear_notification(number)
                norm = normalize_number(number)
                self.app_window.app.clear_notification(norm)
                self.app_window.app.clear_notification(f"missed_{norm}")
        except Exception as e:
            logger.warning(f"[Messages] Notification clear warning: {e}")
        return number

    def on_scroll_changed(self, adj):
        """Handle scroll position change."""
        if self.is_fetching or not self.last_fetch_has_more:
            return
        is_at_bottom = (adj.get_value() + adj.get_page_size()) >= (adj.get_upper() - 800)
        if is_at_bottom:
            self._start_fetch()

    def _on_messages_updated(self, _db, chat_id, change):
        """React to message database changes, updating single rows when possible."""
        if (not chat_id or change not in ("insert", "status")
                or self.active_filter != "all" or self.search_entry.get_text().strip()):
            self.refresh_list()
            return

        run_in_background(self.db.get_conversation_summary, chat_id,
                          on_complete=lambda summary: self._apply_row_update(chat_id, summary, change))

    def _apply_row_update(self, chat_id, summary, change):
        """Update a single conversation row in place from a fresh summary."""
        if summary is None:
            self.refresh_list()
            return

        old_item = None
        idx = -1
        for i in range(self.model.get_n_items()):
            item = self.model.get_item(i)
            if item.number == chat_id:
                idx = i
                old_item = item
                break

        if old_item is None:
            self.refresh_list()
            return

        body, ts, status, unread = summary
        if not body:
            body = _("[Multimedia Message]")

        new_item = ConversationItem(chat_id, old_item.name, body, ts, unread, status=status)
        target = 0 if change == "insert" else idx
        self.model.remove(idx)
        self.model.insert(target, new_item)

    def refresh_list(self):
        """Trigger a reload of the conversation list."""
        if (self._refresh_timer is not None) and self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
        self._refresh_timer = GLib.timeout_add(200, self._do_refresh_list)

    def _do_refresh_list(self):
        self._refresh_timer = None
        if self.active_chat_number and self.active_chat_page:
            self._update_active_chat_title()

        self.load_token += 1
        self.page_offset = 0
        self._start_fetch()
        return False

    def _update_active_chat_title(self):
        """Update title of the active chat page based on current blocklist/contact status."""
        target = self.active_chat_number
        name = None

        if isinstance(target, list):
            custom_name = self.db.get_group_name(target)
            if custom_name:
                name = custom_name
        else:
            norm = normalize_number(target)
            if self.db.is_blocked(norm):
                name = _("Blocked Number")
            else:
                custom_name = self.db.get_group_name([target])
                if custom_name:
                    name = custom_name
                else:
                    name = self.app_window.eds.get_display_name(target) or _("Unknown")

        if name == "Unknown":
            name = _("Unknown")

        if name and self.active_chat_page:
            self.active_chat_page.update_contact_details(name)

    def _start_fetch(self):
        """Start fetching data in background."""
        current_token = self.load_token
        self.is_fetching = True
        self.spinner.set_visible(True)

        def _bg_fetch():
            limit = self.page_limit
            if self.page_offset == 0:
                try:
                    count = self.db.get_total_unread_count()
                    limit = max(50, count + 10)
                except Exception as e:
                    logger.warning(f"[MessagesView] Unread count error: {e}")
            return self._fetch_and_process(self.page_offset, limit=limit)

        DataLoader.load_data(
            fetch_func=_bg_fetch,
            model_add_func=self.add_chunk,
            model=self.model,
            check_token_func=lambda: self.load_token == current_token,
            clear_on_first_chunk=(self.page_offset == 0)
        )

    def _fetch_and_process(self, offset, limit=None):
        """Fetch rows from DB and process them."""
        current_limit = limit if limit is not None else self.page_limit
        raw_rows = self.db.get_conversations(limit=current_limit + 1, offset=offset, filter_type=self.active_filter)
        contact_map = self.db.get_contacts_lookup_map()
        group_names = self.db.get_all_group_names()
        blocked_numbers = {normalize_number(b[1]) for b in self.db.get_blocked_numbers()}

        processed = []
        has_more = False

        if len(raw_rows) > current_limit:
            has_more = True
            raw_rows = raw_rows[:current_limit]

        for r in raw_rows:
            num, body, ts, unread, _ignored, status = r[0], r[1], r[2], r[3], r[4], r[5]

            if "," in num:
                recipients = [n.strip() for n in num.split(',')]
                custom_name = group_names.get(num)

                if custom_name:
                    name = custom_name
                else:
                    display_names = []
                    for rc in recipients:
                        resolved = self._resolve_contact_name(contact_map, rc)
                        display_names.append(resolved if resolved else rc)
                    name = ", ".join(display_names)
            else:
                norm = normalize_number(num)
                if norm in blocked_numbers:
                    name = _("Blocked Number")
                else:
                    custom_name = group_names.get(num)
                    if custom_name:
                        name = custom_name
                    else:
                        name = self._resolve_contact_name(contact_map, num)
                        if not name:
                            if any(c.isalpha() for c in num):
                                name = _("Unknown")
                            else:
                                name = num
                        elif name == "Unknown":
                            name = _("Unknown")

            if not body:
                body = _("[Multimedia Message]")

            processed.append({
                "number": num,
                "name": name,
                "body": body,
                "ts": ts,
                "unread": unread,
                "status": status
            })

        self.last_fetch_has_more = has_more

        return processed

    def add_chunk(self, model, items):
        """Add processed items to the model."""
        self.is_fetching = False
        self.list_view.set_opacity(1)

        real_items_count = 0
        new_conv_items = []

        for d in items:
            real_items_count += 1
            new_conv_items.append(ConversationItem(
                d["number"], d["name"], d["body"], d["ts"], d["unread"], status=d.get("status")
            ))

        if new_conv_items:
            model.splice(model.get_n_items(), 0, new_conv_items)

        self.page_offset += real_items_count

        self.spinner.set_visible(False)

        return False

    def _on_bind_row(self, factory, list_item):
        """Bind row items to widgets."""
        ConversationRowFactory.bind(factory, list_item)

    def on_activate_conv(self, lv, pos):
        """Handle activation of a conversation row."""
        item = self.model.get_item(pos)
        self.open_chat(item.number, item.name)

    def close_active_chat(self):
        """Close the currently active chat page."""
        self._reset_search_ui()
        self.active_filter = "all"
        self._update_filter_visuals()
        GLib.idle_add(lambda: self.nav_view.pop() or False)

    def open_chat(self, number_or_list, name=None, target_msg_id=None, prefill_text=None, prefill_attachments=None):
        """Open a chat page for the specified number or list of numbers."""
        canonical_target = number_or_list
        if isinstance(number_or_list, str) and "," in number_or_list:
            canonical_target = sorted([n.strip() for n in number_or_list.split(',')])
        elif isinstance(number_or_list, list):
            canonical_target = sorted(number_or_list)

        if isinstance(canonical_target, list):
            target_number = canonical_target
            chat_tag = f"chat_{'_'.join(target_number)}"

            grp_id = ",".join(target_number)
            self.check_and_clear_notification(grp_id)

            custom_name = self.db.get_group_name(target_number)
            if custom_name:
                name = custom_name
            elif not name:
                names = []
                for n in target_number:
                    names.append(self.app_window.eds.get_display_name(n) or n)
                name = ", ".join(names)
        else:
            target_number = self.check_and_clear_notification(canonical_target)
            chat_tag = f"chat_{target_number}"
            custom_name = self.db.get_group_name([target_number])
            if custom_name:
                name = custom_name
            elif not name or name == target_number:
                name = self.app_window.eds.get_display_name(target_number) or target_number

        if name == "Unknown":
            name = _("Unknown")

        if self.active_chat_number == target_number and not target_msg_id:
            if (prefill_text or prefill_attachments) and self.active_chat_page:
                self.active_chat_page.append_prefill(prefill_text, prefill_attachments)
            return

        self.nav_view.pop_to_page(self.main_page)

        unread_count = 0
        if not target_msg_id:
            unread_count = self.db.get_unread_count(target_number)

        page = Adw.NavigationPage(title=name, tag=chat_tag)

        self.active_chat_number = target_number

        self.active_chat_page = ChatPage(self.db, self.app_window, target_number, self, name, target_msg_id, unread_count=unread_count, prefill_text=prefill_text, prefill_attachments=prefill_attachments)

        page.set_child(self.active_chat_page)

        created_page = self.active_chat_page

        def on_pop(p):
            if self.active_chat_page is created_page:
                self.active_chat_number = None
                self.active_chat_page = None
                self.app_window.ofono.set_active_chat(None)
                self._reset_search_ui()
                self.active_filter = "all"
                self._update_filter_visuals()
                self.refresh_list()

        page.connect("hidden", on_pop)
        self.nav_view.push(page)

        self.app_window.ofono.set_active_chat(target_number)

    def set_search_mode(self, mode):
        """Set the search mode to either 'contacts' or 'messages'."""
        self.search_mode = mode
        if mode == "contacts":
            self.btn_contacts.add_css_class("suggested-action")
            self.btn_msgs.remove_css_class("suggested-action")
            self.search_entry.set_placeholder_text(_("Search Contacts..."))
        else:
            self.btn_contacts.remove_css_class("suggested-action")
            self.btn_msgs.add_css_class("suggested-action")
            self.search_entry.set_placeholder_text(_("Search Messages..."))
        self.on_search_changed(self.search_entry)

    def on_search_changed(self, entry):
        """Handle search entry text changes."""
        if self._is_programmatic_update:
            return

        text = entry.get_text().strip()
        count = len(self.selected_recipients)

        self._update_compose_button()

        if not text and count == 0:
            self.content_stack.set_visible_child_name("conversations")
            return

        self.content_stack.set_visible_child_name("search_results")
        if self.search_timer:
            GLib.source_remove(self.search_timer)
        self.search_timer = GLib.timeout_add(200, self.do_search_debounced)

    def do_search_debounced(self):
        """Perform search after a short delay."""
        query = self.search_entry.get_text()
        if self.search_mode == "contacts":
            self.perform_contact_search(query)
        else:
            self.perform_message_search(query)
        self.search_timer = None
        return False

    def _update_compose_button(self):
        """Refresh the compose button label based on selection and existing threads."""
        count = len(self.selected_recipients)
        if count == 0:
            self.action_box.set_visible(False)
            self.toggle_box.set_visible(True)
            return

        self.action_box.set_visible(True)
        self.toggle_box.set_visible(False)
        self.btn_compose.set_label(_("Start ({count})").format(count=count))

        recipients = list(self.selected_recipients)
        target = recipients[0] if count == 1 else recipients

        def done(exists):
            if exists and self.selected_recipients == set(recipients):
                self.btn_compose.set_label(_("Open ({count})").format(count=count))

        run_in_background(self.db.conversation_exists, target, on_complete=done)

    def on_compose_clicked(self, btn):
        """Handle click on compose button."""
        if not self.selected_recipients:
            return
        recipients = list(self.selected_recipients)

        self._reset_search_ui()

        if len(recipients) == 1:
            self.open_chat(recipients[0], None)
        else:
            self.open_chat(recipients, None)

    def on_cancel_clicked(self, btn):
        """Handle click on cancel button."""
        self._reset_search_ui()

    def _reset_search_ui(self):
        """Reset the search UI state."""
        self._is_programmatic_update = True
        self.selected_recipients.clear()
        self.search_entry.set_text("")
        self._is_programmatic_update = False

        self.content_stack.set_visible_child_name("conversations")
        self.action_box.set_visible(False)
        self.toggle_box.set_visible(True)

    def _toggle_selection(self, number, check_btn=None, clear_search=False):
        """Toggle selection of a contact in search results."""
        if number in self.selected_recipients:
            self.selected_recipients.remove(number)
            if check_btn:
                with check_btn.handler_block(check_btn.handler_id):
                    check_btn.set_active(False)
        else:
            self.selected_recipients.add(number)
            if check_btn:
                with check_btn.handler_block(check_btn.handler_id):
                    check_btn.set_active(True)

        if clear_search:
            self._is_programmatic_update = True
            self.search_entry.set_text("")
            self._is_programmatic_update = False

        self._update_compose_button()

    def perform_contact_search(self, query):
        """Search for contacts matching the query."""
        self.contact_search_token = self.contact_search_token + 1
        current_token = self.contact_search_token

        norm_query = normalize_number(query)

        manual_entry_row = None
        if norm_query and len(norm_query) >= 2:
            row_box = Gtk.Box(spacing=12)
            row_box.set_margin_start(8)
            row_box.set_margin_end(8)
            row_box.set_margin_top(8)
            row_box.set_margin_bottom(8)

            chk = Gtk.CheckButton()
            chk.set_active(norm_query in self.selected_recipients)
            chk.handler_id = chk.connect("toggled", lambda b: GLib.idle_add(lambda: self._toggle_selection(norm_query, b, clear_search=True) or False))
            row_box.append(chk)

            lbl = Gtk.Label(label=_("Add Number: {number}").format(number=norm_query), xalign=0, hexpand=True, css_classes=["heading"])
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            row_box.append(lbl)

            row = Gtk.ListBoxRow()
            row.set_child(row_box)
            ctrl = Gtk.GestureClick()
            ctrl.connect("released", lambda c, n, x, y: GLib.idle_add(lambda: self._toggle_selection(norm_query, chk, clear_search=True) or False))
            row.add_controller(ctrl)
            manual_entry_row = row

        def _bg_fetch():
            res = self.app_window.eds.search_contacts(query, limit=30)
            s_map = {}
            if self.app_window.eds and (self.app_window.eds is not None):
                sources = self.app_window.eds.get_sources_info()
                for s in sources:
                    s_map[s['uid']] = s['name']
            convs = self._search_conversations(query, res)
            return res, s_map, convs

        def _on_done(results, source_map, conversations):
            if self.contact_search_token != current_token:
                return False

            while c := self.results_list.get_first_child():
                self.results_list.remove(c)

            if manual_entry_row:
                self.results_list.append(manual_entry_row)

            for conv_id, display_name, is_group in conversations:
                row = Adw.ActionRow(title=display_name)
                row.set_subtitle(_("Group chat") if is_group else conv_id)
                row.set_activatable(True)
                row.add_prefix(Gtk.Image.new_from_icon_name("chat-message-new-symbolic"))
                row.connect("activated", lambda r, cid=conv_id: GLib.idle_add(
                    lambda: [self._reset_search_ui(), self.open_chat(cid, None)] and False))
                self.results_list.append(row)

            if not results and not conversations:
                lbl = Gtk.Label(label=_("No contacts found"), css_classes=["dim-label", "caption"])
                lbl.set_margin_top(20)
                self.results_list.append(lbl)
                return False

            for res in results:
                name = f"{res[1]} {res[2]}".strip()
                source_uid = res[6] if len(res) > 6 else None

                for (num, label) in res[3]:
                    norm_num = normalize_number(num)

                    row = Gtk.ListBoxRow()
                    box = Gtk.Box(spacing=12)
                    box.set_margin_start(8)
                    box.set_margin_end(8)
                    box.set_margin_top(8)
                    box.set_margin_bottom(8)

                    chk = Gtk.CheckButton()
                    chk.set_active(norm_num in self.selected_recipients)
                    chk.handler_id = chk.connect("toggled", lambda b, n=norm_num: GLib.idle_add(lambda: self._toggle_selection(n, b) or False))
                    box.append(chk)

                    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                    lbl_name = Gtk.Label(label=name, xalign=0, css_classes=["heading"])
                    lbl_name.set_ellipsize(Pango.EllipsizeMode.END)
                    lbl_num = Gtk.Label(label=f"{label}: {norm_num}", xalign=0, css_classes=["caption", "dim-label"])
                    lbl_num.set_ellipsize(Pango.EllipsizeMode.END)

                    vbox.append(lbl_name)
                    vbox.append(lbl_num)

                    if source_uid and source_uid in source_map:
                        s_name = source_map[source_uid]
                        lbl_source = Gtk.Label(label=s_name, xalign=0, css_classes=["tiny-label"])
                        lbl_source.set_ellipsize(Pango.EllipsizeMode.END)
                        vbox.append(lbl_source)

                    box.append(vbox)
                    row.set_child(box)

                    ctrl = Gtk.GestureClick()
                    ctrl.connect("released", lambda c, n, x, y, b=chk, nm=norm_num: GLib.idle_add(lambda: self._toggle_selection(nm, b) or False))
                    row.add_controller(ctrl)
                    self.results_list.append(row)
            return False

        def _bg_task():
            res, s_map, convs = _bg_fetch()
            GLib.idle_add(lambda: _on_done(res, s_map, convs))

        run_in_background(_bg_task)

    def _search_conversations(self, query, contact_results):
        """Find existing conversations matching the query by group name, number or member."""
        q = query.strip().lower()
        if not q:
            return []

        group_names = self.db.get_all_group_names()
        conv_ids = self.db.get_conversation_ids()
        norm_query = normalize_number(query)

        contact_numbers = set()
        for res in contact_results or []:
            for (num, _lbl) in res[3]:
                n = normalize_number(num)
                if n:
                    contact_numbers.add(n)

        matches = []
        for conv_id in conv_ids:
            custom = group_names.get(conv_id, "")
            is_group = "," in conv_id
            participants = [p.strip() for p in conv_id.split(",")] if is_group else [conv_id]

            hit = False
            if custom and q in custom.lower():
                hit = True
            elif norm_query and len(norm_query) >= 3 and any(norm_query in p for p in participants):
                hit = is_group
            elif is_group and contact_numbers and any(p in contact_numbers for p in participants):
                hit = True

            if hit:
                display = custom
                if not display:
                    names = [self.app_window.eds.get_display_name(p) or p for p in participants]
                    display = ", ".join(names)
                matches.append((conv_id, display, is_group))

            if len(matches) >= 10:
                break

        return matches

    def perform_message_search(self, query):
        """Search for messages matching the query."""
        self.message_search_token = self.message_search_token + 1
        current_token = self.message_search_token

        def _bg_fetch():
            results = self.db.search_messages(query)
            contact_map = self.db.get_contacts_lookup_map()
            return results, contact_map

        def _on_done(results, contact_map):
            if self.message_search_token != current_token:
                return False

            while c := self.results_list.get_first_child():
                self.results_list.remove(c)

            if not results:
                lbl = Gtk.Label(label=_("No results found"), css_classes=["dim-label", "caption"])
                lbl.set_margin_top(20)
                self.results_list.append(lbl)
                return False

            for r in results:
                number, body, ts, msg_id = r[0], r[1], r[2], r[3]
                name = self._resolve_contact_name(contact_map, number)
                if not name:
                    name = number

                if name == "Unknown":
                    name = _("Unknown")

                preview = str(body).replace("\n", " ")
                if len(preview) > 40:
                    preview = preview[:40] + "..."

                row = Adw.ActionRow(title=name, subtitle=preview)
                date_str = str(ts).split(" ")[0]
                try:
                    dt = parse_timestamp(str(ts))
                    date_str = dt.strftime(get_date_format())
                except Exception:
                    pass
                row.add_suffix(Gtk.Label(label=date_str, css_classes=["caption", "dim-label"]))

                btn = Gtk.Button(icon_name="go-next-symbolic", css_classes=["flat"])
                btn.connect("clicked", lambda b, n=number, mid=msg_id: GLib.idle_add(lambda: self.open_chat(n, None, target_msg_id=mid) or False))

                row.add_suffix(btn)
                self.results_list.append(row)
            return False

        def _bg_task():
            res, c_map = _bg_fetch()
            GLib.idle_add(lambda: _on_done(res, c_map))

        run_in_background(_bg_task)
