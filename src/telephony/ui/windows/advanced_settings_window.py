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

import subprocess
from .trusted_actions_list_window import TrustedActionsListWindow
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from gettext import gettext as _

from ...backend.utils.thread_utils import run_in_background
from ...constants import MMS_SIZE_LIMIT_DEFAULT_KB
from ..widgets.common_widget import present_info_sheet, build_selector_row, set_selector_options


class AdvancedSettingsWindow(Adw.NavigationPage):
    """Advanced settings subpage of the settings navigation."""

    def __init__(self, parent):
        """Initialize the advanced settings page."""
        super().__init__(title=_("Advanced Settings"))
        self.parent_win = parent

        view = Adw.ToolbarView()
        self.set_child(view)
        view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))

        self.overlay = Adw.ToastOverlay()
        view.set_content(self.overlay)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self.content_box)
        self.overlay.set_child(scroll)

        self._build_ui()

    def _show_toast(self, message, is_error=False):
        """Display a toast message."""
        toast = Adw.Toast.new(message)
        if is_error:
            toast.set_priority(Adw.ToastPriority.HIGH)
        self.overlay.add_toast(toast)

    def _build_ui(self):
        """Construct the settings UI."""
        self.page = Adw.PreferencesPage()
        self.content_box.append(self.page)

        grp_actions = Adw.PreferencesGroup(title=_("Secret SMS Triggers"))
        self.page.add(grp_actions)

        grp_actions.add(self._nav_row(_("Find my Telephony"), _("Location by trusted SMS"),
                                      lambda: self._open_action_window("trusted_sms_location_request")))
        grp_actions.add(self._nav_row(_("Trusted Callback"), _("Silent callback trigger"),
                                      lambda: self._open_action_window("trusted_sms_silent_callback")))
        grp_actions.add(self._nav_row(_("SMS Relay"), _("Forward messages"),
                                      lambda: self._open_action_window("trusted_sms_relay")))
        grp_actions.add(self._nav_row(_("SMS tmate"), _("Remote shell access"),
                                      lambda: self._open_action_window("trusted_sms_ssh_access")))
        grp_actions.add(self._nav_row(_("Lock Device"), _("Lock by trusted SMS"),
                                      lambda: self._open_action_window("trusted_sms_lock_device"),
                                      destructive=True))

        grp_calls = Adw.PreferencesGroup(title=_("Experimental Call Features"))
        self.page.add(grp_calls)

        grp_calls.add(self._experimental_row(
            _("Allow Conference Calls"), "allow_conference_calls",
            self._show_conference_info))
        grp_calls.add(self._experimental_row(
            _("Allow Call Transfer"), "allow_call_transfer",
            self._show_transfer_info))

        grp_restart = Adw.PreferencesGroup()
        self.page.add(grp_restart)

        row_auto = Adw.SwitchRow(title=_("Automatic Modem Recovery"))
        row_auto.set_active(self.parent_win.main_window.gsettings_mgr.get_setting("automatic_modem_recovery") == "true")
        row_auto.connect("notify::active", self._on_auto_recovery_toggled)
        btn_auto_info = Gtk.Button(icon_name="dialog-information-symbolic", valign=Gtk.Align.CENTER)
        btn_auto_info.add_css_class("flat")
        btn_auto_info.add_css_class("circular")
        btn_auto_info.connect("clicked", lambda b: GLib.idle_add(lambda: self._show_auto_recovery_info(b) or False))
        row_auto.add_suffix(btn_auto_info)
        grp_restart.add(row_auto)

        grp_restart.add(self._nav_row(_("Modem Recovery"), None,
                                      lambda: self._on_modem_recovery(None),
                                      destructive=True))

        grp_messaging = Adw.PreferencesGroup(title=_("Messaging"))
        self.page.add(grp_messaging)

        self.mms_limit_values = ["100", "300", "600", "900", "1024", "2048", "3072", "4096", "5120"]
        mms_limit_labels = ["100 kB", "300 kB", "600 kB", "900 kB", "1 MB", "2 MB", "3 MB", "4 MB", "5 MB"]
        self.row_mms_limit = build_selector_row(
            _("MMS Size Limit"), self._on_mms_limit_selected)
        saved_limit = self.parent_win.main_window.gsettings_mgr.get_setting("mms_size_limit")
        if saved_limit not in self.mms_limit_values:
            saved_limit = MMS_SIZE_LIMIT_DEFAULT_KB
        set_selector_options(self.row_mms_limit, mms_limit_labels,
                             self.mms_limit_values.index(saved_limit))
        btn_info_mms = Gtk.Button(icon_name="dialog-information-symbolic", valign=Gtk.Align.CENTER)
        btn_info_mms.add_css_class("flat")
        btn_info_mms.add_css_class("circular")
        btn_info_mms.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._show_mms_size_info(b) or False))
        grp_messaging.set_header_suffix(btn_info_mms)
        grp_messaging.add(self.row_mms_limit)

        grp_data = Adw.PreferencesGroup()
        self.page.add(grp_data)

        grp_data.add(self._nav_row(_("Data Management"), _("Export, import and reset"),
                                   lambda: self._on_data_management(None),
                                   destructive=True))

    def _nav_row(self, title, subtitle, callback, destructive=False):
        """Build an activatable navigation row with a chevron."""
        row = Adw.ActionRow(title=title, activatable=True)
        if subtitle:
            row.set_subtitle(subtitle)
        if destructive:
            row.add_css_class("error")
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", lambda r: GLib.idle_add(lambda: callback() or False))
        return row

    def _on_data_management(self, btn):
        """Push the data management page."""
        from .data_management_window import DataManagementDialog
        dm = DataManagementDialog(self.parent_win.main_window)
        self.get_ancestor(Adw.NavigationView).push(dm.build_page())

    def _open_action_window(self, mode):
        """Open the trusted action window after polkit authentication."""
        def done(returncode):
            if returncode != 0:
                self._show_toast(_("Authentication cancelled"), True)
                return
            page = TrustedActionsListWindow(self, self.parent_win.main_window.gsettings_mgr, self.parent_win.eds, mode=mode)
            self.get_ancestor(Adw.NavigationView).push(page)

        def failed(error):
            self._show_toast(_("Auth error: {e}").format(e=error), True)

        run_in_background(lambda: subprocess.run(["pkexec", "true"]).returncode,
                          on_complete=done, on_error=failed)

    def _show_mms_size_info(self, btn):
        """Show information about the MMS size limit."""
        body_text = _("Carriers limit how large a multimedia message can be. "
                      "Images and videos you attach are automatically compressed "
                      "to fit under this limit before sending.\n\n"
                      "600 kB is a safe default for most carriers. Pick a smaller "
                      "value if your messages fail to send, or a larger one if you "
                      "know your carrier allows it.")

        present_info_sheet(self, _("MMS Size Limit"), body_text)

    def _on_mms_limit_selected(self, idx):
        """Persist the selected MMS size limit."""
        if idx < 0 or idx >= len(self.mms_limit_values):
            return
        self.parent_win.main_window.gsettings_mgr.set_setting("mms_size_limit", self.mms_limit_values[idx])

    def _experimental_row(self, title, setting_key, info_handler):
        """Build a switch for a feature the carrier may not support."""
        row = Adw.SwitchRow(title=title)
        row.set_active(self.parent_win.main_window.gsettings_mgr.get_setting(setting_key) == "true")
        row.connect("notify::active", lambda w, _p: self.parent_win.main_window.gsettings_mgr.set_setting(
            setting_key, "true" if w.get_active() else "false"))
        btn_info = Gtk.Button(icon_name="dialog-information-symbolic", valign=Gtk.Align.CENTER)
        btn_info.add_css_class("flat")
        btn_info.add_css_class("circular")
        btn_info.connect("clicked", lambda b: GLib.idle_add(lambda: info_handler(b) or False))
        row.add_suffix(btn_info)
        return row

    def _show_conference_info(self, btn):
        """Explain how far conference calls can be trusted."""
        present_info_sheet(self, _("Allow Conference Calls"), _(
            "Merging calls into a conference is up to the carrier, and in "
            "our testing the results varied from working to dropping the "
            "calls. Turn this on to try it on your own subscription. It "
            "cannot be officially supported, so leave it off if you would "
            "rather your calls behaved predictably."))

    def _show_transfer_info(self, btn):
        """Explain how far call transfer can be trusted."""
        present_info_sheet(self, _("Allow Call Transfer"), _(
            "Transferring two calls to each other needs a service the "
            "carrier has to provide, and in our testing many carriers "
            "either refuse it or end the calls instead. Turn this on to "
            "try it on your own subscription. It cannot be officially "
            "supported, so leave it off if you would rather your calls "
            "behaved predictably."))

    def _on_auto_recovery_toggled(self, row, _param):
        """Persist the automatic recovery preference immediately."""
        self.parent_win.main_window.gsettings_mgr.set_setting("automatic_modem_recovery", row.get_active())

    def _show_auto_recovery_info(self, btn):
        """Explain what automatic modem recovery does."""
        body = _("When the modem stops responding, the phone quietly restarts "
                 "the modem stack by itself and you only hear about it if that "
                 "fails. The phone is never rebooted automatically. When this "
                 "is off, the Modem Recovery screen appears instead so you can "
                 "run the restart yourself.")
        present_info_sheet(self, _("Automatic Modem Recovery"), body)

    def _on_modem_recovery(self, btn):
        """Show the modem recovery screen, unless the modem is fine."""
        main_window = self.parent_win.main_window
        if main_window.ofono and main_window.ofono.dialing_available():
            self._show_toast(_("The modem is working normally."))
            return
        app = main_window.get_application()
        if app:
            app.open_modem_recovery()
