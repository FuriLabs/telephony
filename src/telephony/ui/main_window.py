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

from ..backend.utils.thread_utils import run_in_background
import urllib.parse
from .windows.import_export_window import ImportExportDialog
from gettext import gettext as _, ngettext

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk
from loguru import logger

from ..backend.utils.phone_utils import normalize_number, get_own_number
from ..backend.utils.system_utils import get_phosh_emergency_calls
from ..backend.utils import region_utils as utils
from .views.history_view import HistoryView
from .views.contacts_view import ContactsView
from .views.dialpad_view import DialpadView
from .views.messages_view import MessagesView
from .windows.settings_window import SettingsWindow
from .windows.contact_editor_window import ContactEditor
from .windows.missed_scheduled_messages_window import MissedScheduledMessagesDialog
from .windows.blocklist_window import BlocklistView
from .windows.blocklist_editor_window import BlocklistEditor
from .windows.info_window import InfoPage
from .windows.contact_picker_window import ContactPicker
from .windows.duplicate_resolution_window import DuplicateResolutionWindow
from .widgets.common_widget import present_choice_sheet, add_choice_row, build_info_sheet


class MainWindow(Adw.Window):
    """The main application window containing the stack of views (History, Dialpad, Messages, Contacts)."""

    def __init__(self, application, ofono_manager, db_manager, eds_manager, mms_manager=None, gsettings_mgr=None, show_calls=False, show_messages=False, show_contacts=False):
        self._unread_timer = None
        self._menu_actions = {}
        self._resolve_section = None
        self.in_error_mode = False
        self._manual_sync_active = False
        self._active_alert = None
        self._ussd_in_flight = False
        self._loading_toast = None
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
        self.mms = mms_manager
        self.gsettings_mgr = gsettings_mgr
        self.scheduler = self.app.scheduler


        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(main_vbox)

        self.header = Adw.HeaderBar()
        title_lbl = Gtk.Label(label=_("Telephony"), css_classes=["title"])
        self.header.set_title_widget(title_lbl)

        info_btn = Gtk.Button(icon_name="dialog-information-symbolic")
        info_btn.add_css_class("flat")
        info_btn.add_css_class("circular")
        info_btn.connect("clicked", lambda b: GLib.idle_add(lambda: InfoPage.show(self) or False))
        self.header.pack_start(info_btn)

        self.actions_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.setup_actions_menu()
        self.header.pack_end(self.actions_btn)
        main_vbox.append(self.header)

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
            self.signal_ids.append((self.ofono, self.ofono.connect('modem-interface-appeared', self._on_modem_interface_appeared)))

        if self.msgs_page:
            self.signal_ids.append((self.db, self.db.connect('messages-updated', lambda *args: self.update_unread_badge())))

        if self.eds.is_ready:
            self.update_unread_badge()
            self._update_sensitive_actions(True)
        else:
            self._update_sensitive_actions(False)

        self.check_own_number()
        self.check_emergency_setup()
        self._check_country_code()

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
        """Say when the background service is down, and offer to start it.

        Without it nothing answers for calls or arriving messages, and
        this window will not stand in, so the user has to know.
        """
        if not self.app.daemon_missing:
            return

        toast = Adw.Toast.new(_("Telephony service is not running"))
        toast.set_timeout(0)
        toast.set_button_label(_("Start"))
        toast.connect("button-clicked", lambda t: self.app.retry_daemon_start(self._on_daemon_retried))
        self.toast_overlay.add_toast(toast)

    def _on_daemon_retried(self, started):
        """Report whether the service answered this time."""
        if started:
            self.notify_success(_("Telephony service started"))
            return
        self.check_daemon_service()

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
            num = get_own_number()
            if not num:
                num = self.gsettings_mgr.get_setting("own_number")

            if not num:
                GLib.idle_add(lambda: self._show_setup_hint(_("Set your number in Settings")) or False)
        run_in_background(_check)

    def _check_country_code(self):
        """Check and setup default country code."""
        def _task():
            cc = self.gsettings_mgr.get_setting("default_country_code")
            if cc:
                utils.set_custom_region(cc)
            else:
                region = utils.detect_region()
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
            ("import-export", self.on_import_export_click),
            ("blocklist", self.on_blocklist_menu_clicked),
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
        main_section.append(_("Import / Export"), "menu.import-export")
        block_section = Gio.Menu()
        block_section.append(_("Blocklist"), "menu.blocklist")

        menu = Gio.Menu()
        menu.append_section(None, self._resolve_section)
        menu.append_section(None, main_section)
        menu.append_section(None, block_section)
        self.actions_btn.set_menu_model(menu)

    def _set_resolve_visible(self, visible):
        """Show or hide the duplicate resolution menu entry."""
        self._resolve_section.remove_all()
        if visible:
            self._resolve_section.append(_("Resolve Duplicates"), "menu.resolve-duplicates")

    def on_import_export_click(self, btn):
        """Open the import and export dialog."""
        ImportExportDialog(self).present()

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

    def _show_toast(self, message, timeout=5):
        """Show a toast and return it."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)
        return toast

    def notify_error(self, message):
        """Show a transient feedback toast, ending any loading toast."""
        self.hide_loading()
        self._show_toast(message)

    def notify_success(self, message):
        """Show a transient feedback toast, ending any loading toast."""
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

    def handle_new_message(self, sender, body, attachments=[], real_sender=None):
        """Handle new message injection into UI."""
        if not self.messages_view:
            return False

        return self.messages_view.handle_incoming_ui(sender, body, attachments, msg_sender=real_sender)

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

    def present_alert(self, dialog):
        """Present an alert, dismissing any alert already showing."""
        if self._active_alert is not None:
            self._active_alert.close()
        self._active_alert = dialog
        dialog.connect("closed", self._on_alert_closed)
        dialog.present(self)

    def _on_alert_closed(self, dialog):
        """Forget the tracked alert once it goes away."""
        if self._active_alert is dialog:
            self._active_alert = None

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
        self.present_alert(build_info_sheet(_("USSD Result"), text, selectable=True))

    def confirm_action(self, title, body, on_confirm):
        """Show a confirmation dialog."""
        d = Adw.AlertDialog(heading=title, body=body)
        d.add_response("cancel", _("Cancel"))
        d.add_response("yes", _("Confirm"))
        d.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE)

        def _cb(dialog, resp):
            if resp == "yes":
                on_confirm()
        d.connect("response", _cb)
        self.present_alert(d)

    def on_settings_click(self, btn):
        """Open the settings sheet."""
        SettingsWindow(self, self.eds, self.ofono).present(self)

    def on_duplicate_resolver_setting_changed(self, settings, key):
        """Handle toggle of duplicate resolver setting."""
        enabled = settings.get_boolean(key)
        if not enabled:
            self._set_resolve_visible(False)
            self.pending_conflicts = []
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
            toast = Adw.Toast.new(message)
            toast.set_button_label(_("Resolve Duplicates"))
            toast.connect("button-clicked", lambda t: GLib.idle_add(
                lambda: self.on_resolve_duplicates_clicked(None) or False))
            self.toast_overlay.add_toast(toast)
        else:
            self._set_resolve_visible(False)

    def on_resolve_duplicates_clicked(self, btn):
        """Handle resolve duplicates button click."""
        if not self.pending_conflicts:
            return
        win = DuplicateResolutionWindow(self.pending_conflicts, self.eds, self.on_resolution_done)
        win.present(self)

    def on_resolution_done(self):
        """Callback when duplicate resolution is done."""
        if self.contacts_view:
            self.contacts_view.check_duplicates()

    def on_blocklist_menu_clicked(self, btn):
        """Open blocklist manager."""
        if not self.eds.is_ready:
            self.notify_error(_("Contacts syncing..."))
            return

        sheet = Adw.Dialog(title=_("Blocklist"))
        sheet.set_content_width(360)
        sheet.set_content_height(500)

        view = BlocklistView(self.db, self)
        self.blocklist_view = view

        def _cleanup(*args):
            self.blocklist_view = None

        sheet.connect("closed", _cleanup)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(view)
        sheet.set_child(toolbar)
        sheet.present(self)

    def present_blocklist_editor(self, number_preset=None):
        """Open blocklist editor dialog."""
        if not self.eds.is_ready:
            self.notify_error(_("Contacts syncing..."))
            return

        name_preset = ""
        if number_preset:
            contact_name = self.eds.get_contact_name(number_preset)
            if contact_name:
                name_preset = contact_name
        BlocklistEditor(self.db, self.eds, self, number_preset=number_preset,
                        name_preset=name_preset).present(self)

    def on_force_sync_click(self, btn):
        """Force address book backends to sync, falling back to a local reload."""

        def task():
            refreshed = self.eds.refresh_backends()
            discovered = self.eds.sync_available_sources()
            return refreshed, discovered

        def done(result):
            refreshed, discovered = result
            if discovered:
                self._manual_sync_active = True
                return
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
            self.notify_error(_("Contacts syncing..."))
            return

        if contact_data is None and number_preset:
            results = self.eds.search_contacts(number_preset)
            if not results:
                pass
            elif len(results) == 1:
                contact_data = self.eds.cache.get(results[0][0])
            else:
                def build(group, sheet):
                    for res in results:
                        c_data = self.eds.cache.get(res[0])
                        if not c_data:
                            continue

                        name = c_data.get('name', _("Unknown"))
                        source_uid = c_data.get('source_uid')
                        source_name = _("Unknown Addressbook")
                        if source_uid and source_uid in self.eds.sources:
                            source_name = self.eds.sources[source_uid].get('name', source_name)

                        add_choice_row(group, sheet, name,
                                       lambda data=c_data: self.present_edit_contact(contact_data=data),
                                       subtitle=source_name)

                present_choice_sheet(self, _("Multiple Contacts Found"), build,
                                     description=_("Which contact would you like to edit?"))
                return

        win = ContactEditor(self.eds, self, contact_data, number_preset=number_preset)
        win.present(self)

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
            if self.ofono.audio.voice_profile_active:
                self.notify_error(_("Please wait, the previous call is still ending"))
            else:
                self.notify_error(_("Call Failed"))
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
        sheet = Adw.Dialog(title=_("Call Details"))
        sheet.set_content_width(360)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        sheet.set_child(toolbar)

        grp_info = Adw.PreferencesGroup()
        page.add(grp_info)
        for title, value in ((_("Number"), item.number), (_("Name"), item.name),
                             (_("Date"), item.full_ts), (_("Duration"), item.duration_str)):
            grp_info.add(Adw.ActionRow(title=title, subtitle=str(value)))

        grp_actions = Adw.PreferencesGroup()
        page.add(grp_actions)

        def add_action(label, callback, destructive=False, needs_eds=False):
            row = Adw.ActionRow(title=label, activatable=True)
            if destructive:
                row.add_css_class("error")
            if needs_eds and not self.eds.is_ready:
                row.set_sensitive(False)
            row.connect("activated", lambda r: GLib.idle_add(
                lambda: [sheet.close(), callback()] and False))
            grp_actions.add(row)

        blocked_list = self.db.get_blocked_numbers()
        is_blocked_id = None
        norm_item_num = normalize_number(item.number)
        for (bid, bnum, _ignored) in blocked_list:
            if normalize_number(bnum) == norm_item_num:
                is_blocked_id = bid
                break

        if is_blocked_id:
            def _unblock():
                self.db.unblock_number(is_blocked_id, item.number)
                self.notify_success(_("Unblocked"))
            add_action(_("Unblock Number"), _unblock, needs_eds=True)
        else:
            lbl = _("Edit Contact") if item.is_saved else _("Add to Contacts")
            add_action(lbl, lambda: self.present_edit_contact(number_preset=item.number), needs_eds=True)

            if not item.is_saved:
                add_action(_("Add to Existing Contact"), lambda: self.on_add_to_existing(item), needs_eds=True)
                add_action(_("Search Number"), lambda: self._search_number_online(item.number))

        add_action(_("Send Message"), lambda: self.present_chat(item.number))
        add_action(_("Copy Number"), lambda: self.copy_to_clipboard(item.number))

        if not is_blocked_id:
            add_action(_("Block Number"), lambda: self.present_blocklist_editor(number_preset=item.number),
                       destructive=True, needs_eds=True)

        add_action(_("Delete this call"),
                   lambda: self.confirm_action(_("Delete Call"), _("Remove this call?"),
                                               lambda: [self.db.delete_call_by_id(item.id)]),
                   destructive=True)

        sheet.present(self)

    def _search_number_online(self, number):
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

            run_in_background(self.eds.add_number_to_contact, uid, item.number, on_complete=done)

        picker = ContactPicker(
            self.eds,
            self,
            _cb,
            title=_("Add Number"),
            action_label=_("Save"),
            allow_custom_number=False,
            return_contact_uid=True
        )
        picker.present(self)
