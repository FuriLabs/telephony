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

import datetime
import os
import time
from collections import defaultdict

from gi.repository import Gio, GLib
from telephony.shared.utils.log_utils import logger

from telephony.daemon.services.dbus_service import TelephonyDaemonDBus
from telephony.daemon.services.callaudiod_dbus_service import CallAudiodDBusService
from telephony.daemon.services.gnome_calls_dbus_service import GnomeCallsDBusService
from telephony.shared.services.system_state_service import SystemStateService
from telephony.daemon.managers.modem_recovery_manager import (execute_modem_recovery, watch_recovery_result)
from telephony.shared.managers.database_manager import DatabaseManager
from telephony.shared.managers.gsettings_manager import GSettingsManager
from telephony.daemon.managers.ofono_manager import OfonoManager
from telephony.daemon.managers.mms_manager import MmsManager
from telephony.shared.managers.eds_manager import EdsManager
from telephony.daemon.managers.ringback_manager import RingbackManager
from telephony.daemon.managers.notification_manager import NotificationManager
from telephony.daemon.managers.call_audio_manager import CallAudioManager
from telephony.daemon.managers.schedule_manager import ScheduleManager
from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.utils.phone_utils import normalize_number, conversation_id, get_own_number
from telephony.shared.utils.region_utils import detect_region, set_custom_region
from telephony.shared.utils.system_utils import (is_ofono_on_bus, is_ril_running,
                                                  is_gsd_airplane_mode,
                                                 is_vendor_radio_disabled)
from telephony.shared.constants import INCALL_APP_ID, EMERGENCY_APP_ID

from gettext import gettext as _, ngettext

MODEM_UNAVAILABLE_DELAY_SECONDS = 5
BOOT_UNAVAILABLE_DELAY_SECONDS = 5
STARTUP_MODEM_CHECK_SECONDS = 15
VOICEMAIL_CONTACT_UID_PREFIX = "telephony-voicemail-"
SYSTEM_ADDRESS_BOOK_UID = "system-address-book"
NETWORK_NUDGE_DELAY_SECONDS = 300
DENIED_NOTIFY_DELAY_SECONDS = 120
MMS_NOTIFICATION_DELAY_MS = 150
HANGUP_FEEDBACK_SUPPRESS_SECONDS = 5


class TelephonyCore:
    """Own every manager and background duty of the telephony service.

    The daemon owns modem state, storage, reception, scheduling and its
    D-Bus services. Window processes use WindowCore instead. The ui
    delegate is limited to service-process integration that cannot live
    in this plain core object: show_incall_ui, is_any_window_active and
    withdraw_number_notifications. Recovery state is published over
    D-Bus for the in-call process rather than applied through the delegate.
    """

    def __init__(self, ui):
        """Hold the owner's state; managers are wired in start()."""
        self.ui = ui
        self.recovery_state = (False, "", False)

        self.notification_manager = None
        self.gsettings_mgr = None
        self.eds = None
        self.db = None
        self.ofono = None
        self.mms = None
        self.ringback = None
        self.scheduler = None
        self.dbus_daemon = None
        self.gnome_calls_dbus = None
        self.callaudiod_dbus = None
        self.sys_state = None
        self.call_audio = None

        self.notification_counts = defaultdict(int)
        self._hangup_requested_at = 0.0
        self._voicemail_last = (False, 0)
        self._vm_contact_busy = False
        self._vm_contact_number = None
        self._modem_watch_timer = None
        self._modem_notified = False
        self._auto_recovery_running = False
        self._modem_was_healthy = False
        self._recovery_pending_unlock = False
        self._net_nudge_timer = None
        self._denied_timer = None
        self._sim_pin_notified = False
        self._denied_notified = False

    def start(self):
        """Wire the managers and background duties of the owner."""
        self.setup_feedbackd()

        logger.info("Initializing services...")
        self.notification_manager = NotificationManager()
        self.gsettings_mgr = GSettingsManager()
        self.apply_region()
        self.eds = EdsManager(owns_live_views=True)
        self.db = DatabaseManager(self.eds, self.gsettings_mgr, owns_writes=True)
        self.eds.set_db(self.db, self.gsettings_mgr)
        self.ofono = OfonoManager(self.db, self.gsettings_mgr)

        self.ringback = RingbackManager(self.ofono, self.gsettings_mgr)

        self.ofono.set_focus_provider(self.ui.is_any_window_active)

        self.mms = MmsManager(self.db, self.eds, self.gsettings_mgr, self.notification_manager,
                              owns_reception=True)
        self.mms.active_chat_provider = lambda: (self.ofono.active_chat_number,
                                                 bool(self.ofono.active_chat_number))
        self.mms.connect('message-received', self.on_mms_received)

        self.call_audio = CallAudioManager(self.ofono, self.ofono.audio, self.gsettings_mgr)
        self.callaudiod_dbus = CallAudiodDBusService(self.ofono, self.call_audio)

        self.dbus_daemon = TelephonyDaemonDBus(self, self.db, self.ofono, self.eds)
        self.gnome_calls_dbus = GnomeCallsDBusService(self.db, self.ofono, self.gsettings_mgr, self.call_audio)
        self.announce_changes()

        self.sys_state = SystemStateService()
        self.sys_state.connect('lock-state-changed', self.on_lock_state_changed)
        self.watch_airplane_mode()
        self.ofono.connect('dial-availability-changed', self.watch_modem_health)
        self.ofono.connect('connection-status', self.watch_modem_health)
        self.ofono.connect('network-status-changed', self.watch_network_status)
        self.ofono.connect('sim-pin-required-changed', self.watch_sim_pin)
        GLib.timeout_add_seconds(STARTUP_MODEM_CHECK_SECONDS, lambda: self.watch_modem_health() or False)
        self.ofono.connect('voicemail-changed', self.on_voicemail_changed)
        self.ofono.connect('voicemail-mailbox-changed', lambda *a: self.ensure_voicemail_contact())
        self.eds.connect('contacts-loaded', lambda *a: self.ensure_voicemail_contact())
        self.ofono.connect('incoming-message', self.on_incoming_message)
        self.ofono.connect('call-missed', self.on_call_missed)
        self.ofono.connect('hangup-requested', self.on_hangup_requested)
        self.ofono.connect('call-removed', self.on_call_removed_feedback)
        self.ofono.connect('notification-cleared', self.on_notification_cleared)

        self.scheduler = ScheduleManager(self.db, self.ofono, self.mms)
        self.scheduler.start()
        run_in_background(self.db.fail_stale_sending)

    def apply_region(self):
        """Give this process the country its numbers belong to.

        Numbers are read into the cache here, and one written without a
        country code has to be given one from somewhere. Only the
        windows were ever told which country to assume, so the store
        they all read was built by the one process still guessing from
        the session locale.
        """
        def task():
            country_code = self.gsettings_mgr.get_setting("default_country_code")
            region = country_code or detect_region()
            if region:
                set_custom_region(region)
                logger.info(f"[App] Numbers without a country code are read as {region}")

        run_in_background(task)

    def announce_changes(self):
        """Tell window instances when the stored data changed.

        They read the same database and address books but subscribe to
        nothing on the modem, so the owner is the one that knows when
        a list is worth rebuilding.
        """
        self.db.connect('messages-updated', lambda _db, number, reason: self.dbus_daemon.emit_signal(
            "MessagesChanged", GLib.Variant("(ss)", (number or "", reason or ""))))
        self.db.connect('blocklist-updated', lambda *_args: self.dbus_daemon.emit_signal(
            "BlocklistChanged", None))
        self.db.connect('history-updated', lambda *_args: self.dbus_daemon.emit_signal(
            "HistoryChanged", None))
        self.eds.connect('contacts-loaded', lambda *_args: self.dbus_daemon.emit_signal(
            "ContactsChanged", None))
        self.eds.connect('address-books-changed', lambda *_args: self.dbus_daemon.emit_signal(
            "AddressBooksChanged", None))

    def on_hangup_requested(self, _manager):
        """Remember that this side asked for a hangup, whichever surface did."""
        self._hangup_requested_at = time.monotonic()

    def on_call_removed_feedback(self, _manager, _path):
        """Sound the hangup tone when the other side ended the call.

        The tone lives in the daemon because the call window's process
        quits right after the last call, and feedbackd ends a client's
        running feedbacks when it leaves the bus — a window-played tone
        gets cut off mid-note. Every local surface proxies its hangup
        through this process, so a removal shortly after any request
        here is ours and stays silent.
        """
        if time.monotonic() - self._hangup_requested_at < HANGUP_FEEDBACK_SUPPRESS_SECONDS:
            return
        self.ofono.audio.play_hangup()

    def setup_feedbackd(self):
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

    def is_chat_open(self, number):
        """Return whether a window reports this chat open and focused.

        The windows keep SetActiveChat truthful on map, unmap and focus
        change, so matching the reported target is the whole answer;
        this process has no windows to ask.
        """
        active = self.ofono.active_chat_number
        if not active:
            return False
        return number == active or normalize_number(number) == active

    def on_incoming_message(self, _ofono_obj, number, body):
        """Handle incoming SMS."""
        priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts_messages()
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

        if self.is_chat_open(number):
            return
        if self.gsettings_mgr.is_conversation_muted(conversation_id(number)):
            logger.debug(f"[App] Muted conversation, no notification for {number}")
            return
        self.broadcast_notification(number, body)

    def on_mms_received(self, _mms_obj, sender, recipients, _date, body, attachments, sender_name):
        """Handle incoming MMS."""
        logger.info(f"MMS Received from {sender}. Recipients: {recipients}")

        if self.db.is_blocked(sender, kind="messages"):
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

        GLib.timeout_add(MMS_NOTIFICATION_DELAY_MS, self.process_mms_notification, chat_id, body, attachments, sender)

    def process_mms_notification(self, chat_id, body, attachments, real_sender=None):
        """Process MMS notification logic."""
        preview_text = body if body else "[Picture Message]"

        if real_sender:
            priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts_messages()
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

        if self.is_chat_open(chat_id):
            return False
        if self.gsettings_mgr.is_conversation_muted(conversation_id(chat_id)):
            logger.debug(f"[App] Muted conversation, no notification for {chat_id}")
            return False
        sender_to_show = real_sender if real_sender else chat_id
        self.broadcast_notification(chat_id, preview_text, lookup_number=sender_to_show)

        return False

    def get_custom_sms_tone(self, number):
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

    def watch_modem_health(self, *args):
        """Arm or clear the modem-unavailable watchdog from modem state.

        Use separate delays for a modem that disappeared after working and
        one that has not appeared during this boot. The constants currently
        use the same delay, but keeping the cases separate allows either
        policy to be adjusted without changing the watchdog logic.
        """
        missing = self.ofono.is_modem_health_degraded()

        if missing:
            if self._modem_watch_timer is None and not self._modem_notified:
                delay = MODEM_UNAVAILABLE_DELAY_SECONDS if self._modem_was_healthy else BOOT_UNAVAILABLE_DELAY_SECONDS
                self._modem_watch_timer = GLib.timeout_add_seconds(
                    delay, self.on_modem_unavailable)
            return

        self._modem_was_healthy = True
        if self._modem_watch_timer is not None:
            GLib.source_remove(self._modem_watch_timer)
            self._modem_watch_timer = None
        if self._modem_notified:
            self._modem_notified = False
            self.notification_manager.close_notification("modem_unavailable")
            self.dismiss_recovery_surface()

    def on_modem_unavailable(self):
        """Act on the dead modem, unless the radio is off on purpose."""
        self._modem_watch_timer = None
        if not self.ofono.is_modem_health_degraded():
            return False

        run_in_background(self.recovery_refusal_reason, on_complete=self.decide_modem_recovery,
                          on_error=lambda error: self.decide_modem_recovery(""))
        return False

    def watch_airplane_mode(self):
        """Notice the radio being switched back on.

        A modem that is gone reports nothing, so nothing would ask
        again after the radio was found switched off, and a recovery
        that was declined would never be offered once it was wanted.
        The switch itself is what says the answer changed.
        """
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.signal_subscribe(
                "org.gnome.SettingsDaemon.Rfkill",
                "org.freedesktop.DBus.Properties", "PropertiesChanged",
                "/org/gnome/SettingsDaemon/Rfkill", None,
                Gio.DBusSignalFlags.NONE, self.on_airplane_mode_changed, None)
        except Exception as e:
            logger.debug(f"[App] Cannot watch the airplane switch: {e}")

    def on_airplane_mode_changed(self, *_args):
        """Rearm the modem watchdog when the airplane switch moves."""
        self.watch_modem_health()

    def is_radio_off_by_choice(self):
        """Return True when the radio is off because it was asked to be; blocking, call from a worker."""
        return is_gsd_airplane_mode() or is_vendor_radio_disabled()

    def recovery_refusal_reason(self):
        """Return a reason to avoid automatic recovery, or an empty string.

        A switched-off radio is intentional state, so restarting the stack
        is not useful until the radio is enabled again.

        For a modem that has never appeared during this boot, an active
        oFono bus name together with a running RIL process and no published
        modem is classified as no-modem. This is only a heuristic since it does
        not prove a provisioning problem or prove that the lower layers are
        healthy. Explicit recovery requests are therefore still allowed to
        restart the stack.
        """
        if self.is_radio_off_by_choice():
            return "radio-off"

        if self._modem_was_healthy or self.ofono.monitor.connected:
            return ""

        if is_ofono_on_bus() and is_ril_running():
            return "no-modem"

        return ""

    def recovery_refusal_text(self, blocker):
        """Put a refusal in words for whoever asked."""
        if blocker == "radio-off":
            return _("The radio is switched off. Turn it back on to use the modem.")
        return _("This device reports no modem. Restarting cannot fix a modem "
                 "that is missing or has no identity.")

    def decide_modem_recovery(self, blocker):
        """Recover the modem silently, show the screen, or leave it alone.

        Reading the radio state runs off the main loop because querying the
        vendor property may block. A deliberately switched-off radio is left
        alone until the airplane/radio state changes and rearms the watchdog.

        The no-modem blocker is a heuristic used only for a modem that has
        never appeared during this boot. It suppresses automatic recovery and
        marks the modem absent, but it is not a hard refusal, an explicit
        recovery request can still restart the stack.
        """
        if blocker == "radio-off":
            logger.info("[App] Modem is unavailable because the radio is off, not offering recovery")
            return

        if not self.ofono.is_modem_health_degraded():
            return

        if blocker:
            logger.warning("[App] No modem on a healthy stack; the banner says so, no screen")
            self._modem_notified = True
            self.ofono.set_modem_absent(True)
            return

        if self.gsettings_mgr.get_setting("automatic_modem_recovery") == "true":
            self.request_auto_recovery()
        else:
            self.surface_modem_recovery()

    def describe_modem_problem(self):
        """Say which part stopped, since they are repaired the same way but do not look alike."""
        if not self.ofono.monitor.connected:
            return _("The modem service is not responding.")
        if self.ofono.is_voice_interface_missing():
            return _("The modem is answering, but it lost its calling service.")
        if self.ofono.modem_online is False:
            return _("The modem is answering, but its radio is switched off.")
        return _("The modem is not working correctly.")

    def surface_modem_recovery(self, failed=False, message=None):
        """Show the recovery screen, or just a bare notification while locked."""
        self._modem_notified = True
        self.publish_recovery_state(True, message or self.describe_modem_problem(), failed)

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
            self.present_recovery_surface()

    def present_recovery_surface(self):
        """Bring up the in-call window on its recovery page."""
        self._recovery_pending_unlock = False
        self.ui.show_incall_ui()

    def dismiss_recovery_surface(self):
        """Take the recovery page down once the modem works again."""
        self._recovery_pending_unlock = False
        self.publish_recovery_state(False, "", False)

    def publish_recovery_state(self, active, message, failed):
        """Publish recovery state for the in-call process over D-Bus.

        The daemon owns modem recovery state, while the recovery page is
        rendered by the separate in-call process. Window presentation is
        handled separately by present_recovery_surface().
        """
        self.recovery_state = (active, message, failed)
        if self.dbus_daemon:
            self.dbus_daemon.emit_signal(
                "RecoveryStateChanged", GLib.Variant("(bsb)", (active, message, failed)))

    def on_lock_state_changed(self, _service, is_locked):
        """Show the pending recovery screen once the user unlocks."""
        if is_locked or not self._recovery_pending_unlock:
            return
        self.notification_manager.close_notification("modem_unavailable")
        self.present_recovery_surface()

    def are_nudges_enabled(self):
        """Service nudges follow the automatic recovery preference."""
        return self.gsettings_mgr.get_setting("automatic_modem_recovery") == "true"

    def cancel_timer(self, attr):
        """Cancel a named GLib timer attribute when armed."""
        timer_id = getattr(self, attr)
        if timer_id is not None:
            GLib.source_remove(timer_id)
            setattr(self, attr, None)

    def watch_network_status(self, _ofono, status):
        """Nudge a stalled registration, surface a persistent denial."""
        if status in ("registered", "roaming") or status == "":
            self.cancel_timer("_net_nudge_timer")
            self.cancel_timer("_denied_timer")
            if self._denied_notified:
                self._denied_notified = False
                self.notification_manager.close_notification("network_denied")
            return

        if status == "denied" and self._denied_timer is None and not self._denied_notified:
            self._denied_timer = GLib.timeout_add_seconds(
                DENIED_NOTIFY_DELAY_SECONDS, self.on_denied_persisted)

        if self._net_nudge_timer is None and self.are_nudges_enabled():
            self._net_nudge_timer = GLib.timeout_add_seconds(
                NETWORK_NUDGE_DELAY_SECONDS, self.nudge_network)

    def nudge_network(self):
        """Fire one registration retry and keep the timer while still bad."""
        if self.ofono.network_status in ("registered", "roaming", ""):
            self._net_nudge_timer = None
            return False
        logger.warning(f"[App] Network stuck in {self.ofono.network_status}, nudging Register()")
        run_in_background(self.ofono.register_network)
        return True

    def on_denied_persisted(self):
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

    def watch_sim_pin(self, _ofono, pin_type):
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
        """Restart the modem stack once; False when a run is already going.

        All recovery requests pass through this method so the radio state is
        checked in one place. A deliberately switched-off radio is the only
        hard refusal because restarting the software stack does not enable it.

        A no-modem result is only a heuristic. A running RIL process can
        still be stuck, so an explicit recovery request is allowed to restart
        the stack anyway. If no modem appears afterward, the heuristic is used
        only to choose the more specific failure message.

        on_done receives (success, reason). reason is empty when a restart
        was attempted and simply did not restore the modem.
        """
        if self._auto_recovery_running:
            return False
        self._auto_recovery_running = True
        state = {"hopeless": ""}

        def verdict(success):
            self._auto_recovery_running = False
            if success:
                logger.info("[App] Modem recovery succeeded")
                self._modem_notified = False
                self.notification_manager.close_notification("modem_unavailable")
                self.dismiss_recovery_surface()
            elif state["hopeless"]:
                logger.error("[App] Modem recovery changed nothing on a device reporting no modem")
                self.ofono.set_modem_absent(True)
                if on_done:
                    on_done(False, self.recovery_refusal_text(state["hopeless"]))
                return
            else:
                logger.error("[App] Modem recovery failed")
                self.surface_modem_recovery(failed=True)
            if on_done:
                on_done(success, "")

        def fired(_result):
            watch_recovery_result(self.ofono, verdict)

        def failed(error):
            logger.warning(f"[App] Modem recovery commands errored: {error}")
            watch_recovery_result(self.ofono, verdict)

        def decided(blocker):
            if blocker == "radio-off":
                self._auto_recovery_running = False
                logger.info("[App] Not restarting the modem stack: the radio is switched off")
                if on_done:
                    on_done(False, self.recovery_refusal_text(blocker))
                return
            state["hopeless"] = blocker
            run_in_background(execute_modem_recovery, on_complete=fired, on_error=failed)

        run_in_background(self.recovery_refusal_reason, on_complete=decided,
                          on_error=lambda error: decided(""))
        return True

    def open_modem_recovery(self):
        """Show the modem recovery screen with the current status."""
        self.surface_modem_recovery()

    def ensure_voicemail_contact(self):
        """Create a Voicemail contact for the mailbox number when missing.

        Create-only by design: when the number already belongs to any
        contact under any name, that reference is left alone. The
        contact carries a uid derived from the number, so a repeat save
        rewrites that one contact instead of minting another, and the
        check reads the stored contacts rather than the in-memory map,
        which trails a write by the time a view takes to stream it back.
        """
        if not self.ofono or not self.eds or not self.eds.is_ready:
            return
        number = self.ofono.get_voicemail_number()
        if not number or self._vm_contact_busy:
            return
        if number == self._vm_contact_number:
            return
        self._vm_contact_busy = True

        def task():
            if self.eds.get_contact_name(number):
                return False
            if self.eds.search_contacts(normalize_number(number) or number):
                return False
            uid = f"{VOICEMAIL_CONTACT_UID_PREFIX}{normalize_number(number) or number}"
            vcard = (f"BEGIN:VCARD\nVERSION:3.0\nFN:{_('Voicemail')}\n"
                     f"TEL;TYPE=VOICE:{number}\nUID:{uid}\nEND:VCARD")
            return self.eds.save_contact(vcard, source_uid=SYSTEM_ADDRESS_BOOK_UID)

        def done(created):
            self._vm_contact_busy = False
            self._vm_contact_number = number
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
        number = self.ofono.get_voicemail_number()
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

        custom_sound = self.get_custom_sms_tone(number)
        if not custom_sound and lookup_number:
            custom_sound = self.get_custom_sms_tone(lookup_number)

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

    def clear_notification(self, number):
        """Clear active notifications for a number."""
        norm_num = normalize_number(number)
        self.notification_counts[norm_num] = 0
        self.notification_manager.close_notification(norm_num)
        self.notification_manager.close_notification(f"missed_{norm_num}")
        self.ui.withdraw_number_notifications(norm_num)
