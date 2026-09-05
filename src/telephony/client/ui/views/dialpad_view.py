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

from gi.repository import Gtk, Adw, Gdk, GLib
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _

from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.utils.phone_utils import normalize_number

BUTTONS_LAYOUT = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#']

DIALPAD_COLUMNS = 3
DIALPAD_ROWS = 6
DIALPAD_MAX_WIDTH = 560
DIAL_MATCH_LIMIT = 5
DIAL_MATCH_QUERY_LIMIT = 20
DIAL_MATCH_MIN_CHARS = 3
DIAL_MATCH_DEBOUNCE_MS = 200
DIALPAD_SIDE_MARGIN = 12
DIALPAD_COLUMN_SPACING = 12
DIALPAD_ROW_SPACING = 10
DIALPAD_KEY_RATIO = 100 / 44


class DialpadKeys(Gtk.Widget):
    __gtype_name__ = "DialpadKeys"

    def __init__(self, grid):
        super().__init__()
        self.grid = grid
        grid.set_parent(self)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL and for_size > 0:
            gaps = (DIALPAD_COLUMNS - 1) * DIALPAD_COLUMN_SPACING
            key_width = (for_size - gaps) / DIALPAD_COLUMNS
            height = int(DIALPAD_ROWS * key_width / DIALPAD_KEY_RATIO +
                         (DIALPAD_ROWS - 1) * DIALPAD_ROW_SPACING)
            return (height, height, -1, -1)
        return self.grid.measure(orientation, for_size)

    def do_size_allocate(self, width, height, baseline):
        self.grid.allocate(width, height, baseline, None)

    def do_dispose(self):
        if self.grid is not None:
            self.grid.unparent()
            self.grid = None


class DialpadView(Adw.Bin):
    """View for dialing phone numbers."""

    def __init__(self, app_window):
        self._lookup_timer = None
        self._lookup_generation = 0
        """Initialize the DialpadView."""
        super().__init__()
        self.app_window = app_window

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_valign(Gtk.Align.FILL)
        box.set_halign(Gtk.Align.FILL)
        box.set_margin_bottom(4)
        box.set_margin_start(DIALPAD_SIDE_MARGIN)
        box.set_margin_end(DIALPAD_SIDE_MARGIN)

        number_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        number_section.set_valign(Gtk.Align.START)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text(_("Enter Number"))
        self.entry.set_alignment(0.5)
        self.entry.add_css_class("title-1")
        self.entry.add_css_class("dial-number-entry")
        self.entry.set_can_focus(True)
        self.entry.set_editable(True)
        self.entry.set_property("im-module", "none")

        self.match_list = Gtk.ListBox()
        self.match_list.add_css_class("boxed-list")
        self.match_list.add_css_class("dial-contact-list")
        self.match_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.match_list.set_valign(Gtk.Align.CENTER)
        self.match_list.set_visible(False)

        match_clamp = Adw.Clamp(maximum_size=DIALPAD_MAX_WIDTH)
        match_clamp.set_hexpand(True)
        match_clamp.set_vexpand(True)
        match_clamp.set_child(self.match_list)

        match_scroller = Gtk.ScrolledWindow()
        match_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        match_scroller.set_vexpand(True)
        match_scroller.set_child(match_clamp)

        self.entry.connect("notify::text", self._schedule_contact_lookup)

        number_section.append(self.entry)
        box.append(number_section)

        box.append(match_scroller)

        grid = Gtk.Grid(row_spacing=DIALPAD_ROW_SPACING,
                        column_spacing=DIALPAD_COLUMN_SPACING)
        grid.set_row_homogeneous(True)
        grid.set_column_homogeneous(True)

        keys = DialpadKeys(grid)
        keys.set_hexpand(True)
        keys.set_valign(Gtk.Align.END)
        box.append(keys)

        for i, digit in enumerate(BUTTONS_LAYOUT):
            btn = Gtk.Button()
            btn.set_size_request(74, 44)
            btn.set_hexpand(True)
            btn.set_vexpand(True)
            btn.add_css_class("pill")
            btn.add_css_class("compact-btn")
            l1 = Gtk.Label()
            l1.set_markup(f"<b>{digit}</b>")
            btn.set_child(l1)
            btn.connect("clicked", lambda b, d=digit: GLib.idle_add(lambda: self.on_digit_clicked(d) or False))
            grid.attach(btn, i % 3, i // 3, 1, 1)

        paste_btn = Gtk.Button(icon_name="edit-paste-symbolic")
        paste_btn.add_css_class("pill")
        paste_btn.add_css_class("compact-btn")
        paste_btn.add_css_class("dialpad-paste")
        paste_btn.set_size_request(74, 44)
        paste_btn.set_hexpand(True)
        paste_btn.set_vexpand(True)
        paste_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_paste_clicked(b) or False))
        grid.attach(paste_btn, 0, 4, 1, 1)

        plus_btn = Gtk.Button(label="+")
        plus_btn.add_css_class("pill")
        plus_btn.add_css_class("compact-btn")
        plus_btn.set_size_request(74, 44)
        plus_btn.set_hexpand(True)
        plus_btn.set_vexpand(True)
        plus_btn.set_opacity(0.8)
        l_plus = Gtk.Label()
        l_plus.set_markup("<b>+</b>")
        plus_btn.set_child(l_plus)
        plus_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_digit_clicked("+") or False))
        grid.attach(plus_btn, 1, 4, 1, 1)

        backspace_btn = Gtk.Button(icon_name="edit-clear-symbolic")
        backspace_btn.add_css_class("pill")
        backspace_btn.add_css_class("compact-btn")
        backspace_btn.add_css_class("dialpad-delete")
        backspace_btn.set_size_request(74, 44)
        backspace_btn.set_hexpand(True)
        backspace_btn.set_vexpand(True)
        backspace_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_backspace(b) or False))
        grid.attach(backspace_btn, 2, 4, 1, 1)

        self.anon_btn = Gtk.Button()
        anon_btn = self.anon_btn
        anon_btn.add_css_class("pill")
        anon_btn.add_css_class("btn-anon-green")
        anon_btn.add_css_class("compact-btn")
        anon_btn.set_size_request(74, 44)
        anon_btn.set_hexpand(True)
        anon_btn.set_vexpand(True)
        anon_content = Gtk.Box(spacing=4)
        anon_content.set_halign(Gtk.Align.CENTER)
        anon_content.append(Gtk.Image.new_from_icon_name("call-start-symbolic"))
        anon_content.append(Gtk.Image.new_from_icon_name("user-not-tracked-symbolic"))
        anon_btn.set_child(anon_content)
        anon_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_anon_call_clicked(b) or False))
        grid.attach(anon_btn, 0, 5, 1, 1)

        clear_all_btn = Gtk.Button(icon_name="user-trash-symbolic")
        clear_all_btn.add_css_class("pill")
        clear_all_btn.add_css_class("compact-btn")
        clear_all_btn.add_css_class("destructive-action")
        clear_all_btn.set_size_request(74, 44)
        clear_all_btn.set_hexpand(True)
        clear_all_btn.set_vexpand(True)
        clear_all_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.entry.set_text("") or False))
        grid.attach(clear_all_btn, 1, 5, 1, 1)

        self.norm_btn = Gtk.Button(icon_name="call-start-symbolic")
        norm_btn = self.norm_btn
        norm_btn.add_css_class("pill")
        norm_btn.add_css_class("call-btn")
        norm_btn.add_css_class("compact-btn")
        norm_btn.set_size_request(74, 44)
        norm_btn.set_hexpand(True)
        norm_btn.set_vexpand(True)
        norm_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_call_clicked(b) or False))
        grid.attach(norm_btn, 2, 5, 1, 1)

        clamp = Adw.Clamp(maximum_size=DIALPAD_MAX_WIDTH)
        clamp.set_vexpand(True)
        clamp.set_child(box)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(clamp)
        scrolled.set_vexpand(True)
        self.set_child(scrolled)

    def _schedule_contact_lookup(self, _entry, _param):
        """Debounce contact matching after the dialed number changes."""
        if (self._lookup_timer is not None) and self._lookup_timer:
            GLib.source_remove(self._lookup_timer)
        self._lookup_generation += 1
        generation = self._lookup_generation
        self._lookup_timer = GLib.timeout_add(
            DIAL_MATCH_DEBOUNCE_MS, self._start_contact_lookup, generation)

    def set_calling_enabled(self, enabled):
        """Enable or disable the two call buttons."""
        self.anon_btn.set_sensitive(enabled)
        self.norm_btn.set_sensitive(enabled)

    def cleanup(self):
        """Cancel the pending contact lookup timer."""
        self._lookup_generation += 1
        if self._lookup_timer:
            GLib.source_remove(self._lookup_timer)
            self._lookup_timer = None

    def _favorite_for(self, text):
        """Return the speed dial contact a single digit stands for."""
        if len(text) != 1 or not text.isdigit():
            return None
        for entry in self.app_window.gsettings_mgr.get_favorites():
            if str(entry.get("slot")) == text:
                return entry
        return None

    def _clear_match_rows(self):
        """Remove every row currently displayed in the match list."""
        row = self.match_list.get_first_child()
        while row is not None:
            next_row = row.get_next_sibling()
            self.match_list.remove(row)
            row = next_row

    def _show_contact_matches(self, matches, show_empty=False):
        """Render the bounded contact result set as native list rows."""
        self._clear_match_rows()

        if not matches and show_empty:
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(_("No matching contacts"))
            row.set_activatable(False)
            row.add_css_class("dim-label")
            row.add_prefix(Gtk.Image.new_from_icon_name("user-not-tracked-symbolic"))
            self.match_list.append(row)

        for match in matches:
            details = [match["number"]]
            if match["speed_slot"] is not None:
                details.append(
                    _("Speed dial {slot}").format(slot=match["speed_slot"]))

            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title_lines(1)
            row.set_title(match["name"])
            row.set_subtitle(" · ".join(
                part for part in details if part))
            row.set_activatable(True)
            row.set_tooltip_text(match["name"])
            row.match_number = match["number"]
            row.connect("activated", self._on_match_activated)

            if match["favorite"]:
                star = Gtk.Image.new_from_icon_name("starred-symbolic")
                star.add_css_class("accent")
                row.add_suffix(star)
                row.add_css_class("favorite")

            self.match_list.append(row)

        self.match_list.set_visible(bool(matches) or show_empty)

    def _on_match_activated(self, row):
        """Fill the dial entry with the selected contact's full number."""
        number = row.match_number
        if not number:
            return
        self.entry.set_text(number)
        self.entry.set_position(-1)
        self.entry.grab_focus()

    @staticmethod
    def _phone_values(phones):
        """Return plain phone numbers from the contact tuple representation."""
        values = []
        for phone in phones:
            if isinstance(phone, (list, tuple)):
                number = phone[0] if phone else ""
            elif isinstance(phone, dict):
                number = phone.get("number", "")
            else:
                number = phone
            if number:
                values.append(str(number))
        return values

    @staticmethod
    def _compact_number(number):
        """Strip visual phone punctuation for partial-number comparisons."""
        return "".join(c for c in str(number).casefold()
                       if c.isalnum() or c in "+*#")

    @classmethod
    def _favorite_matches_query(cls, favorite, query, query_norm):
        """Return whether a saved favorite partially matches the query."""
        name = str(favorite.get("name", "")).casefold()
        number = favorite.get("number", "")
        query_text = query.casefold()
        query_compact = cls._compact_number(query)
        number_compact = cls._compact_number(number)
        number_norm = normalize_number(number)
        return bool(
            (query_text and query_text in name) or
            (query_compact and query_compact in number_compact) or
            (query_norm and query_norm in number_norm)
        )

    def _find_contact_matches(self, query, favorites):
        """Search, deduplicate and rank partial contacts off the GTK thread."""
        favorite_numbers = {}
        for favorite in favorites:
            number = favorite.get("number", "")
            norm = normalize_number(number)
            if not norm:
                continue
            current = favorite_numbers.get(norm)
            if current is None or self._slot_rank(favorite) < self._slot_rank(current):
                favorite_numbers[norm] = favorite

        query_compact = self._compact_number(query)
        query_norm = normalize_number(query)
        rows = self.app_window.eds.search_contacts(
            query, limit=DIAL_MATCH_QUERY_LIMIT)

        matches = []
        seen_numbers = set()
        for favorite in favorites:
            number = str(favorite.get("number", ""))
            norm = normalize_number(number)
            dedupe_key = norm or self._compact_number(number)
            if (not dedupe_key or dedupe_key in seen_numbers or
                    not self._favorite_matches_query(
                        favorite, query, query_norm)):
                continue
            seen_numbers.add(dedupe_key)
            matches.append({
                "name": favorite.get("name") or number or _("Unknown"),
                "number": number,
                "favorite": True,
                "speed_slot": favorite.get("slot"),
            })

        for contact in rows:
            numbers = self._phone_values(contact[3] if len(contact) > 3 else [])
            if not numbers:
                continue

            def phone_rank(number):
                norm = normalize_number(number)
                compact = self._compact_number(number)
                partial = ((query_compact and query_compact in compact) or
                           (query_norm and query_norm in norm))
                return (not partial, norm not in favorite_numbers)

            number = min(numbers, key=phone_rank)
            norm = normalize_number(number)
            dedupe_key = norm or self._compact_number(number)
            if not dedupe_key or dedupe_key in seen_numbers:
                continue
            seen_numbers.add(dedupe_key)

            first = contact[1] if len(contact) > 1 else ""
            last = contact[2] if len(contact) > 2 else ""
            name = " ".join(part for part in (first, last) if part).strip()
            speed_favorite = favorite_numbers.get(norm)
            contact_favorite = bool(contact[5]) if len(contact) > 5 else False
            speed_slot = speed_favorite.get("slot") if speed_favorite else None
            matches.append({
                "name": name or _("Unknown"),
                "number": number,
                "favorite": bool(speed_favorite or contact_favorite),
                "speed_slot": speed_slot,
            })

        matches.sort(key=lambda match: (
            not match["favorite"],
            self._slot_rank(match),
            match["name"].casefold(),
            match["number"],
        ))
        return matches[:DIAL_MATCH_LIMIT]

    @staticmethod
    def _slot_rank(entry):
        """Sort speed-dial slots numerically, after entries with real slots."""
        slot = entry.get("speed_slot", entry.get("slot"))
        try:
            return int(slot)
        except (TypeError, ValueError):
            return 999

    def _apply_contact_matches(self, generation, query, matches):
        """Ignore stale background results and show the current query's rows."""
        if (generation != self._lookup_generation or
                query != self.entry.get_text().strip()):
            return
        self._show_contact_matches(matches, show_empty=True)

    def _start_contact_lookup(self, generation):
        """Resolve a speed-dial digit or start a partial contact search."""
        self._lookup_timer = None
        query = self.entry.get_text().strip()

        if generation != self._lookup_generation:
            return False

        favorite = self._favorite_for(query)
        if favorite:
            self._show_contact_matches([{
                "name": favorite.get("name") or favorite.get("number", ""),
                "number": favorite.get("number", ""),
                "favorite": True,
                "speed_slot": favorite.get("slot"),
            }])
            return False

        if len(query) < DIAL_MATCH_MIN_CHARS:
            self._show_contact_matches([])
            return False

        favorites = list(self.app_window.gsettings_mgr.get_favorites())
        run_in_background(
            self._find_contact_matches,
            query,
            favorites,
            on_complete=lambda matches: self._apply_contact_matches(
                generation, query, matches),
            on_error=lambda error: logger.warning(
                f"[Dialpad] Contact lookup failed: {error}"),
        )
        return False

    def _is_ussd(self, number):
        """Check if the number is a USSD code."""
        return (number.startswith("*") or number.startswith("#")) and number.endswith("#")

    def on_digit_clicked(self, digit):
        """Handle digit button click."""
        pos = self.entry.get_position()
        text = self.entry.get_text()
        new_text = text[:pos] + digit + text[pos:]
        self.entry.set_text(new_text)
        self.entry.set_position(pos + 1)

        if self.app_window.ofono and self.app_window.ofono.active_calls:
            self.app_window.ofono.send_dtmf(digit)

    def on_backspace(self, btn):
        """Handle backspace button click."""
        text = self.entry.get_text()
        pos = self.entry.get_position()

        is_selected = False
        start = 0
        end = 0

        try:
            bounds = self.entry.get_selection_bounds()
            if bounds and len(bounds) == 3:
                is_selected, start, end = bounds
        except Exception as e:
            logger.debug(f"[Dialpad] Selection bounds error: {e}")

        if is_selected:
            low = min(start, end)
            high = max(start, end)
            new_text = text[:low] + text[high:]
            self.entry.set_text(new_text)
            self.entry.set_position(low)
        else:
            if pos > 0:
                new_text = text[:pos - 1] + text[pos:]
                self.entry.set_text(new_text)
                self.entry.set_position(pos - 1)

    def on_call_clicked(self, btn):
        """Handle call button click."""
        number = self.entry.get_text().strip()
        if not number:
            return

        favorite = self._favorite_for(number)
        if favorite:
            self.app_window.start_call(favorite.get("number", ""))
            return

        if self._is_ussd(number):
            self.app_window.handle_ussd(number)
        else:
            self.app_window.start_call(number)

    def on_anon_call_clicked(self, btn):
        """Handle anonymous call button click."""
        number = self.entry.get_text().strip()
        if not number:
            return

        favorite = self._favorite_for(number)
        if favorite:
            self.app_window.start_call(favorite.get("number", ""), hide_id=True)
            return

        if self._is_ussd(number):
            self.app_window.handle_ussd(number)
        else:
            self.app_window.start_call(number, hide_id=True)

    def on_paste_clicked(self, btn):
        """Handle paste button click."""
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.read_text_async(None, self.on_paste_finish)

    def on_paste_finish(self, clipboard, result):
        """Finish paste operation."""
        try:
            text = clipboard.read_text_finish(result)
            if text:
                valid_number = normalize_number(text)
                if valid_number:
                    pos = self.entry.get_position()
                    current = self.entry.get_text()
                    new_text = current[:pos] + valid_number + current[pos:]
                    self.entry.set_text(new_text)
                    self.entry.set_position(pos + len(valid_number))
                else:
                    if (self.app_window and self.app_window.notify_error is not None):
                        self.app_window.notify_error(_("Invalid number in clipboard"))
                    else:
                        self.app_window.add_toast(Adw.Toast(title=_("Invalid number")))
        except Exception as e:
            logger.warning(f"[Dialpad] Paste finish error: {e}")
