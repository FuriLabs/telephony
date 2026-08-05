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

from telephony.shared.constants import DEFAULT_MAX_ATTACHMENT_SIZE
from telephony.shared.utils.log_utils import logger


def max_attachment_size(gsettings_mgr):
    """Return the MMS attachment size budget in bytes from settings, else the default."""
    if gsettings_mgr:
        try:
            val = gsettings_mgr.get_setting("mms_size_limit")
            if val:
                return int(val) * 1024
        except Exception as e:
            logger.warning(f"[MMS] Invalid size limit setting, using default: {e}")
    return DEFAULT_MAX_ATTACHMENT_SIZE
