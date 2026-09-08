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

from telephony.shared.utils.thread_utils import run_in_background

import subprocess
import time
from telephony.shared.utils.log_utils import logger

from gi.repository import Gio, GLib

class DeviceLockManager:
    """Manages the device locking sequence triggered by SMS."""

    def lock_device(self, current_pin, new_pin, sudo_pw):
        """Initiates the device lock sequence asynchronously."""
        run_in_background(self.task, current_pin, new_pin, sudo_pw)

    def task(self, current_pin, new_pin, sudo_pw):
        try:
            logger.warning("[DeviceLock] Initiating device lock sequence!")

            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            manager = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "org.ofono", "/", "org.ofono.Manager", None)
            result = manager.call_sync("GetModems", None, Gio.DBusCallFlags.NONE, -1, None)
            modems = result.unpack()[0]

            if modems:
                modem_path = modems[0][0]
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
