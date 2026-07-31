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

import gi
gi.require_version('EBookContacts', '1.2')
from gi.repository import EBookContacts
from loguru import logger
from gettext import gettext as _
from ..utils.phone_utils import normalize_number
from ..utils.vcard_utils import unfold_vcard


class EdsContactsManager:
    def get_display_name(self, number):
        """Return the user-facing name for a number, honoring the blocklist."""
        if self.db_ref and self.db_ref.is_blocked(number):
            return _("Blocked Number")
        return self.get_contact_name(number)

    def remove_number_everywhere(self, number):
        """Remove a number from all contacts, deleting contacts left otherwise empty."""
        norm = normalize_number(number)
        for c in self.search_contacts(norm):
            uid = c[0]

            with self.cache_lock:
                full_contact = self.cache.get(uid)

            should_delete = False
            if full_contact:
                phones = full_contact.get('phones', [])
                other_phones = [p for p in phones if normalize_number(p[0]) != norm]
                emails = full_contact.get('emails', [])

                has_other_fields = False
                vcard = self.get_contact_vcard(uid) or full_contact.get('vcard', '')
                if vcard:
                    for line in unfold_vcard(vcard).splitlines():
                        if ":" not in line:
                            continue
                        key = line.split(":", 1)[0].split(";")[0].upper()
                        if key in ["ORG", "TITLE", "ADR", "NOTE", "URL", "BDAY", "ANNIVERSARY"]:
                            has_other_fields = True
                            break

                if not other_phones and not emails and not has_other_fields:
                    should_delete = True

            if should_delete:
                logger.info(f"[EDS] Deleting contact {uid} (empty after removing {norm})")
                self.delete_contact(uid)
            else:
                self.remove_number_from_contact(uid, norm)

    def get_contact_name(self, number):
        """Look up a contact name by phone number."""
        norm = normalize_number(number)

        with self.cache_lock:
            candidates = self.lookup_map.get(norm, [])
            if candidates:
                candidates.sort(key=lambda x: x[0])
                return candidates[0][1]

        if any(c.isalpha() for c in str(number)):
            return "Unknown"

        return None

    def get_contact_vcard(self, uid):
        """Get the vCard for a contact (from DB)."""
        return self.db_ref.get_contact_vcard(uid)

    def search_contacts(self, query, limit=None, offset=0):
        """Search for contacts by name or number from the local contacts DB."""
        if not self.db_ref:
            return []
        return self.db_ref.search_contacts_db(query, limit=limit, offset=offset)

    def _is_andromeda_source(self, source_uid):
        """Return True when the source is the read-only Andromeda Contacts book."""
        with self.sources_lock:
            info = self.sources.get(source_uid)
        return bool(info) and info.get('name') == "Andromeda Contacts"

    def _get_writable_client(self, source_uid=None):
        """Get the client for source_uid, or the highest ranked when unspecified."""
        with self.sources_lock:
            if source_uid:
                info = self.sources.get(source_uid)
                if info is None:
                    logger.warning(f"[EDS] No connected client for requested source {source_uid}")
                    return None
                return info.get('client')

            sorted_sources = sorted(self.sources.values(), key=lambda x: x['rank'])
            if sorted_sources:
                return sorted_sources[0].get('client')
        return None

    def save_contact(self, vcard_string, uid=None, source_uid=None):
        """Save a contact from a VCard string."""
        lines = vcard_string.splitlines()
        cleaned_lines = []
        for line in lines:
            if line.startswith("TEL"):
                try:
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        raw = parts[1].strip()
                        key_part = parts[0]

                        if "X-EVOLUTION-E164" in key_part:
                            subparts = key_part.split(";")
                            new_subparts = [sp for sp in subparts if not sp.startswith("X-EVOLUTION-E164")]
                            key_part = ";".join(new_subparts)

                        cleaned_lines.append(f"{key_part}:{raw}")
                    else:
                        cleaned_lines.append(line)
                except Exception as e:
                    logger.warning(f"[EDS] VCard line processing error: {e}")
                    cleaned_lines.append(line)
            else:
                cleaned_lines.append(line)

        final_vcard = "\n".join(cleaned_lines)

        try:
            real_uid = None
            client = None

            if uid and isinstance(uid, str) and uid.strip():
                s_uid, r_uid = self._parse_composite_uid(uid)
                if not s_uid:
                    s_uid = source_uid
                    r_uid = uid

                if not s_uid:
                    with self.cache_lock:
                        cached = self.cache.get(uid)
                    if cached:
                        s_uid = cached.get('source_uid')

                if s_uid:
                    if self._is_andromeda_source(s_uid):
                        logger.warning(f"[EDS] Refusing to modify Andromeda Contact {uid}")
                        return False

                    client = self._get_writable_client(s_uid)
                    real_uid = r_uid
                else:
                    logger.error(f"[EDS] Save failed: Could not determine source for UID {uid}")
                    return False
            else:
                if source_uid and self._is_andromeda_source(source_uid):
                    logger.warning("[EDS] Refusing to save new Andromeda Contact")
                    return False

                client = self._get_writable_client(source_uid)

            if not client:
                logger.error("[EDS] Save failed: No writable client found.")
                return False

            if real_uid:
                lines = final_vcard.splitlines()
                lines = [line for line in lines if not line.upper().startswith("UID:")]
                uid_line = f"UID:{real_uid}"
                if "END:VCARD" in lines:
                    idx = lines.index("END:VCARD")
                    lines.insert(idx, uid_line)
                else:
                    lines.append(uid_line)
                    lines.append("END:VCARD")
                final_vcard = "\n".join(lines)
                contact = EBookContacts.Contact.new_from_vcard(final_vcard)
                client.modify_contact_sync(contact, EBookContacts.BookOperationFlags.NONE, None)
                logger.info(f"[EDS] Modified: {real_uid}")
            else:
                contact = EBookContacts.Contact.new_from_vcard(final_vcard)
                client.add_contact_sync(contact, EBookContacts.BookOperationFlags.NONE, None)
                logger.info("[EDS] Created new contact")
            return True
        except Exception as e:
            logger.error(f"[EDS] Save Error: {e}")
            return False

    def delete_contact(self, uid):
        """Delete a contact by UID."""
        if not isinstance(uid, str) or not uid.strip():
            logger.error("[EDS] Delete failed: Invalid UID format")
            return False

        s_uid, r_uid = self._parse_composite_uid(uid)

        if not s_uid:
            with self.cache_lock:
                cached = self.cache.get(uid)
            if cached:
                s_uid = cached.get('source_uid')
                r_uid = uid
            else:
                logger.warning(f"[EDS] Delete warning: UID {uid} not found in cache and no source specified.")
                if ":" in uid:
                    parts = uid.split(":", 1)
                    s_uid = parts[0]
                    r_uid = parts[1]

        if not s_uid:
            logger.error(f"[EDS] Delete failed: Unknown source for {uid}")
            return False

        if self._is_andromeda_source(s_uid):
            logger.warning(f"[EDS] Refusing to delete Andromeda Contact {uid}")
            return False

        try:
            client = self._get_writable_client(s_uid)
            if not client:
                logger.error(f"[EDS] Delete failed: No client for {s_uid}")
                return False

            client.remove_contact_by_uid_sync(r_uid, EBookContacts.BookOperationFlags.NONE, None)
            logger.info(f"[EDS] Deleted: {uid}")
            return True
        except Exception as e:
            logger.error(f"[EDS] Delete Error: {e}")
            return False

    def delete_contacts(self, uids):
        """Delete multiple contacts by UIDs (Batch Operation)."""
        if not uids:
            return True

        uids_by_source = {}
        for uid in uids:
            s_uid, r_uid = self._parse_composite_uid(uid)
            if not s_uid:
                with self.cache_lock:
                    cached = self.cache.get(uid)
                if cached:
                    s_uid = cached.get('source_uid')
                    r_uid = uid
                elif ":" in uid:
                    parts = uid.split(":", 1)
                    s_uid = parts[0]
                    r_uid = parts[1]

            if s_uid:
                if s_uid not in uids_by_source:
                    uids_by_source[s_uid] = []
                uids_by_source[s_uid].append(r_uid)
            else:
                logger.warning(f"[EDS] Batch delete skipped unknown source for {uid}")

        success = True
        for s_uid, r_uids in uids_by_source.items():
            if self._is_andromeda_source(s_uid):
                logger.warning(f"[EDS] Refusing to batch delete Andromeda Contacts from {s_uid}")
                success = False
                continue

            try:
                client = self._get_writable_client(s_uid)
                if not client:
                    logger.error(f"[EDS] Batch delete failed: No client for {s_uid}")
                    success = False
                    continue

                client.remove_contacts_sync(r_uids, EBookContacts.BookOperationFlags.NONE, None)
                logger.info(f"[EDS] Batch deleted {len(r_uids)} contacts from {s_uid}")

            except Exception as e:
                logger.error(f"[EDS] Batch delete error for {s_uid}: {e}")
                success = False

        return success

    def delete_all_contacts(self, source_uid=None):
        """
        Delete all contacts (Dangerous).
        If source_uid is provided, only deletes from that source.
        """
        with self.cache_lock:
            if source_uid:
                uids = [uid for uid, c in self.cache.items() if c.get('source_uid') == source_uid]
            else:
                uids = list(self.cache.keys())

        count = 0
        for uid in uids:
            if self.delete_contact(uid):
                count += 1

        target = source_uid if source_uid else "ALL SOURCES"
        logger.info(f"[EDS] Deleted {count} contacts from {target}.")
        return True

    def add_number_to_contact(self, uid, number, label="Mobile"):
        """Add a phone number to an existing contact."""
        logger.info(f"[EDS] Adding number {number} to contact {uid}")
        with self.cache_lock:
            contact = self.cache.get(uid)

        if not contact:
            logger.warning(f"[EDS] Add Number Failed: Contact {uid} not found in cache")
            return False

        vcard = unfold_vcard(contact.get('vcard', ''))
        if not vcard:
            vcard = self.get_contact_vcard(uid)
            if not vcard:
                logger.warning(f"[EDS] Add Number Failed: No VCard for {uid}")
                return False
            vcard = unfold_vcard(vcard)

        lines = vcard.splitlines()
        new_lines = []
        inserted = False

        type_str = label.upper()
        new_line = f"TEL;TYPE={type_str}:{number}"

        for line in lines:
            if line.strip().upper() == "END:VCARD":
                new_lines.append(new_line)
                new_lines.append(line)
                inserted = True
            else:
                new_lines.append(line)

        if not inserted:
            new_lines.append(new_line)
            new_lines.append("END:VCARD")

        final_vcard = "\n".join(new_lines)
        return self.save_contact(final_vcard, uid=uid)

    def remove_number_from_contact(self, uid, number):
        """Remove a phone number from an existing contact."""
        logger.info(f"[EDS] Removing number {number} from contact {uid}")
        with self.cache_lock:
            contact = self.cache.get(uid)

        if not contact:
            logger.warning(f"[EDS] Remove Number Failed: Contact {uid} not found in cache")
            return False

        vcard = unfold_vcard(contact.get('vcard', ''))
        if not vcard:
            vcard = self.get_contact_vcard(uid)
            if not vcard:
                logger.warning(f"[EDS] Remove Number Failed: No VCard for {uid}")
                return False
            vcard = unfold_vcard(vcard)

        norm_target = normalize_number(number)
        if not norm_target:
            logger.warning(f"[EDS] Remove Number Failed: Could not normalize {number}")
            return False

        lines = vcard.splitlines()
        new_lines = []

        def is_match(line):
            if not line.startswith("TEL"):
                return False
            try:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    val = normalize_number(parts[1])
                    return val == norm_target
            except Exception as e:
                logger.debug(f"[EDS] Line parse error: {e}")
            return False

        for line in lines:
            if is_match(line):
                logger.info(f"[EDS] Removing matching line: {line}")
                continue
            new_lines.append(line)

        final_vcard = "\n".join(new_lines)
        return self.save_contact(final_vcard, uid=uid)
