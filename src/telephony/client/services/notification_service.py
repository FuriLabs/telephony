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
from telephony.shared.utils.log_utils import logger


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
        self.subscribe()

    def subscribe(self):
        """Subscribe to DBus signals."""
        self.connection.signal_subscribe(
            NOTIFY_DBUS_NAME, NOTIFY_INTERFACE, "ActionInvoked",
            NOTIFY_DBUS_PATH, None, Gio.DBusSignalFlags.NONE,
            self.on_action_invoked, None
        )
        self.connection.signal_subscribe(
            NOTIFY_DBUS_NAME, NOTIFY_INTERFACE, "NotificationClosed",
            NOTIFY_DBUS_PATH, None, Gio.DBusSignalFlags.NONE,
            self.on_notification_closed, None
        )

    def on_action_invoked(self, conn, sender, path, iface, signal, params, user_data):
        """Handle ActionInvoked signal."""
        args = params.unpack()
        nid = args[0]
        action_key = args[1]
        self.emit('action-invoked', nid, action_key)

    def on_notification_closed(self, conn, sender, path, iface, signal, params, user_data):
        """Handle NotificationClosed signal."""
        args = params.unpack()
        nid = args[0]
        reason = args[1]
        self.emit('notification-closed', nid, reason)

    def call_notify(self, params, callback):
        """Call Notify and hand the server id to the callback, or 0 on failure."""
        self.connection.call(
            NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
            "Notify", params, None, Gio.DBusCallFlags.NONE, -1, None,
            self.on_notify_done, callback
        )

    def on_notify_done(self, connection, result, callback):
        """Deliver the server id for a finished Notify."""
        try:
            nid = connection.call_finish(result).unpack()[0]
        except GLib.Error as e:
            logger.warning(f"[NotificationService] Notify failed: {e}")
            nid = 0

        callback(nid)

    def call_close(self, nid):
        """Call the CloseNotification method."""
        self.connection.call(
            NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
            "CloseNotification", GLib.Variant('(u)', (nid,)),
            None, Gio.DBusCallFlags.NONE, -1, None,
            self.on_close_done, None
        )

    def on_close_done(self, connection, result, _user_data):
        """Report a withdrawal the server refused."""
        try:
            connection.call_finish(result)
        except GLib.Error as e:
            logger.debug(f"[NotificationService] CloseNotification failed: {e}")
