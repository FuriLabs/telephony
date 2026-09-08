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

import mimetypes
import os
import random
import shutil
import tempfile
import threading
import time
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.mms_utils import max_attachment_size
from telephony.shared.constants import DEFAULT_MAX_ATTACHMENT_SIZE
from gi.repository import Gio, GLib, GObject

from telephony.shared.utils.phone_utils import get_own_number, normalize_number
from telephony.shared.utils.thread_utils import run_in_background



MMS_RESOLVE_TIMEOUT_SECONDS = 180
UNCLAIMED_STATE_LIMIT = 20
SEEN_SIGNATURE_LIMIT = 50

MIME_EXTENSION_MAP = {
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


class MmsManager(GObject.Object):
    """
    Manages MMS sending, receiving, and storage, interfacing with ofono and mmsd.
    """
    __gsignals__ = {
        'message-received': (GObject.SignalFlags.RUN_FIRST, None, (str, object, str, str, object, str)),
    }

    def __init__(self, db_manager, eds_manager=None, gsettings_mgr=None, notification_manager=None,
                 owns_reception=True):
        """Initialize the MMS manager.

        Only the instance that owns reception files arriving messages,
        so a second instance cannot store the same message again.
        """
        super().__init__()
        self.db = db_manager
        self.eds = eds_manager
        self.gsettings_mgr = gsettings_mgr
        self.notification_manager = notification_manager
        self.owns_reception = owns_reception

        self.bus = None
        self.proxy = None
        self.manager_proxy = None
        self.connected = False
        self.service_path = None
        self.subs = []

        self.seen_mms_signatures = []
        self.processed_paths = set()
        self.last_sent_mms = None
        self.active_chat_provider = None

        self.inflight_mms = {}
        self.inflight_mms_paths = {}
        self.unclaimed_mms_states = {}
        self.mms_send_lock = threading.Lock()

        if not mimetypes.inited:
            mimetypes.init()

        self.local_att_dir = os.path.join(GLib.get_user_data_dir(), "telephony", "attachments")

        os.makedirs(self.local_att_dir, exist_ok=True)

        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        try:

            if self.owns_reception:
                self.subs.append(self.bus.signal_subscribe(
                    "org.ofono.mms", "org.ofono.mms.Service", "MessageAdded",
                    None, None, Gio.DBusSignalFlags.NONE,
                    self.on_message_added_raw, None
                ))

            self.subs.append(self.bus.signal_subscribe(
                "org.ofono.mms", "org.ofono.mms.Message", "PropertyChanged",
                None, None, Gio.DBusSignalFlags.NONE,
                self.on_message_prop_changed, None
            ))

            self.subs.append(self.bus.signal_subscribe(
                "org.ofono.mms", "org.ofono.mms.Service", "MessageSendError",
                None, None, Gio.DBusSignalFlags.NONE,
                self.on_message_send_error, None
            ))

            self.init_manager()

        except Exception as e:
            logger.error(f"[MMS] Bus Error: {e}")

    def init_manager(self):
        """Initialize the DBus proxy to the ofono MMS manager."""
        try:
            self.manager_proxy = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono.mms", "/org/ofono/mms", "org.ofono.mms.Manager", None
            )
            ret = self.manager_proxy.call_sync("GetServices", None, Gio.DBusCallFlags.NONE, -1, None)
            services = ret.unpack()[0]
            if services:
                self.service_path = services[0][0]
                logger.debug("[MMS-LOG] MANAGER-INIT | MMS Service found and connected")
                self.connect_service_proxy()
                self.load_existing_messages()
            else:
                logger.debug("[MMS-LOG] MANAGER-INIT | No MMS services found")
        except Exception as e:
            logger.debug(f"[MMS-LOG] INIT-FAILED | {e}")

    def connect_service_proxy(self):
        """Connect to the specific MMS service proxy."""
        try:
            self.proxy = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono.mms", self.service_path, "org.ofono.mms.Service", None
            )
            if self.proxy:
                self.connected = True
                if self.gsettings_mgr and self.gsettings_mgr.get_setting("delivery_reports") == "true":
                    self.set_delivery_reports(True)
        except Exception as e:
            logger.debug(f"[MMS-LOG] PROXY-FAILED | {e}")

    def set_delivery_reports(self, enabled):
        """Ask mmsd for MMS delivery reports; blocking, call from a worker."""
        if not self.proxy:
            return
        try:
            self.proxy.call_sync("SetProperty",
                                 GLib.Variant("(sv)", ("UseDeliveryReports", GLib.Variant("b", enabled))),
                                 Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.error(f"[MMS-LOG] DELIVERY-REPORTS | {e}")

    def load_existing_messages(self):
        """Load messages already present in the daemon."""
        if not self.proxy or not self.owns_reception:
            return
        logger.debug("[MMS-LOG] HISTORY-CHECK | Scanning daemon for existing messages")
        try:
            ret = self.proxy.call_sync("GetMessages", None, Gio.DBusCallFlags.NONE, -1, None)
            messages = ret.unpack()[0]
            for msg_path, props in messages:
                self.process_message_signal(msg_path, props)
        except Exception as e:
            logger.debug(f"[MMS-LOG] HISTORY-ERROR | {e}")

    def on_message_added_raw(self, conn, sender, path, iface, signal, params, user_data):
        """Handle raw DBus MessageAdded signal."""
        try:
            msg_path, props = params.unpack()
            logger.debug(f"[MMS-LOG] SIGNAL-RECV | New message signal at {msg_path}")
            self.process_message_signal(msg_path, props)
        except Exception as e:
            logger.debug(f"[MMS-LOG] SIGNAL-ERROR | {e}")

    def process_message_signal(self, msg_path, props):
        """Process a message signal, parsing properties and handling persistence."""
        try:
            if msg_path in self.processed_paths:
                logger.debug(f"[MMS-LOG] DEDUP-PATH | Ignoring duplicate signal for {msg_path}")
                return

            status = props.get('Status', '')
            if isinstance(status, GLib.Variant):
                status = status.unpack()

            logger.debug(f"[MMS-LOG] MSG-STATUS | Path: {msg_path} | Status: {status}")

            if status == 'sent':
                return

            if status == 'received' or status == 'downloaded':
                sender = props.get('Sender', 'Unknown')
                if isinstance(sender, GLib.Variant):
                    sender = sender.unpack()

                date = props.get('Date', '')
                if isinstance(date, GLib.Variant):
                    date = date.unpack()

                subject = props.get('Subject', '')
                if isinstance(subject, GLib.Variant):
                    subject = subject.unpack()

                signature = f"{sender}_{date}_{subject}"

                if signature in self.seen_mms_signatures:
                    logger.debug(f"[MMS-LOG] DEDUP-SIG | Duplicate content ignored: {signature}")
                    self.processed_paths.add(msg_path)
                    self.delete_message_from_daemon(msg_path)
                    return

                self.seen_mms_signatures.append(signature)
                if len(self.seen_mms_signatures) > SEEN_SIGNATURE_LIMIT:
                    self.seen_mms_signatures.pop(0)
                self.processed_paths.add(msg_path)

                logger.debug("[MMS-LOG] MSG-PROCESS | Starting parse and store sequence")

                def process():
                    self.parse_and_store(msg_path, props)
                    self.delete_message_from_daemon(msg_path)

                run_in_background(process)

            elif status == 'draft':
                logger.debug("[MMS-LOG] MSG-WAIT | Notification only. Waiting for auto-download.")
        except Exception as e:
            logger.debug(f"[MMS-LOG] SIGNAL-PROC-ERR | {e}")

    def delete_message_from_daemon(self, msg_path):
        """Delete a message from the ofono daemon."""
        try:
            msg_proxy = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono.mms", msg_path, "org.ofono.mms.Message", None
            )
            msg_proxy.call_sync("Delete", None, Gio.DBusCallFlags.NONE, -1, None)
            logger.debug(f"[MMS-LOG] MSG-CLEANUP | Removed {msg_path} from daemon storage")
        except Exception as e:
            logger.debug(f"[MMS-LOG] CLEANUP-FAILED | {e}")

    def user_size_limit(self):
        """Return the configured size limit in bytes, or None when unset."""
        limit = max_attachment_size(self.gsettings_mgr)
        return limit if limit != DEFAULT_MAX_ATTACHMENT_SIZE else None

    def get_max_attachment_size(self):
        """Return the attachment size budget from settings, else the default."""
        return self.user_size_limit() or DEFAULT_MAX_ATTACHMENT_SIZE

    def send_mms(self, recipients, subject=None, body=None, attachment_paths=[]):
        """Send an MMS message."""
        if not self.proxy:
            self.init_manager()
            if not self.proxy:
                logger.error("[MMS] Cannot send: MMS service not ready")
                return False

        current_sig = f"{sorted(recipients)}_{body}_{len(attachment_paths)}"
        if self.last_sent_mms:
            last_sig, last_time = self.last_sent_mms
            if last_sig == current_sig and (time.time() - last_time) < 3.0:
                logger.debug("[MMS-LOG] SEND-BLOCK | Double-click prevented")
                return False

        self.last_sent_mms = (current_sig, time.time())

        fmt_attachments = []
        temp_files = []

        logger.debug(f"[MMS-LOG] SEND-START | Recipients: {len(recipients)} | Files: {len(attachment_paths)}")

        if body:
            try:
                tf = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix=".txt")
                tf.write(body)
                tf.close()
                temp_files.append(tf.name)
                fmt_attachments.append((f"text_{os.path.basename(tf.name)}", "text/plain", tf.name))
            except Exception as e:
                logger.debug(f"[MMS-LOG] BODY-TEMP-FAIL | {e}")

        for path in attachment_paths:
            ctype = self.detect_mime(path)
            fmt_attachments.append((os.path.basename(path), ctype or "application/octet-stream", path))

        try:
            ret = self.proxy.call_sync("SendMessage",
                                       GLib.Variant("(asva(sss))", (list(recipients), GLib.Variant('s', ""), fmt_attachments)),
                                       Gio.DBusCallFlags.NONE, -1, None)
            self.cleanup(temp_files)
            msg_path = ret.unpack()[0]
            logger.debug(f"[MMS-LOG] SEND-SUCCESS | Handed off to mmsd as {msg_path}")

            return msg_path
        except Exception as e:
            self.cleanup(temp_files)
            logger.debug(f"[MMS-LOG] SEND-FAILED | {e}")
            return None

    def send_mms_tracked(self, recipients, body, attachment_paths, row_id):
        """Send an MMS and resolve the stored row's status from daemon signals."""
        path = self.send_mms(recipients, body=body, attachment_paths=attachment_paths)
        if not path:
            self.db.update_message_status(row_id, "failed")
            return

        with self.mms_send_lock:
            state = self.unclaimed_mms_states.pop(path, None)
            if state is None:
                self.inflight_mms[row_id] = path
                self.inflight_mms_paths[path] = row_id

        if state is not None:
            self.resolve_mms(row_id, state)
            return

        GLib.timeout_add_seconds(MMS_RESOLVE_TIMEOUT_SECONDS, self.timeout_mms, row_id)

    def resolve_mms(self, row_id, state):
        """Write the final status for an in-flight MMS row."""
        status = "sent" if state == "sent" else "failed"
        logger.info(f"[MMS] Row {row_id} resolved: {status}")
        self.db.update_message_status(row_id, status)

    def timeout_mms(self, row_id):
        """Fail an MMS row that never received a state signal."""
        with self.mms_send_lock:
            if row_id not in self.inflight_mms:
                return False
            path = self.inflight_mms.pop(row_id)
            self.inflight_mms_paths.pop(path, None)

        logger.warning(f"[MMS] Row {row_id} timed out without a state signal")
        self.db.update_message_status(row_id, "failed")
        return False

    def on_message_prop_changed(self, conn, sender, path, iface, signal, params, user_data):
        """Resolve in-flight sends from mmsd message status changes."""
        try:
            name, value = params.unpack()
        except Exception as e:
            logger.debug(f"[MMS] Message state unpack failed: {e}")
            return

        if name != "status" or value != "sent":
            return

        row_id = None
        with self.mms_send_lock:
            row_id = self.inflight_mms_paths.pop(path, None)
            if row_id is not None:
                self.inflight_mms.pop(row_id, None)
            else:
                self.unclaimed_mms_states[path] = value
                while len(self.unclaimed_mms_states) > UNCLAIMED_STATE_LIMIT:
                    self.unclaimed_mms_states.pop(next(iter(self.unclaimed_mms_states)))

        if row_id is not None:
            self.resolve_mms(row_id, "sent")

    def on_message_send_error(self, conn, sender, path, iface, signal, params, user_data):
        """Fail the oldest in-flight MMS when the daemon reports a send error."""
        logger.warning(f"[MMS] MessageSendError from daemon: {params.unpack() if params else None}")
        row_id = None
        with self.mms_send_lock:
            if self.inflight_mms:
                row_id = next(iter(self.inflight_mms))
                msg_path = self.inflight_mms.pop(row_id)
                self.inflight_mms_paths.pop(msg_path, None)

        if row_id is not None:
            self.resolve_mms(row_id, "failed")

    def cleanup(self, files):
        """Clean up temporary files."""
        for tmp in files:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception as e:
                    logger.debug(f"[MMS-LOG] CLEANUP-FAIL | {e}")

    def parse_and_store(self, path, props):
        """Parse message content and attachments, and store in database."""
        try:
            sender = props.get('Sender', None) or "Unknown"
            if isinstance(sender, GLib.Variant):
                sender = sender.unpack()

            if self.db and self.db.is_blocked(sender, kind="messages"):
                logger.info(f"[MMS] Dropping message from blocked sender {sender}")
                self.delete_message_from_daemon(path)
                return

            recipients = props.get('Recipients', [])
            if isinstance(recipients, GLib.Variant):
                recipients = recipients.unpack()

            date = props.get('Date', '')
            if isinstance(date, GLib.Variant):
                date = date.unpack()

            attachments = props.get('Attachments', [])
            if isinstance(attachments, GLib.Variant):
                attachments = attachments.unpack()

            final_atts = []
            body_text = ""

            logger.debug(f"[MMS] Parsing message with {len(attachments)} attachments")

            for att in attachments:
                if len(att) < 3:
                    continue

                mime, src_path = str(att[1]), att[2]
                real_mime = self.detect_mime(src_path)
                is_file_only = any(x in real_mime.lower() for x in ["vcard", "smil", "shellscript", "python", "javascript"])

                if real_mime == "text/plain" and not is_file_only:
                    content = self.read_text_safely(src_path)
                    if content and not body_text:
                        body_text = content
                        logger.debug(f"[MMS] Body extracted ({len(content)} chars)")
                elif "smil" not in real_mime.lower():
                    dest = self.sanitize_and_store(src_path, mime)
                    if dest:
                        final_atts.append(dest)
                        logger.debug(f"[MMS] Attachment saved: {real_mime} -> {os.path.basename(dest)}")

            logger.debug(f"[MMS] Parsing complete, {len(final_atts)} attachments saved")

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
                participants.discard(own_number)
                participants.discard(norm_own)

            clean_list = sorted([normalize_number(p) for p in participants if p])

            if len(clean_list) == 0:
                saved_remote_number = sender
            elif len(clean_list) == 1:
                saved_remote_number = clean_list[0]
            else:
                saved_remote_number = clean_list

            status = "unread"
            if self.active_chat_provider:
                try:
                    active_chat, focused = self.active_chat_provider()
                    chat_id = saved_remote_number if isinstance(saved_remote_number, str) else ",".join(saved_remote_number)
                    if active_chat and focused and chat_id == active_chat:
                        status = "read"
                except Exception as e:
                    logger.debug(f"[MMS] Active chat check failed: {e}")

            if self.db:
                self.db.add_message(
                    remote_number=saved_remote_number,
                    direction="incoming",
                    body=body_text,
                    status=status,
                    subject=None,
                    attachments=final_atts,
                    sender=sender
                )

            GLib.idle_add(self.emit, 'message-received', sender, recipients, str(date), body_text, final_atts, sender_name)

        except Exception as e:
            logger.error(f"[MMS] Parse error: {e}")

    def read_text_safely(self, path):
        """Safely read text content from a file."""
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        except Exception as e:
            logger.debug(f"[MMS] Text read failed: {e}")
        return None

    def detect_mime(self, path):
        """Detect MIME type of a file using python-magic."""
        try:
            import magic
            mime = magic.from_file(path, mime=True)
            if mime and mime in ["text/html", "text/plain", "text/xml", "application/xml"]:
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        header = f.read(1024)
                        if "<smil" in header.lower():
                            return "application/smil+xml"
                except Exception as e:
                    logger.warning(f"[MMS] Failed to sniff content for SMIL: {e}")

            if mime:
                return mime
        except Exception as e:
            logger.debug(f"[MMS] MIME detection failed: {e}")
        return "application/octet-stream"

    def sanitize_and_store(self, src_path, mime_hint=None):
        """Sanitize filename and store attachment in local storage."""
        try:
            if not os.path.exists(src_path):
                return None
            detected = self.detect_mime(src_path)
            final_mime = detected if detected != "application/octet-stream" else (mime_hint or detected)

            if not mimetypes.inited:
                mimetypes.init()

            ext = mimetypes.guess_extension(final_mime)

            ext = MIME_EXTENSION_MAP.get(final_mime, ext)
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
            logger.debug(f"[MMS] Attachment store failed: {e}")
            return None
