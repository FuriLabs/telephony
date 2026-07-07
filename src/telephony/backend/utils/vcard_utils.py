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


def parse_contact_safe(contact, source_uid):
    """
    Safely extract contact details (UID, name, phones, emails, and generated vcard hash)
    from an EBookContacts.Contact object. Falls back to raw string parsing if API throws an error.
    """
    try:
        real_uid = contact.get_const(EBookContacts.ContactField.UID)
        if not real_uid:
            real_uid = ""

        name = contact.get_const(EBookContacts.ContactField.FULL_NAME) or "Unknown"

        vcard = ""
        is_fav = False

        try:
            vcard = unfold_vcard(contact.to_string(1))
        except Exception:
            vcard = ""

        phones = []
        try:
            raw_lines = vcard.splitlines()
            for line in raw_lines:
                if line.startswith("TEL"):
                    parts = line.split(":", 1)
                    if len(parts) < 2:
                        continue

                    val_part = parts[1].strip()
                    key_part = parts[0].strip().upper()

                    final_number = val_part

                    if "X-EVOLUTION-E164" in key_part:
                        try:
                            match = re.search(r'X-EVOLUTION-E164=([^;:]+)', key_part)
                            if match:
                                raw_param = match.group(1)
                                parsed_e164 = parse_evolution_e164_param(raw_param)
                                if parsed_e164:
                                    final_number = parsed_e164
                        except Exception as ex:
                            logger.warning(f"[VCardUtils] Manual E164 parse warning: {ex}")

                    if final_number == "UNKNOWN":
                        continue

                    norm = normalize_number(final_number)
                    label = "Mobile"
                    if "WORK" in key_part:
                        label = "Work"
                    elif "HOME" in key_part:
                        label = "Home"
                    elif "FAX" in key_part:
                        label = "Fax"
                    elif "MAIN" in key_part:
                        label = "Main"
                    elif "OTHER" in key_part:
                        label = "Other"

                    phones.append((norm, label))
        except Exception as e:
            logger.error(f"[VCardUtils] Raw phone parse failed: {e}")
            tel_attrs = contact.get_attributes(EBookContacts.ContactField.TEL)
            for attr in tel_attrs:
                val = attr.get_value()
                if val:
                    phones.append((normalize_number(val), "Mobile"))

        emails = []
        mail_attrs = contact.get_attributes(EBookContacts.ContactField.EMAIL)
        for attr in mail_attrs:
            val = attr.get_value()
            if not val:
                continue

            label = "Home"
            type_param = attr.get_param("TYPE")
            if type_param:
                t_upper = type_param.upper()
                if "WORK" in t_upper:
                    label = "Work"
                elif "OTHER" in t_upper:
                    label = "Other"

            emails.append((val, label))

        if "X-FOLKS-FAVOURITE:true" in vcard or "X-FOLKS-FAVOURITE:TRUE" in vcard:
            is_fav = True

        search_index = name.lower()
        search_phones = []
        for p in phones:
            n = normalize_number(p[0])
            if n:
                search_phones.append(n)
            search_phones.append(p[0].replace(" ", "").replace("-", ""))

        v_hash = hashlib.md5(vcard.encode('utf-8')).hexdigest()

        return {
            'uid': f"{source_uid}:{real_uid}",
            'source_uid': source_uid,
            'name': name, 'phones': phones, 'emails': emails, 'vcard': vcard, 'vcard_hash': v_hash,
            'idx_name': search_index, 'idx_phones': search_phones, 'is_fav': is_fav
        }

    except Exception:
        try:
            vcard = unfold_vcard(contact.to_string(1))
        except Exception as ev:
            logger.warning(f"[VCardUtils] VCard generation failed: {ev}")
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
            key_tokens = key_part.split(";")
            main_key = key_tokens[0].upper()

            if main_key == "FN":
                name = value_part
            elif main_key == "TEL":
                raw_number = value_part
                if "X-EVOLUTION-E164" in key_part:
                    try:
                        parts = key_part.split(";")
                        for p in parts:
                            if p.upper().startswith("X-EVOLUTION-E164"):
                                val = p.split("=", 1)[1]
                                parsed_e164 = parse_evolution_e164_param(val)
                                if parsed_e164:
                                    raw_number = parsed_e164
                                break
                    except Exception as e:
                        logger.debug(f"[VCardUtils] VCard E164 parse error: {e}")

                number = normalize_number(raw_number)
                label = "Mobile"
                meta_str = key_part.upper()
                if "WORK" in meta_str:
                    label = "Work"
                elif "HOME" in meta_str:
                    label = "Home"
                elif "FAX" in meta_str:
                    label = "Fax"
                elif "MAIN" in meta_str:
                    label = "Main"
                elif "OTHER" in meta_str:
                    label = "Other"
                phones.append((number, label))
            elif main_key == "EMAIL":
                addr = value_part
                meta_str = key_part.upper()
                label = "Home"
                if "WORK" in meta_str:
                    label = "Work"
                elif "OTHER" in meta_str:
                    label = "Other"
                elif "HOME" in meta_str:
                    label = "Home"
                emails.append((addr, label))
            elif main_key == "X-FOLKS-FAVOURITE":
                if value_part.lower() == "true":
                    is_fav = True

        search_index = name.lower()
        search_phones = []
        for p in phones:
            n = normalize_number(p[0])
            if n:
                search_phones.append(n)
            search_phones.append(p[0].replace(" ", "").replace("-", ""))

        v_hash = hashlib.md5(vcard.encode('utf-8')).hexdigest()

        return {
            'uid': f"{source_uid}:{real_uid}",
            'source_uid': source_uid,
            'name': name, 'phones': phones, 'emails': emails, 'vcard': vcard, 'vcard_hash': v_hash,
            'idx_name': search_index, 'idx_phones': search_phones, 'is_fav': is_fav
        }
