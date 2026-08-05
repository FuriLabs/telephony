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

from gi.repository import GObject, Gio, GLib
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.thread_utils import run_in_background

SYSTEMD_BUS = "org.freedesktop.systemd1"
SYSTEMD_PATH = "/org/freedesktop/systemd1"
SYSTEMD_MANAGER_IFACE = "org.freedesktop.systemd1.Manager"
SYSTEMD_UNIT_IFACE = "org.freedesktop.systemd1.Unit"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
UNIT_NAME = "telephony.service"
UNIT_PATH = "/org/freedesktop/systemd1/unit/telephony_2eservice"


class ServiceMonitor(GObject.Object):
    """Follows the telephony unit's systemd state, push-driven.

    The bus name watch says only present or gone; this adds the why:
    restarting on its own, stopped and waiting for activation, or
    parked as failed — the one state where activation is refused
    until the unit is reset.
    """

    __gsignals__ = {
        'unit-state-changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__()
        self.state = "unknown"
        self._active_state = ""
        self._sub_state = ""
        self.bus = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[ServiceMonitor] No session bus: {e}")
            return

        self.bus.call(SYSTEMD_BUS, SYSTEMD_PATH, SYSTEMD_MANAGER_IFACE, "Subscribe",
                      None, None, Gio.DBusCallFlags.NONE, -1, None, self._on_subscribed)
        self.bus.signal_subscribe(
            SYSTEMD_BUS, PROPERTIES_IFACE, "PropertiesChanged", UNIT_PATH,
            None, Gio.DBusSignalFlags.NONE, self._on_unit_properties, None)
        self._read_state()

    def _on_subscribed(self, bus, result):
        """Log when systemd refuses the change subscription."""
        try:
            bus.call_finish(result)
        except Exception as e:
            logger.warning(f"[ServiceMonitor] systemd subscribe failed: {e}")

    def _read_state(self):
        """Seed the unit state; the signal covers everything after."""
        def fetch():
            res = self.bus.call_sync(
                SYSTEMD_BUS, UNIT_PATH, PROPERTIES_IFACE, "GetAll",
                GLib.Variant("(s)", (SYSTEMD_UNIT_IFACE,)),
                GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE, -1, None)
            return res.unpack()[0]

        def apply(props):
            if props:
                self._apply_props(props)

        run_in_background(fetch, on_complete=apply,
                          on_error=lambda e: logger.debug(f"[ServiceMonitor] Unit read failed: {e}"))

    def _on_unit_properties(self, *args):
        iface, changed, _invalidated = args[5].unpack()
        if iface == SYSTEMD_UNIT_IFACE and changed:
            self._apply_props(changed)

    def _apply_props(self, props):
        """Fold systemd's two state fields into one answer for the UI."""
        self._active_state = props.get("ActiveState", self._active_state)
        self._sub_state = props.get("SubState", self._sub_state)

        if self._active_state == "failed":
            state = "failed"
        elif self._active_state == "activating" and self._sub_state == "auto-restart":
            state = "restarting"
        elif self._active_state in ("inactive", "deactivating"):
            state = "stopped"
        elif self._active_state == "active":
            state = "running"
        else:
            state = self._active_state or "unknown"

        if state != self.state:
            self.state = state
            logger.info(f"[ServiceMonitor] telephony.service is {state}")
            GLib.idle_add(self.emit, 'unit-state-changed', state)

    def start_service(self, on_done):
        """Reset a failed unit and start it; on_done hears whether it worked.

        A failed unit refuses bus activation until ResetFailedUnit, so
        this is the only start path that always works.
        """
        def task():
            try:
                self.bus.call_sync(
                    SYSTEMD_BUS, SYSTEMD_PATH, SYSTEMD_MANAGER_IFACE, "ResetFailedUnit",
                    GLib.Variant("(s)", (UNIT_NAME,)), None, Gio.DBusCallFlags.NONE, -1, None)
            except Exception as e:
                logger.debug(f"[ServiceMonitor] ResetFailedUnit: {e}")
            try:
                self.bus.call_sync(
                    SYSTEMD_BUS, SYSTEMD_PATH, SYSTEMD_MANAGER_IFACE, "StartUnit",
                    GLib.Variant("(ss)", (UNIT_NAME, "replace")), None,
                    Gio.DBusCallFlags.NONE, -1, None)
                return True
            except Exception as e:
                logger.error(f"[ServiceMonitor] StartUnit failed: {e}")
                return False

        run_in_background(task, on_complete=on_done)
