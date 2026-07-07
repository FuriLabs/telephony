# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Gtk
from telephony.shared.utils.log_utils import logger
from telephony.shared.services.system_state_service import SystemStateService
from telephony.shared.utils.system_utils import press_power_button


class ProximityFader(Gtk.Window):

    def __init__(self):
        super().__init__()

        self.is_active_internal = False
        self.sys_state = SystemStateService()
        self.is_idle = self.sys_state.is_idle
        self.is_locked = self.sys_state.is_locked
        self._fader_turned_off_screen = False

        self.signal_ids = [
            (self.sys_state, self.sys_state.connect("idle-state-changed", self.on_idle_changed)),
            (self.sys_state, self.sys_state.connect("lock-state-changed", self.on_lock_changed)),
        ]

        self.set_title("Fader")
        self.set_decorated(False)
        self.set_css_classes(["black-fader"])

    def release(self):
        for source, handler_id in self.signal_ids:
            if source.handler_is_connected(handler_id):
                source.disconnect(handler_id)
        self.signal_ids.clear()

    def set_active(self, active):
        if active == self.is_active_internal:
            return

        self.is_active_internal = active

        if active:
            if self.is_locked:
                if not self.is_idle:
                    self._fader_turned_off_screen = True
                    self.press_power_key()
            else:
                self.enable_software_fader()
        else:
            self.disable_software_fader()

            if self._fader_turned_off_screen and self.is_idle:
                self.press_power_key()
                self._fader_turned_off_screen = False

    def press_power_key(self):
        try:
            press_power_button()
        except Exception as e:
            logger.error(f"[Fader] Power key simulation failed: {e}")

    def enable_software_fader(self):
        if not self.get_visible():
            self.present()
            self.fullscreen()

    def disable_software_fader(self):
        if self.get_visible():
            self.hide()

    def on_idle_changed(self, monitor, is_idle):
        self.is_idle = is_idle

        if not self.is_idle:
            self._fader_turned_off_screen = False
            if self.is_active_internal:
                if self.is_locked:
                    self._fader_turned_off_screen = True
                    self.press_power_key()
                else:
                    self.enable_software_fader()
            return

        if not self.is_active_internal and self._fader_turned_off_screen:
            self.press_power_key()
            self._fader_turned_off_screen = False

    def on_lock_changed(self, monitor, is_locked):
        self.is_locked = is_locked

        if self.is_active_internal:
            if self.is_locked:
                self.disable_software_fader()
                if not self.is_idle:
                    self._fader_turned_off_screen = True
                    self.press_power_key()
            else:
                self.enable_software_fader()
