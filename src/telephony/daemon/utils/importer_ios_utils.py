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
from datetime import datetime

from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.datetime_utils import format_timestamp

from telephony.daemon.utils.importer_core_utils import _get_value
from telephony.shared.utils.phone_utils import normalize_number

MAC_ABSOLUTE_EPOCH = 978307200
NANOSECOND_DIGIT_THRESHOLD = 10
NANOSECONDS_PER_SECOND = 1000000000


def import_ios_sms(db_manager, file_path, manifest_path=None, backup_dir=None):
    """Import SMS/iMessage from an iOS sms.db backup."""
    if not os.path.exists(file_path):
        return False, _("File not found.")

    count = 0
    try:
        conn = sqlite3.connect(file_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        try:
            c.execute("""
                SELECT m.ROWID, m.*, h.id as handle_id_phone
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
            """)
            rows = c.fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"[Importer] Standard iOS SMS JOIN failed: {e}. Attempting dynamic fallback.")
            try:
                c.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row['name'] for row in c.fetchall()]

                target_table = None
                for t in ['message', 'messages', 'chat', 'sms']:
                    if t in tables:
                        target_table = t
                        break

                if not target_table:
                    conn.close()
                    return False, _("Could not find messages table in iOS database.")

                c.execute(f"SELECT ROWID, * FROM {target_table}")
                rows = c.fetchall()
            except sqlite3.OperationalError as dyn_e:
                logger.error(f"[Importer] Dynamic iOS SMS fallback failed: {dyn_e}")
                conn.close()
                return False, _("Failed to read iOS SMS database structure.")

        msg_attachments = {}
        try:
            c.execute("""
                SELECT maj.message_id, a.filename, a.mime_type
                FROM message_attachment_join maj
                JOIN attachment a ON maj.attachment_id = a.ROWID
            """)
            for a_row in c.fetchall():
                m_id = a_row["message_id"]
                if m_id not in msg_attachments:
                    msg_attachments[m_id] = []
                msg_attachments[m_id].append({
                    "filename": a_row["filename"],
                    "mime_type": a_row["mime_type"]
                })
        except sqlite3.OperationalError as e:
            logger.warning(f"[Importer] iOS SMS attachments query failed: {e}")

        file_hash_map = {}
        if manifest_path and os.path.exists(manifest_path):
            try:
                m_conn = sqlite3.connect(manifest_path)
                m_c = m_conn.cursor()
                m_c.execute("SELECT fileID, relativePath FROM Files WHERE domain='MediaDomain'")
                for m_row in m_c.fetchall():
                    file_hash_map[m_row[1]] = m_row[0]
                m_conn.close()
            except Exception as e:
                logger.warning(f"[Importer] Failed to read Manifest.db: {e}")

        local_att_dir = os.path.join(db_manager.get_data_dir(), "attachments")
        os.makedirs(local_att_dir, exist_ok=True)

        to_insert = []
        with db_manager.lock:
            db_c = db_manager.conn_messages.cursor()
            db_c.execute("SELECT remote_number, body, timestamp, direction FROM messages")
            existing_messages = {(r[0], r[1], r[2], r[3]) for r in db_c.fetchall()}

        for row in rows:
            try:
                row_dict = dict(row)
                m_id = row_dict["ROWID"]
                phone = _get_value(row_dict, ['handle_id_phone', 'id', 'account', 'sender'])
                if not phone:
                    continue

                norm_number = normalize_number(str(phone), permissive=True)
                text = _get_value(row_dict, ['text', 'body', 'message'], "")
                if text is None:
                    text = ""
                is_from_me = _get_value(row_dict, ['is_from_me', 'type', 'direction'], 0)
                date = _get_value(row_dict, ['date', 'time', 'timestamp'], 0)

                dir_str = "outgoing" if is_from_me in (1, "1", True) else "incoming"

                try:
                    date_float = float(date)
                    if len(str(int(date_float))) > NANOSECOND_DIGIT_THRESHOLD:
                        date_float = date_float / NANOSECONDS_PER_SECOND
                    unix_ts = date_float + MAC_ABSOLUTE_EPOCH
                    time_str = format_timestamp(datetime.fromtimestamp(unix_ts))
                except Exception as e:
                    logger.warning(f"[Importer] Error parsing iOS message date {date}: {e}")
                    time_str = format_timestamp()

                if not norm_number or not dir_str or not time_str:
                    logger.warning("[Importer] Skipping iOS message: missing required details")
                    continue

                sig = (norm_number, str(text), time_str, dir_str)
                if sig in existing_messages:
                    continue

                existing_messages.add(sig)

                sender = "Me" if dir_str == "outgoing" else norm_number

                attachments_json = "[]"
                msg_type = "sms"
                if m_id in msg_attachments:
                    local_atts = []
                    for att in msg_attachments[m_id]:
                        orig_path = att["filename"]
                        if orig_path:
                            rel_path = orig_path.replace("~/", "")

                            file_hash = file_hash_map.get(rel_path)
                            if file_hash and backup_dir:
                                backup_file_path = os.path.join(backup_dir, file_hash[:2], file_hash)
                                if not os.path.exists(backup_file_path):
                                    backup_file_path = os.path.join(backup_dir, file_hash)

                                if os.path.exists(backup_file_path):
                                    new_filename = f"{file_hash}_{os.path.basename(orig_path)}"
                                    dest_path = os.path.join(local_att_dir, new_filename)
                                    try:
                                        shutil.copy2(backup_file_path, dest_path)
                                        local_atts.append(dest_path)
                                        msg_type = "mms"
                                    except Exception as e:
                                        logger.warning(f"[Importer] Failed to copy attachment {rel_path}: {e}")

                    if local_atts:
                        attachments_json = json.dumps(local_atts)

                to_insert.append((norm_number, dir_str, str(text), "read", time_str, msg_type, attachments_json, sender))

            except Exception as e:
                logger.warning(f"[Importer] Error processing iOS SMS row: {e}")
                continue

        if to_insert:
            to_insert.sort(key=lambda x: x[4])

            with db_manager.lock:
                db_c = db_manager.conn_messages.cursor()
                db_c.executemany('''INSERT INTO messages
                                (remote_number, direction, body, status, timestamp, type, attachments, sender)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', to_insert)
                db_manager.conn_messages.commit()
            count = len(to_insert)

        conn.close()
        return True, _("Imported {count} messages from iOS backup.").format(count=count)
    except Exception as e:
        logger.error(f"[Importer] Error importing iOS SMS: {e}")
        return False, str(e)


def import_ios_calls(db_manager, file_path):
    """Import call history from an iOS CallHistory.storedata backup."""
    if not os.path.exists(file_path):
        return False, _("File not found.")

    count = 0
    try:
        conn = sqlite3.connect(file_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        try:
            c.execute("SELECT * FROM ZCALLRECORD")
            rows = c.fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"[Importer] Standard iOS Calls query failed: {e}. Attempting dynamic fallback.")
            try:
                c.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row['name'] for row in c.fetchall()]

                target_table = None
                for t in ['ZCALLRECORD', 'call', 'calls', 'call_history', 'records']:
                    if t in tables:
                        target_table = t
                        break

                if not target_table:
                    conn.close()
                    return False, _("Could not find call history table in iOS database.")

                c.execute(f"SELECT * FROM {target_table}")
                rows = c.fetchall()
            except sqlite3.OperationalError as dyn_e:
                logger.error(f"[Importer] Dynamic iOS Calls fallback failed: {dyn_e}")
                conn.close()
                return False, _("Failed to read iOS Calls database structure.")

        to_insert = []
        with db_manager.lock:
            db_c = db_manager.conn_calls.cursor()
            db_c.execute("SELECT number, timestamp, duration, direction FROM history")
            existing_calls = {(r[0], r[1], r[2], r[3]) for r in db_c.fetchall()}

        for row in rows:
            try:
                row_dict = dict(row)
                phone = _get_value(row_dict, ['ZADDRESS', 'address', 'target', 'number', 'phone'])
                if not phone:
                    continue

                originated = _get_value(row_dict, ['ZORIGINATED', 'originated', 'direction'], 0)
                answered = _get_value(row_dict, ['ZANSWERED', 'answered', 'status'], 1)
                duration = _get_value(row_dict, ['ZDURATION', 'duration', 'length'], 0)
                date = _get_value(row_dict, ['ZDATE', 'date', 'time', 'timestamp'], 0)

                if originated in (1, "1", True):
                    dir_str = "outgoing"
                elif answered in (0, "0", False):
                    dir_str = "missed"
                else:
                    dir_str = "incoming"

                try:
                    unix_ts = float(date) + MAC_ABSOLUTE_EPOCH
                    time_str = format_timestamp(datetime.fromtimestamp(unix_ts))
                except Exception as e:
                    logger.warning(f"[Importer] Error parsing iOS call date {date}: {e}")
                    time_str = format_timestamp()

                norm_number = normalize_number(str(phone))

                sig = (norm_number, time_str, duration, dir_str)
                if sig in existing_calls:
                    continue

                existing_calls.add(sig)

                real_name = "Unknown"
                if db_manager.eds:
                    real_name = db_manager.eds.get_contact_name(norm_number) or "Unknown"

                if not norm_number or not dir_str or not time_str:
                    logger.warning("[Importer] Skipping iOS call: missing required details (timestamp missing)")
                    continue
                to_insert.append((norm_number, real_name, dir_str, duration, time_str))

            except Exception as e:
                logger.warning(f"[Importer] Error processing iOS Calls row: {e}")
                continue

        if to_insert:
            to_insert.sort(key=lambda x: x[4])

            with db_manager.lock:
                db_c = db_manager.conn_calls.cursor()
                db_c.executemany("INSERT INTO history (number, name, direction, duration, timestamp) VALUES (?, ?, ?, ?, ?)", to_insert)
                db_manager.conn_calls.commit()
            count = len(to_insert)

        conn.close()
        return True, _("Imported {count} calls from iOS backup.").format(count=count)
    except Exception as e:
        logger.error(f"[Importer] Error importing iOS Calls: {e}")
        return False, str(e)
