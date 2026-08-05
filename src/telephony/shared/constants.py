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

SHEET_CONTENT_WIDTH = 360
CAPTURE_SHEET_HEIGHT = 800

DEFAULT_MAX_ATTACHMENT_SIZE = 600 * 1024

DAEMON_WAIT_TRIES = 20
DAEMON_WAIT_STEP_SECONDS = 0.5
