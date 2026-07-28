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

from ..utils.thread_utils import run_in_background

from gi.repository import GLib
from ..utils.phone_utils import normalize_number
from ..utils.vcard_utils import parse_contact_safe


class EdsEventsManager:
    def _handle_backend_update(self, contacts, source_uid):
        """Handle updates from the EDS backend for a specific source."""
        if source_uid not in self.sources:
            return

        rank = self.sources[source_uid]['rank']
        db_batch = []
        for c in contacts:
            data = parse_contact_safe(c, source_uid)
            uid = data.get('uid')
            if not uid:
                continue

            with self.cache_lock:
                existing = self.cache.get(uid)

            if (existing and
                    existing.get('name') == data.get('name') and
                    existing.get('phones') == data.get('phones') and
                    existing.get('emails') == data.get('emails') and
                    existing.get('vcard_hash') == data.get('vcard_hash')):
                continue

            with self.cache_lock:
                for p in existing.get('phones', []) if existing else []:
                    norm = normalize_number(p[0])
                    if norm and norm in self.lookup_map:
                        self.lookup_map[norm] = [x for x in self.lookup_map[norm] if x[3] != uid]
                        if not self.lookup_map[norm]:
                            del self.lookup_map[norm]

                cache_data = data.copy()
                cache_data.pop('vcard', None)
                self.cache[uid] = cache_data

                for p_data in data['phones']:
                    norm = normalize_number(p_data[0])
                    if not norm:
                        continue
                    self.lookup_map.setdefault(norm, []).append((rank, data['name'], source_uid, uid))
                    if existing:
                        GLib.idle_add(lambda n=norm, fn=data['name']: self.gsettings_mgr.update_special_list_names(n, fn) or False)

            db_batch.append(data)

        if db_batch:
            self.db_ref.upsert_contacts_batch(db_batch, source_uid)
            GLib.idle_add(self.emit, 'contacts-loaded')

    def _on_objects_added(self, view, contacts, source_uid):
        """Handle objects added signal."""
        run_in_background(self._handle_backend_update, contacts, source_uid)

    def _on_objects_modified(self, view, contacts, source_uid):
        """Handle objects modified signal."""
        run_in_background(self._handle_backend_update, contacts, source_uid)

    def _on_objects_removed(self, view, uids, source_uid):
        """Handle objects removed signal."""
        def task():
            with self.cache_lock:
                for real_uid in uids:
                    uid = self._make_composite_uid(source_uid, real_uid)
                    contact = self.cache.pop(uid, None)
                    for p in contact.get('phones', []) if contact else []:
                        norm = normalize_number(p[0])
                        if norm and norm in self.lookup_map:
                            self.lookup_map[norm] = [x for x in self.lookup_map[norm] if x[3] != uid]
                            if not self.lookup_map[norm]:
                                del self.lookup_map[norm]
                    self.db_ref.delete_contact(uid)
            GLib.idle_add(self.emit, 'contacts-loaded')

        run_in_background(task)
