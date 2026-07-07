# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import GLib

from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.system_utils import (restart_ril_modem, restart_ofono_service,
                                                 restart_modemmanager, wait_for_ofono)

RECOVERY_TIMEOUT_SECONDS = 30


def execute_modem_recovery():
    logger.warning("[ModemRecovery] Restarting RIL daemon and ofono")
    restart_ril_modem()
    restart_ofono_service()
    wait_for_ofono()
    logger.warning("[ModemRecovery] Restarting ModemManager")
    restart_modemmanager()


def watch_recovery_result(ofono, on_done, timeout_seconds=RECOVERY_TIMEOUT_SECONDS):
    state = {"handler": None, "timer": None, "done": False}

    # Recovery can complete from either the signal or timeout, but only once.
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
        logger.info(f"[ModemRecovery] Recovery {"succeeded" if success else "failed"}")
        on_done(success)

    def on_availability(_ofono, available):
        if available and ofono.modem_online is True:
            finish(True)

    def on_timeout():
        state["timer"] = None
        finish(False)
        return False

    state["handler"] = ofono.connect("dial-availability-changed", on_availability)
    state["timer"] = GLib.timeout_add_seconds(timeout_seconds, on_timeout)

    if ofono.is_dialing_available() and ofono.modem_online is True:
        finish(True)
