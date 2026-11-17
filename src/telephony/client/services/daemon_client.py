# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import json

from gi.repository import Gio, GLib
from telephony.shared.utils.log_utils import logger

from telephony.shared.constants import DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE

DAEMON_CALL_TIMEOUT_MS = 30000
DAEMON_SLOW_CALL_TIMEOUT_MS = 600000


class DaemonClient:

    def __init__(self):
        self.bus = None
        self._subscriptions = []
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[DaemonClient] No session bus: {e}")

    def call(self, method, params=None, reply_type=None, timeout_ms=DAEMON_CALL_TIMEOUT_MS):
        if not self.bus:
            return None
        try:
            res = self.bus.call_sync(
                DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, method,
                params, reply_type, Gio.DBusCallFlags.NONE,
                timeout_ms, None)
            return res.unpack() if res is not None else None
        except Exception as e:
            logger.error(f"[DaemonClient] {method} failed: {e}")
            return None

    def call_async(self, method, params=None):
        if not self.bus:
            return
        self.bus.call(
            DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, method,
            params, None, Gio.DBusCallFlags.NONE,
            DAEMON_CALL_TIMEOUT_MS, None, self.on_async_done, method)

    def call_with_reply(self, method, params, callback, timeout_ms=DAEMON_CALL_TIMEOUT_MS):
        if not self.bus:
            GLib.idle_add(callback, None)
            return

        def done(bus, result):
            try:
                res = bus.call_finish(result)
            except Exception as e:
                logger.error(f"[DaemonClient] {method} failed: {e}")
                callback(None)
                return
            callback(res.unpack() if res is not None else None)

        self.bus.call(
            DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, method,
            params, None, Gio.DBusCallFlags.NONE,
            timeout_ms, None, done)

    def dial(self, number, hide_id, callback):
        self.call_with_reply("Dial", GLib.Variant("(sb)", (number, bool(hide_id))), callback)

    def on_async_done(self, bus, result, method):
        try:
            bus.call_finish(result)
        except Exception as e:
            logger.error(f"[DaemonClient] {method} failed: {e}")

    def subscribe(self, signal_name, handler):
        if not self.bus:
            return
        self._subscriptions.append(self.bus.signal_subscribe(
            DAEMON_BUS_NAME, DAEMON_INTERFACE, signal_name, DAEMON_OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, handler, None))

    def disconnect(self):
        if not self.bus:
            return
        for sub in self._subscriptions:
            self.bus.signal_unsubscribe(sub)
        self._subscriptions = []

    def send_tracked_sms(self, number, text, callback=None):
        params = GLib.Variant("(ss)", (number, text))
        if callback is None:
            self.call_async("SendTrackedSms", params)
            return
        self.call_with_reply("SendTrackedSms", params,
                             lambda reply: callback(bool(reply and reply[0])))

    def send_mms(self, number, text, attachments, callback=None):
        params = GLib.Variant("(sss)", (number, text, json.dumps(attachments or [])))
        if callback is None:
            self.call_async("SendMms", params)
            return
        self.call_with_reply("SendMms", params,
                             lambda reply: callback(bool(reply and reply[0])))

    def schedule_sms(self, number, text, timestamp, callback=None):
        params = GLib.Variant("(sss)", (number, text, timestamp))
        if callback is None:
            self.call_async("ScheduleSms", params)
            return
        self.call_with_reply("ScheduleSms", params,
                             lambda reply: callback(bool(reply and reply[0])))

    def schedule_mms(self, number, text, attachments, timestamp, callback=None):
        params = GLib.Variant("(ssss)", (number, text, json.dumps(attachments or []), timestamp))
        if callback is None:
            self.call_async("ScheduleMms", params)
            return
        self.call_with_reply("ScheduleMms", params,
                             lambda reply: callback(bool(reply and reply[0])))

    def save_draft(self, number, text, attachments, callback=None):
        params = GLib.Variant("(sss)", (number, text, json.dumps(attachments or [])))
        if callback is None:
            self.call_async("SaveDraft", params)
            return
        self.call_with_reply("SaveDraft", params,
                             lambda reply: callback(reply is not None))

    def mark_thread_read(self, number):
        self.call_async("MarkThreadAsRead", GLib.Variant("(s)", (number,)))

    def mark_conversation_unread(self, number, msg_id, callback=None):
        params = GLib.Variant("(si)", (number, msg_id))
        if callback is None:
            self.call_async("MarkConversationUnread", params)
            return
        self.call_with_reply("MarkConversationUnread", params,
                             lambda reply: callback(reply is not None))

    def reschedule_message(self, msg_id, timestamp, callback=None):
        params = GLib.Variant("(is)", (msg_id, timestamp))
        if callback is None:
            self.call_async("RescheduleMessage", params)
            return
        self.call_with_reply("RescheduleMessage", params,
                             lambda reply: callback(bool(reply and reply[0])))

    def retry_message(self, msg_id):
        self.call_async("RetryMessage", GLib.Variant("(i)", (msg_id,)))

    def delete_message(self, msg_id, callback=None):
        params = GLib.Variant("(i)", (msg_id,))
        if callback is None:
            return self.call("DeleteMessage", params) is not None
        self.call_with_reply("DeleteMessage", params,
                             lambda reply: callback(reply is not None))

    def delete_conversation(self, number):
        reply = self.call("DeleteConversation", GLib.Variant("(s)", (number,)))
        return reply is not None

    def set_group_name(self, recipients, name, callback=None):
        params = GLib.Variant("(ss)", (",".join(recipients), name))
        if callback is None:
            self.call_async("SetGroupName", params)
            return
        self.call_with_reply("SetGroupName", params,
                             lambda reply: callback(bool(reply and reply[0])))

    def add_blocked_number(self, number, note, block_calls=True, block_messages=True,
                           callback=None):
        params = GLib.Variant("(ssbb)", (number, note, block_calls, block_messages))
        if callback is None:
            self.call_async("AddBlockedNumber", params)
            return
        self.call_with_reply("AddBlockedNumber", params,
                             lambda reply: callback(bool(reply and reply[0])))

    def update_blocked_number(self, bid, number, note, block_calls=True, block_messages=True,
                              callback=None):
        params = GLib.Variant("(sssbb)", (str(bid), number, note,
                                          block_calls, block_messages))
        if callback is None:
            self.call_async("UpdateBlockedNumber", params)
            return
        self.call_with_reply("UpdateBlockedNumber", params,
                             lambda reply: callback(bool(reply and reply[0])))

    def set_blocked_number_flags(self, bid, block_calls, block_messages, callback=None):
        params = GLib.Variant("(sbb)", (str(bid), block_calls, block_messages))
        if callback is None:
            self.call_async("SetBlockedNumberFlags", params)
            return
        self.call_with_reply("SetBlockedNumberFlags", params,
                             lambda reply: callback(bool(reply and reply[0])))

    def import_blocklist(self, json_data):
        reply = self.call("ImportBlocklist", GLib.Variant("(s)", (json_data,)),
                          GLib.VariantType("(uu)"))
        return tuple(reply) if reply else (0, 0)

    def remove_blocked_number(self, bid, callback=None):
        if callback is None:
            self.call_async("RemoveBlockedNumber", GLib.Variant("(s)", (str(bid),)))
            return
        self.call_with_reply("RemoveBlockedNumber", GLib.Variant("(s)", (str(bid),)),
                             lambda _reply: callback())

    def delete_call_entry(self, call_id):
        self.call_async("DeleteCallHistoryEntry", GLib.Variant("(i)", (call_id,)))

    def update_history_names(self, numbers, new_name, callback=None):
        params = GLib.Variant("(ss)", (json.dumps(list(numbers)), new_name or ""))
        if callback is None:
            self.call_async("UpdateHistoryNames", params)
            return
        self.call_with_reply("UpdateHistoryNames", params,
                             lambda reply: callback(reply is not None))

    def clear_call_history(self, callback):
        self.call_with_reply("ClearCallHistory", None,
                             lambda reply: callback(reply is not None))

    def clear_messages(self, callback):
        self.call_with_reply("ClearMessages", None,
                             lambda reply: callback(reply is not None),
                             timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def clear_group_names(self, callback):
        self.call_with_reply("ClearGroupNames", None,
                             lambda reply: callback(reply is not None))

    def clear_blocklist(self, callback):
        self.call_with_reply("ClearBlocklist", None,
                             lambda reply: callback(reply is not None))

    def clear_everything(self, source_uid, callback):
        self.call_with_reply("ClearEverything", GLib.Variant("(s)", (source_uid or "",)),
                             lambda reply: callback(reply is not None),
                             timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def get_missed_messages(self, callback):
        def parsed(reply):
            if not reply:
                callback([])
                return
            try:
                callback(json.loads(reply[0]))
            except Exception as e:
                logger.error(f"[DaemonClient] Bad missed messages payload: {e}")
                callback([])

        self.call_with_reply("GetMissedMessages", None, parsed)

    def send_missed_message(self, msg_id):
        self.call_async("SendMissedMessage", GLib.Variant("(i)", (msg_id,)))

    def import_reply(self, reply, callback):
        callback(reply if reply else (False, ""))

    def import_chatty(self, db_path, mms_path, callback):
        self.call_with_reply(
            "ImportChatty", GLib.Variant("(ss)", (db_path or "", mms_path or "")),
            lambda reply: self.import_reply(reply, callback),
            timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def import_local_calls(self, db_path, callback):
        self.call_with_reply(
            "ImportLocalCalls", GLib.Variant("(s)", (db_path or "",)),
            lambda reply: self.import_reply(reply, callback),
            timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def import_android_sms(self, file_path):
        reply = self.call("ImportAndroidSms", GLib.Variant("(s)", (file_path,)),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def import_android_calls(self, file_path):
        reply = self.call("ImportAndroidCalls", GLib.Variant("(s)", (file_path,)),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def import_ios_sms(self, file_path, manifest_path=None, backup_dir=None):
        reply = self.call("ImportIosSms",
                          GLib.Variant("(sss)", (file_path, manifest_path or "", backup_dir or "")),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def import_ios_calls(self, file_path):
        reply = self.call("ImportIosCalls", GLib.Variant("(s)", (file_path,)),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def save_contact(self, vcard, uid=None, source_uid=None):
        reply = self.call("SaveContact",
                          GLib.Variant("(sss)", (vcard, uid or "", source_uid or "")),
                          GLib.VariantType("(bs)"))
        if not reply:
            return (False, "write-failed")
        return (bool(reply[0]), reply[1])

    def delete_contact(self, uid, callback=None):
        params = GLib.Variant("(s)", (uid,))
        if callback is None:
            self.call_async("DeleteContact", params)
            return
        self.call_with_reply("DeleteContact", params,
                             lambda reply: callback(reply is not None))

    def delete_contacts(self, uids):
        reply = self.call("DeleteContacts", GLib.Variant("(s)", (json.dumps(list(uids)),)),
                          timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply is not None

    def refresh_contacts(self, callback):
        self.call_with_reply("RefreshContacts", None,
                             lambda reply: callback(reply[0] if reply else 0),
                             timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def import_contacts(self, vcard_data, source_uid=None):
        reply = self.call("ImportContacts",
                          GLib.Variant("(ss)", (vcard_data, source_uid or "")),
                          GLib.VariantType("(i)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply[0] if reply else 0

    def import_sim_contacts(self, source_uid, callback):
        self.call_with_reply("ImportSimContacts", GLib.Variant("(s)", (source_uid or "",)),
                             callback, timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def get_telephony_state(self, callback):
        self.call_with_reply("GetTelephonyState", None,
                             lambda reply: callback(reply[0] if reply is not None else None))

    def set_active_chat(self, number):
        self.call_async("SetActiveChat", GLib.Variant("(s)", (number or "",)))

    def disable_all_forwarding(self, callback):
        self.call_with_reply("DisableAllForwarding", None, callback,
                             timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def disable_all_barrings(self, password, callback):
        self.call_with_reply("DisableAllBarrings", GLib.Variant("(s)", (password,)),
                             callback, timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def change_barring_password(self, old, new, callback):
        self.call_with_reply("ChangeBarringPassword", GLib.Variant("(ss)", (old, new)),
                             callback, timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def set_setting(self, key, value):
        packed = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
        self.call_async("SetSetting", GLib.Variant("(ss)", (key, packed)))

    def first_or_none(self, reply, callback):
        callback(reply[0] if reply and reply[0] else None)

    def get_own_number(self, callback):
        self.call_with_reply("GetOwnNumber", None,
                             lambda reply: self.first_or_none(reply, callback))

    def detect_region(self, callback):
        self.call_with_reply("DetectRegion", None,
                             lambda reply: self.first_or_none(reply, callback))

    def request_recovery(self, callback):
        self.call_with_reply("RequestRecovery", None, callback,
                             timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def clear_notification(self, number):
        self.call_async("ClearNotification", GLib.Variant("(s)", (number,)))

    def silence_ring(self):
        self.call_async("SilenceRing")

    def set_audio_route(self, route):
        self.call_async("SetAudioRoute", GLib.Variant("(s)", (route,)))

    def set_input_route(self, route):
        self.call_async("SetInputRoute", GLib.Variant("(s)", (route,)))

    def set_delivery_reports(self, enabled):
        return self.call("SetDeliveryReports", GLib.Variant("(b)", (bool(enabled),)),
                         GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def set_call_recording(self, enabled):
        self.call_async("StartCallRecording" if enabled else "StopCallRecording")

    def get_call_recording_state(self, callback):
        self.call_with_reply("GetCallRecordingState", None, callback)

    def set_mic_muted(self, muted):
        self.call_async("MuteMic" if muted else "UnmuteMic")

    def get_audio_routes(self, callback):
        self.call_with_reply("GetAudioRoutes", None, callback)

    def prepare_attachment(self, source_path, max_bytes, callback):
        self.call_with_reply("PrepareAttachment",
                             GLib.Variant("(si)", (source_path, int(max_bytes))),
                             callback, timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def clear_contacts(self, source_uid, callback):
        self.call_with_reply("ClearContacts", GLib.Variant("(s)", (source_uid or "",)),
                             lambda reply: callback(reply is not None),
                             timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def delete_address_book(self, source_uid, callback):
        self.call_with_reply("DeleteAddressBook", GLib.Variant("(s)", (source_uid,)),
                             lambda reply: callback(bool(reply and reply[0])),
                             timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def get_address_books(self, callback=None):
        def unpack(reply):
            if not reply:
                return None
            try:
                return json.loads(reply[0])
            except Exception as e:
                logger.error(f"[DaemonClient] Bad address book list: {e}")
                return None

        if callback is None:
            return unpack(self.call("GetAddressBooks", None, GLib.VariantType("(s)")))

        self.call_with_reply("GetAddressBooks", None, lambda reply: callback(unpack(reply)))

    def create_address_book(self, name, callback):
        self.call_with_reply("CreateAddressBook", GLib.Variant("(s)", (name,)),
                             lambda reply: callback(bool(reply and reply[0])),
                             timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
