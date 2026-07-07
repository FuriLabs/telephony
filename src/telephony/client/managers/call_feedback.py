# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import time

import gi
from telephony.shared.utils.log_utils import logger

gi.require_version("Lfb", "0.0")
from gi.repository import Lfb, Gio, GLib

from telephony.shared.utils.gst_utils import get_gst
from telephony.shared.constants import APP_ID

KNOCK_MIN_INTERVAL_SECONDS = 1
KNOCK_SOUND_URI = "file:///usr/share/sounds/freedesktop/stereo/device-added.oga"


class CallFeedback:

    def __init__(self, app_id=APP_ID):
        self.lfb_available = True
        try:
            Lfb.init(app_id)
        except Exception as e:
            logger.warning(f"[Feedback] Lfb init failed: {e}")
            self.lfb_available = False

        self.last_knock_time = 0
        self.knock_pipeline = None
        self.knock_bus = None
        self.knock_bus_handlers = []

        self.is_near = False
        self.proximity_claimed = False
        self.claim_wanted = False
        self.claim_in_flight = False
        self.sensor_proxy = None
        self.init_sensor_proxy()

    def init_sensor_proxy(self):
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
            "net.hadess.SensorProxy", "/net/hadess/SensorProxy",
            "net.hadess.SensorProxy", None,
            self.on_sensor_proxy_ready, None
        )

    def on_sensor_proxy_ready(self, _source, result, _user_data):
        try:
            self.sensor_proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error as e:
            logger.error(f"[Hardware] SensorProxy Error: {e}")
            return

        self.sensor_proxy.connect("g-properties-changed", self.on_sensor_changed)

        cached = self.sensor_proxy.get_cached_property("ProximityNear")
        if cached:
            self.is_near = cached.get_boolean()

        if self.claim_wanted:
            self.send_claim()

    def on_sensor_changed(self, proxy, changed, _invalidated):
        try:
            unpacked = changed.unpack()
            if "ProximityNear" in unpacked:
                self.is_near = unpacked["ProximityNear"]
                logger.info(f"[Hardware] Proximity Near: {self.is_near}")
        except Exception as e:
            logger.error(f"[Hardware] Sensor Changed Error: {e}")

    def update_hardware_state(self, is_earpiece_active):
        self.claim_wanted = bool(is_earpiece_active)
        self.send_claim()

    def send_claim(self):
        if not self.sensor_proxy or self.claim_in_flight:
            return

        if self.claim_wanted == self.proximity_claimed:
            return

        method = "ClaimProximity" if self.claim_wanted else "ReleaseProximity"
        self.claim_in_flight = True
        self.sensor_proxy.call(
            method, None, Gio.DBusCallFlags.NONE, -1, None,
            self.on_claim_done, (method, self.claim_wanted)
        )

    def on_claim_done(self, proxy, result, user_data):
        method, claimed = user_data
        self.claim_in_flight = False

        try:
            proxy.call_finish(result)
        except GLib.Error as e:
            logger.error(f"[Hardware] {method} failed: {e}")
            return

        self.proximity_claimed = claimed
        logger.info(f"[Hardware] {method} successful")
        self.send_claim()

    def play_error_alert(self):
        if not self.lfb_available:
            return
        try:
            Lfb.Event.new("message-new-instant").trigger_feedback_async(None, None, None)
        except Exception as e:
            logger.error(f"[Feedback] Error alert failed: {e}")

    def play_hangup(self, feedback=True):
        self.update_hardware_state(False)

        if feedback and self.lfb_available:
            try:
                Lfb.Event.new("phone-hangup").trigger_feedback_async(None, None, None)
            except Exception as e:
                logger.error(f"[Feedback] Play hangup failed: {e}")

    def play_knock(self):
        try:
            now = time.time()
            if now - self.last_knock_time < KNOCK_MIN_INTERVAL_SECONDS:
                return

            self.last_knock_time = now
            try:
                if self.knock_pipeline:
                    self.teardown_knock_pipeline()

                Gst = get_gst()
                self.knock_pipeline = Gst.ElementFactory.make("playbin", "knock_player")
                self.knock_pipeline.set_property("uri", KNOCK_SOUND_URI)
                self.knock_pipeline.set_state(Gst.State.PLAYING)

                self.knock_bus = self.knock_pipeline.get_bus()
                self.knock_bus.add_signal_watch()
                self.knock_bus_handlers = [
                    self.knock_bus.connect("message::eos", self.on_knock_eos),
                    self.knock_bus.connect("message::error", self.on_knock_error),
                ]

            except Exception as e:
                logger.error(f"[Feedback] Knock failed: {e}")
        except Exception as e:
            logger.error(f"[Feedback] Play knock error: {e}")

    def teardown_knock_pipeline(self):
        if self.knock_bus:
            for handler_id in self.knock_bus_handlers:
                try:
                    self.knock_bus.disconnect(handler_id)
                except Exception as e:
                    logger.debug(f"[Feedback] Knock bus disconnect error (ignorable): {e}")
            self.knock_bus_handlers = []
            try:
                self.knock_bus.remove_signal_watch()
            except Exception as e:
                logger.debug(f"[Feedback] Knock bus watch removal error (ignorable): {e}")
            self.knock_bus = None

        if self.knock_pipeline:
            self.knock_pipeline.set_state(get_gst().State.NULL)
            self.knock_pipeline = None

    def on_knock_eos(self, bus, msg):
        try:
            self.teardown_knock_pipeline()
        except Exception as e:
            logger.error(f"[Feedback] Knock EOS error: {e}")

    def on_knock_error(self, bus, msg):
        try:
            err, debug = msg.parse_error()
            logger.error(f"[Feedback] Knock pipeline error: {err} - {debug}")
            self.teardown_knock_pipeline()
        except Exception as e:
            logger.error(f"[Feedback] Knock error handler failed: {e}")
