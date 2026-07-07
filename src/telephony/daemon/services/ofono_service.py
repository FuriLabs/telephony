# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Gio, GLib, GObject
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.ofono_utils import get_first_non_hfp_modem, is_hfp_modem


class OfonoService(GObject.Object):
    """
    Monitors the status of the ofono service and modem availability.
    """
    __gsignals__ = {
        "status-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "modem-ready": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__()
        self.connected = False
        self.modem_path = None
        self.manager_proxy = None
        self.manager_serial = 0

        bus_type = Gio.BusType.SYSTEM
        self.bus = Gio.bus_get_sync(bus_type, None)

        self.watcher_id = Gio.bus_watch_name(
            bus_type,
            "org.ofono",
            Gio.BusNameWatcherFlags.NONE,
            self.on_name_appeared,
            self.on_name_vanished
        )

    def on_name_appeared(self, connection, name, _name_owner):
        logger.info(f"[Monitor] Service {name} appeared.")
        self.init_manager()

    def on_name_vanished(self, connection, name):
        logger.warning(f"[Monitor] Service {name} vanished.")
        self.handle_disconnection("Ofono service stopped")

    def init_manager(self):
        self.manager_serial += 1
        Gio.DBusProxy.new(
            self.bus, Gio.DBusProxyFlags.NONE, None,
            "org.ofono", "/", "org.ofono.Manager", None,
            self.on_manager_proxy_ready, self.manager_serial)

    def on_manager_proxy_ready(self, _source, result, serial):
        try:
            proxy = Gio.DBusProxy.new_finish(result)
        except GLib.Error as e:
            logger.error(f"[Monitor] Could not get Manager proxy: {e}")
            self.emit("status-changed", "error", "Could not get Manager proxy")
            return

        if serial != self.manager_serial:
            proxy.run_dispose()
            return

        self.manager_proxy = proxy
        self.manager_proxy.connect("g-signal", self.on_manager_signal)
        self.scan_modems()

    def on_manager_signal(self, proxy, sender, signal, params):
        if signal == "ModemAdded":
            path, props = params.unpack()
            logger.info(f"[Monitor] Signal: ModemAdded {path}")
            self.handle_new_modem(path, props)
        elif signal == "ModemRemoved":
            path = params.unpack()[0]
            logger.info(f"[Monitor] Signal: ModemRemoved {path}")
            if self.modem_path == path:
                self.handle_disconnection("Modem removed")

    def scan_modems(self):
        self.manager_proxy.call(
            "GetModems", None, Gio.DBusCallFlags.NONE, -1, None,
            self.on_scan_modems_done, None)

    def on_scan_modems_done(self, proxy, result, _user_data):
        try:
            modems = proxy.call_finish(result).unpack()[0]
        except GLib.Error as e:
            logger.warning(f"[Monitor] Could not list modems: {e}")
            self.emit("status-changed", "error", "Could not list modems")
            return

        if proxy is not self.manager_proxy:
            return

        modem = get_first_non_hfp_modem(modems)
        if modem:
            path, props = modem
            self.handle_new_modem(path, props)
        else:
            self.emit("status-changed", "searching", "Waiting for modem...")

    def handle_new_modem(self, path, props=None):
        if is_hfp_modem(path, props):
            logger.debug(f"[Monitor] Ignoring HFP modem: {path}")
            return

        if self.modem_path != path:
            self.modem_path = path
            self.connected = True
            logger.info(f"[Monitor] Modem Ready: {path}")
            self.emit("modem-ready", path)
            self.emit("status-changed", "connected", "Ready")

    def handle_disconnection(self, reason):
        if self.connected or self.modem_path:
            logger.warning(f"[Monitor] Disconnected: {reason}")
            self.connected = False
            self.modem_path = None
            self.manager_proxy = None
            self.manager_serial += 1
            self.emit("status-changed", "offline", reason)

    def stop(self):
        if self.watcher_id:
            Gio.bus_unwatch_name(self.watcher_id)
