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

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib

from telephony.shared.utils.log_utils import logger

FLASHLIGHT_BUS = "io.furios.Flashlightd"
FLASHLIGHT_PATH = "/io/furios/Flashlightd"
FLASHLIGHT_IFACE = "io.furios.Flashlightd"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
FLASHLIGHT_CALL_TIMEOUT_MS = 2000


class FlashlightClient:
    """Drives the hardware torch through the flashlight daemon.

    Everything is asked on the bus asynchronously, since this runs on
    the UI path of a camera page. The daemon owns the LED; losing this
    process with the torch lit leaves it lit, which is why the camera
    pages turn it off on every way out rather than trusting teardown.
    """

    def __init__(self):
        """Initialize the client; nothing touches the bus yet."""
        self._max_brightness = None
        self.bus = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[Flashlight] No session bus: {e}")

    def set_on(self):
        """Light the torch at full brightness."""
        if self._max_brightness is not None:
            self.set_brightness(self._max_brightness)
            return
        self.get_max(self.on_max)

    def set_off(self):
        """Put the torch out; safe to repeat."""
        self.set_brightness(0)

    def on_max(self, value):
        """Remember the ceiling and light at it."""
        if value is None:
            return
        self._max_brightness = value
        self.set_brightness(value)

    def get_max(self, callback):
        """Ask the daemon for its brightness ceiling.

        The reply is a boxed variant, and unpack already opens the box
        along with the tuple: one unpack yields the number, a second
        would be asking an integer to unpack itself.
        """
        if not self.bus:
            callback(None)
            return

        def done(bus, result):
            try:
                res = bus.call_finish(result)
                callback(res.unpack()[0] if res else None)
            except Exception as e:
                logger.error(f"[Flashlight] Reading MaxBrightness failed: {e}")
                callback(None)

        self.bus.call(FLASHLIGHT_BUS, FLASHLIGHT_PATH, PROPERTIES_IFACE, "Get",
                      GLib.Variant("(ss)", (FLASHLIGHT_IFACE, "MaxBrightness")),
                      None, Gio.DBusCallFlags.NONE, FLASHLIGHT_CALL_TIMEOUT_MS, None, done)

    def set_brightness(self, value):
        """Command one brightness level, logging a refusal."""
        if not self.bus:
            return

        def done(bus, result):
            try:
                bus.call_finish(result)
            except Exception as e:
                logger.error(f"[Flashlight] SetBrightness({value}) failed: {e}")

        self.bus.call(FLASHLIGHT_BUS, FLASHLIGHT_PATH, FLASHLIGHT_IFACE, "SetBrightness",
                      GLib.Variant("(u)", (int(value),)),
                      None, Gio.DBusCallFlags.NONE, FLASHLIGHT_CALL_TIMEOUT_MS, None, done)
