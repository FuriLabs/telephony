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
import subprocess
import urllib.parse
from .windows.import_export_window import ImportExportDialog
import sys
from gettext import gettext as _, ngettext

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango
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


class MainWindow(Adw.Window):
    """The main application window containing the stack of views (History, Dialpad, Messages, Contacts)."""

    def __init__(self, application, ofono_manager, db_manager, eds_manager, mms_manager=None, gsettings_mgr=None, show_calls=False, show_messages=False, show_contacts=False):
        self._unread_timer = None
        self._menu_actions = {}
        self._resolve_section = None
        self.in_error_mode = False
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


        self.overlay = Gtk.Overlay()
        self.set_content(self.overlay)

        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(main_vbox)

        header = Adw.HeaderBar()
        title_lbl = Gtk.Label(label=_("Telephony"), css_classes=["title"])
        header.set_title_widget(title_lbl)

        info_btn = Gtk.Button(icon_name="dialog-information-symbolic")
        info_btn.add_css_class("flat")
        info_btn.add_css_class("circular")
        info_btn.connect("clicked", lambda b: GLib.idle_add(lambda: InfoPage.show(self) or False))
        header.pack_start(info_btn)

        self.actions_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.setup_actions_menu()
        header.pack_end(self.actions_btn)
        main_vbox.append(header)

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

        self.stack.connect("notify::visible-child-name",
                           lambda *a: self._ensure_view(self.stack.get_visible_child_name()))
        self._ensure_view(self.stack.get_visible_child_name())

        self.stack.set_vexpand(True)
        main_vbox.append(self.stack)

        self.switcher = Adw.ViewSwitcherBar(stack=self.stack, reveal=True)
        main_vbox.append(self.switcher)

        self._notif_hide_id = None
        self.notif_revealer = Gtk.Revealer(valign=Gtk.Align.END, halign=Gtk.Align.CENTER)
        self.notif_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.notif_revealer.set_margin_bottom(60)

        self.notif_box = Gtk.Box(spacing=10)
        self.notif_box.add_css_class("app-notification")
        self.notif_box.set_halign(Gtk.Align.CENTER)

        self.notif_label = Gtk.Label(label="")
        self.notif_label.set_halign(Gtk.Align.CENTER)
        self.notif_label.set_wrap(True)
        self.notif_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.notif_label.set_max_width_chars(40)
        self.notif_label.set_natural_wrap_mode(Gtk.NaturalWrapMode.INHERIT)
        self.notif_box.append(self.notif_label)

        self.notif_revealer.set_child(self.notif_box)
        self.overlay.add_overlay(self.notif_revealer)

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
            self.notify_loading(_("Syncing Contacts..."), duration=0)
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
            missed_messages_dialog = MissedScheduledMessagesDialog(self)
            self.enqueue_popup(missed_messages_dialog.check_missed_scheduled_messages)

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
        self.overlay = None

        self.set_content(None)

    def check_own_number(self):
        """Check if own number is set, warn if not."""
        def _check():
            num = get_own_number()
            if not num:
                num = self.gsettings_mgr.get_setting("own_number")

            if not num:
                GLib.idle_add(lambda: self.show_top_notif(_("Set your number in Settings"), "notif-error", duration=0))
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
                    GLib.idle_add(lambda: self._dismiss_country_code_notif() or False)
                else:
                    GLib.idle_add(lambda: self.show_top_notif(_("Please set Default Country Code in Settings"), "notif-error", duration=0))
        run_in_background(_task)

    def _dismiss_country_code_notif(self):
        """Hide the country-code banner once detection has succeeded."""
        if self.notif_label.get_text() == _("Please set Default Country Code in Settings"):
            self._hide_top_notif()

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

            GLib.idle_add(lambda: self.show_top_notif(_("Setup Emergency Numbers in Settings"), "notif-error", duration=8))
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

    def show_top_notif(self, message, style_class, duration=3):
        """Show a temporary notification at the top of the window."""
        self.notif_label.set_text(message)
        self.notif_box.remove_css_class("notif-error")
        self.notif_box.remove_css_class("notif-success")
        self.notif_box.remove_css_class("notif-info")
        self.notif_box.add_css_class(style_class)
        self.notif_revealer.set_reveal_child(True)
        if self._notif_hide_id:
            GLib.source_remove(self._notif_hide_id)
            self._notif_hide_id = None
        if duration > 0:
            self._notif_hide_id = GLib.timeout_add_seconds(duration, self._hide_top_notif)

    def _hide_top_notif(self):
        """Hide the top notification banner."""
        self._notif_hide_id = None
        self.notif_revealer.set_reveal_child(False)
        return False

    def notify_error(self, message):
        """Helper to show error notification."""
        self.show_top_notif(f"{message}", "notif-error")

    def notify_success(self, message):
        """Helper to show success notification."""
        self.show_top_notif(f"{message}", "notif-success")

    def notify_loading(self, message, duration=10):
        """Helper to show loading notification."""
        self.show_top_notif(message, "notif-info", duration=duration)

    def hide_loading(self):
        """Hide the notification area."""
        self.notif_revealer.set_reveal_child(False)

    def on_contacts_loaded(self, *args):
        """Handle contacts loaded event."""
        if self.eds.is_ready:
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
        """Handle ofono status changes."""
        if status == 'connected':
            self.notify_success(_("Modem Connected"))
        elif status == 'error':
            self.notify_error(_("Modem Error: {message}").format(message=message))

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

    def handle_ussd(self, code):
        """Initiate a USSD request."""
        self.notify_loading(_("Sending USSD..."))

        def _task():
            if self.ofono:
                res = self.ofono.send_ussd(code)
                GLib.idle_add(self.hide_loading)
                if res:
                    GLib.idle_add(lambda: self.show_ussd_dialog(res))
            else:
                GLib.idle_add(self.hide_loading)
                GLib.idle_add(lambda: self.notify_error(_("Modem not ready")))
        run_in_background(_task)

    def show_ussd_dialog(self, text):
        """Show USSD response dialog."""
        d = Adw.MessageDialog(heading=_("USSD Result"), body=text)
        d.set_transient_for(self)
        d.add_response("ok", _("OK"))
        d.connect("response", lambda d, r: GLib.idle_add(lambda: d.close() or False))
        d.present()

    def confirm_action(self, title, body, on_confirm):
        """Show a confirmation dialog."""
        d = Adw.MessageDialog(heading=title, body=body)
        d.set_transient_for(self)
        d.add_response("cancel", _("Cancel"))
        d.add_response("yes", _("Confirm"))
        d.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE)

        def _cb(dialog, resp):
            GLib.idle_add(lambda: dialog.close() or False)
            if resp == "yes":
                on_confirm()
        d.connect("response", _cb)
        d.present()

    def on_settings_click(self, btn):
        """Open settings window."""
        win = SettingsWindow(self, self.eds, self.ofono)
        win.set_modal(False)
        win.present()

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
        resolver_enabled = self.gsettings_mgr.gsettings.get_boolean("duplicate-resolver-enabled")

        if resolver_enabled and count > 0:
            self._set_resolve_visible(True)
            message = ngettext(
                "Found {count} duplicate contact. Check menu.",
                "Found {count} duplicate contacts. Check menu.",
                count
            ).format(count=count)
            self.show_top_notif(message, "notif-info", duration=5)
        else:
            self._set_resolve_visible(False)

    def on_resolve_duplicates_clicked(self, btn):
        """Handle resolve duplicates button click."""
        if not self.pending_conflicts:
            return
        win = DuplicateResolutionWindow(self, self.pending_conflicts, self.eds, self.on_resolution_done)
        win.present()

    def on_resolution_done(self):
        """Callback when duplicate resolution is done."""
        if self.contacts_view:
            self.contacts_view.check_duplicates()

    def on_blocklist_menu_clicked(self, btn):
        """Open blocklist manager."""
        if not self.eds.is_ready:
            self.notify_loading(_("Contacts syncing..."))
            return

        win = Adw.Window(title=_("Blocklist"), transient_for=self)
        win.set_default_size(360, 500)
        win.set_modal(False)

        view = BlocklistView(self.db, self)
        self.blocklist_view = view

        def _cleanup(*args):
            self.blocklist_view = None
            return False

        win.connect("close-request", _cleanup)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        btn_close = Gtk.Button(label=_("Close"))
        btn_close.connect("clicked", lambda b: GLib.idle_add(lambda: win.close() or False))
        header.pack_end(btn_close)
        content.append(header)
        content.append(view)
        win.set_content(content)
        win.present()

    def present_blocklist_editor(self, number_preset=None):
        """Open blocklist editor dialog."""
        if not self.eds.is_ready:
            self.notify_loading("Contacts syncing...")
            return

        name_preset = ""
        if number_preset:
            contact_name = self.eds.get_contact_name(number_preset)
            if contact_name:
                name_preset = contact_name
        win = BlocklistEditor(self.db, self.eds, self, number_preset=number_preset, name_preset=name_preset)
        win.set_modal(False)
        win.present()

    def on_force_sync_click(self, btn):
        """Force address book backends to sync, falling back to a local reload."""
        self.notify_loading(_("Syncing address books..."))

        def task():
            refreshed = self.eds.refresh_backends()
            discovered = self.eds.sync_available_sources()
            return refreshed, discovered

        def done(result):
            refreshed, discovered = result
            if discovered:
                self.notify_loading(_("Reloading Contacts..."))
                return
            if refreshed:
                self.notify_success(_("Sync started for {count} address books").format(count=refreshed))
                return
            self.notify_loading(_("Reloading Contacts..."))
            self.eds.reload()

        def failed(error):
            logger.error(f"[MainWindow] Backend refresh failed: {error}")
            self.notify_loading(_("Reloading Contacts..."))
            self.eds.reload()

        run_in_background(task, on_complete=done, on_error=failed)

    def present_edit_contact(self, contact_data=None, number_preset=None):
        """Open contact editor."""
        if not self.eds.is_ready:
            self.notify_loading(_("Contacts syncing..."))
            return

        if contact_data is None and number_preset:
            results = self.eds.search_contacts(number_preset)
            if not results:
                pass
            elif len(results) == 1:
                contact_data = self.eds.cache.get(results[0][0])
            else:
                d = Adw.MessageDialog(transient_for=self, heading=_("Multiple Contacts Found"))
                d.set_body(_("Which contact would you like to edit?"))

                btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                btn_box.set_margin_top(15)

                for res in results:
                    uid = res[0]
                    c_data = self.eds.cache.get(uid)
                    if not c_data:
                        continue

                    name = c_data.get('name', _("Unknown"))
                    source_uid = c_data.get('source_uid')
                    source_name = _("Unknown Addressbook")
                    if source_uid and source_uid in self.eds.sources:
                        source_name = self.eds.sources[source_uid].get('name', source_name)

                    if len(name) > 25:
                        name = name[:22] + "..."
                    if len(source_name) > 30:
                        source_name = source_name[:27] + "..."

                    contact_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                    lbl_name = Gtk.Label(label=name)
                    lbl_name.set_halign(Gtk.Align.CENTER)
                    lbl_source = Gtk.Label(label=source_name)
                    lbl_source.set_halign(Gtk.Align.CENTER)
                    lbl_source.add_css_class("caption")
                    lbl_source.add_css_class("dim-label")

                    contact_box.append(lbl_name)
                    contact_box.append(lbl_source)

                    btn_select = Gtk.Button()
                    btn_select.set_child(contact_box)
                    btn_select.add_css_class("suggested-action")

                    def _select_cb(b, data=c_data):
                        GLib.idle_add(lambda: d.close() or False)
                        self.present_edit_contact(contact_data=data)

                    btn_select.connect("clicked", lambda b, data=c_data: GLib.idle_add(lambda: _select_cb(b, data=data) or False))
                    btn_box.append(btn_select)

                btn_cancel = Gtk.Button(label=_("Cancel"))
                btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: d.close() or False))
                btn_box.append(btn_cancel)

                d.set_extra_child(btn_box)
                d.present()
                return

        win = ContactEditor(self.eds, self, contact_data, number_preset=number_preset)
        win.set_transient_for(self)
        win.set_modal(False)
        win.present()

    def present_chat(self, number):
        """Open chat for a number."""
        if not self.show_messages_mode:
            logger.info("Messages mode not active. Launching Messages mode for chat.")
            subprocess.Popen([sys.executable, "-m", "telephony.main", "--open-chat", number])
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
                self.notify_error(_("Call Failed"))

        run_in_background(self.ofono.dial, number, on_complete=done, hide_id=hide_id)

    def show_call_details(self, item):
        """Show details dialog for a history item."""
        d = Adw.MessageDialog(transient_for=self, heading=_("Call Details"))
        body = (_("Number: {number}\nName: {name}\nDate: {date}\nDuration: {duration}")
                .format(number=item.number, name=item.name, date=item.full_ts, duration=item.duration_str))
        d.set_body(body)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        btn_box.set_margin_top(15)

        blocked_list = self.db.get_blocked_numbers()
        is_blocked_id = None
        norm_item_num = normalize_number(item.number)
        for (bid, bnum, _ignored) in blocked_list:
            if normalize_number(bnum) == norm_item_num:
                is_blocked_id = bid
                break

        if is_blocked_id:
            btn_unblock = Gtk.Button(label=_("Unblock Number"))
            btn_unblock.add_css_class("suggested-action")

            def _unblock_cb(b):
                GLib.idle_add(lambda: d.close() or False)
                self.db.unblock_number(is_blocked_id, item.number)
                self.notify_success(_("Unblocked"))
            btn_unblock.connect("clicked", lambda b: GLib.idle_add(lambda: _unblock_cb(b) or False))

            if not self.eds.is_ready:
                btn_unblock.set_sensitive(False)

            btn_box.append(btn_unblock)
        else:
            lbl = _("Edit Contact") if item.is_saved else _("Add to Contacts")
            btn_contact = Gtk.Button(label=lbl)
            btn_contact.add_css_class("suggested-action")
            btn_contact.connect("clicked", lambda b: GLib.idle_add(lambda: [self.present_edit_contact(number_preset=item.number), d.close()] and False))

            if not self.eds.is_ready:
                btn_contact.set_sensitive(False)

            btn_box.append(btn_contact)

            if not item.is_saved:
                btn_add_existing = Gtk.Button(label=_("Add to Existing Contact"))
                btn_add_existing.add_css_class("suggested-action")
                btn_add_existing.connect("clicked", lambda b: GLib.idle_add(lambda: [self.on_add_to_existing(item), d.close()] and False))

                if not self.eds.is_ready:
                    btn_add_existing.set_sensitive(False)

                btn_box.append(btn_add_existing)

                btn_search = Gtk.Button(label=_("Search Number"))
                btn_search.add_css_class("suggested-action")

                def _search_cb(b):
                    clean_num = item.number.replace("+", "")
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
                    d.close()
                btn_search.connect("clicked", lambda b: GLib.idle_add(lambda: _search_cb(b) or False))
                btn_box.append(btn_search)

        btn_sms = Gtk.Button(label=_("Send Message"))
        btn_sms.add_css_class("suggested-action")
        btn_sms.connect("clicked", lambda b: GLib.idle_add(lambda: [self.present_chat(item.number), d.close()] and False))
        btn_box.append(btn_sms)

        btn_copy = Gtk.Button(label=_("Copy Number"))
        btn_copy.connect("clicked", lambda b: GLib.idle_add(lambda: [self.copy_to_clipboard(item.number), d.close()] and False))
        btn_box.append(btn_copy)

        if not is_blocked_id:
            btn_block = Gtk.Button(label=_("Block Number"))
            btn_block.add_css_class("destructive-action")

            def _block_cb(b):
                GLib.idle_add(lambda: d.close() or False)
                self.present_blocklist_editor(number_preset=item.number)
            btn_block.connect("clicked", lambda b: GLib.idle_add(lambda: _block_cb(b) or False))

            if not self.eds.is_ready:
                btn_block.set_sensitive(False)

            btn_box.append(btn_block)

        btn_del = Gtk.Button(label=_("Delete this call"))
        btn_del.add_css_class("destructive-action")

        def _del_cb(b):
            GLib.idle_add(lambda: d.close() or False)
            self.confirm_action(_("Delete Call"), _("Remove this call?"), lambda: [self.db.delete_call_by_id(item.id)])
        btn_del.connect("clicked", lambda b: GLib.idle_add(lambda: _del_cb(b) or False))
        btn_box.append(btn_del)

        btn_close = Gtk.Button(label=_("Close"))
        btn_close.connect("clicked", lambda b: GLib.idle_add(lambda: d.close() or False))
        btn_box.append(btn_close)

        d.set_extra_child(btn_box)
        d.present()

    def copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        Gdk.Display.get_default().get_clipboard().set(text)
        self.notify_success(_("Number copied"))

    def on_add_to_existing(self, item):
        """Handle adding number to an existing contact."""
        def _cb(result):
            uid, name = result
            self.notify_loading(_("Saving contact..."))

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
        picker.present()
