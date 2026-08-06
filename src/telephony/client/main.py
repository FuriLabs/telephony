#!/usr/bin/env python3
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

import os
import sys
import signal

script_path = os.path.realpath(__file__)
package_dir = os.path.dirname(os.path.dirname(script_path))
install_dir = os.path.dirname(package_dir)

sys.path.insert(0, install_dir)

from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.translation_utils import install_i18n
from telephony.shared.constants import APP_ID, INCALL_APP_ID

import argparse
from urllib.parse import unquote

from gi.repository import GLib

MESSAGE_URI_SCHEMES = ("sms:", "smsto:", "mms:", "mmsto:")
CALL_URI_SCHEMES = ("tel:", "callto:")
PROGRAM_MODES = {
    "io.furios.Telephony.Calls": "--calls",
    "io.furios.Telephony.Messages": "--messages",
    "io.furios.Telephony.Contacts": "--contacts",
    "io.furios.Telephony.Incall": "--incall",
}


def build_arg_parser():
    """Build the window command line contract, shared with the running instance."""
    parser = argparse.ArgumentParser(
        prog="io.furios.Telephony",
        description="Telephony windows for calls, messages and contacts.")
    parser.add_argument("--calls", action="store_true", help="open the calls window")
    parser.add_argument("--messages", action="store_true", help="open the messages window")
    parser.add_argument("--contacts", action="store_true", help="open the contacts window")
    parser.add_argument("--full", action="store_true", help="open the full window")
    parser.add_argument("--incall", action="store_true", help="open the in-call window")
    parser.add_argument("--open-chat", metavar="NUMBER", help="open the chat with a number")
    parser.add_argument("--debug", action="store_true", help="log at debug level")
    parser.add_argument("uris", nargs="*", metavar="URI", help="tel: or sms: URIs to open")
    return parser


def messaging_requested(argv):
    """Return True when the arguments name a conversation to open."""
    if any(a.startswith("--open-chat") for a in argv):
        return True
    return any(a.startswith(MESSAGE_URI_SCHEMES) for a in argv)


def call_requested(argv):
    """Return True when the arguments name a number to dial."""
    return any(a.startswith(CALL_URI_SCHEMES) for a in argv)


def main():
    """
    Entry point for every telephony window.

    Each launcher is installed as its own program name pointing at
    this script, so every process is recognizable in ps, cgroups and
    the system monitors; the name implies the mode flag and explicit
    flags keep working on the plain name. The daemon has its own
    entry point in daemon/main.py.
    """
    install_i18n()

    implied_mode = PROGRAM_MODES.get(os.path.basename(sys.argv[0]))
    if implied_mode and implied_mode not in sys.argv:
        sys.argv.append(implied_mode)

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    opts = build_arg_parser().parse_args()

    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if opts.debug else "WARNING")

    if not any((opts.full, opts.calls, opts.messages, opts.contacts, opts.incall)):
        opts.full = True

    if opts.calls:
        application_id = f"{APP_ID}.Calls"
    elif opts.messages:
        application_id = f"{APP_ID}.Messages"
    elif opts.contacts:
        application_id = f"{APP_ID}.Contacts"
    elif opts.incall:
        application_id = INCALL_APP_ID
    elif messaging_requested(sys.argv):
        application_id = f"{APP_ID}.Messages"
    elif call_requested(sys.argv):
        application_id = f"{APP_ID}.Calls"
    else:
        application_id = APP_ID

    open_chat_number = opts.open_chat
    dial_number = None
    for uri in opts.uris:
        if uri.startswith(CALL_URI_SCHEMES):
            dial_number = unquote(uri.split(":", 1)[1])
        elif uri.startswith(MESSAGE_URI_SCHEMES):
            rest = uri.split(":", 1)[1].split("?", 1)[0]
            open_chat_number = unquote(rest)

    from telephony.client.app import App
    app = App(application_id=application_id)
    app.register(None)

    if app.get_is_remote():
        if open_chat_number:
            app.activate_action("open-chat", GLib.Variant("s", open_chat_number))
        elif dial_number:
            app.activate_action("dial-number", GLib.Variant("s", dial_number))
        else:
            app.activate()
        return 0

    if open_chat_number:
        GLib.idle_add(lambda: app.activate_action("open-chat", GLib.Variant("s", open_chat_number)) or False)
    elif dial_number:
        GLib.idle_add(lambda: app.activate_action("dial-number", GLib.Variant("s", dial_number)) or False)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
