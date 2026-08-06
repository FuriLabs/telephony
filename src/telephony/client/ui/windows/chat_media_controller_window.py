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
from datetime import datetime
from gi.repository import Gtk, Gdk, GLib
from telephony.shared.utils.log_utils import logger
from gettext import gettext as _

from telephony.client.ui.widgets.common_widget import present_choice_sheet, add_choice_row
from telephony.client.ui.windows.camera_photo_window import CameraPhoto
from telephony.client.ui.windows.camera_video_window import CameraVideo
from telephony.client.ui.windows.sound_recorder_window import SoundRecorder

URI_LIST_MIME = "text/uri-list"
IMAGE_PASTE_MIMES = ("image/png", "image/jpeg")


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
            if self._clipboard_has_media():
                add_choice_row(group, sheet, _("Paste"), self.ingest_clipboard,
                               icon="edit-paste-symbolic")
            add_choice_row(group, sheet, _("Schedule"), self.chat_page._open_schedule_picker,
                           icon="alarm-symbolic")

        present_choice_sheet(self.window, _("Select Attachment"), build)

    def _clipboard_has_media(self):
        """Return whether the clipboard offers files or an image."""
        formats = self.window.get_clipboard().get_formats()
        if formats.contain_mime_type(URI_LIST_MIME) or formats.contain_gtype(Gdk.FileList):
            return True
        if formats.contain_gtype(Gdk.Texture):
            return True
        return any(formats.contain_mime_type(m) for m in IMAGE_PASTE_MIMES)

    def ingest_clipboard(self):
        """Attach clipboard files or a clipboard image; return whether media was found."""
        clipboard = self.window.get_clipboard()
        formats = clipboard.get_formats()
        if formats.contain_mime_type(URI_LIST_MIME) or formats.contain_gtype(Gdk.FileList):
            clipboard.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None,
                                       self._on_clipboard_files)
            return True
        if formats.contain_gtype(Gdk.Texture) or any(formats.contain_mime_type(m) for m in IMAGE_PASTE_MIMES):
            clipboard.read_texture_async(None, self._on_clipboard_texture)
            return True
        return False

    def _on_clipboard_files(self, clipboard, result):
        """Feed pasted files through the standard attachment pipeline."""
        try:
            file_list = clipboard.read_value_finish(result)
        except GLib.Error as e:
            logger.warning(f"[MediaController] Clipboard file read failed: {e}")
            return
        for file in file_list.get_files():
            path = file.get_path()
            if path:
                self._on_media_captured(None, path)
            else:
                logger.debug(f"[MediaController] Skipping non-local clipboard file: {file.get_uri()}")

    def _on_clipboard_texture(self, clipboard, result):
        """Persist a pasted image and feed it through the attachment pipeline."""
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as e:
            logger.warning(f"[MediaController] Clipboard image read failed: {e}")
            return
        if texture is None:
            return
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(GLib.get_tmp_dir(), f"pasted_{now}.png")
        if not texture.save_to_png(path):
            logger.error(f"[MediaController] Saving pasted image to {path} failed")
            return
        self._on_media_captured(None, path)

    def _open_camera(self):
        """Open photo camera modal."""
        try:
            cam = CameraPhoto(self.window, lambda path: self._on_media_captured(None, path))
            cam.present(self.window)
        except Exception as e:
            logger.error(f"Failed to open camera: {e}")
            self._show_error(None, _("Could not start the camera."))

    def _open_video(self):
        """Open video camera modal."""
        try:
            cam = CameraVideo(self.window, lambda path: self._on_media_captured(None, path))
            cam.present(self.window)
        except Exception as e:
            logger.error(f"Failed to open video camera: {e}")
            self._show_error(None, _("Could not start video recording."))

    def _open_audio_recorder(self):
        """Open audio recorder modal."""
        try:
            max_bytes = self.chat_page._remaining_attachment_budget()
            rec = SoundRecorder(self.window, lambda path: self._on_media_captured(None, path),
                                max_bytes=max_bytes)
            rec.present(self.window)
        except Exception as e:
            logger.error(f"Failed to open audio recorder: {e}")
            self._show_error(None, _("Could not start audio recorder."))

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
        """Hand captured or selected media to the chat page.

        Storage and shrink-to-fit happen in the daemon, so the file is
        forwarded as it is.
        """
        if not path or not os.path.exists(path):
            return
        self.chat_page.on_attachment_captured(path)

    def _show_error(self, title, message):
        """Report a failure the user can only acknowledge."""
        self.window.notify_error(message)
