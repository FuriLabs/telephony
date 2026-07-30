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

from gi.repository import Gtk, Adw, Gdk, GLib
from loguru import logger
from gettext import gettext as _

from ...backend.utils.phone_utils import normalize_number
from ...backend.utils.thread_utils import run_in_background
from ...backend.utils.vcard_utils import extract_e164_number, unfold_vcard
from .date_time_picker_window import DateTimePicker
from .duplicate_resolution_window import DuplicateResolutionWindow
from ..widgets.common_widget import translate_phone_label


class ContactEditor(Adw.Window):
    """Window for editing or viewing contact details."""

    def __init__(self, eds_manager, main_window, contact_data=None, number_preset=None):
        self.btn_save = None
        self.source_toggles = None
        self.switch_fav = None
        self._saving_in_progress = False
        """Initialize the Contact Editor."""
        super().__init__(title=_("Contact Details"))
        self.eds = eds_manager
        self.main_window = main_window
        self.uid = contact_data['uid'] if contact_data else None

        self.vcard_cache = ""
        if contact_data:
            if 'vcard' in contact_data and contact_data['vcard']:
                self.vcard_cache = contact_data['vcard']
            elif self.uid:
                self.vcard_cache = self.eds.get_contact_vcard(self.uid)

        self.contact_name = contact_data['name'] if contact_data else ""

        self.number_preset = number_preset

        self.mode = "VIEW" if self.uid else "EDIT"

        self.set_modal(True)
        self.set_default_size(380, 650)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        self.phone_entries = []
        self.email_entries = []
        self.adv_entries = {}

        self.selected_date = None

        self.refresh_ui()

    def _clean_vcard_str(self, text):
        """Clean up vCard text field (unescape)."""
        if not text:
            return ""
        return text.replace(r"\,", ",").replace(r"\;", ";").replace(r"\n", "\n").strip()

    def refresh_ui(self):
        """Rebuild the UI based on current mode and data."""
        view = Adw.ToolbarView()

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        view.add_top_bar(header)

        is_andromeda = False
        if self.uid:
            current_source = self.uid.split(':', 1)[0] if ':' in self.uid else None
            sources = self.eds.get_sources_info()
            for s in sources:
                if s['uid'] == current_source and s['name'] == "Andromeda Contacts":
                    is_andromeda = True
                    break

        if self.mode == "VIEW":
            btn_close = Gtk.Button(label=_("Close"))
            btn_close.connect("clicked", lambda b: GLib.idle_add(lambda: self.close() or False))
            header.pack_start(btn_close)

            btn_edit = Gtk.Button(label=_("Edit"))
            btn_edit.add_css_class("suggested-action")
            btn_edit.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_edit_mode_click(b) or False))

            if not is_andromeda:
                header.pack_end(btn_edit)

            if not self.eds.is_ready:
                btn_edit.set_sensitive(False)

            if self.uid:
                btn_export = Gtk.Button(icon_name="document-save-symbolic")
                btn_export.add_css_class("circular")
                btn_export.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_export_click(b) or False))
                header.pack_end(btn_export)
        else:
            btn_cancel = Gtk.Button(label=_("Cancel"))
            btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_cancel_edit(b) or False))
            header.pack_start(btn_cancel)

            self.btn_save = Gtk.Button(label=_("Save"))
            self.btn_save.add_css_class("suggested-action")
            self.btn_save.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_save(b) or False))
            header.pack_end(self.btn_save)

            if not self.eds.is_ready:
                self.btn_save.set_sensitive(False)

        page = Adw.PreferencesPage()
        view.set_content(page)

        current_phones = self._extract_phones_with_labels()
        current_emails = self._extract_emails_with_labels()

        grp_id = Adw.PreferencesGroup(title=_("Identity"))

        if self.mode == "EDIT":
            self.row_fav = Adw.ActionRow(title=_("Favorite"))
            self.switch_fav = Gtk.Switch(valign=Gtk.Align.CENTER)
            is_fav = self._extract_field("X-FOLKS-FAVOURITE")
            self.switch_fav.set_active(is_fav and is_fav.lower() == "true")
            self.row_fav.add_suffix(self.switch_fav)
            grp_id.add(self.row_fav)

        if self.mode == "EDIT":
            self.entry_fn = Adw.EntryRow(title=_("Full Name"))
            self.entry_fn.set_text(self.contact_name)

            grp_id.add(self.entry_fn)
        else:
            if self.contact_name:
                grp_id.add(self._create_view_row(_("Name"), self.contact_name))

            is_fav = self._extract_field("X-FOLKS-FAVOURITE")
            if is_fav and is_fav.lower() == "true":
                grp_id.add(self._create_view_row(_("Favorite"), _("Yes")))

        page.add(grp_id)

        self._add_address_books_group(page)

        grp_phones = Adw.PreferencesGroup(title=_("Phone Numbers"))
        if self.mode == "EDIT":
            self.phone_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.phone_entries = []

            if current_phones:
                phones = current_phones
            elif self.number_preset:
                phones = [(self.number_preset, "Mobile")]
            else:
                phones = [("", "Mobile")]

            for p_num, p_label in phones:
                self._add_phone_row(p_num, p_label)

            grp_phones.add(self.phone_rows_box)
            btn_add_phone = Gtk.Button(label=_("Add Number"), margin_top=6)
            btn_add_phone.connect("clicked", lambda b: GLib.idle_add(lambda: self._add_phone_row("", "Mobile") or False))
            grp_phones.add(btn_add_phone)
        else:
            if current_phones:
                for num, lbl in current_phones:
                    grp_phones.add(self._create_view_row(translate_phone_label(lbl), num))
            else:
                grp_phones.set_visible(False)
        page.add(grp_phones)

        grp_emails = Adw.PreferencesGroup(title=_("Email Addresses"))
        if self.mode == "EDIT":
            self.email_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.email_entries = []

            emails = current_emails if current_emails else [("", "Home")]
            for e_addr, e_label in emails:
                self._add_email_row(e_addr, e_label)

            grp_emails.add(self.email_rows_box)
            btn_add_email = Gtk.Button(label=_("Add Email"), margin_top=6)
            btn_add_email.connect("clicked", lambda b: GLib.idle_add(lambda: self._add_email_row("", "Home") or False))
            grp_emails.add(btn_add_email)
        else:
            if current_emails:
                for addr, lbl in current_emails:
                    grp_emails.add(self._create_view_row(translate_phone_label(lbl), addr))
            else:
                grp_emails.set_visible(False)
        page.add(grp_emails)

        grp_adv = Adw.PreferencesGroup(title=_("Additional Details"))

        text_fields = [
            ("ORG", _("Organization")), ("TITLE", _("Job Title")),
            ("ADR", _("Address")), ("NOTE", _("Notes")),
            ("URL", _("Website"))
        ]

        has_adv_data = False
        self.adv_entries = {}

        for key, lbl in text_fields:
            val = self._extract_field(key)
            if self.mode == "EDIT":
                row = Adw.EntryRow(title=lbl)
                if val:
                    row.set_text(val)

                grp_adv.add(row)
                self.adv_entries[key] = row
                has_adv_data = True
            else:
                if val:
                    grp_adv.add(self._create_view_row(lbl, val))
                    has_adv_data = True

        bday_val = self._extract_field("BDAY")
        anniv_val = self._extract_field("ANNIVERSARY")

        if self.mode == "EDIT":
            self.bday_row = self._create_date_editor(grp_adv, _("Birthday"), bday_val)
            self.anniv_row = self._create_date_editor(grp_adv, _("Anniversary"), anniv_val)
            has_adv_data = True
        else:
            if bday_val:
                grp_adv.add(self._create_view_row(_("Birthday"), bday_val))
                has_adv_data = True
            if anniv_val:
                grp_adv.add(self._create_view_row(_("Anniversary"), anniv_val))
                has_adv_data = True

        if not has_adv_data and self.mode == "VIEW":
            grp_adv.set_visible(False)
        page.add(grp_adv)

        if self.uid and not is_andromeda:
            grp_danger = Adw.PreferencesGroup()
            btn_del = Gtk.Button(label=_("Delete Contact"))
            btn_del.add_css_class("destructive-action")
            btn_del.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_delete(b) or False))
            if not self.eds.is_ready:
                btn_del.set_sensitive(False)
            grp_danger.add(btn_del)
            page.add(grp_danger)

        self.toast_overlay.set_child(view)

    def _add_address_books_group(self, page):
        grp = Adw.PreferencesGroup(title=_("Address Books"))
        page.add(grp)

        self.source_toggles = {}
        sources = self.eds.get_sources_info()
        enabled = [s for s in sources if s['enabled']]

        current_source = None
        if self.uid and ':' in self.uid:
            current_source = self.uid.split(':', 1)[0]

        if self.mode == "VIEW":
            found = False
            for s in enabled:
                if s['uid'] == current_source:
                    row = Adw.ActionRow(title=s['name'])
                    row.add_suffix(Gtk.Image(icon_name="object-select-symbolic"))
                    grp.add(row)
                    found = True
            if not found and current_source:
                grp.add(Adw.ActionRow(title=_("Unknown Source")))
        else:
            default_source = next((s['uid'] for s in enabled if s['is_system_default']), None)

            for s in enabled:
                if not self.uid and s['name'] == "Andromeda Contacts":
                    continue
                row = Adw.SwitchRow(title=s['name'])
                is_active = False
                if self.uid:
                    if s['uid'] == current_source:
                        is_active = True
                    row.set_sensitive(False)
                else:
                    if s['uid'] == default_source:
                        is_active = True
                row.set_active(is_active)
                grp.add(row)
                self.source_toggles[s['uid']] = row

    def _create_date_editor(self, group, title, current_val):
        """Create a date editor row."""
        row = Adw.ActionRow(title=title)

        lbl_date = Gtk.Label(label=_("Select Date"), css_classes=["dim-label"])
        row.add_suffix(lbl_date)

        btn_edit = Gtk.Button(icon_name="x-office-calendar-symbolic", valign=Gtk.Align.CENTER)
        btn_edit.add_css_class("flat")
        btn_edit.add_css_class("circular")
        row.add_suffix(btn_edit)

        btn_clear = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER)
        btn_clear.add_css_class("flat")
        btn_clear.add_css_class("circular")
        btn_clear.set_tooltip_text(_("Clear Date"))
        row.add_suffix(btn_clear)

        group.add(row)

        row._selected_date = None

        if current_val:
            try:
                y, m, d = 0, 0, 0
                if "-" in current_val:
                    parts = current_val.split("-")
                    if len(parts) == 3:
                        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                elif len(current_val) == 8 and current_val.isdigit():
                    y = int(current_val[0:4])
                    m = int(current_val[4:6])
                    d = int(current_val[6:8])

                if y > 0:
                    row._selected_date = GLib.DateTime.new_local(y, m, d, 0, 0, 0)
                    lbl_date.set_label(f"{y:04d}-{m:02d}-{d:02d}")
                    lbl_date.remove_css_class("dim-label")
            except Exception as e:
                logger.warning(f"Failed to parse {title}: {e}")

        btn_edit.connect("clicked", lambda b: GLib.idle_add(lambda: self._open_date_picker(title, row, lbl_date) or False))
        btn_clear.connect("clicked", lambda b: GLib.idle_add(lambda: [setattr(row, '_selected_date', None), lbl_date.set_label(_("Select Date")), lbl_date.add_css_class("dim-label")] and False))

        return row

    def _open_date_picker(self, title, row, lbl_widget):
        """Open the DateTimePicker dialog."""
        def _on_picked(date):
            row._selected_date = date
            lbl_widget.set_label(date.format("%Y-%m-%d"))
            lbl_widget.remove_css_class("dim-label")
            if (self.btn_save is not None):
                self.btn_save.set_sensitive(True)

        DateTimePicker(
            parent=self,
            title=_("Select {title}").format(title=title),
            initial_date=row._selected_date,
            include_time=False,
            on_confirm=_on_picked
        )

    def _create_view_row(self, title, value):
        """Create a read-only row for viewing data."""
        row = Adw.ActionRow(title=title, subtitle=value)
        btn_copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        btn_copy.set_tooltip_text(_("Copy {title}").format(title=title))
        btn_copy.connect("clicked", lambda b: GLib.idle_add(lambda: self._copy_to_clipboard(value) or False))
        row.add_suffix(btn_copy)
        return row

    def _copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
        self.toast_overlay.add_toast(Adw.Toast.new(_("Copied to clipboard")))

    def on_edit_mode_click(self, btn):
        """Switch to EDIT mode."""
        self.mode = "EDIT"
        self.set_title(_("Edit Contact"))
        self.refresh_ui()

    def on_cancel_edit(self, btn):
        """Cancel editing."""
        if self.uid:
            self.mode = "VIEW"
            self.set_title(_("Contact Details"))
            self.refresh_ui()
        else:
            GLib.idle_add(lambda: self.close() or False)

    def _add_phone_row(self, text="", label="Mobile"):
        """Add a phone number entry row."""
        label_keys = ["Mobile", "Work", "Home", "Fax", "Other"]
        display_labels = [_("Mobile"), _("Work"), _("Home"), _("Fax"), _("Other")]
        self._add_field_row(self.phone_rows_box, self.phone_entries,
                            label_keys, display_labels, label, text,
                            Gtk.InputPurpose.PHONE)

    def _add_email_row(self, text="", label="Home"):
        """Add an email entry row."""
        label_keys = ["Home", "Work", "Other"]
        display_labels = [_("Home"), _("Work"), _("Other")]
        self._add_field_row(self.email_rows_box, self.email_entries,
                            label_keys, display_labels, label, text,
                            Gtk.InputPurpose.EMAIL)

    def _add_field_row(self, rows_box, entries_list, label_keys, display_labels, label, text, purpose):
        """Add a two-line field card: label selector and remove button on
        top, full-width value entry below."""
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["card"])

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                      margin_top=6, margin_start=10, margin_end=6)
        dropdown = Gtk.DropDown.new_from_strings(display_labels)
        if label in label_keys:
            dropdown.set_selected(label_keys.index(label))
        dropdown.set_valign(Gtk.Align.CENTER)
        dropdown.add_css_class("flat")
        top.append(dropdown)
        top.append(Gtk.Box(hexpand=True))

        btn_remove = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat", "circular"], valign=Gtk.Align.CENTER)
        btn_remove.connect("clicked", lambda b: GLib.idle_add(lambda: [rows_box.remove(row), entries_list.remove((entry, dropdown))] and False))
        top.append(btn_remove)
        row.append(top)

        entry = Gtk.Entry(text=text)
        entry.set_input_purpose(purpose)
        entry.set_hexpand(True)
        entry.set_margin_start(10)
        entry.set_margin_end(10)
        entry.set_margin_bottom(10)
        row.append(entry)

        rows_box.append(row)
        entries_list.append((entry, dropdown))

    def _extract_phones_with_labels(self):
        """Parse phone numbers from vCard data."""
        if not self.vcard_cache:
            return []
        res = []

        unfolded = unfold_vcard(self.vcard_cache)

        for line in unfolded.splitlines():
            if line.startswith("TEL"):
                parts = line.split(":", 1)
                if len(parts) > 1:
                    raw_number = self._clean_vcard_str(parts[1])
                    meta = parts[0].upper()

                    if "X-EVOLUTION-E164" in meta:
                        raw_number = extract_e164_number(parts[0], raw_number)

                    number = normalize_number(raw_number)
                    label = "Mobile"
                    if "WORK" in meta:
                        label = "Work"
                    elif "HOME" in meta:
                        label = "Home"
                    elif "FAX" in meta:
                        label = "Fax"
                    elif "VOICE" in meta and "CELL" not in meta:
                        label = "Other"
                    res.append((number, label))
        return res

    def _extract_emails_with_labels(self):
        """Parse emails from vCard data."""
        if not self.vcard_cache:
            return []
        res = []
        unfolded = unfold_vcard(self.vcard_cache)
        for line in unfolded.splitlines():
            if line.startswith("EMAIL"):
                parts = line.split(":", 1)
                if len(parts) > 1:
                    email = self._clean_vcard_str(parts[1])
                    meta = parts[0].upper()
                    label = "Home"
                    if "WORK" in meta:
                        label = "Work"
                    elif "HOME" in meta:
                        label = "Home"
                    elif "OTHER" in meta:
                        label = "Other"
                    res.append((email, label))
        return res

    def _extract_field(self, key):
        """Extract a generic vCard field."""
        if not self.vcard_cache:
            return ""
        unfolded = unfold_vcard(self.vcard_cache)
        for line in unfolded.splitlines():
            if line.startswith(f"{key}:") or line.startswith(f"{key};"):
                try:
                    raw_val = line.split(":", 1)[1].replace(";", " ")
                    return self._clean_vcard_str(raw_val)
                except Exception as e:
                    logger.debug(f"[ContactEditor] Field extract error: {e}")
                    return ""
        return None

    def on_export_click(self, btn):
        """Handle contact export."""
        dialog = Gtk.FileChooserNative(title=_("Export Contact"), transient_for=self, action=Gtk.FileChooserAction.SAVE)
        safe_name = "".join(x for x in self.contact_name if x.isalnum()) or "contact"
        dialog.set_current_name(f"{safe_name}.vcf")
        dialog.connect("response", self.on_export_response)
        dialog.show()

    def on_export_response(self, dialog, response):
        """Handle export dialog response."""
        if response == Gtk.ResponseType.ACCEPT:
            f = dialog.get_file()
            try:
                with open(f.get_path(), 'w', encoding='utf-8') as f_out:
                    f_out.write(self.vcard_cache)
            except Exception as e:
                logger.error(e)
        GLib.idle_add(lambda: dialog.destroy() or False)

    def on_delete(self, btn):
        """Handle contact deletion."""
        self.main_window.confirm_action(_("Delete Contact"), _("Are you sure you want to delete this contact?"), self._do_delete)

    def _do_delete(self):
        """Perform deletion."""
        if not self.uid:
            GLib.idle_add(lambda: self.close() or False)
            return

        contact = self.eds.cache.get(self.uid)
        numbers = []
        if contact and 'phones' in contact:
            numbers = [p[0] for p in contact['phones']]

        self.main_window.notify_loading(_("Deleting contact..."))

        if self.main_window.contacts_view:
            self.main_window.contacts_view.search.set_text("")

        def task():
            self.eds.delete_contact(self.uid)
            if numbers:
                self.main_window.db.update_history_names(numbers, new_name=None)
            return numbers

        def done(deleted_numbers):
            for num in deleted_numbers or []:
                self.main_window.gsettings_mgr.reset_special_list_names(num)
            self.close()

        run_in_background(task, on_complete=done)

    def _generate_vcard_from_ui(self, phones_to_save):
        """Generate VCard string from UI fields."""
        preserved_lines = []
        if self.vcard_cache:
            ignore_prefixes = (
                "BEGIN", "VERSION", "END", "FN", "N", "TEL", "EMAIL",
                "ORG", "TITLE", "ADR", "NOTE", "URL", "BDAY", "ANNIVERSARY",
                "X-FOLKS-FAVOURITE"
            )

            unfolded = unfold_vcard(self.vcard_cache)
            for line in unfolded.splitlines():
                parts = line.split(":", 1)
                key = parts[0].split(";")[0].upper()
                if key not in ignore_prefixes:
                    preserved_lines.append(line)

        lines = ["BEGIN:VCARD", "VERSION:3.0"]
        lines.extend(preserved_lines)

        fn = self.entry_fn.get_text().strip() or "Unknown"
        lines.append(f"FN:{fn}")

        parts = fn.split(" ")
        if len(parts) > 1:
            lines.append(f"N:{parts[-1]};{parts[0]};;;")
        else:
            lines.append(f"N:{fn};;;;")

        for p_val, lbl in phones_to_save:
            v_type = {"Mobile": "CELL", "Work": "WORK,VOICE", "Home": "HOME,VOICE", "Fax": "FAX", "Other": "VOICE"}.get(lbl, "CELL")
            lines.append(f"TEL;TYPE={v_type}:{p_val}")

        label_options_email = ["Home", "Work", "Other"]
        for entry, dropdown in self.email_entries:
            e_val = entry.get_text().strip()
            if e_val:
                lbl = label_options_email[dropdown.get_selected()]
                v_type = {"Home": "HOME", "Work": "WORK", "Other": "OTHER"}.get(lbl, "HOME")
                lines.append(f"EMAIL;TYPE={v_type}:{e_val}")

        for key, row in self.adv_entries.items():
            val = row.get_text().strip()
            if val:
                lines.append(f"{key}:{val}")

        if self.bday_row._selected_date:
            lines.append(f"BDAY:{self.bday_row._selected_date.format('%Y-%m-%d')}")

        if self.anniv_row._selected_date:
            lines.append(f"ANNIVERSARY:{self.anniv_row._selected_date.format('%Y-%m-%d')}")

        if (self.switch_fav is not None) and self.switch_fav.get_active():
            lines.append("X-FOLKS-FAVOURITE:true")

        lines.append("END:VCARD")
        return "\n".join(lines)

    def _get_current_contact_data(self, phones_to_save):
        """Construct a contact dictionary from UI."""
        vcard = self._generate_vcard_from_ui(phones_to_save)

        emails = []
        label_options_email = ["Home", "Work", "Other"]
        for entry, dropdown in self.email_entries:
            e_val = entry.get_text().strip()
            if e_val:
                lbl = label_options_email[dropdown.get_selected()]
                emails.append((e_val, lbl))

        return {
            'uid': self.uid,
            'name': self.entry_fn.get_text().strip() or "Unknown",
            'phones': phones_to_save,
            'emails': emails,
            'vcard': vcard,
            'is_fav': (self.switch_fav is not None) and self.switch_fav.get_active()
        }

    def on_save(self, btn, force=False):
        """Save contact changes."""
        if not force:
            if self._saving_in_progress:
                return
            self._saving_in_progress = True

        try:
            phones_to_save = []
            label_options_phone = ["Mobile", "Work", "Home", "Fax", "Other"]

            for entry, dropdown in self.phone_entries:
                raw_val = entry.get_text().strip()
                if raw_val:
                    norm_val = normalize_number(raw_val)
                    phones_to_save.append((norm_val, label_options_phone[dropdown.get_selected()]))

            blocked_conflict = None

            selected_sources = []
            if (self.source_toggles is not None):
                for uid, row in self.source_toggles.items():
                    if row.get_active():
                        selected_sources.append(uid)

            if not selected_sources:
                sources = self.eds.get_sources_info()
                def_uid = next((s['uid'] for s in sources if s['is_system_default']), None)
                if def_uid:
                    selected_sources.append(def_uid)

            conflicts_by_num = {}

            resolver_enabled = self.main_window.gsettings_mgr.gsettings.get_boolean("duplicate-resolver-enabled")

            if not force:
                for norm_val, lbl in phones_to_save:
                    if self.main_window.db.is_blocked(norm_val):
                        blocked_conflict = norm_val
                        break

                    if resolver_enabled:
                        with self.eds.cache_lock:
                            candidates = self.eds.lookup_map.get(norm_val, [])
                            for cand in candidates:
                                _c1, _c2, c_source_uid, c_uid = cand
                                if c_uid != self.uid and c_source_uid in selected_sources:
                                    if norm_val not in conflicts_by_num:
                                        conflicts_by_num[norm_val] = []
                                    if c_uid not in conflicts_by_num[norm_val]:
                                        conflicts_by_num[norm_val].append(c_uid)

                    if blocked_conflict:
                        break

            if blocked_conflict:
                self._saving_in_progress = False

                def _do_unblock_and_save():
                    blocked_list = self.main_window.db.get_blocked_numbers()
                    for bid, bnum, _ignored in blocked_list:
                        if normalize_number(bnum) == blocked_conflict:
                            self.main_window.db.remove_blocked_number(bid)
                            break
                    self.on_save(btn, force=True)

                self._confirm_unblock_add(blocked_conflict, _do_unblock_and_save)
                return

            if conflicts_by_num:
                self._saving_in_progress = False
                new_data = self._get_current_contact_data(phones_to_save)

                conflicts = []
                for num, uids in conflicts_by_num.items():
                    conflict_list = [new_data]
                    for c_uid in uids:
                        c_contact = self.eds.cache.get(c_uid)
                        if c_contact:
                            conflict_list.append(c_contact)
                    if len(conflict_list) > 1:
                        conflicts.append((num, conflict_list))

                if conflicts:
                    def on_wizard_done(force_save=False):
                        self.close()
                        if force_save:
                            self.on_save(self.btn_save, force=True)

                    win = DuplicateResolutionWindow(self, conflicts, self.eds, on_wizard_done)
                    win.present()
                    return

            self.main_window.notify_loading(_("Saving contact..."))
            self.btn_save.set_sensitive(False)

            if self.main_window.contacts_view:
                self.main_window.contacts_view.search.set_text("")

            final_vcard = self._generate_vcard_from_ui(phones_to_save)
            fn = self.entry_fn.get_text().strip() or "Unknown"

            original_source = None
            if self.uid and ':' in self.uid:
                original_source = self.uid.split(':', 1)[0]

            run_in_background(
                self._write_contact, final_vcard, phones_to_save,
                selected_sources, original_source,
                on_complete=lambda new_phones: self._on_save_finished(fn, new_phones),
                on_error=self._on_save_failed
            )
        except Exception as e:
            self._saving_in_progress = False
            self.btn_save.set_sensitive(True)
            logger.error(f"[ContactEditor] On save error: {e}")

    def _write_contact(self, final_vcard, phones_to_save, selected_sources, original_source):
        """Write the contact to the selected address books off the main thread."""
        for s_uid in selected_sources:
            if self.uid and s_uid == original_source:
                self.eds.save_contact(final_vcard, self.uid)
            else:
                self.eds.save_contact(final_vcard, source_uid=s_uid)

        new_phones = set()
        for p, _lbl in phones_to_save:
            n = normalize_number(p)
            if n:
                new_phones.add(n)
        return new_phones

    def _on_save_finished(self, fn, new_phones):
        """Update special lists and close the editor after a successful save."""
        try:
            old_phones = set()
            if self.vcard_cache:
                for p, _lbl in self._extract_phones_with_labels():
                    n = normalize_number(p)
                    if n:
                        old_phones.add(n)

            for p in old_phones - new_phones:
                self.main_window.gsettings_mgr.reset_special_list_names(p)

            if self.contact_name != fn:
                for p in new_phones:
                    self.main_window.gsettings_mgr.update_special_list_names(p, fn)
        except Exception as ex:
            logger.error(f"[ContactEditor] Special list update error: {ex}")

        self._saving_in_progress = False
        self.close()

    def _on_save_failed(self, error):
        """Re-enable saving after a failed background save."""
        self._saving_in_progress = False
        self.btn_save.set_sensitive(True)
        self.main_window.notify_error(_("Failed to save contact"))
        logger.error(f"[ContactEditor] Save failed: {error}")

    def _confirm_unblock_add(self, _number_str, on_confirm):
        """Show confirmation to unblock and add to contacts."""
        d = Adw.MessageDialog(
            heading=_("Conflict"),
            body=_("Number can't be on both Blocklist and Contacts.\n\nDo you want to proceed with Unblocking and add the number to Contacts?")
        )
        d.set_transient_for(self)
        d.add_response("cancel", _("Cancel"))
        d.add_response("yes", _("Yes, Add to Contacts"))
        d.set_response_appearance("yes", Adw.ResponseAppearance.SUGGESTED)

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
