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


from telephony.shared.utils.datetime_utils import parse_timestamp

import os
import mimetypes
import re
import hashlib
import shutil
import html
from concurrent.futures import ThreadPoolExecutor
from gettext import gettext as _

import gi
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Pango, Gio, GLib, Adw
from telephony.shared.utils.log_utils import logger
from telephony.shared.utils.locale_utils import get_date_format, get_time_format


class ChatBubbleFactory:
    """Factory for creating chat message bubbles in the chat view."""

    _executor = ThreadPoolExecutor(max_workers=4)
    _thumb_meta_cache = {}
    _texture_cache = {}

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
        media_box.last_paths = []
        content_box.append(media_box)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(450)
        clamp.set_tightening_threshold(300)

        lbl_msg = Gtk.Label(wrap=True, xalign=0)
        lbl_msg.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl_msg.set_natural_wrap_mode(Gtk.NaturalWrapMode.INHERIT)
        lbl_msg.set_use_markup(True)
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

        spacer = Gtk.Box(hexpand=True)

        if is_incoming:
            row.append(bubble)
            row.append(btn_menu)
            row.append(spacer)
        else:
            row.append(spacer)
            row.append(btn_menu)
            row.append(bubble)

        widgets = {
            "root": row, "bubble": bubble, "media_box": media_box,
            "lbl_msg": lbl_msg, "lbl_time": lbl_time, "lbl_sender": lbl_sender,
            "btn_menu": btn_menu, "menu_ctx": None
        }
        group = Gio.SimpleActionGroup()
        for key in ("copy", "copy-number", "add-contact", "reschedule", "forward", "unread", "delete"):
            action = Gio.SimpleAction.new(key, None)
            action.connect("activate", lambda a, param, k=key: ChatBubbleFactory._safe_run(
                lambda: ChatBubbleFactory._run_menu_action(widgets, k.replace("-", "_")), "Menu action failed"))
            group.add_action(action)
        btn_menu.insert_action_group("bubble", group)
        btn_menu.set_create_popup_func(lambda mb: ChatBubbleFactory._prepare_menu(widgets))
        return widgets

    @staticmethod
    def _prepare_menu(w):
        """Build the context menu model for the currently bound message."""
        ctx = w["menu_ctx"]
        if not ctx:
            return

        item = ctx["item"]

        found_number = None
        if item.body:
            match = re.search(r'(?:\d *){4,}', item.body)
            if match:
                found_number = match.group(0).replace(" ", "")
        ctx["found_number"] = found_number

        menu = Gio.Menu()
        if ctx["add_contact_cb"] is not None:
            menu.append(_("Add to Contacts"), "bubble.add-contact")
        if item.status in ("scheduled", "failed") and ctx["reschedule_cb"] is not None:
            label = _("Retry Send") if item.status == "failed" else _("Re-Schedule")
            menu.append(label, "bubble.reschedule")
        menu.append(_("Copy Text"), "bubble.copy")
        if found_number is not None:
            menu.append(_("Copy Number"), "bubble.copy-number")
        menu.append(_("Forward"), "bubble.forward")
        if item.direction == "incoming":
            menu.append(_("Mark as Unread"), "bubble.unread")
        menu.append(_("Delete"), "bubble.delete")
        w["btn_menu"].set_menu_model(menu)

    @staticmethod
    def _run_menu_action(w, action):
        """Execute a context menu action against the currently bound message."""
        ctx = w["menu_ctx"]
        if not ctx:
            return

        item = ctx["item"]

        if action == "copy":
            Gdk.Display.get_default().get_clipboard().set(item.body or "")
        elif action == "copy_number":
            if ctx.get("found_number"):
                Gdk.Display.get_default().get_clipboard().set(ctx["found_number"])
        elif action == "forward":
            if ctx["forward_cb"]:
                GLib.timeout_add(50, lambda: ctx["forward_cb"](item) or False)
        elif action == "delete":
            if ctx["delete_cb"]:
                GLib.timeout_add(50, lambda: ctx["delete_cb"](item.id) or False)
        elif action == "reschedule":
            if ctx["reschedule_cb"]:
                GLib.timeout_add(50, lambda: ctx["reschedule_cb"](item) or False)
        elif action == "unread":
            if ctx["unread_cb"]:
                GLib.timeout_add(50, lambda: ctx["unread_cb"](item.id) or False)
        elif action == "add_contact":
            cb = ctx["add_contact_cb"]
            if cb:
                GLib.timeout_add(50, lambda: cb(number_preset=ctx["sender_num"]) or False)

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

        show_add_contact = False
        add_contact_num = None

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
                show_add_contact = True
                add_contact_num = sender_num
        else:
            target_w["lbl_sender"].set_visible(False)

            target_w["bubble"].remove_css_class("chat-bubble-failed")
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
            elif item.status == "failed":
                target_w["bubble"].remove_css_class("chat-bubble-scheduled")
                target_w["bubble"].add_css_class("chat-bubble-out")
                target_w["bubble"].add_css_class("chat-bubble-failed")
                target_w["lbl_time"].set_text(_("Failed to send"))
            elif item.status == "sending":
                target_w["bubble"].remove_css_class("chat-bubble-scheduled")
                target_w["bubble"].add_css_class("chat-bubble-out")
                target_w["lbl_time"].set_text(_("Sending..."))
            elif item.status == "delivered":
                target_w["bubble"].remove_css_class("chat-bubble-scheduled")
                target_w["bubble"].add_css_class("chat-bubble-out")
                target_w["lbl_time"].set_text(
                    _("{time} · Delivered").format(time=item.display_time))
            else:
                target_w["bubble"].remove_css_class("chat-bubble-scheduled")
                target_w["bubble"].add_css_class("chat-bubble-out")

        target_w["menu_ctx"] = {
            "item": item,
            "delete_cb": delete_callback,
            "forward_cb": forward_callback,
            "reschedule_cb": reschedule_cb,
            "unread_cb": unread_callback,
            "add_contact_cb": add_contact_cb if show_add_contact else None,
            "sender_num": add_contact_num,
        }

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
                import magic
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
                        from PIL import Image, ImageOps
                        with Image.open(path) as img:
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGB")
                            img = ImageOps.fit(img, (60, 60), method=Image.Resampling.LANCZOS)
                            img.save(target_thumb, "JPEG", quality=70)
                        thumb_path = target_thumb
                    elif is_video:
                        import av
                        from PIL import Image, ImageOps
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

        def _update_ui(thumb_path, icon_name, is_video):
            if not check_func():
                return False

            if icon_name:
                icon_widget.set_from_icon_name(icon_name)

            if thumb_path and os.path.exists(thumb_path):
                pic = Gtk.Picture()
                pic.set_content_fit(Gtk.ContentFit.COVER)
                pic.set_can_shrink(True)
                pic.set_paintable(ChatBubbleFactory._get_cached_texture(thumb_path))

                if is_video:
                    overlay = Gtk.Overlay()
                    overlay.set_child(pic)

                    play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
                    play_icon.set_pixel_size(24)
                    play_icon.set_halign(Gtk.Align.CENTER)
                    play_icon.set_valign(Gtk.Align.CENTER)
                    play_icon.add_css_class("icon-white-shadow")
                    overlay.add_overlay(play_icon)
                    stack_ref.add_named(overlay, "thumb")
                else:
                    stack_ref.add_named(pic, "thumb")

                stack_ref.set_visible_child_name("thumb")

            return False

        cached = ChatBubbleFactory._thumb_meta_cache.get(path)
        if cached is not None:
            GLib.idle_add(lambda: _update_ui(*cached))
            return

        def _on_done(f):
            if not check_func():
                return

            try:
                res = f.result()

                if os.path.exists(path):
                    meta_cache = ChatBubbleFactory._thumb_meta_cache
                    meta_cache[path] = res
                    while len(meta_cache) > 256:
                        meta_cache.pop(next(iter(meta_cache)))

                GLib.idle_add(lambda: _update_ui(*res))

            except Exception as e:
                logger.debug(f"Async detection done error: {e}")

        ChatBubbleFactory._executor.submit(_bg_task).add_done_callback(_on_done)

    @staticmethod
    def _get_cached_texture(thumb_path):
        """Return a texture for a thumbnail file, decoding it only once."""
        cache = ChatBubbleFactory._texture_cache
        tex = cache.get(thumb_path)
        if tex is None:
            tex = Gdk.Texture.new_from_filename(thumb_path)
            cache[thumb_path] = tex
            while len(cache) > 128:
                cache.pop(next(iter(cache)))
        return tex

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
        """Show the attachment actions menu."""
        group = Gio.SimpleActionGroup()
        entries = (
            ("open", lambda: ChatBubbleFactory.on_open(path, widget_ref)),
            ("copy", lambda: ChatBubbleFactory.on_copy_file(path)),
            ("save-as", lambda: ChatBubbleFactory.on_save_as(path, widget_ref)),
        )
        for key, callback in entries:
            action = Gio.SimpleAction.new(key, None)
            action.connect("activate", lambda a, param, cb=callback: ChatBubbleFactory._safe_run(cb))
            group.add_action(action)

        menu = Gio.Menu()
        menu.append(_("Open with"), "attachment.open")
        menu.append(_("Copy"), "attachment.copy")
        menu.append(_("Save as"), "attachment.save-as")

        btn.insert_action_group("attachment", group)

        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() or False))
        pop.set_parent(btn)
        GLib.idle_add(lambda: pop.popup() or False)

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
                w.unparent()
            list_item.widgets = None
        list_item.set_child(None)
