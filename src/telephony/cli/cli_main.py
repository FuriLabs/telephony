#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

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
