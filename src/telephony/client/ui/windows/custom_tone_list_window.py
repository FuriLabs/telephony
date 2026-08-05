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

import os
import shutil
from gi.repository import Gtk, Adw, GLib
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _

from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.thread_utils import run_in_background
from telephony.client.ui.widgets.common_widget import (populate_contact_search_results, translate_phone_label)


class CustomToneListWindow(Adw.NavigationPage):
    """Settings subpage managing the contacts with custom tones (SMS or Ringtone)."""

    def __init__(self, parent, db, eds, mode="sms"):
        self.grp_list = None
        title = _("Individual SMS Notifications") if mode == "sms" else _("Individual Ringtones")
        super().__init__(title=title)
        self.gsettings_mgr = db
        self.eds = eds
        self.mode = mode
        self.app_window = parent.main_window

        self.local_tones = []
        try:
            current = self.gsettings_mgr.get_notification_override_sms_custom_tone_contacts() if mode == "sms" else self.gsettings_mgr.get_notification_override_call_custom_contacts()
            if current:
                for c in current:
                    self.local_tones.append(c.copy())
        except Exception as e:
            logger.error(f"[CustomTone] Failed to load initial data: {e}")

        view = Adw.ToolbarView()
        self.set_child(view)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)

        btn_save = Gtk.Button(label=_("Save"))
        btn_save.add_css_class("suggested-action")
        btn_save.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_save_clicked(b) or False))
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

        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Find contact to add..."))
        search_box.append(self.search_entry)

        self.search_timer = None
        self.search_token = 0

        main_box.append(search_box)

        info_text = _("Select a contact and choose a custom sound file (.ogg or .oga). The file will be copied to your local storage.")
        lbl_info = Gtk.Label(label=info_text, wrap=True)
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

        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_results_list.connect("row-activated", self._on_result_activated)

        self.eds_signals = []
        self.db_signals = []
        self._connect_external_signals()

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

        self._refresh_list()

    def _connect_external_signals(self):
        """Connect the external refresh signals if not already connected."""
        if self.db_signals:
            return
        if self.app_window is not None:
            sig_id = self.app_window.db.connect('blocklist-updated', lambda *args: GLib.idle_add(self._refresh_list))
            self.db_signals.append((self.app_window.db, sig_id))

    def _on_map(self, widget):
        """Reconnect external signals and catch up when shown again."""
        self._connect_external_signals()
        self._refresh_list()

    def _on_unmap(self, widget):
        if self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

        for obj, sig_id in self.eds_signals:
            if obj.handler_is_connected(sig_id):
                obj.disconnect(sig_id)
        self.eds_signals.clear()

        for obj, sig_id in self.db_signals:
            if obj.handler_is_connected(sig_id):
                obj.disconnect(sig_id)
        self.db_signals.clear()

    def close(self):
        """Pop this page once; repeat taps during the transition are ignored."""
        nav = self.get_ancestor(Adw.NavigationView)
        if nav and nav.get_visible_page() is self:
            nav.pop()

    def _on_save_clicked(self, btn):
        """Handle save button click."""
        if self.mode == "sms":
            self.gsettings_mgr.set_notification_override_sms_custom_tone_contacts(self.local_tones)
        else:
            self.gsettings_mgr.set_notification_override_call_custom_contacts(self.local_tones)
        GLib.idle_add(lambda: self.close() or False)

    def _refresh_list(self):
        """Refresh the list of configured tones."""
        if (self.grp_list is not None) and self.grp_list:
            self.page_list.remove(self.grp_list)

        self.grp_list = Adw.PreferencesGroup()
        self.page_list.add(self.grp_list)

        if not self.local_tones:
            pass

        for c in self.local_tones:
            self._create_tone_row(c)

    def _create_tone_row(self, tone_data):
        """Create a row for a configured tone."""
        name = tone_data.get("name", _("Unknown"))
        number = tone_data.get("number", "")
        path = tone_data.get("path", "")

        filename = os.path.basename(path)

        row = Adw.ActionRow(title=name, subtitle=f"{number}\n{filename}")
        row.set_title_lines(1)
        row.set_subtitle_lines(2)

        btn_edit = Gtk.Button(icon_name="document-edit-symbolic")
        btn_edit.add_css_class("flat")
        btn_edit.add_css_class("circular")
        btn_edit.set_tooltip_text(_("Edit"))
        btn_edit.connect("clicked", lambda b: self._on_edit_tone(tone_data))
        row.add_suffix(btn_edit)

        btn_del = Gtk.Button(icon_name="user-trash-symbolic")
        btn_del.add_css_class("flat")
        btn_del.add_css_class("circular")
        btn_del.set_tooltip_text(_("Remove"))
        btn_del.connect("clicked", lambda b: self._delete_tone(tone_data))

        row.add_suffix(btn_del)
        self.grp_list.add(row)

    def _on_edit_tone(self, tone_data):
        """Edit an existing tone configuration."""
        self._open_audio_picker(tone_data)

    def _delete_tone(self, tone_data):
        """Delete a tone configuration (defer file deletion)."""
        target_num = normalize_number(tone_data.get("number", ""))
        self.local_tones = [c for c in self.local_tones if normalize_number(c.get("number", "")) != target_num]

        GLib.idle_add(lambda: self._refresh_list())

    def _on_search_changed(self, entry):
        """Handle search text change."""
        if self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

        self.search_timer = GLib.timeout_add(200, self._perform_search)

    def _perform_search(self):
        """Execute search."""
        self.search_timer = None
        query = self.search_entry.get_text().strip()
        if not query:
            self.stack.set_visible_child_name("list")
            return False

        self.stack.set_visible_child_name("search")

        self.search_token += 1
        current_token = self.search_token

        def done(contacts):
            if self.search_token != current_token:
                return
            self._build_search_results(contacts or [], query)

        run_in_background(self.eds.search_contacts, query, on_complete=done)
        return False

    def _is_result_added(self, normalized_number):
        """Return True when the normalized number already has a custom tone."""
        for existing in self.local_tones:
            if normalize_number(existing.get("number", "")) == normalized_number:
                return True
        return False

    def _build_search_results(self, contacts, query):
        """Build search results UI."""
        populate_contact_search_results(
            self.search_results_list,
            contacts,
            self.eds,
            is_added=self._is_result_added,
            on_add=lambda row: self._on_result_activated(None, row),
            translate_label=translate_phone_label,
            unknown_name=_("Unknown")
        )

    def _on_result_activated(self, listbox, row):
        """Handle activation of a search result."""
        if not row.get_sensitive():
            return

        data = getattr(row, "contact_data", None)
        if data is None:
            return

        self._open_audio_picker(data)

    def _open_audio_picker(self, contact_data):
        """Open file picker to select custom tone."""
        dialog = Gtk.FileChooserNative(
            title=_("Select Tone for {name}").format(name=contact_data.get('name')),
            transient_for=self.get_root(),
            action=Gtk.FileChooserAction.OPEN,
            accept_label=_("Select"),
            cancel_label=_("Cancel")
        )

        filter_audio = Gtk.FileFilter()
        filter_audio.set_name(_("Supported Audio (OGG)"))
        filter_audio.add_mime_type("audio/ogg")
        filter_audio.add_mime_type("application/ogg")
        filter_audio.add_pattern("*.oga")
        filter_audio.add_pattern("*.ogg")

        dialog.add_filter(filter_audio)

        def on_response(d, response):
            if response == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                path = f.get_path()
                if path:
                    self._process_selected_file(contact_data, path)
            GLib.idle_add(lambda: d.destroy() or False)

        dialog.connect("response", on_response)
        dialog.show()

    def _process_selected_file(self, contact_data, src_path):
        """Copy selected file and add to configuration."""
        target_dir = os.path.expanduser("~/.local/share/telephony/sounds")
        if not os.path.exists(target_dir):
            os.makedirs(target_dir, exist_ok=True)

        norm_num = normalize_number(contact_data.get("number", ""))
        ext = os.path.splitext(src_path)[1].lower()
        if ext not in [".ogg", ".oga"]:
            self.overlay.add_toast(Adw.Toast.new(_("Only .ogg or .oga files are supported")))
            return

        suffix = "sms" if self.mode == "sms" else "ringtone"
        new_filename = f"{norm_num}-{suffix}{ext}"
        dest_path = os.path.join(target_dir, new_filename)

        try:
            shutil.copy2(src_path, dest_path)
        except Exception as e:
            logger.error(f"[CustomTone] Copy failed: {e}")
            self.overlay.add_toast(Adw.Toast.new(_("Failed to copy audio file")))
            return

        self.local_tones = [t for t in self.local_tones if normalize_number(t.get("number", "")) != norm_num]

        new_entry = {
            "name": contact_data.get("name"),
            "number": norm_num,
            "path": dest_path
        }

        self.local_tones.insert(0, new_entry)

        def _update_ui():
            self.search_entry.set_text("")
            self.stack.set_visible_child_name("list")
            self._refresh_list()
            return False

        GLib.idle_add(_update_ui)
