# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

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

    def __init__(self):
        self._max_brightness = None
        self.bus = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[Flashlight] No session bus: {e}")

    def set_on(self):
        if self._max_brightness is not None:
            self.set_brightness(self._max_brightness)
            return
        self.get_max(self.on_max)

    def set_off(self):
        self.set_brightness(0)

    def on_max(self, value):
        if value is None:
            return
        self._max_brightness = value
        self.set_brightness(value)

    def get_max(self, callback):
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
