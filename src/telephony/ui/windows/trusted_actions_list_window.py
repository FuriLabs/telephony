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

from gi.repository import Gtk, Adw, GLib, Pango, Gio, Gdk, GdkPixbuf
from loguru import logger
from gettext import gettext as _
from io import BytesIO

from ...backend.utils.thread_utils import run_in_background
from ...backend.utils.phone_utils import normalize_number
from ..widgets.common_widget import populate_contact_search_results, translate_phone_label


class TrustedActionsListWindow(Adw.NavigationPage):
    """Settings subpage managing the list of trusted actions/contacts."""

    def __init__(self, parent, db, eds, mode="trusted_sms_location_request"):
        self.grp_list = None
        self.gsettings_handler_id = None
        self.totp_btn = None
        try:
            self.mode = mode
            self.gsettings_mgr = db
            self.eds = eds
            self.get_contacts_func = None
            self.set_contacts_func = None
            self.get_enabled_func = None
            self.set_enabled_func = None
            self.get_seed_func = None
            self.set_seed_func = None
            self.remove_seed_func = None

            if self.mode == "trusted_sms_location_request":
                self.get_contacts_func = self.gsettings_mgr.get_trusted_sms_location_request
                self.set_contacts_func = self.gsettings_mgr.set_trusted_sms_location_request
                self.get_enabled_func = self.gsettings_mgr.get_trusted_sms_location_request_enabled
                self.set_enabled_func = self.gsettings_mgr.set_trusted_sms_location_request_enabled
                self.get_seed_func = self.gsettings_mgr.get_trusted_sms_location_request_totp_seed
                self.set_seed_func = self.gsettings_mgr.set_trusted_sms_location_request_totp_seed
                self.remove_seed_func = self.gsettings_mgr.remove_trusted_sms_location_request_totp_seed
            elif self.mode == "trusted_sms_silent_callback":
                self.get_contacts_func = self.gsettings_mgr.get_trusted_sms_silent_callback
                self.set_contacts_func = self.gsettings_mgr.set_trusted_sms_silent_callback
                self.get_enabled_func = self.gsettings_mgr.get_trusted_sms_silent_callback_enabled
                self.set_enabled_func = self.gsettings_mgr.set_trusted_sms_silent_callback_enabled
                self.get_seed_func = self.gsettings_mgr.get_trusted_sms_silent_callback_totp_seed
                self.set_seed_func = self.gsettings_mgr.set_trusted_sms_silent_callback_totp_seed
                self.remove_seed_func = self.gsettings_mgr.remove_trusted_sms_silent_callback_totp_seed
            elif self.mode == "trusted_sms_relay":
                self.get_contacts_func = self.gsettings_mgr.get_trusted_sms_relay
                self.set_contacts_func = self.gsettings_mgr.set_trusted_sms_relay
                self.get_enabled_func = self.gsettings_mgr.get_trusted_sms_relay_enabled
                self.set_enabled_func = self.gsettings_mgr.set_trusted_sms_relay_enabled
                self.get_seed_func = self.gsettings_mgr.get_trusted_sms_relay_totp_seed
                self.set_seed_func = self.gsettings_mgr.set_trusted_sms_relay_totp_seed
                self.remove_seed_func = self.gsettings_mgr.remove_trusted_sms_relay_totp_seed
            elif self.mode == "trusted_sms_ssh_access":
                self.get_contacts_func = self.gsettings_mgr.get_trusted_sms_ssh_access
                self.set_contacts_func = self.gsettings_mgr.set_trusted_sms_ssh_access
                self.get_enabled_func = self.gsettings_mgr.get_trusted_sms_ssh_access_enabled
                self.set_enabled_func = self.gsettings_mgr.set_trusted_sms_ssh_access_enabled
                self.get_seed_func = self.gsettings_mgr.get_trusted_sms_ssh_access_totp_seed
                self.set_seed_func = self.gsettings_mgr.set_trusted_sms_ssh_access_totp_seed
                self.remove_seed_func = self.gsettings_mgr.remove_trusted_sms_ssh_access_totp_seed
            elif self.mode == "trusted_sms_lock_device":
                self.get_contacts_func = self.gsettings_mgr.get_trusted_sms_lock_device
                self.set_contacts_func = self.gsettings_mgr.set_trusted_sms_lock_device
                self.get_enabled_func = self.gsettings_mgr.get_trusted_sms_lock_device_enabled
                self.set_enabled_func = self.gsettings_mgr.set_trusted_sms_lock_device_enabled
                self.get_seed_func = self.gsettings_mgr.get_trusted_sms_lock_device_totp_seed
                self.set_seed_func = self.gsettings_mgr.set_trusted_sms_lock_device_totp_seed
                self.remove_seed_func = self.gsettings_mgr.remove_trusted_sms_lock_device_totp_seed

            parent_win = getattr(parent, 'parent_win', None)
            if parent_win is not None:
                self.app_window = parent_win.main_window
            else:
                self.app_window = getattr(parent, 'main_window', None)

            title = _("Set \"Find my Telephony\"")
            if mode == "trusted_sms_silent_callback":
                title = _("Set \"Trusted Callback\"")
            elif mode == "trusted_sms_relay":
                title = _("Set \"SMS -Relay\"")
            elif mode == "trusted_sms_ssh_access":
                title = _("Set \"SMS -tmate\"")
            elif mode == "trusted_sms_lock_device":
                title = _("Set \"SMS Switch Lock Device\"")

            super().__init__(title=title)

            self.local_contacts = []
            try:
                current = self.get_contacts_func() if self.get_contacts_func else None
                if current:
                    for c in current:
                        self.local_contacts.append(c.copy())
            except Exception as e:
                logger.error(
                    f"[TrustedActions] Failed to load initial contacts for mode {self.mode}: {e}")

            view = Adw.ToolbarView()
            self.set_child(view)

            header = Adw.HeaderBar(
                show_end_title_buttons=False, show_start_title_buttons=False)

            btn_save = Gtk.Button(label=_("Save"))
            btn_save.add_css_class("suggested-action")
            btn_save.connect("clicked", lambda b: GLib.idle_add(
                lambda: self._on_save_clicked(b) or False))
            header.pack_end(btn_save)

            view.add_top_bar(header)

            self.overlay = Adw.ToastOverlay()
            view.set_content(self.overlay)

            main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.overlay.set_child(main_box)

            self.enable_grp = Adw.PreferencesGroup()
            self.enable_grp.set_margin_start(12)
            self.enable_grp.set_margin_end(12)
            self.enable_grp.set_margin_top(12)

            self.enable_row = Adw.ActionRow(title=_("Enable Feature"))
            self.enable_switch = Gtk.Switch()
            self.enable_switch.set_valign(Gtk.Align.CENTER)
            self.enable_row.add_suffix(self.enable_switch)
            self.enable_grp.add(self.enable_row)

            main_box.append(self.enable_grp)

            self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.content_box.set_vexpand(True)
            main_box.append(self.content_box)

            search_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=6)
            search_box.set_margin_start(12)
            search_box.set_margin_end(12)
            search_box.set_margin_top(12)
            search_box.set_margin_bottom(6)

            self.search_entry = Gtk.SearchEntry(
                placeholder_text=_("Find and add contacts..."))
            search_box.append(self.search_entry)

            self.search_timer = None
            self.search_token = 0

            self.content_box.append(search_box)

            info_text = _(
                "Contacts can trigger an action by sending a secret SMS message. Add contacts below and set their secret trigger phrase.")
            if mode == "trusted_sms_location_request":
                info_text = _(
                    "Trusted contacts can request your location by sending a secret SMS message. TOTP is mandatory for this feature to work. Add contacts below and set their secret trigger phrase. Example template: 'SECRET TOTP' Real life example: 'Gorilla 123456'")
            elif mode == "trusted_sms_silent_callback":
                info_text = _(
                    "Trusted contacts can silently trigger a callback by sending a secret SMS message. TOTP is mandatory for this feature to work. Add contacts below and set their secret trigger phrase. Example template: 'SECRET TOTP' Real life example: 'Gorilla 123456'")
            elif mode == "trusted_sms_relay":
                info_text = _(
                    "Trusted contacts can send an SMS relay. TOTP is mandatory for this feature to work. Add contacts below and set their secret trigger phrase. Example template: 'SECRET TOTP +1234567890 MESSAGE' Real life example: 'Gorilla 123456 +1234567890 Hello'")
            elif mode == "trusted_sms_ssh_access":
                info_text = _(
                    "Trusted contacts can start a tmate SSH session and receive the SSH access string by sending a secret SMS message. TOTP is mandatory for this feature to work. Add contacts below and set their secret trigger phrase. Example template: 'SECRET TOTP' Real life example: 'Gorilla 123456'")
            elif mode == "trusted_sms_lock_device":
                info_text = _(
                    "Trusted contacts can lock your device: the SIM PIN is changed to a new one and locked, and the phone powers off. TOTP is mandatory for this feature to work. Add contacts below and set their secret trigger phrase. Example template: 'SECRET TOTP SIM_CURRENT_PIN SIM_NEW_PIN SUDO_PASSWORD' Real life example: 'Gorilla 123456 0000 1234 SupportYourLocalFootball'")

            lbl_info = Gtk.Label(label=info_text, wrap=True)
            lbl_info.add_css_class("dim-label")
            lbl_info.set_margin_start(12)
            lbl_info.set_margin_end(12)
            lbl_info.set_margin_bottom(12)
            lbl_info.set_xalign(0)
            self.content_box.append(lbl_info)

            self.stack = Gtk.Stack()
            self.stack.set_vexpand(True)
            self.content_box.append(self.stack)

            self.page_list = Adw.PreferencesPage()

            if True:
                self.totp_grp = Adw.PreferencesGroup()
                self.page_list.add(self.totp_grp)

                self.totp_btn = Gtk.Button()
                self.totp_btn.add_css_class("suggested-action")
                self.totp_btn.set_margin_bottom(8)
                self.totp_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._show_totp_setup() or False))
                self.totp_grp.add(self.totp_btn)
                self._update_totp_button_label()

            self.grp_list = Adw.PreferencesGroup()
            self.page_list.add(self.grp_list)
            self.stack.add_named(self.page_list, "list")

            self.search_results_list = Gtk.ListBox(css_classes=["boxed-list"])
            self.search_results_list.set_margin_start(12)
            self.search_results_list.set_margin_end(12)
            self.search_results_list.set_margin_top(12)
            self.search_results_list.set_margin_bottom(12)
            self.search_results_list.set_selection_mode(Gtk.SelectionMode.NONE)

            scroll_search = Gtk.ScrolledWindow()
            scroll_search.set_child(self.search_results_list)
            self.stack.add_named(scroll_search, "search")

            self.stack.set_visible_child_name("list")

            self.search_entry.connect(
                "search-changed", self._on_search_changed)
            self.search_results_list.connect(
                "row-activated", self._on_result_activated)

            self.eds_signals = []
            self.db_signals = []
            self._connect_external_signals()

            self.connect("map", self._on_map)
            self.connect("unmap", self._on_unmap)

            self._refresh_list()

            enabled_getter = self.get_enabled_func
            if enabled_getter:
                self.enable_switch.set_active(enabled_getter())
                self._update_visibility()

            self.enable_switch.connect("notify::active", self._on_enable_switch_toggled)

            self._connect_gsettings_signal()

        except Exception as e:
            logger.error(f"[TrustedActionsListWindow] Init error: {e}")

    def _connect_external_signals(self):
        """Connect the external refresh signals if not already connected."""
        if self.db_signals:
            return
        if self.app_window is not None:
            sig_id = self.app_window.db.connect(
                'blocklist-updated', lambda *args: GLib.idle_add(self._refresh_list))
            self.db_signals.append((self.app_window.db, sig_id))

    def _connect_gsettings_signal(self):
        """Connect the gsettings listener if not already connected."""
        if self.gsettings_handler_id is not None:
            return
        self.gsettings_handler_id = self.gsettings_mgr.gsettings.connect("changed", self._on_gsettings_changed)

    def _on_map(self, widget):
        """Reconnect external signals and catch up when shown again."""
        self._connect_external_signals()
        self._connect_gsettings_signal()
        self._refresh_list()

    def _on_enable_switch_toggled(self, switch, gparam):
        is_active = switch.get_active()
        setter = self.set_enabled_func
        if setter:
            setter(is_active)
        self._update_visibility()

    def _update_visibility(self):
        is_active = self.enable_switch.get_active()
        self.content_box.set_visible(is_active)

    def _on_gsettings_changed(self, settings, key):
        expected_key = self.mode.replace("_", "-") + "-enabled"
        if key == expected_key:
            enabled_getter = self.get_enabled_func
            if enabled_getter:
                is_active = enabled_getter()
                if self.enable_switch.get_active() != is_active:
                    self.enable_switch.set_active(is_active)

    def _update_totp_button_label(self):
        if not (self.totp_btn is not None):
            return

        secret = self.gsettings_mgr.secret_manager.get_secret(self.mode)
        if secret:
            self.totp_btn.set_label(_("View or edit TOTP"))
        else:
            self.totp_btn.set_label(_("Setup TOTP"))

    def _on_unmap(self, widget):
        if self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

        if (self.gsettings_handler_id is not None) and self.gsettings_mgr.gsettings.handler_is_connected(self.gsettings_handler_id):
            self.gsettings_mgr.gsettings.disconnect(self.gsettings_handler_id)
        self.gsettings_handler_id = None

        for obj, sig_id in self.eds_signals:
            if obj.handler_is_connected(sig_id):
                obj.disconnect(sig_id)
        self.eds_signals.clear()

        for obj, sig_id in self.db_signals:
            if obj.handler_is_connected(sig_id):
                obj.disconnect(sig_id)
        self.db_signals.clear()

    def close(self):
        """Pop this page off the settings navigation."""
        nav = self.get_ancestor(Adw.NavigationView)
        if nav:
            nav.pop()

    def _on_save_clicked(self, btn):
        """Handle save button click."""
        if self.set_contacts_func:
            self.set_contacts_func(self.local_contacts)
        GLib.idle_add(lambda: self.close() or False)

    def _refresh_list(self):
        """Refresh the list of contacts."""
        if (self.grp_list is not None) and self.grp_list:
            self.page_list.remove(self.grp_list)

        self.grp_list = Adw.PreferencesGroup()
        self.page_list.add(self.grp_list)

        if not self.local_contacts:
            pass

        for c in self.local_contacts:
            self._create_contact_row(c)

    def _create_contact_row(self, contact_data):
        """Create a row for a contact."""
        name = contact_data.get("name", _("Unknown"))
        number = contact_data.get("number", "")
        secret = contact_data.get("secret", "")

        row = Adw.PreferencesRow()

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        main_box.set_margin_top(10)
        main_box.set_margin_bottom(10)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)

        lbl_name = Gtk.Label(label=name, xalign=0)
        lbl_name.add_css_class("heading")
        lbl_name.set_ellipsize(Pango.EllipsizeMode.END)
        main_box.append(lbl_name)

        lbl_num = Gtk.Label(label=number, xalign=0)
        lbl_num.add_css_class("caption")
        lbl_num.add_css_class("dim-label")
        lbl_num.set_ellipsize(Pango.EllipsizeMode.END)
        main_box.append(lbl_num)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_box.set_margin_top(4)

        entry = Gtk.PasswordEntry()
        entry.set_property("show-peek-icon", True)
        entry.set_property("placeholder-text", _("Secret message"))
        entry.set_text(secret)
        entry.set_hexpand(True)

        entry.connect("changed", lambda e: self._on_secret_changed(
            contact_data, e.get_text()))

        action_box.append(entry)

        btn_del = Gtk.Button(icon_name="user-trash-symbolic")
        btn_del.add_css_class("flat")
        btn_del.add_css_class("circular")
        btn_del.set_tooltip_text(_("Remove Contact"))
        btn_del.connect(
            "clicked", lambda b: self._delete_contact(contact_data))

        action_box.append(btn_del)

        main_box.append(action_box)

        row.set_child(main_box)
        self.grp_list.add(row)

    def _on_secret_changed(self, contact_data, new_secret):
        """Handle secret text change."""
        contact_data["secret"] = new_secret

    def _delete_contact(self, contact_data):
        """Delete a contact from the list."""
        target_num = normalize_number(contact_data.get("number", ""))

        self.local_contacts = [c for c in self.local_contacts if normalize_number(
            c.get("number", "")) != target_num]

        GLib.idle_add(lambda: self._refresh_list())
        self.overlay.add_toast(Adw.Toast.new(
            _("Contact removed (Click Save to commit)")))

    def _on_search_changed(self, entry):
        """Handle search text change."""
        if self.search_timer:
            GLib.source_remove(self.search_timer)
            self.search_timer = None

        self.search_timer = GLib.timeout_add(200, self._perform_search)

    def _perform_search(self):
        """Execute search."""
        self.search_timer = None
        query = self.search_entry.get_text().strip()
        if not query:
            self.stack.set_visible_child_name("list")
            return False

        self.stack.set_visible_child_name("search")

        self.search_token += 1
        current_token = self.search_token

        def _bg_fetch():
            contacts = self.eds.search_contacts(query, limit=30)
            return contacts

        def _on_done(contacts):
            if self.search_token != current_token:
                return False
            self._build_search_results(contacts, query)
            return False

        def _bg_task():
            res = _bg_fetch()
            GLib.idle_add(lambda: _on_done(res))

        run_in_background(_bg_task)
        return False

    def _is_result_added(self, normalized_number):
        """Return True when the normalized number is already in the local list."""
        for existing in self.local_contacts:
            if normalize_number(existing.get("number", "")) == normalized_number:
                return True
        return False

    def _build_search_results(self, contacts, query):
        """Build search results UI."""
        populate_contact_search_results(
            self.search_results_list,
            contacts,
            self.eds,
            is_added=self._is_result_added,
            on_add=lambda row: self._on_result_activated(None, row),
            translate_label=translate_phone_label,
            unknown_name=_("Unknown")
        )

    def _on_result_activated(self, listbox, row):
        """Handle activation of a search result."""
        if not row.get_sensitive():
            return

        data = getattr(row, "contact_data", None)
        if data is None:
            return
        name = data.get("name")
        raw_number = data.get("number")
        number = normalize_number(raw_number)

        for t in self.local_contacts:
            if normalize_number(t.get("number")) == number:
                self.overlay.add_toast(Adw.Toast.new(
                    _("Contact already in trusted list")))
                return

        new_entry = {"name": name, "number": raw_number, "secret": ""}
        self.local_contacts.insert(0, new_entry)

        def _update_ui():
            self.search_entry.set_text("")
            self.stack.set_visible_child_name("list")
            self._refresh_list()
            return False

        GLib.idle_add(_update_ui)

        self.overlay.add_toast(Adw.Toast.new(_("Contact added")))

    def _show_totp_setup(self):
        """Show the TOTP setup dialog."""
        seed_getter = self.get_seed_func
        if not seed_getter:
            return

        seed = seed_getter()
        if not seed:
            seed = self.gsettings_mgr.generate_totp_seed()

        self._open_totp_dialog(seed)

    def _open_totp_dialog(self, seed):
        action_name = self.mode.replace("trusted_sms_", "").replace("_", " ").title()
        uri = self.gsettings_mgr.get_totp_uri(seed, action_name)

        dialog = Adw.Window(title=_("TOTP Setup"), transient_for=self.get_root(), modal=True)
        dialog.set_default_size(350, 450)

        overlay = Adw.ToastOverlay()
        dialog.set_content(overlay)

        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay.set_child(main_vbox)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda b: GLib.idle_add(lambda: dialog.close() or False))
        header.pack_start(btn_cancel)

        btn_save = Gtk.Button(label=_("Save"))
        btn_save.add_css_class("suggested-action")
        def _on_save(b):
            seed_setter = self.set_seed_func
            if seed_setter:
                seed_setter(seed)
            dialog.close()
        def _on_save_wrapper(b):
            _on_save(b)
            self._update_totp_button_label()
            return False

        btn_save.connect("clicked", lambda b: GLib.idle_add(lambda: _on_save_wrapper(b)))
        header.pack_end(btn_save)

        main_vbox.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        main_vbox.append(scroll)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        scroll.set_child(content_box)

        lbl_inst = Gtk.Label(
            label=_("Share this code with your trusted contact. They should add it to an authenticator app such as Gnome Authenticator.\n\nIf entering manually, ensure the following settings:\n• Algorithm: SHA1\n• Digits: 6\n• Interval: 60 seconds\n\nEach action has its own code. When triggering an action by SMS, they must append the current 6-digit code from their authenticator after the keyword."),
            wrap=True
        )
        content_box.append(lbl_inst)

        seed_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        seed_box.set_halign(Gtk.Align.CENTER)

        lbl_seed = Gtk.Label(label=seed)
        lbl_seed.add_css_class("heading")
        lbl_seed.set_selectable(True)
        seed_box.append(lbl_seed)

        content_box.append(seed_box)

        import qrcode
        img = qrcode.make(uri)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        bytes_data = GLib.Bytes.new(buf.getvalue())
        stream = Gio.MemoryInputStream.new_from_bytes(bytes_data)
        pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)

        pic = Gtk.Picture.new_for_paintable(texture)
        pic.set_can_shrink(True)
        content_box.append(pic)

        btn_copy = Gtk.Button(label=_("Copy TOTP key"))
        btn_copy.add_css_class("suggested-action")

        def _copy_seed(b):
            clipboard = dialog.get_display().get_clipboard()
            clipboard.set(seed)
            overlay.add_toast(Adw.Toast.new(_("Copied to clipboard")))

        btn_copy.connect("clicked", _copy_seed)
        content_box.append(btn_copy)

        btn_regen = Gtk.Button(label=_("Regenerate"))
        btn_regen.add_css_class("destructive-action")
        btn_regen.connect("clicked", lambda b: GLib.idle_add(lambda: self._confirm_regen(dialog) or False))
        content_box.append(btn_regen)

        secret = self.gsettings_mgr.secret_manager.get_secret(self.mode)
        if secret:
            btn_remove = Gtk.Button(label=_("Remove current TOTP"))
            btn_remove.add_css_class("destructive-action")
            btn_remove.connect("clicked", lambda b: GLib.idle_add(lambda: self._confirm_remove(dialog) or False))
            content_box.append(btn_remove)

        dialog.present()

    def _confirm_remove(self, parent_dialog):
        confirm = Adw.MessageDialog(
            heading=_("Remove TOTP Key?"),
            body=_("Are you sure you want to remove the TOTP key? This action will disable TOTP verification for this feature."),
            transient_for=parent_dialog,
        )
        confirm.add_response("cancel", _("Cancel"))
        confirm.add_response("remove", _("Yes"))
        confirm.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dialog, response):
            if response == "remove":
                remover = self.remove_seed_func
                if remover:
                    remover()
                self._update_totp_button_label()
                parent_dialog.close()

        confirm.connect("response", on_response)
        confirm.present()

    def _confirm_regen(self, parent_dialog):
        confirm = Adw.MessageDialog(
            heading=_("Regenerate Code?"),
            body=_("This will invalidate the current code in your contact's authenticator app. Are you sure?"),
            transient_for=parent_dialog,
        )
        confirm.add_response("cancel", _("Cancel"))
        confirm.add_response("regenerate", _("Regenerate"))
        confirm.set_response_appearance("regenerate", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dialog, response):
            if response == "regenerate":
                new_seed = self.gsettings_mgr.generate_totp_seed()
                parent_dialog.close()
                GLib.idle_add(lambda: self._open_totp_dialog(new_seed) or False)

        confirm.connect("response", on_response)
        confirm.present()
