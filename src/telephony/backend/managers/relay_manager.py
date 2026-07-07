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

from loguru import logger


class RelayManager:
    """Manages SMS relay triggered by SMS."""

    def __init__(self, ofono_manager):
        self.ofono_manager = ofono_manager

    def execute_relay(self, source_number, target_number, message):
        """Forwards the message to the target_number."""
        logger.info(f"[SMSRelay] Trigger MATCH from {source_number} to {target_number}")
        if self.ofono_manager.send_sms(target_number, message):
            try:
                self.ofono_manager.db.add_message(target_number, "outgoing", message, "sent", sender="Me")
            except Exception as e:
                logger.error(f"[SMSRelay] Error saving to db: {e}")
