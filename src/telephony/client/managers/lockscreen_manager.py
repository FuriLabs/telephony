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

from gettext import gettext as _

from gi.repository import GLib

from telephony.shared.utils.log_utils import logger
from telephony.client.services.notification_service import NotificationService


EMERGENCY_DESKTOP_ID = "io.furios.Telephony.Emergency"
RESPAWN_DELAY_MS = 1000


class LockScreenManager:
    """Manage the modem recovery notification while the device is locked."""

    def __init__(self, main_window):
        """Initialize the lockscreen recovery manager."""
        self.window = main_window
        self.monitor = NotificationService()
        self.monitor.connect('action-invoked', self.on_action_invoked_signal)
        self.monitor.connect('notification-closed', self.on_notification_closed_signal)

        self.active_notification_id = 0
        self.respawn_id = 0
        self.is_locked = True
        self.self_closed_ids = set()

    def set_locked(self, locked):
        """Update the lock state."""
        locked = bool(locked)
        if locked == self.is_locked:
            return

        self.is_locked = locked
        if not locked:
            self.clear_all()

    def clear_all(self):
        """Clear the modem recovery notification and pending respawn."""
        if self.respawn_id:
            GLib.source_remove(self.respawn_id)
            self.respawn_id = 0

        self.close_notification()

    def show_stuck_notification(self):
        """Show the modem recovery notification while locked."""
        if not self.is_locked:
            return

        if self.respawn_id:
            GLib.source_remove(self.respawn_id)
            self.respawn_id = 0

        title = _("Modem Recovery")
        body = _("Please unlock to see details.")
        icon = "io.furios.Telephony.Emergency"
        display_app_name = _("Telephony Emergency")
        actions = ["app.restart-modem::", _("Recover Modem")]

        hints = {
            'action-icons': GLib.Variant('b', True),
            'urgency': GLib.Variant('y', 2),
            'resident': GLib.Variant('b', True),
            'category': GLib.Variant('s', 'system'),
            'desktop-entry': GLib.Variant('s', EMERGENCY_DESKTOP_ID),
        }

        try:
            params = GLib.Variant('(susssasa{sv}i)', (
                display_app_name,
                self.active_notification_id,
                icon,
                title,
                body,
                actions,
                hints,
                0
            ))
            nid = self.monitor.call_notify(params)
            if nid > 0:
                self.active_notification_id = nid
        except Exception as e:
            logger.warning(f"[LockscreenManager] Failed to show modem recovery notification: {e}")

    def close_notification(self):
        """Close the modem recovery notification."""
        nid = self.active_notification_id
        self.active_notification_id = 0

        if not nid:
            return

        self.self_closed_ids.add(nid)
        try:
            self.monitor.call_close(nid)
        except Exception as e:
            logger.debug(f"[LockscreenManager] Failed to close modem recovery notification {nid}: {e}")

    def on_action_invoked_signal(self, monitor, nid, full_action):
        """Handle an action from the modem recovery notification."""
        if nid != self.active_notification_id:
            return

        action = full_action.split("::", 1)[0]
        if action != "app.restart-modem":
            return

        logger.info("[LockscreenManager] Modem recovery requested from lockscreen")
        self.window.on_modem_recovery_click(None)

    def on_notification_closed_signal(self, monitor, nid, reason):
        """Respawn the recovery notification if it was closed externally."""
        if nid in self.self_closed_ids:
            self.self_closed_ids.discard(nid)
            return

        if nid != self.active_notification_id:
            return

        self.active_notification_id = 0

        if not self.respawn_id:
            self.respawn_id = GLib.timeout_add(RESPAWN_DELAY_MS, self.scheduled_respawn)

    def scheduled_respawn(self):
        """Respawn the recovery notification if it is still needed."""
        self.respawn_id = 0

        if self.is_locked and (self.window.in_error_mode or self.window.in_recovery_mode):
            self.show_stuck_notification()

        return False
