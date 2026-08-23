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
import os
import shutil
from datetime import timedelta, timezone
from gettext import gettext as _
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.datetime_utils import parse_timestamp


def dt_to_unix(ts_str):
    """Convert timestamp string to unix epoch."""
    return int(parse_timestamp(ts_str).timestamp())


CHATTY_MM_SELF_USER = "invalid-0000000000000000"
CHATTY_ID_PHONE = 1
CHATTY_PROTOCOL_MMS_SMS = 1
CHATTY_MESSAGE_TYPE_TEXT = 1
CHATTY_STATUS_RECEIVED = 2
CHATTY_STATUS_SENT = 3
CHATTY_THREAD_DIRECT = 0
CHATTY_THREAD_GROUP = 1

CHATTY_V4_SCHEMA = '''
CREATE TABLE mime_type (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE);
CREATE TABLE files (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  url TEXT NOT NULL UNIQUE,
  path TEXT,
  mime_type_id INTEGER REFERENCES mime_type(id),
  status INT,
  size INTEGER);
CREATE TABLE file_metadata (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,
  width INTEGER,
  height INTEGER,
  duration INTEGER);
CREATE TABLE users (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL,
  alias TEXT,
  avatar_id INTEGER REFERENCES files(id),
  type INTEGER NOT NULL,
  UNIQUE (username, type));
CREATE TABLE accounts (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  password TEXT,
  enabled INTEGER DEFAULT 0,
  protocol INTEGER NOT NULL,
  UNIQUE (user_id, protocol));
CREATE TABLE threads (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  alias TEXT,
  avatar_id INTEGER REFERENCES files(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  type INTEGER NOT NULL,
  encrypted INTEGER DEFAULT 0,
  last_read_id INTEGER REFERENCES messages(id),
  visibility INT NOT NULL DEFAULT 0,
  notification INTEGER NOT NULL DEFAULT 1,
  UNIQUE (name, account_id, type));
CREATE TABLE thread_members (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  UNIQUE (thread_id, user_id));
CREATE TABLE messages (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  uid TEXT NOT NULL,
  thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  sender_id INTEGER REFERENCES users(id),
  user_alias TEXT,
  body TEXT NOT NULL,
  body_type INTEGER NOT NULL,
  direction INTEGER NOT NULL,
  time INTEGER NOT NULL,
  status INTEGER,
  encrypted INTEGER DEFAULT 0,
  preview_id INTEGER REFERENCES files(id),
  subject TEXT,
  UNIQUE (uid, thread_id, body, time));
CREATE TABLE mm_messages (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  protocol INTEGER NOT NULL,
  smsc TEXT,
  time_sent INTEGER,
  validity INTEGER,
  reference_number INTEGER);
CREATE TABLE message_files (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id),
  preview_id INTEGER REFERENCES files(id),
  UNIQUE (message_id, file_id));
'''


def export_linux_chatty(db_manager, dest_db_path):
    """Export messages to a Chatty history database (schema version 4).

    Produces the same table layout chatty-history.c creates on first run,
    including the modem-manager self user and account, so Chatty opens the
    result as its own. Attachments are not exported, message text only.
    """
    try:
        if os.path.exists(dest_db_path):
            os.remove(dest_db_path)

        conn = sqlite3.connect(dest_db_path)
        c = conn.cursor()
        c.executescript(CHATTY_V4_SCHEMA)
        c.execute("PRAGMA user_version = 4")

        c.execute("INSERT INTO users(username,type) VALUES (?,?)", (CHATTY_MM_SELF_USER, CHATTY_ID_PHONE))
        mm_user_id = c.lastrowid
        c.execute("INSERT INTO accounts(user_id,protocol) VALUES (?,?)", (mm_user_id, CHATTY_PROTOCOL_MMS_SMS))
        mm_account_id = c.lastrowid

        user_ids = {CHATTY_MM_SELF_USER: mm_user_id}

        def ensure_user(number):
            if number not in user_ids:
                c.execute("INSERT OR IGNORE INTO users(username,type) VALUES (?,?)", (number, CHATTY_ID_PHONE))
                c.execute("SELECT id FROM users WHERE username=? AND type=?", (number, CHATTY_ID_PHONE))
                user_ids[number] = c.fetchone()[0]
            return user_ids[number]

        thread_ids = {}

        def ensure_thread(remote_number):
            if remote_number not in thread_ids:
                members = remote_number.split(",")
                ttype = CHATTY_THREAD_GROUP if len(members) > 1 else CHATTY_THREAD_DIRECT
                c.execute("INSERT INTO threads(name,account_id,type) VALUES (?,?,?)",
                          (remote_number, mm_account_id, ttype))
                thread_id = c.lastrowid
                for member in members:
                    c.execute("INSERT OR IGNORE INTO thread_members(thread_id,user_id) VALUES (?,?)",
                              (thread_id, ensure_user(member)))
                thread_ids[remote_number] = (thread_id, ttype)
            return thread_ids[remote_number]

        count = 0
        with db_manager.lock:
            src_c = db_manager.conn_messages.cursor()
            src_c.execute("SELECT * FROM messages ORDER BY timestamp ASC, id ASC")
            rows = src_c.fetchall()
            col_names = [desc[0] for desc in src_c.description]

        for row in rows:
            row_dict = dict(zip(col_names, row))
            remote_number = row_dict.get('remote_number', '')
            if not remote_number:
                continue

            direction = row_dict.get('direction', 'incoming')
            thread_id, ttype = ensure_thread(remote_number)

            sender_id = None
            if direction == "incoming":
                sender = row_dict.get('sender') or ''
                if ttype == CHATTY_THREAD_GROUP and sender and sender != "Me":
                    sender_id = ensure_user(sender)
                elif ttype == CHATTY_THREAD_DIRECT:
                    sender_id = ensure_user(remote_number)
            elif ttype == CHATTY_THREAD_DIRECT:
                sender_id = ensure_user(remote_number)

            c.execute('''INSERT INTO messages
                         (uid, thread_id, sender_id, body, body_type, direction, time, status, subject)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (f"telephony-{row_dict.get('id', count)}", thread_id, sender_id,
                       row_dict.get('body') or '', CHATTY_MESSAGE_TYPE_TEXT,
                       1 if direction == "incoming" else -1,
                       dt_to_unix(row_dict.get('timestamp', '')),
                       CHATTY_STATUS_RECEIVED if direction == "incoming" else CHATTY_STATUS_SENT,
                       row_dict.get('subject')))
            c.execute("INSERT INTO mm_messages(message_id,account_id,protocol) VALUES (?,?,?)",
                      (c.lastrowid, mm_account_id, CHATTY_PROTOCOL_MMS_SMS))
            count += 1

        conn.commit()
        conn.close()
        return True, _("Exported {count} messages to Chatty database.").format(count=count)
    except Exception as e:
        logger.error(f"Chatty export error: {e}")
        return False, str(e)


def to_gom_iso(dt):
    """Serialize a local naive datetime the way gom does: ISO 8601 in UTC."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def export_linux_calls(db_manager, dest_db_path):
    """Export call history to a gnome-calls records.db.

    gnome-calls reads its records through gom, so the file must carry the
    _gom_version bookkeeping table and a 'calls' table where start/answered/end
    are ISO 8601 UTC strings; duration is derived, not stored. An inbound call
    without an answered time is what gnome-calls shows as missed.
    """
    try:
        if os.path.exists(dest_db_path):
            os.remove(dest_db_path)

        conn = sqlite3.connect(dest_db_path)
        c = conn.cursor()

        c.execute("CREATE TABLE IF NOT EXISTS '_gom_version' ('version' INTEGER)")
        c.execute("INSERT INTO _gom_version (version) VALUES (1)")
        c.execute("INSERT INTO _gom_version (version) VALUES (2)")
        c.execute('''CREATE TABLE IF NOT EXISTS 'calls' (
            'id' INTEGER PRIMARY KEY AUTOINCREMENT,
            'target' TEXT,
            'inbound' INTEGER,
            'start' BLOB,
            'answered' BLOB,
            'end' BLOB,
            'protocol' TEXT
        )''')

        count = 0
        with db_manager.lock:
            src_c = db_manager.conn_calls.cursor()
            src_c.execute("SELECT * FROM history ORDER BY timestamp ASC, id ASC")
            rows = src_c.fetchall()
            col_names = [desc[0] for desc in src_c.description]

        for row in rows:
            row_dict = dict(zip(col_names, row))
            number = row_dict.get('number', '')
            if not number:
                continue

            direction = row_dict.get('direction', 'incoming')
            try:
                duration = max(0, int(row_dict.get('duration') or 0))
            except (TypeError, ValueError):
                duration = 0
            start_dt = parse_timestamp(row_dict.get('timestamp', ''))

            inbound = 1 if direction in ("incoming", "missed", "rejected") else 0
            answered = None
            if direction in ("incoming", "outgoing"):
                answered = to_gom_iso(start_dt)
            end = to_gom_iso(start_dt + timedelta(seconds=duration))

            c.execute('''INSERT INTO calls (target, inbound, start, answered, 'end', protocol)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (number, inbound, to_gom_iso(start_dt), answered, end, "tel"))
            count += 1

        conn.commit()
        conn.close()
        return True, _("Exported {count} calls to Gnome Calls database.").format(count=count)
    except Exception as e:
        logger.error(f"Calls export error: {e}")
        return False, str(e)


def export_linux_telephony(db_manager, dest_db_path, is_messages=True):
    """
    Export by simply copying the Telephony sqlite file.

    In a true ETL sense, this is a direct raw data transfer where the source schema and destination schema are identical.
    """
    try:
        data_dir = db_manager.get_data_dir()
        source_db = os.path.join(data_dir, "messages.db" if is_messages else "calls.db")

        if not os.path.exists(source_db):
            return False, _("Source database not found.")

        shutil.copy2(source_db, dest_db_path)
        return True, _("Exported Telephony database successfully.")
    except Exception as e:
        logger.error(f"Telephony export error: {e}")
        return False, str(e)
