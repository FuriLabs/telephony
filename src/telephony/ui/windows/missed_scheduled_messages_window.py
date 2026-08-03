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

from gi.repository import Gtk, Adw, GLib, Pango
from loguru import logger
from gettext import gettext as _
from ...backend.utils.datetime_utils import parse_timestamp
from ..widgets.chat_bubbles_widget import ChatBubbleFactory
from ...backend.utils.thread_utils import run_in_background
import json
import os
import time
from ..widgets.common_widget import close_dialog


class MissedScheduledMessagesDialog:
    """Logic and UI for displaying and handling missed scheduled messages."""

    def __init__(self, app_window):
        self.app_window = app_window
        self.db = app_window.db
        self.eds = app_window.eds
        self.scheduler = app_window.app.scheduler
        self.ofono = app_window.ofono
        self.mms = app_window.mms

    def check_missed_scheduled_messages(self, done_callback=None):
        """Check for messages that were missed while app was closed."""
        def done(missed):
            if not missed:
                if done_callback:
                    done_callback()
                return
            self._process_missed_message_queue(missed, 0, done_callback)

        def failed(error):
            logger.error(f"[MissedScheduled] Check missed messages error: {error}")
            if done_callback:
                done_callback()

        run_in_background(self.scheduler.get_missed_messages, buffer_minutes=1,
                          on_complete=done, on_error=failed)

    def _process_missed_message_queue(self, messages, index, done_callback):
        """Recursively show dialogs for missed messages."""
        if index >= len(messages):
            if done_callback:
                done_callback()
            return

        msg = messages[index]
        mid, number, body, subject, attachments_json, scheduled_ts = msg
        total_count = len(messages)
        current_count = index + 1

        contact_name = number
        if "," in number:
            parts = number.split(",")
            name = self.db.get_group_name(parts)
            if name:
                contact_name = name
        else:
            cname = self.eds.get_contact_name(number)
            if cname:
                contact_name = cname

        display_name = contact_name if len(contact_name) < 30 else contact_name[:27] + "..."
        display_body = body if len(body) < 150 else body[:147] + "..."

        d = Adw.AlertDialog(
            heading=_("Scheduled ({current}/{total})").format(current=current_count, total=total_count)
        )

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(6)
        main_box.set_margin_bottom(6)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)

        lbl_to = Gtk.Label(label=_("To: {name}").format(name=display_name))
        lbl_to.add_css_class("dim-label")
        lbl_to.set_halign(Gtk.Align.CENTER)
        main_box.append(lbl_to)

        bubble_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bubble_box.add_css_class("chat-bubble-scheduled")
        bubble_box.set_halign(Gtk.Align.CENTER)
        bubble_box.set_margin_top(4)
        bubble_box.set_margin_bottom(4)

        media_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        media_box.set_visible(False)
        bubble_box.append(media_box)

        lbl_msg = Gtk.Label(wrap=True, xalign=0)
        lbl_msg.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl_msg.set_max_width_chars(30)
        lbl_msg.set_natural_wrap_mode(Gtk.NaturalWrapMode.INHERIT)
        lbl_msg.set_use_markup(True)
        lbl_msg.set_lines(4)
        lbl_msg.set_ellipsize(Pango.EllipsizeMode.END)

        if display_body:
            final_body = ChatBubbleFactory._linkify(display_body)
            lbl_msg.set_markup(final_body)
            lbl_msg.set_visible(True)
        else:
            lbl_msg.set_visible(False)

        bubble_box.append(lbl_msg)

        if attachments_json:
            try:
                att_list = json.loads(attachments_json)
                processed_files = []
                for att in att_list:
                    real_path = att
                    if isinstance(att, (list, tuple)) and len(att) > 2:
                        real_path = att[2]
                    if os.path.exists(real_path):
                        processed_files.append({"path": real_path})

                if processed_files:
                    media_box.set_visible(True)
                    ChatBubbleFactory._create_attachment_layout(media_box, processed_files, bubble_box, lambda: True)
            except Exception as ex:
                logger.error(f"[MissedScheduled] Attachment processing error: {ex}")

        lbl_time = Gtk.Label(css_classes=["chat-time"], xalign=1)
        if scheduled_ts:
            try:
                dt = parse_timestamp(scheduled_ts)
                short_ts = dt.strftime("%H:%M %d.%m")
                lbl_time.set_text(_("Scheduled: {time}").format(time=short_ts))
            except Exception as ex:
                logger.warning(f"[MissedScheduled] Date parse warning: {ex}")
                lbl_time.set_text(scheduled_ts)

        bubble_box.append(lbl_time)
        main_box.append(bubble_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        btn_send = Gtk.Button(label=_("Send Now"))
        btn_send.add_css_class("suggested-action")

        def _on_send_now(b):
            GLib.idle_add(lambda: close_dialog(d) or False)
            self._send_missed_message_bg(msg)
            GLib.idle_add(lambda: self._process_missed_message_queue(messages, index + 1, done_callback))

        btn_send.connect("clicked", lambda b: GLib.idle_add(lambda: _on_send_now(b) or False))
        btn_box.append(btn_send)

        btn_remove = Gtk.Button(label=_("Remove"))
        btn_remove.add_css_class("destructive-action")

        def _on_remove(b):
            GLib.idle_add(lambda: close_dialog(d) or False)
            self.db.delete_scheduled_messages([mid])
            self.scheduler.remove_cron(mid)

            GLib.idle_add(lambda: self._process_missed_message_queue(messages, index + 1, done_callback))

        btn_remove.connect("clicked", lambda b: GLib.idle_add(lambda: _on_remove(b) or False))
        btn_box.append(btn_remove)

        main_box.append(btn_box)
        d.set_extra_child(main_box)
        d.present(self.app_window)

    def _send_missed_message_bg(self, msg):
        """Send message in background thread."""
        def _task():
            try:
                if self.ofono and not self.ofono.msg_proxy:
                    retries = 30
                    while retries > 0:
                        if self.ofono.msg_proxy:
                            break
                        time.sleep(1)
                        retries -= 1
                    if not self.ofono.msg_proxy:
                        logger.error("[MissedScheduled] Modem not ready after waiting, proceeding with risk of failure")

                mid, number, body, subject, attachments_json, scheduled_ts = msg
                attachments = []
                if attachments_json:
                    try:
                        attachments = json.loads(attachments_json)
                    except Exception as e:
                        logger.warning(f"[MissedScheduled] Failed to parse attachments JSON: {e}")

                self.db.update_message_schedule(mid, status="sending")
                self.scheduler.remove_cron(mid)

                is_group = "," in number
                if is_group or attachments or subject:
                    if self.mms:
                        targets = [n.strip() for n in number.split(",")] if is_group else [number]
                        self.mms.send_mms_tracked(targets, body, attachments, mid)
                else:
                    if self.ofono:
                        self.ofono.send_sms_tracked(number, body, mid)

                GLib.idle_add(lambda: self.app_window.notify_success(_("Sending message to {number}...").format(number=number)))

            except Exception as e:
                logger.error(f"[MissedScheduled] Send missed bg error: {e}")

        run_in_background(_task)
