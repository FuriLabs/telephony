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

"""CLI commands for Management."""

from gi.repository import GLib, Gio
from telephony.cli.cli_utils import get_proxy


def cmd_clear_messages(args):
    """Execute the clear messages command."""
    proxy = get_proxy()
    proxy.call_sync("ClearMessages", None, Gio.DBusCallFlags.NONE, -1, None)
    print("Messages cleared.")


def cmd_clear_groups(args):
    """Execute the clear groups command."""
    proxy = get_proxy()
    proxy.call_sync("ClearGroupNames", None, Gio.DBusCallFlags.NONE, -1, None)
    print("Group names cleared.")


def cmd_clear_blocklist(args):
    """Execute the clear blocklist command."""
    proxy = get_proxy()
    proxy.call_sync("ClearBlocklist", None, Gio.DBusCallFlags.NONE, -1, None)
    print("Blocklist cleared.")


def cmd_clear_contacts(args):
    """Execute the clear contacts command."""
    proxy = get_proxy()
    proxy.call_sync("ClearContacts", GLib.Variant("(s)", (args.source_uid or "",)), Gio.DBusCallFlags.NONE, -1, None)
    print("Contacts cleared.")


def cmd_clear_settings(args):
    """Execute the clear settings command."""
    proxy = get_proxy()
    proxy.call_sync("ClearSettings", None, Gio.DBusCallFlags.NONE, -1, None)
    print("Settings cleared.")


def cmd_clear_everything(args):
    """Execute the clear everything command."""
    proxy = get_proxy()
    proxy.call_sync("ClearEverything", GLib.Variant("(s)", (args.source_uid or "",)), Gio.DBusCallFlags.NONE, -1, None)
    print("Everything cleared.")


def cmd_delete_addressbook(args):
    """Execute the delete addressbook command."""
    proxy = get_proxy()
    res = proxy.call_sync("DeleteAddressBook", GLib.Variant("(s)", (args.source_uid,)), Gio.DBusCallFlags.NONE, -1, None)
    success = res.unpack()[0]
    if success:
        print(f"Address book {args.source_uid} deleted.")
    else:
        print(f"Failed to delete address book {args.source_uid}.")


def cmd_import_chatty(args):
    """Execute the import chatty command."""
    proxy = get_proxy()
    res = proxy.call_sync("ImportChatty", GLib.Variant("(ss)", (args.db_path, args.mms_path)), Gio.DBusCallFlags.NONE, -1, None)
    success, msg = res.unpack()
    print(f"Success: {success}, Message: {msg}")


def cmd_import_local_calls(args):
    """Execute the import local calls command."""
    proxy = get_proxy()
    res = proxy.call_sync("ImportLocalCalls", GLib.Variant("(s)", (args.db_path,)), Gio.DBusCallFlags.NONE, -1, None)
    success, msg = res.unpack()
    print(f"Success: {success}, Message: {msg}")


def cmd_import_android_sms(args):
    """Execute the import android sms command."""
    proxy = get_proxy()
    res = proxy.call_sync("ImportAndroidSms", GLib.Variant("(s)", (args.file_path,)), Gio.DBusCallFlags.NONE, -1, None)
    success, msg = res.unpack()
    print(f"Success: {success}, Message: {msg}")


def cmd_import_android_calls(args):
    """Execute the import android calls command."""
    proxy = get_proxy()
    res = proxy.call_sync("ImportAndroidCalls", GLib.Variant("(s)", (args.file_path,)), Gio.DBusCallFlags.NONE, -1, None)
    success, msg = res.unpack()
    print(f"Success: {success}, Message: {msg}")


def cmd_import_ios_sms(args):
    """Execute the import ios sms command."""
    proxy = get_proxy()
    res = proxy.call_sync("ImportIosSms", GLib.Variant("(s)", (args.file_path,)), Gio.DBusCallFlags.NONE, -1, None)
    success, msg = res.unpack()
    print(f"Success: {success}, Message: {msg}")


def cmd_import_ios_calls(args):
    """Execute the import ios calls command."""
    proxy = get_proxy()
    res = proxy.call_sync("ImportIosCalls", GLib.Variant("(s)", (args.file_path,)), Gio.DBusCallFlags.NONE, -1, None)
    success, msg = res.unpack()
    print(f"Success: {success}, Message: {msg}")


def setup_parsers(subparsers):
    """Setup argument parsers for management commands."""
    p_cm = subparsers.add_parser("clear-messages", help="Clear all messages")
    p_cm.set_defaults(func=cmd_clear_messages)

    p_cg = subparsers.add_parser("clear-groups", help="Clear all custom group names")
    p_cg.set_defaults(func=cmd_clear_groups)

    p_cb = subparsers.add_parser("clear-blocklist", help="Clear the blocklist")
    p_cb.set_defaults(func=cmd_clear_blocklist)

    p_cc = subparsers.add_parser("clear-contacts", help="Clear contacts")
    p_cc.add_argument("--source-uid", type=str, default="", help="Specific address book UID")
    p_cc.set_defaults(func=cmd_clear_contacts)

    p_cs = subparsers.add_parser("clear-settings", help="Clear custom settings")
    p_cs.set_defaults(func=cmd_clear_settings)

    p_ce = subparsers.add_parser("clear-everything", help="Clear EVERYTHING (database, settings, contacts)")
    p_ce.add_argument("--source-uid", type=str, default="", help="Specific address book UID for contacts deletion")
    p_ce.set_defaults(func=cmd_clear_everything)

    p_da = subparsers.add_parser("delete-addressbook", help="Delete an address book permanently")
    p_da.add_argument("source_uid", type=str, help="Address book UID to delete")
    p_da.set_defaults(func=cmd_delete_addressbook)

    p_ic = subparsers.add_parser("import-chatty", help="Import Chatty database")
    p_ic.add_argument("db_path", type=str, help="Path to chatty.db")
    p_ic.add_argument("mms_path", type=str, help="Path to mms attachments dir")
    p_ic.set_defaults(func=cmd_import_chatty)

    p_ilc = subparsers.add_parser("import-local-calls", help="Import Calls database")
    p_ilc.add_argument("db_path", type=str, help="Path to records.db")
    p_ilc.set_defaults(func=cmd_import_local_calls)

    p_ias = subparsers.add_parser("import-android-sms", help="Import Android SMS XML")
    p_ias.add_argument("file_path", type=str, help="Path to XML file")
    p_ias.set_defaults(func=cmd_import_android_sms)

    p_iac = subparsers.add_parser("import-android-calls", help="Import Android Calls XML")
    p_iac.add_argument("file_path", type=str, help="Path to XML file")
    p_iac.set_defaults(func=cmd_import_android_calls)

    p_iis = subparsers.add_parser("import-ios-sms", help="Import iOS SMS DB")
    p_iis.add_argument("file_path", type=str, help="Path to sqlite DB")
    p_iis.set_defaults(func=cmd_import_ios_sms)

    p_iic = subparsers.add_parser("import-ios-calls", help="Import iOS Calls DB")
    p_iic.add_argument("file_path", type=str, help="Path to sqlite DB")
    p_iic.set_defaults(func=cmd_import_ios_calls)
