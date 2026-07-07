# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from telephony.shared.utils.thread_utils import run_in_background

import subprocess
import time
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.ofono_utils import get_first_non_hfp_modem

from gi.repository import Gio, GLib

class DeviceLockManager:

    def lock_device(self, current_pin, new_pin, sudo_pw):
        run_in_background(self.task, current_pin, new_pin, sudo_pw)

    def task(self, current_pin, new_pin, sudo_pw):
        try:
            logger.warning("[DeviceLock] Initiating device lock sequence!")

            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            manager = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "org.ofono", "/", "org.ofono.Manager", None)
            result = manager.call_sync("GetModems", None, Gio.DBusCallFlags.NONE, -1, None)
            modems = result.unpack()[0]

            modem = get_first_non_hfp_modem(modems)
            if modem:
                modem_path, _props = modem
                sim_proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "org.ofono", modem_path, "org.ofono.SimManager", None)

                sim_proxy.call_sync("ChangePin", GLib.Variant("(sss)", ("pin", current_pin, new_pin)), Gio.DBusCallFlags.NONE, -1, None)
                time.sleep(2)
                sim_proxy.call_sync("LockPin", GLib.Variant("(ss)", ("pin", new_pin)), Gio.DBusCallFlags.NONE, -1, None)
                time.sleep(2)

            sudo_bytes = f"{sudo_pw}\n".encode()

            subprocess.run(
                ["sudo", "-S", "poweroff"],
                shell=False, check=False, input=sudo_bytes
            )
        except Exception as e:
            logger.error(f"[DeviceLock] Exec error: {e}")
