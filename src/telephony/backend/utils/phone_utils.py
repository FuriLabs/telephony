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

import re
from functools import lru_cache

import phonenumbers
from gi.repository import Gio
from loguru import logger

from .region_utils import get_system_region


def normalize_number(number, permissive=False):
    """
    Normalize a phone number to E164 format.
    """
    return _normalize_number_cached(number, get_system_region(), permissive)


@lru_cache(maxsize=4096)
def _normalize_number_cached(number, region, permissive=False):
    """
    Internal cached implementation of normalize_number.
    """
    if not number:
        logger.warning("[Utils] normalize_number: Input is empty")
        return ""

    number_str = str(number).strip()

    prefix = ""
    if number_str.startswith("#31#"):
        prefix = "#31#"
        number_str = number_str[4:]

    if any(c.isalpha() for c in number_str):
        logger.warning(f"[Utils] normalize_number: Input contains letters, returning as-is: {prefix}{number_str}")
        return prefix + number_str

    if not number_str.startswith("+"):
        clean_check = number_str.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if clean_check.isdigit() and len(clean_check) < 7:
            logger.info(f"[Utils] normalize_number: Preserving short number: {clean_check}")
            return prefix + clean_check

    try:
        parsed = phonenumbers.parse(number_str, region)
        if phonenumbers.is_possible_number(parsed):
            if number_str.strip().startswith("+"):
                clean_input = re.sub(r'[^0-9\+]', '', number_str)
                if not clean_input.startswith("+" + str(parsed.country_code)):
                    logger.warning(f"[Utils] normalize_number: CC mismatch! Input: {number_str}, Parsed CC: {parsed.country_code}. Returning input.")
                    return prefix + clean_input
            return prefix + phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        logger.warning(f"[Utils] normalize_number: Number parsed but not possible: {number_str}")
    except phonenumbers.NumberParseException as e:
        logger.warning(f"[Utils] normalize_number: Lib parsing failed for '{number_str}': {e}")

    clean_s = number_str.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    if clean_s.isdigit() and 2 <= len(clean_s) <= 6:
        logger.warning(f"[Utils] normalize_number: Detected short code or local number, returning raw: {clean_s}")
        return prefix + clean_s

    if permissive:
        logger.warning(f"[Utils] normalize_number: Permissive mode enabled, returning original: {number_str}")
        return prefix + number_str

    final_clean = re.sub(r'[^0-9\+\*\#]', '', number_str)
    logger.warning(f"[Utils] normalize_number: Fallback regex cleaning used on: {number_str} -> {final_clean}")
    return prefix + final_clean


def parse_evolution_e164_param(param_value):
    """
    Parse the X-EVOLUTION-E164 parameter value.
    Can be in formats:
    - local_number,"+country_code"
    - "+country_code",local_number
    Returns the combined E164 number or None if parsing fails.
    """
    if not param_value:
        return None

    tokens = param_value.split(",")
    local_part = ""
    country_part = ""

    for token in tokens:
        cleaned = token.strip().replace('"', '')
        if cleaned.startswith("+"):
            country_part = cleaned
        else:
            local_part = cleaned

    if country_part and local_part:
        return country_part + local_part
    elif country_part:
        return country_part
    elif local_part:
        return local_part

    return None


def get_own_number():
    """
    Attempt to retrieve the own phone number from the modem.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        manager = None
        app = Gio.Application.get_default()
        if app and getattr(app, 'ofono_mgr', None) and app.ofono_mgr.ofono:
            manager = app.ofono_mgr.ofono.manager_proxy

        if not manager:
            manager = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono", "/", "org.ofono.Manager", None)
        result = manager.call_sync("GetModems", None, Gio.DBusCallFlags.NONE, -1, None)
        modems = result.unpack()[0]
        if modems:
            modem_path = modems[0][0]
            sim_proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono", modem_path, "org.ofono.SimManager", None)
            props = sim_proxy.call_sync("GetProperties", None, Gio.DBusCallFlags.NONE, -1, None)
            data = props.unpack()[0]
            nums = data.get("SubscriberNumbers")
            if nums and len(nums) > 0:
                logger.info(f"[Utils] Detected Own Number (SIM): {nums[0]}")
                return nums[0]
    except Exception as e:
        logger.warning(f"[Utils] Own number detection failed: {e}")

    return None
