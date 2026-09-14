# Copyright (C) 2026 Bardia Moshiri <bardia@furilabs.com>
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

from gi.repository import Gio, GLib

from telephony.shared.utils.log_utils import logger


BUS_NAME = "org.mobian_project.CallAudio"
OBJECT_PATH = "/org/mobian_project/CallAudio"
CALL_AUDIO_INTERFACE = "org.mobian_project.CallAudio"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

AUDIO_MODE_DEFAULT = 0
AUDIO_MODE_CALL = 1
AUDIO_MODE_UNKNOWN = 255

AUDIO_STATE_OFF = 0
AUDIO_STATE_ON = 1
AUDIO_STATE_UNKNOWN = 255


CALL_AUDIO_XML = """
<node>
  <interface name="org.mobian_project.CallAudio">
    <method name="SelectMode">
      <arg direction="in" name="mode" type="u"/>
      <arg direction="out" name="success" type="b"/>
    </method>
    <property name="AudioMode" type="u" access="read"/>
    <method name="EnableSpeaker">
      <arg direction="in" name="enable" type="b"/>
      <arg direction="out" name="success" type="b"/>
    </method>
    <property name="SpeakerState" type="u" access="read"/>
    <method name="MuteMic">
      <arg direction="in" name="mute" type="b"/>
      <arg direction="out" name="success" type="b"/>
    </method>
    <property name="MicState" type="u" access="read"/>
  </interface>
</node>
"""


class CallAudiodDBusService:
    """Expose Telephony call audio through callaudiod D-Bus API"""

    def __init__(self, ofono, call_audio):
        self.ofono = ofono
        self.call_audio = call_audio
        self.audio = ofono.audio
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        self.interface_info = Gio.DBusNodeInfo.new_for_xml(CALL_AUDIO_XML).interfaces[0]

        self.registration_id = 0
        self.name_owner_id = 0
        self.audio_signal_id = 0
        self.property_snapshot = self.audio_properties()

        self.register_object()
        self.connect_sources()

        self.name_owner_id = Gio.bus_own_name_on_connection(self.bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, self.on_name_acquired, self.on_name_lost)

    def register_object(self):
        """Export org.mobian_project.CallAudio."""
        self.registration_id = self.bus.register_object(OBJECT_PATH, self.interface_info, self.handle_method_call, self.get_property, None)
        if not self.registration_id:
            raise RuntimeError(f"Failed to register {CALL_AUDIO_INTERFACE}")

        logger.info(f"[CallAudiodDBus] Registered interface at {OBJECT_PATH}")

    def connect_sources(self):
        """Follow applied audio state changes."""
        self.audio_signal_id = self.call_audio.connect("audio-state-applied", self.on_audio_state_applied)

    def on_name_acquired(self, connection, name):
        logger.info(f"[CallAudiodDBus] Acquired bus name {name}")

    def on_name_lost(self, connection, name):
        logger.error(f"[CallAudiodDBus] Could not own {name}; another call audio provider may already be running")

    def audio_mode(self):
        """Return the current call audio mode."""
        return AUDIO_MODE_CALL if self.call_audio.voice_profile_active else AUDIO_MODE_DEFAULT

    def speaker_state(self):
        """Return the current speaker state."""
        if not self.audio.current_route:
            return AUDIO_STATE_UNKNOWN
        return AUDIO_STATE_ON if self.audio.current_route == "speaker" else AUDIO_STATE_OFF

    def mic_state(self):
        """Return the current microphone mute state."""
        return AUDIO_STATE_ON if self.audio.mic_muted else AUDIO_STATE_OFF

    def audio_properties(self):
        """Return the current call audio properties."""
        return {
            "AudioMode": self.audio_mode(),
            "SpeakerState": self.speaker_state(),
            "MicState": self.mic_state(),
        }

    def get_property(self, connection, sender, object_path, interface_name, property_name):
        """Handle D-Bus property reads."""
        if interface_name != CALL_AUDIO_INTERFACE:
            return None

        properties = self.audio_properties()
        if property_name not in properties:
            return None

        return GLib.Variant("u", properties[property_name])

    def refresh_properties(self):
        """Emit property changes after audio state changes."""
        current = self.audio_properties()
        changed = {name: value for name, value in current.items() if self.property_snapshot.get(name) != value}
        self.property_snapshot = current

        if not changed:
            return

        changed_variants = {name: GLib.Variant("u", value) for name, value in changed.items()}
        self.bus.emit_signal(None, OBJECT_PATH, PROPERTIES_INTERFACE, "PropertiesChanged", GLib.Variant("(sa{sv}as)", (CALL_AUDIO_INTERFACE, changed_variants, [])))

    def on_audio_state_applied(self, *args):
        """Publish applied call-audio state changes over D-Bus."""
        self.refresh_properties()

    def handle_method_call(self, connection, sender, object_path, interface_name, method_name, parameters, invocation):
        """Implement org.mobian_project.CallAudio."""
        if interface_name != CALL_AUDIO_INTERFACE:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownInterface", f"Unknown interface {interface_name}")
            return

        if method_name == "SelectMode":
            mode = int(parameters.unpack()[0])
            if mode not in (AUDIO_MODE_DEFAULT, AUDIO_MODE_CALL):
                invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", "Mode must be 0 or 1")
                return

            logger.debug(f"[CallAudiodDBus] SelectMode call is a stub (mode={mode})")
            invocation.return_value(GLib.Variant("(b)", (True,)))
            return

        if method_name == "EnableSpeaker":
            enable = bool(parameters.unpack()[0])
            try:
                self.call_audio.set_route("speaker" if enable else "earpiece")
                self.refresh_properties()
                invocation.return_value(GLib.Variant("(b)", (True,)))
            except Exception as e:
                logger.error(f"[CallAudiodDBus] Failed to set speaker state: {e}")
                invocation.return_value(GLib.Variant("(b)", (False,)))
            return

        if method_name == "MuteMic":
            mute = bool(parameters.unpack()[0])
            try:
                self.call_audio.set_mic_muted(mute)
                self.refresh_properties()
                invocation.return_value(GLib.Variant("(b)", (True,)))
            except Exception as e:
                logger.error(f"[CallAudiodDBus] Failed to set microphone mute state: {e}")
                invocation.return_value(GLib.Variant("(b)", (False,)))
            return

        invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", f"Method {method_name} is not implemented")

    def close(self):
        """Unregister the service."""
        if self.audio_signal_id:
            try:
                self.call_audio.disconnect(self.audio_signal_id)
            except Exception as e:
                logger.debug(f"[CallAudiodDBus] Failed to disconnect audio signal: {e}")
            self.audio_signal_id = 0

        if self.registration_id:
            try:
                self.bus.unregister_object(self.registration_id)
            except Exception as e:
                logger.debug(f"[CallAudiodDBus] Failed to unregister object: {e}")
            self.registration_id = 0

        if self.name_owner_id:
            try:
                Gio.bus_unown_name(self.name_owner_id)
            except Exception as e:
                logger.debug(f"[CallAudiodDBus] Failed to release {BUS_NAME}: {e}")
            self.name_owner_id = 0
