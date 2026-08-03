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

from ...constants import SHEET_CONTENT_WIDTH

STK_NOTIFICATION_KEY = "stk-request"
PROMPT_WINDOW_HEIGHT = 560


class StkPrompter:
    """Presents SIM Toolkit requests on whatever surface is available.

    Prompts attach to a visible app window as a dialog; with every
    window closed the daemon presents a small window of its own, and
    on a locked phone a notification announces the request and the
    prompt appears at unlock. Every surface answers the pending agent
    request exactly once, also when it is dismissed.
    """

    def __init__(self, app, stk, sys_state, notification_manager):
        """Wire the prompter to the toolkit manager and system state."""
        self.app = app
        self.stk = stk
        self.sys_state = sys_state
        self.notifications = notification_manager
        self._dialog = None
        self._own_window = None
        self._answered = False
        self._deferred = None
        self._generation = 0
        stk.connect('agent-request', self._on_request)
        stk.connect('request-cancelled', self._on_cancelled)
        sys_state.connect('lock-state-changed', self._on_lock_changed)

    def _on_request(self, _stk, kind, payload):
        """Route a new agent request to a surface or defer it while locked."""
        self.stk.mark_handled()
        if kind == "PlayTone":
            logger.debug("[StkPrompter] PlayTone acknowledged without audio")
            self.stk.reply()
            return
        if self.sys_state.is_locked:
            self._deferred = (kind, payload)
            self.notifications.send_notification(
                STK_NOTIFICATION_KEY, _("SIM Services"),
                _("A SIM request is waiting. Unlock to continue."))
            return
        self._present(kind, payload)

    def _on_lock_changed(self, _svc, locked):
        """Present a deferred request once the phone is unlocked."""
        if locked or self._deferred is None:
            return
        kind, payload = self._deferred
        self._deferred = None
        self.notifications.close_notification(STK_NOTIFICATION_KEY)
        self._present(kind, payload)

    def _on_cancelled(self, _stk):
        """Tear down the current surface after the SIM cancelled."""
        self._deferred = None
        self.notifications.close_notification(STK_NOTIFICATION_KEY)
        self._answered = True
        self._close_surface()

    def _close_surface(self):
        """Close whichever surface is currently presented."""
        if self._dialog:
            dialog, self._dialog = self._dialog, None
            dialog.force_close()
        if self._own_window:
            window, self._own_window = self._own_window, None
            window.destroy()

    def _host_window(self):
        """Return a visible application window to attach to, if any."""
        for window in self.app.get_windows():
            if window.get_visible() and window.get_mapped():
                return window
        return None

    def _present(self, kind, payload):
        """Build and present the surface for one request.

        Each surface carries the generation it was built for, because a
        closing surface reports its dismissal asynchronously and must
        never answer the request that replaced it.
        """
        self._generation += 1
        generation = self._generation
        self._close_surface()
        self._answered = False
        try:
            content, title = self._build_content(kind, payload)
        except Exception as e:
            logger.error(f"[StkPrompter] Could not build the {kind} prompt: {e}")
            self._answered = True
            self.stk.end_session()
            return

        host = self._host_window()
        if host:
            dialog = Adw.Dialog(title=title)
            dialog.set_content_width(SHEET_CONTENT_WIDTH)
            dialog.set_child(content)
            dialog.connect("closed", self._on_surface_closed, kind, generation)
            self._dialog = dialog
            dialog.present(host)
        else:
            window = Adw.Window(title=title)
            window.set_default_size(SHEET_CONTENT_WIDTH, PROMPT_WINDOW_HEIGHT)
            window.set_content(content)
            window.connect("close-request", self._on_window_close_request, kind, generation)
            self._own_window = window
            window.present()

    def _on_surface_closed(self, _dialog, kind, generation):
        """Answer a dismissed dialog if no button already did."""
        if generation == self._generation:
            self._dialog = None
        self._finish_dismissal(kind, generation)

    def _on_window_close_request(self, _window, kind, generation):
        """Answer a dismissed prompt window if no button already did."""
        if generation == self._generation:
            self._own_window = None
        self._finish_dismissal(kind, generation)
        return False

    def _finish_dismissal(self, kind, generation):
        """Resolve an unanswered request as the spec expects on dismissal."""
        if self._answered or generation != self._generation:
            return
        self._answered = True
        if kind == "DisplayText" or kind == "DisplayActionInformation":
            self.stk.reply()
        elif kind in ("RequestConfirmation", "ConfirmCallSetup",
                      "ConfirmOpenChannel", "ConfirmLaunchBrowser"):
            self.stk.reply(False)
        else:
            self.stk.end_session()

    def _answer(self, value=None, error=None):
        """Answer the pending request once and close the surface."""
        if self._answered:
            return
        self._answered = True
        if error == "back":
            self.stk.go_back()
        elif error == "end":
            self.stk.end_session()
        else:
            self.stk.reply(value)
        self._close_surface()

    def _build_content(self, kind, payload):
        """Build the widget tree and title for one request kind."""
        if kind == "RequestSelection":
            return self._build_selection(payload), payload["title"] or _("SIM Services")
        if kind == "DisplayText":
            return self._build_text(payload), _("SIM Services")
        if kind in ("RequestInput", "RequestDigits"):
            return self._build_input(payload), _("SIM Services")
        if kind in ("RequestKey", "RequestDigit", "RequestQuickDigit"):
            return self._build_key(payload), _("SIM Services")
        if kind in ("RequestConfirmation", "ConfirmCallSetup",
                    "ConfirmOpenChannel", "ConfirmLaunchBrowser"):
            return self._build_confirmation(kind, payload), _("SIM Services")
        return self._build_progress(kind, payload), _("SIM Services")

    def _toolbar(self, content, back=False):
        """Wrap content in a toolbar view with the standard header."""
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        if back:
            btn_back = Gtk.Button(icon_name="go-previous-symbolic")
            btn_back.connect("clicked", lambda b: GLib.idle_add(
                lambda: self._answer(error="back") or False))
            header.pack_start(btn_back)
        view.add_top_bar(header)
        view.set_content(content)
        return view

    def _body_box(self):
        """Build the standard prompt body container."""
        return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_top=12, margin_start=20, margin_end=20, margin_bottom=24)

    def _button_row(self, reject_label, on_reject, accept_label, on_accept):
        """Build the standard reject and accept pill pair."""
        row = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=8)
        btn_reject = Gtk.Button(label=reject_label, css_classes=["pill"])
        btn_reject.connect("clicked", lambda b: GLib.idle_add(lambda: on_reject() or False))
        row.append(btn_reject)
        btn_accept = Gtk.Button(label=accept_label, css_classes=["pill", "suggested-action"])
        btn_accept.connect("clicked", lambda b: GLib.idle_add(lambda: on_accept() or False))
        row.append(btn_accept)
        return row, btn_accept

    def _build_selection(self, payload):
        """Build the menu selection list."""
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        page.add(group)
        for index, label in enumerate(payload["items"]):
            row = Adw.ActionRow(title=label, activatable=True)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda r, i=index: GLib.idle_add(
                lambda: self._answer(i) or False))
            group.add(row)
        return self._toolbar(page, back=True)

    def _build_text(self, payload):
        """Build the display text prompt."""
        box = self._body_box()
        lbl = Gtk.Label(label=payload["text"], wrap=True, xalign=0)
        box.append(lbl)
        row = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=8)
        btn = Gtk.Button(label=_("OK"), css_classes=["pill", "suggested-action"])
        btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._answer() or False))
        row.append(btn)
        box.append(row)
        return self._toolbar(box)

    def _build_input(self, payload):
        """Build the text or digit input prompt."""
        box = self._body_box()
        if payload["title"]:
            box.append(Gtk.Label(label=payload["title"], wrap=True, xalign=0,
                                 css_classes=["title-4"]))
        group = Adw.PreferencesGroup()
        if payload["hidden"]:
            entry = Adw.PasswordEntryRow(title=_("Response"))
        else:
            entry = Adw.EntryRow(title=_("Response"))
        entry.set_text(payload["default"] or "")
        if payload["digits"]:
            entry.set_input_purpose(Gtk.InputPurpose.PHONE)
        group.add(entry)
        box.append(group)

        hint = Gtk.Label(css_classes=["caption", "dim-label"], xalign=0)
        hint.set_text(_("{minimum}–{maximum} characters").format(
            minimum=payload["min"], maximum=payload["max"]))
        box.append(hint)

        row, btn_ok = self._button_row(_("Cancel"), lambda: self._answer(error="back"),
                                       _("OK"), lambda: self._answer(entry.get_text()))
        box.append(row)

        def sync_ok(*_args):
            length = len(entry.get_text())
            btn_ok.set_sensitive(payload["min"] <= length <= payload["max"])
        entry.connect("changed", sync_ok)
        sync_ok()
        return self._toolbar(box)

    def _build_key(self, payload):
        """Build the single key prompt, quick variants answer on the keypress."""
        box = self._body_box()
        if payload["title"]:
            box.append(Gtk.Label(label=payload["title"], wrap=True, xalign=0,
                                 css_classes=["title-4"]))
        group = Adw.PreferencesGroup()
        entry = Adw.EntryRow(title=_("Response"))
        if payload["digits"]:
            entry.set_input_purpose(Gtk.InputPurpose.PHONE)
        group.add(entry)
        box.append(group)

        if payload["quick"]:
            entry.connect("changed", lambda e: GLib.idle_add(
                lambda: (self._answer(e.get_text()[:1]) if e.get_text() else None) or False))
        else:
            row, btn_ok = self._button_row(_("Cancel"), lambda: self._answer(error="back"),
                                           _("OK"), lambda: self._answer(entry.get_text()[:1]))
            box.append(row)

            def sync_ok(*_args):
                btn_ok.set_sensitive(len(entry.get_text()) >= 1)
            entry.connect("changed", sync_ok)
            sync_ok()
        return self._toolbar(box)

    def _build_confirmation(self, kind, payload):
        """Build the confirmation prompt."""
        box = self._body_box()
        box.append(Gtk.Label(label=payload["text"], wrap=True,
                             justify=Gtk.Justification.CENTER, halign=Gtk.Align.CENTER))
        if kind == "ConfirmLaunchBrowser":
            box.append(Gtk.Label(label=payload["url"], wrap=True, halign=Gtk.Align.CENTER,
                                 css_classes=["caption", "dim-label"]))
        box.append(Gtk.Label(label=_("Confirm only if you started this yourself."),
                             css_classes=["caption", "dim-label"], halign=Gtk.Align.CENTER))

        def accept():
            if kind == "ConfirmLaunchBrowser":
                launcher = Gtk.UriLauncher(uri=payload["url"])
                launcher.launch(None, None, None)
            self._answer(True)

        row, _btn = self._button_row(_("Reject"), lambda: self._answer(False),
                                     _("Confirm"), accept)
        box.append(row)
        return self._toolbar(box)

    def _build_progress(self, kind, payload):
        """Build the ongoing action display for tones and action texts."""
        box = self._body_box()
        box.set_valign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner(width_request=32, height_request=32, halign=Gtk.Align.CENTER)
        spinner.start()
        box.append(spinner)
        text = payload.get("text") or _("SIM Services")
        box.append(Gtk.Label(label=text, wrap=True, halign=Gtk.Align.CENTER))
        row = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=8)
        btn = Gtk.Button(label=_("Dismiss"), css_classes=["pill"])
        btn.connect("clicked", lambda b: GLib.idle_add(
            lambda: self._answer(error=None if kind == "DisplayActionInformation" else "end") or False))
        row.append(btn)
        box.append(row)
        return self._toolbar(box)
