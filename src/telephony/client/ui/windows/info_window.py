# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

from gi.repository import Adw, Gtk
from gettext import gettext as _
from telephony.client.ui.widgets.common_widget import stay_a_sheet

APP_VERSION = "1.00"
WEBSITE_URL = "https://github.com/FuriLabs/telephony"
ISSUE_URL = "https://github.com/FuriLabs/telephony/issues"


class InfoPage:

    @staticmethod
    def show(parent_window):
        dialog = Adw.AboutDialog(
            application_name="Telephony",
            application_icon="io.furios.Telephony.Calls",
            developer_name="alaraajavamma",
            version=APP_VERSION,
            license_type=Gtk.License.GPL_3_0,
            website=WEBSITE_URL,
            issue_url=ISSUE_URL,
            comments=_("Fast phone dialer and messaging client designed for FuriOS."),
        )
        stay_a_sheet(dialog)
        dialog.present(parent_window)
