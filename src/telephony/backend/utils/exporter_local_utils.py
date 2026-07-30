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
from gettext import gettext as _
from loguru import logger
from .datetime_utils import parse_timestamp


def _dt_to_unix(ts_str):
    """Convert timestamp string to unix epoch."""
    return int(parse_timestamp(ts_str).timestamp())


def export_linux_chatty(db_manager, dest_db_path):
    """
    Export messages to Chatty format SQLite DB.

    This function implements a strict ETL pipeline:
    Extract: Reads message records from the Telephony local cache.
    Transform: Translates directions (incoming/outgoing to integers), converts ISO timestamps to UNIX epochs, and generates IDs.
    Load: Safely writes records into a predefined Chatty schema.
    """
    try:
        if os.path.exists(dest_db_path):
            os.remove(dest_db_path)

        conn = sqlite3.connect(dest_db_path)
        c = conn.cursor()

        c.execute('''CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT,
            account TEXT,
            room TEXT,
            who TEXT,
            alias TEXT,
            timestamp INTEGER,
            direction INTEGER,
            message TEXT,
            protocol TEXT,
            status INTEGER
        )''')

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
                body = row_dict.get('body', '')
                timestamp_str = row_dict.get('timestamp', '')

                unix_ts = _dt_to_unix(timestamp_str)
                dir_val = 1 if direction == "incoming" else -1

                c.execute('''INSERT INTO messages
                             (uid, account, room, who, alias, timestamp, direction, message, protocol, status)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (str(row_dict.get('id', '')), "telephony", remote_number, remote_number, remote_number,
                           unix_ts, dir_val, body, "SMS", 1))
                count += 1

        conn.commit()
        conn.close()
        return True, _("Exported {count} messages to Chatty database.").format(count=count)
    except Exception as e:
        logger.error(f"Chatty export error: {e}")
        return False, str(e)


def export_linux_calls(db_manager, dest_db_path):
    """
    Export calls to Gnome Calls format SQLite DB.

    This function implements a strict ETL pipeline:
    Extract: Queries the history table.
    Transform: Computes Gnome Calls 'inbound' boolean representation and parses time variables.
    Load: Populates a newly instantiated records table on the target database.
    """
    try:
        if os.path.exists(dest_db_path):
            os.remove(dest_db_path)

        conn = sqlite3.connect(dest_db_path)
        c = conn.cursor()

        c.execute('''CREATE TABLE records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT,
            inbound INTEGER,
            duration INTEGER,
            time INTEGER
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
                duration = row_dict.get('duration', 0)
                timestamp_str = row_dict.get('timestamp', '')

                unix_ts = _dt_to_unix(timestamp_str)
                inbound = 1 if direction in ("incoming", "missed") else 0

                c.execute("INSERT INTO records (target, inbound, duration, time) VALUES (?, ?, ?, ?)",
                          (number, inbound, duration, unix_ts))
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
