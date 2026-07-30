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

from gi.repository import Gtk, Adw, GLib
from loguru import logger
from gettext import gettext as _
from ...backend.utils.thread_utils import run_in_background
import os


class DataManagementDialog:
    """Dialog and logic for clearing data from the application."""

    def __init__(self, app_window):
        self.app_window = app_window
        self.db = app_window.db
        self.eds = app_window.eds

    def present(self):
        d = Adw.MessageDialog(heading=_("Database Management"), body=_("Select data to permanently delete."))
        d.set_transient_for(self.app_window)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(20)
        box.set_margin_bottom(10)
        box.set_margin_start(20)
        box.set_margin_end(20)

        def make_red_button(label, callback):
            b = Gtk.Button(label=label)
            b.add_css_class("destructive-action")
            b.connect("clicked", lambda x: GLib.idle_add(lambda: [d.close(), callback()] and False))
            return b

        box.append(make_red_button(_("Delete Call History"), self.ask_clear_history))
        box.append(make_red_button(_("Delete All Messages"), self.ask_clear_messages))
        box.append(make_red_button(_("Delete All Group Names"), self.ask_clear_groups))
        box.append(make_red_button(_("Delete Blocklist"), self.ask_clear_blocklist))
        box.append(make_red_button(_("Delete All Contacts"), self.ask_clear_contacts))
        box.append(make_red_button(_("Delete Address Book"), self.ask_delete_addressbook))


        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(10)
        sep.set_margin_bottom(10)
        box.append(sep)

        btn_all = Gtk.Button(label=_("DELETE EVERYTHING"))
        btn_all.add_css_class("destructive-action")
        btn_all.connect("clicked", lambda x: GLib.idle_add(lambda: [d.close(), self.ask_clear_everything()] and False))
        box.append(btn_all)

        btn_close = Gtk.Button(label=_("Close"))
        btn_close.connect("clicked", lambda b: GLib.idle_add(lambda: d.close() or False))
        box.append(btn_close)

        d.set_extra_child(box)
        d.present()

    def _confirm_destructive(self, title, body, on_confirm):
        """Show destructive action confirmation."""
        d = Adw.MessageDialog(heading=title, body=body)
        d.set_transient_for(self.app_window)
        d.add_response("cancel", _("Cancel"))
        d.add_response("delete", _("Delete"))
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def _cb(dialog, resp):
            GLib.idle_add(lambda: dialog.close() or False)
            if resp == "delete":
                on_confirm()

        d.connect("response", _cb)
        d.present()

    def ask_clear_history(self):
        """Ask to clear history."""
        self._confirm_destructive(_("Delete Call History"), _("Are you sure? This cannot be undone."), self._do_clear_history)

    def ask_clear_messages(self):
        """Ask to clear messages."""
        self._confirm_destructive(_("Delete All Messages"), _("This will delete all SMS/MMS and attachments."), self._do_clear_messages)

    def ask_clear_groups(self):
        """Ask to clear group names."""
        self._confirm_destructive(_("Delete Group Names"), _("Reset all custom group chat names?"), self._do_clear_groups)

    def ask_clear_blocklist(self):
        """Ask to clear blocklist."""
        self._confirm_destructive(_("Delete Blocklist"), _("Unblock all numbers?"), self._do_clear_blocklist)

    def ask_clear_contacts(self):
        """Ask to clear contacts."""
        self._prompt_delete_contacts_source(self._do_clear_contacts)

    def ask_delete_addressbook(self):
        """Ask to delete an entire address book."""
        self._prompt_delete_addressbook(self._do_delete_addressbook)

    def ask_clear_everything(self):
        """Ask to clear everything."""
        self._prompt_delete_contacts_source(self._do_clear_everything, everything=True)

    def _prompt_delete_addressbook(self, callback):
        """Prompt user for which address book to permanently delete."""
        sources = self.eds.get_sources_info()

        protected_uids = ["system-address-book"]
        protected_names = ["Andromeda Contacts"]

        enabled_sources = [s for s in sources if s['uid'] not in protected_uids and s['name'] not in protected_names]

        if not enabled_sources:
            self._confirm_destructive(_("No deletable address books"), _("There are no custom address books that can be deleted."), lambda: None)
            return

        d = Adw.MessageDialog(
            heading=_("Delete Address Book"),
            body=_("Which address book do you want to delete permanently?\n\nThis will wipe it from the system.")
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
            btn.add_css_class("destructive-action")

            def _cb_specific(b, uid=s['uid'], source_name=name):
                GLib.idle_add(lambda: d.close() or False)
                self._confirm_destructive(
                    _("Delete Address Book"),
                    _("Are you sure you want to permanently delete the '{name}' address book?").format(name=source_name),
                    lambda: callback(uid)
                )
            btn.connect("clicked", lambda b, cb=_cb_specific: GLib.idle_add(lambda: cb(b) or False))
            box.append(btn)

        d.set_extra_child(box)
        d.present()

    def _prompt_delete_contacts_source(self, callback, everything=False):
        """Prompt user for which address book to delete (or All)."""
        sources = self.eds.get_sources_info()

        enabled_sources = [s for s in sources if s['enabled']]

        title = _("Delete EVERYTHING") if everything else _("Delete All Contacts")
        body = _("Wipe database, config files and contacts? App will reset.") if everything else _("Really delete everyone from your address book?")

        if not enabled_sources:
            self._confirm_destructive(title, body, lambda: callback(None))
            return

        d = Adw.MessageDialog(
            heading=title,
            body=body + "\n\n" + _("Select which address book to clear:")
        )
        d.set_transient_for(self.app_window)
        d.add_response("cancel", _("Cancel"))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(24)
        box.set_margin_end(24)

        btn_all = Gtk.Button(label=_("All Address Books"))
        btn_all.add_css_class("destructive-action")

        def _cb_all(b):
            GLib.idle_add(lambda: d.close() or False)
            callback(None)
        btn_all.connect("clicked", lambda b: GLib.idle_add(lambda: _cb_all(b) or False))
        box.append(btn_all)

        for s in enabled_sources:
            name = s['name']
            if len(name) > 30:
                name = name[:27] + "..."

            btn = Gtk.Button(label=name)
            btn.add_css_class("destructive-action")

            def _cb_specific(b, uid=s['uid']):
                GLib.idle_add(lambda: d.close() or False)
                callback(uid)
            btn.connect("clicked", lambda b, cb=_cb_specific: GLib.idle_add(lambda: cb(b) or False))
            box.append(btn)

        d.set_extra_child(box)
        d.present()

    def _do_clear_history(self):
        """Clear history action."""
        run_in_background(self.db.clear_history,
                          on_complete=lambda _r: self.app_window.notify_success(_("Call History Deleted")))

    def _do_clear_messages(self):
        """Clear messages action."""
        self.app_window.notify_loading(_("Deleting messages..."))

        def task():
            self.db.clear_messages()
            GLib.idle_add(self.app_window.hide_loading)

            GLib.idle_add(lambda: self.app_window.notify_success(_("Messages & Attachments Deleted")))
        run_in_background(task)

    def _do_clear_groups(self):
        """Clear groups action."""
        run_in_background(self.db.clear_group_names,
                          on_complete=lambda _r: self.app_window.notify_success(_("Group Names Reset")))

    def _do_clear_blocklist(self):
        """Clear blocklist action."""
        run_in_background(self.db.clear_blocklist,
                          on_complete=lambda _r: self.app_window.notify_success(_("Blocklist Cleared")))

    def _do_clear_contacts(self, source_uid=None):
        """Clear contacts action."""
        self.app_window.notify_loading(_("Deleting contacts..."))

        def task():
            self.eds.delete_all_contacts(source_uid=source_uid)
            GLib.idle_add(self.app_window.hide_loading)

            GLib.idle_add(lambda: self.app_window.notify_success(_("Contacts Deleted")))
        run_in_background(task)

    def _do_delete_addressbook(self, source_uid):
        """Delete entire address book action."""
        if not source_uid:
            return

        self.app_window.notify_loading(_("Deleting address book..."))

        def task():
            success = self.eds.delete_addressbook(source_uid)
            GLib.idle_add(self.app_window.hide_loading)
            if success:
                GLib.idle_add(lambda: self.app_window.notify_success(_("Address Book Deleted")))
            else:
                GLib.idle_add(lambda: self.app_window.notify_error(_("Failed to delete Address Book")))
        run_in_background(task)

    def _do_clear_everything(self, source_uid=None):
        """Clear everything action."""
        self.app_window.notify_loading(_("Wiping Database..."))

        def task():
            self.db.clear_everything()
            self.eds.delete_all_contacts(source_uid=source_uid)
            try:
                cfg = os.path.join(GLib.get_user_config_dir(), "telephony.json")
                if os.path.exists(cfg):
                    os.remove(cfg)
            except Exception as e:
                logger.warning(f"[DataManagement] Config removal failed: {e}")
            GLib.idle_add(self.app_window.hide_loading)

            GLib.idle_add(lambda: self.app_window.notify_success(_("App Reset Complete")))
        run_in_background(task)
