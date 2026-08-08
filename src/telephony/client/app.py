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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
gi.require_version('EDataServer', '1.2')
gi.require_version('EBookContacts', '1.2')
gi.require_version('EBook', '1.2')

from gi.repository import Gtk, Adw, Gio, Gdk, GLib, Gst, GObject
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.system_utils import launch_desktop_uri, ensure_daemon_running
from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.constants import (APP_ID, INCALL_DESKTOP_FILE, CALLS_DESKTOP_FILE, MESSAGES_DESKTOP_FILE)
from telephony.client.window_core import WindowCore
from telephony.client.ui.main_window import MainWindow
from telephony.client.ui.windows.incall_window import InCallWindow


class App(Adw.Application):
    """The windowed face of a telephony process.

    Everything that is not a window lives in WindowCore; this class
    draws windows, routes launcher intents to them and implements the
    ui delegate the core calls when something must reach the screen.
    The manager handles are mirrored as attributes because every
    window and dialog receives them from here.
    """

    def __init__(self, application_id=APP_ID):
        """Initialize the Application."""
        super().__init__(application_id=application_id)

        self.core = WindowCore(application_id, ui=self)
        self.owns_incall_ui = self.core.owns_incall_ui

        self.db = None
        self.ofono = None
        self.eds = None
        self.gsettings_mgr = None
        self.daemon_client = None
        self.incall = None

    @property
    def daemon_missing(self):
        """Whether the last attempt to reach or start the daemon failed."""
        return self.core.daemon_missing

    def retry_daemon_start(self, on_done):
        """Ask the bus for the service again; on_done hears whether it came."""
        self.core.retry_daemon_start(on_done)

    def request_auto_recovery(self, on_done=None):
        """Restart the modem stack once, silently; False when already running."""
        return self.core.request_auto_recovery(on_done)

    def open_modem_recovery(self):
        """Show the modem recovery screen with the current status."""
        self.core.open_modem_recovery()

    def clear_notification(self, number):
        """Clear active notifications for a number."""
        self.core.clear_notification(number)

    def _close_through_hook(self, *args):
        """Clear a main window's stacked sheets so one close closes everything."""
        window = next((a for a in args if isinstance(a, Gtk.Window)), None)
        if not isinstance(window, MainWindow):
            return True
        dialog = window.get_visible_dialog()
        while dialog is not None:
            dialog.force_close()
            following = window.get_visible_dialog()
            if following is dialog:
                break
            dialog = following
        return True

    def do_startup(self):
        """Perform application startup tasks."""
        Gtk.Application.do_startup(self)
        GLib.set_prgname(self.get_application_id())

        GObject.add_emission_hook(Gtk.Window, "close-request", self._close_through_hook)

        run_in_background(ensure_daemon_running)

        if not Gst.is_initialized():
            Gst.init(None)

        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        self._setup_css()
        self._setup_icon_paths()

        self.core.start()

        self.db = self.core.db
        self.ofono = self.core.ofono
        self.eds = self.core.eds
        self.gsettings_mgr = self.core.gsettings_mgr
        self.daemon_client = self.core.daemon_client

        action_open = Gio.SimpleAction.new("open-chat", GLib.VariantType.new("s"))
        action_open.connect("activate", self.on_action_open_chat)
        self.add_action(action_open)

        action_recovery = Gio.SimpleAction.new("modem-recovery", None)
        action_recovery.connect("activate", lambda a, p: self.open_modem_recovery())
        self.add_action(action_recovery)

        action_dial = Gio.SimpleAction.new("dial-number", GLib.VariantType.new("s"))
        action_dial.connect("activate", self.on_action_dial)
        self.add_action(action_dial)

    def _ensure_incall_window(self):
        """Ensure the InCallWindow is initialized."""
        if self.incall is None:
            logger.info("Initializing InCallWindow (Lazy Load)")
            self.incall = InCallWindow(self.gsettings_mgr, self.ofono, self.eds, self.db)
            self.add_window(self.incall)

            self.incall.connect("close-request", self._on_incall_closed)

    def _on_incall_closed(self, window):
        """Forget the call window once it is really gone.

        The window itself decides whether a close means stepping aside
        or leaving, so reaching here means it left.
        """
        logger.info("InCallWindow closed, dropping it")
        self.incall = None
        return False

    def any_window_active(self):
        """Return True when any application window currently has focus."""
        return any(win.is_active() for win in self.get_windows())

    def withdraw_number_notifications(self, norm_num):
        """Withdraw the shell notifications filed under a number."""
        try:
            self.withdraw_notification(norm_num)
            self.withdraw_notification(f"missed_{norm_num}")
        except Exception as e:
            logger.warning(f"Failed to withdraw notification: {e}")

    def release_keyboard_focus(self):
        """Drop entry focus in all windows and order the on-screen keyboard away."""
        for win in self.get_windows():
            win.set_focus(None)
        self._hide_osk()

    def _hide_osk(self):
        """Hide the Phosh on-screen keyboard via its D-Bus interface."""
        try:
            bus = self.get_dbus_connection() or Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call(
                "sm.puri.OSK0", "/sm/puri/OSK0", "sm.puri.OSK0", "SetVisible",
                GLib.Variant("(b)", (False,)),
                None, Gio.DBusCallFlags.NONE, -1, None, None)
        except Exception as e:
            logger.debug(f"OSK hide request failed: {e}")

    def _present_incall_window(self):
        """Present the in-call window after the on-screen keyboard has dismissed."""
        if self.incall:
            self.incall.defer_present = False
            self.incall.present()
            self.incall.update_state()
        return False

    def show_incall_ui(self):
        """Bring up the call window, wherever it lives.

        The window runs as its own application so the shell can tell it
        apart from the other launchers, which means the owner starts it
        rather than drawing it. Launching an instance that already runs
        reaches the running one, so a second call is not a second
        window.
        """
        if self.owns_incall_ui:
            self._ensure_incall_window()
            self.incall.defer_present = True
            self.release_keyboard_focus()
            GLib.idle_add(self._present_incall_window)
            return

        app_info = Gio.DesktopAppInfo.new(INCALL_DESKTOP_FILE)
        if not app_info:
            logger.error(f"[App] {INCALL_DESKTOP_FILE} is missing, no call window to show")
            return

        try:
            app_info.launch(None, None)
        except Exception as e:
            logger.error(f"[App] Could not start the call window: {e}")

    def apply_recovery_state(self, active, message, failed):
        """Put the call window on its recovery page, or take it off."""
        if not active:
            if self.incall:
                self.incall.exit_recovery_mode()
            return

        self._ensure_incall_window()
        self.incall.enter_recovery_mode(message, failed=failed)

    def apply_service_presence(self, present, unit_state):
        """Relay the service's presence to every open window."""
        for win in self.get_windows():
            if isinstance(win, (MainWindow, InCallWindow)):
                win.apply_service_presence(present, unit_state)

    def start_service(self, on_done):
        """Start the service by whichever path the unit state allows."""
        self.core.start_service(on_done)

    def _setup_icon_paths(self):
        """
        Ensure icon lookup works under the daemon's startup conditions.
        Re-registers libadwaita's internal resource icons, whose registration
        can be missed when the process starts at session bring-up, and adds
        the system icon directory for minimal service environments.
        """
        try:
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            theme.add_resource_path("/org/gnome/Adwaita/icons")
            theme.add_search_path("/usr/share/icons")
        except Exception as e:
            logger.warning(f"Icon path setup failed: {e}")

    def _setup_css(self):
        """Load and apply custom CSS."""
        provider = Gtk.CssProvider()
        css_file = Gio.File.new_for_path(os.path.join(os.path.dirname(__file__), 'style.css'))
        provider.load_from_file(css_file)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _launcher_modes(self):
        """Return the (calls, messages, contacts) surfaces this launcher owns.

        A bare activation carries no arguments, so the application id is
        the only truth about what this process may draw; a full window
        inside the Calls process would put a Messages tab under the
        Calls icon.
        """
        app_id = self.get_application_id()
        if app_id == f"{APP_ID}.Calls":
            return (True, False, True)
        if app_id == f"{APP_ID}.Messages":
            return (False, True, False)
        if app_id == f"{APP_ID}.Contacts":
            return (False, False, True)
        return (True, True, True)

    def do_activate(self):
        """Activate application."""
        Adw.Application.do_activate(self)

        logger.info("Application activated via DBus.")

        if self.owns_incall_ui:
            self._ensure_incall_window()
            self._present_incall_window()
            return

        calls, messages, contacts = self._launcher_modes()

        for existing_win in self.get_windows():
            if not isinstance(existing_win, MainWindow):
                continue
            if (existing_win.show_calls_mode == calls and
                    existing_win.show_messages_mode == messages and
                    existing_win.show_contacts_mode == contacts):
                logger.info("Reusing this launcher's window upon DBus activation.")
                existing_win.set_visible(True)
                existing_win.present()
                return

        logger.info(f"Opening this launcher's window upon DBus activation (calls={calls}, messages={messages}, contacts={contacts})")
        win = MainWindow(
            self,
            self.ofono,
            self.db,
            self.eds,
            self.gsettings_mgr,
            show_calls=calls,
            show_messages=messages,
            show_contacts=contacts
        )
        win.present()
        return

    def on_window_destroyed(self, win):
        """Handle window destruction."""
        logger.info(f"Window destroyed. Remaining: {len(self.get_windows())}")
        if not self.get_windows():
            if self.ofono:
                self.ofono.set_active_chat(None)
            logger.info("[App] Last window closed, leaving the daemon to it")
            GLib.idle_add(self.quit)
        return False

    def on_action_open_chat(self, _action, parameter):
        """Handle open-chat action."""
        number = parameter.get_string()
        self.open_messages_chat(number)
        self.clear_notification(number)

    def open_messages_chat(self, number):
        """Open a chat in a messages window this launcher may draw.

        Prefers the dedicated messages window and falls back to an open
        full window, the same order do_command_line resolves --messages
        --open-chat. When this process has no messages surface and its
        launcher does not own one, the chat is handed to the Messages
        launcher: a chat drawn inside the Calls process would carry the
        Calls icon, because the shell names surfaces after the
        application id, not after what they show.
        """
        target = None
        for win in self.get_windows():
            if isinstance(win, MainWindow) and win.show_messages_mode:
                target = win
                if not win.show_calls_mode:
                    break
        if target is None:
            if self.get_application_id() not in (APP_ID, f"{APP_ID}.Messages"):
                logger.info("Chat belongs to the Messages launcher, handing it over")
                launch_desktop_uri(MESSAGES_DESKTOP_FILE, f"sms:{number}")
                return
            logger.info("No messages window for chat, creating one")
            target = MainWindow(self, self.ofono, self.db, self.eds,
                                self.gsettings_mgr, show_calls=False,
                                show_messages=True, show_contacts=False)
        target.set_visible(True)
        target.present()
        target.open_chat_for_number(number)

    def on_action_dial(self, _action, parameter):
        """Handle dial-number action.

        A process without a dialpad hands the number to the Calls
        launcher rather than drawing one under its own icon.
        """
        number = parameter.get_string()

        target_win = None
        for win in self.get_windows():
            if isinstance(win, MainWindow) and win.show_calls_mode:
                target_win = win
                break

        if target_win:
            target_win.present()
            target_win.open_dialpad_with_number(number)
            self.clear_notification(number)
            return

        if self.get_application_id() not in (APP_ID, f"{APP_ID}.Calls"):
            logger.info("Dialpad belongs to the Calls launcher, handing it over")
            launch_desktop_uri(CALLS_DESKTOP_FILE, f"tel:{number}")
            self.clear_notification(number)
            return

        logger.info("No existing window found with dialpad for intent. Launching full window.")
        win = MainWindow(
            self,
            self.ofono,
            self.db,
            self.eds,
            self.gsettings_mgr,
            show_calls=True,
            show_messages=True,
            show_contacts=True
        )
        win.present()
        win.open_dialpad_with_number(number)
        self.clear_notification(number)
