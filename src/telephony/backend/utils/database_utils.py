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

from .db_calls_utils import DbCallsMixin
from .db_messages_utils import DbMessagesMixin
from .db_blocklist_utils import DbBlocklistMixin
from .db_contacts_utils import DbContactsMixin

from .thread_utils import run_in_background
from .phone_utils import normalize_number


import os
import shutil
import sqlite3
import threading

from gi.repository import GLib, GObject
from loguru import logger


class DatabaseManager(GObject.Object, DbCallsMixin, DbMessagesMixin, DbBlocklistMixin, DbContactsMixin):
    """
    Manages SQLite databases for settings, calls, messages, contacts, and blocklist.
    """

    __gsignals__ = {
        'history-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'messages-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'blocklist-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'contacts-updated': (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self, eds_manager, gsettings_mgr=None):
        """Initialize the database manager."""
        GObject.Object.__init__(self)
        self.eds = eds_manager
        self.gsettings_mgr = gsettings_mgr

        self.conn_calls = None
        self.conn_messages = None
        self.conn_blocklist = None
        self.conn_contacts = None

        self._history_cache = None
        self._conversations_cache = None

        self.lock = threading.Lock()
        self.init_dbs()

    def preload_caches(self):
        """Preload initial data into RAM caches."""
        try:
            with self.lock:
                c = self.conn_calls.cursor()
                sql = "SELECT id, number, name, direction, duration, timestamp FROM history ORDER BY timestamp DESC, id DESC LIMIT 51"
                c.execute(sql)
                self._history_cache = c.fetchall()

            with self.lock:
                c = self.conn_messages.cursor()
                query_sql = '''
                    SELECT m.remote_number, m.body, m.timestamp,
                           (SELECT COUNT(*) FROM messages WHERE remote_number = m.remote_number AND status = 'unread') as unread_count,
                           m.id,
                           m.status
                    FROM messages m
                    WHERE m.id IN (
                        SELECT id FROM messages m2
                        WHERE m2.remote_number = m.remote_number
                        ORDER BY m2.timestamp DESC, m2.id DESC LIMIT 1
                    )
                    ORDER BY m.timestamp DESC, m.id DESC LIMIT 51
                '''
                c.execute(query_sql)
                self._conversations_cache = c.fetchall()

            logger.info("[DB] Caches preloaded.")
        except Exception as e:
            logger.error(f"[DB] Preload caches error: {e}")

    def invalidate_cache(self, cache_type="all"):
        """Invalidate specific cache to force reload on next access."""
        if cache_type == "history" or cache_type == "all":
            self._history_cache = None
        if cache_type == "conversations" or cache_type == "all":
            self._conversations_cache = None

    def init_dbs(self):
        """Initialize all database connections and tables."""
        try:
            data_dir = os.path.join(GLib.get_user_data_dir(), "telephony")

            if not os.path.exists(data_dir):
                os.makedirs(data_dir, exist_ok=True)

            att_dir = os.path.join(data_dir, "attachments")
            if not os.path.exists(att_dir):
                os.makedirs(att_dir, exist_ok=True)

            self.conn_contacts = sqlite3.connect(os.path.join(data_dir, "contacts.db"), check_same_thread=False)
            c = self.conn_contacts.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS contacts
                         (uid TEXT PRIMARY KEY,
                          source_uid TEXT NOT NULL,
                          name TEXT,
                          phones TEXT,
                          emails TEXT,
                          vcard TEXT,
                          search_index_name TEXT,
                          search_index_phones TEXT)''')
            c.execute("CREATE INDEX IF NOT EXISTS idx_source_uid ON contacts(source_uid)")

            schema_ver = self.gsettings_mgr.get_setting("contacts_schema_version") if self.gsettings_mgr else None
            if not schema_ver or int(schema_ver) < 1:
                logger.info("[DB] Migrating contacts cache to schema v1 (composite UIDs)...")
                c.execute("DELETE FROM contacts")
                if self.gsettings_mgr:
                    self.gsettings_mgr.set_setting("contacts_schema_version", "1")

            self.conn_contacts.commit()

            self.conn_calls = sqlite3.connect(os.path.join(data_dir, "calls.db"), check_same_thread=False)
            c = self.conn_calls.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS history
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          number TEXT,
                          name TEXT,
                          direction TEXT,
                          duration INTEGER,
                          timestamp DATETIME)''')
            self.conn_calls.commit()

            self.conn_messages = sqlite3.connect(os.path.join(data_dir, "messages.db"), check_same_thread=False)
            c = self.conn_messages.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS messages
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          remote_number TEXT,
                          direction TEXT,
                          body TEXT,
                          status TEXT,
                          timestamp DATETIME,
                          type TEXT DEFAULT 'sms',
                          subject TEXT DEFAULT NULL,
                          attachments TEXT DEFAULT '[]',
                          sender TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS group_names
                         (id TEXT PRIMARY KEY, name TEXT)''')

            try:
                c.execute("SELECT sql FROM sqlite_master WHERE name='messages_search'")
                row = c.fetchone()
                if row and "content=" not in row[0]:
                    logger.info("[DB] Dropping legacy FTS table")
                    c.execute("DROP TABLE messages_search")
            except Exception as e:
                logger.error(f"[DB] Legacy FTS Check Error: {e}")

            c.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS messages_search
                         USING fts5(body, remote_number, content='messages', content_rowid='id')''')

            c.execute('''CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                           INSERT INTO messages_search(rowid, body, remote_number) VALUES (new.id, new.body, new.remote_number);
                         END;''')
            c.execute('''CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                           INSERT INTO messages_search(messages_search, rowid, body, remote_number) VALUES('delete', old.id, old.body, old.remote_number);
                         END;''')
            c.execute('''CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                           INSERT INTO messages_search(messages_search, rowid, body, remote_number) VALUES('delete', old.id, old.body, old.remote_number);
                           INSERT INTO messages_search(rowid, body, remote_number) VALUES (new.id, new.body, new.remote_number);
                         END;''')

            self.conn_messages.commit()
            self._upgrade_schema_messages()
            self._rebuild_fts()

            self.conn_blocklist = sqlite3.connect(os.path.join(data_dir, "blocklist.db"), check_same_thread=False)
            c = self.conn_blocklist.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS blocklist
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          number TEXT UNIQUE NOT NULL,
                          note TEXT)''')
            self.conn_blocklist.commit()

            logger.info("[DB] Databases initialized.")
            run_in_background(self.preload_caches)
        except Exception as e:
            logger.error(f"[DB] Init Error: {e}")

    def _upgrade_schema_messages(self):
        """Check for and apply schema updates to the messages database."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("PRAGMA table_info(messages)")
                cols = [info[1] for info in c.fetchall()]

                if 'subject' not in cols:
                    logger.info("[DB] Upgrading messages schema: Adding 'subject'")
                    c.execute("ALTER TABLE messages ADD COLUMN subject TEXT DEFAULT NULL")

                if 'attachments' not in cols:
                    logger.info("[DB] Upgrading messages schema: Adding 'attachments'")
                    c.execute("ALTER TABLE messages ADD COLUMN attachments TEXT DEFAULT '[]'")

                if 'sender' not in cols:
                    logger.info("[DB] Upgrading messages schema: Adding 'sender'")
                    c.execute("ALTER TABLE messages ADD COLUMN sender TEXT")

                if 'scheduled_timestamp' not in cols:
                    logger.info("[DB] Upgrading messages schema: Adding 'scheduled_timestamp'")
                    c.execute("ALTER TABLE messages ADD COLUMN scheduled_timestamp TEXT DEFAULT NULL")

                self.conn_messages.commit()
        except Exception as e:
            logger.error(f"[DB] Schema Upgrade Error: {e}")

    def _rebuild_fts(self):
        """Rebuild the Full Text Search index if needed."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                fts_count = 0
                try:
                    c.execute("SELECT count(*) FROM messages_search_docsize")
                    row = c.fetchone()
                    if row:
                        fts_count = row[0]
                except sqlite3.OperationalError:
                    pass

                c.execute("SELECT count(*) FROM messages")
                msg_count = c.fetchone()[0]

                if fts_count == 0 and msg_count > 0:
                    logger.info("[DB] Rebuilding FTS index...")
                    c.execute("INSERT INTO messages_search(messages_search) VALUES('rebuild')")
                    self.conn_messages.commit()
        except Exception as e:
            logger.error(f"[DB] FTS Rebuild Error: {e}")

    def get_data_dir(self):
        """Return the application data directory."""
        return os.path.join(GLib.get_user_data_dir(), "telephony")

    def set_group_name(self, recipients_list, name):
        """Set a custom name for a group conversation."""
        try:
            sorted_nums = sorted([normalize_number(n, permissive=True) for n in recipients_list])
            group_id = ",".join(sorted_nums)

            with self.lock:
                c = self.conn_messages.cursor()
                if not name:
                    c.execute("DELETE FROM group_names WHERE id=?", (group_id,))
                else:
                    c.execute("INSERT OR REPLACE INTO group_names (id, name) VALUES (?, ?)", (group_id, name))
                self.conn_messages.commit()
        except Exception as e:
            logger.error(f"[DB] Set Group Name Error: {e}")

    def get_group_name(self, recipients_list):
        """Get the custom name for a group conversation."""
        try:
            sorted_nums = sorted([normalize_number(n, permissive=True) for n in recipients_list])
            group_id = ",".join(sorted_nums)
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT name FROM group_names WHERE id=?", (group_id,))
                row = c.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"[DB] Get Group Name Error: {e}")
            return None

    def clear_messages(self):
        """Delete all messages and attachments."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("DELETE FROM messages")
                self.conn_messages.commit()

            att_dir = os.path.join(self.get_data_dir(), "attachments")
            if os.path.exists(att_dir):
                shutil.rmtree(att_dir)
                os.makedirs(att_dir)

            with self.lock:
                self.conn_messages.execute("VACUUM")

            self.invalidate_cache("conversations")
            GLib.idle_add(self.emit, 'messages-updated')
            return True
        except Exception as e:
            logger.error(f"[DB] Clear Messages Error: {e}")
            return False

    def clear_group_names(self):
        """Delete all custom group names."""
        try:
            with self.lock:
                self.conn_messages.execute("DELETE FROM group_names")
                self.conn_messages.commit()
            return True
        except Exception as e:
            logger.error(f"[DB] Clear Group Names Error: {e}")
            return False

    def clear_blocklist(self):
        """Delete all blocked numbers."""
        try:
            with self.lock:
                self.conn_blocklist.execute("DELETE FROM blocklist")
                self.conn_blocklist.commit()
            GLib.idle_add(self.emit, 'blocklist-updated')
            return True
        except Exception as e:
            logger.error(f"[DB] Clear Blocklist Error: {e}")
            return False

    def clear_history(self):
        """Delete all call history."""
        try:
            with self.lock:
                c = self.conn_calls.cursor()
                c.execute("DELETE FROM history")
                self.conn_calls.commit()
            self.invalidate_cache("history")
            GLib.idle_add(self.emit, 'history-updated')
        except Exception as e:
            logger.error(f"[DB] Clear History Error: {e}")

    def clear_settings(self):
        """Delete all settings."""
        try:
            return True
        except Exception as e:
            logger.error(f"[DB] Clear Settings Error: {e}")
            return False

    def clear_everything(self):
        """Clear all data from all databases."""
        self.clear_messages()
        self.clear_settings()

        try:
            with self.lock:
                self.conn_calls.execute("DELETE FROM history")
                self.conn_calls.commit()
                self.conn_blocklist.execute("DELETE FROM blocklist")
                self.conn_blocklist.commit()
                self.clear_group_names()

                self.conn_calls.execute("VACUUM")
                self.conn_blocklist.execute("VACUUM")

                self.invalidate_cache("all")

                GLib.idle_add(self.emit, 'history-updated')
                GLib.idle_add(self.emit, 'blocklist-updated')

                return True
        except Exception as e:
            logger.error(f"[DB] Clear Everything Error: {e}")
            return False
