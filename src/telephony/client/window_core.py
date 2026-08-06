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

from gi.repository import Gio, GLib
from telephony.shared.utils.log_utils import logger

from telephony.client.services.daemon_client import DaemonClient
from telephony.client.services.service_monitor import ServiceMonitor
from telephony.shared.managers.database_manager import DatabaseManager
from telephony.client.managers.settings_mirror import SettingsMirror
from telephony.client.managers.ofono_mirror import OfonoMirror
from telephony.shared.managers.mms_manager import MmsManager
from telephony.shared.managers.eds_manager import EdsManager
from telephony.shared.managers.notification_manager import NotificationManager
from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.utils.locale_utils import init_locale
from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.system_utils import trim_native_heap
from telephony.shared.constants import INCALL_APP_ID, DAEMON_BUS_NAME

DAEMON_START_TIMEOUT_MS = 25000
DBUS_START_REPLY_SUCCESS = 1
DBUS_START_REPLY_ALREADY_RUNNING = 2
HEAP_TRIM_AFTER_STARTUP_SECONDS = 120
HEAP_TRIM_INTERVAL_SECONDS = 1800


class WindowCore:
    """Owns the mirrors and the daemon liaison of a window process.

    A window process reads local mirrors, listens to the daemon's
    broadcasts and sends every action to the daemon; nothing here
    touches the modem, writes a store or receives anything. The
    daemon-side counterpart is TelephonyCore, which owns all of that;
    the two roles are separate classes on purpose, so neither carries
    the other's branches.
    """

    def __init__(self, application_id, ui):
        """Make sure an owner answers; managers are wired in start()."""
        self.ui = ui
        self.owns_incall_ui = application_id == INCALL_APP_ID
        self.daemon_missing = False
        self.recovery_state = (False, "", False)

        self.notification_manager = None
        self.gsettings_mgr = None
        self.eds = None
        self.db = None
        self.ofono = None
        self.mms = None
        self.daemon_client = None
        self.service_monitor = None
        self._last_presence = None

        self._ensure_daemon_running()

    def _ensure_daemon_running(self):
        """Ask the bus for the owner when none answers.

        A window never takes the owner role itself, not even when the
        service refuses to start: two owners file every arriving
        message twice, which is worse than a window that says the
        service is down and offers to start it again.
        """
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[App] No session bus, the daemon cannot be reached: {e}")
            self.daemon_missing = True
            return

        if self._daemon_name_owned(bus):
            return

        self.daemon_missing = not self._start_daemon_service(bus)
        if self.daemon_missing:
            logger.error("[App] The telephony service could not be started")

    def retry_daemon_start(self, on_done):
        """Ask the bus for the service again; on_done hears whether it came.

        The window stays usable while this runs, because the reply can
        take as long as a cold service start.
        """
        def task():
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            started = self._daemon_name_owned(bus) or self._start_daemon_service(bus)
            self.daemon_missing = not started
            return started

        run_in_background(task, on_complete=on_done)

    def _daemon_name_owned(self, bus):
        """Return True when a process already answers for the plain name."""
        try:
            res = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)", (DAEMON_BUS_NAME,)),
                GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, -1, None)
            return res.unpack()[0]
        except Exception as e:
            logger.warning(f"[App] Could not check for a running daemon: {e}")
            return False

    def _start_daemon_service(self, bus):
        """Have the bus start the telephony service; blocking, bounded wait.

        The reply arrives once the service owns the name, so a window
        that gets it can proxy its first action straight away.
        """
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
        """Wire the mirrors and the daemon subscriptions."""
        init_locale()

        logger.info("Initializing window mirrors...")
        self.notification_manager = NotificationManager()
        self.daemon_client = DaemonClient()
        self.gsettings_mgr = SettingsMirror(self.daemon_client)
        self.eds = EdsManager(owns_live_views=False)
        self.db = DatabaseManager(self.eds, self.gsettings_mgr, owns_writes=False)
        self.eds.set_db(self.db, self.gsettings_mgr)
        self.ofono = OfonoMirror(self.gsettings_mgr, daemon_client=self.daemon_client)

        self.mms = MmsManager(self.db, self.eds, self.gsettings_mgr, self.notification_manager,
                              owns_reception=False)

        self._follow_daemon_changes()

        self.service_monitor = ServiceMonitor()
        self.service_monitor.connect('unit-state-changed', self._publish_presence)

        GLib.timeout_add_seconds(HEAP_TRIM_AFTER_STARTUP_SECONDS, trim_native_heap)
        GLib.timeout_add_seconds(HEAP_TRIM_INTERVAL_SECONDS, lambda: trim_native_heap() or True)

        Gio.bus_watch_name(Gio.BusType.SESSION, DAEMON_BUS_NAME,
                           Gio.BusNameWatcherFlags.NONE,
                           self._on_daemon_appeared, self._on_daemon_vanished)

    def _on_daemon_appeared(self, _bus, _name, _owner):
        """Record that the service answers again.

        The bus re-resolves the signal subscriptions to the new owner
        by itself, so nothing needs reconnecting here.
        """
        if self.daemon_missing:
            logger.info("[App] The telephony service is back")
        self.daemon_missing = False
        self._publish_presence()

    def _on_daemon_vanished(self, _bus, _name):
        """Record that the service left the bus.

        Actions revive it through activation, and this flag
        keeps daemon_missing truthful for the windows that show it.
        """
        self.daemon_missing = True
        self._publish_presence()

    def _publish_presence(self, *args):
        """Tell the surfaces whether the service answers, and why not."""
        state = self.service_monitor.state if self.service_monitor else "unknown"
        snapshot = (not self.daemon_missing, state)
        if snapshot == self._last_presence:
            return
        self._last_presence = snapshot
        self.ui.apply_service_presence(*snapshot)

    def start_service(self, on_done):
        """Start the service by whichever path the unit state allows."""
        if self.service_monitor and self.service_monitor.state == "failed":
            self.service_monitor.start_service(on_done)
            return
        self.retry_daemon_start(on_done)

    def _follow_daemon_changes(self):
        """Rebuild lists when the owner reports a change.

        The change arrives over the bus and is repeated on the local
        managers, so every view keeps listening to what it always did.
        """
        self.daemon_client.subscribe(
            "MessagesChanged",
            lambda *args: GLib.idle_add(
                self._replay_change, 'messages-updated', args[5].unpack()[0], args[5].unpack()[1]))
        self.daemon_client.subscribe(
            "BlocklistChanged",
            lambda *args: GLib.idle_add(self._replay_change, 'blocklist-updated'))
        self.daemon_client.subscribe(
            "HistoryChanged",
            lambda *args: GLib.idle_add(self._replay_change, 'history-updated'))
        self.daemon_client.subscribe(
            "ContactsChanged",
            lambda *args: run_in_background(self.eds.reload_cache_from_db))

        if self.owns_incall_ui:
            self.daemon_client.subscribe(
                "RecoveryStateChanged",
                lambda *args: GLib.idle_add(self.ui.apply_recovery_state, *args[5].unpack()))
            self._seed_recovery_state()

    def _seed_recovery_state(self):
        """Ask the owner what the modem is doing right now.

        This window can start after the state changed, and a signal
        that already went out would leave its recovery page blank.
        """
        def task():
            return self.daemon_client.call(
                "GetRecoveryState", None, GLib.VariantType("(bsb)"))

        def done(state):
            if state:
                self.ui.apply_recovery_state(*state)

        run_in_background(task, on_complete=done)

    def _replay_change(self, name, *args):
        """Repeat on the local managers what the owner reported."""
        self.db.emit(name, *args)
        return False

    def request_auto_recovery(self, on_done=None):
        """Have the owner restart the modem stack; on_done hears the verdict."""
        def done(reply):
            if on_done:
                on_done(bool(reply and reply[0]))

        self.daemon_client.request_recovery(done)
        return True

    def open_modem_recovery(self):
        """Show the modem recovery screen with the current status."""
        self.ui.show_incall_ui()

    def clear_notification(self, number):
        """Tell the owner its alerts for a number were seen."""
        norm_num = normalize_number(number)
        self.daemon_client.clear_notification(norm_num)
        self.ui.withdraw_number_notifications(norm_num)
