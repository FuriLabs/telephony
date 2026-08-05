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

import sys

from gi.repository import Gio, GLib
from telephony.backend.utils.log_utils import logger

from .backend.utils.system_utils import launch_desktop_uri
from .constants import DAEMON_APP_ID, INCALL_DESKTOP_FILE, CALLS_DESKTOP_FILE, MESSAGES_DESKTOP_FILE
from .telephony_core import TelephonyCore


class DaemonApp(Gio.Application):
    """The headless face of the telephony service.

    Wraps TelephonyCore in a bare Gio.Application that owns the daemon
    bus name and holds itself alive. It implements the same ui delegate
    the windowed application does, with window-free answers: no window
    is ever active, no chat is ever open on screen, and every screen
    the user must reach is a launcher started with a scheme URI.

    ofono is mirrored as an attribute because module helpers reach the
    modem through the default application object.
    """

    def __init__(self):
        """Initialize the service application."""
        super().__init__(application_id=DAEMON_APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.core = TelephonyCore(DAEMON_APP_ID, ui=self)
        self.ofono = None

    def do_startup(self):
        """Bring up the managers and stay resident."""
        Gio.Application.do_startup(self)
        GLib.set_prgname(self.get_application_id())

        self.hold()
        self.core.start()
        self.ofono = self.core.ofono
        self.core.ofono.connect('call-added', self._on_global_call_added)

        action_open = Gio.SimpleAction.new("open-chat", GLib.VariantType.new("s"))
        action_open.connect("activate", self.on_action_open_chat)
        self.add_action(action_open)

        action_recovery = Gio.SimpleAction.new("modem-recovery", None)
        action_recovery.connect("activate", lambda a, p: self.core.open_modem_recovery())
        self.add_action(action_recovery)

        action_dial = Gio.SimpleAction.new("dial-number", GLib.VariantType.new("s"))
        action_dial.connect("activate", self.on_action_dial)
        self.add_action(action_dial)

    def on_action_open_chat(self, _action, parameter):
        """Open the conversation in the messages launcher."""
        number = parameter.get_string()
        launch_desktop_uri(MESSAGES_DESKTOP_FILE, f"sms:{number}")
        self.core.clear_notification(number)

    def on_action_dial(self, _action, parameter):
        """Put the number on the dialpad of the calls launcher."""
        number = parameter.get_string()
        launch_desktop_uri(CALLS_DESKTOP_FILE, f"tel:{number}")
        self.core.clear_notification(number)

    def do_activate(self):
        """Answer an activation without raising anything.

        The service has no window; the launchers activate their own
        application ids and never this one.
        """
        logger.info("[Daemon] Activated; the service has no window to raise")

    def do_command_line(self, command_line):
        """Accept the service arguments and refuse the window ones."""
        args = command_line.get_arguments()[1:]

        if "--debug" in args:
            logger.remove()
            logger.add(sys.stderr, level="DEBUG")

        extra = [a for a in args if a not in ("--start-monitoring", "--debug")]
        if extra:
            logger.warning(f"[Daemon] Ignoring window arguments: {extra}")
        return 0

    def any_window_active(self):
        """No window ever has focus in the service process."""
        return False

    def deliver_message_to_windows(self, chat_id, body, attachments=None, real_sender=None):
        """No chat is ever open here, so every message gets a notification."""
        return False

    def apply_recovery_state(self, active, message, failed):
        """The recovery page lives in the in-call process.

        The core only calls this on the process that owns the in-call
        window; the service publishes the state over the bus instead.
        """
        logger.debug("[Daemon] Recovery state is published, not drawn here")

    def _on_global_call_added(self, _manager, path, _props):
        """Bring up the call surface for every new call."""
        self.show_incall_ui()

    def show_incall_ui(self):
        """Launch the in-call process that draws the call surface.

        Launching an instance that already runs reaches the running
        one, so a second call is not a second window.
        """
        app_info = Gio.DesktopAppInfo.new(INCALL_DESKTOP_FILE)
        if not app_info:
            logger.error(f"[Daemon] {INCALL_DESKTOP_FILE} is missing, no call window to show")
            return

        try:
            app_info.launch(None, None)
        except Exception as e:
            logger.error(f"[Daemon] Could not start the call window: {e}")

    def withdraw_number_notifications(self, norm_num):
        """Withdraw the shell notifications filed under a number."""
        try:
            self.withdraw_notification(norm_num)
            self.withdraw_notification(f"missed_{norm_num}")
        except Exception as e:
            logger.warning(f"Failed to withdraw notification: {e}")
