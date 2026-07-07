# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

"""Constants shared between modules. Single-module constants stay local."""

APP_ID = "io.furios.Telephony"
INCALL_APP_ID = "io.furios.Telephony.Incall"
EMERGENCY_APP_ID = "io.furios.Telephony.Emergency"
INCALL_DESKTOP_FILE = "io.furios.Telephony.Incall.desktop"
CALLS_DESKTOP_FILE = "io.furios.Telephony.Calls.desktop"
MESSAGES_DESKTOP_FILE = "io.furios.Telephony.Messages.desktop"

DAEMON_APP_ID = "io.furios.Telephony.Daemon"
DAEMON_BUS_NAME = "io.furios.Telephony.Daemon"
DAEMON_OBJECT_PATH = "/io/furios/Telephony/Daemon"
DAEMON_INTERFACE = "io.furios.Telephony.Daemon"

NOTIFY_DBUS_NAME = "org.freedesktop.Notifications"
NOTIFY_DBUS_PATH = "/org/freedesktop/Notifications"
NOTIFY_INTERFACE = "org.freedesktop.Notifications"

CALL_VOLUME_MIN_PERCENT = 10
CALL_VOLUME_MAX_PERCENT = 100
CALL_VOLUME_DEFAULT_PERCENT = 80

MMS_SIZE_LIMIT_DEFAULT_KB = "600"

VIEWFINDER_START_DELAY_MS = 200
PLAYBACK_PROGRESS_INTERVAL_MS = 500
EOS_TIMEOUT_MS = 3000
PROGRESS_BAR_WIDTH = 200

ALERT_SHEET_SPACING = 12
ALERT_SHEET_MARGIN = 18
SETTINGS_SHEET_HEIGHT = 750
VIEWFINDER_SINK_WIDTH = 540
SCROLL_SETTLE_MS = 120
SHEET_CONTENT_WIDTH = 360
CAPTURE_SHEET_HEIGHT = 800
CONTACT_SHEET_HEIGHT = 650

BLOCKLIST_SETTLE_MS = 400
