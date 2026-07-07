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

from ..utils.phone_utils import normalize_number


class EdsCacheMixin:
    def _rebuild_lookup_map(self):
        """Rebuild the phone number lookup map based on current cache and ranks."""
        with self.sources_lock:
            ranks = {uid: info.get('rank', 999) for uid, info in self.sources.items()}

        with self.cache_lock:
            self.lookup_map = {}

            for uid, contact in self.cache.items():
                source_uid = contact.get('source_uid')
                rank = ranks.get(source_uid, 999)

                if 'phones' in contact:
                    for p_data in contact['phones']:
                        norm = normalize_number(p_data[0])
                        if norm:
                            if norm not in self.lookup_map:
                                self.lookup_map[norm] = []
                            self.lookup_map[norm].append((rank, contact['name'], source_uid, uid))
