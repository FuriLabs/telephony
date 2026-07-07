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
import urllib.request
from loguru import logger
from gi.repository import Gio, GLib


class TmateManager:
    """Manages tmate SSH sessions triggered by SMS."""

    def __init__(self, ofono_manager):
        self.ofono_manager = ofono_manager

    def start_session(self, target_number):
        """Starts a tmate session and SMSes the SSH link back to target_number."""
        run_in_background(self._task, target_number,)

    def _task(self, target_number):
        try:
            urllib.request.urlopen('https://8.8.8.8', timeout=2)
            has_internet = True
        except Exception:
            has_internet = False

        if not has_internet:
            logger.info("[SMStmate] No internet, trying to activate context1")
            try:
                cm = self.ofono_manager._get_proxy("org.ofono.ConnectionManager")
                if cm:
                    contexts = cm.call_sync("GetContexts", None, Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
                    for path, props in contexts:
                        if path.endswith("/context1"):
                            ctx = self.ofono_manager._get_proxy("org.ofono.ConnectionContext", path)
                            if ctx:
                                ctx.call_sync("SetProperty", GLib.Variant("(sv)", ("Active", GLib.Variant("b", False))), Gio.DBusCallFlags.NONE, -1, None)
                                time.sleep(1)
                                ctx.call_sync("SetProperty", GLib.Variant("(sv)", ("Active", GLib.Variant("b", True))), Gio.DBusCallFlags.NONE, -1, None)
                                time.sleep(5)
                            break
            except Exception as e:
                logger.error(f"[SMStmate] Failed to activate context: {e}")

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
                    except Exception:
                        pass
            else:
                self.ofono_manager.send_sms(target_number, "tmate failed to start or get SSH string.")
        except Exception as e:
            logger.error(f"[SMStmate] Exec error: {e}")
