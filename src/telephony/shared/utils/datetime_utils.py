# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from datetime import datetime

from telephony.shared.utils.log_utils import logger

STANDARD_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_timestamp(dt=None):
    if dt is None:
        dt = datetime.now()
    return dt.strftime(STANDARD_FORMAT)


def parse_timestamp(ts_str):
    try:
        return datetime(int(ts_str[0:4]), int(ts_str[5:7]), int(ts_str[8:10]), int(ts_str[11:13]), int(ts_str[14:16]), int(ts_str[17:19]))
    except Exception:
        try:
            return datetime.strptime(ts_str, STANDARD_FORMAT)
        except Exception as e:
            logger.debug(f"[Utils] Unparseable timestamp {ts_str!r}, using now: {e}")
            return datetime.now()
