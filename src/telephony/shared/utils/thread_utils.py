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

import threading
from gi.repository import GLib
from telephony.shared.utils.log_utils import logger


def run_in_background(task_func, *args, on_complete=None, on_error=None, **kwargs):
    """
    Executes a task function in a background daemon thread.
    Optionally calls on_complete(result) or on_error(exception) on the GLib main thread.
    """
    def _thread_wrapper():
        try:
            result = task_func(*args, **kwargs)
            if on_complete:
                GLib.idle_add(lambda: on_complete(result) or False)
        except Exception as e:
            logger.error(f"[ThreadUtils] Background task error: {e}")
            if on_error:
                GLib.idle_add(lambda e=e: on_error(e) or False)

    thread = threading.Thread(target=_thread_wrapper, daemon=True)
    thread.start()
    return thread
