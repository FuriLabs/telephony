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
from loguru import logger

from ...backend.utils.thread_utils import run_in_background


class AdvancedSettingsWindow(Adw.Window):
    """Advanced settings window."""

    def __init__(self, parent):
        """Initialize the advanced settings window."""
        super().__init__(title=_("Advanced Settings"))
        self.set_transient_for(parent)
        self.parent_win = parent
        self.set_modal(True)
        self.set_default_size(450, 800)

        self.overlay = Adw.ToastOverlay()
        self.set_content(self.overlay)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(main_box)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: self.close() or False))
        header.pack_start(btn_cancel)

        btn_save = Gtk.Button(label=_("Save"))
        btn_save.add_css_class("suggested-action")
        btn_save.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_save_clicked(b) or False))
        header.pack_end(btn_save)

        main_box.append(header)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        main_box.append(self.stack)

        loading_page = Adw.StatusPage()
        loading_page.set_title(_("Loading..."))
        loading_page.set_icon_name("network-cellular-signal-good-symbolic")

        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        spinner.set_halign(Gtk.Align.CENTER)

        box_spin = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box_spin.set_valign(Gtk.Align.CENTER)
        box_spin.append(spinner)
        loading_page.set_child(box_spin)

        self.stack.add_named(loading_page, "loading")

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self.content_box)
        self.stack.add_named(scroll, "content")

        self.stack.set_visible_child_name("loading")
        self._pending_timeouts = set()
        self.connect("close-request", self._on_close_request)
        self._add_timeout(100, self._load_data)

    def _add_timeout(self, interval_ms, callback):
        """Schedule a tracked one-shot timeout cancelled on close."""
        holder = {}

        def fire():
            self._pending_timeouts.discard(holder["id"])
            callback()
            return False

        holder["id"] = GLib.timeout_add(interval_ms, fire)
        self._pending_timeouts.add(holder["id"])

    def _on_close_request(self, win):
        """Cancel pending timeouts when the window closes."""
        for source_id in self._pending_timeouts:
            GLib.source_remove(source_id)
        self._pending_timeouts.clear()
        return False

    def _show_toast(self, message, is_error=False):
        """Display a toast message."""
        toast = Adw.Toast.new(message)
        if is_error:
            toast.set_priority(Adw.ToastPriority.HIGH)
        self.overlay.add_toast(toast)

    def _load_data(self):
        """Build the settings UI after the loading page is shown."""
        try:
            self._build_ui()
            self.stack.set_visible_child_name("content")
        except Exception as e:
            logger.error(f"[AdvancedSettings] Load error: {e}")
            self._show_toast(_("Error loading settings: {e}").format(e=e), True)

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
            win = TrustedActionsListWindow(self, self.parent_win.main_window.gsettings_mgr, self.parent_win.eds, mode=mode)
            win.present()

        def failed(error):
            self._show_toast(_("Auth error: {e}").format(e=error), True)

        run_in_background(lambda: subprocess.run(["pkexec", "true"]).returncode,
                          on_complete=done, on_error=failed)

    def on_save_clicked(self, btn):
        """Apply all pending changes."""
        self.overlay.add_toast(Adw.Toast.new(_("Advanced Settings Applied")))
        self._add_timeout(1500, self.close)

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
        dialog = Adw.MessageDialog(transient_for=self, heading=_("Automatic Modem Recovery"), body=body)
        dialog.add_response("close", _("Close"))
        dialog.set_response_appearance("close", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: GLib.idle_add(lambda: d.close() or False))
        dialog.present()

    def _on_modem_recovery(self, btn):
        """Show the modem recovery screen, unless the modem is fine."""
        main_window = self.parent_win.main_window
        if main_window.ofono and main_window.ofono.dialing_available():
            self._show_toast(_("The modem is working normally."))
            return
        app = main_window.get_application()
        if app:
            app.open_modem_recovery()
