# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import os
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Adw, Gst, GLib, Gdk, Gsk, Graphene

from telephony.shared.constants import CAPTURE_SHEET_HEIGHT
from telephony.shared.utils.log_utils import logger


def format_duration(seconds):
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


CAPTURE_SWEEP_AGE_SECONDS = 24 * 3600


class PreviewStage(Gtk.Widget):

    def __init__(self, child):
        super().__init__()
        self._child = child
        self._child.set_parent(self)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._child.connect("notify::paintable", self.on_paintable_replaced)
        self._paintable_watch = None
        self.watch_paintable()

    def on_paintable_replaced(self, _picture, _pspec):
        self.watch_paintable()
        self.queue_allocate()

    def watch_paintable(self):
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
        if self._paintable_watch is not None:
            obj, handler = self._paintable_watch
            obj.disconnect(handler)
            self._paintable_watch = None
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)


class MediaCaptureWindow(Adw.NavigationPage):

    def capture_dir(self):
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

    def light_icon_name(self):
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        if theme.has_icon("flashlight-symbolic"):
            return "flashlight-symbolic"
        return "display-brightness-symbolic"

    def letterbox(self, picture):
        return PreviewStage(picture)

    def request_capture_height(self, parent_window):
        available = parent_window.get_height() if parent_window else 0
        if available > 0:
            self.set_size_request(-1, min(CAPTURE_SHEET_HEIGHT, int(available * 0.8)))
        else:
            self.set_size_request(-1, CAPTURE_SHEET_HEIGHT)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._closed = False
        self._timeout_ids = set()
        self.remote_fd = -1

    def take_remote_fd(self, portal):
        """Take the portal remote the next pipeline is built on. pipewiresrc dups the descriptor it is handed, so the original outlives the pipeline and belongs to whoever asked for it."""
        self.close_remote_fd()
        self.remote_fd = portal.pipeline_fd()
        return self.remote_fd

    def close_remote_fd(self):
        if self.remote_fd < 0:
            return
        try:
            os.close(self.remote_fd)
        except OSError as e:
            logger.warning(f"[MediaCapture] Remote fd close failed: {e}")
        self.remote_fd = -1

    def schedule_timeout(self, interval_ms, callback):
        holder = {"id": 0}

        def wrapper():
            if self._closed:
                self._timeout_ids.discard(holder["id"])
                return False
            keep_going = bool(callback())
            if not keep_going:
                self._timeout_ids.discard(holder["id"])
            return keep_going

        holder["id"] = GLib.timeout_add(interval_ms, wrapper)
        self._timeout_ids.add(holder["id"])
        return holder["id"]

    def cancel_timeout(self, source_id):
        if source_id in self._timeout_ids:
            GLib.source_remove(source_id)
            self._timeout_ids.discard(source_id)

    def cancel_tracked_timeouts(self):
        for source_id in list(self._timeout_ids):
            GLib.source_remove(source_id)
        self._timeout_ids.clear()

    def watch_bus(self, element, handler):
        bus = element.get_bus()
        bus.add_signal_watch()
        handler_id = bus.connect("message", handler)
        return bus, handler_id

    def recording_timer_text(self, elapsed_seconds):
        return f"{format_duration(elapsed_seconds)} / {format_duration(self.max_seconds)}"

    def playback_progress_text(self, position_seconds, duration_seconds):
        return f"{format_duration(position_seconds)} / {format_duration(duration_seconds)}"

    def update_timer(self):
        if not self.is_recording:
            return False

        now = time.time()
        diff = now - self.start_time

        if diff >= self.max_seconds:
            self.stop_recording()
            return False

        self.lbl_timer.set_label(self.recording_timer_text(diff))
        return True

    def update_playback_progress(self):
        if not self.player:
            return False

        ok_pos, position = self.player.query_position(Gst.Format.TIME)
        ok_dur, duration = self.player.query_duration(Gst.Format.TIME)
        if not ok_pos or not ok_dur or duration <= 0:
            return True

        self.lbl_progress.set_label(
            self.playback_progress_text(position // Gst.SECOND, duration // Gst.SECOND))
        self.progress_bar.set_fraction(min(position / duration, 1.0))
        return True

    def show_error(self, message):
        toast = Adw.Toast.new(message)
        self.toast_overlay.add_toast(toast)
