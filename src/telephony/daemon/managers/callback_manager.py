# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import GLib
from telephony.shared.utils.log_utils import logger


class CallbackManager:

    def __init__(self, ofono_manager):
        self.ofono_manager = ofono_manager

    def execute_callback(self, number, retries=5):
        if len(self.ofono_manager.active_calls) > 0:
            if retries > 0:
                GLib.timeout_add_seconds(2, lambda: self.execute_callback(number, retries - 1) or False)
            else:
                logger.warning("[TrustedCallback] Could not execute callback, line is busy.")
            return

        logger.info(f"[TrustedCallback] Executing callback to {number}")
        self.ofono_manager.dial(number, hide_id=False)
