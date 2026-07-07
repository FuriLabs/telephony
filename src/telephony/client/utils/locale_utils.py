# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import locale

from telephony.shared.utils.log_utils import logger


def init_locale():
    try:
        locale.setlocale(locale.LC_ALL, "")
        logger.info(f"[Utils] Initialized Locale: {locale.getlocale()}")
    except Exception as e:
        logger.warning(f"[Utils] Failed to set default locale: {e}")
        try:
            locale.setlocale(locale.LC_ALL, "C.UTF-8")
        except Exception as e2:
            logger.warning(f"[Utils] Fallback locale failed too: {e2}")


def get_date_format():
    try:
        return locale.nl_langinfo(locale.D_FMT)
    except Exception as e:
        logger.warning(f"[LocaleUtils] get_date_format error: {e}. Falling back to %x")
        return "%x"


def get_time_format():
    return "%H:%M"
