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
from telephony.client.ui.windows.trusted_actions_list_window import TrustedActionsListWindow
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from gettext import gettext as _
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.constants import MMS_SIZE_LIMIT_DEFAULT_KB
from telephony.client.ui.widgets.common_widget import (present_info_sheet, build_selector_row, set_selector_options, build_nav_row)


class AdvancedSettingsWindow(Adw.NavigationPage):
    """Advanced settings subpage of the settings navigation."""

    def __init__(self, parent):
        """Initialize the advanced settings page."""
        super().__init__(title=_("Advanced Settings"))
        self.parent_win = parent
        self.row_recovery = None
        self.recovery_spinner = None
        self._recovery_running = False

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
        self.page.set_description(
            _("For experienced users. This page lets trusted senders trigger "
              "actions on your phone by text message, restarts the modem, "
              "changes which app icons are installed, and erases your data."))
        self.content_box.append(self.page)

        grp_actions = Adw.PreferencesGroup(title=_("Secret SMS Triggers"))
        self.page.add(grp_actions)
        grp_actions.set_visible(self.parent_win.mode_messages)

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
        grp_calls.set_visible(self.parent_win.mode_calls)

        grp_calls.add(self._experimental_row(
            _("Allow Conference Calls"), "allow_conference_calls",
            self._show_conference_info))
        grp_calls.add(self._experimental_row(
            _("Allow Call Transfer"), "allow_call_transfer",
            self._show_transfer_info))

        grp_restart = Adw.PreferencesGroup()
        self.page.add(grp_restart)
        grp_restart.set_visible(self.parent_win.mode_calls or self.parent_win.mode_messages)

        row_auto = Adw.SwitchRow(title=_("Automatic Modem Recovery"))
        row_auto.set_active(self.parent_win.main_window.gsettings_mgr.get_setting("automatic_modem_recovery") == "true")
        row_auto.connect("notify::active", self._on_auto_recovery_toggled)
        btn_auto_info = self._info_button(self._show_auto_recovery_info)
        row_auto.add_suffix(btn_auto_info)
        grp_restart.add(row_auto)

        self.row_recovery = Adw.ActionRow(title=_("Modem Recovery"), activatable=True)
        self.row_recovery.add_css_class("error")
        self.row_recovery.connect("activated", lambda r: self._on_modem_recovery(None))
        self.recovery_spinner = Adw.Spinner()
        self.recovery_spinner.set_visible(False)
        self.row_recovery.add_suffix(self.recovery_spinner)
        self._describe_recovery_row()
        grp_restart.add(self.row_recovery)

        grp_messaging = Adw.PreferencesGroup(title=_("Messaging"))
        self.page.add(grp_messaging)
        grp_messaging.set_visible(self.parent_win.mode_messages)

        self.mms_limit_values = ["100", "300", "600", "900", "1024", "2048", "3072", "4096", "5120"]
        mms_limit_labels = ["100 kB", "300 kB", "600 kB", "900 kB", "1 MB", "2 MB", "3 MB", "4 MB", "5 MB"]
        self.row_mms_limit = build_selector_row(
            _("MMS Size Limit"), self._on_mms_limit_selected)
        saved_limit = self.parent_win.main_window.gsettings_mgr.get_setting("mms_size_limit")
        if saved_limit not in self.mms_limit_values:
            saved_limit = MMS_SIZE_LIMIT_DEFAULT_KB
        set_selector_options(self.row_mms_limit, mms_limit_labels,
                             self.mms_limit_values.index(saved_limit))
        btn_info_mms = self._info_button(self._show_mms_size_info)
        grp_messaging.set_header_suffix(btn_info_mms)
        grp_messaging.add(self.row_mms_limit)

        grp_data = Adw.PreferencesGroup()
        self.page.add(grp_data)

        grp_data.add(self._nav_row(_("Data Management"), _("Export, import and reset"),
                                   lambda: self._on_data_management(None),
                                   destructive=True))

        self._init_desktop_toggles(self.page)

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

    def _info_button(self, handler):
        """Build the round info button that opens an explanation sheet."""
        button = Gtk.Button(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.add_css_class("circular")
        button.connect("clicked", lambda b: GLib.idle_add(lambda: handler(b) or False))
        return button

    def _nav_row(self, title, subtitle, callback, destructive=False):
        """Build an activatable navigation row with a chevron."""
        return build_nav_row(title, subtitle, callback, destructive=destructive)

    def _push_page(self, page):
        """Push a page, which takes the focus rather than its contents.

        A text field taking it brings the keyboard up with the page,
        and a page is opened to be read before it is typed into.
        """
        page.set_focusable(True)
        self.get_ancestor(Adw.NavigationView).push(page)

    def _on_data_management(self, btn):
        """Push the data management page."""
        from .data_management_window import DataManagementDialog
        dm = DataManagementDialog(self.parent_win.main_window)
        self._push_page(dm.build_page())

    def _open_action_window(self, mode):
        """Open the trusted action window after polkit authentication."""
        def done(returncode):
            if returncode != 0:
                self._show_toast(_("Authentication cancelled"), True)
                return
            page = TrustedActionsListWindow(self, self.parent_win.main_window.gsettings_mgr, self.parent_win.eds, mode=mode)
            self._push_page(page)

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
        btn_info = Gtk.Button(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER)
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

    def _describe_recovery_row(self, subtitle=None):
        """Say what the row would do, or what it just did.

        A working modem has nothing to repair, so the row says that and
        stops offering: an enabled button that answers every press with
        a refusal is a worse answer than a disabled one.
        """
        if subtitle is not None:
            self.row_recovery.set_subtitle(subtitle)
            return

        ofono = self.parent_win.main_window.ofono
        working = bool(ofono and ofono.dialing_available())
        self.row_recovery.set_subtitle(
            _("The modem is working") if working
            else _("Restart the modem, which takes about 30 seconds"))
        self.row_recovery.set_sensitive(not working)

    def _on_modem_recovery(self, btn):
        """Repair the modem from here, and say what came of it.

        The row does the work itself rather than sending the user to a
        screen that may not appear, because a press that shows nothing
        is indistinguishable from one that did nothing.

        Whether a restart is worth attempting is the owner's to answer,
        since it is the one that watches the modem, and answering it
        here as well is how this row came to miss the hardware switch
        while the watchdog honoured it.
        """
        main_window = self.parent_win.main_window
        if self._recovery_running:
            return

        app = main_window.get_application()
        if not app:
            return

        self._recovery_running = True
        self.row_recovery.set_sensitive(False)
        self.recovery_spinner.set_visible(True)
        self._describe_recovery_row(_("Restarting the modem…"))

        if not app.request_auto_recovery(self._on_recovery_finished):
            self._on_recovery_finished(False)

    def _on_recovery_finished(self, success, reason=""):
        """Report the outcome in the row the user pressed."""
        self._recovery_running = False
        self.recovery_spinner.set_visible(False)
        self.row_recovery.set_sensitive(True)
        if reason:
            self._describe_recovery_row(reason)
            return
        self._describe_recovery_row(_("Calls and messages work again") if success
                                    else _("Recovery did not help. The modem still is not responding"))
