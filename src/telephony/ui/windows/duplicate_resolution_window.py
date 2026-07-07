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

from ...backend.utils.thread_utils import run_in_background

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from loguru import logger
from gettext import gettext as _

from ...backend.utils.phone_utils import normalize_number
from ...backend.utils.vcard_utils import unfold_vcard


class DuplicateResolutionWindow(Adw.Window):
    """
    Window to resolve contact duplicates.
    Shows conflicts one by one or allows merging all.
    """

    def __init__(self, parent_window, conflicts, eds_manager, on_done_callback):
        super().__init__(title=_("Duplicate Contact"))
        self.set_transient_for(parent_window)
        self.set_modal(True)
        self.set_default_size(500, 600)

        self.conflicts = conflicts
        self.eds = eds_manager
        self.on_done = on_done_callback
        self.current_index = 0

        if not self.eds.is_ready:
            logger.warning("[DuplicateResolutionWindow] Initialized while EDS is not ready!")

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.set_content(self.stack)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self.main_box, "main")

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        self.btn_cancel = Gtk.Button(label=_("Cancel"))
        self.btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_cancel(b) or False))
        header.pack_start(self.btn_cancel)

        self.btn_merge_all = Gtk.Button(label=_("Merge all"))
        self.btn_merge_all.add_css_class("suggested-action")
        self.btn_merge_all.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_merge_all(b) or False))
        header.pack_end(self.btn_merge_all)

        self.main_box.append(header)

        self.lbl_status = Gtk.Label()
        self.lbl_status.set_margin_top(12)
        self.lbl_status.set_margin_bottom(12)
        self.main_box.append(self.lbl_status)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_vexpand(True)
        self.main_box.append(self.scroll)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.content_box.set_margin_top(12)
        self.content_box.set_margin_bottom(12)
        self.content_box.set_margin_start(12)
        self.content_box.set_margin_end(12)
        self.scroll.set_child(self.content_box)

        self.load_page = Adw.StatusPage()
        self.load_page.set_title(_("Merging Contacts..."))

        box_spin = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box_spin.set_halign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(64, 64)
        box_spin.append(self.spinner)

        self.lbl_progress = Gtk.Label(label="")
        box_spin.append(self.lbl_progress)

        self.load_page.set_child(box_spin)
        self.stack.add_named(self.load_page, "loading")

        self.show_current_conflict()

    def update_status(self):
        remaining = len(self.conflicts) - self.current_index
        self.lbl_status.set_text(_("Remaining conflicts: {count}").format(count=remaining))

    def show_current_conflict(self):
        child = self.content_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.content_box.remove(child)
            child = next_child

        if self.current_index >= len(self.conflicts):
            self.close()
            if self.on_done:
                self.on_done()
            return

        self.update_status()

        number, contacts = self.conflicts[self.current_index]

        lbl_info = Gtk.Label(label=_("{number} is saved to contacts:").format(number=number))
        lbl_info.add_css_class("heading")
        lbl_info.set_wrap(True)
        lbl_info.set_justify(Gtk.Justification.CENTER)
        self.content_box.append(lbl_info)

        for contact in contacts:
            source_name = ""
            source_uid = contact.get('source_uid')
            if source_uid:
                sources = self.eds.get_sources_info()
                s_info = next((s for s in sources if s['uid'] == source_uid), None)
                if s_info:
                    source_name = s_info['name']

            card_title = _("Address Book: {name}").format(name=source_name) if source_name else ""
            card = Adw.PreferencesGroup(title=card_title)

            name = contact.get('name', _("Unknown"))

            row = Adw.ActionRow(title=name)
            row.set_title_lines(1)

            phones = contact.get('phones', [])
            p_str = ", ".join([f"{p[0]} ({p[1]})" for p in phones])
            if p_str:
                row.set_subtitle(p_str)

            card.add(row)

            emails = contact.get('emails', [])
            if emails:
                e_str = ", ".join([e[0] for e in emails])
                row_email = Adw.ActionRow(title=_("Email Addresses"), subtitle=e_str)
                card.add(row_email)

            btn_keep = Gtk.Button(label=_("Keep this, Remove duplicates"))
            btn_keep.add_css_class("suggested-action")
            btn_keep.set_margin_top(6)
            btn_keep.connect("clicked", lambda b, c=contact: self.resolve_keep(c))
            card.add(btn_keep)

            btn_merge = Gtk.Button(label=_("Keep this, Merge duplicates"))
            btn_merge.add_css_class("suggested-action")
            btn_merge.set_margin_top(6)
            btn_merge.connect("clicked", lambda b, c=contact: self.resolve_merge(c))
            card.add(btn_merge)

            self.content_box.append(card)

    def _resolve_in_thread(self, target_contact, logic_func):
        """
        Run the resolution logic in a background thread to keep UI responsive.
        """
        self.stack.set_visible_child_name("loading")
        self.spinner.start()

        def task():
            try:
                logic_func(target_contact)
                GLib.idle_add(self._on_resolve_complete)
            except Exception as e:
                logger.error(f"Resolution error: {e}")
                GLib.idle_add(self._on_resolve_complete)

        run_in_background(task)

    def _on_resolve_complete(self):
        """Called on main thread when single resolution is done."""
        self.spinner.stop()
        self.stack.set_visible_child_name("main")
        self.current_index += 1
        self.show_current_conflict()

    def resolve_keep(self, kept_contact):
        """User clicked 'Keep this'."""
        self._resolve_in_thread(kept_contact, self._logic_keep_strict)

    def resolve_merge(self, kept_contact):
        """User clicked 'Merge'."""
        self._resolve_in_thread(kept_contact, self._logic_merge_smart)

    def _logic_keep_strict(self, kept_contact):
        """
        Strict Keep: Delete all others. Do NOT merge data.
        Target contact remains untouched.
        """
        number, contacts = self.conflicts[self.current_index]
        others = [c for c in contacts if c['uid'] != kept_contact['uid']]
        uids_to_remove = [o['uid'] for o in others]

        self.eds.delete_contacts(uids_to_remove)

    def _logic_merge_smart(self, kept_contact):
        """
        Smart Merge: Pull unique data from others into kept_contact, then delete others.
        """
        number, contacts = self.conflicts[self.current_index]
        others = [c for c in contacts if c['uid'] != kept_contact['uid']]

        new_vcard = self._merge_contact_data(kept_contact, others)

        self.eds.save_contact(new_vcard, uid=kept_contact['uid'])

        uids_to_remove = [o['uid'] for o in others]
        self.eds.delete_contacts(uids_to_remove)

    def _merge_contact_data(self, target, others):
        """Merge data from others into target and return vCard string."""
        target_vcard = target.get('vcard', '')
        if not target_vcard and target['uid']:
            target_vcard = self.eds.get_contact_vcard(target['uid'])

        lines = unfold_vcard(target_vcard).splitlines()
        lines = [line for line in lines if line.strip().upper() != "END:VCARD" and line.strip()]

        existing_numbers = set()
        existing_emails = set()

        for line in lines:
            if line.startswith("TEL"):
                parts = line.split(":", 1)
                if len(parts) > 1:
                    existing_numbers.add(normalize_number(parts[1]))
            elif line.startswith("EMAIL"):
                parts = line.split(":", 1)
                if len(parts) > 1:
                    existing_emails.add(parts[1].strip().lower())

        for other in others:
            v = other.get('vcard', '')
            if not v:
                v = self.eds.get_contact_vcard(other['uid'])
            if not v:
                continue

            o_lines = unfold_vcard(v).splitlines()
            for line in o_lines:
                if line.startswith("TEL"):
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        norm = normalize_number(parts[1])
                        if norm and norm not in existing_numbers:
                            lines.append(line)
                            existing_numbers.add(norm)
                elif line.startswith("EMAIL"):
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        val = parts[1].strip().lower()
                        if val and val not in existing_emails:
                            lines.append(line)
                            existing_emails.add(val)
                elif any(line.startswith(p) for p in ["ADR", "NOTE", "URL", "BDAY", "ANNIVERSARY", "ORG", "TITLE"]):
                    if line not in lines:
                        lines.append(line)

        lines.append("END:VCARD")
        return "\n".join(lines)

    def on_merge_all(self, btn):
        self.stack.set_visible_child_name("loading")
        self.spinner.start()
        run_in_background(self._merge_all_thread)

    def _merge_all_thread(self):
        total = len(self.conflicts) - self.current_index
        processed = 0

        try:
            while self.current_index < len(self.conflicts):
                number, contacts = self.conflicts[self.current_index]

                best = contacts[0]
                max_score = -1

                for c in contacts:
                    score = 0
                    if c.get('is_fav'):
                        score += 10
                    if c.get('emails'):
                        score += 5
                    if len(c.get('name', '')) > len(best.get('name', '')):
                        score += 1

                    if score > max_score:
                        max_score = score
                        best = c

                self._logic_merge_smart(best)

                self.current_index += 1
                processed += 1

                GLib.idle_add(self._update_progress, processed, total)
        except Exception as e:
            logger.error(f"Merge all error: {e}")

        GLib.idle_add(self._finish_merge)

    def _update_progress(self, current, total):
        self.lbl_progress.set_text(_("Merging {current}/{total}...").format(current=current, total=total))

    def _finish_merge(self):
        self.spinner.stop()
        self.close()
        if self.on_done:
            self.on_done()

    def on_cancel(self, btn):
        self.close()
        if self.on_done:
            self.on_done()
