# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from telephony.shared.utils.log_utils import logger


DEFAULT_MAX_ATTACHMENT_SIZE = 600 * 1024


def max_attachment_size(gsettings_mgr):
    if gsettings_mgr:
        try:
            val = gsettings_mgr.get_setting("mms_size_limit")
            if val:
                return int(val) * 1024
        except Exception as e:
            logger.warning(f"[MMS] Invalid size limit setting, using default: {e}")
    return DEFAULT_MAX_ATTACHMENT_SIZE
