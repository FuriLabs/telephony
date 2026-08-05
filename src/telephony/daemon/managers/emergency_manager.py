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

from telephony.shared.utils.log_utils import logger
from gi.repository import GLib, Gio
from telephony.daemon.services.emergency_service import EmergencyService

from gettext import gettext as _

from telephony.shared.constants import (EMERGENCY_APP_ID, NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE)


SELECTION_REVERT_MS = 15000
CONFIRM_REVERT_MS = 10000


class EmergencyManager:
    """
    Manages emergency calls and lockscreen emergency interface.
    """

    def __init__(self, ofono_manager, db_manager, gsettings_mgr=None, notification_manager=None):
        """Initialize the Emergency Manager."""
        self.ofono = ofono_manager
        self.db = db_manager
        self.gsettings_mgr = gsettings_mgr
        self.notification_manager = notification_manager
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        self.active_notifications = {}
        self.current_state = "IDLE"

        self.is_locked = False
        self.feature_enabled = True
        self.calls_active = False

        self.respawn_id = None
        self._revert_timer_id = None

        self.monitor = EmergencyService()
        self.monitor.connect('lock-state-changed', self._on_lock_state_changed)
        self.monitor.connect('feature-enabled-changed', self._on_feature_enabled_changed)

        if self.notification_manager:
            self.notification_manager.connect('action-invoked', self._on_action_invoked_signal)
            self.notification_manager.connect('notification-closed', self._on_notification_closed_signal)

        self.ofono.connect('call-added', self._on_call_status_changed)
        self.ofono.connect('call-removed', self._on_call_status_changed)

        self.is_locked = self.monitor.is_locked
        self.feature_enabled = self.monitor.get_feature_enabled()

        self._evaluate_state()

    def _on_feature_enabled_changed(self, monitor, enabled):
        """Handle feature enabled change."""
        self.feature_enabled = enabled
        self._evaluate_state()

    def _on_lock_state_changed(self, monitor, is_locked):
        """Handle lock state change."""
        self.is_locked = is_locked
        self._evaluate_state()

    def _on_call_status_changed(self, *args):
        """Handle call status change."""
        has_calls = len(self.ofono.active_calls) > 0
        if has_calls != self.calls_active:
            self.calls_active = has_calls
            self._evaluate_state()

    def _evaluate_state(self):
        """Determine and transition to the correct state."""
        if self.calls_active or not self.feature_enabled:
            self._clear_all()
            self.current_state = "IDLE"
            return

        if not self.is_locked:
            if self.current_state != "IDLE":
                self._clear_all()
                self.current_state = "IDLE"
            return

        if self.is_locked:
            if self.current_state == "IDLE":
                self._show_guardian()
            elif self.current_state == "GUARDIAN":
                if not self.active_notifications and self.respawn_id is None:
                    self._show_guardian()

    def _load_numbers(self):
        """Load configured emergency numbers merged with the network list."""
        return self.ofono.get_emergency_numbers()

    def _show_guardian(self):
        """Show the initial guardian notification."""
        if self.calls_active:
            return
        if self.respawn_id:
            GLib.source_remove(self.respawn_id)
            self.respawn_id = None

        self._clear_all()
        self.current_state = "GUARDIAN"
        self._send_notif(
            "guardian", _("Emergency Mode"), _("Emergency calls only."),
            "io.furios.Telephony.Emergency", ["app.emergency_menu::", _("Start Emergency Process")]
        )

    def _show_selection(self):
        """Show the list of emergency numbers."""
        if self.calls_active:
            return
        self._clear_all()
        self.current_state = "SELECTION"

        numbers = self._load_numbers()

        self._send_notif(
            "back_btn", _("Cancel"), _("Return to Lockscreen"),
            "io.furios.Telephony.Emergency", ["app.emergency_cancel::", _("Back")]
        )

        if not numbers:
            self._send_notif("error", _("No Numbers Configured"), _("Set numbers in Settings."), "dialog-error-symbolic", [])
            return

        for item in numbers:
            name = item.get("name", "Unknown")
            number = item.get("number", "")
            if not number:
                continue

            action_id = f"app.emergency_dial::{number}::{name}"
            self._send_notif(
                f"contact_{number}", name, _("Call {number}").format(number=number),
                "io.furios.Telephony.Emergency", [action_id, f"🟢 {name}"]
            )

        self._arm_revert_timer(SELECTION_REVERT_MS, "SELECTION")

    def _show_confirmation(self, number, name):
        """Show confirmation dialog before dialing."""
        if self.calls_active:
            return
        self._clear_all()
        self.current_state = "CONFIRM"

        self._send_notif(
            "confirm", _("Confirm Emergency Call"), _("Call {name}?").format(name=name),
            "io.furios.Telephony.Emergency",
            [f"app.emergency_confirm::{number}", _("🟢 Yes, Call"), "app.emergency_cancel::", _("🔴 No, Return")]
        )

        self._arm_revert_timer(CONFIRM_REVERT_MS, "CONFIRM")

    def _arm_revert_timer(self, interval_ms, state):
        """Arm the revert timer, replacing any pending one."""
        if self._revert_timer_id:
            GLib.source_remove(self._revert_timer_id)
        self._revert_timer_id = GLib.timeout_add(interval_ms, self._run_revert, state)

    def _run_revert(self, originating_state):
        """Run the revert timer callback once."""
        self._revert_timer_id = None
        self._timeout_revert(originating_state)
        return False

    def _timeout_revert(self, originating_state):
        """Revert to guardian state on timeout."""
        if self.current_state == originating_state and self.is_locked and not self.calls_active:
            self._show_guardian()
        return False

    def _send_notif(self, internal_id, title, body, icon, actions):
        """Send a notification."""
        lockscreen_whitelist = actions[0::2]
        hints = {
            'action-icons': GLib.Variant('b', True),
            'urgency': GLib.Variant('y', 0),
            'resident': GLib.Variant('b', True),
            'category': GLib.Variant('s', 'system'),
            'desktop-entry': GLib.Variant('s', EMERGENCY_APP_ID),
            'X-Phosh-Lockscreen-Actions': GLib.Variant('as', lockscreen_whitelist),
            'suppress-sound': GLib.Variant('b', True),
            'sound-name': GLib.Variant('s', 'telephony-silent'),
            'feedback-event': GLib.Variant('s', 'telephony-silent'),
            'feedback-profile': GLib.Variant('s', 'silent')
        }

        replaces_id = self.active_notifications.get(internal_id, 0)

        try:
            params = GLib.Variant('(susssasa{sv}i)', (
                "Emergency", replaces_id, icon, title, body, actions, hints, 0
            ))
            result = self.connection.call_sync(
                NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
                "Notify", params, None, Gio.DBusCallFlags.NONE, -1, None
            )
            self.active_notifications[internal_id] = result.unpack()[0]
        except Exception as e:
            logger.error(f"[Emergency] Notify Failed: {e}")

    def _clear_all(self):
        """Clear all active notifications."""
        for nid in self.active_notifications.values():
            try:
                self.connection.call_sync(
                    NOTIFY_DBUS_NAME, NOTIFY_DBUS_PATH, NOTIFY_INTERFACE,
                    "CloseNotification", GLib.Variant('(u)', (nid,)), None, Gio.DBusCallFlags.NONE, -1, None
                )
            except Exception as e:
                logger.warning(f"[Emergency] Clear Notif Warning: {e}")
        self.active_notifications.clear()

    def _on_action_invoked_signal(self, manager, nid, action_key):
        """Handle notification action invoked signal."""
        action_payload = f"{nid}::{action_key}"
        parts = action_payload.split("::")
        if len(parts) < 2:
            return
        nid = int(parts[0])
        action_string = "::".join(parts[1:])

        if nid not in self.active_notifications.values():
            return

        logger.info(f"[Emergency] Action: {action_string}")
        action_parts = action_string.split("::")
        key = action_parts[0]

        if key == "app.emergency_menu":
            self._show_selection()
        elif key == "app.emergency_cancel":
            if self.current_state == "SELECTION":
                self._show_guardian()
            else:
                self._show_selection()
        elif key == "app.emergency_dial":
            if len(action_parts) >= 3:
                self._show_confirmation(action_parts[1], action_parts[2])
        elif key == "app.emergency_confirm":
            if len(action_parts) >= 2:
                number = action_parts[1]
                logger.info(f"[Emergency] DIALING {number}")
                self._clear_all()
                self.current_state = "IDLE"
                self.ofono.dial(number)

    def _on_notification_closed_signal(self, monitor, nid, reason):
        """Handle notification closed signal."""
        keys_to_remove = [k for k, v in self.active_notifications.items() if v == nid]
        for k in keys_to_remove:
            del self.active_notifications[k]

        should_respawn = False

        if self.is_locked and not self.calls_active:
            if self.current_state == "GUARDIAN" and not self.active_notifications:
                should_respawn = True
            elif self.current_state in ["SELECTION", "CONFIRM"] and not self.active_notifications:
                self.current_state = "IDLE"
                should_respawn = True

        if should_respawn and self.respawn_id is None:
            self.respawn_id = GLib.timeout_add(1000, self._scheduled_respawn)

    def _scheduled_respawn(self):
        """Respawn the guardian notification."""
        self.respawn_id = None
        self._evaluate_state()
        return False
