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
import time
import gi
import pulsectl
from loguru import logger

gi.require_version('Lfb', '0.0')
gi.require_version('Gst', '1.0')
from gi.repository import Lfb, Gio, GLib, Gst

from ...backend.utils.system_utils import get_feedbackd_profile, set_feedbackd_profile


class TelephonyAudioManager:
    """
    Manages audio routing, feedback (ringing), and proximity sensor.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(TelephonyAudioManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, app_id="io.furios.Telephony"):
        """Initialize the audio manager singleton."""
        if self._initialized:
            return
        self._initialized = True
        self._last_mute_state = None
        self.lfb_available = True

        try:
            if not Gst.is_initialized():
                Gst.init(None)
        except Exception as e:
            logger.warning(f"[Audio] Gst init failed: {e}")

        try:
            Lfb.init(app_id)
        except Exception as e:
            logger.warning(f"[Audio] Lfb init failed: {e}")
            self.lfb_available = False

        self.is_ringing = False
        self.ringing_event = None

        self.last_knock_time = 0
        self.knock_pipeline = None

        self._pre_max_fb_profile = None
        self._pre_max_mute = None
        self._pre_max_vol = None
        self._pre_max_sink_name = None

        self._pre_call_vol = None
        self._pre_call_sink_name = None
        self._pre_call_port = None

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
        """Update hardware state based on whether the earpiece is active."""
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

    def start_ringing(self, custom_path=None):
        """Start the ringing feedback."""
        if not self.lfb_available or self.is_ringing:
            return
        try:
            self.is_ringing = True
            self.ringing_event = Lfb.Event.new("phone-incoming-call")
            self.ringing_event.set_timeout(0)

            if custom_path:
                if os.path.exists(custom_path):
                    logger.debug(f"[Audio] Request to play custom ringtone: {custom_path}")
                    self.ringing_event.set_sound_file(custom_path)
                else:
                    logger.warning(f"[Audio] Custom ringtone file not found: {custom_path}")

            self.ringing_event.trigger_feedback_async(None, None, None)
        except Exception as e:
            logger.error(f"[Audio] Start ringing failed: {e}")
            self.is_ringing = False

    def stop_ringing(self):
        """Stop the ringing feedback."""
        if not self.is_ringing:
            return

        if hasattr(self, 'ringing_id') and self.ringing_id:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                proxy = Gio.DBusProxy.new_sync(
                    bus, Gio.DBusProxyFlags.NONE, None,
                    "org.sigxcpu.feedbackd", "/org/sigxcpu/feedbackd",
                    "org.sigxcpu.Feedback", None
                )
                proxy.call_sync(
                    "EndFeedback",
                    GLib.Variant('(u)', (self.ringing_id,)),
                    Gio.DBusCallFlags.NONE, -1, None
                )
            except Exception as e:
                logger.warning(f"[Audio] Manual EndFeedback failed: {e}")
            self.ringing_id = None

        if self.ringing_event:
            try:
                self.ringing_event.end_feedback_async(None, None, None)
            except Exception as e:
                logger.warning(f"[Audio] Stop ringing warning: {e}")
            finally:
                self.ringing_event = None

        self.is_ringing = False

    def play_hangup(self):
        """Play hangup feedback and release proximity."""
        self.stop_ringing()
        if self.proximity_claimed:
            self._set_claim(False)

        if self.lfb_available:
            try:
                Lfb.Event.new("phone-hangup").trigger_feedback_async(None, None, None)
            except Exception as e:
                logger.error(f"[Audio] Play hangup failed: {e}")

    def play_knock(self):
        """Play a knock sound."""
        try:
            now = time.time()
            if now - self.last_knock_time < 1:
                return

            self.last_knock_time = now
            try:
                if self.knock_pipeline:
                    self.knock_pipeline.set_state(Gst.State.NULL)

                uri = "file:///usr/share/sounds/freedesktop/stereo/device-added.oga"
                self.knock_pipeline = Gst.ElementFactory.make("playbin", "knock_player")
                self.knock_pipeline.set_property("uri", uri)
                self.knock_pipeline.set_state(Gst.State.PLAYING)

                bus = self.knock_pipeline.get_bus()
                bus.add_signal_watch()
                bus.connect("message::eos", self._on_knock_eos)
                bus.connect("message::error", self._on_knock_error)

            except Exception as e:
                logger.error(f"[Audio] Knock failed: {e}")
        except Exception as e:
            logger.error(f"[Audio] Play knock error: {e}")

    def _on_knock_eos(self, bus, msg):
        """Handle End-Of-Stream for knock player."""
        try:
            if self.knock_pipeline:
                self.knock_pipeline.set_state(Gst.State.NULL)
                self.knock_pipeline = None
        except Exception as e:
            logger.error(f"[Audio] Knock EOS error: {e}")

    def _on_knock_error(self, bus, msg):
        """Handle error for knock player."""
        try:
            err, debug = msg.parse_error()
            logger.error(f"[Audio] Knock pipeline error: {err} - {debug}")
            if self.knock_pipeline:
                self.knock_pipeline.set_state(Gst.State.NULL)
                self.knock_pipeline = None
        except Exception as e:
            logger.error(f"[Audio] Knock error handler failed: {e}")

    def set_voice_profile(self, enable=True):
        """Set the PulseAudio card profile to voicecall or default."""
        profile_name = "voicecall" if enable else "default"
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                cards = pulse.card_list()
                target_card = None
                for c in cards:
                    if c.name == "droid_card.primary":
                        target_card = c
                        break

                if target_card:
                    pulse.card_profile_set(target_card, profile_name)
                    if not enable:
                        sink = pulse.get_sink_by_name("sink.primary_output")
                        if sink:
                            pulse.sink_port_set(sink.index, "output-speaker")
                else:
                    logger.warning("[Audio] droid_card.primary not found")

        except Exception as e:
            logger.error(f"[Audio] Set profile failed: {e}")

    def set_audio_route(self, mode="earpiece"):
        """Route audio to earpiece, speaker, or dummy routes."""
        if mode == "speaker":
            port_name = "output-speaker"
        elif mode == "earpiece":
            port_name = "output-earpiece"
        else:
            logger.info(f"[Audio] Setting dummy output route: {mode}")
            return

        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                sink = pulse.get_sink_by_name("sink.primary_output")
                if sink:
                    pulse.sink_port_set(sink.index, "output-parking")
                    pulse.sink_port_set(sink.index, port_name)
                else:
                    logger.warning("[Audio] sink.primary_output not found")
        except Exception as e:
            logger.error(f"[Audio] Set route failed: {e}")

    def set_input_route(self, mode="mic"):
        """Route input audio to mic or dummy routes."""
        logger.info(f"[Audio] Setting input route: {mode}")

    def get_available_outputs(self):
        """Return a list of available output routes."""
        routes = [{"id": "earpiece", "name": "Earpiece", "icon": "audio-headphones-symbolic"},
                  {"id": "speaker", "name": "Speaker", "icon": "audio-speakers-symbolic"}]
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                cards = pulse.card_list()
                for c in cards:
                    if "bluez" in c.name:
                        routes.append({"id": "bluetooth", "name": "Bluetooth", "icon": "bluetooth-active-symbolic"})
                        break
                sink = pulse.get_sink_by_name("sink.primary_output")
                if sink:
                    for p in sink.port_list:
                        if "wired_headphone" in p.name or "headset" in p.name:
                            routes.append({"id": "wired", "name": "Wired Headset", "icon": "audio-headphones-symbolic"})
                            break
        except Exception as e:
            logger.warning(f"[Audio] Failed to get available outputs: {e}")
            if not any(r['id'] == 'earpiece' for r in routes):
                routes.append({"id": "earpiece", "name": "Earpiece", "icon": "audio-headphones-symbolic"})
            if not any(r['id'] == 'speaker' for r in routes):
                routes.append({"id": "speaker", "name": "Speaker", "icon": "audio-speakers-symbolic"})

        if not any(r['id'] == 'wired' for r in routes):
            routes.append({"id": "wired", "name": "Wired Headset", "icon": "audio-headphones-symbolic"})
        if not any(r['id'] == 'bluetooth' for r in routes):
            routes.append({"id": "bluetooth", "name": "Bluetooth", "icon": "bluetooth-active-symbolic"})

        return routes

    def get_available_inputs(self):
        """Return a list of available input routes."""
        routes = [{"id": "mic", "name": "Microphone", "icon": "audio-input-microphone-symbolic"}]
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                cards = pulse.card_list()
                for c in cards:
                    if "bluez" in c.name:
                        routes.append({"id": "bluetooth", "name": "Bluetooth Mic", "icon": "bluetooth-active-symbolic"})
                        break
        except Exception as e:
            logger.warning(f"[Audio] Failed to get available inputs: {e}")

        if not any(r['id'] == 'wired' for r in routes):
            routes.append({"id": "wired", "name": "Wired Mic", "icon": "audio-input-microphone-symbolic"})
        if not any(r['id'] == 'bluetooth' for r in routes):
            routes.append({"id": "bluetooth", "name": "Bluetooth Mic", "icon": "bluetooth-active-symbolic"})

        return routes

    def _get_call_sink(self, pulse, preferred_name=None):
        """Return the sink used for call audio, preferring the droid primary output."""
        for name in (preferred_name, "sink.primary_output"):
            if not name:
                continue
            try:
                return pulse.get_sink_by_name(name)
            except Exception as e:
                logger.debug(f"[Audio] Sink {name} not found: {e}")

        info = pulse.server_info()
        try:
            return pulse.get_sink_by_name(info.default_sink_name)
        except Exception as e:
            logger.debug(f"[Audio] Default sink lookup failed: {e}")
            return None

    def prepare_call_audio(self, level):
        """
        Save the media port and volume once, then prime the earpiece port at
        the configured call level so voicecall routing starts with it.
        """
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                sink = self._get_call_sink(pulse)
                if not sink:
                    logger.warning("[Audio] No sink found to prepare call audio")
                    return

                if self._pre_call_vol is None:
                    self._pre_call_sink_name = sink.name
                    self._pre_call_vol = max(sink.volume.values) if sink.volume.values else 1.0
                    self._pre_call_port = sink.port_active.name if sink.port_active else None

                try:
                    pulse.sink_port_set(sink.index, "output-earpiece")
                except Exception as e:
                    logger.debug(f"[Audio] Earpiece port set failed: {e}")

                pulse.volume_set_all_chans(sink, level)
                logger.info(f"[Audio] Prepared earpiece at {int(level * 100)}% for call start")
        except Exception as e:
            logger.error(f"[Audio] Prepare call audio failed: {e}")

    def _pick_media_port(self, sink):
        """Choose the output port to return to after the last call ends."""
        usable = [p.name for p in sink.port_list if getattr(p, 'available', None) != 'no']

        if self._pre_call_port and self._pre_call_port != "output-parking" and self._pre_call_port in usable:
            return self._pre_call_port

        for name in ("output-wired_headphone", "output-wired_headset", "output-speaker"):
            if name in usable:
                return name
        return None

    def push_call_volume(self, level):
        """
        Apply the configured base call volume to the call sink.
        The previous volume is saved once; restore with restore_call_volume.
        Values above 1.0 apply software amplification.
        """
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                sink = self._get_call_sink(pulse)
                if not sink:
                    logger.warning("[Audio] No sink found for call volume push")
                    return

                if self._pre_call_vol is None:
                    self._pre_call_sink_name = sink.name
                    self._pre_call_vol = max(sink.volume.values) if sink.volume.values else 1.0

                pulse.volume_set_all_chans(sink, level)
                logger.info(f"[Audio] Call volume set to {int(level * 100)}% on {sink.name}")
        except Exception as e:
            logger.error(f"[Audio] Push call volume failed: {e}")

    def restore_call_volume(self):
        """Restore the media output port and volume saved by prepare_call_audio."""
        if self._pre_call_vol is None:
            return
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                sink = self._get_call_sink(pulse, self._pre_call_sink_name)
                if sink:
                    target_port = self._pick_media_port(sink)
                    if target_port:
                        try:
                            pulse.sink_port_set(sink.index, target_port)
                        except Exception as e:
                            logger.debug(f"[Audio] Media port restore failed: {e}")
                    pulse.volume_set_all_chans(sink, self._pre_call_vol)
                    logger.info(f"[Audio] Restored media audio on {sink.name} port {target_port}")
                else:
                    logger.warning("[Audio] Could not find sink to restore call volume")
        except Exception as e:
            logger.error(f"[Audio] Restore call volume failed: {e}")
        finally:
            self._pre_call_vol = None
            self._pre_call_sink_name = None
            self._pre_call_port = None

    def mute(self, muted=True):
        """Mute or unmute the default source."""
        if self._last_mute_state == muted:
            return

        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                info = pulse.server_info()
                default_source_name = info.default_source_name

                source = pulse.get_source_by_name(default_source_name)
                if source:
                    pulse.source_mute(source.index, muted)
                    self._last_mute_state = muted
                    logger.info(f"[Audio] Microphone mute set to: {muted}")
                else:
                    logger.warning(f"[Audio] Default source {default_source_name} not found")

        except Exception as e:
            logger.error(f"[Audio] Mute failed: {e}")

    def force_max_feedback(self, restore=False):
        """
        Force un-mute and set volume to 100% to override silent modes.
        If restore=True, attempts to restore previous state.
        Safe for multiple calls: only first call saves state.
        """
        if restore:
            try:
                if self._pre_max_fb_profile:
                    set_feedbackd_profile(self._pre_max_fb_profile)
                    self._pre_max_fb_profile = None

                with pulsectl.Pulse('telephony-audio') as pulse:
                    target_sink_name = self._pre_max_sink_name
                    sink = None
                    if target_sink_name:
                        try:
                            sink = pulse.get_sink_by_name(target_sink_name)
                        except Exception:
                            logger.warning(f"[Audio] Saved sink {target_sink_name} not found, trying default.")

                    if not sink:
                        info = pulse.server_info()
                        sink = pulse.get_sink_by_name(info.default_sink_name)

                    if sink:
                        if self._pre_max_mute is not None:
                            pulse.sink_mute(sink.index, self._pre_max_mute)
                            self._pre_max_mute = None

                        if self._pre_max_vol is not None:
                            pulse.volume_set_all_chans(sink, self._pre_max_vol)
                            self._pre_max_vol = None

                        self._pre_max_sink_name = None
                        logger.info(f"[Audio] Restored volume state on sink: {sink.name}")
                    else:
                        logger.warning("[Audio] Could not find sink to restore volume.")
            except Exception as e:
                logger.error(f"[Audio] Restore volume failed: {e}")
            return

        if self._pre_max_fb_profile is not None:
            logger.debug("[Audio] Force max feedback already active, skipping state save.")
        else:
            self._pre_max_fb_profile = get_feedbackd_profile()

        current_profile = get_feedbackd_profile()
        if current_profile != "full":
            set_feedbackd_profile("full")

        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                info = pulse.server_info()
                sink = pulse.get_sink_by_name(info.default_sink_name)

                if sink:
                    if self._pre_max_sink_name is None:
                        self._pre_max_sink_name = sink.name

                    if self._pre_max_mute is None:
                        self._pre_max_mute = sink.mute

                    if self._pre_max_vol is None:
                        if sink.volume.values:
                            self._pre_max_vol = max(sink.volume.values) / 65536.0
                        else:
                            self._pre_max_vol = 0.5

                    pulse.sink_mute(sink.index, False)
                    pulse.volume_set_all_chans(sink, 1.0)
                    logger.info(f"[Audio] Forced MAX volume on sink: {sink.name}")
                else:
                    logger.warning("[Audio] Default sink not found for max feedback")
        except Exception as e:
            logger.error(f"[Audio] Force max feedback pulse error: {e}")
