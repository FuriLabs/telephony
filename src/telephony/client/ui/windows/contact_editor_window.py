# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Gtk, Adw, Gdk, Gio, GLib
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _

from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.utils.vcard_utils import extract_e164_number, unfold_vcard
from telephony.client.ui.windows.date_time_picker_window import DateTimePicker
from telephony.client.ui.windows.duplicate_resolution_window import DuplicateResolutionWindow
from telephony.client.ui.windows.qr_share_window import QrShareDialog
from telephony.client.ui.widgets.common_widget import (translate_phone_label,
                                                      blocked_state_text, close_sheet,
                                                      close_sheet_page, present_sheet_page,
                                                      present_unblock_choice)
from telephony.shared.constants import BLOCKLIST_SETTLE_MS, CONTACT_SHEET_HEIGHT


class ContactEditor(Adw.NavigationPage):

    def __init__(self, eds_manager, main_window, contact_data=None, number_preset=None,
                 start_mode=None):
        self.btn_save = None
        self.source_toggles = None
        self.switch_fav = None
        self._saving_in_progress = False
        self._destroyed = False
        super().__init__(title=_("Contact Details"))
        self.connect("hidden", self.on_closed)
        self.connect("showing", self.reload_blocked_numbers)
        self.eds = eds_manager
        self.main_window = main_window
        self.uid = contact_data["uid"] if contact_data else None

        self.vcard_cache = ""
        self._vcard_loading = False
        if contact_data:
            if "vcard" in contact_data and contact_data["vcard"]:
                self.vcard_cache = contact_data["vcard"]
            elif self.uid:
                self._vcard_loading = True

        self.contact_name = contact_data["name"] if contact_data else ""

        self.number_preset = number_preset

        self.mode = start_mode or ("VIEW" if self.uid else "EDIT")
        self.edits_a_saved_contact = self.mode == "EDIT" and self.uid is not None
        if self.mode == "EDIT":
            self.set_title(_("Edit Contact") if self.uid else _("New Contact"))

        self.set_size_request(-1, CONTACT_SHEET_HEIGHT)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_child(self.toast_overlay)


        self.phone_entries = []
        self.blocked_entries = {}
        self.phone_view_rows = []
        self.email_entries = []
        self.adv_entries = {}

        self.selected_date = None

        self.refresh_ui()

        if self._vcard_loading:
            run_in_background(self.eds.get_contact_vcard, self.uid,
                              on_complete=self.on_vcard_loaded, on_error=self.on_vcard_load_failed)

    def on_closed(self, _dialog):
        def check():
            if self.get_parent() is None:
                self._destroyed = True
            return False

        GLib.idle_add(check)

    def on_vcard_loaded(self, vcard):
        self._vcard_loading = False
        if self._destroyed:
            return
        if vcard:
            self.vcard_cache = vcard
        if self.mode == "VIEW":
            self.refresh_ui()

    def on_vcard_load_failed(self, error):
        logger.error(f"[ContactEditor] vCard load failed: {error}")
        self._vcard_loading = False
        if self._destroyed:
            return
        if self.mode == "VIEW":
            self.refresh_ui()

    def scrolled_inside(self, widget):
        if isinstance(widget, Gtk.ScrolledWindow):
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = self.scrolled_inside(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    def hold_content_height(self, page):
        scroller = self.scrolled_inside(page)
        if scroller is None:
            return
        scroller.set_propagate_natural_height(False)
        scroller.set_vexpand(True)
        page.set_vexpand(True)

    def clean_vcard_str(self, text):
        if not text:
            return ""
        return text.replace(r"\,", ",").replace(r"\;", ";").replace(r"\n", "\n").strip()

    def refresh_ui(self):
        view = Adw.ToolbarView()

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        view.add_top_bar(header)

        is_read_only_source = False
        refusal = ""
        if self.uid:
            current_source = self.uid.split(":", 1)[0] if ":" in self.uid else None
            if current_source in self.eds.read_only_source_uids():
                is_read_only_source = True
                refusal = self.eds.read_only_reason(current_source)

        if self.mode == "VIEW":
            header.set_show_end_title_buttons(True)

            btn_edit = Gtk.Button(label=_("Edit"))
            btn_edit.add_css_class("suggested-action")
            btn_edit.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_edit_mode_click(b) or False))

            if not is_read_only_source:
                header.pack_end(btn_edit)

            if not self.eds.is_ready or self._vcard_loading:
                btn_edit.set_sensitive(False)

            if self.uid:
                btn_export = Gtk.Button(icon_name="document-save-symbolic")
                btn_export.add_css_class("circular")
                btn_export.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_export_click(b) or False))
                btn_export.set_sensitive(not self._vcard_loading)
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

        if refusal:
            banner = Adw.Banner(title=refusal, revealed=True)
            view.add_top_bar(banner)

        page = Adw.PreferencesPage()
        view.set_content(page)
        self.hold_content_height(page)

        current_phones = self.extract_phones_with_labels()
        current_emails = self.extract_emails_with_labels()

        grp_id = Adw.PreferencesGroup(title=_("Identity"))

        if self.mode == "EDIT":
            self.row_fav = Adw.ActionRow(title=_("Favorite"))
            self.switch_fav = Gtk.Switch(valign=Gtk.Align.CENTER)
            is_fav = self.extract_field("X-FOLKS-FAVOURITE")
            self.switch_fav.set_active(is_fav and is_fav.lower() == "true")
            self.row_fav.add_suffix(self.switch_fav)
            grp_id.add(self.row_fav)

        if self.mode == "EDIT":
            self.entry_fn = Adw.EntryRow(title=_("Full Name"))
            self.entry_fn.set_text(self.contact_name)

            grp_id.add(self.entry_fn)
        else:
            is_fav_val = self.extract_field("X-FOLKS-FAVOURITE")
            is_fav = bool(is_fav_val and is_fav_val.lower() == "true")
            if self.contact_name:
                row_name = self.create_view_row(_("Name"), self.contact_name)
                if is_fav:
                    row_name.add_suffix(Gtk.Image.new_from_icon_name("starred-symbolic"))
                grp_id.add(row_name)
            elif is_fav:
                grp_id.add(self.create_view_row(_("Favorite"), _("Yes")))

        page.add(grp_id)

        self.add_address_books_group(page)

        self.grp_phones = Adw.PreferencesGroup(title=_("Phone Numbers"))
        if self.mode == "EDIT":
            self.phone_entries = []

            if current_phones:
                phones = current_phones
            elif self.number_preset:
                phones = [(self.number_preset, "Mobile")]
            else:
                phones = [("", "Mobile")]

            for p_num, p_label in phones:
                self.add_phone_row(p_num, p_label)

            self.grp_phones.set_header_suffix(self.group_add_button(_("Add Number"), lambda: self.add_phone_row("", "Mobile")))
            self.reload_blocked_numbers()
        else:
            if current_phones:
                self.phone_view_rows = []
                for num, lbl in current_phones:
                    self.grp_phones.add(self.phone_view_row(num, lbl))
                self.reload_blocked_numbers()
            else:
                self.grp_phones.set_visible(False)
        page.add(self.grp_phones)

        self.grp_emails = Adw.PreferencesGroup(title=_("Email Addresses"))
        if self.mode == "EDIT":
            self.email_entries = []

            emails = current_emails if current_emails else [("", "Home")]
            for e_addr, e_label in emails:
                self.add_email_row(e_addr, e_label)

            self.grp_emails.set_header_suffix(self.group_add_button(_("Add Email"), lambda: self.add_email_row("", "Home")))
        else:
            if current_emails:
                for addr, lbl in current_emails:
                    self.grp_emails.add(self.email_view_row(addr, lbl))
            else:
                self.grp_emails.set_visible(False)
        page.add(self.grp_emails)

        grp_adv = Adw.PreferencesGroup()
        exp_adv = Adw.ExpanderRow(title=_("Additional Details"))
        grp_adv.add(exp_adv)

        text_fields = [
            ("ORG", _("Organization")), ("TITLE", _("Job Title")),
            ("ADR", _("Address")), ("NOTE", _("Notes")),
            ("URL", _("Website"))
        ]

        has_adv_data = False
        self.adv_entries = {}

        for key, lbl in text_fields:
            val = self.extract_field(key)
            if self.mode == "EDIT":
                row = Adw.EntryRow(title=lbl)
                if val:
                    row.set_text(val)
                    has_adv_data = True

                exp_adv.add_row(row)
                self.adv_entries[key] = row
            else:
                if val:
                    exp_adv.add_row(self.create_view_row(lbl, val))
                    has_adv_data = True

        bday_val = self.extract_field("BDAY")
        anniv_val = self.extract_field("ANNIVERSARY")

        if self.mode == "EDIT":
            self.bday_row = self.create_date_editor(_("Birthday"), bday_val)
            self.anniv_row = self.create_date_editor(_("Anniversary"), anniv_val)
            exp_adv.add_row(self.bday_row)
            exp_adv.add_row(self.anniv_row)
            if bday_val or anniv_val:
                has_adv_data = True
        else:
            if bday_val:
                exp_adv.add_row(self.create_view_row(_("Birthday"), bday_val))
                has_adv_data = True
            if anniv_val:
                exp_adv.add_row(self.create_view_row(_("Anniversary"), anniv_val))
                has_adv_data = True

        exp_adv.set_expanded(has_adv_data and self.mode == "EDIT")
        if not has_adv_data and self.mode == "VIEW":
            grp_adv.set_visible(False)
        page.add(grp_adv)

        if self.uid:
            grp_share = Adw.PreferencesGroup()
            row_qr = Adw.ActionRow(title=_("Share as QR code"), activatable=True)
            row_qr.connect("activated", lambda r: GLib.idle_add(lambda: self.share_qr() or False))
            grp_share.add(row_qr)
            page.add(grp_share)

        if self.uid and not is_read_only_source:
            grp_danger = Adw.PreferencesGroup()
            row_del = Adw.ActionRow(title=_("Delete Contact"), activatable=True)
            row_del.add_css_class("error")
            row_del.connect("activated", lambda r: GLib.idle_add(lambda: self.on_delete(None) or False))
            if not self.eds.is_ready:
                row_del.set_sensitive(False)
            grp_danger.add(row_del)
            page.add(grp_danger)

        self.toast_overlay.set_child(view)

    def add_address_books_group(self, page):
        grp = Adw.PreferencesGroup(title=_("Address Books"))
        page.add(grp)

        self.source_toggles = {}
        sources = self.eds.get_sources_info()
        enabled = [s for s in sources if s["enabled"]]

        current_source = None
        if self.uid and ":" in self.uid:
            current_source = self.uid.split(":", 1)[0]

        if self.mode == "VIEW":
            found = False
            for s in enabled:
                if s["uid"] == current_source:
                    row = Adw.ActionRow(title=s["name"])
                    row.add_suffix(Gtk.Image(icon_name="object-select-symbolic"))
                    grp.add(row)
                    found = True
            if not found and current_source:
                grp.add(Adw.ActionRow(title=_("Unknown Source")))
        else:
            default_source = next((s["uid"] for s in enabled if s["is_system_default"]), None)

            read_only_uids = self.eds.read_only_source_uids()
            for s in enabled:
                if not self.uid and s["uid"] in read_only_uids:
                    continue
                row = Adw.SwitchRow(title=s["name"])
                is_active = False
                if self.uid:
                    if s["uid"] == current_source:
                        is_active = True
                    row.set_sensitive(False)
                else:
                    if s["uid"] == default_source:
                        is_active = True
                row.set_active(is_active)
                grp.add(row)
                self.source_toggles[s["uid"]] = row

    def create_date_editor(self, title, current_val):
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

        def clear_date():
            row._selected_date = None
            lbl_date.set_label(_("Select Date"))
            lbl_date.add_css_class("dim-label")
            return False

        btn_edit.connect("clicked", lambda b: GLib.idle_add(lambda: self.open_date_picker(title, row, lbl_date) or False))
        btn_clear.connect("clicked", lambda b: GLib.idle_add(clear_date))

        return row

    def open_date_picker(self, title, row, lbl_widget):
        def on_picked(date):
            row._selected_date = date
            lbl_widget.set_label(date.format("%Y-%m-%d"))
            lbl_widget.remove_css_class("dim-label")
            if (self.btn_save is not None):
                self.btn_save.set_sensitive(True)

        DateTimePicker(
            parent=self.get_root(),
            title=_("Select {title}").format(title=title),
            initial_date=row._selected_date,
            include_time=False,
            on_confirm=on_picked
        )

    def create_view_row(self, title, value, copy_text=None):
        row = Adw.ActionRow(title=title, subtitle=value)
        value = copy_text if copy_text is not None else value
        btn_copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        btn_copy.set_tooltip_text(_("Copy {title}").format(title=title))
        btn_copy.connect("clicked", lambda b: GLib.idle_add(lambda: self.copy_to_clipboard(value) or False))
        row.add_suffix(btn_copy)
        return row

    def copy_to_clipboard(self, text):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
        self.toast_overlay.add_toast(Adw.Toast.new(_("Copied to clipboard")))

    def on_edit_mode_click(self, btn):
        editor = ContactEditor(
            self.eds, self.main_window,
            contact_data={"uid": self.uid, "name": self.contact_name,
                          "vcard": self.vcard_cache},
            start_mode="EDIT")
        present_sheet_page(self.main_window, editor)
        editor.set_size_request(-1, CONTACT_SHEET_HEIGHT)

    def on_cancel_edit(self, btn):
        GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)

    def leave_after_saving(self):
        if self.edits_a_saved_contact:
            close_sheet(self.get_root())
            return
        close_sheet_page(self.get_root())

    def add_phone_row(self, text="", label="Mobile"):
        label_keys = ["Mobile", "Work", "Home", "Fax", "Other"]
        display_labels = [_("Mobile"), _("Work"), _("Home"), _("Fax"), _("Other")]
        self.add_field_row(self.grp_phones, self.phone_entries,
                            label_keys, display_labels, label, text,
                            _("Phone"), Gtk.InputPurpose.PHONE, blockable=True)

    def blocked_entry(self, number):
        norm = normalize_number((number or "").strip())
        return self.blocked_entries.get(norm) if norm else None

    def show_number_state(self, button, number, label, row=None, type_row=None):
        entry = self.blocked_entry(number)
        button.set_css_classes(["flat", "circular"] + (["error"] if entry else []))
        button.set_tooltip_text(
            _("Unblock this number") if entry else _("Block this number"))
        button.set_sensitive(bool((number or "").strip()))

        state = blocked_state_text(entry)
        said = f"{label} · {state}" if state else label
        if row is not None:
            row.set_subtitle(said)
        if type_row is not None:
            type_row.set_subtitle(said)

    def on_block_clicked(self, number):
        number = (number or "").strip()
        if not number:
            return

        entry = self.blocked_entry(number)
        if entry is None:
            self.main_window.present_blocklist_editor(number_preset=number)
            return

        present_unblock_choice(
            self.main_window, self.main_window.daemon, entry, None,
            lambda: GLib.timeout_add(BLOCKLIST_SETTLE_MS, self.reload_blocked_numbers))

    def show_entry_row_state(self, row):
        label = row.display_labels[row.type_row._selected_index]
        self.show_number_state(row.block_button, row.get_text(), label,
                               type_row=row.type_row)

    def reload_blocked_numbers(self, *_args):
        def task():
            return {normalize_number(entry["number"]): dict(entry)
                    for entry in self.main_window.db.get_blocked_numbers()}

        run_in_background(task, on_complete=self.apply_blocked_numbers)
        return False

    def apply_blocked_numbers(self, blocked):
        self.blocked_entries = blocked or {}
        for row, _type_row in self.phone_entries:
            self.show_entry_row_state(row)
        for row, button, number, label in self.phone_view_rows:
            self.show_number_state(button, number, label, row=row)

    def add_email_row(self, text="", label="Home"):
        label_keys = ["Home", "Work", "Other"]
        display_labels = [_("Home"), _("Work"), _("Other")]
        self.add_field_row(self.grp_emails, self.email_entries,
                            label_keys, display_labels, label, text,
                            _("Email"), Gtk.InputPurpose.EMAIL)

    def add_field_row(self, group, entries_list, label_keys, display_labels, label, text, title, purpose,
                      blockable=False):
        row = Adw.EntryRow(title=title)
        row.set_text(text)
        row.set_input_purpose(purpose)

        type_row = Adw.ExpanderRow(title=_("Type"))
        type_row._selected_index = label_keys.index(label) if label in label_keys else 0
        type_row._option_checks = []
        type_row.set_subtitle(display_labels[type_row._selected_index])

        def pick_type(index):
            type_row._selected_index = index
            type_row.set_subtitle(display_labels[index])
            for i, check in enumerate(type_row._option_checks):
                check.set_visible(i == index)
            type_row.set_expanded(False)
            if blockable:
                self.show_entry_row_state(row)

        for i, name in enumerate(display_labels):
            option = Adw.ActionRow(title=name, activatable=True)
            check = Gtk.Image.new_from_icon_name("object-select-symbolic")
            check.set_visible(i == type_row._selected_index)
            option.add_suffix(check)
            option.connect("activated", lambda r, i=i: GLib.idle_add(lambda: pick_type(i) or False))
            type_row.add_row(option)
            type_row._option_checks.append(check)

        btn_remove = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat", "circular"], valign=Gtk.Align.CENTER)
        btn_remove.connect("clicked", lambda b: GLib.idle_add(
            lambda: [group.remove(row), group.remove(type_row), entries_list.remove((row, type_row))] and False))

        if blockable:
            row.type_row = type_row
            row.display_labels = display_labels
            row.block_button = Gtk.Button(icon_name="action-unavailable-symbolic",
                                          valign=Gtk.Align.CENTER)
            row.block_button.connect("clicked", lambda b: GLib.idle_add(
                lambda: self.on_block_clicked(row.get_text()) or False))
            row.add_suffix(row.block_button)

            row.connect("changed", lambda _e: self.show_entry_row_state(row))
            self.show_entry_row_state(row)

        row.add_suffix(btn_remove)
        group.add(row)
        group.add(type_row)
        entries_list.append((row, type_row))

    def group_add_button(self, tooltip, callback):
        btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER)
        btn.set_tooltip_text(tooltip)
        btn.connect("clicked", lambda b: GLib.idle_add(lambda: callback() or False))
        return btn

    def phone_view_row(self, number, label):
        shown_label = translate_phone_label(label)
        row = self.create_view_row(number, shown_label, copy_text=number)

        btn_block = Gtk.Button(icon_name="action-unavailable-symbolic",
                               valign=Gtk.Align.CENTER)
        btn_block.connect("clicked", lambda b: GLib.idle_add(
            lambda: self.on_block_clicked(number) or False))
        row.add_suffix(btn_block)

        btn_msg = Gtk.Button(icon_name="mail-message-new-symbolic", valign=Gtk.Align.CENTER)
        btn_msg.add_css_class("circular")
        btn_msg.add_css_class("secondary-btn")
        btn_msg.set_size_request(34, 34)
        btn_msg.connect("clicked", lambda b: GLib.idle_add(lambda: self.message_number(number) or False))

        btn_call = Gtk.Button(icon_name="call-start-symbolic", valign=Gtk.Align.CENTER)
        btn_call.add_css_class("circular")
        btn_call.add_css_class("call-btn-small")
        btn_call.set_size_request(34, 34)
        btn_call.set_sensitive(bool(self.main_window.ofono and self.main_window.ofono.is_dialing_available()))
        btn_call.connect("clicked", lambda b: GLib.idle_add(lambda: self.call_number(number) or False))

        self.phone_view_rows.append((row, btn_block, number, shown_label))
        self.show_number_state(btn_block, number, shown_label, row=row)

        row.add_suffix(btn_msg)
        row.add_suffix(btn_call)
        return row

    def email_view_row(self, address, label):
        row = self.create_view_row(address, translate_phone_label(label), copy_text=address)
        btn_mail = Gtk.Button(icon_name="mail-send-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        btn_mail.connect("clicked", lambda b: GLib.idle_add(lambda: self.open_mailto(address) or False))
        row.add_suffix(btn_mail)
        return row

    def message_number(self, number):
        close_sheet_page(self.get_root())
        self.main_window.present_chat(normalize_number(number))

    def call_number(self, number):
        close_sheet_page(self.get_root())
        self.main_window.start_call(number)

    def open_mailto(self, address):
        try:
            Gio.AppInfo.launch_default_for_uri(f"mailto:{address}", None)
        except Exception as e:
            logger.warning(f"[ContactEditor] Could not open mail client: {e}")
            self.toast_overlay.add_toast(Adw.Toast.new(_("No email app available")))

    def extract_phones_with_labels(self):
        if not self.vcard_cache:
            return []
        res = []

        unfolded = unfold_vcard(self.vcard_cache)

        for line in unfolded.splitlines():
            if line.startswith("TEL"):
                parts = line.split(":", 1)
                if len(parts) > 1:
                    raw_number = self.clean_vcard_str(parts[1])
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

    def extract_emails_with_labels(self):
        if not self.vcard_cache:
            return []
        res = []
        unfolded = unfold_vcard(self.vcard_cache)
        for line in unfolded.splitlines():
            if line.startswith("EMAIL"):
                parts = line.split(":", 1)
                if len(parts) > 1:
                    email = self.clean_vcard_str(parts[1])
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

    def extract_field(self, key):
        if not self.vcard_cache:
            return ""
        unfolded = unfold_vcard(self.vcard_cache)
        for line in unfolded.splitlines():
            if line.startswith(f"{key}:") or line.startswith(f"{key};"):
                try:
                    raw_val = line.split(":", 1)[1].replace(";", " ")
                    return self.clean_vcard_str(raw_val)
                except Exception as e:
                    logger.debug(f"[ContactEditor] Field extract error: {e}")
                    return ""
        return None

    def on_export_click(self, btn):
        dialog = Gtk.FileChooserNative(title=_("Export Contact"), transient_for=self.get_root(), action=Gtk.FileChooserAction.SAVE)
        safe_name = "".join(x for x in self.contact_name if x.isalnum()) or "contact"
        dialog.set_current_name(f"{safe_name}.vcf")
        dialog.connect("response", self.on_export_response)
        dialog.show()

    def on_export_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            f = dialog.get_file()
            try:
                with open(f.get_path(), "w", encoding="utf-8") as f_out:
                    f_out.write(self.vcard_cache)
            except Exception as e:
                logger.error(e)
        GLib.idle_add(lambda: dialog.destroy() or False)

    def share_qr(self):
        name = self.contact_name or self.extract_field("FN") or ""
        lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{name}"]
        lines.extend(f"TEL;TYPE={label}:{number}"
                     for number, label in self.extract_phones_with_labels())
        lines.extend(f"EMAIL;TYPE={label}:{address}"
                     for address, label in self.extract_emails_with_labels())
        lines.append("END:VCARD")

        dialog = QrShareDialog(name, "\n".join(lines))
        if dialog.matrix is None:
            self.toast_overlay.add_toast(Adw.Toast.new(_("Could not create the QR code")))
            return
        present_sheet_page(self.get_root(), dialog)

    def on_delete(self, btn):
        self.main_window.confirm_action(
            _("Delete Contact"),
            _("{name} will be removed from your address book.").format(
                name=self.contact_name or _("This contact")),
            self.do_delete)

    def do_delete(self):
        if not self.uid:
            GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)
            return

        contact = self.eds.cache.get(self.uid)
        numbers = []
        if contact and "phones" in contact:
            numbers = [p[0] for p in contact["phones"]]

        if self.main_window.contacts_view:
            self.main_window.contacts_view.search.set_text("")

        def done(_ok):
            for num in numbers:
                self.main_window.gsettings_mgr.reset_special_list_names(num)
            close_sheet_page(self.get_root())

        def deleted(_ok):
            if not numbers:
                done(True)
                return
            self.main_window.daemon.update_history_names(numbers, None, done)

        self.main_window.daemon.delete_contact(self.uid, deleted)

    def generate_vcard_from_ui(self, phones_to_save):
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
        for entry, type_row in self.email_entries:
            e_val = entry.get_text().strip()
            if e_val:
                lbl = label_options_email[type_row._selected_index]
                v_type = {"Home": "HOME", "Work": "WORK", "Other": "OTHER"}.get(lbl, "HOME")
                lines.append(f"EMAIL;TYPE={v_type}:{e_val}")

        for key, row in self.adv_entries.items():
            val = row.get_text().strip()
            if val:
                lines.append(f"{key}:{val}")

        if self.bday_row._selected_date:
            lines.append(f"BDAY:{self.bday_row._selected_date.format("%Y-%m-%d")}")

        if self.anniv_row._selected_date:
            lines.append(f"ANNIVERSARY:{self.anniv_row._selected_date.format("%Y-%m-%d")}")

        if (self.switch_fav is not None) and self.switch_fav.get_active():
            lines.append("X-FOLKS-FAVOURITE:true")

        lines.append("END:VCARD")
        return "\n".join(lines)

    def get_current_contact_data(self, phones_to_save):
        vcard = self.generate_vcard_from_ui(phones_to_save)

        emails = []
        label_options_email = ["Home", "Work", "Other"]
        for entry, type_row in self.email_entries:
            e_val = entry.get_text().strip()
            if e_val:
                lbl = label_options_email[type_row._selected_index]
                emails.append((e_val, lbl))

        return {
            "uid": self.uid,
            "name": self.entry_fn.get_text().strip() or "Unknown",
            "phones": phones_to_save,
            "emails": emails,
            "vcard": vcard,
            "is_fav": (self.switch_fav is not None) and self.switch_fav.get_active()
        }

    def on_save(self, btn, force=False):
        if not force:
            if self._saving_in_progress:
                return
            self._saving_in_progress = True

        try:
            phones_to_save = []
            label_options_phone = ["Mobile", "Work", "Home", "Fax", "Other"]

            for entry, type_row in self.phone_entries:
                raw_val = entry.get_text().strip()
                if raw_val:
                    norm_val = normalize_number(raw_val)
                    phones_to_save.append((norm_val, label_options_phone[type_row._selected_index]))

            selected_sources = []
            if (self.source_toggles is not None):
                for uid, row in self.source_toggles.items():
                    if row.get_active():
                        selected_sources.append(uid)

            if not selected_sources:
                sources = self.eds.get_sources_info()
                def_uid = next((s["uid"] for s in sources if s["is_system_default"]), None)
                if def_uid:
                    selected_sources.append(def_uid)

            if force:
                self.proceed_with_save(phones_to_save, selected_sources)
                return

            resolver_enabled = self.main_window.gsettings_mgr.gsettings.get_boolean("duplicate-resolver-enabled")

            run_in_background(
                self.run_save_prechecks, phones_to_save, selected_sources, resolver_enabled,
                on_complete=lambda result: self.on_prechecks_done(result, phones_to_save, selected_sources),
                on_error=self.on_precheck_failed
            )
        except Exception as e:
            self._saving_in_progress = False
            if (self.btn_save is not None):
                self.btn_save.set_sensitive(True)
            logger.error(f"[ContactEditor] On save error: {e}")

    def run_save_prechecks(self, phones_to_save, selected_sources, resolver_enabled):
        conflicts_by_num = {}

        for norm_val, _lbl in phones_to_save:
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

        return conflicts_by_num

    def on_prechecks_done(self, result, phones_to_save, selected_sources):
        if self._destroyed:
            self._saving_in_progress = False
            return

        try:
            conflicts_by_num = result

            if conflicts_by_num:
                new_data = self.get_current_contact_data(phones_to_save)

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
                    self._saving_in_progress = False

                    def on_wizard_done(force_save=False):
                        if force_save:
                            self.on_save(self.btn_save, force=True)

                    page = DuplicateResolutionWindow(conflicts, self.eds, self.main_window.daemon, on_wizard_done)
                    present_sheet_page(self.get_root(), page)
                    return

            self.proceed_with_save(phones_to_save, selected_sources)
        except Exception as e:
            self._saving_in_progress = False
            if (self.btn_save is not None):
                self.btn_save.set_sensitive(True)
            logger.error(f"[ContactEditor] On save error: {e}")

    def on_precheck_failed(self, error):
        self._saving_in_progress = False
        logger.error(f"[ContactEditor] Save pre-check failed: {error}")
        if self._destroyed:
            return
        self.toast_overlay.add_toast(Adw.Toast.new(_("Failed to save contact")))

    def proceed_with_save(self, phones_to_save, selected_sources):
        try:
            self.btn_save.set_sensitive(False)

            if self.main_window.contacts_view:
                self.main_window.contacts_view.search.set_text("")

            final_vcard = self.generate_vcard_from_ui(phones_to_save)
            fn = self.entry_fn.get_text().strip() or "Unknown"

            original_source = None
            if self.uid and ":" in self.uid:
                original_source = self.uid.split(":", 1)[0]

            run_in_background(
                self.write_contact, final_vcard, phones_to_save,
                selected_sources, original_source,
                on_complete=lambda new_phones: self.on_save_finished(fn, new_phones),
                on_error=lambda error: self.on_save_failed(error, fn, final_vcard)
            )
            self._saving_in_progress = False
            self.leave_after_saving()
        except Exception as e:
            self._saving_in_progress = False
            self.btn_save.set_sensitive(True)
            logger.error(f"[ContactEditor] On save error: {e}")

    def write_contact(self, final_vcard, phones_to_save, selected_sources, original_source):
        if not selected_sources:
            raise RuntimeError("No address book selected for the contact")

        failures = 0
        reason = ""
        for s_uid in selected_sources:
            if self.uid and s_uid == original_source:
                ok, why = self.main_window.daemon.save_contact(final_vcard, uid=self.uid)
            else:
                ok, why = self.main_window.daemon.save_contact(final_vcard, source_uid=s_uid)
            if not ok:
                failures += 1
                reason = why or reason

        if failures:
            raise RuntimeError(reason or "write-failed")

        new_phones = set()
        for p, _lbl in phones_to_save:
            n = normalize_number(p)
            if n:
                new_phones.add(n)
        return new_phones

    def on_save_finished(self, fn, new_phones):
        try:
            old_phones = set()
            if self.vcard_cache:
                for p, _lbl in self.extract_phones_with_labels():
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


    def on_save_failed(self, error, fn, final_vcard):
        logger.error(f"[ContactEditor] Save failed: {error}")
        try:
            editor = ContactEditor(self.eds, self.main_window,
                                   contact_data={"uid": self.uid, "name": fn, "vcard": final_vcard},
                                   start_mode="EDIT")
            present_sheet_page(self.main_window, editor)
            messages = {"read-only": _("This address book is read-only"),
                        "no-writable-book": _("No writable address book available")}
            editor.toast_overlay.add_toast(Adw.Toast.new(
                messages.get(str(error), _("Failed to save contact"))))
        except Exception as e:
            logger.error(f"[ContactEditor] Could not reopen editor after failure: {e}")
            self.main_window.notify_error(_("Failed to save contact"))

    def show_error(self, title, msg):
        self.toast_overlay.add_toast(Adw.Toast.new(msg))
