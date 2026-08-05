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

import datetime
import json
from gi.repository import Gio, GLib
import os
import re
from telephony.backend.utils.log_utils import logger
import uuid

from telephony.backend.utils.thread_utils import run_in_background
from telephony.backend.utils.phone_utils import get_own_number
from telephony.backend.utils.region_utils import detect_region
from telephony.backend.utils.attachment_utils import prepare_attachment, own_attachments
from telephony.constants import DAEMON_OBJECT_PATH, DAEMON_INTERFACE

MISSED_MESSAGE_BUFFER_MINUTES = 14400


def _to_variant(value):
    """Wrap a plain python value as the matching GLib.Variant."""
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, int):
        return GLib.Variant("i", value)
    if isinstance(value, float):
        return GLib.Variant("d", value)
    return GLib.Variant("s", str(value))


DAEMON_INTERFACE_XML = """
<node>
  <interface name="io.furios.Telephony.Daemon">
    <!-- Call operations -->
    <method name="Dial">
      <arg type="s" name="number" direction="in"/>
      <arg type="b" name="hide_id" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="message" direction="out"/>
    </method>
    <method name="Answer">
      <arg type="s" name="call_path" direction="in"/>
    </method>
    <method name="Hangup">
      <arg type="s" name="call_path" direction="in"/>
    </method>
    <method name="HangupAll">
    </method>
    <method name="SwapCalls">
    </method>
    <method name="SendDtmf">
      <arg type="s" name="tones" direction="in"/>
    </method>
    <method name="DeclineWithSms">
      <arg type="s" name="call_path" direction="in"/>
      <arg type="s" name="sms_text" direction="in"/>
    </method>
    <method name="MuteMic">
    </method>
    <method name="UnmuteMic">
    </method>
    <method name="SetSpeakerphone">
      <arg type="b" name="enable" direction="in"/>
    </method>


    <!-- SMS operations -->
    <method name="SendSms">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="text" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>


    <method name="ScheduleSms">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="text" direction="in"/>
      <arg type="s" name="scheduled_timestamp" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>
    <method name="ScheduleMms">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="text" direction="in"/>
      <arg type="s" name="attachments" direction="in"/>
      <arg type="s" name="scheduled_timestamp" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>
    <method name="SendMms">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="text" direction="in"/>
      <arg type="s" name="attachments_json" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>

    <method name="DeleteMessage">
      <arg type="i" name="msg_id" direction="in"/>
    </method>
    <method name="DeleteConversation">
      <arg type="s" name="number" direction="in"/>
    </method>
    <method name="MarkThreadAsRead">
      <arg type="s" name="number" direction="in"/>
    </method>
    <method name="MarkConversationUnread">
      <arg type="s" name="number" direction="in"/>
      <arg type="i" name="msg_id" direction="in"/>
    </method>
    <method name="SaveDraft">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="text" direction="in"/>
      <arg type="s" name="attachments_json" direction="in"/>
    </method>
    <method name="RescheduleMessage">
      <arg type="i" name="msg_id" direction="in"/>
      <arg type="s" name="scheduled_timestamp" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>
    <method name="RetryMessage">
      <arg type="i" name="msg_id" direction="in"/>
    </method>
    <method name="SendTrackedSms">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="text" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>
    <method name="DeleteCallHistoryEntry">
      <arg type="i" name="call_id" direction="in"/>
    </method>
    <method name="UpdateHistoryNames">
      <arg type="s" name="numbers_json" direction="in"/>
      <arg type="s" name="new_name" direction="in"/>
    </method>

    <!-- Data Retrieval (returns JSON strings for simplicity) -->

    <!-- Clear/Delete operations -->
    <method name="ClearMessages">
    </method>
    <method name="SetGroupName">
      <arg type="s" name="recipients" direction="in"/>
      <arg type="s" name="new_name" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>
    <method name="ClearGroupNames">
    </method>
    <method name="ClearBlocklist">
    </method>
    <method name="ClearContacts">
      <arg type="s" name="source_uid" direction="in"/>
    </method>

    <method name="ClearEverything">
      <arg type="s" name="source_uid" direction="in"/>
    </method>
    <method name="DeleteAddressBook">
      <arg type="s" name="source_uid" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>

    <!-- Import operations -->
    <method name="ImportChatty">
      <arg type="s" name="db_path" direction="in"/>
      <arg type="s" name="mms_path" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="msg" direction="out"/>
    </method>
    <method name="ImportLocalCalls">
      <arg type="s" name="db_path" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="msg" direction="out"/>
    </method>
    <method name="ImportAndroidSms">
      <arg type="s" name="file_path" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="msg" direction="out"/>
    </method>
    <method name="ImportAndroidCalls">
      <arg type="s" name="file_path" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="msg" direction="out"/>
    </method>
    <method name="ImportIosSms">
      <arg type="s" name="file_path" direction="in"/>
      <arg type="s" name="manifest_path" direction="in"/>
      <arg type="s" name="backup_dir" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="msg" direction="out"/>
    </method>
    <method name="ImportIosCalls">
      <arg type="s" name="file_path" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="msg" direction="out"/>
    </method>

    <method name="ClearCallHistory">
    </method>
    <method name="GetCallHistory">
      <arg type="i" name="limit" direction="in"/>
      <arg type="s" name="json_data" direction="out"/>
    </method>
    <method name="GetConversations">
      <arg type="i" name="limit" direction="in"/>
      <arg type="s" name="json_data" direction="out"/>
    </method>
    <method name="GetMessages">
      <arg type="s" name="number" direction="in"/>
      <arg type="i" name="limit" direction="in"/>
      <arg type="s" name="json_data" direction="out"/>
    </method>
    <method name="GetContacts">
      <arg type="s" name="query" direction="in"/>
      <arg type="s" name="json_data" direction="out"/>
    </method>
    <method name="GetActiveCalls">
      <arg type="s" name="json_data" direction="out"/>
    </method>


    <!-- Settings operations -->
    <method name="GetSetting">
      <arg type="s" name="key" direction="in"/>
      <arg type="s" name="value" direction="out"/>
    </method>
    <method name="SetSetting">
      <arg type="s" name="key" direction="in"/>
      <arg type="s" name="value" direction="in"/>
    </method>
    <!-- Blocklist operations -->
    <method name="GetBlocklist">
      <arg type="s" name="json_data" direction="out"/>
    </method>
    <method name="AddBlockedNumber">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="note" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>
    <method name="RemoveBlockedNumber">
      <arg type="s" name="bid" direction="in"/>
    </method>

    <!-- Scheduled Messages operations -->
    <method name="GetMissedMessages">
      <arg type="s" name="json_data" direction="out"/>
    </method>
    <method name="SendMissedMessage">
      <arg type="i" name="msg_id" direction="in"/>
    </method>

    <!-- Contact operations -->
    <method name="GetAddressBooks">
      <arg type="s" name="json_data" direction="out"/>
    </method>
    <method name="SetAddressBookPriority">
      <arg type="s" name="uid_list_json" direction="in"/>
    </method>
    <method name="ImportContacts">
      <arg type="s" name="vcard_data" direction="in"/>
      <arg type="s" name="source_uid" direction="in"/>
      <arg type="i" name="count" direction="out"/>
    </method>
    <method name="ExportContacts">
      <arg type="s" name="source_uid" direction="in"/>
      <arg type="s" name="vcard_data" direction="out"/>
    </method>
    <method name="AddContact">
      <arg type="s" name="name" direction="in"/>
      <arg type="s" name="number" direction="in"/>
    </method>
    <method name="SaveContact">
      <arg type="s" name="vcard" direction="in"/>
      <arg type="s" name="uid" direction="in"/>
      <arg type="s" name="source_uid" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>
    <method name="DeleteContacts">
      <arg type="s" name="uids_json" direction="in"/>
    </method>
    <method name="RefreshContacts">
      <arg type="i" name="count" direction="out"/>
    </method>
    <method name="ModifyContact">
      <arg type="s" name="uid" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="s" name="number" direction="in"/>
    </method>
    <method name="DeleteContact">
      <arg type="s" name="uid" direction="in"/>
    </method>

    <!-- Signals -->
    <signal name="IncomingCall">
      <arg type="s" name="call_path"/>
      <arg type="s" name="number"/>
    </signal>
    <signal name="CallChanged">
      <arg type="s" name="call_path"/>
      <arg type="s" name="state"/>
    </signal>
    <signal name="CallRemoved">
      <arg type="s" name="call_path"/>
    </signal>
    <signal name="IncomingSms">
      <arg type="s" name="number"/>
      <arg type="s" name="text"/>
    </signal>
    <signal name="MessagesChanged">
      <arg type="s" name="number"/>
      <arg type="s" name="reason"/>
    </signal>
    <signal name="ContactsChanged"/>
    <signal name="BlocklistChanged"/>
    <signal name="HistoryChanged"/>
    <method name="GetTelephonyState">
      <arg type="a{sv}" name="state" direction="out"/>
    </method>
    <method name="SetActiveChat">
      <arg type="s" name="number" direction="in"/>
    </method>
    <method name="ImportSimContacts">
      <arg type="s" name="source_uid" direction="in"/>
      <arg type="i" name="count" direction="out"/>
      <arg type="s" name="message" direction="out"/>
    </method>
    <method name="DisableAllForwarding">
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="message" direction="out"/>
    </method>
    <method name="DisableAllBarrings">
      <arg type="s" name="password" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="message" direction="out"/>
    </method>
    <method name="ChangeBarringPassword">
      <arg type="s" name="old_password" direction="in"/>
      <arg type="s" name="new_password" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="message" direction="out"/>
    </method>
    <method name="GetOwnNumber">
      <arg type="s" name="number" direction="out"/>
    </method>
    <method name="RequestRecovery">
      <arg type="b" name="success" direction="out"/>
    </method>
    <method name="ClearNotification">
      <arg type="s" name="number" direction="in"/>
    </method>
    <method name="PrepareAttachment">
      <arg type="s" name="source_path" direction="in"/>
      <arg type="i" name="max_bytes" direction="in"/>
      <arg type="s" name="path" direction="out"/>
      <arg type="s" name="message" direction="out"/>
    </method>
    <method name="SilenceRing">
    </method>
    <method name="SetAudioRoute">
      <arg type="s" name="route" direction="in"/>
    </method>
    <method name="SetInputRoute">
      <arg type="s" name="route" direction="in"/>
    </method>
    <method name="SetDeliveryReports">
      <arg type="b" name="enabled" direction="in"/>
      <arg type="b" name="success" direction="out"/>
      <arg type="s" name="message" direction="out"/>
    </method>
    <method name="GetAudioRoutes">
      <arg type="a(sb)" name="outputs" direction="out"/>
      <arg type="a(sb)" name="inputs" direction="out"/>
    </method>
    <method name="DetectRegion">
      <arg type="s" name="region" direction="out"/>
    </method>
    <signal name="NetworkServiceChanged">
      <arg type="s" name="service"/>
      <arg type="s" name="name"/>
      <arg type="s" name="value"/>
    </signal>
    <signal name="CapabilityChanged">
      <arg type="b" name="can_dial"/>
      <arg type="s" name="reason"/>
      <arg type="s" name="description"/>
    </signal>
    <signal name="ModemStateChanged">
      <arg type="a{sv}" name="state"/>
    </signal>
    <signal name="VoicemailChanged">
      <arg type="b" name="waiting"/>
      <arg type="i" name="count"/>
    </signal>
    <signal name="AudioRouteChanged">
      <arg type="a{sv}" name="state"/>
    </signal>
    <signal name="UssdReceived">
      <arg type="s" name="text"/>
    </signal>
    <signal name="RecoveryStateChanged">
      <arg type="b" name="active"/>
      <arg type="s" name="message"/>
      <arg type="b" name="failed"/>
    </signal>
    <method name="SendUssd">
      <arg type="s" name="command" direction="in"/>
      <arg type="s" name="response" direction="out"/>
    </method>
    <method name="CallAction">
      <arg type="s" name="action" direction="in"/>
      <arg type="s" name="argument" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="GetNetworkProperties">
      <arg type="s" name="service" direction="in"/>
      <arg type="a{sv}" name="properties" direction="out"/>
    </method>
    <method name="SetNetworkProperty">
      <arg type="s" name="service" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="in"/>
      <arg type="s" name="password" direction="in"/>
      <arg type="s" name="error" direction="out"/>
    </method>
    <method name="GetRecoveryState">
      <arg type="b" name="active" direction="out"/>
      <arg type="s" name="message" direction="out"/>
      <arg type="b" name="failed" direction="out"/>
    </method>
  </interface>
</node>
"""


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)


class TelephonyDaemonDBus:
    def __init__(self, app, db, ofono, eds):
        self.app = app
        self.db = db
        self.ofono = ofono
        self.eds = eds

        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.node_info = Gio.DBusNodeInfo.new_for_xml(DAEMON_INTERFACE_XML)
        self.interface_info = self.node_info.interfaces[0]

        self.reg_id = self.bus.register_object(
            DAEMON_OBJECT_PATH,
            self.interface_info,
            self._handle_method_call,
            None, None
        )
        logger.info(f"TelephonyDaemonDBus registered on Session Bus at {DAEMON_OBJECT_PATH} (id {self.reg_id})")

        if self.ofono:
            self.ofono.connect('call-added', self._on_call_added)
            self.ofono.connect('call-changed', self._on_call_changed)
            self.ofono.connect('call-removed', self._on_call_removed)
            self.ofono.connect('incoming-message', self._on_incoming_message)
            self.ofono.connect('dial-availability-changed', self._on_dial_availability_changed)
            self.ofono.connect('connection-status', self._on_modem_presence_changed)
            self.ofono.connect('modem-interface-appeared', self._on_modem_presence_changed)
            self.ofono.connect('voicemail-changed', self._on_voicemail_changed)
            self.ofono.connect('ussd-notification', self._on_ussd_received)
            self.ofono.connect('network-service-changed', self._on_network_service_changed)

        if self.app and self.app.call_audio:
            self.app.call_audio.connect('audio-state-applied',
                                        lambda *_args: self._emit_audio_route())

    def _on_call_added(self, manager, path, props):
        number = props.get("number", "Unknown")
        self.emit_signal("IncomingCall", GLib.Variant("(ss)", (path, number)))

    def _on_call_changed(self, manager, path, state):
        self.emit_signal("CallChanged", GLib.Variant("(ss)", (path, state)))

    def _on_call_removed(self, manager, path):
        self.emit_signal("CallRemoved", GLib.Variant("(s)", (path,)))

    def _on_incoming_message(self, manager, number, body):
        self.emit_signal("IncomingSms", GLib.Variant("(ss)", (number, body)))

    def _on_dial_availability_changed(self, _manager, _available):
        self._emit_capability()

    def _emit_capability(self):
        """Broadcast whether calls can be placed and why not."""
        can_dial, reason, description = self.ofono.capability_state()
        self.emit_signal("CapabilityChanged", GLib.Variant("(bss)", (can_dial, reason, description)))

    def _on_modem_presence_changed(self, _manager, *_args):
        """Broadcast the modem snapshot; presence also moves capability."""
        state = self.ofono.modem_state()
        packed = {
            "present": GLib.Variant("b", state["present"]),
            "online": GLib.Variant("b", state["online"]),
            "interfaces": GLib.Variant("as", state["interfaces"]),
            "emergency_numbers": GLib.Variant("as", state["emergency_numbers"]),
        }
        self.emit_signal("ModemStateChanged", GLib.Variant("(a{sv})", (packed,)))
        self._emit_capability()

    def _on_voicemail_changed(self, _manager, waiting, count):
        self.emit_signal("VoicemailChanged", GLib.Variant("(bi)", (waiting, count)))

    def _on_ussd_received(self, _manager, text):
        self.emit_signal("UssdReceived", GLib.Variant("(s)", (text,)))

    def _emit_audio_route(self):
        """Broadcast the applied in-call audio route."""
        audio = self.ofono.audio
        packed = {
            "speaker": GLib.Variant("b", audio.current_route == "speaker"),
            "mic_muted": GLib.Variant("b", audio.mic_muted),
            "route": GLib.Variant("s", audio.current_route),
            "input": GLib.Variant("s", audio.current_input),
        }
        self.emit_signal("AudioRouteChanged", GLib.Variant("(a{sv})", (packed,)))

    def _on_network_service_changed(self, _manager, service, name, value):
        self.emit_signal("NetworkServiceChanged", GLib.Variant("(sss)", (service, name, str(value))))

    def _handle_getownnumber(self, parameters, invocation):
        """Read the subscriber's own number for a window instance."""
        run_in_background(get_own_number,
                          on_complete=lambda num: invocation.return_value(GLib.Variant("(s)", (num or "",))))

    def _handle_detectregion(self, parameters, invocation):
        """Detect the network region for a window instance."""
        run_in_background(detect_region,
                          on_complete=lambda region: invocation.return_value(GLib.Variant("(s)", (region or "",))))

    def _handle_requestrecovery(self, parameters, invocation):
        """Run modem recovery for a window instance; the reply is the verdict."""
        state = {"resolved": False}

        def resolve(success):
            if state["resolved"]:
                return
            state["resolved"] = True
            invocation.return_value(GLib.Variant("(b)", (success,)))

        started = self.app.request_auto_recovery(on_done=resolve)
        if not started:
            resolve(False)

    def _handle_clearnotification(self, parameters, invocation):
        """Withdraw the notifications a window says the user has seen."""
        number = parameters.unpack()[0]
        self.app.clear_notification(number)
        invocation.return_value(None)

    def _handle_prepareattachment(self, parameters, invocation):
        """Store an attachment and make it fit the caller's byte budget."""
        source_path, max_bytes = parameters.unpack()

        def task():
            return prepare_attachment(source_path, max_bytes)

        def done(result):
            path, message = result if result else (None, "error")
            invocation.return_value(GLib.Variant("(ss)", (path or "", message)))

        run_in_background(task, on_complete=done)

    def _handle_sendussd(self, params, invocation):
        """Run a USSD request for a window instance and hand back the reply."""
        command = params.unpack()[0]

        def done(response):
            invocation.return_value(GLib.Variant("(s)", (response or "",)))

        def failed(error):
            logger.error(f"[Daemon] USSD request failed: {error}")
            invocation.return_value(GLib.Variant("(s)", ("",)))

        run_in_background(self.ofono.send_ussd, command, on_complete=done, on_error=failed)

    def _handle_callaction(self, params, invocation):
        """Run a call control action a window instance asked for."""
        action, argument = params.unpack()
        actions = {
            "create_multiparty": lambda: self.ofono.create_multiparty(),
            "hangup_multiparty": lambda: self.ofono.hangup_multiparty(),
            "transfer": lambda: self.ofono.transfer_call(),
            "private_chat": lambda: self.ofono.private_chat(argument),
        }
        handler = actions.get(action)
        if handler is None:
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return

        def done(result):
            invocation.return_value(GLib.Variant("(b)", (bool(result and result[0]),)))

        def failed(error):
            logger.error(f"[Daemon] Call action {action} failed: {error}")
            invocation.return_value(GLib.Variant("(b)", (False,)))

        run_in_background(handler, on_complete=done, on_error=failed)

    def _handle_getnetworkproperties(self, params, invocation):
        """Read a supplementary service for a window instance."""
        service = params.unpack()[0]

        def done(props):
            packed = {k: GLib.Variant("s", str(v)) for k, v in (props or {}).items()}
            invocation.return_value(GLib.Variant("(a{sv})", (packed,)))

        def failed(error):
            logger.error(f"[Daemon] Network property read failed: {error}")
            invocation.return_value(GLib.Variant("(a{sv})", ({},)))

        run_in_background(self.ofono.get_service_properties, service,
                          on_complete=done, on_error=failed)

    def _handle_setnetworkproperty(self, params, invocation):
        """Change a supplementary service for a window instance."""
        service, name, value, password = params.unpack()

        def task():
            if service == "barring" and password:
                return self.ofono.set_barring_property(name, value, password)
            return self.ofono.set_service_property(service, name, value)

        def done(result):
            ok, error = result if result else (False, "no reply")
            invocation.return_value(GLib.Variant("(s)", ("" if ok else (error or "failed"),)))

        def failed(error):
            logger.error(f"[Daemon] Network property write failed: {error}")
            invocation.return_value(GLib.Variant("(s)", (str(error),)))

        run_in_background(task, on_complete=done, on_error=failed)

    def _handle_getrecoverystate(self, parameters, invocation):
        """Report the recovery state to a call window that just started.

        The window can start after the state changed, so the signal
        alone would leave it showing nothing.
        """
        active, message, failed = self.app.recovery_state
        invocation.return_value(GLib.Variant("(bsb)", (active, message, failed)))

    def _handle_gettelephonystate(self, parameters, invocation):
        """Assemble the full snapshot a client seeds its mirror from.

        Everything the signals report incrementally is here in one read,
        so a client that subscribes first and seeds second misses nothing.
        """
        can_dial, reason, description = self.ofono.capability_state()
        modem = self.ofono.modem_state()
        audio = self.ofono.audio
        recovery_active, recovery_message, recovery_failed = self.app.recovery_state
        calls = {path: GLib.Variant("a{sv}", {k: _to_variant(v) for k, v in props.items()})
                 for path, props in self.ofono.calls_snapshot().items()}
        state = {
            "calls": GLib.Variant("a{sv}", calls),
            "can_dial": GLib.Variant("b", can_dial),
            "dial_reason": GLib.Variant("s", reason),
            "dial_description": GLib.Variant("s", description),
            "modem_present": GLib.Variant("b", modem["present"]),
            "modem_online": GLib.Variant("b", modem["online"]),
            "interfaces": GLib.Variant("as", modem["interfaces"]),
            "emergency_numbers": GLib.Variant("as", modem["emergency_numbers"]),
            "voicemail_waiting": GLib.Variant("b", self.ofono.voicemail_waiting),
            "voicemail_count": GLib.Variant("i", self.ofono.voicemail_count),
            "voicemail_mailbox": GLib.Variant("s", self.ofono.voicemail_mailbox),
            "speaker": GLib.Variant("b", audio.current_route == "speaker"),
            "mic_muted": GLib.Variant("b", audio.mic_muted),
            "route": GLib.Variant("s", audio.current_route),
            "input": GLib.Variant("s", audio.current_input),
            "recovery_active": GLib.Variant("b", recovery_active),
            "recovery_message": GLib.Variant("s", recovery_message),
            "recovery_failed": GLib.Variant("b", recovery_failed),
        }
        invocation.return_value(GLib.Variant("(a{sv})", (state,)))

    def _handle_setactivechat(self, parameters, invocation):
        """Handle SetActiveChat command; an empty number clears it."""
        number = parameters.unpack()[0]
        self.ofono.set_active_chat(number if number else None)
        invocation.return_value(None)

    def _reply_ss_result(self, invocation, result):
        """Answer a supplementary-service request with its worker result."""
        ok, error = result if result else (False, "no reply")
        invocation.return_value(GLib.Variant("(bs)", (ok, error or "")))

    def _handle_disableallforwarding(self, parameters, invocation):
        """Handle DisableAllForwarding command."""
        run_in_background(self.ofono.disable_all_forwarding,
                          on_complete=lambda result: self._reply_ss_result(invocation, result))

    def _handle_disableallbarrings(self, parameters, invocation):
        """Handle DisableAllBarrings command."""
        password = parameters.unpack()[0]
        run_in_background(self.ofono.disable_all_barrings, password,
                          on_complete=lambda result: self._reply_ss_result(invocation, result))

    def _handle_changebarringpassword(self, parameters, invocation):
        """Handle ChangeBarringPassword command."""
        old, new = parameters.unpack()
        run_in_background(self.ofono.change_barring_password, old, new,
                          on_complete=lambda result: self._reply_ss_result(invocation, result))

    def _handle_importsimcontacts(self, parameters, invocation):
        """Read the SIM phonebook and import its vcards for a window instance."""
        source_uid = parameters.unpack()[0]

        if self._is_protected_source(source_uid, "[DBus] Refusing SIM import to {name}"):
            invocation.return_value(GLib.Variant("(is)", (-1, "protected-book")))
            return

        def task():
            return self.ofono.import_sim_contacts()

        def done(vcard_data):
            if vcard_data is None:
                invocation.return_value(GLib.Variant("(is)", (-1, "sim-read-failed")))
                return
            vcards = re.findall(r'BEGIN:VCARD.*?END:VCARD', vcard_data, re.DOTALL)
            if not vcards:
                invocation.return_value(GLib.Variant("(is)", (0, "empty")))
                return
            count = 0
            for vcard in vcards:
                if self.eds.save_contact(vcard, source_uid=source_uid if source_uid else None):
                    count += 1
            invocation.return_value(GLib.Variant("(is)", (count, "")))

        run_in_background(task, on_complete=done)

    def emit_signal(self, signal_name, parameters):
        self.bus.emit_signal(
            None,
            DAEMON_OBJECT_PATH,
            DAEMON_INTERFACE,
            signal_name,
            parameters
        )

    def _handle_method_call(self, connection, sender, object_path, interface_name, method_name, parameters, invocation):
        """Route incoming D-Bus method calls to the appropriate handler."""
        handlers = {
            "Dial": self._handle_dial,
            "Answer": self._handle_answer,
            "Hangup": self._handle_hangup,
            "HangupAll": self._handle_hangupall,
            "SwapCalls": self._handle_swapcalls,
            "MuteMic": self._handle_mutemic,
            "UnmuteMic": self._handle_unmutemic,
            "SetSpeakerphone": self._handle_setspeakerphone,
            "SendDtmf": self._handle_senddtmf,
            "DeclineWithSms": self._handle_declinewithsms,
            "ScheduleSms": self._handle_schedulesms,
            "ScheduleMms": self._handle_schedulemms,
            "SendSms": self._handle_sendsms,
            "SendUssd": self._handle_sendussd,
            "CallAction": self._handle_callaction,
            "GetNetworkProperties": self._handle_getnetworkproperties,
            "SetNetworkProperty": self._handle_setnetworkproperty,
            "GetRecoveryState": self._handle_getrecoverystate,
            "GetTelephonyState": self._handle_gettelephonystate,
            "SetActiveChat": self._handle_setactivechat,
            "ImportSimContacts": self._handle_importsimcontacts,
            "DisableAllForwarding": self._handle_disableallforwarding,
            "DisableAllBarrings": self._handle_disableallbarrings,
            "ChangeBarringPassword": self._handle_changebarringpassword,
            "GetOwnNumber": self._handle_getownnumber,
            "DetectRegion": self._handle_detectregion,
            "RequestRecovery": self._handle_requestrecovery,
            "ClearNotification": self._handle_clearnotification,
            "PrepareAttachment": self._handle_prepareattachment,
            "SilenceRing": self._handle_silencering,
            "SetAudioRoute": self._handle_setaudioroute,
            "SetInputRoute": self._handle_setinputroute,
            "SetDeliveryReports": self._handle_setdeliveryreports,
            "GetAudioRoutes": self._handle_getaudioroutes,
            "DeleteMessage": self._handle_deletemessage,
            "DeleteConversation": self._handle_deleteconversation,
            "MarkThreadAsRead": self._handle_markthreadasread,
            "MarkConversationUnread": self._handle_markconversationunread,
            "SaveDraft": self._handle_savedraft,
            "RescheduleMessage": self._handle_reschedulemessage,
            "RetryMessage": self._handle_retrymessage,
            "SendTrackedSms": self._handle_sendtrackedsms,
            "DeleteCallHistoryEntry": self._handle_deletecallhistoryentry,
            "UpdateHistoryNames": self._handle_updatehistorynames,
            "ClearMessages": self._handle_clearmessages,
            "SetGroupName": self._handle_setgroupname,
            "ClearGroupNames": self._handle_cleargroupnames,
            "ClearBlocklist": self._handle_clearblocklist,
            "ClearContacts": self._handle_clearcontacts,

            "ClearEverything": self._handle_cleareverything,
            "DeleteAddressBook": self._handle_deleteaddressbook,
            "ImportChatty": self._handle_importchatty,
            "ImportLocalCalls": self._handle_importlocalcalls,
            "ImportAndroidSms": self._handle_importandroidsms,
            "ImportAndroidCalls": self._handle_importandroidcalls,
            "ImportIosSms": self._handle_importiossms,
            "ImportIosCalls": self._handle_importioscalls,
            "ClearCallHistory": self._handle_clearcallhistory,
            "GetCallHistory": self._handle_getcallhistory,
            "GetConversations": self._handle_getconversations,
            "GetMessages": self._handle_getmessages,
            "GetContacts": self._handle_getcontacts,
            "GetActiveCalls": self._handle_getactivecalls,
            "SendMms": self._handle_sendmms,
            "GetSetting": self._handle_getsetting,
            "SetSetting": self._handle_setsetting,
            "GetBlocklist": self._handle_getblocklist,
            "AddBlockedNumber": self._handle_addblockednumber,
            "RemoveBlockedNumber": self._handle_removeblockednumber,
            "GetMissedMessages": self._handle_getmissedmessages,
            "SendMissedMessage": self._handle_sendmissedmessage,
            "GetAddressBooks": self._handle_getaddressbooks,
            "SetAddressBookPriority": self._handle_setaddressbookpriority,
            "ImportContacts": self._handle_importcontacts,
            "ExportContacts": self._handle_exportcontacts,
            "AddContact": self._handle_addcontact,
            "SaveContact": self._handle_savecontact,
            "DeleteContact": self._handle_deletecontact,
            "DeleteContacts": self._handle_deletecontacts,
            "RefreshContacts": self._handle_refreshcontacts,
            "ModifyContact": self._handle_modifycontact,
        }
        handler = handlers.get(method_name)
        if handler:
            try:
                logger.info(f"DBus Method Call: {method_name} from {sender}")
                handler(parameters, invocation)
            except Exception as e:
                logger.error(f"[DBusService] Exception handling {method_name}: {e}")
                invocation.return_dbus_error("org.telephony.Error.Failed", str(e))
        else:
            logger.warning(f"[DBusService] Unhandled method: {method_name}")
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", f"Method {method_name} is not implemented")

    def _handle_dial(self, parameters, invocation):
        """Handle Dial command; the reply waits for the real outcome."""
        number, hide_id = parameters.unpack()
        state = {"resolved": False}

        def on_result(success, message):
            if state["resolved"]:
                return
            state["resolved"] = True
            invocation.return_value(GLib.Variant("(bs)", (success, message)))

        def do_dial():
            try:
                self.ofono.dial(number, hide_id=hide_id, on_result=on_result)
            except Exception as e:
                logger.error(f"[DBusService] Dial failed: {e}")
                on_result(False, str(e))

        GLib.idle_add(do_dial)

    def _handle_answer(self, parameters, invocation):
        """Handle Answer command."""
        call_path = parameters.unpack()[0]
        if self.ofono:
            if not call_path:
                for p, data in self.ofono.active_calls.items():
                    if data.get("state") in ["incoming", "waiting"]:
                        call_path = p
                        break
            if call_path:
                self.ofono.answer_call(call_path)
        invocation.return_value(None)

    def _handle_hangup(self, parameters, invocation):
        """Handle Hangup command."""
        call_path = parameters.unpack()[0]
        if self.ofono:
            if not call_path:
                for p, data in self.ofono.active_calls.items():
                    call_path = p
                    break
            if call_path:
                self.ofono.hangup_call(call_path)
        invocation.return_value(None)

    def _handle_hangupall(self, parameters, invocation):
        """Handle HangupAll command."""
        if self.ofono:
            self.ofono.hangup_all()
        invocation.return_value(None)

    def _handle_swapcalls(self, parameters, invocation):
        """Handle SwapCalls command."""
        if self.ofono:
            self.ofono.swap_calls()
        invocation.return_value(None)

    def _handle_mutemic(self, parameters, invocation):
        """Handle MuteMic command."""
        self.app.call_audio.set_mic_muted(True)
        invocation.return_value(None)

    def _handle_unmutemic(self, parameters, invocation):
        """Handle UnmuteMic command."""
        self.app.call_audio.set_mic_muted(False)
        invocation.return_value(None)

    def _handle_setspeakerphone(self, parameters, invocation):
        """Handle SetSpeakerphone command."""
        enable = parameters.unpack()[0]
        self.app.call_audio.set_route("speaker" if enable else "earpiece")
        invocation.return_value(None)

    def _handle_silencering(self, parameters, invocation):
        """Handle SilenceRing command."""
        self.app.call_audio.silence_ring()
        invocation.return_value(None)

    def _handle_setaudioroute(self, parameters, invocation):
        """Handle SetAudioRoute command."""
        route = parameters.unpack()[0]
        self.app.call_audio.set_route(route)
        invocation.return_value(None)

    def _handle_setinputroute(self, parameters, invocation):
        """Handle SetInputRoute command."""
        route = parameters.unpack()[0]
        self.app.call_audio.set_input(route)
        invocation.return_value(None)

    def _handle_setdeliveryreports(self, parameters, invocation):
        """Ask the network for SMS delivery reports for a window instance."""
        enabled = parameters.unpack()[0]
        run_in_background(self.ofono.set_delivery_reports, enabled,
                          on_complete=lambda result: self._reply_ss_result(invocation, result))

    def _handle_getaudioroutes(self, parameters, invocation):
        """List the selectable output and input routes for a window."""
        def fetch():
            outputs = [(r['id'], bool(r.get('available', True)))
                       for r in self.ofono.audio.get_available_outputs()]
            inputs = [(r['id'], bool(r.get('available', True)))
                      for r in self.ofono.audio.get_available_inputs()]
            return (outputs, inputs)

        def done(result):
            outputs, inputs = result if result else ([], [])
            invocation.return_value(GLib.Variant("(a(sb)a(sb))", (outputs, inputs)))

        run_in_background(fetch, on_complete=done)

    def _handle_senddtmf(self, parameters, invocation):
        """Handle SendDtmf command."""
        tones = parameters.unpack()[0]
        if self.ofono:
            self.ofono.send_dtmf(tones)
        invocation.return_value(None)

    def _handle_declinewithsms(self, parameters, invocation):
        """Handle DeclineWithSms command."""
        call_path, sms_text = parameters.unpack()
        if self.ofono:
            if not call_path:
                for p, data in self.ofono.active_calls.items():
                    call_path = p
                    break
            if call_path:
                call_info = self.ofono.active_calls.get(call_path)
                number = call_info.get('number') if call_info else None
                self.ofono.hangup_call(call_path)
                if number and sms_text:
                    self.ofono.send_quick_response(number, sms_text)
        invocation.return_value(None)

    def _normalize_schedule_timestamp(self, ts):
        """Return the timestamp in the scheduler's format, or None when invalid."""
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.datetime.strptime(ts, fmt).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError as e:
                logger.debug(f"[DBus] Schedule timestamp {ts} does not match {fmt}: {e}")
        return None

    def _handle_schedulesms(self, parameters, invocation):
        """Handle ScheduleSms command."""
        number, text, scheduled_timestamp = parameters.unpack()
        success = False
        scheduled_timestamp = self._normalize_schedule_timestamp(scheduled_timestamp)
        if scheduled_timestamp and self.app and self.app.scheduler:
            row_id = self.db.add_message(number, 'outgoing', text, status='scheduled', subject=None, attachments=[], sender="Me", scheduled_timestamp=scheduled_timestamp)
            self.app.scheduler.add_cron(row_id, scheduled_timestamp)
            success = True
        invocation.return_value(GLib.Variant("(b)", (success,)))

    def _handle_schedulemms(self, parameters, invocation):
        """Handle ScheduleMms command."""
        number, text, attachments_json, scheduled_timestamp = parameters.unpack()
        attachments = []
        try:
            if attachments_json:
                attachments = own_attachments(json.loads(attachments_json))
        except Exception as e:
            logger.warning(f"Failed to parse attachments: {e}")

        success = False
        scheduled_timestamp = self._normalize_schedule_timestamp(scheduled_timestamp)
        if scheduled_timestamp and self.app and self.app.scheduler:
            row_id = self.db.add_message(number, 'outgoing', text, status='scheduled', subject=None, attachments=attachments, sender="Me", scheduled_timestamp=scheduled_timestamp)
            self.app.scheduler.add_cron(row_id, scheduled_timestamp)
            success = True
        invocation.return_value(GLib.Variant("(b)", (success,)))

    def _handle_sendsms(self, parameters, invocation):
        """Handle SendSms command."""
        number, text = parameters.unpack()
        success = False
        if self.ofono:
            row_id = self.db.add_message(number, 'outgoing', text, status='draft', subject=None, attachments=[], sender="Me")
            success = self.ofono.send_sms(number, text)
            if success:
                self.db.update_message_schedule(row_id, status="sent", timestamp=None)
        invocation.return_value(GLib.Variant("(b)", (success,)))

    def _handle_deletemessage(self, parameters, invocation):
        """Handle DeleteMessage command."""
        msg_id = parameters.unpack()[0]
        if self.db:
            self.db.delete_message(msg_id)
        if self.app and self.app.scheduler:
            self.app.scheduler.remove_cron(msg_id)
        invocation.return_value(None)

    def _handle_deleteconversation(self, parameters, invocation):
        """Handle DeleteConversation command."""
        number = parameters.unpack()[0]
        if self.db:
            self.db.delete_conversation(number)
        if self.app and self.app.scheduler:
            self.app.scheduler.schedule_next_run()
        invocation.return_value(None)

    def _handle_markthreadasread(self, parameters, invocation):
        """Handle MarkThreadAsRead command."""
        number = parameters.unpack()[0]
        if self.db:
            self.db.mark_conversation_read(number)
        invocation.return_value(None)

    def _handle_markconversationunread(self, parameters, invocation):
        """Handle MarkConversationUnread command."""
        number, msg_id = parameters.unpack()
        if not self.db:
            invocation.return_value(None)
            return
        self._run_task_then_reply(
            invocation, lambda: self.db.mark_conversation_unread_from_message(number, msg_id))

    def _handle_savedraft(self, parameters, invocation):
        """Replace the stored draft for a conversation.

        An empty text with no attachments just clears the draft, which
        is what a window asks for right before it hands off a send.
        """
        number, text, attachments_json = parameters.unpack()
        attachments = []
        try:
            if attachments_json:
                attachments = json.loads(attachments_json)
        except Exception as e:
            logger.warning(f"[DBus] Failed to parse draft attachments: {e}")

        def task():
            self.db.delete_drafts(number)
            if text or attachments:
                self.db.add_message(number, 'outgoing', text, status='draft',
                                    subject=None, attachments=attachments, sender="Me")

        if not self.db:
            invocation.return_value(None)
            return
        self._run_task_then_reply(invocation, task)

    def _handle_reschedulemessage(self, parameters, invocation):
        """Handle RescheduleMessage command."""
        msg_id, scheduled_timestamp = parameters.unpack()
        success = False
        scheduled_timestamp = self._normalize_schedule_timestamp(scheduled_timestamp)
        if scheduled_timestamp and self.db:
            success = bool(self.db.update_message_schedule(
                msg_id, status="scheduled", timestamp=scheduled_timestamp))
            if success and self.app and self.app.scheduler:
                self.app.scheduler.add_cron(msg_id, scheduled_timestamp)
        invocation.return_value(GLib.Variant("(b)", (success,)))

    def _handle_retrymessage(self, parameters, invocation):
        """Resend a failed message for a window instance, keeping its row."""
        msg_id = parameters.unpack()[0]

        def task():
            details = self.db.get_message_details(msg_id)
            if not details:
                logger.warning(f"[DBus] Retry asked for unknown message {msg_id}")
                return
            _mid, number, body, subject, attachments_json, _status = details
            attachments = []
            try:
                if attachments_json:
                    attachments = json.loads(attachments_json)
            except Exception as e:
                logger.warning(f"[DBus] Failed to parse retry attachments: {e}")

            self.db.update_message_status(msg_id, "sending")
            is_group = "," in number
            if is_group or attachments or subject:
                if self.app and self.app.mms:
                    targets = [n.strip() for n in number.split(",")] if is_group else [number]
                    self.app.mms.send_mms_tracked(targets, body, attachments, msg_id)
                else:
                    self.db.update_message_status(msg_id, "failed")
            elif self.ofono:
                self.ofono.send_sms_tracked(number, body, msg_id)
            else:
                self.db.update_message_status(msg_id, "failed")

        if not self.db:
            invocation.return_value(None)
            return
        self._run_task_then_reply(invocation, task)

    def _handle_sendtrackedsms(self, parameters, invocation):
        """Record an SMS and send it with delivery tracking for a window."""
        number, text = parameters.unpack()

        def done(ok):
            invocation.return_value(GLib.Variant("(b)", (bool(ok),)))

        def failed(error):
            logger.error(f"[DBus] Tracked SMS send failed: {error}")
            invocation.return_value(GLib.Variant("(b)", (False,)))

        if not self.ofono:
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return
        run_in_background(self.ofono.send_quick_response, number, text,
                          on_complete=done, on_error=failed)

    def _handle_deletecallhistoryentry(self, parameters, invocation):
        """Handle DeleteCallHistoryEntry command."""
        call_id = parameters.unpack()[0]
        if self.db:
            self.db.delete_call_by_id(call_id)
        invocation.return_value(None)

    def _handle_updatehistorynames(self, parameters, invocation):
        """Handle UpdateHistoryNames command."""
        numbers_json, new_name = parameters.unpack()
        numbers = []
        try:
            if numbers_json:
                numbers = json.loads(numbers_json)
        except Exception as e:
            logger.warning(f"[DBus] Failed to parse history numbers: {e}")

        if not numbers or not self.db:
            invocation.return_value(None)
            return
        self._run_task_then_reply(
            invocation, lambda: self.db.update_history_names(numbers, new_name=new_name or None))

    def _handle_clearmessages(self, parameters, invocation):
        """Handle ClearMessages command."""
        if self.db:
            self.db.clear_messages()
        invocation.return_value(None)

    def _handle_setgroupname(self, parameters, invocation):
        """Handle SetGroupName command."""
        recipients, new_name = parameters.unpack()
        success = False
        if self.db:
            recipients_list = [r.strip() for r in recipients.split(",") if r.strip()]
            if recipients_list:
                success = bool(self.db.set_group_name(recipients_list, new_name))
        invocation.return_value(GLib.Variant("(b)", (success,)))

    def _handle_cleargroupnames(self, parameters, invocation):
        """Handle ClearGroupNames command."""
        if self.db:
            self.db.clear_group_names()
        invocation.return_value(None)

    def _handle_clearblocklist(self, parameters, invocation):
        """Handle ClearBlocklist command."""
        if self.db:
            self.db.clear_blocklist()
        invocation.return_value(None)

    def _is_protected_source(self, source_uid, log_template):
        """Return True when the source must not be modified via CLI.

        log_template must contain a {name} placeholder which is filled with
        the protected source name for the refusal warning.
        """
        if source_uid == "system-address-book":
            logger.warning(log_template.format(name="system-address-book"))
            return True
        if not self.eds:
            return False
        sources = self.eds.get_sources_info()
        for s in sources:
            if s.get('uid') == source_uid and s.get('name') == "Andromeda Contacts":
                logger.warning(log_template.format(name="Andromeda Contacts"))
                return True
        return False

    def _is_protected_contact_source(self, source_uid):
        """Return True when a contact's source is protected, without logging."""
        if source_uid == "system-address-book":
            return True
        with self.eds.sources_lock:
            info = self.eds.sources.get(source_uid)
        return bool(info) and info.get('name') == "Andromeda Contacts"

    def _is_protected_contact(self, contact, uid, verb):
        """Return True when the contact lives in a protected source.

        Logs the exact per-verb refusal warning used by the CLI handlers.
        """
        s_uid = contact.get('source_uid')
        if s_uid == "system-address-book":
            logger.warning(f"[DBus] Refusing to {verb} system-address-book Contact {uid} via CLI")
            return True
        if self._is_protected_contact_source(s_uid):
            logger.warning(f"[DBus] Refusing to {verb} Andromeda Contact {uid} via CLI")
            return True
        return False

    def _delete_unprotected_contacts(self):
        """Delete every cached contact that is not in a protected source."""
        with self.eds.cache_lock:
            cached_items = list(self.eds.cache.items())

        uids_to_delete = []
        for uid, contact in cached_items:
            if not self._is_protected_contact_source(contact.get('source_uid')):
                uids_to_delete.append(uid)

        for uid in uids_to_delete:
            self.eds.delete_contact(uid)

    def _clear_contacts_task(self, source_uid, is_protected):
        """Delete contacts per the source filter; runs on a worker thread."""
        if not self.eds:
            return
        if not source_uid:
            self._delete_unprotected_contacts()
        elif not is_protected:
            self.eds.delete_all_contacts(source_uid=source_uid)

    def _run_task_then_reply(self, invocation, task):
        """Run task off the main thread, then reply to the invocation."""
        def done(_result):
            invocation.return_value(None)

        def failed(error):
            logger.error(f"[DBus] Background task failed: {error}")
            invocation.return_value(None)

        run_in_background(task, on_complete=done, on_error=failed)

    def _handle_clearcontacts(self, parameters, invocation):
        """Handle ClearContacts command."""
        source_uid = parameters.unpack()[0]
        is_protected = self._is_protected_source(source_uid, "[DBus] Refusing to clear {name} via CLI")
        self._run_task_then_reply(invocation, lambda: self._clear_contacts_task(source_uid, is_protected))

    def _handle_cleareverything(self, parameters, invocation):
        """Handle ClearEverything command."""
        source_uid = parameters.unpack()[0]
        is_protected = self._is_protected_source(source_uid, "[DBus] Refusing to clear {name} via CLI")

        def task():
            if self.db:
                self.db.clear_everything()
            self._clear_contacts_task(source_uid, is_protected)
            try:
                cfg = os.path.join(GLib.get_user_config_dir(), "telephony.json")
                if os.path.exists(cfg):
                    os.remove(cfg)
            except Exception as e:
                logger.warning(f"[DBus] Config removal failed: {e}")

        self._run_task_then_reply(invocation, task)

    def _handle_deleteaddressbook(self, parameters, invocation):
        """Handle DeleteAddressBook command."""
        source_uid = parameters.unpack()[0]
        success = False

        is_protected = self._is_protected_source(source_uid, "[DBus] Refusing to delete {name}")

        if not is_protected and self.eds:
            success = self.eds.delete_addressbook(source_uid)
        invocation.return_value(GLib.Variant("(b)", (success,)))

    def _run_import(self, invocation, task):
        """Run an importer off the main thread and reply with its result."""
        def done(result):
            success, msg = result
            invocation.return_value(GLib.Variant("(bs)", (success, msg)))

        def failed(error):
            logger.error(f"[DBus] Import failed: {error}")
            invocation.return_value(GLib.Variant("(bs)", (False, str(error))))

        if not self.db:
            invocation.return_value(GLib.Variant("(bs)", (False, "")))
            return
        run_in_background(task, on_complete=done, on_error=failed)

    def _handle_importchatty(self, parameters, invocation):
        """Handle ImportChatty command."""
        from telephony.backend.utils.importer_local_utils import import_local_chatty
        db_path, mms_path = parameters.unpack()
        self._run_import(invocation, lambda: import_local_chatty(self.db, db_path, mms_path))

    def _handle_importlocalcalls(self, parameters, invocation):
        """Handle ImportLocalCalls command."""
        from telephony.backend.utils.importer_local_utils import import_local_calls
        db_path = parameters.unpack()[0]
        self._run_import(invocation, lambda: import_local_calls(self.db, db_path))

    def _handle_importandroidsms(self, parameters, invocation):
        """Handle ImportAndroidSms command."""
        from telephony.backend.utils.importer_android_utils import import_android_sms
        file_path = parameters.unpack()[0]
        self._run_import(invocation, lambda: import_android_sms(self.db, file_path))

    def _handle_importandroidcalls(self, parameters, invocation):
        """Handle ImportAndroidCalls command."""
        from telephony.backend.utils.importer_android_utils import import_android_calls
        file_path = parameters.unpack()[0]
        self._run_import(invocation, lambda: import_android_calls(self.db, file_path))

    def _handle_importiossms(self, parameters, invocation):
        """Handle ImportIosSms command."""
        from telephony.backend.utils.importer_ios_utils import import_ios_sms
        file_path, manifest_path, backup_dir = parameters.unpack()
        self._run_import(invocation, lambda: import_ios_sms(
            self.db, file_path, manifest_path or None, backup_dir or None))

    def _handle_importioscalls(self, parameters, invocation):
        """Handle ImportIosCalls command."""
        from telephony.backend.utils.importer_ios_utils import import_ios_calls
        file_path = parameters.unpack()[0]
        self._run_import(invocation, lambda: import_ios_calls(self.db, file_path))

    def _handle_clearcallhistory(self, parameters, invocation):
        """Handle ClearCallHistory command."""
        if self.db:
            self.db.clear_history()
        invocation.return_value(None)

    def _handle_getcallhistory(self, parameters, invocation):
        """Handle GetCallHistory command."""
        limit = parameters.unpack()[0]
        rows = self.db.get_history(limit=limit) if self.db else []
        data = [{"id": r[0], "number": r[1], "name": r[2], "direction": r[3], "duration": r[4], "timestamp": r[5]} for r in rows]
        invocation.return_value(GLib.Variant("(s)", (json.dumps(data, cls=DateTimeEncoder),)))

    def _handle_getconversations(self, parameters, invocation):
        """Handle GetConversations command."""
        limit = parameters.unpack()[0]
        rows = self.db.get_conversations(limit=limit) if self.db else []
        data = [{"id": r[4], "number": r[0], "direction": "unknown", "body": r[1], "timestamp": r[2], "unread_count": r[3]} for r in rows]
        invocation.return_value(GLib.Variant("(s)", (json.dumps(data, cls=DateTimeEncoder),)))

    def _handle_getmessages(self, parameters, invocation):
        """Handle GetMessages command."""
        number, limit = parameters.unpack()
        rows = self.db.get_chat_messages(number, limit=limit) if self.db else []
        data = [{"id": r[0], "direction": r[1], "body": r[2], "status": r[4], "timestamp": r[3]} for r in rows]
        invocation.return_value(GLib.Variant("(s)", (json.dumps(data, cls=DateTimeEncoder),)))

    def _handle_getcontacts(self, parameters, invocation):
        """Handle GetContacts command."""
        query = parameters.unpack()[0]
        rows = self.db.search_contacts(query) if self.db else []
        data = [{"uid": r[0], "name": f"{r[1]} {r[2]}".strip(), "numbers": [p[0] for p in r[3]] if r[3] else []} for r in rows]
        invocation.return_value(GLib.Variant("(s)", (json.dumps(data, cls=DateTimeEncoder),)))

    def _handle_getactivecalls(self, parameters, invocation):
        """Handle GetActiveCalls command."""
        calls = []
        if self.ofono:
            for path, data in self.ofono.active_calls.items():
                calls.append({"path": path, "number": data.get("number"), "state": data.get("state"), "direction": data.get("direction")})
        invocation.return_value(GLib.Variant("(s)", (json.dumps(calls, cls=DateTimeEncoder),)))

    def _handle_sendmms(self, parameters, invocation):
        """Handle SendMms command."""
        number, text, attachments_json = parameters.unpack()
        attachments = []
        try:
            if attachments_json:
                attachments = own_attachments(json.loads(attachments_json))
        except Exception as e:
            logger.warning(f"Failed to parse attachments: {e}")

        success = False
        if self.app and self.app.mms:
            numbers = [n.strip() for n in number.split(',') if n.strip()]
            row_id = self.db.add_message(number, 'outgoing', text, status='sending', subject=None, attachments=attachments, sender="Me")
            self.app.mms.send_mms_tracked(numbers, text, attachments, row_id)
            success = True
        invocation.return_value(GLib.Variant("(b)", (success,)))

    def _handle_getsetting(self, parameters, invocation):
        """Handle GetSetting command."""
        key = parameters.unpack()[0]
        value = ""
        if self.app and self.app.gsettings_mgr:
            val = self.app.gsettings_mgr.get_setting(key)
            if val is not None:
                if isinstance(val, (list, dict, bool)):
                    value = json.dumps(val)
                else:
                    value = str(val)
        invocation.return_value(GLib.Variant("(s)", (value,)))

    def _handle_setsetting(self, parameters, invocation):
        """Handle SetSetting command."""
        key, value = parameters.unpack()
        key = key.replace("_", "-")
        if self.app and self.app.gsettings_mgr:
            schema = self.app.gsettings_mgr.gsettings.get_property('settings-schema')
            if not schema or not schema.has_key(key):
                invocation.return_error_literal(Gio.dbus_error_quark(), Gio.DBusError.INVALID_ARGS, f"Unknown setting: {key}")
                return
            key_type = schema.get_key(key).get_value_type().dup_string()

            try:
                if key_type == 'b':
                    parsed_val = value.lower() in ['true', '1', 'yes']
                    self.app.gsettings_mgr.gsettings.set_boolean(key, parsed_val)
                elif key_type == 'i':
                    self.app.gsettings_mgr.gsettings.set_int(key, int(value))
                elif key_type in ['as', 'aa{ss}']:
                    parsed_val = json.loads(value)
                    self.app.gsettings_mgr.set_setting(key, parsed_val)
                else:
                    self.app.gsettings_mgr.set_setting(key, value)
            except Exception as e:
                logger.error(f"Failed to parse and set advanced setting: {e}")
                invocation.return_error_literal(Gio.dbus_error_quark(), Gio.DBusError.INVALID_ARGS, str(e))
                return

        invocation.return_value(None)

    def _handle_getblocklist(self, parameters, invocation):
        """Handle GetBlocklist command."""
        rows = self.db.get_blocked_numbers() if self.db else []
        data = [{"id": r[0], "number": r[1], "note": r[2]} for r in rows]
        invocation.return_value(GLib.Variant("(s)", (json.dumps(data),)))

    def _handle_addblockednumber(self, parameters, invocation):
        """Handle AddBlockedNumber command."""
        number, note = parameters.unpack()

        def done(ok):
            invocation.return_value(GLib.Variant("(b)", (bool(ok),)))

        def failed(error):
            logger.error(f"[DBus] Block number failed: {error}")
            invocation.return_value(GLib.Variant("(b)", (False,)))

        if not self.db:
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return
        run_in_background(self.db.block_number, number, note,
                          on_complete=done, on_error=failed)

    def _handle_removeblockednumber(self, parameters, invocation):
        """Handle RemoveBlockedNumber command."""
        bid = parameters.unpack()[0]
        if self.db:
            try:
                self.db.unblock_number(int(bid))
            except ValueError as e:
                logger.debug(f"ValueError in unblock_number: {e}")
        invocation.return_value(None)

    def _handle_getmissedmessages(self, parameters, invocation):
        """Handle GetMissedMessages command."""
        data = []
        if self.app and self.app.scheduler:
            missed = self.app.scheduler.get_missed_messages()
            data = [{"id": m[0], "number": m[1], "body": m[2], "subject": m[3],
                     "attachments": m[4] or "[]", "timestamp": m[5]} for m in missed]
        else:
            logger.debug("[DBus] Cannot get missed messages: scheduler not available")
        invocation.return_value(GLib.Variant("(s)", (json.dumps(data, cls=DateTimeEncoder),)))

    def _handle_sendmissedmessage(self, parameters, invocation):
        """Handle SendMissedMessage command."""
        msg_id = parameters.unpack()[0]
        if self.app and self.app.scheduler:
            missed = self.app.scheduler.get_missed_messages(buffer_minutes=MISSED_MESSAGE_BUFFER_MINUTES)
            target = next((m for m in missed if m[0] == msg_id), None)
            if target:
                self.app.scheduler._process_message(target)
        else:
            logger.debug("[DBus] Cannot send missed message: scheduler not available")
        invocation.return_value(None)

    def _handle_getaddressbooks(self, parameters, invocation):
        """Handle GetAddressBooks command."""
        sources = []
        if self.eds:
            sources = self.eds.get_sources_info()
        invocation.return_value(GLib.Variant("(s)", (json.dumps(sources),)))

    def _handle_setaddressbookpriority(self, parameters, invocation):
        """Handle SetAddressBookPriority command."""
        uid_list_json = parameters.unpack()[0]
        if self.eds:
            try:
                uid_list = json.loads(uid_list_json)
                sources = self.eds.get_sources_info()
                new_config = []
                for uid in uid_list:
                    s = next((x for x in sources if x['uid'] == uid), None)
                    if s:
                        new_config.append(s)
                if new_config:
                    self.eds.update_sources_config(new_config)
            except Exception as e:
                logger.error(f"Failed to parse addressbook priorities: {e}")
        invocation.return_value(None)

    def _handle_importcontacts(self, parameters, invocation):
        """Handle ImportContacts command."""
        vcard_data, source_uid = parameters.unpack()
        count = 0

        is_protected = self._is_protected_source(source_uid, "[DBus] Refusing to import to {name} via CLI")

        if not is_protected and self.eds:
            vcards = re.findall(r'BEGIN:VCARD.*?END:VCARD', vcard_data, re.DOTALL)
            for vcard in vcards:
                if self.eds.save_contact(vcard, source_uid=source_uid if source_uid else None):
                    count += 1
        invocation.return_value(GLib.Variant("(i)", (count,)))

    def _handle_exportcontacts(self, parameters, invocation):
        """Handle ExportContacts command."""
        source_uid = parameters.unpack()[0]

        def fetch():
            if not self.db:
                return ""
            vcards = self.db.get_all_vcards(source_uid if source_uid else None)
            return "".join(v + "\n" for v in vcards)

        def done(full_content):
            invocation.return_value(GLib.Variant("(s)", (full_content,)))

        def failed(error):
            logger.error(f"[DBus] Export contacts failed: {error}")
            invocation.return_value(GLib.Variant("(s)", ("",)))

        run_in_background(fetch, on_complete=done, on_error=failed)

    def _handle_addcontact(self, parameters, invocation):
        """Handle AddContact command."""
        name, number = parameters.unpack()
        if self.eds:
            uid = str(uuid.uuid4())
            vcard_data = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nTEL:{number}\nUID:{uid}\nEND:VCARD"
            self.eds.save_contact(vcard_data)
        invocation.return_value(None)

    def _handle_savecontact(self, parameters, invocation):
        """Write a full vCard to a book, refusing the read-only sync book."""
        vcard, uid, source_uid = parameters.unpack()

        if uid:
            with self.eds.cache_lock:
                contact = self.eds.cache.get(uid)
            if contact and self._is_protected_contact_source(contact.get('source_uid')):
                logger.warning(f"[DBus] Refusing to modify protected contact {uid}")
                invocation.return_value(GLib.Variant("(b)", (False,)))
                return
        if source_uid and self._is_protected_contact_source(source_uid):
            logger.warning(f"[DBus] Refusing to write into protected source {source_uid}")
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return

        def done(ok):
            invocation.return_value(GLib.Variant("(b)", (bool(ok),)))

        def failed(error):
            logger.error(f"[DBus] Save contact failed: {error}")
            invocation.return_value(GLib.Variant("(b)", (False,)))

        if not self.eds:
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return
        run_in_background(self.eds.save_contact, vcard, uid or None, source_uid or None,
                          on_complete=done, on_error=failed)

    def _handle_deletecontacts(self, parameters, invocation):
        """Delete a batch of contacts, skipping the protected ones."""
        uids_json = parameters.unpack()[0]
        uids = []
        try:
            if uids_json:
                uids = json.loads(uids_json)
        except Exception as e:
            logger.warning(f"[DBus] Failed to parse contact uids: {e}")

        def task():
            for uid in uids:
                with self.eds.cache_lock:
                    contact = self.eds.cache.get(uid)
                if contact and self._is_protected_contact(contact, uid, "delete"):
                    continue
                self.eds.delete_contact(uid)

        if not uids or not self.eds:
            invocation.return_value(None)
            return
        self._run_task_then_reply(invocation, task)

    def _handle_refreshcontacts(self, parameters, invocation):
        """Ask every refresh-capable backend to re-sync with its remote."""
        def done(count):
            invocation.return_value(GLib.Variant("(i)", (int(count or 0),)))

        def failed(error):
            logger.error(f"[DBus] Backend refresh failed: {error}")
            invocation.return_value(GLib.Variant("(i)", (0,)))

        if not self.eds:
            invocation.return_value(GLib.Variant("(i)", (0,)))
            return
        run_in_background(self.eds.refresh_backends, on_complete=done, on_error=failed)

    def _handle_deletecontact(self, parameters, invocation):
        """Handle DeleteContact command."""
        uid = parameters.unpack()[0]
        if self.eds:
            with self.eds.cache_lock:
                contact = self.eds.cache.get(uid)
            if contact and self._is_protected_contact(contact, uid, "delete"):
                invocation.return_value(None)
                return
            self.eds.delete_contact(uid)
        invocation.return_value(None)

    def _handle_modifycontact(self, parameters, invocation):
        """Handle ModifyContact command."""
        uid, name, number = parameters.unpack()
        if self.eds:
            with self.eds.cache_lock:
                contact = self.eds.cache.get(uid)
            if contact and self._is_protected_contact(contact, uid, "modify"):
                invocation.return_value(None)
                return
            vcard_data = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nTEL:{number}\nUID:{uid}\nEND:VCARD"
            self.eds.save_contact(vcard_data, uid=uid)
        invocation.return_value(None)
