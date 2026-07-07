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

import datetime
import calendar
from gi.repository import Gtk, Adw, GLib
from loguru import logger
from gettext import gettext as _


class DateTimePicker:
    """A reusable dialog for selecting date and optional time."""

    def __init__(self, parent, title=None, initial_date=None, include_time=False, on_confirm=None):
        """Initialize the DateTimePicker."""
        self.parent = parent
        self.title = title if title else _("Select Date")
        self.include_time = include_time
        self.callback = on_confirm

        self.selected_date = initial_date if initial_date else GLib.DateTime.new_now_local()

        self.dialog = Adw.MessageDialog(
            transient_for=self.parent,
            heading=self.title
        )

        self.dialog.add_response("cancel", _("Cancel"))
        self.dialog.add_response("confirm", _("Confirm"))
        self.dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        self.dialog.connect("response", self._on_response)

        self._build_ui()
        self.dialog.present()

    def _build_ui(self):
        """Construct the picker UI."""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_halign(Gtk.Align.CENTER)

        controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        controls_box.set_halign(Gtk.Align.CENTER)

        curr_year = datetime.datetime.now().year

        date_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        date_row.set_homogeneous(True)

        box_year = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl_year = Gtk.Label(label=_("Year"), css_classes=["dim-label"])
        lbl_year.set_halign(Gtk.Align.CENTER)

        adj_year = Gtk.Adjustment(
            value=self.selected_date.get_year(),
            lower=1900,
            upper=curr_year + 5,
            step_increment=1,
            page_increment=10,
            page_size=0
        )
        self.spin_year = Gtk.SpinButton(adjustment=adj_year, climb_rate=1, digits=0)
        self.spin_year.set_valign(Gtk.Align.CENTER)
        self.spin_year.set_alignment(0.5)
        box_year.append(lbl_year)
        box_year.append(self.spin_year)
        date_row.append(box_year)

        box_month = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl_month = Gtk.Label(label=_("Month"), css_classes=["dim-label"])
        lbl_month.set_halign(Gtk.Align.CENTER)

        adj_month = Gtk.Adjustment(
            value=self.selected_date.get_month(),
            lower=1,
            upper=12,
            step_increment=1,
            page_increment=1,
            page_size=0
        )
        self.spin_month = Gtk.SpinButton(adjustment=adj_month, climb_rate=1, digits=0)
        self.spin_month.set_valign(Gtk.Align.CENTER)
        self.spin_month.set_alignment(0.5)
        box_month.append(lbl_month)
        box_month.append(self.spin_month)
        date_row.append(box_month)

        controls_box.append(date_row)
        main_box.append(controls_box)

        self.calendar_widget = Gtk.Calendar()
        self.calendar_widget.set_show_heading(False)
        self.calendar_widget.set_show_day_names(True)
        self.calendar_widget.select_day(self.selected_date)
        self.calendar_widget.add_css_class("card")
        main_box.append(self.calendar_widget)

        if self.include_time:
            time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            time_row.set_halign(Gtk.Align.FILL)
            time_row.set_margin_top(10)
            time_row.set_homogeneous(True)

            box_hour = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            lbl_hour = Gtk.Label(label=_("Hour"), css_classes=["dim-label"])
            lbl_hour.set_halign(Gtk.Align.CENTER)

            adj_hour = Gtk.Adjustment(
                value=self.selected_date.get_hour(),
                lower=0,
                upper=23,
                step_increment=1,
                page_increment=1,
                page_size=0
            )
            self.spin_hour = Gtk.SpinButton(adjustment=adj_hour, climb_rate=1, digits=0)
            self.spin_hour.set_valign(Gtk.Align.CENTER)
            self.spin_hour.set_alignment(0.5)
            box_hour.append(lbl_hour)
            box_hour.append(self.spin_hour)
            time_row.append(box_hour)

            box_min = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            lbl_min = Gtk.Label(label=_("Minute"), css_classes=["dim-label"])
            lbl_min.set_halign(Gtk.Align.CENTER)

            adj_min = Gtk.Adjustment(
                value=self.selected_date.get_minute(),
                lower=0,
                upper=59,
                step_increment=1,
                page_increment=10,
                page_size=0
            )
            self.spin_min = Gtk.SpinButton(adjustment=adj_min, climb_rate=1, digits=0)
            self.spin_min.set_valign(Gtk.Align.CENTER)
            self.spin_min.set_alignment(0.5)
            box_min.append(lbl_min)
            box_min.append(self.spin_min)
            time_row.append(box_min)

            main_box.append(time_row)

        self.dialog.set_extra_child(main_box)

        self.spin_year.connect("value-changed", self._on_spinner_changed)
        self.spin_month.connect("value-changed", self._on_spinner_changed)

    def _on_spinner_changed(self, *args):
        """Update calendar when year or month spinners change."""
        y = self.spin_year.get_value_as_int()
        m = self.spin_month.get_value_as_int()

        current_day = self.calendar_widget.get_date().get_day_of_month()
        last_day = calendar.monthrange(y, m)[1]
        day = min(current_day, last_day)

        new_d = GLib.DateTime.new_local(y, m, day, 0, 0, 0)
        self.calendar_widget.select_day(new_d)

    def _on_response(self, dialog, response):
        """Handle dialog response."""
        if response == "confirm":
            try:
                date = self.calendar_widget.get_date()
                y, m, d = date.get_year(), date.get_month(), date.get_day_of_month()

                h, mn = 0, 0
                if self.include_time:
                    h = self.spin_hour.get_value_as_int()
                    mn = self.spin_min.get_value_as_int()

                final_dt = GLib.DateTime.new_local(y, m, d, h, mn, 0)

                if self.callback:
                    self.callback(final_dt)
            except Exception as e:
                logger.error(f"DateTime selection error: {e}")

        GLib.idle_add(lambda: dialog.close() or False)
