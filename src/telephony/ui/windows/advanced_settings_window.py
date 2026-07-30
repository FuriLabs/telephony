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

from ...backend.utils.system_utils import restart_ril_modem
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

        btn_find = Gtk.Button(label=_("Set \"Find my Telephony\""))
        btn_find.add_css_class("suggested-action")
        btn_find.set_margin_bottom(8)
        btn_find.connect("clicked", lambda b: GLib.idle_add(lambda: self._open_action_window("trusted_sms_location_request") or False))
        grp_actions.add(btn_find)

        btn_callback = Gtk.Button(label=_("Set \"Trusted Callback\""))
        btn_callback.add_css_class("suggested-action")
        btn_callback.set_margin_bottom(8)
        btn_callback.connect("clicked", lambda b: GLib.idle_add(lambda: self._open_action_window("trusted_sms_silent_callback") or False))
        grp_actions.add(btn_callback)

        btn_relay = Gtk.Button(label=_("Set \"SMS -Relay\""))
        btn_relay.add_css_class("suggested-action")
        btn_relay.set_margin_bottom(8)
        btn_relay.connect("clicked", lambda b: GLib.idle_add(lambda: self._open_action_window("trusted_sms_relay") or False))
        grp_actions.add(btn_relay)

        btn_tmate = Gtk.Button(label=_("Set \"SMS -tmate\""))
        btn_tmate.add_css_class("suggested-action")
        btn_tmate.set_margin_bottom(8)
        btn_tmate.connect("clicked", lambda b: GLib.idle_add(lambda: self._open_action_window("trusted_sms_ssh_access") or False))
        grp_actions.add(btn_tmate)

        btn_wipe = Gtk.Button(label=_("Set \"SMS Switch Wipe Device\""))
        btn_wipe.add_css_class("destructive-action")
        btn_wipe.connect("clicked", lambda b: GLib.idle_add(lambda: self._open_action_window("trusted_sms_remote_wipe") or False))
        grp_actions.add(btn_wipe)

        grp_restart = Adw.PreferencesGroup()
        self.page.add(grp_restart)

        btn_restart = Gtk.Button(label=_("Restart Modem"))
        btn_restart.add_css_class("destructive-action")
        btn_restart.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_restart_modem(b) or False))
        grp_restart.add(btn_restart)

        grp_data = Adw.PreferencesGroup()
        self.page.add(grp_data)

        btn_data = Gtk.Button(label=_("Data Management"))
        btn_data.add_css_class("destructive-action")
        btn_data.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_data_management(b) or False))
        grp_data.add(btn_data)

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

    def _on_restart_modem(self, btn):
        """Handle restart modem action."""
        self._show_toast(_("Restarting Modem..."), False)
        run_in_background(restart_ril_modem)
        GLib.idle_add(lambda: self.close() or False)
