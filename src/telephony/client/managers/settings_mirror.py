# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from telephony.shared.managers.gsettings_manager import GSettingsManager


class SettingsMirror(GSettingsManager):
    """Settings view of a window process: reads locally, writes via the owner. dconf stays the arbiter and its change signal the notifier, so every reader keeps its local subscription; only the write travels, which makes the daemon the single dconf writer."""

    def __init__(self, daemon_client):
        super().__init__()
        self.daemon = daemon_client

    def set_setting(self, key, val):
        self.daemon.set_setting(key, val)
