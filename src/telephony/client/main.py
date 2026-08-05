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
import time

script_path = os.path.realpath(__file__)
package_dir = os.path.dirname(os.path.dirname(script_path))
install_dir = os.path.dirname(package_dir)

sys.path.insert(0, install_dir)

__package__ = "telephony.client"

from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.translation_utils import install_i18n
from telephony.shared.utils.system_utils import (start_systemd_service, is_systemd_service_active,
                                                 is_daemon_bus_running)
from telephony.shared.constants import APP_ID, INCALL_APP_ID

MESSAGE_URI_SCHEMES = ("sms:", "smsto:", "mms:", "mmsto:")
CALL_URI_SCHEMES = ("tel:", "callto:")
PROGRAM_MODES = {
    "io.furios.Telephony.Calls": "--calls",
    "io.furios.Telephony.Messages": "--messages",
    "io.furios.Telephony.Contacts": "--contacts",
    "io.furios.Telephony.Incall": "--incall",
}


def messaging_requested(argv):
    """Return True when the arguments name a conversation to open."""
    if any(a.startswith("--open-chat") for a in argv):
        return True
    return any(a.startswith(MESSAGE_URI_SCHEMES) for a in argv)


def call_requested(argv):
    """Return True when the arguments name a number to dial."""
    return any(a.startswith(CALL_URI_SCHEMES) for a in argv)


def ensure_daemon_running():
    """Start the telephony service if it is not on the bus yet."""
    if is_daemon_bus_running():
        logger.info("Daemon already running (D-Bus). Skipping systemd check.")
        return
    try:
        if not is_systemd_service_active("telephony.service"):
            logger.info("Telephony service not running. Starting it...")
            start_systemd_service("telephony.service")

        logger.info("Waiting for the telephony daemon to appear on the bus...")
        for _ in range(20):
            if is_daemon_bus_running():
                logger.info("Daemon is now running.")
                break
            time.sleep(0.5)
    except Exception as e:
        logger.warning(f"Failed to check/start systemd service: {e}")


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

    is_debug = "--debug" in sys.argv

    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if is_debug else "WARNING")

    has_ui_flags = any(f in sys.argv for f in ["--full", "--calls", "--messages", "--contacts"])

    if not has_ui_flags and "--incall" not in sys.argv:
        sys.argv.append("--full")

    ensure_daemon_running()

    if "--calls" in sys.argv:
        application_id = f"{APP_ID}.Calls"
    elif "--messages" in sys.argv:
        application_id = f"{APP_ID}.Messages"
    elif "--contacts" in sys.argv:
        application_id = f"{APP_ID}.Contacts"
    elif "--incall" in sys.argv:
        application_id = INCALL_APP_ID
    elif messaging_requested(sys.argv):
        application_id = f"{APP_ID}.Messages"
    elif call_requested(sys.argv):
        application_id = f"{APP_ID}.Calls"
    else:
        application_id = APP_ID

    from telephony.client.app import App
    app = App(application_id=application_id)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
