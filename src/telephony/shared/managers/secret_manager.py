# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import gi
from telephony.shared.utils.log_utils import logger


class SecretManager:

    def __init__(self):
        self.secret_schema = None

    def secret(self):
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        if self.secret_schema is None:
            self.secret_schema = Secret.Schema.new(
                "io.furios.Telephony.Totp",
                Secret.SchemaFlags.NONE,
                {"action": Secret.SchemaAttributeType.STRING}
            )
        return Secret, self.secret_schema

    def get_secret(self, action_name):
        try:
            Secret, schema = self.secret()
            return Secret.password_lookup_sync(schema, {"action": action_name}, None)
        except Exception as e:
            logger.error(f"[SecretManager] Error getting secret for {action_name}: {e}")
            return None

    def store_secret(self, action_name, secret_value):
        try:
            Secret, schema = self.secret()
            Secret.password_store_sync(
                schema,
                {"action": action_name},
                Secret.COLLECTION_DEFAULT,
                f"Telephony TOTP Seed: {action_name}",
                secret_value,
                None
            )
        except Exception as e:
            logger.error(f"[SecretManager] Error storing secret for {action_name}: {e}")

    def clear_secret(self, action_name):
        try:
            Secret, schema = self.secret()
            Secret.password_clear_sync(schema, {"action": action_name}, None)
        except Exception as e:
            logger.error(f"[SecretManager] Error clearing secret for {action_name}: {e}")
