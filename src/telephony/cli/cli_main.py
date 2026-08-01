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
import argparse

script_path = os.path.realpath(__file__)
cli_dir = os.path.dirname(script_path)
telephony_dir = os.path.dirname(cli_dir)
install_dir = os.path.dirname(telephony_dir)

sys.path.insert(0, install_dir)

from telephony.cli.commands import call_command as call
from telephony.cli.commands import message_command as message
from telephony.cli.commands import contact_command as contact
from telephony.cli.commands import setting_command as setting
from telephony.cli.commands import listen_command as listen
from telephony.cli.commands import blocklist_command as blocklist
from telephony.cli.commands import management_command as management


def main():
    parser = argparse.ArgumentParser(description="Telephony CLI interface")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    subparsers.required = True

    call.setup_parsers(subparsers)
    message.setup_parsers(subparsers)
    contact.setup_parsers(subparsers)
    setting.setup_parsers(subparsers)
    listen.setup_parsers(subparsers)
    blocklist.setup_parsers(subparsers)
    management.setup_parsers(subparsers)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
