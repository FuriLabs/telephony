#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

"""Fail when an export of our own data does not survive being read back.

Every message and call has to arrive, a real duplicate has to survive,
each picture has to stay with its own message, and reading the same file
twice must add nothing.

Runs from make test, not make check: importing the app needs python3-gi
and python3-phonenumbers, which it depends on but the build does not.
"""

import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from telephony.client.utils.exporter_local_utils import export_linux_telephony
from telephony.daemon.utils.importer_telephony_utils import import_telephony_export

MESSAGES_SCHEMA = """CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
 remote_number TEXT, direction TEXT, body TEXT, status TEXT, timestamp DATETIME,
 type TEXT DEFAULT 'sms', subject TEXT DEFAULT NULL, attachments TEXT DEFAULT '[]',
 sender TEXT);
CREATE TABLE group_names (id TEXT PRIMARY KEY, name TEXT);"""

CALLS_SCHEMA = """CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT,
 name TEXT, direction TEXT, duration INTEGER, timestamp DATETIME, anonymous INTEGER DEFAULT 0,
 multiparty INTEGER DEFAULT 0, transferred INTEGER DEFAULT 0, disconnect_reason TEXT);"""


class ReceivingPhone:
    """The part of a database manager an import uses, without the signals that need a main loop."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.lock = threading.Lock()
        self.eds = None
        self.conn_messages = sqlite3.connect(os.path.join(data_dir, "messages.db"))
        self.conn_messages.executescript(MESSAGES_SCHEMA)
        self.conn_calls = sqlite3.connect(os.path.join(data_dir, "calls.db"))
        self.conn_calls.executescript(CALLS_SCHEMA)

    def get_data_dir(self):
        return self.data_dir

    def close(self):
        self.conn_messages.close()
        self.conn_calls.close()


class SendingPhone:
    def __init__(self, data_dir):
        self.data_dir = data_dir

    def get_data_dir(self):
        return self.data_dir


def write_picture(path, tint):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = b"".join(b"\x00" + bytes(tint) * 8 for _ in range(8))

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return path


def sending_messages(data_dir):
    green = write_picture(os.path.join(data_dir, "one", "IMG_0001.png"), (60, 160, 60))
    red = write_picture(os.path.join(data_dir, "two", "IMG_0001.png"), (200, 60, 60))

    connection = sqlite3.connect(os.path.join(data_dir, "messages.db"))
    connection.executescript(MESSAGES_SCHEMA)
    connection.executemany(
        "INSERT INTO messages (remote_number, direction, body, status, timestamp, type,"
        " subject, attachments, sender) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("+358401112222", "incoming", "plain", "read", "2026-11-01 10:00:00",
             "sms", None, "[]", "+358401112222"),
            ("+358401112222", "outgoing", "answered", "delivered", "2026-11-01 10:01:00",
             "sms", None, "[]", "Me"),
            ("+358409876543", "incoming", "twice over", "read", "2026-11-01 10:02:00",
             "sms", None, "[]", "+358409876543"),
            ("+358409876543", "incoming", "twice over", "read", "2026-11-01 10:02:00",
             "sms", None, "[]", "+358409876543"),
            ("+358401112222", "incoming", "the green one", "read", "2026-11-01 10:03:00",
             "mms", "a subject", json.dumps([green]), "+358401112222"),
            ("+358401112222", "incoming", "the red one", "read", "2026-11-01 10:04:00",
             "mms", None, json.dumps([red]), "+358401112222"),
            ("../../escape", "incoming", "a number that is not one", "read",
             "2026-11-01 10:05:00", "mms", None, json.dumps([green]), "../../escape"),
        ])
    connection.execute("INSERT INTO group_names VALUES ('+358401112222,+358409876543', 'Sauna')")
    connection.commit()
    connection.close()
    return {"green": open(green, "rb").read(), "red": open(red, "rb").read()}


def sending_calls(data_dir):
    connection = sqlite3.connect(os.path.join(data_dir, "calls.db"))
    connection.executescript(CALLS_SCHEMA)
    connection.executemany(
        "INSERT INTO history (number, name, direction, duration, timestamp)"
        " VALUES (?, ?, ?, ?, ?)", [
            ("+358401112222", "Someone", "incoming", 65, "2026-11-01 10:00:00"),
            ("+358401112222", "Someone", "outgoing", 0, "2026-11-01 10:01:00"),
            ("+358409876543", "Unknown", "missed", 0, "2026-11-01 10:02:00"),
            ("+358409876543", "Unknown", "missed", 0, "2026-11-01 10:02:00"),
        ])
    connection.commit()
    connection.close()


def landed(receiving, body):
    row = receiving.conn_messages.execute(
        "SELECT attachments FROM messages WHERE body = ?", (body,)).fetchone()
    if not row:
        return None
    stored = json.loads(row[0] or "[]")
    return stored[0] if stored else None


def main():
    failures = []

    def expect(what, got, want):
        if got != want:
            failures.append(f"{what}: got {got!r}, wanted {want!r}")

    work = tempfile.mkdtemp(prefix="telephony-bundle-")
    try:
        sending_dir = os.path.join(work, "sending")
        os.makedirs(sending_dir)
        pictures = sending_messages(sending_dir)
        sending_calls(sending_dir)

        bundle = os.path.join(work, "messages.zip")
        ok, detail = export_linux_telephony(SendingPhone(sending_dir), bundle, is_messages=True)
        expect("the messages export succeeds", ok, True)

        with zipfile.ZipFile(bundle) as held:
            names = held.namelist()
        expect("the bundle carries the database", "messages.db" in names, True)
        expect("two pictures of the same name stay two pictures",
               len([n for n in names if n.startswith("attachments/")]), 2)

        calls_export = os.path.join(work, "calls.db")
        ok, detail = export_linux_telephony(SendingPhone(sending_dir), calls_export,
                                            is_messages=False)
        expect("the call history export succeeds", ok, True)

        receiving_dir = os.path.join(work, "receiving")
        os.makedirs(receiving_dir)
        receiving = ReceivingPhone(receiving_dir)

        ok, detail = import_telephony_export(receiving, bundle)
        expect("the messages import succeeds", ok, True)
        ok, detail = import_telephony_export(receiving, calls_export)
        expect("the call history import succeeds", ok, True)

        count = receiving.conn_messages.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        expect("every message arrives", count, 7)
        expect("a real duplicate survives", receiving.conn_messages.execute(
            "SELECT COUNT(*) FROM messages WHERE body = 'twice over'").fetchone()[0], 2)
        expect("a delivered message stays delivered", receiving.conn_messages.execute(
            "SELECT COUNT(*) FROM messages WHERE status = 'delivered'").fetchone()[0], 1)
        expect("a subject survives", receiving.conn_messages.execute(
            "SELECT subject FROM messages WHERE body = 'the green one'").fetchone()[0],
            "a subject")
        expect("a group keeps its name", receiving.conn_messages.execute(
            "SELECT name FROM group_names").fetchone()[0], "Sauna")
        expect("every call arrives", receiving.conn_calls.execute(
            "SELECT COUNT(*) FROM history").fetchone()[0], 4)
        expect("a missed call is still missed", receiving.conn_calls.execute(
            "SELECT COUNT(*) FROM history WHERE direction = 'missed'").fetchone()[0], 2)

        green = landed(receiving, "the green one")
        red = landed(receiving, "the red one")
        expect("the green message has a picture", bool(green), True)
        expect("the red message has a picture", bool(red), True)
        if green and red:
            expect("the two messages do not share one file", green != red, True)
            expect("the green message kept the green picture",
                   open(green, "rb").read(), pictures["green"])
            expect("the red message kept the red picture",
                   open(red, "rb").read(), pictures["red"])

        escaped = landed(receiving, "a number that is not one")
        inside = os.path.join(os.path.realpath(receiving_dir), "attachments")
        expect("a number cannot place a file outside the attachments folder",
               bool(escaped) and os.path.realpath(escaped).startswith(inside), True)

        import_telephony_export(receiving, bundle)
        import_telephony_export(receiving, calls_export)
        expect("reading the same messages again adds nothing", receiving.conn_messages.execute(
            "SELECT COUNT(*) FROM messages").fetchone()[0], 7)
        expect("reading the same calls again adds nothing", receiving.conn_calls.execute(
            "SELECT COUNT(*) FROM history").fetchone()[0], 4)

        expect("the live databases are refused as a destination",
               export_linux_telephony(SendingPhone(sending_dir),
                                      os.path.join(sending_dir, "messages.db"))[0], False)
        expect("and the database it would have overwritten is still there",
               os.path.exists(os.path.join(sending_dir, "messages.db")), True)

        awkward = os.path.join(work, "calls #2 (why?).db")
        shutil.copy2(calls_export, awkward)
        ok, detail = import_telephony_export(receiving, awkward)
        expect("a file name with punctuation can be read", ok, True)

        second_dir = os.path.join(work, "other-phone")
        os.makedirs(second_dir)
        other = write_picture(os.path.join(second_dir, "one", "IMG_0001.png"), (20, 20, 220))
        connection = sqlite3.connect(os.path.join(second_dir, "messages.db"))
        connection.executescript(MESSAGES_SCHEMA)
        connection.execute(
            "INSERT INTO messages (remote_number, direction, body, status, timestamp, type,"
            " attachments, sender) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("+358401112222", "incoming", "from the other phone", "read",
             "2026-11-02 10:00:00", "mms", json.dumps([other]), "+358401112222"))
        connection.commit()
        connection.close()

        second_bundle = os.path.join(work, "other.zip")
        export_linux_telephony(SendingPhone(second_dir), second_bundle, is_messages=True)
        import_telephony_export(receiving, second_bundle)

        from_other = landed(receiving, "from the other phone")
        expect("a picture from another phone with the same name is kept",
               bool(from_other) and open(from_other, "rb").read() == open(other, "rb").read(), True)
        expect("and it did not replace the first phone's picture",
               open(landed(receiving, "the green one"), "rb").read(), pictures["green"])

        leftovers = [n for n in os.listdir(tempfile.gettempdir())
                     if n.startswith("telephony-bundle-import")]
        expect("no extracted bundle is left behind", leftovers, [])

        receiving.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        print(f"{len(failures)} bundle failure(s)", file=sys.stderr)
        return 1
    print("telephony bundle clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
