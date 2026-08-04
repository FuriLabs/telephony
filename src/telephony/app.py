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

import argparse
import sys
import os
import datetime
from collections import defaultdict
from urllib.parse import unquote

from telephony.backend.utils.thread_utils import run_in_background

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
gi.require_version('EDataServer', '1.2')
gi.require_version('EBookContacts', '1.2')
gi.require_version('EBook', '1.2')

from gi.repository import Gtk, Adw, Gio, Gdk, GLib, Gst
from loguru import logger
from .backend.services.dbus_service import TelephonyDaemonDBus
from .backend.services.system_state_service import SystemStateService
from .backend.managers.modem_recovery_manager import execute_modem_recovery, watch_recovery_result
from .constants import APP_ID, INCALL_APP_ID, EMERGENCY_APP_ID, INCALL_DESKTOP_FILE, DAEMON_APP_ID, DAEMON_BUS_NAME
from .backend.managers.database_manager import DatabaseManager
from .backend.managers.gsettings_manager import GSettingsManager
from .backend.managers.ofono_manager import OfonoManager
from .backend.managers.mms_manager import MmsManager
from .ui.main_window import MainWindow
from .backend.managers.eds_manager import EdsManager
from .backend.utils.phone_utils import normalize_number, get_own_number
from .backend.utils.locale_utils import init_locale
from .backend.managers.emergency_manager import EmergencyManager
from .ui.windows.incall_window import InCallWindow
from .backend.managers.ringback_manager import RingbackManager
from .backend.managers.notification_manager import NotificationManager
from .backend.services.daemon_client import DaemonClient
from .backend.managers.schedule_manager import ScheduleManager

from gettext import gettext as _, ngettext

MODEM_UNAVAILABLE_DELAY_SECONDS = 60
BOOT_UNAVAILABLE_DELAY_SECONDS = 5
STARTUP_MODEM_CHECK_SECONDS = 15
NETWORK_NUDGE_DELAY_SECONDS = 300
DENIED_NOTIFY_DELAY_SECONDS = 120
DAEMON_START_TIMEOUT_MS = 25000
DBUS_START_REPLY_SUCCESS = 1
DBUS_START_REPLY_ALREADY_RUNNING = 2


class App(Adw.Application):
    """
    Main Application class handling startup, lifecycle, and core managers.
    """

    def __init__(self, application_id=APP_ID):
        """Initialize the Application."""
        super().__init__(application_id=application_id,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

        self.daemon_missing = False
        self.is_daemon = self._resolve_daemon_role(application_id)
        self.owns_incall_ui = application_id == INCALL_APP_ID
        self.recovery_state = (False, "", False)

        self.db = None
        self.ofono = None
        self.eds = None
        self.mms = None
        self.emergency = None
        self.incall = None
        self.ringback = None
        self.scheduler = None
        self._replaying_change = False

        self.notification_counts = defaultdict(int)

    def _resolve_daemon_role(self, application_id):
        """Decide whether this process owns the background work.

        Every launcher has its own application id so the shell can tell
        the windows apart, which means one telephony process per icon.
        Exactly one of them may hold the modem, store what arrives and
        run scheduled work: the instance carrying the plain id, which is
        what the service starts. When no owner answers, this asks the bus
        to start the service rather than stepping in, because a window
        that takes the role keeps the plain name unclaimed and the other
        windows would each take it too and send their calls nowhere.
        A window never takes the role, not even when the service refuses
        to start: two owners file every arriving message twice, which is
        worse than a window that says the service is down and offers to
        start it again.
        """
        if application_id == DAEMON_APP_ID:
            return True

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[App] No session bus, the daemon cannot be reached: {e}")
            self.daemon_missing = True
            return False

        if self._daemon_name_owned(bus):
            return False

        self.daemon_missing = not self._start_daemon_service(bus)
        if self.daemon_missing:
            logger.error("[App] The telephony service could not be started")
        return False

    def retry_daemon_start(self, on_done):
        """Ask the bus for the service again; on_done hears whether it came.

        The window stays usable while this runs, because the reply can
        take as long as a cold service start.
        """
        def task():
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            started = self._daemon_name_owned(bus) or self._start_daemon_service(bus)
            self.daemon_missing = not started
            return started

        run_in_background(task, on_complete=on_done)

    def _daemon_name_owned(self, bus):
        """Return True when a process already answers for the plain name."""
        try:
            res = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)", (DAEMON_BUS_NAME,)),
                GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, -1, None)
            return res.unpack()[0]
        except Exception as e:
            logger.warning(f"[App] Could not check for a running daemon: {e}")
            return False

    def _start_daemon_service(self, bus):
        """Have the bus start the telephony service; blocking, bounded wait.

        The reply arrives once the service owns the name, so a window
        that gets it can proxy its first action straight away.
        """
        logger.info("[App] No telephony daemon found, asking the bus to start the service")
        try:
            res = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "StartServiceByName", GLib.Variant("(su)", (DAEMON_BUS_NAME, 0)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE,
                DAEMON_START_TIMEOUT_MS, None)
            started = res.unpack()[0] in (DBUS_START_REPLY_SUCCESS, DBUS_START_REPLY_ALREADY_RUNNING)
        except Exception as e:
            logger.warning(f"[App] Could not start the telephony service: {e}")
            return False

        if started:
            logger.info("[App] Telephony service started, staying a window")
        return started

    def _announce_changes(self):
        """Tell window instances when the stored data changed.

        They read the same database and address books but subscribe to
        nothing on the modem, so the owner is the one that knows when
        a list is worth rebuilding.
        """
        self.db.connect('messages-updated', lambda _db, number, reason: self.dbus_daemon.emit_signal(
            "MessagesChanged", GLib.Variant("(ss)", (number or "", reason or ""))))
        self.db.connect('blocklist-updated', lambda *_args: self.dbus_daemon.emit_signal(
            "BlocklistChanged", None))
        self.eds.connect('contacts-loaded', lambda *_args: self.dbus_daemon.emit_signal(
            "ContactsChanged", None))

    def _follow_daemon_changes(self):
        """Rebuild lists when the owner reports a change.

        The change arrives over the bus and is repeated on the local
        managers, so every view keeps listening to what it always did.
        """
        self.daemon_client = DaemonClient()
        self.daemon_client.subscribe(
            "MessagesChanged",
            lambda *args: GLib.idle_add(
                self._replay_change, 'messages-updated', args[5].unpack()[0], args[5].unpack()[1]))
        self.daemon_client.subscribe(
            "BlocklistChanged",
            lambda *args: GLib.idle_add(self._replay_change, 'blocklist-updated'))
        self.daemon_client.subscribe(
            "ContactsChanged",
            lambda *args: run_in_background(self.eds.reload_cache_from_db))

        if self.owns_incall_ui:
            self.daemon_client.subscribe(
                "RecoveryStateChanged",
                lambda *args: GLib.idle_add(self._apply_recovery_state, *args[5].unpack()))
            self._seed_recovery_state()

        self.db.connect('messages-updated', lambda _db, number, reason: self._report_change(
            "messages", number or "", reason or ""))
        self.db.connect('blocklist-updated', lambda *_args: self._report_change("blocklist", "", ""))

    def _seed_recovery_state(self):
        """Ask the owner what the modem is doing right now.

        This window can start after the state changed, and a signal
        that already went out would leave its recovery page blank.
        """
        def task():
            return self.daemon_client.call(
                "GetRecoveryState", None, GLib.VariantType("(bsb)"))

        def done(state):
            if state:
                self._apply_recovery_state(*state)

        run_in_background(task, on_complete=done)

    def _replay_change(self, name, *args):
        """Repeat on the local managers what the owner reported.

        The flag marks the emission as second-hand: a window must not
        report back a change it was only told about, or the report
        would bounce between the owner and the windows forever.
        """
        self._replaying_change = True
        try:
            self.db.emit(name, *args)
        finally:
            self._replaying_change = False
        return False

    def _report_change(self, kind, number, reason):
        """Tell the owner about a write this window made.

        Nothing watches the database file, so a thread marked read here
        stays unread in the other windows until the owner repeats it.
        """
        if self._replaying_change or not self.daemon_client:
            return
        self.daemon_client.call_async(
            "NotifyChanged", GLib.Variant("(sss)", (kind, number, reason)))

    def _setup_feedbackd(self):
        """Setup feedbackd application profiles."""
        def task():
            source = Gio.SettingsSchemaSource.get_default()
            if not source:
                return

            try:
                schema = source.lookup("org.sigxcpu.feedbackd", True)
                if not schema or not schema.has_key('allow-important'):
                    logger.debug("org.sigxcpu.feedbackd schema or allow-important key missing")
                else:
                    settings = Gio.Settings(schema_id="org.sigxcpu.feedbackd")
                    allowed = settings.get_strv('allow-important')

                    apps_to_add = [INCALL_APP_ID, EMERGENCY_APP_ID]
                    new_allowed = list(set(allowed + apps_to_add))

                    if len(new_allowed) != len(allowed):
                        settings.set_strv('allow-important', new_allowed)
                        logger.info("Added apps to feedbackd allow-important list.")
            except Exception as e:
                logger.warning(f"Failed to setup feedbackd allow-important: {e}")

            try:
                schema = source.lookup("org.sigxcpu.feedbackd.application", True)
                if not schema or not schema.has_key("profile"):
                    logger.debug("org.sigxcpu.feedbackd.application schema or profile key missing")
                else:
                    emerg_settings = Gio.Settings(schema_id="org.sigxcpu.feedbackd.application", path="/org/sigxcpu/feedbackd/application/io-furios-telephony-Emergency/")
                    emerg_settings.set_string("profile", "silent")

                    incall_settings = Gio.Settings(schema_id="org.sigxcpu.feedbackd.application", path="/org/sigxcpu/feedbackd/application/io-furios-telephony-Incall/")
                    incall_settings.set_string("profile", "silent")

                    logger.info("Set feedbackd silent profiles for emergency and incall using Gio.Settings.")
            except Exception as e:
                logger.warning(f"Failed to set feedbackd silent profiles using Gio.Settings: {e}")

        run_in_background(task)

    def do_startup(self):
        """Perform application startup tasks."""
        Gtk.Application.do_startup(self)
        GLib.set_prgname(self.get_application_id())

        self._setup_feedbackd()

        if not Gst.is_initialized():
            Gst.init(None)

        if self.is_daemon:
            self.hold()

        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        self._setup_css()
        self._setup_icon_paths()

        init_locale()

        logger.info("Initializing services...")
        self.notification_manager = NotificationManager()
        self.gsettings_mgr = GSettingsManager()
        self.eds = EdsManager(owns_live_views=self.is_daemon)
        self.db = DatabaseManager(self.eds, self.gsettings_mgr)
        self.eds.set_db(self.db, self.gsettings_mgr)
        self.ofono = OfonoManager(self.db, self.gsettings_mgr, owns_reception=self.is_daemon)

        self.emergency = None
        self.ringback = None
        if self.is_daemon:
            self.emergency = EmergencyManager(self.ofono, self.db, self.gsettings_mgr, self.notification_manager)
            self.ringback = RingbackManager(self.ofono, self.gsettings_mgr)

        self.incall = None

        if self.is_daemon:
            self.ofono.connect('call-added', self._on_global_call_added)

        self.ofono.set_focus_provider(self._any_window_active)

        self.mms = MmsManager(self.db, self.eds, self.gsettings_mgr, self.notification_manager,
                              owns_reception=self.is_daemon)
        self.mms.active_chat_provider = lambda: (self.ofono.active_chat_number, self._any_window_active())
        if self.is_daemon:
            self.mms.connect('message-received', self.on_mms_received)

        self.dbus_daemon = None
        self.daemon_client = None
        if self.is_daemon:
            self.dbus_daemon = TelephonyDaemonDBus(self, self.db, self.ofono, self.eds)
            self._announce_changes()
        else:
            self._follow_daemon_changes()

        self._voicemail_last = (False, 0)
        self._vm_contact_busy = False
        self._modem_watch_timer = None
        self._modem_notified = False
        self._auto_recovery_running = False
        self._modem_was_healthy = False
        self._recovery_pending_unlock = False
        self.sys_state = SystemStateService()
        self.sys_state.connect('lock-state-changed', self._on_lock_state_changed)
        self._net_nudge_timer = None
        self._denied_timer = None
        self._sim_pin_notified = False
        self._denied_notified = False
        if self.is_daemon:
            self.ofono.connect('dial-availability-changed', self._watch_modem_health)
            self.ofono.connect('connection-status', self._watch_modem_health)
        self.ofono.connect('network-status-changed', self._watch_network_status)
        self.ofono.connect('sim-pin-required-changed', self._watch_sim_pin)
        if self.is_daemon:
            GLib.timeout_add_seconds(STARTUP_MODEM_CHECK_SECONDS, lambda: self._watch_modem_health() or False)
        if self.is_daemon:
            self.ofono.connect('voicemail-changed', self.on_voicemail_changed)
        self.ofono.connect('voicemail-mailbox-changed', lambda *a: self.ensure_voicemail_contact())
        self.eds.connect('contacts-loaded', lambda *a: self.ensure_voicemail_contact())
        if self.is_daemon:
            self.ofono.connect('incoming-message', self.on_incoming_message)
            self.ofono.connect('call-missed', self.on_call_missed)
        self.ofono.connect('notification-cleared', self.on_notification_cleared)

        self.scheduler = None
        if self.is_daemon:
            self.scheduler = ScheduleManager(self.db, self.ofono, self.mms)
            self.scheduler.start()

        run_in_background(self.db.fail_stale_sending)

        action_open = Gio.SimpleAction.new("open-chat", GLib.VariantType.new("s"))
        action_open.connect("activate", self.on_action_open_chat)
        self.add_action(action_open)

        action_recovery = Gio.SimpleAction.new("modem-recovery", None)
        action_recovery.connect("activate", lambda a, p: self.open_modem_recovery())
        self.add_action(action_recovery)

        action_dial = Gio.SimpleAction.new("dial-number", GLib.VariantType.new("s"))
        action_dial.connect("activate", self.on_action_dial)
        self.add_action(action_dial)

    def _ensure_incall_window(self):
        """Ensure the InCallWindow is initialized."""
        if self.incall is None:
            logger.info("Initializing InCallWindow (Lazy Load)")
            self.incall = InCallWindow(self.gsettings_mgr, self.ofono, self.eds, self.db)

            self.incall.connect("close-request", self._on_incall_closed)

    def _on_incall_closed(self, window):
        """Refuse the close during a call, drop the window otherwise.

        A closed window during a call would leave the call running with
        no way back to it, since the call launcher carries no icon. With
        no call left there is nothing to return to, and holding the
        window keeps its widgets and render buffers for the rest of the
        session to save a rebuild that only costs a moment.
        """
        if self.ofono and self.ofono.active_calls:
            logger.info("InCallWindow close refused, a call is running")
            return True

        logger.info("InCallWindow closed, dropping it")
        self.incall = None
        return False

    def _any_window_active(self):
        """Return True when any application window currently has focus."""
        return any(win.is_active() for win in self.get_windows())

    def release_keyboard_focus(self):
        """Drop entry focus in all windows and order the on-screen keyboard away."""
        for win in self.get_windows():
            win.set_focus(None)
        self._hide_osk()

    def _hide_osk(self):
        """Hide the Phosh on-screen keyboard via its D-Bus interface."""
        try:
            bus = self.get_dbus_connection() or Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call(
                "sm.puri.OSK0", "/sm/puri/OSK0", "sm.puri.OSK0", "SetVisible",
                GLib.Variant("(b)", (False,)),
                None, Gio.DBusCallFlags.NONE, -1, None, None)
        except Exception as e:
            logger.debug(f"OSK hide request failed: {e}")

    def _present_incall_window(self):
        """Present the in-call window after the on-screen keyboard has dismissed."""
        if self.incall:
            self.incall.defer_present = False
            self.incall.present()
            self.incall.update_state()
        return False

    def _on_global_call_added(self, _manager, path, _props):
        """Global handler for new calls to ensure InCallWindow is presented."""
        self.show_incall_ui()

    def show_incall_ui(self):
        """Bring up the call window, wherever it lives.

        The window runs as its own application so the shell can tell it
        apart from the other launchers, which means the owner starts it
        rather than drawing it. Launching an instance that already runs
        reaches the running one, so a second call is not a second
        window.
        """
        if self.owns_incall_ui:
            self._ensure_incall_window()
            self.incall.defer_present = True
            self.release_keyboard_focus()
            GLib.idle_add(self._present_incall_window)
            return

        app_info = Gio.DesktopAppInfo.new(INCALL_DESKTOP_FILE)
        if not app_info:
            logger.error(f"[App] {INCALL_DESKTOP_FILE} is missing, no call window to show")
            return

        try:
            app_info.launch(None, None)
        except Exception as e:
            logger.error(f"[App] Could not start the call window: {e}")

    def _setup_icon_paths(self):
        """
        Ensure icon lookup works under the daemon's startup conditions.
        Re-registers libadwaita's internal resource icons, whose registration
        can be missed when the process starts at session bring-up, and adds
        the system icon directory for minimal service environments.
        """
        try:
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            theme.add_resource_path("/org/gnome/Adwaita/icons")
            theme.add_search_path("/usr/share/icons")
        except Exception as e:
            logger.warning(f"Icon path setup failed: {e}")

    def _setup_css(self):
        """Load and apply custom CSS."""
        provider = Gtk.CssProvider()
        css_file = Gio.File.new_for_path(os.path.join(os.path.dirname(__file__), 'style.css'))
        provider.load_from_file(css_file)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        """Activate application."""
        Adw.Application.do_activate(self)

        logger.info("Application activated via DBus.")

        for existing_win in self.get_windows():
            if not isinstance(existing_win, MainWindow):
                continue
            if existing_win.show_calls_mode and existing_win.show_messages_mode and existing_win.show_contacts_mode:
                logger.info("Reusing existing full window upon DBus activation.")
                existing_win.set_visible(True)
                existing_win.present()
                return

        logger.info("Opening GUI Window (Full Mode) upon DBus activation...")
        win = MainWindow(
            self,
            self.ofono,
            self.db,
            self.eds,
            self.mms,
            self.gsettings_mgr,
            show_calls=True,
            show_messages=True,
            show_contacts=True
        )
        win.present()
        return

    def _parse_command_line(self, argv):
        """Parse command line arguments delivered to the primary instance."""
        parser = argparse.ArgumentParser(prog="telephony", add_help=False)
        parser.add_argument("--start-monitoring", action="store_true", help="Start in monitoring mode")
        parser.add_argument("--calls", action="store_true", help="Calls mode")
        parser.add_argument("--messages", action="store_true", help="Messages mode")
        parser.add_argument("--contacts", action="store_true", help="Contacts mode")
        parser.add_argument("--full", action="store_true", help="Full mode")
        parser.add_argument("--open-chat", metavar="NUMBER", help="Open chat for number")
        parser.add_argument("--debug", action="store_true", help="Debug mode")
        parser.add_argument("--incall", action="store_true", help="Incall mode")
        parser.add_argument("uris", nargs="*", help="tel:/sms: URIs to open")

        opts, unknown = parser.parse_known_args(argv)
        if unknown:
            logger.warning(f"Ignoring unknown command line arguments: {unknown}")
        return opts

    def do_command_line(self, command_line):
        """Handle command line arguments."""
        try:
            opts = self._parse_command_line(command_line.get_arguments()[1:])
        except (argparse.ArgumentError, SystemExit) as e:
            logger.warning(f"Failed to parse command line: {e}")
            return 1

        if opts.debug:
            logger.remove()
            logger.add(sys.stderr, level="DEBUG")

        if opts.start_monitoring or (opts.debug and not opts.uris):
            logger.info("Started in Monitoring/Debug Mode. Skipping background window creation.")
            return 0

        if opts.incall:
            logger.info("Opening InCallWindow requested via command line.")
            self._ensure_incall_window()
            self.release_keyboard_focus()
            GLib.idle_add(self._present_incall_window)
            return 0

        focus_tab = None
        open_chat_number = None
        dialpad_number = None

        if opts.calls:
            focus_tab = "dialpad"
        if opts.messages:
            focus_tab = "messages"
        if opts.contacts:
            focus_tab = "contacts"
        if opts.open_chat:
            open_chat_number = opts.open_chat
            focus_tab = "messages"

        for arg in opts.uris:
            if arg.startswith(("tel:", "callto:")):
                dialpad_number = unquote(arg.split(":", 1)[1])
                focus_tab = "dialpad"
            elif arg.startswith(("sms:", "smsto:", "mms:", "mmsto:")):
                rest = arg.split(":", 1)[1]
                if "?" in rest:
                    rest = rest.split("?", 1)[0]
                open_chat_number = unquote(rest)
                focus_tab = "messages"

        mode_calls = True
        mode_messages = True
        mode_contacts = True

        if opts.calls:
            mode_messages = False
        elif opts.contacts:
            mode_calls = False
            mode_messages = False
        elif opts.messages:
            mode_calls = False
            mode_contacts = False

        target_win = None
        for existing_win in self.get_windows():
            if isinstance(existing_win, MainWindow):
                if (existing_win.show_calls_mode == mode_calls and
                    existing_win.show_messages_mode == mode_messages and
                        existing_win.show_contacts_mode == mode_contacts):
                    target_win = existing_win
                    break

        if target_win is None and (dialpad_number or open_chat_number):
            for existing_win in self.get_windows():
                if isinstance(existing_win, MainWindow):
                    if dialpad_number and existing_win.show_calls_mode:
                        target_win = existing_win
                        break
                    if open_chat_number and existing_win.show_messages_mode:
                        target_win = existing_win
                        break

        if target_win:
            logger.info("Reusing existing matching main window.")
            target_win.set_visible(True)
            target_win.present()

            if open_chat_number:
                logger.info(f"Auto-opening chat for: {open_chat_number}")
                target_win.open_chat_for_number(open_chat_number)
            elif dialpad_number:
                logger.info(f"Auto-opening dialpad for: {dialpad_number}")
                target_win.open_dialpad_with_number(dialpad_number)
            elif focus_tab and target_win.stack and target_win.stack.get_child_by_name(focus_tab):
                target_win.stack.set_visible_child_name(focus_tab)

            return 0

        logger.info(f"Opening GUI Window (Calls: {mode_calls}, Messages: {mode_messages}, Contacts: {mode_contacts})...")
        win = MainWindow(
            self,
            self.ofono,
            self.db,
            self.eds,
            self.mms,
            self.gsettings_mgr,
            show_calls=mode_calls,
            show_messages=mode_messages,
            show_contacts=mode_contacts
        )
        win.present()

        if open_chat_number:
            logger.info(f"Auto-opening chat for: {open_chat_number}")
            win.open_chat_for_number(open_chat_number)
        elif dialpad_number:
            logger.info(f"Auto-opening dialpad for: {dialpad_number}")
            win.open_dialpad_with_number(dialpad_number)
        elif focus_tab and win.stack and win.stack.get_child_by_name(focus_tab):
            win.stack.set_visible_child_name(focus_tab)

        return 0

    def on_window_destroyed(self, win):
        """Handle window destruction."""
        logger.info(f"Window destroyed. Remaining: {len(self.get_windows())}")
        if not self.get_windows():
            if self.ofono:
                self.ofono.set_active_chat(None)
            if not self.is_daemon:
                logger.info("[App] Last window closed, leaving the daemon to it")
                GLib.idle_add(self.quit)
        return False

    def on_incoming_message(self, _ofono_obj, number, body):
        """Handle incoming SMS."""
        is_chat_open = False

        priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts()
        try:
            norm_sender = normalize_number(number)
            for p in priority_list:
                p_num = normalize_number(p.get("number", ""))
                if p_num and p_num == norm_sender:
                    logger.info(f"[Priority] SMS from {number} - forcing MAX volume")
                    self.notification_manager.audio.force_max_feedback()
                    GLib.timeout_add_seconds(1, lambda: self.notification_manager.audio.force_max_feedback() or False)
                    GLib.timeout_add_seconds(5, lambda: self.notification_manager.audio.force_max_feedback(restore=True) or False)
                    break
        except Exception as e:
            logger.debug(f"Exception checking DND bypass: {e}")

        for win in self.get_windows():
            if isinstance(win, MainWindow):
                if win.handle_new_message(number, body):
                    is_chat_open = True

        if not is_chat_open:
            self.broadcast_notification(number, body)

    def on_mms_received(self, _mms_obj, sender, recipients, _date, body, attachments, sender_name):
        """Handle incoming MMS."""
        logger.info(f"MMS Received from {sender}. Recipients: {recipients}")

        if self.db.is_blocked(sender):
            logger.info(f"Blocked MMS from {sender}")
            return

        own_number = get_own_number()

        if not own_number:
            own_number = self.gsettings_mgr.get_setting("own_number")
            if own_number:
                own_number = normalize_number(own_number)

        valid_recipients = [r for r in recipients if r and r.strip()]

        participants = set(valid_recipients)
        participants.add(sender)

        if own_number:
            norm_own = normalize_number(own_number)
            if own_number in participants:
                participants.remove(own_number)
            if norm_own in participants:
                participants.remove(norm_own)

        clean_list = sorted([n for n in [normalize_number(p) for p in participants] if n])

        if len(clean_list) > 1:
            chat_id = ",".join(clean_list)
        else:
            chat_id = sender

        GLib.timeout_add(150, self._process_mms_notification, chat_id, body, attachments, sender)

    def _process_mms_notification(self, chat_id, body, attachments, real_sender=None):
        """Process MMS notification logic."""
        preview_text = body if body else "[Picture Message]"
        is_chat_open = False

        if real_sender:
            priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts()
            try:
                norm_sender = normalize_number(real_sender)
                for p in priority_list:
                    p_num = normalize_number(p.get("number", ""))
                    if p_num and p_num == norm_sender:
                        logger.info(f"[Priority] MMS from {real_sender} - forcing MAX volume")
                        self.notification_manager.audio.force_max_feedback()
                        GLib.timeout_add_seconds(1, lambda: self.notification_manager.audio.force_max_feedback() or False)
                        GLib.timeout_add_seconds(5, lambda: self.notification_manager.audio.force_max_feedback(restore=True) or False)
                        break
            except Exception as e:
                logger.error(f"[Priority] Check failed in MMS: {e}")

        for win in self.get_windows():
            if isinstance(win, MainWindow):
                if win.handle_new_message(chat_id, preview_text, attachments, real_sender):
                    is_chat_open = True

        if not is_chat_open:
            sender_to_show = real_sender if real_sender else chat_id
            self.broadcast_notification(chat_id, preview_text, lookup_number=sender_to_show)

        return False

    def _get_custom_sms_tone(self, number):
        """Check for a custom SMS tone for the given number."""
        tones = self.gsettings_mgr.get_notification_override_sms_custom_tone_contacts()
        try:
            norm = normalize_number(number)
            for t in tones:
                if normalize_number(t.get("number", "")) == norm:
                    path = t.get("path")
                    if path and os.path.exists(path):
                        logger.debug(f"[App] Found custom SMS tone for {number}: {path}")
                        return path
                    else:
                        logger.warning(f"[App] Custom tone defined for {number} but file missing: {path}")
        except Exception as e:
            logger.error(f"Get custom SMS tone error: {e}")
        return None

    def _watch_modem_health(self, *args):
        """Arm or clear the modem-unavailable watchdog from modem state.

        A modem that was healthy earlier gets a grace period since firmware
        asserts usually recover on their own; a modem that never appeared
        after boot gets none, there is nothing transient about it.
        """
        missing = self.ofono.modem_health_degraded()

        if missing:
            if self._modem_watch_timer is None and not self._modem_notified:
                delay = MODEM_UNAVAILABLE_DELAY_SECONDS if self._modem_was_healthy else BOOT_UNAVAILABLE_DELAY_SECONDS
                self._modem_watch_timer = GLib.timeout_add_seconds(
                    delay, self._on_modem_unavailable)
            return

        self._modem_was_healthy = True
        if self._modem_watch_timer is not None:
            GLib.source_remove(self._modem_watch_timer)
            self._modem_watch_timer = None
        if self._modem_notified:
            self._modem_notified = False
            self.notification_manager.close_notification("modem_unavailable")
            self._dismiss_recovery_surface()

    def _on_modem_unavailable(self):
        """Act on the dead modem: silent recovery first, the screen otherwise."""
        self._modem_watch_timer = None
        if not self.ofono.modem_health_degraded():
            return False

        if self.gsettings_mgr.get_setting("automatic_modem_recovery") == "true":
            self.request_auto_recovery()
        else:
            self._surface_modem_recovery()
        return False

    def _describe_modem_problem(self):
        """One plain sentence about what exactly is broken."""
        if not self.ofono.monitor.connected:
            return _("The modem is not responding.")
        if self.ofono.voice_interface_missing():
            return _("The modem lost its calling service.")
        return _("The modem is not working correctly.")

    def _surface_modem_recovery(self, failed=False):
        """Show the recovery screen, or just a bare notification while locked."""
        self._modem_notified = True
        self._publish_recovery_state(True, self._describe_modem_problem(), failed)

        if self.sys_state.is_locked:
            self._recovery_pending_unlock = True
            self.notification_manager.send_notification(
                id_key="modem_unavailable",
                title=_("Modem Recovery"),
                body=_("Please unlock to see details."),
                app_id_hint="io.furios.Telephony.Calls",
                actions={"default": "app.modem-recovery", "app.modem-recovery": _("Open")},
                priority=2
            )
        else:
            self._present_recovery_surface()

    def _present_recovery_surface(self):
        """Bring up the in-call window on its recovery page."""
        self._recovery_pending_unlock = False
        self.show_incall_ui()

    def _dismiss_recovery_surface(self):
        """Take the recovery page down once the modem works again."""
        self._recovery_pending_unlock = False
        self._publish_recovery_state(False, "", False)

    def _publish_recovery_state(self, active, message, failed):
        """Report the recovery state to whoever draws the call window.

        The modem is watched here but the recovery page belongs to the
        call window, which is moving to a process of its own, so this
        reports the state instead of reaching into the window.
        """
        self.recovery_state = (active, message, failed)
        if self.dbus_daemon:
            self.dbus_daemon.emit_signal(
                "RecoveryStateChanged", GLib.Variant("(bsb)", (active, message, failed)))
        if self.owns_incall_ui:
            self._apply_recovery_state(active, message, failed)
            return
        if active:
            self.show_incall_ui()

    def _apply_recovery_state(self, active, message, failed):
        """Put the call window on its recovery page, or take it off."""
        if not active:
            if self.incall:
                self.incall.exit_recovery_mode()
            return

        self._ensure_incall_window()
        self.incall.enter_recovery_mode(message, failed=failed)

    def _on_lock_state_changed(self, _service, is_locked):
        """Show the pending recovery screen once the user unlocks."""
        if is_locked or not self._recovery_pending_unlock:
            return
        self.notification_manager.close_notification("modem_unavailable")
        self._present_recovery_surface()

    def _nudges_enabled(self):
        """Service nudges follow the automatic recovery preference."""
        return self.gsettings_mgr.get_setting("automatic_modem_recovery") == "true"

    def _cancel_timer(self, attr):
        """Cancel a named GLib timer attribute when armed."""
        timer_id = getattr(self, attr)
        if timer_id is not None:
            GLib.source_remove(timer_id)
            setattr(self, attr, None)

    def _watch_network_status(self, _ofono, status):
        """Nudge a stalled registration, surface a persistent denial."""
        if status in ("registered", "roaming") or status == "":
            self._cancel_timer("_net_nudge_timer")
            self._cancel_timer("_denied_timer")
            if self._denied_notified:
                self._denied_notified = False
                self.notification_manager.close_notification("network_denied")
            return

        if status == "denied" and self._denied_timer is None and not self._denied_notified:
            self._denied_timer = GLib.timeout_add_seconds(
                DENIED_NOTIFY_DELAY_SECONDS, self._on_denied_persisted)

        if self._net_nudge_timer is None and self._nudges_enabled():
            self._net_nudge_timer = GLib.timeout_add_seconds(
                NETWORK_NUDGE_DELAY_SECONDS, self._nudge_network)

    def _nudge_network(self):
        """Fire one registration retry and keep the timer while still bad."""
        if self.ofono.network_status in ("registered", "roaming", ""):
            self._net_nudge_timer = None
            return False
        logger.warning(f"[App] Network stuck in {self.ofono.network_status}, nudging Register()")
        run_in_background(self.ofono.register_network)
        return True

    def _on_denied_persisted(self):
        """Tell the user about a lasting registration denial."""
        self._denied_timer = None
        if self.ofono.network_status != "denied":
            return False
        self._denied_notified = True
        self.notification_manager.send_notification(
            id_key="network_denied",
            title=_("Network registration denied"),
            body=_("The network rejected this SIM. Contact your operator."),
            app_id_hint="io.furios.Telephony.Calls",
            priority=2
        )
        return False

    def _watch_sim_pin(self, _ofono, pin_type):
        """Tell the user when the SIM waits for its PIN."""
        if pin_type in ("none", ""):
            if self._sim_pin_notified:
                self._sim_pin_notified = False
                self.notification_manager.close_notification("sim_pin")
            return
        if self._sim_pin_notified:
            return
        self._sim_pin_notified = True
        self.notification_manager.send_notification(
            id_key="sim_pin",
            title=_("SIM PIN required"),
            body=_("Calls and messages stay offline until the SIM is unlocked."),
            app_id_hint="io.furios.Telephony.Calls",
            priority=2
        )

    def request_auto_recovery(self, on_done=None):
        """Restart the modem stack once, silently; False when already running."""
        if self._auto_recovery_running:
            return False
        self._auto_recovery_running = True

        def verdict(success):
            self._auto_recovery_running = False
            if success:
                logger.info("[App] Modem recovery succeeded")
                self._modem_notified = False
                self.notification_manager.close_notification("modem_unavailable")
                self._dismiss_recovery_surface()
            else:
                logger.error("[App] Modem recovery failed")
                self._surface_modem_recovery(failed=True)
            if on_done:
                on_done(success)

        def fired(_result):
            watch_recovery_result(self.ofono, verdict)

        def failed(error):
            logger.warning(f"[App] Modem recovery commands errored: {error}")
            watch_recovery_result(self.ofono, verdict)

        run_in_background(execute_modem_recovery, on_complete=fired, on_error=failed)
        return True

    def open_modem_recovery(self):
        """Show the modem recovery screen with the current status."""
        self._surface_modem_recovery()

    def ensure_voicemail_contact(self):
        """Create a Voicemail contact for the mailbox number when missing.

        Create-only by design: when the number already belongs to any
        contact under any name, that reference is left alone.
        """
        if not self.ofono or not self.eds or not self.eds.is_ready:
            return
        number = self.ofono.voicemail_number()
        if not number or self._vm_contact_busy:
            return
        self._vm_contact_busy = True

        def task():
            if self.eds.get_contact_name(number):
                return False
            vcard = f"BEGIN:VCARD\nVERSION:3.0\nFN:{_('Voicemail')}\nTEL;TYPE=VOICE:{number}\nEND:VCARD"
            return self.eds.save_contact(vcard, source_uid="system-address-book")

        def done(created):
            self._vm_contact_busy = False
            if created:
                logger.info("[App] Voicemail contact created in the Personal book")

        def failed(error):
            self._vm_contact_busy = False
            logger.warning(f"[App] Voicemail contact check failed: {error}")

        run_in_background(task, on_complete=done, on_error=failed)

    def on_voicemail_changed(self, _ofono, waiting, count):
        """Notify about newly waiting voicemail like a missed call."""
        prev_waiting, prev_count = self._voicemail_last
        self._voicemail_last = (waiting, count)
        if not waiting:
            if prev_waiting:
                self.notification_manager.close_notification("voicemail")
            return
        if prev_waiting and count <= prev_count:
            return

        if count:
            body = ngettext("{count} new message", "{count} new messages", count).format(count=count)
        else:
            body = _("New voicemail")

        actions = {}
        number = self.ofono.voicemail_number()
        if number:
            actions["default"] = f"app.dial-number('{number}')"
            actions[f"app.dial-number('{number}')"] = _("Call Back")

        self.notification_manager.send_notification(
            id_key="voicemail",
            title=_("Voicemail"),
            body=body,
            app_id_hint="io.furios.Telephony.Calls",
            actions=actions,
            priority=2
        )

    def on_call_missed(self, _ofono_obj, number):
        """Handle missed call event."""
        logger.info(f"Missed call from {number}, sending notification")
        norm_num = normalize_number(number)
        contact_name = self.eds.get_contact_name(number)

        is_unknown = False
        if contact_name and contact_name != "Unknown":
            display_name = contact_name
        elif number and number != "Unknown":
            display_name = number
        else:
            is_unknown = True
            display_name = _("Unknown")

        now_str = datetime.datetime.now().strftime("%H:%M")

        title = "{} {}".format(_("Missed Call"), now_str)
        body = f"{display_name}"

        actions = {}
        if not is_unknown:
            actions["default"] = f"app.dial-number('{number}')"
            if not any(c.isalpha() for c in str(number)):
                actions[f"app.dial-number('{number}')"] = _("Call Back")

        self.notification_manager.send_notification(
            id_key=f"missed_{norm_num}",
            title=title,
            body=body,
            app_id_hint="io.furios.Telephony.Calls",
            actions=actions,
            priority=2
        )

    def broadcast_notification(self, number, body, lookup_number=None):
        """Broadcast a message notification."""
        norm_num = normalize_number(number)

        target_for_name = lookup_number if lookup_number else number
        contact_name = self.eds.get_contact_name(target_for_name)

        if contact_name and contact_name != "Unknown":
            name = contact_name
        elif target_for_name and target_for_name != "Unknown":
            name = target_for_name
        else:
            name = _("Unknown")

        self.notification_counts[norm_num] += 1
        count = self.notification_counts[norm_num]

        title = name
        if count > 1:
            body = ngettext("{count} new message", "{count} new messages", count).format(count=count)

        actions = {
            "default": f"app.open-chat('{number}')",
            f"app.open-chat('{number}')": _("Open")
        }

        if "," not in number and not any(c.isalpha() for c in str(number)):
            actions[f"app.dial-number('{number}')"] = _("Call")

        custom_sound = self._get_custom_sms_tone(number)
        if not custom_sound and lookup_number:
            custom_sound = self._get_custom_sms_tone(lookup_number)

        if custom_sound:
            logger.debug(f"[App] Broadcasting notification with custom sound: {custom_sound}")

        self.notification_manager.send_notification(
            id_key=norm_num,
            title=title,
            body=body,
            app_id_hint="io.furios.Telephony.Messages",
            actions=actions,
            priority=2,
            sound_file=custom_sound
        )

    def on_notification_cleared(self, _ofono_obj, number):
        """Handle notification cleared event."""
        logger.debug(f"Clearing notification for {number} (Interaction detected)")
        self.clear_notification(number)

    def on_action_open_chat(self, _action, parameter):
        """Handle open-chat action."""
        number = parameter.get_string()
        self.open_messages_chat(number)
        self.clear_notification(number)

    def open_messages_chat(self, number):
        """Open a chat in a messages window without spawning a new process.

        Prefers the dedicated messages window, falls back to an open full
        window and only creates a messages window when neither exists,
        the same order do_command_line resolves --messages --open-chat.
        """
        target = None
        for win in self.get_windows():
            if isinstance(win, MainWindow) and win.show_messages_mode:
                target = win
                if not win.show_calls_mode:
                    break
        if target is None:
            logger.info("No messages window for chat, creating one")
            target = MainWindow(self, self.ofono, self.db, self.eds, self.mms,
                                self.gsettings_mgr, show_calls=False,
                                show_messages=True, show_contacts=False)
        target.set_visible(True)
        target.present()
        target.open_chat_for_number(number)

    def on_action_dial(self, _action, parameter):
        """Handle dial-number action."""
        number = parameter.get_string()

        target_win = None
        for win in self.get_windows():
            if isinstance(win, MainWindow) and win.show_calls_mode:
                target_win = win
                break

        if target_win:
            target_win.present()
            target_win.open_dialpad_with_number(number)
            self.clear_notification(number)
            return

        logger.info("No existing window found with dialpad for intent. Launching full window.")
        win = MainWindow(
            self,
            self.ofono,
            self.db,
            self.eds,
            self.mms,
            self.gsettings_mgr,
            show_calls=True,
            show_messages=True,
            show_contacts=True
        )
        win.present()
        win.open_dialpad_with_number(number)
        self.clear_notification(number)

    def clear_notification(self, number):
        """Clear active notifications for a number."""
        norm_num = normalize_number(number)
        self.notification_counts[norm_num] = 0
        self.notification_manager.close_notification(norm_num)
        self.notification_manager.close_notification(f"missed_{norm_num}")
        try:
            self.withdraw_notification(norm_num)
            self.withdraw_notification(f"missed_{norm_num}")
        except Exception as e:
            logger.warning(f"Failed to withdraw notification: {e}")
