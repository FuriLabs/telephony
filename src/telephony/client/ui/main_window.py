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

from telephony.shared.utils.thread_utils import run_in_background
import urllib.parse
from gettext import gettext as _, ngettext

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.system_utils import get_phosh_emergency_calls
from telephony.shared.utils import region_utils as utils
from telephony.client.ui.views.history_view import HistoryView
from telephony.client.ui.views.contacts_view import ContactsView
from telephony.client.ui.views.dialpad_view import DialpadView
from telephony.client.ui.views.messages_view import MessagesView
from telephony.client.ui.windows.settings_window import SettingsWindow
from telephony.client.ui.windows.contact_editor_window import ContactEditor
from telephony.client.ui.windows.missed_scheduled_messages_window import (MissedScheduledMessagesDialog)
from telephony.client.ui.windows.blocklist_editor_window import BlocklistEditor
from telephony.client.ui.windows.info_window import InfoPage
from telephony.client.ui.windows.contact_picker_window import ContactPicker
from telephony.client.ui.windows.duplicate_resolution_window import DuplicateResolutionWindow
from telephony.client.utils.model_utils import (call_direction_text, call_outcome_text,
                                                call_ending_text)
from telephony.client.ui.widgets.common_widget import (present_choice_sheet, add_choice_row,
                                                      build_info_sheet,
                                                      install_sheet_host, present_sheet,
                                                      present_sheet_page, close_sheet_page,
                                                      present_alert_sheet)

CAPABILITY_BANNER_REASONS = ("no-modem", "airplane-mode", "no-voice-service")


class MainWindow(Adw.Window):
    """The main application window containing the stack of views (History, Dialpad, Messages, Contacts)."""

    def __init__(self, application, ofono_manager, db_manager, eds_manager, gsettings_mgr=None, show_calls=False, show_messages=False, show_contacts=False):
        self._unread_timer = None
        self._menu_actions = {}
        self._resolve_section = None
        self.in_error_mode = False
        self._manual_sync_active = False
        self._ussd_in_flight = False
        self._loading_toast = None
        self._current_toast = None
        self._current_message = None
        self._setup_hint_shown = False
        """Initialize the main window."""
        super().__init__(application=application)
        self.app = application

        self.show_calls_mode = show_calls
        self.show_messages_mode = show_messages
        self.show_contacts_mode = show_contacts

        self.set_title("Telephony")
        self.set_icon_name("io.furios.Telephony")
        self.set_default_size(360, 600)
        self.eds = eds_manager
        self.db = db_manager
        self.ofono = ofono_manager
        self.gsettings_mgr = gsettings_mgr
        self.daemon = self.app.daemon_client


        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.sheet_host = install_sheet_host(self)

        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(main_vbox)

        self.header = Adw.HeaderBar()
        title_lbl = Gtk.Label(label=_("Telephony"), css_classes=["title"])
        self.header.set_title_widget(title_lbl)

        self.actions_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.setup_actions_menu()
        self.header.pack_end(self.actions_btn)
        main_vbox.append(self.header)

        self.banner = Adw.Banner()
        self.banner.set_revealed(False)
        self.banner.connect("button-clicked", self._on_banner_action)
        self._banner_action = None
        self._banner_states = {}
        main_vbox.append(self.banner)

        self.stack = Adw.ViewStack()

        self.history_view = None
        self.contacts_view = None
        self.messages_view = None
        self.dialpad_view = None
        self.msgs_page = None
        self._view_pages = {}

        def add_lazy_page(name, title, icon):
            placeholder = Adw.Bin()
            page = self.stack.add_titled(placeholder, name, title)
            page.set_icon_name(icon)
            self._view_pages[name] = placeholder
            return page

        if self.show_calls_mode:
            add_lazy_page("dialpad", _("Dialpad"), "input-dialpad-symbolic")
            add_lazy_page("history", _("History"), "document-open-recent-symbolic")

        if self.show_messages_mode:
            self.msgs_page = add_lazy_page("messages", _("Messages"), "mail-message-new-symbolic")

        if self.show_contacts_mode:
            add_lazy_page("contacts", _("Contacts"), "system-users-symbolic")

        self.stack.connect("notify::visible-child-name", self._on_stack_page_changed)
        self._check_country_code()
        self._ensure_view(self.stack.get_visible_child_name())

        self.stack.set_vexpand(True)
        main_vbox.append(self.stack)

        self.switcher = Adw.ViewSwitcherBar(stack=self.stack, reveal=True)
        main_vbox.append(self.switcher)


        self.signal_ids = []
        if self.ofono:
            self.signal_ids.append((self.ofono, self.ofono.connect('connection-status', self.on_ofono_status)))
            self.signal_ids.append((self.ofono, self.ofono.connect('action-error', lambda obj, msg: self.notify_error(msg))))
            self.signal_ids.append((self.ofono, self.ofono.connect('ussd-notification', lambda obj, msg: self.show_ussd_dialog(msg))))

        self.signal_ids.append((self.eds, self.eds.connect('contacts-loaded', self.on_contacts_loaded)))

        if self.ofono:
            for sig in ('call-added', 'call-removed', 'dial-availability-changed'):
                self.signal_ids.append((self.ofono, self.ofono.connect(sig, self._refresh_calling_controls)))
            self.signal_ids.append((self.ofono, self.ofono.connect('dial-availability-changed', self._on_capability_changed)))
            self.signal_ids.append((self.ofono, self.ofono.connect('modem-interface-appeared', self._on_modem_interface_appeared)))
            self._on_capability_changed()

        if self.msgs_page:
            self.signal_ids.append((self.db, self.db.connect('messages-updated', lambda *args: self.update_unread_badge())))

        if self.eds.is_ready:
            self.update_unread_badge()
            self._update_sensitive_actions(True)
        else:
            self._update_sensitive_actions(False)
            self.set_banner_state("syncing", _("Syncing contacts…"), priority=10)

        self.check_own_number()
        self.check_emergency_setup()

        self.connect("close-request", self.on_close_request)
        self.connect("map", self._on_window_map)
        self._initial_check_done = False

        self.signal_ids.append((self.gsettings_mgr.gsettings, self.gsettings_mgr.gsettings.connect("changed::duplicate-resolver-enabled", self.on_duplicate_resolver_setting_changed)))

        self.popup_queue = []
        self.is_popup_active = False
        self.pending_conflicts = []
        self._duplicate_count = 0

        self.blocklist_view = None

    def enqueue_popup(self, start_func):
        """Enqueue a popup/dialog task to ensure they don't overlap."""
        self.popup_queue.append(start_func)
        self.process_popup_queue()

    def process_popup_queue(self):
        """Process the next popup task."""
        if self.is_popup_active:
            return
        if not self.popup_queue:
            return

        self.is_popup_active = True
        func = self.popup_queue.pop(0)
        try:
            func(self.on_popup_done)
        except Exception as e:
            logger.error(f"[MainWindow] Popup task failed: {e}")
            self.on_popup_done()

    def on_popup_done(self):
        """Callback when a popup task is finished."""
        self.is_popup_active = False
        self.process_popup_queue()

    def _on_window_map(self, *args):
        """Handle window map event."""
        if not self._initial_check_done:
            self._initial_check_done = True
            self.check_daemon_service()
            missed_messages_dialog = MissedScheduledMessagesDialog(self)
            self.enqueue_popup(missed_messages_dialog.check_missed_scheduled_messages)

    def check_daemon_service(self):
        """Show the service state this window was born into."""
        self.apply_service_presence(not self.app.daemon_missing,
                                    self.app.core.service_monitor.state)

    def apply_service_presence(self, present, unit_state):
        """Keep a standing banner while the service is away.

        Without the service nothing answers for incoming calls or
        arriving messages, so its absence deserves a lasting surface.
        Sending stays enabled: a send revives the service through bus
        activation. Only the failed state blocks activation, and its
        Start path resets the unit first.
        """
        if present:
            self.clear_banner_state("service")
            return
        if unit_state == "restarting":
            self.set_banner_state("service", _("Telephony service is restarting…"), priority=30)
            return
        self.set_banner_state("service", _("Telephony service is not running"),
                              button_label=_("Start"),
                              action=self._start_service_from_banner,
                              priority=30)

    def _start_service_from_banner(self):
        """Run the banner's start offer."""
        self.app.start_service(self._on_daemon_retried)

    def _on_daemon_retried(self, started):
        """Report whether the service answered this time."""
        if started:
            self.notify_success(_("Telephony service started"))

    def on_close_request(self, *args):
        """Handle window close request."""
        self.cleanup()
        self.app.on_window_destroyed(self)
        return False

    def cleanup(self):
        """Cleanup resources and widgets before destruction."""
        if self._unread_timer:
            GLib.source_remove(self._unread_timer)
            self._unread_timer = None

        for obj, sig_id in self.signal_ids:
            if obj.handler_is_connected(sig_id):
                obj.disconnect(sig_id)
        self.signal_ids.clear()

        if self.history_view:
            self.history_view.cleanup()

        if self.messages_view:
            self.messages_view.cleanup()

        if self.contacts_view:
            self.contacts_view.cleanup()

        if self.dialpad_view:
            self.dialpad_view.cleanup()

        self.switcher = None
        self.stack = None
        self.toast_overlay = None

        self.set_content(None)

    def check_own_number(self):
        """Check if own number is set, warn if not."""
        def _check():
            num = self.app.daemon_client.get_own_number()
            if not num:
                num = self.gsettings_mgr.get_setting("own_number")

            if not num:
                GLib.idle_add(lambda: self._show_setup_hint(_("Set your number in Settings")) or False)
        run_in_background(_check)

    def _check_country_code(self):
        """Give this window the country its numbers belong to.

        A stored number without a country code is read as belonging to
        wherever the app thinks it is, so the answer has to be in hand
        before the first list is built rather than shortly after it.
        The setting is a local read and is applied straight away; only
        asking the modem is worth a thread, and that is the case where
        there is no answer to be late with.
        """
        cc = self.gsettings_mgr.get_setting("default_country_code")
        if cc:
            utils.set_custom_region(cc)
            return

        def _task():
            region = self.app.daemon_client.detect_region()
            if region:
                self.gsettings_mgr.set_setting("default_country_code", region)
                utils.set_custom_region(region)
            else:
                GLib.idle_add(lambda: self._show_setup_hint(_("Please set Default Country Code in Settings")) or False)
        run_in_background(_task)

    def _on_modem_interface_appeared(self, _ofono, interface):
        """Retry region detection once network registration becomes available."""
        if interface != "org.ofono.NetworkRegistration":
            return
        if self.gsettings_mgr.get_setting("default_country_code"):
            return
        self._check_country_code()

    def check_emergency_setup(self):
        """Check if emergency numbers are configured."""
        try:
            if not get_phosh_emergency_calls():
                return

            if self.gsettings_mgr.get_emergency_numbers():
                return

            GLib.idle_add(lambda: self._show_setup_hint(_("Setup Emergency Numbers in Settings")) or False)
        except Exception as e:
            logger.warning(f"[MainWindow] Emergency setup check warning: {e}")

    def setup_actions_menu(self):
        """Initialize the primary actions menu."""
        group = Gio.SimpleActionGroup()
        entries = (
            ("resolve-duplicates", self.on_resolve_duplicates_clicked),
            ("settings", self.on_settings_click),
            ("reload-contacts", self.on_force_sync_click),
            ("about", self.on_about_click),
        )
        for name, callback in entries:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda a, p, cb=callback: GLib.idle_add(lambda: cb(None) or False))
            group.add_action(action)
            self._menu_actions[name] = action
        self.insert_action_group("menu", group)

        self._resolve_section = Gio.Menu()
        main_section = Gio.Menu()
        main_section.append(_("Settings"), "menu.settings")
        main_section.append(_("Reload Contacts"), "menu.reload-contacts")
        main_section.append(_("About"), "menu.about")
        menu = Gio.Menu()
        menu.append_section(None, self._resolve_section)
        menu.append_section(None, main_section)
        self.actions_btn.set_menu_model(menu)

    def on_about_click(self, _btn):
        """Open the about page from the menu."""
        InfoPage.show(self)

    def _set_resolve_visible(self, visible):
        """Show or hide the duplicate resolution menu entry."""
        self._resolve_section.remove_all()
        if visible:
            self._resolve_section.append(_("Resolve Duplicates"), "menu.resolve-duplicates")

    def _update_sensitive_actions(self, sensitive):
        """Enable or disable actions based on readiness."""
        for name in ("settings", "reload-contacts", "import-export", "blocklist"):
            if name in self._menu_actions:
                self._menu_actions[name].set_enabled(sensitive)

    def _refresh_calling_controls(self, *args):
        """Grey out call-starting controls while no new call can be placed."""
        available = self.ofono.dialing_available() if self.ofono else True
        if self.dialpad_view:
            self.dialpad_view.set_calling_enabled(available)
        if self.history_view:
            self.history_view.set_calling_enabled(available)
        if self.contacts_view:
            self.contacts_view.set_calling_enabled(available)

    def _toast_target(self):
        """Return the overlay of the topmost surface.

        A presented dialog is painted above the window content, so a
        toast raised on the window's own overlay would sit hidden
        underneath an open sheet.
        """
        dialog = self.get_visible_dialog()
        if dialog is not None:
            overlay = self._find_toast_overlay(dialog.get_child())
            if overlay is not None:
                return overlay
        return self.toast_overlay

    def _find_toast_overlay(self, widget):
        """Find the first toast overlay inside a widget tree."""
        if widget is None:
            return None
        if isinstance(widget, Adw.ToastOverlay):
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = self._find_toast_overlay(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    def set_banner_state(self, key, message, button_label=None, action=None, priority=0):
        """Record a lasting state and show the most important one.

        States are keyed so two conditions cannot overwrite each other,
        and a state that ends never takes down a different one that is
        still true.
        """
        self._banner_states[key] = {"message": message, "button": button_label,
                                    "action": action, "priority": priority}
        self._refresh_banner()

    def clear_banner_state(self, key):
        """Drop one state and fall back to whatever else still holds."""
        if self._banner_states.pop(key, None) is not None:
            self._refresh_banner()

    def _refresh_banner(self):
        """Show the highest priority state, or nothing at all."""
        if not self._banner_states:
            self._banner_action = None
            self.banner.set_revealed(False)
            return

        state = max(self._banner_states.values(), key=lambda s: s["priority"])
        self.banner.set_title(state["message"])
        self.banner.set_button_label(state["button"] or "")
        self._banner_action = state["action"]
        self.banner.set_revealed(True)

    def _on_banner_action(self, _banner):
        """Run whatever the banner offered."""
        if self._banner_action:
            GLib.idle_add(lambda: self._banner_action() or False)

    def _show_toast(self, message, timeout=5, priority=None):
        """Show one toast on the topmost surface, replacing what it supersedes.

        Adwaita shows a single toast at a time and queues the rest, so a
        stale message would otherwise stand in front of a newer one. The
        current toast is dismissed instead of queued, and repeating the
        message that is already showing only restarts its timer.
        """
        if self._current_toast is not None and self._current_message == message:
            return self._current_toast

        if self._current_toast is not None:
            self._current_toast.dismiss()

        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        if priority is not None:
            toast.set_priority(priority)
        toast.connect("dismissed", self._on_toast_dismissed, message)
        self._current_toast = toast
        self._current_message = message
        self._toast_target().add_toast(toast)
        return toast

    def _on_toast_dismissed(self, toast, message):
        """Forget the toast that just went away."""
        if self._current_toast is toast:
            self._current_toast = None
            self._current_message = None

    def notify_error(self, message):
        """Report a refusal, ahead of anything already showing."""
        self.hide_loading()
        self._show_toast(message, priority=Adw.ToastPriority.HIGH)

    def notify_success(self, message):
        """Report a finished action the screen does not already show."""
        self.hide_loading()
        self._show_toast(message)

    def notify_loading(self, message):
        """Show a persistent toast for a long running operation."""
        self.hide_loading()
        self._loading_toast = self._show_toast(message, timeout=0)

    def hide_loading(self):
        """Dismiss the long running operation toast."""
        if self._loading_toast is not None:
            self._loading_toast.dismiss()
            self._loading_toast = None
            self._current_toast = None
            self._current_message = None

    def _show_setup_hint(self, message):
        """Show at most one settings hint per launch, with a shortcut."""
        if self._setup_hint_shown:
            return
        self._setup_hint_shown = True
        toast = Adw.Toast.new(message)
        toast.set_timeout(10)
        toast.set_button_label(_("Settings"))
        toast.connect("button-clicked", lambda t: GLib.idle_add(lambda: self.on_settings_click(None) or False))
        self.toast_overlay.add_toast(toast)

    def on_contacts_loaded(self, *args):
        """Handle contacts loaded event."""
        if self.eds.is_ready:
            self.clear_banner_state("syncing")
            if self._manual_sync_active:
                self._manual_sync_active = False
                self.notify_success(_("Contacts refreshed"))
            else:
                self.hide_loading()
            self._update_sensitive_actions(True)

    def update_unread_badge(self):
        """Update the unread messages badge number."""
        if self.msgs_page and self.db:
            if (self._unread_timer is not None) and self._unread_timer:
                GLib.source_remove(self._unread_timer)
            self._unread_timer = GLib.timeout_add(200, self._do_update_unread_badge)

    def _do_update_unread_badge(self):
        self._unread_timer = None
        if self.msgs_page and self.db:
            def _fetch_unread():
                count = self.db.get_total_unread_count()
                GLib.idle_add(lambda: self.msgs_page.set_badge_number(count) or False)
            run_in_background(_fetch_unread)
        return False

    def on_ofono_status(self, manager, status, message):
        """Log ofono status changes; the recovery flow owns the surfacing."""
        logger.debug(f"[MainWindow] ofono status {status}: {message}")

    def _on_capability_changed(self, *args):
        """Say why calls cannot be placed, when the reason will last.

        Transient states stay off the banner: warming up and call
        teardown resolve themselves in seconds, and an ongoing call is
        not a problem to report. Messaging is store-and-forward and is
        never gated by any of this.
        """
        if self.ofono.dial_reason in CAPABILITY_BANNER_REASONS:
            self.set_banner_state("capability", self.ofono.dial_description, priority=20)
        else:
            self.clear_banner_state("capability")

    def _on_stack_page_changed(self, *args):
        """Build the newly selected view lazily and refresh the chrome."""
        self._ensure_view(self.stack.get_visible_child_name())
        self.sync_chat_chrome()

    def sync_chat_chrome(self):
        """Hide the window header while the messages tab shows an open chat.

        The chat page carries its own header, so keeping the window one
        stacked two navigation bars on top of each other.
        """
        in_chat = (self.stack.get_visible_child_name() == "messages"
                   and self.messages_view is not None
                   and self.messages_view.in_chat())
        self.header.set_visible(not in_chat)

    def _ensure_view(self, name):
        """Construct the view for a stack page on first use."""
        placeholder = self._view_pages.get(name)
        if placeholder is None or placeholder.get_child() is not None:
            return

        logger.debug(f"[MainWindow] Building view for tab: {name}")
        if name == "dialpad":
            self.dialpad_view = DialpadView(self)
            placeholder.set_child(self.dialpad_view)
            self._refresh_calling_controls()
        elif name == "history":
            self.history_view = HistoryView(self.db, self)
            placeholder.set_child(self.history_view)
            self._refresh_calling_controls()
        elif name == "messages":
            self.messages_view = MessagesView(self.db, self)
            placeholder.set_child(self.messages_view)
        elif name == "contacts":
            self.contacts_view = ContactsView(self.eds, self)
            placeholder.set_child(self.contacts_view)
            self._refresh_calling_controls()

    def open_chat_for_number(self, number):
        """Switch to messages view and open chat."""
        if self.show_messages_mode:
            self._ensure_view("messages")
            self.stack.set_visible_child_name("messages")
            self.messages_view.open_chat(number, number)

    def open_dialpad_with_number(self, number):
        """Switch to dialpad and pre-fill number."""
        if self.show_calls_mode:
            self._ensure_view("dialpad")
            self.stack.set_visible_child_name("dialpad")
            self.dialpad_view.entry.set_text(number)
            self.dialpad_view.entry.set_position(-1)

    def handle_ussd(self, code):
        """Initiate a USSD request, one at a time."""
        if self._ussd_in_flight:
            self.notify_error(_("A USSD request is already in progress"))
            return
        if not self.ofono:
            self.notify_error(_("Modem not ready"))
            return

        self._ussd_in_flight = True
        self.notify_loading(_("Sending USSD..."))

        def done(res):
            self._ussd_in_flight = False
            self.hide_loading()
            if res:
                self.show_ussd_dialog(res)
            else:
                self.notify_error(_("USSD request failed"))

        def failed(error):
            self._ussd_in_flight = False
            self.hide_loading()
            logger.error(f"[MainWindow] USSD request failed: {error}")
            self.notify_error(_("USSD request failed"))

        run_in_background(self.ofono.send_ussd, code, on_complete=done, on_error=failed)

    def show_ussd_dialog(self, text):
        """Show the USSD response sheet, replacing any shown before it."""
        present_sheet_page(self, build_info_sheet(_("USSD Result"), text, selectable=True),
                           replace=True)

    def confirm_action(self, title, body, on_confirm):
        """Show a confirmation dialog."""
        def _cb(resp):
            if resp == "yes":
                on_confirm()
        present_alert_sheet(self, title, body,
                            [("cancel", _("Cancel"), None), ("yes", _("Confirm"), "destructive")],
                            _cb)

    def on_settings_click(self, btn):
        """Open the settings sheet."""
        present_sheet(self, SettingsWindow(self, self.eds, self.ofono))

    def on_duplicate_resolver_setting_changed(self, settings, key):
        """Handle toggle of duplicate resolver setting."""
        enabled = settings.get_boolean(key)
        if not enabled:
            self._set_resolve_visible(False)
            self.pending_conflicts = []
            self.clear_banner_state("duplicates")
        else:
            if self.contacts_view:
                self.contacts_view.check_duplicates()

    def update_duplicate_status(self, conflicts):
        """Update duplicate resolution UI status."""
        self.pending_conflicts = conflicts
        count = len(conflicts)
        previous = self._duplicate_count
        self._duplicate_count = count
        resolver_enabled = self.gsettings_mgr.gsettings.get_boolean("duplicate-resolver-enabled")

        if resolver_enabled and count > 0:
            self._set_resolve_visible(True)
            if count == previous:
                return

            message = ngettext(
                "Found {count} duplicate contact.",
                "Found {count} duplicate contacts.",
                count
            ).format(count=count)
            self.set_banner_state("duplicates", message, _("Resolve"),
                                  lambda: self.on_resolve_duplicates_clicked(None))
        else:
            self._set_resolve_visible(False)
            self.clear_banner_state("duplicates")

    def on_resolve_duplicates_clicked(self, btn):
        """Handle resolve duplicates button click."""
        if not self.pending_conflicts:
            return
        win = DuplicateResolutionWindow(self.pending_conflicts, self.eds, self.daemon, self.on_resolution_done)
        win.present_standalone(self)

    def on_resolution_done(self):
        """Callback when duplicate resolution is done."""
        if self.contacts_view:
            self.contacts_view.check_duplicates()

    def present_blocklist_editor(self, number_preset=None):
        """Open blocklist editor dialog."""
        if not self.eds.is_ready:
            return

        name_preset = ""
        if number_preset:
            contact_name = self.eds.get_contact_name(number_preset)
            if contact_name:
                name_preset = contact_name
        present_sheet_page(self, BlocklistEditor(self.db, self.eds, self,
                                                 number_preset=number_preset,
                                                 name_preset=name_preset))

    def on_force_sync_click(self, btn):
        """Force address book backends to sync, falling back to a local reload."""

        def task():
            return self.daemon.refresh_contacts()

        def done(refreshed):
            if refreshed:
                self.notify_success(_("Sync started for {count} address books").format(count=refreshed))
                return
            self._manual_sync_active = True
            self.eds.reload()

        def failed(error):
            logger.error(f"[MainWindow] Backend refresh failed: {error}")
            self._manual_sync_active = True
            self.eds.reload()

        run_in_background(task, on_complete=done, on_error=failed)

    def present_edit_contact(self, contact_data=None, number_preset=None):
        """Open contact editor."""
        if not self.eds.is_ready:
            return

        if contact_data is None and number_preset:
            results = self.eds.search_contacts(number_preset)
            if not results:
                pass
            elif len(results) == 1:
                contact_data = self.eds.cache.get(results[0][0])
            else:
                def offer(books):
                    book_names = {book['uid']: book['name'] for book in (books or [])}
                    self._choose_contact_to_edit(results, book_names)

                run_in_background(self.daemon.get_address_books, on_complete=offer)
                return

        self._open_contact_editor(contact_data, number_preset)

    def _open_contact_editor(self, contact_data, number_preset):
        """Show the editor for one contact."""
        present_sheet_page(self, ContactEditor(self.eds, self, contact_data,
                                               number_preset=number_preset))

    def _choose_contact_to_edit(self, results, book_names):
        """Ask which of the matching contacts to edit.

        The books are named by the owner rather than by this window: a
        window keeps no address book registry of its own, so asking it
        leaves every book reading as unknown.
        """
        def build(group, window):
            for res in results:
                c_data = self.eds.cache.get(res[0])
                if not c_data:
                    continue

                add_choice_row(group, window, c_data.get('name', _("Unknown")),
                               lambda data=c_data: self.present_edit_contact(contact_data=data),
                               subtitle=book_names.get(c_data.get('source_uid'),
                                                       _("Unknown Addressbook")),
                               opens_flow=True)

        present_choice_sheet(self, _("Multiple Contacts Found"), build,
                             description=_("Which contact would you like to edit?"))

    def present_chat(self, number):
        """Open chat for a number."""
        if not self.show_messages_mode:
            logger.info("Messages mode not active. Opening the messages window for chat.")
            self.get_application().open_messages_chat(number)
            return

        self.stack.set_visible_child_name("messages")
        name = self.eds.get_contact_name(number) or number
        self.messages_view.open_chat(number, name)

    def start_call(self, number, hide_id=False):
        """Start a call."""
        self.set_focus(None)

        if self.ofono and not self.ofono.dialing_available():
            description = self.ofono.dial_description
            self.notify_error(description if description else _("Call Failed"))
            return

        status_msg = _("Calling (Anonymous)...") if hide_id else _("Calling {number}...").format(number=number)
        self.notify_success(status_msg)

        if not self.ofono:
            self.notify_error(_("Call Failed"))
            return

        def done(success):
            if not success:
                logger.error(f"[MainWindow] Call failed to {number}")

        run_in_background(self.ofono.dial, number, on_complete=done, hide_id=hide_id)

    def show_call_details(self, item):
        """Show the call details sheet for a history item."""
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        toolbar.set_content(page)

        grp_info = Adw.PreferencesGroup()
        page.add(grp_info)
        rows = [(_("Number"), item.number), (_("Name"), item.name),
                (_("Direction"), call_direction_text(item.direction)),
                (_("Result"), call_outcome_text(item.direction, item.disconnect_reason)),
                (_("Date"), item.full_ts), (_("Duration"), item.duration_str)]

        if item.anonymous:
            rows.append((_("Caller ID"), _("Hidden")))
        if item.multiparty:
            rows.append((_("Conference"), _("This call was part of a conference")))
        if item.transferred:
            rows.append((_("Transferred"), _("The call was handed to someone else")))

        rows.append((_("Ended"), call_ending_text(item.disconnect_reason, item.direction)))

        for title, value in rows:
            grp_info.add(Adw.ActionRow(title=title, subtitle=str(value)))

        grp_actions = Adw.PreferencesGroup()
        page.add(grp_actions)

        def add_action(label, callback, destructive=False, needs_eds=False, opens_flow=False):
            """Add one action row.

            An action that opens another flow leaves these details
            where they are, so the flow goes on top of them and back
            comes here. One that finishes the job takes them away.
            """
            row = Adw.ActionRow(title=label, activatable=True)
            if destructive:
                row.add_css_class("error")
            if needs_eds and not self.eds.is_ready:
                row.set_sensitive(False)
            if opens_flow:
                row.connect("activated", lambda r: GLib.idle_add(
                    lambda: callback() or False))
            else:
                row.connect("activated", lambda r: GLib.idle_add(
                    lambda: [close_sheet_page(self), callback()] and False))
            grp_actions.add(row)

        blocked_list = self.db.get_blocked_numbers()
        is_blocked_id = None
        norm_item_num = normalize_number(item.number)
        for entry in blocked_list:
            if normalize_number(entry["number"]) == norm_item_num:
                is_blocked_id = entry["id"]
                break

        if is_blocked_id:
            def _unblock():
                self.daemon.remove_blocked_number(is_blocked_id)
                self.notify_success(_("Unblocked"))
            add_action(_("Unblock Number"), _unblock, needs_eds=True)
        else:
            lbl = _("Edit Contact") if item.is_saved else _("Add to Contacts")
            add_action(lbl, lambda: self.present_edit_contact(number_preset=item.number),
                           needs_eds=True, opens_flow=True)

            if not item.is_saved:
                add_action(_("Add to Existing Contact"), lambda: self.on_add_to_existing(item),
                               needs_eds=True, opens_flow=True)
                add_action(_("Search Number"), lambda: self.search_number_online(item.number))

        add_action(_("Send Message"), lambda: self.present_chat(item.number))
        add_action(_("Copy Number"), lambda: self.copy_to_clipboard(item.number))

        if not is_blocked_id:
            add_action(_("Block Number"), lambda: self.present_blocklist_editor(number_preset=item.number),
                       opens_flow=True,
                       destructive=True, needs_eds=True)

        add_action(_("Delete this call"),
                   lambda: self.confirm_action(
                       _("Delete Call"),
                       _("The call with {who} on {when} will be removed from your history.").format(
                           who=item.name or item.number, when=item.full_ts),
                       lambda: [close_sheet_page(self),
                                self.daemon.delete_call_entry(item.id)]),
                   destructive=True, opens_flow=True)

        present_sheet_page(self, Adw.NavigationPage(title=_("Call Details"), child=toolbar))

    def search_number_online(self, number):
        """Open the configured search engine for a phone number."""
        clean_num = number.replace("+", "")
        engine = self.gsettings_mgr.get_setting("unknown_callers_engine") or "duckduckgo"
        custom_url = self.gsettings_mgr.get_setting("unknown_callers_custom_url") or ""

        search_url = ""
        encoded_num = urllib.parse.quote(clean_num)

        if engine == "startpage":
            search_url = f"https://www.startpage.com/do/dsearch?query={encoded_num}"
        elif engine == "duckduckgo":
            search_url = f"https://duckduckgo.com/?q={encoded_num}"
        elif engine == "custom" and custom_url:
            search_url = custom_url.replace("{number}", encoded_num)

        if search_url:
            Gio.AppInfo.launch_default_for_uri(search_url, None)

    def copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        Gdk.Display.get_default().get_clipboard().set(text)
        self.notify_success(_("Number copied"))

    def on_add_to_existing(self, item):
        """Handle adding number to an existing contact."""
        def _cb(result):
            uid, name = result

            def done(success):
                if success:
                    self.notify_success(_("Added to {name}").format(name=name))
                else:
                    logger.error(f"[MainWindow] Failed to add number {item.number} to contact {uid}")
                    self.notify_error(_("Failed to add number"))

            def add_task():
                vcard = self.eds.build_number_added_vcard(uid, item.number)
                if not vcard:
                    return False
                return self.daemon.save_contact(vcard, uid=uid)[0]

            run_in_background(add_task, on_complete=done)

        picker = ContactPicker(
            self.eds,
            self,
            _cb,
            title=_("Add Number"),
            action_label=_("Save"),
            allow_custom_number=False,
            return_contact_uid=True
        )
        present_sheet_page(self, picker)
