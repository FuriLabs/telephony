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
import shutil
from datetime import datetime
from gi.repository import Gtk, Adw, GLib
from telephony.backend.utils.log_utils import logger
from gettext import gettext as _

from ..widgets.common_widget import present_choice_sheet, add_choice_row
from .camera_photo_window import CameraPhoto
from .camera_video_window import CameraVideo
from .sound_recorder_window import SoundRecorder


class ChatMediaController:
    """Handles media attachment UI flows (Camera, Audio, File Selection) for ChatPage."""

    def __init__(self, chat_page, window):
        self.chat_page = chat_page
        self.window = window

    def on_attach_clicked(self, btn):
        """Show the attachment chooser sheet."""
        def build(group, sheet):
            add_choice_row(group, sheet, _("Take Photo"), self._open_camera,
                           icon="camera-photo-symbolic")
            add_choice_row(group, sheet, _("Record Video"), self._open_video,
                           icon="camera-video-symbolic")
            add_choice_row(group, sheet, _("Record Audio"), self._open_audio_recorder,
                           icon="audio-input-microphone-symbolic")
            add_choice_row(group, sheet, _("Choose File"), self._open_file_chooser,
                           icon="folder-open-symbolic")
            add_choice_row(group, sheet, _("Schedule"), self.chat_page._open_schedule_picker,
                           icon="alarm-symbolic")

        present_choice_sheet(self.window, _("Select Attachment"), build)

    def _open_camera(self):
        """Open photo camera modal."""
        try:
            cam = CameraPhoto(self.window, lambda path: self._on_media_captured(None, path))
            cam.present(self.window)
        except Exception as e:
            logger.error(f"Failed to open camera: {e}")
            self._show_error(_("Camera Error"), _("Could not start the camera."))

    def _open_video(self):
        """Open video camera modal."""
        try:
            cam = CameraVideo(self.window, lambda path: self._on_media_captured(None, path))
            cam.present(self.window)
        except Exception as e:
            logger.error(f"Failed to open video camera: {e}")
            self._show_error(_("Camera Error"), _("Could not start video recording."))

    def _open_audio_recorder(self):
        """Open audio recorder modal."""
        try:
            max_bytes = self.chat_page._remaining_attachment_budget()
            rec = SoundRecorder(self.window, lambda path: self._on_media_captured(None, path),
                                max_bytes=max_bytes)
            rec.present(self.window)
        except Exception as e:
            logger.error(f"Failed to open audio recorder: {e}")
            self._show_error(_("Audio Error"), _("Could not start audio recorder."))

    def _open_file_chooser(self):
        """Open standard file chooser for attachments."""
        dialog = Gtk.FileDialog(title=_("Select Attachment"))
        dialog.set_modal(True)

        def _on_resp(d, r):
            try:
                file = d.open_finish(r)
                if file:
                    self._on_media_captured(None, file.get_path())
            except GLib.Error as e:
                if e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    logger.debug("[MediaController] File dialog dismissed by user")
                else:
                    logger.error(f"File dialog error: {e}")
            except Exception as e:
                logger.error(f"File dialog error: {e}")

        dialog.open(self.window, None, _on_resp)

    def _on_media_captured(self, _source, path):
        """Handle captured/selected media file and pass to ChatPage."""
        if not path or not os.path.exists(path):
            return

        try:
            att_dir = os.path.join(GLib.get_user_data_dir(), "telephony", "attachments")
            os.makedirs(att_dir, exist_ok=True)

            fname = os.path.basename(path)
            now = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = os.path.join(att_dir, f"{now}_{fname}")

            if path != dest:
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.copy2(path, dest)

            if "/tmp/" in path:
                try:
                    os.remove(path)
                except Exception as e:
                    logger.debug(f"[MediaController] Temp file removal failed: {e}")

            self.chat_page.on_attachment_captured(dest)

        except Exception as e:
            logger.error(f"Failed to process attachment: {e}")
            self._show_error(_("Attachment Error"), _("Failed to prepare attachment."))

    def _show_error(self, title, message):
        """Show error dialog."""
        d = Adw.AlertDialog(heading=title, body=message)
        d.add_response("close", _("Close"))
        d.present(self.window)
