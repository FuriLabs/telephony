#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import argparse
import os
import sys
import signal
import time

script_path = os.path.realpath(__file__)
package_dir = os.path.dirname(os.path.dirname(script_path))
install_dir = os.path.dirname(package_dir)

sys.path.insert(0, install_dir)

from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.translation_utils import install_i18n
from telephony.shared.utils.system_utils import stop_systemd_service, is_daemon_bus_running


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="telephony-server",
        description="The telephony daemon: owns the modem, audio and databases.")
    parser.add_argument("--debug", action="store_true",
                        help="log at debug level, replacing a running service")
    return parser


def main():
    install_i18n()

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    is_debug = build_arg_parser().parse_args().debug

    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if is_debug else "WARNING")

    if is_debug and is_daemon_bus_running():
        logger.warning("Another daemon is running. Stopping it to enable local debug output...")
        try:
            stop_systemd_service("telephony.service")
            for _ in range(10):
                if not is_daemon_bus_running():
                    logger.info("Service stopped.")
                    break
                time.sleep(0.5)
        except Exception as e:
            logger.warning(f"Failed to stop service: {e}")

    from telephony.daemon.daemon_app import DaemonApp
    app = DaemonApp()
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
