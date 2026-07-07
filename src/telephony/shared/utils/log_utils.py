# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import logging
import sys

LOG_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(module)s:%(funcName)s:%(lineno)d - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.FATAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARN,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


class TelephonyLogger(logging.Logger):

    def add(self, sink=sys.stderr, level="DEBUG"):
        handler = logging.StreamHandler(sink)
        handler.setLevel(LOG_LEVELS.get(level.upper(), logging.DEBUG))
        handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        self.addHandler(handler)
        return len(self.handlers)

    def remove(self, *_args):
        for handler in list(self.handlers):
            self.removeHandler(handler)


logging.setLoggerClass(TelephonyLogger)
logger = logging.getLogger("telephony")
logger.setLevel(logging.DEBUG)
logging.setLoggerClass(logging.Logger)
