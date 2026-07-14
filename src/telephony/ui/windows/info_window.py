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

import webbrowser
from gi.repository import Adw, Gtk, GLib
from loguru import logger
from gettext import gettext as _


class InfoPage:
    """Displays the About/Info page for Telephony."""

    @staticmethod
    def show(parent_window):
        """Create and show the Info window."""
        win = Adw.Window(title=_("About Telephony"))
        win.set_transient_for(parent_window)
        win.set_modal(True)
        win.set_default_size(360, 520)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        win.set_content(content_box)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        btn_close = Gtk.Button(label=_("Close"))
        btn_close.connect("clicked", lambda b: GLib.idle_add(lambda: win.close() or False))
        header.pack_end(btn_close)

        content_box.append(header)

        page = Adw.PreferencesPage()
        content_box.append(page)

        header_group = Adw.PreferencesGroup()

        header_row = Adw.ActionRow(
            title="Telephony",
            subtitle=_("Fast phone dialer and messaging client designed for FLX1 and FLX1s.")
        )
        header_row.set_activatable(False)
        header_row.add_prefix(
            Gtk.Image.new_from_icon_name("io.furios.Telephony")
        )

        header_group.add(header_row)
        page.add(header_group)

        info_group = Adw.PreferencesGroup()

        def info_row(title, value):
            row = Adw.ActionRow(title=title)
            row.add_suffix(Gtk.Label(label=value, xalign=1))
            row.set_activatable(False)
            return row

        info_group.add(info_row(_("Version"), "1.00"))
        info_group.add(info_row(_("Developer"), "alaraajavamma"))
        info_group.add(info_row(_("License"), "GPL-3.0"))

        page.add(info_group)

        links_group = Adw.PreferencesGroup(title=_("Links"))

        def link_row(title, subtitle, url):
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            row.set_activatable(False)

            btn = Gtk.Button(icon_name="tab-new-symbolic")
            btn.add_css_class("flat")
            btn.add_css_class("circular")
            btn.set_valign(Gtk.Align.CENTER)

            def on_click(button):
                logger.info(f"Opening: {url}")
                try:
                    webbrowser.open(url)
                except Exception as e:
                    logger.error(f"Error opening link: {e}")

            btn.connect("clicked", lambda b: GLib.idle_add(lambda: on_click(b) or False))
            row.add_suffix(btn)
            return row

        links_group.add(
            link_row(
                _("Website"),
                "github.com/FuriLabs/telephony",
                "https://github.com/FuriLabs/telephony"
            )
        )

        links_group.add(
            link_row(
                _("Issue Tracker"),
                _("Report bugs or request features"),
                "https://github.com/FuriLabs/telephony/issues"
            )
        )

        page.add(links_group)

        win.present()
