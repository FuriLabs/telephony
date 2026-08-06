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
from gettext import gettext as _


class ImportWizardWindow(Adw.NavigationPage):
    """Wizard page choosing the source of a Calls or Chatty import.

    Lives inside the import flow's navigation, so the back button and
    the back gesture leave the wizard the way every other step does.
    """

    def __init__(self, parent_window, import_type, done_callback):
        """
        import_type: 'chatty' or 'calls'
        done_callback(db_path, mms_dir) -> db_path and mms_dir can be None if system default is chosen
        """
        title = _("Import Messages") if import_type == 'chatty' else _("Import Call History")
        super().__init__(title=title)
        self.parent_window = parent_window
        self.import_type = import_type
        self.done_callback = done_callback

        self.custom_db_path = None
        self.custom_mms_path = None
        self._is_finished = False
        self.connect("hidden", self._on_hidden)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(self.main_box)

        self.header = Adw.HeaderBar(show_end_title_buttons=False)
        self.main_box.append(self.header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.main_box.append(self.stack)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_vexpand(True)
        self.stack.add_named(self.scroll, "main")

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.content_box.set_margin_top(12)
        self.content_box.set_margin_bottom(12)
        self.content_box.set_margin_start(12)
        self.content_box.set_margin_end(12)
        self.scroll.set_child(self.content_box)

        self.load_page = Adw.StatusPage()
        self.load_page.set_title(_("Waiting for selection..."))

        box_spin = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box_spin.set_halign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(64, 64)
        box_spin.append(self.spinner)

        self.load_page.set_child(box_spin)
        self.stack.add_named(self.load_page, "loading")

        self.stack.set_visible_child_name("main")

        self._build_step_source()

    def _clear_box(self):
        while child := self.content_box.get_first_child():
            self.content_box.remove(child)

    def _build_step_source(self):
        self._clear_box()

        lbl_info = Gtk.Label(label=_("Where do you want to import from?"))
        lbl_info.add_css_class("heading")
        lbl_info.set_wrap(True)
        lbl_info.set_justify(Gtk.Justification.CENTER)
        self.content_box.append(lbl_info)

        card = Adw.PreferencesGroup()

        row_sys = Adw.ActionRow(title=_("System Database"))
        if self.import_type == 'chatty':
            row_sys.set_subtitle(_("Import from local Chatty installation"))
        else:
            row_sys.set_subtitle(_("Import from local Calls installation"))

        btn_sys = Gtk.Button(label=_("Import"))
        btn_sys.add_css_class("suggested-action")
        btn_sys.set_valign(Gtk.Align.CENTER)
        btn_sys.connect("clicked", lambda b: GLib.idle_add(lambda: self._finish_system() or False))
        row_sys.add_suffix(btn_sys)
        card.add(row_sys)

        row_custom = Adw.ActionRow(title=_("Custom Database Backup"))
        row_custom.set_subtitle(_("Select an exported SQLite DB file"))

        btn_custom = Gtk.Button(label=_("Select File"))
        btn_custom.set_valign(Gtk.Align.CENTER)
        btn_custom.connect("clicked", lambda b: GLib.idle_add(lambda: self._step_custom_db() or False))
        row_custom.add_suffix(btn_custom)
        card.add(row_custom)

        self.content_box.append(card)

    def _finish_system(self):
        self._is_finished = True
        self.stack.set_visible_child_name("loading")
        self.spinner.start()
        self.done_callback(None, None)
        self._leave()

    def _on_hidden(self, page):
        """Report a cancelled wizard exactly once, on any exit path."""
        if page.get_parent() is None and not self._is_finished:
            self.done_callback(False, False)

    def _leave(self):
        """Leave the wizard, however it finished."""
        nav = self.get_ancestor(Adw.NavigationView)
        if nav and nav.get_visible_page() is self:
            nav.pop()

    def _step_custom_db(self):
        dialog = Gtk.FileChooserNative(
            title=_("Select Database File"),
            transient_for=self.parent_window,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.connect("response", self._on_db_file_selected)
        dialog.show()

    def _on_db_file_selected(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            self.custom_db_path = dialog.get_file().get_path()
            if self.import_type == 'chatty':
                self._build_step_mms()
            else:
                self._finish_custom()

    def _build_step_mms(self):
        self._clear_box()

        lbl_info = Gtk.Label(label=_("MMS Attachments (Optional)"))
        lbl_info.add_css_class("heading")
        lbl_info.set_wrap(True)
        lbl_info.set_justify(Gtk.Justification.CENTER)
        self.content_box.append(lbl_info)

        card = Adw.PreferencesGroup()

        row_mms = Adw.ActionRow(title=_("MMS Folder Backup"))
        row_mms.set_subtitle(_("Select the directory containing images/videos"))

        btn_mms = Gtk.Button(label=_("Select Folder"))
        btn_mms.add_css_class("suggested-action")
        btn_mms.set_valign(Gtk.Align.CENTER)
        btn_mms.connect("clicked", lambda b: GLib.idle_add(lambda: self._step_custom_mms() or False))
        row_mms.add_suffix(btn_mms)
        card.add(row_mms)

        row_skip = Adw.ActionRow(title=_("Skip Attachments"))
        row_skip.set_subtitle(_("Import SMS and texts only"))

        def on_skip():
            self.custom_mms_path = ""
            self._finish_custom()

        btn_skip = Gtk.Button(label=_("Skip"))
        btn_skip.set_valign(Gtk.Align.CENTER)
        btn_skip.connect("clicked", lambda b: GLib.idle_add(lambda: on_skip() or False))
        row_skip.add_suffix(btn_skip)
        card.add(row_skip)

        self.content_box.append(card)

    def _step_custom_mms(self):
        dialog = Gtk.FileChooserNative(
            title=_("Select MMS Folder"),
            transient_for=self.parent_window,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.connect("response", self._on_mms_folder_selected)
        dialog.show()

    def _on_mms_folder_selected(self, dialog, response):
        """
        Handles user folder selection for MMS files.
        If dismissed without a selection, explicit empty string is set
        to prevent automatic fallback to the system default path.
        """
        if response == Gtk.ResponseType.ACCEPT:
            self.custom_mms_path = dialog.get_file().get_path()
        else:
            self.custom_mms_path = ""
        self._finish_custom()

    def _finish_custom(self):
        self._is_finished = True
        self.stack.set_visible_child_name("loading")
        self.spinner.start()
        self.done_callback(self.custom_db_path, self.custom_mms_path)
        self._leave()
