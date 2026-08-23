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

from dataclasses import dataclass
from gi.repository import GObject, Gio, GLib
from gettext import gettext as _

from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.thread_utils import run_in_background
from telephony.client.services.daemon_client import DaemonClient
from telephony.shared.constants import DAEMON_BUS_NAME

RESEED_DEBOUNCE_MS = 80


@dataclass
class MirrorAudioState:
    """Audio route facts mirrored from the daemon's broadcasts."""

    voice_profile_active: bool = False
    current_route: str = "earpiece"
    current_input: str = "mic"
    mic_muted: bool = False


class OfonoMirror(GObject.Object):
    """Window-side view of the daemon's telephony state.

    Holds no modem connection of its own: state arrives as one
    GetTelephonyState seed plus the daemon's broadcasts, and every
    action is a request to the daemon. The signal surface matches
    OfonoManager so window code cannot tell the difference, and a
    fresh seed after every daemon (re)appearance makes restarts
    self-healing.
    """

    __gsignals__ = {
        'audio-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'connection-status': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'action-error': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'call-added': (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        'call-removed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'call-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'dial-availability-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        'voicemail-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool, int)),
        'modem-interface-appeared': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'ussd-notification': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'network-service-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, str, object)),
    }

    def __init__(self, gsettings_mgr=None, daemon_client=None):
        super().__init__()
        self.gsettings_mgr = gsettings_mgr
        self.daemon = daemon_client if daemon_client is not None else DaemonClient()
        self.audio = MirrorAudioState()
        self.active_calls = {}
        self.can_dial = False
        self.dial_reason = "starting"
        self.dial_description = ""
        self.modem_present = False
        self.modem_online = False
        self.interfaces = set()
        self.network_emergency_numbers = set()
        self.voicemail_waiting = False
        self.voicemail_count = 0
        self.voicemail_mailbox = ""
        self._reseed_id = 0

        for signal_name, handler in (
                ("IncomingCall", self.on_sig_call_added),
                ("CallChanged", self.on_sig_call_changed),
                ("CallRemoved", self.on_sig_call_removed),
                ("CapabilityChanged", self.on_sig_capability),
                ("ModemStateChanged", self.on_sig_modem_state),
                ("VoicemailChanged", self.on_sig_voicemail),
                ("AudioRouteChanged", self.on_sig_audio),
                ("UssdReceived", self.on_sig_ussd),
                ("NetworkServiceChanged", self.on_sig_network_service)):
            self.daemon.subscribe(signal_name, handler)

        self._watch_id = Gio.bus_watch_name(
            Gio.BusType.SESSION, DAEMON_BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            self.on_owner_appeared, self.on_owner_vanished)

    def on_owner_appeared(self, _connection, _name, _owner):
        """Seed, and re-seed after every daemon restart."""
        self.schedule_reseed()

    def on_owner_vanished(self, _connection, _name):
        """A gone owner cannot place calls; say so until it returns.

        Call state is left alone: calls live in ofonod and may still be
        ringing, and the re-seed on return reconciles what really
        happened.
        """
        self.apply_capability(False, "service-down", _("Telephony service is not running"))

    def schedule_reseed(self):
        """Coalesce state reads; one snapshot answers a burst of events."""
        if self._reseed_id:
            return
        self._reseed_id = GLib.timeout_add(RESEED_DEBOUNCE_MS, self.start_reseed)

    def start_reseed(self):
        self._reseed_id = 0
        run_in_background(self.daemon.get_telephony_state, on_complete=self.apply_state)
        return GLib.SOURCE_REMOVE

    def apply_state(self, state):
        """Rebuild the mirror from one snapshot, telling listeners what moved."""
        if state is None:
            return
        calls = state.get("calls", {})
        old_calls = self.active_calls
        self.active_calls = calls
        for path, props in calls.items():
            if path not in old_calls:
                self.emit('call-added', path, props)
            elif props.get('state') != old_calls[path].get('state'):
                self.emit('call-changed', path, props.get('state', ''))
        for path in old_calls:
            if path not in calls:
                self.emit('call-removed', path)

        self.apply_capability(state.get("can_dial", False),
                               state.get("dial_reason", ""),
                               state.get("dial_description", ""))
        self.apply_modem_state(state.get("modem_present", False),
                                state.get("modem_online", False),
                                state.get("interfaces", []),
                                state.get("emergency_numbers", []))

        waiting = state.get("voicemail_waiting", False)
        count = state.get("voicemail_count", 0)
        if (waiting, count) != (self.voicemail_waiting, self.voicemail_count):
            self.voicemail_waiting = waiting
            self.voicemail_count = count
            self.emit('voicemail-changed', waiting, count)
        self.voicemail_mailbox = state.get("voicemail_mailbox", "")
        self.apply_audio_state(state)

    def apply_capability(self, can_dial, reason, description):
        self.can_dial = can_dial
        self.dial_reason = reason
        self.dial_description = description
        self.audio.voice_profile_active = (reason == "call-ending")
        self.emit('dial-availability-changed', can_dial)

    def apply_modem_state(self, present, online, interfaces, emergency_numbers):
        self.network_emergency_numbers = set(emergency_numbers)
        new_interfaces = set(interfaces)
        added = new_interfaces - self.interfaces
        presence_changed = present != self.modem_present
        self.modem_present = present
        self.modem_online = online
        self.interfaces = new_interfaces
        for iface in sorted(added):
            self.emit('modem-interface-appeared', iface)
        if presence_changed:
            self.emit('connection-status',
                      "connected" if present else "disconnected", "")

    def on_sig_call_added(self, *args):
        self.schedule_reseed()

    def on_sig_call_changed(self, *args):
        path, state = args[5].unpack()
        if path in self.active_calls:
            self.active_calls[path]['state'] = state
        self.emit('call-changed', path, state)
        self.schedule_reseed()

    def on_sig_call_removed(self, *args):
        path = args[5].unpack()[0]
        if self.active_calls.pop(path, None) is not None:
            self.emit('call-removed', path)
        self.schedule_reseed()

    def on_sig_capability(self, *args):
        self.apply_capability(*args[5].unpack())

    def on_sig_modem_state(self, *args):
        state = args[5].unpack()[0]
        self.apply_modem_state(state.get("present", False),
                                state.get("online", False),
                                state.get("interfaces", []),
                                state.get("emergency_numbers", []))

    def on_sig_voicemail(self, *args):
        waiting, count = args[5].unpack()
        self.voicemail_waiting = waiting
        self.voicemail_count = count
        self.emit('voicemail-changed', waiting, count)
        self.schedule_reseed()

    def on_sig_audio(self, *args):
        state = args[5].unpack()[0]
        self.apply_audio_state(state)

    def apply_audio_state(self, state):
        self.audio.current_route = state.get("route") or ("speaker" if state.get("speaker") else "earpiece")
        self.audio.current_input = state.get("input") or "mic"
        self.audio.mic_muted = bool(state.get("mic_muted"))
        self.emit('audio-changed')

    def on_sig_ussd(self, *args):
        self.emit('ussd-notification', args[5].unpack()[0])

    def on_sig_network_service(self, *args):
        service, name, value = args[5].unpack()
        self.emit('network-service-changed', service, name,
                  self.restore_service_value(name, value))

    def is_dialing_available(self):
        """Return the daemon's verdict on placing a new call."""
        return self.can_dial

    def has_modem_interface(self, interface):
        """Return whether the modem currently publishes an interface."""
        return interface in self.interfaces

    def dial(self, number, hide_id=False, on_result=None):
        """Ask the owner to place a call; refusals surface as action-error."""
        self.daemon.dial(number, hide_id, self.on_remote_dial_done)
        return True

    def on_remote_dial_done(self, reply):
        """Surface a dial the daemon refused or never heard."""
        if reply is None:
            self.emit('action-error', _("Telephony service is not running"))
            return
        success, message = reply
        if not success:
            self.emit('action-error', message if message else _("Modem not ready"))

    def answer_call(self, target_path):
        """Answer an incoming call."""
        self.daemon.call_async("Answer", GLib.Variant("(s)", (target_path,)))

    def hangup_call(self, path):
        """Hang up one call."""
        self.daemon.call_async("Hangup", GLib.Variant("(s)", (path,)))

    def hangup_all(self):
        """Hang up every call."""
        self.daemon.call_async("HangupAll", None)

    def swap_calls(self):
        """Swap active and held calls."""
        self.daemon.call_async("SwapCalls", None)

    def send_dtmf(self, tones):
        """Send DTMF tones during a call."""
        self.daemon.call_async("SendDtmf", GLib.Variant("(s)", (tones,)))

    def create_multiparty(self):
        """Merge the active and held calls; blocking, call from a worker."""
        reply = self.daemon.call("CallAction", GLib.Variant("(ss)", ("create_multiparty", "")),
                                 GLib.VariantType("(b)"))
        ok = bool(reply and reply[0])
        return (ok, None if ok else "refused")

    def hangup_multiparty(self):
        """Hang up the conference; blocking, call from a worker."""
        reply = self.daemon.call("CallAction", GLib.Variant("(ss)", ("hangup_multiparty", "")),
                                 GLib.VariantType("(b)"))
        ok = bool(reply and reply[0])
        return (ok, None if ok else "refused")

    def private_chat(self, path):
        """Split one conference participant out; blocking, call from a worker."""
        reply = self.daemon.call("CallAction", GLib.Variant("(ss)", ("private_chat", path)),
                                 GLib.VariantType("(b)"))
        ok = bool(reply and reply[0])
        return (ok, None if ok else "refused")

    def transfer_call(self):
        """Connect active and held calls to each other; blocking, call from a worker."""
        reply = self.daemon.call("CallAction", GLib.Variant("(ss)", ("transfer", "")),
                                 GLib.VariantType("(b)"))
        ok = bool(reply and reply[0])
        return (ok, None if ok else "refused")

    def send_quick_response(self, number, text):
        """Record and send an SMS with delivery tracking."""
        run_in_background(self.daemon.send_tracked_sms, number, text)
        return True

    def send_ussd(self, command):
        """Send a USSD command; blocking, call from a worker."""
        reply = self.daemon.call("SendUssd", GLib.Variant("(s)", (command,)),
                                 GLib.VariantType("(s)"))
        return reply[0] if reply and reply[0] else None

    def set_active_chat(self, number):
        """Tell the owner which chat is open so its alerts stay quiet."""
        self.daemon.set_active_chat(self.normalize_chat_target(number))

    def normalize_chat_target(self, number):
        """Collapse a chat target to the comparable form reception uses."""
        if not number:
            return ""
        if isinstance(number, list):
            return ",".join(sorted(normalize_number(n) for n in number))
        if "," in number:
            return number
        return normalize_number(number)

    def get_service_properties(self, service):
        """Read a supplementary service's properties; blocking, call from a worker."""
        reply = self.daemon.call("GetNetworkProperties", GLib.Variant("(s)", (service,)),
                                 GLib.VariantType("(a{sv})"))
        if reply is None:
            return None
        return {k: self.restore_service_value(k, v) for k, v in reply[0].items()}

    def restore_service_value(self, name, packed):
        """Turn a relayed property back into the type the UI expects."""
        text = packed if isinstance(packed, str) else str(packed)
        if name.endswith("Timeout"):
            try:
                return int(text)
            except ValueError:
                return 0
        return text

    def set_service_property(self, service, name, value):
        """Set a supplementary service property; blocking, call from a worker."""
        return self.ask_network_write(service, name, value, "")

    def set_barring_property(self, name, value, password):
        """Set a call barring rule; blocking, call from a worker."""
        return self.ask_network_write("barring", name, value, password)

    def ask_network_write(self, service, name, value, password):
        """Have the owner change a supplementary service."""
        reply = self.daemon.call(
            "SetNetworkProperty",
            GLib.Variant("(ssvs)", (service, name, GLib.Variant("s", str(value)), password or "")),
            GLib.VariantType("(s)"))
        if reply is None:
            return (False, "no reply")
        error = reply[0]
        return (not error, error or None)

    def change_barring_password(self, old, new):
        """Change the network barring password; blocking, call from a worker."""
        reply = self.daemon.change_barring_password(old, new)
        if reply is None:
            return (False, "no reply")
        return (reply[0], None if reply[0] else (reply[1] or "refused"))

    def disable_all_forwarding(self):
        """Clear every forwarding rule; blocking, call from a worker."""
        reply = self.daemon.disable_all_forwarding()
        if reply is None:
            return (False, "no reply")
        return (reply[0], None if reply[0] else (reply[1] or "refused"))

    def disable_all_barrings(self, password):
        """Clear every barring rule; blocking, call from a worker."""
        reply = self.daemon.disable_all_barrings(password)
        if reply is None:
            return (False, "no reply")
        return (reply[0], None if reply[0] else (reply[1] or "refused"))

    def set_delivery_reports(self, enabled):
        """Ask the network for delivery reports; blocking, call from a worker."""
        reply = self.daemon.set_delivery_reports(enabled)
        if reply is None:
            return (False, "no reply")
        return (reply[0], None if reply[0] else (reply[1] or "refused"))
