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

from gi.repository import Gio, GObject
from telephony.backend.utils.log_utils import logger


class OfonoService(GObject.Object):
    """
    Monitors the status of the ofono service and modem availability.
    """
    __gsignals__ = {
        'status-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'modem-ready': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        """Initialize the Ofono Monitor."""
        super().__init__()
        self.connected = False
        self.modem_path = None
        self.manager_proxy = None

        bus_type = Gio.BusType.SYSTEM
        self.bus = Gio.bus_get_sync(bus_type, None)

        self.watcher_id = Gio.bus_watch_name(
            bus_type,
            "org.ofono",
            Gio.BusNameWatcherFlags.NONE,
            self._on_name_appeared,
            self._on_name_vanished
        )

    def _on_name_appeared(self, connection, name, _name_owner):
        """Handle service appearance."""
        logger.info(f"[Monitor] Service {name} appeared.")
        self._init_manager()

    def _on_name_vanished(self, connection, name):
        """Handle service disappearance."""
        logger.warning(f"[Monitor] Service {name} vanished.")
        self._handle_disconnection("Ofono service stopped")

    def _init_manager(self):
        """Initialize the ofono manager proxy."""
        self.manager_proxy = Gio.DBusProxy.new_sync(
            self.bus, Gio.DBusProxyFlags.NONE, None,
            "org.ofono", "/", "org.ofono.Manager", None)

        if self.manager_proxy:
            self.manager_proxy.connect("g-signal", self._on_manager_signal)
            self._scan_modems()
        else:
            self.emit('status-changed', 'error', "Could not get Manager proxy")

    def _on_manager_signal(self, proxy, sender, signal, params):
        """Handle signals from the ofono manager."""
        if signal == "ModemAdded":
            path, props = params.unpack()
            logger.info(f"[Monitor] Signal: ModemAdded {path}")
            self._handle_new_modem(path)
        elif signal == "ModemRemoved":
            path = params.unpack()[0]
            logger.info(f"[Monitor] Signal: ModemRemoved {path}")
            if self.modem_path == path:
                self._handle_disconnection("Modem removed")

    def _scan_modems(self):
        """Scan for existing modems."""
        result = self.manager_proxy.call_sync("GetModems", None, Gio.DBusCallFlags.NONE, -1, None)
        modems = result.unpack()[0]
        if modems:
            self._handle_new_modem(modems[0][0])
        else:
            self.emit('status-changed', 'searching', 'Waiting for modem...')

    def _handle_new_modem(self, path):
        """Register a new modem path."""
        if self.modem_path != path:
            self.modem_path = path
            self.connected = True
            logger.info(f"[Monitor] Modem Ready: {path}")
            self.emit('modem-ready', path)
            self.emit('status-changed', 'connected', 'Ready')

    def _handle_disconnection(self, reason):
        """Handle modem or service disconnection."""
        if self.connected or self.modem_path:
            logger.warning(f"[Monitor] Disconnected: {reason}")
            self.connected = False
            self.modem_path = None
            self.manager_proxy = None
            self.emit('status-changed', 'offline', reason)

    def stop(self):
        """Stop the monitor."""
        if self.watcher_id:
            Gio.bus_unwatch_name(self.watcher_id)
