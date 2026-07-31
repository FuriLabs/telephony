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
import subprocess
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

from .backend.utils.database_utils import DatabaseManager
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
from .backend.managers.schedule_manager import ScheduleManager

from gettext import gettext as _, ngettext



class App(Adw.Application):
    """
    Main Application class handling startup, lifecycle, and core managers.
    """

    def __init__(self, application_id="io.furios.Telephony"):
        """Initialize the Application."""
        super().__init__(application_id=application_id,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

        self.db = None
        self.ofono = None
        self.eds = None
        self.mms = None
        self.emergency = None
        self.incall = None
        self.ringback = None
        self.scheduler = None

        self.notification_counts = defaultdict(int)

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

                    apps_to_add = ['io.furios.Telephony.incall', 'io.furios.Telephony.emergency']
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
                    emerg_settings = Gio.Settings(schema_id="org.sigxcpu.feedbackd.application", path="/org/sigxcpu/feedbackd/application/io-furios-telephony-emergency/")
                    emerg_settings.set_string("profile", "silent")

                    incall_settings = Gio.Settings(schema_id="org.sigxcpu.feedbackd.application", path="/org/sigxcpu/feedbackd/application/io-furios-telephony-incall/")
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

        self.hold()

        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        self._setup_css()
        self._setup_icon_paths()

        init_locale()

        logger.info("Initializing services...")
        self.notification_manager = NotificationManager()
        self._voicemail_notified = False
        self._voicemail_body = None
        self.gsettings_mgr = GSettingsManager()
        self.eds = EdsManager()
        self.db = DatabaseManager(self.eds, self.gsettings_mgr)
        self.eds.set_db(self.db, self.gsettings_mgr)
        self.ofono = OfonoManager(self.db, self.gsettings_mgr)

        self.emergency = EmergencyManager(self.ofono, self.db, self.gsettings_mgr, self.notification_manager)
        self.ringback = RingbackManager(self.ofono, self.gsettings_mgr)

        self.incall = None

        self.ofono.connect('call-added', self._on_global_call_added)

        self.ofono.set_focus_provider(self._any_window_active)

        self.mms = MmsManager(self.db, self.eds, self.gsettings_mgr, self.notification_manager)
        self.mms.active_chat_provider = lambda: (self.ofono.active_chat_number, self._any_window_active())
        self.mms.connect('message-received', self.on_mms_received)

        self.dbus_daemon = TelephonyDaemonDBus(self, self.db, self.ofono, self.eds)

        self.ofono.connect('incoming-message', self.on_incoming_message)
        self.ofono.connect('call-missed', self.on_call_missed)
        self.ofono.connect('notification-cleared', self.on_notification_cleared)
        self.ofono.connect('voicemail-changed', self.on_voicemail_changed)

        self.scheduler = ScheduleManager(self.db, self.ofono, self.mms)
        self.scheduler.start()

        run_in_background(self.db.fail_stale_sending)

        action_open = Gio.SimpleAction.new("open-chat", GLib.VariantType.new("s"))
        action_open.connect("activate", self.on_action_open_chat)
        self.add_action(action_open)

        action_dial = Gio.SimpleAction.new("dial-number", GLib.VariantType.new("s"))
        action_dial.connect("activate", self.on_action_dial)
        self.add_action(action_dial)

    def _ensure_incall_window(self):
        """Ensure the InCallWindow is initialized."""
        if self.incall is None:
            logger.info("Initializing InCallWindow (Lazy Load)")
            self.incall = InCallWindow(self.gsettings_mgr, self.ofono, self.eds, self.db)

            def _on_incall_closed(_w):
                logger.info("InCallWindow hidden.")
                return True

            self.incall.connect("close-request", _on_incall_closed)

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
        self._ensure_incall_window()
        self.incall.defer_present = True
        self.release_keyboard_focus()
        GLib.idle_add(self._present_incall_window)

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
        return False

    def do_shutdown(self):
        """Perform shutdown cleanup."""
        super().do_shutdown()

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

    def on_voicemail_changed(self, _ofono_obj, waiting, count, count_known, number):
        """Announce waiting voicemail, and withdraw the notice once retrieved."""
        if not waiting:
            if self._voicemail_notified:
                self.notification_manager.close_notification("voicemail")
                self._voicemail_notified = False
                self._voicemail_body = None
            return

        # The count is usually unknown: the indication commonly carries none at
        # all, so say that something is waiting rather than invent a number.
        if count_known and count > 0:
            body = ngettext("{count} new message", "{count} new messages", count).format(count=count)
        else:
            body = _("You have new voicemail")

        # ofono delivers the mailbox number a moment after the flag itself, so
        # re-posting on every property change would alert twice for one message.
        if self._voicemail_notified and body == self._voicemail_body:
            return

        actions = {}
        if number:
            actions["default"] = f"app.dial-number('{number}')"
            actions[f"app.dial-number('{number}')"] = _("Call Voicemail")

        self.notification_manager.send_notification(
            id_key="voicemail",
            title=_("Voicemail"),
            body=body,
            app_id_hint="io.furios.Telephony.Calls",
            actions=actions,
            priority=1
        )
        self._voicemail_notified = True
        self._voicemail_body = body

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

        target_win = None
        for win in self.get_windows():
            if isinstance(win, MainWindow) and win.show_messages_mode:
                target_win = win
                break

        if target_win:
            target_win.present()
            target_win.open_chat_for_number(number)
            self.clear_notification(number)
            return

        logger.info("No suitable window for chat, launching new instance.")
        subprocess.Popen([sys.executable, "-m", "telephony.main", "--open-chat", number])

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
