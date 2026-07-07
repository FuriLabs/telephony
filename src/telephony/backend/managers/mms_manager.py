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

from .mms_parser_manager import MmsParserMixin


import mimetypes
import os
import tempfile
import time
from loguru import logger
from gettext import gettext as _
from gi.repository import Gio, GLib, GObject


NOTIFY_DBUS_NAME = "org.freedesktop.Notifications"
NOTIFY_DBUS_PATH = "/org/freedesktop/Notifications"
NOTIFY_INTERFACE = "org.freedesktop.Notifications"


class MmsManager(GObject.Object, MmsParserMixin):
    """
    Manages MMS sending, receiving, and storage, interfacing with ofono and mmsd.
    """
    __gsignals__ = {
        'message-received': (GObject.SignalFlags.RUN_FIRST, None, (str, object, str, str, object, str)),
        'message-added': (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        'message-removed': (GObject.SignalFlags.RUN_FIRST, None, (str,))
    }

    def __init__(self, db_manager, eds_manager=None, gsettings_mgr=None, notification_manager=None):
        """Initialize the MMS manager."""
        super().__init__()
        self.db = db_manager
        self.eds = eds_manager
        self.gsettings_mgr = gsettings_mgr
        self.notification_manager = notification_manager

        self.bus = None
        self.proxy = None
        self.manager_proxy = None
        self.connected = False
        self.service_path = None
        self.subs = []

        self.seen_mms_signatures = []
        self.processed_paths = set()
        self.last_sent_mms = None

        if not mimetypes.inited:
            mimetypes.init()

        self.local_att_dir = os.path.join(GLib.get_user_data_dir(), "telephony", "attachments")
        self.mmsd_storage_dir = os.path.expanduser("~/.mms/modemmanager")

        if not os.path.exists(self.local_att_dir):
            try:
                os.makedirs(self.local_att_dir, exist_ok=True)
            except Exception as e:
                logger.error(f"[MMS] Failed to create attachment dir: {e}")

        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            self.subs.append(self.bus.signal_subscribe(
                "org.ofono.mms", "org.ofono.mms.Service", "MessageAdded",
                None, None, Gio.DBusSignalFlags.NONE,
                self._on_message_added_raw, None
            ))

            self.subs.append(self.bus.signal_subscribe(
                "org.ofono.mms", "org.ofono.mms.Service", "MessageRemoved",
                None, None, Gio.DBusSignalFlags.NONE,
                self._on_message_removed_raw, None
            ))

            self._init_manager()

        except Exception as e:
            logger.error(f"[MMS] Bus Error: {e}")

    def _log(self, step, detail=""):
        """Internal logging helper."""
        logger.debug(f"[MMS-LOG] {step} | {detail}")

    def _init_manager(self):
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
                self._log("MANAGER-INIT", "MMS Service found and connected")
                self._connect_service_proxy()
                self.load_existing_messages()
            else:
                self._log("MANAGER-INIT", "No MMS services found")
        except Exception as e:
            self._log("INIT-FAILED", str(e))

    def _connect_service_proxy(self):
        """Connect to the specific MMS service proxy."""
        try:
            self.proxy = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono.mms", self.service_path, "org.ofono.mms.Service", None
            )
            if self.proxy:
                self.connected = True
        except Exception as e:
            self._log("PROXY-FAILED", str(e))

    def load_existing_messages(self):
        """Load messages already present in the daemon."""
        if not self.proxy:
            return
        self._log("HISTORY-CHECK", "Scanning daemon for existing messages")
        try:
            ret = self.proxy.call_sync("GetMessages", None, Gio.DBusCallFlags.NONE, -1, None)
            messages = ret.unpack()[0]
            for msg_path, props in messages:
                self._process_message_signal(msg_path, props)
        except Exception as e:
            self._log("HISTORY-ERROR", str(e))

    def _on_message_added_raw(self, conn, sender, path, iface, signal, params, user_data):
        """Handle raw DBus MessageAdded signal."""
        try:
            msg_path, props = params.unpack()
            self._log("SIGNAL-RECV", f"New message signal at {msg_path}")
            self._process_message_signal(msg_path, props)
            self.emit('message-added', msg_path, props)
        except Exception as e:
            self._log("SIGNAL-ERROR", str(e))

    def _process_message_signal(self, msg_path, props):
        """Process a message signal, parsing properties and handling persistence."""
        try:
            if msg_path in self.processed_paths:
                self._log("DEDUP-PATH", f"Ignoring duplicate signal for {msg_path}")
                return

            status = props.get('Status', '')
            if isinstance(status, GLib.Variant):
                status = status.unpack()

            self._log("MSG-STATUS", f"Path: {msg_path} | Status: {status}")

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
                    self._log("DEDUP-SIG", f"Duplicate content ignored: {signature}")
                    self.processed_paths.add(msg_path)
                    self._delete_message_from_daemon(msg_path)
                    return

                self.seen_mms_signatures.append(signature)
                if len(self.seen_mms_signatures) > 50:
                    self.seen_mms_signatures.pop(0)
                self.processed_paths.add(msg_path)

                self._log("MSG-PROCESS", "Starting parse and store sequence")
                self._parse_and_store(msg_path, props)
                self._delete_message_from_daemon(msg_path)

            elif status == 'draft':
                self._log("MSG-WAIT", "Notification only. Waiting for auto-download.")
        except Exception as e:
            self._log("SIGNAL-PROC-ERR", str(e))

    def _delete_message_from_daemon(self, msg_path):
        """Delete a message from the ofono daemon."""
        try:
            msg_proxy = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono.mms", msg_path, "org.ofono.mms.Message", None
            )
            msg_proxy.call_sync("Delete", None, Gio.DBusCallFlags.NONE, -1, None)
            self._log("MSG-CLEANUP", f"Removed {msg_path} from daemon storage")
        except Exception as e:
            self._log("CLEANUP-FAILED", str(e))

    def _on_message_removed_raw(self, conn, sender, path, iface, signal, params, user_data):
        """Handle raw DBus MessageRemoved signal."""
        try:
            msg_path = params.unpack()[0]
            self._log("SIGNAL-REMOVE", f"Message {msg_path} removed from daemon")
            self.emit('message-removed', msg_path)
        except Exception as e:
            self._log("REMOVE-SIGNAL-ERR", str(e))

    def send_mms(self, recipients, subject=None, body=None, attachment_paths=[]):
        """Send an MMS message."""
        if not self.proxy:
            self._init_manager()
            if not self.proxy:
                self.emit('action-error', _("Modem not ready"))
                return False

        current_sig = f"{sorted(recipients)}_{body}_{len(attachment_paths)}"
        if self.last_sent_mms:
            last_sig, last_time = self.last_sent_mms
            if last_sig == current_sig and (time.time() - last_time) < 3.0:
                self._log("SEND-BLOCK", "Double-click prevented")
                return False

        self.last_sent_mms = (current_sig, time.time())

        fmt_attachments = []
        temp_files = []

        self._log("SEND-START", f"Recipients: {len(recipients)} | Files: {len(attachment_paths)}")

        if body:
            try:
                tf = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix=".txt")
                tf.write(body)
                tf.close()
                temp_files.append(tf.name)
                fmt_attachments.append((f"text_{os.path.basename(tf.name)}", "text/plain", tf.name))
            except Exception as e:
                self._log("BODY-TEMP-FAIL", str(e))

        for path in attachment_paths:
            ctype = self._detect_mime(path)
            fmt_attachments.append((os.path.basename(path), ctype or "application/octet-stream", path))

        try:
            self.proxy.call_sync("SendMessage",
                                 GLib.Variant("(asva(sss))", (list(recipients), GLib.Variant('s', ""), fmt_attachments)),
                                 Gio.DBusCallFlags.NONE, -1, None)
            self._cleanup(temp_files)
            self._log("SEND-SUCCESS", "Handed off to ofono")

            return True
        except Exception as e:
            self._cleanup(temp_files)
            self._log("SEND-FAILED", str(e))
            return False

    def _cleanup(self, files):
        """Clean up temporary files."""
        for tmp in files:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception as e:
                    self._log("CLEANUP-FAIL", str(e))
