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
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.thread_utils import run_in_background
from telephony.client.ui.widgets.common_widget import (present_info_sheet, build_selector_row, set_selector_options, present_alert_sheet)

RING_TIME_VALUES = [5, 10, 15, 20, 25, 30]

FORWARDING_RULES = (
    ("VoiceUnconditional", "always"),
    ("VoiceBusy", "busy"),
    ("VoiceNoReply", "noreply"),
    ("VoiceNotReachable", "unreachable"),
)


class NetworkServicesWindow(Adw.NavigationPage):
    """Network supplementary services subpage of the settings navigation.

    Every value on this page lives in the carrier network, so reads and
    writes are slow round trips: operations run one at a time through a
    queue because ofono rejects overlapping requests, rows show a
    pending state until the network confirms, and PropertyChanged is
    authoritative for what a row finally displays.
    """

    def __init__(self, parent):
        """Initialize the network services page."""
        super().__init__(title=_("Network Services"))
        self.parent_win = parent
        self.ofono = parent.main_window.ofono

        self._ops = []
        self._op_running = False
        self._service_sig = None
        self._syncing_ui = False
        self._loaded = False
        self._known = {}
        self._ring_values = list(RING_TIME_VALUES)

        view = Adw.ToolbarView()
        self.set_child(view)
        view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))

        self.overlay = Adw.ToastOverlay()
        view.set_content(self.overlay)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.overlay.set_child(scroll)

        self.page = Adw.PreferencesPage()
        scroll.set_child(self.page)

        self.build_forwarding_group()
        self.build_calling_group()
        self.build_barring_group()

        self.connect("map", self.on_map)
        self.connect("unmap", self.on_unmap)

    def info_button(self, title, body):
        """Build a group header info button opening an info sheet."""
        btn = Gtk.Button(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER)
        btn.add_css_class("flat")
        btn.add_css_class("circular")
        btn.connect("clicked", lambda b: GLib.idle_add(
            lambda: present_info_sheet(self, title, body) or False))
        return btn

    def build_forwarding_group(self):
        """Build the call forwarding rules group."""
        self.grp_forwarding = Adw.PreferencesGroup(title=_("Call Forwarding"))
        body = _("Forwarding happens in the network before your phone is "
                 "reached.\n\n"
                 "Always redirects every call immediately. When Busy applies "
                 "while you are on another call, When Unanswered after the "
                 "phone has rung for the chosen time, and When Unreachable "
                 "when the phone is off or out of coverage.\n\n"
                 "Enter numbers in international format, for example "
                 "+358401234567. Applying an empty number turns that rule "
                 "off.")
        self.grp_forwarding.set_header_suffix(self.info_button(_("Call Forwarding"), body))
        self.page.add(self.grp_forwarding)

        labels = {
            "VoiceUnconditional": _("Always"),
            "VoiceBusy": _("When Busy"),
            "VoiceNoReply": _("When Unanswered"),
            "VoiceNotReachable": _("When Unreachable"),
        }
        self.cf_rows = {}
        for prop, _key in FORWARDING_RULES:
            row = Adw.EntryRow(title=labels[prop])
            row.set_input_purpose(Gtk.InputPurpose.PHONE)
            row.set_show_apply_button(True)
            row.connect("apply", self.on_forwarding_apply, prop)
            row.set_sensitive(False)
            self.cf_rows[prop] = row
            self.grp_forwarding.add(row)
            if prop == "VoiceNoReply":
                self.row_ring_time = build_selector_row(_("Ring Time"), self.on_ring_time_selected)
                set_selector_options(self.row_ring_time, [
                    _("{count} seconds").format(count=v) for v in RING_TIME_VALUES], 2)
                self.row_ring_time.set_sensitive(False)
                self.grp_forwarding.add(self.row_ring_time)

        self.row_cf_disable = Adw.ActionRow(title=_("Disable All Forwarding"), activatable=True)
        self.row_cf_disable.add_css_class("error")
        self.row_cf_disable.connect("activated", lambda r: GLib.idle_add(
            lambda: self.confirm_disable_forwarding() or False))
        self.row_cf_disable.set_sensitive(False)
        self.grp_forwarding.add(self.row_cf_disable)

    def build_calling_group(self):
        """Build the call waiting and caller id group."""
        grp = Adw.PreferencesGroup()
        self.page.add(grp)

        self.sw_call_waiting = Adw.SwitchRow(title=_("Call Waiting"))
        btn = self.info_button(_("Call Waiting"), _(
            "When enabled, a second incoming call beeps during an ongoing "
            "call instead of the caller getting a busy signal."))
        self.sw_call_waiting.add_suffix(btn)
        self.sw_call_waiting.connect("notify::active", self.on_call_waiting_toggled)
        self.sw_call_waiting.set_sensitive(False)
        grp.add(self.sw_call_waiting)

        self.clir_options = [
            ("default", _("Network Default")),
            ("enabled", _("Hidden")),
            ("disabled", _("Shown")),
        ]
        self.row_clir = build_selector_row(_("Hide Caller ID"), self.on_clir_selected)
        set_selector_options(self.row_clir, [name for _k, name in self.clir_options], 0)
        btn = self.info_button(_("Hide Caller ID"), _(
            "Choose whether people you call see your number. Network "
            "Default follows your subscription setting. Hiding can also be "
            "chosen for a single call from a contact's call menu."))
        self.row_clir.add_suffix(btn)
        self.row_clir.set_sensitive(False)
        grp.add(self.row_clir)

    def build_barring_group(self):
        """Build the call barring group."""
        self.grp_barring = Adw.PreferencesGroup(title=_("Call Barring"))
        body = _("Barring blocks whole classes of calls in the network.\n\n"
                 "Every change needs the barring password from your "
                 "operator, often 0000 by default. Repeated wrong attempts "
                 "can lock barring at the network side, in which case only "
                 "your operator can unlock it.")
        self.grp_barring.set_header_suffix(self.info_button(_("Call Barring"), body))
        self.page.add(self.grp_barring)

        self.barring_out_options = [
            ("disabled", _("Allowed")),
            ("all", _("Bar All Calls")),
            ("international", _("Bar International Calls")),
            ("internationalnothome", _("Bar International Except Home")),
        ]
        self.row_barring_out = build_selector_row(
            _("Outgoing Calls"), lambda i: self.on_barring_selected("VoiceOutgoing", i))
        set_selector_options(self.row_barring_out, [name for _k, name in self.barring_out_options], 0)
        self.row_barring_out.set_sensitive(False)
        self.grp_barring.add(self.row_barring_out)

        self.barring_in_options = [
            ("disabled", _("Allowed")),
            ("always", _("Bar All Calls")),
            ("whenroaming", _("Bar When Roaming")),
        ]
        self.row_barring_in = build_selector_row(
            _("Incoming Calls"), lambda i: self.on_barring_selected("VoiceIncoming", i))
        set_selector_options(self.row_barring_in, [name for _k, name in self.barring_in_options], 0)
        self.row_barring_in.set_sensitive(False)
        self.grp_barring.add(self.row_barring_in)

        self.entry_barring_pw = Adw.PasswordEntryRow(title=_("Barring Password"))
        self.grp_barring.add(self.entry_barring_pw)

        self.exp_change_pw = Adw.ExpanderRow(title=_("Change Barring Password"))
        self.entry_pw_old = Adw.PasswordEntryRow(title=_("Current Password"))
        self.entry_pw_new = Adw.PasswordEntryRow(title=_("New Password"))
        self.exp_change_pw.add_row(self.entry_pw_old)
        self.exp_change_pw.add_row(self.entry_pw_new)
        row_apply = Adw.ActionRow(title=_("Change Barring Password"), activatable=True)
        row_apply.add_suffix(Gtk.Image.new_from_icon_name("object-select-symbolic"))
        row_apply.connect("activated", lambda r: GLib.idle_add(
            lambda: self.on_change_password() or False))
        self.exp_change_pw.add_row(row_apply)
        self.exp_change_pw.set_sensitive(False)
        self.grp_barring.add(self.exp_change_pw)

        self.row_cb_disable = Adw.ActionRow(title=_("Disable All Barrings"), activatable=True)
        self.row_cb_disable.add_css_class("error")
        self.row_cb_disable.connect("activated", lambda r: GLib.idle_add(
            lambda: self.confirm_disable_barrings() or False))
        self.row_cb_disable.set_sensitive(False)
        self.grp_barring.add(self.row_cb_disable)

    def on_map(self, _widget):
        """Subscribe to service updates and start the first load."""
        if self.ofono and self._service_sig is None:
            self._service_sig = self.ofono.connect("network-service-changed", self.on_service_changed)
        if not self._loaded:
            self._loaded = True
            self.load_all()

    def on_unmap(self, _widget):
        """Drop the service subscription while off screen."""
        if self.ofono and self._service_sig is not None:
            self.ofono.disconnect(self._service_sig)
            self._service_sig = None

    def toast(self, message):
        """Show a transient message on this page."""
        self.overlay.add_toast(Adw.Toast.new(message))

    def enqueue(self, task, on_done):
        """Queue a blocking network operation; operations run one at a time."""
        self._ops.append((task, on_done))
        self.pump_ops()

    def pump_ops(self):
        """Start the next queued operation if none is running."""
        if self._op_running or not self._ops:
            return
        self._op_running = True
        task, on_done = self._ops.pop(0)

        def done(result):
            self._op_running = False
            on_done(result)
            self.pump_ops()

        def failed(error):
            self._op_running = False
            logger.error(f"[NetworkServices] Operation failed: {error}")
            on_done(None)
            self.pump_ops()

        run_in_background(task, on_complete=done, on_error=failed)

    def load_all(self):
        """Query every available service from the network."""
        if not self.ofono:
            self.toast(_("The modem is not working correctly."))
            return
        if self.ofono.has_modem_interface("org.ofono.CallForwarding"):
            self.enqueue(lambda: self.ofono.get_service_properties("forwarding"),
                          self.on_forwarding_loaded)
        else:
            self.grp_forwarding.set_description(_("Not available on this network"))
        if self.ofono.has_modem_interface("org.ofono.CallSettings"):
            self.enqueue(lambda: self.ofono.get_service_properties("settings"),
                          self.on_settings_loaded)
        if self.ofono.has_modem_interface("org.ofono.CallBarring"):
            self.enqueue(lambda: self.ofono.get_service_properties("barring"),
                          self.on_barring_loaded)
        else:
            self.grp_barring.set_description(_("Not available on this network"))

    def on_forwarding_loaded(self, props):
        """Populate the forwarding rows from the network reply."""
        if props is None:
            self.grp_forwarding.set_description(_("Could not read settings from the network"))
            return
        self.grp_forwarding.set_description(None)
        for prop, _key in FORWARDING_RULES:
            self.apply_service_value("forwarding", prop, props.get(prop, ""))
            self.cf_rows[prop].set_sensitive(True)
        self.apply_service_value("forwarding", "VoiceNoReplyTimeout", props.get("VoiceNoReplyTimeout", 20))
        self.row_cf_disable.set_sensitive(True)

    def on_settings_loaded(self, props):
        """Populate call waiting and caller id from the network reply."""
        if props is None:
            return
        self.apply_service_value("settings", "VoiceCallWaiting", props.get("VoiceCallWaiting", "disabled"))
        self.apply_service_value("settings", "HideCallerId", props.get("HideCallerId", "default"))
        self.sw_call_waiting.set_sensitive(True)
        self.row_clir.set_sensitive(True)

    def on_barring_loaded(self, props):
        """Populate the barring selectors from the network reply."""
        if props is None:
            self.grp_barring.set_description(_("Could not read settings from the network"))
            return
        self.grp_barring.set_description(None)
        self.apply_service_value("barring", "VoiceOutgoing", props.get("VoiceOutgoing", "disabled"))
        self.apply_service_value("barring", "VoiceIncoming", props.get("VoiceIncoming", "disabled"))
        self.row_barring_out.set_sensitive(True)
        self.row_barring_in.set_sensitive(True)
        self.exp_change_pw.set_sensitive(True)
        self.row_cb_disable.set_sensitive(True)

    def on_service_changed(self, _mgr, service, name, value):
        """Reflect a confirmed network change in the UI."""
        self.apply_service_value(service, name, value)

    def apply_service_value(self, service, name, value):
        """Write one confirmed service value into its row."""
        self._known[(service, name)] = value
        self._syncing_ui = True
        if service == "forwarding" and name in self.cf_rows:
            row = self.cf_rows[name]
            row.set_text(value or "")
            row.set_sensitive(True)
            if name == "VoiceNoReply":
                self.row_ring_time.set_sensitive(bool(value))
        elif service == "forwarding" and name == "VoiceNoReplyTimeout":
            values = list(RING_TIME_VALUES)
            if value not in values:
                values.append(value)
                values.sort()
            set_selector_options(self.row_ring_time,
                                 [_("{count} seconds").format(count=v) for v in values],
                                 values.index(value))
            self._ring_values = values
        elif service == "settings" and name == "VoiceCallWaiting":
            self.sw_call_waiting.set_active(value == "enabled")
            self.sw_call_waiting.set_sensitive(True)
        elif service == "settings" and name == "HideCallerId":
            idx = next((i for i, (k, _n) in enumerate(self.clir_options) if k == value), 0)
            set_selector_options(self.row_clir, [n for _k, n in self.clir_options], idx)
        elif service == "barring" and name == "VoiceOutgoing":
            idx = next((i for i, (k, _n) in enumerate(self.barring_out_options) if k == value), 0)
            set_selector_options(self.row_barring_out, [n for _k, n in self.barring_out_options], idx)
            self.row_barring_out.set_sensitive(True)
        elif service == "barring" and name == "VoiceIncoming":
            idx = next((i for i, (k, _n) in enumerate(self.barring_in_options) if k == value), 0)
            set_selector_options(self.row_barring_in, [n for _k, n in self.barring_in_options], idx)
            self.row_barring_in.set_sensitive(True)
        self._syncing_ui = False

    def on_forwarding_apply(self, row, prop):
        """Send an edited forwarding rule to the network."""
        number = row.get_text().strip()
        row.set_sensitive(False)

        def done(result):
            if result and result[0]:
                self.apply_service_value("forwarding", prop, number)
                if prop == "VoiceNoReply":
                    self.row_ring_time.set_sensitive(bool(number))
            else:
                self.apply_service_value("forwarding", prop, self._known.get(("forwarding", prop), ""))
                self.toast(_("Could not change the setting"))
            row.set_sensitive(True)

        self.enqueue(lambda: self.ofono.set_service_property("forwarding", prop, number), done)

    def on_ring_time_selected(self, idx):
        """Send the picked no-reply ring time to the network."""
        if self._syncing_ui:
            return
        seconds = self._ring_values[idx]

        def done(result):
            if not (result and result[0]):
                known = self._known.get(("forwarding", "VoiceNoReplyTimeout"), 20)
                self.apply_service_value("forwarding", "VoiceNoReplyTimeout", known)
                self.toast(_("Could not change the setting"))

        self.enqueue(lambda: self.ofono.set_service_property("forwarding", "VoiceNoReplyTimeout", int(seconds)), done)

    def confirm_disable_forwarding(self):
        """Confirm and clear every forwarding rule."""
        def on_resp(resp):
            if resp != "disable":
                return
            def done(result):
                if result and result[0]:
                    for prop, _key in FORWARDING_RULES:
                        self.apply_service_value("forwarding", prop, "")
                else:
                    self.toast(_("Could not change the setting"))
            self.enqueue(lambda: self.ofono.disable_all_forwarding(), done)
        present_alert_sheet(
            self.get_root(), _("Disable All Forwarding"),
            _("Turn off every call forwarding rule?"),
            [("cancel", _("Cancel"), None), ("disable", _("Disable"), "destructive")],
            on_resp)

    def on_call_waiting_toggled(self, row, _pspec):
        """Send the call waiting switch state to the network."""
        if self._syncing_ui:
            return
        target = "enabled" if row.get_active() else "disabled"
        row.set_sensitive(False)

        def done(result):
            if result and result[0]:
                self._known[("settings", "VoiceCallWaiting")] = target
            else:
                known = self._known.get(("settings", "VoiceCallWaiting"), "disabled")
                self.apply_service_value("settings", "VoiceCallWaiting", known)
                self.toast(_("Could not change the setting"))
            row.set_sensitive(True)

        self.enqueue(lambda: self.ofono.set_service_property("settings", "VoiceCallWaiting", target), done)

    def on_clir_selected(self, idx):
        """Send the caller id preference to the network."""
        if self._syncing_ui:
            return
        value = self.clir_options[idx][0]

        def done(result):
            if result and result[0]:
                self._known[("settings", "HideCallerId")] = value
            else:
                known = self._known.get(("settings", "HideCallerId"), "default")
                self.apply_service_value("settings", "HideCallerId", known)
                self.toast(_("Could not change the setting"))

        self.enqueue(lambda: self.ofono.set_service_property("settings", "HideCallerId", value), done)

    def barring_password(self):
        """Read the barring password entry, empty when missing."""
        return self.entry_barring_pw.get_text().strip()

    def on_barring_selected(self, prop, idx):
        """Send a barring rule change to the network."""
        if self._syncing_ui:
            return
        options = self.barring_out_options if prop == "VoiceOutgoing" else self.barring_in_options
        value = options[idx][0]
        password = self.barring_password()
        if not password:
            self.toast(_("Enter the barring password first"))
            known = self._known.get(("barring", prop), "disabled")
            self.apply_service_value("barring", prop, known)
            return

        def done(result):
            if result and result[0]:
                self._known[("barring", prop)] = value
            else:
                known = self._known.get(("barring", prop), "disabled")
                self.apply_service_value("barring", prop, known)
                self.toast(self.barring_error(result))

        self.enqueue(lambda: self.ofono.set_barring_property(prop, value, password), done)

    def barring_error(self, result):
        """Map a barring failure to a readable message."""
        if result and result[1] and "IncorrectPassword" in result[1]:
            return _("Wrong barring password")
        return _("Could not change the setting")

    def on_change_password(self):
        """Change the network barring password."""
        old = self.entry_pw_old.get_text().strip()
        new = self.entry_pw_new.get_text().strip()
        if not old or not new:
            self.toast(_("Enter the barring password first"))
            return

        def done(result):
            if result and result[0]:
                self.toast(_("Barring password changed"))
                self.entry_pw_old.set_text("")
                self.entry_pw_new.set_text("")
                self.exp_change_pw.set_expanded(False)
            else:
                self.toast(self.barring_error(result))

        self.enqueue(lambda: self.ofono.change_barring_password(old, new), done)

    def confirm_disable_barrings(self):
        """Confirm and clear every barring rule."""
        password = self.barring_password()
        if not password:
            self.toast(_("Enter the barring password first"))
            return

        def on_resp(resp):
            if resp != "disable":
                return
            def done(result):
                if result and result[0]:
                    self.apply_service_value("barring", "VoiceOutgoing", "disabled")
                    self.apply_service_value("barring", "VoiceIncoming", "disabled")
                else:
                    self.toast(self.barring_error(result))
            self.enqueue(lambda: self.ofono.disable_all_barrings(password), done)
        present_alert_sheet(
            self.get_root(), _("Disable All Barrings"),
            _("Turn off every call barring rule?"),
            [("cancel", _("Cancel"), None), ("disable", _("Disable"), "destructive")],
            on_resp)
