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

from gi.repository import Gtk, GLib, Adw
import time
import os
import urllib.parse

from loguru import logger
from ...backend.services.system_state_service import SystemStateService
from gettext import gettext as _

from ...backend.managers.audio_manager import TelephonyAudioManager
from ..windows.fader_window import ProximityFader
from ..widgets.incall_elements_widget import DynamicHangupButton, create_truncated_label
from ...backend.managers.lockscreen_manager import LockScreenManager
from ...backend.utils.system_utils import restart_ril_modem
from ...backend.utils.phone_utils import normalize_number

KEYPAD_LAYOUT = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#']


class InCallWindow(Gtk.Window):
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
        self.audio = TelephonyAudioManager()
        self.fader = ProximityFader()

        self.lock_manager = LockScreenManager(self.ofono, self.eds, self.audio, self)

        self.active_path = None
        self.is_speaker = False
        self.is_muted = False
        self.dtmf_visible = False
        self.call_history = {}
        self.ignored_calls = set()

        self.is_ringing = False
        self.is_closing = False
        self.in_error_mode = False
        self.manual_hangup = False

        self.is_locked = False

        self._timer_id = None
        self._proximity_timer_id = None

        self._setup_ui()

        def _on_close_req(w):
            self.set_visible(False)
            return True

        self.connect('close-request', _on_close_req)

        self.ofono.connect('call-removed', self.on_call_removed)
        self.ofono.connect('call-added', lambda *a: self.update_state())
        self.ofono.connect('call-changed', lambda *a: self.update_state())

        self.sys_state = SystemStateService()
        self.is_locked = self.sys_state.is_locked
        self.sys_state.connect("lock-state-changed", self._on_lock_changed)

        self.update_state()

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
        self.set_child(self.main_box)

        self.bg_scrolled = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=220)
        self.bg_calls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=15, margin_bottom=15, margin_start=15, margin_end=15)
        self.bg_scrolled.set_child(self.bg_calls_box)
        self.main_box.append(self.bg_scrolled)

        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, vexpand=True, valign=Gtk.Align.CENTER)
        self.lbl_name = create_truncated_label(_("Unknown"), ["title-1"], max_chars=18)
        self.lbl_number = Gtk.Label(css_classes=["title-2", "dim-label"])
        self.lbl_status = Gtk.Label(css_classes=["body", "accent"])
        for lbl in [self.lbl_name, self.lbl_number, self.lbl_status]:
            self.info_box.append(lbl)
        self.main_box.append(self.info_box)

        self.pad_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        pad_grid = Gtk.Grid(row_spacing=8, column_spacing=15, halign=Gtk.Align.CENTER)
        for i, c in enumerate(KEYPAD_LAYOUT):
            b = Gtk.Button(label=c, css_classes=["pill", "dialpad-btn"], width_request=60, height_request=50)
            b.connect("clicked", lambda x, ch=c: GLib.idle_add(lambda: self.ofono.send_dtmf(ch) or False))
            pad_grid.attach(b, i % 3, i // 3, 1, 1)
        self.pad_revealer.set_child(pad_grid)
        self.main_box.append(self.pad_revealer)

        self.controls_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.main_box.append(self.controls_stack)

        inc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, valign=Gtk.Align.END, margin_bottom=40)

        self.btn_search_unknown = Gtk.Button(label=_("Search Number"), css_classes=["pill"], halign=Gtk.Align.CENTER)
        self.btn_search_unknown.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_search_unknown_click(b) or False))
        self.btn_search_unknown.set_visible(False)
        inc_box.append(self.btn_search_unknown)

        btn_msg = Gtk.Button(label=_("Hangup and Send SMS"), css_classes=["pill"], halign=Gtk.Align.CENTER)
        btn_msg.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_reject_with_msg(b) or False))
        inc_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20, halign=Gtk.Align.CENTER)
        inc_row.append(self._mk_btn("audio-volume-muted-symbolic", self.on_silent_click))
        inc_row.append(self._mk_btn("call-start-symbolic", self.on_answer_click, "btn-green"))
        inc_row.append(self._mk_btn("call-stop-symbolic", self.on_hangup_click, "destructive-action"))
        inc_box.append(btn_msg)
        inc_box.append(inc_row)
        self.controls_stack.add_named(inc_box, "incoming")

        act_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, valign=Gtk.Align.END, margin_bottom=40)
        grid = Gtk.Grid(row_spacing=15, column_spacing=25, halign=Gtk.Align.CENTER)
        self.btn_output = self._mk_btn("audio-headphones-symbolic", self.on_output_routing_click)
        self.btn_input = self._mk_btn("audio-input-microphone-symbolic", self.on_input_routing_click)

        self._setup_audio_routing_popover()

        self.btn_pad = self._mk_btn("input-dialpad-symbolic", self.on_pad_toggle)
        self.btn_hold = self._mk_btn("media-playback-pause-symbolic", self.on_hold_toggle)

        grid.attach(self.btn_output, 0, 0, 1, 1)
        grid.attach(self.btn_input, 1, 0, 1, 1)
        grid.attach(self.btn_pad, 0, 1, 1, 1)
        grid.attach(self.btn_hold, 1, 1, 1, 1)

        self.btn_hangup_act = DynamicHangupButton()
        self.btn_hangup_act.set_halign(Gtk.Align.CENTER)
        self.btn_hangup_act.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_hangup_click(b) or False))
        act_box.append(grid)
        act_box.append(self.btn_hangup_act)
        self.controls_stack.add_named(act_box, "active")

        self.err_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.err_box.add_css_class("error-box")
        lbl_err = Gtk.Label(label=_("Modem Stuck"), css_classes=["error-title"])
        lbl_err_msg = Gtk.Label(label=_("The call failed to disconnect."), css_classes=["body"])
        self.btn_restart = Gtk.Button(label=_("🔴 Reboot Modem"))
        self.btn_restart.add_css_class("destructive-action")
        self.btn_restart.add_css_class("pill")
        self.btn_restart.set_size_request(240, 60)
        self.btn_restart.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_restart_modem_click(b) or False))
        self.err_box.append(lbl_err)
        self.err_box.append(lbl_err_msg)
        self.err_box.append(self.btn_restart)
        self.controls_stack.add_named(self.err_box, "error")

    def _mk_btn(self, icon, cb, cls=None):
        """Helper to create a circular icon button."""
        b = Gtk.Button(icon_name=icon, css_classes=["circular"], width_request=70, height_request=70)
        if cls:
            b.add_css_class(cls)
        b.connect("clicked", lambda btn: GLib.idle_add(lambda: cb(btn) or False))
        return b

    def _setup_audio_routing_popover(self):
        """Setup the audio routing popover menu."""
        self.output_popover = Gtk.Popover()
        self.output_popover.set_position(Gtk.PositionType.TOP)
        self.output_popover.set_parent(self.btn_output)

        self.out_pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.out_pop_box.set_margin_top(12)
        self.out_pop_box.set_margin_bottom(12)
        self.out_pop_box.set_margin_start(12)
        self.out_pop_box.set_margin_end(12)
        self.out_pop_box.set_size_request(280, -1)
        self.output_popover.set_child(self.out_pop_box)

        self.output_group = Adw.PreferencesGroup(title=_("Output"))
        self.out_pop_box.append(self.output_group)

        self.input_popover = Gtk.Popover()
        self.input_popover.set_position(Gtk.PositionType.TOP)
        self.input_popover.set_parent(self.btn_input)

        self.in_pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.in_pop_box.set_margin_top(12)
        self.in_pop_box.set_margin_bottom(12)
        self.in_pop_box.set_margin_start(12)
        self.in_pop_box.set_margin_end(12)
        self.in_pop_box.set_size_request(280, -1)
        self.input_popover.set_child(self.in_pop_box)

        self.input_group = Adw.PreferencesGroup(title=_("Input"))
        self.in_pop_box.append(self.input_group)

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

    def update_state(self):
        """Refresh call state and UI."""
        self.lock_manager.set_locked(self.is_locked)

        if self.in_error_mode:
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

        if not calls:
            if self.active_path is not None or self.is_ringing or self.is_muted or self.is_speaker:
                self._clean_reset()
            if self.is_visible():
                self.set_visible(False)
            return

        self._start_timers()

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
                    c_unknown = self.call_history[path]['name'] == _("Unknown")
                    if uc_action in ["hide", "silence"] and c_unknown and not override_volume:
                        is_silenced = True

                    if not self.call_history[path].get('knocked', False) and not is_silenced:
                        self.audio.play_knock()
                        self.call_history[path]['knocked'] = True

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

            if not self.manual_hangup and len(calls) == 1:
                if override_volume:
                    logger.info(f"[Priority] Call from {p_data['number']} - forcing MAX volume")
                    from gi.repository import GLib
                    self.audio.force_max_feedback()
                    GLib.timeout_add_seconds(1, lambda: self.audio.force_max_feedback() or False)
                    GLib.timeout_add_seconds(5, lambda: self.audio.force_max_feedback(restore=True) or False)

                if not self.is_ringing and not is_silenced:
                    custom_ring = self._get_custom_ringtone(p_data['number'])
                    self.audio.start_ringing(custom_path=custom_ring)
                    self.is_ringing = True
                elif is_silenced and self.is_ringing:
                    self.audio.stop_ringing()
                    self.is_ringing = False

            self.controls_stack.set_visible_child_name("incoming")
            self.lbl_status.set_text(_("Incoming Call...") if not is_silenced else _("Silenced Incoming Call"))
        else:
            if self.is_ringing:
                self.audio.stop_ringing()
                self.is_ringing = False
            self.audio.set_voice_profile(True)
            self.audio.mute(self.is_muted)
            self.controls_stack.set_visible_child_name("active")
            self._toggle_blue(self.btn_output, self.is_speaker)
            self._toggle_blue(self.btn_input, self.is_muted)

            can_hold = p_data['state'] in ['active', 'held']
            self.btn_hold.set_sensitive(can_hold)

            self._toggle_blue(self.btn_hold, p_data['state'] == 'held')
            self.lbl_status.set_text(_(p_data['state']).capitalize())

        bg_list = [(x[1], x[2]) for x in sorted_c[1:]]
        self._render_bg(bg_list)

    def _get_custom_ringtone(self, number):
        """Check for a custom ringtone for the caller."""
        tones = self.gsettings_mgr.get_notification_override_call_custom_contacts()
        norm = normalize_number(number)

        for t in tones:
            if normalize_number(t.get("number", "")) == norm:
                path = t.get("path")
                if path and os.path.exists(path):
                    return path
        return None

    def _clean_reset(self):
        """Reset window state to idle."""
        self._stop_timers()
        self.lock_manager.clear_all()
        self.audio.stop_ringing()
        self.is_ringing = False

        self.audio.set_voice_profile(False)
        self.audio.mute(False)
        self.audio.update_hardware_state(False)

        self.active_path = None
        self.is_speaker = False
        self.is_muted = False
        self.dtmf_visible = False
        self.manual_hangup = False
        self.ignored_calls.clear()
        self.fader.set_active(False)

        self._toggle_blue(self.btn_output, False)
        self._toggle_blue(self.btn_input, False)
        self._toggle_blue(self.btn_pad, False)
        self.pad_revealer.set_reveal_child(False)

        self.call_history.clear()
        if self.is_visible():
            self.set_visible(False)

    def _render_bg(self, bg_list):
        """Render background/held calls list."""
        while c := self.bg_calls_box.get_first_child():
            self.bg_calls_box.remove(c)
        for path, data in bg_list:
            if path in self.ignored_calls:
                continue
            name = self.eds.get_contact_name(data['number'])
            if not name or name == "Unknown":
                name = _("Unknown")

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["card"])
            lbl_title = create_truncated_label(f"{name} ({_(data['state'])})", ["heading"], max_chars=22)
            lbl_title.set_halign(Gtk.Align.START)
            card.append(lbl_title)

            btn_box = Gtk.Box(spacing=15, halign=Gtk.Align.END)
            b_hide = Gtk.Button(icon_name="call-stop-symbolic", css_classes=["circular", "destructive-action", "bg-btn"])
            b_hide.set_size_request(50, 50)
            b_hide.connect("clicked", lambda b, p=path: GLib.idle_add(lambda: self.on_ignore_call(p) or False))
            btn_box.append(b_hide)

            b_sms = Gtk.Button(icon_name="mail-message-new-symbolic", css_classes=["circular", "bg-btn"])
            b_sms.set_size_request(50, 50)
            b_sms.connect("clicked", lambda b, p=path, n=data['number']: GLib.idle_add(lambda: self.on_ignore_with_sms(p, n) or False))
            btn_box.append(b_sms)

            b_act = Gtk.Button(css_classes=["circular", "btn-green", "bg-btn"])
            b_act.set_size_request(50, 50)
            if data['state'] == 'held':
                b_act.set_icon_name("call-start-symbolic")
                b_act.connect("clicked", lambda b: GLib.idle_add(lambda: self.ofono.swap_calls() or False))
            elif data['state'] in ['incoming', 'waiting']:
                b_act.set_icon_name("call-start-symbolic")
                b_act.connect("clicked", lambda b, p=path: GLib.idle_add(lambda: self.ofono.answer_call(p) or False))
            btn_box.append(b_act)
            card.append(btn_box)
            self.bg_calls_box.append(card)

    def on_ignore_call(self, path):
        """Ignore a background call."""
        self.ignored_calls.add(path)
        if self.is_ringing:
            self.audio.stop_ringing()
            self.is_ringing = False
        self.update_state()

    def on_ignore_with_sms(self, path, number):
        """Ignore a call and send a quick response."""
        msg = self.gsettings_mgr.get_setting("reject_call_message") or _("I can't talk right now.")
        self.ofono.send_sms(number, msg)
        self.ignored_calls.add(path)
        if self.is_ringing:
            self.audio.stop_ringing()
            self.is_ringing = False
        self.update_state()

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

        if engine == "startpage":
            search_url = f"https://www.startpage.com/do/dsearch?query={encoded_num}"
        elif engine == "duckduckgo":
            search_url = f"https://duckduckgo.com/?q={encoded_num}"
        elif engine == "custom" and custom_url:
            search_url = custom_url.replace("{number}", encoded_num)

        if search_url:
            from gi.repository import Gio
            Gio.AppInfo.launch_default_for_uri(search_url, None)

    def on_silent_click(self, btn):
        """Silences the ringer and hides the window."""
        self.audio.stop_ringing()
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
        if len(self.ofono.active_calls) > 1:
            self.ofono.hangup_all()
        elif self.active_path:
            self.ofono.hangup_call(self.active_path)
        else:
            self.ofono.hangup_all()

    def _start_closing_sequence(self):
        """Start the call closing animation/logic."""
        self.is_closing = True
        self.audio.stop_ringing()
        GLib.timeout_add(3500, self._verify_hangup_success)

    def _verify_hangup_success(self):
        """Verify calls are actually gone, otherwise trigger error state."""
        if not self.ofono.active_calls:
            self.is_closing = False
            self._clean_reset()
            return False
        self._enter_error_state()
        return False

    def _enter_error_state(self):
        """Show error UI when hangup fails."""
        self.present()
        self.in_error_mode = True
        self.is_closing = False
        self.audio.play_hangup()
        self.fader.set_active(False)
        self.audio.update_hardware_state(False)
        self.audio.set_voice_profile(False)
        self.is_speaker = False
        self.controls_stack.set_visible_child_name("error")
        self.lock_manager.show_stuck_notification()

    def on_restart_modem_click(self, btn):
        """Force restart the RIL."""
        self.btn_restart.set_sensitive(False)
        self.btn_restart.set_label(_("Restarting RIL..."))
        try:
            restart_ril_modem()
        except Exception as e:
            logger.error(f"RIL Restart Failed: {e}")
        self.restart_attempts = 0
        GLib.timeout_add(2000, self._check_restart_success)

    def _check_restart_success(self):
        """Poll for RIL restart success."""
        self.ofono._sync_existing_calls()
        if not self.ofono.active_calls:
            self.btn_restart.set_label(_("Restored!"))
            self.audio.play_hangup()
            GLib.timeout_add(1000, self._reset_from_error)
            return False
        self.restart_attempts += 1
        if self.restart_attempts > 15:
            self.btn_restart.set_label(_("Failed. Reboot Device."))
            return False
        self.btn_restart.set_label(_("Verifying... {attempt}").format(attempt=self.restart_attempts))
        return True

    def _reset_from_error(self, *args):
        """Reset UI after error recovery."""
        self.in_error_mode = False
        self.btn_restart.set_sensitive(True)
        self.btn_restart.set_label(_("🔴 Reboot Modem"))
        self._clean_reset()
        return False

    def on_call_removed(self, m, p):
        """Handle call removed signal."""
        if self.is_closing or self.in_error_mode:
            return
        self.audio.play_hangup()
        self.fader.set_active(False)
        if p in self.ignored_calls:
            self.ignored_calls.remove(p)
        self.update_state()

    def on_output_routing_click(self, btn):
        """Populate and show the output routing popover."""
        self.output_popover.popup()

        self.out_pop_box.remove(self.output_group)
        self.output_group = Adw.PreferencesGroup(title=_("Output"))
        self.out_pop_box.append(self.output_group)

        out_routes = self.audio.get_available_outputs()

        for r in out_routes:
            row = Adw.ActionRow(title=r['name'])
            icon = Gtk.Image.new_from_icon_name(r['icon'])
            row.add_prefix(icon)

            route_id = r['id']
            if route_id == ("speaker" if self.is_speaker else "earpiece"):
                img_check = Gtk.Image.new_from_icon_name("object-select-symbolic")
                row.add_suffix(img_check)

            row.set_activatable(True)

            def _cb_out(row_widget, r_id=route_id):
                GLib.idle_add(lambda: self.output_popover.popdown() or False)
                self._handle_output_selection(r_id)
            row.connect("activated", _cb_out)
            self.output_group.add(row)

    def on_input_routing_click(self, btn):
        """Populate and show the input routing popover."""
        self.input_popover.popup()

        self.in_pop_box.remove(self.input_group)
        self.input_group = Adw.PreferencesGroup(title=_("Input"))
        self.in_pop_box.append(self.input_group)

        in_routes = self.audio.get_available_inputs()

        mute_row = Adw.ActionRow(title=_("Mute Microphone"))
        mute_icon = Gtk.Image.new_from_icon_name("microphone-sensitivity-muted-symbolic")
        mute_row.add_prefix(mute_icon)
        if self.is_muted:
            mute_check = Gtk.Image.new_from_icon_name("object-select-symbolic")
            mute_row.add_suffix(mute_check)
        mute_row.set_activatable(True)

        def _cb_mute(row_widget):
            GLib.idle_add(lambda: self.input_popover.popdown() or False)
            self.on_mute_toggle(None)
        mute_row.connect("activated", _cb_mute)
        self.input_group.add(mute_row)

        for r in in_routes:
            row = Adw.ActionRow(title=r['name'])
            icon = Gtk.Image.new_from_icon_name(r['icon'])
            row.add_prefix(icon)

            route_id = r['id']
            if route_id == "mic" and not self.is_muted:
                img_check = Gtk.Image.new_from_icon_name("object-select-symbolic")
                row.add_suffix(img_check)

            row.set_activatable(True)

            def _cb_in(row_widget, r_id=route_id):
                GLib.idle_add(lambda: self.input_popover.popdown() or False)
                if self.is_muted:
                    self.on_mute_toggle(None)
                self.audio.set_input_route(r_id)
            row.connect("activated", _cb_in)
            self.input_group.add(row)

    def _handle_output_selection(self, route_id):
        """Handle output route selection."""
        self.audio.set_audio_route(route_id)
        if route_id == "speaker":
            self.is_speaker = True
            self.btn_output.set_icon_name("audio-speakers-symbolic")
            self._toggle_blue(self.btn_output, True)
        elif route_id == "earpiece":
            self.is_speaker = False
            self.btn_output.set_icon_name("audio-headphones-symbolic")
            self._toggle_blue(self.btn_output, False)
        else:
            self.is_speaker = False
            self.btn_output.set_icon_name("audio-headphones-symbolic")
            self._toggle_blue(self.btn_output, True)

        self._proximity_tick()
        self.lock_manager.sync_notifications(self.ofono.active_calls, self.call_history, self.ignored_calls)

    def on_mute_toggle(self, btn):
        """Toggle microphone mute."""
        self.is_muted = not self.is_muted
        self.audio.mute(self.is_muted)
        self.btn_input.set_icon_name("microphone-sensitivity-muted-symbolic" if self.is_muted else "audio-input-microphone-symbolic")
        self._toggle_blue(self.btn_input, self.is_muted)
        self.lock_manager.sync_notifications(self.ofono.active_calls, self.call_history, self.ignored_calls)

    def on_speaker_toggle(self, btn):
        """Toggle speaker output."""
        if self.is_speaker:
            self._handle_output_selection("earpiece")
        else:
            self._handle_output_selection("speaker")

    def on_hold_toggle(self, btn):
        """Toggle call hold."""
        self.ofono.swap_calls()

    def on_pad_toggle(self, btn):
        """Toggle dialpad visibility."""
        self.dtmf_visible = not self.dtmf_visible
        self.pad_revealer.set_reveal_child(self.dtmf_visible)
        self._toggle_blue(btn, self.dtmf_visible)

    def on_reject_with_msg(self, btn):
        """Reject call and send SMS."""
        self._start_closing_sequence()
        self.set_visible(False)
        msg = self.gsettings_mgr.get_setting("reject_call_message") or _("I can't talk right now.")
        self.ofono.send_sms(self.ofono.active_calls[self.active_path]['number'], msg)
        self.ofono.hangup_call(self.active_path)

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
            calls = self.ofono.active_calls
            should_knock = False
            for p, d in calls.items():
                if d['state'] in ['waiting', 'incoming'] and p != self.active_path:
                    if p not in self.ignored_calls:
                        should_knock = True
                        break
            if should_knock:
                self.audio.play_knock()

        return True

    def _on_lock_changed(self, monitor, is_locked):
        self.is_locked = is_locked
        self.update_state()
