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

import os
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, Gst, GLib, Gsk, Graphene

from telephony.shared.constants import CAPTURE_SHEET_HEIGHT
from telephony.shared.utils.log_utils import logger



def _format_duration(seconds):
    """Format a duration in whole seconds as M:SS."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


CAPTURE_SWEEP_AGE_SECONDS = 24 * 3600


class PreviewStage(Gtk.Widget):
    """A box that sizes its child instead of asking the child.

    A widget subclass with no layout manager, because a manager would
    supply the measurement this class exists to withhold.
    """

    def __init__(self, child):
        """Adopt the child; the stage owns it until disposed."""
        super().__init__()
        self._child = child
        self._child.set_parent(self)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._child.connect("notify::paintable", self._on_paintable_replaced)
        self._paintable_watch = None
        self._watch_paintable()

    def _on_paintable_replaced(self, _picture, _pspec):
        """Follow the new paintable and reshape around it."""
        self._watch_paintable()
        self.queue_allocate()

    def _watch_paintable(self):
        """Reallocate when the current paintable learns its size."""
        if self._paintable_watch is not None:
            obj, handler = self._paintable_watch
            obj.disconnect(handler)
            self._paintable_watch = None
        paintable = self._child.get_paintable()
        if paintable is not None:
            self._paintable_watch = (paintable, paintable.connect(
                "invalidate-size", lambda _p: self.queue_allocate()))

    def do_measure(self, orientation, for_size):
        return (0, 0, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        """Give the child the shape of what it shows, centered.

        Allocated the whole box, a contained frame floats inset from
        the widget's corners, so a rounded clip on the child trims
        only the empty letterbox bars and the visible frame stays
        square. Shaped to the frame's aspect, the child's corners are
        the frame's corners and the rounding lands on pixels.
        """
        paintable = self._child.get_paintable()
        pw = paintable.get_intrinsic_width() if paintable else 0
        ph = paintable.get_intrinsic_height() if paintable else 0
        if pw > 0 and ph > 0 and width > 0 and height > 0:
            scale = min(width / pw, height / ph)
            cw, ch = max(1, round(pw * scale)), max(1, round(ph * scale))
            transform = Gsk.Transform.new().translate(
                Graphene.Point().init((width - cw) / 2, (height - ch) / 2))
            self._child.allocate(cw, ch, baseline, transform)
            return
        self._child.allocate(width, height, baseline, None)

    def do_dispose(self):
        """Release the child and its paintable watch before the widget goes."""
        if self._paintable_watch is not None:
            obj, handler = self._paintable_watch
            obj.disconnect(handler)
            self._paintable_watch = None
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)


class MediaCaptureWindow(Adw.NavigationPage):
    """Base capture sheet providing shared timeout, bus watch and progress scaffolding."""

    def capture_dir(self):
        """Return the on-disk directory captures are written to.

        The system temp directory is tmpfs on this platform, so a
        video recording written there streams straight into RAM for
        as long as it runs. The cache directory is real storage;
        leftovers from interrupted sessions are swept on the next
        capture rather than on boot, which tmpfs used to do for free.
        Blocking, call from a worker or accept the mkdir on first use.
        """
        directory = os.path.join(GLib.get_user_cache_dir(), "telephony", "captures")
        os.makedirs(directory, exist_ok=True)
        now = time.time()
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            try:
                if now - os.path.getmtime(path) > CAPTURE_SWEEP_AGE_SECONDS:
                    os.remove(path)
            except OSError as e:
                logger.debug(f"[Capture] Sweep skipped {name}: {e}")
        return directory

    def reveal_on_first_frame(self, picture):
        """Keep a picture hidden until its paintable actually draws.

        The stage gives the picture its full box before the camera is
        awake, and the sink paints black until frames arrive; hidden,
        the card shows instead, and the live image appears in place.
        """
        paintable = picture.get_paintable()
        if paintable is None:
            picture.set_visible(True)
            return
        picture.set_visible(False)
        state = {"handler": None}

        def first_frame(_paintable):
            picture.set_visible(True)
            if state["handler"] is not None:
                paintable.disconnect(state["handler"])
                state["handler"] = None

        state["handler"] = paintable.connect("invalidate-contents", first_frame)

    def letterbox(self, picture):
        """Keep a picture from dictating the size of its box.

        A picture asks for the natural size of whatever it shows, so
        the box jumps when the camera starts, the camera flips or the
        review appears, each with its own dimensions. The stage
        reports no size of its own and hands the picture exactly the
        room it was given, so the picture scales into it; a scroll
        area would break the request too, but it allocates its child
        at natural size, which had every camera frame painted at full
        resolution into a box showing a fraction of it.
        """
        return PreviewStage(picture)

    def request_capture_height(self, parent_window):
        """Ask for the capture height, capped to the window it opens in.

        The fixed ask overflowed short screens, pushing the controls
        off the bottom, and a preview that shrinks to fit is more use
        than one that covers the window. The fraction leaves the sheet
        visibly a sheet rather than a takeover.
        """
        available = parent_window.get_height() if parent_window else 0
        if available > 0:
            self.set_size_request(-1, min(CAPTURE_SHEET_HEIGHT, int(available * 0.8)))
        else:
            self.set_size_request(-1, CAPTURE_SHEET_HEIGHT)

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
