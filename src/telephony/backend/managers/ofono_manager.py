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

from .ofono_calls_manager import OfonoCallsManager
from .ofono_messaging_manager import OfonoMessagingManager
from .ofono_trusted_actions_manager import OfonoTrustedActionsManager


import threading
import time
from loguru import logger
from gi.repository import Gio, GLib, GObject

from ...backend.utils.phone_utils import normalize_number
from ...backend.utils.system_utils import restart_ril_modem
from ...backend.utils.thread_utils import run_in_background
from ..services.ofono_service import OfonoService
from .location_manager import LocationManager
from .audio_manager import TelephonyAudioManager
from .tmate_manager import TmateManager
from .device_lock_manager import DeviceLockManager
from .callback_manager import CallbackManager
from .relay_manager import RelayManager

REPEATED_CALL_WINDOW_SECONDS = 300
REPEATED_CALL_THRESHOLD = 3

# ofono publishes MessageWaiting before it has read the SIM's message-waiting
# state, briefly reporting False and correcting itself a fraction of a second
# later (~320ms measured at boot on an FLX1s). Reporting that first False looks
# like the indication clearing, so let a negative first reading settle before
# believing it. A positive one needs no wait; ofono does not invent those.
VOICEMAIL_SETTLE_SECONDS = 3


class OfonoManager(GObject.Object, OfonoCallsManager, OfonoMessagingManager, OfonoTrustedActionsManager):
    """
    Manages voice calls, SMS, USSD, and voicemail indication via ofono.
    """
    __gsignals__ = {
        'incoming-message': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'connection-status': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'action-error': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'call-added': (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        'call-removed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'call-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'call-missed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'notification-cleared': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'ussd-notification': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'voicemail-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool, int, bool, str)),
    }

    def __init__(self, db_manager, gsettings_mgr=None):
        """Initialize the Ofono manager."""
        super().__init__()
        self.db = db_manager
        self.gsettings_mgr = gsettings_mgr

        self.voice_proxy = None
        self.msg_proxy = None
        self.ussd_proxy = None
        self.vol_proxy = None
        self.mwi_proxy = None
        self.modem_path = None
        self.bus = None

        self.voice_handler_id = None
        self.msg_handler_id = None
        self.ussd_handler_id = None
        self.mwi_handler_id = None

        self.voicemail = {"waiting": False, "count": None, "number": None}

        self.active_calls = {}
        self.active_chat_number = None
        self.focus_provider = None

        self.seen_sms_signatures = []
        self.last_sent_sms = None
        self.trusted_trigger_history = {}

        self.inflight_sms = {}
        self.inflight_sms_paths = {}
        self.unclaimed_sms_states = {}
        self.send_lock = threading.Lock()
        self._sms_state_sub = None

        self.call_history_tracker = {}
        self.is_volume_boosted = False

        self.monitor = OfonoService()
        self.monitor.connect('status-changed', self._on_monitor_status)
        self.monitor.connect('modem-ready', self._on_modem_ready)

        self.location_manager = LocationManager()
        self.audio = TelephonyAudioManager()
        self.tmate_manager = TmateManager(self)
        self.device_lock_manager = DeviceLockManager()
        self.callback_manager = CallbackManager(self)
        self.relay_manager = RelayManager(self)

    def set_active_chat(self, number):
        """Set the currently active chat to suppress notifications."""
        if not number:
            self.active_chat_number = None
        elif isinstance(number, list):
            self.active_chat_number = ",".join(sorted(normalize_number(n) for n in number))
        elif "," in number:
            self.active_chat_number = number
        else:
            self.active_chat_number = normalize_number(number)

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
        if self.mwi_proxy and self.mwi_handler_id:
            try:
                self.mwi_proxy.disconnect(self.mwi_handler_id)
            except Exception as e:
                logger.warning(f"[OfonoManager] Voicemail disconnect warning: {e}")

        self.voice_handler_id = None
        self.msg_handler_id = None
        self.ussd_handler_id = None
        self.mwi_handler_id = None

        if self.voice_proxy:
            self.voice_proxy.run_dispose()
        if self.msg_proxy:
            self.msg_proxy.run_dispose()
        if self.ussd_proxy:
            self.ussd_proxy.run_dispose()
        if self.vol_proxy:
            self.vol_proxy.run_dispose()
        if self.mwi_proxy:
            self.mwi_proxy.run_dispose()

        self.voice_proxy = None
        self.msg_proxy = None
        self.ussd_proxy = None
        self.vol_proxy = None
        self.mwi_proxy = None

        # Note: self.voicemail is deliberately left as-is. Losing the modem
        # means we can no longer see the indication, which is not the same as
        # the indication having cleared -- resetting it here would look like
        # the voicemail went away and, on reconnect, like a new one arrived.

    def _on_modem_ready(self, monitor, path):
        """Handle modem ready event."""
        self._cleanup_state()

        self.modem_path = path
        self.bus = monitor.bus
        logger.info(f"[OfonoManager] Proxies ready for {path}")

        self.voice_proxy = self._get_proxy("org.ofono.VoiceCallManager")
        if self.voice_proxy:
            self.voice_handler_id = self.voice_proxy.connect("g-signal", self.on_voice_signal)

        self.msg_proxy = self._get_proxy("org.ofono.MessageManager")
        if self.msg_proxy:
            self.msg_handler_id = self.msg_proxy.connect("g-signal", self.on_message_signal)

        self.ussd_proxy = self._get_proxy("org.ofono.SupplementaryServices")
        if self.ussd_proxy:
            self.ussd_handler_id = self.ussd_proxy.connect("g-signal", self.on_ussd_signal)

        self.vol_proxy = self._get_proxy("org.ofono.CallVolume")

        # MessageWaiting only joins Modem.Interfaces once the SIM is ready, so
        # it can legitimately be absent here; the modem-ready cycle brings it
        # back when the SIM appears.
        self.mwi_proxy = self._get_proxy("org.ofono.MessageWaiting")
        if self.mwi_proxy:
            self.mwi_handler_id = self.mwi_proxy.connect("g-signal", self.on_voicemail_signal)
            self.sync_voicemail()

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
        except Exception as e:
            logger.error(f"Voice signal error: {e}")

    def _add_call(self, path, props):
        """Process a new call."""
        raw_number = props.get("LineIdentification", "Unknown")
        number = normalize_number(raw_number)
        state = props.get("State", "unknown")

        is_silenced = False

        if state in ["incoming", "waiting"]:
            if self.db.is_blocked(number):
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

            self._check_priority_call(number)
            self._check_repeated_call_bypass(number)

        if path in self.active_calls:
            if "LineIdentification" in props:
                self.active_calls[path]["number"] = normalize_number(props["LineIdentification"])
            if "State" in props:
                self._on_call_prop_changed(None, None, "PropertyChanged", GLib.Variant("(sv)", ("State", GLib.Variant("s", props["State"]))), path)
            return

        call_proxy = self._get_proxy("org.ofono.VoiceCall", path)

        self.active_calls[path] = {
            "number": number,
            "state": state,
            "start": time.time() if state == "active" else None,
            "direction": "incoming" if state == "incoming" else "outgoing",
            "answered": (state == "active"),
            "proxy": call_proxy,
            "silenced": is_silenced
        }

        if call_proxy:
            call_proxy.connect("g-signal", self._on_call_prop_changed, path)

        self.emit('call-added', path, self.active_calls[path])

    def _on_call_prop_changed(self, proxy, sender, signal, params, path):
        """Handle property changes for a specific call."""
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
        status = "missed"
        if data["direction"] == "incoming":
            status = "incoming" if data["answered"] else "missed"
        else:
            status = "outgoing" if data["answered"] else "cancelled"
            if duration == 0:
                status = "cancelled"

        num = data.get("number")
        if not num:
            num = "Unknown"

        try:
            self.db.add_call(num, None, status, duration)
        except Exception as e:
            logger.error(f"[OfonoManager] Logging call failed: {e}")

        if status == "missed":
            self.emit('call-missed', num)

    def sync_voicemail(self):
        """Read the message-waiting state once the proxy is up.

        Reading at startup rather than waiting for a signal is what makes an
        indication that predates the app visible at all: voicemail that arrived
        while it was closed, or before a reboot, produces nothing to catch.
        """
        props = self._read_voicemail_props()
        if props is None:
            return

        if not props.get("VoicemailWaiting", False):
            GLib.timeout_add_seconds(VOICEMAIL_SETTLE_SECONDS, self._settle_voicemail)
            return

        self._apply_voicemail(props)

    def _settle_voicemail(self):
        """Re-read after a negative first reading, then trust the result."""
        props = self._read_voicemail_props()
        if props is not None:
            self._apply_voicemail(props)
        return False

    def _read_voicemail_props(self):
        """Fetch MessageWaiting properties, or None when unavailable."""
        if not self.mwi_proxy:
            return None
        try:
            ret = self.mwi_proxy.call_sync("GetProperties", None, Gio.DBusCallFlags.NONE, -1, None)
            return ret.unpack()[0]
        except Exception as e:
            logger.error(f"[OfonoManager] Voicemail read failed: {e}")
            return None

    def on_voicemail_signal(self, proxy, sender, signal, params):
        """Handle PropertyChanged from org.ofono.MessageWaiting."""
        if signal != "PropertyChanged":
            return

        try:
            name, value = params.unpack()
        except Exception as e:
            logger.debug(f"[OfonoManager] Voicemail unpack failed: {e}")
            return

        if name == "VoicemailWaiting":
            self.voicemail["waiting"] = bool(value)
        elif name == "VoicemailMessageCount":
            self.voicemail["count"] = self._voicemail_count(value)
        elif name == "VoicemailMailboxNumber":
            self.voicemail["number"] = value or None
        else:
            return

        self._emit_voicemail()

    def _apply_voicemail(self, props):
        """Fold a full property read into the cached state and publish it."""
        self.voicemail = {
            "waiting": bool(props.get("VoicemailWaiting", False)),
            "count": self._voicemail_count(props.get("VoicemailMessageCount", 0)),
            "number": props.get("VoicemailMailboxNumber") or None,
        }
        self._emit_voicemail()

    def _voicemail_count(self, value):
        """Normalise the message count, treating zero as unknown.

        A zero count alongside a set indication means "something is waiting",
        not "no messages" -- the indication commonly carries no count at all.
        Collapsing it to None keeps the UI from claiming zero new messages
        while messages are in fact waiting.
        """
        try:
            count = int(value)
        except (TypeError, ValueError):
            return None
        return count if count > 0 else None

    def _emit_voicemail(self):
        """Announce the current message-waiting state."""
        waiting = self.voicemail["waiting"]
        count = self.voicemail["count"]
        number = self.voicemail["number"]

        logger.info(f"[OfonoManager] Voicemail waiting={waiting} count={count}")
        self.emit('voicemail-changed', waiting, count or 0, count is not None, number or "")

    def on_ussd_signal(self, proxy, sender, signal, params):
        """Handle USSD signals."""
        if signal == "NotificationReceived":
            self.emit('ussd-notification', params.unpack()[0])

    def _check_priority_contact(self, sender):
        """Check if sender is a priority contact and override volume."""
        try:
            priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts()
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

    def _check_priority_call(self, number):
        """Check if caller is priority and override volume."""
        if len(self.active_calls) > 0:
            return

        try:
            priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts()
            norm_num = normalize_number(number)
            for p in priority_list:
                p_num = normalize_number(p.get("number", ""))
                if p_num and p_num == norm_num:
                    logger.info(f"[Priority] Call from {number} - forcing MAX volume")
                    self.audio.force_max_feedback()
                    self.is_volume_boosted = True
                    return
        except Exception as e:
            logger.error(f"[Priority] Call Check failed: {e}")

    def _check_repeated_call_bypass(self, number):
        """Check if repeated calls should bypass silent mode."""
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
