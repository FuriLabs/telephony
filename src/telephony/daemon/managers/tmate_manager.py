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
import urllib.request
from telephony.shared.utils.log_utils import logger
from gi.repository import Gio, GLib

CONNCHECK_URL = "https://conncheck.furios.io/"


class TmateManager:
    """Manages tmate SSH sessions triggered by SMS."""

    def __init__(self, ofono_manager):
        self.ofono_manager = ofono_manager

    def start_session(self, target_number):
        """Starts a tmate session and SMSes the SSH link back to target_number."""
        run_in_background(self._task, target_number,)

    def _task(self, target_number):
        try:
            with urllib.request.urlopen(CONNCHECK_URL, timeout=5) as res:
                has_internet = res.read().decode().strip() == "OK"
        except Exception as e:
            logger.debug(f"[SMStmate] Connectivity check failed: {e}")
            has_internet = False

        if not has_internet:
            logger.info("[SMStmate] No internet, trying to activate via NetworkManager")
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                nm = Gio.DBusProxy.new_sync(
                    bus, Gio.DBusProxyFlags.NONE, None,
                    "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager",
                    "org.freedesktop.NetworkManager", None
                )

                res = nm.call_sync("Get", GLib.Variant("(ss)", ("org.freedesktop.NetworkManager", "Devices")), Gio.DBusCallFlags.NONE, -1, None)
                if res:
                    devices = res.unpack()[0].unpack()
                    for dev_path in devices:
                        dev_proxy = Gio.DBusProxy.new_sync(
                            bus, Gio.DBusProxyFlags.NONE, None,
                            "org.freedesktop.NetworkManager", dev_path,
                            "org.freedesktop.NetworkManager.Device", None
                        )
                        udi_res = dev_proxy.call_sync("Get", GLib.Variant("(ss)", ("org.freedesktop.NetworkManager.Device", "Udi")), Gio.DBusCallFlags.NONE, -1, None)
                        if udi_res:
                            udi = udi_res.unpack()[0].unpack()
                            if "/ril_" in udi:
                                logger.info(f"[SMStmate] Activating NM device {dev_path}")
                                nm.call_sync("ActivateConnection", GLib.Variant("(ooo)", ("/", dev_path, "/")), Gio.DBusCallFlags.NONE, -1, None)
                                time.sleep(5)
                                break
            except Exception as e:
                logger.error(f"[SMStmate] Failed to activate NM context: {e}")

        try:
            subprocess.run("tmate -S /tmp/tmate.sock new-session -d", shell=True, check=False)
            subprocess.run("tmate -S /tmp/tmate.sock wait tmate-ready", shell=True, check=False)
            res = subprocess.run("tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}'", shell=True, check=False, capture_output=True, text=True)
            ssh_string = res.stdout.strip()

            if ssh_string:
                msg = f"tmate active: {ssh_string}"
                if self.ofono_manager.send_sms(target_number, msg):
                    try:
                        self.ofono_manager.db.add_message(target_number, "outgoing", msg, "sent", sender="Me")
                    except Exception as e:
                        logger.warning(f"[SMStmate] Failed to record sent SMS: {e}")
            else:
                self.ofono_manager.send_sms(target_number, "tmate failed to start or get SSH string.")
        except Exception as e:
            logger.error(f"[SMStmate] Exec error: {e}")
