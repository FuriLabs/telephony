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

from gi.repository import Gtk, Adw, Gdk, GLib, Pango
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _

from telephony.shared.utils.phone_utils import normalize_number

BUTTONS_LAYOUT = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#']


class DialpadView(Adw.Bin):
    """View for dialing phone numbers."""

    def __init__(self, app_window):
        self._lookup_timer = None
        """Initialize the DialpadView."""
        super().__init__()
        self.app_window = app_window

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text(_("Enter number"))
        self.entry.set_alignment(0.5)
        self.entry.add_css_class("title-1")
        self.entry.set_can_focus(True)
        self.entry.set_editable(True)
        self.entry.set_property("im-module", "none")

        self.info_label = Gtk.Label(label="")
        self.info_label.add_css_class("caption")
        self.info_label.add_css_class("dim-label")
        self.info_label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(self.info_label)

        self.entry.connect("notify::text", lambda obj, pspec: self._update_info_label(obj, pspec))

        box.append(self.entry)

        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        box.append(grid)

        for i, digit in enumerate(BUTTONS_LAYOUT):
            btn = Gtk.Button()
            btn.set_size_request(74, 44)
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
        paste_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_paste_clicked(b) or False))
        grid.attach(paste_btn, 0, 4, 1, 1)

        plus_btn = Gtk.Button(label="+")
        plus_btn.add_css_class("pill")
        plus_btn.add_css_class("compact-btn")
        plus_btn.set_size_request(74, 44)
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
        backspace_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_backspace(b) or False))
        grid.attach(backspace_btn, 2, 4, 1, 1)

        self.anon_btn = Gtk.Button()
        anon_btn = self.anon_btn
        anon_btn.add_css_class("pill")
        anon_btn.add_css_class("btn-anon-green")
        anon_btn.add_css_class("compact-btn")
        anon_btn.set_size_request(74, 44)
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
        clear_all_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.entry.set_text("") or False))
        grid.attach(clear_all_btn, 1, 5, 1, 1)

        self.norm_btn = Gtk.Button(icon_name="call-start-symbolic")
        norm_btn = self.norm_btn
        norm_btn.add_css_class("pill")
        norm_btn.add_css_class("call-btn")
        norm_btn.add_css_class("compact-btn")
        norm_btn.set_size_request(74, 44)
        norm_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_call_clicked(b) or False))
        grid.attach(norm_btn, 2, 5, 1, 1)

        clamp = Adw.Clamp(maximum_size=300)
        clamp.set_child(box)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(clamp)
        scrolled.set_vexpand(True)
        self.set_child(scrolled)

    def _update_info_label(self, entry, param):
        """Update the info label based on input."""
        if (self._lookup_timer is not None) and self._lookup_timer:
            GLib.source_remove(self._lookup_timer)
        self._lookup_timer = GLib.timeout_add(200, self._perform_lookup)

    def set_calling_enabled(self, enabled):
        """Enable or disable the two call buttons."""
        self.anon_btn.set_sensitive(enabled)
        self.norm_btn.set_sensitive(enabled)

    def cleanup(self):
        """Cancel the pending contact lookup timer."""
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

    def _perform_lookup(self):
        """Perform contact lookup."""
        self._lookup_timer = None
        text = self.entry.get_text().strip()

        favorite = self._favorite_for(text)
        if favorite:
            self.info_label.set_text(_("{name} · Speed dial {slot}").format(
                name=favorite.get("name") or favorite.get("number", ""),
                slot=favorite.get("slot")))
            self.info_label.remove_css_class("dim-label")
            return False

        if len(text) < 3:
            self.info_label.set_text("")
            self.info_label.add_css_class("dim-label")
            return False

        name = self.app_window.eds.get_contact_name(text)
        if name:
            self.info_label.set_text(name)
            self.info_label.remove_css_class("dim-label")
        else:
            self.info_label.set_text(_("Unknown"))
            self.info_label.add_css_class("dim-label")
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
