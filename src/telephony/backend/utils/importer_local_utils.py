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
import shutil
import sqlite3

from loguru import logger

from .importer_core_utils import _get_chatty_db_path, _get_value, _get_chatty_mms_path, _get_calls_db_path, _parse_generic_timestamp
from .phone_utils import normalize_number

CHATTY_MM_SELF_USER = "invalid-0000000000000000"
CHATTY_SMS_PROTOCOLS = (1, 2)
CHATTY_DIRECTION_SYSTEM = 0
CHATTY_STATUS_DRAFT = 1
CHATTY_THREAD_GROUP = 1


def _copy_attachments(db_manager, source_paths, prefix):
    """Copy attachment files into our own data dir so they survive Chatty removal."""
    copied = []
    att_dir = os.path.join(db_manager.get_data_dir(), "attachments")
    for src in source_paths:
        try:
            if not os.path.isfile(src):
                logger.warning(f"[Importer] Attachment missing, skipping: {src}")
                continue
            os.makedirs(att_dir, exist_ok=True)
            dest = os.path.join(att_dir, f"{prefix}_{os.path.basename(src)}")
            if not os.path.exists(dest):
                shutil.copy2(src, dest)
            copied.append(dest)
        except Exception as e:
            logger.warning(f"[Importer] Failed to copy attachment {src}: {e}")
    return copied


def _read_chatty_history(c, tables, chatty_data_dir):
    """Read messages from a modern Chatty (>= 0.4) history database.

    The number never lives in the messages table: 1:1 threads carry it in
    threads.name and group members hang off thread_members/users.
    """
    members = {}
    if 'thread_members' in tables and 'users' in tables:
        c.execute('''SELECT tm.thread_id, u.username FROM thread_members tm
                     JOIN users u ON u.id = tm.user_id''')
        for row in c.fetchall():
            username = row['username']
            if not username or username == CHATTY_MM_SELF_USER:
                continue
            members.setdefault(row['thread_id'], []).append(username)

    attachments = {}
    if 'message_files' in tables and 'files' in tables:
        c.execute('''SELECT mf.message_id, f.path, mt.name AS mime
                     FROM message_files mf
                     JOIN files f ON f.id = mf.file_id
                     LEFT JOIN mime_type mt ON mt.id = f.mime_type_id''')
        for row in c.fetchall():
            path = row['path']
            if not path or row['mime'] in ("application/smil", "text/plain"):
                continue
            if not os.path.isabs(path):
                path = os.path.join(chatty_data_dir, path)
            attachments.setdefault(row['message_id'], []).append(path)

    protocols = ",".join(str(p) for p in CHATTY_SMS_PROTOCOLS)
    c.execute(f'''SELECT m.id, m.body, m.direction, m.time,
                         t.id AS tid, t.name AS thread_name, t.type AS thread_type,
                         s.username AS sender_number
                  FROM messages m
                  JOIN threads t ON t.id = m.thread_id
                  JOIN accounts a ON a.id = t.account_id
                  LEFT JOIN users s ON s.id = m.sender_id
                  WHERE a.protocol IN ({protocols})
                    AND m.direction != {CHATTY_DIRECTION_SYSTEM}
                    AND IFNULL(m.status, 0) != {CHATTY_STATUS_DRAFT}''')

    messages = []
    for row in c.fetchall():
        try:
            is_group = row['thread_type'] == CHATTY_THREAD_GROUP
            if is_group:
                nums = sorted({normalize_number(n, permissive=True) for n in members.get(row['tid'], [])} - {None, ""})
                number = ",".join(nums) if nums else normalize_number(str(row['thread_name']), permissive=True)
            else:
                number = normalize_number(str(row['thread_name']), permissive=True)
            if not number:
                continue

            dir_str = "incoming" if row['direction'] == 1 else "outgoing"
            if dir_str == "outgoing":
                sender = "Me"
            elif is_group and row['sender_number'] and row['sender_number'] != CHATTY_MM_SELF_USER:
                sender = normalize_number(str(row['sender_number']), permissive=True) or number
            else:
                sender = number

            messages.append({
                'number': number,
                'direction': dir_str,
                'body': row['body'] or "",
                'time_str': _parse_generic_timestamp(row['time']),
                'attachments': attachments.get(row['id'], []),
                'sender': sender,
                'is_group': is_group,
                'prefix': f"chatty_{row['id']}",
            })
        except Exception as e:
            logger.warning(f"[Importer] Error processing Chatty message row: {e}")
    return messages


def _read_chatty_flat(c, tables, mms_dir):
    """Fallback reader for old flat single-table message databases."""
    target_table = None
    for t in ['messages', 'chat', 'chatty_messages', 'sms']:
        if t in tables:
            target_table = t
            break

    if not target_table:
        return None

    c.execute(f"SELECT * FROM {target_table}")
    messages = []
    for row in c.fetchall():
        try:
            row_dict = dict(row)
            phone = _get_value(row_dict, ['phone', 'number', 'address', 'target', 'remote_number', 'contact', 'who'])
            if not phone:
                continue

            norm_number = normalize_number(str(phone), permissive=True)
            if not norm_number:
                continue

            uid = _get_value(row_dict, ['uid', 'id', 'message_id'])
            text = _get_value(row_dict, ['text', 'body', 'message', 'msg', 'content'], "")
            time_val = _get_value(row_dict, ['time', 'date', 'timestamp', 'created', 'created_at'])
            direction = _get_value(row_dict, ['direction', 'type', 'is_from_me', 'inbound'])
            dir_str = "incoming" if direction in (1, "1", True, "incoming") else "outgoing"

            attachments = []
            if uid and os.path.isdir(mms_dir):
                for folder in os.listdir(mms_dir):
                    if folder.endswith(str(uid)):
                        folder_path = os.path.join(mms_dir, folder)
                        if os.path.isdir(folder_path):
                            for file in os.listdir(folder_path):
                                attachments.append(os.path.join(folder_path, file))

            messages.append({
                'number': norm_number,
                'direction': dir_str,
                'body': str(text),
                'time_str': _parse_generic_timestamp(time_val),
                'attachments': attachments,
                'sender': "Me" if dir_str == "outgoing" else norm_number,
                'is_group': False,
                'prefix': f"chatty_{uid}",
            })
        except Exception as e:
            logger.warning(f"[Importer] Error processing Chatty message row: {e}")
    return messages


def import_local_chatty(db_manager, db_path=None, mms_dir=None):
    """Import messages from a Chatty database."""
    db_path = db_path if db_path is not None else _get_chatty_db_path()
    if not os.path.exists(db_path):
        return False, _("Chatty database not found.")

    mms_dir = mms_dir if mms_dir is not None else _get_chatty_mms_path()
    chatty_data_dir = os.path.dirname(os.path.normpath(mms_dir))
    count = 0

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        try:
            c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row['name'] for row in c.fetchall()}

            if 'threads' in tables and 'messages' in tables:
                messages = _read_chatty_history(c, tables, chatty_data_dir)
            else:
                messages = _read_chatty_flat(c, tables, mms_dir)

            if messages is None:
                conn.close()
                return False, _("Could not find messages table in Chatty database.")
        except sqlite3.OperationalError as e:
            logger.error(f"[Importer] Chatty query failed: {e}")
            conn.close()
            return False, _("Failed to read Chatty database structure.")

        conn.close()

        to_insert = []
        with db_manager.lock:
            db_c = db_manager.conn_messages.cursor()
            db_c.execute("SELECT remote_number, body, timestamp, direction FROM messages")
            existing_messages = {(r[0], r[1], r[2], r[3]) for r in db_c.fetchall()}

        for msg in messages:
            sig = (msg['number'], msg['body'], msg['time_str'], msg['direction'])
            if sig in existing_messages:
                continue

            existing_messages.add(sig)

            attachments = _copy_attachments(db_manager, msg['attachments'], msg['prefix'])
            if not msg['body'] and not attachments:
                continue

            att_json = json.dumps(attachments) if attachments else "[]"
            msg_type = 'mms' if attachments or msg['is_group'] else 'sms'
            to_insert.append((msg['number'], msg['direction'], msg['body'], "read",
                              msg['time_str'], msg_type, att_json, msg['sender']))

        if to_insert:
            to_insert.sort(key=lambda x: x[4])

            with db_manager.lock:
                db_c = db_manager.conn_messages.cursor()
                db_c.executemany('''INSERT INTO messages
                                (remote_number, direction, body, status, timestamp, type, attachments, sender)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', to_insert)
                db_manager.conn_messages.commit()
            count = len(to_insert)

        return True, _("Imported {count} messages from Chatty.").format(count=count)
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
                norm_number = normalize_number(str(target))
                if not norm_number or not time_str:
                    logger.warning("[Importer] Skipping local call: missing required details")
                    continue

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

        return True, _("Imported {count} calls.").format(count=count)
    except Exception as e:
        logger.error(f"[Importer] Error importing Calls: {e}")
        return False, str(e)
