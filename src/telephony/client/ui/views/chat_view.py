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


from telephony.shared.utils.mms_utils import max_attachment_size
from telephony.shared.utils.thread_utils import run_in_background
from telephony.client.ui.windows.chat_media_controller_window import ChatMediaController
from telephony.shared.utils.datetime_utils import parse_timestamp

import json
import os
import mimetypes
from datetime import datetime
from gettext import gettext as _

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango, GObject, Gst
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.phone_utils import normalize_number, conversation_id
from telephony.client.utils.model_utils import MessageItem
from telephony.client.ui.windows.date_time_picker_window import DateTimePicker
from telephony.client.ui.windows.contact_picker_window import ContactPicker
from telephony.client.ui.widgets.chat_bubbles_widget import ChatBubbleFactory

LOAD_MORE_BATCH = 50
MAX_MESSAGES_IN_MEMORY = 200
RECENT_DUPLICATE_SCAN_ITEMS = 8


class ChatPage(Gtk.Box):
    """Page displaying the chat conversation history and input area."""

    def __init__(self, db, app_window, number_or_list, messages_view, contact_name="", target_id=None, unread_count=0, prefill_text=None, prefill_attachments=None):
        self._read_timer = None
        self._refresh_timer = None
        self.focus_handler_id = None
        self.search_timer = None
        self.search_token = 0
        self._draft_saved = False
        """Initialize the ChatPage."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.db = db
        self.app_window = app_window
        self.messages_view = messages_view
        self.attachments = []
        self._recipient_call_btns = []
        self.media_controller = ChatMediaController(self, self.app_window)

        self.initial_unread_count = unread_count

        if isinstance(number_or_list, str) and "," in number_or_list:
            self.is_group = True
            self.recipients = sorted([normalize_number(n.strip()) for n in number_or_list.split(',')])
            self.number = ",".join(self.recipients)
            self.history_enabled = True
        elif isinstance(number_or_list, list):
            self.is_group = True
            self.recipients = sorted([normalize_number(n) for n in number_or_list])
            self.number = ",".join(self.recipients)
            self.history_enabled = True
        else:
            self.is_group = False
            norm = normalize_number(number_or_list)
            self.recipients = [norm]
            self.number = norm
            self.history_enabled = True

        if not contact_name and any(c.isalpha() for c in self.number):
            self.contact_name = _("Unknown")
        else:
            self.contact_name = contact_name

        if self.contact_name == "Unknown":
            self.contact_name = _("Unknown")

        self.db_number = self.number

        self.is_loading = True
        self.is_jumping = False
        self.is_fetching_older = False
        self.all_messages_loaded = False
        self.target_highlight_id = target_id
        self.has_active_divider = False
        self._pending_initial_read = False

        self.full_history = []
        self.newest_db_offset = 0
        self.oldest_db_offset = 0

        if self.app_window:
            self.focus_handler_id = self.app_window.connect("notify::is-active", lambda obj, pspec: self.on_window_focus_changed(obj, pspec))
            self.connect("map", self.on_map)
            self.connect("unmap", self.on_unmap)

        self.db_signals = []
        self.eds_signals = []

        sig_id = self.db.connect('messages-updated', self._on_messages_updated)
        self.db_signals.append((self.db, sig_id))

        self._setup_ui()

        if prefill_text:
            self.msg_buffer.set_text(prefill_text)

        if prefill_attachments:
            for p in prefill_attachments:
                if p not in self.attachments:
                    self.attachments.append(p)
            self.refresh_attachment_ui()

        if self.history_enabled:
            if self.target_highlight_id:
                self.load_context_for_message(self.target_highlight_id)
            else:
                run_in_background(self._initial_load)
        else:
            self.store.insert(0, MessageItem(0, "divider", _("Draft to {count} recipients").format(count=len(self.recipients)), "", "", is_divider=True))
            self.content_stack.set_visible_child_name("chat")

    def _setup_ui(self):
        """Setup the chat page UI components."""
        header = Adw.HeaderBar(show_start_title_buttons=True, show_end_title_buttons=True)

        title_text = self.contact_name
        if title_text == self.number and any(c.isdigit() for c in str(self.number)):
            title_text = _("Unknown")

        subtitle_text = self.number

        if self.is_group:
            participant_names = []
            for num in self.recipients:
                name = self.app_window.eds.get_display_name(num)
                if not name:
                    if not any(c.isalpha() for c in num):
                        name = _("Unknown")
                    else:
                        name = num
                elif name == "Unknown":
                    name = _("Unknown")
                participant_names.append(name)

            full_str = ", ".join(participant_names)
            subtitle_text = full_str if len(full_str) < 50 else _("{count} Participants").format(count=len(self.recipients))
        self.title_widget = Adw.WindowTitle(title=title_text, subtitle=subtitle_text)
        header.set_title_widget(self.title_widget)

        self.btn_search = Gtk.ToggleButton(icon_name="system-search-symbolic", css_classes=["circular"])
        header.pack_end(self.btn_search)

        self.btn_menu = Gtk.ToggleButton(icon_name="view-more-symbolic", css_classes=["circular"])
        header.pack_end(self.btn_menu)

        self.append(header)

        self.details_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._details_built = False
        self.btn_menu.connect("toggled", self._ensure_details_panel)
        self.btn_menu.bind_property("active", self.details_revealer, "reveal-child", GObject.BindingFlags.BIDIRECTIONAL)
        self.append(self.details_revealer)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(hexpand=True)
        self.search_bar.set_child(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)

        self.btn_search.bind_property("active", self.search_bar, "search-mode-enabled", GObject.BindingFlags.BIDIRECTIONAL)
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.append(self.search_bar)

        def _on_btn_search_toggled(btn):
            if btn.get_active() and self.btn_menu.get_active():
                self.btn_menu.set_active(False)
        self.btn_search.connect("toggled", _on_btn_search_toggled)

        def _on_btn_menu_toggled(btn):
            if btn.get_active() and self.btn_search.get_active():
                self.btn_search.set_active(False)
            if btn.get_active():
                self._refresh_recipient_call_buttons()
        self.btn_menu.connect("toggled", _on_btn_menu_toggled)

        self.store = Gio.ListStore(item_type=MessageItem)

        self.filter = Gtk.CustomFilter.new(self._filter_func, None)
        self.filter_model = Gtk.FilterListModel.new(self.store, self.filter)

        self.selection = Gtk.SingleSelection(model=self.filter_model)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", ChatBubbleFactory.unbind)
        factory.connect("teardown", ChatBubbleFactory.teardown)

        self.list_view = Gtk.ListView(model=self.selection, factory=factory)
        self.scrolled = Gtk.ScrolledWindow(child=self.list_view, vexpand=True)
        self.scrolled.add_css_class("inverted-list")

        self.chat_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.loading_spinner = Adw.Spinner()
        self.loading_spinner.set_size_request(24, 24)
        self.loading_spinner.set_margin_top(12)
        self.loading_spinner.set_margin_bottom(12)
        self.loading_spinner.set_halign(Gtk.Align.CENTER)
        self.loading_spinner.set_visible(False)
        self.chat_vbox.append(self.loading_spinner)
        self.chat_vbox.append(self.scrolled)

        self.content_stack = Gtk.Stack()
        self.content_stack.add_named(Gtk.Box(), "loading")
        self.content_stack.add_named(self.chat_vbox, "chat")

        self.search_results_list = Gtk.ListBox(css_classes=["boxed-list"])
        self.search_results_list.set_margin_start(10)
        self.search_results_list.set_margin_end(10)
        self.search_results_list.set_margin_top(10)
        self.search_results_list.set_margin_bottom(10)
        self.search_results_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.search_scrolled = Gtk.ScrolledWindow(child=self.search_results_list)
        self.content_stack.add_named(self.search_scrolled, "search_results")

        self.content_stack.set_visible_child_name("loading")
        self.append(self.content_stack)

        self.v_adj = self.scrolled.get_vadjustment()
        self.v_adj.connect("value-changed", self.on_scroll_changed)

        self.att_scrolled = Gtk.ScrolledWindow()
        self.att_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.att_scrolled.set_min_content_height(40)
        self.att_scrolled.set_margin_start(10)
        self.att_scrolled.set_margin_end(10)
        self.att_scrolled.set_visible(False)

        self.att_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.att_scrolled.set_child(self.att_box)

        self.append(self.att_scrolled)

        input_box = Gtk.Box(spacing=6)
        input_box.set_margin_start(6)
        input_box.set_margin_end(6)
        input_box.set_margin_top(6)
        input_box.set_margin_bottom(6)
        input_box.set_valign(Gtk.Align.END)

        btn_attach = Gtk.Button(icon_name="pan-up-symbolic", css_classes=["circular", "suggested-action"])
        btn_attach.connect("clicked", lambda b: GLib.idle_add(lambda: self.media_controller.on_attach_clicked(b) or False))
        btn_attach.set_valign(Gtk.Align.CENTER)
        input_box.append(btn_attach)

        self.msg_buffer = Gtk.TextBuffer()
        self.msg_buffer.connect("changed", self.on_input_changed)

        self.msg_view = Gtk.TextView(buffer=self.msg_buffer)
        self.msg_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.msg_view.set_accepts_tab(False)
        self.msg_view.connect("paste-clipboard", self._on_paste_clipboard)

        self.msg_scroll = Gtk.ScrolledWindow()
        self.msg_scroll.set_child(self.msg_view)

        self.msg_scroll.set_min_content_height(20)
        self.msg_scroll.set_size_request(-1, 38)

        self.msg_scroll.set_has_frame(False)
        self.msg_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self.msg_scroll.set_propagate_natural_height(True)
        self.msg_scroll.set_max_content_height(140)

        self.msg_scroll.set_hexpand(True)
        self.msg_scroll.set_valign(Gtk.Align.CENTER)
        self.msg_scroll.add_css_class("chat-input")

        input_box.append(self.msg_scroll)

        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.msg_view.add_controller(key_controller)

        self.btn_send = Gtk.Button(icon_name="mail-send-symbolic", css_classes=["suggested-action", "btn-green", "circular"])
        self.btn_send.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_send(b) or False))
        self.btn_send.set_valign(Gtk.Align.CENTER)

        input_box.append(self.btn_send)

        if any(c.isalpha() for c in self.number):
            self.btn_send.set_sensitive(False)
            self.msg_view.set_editable(False)
            self.msg_view.set_cursor_visible(False)
            btn_attach.set_sensitive(False)
            self.msg_buffer.set_text(_("Replies not supported for this sender"))

        self.append(input_box)

    def _on_factory_setup(self, factory, list_item):
        """Setup handler for list item factory."""
        ChatBubbleFactory.setup(factory, list_item)

    def _on_factory_bind(self, factory, list_item):
        """Bind handler for list item factory."""
        ChatBubbleFactory.bind(factory, list_item,
                               delete_callback=self.on_delete_message,
                               contact_lookup=self.app_window.eds.get_display_name,
                               add_contact_cb=self.app_window.present_edit_contact,
                               is_group=self.is_group,
                               forward_callback=self.on_forward_message,
                               reschedule_cb=self.on_reschedule_message,
                               unread_callback=self.on_mark_unread
                               )

    def on_mark_unread(self, message_id):
        """Mark conversation as unread from specific message ID upwards."""
        def done(_result):
            if (self.messages_view is not None):
                self.messages_view.close_active_chat()

        run_in_background(self.app_window.daemon.mark_conversation_unread,
                          self.db_number, message_id, on_complete=done)

    def on_reschedule_message(self, item):
        """Reschedule a scheduled message or retry a failed one."""
        if item.status == "failed":
            self.on_retry_message(item)
            return

        initial = None
        if item.scheduled_timestamp:
            try:
                dt_p = parse_timestamp(item.scheduled_timestamp)
                initial = GLib.DateTime.new_local(dt_p.year, dt_p.month, dt_p.day, dt_p.hour, dt_p.minute, dt_p.second)
            except Exception as e:
                logger.warning(f"Failed to parse scheduled timestamp: {e}")

        def _on_picked(dt):
            now = GLib.DateTime.new_now_local()
            if dt.compare(now) <= 0:
                self.app_window.notify_error(_("Scheduled time must be in the future"))
                return

            ts_str = dt.format("%Y-%m-%d %H:%M:%S")

            def done(_result):
                self._reload_chat()
                self.app_window.notify_success(_("Rescheduled for {time}").format(time=ts_str))

            run_in_background(self.app_window.daemon.reschedule_message, item.id, ts_str,
                              on_complete=done)

        DateTimePicker(
            parent=self.app_window,
            title=_("Re-Schedule Message"),
            include_time=True,
            initial_date=initial,
            on_confirm=_on_picked
        )

    def _start_load_thread(self):
        """Start thread to load older messages."""
        if self.is_fetching_older or not self.app_window.is_active():
            return False

        self.is_fetching_older = True
        self.loading_spinner.set_visible(True)
        run_in_background(self._fetch_older_messages)
        return False

    def _fetch_older_messages(self):
        """Fetch older messages from DB."""
        try:
            offset = self.oldest_db_offset
            limit = LOAD_MORE_BATCH
            msgs = self.db.get_chat_messages(self.number, limit=limit + 1, offset=offset)

            has_more = False
            if len(msgs) > limit:
                has_more = True
                msgs = msgs[:limit]

            msgs.sort(key=lambda x: x[3] if len(x) > 3 and x[3] else "")
            GLib.idle_add(self._insert_older_messages, msgs, has_more)
        except Exception as e:
            logger.warning(f"[ChatPage] Fetch older messages failed: {e}")
            GLib.idle_add(self._fetch_older_failed)

    def _fetch_older_failed(self):
        self.is_fetching_older = False
        self.loading_spinner.set_visible(False)
        return False

    def _insert_older_messages(self, msgs, has_more):
        """Insert loaded messages into the model."""
        self.full_history = msgs + self.full_history
        new_items = []

        msgs_reversed = list(reversed(msgs))

        for m in msgs_reversed:
            msg_id = m[0]
            subject = m[5] if len(m) > 5 else None
            att = m[6] if len(m) > 6 else []
            sender_val = m[7] if len(m) > 7 else "Unknown"
            sched_ts = m[8] if len(m) > 8 else None
            new_items.append(MessageItem(msg_id, m[1], m[2], m[3], m[4], subject=subject, attachments=att, sender=sender_val, scheduled_timestamp=sched_ts))

        if not new_items:
            self.is_fetching_older = False
            self.loading_spinner.set_visible(False)
            if not has_more:
                self.all_messages_loaded = True
            return False

        n_items = self.store.get_n_items()
        self.store.splice(n_items, 0, new_items)

        self.oldest_db_offset += len(new_items)

        new_total = self.store.get_n_items()
        if new_total > MAX_MESSAGES_IN_MEMORY:
            remove_count = new_total - MAX_MESSAGES_IN_MEMORY
            self.store.splice(0, remove_count, [])
            self.newest_db_offset += remove_count

        self.is_fetching_older = False
        self.loading_spinner.set_visible(False)

        if not has_more:
            self.all_messages_loaded = True

        return False

    def _initial_load(self):
        """Perform initial message load."""
        try:
            limit = max(50, self.initial_unread_count + 10)
            msgs = self.db.get_chat_messages(self.number, limit=limit + 1, offset=0)

            draft_msg = None
            if msgs:
                drafts = [m for m in msgs if m[4] == 'draft']
                if drafts:
                    draft_msg = drafts[0]
                    msgs = [m for m in msgs if m[4] != 'draft']

            has_more = False
            if len(msgs) > limit:
                has_more = True
                msgs = msgs[:limit]

            msgs.sort(key=lambda x: x[3] if len(x) > 3 and x[3] else "")
            GLib.idle_add(self._update_initial_ui, msgs, has_more, draft_msg)
        except Exception as e:
            logger.error(f"[ChatPage] Initial load failed: {e}")
            GLib.idle_add(lambda: self.content_stack.set_visible_child_name("chat"))

    def _update_initial_ui(self, msgs, has_more, draft_msg=None):
        """Populate the model with initial messages."""
        self.full_history = msgs
        items = []

        if draft_msg:
            has_prefill = self.msg_buffer.get_char_count() > 0 or bool(self.attachments)
            if not has_prefill:
                body = draft_msg[2]
                atts = draft_msg[6]
                self.msg_buffer.set_text(body if body else "")
                if atts:
                    for a in atts:
                        if a not in self.attachments:
                            self.attachments.append(a)
                    self.refresh_attachment_ui()

        if not has_more:
            self.all_messages_loaded = True
        else:
            self.all_messages_loaded = False

        has_unread = False
        first_unread_index = -1
        target_index = -1

        for m in msgs:
            msg_id = m[0]
            status = m[4]
            if status == 'unread' and not has_unread:
                has_unread = True
                items.append(MessageItem(0, "divider", _("— New Messages —"), "", "", is_divider=True))
                first_unread_index = len(items) - 1
                self.has_active_divider = True

            subject = m[5] if len(m) > 5 else None
            att = m[6] if len(m) > 6 else []
            sender_val = m[7] if len(m) > 7 else "Unknown"
            sched_ts = m[8] if len(m) > 8 else None

            items.append(MessageItem(msg_id, m[1], m[2], m[3], m[4], subject=subject, attachments=att, sender=sender_val, scheduled_timestamp=sched_ts))

            if self.target_highlight_id and msg_id == self.target_highlight_id:
                target_index = len(items) - 1

        items.reverse()
        if target_index >= 0:
            target_index = len(items) - 1 - target_index
        if first_unread_index >= 0:
            first_unread_index = len(items) - 1 - first_unread_index

        self.store.remove_all()
        self.store.splice(0, 0, items)

        self.newest_db_offset = 0
        self.oldest_db_offset = len(items)

        final_idx = 0
        if target_index >= 0:
            final_idx = target_index
        elif has_unread and first_unread_index >= 0:
            final_idx = first_unread_index

        self._finalize_loading(final_idx)

    def _finalize_loading(self, index):
        """Finalize loading, scroll to position, and reveal."""
        self.content_stack.set_visible_child_name("chat")

        def reveal():
            if self.store.get_n_items() > index:
                self.list_view.scroll_to(index, Gtk.ListScrollFlags.FOCUS | Gtk.ListScrollFlags.SELECT, None)
            else:
                self.list_view.scroll_to(0, Gtk.ListScrollFlags.NONE, None)

            self.is_loading = False
            if self.app_window.is_active():
                self._mark_read_debounced()
            else:
                self._pending_initial_read = True
            return False

        GLib.timeout_add(50, reveal)

    def on_search_changed(self, entry):
        """Handle search query changes."""
        query = entry.get_text().strip()
        if not query:
            self.content_stack.set_visible_child_name("chat")
            return

        self.content_stack.set_visible_child_name("search_results")

        if (self.search_timer is not None) and self.search_timer:
            GLib.source_remove(self.search_timer)
        self.search_timer = GLib.timeout_add(200, self.perform_search, query)

    def perform_search(self, query):
        """Execute message search."""
        self.search_token = self.search_token + 1
        current_token = self.search_token

        def _fetch():
            return self.db.search_messages(query, chat_id=self.db_number, limit=100)

        def _on_done(rows):
            if self.search_token != current_token:
                return False

            while child := self.search_results_list.get_first_child():
                self.search_results_list.remove(child)

            if not rows:
                lbl = Gtk.Label(label=_("No results found in this conversation"), css_classes=["dim-label"])
                lbl.set_margin_top(20)
                self.search_results_list.append(lbl)
                return False

            for r in rows:
                body = r[1]
                ts = r[2]
                msg_id = r[3]

                preview = str(body).replace("\n", " ")
                if len(preview) > 60:
                    preview = preview[:60] + "..."

                row = Adw.ActionRow(title=preview, subtitle=str(ts))
                row.set_activatable(True)

                btn_jump = Gtk.Button(icon_name="go-next-symbolic", css_classes=["flat", "circular"])
                btn_jump.set_valign(Gtk.Align.CENTER)
                btn_jump.connect("clicked", lambda b, mid=msg_id: GLib.idle_add(lambda: [self.load_context_for_message(mid), self.content_stack.set_visible_child_name("chat")] and False))
                row.add_suffix(btn_jump)

                self.search_results_list.append(row)

        def _bg_task():
            try:
                rows = _fetch()
                GLib.idle_add(_on_done, rows)
            except Exception as e:
                logger.warning(f"[ChatPage] Search failed: {e}")

        run_in_background(_bg_task)
        self.search_timer = None
        return False

    def _filter_func(self, item, user_data):
        """Filter function for the list model."""
        return True

    def on_input_changed(self, buffer):
        """Handle input text changes (auto-resize)."""
        if buffer.get_line_count() > 3:
            self.msg_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        else:
            self.msg_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        """Handle key presses (Shift+Enter for new line)."""
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            if state & Gdk.ModifierType.SHIFT_MASK:
                if self.btn_send.get_sensitive():
                    self.on_send()
                return True
            return False
        return False

    def _ensure_details_panel(self, btn):
        """Build the details panel the first time the menu is opened."""
        if btn.get_active() and not self._details_built:
            self._details_built = True
            self.setup_details_panel()

    def _conversation_id(self):
        """Return the stable id of this conversation."""
        return conversation_id(self.recipients if self.is_group else self.number)

    def _on_mute_toggled(self, row, _pspec):
        """Persist the mute preference for this conversation."""
        muted = row.get_active()
        self.app_window.gsettings_mgr.set_conversation_muted(self._conversation_id(), muted)
        self.db.emit('messages-updated', "", "status")

    def _refresh_recipient_call_buttons(self):
        """Sync recipient call buttons with the current dial availability."""
        available = self.app_window.ofono.dialing_available() if self.app_window.ofono else True
        for btn in self._recipient_call_btns:
            btn.set_sensitive(available)

    def setup_details_panel(self):
        """Setup the conversation details slide-down panel."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.add_css_class("background")

        if self.is_group:
            lbl = Gtk.Label(label=_("Group Name"), xalign=0, css_classes=["heading"])
            entry = Gtk.Entry(placeholder_text=_("Set group name..."))
            current_name = self.contact_name if self.contact_name != ", ".join(self.recipients) else ""
            entry.set_text(current_name)
            entry.connect("activate", lambda b: self.on_group_rename(b))
            box.append(lbl)
            box.append(entry)
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self.sw_mute = Adw.SwitchRow(title=_("Mute This Chat"))
        self.sw_mute.set_subtitle(_("Messages arrive without a notification"))
        self.sw_mute.set_active(self.app_window.gsettings_mgr.is_conversation_muted(
            self._conversation_id()))
        self.sw_mute.connect("notify::active", self._on_mute_toggled)
        grp_mute = Adw.PreferencesGroup()
        grp_mute.add(self.sw_mute)
        box.append(grp_mute)

        lbl_part = Gtk.Label(label=_("Participants"), xalign=0, css_classes=["heading"])
        box.append(lbl_part)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_max_content_height(350)
        scrolled.set_propagate_natural_height(True)

        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        block_buttons = {}
        dialing_ok = self.app_window.ofono.dialing_available() if self.app_window.ofono else True

        for rec in self.recipients:
            hbox = Gtk.Box(spacing=12, css_classes=["card"])
            hbox.set_margin_start(8)
            hbox.set_margin_end(8)
            hbox.set_margin_top(8)
            hbox.set_margin_bottom(8)

            name = self.app_window.eds.get_display_name(rec)
            if not name:
                if not any(c.isalpha() for c in rec):
                    name = _("Unknown")
                else:
                    name = rec
            elif name == "Unknown":
                name = _("Unknown")

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

            lbl_name = Gtk.Label(label=name, xalign=0, css_classes=["body", "emphasized"])
            lbl_name.set_ellipsize(Pango.EllipsizeMode.END)
            lbl_name.set_max_width_chars(15)
            vbox.append(lbl_name)

            lbl_num = Gtk.Label(label=rec, xalign=0, css_classes=["caption", "dim-label"])
            lbl_num.set_ellipsize(Pango.EllipsizeMode.END)
            vbox.append(lbl_num)

            hbox.append(vbox)
            hbox.append(Gtk.Box(hexpand=True))

            actions_box = Gtk.Box(spacing=12)

            is_saved = self.app_window.eds.get_contact_name(rec) is not None
            icon_edit = "document-edit-symbolic" if is_saved else "contact-new-symbolic"
            b_edit = Gtk.Button(icon_name=icon_edit, css_classes=["circular", "suggested-action"])
            b_edit.set_valign(Gtk.Align.CENTER)

            if not self.app_window.eds.is_ready:
                b_edit.set_sensitive(False)

            actions_box.append(b_edit)

            def _handle_edit_click(n=rec):
                self.btn_menu.set_active(False)
                GLib.timeout_add(50, lambda: self.app_window.present_edit_contact(number_preset=n) or False)
            b_edit.connect("clicked", lambda b, n=rec: _handle_edit_click(n))

            can_call = not any(c.isalpha() for c in rec)
            b_call = Gtk.Button(icon_name="call-start-symbolic", css_classes=["circular", "call-btn"])
            b_call.set_valign(Gtk.Align.CENTER)
            b_call.set_sensitive(can_call and dialing_ok)
            actions_box.append(b_call)
            if can_call:
                self._recipient_call_btns.append(b_call)
                b_call.connect("clicked", lambda b, n=rec: GLib.idle_add(lambda: self.app_window.start_call(n) or False))

            b_blk = Gtk.Button(icon_name="action-unavailable-symbolic", css_classes=["circular", "destructive-action"])
            b_blk.set_valign(Gtk.Align.CENTER)
            b_blk.set_sensitive(False)

            actions_box.append(b_blk)
            block_buttons[rec] = b_blk

            def _toggle_block(btn, number):
                self.btn_menu.set_active(False)

                def _do_toggle():
                    if not self.db.is_blocked(number):
                        return False
                    blocked = self.db.get_blocked_numbers()
                    norm_rec = normalize_number(number)
                    for bid, bnum, _ignored in blocked:
                        if normalize_number(bnum) == norm_rec:
                            self.app_window.daemon.remove_blocked_number(bid)
                            break
                    return True

                def _after_toggle(did_unblock, target_btn=btn):
                    if did_unblock:
                        target_btn.set_icon_name("action-unavailable-symbolic")
                        self.app_window.notify_success(_("Unblocked"))
                        return
                    self.app_window.present_blocklist_editor(number_preset=number)

                run_in_background(_do_toggle, on_complete=_after_toggle)

            b_blk.connect("clicked", lambda b, n=rec: _toggle_block(b, n))

            hbox.append(actions_box)
            list_box.append(hbox)

        def _fetch_block_states():
            return {num: self.db.is_blocked(num) for num in block_buttons}

        def _apply_block_states(states):
            for num, btn in block_buttons.items():
                blocked_state = (states or {}).get(num, False)
                btn.set_icon_name("changes-allow-symbolic" if blocked_state else "action-unavailable-symbolic")
                btn.set_sensitive(self.app_window.eds.is_ready)

        run_in_background(_fetch_block_states, on_complete=_apply_block_states)

        scrolled.set_child(list_box)
        box.append(scrolled)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        b_del_all = Gtk.Button(label=_("Delete Conversation"), css_classes=["destructive-action"])

        def _handle_del_all(btn):
            self.btn_menu.set_active(False)
            GLib.timeout_add(50, lambda: self.on_delete_conversation(btn) or False)
        b_del_all.connect("clicked", lambda b: GLib.idle_add(lambda: _handle_del_all(b) or False))
        box.append(b_del_all)

        self.details_revealer.set_child(box)

    def on_forward_message(self, item):
        """Handle forwarding a message."""
        body = item.body
        attachments = item.attachments if (item.attachments) else []

        sender_display = item.sender
        if sender_display == "Me":
            sender_display = _("Me")
        elif sender_display == "Unknown":
            sender_display = _("Unknown")
        else:
            name = self.app_window.eds.get_display_name(sender_display)
            if name:
                sender_display = name
                if sender_display == "Unknown":
                    sender_display = _("Unknown")

        forward_text = _("Forwarded from {sender}:\n{body}").format(sender=sender_display, body=body) if body else _("Forwarded from {sender}").format(sender=sender_display)

        def _on_pick(target):
            self.messages_view.open_chat(target, prefill_text=forward_text, prefill_attachments=attachments)

        picker = ContactPicker(
            self.app_window.eds,
            self.app_window,
            _on_pick,
            title=_("Forward To"),
            action_label=_("Forward"),
            allow_custom_number=True,
            return_contact_uid=False
        )
        picker.present(self.app_window)

    def on_delete_message(self, item_id):
        """Handle individual message deletion."""
        def on_confirm_delete():
            def task():
                details = self.db.get_message_details(item_id)
                if details and details[4]:
                    for path in json.loads(details[4]):
                        if os.path.exists(path):
                            try:
                                os.remove(path)
                            except Exception as e:
                                logger.warning(f"[Chat] Attachment delete failed: {e}")

                self.app_window.daemon.delete_message(item_id)

            run_in_background(task)

        self.app_window.confirm_action(_("Delete Message"), _("Are you sure you want to delete this message?"), on_confirm_delete)

    def append_prefill(self, text, attachments=None):
        """Merge forwarded content into the open composer."""
        if text:
            start, end = self.msg_buffer.get_bounds()
            existing = self.msg_buffer.get_text(start, end, True)
            self.msg_buffer.set_text(f"{existing}\n{text}" if existing.strip() else text)

        if attachments:
            for p in attachments:
                if p not in self.attachments:
                    self.attachments.append(p)
            self.refresh_attachment_ui()

    def _on_messages_updated(self, _db, chat_id, change):
        """React to database changes relevant to this conversation."""
        if chat_id and chat_id != self.db_number:
            return
        if self.is_loading:
            return

        if change == "insert":
            self._append_new_messages()
        elif change == "status":
            self._sync_message_statuses()
        elif change == "draft":
            return
        else:
            self._reload_chat()

    def _newest_item_id(self):
        """Return the id of the newest real message currently in the store."""
        for i in range(self.store.get_n_items()):
            item = self.store.get_item(i)
            if item.id:
                return item.id
        return 0

    def _append_new_messages(self):
        """Fetch rows newer than the current newest item and prepend them."""
        newest_id = self._newest_item_id()

        def fetch():
            return self.db.get_chat_messages(self.number, limit=20, offset=0)

        def done(rows):
            existing_ids = {self.store.get_item(i).id for i in range(self.store.get_n_items())}
            existing_ids.discard(0)
            fresh = [m for m in (rows or []) if m[0] > newest_id and m[0] not in existing_ids and m[4] != 'draft']
            if not fresh:
                return

            for i in reversed(range(self.store.get_n_items())):
                item = self.store.get_item(i)
                if item.id == 0 and not item.is_divider:
                    self.store.remove(i)

            items = []
            for m in fresh:
                subject = m[5] if len(m) > 5 else None
                att = m[6] if len(m) > 6 else []
                sender_val = m[7] if len(m) > 7 else "Unknown"
                sched_ts = m[8] if len(m) > 8 else None
                items.append(MessageItem(m[0], m[1], m[2], m[3], m[4], subject=subject, attachments=att, sender=sender_val, scheduled_timestamp=sched_ts))

            self.store.splice(0, 0, items)

            if self.v_adj.get_value() <= 20 and self.app_window.is_active():
                self.scroll_to_bottom()
                self._mark_read_debounced()

        run_in_background(fetch, on_complete=done)

    def _sync_message_statuses(self):
        """Refresh the status of recent items in place from the database."""
        def fetch():
            return self.db.get_chat_messages(self.number, limit=30, offset=0)

        def done(rows):
            by_id = {m[0]: m[4] for m in rows or []}
            was_at_bottom = self.v_adj.get_value() <= 20
            changed = False
            for i in range(self.store.get_n_items()):
                item = self.store.get_item(i)
                if not item.id or item.id not in by_id:
                    continue
                new_status = by_id[item.id]
                if item.status == new_status:
                    continue
                replacement = MessageItem(item.id, item.direction, item.body, item.timestamp, new_status,
                                          subject=item.subject, attachments=item.attachments,
                                          sender=item.sender, scheduled_timestamp=item.scheduled_timestamp)
                self.store.splice(i, 1, [replacement])
                changed = True

            if changed and was_at_bottom:
                self.scroll_to_bottom()

        run_in_background(fetch, on_complete=done)

    def on_retry_message(self, item):
        """Resend a failed message."""
        self.app_window.daemon.retry_message(item.id)

    def _reload_chat(self):
        """Reload the chat conversation."""
        if (self._refresh_timer is not None) and self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
        self._refresh_timer = GLib.timeout_add(200, self._do_reload_chat)

    def _do_reload_chat(self):
        self._refresh_timer = None
        self.store.remove_all()
        self.content_stack.set_visible_child_name("loading")
        run_in_background(self._initial_load)
        return False

    def update_contact_details(self, name):
        """Update the page title and internal contact name."""
        if not name:
            return

        display_name = name
        if display_name == self.number:
            display_name = _("Unknown")
        elif display_name == "Unknown":
            display_name = _("Unknown")

        if display_name != self.contact_name:
            self.contact_name = display_name
            self.title_widget.set_title(display_name)

    def on_group_rename(self, entry):
        """Handle group renaming."""
        new_name = entry.get_text().strip()
        if not new_name:
            return

        def done(_result):
            self.contact_name = new_name
            self.title_widget.set_title(new_name)

        run_in_background(self.app_window.daemon.set_group_name, self.recipients, new_name, on_complete=done)

    def _on_paste_clipboard(self, text_view):
        """Divert file and image pastes into the attachment pipeline."""
        if self.media_controller.ingest_clipboard():
            text_view.stop_emission_by_name("paste-clipboard")

    def _remaining_attachment_budget(self):
        """Return how many bytes of MMS attachment budget are still available."""
        budget = max_attachment_size(self.app_window.gsettings_mgr)
        used = sum(os.path.getsize(p) for p in self.attachments if os.path.exists(p))
        return budget - used

    def on_attachment_captured(self, path):
        """Hand a captured file to the daemon to store and fit the budget."""
        if not path or path in self.attachments:
            return

        remaining = self._remaining_attachment_budget()
        mime, _encoding = mimetypes.guess_type(path)
        try:
            oversized = os.path.getsize(path) > remaining
        except OSError as e:
            logger.warning(f"[Chat] Attachment size check failed: {e}")
            oversized = False

        if oversized and mime and mime.startswith("image/"):
            self.app_window.notify_loading(_("Compressing image..."))
        elif oversized and mime and mime.startswith("video/"):
            self.app_window.notify_loading(_("Compressing video..."))

        run_in_background(self.app_window.daemon.prepare_attachment, path, remaining,
                          on_complete=lambda reply: self._on_attachment_prepared(reply, path))

    def _on_attachment_prepared(self, reply, source_path):
        """Add the stored attachment, or say why it could not be."""
        self.app_window.hide_loading()
        if reply is None:
            self.app_window.notify_error(_("Failed to prepare attachment."))
            return
        stored_path, code = reply
        if stored_path:
            self._append_attachment(stored_path)
            if "/tmp/" in source_path:
                try:
                    os.remove(source_path)
                except OSError as e:
                    logger.debug(f"[Chat] Temp source removal failed: {e}")
            return
        errors = {
            "image-too-large": _("Image is too large to send"),
            "video-failed": _("Video compression failed"),
            "too-large": _("File is too large to send via MMS"),
        }
        self.app_window.notify_error(errors.get(code, _("Failed to prepare attachment.")))

    def _append_attachment(self, path):
        """Add a processed attachment and refresh the chip area."""
        if path not in self.attachments:
            self.attachments.append(path)
            self.refresh_attachment_ui()

    def refresh_attachment_ui(self):
        """Update attachment UI area."""
        while child := self.att_box.get_first_child():
            self.att_box.remove(child)
        if not self.attachments:
            self.att_scrolled.set_visible(False)
            return
        self.att_scrolled.set_visible(True)
        for path in self.attachments:
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            chip.add_css_class("att-chip")
            lbl = Gtk.Label(label=os.path.basename(path))
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(20)
            btn_close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"])
            btn_close.connect("clicked", lambda b, p=path: GLib.idle_add(lambda: self.remove_attachment_by_path(p) or False))
            chip.append(lbl)
            chip.append(btn_close)
            self.att_box.append(chip)

    def remove_attachment_by_path(self, path_to_remove):
        """Remove an attachment from the list."""
        if path_to_remove in self.attachments:
            self.attachments.remove(path_to_remove)
            if "/tmp/" in path_to_remove and os.path.exists(path_to_remove):
                try:
                    os.remove(path_to_remove)
                except Exception as e:
                    logger.warning(f"[Chat] Temp file removal failed: {e}")
            self.refresh_attachment_ui()

    def load_context_for_message(self, msg_id):
        """Load messages around a specific message ID."""
        self.is_jumping = True

        def _fetch_context():
            msgs = self.db.get_chat_messages_around(msg_id, limit_before=50, limit_after=None)
            newer_count = self.db.get_message_offset(msg_id)
            return msgs, newer_count

        def _update_ui(msgs, newer_count):
            self.store.remove_all()

            self.full_history = list(msgs)

            items = []

            target_index = -1

            for m in msgs:
                mid = m[0]

                subject = m[5]
                att = m[6]
                sender_val = m[7] if len(m) > 7 else "Unknown"
                if sender_val == "Unknown":
                    sender_val = _("Unknown")
                elif sender_val == "Me":
                    sender_val = _("Me")

                sched_ts = m[8] if len(m) > 8 else None

                item = MessageItem(mid, m[1], m[2], m[3], m[4], subject=subject, attachments=att, sender=sender_val, scheduled_timestamp=sched_ts)
                items.append(item)

                if mid == msg_id:
                    target_index = len(items) - 1

            items.reverse()
            if target_index >= 0:
                target_index = len(items) - 1 - target_index

            self.store.splice(0, 0, items)

            newer_messages_loaded = 0
            for m in reversed(msgs):
                if m[0] == msg_id:
                    break
                newer_messages_loaded += 1

            self.newest_db_offset = max(0, newer_count - newer_messages_loaded)
            self.oldest_db_offset = self.newest_db_offset + len(items)

            def reveal():
                self.is_loading = False
                self.content_stack.set_visible_child_name("chat")

                def scroll_after_visible():
                    if target_index >= 0:
                        self.list_view.scroll_to(target_index, Gtk.ListScrollFlags.FOCUS | Gtk.ListScrollFlags.SELECT, None)

                    def end_jump():
                        self.is_jumping = False
                        return False

                    GLib.timeout_add(300, end_jump)
                    return False

                GLib.timeout_add(50, scroll_after_visible)
                return False

            GLib.timeout_add(300, reveal)

        def _bg():
            try:
                msgs, newer_count = _fetch_context()
                GLib.idle_add(_update_ui, msgs, newer_count)
            except Exception as e:
                logger.warning(f"[ChatPage] Load context failed: {e}")

        run_in_background(_bg)

    def _open_schedule_picker(self):
        """Open datetime picker for scheduling."""
        buffer = self.msg_view.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, True).strip()
        has_att = len(self.attachments) > 0

        if not text and not has_att:
            self.app_window.notify_error(_("Nothing to schedule"))
            return

        def _on_picked(dt):
            now = GLib.DateTime.new_now_local()
            if dt.compare(now) <= 0:
                self.app_window.notify_error(_("Scheduled time must be in the future"))
                return

            ts_str = dt.format("%Y-%m-%d %H:%M:%S")
            self.on_send(scheduled_timestamp=ts_str)

        DateTimePicker(
            parent=self.app_window,
            title=_("Schedule Message"),
            include_time=True,
            on_confirm=_on_picked
        )

    def on_send(self, *args, scheduled_timestamp=None):
        """Handle send button click or scheduled send."""
        buffer = self.msg_view.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, True).strip()
        has_att = len(self.attachments) > 0
        if not text and not has_att:
            return

        self.btn_send.set_sensitive(False)
        self._draft_saved = True
        pending_attachments = list(self.attachments)
        buffer.set_text("")
        self.attachments = []
        self.refresh_attachment_ui()

        def prepare():
            final_attachments = pending_attachments
            self.app_window.daemon.save_draft(self.number, "", [])
            if scheduled_timestamp:
                if self.is_group or final_attachments:
                    return self.app_window.daemon.schedule_mms(
                        self.number, text, final_attachments, scheduled_timestamp)
                return self.app_window.daemon.schedule_sms(self.number, text, scheduled_timestamp)
            if self.is_group or final_attachments:
                return self.app_window.daemon.send_mms(self.number, text, final_attachments)
            return self.app_window.daemon.send_tracked_sms(self.number, text)

        def done(sent):
            self.btn_send.set_sensitive(True)
            if not sent:
                self.app_window.notify_error(_("Failed to send"))
                return
            if scheduled_timestamp:
                self.app_window.notify_success(_("Message scheduled for {time}").format(time=scheduled_timestamp))
            self.scroll_to_bottom()

        run_in_background(prepare, on_complete=done)

    def on_scroll_changed(self, adj):
        """Handle scroll position change to mark read or fetch older."""
        if self.is_loading or self.is_jumping:
            return

        is_near_older = (adj.get_value() + adj.get_page_size()) >= (adj.get_upper() - 800)

        if is_near_older and not self.all_messages_loaded:
            if not self.is_fetching_older:
                self._start_load_thread()

        self.check_read_status()

    def check_read_status(self):
        """Check if bottom is reached and mark read."""
        adj = self.v_adj
        is_at_bottom = adj.get_value() <= 20
        if is_at_bottom and self.app_window.is_active():
            self._mark_read_debounced()

    def _mark_read_debounced(self):
        """Trigger mark read with debounce."""
        if (self._read_timer is not None) and self._read_timer:
            GLib.source_remove(self._read_timer)
        self._read_timer = GLib.timeout_add(200, self._do_mark_read)

    def _do_mark_read(self):
        """Perform mark read logic."""
        self.app_window.daemon.mark_thread_read(self.db_number)
        self.messages_view.check_and_clear_notification(self.db_number)

        self.has_active_divider = False
        self._read_timer = None
        return False

    def on_window_focus_changed(self, window, param):
        """Handle window focus change."""
        self._report_active_chat()
        if not window.is_active():
            return

        if self._pending_initial_read:
            self._pending_initial_read = False
            self._mark_read_debounced()
        else:
            self.check_read_status()

    def on_delete_conversation(self, btn):
        """Handle conversation deletion."""
        def do_full_delete():
            target_id = self.number
            self.messages_view.close_active_chat()

            def task():
                all_msgs = self.db.get_chat_messages(target_id, limit=100000)

                files_deleted = 0
                for m in all_msgs:
                    atts = m[6] if len(m) > 6 else []
                    for path in atts:
                        if os.path.exists(path):
                            try:
                                os.remove(path)
                                files_deleted += 1
                            except Exception as e:
                                logger.error(f"Failed to delete {path}: {e}")

                logger.info(f"[Delete] Wiped {len(all_msgs)} messages and {files_deleted} files for ID: {target_id}")

                self.app_window.daemon.delete_conversation(target_id)

            run_in_background(task)

        title = _("Delete Group") if self.is_group else _("Delete Conversation")
        body = _("This will permanently delete the conversation history and all saved media files.")
        self.app_window.confirm_action(title, body, do_full_delete)

    def scroll_to_bottom(self):
        """Scroll to the bottom of the list."""
        def _do():
            n = self.store.get_n_items()
            if n > 0:
                self.list_view.scroll_to(0, Gtk.ListScrollFlags.NONE, None)
            return False
        GLib.idle_add(_do)

    def _report_active_chat(self):
        """Tell the daemon whether this chat is open and focused.

        The daemon mutes notifications only for the reported chat, so
        the report must go both ways: set while this page is on screen
        in a focused window, cleared the moment either stops holding.
        """
        focused = self.get_mapped() and self.app_window and self.app_window.is_active()
        if focused:
            self.app_window.ofono.set_active_chat(self.recipients if self.is_group else self.number)
        else:
            self.app_window.ofono.set_active_chat(None)

    def on_map(self, widget):
        """Reconnect listeners dropped on unmap and catch up missed changes."""
        self._draft_saved = False
        self._report_active_chat()

        if self.app_window and self.focus_handler_id is None:
            self.focus_handler_id = self.app_window.connect("notify::is-active", lambda obj, pspec: self.on_window_focus_changed(obj, pspec))

        if self.db_signals:
            return

        sig_id = self.db.connect('messages-updated', self._on_messages_updated)
        self.db_signals.append((self.db, sig_id))

        if not self.is_loading:
            self._append_new_messages()
            self._sync_message_statuses()

    def on_unmap(self, widget):
        """Cleanup on widget unmap."""
        self._report_active_chat()
        if not self._draft_saved:
            self._save_draft()

        for timer_attr in ("_read_timer", "_refresh_timer", "search_timer"):
            timer_id = getattr(self, timer_attr)
            if timer_id:
                GLib.source_remove(timer_id)
                setattr(self, timer_attr, None)

        if self.app_window and (self.focus_handler_id is not None):
            if self.app_window.handler_is_connected(self.focus_handler_id):
                try:
                    self.app_window.disconnect(self.focus_handler_id)
                except Exception as e:
                    logger.debug(f"[ChatPage] Disconnect focus handler warning: {e}")
            self.focus_handler_id = None

        for obj, sig_id in self.db_signals:
            if obj.handler_is_connected(sig_id):
                obj.disconnect(sig_id)
        self.db_signals.clear()

        for obj, sig_id in self.eds_signals:
            if obj.handler_is_connected(sig_id):
                obj.disconnect(sig_id)
        self.eds_signals.clear()

    def _save_draft(self):
        """Replace any stored draft with the current composer content."""
        if not self.msg_view.get_editable():
            return

        buffer = self.msg_view.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, True).strip()
        draft_attachments = list(self.attachments)

        def persist():
            self.app_window.daemon.save_draft(self.number, text, draft_attachments)
            return bool(text or draft_attachments)

        def done(saved):
            if saved:
                logger.info("[ChatPage] Draft saved")
            if self.messages_view:
                self.messages_view.refresh_list()

        run_in_background(persist, on_complete=done)
