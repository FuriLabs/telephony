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

from telephony.backend.utils.log_utils import logger
from gi.repository import GLib, Gio
from ..services.notification_service import NotificationService

from gettext import gettext as _

INCALL_DESKTOP_ID = "io.furios.Telephony.Incall"
EMERGENCY_DESKTOP_ID = "io.furios.Telephony.Emergency"
PHOSH_NOTIFY_SCHEMA = "sm.puri.phosh.notifications"

RESPAWN_DELAY_MS = 1000


class LockScreenManager:
    """
    Manages lockscreen notifications and screen wakeup behavior for calls.
    """

    def __init__(self, ofono_manager, eds_manager, audio_manager, main_window):
        """Initialize the LockScreen Manager."""
        self.ofono = ofono_manager
        self.eds = eds_manager
        self.audio = audio_manager
        self.window = main_window

        self.monitor = NotificationService()
        self.monitor.connect('action-invoked', self._on_action_invoked_signal)
        self.monitor.connect('notification-closed', self._on_notification_closed_signal)

        self.phosh_settings = None
        self.saved_settings = {}
        try:
            self.phosh_settings = Gio.Settings(schema_id=PHOSH_NOTIFY_SCHEMA)
        except Exception as e:
            logger.warning(f"Schema {PHOSH_NOTIFY_SCHEMA} not found or failed to load: {e}")

        self.active_notifications = {}
        self.respawn_timers = {}
        self.is_locked = True
        self.self_closed_ids = set()

    def set_locked(self, locked):
        """Update the lock state."""
        self.is_locked = locked
        if not locked:
            if "stuck_modem" in self.active_notifications:
                self._close_notification("stuck_modem")

    def clear_all(self):
        """Clear all active notifications and restore settings."""
        for path in list(self.active_notifications.keys()):
            self._close_notification(path)
        for tid in self.respawn_timers.values():
            GLib.source_remove(tid)
        self.respawn_timers.clear()

        self._restore_screen_wakeup()

    def show_stuck_notification(self):
        """Show the Stuck Call / Modem Recovery notification."""
        if not self.is_locked:
            return

        self.clear_all()
        self._restore_screen_wakeup()

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

        key = "stuck_modem"
        current_id = self.active_notifications.get(key, 0)

        params = GLib.Variant('(susssasa{sv}i)', (
            display_app_name,
            current_id,
            icon,
            title,
            body,
            actions,
            hints,
            0
        ))
        nid = self.monitor.call_notify(params)
        if nid > 0:
            self.active_notifications[key] = nid

    def sync_notifications(self, calls, call_history, ignored_calls):
        """Sync notification state with active calls."""
        current_paths = set(calls.keys())
        active_paths = set(self.active_notifications.keys())

        if "stuck_modem" in active_paths:
            return

        for path in active_paths:
            if path not in current_paths or path in ignored_calls:
                self._close_notification(path)

        if self.is_locked:
            for p, d in calls.items():
                if p not in ignored_calls:
                    name = None
                    if p in call_history:
                        name = call_history[p]['name']
                        if name == _("Unknown"):
                            name = None

                    if not name:
                        contact_name = self.eds.get_contact_name(d['number'])
                        if contact_name and contact_name != "Unknown":
                            name = contact_name
                        elif d.get('number') and d.get('number') != "Unknown":
                            name = d['number']
                        else:
                            name = _("Unknown")

                    self._update_single_notification(p, d, name)

    def _update_single_notification(self, path, data, name):
        """Update or create a notification for a single call."""
        state = data['state']
        active_call_count = len(self.ofono.active_calls)

        should_wake = (state == 'incoming' and active_call_count == 1)

        if should_wake:
            self._restore_screen_wakeup()
            notif_category = 'call'
            notif_urgency = 2
        else:
            self._suppress_screen_wakeup()
            notif_category = 'im'
            notif_urgency = 0

        if path in self.respawn_timers:
            GLib.source_remove(self.respawn_timers[path])
            del self.respawn_timers[path]

        if path not in self.active_notifications:
            self.active_notifications[path] = 0

        current_id = self.active_notifications[path]

        title = name
        body = ""
        icon = "io.furios.Telephony"
        display_app_name = "Telephony"
        actions = []
        lockscreen_whitelist = []

        def add_action(act_id, label):
            secure_id = f"{act_id}::{path}"
            actions.append(secure_id)
            actions.append(label)
            lockscreen_whitelist.append(secure_id)

        has_multiple_calls = (active_call_count > 1)

        if state == 'incoming':
            display_app_name = _("Telephony - Incoming")
            title = _("🟢 Incoming: {name}").format(name=name)
            body = _("Incoming Call...")

            if has_multiple_calls:
                add_action("app.answer", _("🔁 Swap"))
            else:
                add_action("app.answer", _("🟢 Answer"))
                add_action("app.decline", _("🔴 Hangup"))

        elif state == 'waiting':
            display_app_name = _("Telephony - Incoming")
            title = _("🟢 Incoming: {name}").format(name=name)
            body = _("Waiting...")
            add_action("app.answer", _("🔁 Swap"))

        elif state == 'held':
            display_app_name = _("Telephony - On Hold")
            title = _("⏸️ {name} (On Hold)").format(name=name)
            body = _("Call On Hold...")
            add_action("app.swap", _("🔁 Resume"))
            add_action("app.decline", _("🔴 Hangup"))

        else:
            display_app_name = _("Telephony - Active Call")
            title = _("🟢 Active: {name}").format(name=name)
            body = _("Active Call")

            if has_multiple_calls:
                add_action("app.hangup", _("🔴 HangupAll"))
            else:
                add_action("app.hangup", _("🔴 Hangup"))

        hints = {
            'action-icons': GLib.Variant('b', True),
            'urgency': GLib.Variant('y', notif_urgency),
            'resident': GLib.Variant('b', True),
            'category': GLib.Variant('s', notif_category),
            'desktop-entry': GLib.Variant('s', INCALL_DESKTOP_ID),
            'X-Phosh-Lockscreen-Actions': GLib.Variant('as', lockscreen_whitelist)
        }

        try:
            params = GLib.Variant('(susssasa{sv}i)', (
                display_app_name,
                current_id,
                icon,
                title,
                body,
                actions,
                hints,
                0
            ))
            nid = self.monitor.call_notify(params)
            if nid > 0:
                self.active_notifications[path] = nid
        except Exception as e:
            logger.warning(f"[LockscreenManager] Exception: {e}")

    def _close_notification(self, path):
        """Close a specific notification."""
        if path in self.respawn_timers:
            GLib.source_remove(self.respawn_timers[path])
            del self.respawn_timers[path]

        if path not in self.active_notifications:
            return
        nid = self.active_notifications[path]
        del self.active_notifications[path]
        if nid:
            self.self_closed_ids.add(nid)
            self.monitor.call_close(nid)

        if not self.active_notifications:
            self._restore_screen_wakeup()

    def _suppress_screen_wakeup(self):
        """Suppress screen wakeup for non-critical updates."""
        if not self.phosh_settings:
            return
        if self.saved_settings:
            return

        keys = ["wakeup-screen-categories", "wakeup-screen-triggers", "wakeup-screen-urgency"]

        for key in keys:
            try:
                current_val = self.phosh_settings.get_value(key)
                self.saved_settings[key] = current_val

                if key == "wakeup-screen-categories":
                    self.phosh_settings.set_value(key, GLib.Variant('as', []))
                elif key == "wakeup-screen-triggers":
                    self.phosh_settings.set_value(key, GLib.Variant('as', []))
                elif key == "wakeup-screen-urgency":
                    type_str = current_val.get_type_string()
                    if type_str == 's':
                        self.phosh_settings.set_value(key, GLib.Variant('s', 'critical'))
                    elif type_str == 'i':
                        self.phosh_settings.set_value(key, GLib.Variant('i', 2))
            except Exception as e:
                logger.error(f"Error suppressing wakeup for {key}: {e}")

    def _restore_screen_wakeup(self):
        """Restore previous screen wakeup settings."""
        if not self.phosh_settings or not self.saved_settings:
            return

        for key, val in self.saved_settings.items():
            try:
                self.phosh_settings.set_value(key, val)
            except Exception as e:
                logger.error(f"Error restoring {key}: {e}")

        self.saved_settings.clear()

    def _on_action_invoked_signal(self, monitor, nid, full_action):
        """Handle action invocation from lockscreen notification."""
        target_path = None
        for p, id_ in self.active_notifications.items():
            if id_ == nid:
                target_path = p
                break
        if not target_path:
            return

        action_parts = full_action.split("::")
        action = action_parts[0]

        logger.info(f"Action {action} on {target_path}")
        has_multiple = (len(self.ofono.active_calls) > 1)

        if action == "app.answer":
            self.ofono.answer_call(target_path)
        elif action == "app.swap":
            self.ofono.swap_calls()
        elif action == "app.decline":
            if has_multiple:
                self.window.on_ignore_call(target_path)
            else:
                self._close_notification(target_path)
                self.window.on_hangup_click(None)
        elif action == "app.hangup":
            self._close_notification(target_path)
            self.window.on_hangup_click(None)
        elif action == "app.restart-modem":
            self.window.on_modem_recovery_click(None)

    def _on_notification_closed_signal(self, monitor, nid, reason):
        """Handle notification closed signal, respawning externally closed calls."""
        if nid in self.self_closed_ids:
            self.self_closed_ids.discard(nid)
            return

        target_path = None
        for p, id_ in self.active_notifications.items():
            if id_ == nid:
                target_path = p
                break

        if target_path and target_path not in self.respawn_timers:
            self.respawn_timers[target_path] = GLib.timeout_add(RESPAWN_DELAY_MS, self._scheduled_respawn, target_path)

    def _scheduled_respawn(self, path):
        """Respawn notification if still relevant."""
        if path in self.respawn_timers:
            del self.respawn_timers[path]

        if path == "stuck_modem":
            if self.is_locked and self.window.in_error_mode:
                self.show_stuck_notification()
            return False

        if self.is_locked and path in self.ofono.active_calls:
            d = self.ofono.active_calls[path]
            contact_name = self.eds.get_contact_name(d['number'])
            if contact_name and contact_name != "Unknown":
                name = contact_name
            elif d.get('number') and d.get('number') != "Unknown":
                name = d['number']
            else:
                name = _("Unknown")

            self.active_notifications[path] = 0
            self._update_single_notification(path, d, name)

        return False
