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

from telephony.shared.utils.thread_utils import run_in_background

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _

from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.vcard_utils import unfold_vcard
from telephony.client.ui.widgets.common_widget import (present_sheet_page, on_sheet_closed,
                                                      close_sheet_page)
from telephony.shared.constants import CONTACT_SHEET_HEIGHT


class DuplicateResolutionWindow(Adw.NavigationPage):
    """Page resolving contact duplicates, one conflict at a time.

    Lives inside a navigation stack so the contact editor can push it
    into the sheet it already owns instead of stacking a second sheet
    on top of it. Leaving pops back to the editor with its fields
    untouched, which is what makes the forced save still find them.
    """

    def __init__(self, conflicts, eds_manager, daemon, on_done_callback):
        super().__init__(title=_("Duplicate Contact"))

        self.conflicts = conflicts
        self.eds = eds_manager
        self.daemon = daemon
        self.on_done = on_done_callback
        self.current_index = 0
        self.keep_pending_edit = False
        self._done_called = False

        if not self.eds.is_ready:
            logger.warning("[DuplicateResolutionWindow] Initialized while EDS is not ready!")

        self.connect("hidden", lambda p: self.call_done())

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        header = Adw.HeaderBar()

        self.btn_merge_all = Gtk.Button(label=_("Merge all"))
        self.btn_merge_all.add_css_class("suggested-action")
        self.btn_merge_all.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_merge_all(b) or False))
        header.pack_end(self.btn_merge_all)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.stack)
        self.set_child(view)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self.main_box, "main")

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
            self.leave()
            return

        self.update_status()

        number, contacts = self.conflicts[self.current_index]

        lbl_info = Gtk.Label(label=_("{number} is saved to contacts:").format(number=number))
        lbl_info.add_css_class("heading")
        lbl_info.set_wrap(True)
        lbl_info.set_justify(Gtk.Justification.CENTER)
        self.content_box.append(lbl_info)

        sources = self.eds.get_sources_info()

        for contact in contacts:
            source_name = ""
            source_uid = contact.get('source_uid')
            if source_uid:
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

    def present_standalone(self, parent):
        """Show the page as its own sheet, for entries with no editor.

        A closing dialog does not hide its pages, so the sheet reports
        the completion itself, otherwise dismissing it would leave the
        duplicates banner claiming conflicts that were already handled.
        """
        self.set_size_request(-1, CONTACT_SHEET_HEIGHT)
        window = parent.get_root()
        present_sheet_page(window, self)
        on_sheet_closed(window, self.call_done)

    def leave(self):
        """Leave the page, however the resolution finished.

        Pushed by the contact editor the page pops back to it, but
        opened from the duplicates banner it is the only page in its
        stack, where popping does nothing and the sheet has to close.
        """
        nav = self.get_ancestor(Adw.NavigationView)
        if nav and nav.get_visible_page() is self and nav.get_previous_page(self):
            nav.pop()
            return
        close_sheet_page(self.get_root())

    def call_done(self):
        """Invoke the completion callback, forwarding a pending editor save.

        Every exit runs through the page being hidden, whether that is
        the back button, the back gesture or a finished merge, so the
        callback is guarded to fire exactly once.
        """
        if self._done_called:
            return
        self._done_called = True
        if not self.on_done:
            return
        if self.keep_pending_edit:
            self.on_done(force_save=True)
        else:
            self.on_done()

    def resolve_in_thread(self, target_contact, contacts, logic_func):
        """
        Run the resolution logic in a background thread to keep UI responsive.
        """
        self.stack.set_visible_child_name("loading")
        self.spinner.start()

        def task():
            try:
                logic_func(target_contact, contacts)
            except Exception as e:
                logger.error(f"Resolution error: {e}")
            GLib.idle_add(self.on_resolve_complete)

        run_in_background(task)

    def on_resolve_complete(self):
        """Called on main thread when single resolution is done."""
        self.spinner.stop()
        self.stack.set_visible_child_name("main")
        self.current_index += 1
        self.show_current_conflict()

    def resolve_keep(self, kept_contact):
        """User clicked 'Keep this'."""
        _number, contacts = self.conflicts[self.current_index]
        self.resolve_in_thread(kept_contact, contacts, self.logic_keep_strict)

    def resolve_merge(self, kept_contact):
        """User clicked 'Merge'."""
        _number, contacts = self.conflicts[self.current_index]
        self.resolve_in_thread(kept_contact, contacts, self.logic_merge_smart)

    def logic_keep_strict(self, kept_contact, contacts):
        """
        Strict Keep: Delete all others. Do NOT merge data.
        Target contact remains untouched.
        """
        others = [c for c in contacts if c['uid'] != kept_contact['uid']]

        if kept_contact.get('uid') is None:
            self.keep_pending_edit = True

        self.daemon.delete_contacts([o['uid'] for o in others if o['uid']])

    def logic_merge_smart(self, kept_contact, contacts):
        """
        Smart Merge: Pull unique data from others into kept_contact, then delete others.
        Duplicates are only removed after the merged contact was saved successfully.
        """
        others = [c for c in contacts if c['uid'] != kept_contact['uid']]

        if kept_contact.get('uid') is None:
            self.keep_pending_edit = True
            self.daemon.delete_contacts([o['uid'] for o in others if o['uid']])
            return

        new_vcard = self.merge_contact_data(kept_contact, others)

        if not self.daemon.save_contact(new_vcard, uid=kept_contact['uid'])[0]:
            logger.error(f"[DuplicateResolution] Merge save failed for {kept_contact['uid']}, keeping duplicates")
            return

        self.daemon.delete_contacts([o['uid'] for o in others if o['uid']])

    def merge_contact_data(self, target, others):
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
        run_in_background(self.merge_all_thread)

    def contact_score(self, contact):
        """Rank a contact for merge-all target selection."""
        return (
            1 if contact.get('uid') else 0,
            1 if contact.get('is_fav') else 0,
            len(contact.get('phones') or []) + len(contact.get('emails') or []),
            len(contact.get('name') or '')
        )

    def merge_all_thread(self):
        pending = self.conflicts[self.current_index:]
        total = len(pending)

        try:
            for processed, (_number, contacts) in enumerate(pending, start=1):
                best = max(contacts, key=self.contact_score)
                self.logic_merge_smart(best, contacts)
                GLib.idle_add(self.update_progress, processed, total)
        except Exception as e:
            logger.error(f"Merge all error: {e}")

        GLib.idle_add(self.finish_merge)

    def update_progress(self, current, total):
        self.lbl_progress.set_text(_("Merging {current}/{total}...").format(current=current, total=total))

    def finish_merge(self):
        self.spinner.stop()
        self.leave()
