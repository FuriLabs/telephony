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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from gettext import gettext as _
from loguru import logger

from ...backend.managers.modem_recovery_manager import execute_recovery_step, VERIFY_POLL_SECONDS, VERIFY_MAX_TICKS
from ...backend.utils.system_utils import save_modem_logs, press_power_button
from ...backend.utils.thread_utils import run_in_background


class ModemRecoveryWindow(Adw.Window):
    def __init__(self, parent, ofono):
        """Initialize the recovery window."""
        super().__init__(title=_("Modem Recovery"), transient_for=parent, modal=True)
        self.ofono = ofono
        self.set_default_size(360, 680)

        self.running = False
        self.current_index = 0
        self.verify_timer = None
        self.verify_ticks = 0

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        self.set_content(toolbar)

        self.overlay = Adw.ToastOverlay()
        toolbar.set_content(self.overlay)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(4)
        box.set_margin_bottom(12)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(box)
        self.overlay.set_child(scroll)

        lbl_info = Gtk.Label(label=_("Steps are tried from gentlest to strongest. Each one is verified before the next unlocks."), wrap=True, xalign=0)
        lbl_info.add_css_class("dim-label")
        lbl_info.add_css_class("caption")
        box.append(lbl_info)

        self.steps = (
            {"id": "ofono", "title": _("Phone service restart"), "desc": _("Restarts the ofono service that manages the modem.")},
            {"id": "radio", "title": _("Radio restart"), "desc": _("Turns the radio off and back on, like flight mode.")},
            {"id": "power", "title": _("Modem power cycle"), "desc": _("Powers the modem down and back up.")},
            {"id": "ril", "title": _("Modem firmware restart"), "desc": _("Restarts the vendor modem firmware.")},
            {"id": "radio_flag", "title": _("Re-enable radio"), "desc": _("Fixes a radio left disabled by an unclean shutdown.")},
            {"id": "reboot", "title": _("Reboot phone"), "desc": _("Opens the system power menu. Last resort.")},
        )

        self.rows_group = Adw.PreferencesGroup()
        box.append(self.rows_group)

        self.rows = []
        for index, step in enumerate(self.steps):
            row = Adw.ActionRow(title=step["title"], subtitle=step["desc"])
            btn = Gtk.Button(label=_("Try"), valign=Gtk.Align.CENTER)
            btn.add_css_class("destructive-action" if step["id"] in ("ril", "reboot") else "suggested-action")
            btn.connect("clicked", lambda b, i=index: GLib.idle_add(lambda: self._on_step_clicked(i) or False))
            spinner = Adw.Spinner(valign=Gtk.Align.CENTER)
            spinner.set_visible(False)
            row.add_suffix(spinner)
            row.add_suffix(btn)
            self.rows_group.add(row)
            self.rows.append({"row": row, "btn": btn, "spinner": spinner, "tried": False})

        btn_logs = Gtk.Button(label=_("Save Modem Logs"))
        btn_logs.set_margin_top(8)
        btn_logs.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_save_logs(b) or False))
        box.append(btn_logs)

        self._update_rows()
        self.connect("close-request", self._on_close_request)

    def _update_rows(self):
        """Enable only the next untried step while nothing is running."""
        for index, row in enumerate(self.rows):
            allowed = (index == self.current_index) and not self.running
            row["btn"].set_sensitive(allowed)
            if row["tried"] and index != self.current_index:
                row["row"].set_subtitle(_("Did not help"))

    def _on_step_clicked(self, index):
        """Run one recovery step and start its verification."""
        if self.running or index != self.current_index:
            return

        step = self.steps[index]
        row = self.rows[index]

        if step["id"] == "reboot":
            press_power_button()
            return

        self.running = True
        row["tried"] = True
        row["btn"].set_visible(False)
        row["spinner"].set_visible(True)
        row["row"].set_subtitle(_("Waiting for the modem..."))
        self._update_rows()

        run_in_background(execute_recovery_step, self.ofono, step["id"], on_error=lambda e: self._on_step_error(e))
        self._arm_verification()

    def _on_step_error(self, error):
        """Log a failed action; verification keeps running regardless.

        The action erroring does not prove the modem stayed down: a RIL
        restart tears down the very D-Bus objects the action talks to, so
        the verdict is left to the interface watch and its timeout.
        """
        logger.warning(f"[ModemRecovery] Step action error: {error}")

    def _arm_verification(self):
        """Poll for dialing to become possible again, with a tick budget."""
        self.verify_ticks = 0
        self.verify_timer = GLib.timeout_add_seconds(VERIFY_POLL_SECONDS, self._verify_tick)

    def _verify_tick(self):
        """Check whether the step brought the modem back."""
        if self.ofono.dialing_available():
            self.verify_timer = None
            self._finish_step(recovered=True)
            return False

        self.verify_ticks += 1
        if self.verify_ticks >= VERIFY_MAX_TICKS:
            self.verify_timer = None
            self._finish_step(recovered=False)
            return False
        return True

    def _finish_step(self, recovered):
        """Wrap up the running step and update the ladder."""
        self._disarm_verification()
        row = self.rows[self.current_index]
        row["spinner"].set_visible(False)
        self.running = False

        if recovered:
            row["row"].set_subtitle(_("Modem recovered"))
            for other in self.rows:
                other["btn"].set_sensitive(False)
            self.overlay.add_toast(Adw.Toast.new(_("Modem recovered")))
            GLib.timeout_add_seconds(2, self._close_after_success)
            return

        row["row"].set_subtitle(_("Did not help"))
        if self.current_index < len(self.rows) - 1:
            self.current_index += 1
        else:
            row["btn"].set_visible(True)
        self._update_rows()

    def _close_after_success(self):
        """Close the window once the success toast has been seen."""
        self.close()
        return False

    def _disarm_verification(self):
        """Cancel the verification timer."""
        if self.verify_timer:
            GLib.source_remove(self.verify_timer)
            self.verify_timer = None

    def _on_save_logs(self, btn):
        """Capture modem evidence for a bug report."""
        btn.set_sensitive(False)

        def done(path):
            btn.set_sensitive(True)
            if path:
                self.overlay.add_toast(Adw.Toast.new(_("Logs saved to {path}").format(path=path)))
            else:
                self.overlay.add_toast(Adw.Toast.new(_("Saving logs failed")))

        run_in_background(save_modem_logs, on_complete=done)

    def _on_close_request(self, win):
        """Clean up watchers when the window closes."""
        self._disarm_verification()
        return False
