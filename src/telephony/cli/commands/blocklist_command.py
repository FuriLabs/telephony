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

"""CLI commands for Blocklist."""

import json
from gi.repository import GLib, Gio
from telephony.cli.cli_utils import get_proxy


def cmd_blocklist_list(args):
    """Execute the blocklist list command."""
    proxy = get_proxy()
    res = proxy.call_sync("GetBlocklist", None, Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for c in data:
        print(f"ID: {c.get('id')} | Number: {c.get('number')} | Note: {c.get('note')}")


def cmd_blocklist_add(args):
    """Execute the blocklist add command."""
    proxy = get_proxy()
    proxy.call_sync("AddBlockedNumber", GLib.Variant("(ss)", (args.number, args.note or "")), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Added {args.number} to blocklist.")


def cmd_blocklist_remove(args):
    """Execute the blocklist remove command."""
    proxy = get_proxy()
    proxy.call_sync("RemoveBlockedNumber", GLib.Variant("(s)", (args.bid,)), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Removed ID {args.bid} from blocklist.")


def setup_parsers(subparsers):
    """Setup argument parsers for blocklist commands."""
    p_blist = subparsers.add_parser("blocklist-list", help="List all blocked numbers")
    p_blist.set_defaults(func=cmd_blocklist_list)

    p_badd = subparsers.add_parser("blocklist-add", help="Add a number to the blocklist")
    p_badd.add_argument("number", type=str)
    p_badd.add_argument("--note", type=str, default="")
    p_badd.set_defaults(func=cmd_blocklist_add)

    p_brem = subparsers.add_parser("blocklist-remove", help="Remove a number from the blocklist by ID")
    p_brem.add_argument("bid", type=str, help="The blocklist ID to remove")
    p_brem.set_defaults(func=cmd_blocklist_remove)
