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
