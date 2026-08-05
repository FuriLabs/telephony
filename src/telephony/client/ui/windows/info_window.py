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

from gi.repository import Adw, Gtk
from gettext import gettext as _

APP_VERSION = "1.00"
WEBSITE_URL = "https://github.com/FuriLabs/telephony"
ISSUE_URL = "https://github.com/FuriLabs/telephony/issues"


class InfoPage:
    """Shows the standard about dialog for Telephony."""

    @staticmethod
    def show(parent_window):
        """Create and present the about dialog."""
        dialog = Adw.AboutDialog(
            application_name="Telephony",
            application_icon="io.furios.Telephony",
            developer_name="alaraajavamma",
            version=APP_VERSION,
            license_type=Gtk.License.GPL_3_0,
            website=WEBSITE_URL,
            issue_url=ISSUE_URL,
            comments=_("Fast phone dialer and messaging client designed for FuriOS."),
        )
        dialog.present(parent_window)
