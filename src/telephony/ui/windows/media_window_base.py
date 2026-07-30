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

import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Adw, Gst, GLib

if not Gst.is_initialized():
    Gst.init(None)

VIEWFINDER_START_DELAY_MS = 200
PLAYBACK_PROGRESS_INTERVAL_MS = 500
EOS_TIMEOUT_MS = 3000
PROGRESS_BAR_WIDTH = 200


def _format_duration(seconds):
    """Format a duration in whole seconds as M:SS."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


class MediaCaptureWindow(Adw.Window):
    """Base window providing shared timeout, bus watch and progress scaffolding."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._closed = False
        self._timeout_ids = set()

    def _schedule_timeout(self, interval_ms, callback):
        """Schedule a tracked GLib timeout that is cancelled when the window closes."""
        holder = {"id": 0}

        def _wrapper():
            """Run the callback unless the window is closed, untracking one-shot sources."""
            if self._closed:
                self._timeout_ids.discard(holder["id"])
                return False
            keep_going = bool(callback())
            if not keep_going:
                self._timeout_ids.discard(holder["id"])
            return keep_going

        holder["id"] = GLib.timeout_add(interval_ms, _wrapper)
        self._timeout_ids.add(holder["id"])
        return holder["id"]

    def _cancel_timeout(self, source_id):
        """Cancel a tracked GLib timeout if it is still pending."""
        if source_id in self._timeout_ids:
            GLib.source_remove(source_id)
            self._timeout_ids.discard(source_id)

    def _cancel_tracked_timeouts(self):
        """Remove every pending GLib timeout owned by this window."""
        for source_id in list(self._timeout_ids):
            GLib.source_remove(source_id)
        self._timeout_ids.clear()

    def _watch_bus(self, element, handler):
        """Attach a tracked signal watch to the element's bus."""
        bus = element.get_bus()
        bus.add_signal_watch()
        handler_id = bus.connect("message", handler)
        return bus, handler_id

    def _recording_timer_text(self, elapsed_seconds):
        """Build the elapsed/limit recording timer text."""
        return f"{_format_duration(elapsed_seconds)} / {_format_duration(self.max_seconds)}"

    def _playback_progress_text(self, position_seconds, duration_seconds):
        """Build the position/duration playback progress text."""
        return f"{_format_duration(position_seconds)} / {_format_duration(duration_seconds)}"

    def _update_timer(self):
        """Update recording timer."""
        if not self.is_recording:
            return False

        now = time.time()
        diff = now - self.start_time

        if diff >= self.max_seconds:
            self._stop_recording()
            return False

        self.lbl_timer.set_label(self._recording_timer_text(diff))
        return True

    def _update_playback_progress(self):
        """Refresh the playback position label and progress bar."""
        if not self.player:
            return False

        ok_pos, position = self.player.query_position(Gst.Format.TIME)
        ok_dur, duration = self.player.query_duration(Gst.Format.TIME)
        if not ok_pos or not ok_dur or duration <= 0:
            return True

        self.lbl_progress.set_label(
            self._playback_progress_text(position // Gst.SECOND, duration // Gst.SECOND))
        self.progress_bar.set_fraction(min(position / duration, 1.0))
        return True

    def _show_error(self, message):
        """Show an error toast."""
        toast = Adw.Toast.new(message)
        self.toast_overlay.add_toast(toast)
