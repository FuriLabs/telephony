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

import quopri
import re
import hashlib
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.phone_utils import normalize_number, parse_evolution_e164_param


def unfold_vcard(vcard_str):
    """
    Unfold vCard lines that are wrapped with a CRLF/LF followed by a space.

    A quoted-printable value wraps differently, ending a line with an
    equals sign and continuing without indenting it, so the two ways a
    line can be broken are both put back together here.
    """
    if not vcard_str:
        return ""

    unfolded = re.sub(r'\r?\n[ \t]', '', vcard_str)
    return re.sub(r'=\r?\n', '', unfolded)


def _decode_quoted_printable(key_part, value):
    """Decode a value the exporter escaped, leaving anything else alone.

    Phones that wrote vcards before UTF-8 was safe to put on a wire
    escaped every byte above ASCII, so a name keeps its letters and a
    number keeps its digits only if the escaping is undone. Read as
    written, the escapes stay in the text: a name reads as its own
    encoding and a number gains digits that were never dialled.
    """
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


def _get_phone_label(meta):
    """Derive a phone label from a TEL line's parameter part."""
    meta = meta.upper()
    for token, label in (("WORK", "Work"), ("HOME", "Home"), ("FAX", "Fax"),
                         ("MAIN", "Main"), ("OTHER", "Other")):
        if token in meta:
            return label
    return "Mobile"


def _get_email_label(meta):
    """Derive an email label from an EMAIL line's parameter part or TYPE param."""
    meta = meta.upper()
    if "WORK" in meta:
        return "Work"
    if "OTHER" in meta:
        return "Other"
    return "Home"


def extract_e164_number(key_part, default):
    """Return the X-EVOLUTION-E164 number from a TEL key part, or default."""
    try:
        match = re.search(r'X-EVOLUTION-E164=([^;:]+)', key_part, re.IGNORECASE)
        if match:
            parsed_e164 = parse_evolution_e164_param(match.group(1))
            if parsed_e164:
                return parsed_e164
    except Exception as e:
        logger.warning(f"[VCardUtils] E164 parse warning: {e}")
    return default


def _number_from_value(value):
    """Return the dialable part of a stored TEL value.

    Newer address books write the number as a uri rather than as
    digits, and everything a uri carries besides the number itself is
    for the caller that dials it, not for the number: an extension
    reaches a desk after the call connects and cannot be dialled with
    the rest.
    """
    if value.lower().startswith("tel:"):
        value = value[4:]
    return value.split(";", 1)[0].strip()


def _parse_tel_line(line):
    """Parse a TEL vcard line into a (number, label) tuple, or None.

    The stored number wins whenever it already carries a country code,
    because the E164 parameter beside it is often just the national
    digits with no country at all. Reading those digits means guessing
    the country they belong to, and a guess is exactly what a number
    written in full does not need.
    """
    parts = line.split(":", 1)
    if len(parts) < 2:
        return None

    key_part = parts[0].strip()
    number = _number_from_value(_decode_quoted_printable(key_part, parts[1].strip()))

    if not number.startswith("+") and "X-EVOLUTION-E164" in key_part.upper():
        number = extract_e164_number(key_part, number)

    if number == "UNKNOWN":
        return None

    return normalize_number(number), _get_phone_label(key_part)


def _build_contact_dict(source_uid, real_uid, name, phones, emails, vcard, is_fav):
    """Assemble the final contact dict with search index and vcard hash."""
    search_phones = []
    for p in phones:
        n = normalize_number(p[0])
        if n:
            search_phones.append(n)
        search_phones.append(p[0].replace(" ", "").replace("-", ""))

    return {
        'uid': f"{source_uid}:{real_uid}",
        'source_uid': source_uid,
        'name': name, 'phones': phones, 'emails': emails, 'vcard': vcard,
        'vcard_hash': hashlib.md5(vcard.encode('utf-8')).hexdigest(),
        'idx_name': name.lower(), 'idx_phones': search_phones, 'is_fav': is_fav
    }


def _property_key(key_part):
    """Return a vcard property name stripped of its group and parameters.

    Exports commonly group related lines as item1.TEL / item1.FN; the
    group prefix carries no meaning here and hid the property name.
    """
    main_key = key_part.split(";")[0]
    if "." in main_key:
        main_key = main_key.split(".", 1)[1]
    return main_key.upper()


def is_property(line, name):
    """Return True when a vcard line carries the named property.

    Grouped lines such as item1.TEL name the same property as TEL, so
    the group prefix is stripped before the comparison.
    """
    head = line.split(":", 1)[0]
    return _property_key(head) == name.upper()


def _unescape_value(value):
    """Decode vcard text escaping: backslashed comma, semicolon, newline."""
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


def parse_vcard_string(vcard_str, source_uid, real_uid=None):
    """
    Extract contact details (UID, name, phones, emails, and generated vcard
    hash) from a serialized vcard string, as delivered by the D-Bus book
    views whose signals carry vcards.
    """
    vcard = unfold_vcard(vcard_str)

    if not real_uid:
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
        value_part = _decode_quoted_printable(key_part, parts[1].strip())
        main_key = _property_key(key_part)

        if main_key == "FN":
            name = _unescape_value(value_part)
        elif main_key == "TEL":
            parsed = _parse_tel_line(line)
            if parsed:
                phones.append(parsed)
        elif main_key == "EMAIL":
            emails.append((value_part, _get_email_label(key_part)))
        elif main_key == "X-FOLKS-FAVOURITE":
            if value_part.lower() == "true":
                is_fav = True

    return _build_contact_dict(source_uid, real_uid, name, phones, emails, vcard, is_fav)
