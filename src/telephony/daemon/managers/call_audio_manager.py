# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import os

from gi.repository import GLib, GObject

from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.constants import (CALL_VOLUME_MIN_PERCENT, CALL_VOLUME_MAX_PERCENT, CALL_VOLUME_DEFAULT_PERCENT)

EXTERNAL_ROUTE_POLL_SECONDS = 1


class CallAudioManager(GObject.Object):

    __gsignals__ = {
        "audio-state-applied": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, ofono, audio, gsettings_mgr):
        super().__init__()
        self.ofono = ofono
        self.audio = audio
        self.gsettings_mgr = gsettings_mgr

        self._ringing = False
        self._ring_suppressed = False
        self._profile_on = False
        self._volume_applied = False
        self._route_poll_id = 0
        self._route_poll_busy = False

        self.ofono.connect("call-added", self.on_calls_changed)
        self.ofono.connect("call-changed", self.on_calls_changed)
        self.ofono.connect("call-removed", self.on_calls_changed)
        self.gsettings_mgr.gsettings.connect(
            "changed::call-volume-levels", self.on_volume_levels_changed)

    def on_calls_changed(self, *_args):
        self.refresh()

    @property
    def voice_profile_active(self):
        return self._profile_on

    def refresh(self):
        calls = self.ofono.active_calls
        if not calls:
            self.teardown()
            return

        engaged = [
            d for d in calls.values()
            if d.get("state") in ("dialing", "alerting", "active", "held")
            and not d.get("disconnect_reason")
        ]
        if engaged:
            self.stop_ring()
            self.engage()
            return

        ringing = [
            (path, data) for path, data in calls.items()
            if data.get("state") in ("incoming", "waiting")
            and not data.get("disconnect_reason")
        ]
        if len(ringing) == 1:
            path, data = ringing[0]
            self.ring_for(path, data)
            return

        self.teardown()

    def ring_for(self, path, data):
        if data.get("silenced", False) or self._ring_suppressed:
            self.stop_ring()
            return
        if self._ringing:
            return
        self.audio.start_ringing(custom_path=self.custom_ringtone(data.get("number", "")))
        self._ringing = True

    def custom_ringtone(self, number):
        tones = self.gsettings_mgr.get_notification_override_call_custom_contacts()
        norm = normalize_number(number)
        for t in tones:
            if normalize_number(t.get("number", "")) == norm:
                path = t.get("path")
                if path and os.path.exists(path):
                    return path
        return None

    def engage(self):
        if not self._profile_on:
            self.audio.save_media_state()
            self.audio.set_voice_profile(True)
            self._profile_on = True
        if not self._volume_applied:
            self._volume_applied = True
            self.audio.set_audio_route(self.audio.initial_call_route())
            self.audio.ensure_sink_unmuted()
            self.push_route_volume()
        self.audio.mute(self.audio.mic_muted)
        if not self._route_poll_id:
            self._route_poll_id = GLib.timeout_add_seconds(
                EXTERNAL_ROUTE_POLL_SECONDS, self.poll_external_route)
        self.emit("audio-state-applied")

    def poll_external_route(self):
        if self._route_poll_busy:
            return GLib.SOURCE_CONTINUE

        self._route_poll_busy = True
        serial = self.audio.route_serial

        def done(route):
            self._route_poll_busy = False
            if serial != self.audio.route_serial:
                return
            if not self._volume_applied or not route or route == self.audio.current_route:
                return
            logger.info(f"[CallAudio] Output route moved externally to {route}")
            old_route = self.audio.current_route
            self.audio.current_route = route

            if route in ("bluetooth", "usb"):
                self.audio.current_input = route
            elif old_route in ("bluetooth", "usb"):
                self.audio.current_input = "mic"

            self.audio.ensure_sink_unmuted()
            self.push_route_volume()
            self.emit("audio-state-applied")

        def failed(error):
            self._route_poll_busy = False
            logger.debug(f"[CallAudio] Route poll failed: {error}")

        run_in_background(self.audio.get_active_output_route, on_complete=done, on_error=failed)
        return GLib.SOURCE_CONTINUE

    def push_route_volume(self):
        levels = self.gsettings_mgr.get_call_volume_levels()
        level = max(CALL_VOLUME_MIN_PERCENT,
                    min(CALL_VOLUME_MAX_PERCENT,
                        levels.get(self.audio.current_route, CALL_VOLUME_DEFAULT_PERCENT))) / 100.0
        self.audio.set_call_volume_level(level)

    def on_volume_levels_changed(self, _settings, _key):
        if self._volume_applied:
            self.push_route_volume()

    def set_route(self, route_id):
        self.audio.set_audio_route(route_id)
        if self._volume_applied:
            self.audio.ensure_sink_unmuted()
            self.push_route_volume()
        self.emit("audio-state-applied")

    def set_input(self, route_id):
        self.audio.set_input_route(route_id)
        self.emit("audio-state-applied")

    def set_mic_muted(self, muted):
        self.audio.mute(muted)
        self.emit("audio-state-applied")

    def silence_ring(self):
        self._ring_suppressed = True
        self.stop_ring()

    def stop_ring(self):
        if self._ringing:
            self.audio.stop_ringing()
            self._ringing = False

    def teardown(self):
        if self._route_poll_id:
            GLib.source_remove(self._route_poll_id)
            self._route_poll_id = 0
        self._ring_suppressed = False
        self.stop_ring()
        if self._profile_on or self._volume_applied:
            self._profile_on = False
            self._volume_applied = False
            self.audio.set_voice_profile(False)
            self.audio.restore_call_volume()
            self.audio.mute(False)
            self.emit("audio-state-applied")
