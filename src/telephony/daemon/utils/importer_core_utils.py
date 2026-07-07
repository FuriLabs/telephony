# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from telephony.shared.utils.datetime_utils import format_timestamp

import os
from datetime import datetime
from telephony.shared.utils.log_utils import logger


def get_value(data, keys, default=None):
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


def get_xml_value(element, keys, default=None):
    if element is None:
        return default
    for key in keys:
        val = element.get(key)
        if val is not None:
            return val
    return default


def get_chatty_db_path():
    return os.path.expanduser("~/.purple/chatty/db/chatty-history.db")


def get_chatty_mms_path():
    return os.path.expanduser("~/.local/share/chatty/mms/")


def get_calls_db_path():
    return os.path.expanduser("~/.local/share/calls/records.db")


def parse_generic_timestamp(ts):
    """Robust timestamp parser that handles variations such as: - String representations of ISO dates (e.g. '2022-01-01T12:00:00Z') - Standard Unix epochs (10 digits) - JavaScript/Android epochs in milliseconds (13 digits) - Microsecond/Nanosecond epochs (16+ digits) - Mac OS absolute time epochs (offset from 2001-01-01)"""
    if not ts:
        return format_timestamp()

    try:
        if isinstance(ts, str) and not ts.replace(".", "", 1).isdigit() and not ts.lstrip("-").isdigit():
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
