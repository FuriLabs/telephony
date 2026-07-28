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

from gettext import gettext as _
from loguru import logger
from gi.repository import GLib

from .phone_utils import normalize_number


class DbBlocklistUtils:
    def block_number(self, number, note=""):
        """
        Block a number and scrub it everywhere: rename it in call history,
        remove it from contacts and drop it from trusted and special lists.
        """
        clean_num = normalize_number(number, permissive=False)
        if not self.add_blocked_number(clean_num, note):
            return False

        self.update_history_names([clean_num], _("Blocked Number"))
        if self.eds:
            self.eds.remove_number_everywhere(clean_num)
        return True

    def unblock_number(self, bid, number=None):
        """Remove a blocklist entry and rename the number back to Unknown."""
        if number is None:
            for row_id, row_num, _note in self.get_blocked_numbers():
                if row_id == bid:
                    number = row_num
                    break

        self.remove_blocked_number(bid)

        if number:
            clean_num = normalize_number(number, permissive=False)
            self.update_history_names([clean_num], _("Unknown"))

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
