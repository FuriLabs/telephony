# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from telephony.shared.utils.log_utils import logger


class RelayManager:

    def __init__(self, ofono_manager):
        self.ofono_manager = ofono_manager

    def execute_relay(self, source_number, target_number, message):
        logger.info(f"[SMSRelay] Trigger MATCH from {source_number} to {target_number}")
        if self.ofono_manager.send_sms(target_number, message):
            try:
                self.ofono_manager.db.add_message(target_number, "outgoing", message, "sent", sender="Me")
            except Exception as e:
                logger.error(f"[SMSRelay] Error saving to db: {e}")
