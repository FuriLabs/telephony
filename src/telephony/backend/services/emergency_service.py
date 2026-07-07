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

from telephony.backend.services.system_state_service import SystemStateService
from loguru import logger

NOTIFY_DBUS_NAME = "org.freedesktop.Notifications"
NOTIFY_DBUS_PATH = "/org/freedesktop/Notifications"
NOTIFY_INTERFACE = "org.freedesktop.Notifications"


class EmergencyService(GObject.Object):
    """
    Monitors system state (lock status, settings) relevant to emergency mode.
    """
    __gsignals__ = {
        'lock-state-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        'feature-enabled-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        'action-invoked': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'notification-closed': (GObject.SignalFlags.RUN_FIRST, None, (int,))
    }

    def __init__(self):
        super().__init__()
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        self.sys_state = SystemStateService()
        self.is_locked = self.sys_state.is_locked
        self.sys_state.connect("lock-state-changed", self._on_lock_changed)

        self.feature_enabled = True
        self.settings = None

        self._init_settings_monitor()

    def _init_settings_monitor(self):
        """Initialize GSettings monitor."""
        try:
            source = Gio.SettingsSchemaSource.get_default()
            if not source:
                return
            schema = source.lookup("sm.puri.phosh.emergency-calls", True)
            if not schema or not schema.has_key("enabled"):
                logger.debug("sm.puri.phosh.emergency-calls schema or enabled key missing")
                return

            self.settings = Gio.Settings.new("sm.puri.phosh.emergency-calls")
            self.settings.connect("changed::enabled", self._on_settings_changed)
        except Exception as e:
            logger.warning(f"[EmergencyService] Failed to init settings monitor: {e}")

    def get_feature_enabled(self):
        """Check if emergency calls feature is enabled."""
        if self.settings:
            try:
                return self.settings.get_boolean("enabled")
            except Exception as e:
                logger.warning(f"[EmergencyService] Failed to get feature enabled: {e}")
        return True

    def _on_settings_changed(self, settings, key):
        """Handle settings change."""
        try:
            self.emit('feature-enabled-changed', settings.get_boolean("enabled"))
        except Exception as e:
            logger.warning(f"[EmergencyService] Settings changed error: {e}")

    def _on_lock_changed(self, monitor, is_locked):
        self.is_locked = is_locked
        GLib.idle_add(self.emit, 'lock-state-changed', self.is_locked)
