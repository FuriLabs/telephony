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

import time

from loguru import logger

from ..utils.system_utils import restart_ril_modem, reenable_disabled_radio, restart_ofono_service

AUTO_RECOVERY_STEP_IDS = ("ofono", "radio", "power", "ril", "radio_flag")
VERIFY_POLL_SECONDS = 3
VERIFY_MAX_TICKS = 15
PROPERTY_SETTLE_SECONDS = 3


def execute_recovery_step(ofono, step_id):
    """Perform one recovery action; blocking, call from a worker thread."""
    if step_id == "ofono":
        restart_ofono_service()
    elif step_id == "radio":
        ofono.set_modem_online(False)
        time.sleep(PROPERTY_SETTLE_SECONDS)
        ofono.set_modem_online(True)
    elif step_id == "power":
        ofono.set_modem_powered(False)
        time.sleep(PROPERTY_SETTLE_SECONDS)
        ofono.set_modem_powered(True)
    elif step_id == "ril":
        restart_ril_modem()
    elif step_id == "radio_flag":
        reenable_disabled_radio()


def run_automatic_recovery(ofono):
    """Run the whole ladder silently, verifying after each step.

    Walks every step except the reboot, from gentlest to strongest, and
    stops as soon as a new call could be placed again. Blocking for up
    to several minutes; call from a worker thread. Returns True when the
    modem recovered.
    """
    for step_id in AUTO_RECOVERY_STEP_IDS:
        logger.warning(f"[ModemRecovery] Automatic step: {step_id}")
        try:
            execute_recovery_step(ofono, step_id)
        except Exception as e:
            logger.warning(f"[ModemRecovery] Automatic step {step_id} error: {e}")

        for _ in range(VERIFY_MAX_TICKS):
            if ofono.dialing_available():
                logger.info(f"[ModemRecovery] Modem recovered after step: {step_id}")
                return True
            time.sleep(VERIFY_POLL_SECONDS)

    return ofono.dialing_available()
