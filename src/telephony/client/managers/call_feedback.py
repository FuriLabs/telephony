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
        self.sensor_proxy = None
        self._init_sensor_proxy()

    def _init_sensor_proxy(self):
        """Initialize the DBus proxy for the sensor daemon."""
        try:
            self.sensor_proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                "net.hadess.SensorProxy", "/net/hadess/SensorProxy",
                "net.hadess.SensorProxy", None
            )
            self.sensor_proxy.connect("g-properties-changed", self._on_sensor_changed)

            res = self.sensor_proxy.get_connection().call_sync(
                self.sensor_proxy.get_name(),
                self.sensor_proxy.get_object_path(),
                "org.freedesktop.DBus.Properties",
                "Get",
                GLib.Variant('(ss)', ("net.hadess.SensorProxy", "ProximityNear")),
                GLib.VariantType('(v)'),
                Gio.DBusCallFlags.NONE,
                -1,
                None
            )
            if res:
                self.is_near = bool(res.unpack()[0])
            else:
                cached = self.sensor_proxy.get_cached_property("ProximityNear")
                if cached:
                    self.is_near = cached.get_boolean()
        except Exception as e:
            logger.error(f"[Hardware] SensorProxy Error: {e}")

    def _on_sensor_changed(self, proxy, changed, _invalidated):
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
        if is_earpiece_active and not self.proximity_claimed:
            self._set_claim(True)
        elif not is_earpiece_active and self.proximity_claimed:
            self._set_claim(False)

    def _set_claim(self, claim):
        """Claim or release the proximity sensor."""
        if not self.sensor_proxy:
            return
        method = "ClaimProximity" if claim else "ReleaseProximity"
        try:
            self.sensor_proxy.call_sync(method, None, Gio.DBusCallFlags.NONE, -1, None)
            self.proximity_claimed = claim
            logger.info(f"[Hardware] {method} successful")
        except Exception as e:
            logger.error(f"[Hardware] {method} failed: {e}")

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
        if self.proximity_claimed:
            self._set_claim(False)

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
                    self._teardown_knock_pipeline()

                Gst = get_gst()
                self.knock_pipeline = Gst.ElementFactory.make("playbin", "knock_player")
                self.knock_pipeline.set_property("uri", KNOCK_SOUND_URI)
                self.knock_pipeline.set_state(Gst.State.PLAYING)

                self.knock_bus = self.knock_pipeline.get_bus()
                self.knock_bus.add_signal_watch()
                self.knock_bus_handlers = [
                    self.knock_bus.connect("message::eos", self._on_knock_eos),
                    self.knock_bus.connect("message::error", self._on_knock_error),
                ]

            except Exception as e:
                logger.error(f"[Feedback] Knock failed: {e}")
        except Exception as e:
            logger.error(f"[Feedback] Play knock error: {e}")

    def _teardown_knock_pipeline(self):
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

    def _on_knock_eos(self, bus, msg):
        """Handle End-Of-Stream for knock player."""
        try:
            self._teardown_knock_pipeline()
        except Exception as e:
            logger.error(f"[Feedback] Knock EOS error: {e}")

    def _on_knock_error(self, bus, msg):
        """Handle error for knock player."""
        try:
            err, debug = msg.parse_error()
            logger.error(f"[Feedback] Knock pipeline error: {err} - {debug}")
            self._teardown_knock_pipeline()
        except Exception as e:
            logger.error(f"[Feedback] Knock error handler failed: {e}")
