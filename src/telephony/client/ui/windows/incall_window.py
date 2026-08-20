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

from gi.repository import Gtk, GLib, Adw, Pango, Gio, GObject
import time
import urllib.parse

from telephony.shared.utils.log_utils import logger
from telephony.shared.services.system_state_service import SystemStateService
from gettext import gettext as _
from gettext import ngettext

from telephony.client.managers.call_feedback import CallFeedback
from telephony.client.ui.windows.fader_window import ProximityFader
from telephony.client.ui.windows.contact_picker_window import ContactPicker
from telephony.client.ui.widgets.incall_elements_widget import (DynamicHangupButton, create_truncated_label)
from telephony.client.ui.widgets.common_widget import (
                                                      install_sheet_host, present_sheet,
                                                      close_sheet, on_sheet_closed,
                                                      add_choice_row, present_sheet_page)
from telephony.client.managers.lockscreen_manager import LockScreenManager
from telephony.shared.utils.thread_utils import run_in_background
from telephony.client.utils.ofono_direct_utils import hangup_all_direct
from telephony.shared.utils.system_utils import save_modem_logs, press_power_button, is_gsd_airplane_mode
from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.call_state_utils import (count_lines, conference_paths, held_single_paths, held_conference_paths)

KEYPAD_LAYOUT = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#']
INCALL_SHEET_HEIGHT = 520
PILL_HEIGHT = 60
PAD_MORPH_DURATION_MS = 250

SEARCH_ENGINE_URLS = {
    "startpage": "https://www.startpage.com/do/dsearch?query={query}",
    "duckduckgo": "https://duckduckgo.com/?q={query}",
}


def call_state_label(state):
    """Return the translated display label for an ofono call state."""
    labels = {
        "active": _("Active"),
        "held": _("On Hold"),
        "dialing": _("Dialing..."),
        "alerting": _("Alerting..."),
        "incoming": _("Incoming Call..."),
        "waiting": _("Waiting..."),
        "disconnected": _("Disconnected"),
    }
    return labels.get(state, state)


def route_label(route_id):
    """Return the translated display label for an audio output route."""
    labels = {
        "earpiece": _("Earpiece"),
        "speaker": _("Speaker"),
        "wired": _("Wired Headset"),
        "bluetooth": _("Bluetooth"),
    }
    return labels.get(route_id, route_id)


def input_route_label(route_id):
    """Return the translated display label for an audio input route."""
    labels = {
        "mic": _("Microphone"),
        "wired": _("Wired Mic"),
        "bluetooth": _("Bluetooth Mic"),
    }
    return labels.get(route_id, route_id)


def route_icon(route_id):
    """Return the icon name for an audio output route."""
    icons = {
        "earpiece": "phone-symbolic",
        "speaker": "audio-speakers-symbolic",
        "wired": "audio-headset-symbolic",
        "bluetooth": "bluetooth-active-symbolic",
    }
    return icons.get(route_id, "phone-symbolic")


def input_route_icon(route_id):
    """Return the icon name for an audio input route."""
    icons = {
        "mic": "audio-input-microphone-symbolic",
        "wired": "audio-headset-symbolic",
        "bluetooth": "bluetooth-active-symbolic",
    }
    return icons.get(route_id, "audio-input-microphone-symbolic")


HANGUP_VERIFY_DELAY_MS = 4000
HANGUP_RETRY_DELAY_MS = 2000
KNOCK_REPEAT_SECONDS = 5


class InCallWindow(Adw.Window):
    """Main call window handling active calls, incoming calls, and call controls."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(InCallWindow, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, gsettings_mgr, ofono_manager, eds_manager, db_manager):
        """Initialize the InCallWindow."""
        if self._initialized:
            return
        super().__init__(title="Telephony", modal=False)
        self.set_icon_name("io.furios.Telephony.Calls")
        self._initialized = True
        self.gsettings_mgr = gsettings_mgr

        self.ofono = ofono_manager
        self.eds = eds_manager
        self.db = db_manager
        self.service_present = True
        self.audio = CallFeedback()
        self.fader = ProximityFader()

        self.lock_manager = LockScreenManager(self.ofono, self.eds, self.audio, self)

        self.active_path = None
        self.is_speaker = False
        self.is_muted = False
        self.dtmf_visible = False
        self.current_route = "earpiece"
        self.current_input_route = "mic"
        self.call_history = {}
        self.ignored_calls = set()

        self.is_closing = False
        self.in_error_mode = False
        self.in_recovery_mode = False
        self.manual_hangup = False

        self.is_locked = False

        self._timer_id = None
        self._proximity_timer_id = None
        self._hangup_verify_id = None
        self._hangup_retry_id = None
        self._next_knock_time = 0
        self._closing_paths = set()
        self.defer_present = True

        self._setup_ui()

        self.connect('close-request', self._on_close_req)

        self.sys_state = SystemStateService()
        self.is_locked = self.sys_state.is_locked

        self.signal_ids = [
            (self.ofono, self.ofono.connect('call-removed', self.on_call_removed)),
            (self.ofono, self.ofono.connect('call-added', lambda *a: self.update_state())),
            (self.ofono, self.ofono.connect('audio-changed', lambda *a: self._on_audio_changed())),
            (self.ofono, self.ofono.connect('call-changed', lambda *a: self.update_state())),
            (self.sys_state, self.sys_state.connect("lock-state-changed", self._on_lock_changed)),
        ]

        self.update_state()

    def _on_close_req(self, window):
        """Stay put during a call, go away once there is none.

        A call in progress keeps its window: losing it would leave the
        call running with nothing to hang it up from. Silencing a
        ringing call is the one way to put it aside, and that hides it
        directly. With no call left the window closes for real, which
        ends the process it runs in.
        """
        if self.in_recovery_mode or self.ofono.active_calls:
            return True

        self._release_signals()
        return False

    def _release_signals(self):
        """Stop listening to what outlives this window.

        The modem mirror is owned by the process, not by the window,
        and a call window is built again for every call. Handlers left
        on the mirror would keep a closed window alive and go on
        driving its destroyed widgets from the next call onwards.
        """
        for source, handler_id in self.signal_ids:
            if source.handler_is_connected(handler_id):
                source.disconnect(handler_id)
        self.signal_ids.clear()
        self.fader.release()

    def _proximity_tick(self):
        """Timer callback for proximity sensor handling."""
        if self.in_error_mode or not self.active_path:
            self.fader.set_active(False)
            self.audio.update_hardware_state(False)
            return True

        is_ear = (not self.is_speaker)
        self.audio.update_hardware_state(is_ear)
        should_blank = is_ear and self.audio.is_near
        self.fader.set_active(should_blank)
        return True

    def _setup_ui(self):
        """Build the in-call UI."""
        self.set_default_size(360, 600)
        self.add_css_class("incall-window")
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.main_box)
        self.set_content(self.toast_overlay)
        self.sheet_host = install_sheet_host(self)

        self.bg_scrolled = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=180)
        self.bg_calls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10, margin_bottom=10, margin_start=15, margin_end=15)
        self.bg_scrolled.set_child(self.bg_calls_box)
        self.main_box.append(self.bg_scrolled)

        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, vexpand=True, valign=Gtk.Align.CENTER, margin_start=20, margin_end=20)
        self.lbl_name = create_truncated_label(_("Unknown"), ["title-2"], max_chars=24)
        self.lbl_number = create_truncated_label("", ["title-2", "dim-label"], max_chars=24)
        self.lbl_status = Gtk.Label(css_classes=["body", "accent"])
        for lbl in [self.lbl_name, self.lbl_number, self.lbl_status]:
            self.info_box.append(lbl)

        self.anon_chip = Gtk.Box(spacing=6, css_classes=["anon-chip"],
                                 halign=Gtk.Align.CENTER, margin_top=6)
        self.anon_chip.append(Gtk.Image.new_from_icon_name("view-conceal-symbolic"))
        self.anon_chip.append(Gtk.Label(label=_("Your number is hidden")))
        self.anon_chip.set_visible(False)
        self.info_box.append(self.anon_chip)
        self.main_box.append(self.info_box)

        self.controls_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.main_box.append(self.controls_stack)

        inc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.END,
                          margin_bottom=16, margin_start=18, margin_end=18)

        self.btn_search_unknown = Gtk.Button(label=_("Search Number"), css_classes=["pill"], halign=Gtk.Align.CENTER)
        self.btn_search_unknown.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_search_unknown_click(b) or False))
        self.btn_search_unknown.set_visible(False)
        inc_box.append(self.btn_search_unknown)

        inc_box.append(self._mk_action_pill("call-start-symbolic", _("Accept"),
                                            self.on_answer_click, style="stack-pill-green"))
        self.pill_silence = self._mk_action_pill("audio-volume-muted-symbolic", _("Silence"),
                                                 self.on_silent_click)
        inc_box.append(self.pill_silence)
        inc_box.append(self._mk_action_pill("mail-message-new-symbolic", _("Hangup and Send SMS"),
                                            self.on_reject_with_msg))
        inc_box.append(self._mk_action_pill("call-stop-symbolic", _("Decline"),
                                            self.on_hangup_click, style="stack-pill-red"))
        self.controls_stack.add_named(inc_box, "incoming")

        act_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.END, margin_bottom=20)

        self.route_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=40, margin_end=40)
        self.btn_output, self.lbl_output_route, self.img_output_route = self._mk_selector_btn(route_icon("earpiece"), route_label("earpiece"), self.on_output_routing_click)
        self.btn_input, self.lbl_input_route, self.img_input_route = self._mk_selector_btn(input_route_icon("mic"), input_route_label("mic"), self.on_input_routing_click)
        self.route_box.append(self.btn_output)
        self.route_box.append(self.btn_input)

        pad_grid = Gtk.Grid(row_spacing=8, column_spacing=15, halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=14)
        for i, c in enumerate(KEYPAD_LAYOUT):
            b = Gtk.Button(label=c, css_classes=["pill", "dialpad-btn"], width_request=60, height_request=46)
            b.connect("clicked", lambda x, ch=c: GLib.idle_add(lambda: self.ofono.send_dtmf(ch) or False))
            pad_grid.attach(b, i % 3, i // 3, 1, 1)

        self.pad_route_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                         vhomogeneous=False, interpolate_size=True,
                                         transition_duration=PAD_MORPH_DURATION_MS)
        self.pad_route_stack.add_named(self.route_box, "routes")
        self.pad_route_stack.add_named(pad_grid, "pad")


        self.multiparty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pills_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.CENTER)
        self.btn_merge = Gtk.Button(css_classes=["pill", "suggested-action"])
        self.btn_merge.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_merge_click(b) or False))
        pills_row.append(self.btn_merge)
        self.btn_transfer = Gtk.Button(label=_("Transfer"), css_classes=["pill", "destructive-action"])
        self.btn_transfer.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_transfer_click(b) or False))
        pills_row.append(self.btn_transfer)
        self.multiparty_box.append(pills_row)
        self.lbl_transfer_hint = Gtk.Label(css_classes=["caption", "dim-label"], justify=Gtk.Justification.CENTER, wrap=True)
        self.multiparty_box.append(self.lbl_transfer_hint)
        self.multiparty_box.set_visible(False)

        self.actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18, halign=Gtk.Align.CENTER)
        mute_wrap, self.btn_mute = self._mk_labeled_btn("microphone-sensitivity-muted-symbolic", _("Mute"), self.on_mute_toggle)
        pad_wrap, self.btn_pad = self._mk_labeled_btn("input-dialpad-symbolic", _("Keypad"), self.on_pad_toggle)
        hold_wrap, self.btn_hold = self._mk_labeled_btn("media-playback-pause-symbolic", _("Hold"), self.on_hold_toggle)
        add_wrap, self.btn_add_call = self._mk_labeled_btn("contact-new-symbolic", _("Add Call"), self.on_add_call_click)
        self.actions_row.append(mute_wrap)
        self.actions_row.append(pad_wrap)
        self.actions_row.append(hold_wrap)
        self.actions_row.append(add_wrap)

        self._legacy_controls = Gtk.Box(visible=False)
        self._legacy_controls.append(self.pad_route_stack)
        self._legacy_controls.append(self.multiparty_box)
        self._legacy_controls.append(self.actions_row)
        act_box.append(self._legacy_controls)

        act_box.set_margin_start(18)
        act_box.set_margin_end(18)
        act_box.set_spacing(10)

        self.pill_audio = self._build_audio_pill()
        act_box.append(self.pill_audio)

        self.pill_keypad = self._build_stack_pill(
            "input-dialpad-symbolic", _("Keypad"), self._open_keypad_sheet)
        act_box.append(self.pill_keypad)

        self._context_mode = "actions"
        self.pill_context = self._build_context_pill()
        act_box.append(self.pill_context)

        self.btn_hangup_act = DynamicHangupButton()
        self.btn_hangup_act.set_halign(Gtk.Align.FILL)
        self.btn_hangup_act.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_hangup_click(b) or False))
        act_box.append(self.btn_hangup_act)
        self.controls_stack.add_named(act_box, "active")

        self.err_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.err_box.set_vexpand(True)
        self.err_box.set_hexpand(True)
        self.err_box.add_css_class("error-box")
        self.err_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20,
                                   valign=Gtk.Align.CENTER, vexpand=True)
        self.err_box.append(self.err_content)
        lbl_err = Gtk.Label(label=_("Modem Recovery"), css_classes=["error-title"])
        self.lbl_err_msg = Gtk.Label(label=_("The modem is not working correctly."), css_classes=["body", "error-text"])
        self.lbl_err_msg.set_wrap(True)
        self.lbl_err_detail = Gtk.Label(css_classes=["error-detail"])
        self.lbl_err_detail.set_wrap(True)
        self.lbl_err_detail.set_justify(Gtk.Justification.CENTER)
        self.lbl_err_detail.set_label(
            _("Calls, messages and mobile data will not work until it is restarted.\n\n"
              "Restarting takes about 30 seconds and ends any call in progress."))
        self.btn_restart = Gtk.Button(label=_("Recover Modem"))
        self.btn_restart.add_css_class("destructive-action")
        self.btn_restart.add_css_class("pill")
        self.btn_restart.set_size_request(240, 60)
        self.btn_restart.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_modem_recovery_click(b) or False))
        self.btn_save_logs = Gtk.Button(label=_("Save Modem Logs"))
        self.btn_save_logs.add_css_class("pill")
        self.btn_save_logs.set_size_request(240, 60)
        self.btn_save_logs.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_save_logs_click(b) or False))
        self.btn_reboot = Gtk.Button(label=_("Reboot phone"))
        self.btn_reboot.add_css_class("destructive-action")
        self.btn_reboot.add_css_class("pill")
        self.btn_reboot.set_size_request(240, 60)
        self.btn_reboot.set_visible(False)
        self.btn_reboot.connect("clicked", lambda b: GLib.idle_add(lambda: press_power_button() or False))
        self.err_content.append(lbl_err)
        self.err_content.append(self.lbl_err_msg)
        self.err_content.append(self.lbl_err_detail)
        self.err_content.append(self.btn_restart)
        self.err_content.append(self.btn_save_logs)
        self.err_content.append(self.btn_reboot)
        self.controls_stack.add_named(self.err_box, "error")

    def _mk_action_pill(self, icon, label, on_click, style="stack-pill"):
        """Build one full-width action pill for the incoming screen."""
        classes = ["destructive-action", "pill"] if style == "stack-pill-red" else [style]
        b = Gtk.Button(css_classes=classes)
        b.set_size_request(-1, PILL_HEIGHT)
        mid = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        mid.append(Gtk.Image.new_from_icon_name(icon))
        mid.append(Gtk.Label(label=label))
        b.set_child(mid)
        b.connect("clicked", lambda btn: GLib.idle_add(lambda: on_click(btn) or False))
        return b

    def _present_call_sheet(self, title):
        """Show the window's sheet on a fresh navigation and return it.

        Every call sheet shares one geometry, like the settings flows,
        and nested steps push pages on the returned navigation so back
        walks the flow instead of opening a second sheet.
        """
        nav = Adw.NavigationView()
        nav.set_size_request(-1, INCALL_SHEET_HEIGHT)
        present_sheet(self, nav)
        return nav

    def _push_sheet_page(self, nav, title, content, target_path=None):
        """Push one page onto a call sheet's navigation.

        Every page carries the caller strip, because the sheet covers
        the screen area that showed who the call is with; target_path
        pins the strip to another call when the page acts on one.
        """
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        wrap.append(self._build_caller_strip(target_path))
        content.set_vexpand(True)
        wrap.append(content)
        view.set_content(wrap)
        page = Adw.NavigationPage(title=title)
        page.set_child(view)
        page.set_focusable(True)
        nav.push(page)

    def _build_caller_strip(self, target_path=None):
        """Build a live caller line mirroring the main screen's labels.

        The labels are property-bound to the window's own name, number
        and status labels, so the timer keeps ticking inside the sheet
        and second calls and conferences show whatever the main screen
        shows; the bindings die with the strip. With a target_path the
        strip is a snapshot of that call instead, because the page
        acts on a call that is not the featured one.
        """
        strip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                        css_classes=["caller-strip"],
                        margin_start=14, margin_end=14)

        if target_path is not None:
            data = self.ofono.active_calls.get(target_path, {})
            number_text = data.get('number', '')
            name_text = self.call_history.get(target_path, {}).get('name') or number_text
            name = Gtk.Label(label=name_text, css_classes=["heading"])
            name.set_ellipsize(Pango.EllipsizeMode.END)
            strip.append(name)
            sub = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
            if number_text and number_text != name_text:
                sub.append(Gtk.Label(label=number_text, css_classes=["dim-label", "caption"]))
                sub.append(Gtk.Label(label="\u00b7", css_classes=["dim-label", "caption"]))
            sub.append(Gtk.Label(label=call_state_label(data.get('state', '')),
                                 css_classes=["dim-label", "caption"]))
            strip.append(sub)
            return strip

        name = Gtk.Label(css_classes=["heading"])
        name.set_ellipsize(Pango.EllipsizeMode.END)
        self.lbl_name.bind_property("label", name, "label", GObject.BindingFlags.SYNC_CREATE)
        strip.append(name)

        sub = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        number = Gtk.Label(css_classes=["dim-label", "caption"])
        number.set_ellipsize(Pango.EllipsizeMode.END)
        self.lbl_number.bind_property("label", number, "label", GObject.BindingFlags.SYNC_CREATE)
        dot = Gtk.Label(label="\u00b7", css_classes=["dim-label", "caption"])
        self.lbl_number.bind_property("label", dot, "visible", GObject.BindingFlags.SYNC_CREATE,
                                      lambda _binding, text: bool(text))
        self.lbl_number.bind_property("label", number, "visible", GObject.BindingFlags.SYNC_CREATE,
                                      lambda _binding, text: bool(text))
        status = Gtk.Label(css_classes=["dim-label", "caption"])
        self.lbl_status.bind_property("label", status, "label", GObject.BindingFlags.SYNC_CREATE)
        sub.append(number)
        sub.append(dot)
        sub.append(status)
        strip.append(sub)
        return strip

    def _rows_page(self, build):
        """Build one preferences page whose group build fills with rows."""
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        build(group)
        page.add(group)
        return page

    def _build_stack_pill(self, icon, label, on_click):
        """Build one full-width pill row that opens a sheet."""
        b = Gtk.Button(css_classes=["stack-pill"])
        b.set_size_request(-1, PILL_HEIGHT)
        center = Gtk.CenterBox()
        mid = Gtk.Box(spacing=7, halign=Gtk.Align.CENTER)
        mid.append(Gtk.Image.new_from_icon_name(icon))
        mid.append(Gtk.Label(label=label))
        center.set_center_widget(mid)
        b.set_child(center)
        b.connect("clicked", lambda btn: GLib.idle_add(lambda: on_click() or False))
        return b

    def _build_audio_pill(self):
        """Build the split audio row: an output pill and an input pill.

        Both halves follow the pill grammar of the rest of the stack;
        the input half turns red with the crossed microphone while the
        daemon reports the microphone muted.
        """
        row = Gtk.Box(spacing=8, homogeneous=True)

        self.pill_output = Gtk.Button(css_classes=["stack-pill"], hexpand=True)
        self.pill_output.set_size_request(-1, PILL_HEIGHT)
        center = Gtk.CenterBox(hexpand=True)
        mid = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        mid.append(Gtk.Image.new_from_icon_name("audio-volume-high-symbolic"))
        self.lbl_pill_out = Gtk.Label(css_classes=["stack-pill-state"])
        self.lbl_pill_out.set_ellipsize(Pango.EllipsizeMode.END)
        mid.append(self.lbl_pill_out)
        center.set_center_widget(mid)
        self.pill_output.set_child(center)
        self.pill_output.connect("clicked", lambda btn: GLib.idle_add(lambda: self._open_output_sheet() or False))
        row.append(self.pill_output)

        self.pill_input = Gtk.Button(css_classes=["stack-pill"], hexpand=True)
        self.pill_input.set_size_request(-1, PILL_HEIGHT)
        center = Gtk.CenterBox(hexpand=True)
        mid = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        self.img_pill_in = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        mid.append(self.img_pill_in)
        self.lbl_pill_in = Gtk.Label(css_classes=["stack-pill-state"])
        self.lbl_pill_in.set_ellipsize(Pango.EllipsizeMode.END)
        mid.append(self.lbl_pill_in)
        center.set_center_widget(mid)
        self.pill_input.set_child(center)
        self.pill_input.connect("clicked", lambda btn: GLib.idle_add(lambda: self._open_input_sheet() or False))
        row.append(self.pill_input)

        return row

    def _build_context_pill(self):
        """Build the contextual pill whose face follows the call mix."""
        b = Gtk.Button(css_classes=["stack-pill"])
        b.set_size_request(-1, PILL_HEIGHT)
        center = Gtk.CenterBox()
        mid = Gtk.Box(spacing=7, halign=Gtk.Align.CENTER)
        self.img_pill_ctx = Gtk.Image.new_from_icon_name("view-more-symbolic")
        mid.append(self.img_pill_ctx)
        self.lbl_pill_ctx = Gtk.Label(label=_("Actions"))
        mid.append(self.lbl_pill_ctx)
        self.lbl_pill_ctx_value = Gtk.Label(css_classes=["stack-pill-state"])
        mid.append(self.lbl_pill_ctx_value)
        center.set_center_widget(mid)
        b.set_child(center)
        b.connect("clicked", lambda btn: GLib.idle_add(lambda: self._open_context_sheet() or False))
        return b

    def _refresh_pills(self):
        """Mirror the daemon's audio truth and the call mix onto the pills."""
        audio = self.ofono.audio
        self.lbl_pill_out.set_text(route_label(audio.current_route))
        muted = audio.mic_muted
        self.lbl_pill_in.set_text(_("Muted") if muted else input_route_label(audio.current_input))
        self.img_pill_in.set_from_icon_name(
            "microphone-sensitivity-muted-symbolic" if muted else "audio-input-microphone-symbolic")
        for w in (self.img_pill_in, self.lbl_pill_in):
            if muted:
                w.add_css_class("stack-pill-muted")
            else:
                w.remove_css_class("stack-pill-muted")

        calls = self.ofono.active_calls
        conf = conference_paths(calls)
        if conf:
            self._context_mode = "conference"
            self.img_pill_ctx.set_from_icon_name("system-users-symbolic")
            self.lbl_pill_ctx.set_text(_("Participants"))
            self.lbl_pill_ctx_value.set_text(f"· {len(conf)}")
        elif count_lines(calls) >= 2:
            self._context_mode = "calls"
            self.img_pill_ctx.set_from_icon_name("call-start-symbolic")
            self.lbl_pill_ctx.set_text(_("Calls"))
            self.lbl_pill_ctx_value.set_text(f"· {len(calls)}")
        else:
            self._context_mode = "actions"
            self.img_pill_ctx.set_from_icon_name("view-more-symbolic")
            self.lbl_pill_ctx.set_text(_("Actions"))
            featured = calls.get(self.active_path) or {}
            if featured.get('state') == 'held':
                self.lbl_pill_ctx_value.set_text(f"\u00b7 {call_state_label('held')}")
            else:
                self.lbl_pill_ctx_value.set_text("")

    def _open_output_sheet(self):
        """Show the output routes on their own sheet."""
        def present(reply):
            outputs, _inputs = reply if reply else ([], [])
            nav = self._present_call_sheet(_("Output"))
            page = Adw.PreferencesPage()
            group = Adw.PreferencesGroup()
            for route_id, available in outputs:
                row = self._mk_route_row(route_icon(route_id), route_label(route_id),
                                         route_id == self.current_route, available)
                if row.get_sensitive():
                    row.connect("activated", lambda r, r_id=route_id: GLib.idle_add(
                        lambda: [close_sheet(self),
                                 self._handle_output_selection(r_id)] and False))
                group.add(row)
            page.add(group)
            self._push_sheet_page(nav, _("Output"), page)

        run_in_background(self.ofono.daemon.get_audio_routes, on_complete=present)

    def _open_input_sheet(self):
        """Show mute and the input routes on their own sheet."""
        def present(reply):
            _outputs, inputs = reply if reply else ([], [])
            audio = self.ofono.audio

            nav = self._present_call_sheet(_("Input"))
            page = Adw.PreferencesPage()

            mute_group = Adw.PreferencesGroup()
            mute_row = Adw.ActionRow(title=_("Mute"), activatable=False)
            mute_row.add_prefix(Gtk.Image.new_from_icon_name("microphone-sensitivity-muted-symbolic"))
            switch = Gtk.Switch(active=audio.mic_muted, valign=Gtk.Align.CENTER)
            switch.connect("state-set",
                           lambda s, state: self.ofono.daemon.set_mic_muted(state) or False)
            mute_row.add_suffix(switch)
            mute_row.set_activatable_widget(switch)
            mute_group.add(mute_row)
            page.add(mute_group)

            input_group = Adw.PreferencesGroup()
            for route_id, available in inputs:
                row = self._mk_route_row(input_route_icon(route_id), input_route_label(route_id),
                                         route_id == self.current_input_route, available)
                if row.get_sensitive():
                    row.connect("activated", lambda r, r_id=route_id: GLib.idle_add(
                        lambda: [close_sheet(self),
                                 self.ofono.daemon.set_mic_muted(False),
                                 self.ofono.daemon.set_input_route(r_id)] and False))
                input_group.add(row)
            page.add(input_group)

            self._push_sheet_page(nav, _("Input"), page)

        run_in_background(self.ofono.daemon.get_audio_routes, on_complete=present)

    def _open_keypad_sheet(self):
        """Show the DTMF keypad as a bottom sheet with an echo line."""
        nav = self._present_call_sheet(_("Keypad"))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                          margin_start=14, margin_end=14, margin_bottom=18)
        echo = Gtk.Label(label="", css_classes=["title-2"])
        echo.set_ellipsize(Pango.EllipsizeMode.START)
        content.append(echo)
        grid = Gtk.Grid(row_spacing=8, column_spacing=8, column_homogeneous=True)

        def press(_btn, ch):
            self.ofono.send_dtmf(ch)
            echo.set_text(echo.get_text() + ch)

        for i, c in enumerate(KEYPAD_LAYOUT):
            key = Gtk.Button(label=c, css_classes=["keypad-key"])
            key.connect("clicked", press, c)
            grid.attach(key, i % 3, i // 3, 1, 1)
        content.append(grid)

        speaker = self._mk_action_pill("audio-volume-high-symbolic", _("Turn to Speaker"),
                                       lambda b: self._handle_output_selection("speaker"))
        speaker.set_margin_top(6)
        speaker.set_visible(self.ofono.audio.current_route == "earpiece")
        content.append(speaker)
        handler = self.ofono.connect("audio-changed", lambda *a: GLib.idle_add(
            lambda: speaker.set_visible(self.ofono.audio.current_route == "earpiece") or False))
        on_sheet_closed(self, lambda: self.ofono.disconnect(handler))

        self._push_sheet_page(nav, _("Keypad"), content)

    def _open_context_sheet(self):
        """Open the sheet the context pill currently stands for."""
        if self._context_mode == "conference":
            self._open_participants_sheet()
        elif self._context_mode == "calls":
            self._open_calls_sheet()
        else:
            self._open_actions_sheet()

    def _open_actions_sheet(self):
        """Show hold and add-call for the single call."""
        calls = self.ofono.active_calls
        p_data = calls.get(self.active_path) or {}

        held = p_data.get('state') == 'held'

        def build(group):
            hold = add_choice_row(group, self,
                                  _("Resume") if held else _("Hold"),
                                  lambda: self.on_hold_toggle(None),
                                  icon="media-playback-start-symbolic" if held
                                  else "media-playback-pause-symbolic")
            hold.set_sensitive(p_data.get('state') in ('active', 'held'))
            add = add_choice_row(group, self, _("Add Call"), lambda: self.on_add_call_click(None),
                                 icon="contact-new-symbolic", opens_flow=True)
            add.set_sensitive(count_lines(calls) < 2 and p_data.get('state') in ('active', 'held'))

        nav = self._present_call_sheet(_("Actions"))
        self._push_sheet_page(nav, _("Actions"), self._rows_page(build))

    def _open_calls_sheet(self):
        """Show swap, merge and transfer for the two-call mix."""
        calls = self.ofono.active_calls
        held_normal = held_single_paths(calls)
        held_conf = held_conference_paths(calls)
        p_data = calls.get(self.active_path) or {}
        primary_free = bool(self.active_path) and p_data.get('state') == 'active' and not p_data.get('multiparty')
        conference_allowed = self.gsettings_mgr.get_setting("allow_conference_calls") == "true"
        transfer_allowed = self.gsettings_mgr.get_setting("allow_call_transfer") == "true"
        show_pair = bool(primary_free and held_normal and (conference_allowed or transfer_allowed))
        show_join = bool(primary_free and held_conf and not held_normal and conference_allowed)

        def build(group):
            add_choice_row(group, self, _("Swap Calls"), self.ofono.swap_calls,
                           icon="media-playback-start-symbolic")
            if show_pair and conference_allowed:
                add_choice_row(group, self, _("Merge Calls"),
                               lambda: self.on_merge_click(self.pill_context),
                               icon="object-flip-horizontal-symbolic")
            elif show_join:
                add_choice_row(group, self, _("Join Conference"),
                               lambda: self.on_merge_click(self.pill_context),
                               icon="object-flip-horizontal-symbolic")
            if show_pair and transfer_allowed:
                a = self.call_history.get(self.active_path, {}).get('name', _("Unknown"))
                b_name = self.call_history.get(held_normal[0], {}).get('name', _("Unknown"))
                add_choice_row(group, self, _("Transfer"),
                               lambda: self.on_transfer_click(self.pill_context),
                               subtitle=_("Transfer connects {a} and {b} together and you leave the call").format(a=a, b=b_name),
                               icon="send-to-symbolic")

        nav = self._present_call_sheet(_("Calls"))
        self._push_sheet_page(nav, _("Calls"), self._rows_page(build))

    def _open_participants_sheet(self):
        """Show every conference participant with split and hangup."""
        calls = self.ofono.active_calls
        conf = conference_paths(calls)

        def build(group):
            for path in conf:
                name = self.call_history.get(path, {}).get('name') or calls[path].get('number', _("Unknown"))
                row = Adw.ActionRow(title=name)
                b_priv = Gtk.Button(icon_name="call-outgoing-symbolic",
                                    css_classes=["flat", "circular"], valign=Gtk.Align.CENTER)
                b_priv.connect("clicked", lambda b, p=path: GLib.idle_add(
                    lambda: [close_sheet(self), self.on_private_chat_click(p)] and False))
                b_drop = Gtk.Button(icon_name="call-stop-symbolic",
                                    css_classes=["flat", "circular", "error"], valign=Gtk.Align.CENTER)
                b_drop.connect("clicked", lambda b, p=path: GLib.idle_add(
                    lambda: [close_sheet(self), self.ofono.hangup_call(p)] and False))
                row.add_suffix(b_priv)
                row.add_suffix(b_drop)
                group.add(row)

        nav = self._present_call_sheet(_("Participants"))
        self._push_sheet_page(nav, _("Participants"), self._rows_page(build))

    def _mk_btn(self, icon, cb, cls=None):
        """Helper to create a circular icon button."""
        b = Gtk.Button(icon_name=icon, css_classes=["circular"], width_request=70, height_request=70)
        if cls:
            b.add_css_class(cls)
        b.connect("clicked", lambda btn: GLib.idle_add(lambda: cb(btn) or False))
        return b

    def _mk_labeled_btn(self, icon, caption, cb, cls=None):
        """Create a circular icon button with a caption underneath."""
        btn = self._mk_btn(icon, cb, cls)
        btn.set_size_request(64, 64)
        btn.set_halign(Gtk.Align.CENTER)

        lbl = Gtk.Label(label=caption, css_classes=["caption", "dim-label"])
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(12)

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        wrap.append(btn)
        wrap.append(lbl)
        return wrap, btn

    def _mk_selector_btn(self, icon, text, cb):
        """Create a full-width route selector button showing the current choice."""
        btn = Gtk.Button(css_classes=["pill"])
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        img = Gtk.Image.new_from_icon_name(icon)
        row.append(img)

        lbl = Gtk.Label(label=text, hexpand=True, xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        row.append(lbl)

        chevron = Gtk.Image.new_from_icon_name("pan-up-symbolic")
        chevron.add_css_class("dim-label")
        row.append(chevron)

        btn.set_child(row)
        btn.connect("clicked", lambda b: GLib.idle_add(lambda: cb(b) or False))
        return btn, lbl, img

    def _show_output_route(self, route_id):
        """Reflect the active output route on its selector button."""
        self.lbl_output_route.set_text(route_label(route_id))
        self.img_output_route.set_from_icon_name(route_icon(route_id))

    def _show_input_route(self, route_id):
        """Reflect the active input route on its selector button."""
        self.lbl_input_route.set_text(input_route_label(route_id))
        self.img_input_route.set_from_icon_name(input_route_icon(route_id))

    def _present_choice_sheet(self, title, build_rows):
        """Show one group of choice rows in the window's sheet."""
        nav = self._present_call_sheet(title)
        self._push_sheet_page(nav, title, self._rows_page(build_rows))

    def _start_timers(self):
        """Start timers only when call is active."""
        if self._timer_id is None:
            self._timer_id = GLib.timeout_add(1000, self._update_timer)
        if self._proximity_timer_id is None:
            self._proximity_timer_id = GLib.timeout_add(100, self._proximity_tick)

    def _stop_timers(self):
        """Stop timers when idle."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if self._proximity_timer_id:
            GLib.source_remove(self._proximity_timer_id)
            self._proximity_timer_id = None

    def apply_service_presence(self, present, unit_state):
        """Grey the call actions while the service is away.

        The call itself lives in ofonod and continues; only our control
        path is gone, and the daemon's return re-syncs everything.
        Hangup stays enabled through a direct ofonod path, so a stuck
        call can always be ended, and answering stays enabled because
        the request revives the service through bus activation.
        """
        self.service_present = present
        for widget in (self.pill_audio, self.pill_keypad, self.pill_context):
            widget.set_sensitive(present)
        if present:
            self.update_state()
        else:
            self.lbl_status.set_text(_("Telephony service is restarting…"))

    def update_state(self):
        """Refresh call state and UI."""
        self.lock_manager.set_locked(self.is_locked)

        if self.in_recovery_mode:
            if not self.ofono.active_calls:
                if not self.is_locked and not self.is_visible():
                    logger.info("[InCall] Showing the recovery page held back by the lock screen")
                    self.defer_present = False
                    self.present()
                return
            logger.info("[InCall] Call appeared while on the recovery page, showing call UI")
            self.in_recovery_mode = False

        if self.in_error_mode:
            if not self.ofono.active_calls:
                logger.info("[InCall] Stuck call released on its own, recovering from error state")
                self._reset_from_error()
                return
            if self.is_locked:
                self.lock_manager.show_stuck_notification()
            return

        calls = self.ofono.active_calls

        self.lock_manager.sync_notifications(calls, self.call_history, self.ignored_calls)

        if self.is_closing:
            if not calls:
                self.is_closing = False
                self._clean_reset()
                return
            if self._closing_paths & set(calls):
                return
            logger.info("[InCall] New call arrived during hangup teardown, recovering")
            self._recover_from_closing()

        if not calls:
            if self.active_path is not None or self.is_muted or self.is_speaker:
                self._clean_reset()
            if self.is_visible():
                self.set_visible(False)
            return

        self._start_timers()

        if not self.defer_present:
            self.present()
        if not self.is_locked:
            self.lock_manager.clear_all()

        self.btn_hangup_act.update_mode(len(calls))

        dead_paths = [p for p in self.call_history if p not in calls]
        for p in dead_paths:
            del self.call_history[p]

        sorted_c = []
        for p, d in calls.items():
            name = self.eds.get_contact_name(d['number'])
            if not name or name == "Unknown":
                name = _("Unknown")

            knocked = self.call_history.get(p, {}).get('knocked', False)
            self.call_history[p] = {'name': name, 'number': d['number'], 'state': d['state'], 'knocked': knocked}
            score = {'active': 10, 'incoming': 5, 'dialing': 8, 'alerting': 8, 'held': 1}.get(d['state'], 0)
            sorted_c.append((score, p, d))

        sorted_c.sort(key=lambda x: x[0], reverse=True)
        if not sorted_c:
            return

        self.active_path, p_data = sorted_c[0][1], sorted_c[0][2]

        is_unknown = self.call_history[self.active_path]['name'] == _("Unknown")

        self.lbl_name.set_text(self.call_history[self.active_path]['name'])
        self.lbl_number.set_text(p_data['number'])

        has_incoming = any(c[2]['state'] == 'incoming' for c in sorted_c)
        if len(calls) > 1 and has_incoming and not self.manual_hangup:
            for score, path, c_data in sorted_c:
                if c_data['state'] == 'incoming':
                    if not self.call_history[path].get('knocked', False) and not self._bg_call_is_silenced(path, c_data):
                        self.audio.play_knock()
                        self.call_history[path]['knocked'] = True
                        self._next_knock_time = time.time() + KNOCK_REPEAT_SECONDS

        if p_data['state'] == 'incoming':
            uc_search = self.gsettings_mgr.get_setting("unknown_callers_search") == "true"
            uc_action = self.gsettings_mgr.get_setting("unknown_callers") or "none"

            if is_unknown and uc_search:
                self.btn_search_unknown.set_visible(True)
            else:
                self.btn_search_unknown.set_visible(False)

            is_silenced = p_data.get('silenced', False)

            caller_norm = normalize_number(p_data['number'])
            override_volume = False

            try:
                priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts()
                for p in priority_list:
                    p_num = normalize_number(p.get("number", ""))
                    if p_num and p_num == caller_norm:
                        override_volume = True
                        break
            except Exception as e:
                logger.warning(f"[InCall] Priority caller check failed: {e}")

            if override_volume:
                is_silenced = False

            if uc_action in ["hide", "silence"] and is_unknown and not override_volume:
                is_silenced = True

            self.anon_chip.set_visible(False)
            self.controls_stack.set_visible_child_name("incoming")
            self.pill_silence.set_sensitive(not is_silenced)
            self.lbl_status.set_text(_("Incoming Call...") if not is_silenced else _("Silenced Incoming Call"))
        else:
            self.anon_chip.set_visible(bool(p_data.get('anonymous')))
            self.controls_stack.set_visible_child_name("active")
            self._show_output_route(self.current_route)
            self._toggle_blue(self.btn_mute, self.is_muted)

            can_hold = p_data['state'] in ['active', 'held']
            self.btn_hold.set_sensitive(can_hold)

            self._toggle_blue(self.btn_hold, p_data['state'] == 'held')
            self.lbl_status.set_text(call_state_label(p_data['state']))

        conf_paths = conference_paths(calls)
        primary_in_conf = self.active_path in conf_paths

        if primary_in_conf:
            self.lbl_name.set_text(_("Conference Call"))
            self.lbl_number.set_text(ngettext("{count} participant", "{count} participants",
                                              len(conf_paths)).format(count=len(conf_paths)))

        self._update_multiparty_actions(calls, p_data, conf_paths)

        bg_list = [(x[1], x[2]) for x in sorted_c[1:] if x[1] not in conf_paths]
        self._render_bg(bg_list, conf_paths, primary_in_conf)
        self._refresh_pills()

    def _bg_call_is_silenced(self, path, c_data):
        """Return True when a background incoming call must stay silent."""
        caller_norm = normalize_number(c_data['number'])
        override_volume = False
        try:
            priority_list = self.gsettings_mgr.get_notification_override_dnd_bypass_contacts()
            for p in priority_list:
                p_num = normalize_number(p.get("number", ""))
                if p_num and p_num == caller_norm:
                    override_volume = True
                    break
        except Exception as e:
            logger.warning(f"[InCall] Priority caller check failed: {e}")

        is_silenced = c_data.get('silenced', False)
        uc_action = self.gsettings_mgr.get_setting("unknown_callers") or "none"
        c_unknown = self.call_history.get(path, {}).get('name') == _("Unknown")
        if uc_action in ["hide", "silence"] and c_unknown and not override_volume:
            is_silenced = True
        return is_silenced

    def _maybe_repeat_knock(self):
        """Repeat the call-waiting knock for eligible background calls."""
        now = time.time()
        if now < self._next_knock_time:
            return
        for path, data in self.ofono.active_calls.items():
            if data['state'] not in ('waiting', 'incoming') or path == self.active_path:
                continue
            if path in self.ignored_calls:
                continue
            if self._bg_call_is_silenced(path, data):
                continue
            self.audio.play_knock()
            self._next_knock_time = now + KNOCK_REPEAT_SECONDS
            return

    def _clean_reset(self):
        """Reset window state to idle."""
        self.main_box.remove_css_class("recovery-mode")
        self.info_box.set_visible(True)
        self.controls_stack.set_vexpand(False)
        self._stop_timers()
        self._next_knock_time = 0
        if self._hangup_verify_id:
            GLib.source_remove(self._hangup_verify_id)
            self._hangup_verify_id = None
        if self._hangup_retry_id:
            GLib.source_remove(self._hangup_retry_id)
            self._hangup_retry_id = None
        self._closing_paths = set()

        self.current_route = "earpiece"

        self.lock_manager.clear_all()
        self.audio.update_hardware_state(False)

        self.active_path = None
        self.is_speaker = False
        self.is_muted = False
        self.dtmf_visible = False
        self.manual_hangup = False
        self.ignored_calls.clear()
        self.fader.set_active(False)

        self.current_input_route = "mic"
        self._show_output_route("earpiece")
        self._show_input_route("mic")
        self._toggle_blue(self.btn_mute, False)
        self._toggle_blue(self.btn_pad, False)
        self.pad_route_stack.set_transition_duration(0)
        self.pad_route_stack.set_visible_child_name("routes")
        self.pad_route_stack.set_transition_duration(PAD_MORPH_DURATION_MS)

        self.call_history.clear()
        if self.is_visible():
            self.set_visible(False)

    def _update_multiparty_actions(self, calls, p_data, conf_paths):
        """Show the merge, join and transfer actions matching the call mix."""
        held_normal = held_single_paths(calls)
        held_conf = held_conference_paths(calls)
        primary_free = bool(self.active_path) and p_data['state'] == 'active' and not p_data.get('multiparty')

        conference_allowed = self.gsettings_mgr.get_setting("allow_conference_calls") == "true"
        transfer_allowed = self.gsettings_mgr.get_setting("allow_call_transfer") == "true"

        show_pair = bool(primary_free and held_normal and (conference_allowed or transfer_allowed))
        show_join = bool(primary_free and held_conf and not held_normal and conference_allowed)

        if show_pair:
            self.btn_merge.set_label(_("Merge Calls"))
            self.btn_merge.set_visible(conference_allowed)
            self.btn_transfer.set_visible(transfer_allowed)
            a = self.call_history.get(self.active_path, {}).get('name', _("Unknown"))
            b = self.call_history.get(held_normal[0], {}).get('name', _("Unknown"))
            self.lbl_transfer_hint.set_text(
                _("Transfer connects {a} and {b} together and you leave the call").format(a=a, b=b))
            self.lbl_transfer_hint.set_visible(True)
        elif show_join:
            self.btn_merge.set_label(_("Join Conference"))
            self.btn_merge.set_visible(True)
            self.btn_transfer.set_visible(False)
            self.lbl_transfer_hint.set_visible(False)
        self.multiparty_box.set_visible(show_pair or show_join)

        self.lbl_transfer_hint.set_visible(self.lbl_transfer_hint.get_visible() and transfer_allowed)

        self.btn_add_call.set_sensitive(count_lines(calls) < 2 and p_data['state'] in ('active', 'held'))

    def _build_participants_card(self, conf_paths):
        """Build the conference participants card with per leg actions."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["card"])
        title = Gtk.Label(label=_("Participants"), css_classes=["caption-heading"], halign=Gtk.Align.START)
        card.append(title)
        for path in conf_paths:
            data = self.ofono.active_calls.get(path)
            if not data:
                continue
            name = self.eds.get_contact_name(data['number'])
            if not name or name == "Unknown":
                name = data['number'] or _("Unknown")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = create_truncated_label(name, ["caption-heading"], max_chars=22)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_hexpand(True)
            row.append(lbl)
            b_priv = Gtk.Button(icon_name="avatar-default-symbolic", css_classes=["circular", "flat"])
            b_priv.set_tooltip_text(_("Private Chat"))
            b_priv.connect("clicked", lambda b, p=path: GLib.idle_add(lambda: self.on_private_chat_click(p) or False))
            row.append(b_priv)
            b_drop = Gtk.Button(icon_name="call-stop-symbolic", css_classes=["circular", "destructive-action"])
            b_drop.connect("clicked", lambda b, p=path: GLib.idle_add(lambda: self.ofono.hangup_call(p) or False))
            row.append(b_drop)
            card.append(row)
        hint = Gtk.Label(label=_("Private moves the others to hold"), css_classes=["caption", "dim-label"], xalign=0)
        card.append(hint)
        return card

    def _build_held_conference_card(self, count):
        """Build the held conference summary card with swap."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["card"])
        lbl_title = create_truncated_label(
            _("Conference Call") + f" ({call_state_label('held')})", ["caption-heading"], max_chars=30)
        lbl_title.set_halign(Gtk.Align.START)
        card.append(lbl_title)
        lbl_num = create_truncated_label(
            ngettext("{count} participant", "{count} participants", count).format(count=count),
            ["caption", "dim-label"], max_chars=30)
        lbl_num.set_halign(Gtk.Align.START)
        card.append(lbl_num)
        btn_box = Gtk.Box(spacing=8, margin_top=4, homogeneous=True)
        b_swap = Gtk.Button(label=_("Swap"), css_classes=["pill", "bg-action"])
        b_swap.connect("clicked", lambda b: GLib.idle_add(lambda: self.ofono.swap_calls() or False))
        btn_box.append(b_swap)
        card.append(btn_box)
        return card

    def on_merge_click(self, btn):
        """Join the active and held calls into one conference."""
        btn.set_sensitive(False)
        run_in_background(self.ofono.create_multiparty,
                          on_complete=lambda result: self._on_multiparty_done(result, btn))

    def on_transfer_click(self, btn):
        """Connect the two calls to each other and leave."""
        btn.set_sensitive(False)
        run_in_background(self.ofono.transfer_call,
                          on_complete=lambda result: self._on_multiparty_done(result, btn))

    def on_private_chat_click(self, path):
        """Split one participant out of the conference."""
        run_in_background(self.ofono.private_chat, path,
                          on_complete=lambda result: self._on_multiparty_done(result, None))

    def _on_multiparty_done(self, result, btn):
        """Re-enable the action and report a refused network request."""
        if btn is not None:
            btn.set_sensitive(True)
        if not (result and result[0]):
            self.toast_overlay.add_toast(Adw.Toast.new(_("The network refused the request")))

    def on_add_call_click(self, btn):
        """Pick a contact or number and dial it as a second call."""
        picker = ContactPicker(self.eds, self, self._on_add_call_picked,
                               title=_("Add Call"), action_label=_("Call"))
        present_sheet_page(self, picker)

    def _on_add_call_picked(self, number):
        """Dial the picked number; ofono holds the current call itself."""
        if number:
            self.ofono.dial(number)

    def _render_bg(self, bg_list, conf_paths, primary_in_conf):
        """Render background calls and the conference card."""
        while c := self.bg_calls_box.get_first_child():
            self.bg_calls_box.remove(c)
        if conf_paths and primary_in_conf:
            self.bg_calls_box.append(self._build_participants_card(conf_paths))
        elif conf_paths:
            self.bg_calls_box.append(self._build_held_conference_card(len(conf_paths)))
        for path, data in bg_list:
            if path in self.ignored_calls:
                continue
            name = self.eds.get_contact_name(data['number'])
            if not name or name == "Unknown":
                name = _("Unknown")

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["card"])
            lbl_title = create_truncated_label(f"{name} ({call_state_label(data['state'])})", ["caption-heading"], max_chars=30)
            lbl_title.set_halign(Gtk.Align.START)
            card.append(lbl_title)

            lbl_num = create_truncated_label(data['number'], ["caption", "dim-label"], max_chars=30)
            lbl_num.set_halign(Gtk.Align.START)
            card.append(lbl_num)

            btn_box = Gtk.Box(spacing=8, margin_top=4, homogeneous=True)
            if data['state'] == 'held':
                b_swap = Gtk.Button(label=_("Swap"), css_classes=["pill", "bg-action"])
                b_swap.connect("clicked", lambda b: GLib.idle_add(lambda: self.ofono.swap_calls() or False))
                btn_box.append(b_swap)
            elif data['state'] in ['incoming', 'waiting']:
                b_act = Gtk.Button(label=_("Answer"), css_classes=["pill", "btn-green", "bg-action"])
                b_act.connect("clicked", lambda b, p=path: GLib.idle_add(lambda: self.ofono.answer_call(p) or False))
                btn_box.append(b_act)

                b_hide = Gtk.Button(label=_("Silence"), css_classes=["pill", "bg-action"])
                b_hide.connect("clicked", lambda b, p=path: GLib.idle_add(lambda: self.on_ignore_call(p) or False))
                btn_box.append(b_hide)

                b_sms = Gtk.Button(label=_("Message"), css_classes=["pill", "bg-action"])
                b_sms.connect("clicked", lambda b, p=path, n=data['number']: GLib.idle_add(lambda: self.on_ignore_with_sms(b, p, n) or False))
                btn_box.append(b_sms)
            card.append(btn_box)

            if data['state'] in ['incoming', 'waiting']:
                lbl_hint = Gtk.Label(label=_("Answering holds the current call"), css_classes=["caption", "dim-label"], xalign=0)
                card.append(lbl_hint)

            self.bg_calls_box.append(card)

    def on_ignore_call(self, path):
        """Ignore a background call."""
        self.ignored_calls.add(path)
        self.ofono.daemon.silence_ring()
        self.update_state()

    def _pick_quick_response(self, anchor, callback, target_path=None):
        """Invoke callback with a quick response message, showing a picker when several exist.

        target_path names the call the message goes to when it is not
        the featured one, so the sheet's caller strip shows the actual
        recipient instead of whoever is on the line.
        """
        messages = self.gsettings_mgr.get_reject_call_messages()
        if not messages:
            messages = [_("I can't talk right now.")]

        if len(messages) == 1:
            callback(messages[0])
            return

        def build(group):
            for msg in messages:
                row = Adw.ActionRow(title=msg, activatable=True)
                row.set_title_lines(2)

                def _cb(row_widget, m=msg):
                    close_sheet(self)
                    callback(m)
                row.connect("activated", _cb)
                group.add(row)

        nav = self._present_call_sheet(_("Hangup and Send SMS"))
        self._push_sheet_page(nav, _("Hangup and Send SMS"), self._rows_page(build),
                              target_path=target_path)

    def on_ignore_with_sms(self, btn, path, number):
        """Ignore a call and send a quick response."""
        def do_ignore(msg):
            self.ofono.send_quick_response(number, msg)
            self.ignored_calls.add(path)
            self.ofono.daemon.silence_ring()
            self.update_state()

        self._pick_quick_response(btn, do_ignore, target_path=path)

    def on_search_unknown_click(self, btn):
        """Open web browser to search for unknown number."""
        if not self.active_path:
            return

        number = self.ofono.active_calls[self.active_path]['number']
        clean_num = number.replace("+", "")

        engine = self.gsettings_mgr.get_setting("unknown_callers_engine") or "duckduckgo"
        custom_url = self.gsettings_mgr.get_setting("unknown_callers_custom_url") or ""

        search_url = ""
        encoded_num = urllib.parse.quote(clean_num)

        if engine in SEARCH_ENGINE_URLS:
            search_url = SEARCH_ENGINE_URLS[engine].format(query=encoded_num)
        elif engine == "custom" and custom_url:
            search_url = custom_url.replace("{number}", encoded_num)

        if search_url:
            from gi.repository import Gio
            Gio.AppInfo.launch_default_for_uri(search_url, None)

    def on_silent_click(self, btn):
        """Silences the ringer and hides the window."""
        self.ofono.daemon.silence_ring()
        self.set_visible(False)

    def on_answer_click(self, btn):
        """Answer the active incoming call."""
        if self.active_path:
            self.ofono.answer_call(self.active_path)

    def on_hangup_click(self, btn):
        """Hangup the active call or all calls."""
        self.manual_hangup = True
        self._start_closing_sequence()
        self.set_visible(False)
        if not self.service_present:
            run_in_background(hangup_all_direct)
            return
        remaining = self.ofono.active_calls
        if len(remaining) > 1 and all(d.get('multiparty') for d in remaining.values()):
            run_in_background(self.ofono.hangup_multiparty)
        elif len(remaining) > 1:
            self.ofono.hangup_all()
        elif self.active_path:
            self.ofono.hangup_call(self.active_path)
        else:
            self.ofono.hangup_all()

    def _start_closing_sequence(self):
        """Start the call closing animation/logic."""
        self.is_closing = True
        self._closing_paths = set(self.ofono.active_calls.keys())
        self.ofono.daemon.silence_ring()
        if self._hangup_verify_id:
            GLib.source_remove(self._hangup_verify_id)
        self._hangup_verify_id = GLib.timeout_add(HANGUP_VERIFY_DELAY_MS, self._verify_hangup_success)
        if self._hangup_retry_id:
            GLib.source_remove(self._hangup_retry_id)
        self._hangup_retry_id = GLib.timeout_add(HANGUP_RETRY_DELAY_MS, self._retry_hangup)

    def _retry_hangup(self):
        """Re-send the hangup for calls the modem has not confirmed released.

        The ofono binder plugin silently drops a hangup that arrives while
        an IMS call is still in early dialing; a second hangup once the
        call reached alerting reliably lands.
        """
        self._hangup_retry_id = None
        if not self.is_closing:
            return False
        stale = self._closing_paths & set(self.ofono.active_calls)
        if not stale:
            return False
        logger.warning(f"[InCall] Hangup unconfirmed, re-sending for {len(stale)} call(s)")
        for path in stale:
            self.ofono.hangup_call(path)
        return False

    def _recover_from_closing(self):
        """Reset the closing state and re-process calls that arrived meanwhile."""
        if self._hangup_verify_id:
            GLib.source_remove(self._hangup_verify_id)
            self._hangup_verify_id = None
        if self._hangup_retry_id:
            GLib.source_remove(self._hangup_retry_id)
            self._hangup_retry_id = None
        self.is_closing = False
        self._closing_paths = set()
        self._clean_reset()
        if self.ofono.active_calls:
            self.update_state()

    def _verify_hangup_success(self):
        """Verify the hung-up calls are gone, otherwise trigger error state."""
        self._hangup_verify_id = None
        if self._closing_paths & set(self.ofono.active_calls):
            self._enter_error_state()
            return False
        self._recover_from_closing()
        return False

    def _enter_error_state(self):
        """Show the recovery page when a call fails to disconnect."""
        self.audio.play_error_alert()
        self.present()
        self.in_error_mode = True
        self.is_closing = False
        self.audio.play_hangup()
        self.fader.set_active(False)
        self.audio.update_hardware_state(False)
        self.is_speaker = False
        self.lbl_err_msg.set_text(_("The call failed to disconnect."))
        self.btn_restart.set_label(_("Recover Modem"))
        self.btn_restart.set_sensitive(True)
        self.btn_reboot.set_visible(False)
        self.controls_stack.set_visible_child_name("error")
        self.main_box.add_css_class("recovery-mode")
        self.info_box.set_visible(False)
        self.controls_stack.set_vexpand(True)

        app = Gio.Application.get_default()
        auto = self.gsettings_mgr.get_setting("automatic_modem_recovery") == "true"
        if auto and app and app.request_auto_recovery(self._on_recovery_done):
            self.btn_restart.set_sensitive(False)
            self.btn_restart.set_label(_("Recovering modem..."))
        else:
            self.lock_manager.show_stuck_notification()

    def enter_recovery_mode(self, reason, failed=False):
        """Show the modem recovery page; it stays until the modem works again.

        Whether this window was ever on screen used to depend on how its
        process came to exist, since filling the page in is not the same
        as asking to be seen. A recovery reaching a process that was
        already running left it repairing the modem invisibly.

        A locked phone is told about it by notification instead, so the
        page is prepared and left waiting rather than shown to nobody.
        """
        if not self.in_recovery_mode and not self.in_error_mode:
            self.audio.play_error_alert()
            self.lbl_name.set_text("")
            self.lbl_number.set_text("")
            self.lbl_status.set_text("")
        self.in_recovery_mode = True
        self.in_error_mode = False
        self.is_closing = False
        self.fader.set_active(False)
        self.audio.update_hardware_state(False)
        if failed:
            self.lbl_err_msg.set_text(_("Could not restore the modem. Please reboot the phone."))
        else:
            self.lbl_err_msg.set_text(reason)
        self.btn_restart.set_label(_("Recover Modem"))
        self.btn_restart.set_sensitive(True)
        self.btn_reboot.set_visible(failed)
        self.controls_stack.set_visible_child_name("error")
        self.main_box.add_css_class("recovery-mode")
        self.info_box.set_visible(False)
        self.controls_stack.set_vexpand(True)
        if self.is_locked:
            logger.info("[InCall] Recovery page ready but the phone is locked, notifying instead")
            self.lock_manager.show_stuck_notification()
            return
        logger.info("[InCall] Showing the recovery page")
        self.defer_present = False
        self.present()

    def exit_recovery_mode(self):
        """Leave the recovery page once the modem works again.

        Resetting only hides the window, and this window is its own
        process, so a repair that ended without a call ending left a
        process standing with nothing on screen to explain it.
        """
        if not self.in_recovery_mode:
            return
        self.in_recovery_mode = False
        self.btn_reboot.set_visible(False)
        self.main_box.remove_css_class("recovery-mode")
        self.info_box.set_visible(True)
        self.controls_stack.set_vexpand(False)
        if self.ofono.active_calls:
            self.update_state()
            return
        self._clean_reset()
        self._close_when_idle()

    def _on_recovery_done(self, success):
        """React to the recovery verdict while the page is showing."""
        if not self.in_error_mode and not self.in_recovery_mode:
            return
        self.btn_restart.set_label(_("Recover Modem"))
        self.btn_restart.set_sensitive(True)
        if success:
            if self.in_error_mode:
                self._reset_from_error()
            return
        if self.in_error_mode and self.is_locked:
            self.lock_manager.show_stuck_notification()

    def on_modem_recovery_click(self, btn):
        """Restart the modem stack from the recovery page.

        Restarting cannot power on a radio that was switched off, so a
        recovery asked for during airplane mode would only report a
        failure over something that is not broken.
        """
        app = Gio.Application.get_default()
        if not app:
            return
        if is_gsd_airplane_mode():
            self.lbl_err_msg.set_text(_("Airplane mode is on"))
            return
        if app.request_auto_recovery(self._on_recovery_done):
            self.btn_restart.set_sensitive(False)
            self.btn_restart.set_label(_("Recovering modem..."))

    def on_save_logs_click(self, btn):
        """Capture modem evidence for a bug report."""
        btn.set_sensitive(False)

        def done(path):
            btn.set_sensitive(True)
            if path:
                self.lbl_err_msg.set_text(_("Logs saved to {path}").format(path=path))
            else:
                self.lbl_err_msg.set_text(_("Saving logs failed"))

        run_in_background(save_modem_logs, on_complete=done)

    def _reset_from_error(self, *args):
        """Reset UI after error recovery."""
        self.in_error_mode = False
        self._clean_reset()
        return False

    def on_call_removed(self, m, p):
        """Handle call removed signal."""
        if self.is_closing:
            if not (self._closing_paths & set(self.ofono.active_calls)):
                logger.info("[InCall] Hangup confirmed by ofono, finishing teardown")
                self._recover_from_closing()
                self._close_when_idle()
            return
        if self.in_error_mode:
            if not self.ofono.active_calls:
                logger.info("[InCall] Stuck call released on its own, recovering from error state")
                self._reset_from_error()
            return
        self.audio.play_hangup(feedback=False)
        self.fader.set_active(False)
        if p in self.ignored_calls:
            self.ignored_calls.remove(p)
        self.update_state()
        self._close_when_idle()

    def _close_when_idle(self):
        """Close once the last call is gone, unless the modem needs the page.

        This window is its own process now, so a window left standing
        after the call holds a process worth of memory until the phone
        is restarted.
        """
        if self.ofono.active_calls or self.in_recovery_mode or self.in_error_mode:
            return
        logger.info("[InCall] No calls left, closing")
        self.close()

    def _mk_route_row(self, icon_name, name, selected, available):
        """Build one selectable route row for a routing popover."""
        row = Adw.ActionRow(title=name)
        row.add_prefix(Gtk.Image.new_from_icon_name(icon_name))

        if not available:
            row.set_subtitle(_("Not connected"))
            row.set_sensitive(False)
            return row

        if selected:
            row.add_suffix(Gtk.Image.new_from_icon_name("object-select-symbolic"))

        row.set_activatable(True)
        return row

    def on_output_routing_click(self, btn):
        """Show the output routing sheet from the daemon's route list."""
        def present(reply):
            if reply is None:
                return
            outputs, _inputs = reply

            def build(group):
                for route_id, available in outputs:
                    row = self._mk_route_row(route_icon(route_id), route_label(route_id),
                                             route_id == self.current_route, available)

                    def _cb_out(row_widget, r_id=route_id):
                        close_sheet(self)
                        self._handle_output_selection(r_id)
                    if row.get_sensitive():
                        row.connect("activated", _cb_out)
                    group.add(row)

            self._present_choice_sheet(_("Output"), build)

        run_in_background(self.ofono.daemon.get_audio_routes, on_complete=present)

    def on_input_routing_click(self, btn):
        """Show the input routing sheet from the daemon's route list."""
        def present(reply):
            if reply is None:
                return
            _outputs, inputs = reply

            def build(group):
                for route_id, available in inputs:
                    row = self._mk_route_row(input_route_icon(route_id), input_route_label(route_id),
                                             route_id == self.current_input_route, available)

                    def _cb_in(row_widget, r_id=route_id):
                        close_sheet(self)
                        if self.is_muted:
                            self.on_mute_toggle(None)
                        self.ofono.daemon.set_input_route(r_id)
                    if row.get_sensitive():
                        row.connect("activated", _cb_in)
                    group.add(row)

            self._present_choice_sheet(_("Input"), build)

        run_in_background(self.ofono.daemon.get_audio_routes, on_complete=present)

    def _handle_output_selection(self, route_id):
        """Send the route intent; the daemon's broadcast renders it."""
        self.ofono.daemon.set_audio_route(route_id)
        self.lock_manager.sync_notifications(self.ofono.active_calls, self.call_history, self.ignored_calls)

    def _on_audio_changed(self):
        """Render the daemon's applied audio state."""
        audio = self.ofono.audio
        self.is_muted = audio.mic_muted
        self.current_route = audio.current_route
        self.current_input_route = audio.current_input
        self.is_speaker = audio.current_route == "speaker"
        self._toggle_blue(self.btn_mute, self.is_muted)
        self._show_output_route(self.current_route)
        self._show_input_route(self.current_input_route)
        self._refresh_pills()
        self._proximity_tick()

    def on_mute_toggle(self, btn):
        """Toggle microphone mute; the daemon's broadcast renders it."""
        self.ofono.daemon.set_mic_muted(not self.is_muted)
        self.lock_manager.sync_notifications(self.ofono.active_calls, self.call_history, self.ignored_calls)

    def on_hold_toggle(self, btn):
        """Toggle call hold."""
        self.ofono.swap_calls()

    def on_pad_toggle(self, btn):
        """Toggle the DTMF pad, morphing it over the audio selector rows."""
        self.dtmf_visible = not self.dtmf_visible
        self.pad_route_stack.set_visible_child_name("pad" if self.dtmf_visible else "routes")
        self._toggle_blue(btn, self.dtmf_visible)

    def on_reject_with_msg(self, btn):
        """Reject call and send a quick response SMS."""
        def do_reject(msg):
            call = self.ofono.active_calls.get(self.active_path)
            if not call:
                return
            self._start_closing_sequence()
            self.set_visible(False)
            self.ofono.send_quick_response(call['number'], msg)
            self.ofono.hangup_call(self.active_path)

        self._pick_quick_response(btn, do_reject)

    def _toggle_blue(self, btn, active):
        """Helper to toggle active button state style."""
        if active:
            btn.add_css_class("blue-active")
        else:
            btn.remove_css_class("blue-active")

    def _update_timer(self):
        """Update call duration timer."""
        if not self.is_visible():
            return True
        if self.active_path:
            d = self.ofono.active_calls.get(self.active_path)
            if d and d['state'] == 'active':
                diff = int(time.time() - d.get('start', time.time()))
                self.lbl_status.set_text(f"{diff // 60:02}:{diff % 60:02}")

        if not self.is_closing and not self.in_error_mode:
            self._maybe_repeat_knock()

        return True

    def _on_lock_changed(self, monitor, is_locked):
        self.is_locked = is_locked
        self.update_state()
