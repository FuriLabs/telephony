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

import gi
gi.require_version('Secret', '1')
from gi.repository import Secret
from loguru import logger


class SecretManager:
    """Manages reading and writing application secrets via libsecret GNOME keyring."""

    def __init__(self):
        """Initializes the secret schema for TOTP keys."""
        self.secret_schema = Secret.Schema.new(
            "io.furios.Telephony.Totp",
            Secret.SchemaFlags.NONE,
            {"action": Secret.SchemaAttributeType.STRING}
        )

    def get_secret(self, action_name):
        """Retrieves a secret from the keyring by action name."""
        try:
            return Secret.password_lookup_sync(self.secret_schema, {"action": action_name}, None)
        except Exception as e:
            logger.error(f"[SecretManager] Error getting secret for {action_name}: {e}")
            return None

    def store_secret(self, action_name, secret_value):
        """Stores a secret in the keyring under the given action name."""
        try:
            Secret.password_store_sync(
                self.secret_schema,
                {"action": action_name},
                Secret.COLLECTION_DEFAULT,
                f"Telephony TOTP Seed: {action_name}",
                secret_value,
                None
            )
        except Exception as e:
            logger.error(f"[SecretManager] Error storing secret for {action_name}: {e}")

    def clear_secret(self, action_name):
        """Removes a secret from the keyring by action name."""
        try:
            Secret.password_clear_sync(self.secret_schema, {"action": action_name}, None)
        except Exception as e:
            logger.error(f"[SecretManager] Error clearing secret for {action_name}: {e}")
