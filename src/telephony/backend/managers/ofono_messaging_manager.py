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

from gi.repository import GLib, Gio
from loguru import logger

from gettext import gettext as _
from ..utils.phone_utils import normalize_number
import time


class OfonoMessagingManager:
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
            err = str(e)
            if any(x in err for x in ["Operation failed", "Timeout", "NoReply", "org.ofono.Error.Failed"]):
                logger.warning(f"[OfonoManager] SMS Error Ignored (Likely Sent): {e}")
                return True
            else:
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

        GLib.timeout_add_seconds(60, self._timeout_sms, row_id)

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
        except Exception:
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
                    while len(self.unclaimed_sms_states) > 20:
                        self.unclaimed_sms_states.pop(next(iter(self.unclaimed_sms_states)))

        if row_id is not None:
            self._resolve_sms(row_id, value)

    def send_quick_response(self, number, text):
        """Send an SMS and record it in the local message database."""
        if not self.send_sms(number, text):
            return False

        try:
            self.db.add_message(number, "outgoing", text, "sent", sender="Me")
        except Exception as e:
            logger.error(f"[OfonoManager] Failed to record quick response: {e}")
        return True

    def send_ussd(self, command):
        """Send a USSD command."""
        if not self.ussd_proxy:
            return _("Not supported")
        try:
            res = self.ussd_proxy.call_sync("Initiate", GLib.Variant("(s)", (command,)), Gio.DBusCallFlags.NONE, -1, None)
            return res.unpack()[0]
        except Exception as e:
            return _("Error: {e}").format(e=e)

    def on_message_signal(self, proxy, sender, signal, params):
        """Handle incoming message signals."""
        if signal in ["IncomingMessage", "ImmediateMessage"]:
            args = params.unpack()
            body = args[0]
            props = args[1]
            raw_sender = props.get('Sender', 'Unknown')
            msg_sender = normalize_number(raw_sender)

            if self.db.is_blocked(msg_sender):
                return

            if signal == "ImmediateMessage":
                logger.info(f"[ImmediateMessage] Forcing full feedback profile for emergency message from {msg_sender}")
                self.audio.force_max_feedback()

                def _play_emergency_tone(count=0):
                    if count >= 5:
                        self.audio.force_max_feedback(restore=True)
                        return False

                    return False

                GLib.idle_add(_play_emergency_tone)
            else:
                self._check_priority_contact(msg_sender)

            sent_time = props.get('SentTime', '')
            if not sent_time:
                sent_time = props.get('LocalSentTime', '')

            sms_signature = f"{msg_sender}_{sent_time}_{body}"

            if sms_signature in self.seen_sms_signatures:
                logger.warning(f"[OfonoManager] Duplicate SMS ignored: {sms_signature}")
                return

            self.seen_sms_signatures.append(sms_signature)
            if len(self.seen_sms_signatures) > 50:
                self.seen_sms_signatures.pop(0)

            self._check_secret_actions(msg_sender, body)

            status = "unread"
            if self.active_chat_number and msg_sender == self.active_chat_number and self.is_app_focused():
                status = "read"

            self.db.add_message(msg_sender, "incoming", body, status, sender=msg_sender)
            self.emit('incoming-message', msg_sender, body)
