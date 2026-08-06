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

import locale

from telephony.shared.utils.log_utils import logger


def init_locale():
    try:
        locale.setlocale(locale.LC_ALL, '')
        logger.info(f"[Utils] Initialized Locale: {locale.getlocale()}")
    except Exception as e:
        logger.warning(f"[Utils] Failed to set default locale: {e}")
        try:
            locale.setlocale(locale.LC_ALL, 'C.UTF-8')
        except Exception as e2:
            logger.warning(f"[Utils] Fallback locale failed too: {e2}")


def get_date_format():
    try:
        return locale.nl_langinfo(locale.D_FMT)
    except Exception as e:
        logger.warning(f"[LocaleUtils] get_date_format error: {e}. Falling back to %x")
        return "%x"


def get_time_format():
    return "%H:%M"
