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
import pyotp
from gi.repository import Gio
from loguru import logger
from ...backend.utils.phone_utils import normalize_number
from .secret_manager import SecretManager


class GSettingsManager:
    """Manages reading and writing application settings via GSettings."""

    def __init__(self):
        self.gsettings = Gio.Settings(schema_id="io.furios.Telephony")
        self.secret_manager = SecretManager()

    def get_setting(self, key):
        """Retrieve a setting value by key via gsettings."""
        try:
            g_key = key.replace("_", "-")

            if g_key not in self.gsettings.props.settings_schema.list_keys():
                return None

            key_type = self.gsettings.props.settings_schema.get_key(g_key).get_value_type().dup_string()
            if key_type == 'b':
                val = self.gsettings.get_boolean(g_key)
                return "true" if val else "false"

            val = self.gsettings.get_string(g_key)
            if not val and g_key in [
                "emergency-numbers", "trusted-sms-location-request",
                "trusted-sms-silent-callback", "trusted-sms-relay",
                "trusted-sms-ssh-access", "trusted-sms-remote-wipe",
                "notification-override-dnd-bypass-contacts",
                "notification-override-sms-custom-tone-contacts",
                "notification-override-call-custom-contacts",
                "address-book-sources"
            ]:
                return "[]"
            return val if val else None
        except Exception as e:
            logger.error(f"[GSettings] Get Setting Error for {key}: {e}")
            return None

    def set_setting(self, key, val):
        """Store a setting value via gsettings."""
        try:
            g_key = key.replace("_", "-")

            if g_key not in self.gsettings.props.settings_schema.list_keys():
                return

            key_type = self.gsettings.props.settings_schema.get_key(g_key).get_value_type().dup_string()
            if key_type == 'b':
                b_val = str(val).lower() == "true" if isinstance(val, str) else bool(val)
                self.gsettings.set_boolean(g_key, b_val)
                return

            self.gsettings.set_string(g_key, val)
        except Exception as e:
            logger.error(f"[GSettings] Settings Error for {key}: {e}")

    def get_emergency_numbers(self):
        try:
            val = self.get_setting("emergency_numbers")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get Emergency Numbers Error: {e}")
        return []

    def set_emergency_numbers(self, numbers_list):
        try:
            val = json.dumps(numbers_list)
            self.set_setting("emergency_numbers", val)
        except Exception as e:
            logger.error(f"[GSettings] Set Emergency Numbers Error: {e}")

    def get_trusted_sms_location_request(self):
        try:
            val = self.get_setting("trusted_sms_location_request")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get Location Request Error: {e}")
        return []

    def set_trusted_sms_location_request(self, contacts_list):
        try:
            val = json.dumps(contacts_list)
            self.set_setting("trusted_sms_location_request", val)
        except Exception as e:
            logger.error(f"[GSettings] Set Location Request Error: {e}")

    def get_trusted_sms_silent_callback(self):
        try:
            val = self.get_setting("trusted_sms_silent_callback")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get Callback Error: {e}")
        return []

    def set_trusted_sms_silent_callback(self, contacts_list):
        try:
            val = json.dumps(contacts_list)
            self.set_setting("trusted_sms_silent_callback", val)
        except Exception as e:
            logger.error(f"[GSettings] Set Callback Error: {e}")

    def get_trusted_sms_relay(self):
        try:
            val = self.get_setting("trusted_sms_relay")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get Relay Error: {e}")
        return []

    def set_trusted_sms_relay(self, contacts_list):
        try:
            val = json.dumps(contacts_list)
            self.set_setting("trusted_sms_relay", val)
        except Exception as e:
            logger.error(f"[GSettings] Set Relay Error: {e}")

    def get_trusted_sms_ssh_access(self):
        try:
            val = self.get_setting("trusted_sms_ssh_access")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get SSH Access Error: {e}")
        return []

    def set_trusted_sms_ssh_access(self, contacts_list):
        try:
            val = json.dumps(contacts_list)
            self.set_setting("trusted_sms_ssh_access", val)
        except Exception as e:
            logger.error(f"[GSettings] Set SSH Access Error: {e}")

    def get_trusted_sms_remote_wipe(self):
        try:
            val = self.get_setting("trusted_sms_remote_wipe")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get Remote Wipe Error: {e}")
        return []

    def set_trusted_sms_remote_wipe(self, contacts_list):
        try:
            val = json.dumps(contacts_list)
            self.set_setting("trusted_sms_remote_wipe", val)
        except Exception as e:
            logger.error(f"[GSettings] Set Remote Wipe Error: {e}")


    def set_trusted_sms_location_request_enabled(self, val):
        self.set_setting("trusted_sms_location_request_enabled", val)

    def set_trusted_sms_silent_callback_enabled(self, val):
        self.set_setting("trusted_sms_silent_callback_enabled", val)

    def set_trusted_sms_relay_enabled(self, val):
        self.set_setting("trusted_sms_relay_enabled", val)

    def set_trusted_sms_ssh_access_enabled(self, val):
        self.set_setting("trusted_sms_ssh_access_enabled", val)

    def set_trusted_sms_remote_wipe_enabled(self, val):
        self.set_setting("trusted_sms_remote_wipe_enabled", val)

    def get_trusted_sms_location_request_enabled(self):
        return self.get_setting("trusted_sms_location_request_enabled") == "true"

    def get_trusted_sms_location_request_totp_seed(self):
        secret = self.secret_manager.get_secret("trusted_sms_location_request")
        if not secret:
            return ""
        return secret

    def set_trusted_sms_location_request_totp_seed(self, val):
        self.secret_manager.store_secret("trusted_sms_location_request", val)

    def remove_trusted_sms_location_request_totp_seed(self):
        self.secret_manager.clear_secret("trusted_sms_location_request")

    def get_trusted_sms_silent_callback_enabled(self):
        return self.get_setting("trusted_sms_silent_callback_enabled") == "true"

    def get_trusted_sms_silent_callback_totp_seed(self):
        secret = self.secret_manager.get_secret("trusted_sms_silent_callback")
        if not secret:
            return ""
        return secret

    def set_trusted_sms_silent_callback_totp_seed(self, val):
        self.secret_manager.store_secret("trusted_sms_silent_callback", val)

    def remove_trusted_sms_silent_callback_totp_seed(self):
        self.secret_manager.clear_secret("trusted_sms_silent_callback")

    def get_trusted_sms_relay_enabled(self):
        return self.get_setting("trusted_sms_relay_enabled") == "true"

    def get_trusted_sms_relay_totp_seed(self):
        secret = self.secret_manager.get_secret("trusted_sms_relay")
        if not secret:
            return ""
        return secret

    def set_trusted_sms_relay_totp_seed(self, val):
        self.secret_manager.store_secret("trusted_sms_relay", val)

    def remove_trusted_sms_relay_totp_seed(self):
        self.secret_manager.clear_secret("trusted_sms_relay")

    def get_trusted_sms_ssh_access_enabled(self):
        return self.get_setting("trusted_sms_ssh_access_enabled") == "true"

    def get_trusted_sms_ssh_access_totp_seed(self):
        secret = self.secret_manager.get_secret("trusted_sms_ssh_access")
        if not secret:
            return ""
        return secret

    def set_trusted_sms_ssh_access_totp_seed(self, val):
        self.secret_manager.store_secret("trusted_sms_ssh_access", val)

    def remove_trusted_sms_ssh_access_totp_seed(self):
        self.secret_manager.clear_secret("trusted_sms_ssh_access")

    def get_trusted_sms_remote_wipe_enabled(self):
        return self.get_setting("trusted_sms_remote_wipe_enabled") == "true"

    def get_trusted_sms_remote_wipe_totp_seed(self):
        secret = self.secret_manager.get_secret("trusted_sms_remote_wipe")
        if not secret:
            return ""
        return secret

    def set_trusted_sms_remote_wipe_totp_seed(self, val):
        self.secret_manager.store_secret("trusted_sms_remote_wipe", val)

    def remove_trusted_sms_remote_wipe_totp_seed(self):
        self.secret_manager.clear_secret("trusted_sms_remote_wipe")

    def generate_totp_seed(self):
        """Generates a random base32 TOTP seed."""
        return pyotp.random_base32()

    def get_totp_uri(self, seed, action_name):
        """Returns a standard otpauth://totp/ URI."""
        return f"otpauth://totp/Telephony:{action_name}?secret={seed}&issuer=Telephony&period=60"

    def get_notification_override_dnd_bypass_contacts(self):
        try:
            val = self.get_setting("notification_override_dnd_bypass_contacts")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get DND Bypass Error: {e}")
        return []

    def set_notification_override_dnd_bypass_contacts(self, contacts_list):
        try:
            val = json.dumps(contacts_list)
            self.set_setting("notification_override_dnd_bypass_contacts", val)
        except Exception as e:
            logger.error(f"[GSettings] Set DND Bypass Error: {e}")

    def get_notification_override_sms_custom_tone_contacts(self):
        try:
            val = self.get_setting("notification_override_sms_custom_tone_contacts")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get SMS Custom Tones Error: {e}")
        return []

    def set_notification_override_sms_custom_tone_contacts(self, tones_list):
        try:
            val = json.dumps(tones_list)
            self.set_setting("notification_override_sms_custom_tone_contacts", val)
        except Exception as e:
            logger.error(f"[GSettings] Set SMS Custom Tones Error: {e}")

    def get_notification_override_call_custom_contacts(self):
        try:
            val = self.get_setting("notification_override_call_custom_contacts")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get Ring Custom Tones Error: {e}")
        return []

    def set_notification_override_call_custom_contacts(self, tones_list):
        try:
            val = json.dumps(tones_list)
            self.set_setting("notification_override_call_custom_contacts", val)
        except Exception as e:
            logger.error(f"[GSettings] Set Ring Custom Tones Error: {e}")

    def _update_special_list(self, key, target_number, action, new_name=None):
        """Helper to update special lists (Trusted, Priority, Custom Tones)."""
        try:
            val = self.get_setting(key)
            if not val:
                return

            items = json.loads(val)
            if not items:
                return

            changed = False
            norm_target = normalize_number(target_number)

            new_list = []
            for item in items:
                item_num = normalize_number(item.get("number", ""))

                if item_num == norm_target:
                    if action == "remove":
                        changed = True
                        continue
                    elif action == "update_name" and new_name:
                        if item.get("name") != new_name:
                            item["name"] = new_name
                            changed = True
                    elif action == "reset_name":
                        if item.get("name") != "Unknown":
                            item["name"] = "Unknown"
                            changed = True

                new_list.append(item)

            if changed:
                self.set_setting(key, json.dumps(new_list))
                logger.info(f"[GSettings] Updated special list '{key}' for {target_number} (Action: {action})")

        except Exception as e:
            logger.error(f"[GSettings] Update special list error for {key}: {e}")

    def remove_from_special_lists(self, number):
        keys = [
            "trusted_sms_location_request", "trusted_sms_silent_callback",
            "trusted_sms_relay", "trusted_sms_ssh_access",
            "trusted_sms_remote_wipe", "notification_override_dnd_bypass_contacts",
            "notification_override_sms_custom_tone_contacts",
            "notification_override_call_custom_contacts"
        ]
        for k in keys:
            self._update_special_list(k, number, "remove")

    def update_special_list_names(self, number, new_name):
        keys = [
            "trusted_sms_location_request", "trusted_sms_silent_callback",
            "trusted_sms_relay", "trusted_sms_ssh_access",
            "trusted_sms_remote_wipe", "notification_override_dnd_bypass_contacts",
            "notification_override_sms_custom_tone_contacts",
            "notification_override_call_custom_contacts"
        ]
        for k in keys:
            self._update_special_list(k, number, "update_name", new_name=new_name)

    def reset_special_list_names(self, number):
        keys = [
            "trusted_sms_location_request", "trusted_sms_silent_callback",
            "trusted_sms_relay", "trusted_sms_ssh_access",
            "trusted_sms_remote_wipe", "notification_override_dnd_bypass_contacts",
            "notification_override_sms_custom_tone_contacts",
            "notification_override_call_custom_contacts"
        ]
        for k in keys:
            self._update_special_list(k, number, "reset_name")
