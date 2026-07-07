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

"""CLI commands for Setting."""

import json
from gi.repository import GLib, Gio
from telephony.cli.cli_utils import get_proxy


def cmd_setting_get(args):
    """Execute the setting get command."""
    proxy = get_proxy()
    res = proxy.call_sync("GetSetting", GLib.Variant("(s)", (args.key,)), Gio.DBusCallFlags.NONE, -1, None)
    print(res.unpack()[0])


def cmd_setting_set(args):
    """Execute the setting set command."""
    proxy = get_proxy()
    proxy.call_sync("SetSetting", GLib.Variant("(ss)", (args.key, args.value)), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Setting {args.key} updated.")


def cmd_addressbook_list(args):
    """Execute the addressbook list command."""
    proxy = get_proxy()
    res = proxy.call_sync("GetAddressBooks", None, Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for s in data:
        en = "Yes" if s.get('enabled') else "No"
        print(f"UID: {s.get('uid')} | Name: {s.get('name')} | Type: {s.get('type')} | Enabled: {en}")


def cmd_addressbook_prioritize(args):
    """Execute the addressbook prioritize command."""
    proxy = get_proxy()
    uid_list = json.dumps(args.uids)
    proxy.call_sync("SetAddressBookPriority", GLib.Variant("(s)", (uid_list,)), Gio.DBusCallFlags.NONE, -1, None)
    print("Address book priority updated.")


def setup_parsers(subparsers):
    """Setup argument parsers for setting commands."""
    p_get_set = subparsers.add_parser("setting-get", help="Get a setting value")
    p_get_set.add_argument("key", type=str)
    p_get_set.set_defaults(func=cmd_setting_get)

    p_set_set = subparsers.add_parser("setting-set", help="Set a setting value")
    p_set_set.add_argument("key", type=str)
    p_set_set.add_argument("value", type=str)
    p_set_set.set_defaults(func=cmd_setting_set)

    p_abook_list = subparsers.add_parser("addressbook-list", help="List all address books")
    p_abook_list.set_defaults(func=cmd_addressbook_list)

    p_abook_prio = subparsers.add_parser("addressbook-prioritize", help="Reorder address book priorities")
    p_abook_prio.add_argument("uids", type=str, nargs="+", help="Ordered list of UIDs")
    p_abook_prio.set_defaults(func=cmd_addressbook_prioritize)
