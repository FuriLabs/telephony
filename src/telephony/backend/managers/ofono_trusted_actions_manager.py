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

    def _check_secret_actions(self, sender, body):
        """Check if message matches any secret action trigger."""
        sender_clean = normalize_number(sender)
        body_clean = body.strip()

        now = time.time()
        if not hasattr(self, 'trusted_trigger_history'):
            self.trusted_trigger_history = {}
        history = self.trusted_trigger_history.get(sender_clean, {'last_success': 0, 'last_warning': 0, 'last_attempt': 0})

        is_trusted = False
        all_trusted = []
        try:
            all_trusted.extend(self.gsettings_mgr.get_trusted_sms_location_request())
            all_trusted.extend(self.gsettings_mgr.get_trusted_sms_silent_callback())
            all_trusted.extend(self.gsettings_mgr.get_trusted_sms_relay())
            all_trusted.extend(self.gsettings_mgr.get_trusted_sms_ssh_access())
            all_trusted.extend(self.gsettings_mgr.get_trusted_sms_remote_wipe())
            for t in all_trusted:
                if normalize_number(t.get("number", "")) == sender_clean:
                    is_trusted = True
                    break
        except Exception:
            pass

        if is_trusted:
            if (now - history['last_attempt']) < 5.0:
                logger.warning(f"[Security] Brute force rate limit hit for {sender_clean}")
                history['last_attempt'] = now
                self.trusted_trigger_history[sender_clean] = history

                return False

            history['last_attempt'] = now
            self.trusted_trigger_history[sender_clean] = history

        if self._check_trusted_sms_location_request(sender_clean, body_clean):
            return True
        if self._check_trusted_sms_silent_callback(sender_clean, body_clean):
            return True
        if self._check_trusted_sms_relay(sender_clean, body_clean):
            return True
        if self._check_trusted_sms_ssh_access(sender_clean, body_clean):
            return True
        if self._check_trusted_sms_remote_wipe(sender_clean, body_clean):
            return True

        return False

    def _check_trusted_sms_location_request(self, sender_clean, body_clean):
        try:
            seed = self.gsettings_mgr.get_trusted_sms_location_request_totp_seed()
            if not seed:
                logger.warning("[FindMyTelephony] TOTP seed not configured. Dropping message.")
                return False

            trusted = self.gsettings_mgr.get_trusted_sms_location_request()
            for t in trusted:
                t_num = normalize_number(t.get("number", ""))
                t_msg = t.get("secret", "").strip()
                if t_num and t_msg and sender_clean == t_num:
                    if body_clean.startswith(t_msg + " "):
                        parts = body_clean[len(t_msg):].strip().split(" ")
                        if len(parts) == 1 and self._verify_totp(seed, parts[0]):
                            limited, history = self._check_rate_limit(sender_clean, "FindMyTelephony")
                            if limited:
                                return True

                            self._mark_success(sender_clean, history)
                            logger.info(f"[FindMyTelephony] Trigger MATCH from {sender_clean}")

                            self.location_manager.get_current_location(
                                callback=lambda lat, lon, acc: self._send_location_response(sender_clean, lat, lon, acc),
                                progress_callback=lambda msg: self._send_progress_sms(sender_clean, msg)
                            )
                            return True
        except Exception as e:
            logger.error(f"[FindMyTelephony] Check error: {e}")
        return False

    def _check_trusted_sms_silent_callback(self, sender_clean, body_clean):
        try:
            seed = self.gsettings_mgr.get_trusted_sms_silent_callback_totp_seed()
            if not seed:
                logger.warning("[TrustedCallback] TOTP seed not configured. Dropping message.")
                return False

            trusted = self.gsettings_mgr.get_trusted_sms_silent_callback()
            for t in trusted:
                t_num = normalize_number(t.get("number", ""))
                t_msg = t.get("secret", "").strip()
                if t_num and t_msg and sender_clean == t_num:
                    if body_clean.startswith(t_msg + " "):
                        parts = body_clean[len(t_msg):].strip().split(" ")
                        if len(parts) == 1 and self._verify_totp(seed, parts[0]):
                            limited, history = self._check_rate_limit(sender_clean, "TrustedCallback")
                            if limited:
                                return True

                            self._mark_success(sender_clean, history)
                            logger.info(f"[TrustedCallback] Trigger MATCH from {sender_clean}")
                            self.callback_manager.execute_callback(sender_clean)
                            return True
        except Exception as e:
            logger.error(f"[TrustedCallback] Check error: {e}")
        return False

    def _check_trusted_sms_relay(self, sender_clean, body_clean):
        try:
            seed = self.gsettings_mgr.get_trusted_sms_relay_totp_seed()
            if not seed:
                logger.warning("[SMSRelay] TOTP seed not configured. Dropping message.")
                return False

            trusted = self.gsettings_mgr.get_trusted_sms_relay()
            for t in trusted:
                t_num = normalize_number(t.get("number", ""))
                t_msg = t.get("secret", "").strip()
                if t_num and t_msg and sender_clean == t_num:
                    if body_clean.startswith(t_msg + " "):
                        parts = body_clean[len(t_msg):].strip().split(" ", 2)
                        if len(parts) == 3 and self._verify_totp(seed, parts[0]):
                            limited, history = self._check_rate_limit(sender_clean, "SMSRelay")
                            if limited:
                                return True

                            self._mark_success(sender_clean, history)
                            target_number = parts[1]
                            message = parts[2]
                            self.relay_manager.execute_relay(sender_clean, target_number, message)
                            return True
        except Exception as e:
            logger.error(f"[SMSRelay] Check error: {e}")
        return False

    def _check_trusted_sms_ssh_access(self, sender_clean, body_clean):
        try:
            seed = self.gsettings_mgr.get_trusted_sms_ssh_access_totp_seed()
            if not seed:
                logger.warning("[SMStmate] TOTP seed not configured. Dropping message.")
                return False

            trusted = self.gsettings_mgr.get_trusted_sms_ssh_access()
            for t in trusted:
                t_num = normalize_number(t.get("number", ""))
                t_msg = t.get("secret", "").strip()
                if t_num and t_msg and sender_clean == t_num:
                    if body_clean.startswith(t_msg + " "):
                        parts = body_clean[len(t_msg):].strip().split(" ")
                        if len(parts) == 1 and self._verify_totp(seed, parts[0]):
                            limited, history = self._check_rate_limit(sender_clean, "SMStmate")
                            if limited:
                                return True

                            self._mark_success(sender_clean, history)
                            logger.info(f"[SMStmate] Trigger MATCH from {sender_clean}")
                            self.tmate_manager.start_session(sender_clean)
                            return True
        except Exception as e:
            logger.error(f"[SMStmate] Check error: {e}")
        return False

    def _check_trusted_sms_remote_wipe(self, sender_clean, body_clean):
        try:
            seed = self.gsettings_mgr.get_trusted_sms_remote_wipe_totp_seed()
            if not seed:
                logger.warning("[WipeDevice] TOTP seed not configured. Dropping message.")
                return False

            trusted = self.gsettings_mgr.get_trusted_sms_remote_wipe()
            for t in trusted:
                t_num = normalize_number(t.get("number", ""))
                t_msg = t.get("secret", "").strip()
                if t_num and t_msg and sender_clean == t_num:
                    if body_clean.startswith(t_msg + " "):
                        parts = body_clean[len(t_msg):].strip().split(" ", 3)
                        if len(parts) == 4 and self._verify_totp(seed, parts[0]):
                            limited, history = self._check_rate_limit(sender_clean, "WipeDevice")
                            if limited:
                                return True

                            self._mark_success(sender_clean, history)
                            current_pin = parts[1]
                            new_pin = parts[2]
                            sudo_pw = parts[3]
                            logger.info(f"[WipeDevice] Trigger MATCH from {sender_clean}")
                            self.wipe_manager.wipe_device(current_pin, new_pin, sudo_pw)
                            return True
        except Exception as e:
            logger.error(f"[WipeDevice] Check error: {e}")
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
            link = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}"
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
