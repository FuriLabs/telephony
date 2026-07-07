# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Adw, GLib
from gettext import gettext as _, ngettext
from telephony.shared.utils.thread_utils import run_in_background
from telephony.client.ui.widgets.common_widget import (present_alert_sheet, present_choice_sheet,
                                                      add_choice_row)


class DataManagementDialog:

    def __init__(self, app_window):
        self.app_window = app_window
        self.db = app_window.db
        self.eds = app_window.eds
        self.page = None

    def build_page(self):
        self.page = Adw.NavigationPage(title=_("Data Management"))
        view = Adw.ToolbarView()
        self.page.set_child(view)
        view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))

        prefs = Adw.PreferencesPage()
        view.set_content(prefs)

        group = Adw.PreferencesGroup(description=_("Select data to permanently delete."))
        prefs.add(group)

        entries = (
            (_("Delete Call History"), self.ask_clear_history),
            (_("Delete All Messages"), self.ask_clear_messages),
            (_("Delete All Group Names"), self.ask_clear_groups),
            (_("Delete Blocklist"), self.ask_clear_blocklist),
            (_("Delete All Contacts"), self.ask_clear_contacts),
            (_("Delete Address Book"), self.ask_delete_addressbook),
        )
        for label, callback in entries:
            row = Adw.ActionRow(title=label, activatable=True)
            row.add_css_class("error")
            row.connect("activated", lambda r, cb=callback: GLib.idle_add(lambda: cb() or False))
            group.add(row)

        grp_all = Adw.PreferencesGroup()
        prefs.add(grp_all)
        row_all = Adw.ActionRow(title=_("DELETE EVERYTHING"), activatable=True)
        row_all.add_css_class("error")
        row_all.connect("activated", lambda r: GLib.idle_add(lambda: self.ask_clear_everything() or False))
        grp_all.add(row_all)

        return self.page

    def confirm_destructive(self, title, body, on_confirm):
        present_alert_sheet(
            self.page.get_root(), title, body,
            [("cancel", _("Cancel"), None), ("delete", _("Delete"), "destructive")],
            lambda answer: on_confirm() if answer == "delete" else None)

    def ask_clear_history(self):
        def ask(count):
            body = ngettext("This will delete {count} call. It cannot be undone.",
                            "This will delete {count} calls. It cannot be undone.",
                            count).format(count=count)
            self.confirm_destructive(_("Delete Call History"), body, self.do_clear_history)

        run_in_background(self.db.count_rows, "history", on_complete=ask)

    def ask_clear_messages(self):
        def ask(count):
            body = ngettext("This will delete {count} message and its attachments.",
                            "This will delete {count} messages and their attachments.",
                            count).format(count=count)
            self.confirm_destructive(_("Delete All Messages"), body, self.do_clear_messages)

        run_in_background(self.db.count_rows, "messages", on_complete=ask)

    def ask_clear_groups(self):
        def ask(count):
            body = ngettext("This will reset {count} custom group name.",
                            "This will reset {count} custom group names.",
                            count).format(count=count)
            self.confirm_destructive(_("Delete Group Names"), body, self.do_clear_groups)

        run_in_background(self.db.count_rows, "group_names", on_complete=ask)

    def ask_clear_blocklist(self):
        def ask(count):
            body = ngettext("This will unblock {count} number.",
                            "This will unblock {count} numbers.",
                            count).format(count=count)
            self.confirm_destructive(_("Delete Blocklist"), body, self.do_clear_blocklist)

        run_in_background(self.db.count_rows, "blocklist", on_complete=ask)

    def ask_clear_contacts(self):
        self.prompt_delete_contacts_source(self.do_clear_contacts)

    def ask_delete_addressbook(self):
        self.prompt_delete_addressbook(self.do_delete_addressbook)

    def ask_clear_everything(self):
        self.prompt_delete_contacts_source(self.do_clear_everything, everything=True)

    def prompt_delete_addressbook(self, callback):
        self.eds.sources_info_async(
            lambda sources: self.choose_addressbook_to_delete(sources, callback))

    def choose_addressbook_to_delete(self, sources, callback):
        protected_uids = ["system-address-book"]
        protected_names = ["Andromeda Contacts"]

        enabled_sources = [s for s in sources if s["uid"] not in protected_uids and s["name"] not in protected_names]

        if not enabled_sources:
            self.confirm_destructive(_("No deletable address books"), _("There are no custom address books that can be deleted."), lambda: None)
            return

        def build(group, window):
            for source in enabled_sources:
                def chosen(uid=source["uid"], source_name=source["name"]):
                    self.confirm_destructive(
                        _("Delete Address Book"),
                        _("Are you sure you want to permanently delete the '{name}' address book?").format(name=source_name),
                        lambda: callback(uid))

                add_choice_row(group, window, source["name"], chosen, destructive=True)

        present_choice_sheet(
            self.page, _("Delete Address Book"), build,
            description=_("Which address book do you want to delete permanently? This wipes it from the system."))

    def prompt_delete_contacts_source(self, callback, everything=False):
        self.eds.sources_info_async(
            lambda sources: self.choose_contacts_source_to_delete(sources, callback, everything))

    def choose_contacts_source_to_delete(self, sources, callback, everything):
        enabled_sources = [s for s in sources if s["enabled"]]

        title = _("Delete EVERYTHING") if everything else _("Delete All Contacts")
        body = _("Wipe database, config files and contacts? App will reset.") if everything else _("Really delete everyone from your address book?")

        if not enabled_sources:
            self.confirm_destructive(title, body, lambda: callback(None))
            return

        def build(group, window):
            add_choice_row(group, window, _("All Address Books"),
                           lambda: callback(None), destructive=True)
            for source in enabled_sources:
                add_choice_row(group, window, source["name"],
                               lambda uid=source["uid"]: callback(uid), destructive=True)

        present_choice_sheet(self.page, title, build, description=body)

    def do_clear_history(self):
        self.app_window.daemon.clear_call_history(
            lambda _ok: self.app_window.notify_success(_("Call History Deleted")))

    def do_clear_messages(self):
        self.app_window.notify_loading(_("Deleting messages..."))

        def done(_ok):
            self.app_window.hide_loading()
            self.app_window.notify_success(_("Messages & Attachments Deleted"))

        self.app_window.daemon.clear_messages(done)

    def do_clear_groups(self):
        self.app_window.daemon.clear_group_names(
            lambda _ok: self.app_window.notify_success(_("Group Names Reset")))

    def do_clear_blocklist(self):
        self.app_window.daemon.clear_blocklist(
            lambda _ok: self.app_window.notify_success(_("Blocklist Cleared")))

    def do_clear_contacts(self, source_uid=None):
        self.app_window.notify_loading(_("Deleting contacts..."))

        def done(_ok):
            self.app_window.hide_loading()
            self.app_window.notify_success(_("Contacts Deleted"))

        self.app_window.daemon.clear_contacts(source_uid, done)

    def do_delete_addressbook(self, source_uid):
        if not source_uid:
            return

        self.app_window.notify_loading(_("Deleting address book..."))

        def done(success):
            self.app_window.hide_loading()
            if success:
                self.app_window.notify_success(_("Address Book Deleted"))
            else:
                self.app_window.notify_error(_("Failed to delete Address Book"))

        self.app_window.daemon.delete_address_book(source_uid, done)

    def do_clear_everything(self, source_uid=None):
        self.app_window.notify_loading(_("Wiping Database..."))

        def done(_ok):
            self.app_window.hide_loading()
            self.app_window.notify_success(_("App Reset Complete"))

        self.app_window.daemon.clear_everything(source_uid, done)
