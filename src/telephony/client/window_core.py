# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Gio, GLib
from telephony.shared.utils.log_utils import logger

from telephony.client.services.daemon_client import DaemonClient
from telephony.client.services.service_monitor import ServiceMonitor
from telephony.shared.managers.database_manager import DatabaseManager
from telephony.client.managers.settings_mirror import SettingsMirror
from telephony.client.managers.ofono_mirror import OfonoMirror
from telephony.shared.managers.eds_manager import EdsManager
from telephony.shared.utils.thread_utils import run_in_background
from telephony.client.utils.locale_utils import init_locale
from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.constants import INCALL_APP_ID, DAEMON_BUS_NAME

DAEMON_START_TIMEOUT_MS = 25000
DBUS_START_REPLY_SUCCESS = 1
DBUS_START_REPLY_ALREADY_RUNNING = 2


class WindowCore:

    def __init__(self, application_id, ui):
        self.ui = ui
        self.owns_incall_ui = application_id == INCALL_APP_ID
        self.daemon_missing = False
        self.recovery_state = (False, "", False)

        self.gsettings_mgr = None
        self.eds = None
        self.db = None
        self.ofono = None
        self.daemon_client = None
        self.service_monitor = None
        self._last_presence = None

        self.ensure_daemon_running()

    def ensure_daemon_running(self):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[App] No session bus, the daemon cannot be reached: {e}")
            self.daemon_missing = True
            return

        if self.is_daemon_name_owned(bus):
            return

        self.daemon_missing = not self.start_daemon_service(bus)
        if self.daemon_missing:
            logger.error("[App] The telephony service could not be started")

    def retry_daemon_start(self, on_done):
        def task():
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            started = self.is_daemon_name_owned(bus) or self.start_daemon_service(bus)
            self.daemon_missing = not started
            return started

        run_in_background(task, on_complete=on_done)

    def is_daemon_name_owned(self, bus):
        try:
            res = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)", (DAEMON_BUS_NAME,)),
                GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, -1, None)
            return res.unpack()[0]
        except Exception as e:
            logger.warning(f"[App] Could not check for a running daemon: {e}")
            return False

    def start_daemon_service(self, bus):
        logger.info("[App] No telephony daemon found, asking the bus to start the service")
        try:
            res = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "StartServiceByName", GLib.Variant("(su)", (DAEMON_BUS_NAME, 0)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE,
                DAEMON_START_TIMEOUT_MS, None)
            started = res.unpack()[0] in (DBUS_START_REPLY_SUCCESS, DBUS_START_REPLY_ALREADY_RUNNING)
        except Exception as e:
            logger.warning(f"[App] Could not start the telephony service: {e}")
            return False

        if started:
            logger.info("[App] Telephony service started, staying a window")
        return started

    def start(self):
        init_locale()

        logger.info("Initializing window mirrors...")
        self.daemon_client = DaemonClient()
        self.gsettings_mgr = SettingsMirror(self.daemon_client)
        self.eds = EdsManager(owns_live_views=False)
        self.eds.sources_provider = self.daemon_client.get_address_books
        self.db = DatabaseManager(self.eds, self.gsettings_mgr, owns_writes=False)
        self.eds.set_db(self.db, self.gsettings_mgr)
        self.ofono = OfonoMirror(self.gsettings_mgr, daemon_client=self.daemon_client)

        self.follow_daemon_changes()

        self.service_monitor = ServiceMonitor()
        self.service_monitor.connect("unit-state-changed", self.publish_presence)


        Gio.bus_watch_name(Gio.BusType.SESSION, DAEMON_BUS_NAME,
                           Gio.BusNameWatcherFlags.NONE,
                           self.on_daemon_appeared, self.on_daemon_vanished)

    def on_daemon_appeared(self, _bus, _name, _owner):
        if self.daemon_missing:
            logger.info("[App] The telephony service is back")
        self.daemon_missing = False
        self.publish_presence()

    def on_daemon_vanished(self, _bus, _name):
        self.daemon_missing = True
        self.publish_presence()

    def publish_presence(self, *args):
        state = self.service_monitor.state if self.service_monitor else "unknown"
        snapshot = (not self.daemon_missing, state)
        if snapshot == self._last_presence:
            return
        self._last_presence = snapshot
        self.ui.apply_service_presence(*snapshot)

    def start_service(self, on_done):
        if self.service_monitor and self.service_monitor.state == "failed":
            self.service_monitor.start_service(on_done)
            return
        self.retry_daemon_start(on_done)

    def follow_daemon_changes(self):
        self.daemon_client.subscribe(
            "MessagesChanged",
            lambda *args: GLib.idle_add(
                self.replay_change, "messages-updated", args[5].unpack()[0], args[5].unpack()[1]))
        self.daemon_client.subscribe(
            "BlocklistChanged",
            lambda *args: GLib.idle_add(self.replay_change, "blocklist-updated"))
        self.daemon_client.subscribe(
            "HistoryChanged",
            lambda *args: GLib.idle_add(self.replay_change, "history-updated"))
        self.daemon_client.subscribe(
            "ContactsChanged",
            lambda *args: run_in_background(self.eds.reload_cache_from_db))
        self.daemon_client.subscribe(
            "AddressBooksChanged",
            lambda *args: GLib.idle_add(self.eds.invalidate_sources_info))

        if self.owns_incall_ui:
            self.daemon_client.subscribe(
                "RecoveryStateChanged",
                lambda *args: GLib.idle_add(self.ui.apply_recovery_state, *args[5].unpack()))
            self.seed_recovery_state()

    def seed_recovery_state(self):
        def task():
            return self.daemon_client.call(
                "GetRecoveryState", None, GLib.VariantType("(bsb)"))

        def done(state):
            if state:
                self.ui.apply_recovery_state(*state)

        run_in_background(task, on_complete=done)

    def replay_change(self, name, *args):
        self.db.emit(name, *args)
        return False

    def request_auto_recovery(self, on_done=None):
        def done(reply):
            if on_done:
                on_done(bool(reply and reply[0]), reply[1] if reply else "")

        self.daemon_client.request_recovery(done)
        return True

    def open_modem_recovery(self):
        self.ui.show_incall_ui()

    def clear_notification(self, number):
        norm_num = normalize_number(number)
        self.daemon_client.clear_notification(norm_num)
        self.ui.withdraw_number_notifications(norm_num)
