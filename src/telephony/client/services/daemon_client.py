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

import json

from gi.repository import Gio, GLib
from telephony.shared.utils.log_utils import logger

from telephony.shared.constants import DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE

DAEMON_CALL_TIMEOUT_MS = 30000
DAEMON_SLOW_CALL_TIMEOUT_MS = 600000


class DaemonClient:
    """Talks to the process that owns the modem.

    A window instance never touches the modem itself, because two
    processes acting on one arriving message would file it twice. It
    asks the owner to act and listens for what the owner reports.
    """

    def __init__(self):
        """Connect to the session bus."""
        self.bus = None
        self._subscriptions = []
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[DaemonClient] No session bus: {e}")

    def call(self, method, params=None, reply_type=None, timeout_ms=DAEMON_CALL_TIMEOUT_MS):
        """Ask the owner to do something; blocking, call from a worker.

        Returns the reply tuple, or None when the owner could not be
        reached, so a caller can tell refusal from silence.
        """
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
        """Ask the owner to do something without waiting for the reply."""
        if not self.bus:
            return
        self.bus.call(
            DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, method,
            params, None, Gio.DBusCallFlags.NONE,
            DAEMON_CALL_TIMEOUT_MS, None, self._on_async_done, method)

    def call_with_reply(self, method, params, callback, timeout_ms=DAEMON_CALL_TIMEOUT_MS):
        """Ask and hand the unpacked reply to callback on the main loop.

        The callback receives None when the owner could not be reached or
        refused the call at the bus level, so silence stays distinguishable
        from a refusal the owner actually answered.
        """
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
        """Place a call through the owner; callback hears (success, message) or None."""
        self.call_with_reply("Dial", GLib.Variant("(sb)", (number, bool(hide_id))), callback)

    def _on_async_done(self, bus, result, method):
        """Log an asked action that the owner refused."""
        try:
            bus.call_finish(result)
        except Exception as e:
            logger.error(f"[DaemonClient] {method} failed: {e}")

    def subscribe(self, signal_name, handler):
        """Follow one thing the owner reports."""
        if not self.bus:
            return
        self._subscriptions.append(self.bus.signal_subscribe(
            DAEMON_BUS_NAME, DAEMON_INTERFACE, signal_name, DAEMON_OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, handler, None))

    def disconnect(self):
        """Stop following the owner."""
        if not self.bus:
            return
        for sub in self._subscriptions:
            self.bus.signal_unsubscribe(sub)
        self._subscriptions = []

    def send_tracked_sms(self, number, text):
        """Record and send an SMS with tracking; blocking, call from a worker."""
        reply = self.call("SendTrackedSms", GLib.Variant("(ss)", (number, text)),
                          GLib.VariantType("(b)"))
        return bool(reply and reply[0])

    def send_mms(self, number, text, attachments):
        """Record and send an MMS with tracking; blocking, call from a worker."""
        reply = self.call("SendMms",
                          GLib.Variant("(sss)", (number, text, json.dumps(attachments or []))),
                          GLib.VariantType("(b)"))
        return bool(reply and reply[0])

    def schedule_sms(self, number, text, timestamp):
        """Store an SMS for later sending; blocking, call from a worker."""
        reply = self.call("ScheduleSms", GLib.Variant("(sss)", (number, text, timestamp)),
                          GLib.VariantType("(b)"))
        return bool(reply and reply[0])

    def schedule_mms(self, number, text, attachments, timestamp):
        """Store an MMS for later sending; blocking, call from a worker."""
        reply = self.call("ScheduleMms",
                          GLib.Variant("(ssss)", (number, text, json.dumps(attachments or []), timestamp)),
                          GLib.VariantType("(b)"))
        return bool(reply and reply[0])

    def save_draft(self, number, text, attachments):
        """Replace the stored draft; blocking, call from a worker."""
        reply = self.call("SaveDraft",
                          GLib.Variant("(sss)", (number, text, json.dumps(attachments or []))))
        return reply is not None

    def mark_thread_read(self, number):
        """Mark a conversation read without waiting for the reply."""
        self.call_async("MarkThreadAsRead", GLib.Variant("(s)", (number,)))

    def mark_conversation_unread(self, number, msg_id):
        """Mark a message and newer ones unread; blocking, call from a worker."""
        reply = self.call("MarkConversationUnread", GLib.Variant("(si)", (number, msg_id)))
        return reply is not None

    def reschedule_message(self, msg_id, timestamp):
        """Move a scheduled message; blocking, call from a worker."""
        reply = self.call("RescheduleMessage", GLib.Variant("(is)", (msg_id, timestamp)),
                          GLib.VariantType("(b)"))
        return bool(reply and reply[0])

    def retry_message(self, msg_id):
        """Resend a failed message without waiting for the reply."""
        self.call_async("RetryMessage", GLib.Variant("(i)", (msg_id,)))

    def delete_message(self, msg_id):
        """Delete one message; blocking, call from a worker."""
        reply = self.call("DeleteMessage", GLib.Variant("(i)", (msg_id,)))
        return reply is not None

    def delete_conversation(self, number):
        """Delete a whole conversation; blocking, call from a worker."""
        reply = self.call("DeleteConversation", GLib.Variant("(s)", (number,)))
        return reply is not None

    def set_group_name(self, recipients, name):
        """Rename a group conversation; blocking, call from a worker."""
        reply = self.call("SetGroupName",
                          GLib.Variant("(ss)", (",".join(recipients), name)),
                          GLib.VariantType("(b)"))
        return bool(reply and reply[0])

    def add_blocked_number(self, number, note, block_calls=True, block_messages=True):
        """Block a number for the chosen domains; blocking, call from a worker."""
        reply = self.call("AddBlockedNumber",
                          GLib.Variant("(ssbb)", (number, note, block_calls, block_messages)),
                          GLib.VariantType("(b)"))
        return bool(reply and reply[0])

    def set_blocked_number_flags(self, bid, block_calls, block_messages):
        """Set one entry's domain flags; blocking, call from a worker."""
        reply = self.call("SetBlockedNumberFlags",
                          GLib.Variant("(sbb)", (str(bid), block_calls, block_messages)),
                          GLib.VariantType("(b)"))
        return bool(reply and reply[0])

    def import_blocklist(self, json_data):
        """Merge an exported blocklist; blocking, call from a worker."""
        reply = self.call("ImportBlocklist", GLib.Variant("(s)", (json_data,)),
                          GLib.VariantType("(uu)"))
        return tuple(reply) if reply else (0, 0)

    def remove_blocked_number(self, bid):
        """Unblock a blocklist entry without waiting for the reply."""
        self.call_async("RemoveBlockedNumber", GLib.Variant("(s)", (str(bid),)))

    def delete_call_entry(self, call_id):
        """Delete one call history row without waiting for the reply."""
        self.call_async("DeleteCallHistoryEntry", GLib.Variant("(i)", (call_id,)))

    def update_history_names(self, numbers, new_name):
        """Rename call history rows; blocking, call from a worker."""
        reply = self.call("UpdateHistoryNames",
                          GLib.Variant("(ss)", (json.dumps(list(numbers)), new_name or "")))
        return reply is not None

    def clear_call_history(self):
        """Wipe call history; blocking, call from a worker."""
        return self.call("ClearCallHistory") is not None

    def clear_messages(self):
        """Wipe messages and attachments; blocking, call from a worker."""
        return self.call("ClearMessages", timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS) is not None

    def clear_group_names(self):
        """Wipe custom group names; blocking, call from a worker."""
        return self.call("ClearGroupNames") is not None

    def clear_blocklist(self):
        """Wipe the blocklist; blocking, call from a worker."""
        return self.call("ClearBlocklist") is not None

    def clear_everything(self, source_uid):
        """Wipe all stored data; blocking, call from a worker."""
        reply = self.call("ClearEverything", GLib.Variant("(s)", (source_uid or "",)),
                          timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply is not None

    def get_missed_messages(self):
        """Fetch missed scheduled messages; blocking, call from a worker."""
        reply = self.call("GetMissedMessages", None, GLib.VariantType("(s)"))
        if not reply:
            return []
        try:
            return json.loads(reply[0])
        except Exception as e:
            logger.error(f"[DaemonClient] Bad missed messages payload: {e}")
            return []

    def send_missed_message(self, msg_id):
        """Send a missed scheduled message without waiting for the reply."""
        self.call_async("SendMissedMessage", GLib.Variant("(i)", (msg_id,)))

    def import_chatty(self, db_path, mms_path):
        """Import a chatty database; blocking, call from a worker."""
        reply = self.call("ImportChatty", GLib.Variant("(ss)", (db_path, mms_path)),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def import_local_calls(self, db_path):
        """Import a gnome-calls database; blocking, call from a worker."""
        reply = self.call("ImportLocalCalls", GLib.Variant("(s)", (db_path,)),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def import_android_sms(self, file_path):
        """Import an Android SMS backup; blocking, call from a worker."""
        reply = self.call("ImportAndroidSms", GLib.Variant("(s)", (file_path,)),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def import_android_calls(self, file_path):
        """Import an Android call backup; blocking, call from a worker."""
        reply = self.call("ImportAndroidCalls", GLib.Variant("(s)", (file_path,)),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def import_ios_sms(self, file_path, manifest_path=None, backup_dir=None):
        """Import an iOS SMS database; blocking, call from a worker."""
        reply = self.call("ImportIosSms",
                          GLib.Variant("(sss)", (file_path, manifest_path or "", backup_dir or "")),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def import_ios_calls(self, file_path):
        """Import an iOS call database; blocking, call from a worker."""
        reply = self.call("ImportIosCalls", GLib.Variant("(s)", (file_path,)),
                          GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply if reply else (False, "")

    def save_contact(self, vcard, uid=None, source_uid=None):
        """Write a full vCard to a book; blocking, call from a worker."""
        reply = self.call("SaveContact",
                          GLib.Variant("(sss)", (vcard, uid or "", source_uid or "")),
                          GLib.VariantType("(b)"))
        return bool(reply and reply[0])

    def delete_contact(self, uid):
        """Delete one contact; blocking, call from a worker."""
        reply = self.call("DeleteContact", GLib.Variant("(s)", (uid,)))
        return reply is not None

    def delete_contacts(self, uids):
        """Delete a batch of contacts; blocking, call from a worker."""
        reply = self.call("DeleteContacts", GLib.Variant("(s)", (json.dumps(list(uids)),)),
                          timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply is not None

    def refresh_contacts(self):
        """Ask the backends to re-sync; blocking, call from a worker."""
        reply = self.call("RefreshContacts", None, GLib.VariantType("(i)"),
                          timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply[0] if reply else 0

    def import_contacts(self, vcard_data, source_uid=None):
        """Import vCards into a book; blocking, call from a worker."""
        reply = self.call("ImportContacts",
                          GLib.Variant("(ss)", (vcard_data, source_uid or "")),
                          GLib.VariantType("(i)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply[0] if reply else 0

    def import_sim_contacts(self, source_uid=None):
        """Import the SIM phonebook into a book; blocking, call from a worker.

        Returns (count, message) where message is a stable code, or None
        when the owner could not be reached.
        """
        return self.call("ImportSimContacts", GLib.Variant("(s)", (source_uid or "",)),
                         GLib.VariantType("(is)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def get_telephony_state(self):
        """Read the full state snapshot; blocking, call from a worker."""
        reply = self.call("GetTelephonyState", None, GLib.VariantType("(a{sv})"))
        return reply[0] if reply is not None else None

    def set_active_chat(self, number):
        """Tell the owner which chat is open so its alerts stay quiet."""
        self.call_async("SetActiveChat", GLib.Variant("(s)", (number or "",)))

    def disable_all_forwarding(self):
        """Clear every forwarding rule; blocking, call from a worker."""
        return self.call("DisableAllForwarding", None,
                         GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def disable_all_barrings(self, password):
        """Clear every barring rule; blocking, call from a worker."""
        return self.call("DisableAllBarrings", GLib.Variant("(s)", (password,)),
                         GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def change_barring_password(self, old, new):
        """Change the network barring password; blocking, call from a worker."""
        return self.call("ChangeBarringPassword", GLib.Variant("(ss)", (old, new)),
                         GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def set_setting(self, key, value):
        """Ask the owner to persist one setting; dconf notifies readers."""
        packed = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
        self.call_async("SetSetting", GLib.Variant("(ss)", (key, packed)))

    def get_own_number(self):
        """Read the subscriber's number; blocking, call from a worker."""
        reply = self.call("GetOwnNumber", None, GLib.VariantType("(s)"))
        return reply[0] if reply and reply[0] else None

    def detect_region(self):
        """Detect the network region; blocking, call from a worker."""
        reply = self.call("DetectRegion", None, GLib.VariantType("(s)"))
        return reply[0] if reply and reply[0] else None

    def request_recovery(self, callback):
        """Run modem recovery; callback hears the verdict, or None."""
        self.call_with_reply("RequestRecovery", None, callback,
                             timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def clear_notification(self, number):
        """Tell the owner its alerts for a number were seen."""
        self.call_async("ClearNotification", GLib.Variant("(s)", (number,)))

    def silence_ring(self):
        """Ask the owner to stop the ringer."""
        self.call_async("SilenceRing")

    def set_audio_route(self, route):
        """Ask the owner to move call audio to an output route."""
        self.call_async("SetAudioRoute", GLib.Variant("(s)", (route,)))

    def set_input_route(self, route):
        """Ask the owner to move call input to a route."""
        self.call_async("SetInputRoute", GLib.Variant("(s)", (route,)))

    def set_delivery_reports(self, enabled):
        """Ask the network for delivery reports; blocking, call from a worker."""
        return self.call("SetDeliveryReports", GLib.Variant("(b)", (bool(enabled),)),
                         GLib.VariantType("(bs)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def set_mic_muted(self, muted):
        """Ask the owner to mute or unmute the microphone."""
        self.call_async("MuteMic" if muted else "UnmuteMic")

    def get_audio_routes(self):
        """List selectable routes; blocking, call from a worker.

        Returns (outputs, inputs) as (id, available) pairs, or None
        when the owner is away.
        """
        return self.call("GetAudioRoutes", None, GLib.VariantType("(a(sb)a(sb))"))

    def prepare_attachment(self, source_path, max_bytes):
        """Have the owner store and fit an attachment; blocking, call from a worker.

        Returns (path, code) — path is empty when preparation failed —
        or None when the owner could not be reached.
        """
        return self.call("PrepareAttachment",
                         GLib.Variant("(si)", (source_path, int(max_bytes))),
                         GLib.VariantType("(ss)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)

    def clear_contacts(self, source_uid=None):
        """Delete every contact of a source, or all unprotected ones; blocking."""
        reply = self.call("ClearContacts", GLib.Variant("(s)", (source_uid or "",)),
                          timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return reply is not None

    def delete_address_book(self, source_uid):
        """Delete a whole address book; blocking, call from a worker."""
        reply = self.call("DeleteAddressBook", GLib.Variant("(s)", (source_uid,)),
                          GLib.VariantType("(b)"), timeout_ms=DAEMON_SLOW_CALL_TIMEOUT_MS)
        return bool(reply and reply[0])
