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

from telephony.backend.managers.gsettings_manager import GSettingsManager


class SettingsMirror(GSettingsManager):
    """Settings view of a window process: reads locally, writes via the owner.

    dconf stays the arbiter and its change signal the notifier, so every
    reader keeps its local subscription; only the write travels, which
    makes the daemon the single dconf writer. Every write helper in the
    base class funnels through set_setting, so this one override covers
    them all.
    """

    def __init__(self, daemon_client):
        super().__init__()
        self.daemon = daemon_client

    def set_setting(self, key, val):
        """Ask the owner to persist one setting."""
        self.daemon.set_setting(key, val)
