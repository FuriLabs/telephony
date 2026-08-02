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
        """Open the data management dialog."""
        from .data_management_window import DataManagementDialog
        dm = DataManagementDialog(self.parent_win.main_window)
        dm.present()

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
        dialog = Adw.AlertDialog(heading=_("Automatic Modem Recovery"), body=body)
        dialog.add_response("close", _("Close"))
        dialog.set_response_appearance("close", Adw.ResponseAppearance.SUGGESTED)
        dialog.present(self)

    def _on_modem_recovery(self, btn):
        """Show the modem recovery screen, unless the modem is fine."""
        main_window = self.parent_win.main_window
        if main_window.ofono and main_window.ofono.dialing_available():
            self._show_toast(_("The modem is working normally."))
            return
        app = main_window.get_application()
        if app:
            app.open_modem_recovery()
