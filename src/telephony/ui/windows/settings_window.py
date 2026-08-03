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
from ...constants import CALL_VOLUME_MIN_PERCENT, CALL_VOLUME_MAX_PERCENT, CALL_VOLUME_DEFAULT_PERCENT
from ...constants import SHEET_CONTENT_WIDTH
from gi.repository import Gtk, Adw, GLib
from loguru import logger
from gettext import gettext as _

from ...backend.utils.phone_utils import normalize_number
from ...backend.utils.region_utils import set_custom_region
from ...backend.utils.system_utils import get_phosh_emergency_calls, set_phosh_emergency_calls
from .dnd_bypass_contacts_list_window import DndBypassContactsListWindow
from .custom_tone_list_window import CustomToneListWindow


from .advanced_settings_window import AdvancedSettingsWindow
from .network_services_window import NetworkServicesWindow
from ..widgets.common_widget import present_info_sheet, build_selector_row, set_selector_options


class SettingsWindow(Adw.Dialog):
    """Settings dialog holding every settings page in one navigation view.

    Presents as a bottom sheet on phone sized windows; subpages push onto
    the navigation view instead of spawning their own windows.
    """

    def __init__(self, main_window, eds_manager, ofono_manager):
        """Initialize Settings Window."""
        self._volume_commit_timer = None
        self.source_rows = None
        self.sources_state = None
        super().__init__(title=_("Settings"))
        self.connect("unmap", self._on_settings_unmap)

        self.main_window = main_window
        self.eds = eds_manager
        self._emergency_commit_timer = None

        self.set_content_width(SHEET_CONTENT_WIDTH)
        self.set_content_height(750)

        self.emergency_rows = []
        self.reject_rows = []

        self.temp_ringback_file = self.main_window.gsettings_mgr.get_setting(
            "ringback_custom_file")

        self.overlay = Adw.ToastOverlay()
        self.set_child(self.overlay)

        self.nav_view = Adw.NavigationView()
        self.overlay.set_child(self.nav_view)

        root_view = Adw.ToolbarView()

        root_view.add_top_bar(Adw.HeaderBar())

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        root_view.set_content(scroll)
        self.nav_view.add(Adw.NavigationPage(title=_("Settings"), tag="settings-root", child=root_view))

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll.set_child(content_box)

        page = Adw.PreferencesPage()
        content_box.append(page)

        grp_cats = Adw.PreferencesGroup()
        page.add(grp_cats)
        grp_cats.add(self._nav_row(_("Calls"), _("Numbers, volume and ringback"),
                                   lambda: self._push_category(_("Calls"), self._build_calls_page),
                                   icon="call-start-symbolic"))
        grp_cats.add(self._nav_row(_("Messages"), _("Quick responses and delivery reports"),
                                   lambda: self._push_category(_("Messages"), self._build_messages_page),
                                   icon="mail-unread-symbolic"))
        grp_cats.add(self._nav_row(_("Contacts"), _("Address books and duplicates"),
                                   lambda: self._push_category(_("Contacts"), self._build_contacts_page),
                                   icon="avatar-default-symbolic"))
        grp_cats.add(self._nav_row(_("Notifications"), _("Exceptions, tones and bypass"),
                                   lambda: self._push_category(_("Notifications"), self._build_notifications_page),
                                   icon="audio-volume-high-symbolic"))
        grp_cats.add(self._nav_row(_("Unknown Callers"), _("Screening and lookup"),
                                   lambda: self._push_category(_("Unknown Callers"), self._build_unknown_callers_page),
                                   icon="dialog-question-symbolic"))
        grp_cats.add(self._nav_row(_("Network Services"), _("Forwarding, waiting and barring"),
                                   lambda: self._open_network_services(None),
                                   icon="network-cellular-signal-good-symbolic"))
        grp_cats.add(self._nav_row(_("Advanced Settings"), None,
                                   lambda: self._open_modem_settings(None),
                                   icon="emblem-system-symbolic"))

    def _push_category(self, title, build):
        """Push a settings category page built fresh from current state."""
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
        scroll = Gtk.ScrolledWindow(vexpand=True)
        page = Adw.PreferencesPage()
        scroll.set_child(page)
        view.set_content(scroll)
        build(page)
        self.nav_view.push(Adw.NavigationPage(title=title, child=view))

    def _build_calls_page(self, page):
        """Build the calls category page."""
        self.emergency_rows = []
        grp_sim = Adw.PreferencesGroup(title=_("SIM Settings"))
        page.add(grp_sim)

        self.entry_own_num = Adw.EntryRow(title=_("My Number"))
        self.entry_own_num.set_title(_("My Number (International Format)"))
        saved_num = self.main_window.gsettings_mgr.get_setting("own_number")
        self.entry_own_num.set_text(saved_num if saved_num else "")
        self.entry_own_num.set_show_apply_button(True)
        self.entry_own_num.connect("apply", self._on_own_number_apply)
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
        self.entry_country_code.set_show_apply_button(True)
        self.entry_country_code.connect("apply", self._on_country_code_apply)
        grp_sim.add(self.entry_country_code)

        self.entry_voicemail = Adw.EntryRow(title=_("Voicemail Number"))
        saved_vm = self.main_window.gsettings_mgr.get_setting("voicemail_number")
        if not saved_vm and self.main_window.ofono:
            saved_vm = self.main_window.ofono.voicemail_mailbox
        self.entry_voicemail.set_text(saved_vm if saved_vm else "")
        self.entry_voicemail.set_show_apply_button(True)
        self.entry_voicemail.connect("apply", self._on_voicemail_apply)
        grp_sim.add(self.entry_voicemail)

        self.grp_call_volume = Adw.PreferencesGroup(title=_("Call Volume"))
        btn_info_vol = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_info_vol.set_valign(Gtk.Align.CENTER)
        btn_info_vol.add_css_class("flat")
        btn_info_vol.add_css_class("circular")
        btn_info_vol.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_call_volume_info(b) or False))
        self.grp_call_volume.set_header_suffix(btn_info_vol)
        page.add(self.grp_call_volume)

        saved_levels = self.main_window.gsettings_mgr.get_call_volume_levels()

        self.volume_scales = {}
        route_titles = (("earpiece", _("Earpiece")),
                        ("speaker", _("Speaker")),
                        ("wired", _("Wired Headset")),
                        ("bluetooth", _("Bluetooth")))
        for route_id, title in route_titles:
            row = Adw.ActionRow(title=title)
            scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, CALL_VOLUME_MIN_PERCENT, CALL_VOLUME_MAX_PERCENT, 10)
            scale.set_value(saved_levels.get(route_id, CALL_VOLUME_DEFAULT_PERCENT))
            scale.set_hexpand(True)
            scale.set_size_request(180, -1)
            scale.set_draw_value(True)
            scale.set_value_pos(Gtk.PositionType.RIGHT)
            scale.connect("value-changed", self._on_volume_scale_changed)
            row.add_suffix(scale)
            self.grp_call_volume.add(row)
            self.volume_scales[route_id] = scale

        grp_rb = Adw.PreferencesGroup(title=_("Ringback Tone"))
        page.add(grp_rb)

        self.sw_rb_enable = Adw.SwitchRow(title=_("Enable Local Ringback"))
        enabled_str = self.main_window.gsettings_mgr.get_setting(
            "ringback_enabled")
        self.sw_rb_enable.set_active(enabled_str == "true")
        self.sw_rb_enable.connect("notify::active", lambda w, p: self.main_window.gsettings_mgr.set_setting(
            "ringback_enabled", "true" if w.get_active() else "false"))

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

        grp_emerg_toggle = Adw.PreferencesGroup(
            title=_("Lockscreen Emergency"))
        page.add(grp_emerg_toggle)

        self.sw_emerg = Adw.SwitchRow(title=_("Show Emergency Button"))
        self.sw_emerg.set_active(self._get_gsettings_emergency())
        self.sw_emerg.connect("notify::active",
                              lambda w, p: self._set_gsettings_emergency(w.get_active()))
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

    def _build_messages_page(self, page):
        """Build the messages category page."""
        self.reject_rows = []
        grp_msg = Adw.PreferencesGroup(title=_("Messaging"))
        page.add(grp_msg)

        self.sw_delivery = Adw.SwitchRow(title=_("Request Delivery Reports"))
        self.sw_delivery.set_active(
            self.main_window.gsettings_mgr.get_setting("delivery_reports") == "true")
        btn_dr_info = Gtk.Button(icon_name="dialog-information-symbolic", valign=Gtk.Align.CENTER)
        btn_dr_info.add_css_class("flat")
        btn_dr_info.add_css_class("circular")
        btn_dr_info.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_delivery_reports_info(b) or False))
        self.sw_delivery.add_suffix(btn_dr_info)
        self.sw_delivery.connect("notify::active", self._on_delivery_reports_toggled)
        grp_msg.add(self.sw_delivery)

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

    def _build_contacts_page(self, page):
        """Build the contacts category page."""
        self.source_rows = None
        self.grp_contacts = Adw.PreferencesGroup(title=_("Address Books"))

        btn_info_contacts = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_info_contacts.add_css_class("flat")
        btn_info_contacts.add_css_class("circular")
        btn_info_contacts.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_addressbook_info(b) or False))

        try:
            self.grp_contacts.set_header_suffix(btn_info_contacts)
        except AttributeError as e:
            logger.debug(f"[Settings] set_header_suffix unavailable, using row fallback: {e}")
            row_info = Adw.ActionRow(title=_("Address Book Info"))
            row_info.add_suffix(btn_info_contacts)
            self.grp_contacts.add(row_info)

        page.add(self.grp_contacts)

        self.row_default_ab = build_selector_row(
            _("Default Address Book"), self._on_default_ab_selected)
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

        self.row_add_ab = Adw.ExpanderRow(title=_("Add Local Address Book"))
        self.entry_new_ab = Adw.EntryRow(title=_("Name"))
        btn_add_ab = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        btn_add_ab.add_css_class("flat")
        btn_add_ab.add_css_class("circular")
        btn_add_ab.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._on_add_addressbook() or False))
        self.entry_new_ab.add_suffix(btn_add_ab)
        self.entry_new_ab.connect("entry-activated", lambda r: GLib.idle_add(
            lambda: self._on_add_addressbook() or False))
        self.row_add_ab.add_row(self.entry_new_ab)
        self.grp_contacts.add(self.row_add_ab)

        self.sources_state = self.eds.get_sources_info()
        self._build_sources_list(rebuild_dropdown=True)

    def _build_notifications_page(self, page):
        """Build the notifications category page."""
        grp_notif = Adw.PreferencesGroup(title=_("Notification Exceptions"))
        page.add(grp_notif)

        self.sw_repeated = Adw.SwitchRow(title=_("Repeated Calls Bypass"))
        self.sw_repeated.set_subtitle(
            _("If the same number calls 3 times in 5 minutes, force max volume."))
        self.sw_repeated.set_active(self.main_window.gsettings_mgr.get_setting(
            "notification_override_repeated_calls_bypass") == "true")
        self.sw_repeated.connect("notify::active", lambda w, p: self.main_window.gsettings_mgr.set_setting(
            "notification_override_repeated_calls_bypass", "true" if w.get_active() else "false"))

        btn_info_rep = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_info_rep.set_valign(Gtk.Align.CENTER)
        btn_info_rep.add_css_class("flat")
        btn_info_rep.add_css_class("circular")
        btn_info_rep.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_repeated_info(b) or False))
        self.sw_repeated.add_suffix(btn_info_rep)

        grp_notif.add(self.sw_repeated)

        row_prio = self._nav_row(_("Notification Overrides"),
                                 _("Manage contacts that always play sound"),
                                 lambda: self._open_priority_window(None))
        btn_info_prio = Gtk.Button(icon_name="dialog-information-symbolic")
        btn_info_prio.set_valign(Gtk.Align.CENTER)
        btn_info_prio.add_css_class("flat")
        btn_info_prio.add_css_class("circular")
        btn_info_prio.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_overrides_info(b) or False))
        row_prio.add_suffix(btn_info_prio)
        grp_notif.add(row_prio)

        grp_notif.add(self._nav_row(_("Individual SMS Notifications"),
                                    _("Set custom notification sounds for specific contacts"),
                                    lambda: self._open_custom_tone_window("sms")))

        grp_notif.add(self._nav_row(_("Individual Ringtones"),
                                    _("Set custom ringtones for specific contacts"),
                                    lambda: self._open_custom_tone_window("ringtone")))

    def _nav_row(self, title, subtitle, callback, icon=None):
        """Build an activatable navigation row with a chevron."""
        row = Adw.ActionRow(title=title, activatable=True)
        if subtitle:
            row.set_subtitle(subtitle)
        if icon:
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", lambda r: GLib.idle_add(lambda: callback() or False))
        return row

    def _show_country_code_info(self, btn):
        """Show information about country code setting."""
        body_text = _("The Default Country Code is used to format numbers that don't include an international prefix (e.g., +358).\n\nIf left empty, the system tries to detect it automatically from your SIM card or network.\n\nYou can manually override it here by entering a 2-letter ISO region code (e.g. US, FI, DE) or a calling code (e.g. +358, 358, +1, 1).")

        present_info_sheet(self, _("Default Country Code"), body_text)

    def _clear_ringback_file(self):
        """Clear the custom ringback file to use default."""
        self.temp_ringback_file = ""
        self.lbl_rb_path.set_text(_("System Default"))
        self.main_window.gsettings_mgr.set_setting("ringback_custom_file", "")

    def _show_ringback_info(self, btn):
        """Show information about ringback tones."""
        body_text = _("The ringback tone is the sound played (toot-toot-toot) while waiting for an "
                      "outgoing call to be answered. Some carriers don't generate it so we have "
                      "option to generate it manually here. \n\n"
                      "You can select your own music file, or clear it to use the standard system tone.")

        present_info_sheet(self, _("Ringback Tone"), body_text)

    def _open_audio_picker(self, btn):
        """Open file picker for custom ringback tone."""
        dialog = Gtk.FileChooserNative(
            title=_("Select Ringback Tone"),
            transient_for=self.get_root(),
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
                self.main_window.gsettings_mgr.set_setting("ringback_custom_file", path)
        GLib.idle_add(lambda: dialog.destroy() or False)

    def _open_modem_settings(self, btn):
        """Push the advanced settings page."""
        self.nav_view.push(AdvancedSettingsWindow(self))

    def _open_network_services(self, btn):
        """Push the network services page."""
        self.nav_view.push(NetworkServicesWindow(self))

    def _show_addressbook_info(self, btn):
        """Show information about address book priority."""
        body_text = _("Select the address books to use with Telephony and arrange them in order of priority. The topmost address book has the highest priority and will be preferred if a number is found in multiple address books. You can use the Duplicate Resolver toggle below to help manage duplicates, but even if disabled, we encourage enabling it for a better experience.")

        present_info_sheet(self, _("Address Books"), body_text)

    def _show_repeated_info(self, btn):
        """Show info about repeated calls bypass."""
        present_info_sheet(self, _("Repeated Calls Bypass"), _("When enabled, if the same phone number calls you 3 times within 5 minutes, the third call (and subsequent ones within the window) will force the phone to ring at maximum volume, bypassing Silent or Do Not Disturb modes."))

    def _show_overrides_info(self, btn):
        """Show info about notification overrides."""
        present_info_sheet(self, _("Notification Overrides"), _("Choose contacts that will always play notifications at full volume, even when the phone is in Silent or Do Not Disturb mode."))

    def _open_priority_window(self, btn):
        """Push the priority contacts management page."""
        self.nav_view.push(DndBypassContactsListWindow(
            self, self.main_window.gsettings_mgr, self.eds))

    def _open_custom_tone_window(self, mode):
        """Push the custom tone management page."""
        self.nav_view.push(CustomToneListWindow(
            self, self.main_window.gsettings_mgr, self.eds, mode=mode))

    def _build_unknown_callers_page(self, page):
        """Build the unknown callers category page."""
        grp_uc = Adw.PreferencesGroup(title=_("Unknown Callers"))
        page.add(grp_uc)

        grp_uc.set_description(_("Applies also to hidden numbers."))
        self.row_uc_action = build_selector_row(_("Unknown Callers Action"), self._on_uc_action_selected)
        self.action_options = [
            ("none", _("Do Nothing")),
            ("block", _("Block Completely")),
            ("hide", _("Silence and Hide UI")),
            ("silence", _("Silence Only"))
        ]
        grp_uc.add(self.row_uc_action)

        self.sw_uc_search = Adw.SwitchRow(title=_(
            "Add Search button to InCall window and Call History for unknown callers"))
        self.sw_uc_search.set_title_lines(0)
        grp_uc.add(self.sw_uc_search)

        self.row_uc_engine = build_selector_row(
            _("Search Engine"), self._on_uc_engine_selected)
        self.engine_options = [
            ("duckduckgo", "DuckDuckGo"),
            ("startpage", "Startpage"),
            ("custom", _("Custom URL"))
        ]
        self.entry_uc_custom = Adw.EntryRow(title=_("Custom URL"))
        self.entry_uc_custom.set_show_apply_button(True)
        self.entry_uc_custom.connect("apply", lambda r: self.main_window.gsettings_mgr.set_setting(
            "unknown_callers_custom_url", r.get_text()))

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
            self.main_window.gsettings_mgr.set_setting(
                "unknown_callers_search", "true" if w.get_active() else "false")
            self._update_uc_ui()
        self.sw_uc_search.connect("notify::active", _on_search_toggle)


        uc_action = self.main_window.gsettings_mgr.get_setting(
            "unknown_callers") or "none"
        action_idx = next(
            (i for i, (k, act_name) in enumerate(self.action_options) if k == uc_action), 0)
        set_selector_options(self.row_uc_action,
                                   [name for _key, name in self.action_options], action_idx)

        self.sw_uc_search.set_active(self.main_window.gsettings_mgr.get_setting(
            "unknown_callers_search") == "true")

        engine = self.main_window.gsettings_mgr.get_setting(
            "unknown_callers_engine") or "duckduckgo"
        engine_idx = next(
            (i for i, (k, eng_name) in enumerate(self.engine_options) if k == engine), 0)
        set_selector_options(self.row_uc_engine,
                                   [name for _key, name in self.engine_options], engine_idx)

        custom_url = self.main_window.gsettings_mgr.get_setting(
            "unknown_callers_custom_url") or ""
        self.entry_uc_custom.set_text(custom_url)

        self._update_uc_ui()

    def _show_custom_url_info(self, btn):
        body_text = _(
            "Enter the URL you want to use for searching unknown numbers. Include '{number}' in the URL where the caller's phone number should go.\n\nFor example: https://mysearch.com/?q={number}\n\nWhen you tap the search button during a call, we will replace '{number}' with the actual phone number and open it in your default browser.")

        present_info_sheet(self, _("Custom Search URL"), body_text)

    def _on_uc_action_selected(self, idx):
        """Persist the unknown callers action immediately."""
        if 0 <= idx < len(self.action_options):
            self.main_window.gsettings_mgr.set_setting(
                "unknown_callers", self.action_options[idx][0])

    def _on_uc_engine_selected(self, idx):
        """Persist the search engine choice immediately."""
        if 0 <= idx < len(self.engine_options):
            self.main_window.gsettings_mgr.set_setting(
                "unknown_callers_engine", self.engine_options[idx][0])
        self._update_uc_ui()

    def _persist_sources(self):
        """Persist address book order, enablement and default in the background."""
        state = [dict(item) for item in self.sources_state]
        run_in_background(self.eds.update_sources_config, state)
        default = next((item for item in state if item.get('is_system_default')), None)
        if default:
            run_in_background(self.eds.set_default_addressbook, default['uid'])

    def _update_uc_ui(self):
        search_active = self.sw_uc_search.get_active()
        self.row_uc_engine.set_visible(search_active)
        idx = self.row_uc_engine._selected_index
        if search_active and idx >= 0 and self.engine_options[idx][0] == "custom":
            self.entry_uc_custom.set_visible(True)
        else:
            self.entry_uc_custom.set_visible(False)

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
        entry_name.connect("changed", lambda e: self._schedule_emergency_persist())

        entry_num = Gtk.Entry(placeholder_text=_("Number"))
        entry_num.set_text(number)
        entry_num.set_hexpand(True)
        entry_num.connect("changed", lambda e: self._schedule_emergency_persist())

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
        self._persist_emergency_numbers()

    def _on_volume_scale_changed(self, scale):
        """Snap drags to steps of ten and debounce persisting the values."""
        snapped = round(scale.get_value() / 10) * 10
        if int(scale.get_value()) != snapped:
            scale.set_value(snapped)
            return

        if self._volume_commit_timer is not None:
            GLib.source_remove(self._volume_commit_timer)
        self._volume_commit_timer = GLib.timeout_add(200, self._commit_volume_levels)

    def _on_settings_unmap(self, widget):
        """Flush pending debounced commits so closing quickly cannot drop them."""
        if self._volume_commit_timer is not None:
            GLib.source_remove(self._volume_commit_timer)
            self._volume_commit_timer = None
            self._commit_volume_levels()
        if self._emergency_commit_timer is not None:
            GLib.source_remove(self._emergency_commit_timer)
            self._emergency_commit_timer = None
            self._persist_emergency_numbers()

    def _commit_volume_levels(self):
        """Write the current slider values to settings."""
        self._volume_commit_timer = None
        volume_levels = {route: max(CALL_VOLUME_MIN_PERCENT, min(CALL_VOLUME_MAX_PERCENT, int(scale.get_value())))
                         for route, scale in self.volume_scales.items()}
        self.main_window.gsettings_mgr.set_call_volume_levels(volume_levels)
        return False

    def _show_call_volume_info(self, btn):
        """Show info about base call volume levels."""
        present_info_sheet(self, _("Call Volume"), _("This is the call volume for each output. The level applies automatically when a call connects and whenever the output changes during a call, and slider changes are heard live. The hardware applies levels in coarse steps, and the earpiece never goes fully silent. The Bluetooth level is stored for upcoming routing support."))

    def _add_reject_row(self, text):
        """Add an editable decline message row."""
        row = Adw.EntryRow(title=_("Decline Message"))
        row.set_text(text)
        row.set_show_apply_button(True)
        row.connect("apply", lambda r: self._persist_reject_messages())

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
        self._persist_reject_messages()

    def _show_delivery_reports_info(self, btn):
        """Explain what delivery reports do and how far to trust them."""
        body = _("When enabled, the network is asked to confirm when your "
                 "text and multimedia messages reach the recipient's phone.\n\n"
                 "A confirmation is reliable when it arrives, but many "
                 "carriers and automated senders never produce one, so a "
                 "message without a confirmation may still have been "
                 "delivered. Delivered never means read.")
        present_info_sheet(self, _("Request Delivery Reports"), body)

    def _on_delivery_reports_toggled(self, row, _pspec):
        """Persist and apply the delivery report preference immediately."""
        enabled = row.get_active()
        self.main_window.gsettings_mgr.set_setting(
            "delivery_reports", "true" if enabled else "false")
        if self.main_window.ofono:
            run_in_background(self.main_window.ofono.set_delivery_reports, enabled)
        app = self.main_window.get_application()
        if app and app.mms:
            run_in_background(app.mms.set_delivery_reports, enabled)

    def _persist_reject_messages(self):
        """Persist the decline messages immediately."""
        reject_msgs = [r.get_text().strip() for r in self.reject_rows if r.get_text().strip()]
        self.main_window.gsettings_mgr.set_reject_call_messages(reject_msgs)
        self.main_window.gsettings_mgr.set_setting(
            "reject_call_message", reject_msgs[0] if reject_msgs else "")

    def _on_own_number_apply(self, row):
        """Persist the own number immediately."""
        raw = row.get_text().strip()
        self.main_window.gsettings_mgr.set_setting(
            "own_number", normalize_number(raw) if raw else "")

    def _on_country_code_apply(self, row):
        """Persist the default country code immediately."""
        cc = row.get_text().strip().upper()
        row.set_text(cc)
        self.main_window.gsettings_mgr.set_setting("default_country_code", cc)
        set_custom_region(cc)

    def _on_voicemail_apply(self, row):
        """Persist the voicemail number immediately."""
        self.main_window.gsettings_mgr.set_setting(
            "voicemail_number", row.get_text().strip())
        app = self.main_window.get_application()
        if app:
            app.ensure_voicemail_contact()

    def _schedule_emergency_persist(self):
        """Debounce persisting the emergency list while typing."""
        if self._emergency_commit_timer is not None:
            GLib.source_remove(self._emergency_commit_timer)
        self._emergency_commit_timer = GLib.timeout_add(500, self._persist_emergency_numbers)

    def _persist_emergency_numbers(self):
        """Write the configured emergency numbers to settings."""
        self._emergency_commit_timer = None
        emerg_list = []
        for row, ename, enum in self.emergency_rows:
            num_txt = enum.get_text().strip()
            if num_txt:
                emerg_list.append({"name": ename.get_text().strip() or "Unknown", "number": num_txt})
        self.main_window.gsettings_mgr.set_emergency_numbers(emerg_list)
        return False

    def _on_default_ab_selected(self, idx):
        """Handle default address book selection change."""
        if idx < 0 or idx >= len(self.sources_state):
            return

        for i, item in enumerate(self.sources_state):
            if i == idx:
                item['is_system_default'] = True
                item['enabled'] = True
            else:
                item['is_system_default'] = False

        self._build_sources_list(rebuild_dropdown=False)
        self._persist_sources()

    def _source_row_subtitle(self, item):
        """Build the subtitle for an address book row."""
        if item['is_system_default']:
            return _("System Default (Always Enabled)")
        if item.get('name') == "Andromeda Contacts":
            return _("Synced from Andromeda")

        status_labels = {
            "connected": _("Connected"),
            "connecting": _("Connecting..."),
            "disconnected": _("Not connected"),
            "awaiting-credentials": _("Login required"),
            "ssl-failed": _("Connection failed (SSL)"),
        }
        bits = []
        if item.get('account'):
            bits.append(item['account'])
        status_label = status_labels.get(item.get('status'))
        if status_label:
            bits.append(status_label)
        return " · ".join(bits)

    def _on_add_addressbook(self):
        """Create a local address book from the inline name entry."""
        name = self.entry_new_ab.get_text().strip()
        if not name:
            return
        self.entry_new_ab.set_text("")
        self.row_add_ab.set_expanded(False)
        run_in_background(self.eds.create_local_addressbook, name,
                          on_complete=lambda ok: self._on_addressbook_created(ok, name))

    def _on_addressbook_created(self, success, name):
        """Refresh the sources list after creating an address book."""
        if not success:
            self.main_window.notify_error(_("Could not create address book"))
            return
        self.main_window.notify_success(_("Address book '{name}' created").format(name=name))
        self._reload_sources_ui()

    def _confirm_delete_addressbook(self, uid, name):
        """Confirm and delete a local address book."""
        dialog = Adw.AlertDialog(
            heading=_("Delete Address Book"),
            body=_("Are you sure you want to permanently delete the '{name}' address book?").format(name=name))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_resp(d, resp):
            if resp != "delete":
                return
            run_in_background(self.eds.delete_addressbook, uid,
                              on_complete=self._on_addressbook_deleted)

        dialog.connect("response", on_resp)
        dialog.present(self)

    def _on_addressbook_deleted(self, success):
        """Refresh the sources list after an address book removal."""
        if not success:
            self.main_window.notify_error(_("Failed to delete Address Book"))
            return
        self.main_window.notify_success(_("Address Book Deleted"))
        self._reload_sources_ui()

    def _reload_sources_ui(self):
        """Re-fetch the sources info off the main thread and rebuild the list."""
        def done(info):
            self.sources_state = info
            self._build_sources_list(rebuild_dropdown=True)

        run_in_background(self.eds.get_sources_info, on_complete=done)

    def _show_ab_info(self, title, msg):
        dialog = Adw.AlertDialog(heading=title, body=msg)
        dialog.add_response("close", _("Close"))
        dialog.set_response_appearance(
            "close", Adw.ResponseAppearance.SUGGESTED)
        dialog.present(self)

    def _build_sources_list(self, rebuild_dropdown=True):
        """Rebuild the address books list UI."""
        if rebuild_dropdown:
            default_idx = next(
                (i for i, item in enumerate(self.sources_state) if item['is_system_default']), 0)
            set_selector_options(
                self.row_default_ab, [item['name'] for item in self.sources_state], default_idx)

        if (self.source_rows is not None):
            for r in self.source_rows:
                self.grp_contacts.remove(r)

        self.source_rows = []

        for i, item in enumerate(self.sources_state):
            item['rank'] = i

            row = Adw.ActionRow(title=item['name'])
            subtitle = self._source_row_subtitle(item)
            if subtitle:
                row.set_subtitle(subtitle)

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

            if item.get('status') in ("ssl-failed", "awaiting-credentials"):
                warn = Gtk.Image(icon_name="dialog-warning-symbolic")
                warn.set_valign(Gtk.Align.CENTER)
                box.append(warn)

            is_protected = item.get('uid') == "system-address-book" or item.get('name') == "Andromeda Contacts"
            if item.get('removable') and not is_protected:
                btn_del = Gtk.Button(icon_name="user-trash-symbolic")
                btn_del.set_valign(Gtk.Align.CENTER)
                btn_del.add_css_class("flat")
                btn_del.add_css_class("circular")
                btn_del.connect("clicked", lambda b, uid=item['uid'], name=item['name']: GLib.idle_add(
                    lambda: self._confirm_delete_addressbook(uid, name) or False))
                box.append(btn_del)

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
            self._persist_sources()

    def _toggle_source(self, uid, active):
        """Update enabled state of a source."""
        for i, item in enumerate(self.sources_state):
            if item['uid'] == uid:
                item['enabled'] = active
                if not active:
                    self.sources_state.append(self.sources_state.pop(i))
                self._build_sources_list(rebuild_dropdown=True)
                self._persist_sources()
                break

