# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

"""Display-name resolution over the contact lookup map.

The messages and history lists resolved names against the same map
with two diverging inline copies; one resolver keeps their fallbacks
identical.
"""

from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.phone_utils import normalize_number


def resolve_contact_name(contact_map, number):
    val = contact_map.get(normalize_number(number))
    if not val:
        return None
    if isinstance(val, list):
        try:
            best = sorted(val, key=lambda x: x[0])[0]
            return best[1]
        except (IndexError, TypeError) as e:
            logger.warning(f"[Contacts] Invalid contact map entry for {number}: {e}")
            return None
    if isinstance(val, str):
        return val
    return None
