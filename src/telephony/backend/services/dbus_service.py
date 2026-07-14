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
from loguru import logger
import uuid

from telephony.backend.utils.importer_local_utils import import_local_chatty, import_local_calls
from telephony.backend.utils.importer_android_utils import import_android_sms, import_android_calls
from telephony.backend.utils.importer_ios_utils import import_ios_sms, import_ios_calls

DAEMON_INTERFACE_XML = """
<node>
  <interface name="io.furios.Telephony.Daemon">
    <!-- Call operations -->
    <method name="Dial">
      <arg type="s" name="number" direction="in"/>
      <arg type="b" name="success" direction="out"/>
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
            "/io/furios/Telephony/Daemon",
            self.interface_info,
            self._handle_method_call,
            None, None
        )
        logger.info(f"TelephonyDaemonDBus registered on Session Bus at /io/furios/Telephony/Daemon (id {self.reg_id})")

        if self.ofono:
            self.ofono.connect('call-added', self._on_call_added)
            self.ofono.connect('call-changed', self._on_call_changed)
            self.ofono.connect('call-removed', self._on_call_removed)
            self.ofono.connect('incoming-message', self._on_incoming_message)

    def _on_call_added(self, manager, path, props):
        number = props.get("number", "Unknown")
        self.emit_signal("IncomingCall", GLib.Variant("(ss)", (path, number)))

    def _on_call_changed(self, manager, path, state):
        self.emit_signal("CallChanged", GLib.Variant("(ss)", (path, state)))

    def _on_call_removed(self, manager, path):
        self.emit_signal("CallRemoved", GLib.Variant("(s)", (path,)))

    def _on_incoming_message(self, manager, number, body):
        self.emit_signal("IncomingSms", GLib.Variant("(ss)", (number, body)))

    def emit_signal(self, signal_name, parameters):
        self.bus.emit_signal(
            None,
            "/io/furios/Telephony/Daemon",
            "io.furios.Telephony.Daemon",
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
            "DeleteMessage": self._handle_deletemessage,
            "DeleteConversation": self._handle_deleteconversation,
            "MarkThreadAsRead": self._handle_markthreadasread,
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
            "DeleteContact": self._handle_deletecontact,
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
        """Handle Dial command."""
        number = parameters.unpack()[0]
        success = True

        def do_dial():
            action = Gio.SimpleAction.new("dial-number", GLib.VariantType.new("s"))
            self.app.on_action_dial(action, GLib.Variant("s", number))
        GLib.idle_add(do_dial)
        invocation.return_value(GLib.Variant("(b)", (success,)))

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
        if self.app and self.app.audio_mgr:
            self.app.audio_mgr.set_microphone_mute(True)
        invocation.return_value(None)

    def _handle_unmutemic(self, parameters, invocation):
        """Handle UnmuteMic command."""
        if self.app and self.app.audio_mgr:
            self.app.audio_mgr.set_microphone_mute(False)
        invocation.return_value(None)

    def _handle_setspeakerphone(self, parameters, invocation):
        """Handle SetSpeakerphone command."""
        enable = parameters.unpack()[0]
        if self.app and self.app.audio_mgr:
            self.app.audio_mgr.set_speakerphone(enable)
        invocation.return_value(None)

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
                    row_id = self.db.add_message(number, 'outgoing', sms_text, status='draft', subject=None, attachments=[], sender="Me")
                    success = self.ofono.send_sms(number, sms_text)
                    if success:
                        self.db.update_message_schedule(row_id, status="sent", timestamp=None)
        invocation.return_value(None)

    def _handle_schedulesms(self, parameters, invocation):
        """Handle ScheduleSms command."""
        number, text, scheduled_timestamp = parameters.unpack()
        success = False
        if self.app and self.app.scheduler:
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
                attachments = json.loads(attachments_json)
        except Exception as e:
            logger.warning(f"Failed to parse attachments: {e}")

        success = False
        if self.app and self.app.scheduler:
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
        invocation.return_value(None)

    def _handle_deleteconversation(self, parameters, invocation):
        """Handle DeleteConversation command."""
        number = parameters.unpack()[0]
        if self.db:
            self.db.delete_conversation(number)
        invocation.return_value(None)

    def _handle_markthreadasread(self, parameters, invocation):
        """Handle MarkThreadAsRead command."""
        number = parameters.unpack()[0]
        if self.db:
            self.db.mark_conversation_read(number)
        invocation.return_value(None)

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
            pass
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

    def _handle_clearcontacts(self, parameters, invocation):
        """Handle ClearContacts command."""
        source_uid = parameters.unpack()[0]

        is_protected = False
        if source_uid == "system-address-book":
            is_protected = True
            logger.warning("[DBus] Refusing to clear system-address-book via CLI")
        elif self.eds:
            sources = self.eds.get_sources_info()
            for s in sources:
                if s.get('uid') == source_uid and s.get('name') == "Andromeda Contacts":
                    is_protected = True
                    logger.warning("[DBus] Refusing to clear Andromeda Contacts via CLI")
                    break

        if not source_uid and self.eds:
            uids_to_delete = []
            for uid, contact in list(self.eds.cache.items()):
                c_source_uid = contact.get('source_uid')
                protected = False
                if c_source_uid == "system-address-book":
                    protected = True
                elif self.eds.sources and c_source_uid in self.eds.sources and self.eds.sources[c_source_uid].get('name') == "Andromeda Contacts":
                    protected = True

                if not protected:
                    uids_to_delete.append(uid)

            for uid in uids_to_delete:
                self.eds.remove_contact(uid)
            invocation.return_value(None)
            return

        if not is_protected and self.eds:
            self.eds.delete_all_contacts(source_uid=source_uid if source_uid else None)
        invocation.return_value(None)


    def _handle_cleareverything(self, parameters, invocation):
        """Handle ClearEverything command."""
        source_uid = parameters.unpack()[0]
        if self.db:
            self.db.clear_everything()

        is_protected = False
        if source_uid == "system-address-book":
            is_protected = True
            logger.warning("[DBus] Refusing to clear system-address-book via CLI")
        elif self.eds:
            sources = self.eds.get_sources_info()
            for s in sources:
                if s.get('uid') == source_uid and s.get('name') == "Andromeda Contacts":
                    is_protected = True
                    logger.warning("[DBus] Refusing to clear Andromeda Contacts via CLI")
                    break

        if not source_uid and self.eds:
            uids_to_delete = []
            for uid, contact in list(self.eds.cache.items()):
                c_source_uid = contact.get('source_uid')
                protected = False
                if c_source_uid == "system-address-book":
                    protected = True
                elif self.eds.sources and c_source_uid in self.eds.sources and self.eds.sources[c_source_uid].get('name') == "Andromeda Contacts":
                    protected = True

                if not protected:
                    uids_to_delete.append(uid)

            for uid in uids_to_delete:
                self.eds.remove_contact(uid)
        elif not is_protected and self.eds:
            self.eds.delete_all_contacts(source_uid=source_uid if source_uid else None)
        try:
            cfg = os.path.join(GLib.get_user_config_dir(), "telephony.json")
            if os.path.exists(cfg):
                os.remove(cfg)
        except Exception:
            pass
        invocation.return_value(None)

    def _handle_deleteaddressbook(self, parameters, invocation):
        """Handle DeleteAddressBook command."""
        source_uid = parameters.unpack()[0]
        success = False

        is_protected = False
        if source_uid == "system-address-book":
            is_protected = True
            logger.warning("[DBus] Refusing to delete system-address-book")
        elif self.eds:
            sources = self.eds.get_sources_info()
            for s in sources:
                if s.get('uid') == source_uid and s.get('name') == "Andromeda Contacts":
                    is_protected = True
                    logger.warning("[DBus] Refusing to delete Andromeda Contacts")
                    break

        if not is_protected and self.eds:
            success = self.eds.delete_addressbook(source_uid)
        invocation.return_value(GLib.Variant("(b)", (success,)))

    def _handle_importchatty(self, parameters, invocation):
        """Handle ImportChatty command."""
        db_path, mms_path = parameters.unpack()
        success = False
        msg = ""
        if self.db:
            success, msg = import_local_chatty(self.db, db_path, mms_path)
        invocation.return_value(GLib.Variant("(bs)", (success, msg)))

    def _handle_importlocalcalls(self, parameters, invocation):
        """Handle ImportLocalCalls command."""
        db_path = parameters.unpack()[0]
        success = False
        msg = ""
        if self.db:
            success, msg = import_local_calls(self.db, db_path)
        invocation.return_value(GLib.Variant("(bs)", (success, msg)))

    def _handle_importandroidsms(self, parameters, invocation):
        """Handle ImportAndroidSms command."""
        file_path = parameters.unpack()[0]
        success = False
        msg = ""
        if self.db:
            success, msg = import_android_sms(self.db, file_path)
        invocation.return_value(GLib.Variant("(bs)", (success, msg)))

    def _handle_importandroidcalls(self, parameters, invocation):
        """Handle ImportAndroidCalls command."""
        file_path = parameters.unpack()[0]
        success = False
        msg = ""
        if self.db:
            success, msg = import_android_calls(self.db, file_path)
        invocation.return_value(GLib.Variant("(bs)", (success, msg)))

    def _handle_importiossms(self, parameters, invocation):
        """Handle ImportIosSms command."""
        file_path = parameters.unpack()[0]
        success = False
        msg = ""
        if self.db:
            success, msg = import_ios_sms(self.db, file_path)
        invocation.return_value(GLib.Variant("(bs)", (success, msg)))

    def _handle_importioscalls(self, parameters, invocation):
        """Handle ImportIosCalls command."""
        file_path = parameters.unpack()[0]
        success = False
        msg = ""
        if self.db:
            success, msg = import_ios_calls(self.db, file_path)
        invocation.return_value(GLib.Variant("(bs)", (success, msg)))

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
                attachments = json.loads(attachments_json)
        except Exception as e:
            logger.warning(f"Failed to parse attachments: {e}")

        success = False
        if self.app and self.app.mms:
            numbers = [n.strip() for n in number.split(',') if n.strip()]
            row_id = self.db.add_message(number, 'outgoing', text, status='draft', subject=None, attachments=attachments, sender="Me")
            success = self.app.mms.send_mms(numbers, text, attachments)
            if success:
                self.db.update_message_schedule(row_id, status="sent", timestamp=None)
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
        if self.app and self.app.gsettings_mgr:
            schema = self.app.gsettings_mgr.gsettings.get_property('settings-schema')
            if schema and schema.has_key(key):
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
        if self.db:
            self.db.add_blocked_number(number, note)
        invocation.return_value(None)

    def _handle_removeblockednumber(self, parameters, invocation):
        """Handle RemoveBlockedNumber command."""
        bid = parameters.unpack()[0]
        if self.db:
            try:
                self.db.remove_blocked_number(int(bid))
            except ValueError as e:
                logger.debug(f"ValueError in remove_blocked_number: {e}")
        invocation.return_value(None)

    def _handle_getmissedmessages(self, parameters, invocation):
        """Handle GetMissedMessages command."""
        data = []
        if self.app and self.app.scheduler:
            missed = self.app.scheduler.get_missed_messages()
            data = [{"id": m[0], "number": m[1], "body": m[2], "timestamp": m[5]} for m in missed]
        else:
            logger.debug("[DBus] Cannot get missed messages: scheduler not available")
        invocation.return_value(GLib.Variant("(s)", (json.dumps(data, cls=DateTimeEncoder),)))

    def _handle_sendmissedmessage(self, parameters, invocation):
        """Handle SendMissedMessage command."""
        msg_id = parameters.unpack()[0]
        if self.app and self.app.scheduler:
            missed = self.app.scheduler.get_missed_messages(buffer_minutes=14400)
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

        is_protected = False
        if source_uid == "system-address-book":
            is_protected = True
            logger.warning("[DBus] Refusing to import to system-address-book via CLI")
        elif self.eds:
            sources = self.eds.get_sources_info()
            for s in sources:
                if s.get('uid') == source_uid and s.get('name') == "Andromeda Contacts":
                    is_protected = True
                    logger.warning("[DBus] Refusing to import to Andromeda Contacts via CLI")
                    break

        if not is_protected and self.eds:
            vcards = re.findall(r'BEGIN:VCARD.*?END:VCARD', vcard_data, re.DOTALL)
            for vcard in vcards:
                if self.eds.save_contact(vcard, source_uid=source_uid if source_uid else None):
                    count += 1
        invocation.return_value(GLib.Variant("(i)", (count,)))

    def _handle_exportcontacts(self, parameters, invocation):
        """Handle ExportContacts command."""
        source_uid = parameters.unpack()[0]
        full_content = ""
        if self.eds:
            contacts = []
            with self.eds.cache_lock:
                if source_uid:
                    contacts = [c for c in self.eds.cache.values() if c.get('source_uid') == source_uid]
                else:
                    contacts = list(self.eds.cache.values())
            for c in contacts:
                v = c.get('vcard', '')
                if v:
                    full_content += v + "\n"
        invocation.return_value(GLib.Variant("(s)", (full_content,)))

    def _handle_addcontact(self, parameters, invocation):
        """Handle AddContact command."""
        name, number = parameters.unpack()
        if self.eds:
            uid = str(uuid.uuid4())
            vcard_data = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nTEL:{number}\nUID:{uid}\nEND:VCARD"
            self.eds.add_contact(vcard_data, "default")
        invocation.return_value(None)

    def _handle_deletecontact(self, parameters, invocation):
        """Handle DeleteContact command."""
        uid = parameters.unpack()[0]
        if self.eds:
            contact = self.eds.cache.get(uid)
            if contact:
                s_uid = contact.get('source_uid')
                if s_uid == "system-address-book":
                    logger.warning(f"[DBus] Refusing to delete system-address-book Contact {uid} via CLI")
                    invocation.return_value(None)
                    return
                if self.eds.sources and s_uid and s_uid in self.eds.sources and self.eds.sources[s_uid].get('name') == "Andromeda Contacts":
                    logger.warning(f"[DBus] Refusing to delete Andromeda Contact {uid} via CLI")
                    invocation.return_value(None)
                    return
            self.eds.remove_contact(uid)
        invocation.return_value(None)

    def _handle_modifycontact(self, parameters, invocation):
        """Handle ModifyContact command."""
        uid, name, number = parameters.unpack()
        if self.eds:
            contact = self.eds.cache.get(uid)
            if contact:
                s_uid = contact.get('source_uid')
                if s_uid == "system-address-book":
                    logger.warning(f"[DBus] Refusing to modify system-address-book Contact {uid} via CLI")
                    invocation.return_value(None)
                    return
                if self.eds.sources and s_uid and s_uid in self.eds.sources and self.eds.sources[s_uid].get('name') == "Andromeda Contacts":
                    logger.warning(f"[DBus] Refusing to modify Andromeda Contact {uid} via CLI")
                    invocation.return_value(None)
                    return
            vcard_data = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nTEL:{number}\nUID:{uid}\nEND:VCARD"
            self.eds.modify_contact(uid, vcard_data)
        invocation.return_value(None)
