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

import sqlite3

from loguru import logger
from gi.repository import GLib

from .phone_utils import normalize_number


class DbBlocklistMixin:
    def add_blocked_number(self, number, note=""):
        """Add a number to the blocklist."""
        try:
            clean_num = normalize_number(number, permissive=False)
            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute("INSERT INTO blocklist (number, note) VALUES (?, ?)", (clean_num, note))
                self.conn_blocklist.commit()

            if self.gsettings_mgr:
                self.gsettings_mgr.remove_from_special_lists(clean_num)
            GLib.idle_add(self.emit, 'blocklist-updated')
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            logger.error(f"[DB] Block Error: {e}")
            return False

    def remove_blocked_number(self, bid):
        """Remove a number from the blocklist by ID."""
        try:
            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute("DELETE FROM blocklist WHERE id = ?", (bid,))
                self.conn_blocklist.commit()
            GLib.idle_add(self.emit, 'blocklist-updated')
        except Exception as e:
            logger.error(f"[DB] Unblock Error: {e}")

    def is_blocked(self, number):
        """Check if a number is blocked."""
        try:
            clean_num = normalize_number(number, permissive=False)
            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute("SELECT id FROM blocklist WHERE number = ?", (clean_num,))
                return c.fetchone() is not None
        except Exception as e:
            logger.error(f"[DB] Block check error: {e}")
            return False

    def get_blocked_numbers(self):
        """Retrieve all blocked numbers."""
        try:
            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute("SELECT id, number, note FROM blocklist ORDER BY id DESC")
                return c.fetchall()
        except Exception as e:
            logger.error(f"[DB] Get Blocked Numbers Error: {e}")
            return []
