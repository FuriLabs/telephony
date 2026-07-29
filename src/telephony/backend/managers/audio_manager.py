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
                else:
                    logger.warning("[Audio] droid_card.primary not found")

        except Exception as e:
            logger.error(f"[Audio] Set profile failed: {e}")

    def _pick_route_port(self, sink, mode):
        """Map a route id to the sink port to activate, or None when absent."""
        if mode == "earpiece":
            return "output-earpiece"
        if mode == "speaker":
            return "output-speaker"
        if mode == "wired":
            for name in ("output-wired_headset", "output-wired_headphone"):
                for p in sink.port_list:
                    if p.name == name and getattr(p, 'available', None) != 'no':
                        return name
        return None

    def set_audio_route(self, mode="earpiece"):
        """
        Route call audio with a single direct port switch on the primary sink.
        A parking hop is never used here: parking is only meant for profile
        changes and the droid card module parks by itself around those.
        """
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                sink = pulse.get_sink_by_name("sink.primary_output")
                if not sink:
                    logger.warning("[Audio] sink.primary_output not found")
                    return

                port_name = self._pick_route_port(sink, mode)
                if not port_name:
                    logger.info(f"[Audio] No port for output route: {mode}")
                    return

                pulse.sink_port_set(sink.index, port_name)
                logger.info(f"[Audio] Output route set to {mode} ({port_name})")
        except Exception as e:
            logger.error(f"[Audio] Set route failed: {e}")

    def set_input_route(self, mode="mic"):
        """Route input audio to mic or dummy routes."""
        logger.info(f"[Audio] Setting input route: {mode}")

    def initial_call_route(self):
        """Return the route a new call should start on."""
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                sink = pulse.get_sink_by_name("sink.primary_output")
                if sink and self._pick_route_port(sink, "wired"):
                    return "wired"
        except Exception as e:
            logger.debug(f"[Audio] Initial route probe failed: {e}")
        return "earpiece"

    def get_active_output_route(self):
        """Map the primary sink's active port to a route id, or None when parked."""
        name = None
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                sink = pulse.get_sink_by_name("sink.primary_output")
                if sink and sink.port_active:
                    name = sink.port_active.name
        except Exception as e:
            logger.debug(f"[Audio] Active route probe failed: {e}")

        if name == "output-earpiece":
            return "earpiece"
        if name == "output-speaker":
            return "speaker"
        if name and "wired" in name:
            return "wired"
        return None

    def set_voice_volume_level(self, level):
        """
        Apply the call volume by setting the phone-role virtual stream volume.
        The droid module forwards only actual changes to the HAL voice volume,
        so the value is written twice with a small nudge in between.
        Returns True when the volume reached a control stream.
        """
        level = max(0.0, min(1.0, level))
        nudge = level + 0.01 if level <= 0.5 else level - 0.01
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                stream = None
                for si in pulse.sink_input_list():
                    if si.proplist.get('media.role') == 'phone':
                        stream = si
                        break

                if not stream:
                    logger.warning("[Audio] No phone-role stream found for voice volume")
                    return False

                pulse.volume_set_all_chans(stream, nudge)
                pulse.volume_set_all_chans(stream, level)
                logger.info(f"[Audio] Voice volume {int(level * 100)}% on stream #{stream.index} ({stream.name})")
                return True
        except Exception as e:
            logger.error(f"[Audio] Set voice volume failed: {e}")
            return False

    def ensure_sink_unmuted(self):
        """Clear any mute that module-device-restore re-applied on a port change."""
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                sink = pulse.get_sink_by_name("sink.primary_output")
                if sink and sink.mute:
                    pulse.sink_mute(sink.index, False)
                    logger.info("[Audio] Cleared restored sink mute")
        except Exception as e:
            logger.error(f"[Audio] Unmute failed: {e}")

    def get_available_outputs(self):
        """Return the output routes with per-route availability."""
        has_bt = False
        has_wired = False
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                for c in pulse.card_list():
                    if "bluez" in c.name:
                        has_bt = True
                        break
                sink = pulse.get_sink_by_name("sink.primary_output")
                if sink:
                    for p in sink.port_list:
                        if "wired_headphone" in p.name or "headset" in p.name:
                            if getattr(p, 'available', None) != 'no':
                                has_wired = True
                                break
        except Exception as e:
            logger.warning(f"[Audio] Failed to probe output routes: {e}")

        return [
            {"id": "earpiece", "name": "Earpiece", "icon": "audio-headphones-symbolic", "available": True},
            {"id": "speaker", "name": "Speaker", "icon": "audio-speakers-symbolic", "available": True},
            {"id": "wired", "name": "Wired Headset", "icon": "audio-headphones-symbolic", "available": has_wired},
            {"id": "bluetooth", "name": "Bluetooth", "icon": "bluetooth-active-symbolic", "available": has_bt},
        ]

    def get_available_inputs(self):
        """Return the input routes with per-route availability."""
        has_bt = False
        has_wired = False
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                for c in pulse.card_list():
                    if "bluez" in c.name:
                        has_bt = True
                        break
                sink = pulse.get_sink_by_name("sink.primary_output")
                if sink:
                    for p in sink.port_list:
                        if "headset" in p.name and getattr(p, 'available', None) != 'no':
                            has_wired = True
                            break
        except Exception as e:
            logger.warning(f"[Audio] Failed to probe input routes: {e}")

        return [
            {"id": "mic", "name": "Microphone", "icon": "audio-input-microphone-symbolic", "available": True},
            {"id": "wired", "name": "Wired Mic", "icon": "audio-input-microphone-symbolic", "available": has_wired},
            {"id": "bluetooth", "name": "Bluetooth Mic", "icon": "bluetooth-active-symbolic", "available": has_bt},
        ]

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

    def save_media_state(self):
        """Snapshot the media volume and active port once before a call."""
        if self._pre_call_vol is not None:
            return
        try:
            with pulsectl.Pulse('telephony-audio') as pulse:
                sink = self._get_call_sink(pulse)
                if not sink:
                    logger.warning("[Audio] No sink found to save media state")
                    return

                self._pre_call_sink_name = sink.name
                self._pre_call_vol = max(sink.volume.values) if sink.volume.values else 1.0
                self._pre_call_port = sink.port_active.name if sink.port_active else None
                logger.info(f"[Audio] Saved media state: {int(self._pre_call_vol * 100)}% on {self._pre_call_port}")
        except Exception as e:
            logger.error(f"[Audio] Save media state failed: {e}")

    def _pick_media_port(self, sink):
        """Choose the output port to return to after the last call ends."""
        usable = [p.name for p in sink.port_list if getattr(p, 'available', None) != 'no']

        if self._pre_call_port and self._pre_call_port != "output-parking" and self._pre_call_port in usable:
            return self._pre_call_port

        for name in ("output-wired_headphone", "output-wired_headset", "output-speaker"):
            if name in usable:
                return name
        return None

    def restore_call_volume(self):
        """
        Restore the media output port and volume saved by save_media_state.
        Run after the profile is back to default: the port switch commits the
        normal mode in the HAL, and the volume is written as an actual change
        because the pcm gain stays frozen for equal values after a call.
        """
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

                    nudge = self._pre_call_vol + 0.01 if self._pre_call_vol <= 0.5 else self._pre_call_vol - 0.01
                    pulse.volume_set_all_chans(sink, nudge)
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
