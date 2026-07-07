# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Gio, GLib, GObject

from telephony.shared.constants import NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE
from telephony.shared.utils.log_utils import logger


class NotificationService(GObject.Object):
    """
    Monitors DBus signals for notification actions and closures.
    """
    __gsignals__ = {
        "action-invoked": (GObject.SignalFlags.RUN_FIRST, None, (int, str)),
        "notification-closed": (GObject.SignalFlags.RUN_FIRST, None, (int, int))
    }

    def __init__(self):
        super().__init__()
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.subscribe()

    def subscribe(self):
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
        args = params.unpack()
        nid = args[0]
        action_key = args[1]
        self.emit("action-invoked", nid, action_key)

    def on_notification_closed(self, conn, sender, path, iface, signal, params, user_data):
        args = params.unpack()
        nid = args[0]
        reason = args[1]
        self.emit("notification-closed", nid, reason)

    def call_notify(self, params, callback):
        self.connection.call(
            NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
            "Notify", params, None, Gio.DBusCallFlags.NONE, -1, None,
            self.on_notify_done, callback
        )

    def on_notify_done(self, connection, result, callback):
        try:
            nid = connection.call_finish(result).unpack()[0]
        except GLib.Error as e:
            logger.warning(f"[NotificationService] Notify failed: {e}")
            nid = 0

        callback(nid)

    def call_close(self, nid):
        self.connection.call(
            NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
            "CloseNotification", GLib.Variant("(u)", (nid,)),
            None, Gio.DBusCallFlags.NONE, -1, None,
            self.on_close_done, None
        )

    def on_close_done(self, connection, result, _user_data):
        try:
            connection.call_finish(result)
        except GLib.Error as e:
            logger.debug(f"[NotificationService] CloseNotification failed: {e}")
