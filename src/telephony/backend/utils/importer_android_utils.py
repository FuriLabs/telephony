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

import mimetypes
import base64
import time
from gettext import gettext as _

import json
import os
import xml.etree.ElementTree as ET

from loguru import logger

from .importer_core_utils import _get_xml_value, _parse_generic_timestamp


def import_android_sms(db_manager, file_path):
    """Import SMS/MMS from an Android XML backup."""
    if not os.path.exists(file_path):
        return False, _("File not found.")

    try:
        tree = ET.parse(file_path)
        root = tree.getroot()

        to_insert = []
        with db_manager.lock:
            db_c = db_manager.conn_messages.cursor()
            db_c.execute("SELECT remote_number, body, timestamp, direction FROM messages")
            existing_messages = {(r[0], r[1], r[2], r[3]) for r in db_c.fetchall()}

        count = 0
        for msg in root.findall('sms') + root.findall('mms'):
            try:
                phone = _get_xml_value(msg, ['address', 'number', 'contact', 'target', 'phone'])
                if not phone:
                    continue

                msg_type = _get_xml_value(msg, ['type', 'msg_box', 'direction'])
                dir_str = "incoming" if msg_type in ("1", "incoming", 1) else "outgoing"

                local_att_dir = os.path.expanduser("~/.local/share/telephony/attachments")
                os.makedirs(local_att_dir, exist_ok=True)

                body = _get_xml_value(msg, ['body', 'text', 'content', 'message', 'msg'], '')

                if msg.tag == 'mms':
                    parts = msg.find('parts')
                    if parts is not None:
                        for part in parts.findall('part'):
                            ct = part.get('ct', '')
                            if ct == 'text/plain':
                                body += part.get('text', '')

                ts = _get_xml_value(msg, ['date', 'time', 'timestamp', 'created'])
                time_str = _parse_generic_timestamp(ts)

                sig = (str(phone), str(body), time_str, dir_str)
                if sig in existing_messages:
                    continue

                existing_messages.add(sig)

                attachments_json = "[]"
                db_msg_type = "sms"

                if msg.tag == 'mms':
                    parts = msg.find('parts')
                    if parts is not None:
                        local_atts = []
                        for part in parts.findall('part'):
                            ct = part.get('ct', '')
                            if part.get('data') and ct and ct != 'application/smil' and ct != 'text/plain':
                                try:
                                    b64_data = part.get('data')
                                    decoded = base64.b64decode(b64_data)

                                    ext = mimetypes.guess_extension(ct) or ".bin"
                                    filename = f"android_mms_{int(time.time() * 1000)}_{count}{ext}"
                                    dest_path = os.path.join(local_att_dir, filename)

                                    with open(dest_path, "wb") as f:
                                        f.write(decoded)

                                    local_atts.append({"filepath": dest_path, "mime": ct})
                                except Exception as e:
                                    logger.warning(f"[Importer] Failed to decode MMS part: {e}")

                        if local_atts:
                            attachments_json = json.dumps(local_atts)
                            db_msg_type = "mms"

                sender = "Me" if dir_str == "outgoing" else str(phone)
                if not phone or not dir_str or not time_str:
                    logger.warning("[Importer] Skipping android message: missing required details (timestamp missing)")
                    continue
                to_insert.append((str(phone), dir_str, str(body), "read", time_str, db_msg_type, attachments_json, sender))

                count += 1
            except Exception as e:
                logger.warning(f"[Importer] Error processing Android SMS row: {e}")
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

        return True, _(f"Imported {count} messages from Android backup.")
    except Exception as e:
        logger.error(f"[Importer] Error importing Android SMS: {e}")
        return False, str(e)


def import_android_calls(db_manager, file_path):
    """Import call history from an Android XML backup."""
    if not os.path.exists(file_path):
        return False, _("File not found.")

    try:
        tree = ET.parse(file_path)
        root = tree.getroot()

        to_insert = []
        with db_manager.lock:
            db_c = db_manager.conn_calls.cursor()
            db_c.execute("SELECT number, timestamp, duration, direction FROM history")
            existing_calls = {(r[0], r[1], r[2], r[3]) for r in db_c.fetchall()}

        count = 0
        for call in root.findall('call'):
            try:
                phone = _get_xml_value(call, ['number', 'address', 'phone', 'contact', 'target'])
                if not phone:
                    continue

                call_type = _get_xml_value(call, ['type', 'direction', 'inbound'])
                if call_type == "1":
                    dir_str = "incoming"
                elif call_type == "2":
                    dir_str = "outgoing"
                elif call_type == "3":
                    dir_str = "missed"
                elif call_type == "5":
                    dir_str = "incoming"
                else:
                    dir_str = "incoming"

                duration_str = _get_xml_value(call, ['duration', 'length'], "0")
                duration = int(duration_str) if str(duration_str).isdigit() else 0

                ts = _get_xml_value(call, ['date', 'time', 'timestamp', 'start'])
                time_str = _parse_generic_timestamp(ts)

                norm_number = str(phone)

                sig = (norm_number, time_str, duration, dir_str)
                if sig in existing_calls:
                    continue

                existing_calls.add(sig)

                real_name = "Unknown"
                if db_manager.eds:
                    real_name = db_manager.eds.get_contact_name(norm_number) or call.get('contact_name') or "Unknown"

                to_insert.append((norm_number, real_name, dir_str, duration, time_str))
                count += 1
            except Exception as e:
                logger.warning(f"[Importer] Error processing Android Calls row: {e}")
                continue

        if to_insert:
            to_insert.sort(key=lambda x: x[4])

            with db_manager.lock:
                db_c = db_manager.conn_calls.cursor()
                db_c.executemany("INSERT INTO history (number, name, direction, duration, timestamp) VALUES (?, ?, ?, ?, ?)", to_insert)
                db_manager.conn_calls.commit()
            count = len(to_insert)

        return True, _(f"Imported {count} calls from Android backup.")
    except Exception as e:
        logger.error(f"[Importer] Error importing Android Calls: {e}")
        return False, str(e)
