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
import hashlib
from loguru import logger
import gi
gi.require_version('EBookContacts', '1.2')
from gi.repository import EBookContacts

from .phone_utils import normalize_number, parse_evolution_e164_param


def unfold_vcard(vcard_str):
    """
    Unfold vCard lines that are wrapped with a CRLF/LF followed by a space.
    """
    if not vcard_str:
        return ""

    unfolded = re.sub(r'\r?\n[ \t]', '', vcard_str)
    return unfolded


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


def _parse_tel_line(line):
    """Parse a TEL vcard line into a (number, label) tuple, or None."""
    parts = line.split(":", 1)
    if len(parts) < 2:
        return None

    key_part = parts[0].strip()
    number = parts[1].strip()

    if "X-EVOLUTION-E164" in key_part.upper():
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


def parse_contact_safe(contact, source_uid):
    """
    Extract contact details (UID, name, phones, emails, and generated vcard hash)
    from an EBookContacts.Contact object by parsing its serialized vcard.

    The EContact field accessors (get_const/get) return raw gconstpointer values
    that PyGObject marshals as integers, so the string-based parser is the only
    path that works from Python.
    """
    try:
        vcard = unfold_vcard(contact.to_string(1))
    except Exception as e:
        logger.warning(f"[VCardUtils] VCard generation failed: {e}")
        vcard = "BEGIN:VCARD\nVERSION:3.0\nFN:Unknown\nEND:VCARD"

    real_uid = contact.get(EBookContacts.ContactField.UID)
    if not real_uid or not isinstance(real_uid, str):
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
        value_part = parts[1].strip()
        main_key = key_part.split(";")[0].upper()

        if main_key == "FN":
            name = value_part
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
