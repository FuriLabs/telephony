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

from datetime import datetime

from loguru import logger

STANDARD_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_timestamp(dt=None):
    """Format a datetime object as a standard string. Defaults to now."""
    if dt is None:
        dt = datetime.now()
    return dt.strftime(STANDARD_FORMAT)


def parse_timestamp(ts_str):
    try:
        return datetime(int(ts_str[0:4]), int(ts_str[5:7]), int(ts_str[8:10]), int(ts_str[11:13]), int(ts_str[14:16]), int(ts_str[17:19]))
    except Exception:
        try:
            return datetime.strptime(ts_str, STANDARD_FORMAT)
        except Exception as e:
            logger.debug(f"[Utils] Unparseable timestamp {ts_str!r}, using now: {e}")
            return datetime.now()
