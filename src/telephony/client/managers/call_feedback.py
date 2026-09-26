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

import time

import gi
from telephony.shared.utils.log_utils import logger

gi.require_version('Lfb', '0.0')
from gi.repository import Lfb, Gio, GLib

from telephony.shared.utils.gst_utils import get_gst
from telephony.shared.constants import APP_ID

KNOCK_MIN_INTERVAL_SECONDS = 1
KNOCK_SOUND_URI = "file:///usr/share/sounds/freedesktop/stereo/device-added.oga"


class CallFeedback:
    """The incall window's feedback: tones, vibration and proximity.

    Everything that changes what the call sounds like — routes, the
    voice profile, volume, ringing — belongs to the daemon's audio
    manager; this class only plays local feedback tones and claims
    the proximity sensor for the screen blank.
    """

    def __init__(self, app_id=APP_ID):
        """Initialize feedback and the proximity sensor proxy."""
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
        """Ask for the sensor daemon's proxy."""
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
            "net.hadess.SensorProxy", "/net/hadess/SensorProxy",
            "net.hadess.SensorProxy", None,
            self.on_sensor_proxy_ready, None
        )

    def on_sensor_proxy_ready(self, _source, result, _user_data):
        """Take the sensor proxy and the proximity state it arrived with.

        Building the proxy reads the interface's properties, so the
        starting value is already cached here and does not need asking
        for again. A claim that was wanted while the proxy was still
        being built is applied now.
        """
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
        """Handle sensor property changes."""
        try:
            unpacked = changed.unpack()
            if "ProximityNear" in unpacked:
                self.is_near = unpacked["ProximityNear"]
                logger.info(f"[Hardware] Proximity Near: {self.is_near}")
        except Exception as e:
            logger.error(f"[Hardware] Sensor Changed Error: {e}")

    def update_hardware_state(self, is_earpiece_active):
        """Claim the proximity sensor while the earpiece is at the ear."""
        self.claim_wanted = bool(is_earpiece_active)
        self.send_claim()

    def send_claim(self):
        """Move the sensor daemon towards the claim the call asks for.

        Only one of these is ever out at a time and the wanted state is
        reconciled when it answers, so a route flipped twice while the
        first call is still in flight settles on what was asked for last
        rather than on whichever reply happens to land last.
        """
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
        """Record what the sensor daemon did, then settle any later change."""
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
        """Play the standard alert sound and vibration for entering an error state."""
        if not self.lfb_available:
            return
        try:
            Lfb.Event.new("message-new-instant").trigger_feedback_async(None, None, None)
        except Exception as e:
            logger.error(f"[Feedback] Error alert failed: {e}")

    def play_hangup(self, feedback=True):
        """Release the proximity claim; sound the feedback when asked.

        The caller passes feedback=False when this side requested the
        hangup, because the tone announces the other side ending the
        call, while the sensor cleanup is owed either way.
        """
        self.update_hardware_state(False)

        if feedback and self.lfb_available:
            try:
                Lfb.Event.new("phone-hangup").trigger_feedback_async(None, None, None)
            except Exception as e:
                logger.error(f"[Feedback] Play hangup failed: {e}")

    def play_knock(self):
        """Play a knock sound."""
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
        """Release the knock pipeline and its bus watch."""
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
        """Handle End-Of-Stream for knock player."""
        try:
            self.teardown_knock_pipeline()
        except Exception as e:
            logger.error(f"[Feedback] Knock EOS error: {e}")

    def on_knock_error(self, bus, msg):
        """Handle error for knock player."""
        try:
            err, debug = msg.parse_error()
            logger.error(f"[Feedback] Knock pipeline error: {err} - {debug}")
            self.teardown_knock_pipeline()
        except Exception as e:
            logger.error(f"[Feedback] Knock error handler failed: {e}")
