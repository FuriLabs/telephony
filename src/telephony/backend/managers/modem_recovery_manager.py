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

from gi.repository import GLib

from loguru import logger

from ..utils.system_utils import restart_ril_modem, restart_ofono_service

RECOVERY_TIMEOUT_SECONDS = 30


def execute_modem_recovery():
    """Restart the whole modem stack; blocking, call from a worker thread.

    Restarting the RIL daemon and then ofono restores every known failure
    the stack can recover from without a reboot, so there is no ladder of
    gentler steps anymore.
    """
    logger.warning("[ModemRecovery] Restarting RIL daemon and ofono")
    restart_ril_modem()
    restart_ofono_service()


def watch_recovery_result(ofono, on_done, timeout_seconds=RECOVERY_TIMEOUT_SECONDS):
    """Report recovery success or failure to on_done(bool); call on the main loop.

    The verdict comes from the modem itself: the dial-availability signal
    the ofono manager emits once the restarted stack republishes its voice
    interface, with a timeout as the failure path. No polling.
    """
    state = {"handler": None, "timer": None, "done": False}

    def finish(success):
        if state["done"]:
            return
        state["done"] = True
        if state["handler"] is not None:
            ofono.disconnect(state["handler"])
            state["handler"] = None
        if state["timer"] is not None:
            GLib.source_remove(state["timer"])
            state["timer"] = None
        logger.info(f"[ModemRecovery] Recovery {'succeeded' if success else 'failed'}")
        on_done(success)

    def on_availability(_ofono, available):
        if available:
            finish(True)

    def on_timeout():
        state["timer"] = None
        finish(False)
        return False

    state["handler"] = ofono.connect('dial-availability-changed', on_availability)
    state["timer"] = GLib.timeout_add_seconds(timeout_seconds, on_timeout)

    if ofono.dialing_available():
        finish(True)
