# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import os
import json
import base64
import xml.etree.ElementTree as ET
from gettext import gettext as _

from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.datetime_utils import parse_timestamp


def dt_to_android_ms(ts_str):
    return str(int(parse_timestamp(ts_str).timestamp() * 1000))


def export_android_sms(db_manager, file_path):
    try:
        root = ET.Element("smses")
        root.set("count", "0")

        count = 0
        with db_manager.lock:
            c = db_manager.conn_messages.cursor()
            c.execute("SELECT * FROM messages ORDER BY timestamp ASC, id ASC")
            rows = c.fetchall()

            col_names = [description[0] for description in c.description]

            for row in rows:
                try:
                    row_dict = dict(zip(col_names, row))

                    remote_number = row_dict.get("remote_number", "")
                    if not remote_number:
                        continue

                    direction = row_dict.get("direction", "incoming")
                    body = row_dict.get("body", "")
                    timestamp_str = row_dict.get("timestamp", "")
                    msg_type_db = row_dict.get("type", "sms")
                    attachments_json = row_dict.get("attachments", "[]")

                    date_ms = dt_to_android_ms(timestamp_str)
                    type_val = "2" if direction == "outgoing" else "1"

                    try:
                        attachments = json.loads(attachments_json)
                    except Exception as e:
                        logger.warning(f"[Exporter] Bad attachment JSON, skipping attachments: {e}")
                        attachments = []

                    if msg_type_db == "mms" or attachments:
                        mms = ET.SubElement(root, "mms")
                        mms.set("address", remote_number)
                        mms.set("date", date_ms)
                        mms.set("msg_box", type_val)
                        mms.set("read", "1")
                        mms.set("m_type", "132" if direction == "incoming" else "128")
                        mms.set("v", "18")
                        mms.set("text_only", "0")

                        parts = ET.SubElement(mms, "parts")

                        if body:
                            part_txt = ET.SubElement(parts, "part")
                            part_txt.set("seq", "0")
                            part_txt.set("ct", "text/plain")
                            part_txt.set("name", "text")
                            part_txt.set("chset", "106")
                            part_txt.set("text", body)

                        seq = 1 if body else 0
                        for att in attachments:
                            att_path = att if isinstance(att, str) else att.get("filepath", "")
                            if not os.path.exists(att_path):
                                continue

                            mime = att.get("mime", "image/jpeg") if isinstance(att, dict) else "image/jpeg"
                            part_media = ET.SubElement(parts, "part")
                            part_media.set("seq", str(seq))
                            part_media.set("ct", mime)
                            part_media.set("name", os.path.basename(att_path))
                            part_media.set("cl", os.path.basename(att_path))

                            try:
                                with open(att_path, "rb") as f:
                                    b64_data = base64.b64encode(f.read()).decode("utf-8")
                                    part_media.set("data", b64_data)
                            except Exception as e:
                                logger.warning(f"Failed to encode MMS attachment {att_path}: {e}")

                            seq += 1
                    else:
                        sms = ET.SubElement(root, "sms")
                        sms.set("address", remote_number)
                        sms.set("date", date_ms)
                        sms.set("type", type_val)
                        sms.set("body", body or "")
                        sms.set("read", "1")
                        sms.set("status", "-1")

                    count += 1
                except Exception as e:
                    logger.warning(f"[Exporter] Failed to process SMS row: {e}")

        root.set("count", str(count))

        xml_str = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        with open(file_path, "wb") as f:
            f.write(xml_str)

        return True, _("Successfully exported {count} messages to XML.").format(count=count)
    except Exception as e:
        logger.error(f"[Exporter] Export Android SMS error: {e}")
        return False, str(e)


def export_android_calls(db_manager, file_path):
    try:
        root = ET.Element("calls")
        root.set("count", "0")

        count = 0
        with db_manager.lock:
            c = db_manager.conn_calls.cursor()
            c.execute("SELECT * FROM history ORDER BY timestamp ASC, id ASC")
            rows = c.fetchall()

            col_names = [description[0] for description in c.description]

            for row in rows:
                try:
                    row_dict = dict(zip(col_names, row))

                    number = row_dict.get("number", "")
                    if not number:
                        continue

                    name = row_dict.get("name", "Unknown")
                    direction = row_dict.get("direction", "incoming")
                    duration = row_dict.get("duration", 0)
                    timestamp_str = row_dict.get("timestamp", "")

                    date_ms = dt_to_android_ms(timestamp_str)

                    type_val = "1"
                    if direction == "outgoing":
                        type_val = "2"
                    elif direction == "missed":
                        type_val = "3"
                    elif direction == "rejected":
                        type_val = "5"

                    call = ET.SubElement(root, "call")
                    call.set("number", number)
                    call.set("duration", str(duration))
                    call.set("date", date_ms)
                    call.set("type", type_val)
                    if name and name != "Unknown":
                        call.set("contact_name", name)

                    count += 1
                except Exception as e:
                    logger.warning(f"[Exporter] Failed to process Call row: {e}")

        root.set("count", str(count))

        xml_str = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        with open(file_path, "wb") as f:
            f.write(xml_str)

        return True, _("Successfully exported {count} calls to XML.").format(count=count)
    except Exception as e:
        logger.error(f"[Exporter] Export Android Calls error: {e}")
        return False, str(e)
