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

import os
from dataclasses import dataclass

from gi.repository import Gio, GLib

from telephony.shared.utils.log_utils import logger

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
CAMERA_IFACE = "org.freedesktop.portal.Camera"
REQUEST_IFACE = "org.freedesktop.portal.Request"
PORTAL_CALL_TIMEOUT_MS = 5000
ACCESS_RESPONSE_TIMEOUT_MS = 15000
DEVICE_POLL_INTERVAL_MS = 150
DEVICE_POLL_TIMEOUT_MS = 6000
BACK_CAMERA = 0
FRONT_CAMERA = 1

_token_serial = 0


@dataclass
class CameraDevice:
    """One camera as the portal's PipeWire remote advertises it."""

    serial: int
    node_name: str
    location: str
    rotation: int
    description: str


class CameraPortal:
    """Camera access through the desktop Camera portal.

    The portal grants access and hands over PipeWire remotes as file
    descriptors; the cameras live as nodes on those private
    connections, invisible on the default socket.
    """

    def __init__(self):
        self._bus = None
        self._fd = -1
        self._spare_fd = -1
        self._refilling = False
        self._provider = None
        self._devices = []
        self._state = "idle"
        self._callbacks = []
        self._signal_id = 0
        self._poll_source = None
        self._poll_deadline = 0.0
        self._response_timeout_source = None

    def open(self, callback):
        """Acquire the portal remote, then call back with the devices.

        The callback receives the device list on success or None on
        failure, always on the main loop. Repeat calls while opening
        share the one handshake; calls after it just answer.
        """
        if self._state == "ready":
            callback(self._devices)
            return
        self._callbacks.append(callback)
        if self._state == "opening":
            return
        self._state = "opening"
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:
            logger.error(f"[CameraPortal] Session bus unavailable: {e}")
            self.finish(None)
            return
        self.request_access()

    def request_access(self):
        """Ask the Camera portal for access, watching for its answer.

        A portal request answers through a Response signal on a request
        object whose path is derivable up front; the subscription must
        exist before the call, or a fast portal answers into nobody.
        """
        global _token_serial
        _token_serial += 1
        token = f"telephony_camera_{os.getpid()}_{_token_serial}"
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        self._signal_id = self._bus.signal_subscribe(
            PORTAL_BUS, REQUEST_IFACE, "Response", request_path, None,
            Gio.DBusSignalFlags.NO_MATCH_RULE, self.on_access_response)
        self._response_timeout_source = GLib.timeout_add(
            ACCESS_RESPONSE_TIMEOUT_MS, self.on_access_timeout)
        self._bus.call(
            PORTAL_BUS, PORTAL_PATH, CAMERA_IFACE, "AccessCamera",
            GLib.Variant("(a{sv})", ({"handle_token": GLib.Variant("s", token)},)),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE,
            PORTAL_CALL_TIMEOUT_MS, None, self.on_access_called)

    def on_access_called(self, bus, result):
        """Fail fast when the access call itself is rejected."""
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            logger.error(f"[CameraPortal] AccessCamera failed: {e}")
            self.finish(None)

    def on_access_timeout(self):
        """Give up on a portal that never answers the access request."""
        self._response_timeout_source = None
        if self._state == "opening":
            logger.error("[CameraPortal] No response to AccessCamera")
            self.finish(None)
        return False

    def on_access_response(self, bus, sender, path, iface, signal, params):
        """Continue to the remote when access is granted."""
        if self._state != "opening":
            return
        response_code = params.unpack()[0]
        if response_code != 0:
            logger.warning(f"[CameraPortal] Camera access denied: {response_code}")
            self.finish(None)
            return
        self._bus.call_with_unix_fd_list(
            PORTAL_BUS, PORTAL_PATH, CAMERA_IFACE, "OpenPipeWireRemote",
            GLib.Variant("(a{sv})", ({},)), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, PORTAL_CALL_TIMEOUT_MS, None, None,
            self.on_remote_opened)

    def on_remote_opened(self, bus, result):
        """Adopt the PipeWire remote and start reading its devices."""
        try:
            reply, fd_list = bus.call_with_unix_fd_list_finish(result)
        except GLib.Error as e:
            logger.error(f"[CameraPortal] OpenPipeWireRemote failed: {e}")
            self.finish(None)
            return
        try:
            self._fd = fd_list.get(reply.unpack()[0])
        except GLib.Error as e:
            logger.error(f"[CameraPortal] Portal fd missing from reply: {e}")
            self.finish(None)
            return
        self.refill_spare()
        self.start_provider()

    def start_provider(self):
        """Enumerate the remote's nodes through the device provider.

        The provider populates asynchronously after start, with no
        completion signal worth the name, so a short poll stands in;
        cameras that exist appear within the first few ticks.
        """
        from telephony.shared.utils.gst_utils import get_gst
        gst = get_gst()
        factory = gst.DeviceProviderFactory.find("pipewiredeviceprovider")
        if factory is None:
            logger.error("[CameraPortal] pipewiredeviceprovider not available")
            self.finish(None)
            return
        self._provider = factory.get()
        try:
            self._provider.set_property("fd", os.dup(self._fd))
        except OSError as e:
            logger.error(f"[CameraPortal] Cannot dup portal fd: {e}")
            self.finish(None)
            return
        self._provider.start()
        self._poll_deadline = GLib.get_monotonic_time() + DEVICE_POLL_TIMEOUT_MS * 1000
        self._poll_source = GLib.timeout_add(DEVICE_POLL_INTERVAL_MS, self.poll_devices)

    def poll_devices(self):
        """Collect the camera nodes once the provider shows them."""
        devices = self._provider.get_devices() if self._provider else []
        if devices:
            self._poll_source = None
            self._devices = [self.describe(d, i) for i, d in enumerate(devices)]
            for device in self._devices:
                logger.info(f"[CameraPortal] Camera: {device}")
            self.maybe_ready()
            return False
        if GLib.get_monotonic_time() > self._poll_deadline:
            self._poll_source = None
            logger.error("[CameraPortal] No camera nodes on the portal remote")
            self.finish(None)
            return False
        return True

    def maybe_ready(self):
        """Answer only once the devices and a pipeline remote both exist.

        The device poll and the spare fetch race; a caller answered on
        the first alone would ask for a pipeline remote that is still
        in flight and be refused for pure timing.
        """
        if self._state == "opening" and self._devices and self._spare_fd >= 0:
            self.finish(self._devices)

    def describe(self, device, index):
        """Flatten one provider device into a CameraDevice.

        The serial is what a source is targeted with: this platform's
        patched pipewiresrc reads target-object as a numeric camera id
        and silently falls back to camera zero on anything else, while
        upstream accepts a serial too, so the number is the one form
        both understand. The list position stands in when the property
        is missing.
        """
        props = device.get_properties()
        rotation = props.get_value("api.libcamera.rotation")
        serial = props.get_value("object.serial")
        return CameraDevice(
            serial=int(serial) if serial is not None else index,
            node_name=props.get_string("node.name") or "",
            location=props.get_string("api.libcamera.location") or "",
            rotation=int(rotation) if rotation is not None else 0,
            description=device.get_display_name() or "")

    def finish(self, devices):
        """Settle the handshake and answer everyone who asked."""
        self._state = "ready" if devices else "failed"
        if self._signal_id and self._bus:
            self._bus.signal_unsubscribe(self._signal_id)
            self._signal_id = 0
        if self._response_timeout_source is not None:
            GLib.source_remove(self._response_timeout_source)
            self._response_timeout_source = None
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback(devices)
        if devices is None:
            self.close()

    def device_for(self, camera_device):
        """Map the back-is-zero convention onto the enumerated nodes.

        Cameras are asked for by facing, not by index: a phone with two
        back sensors still has exactly one answer for "the front one".
        Without location metadata the list order stands in, which on a
        laptop makes zero the built-in webcam.
        """
        wanted = "front" if camera_device == FRONT_CAMERA else "back"
        for device in self._devices:
            if device.location == wanted:
                return device
        if self._devices:
            return self._devices[min(camera_device, len(self._devices) - 1)]
        return None

    def pipeline_fd(self):
        """Hand out a fresh remote for one pipewiresrc, which owns it.

        A PipeWire remote is a per-client protocol stream, so a dup of
        the enumerator's fd is the same conversation twice: the second
        speaker's requests are ignored and its source falls back to
        the default node, which pinned every flip to the back camera.
        Each pipeline therefore gets its own remote from the portal,
        pre-fetched so this stays answerable without waiting.
        """
        if self._spare_fd < 0:
            logger.error("[CameraPortal] No spare portal remote ready")
            return -1
        fd, self._spare_fd = self._spare_fd, -1
        self.refill_spare()
        return fd

    def refill_spare(self):
        """Fetch the next pipeline remote while nobody is waiting."""
        if self._refilling or self._bus is None:
            return
        self._refilling = True
        self._bus.call_with_unix_fd_list(
            PORTAL_BUS, PORTAL_PATH, CAMERA_IFACE, "OpenPipeWireRemote",
            GLib.Variant("(a{sv})", ({},)), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, PORTAL_CALL_TIMEOUT_MS, None, None,
            self.on_spare_opened)

    def on_spare_opened(self, bus, result):
        """Bank the pre-fetched remote for the next pipeline."""
        self._refilling = False
        try:
            reply, fd_list = bus.call_with_unix_fd_list_finish(result)
            fd = fd_list.get(reply.unpack()[0])
        except GLib.Error as e:
            logger.error(f"[CameraPortal] Spare remote fetch failed: {e}")
            if self._state == "opening":
                self.finish(None)
            return
        if self._state == "failed" or self._bus is None:
            try:
                os.close(fd)
            except OSError as e:
                logger.warning(f"[CameraPortal] Spare fd close failed: {e}")
            return
        self._spare_fd = fd
        self.maybe_ready()

    def close(self):
        """Drop the provider, the remote and every pending answer."""
        if self._poll_source is not None:
            GLib.source_remove(self._poll_source)
            self._poll_source = None
        if self._response_timeout_source is not None:
            GLib.source_remove(self._response_timeout_source)
            self._response_timeout_source = None
        if self._signal_id and self._bus:
            self._bus.signal_unsubscribe(self._signal_id)
            self._signal_id = 0
        if self._provider is not None:
            self._provider.stop()
            self._provider = None
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError as e:
                logger.warning(f"[CameraPortal] Portal fd close failed: {e}")
            self._fd = -1
        if self._spare_fd >= 0:
            try:
                os.close(self._spare_fd)
            except OSError as e:
                logger.warning(f"[CameraPortal] Spare fd close failed: {e}")
            self._spare_fd = -1
        self._devices = []
        if self._state != "failed":
            self._state = "idle"
