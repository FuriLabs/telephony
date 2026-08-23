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
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _

from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.thread_utils import run_in_background
from telephony.client.ui.widgets.common_widget import (
                                                      wire_blocklist_switch_locks,
                                                      close_sheet_page, present_alert_sheet)


class BlocklistEditor(Adw.NavigationPage):
    """Bottom sheet to add a new number to the blocklist."""

    def __init__(self, db_manager, eds_manager, parent_window, number_preset=None, name_preset=None):
        """Initialize the Blocklist Editor."""
        super().__init__(title=_("Block Number"))
        self.set_size_request(-1, 400)

        self.db = db_manager
        self.eds = eds_manager
        self.app_window = parent_window
        self.daemon = parent_window.daemon

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(content)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False))
        header.pack_start(btn_cancel)

        btn_save = Gtk.Button(label=_("Save"))
        btn_save.add_css_class("suggested-action")
        btn_save.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_save(b) or False))
        header.pack_end(btn_save)

        content.append(header)

        page = Adw.PreferencesPage()
        grp = Adw.PreferencesGroup()
        grp.set_title(_("Number Details"))
        grp.set_description(_("Calls from this number will be automatically rejected."))
        page.add(grp)
        content.append(page)

        self.entry_num = Adw.EntryRow(title=_("Number"))
        self.entry_num.set_input_purpose(Gtk.InputPurpose.PHONE)
        if number_preset:
            self.entry_num.set_text(number_preset)

        grp.add(self.entry_num)

        self.entry_note = Adw.EntryRow(title=_("Note (Optional)"))
        if name_preset:
            self.entry_note.set_text(name_preset)

        grp.add(self.entry_note)
        self.sw_calls = Adw.SwitchRow(title=_("Block Calls"), active=True)
        self.sw_calls.add_prefix(Gtk.Image.new_from_icon_name("call-stop-symbolic"))
        grp.add(self.sw_calls)
        self.sw_messages = Adw.SwitchRow(title=_("Block Messages"), active=True)
        self.sw_messages.add_prefix(Gtk.Image.new_from_icon_name("mail-unread-symbolic"))
        grp.add(self.sw_messages)
        wire_blocklist_switch_locks(self.sw_calls, self.sw_messages)

    def on_save(self, btn):
        """Validate and save the blocked number."""
        raw_num = self.entry_num.get_text().strip()
        note = self.entry_note.get_text().strip()

        if not raw_num:
            return

        norm_num = normalize_number(raw_num)

        if self.db.is_blocked(norm_num, kind="any"):
            logger.warning(f"[Blocklist] Block rejected: Number {norm_num} is already blocked.")
            self.show_error(_("Duplicate"), _("This number is already blocked."))
            return

        block_calls = bool(self.sw_calls and self.sw_calls.get_active())
        block_messages = bool(self.sw_messages and self.sw_messages.get_active())

        block_calls = self.sw_calls.get_active()
        block_messages = self.sw_messages.get_active()

        def start_block(done):
            run_in_background(self.daemon.add_blocked_number, norm_num, note,
                              block_calls, block_messages, on_complete=done)

        def confirmed():
            """Leave first, then block.

            Answering the question is the end of the flow. Falling back
            into the block sheet for however long the write takes reads
            as the question not having worked, so the sheet goes first
            and the write reports through a toast if it fails.
            """
            close_sheet_page(self.get_root())
            start_block(lambda ok: None if ok else
                         self.app_window.notify_error(_("Failed to save to blocklist.")))

        if self.eds.search_contacts(norm_num):
            self.confirm_block_remove(raw_num, confirmed)
            return

        def direct_done(success):
            if success:
                close_sheet_page(self.get_root())
            else:
                self.show_error(_("Database Error"), _("Failed to save to blocklist."))

        start_block(direct_done)

    def confirm_block_remove(self, _number_str, on_confirm):
        """Show confirmation to block and remove from contacts."""
        present_alert_sheet(
            self.get_root(), _("Conflict"),
            _("Number can't be on both Blocklist and Contacts.\n\nDo you want to proceed with Blocking and remove the number from Contacts?"),
            [("cancel", _("Cancel"), None), ("yes", _("Yes, Block"), "destructive")],
            lambda answer: on_confirm() if answer == "yes" else None)

    def show_error(self, title, msg):
        """Report a failure the user can only acknowledge."""
        self.app_window.notify_error(msg)
