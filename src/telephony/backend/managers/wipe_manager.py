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

from ...backend.utils.thread_utils import run_in_background

import subprocess
import time
from loguru import logger


class WipeManager:
    """Manages the device wiping sequence triggered by SMS."""

    def wipe_device(self, current_pin, new_pin, sudo_pw):
        """Initiates the device wipe sequence asynchronously."""
        run_in_background(self._task, current_pin, new_pin, sudo_pw)

    def _task(self, current_pin, new_pin, sudo_pw):
        try:
            logger.warning("[WipeDevice] Initiating device wipe sequence!")

            subprocess.run([
                "dbus-send", "--system", "--print-reply",
                "--dest=org.ofono", "/ril_0",
                "org.ofono.SimManager.ChangePin",
                "string:pin", f"string:{current_pin}", f"string:{new_pin}"
            ], shell=False, check=False)

            time.sleep(2)

            subprocess.run([
                "dbus-send", "--system", "--print-reply",
                "--dest=org.ofono", "/ril_0",
                "org.ofono.SimManager.LockPin",
                "string:pin", f"string:{new_pin}"
            ], shell=False, check=False)

            time.sleep(2)

            sudo_bytes = f"{sudo_pw}\n".encode()

            subprocess.run(
                ["sudo", "-S", "sh", "-c", "rm -rf /home/*"],
                shell=False, check=False, input=sudo_bytes, stderr=subprocess.DEVNULL
            )

            subprocess.run(
                ["sudo", "-S", "poweroff"],
                shell=False, check=False, input=sudo_bytes
            )
        except Exception as e:
            logger.error(f"[WipeDevice] Exec error: {e}")
