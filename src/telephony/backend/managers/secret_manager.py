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
from telephony.backend.utils.log_utils import logger


class SecretManager:
    """Manages reading and writing application secrets via libsecret GNOME keyring."""

    def __init__(self):
        """Initialize with the schema unbuilt; secrets are rarely touched."""
        self.secret_schema = None

    def _secret(self):
        """Return the Secret module and schema, importing on first use.

        Deferred because libsecret drags cairo along and a process only
        touches secrets when a trusted action is configured or fires.
        """
        gi.require_version('Secret', '1')
        from gi.repository import Secret
        if self.secret_schema is None:
            self.secret_schema = Secret.Schema.new(
                "io.furios.Telephony.Totp",
                Secret.SchemaFlags.NONE,
                {"action": Secret.SchemaAttributeType.STRING}
            )
        return Secret, self.secret_schema

    def get_secret(self, action_name):
        """Retrieves a secret from the keyring by action name."""
        try:
            Secret, schema = self._secret()
            return Secret.password_lookup_sync(schema, {"action": action_name}, None)
        except Exception as e:
            logger.error(f"[SecretManager] Error getting secret for {action_name}: {e}")
            return None

    def store_secret(self, action_name, secret_value):
        """Stores a secret in the keyring under the given action name."""
        try:
            Secret, schema = self._secret()
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
        """Removes a secret from the keyring by action name."""
        try:
            Secret, schema = self._secret()
            Secret.password_clear_sync(schema, {"action": action_name}, None)
        except Exception as e:
            logger.error(f"[SecretManager] Error clearing secret for {action_name}: {e}")
