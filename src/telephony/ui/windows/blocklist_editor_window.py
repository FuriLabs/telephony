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

from ...backend.utils.phone_utils import normalize_number


class BlocklistEditor(Adw.Window):
    """Window to add a new number to the blocklist."""

    def __init__(self, db_manager, eds_manager, parent_window, number_preset=None, name_preset=None):
        """Initialize the Blocklist Editor."""
        super().__init__(title=_("Block Number"))
        self.set_transient_for(parent_window)
        self.set_modal(True)
        self.set_default_size(350, 400)

        self.db = db_manager
        self.eds = eds_manager

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(content)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: self.close() or False))
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

    def on_save(self, btn):
        """Validate and save the blocked number."""
        raw_num = self.entry_num.get_text().strip()
        note = self.entry_note.get_text().strip()

        if not raw_num:
            return

        norm_num = normalize_number(raw_num)

        if self.db.is_blocked(norm_num):
            logger.warning(f"[Blocklist] Block rejected: Number {norm_num} is already blocked.")
            self._show_error(_("Duplicate"), _("This number is already blocked."))
            return

        def _do_block():
            if self.db.block_number(norm_num, note):
                logger.info(f"[Blocklist] Added number: {norm_num}")
                GLib.idle_add(lambda: self.close() or False)
            else:
                self._show_error(_("Database Error"), _("Failed to save to blocklist."))

        if self.eds.search_contacts(norm_num):
            self._confirm_block_remove(raw_num, _do_block)
            return

        _do_block()

    def _confirm_block_remove(self, _number_str, on_confirm):
        """Show confirmation to block and remove from contacts."""
        d = Adw.MessageDialog(
            heading=_("Conflict"),
            body=_("Number can't be on both Blocklist and Contacts.\n\nDo you want to proceed with Blocking and remove the number from Contacts?")
        )
        d.set_transient_for(self)
        d.add_response("cancel", _("Cancel"))
        d.add_response("yes", _("Yes, Block"))
        d.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE)

        def _cb(dialog, resp):
            GLib.idle_add(lambda: dialog.close() or False)
            if resp == "yes":
                on_confirm()

        d.connect("response", _cb)
        d.present()

    def _show_error(self, title, msg):
        """Show error dialog."""
        d = Adw.MessageDialog(heading=title, body=msg)
        d.set_transient_for(self)
        d.add_response("ok", _("OK"))
        d.connect("response", lambda d, r: GLib.idle_add(lambda: d.close() or False))
        d.present()
