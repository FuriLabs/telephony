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


def _systemd_manager(bus_type):
    """Return a proxy to a systemd manager; blocking, call from a worker."""
    bus = Gio.bus_get_sync(bus_type, None)
    return Gio.DBusProxy.new_sync(
        bus, Gio.DBusProxyFlags.NONE, None,
        "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager",
        None
    )


def _systemd_unit_action(method, service_name, bus_type):
    """Ask a systemd manager to act on one unit; blocking, call from a worker."""
    _systemd_manager(bus_type).call_sync(
        method,
        GLib.Variant("(ss)", (service_name, "replace")),
        Gio.DBusCallFlags.NONE,
        -1,
        None
    )


def start_systemd_service(service_name, bus_type=Gio.BusType.SESSION):
    """Start a systemd service; blocking, call from a worker."""
    try:
        _systemd_unit_action("StartUnit", service_name, bus_type)
        logger.info(f"Started service: {service_name}")
    except Exception as e:
        logger.error(f"Failed to start systemd service {service_name}: {e}")


def stop_systemd_service(service_name, bus_type=Gio.BusType.SESSION):
    """Stop a systemd service; blocking, call from a worker."""
    try:
        _systemd_unit_action("StopUnit", service_name, bus_type)
        logger.info(f"Stopped service: {service_name}")
    except Exception as e:
        logger.error(f"Failed to stop systemd service {service_name}: {e}")


def restart_systemd_service(service_name, bus_type=Gio.BusType.SESSION):
    """Restart a systemd service; blocking, call from a worker."""
    try:
        _systemd_unit_action("RestartUnit", service_name, bus_type)
        logger.info(f"Restarted service: {service_name}")
    except Exception as e:
        logger.error(f"Failed to restart systemd service {service_name}: {e}")


def is_systemd_service_active(service_name, bus_type=Gio.BusType.SESSION):
    """Return True while a systemd service is active; blocking, call from a worker."""
    try:
        result = _systemd_manager(bus_type).call_sync(
            "GetUnit",
            GLib.Variant("(s)", (service_name,)),
            Gio.DBusCallFlags.NONE,
            -1,
            None
        )
        unit_path = result.unpack()[0]

        bus = Gio.bus_get_sync(bus_type, None)
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


OFONO_BUS_NAME = "org.ofono"
OFONO_WAIT_TRIES = 20
OFONO_WAIT_STEP_SECONDS = 0.5


def restart_ofono_service():
    """Restart the system ofono service; blocking, call from a worker.

    ofono is a system unit, so this goes to the system manager rather
    than the session one every other service here lives on.
    """
    restart_systemd_service("ofono.service", Gio.BusType.SYSTEM)


def wait_for_ofono():
    """Wait until ofono answers on the system bus; blocking, call from a worker.

    Bounded, and says so when it gives up: whoever waited carries on
    regardless, so the wait is the point and the outcome is only worth
    knowing about in the log.
    """
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    for _ in range(OFONO_WAIT_TRIES):
        try:
            res = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)", (OFONO_BUS_NAME,)),
                GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, -1, None)
            if res.unpack()[0]:
                return
        except Exception as e:
            logger.debug(f"[SystemUtils] ofono name check failed: {e}")
        time.sleep(OFONO_WAIT_STEP_SECONDS)
    logger.warning("[SystemUtils] ofono did not answer within the wait")


def is_ofono_on_bus():
    """Return True when ofono itself answers; blocking, call from a worker.

    Not the same question as whether there is a modem: the unit sits in
    start-pre waiting for a binder that a switched-off radio never
    provides, so a name on the bus is what says ofono got that far.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        res = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", (OFONO_BUS_NAME,)),
            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, -1, None)
        return bool(res.unpack()[0])
    except Exception as e:
        logger.debug(f"[SystemUtils] Could not ask for ofono on the bus: {e}")
        return False


def is_ril_running():
    """Return True when the vendor RIL daemon is running; blocking, from a worker.

    Only an explicit running counts. The property is unset until init
    reaches the service, so an empty read means the question was asked
    too early rather than that the device has no RIL.
    """
    return getprop("init.svc.vendor.ril-daemon-mtk") == "running"


def restart_modemmanager():
    """Restart ModemManager; blocking, call from a worker.

    A system unit like ofono, so it goes to the system manager rather
    than the session one every other service here lives on.
    """
    restart_systemd_service("ModemManager.service", Gio.BusType.SYSTEM)


def getprop(name):
    """Read an Android property, empty when it cannot be read; blocking, call from a worker."""
    try:
        result = subprocess.run(["getprop", name], capture_output=True, text=True)
        return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Could not read the property {name}: {e}")
        return ""


def is_vendor_radio_disabled():
    """Return True when the device ships with its radio turned off; blocking, call from a worker."""
    return getprop("persist.vendor.radio.disabled") == "1"


def is_gsd_airplane_mode():
    """Return True while the desktop reports airplane mode.

    The setting daemon owns the rfkill switches and is the one that
    knows the user asked for them to be off, so the answer comes from
    there rather than from the kernel state it maintains.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            "org.gnome.SettingsDaemon.Rfkill",
            "/org/gnome/SettingsDaemon/Rfkill",
            "org.gnome.SettingsDaemon.Rfkill",
            None
        )
        airplane_mode = proxy.get_cached_property("AirplaneMode")
        return bool(airplane_mode.unpack()) if airplane_mode else False
    except Exception as e:
        logger.debug(f"Could not read the airplane mode state: {e}")
        return False

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


def wtype(*args):
    """Send input to the compositor through wtype; blocking, call from a worker."""
    try:
        subprocess.run(["wtype", *args], check=False)
    except Exception as e:
        logger.error(f"Input simulation failed: {e}")


def press_power_button():
    """Simulates a Power Button Press via wtype."""
    wtype("-k", "XF86PowerOff")




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
