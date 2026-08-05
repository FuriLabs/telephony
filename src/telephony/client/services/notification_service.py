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

from gi.repository import Gio, GLib, GObject

from telephony.shared.constants import NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE


class NotificationService(GObject.Object):
    """
    Monitors DBus signals for notification actions and closures.
    """
    __gsignals__ = {
        'action-invoked': (GObject.SignalFlags.RUN_FIRST, None, (int, str)),
        'notification-closed': (GObject.SignalFlags.RUN_FIRST, None, (int, int))
    }

    def __init__(self):
        """Initialize the Notification Monitor."""
        super().__init__()
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._subscribe()

    def _subscribe(self):
        """Subscribe to DBus signals."""
        self.connection.signal_subscribe(
            NOTIFY_DBUS_NAME, NOTIFY_INTERFACE, "ActionInvoked",
            NOTIFY_DBUS_PATH, None, Gio.DBusSignalFlags.NONE,
            self._on_action_invoked, None
        )
        self.connection.signal_subscribe(
            NOTIFY_DBUS_NAME, NOTIFY_INTERFACE, "NotificationClosed",
            NOTIFY_DBUS_PATH, None, Gio.DBusSignalFlags.NONE,
            self._on_notification_closed, None
        )

    def _on_action_invoked(self, conn, sender, path, iface, signal, params, user_data):
        """Handle ActionInvoked signal."""
        args = params.unpack()
        nid = args[0]
        action_key = args[1]
        self.emit('action-invoked', nid, action_key)

    def _on_notification_closed(self, conn, sender, path, iface, signal, params, user_data):
        """Handle NotificationClosed signal."""
        args = params.unpack()
        nid = args[0]
        reason = args[1]
        self.emit('notification-closed', nid, reason)

    def call_notify(self, params):
        """Call the Notify method on the notification service."""
        result = self.connection.call_sync(
            NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
            "Notify", params, None, Gio.DBusCallFlags.NONE, -1, None
        )
        return result.unpack()[0]

    def call_close(self, nid):
        """Call the CloseNotification method."""
        self.connection.call_sync(
            NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
            "CloseNotification", GLib.Variant('(u)', (nid,)),
            None, Gio.DBusCallFlags.NONE, -1, None
        )
