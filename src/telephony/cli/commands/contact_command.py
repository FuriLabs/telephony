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

"""CLI commands for Contact."""

import json
from gi.repository import GLib, Gio
from telephony.cli.cli_utils import get_proxy


def cmd_contacts(args):
    """Execute the contacts command."""
    proxy = get_proxy()
    res = proxy.call_sync("GetContacts", GLib.Variant("(s)", (args.query,)), Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for c in data:
        print(f"Name: {c.get('name')} | Numbers: {', '.join(c.get('numbers', []))}")


def cmd_contact_add(args):
    """Execute the contact add command."""
    proxy = get_proxy()
    proxy.call_sync("AddContact", GLib.Variant("(ss)", (args.name, args.number)), Gio.DBusCallFlags.NONE, -1, None)
    print("Contact added.")


def cmd_contact_mod(args):
    """Execute the contact mod command."""
    proxy = get_proxy()
    proxy.call_sync("ModifyContact", GLib.Variant("(sss)", (args.uid, args.name, args.number)), Gio.DBusCallFlags.NONE, -1, None)
    print("Contact modified.")


def cmd_contact_del(args):
    """Execute the contact del command."""
    proxy = get_proxy()
    proxy.call_sync("DeleteContact", GLib.Variant("(s)", (args.uid,)), Gio.DBusCallFlags.NONE, -1, None)
    print("Contact deleted.")


def cmd_contact_import(args):
    """Execute the contact import command."""
    proxy = get_proxy()
    with open(args.vcf_path, 'r', encoding='utf-8') as f:
        vcard_data = f.read()
    res = proxy.call_sync("ImportContacts", GLib.Variant("(ss)", (vcard_data, args.source_uid or "")), Gio.DBusCallFlags.NONE, -1, None)
    count = res.unpack()[0]
    print(f"Imported {count} contacts.")


def cmd_contact_export(args):
    """Execute the contact export command."""
    proxy = get_proxy()
    res = proxy.call_sync("ExportContacts", GLib.Variant("(s)", (args.source_uid or "",)), Gio.DBusCallFlags.NONE, -1, None)
    vcard_data = res.unpack()[0]
    with open(args.dest_path, 'w', encoding='utf-8') as f:
        f.write(vcard_data)
    print(f"Exported contacts to {args.dest_path}.")


def setup_parsers(subparsers):
    """Setup argument parsers for contact commands."""
    p_contacts = subparsers.add_parser("contacts", help="Search contacts")
    p_contacts.add_argument("query", type=str, nargs="?", default="", help="Search query")
    p_contacts.set_defaults(func=cmd_contacts)

    p_cadd = subparsers.add_parser("contact-add", help="Add a contact")
    p_cadd.add_argument("name", type=str)
    p_cadd.add_argument("number", type=str)
    p_cadd.set_defaults(func=cmd_contact_add)

    p_cmod = subparsers.add_parser("contact-mod", help="Modify a contact")
    p_cmod.add_argument("uid", type=str)
    p_cmod.add_argument("name", type=str)
    p_cmod.add_argument("number", type=str)
    p_cmod.set_defaults(func=cmd_contact_mod)

    p_cdel = subparsers.add_parser("contact-del", help="Delete a contact")
    p_cdel.add_argument("uid", type=str)
    p_cdel.set_defaults(func=cmd_contact_del)

    p_cimp = subparsers.add_parser("contact-import", help="Import contacts from a vCard file")
    p_cimp.add_argument("vcf_path", type=str)
    p_cimp.add_argument("--source-uid", type=str, default="", help="Specific address book UID")
    p_cimp.set_defaults(func=cmd_contact_import)

    p_cexp = subparsers.add_parser("contact-export", help="Export contacts to a vCard file")
    p_cexp.add_argument("dest_path", type=str)
    p_cexp.add_argument("--source-uid", type=str, default="", help="Specific address book UID")
    p_cexp.set_defaults(func=cmd_contact_export)
