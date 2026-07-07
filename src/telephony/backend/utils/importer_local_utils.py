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

from gettext import gettext as _

import json
import os
import sqlite3

from loguru import logger

from .importer_core_utils import _get_chatty_db_path, _get_value, _get_chatty_mms_path, _get_calls_db_path, _parse_generic_timestamp


def import_local_chatty(db_manager, db_path=None, mms_dir=None):
    """Import messages from a Chatty database."""
    db_path = db_path if db_path is not None else _get_chatty_db_path()
    if not os.path.exists(db_path):
        return False, _("Chatty database not found.")

    mms_dir = mms_dir if mms_dir is not None else _get_chatty_mms_path()
    count = 0

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        try:
            c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row['name'] for row in c.fetchall()]

            target_table = None
            for t in ['messages', 'chat', 'chatty_messages', 'sms']:
                if t in tables:
                    target_table = t
                    break

            if not target_table:
                conn.close()
                return False, _("Could not find messages table in Chatty database.")

            c.execute(f"SELECT * FROM {target_table}")
            rows = c.fetchall()
        except sqlite3.OperationalError as e:
            logger.error(f"[Importer] Chatty query failed: {e}")
            conn.close()
            return False, _("Failed to read Chatty database structure.")

        to_insert = []
        with db_manager.lock:
            db_c = db_manager.conn_messages.cursor()
            db_c.execute("SELECT remote_number, body, timestamp, direction FROM messages")
            existing_messages = {(r[0], r[1], r[2], r[3]) for r in db_c.fetchall()}

        for row in rows:
            try:
                row_dict = dict(row)
                phone = _get_value(row_dict, ['phone', 'number', 'address', 'target', 'remote_number', 'contact'])
                if not phone:
                    continue

                uid = _get_value(row_dict, ['uid', 'id', 'message_id'])
                text = _get_value(row_dict, ['text', 'body', 'message', 'msg', 'content'], "")
                time_val = _get_value(row_dict, ['time', 'date', 'timestamp', 'created', 'created_at'])
                direction = _get_value(row_dict, ['direction', 'type', 'is_from_me', 'inbound'])

                dir_str = "incoming" if direction in (1, "1", True, "incoming") else "outgoing"
                time_str = _parse_generic_timestamp(time_val)

                sig = (str(phone), str(text), time_str, dir_str)
                if sig in existing_messages:
                    continue

                existing_messages.add(sig)

                attachments = []
                if os.path.exists(mms_dir) and uid:
                    for folder in os.listdir(mms_dir):
                        if folder.endswith(str(uid)):
                            folder_path = os.path.join(mms_dir, folder)
                            if os.path.isdir(folder_path):
                                for file in os.listdir(folder_path):
                                    attachments.append(os.path.join(folder_path, file))

                att_json = json.dumps(attachments) if attachments else "[]"
                msg_type = 'mms' if attachments else 'sms'
                sender = "Me" if dir_str == "outgoing" else str(phone)

                if not phone or not dir_str or not time_str:
                    logger.warning("[Importer] Skipping local message: missing required details (timestamp missing)")
                    continue
                to_insert.append((str(phone), dir_str, str(text), "read", time_str, msg_type, att_json, sender))
            except Exception as e:
                logger.warning(f"[Importer] Error processing Chatty message row: {e}")
                continue

        conn.close()

        if to_insert:
            to_insert.sort(key=lambda x: x[4])

            with db_manager.lock:
                db_c = db_manager.conn_messages.cursor()
                db_c.executemany('''INSERT INTO messages
                                (remote_number, direction, body, status, timestamp, type, attachments, sender)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', to_insert)
                db_manager.conn_messages.commit()
            count = len(to_insert)

        db_manager.invalidate_cache("conversations")
        return True, _(f"Imported {count} messages from Chatty.")
    except Exception as e:
        logger.error(f"[Importer] Error importing Chatty: {e}")
        return False, str(e)


def import_local_calls(db_manager, db_path=None):
    """Import call history from a Calls database."""
    db_path = db_path if db_path is not None else _get_calls_db_path()
    if not os.path.exists(db_path):
        return False, _("Calls database not found.")

    count = 0
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        try:
            c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row['name'] for row in c.fetchall()]

            target_table = None
            for t in ['records', 'calls', 'call_history', 'history']:
                if t in tables:
                    target_table = t
                    break

            if not target_table:
                conn.close()
                return False, _("Could not find call history table in Calls database.")

            c.execute(f"SELECT * FROM {target_table}")
            rows = c.fetchall()
        except sqlite3.OperationalError as e:
            logger.error(f"[Importer] Calls query failed: {e}")
            conn.close()
            return False, _("Failed to read Calls database structure.")

        to_insert = []
        with db_manager.lock:
            db_c = db_manager.conn_calls.cursor()
            db_c.execute("SELECT number, timestamp, duration, direction FROM history")
            existing_calls = {(r[0], r[1], r[2], r[3]) for r in db_c.fetchall()}

        for row in rows:
            try:
                row_dict = dict(row)
                target = _get_value(row_dict, ['target', 'number', 'phone', 'address', 'remote_number'])
                if not target:
                    continue

                duration = _get_value(row_dict, ['duration', 'length'], 0)
                inbound = _get_value(row_dict, ['inbound', 'direction', 'type', 'is_incoming'])
                start_time = _get_value(row_dict, ['time', 'start', 'date', 'timestamp', 'created', 'started_at'])

                dir_str = "incoming" if inbound in (1, "1", True, "incoming") else "outgoing"

                if dir_str == "incoming" and duration in (0, "0"):
                    dir_str = "missed"

                time_str = _parse_generic_timestamp(start_time)
                norm_number = str(target)

                sig = (norm_number, time_str, duration, dir_str)
                if sig in existing_calls:
                    continue

                existing_calls.add(sig)

                real_name = "Unknown"
                if db_manager.eds:
                    real_name = db_manager.eds.get_contact_name(norm_number) or "Unknown"

                to_insert.append((norm_number, real_name, dir_str, duration, time_str))
            except Exception as e:
                logger.warning(f"[Importer] Error processing Calls row: {e}")
                continue

        conn.close()

        if to_insert:
            with db_manager.lock:
                db_c = db_manager.conn_calls.cursor()
                db_c.executemany("INSERT INTO history (number, name, direction, duration, timestamp) VALUES (?, ?, ?, ?, ?)", to_insert)
                db_manager.conn_calls.commit()
            count = len(to_insert)

        db_manager.invalidate_cache("history")
        return True, _(f"Imported {count} calls.")
    except Exception as e:
        logger.error(f"[Importer] Error importing Calls: {e}")
        return False, str(e)
