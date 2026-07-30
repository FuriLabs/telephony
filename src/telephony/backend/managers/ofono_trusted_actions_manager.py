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

from loguru import logger
import pyotp

from gettext import gettext as _
from ..utils.phone_utils import normalize_number
import time

OPENSTREETMAP_URL = "https://www.openstreetmap.org/"


class OfonoTrustedActionsManager:
    def _check_rate_limit(self, sender_clean, prefix):
        now = time.time()
        if not hasattr(self, 'trusted_trigger_history'):
            self.trusted_trigger_history = {}

        history = self.trusted_trigger_history.get(sender_clean, {'last_success': 0, 'last_warning': 0, 'last_attempt': 0})

        if (now - history['last_success']) < 60.0:
            logger.warning(f"[{prefix}] Success rate limit hit for {sender_clean}")
            if (now - history['last_warning']) > 60.0:
                msg = _("Please wait 60 seconds before triggering {prefix} again.").format(prefix=prefix)
                if self.send_sms(sender_clean, msg):
                    try:
                        self.db.add_message(sender_clean, "outgoing", msg, "sent", sender="Me")
                    except Exception:
                        pass
                history['last_warning'] = now

            self.trusted_trigger_history[sender_clean] = history
            return True, history

        return False, history

    def _mark_success(self, sender_clean, history):
        history['last_success'] = time.time()
        self.trusted_trigger_history[sender_clean] = history

    def _verify_totp(self, seed, code):
        if not seed or not code:
            return False
        try:
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
                "prefix": "WipeDevice",
                "seed_getter": self.gsettings_mgr.get_trusted_sms_remote_wipe_totp_seed,
                "list_getter": self.gsettings_mgr.get_trusted_sms_remote_wipe,
                "expected_parts": 4,
                "action": self._run_remote_wipe_action,
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

    def _run_remote_wipe_action(self, sender_clean, parts):
        """Execute the remote wipe action."""
        current_pin = parts[1]
        new_pin = parts[2]
        sudo_pw = parts[3]
        logger.info(f"[WipeDevice] Trigger MATCH from {sender_clean}")
        self.device_lock_manager.lock_device(current_pin, new_pin, sudo_pw)

    def _check_secret_actions(self, sender, body):
        """Check if message matches any secret action trigger."""
        sender_clean = normalize_number(sender)
        body_clean = body.strip()

        now = time.time()
        if not hasattr(self, 'trusted_trigger_history'):
            self.trusted_trigger_history = {}
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
        seed = entry["seed_getter"]()
        if not seed:
            logger.warning(f"[{prefix}] TOTP seed not configured. Dropping message.")
            return False

        if trusted is None:
            trusted = entry["list_getter"]()

        expected_parts = entry["expected_parts"]
        maxsplit = expected_parts - 1 if expected_parts > 1 else -1
        try:
            for t in trusted:
                t_num = normalize_number(t.get("number", ""))
                t_msg = t.get("secret", "").strip()
                if not t_num or not t_msg or sender_clean != t_num or not body_clean.startswith(t_msg + " "):
                    continue

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
