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

import json
import os
import tempfile
import shutil
from gi.repository import Gtk, Adw, GLib
from telephony.client.ui.widgets.common_widget import (present_sheet, on_sheet_closed,
                                                      sheet_navigation)
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _
from telephony.shared.utils.thread_utils import run_in_background
from telephony.client.ui.windows.import_wizard_window import ImportWizardWindow
from telephony.client.utils.exporter_android_utils import export_android_sms, export_android_calls
from telephony.client.utils.exporter_local_utils import (export_linux_chatty, export_linux_calls, export_linux_telephony)
from telephony.client.utils.ios_extractor_utils import IOSBackupExtractor


FLOW_SHEET_HEIGHT = 560


class ImportExportDialog:
    """Dialog and logic for ETL style Import/Export."""

    def __init__(self, app_window, nav_view=None):
        self.app_window = app_window
        self.db = app_window.db
        self.eds = app_window.eds
        self.nav_view = nav_view
        self._sheet = None
        self.mode_calls = bool(app_window.show_calls_mode)
        self.mode_messages = bool(app_window.show_messages_mode)
        self.mode_contacts = bool(app_window.show_contacts_mode)

    def on_flow_closed(self, _dialog):
        """Forget the flow once its sheet goes away."""
        self.nav_view = None
        self._sheet = None

    def push_page(self, page):
        """Push a page, which takes the focus rather than its contents.

        A text field taking it brings the keyboard up with the page,
        and a page is opened to be read before it is typed into.
        """
        page.set_focusable(True)
        self.nav_view.push(page)

    def show_choices(self, title, entries, description=None):
        """Show a step of the flow, as a page when a navigation hosts it.

        Inside settings the steps are pushed pages, so the back button
        and the back gesture walk the flow; without a navigation the
        same choices open as a sheet.
        """
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
        page_content = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        if description:
            group.set_description(description)
        for label, callback, subtitle in entries:
            row = Adw.ActionRow(title=label, activatable=True)
            if subtitle:
                row.set_subtitle(subtitle)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda r, cb=callback: GLib.idle_add(lambda: cb() or False))
            group.add(row)
        page_content.add(group)
        view.set_content(page_content)

        page = Adw.NavigationPage(title=title)
        page.set_child(view)
        self.push_page(page)

    def present(self):
        """Show the import and export flow in one navigable sheet.

        The navigation is remembered so the flow can be reopened where
        it was, but the sheet may have moved on to something else since,
        and pushing onto a navigation that is no longer in the window
        adds pages nobody will ever see.
        """
        showing = sheet_navigation(self.app_window.sheet_host.get_sheet())
        if self.nav_view is not None and self.nav_view is not showing:
            self.nav_view = None

        if self.nav_view is None:
            self.nav_view = Adw.NavigationView()
            self.nav_view.set_size_request(-1, FLOW_SHEET_HEIGHT)
            self._sheet = self.app_window
            present_sheet(self.app_window, self.nav_view)
            on_sheet_closed(self.app_window, lambda: self.on_flow_closed(None))

        rows = []
        if self.mode_contacts:
            rows.append((_("Import Contacts from vCard"), self.on_import_clicked, None))
            rows.append((_("Export Contacts to vCard"), self.on_export_all_clicked, None))
            rows.append((_("Import Contacts from SIM card"), self.ask_import_sim, None))
        if self.mode_messages:
            rows.append((_("Import From local Chatty"), self.ask_import_chatty, None))
        if self.mode_calls:
            rows.append((_("Import From local Calls"), self.ask_import_local_calls, None))
        if self.mode_calls or self.mode_messages:
            rows.append((_("Import From Android"), self.ask_import_android, None))
            rows.append((_("Import From iOS"), self.ask_import_ios, None))
            if self.mode_calls and self.mode_messages:
                export_title = _("Export Calls or Messages")
            elif self.mode_calls:
                export_title = _("Export Call History")
            else:
                export_title = _("Export Messages")
            rows.append((export_title, self.ask_export_data, None))
        rows.append((_("Export Blocklist"), self.on_export_blocklist, None))
        rows.append((_("Import Blocklist"), self.on_import_blocklist, None))
        self.show_choices(_("Import and Export"), rows)

    def on_export_blocklist(self):
        """Save the blocklist as a JSON file."""
        dialog = Gtk.FileChooserNative(title=_("Export File"), transient_for=self.app_window,
                                       action=Gtk.FileChooserAction.SAVE)
        dialog.set_current_name("blocklist.json")

        def on_resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                path = d.get_file().get_path()

                def task():
                    entries = self.db.get_blocked_numbers()
                    for entry in entries:
                        entry.pop("id", None)
                    with open(path, "w") as handle:
                        json.dump(entries, handle, indent=2)
                    return len(entries)

                run_in_background(task, on_complete=lambda n: self.app_window.notify_success(
                    _("Exported {count} entries.").format(count=n)),
                    on_error=lambda e: self.app_window.notify_error(_("Export failed.")))
            GLib.idle_add(lambda: d.destroy() or False)

        dialog.connect("response", on_resp)
        dialog.show()

    def on_import_blocklist(self):
        """Merge a blocklist JSON file; importing only ever adds or widens."""
        dialog = Gtk.FileChooserNative(title=_("Select File"), transient_for=self.app_window,
                                       action=Gtk.FileChooserAction.OPEN)

        def on_resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                path = d.get_file().get_path()

                def task():
                    with open(path) as handle:
                        payload = handle.read()
                    json.loads(payload)
                    return self.app_window.daemon.import_blocklist(payload)

                def done(result):
                    added, updated = result
                    self.app_window.notify_success(
                        _("Imported: {added} added, {updated} widened.").format(
                            added=added, updated=updated))

                run_in_background(task, on_complete=done,
                                  on_error=lambda e: self.app_window.notify_error(_("Import failed.")))
            GLib.idle_add(lambda: d.destroy() or False)

        dialog.connect("response", on_resp)
        dialog.show()

    def ask_import_sim(self):
        """Ask where the SIM contacts should land before reading them."""
        self.prompt_target_book(self.import_sim_to)

    def import_sim_to(self, source_uid):
        """Import contacts from the SIM phonebook through the daemon."""
        self.app_window.notify_loading(_("Importing contacts from SIM..."))

        def done(reply):
            self.app_window.hide_loading()
            if reply is None:
                self.app_window.notify_error(_("SIM Import failed: {e}").format(e="no reply"))
                return
            count, message = reply
            if count < 0:
                self.app_window.notify_error(_("SIM Import failed: {e}").format(e=message))
                return
            if count == 0:
                self.app_window.notify_error(_("No contacts found on SIM card."))
                return
            self.app_window.notify_success(_("Imported {count} contacts from SIM.").format(count=count))
            self.eds.reload()

        run_in_background(lambda: self.app_window.daemon.import_sim_contacts(source_uid), on_complete=done)

    def ask_import_chatty(self):
        def on_wizard_done(db_path, mms_path):
            if db_path is False:
                return
            self.app_window.notify_loading(_("Importing Chatty..."))

            def task():
                success, msg = self.app_window.daemon.import_chatty(db_path, mms_path)
                GLib.idle_add(self.app_window.hide_loading)
                if success:
                    GLib.idle_add(lambda: self.app_window.notify_success(msg))

                else:
                    GLib.idle_add(lambda: self.app_window.notify_error(msg))
            run_in_background(task)

        self.push_page(ImportWizardWindow(self.app_window, "chatty", on_wizard_done))

    def ask_import_local_calls(self):
        def on_wizard_done(db_path, mms_path):
            if db_path is False:
                return
            self.app_window.notify_loading(_("Importing Calls..."))

            def task():
                success, msg = self.app_window.daemon.import_local_calls(db_path)
                GLib.idle_add(self.app_window.hide_loading)
                if success:
                    GLib.idle_add(lambda: self.app_window.notify_success(msg))

                else:
                    GLib.idle_add(lambda: self.app_window.notify_error(msg))
            run_in_background(task)

        self.push_page(ImportWizardWindow(self.app_window, "calls", on_wizard_done))

    def ask_import_android(self):
        """Show the Android import choices."""
        rows = []
        if self.mode_messages:
            rows.append((_("Select SMS/MMS file"), lambda: self.open_file_chooser("android_sms"), None))
        if self.mode_calls:
            rows.append((_("Select Call history file"), lambda: self.open_file_chooser("android_calls"), None))
        self.show_choices(_("Import From Android"), rows)

    def ask_import_ios(self):
        """Show the iOS import choices."""
        rows = [(_("Direct USB Import (Recommended)"), self.start_ios_usb_import, None)]
        if self.mode_messages:
            rows.append((_("Select SMS/MMS file"), lambda: self.open_file_chooser("ios_sms"), None))
        if self.mode_calls:
            rows.append((_("Select Call history file"), lambda: self.open_file_chooser("ios_calls"), None))
        self.show_choices(_("Import From iOS"), rows)

    def start_ios_usb_import(self):
        run_in_background(IOSBackupExtractor.check_connection, on_complete=self.on_ios_connection_checked)

    def on_ios_connection_checked(self, result):
        """Continue the USB import once the device probe finishes."""
        connected, trusted, _detail = result if result else (False, False, "")
        if not connected:
            self.app_window.notify_error(_("No iOS device detected. Please connect your iPhone via USB."))
            return
        if not trusted:
            self.app_window.notify_error(_("Device not trusted. Please unlock your iPhone and tap 'Trust'."))
            return

        self.app_window.notify_loading(_("Extracting backup from iPhone... This may take a long time!"))

        def task():
            tmp_dir = tempfile.mkdtemp(prefix="ios_backup_")
            try:
                success, error = IOSBackupExtractor.run_backup(tmp_dir)
                if not success:
                    GLib.idle_add(self.app_window.hide_loading)
                    GLib.idle_add(lambda err=error: self.app_window.notify_error(_("USB Backup failed: {e}").format(e=err)))
                    return

                sms_path, calls_path, manifest_path = IOSBackupExtractor.find_databases(tmp_dir)

                messages_msg = _("No SMS database found in backup.")
                calls_msg = _("No Call History database found in backup.")

                if sms_path and os.path.exists(sms_path):
                    GLib.idle_add(lambda: self.app_window.notify_loading(_("Importing iPhone SMS...")))
                    _ok, messages_msg = self.app_window.daemon.import_ios_sms(sms_path, manifest_path, tmp_dir)

                if calls_path and os.path.exists(calls_path):
                    GLib.idle_add(lambda: self.app_window.notify_loading(_("Importing iPhone Calls...")))
                    _ok, calls_msg = self.app_window.daemon.import_ios_calls(calls_path)

                GLib.idle_add(self.app_window.hide_loading)
                GLib.idle_add(lambda msg=f"{messages_msg}\n{calls_msg}": self.app_window.notify_success(msg))

            except Exception as e:
                logger.error(f"[IOS Import] Exception: {e}")
                GLib.idle_add(self.app_window.hide_loading)
                GLib.idle_add(lambda err=e: self.app_window.notify_error(_("Error during iOS import: {e}").format(e=err)))
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        run_in_background(task)

    def open_file_chooser(self, file_type):
        action = Gtk.FileChooserAction.OPEN
        dialog = Gtk.FileChooserNative(title=_("Select File"), transient_for=self.app_window, action=action)
        if "xml" in file_type or "android" in file_type:
            filter_ext = Gtk.FileFilter()
            filter_ext.set_name(_("XML Files"))
            filter_ext.add_pattern("*.xml")
            dialog.add_filter(filter_ext)

        def on_resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                self.run_import_task(file_type, f.get_path())
            GLib.idle_add(lambda: d.destroy() or False)

        dialog.connect("response", on_resp)
        dialog.show()

    def run_import_task(self, import_type, file_path):
        self.app_window.notify_loading(_("Importing..."))

        def task():
            success = False
            msg = ""
            if import_type == "android_sms":
                success, msg = self.app_window.daemon.import_android_sms(file_path)
            elif import_type == "android_calls":
                success, msg = self.app_window.daemon.import_android_calls(file_path)
            elif import_type == "ios_sms":
                success, msg = self.app_window.daemon.import_ios_sms(file_path)
            elif import_type == "ios_calls":
                success, msg = self.app_window.daemon.import_ios_calls(file_path)

            GLib.idle_add(self.app_window.hide_loading)
            if success:
                GLib.idle_add(lambda: self.app_window.notify_success(msg))

            else:
                GLib.idle_add(lambda: self.app_window.notify_error(msg))

        run_in_background(task)

    def ask_export_data(self):
        """Show the export destination choices."""
        self.show_choices(_("Export Data"), [
            (_("Linux - Chatty/Calls"), lambda: self.show_export_type_chooser("linux_chatty_calls"), None),
            (_("Linux - Telephony"), lambda: self.show_export_type_chooser("linux_telephony"), None),
            (_("Android"), lambda: self.show_export_type_chooser("android"), None),
            (_("iOS"), lambda: self.show_export_type_chooser("ios"), None),
        ])

    def show_export_type_chooser(self, dest_format):
        """Show the export content choices for a destination format."""
        description = None
        if dest_format == "ios":
            description = _("Note: This export uses the Android format. When importing this file onto an iOS device, please use the \"Import From Android\" option.")

        rows = []
        if self.mode_messages:
            rows.append((_("Messages (SMS/MMS)"), lambda: self.open_export_file_chooser(dest_format, "sms"), None))
        if self.mode_calls:
            rows.append((_("Call History"), lambda: self.open_export_file_chooser(dest_format, "calls"), None))
        if len(rows) == 1:
            rows[0][1]()
            return
        self.show_choices(_("Export to {fmt}").format(fmt=dest_format), rows, description=description)

    def open_export_file_chooser(self, dest_format, export_type):
        dialog = Gtk.FileChooserNative(title=_("Export File"), transient_for=self.app_window, action=Gtk.FileChooserAction.SAVE)

        default_name = f"export_{export_type}"
        if dest_format == "android":
            default_name += ".xml"
        elif dest_format == "linux_telephony":
            default_name += ".db"
        elif dest_format == "linux_chatty_calls":
            default_name += ".db"
        elif dest_format == "ios":
            default_name += ".db"
        else:
            default_name += ".bak"

        dialog.set_current_name(default_name)

        def on_resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                self.run_export_task(dest_format, export_type, f.get_path())
            GLib.idle_add(lambda: d.destroy() or False)

        dialog.connect("response", on_resp)
        dialog.show()

    def run_export_task(self, dest_format, export_type, file_path):
        self.app_window.notify_loading(_("Exporting..."))

        def task():
            success = False
            msg = ""

            if dest_format == "android":
                if export_type == "sms":
                    success, msg = export_android_sms(self.db, file_path)
                else:
                    success, msg = export_android_calls(self.db, file_path)
            elif dest_format == "linux_chatty_calls":
                if export_type == "sms":
                    success, msg = export_linux_chatty(self.db, file_path)
                else:
                    success, msg = export_linux_calls(self.db, file_path)
            elif dest_format == "linux_telephony":
                success, msg = export_linux_telephony(self.db, file_path, is_messages=(export_type == "sms"))
            elif dest_format == "ios":
                if export_type == "sms":
                    success, msg = export_android_sms(self.db, file_path)
                else:
                    success, msg = export_android_calls(self.db, file_path)

            GLib.idle_add(self.app_window.hide_loading)
            if success:
                GLib.idle_add(lambda: self.app_window.notify_success(msg))
            else:
                GLib.idle_add(lambda: self.app_window.notify_error(msg))

        run_in_background(task)

    def on_import_clicked(self, btn=None):
        """Handle Import Click."""
        dialog = Gtk.FileChooserNative(title=_("Import VCard"), transient_for=self.app_window, action=Gtk.FileChooserAction.OPEN)
        filter_vcf = Gtk.FileFilter()
        filter_vcf.set_name(_("VCard Files"))
        filter_vcf.add_pattern("*.vcf")
        dialog.add_filter(filter_vcf)
        dialog.connect("response", self.on_import_response)
        dialog.show()

    def on_import_response(self, dialog, response):
        """Handle import dialog response."""
        if response == Gtk.ResponseType.ACCEPT:
            f = dialog.get_file()
            self.prompt_import_source(f.get_path())
        GLib.idle_add(lambda: dialog.destroy() or False)

    def prompt_import_source(self, path):
        """Prompt user for target address book."""
        self.prompt_target_book(lambda uid: self.start_import(path, uid))

    def prompt_target_book(self, on_chosen):
        """Ask which address book an import should write to.

        The choice is skipped when there is nothing to choose, the
        default book leads so the common answer is the first one, and
        read-only books never appear because the daemon would refuse
        the write anyway.
        """
        self.app_window.eds.sources_info_async(
            lambda sources: self.choose_target_book(sources, on_chosen))

    def choose_target_book(self, sources, on_chosen):
        """Ask for the book now that the list is known."""
        read_only_uids = self.app_window.eds.read_only_source_uids()
        enabled_sources = [s for s in sources if s['enabled'] and s['uid'] not in read_only_uids]

        if not enabled_sources:
            self.app_window.notify_error(_("No enabled address books found"))
            return

        if len(enabled_sources) == 1:
            on_chosen(enabled_sources[0]['uid'])
            return

        enabled_sources.sort(key=lambda s: not s.get('is_system_default'))

        self.show_choices(_("Select Address Book"), [
            (source['name'], (lambda uid=source['uid']: on_chosen(uid)),
             _("Default") if source.get('is_system_default') else None)
            for source in enabled_sources
        ], description=_("Where do you want to import contacts?"))

    def start_import(self, path, source_uid):
        self.app_window.notify_loading(_("Importing..."))
        run_in_background(self.import_task, path, source_uid)

    def import_task(self, path, source_uid=None):
        """Background import task."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            count = self.app_window.daemon.import_contacts(content, source_uid)
            GLib.idle_add(self.app_window.hide_loading)
            GLib.idle_add(lambda: self.app_window.notify_success(_("Imported {count} contacts").format(count=count)))
        except Exception as e:
            logger.error(f"[MainWindow] Import failed: {e}")
            GLib.idle_add(self.app_window.hide_loading)
            GLib.idle_add(lambda e=e: self.app_window.notify_error(_("Import failed: {e}").format(e=str(e))))

    def on_export_all_clicked(self, btn=None):
        """Handle Export Click."""
        self.prompt_export_source()

    def prompt_export_source(self):
        """Prompt user for which address book to export (or All)."""
        self.app_window.eds.sources_info_async(self.choose_export_source)

    def choose_export_source(self, sources):
        """Offer the books now that the list is known."""
        enabled_sources = [s for s in sources if s['enabled']]

        if not enabled_sources:
            self.app_window.notify_error(_("No enabled address books found"))
            return

        enabled_sources.sort(key=lambda s: not s.get('is_system_default'))

        self.show_choices(_("Export Contacts"), [
            (_("Export All Address Books"), lambda: self.show_export_file_chooser(None), None)
        ] + [
            (source['name'], (lambda uid=source['uid']: self.show_export_file_chooser(uid)),
             _("Default") if source.get('is_system_default') else None)
            for source in enabled_sources
        ], description=_("Which address book do you want to export?"))

    def show_export_file_chooser(self, source_uid):
        """Show file chooser for export location."""
        dialog = Gtk.FileChooserNative(title=_("Export Contacts"), transient_for=self.app_window, action=Gtk.FileChooserAction.SAVE)
        name_suffix = "all" if not source_uid else "filtered"
        dialog.set_current_name(f"contacts_backup_{name_suffix}.vcf")

        def on_resp(d, r):
            self.on_export_file_response(d, r, source_uid)

        dialog.connect("response", on_resp)
        dialog.show()

    def on_export_file_response(self, dialog, response, source_uid):
        """Handle export file selection response."""
        if response == Gtk.ResponseType.ACCEPT:
            f = dialog.get_file()
            run_in_background(self.export_all_task, f.get_path(), source_uid)
        GLib.idle_add(lambda: dialog.destroy() or False)

    def export_all_task(self, path, source_uid=None):
        """Background export task."""
        try:
            vcards = self.app_window.db.get_all_vcards(source_uid)

            if not vcards:
                GLib.idle_add(lambda: self.app_window.notify_error(_("No contacts found to export")))
                return

            full_content = "".join(v + "\n" for v in vcards)

            temp_path = path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(full_content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)

            GLib.idle_add(lambda: self.app_window.notify_success(_("Exported {count} contacts").format(count=len(vcards))))
        except Exception as e:
            logger.error(f"[MainWindow] Export error: {e}")
            GLib.idle_add(lambda e=e: self.app_window.notify_error(_("Export error: {e}").format(e=e)))
