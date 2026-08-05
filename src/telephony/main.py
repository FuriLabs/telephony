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
telephony_dir = os.path.dirname(script_path)
install_dir = os.path.dirname(telephony_dir)

sys.path.insert(0, install_dir)

__package__ = "telephony"

from gi.repository import Gio, GLib
from telephony.backend.utils.log_utils import logger

from .daemon_app import DaemonApp
from .backend.utils.translation_utils import install_i18n
from .backend.utils.system_utils import start_systemd_service, stop_systemd_service, is_systemd_service_active
from .constants import APP_ID, INCALL_APP_ID, DAEMON_BUS_NAME

MESSAGE_URI_SCHEMES = ("sms:", "smsto:", "mms:", "mmsto:")
CALL_URI_SCHEMES = ("tel:", "callto:")


def messaging_requested(argv):
    """Return True when the arguments name a conversation to open."""
    if any(a.startswith("--open-chat") for a in argv):
        return True
    return any(a.startswith(MESSAGE_URI_SCHEMES) for a in argv)


def call_requested(argv):
    """Return True when the arguments name a number to dial."""
    return any(a.startswith(CALL_URI_SCHEMES) for a in argv)


def is_monitor_running():
    """
    Check if the application monitor service is already running via DBus.
    """
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    res = bus.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameHasOwner",
        GLib.Variant("(s)", (DAEMON_BUS_NAME,)),
        GLib.VariantType("(b)"),
        Gio.DBusCallFlags.NONE,
        -1,
        None
    )
    return res.unpack()[0]


def main():
    """
    Main entry point for the application.
    """
    install_i18n()

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    logger.remove()
    if "--debug" in sys.argv:
        logger.add(sys.stderr, level="DEBUG")

        if is_monitor_running():
            logger.warning("Another instance is running. Stopping it to enable local debug output...")
            try:
                stop_systemd_service("telephony.service")
                for _ in range(10):
                    if not is_monitor_running():
                        logger.info("Service stopped.")
                        break
                    time.sleep(0.5)
            except Exception as e:
                logger.warning(f"Failed to stop service: {e}")
    else:
        logger.add(sys.stderr, level="WARNING")

    is_monitoring = "--start-monitoring" in sys.argv
    is_debug = "--debug" in sys.argv

    has_ui_flags = any(f in sys.argv for f in ["--full", "--calls", "--messages", "--contacts"])

    if not is_monitoring and not is_debug and not has_ui_flags and "--incall" not in sys.argv:
        sys.argv.append("--full")
        has_ui_flags = True

    if has_ui_flags and not is_monitoring and not is_debug:
        if is_monitor_running():
            logger.info("Monitor already running (D-Bus). Skipping systemd check.")
        else:
            try:
                if not is_systemd_service_active("telephony.service"):
                    logger.info("Telephony service not running. Starting it...")
                    start_systemd_service("telephony.service")

                logger.info("Waiting for Telephony service D-Bus monitor to start...")
                for _ in range(20):
                    if is_monitor_running():
                        logger.info("Monitor is now running.")
                        break
                    time.sleep(0.5)
            except Exception as e:
                logger.warning(f"Failed to check/start systemd service: {e}")

    if is_monitoring or is_debug:
        app = DaemonApp()
        return app.run(sys.argv)

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

    from .app import App
    app = App(application_id=application_id)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
