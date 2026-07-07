# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

"""CLI commands for Blocklist."""

import json
from gi.repository import GLib, Gio
from telephony.cli.cli_utils import get_proxy


def domain_text(entry):
    calls = entry.get("block_calls", True)
    messages = entry.get("block_messages", True)
    if calls and messages:
        return "calls+messages"
    return "calls" if calls else "messages"


def cmd_blocklist_list(args):
    proxy = get_proxy()
    res = proxy.call_sync("GetBlocklist", None, Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for c in data:
        print(f"ID: {c.get("id")} | Number: {c.get("number")} | Blocks: {domain_text(c)} | Note: {c.get("note")}")


def cmd_blocklist_add(args):
    proxy = get_proxy()
    proxy.call_sync("AddBlockedNumber",
                    GLib.Variant("(ssbb)", (args.number, args.note or "",
                                            not args.messages_only, not args.calls_only)),
                    Gio.DBusCallFlags.NONE, -1, None)
    print(f"Added {args.number} to blocklist.")


def cmd_blocklist_export(args):
    proxy = get_proxy()
    res = proxy.call_sync("GetBlocklist", None, Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for entry in data:
        entry.pop("id", None)
    with open(args.file, "w") as handle:
        json.dump(data, handle, indent=2)
    print(f"Exported {len(data)} entries to {args.file}.")


def cmd_blocklist_import(args):
    with open(args.file) as handle:
        payload = handle.read()
    json.loads(payload)
    proxy = get_proxy()
    res = proxy.call_sync("ImportBlocklist", GLib.Variant("(s)", (payload,)),
                          Gio.DBusCallFlags.NONE, -1, None)
    added, updated = res.unpack()
    print(f"Imported: {added} added, {updated} widened.")


def cmd_blocklist_remove(args):
    proxy = get_proxy()
    proxy.call_sync("RemoveBlockedNumber", GLib.Variant("(s)", (args.bid,)), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Removed ID {args.bid} from blocklist.")


def setup_parsers(subparsers):
    p_blist = subparsers.add_parser("blocklist-list", help="List all blocked numbers")
    p_blist.set_defaults(func=cmd_blocklist_list)

    p_badd = subparsers.add_parser("blocklist-add", help="Add a number to the blocklist")
    p_badd.add_argument("number", type=str)
    p_badd.add_argument("--note", type=str, default="")
    group = p_badd.add_mutually_exclusive_group()
    group.add_argument("--calls-only", action="store_true", help="block calls but not messages")
    group.add_argument("--messages-only", action="store_true", help="block messages but not calls")
    p_badd.set_defaults(func=cmd_blocklist_add)

    p_bexp = subparsers.add_parser("blocklist-export", help="Export the blocklist to a JSON file")
    p_bexp.add_argument("file", type=str)
    p_bexp.set_defaults(func=cmd_blocklist_export)

    p_bimp = subparsers.add_parser("blocklist-import", help="Merge a JSON file into the blocklist")
    p_bimp.add_argument("file", type=str)
    p_bimp.set_defaults(func=cmd_blocklist_import)

    p_brem = subparsers.add_parser("blocklist-remove", help="Remove a number from the blocklist by ID")
    p_brem.add_argument("bid", type=str, help="The blocklist ID to remove")
    p_brem.set_defaults(func=cmd_blocklist_remove)
