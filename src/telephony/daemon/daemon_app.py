# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Gio, GLib
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.system_utils import launch_desktop_uri
from telephony.shared.constants import (DAEMON_APP_ID, INCALL_DESKTOP_FILE, CALLS_DESKTOP_FILE, MESSAGES_DESKTOP_FILE)
from telephony.daemon.telephony_core import TelephonyCore


class DaemonApp(Gio.Application):

    def __init__(self):
        super().__init__(application_id=DAEMON_APP_ID)
        self.core = TelephonyCore(ui=self)
        self.ofono = None

    def do_startup(self):
        Gio.Application.do_startup(self)
        GLib.set_prgname(self.get_application_id())

        self.hold()
        self.core.start()
        self.ofono = self.core.ofono
        self.core.ofono.connect("call-added", self.on_global_call_added)

        action_open = Gio.SimpleAction.new("open-chat", GLib.VariantType.new("s"))
        action_open.connect("activate", self.on_action_open_chat)
        self.add_action(action_open)

        action_recovery = Gio.SimpleAction.new("modem-recovery", None)
        action_recovery.connect("activate", lambda a, p: self.core.open_modem_recovery())
        self.add_action(action_recovery)

        action_dial = Gio.SimpleAction.new("dial-number", GLib.VariantType.new("s"))
        action_dial.connect("activate", self.on_action_dial)
        self.add_action(action_dial)

    def on_action_open_chat(self, _action, parameter):
        number = parameter.get_string()
        launch_desktop_uri(MESSAGES_DESKTOP_FILE, f"sms:{number}")
        self.core.clear_notification(number)

    def on_action_dial(self, _action, parameter):
        number = parameter.get_string()
        launch_desktop_uri(CALLS_DESKTOP_FILE, f"tel:{number}")
        self.core.clear_notification(number)

    def do_activate(self):
        logger.info("[Daemon] Activated; the service has no window to raise")

    def is_any_window_active(self):
        return False

    def on_global_call_added(self, _manager, path, _props):
        self.show_incall_ui()

    def show_incall_ui(self):
        try:
            app_info = Gio.DesktopAppInfo.new(INCALL_DESKTOP_FILE)
        except TypeError as e:
            logger.error(f"[Daemon] {INCALL_DESKTOP_FILE} is missing, no call window to show: {e}")
            return

        try:
            app_info.launch(None, None)
        except Exception as e:
            logger.error(f"[Daemon] Could not start the call window: {e}")

    def withdraw_number_notifications(self, norm_num):
        try:
            self.withdraw_notification(norm_num)
            self.withdraw_notification(f"missed_{norm_num}")
        except Exception as e:
            logger.warning(f"Failed to withdraw notification: {e}")
