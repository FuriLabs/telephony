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
from ...backend.utils.phone_utils import normalize_number

from gi.repository import Gtk, Adw, GLib
from loguru import logger
from gettext import gettext as _


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


def populate_contact_search_results(results_list, contacts, eds, is_added, on_add, translate_label=None, unknown_name="Unknown"):
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

                chunk_size = 20
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
