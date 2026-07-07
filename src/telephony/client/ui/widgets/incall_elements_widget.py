# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Gtk, Pango
from gettext import gettext as _


def create_truncated_label(text, css_classes=[], max_chars=20):
    lbl = Gtk.Label(label=text)
    for c in css_classes:
        lbl.add_css_class(c)
    lbl.set_ellipsize(Pango.EllipsizeMode.END)
    lbl.set_max_width_chars(max_chars)
    return lbl


class DynamicHangupButton(Gtk.Button):

    def __init__(self):
        super().__init__()
        self.add_css_class("destructive-action")
        self.add_css_class("pill")
        self.set_size_request(260, 60)
        self.update_mode(1)

    def update_mode(self, count):
        label = _("Hangup All Calls") if count > 1 else _("Hang Up")
        self.set_child(Gtk.Label(label=label, css_classes=["title-4"]))
