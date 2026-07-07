# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import sys

from gi.repository import Gio

from telephony.shared.constants import DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE


def get_proxy():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    proxy = Gio.DBusProxy.new_sync(
        bus,
        Gio.DBusProxyFlags.DO_NOT_AUTO_START,
        None,
        DAEMON_BUS_NAME,
        DAEMON_OBJECT_PATH,
        DAEMON_INTERFACE,
        None
    )

    if not proxy.get_name_owner():
        start_hint = """The telephony daemon is not running.

Start it with:
    systemctl --user start telephony.service

Or, to have it start with your session:
    systemctl --user enable --now telephony.service"""

        print(start_hint, file=sys.stderr)
        raise SystemExit(1)

    return proxy
