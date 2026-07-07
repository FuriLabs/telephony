# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import re
from functools import lru_cache

import phonenumbers
from gi.repository import Gio
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.ofono_utils import get_first_non_hfp_modem

from telephony.shared.utils.region_utils import get_system_region


def normalize_number(number, permissive=False):
    return normalize_number_cached(number, get_system_region(), permissive)


@lru_cache(maxsize=4096)
def normalize_number_cached(number, region, permissive=False):
    if not number:
        logger.warning("[Utils] normalize_number: Input is empty")
        return ""

    number_str = str(number).strip()

    for clir_prefix in ("#31#", "*31#"):
        if number_str.startswith(clir_prefix):
            number_str = number_str[len(clir_prefix):]
            break

    if any(c.isalpha() for c in number_str):
        logger.debug(f"[Utils] normalize_number: Input contains letters, returning as-is: {number_str}")
        return number_str

    if not number_str.startswith("+"):
        clean_check = number_str.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if clean_check.isdigit() and len(clean_check) < 7:
            logger.debug(f"[Utils] normalize_number: Preserving short number: {clean_check}")
            return clean_check

    try:
        parsed = phonenumbers.parse(number_str, region)
        reason = phonenumbers.is_possible_number_with_reason(parsed)
        if reason == phonenumbers.ValidationResult.IS_POSSIBLE_LOCAL_ONLY:
            local_only = re.sub(r'[^0-9\*\#]', "", number_str)
            logger.debug(f"[Utils] normalize_number: Local-only number, no international form: {local_only}")
            return local_only
        if reason == phonenumbers.ValidationResult.IS_POSSIBLE:
            if number_str.strip().startswith("+"):
                clean_input = re.sub(r'[^0-9\+]', "", number_str)
                if not clean_input.startswith("+" + str(parsed.country_code)):
                    logger.warning(f"[Utils] normalize_number: CC mismatch! Input: {number_str}, Parsed CC: {parsed.country_code}. Returning input.")
                    return clean_input
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        logger.debug(f"[Utils] normalize_number: Number parsed but not possible: {number_str}")
    except phonenumbers.NumberParseException as e:
        logger.debug(f"[Utils] normalize_number: Lib parsing failed for '{number_str}': {e}")

    clean_s = number_str.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    if clean_s.isdigit() and 2 <= len(clean_s) <= 6:
        logger.debug(f"[Utils] normalize_number: Detected short code or local number, returning raw: {clean_s}")
        return clean_s

    if permissive:
        logger.debug(f"[Utils] normalize_number: Permissive mode enabled, returning original: {number_str}")
        return number_str

    final_clean = re.sub(r'[^0-9\+\*\#]', "", number_str)
    logger.debug(f"[Utils] normalize_number: Fallback regex cleaning used on: {number_str} -> {final_clean}")
    return final_clean


def parse_evolution_e164_param(param_value):
    if not param_value:
        return None

    tokens = param_value.split(",")
    local_part = ""
    country_part = ""

    for token in tokens:
        cleaned = token.strip().replace('"', "")
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
    try:
        modem_path = None
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        manager = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.ofono", "/", "org.ofono.Manager", None)
        result = manager.call_sync("GetModems", None, Gio.DBusCallFlags.NONE, -1, None)
        modems = result.unpack()[0]
        modem = get_first_non_hfp_modem(modems)
        if modem:
            modem_path, _props = modem

        if modem_path:
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


def build_search_variants(token):
    variants = {token}
    norm = normalize_number(token)
    if norm:
        variants.add(norm)

    try:
        country_code = phonenumbers.country_code_for_region(get_system_region())
    except Exception as e:
        logger.debug(f"[Utils] Region code lookup failed: {e}")
        country_code = None

    region_prefix = f"+{country_code}" if country_code else ""
    if region_prefix and token.startswith("0"):
        variant = region_prefix + token[1:]
        variants.add(variant)
        v_norm = normalize_number(variant)
        if v_norm:
            variants.add(v_norm)

    if region_prefix and token.startswith(region_prefix):
        variants.add("0" + token[len(region_prefix):])

    return variants


def conversation_id(number_or_list):
    if isinstance(number_or_list, (list, tuple, set)):
        numbers = list(number_or_list)
    else:
        numbers = str(number_or_list).split(",")
    cleaned = sorted({normalize_number(n.strip(), permissive=True) for n in numbers if str(n).strip()})
    return ",".join(cleaned)
