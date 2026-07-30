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
import pulsectl
from loguru import logger
from gi.repository import GObject, GLib, Gio, Gst

DEFAULT_TONE = "/usr/share/sounds/freedesktop/stereo/phone-outgoing-calling.oga"
LOOP_DELAY_MS = 1500


class RingbackManager(GObject.Object):
    """
    Manages ringback tone generation for outgoing calls using GStreamer playbin and PulseAudio.
    """

    def __init__(self, ofono_manager, gsettings_mgr=None):
        """Initialize the Ringback Manager from the ofono manager's call signals."""
        super().__init__()
        self.ofono = ofono_manager
        self.gsettings_mgr = gsettings_mgr

        self.pipeline = None
        self.current_call_path = None
        self.active_sound_file = None
        self.loop_timer_id = None

        self.pulse_client = None
        self.sink_module_id = None
        self.loopback_module_id = None
        self.modules_loaded = False

        try:
            if not Gst.is_initialized():
                Gst.init(None)
                logger.info("[RingbackManager] GStreamer initialized.")
        except Exception as e:
            logger.warning(f"[RingbackManager] GStreamer init warning: {e}")

        self.session_bus = None
        try:
            self.session_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.error(f"[RingbackManager] Session bus init failed: {e}")

        self.ofono.connect('call-added', self._on_call_added)
        self.ofono.connect('call-changed', self._on_call_changed)
        self.ofono.connect('call-removed', self._on_call_removed)
        self.ofono.connect('connection-status', self._on_connection_status)

        logger.info("[RingbackManager] Initialized successfully.")

    def _on_connection_status(self, _manager, status, _msg):
        """Stop ringback when the modem disappears."""
        if status in ("offline", "error"):
            self._stop_ringback()
            self.current_call_path = None

    def _on_call_added(self, _manager, path, data):
        """Handle new call creation and check if ringback is needed."""
        self._pause_mpris_players()
        state = data.get('state', '')
        if data.get('direction') != 'outgoing' or state not in ("dialing", "alerting"):
            return

        logger.info(f"[RingbackManager] Outgoing call detected: {path} (State: {state})")
        self.current_call_path = path
        self._check_call_state(state)

    def _on_call_changed(self, _manager, path, state):
        """Monitor call state changes to trigger or stop ringback."""
        if path != self.current_call_path:
            return
        logger.debug(f"[RingbackManager] Call state changed: {state}")
        self._check_call_state(state)

    def _on_call_removed(self, _manager, path):
        """Handle call removal and stop ringback if active."""
        if path != self.current_call_path:
            return
        logger.info(f"[RingbackManager] Call removed: {path}. Stopping ringback.")
        self._stop_ringback()
        self.current_call_path = None

    def _check_call_state(self, state):
        """Evaluate call state to manage ringback lifecycle."""
        if state in ["dialing", "alerting"]:
            self._start_ringback()
        elif state in ["active", "disconnected", "incoming"]:
            logger.info(f"[RingbackManager] Call state '{state}' requires stopping ringback.")
            self._stop_ringback()

    def _ensure_pulse_modules_loaded(self):
        """Load PulseAudio modules if not already loaded."""
        try:
            if self.modules_loaded and self.pulse_client:
                return True

            logger.info("[RingbackManager] Loading PulseAudio modules...")
            self.pulse_client = pulsectl.Pulse('telephony-ringback')

            mod1 = self.pulse_client.module_load("module-null-sink", "sink_name=call_injector")
            self.sink_module_id = int(mod1)
            logger.info(f"[RingbackManager] Loaded null-sink: {self.sink_module_id}")

            mod2 = self.pulse_client.module_load("module-loopback", "source=call_injector.monitor sink=sink.primary_output")
            self.loopback_module_id = int(mod2)
            logger.info(f"[RingbackManager] Loaded loopback: {self.loopback_module_id}")

            self.modules_loaded = True
            return True
        except Exception as e:
            logger.error(f"[RingbackManager] Failed to load pulse modules: {e}")
            self._ensure_pulse_modules_unloaded()
            return False

    def _ensure_pulse_modules_unloaded(self):
        """Unload PulseAudio modules if loaded."""
        try:
            if self.pulse_client:
                if self.loopback_module_id is not None:
                    try:
                        self.pulse_client.module_unload(self.loopback_module_id)
                        logger.info(f"[RingbackManager] Unloaded loopback module: {self.loopback_module_id}")
                    except Exception as e:
                        logger.warning(f"[RingbackManager] Unload loopback failed: {e}")
                    self.loopback_module_id = None

                if self.sink_module_id is not None:
                    try:
                        self.pulse_client.module_unload(self.sink_module_id)
                        logger.info(f"[RingbackManager] Unloaded sink module: {self.sink_module_id}")
                    except Exception as e:
                        logger.warning(f"[RingbackManager] Unload sink failed: {e}")
                    self.sink_module_id = None

                self.pulse_client.close()
                self.pulse_client = None

            self.modules_loaded = False
        except Exception as e:
            logger.error(f"[RingbackManager] Unload pulse modules error: {e}")

    def _start_ringback(self):
        """Start the ringback tone playback using playbin."""
        enabled_str = self.gsettings_mgr.get_setting("ringback_enabled")
        if enabled_str != "true":
            logger.info("[RingbackManager] Ringback disabled in settings.")
            return

        if not self._ensure_pulse_modules_loaded():
            logger.error("[RingbackManager] Cannot start ringback: Pulse modules failed.")
            return

        if self.pipeline:
            _, state, _ = self.pipeline.get_state(0)
            if state in [Gst.State.PLAYING, Gst.State.PAUSED]:
                return

        custom_file = self.gsettings_mgr.get_setting("ringback_custom_file")

        self.active_sound_file = DEFAULT_TONE
        if custom_file and os.path.exists(custom_file):
            self.active_sound_file = custom_file

        logger.info(f"[RingbackManager] Starting playback for: {self.active_sound_file}")

        try:
            self.pipeline = Gst.ElementFactory.make("playbin", "ringback-player")
            if not self.pipeline:
                logger.error("[RingbackManager] Failed to create playbin element.")
                self._stop_ringback()
                return

            pulse_sink = Gst.ElementFactory.make("pulsesink", "ringback-sink")
            if not pulse_sink:
                logger.error("[RingbackManager] Failed to create pulsesink element.")
                self._stop_ringback()
                return

            pulse_sink.set_property("device", "call_injector")
            self.pipeline.set_property("audio-sink", pulse_sink)

            uri = f"file://{self.active_sound_file}"
            self.pipeline.set_property("uri", uri)

            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)
            bus.connect("message::error", self._on_error)

            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                logger.error("[RingbackManager] Failed to set pipeline to PLAYING.")
                self._stop_ringback()
            else:
                logger.info("[RingbackManager] Pipeline started successfully.")

        except Exception as e:
            logger.error(f"[RingbackManager] Start ringback error: {e}")
            self._stop_ringback()

    def _stop_ringback(self):
        """Stop ringback playback and unload modules."""
        if self.loop_timer_id:
            GLib.source_remove(self.loop_timer_id)
            self.loop_timer_id = None

        if self.pipeline:
            logger.info("[RingbackManager] Stopping pipeline.")
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None

        self._ensure_pulse_modules_unloaded()

    def _on_eos(self, bus, msg):
        """Handle End of Stream by pausing and scheduling a delayed restart."""
        logger.info("[RingbackManager] EOS reached. Pausing for loop delay.")
        if self.pipeline:
            self.pipeline.set_state(Gst.State.PAUSED)
            self.loop_timer_id = GLib.timeout_add(LOOP_DELAY_MS, self._restart_loop)

    def _restart_loop(self):
        """Restart playback from the beginning after loop delay."""
        self.loop_timer_id = None
        if self.pipeline:
            logger.debug("[RingbackManager] Restarting loop...")
            if not self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0):
                logger.warning("[RingbackManager] Seek failed during loop restart.")
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline.set_state(Gst.State.PLAYING)
            else:
                self.pipeline.set_state(Gst.State.PLAYING)
        return False

    def _on_error(self, bus, msg):
        """Handle GStreamer errors."""
        err, debug = msg.parse_error()
        logger.error(f"[RingbackManager] Pipeline error: {err} - {debug}")
        self._stop_ringback()

    def _pause_mpris_players(self):
        """Pause any active media players via DBus."""
        try:
            proxy = Gio.DBusProxy.new_sync(
                self.session_bus, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", None)
            names = proxy.call_sync("ListNames", None, Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
            for name in names:
                if name.startswith("org.mpris.MediaPlayer2"):
                    self._send_pause(name)
        except Exception as e:
            logger.warning(f"[RingbackManager] Pause MPRIS warning: {e}")

    def _send_pause(self, bus_name):
        """Send pause command to a specific MPRIS player."""
        try:
            player = Gio.DBusProxy.new_sync(
                self.session_bus, Gio.DBusProxyFlags.NONE, None,
                bus_name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", None)
            player.call_sync("Pause", None, Gio.DBusCallFlags.NONE, -1, None)
            logger.info(f"[RingbackManager] Paused player: {bus_name}")
        except Exception as e:
            logger.warning(f"[RingbackManager] Pause player {bus_name} failed: {e}")
