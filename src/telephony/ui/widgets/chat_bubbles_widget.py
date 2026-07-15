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


from ...backend.utils.datetime_utils import parse_timestamp

import os
import mimetypes
import re
import hashlib
import magic
import av
import shutil
import html
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageOps
from gettext import gettext as _

import gi
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Pango, Gio, GLib, Adw
from loguru import logger
from ...backend.utils.locale_utils import get_date_format, get_time_format


class ChatBubbleFactory:
    """Factory for creating chat message bubbles in the chat view."""

    _executor = ThreadPoolExecutor(max_workers=4)

    @staticmethod
    def _safe_run(func, error_msg="Action failed"):
        """Wrapper to execute a function safely with logging."""
        try:
            func()
        except Exception as e:
            logger.exception(f"{error_msg}: {e}")

    @staticmethod
    def _create_bubble_widgets(is_incoming=True):
        """Create a bubble layout (either incoming or outgoing)."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_margin_top(2)
        row.set_margin_bottom(2)
        row.set_margin_start(10)
        row.set_margin_end(10)

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bubble.set_valign(Gtk.Align.START)
        bubble.set_name("bubble_box")
        if is_incoming:
            bubble.add_css_class("chat-bubble-in")
        else:
            bubble.add_css_class("chat-bubble-out")

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bubble.append(content_box)

        media_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        media_box.set_visible(False)
        content_box.append(media_box)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(450)
        clamp.set_tightening_threshold(300)

        lbl_msg = Gtk.Label(wrap=True, xalign=0)
        lbl_msg.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl_msg.set_natural_wrap_mode(Gtk.NaturalWrapMode.INHERIT)
        lbl_msg.set_use_markup(True)
        lbl_msg.set_selectable(True)
        clamp.set_child(lbl_msg)
        content_box.append(clamp)

        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        meta_box.set_halign(Gtk.Align.END)

        lbl_sender = Gtk.Label(css_classes=["caption", "dim-label"])
        lbl_sender.set_wrap(True)
        lbl_sender.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl_sender.set_max_width_chars(20)
        lbl_sender.set_visible(False)

        lbl_time = Gtk.Label(css_classes=["chat-time"], xalign=1)

        meta_box.append(lbl_sender)
        meta_box.append(lbl_time)
        bubble.append(meta_box)

        btn_menu = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat", "bubble-menu"])
        btn_menu.set_opacity(1)

        pop = Gtk.Popover()

        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        menu_box.set_margin_start(6)
        menu_box.set_margin_end(6)
        menu_box.set_margin_top(6)
        menu_box.set_margin_bottom(6)

        def create_menu_btn(icon, label, style_class="suggested-action"):
            b = Gtk.Button()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=label))
            b.set_child(box)
            if style_class:
                b.add_css_class(style_class)
            return b

        btn_copy = create_menu_btn("edit-copy-symbolic", _("Copy Text"))
        btn_copy_num = create_menu_btn("edit-copy-symbolic", _("Copy Number"))
        btn_add_contact = create_menu_btn("contact-new-symbolic", _("Add to Contacts"), "suggested-action")
        btn_add_contact.set_visible(False)
        btn_resched = create_menu_btn("alarm-symbolic", _("Re-Schedule"), "suggested-action")
        btn_resched.set_visible(False)
        btn_forward = create_menu_btn("mail-forward-symbolic", _("Forward"))
        btn_unread = create_menu_btn("mail-unread-symbolic", _("Mark as Unread"))
        btn_del = create_menu_btn("user-trash-symbolic", _("Delete"), "destructive-action")

        menu_box.append(btn_add_contact)
        menu_box.append(btn_resched)
        menu_box.append(btn_copy)
        menu_box.append(btn_copy_num)
        menu_box.append(btn_forward)
        menu_box.append(btn_unread)
        menu_box.append(btn_del)

        pop.set_child(menu_box)
        btn_menu.set_popover(pop)

        spacer = Gtk.Box(hexpand=True)

        if is_incoming:
            row.append(bubble)
            row.append(btn_menu)
            row.append(spacer)
        else:
            row.append(spacer)
            row.append(btn_menu)
            row.append(bubble)

        return {
            "root": row, "bubble": bubble, "media_box": media_box,
            "lbl_msg": lbl_msg, "lbl_time": lbl_time, "lbl_sender": lbl_sender,
            "pop": pop, "actions": (btn_copy, btn_copy_num, btn_del, btn_add_contact, btn_forward, btn_resched, btn_unread)
        }

    @staticmethod
    def setup(factory, list_item):
        """Setup UI widgets for a chat bubble row."""
        stack = Gtk.Stack()
        stack.set_vhomogeneous(False)
        stack.set_hhomogeneous(False)
        stack.add_css_class("inverted-row")

        div_box = Gtk.Box()
        div_lbl = Gtk.Label(css_classes=["dim-label", "caption"], hexpand=True, halign=Gtk.Align.CENTER)
        div_box.append(div_lbl)
        stack.add_named(div_box, "divider")

        in_widgets = ChatBubbleFactory._create_bubble_widgets(is_incoming=True)
        stack.add_named(in_widgets["root"], "incoming")

        out_widgets = ChatBubbleFactory._create_bubble_widgets(is_incoming=False)
        stack.add_named(out_widgets["root"], "outgoing")

        list_item.set_child(stack)

        list_item.widgets = {
            "stack": stack, "div_lbl": div_lbl,
            "incoming": in_widgets, "outgoing": out_widgets
        }

    @staticmethod
    def bind(factory, list_item, delete_callback=None, contact_lookup=None, add_contact_cb=None, is_group=False, forward_callback=None, reschedule_cb=None, unread_callback=None):
        """Bind data to the chat bubble widgets."""
        if list_item.get_child() is None:
            return

        w = list_item.widgets
        item = list_item.get_item()

        current_id = w.get("load_id", 0) + 1
        w["load_id"] = current_id

        if item.is_divider:
            w["stack"].set_visible_child_name("divider")
            w["div_lbl"].set_text(item.body)
            return

        target_w = w["incoming"] if item.direction == "incoming" else w["outgoing"]
        w["stack"].set_visible_child_name(item.direction if item.direction == "incoming" else "outgoing")

        if item.body:
            if not item.cached_markup:
                item.cached_markup = ChatBubbleFactory._linkify(item.body)
            target_w["lbl_msg"].set_markup(item.cached_markup)
            target_w["lbl_msg"].set_visible(True)
        else:
            target_w["lbl_msg"].set_visible(False)

        target_w["lbl_time"].set_text(item.display_time)

        m_box = target_w["media_box"]

        processed_files = []
        new_paths_sig = []

        if (item.attachments is not None) and item.attachments:
            for idx, att_path in enumerate(item.attachments):
                real_path = att_path
                if isinstance(att_path, list) or isinstance(att_path, tuple):
                    real_path = att_path[2] if len(att_path) > 2 else str(att_path)

                processed_files.append({"path": real_path})
                new_paths_sig.append(real_path)

        current_sig = m_box.last_paths

        if current_sig != new_paths_sig:
            while child := m_box.get_first_child():
                m_box.remove(child)

            m_box.set_visible(bool(processed_files))
            if processed_files:
                def check_func(): return w.get("load_id") == current_id
                ChatBubbleFactory._create_attachment_layout(m_box, processed_files, target_w["bubble"], check_func)

            m_box.last_paths = new_paths_sig

        if item.direction == "incoming":
            sender_num = item.sender
            sender_name = None

            check_unknown = (sender_num != "Unknown" and sender_num != _("Unknown"))

            if contact_lookup and sender_num and check_unknown:
                sender_name = contact_lookup(sender_num)
                if sender_name == "Unknown":
                    sender_name = _("Unknown")

            if is_group:
                display_text = sender_name
                if not display_text:
                    if not any(c.isalpha() for c in str(sender_num)):
                        display_text = _("Unknown")
                    else:
                        display_text = sender_num
                        if display_text == "Me":
                            display_text = _("Me")
                elif display_text == "Unknown":
                    display_text = _("Unknown")

                target_w["lbl_sender"].set_text(display_text)
                target_w["lbl_sender"].set_visible(True)
            else:
                target_w["lbl_sender"].set_visible(False)

            if not sender_name and add_contact_cb and sender_num and check_unknown:
                target_w["actions"][3].set_visible(True)
                if (target_w["actions"][3].h is not None):
                    target_w["actions"][3].disconnect(target_w["actions"][3].h)

                def _add_contact_action():
                    target_w["pop"].popdown()
                    GLib.timeout_add(50, lambda: add_contact_cb(number_preset=sender_num) or False)

                target_w["actions"][3].h = target_w["actions"][3].connect(
                    "clicked",
                    lambda x: ChatBubbleFactory._safe_run(_add_contact_action, "Add contact failed")
                )
            else:
                target_w["actions"][3].set_visible(False)
        else:
            target_w["lbl_sender"].set_visible(False)
            target_w["actions"][3].set_visible(False)

            if item.status == "scheduled":
                target_w["bubble"].remove_css_class("chat-bubble-out")
                target_w["bubble"].add_css_class("chat-bubble-scheduled")
                if item.scheduled_timestamp:
                    try:
                        dt = parse_timestamp(item.scheduled_timestamp)
                        short_ts = dt.strftime(f"{get_time_format()} {get_date_format()}")
                        target_w["lbl_time"].set_text(_("Scheduled: {time}").format(time=short_ts))
                    except Exception as e:
                        logger.warning(f"Failed to format scheduled time: {e}")
            else:
                target_w["bubble"].remove_css_class("chat-bubble-scheduled")
                target_w["bubble"].add_css_class("chat-bubble-out")

        ChatBubbleFactory._connect_actions(target_w, item, delete_callback, target_w["bubble"], processed_files, forward_callback, reschedule_cb, unread_callback)

    @staticmethod
    def _resolve_icon_name(mime, path):
        """Resolve icon name from mime type."""
        if not mime:
            return "dialog-question-symbolic"

        if mime.startswith('image/'):
            return "image-x-generic-symbolic"
        elif mime.startswith('video/'):
            return "video-x-generic-symbolic"
        elif mime.startswith('audio/'):
            return "audio-x-generic-symbolic"
        elif mime == 'application/pdf':
            return "application-pdf-symbolic"
        elif mime in ('application/zip', 'application/x-tar', 'application/gzip', 'application/x-7z-compressed', 'application/vnd.rar'):
            return "package-x-generic-symbolic"
        elif mime in ('text/vcard', 'text/x-vcard'):
            return "x-office-address-book-symbolic"
        elif mime.startswith('text/'):
            return "text-x-generic-symbolic"

        return "text-x-generic-symbolic"

    @staticmethod
    def _create_single_attachment(meta, widget_ref, check_func):
        """Create a single attachment widget (60px) with async loading."""
        real_path = meta["path"]

        btn = Gtk.Button()
        btn.set_has_frame(False)
        btn.add_css_class("attachment-btn")
        btn.connect("clicked", lambda b: ChatBubbleFactory._on_attachment_clicked(b, real_path, widget_ref))

        aspect = Gtk.AspectFrame(obey_child=False, ratio=1.0)
        aspect.set_size_request(60, 60)
        btn.set_child(aspect)

        stack = Gtk.Stack()
        stack.add_css_class("rounded-corners")
        stack.set_overflow(Gtk.Overflow.HIDDEN)
        aspect.set_child(stack)

        icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        icon_box.set_valign(Gtk.Align.FILL)
        icon_box.set_halign(Gtk.Align.FILL)
        icon_box.add_css_class("attachment-tile")

        icon = Gtk.Image.new_from_icon_name("content-loading-symbolic")
        icon.set_pixel_size(24)
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_vexpand(True)
        icon.set_hexpand(True)
        icon_box.append(icon)

        stack.add_named(icon_box, "icon")
        stack.set_visible_child_name("icon")

        ChatBubbleFactory._detect_and_load_async(real_path, icon, stack, check_func)

        return btn

    @staticmethod
    def _detect_and_load_async(path, icon_widget, stack_ref, check_func):
        """Worker to detect mime and load thumbnail."""
        def _bg_task():
            if not os.path.exists(path):
                return None, "dialog-error-symbolic", None

            mime = None
            try:
                mime = magic.from_file(path, mime=True)
            except Exception as e:
                logger.debug(f"[Bubbles] magic.from_file failed: {e}")

            if not mime:
                mime, _encoding = mimetypes.guess_type(path)

            icon_name = ChatBubbleFactory._resolve_icon_name(mime, path)

            thumb_path = None
            is_video = mime and mime.startswith('video/')
            is_image = mime and mime.startswith('image/')

            if not is_video and not is_image:
                ext = os.path.splitext(path)[1].lower()
                if ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'):
                    is_video = True
                elif ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
                    is_image = True

            try:
                cache_dir = os.path.join(GLib.get_user_cache_dir(), "telephony", "thumbs")
                if not os.path.exists(cache_dir):
                    os.makedirs(cache_dir, exist_ok=True)

                path_hash = hashlib.md5(path.encode("utf-8")).hexdigest()
                target_thumb = os.path.join(cache_dir, f"{path_hash}.jpg")

                if os.path.exists(target_thumb):
                    thumb_path = target_thumb
                else:
                    if is_image:
                        with Image.open(path) as img:
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGB")
                            img = ImageOps.fit(img, (60, 60), method=Image.Resampling.LANCZOS)
                            img.save(target_thumb, "JPEG", quality=70)
                        thumb_path = target_thumb
                    elif is_video:
                        with av.open(path) as container:
                            stream = container.streams.video[0]
                            frame = next(container.decode(stream))
                            img = frame.to_image()
                            img = ImageOps.fit(img, (60, 60), method=Image.Resampling.LANCZOS)
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGB")
                            img.save(target_thumb, "JPEG", quality=70)
                        thumb_path = target_thumb
            except Exception as e:
                logger.debug(f"Thumb gen failed: {e}")

            return thumb_path, icon_name, is_video

        def _on_done(f):
            if not check_func():
                return

            try:
                res = f.result()
                thumb_path, icon_name, is_video = res

                def _update_ui():
                    if not check_func():
                        return False

                    if icon_name:
                        icon_widget.set_from_icon_name(icon_name)

                    if thumb_path and os.path.exists(thumb_path):
                        if is_video:
                            overlay = Gtk.Overlay()
                            pic = Gtk.Picture()
                            pic.set_content_fit(Gtk.ContentFit.COVER)
                            pic.set_can_shrink(True)
                            pic.set_filename(thumb_path)
                            overlay.set_child(pic)

                            play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
                            play_icon.set_pixel_size(24)
                            play_icon.set_halign(Gtk.Align.CENTER)
                            play_icon.set_valign(Gtk.Align.CENTER)
                            play_icon.add_css_class("icon-white-shadow")
                            overlay.add_overlay(play_icon)
                            stack_ref.add_named(overlay, "thumb")
                        else:
                            pic = Gtk.Picture()
                            pic.set_content_fit(Gtk.ContentFit.COVER)
                            pic.set_can_shrink(True)
                            pic.set_filename(thumb_path)
                            stack_ref.add_named(pic, "thumb")

                        stack_ref.set_visible_child_name("thumb")

                    return False

                GLib.idle_add(_update_ui)

            except Exception as e:
                logger.debug(f"Async detection done error: {e}")

        ChatBubbleFactory._executor.submit(_bg_task).add_done_callback(_on_done)

    @staticmethod
    def _create_attachment_layout(container, files, widget_ref, check_func):
        """
        Create a grid/row layout for attachments.
        Fixed size 60px, max 3 per row.
        """
        count = len(files)
        chunk_size = 3

        for i in range(0, count, chunk_size):
            chunk = files[i:i + chunk_size]
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row_box.set_halign(Gtk.Align.START)
            container.append(row_box)

            for meta in chunk:
                widget = ChatBubbleFactory._create_single_attachment(meta, widget_ref, check_func)
                row_box.append(widget)

    @staticmethod
    def _on_attachment_clicked(btn, path, widget_ref):
        """Handle attachment click."""
        pop = Gtk.Popover()
        pop.connect("closed", lambda p: p.unparent())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        def _add_action(icon, label, callback, style="suggested-action"):
            b = Gtk.Button()
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            btn_box.append(Gtk.Image(icon_name=icon))
            btn_box.append(Gtk.Label(label=label))
            b.set_child(btn_box)
            if style:
                b.add_css_class(style)

            def _handle_click():
                pop.popdown()
                GLib.timeout_add(50, lambda: ChatBubbleFactory._safe_run(callback) or False)
            b.connect("clicked", lambda x: _handle_click())
            box.append(b)

        _add_action("document-open-symbolic", _("Open with"), lambda: ChatBubbleFactory.on_open(path, widget_ref), "action-blue-light")
        _add_action("edit-copy-symbolic", _("Copy"), lambda: ChatBubbleFactory.on_copy_file(path), "action-blue-mid")
        _add_action("document-save-symbolic", _("Save as"), lambda: ChatBubbleFactory.on_save_as(path, widget_ref), "suggested-action")

        pop.set_child(box)
        pop.set_parent(btn)
        GLib.idle_add(lambda: pop.popup() or False)

    @staticmethod
    def _connect_actions(w, item, delete_callback, widget_ref, processed_files, forward_callback, reschedule_cb, unread_callback):
        """Connect actions to the bubble menu."""
        b_copy, b_copy_num, b_del, _, b_fwd, b_resched, btn_unread = w["actions"]

        if (b_copy.h is not None):
            b_copy.disconnect(b_copy.h)

        def _copy_action():
            Gdk.Display.get_default().get_clipboard().set(item.body or "")
            w["pop"].popdown()

        b_copy.h = b_copy.connect(
            "clicked",
            lambda x: ChatBubbleFactory._safe_run(_copy_action, "Copy text failed")
        )

        found_number = None
        if item.body:
            match = re.search(r'(?:\d *){4,}', item.body)
            if match:
                found_number = match.group(0).replace(" ", "")

        if found_number:
            b_copy_num.set_visible(True)
            if (b_copy_num.h is not None):
                b_copy_num.disconnect(b_copy_num.h)

            def _copy_num_action():
                Gdk.Display.get_default().get_clipboard().set(found_number)
                w["pop"].popdown()

            b_copy_num.h = b_copy_num.connect(
                "clicked",
                lambda x: ChatBubbleFactory._safe_run(_copy_num_action, "Copy number failed")
            )
        else:
            b_copy_num.set_visible(False)

        if (b_fwd.h is not None):
            b_fwd.disconnect(b_fwd.h)

        def _fwd_action():
            w["pop"].popdown()
            if forward_callback:
                GLib.timeout_add(50, lambda: forward_callback(item) or False)

        b_fwd.h = b_fwd.connect(
            "clicked",
            lambda x: ChatBubbleFactory._safe_run(_fwd_action, "Forward failed")
        )

        if (b_del.h is not None):
            b_del.disconnect(b_del.h)

        def _del_action():
            w["pop"].popdown()
            if delete_callback:
                GLib.timeout_add(50, lambda: delete_callback(item.id) or False)

        b_del.h = b_del.connect(
            "clicked",
            lambda x: ChatBubbleFactory._safe_run(_del_action, "Delete failed")
        )

        if item.status == "scheduled" and reschedule_cb:
            b_resched.set_visible(True)
            if (b_resched.h is not None):
                b_resched.disconnect(b_resched.h)

            def _resched_action():
                w["pop"].popdown()
                GLib.timeout_add(50, lambda: reschedule_cb(item) or False)

            b_resched.h = b_resched.connect(
                "clicked",
                lambda x: ChatBubbleFactory._safe_run(_resched_action, "Reschedule failed")
            )
        else:
            b_resched.set_visible(False)

        if item.direction == "incoming":
            btn_unread.set_visible(True)
            if (btn_unread.h is not None):
                btn_unread.disconnect(btn_unread.h)

            def _unread_action():
                w["pop"].popdown()
                if unread_callback:
                    GLib.timeout_add(50, lambda: unread_callback(item.id) or False)

            btn_unread.h = btn_unread.connect(
                "clicked",
                lambda x: ChatBubbleFactory._safe_run(_unread_action, "Mark Unread failed")
            )
        else:
            btn_unread.set_visible(False)

    @staticmethod
    def _get_root_window(widget):
        """Helper to find the root Gtk.Window."""
        root = widget.get_root()
        if isinstance(root, Gtk.Window):
            return root
        return None

    @staticmethod
    def on_copy_file(path):
        """Copy file URI to clipboard."""
        uri = GLib.filename_to_uri(path, None)
        uri_list = f"{uri}\r\n"
        b_data = GLib.Bytes.new(uri_list.encode('utf-8'))
        content = Gdk.ContentProvider.new_for_bytes("text/uri-list", b_data)
        Gdk.Display.get_default().get_clipboard().set_content(content)

    @staticmethod
    def on_open(path, widget_ref):
        """Open file with default application."""
        f = Gio.File.new_for_path(path)
        try:
            info = f.query_info("standard::content-type", Gio.FileQueryInfoFlags.NONE, None)
            content_type = info.get_content_type()
        except Exception as e:
            logger.warning(f"[Bubbles] Content type query failed: {e}")
            content_type = None

        if not content_type:
            mt, _enc = mimetypes.guess_type(path)
            content_type = mt or "application/octet-stream"

        parent = ChatBubbleFactory._get_root_window(widget_ref)
        dialog = Gtk.AppChooserDialog(
            transient_for=parent,
            modal=True,
            content_type=content_type
        )
        dialog.set_heading(_("Open With..."))

        def on_response(d, res):
            if res == Gtk.ResponseType.OK:
                app_info = d.get_app_info()
                if app_info:
                    try:
                        app_info.launch([f], None)
                    except Exception as e:
                        logger.error(f"Launch error: {e}")
            GLib.idle_add(lambda: d.destroy() or False)

        dialog.connect("response", on_response)
        dialog.present()

    @staticmethod
    def on_save_as(src_path, widget_ref):
        """Save attachment as..."""
        parent = ChatBubbleFactory._get_root_window(widget_ref)

        dialog = Gtk.FileChooserNative(
            title=_("Save File As"),
            transient_for=parent,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.set_current_name(os.path.basename(src_path))

        def on_response(d, res):
            if res == Gtk.ResponseType.ACCEPT:
                gfile = d.get_file()
                if gfile:
                    dest_path = gfile.get_path()
                    try:
                        shutil.copy(src_path, dest_path)
                    except Exception as e:
                        logger.warning(f"[Bubbles] Save as failed: {e}")
            GLib.idle_add(lambda: d.destroy() or False)

        dialog.connect("response", on_response)
        dialog.show()

    @staticmethod
    def _linkify(text):
        """Convert URLs and emails to links in text."""
        if not text:
            return ""

        safe_text = html.escape(text)

        url_pattern = re.compile(r'(https?://[^\s<>"]+|www\.[^\s<>"]+)')

        def url_repl(match):
            url = match.group(0)
            href = url
            if url.startswith("www."):
                href = "http://" + url
            return f'<a href="{href}">{url}</a>'

        safe_text = url_pattern.sub(url_repl, safe_text)

        email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')

        def email_repl(match):
            addr = match.group(0)
            return f'<a href="mailto:{addr}">{addr}</a>'

        safe_text = email_pattern.sub(email_repl, safe_text)

        return safe_text

    @staticmethod
    def unbind(factory, list_item):
        """Unbind data and free expensive resources when row leaves viewport."""
        if list_item.get_child() is None:
            return

        w = list_item.widgets
        w["load_id"] = w.get("load_id", 0) + 1

        for side in ("incoming", "outgoing"):
            if side in w:
                target_w = w[side]
                m_box = target_w.get("media_box")
                if m_box:
                    while child := m_box.get_first_child():
                        m_box.remove(child)
                    m_box.last_paths = []

                target_w["lbl_msg"].set_markup("")

    @staticmethod
    def teardown(factory, list_item):
        """Teardown the chat bubble widgets."""
        if (list_item.get_child() is not None) and list_item.widgets:
            for w in list_item.widgets.values():
                if hasattr(w, "unparent"):
                    w.unparent()
            list_item.widgets = None
        list_item.set_child(None)
