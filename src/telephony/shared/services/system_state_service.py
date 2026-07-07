# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import os
from gi.repository import Gio, GLib, GObject
from telephony.shared.utils.log_utils import logger


class SystemStateService(GObject.Object):
    """Centralized monitor for system-level states like logind lock status and display idle state to prevent duplicate DBus proxy creation."""

    __gsignals__ = {
        "lock-state-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "idle-state-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,))
    }

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(SystemStateService, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        super().__init__()
        self._initialized = True

        self.sys_bus = None
        self.session_proxy = None

        self.is_locked = False
        self.is_idle = False

        self.init_monitor()

    def init_monitor(self):
        try:
            self.sys_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            mgr = Gio.DBusProxy.new_sync(
                self.sys_bus, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.login1", "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager", None
            )

            current_uid = os.getuid()
            sessions = mgr.call_sync("ListSessions", None, Gio.DBusCallFlags.NONE, -1, None).unpack()[0]

            target_path = None
            for s in sessions:
                if s[1] == current_uid and s[3]:
                    target_path = s[4]
                    break

            if not target_path:
                for s in sessions:
                    if s[1] == current_uid:
                        target_path = s[4]
                        break

            if target_path:
                logger.info(f"[SystemStateService] Tracking Session: {target_path}")
                self.session_proxy = Gio.DBusProxy.new_sync(
                    self.sys_bus, Gio.DBusProxyFlags.NONE, None,
                    "org.freedesktop.login1", target_path,
                    "org.freedesktop.login1.Session", None
                )
                self.session_proxy.connect("g-properties-changed", self.on_prop_changed)

                lock_val = self.session_proxy.get_cached_property("LockedHint")
                if lock_val:
                    self.is_locked = lock_val.get_boolean()

                idle_val = self.session_proxy.get_cached_property("IdleHint")
                if idle_val:
                    self.is_idle = idle_val.get_boolean()
            else:
                logger.warning("[SystemStateService] No valid active session found for logind monitoring.")

        except Exception as e:
            logger.error(f"[SystemStateService] Init error: {e}")

    def on_prop_changed(self, proxy, changed_props, _invalidated_props):
        props = changed_props.unpack()
        if "LockedHint" in props:
            val = props["LockedHint"]
            if self.is_locked != val:
                self.is_locked = val
                GLib.idle_add(self.emit, "lock-state-changed", self.is_locked)
                logger.debug(f"[SystemStateService] Locked state changed: {self.is_locked}")

        if "IdleHint" in props:
            val = props["IdleHint"]
            if self.is_idle != val:
                self.is_idle = val
                GLib.idle_add(self.emit, "idle-state-changed", self.is_idle)
                logger.debug(f"[SystemStateService] Idle state changed: {self.is_idle}")
