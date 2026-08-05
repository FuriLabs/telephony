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

"""Display-name resolution over the contact lookup map.

The messages and history lists resolved names against the same map
with two diverging inline copies; one resolver keeps their fallbacks
identical.
"""

from telephony.backend.utils.log_utils import logger
from telephony.backend.utils.phone_utils import normalize_number


def resolve_contact_name(contact_map, number):
    """Return the best contact name for a number, or None when unknown.

    A map value is either a plain name or a priority list of
    (priority, name) pairs, where the lowest priority wins to match
    the address book order.
    """
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
