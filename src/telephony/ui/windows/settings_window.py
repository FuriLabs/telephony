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
import subprocess
from ...backend.utils.thread_utils import run_in_background
from gi.repository import Gtk, Adw, GLib
from loguru import logger
from gettext import gettext as _

from ...backend.utils.phone_utils import normalize_number
from ...backend.utils.region_utils import set_custom_region
from ...backend.utils.system_utils import get_phosh_emergency_calls, set_phosh_emergency_calls
from .dnd_bypass_contacts_list_window import DndBypassContactsListWindow
from .custom_tone_list_window import CustomToneListWindow


from .advanced_settings_window import AdvancedSettingsWindow


class SettingsWindow(Adw.Window):
    """Main settings window for the application."""

    def __init__(self, main_window, eds_manager, ofono_manager):
        self.source_rows = None
        self.sources_state = None
        """Initialize Settings Window."""
        super().__init__(title=_("Settings"))

        self.main_window = main_window
        self.eds = eds_manager

        self.set_transient_for(main_window)
        self.set_modal(True)
        self.set_default_size(400, 750)

        self.emergency_rows = []
        self.reject_rows = []

        self.temp_ringback_file = self.main_window.gsettings_mgr.get_setting(
            "ringback_custom_file")

        self.overlay = Adw.ToastOverlay()
        self.set_content(self.overlay)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(main_box)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(
            lambda: self.close() or False))
        header.pack_start(btn_cancel)

        btn_save = Gtk.Button(label=_("Save"))
        btn_save.add_css_class("suggested-action")
        btn_save.connect("clicked", lambda b: GLib.idle_add(
            lambda: self.on_save_clicked(b) or False))
        header.pack_end(btn_save)

        main_box.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        main_box.append(scroll)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll.set_child(content_box)

        page = Adw.PreferencesPage()
        content_box.append(page)

        grp_rb = Adw.PreferencesGroup(title=_("Ringback Tone"))
        page.add(grp_rb)

        self.sw_rb_enable = Adw.SwitchRow(title=_("Enable Local Ringback"))
        enabled_str = self.main_window.gsettings_mgr.get_setting(
            "ringback_enabled")
        self.sw_rb_enable.set_active(enabled_str == "true")

        btn_info = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_info.set_valign(Gtk.Align.CENTER)
        btn_info.add_css_class("flat")
        btn_info.add_css_class("circular")
        btn_info.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_ringback_info(b) or False))
        self.sw_rb_enable.add_suffix(btn_info)

        grp_rb.add(self.sw_rb_enable)

        self.row_rb_file = Adw.ActionRow(title=_("Selected Tone"))

        display_text = self.temp_ringback_file if self.temp_ringback_file else _(
            "System Default")

        self.lbl_rb_path = Gtk.Label(label=display_text)
        self.lbl_rb_path.set_ellipsize(3)
        self.lbl_rb_path.set_max_width_chars(25)
        self.lbl_rb_path.set_valign(Gtk.Align.CENTER)
        self.lbl_rb_path.add_css_class("dim-label")
        self.row_rb_file.add_suffix(self.lbl_rb_path)

        btn_clear = Gtk.Button(icon_name="edit-clear-symbolic")
        btn_clear.set_valign(Gtk.Align.CENTER)
        btn_clear.add_css_class("flat")
        btn_clear.add_css_class("circular")
        btn_clear.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._clear_ringback_file() or False))
        self.row_rb_file.add_suffix(btn_clear)

        btn_pick = Gtk.Button(icon_name="folder-open-symbolic")
        btn_pick.set_valign(Gtk.Align.CENTER)
        btn_pick.add_css_class("flat")
        btn_pick.add_css_class("circular")
        btn_pick.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._open_audio_picker(b) or False))
        self.row_rb_file.add_suffix(btn_pick)

        grp_rb.add(self.row_rb_file)

        grp_sim = Adw.PreferencesGroup(title=_("SIM Settings"))
        page.add(grp_sim)

        self.entry_own_num = Adw.EntryRow(title=_("My Number"))
        self.entry_own_num.set_title(_("My Number (International Format)"))
        saved_num = self.main_window.gsettings_mgr.get_setting("own_number")
        self.entry_own_num.set_text(saved_num if saved_num else "")

        grp_sim.add(self.entry_own_num)

        self.entry_country_code = Adw.EntryRow(title=_("Default Country Code"))
        self.entry_country_code.set_title(_("Default Country Code"))

        btn_cc_info = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_cc_info.set_valign(Gtk.Align.CENTER)
        btn_cc_info.add_css_class("flat")
        btn_cc_info.add_css_class("circular")
        btn_cc_info.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_country_code_info(b) or False))
        self.entry_country_code.add_suffix(btn_cc_info)

        saved_cc = self.main_window.gsettings_mgr.get_setting(
            "default_country_code")
        self.entry_country_code.set_text(saved_cc if saved_cc else "")
        grp_sim.add(self.entry_country_code)

        self.grp_contacts = Adw.PreferencesGroup(title=_("Address Books"))

        btn_info_contacts = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_info_contacts.add_css_class("flat")
        btn_info_contacts.add_css_class("circular")
        btn_info_contacts.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_addressbook_info(b) or False))

        try:
            self.grp_contacts.set_header_suffix(btn_info_contacts)
        except AttributeError:
            row_info = Adw.ActionRow(title=_("Address Book Info"))
            row_info.add_suffix(btn_info_contacts)
            self.grp_contacts.add(row_info)

        page.add(self.grp_contacts)

        self.model_default_ab = Gtk.StringList()
        self.row_default_ab = Adw.ComboRow(
            title=_("Default Address Book"), model=self.model_default_ab)
        self.row_default_ab.connect(
            "notify::selected", lambda obj, pspec: self._on_default_ab_changed(obj, pspec))
        self.grp_contacts.add(self.row_default_ab)

        self.sw_duplicate_resolver = Adw.SwitchRow(
            title=_("Duplicate Resolver"),
            subtitle=_(
                "Scan for duplicate numbers and show a resolution banner in the Contacts view.")
        )
        res_val = self.main_window.gsettings_mgr.gsettings.get_boolean(
            "duplicate-resolver-enabled")
        self.sw_duplicate_resolver.set_active(res_val)
        self.sw_duplicate_resolver.connect("notify::active", lambda w, p: self.main_window.gsettings_mgr.gsettings.set_boolean(
            "duplicate-resolver-enabled", w.get_active()))
        self.grp_contacts.add(self.sw_duplicate_resolver)

        self.sources_state = self.eds.get_sources_info()
        self._build_sources_list(rebuild_dropdown=True)

        grp_emerg_toggle = Adw.PreferencesGroup(
            title=_("Lockscreen Emergency"))
        page.add(grp_emerg_toggle)

        self.sw_emerg = Adw.SwitchRow(title=_("Show Emergency Button"))
        self.sw_emerg.set_active(self._get_gsettings_emergency())
        grp_emerg_toggle.add(self.sw_emerg)

        self.grp_emerg_list = Adw.PreferencesGroup(
            title=_("Emergency Numbers"))
        page.add(self.grp_emerg_list)

        row_add = Adw.ActionRow(title=_("Add Number"))
        btn_add = Gtk.Button(icon_name="list-add-symbolic")
        btn_add.add_css_class("flat")
        btn_add.add_css_class("circular")
        btn_add.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._add_emergency_placeholder(b) or False))
        row_add.add_suffix(btn_add)
        self.grp_emerg_list.add(row_add)

        saved_list = self.main_window.gsettings_mgr.get_emergency_numbers()
        for item in saved_list:
            self._add_emergency_row(
                item.get("name", ""), item.get("number", ""))

        self.grp_reject_list = Adw.PreferencesGroup(title=_("Quick Response"))
        page.add(self.grp_reject_list)

        row_add_msg = Adw.ActionRow(title=_("Add Decline Message"))
        btn_add_msg = Gtk.Button(icon_name="list-add-symbolic")
        btn_add_msg.add_css_class("flat")
        btn_add_msg.add_css_class("circular")
        btn_add_msg.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._add_reject_row("") or False))
        row_add_msg.add_suffix(btn_add_msg)
        self.grp_reject_list.add(row_add_msg)

        saved_msgs = self.main_window.gsettings_mgr.get_reject_call_messages()
        if not saved_msgs:
            saved_msgs = [_("I can't talk right now.")]
        for msg in saved_msgs:
            self._add_reject_row(msg)

        grp_notif = Adw.PreferencesGroup(title=_("Notification Exceptions"))
        page.add(grp_notif)

        self.sw_repeated = Adw.SwitchRow(title=_("Repeated Calls Bypass"))
        self.sw_repeated.set_subtitle(
            _("If the same number calls 3 times in 5 minutes, force max volume."))
        self.sw_repeated.set_active(self.main_window.gsettings_mgr.get_setting(
            "notification_override_repeated_calls_bypass") == "true")

        btn_info_rep = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_info_rep.set_valign(Gtk.Align.CENTER)
        btn_info_rep.add_css_class("flat")
        btn_info_rep.add_css_class("circular")
        btn_info_rep.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_repeated_info(b) or False))
        self.sw_repeated.add_suffix(btn_info_rep)

        grp_notif.add(self.sw_repeated)

        row_prio = Adw.ActionRow(title=_("Notification Overrides"))
        row_prio.set_subtitle(_("Manage contacts that always play sound"))

        btn_info_prio = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_info_prio.set_valign(Gtk.Align.CENTER)
        btn_info_prio.add_css_class("flat")
        btn_info_prio.add_css_class("circular")
        btn_info_prio.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_overrides_info(b) or False))

        btn_prio = Gtk.Button(label=_("Manage"))
        btn_prio.set_valign(Gtk.Align.CENTER)
        btn_prio.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._open_priority_window(b) or False))
        row_prio.add_suffix(btn_prio)
        row_prio.add_suffix(btn_info_prio)
        grp_notif.add(row_prio)

        row_sms_tone = Adw.ActionRow(title=_("Individual SMS Notifications"))
        row_sms_tone.set_subtitle(
            _("Set custom notification sounds for specific contacts"))

        btn_sms_tone = Gtk.Button(label=_("Manage"))
        btn_sms_tone.set_valign(Gtk.Align.CENTER)
        btn_sms_tone.connect(
            "clicked", lambda b: self._open_custom_tone_window("sms"))
        row_sms_tone.add_suffix(btn_sms_tone)
        grp_notif.add(row_sms_tone)

        row_ring_tone = Adw.ActionRow(title=_("Individual Ringtones"))
        row_ring_tone.set_subtitle(
            _("Set custom ringtones for specific contacts"))

        btn_ring_tone = Gtk.Button(label=_("Manage"))
        btn_ring_tone.set_valign(Gtk.Align.CENTER)
        btn_ring_tone.connect(
            "clicked", lambda b: self._open_custom_tone_window("ringtone"))
        row_ring_tone.add_suffix(btn_ring_tone)
        grp_notif.add(row_ring_tone)

        self._init_desktop_toggles(page)

        self._init_unknown_callers(page)

        grp_adv = Adw.PreferencesGroup()
        page.add(grp_adv)

        btn_adv = Gtk.Button(label=_("Advanced Settings"))
        btn_adv.add_css_class("suggested-action")
        btn_adv.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._open_modem_settings(b) or False))

        grp_adv.add(btn_adv)

    def _show_country_code_info(self, btn):
        """Show information about country code setting."""
        body_text = _("The Default Country Code is used to format numbers that don't include an international prefix (e.g., +358).\n\nIf left empty, the system tries to detect it automatically from your SIM card or network.\n\nYou can manually override it here by entering a 2-letter ISO region code (e.g. US, FI, DE) or a calling code (e.g. +358, 358, +1, 1).")

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Default Country Code"),
            body=body_text
        )
        dialog.add_response("close", _("Close"))
        dialog.set_response_appearance(
            "close", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: GLib.idle_add(
            lambda: d.close() or False))
        dialog.present()

    def _clear_ringback_file(self):
        """Clear the custom ringback file to use default."""
        self.temp_ringback_file = ""
        self.lbl_rb_path.set_text(_("System Default"))

    def _show_ringback_info(self, btn):
        """Show information about ringback tones."""
        body_text = _("The ringback tone is the sound played (toot-toot-toot) while waiting for an "
                      "outgoing call to be answered. Some carriers don't generate it so we have "
                      "option to generate it manually here. \n\n"
                      "You can select your own music file, or clear it to use the standard system tone.")

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Ringback Tone"),
            body=body_text
        )
        dialog.add_response("close", _("Close"))
        dialog.set_response_appearance(
            "close", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: GLib.idle_add(
            lambda: d.close() or False))
        dialog.present()

    def _open_audio_picker(self, btn):
        """Open file picker for custom ringback tone."""
        dialog = Gtk.FileChooserNative(
            title=_("Select Ringback Tone"),
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label=_("Select"),
            cancel_label=_("Cancel")
        )

        filter_audio = Gtk.FileFilter()
        filter_audio.set_name(_("Audio Files"))
        filter_audio.add_mime_type("audio/*")
        for pattern in ["*.oga", "*.ogg", "*.wav", "*.mp3", "*.flac", "*.m4a"]:
            filter_audio.add_pattern(pattern)

        dialog.add_filter(filter_audio)
        dialog.connect("response", self._on_audio_selected)
        dialog.show()

    def _on_audio_selected(self, dialog, response):
        """Handle audio file selection."""
        if response == Gtk.ResponseType.ACCEPT:
            f = dialog.get_file()
            path = f.get_path()
            if path:
                self.temp_ringback_file = path
                self.lbl_rb_path.set_label(path)
        GLib.idle_add(lambda: dialog.destroy() or False)

    def _open_modem_settings(self, btn):
        """Open advanced modem settings."""
        win = AdvancedSettingsWindow(self)
        win.present()

    def _show_addressbook_info(self, btn):
        """Show information about address book priority."""
        body_text = _("Select the address books to use with Telephony and arrange them in order of priority. The topmost address book has the highest priority and will be preferred if a number is found in multiple address books. You can use the Duplicate Resolver toggle below to help manage duplicates, but even if disabled, we encourage enabling it for a better experience.")

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Address Books"),
            body=body_text
        )
        dialog.add_response("close", _("Close"))
        dialog.set_response_appearance(
            "close", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: GLib.idle_add(
            lambda: d.close() or False))
        dialog.present()

    def _show_repeated_info(self, btn):
        """Show info about repeated calls bypass."""
        d = Adw.MessageDialog(
            transient_for=self,
            heading=_("Repeated Calls Bypass"),
            body=_("When enabled, if the same phone number calls you 3 times within 5 minutes, the third call (and subsequent ones within the window) will force the phone to ring at maximum volume, bypassing Silent or Do Not Disturb modes.")
        )
        d.add_response("ok", _("OK"))
        d.connect("response", lambda d, r: GLib.idle_add(
            lambda: d.close() or False))
        d.present()

    def _show_overrides_info(self, btn):
        """Show info about notification overrides."""
        d = Adw.MessageDialog(
            transient_for=self,
            heading=_("Notification Overrides"),
            body=_("Choose contacts that will always play notifications at full volume, even when the phone is in Silent or Do Not Disturb mode.")
        )
        d.add_response("ok", _("OK"))
        d.connect("response", lambda d, r: GLib.idle_add(
            lambda: d.close() or False))
        d.present()

    def _open_priority_window(self, btn):
        """Open the priority contacts management window."""
        win = DndBypassContactsListWindow(
            self, self.main_window.gsettings_mgr, self.eds)
        win.present()

    def _open_custom_tone_window(self, mode):
        """Open the custom tone management window."""
        win = CustomToneListWindow(
            self, self.main_window.gsettings_mgr, self.eds, mode=mode)
        win.present()

    def _init_unknown_callers(self, page):
        """Initialize Unknown Callers options."""
        grp_uc = Adw.PreferencesGroup(title=_("Unknown Callers"))
        page.add(grp_uc)

        self.model_uc_action = Gtk.StringList()
        self.row_uc_action = Adw.ComboRow(
            title=_("Unknown Callers Action"), model=self.model_uc_action)
        self.row_uc_action.set_title_lines(0)
        self.row_uc_action.set_subtitle(_("Applies also to hidden numbers."))
        self.row_uc_action.set_subtitle_lines(0)
        self.action_options = [
            ("none", _("Do Nothing")),
            ("block", _("Block Completely")),
            ("hide", _("Silence and Hide UI")),
            ("silence", _("Silence Only"))
        ]
        for act_id, act_name in self.action_options:
            self.model_uc_action.append(act_name)

        grp_uc.add(self.row_uc_action)

        self.sw_uc_search = Adw.SwitchRow(title=_(
            "Add Search button to InCall window and Call History for unknown callers"))
        self.sw_uc_search.set_title_lines(0)
        grp_uc.add(self.sw_uc_search)

        self.model_uc_engine = Gtk.StringList()
        self.row_uc_engine = Adw.ComboRow(
            title=_("Search Engine"), model=self.model_uc_engine)
        self.engine_options = [
            ("duckduckgo", "DuckDuckGo"),
            ("startpage", "Startpage"),
            ("custom", _("Custom URL"))
        ]
        for _ignored, eng_name in self.engine_options:
            self.model_uc_engine.append(eng_name)

        self.entry_uc_custom = Adw.EntryRow(title=_("Custom URL"))

        btn_uc_info = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_uc_info.set_valign(Gtk.Align.CENTER)
        btn_uc_info.add_css_class("flat")
        btn_uc_info.add_css_class("circular")
        btn_uc_info.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_custom_url_info(b) or False))
        self.entry_uc_custom.add_suffix(btn_uc_info)

        grp_uc.add(self.row_uc_engine)
        grp_uc.add(self.entry_uc_custom)

        def _on_search_toggle(w, p):
            self._update_uc_ui()
        self.sw_uc_search.connect("notify::active", _on_search_toggle)

        def _on_engine_change(w, p):
            idx = w.get_selected()
            if idx >= 0 and self.engine_options[idx][0] == "custom":
                self.entry_uc_custom.set_visible(True)
            else:
                self.entry_uc_custom.set_visible(False)
        self.row_uc_engine.connect("notify::selected", _on_engine_change)

        uc_action = self.main_window.gsettings_mgr.get_setting(
            "unknown_callers") or "none"
        for i, (k, act_name) in enumerate(self.action_options):
            if k == uc_action:
                self.row_uc_action.set_selected(i)
                break

        self.sw_uc_search.set_active(self.main_window.gsettings_mgr.get_setting(
            "unknown_callers_search") == "true")

        engine = self.main_window.gsettings_mgr.get_setting(
            "unknown_callers_engine") or "duckduckgo"
        for i, (k, eng_name) in enumerate(self.engine_options):
            if k == engine:
                self.row_uc_engine.set_selected(i)
                break

        custom_url = self.main_window.gsettings_mgr.get_setting(
            "unknown_callers_custom_url") or ""
        self.entry_uc_custom.set_text(custom_url)

        self._update_uc_ui()

    def _show_custom_url_info(self, btn):
        body_text = _(
            "Enter the URL you want to use for searching unknown numbers. Include '{number}' in the URL where the caller's phone number should go.\n\nFor example: https://mysearch.com/?q={number}\n\nWhen you tap the search button during a call, we will replace '{number}' with the actual phone number and open it in your default browser.")

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Custom Search URL"),
            body=body_text
        )
        dialog.add_response("close", _("Close"))
        dialog.set_response_appearance(
            "close", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: GLib.idle_add(
            lambda: d.close() or False))
        dialog.present()

    def _update_uc_ui(self):
        search_active = self.sw_uc_search.get_active()
        self.row_uc_engine.set_visible(search_active)
        idx = self.row_uc_engine.get_selected()
        if search_active and idx >= 0 and self.engine_options[idx][0] == "custom":
            self.entry_uc_custom.set_visible(True)
        else:
            self.entry_uc_custom.set_visible(False)

    def _init_desktop_toggles(self, page):
        """Initialize desktop shortcut toggles."""
        grp_dt = Adw.PreferencesGroup(title=_("Desktop Shortcuts"))
        page.add(grp_dt)

        shortcuts = [
            ("Telephony", "io.furios.Telephony.desktop", "full"),
            (_("Calls"), "io.furios.Telephony.Calls.desktop", "calls"),
            (_("Messages"), "io.furios.Telephony.Messages.desktop", "messages"),
            (_("Contacts"), "io.furios.Telephony.Contacts.desktop", "contacts")
        ]

        self.dt_toggles = []

        for name, filename, autostart_key in shortcuts:
            row = Adw.SwitchRow(title=name)

            is_visible = self._is_desktop_file_visible(filename)

            row.set_active(is_visible)
            handler_id = row.connect("notify::active", lambda w, p, f=filename,
                                     k=autostart_key: self._toggle_desktop_file(w, f, k, w.get_active()))
            grp_dt.add(row)
            self.dt_toggles.append((row, filename, handler_id, autostart_key))

        self._update_autostart_ui_state()

    def _is_desktop_file_visible(self, filename):
        """Check if desktop file is visible by looking at home and system files."""
        user_path = os.path.join(self._get_user_desktop_dir(), filename)
        sys_path = self._get_system_desktop_path(filename)

        target_path = user_path if os.path.exists(user_path) else sys_path

        if not target_path or not os.path.exists(target_path):
            return True

        try:
            with open(target_path, 'r') as f:
                content = f.read()
                if "NoDisplay=true" in content or "Hidden=true" in content:
                    return False
        except Exception as e:
            logger.error(f"[Settings] Error reading desktop file {target_path}: {e}")

        return True

    def _update_autostart_ui_state(self):
        """Update the full toggle based on the others."""
        full_row = None
        others_all_active = True

        for row, filename, handler_id, key in self.dt_toggles:
            if key == "full":
                full_row = row
            else:
                if not row.get_active():
                    others_all_active = False

        if full_row:
            if not others_all_active:
                if not full_row.get_active():
                    full_row.set_active(True)
                full_row.set_sensitive(False)
            else:
                full_row.set_sensitive(True)

    def _get_user_desktop_dir(self):
        """Get user applications directory."""
        return os.path.expanduser("~/.local/share/applications")

    def _get_system_desktop_path(self, filename):
        """Find system desktop file."""
        paths = [
            "/usr/share/applications",
            "/usr/local/share/applications"
        ]
        for p in paths:
            full = os.path.join(p, filename)
            if os.path.exists(full):
                return full
        return None

    def _toggle_desktop_file(self, row, filename, autostart_key, visible):
        """Toggle desktop file visibility asynchronously using pkexec."""
        self._update_autostart_ui_state()

        if visible == self._is_desktop_file_visible(filename):
            return

        def _task():
            try:
                user_path = os.path.join(
                    self._get_user_desktop_dir(), filename)
                if os.path.exists(user_path):
                    try:
                        os.remove(user_path)
                    except Exception as e:
                        logger.warning(
                            f"[Settings] Remove user desktop file warning: {e}")

                sys_path = self._get_system_desktop_path(filename)
                if not sys_path:
                    logger.warning(
                        f"[Settings] No system desktop file found for {filename}")
                    GLib.idle_add(self._revert_toggle, row, not visible)
                    return

                if visible:
                    cmd_str = (
                        f"sed -i '/^NoDisplay=true/d' '{sys_path}' && "
                        f"sed -i '/^Hidden=true/d' '{sys_path}'"
                    )
                    cmd = ['pkexec', 'sh', '-c', cmd_str]
                else:
                    cmd_str = (
                        f"grep -q '^NoDisplay=' '{sys_path}' && "
                        f"sed -i 's/^NoDisplay=.*/NoDisplay=true/' '{sys_path}' || "
                        f"sed -i '/^\\[Desktop Entry\\]/a NoDisplay=true' '{sys_path}'"
                    )
                    cmd = ['pkexec', 'sh', '-c', cmd_str]

                res = subprocess.run(cmd, check=False)
                if res.returncode == 0:
                    logger.info(
                        f"[Settings] Successfully toggled desktop visibility for {filename} to {visible}")
                else:
                    logger.warning(
                        f"[Settings] pkexec failed or was cancelled (exit code {res.returncode})")
                    GLib.idle_add(self._revert_toggle, row, not visible)

            except Exception as e:
                logger.error(
                    f"[SettingsWindow] Toggle desktop file error: {e}")
                GLib.idle_add(self._revert_toggle, row, not visible)

        run_in_background(_task)

    def _revert_toggle(self, row, original_state):
        """Revert a desktop toggle switch if pkexec fails."""
        handler_id = None
        for r, fname, hid, _ignored in self.dt_toggles:
            if r == row:
                handler_id = hid
                break

        if handler_id:
            with row.handler_block(handler_id):
                row.set_active(original_state)
        else:
            row.set_active(original_state)

    def _get_gsettings_emergency(self):
        """Get emergency button setting via Gio.Settings."""
        return get_phosh_emergency_calls()

    def _set_gsettings_emergency(self, enabled):
        """Set emergency button setting via Gio.Settings."""
        set_phosh_emergency_calls(enabled)

    def _add_emergency_placeholder(self, btn):
        """Add a placeholder row for emergency number."""
        self._add_emergency_row("", "")

    def _add_emergency_row(self, name, number):
        """Add a configured emergency number row."""
        row = Adw.PreferencesRow()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        entry_name = Gtk.Entry(placeholder_text=_("Name (e.g. Mom)"))
        entry_name.set_text(name)
        entry_name.set_hexpand(True)

        entry_num = Gtk.Entry(placeholder_text=_("Number"))
        entry_num.set_text(number)
        entry_num.set_hexpand(True)

        btn_del = Gtk.Button(label=_("Delete"))
        btn_del.add_css_class("destructive-action")
        btn_del.set_hexpand(True)
        btn_del.connect("clicked", lambda b: self._remove_emergency_row(row))

        box.append(entry_name)
        box.append(entry_num)
        box.append(btn_del)

        row.set_child(box)
        self.grp_emerg_list.add(row)
        self.emergency_rows.append((row, entry_name, entry_num))

    def _remove_emergency_row(self, row):
        """Remove an emergency row."""
        self.grp_emerg_list.remove(row)
        self.emergency_rows = [x for x in self.emergency_rows if x[0] != row]

    def _add_reject_row(self, text):
        """Add an editable decline message row."""
        row = Adw.EntryRow(title=_("Decline Message"))
        row.set_text(text)

        btn_del = Gtk.Button(icon_name="user-trash-symbolic")
        btn_del.set_valign(Gtk.Align.CENTER)
        btn_del.add_css_class("flat")
        btn_del.add_css_class("circular")
        btn_del.connect("clicked", lambda b: self._remove_reject_row(row))
        row.add_suffix(btn_del)

        self.grp_reject_list.add(row)
        self.reject_rows.append(row)

    def _remove_reject_row(self, row):
        """Remove a decline message row."""
        self.grp_reject_list.remove(row)
        self.reject_rows = [r for r in self.reject_rows if r != row]

    def _on_default_ab_changed(self, row, param):
        """Handle default address book selection change."""
        idx = row.get_selected()
        if idx < 0 or idx >= len(self.sources_state):
            return

        for i, item in enumerate(self.sources_state):
            if i == idx:
                item['is_system_default'] = True
                item['enabled'] = True
            else:
                item['is_system_default'] = False

        self._build_sources_list(rebuild_dropdown=False)

    def _show_ab_info(self, title, msg):
        dialog = Adw.MessageDialog(heading=title, body=msg)
        dialog.set_transient_for(self)
        dialog.add_response("close", _("Close"))
        dialog.set_response_appearance(
            "close", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: GLib.idle_add(
            lambda: d.close() or False))
        dialog.present()

    def _build_sources_list(self, rebuild_dropdown=True):
        """Rebuild the address books list UI."""
        if rebuild_dropdown:
            self.row_default_ab.freeze_notify()
            n = self.model_default_ab.get_n_items()
            if n > 0:
                self.model_default_ab.splice(0, n, [])

            for item in self.sources_state:
                self.model_default_ab.append(item['name'])

            for i, item in enumerate(self.sources_state):
                if item['is_system_default']:
                    self.row_default_ab.set_selected(i)
                    break
            self.row_default_ab.thaw_notify()

        if (self.source_rows is not None):
            for r in self.source_rows:
                self.grp_contacts.remove(r)

        self.source_rows = []

        for i, item in enumerate(self.sources_state):
            item['rank'] = i

            row = Adw.ActionRow(title=item['name'])
            if item['is_system_default']:
                row.set_subtitle(_("System Default (Always Enabled)"))

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_valign(Gtk.Align.CENTER)

            if item.get('uid') == "system-address-book":
                btn_info_personal = Gtk.Button(
                    icon_name="dialog-information-symbolic")
                btn_info_personal.set_valign(Gtk.Align.CENTER)
                btn_info_personal.add_css_class("flat")
                btn_info_personal.add_css_class("circular")
                btn_info_personal.connect("clicked", lambda b: GLib.idle_add(lambda: self._show_ab_info(_("Personal Address Book"), _(
                    "This is the address book that will be synced to Andromeda if you choose to do so in System Settings.")) or False))
                box.append(btn_info_personal)
            elif item.get('name') == "Andromeda Contacts":
                btn_info_andro = Gtk.Button(
                    icon_name="dialog-information-symbolic")
                btn_info_andro.set_valign(Gtk.Align.CENTER)
                btn_info_andro.add_css_class("flat")
                btn_info_andro.add_css_class("circular")
                btn_info_andro.connect("clicked", lambda b: GLib.idle_add(lambda: self._show_ab_info(_("Andromeda Contacts"), _(
                    "This is the address book that will be synced from Andromeda if you choose to do so in System Settings.")) or False))
                box.append(btn_info_andro)

            btn_up = Gtk.Button(icon_name="go-up-symbolic")
            btn_up.add_css_class("flat")
            btn_up.set_sensitive(i > 0 and item['enabled'])
            btn_up.connect("clicked", lambda b,
                           idx=i: self._move_source(idx, -1))
            box.append(btn_up)

            btn_down = Gtk.Button(icon_name="go-down-symbolic")
            btn_down.add_css_class("flat")
            btn_down.set_sensitive(
                i < len(self.sources_state) - 1 and item['enabled'])
            btn_down.connect("clicked", lambda b,
                             idx=i: self._move_source(idx, 1))
            box.append(btn_down)

            sw = Gtk.Switch()
            sw.set_valign(Gtk.Align.CENTER)
            sw.set_active(item['enabled'])
            if item['is_system_default']:
                sw.set_sensitive(False)
                sw.set_active(True)

            sw.connect("notify::active", lambda w, p,
                       uid=item['uid']: self._toggle_source(uid, w.get_active()))

            box.append(sw)

            row.add_suffix(box)
            self.grp_contacts.add(row)
            self.source_rows.append(row)

    def _move_source(self, index, direction):
        """Move source up or down in the list."""
        new_index = index + direction
        if 0 <= new_index < len(self.sources_state):
            self.sources_state[index], self.sources_state[new_index] = self.sources_state[new_index], self.sources_state[index]
            self._build_sources_list(rebuild_dropdown=True)

    def _toggle_source(self, uid, active):
        """Update enabled state of a source."""
        for i, item in enumerate(self.sources_state):
            if item['uid'] == uid:
                item['enabled'] = active
                if not active:
                    self.sources_state.append(self.sources_state.pop(i))
                self._build_sources_list(rebuild_dropdown=True)
                break

    def on_save_clicked(self, btn):
        """Save settings and close."""
        raw_num = self.entry_own_num.get_text().strip()
        final_num = ""
        if raw_num:
            final_num = normalize_number(raw_num)
        self.main_window.gsettings_mgr.set_setting("own_number", final_num)

        cc = self.entry_country_code.get_text().strip()
        if cc:
            cc = cc.upper()
        self.main_window.gsettings_mgr.set_setting("default_country_code", cc)
        set_custom_region(cc)

        reject_msgs = [r.get_text().strip() for r in self.reject_rows if r.get_text().strip()]
        self.main_window.gsettings_mgr.set_reject_call_messages(reject_msgs)
        self.main_window.gsettings_mgr.set_setting(
            "reject_call_message", reject_msgs[0] if reject_msgs else "")

        self._set_gsettings_emergency(self.sw_emerg.get_active())
        emerg_list = []
        for row, ename, enum in self.emergency_rows:
            n_txt = ename.get_text().strip()
            num_txt = enum.get_text().strip()
            if num_txt:
                emerg_list.append(
                    {"name": n_txt or "Unknown", "number": num_txt})

        self.main_window.gsettings_mgr.set_emergency_numbers(emerg_list)

        self.main_window.gsettings_mgr.set_setting(
            "notification_override_repeated_calls_bypass", "true" if self.sw_repeated.get_active() else "false")

        self.main_window.gsettings_mgr.set_setting(
            "ringback_enabled", "true" if self.sw_rb_enable.get_active() else "false")

        if self.temp_ringback_file:
            self.main_window.gsettings_mgr.set_setting(
                "ringback_custom_file", self.temp_ringback_file)
        else:
            self.main_window.gsettings_mgr.set_setting(
                "ringback_custom_file", "")

        idx_act = self.row_uc_action.get_selected()
        action_val = "none"
        if idx_act >= 0:
            action_val = self.action_options[idx_act][0]
        self.main_window.gsettings_mgr.set_setting(
            "unknown_callers", action_val)

        self.main_window.gsettings_mgr.set_setting(
            "unknown_callers_search", "true" if self.sw_uc_search.get_active() else "false")

        idx = self.row_uc_engine.get_selected()
        engine = "duckduckgo"
        if idx >= 0:
            engine = self.engine_options[idx][0]
        self.main_window.gsettings_mgr.set_setting(
            "unknown_callers_engine", engine)
        self.main_window.gsettings_mgr.set_setting(
            "unknown_callers_custom_url", self.entry_uc_custom.get_text())

        if (self.sources_state is not None):
            self.eds.update_sources_config(self.sources_state)

            for item in self.sources_state:
                if item['is_system_default']:
                    self.eds.set_default_addressbook(item['uid'])
                    break

        self.overlay.add_toast(Adw.Toast.new(_("Settings Saved")))
        GLib.idle_add(lambda: self.close() or False)
