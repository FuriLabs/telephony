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
from telephony.backend.utils.log_utils import logger


class CallbackManager:
    """Manages trusted callbacks triggered by SMS."""

    def __init__(self, ofono_manager):
        self.ofono_manager = ofono_manager

    def execute_callback(self, number, retries=5):
        """Silently calls the number back, retrying if line is busy."""
        if len(self.ofono_manager.active_calls) > 0:
            if retries > 0:
                GLib.timeout_add_seconds(2, lambda: self.execute_callback(number, retries - 1) or False)
            else:
                logger.warning("[TrustedCallback] Could not execute callback, line is busy.")
            return

        logger.info(f"[TrustedCallback] Executing callback to {number}")
        self.ofono_manager.dial(number, hide_id=False)
