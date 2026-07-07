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

from loguru import logger
import dbus


class OfonoBackend:
    """Backend class to interact with Ofono via DBus for advanced settings."""

    def __init__(self):
        """Initialize the Ofono backend."""
        self.bus = dbus.SystemBus()
        self.modem_path = self._find_modem()

    def _find_modem(self):
        """Find the first available modem."""
        try:
            manager = dbus.Interface(self.bus.get_object('org.ofono', '/'), 'org.ofono.Manager')
            modems = manager.GetModems()
            for path, properties in modems:
                if "org.ofono.ConnectionManager" in properties["Interfaces"]:
                    return path
            if modems:
                return modems[0][0]
        except Exception as e:
            logger.warning(f"[Settings] Find modem warning: {e}")
        return None

    def get_interface(self, iface_name):
        """Get a DBus interface on the current modem."""
        if not self.modem_path:
            return None
        try:
            return dbus.Interface(self.bus.get_object('org.ofono', self.modem_path), iface_name)
        except Exception as e:
            logger.debug(f"[Settings] Get interface {iface_name} failed: {e}")
            return None

    def set_property(self, iface_name, prop, value):
        """Set a property on an interface."""
        iface = self.get_interface(iface_name)
        if iface:
            if isinstance(value, bool):
                val = dbus.Boolean(1 if value else 0)
            else:
                val = value
            iface.SetProperty(prop, val)
            return True
        return False
