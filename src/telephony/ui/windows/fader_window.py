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

from gi.repository import Gtk
from loguru import logger
from ...backend.services.system_state_service import SystemStateService
from ...backend.utils.system_utils import press_power_button


class ProximityFader(Gtk.Window):
    """Handles screen blanking/fading based on proximity sensor state."""

    def __init__(self):
        """Initialize the Proximity Fader."""
        super().__init__()

        self.is_active_internal = False
        self.sys_state = SystemStateService()
        self.is_idle = self.sys_state.is_idle
        self.is_locked = self.sys_state.is_locked
        self._fader_turned_off_screen = False

        self.sys_state.connect("idle-state-changed", self._on_idle_changed)
        self.sys_state.connect("lock-state-changed", self._on_lock_changed)

        self.set_title("Fader")
        self.set_decorated(False)
        self.set_css_classes(["black-fader"])

    def set_active(self, active):
        """Enable or disable the fader, blanking by power key when locked."""
        if active == self.is_active_internal:
            return

        self.is_active_internal = active

        if active:
            if self.is_locked:
                if not self.is_idle:
                    self._fader_turned_off_screen = True
                    self._press_power_key()
            else:
                self._enable_software_fader()
        else:
            self._disable_software_fader()

            if self._fader_turned_off_screen and self.is_idle:
                self._press_power_key()
                self._fader_turned_off_screen = False

    def _press_power_key(self):
        """Simulates Power Button Press via wtype"""
        try:
            press_power_button()
        except Exception as e:
            logger.error(f"[Fader] Power key simulation failed: {e}")

    def _enable_software_fader(self):
        """Show the software black overlay."""
        if not self.get_visible():
            self.present()
            self.fullscreen()

    def _disable_software_fader(self):
        """Hide the software black overlay."""
        if self.get_visible():
            self.hide()

    def _on_idle_changed(self, monitor, is_idle):
        """Track screen idle state and recover a wake-up the fader still owes."""
        self.is_idle = is_idle

        if not self.is_idle:
            self._fader_turned_off_screen = False
            if self.is_active_internal:
                if self.is_locked:
                    self._fader_turned_off_screen = True
                    self._press_power_key()
                else:
                    self._enable_software_fader()
            return

        if not self.is_active_internal and self._fader_turned_off_screen:
            self._press_power_key()
            self._fader_turned_off_screen = False

    def _on_lock_changed(self, monitor, is_locked):
        """Switch between hardware and software blanking when the lock state changes."""
        self.is_locked = is_locked

        if self.is_active_internal:
            if self.is_locked:
                self._disable_software_fader()
                if not self.is_idle:
                    self._fader_turned_off_screen = True
                    self._press_power_key()
            else:
                self._enable_software_fader()
