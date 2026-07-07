# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Gtk, Pango, GLib
from gettext import gettext as _

from telephony.shared.utils.phone_utils import normalize_number
from telephony.client.ui.widgets.common_widget import blocked_state_text


class HistoryRowFactory:

    def __init__(self, app_window):
        self.app_window = app_window
        self.calling_enabled = True
        self.blocked_entries = {}
        self._live_call_btns = set()

    def reload_blocked_numbers(self):
        db = self.app_window.db
        if not db:
            return
        self.blocked_entries = {normalize_number(entry["number"]): dict(entry)
                                for entry in db.get_blocked_numbers()}

    def setup(self, factory, list_item):
        stack = Gtk.Stack()

        box = Gtk.Box(spacing=10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(3)
        box.set_margin_bottom(3)

        icon = Gtk.Image()
        icon.set_pixel_size(16)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_valign(Gtk.Align.CENTER)

        name = Gtk.Label(xalign=0, css_classes=["body", "emphasized"])
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_hexpand(True)
        name.set_max_width_chars(25)

        num = Gtk.Label(xalign=0, css_classes=["tiny-label", "dim-label"])
        num.set_ellipsize(Pango.EllipsizeMode.END)

        num_box = Gtk.Box(spacing=4)
        num_box.append(num)

        markers = {}
        for key, icon_name, css in (("anon", "view-conceal-symbolic", "dim-label"),
                                    ("conf", "system-users-symbolic", "dim-label"),
                                    ("xfer", "send-to-symbolic", "dim-label"),
                                    ("netfail", "dialog-warning-symbolic", "dim-label"),
                                    ("blocked", "action-unavailable-symbolic", "marker-rejected")):
            mark = Gtk.Image.new_from_icon_name(icon_name)
            mark.set_pixel_size(10)
            mark.add_css_class(css)
            mark.set_visible(False)
            num_box.append(mark)
            markers[key] = mark

        vbox.append(name)
        vbox.append(num_box)

        spacer = Gtk.Box(hexpand=True)

        rbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        rbox.set_valign(Gtk.Align.CENTER)

        time_lbl = Gtk.Label(css_classes=["tiny-label", "dim-label", "monospace"])
        rbox.append(time_lbl)

        call_btn = Gtk.Button(icon_name="call-start-symbolic")
        call_btn.handler_id = None
        call_btn.add_css_class("circular")
        call_btn.add_css_class("call-btn-small")
        call_btn.set_size_request(34, 34)
        call_btn.set_valign(Gtk.Align.CENTER)
        call_btn.set_vexpand(False)
        call_btn.set_sensitive(self.calling_enabled)
        self._live_call_btns.add(call_btn)

        info_btn = Gtk.Button(icon_name="help-about-symbolic")
        info_btn.handler_id = None
        info_btn.add_css_class("circular")
        info_btn.add_css_class("secondary-btn")
        info_btn.set_size_request(34, 34)
        info_btn.set_valign(Gtk.Align.CENTER)

        rbox.append(info_btn)
        rbox.append(call_btn)

        box.append(icon)
        box.append(vbox)
        box.append(spacer)
        box.append(rbox)
        stack.add_named(box, "standard")

        div_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        div_box.set_halign(Gtk.Align.CENTER)
        div_lbl = Gtk.Label(label="—")
        div_lbl.add_css_class("dim-label")
        div_lbl.add_css_class("caption")
        div_box.append(div_lbl)
        stack.add_named(div_box, "divider")

        list_item.set_child(stack)

        list_item.widgets = {
            "stack": stack,
            "icon": icon, "name": name, "num": num, "markers": markers, "time": time_lbl,
            "call_btn": call_btn, "info_btn": info_btn,
            "div_lbl": div_lbl
        }

    def bind(self, factory, list_item):
        item = list_item.get_item()
        if list_item.get_child() is None:
            self.setup(factory, list_item)

        w = list_item.widgets
        stack = w["stack"]

        if item.is_divider:
            stack.set_visible_child_name("divider")
            w["div_lbl"].set_text(item.label or "—")
            return

        stack.set_visible_child_name("standard")

        display_name = item.name if item.name else item.number
        if display_name == item.number and any(c.isdigit() for c in str(item.number)):
            display_name = _("Unknown")

        w["name"].set_text(display_name)
        hidden_number = item.anonymous and not any(c.isdigit() for c in str(item.number))
        w["num"].set_text(_("Hidden number") if hidden_number else item.number)
        w["markers"]["anon"].set_visible(bool(item.anonymous))
        w["markers"]["conf"].set_visible(bool(item.multiparty))
        w["markers"]["xfer"].set_visible(bool(item.transferred))
        w["markers"]["netfail"].set_visible(item.disconnect_reason == "network")

        entry = self.blocked_entries.get(normalize_number(item.number))
        refuses_calls = bool(entry and entry["block_calls"])
        w["markers"]["blocked"].set_visible(refuses_calls)
        if refuses_calls:
            w["markers"]["blocked"].set_tooltip_text(blocked_state_text(entry))
        w["time"].set_text(item.display_time)

        w["icon"].remove_css_class("call-icon-missed")
        w["icon"].remove_css_class("call-icon-incoming")
        w["icon"].remove_css_class("call-icon-outgoing")
        w["icon"].remove_css_class("call-icon-cancelled")

        w["icon"].remove_css_class("call-icon-voicemail")
        w["info_btn"].set_visible(item.direction != "voicemail")

        if item.direction == "voicemail":
            w["icon"].set_from_icon_name("mail-unread-symbolic")
            w["icon"].add_css_class("call-icon-voicemail")
        elif item.direction == "incoming":
            w["icon"].set_from_icon_name("call-incoming-symbolic")
            w["icon"].add_css_class("call-icon-incoming")
        elif item.direction in ("missed", "rejected"):
            w["icon"].set_from_icon_name("call-missed-symbolic")
            w["icon"].add_css_class("call-icon-missed")
        elif item.direction == "outgoing":
            w["icon"].set_from_icon_name("call-outgoing-symbolic")
            w["icon"].add_css_class("call-icon-outgoing")
        elif item.direction == "cancelled":
            w["icon"].set_from_icon_name("call-outgoing-symbolic")
            w["icon"].add_css_class("call-icon-cancelled")
        else:
            w["icon"].set_from_icon_name("call-start-symbolic")

        if (w["call_btn"].handler_id is not None) and w["call_btn"].handler_is_connected(w["call_btn"].handler_id):
            w["call_btn"].disconnect(w["call_btn"].handler_id)
        if (w["info_btn"].handler_id is not None) and w["info_btn"].handler_is_connected(w["info_btn"].handler_id):
            w["info_btn"].disconnect(w["info_btn"].handler_id)

        w["call_btn"].handler_id = w["call_btn"].connect("clicked", lambda b: GLib.idle_add(lambda: self.on_call_click(b, item.number) or False))
        w["info_btn"].handler_id = w["info_btn"].connect("clicked", lambda b: GLib.idle_add(lambda: self.on_info_click(b, item) or False))

    def unbind(self, factory, list_item):
        if (list_item.get_child() is not None):
            w = list_item.widgets
            if (w["call_btn"].handler_id is not None) and w["call_btn"].handler_is_connected(w["call_btn"].handler_id):
                w["call_btn"].disconnect(w["call_btn"].handler_id)
            if (w["info_btn"].handler_id is not None) and w["info_btn"].handler_is_connected(w["info_btn"].handler_id):
                w["info_btn"].disconnect(w["info_btn"].handler_id)

    def teardown(self, factory, list_item):
        if (list_item.get_child() is not None):
            self._live_call_btns.discard(list_item.widgets["call_btn"])
            list_item.widgets = None
        list_item.set_child(None)

    def set_calling_enabled(self, enabled):
        self.calling_enabled = enabled
        for btn in self._live_call_btns:
            btn.set_sensitive(enabled)

    def on_call_click(self, btn, number):
        safe_num = normalize_number(number)
        if safe_num:
            self.app_window.start_call(safe_num)
        else:
            self.app_window.start_call(number)

    def on_info_click(self, btn, item):
        self.app_window.show_call_details(item)
