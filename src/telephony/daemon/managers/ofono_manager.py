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

import threading
import time
from gettext import gettext as _

from telephony.shared.utils.log_utils import logger
from gi.repository import Gio, GLib, GObject

from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.call_state_utils import count_lines
from telephony.shared.utils.system_utils import restart_ril_modem
from telephony.shared.utils.thread_utils import run_in_background
from telephony.daemon.services.ofono_service import OfonoService
from telephony.daemon.managers.location_manager import LocationManager
from telephony.daemon.managers.audio_manager import TelephonyAudioManager
from telephony.daemon.managers.tmate_manager import TmateManager
from telephony.daemon.managers.device_lock_manager import DeviceLockManager
from telephony.daemon.managers.callback_manager import CallbackManager

from telephony.daemon.managers.relay_manager import RelayManager

SS_REQUEST_TIMEOUT_MS = 90000
REPEATED_CALL_WINDOW_SECONDS = 300
REPEATED_CALL_THRESHOLD = 3
ANSWER_SWAP_DELAY_MS = 500
EMERGENCY_FEEDBACK_RESTORE_SECONDS = 5
SMS_RESOLVE_TIMEOUT_SECONDS = 60
UNCLAIMED_STATE_LIMIT = 20
DELIVERY_WATCH_LIMIT = 50
SEEN_SIGNATURE_LIMIT = 50
PENDING_DIAL_TIMEOUT_SECONDS = 15
PHONEBOOK_IMPORT_TIMEOUT_MS = 100000
VOICEMAIL_UNCONFIGURED_COUNT = 255
OPENSTREETMAP_URL = "https://www.openstreetmap.org/"


REPEATED_MESSAGES_BYPASS_COUNT = 3
REPEATED_MESSAGES_WINDOW_SECONDS = 60

class OfonoManager(GObject.Object):
    """
    Manages voice calls, SMS, and USSD via ofono.
    """
    __gsignals__ = {
        'incoming-message': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'connection-status': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'action-error': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'call-added': (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        'call-removed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'call-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'call-missed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'hangup-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'dial-availability-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        'voicemail-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool, int)),
        'voicemail-mailbox-changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'modem-interface-appeared': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'network-status-changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'sim-pin-required-changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'notification-cleared': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'ussd-notification': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'network-service-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, str, object)),
    }

    def __init__(self, db_manager, gsettings_mgr=None):
        """Initialize the Ofono manager.

        Only the daemon constructs this class: it stores what arrives,
        acts on trusted senders and answers for the modem. Window
        processes hold an OfonoMirror instead, so an app opened twice
        cannot file a message twice or run a text triggered action
        twice.
        """
        super().__init__()
        self.db = db_manager
        self.gsettings_mgr = gsettings_mgr

        self.voice_proxy = None
        self.msg_proxy = None
        self.ussd_proxy = None
        self.cf_proxy = None
        self.cf_handler_id = None
        self.cb_proxy = None
        self.cb_handler_id = None
        self.cs_proxy = None
        self.cs_handler_id = None
        self.network_emergency_numbers = set()
        self.vol_proxy = None
        self.modem_path = None
        self.bus = None

        self.voice_handler_id = None
        self.msg_handler_id = None
        self.ussd_handler_id = None
        self.mw_proxy = None
        self.mw_handler_id = None
        self.modem_proxy = None
        self.modem_handler_id = None
        self._seen_interfaces = set()
        self._interfaces_known = False
        self.modem_online = None
        self._pending_dial = None
        self._pending_dial_timeout_id = 0
        self.clir_hidden = False
        self._dial_hides_id = False
        self.message_history_tracker = {}

        self.netreg_proxy = None
        self.netreg_handler_id = None
        self.simmgr_proxy = None
        self.simmgr_handler_id = None
        self.network_status = ""
        self.pin_required = ""
        self.voicemail_waiting = False
        self.voicemail_count = 0
        self.voicemail_mailbox = ""

        self.active_calls = {}
        self.active_chat_number = None
        self.focus_provider = None

        self.seen_sms_signatures = []
        self.last_sent_sms = None
        self.trusted_trigger_history = {}

        self.inflight_sms = {}
        self.inflight_sms_paths = {}
        self.unclaimed_sms_states = {}
        self.delivery_watch = {}
        self._sms_history_sub = None
        self.send_lock = threading.Lock()
        self._sms_state_sub = None

        self.call_history_tracker = {}
        self.is_volume_boosted = False

        self.monitor = OfonoService()
        self.monitor.connect('status-changed', self._on_monitor_status)
        self.monitor.connect('modem-ready', self._on_modem_ready)

        self.location_manager = LocationManager()
        self.audio = TelephonyAudioManager()
        self.audio.on_profile_change = self.notify_dial_availability
        self.tmate_manager = TmateManager(self)
        self.device_lock_manager = DeviceLockManager()
        self.callback_manager = CallbackManager(self)
        self.relay_manager = RelayManager(self)

    def voicemail_number(self):
        """Return the user-set mailbox number, falling back to the SIM's."""
        custom = self.gsettings_mgr.get_setting("voicemail_number") if self.gsettings_mgr else ""
        return custom or self.voicemail_mailbox

    def _apply_voicemail_props(self, props):
        """Apply MessageWaiting properties and announce changes.

        A message count of 255 is the carrier standard for a mailbox
        that is not configured, so it is stored as no voicemail at all.
        """
        waiting = self.voicemail_waiting
        count = self.voicemail_count
        if 'VoicemailWaiting' in props:
            waiting = bool(props['VoicemailWaiting'])
        if 'VoicemailMessageCount' in props:
            count = int(props['VoicemailMessageCount'])
        if count == VOICEMAIL_UNCONFIGURED_COUNT:
            logger.debug("[OfonoManager] Voicemail mailbox not configured, hiding voicemail")
            waiting = False
            count = 0
        if waiting != self.voicemail_waiting or count != self.voicemail_count:
            self.voicemail_waiting = waiting
            self.voicemail_count = count
            GLib.idle_add(self.emit, 'voicemail-changed', self.voicemail_waiting, self.voicemail_count)
        if 'VoicemailMailboxNumber' in props and str(props['VoicemailMailboxNumber']) != self.voicemail_mailbox:
            self.voicemail_mailbox = str(props['VoicemailMailboxNumber'])
            GLib.idle_add(self.emit, 'voicemail-mailbox-changed', self.voicemail_mailbox)

    def on_message_waiting_signal(self, proxy, sender, signal, params):
        """Handle MessageWaiting property changes."""
        if signal != "PropertyChanged":
            return
        try:
            name, value = params.unpack()
        except Exception as e:
            logger.debug(f"[OfonoManager] MessageWaiting unpack failed: {e}")
            return
        self._apply_voicemail_props({name: value})

    def on_modem_signal(self, proxy, sender, signal, params):
        """Watch modem property changes for interfaces that appear late.

        ofono exports MessageWaiting and NetworkRegistration only once the
        SIM is ready, which can be well after modem-ready. A read attempted
        before that fails with UnknownMethod and nothing re-announces the
        already-set state, so newly appearing interfaces trigger a re-read.
        """
        if signal != "PropertyChanged":
            return
        try:
            name, value = params.unpack()
        except Exception as e:
            logger.debug(f"[OfonoManager] Modem property unpack failed: {e}")
            return
        if name == "Online":
            self._set_modem_online(value)
            return
        if name != "Interfaces":
            return
        self._apply_modem_interfaces(value)

    def _set_modem_online(self, online):
        """Track the radio state; False while flight mode is enabled."""
        online = bool(online)
        if online == self.modem_online:
            return
        self.modem_online = online
        logger.info(f"[OfonoManager] Modem online: {online}")
        self.notify_dial_availability()

    def _apply_modem_interfaces(self, interfaces):
        """Fold the modem interface list into state and react to changes."""
        current = set(interfaces)
        added = current - self._seen_interfaces
        removed = self._seen_interfaces - current
        self._seen_interfaces = current
        self._interfaces_known = True

        for interface in added:
            logger.info(f"[OfonoManager] Modem interface appeared: {interface}")
            GLib.idle_add(self.emit, 'modem-interface-appeared', interface)
        for interface in removed:
            logger.warning(f"[OfonoManager] Modem interface vanished: {interface}")

        if "org.ofono.MessageWaiting" in added:
            self._load_voicemail_state()
        if "org.ofono.NetworkRegistration" in added:
            self._attach_netreg()
        if "org.ofono.NetworkRegistration" in removed:
            self._set_network_status("")
        if "org.ofono.SimManager" in added:
            self._attach_simmgr()

        if added or removed:
            self.notify_dial_availability()

    def _attach_netreg(self):
        """Follow network registration once ofono exports the interface."""
        if self.netreg_proxy:
            return
        self.netreg_proxy = self._get_proxy("org.ofono.NetworkRegistration")
        if not self.netreg_proxy:
            return
        self.netreg_handler_id = self.netreg_proxy.connect("g-signal", self.on_netreg_signal)
        self._load_service_property(self.netreg_proxy, "Status", self._set_network_status)

    def _attach_simmgr(self):
        """Follow the SIM lock state once ofono exports the interface."""
        if self.simmgr_proxy:
            return
        self.simmgr_proxy = self._get_proxy("org.ofono.SimManager")
        if not self.simmgr_proxy:
            return
        self.simmgr_handler_id = self.simmgr_proxy.connect("g-signal", self.on_simmgr_signal)
        self._load_service_property(self.simmgr_proxy, "PinRequired", self._set_pin_required)

    def _load_service_property(self, proxy, name, setter):
        """Read one property off the main thread and feed it to its setter."""
        def fetch():
            ret = proxy.call_sync("GetProperties", None, Gio.DBusCallFlags.NONE, -1, None)
            return ret.unpack()[0].get(name)

        def apply(value):
            if value is not None:
                setter(value)

        run_in_background(fetch, on_complete=apply,
                          on_error=self._modem_went_away(f"property {name}"))

    @staticmethod
    def _modem_went_away(what):
        """Return a handler that shrugs when the modem answers no more.

        A property read races the modem disappearing, and losing that
        race is how a modem goes away rather than a fault of its own.
        Reporting it as an unhandled failure said nothing about which
        read it was, and every one of them is asked again when the
        interface comes back.
        """
        def handler(error):
            logger.info(f"[OfonoManager] {what} not read, the modem is gone: {error}")
        return handler

    def on_netreg_signal(self, proxy, sender, signal, params):
        """Handle NetworkRegistration property changes."""
        if signal != "PropertyChanged":
            return
        try:
            name, value = params.unpack()
        except Exception as e:
            logger.debug(f"[OfonoManager] NetworkRegistration unpack failed: {e}")
            return
        if name == "Status":
            self._set_network_status(value)

    def on_simmgr_signal(self, proxy, sender, signal, params):
        """Handle SimManager property changes."""
        if signal != "PropertyChanged":
            return
        try:
            name, value = params.unpack()
        except Exception as e:
            logger.debug(f"[OfonoManager] SimManager unpack failed: {e}")
            return
        if name == "PinRequired":
            self._set_pin_required(value)

    def _set_network_status(self, status):
        """Track the registration status and announce changes."""
        status = str(status)
        if status == self.network_status:
            return
        self.network_status = status
        logger.info(f"[OfonoManager] Network status: {status or 'unknown'}")
        GLib.idle_add(self.emit, 'network-status-changed', status)

    def _set_pin_required(self, pin_type):
        """Track the SIM lock requirement and announce changes."""
        pin_type = str(pin_type)
        if pin_type == self.pin_required:
            return
        self.pin_required = pin_type
        logger.info(f"[OfonoManager] SIM pin required: {pin_type or 'none'}")
        GLib.idle_add(self.emit, 'sim-pin-required-changed', pin_type)

    def register_network(self):
        """Ask ofono to retry network registration; blocking, worker."""
        if not self.netreg_proxy:
            return
        try:
            self.netreg_proxy.call_sync("Register", None, Gio.DBusCallFlags.NONE, 30000, None)
            logger.info("[OfonoManager] Network registration nudge sent")
        except Exception as e:
            logger.warning(f"[OfonoManager] Network registration nudge failed: {e}")

    def _load_modem_interfaces(self):
        """Seed the interface list and radio state at modem-ready.

        Unlike the SIM-dependent interfaces, org.ofono.Modem exists for as
        long as the modem object does, so this read is reliable.
        """
        proxy = self.modem_proxy
        if not proxy:
            return

        def fetch():
            ret = proxy.call_sync("GetProperties", None, Gio.DBusCallFlags.NONE, -1, None)
            return ret.unpack()[0]

        def apply_seed(props):
            if not self._interfaces_known:
                self._apply_modem_interfaces(props.get("Interfaces", []))
            online = props.get("Online")
            if online is not None and self.modem_online is None:
                self._set_modem_online(online)

        run_in_background(fetch, on_complete=apply_seed,
                          on_error=self._modem_went_away("the modem properties"))

    def _load_voicemail_state(self):
        """Fetch the initial MessageWaiting properties off the main thread."""
        proxy = self.mw_proxy
        if not proxy:
            return

        def fetch():
            ret = proxy.call_sync("GetProperties", None, Gio.DBusCallFlags.NONE, -1, None)
            return ret.unpack()[0]

        run_in_background(fetch, on_complete=self._apply_voicemail_props,
                          on_error=self._modem_went_away("the voicemail state"))

    def dialing_available(self):
        """Return True when a new outgoing call can be placed right now.

        Until the first interface read lands the state is unknown and stays
        optimistic, so a slow seed never locks dialing out. Once known it is
        authoritative — including the empty list a powered-down modem
        publishes — and catches the half-dead modem whose object exists but
        whose VoiceCallManager has vanished.
        """
        if not self.voice_proxy or self.active_calls or self.audio.voice_profile_active:
            return False
        if self._interfaces_known and "org.ofono.VoiceCallManager" not in self._seen_interfaces:
            return False
        if self.modem_online is False:
            return False
        return True

    def notify_dial_availability(self):
        """Announce the current dial availability on the main loop."""
        GLib.idle_add(self.emit, 'dial-availability-changed', self.dialing_available())

    def voice_interface_missing(self):
        """Return True when the modem is known to lack VoiceCallManager."""
        return self._interfaces_known and "org.ofono.VoiceCallManager" not in self._seen_interfaces

    def modem_health_degraded(self):
        """Return True when no modem exists at all or its voice interface is gone.

        The no-modem case covers the unclean-shutdown boot where the RIL
        daemon never starts: ofono runs but never reports a modem, so
        there is no interface list to watch.
        """
        return not self.monitor.connected or self.voice_interface_missing()

    def set_active_chat(self, number):
        """Set the currently active chat to suppress notifications.

        Suppression decisions are made where messages are received, so a
        window forwards the target to the daemon instead of keeping a
        flag no reception path ever reads.
        """
        target = self._normalize_chat_target(number)
        self.active_chat_number = target if target else None

    def _normalize_chat_target(self, number):
        """Collapse a chat target to the comparable form reception uses."""
        if not number:
            return ""
        if isinstance(number, list):
            return ",".join(sorted(normalize_number(n) for n in number))
        if "," in number:
            return number
        return normalize_number(number)

    def capability_state(self):
        """Return (can_dial, reason, description) explaining dial availability.

        The reason is a stable machine-readable code for other clients of
        the daemon interface; the description is translated here because
        the daemon runs in the user's session locale.
        """
        if not self.monitor.connected:
            return (False, "no-modem", _("Modem not ready"))
        if self.modem_online is False:
            return (False, "airplane-mode", _("Airplane mode is on"))
        if self.voice_interface_missing():
            return (False, "no-voice-service", _("Modem not ready"))
        if self.active_calls:
            return (False, "in-call", _("Cannot dial while in another call"))
        if self.audio.voice_profile_active:
            return (False, "call-ending", _("Please wait, the previous call is still ending"))
        if not self.voice_proxy:
            return (False, "starting", _("Modem not ready"))
        return (True, "", "")

    def modem_state(self):
        """Return the modem presence snapshot for the state broadcasts."""
        return {
            "present": bool(self.monitor.connected),
            "online": bool(self.modem_online),
            "interfaces": sorted(self._seen_interfaces),
            "emergency_numbers": sorted(self.network_emergency_numbers),
        }

    def calls_snapshot(self):
        """Return the active calls as plain serializable dicts."""
        snapshot = {}
        for path, props in self.active_calls.items():
            snapshot[path] = {k: v for k, v in props.items()
                              if isinstance(v, (str, bool, int, float))}
        return snapshot

    def import_sim_contacts(self):
        """Read the SIM phonebook as vcard text; blocking, call from a worker.

        Returns None when the phonebook cannot be read so an empty SIM
        stays distinguishable from a failure.
        """
        if not self.modem_path or not self.bus:
            return None
        try:
            res = self.bus.call_sync(
                "org.ofono", self.modem_path, "org.ofono.Phonebook", "Import",
                None, None, Gio.DBusCallFlags.NONE, PHONEBOOK_IMPORT_TIMEOUT_MS, None)
            return res.unpack()[0]
        except Exception as e:
            logger.error(f"[OfonoManager] SIM phonebook import failed: {e}")
            return None

    def set_focus_provider(self, provider):
        """Set a callable reporting whether an application window is focused."""
        self.focus_provider = provider

    def is_app_focused(self):
        """Return True when the focus provider reports an active window."""
        try:
            return bool(self.focus_provider and self.focus_provider())
        except Exception as e:
            logger.debug(f"[OfonoManager] Focus provider error: {e}")
            return False

    def _on_monitor_status(self, monitor, status, msg):
        """Handle monitor status changes."""
        if status == "offline" or status == "error":
            if len(self.active_calls) > 0:
                logger.error("[OfonoManager] Modem lost during active call! Triggering RIL restart.")
                run_in_background(restart_ril_modem)

            self._cleanup_state()

        self.emit('connection-status', status, msg)
        self.notify_dial_availability()

    def _cleanup_state(self):
        """Clean up internal state and disconnect signals."""
        if self.active_calls:
            logger.warning("[OfonoManager] Modem lost. Clearing active calls.")
            for path in list(self.active_calls.keys()):
                self._force_remove(path)

        if self.voice_proxy and self.voice_handler_id:
            try:
                self.voice_proxy.disconnect(self.voice_handler_id)
            except Exception as e:
                logger.warning(f"[OfonoManager] Voice disconnect warning: {e}")
        if self.msg_proxy and self.msg_handler_id:
            try:
                self.msg_proxy.disconnect(self.msg_handler_id)
            except Exception as e:
                logger.warning(f"[OfonoManager] Msg disconnect warning: {e}")
        if self.ussd_proxy and self.ussd_handler_id:
            try:
                self.ussd_proxy.disconnect(self.ussd_handler_id)
            except Exception as e:
                logger.warning(f"[OfonoManager] USSD disconnect warning: {e}")

        if self.mw_proxy and self.mw_handler_id:
            try:
                self.mw_proxy.disconnect(self.mw_handler_id)
            except Exception as e:
                logger.warning(f"[OfonoManager] MessageWaiting disconnect warning: {e}")
        if self.modem_proxy and self.modem_handler_id:
            try:
                self.modem_proxy.disconnect(self.modem_handler_id)
            except Exception as e:
                logger.warning(f"[OfonoManager] Modem disconnect warning: {e}")
        for proxy, handler_id, label in ((self.netreg_proxy, self.netreg_handler_id, "NetworkRegistration"),
                                         (self.simmgr_proxy, self.simmgr_handler_id, "SimManager"),
                                         (self.cf_proxy, self.cf_handler_id, "CallForwarding"),
                                         (self.cb_proxy, self.cb_handler_id, "CallBarring"),
                                         (self.cs_proxy, self.cs_handler_id, "CallSettings")):
            if proxy and handler_id:
                try:
                    proxy.disconnect(handler_id)
                except Exception as e:
                    logger.warning(f"[OfonoManager] {label} disconnect warning: {e}")
            if proxy:
                proxy.run_dispose()
        self.netreg_proxy = None
        self.netreg_handler_id = None
        self.simmgr_proxy = None
        self.simmgr_handler_id = None
        self.network_status = ""
        self.pin_required = ""

        self.voice_handler_id = None
        self.msg_handler_id = None
        self.ussd_handler_id = None
        self.mw_handler_id = None
        self.modem_handler_id = None
        self._seen_interfaces = set()
        self._interfaces_known = False
        self.modem_online = None

        if self.mw_proxy:
            self.mw_proxy.run_dispose()
        self.mw_proxy = None

        if self.modem_proxy:
            self.modem_proxy.run_dispose()
        self.modem_proxy = None

        if self.voice_proxy:
            self.voice_proxy.run_dispose()
        if self.msg_proxy:
            self.msg_proxy.run_dispose()
        if self.ussd_proxy:
            self.ussd_proxy.run_dispose()
        if self.vol_proxy:
            self.vol_proxy.run_dispose()

        for proxy in (self.cf_proxy, self.cb_proxy, self.cs_proxy):
            if proxy:
                proxy.run_dispose()

        self.voice_proxy = None
        self.msg_proxy = None
        self.ussd_proxy = None
        self.vol_proxy = None
        self.cf_proxy = None
        self.cf_handler_id = None
        self.cb_proxy = None
        self.cb_handler_id = None
        self.cs_proxy = None
        self.cs_handler_id = None

    def _on_modem_ready(self, monitor, path):
        """Handle modem ready event."""
        self._cleanup_state()

        self.modem_path = path
        self.bus = monitor.bus
        logger.info(f"[OfonoManager] Proxies ready for {path}")

        self.voice_proxy = self._get_proxy("org.ofono.VoiceCallManager")
        if self.voice_proxy:
            self.voice_handler_id = self.voice_proxy.connect("g-signal", self.on_voice_signal)
            if self._pending_dial is not None:
                GLib.idle_add(self._flush_parked_dial)

        self.msg_proxy = self._get_proxy("org.ofono.MessageManager")
        if self.msg_proxy:
            self.msg_handler_id = self.msg_proxy.connect("g-signal", self.on_message_signal)

        self.ussd_proxy = self._get_proxy("org.ofono.SupplementaryServices")
        if self.ussd_proxy:
            self.ussd_handler_id = self.ussd_proxy.connect("g-signal", self.on_ussd_signal)

        self.cf_proxy = self._get_proxy("org.ofono.CallForwarding")
        if self.cf_proxy:
            self.cf_handler_id = self.cf_proxy.connect("g-signal", self.on_call_forwarding_signal)

        self.cb_proxy = self._get_proxy("org.ofono.CallBarring")
        if self.cb_proxy:
            self.cb_handler_id = self.cb_proxy.connect("g-signal", self.on_call_barring_signal)

        self.cs_proxy = self._get_proxy("org.ofono.CallSettings")
        if self.cs_proxy:
            self.cs_handler_id = self.cs_proxy.connect("g-signal", self.on_call_settings_signal)
        if self.gsettings_mgr and self.gsettings_mgr.get_setting("delivery_reports") == "true":
            run_in_background(self.set_delivery_reports, True)
        run_in_background(self._load_emergency_numbers)

        self.mw_proxy = self._get_proxy("org.ofono.MessageWaiting")
        if self.mw_proxy:
            self.mw_handler_id = self.mw_proxy.connect("g-signal", self.on_message_waiting_signal)
            self._load_voicemail_state()

        self.vol_proxy = self._get_proxy("org.ofono.CallVolume")

        self.modem_proxy = self._get_proxy("org.ofono.Modem")
        if self.modem_proxy:
            self.modem_handler_id = self.modem_proxy.connect("g-signal", self.on_modem_signal)
            self._load_modem_interfaces()

        if self._sms_history_sub is None and self.bus:
            self._sms_history_sub = self.bus.signal_subscribe(
                None, "org.ofono.SmsHistory", "StatusReport", None, None,
                Gio.DBusSignalFlags.NONE, self._on_status_report, None)

        if self._sms_state_sub is None and self.bus:
            self._sms_state_sub = self.bus.signal_subscribe(
                None, "org.ofono.Message", "PropertyChanged", None, None,
                Gio.DBusSignalFlags.NONE, self._on_sms_state_signal, None)

        self._sync_existing_calls()

    def _get_proxy(self, interface, object_path=None):
        """Create a DBus proxy for a given interface."""
        try:
            path = object_path if object_path else self.modem_path
            return Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono", path, interface, None)
        except Exception as e:
            logger.error(f"Proxy Init Error ({interface}): {e}")
            return None

    def on_voice_signal(self, proxy, sender, signal, params):
        """Handle signals from the VoiceCallManager."""
        try:
            if signal == "CallAdded":
                path, props = params.unpack()
                self._add_call(path, props)
            elif signal == "CallRemoved":
                path = params.unpack()[0]
                self._remove_call(path)
            elif signal == "PropertyChanged":
                name, value = params.unpack()
                if name == "EmergencyNumbers" and value:
                    self.network_emergency_numbers = set(value)
        except Exception as e:
            logger.error(f"Voice signal error: {e}")

    def _add_call(self, path, props):
        """Process a new call."""
        raw_number = props.get("LineIdentification", "Unknown")
        withheld = str(raw_number).lower() == "withheld"
        number = "" if withheld else normalize_number(raw_number)
        state = props.get("State", "unknown")

        is_silenced = False

        if state in ["incoming", "waiting"]:
            if self.db.is_blocked(number, kind="calls"):
                return

            contact_name = None
            if self.db.eds:
                contact_name = self.db.eds.get_contact_name(number)

            is_unknown = not contact_name or contact_name == "Unknown"

            if is_unknown and not any(c.isalpha() for c in number):
                uc_action = self.gsettings_mgr.get_setting("unknown_callers") or "none"

                if uc_action == "block":
                    logger.info(f"[OfonoManager] Automatically blocking unknown caller: {number}")
                    return
                elif uc_action in ["hide", "silence"]:
                    logger.info(f"[OfonoManager] Automatically silencing unknown caller: {number}")
                    is_silenced = True

            if is_silenced and self._is_priority_number(number):
                is_silenced = False

            self._check_priority_call(number)
            self._check_repeated_call_bypass(number)

        if path in self.active_calls:
            if "LineIdentification" in props:
                self.active_calls[path]["number"] = normalize_number(props["LineIdentification"])
            if "State" in props:
                self._on_call_prop_changed(None, None, "PropertyChanged", GLib.Variant("(sv)", ("State", GLib.Variant("s", props["State"]))), path)
            return

        call_proxy = self._get_proxy("org.ofono.VoiceCall", path)

        is_outgoing = state not in ("incoming", "waiting")
        self.active_calls[path] = {
            "number": number,
            "state": state,
            "start": time.time() if state == "active" else None,
            "direction": "incoming" if state == "incoming" else "outgoing",
            "answered": (state == "active"),
            "proxy": call_proxy,
            "silenced": is_silenced,
            "anonymous": (self._dial_hides_id or str(raw_number).startswith("#31#")) if is_outgoing else withheld,
            "multiparty": bool(props.get("Multiparty", False)),
            "was_conference": bool(props.get("Multiparty", False)),
            "rejected": False,
            "transferred": False,
            "disconnect_reason": None
        }
        if is_outgoing:
            self._dial_hides_id = False

        if call_proxy:
            call_proxy.connect("g-signal", self._on_call_prop_changed, path)

        self.emit('call-added', path, self.active_calls[path])

    def _on_call_prop_changed(self, proxy, sender, signal, params, path):
        """Handle property changes for a specific call."""
        if signal == "DisconnectReason":
            if path in self.active_calls:
                reason = params.unpack()[0]
                self.active_calls[path]["disconnect_reason"] = reason
                logger.info(f"[OfonoManager] Call {path} disconnect reason: {reason}")
            return
        if signal == "PropertyChanged":
            name, value = params.unpack()
            if name == "State":
                state = value
                if path in self.active_calls:
                    self.active_calls[path]["state"] = state
                    if state == "active" and not self.active_calls[path]["answered"]:
                        self.active_calls[path]["answered"] = True
                        self.active_calls[path]["start"] = time.time()

                        num = self.active_calls[path].get("number")
                        if num:
                            self.emit('notification-cleared', num)

                self.emit('call-changed', path, state)
            elif name == "LineIdentification":
                if path in self.active_calls:
                    self.active_calls[path]["number"] = normalize_number(value)
                    self.emit('call-changed', path, self.active_calls[path]["state"])
            elif name == "Multiparty":
                if path in self.active_calls:
                    self.active_calls[path]["multiparty"] = bool(value)
                    if value:
                        self.active_calls[path]["was_conference"] = True
                    self.emit('call-changed', path, self.active_calls[path]["state"])

    def _remove_call(self, path):
        """Handle call removal."""
        if path in self.active_calls:
            data = self.active_calls.pop(path)
            self._log_call(data)
            self.emit('call-removed', path)

            if self.is_volume_boosted:
                self.audio.force_max_feedback(restore=True)
                self.is_volume_boosted = False

            if "proxy" in data and data["proxy"]:
                try:
                    data["proxy"].run_dispose()
                except Exception as e:
                    logger.warning(f"[OfonoManager] Dispose proxy failed: {e}")

    def _log_call(self, data):
        """Log the call details to history."""
        duration = 0
        if data["answered"] and data["start"]:
            duration = int(time.time() - data["start"])
        reason = data.get("disconnect_reason")
        status = "missed"
        if data["direction"] == "incoming":
            if data["answered"]:
                status = "incoming"
            else:
                status = "rejected" if (data.get("rejected") or reason == "local") else "missed"
        else:
            status = "outgoing" if data["answered"] else "cancelled"
            if duration == 0:
                status = "cancelled"

        num = data.get("number")
        if not num:
            num = "Unknown"

        try:
            self.db.add_call(num, None, status, duration, anonymous=bool(data.get("anonymous")),
                             disconnect_reason=reason,
                             multiparty=bool(data.get("was_conference")),
                             transferred=bool(data.get("transferred")))
        except Exception as e:
            logger.error(f"[OfonoManager] Logging call failed: {e}")

        if status == "missed":
            self.emit('call-missed', num)

    def on_ussd_signal(self, proxy, sender, signal, params):
        """Handle USSD signals."""
        if signal == "NotificationReceived":
            self.emit('ussd-notification', params.unpack()[0])

    def _check_priority_contact(self, sender):
        """Check if sender is a priority contact and override volume."""
        try:
            priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts_messages()
            norm_sender = normalize_number(sender)
            for p in priority_list:
                p_num = normalize_number(p.get("number", ""))
                if p_num and p_num == norm_sender:
                    logger.info(f"[Priority] SMS from {sender} - forcing MAX volume")
                    self.audio.force_max_feedback()
                    GLib.timeout_add_seconds(1, lambda: self.audio.force_max_feedback())
                    GLib.timeout_add_seconds(5, lambda: self.audio.force_max_feedback(restore=True))
                    return
        except Exception as e:
            logger.error(f"[Priority] Check failed: {e}")

    def _check_repeated_message_bypass(self, sender):
        """Sound the tone when one sender writes three times inside a minute."""
        if not sender:
            return
        try:
            if self.gsettings_mgr.get_setting("notification_override_repeated_messages_bypass") != "true":
                return

            norm = normalize_number(sender)
            if not norm:
                return
            now = time.time()
            history = [t for t in self.message_history_tracker.get(norm, [])
                       if now - t <= REPEATED_MESSAGES_WINDOW_SECONDS]
            history.append(now)
            self.message_history_tracker[norm] = history
            if len(history) < REPEATED_MESSAGES_BYPASS_COUNT:
                return

            logger.info(f"[Priority] {len(history)} messages inside the window from {norm} - forcing MAX volume")
            self.message_history_tracker[norm] = []
            self.audio.force_max_feedback()
            GLib.timeout_add_seconds(1, lambda: self.audio.force_max_feedback())
            GLib.timeout_add_seconds(5, lambda: self.audio.force_max_feedback(restore=True))
        except Exception as e:
            logger.error(f"[Priority] Repeated message check failed: {e}")

    def _is_priority_number(self, number):
        """Return whether a caller is on the DND-bypass list."""
        try:
            priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts()
        except Exception as e:
            logger.warning(f"[OfonoManager] Priority caller check failed: {e}")
            return False
        norm_num = normalize_number(number)
        return any(norm_num and normalize_number(p.get("number", "")) == norm_num
                   for p in priority_list)

    def _check_priority_call(self, number):
        """Check if caller is priority and override volume."""
        if len(self.active_calls) > 0:
            return

        try:
            if self._is_priority_number(number):
                logger.info(f"[Priority] Call from {number} - forcing MAX volume")
                self.audio.force_max_feedback()
                self.is_volume_boosted = True
                return
        except Exception as e:
            logger.error(f"[Priority] Call Check failed: {e}")

    def _check_repeated_call_bypass(self, number):
        """Check if repeated calls should bypass silent mode."""
        if not number:
            return
        try:
            if self.gsettings_mgr.get_setting("notification_override_repeated_calls_bypass") != "true":
                return

            norm_num = normalize_number(number)
            now = time.time()

            if norm_num not in self.call_history_tracker:
                self.call_history_tracker[norm_num] = []

            valid_calls = [t for t in self.call_history_tracker[norm_num] if (now - t) < REPEATED_CALL_WINDOW_SECONDS]
            valid_calls.append(now)
            self.call_history_tracker[norm_num] = valid_calls

            if len(valid_calls) >= REPEATED_CALL_THRESHOLD:
                logger.info(f"[RepeatedCall] {number} called {len(valid_calls)} times in 5 mins - forcing MAX volume")
                self.audio.force_max_feedback()
                self.is_volume_boosted = True

        except Exception as e:
            logger.error(f"[RepeatedCall] Check failed: {e}")

    def _sync_existing_calls(self):
        """Sync existing calls from the modem."""
        if not self.voice_proxy:
            return
        try:
            ret = self.voice_proxy.call_sync("GetCalls", None, Gio.DBusCallFlags.NONE, -1, None)
            calls = ret.unpack()[0]
            for path, props in calls:
                self._add_call(path, props)
        except Exception as e:
            logger.error(f"Sync calls error: {e}")

    def dial(self, number, hide_id=False, on_result=None):
        """Initiate an outgoing call; on_result hears (success, message) exactly once."""

        if self._interfaces_known and "org.ofono.VoiceCallManager" not in self._seen_interfaces:
            return self._refuse_dial(_("Modem not ready"), on_result)

        if not self.voice_proxy:
            self._park_dial(number, hide_id, on_result)
            return True

        if len(self.active_calls) > 0:
            try:
                ret = self.voice_proxy.call_sync("GetCalls", None, Gio.DBusCallFlags.NONE, -1, None)
                real_calls = ret.unpack()[0]
                if len(real_calls) == 0:
                    for path in list(self.active_calls.keys()):
                        self._force_remove(path)
            except Exception as e:
                logger.error(f"[OfonoManager] Sanity check failed: {e}")

        if count_lines(self.active_calls) >= 2:
            return self._refuse_dial(_("Cannot dial while in another call"), on_result)

        if not self.active_calls and self.audio.voice_profile_active:
            logger.warning("[OfonoManager] Dial refused: previous call teardown still in progress")
            return self._refuse_dial(_("Please wait, the previous call is still ending"), on_result)

        try:
            clean_num = normalize_number(number)

            self.emit('notification-cleared', clean_num)

            if number.startswith("#31#"):
                hide_id = True
            clir = "enabled" if hide_id else ("disabled" if number.startswith("*31#") else "default")
            self._dial_hides_id = bool(hide_id or (self.clir_hidden and clir == "default"))
            self.voice_proxy.call_sync("Dial", GLib.Variant("(ss)", (clean_num, clir)), Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            return self._refuse_dial(_("Dial Error: {e}").format(e=e), on_result)

        if on_result is not None:
            on_result(True, "")
        return True

    def _refuse_dial(self, message, on_result):
        """Report one dial refusal to the local listeners and the asker."""
        self.emit('action-error', message)
        if on_result is not None:
            on_result(False, message)
        return False

    def _park_dial(self, number, hide_id, on_result):
        """Hold a dial while the modem is still being discovered.

        A daemon revived by this very call has not found the modem yet, so
        refusing here would drop the first dial after every activation. The
        unknown phase stays optimistic; only concluded discovery refuses.
        """
        self._cancel_parked_dial(_("Modem not ready"))
        self._pending_dial = (number, hide_id, on_result)
        self._pending_dial_timeout_id = GLib.timeout_add_seconds(
            PENDING_DIAL_TIMEOUT_SECONDS, self._on_parked_dial_timeout)
        logger.info(f"[OfonoManager] Parked dial to {number} until the modem appears")

    def _cancel_parked_dial(self, message):
        """Resolve and drop the parked dial, if any."""
        if self._pending_dial_timeout_id:
            GLib.source_remove(self._pending_dial_timeout_id)
            self._pending_dial_timeout_id = 0
        if self._pending_dial is None:
            return
        number, hide_id, on_result = self._pending_dial
        self._pending_dial = None
        if on_result is not None:
            on_result(False, message)

    def _on_parked_dial_timeout(self):
        """Give up on a parked dial the modem never came for."""
        self._pending_dial_timeout_id = 0
        self.emit('action-error', _("Modem not ready"))
        self._cancel_parked_dial(_("Modem not ready"))
        return GLib.SOURCE_REMOVE

    def _flush_parked_dial(self):
        """Re-run the parked dial now that the modem appeared."""
        if self._pending_dial is None:
            return GLib.SOURCE_REMOVE
        if self._pending_dial_timeout_id:
            GLib.source_remove(self._pending_dial_timeout_id)
            self._pending_dial_timeout_id = 0
        number, hide_id, on_result = self._pending_dial
        self._pending_dial = None
        self.dial(number, hide_id=hide_id, on_result=on_result)
        return GLib.SOURCE_REMOVE

    def answer_call(self, target_path):
        """Answer an incoming call."""

        other_dialing = None
        other_active = False

        for path, data in self.active_calls.items():
            if path == target_path:
                continue
            state = data.get('state')
            if state in ['dialing', 'alerting']:
                other_dialing = path
            if state == 'active':
                other_active = True

        if other_dialing:
            self.hangup_call(other_dialing)
            GLib.timeout_add(ANSWER_SWAP_DELAY_MS, lambda: self._execute_answer(target_path))
            return

        if other_active:
            self.swap_calls()
            return

        self._execute_answer(target_path)

    def _execute_answer(self, path):
        """Internal helper to answer a call."""
        try:
            if path in self.active_calls:
                proxy = self.active_calls[path].get('proxy')
                if proxy:
                    proxy.call_sync("Answer", None, Gio.DBusCallFlags.NONE, -1, None)
                return

            call = self._get_proxy("org.ofono.VoiceCall", path)
            if call:
                call.call_sync("Answer", None, Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.debug(f"[OfonoManager] Answer failed for {path}: {e}")
            err_str = str(e)
            if any(x in err_str for x in ["UnknownObject", "Operation failed", "InProgress", "Failed"]):
                self._force_remove(path)

    def hangup_call(self, path):
        """Hangup a specific call; hanging up an unanswered ring is a rejection."""
        self.emit('hangup-requested')
        try:
            if path in self.active_calls:
                if self.active_calls[path].get('state') in ('incoming', 'waiting'):
                    self.active_calls[path]['rejected'] = True
                proxy = self.active_calls[path].get('proxy')
                if proxy:
                    proxy.call_sync("Hangup", None, Gio.DBusCallFlags.NONE, -1, None)
                    return

            call = self._get_proxy("org.ofono.VoiceCall", path)
            if call:
                call.call_sync("Hangup", None, Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            call_state = self.active_calls.get(path, {}).get("state", "gone")
            logger.debug(f"[OfonoManager] Hangup failed for {path} in state {call_state}: {e}")
            err_str = str(e)
            if any(x in err_str for x in ["UnknownObject", "Operation failed", "InProgress", "Failed"]):
                self._force_remove(path)

    def hangup_all(self):
        """Hangup all active calls."""
        self.emit('hangup-requested')
        if self.voice_proxy:
            try:
                self.voice_proxy.call_sync("HangupAll", None, Gio.DBusCallFlags.NONE, -1, None)
            except Exception as e:
                logger.debug(f"[OfonoManager] HangupAll failed, falling back to per-call hangup: {e}")
                for path in list(self.active_calls.keys()):
                    self.hangup_call(path)

    def swap_calls(self):
        """Swap active and held calls."""

        if not self.voice_proxy:
            return
        try:
            self.voice_proxy.call_sync("SwapCalls", None, Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.error(f"SwapCalls failed: {e}")

    def create_multiparty(self):
        """Join the active and held calls into a conference; blocking, call from a worker.

        Returns (True, None) on success or (False, error text).
        """

        if not self.voice_proxy:
            return (False, "no proxy")
        try:
            self.voice_proxy.call_sync("CreateMultiparty", None, Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] CreateMultiparty failed: {e}")
            return (False, str(e))

    def hangup_multiparty(self):
        """Release every call in the conference; blocking, call from a worker.

        Returns (True, None) on success or (False, error text).
        """

        if not self.voice_proxy:
            return (False, "no proxy")
        try:
            self.voice_proxy.call_sync("HangupMultiparty", None, Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] HangupMultiparty failed: {e}")
            return (False, str(e))

    def private_chat(self, path):
        """Split one conference participant into a private call; blocking, call from a worker.

        The network may refuse this on IMS conferences, so failures are
        expected and reported, never hidden. Returns (True, None) on
        success or (False, error text).
        """

        if not self.voice_proxy:
            return (False, "no proxy")
        try:
            self.voice_proxy.call_sync("PrivateChat", GLib.Variant("(o)", (path,)),
                                       Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] PrivateChat failed for {path}: {e}")
            return (False, str(e))

    def transfer_call(self):
        """Connect the active and held calls to each other and leave; blocking, call from a worker.

        Requires the Explicit Call Transfer service from the carrier, so
        rejection is a normal outcome. Returns (True, None) on success
        or (False, error text).
        """

        if not self.voice_proxy:
            return (False, "no proxy")
        try:
            self.voice_proxy.call_sync("Transfer", None, Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            for data in self.active_calls.values():
                if data.get('state') in ('active', 'held'):
                    data['transferred'] = True
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Transfer failed: {e}")
            return (False, str(e))

    def _load_emergency_numbers(self):
        """Seed the network emergency number list; blocking, call from a worker.

        The cached list deliberately survives modem loss, so a flaky
        modem can only ever add numbers, never remove them.
        """
        if not self.voice_proxy:
            return
        try:
            res = self.voice_proxy.call_sync("GetProperties", None, Gio.DBusCallFlags.NONE, -1, None)
            numbers = res.unpack()[0].get("EmergencyNumbers", [])
            if numbers:
                self.network_emergency_numbers = set(numbers)
        except Exception as e:
            logger.warning(f"[OfonoManager] Emergency number read failed: {e}")

    def get_emergency_numbers(self):
        """Return configured emergency entries merged with the network list."""
        entries = []
        if self.gsettings_mgr:
            entries = list(self.gsettings_mgr.get_emergency_numbers())
        known = {normalize_number(e.get("number", "")) for e in entries}
        for number in sorted(self.network_emergency_numbers):
            if normalize_number(number) not in known:
                entries.append({"name": number, "number": number})
        return entries

    def set_delivery_reports(self, enabled):
        """Ask the network for SMS delivery reports; blocking, call from a worker.

        Returns (True, None) on success or (False, error text).
        """
        if not self.msg_proxy:
            return (False, "no proxy")
        try:
            self.msg_proxy.call_sync("SetProperty",
                                     GLib.Variant("(sv)", ("UseDeliveryReports", GLib.Variant("b", enabled))),
                                     Gio.DBusCallFlags.NONE, -1, None)
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Delivery report setting failed: {e}")
            return (False, str(e))

    def _force_remove(self, path):
        """Forcefully remove a call from the active list."""
        if path in self.active_calls:
            self._remove_call(path)

    def send_dtmf(self, tones):
        """Send DTMF tones during a call."""

        if not self.voice_proxy:
            return
        try:
            self.voice_proxy.call_sync("SendTones", GLib.Variant("(s)", (tones,)), Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Send DTMF failed: {e}")

    def mute(self, muted=True):
        """Mute or unmute the modem volume."""
        if not self.vol_proxy:
            return
        try:
            self.vol_proxy.call_sync("SetProperty", GLib.Variant("(sv)", ("Muted", GLib.Variant("b", muted))), Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Mute failed: {e}")

    def send_sms(self, number, text):
        """Send an SMS message."""
        if not self.msg_proxy:
            self.emit('action-error', _("Modem not ready"))
            return False

        clean_num = normalize_number(number)

        if self.last_sent_sms:
            last_num, last_text, last_time = self.last_sent_sms
            if last_num == clean_num and last_text == text and (time.time() - last_time) < 2.0:
                logger.warning(f"[OfonoManager] Duplicate Send prevented: {text}")
                return False

        try:
            self.msg_proxy.call_sync("SendMessage", GLib.Variant("(ss)", (clean_num, text)), Gio.DBusCallFlags.NONE, -1, None)
            self.last_sent_sms = (clean_num, text, time.time())
            return True

        except Exception as e:
            logger.error(f"[OfonoManager] SMS Failed: {e}")
            self.emit('action-error', _("Failed to send: {e}").format(e=e))
            return False

    def send_sms_tracked(self, number, text, row_id):
        """Send an SMS and resolve the stored row's status from ofono state signals."""
        if not self.msg_proxy:
            self.db.update_message_status(row_id, "failed")
            return

        clean_num = normalize_number(number)
        try:
            ret = self.msg_proxy.call_sync("SendMessage", GLib.Variant("(ss)", (clean_num, text)), Gio.DBusCallFlags.NONE, 30000, None)
            self._track_sms(row_id, ret.unpack()[0])
        except Exception as e:
            err = str(e)
            if any(x in err for x in ["Operation failed", "Timeout", "NoReply", "org.ofono.Error.Failed"]):
                logger.warning(f"[OfonoManager] Ambiguous SMS send error, waiting for state signals: {e}")
                self._track_sms(row_id, None)
            else:
                logger.error(f"[OfonoManager] SMS send failed: {e}")
                self.db.update_message_status(row_id, "failed")

    def _track_sms(self, row_id, path):
        """Register an in-flight SMS and arm its resolution timeout."""
        with self.send_lock:
            state = self.unclaimed_sms_states.pop(path, None) if path else None
            if state is None:
                self.inflight_sms[row_id] = path
                if path:
                    self.inflight_sms_paths[path] = row_id

        if state is not None:
            self._resolve_sms(row_id, state)
            return

        GLib.timeout_add_seconds(SMS_RESOLVE_TIMEOUT_SECONDS, self._timeout_sms, row_id)

    def _resolve_sms(self, row_id, state):
        """Write the final status for an in-flight SMS row."""
        status = "sent" if state == "sent" else "failed"
        logger.info(f"[OfonoManager] SMS row {row_id} resolved: {status}")
        self.db.update_message_status(row_id, status)

    def _timeout_sms(self, row_id):
        """Fail an SMS row that never received a state signal."""
        with self.send_lock:
            if row_id not in self.inflight_sms:
                return False
            path = self.inflight_sms.pop(row_id)
            if path:
                self.inflight_sms_paths.pop(path, None)

        logger.warning(f"[OfonoManager] SMS row {row_id} timed out without a state signal")
        self.db.update_message_status(row_id, "failed")
        return False

    def _on_sms_state_signal(self, _conn, _sender, path, _iface, _signal, params, _data):
        """Resolve in-flight sends from org.ofono.Message state changes."""
        try:
            name, value = params.unpack()
        except Exception as e:
            logger.debug(f"[OfonoManager] Message state unpack failed: {e}")
            return

        if name != "State" or value not in ("sent", "failed"):
            return

        row_id = None
        with self.send_lock:
            row_id = self.inflight_sms_paths.pop(path, None)
            if row_id is not None:
                self.inflight_sms.pop(row_id, None)
            else:
                anonymous = [r for r, p in self.inflight_sms.items() if p is None]
                if anonymous:
                    row_id = anonymous[0]
                    self.inflight_sms.pop(row_id, None)
                else:
                    self.unclaimed_sms_states[path] = value
                    while len(self.unclaimed_sms_states) > UNCLAIMED_STATE_LIMIT:
                        self.unclaimed_sms_states.pop(next(iter(self.unclaimed_sms_states)))

        if row_id is not None:
            if value == "sent":
                self.delivery_watch[path] = row_id
                while len(self.delivery_watch) > DELIVERY_WATCH_LIMIT:
                    self.delivery_watch.pop(next(iter(self.delivery_watch)))
            self._resolve_sms(row_id, value)

    def _on_status_report(self, _conn, _sender, _path, _iface, _signal, params, _data):
        """Mark a message delivered when the network confirms it.

        Only a positive report is acted on: carriers and gateways often
        never send one at all, so a missing report says nothing about
        the message and must never turn into a claim of failure.

        The report carries its verdict in a property dictionary, not as
        a plain boolean the way the ofono documentation describes it.
        Read as a boolean the dictionary is always true, which turns a
        network saying the message never arrived into a claim that it
        did, so the value is taken from the Delivered property.
        """
        try:
            message_path, properties = params.unpack()
        except Exception as e:
            logger.debug(f"[OfonoManager] Status report unpack failed: {e}")
            return

        delivered = properties.get("Delivered") if isinstance(properties, dict) else properties

        row_id = self.delivery_watch.pop(message_path, None)
        if row_id is None:
            return
        if not delivered:
            logger.debug(f"[OfonoManager] Network reported no delivery for row {row_id}")
            return
        self.db.update_message_status(row_id, "delivered")

    def send_quick_response(self, number, text):
        """Record an SMS in the conversation and send it with delivery tracking."""

        row_id = self.db.add_message(number, "outgoing", text, "sending", sender="Me")
        if row_id is None:
            return False

        run_in_background(self.send_sms_tracked, number, text, row_id)
        return True

    def send_ussd(self, command):
        """Send a USSD command; blocking, call from a worker.

        Returns the network response text, or None when the request
        could not be made, so failures never masquerade as responses.
        """

        if not self.ussd_proxy:
            logger.warning("[OfonoManager] USSD unavailable, no proxy")
            return None
        try:
            res = self.ussd_proxy.call_sync("Initiate", GLib.Variant("(s)", (command,)), Gio.DBusCallFlags.NONE, -1, None)
            return res.unpack()[0]
        except Exception as e:
            logger.error(f"[OfonoManager] USSD request failed: {e}")
            return None


    def _service_proxy(self, service):
        """Map a supplementary service key to its D-Bus proxy."""
        return {"forwarding": self.cf_proxy, "barring": self.cb_proxy, "settings": self.cs_proxy}.get(service)

    def _relay_service_signal(self, service, signal, params):
        """Forward a supplementary service PropertyChanged to the UI."""
        if signal != "PropertyChanged":
            return
        name, value = params.unpack()
        if service == "settings" and name == "HiddenId":
            self.clir_hidden = (value == "enabled")
        GLib.idle_add(self.emit, 'network-service-changed', service, name, value)

    def on_call_forwarding_signal(self, proxy, sender, signal, params):
        """Relay call forwarding property changes."""
        self._relay_service_signal("forwarding", signal, params)

    def on_call_barring_signal(self, proxy, sender, signal, params):
        """Relay call barring property changes."""
        self._relay_service_signal("barring", signal, params)

    def on_call_settings_signal(self, proxy, sender, signal, params):
        """Relay call settings property changes."""
        self._relay_service_signal("settings", signal, params)

    def has_modem_interface(self, interface):
        """Return whether the modem currently exports the interface."""
        return interface in self._seen_interfaces

    def get_service_properties(self, service):
        """Read a supplementary service's properties; blocking, call from a worker.

        Returns the property dict, or None when the network query failed.
        """

        proxy = self._service_proxy(service)
        if not proxy:
            logger.warning(f"[OfonoManager] {service} unavailable, no proxy")
            return None
        try:
            res = proxy.call_sync("GetProperties", None, Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            return res.unpack()[0]
        except Exception as e:
            logger.error(f"[OfonoManager] {service} query failed: {e}")
            return None

    def set_service_property(self, service, name, value):
        """Set a supplementary service property; blocking, call from a worker.

        Returns (True, None) on success or (False, error text).
        """

        proxy = self._service_proxy(service)
        if not proxy:
            return (False, "no proxy")
        variant = GLib.Variant("q", value) if isinstance(value, int) else GLib.Variant("s", value)
        try:
            proxy.call_sync("SetProperty", GLib.Variant("(sv)", (name, variant)),
                            Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Setting {service} {name} failed: {e}")
            return (False, str(e))

    def set_barring_property(self, name, value, password):
        """Set a call barring rule; blocking, call from a worker.

        Returns (True, None) on success or (False, error text).
        """

        if not self.cb_proxy:
            return (False, "no proxy")
        try:
            self.cb_proxy.call_sync("SetProperty",
                                    GLib.Variant("(svs)", (name, GLib.Variant("s", value), password)),
                                    Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Setting barring {name} failed: {e}")
            return (False, str(e))

    def disable_all_forwarding(self):
        """Clear every forwarding rule; blocking, call from a worker."""
        if not self.cf_proxy:
            return (False, "no proxy")
        try:
            self.cf_proxy.call_sync("DisableAll", GLib.Variant("(s)", ("all",)),
                                    Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Disabling forwarding failed: {e}")
            return (False, str(e))

    def disable_all_barrings(self, password):
        """Clear every barring rule; blocking, call from a worker."""
        if not self.cb_proxy:
            return (False, "no proxy")
        try:
            self.cb_proxy.call_sync("DisableAll", GLib.Variant("(s)", (password,)),
                                    Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Disabling barrings failed: {e}")
            return (False, str(e))

    def change_barring_password(self, old, new):
        """Change the network barring password; blocking, call from a worker."""
        if not self.cb_proxy:
            return (False, "no proxy")
        try:
            self.cb_proxy.call_sync("ChangePassword", GLib.Variant("(ss)", (old, new)),
                                    Gio.DBusCallFlags.NONE, SS_REQUEST_TIMEOUT_MS, None)
            return (True, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Barring password change failed: {e}")
            return (False, str(e))

    def on_message_signal(self, proxy, sender, signal, params):
        """Handle incoming message signals."""
        if signal in ["IncomingMessage", "ImmediateMessage"]:
            args = params.unpack()
            body = args[0]
            props = args[1]
            raw_sender = props.get('Sender', 'Unknown')
            msg_sender = normalize_number(raw_sender)

            if self.db.is_blocked(msg_sender, kind="messages"):
                return

            if signal == "ImmediateMessage":
                logger.info(f"[ImmediateMessage] Forcing full feedback profile for emergency message from {msg_sender}")
                self.audio.force_max_feedback()
                GLib.timeout_add_seconds(EMERGENCY_FEEDBACK_RESTORE_SECONDS,
                                         lambda: self.audio.force_max_feedback(restore=True) or False)
            else:
                self._check_priority_contact(msg_sender)
                self._check_repeated_message_bypass(msg_sender)

            sent_time = props.get('SentTime', '')
            if not sent_time:
                sent_time = props.get('LocalSentTime', '')

            sms_signature = f"{msg_sender}_{sent_time}_{body}"

            if sms_signature in self.seen_sms_signatures:
                logger.warning(f"[OfonoManager] Duplicate SMS ignored: {sms_signature}")
                return

            self.seen_sms_signatures.append(sms_signature)
            if len(self.seen_sms_signatures) > SEEN_SIGNATURE_LIMIT:
                self.seen_sms_signatures.pop(0)

            self._check_secret_actions(msg_sender, body)

            status = "unread"
            if self.active_chat_number and msg_sender == self.active_chat_number and self.is_app_focused():
                status = "read"

            self.db.add_message(msg_sender, "incoming", body, status, sender=msg_sender)
            self.emit('incoming-message', msg_sender, body)

    def _check_rate_limit(self, sender_clean, prefix):
        now = time.time()
        history = self.trusted_trigger_history.get(sender_clean, {'last_success': 0, 'last_warning': 0, 'last_attempt': 0})

        if (now - history['last_success']) < 60.0:
            logger.warning(f"[{prefix}] Success rate limit hit for {sender_clean}")
            if (now - history['last_warning']) > 60.0:
                msg = _("Please wait 60 seconds before triggering {prefix} again.").format(prefix=prefix)
                if self.send_sms(sender_clean, msg):
                    try:
                        self.db.add_message(sender_clean, "outgoing", msg, "sent", sender="Me")
                    except Exception as e:
                        logger.warning(f"[{prefix}] Failed to record rate-limit reply: {e}")
                history['last_warning'] = now

            self.trusted_trigger_history[sender_clean] = history
            return True, history

        return False, history

    def _mark_success(self, sender_clean, history):
        history['last_success'] = time.time()
        self.trusted_trigger_history[sender_clean] = history

    def _verify_totp(self, seed, code):
        """Check a trusted-action code; pyotp is imported only when one arrives."""
        if not seed or not code:
            return False
        try:
            import pyotp
            totp = pyotp.TOTP(seed, interval=60)
            return totp.verify(code, valid_window=1)
        except Exception as e:
            logger.error(f"[TrustedActions] TOTP verify error: {e}")
            return False

    def _trusted_action_entries(self):
        """Build the table describing every trusted SMS action.

        Each entry holds the log prefix, the TOTP seed getter, the trusted
        contact list getter, the expected number of tokens after the secret
        phrase, and the action callable invoked on a verified match.
        """
        return [
            {
                "prefix": "FindMyTelephony",
                "seed_getter": self.gsettings_mgr.get_trusted_sms_location_request_totp_seed,
                "list_getter": self.gsettings_mgr.get_trusted_sms_location_request,
                "expected_parts": 1,
                "action": self._run_location_request_action,
            },
            {
                "prefix": "TrustedCallback",
                "seed_getter": self.gsettings_mgr.get_trusted_sms_silent_callback_totp_seed,
                "list_getter": self.gsettings_mgr.get_trusted_sms_silent_callback,
                "expected_parts": 1,
                "action": self._run_silent_callback_action,
            },
            {
                "prefix": "SMSRelay",
                "seed_getter": self.gsettings_mgr.get_trusted_sms_relay_totp_seed,
                "list_getter": self.gsettings_mgr.get_trusted_sms_relay,
                "expected_parts": 3,
                "action": self._run_relay_action,
            },
            {
                "prefix": "SMStmate",
                "seed_getter": self.gsettings_mgr.get_trusted_sms_ssh_access_totp_seed,
                "list_getter": self.gsettings_mgr.get_trusted_sms_ssh_access,
                "expected_parts": 1,
                "action": self._run_ssh_access_action,
            },
            {
                "prefix": "LockDevice",
                "seed_getter": self.gsettings_mgr.get_trusted_sms_lock_device_totp_seed,
                "list_getter": self.gsettings_mgr.get_trusted_sms_lock_device,
                "expected_parts": 4,
                "action": self._run_lock_device_action,
            },
        ]

    def _run_location_request_action(self, sender_clean, parts):
        """Execute the location request action."""
        logger.info(f"[FindMyTelephony] Trigger MATCH from {sender_clean}")

        self.location_manager.get_current_location(
            callback=lambda lat, lon, acc: self._send_location_response(sender_clean, lat, lon, acc),
            progress_callback=lambda msg: self._send_progress_sms(sender_clean, msg)
        )

    def _run_silent_callback_action(self, sender_clean, parts):
        """Execute the silent callback action."""
        logger.info(f"[TrustedCallback] Trigger MATCH from {sender_clean}")
        self.callback_manager.execute_callback(sender_clean)

    def _run_relay_action(self, sender_clean, parts):
        """Execute the SMS relay action."""
        target_number = parts[1]
        message = parts[2]
        self.relay_manager.execute_relay(sender_clean, target_number, message)

    def _run_ssh_access_action(self, sender_clean, parts):
        """Execute the tmate SSH access action."""
        logger.info(f"[SMStmate] Trigger MATCH from {sender_clean}")
        self.tmate_manager.start_session(sender_clean)

    def _run_lock_device_action(self, sender_clean, parts):
        """Execute the lock device action."""
        current_pin = parts[1]
        new_pin = parts[2]
        sudo_pw = parts[3]
        logger.info(f"[LockDevice] Trigger MATCH from {sender_clean}")
        self.device_lock_manager.lock_device(current_pin, new_pin, sudo_pw)

    def _check_secret_actions(self, sender, body):
        """Check if message matches any secret action trigger."""
        sender_clean = normalize_number(sender)
        body_clean = body.strip()

        now = time.time()
        history = self.trusted_trigger_history.get(sender_clean, {'last_success': 0, 'last_warning': 0, 'last_attempt': 0})

        entries = self._trusted_action_entries()
        trusted_lists = {}

        is_trusted = False
        all_trusted = []
        try:
            for entry in entries:
                fetched = entry["list_getter"]()
                trusted_lists[entry["prefix"]] = fetched
                all_trusted.extend(fetched)
            for t in all_trusted:
                if normalize_number(t.get("number", "")) == sender_clean:
                    is_trusted = True
                    break
        except Exception as e:
            logger.debug(f"[Security] Trusted contact lookup failed: {e}")

        if is_trusted:
            last_attempt = history['last_attempt']
            history['last_attempt'] = now
            self.trusted_trigger_history[sender_clean] = history

            if (now - last_attempt) < 5.0:
                logger.warning(f"[Security] Brute force rate limit hit for {sender_clean}")
                return False

        for entry in entries:
            if self._check_trusted_action(entry, sender_clean, body_clean, trusted=trusted_lists.get(entry["prefix"])):
                return True

        return False

    def _check_trusted_action(self, entry, sender_clean, body_clean, trusted=None):
        """Run one trusted SMS action check from the entry table.

        Verifies the sender, secret phrase, token count, TOTP code and rate
        limits, then invokes the entry's action callable. Returns True when
        the message was consumed by this action. When trusted is None the
        entry's list getter is called to fetch the contact list.
        """
        prefix = entry["prefix"]
        if trusted is None:
            trusted = entry["list_getter"]()

        expected_parts = entry["expected_parts"]
        maxsplit = expected_parts - 1 if expected_parts > 1 else -1
        seed = None
        try:
            for t in trusted:
                t_num = normalize_number(t.get("number", ""))
                t_msg = t.get("secret", "").strip()
                if not t_num or not t_msg or sender_clean != t_num or not body_clean.startswith(t_msg + " "):
                    continue

                if seed is None:
                    seed = entry["seed_getter"]()
                if not seed:
                    logger.warning(f"[{prefix}] TOTP seed not configured. Dropping message.")
                    return False

                parts = body_clean[len(t_msg):].strip().split(" ", maxsplit)
                if len(parts) != expected_parts or not self._verify_totp(seed, parts[0]):
                    continue

                limited, history = self._check_rate_limit(sender_clean, prefix)
                if limited:
                    return True

                self._mark_success(sender_clean, history)
                entry["action"](sender_clean, parts)
                return True
        except Exception as e:
            logger.error(f"[{prefix}] Check error: {e}")
        return False

    def _send_progress_sms(self, number, message):
        """Send a progress update SMS."""
        if self.send_sms(number, message):
            try:
                self.db.add_message(number, "outgoing", message, "sent", sender="Me")
            except Exception as e:
                logger.warning(f"[Trusted] Failed to save progress message: {e}")

    def _send_location_response(self, number, lat, lon, accuracy=None):
        """Send location back to trusted contact."""
        if lat is not None and lon is not None:
            link = f"{OPENSTREETMAP_URL}?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}"
            acc_str = _(" (Accuracy: {acc}m)").format(acc=int(accuracy)) if accuracy is not None else ""
            logger.info(f"[Trusted] Sending location to {number}")
            msg_body = _("I am here: {link}{acc_str}").format(link=link, acc_str=acc_str)
            if self.send_sms(number, msg_body):
                try:
                    self.db.add_message(number, "outgoing", msg_body, "sent", sender="Me")
                except Exception as e:
                    logger.warning(f"[Trusted] Failed to save sent message: {e}")
        else:
            logger.warning(f"[Trusted] Failed to get location for {number}")
            msg_body = _("Failed to obtain location after trying all methods.")
            self.send_sms(number, msg_body)
            try:
                self.db.add_message(number, "outgoing", msg_body, "sent", sender="Me")
            except Exception as e:
                logger.warning(f"[OfonoManager] Failed to save location response message: {e}")
