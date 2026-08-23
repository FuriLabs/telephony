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
import os
import re
import shutil
import sqlite3
import threading
from gettext import gettext as _

from gi.repository import GLib, GObject
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.datetime_utils import format_timestamp
from telephony.shared.utils.phone_utils import build_search_variants, normalize_number
from telephony.shared.utils.thread_utils import run_in_background

SQLITE_MAX_BATCH_VARIABLES = 900
SQLITE_CACHE_KIB = 512
PHONE_VARIANT_PATTERN = re.compile(r"[0-9+*#]+")


HISTORY_COLUMNS = ("id", "number", "name", "direction", "duration",
                   "timestamp", "anonymous", "multiparty", "transferred",
                   "disconnect_reason")


class DatabaseManager(GObject.Object):
    """
    Manages SQLite databases for settings, calls, messages, contacts, and blocklist.
    """

    __gsignals__ = {
        'history-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'messages-updated': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'blocklist-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'contacts-updated': (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self, eds_manager, gsettings_mgr=None, owns_writes=True):
        """Initialize the database manager.

        owns_writes is False in a window process, whose writes must go
        through the daemon interface; every data write refuses locally
        so a bypass cannot creep back in. Schema setup stays allowed
        because every process opens the same files.
        """
        GObject.Object.__init__(self)
        self.eds = eds_manager
        self.gsettings_mgr = gsettings_mgr
        self.owns_writes = owns_writes

        self.conn_calls = None
        self.conn_messages = None
        self.conn_blocklist = None
        self.conn_contacts = None

        self.lock = threading.Lock()
        self.init_dbs()

    def tune_connection(self, conn):
        """Apply WAL journaling, relaxed sync and a bounded page cache.

        sqlite would otherwise keep up to two megabytes of pages warm
        per connection, and this manager holds four connections in
        every process for databases read in occasional bursts.
        """
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
        except Exception as e:
            logger.warning(f"[DB] Connection tuning failed: {e}")

    def init_dbs(self):
        """Open every database; only the writing side touches the schema.

        A window may open files the daemon has not migrated yet on the
        very first boot; its reads degrade gracefully and heal on the
        next refresh once the daemon has run.
        """
        try:
            data_dir = os.path.join(GLib.get_user_data_dir(), "telephony")

            if not os.path.exists(data_dir):
                os.makedirs(data_dir, exist_ok=True)

            att_dir = os.path.join(data_dir, "attachments")
            if not os.path.exists(att_dir):
                os.makedirs(att_dir, exist_ok=True)

            self.conn_contacts = sqlite3.connect(os.path.join(data_dir, "contacts.db"), check_same_thread=False)
            self.tune_connection(self.conn_contacts)
            self.conn_calls = sqlite3.connect(os.path.join(data_dir, "calls.db"), check_same_thread=False)
            self.tune_connection(self.conn_calls)
            self.conn_messages = sqlite3.connect(os.path.join(data_dir, "messages.db"), check_same_thread=False)
            self.tune_connection(self.conn_messages)
            self.conn_blocklist = sqlite3.connect(os.path.join(data_dir, "blocklist.db"), check_same_thread=False)
            self.tune_connection(self.conn_blocklist)

            if self.owns_writes:
                self.migrate_schema()

            logger.info("[DB] Databases initialized.")
        except Exception as e:
            logger.error(f"[DB] Init Error: {e}")

    def migrate_schema(self):
        """Create and upgrade every table; daemon only, the single writer."""
        try:
            c = self.conn_contacts.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS contacts
                         (uid TEXT PRIMARY KEY,
                          source_uid TEXT NOT NULL,
                          name TEXT,
                          phones TEXT,
                          emails TEXT,
                          vcard TEXT,
                          search_index_name TEXT,
                          search_index_phones TEXT,
                          is_fav INTEGER DEFAULT 0,
                          vcard_hash TEXT)''')
            c.execute("CREATE INDEX IF NOT EXISTS idx_source_uid ON contacts(source_uid)")

            self.conn_contacts.commit()

            c = self.conn_calls.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS history
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          number TEXT,
                          name TEXT,
                          direction TEXT,
                          duration INTEGER,
                          timestamp DATETIME,
                          anonymous INTEGER DEFAULT 0,
                    multiparty INTEGER DEFAULT 0,
                    transferred INTEGER DEFAULT 0)''')
            history_cols = [row[1] for row in c.execute("PRAGMA table_info(history)").fetchall()]
            for column in ("anonymous", "multiparty", "transferred"):
                if column not in history_cols:
                    c.execute(f"ALTER TABLE history ADD COLUMN {column} INTEGER DEFAULT 0")
            if "disconnect_reason" not in history_cols:
                c.execute("ALTER TABLE history ADD COLUMN disconnect_reason TEXT DEFAULT NULL")
            c.execute("CREATE INDEX IF NOT EXISTS idx_history_ts ON history(timestamp DESC, id DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_history_number ON history(number)")
            self.conn_calls.commit()

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
            c.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(remote_number, timestamp DESC, id DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_messages_unread ON messages(remote_number) WHERE status='unread' AND direction='incoming'")

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
            self.upgrade_schema_messages()
            self.rebuild_fts()

            c = self.conn_blocklist.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS blocklist
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          number TEXT UNIQUE NOT NULL,
                          note TEXT,
                          block_calls INTEGER DEFAULT 1,
                          block_messages INTEGER DEFAULT 1)'''
                      )
            block_cols = [row[1] for row in c.execute("PRAGMA table_info(blocklist)").fetchall()]
            for column in ("block_calls", "block_messages"):
                if column not in block_cols:
                    c.execute(f"ALTER TABLE blocklist ADD COLUMN {column} INTEGER DEFAULT 1")
            self.conn_blocklist.commit()
        except Exception as e:
            logger.error(f"[DB] Migrate Error: {e}")

    def upgrade_schema_messages(self):
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

    def rebuild_fts(self):
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
                except sqlite3.OperationalError as e:
                    logger.debug(f"[DB] FTS docsize table unavailable, assuming empty index: {e}")

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

    def refuse_write(self, action):
        """Return True and log when this process may not write the databases."""
        if self.owns_writes:
            return False
        logger.error(f"[DB] Refusing {action}: only the daemon writes the database")
        return True

    def set_group_name(self, recipients_list, name):
        """Set a custom name for a group conversation."""
        if self.refuse_write("set_group_name"):
            return False
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
            return True
        except Exception as e:
            logger.error(f"[DB] Set Group Name Error: {e}")
            return False

    def get_all_group_names(self):
        """Return the full mapping of conversation ids to custom group names."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT id, name FROM group_names")
                return dict(c.fetchall())
        except Exception as e:
            logger.error(f"[DB] Get Group Names Error: {e}")
            return {}

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
        if self.refuse_write("clear_messages"):
            return False
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

            GLib.idle_add(self.emit, 'messages-updated', "", "delete")
            return True
        except Exception as e:
            logger.error(f"[DB] Clear Messages Error: {e}")
            return False

    def count_rows(self, table):
        """Count what a wipe would take; blocking, call from a worker.

        Returns zero when the count cannot be read, since a question
        about deleting nothing is better than one that cannot be asked.
        """
        connections = {
            "history": self.conn_calls,
            "messages": self.conn_messages,
            "group_names": self.conn_messages,
            "blocklist": self.conn_blocklist,
        }
        connection = connections.get(table)
        if connection is None:
            logger.error(f"[DB] No connection for counting {table}")
            return 0
        try:
            with self.lock:
                c = connection.cursor()
                c.execute(f"SELECT COUNT(*) FROM {table}")
                row = c.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"[DB] Count {table} error: {e}")
            return 0

    def clear_group_names(self):
        """Delete all custom group names."""
        if self.refuse_write("clear_group_names"):
            return False
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
        if self.refuse_write("clear_blocklist"):
            return False
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
        if self.refuse_write("clear_history"):
            return
        try:
            with self.lock:
                c = self.conn_calls.cursor()
                c.execute("DELETE FROM history")
                self.conn_calls.commit()
            GLib.idle_add(self.emit, 'history-updated')
        except Exception as e:
            logger.error(f"[DB] Clear History Error: {e}")


    def clear_everything(self):
        """Clear all data from all databases."""
        if self.refuse_write("clear_everything"):
            return False
        self.clear_messages()

        try:
            with self.lock:
                self.conn_calls.execute("DELETE FROM history")
                self.conn_calls.commit()
                self.conn_blocklist.execute("DELETE FROM blocklist")
                self.conn_blocklist.commit()
                self.conn_messages.execute("DELETE FROM group_names")
                self.conn_messages.commit()

                self.conn_calls.execute("VACUUM")
                self.conn_blocklist.execute("VACUUM")


                GLib.idle_add(self.emit, 'history-updated')
                GLib.idle_add(self.emit, 'blocklist-updated')

                return True
        except Exception as e:
            logger.error(f"[DB] Clear Everything Error: {e}")
            return False

    def add_call(self, number, _name_ignored, direction, duration=0, anonymous=False,
                 multiparty=False, transferred=False, disconnect_reason=None):
        """Add a call entry to history."""
        if self.refuse_write("add_call"):
            return

        def task():
            try:
                norm_number = normalize_number(number, permissive=False)
                real_name = "Unknown"
                if self.eds:
                    real_name = self.eds.get_contact_name(norm_number) or "Unknown"
                now_str = format_timestamp()

                with self.lock:
                    c = self.conn_calls.cursor()
                    c.execute("INSERT INTO history (number, name, direction, duration, timestamp, anonymous, multiparty, transferred, disconnect_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (norm_number, real_name, direction, duration, now_str, 1 if anonymous else 0,
                               1 if multiparty else 0, 1 if transferred else 0, disconnect_reason))
                    self.conn_calls.commit()
                GLib.idle_add(self.emit, 'history-updated')
            except Exception as e:
                logger.error(f"[DB] Add Call Error: {e}")
        run_in_background(task)

    def get_history(self, direction=None, limit=200, offset=0):
        """Retrieve call history with optional filtering."""
        try:
            with self.lock:
                c = self.conn_calls.cursor()
                sql = "SELECT id, number, name, direction, duration, timestamp, anonymous, multiparty, transferred, disconnect_reason FROM history"
                params = []

                if direction and direction != "all":
                    if direction == "incoming":
                        sql += " WHERE direction IN ('incoming', 'missed', 'rejected')"
                    elif direction == "outgoing":
                        sql += " WHERE direction IN ('outgoing', 'cancelled')"
                    else:
                        sql += " WHERE direction = ?"
                        params.append(direction)

                sql += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                c.execute(sql, params)
                return [dict(zip(HISTORY_COLUMNS, r)) for r in c.fetchall()]
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

                sql = "SELECT id, number, name, direction, duration, timestamp, anonymous, multiparty, transferred, disconnect_reason FROM history WHERE "
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
                return [dict(zip(HISTORY_COLUMNS, r)) for r in c.fetchall()]
        except Exception as e:
            logger.error(f"[DB] Search History Error: {e}")
            return []

    def delete_call_by_id(self, call_id):
        """Delete a specific call entry by ID."""
        if self.refuse_write("delete_call_by_id"):
            return

        def task():
            try:
                with self.lock:
                    c = self.conn_calls.cursor()
                    c.execute("DELETE FROM history WHERE id=?", (call_id,))
                    self.conn_calls.commit()
                GLib.idle_add(self.emit, 'history-updated')
            except Exception as e:
                logger.error(f"[DB] Delete Call Error: {e}")
        run_in_background(task)

    def update_history_names(self, numbers, new_name=None):
        """Update the name in call history for specific numbers."""
        if not numbers:
            return
        if self.refuse_write("update_history_names"):
            return
        try:
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
            GLib.idle_add(self.emit, 'history-updated')
        except Exception as e:
            logger.error(f"[DB] Update history names error: {e}")

    def normalize_chat_id(self, number_or_list):
        """Normalize a recipient or recipient list to the stored conversation id."""
        if isinstance(number_or_list, list):
            cleaned = [normalize_number(n, permissive=True) for n in number_or_list]
            return ",".join(sorted(cleaned))
        if "," in str(number_or_list):
            return number_or_list
        return normalize_number(number_or_list, permissive=True)

    def conversation_exists(self, number_or_list):
        """Check whether a conversation already exists for the given recipients."""
        try:
            chat_id = self.normalize_chat_id(number_or_list)
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT 1 FROM messages WHERE remote_number=? AND status != 'draft' LIMIT 1", (chat_id,))
                return c.fetchone() is not None
        except Exception as e:
            logger.error(f"[DB] Conversation exists check error: {e}")
            return False

    def get_conversation_summary(self, number):
        """Return the latest message and unread count for one conversation."""
        try:
            chat_id = self.normalize_chat_id(number)
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''SELECT body, timestamp, status,
                                    (SELECT COUNT(*) FROM messages WHERE remote_number=? AND status='unread' AND direction='incoming')
                             FROM messages WHERE remote_number=?
                             ORDER BY timestamp DESC, id DESC LIMIT 1''', (chat_id, chat_id))
                return c.fetchone()
        except Exception as e:
            logger.error(f"[DB] Get Conversation Summary Error: {e}")
            return None

    def get_conversation_ids(self):
        """Return all distinct conversation ids."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT DISTINCT remote_number FROM messages WHERE status != 'draft'")
                return [r[0] for r in c.fetchall() if r[0]]
        except Exception as e:
            logger.error(f"[DB] Get Conversation Ids Error: {e}")
            return []

    def add_message(self, remote_number, direction, body, status="unread", subject=None, attachments=[], sender=None, scheduled_timestamp=None):
        """Add a message to the database."""
        if not remote_number or not direction:
            logger.warning("[DB] Cannot add message: missing remote_number or direction.")
            return None
        if self.refuse_write("add_message"):
            return None
        try:
            norm_number = ""
            if isinstance(remote_number, list):
                cleaned = [normalize_number(n, permissive=True) for n in remote_number]
                norm_number = ",".join(sorted(cleaned))
            elif "," in str(remote_number):
                norm_number = remote_number
            else:
                norm_number = normalize_number(remote_number, permissive=True)

            att_json = json.dumps(attachments) if attachments else "[]"
            msg_type = 'mms' if (subject or attachments) else 'sms'

            if not sender:
                sender = "Me" if direction == "outgoing" else norm_number

            now_str = format_timestamp()
            final_ts = now_str
            if status == "scheduled" and scheduled_timestamp:
                final_ts = scheduled_timestamp

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''INSERT INTO messages
                             (remote_number, direction, body, status, timestamp, type, subject, attachments, sender, scheduled_timestamp)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (norm_number, direction, body, status, final_ts, msg_type, subject, att_json, sender, scheduled_timestamp))
                self.conn_messages.commit()
                rowid = c.lastrowid

            GLib.idle_add(self.emit, 'messages-updated', norm_number, "insert")
            return rowid
        except Exception as e:
            logger.error(f"[DB] Add Message Error: {e}")
            return None

    def get_missed_scheduled_messages(self, timestamp_limit):
        """Retrieve scheduled messages that were missed (older than limit)."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''SELECT id, remote_number, body, subject, attachments, scheduled_timestamp
                             FROM messages
                             WHERE status='scheduled' AND scheduled_timestamp < ?''', (timestamp_limit,))
                return c.fetchall()
        except Exception as e:
            logger.error(f"[DB] Get Missed Scheduled Messages Error: {e}")
            return []

    def get_recent_scheduled_messages(self, start_ts, end_ts):
        """Retrieve scheduled messages within a time window [start, end]."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''SELECT id, remote_number, body, subject, attachments, scheduled_timestamp
                             FROM messages
                             WHERE status='scheduled' AND scheduled_timestamp >= ? AND scheduled_timestamp <= ?''', (start_ts, end_ts))
                return c.fetchall()
        except Exception as e:
            logger.error(f"[DB] Get Recent Scheduled Messages Error: {e}")
            return []

    def get_next_scheduled_timestamp(self):
        """Get the timestamp of the next upcoming scheduled message."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT MIN(scheduled_timestamp) FROM messages WHERE status='scheduled'")
                row = c.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"[DB] Get Next Scheduled Timestamp Error: {e}")
            return None

    def get_message_details(self, msg_id):
        """Retrieve details of a single message."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''SELECT id, remote_number, body, subject, attachments, status
                             FROM messages WHERE id=?''', (msg_id,))
                return c.fetchone()
        except Exception as e:
            logger.error(f"[DB] Get Message Details Error: {e}")
            return None

    def update_message_status(self, msg_id, status):
        """Update a single message's status and notify listeners."""
        if self.refuse_write("update_message_status"):
            return False
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("UPDATE messages SET status=? WHERE id=?", (status, msg_id))
                c.execute("SELECT remote_number FROM messages WHERE id=?", (msg_id,))
                row = c.fetchone()
                self.conn_messages.commit()

            GLib.idle_add(self.emit, 'messages-updated', row[0] if row else "", "status")
            return True
        except Exception as e:
            logger.error(f"[DB] Update Status Error: {e}")
            return False

    def fail_stale_sending(self):
        """Mark messages stuck in sending state from a previous run as failed."""
        if self.refuse_write("fail_stale_sending"):
            return
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("UPDATE messages SET status='failed' WHERE status='sending'")
                changed = c.rowcount
                self.conn_messages.commit()

            if changed > 0:
                logger.warning(f"[DB] Marked {changed} stale sending messages as failed")
                GLib.idle_add(self.emit, 'messages-updated', "", "status")
        except Exception as e:
            logger.error(f"[DB] Fail Stale Sending Error: {e}")

    def update_message_schedule(self, msg_id, status="sent", timestamp=None):
        """Update message status or reschedule it."""
        if self.refuse_write("update_message_schedule"):
            return False
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                if status == "scheduled" and timestamp:
                    c.execute("UPDATE messages SET status=?, scheduled_timestamp=? WHERE id=?", (status, timestamp, msg_id))
                elif status in ("sent", "sending"):
                    now_str = format_timestamp()
                    c.execute("UPDATE messages SET status=?, timestamp=?, scheduled_timestamp=NULL WHERE id=?", (status, now_str, msg_id))
                else:
                    c.execute("UPDATE messages SET status=? WHERE id=?", (status, msg_id))
                c.execute("SELECT remote_number FROM messages WHERE id=?", (msg_id,))
                row = c.fetchone()
                self.conn_messages.commit()
                GLib.idle_add(self.emit, 'messages-updated', row[0] if row else "", "status")
                return True
        except Exception as e:
            logger.error(f"[DB] Update Schedule Error: {e}")
            return False

    def get_conversations(self, query=None, limit=50, offset=0, filter_type="all"):
        """Retrieve a list of recent conversations."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()

                base_query = '''
                    SELECT m.remote_number, m.body, m.timestamp,
                           (SELECT COUNT(*) FROM messages WHERE remote_number = m.remote_number AND status = 'unread' AND direction='incoming') as unread_count,
                           m.id,
                           m.status
                    FROM messages m
                    WHERE m.id IN (
                        SELECT id FROM messages m2
                        WHERE m2.remote_number = m.remote_number
                        ORDER BY m2.timestamp DESC, m2.id DESC LIMIT 1
                    )
                '''

                if filter_type == "unread":
                    base_query += " AND (SELECT COUNT(*) FROM messages WHERE remote_number = m.remote_number AND status = 'unread' AND direction='incoming') > 0"
                elif filter_type == "group":
                    base_query += " AND m.remote_number LIKE '%,%'"
                elif filter_type == "individual":
                    base_query += " AND m.remote_number NOT LIKE '%,%'"
                elif filter_type == "alphabetical":
                    base_query += " AND m.remote_number GLOB '*[a-zA-Z]*'"

                base_query += " ORDER BY m.timestamp DESC, m.id DESC"

                needs_contact_filter = filter_type in ("saved", "unknown")

                if not needs_contact_filter:
                    base_query += " LIMIT ? OFFSET ?"
                    c.execute(base_query, (limit, offset))
                    rows = c.fetchall()
                    return rows

                c.execute(base_query)
                all_rows = c.fetchall()

                filtered_rows = []
                contact_map = self.get_contacts_lookup_map()

                for r in all_rows:
                    num = str(r[0])
                    is_saved = False
                    if "," in num:
                        recipients = [n.strip() for n in num.split(',')]
                        for rc in recipients:
                            norm = normalize_number(rc)
                            if norm in contact_map:
                                is_saved = True
                                break
                    else:
                        norm = normalize_number(num)
                        if norm in contact_map:
                            is_saved = True

                    if filter_type == "saved" and is_saved:
                        filtered_rows.append(r)
                    elif filter_type == "unknown" and not is_saved:
                        filtered_rows.append(r)

                return filtered_rows[offset:offset+limit]
        except Exception as e:
            logger.error(f"[DB] Get Conversations Error: {e}")
            return []

    def search_messages(self, query, chat_id=None, limit=50, offset=0):
        """Search messages using FTS."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()

                words = query.split()
                fts_query = " AND ".join([f"{w}*" for w in words])

                sql = """
                    SELECT m.remote_number, m.body, m.timestamp, m.id
                    FROM messages_search fts
                    JOIN messages m ON fts.rowid = m.id
                    WHERE messages_search MATCH ?
                """
                params = [fts_query]

                if chat_id:
                    if "," in str(chat_id):
                        norm_id = chat_id
                    else:
                        norm_id = normalize_number(chat_id, permissive=True)

                    sql += " AND m.remote_number = ?"
                    params.append(norm_id)

                sql += " ORDER BY rank LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                c.execute(sql, params)
                return c.fetchall()
        except Exception as e:
            logger.error(f"[DB] Search Error: {e}")
            try:
                with self.lock:
                    c = self.conn_messages.cursor()
                    sql = "SELECT remote_number, body, timestamp, id FROM messages WHERE (body LIKE ? OR remote_number LIKE ?)"
                    like_query = f"%{query}%"
                    params = [like_query, like_query]

                    if chat_id:
                        sql += " AND remote_number = ?"
                        params.append(chat_id)

                    sql += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
                    params.extend([limit, offset])

                    c.execute(sql, params)
                    return c.fetchall()
            except Exception as e:
                logger.error(f"[DB] Fallback Search Error: {e}")
                return []

    def get_message_offset(self, msg_id):
        """Returns the number of messages newer than msg_id in its conversation."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT remote_number, timestamp FROM messages WHERE id=?", (msg_id,))
                target = c.fetchone()
                if not target:
                    return 0
                remote_number, target_ts = target
                c.execute("SELECT COUNT(*) FROM messages WHERE remote_number=? AND timestamp > ?", (remote_number, target_ts))
                row = c.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"[DB] Get Message Offset Error: {e}")
            return 0

    def get_chat_messages_around(self, msg_id, limit_before=20, limit_after=None):
        """Retrieve context messages around a specific message ID."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()

                c.execute("SELECT remote_number, timestamp FROM messages WHERE id=?", (msg_id,))
                target = c.fetchone()
                if not target:
                    return []

                remote_number, target_ts = target

                c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                             FROM messages
                             WHERE remote_number=? AND timestamp <= ? AND id != ?
                             ORDER BY timestamp DESC, id DESC LIMIT ?''',
                          (remote_number, target_ts, msg_id, limit_before))
                before = c.fetchall()

                if limit_after is not None:
                    c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                                 FROM messages
                                 WHERE remote_number=? AND timestamp >= ? AND id != ?
                                 ORDER BY timestamp ASC, id ASC LIMIT ?''',
                              (remote_number, target_ts, msg_id, limit_after))
                else:
                    c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                                 FROM messages
                                 WHERE remote_number=? AND timestamp >= ? AND id != ?
                                 ORDER BY timestamp ASC, id ASC''',
                              (remote_number, target_ts, msg_id))
                after = c.fetchall()

                c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                             FROM messages WHERE id=?''', (msg_id,))
                current = c.fetchall()

                full_list = before[::-1] + current + after

                return [
                    (
                        r[0], r[1], r[2], r[3], r[4], r[5],
                        json.loads(r[6]) if r[6] else [],
                        r[7] if len(r) > 7 else "Unknown",
                        r[8] if len(r) > 8 else None
                    )
                    for r in full_list
                ]
        except Exception as e:
            logger.error(f"[DB] Get Context Error: {e}")
            return []

    def get_chat_messages(self, number, limit=100, offset=0):
        """Retrieve messages for a specific conversation."""
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                             FROM messages WHERE remote_number=?
                             ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?''', (norm_number, limit, offset))

                raw_rows = c.fetchall()
                return [
                    (
                        r[0], r[1], r[2], r[3], r[4], r[5],
                        json.loads(r[6]) if r[6] else [],
                        r[7] if len(r) > 7 else "Unknown",
                        r[8] if len(r) > 8 else None
                    )
                    for r in raw_rows
                ]
        except Exception as e:
            logger.error(f"[DB] Get Chat Error: {e}")
            return []

    def mark_conversation_read(self, number):
        """Mark a conversation as read."""
        if self.refuse_write("mark_conversation_read"):
            return
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("UPDATE messages SET status='read' WHERE remote_number=? AND status='unread' AND direction='incoming'", (norm_number,))
                changed = c.rowcount
                self.conn_messages.commit()

            if changed > 0:
                GLib.idle_add(self.emit, 'messages-updated', norm_number, "status")
        except Exception as e:
            logger.error(f"[DB] Mark Read Error: {e}")

    def mark_conversation_unread_from_message(self, number, message_id):
        """Mark a message and all newer messages in the conversation as unread."""
        if self.refuse_write("mark_conversation_unread_from_message"):
            return
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT timestamp FROM messages WHERE id=?", (message_id,))
                row = c.fetchone()
                if not row:
                    return

                target_ts = row[0]
                c.execute("UPDATE messages SET status='unread' WHERE remote_number=? AND direction='incoming' AND status='read' AND (timestamp > ? OR (timestamp = ? AND id >= ?))", (norm_number, target_ts, target_ts, message_id))
                self.conn_messages.commit()
            GLib.idle_add(self.emit, 'messages-updated', norm_number, "status")
        except Exception as e:
            logger.error(f"[DB] Mark Unread Error: {e}")

    def get_unread_count(self, number):
        """Count unread incoming messages for a single conversation."""
        try:
            if isinstance(number, list):
                cleaned = [normalize_number(n, permissive=True) for n in number]
                norm_number = ",".join(sorted(cleaned))
            elif "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT COUNT(*) FROM messages WHERE remote_number=? AND status='unread' AND direction='incoming'", (norm_number,))
                row = c.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"[DB] Get Unread Count Error: {e}")
            return 0

    def get_total_unread_count(self):
        """Get the global count of unread messages."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT COUNT(*) FROM messages WHERE status='unread' AND direction='incoming'")
                row = c.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"[DB] Get Total Unread Error: {e}")
            return 0

    def delete_message(self, msg_id):
        """Delete a single message."""
        if self.refuse_write("delete_message"):
            return False
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT remote_number FROM messages WHERE id=?", (msg_id,))
                row = c.fetchone()
                c.execute("DELETE FROM messages WHERE id=?", (msg_id,))
                self.conn_messages.commit()
                GLib.idle_add(self.emit, 'messages-updated', row[0] if row else "", "delete")
                return True
        except Exception as e:
            logger.error(f"[DB] Delete Msg Error: {e}")
            return False

    def delete_drafts(self, number):
        """Delete all drafts for a conversation."""
        if self.refuse_write("delete_drafts"):
            return False
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("DELETE FROM messages WHERE remote_number=? AND status='draft'", (norm_number,))
                changed = c.rowcount
                self.conn_messages.commit()

            if changed > 0:
                GLib.idle_add(self.emit, 'messages-updated', norm_number, "draft")
            return True
        except Exception as e:
            logger.error(f"[DB] Delete Drafts Error: {e}")
            return False

    def delete_conversation(self, number):
        """Delete an entire conversation."""
        if self.refuse_write("delete_conversation"):
            return False
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("DELETE FROM messages WHERE remote_number=?", (norm_number,))
                self.conn_messages.commit()
                GLib.idle_add(self.emit, 'messages-updated', norm_number, "delete")
                return True
        except Exception as e:
            logger.error(f"[DB] Delete Conv Error: {e}")
            return False

    def set_blocked_flags(self, bid, block_calls, block_messages):
        """Set both domain flags of one blocklist entry."""
        if self.refuse_write("set_blocked_flags"):
            return False
        try:
            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute("UPDATE blocklist SET block_calls = ?, block_messages = ? WHERE id = ?",
                          (1 if block_calls else 0, 1 if block_messages else 0, bid))
                self.conn_blocklist.commit()
            GLib.idle_add(self.emit, 'blocklist-updated')
            return True
        except Exception as e:
            logger.error(f"[DB] Set Blocked Flags Error: {e}")
            return False

    def update_blocked_number(self, bid, number, note="", block_calls=True, block_messages=True):
        """Change one blocklist entry, its number included.

        The number is what a block is, so changing it changes this row
        rather than starting another one: the entry keeps its id, and
        the flags are written as given instead of merged, because an
        edit is the user saying what the block should be now.

        A number already blocked under another entry is refused rather
        than folded into it, since folding would silently take the
        other entry's settings and drop this one.

        Blocking, call from a worker.
        """
        if self.refuse_write("update_blocked_number"):
            return False
        try:
            clean_num = normalize_number(number, permissive=False)
            if not clean_num:
                logger.warning("[DB] Refusing a blocklist entry with no number")
                return False

            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute("SELECT id FROM blocklist WHERE number = ? AND id != ?", (clean_num, bid))
                if c.fetchone() is not None:
                    logger.warning(f"[DB] {clean_num} is already blocked under another entry")
                    return False

                c.execute("""UPDATE blocklist
                             SET number = ?, note = ?, block_calls = ?, block_messages = ?
                             WHERE id = ?""",
                          (clean_num, note, 1 if block_calls else 0,
                           1 if block_messages else 0, bid))
                self.conn_blocklist.commit()

            if self.gsettings_mgr:
                self.gsettings_mgr.remove_from_special_lists(clean_num)
            GLib.idle_add(self.emit, 'blocklist-updated')
            return True
        except Exception as e:
            logger.error(f"[DB] Update blocked number error: {e}")
            return False

    def import_blocklist(self, entries):
        """Merge imported entries; adds numbers and only ever raises flags."""
        if self.refuse_write("import_blocklist"):
            return (0, 0)
        before = {e["number"]: e for e in self.get_blocked_numbers()}
        added = 0
        updated = 0
        for entry in entries:
            number = str(entry.get("number", "")).strip()
            if not number:
                continue
            clean = normalize_number(number, permissive=False)
            ok = self.add_blocked_number(number, str(entry.get("note", "") or ""),
                                         bool(entry.get("block_calls", True)),
                                         bool(entry.get("block_messages", True)))
            if not ok:
                continue
            if clean in before:
                updated += 1
            else:
                added += 1
        return (added, updated)

    def block_number(self, number, note="", block_calls=True, block_messages=True):
        """
        Block a number and scrub it everywhere: rename it in call history,
        remove it from contacts and drop it from trusted and special lists.
        """
        if self.refuse_write("block_number"):
            return False
        clean_num = normalize_number(number, permissive=False)
        if not self.add_blocked_number(clean_num, note, block_calls, block_messages):
            return False

        self.update_history_names([clean_num], _("Blocked Number"))
        if self.eds:
            self.eds.remove_number_everywhere(clean_num)
        return True

    def unblock_number(self, bid, number=None):
        """Remove a blocklist entry and rename the number back to Unknown."""
        if self.refuse_write("unblock_number"):
            return
        if number is None:
            for entry in self.get_blocked_numbers():
                if entry["id"] == bid:
                    number = entry["number"]
                    break

        self.remove_blocked_number(bid)

        if number:
            clean_num = normalize_number(number, permissive=False)
            self.update_history_names([clean_num], _("Unknown"))

    def add_blocked_number(self, number, note="", block_calls=True, block_messages=True):
        """Add or widen a blocklist entry; merging never unblocks a domain."""
        if self.refuse_write("add_blocked_number"):
            return False
        try:
            clean_num = normalize_number(number, permissive=False)
            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute("""INSERT INTO blocklist (number, note, block_calls, block_messages)
                             VALUES (?, ?, ?, ?)
                             ON CONFLICT(number) DO UPDATE SET
                               block_calls = MAX(block_calls, excluded.block_calls),
                               block_messages = MAX(block_messages, excluded.block_messages),
                               note = CASE WHEN excluded.note != '' THEN excluded.note ELSE note END""",
                          (clean_num, note, 1 if block_calls else 0, 1 if block_messages else 0))
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
        if self.refuse_write("remove_blocked_number"):
            return
        try:
            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute("DELETE FROM blocklist WHERE id = ?", (bid,))
                self.conn_blocklist.commit()
            GLib.idle_add(self.emit, 'blocklist-updated')
        except Exception as e:
            logger.error(f"[DB] Unblock Error: {e}")

    def is_blocked(self, number, kind="calls"):
        """Check whether a number is blocked: for calls, messages, or any at all.

        Enforcement asks for its own domain; surfaces that only need to
        know whether an entry exists ask for any, since an entry always
        blocks at least one domain.
        """
        conditions = {"calls": "AND block_calls = 1",
                      "messages": "AND block_messages = 1",
                      "any": ""}
        condition = conditions.get(kind, conditions["calls"])
        try:
            clean_num = normalize_number(number, permissive=False)
            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute(f"SELECT id FROM blocklist WHERE number = ? {condition}", (clean_num,))
                return c.fetchone() is not None
        except Exception as e:
            logger.error(f"[DB] Block check error: {e}")
            return False

    def get_blocked_numbers(self):
        """Retrieve every blocklist entry as a dict with its domain flags."""
        try:
            with self.lock:
                c = self.conn_blocklist.cursor()
                c.execute("SELECT id, number, note, block_calls, block_messages FROM blocklist ORDER BY id DESC")
                return [{"id": r[0], "number": r[1], "note": r[2],
                         "block_calls": bool(r[3]), "block_messages": bool(r[4])} for r in c.fetchall()]
        except Exception as e:
            logger.error(f"[DB] Get Blocked Numbers Error: {e}")
            return []

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
            tokens = query.lower().split() if query else []
            if query and not tokens:
                return []

            allowed_sources = self.eds.loaded_source_uids() if self.eds else set()
            if not allowed_sources:
                return []

            where = ["source_uid IN ({})".format(",".join("?" for _ in allowed_sources))]
            params = sorted(allowed_sources)
            for token in tokens:
                clause = ["instr(search_index_name, ?) > 0"]
                params.append(token)
                for variant in build_search_variants(token):
                    if not PHONE_VARIANT_PATTERN.fullmatch(variant):
                        continue
                    clause.append("instr(search_index_phones, ?) > 0")
                    params.append(variant)
                where.append("(" + " OR ".join(clause) + ")")

            sql = ("SELECT uid, name, phones, emails, is_fav, source_uid FROM contacts"
                   + " WHERE " + " AND ".join(where)
                   + " ORDER BY is_fav DESC,"
                   + " CASE WHEN search_index_name IS NULL OR search_index_name = ''"
                   + " THEN 'zz_unknown' ELSE search_index_name END")
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
        """Return stored vcards for one source or for every loaded source."""
        try:
            with self.lock:
                c = self.conn_contacts.cursor()
                if source_uid:
                    c.execute("SELECT vcard FROM contacts WHERE source_uid=?", (source_uid,))
                else:
                    allowed = self.eds.loaded_source_uids() if self.eds else set()
                    if not allowed:
                        return []
                    placeholders = ",".join("?" for _ in allowed)
                    c.execute(f"SELECT vcard FROM contacts WHERE source_uid IN ({placeholders})",
                              sorted(allowed))
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
        """Insert or update a batch of cached contacts for one source."""
        if self.refuse_write("upsert_contacts_batch"):
            return False
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
        if self.refuse_write("delete_contact"):
            return False
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
        if self.refuse_write("sync_deleted_contacts"):
            return False
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
