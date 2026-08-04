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
from loguru import logger

from ...constants import DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE

DAEMON_CALL_TIMEOUT_MS = 30000


class DaemonClient:
    """Talks to the process that owns the modem.

    A window instance never touches the modem itself, because two
    processes acting on one arriving message would file it twice. It
    asks the owner to act and listens for what the owner reports.
    """

    def __init__(self):
        """Connect to the session bus."""
        self.bus = None
        self._subscriptions = []
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[DaemonClient] No session bus: {e}")

    def call(self, method, params=None, reply_type=None):
        """Ask the owner to do something; blocking, call from a worker.

        Returns the reply tuple, or None when the owner could not be
        reached, so a caller can tell refusal from silence.
        """
        if not self.bus:
            return None
        try:
            res = self.bus.call_sync(
                DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, method,
                params, reply_type, Gio.DBusCallFlags.NONE,
                DAEMON_CALL_TIMEOUT_MS, None)
            return res.unpack() if res else None
        except Exception as e:
            logger.error(f"[DaemonClient] {method} failed: {e}")
            return None

    def call_async(self, method, params=None):
        """Ask the owner to do something without waiting for the reply."""
        if not self.bus:
            return
        self.bus.call(
            DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, method,
            params, None, Gio.DBusCallFlags.NONE,
            DAEMON_CALL_TIMEOUT_MS, None, self._on_async_done, method)

    def _on_async_done(self, bus, result, method):
        """Log an asked action that the owner refused."""
        try:
            bus.call_finish(result)
        except Exception as e:
            logger.error(f"[DaemonClient] {method} failed: {e}")

    def subscribe(self, signal_name, handler):
        """Follow one thing the owner reports."""
        if not self.bus:
            return
        self._subscriptions.append(self.bus.signal_subscribe(
            DAEMON_BUS_NAME, DAEMON_INTERFACE, signal_name, DAEMON_OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, handler, None))

    def disconnect(self):
        """Stop following the owner."""
        if not self.bus:
            return
        for sub in self._subscriptions:
            self.bus.signal_unsubscribe(sub)
        self._subscriptions = []
