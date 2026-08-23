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
from gettext import gettext as _

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, Gst, GLib
from telephony.shared.utils.log_utils import logger

from telephony.shared.constants import (VIEWFINDER_START_DELAY_MS, PLAYBACK_PROGRESS_INTERVAL_MS, EOS_TIMEOUT_MS, PROGRESS_BAR_WIDTH, VIEWFINDER_SINK_WIDTH)
from telephony.client.services.camera_portal import CameraPortal
from telephony.client.services.flashlight_client import FlashlightClient

PIPELINE_DRAIN_TIMEOUT_NS = 2 * Gst.SECOND
from telephony.client.ui.windows.media_window_base import MediaCaptureWindow
from telephony.client.ui.widgets.common_widget import close_sheet_page

RECORD_TIMER_INTERVAL_MS = 100
MAX_RECORD_SECONDS = 8
MIN_VALID_VIDEO_BYTES = 1000
LARGE_VIDEO_WARN_BYTES = 300 * 1024


class CameraVideo(MediaCaptureWindow):
    """Camera window for recording videos."""

    def __init__(self, parent_window, on_attach_callback):
        super().__init__()
        self.request_capture_height(parent_window)

        registry = Gst.Registry.get()
        droidvdec = registry.lookup_feature("droidvdec")
        if droidvdec:
            droidvdec.set_rank(Gst.Rank.NONE)
            logger.info("[Camera-Video] Disabled droidvdec to force software decoding.")

        self.on_attach_callback = on_attach_callback
        self._attached = False
        self.set_title(_("Record Video"))

        self.output_path = None
        self.toast_overlay = None

        self.pipeline = None
        self.bus = None
        self.bus_handler_id = None
        self.record_bin = None
        self.tee_record_pad = None
        self.take_eos_sent = False
        self.player = None
        self.player_bus = None
        self.player_bus_handler_id = None
        self.is_recording = False
        self.camera_device = 0
        self.light_on = False
        self.portal = CameraPortal()
        self.flashlight = FlashlightClient()
        self.start_time = 0
        self.timer_id = None
        self.eos_timeout_id = None
        self.progress_timer_id = None
        self.max_seconds = MAX_RECORD_SECONDS

        self._setup_ui()
        self.connect("hidden", self._on_closed)

        self._schedule_timeout(VIEWFINDER_START_DELAY_MS, self._start_viewfinder)

    def _setup_ui(self):
        """Build the UI components."""
        self.toast_overlay = Adw.ToastOverlay()
        self.set_child(self.toast_overlay)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(content)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        content.append(header)


        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        content.append(self.stack)

        self.page_capture = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card_box.add_css_class("preview-round")
        card_box.set_hexpand(True)
        card_box.set_vexpand(True)
        card_box.set_margin_top(10)
        card_box.set_margin_bottom(10)
        card_box.set_margin_start(10)
        card_box.set_margin_end(10)
        card_box.set_overflow(Gtk.Overflow.HIDDEN)

        self.viewfinder_widget = Gtk.Picture()
        self.viewfinder_widget.add_css_class("preview-round")
        self.viewfinder_widget.set_overflow(Gtk.Overflow.HIDDEN)
        self.viewfinder_widget.set_can_shrink(True)
        self.viewfinder_widget.set_hexpand(True)
        self.viewfinder_widget.set_vexpand(True)
        self.viewfinder_widget.set_content_fit(Gtk.ContentFit.CONTAIN)

        self.preview_card = card_box
        self.viewfinder_holder = self.letterbox(self.viewfinder_widget)
        card_box.append(self.viewfinder_holder)
        self.page_capture.append(card_box)

        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ctrl_box.set_halign(Gtk.Align.CENTER)
        ctrl_box.set_margin_top(20)
        ctrl_box.set_margin_bottom(20)

        self.lbl_timer = Gtk.Label(label=self._recording_timer_text(0))
        self.lbl_timer.add_css_class("title-2")
        self.lbl_timer.add_css_class("numeric")
        ctrl_box.append(self.lbl_timer)

        record_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=30)
        record_row.set_halign(Gtk.Align.CENTER)

        self.btn_record = Gtk.Button()
        self.btn_record.set_icon_name("media-record-symbolic")
        self.btn_record.set_size_request(80, 80)
        self.btn_record.add_css_class("video-record-button")
        self.btn_record.add_css_class("circular")
        self.btn_record.set_halign(Gtk.Align.CENTER)
        self.btn_record.set_valign(Gtk.Align.CENTER)
        self.btn_record.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_record_toggle(b) or False))
        record_row.append(self.btn_record)

        self.btn_light = Gtk.ToggleButton(icon_name=self.light_icon_name(), css_classes=["circular"])
        self.btn_light.set_size_request(48, 48)
        self.btn_light.set_valign(Gtk.Align.CENTER)
        self.btn_light.connect("toggled", lambda b: GLib.idle_add(lambda: self._on_light_toggled(b) or False))
        record_row.prepend(self.btn_light)

        self.btn_flip = Gtk.Button(icon_name="camera-switch-symbolic", css_classes=["circular"])
        self.btn_flip.set_size_request(48, 48)
        self.btn_flip.set_valign(Gtk.Align.CENTER)
        self.btn_flip.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_flip_camera() or False))
        record_row.append(self.btn_flip)
        ctrl_box.append(record_row)

        self.page_capture.append(ctrl_box)
        self.stack.add_named(self.page_capture, "capture")

        self.page_review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        review_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        review_card.add_css_class("preview-round")
        review_card.set_hexpand(True)
        review_card.set_vexpand(True)
        review_card.set_margin_top(10)
        review_card.set_margin_bottom(10)
        review_card.set_margin_start(10)
        review_card.set_margin_end(10)
        review_card.set_overflow(Gtk.Overflow.HIDDEN)

        self.review_widget = Gtk.Picture()
        self.review_widget.add_css_class("preview-round")
        self.review_widget.set_overflow(Gtk.Overflow.HIDDEN)
        self.review_widget.set_can_shrink(True)
        self.review_widget.set_hexpand(True)
        self.review_widget.set_vexpand(True)
        self.review_widget.set_content_fit(Gtk.ContentFit.CONTAIN)

        review_card.append(self.letterbox(self.review_widget))
        self.page_review.append(review_card)

        act_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        act_box.set_halign(Gtk.Align.CENTER)
        act_box.set_margin_top(20)
        act_box.set_margin_bottom(20)

        self.btn_play = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.btn_play.add_css_class("circular")
        self.btn_play.add_css_class("suggested-action")
        self.btn_play.set_size_request(60, 60)
        self.btn_play.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_play_toggle(b) or False))

        b_p_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        b_p_box.set_halign(Gtk.Align.CENTER)
        b_p_box.append(self.btn_play)
        act_box.append(b_p_box)

        self.lbl_progress = Gtk.Label(label=self._playback_progress_text(0, 0))
        self.lbl_progress.add_css_class("numeric")
        self.lbl_progress.add_css_class("dim-label")
        act_box.append(self.lbl_progress)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_size_request(PROGRESS_BAR_WIDTH, -1)
        self.progress_bar.set_halign(Gtk.Align.CENTER)
        act_box.append(self.progress_bar)

        row_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)

        btn_retake = Gtk.Button(label=_("Retake"))
        btn_retake.add_css_class("pill")
        btn_retake.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_retake_clicked(b) or False))
        row_btns.append(btn_retake)

        btn_attach = Gtk.Button(label=_("Attach Video"))
        btn_attach.add_css_class("pill")
        btn_attach.add_css_class("suggested-action")
        btn_attach.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_attach_clicked(b) or False))
        row_btns.append(btn_attach)

        act_box.append(row_btns)
        self.page_review.append(act_box)
        self.stack.add_named(self.page_review, "review")

    def _on_flip_camera(self):
        """Switch between the back and the front camera.

        Only between takes: the source cannot change camera while it
        runs, and restarting the pipeline mid-recording would end the
        take, so a recording keeps the camera it started with.
        """
        if self.is_recording:
            return
        self.btn_flip.set_sensitive(False)
        self.btn_light.set_active(False)
        self.camera_device = 1 - self.camera_device
        self.btn_light.set_sensitive(self.camera_device == 0)
        self._start_viewfinder()

    def _on_light_toggled(self, btn):
        """Drive the torch; the back camera's is the only light."""
        self.light_on = btn.get_active()
        if self.light_on:
            btn.add_css_class("suggested-action")
        else:
            btn.remove_css_class("suggested-action")
        if self.light_on:
            self.flashlight.set_on()
        else:
            self.flashlight.set_off()

    def _light_off(self):
        """Put the torch out; safe from any exit path."""
        self.btn_light.set_active(False)
        self.flashlight.set_off()

    def _start_viewfinder(self):
        """Start the viewfinder once the camera portal has answered.

        The portal handshake is asynchronous only the first time; once
        the remote is open, the answer is immediate and a flip or a
        retake builds its pipeline in the same breath.
        """
        if self._closed:
            return False

        self._stop_pipeline()
        self.portal.open(self._on_portal_ready)
        return False

    def _on_portal_ready(self, devices):
        """Build the viewfinder pipeline on the portal's remote."""
        if self._closed:
            return
        device = self.portal.device_for(self.camera_device)
        fd = self.portal.pipeline_fd()
        if not devices or device is None or fd < 0:
            self._show_error(_("Error: {e}").format(e="camera unavailable"))
            return

        try:
            pipeline_str = (
                f"pipewiresrc fd={fd} target-object={device.serial} ! tee name=t "
                f"t. ! queue leaky=downstream max-size-buffers=2 ! videorate drop-only=true ! video/x-raw,framerate=30/1 ! videoconvert ! videoflip video-direction=auto ! videoscale ! video/x-raw,width={VIEWFINDER_SINK_WIDTH},pixel-aspect-ratio=1/1 ! gtk4paintablesink name=sink"
            )
            logger.info(f"[Camera-Video] Starting viewfinder: {pipeline_str}")

            self.pipeline = Gst.parse_launch(pipeline_str)

            sink = self.pipeline.get_by_name("sink")
            if sink:
                paintable = sink.get_property("paintable")
                self.viewfinder_widget.set_paintable(paintable)
                self.reveal_on_first_frame(self.viewfinder_widget)

            self.bus, self.bus_handler_id = self._watch_bus(self.pipeline, self._on_viewfinder_message)

            self.pipeline.set_state(Gst.State.PLAYING)
            return False
        except Exception as e:
            logger.error(f"[Camera-Video] Failed to start viewfinder: {e}")
            self._show_error(_("Error: {e}").format(e=e))
            return False

    def _on_viewfinder_message(self, bus, message):
        """Handle viewfinder messages."""
        t = message.type
        if (t == Gst.MessageType.STATE_CHANGED and message.src == self.pipeline
                and message.parse_state_changed()[1] == Gst.State.PLAYING):
            self.btn_flip.set_sensitive(True)
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Video] Pipeline error: {err} : {debug}")
            if self.record_bin is not None:
                GLib.idle_add(lambda: self._abort_take(str(err)) or False)
                return
            self.btn_flip.set_sensitive(True)
            self._stop_pipeline()
            self._show_error(_("Error: {e}").format(e=err))

    def _stop_pipeline(self):
        """Stop the GStreamer pipeline and release its bus watch."""
        self.viewfinder_widget.set_paintable(None)
        self.record_bin = None
        self.tee_record_pad = None
        self.is_recording = False
        if self.bus:
            if self.bus_handler_id:
                self.bus.disconnect(self.bus_handler_id)
            self.bus.remove_signal_watch()
            self.bus = None
            self.bus_handler_id = None
        if self.pipeline:
            logger.debug("[Camera-Video] Stopping pipeline...")
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_state(PIPELINE_DRAIN_TIMEOUT_NS)
            self.pipeline = None
        if self.timer_id:
            self._cancel_timeout(self.timer_id)
            self.timer_id = None

    def _on_record_toggle(self, btn):
        """Toggle recording state."""
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        """Attach a recording branch to the running camera.

        The take taps the tee of the viewfinder that is already
        delivering, so the file starts on a frame the screen shows: a
        pipeline rebuilt for recording reopened the sensor, and its
        first second of exposure ramp opened every file with black.
        """
        if self.pipeline is None:
            self._show_error(_("Failed to record video: {error_msg}").format(error_msg="no camera"))
            return

        self.output_path = os.path.join(self.capture_dir(), f"video_{int(time.time())}.mkv")
        self.take_eos_sent = False
        bin_description = (
            f"queue name=record_queue max-size-bytes=0 max-size-time=2000000000 ! "
            f"videorate skip-to-first=true ! video/x-raw,framerate=30/1 ! "
            f"videoconvert ! videoflip video-direction=auto ! videoscale ! video/x-raw,width={VIEWFINDER_SINK_WIDTH},pixel-aspect-ratio=1/1 ! videoconvert ! "
            f"jpegenc quality=70 ! matroskamux name=mux offset-to-zero=true ! filesink name=record_sink location={self.output_path} "
            f"autoaudiosrc ! queue name=audio_queue ! audioconvert ! avenc_aac ! mux."
        )

        try:
            logger.info(f"[Camera-Video] Starting take: {bin_description}")
            self.record_bin = Gst.parse_bin_from_description(bin_description, True)
            self.pipeline.add(self.record_bin)
            self.record_bin.sync_state_with_parent()
            tee = self.pipeline.get_by_name("t")
            self.tee_record_pad = tee.request_pad_simple("src_%u")
            self.tee_record_pad.link(self.record_bin.get_static_pad("sink"))
        except Exception as e:
            logger.error(f"[Camera-Video] Failed to start take: {e}")
            self._abort_take(str(e))
            return

        self.is_recording = True
        self.btn_flip.set_sensitive(False)
        self.start_time = time.time()
        self.btn_record.set_icon_name("media-playback-stop-symbolic")
        self.timer_id = self._schedule_timeout(RECORD_TIMER_INTERVAL_MS, self._update_timer)

    def _stop_recording(self):
        """End the take by draining its branch while the camera runs.

        The tee pad is blocked first, since a branch answering EOS to
        further buffers would poison the tee's combined flow and take
        the viewfinder down with it; the EOS goes to both queues from
        inside the block, and the file is finished when it reaches the
        file sink, which is watched from a probe because a sink inside
        a still-playing pipeline finishes without a bus message.
        """
        self.is_recording = False
        self.btn_flip.set_sensitive(True)
        self.btn_record.set_icon_name("media-record-symbolic")
        self.btn_record.set_sensitive(False)

        if self.timer_id:
            self._cancel_timeout(self.timer_id)
            self.timer_id = None

        if self.record_bin is None or self.pipeline is None:
            self._show_review()
            self.btn_record.set_sensitive(True)
            return

        record_sink = self.record_bin.get_by_name("record_sink")
        record_sink.get_static_pad("sink").add_probe(
            Gst.PadProbeType.EVENT_DOWNSTREAM, self._on_record_sink_event)
        self.tee_record_pad.add_probe(
            Gst.PadProbeType.BLOCK_DOWNSTREAM, self._on_tee_blocked)
        self.eos_timeout_id = self._schedule_timeout(EOS_TIMEOUT_MS, self._force_stop)

    def _on_tee_blocked(self, pad, info):
        """Send the take its EOS once the tee has stopped feeding it."""
        if not self.take_eos_sent and self.record_bin is not None:
            self.take_eos_sent = True
            for name in ("record_queue", "audio_queue"):
                element = self.record_bin.get_by_name(name)
                if element:
                    element.get_static_pad("sink").send_event(Gst.Event.new_eos())
        return Gst.PadProbeReturn.OK

    def _on_record_sink_event(self, pad, info):
        """Finish the take when its EOS reaches the file sink."""
        event = info.get_event()
        if event is not None and event.type == Gst.EventType.EOS:
            GLib.idle_add(self._finish_take)
        return Gst.PadProbeReturn.OK

    def _teardown_take(self):
        """Detach and dispose the take's branch; the camera keeps running."""
        record_bin, self.record_bin = self.record_bin, None
        pad, self.tee_record_pad = self.tee_record_pad, None
        if record_bin is None or self.pipeline is None:
            return
        record_bin.set_state(Gst.State.NULL)
        self.pipeline.remove(record_bin)
        tee = self.pipeline.get_by_name("t")
        if pad is not None and tee is not None:
            tee.release_request_pad(pad)

    def _finish_take(self):
        """Hand the finished file to the review page."""
        if self.eos_timeout_id:
            self._cancel_timeout(self.eos_timeout_id)
            self.eos_timeout_id = None
        self._teardown_take()
        if self._closed:
            return False
        self.btn_record.set_sensitive(True)
        self._show_review()
        return False

    def _abort_take(self, error_msg):
        """Drop a failed take and stay on the live viewfinder."""
        self._teardown_take()
        self.is_recording = False
        self.btn_flip.set_sensitive(True)
        self.btn_record.set_icon_name("media-record-symbolic")
        if self.timer_id:
            self._cancel_timeout(self.timer_id)
            self.timer_id = None
        if self._closed:
            return
        self.btn_record.set_sensitive(True)
        logger.error(f"[Camera-Video] Take failed: {error_msg}")
        self._show_error(_("Failed to record video: {error_msg}").format(error_msg=error_msg))

    def _force_stop(self):
        """Force the take closed if its EOS never lands."""
        logger.warning("[Camera-Video] EOS timeout, forcing stop.")
        self.eos_timeout_id = None
        self._finish_take()
        return False

    def _show_review(self):
        """Show the review page."""
        if self._closed:
            return
        if self.output_path and os.path.exists(self.output_path):
            size = os.path.getsize(self.output_path)
            logger.info(f"[Camera-Video] Video recorded. Size: {size}")
            if size < MIN_VALID_VIDEO_BYTES:
                self._show_error(_("Recording failed (File empty)."))
                self._on_retake_clicked(None)
                return
            elif size > LARGE_VIDEO_WARN_BYTES:
                self._show_error(_("Large video ({size}KB). Will be compressed on send.").format(size=size // 1024))

        self.btn_light.set_active(False)
        self.stack.set_visible_child_name("review")
        self._present_poster()

    def _present_poster(self):
        """Stand the take's first frame up before anyone presses play.

        The player is built paused, so preroll decodes exactly one
        frame into the paintable and the review page opens showing
        the take instead of an empty card; play merely resumes. The
        end of playback rewinds to this same paused-at-start state.
        """
        self._stop_playback()
        if self._build_player():
            self.player.set_state(Gst.State.PAUSED)

    def _build_player(self):
        """Assemble the player around the recorded file, not yet rolling."""
        if not self.output_path or not os.path.exists(self.output_path):
            return False
        if os.path.getsize(self.output_path) == 0:
            self._show_error(_("Cannot play empty file."))
            return False
        try:
            self.player = Gst.parse_launch(f"playbin uri=file://{self.output_path}")
            sink = Gst.ElementFactory.make("gtk4paintablesink", "psink")
            self.player.set_property("video-sink", sink)
            paintable = sink.get_property("paintable")
            self.review_widget.set_paintable(paintable)
            self.reveal_on_first_frame(self.review_widget)
            self.player_bus, self.player_bus_handler_id = self._watch_bus(self.player, self._on_player_message)
            return True
        except Exception as e:
            logger.error(f"[Camera-Video] Player build failed: {e}")
            self._show_error(str(e))
            return False

    def _on_play_toggle(self, btn):
        """Resume the poster player, or pause it in place."""
        if self.player is None:
            if not self._build_player():
                return
            self.player.set_state(Gst.State.PAUSED)
        state = self.player.get_state(0)[1]
        if state == Gst.State.PLAYING:
            self.player.set_state(Gst.State.PAUSED)
            self.btn_play.set_icon_name("media-playback-start-symbolic")
            if self.progress_timer_id:
                self._cancel_timeout(self.progress_timer_id)
                self.progress_timer_id = None
            return
        self._stop_pipeline()
        self.player.set_state(Gst.State.PLAYING)
        self.btn_play.set_icon_name("media-playback-pause-symbolic")
        if self.progress_timer_id is None:
            self.progress_timer_id = self._schedule_timeout(
                PLAYBACK_PROGRESS_INTERVAL_MS, self._update_playback_progress)

    def _stop_playback(self):
        """Stop video playback and release playback resources."""
        if self.progress_timer_id:
            self._cancel_timeout(self.progress_timer_id)
            self.progress_timer_id = None
        if self.player_bus:
            if self.player_bus_handler_id:
                self.player_bus.disconnect(self.player_bus_handler_id)
            self.player_bus.remove_signal_watch()
            self.player_bus = None
            self.player_bus_handler_id = None
        if self.player:
            self.player.set_state(Gst.State.NULL)
            self.player = None
        self.review_widget.set_visible(False)
        self.btn_play.set_icon_name("media-playback-start-symbolic")
        self.lbl_progress.set_label(self._playback_progress_text(0, 0))
        self.progress_bar.set_fraction(0.0)

    def _on_player_message(self, bus, message):
        """Handle playback messages.

        The end of the video is not the end of the player: tearing it
        down leaves the picture holding a dead paintable, which paints
        black. Rewound and paused it keeps the first frame up as a
        poster, and play starts it again from the top.

        The bar is finished by hand first, because it follows a timer
        that last fired up to half a second before the end and would
        otherwise stand a few percent short of done forever.
        """
        t = message.type
        if t == Gst.MessageType.EOS:
            ok_dur, duration = self.player.query_duration(Gst.Format.TIME)
            if ok_dur and duration > 0:
                seconds = duration // Gst.SECOND
                self.lbl_progress.set_label(self._playback_progress_text(seconds, seconds))
                self.progress_bar.set_fraction(1.0)
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.player.set_state(Gst.State.PAUSED)
            if self.progress_timer_id:
                self._cancel_timeout(self.progress_timer_id)
                self.progress_timer_id = None
            self.btn_play.set_icon_name("media-playback-start-symbolic")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Video] Playback error: {err} : {debug}")
            self._stop_playback()

    def _restart_viewfinder_safe(self):
        """Restart viewfinder and reset UI state."""
        if self._closed:
            return False
        self.btn_record.set_sensitive(True)
        self._start_viewfinder()
        return False

    def _on_retake_clicked(self, btn):
        """Return to the viewfinder, restarting it only after playback.

        A finished take leaves the camera running, so retake is just a
        page turn; only playback stops the viewfinder to keep a single
        stream decoding at a time, and coming back from it restarts.
        """
        self._stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            try:
                os.remove(self.output_path)
            except Exception as e:
                logger.warning(f"[Camera-Video] Failed to remove temp file: {e}")
        self.output_path = None
        self.lbl_timer.set_label(self._recording_timer_text(0))
        self.stack.set_visible_child_name("capture")
        if self.pipeline is None:
            self._restart_viewfinder_safe()

    def _on_attach_clicked(self, btn):
        """Handle attach button click."""
        self._stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            if self.on_attach_callback:
                self._attached = True
                self.on_attach_callback(self.output_path)
        GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)

    def _on_closed(self, _dialog):
        """Tear down capture state when the sheet closes."""
        self._closed = True
        self._light_off()
        self._cancel_tracked_timeouts()
        self._stop_pipeline()
        self._stop_playback()
        self.portal.close()
        self._discard_unattached_output()

    def _discard_unattached_output(self):
        """Delete the recorded file when the window closes without attaching."""
        if self._attached or not self.output_path:
            return
        try:
            if os.path.exists(self.output_path):
                os.remove(self.output_path)
        except Exception as e:
            logger.warning(f"[Camera-Video] Temp file removal failed: {e}")
