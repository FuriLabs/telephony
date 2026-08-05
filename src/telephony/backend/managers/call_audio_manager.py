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

from gi.repository import GLib, GObject

from telephony.backend.utils.log_utils import logger
from telephony.backend.utils.phone_utils import normalize_number
from telephony.backend.utils.thread_utils import run_in_background
from telephony.constants import CALL_VOLUME_MIN_PERCENT, CALL_VOLUME_MAX_PERCENT, CALL_VOLUME_DEFAULT_PERCENT

EXTERNAL_ROUTE_POLL_SECONDS = 1


class CallAudioManager(GObject.Object):
    """Drives call audio from call state: ring, profile, route, volume.

    The audio device is one shared resource, so the owner of the calls
    decides what plays. The state machine is the call window's old
    one, moved here unchanged; the window now sends intents and
    renders the AudioRouteChanged broadcasts. Every applied change
    raises audio-state-applied so the D-Bus service can broadcast.
    """

    __gsignals__ = {
        'audio-state-applied': (GObject.SignalFlags.RUN_FIRST, None, ()),
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

        self.ofono.connect('call-added', self._on_calls_changed)
        self.ofono.connect('call-changed', self._on_calls_changed)
        self.ofono.connect('call-removed', self._on_calls_changed)
        self.gsettings_mgr.gsettings.connect(
            "changed::call-volume-levels", self._on_volume_levels_changed)

    def _on_calls_changed(self, *_args):
        self._refresh()

    def _refresh(self):
        """Steer ring and profile to match what the calls are doing."""
        calls = self.ofono.active_calls
        if not calls:
            self._teardown()
            return

        engaged = [d for d in calls.values() if d.get('state') != 'incoming']
        if engaged:
            self._stop_ring()
            self._engage()
            return

        if len(calls) == 1:
            path, data = next(iter(calls.items()))
            self._ring_for(path, data)

    def _ring_for(self, path, data):
        """Ring one incoming call unless it is silenced.

        The priority-caller volume boost is not repeated here: the
        modem manager already forces and restores it around the call.
        """
        if data.get('silenced', False) or self._ring_suppressed:
            self._stop_ring()
            return
        if self._ringing:
            return
        self.audio.start_ringing(custom_path=self._custom_ringtone(data.get('number', '')))
        self._ringing = True

    def _custom_ringtone(self, number):
        """Return the caller's custom ringtone path, or None."""
        tones = self.gsettings_mgr.get_notification_override_call_custom_contacts()
        norm = normalize_number(number)
        for t in tones:
            if normalize_number(t.get("number", "")) == norm:
                path = t.get("path")
                if path and os.path.exists(path):
                    return path
        return None

    def _engage(self):
        """Bring up the voice profile, route and volume for a live call."""
        if not self._profile_on:
            self.audio.save_media_state()
            self.audio.set_voice_profile(True)
            self._profile_on = True
        if not self._volume_applied:
            self._volume_applied = True
            self.audio.set_audio_route(self.audio.initial_call_route())
            self.audio.ensure_sink_unmuted()
            self._push_route_volume()
        self.audio.mute(self.audio.mic_muted)
        if not self._route_poll_id:
            self._route_poll_id = GLib.timeout_add_seconds(
                EXTERNAL_ROUTE_POLL_SECONDS, self._poll_external_route)
        self.emit('audio-state-applied')

    def _poll_external_route(self):
        """Adopt port changes made outside the app, like a headset plug.

        PulseAudio moves the port itself when hardware appears, so this
        follows instead of fighting it: the route name is adopted and
        its configured volume applied.
        """
        if self._route_poll_busy:
            return GLib.SOURCE_CONTINUE

        self._route_poll_busy = True

        def done(route):
            self._route_poll_busy = False
            if not self._volume_applied or not route or route == self.audio.current_route:
                return
            logger.info(f"[CallAudio] Output route moved externally to {route}")
            self.audio.current_route = route
            self.audio.ensure_sink_unmuted()
            self._push_route_volume()
            self.emit('audio-state-applied')

        def failed(error):
            self._route_poll_busy = False
            logger.debug(f"[CallAudio] Route poll failed: {error}")

        run_in_background(self.audio.get_active_output_route, on_complete=done, on_error=failed)
        return GLib.SOURCE_CONTINUE

    def _push_route_volume(self):
        """Apply the current route's configured level to the call sink."""
        levels = self.gsettings_mgr.get_call_volume_levels()
        level = max(CALL_VOLUME_MIN_PERCENT,
                    min(CALL_VOLUME_MAX_PERCENT,
                        levels.get(self.audio.current_route, CALL_VOLUME_DEFAULT_PERCENT))) / 100.0
        self.audio.set_call_volume_level(level)

    def _on_volume_levels_changed(self, _settings, _key):
        """Re-apply the active route's level live when its slider moves."""
        if self._volume_applied:
            self._push_route_volume()

    def set_route(self, route_id):
        """Apply an output route intent from a window."""
        self.audio.set_audio_route(route_id)
        if self._volume_applied:
            self.audio.ensure_sink_unmuted()
            self._push_route_volume()
        self.emit('audio-state-applied')

    def set_input(self, route_id):
        """Apply an input route intent from a window."""
        self.audio.set_input_route(route_id)
        self.emit('audio-state-applied')

    def set_mic_muted(self, muted):
        """Apply a mute intent from a window."""
        self.audio.mute(muted)
        self.emit('audio-state-applied')

    def silence_ring(self):
        """Stop the ringer until the calls change again."""
        self._ring_suppressed = True
        self._stop_ring()

    def _stop_ring(self):
        if self._ringing:
            self.audio.stop_ringing()
            self._ringing = False

    def _teardown(self):
        """Take call audio down after the last call ends."""
        if self._route_poll_id:
            GLib.source_remove(self._route_poll_id)
            self._route_poll_id = 0
        self._ring_suppressed = False
        self._stop_ring()
        if self._profile_on or self._volume_applied:
            self._profile_on = False
            self._volume_applied = False
            self.audio.set_voice_profile(False)
            self.audio.restore_call_volume()
            self.audio.mute(False)
            self.emit('audio-state-applied')
