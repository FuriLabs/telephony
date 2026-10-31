# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dw5

import array
import os
import re
import signal
import subprocess
import time
import wave
from gettext import gettext as _

from gi.repository import GLib, GObject

from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.thread_utils import run_in_background

# The far end leaves the modem on PCM_2_CAP. Routing it into UL6 exposes it on
# Capture_6, independently of the PulseAudio mic source used for the near end.
FAR_ROUTES = ("UL6_CH1 PCM_2_CAP_CH1", "UL6_CH2 PCM_2_CAP_CH1")
FAR_DEVICE = "hw:0,15"
MIC_SOURCE = "audio-manager-input"
SAMPLE_RATE = 48000
MIC_GAIN = 4
MERGE_CHUNK_FRAMES = 48000
MIXER_TIMEOUT_SECONDS = 2
STOP_TIMEOUT_SECONDS = 5
MONITOR_INTERVAL_SECONDS = 2
ENGAGED_STATES = ("dialing", "alerting", "active", "held")
CURRENT_STATES = ("dialing", "alerting", "active")

# Asks for a signal if the daemon dies, then checks the daemon is still the parent,
# because a parent that died before that request would never send one.
ORPHAN_GUARD = ["setpriv", "--pdeathsig", "INT", "sh", "-c",
                '[ "$PPID" = "$1" ] || exit 1; shift; exec "$@"', "capture"]

FORMAT_STEREO = "stereo"
FORMAT_MONO = "mono"
FORMAT_SEPARATE = "separate"


def set_far_routes(state):
    ok = True
    for name in FAR_ROUTES:
        try:
            result = subprocess.run(["amixer", "-c0", "-q", "cset", f"name={name}", state],
                                    capture_output=True, text=True,
                                    timeout=MIXER_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error(f"[CallRecording] Mixer {name} {state}: {e}")
            ok = False
            continue
        if result.returncode != 0:
            logger.error(f"[CallRecording] Mixer {name} {state}: {result.stderr.strip()}")
            ok = False
    return ok


def clamp(sample):
    return max(-32768, min(32767, sample))


def merge_recordings(far_path, mic_path, out_path, output_format, mic_gain=MIC_GAIN):
    """Combine both sides: mono mixes them, stereo puts the far end left and the near end right."""
    stereo = output_format == FORMAT_STEREO
    with wave.open(far_path) as far, wave.open(mic_path) as mic:
        far_channels = far.getnchannels()
        if far.getframerate() != mic.getframerate():
            raise ValueError("Sample rates differ")
        with wave.open(out_path, "wb") as out:
            out.setnchannels(2 if stereo else 1)
            out.setsampwidth(2)
            out.setframerate(far.getframerate())
            while True:
                far_data = far.readframes(MERGE_CHUNK_FRAMES)
                mic_data = mic.readframes(MERGE_CHUNK_FRAMES)
                if not far_data and not mic_data:
                    break
                left = array.array("h", far_data)[::far_channels]
                right = array.array("h", mic_data)
                frames = max(len(left), len(right))
                if stereo:
                    block = array.array("h", bytes(frames * 4))
                    for i, sample in enumerate(left):
                        block[2 * i] = sample
                    for i, sample in enumerate(right):
                        block[2 * i + 1] = clamp(sample * mic_gain)
                else:
                    mixed = [0] * frames
                    for i, sample in enumerate(left):
                        mixed[i] = sample
                    for i, sample in enumerate(right):
                        mixed[i] += sample * mic_gain
                    block = array.array("h", [clamp(sample) for sample in mixed])
                out.writeframes(block.tobytes())


def release_capture(procs):
    """Runs off the main thread, so the capture device and routes are free when it returns."""
    for proc in procs:
        try:
            proc.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    set_far_routes("off")


def finish_recording(far_path, mic_path, out_path, output_format):
    """Runs off the main thread. The separate files are only removed after a good merge."""
    if output_format == FORMAT_SEPARATE:
        return
    try:
        merge_recordings(far_path, mic_path, out_path, output_format)
    except (OSError, EOFError, ValueError, wave.Error) as e:
        logger.error(f"[CallRecording] Merge failed, keeping separate files: {e}")
        try:
            os.unlink(out_path)
        except OSError:
            pass
        return
    os.unlink(far_path)
    os.unlink(mic_path)


class CallRecordingManager(GObject.Object):

    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, ofono, gsettings_mgr):
        super().__init__()
        self.ofono = ofono
        self.gsettings_mgr = gsettings_mgr
        self.procs = []
        self.far_path = None
        self.mic_path = None
        self.merged_path = None
        self.monitor_id = 0
        self.capture_busy = False
        self.start_pending = False
        # Calls in the current recording. A recording never spans unrelated calls.
        self.recorded_calls = set()
        # Calls where recording was stopped, which "always record" must not restart.
        self.suppressed_calls = set()
        self.available = self.detect_available()
        if self.available:
            # Clears routes left on if the daemon previously died while recording.
            set_far_routes("off")
        self.ofono.connect("call-added", self.on_calls_changed)
        self.ofono.connect("call-changed", self.on_calls_changed)
        self.ofono.connect("call-removed", self.on_calls_changed)

    @property
    def active(self):
        return bool(self.procs)

    def detect_available(self):
        try:
            with open("/proc/asound/pcm") as pcm:
                if "Hostless_Speech" not in pcm.read():
                    return False
            return all(subprocess.run(["amixer", "-c0", "cget", f"name={name}"],
                                      capture_output=True,
                                      timeout=MIXER_TIMEOUT_SECONDS).returncode == 0
                       for name in FAR_ROUTES)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.info(f"[CallRecording] Not available: {e}")
            return False

    def engaged_calls(self):
        return {path: data for path, data in self.ofono.active_calls.items()
                if data.get("state") in ENGAGED_STATES and not data.get("disconnect_reason")}

    def current_calls(self, engaged):
        return {path for path, data in engaged.items() if data.get("state") in CURRENT_STATES}

    def on_calls_changed(self, *_args):
        engaged = self.engaged_calls()
        self.suppressed_calls &= set(engaged)
        if not engaged:
            self.start_pending = False
        if self.active:
            self.update_recorded_calls(engaged)
        if (self.available and not self.active
                and any(data.get("state") == "active" and path not in self.suppressed_calls
                        for path, data in engaged.items())
                and self.gsettings_mgr.gsettings.get_boolean("call-recording-always")):
            self.start()

    def update_recorded_calls(self, engaged):
        current = self.current_calls(engaged)
        if not self.recorded_calls & set(engaged):
            self.stop()
        elif current and not self.recorded_calls & current:
            # Another call took over while the recorded one is on hold.
            self.stop()
        elif any(engaged[path].get("multiparty") for path in self.recorded_calls & current):
            # A conference is one conversation. Two active calls alone aren't: that
            # also happens briefly during a swap, before the old call reports held.
            self.recorded_calls |= {path for path in current if engaged[path].get("multiparty")}

    def stop_by_user(self):
        self.suppressed_calls |= self.recorded_calls
        self.start_pending = False
        self.stop()

    def output_directory(self):
        base = (GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
                or os.path.join(GLib.get_home_dir(), "Music"))
        directory = os.path.join(base, "Call Recordings")
        os.makedirs(directory, mode=0o700, exist_ok=True)
        return directory

    def choose_paths(self, directory, number):
        base = os.path.join(directory, f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{number}")
        stem = base
        attempt = 1
        while any(os.path.exists(stem + suffix)
                  for suffix in ("_caller.wav", "_me.wav", ".wav")):
            attempt += 1
            stem = f"{base}_{attempt}"
        self.far_path = f"{stem}_caller.wav"
        self.mic_path = f"{stem}_me.wav"
        self.merged_path = f"{stem}.wav"

    def spawn(self, command):
        self.procs.append(subprocess.Popen(ORPHAN_GUARD + [str(os.getpid())] + command))

    def start(self):
        if self.active:
            return True
        calls = self.engaged_calls()
        if not self.available or not calls:
            return False
        if self.capture_busy:
            self.start_pending = True
            return True

        try:
            directory = self.output_directory()
        except OSError as e:
            logger.error(f"[CallRecording] Output directory: {e}")
            self.ofono.emit("action-error", _("Could not start recording"))
            return False

        self.recorded_calls = self.current_calls(calls) or set(calls)
        number = calls[min(self.recorded_calls)].get("number") or ""
        self.choose_paths(directory, re.sub(r"[^0-9+]", "", number) or "unknown")

        if not set_far_routes("on"):
            set_far_routes("off")
            self.ofono.emit("action-error", _("Could not start recording"))
            return False

        previous_umask = os.umask(0o077)
        try:
            self.spawn(["arecord", "-q", "-D", FAR_DEVICE, "-f", "S16_LE",
                        "-r", str(SAMPLE_RATE), "-c", "2", self.far_path])
            self.spawn(["parecord", f"--device={MIC_SOURCE}", "--file-format=wav",
                        "--format=s16le", f"--rate={SAMPLE_RATE}", "--channels=1",
                        self.mic_path])
        except OSError as e:
            logger.error(f"[CallRecording] Start failed: {e}")
            self.stop()
            self.ofono.emit("action-error", _("Could not start recording"))
            return False
        finally:
            os.umask(previous_umask)

        self.monitor_id = GLib.timeout_add_seconds(MONITOR_INTERVAL_SECONDS, self.monitor)
        logger.info(f"[CallRecording] Recording to {self.merged_path}")
        self.emit("state-changed", True)
        return True

    def monitor(self):
        failed = [proc for proc in self.procs if proc.poll() is not None]
        if not failed:
            return GLib.SOURCE_CONTINUE
        for proc in failed:
            logger.error(f"[CallRecording] {proc.args[len(ORPHAN_GUARD) + 1]} "
                         f"exited with {proc.returncode}")
        self.monitor_id = 0
        self.suppressed_calls |= self.recorded_calls
        self.stop()
        self.ofono.emit("action-error", _("Call recording stopped unexpectedly"))
        return GLib.SOURCE_REMOVE

    def stop(self):
        if not self.active:
            return
        if self.monitor_id:
            GLib.source_remove(self.monitor_id)
            self.monitor_id = 0
        procs = self.procs
        self.procs = []
        self.recorded_calls = set()
        for proc in procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        logger.info("[CallRecording] Stopped")
        self.emit("state-changed", False)
        paths = (self.far_path, self.mic_path, self.merged_path)
        self.capture_busy = True

        def released(*_args):
            self.on_capture_released(paths)

        run_in_background(release_capture, procs, on_complete=released, on_error=released)

    def on_capture_released(self, paths):
        self.capture_busy = False
        output_format = self.gsettings_mgr.gsettings.get_string("call-recording-format")
        run_in_background(finish_recording, *paths, output_format,
                          on_error=lambda e: logger.error(f"[CallRecording] Finish failed: {e}"))
        if self.start_pending:
            self.start_pending = False
            self.start()
        else:
            self.on_calls_changed()
