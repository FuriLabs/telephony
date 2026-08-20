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

import weakref

from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.utils.phone_utils import normalize_number

from gi.repository import Gtk, Adw, GLib
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _
from telephony.shared.constants import SHEET_CONTENT_WIDTH, ALERT_SHEET_SPACING, ALERT_SHEET_MARGIN

LIST_CHUNK_SIZE = 20
INFO_SHEET_MAX_HEIGHT = 520

_CLOSING_DIALOGS = weakref.WeakSet()


def install_sheet_host(window):
    """Give a window the bottom sheet its flows are shown in.

    Adwaita's sheet is a widget that lives in the window rather than
    something presented onto it, so the window keeps one and shows
    whatever is asked for in it. It spans the window by itself, which
    a sheet made out of a dialog does not, and a dialog offers nothing
    to ask for it with.

    One per window is the design rather than a limit: a flow opened
    from inside a sheet pushes a page on the navigation already there
    instead of opening a second sheet over the first.

    The window lets go of what it is showing before the sheet is given
    it. A widget has one parent, so handing it over while the window
    still holds it fails and leaves the window with nothing in it.
    """
    content = window.get_content()
    window.set_content(None)

    host = Adw.BottomSheet()
    host.set_modal(True)
    host.set_content(content)
    window.set_content(host)
    return host


def present_sheet(window, child):
    """Show a widget as the window's bottom sheet."""
    window.sheet_host.set_sheet(child)
    window.sheet_host.set_open(True)


def present_alert_sheet(window, heading, body, responses, on_response, extra_child=None):
    """Ask a question in the window's sheet.

    responses are (id, label, appearance) with appearance one of None,
    "suggested" or "destructive". The answer reaches on_response only
    when a button is pressed: leaving by the back arrow is the same as
    saying no, which is what every caller of this already treated an
    untouched question as.

    A question asked on top of a flow keeps that flow's height, so the
    sheet does not shrink to the question and grow back afterwards, and
    it is left by the back arrow the flow already has. A question asked
    with nothing underneath has no arrow to offer, so it carries its
    own way out.
    """
    host = window.sheet_host
    showing = sheet_navigation(host.get_sheet()) if host.get_open() else None

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    if showing is None:
        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda _b: GLib.idle_add(
            lambda: close_sheet(window) or False))
        header.pack_start(btn_cancel)
    toolbar.add_top_bar(header)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ALERT_SHEET_SPACING,
                  margin_top=ALERT_SHEET_MARGIN, margin_bottom=ALERT_SHEET_MARGIN,
                  margin_start=ALERT_SHEET_MARGIN, margin_end=ALERT_SHEET_MARGIN)

    if body:
        label = Gtk.Label(label=body, wrap=True, xalign=0, valign=Gtk.Align.START)
        box.append(label)

    if extra_child is not None:
        box.append(extra_child)

    for response_id, label_text, appearance in responses:
        button = Gtk.Button(label=label_text)
        button.add_css_class("pill")
        if appearance:
            button.add_css_class(f"{appearance}-action")
        button.connect("clicked", lambda _b, rid=response_id: GLib.idle_add(
            lambda: [close_sheet_page(window), on_response(rid)] and False))
        box.append(button)

    scroll = Gtk.ScrolledWindow(propagate_natural_height=True, vexpand=True)
    scroll.set_child(box)
    toolbar.set_content(scroll)

    present_sheet_page(window, Adw.NavigationPage(title=heading, child=toolbar))


def sheet_navigation(widget):
    """Find the navigation a sheet keeps its pages in.

    A flow does not have to be a navigation itself to hold one: the
    settings sheet is a bin around a toast overlay around its own
    navigation, and pages opened from inside it belong on that one
    rather than on a fresh one that would replace the flow.
    """
    if widget is None:
        return None
    if isinstance(widget, Adw.NavigationView):
        return widget

    child = widget.get_first_child()
    while child is not None:
        found = sheet_navigation(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def present_sheet_page(window, page, replace=False):
    """Show a page in the window's sheet, pushing it onto whatever is there.

    A window has one sheet, so a flow opened from inside another one
    goes on top of it as a page and back walks out of it. That is what
    a sheet on top of a sheet was standing in for.

    Replacing is for what arrives on its own rather than by asking: a
    second answer to a network request takes the place of the first
    instead of burying it.

    A page going onto an open sheet asks for the height the sheet
    already has, so a short page does not shrink the sheet around it
    and let it spring back when the page is left.

    The page takes the focus itself so it does not go to whatever the
    page happens to hold first. A text field taking it brings the
    keyboard with it, and a page is opened to be read before it is
    typed into. Nothing is made unfocusable, so tapping a field still
    works and the tab order is untouched.
    """
    page.set_focusable(True)
    host = window.sheet_host
    nav = sheet_navigation(host.get_sheet()) if host.get_open() else None
    if nav is not None:
        page.set_size_request(-1, nav.get_height())
        if replace:
            nav.replace([page])
            return
        nav.push(page)
        return

    nav = Adw.NavigationView()
    nav.add(page)
    present_sheet(window, nav)


def close_sheet_page(window):
    """Leave the flow on top, or take the sheet down when it is the last."""
    nav = sheet_navigation(window.sheet_host.get_sheet())
    if nav is not None:
        visible = nav.get_visible_page()
        if visible is not None and nav.get_previous_page(visible) is not None:
            nav.pop()
            return
    close_sheet(window)


def on_sheet_closed(window, callback):
    """Run callback once, when the window's sheet is taken down.

    A sheet is a widget rather than a dialog, so it reports going away
    by its open state changing rather than by closing.
    """
    state = {"id": None}

    def watch(host, _param):
        if host.get_open():
            return
        host.disconnect(state["id"])
        callback()

    state["id"] = window.sheet_host.connect("notify::open", watch)


def close_sheet(window):
    """Take the window's bottom sheet down."""
    window.sheet_host.set_open(False)


def stay_a_sheet(dialog):
    """Keep a dialog at the bottom of the screen rather than floating.

    Adwaita decides between a sheet and a floating dialog from how wide
    the window is, and window width is display scale: the same phone is
    360 points across at 300 per cent and 540 at 200, so lowering the
    scale turned every dialog into a box in the middle of the screen.

    What still comes through here is what a dialog is for: the alerts,
    which stack over whatever is showing and answer with a response.
    The flows are sheets in their own right and go to the window's.
    """
    dialog.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)


def present_choice_sheet(parent, title, build_rows, description=None):
    """Show a bottom sheet with a single group of choice rows.

    build_rows(group, sheet) fills the group; rows close the sheet
    themselves when picked.
    """
    window = parent.get_root()

    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))

    page = Adw.PreferencesPage()
    group = Adw.PreferencesGroup()
    if description:
        group.set_description(description)
    page.add(group)
    toolbar.set_content(page)

    build_rows(group, window)
    present_sheet_page(window, Adw.NavigationPage(title=title, child=toolbar))
    return window


def _choice_row(group, label, callback, dismiss, subtitle=None, destructive=False, icon=None):
    """Add one activatable row that puts its own surface away and runs callback."""
    row = Adw.ActionRow(title=label, activatable=True)
    if subtitle:
        row.set_subtitle(subtitle)
    if icon:
        row.add_prefix(Gtk.Image.new_from_icon_name(icon))
    if destructive:
        row.add_css_class("error")
    row.connect("activated", lambda r: GLib.idle_add(
        lambda: [dismiss(), callback()] and False))
    group.add(row)
    return row


def add_choice_row(group, window, label, callback, subtitle=None, destructive=False,
                   icon=None, opens_flow=False):
    """Add one activatable row that leaves its flow and runs its callback.

    A row that opens another flow keeps the one it is in, so the new
    flow arrives on top of it and back walks out the way the user came.
    Taking the choice away first leaves whatever it opens with nothing
    behind it and no way back.
    """
    dismiss = (lambda: None) if opens_flow else (lambda: close_sheet_page(window))
    return _choice_row(group, label, callback, dismiss,
                       subtitle=subtitle, destructive=destructive, icon=icon)


def build_info_sheet(title, text, selectable=False):
    """Build the page holding a titled block of explanatory text.

    The sheet presentation ignores the natural height of a wrapped
    label, so the scroll area asks for the measured text height up to
    a cap, otherwise long texts render as a short scrolling stub.
    """
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))

    scroll = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=INFO_SHEET_MAX_HEIGHT)
    lbl = Gtk.Label(label=text, wrap=True, xalign=0, selectable=selectable, valign=Gtk.Align.START)
    lbl.set_margin_top(4)
    lbl.set_margin_bottom(24)
    lbl.set_margin_start(16)
    lbl.set_margin_end(16)
    scroll.set_child(lbl)
    natural_height = lbl.measure(Gtk.Orientation.VERTICAL, SHEET_CONTENT_WIDTH)[1]
    scroll.set_min_content_height(min(natural_height, INFO_SHEET_MAX_HEIGHT))
    toolbar.set_content(scroll)
    return Adw.NavigationPage(title=title, child=toolbar)


def present_info_sheet(parent, title, text):
    """Show a titled block of explanatory text in the window's sheet."""
    present_sheet_page(parent.get_root(), build_info_sheet(title, text))


def build_selector_row(title, on_select=None):
    """Build an expander row selector; options come from set_selector_options."""
    row = Adw.ExpanderRow(title=title)
    row._selected_index = 0
    row._option_rows = []
    row._option_checks = []
    row._on_select = on_select
    return row


def set_selector_options(row, labels, selected_index):
    """Replace the inline options of an expander selector."""
    for option in row._option_rows:
        row.remove(option)
    row._option_rows = []
    row._option_checks = []
    for i, name in enumerate(labels):
        option = Adw.ActionRow(title=name, activatable=True)
        check = Gtk.Image.new_from_icon_name("object-select-symbolic")
        check.set_visible(i == selected_index)
        option.add_suffix(check)
        option.connect("activated", lambda r, i=i: GLib.idle_add(
            lambda: _pick_selector_option(row, i) or False))
        row.add_row(option)
        row._option_rows.append(option)
        row._option_checks.append(check)
    row._selected_index = selected_index
    if labels:
        row.set_subtitle(labels[selected_index])


def _pick_selector_option(row, index):
    """Apply a tapped selector option and collapse the row."""
    row._selected_index = index
    row.set_subtitle(row._option_rows[index].get_title())
    for i, check in enumerate(row._option_checks):
        check.set_visible(i == index)
    row.set_expanded(False)
    if row._on_select:
        row._on_select(index)


def build_nav_row(title, subtitle, callback, icon=None, destructive=False):
    """Build an activatable row that opens another page."""
    row = Adw.ActionRow(title=title, activatable=True)
    if subtitle:
        row.set_subtitle(subtitle)
    if icon:
        row.add_prefix(Gtk.Image.new_from_icon_name(icon))
    if destructive:
        row.add_css_class("error")
    row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
    row.connect("activated", lambda r: GLib.idle_add(lambda: callback() or False))
    return row


class EntryListGroup(Adw.PreferencesGroup):
    """A settings list whose entries are typed in and edited in place.

    Every entry is an expander showing its values, opening to the same
    fields the add row uses, so adding and editing look identical. New
    values commit on an explicit action rather than while typing, so a
    half typed entry is never stored, and a rejected value leaves the
    fields untouched so it can be corrected.
    """

    def __init__(self, title, fields, add_label, empty_label,
                 on_add, on_delete, on_update=None, on_error=None):
        """Initialize the list group."""
        super().__init__(title=title)
        self.fields = fields
        self.add_label = add_label
        self.empty_label = empty_label
        self.on_add = on_add
        self.on_delete = on_delete
        self.on_update = on_update
        self.on_error = on_error
        self.entries = []
        self._rows = []
        self._add_row = None
        self._add_entries = {}
        self._empty_row = None

    def set_entries(self, entries):
        """Replace the listed entries, rebuilding the rows."""
        self.entries = list(entries)
        self._rebuild()

    def _rebuild(self):
        """Rebuild the add row and one expander per entry."""
        for row in self._rows:
            self.remove(row)
        self._rows = []
        if self._add_row is not None:
            self.remove(self._add_row)
        if self._empty_row is not None:
            self.remove(self._empty_row)
            self._empty_row = None

        self._add_row = self._build_add_row()
        self.add(self._add_row)

        if not self.entries:
            self._empty_row = Adw.ActionRow(title=self.empty_label)
            self._empty_row.add_css_class("dim-label")
            self.add(self._empty_row)
            return

        for entry in self.entries:
            row = self._build_entry_row(entry)
            self.add(row)
            self._rows.append(row)

    def _build_field_row(self, field, value=""):
        """Build one editable field row."""
        row = Adw.EntryRow(title=field["label"])
        row.set_text(value or "")
        if field.get("purpose"):
            row.set_input_purpose(field["purpose"])
        return row

    def _build_add_row(self):
        """Build the expander that adds a new entry."""
        expander = Adw.ExpanderRow(title=self.add_label)
        expander.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))

        self._add_entries = {}
        for field in self.fields:
            entry = self._build_field_row(field)
            self._add_entries[field["key"]] = entry
            expander.add_row(entry)

        action = Adw.ActionRow(title=self.add_label, activatable=True)
        action.add_css_class("accent")
        action.connect("activated", lambda r: GLib.idle_add(lambda: self._commit_add(expander) or False))
        expander.add_row(action)

        def sync(*_args):
            action.set_sensitive(self._values_complete(self._add_entries))
        for entry in self._add_entries.values():
            entry.connect("changed", sync)
        sync()
        return expander

    def _values_complete(self, entries):
        """Return whether every required field has text."""
        for field in self.fields:
            if field.get("required") and not entries[field["key"]].get_text().strip():
                return False
        return True

    def _read_values(self, entries):
        """Read the current field values."""
        return {key: entry.get_text().strip() for key, entry in entries.items()}

    def _commit_add(self, expander):
        """Hand the typed values to the caller and reset on success."""
        values = self._read_values(self._add_entries)
        ok, error = self.on_add(values)
        if not ok:
            if error and self.on_error:
                self.on_error(error)
            return
        expander.set_expanded(False)
        for entry in self._add_entries.values():
            entry.set_text("")

    def _build_entry_row(self, entry):
        """Build the expander for one existing entry."""
        primary = entry.get(self.fields[0]["key"], "")
        expander = Adw.ExpanderRow(title=primary or self.empty_label)
        if len(self.fields) > 1:
            expander.set_subtitle(entry.get(self.fields[1]["key"], ""))

        field_entries = {}
        for field in self.fields:
            row = self._build_field_row(field, entry.get(field["key"], ""))
            if self.on_update:
                row.set_show_apply_button(True)
                row.connect("apply", lambda r, e=entry, fe=field_entries, ex=expander:
                            GLib.idle_add(lambda: self._commit_update(e, fe, ex) or False))
            else:
                row.set_editable(False)
            field_entries[field["key"]] = row
            expander.add_row(row)

        delete = Adw.ActionRow(title=_("Delete"), activatable=True)
        delete.add_css_class("error")
        delete.connect("activated", lambda r, e=entry: GLib.idle_add(lambda: self.on_delete(e) or False))
        expander.add_row(delete)
        return expander

    def _commit_update(self, entry, field_entries, expander):
        """Hand edited values to the caller and refresh the row summary."""
        values = self._read_values(field_entries)
        if not self._values_complete(field_entries):
            return
        ok, error = self.on_update(entry, values)
        if not ok:
            if error and self.on_error:
                self.on_error(error)
            return
        expander.set_title(values.get(self.fields[0]["key"], ""))
        if len(self.fields) > 1:
            expander.set_subtitle(values.get(self.fields[1]["key"], ""))


def translate_phone_label(label):
    """Translate a phone label key to its localized string."""
    LABELS = {
        "Mobile": _("Mobile"),
        "Work": _("Work"),
        "Home": _("Home"),
        "Fax": _("Fax"),
        "Other": _("Other"),
        "Main": _("Main")
    }
    return LABELS.get(label, label)


def populate_contact_search_results(results_list, contacts, eds, is_added, on_add, translate_label=None, unknown_name="Unknown", source_map=None):
    """Populate a Gtk.ListBox with contact search result rows.

    Clears results_list, then appends one Adw.ActionRow per phone number of
    each contact tuple, storing {"name", "number"} on row.contact_data.
    is_added(normalized_number) marks rows that are already in the caller's
    list: those rows are made insensitive and get a check icon, while the
    others get an add button that invokes on_add(row). translate_label, when
    given, maps the phone label key to a localized string for the subtitle,
    and unknown_name is the display name fallback. Appends a placeholder
    label when no rows were produced.
    """
    while child := results_list.get_first_child():
        results_list.remove(child)

    has_results = False

    if source_map is None:
        source_map = {}
        if eds:
            sources = eds.get_sources_info()
            for s in sources:
                source_map[s['uid']] = s['name']

    if contacts:
        for c in contacts:
            first = c[1]
            last = c[2]
            phones = c[3]
            source_uid = c[6] if len(c) > 6 else None

            first_name = first or ""
            last_name = last or ""
            full_name = f"{first_name} {last_name}".strip() or unknown_name

            for ph_num, ph_label in phones:
                shown_label = translate_label(ph_label) if translate_label else ph_label
                subtitle_text = f"{ph_num} ({shown_label})"

                if source_uid and source_uid in source_map:
                    s_name = source_map[source_uid]
                    subtitle_text += f"\n{s_name}"

                row = Adw.ActionRow(title=full_name, subtitle=subtitle_text)
                row.set_subtitle_lines(2)
                row.contact_data = {"name": full_name, "number": ph_num}

                norm_ph = normalize_number(ph_num)
                if is_added(norm_ph):
                    row.set_sensitive(False)
                    row.add_suffix(Gtk.Image(icon_name="object-select-symbolic"))
                else:
                    btn_add = Gtk.Button(icon_name="list-add-symbolic")
                    btn_add.add_css_class("flat")
                    btn_add.add_css_class("circular")
                    btn_add.set_valign(Gtk.Align.CENTER)
                    btn_add.connect("clicked", lambda b, r=row: on_add(r))
                    row.add_suffix(btn_add)

                results_list.append(row)
                has_results = True

    if not has_results:
        lbl = Gtk.Label(label=_("No contacts found"))
        lbl.add_css_class("dim-label")
        lbl.set_margin_top(20)
        results_list.append(lbl)


class DataLoader:
    """Helper for loading data asynchronously in chunks to prevent UI blocking."""

    @staticmethod
    def load_data(fetch_func, model_add_func, model, check_token_func=None, on_finish=None, clear_on_first_chunk=True):
        """Execute data loading task."""
        def task():
            try:
                if check_token_func and not check_token_func():
                    return
                processed_items = fetch_func()

                if not processed_items:
                    def update_empty():
                        if check_token_func and not check_token_func():
                            return False
                        if clear_on_first_chunk:
                            model.remove_all()
                        if model_add_func:
                            model_add_func(model, [])
                        return False
                    GLib.idle_add(update_empty)
                    if on_finish and callable(on_finish):
                        GLib.idle_add(on_finish)
                    return

                chunk_size = LIST_CHUNK_SIZE
                state = {'idx': 0, 'is_first_chunk': True}

                def process_next_chunk():
                    if check_token_func and not check_token_func():
                        return False

                    idx = state['idx']
                    if idx >= len(processed_items):
                        if on_finish and callable(on_finish):
                            on_finish()
                        return False

                    if state['is_first_chunk'] and clear_on_first_chunk:
                        model.remove_all()

                    chunk = processed_items[idx:idx + chunk_size]
                    if chunk:
                        model_add_func(model, chunk)

                    state['is_first_chunk'] = False
                    state['idx'] += chunk_size
                    return True

                GLib.idle_add(process_next_chunk)
            except Exception as e:
                logger.error(f"Data loading error: {e}")
        run_in_background(task)


def blocklist_domains_for(main_window):
    """Which blocklist domains this launcher speaks for: (calls, messages).

    A single-purpose launcher blocks only its own domain; the full
    window and a contacts-only window speak for both.
    """
    calls = bool(main_window.show_calls_mode)
    messages = bool(main_window.show_messages_mode)
    if calls and not messages:
        return (True, False)
    if messages and not calls:
        return (False, True)
    return (True, True)


def wire_blocklist_switch_locks(sw_calls, sw_messages):
    """Keep at least one blocklist switch on; the last block locks.

    With both switches present either may turn off while the other
    holds; the survivor locks with a hint until both are on again.
    A lone switch is information, not a question: it stays locked and
    points at the trash can, which owns removal.
    """
    if sw_calls is None or sw_messages is None:
        lone = sw_calls or sw_messages
        if lone is not None:
            lone.set_sensitive(False)
            lone.set_subtitle(_("Remove the block with the trash can"))
        return

    def sync(*_args):
        calls_on = sw_calls.get_active()
        messages_on = sw_messages.get_active()
        sw_calls.set_sensitive(messages_on)
        sw_messages.set_sensitive(calls_on)
        sw_calls.set_subtitle("" if messages_on else _("Last block — remove the number with the trash can"))
        sw_messages.set_subtitle("" if calls_on else _("Last block — remove the number with the trash can"))

    sw_calls.connect("notify::active", sync)
    sw_messages.connect("notify::active", sync)
    sync()
