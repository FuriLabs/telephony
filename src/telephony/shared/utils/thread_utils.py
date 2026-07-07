# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import threading
from gi.repository import GLib
from telephony.shared.utils.log_utils import logger


def run_in_background(task_func, *args, on_complete=None, on_error=None, **kwargs):
    def thread_wrapper():
        try:
            result = task_func(*args, **kwargs)
            if on_complete:
                GLib.idle_add(lambda: on_complete(result) or False)
        except Exception as e:
            if on_error:
                logger.debug(f"[ThreadUtils] Background task error: {e}")
                GLib.idle_add(lambda e=e: on_error(e) or False)
            else:
                logger.error(f"[ThreadUtils] Background task error: {e}")

    thread = threading.Thread(target=thread_wrapper, daemon=True)
    thread.start()
    return thread
