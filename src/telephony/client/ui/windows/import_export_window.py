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

import os
import tempfile
import shutil
from gi.repository import Gtk, Adw, GLib
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _
from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.constants import SHEET_CONTENT_WIDTH
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

    def _on_flow_closed(self, _dialog):
        """Forget the flow once its sheet goes away."""
        self.nav_view = None
        self._sheet = None

    def _show_choices(self, title, entries, description=None):
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
        self.nav_view.push(page)

    def present(self):
        """Show the import and export flow in one navigable sheet."""
        if self.nav_view is None:
            self.nav_view = Adw.NavigationView()
            sheet = Adw.Dialog(title=_("Import and Export"))
            sheet.set_content_width(SHEET_CONTENT_WIDTH)
            sheet.set_content_height(FLOW_SHEET_HEIGHT)
            sheet.set_child(self.nav_view)
            sheet.connect("closed", self._on_flow_closed)
            self._sheet = sheet
            sheet.present(self.app_window)

        self._show_choices(_("Import and Export"), [
            (_("Import Contacts from vCard"), self.on_import_clicked, None),
            (_("Export Contacts to vCard"), self.on_export_all_clicked, None),
            (_("Import Contacts from SIM card"), self.ask_import_sim, None),
            (_("Import From local Chatty"), self.ask_import_chatty, None),
            (_("Import From local Calls"), self.ask_import_local_calls, None),
            (_("Import From Android"), self.ask_import_android, None),
            (_("Import From iOS"), self.ask_import_ios, None),
            (_("Export Calls or Messages"), self.ask_export_data, None),
        ])

    def ask_import_sim(self):
        """Ask where the SIM contacts should land before reading them."""
        self._prompt_target_book(self._import_sim_to)

    def _import_sim_to(self, source_uid):
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

        self.nav_view.push(ImportWizardWindow(self.app_window, "chatty", on_wizard_done))

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

        self.nav_view.push(ImportWizardWindow(self.app_window, "calls", on_wizard_done))

    def ask_import_android(self):
        """Show the Android import choices."""
        self._show_choices(_("Import From Android"), [
            (_("Select SMS/MMS file"), lambda: self._open_file_chooser("android_sms"), None),
            (_("Select Call history file"), lambda: self._open_file_chooser("android_calls"), None),
        ])

    def ask_import_ios(self):
        """Show the iOS import choices."""
        self._show_choices(_("Import From iOS"), [
            (_("Direct USB Import (Recommended)"), self._start_ios_usb_import, None),
            (_("Select SMS/MMS file"), lambda: self._open_file_chooser("ios_sms"), None),
            (_("Select Call history file"), lambda: self._open_file_chooser("ios_calls"), None),
        ])

    def _start_ios_usb_import(self):
        run_in_background(IOSBackupExtractor.check_connection, on_complete=self._on_ios_connection_checked)

    def _on_ios_connection_checked(self, result):
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

    def _open_file_chooser(self, file_type):
        action = Gtk.FileChooserAction.OPEN
        dialog = Gtk.FileChooserNative(title=_("Select File"), transient_for=self.app_window, action=action)
        if "xml" in file_type or "android" in file_type:
            filter_ext = Gtk.FileFilter()
            filter_ext.set_name(_("XML Files"))
            filter_ext.add_pattern("*.xml")
            dialog.add_filter(filter_ext)

        def _on_resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                self._run_import_task(file_type, f.get_path())
            GLib.idle_add(lambda: d.destroy() or False)

        dialog.connect("response", _on_resp)
        dialog.show()

    def _run_import_task(self, import_type, file_path):
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
        self._show_choices(_("Export Data"), [
            (_("Linux - Chatty/Calls"), lambda: self._show_export_type_chooser("linux_chatty_calls"), None),
            (_("Linux - Telephony"), lambda: self._show_export_type_chooser("linux_telephony"), None),
            (_("Android"), lambda: self._show_export_type_chooser("android"), None),
            (_("iOS"), lambda: self._show_export_type_chooser("ios"), None),
        ])

    def _show_export_type_chooser(self, dest_format):
        """Show the export content choices for a destination format."""
        description = None
        if dest_format == "ios":
            description = _("Note: This export uses the Android format. When importing this file onto an iOS device, please use the \"Import From Android\" option.")

        self._show_choices(_("Export to {fmt}").format(fmt=dest_format), [
            (_("Messages (SMS/MMS)"), lambda: self._open_export_file_chooser(dest_format, "sms"), None),
            (_("Call History"), lambda: self._open_export_file_chooser(dest_format, "calls"), None),
        ], description=description)

    def _open_export_file_chooser(self, dest_format, export_type):
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

        def _on_resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                self._run_export_task(dest_format, export_type, f.get_path())
            GLib.idle_add(lambda: d.destroy() or False)

        dialog.connect("response", _on_resp)
        dialog.show()

    def _run_export_task(self, dest_format, export_type, file_path):
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
            self._prompt_import_source(f.get_path())
        GLib.idle_add(lambda: dialog.destroy() or False)

    def _prompt_import_source(self, path):
        """Prompt user for target address book."""
        self._prompt_target_book(lambda uid: self._start_import(path, uid))

    def _prompt_target_book(self, on_chosen):
        """Ask which address book an import should write to.

        The choice is skipped when there is nothing to choose, the
        default book leads so the common answer is the first one, and
        read-only books never appear because the daemon would refuse
        the write anyway.
        """
        sources = self.app_window.eds.get_sources_info()
        read_only_uids = self.app_window.eds.read_only_source_uids()
        enabled_sources = [s for s in sources if s['enabled'] and s['uid'] not in read_only_uids]

        if not enabled_sources:
            self.app_window.notify_error(_("No enabled address books found"))
            return

        if len(enabled_sources) == 1:
            on_chosen(enabled_sources[0]['uid'])
            return

        enabled_sources.sort(key=lambda s: not s.get('is_system_default'))

        self._show_choices(_("Select Address Book"), [
            (source['name'], (lambda uid=source['uid']: on_chosen(uid)),
             _("Default") if source.get('is_system_default') else None)
            for source in enabled_sources
        ], description=_("Where do you want to import contacts?"))

    def _start_import(self, path, source_uid):
        self.app_window.notify_loading(_("Importing..."))
        run_in_background(self._import_task, path, source_uid)

    def _import_task(self, path, source_uid=None):
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
        self._prompt_export_source()

    def _prompt_export_source(self):
        """Prompt user for which address book to export (or All)."""
        sources = self.app_window.eds.get_sources_info()
        enabled_sources = [s for s in sources if s['enabled']]

        if not enabled_sources:
            self.app_window.notify_error(_("No enabled address books found"))
            return

        enabled_sources.sort(key=lambda s: not s.get('is_system_default'))

        self._show_choices(_("Export Contacts"), [
            (_("Export All Address Books"), lambda: self._show_export_file_chooser(None), None)
        ] + [
            (source['name'], (lambda uid=source['uid']: self._show_export_file_chooser(uid)),
             _("Default") if source.get('is_system_default') else None)
            for source in enabled_sources
        ], description=_("Which address book do you want to export?"))

    def _show_export_file_chooser(self, source_uid):
        """Show file chooser for export location."""
        dialog = Gtk.FileChooserNative(title=_("Export Contacts"), transient_for=self.app_window, action=Gtk.FileChooserAction.SAVE)
        name_suffix = "all" if not source_uid else "filtered"
        dialog.set_current_name(f"contacts_backup_{name_suffix}.vcf")

        def _on_resp(d, r):
            self.on_export_file_response(d, r, source_uid)

        dialog.connect("response", _on_resp)
        dialog.show()

    def on_export_file_response(self, dialog, response, source_uid):
        """Handle export file selection response."""
        if response == Gtk.ResponseType.ACCEPT:
            f = dialog.get_file()
            run_in_background(self._export_all_task, f.get_path(), source_uid)
        GLib.idle_add(lambda: dialog.destroy() or False)

    def _export_all_task(self, path, source_uid=None):
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
