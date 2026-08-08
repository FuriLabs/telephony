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

"""One-shot ofonod calls for what must not depend on the daemon.

Deliberate policy exceptions to the daemon-executes-everything rule:
ending a stuck call is safety-critical, so it keeps a stateless
direct path for when the daemon cannot serve. Single calls only,
never subscriptions, so windows stay stateless toward ofonod.
"""

from gi.repository import Gio
from telephony.shared.utils.log_utils import logger

OFONO_BUS = "org.ofono"
DIRECT_CALL_TIMEOUT_MS = 10000


def _first_modem_path(bus):
    """Return the first modem's object path, or None; blocking."""
    res = bus.call_sync(
        OFONO_BUS, "/", "org.ofono.Manager", "GetModems",
        None, None, Gio.DBusCallFlags.NONE, DIRECT_CALL_TIMEOUT_MS, None)
    modems = res.unpack()[0]
    return modems[0][0] if modems else None


def hangup_all_direct():
    """Hang up every call straight on ofonod; blocking, call from a worker.

    Returns whether the request reached the modem.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        modem_path = _first_modem_path(bus)
        if not modem_path:
            logger.error("[OfonoDirect] No modem for the direct hangup")
            return False
        bus.call_sync(
            OFONO_BUS, modem_path, "org.ofono.VoiceCallManager", "HangupAll",
            None, None, Gio.DBusCallFlags.NONE, DIRECT_CALL_TIMEOUT_MS, None)
        logger.info("[OfonoDirect] Hung up all calls directly")
        return True
    except Exception as e:
        logger.error(f"[OfonoDirect] Direct hangup failed: {e}")
        return False
