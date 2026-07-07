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

from .thread_utils import run_in_background
from .datetime_utils import format_timestamp

from loguru import logger
from gi.repository import GLib

from .phone_utils import normalize_number


class DbCallsMixin:
    def add_call(self, number, _name_ignored, direction, duration=0):
        """Add a call entry to history."""
        def task():
            try:
                norm_number = normalize_number(number, permissive=False)
                real_name = "Unknown"
                if self.eds:
                    real_name = self.eds.get_contact_name(norm_number) or "Unknown"
                now_str = format_timestamp()

                with self.lock:
                    c = self.conn_calls.cursor()
                    c.execute("INSERT INTO history (number, name, direction, duration, timestamp) VALUES (?, ?, ?, ?, ?)",
                              (norm_number, real_name, direction, duration, now_str))
                    self.conn_calls.commit()
                self.invalidate_cache("history")
                GLib.idle_add(self.emit, 'history-updated')
            except Exception as e:
                logger.error(f"[DB] Add Call Error: {e}")
        run_in_background(task)

    def get_history(self, direction=None, limit=200, offset=0):
        """Retrieve call history with optional filtering."""
        try:
            if offset == 0 and (not direction or direction == "all") and self._history_cache is not None:
                if len(self._history_cache) >= limit or len(self._history_cache) < 51:
                    return self._history_cache[:limit]

            with self.lock:
                c = self.conn_calls.cursor()
                sql = "SELECT id, number, name, direction, duration, timestamp FROM history"
                params = []

                if direction and direction != "all":
                    if direction == "incoming":
                        sql += " WHERE direction IN ('incoming', 'missed')"
                    elif direction == "outgoing":
                        sql += " WHERE direction IN ('outgoing', 'cancelled')"
                    else:
                        sql += " WHERE direction = ?"
                        params.append(direction)

                sql += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                c.execute(sql, params)
                return c.fetchall()
        except Exception as e:
            logger.error(f"[DB] Get History Error: {e}")
            return []

    def search_history(self, query, limit=50, offset=0):
        """Search call history by number or contact name."""
        try:
            found_numbers = set()
            if self.eds:
                contacts = self.eds.search_contacts(query)
                for c in contacts:
                    if c[3]:
                        for p in c[3]:
                            found_numbers.add(p[0])

            with self.lock:
                c = self.conn_calls.cursor()

                sql = "SELECT id, number, name, direction, duration, timestamp FROM history WHERE "
                params = []

                conditions = ["number LIKE ?"]
                params.append(f"%{query}%")

                if found_numbers:
                    placeholders = ",".join("?" * len(found_numbers))
                    conditions.append(f"number IN ({placeholders})")
                    params.extend(list(found_numbers))

                sql += f"({' OR '.join(conditions)})"
                sql += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                c.execute(sql, params)
                return c.fetchall()
        except Exception as e:
            logger.error(f"[DB] Search History Error: {e}")
            return []

    def delete_call_by_id(self, call_id):
        """Delete a specific call entry by ID."""
        def task():
            try:
                with self.lock:
                    c = self.conn_calls.cursor()
                    c.execute("DELETE FROM history WHERE id=?", (call_id,))
                    self.conn_calls.commit()
                self.invalidate_cache("history")
                GLib.idle_add(self.emit, 'history-updated')
            except Exception as e:
                logger.error(f"[DB] Delete Call Error: {e}")
        run_in_background(task)

    def update_history_names(self, numbers, new_name=None):
        """Update the name in call history for specific numbers."""
        try:
            if not numbers:
                return
            with self.lock:
                c = self.conn_calls.cursor()
                placeholders = ",".join("?" * len(numbers))

                if new_name:
                    sql = f"UPDATE history SET name=? WHERE number IN ({placeholders})"
                    params = [new_name] + list(numbers)
                else:
                    sql = f"UPDATE history SET name=number WHERE number IN ({placeholders})"
                    params = list(numbers)

                c.execute(sql, params)
                self.conn_calls.commit()
                logger.info(f"[DB] Updated history names for {len(numbers)} numbers.")
            self.invalidate_cache("history")
            GLib.idle_add(self.emit, 'history-updated')
        except Exception as e:
            logger.error(f"[DB] Update history names error: {e}")
