# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

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

    serial: int
    location: str


class CameraPortal:

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
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            logger.error(f"[CameraPortal] AccessCamera failed: {e}")
            self.finish(None)

    def on_access_timeout(self):
        self._response_timeout_source = None
        if self._state == "opening":
            logger.error("[CameraPortal] No response to AccessCamera")
            self.finish(None)
        return False

    def on_access_response(self, bus, sender, path, iface, signal, params):
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
        if self._state == "opening" and self._devices and self._spare_fd >= 0:
            self.finish(self._devices)

    def describe(self, device, index):
        props = device.get_properties()
        serial = props.get_value("object.serial")
        return CameraDevice(
            serial=int(serial) if serial is not None else index,
            location=props.get_string("api.libcamera.location") or "")

    def finish(self, devices):
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
        wanted = "front" if camera_device == FRONT_CAMERA else "back"
        for device in self._devices:
            if device.location == wanted:
                return device
        if self._devices:
            return self._devices[min(camera_device, len(self._devices) - 1)]
        return None

    def pipeline_fd(self):
        if self._spare_fd < 0:
            logger.error("[CameraPortal] No spare portal remote ready")
            return -1
        fd, self._spare_fd = self._spare_fd, -1
        self.refill_spare()
        return fd

    def refill_spare(self):
        if self._refilling or self._bus is None:
            return
        self._refilling = True
        self._bus.call_with_unix_fd_list(
            PORTAL_BUS, PORTAL_PATH, CAMERA_IFACE, "OpenPipeWireRemote",
            GLib.Variant("(a{sv})", ({},)), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, PORTAL_CALL_TIMEOUT_MS, None, None,
            self.on_spare_opened)

    def on_spare_opened(self, bus, result):
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
