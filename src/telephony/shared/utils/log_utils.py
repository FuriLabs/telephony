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

import logging
import sys

LOG_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(module)s:%(funcName)s:%(lineno)d - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class TelephonyLogger(logging.Logger):
    """The standard library logger wearing the slice of loguru we used.

    Every call site only ever used the level methods plus add and
    remove, and loguru kept about four megabytes resident in every
    process for that. The level methods are inherited untouched, so
    module, function and line in the output stay accurate.
    """

    def add(self, sink=sys.stderr, level="DEBUG"):
        """Attach a stream handler at the given level, loguru style."""
        handler = logging.StreamHandler(sink)
        handler.setLevel(getattr(logging, level, logging.DEBUG))
        handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        self.addHandler(handler)
        return len(self.handlers)

    def remove(self, *_args):
        """Drop every attached handler, loguru style."""
        for handler in list(self.handlers):
            self.removeHandler(handler)


logging.setLoggerClass(TelephonyLogger)
logger = logging.getLogger("telephony")
logger.setLevel(logging.DEBUG)
logging.setLoggerClass(logging.Logger)
