# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import json
from gi.repository import Gio
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.managers.secret_manager import SecretManager

SPECIAL_LIST_KEYS = [
    "trusted_sms_location_request", "trusted_sms_silent_callback",
    "trusted_sms_relay", "trusted_sms_ssh_access",
    "trusted_sms_lock_device", "notification_override_dnd_bypass_contacts",
    "notification_override_dnd_bypass_contacts_messages",
    "notification_override_sms_custom_tone_contacts",
    "notification_override_call_custom_contacts"
]


class GSettingsManager:

    def __init__(self):
        self.gsettings = Gio.Settings(schema_id="io.furios.Telephony")
        self.secret_manager = SecretManager()

    def get_setting(self, key):
        try:
            g_key = key.replace("_", "-")

            if g_key not in self.gsettings.props.settings_schema.list_keys():
                return None

            key_type = self.gsettings.props.settings_schema.get_key(g_key).get_value_type().dup_string()
            if key_type == "b":
                val = self.gsettings.get_boolean(g_key)
                return "true" if val else "false"

            val = self.gsettings.get_string(g_key)
            if not val and g_key in [
                "emergency-numbers", "trusted-sms-location-request",
                "trusted-sms-silent-callback", "trusted-sms-relay",
                "trusted-sms-ssh-access", "trusted-sms-lock-device",
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
        try:
            g_key = key.replace("_", "-")

            if g_key not in self.gsettings.props.settings_schema.list_keys():
                logger.warning(f"[GSettings] Dropping write to unknown key {g_key}; "
                               "a schema installed after this process started is not visible to it")
                return

            key_type = self.gsettings.props.settings_schema.get_key(g_key).get_value_type().dup_string()
            if key_type == "b":
                b_val = str(val).lower() == "true" if isinstance(val, str) else bool(val)
                self.gsettings.set_boolean(g_key, b_val)
                return

            self.gsettings.set_string(g_key, val)
        except Exception as e:
            logger.error(f"[GSettings] Settings Error for {key}: {e}")

    def get_call_volume_levels(self):
        levels = {"earpiece": 80, "speaker": 80, "wired": 80, "bluetooth": 80, "usb": 80}
        try:
            val = self.get_setting("call_volume_levels")
            if val:
                saved = json.loads(val)
                for route in levels:
                    if route in saved:
                        levels[route] = int(saved[route])
        except Exception as e:
            logger.error(f"[GSettings] Get Call Volume Levels Error: {e}")
        return levels

    def set_call_volume_levels(self, levels_dict):
        try:
            self.set_setting("call_volume_levels", json.dumps(levels_dict))
        except Exception as e:
            logger.error(f"[GSettings] Set Call Volume Levels Error: {e}")

    def get_favorites(self):
        return self.get_json_setting("favorites", "Favorites")

    def set_favorites(self, entries):
        self.set_json_setting("favorites", entries, "Favorites")

    def get_muted_conversations(self):
        try:
            val = self.get_setting("muted_conversations")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get Muted Conversations Error: {e}")
        return []

    def is_conversation_muted(self, conversation_id):
        return conversation_id in self.get_muted_conversations()

    def set_conversation_muted(self, conversation_id, muted):
        current = self.get_muted_conversations()
        if muted and conversation_id not in current:
            current.append(conversation_id)
        elif not muted and conversation_id in current:
            current.remove(conversation_id)
        else:
            return
        self.set_setting("muted_conversations", json.dumps(current))

    def get_reject_call_messages(self):
        try:
            val = self.get_setting("reject_call_messages")
            if val:
                messages = json.loads(val)
                if messages:
                    return messages
        except Exception as e:
            logger.error(f"[GSettings] Get Reject Messages Error: {e}")

        legacy = self.get_setting("reject_call_message")
        if legacy:
            return [legacy]
        return []

    def set_reject_call_messages(self, messages_list):
        try:
            self.set_setting("reject_call_messages", json.dumps(messages_list))
        except Exception as e:
            logger.error(f"[GSettings] Set Reject Messages Error: {e}")

    def get_json_setting(self, key, error_label):
        try:
            val = self.get_setting(key)
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[GSettings] Get {error_label} Error: {e}")
        return []

    def set_json_setting(self, key, value, error_label):
        try:
            self.set_setting(key, json.dumps(value))
        except Exception as e:
            logger.error(f"[GSettings] Set {error_label} Error: {e}")

    def get_emergency_numbers(self):
        return self.get_json_setting("emergency_numbers", "Emergency Numbers")

    def set_emergency_numbers(self, numbers_list):
        self.set_json_setting("emergency_numbers", numbers_list, "Emergency Numbers")

    def get_trusted_sms_location_request(self):
        return self.get_json_setting("trusted_sms_location_request", "Location Request")

    def set_trusted_sms_location_request(self, contacts_list):
        self.set_json_setting("trusted_sms_location_request", contacts_list, "Location Request")

    def get_trusted_sms_silent_callback(self):
        return self.get_json_setting("trusted_sms_silent_callback", "Callback")

    def set_trusted_sms_silent_callback(self, contacts_list):
        self.set_json_setting("trusted_sms_silent_callback", contacts_list, "Callback")

    def get_trusted_sms_relay(self):
        return self.get_json_setting("trusted_sms_relay", "Relay")

    def set_trusted_sms_relay(self, contacts_list):
        self.set_json_setting("trusted_sms_relay", contacts_list, "Relay")

    def get_trusted_sms_ssh_access(self):
        return self.get_json_setting("trusted_sms_ssh_access", "SSH Access")

    def set_trusted_sms_ssh_access(self, contacts_list):
        self.set_json_setting("trusted_sms_ssh_access", contacts_list, "SSH Access")

    def get_trusted_sms_lock_device(self):
        return self.get_json_setting("trusted_sms_lock_device", "Lock Device")

    def set_trusted_sms_lock_device(self, contacts_list):
        self.set_json_setting("trusted_sms_lock_device", contacts_list, "Lock Device")


    def set_trusted_sms_location_request_enabled(self, val):
        self.set_setting("trusted_sms_location_request_enabled", val)

    def set_trusted_sms_silent_callback_enabled(self, val):
        self.set_setting("trusted_sms_silent_callback_enabled", val)

    def set_trusted_sms_relay_enabled(self, val):
        self.set_setting("trusted_sms_relay_enabled", val)

    def set_trusted_sms_ssh_access_enabled(self, val):
        self.set_setting("trusted_sms_ssh_access_enabled", val)

    def set_trusted_sms_lock_device_enabled(self, val):
        self.set_setting("trusted_sms_lock_device_enabled", val)

    def get_trusted_sms_location_request_enabled(self):
        return self.get_setting("trusted_sms_location_request_enabled") == "true"

    def get_totp_seed(self, action):
        secret = self.secret_manager.get_secret(action)
        if not secret:
            return ""
        return secret

    def set_totp_seed(self, action, seed):
        self.secret_manager.store_secret(action, seed)

    def remove_totp_seed(self, action):
        self.secret_manager.clear_secret(action)

    def get_trusted_sms_location_request_totp_seed(self):
        return self.get_totp_seed("trusted_sms_location_request")

    def set_trusted_sms_location_request_totp_seed(self, val):
        self.set_totp_seed("trusted_sms_location_request", val)

    def remove_trusted_sms_location_request_totp_seed(self):
        self.remove_totp_seed("trusted_sms_location_request")

    def get_trusted_sms_silent_callback_enabled(self):
        return self.get_setting("trusted_sms_silent_callback_enabled") == "true"

    def get_trusted_sms_silent_callback_totp_seed(self):
        return self.get_totp_seed("trusted_sms_silent_callback")

    def set_trusted_sms_silent_callback_totp_seed(self, val):
        self.set_totp_seed("trusted_sms_silent_callback", val)

    def remove_trusted_sms_silent_callback_totp_seed(self):
        self.remove_totp_seed("trusted_sms_silent_callback")

    def get_trusted_sms_relay_enabled(self):
        return self.get_setting("trusted_sms_relay_enabled") == "true"

    def get_trusted_sms_relay_totp_seed(self):
        return self.get_totp_seed("trusted_sms_relay")

    def set_trusted_sms_relay_totp_seed(self, val):
        self.set_totp_seed("trusted_sms_relay", val)

    def remove_trusted_sms_relay_totp_seed(self):
        self.remove_totp_seed("trusted_sms_relay")

    def get_trusted_sms_ssh_access_enabled(self):
        return self.get_setting("trusted_sms_ssh_access_enabled") == "true"

    def get_trusted_sms_ssh_access_totp_seed(self):
        return self.get_totp_seed("trusted_sms_ssh_access")

    def set_trusted_sms_ssh_access_totp_seed(self, val):
        self.set_totp_seed("trusted_sms_ssh_access", val)

    def remove_trusted_sms_ssh_access_totp_seed(self):
        self.remove_totp_seed("trusted_sms_ssh_access")

    def get_trusted_sms_lock_device_enabled(self):
        return self.get_setting("trusted_sms_lock_device_enabled") == "true"

    def get_trusted_sms_lock_device_totp_seed(self):
        return self.get_totp_seed("trusted_sms_lock_device")

    def set_trusted_sms_lock_device_totp_seed(self, val):
        self.set_totp_seed("trusted_sms_lock_device", val)

    def remove_trusted_sms_lock_device_totp_seed(self):
        self.remove_totp_seed("trusted_sms_lock_device")

    def generate_totp_seed(self):
        import pyotp
        return pyotp.random_base32()

    def get_totp_uri(self, seed, action_name):
        return f"otpauth://totp/Telephony:{action_name}?secret={seed}&issuer=Telephony&period=60"

    def get_notification_override_dnd_bypass_contacts(self):
        return self.get_json_setting("notification_override_dnd_bypass_contacts", "DND Bypass")

    def set_notification_override_dnd_bypass_contacts(self, contacts_list):
        self.set_json_setting("notification_override_dnd_bypass_contacts", contacts_list, "DND Bypass")

    def get_notification_override_dnd_bypass_contacts_messages(self):
        return self.get_json_setting("notification_override_dnd_bypass_contacts_messages", "DND Bypass")

    def set_notification_override_dnd_bypass_contacts_messages(self, contacts_list):
        self.set_json_setting("notification_override_dnd_bypass_contacts_messages", contacts_list, "DND Bypass")

    def get_notification_override_sms_custom_tone_contacts(self):
        return self.get_json_setting("notification_override_sms_custom_tone_contacts", "SMS Custom Tones")

    def set_notification_override_sms_custom_tone_contacts(self, tones_list):
        self.set_json_setting("notification_override_sms_custom_tone_contacts", tones_list, "SMS Custom Tones")

    def get_notification_override_call_custom_contacts(self):
        return self.get_json_setting("notification_override_call_custom_contacts", "Ring Custom Tones")

    def set_notification_override_call_custom_contacts(self, tones_list):
        self.set_json_setting("notification_override_call_custom_contacts", tones_list, "Ring Custom Tones")

    def update_special_list(self, key, target_number, action, new_name=None):
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
        for k in SPECIAL_LIST_KEYS:
            self.update_special_list(k, number, "remove")

    def update_special_list_names(self, number, new_name):
        for k in SPECIAL_LIST_KEYS:
            self.update_special_list(k, number, "update_name", new_name=new_name)

    def reset_special_list_names(self, number):
        for k in SPECIAL_LIST_KEYS:
            self.update_special_list(k, number, "reset_name")
