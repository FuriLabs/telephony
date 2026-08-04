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

import builtins
import gettext
import locale
from telephony.backend.utils.log_utils import logger

LOCALEDIR = "/usr/share/locale"
DOMAIN = "telephony"


def install_i18n():
    """Install i18n support."""
    try:
        try:
            locale.setlocale(locale.LC_ALL, '')
            logger.info(f"Locale set to: {locale.getlocale()}")
        except locale.Error as e:
            logger.warning(f"Failed to set locale: {e}")

        localedir = LOCALEDIR

        logger.info(f"Binding text domain '{DOMAIN}' to '{localedir}'")
        gettext.bindtextdomain(DOMAIN, localedir)
        gettext.textdomain(DOMAIN)

        logger.info("Installing gettext")
        gettext.install(DOMAIN, localedir)

    except Exception as e:
        logger.error(f"Failed to install i18n: {e}")
        builtins._ = lambda x: x
