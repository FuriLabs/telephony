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

from telephony.shared.utils.datetime_utils import format_timestamp

import os
from datetime import datetime
from telephony.shared.utils.log_utils import logger


def _get_value(data, keys, default=None):
    """
    Attempts to retrieve a value from a dictionary or sqlite3.Row using a list of possible keys.
    Returns the first non-None value found, or the default if none are found.
    """
    if not data:
        return default
    for key in keys:
        try:
            val = data[key]
            if val is not None:
                return val
        except (KeyError, IndexError, TypeError):
            continue
    return default


def _get_xml_value(element, keys, default=None):
    """
    Attempts to retrieve an attribute from an XML element using a list of possible keys.
    Returns the first non-None value found, or the default if none are found.
    """
    if element is None:
        return default
    for key in keys:
        val = element.get(key)
        if val is not None:
            return val
    return default


def _get_chatty_db_path():
    return os.path.expanduser("~/.purple/chatty/db/chatty-history.db")


def _get_chatty_mms_path():
    return os.path.expanduser("~/.local/share/chatty/mms/")


def _get_calls_db_path():
    return os.path.expanduser("~/.local/share/calls/records.db")


def _parse_generic_timestamp(ts):
    """
    Robust timestamp parser that handles variations such as:
    - String representations of ISO dates (e.g. '2022-01-01T12:00:00Z')
    - Standard Unix epochs (10 digits)
    - JavaScript/Android epochs in milliseconds (13 digits)
    - Microsecond/Nanosecond epochs (16+ digits)
    - Mac OS absolute time epochs (offset from 2001-01-01)
    """
    if not ts:
        return format_timestamp()

    try:
        if isinstance(ts, str) and not ts.replace('.', '', 1).isdigit() and not ts.lstrip('-').isdigit():
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return format_timestamp(dt)

        ts_float = float(ts)
        ts_int_str = str(int(ts_float))

        length = len(ts_int_str)
        if length > 18:
            ts_float = ts_float / 1_000_000_000_000
        elif length > 15:
            ts_float = ts_float / 1_000_000_000
        elif length > 12:
            ts_float = ts_float / 1_000

        return format_timestamp(datetime.fromtimestamp(ts_float))
    except Exception as e:
        logger.warning(f"[Importer] Error parsing timestamp {ts}: {e}")
        return format_timestamp()
