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
import subprocess
import sys
import time

from gi.repository import Gio

BUS_NAME = "io.furios.Telephony"
OBJ_PATH = "/io/furios/Telephony/Daemon"
IFACE_NAME = "io.furios.Telephony.Daemon"


def get_proxy():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    proxy = Gio.DBusProxy.new_sync(
        bus,
        Gio.DBusProxyFlags.DO_NOT_AUTO_START,
        None,
        BUS_NAME,
        OBJ_PATH,
        IFACE_NAME,
        None
    )

    if not proxy.get_name_owner():
        print("Daemon not running. Starting it now...")
        env = os.environ.copy()

        subprocess.Popen(
            [sys.executable, "-m", "telephony.main"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL
        )

        for _ in range(50):
            time.sleep(0.1)
            proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                BUS_NAME,
                OBJ_PATH,
                IFACE_NAME,
                None
            )
            if proxy.get_name_owner():
                break

    return proxy
