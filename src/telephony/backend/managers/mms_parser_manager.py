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
import magic
import mimetypes
import tempfile
import time
import random
from gi.repository import GLib
from ..utils.phone_utils import get_own_number, normalize_number

import re
from loguru import logger


class MmsParserManager:
    def _parse_and_store(self, path, props):
        """Parse message content and attachments, and store in database."""
        try:
            sender_var = props.get('Sender', None)
            sender = sender_var if sender_var else "Unknown"
            if isinstance(sender, GLib.Variant):
                sender = sender.unpack()

            rec_var = props.get('Recipients', [])
            recipients = rec_var
            if isinstance(rec_var, GLib.Variant):
                recipients = rec_var.unpack()

            date = props.get('Date', '')
            if isinstance(date, GLib.Variant):
                date = date.unpack()

            attachments = props.get('Attachments', [])
            if isinstance(attachments, GLib.Variant):
                attachments = attachments.unpack()

            final_atts = []
            body_text = ""
            processed_paths = set()

            self._log("PARSING-START", f"Attachments found: {len(attachments)}")

            for att in attachments:
                if len(att) >= 3:
                    mime, src_path = str(att[1]), att[2]
                    processed_paths.add(src_path)

                    real_mime = self._detect_mime(src_path)
                    is_file_only = any(x in real_mime.lower() for x in ["vcard", "smil", "shellscript", "python", "javascript"])

                    if real_mime == "text/plain" and not is_file_only:
                        content = self._read_text_safely(src_path)
                        if content and not body_text:
                            body_text = content
                            self._log("TEXT-FOUND", f"Body extracted ({len(content)} chars)")
                    elif "smil" not in real_mime.lower():
                        dest = self._sanitize_and_store(src_path, mime)
                        if dest:
                            final_atts.append(dest)
                            self._log("FILE-SAVED", f"MIME: {real_mime} -> {os.path.basename(dest)}")

            uuid = path.split('/')[-1]
            if os.path.exists(self.mmsd_storage_dir):
                for fname in os.listdir(self.mmsd_storage_dir):
                    if fname.startswith(uuid) and ".attachment." in fname:
                        full_path = os.path.join(self.mmsd_storage_dir, fname)
                        if full_path not in processed_paths:
                            self._log("GHOST-FOUND", f"Orphan file: {fname}")
                            mime = self._detect_mime(full_path)
                            if "smil" in mime.lower():
                                continue
                            is_file_only = any(x in mime.lower() for x in ["vcard", "shellscript", "python"])
                            if mime == "text/plain" and not is_file_only:
                                content = self._read_text_safely(full_path)
                                if content and not body_text:
                                    body_text = content
                            else:
                                dest = self._sanitize_and_store(full_path, mime)
                                if dest:
                                    final_atts.append(dest)

            if not final_atts and not body_text:
                self._log("CARVER-TRIGGER", "No reported attachments. Attempting raw PDU carving.")
                raw_pdu_path = os.path.join(self.mmsd_storage_dir, uuid)
                if os.path.exists(raw_pdu_path):
                    carved = self._carve_images_from_pdu(raw_pdu_path, uuid)
                    for c_path in carved:
                        final_atts.append(c_path)
                        self._log("CARVE-SUCCESS", os.path.basename(c_path))

            self._log("PARSING-COMPLETE", f"Total Attachments Saved: {len(final_atts)}")

            sender_name = sender
            if self.eds and sender != "Unknown":
                name = self.eds.get_contact_name(sender)
                if name:
                    sender_name = name

            own_number = get_own_number()
            if not own_number and self.db:
                own_number = self.gsettings_mgr.get_setting("own_number") if self.gsettings_mgr else ""

            valid_recipients = [r for r in recipients if r and r.strip()]
            participants = set(valid_recipients)

            if sender != "Unknown":
                participants.add(sender)

            if own_number:
                norm_own = normalize_number(own_number)
                if own_number in participants:
                    participants.remove(own_number)
                if norm_own in participants:
                    participants.remove(norm_own)

            clean_list = sorted([normalize_number(p) for p in participants if p])

            if len(clean_list) == 0:
                saved_remote_number = sender
            elif len(clean_list) == 1:
                saved_remote_number = clean_list[0]
            else:
                saved_remote_number = clean_list

            if self.db:
                self._log("DB-SAVE", "Saving incoming MMS to database...")
                self.db.add_message(
                    remote_number=saved_remote_number,
                    direction="incoming",
                    body=body_text,
                    status="unread",
                    subject=None,
                    attachments=final_atts,
                    sender=sender
                )

            self.emit('message-received', sender, recipients, str(date), body_text, final_atts, sender_name)

        except Exception as e:
            self._log("PARSE-ERROR", str(e))

    def _read_text_safely(self, path):
        """Safely read text content from a file."""
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        except Exception as e:
            self._log("READ-TEXT-FAIL", str(e))
        return None

    def _detect_mime(self, path):
        """Detect MIME type of a file using python-magic."""
        try:
            mime = magic.from_file(path, mime=True)
            if mime and mime in ["text/html", "text/plain", "text/xml", "application/xml"]:
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        header = f.read(1024)
                        if "<smil" in header.lower():
                            return "application/smil+xml"
                except Exception as e:
                    logger.warning(f"[MMSManager] Failed to sniff content for SMIL: {e}")

            if mime:
                return mime
        except Exception as e:
            self._log("MIME-DETECT-FAIL", str(e))
        return "application/octet-stream"

    def _sanitize_and_store(self, src_path, mime_hint=None):
        """Sanitize filename and store attachment in local storage."""
        try:
            if not os.path.exists(src_path):
                return None
            detected = self._detect_mime(src_path)
            final_mime = detected if detected != "application/octet-stream" else (mime_hint or detected)

            if not mimetypes.inited:
                mimetypes.init()

            ext = mimetypes.guess_extension(final_mime)

            manual_map = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                "image/bmp": ".bmp", "image/tiff": ".tiff", "image/webp": ".webp",
                "image/heic": ".heic", "image/heif": ".heif", "image/jp2": ".jp2",
                "image/svg+xml": ".svg", "image/x-icon": ".ico",

                "video/mp4": ".mp4", "video/x-matroska": ".mkv", "video/webm": ".webm",
                "video/x-msvideo": ".avi", "video/quicktime": ".mov",
                "video/3gpp": ".3gp", "video/3gpp2": ".3g2",
                "video/x-ms-wmv": ".wmv", "video/mpeg": ".mpg", "video/x-flv": ".flv",

                "audio/mpeg": ".mp3", "audio/flac": ".flac", "audio/x-wav": ".wav",
                "audio/wav": ".wav", "audio/ogg": ".ogg", "audio/amr": ".amr",
                "audio/mp4": ".m4a", "audio/aac": ".aac", "audio/x-m4a": ".m4a",
                "audio/midi": ".mid", "audio/x-matroska": ".mka",

                "application/pdf": ".pdf", "application/rtf": ".rtf",
                "application/msword": ".doc", "application/vnd.ms-excel": ".xls",
                "application/vnd.ms-powerpoint": ".ppt",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                "application/vnd.oasis.opendocument.text": ".odt",
                "application/vnd.oasis.opendocument.spreadsheet": ".ods",
                "application/vnd.oasis.opendocument.presentation": ".odp",
                "text/plain": ".txt", "text/csv": ".csv", "text/x-log": ".log",
                "text/vcard": ".vcf", "text/calendar": ".ics", "text/html": ".html",
                "application/json": ".json", "text/yaml": ".yaml", "text/xml": ".xml",
                "text/x-python": ".py", "text/x-shellscript": ".sh",

                "application/zip": ".zip", "application/x-tar": ".tar",
                "application/gzip": ".tar.gz", "application/x-bzip2": ".tar.bz2",
                "application/x-xz": ".tar.xz", "application/x-rar-compressed": ".rar",
                "application/x-7z-compressed": ".7z",
                "application/vnd.debian.binary-package": ".deb",
                "application/x-debian-package": ".deb", "application/vnd.android.package-archive": ".apk",
                "application/java-archive": ".jar", "application/smil+xml": ".smil"
            }
            if final_mime in manual_map:
                ext = manual_map[final_mime]
            if not ext:
                ext = ".bin"

            new_name = f"mms_{int(time.time())}_{random.randint(1000, 9999)}{ext}"
            dest_path = os.path.join(self.local_att_dir, new_name)
            shutil.copy2(src_path, dest_path)

            fd = os.open(dest_path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return dest_path
        except Exception as e:
            self._log("SANITIZE-STORE-FAIL", str(e))
            return None

    def _carve_images_from_pdu(self, pdu_path, uuid):
        """Extract images from raw PDU if needed."""
        carved_files = []
        try:
            with open(pdu_path, 'rb') as f:
                data = f.read()
            jpeg_pattern = re.compile(b'\xff\xd8.+?\xff\xd9', re.DOTALL)
            for i, match in enumerate(jpeg_pattern.finditer(data)):
                if len(match.group(0)) > 2048:
                    with tempfile.NamedTemporaryFile(delete=False) as tf:
                        tf.write(match.group(0))
                        tf_path = tf.name
                    final_path = self._sanitize_and_store(tf_path, "image/jpeg")
                    if final_path:
                        carved_files.append(final_path)
                    os.remove(tf_path)
            png_pattern = re.compile(b'\x89PNG\r\n\x1a\n.+?IEND\xae\x42\x60\x82', re.DOTALL)
            for i, match in enumerate(png_pattern.finditer(data)):
                with tempfile.NamedTemporaryFile(delete=False) as tf:
                    tf.write(match.group(0))
                    tf_path = tf.name
                final_path = self._sanitize_and_store(tf_path, "image/png")
                if final_path:
                    carved_files.append(final_path)
                os.remove(tf_path)
        except Exception as e:
            self._log("CARVE-FAIL", str(e))
        return carved_files
