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

import subprocess
from loguru import logger
from gi.repository import Gio, GLib


def start_systemd_service(service_name):
    """
    Starts a systemd user service via DBus.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        manager = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            None
        )
        manager.call_sync(
            "StartUnit",
            GLib.Variant("(ss)", (service_name, "replace")),
            Gio.DBusCallFlags.NONE,
            -1,
            None
        )
        logger.info(f"Started user service: {service_name}")
    except Exception as e:
        logger.error(f"Failed to start systemd service {service_name}: {e}")


def stop_systemd_service(service_name):
    """
    Stops a systemd user service via DBus.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        manager = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            None
        )
        manager.call_sync(
            "StopUnit",
            GLib.Variant("(ss)", (service_name, "replace")),
            Gio.DBusCallFlags.NONE,
            -1,
            None
        )
        logger.info(f"Stopped user service: {service_name}")
    except Exception as e:
        logger.error(f"Failed to stop systemd service {service_name}: {e}")


def is_systemd_service_active(service_name):
    """
    Checks if a systemd user service is active via DBus.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        manager = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            None
        )
        result = manager.call_sync(
            "GetUnit",
            GLib.Variant("(s)", (service_name,)),
            Gio.DBusCallFlags.NONE,
            -1,
            None
        )
        unit_path = result.unpack()[0]

        unit_proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.systemd1",
            unit_path,
            "org.freedesktop.DBus.Properties",
            None
        )
        state_variant = unit_proxy.call_sync(
            "Get",
            GLib.Variant("(ss)", ("org.freedesktop.systemd1.Unit", "ActiveState")),
            Gio.DBusCallFlags.NONE,
            -1,
            None
        )
        state = state_variant.unpack()[0].unpack()
        return state == "active"
    except Exception as e:
        logger.debug(f"Failed to check systemd service active state for {service_name}: {e}")
        return False


def get_phosh_emergency_calls():
    source = Gio.SettingsSchemaSource.get_default()
    if not source:
        return []
    try:
        schema = source.lookup("sm.puri.phosh.emergency-calls", True)
        if schema and schema.has_key("enabled"):
            settings = Gio.Settings(schema_id="sm.puri.phosh.emergency-calls")
            enabled = settings.get_boolean("enabled")
            if enabled:
                return ["io.furios.Telephony.emergency"]
            return []
        return []
    except Exception as e:
        logger.warning(f"Could not read phosh emergency calls: {e}")
        return []


def set_phosh_emergency_calls(enabled):
    source = Gio.SettingsSchemaSource.get_default()
    if not source:
        return
    try:
        schema = source.lookup("sm.puri.phosh.emergency-calls", True)
        if schema and schema.has_key("enabled"):
            settings = Gio.Settings(schema_id="sm.puri.phosh.emergency-calls")
            settings.set_boolean("enabled", enabled)
    except Exception as e:
        logger.warning(f"Could not write phosh emergency calls: {e}")


def get_feedbackd_profile():
    try:
        settings = Gio.Settings(schema_id="org.sigxcpu.feedbackd")
        return settings.get_string("profile")
    except Exception as e:
        logger.warning(f"Could not read feedbackd profile: {e}")
        return "full"


def set_feedbackd_profile(profile_name):
    try:
        settings = Gio.Settings(schema_id="org.sigxcpu.feedbackd")
        settings.set_string("profile", profile_name)
    except Exception as e:
        logger.warning(f"Could not write feedbackd profile: {e}")


def is_location_enabled():
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None,
                                       "org.freedesktop.GeoClue2",
                                       "/org/freedesktop/GeoClue2/Manager",
                                       "org.freedesktop.DBus.Properties",
                                       None)
        inUse = proxy.Get("(ss)", "org.freedesktop.GeoClue2.Manager", "InUse")
        return bool(inUse)
    except Exception as e:
        logger.warning(f"Failed to check location status: {e}")
        return False


def enable_location():
    try:
        settings = Gio.Settings(schema_id="org.gnome.system.location")
        settings.set_boolean("enabled", True)
    except Exception as e:
        logger.warning(f"Could not enable location: {e}")


def restart_mtk_modem():
    """Restarts the MTK modem stack by setting the prop via shell."""
    try:
        subprocess.run(["setprop", "vendor.ril.mtk.restart", "1"], check=False)
    except Exception as e:
        logger.error(f"Failed to restart MTK modem: {e}")


def press_power_button():
    """Simulates a Power Button Press via wtype."""
    try:
        subprocess.run(["wtype", "-k", "XF86PowerOff"], check=False)
    except Exception as e:
        logger.error(f"Power key simulation failed: {e}")
