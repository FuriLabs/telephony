# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import os
import time
from gettext import gettext as _

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
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

        self.setup_ui()
        self.connect("hidden", self.on_closed)

        self.schedule_timeout(VIEWFINDER_START_DELAY_MS, self.start_viewfinder)

    def setup_ui(self):
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

        self.viewfinder_holder = self.letterbox(self.viewfinder_widget)
        card_box.append(self.viewfinder_holder)
        self.page_capture.append(card_box)

        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ctrl_box.set_halign(Gtk.Align.CENTER)
        ctrl_box.set_margin_top(20)
        ctrl_box.set_margin_bottom(20)

        self.lbl_timer = Gtk.Label(label=self.recording_timer_text(0))
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
        self.btn_record.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_record_toggle(b) or False))
        record_row.append(self.btn_record)

        self.btn_light = Gtk.ToggleButton(icon_name=self.light_icon_name(), css_classes=["circular"])
        self.btn_light.set_size_request(48, 48)
        self.btn_light.set_valign(Gtk.Align.CENTER)
        self.btn_light.connect("toggled", lambda b: GLib.idle_add(lambda: self.on_light_toggled(b) or False))
        record_row.prepend(self.btn_light)

        self.btn_flip = Gtk.Button(icon_name="camera-switch-symbolic", css_classes=["circular"])
        self.btn_flip.set_size_request(48, 48)
        self.btn_flip.set_valign(Gtk.Align.CENTER)
        self.btn_flip.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_flip_camera() or False))
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
        self.btn_play.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_play_toggle(b) or False))

        b_p_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        b_p_box.set_halign(Gtk.Align.CENTER)
        b_p_box.append(self.btn_play)
        act_box.append(b_p_box)

        self.lbl_progress = Gtk.Label(label=self.playback_progress_text(0, 0))
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
        btn_retake.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_retake_clicked(b) or False))
        row_btns.append(btn_retake)

        btn_attach = Gtk.Button(label=_("Attach Video"))
        btn_attach.add_css_class("pill")
        btn_attach.add_css_class("suggested-action")
        btn_attach.connect("clicked", lambda b: GLib.idle_add(lambda: self.on_attach_clicked(b) or False))
        row_btns.append(btn_attach)

        act_box.append(row_btns)
        self.page_review.append(act_box)
        self.stack.add_named(self.page_review, "review")

    def on_flip_camera(self):
        if self.is_recording:
            return
        self.btn_flip.set_sensitive(False)
        self.btn_light.set_active(False)
        self.camera_device = 1 - self.camera_device
        self.btn_light.set_sensitive(self.camera_device == 0)
        self.start_viewfinder()

    def on_light_toggled(self, btn):
        self.light_on = btn.get_active()
        if self.light_on:
            btn.add_css_class("suggested-action")
        else:
            btn.remove_css_class("suggested-action")
        if self.light_on:
            self.flashlight.set_on()
        else:
            self.flashlight.set_off()

    def light_off(self):
        self.btn_light.set_active(False)
        self.flashlight.set_off()

    def start_viewfinder(self):
        if self._closed:
            return False

        self.stop_pipeline()
        self.portal.open(self.on_portal_ready)
        return False

    def on_portal_ready(self, devices):
        if self._closed:
            return
        device = self.portal.device_for(self.camera_device)
        fd = self.take_remote_fd(self.portal)
        if not devices or device is None or fd < 0:
            self.show_error(_("Error: {e}").format(e="camera unavailable"))
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

            self.bus, self.bus_handler_id = self.watch_bus(self.pipeline, self.on_viewfinder_message)

            self.pipeline.set_state(Gst.State.PLAYING)
            return False
        except Exception as e:
            logger.error(f"[Camera-Video] Failed to start viewfinder: {e}")
            self.show_error(_("Error: {e}").format(e=e))
            return False

    def on_viewfinder_message(self, bus, message):
        t = message.type
        if (t == Gst.MessageType.STATE_CHANGED and message.src == self.pipeline
                and message.parse_state_changed()[1] == Gst.State.PLAYING):
            self.btn_flip.set_sensitive(True)
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Video] Pipeline error: {err} : {debug}")
            if self.record_bin is not None:
                GLib.idle_add(lambda: self.abort_take(str(err)) or False)
                return
            self.btn_flip.set_sensitive(True)
            self.stop_pipeline()
            self.show_error(_("Error: {e}").format(e=err))

    def stop_pipeline(self):
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
        self.close_remote_fd()
        if self.timer_id:
            self.cancel_timeout(self.timer_id)
            self.timer_id = None

    def on_record_toggle(self, btn):
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self.pipeline is None:
            self.show_error(_("Failed to record video: {error_msg}").format(error_msg="no camera"))
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
            self.abort_take(str(e))
            return

        self.is_recording = True
        self.btn_flip.set_sensitive(False)
        self.start_time = time.time()
        self.btn_record.set_icon_name("media-playback-stop-symbolic")
        self.timer_id = self.schedule_timeout(RECORD_TIMER_INTERVAL_MS, self.update_timer)

    def stop_recording(self):
        self.is_recording = False
        self.btn_flip.set_sensitive(True)
        self.btn_record.set_icon_name("media-record-symbolic")
        self.btn_record.set_sensitive(False)

        if self.timer_id:
            self.cancel_timeout(self.timer_id)
            self.timer_id = None

        if self.record_bin is None or self.pipeline is None:
            self.show_review()
            self.btn_record.set_sensitive(True)
            return

        record_sink = self.record_bin.get_by_name("record_sink")
        record_sink.get_static_pad("sink").add_probe(
            Gst.PadProbeType.EVENT_DOWNSTREAM, self.on_record_sink_event)
        self.tee_record_pad.add_probe(
            Gst.PadProbeType.BLOCK_DOWNSTREAM, self.on_tee_blocked)
        self.eos_timeout_id = self.schedule_timeout(EOS_TIMEOUT_MS, self.force_stop)

    def on_tee_blocked(self, pad, info):
        if not self.take_eos_sent and self.record_bin is not None:
            self.take_eos_sent = True
            for name in ("record_queue", "audio_queue"):
                element = self.record_bin.get_by_name(name)
                if element:
                    element.get_static_pad("sink").send_event(Gst.Event.new_eos())
        return Gst.PadProbeReturn.OK

    def on_record_sink_event(self, pad, info):
        event = info.get_event()
        if event is not None and event.type == Gst.EventType.EOS:
            GLib.idle_add(self.finish_take)
        return Gst.PadProbeReturn.OK

    def teardown_take(self):
        record_bin, self.record_bin = self.record_bin, None
        pad, self.tee_record_pad = self.tee_record_pad, None
        if record_bin is None or self.pipeline is None:
            return
        record_bin.set_state(Gst.State.NULL)
        self.pipeline.remove(record_bin)
        tee = self.pipeline.get_by_name("t")
        if pad is not None and tee is not None:
            tee.release_request_pad(pad)

    def finish_take(self):
        if self.eos_timeout_id:
            self.cancel_timeout(self.eos_timeout_id)
            self.eos_timeout_id = None
        self.teardown_take()
        if self._closed:
            return False
        self.btn_record.set_sensitive(True)
        self.show_review()
        return False

    def abort_take(self, error_msg):
        self.teardown_take()
        self.is_recording = False
        self.btn_flip.set_sensitive(True)
        self.btn_record.set_icon_name("media-record-symbolic")
        if self.timer_id:
            self.cancel_timeout(self.timer_id)
            self.timer_id = None
        if self._closed:
            return
        self.btn_record.set_sensitive(True)
        logger.error(f"[Camera-Video] Take failed: {error_msg}")
        self.show_error(_("Failed to record video: {error_msg}").format(error_msg=error_msg))

    def force_stop(self):
        logger.warning("[Camera-Video] EOS timeout, forcing stop.")
        self.eos_timeout_id = None
        self.finish_take()
        return False

    def show_review(self):
        if self._closed:
            return
        if self.output_path and os.path.exists(self.output_path):
            size = os.path.getsize(self.output_path)
            logger.info(f"[Camera-Video] Video recorded. Size: {size}")
            if size < MIN_VALID_VIDEO_BYTES:
                self.show_error(_("Recording failed (File empty)."))
                self.on_retake_clicked(None)
                return
            elif size > LARGE_VIDEO_WARN_BYTES:
                self.show_error(_("Large video ({size}KB). Will be compressed on send.").format(size=size // 1024))

        self.btn_light.set_active(False)
        self.stack.set_visible_child_name("review")
        self.present_poster()

    def present_poster(self):
        self.stop_playback()
        if self.build_player():
            self.player.set_state(Gst.State.PAUSED)

    def build_player(self):
        if not self.output_path or not os.path.exists(self.output_path):
            return False
        if os.path.getsize(self.output_path) == 0:
            self.show_error(_("Cannot play empty file."))
            return False
        try:
            self.player = Gst.parse_launch(f"playbin uri=file://{self.output_path}")
            sink = Gst.ElementFactory.make("gtk4paintablesink", "psink")
            self.player.set_property("video-sink", sink)
            paintable = sink.get_property("paintable")
            self.review_widget.set_paintable(paintable)
            self.reveal_on_first_frame(self.review_widget)
            self.player_bus, self.player_bus_handler_id = self.watch_bus(self.player, self.on_player_message)
            return True
        except Exception as e:
            logger.error(f"[Camera-Video] Player build failed: {e}")
            self.show_error(str(e))
            return False

    def on_play_toggle(self, btn):
        if self.player is None:
            if not self.build_player():
                return
            self.player.set_state(Gst.State.PAUSED)
        state = self.player.get_state(0)[1]
        if state == Gst.State.PLAYING:
            self.player.set_state(Gst.State.PAUSED)
            self.btn_play.set_icon_name("media-playback-start-symbolic")
            if self.progress_timer_id:
                self.cancel_timeout(self.progress_timer_id)
                self.progress_timer_id = None
            return
        self.stop_pipeline()
        self.player.set_state(Gst.State.PLAYING)
        self.btn_play.set_icon_name("media-playback-pause-symbolic")
        if self.progress_timer_id is None:
            self.progress_timer_id = self.schedule_timeout(
                PLAYBACK_PROGRESS_INTERVAL_MS, self.update_playback_progress)

    def stop_playback(self):
        if self.progress_timer_id:
            self.cancel_timeout(self.progress_timer_id)
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
        self.lbl_progress.set_label(self.playback_progress_text(0, 0))
        self.progress_bar.set_fraction(0.0)

    def on_player_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            ok_dur, duration = self.player.query_duration(Gst.Format.TIME)
            if ok_dur and duration > 0:
                seconds = duration // Gst.SECOND
                self.lbl_progress.set_label(self.playback_progress_text(seconds, seconds))
                self.progress_bar.set_fraction(1.0)
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.player.set_state(Gst.State.PAUSED)
            if self.progress_timer_id:
                self.cancel_timeout(self.progress_timer_id)
                self.progress_timer_id = None
            self.btn_play.set_icon_name("media-playback-start-symbolic")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"[Camera-Video] Playback error: {err} : {debug}")
            self.stop_playback()

    def restart_viewfinder_safe(self):
        if self._closed:
            return False
        self.btn_record.set_sensitive(True)
        self.start_viewfinder()
        return False

    def on_retake_clicked(self, btn):
        self.stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            try:
                os.remove(self.output_path)
            except Exception as e:
                logger.warning(f"[Camera-Video] Failed to remove temp file: {e}")
        self.output_path = None
        self.lbl_timer.set_label(self.recording_timer_text(0))
        self.stack.set_visible_child_name("capture")
        if self.pipeline is None:
            self.restart_viewfinder_safe()

    def on_attach_clicked(self, btn):
        self.stop_playback()
        if self.output_path and os.path.exists(self.output_path):
            if self.on_attach_callback:
                self._attached = True
                self.on_attach_callback(self.output_path)
        GLib.idle_add(lambda: close_sheet_page(self.get_root()) or False)

    def on_closed(self, _dialog):
        self._closed = True
        self.light_off()
        self.cancel_tracked_timeouts()
        self.stop_pipeline()
        self.stop_playback()
        self.portal.close()
        self.discard_unattached_output()

    def discard_unattached_output(self):
        if self._attached or not self.output_path:
            return
        try:
            if os.path.exists(self.output_path):
                os.remove(self.output_path)
        except Exception as e:
            logger.warning(f"[Camera-Video] Temp file removal failed: {e}")
