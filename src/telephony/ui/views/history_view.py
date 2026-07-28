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

from datetime import datetime, timedelta
from gi.repository import Gtk, Adw, Gio, GLib, Pango
from loguru import logger
from gettext import gettext as _

from ...backend.utils.phone_utils import normalize_number
from ...backend.utils.locale_utils import get_date_format, get_time_format
from ...backend.utils.model_utils import CallItem
from ..widgets.common_widget import DataLoader
from ..widgets.history_rows_widget import HistoryRowFactory


class HistoryView(Adw.Bin):
    """View displaying call history with filtering options."""

    def __init__(self, db_manager, app_window):
        self._refresh_timer = None
        self.search_timer = None
        """Initialize the HistoryView."""
        super().__init__()
        self.db = db_manager
        self.app_window = app_window
        self.active_type = "all"
        self.active_bucket = "any"
        self.active_date_bucket = "any"
        self.load_token = 0
        self.page_offset = 0
        self.page_limit = 50
        self.is_fetching = False

        self.is_search_mode = False
        self.search_query = ""

        self.row_factory = HistoryRowFactory(app_window)

        self.db.connect('history-updated', lambda *args: GLib.idle_add(lambda: self.refresh_data()))
        self.db.connect('blocklist-updated', lambda *args: GLib.idle_add(lambda: self.refresh_data()))
        if self.app_window.eds:
            self.app_window.eds.connect('contacts-loaded', lambda *args: GLib.idle_add(lambda: self.refresh_data()))

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.header_stack = Gtk.Stack()
        self.header_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.filter_box.set_halign(Gtk.Align.CENTER)
        self.filter_box.set_margin_top(12)
        self.filter_box.set_margin_bottom(12)
        self.setup_filter_ui()
        self.header_stack.add_named(self.filter_box, "filter")

        self.search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.search_box.set_margin_top(12)
        self.search_box.set_margin_bottom(12)
        self.search_box.set_margin_start(12)
        self.search_box.set_margin_end(12)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_box.append(self.search_entry)

        btn_cancel_search = Gtk.Button(label=_("Cancel"))
        btn_cancel_search.connect("clicked", lambda b: GLib.idle_add(lambda: self.toggle_search_mode(False) or False))
        self.search_box.append(btn_cancel_search)

        self.header_stack.add_named(self.search_box, "search")

        main_box.append(self.header_stack)
        main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self.model = Gio.ListStore(item_type=CallItem)
        self.selection_model = Gtk.SingleSelection(model=self.model)
        self.selection_model.set_autoselect(False)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.row_factory.setup)
        factory.connect("bind", self._on_bind_row)
        factory.connect("unbind", self.row_factory.unbind)
        factory.connect("teardown", self.row_factory.teardown)

        self.list_view = Gtk.ListView(model=self.selection_model, factory=factory)

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

        main_box.append(self.scrolled)
        main_box.append(self.spinner)

        self.set_child(main_box)
        self.refresh_data()

    def setup_filter_ui(self):
        """Setup the filter buttons and popovers."""
        lbl_all = Gtk.Label(label=_("All"))
        lbl_all.set_ellipsize(Pango.EllipsizeMode.END)
        lbl_all.set_max_width_chars(10)
        self.btn_all = Gtk.Button()
        self.btn_all.set_child(lbl_all)
        self.btn_all.add_css_class("all-button-history")
        self.btn_all.add_css_class("gray-btn")
        self.btn_all.set_size_request(-1, 34)
        self.btn_all.connect("clicked", lambda b, t="all": GLib.idle_add(lambda: self.on_type_click(b, t) or False))
        self.filter_box.append(self.btn_all)

        self.btn_in = Gtk.Button(icon_name="call-incoming-symbolic")
        self.btn_in.add_css_class("circular")
        self.btn_in.add_css_class("gray-btn")
        self.btn_in.set_size_request(34, 34)
        self.btn_in.connect("clicked", lambda b, t="incoming": GLib.idle_add(lambda: self.on_type_click(b, t) or False))
        self.filter_box.append(self.btn_in)

        self.btn_out = Gtk.Button(icon_name="call-outgoing-symbolic")
        self.btn_out.add_css_class("circular")
        self.btn_out.add_css_class("gray-btn")
        self.btn_out.set_size_request(34, 34)
        self.btn_out.connect("clicked", lambda b, t="outgoing": GLib.idle_add(lambda: self.on_type_click(b, t) or False))
        self.filter_box.append(self.btn_out)

        self.btn_dur = Gtk.MenuButton(icon_name="preferences-system-time-symbolic")
        self.btn_dur.add_css_class("circular")
        self.btn_dur.add_css_class("gray-btn")
        self.btn_dur.set_size_request(34, 34)
        self.setup_dur_popover()
        self.filter_box.append(self.btn_dur)

        self.btn_date = Gtk.MenuButton(icon_name="x-office-calendar-symbolic")
        self.btn_date.add_css_class("circular")
        self.btn_date.add_css_class("gray-btn")
        self.btn_date.set_size_request(34, 34)
        self.setup_date_popover()
        self.filter_box.append(self.btn_date)

        self.btn_search = Gtk.Button(icon_name="system-search-symbolic")
        self.btn_search.add_css_class("circular")
        self.btn_search.add_css_class("gray-btn")
        self.btn_search.set_size_request(34, 34)
        self.btn_search.connect("clicked", lambda b: GLib.idle_add(lambda: self.toggle_search_mode(True) or False))
        self.filter_box.append(self.btn_search)

        self._update_visuals()

    def setup_dur_popover(self):
        """Setup duration filter popover."""
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        pop_box.set_margin_top(12)
        pop_box.set_margin_bottom(12)
        pop_box.set_margin_start(12)
        pop_box.set_margin_end(12)
        pop_box.set_size_request(200, -1)
        self.duration_buttons = {}
        buckets = [(_("Any duration"), "any"), (_("Unanswered"), "0"), (_("1s - 60s"), "60s"), (_("1-3 mins"), "3m"), (_("3-10 mins"), "10m"), (_("> 15 mins"), "15m+")]
        for lbl, val in buckets:
            b = Gtk.Button(label=lbl)
            b.set_halign(Gtk.Align.FILL)
            b.connect("clicked", lambda btn, v=val: GLib.idle_add(lambda: self.on_dur_click(btn, v) or False))
            pop_box.append(b)
            self.duration_buttons[val] = b
        self.dur_popover = Gtk.Popover()
        self.dur_popover.set_child(pop_box)
        self.btn_dur.set_popover(self.dur_popover)

    def setup_date_popover(self):
        """Setup date filter popover."""
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        pop_box.set_margin_top(12)
        pop_box.set_margin_bottom(12)
        pop_box.set_margin_start(12)
        pop_box.set_margin_end(12)
        pop_box.set_size_request(200, -1)
        self.date_buttons = {}
        opts = [(_("Any date"), "any"), (_("Today"), "today"), (_("Last 3 days"), "3days"), (_("Last 14 days"), "14days"), (_("Last 30 days"), "30days"), (_("Last 60 days"), "60days")]
        for lbl, val in opts:
            b = Gtk.Button(label=lbl)
            b.set_halign(Gtk.Align.FILL)
            b.connect("clicked", lambda btn, v=val: GLib.idle_add(lambda: self.on_date_click(btn, v) or False))
            pop_box.append(b)
            self.date_buttons[val] = b
        self.date_popover = Gtk.Popover()
        self.date_popover.set_child(pop_box)
        self.btn_date.set_popover(self.date_popover)

    def on_type_click(self, btn, val):
        """Handle call type filter change."""
        self.active_type = val
        self._update_visuals()
        self.refresh_data()

    def on_dur_click(self, btn, val):
        """Handle duration filter change."""
        self.active_bucket = val
        GLib.idle_add(lambda: self.dur_popover.popdown() or False)
        self._update_visuals()
        self.refresh_data()

    def on_date_click(self, btn, val):
        """Handle date filter change."""
        self.active_date_bucket = val
        GLib.idle_add(lambda: self.date_popover.popdown() or False)
        self._update_visuals()
        self.refresh_data()

    def _update_visuals(self):
        """Update filter button styles."""
        def set_style_main(btn, active):
            if active:
                btn.add_css_class("suggested-action")
                btn.remove_css_class("gray-btn")
            else:
                btn.remove_css_class("suggested-action")
                btn.add_css_class("gray-btn")

        def set_style_popover(btn, active):
            if active:
                btn.add_css_class("suggested-action")
            else:
                btn.remove_css_class("suggested-action")

        set_style_main(self.btn_all, self.active_type == "all")
        set_style_main(self.btn_in, self.active_type == "incoming")
        set_style_main(self.btn_out, self.active_type == "outgoing")
        set_style_main(self.btn_dur, self.active_bucket != "any")
        set_style_main(self.btn_date, self.active_date_bucket != "any")
        for val, btn in self.duration_buttons.items():
            set_style_popover(btn, self.active_bucket == val)
        for val, btn in self.date_buttons.items():
            set_style_popover(btn, self.active_date_bucket == val)

    def on_scroll_changed(self, adj):
        """Handle scroll position change."""
        if self.is_fetching:
            return
        is_at_bottom = (adj.get_value() + adj.get_page_size()) >= (adj.get_upper() - 800)
        if is_at_bottom:
            self._start_fetch()

    def refresh_data(self):
        """Reload the history data."""
        if (self._refresh_timer is not None) and self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
        self._refresh_timer = GLib.timeout_add(200, self._do_refresh_data)

    def _do_refresh_data(self):
        self._refresh_timer = None
        self.load_token += 1
        self.page_offset = 0
        self._start_fetch()
        return False

    def _start_fetch(self):
        """Start fetching data in background."""
        current_token = self.load_token
        self.is_fetching = True
        self.spinner.set_visible(True)
        DataLoader.load_data(
            fetch_func=lambda: self._fetch_and_process(self.page_offset),
            model_add_func=self.add_chunk,
            model=self.model,
            check_token_func=lambda: self.load_token == current_token,
            clear_on_first_chunk=(self.page_offset == 0)
        )

    def toggle_search_mode(self, enabled):
        """Enable or disable search mode."""
        self.is_search_mode = enabled
        if enabled:
            self.header_stack.set_visible_child_name("search")
            self.search_entry.grab_focus()
        else:
            self.header_stack.set_visible_child_name("filter")
            self.search_entry.set_text("")
            self.search_query = ""
            self.refresh_data()

    def on_search_changed(self, entry):
        """Handle search input changes."""
        text = entry.get_text().strip()
        self.search_query = text
        if (self.search_timer is not None) and self.search_timer:
            GLib.source_remove(self.search_timer)
        self.search_timer = GLib.timeout_add(200, self._trigger_search)

    def _trigger_search(self):
        """Trigger the data refresh for search."""
        self.refresh_data()
        self.search_timer = None
        return False

    def _fetch_and_process(self, offset):
        """Fetch rows from DB and process them."""
        if self.is_search_mode and self.search_query:
            raw_rows = self.db.search_history(self.search_query, limit=self.page_limit + 1, offset=offset)
        else:
            raw_rows = self.db.get_history(direction=self.active_type, limit=self.page_limit + 1, offset=offset)

        contact_map = self.db.get_contacts_lookup_map()
        now = datetime.now()
        today = now.date()
        limit_date = None

        if not self.is_search_mode:
            if self.active_date_bucket == "today":
                limit_date = today
            elif self.active_date_bucket == "3days":
                limit_date = today - timedelta(days=3)
            elif self.active_date_bucket == "14days":
                limit_date = today - timedelta(days=14)
            elif self.active_date_bucket == "30days":
                limit_date = today - timedelta(days=30)
            elif self.active_date_bucket == "60days":
                limit_date = today - timedelta(days=60)

        processed = []
        has_more = False

        if len(raw_rows) > self.page_limit:
            has_more = True
            raw_rows = raw_rows[:self.page_limit]

        for r in raw_rows:
            call_id, number, _ignored, direction, duration, ts_str = r

            if not self.is_search_mode and self.active_bucket != 'any':
                try:
                    d = int(duration)
                except Exception as e:
                    logger.debug(f"[History] Duration parse error: {e}")
                    d = 0
                b = self.active_bucket
                if b == "0" and d != 0:
                    continue
                elif b == "60s" and not (0 < d <= 60):
                    continue
                elif b == "3m" and not (60 < d <= 180):
                    continue
                elif b == "10m" and not (180 < d <= 600):
                    continue
                elif b == "15m+" and d <= 900:
                    continue

            full_ts_str = str(ts_str)
            display_time_str = str(ts_str)
            try:
                dt = parse_timestamp(ts_str)
                if not self.is_search_mode and self.active_date_bucket != 'any':
                    if self.active_date_bucket == "today":
                        if dt.date() != today:
                            continue
                    else:
                        if dt.date() < limit_date:
                            continue

                date_disp = dt.strftime(get_date_format())
                time_disp = dt.strftime(get_time_format())
                full_ts_str = f"{date_disp} {time_disp}"

                if dt.date() == today:
                    display_time_str = time_disp
                else:
                    display_time_str = date_disp
            except Exception as e:
                logger.debug(f"[History] Timestamp parse error: {e}")
                if not self.is_search_mode and self.active_date_bucket != 'any':
                    continue

            norm_num = normalize_number(number)

            raw_contact = contact_map.get(norm_num)
            if raw_contact:
                if isinstance(raw_contact, list) and raw_contact:
                    best = sorted(raw_contact, key=lambda x: x[0])[0]
                    real_name = best[1]
                elif isinstance(raw_contact, str):
                    real_name = raw_contact
                else:
                    real_name = number
            else:
                real_name = number

            if not real_name or real_name == "Unknown":
                real_name = _("Unknown")

            is_saved = (norm_num in contact_map)

            processed.append({
                "id": call_id,
                "number": number,
                "name": real_name,
                "direction": direction,
                "duration": duration,
                "full_ts": full_ts_str,
                "display_time": display_time_str,
                "is_saved": is_saved
            })

        self.last_fetch_has_more = has_more

        return processed

    def add_chunk(self, model, items):
        """Add processed items to the model."""
        self.is_fetching = not self.last_fetch_has_more

        real_items_count = 0
        new_call_items = []

        for d in items:
            real_items_count += 1
            new_call_items.append(CallItem(
                d["id"],
                d["number"],
                d["name"],
                d["direction"],
                d["duration"],
                d["full_ts"],
                d["display_time"],
                d["is_saved"]
            ))

        if new_call_items:
            model.splice(model.get_n_items(), 0, new_call_items)

        self.page_offset += real_items_count

        self.spinner.set_visible(False)

        return False

    def cleanup(self):
        """Cleanup resources before destruction."""
        self.load_token += 1
        if (self.search_timer is not None) and self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

        if self.model:
            self.model.remove_all()

    def _on_bind_row(self, factory, list_item):
        """Bind row items to widgets."""
        self.row_factory.bind(factory, list_item)
