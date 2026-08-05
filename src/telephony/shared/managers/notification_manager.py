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
import re
import gi
from telephony.shared.utils.log_utils import logger
from gi.repository import Gio, GLib, GObject

from telephony.shared.managers.audio_manager import TelephonyAudioManager
from telephony.shared.constants import APP_ID, NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE

gi.require_version('Lfb', '0.0')
from gi.repository import Lfb


class NotificationManager(GObject.Object):
    """
    Manages sending generic desktop notifications.
    """
    __gsignals__ = {
        'action-invoked': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'notification-closed': (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
    }

    def __init__(self):
        """Initialize the Notification Manager."""
        super().__init__()
        try:
            Lfb.init(APP_ID)
        except Exception as e:
            logger.error(f"[NotificationManager] Lfb init failed: {e}")

        self.audio = TelephonyAudioManager()

        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        self.active_notifications = {}
        self.default_actions = {}

        if self.connection:
            try:
                self.connection.signal_subscribe(
                    "org.freedesktop.Notifications",
                    "org.freedesktop.Notifications",
                    "ActionInvoked",
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._on_action_invoked,
                    None
                )
                self.connection.signal_subscribe(
                    "org.freedesktop.Notifications",
                    "org.freedesktop.Notifications",
                    "NotificationClosed",
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._on_notification_closed,
                    None
                )
            except Exception as e:
                logger.error(f"Failed to subscribe to ActionInvoked: {e}")

    def _on_notification_closed(self, conn, sender, path, iface, signal, params, user_data):
        """Handle notification close event."""
        nid, reason = params.unpack()
        self.emit('notification-closed', str(nid), reason)

    def _on_action_invoked(self, conn, sender, path, iface, signal, params, user_data):
        """Handle notification action invocation."""
        nid, action_key = params.unpack()

        self.emit('action-invoked', str(nid), action_key)

        target_action = action_key
        if action_key == "default" and nid in self.default_actions:
            target_action = self.default_actions[nid]

        if target_action and target_action.startswith("app."):
            app = Gio.Application.get_default()
            if not app:
                return
            m = re.match(r"^app\.([a-zA-Z0-9-]+)\('(.*)'\)$", target_action)
            if m:
                app.activate_action(m.group(1), GLib.Variant("s", m.group(2)))
            elif re.match(r"^app\.[a-zA-Z0-9-]+$", target_action):
                app.activate_action(target_action[4:], None)

    def trigger_sound_event(self, event_name, sound_file=None):
        """Trigger feedback event with optional custom sound file."""
        try:
            event = Lfb.Event.new(event_name)
            if sound_file:
                if os.path.exists(sound_file):
                    event.set_sound_file(sound_file)

            event.trigger_feedback_async(None, None, None)
        except Exception as e:
            logger.error(f"[NotificationManager] Trigger sound event failed: {e}")

    def send_notification(self, id_key, title, body, app_id_hint=APP_ID, actions=None, priority=1, sound_file=None):
        """Send a notification via DBus."""
        if not self.connection:
            return

        if id_key in self.active_notifications:
            self.close_notification(id_key)

        replaces_id = 0

        actions_list = []
        if actions:
            if "default" in actions:
                actions_list.append("default")
                actions_list.append(actions["default"])
            for k, v in actions.items():
                if k != "default":
                    actions_list.append(k)
                    actions_list.append(v)

        hints = {
            "urgency": GLib.Variant("y", 1 if priority > 0 else 0),
            "desktop-entry": GLib.Variant("s", app_id_hint),
            "category": GLib.Variant("s", "im.received")
        }

        if sound_file:
            logger.debug(f"[NotificationManager] Request to play sound: {sound_file}")
            if os.path.exists(sound_file):
                self.trigger_sound_event("message-new-sms", sound_file)

                hints['suppress-sound'] = GLib.Variant('b', True)
                hints['sound-name'] = GLib.Variant('s', 'none')
            else:
                logger.warning(f"[NotificationManager] Sound file not found: {sound_file}")
        elif app_id_hint == "io.furios.Telephony.Messages":
            self.trigger_sound_event("message-new-sms")
            hints['suppress-sound'] = GLib.Variant('b', True)
            hints['sound-name'] = GLib.Variant('s', 'none')

        try:
            logger.debug(f"[NotificationManager] Sending notification '{title}' with hints: {hints}")
            params = GLib.Variant(
                "(susssasa{sv}i)",
                (
                    "Telephony",
                    replaces_id,
                    app_id_hint,
                    title,
                    body,
                    actions_list,
                    hints,
                    -1
                )
            )

            res = self.connection.call_sync(
                NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
                "Notify", params, None, Gio.DBusCallFlags.NONE, -1, None
            )

            new_id = res.unpack()[0]
            self.active_notifications[id_key] = new_id
            if actions and "default" in actions:
                self.default_actions[new_id] = actions["default"]

        except Exception as e:
            logger.error(f"Failed to send DBus notification: {e}")

    def close_notification(self, id_key):
        """Close a specific notification by internal key."""
        if not self.connection or id_key not in self.active_notifications:
            return

        nid = self.active_notifications[id_key]
        try:
            self.connection.call_sync(
                NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
                "CloseNotification", GLib.Variant("(u)", (nid,)),
                None, Gio.DBusCallFlags.NONE, -1, None
            )
        except Exception as e:
            logger.warning(f"Close notification warning: {e}")

        if nid in self.default_actions:
            del self.default_actions[nid]
        del self.active_notifications[id_key]

    def clear_all(self):
        """Close all active notifications."""
        keys = list(self.active_notifications.keys())
        for k in keys:
            self.close_notification(k)
