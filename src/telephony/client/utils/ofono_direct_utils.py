# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

"""One-shot ofonod calls for what must not depend on the daemon.

Deliberate policy exceptions to the daemon-executes-everything rule:
ending a stuck call is safety-critical, so it keeps a stateless
direct path for when the daemon cannot serve. Single calls only,
never subscriptions, so windows stay stateless toward ofonod.
"""

from gi.repository import Gio
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.ofono_utils import get_first_non_hfp_modem

OFONO_BUS = "org.ofono"
DIRECT_CALL_TIMEOUT_MS = 10000


def first_modem_path(bus):
    res = bus.call_sync(
        OFONO_BUS, "/", "org.ofono.Manager", "GetModems",
        None, None, Gio.DBusCallFlags.NONE, DIRECT_CALL_TIMEOUT_MS, None)
    modems = res.unpack()[0]
    modem = get_first_non_hfp_modem(modems)
    return modem[0] if modem else None


def hangup_all_direct():
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        modem_path = first_modem_path(bus)
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
