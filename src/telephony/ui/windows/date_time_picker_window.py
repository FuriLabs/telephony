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

from ..widgets.common_widget import close_dialog

CONTENT_MARGIN = 16
BOTTOM_MARGIN = 24
TIME_ENTRY_CHARS = 3


def _format_value(value):
    """Format a stepper value as a zero padded two digit string."""
    return f"{int(value):02d}"


class DateTimePicker:
    """A reusable bottom sheet for selecting a date and optional time."""

    def __init__(self, parent, title=None, initial_date=None, include_time=False, on_confirm=None):
        """Initialize the DateTimePicker."""
        self.parent = parent
        self.title = title if title else _("Select Date")
        self.include_time = include_time
        self.callback = on_confirm
        self._entry_sync_guard = False
        self._confirmed = False

        self.selected_date = initial_date if initial_date else GLib.DateTime.new_now_local()

        self.dialog = Adw.Dialog(title=self.title)
        self.dialog.set_content_width(360)

        self.toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda x: GLib.idle_add(lambda: close_dialog(self.dialog) or False))
        header.pack_start(btn_cancel)

        btn_confirm = Gtk.Button(label=_("Confirm"))
        btn_confirm.add_css_class("suggested-action")
        btn_confirm.connect("clicked", lambda x: GLib.idle_add(lambda: self._on_confirm() or False))
        header.pack_end(btn_confirm)

        self.toolbar.add_top_bar(header)
        self.dialog.set_child(self.toolbar)

        self._build_ui()
        self.dialog.present(self.parent)

    def _build_ui(self):
        """Construct the picker UI."""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(CONTENT_MARGIN)
        main_box.set_margin_start(CONTENT_MARGIN)
        main_box.set_margin_end(CONTENT_MARGIN)
        main_box.set_margin_bottom(BOTTOM_MARGIN)

        self.calendar_widget = Gtk.Calendar()
        self.calendar_widget.set_show_day_names(True)
        self.calendar_widget.select_day(self.selected_date)
        self.calendar_widget.add_css_class("card")
        main_box.append(self.calendar_widget)

        if self.include_time:
            time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            time_row.set_halign(Gtk.Align.CENTER)
            time_row.set_margin_top(10)

            self.adj_hour = Gtk.Adjustment(
                value=self.selected_date.get_hour(),
                lower=0,
                upper=23,
                step_increment=1,
                page_increment=6,
                page_size=0
            )
            time_row.append(self._build_stepper(_("Hour"), self.adj_hour))

            self.adj_min = Gtk.Adjustment(
                value=self.selected_date.get_minute(),
                lower=0,
                upper=59,
                step_increment=1,
                page_increment=10,
                page_size=0
            )
            time_row.append(self._build_stepper(_("Minute"), self.adj_min))

            main_box.append(time_row)

        self.toolbar.set_content(main_box)

    def _build_stepper(self, title, adjustment):
        """Build a touch stepper for one time unit.

        The step buttons never take focus, so the on screen keyboard
        only opens when the value entry itself is tapped for direct
        typing.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl = Gtk.Label(label=title, css_classes=["dim-label"])
        lbl.set_halign(Gtk.Align.CENTER)
        box.append(lbl)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, css_classes=["linked"])

        btn_down = Gtk.Button(icon_name="list-remove-symbolic", focus_on_click=False)
        btn_down.connect("clicked", lambda b: self._step(adjustment, -adjustment.get_step_increment()))
        row.append(btn_down)

        entry = Gtk.Entry(
            text=_format_value(adjustment.get_value()),
            xalign=0.5,
            width_chars=TIME_ENTRY_CHARS,
            max_width_chars=TIME_ENTRY_CHARS,
            input_purpose=Gtk.InputPurpose.DIGITS
        )
        entry.connect("changed", self._on_entry_changed, adjustment)
        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("leave", self._on_entry_unfocused, entry, adjustment)
        entry.add_controller(focus_controller)
        adjustment.connect("value-changed", self._on_value_changed, entry)
        row.append(entry)

        btn_up = Gtk.Button(icon_name="list-add-symbolic", focus_on_click=False)
        btn_up.connect("clicked", lambda b: self._step(adjustment, adjustment.get_step_increment()))
        row.append(btn_up)

        box.append(row)
        return box

    def _step(self, adjustment, delta):
        """Step a value, wrapping around at the bounds."""
        value = adjustment.get_value() + delta
        if value > adjustment.get_upper():
            value = adjustment.get_lower()
        elif value < adjustment.get_lower():
            value = adjustment.get_upper()
        adjustment.set_value(value)

    def _on_value_changed(self, adjustment, entry):
        """Reflect a stepped value in the entry."""
        if self._entry_sync_guard:
            return
        self._entry_sync_guard = True
        entry.set_text(_format_value(adjustment.get_value()))
        self._entry_sync_guard = False

    def _on_entry_changed(self, entry, adjustment):
        """Apply a typed value without reformatting while typing."""
        if self._entry_sync_guard:
            return
        text = entry.get_text()
        if not text.isdigit():
            return
        value = min(max(int(text), adjustment.get_lower()), adjustment.get_upper())
        self._entry_sync_guard = True
        adjustment.set_value(value)
        self._entry_sync_guard = False

    def _on_entry_unfocused(self, controller, entry, adjustment):
        """Normalize the entry text once typing is done."""
        self._entry_sync_guard = True
        entry.set_text(_format_value(adjustment.get_value()))
        self._entry_sync_guard = False

    def _on_confirm(self):
        """Apply the picked date once, then close the sheet."""
        if self._confirmed:
            return
        self._confirmed = True
        try:
            date = self.calendar_widget.get_date()
            y, m, d = date.get_year(), date.get_month(), date.get_day_of_month()

            h, mn = 0, 0
            if self.include_time:
                h = int(self.adj_hour.get_value())
                mn = int(self.adj_min.get_value())

            final_dt = GLib.DateTime.new_local(y, m, d, h, mn, 0)
        except Exception as e:
            logger.error(f"DateTime selection error: {e}")
            close_dialog(self.dialog)
            return

        close_dialog(self.dialog)
        if self.callback:
            self.callback(final_dt)
