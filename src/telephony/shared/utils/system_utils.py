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

import datetime
import os
import subprocess
import time
from telephony.shared.utils.log_utils import logger
from telephony.shared.constants import DAEMON_BUS_NAME
from gi.repository import Gio, GLib

DAEMON_WAIT_TRIES = 20
DAEMON_WAIT_STEP_SECONDS = 0.5


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
                return ["io.furios.Telephony.Emergency"]
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


def restart_ofono_service():
    """Restart the system ofono service; blocking, call from a worker."""
    try:
        result = subprocess.run(["systemctl", "restart", "ofono.service"],
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.error(f"[SystemUtils] ofono restart failed: {result.stderr.strip()}")
    except Exception as e:
        logger.error(f"[SystemUtils] ofono restart error: {e}")


def setprop(name, value):
    """Write an Android property; blocking, call from a worker."""
    try:
        subprocess.run(["setprop", name, value], check=False)
    except Exception as e:
        logger.error(f"Failed to set the property {name}: {e}")


def restart_ril_modem():
    """Restart the vendor RIL daemon; blocking, call from a worker."""
    setprop("ctl.restart", "vendor.ril-daemon-mtk")


def save_modem_logs():
    """Capture ofono journal and radio logcat to a file; returns the path or None."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = os.path.expanduser("~/Documents")
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"modem-logs-{stamp}.txt")

    sections = (
        ("=== journalctl ofono ===", ["journalctl", "-b", "-u", "ofono", "--no-pager", "-n", "3000"]),
        ("=== journalctl telephony ===", ["journalctl", "--user", "-b", "-u", "telephony.service", "--no-pager", "-n", "1000"]),
        ("=== logcat radio ===", ["logcat", "-b", "radio", "-d", "-t", "3000"]),
    )

    wrote_any = False
    with open(path, "w") as f:
        for header, cmd in sections:
            f.write(header + "\n")
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                f.write(out.stdout or "")
                if out.stderr:
                    f.write(out.stderr)
                wrote_any = wrote_any or bool(out.stdout)
            except Exception as e:
                logger.warning(f"[SystemUtils] Log capture failed for {cmd[0]}: {e}")
                f.write(f"capture failed: {e}\n")
            f.write("\n")

    if not wrote_any:
        logger.error("[SystemUtils] Modem log capture produced no output")
        return None
    return path


def press_power_button():
    """Simulates a Power Button Press via wtype."""
    try:
        subprocess.run(["wtype", "-k", "XF86PowerOff"], check=False)
    except Exception as e:
        logger.error(f"Power key simulation failed: {e}")


def trim_native_heap():
    """Return freed malloc memory to the kernel; safe to call anytime.

    Loading thousands of contacts through JSON and EDS creates a large
    transient allocation burst, and glibc keeps the freed pages parked
    in its arenas forever. Returns False so it can sit directly in a
    one-shot GLib timeout.
    """
    import ctypes
    import gc
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception as e:
        logger.debug(f"[Memory] malloc_trim unavailable: {e}")
    return False


def launch_desktop_uri(desktop_file, uri):
    """Hand a scheme URI to the launcher that owns it.

    Content always opens under the launcher whose desktop id owns that
    kind of content, so the shell shows the right icon for the surface.
    """
    app_info = Gio.DesktopAppInfo.new(desktop_file)
    if not app_info:
        logger.error(f"[Launch] {desktop_file} is missing, cannot open {uri}")
        return False
    try:
        app_info.launch_uris([uri], None)
        return True
    except Exception as e:
        logger.error(f"[Launch] Could not launch {desktop_file}: {e}")
        return False


def is_daemon_bus_running():
    """Return True when the telephony daemon owns its D-Bus name (blocking, call from a worker or at startup)."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    res = bus.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameHasOwner",
        GLib.Variant("(s)", (DAEMON_BUS_NAME,)),
        GLib.VariantType("(b)"),
        Gio.DBusCallFlags.NONE,
        -1,
        None
    )
    return res.unpack()[0]


def ensure_daemon_running():
    """Start the telephony service if absent; blocking, call from a worker."""
    if is_daemon_bus_running():
        logger.info("[Service] Daemon already running (D-Bus). Skipping systemd check.")
        return
    try:
        if not is_systemd_service_active("telephony.service"):
            logger.info("[Service] Telephony service not running. Starting it...")
            start_systemd_service("telephony.service")

        logger.info("[Service] Waiting for the telephony daemon to appear on the bus...")
        for _ in range(DAEMON_WAIT_TRIES):
            if is_daemon_bus_running():
                logger.info("[Service] Daemon is now running.")
                return
            time.sleep(DAEMON_WAIT_STEP_SECONDS)
        logger.warning("[Service] Daemon did not appear; the windows will say so.")
    except Exception as e:
        logger.warning(f"[Service] Failed to check/start systemd service: {e}")
