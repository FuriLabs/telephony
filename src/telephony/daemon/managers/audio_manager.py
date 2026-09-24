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
import threading
from contextlib import contextmanager

import gi
from telephony.shared.utils.log_utils import logger

gi.require_version('Lfb', '0.0')
from gi.repository import Lfb

from telephony.shared.utils.system_utils import get_feedbackd_profile, set_feedbackd_profile
from telephony.shared.constants import APP_ID

FALLBACK_MEDIA_VOLUME = 0.5

AUDIO_MANAGER_CARD = "audio-manager-card"
AUDIO_MANAGER_SINK = "audio-manager-output"
AUDIO_MANAGER_SOURCE = "audio-manager-input"
DROID_CARD = "droid_card.primary"
DROID_SINK = "sink.primary_output"
DROID_SOURCE = "source.primary_input"


class TelephonyAudioManager:
    """
    Manages audio routing, the voice profile, volume and ringing.

    Local feedback tones and the proximity sensor live in the incall
    window's CallFeedback; this class is the daemon's single writer for
    everything that changes what a call sounds like.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(TelephonyAudioManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, app_id=APP_ID):
        """Initialize the audio manager singleton."""
        if self._initialized:
            return
        self._initialized = True
        self._last_mute_state = None
        self.current_route = "earpiece"
        self.current_input = "mic"
        self.mic_muted = False
        self.lfb_available = True

        try:
            Lfb.init(app_id)
        except Exception as e:
            logger.warning(f"[Audio] Lfb init failed: {e}")
            self.lfb_available = False

        self.is_ringing = False
        self.ringing_event = None

        self._pre_max_fb_profile = None
        self._pre_max_mute = None
        self._pre_max_vol = None
        self._pre_max_sink_name = None

        self._pre_call_vol = None
        self._pre_call_sink_name = None
        self._pre_call_port = None

        self._pulse_conn = None
        self._pulse_lock = threading.RLock()
        self.voice_profile_active = False
        self.on_profile_change = None


    @contextmanager
    def pulse(self):
        """Yield the shared pulsectl connection under a lock.

        The connection is created on first use and reused afterwards; when an
        operation fails with a pulsectl error the connection is dropped so the
        next operation reconnects to the daemon. pulsectl is imported here
        rather than at module scope so an idle process never maps it.
        """
        import pulsectl
        with self._pulse_lock:
            if self._pulse_conn is None or not self._pulse_conn.connected:
                self.close_pulse()
                self._pulse_conn = pulsectl.Pulse('telephony-audio')
            try:
                yield self._pulse_conn
            except pulsectl.PulseError:
                self.close_pulse()
                raise

    def close_pulse(self):
        """Close the shared pulsectl connection if one exists."""
        if self._pulse_conn is None:
            return
        try:
            self._pulse_conn.close()
        except Exception as e:
            logger.debug(f"[Audio] Pulse close error (ignorable): {e}")
        self._pulse_conn = None

    def lookup_sink(self, pulse, name):
        """Return the named sink or None when it is absent."""
        import pulsectl
        try:
            return pulse.get_sink_by_name(name)
        except pulsectl.PulseIndexError:
            logger.debug(f"[Audio] Sink {name} not present")
            return None

    def lookup_source(self, pulse, name):
        """Return the named source or None when it is absent."""
        import pulsectl
        try:
            return pulse.get_source_by_name(name)
        except pulsectl.PulseIndexError:
            logger.debug(f"[Audio] Source {name} not present")
            return None

    def get_audio_backend(self, pulse):
        """Return (backend, card), preferring audio-manager over Droid."""
        cards = pulse.card_list()

        for card in cards:
            if card.name == AUDIO_MANAGER_CARD:
                return "audio-manager", card

        for card in cards:
            if card.name == DROID_CARD:
                return "droid", card

        return None, None

    def get_primary_sink(self, pulse):
        """Return the local hardware sink for the active audio backend."""
        backend, _card = self.get_audio_backend(pulse)
        if backend == "audio-manager":
            return self.lookup_sink(pulse, AUDIO_MANAGER_SINK)
        if backend == "droid":
            return self.lookup_sink(pulse, DROID_SINK)

        info = pulse.server_info()
        return self.lookup_sink(pulse, info.default_sink_name)

    def get_primary_source(self, pulse):
        """Return the local hardware source for the active audio backend."""
        backend, _card = self.get_audio_backend(pulse)
        if backend == "audio-manager":
            return self.lookup_source(pulse, AUDIO_MANAGER_SOURCE)
        if backend == "droid":
            return self.lookup_source(pulse, DROID_SOURCE)

        info = pulse.server_info()
        return self.lookup_source(pulse, info.default_source_name)

    @staticmethod
    def audio_manager_profile_for_route(route):
        """Return the audio-manager cellular profile for a call route."""
        if route == "bluetooth":
            return "cellular-bluetooth"
        if route == "usb":
            return "cellular-usb"
        return "cellular"

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

        if self.ringing_event:
            try:
                self.ringing_event.end_feedback_async(None, None, None)
            except Exception as e:
                logger.warning(f"[Audio] Stop ringing warning: {e}")
            finally:
                self.ringing_event = None

        self.is_ringing = False

    def play_hangup(self, feedback=True):
        """Release ringing; sound the feedback when asked.

        The caller passes feedback=False when this side requested the
        hangup, because the tone announces the other side ending the
        call, while the ringtone cleanup is owed either way.
        """
        self.stop_ringing()

        if feedback and self.lfb_available:
            try:
                Lfb.Event.new("phone-hangup").trigger_feedback_async(None, None, None)
            except Exception as e:
                logger.error(f"[Audio] Play hangup failed: {e}")

    def set_voice_profile(self, enable=True):
        """Enable or disable the call profile for the available audio backend.

        Audio Manager calls always start locally on the earpiece with the
        built-in microphone. The user may switch to speaker, wired,
        Bluetooth, or USB after the call profile is active.
        """
        self.voice_profile_active = enable

        try:
            with self.pulse() as pulse:
                backend, target_card = self.get_audio_backend(pulse)
                if not target_card:
                    logger.warning("[Audio] No supported PulseAudio card found")
                    return

                if backend == "audio-manager":
                    if enable:
                        self.current_route = "earpiece"
                        self.current_input = "mic"
                        pulse.card_profile_set(target_card, "cellular")

                        sink = self.lookup_sink(pulse, AUDIO_MANAGER_SINK)
                        if sink:
                            port_name = self.pick_route_port(sink, "earpiece")
                            if port_name:
                                pulse.sink_port_set(sink.index, port_name)
                            else:
                                logger.warning("[Audio] Audio Manager earpiece port not found")
                        else:
                            logger.warning("[Audio] Audio Manager output sink not found")

                        source = self.lookup_source(pulse, AUDIO_MANAGER_SOURCE)
                        if source:
                            port_name = self.pick_input_port(source, "mic")
                            if port_name:
                                pulse.source_port_set(source.index, port_name)
                            else:
                                logger.warning("[Audio] Audio Manager built-in mic port not found")
                        else:
                            logger.warning("[Audio] Audio Manager input source not found")

                        profile_name = "cellular"
                    else:
                        profile_name = "default"
                        pulse.card_profile_set(target_card, profile_name)
                else:
                    profile_name = "voicecall" if enable else "default"
                    pulse.card_profile_set(target_card, profile_name)

                logger.info(f"[Audio] {backend} card profile set to {profile_name}")

        except Exception as e:
            logger.error(f"[Audio] Set profile failed: {e}")

        if self.on_profile_change:
            try:
                self.on_profile_change()
            except Exception as e:
                logger.error(f"[Audio] Profile change callback failed: {e}")

    def pick_route_port(self, sink, mode):
        """Map a route id to a port exposed by either supported backend."""
        candidates = {
            "earpiece": ("analog-output-earpiece", "output-earpiece"),
            "speaker": ("analog-output-speaker", "output-speaker"),
            "wired": (
                "analog-output-headset",
                "analog-output-headphones",
                "output-wired_headset",
                "output-wired_headphone",
            ),
        }.get(mode, ())

        for name in candidates:
            for port in sink.port_list:
                if port.name == name and self.is_port_available(port):
                    return name
        return None

    def pick_input_port(self, source, mode):
        """Map a local input route to a port exposed by audio-manager."""
        candidates = {
            "mic": ("analog-input-internal-mic", "input-builtin_mic"),
            "wired": ("analog-input-headset-mic", "input-wired_headset"),
        }.get(mode, ())

        for name in candidates:
            for port in source.port_list:
                if port.name == name and self.is_port_available(port):
                    return name
        return None

    @staticmethod
    def is_port_available(port):
        """Treat ports without an availability field as usable."""
        try:
            return port.available != 'no'
        except AttributeError:
            return True

    @staticmethod
    def is_profile_available(card, profile_name):
        """Return whether a named card profile exists and is usable."""
        for profile in card.profile_list:
            if profile.name != profile_name:
                continue
            try:
                return profile.available != 'no'
            except AttributeError:
                return True
        return False

    def set_audio_route(self, mode="earpiece"):
        """Route call output using Audio Manager when present, otherwise Droid.

        Audio Manager Bluetooth and USB call routes are duplex profiles, so
        selecting either route moves both the output and input together. When
        leaving an external route for a local one, restore the local cellular
        profile and built-in microphone first.
        """
        try:
            with self.pulse() as pulse:
                backend, card = self.get_audio_backend(pulse)
                if not card:
                    logger.warning("[Audio] No supported PulseAudio card found")
                    return

                if backend == "audio-manager" and mode in ("bluetooth", "usb"):
                    if not self.voice_profile_active:
                        self.current_route = mode
                        self.current_input = mode
                        return

                    profile_name = self.audio_manager_profile_for_route(mode)
                    if not self.is_profile_available(card, profile_name):
                        logger.warning(f"[Audio] {mode} call route is not available")
                        return

                    # The bridged Bluetooth/USB call profiles are duplex
                    # both directions must describe the same external device.
                    pulse.card_profile_set(card, profile_name)
                    self.current_route = mode
                    self.current_input = mode
                    logger.info(f"[Audio] Call route set to {mode} ({profile_name})")
                    return

                if backend == "audio-manager" and self.voice_profile_active:
                    # Local earpiece/speaker/wired routing lives under the
                    # hostless cellular profile. If we came from Bluetooth or
                    # USB, return the input to the built-in mic as well.
                    was_external = self.current_route in ("bluetooth", "usb") or self.current_input in ("bluetooth", "usb")
                    pulse.card_profile_set(card, "cellular")
                    if was_external:
                        source = self.lookup_source(pulse, AUDIO_MANAGER_SOURCE)
                        if source:
                            input_port = self.pick_input_port(source, "mic")
                            if input_port:
                                pulse.source_port_set(source.index, input_port)
                        self.current_input = "mic"

                sink = self.get_primary_sink(pulse)
                if not sink:
                    logger.warning("[Audio] Primary output sink not found")
                    return

                port_name = self.pick_route_port(sink, mode)
                if not port_name:
                    logger.info(f"[Audio] No port for output route: {mode}")
                    return

                pulse.sink_port_set(sink.index, port_name)
                self.current_route = mode
                logger.info(f"[Audio] Output route set to {mode} ({port_name})")
        except Exception as e:
            logger.error(f"[Audio] Set route failed: {e}")

    def set_input_route(self, mode="mic"):
        """Route call input"""
        try:
            with self.pulse() as pulse:
                backend, card = self.get_audio_backend(pulse)
                if backend != "audio-manager":
                    self.current_input = mode
                    logger.info(f"[Audio] Setting input route: {mode}")
                    return

                if not card:
                    logger.warning("[Audio] No supported PulseAudio card found")
                    return

                if mode in ("bluetooth", "usb"):
                    if not self.voice_profile_active:
                        self.current_input = mode
                        self.current_route = mode
                        return

                    profile_name = self.audio_manager_profile_for_route(mode)
                    if not self.is_profile_available(card, profile_name):
                        logger.warning(f"[Audio] {mode} input route is not available")
                        return

                    # Bluetooth/USB are one duplex call path. Selecting the
                    # input therefore selects the matching output too.
                    pulse.card_profile_set(card, profile_name)
                    self.current_input = mode
                    self.current_route = mode
                    logger.info(f"[Audio] Call input/output set to {mode} ({profile_name})")
                    return

                # A local input cannot coexist with an external duplex call
                # profile. Return to hostless cellular first. If the output was
                # external, use the earpiece as the local output.
                if self.voice_profile_active:
                    was_external = self.current_route in ("bluetooth", "usb") or self.current_input in ("bluetooth", "usb")
                    pulse.card_profile_set(card, "cellular")
                    if was_external:
                        sink = self.lookup_sink(pulse, AUDIO_MANAGER_SINK)
                        if sink:
                            output_port = self.pick_route_port(sink, "earpiece")
                            if output_port:
                                pulse.sink_port_set(sink.index, output_port)
                        self.current_route = "earpiece"

                source = self.get_primary_source(pulse)
                if not source:
                    logger.warning("[Audio] Audio Manager input source not found")
                    return

                port_name = self.pick_input_port(source, mode)
                if not port_name:
                    logger.info(f"[Audio] No port for input route: {mode}")
                    return

                pulse.source_port_set(source.index, port_name)
                self.current_input = mode
                logger.info(f"[Audio] Input route set to {mode} ({port_name})")
        except Exception as e:
            logger.error(f"[Audio] Set input route failed: {e}")

    def initial_call_route(self):
        """Return the route a new call should start on."""
        try:
            with self.pulse() as pulse:
                backend, _card = self.get_audio_backend(pulse)
                if backend == "audio-manager":
                    return "earpiece"

                sink = self.get_primary_sink(pulse)
                if sink and self.pick_route_port(sink, "wired"):
                    return "wired"
        except Exception as e:
            logger.debug(f"[Audio] Initial route probe failed: {e}")
        return "earpiece"

    def get_active_output_route(self):
        """Return the active call output route for either supported backend."""
        name = None
        try:
            with self.pulse() as pulse:
                backend, card = self.get_audio_backend(pulse)
                if backend == "audio-manager" and card and card.profile_active:
                    profile_name = card.profile_active.name
                    if profile_name == "cellular-bluetooth":
                        return "bluetooth"
                    if profile_name == "cellular-usb":
                        return "usb"

                sink = self.get_primary_sink(pulse)
                if sink and sink.port_active:
                    name = sink.port_active.name
        except Exception as e:
            logger.debug(f"[Audio] Active route probe failed: {e}")

        if name in ("analog-output-earpiece", "output-earpiece"):
            return "earpiece"
        if name in ("analog-output-speaker", "output-speaker"):
            return "speaker"
        if name and ("headset" in name or "headphone" in name or "wired" in name):
            return "wired"
        return None

    def set_call_volume_level(self, level):
        """
        Apply the call volume by setting the primary sink volume, which is the
        effective call loudness on this stack; the phone-role stream is only
        the fine trim on top and is left to the user.
        """
        level = max(0.0, min(1.0, level))
        try:
            with self.pulse() as pulse:
                sink = self.get_primary_sink(pulse)
                if not sink:
                    logger.warning("[Audio] Primary output sink not found for call volume")
                    return

                pulse.volume_set_all_chans(sink, level)
                logger.info(f"[Audio] Call volume set to {int(level * 100)}% on {sink.name}")
        except Exception as e:
            logger.error(f"[Audio] Set call volume failed: {e}")

    def ensure_sink_unmuted(self):
        """Clear any mute that module-device-restore re-applied on a port change."""
        try:
            with self.pulse() as pulse:
                sink = self.get_primary_sink(pulse)
                if sink and sink.mute:
                    pulse.sink_mute(sink.index, False)
                    logger.info("[Audio] Cleared restored sink mute")
        except Exception as e:
            logger.error(f"[Audio] Unmute failed: {e}")

    def get_available_outputs(self):
        """Return the output routes with per-route availability."""
        has_bt = False
        has_usb = False
        has_wired = False
        backend = None
        try:
            with self.pulse() as pulse:
                backend, card = self.get_audio_backend(pulse)
                if backend == "audio-manager" and card:
                    has_bt = self.is_profile_available(card, "cellular-bluetooth")
                    has_usb = self.is_profile_available(card, "cellular-usb")
                else:
                    has_bt = any("bluez" in c.name for c in pulse.card_list())

                sink = self.get_primary_sink(pulse)
                if sink:
                    for p in sink.port_list:
                        if "headphone" in p.name or "headset" in p.name:
                            if self.is_port_available(p):
                                has_wired = True
                                break
        except Exception as e:
            logger.warning(f"[Audio] Failed to probe output routes: {e}")

        routes = [
            {"id": "earpiece", "name": "Earpiece", "icon": "phone-symbolic", "available": True},
            {"id": "speaker", "name": "Speaker", "icon": "audio-speakers-symbolic", "available": True},
            {"id": "wired", "name": "Wired Headset", "icon": "audio-headset-symbolic", "available": has_wired},
            {"id": "bluetooth", "name": "Bluetooth", "icon": "bluetooth-active-symbolic", "available": has_bt},
        ]
        if backend == "audio-manager":
            routes.append(
                {"id": "usb", "name": "USB Headset", "icon": "audio-headset-symbolic", "available": has_usb}
            )
        return routes

    def get_available_inputs(self):
        """Return input routes with availability for the active backend."""
        has_bt = False
        has_usb = False
        has_wired = False
        backend = None
        try:
            with self.pulse() as pulse:
                backend, card = self.get_audio_backend(pulse)
                if backend == "audio-manager" and card:
                    has_bt = self.is_profile_available(card, "cellular-bluetooth")
                    has_usb = self.is_profile_available(card, "cellular-usb")
                else:
                    has_bt = any("bluez" in c.name for c in pulse.card_list())

                source = self.get_primary_source(pulse)
                if source:
                    for port in source.port_list:
                        if ("headset" in port.name or "wired" in port.name) and self.is_port_available(port):
                            has_wired = True
                            break
        except Exception as e:
            logger.warning(f"[Audio] Failed to probe input routes: {e}")

        routes = [
            {"id": "mic", "name": "Microphone", "icon": "audio-input-microphone-symbolic", "available": True},
            {"id": "wired", "name": "Wired Mic", "icon": "audio-headset-symbolic", "available": has_wired},
            {"id": "bluetooth", "name": "Bluetooth Mic", "icon": "bluetooth-active-symbolic", "available": has_bt},
        ]
        if backend == "audio-manager":
            routes.append(
                {"id": "usb", "name": "USB Microphone", "icon": "audio-input-microphone-symbolic", "available": has_usb}
            )
        return routes

    def get_call_sink(self, pulse, preferred_name=None):
        """Return the local sink used for media and hostless call volume."""
        if preferred_name:
            sink = self.lookup_sink(pulse, preferred_name)
            if sink:
                return sink

        sink = self.get_primary_sink(pulse)
        if sink:
            return sink

        info = pulse.server_info()
        return self.lookup_sink(pulse, info.default_sink_name)

    def save_media_state(self):
        """Snapshot the media volume and active port once before a call."""
        if self._pre_call_vol is not None:
            return
        try:
            with self.pulse() as pulse:
                sink = self.get_call_sink(pulse)
                if not sink:
                    logger.warning("[Audio] No sink found to save media state")
                    return

                self._pre_call_sink_name = sink.name
                self._pre_call_vol = max(sink.volume.values) if sink.volume.values else 1.0
                self._pre_call_port = sink.port_active.name if sink.port_active else None
                logger.info(f"[Audio] Saved media state: {int(self._pre_call_vol * 100)}% on {self._pre_call_port}")
        except Exception as e:
            logger.error(f"[Audio] Save media state failed: {e}")

    def pick_media_port(self, sink):
        """Choose a non-earpiece output port when restoring media audio."""
        usable = [p.name for p in sink.port_list if self.is_port_available(p)]

        blocked = {
            "output-parking",
            "output-earpiece",
            "analog-output-earpiece",
        }
        if (
            self._pre_call_port
            and self._pre_call_port not in blocked
            and self._pre_call_port in usable
        ):
            return self._pre_call_port

        for name in (
            "analog-output-headphones",
            "analog-output-headset",
            "output-wired_headphone",
            "output-wired_headset",
            "analog-output-speaker",
            "output-speaker",
        ):
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
            with self.pulse() as pulse:
                sink = self.get_call_sink(pulse, self._pre_call_sink_name)
                if sink:
                    target_port = self.pick_media_port(sink)
                    if target_port:
                        try:
                            pulse.sink_port_set(sink.index, target_port)
                        except Exception as e:
                            logger.debug(f"[Audio] Media port restore failed: {e}")

                    pulse.volume_set_all_chans(sink, self._pre_call_vol)
                    logger.info(f"[Audio] Restored media audio on {sink.name} port {target_port}")
                    self._pre_call_vol = None
                    self._pre_call_sink_name = None
                    self._pre_call_port = None
                else:
                    logger.warning("[Audio] Could not find sink to restore call volume")
        except Exception as e:
            logger.error(f"[Audio] Restore call volume failed: {e}")

    def mute(self, muted=True):
        """Mute or unmute the default source."""
        if self._last_mute_state == muted:
            return

        try:
            with self.pulse() as pulse:
                source = self.get_primary_source(pulse)
                if source:
                    pulse.source_mute(source.index, muted)
                    self._last_mute_state = muted
                    self.mic_muted = muted
                    logger.info(f"[Audio] Microphone mute set to: {muted}")
                else:
                    logger.warning("[Audio] Primary input source not found")

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

                with self.pulse() as pulse:
                    target_sink_name = self._pre_max_sink_name
                    sink = None
                    if target_sink_name:
                        try:
                            sink = self.lookup_sink(pulse, target_sink_name)
                        except Exception:
                            logger.warning(f"[Audio] Saved sink {target_sink_name} not found, trying default.")

                    if not sink:
                        info = pulse.server_info()
                        sink = self.lookup_sink(pulse, info.default_sink_name)

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
            with self.pulse() as pulse:
                info = pulse.server_info()
                sink = self.lookup_sink(pulse, info.default_sink_name)

                if sink:
                    if self._pre_max_sink_name is None:
                        self._pre_max_sink_name = sink.name

                    if self._pre_max_mute is None:
                        self._pre_max_mute = sink.mute

                    if self._pre_max_vol is None:
                        if sink.volume.values:
                            self._pre_max_vol = max(sink.volume.values)
                        else:
                            self._pre_max_vol = FALLBACK_MEDIA_VOLUME

                    pulse.sink_mute(sink.index, False)
                    pulse.volume_set_all_chans(sink, 1.0)
                    logger.info(f"[Audio] Forced MAX volume on sink: {sink.name}")
                else:
                    logger.warning("[Audio] Default sink not found for max feedback")
        except Exception as e:
            logger.error(f"[Audio] Force max feedback pulse error: {e}")
