# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")

from gi.repository import Gtk, Adw, Gio, Gdk, GLib, Gst, GObject
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.system_utils import launch_desktop_uri, ensure_daemon_running
from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.constants import (APP_ID, INCALL_DESKTOP_FILE, CALLS_DESKTOP_FILE, MESSAGES_DESKTOP_FILE)
from telephony.client.window_core import WindowCore
from telephony.client.ui.main_window import MainWindow
from telephony.client.ui.windows.incall_window import InCallWindow


class App(Adw.Application):

    def __init__(self, application_id=APP_ID):
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
        return self.core.daemon_missing

    def retry_daemon_start(self, on_done):
        self.core.retry_daemon_start(on_done)

    def request_auto_recovery(self, on_done=None):
        return self.core.request_auto_recovery(on_done)

    def open_modem_recovery(self):
        self.core.open_modem_recovery()

    def clear_notification(self, number):
        self.core.clear_notification(number)

    def close_through_hook(self, *args):
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
        Gtk.Application.do_startup(self)
        GLib.set_prgname(self.get_application_id())

        GObject.add_emission_hook(Gtk.Window, "close-request", self.close_through_hook)

        run_in_background(ensure_daemon_running)

        if not Gst.is_initialized():
            Gst.init(None)

        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        self.setup_css()
        self.setup_icon_paths()

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

    def ensure_incall_window(self):
        if self.incall is None:
            logger.info("Initializing InCallWindow (Lazy Load)")
            self.incall = InCallWindow(self.gsettings_mgr, self.ofono, self.eds, self.db)
            self.add_window(self.incall)

            self.incall.connect("close-request", self.on_incall_closed)

    def on_incall_closed(self, window):
        logger.info("InCallWindow closed, dropping it")
        self.incall = None
        return False

    def is_any_window_active(self):
        return any(win.is_active() for win in self.get_windows())

    def withdraw_number_notifications(self, norm_num):
        try:
            self.withdraw_notification(norm_num)
            self.withdraw_notification(f"missed_{norm_num}")
        except Exception as e:
            logger.warning(f"Failed to withdraw notification: {e}")

    def release_keyboard_focus(self):
        for win in self.get_windows():
            win.set_focus(None)
        self.hide_osk()

    def hide_osk(self):
        try:
            bus = self.get_dbus_connection() or Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call(
                "sm.puri.OSK0", "/sm/puri/OSK0", "sm.puri.OSK0", "SetVisible",
                GLib.Variant("(b)", (False,)),
                None, Gio.DBusCallFlags.NONE, -1, None, None)
        except Exception as e:
            logger.debug(f"OSK hide request failed: {e}")

    def present_incall_window(self):
        if self.incall:
            self.incall.defer_present = False
            self.incall.present()
            self.incall.update_state()
        return False

    def show_incall_ui(self):
        if self.owns_incall_ui:
            self.ensure_incall_window()
            self.incall.defer_present = True
            self.release_keyboard_focus()
            GLib.idle_add(self.present_incall_window)
            return

        try:
            app_info = Gio.DesktopAppInfo.new(INCALL_DESKTOP_FILE)
        except TypeError as e:
            logger.error(f"[App] {INCALL_DESKTOP_FILE} is missing, no call window to show: {e}")
            return

        try:
            app_info.launch(None, None)
        except Exception as e:
            logger.error(f"[App] Could not start the call window: {e}")

    def apply_recovery_state(self, active, message, failed):
        if not active:
            if self.incall:
                self.incall.exit_recovery_mode()
            return

        self.ensure_incall_window()
        self.incall.enter_recovery_mode(message, failed=failed)

    def apply_service_presence(self, present, unit_state):
        for win in self.get_windows():
            if isinstance(win, (MainWindow, InCallWindow)):
                win.apply_service_presence(present, unit_state)

    def start_service(self, on_done):
        self.core.start_service(on_done)

    def setup_icon_paths(self):
        try:
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            theme.add_resource_path("/org/gnome/Adwaita/icons")
            theme.add_search_path("/usr/share/icons")
        except Exception as e:
            logger.warning(f"Icon path setup failed: {e}")

    def setup_css(self):
        provider = Gtk.CssProvider()
        css_file = Gio.File.new_for_path(os.path.join(os.path.dirname(__file__), "style.css"))
        provider.load_from_file(css_file)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def launcher_modes(self):
        app_id = self.get_application_id()
        if app_id == f"{APP_ID}.Calls":
            return (True, False, True)
        if app_id == f"{APP_ID}.Messages":
            return (False, True, False)
        if app_id == f"{APP_ID}.Contacts":
            return (False, False, True)
        return (True, True, True)

    def do_activate(self):
        Adw.Application.do_activate(self)

        logger.info("Application activated via DBus.")

        if self.owns_incall_ui:
            self.ensure_incall_window()
            self.present_incall_window()
            return

        calls, messages, contacts = self.launcher_modes()

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
        logger.info(f"Window destroyed. Remaining: {len(self.get_windows())}")
        if not self.get_windows():
            if self.ofono:
                self.ofono.set_active_chat(None)
            logger.info("[App] Last window closed, leaving the daemon to it")
            GLib.idle_add(self.quit)
        return False

    def on_action_open_chat(self, _action, parameter):
        number = parameter.get_string()
        self.open_messages_chat(number)
        self.clear_notification(number)

    def open_messages_chat(self, number):
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
