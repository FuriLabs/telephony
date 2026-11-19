# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from collections import Counter
from gettext import gettext as _
from urllib.parse import quote

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile

from telephony.shared.utils.log_utils import logger

from telephony.daemon.utils.importer_local_utils import copy_attachments
from telephony.shared.utils.attachment_entry_utils import attachment_file

MESSAGE_COLUMNS = ("remote_number", "direction", "body", "status", "timestamp",
                   "type", "subject", "attachments", "sender")
CALL_COLUMNS = ("number", "name", "direction", "duration", "timestamp",
                "anonymous", "multiparty", "transferred", "disconnect_reason")


def table_names(connection):
    cursor = connection.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cursor.fetchall()}


def present_columns(connection, table, wanted):
    cursor = connection.cursor()
    cursor.execute(f"PRAGMA table_info({table})")
    have = {row[1] for row in cursor.fetchall()}
    return [column for column in wanted if column in have]


def attachment_files(raw, source_dir):
    """Find every attachment of an exported row on this phone. The paths belong to the phone that wrote them, so each is looked for in the bundle too, and anything missing is left out rather than kept as a path to nothing. Only the path is kept, because a chat bubble can open nothing else."""
    try:
        stored = json.loads(raw) if raw else []
    except Exception as e:
        logger.warning(f"[Importer] Unreadable attachment list: {e}")
        return []

    found = []
    for entry in stored:
        path = attachment_file(entry)
        if not path:
            continue
        beside = os.path.join(source_dir, "attachments", os.path.basename(path))
        if os.path.isfile(path):
            found.append(path)
        elif os.path.isfile(beside):
            found.append(beside)
    return found


def file_prefix(number):
    """A name to copy an attachment under. The number comes from the phone that wrote the export, so anything that could step outside the folder is dropped from it."""
    kept = "".join(c if c.isalnum() or c in "+-" else "_" for c in str(number))
    return f"telephony_{kept}"


def import_telephony_messages(db_manager, connection, source_dir):
    """Copy every message of an export across. Numbers are taken exactly as written, because normalising one side of the comparison and not the other makes every row look new. Two messages can match in number, text, time and direction, so what is here is counted and only the surplus is added."""
    columns = present_columns(connection, "messages", MESSAGE_COLUMNS)
    if "remote_number" not in columns or "timestamp" not in columns:
        return False, _("Source database not found.")

    cursor = connection.cursor()
    cursor.execute(f"SELECT {', '.join(columns)} FROM messages ORDER BY timestamp")
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    with db_manager.lock:
        existing = db_manager.conn_messages.cursor()
        existing.execute("SELECT remote_number, body, timestamp, direction FROM messages")
        held = Counter((r[0], r[1], r[2], r[3]) for r in existing.fetchall())

    to_insert = []
    for row in rows:
        number = str(row.get("remote_number") or "").strip()
        timestamp = row.get("timestamp")
        if not number or not timestamp:
            continue

        body = row.get("body") or ""
        direction = row.get("direction") or "incoming"
        signature = (number, body, timestamp, direction)
        if held[signature]:
            held[signature] -= 1
            continue

        found = attachment_files(row.get("attachments"), source_dir)
        attachments = copy_attachments(db_manager, found, file_prefix(number))

        to_insert.append((number, direction, body, row.get("status") or "read",
                          timestamp, row.get("type") or "sms", row.get("subject"),
                          json.dumps(attachments) if attachments else "[]",
                          row.get("sender") or ("Me" if direction == "outgoing" else number)))

    if to_insert:
        with db_manager.lock:
            target = db_manager.conn_messages.cursor()
            target.executemany('''INSERT INTO messages
                               (remote_number, direction, body, status, timestamp,
                                type, subject, attachments, sender)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', to_insert)
            db_manager.conn_messages.commit()

    import_group_names(db_manager, connection)
    return True, _("Imported {count} messages.").format(count=len(to_insert))


def import_group_names(db_manager, connection):
    """Carry over the names given to group conversations. They live beside the messages and are what the user typed, so a thread that was named on one phone keeps its name on the next."""
    if "group_names" not in table_names(connection):
        return

    try:
        cursor = connection.cursor()
        cursor.execute("SELECT id, name FROM group_names")
        names = cursor.fetchall()
        if not names:
            return

        with db_manager.lock:
            target = db_manager.conn_messages.cursor()
            target.executemany("INSERT OR IGNORE INTO group_names (id, name) VALUES (?, ?)",
                               names)
            db_manager.conn_messages.commit()
    except Exception as e:
        logger.warning(f"[Importer] Group names not carried over: {e}")


def import_telephony_calls(db_manager, connection):
    """Copy the call history of an export across, numbers verbatim and directions untouched, so a missed call is still missed on the other phone."""
    columns = present_columns(connection, "history", CALL_COLUMNS)
    if "number" not in columns or "timestamp" not in columns:
        return False, _("Source database not found.")

    cursor = connection.cursor()
    cursor.execute(f"SELECT {', '.join(columns)} FROM history ORDER BY timestamp")
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    with db_manager.lock:
        existing = db_manager.conn_calls.cursor()
        existing.execute("SELECT number, timestamp, duration, direction FROM history")
        held = Counter((r[0], r[1], r[2], r[3]) for r in existing.fetchall())

    to_insert = []
    for row in rows:
        number = str(row.get("number") or "").strip()
        timestamp = row.get("timestamp")
        if not number or not timestamp:
            continue

        duration = row.get("duration") or 0
        direction = row.get("direction") or "incoming"
        signature = (number, timestamp, duration, direction)
        if held[signature]:
            held[signature] -= 1
            continue

        name = row.get("name")
        if not name and db_manager.eds:
            name = db_manager.eds.get_contact_name(number)

        to_insert.append((number, name or "Unknown", direction, duration, timestamp,
                          row.get("anonymous") or 0, row.get("multiparty") or 0,
                          row.get("transferred") or 0, row.get("disconnect_reason")))

    if to_insert:
        with db_manager.lock:
            target = db_manager.conn_calls.cursor()
            target.executemany('''INSERT INTO history
                               (number, name, direction, duration, timestamp,
                                anonymous, multiparty, transferred, disconnect_reason)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', to_insert)
            db_manager.conn_calls.commit()

    return True, _("Imported {count} calls.").format(count=len(to_insert))


def unpack_bundle(file_path):
    """Spread an exported bundle out in a working directory and point at the database inside it. The pictures travel beside the database, so both have to be on disk before a row can be told where its attachment went."""
    work = tempfile.mkdtemp(prefix="telephony-import-")
    with zipfile.ZipFile(file_path) as bundle:
        for member in bundle.namelist():
            if member.startswith("/") or ".." in member:
                logger.warning(f"[Importer] Refusing bundle entry {member}")
                continue
            bundle.extract(member, work)

    for name in sorted(os.listdir(work)):
        if name.endswith(".db"):
            return work, os.path.join(work, name)
    return work, None


def import_telephony_export(db_manager, file_path):
    """Read an export of this app back in. The columns are our own, so a missed call stays missed and a name stays with its number, both of which the importers written for other apps have to guess at and get wrong. Messages arrive as a bundle with their pictures, call history as a database on its own, and an older export of either is still just a database."""
    if not file_path or not os.path.exists(file_path):
        return False, _("File not found.")

    work = None
    connection = None
    try:
        if zipfile.is_zipfile(file_path):
            try:
                work, inner = unpack_bundle(file_path)
            except Exception as e:
                logger.error(f"[Importer] Unreadable export bundle: {e}")
                return False, _("Source database not found.")

            if inner is None:
                return False, _("Source database not found.")
            file_path = inner

        connection = sqlite3.connect(f"file:{quote(file_path)}?mode=ro", uri=True)
        tables = table_names(connection)
        source_dir = os.path.dirname(os.path.abspath(file_path))

        if "messages" in tables:
            return import_telephony_messages(db_manager, connection, source_dir)
        if "history" in tables:
            return import_telephony_calls(db_manager, connection)

        return False, _("Source database not found.")
    except sqlite3.DatabaseError as e:
        logger.error(f"[Importer] Not a Telephony export: {e}")
        return False, _("Source database not found.")
    except Exception as e:
        logger.error(f"[Importer] Telephony import failed: {e}")
        return False, str(e)
    finally:
        if connection is not None:
            connection.close()
        if work:
            shutil.rmtree(work, ignore_errors=True)
