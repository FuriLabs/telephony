# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import quopri
import re
import hashlib
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.phone_utils import normalize_number, parse_evolution_e164_param


def unfold_vcard(vcard_str):
    if not vcard_str:
        return ""

    unfolded = re.sub(r'\r?\n[ \t]', "", vcard_str)
    return re.sub(r'=\r?\n', "", unfolded)


def decode_quoted_printable(key_part, value):
    if "ENCODING=QUOTED-PRINTABLE" not in key_part.upper():
        return value

    charset = "utf-8"
    match = re.search(r'CHARSET=([^;:]+)', key_part, re.IGNORECASE)
    if match:
        charset = match.group(1).strip('"') or "utf-8"

    try:
        return quopri.decodestring(value.encode("ascii", "replace")).decode(charset, "replace")
    except Exception as e:
        logger.warning(f"[VCardUtils] Quoted-printable decode failed: {e}")
        return value


def get_phone_label(meta):
    meta = meta.upper()
    for token, label in (("WORK", "Work"), ("HOME", "Home"), ("FAX", "Fax"),
                         ("MAIN", "Main"), ("OTHER", "Other")):
        if token in meta:
            return label
    return "Mobile"


def get_email_label(meta):
    meta = meta.upper()
    if "WORK" in meta:
        return "Work"
    if "OTHER" in meta:
        return "Other"
    return "Home"


def extract_e164_number(key_part, default):
    try:
        match = re.search(r'X-EVOLUTION-E164=([^;:]+)', key_part, re.IGNORECASE)
        if match:
            parsed_e164 = parse_evolution_e164_param(match.group(1))
            if parsed_e164:
                return parsed_e164
    except Exception as e:
        logger.warning(f"[VCardUtils] E164 parse warning: {e}")
    return default


def number_from_value(value):
    if value.lower().startswith("tel:"):
        value = value[4:]
    return value.split(";", 1)[0].strip()


def parse_tel_line(line):
    parts = line.split(":", 1)
    if len(parts) < 2:
        return None

    key_part = parts[0].strip()
    number = number_from_value(decode_quoted_printable(key_part, parts[1].strip()))

    if not number.startswith("+") and "X-EVOLUTION-E164" in key_part.upper():
        number = extract_e164_number(key_part, number)

    if number == "UNKNOWN":
        return None

    return normalize_number(number), get_phone_label(key_part)


def build_contact_dict(source_uid, real_uid, name, phones, emails, vcard, is_fav):
    search_phones = []
    for p in phones:
        n = normalize_number(p[0])
        if n:
            search_phones.append(n)
        search_phones.append(p[0].replace(" ", "").replace("-", ""))

    return {
        "uid": f"{source_uid}:{real_uid}",
        "source_uid": source_uid,
        "name": name, "phones": phones, "emails": emails, "vcard": vcard,
        "vcard_hash": hashlib.md5(vcard.encode("utf-8")).hexdigest(),
        "idx_name": name.lower(), "idx_phones": search_phones, "is_fav": is_fav
    }


def property_key(key_part):
    main_key = key_part.split(";")[0]
    if "." in main_key:
        main_key = main_key.split(".", 1)[1]
    return main_key.upper()


def is_property(line, name):
    head = line.split(":", 1)[0]
    return property_key(head) == name.upper()


def unescape_value(value):
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in (",", ";", "\\"):
                out.append(nxt)
                i += 2
                continue
            if nxt in ("n", "N"):
                out.append("\n")
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_vcard_string(vcard_str, source_uid):
    """Extract contact details (UID, name, phones, emails, and generated vcard hash) from a serialized vcard string, as delivered by the D-Bus book views whose signals carry vcards."""
    vcard = unfold_vcard(vcard_str)

    real_uid = None
    for line in vcard.splitlines():
        if line.startswith("UID:"):
            real_uid = line[4:].strip()
            break
    if not real_uid or not isinstance(real_uid, str):
        real_uid = ""

    name = "Unknown"
    phones = []
    emails = []
    is_fav = False

    for line in vcard.splitlines():
        parts = line.split(":", 1)
        if len(parts) < 2:
            continue

        key_part = parts[0].strip()
        value_part = decode_quoted_printable(key_part, parts[1].strip())
        main_key = property_key(key_part)

        if main_key == "FN":
            name = unescape_value(value_part)
        elif main_key == "TEL":
            parsed = parse_tel_line(line)
            if parsed:
                phones.append(parsed)
        elif main_key == "EMAIL":
            emails.append((value_part, get_email_label(key_part)))
        elif main_key == "X-FOLKS-FAVOURITE":
            if value_part.lower() == "true":
                is_fav = True

    return build_contact_dict(source_uid, real_uid, name, phones, emails, vcard, is_fav)
