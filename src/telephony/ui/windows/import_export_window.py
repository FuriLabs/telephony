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
import dbus
import tempfile
import shutil
import re
from gi.repository import Gtk, Adw, GLib
from loguru import logger
from gettext import gettext as _
from ...backend.utils.thread_utils import run_in_background
from .import_wizard_window import ImportWizardWindow
from ...backend.utils.importer_local_utils import import_local_chatty, import_local_calls
from ...backend.utils.importer_android_utils import import_android_sms, import_android_calls
from ...backend.utils.importer_ios_utils import import_ios_sms, import_ios_calls
from ...backend.utils.exporter_android_utils import export_android_sms, export_android_calls
from ...backend.utils.exporter_local_utils import export_linux_chatty, export_linux_calls, export_linux_telephony
from ...backend.utils.ios_extractor_utils import IOSBackupExtractor


class ImportExportDialog:
    """Dialog and logic for ETL style Import/Export."""

    def __init__(self, app_window):
        self.app_window = app_window
        self.db = app_window.db
        self.eds = app_window.eds

    def present(self):
        d = Adw.MessageDialog(heading=_("Import / Export"), body=_("Extract, transform, load (ETL).\nSelect data source or destination."))
        d.set_transient_for(self.app_window)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(20)
        box.set_margin_bottom(10)
        box.set_margin_start(20)
        box.set_margin_end(20)

        def make_btn(label, callback, style=None):
            b = Gtk.Button(label=label)
            if style:
                b.add_css_class(style)
            b.connect("clicked", lambda x: GLib.idle_add(lambda: [d.close(), callback()] and False))
            return b

        box.append(make_btn(_("Import Contacts from vCard"), self.on_import_clicked))
        box.append(make_btn(_("Export Contacts to vCard"), self.on_export_all_clicked))
        box.append(make_btn(_("Import Contacts from SIM card"), self.ask_import_sim))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=5, margin_bottom=5))

        box.append(make_btn(_("Import From local Chatty"), self.ask_import_chatty))
        box.append(make_btn(_("Import From local Calls"), self.ask_import_local_calls))
        box.append(make_btn(_("Import From Android"), self.ask_import_android))
        box.append(make_btn(_("Import From iOS"), self.ask_import_ios))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=5, margin_bottom=5))

        box.append(make_btn(_("Export Calls or Messages"), self.ask_export_data))

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: d.close() or False))
        box.append(btn_cancel)

        d.set_extra_child(box)
        d.present()

    def ask_import_sim(self):
        """Import contacts from SIM using org.ofono.Phonebook."""
        self.app_window.notify_loading(_("Importing contacts from SIM..."))

        def _task():
            try:
                bus = dbus.SystemBus()
                manager = dbus.Interface(bus.get_object('org.ofono', '/'), 'org.ofono.Manager')
                modems = manager.GetModems()
                if not modems:
                    GLib.idle_add(self.app_window.hide_loading)
                    GLib.idle_add(lambda: self.app_window.notify_error(_("No modem found for SIM import")))
                    return

                path = modems[0][0]
                phonebook = dbus.Interface(bus.get_object('org.ofono', path), 'org.ofono.Phonebook')
                vcard_data = phonebook.Import(timeout=100)

                if not vcard_data:
                    GLib.idle_add(self.app_window.hide_loading)
                    GLib.idle_add(lambda: self.app_window.notify_error(_("No contacts found on SIM card.")))
                    return

                vcards = re.findall(r'BEGIN:VCARD.*?END:VCARD', str(vcard_data), re.DOTALL)
                count = 0
                for vcard in vcards:
                    if self.eds.save_contact(vcard):
                        count += 1

                GLib.idle_add(self.app_window.hide_loading)
                GLib.idle_add(lambda: self.app_window.notify_success(_("Imported {count} contacts from SIM.").format(count=count)))
                GLib.idle_add(self.eds.reload)
            except Exception as e:
                logger.error(f"[ImportExport] SIM Import error: {e}")
                GLib.idle_add(self.app_window.hide_loading)
                GLib.idle_add(lambda err=e: self.app_window.notify_error(_("SIM Import failed: {e}").format(e=err)))

        run_in_background(_task)

    def ask_import_chatty(self):
        def on_wizard_done(db_path, mms_path):
            if db_path is False:
                return
            self.app_window.notify_loading(_("Importing Chatty..."))

            def task():
                success, msg = import_local_chatty(self.db, db_path, mms_path)
                GLib.idle_add(self.app_window.hide_loading)
                if success:
                    GLib.idle_add(lambda: self.app_window.notify_success(msg))

                else:
                    GLib.idle_add(lambda: self.app_window.notify_error(msg))
            run_in_background(task)

        win = ImportWizardWindow(self.app_window, "chatty", on_wizard_done)
        win.present()

    def ask_import_local_calls(self):
        def on_wizard_done(db_path, mms_path):
            if db_path is False:
                return
            self.app_window.notify_loading(_("Importing Calls..."))

            def task():
                success, msg = import_local_calls(self.db, db_path)
                GLib.idle_add(self.app_window.hide_loading)
                if success:
                    GLib.idle_add(lambda: self.app_window.notify_success(msg))

                else:
                    GLib.idle_add(lambda: self.app_window.notify_error(msg))
            run_in_background(task)

        win = ImportWizardWindow(self.app_window, "calls", on_wizard_done)
        win.present()

    def ask_import_android(self):
        d = Adw.MessageDialog(heading=_("Import From Android"), body=_("Select an option:"))
        d.set_transient_for(self.app_window)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=20, margin_bottom=10, margin_start=20, margin_end=20)

        def make_btn(label, callback):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda x: GLib.idle_add(lambda: [d.close(), callback()] and False))
            return b

        box.append(make_btn(_("Select SMS/MMS file"), lambda: self._open_file_chooser("android_sms")))
        box.append(make_btn(_("Select Call history file"), lambda: self._open_file_chooser("android_calls")))
        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: d.close() or False))
        box.append(btn_cancel)

        d.set_extra_child(box)
        d.present()

    def ask_import_ios(self):
        d = Adw.MessageDialog(heading=_("Import From iOS"), body=_("Select an option:"))
        d.set_transient_for(self.app_window)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=20, margin_bottom=10, margin_start=20, margin_end=20)

        def make_btn(label, callback, is_suggested=False):
            b = Gtk.Button(label=label)
            if is_suggested:
                b.add_css_class("suggested-action")
            b.connect("clicked", lambda x: GLib.idle_add(lambda: [d.close(), callback()] and False))
            return b

        btn_usb = make_btn(_("Direct USB Import (Recommended)"), self._start_ios_usb_import, True)
        box.append(btn_usb)

        box.append(make_btn(_("Select SMS/MMS file"), lambda: self._open_file_chooser("ios_sms")))
        box.append(make_btn(_("Select Call history file"), lambda: self._open_file_chooser("ios_calls")))

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: d.close() or False))
        box.append(btn_cancel)

        d.set_extra_child(box)
        d.present()

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
                    _ok, messages_msg = import_ios_sms(self.db, sms_path, manifest_path, tmp_dir)

                if calls_path and os.path.exists(calls_path):
                    GLib.idle_add(lambda: self.app_window.notify_loading(_("Importing iPhone Calls...")))
                    _ok, calls_msg = import_ios_calls(self.db, calls_path)

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
                success, msg = import_android_sms(self.db, file_path)
            elif import_type == "android_calls":
                success, msg = import_android_calls(self.db, file_path)
            elif import_type == "ios_sms":
                success, msg = import_ios_sms(self.db, file_path)
            elif import_type == "ios_calls":
                success, msg = import_ios_calls(self.db, file_path)

            GLib.idle_add(self.app_window.hide_loading)
            if success:
                GLib.idle_add(lambda: self.app_window.notify_success(msg))

            else:
                GLib.idle_add(lambda: self.app_window.notify_error(msg))

        run_in_background(task)

    def ask_export_data(self):
        d = Adw.MessageDialog(heading=_("Export Data"), body=_("Select destination format:"))
        d.set_transient_for(self.app_window)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=20, margin_bottom=10, margin_start=20, margin_end=20)

        def make_btn(label, callback):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda x: GLib.idle_add(lambda: [d.close(), callback()] and False))
            return b

        box.append(make_btn(_("Linux - Chatty/Calls"), lambda: self._show_export_type_chooser("linux_chatty_calls")))
        box.append(make_btn(_("Linux - Telephony"), lambda: self._show_export_type_chooser("linux_telephony")))
        box.append(make_btn(_("Android"), lambda: self._show_export_type_chooser("android")))
        box.append(make_btn(_("iOS"), lambda: self._show_export_type_chooser("ios")))

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: d.close() or False))
        box.append(btn_cancel)

        d.set_extra_child(box)
        d.present()

    def _show_export_type_chooser(self, dest_format):
        d = Adw.MessageDialog(heading=_("Export to {fmt}").format(fmt=dest_format), body=_("What do you want to export?"))
        d.set_transient_for(self.app_window)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=20, margin_bottom=10, margin_start=20, margin_end=20)

        if dest_format == "ios":
            info_lbl = Gtk.Label(
                label=_("Note: This export uses the Android format. When importing this file onto an iOS device, please use the \"Import From Android\" option."),
                wrap=True
            )
            info_lbl.add_css_class("dim-label")
            box.append(info_lbl)

        def make_btn(label, export_type):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda x: GLib.idle_add(lambda: [d.close(), self._open_export_file_chooser(dest_format, export_type)] and False))
            return b

        box.append(make_btn(_("Messages (SMS/MMS)"), "sms"))
        box.append(make_btn(_("Call History"), "calls"))

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: d.close() or False))
        box.append(btn_cancel)

        d.set_extra_child(box)
        d.present()

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
        sources = self.app_window.eds.get_sources_info()
        enabled_sources = [s for s in sources if s['enabled']]

        if not enabled_sources:
            self.app_window.notify_error(_("No enabled address books found"))
            return

        if len(enabled_sources) == 1:
            self._start_import(path, enabled_sources[0]['uid'])
            return

        d = Adw.MessageDialog(
            heading=_("Select Address Book"),
            body=_("Where do you want to import contacts?")
        )
        d.set_transient_for(self.app_window)
        d.add_response("cancel", _("Cancel"))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(24)
        box.set_margin_end(24)

        for s in enabled_sources:
            name = s['name']
            if len(name) > 30:
                name = name[:27] + "..."

            btn = Gtk.Button(label=name)
            btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda b, uid=s['uid']: GLib.idle_add(lambda: [d.close(), self._start_import(path, uid)] and False))
            box.append(btn)

        d.set_extra_child(box)
        d.present()

    def _start_import(self, path, source_uid):
        self.app_window.notify_loading(_("Importing..."))
        run_in_background(self._import_task, path, source_uid)

    def _import_task(self, path, source_uid=None):
        """Background import task."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            vcards = re.findall(r'BEGIN:VCARD.*?END:VCARD', content, re.DOTALL)
            count = 0
            for vcard in vcards:
                if self.app_window.eds.save_contact(vcard, source_uid=source_uid):
                    count += 1
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

        d = Adw.MessageDialog(
            heading=_("Export Contacts"),
            body=_("Which address book do you want to export?")
        )
        d.set_transient_for(self.app_window)
        d.add_response("cancel", _("Cancel"))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(20)
        box.set_margin_bottom(10)
        box.set_margin_start(20)
        box.set_margin_end(20)

        btn_all = Gtk.Button(label=_("Export All Address Books"))

        def _cb_all(b):
            d.close()
            self._show_export_file_chooser(None)
        btn_all.connect("clicked", lambda b: GLib.idle_add(lambda: _cb_all(b) or False))
        box.append(btn_all)

        for s in enabled_sources:
            name = s['name']
            if len(name) > 30:
                name = name[:27] + "..."

            btn = Gtk.Button(label=name)

            def _cb_specific(b, uid=s['uid']):
                d.close()
                self._show_export_file_chooser(uid)
            btn.connect("clicked", lambda b, uid=s['uid']: GLib.idle_add(lambda: _cb_specific(b, uid) or False))
            box.append(btn)

        d.set_extra_child(box)
        d.present()

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
            contacts = []
            with self.app_window.eds.cache_lock:
                if source_uid:
                    contacts = [c for c in self.app_window.eds.cache.values() if c.get('source_uid') == source_uid]
                else:
                    contacts = list(self.app_window.eds.cache.values())

            if not contacts:
                GLib.idle_add(lambda: self.app_window.notify_error(_("No contacts found to export")))
                return

            full_content = ""
            for c in contacts:
                v = c.get('vcard', '')
                if v:
                    full_content += v + "\n"

            temp_path = path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(full_content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)

            GLib.idle_add(lambda: self.app_window.notify_success(_("Exported {count} contacts").format(count=len(contacts))))
        except Exception as e:
            logger.error(f"[MainWindow] Export error: {e}")
            GLib.idle_add(lambda e=e: self.app_window.notify_error(_("Export error: {e}").format(e=e)))
