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

"""Attachment storage and shrink-to-fit, run by the daemon.

The attachment directory is a data store, so only the daemon writes
it, and a compressed result lands there too: a result parked in /tmp
would be gone after a reboot while a scheduled MMS still points at
it. All functions are blocking and belong on a worker.
"""

import os
import shutil
import tempfile
import time
import mimetypes
from datetime import datetime

from gi.repository import GLib
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.gst_utils import get_gst

IMAGE_COMPRESS_ATTEMPTS = 8
IMAGE_QUALITY_START = 85
IMAGE_QUALITY_FLOOR = 45
IMAGE_QUALITY_STEP = 15
IMAGE_SCALE_STEP = 0.7
VIDEO_TARGET_WIDTH = 320
VIDEO_TARGET_HEIGHT = 240
VIDEO_TARGET_BITRATE_KBPS = 200
VIDEO_COMPRESS_TIMEOUT_SECONDS = 30


def attachments_dir():
    """Return the attachment store directory, creating it when missing."""
    directory = os.path.join(GLib.get_user_data_dir(), "telephony", "attachments")
    os.makedirs(directory, exist_ok=True)
    return directory


def store_attachment(source_path):
    """Copy a file into the attachment store under a timestamped name.

    The stamp carries microseconds because two same-named files can
    arrive in the same second — a prepare directly followed by the
    send-time ownership copy does exactly that.
    """
    fname = os.path.basename(source_path)
    now = datetime.now().strftime("%Y%m%d_%H%M%S%f")
    dest = os.path.join(attachments_dir(), f"{now}_{fname}")
    if source_path != dest:
        if os.path.exists(dest):
            os.remove(dest)
        shutil.copy2(source_path, dest)
    return dest


def own_attachments(paths):
    """Give a message its own copy of each attachment.

    Messages must not share files: a forward reuses the original's
    stored file, and deleting the original would otherwise orphan the
    forward. A source that cannot be copied is kept as it is, so the
    failure surfaces in the send instead of vanishing here.
    """
    owned = []
    for src in paths:
        try:
            owned.append(store_attachment(src))
        except Exception as e:
            logger.warning(f"[Attachments] Ownership copy failed for {src}: {e}")
            owned.append(src)
    return owned


def prepare_attachment(source_path, max_bytes):
    """Store an attachment and make it fit the budget.

    Returns (stored_path, "") on success, or (None, code) where code is
    one of: missing, image-too-large, video-failed, too-large.
    """
    if not source_path or not os.path.exists(source_path):
        return (None, "missing")

    stored = store_attachment(source_path)
    if os.path.getsize(stored) <= max_bytes:
        return (stored, "")

    mime, _encoding = mimetypes.guess_type(stored)
    if mime and mime.startswith("image/"):
        fitted = compress_image(stored, max_bytes)
        failure = "image-too-large"
    elif mime and mime.startswith("video/"):
        fitted = compress_video_to_fit(stored, max_bytes)
        failure = "video-failed"
    else:
        fitted = None
        failure = "too-large"

    os.remove(stored)
    if fitted is None:
        return (None, failure)

    final = store_attachment(fitted)
    os.remove(fitted)
    return (final, "")


def compress_image(src_path, max_bytes):
    """Downscale and re-encode an image until it fits the byte budget."""
    from PIL import Image, ImageOps

    try:
        with Image.open(src_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")

            quality = IMAGE_QUALITY_START
            scale = 1.0
            for attempt in range(IMAGE_COMPRESS_ATTEMPTS):
                width = max(1, int(img.width * scale))
                height = max(1, int(img.height * scale))
                resized = img if scale == 1.0 else img.resize((width, height), Image.LANCZOS)

                tmp_path = os.path.join(tempfile.gettempdir(), f"img_c_{int(time.time())}_{attempt}.jpg")
                resized.save(tmp_path, "JPEG", quality=quality, optimize=True)

                if os.path.getsize(tmp_path) <= max_bytes:
                    return tmp_path

                os.remove(tmp_path)
                if quality > IMAGE_QUALITY_FLOOR:
                    quality -= IMAGE_QUALITY_STEP
                else:
                    scale *= IMAGE_SCALE_STEP
    except Exception as e:
        logger.error(f"[Attachments] Image compression error: {e}")
    return None


def compress_video_to_fit(path, max_bytes):
    """Compress a video and verify it fits the byte budget."""
    compressed = compress_video(path)
    if compressed and os.path.getsize(compressed) <= max_bytes:
        return compressed
    if compressed and os.path.exists(compressed):
        os.remove(compressed)
    return None


def compress_video(src_path):
    """Re-encode a video small; returns the temp result path or None."""
    try:
        Gst = get_gst()
        tmp_path = os.path.join(tempfile.gettempdir(), f"vid_c_{int(time.time())}_{os.path.basename(src_path)}")

        pipeline_str = (
            f"filesrc location=\"{src_path}\" ! decodebin ! videoconvert ! videoscale ! "
            f"video/x-raw,width={VIDEO_TARGET_WIDTH},height={VIDEO_TARGET_HEIGHT} ! videoconvert ! "
            f"x264enc tune=zerolatency bitrate={VIDEO_TARGET_BITRATE_KBPS} speed-preset=ultrafast ! "
            f"h264parse ! mp4mux ! filesink location=\"{tmp_path}\""
        )

        pipeline = Gst.parse_launch(pipeline_str)
        bus = pipeline.get_bus()
        pipeline.set_state(Gst.State.PLAYING)

        msg = bus.timed_pop_filtered(
            VIDEO_COMPRESS_TIMEOUT_SECONDS * Gst.SECOND,
            Gst.MessageType.ERROR | Gst.MessageType.EOS
        )

        pipeline.set_state(Gst.State.NULL)

        if msg and msg.type == Gst.MessageType.EOS:
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                return tmp_path

        logger.warning("[Attachments] Video compression did not finish properly")
        return None
    except Exception as e:
        logger.error(f"[Attachments] Video compression error: {e}")
        return None
