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

import json

from loguru import logger
from gi.repository import GLib

from .phone_utils import build_search_variants, normalize_number

SQLITE_MAX_BATCH_VARIABLES = 900


class DbContactsUtils:
    def get_contacts_lookup_map(self):
        """Get a snapshot of the current contact lookup map."""
        if not self.eds:
            return {}
        with self.eds.cache_lock:
            return dict(self.eds.lookup_map)

    def search_contacts(self, query="", limit=None, offset=0):
        """Search contacts via EDS."""
        if not self.eds:
            return []
        try:
            return self.eds.search_contacts(query, limit=limit, offset=offset)
        except Exception as e:
            logger.error(f"[DB] Search contacts error: {e}")
            return []

    def get_cached_contacts(self, source_uid):
        """Retrieve slim cached contacts for a specific source without vcard blobs."""
        try:
            with self.lock:
                c = self.conn_contacts.cursor()
                c.execute("SELECT uid, name, phones, emails, vcard_hash, is_fav FROM contacts WHERE source_uid=?", (source_uid,))
                rows = c.fetchall()

            results = []
            for r in rows:
                results.append({
                    'uid': r[0],
                    'name': r[1],
                    'phones': json.loads(r[2]) if r[2] else [],
                    'emails': json.loads(r[3]) if r[3] else [],
                    'vcard_hash': r[4],
                    'is_fav': bool(r[5]),
                })
            return results
        except Exception as e:
            logger.error(f"[DB] Get Cached Contacts Error: {e}")
            return []

    def search_contacts_db(self, query="", limit=None, offset=0):
        """Search the contacts table, mirroring the former in-memory semantics."""
        try:
            where = []
            params = []
            tokens = query.lower().split() if query else []
            for token in tokens:
                clause = ["instr(search_index_name, ?) > 0"]
                params.append(token)
                for variant in build_search_variants(token):
                    clause.append("instr(search_index_phones, ?) > 0")
                    params.append(variant)
                where.append("(" + " OR ".join(clause) + ")")

            sql = ("SELECT uid, name, phones, emails, is_fav, source_uid FROM contacts"
                   + ((" WHERE " + " AND ".join(where)) if where else "")
                   + " ORDER BY is_fav DESC,"
                   + " CASE WHEN name IS NULL OR name = '' THEN 1 ELSE 0 END, LOWER(name)")
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            elif offset:
                sql += " LIMIT -1 OFFSET ?"
                params.append(offset)

            with self.lock:
                c = self.conn_contacts.cursor()
                c.execute(sql, params)
                rows = c.fetchall()

            results = []
            for uid, name, phones_json, emails_json, is_fav, source_uid in rows:
                if not uid:
                    continue
                parts = (name or "").split(" ", 1)
                first = parts[0]
                last = parts[1] if len(parts) > 1 else ""
                phones = json.loads(phones_json) if phones_json else []
                emails = json.loads(emails_json) if emails_json else []
                results.append((uid, first, last, phones, emails, bool(is_fav), source_uid))
            return results
        except Exception as e:
            logger.error(f"[DB] Contact search error: {e}")
            return []

    def get_all_vcards(self, source_uid=None):
        """Return every stored vcard, optionally filtered to one source."""
        try:
            with self.lock:
                c = self.conn_contacts.cursor()
                if source_uid:
                    c.execute("SELECT vcard FROM contacts WHERE source_uid=?", (source_uid,))
                else:
                    c.execute("SELECT vcard FROM contacts")
                rows = c.fetchall()
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.error(f"[DB] Get vcards error: {e}")
            return []

    def get_contact_vcard(self, uid):
        """Retrieve the vcard string for a contact by UID."""
        try:
            with self.lock:
                c = self.conn_contacts.cursor()
                c.execute("SELECT vcard FROM contacts WHERE uid=?", (uid,))
                row = c.fetchone()
                return row[0] if row else ""
        except Exception as e:
            logger.error(f"[DB] Get Contact VCard Error: {e}")
            return ""

    def upsert_contacts_batch(self, data_list, source_uid):
        try:
            batch = []
            for data in data_list:
                uid = data.get('uid')
                if not uid:
                    continue
                name = data.get('name', '')
                phones = json.dumps(data.get('phones', []))
                emails = json.dumps(data.get('emails', []))
                vcard = data.get('vcard', '')
                idx_name = data.get('idx_name', '')
                idx_phones = json.dumps(data.get('idx_phones', []))
                is_fav = 1 if data.get('is_fav') else 0
                vcard_hash = data.get('vcard_hash')
                batch.append((uid, source_uid, name, phones, emails, vcard, idx_name, idx_phones, is_fav, vcard_hash))
            if batch:
                with self.lock:
                    c = self.conn_contacts.cursor()
                    c.executemany('INSERT OR REPLACE INTO contacts (uid, source_uid, name, phones, emails, vcard, search_index_name, search_index_phones, is_fav, vcard_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', batch)
                    self.conn_contacts.commit()
                GLib.idle_add(self.emit, 'contacts-updated')
            return True
        except Exception as e:
            logger.error(f"[DB] Upsert Contacts Batch Error: {e}")
            return False

    def delete_contact(self, uid):
        """Delete a contact from the cache."""
        try:
            with self.lock:
                c = self.conn_contacts.cursor()

                c.execute("SELECT phones FROM contacts WHERE uid=?", (uid,))
                row = c.fetchone()
                if row:
                    try:
                        phones_json = json.loads(row[0])
                        for p in phones_json:
                            norm = normalize_number(p[0])
                            if norm and self.gsettings_mgr:
                                self.gsettings_mgr.remove_from_special_lists(norm)
                    except json.JSONDecodeError as e:
                        logger.error(f"[DB] Failed to decode phones json during delete: {e}")
                    except Exception as e:
                        logger.error(f"[DB] Error removing from special lists: {e}")

                c.execute("DELETE FROM contacts WHERE uid=?", (uid,))
                self.conn_contacts.commit()
            GLib.idle_add(self.emit, 'contacts-updated')
            return True
        except Exception as e:
            logger.error(f"[DB] Delete Contact Error: {e}")
            return False

    def sync_deleted_contacts(self, source_uid, active_uids_list):
        """Remove stale contacts that are no longer in the source."""
        try:
            if not active_uids_list:
                with self.lock:
                    c = self.conn_contacts.cursor()

                    c.execute("SELECT phones FROM contacts WHERE source_uid=?", (source_uid,))
                    for row in c.fetchall():
                        try:
                            phones_json = json.loads(row[0])
                            for p in phones_json:
                                norm = normalize_number(p[0])
                                if norm and self.gsettings_mgr:
                                    self.gsettings_mgr.remove_from_special_lists(norm)
                        except json.JSONDecodeError as e:
                            logger.error(f"[DB] Failed to decode phones json during sync: {e}")
                        except Exception as e:
                            logger.error(f"[DB] Error removing stale contacts: {e}")

                    c.execute("DELETE FROM contacts WHERE source_uid=?", (source_uid,))
                    self.conn_contacts.commit()
                GLib.idle_add(self.emit, 'contacts-updated')
                return True

            with self.lock:
                c = self.conn_contacts.cursor()
                c.execute("SELECT uid FROM contacts WHERE source_uid=?", (source_uid,))
                local_uids = {r[0] for r in c.fetchall()}

                active_set = set(active_uids_list)
                to_delete = list(local_uids - active_set)

                if to_delete:
                    batch_size = SQLITE_MAX_BATCH_VARIABLES
                    for i in range(0, len(to_delete), batch_size):
                        batch = to_delete[i:i + batch_size]
                        placeholders = ",".join("?" * len(batch))

                        c.execute(f"SELECT phones FROM contacts WHERE uid IN ({placeholders})", batch)
                        for row in c.fetchall():
                            try:
                                phones_json = json.loads(row[0])
                                for p in phones_json:
                                    norm = normalize_number(p[0])
                                    if norm and self.gsettings_mgr:
                                        self.gsettings_mgr.remove_from_special_lists(norm)
                            except json.JSONDecodeError as e:
                                logger.error(f"[DB] Failed to decode phones json during clear: {e}")
                            except Exception as e:
                                logger.error(f"[DB] Error removing from special lists: {e}")

                        c.execute(f"DELETE FROM contacts WHERE uid IN ({placeholders})", batch)
                    self.conn_contacts.commit()
                    logger.info(f"[DB] Synced: Removed {len(to_delete)} stale contacts for source {source_uid}")

            GLib.idle_add(self.emit, 'contacts-updated')
            return True
        except Exception as e:
            logger.error(f"[DB] Sync Deleted Contacts Error: {e}")
            return False
