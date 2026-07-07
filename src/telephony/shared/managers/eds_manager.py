# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import threading
import json
import time
import uuid
import configparser
from functools import partial
from gettext import gettext as _

from telephony.shared.utils.log_utils import logger

from gi.repository import Gio, GLib, GObject

from telephony.shared.utils.thread_utils import run_in_background
from telephony.shared.utils.phone_utils import normalize_number
from telephony.shared.utils.vcard_utils import parse_vcard_string, unfold_vcard, is_property

EDS_SOURCES_BUS_NAME = "org.gnome.evolution.dataserver.Sources5"
EDS_SOURCES_PATH = "/org/gnome/evolution/dataserver/SourceManager"
EDS_SOURCE_MANAGER_IFACE = "org.gnome.evolution.dataserver.SourceManager"
EDS_SOURCE_IFACE = "org.gnome.evolution.dataserver.Source"
EDS_SOURCE_WRITABLE_IFACE = "org.gnome.evolution.dataserver.Source.Writable"
EDS_STATUS_CONNECTED = "connected"
EDS_CONNECTION_STATUS_PROP = "ConnectionStatus"
EDS_STATUS_SETTLE_MS = 1500
EDS_SOURCE_REMOVABLE_IFACE = "org.gnome.evolution.dataserver.Source.Removable"
EDS_BOOK_BUS_NAME = "org.gnome.evolution.dataserver.AddressBook10"
EDS_FACTORY_PATH = "/org/gnome/evolution/dataserver/AddressBookFactory"
EDS_FACTORY_IFACE = "org.gnome.evolution.dataserver.AddressBookFactory"
EDS_BOOK_IFACE = "org.gnome.evolution.dataserver.AddressBook"
EDS_VIEW_IFACE = "org.gnome.evolution.dataserver.AddressBookView"
EDS_MATCH_ALL_QUERY = '(contains "x-evolution-any-field" "")'
VCARD_BEGIN = "BEGIN:VCARD"
EDS_CALL_TIMEOUT_MS = 25000
VIEW_FLAG_NOTIFY_INITIAL = 1
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
EVOLUTION_SCHEMA_ID = "org.gnome.evolution"
DEFAULT_BOOK_KEY = "default-address-book"
SYSTEM_BOOK_UID = "system-address-book"
LOCAL_BACKEND_NAMES = ("local", "")
CREATE_APPEAR_TIMEOUT_SECONDS = 5
BOOK_KEYFILE_TEMPLATE = """[Data Source]
DisplayName={name}
Enabled=true
Parent=local

[Address Book]
BackendName=local
"""


class EdsManager(GObject.Object):
    __gsignals__ = {
        "contacts-loaded": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "address-books-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, owns_live_views=True):
        super().__init__()
        self.owns_live_views = owns_live_views
        self.sources = {}
        self.sources_lock = threading.Lock()
        self.reload_lock = threading.Lock()

        self.cache = {}
        self.cache_lock = threading.Lock()
        self._cache_loaded_sources = set()
        self._source_ranks = {}

        self.lookup_map = {}

        self.db_ref = None
        self.gsettings_mgr = None
        self.sources_provider = None
        self.bus = None
        self.books = {}
        self.books_lock = threading.Lock()
        self._book_locks = {}
        self.book_openers = {}
        self.registry_watched = False
        self._registry_sub_ids = []
        self._status_settle_id = 0
        self._registry_paths = {}
        self.is_ready = False
        self._sources_info_cache = None
        self._sources_fetch_running = False
        self._last_books_signature = None
        self._applied_config_json = None

    def set_db(self, db_manager, gsettings_mgr):
        self.db_ref = db_manager
        self.gsettings_mgr = gsettings_mgr
        self.gsettings_mgr.gsettings.connect(
            "changed::address-book-sources", self.on_sources_config_changed)

        run_in_background(self.load_cache_initial)
        if self.owns_live_views:
            run_in_background(self.start_live_sources)

    def on_sources_config_changed(self, _settings, _key):
        """Reload when another process rewrote the book configuration. dconf reports the change to every process; the one that wrote it already reloaded and recognizes its own value, so only the others rebuild, and the daemon's live views follow a settings change without a restart."""
        current = self.gsettings_mgr.get_setting("address_book_sources")
        if current == self._applied_config_json:
            return
        logger.info("[EDS] Address book configuration changed elsewhere, reloading")
        self.reload()

    def load_cache_initial(self):
        saved_config = self.saved_config_entries()
        if not saved_config:
            return
        saved_config.sort(key=lambda x: x.get("rank", 999))

        def load_sources_async():
            with self.reload_lock:
                for item in saved_config:
                    if item.get("enabled", True):
                        self.load_from_local_db(item["uid"], item.get("rank", 0))
            if not self.owns_live_views:
                self.rebuild_lookup_map()
                self.is_ready = True
            GLib.idle_add(self.emit, "contacts-loaded")

        run_in_background(load_sources_async)

    def saved_config_entries(self):
        saved_json = self.gsettings_mgr.get_setting("address_book_sources") if self.gsettings_mgr else None
        if not saved_json:
            return []
        try:
            return json.loads(saved_json)
        except Exception as e:
            logger.warning(f"[EDS] Saved config unreadable: {e}")
            return []

    def get_bus(self):
        if self.bus is None:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return self.bus

    def registry_objects(self):
        reply = self.get_bus().call_sync(
            EDS_SOURCES_BUS_NAME, EDS_SOURCES_PATH, OBJECT_MANAGER_IFACE,
            "GetManagedObjects", None,
            GLib.VariantType("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE,
            EDS_CALL_TIMEOUT_MS, None)
        objects = reply.unpack()[0]

        books = {}
        paths = {}
        for path, ifaces in objects.items():
            props = ifaces.get(EDS_SOURCE_IFACE)
            if not props:
                continue
            uid = props.get("UID", "")
            paths[path] = uid
            parsed = self.parse_source_data(props.get("Data", ""))
            if parsed is None or not parsed["is_addressbook"]:
                continue
            books[uid] = {
                "uid": uid,
                "path": path,
                "name": parsed["name"] or uid,
                "enabled": parsed["enabled"],
                "parent": parsed["parent"],
                "backend": parsed["backend"],
                "is_local": parsed["backend"] in LOCAL_BACKEND_NAMES,
                "removable": EDS_SOURCE_REMOVABLE_IFACE in ifaces,
                "writable": EDS_SOURCE_WRITABLE_IFACE in ifaces,
                "status": (props.get("ConnectionStatus") or None),
            }
        self._registry_paths = paths
        return books

    def parse_source_data(self, data):
        parser = configparser.RawConfigParser(strict=False, interpolation=None)
        try:
            parser.read_string(data)
        except Exception as e:
            logger.debug(f"[EDS] Unreadable source keyfile: {e}")
            return None
        if not parser.has_section("Data Source"):
            return None
        section = parser["Data Source"]
        book = parser["Address Book"] if parser.has_section("Address Book") else {}
        return {
            "name": section.get("DisplayName", ""),
            "enabled": section.get("Enabled", "true").lower() == "true",
            "parent": section.get("Parent", ""),
            "backend": book.get("BackendName", "") if book else None,
            "is_addressbook": parser.has_section("Address Book"),
        }

    def watch_registry(self):
        if self.registry_watched:
            return
        self.registry_watched = True
        bus = self.get_bus()
        self._registry_sub_ids.append(bus.signal_subscribe(
            EDS_SOURCES_BUS_NAME, OBJECT_MANAGER_IFACE, "InterfacesAdded",
            EDS_SOURCES_PATH, None, Gio.DBusSignalFlags.NONE,
            self.on_registry_added))
        self._registry_sub_ids.append(bus.signal_subscribe(
            EDS_SOURCES_BUS_NAME, OBJECT_MANAGER_IFACE, "InterfacesRemoved",
            EDS_SOURCES_PATH, None, Gio.DBusSignalFlags.NONE,
            self.on_registry_removed))
        self._registry_sub_ids.append(bus.signal_subscribe(
            EDS_SOURCES_BUS_NAME, PROPERTIES_IFACE, "PropertiesChanged",
            None, EDS_SOURCE_IFACE, Gio.DBusSignalFlags.NONE,
            self.on_source_properties_changed))

    def on_source_properties_changed(self, _conn, _sender, _path, _iface, _member, params):
        try:
            changed_iface, changed, invalidated = params.unpack()
        except Exception as e:
            logger.debug(f"[EDS] Bad property payload: {e}")
            return

        if changed_iface != EDS_SOURCE_IFACE:
            return
        if EDS_CONNECTION_STATUS_PROP not in changed and EDS_CONNECTION_STATUS_PROP not in invalidated:
            return

        self.invalidate_sources_info()
        if not self.owns_live_views or self._status_settle_id:
            return
        self._status_settle_id = GLib.timeout_add(EDS_STATUS_SETTLE_MS,
                                                  self.announce_books_after_status)

    def announce_books_after_status(self):
        self._status_settle_id = 0
        run_in_background(self.emit_books_changed_if_moved)
        return False

    def on_registry_added(self, _conn, _sender, _path, _iface, _member, params):
        self.invalidate_sources_info()
        if not self.owns_live_views:
            return
        try:
            _path_added, ifaces = params.unpack()
        except Exception as e:
            logger.debug(f"[EDS] Bad addition payload: {e}")
            return
        props = ifaces.get(EDS_SOURCE_IFACE)
        if not props:
            return
        uid = props.get("UID", "")
        parsed = self.parse_source_data(props.get("Data", ""))
        if not uid or parsed is None or not parsed["is_addressbook"]:
            return
        with self.sources_lock:
            known = uid in self.sources
        if known:
            return
        logger.info(f"[EDS] Address book {uid} appeared, discovering it")
        self.reload()

    def on_registry_removed(self, _conn, _sender, _path, _iface, _member, params):
        self.invalidate_sources_info()
        try:
            path, ifaces = params.unpack()
        except Exception as e:
            logger.debug(f"[EDS] Bad removal payload: {e}")
            return
        if EDS_SOURCE_IFACE not in ifaces:
            return
        uid = self._registry_paths.pop(path, None)
        if not uid:
            return

        with self.sources_lock:
            if uid not in self.sources:
                return

        def task():
            logger.info(f"[EDS] Address book {uid} was removed, dropping its contacts")
            self.remove_source(uid)
            GLib.idle_add(self.emit, "contacts-loaded")
            self.emit_books_changed_if_moved()

        run_in_background(task)

    def invalidate_sources_info(self):
        if not self.owns_live_views and self.sources_provider:
            self.start_sources_fetch()
            return
        self._sources_info_cache = None

    def book_lock(self, uid):
        with self.books_lock:
            lock = self._book_locks.get(uid)
            if lock is None:
                lock = threading.Lock()
                self._book_locks[uid] = lock
            return lock

    def open_book(self, uid):
        with self.book_lock(uid):
            return self.open_book_locked(uid)

    def open_book_locked(self, uid):
        with self.books_lock:
            record = self.books.get(uid)
        if record:
            return record
        bus = self.get_bus()
        reply = bus.call_sync(
            EDS_BOOK_BUS_NAME, EDS_FACTORY_PATH, EDS_FACTORY_IFACE,
            "OpenAddressBook", GLib.Variant("(s)", (uid,)),
            GLib.VariantType("(ss)"), Gio.DBusCallFlags.NONE,
            EDS_CALL_TIMEOUT_MS, None)
        book_path, book_bus_name = reply.unpack()
        bus.call_sync(
            book_bus_name, book_path, EDS_BOOK_IFACE,
            "Open", None, GLib.VariantType("(as)"),
            Gio.DBusCallFlags.NONE, EDS_CALL_TIMEOUT_MS, None)
        record = {"uid": uid, "path": book_path, "bus_name": book_bus_name,
                  "view_path": None, "sub_ids": []}
        record = self.remember_book(uid, record)
        logger.info(f"[EDS] Book open for {uid} at {book_path}")
        return record

    def book_call(self, uid, method, params, reply_type):
        record = self.open_book(uid)
        try:
            return self.get_bus().call_sync(
                record["bus_name"], record["path"], EDS_BOOK_IFACE,
                method, params, reply_type, Gio.DBusCallFlags.NONE,
                EDS_CALL_TIMEOUT_MS, None)
        except GLib.Error as e:
            if not self.is_gone_error(e):
                raise
            logger.warning(f"[EDS] {method} on {uid} lost its book, reopening: {e}")
            with self.book_lock(uid):
                current = None
                with self.books_lock:
                    current = self.books.get(uid)
                if current is record or current is None:
                    self.close_book_locked(uid)
                    current = self.open_book_locked(uid)
            return self.get_bus().call_sync(
                current["bus_name"], current["path"], EDS_BOOK_IFACE,
                method, params, reply_type, Gio.DBusCallFlags.NONE,
                EDS_CALL_TIMEOUT_MS, None)

    def remember_book(self, uid, record):
        with self.books_lock:
            existing = self.books.get(uid)
            if existing:
                return existing
            self.books[uid] = record
            return record

    def open_book_async(self, uid, callback):
        with self.books_lock:
            record = self.books.get(uid)
        if record:
            callback(record, None)
            return

        waiting = self.book_openers.get(uid)
        if waiting is not None:
            waiting.append(callback)
            return

        self.book_openers[uid] = [callback]
        self.get_bus().call(
            EDS_BOOK_BUS_NAME, EDS_FACTORY_PATH, EDS_FACTORY_IFACE,
            "OpenAddressBook", GLib.Variant("(s)", (uid,)),
            GLib.VariantType("(ss)"), Gio.DBusCallFlags.NONE,
            EDS_CALL_TIMEOUT_MS, None, self.on_address_book_opened, uid)

    def on_address_book_opened(self, connection, result, uid):
        try:
            book_path, book_bus_name = connection.call_finish(result).unpack()
        except GLib.Error as e:
            self.finish_book_open(uid, None, e)
            return

        connection.call(
            book_bus_name, book_path, EDS_BOOK_IFACE,
            "Open", None, GLib.VariantType("(as)"),
            Gio.DBusCallFlags.NONE, EDS_CALL_TIMEOUT_MS, None,
            self.on_book_ready, (uid, book_path, book_bus_name))

    def on_book_ready(self, connection, result, user_data):
        uid, book_path, book_bus_name = user_data
        try:
            connection.call_finish(result)
        except GLib.Error as e:
            self.finish_book_open(uid, None, e)
            return

        record = {"uid": uid, "path": book_path, "bus_name": book_bus_name,
                  "view_path": None, "sub_ids": []}
        record = self.remember_book(uid, record)
        logger.info(f"[EDS] Book open for {uid} at {book_path}")
        self.finish_book_open(uid, record, None)

    def finish_book_open(self, uid, record, error):
        for callback in self.book_openers.pop(uid, []):
            callback(record, error)

    def book_call_async(self, uid, method, params, reply_type, callback):
        def opened(record, error):
            if error:
                callback(None, error)
                return
            self.get_bus().call(
                record["bus_name"], record["path"], EDS_BOOK_IFACE,
                method, params, reply_type, Gio.DBusCallFlags.NONE,
                EDS_CALL_TIMEOUT_MS, None, answered, record)

        def answered(connection, result, record):
            try:
                callback(connection.call_finish(result), None)
            except GLib.Error as e:
                if not self.is_gone_error(e):
                    callback(None, e)
                    return
                logger.warning(f"[EDS] {method} on {uid} lost its book, reopening: {e}")
                self.forget_dead_book(uid, record)
                self.open_book_async(uid, reopened)

        def reopened(record, error):
            if error:
                callback(None, error)
                return
            self.get_bus().call(
                record["bus_name"], record["path"], EDS_BOOK_IFACE,
                method, params, reply_type, Gio.DBusCallFlags.NONE,
                EDS_CALL_TIMEOUT_MS, None, retried, None)

        def retried(connection, result, _user_data):
            try:
                callback(connection.call_finish(result), None)
            except GLib.Error as e:
                callback(None, e)

        self.open_book_async(uid, opened)

    def forget_dead_book(self, uid, record):
        with self.books_lock:
            current = self.books.get(uid)
        if current is record or current is None:
            self.close_book(uid)

    def is_gone_error(self, error):
        gone = ("org.freedesktop.DBus.Error.ServiceUnknown",
                "org.freedesktop.DBus.Error.NameHasNoOwner",
                "org.freedesktop.DBus.Error.Disconnected")
        try:
            return Gio.DBusError.get_remote_error(error) in gone
        except Exception as e:
            logger.debug(f"[EDS] Error name lookup failed: {e}")
            return False

    def close_book(self, uid):
        with self.book_lock(uid):
            self.close_book_locked(uid)

    def close_book_locked(self, uid):
        with self.books_lock:
            record = self.books.pop(uid, None)
        if not record:
            return
        bus = self.get_bus()
        for sub_id in record["sub_ids"]:
            try:
                bus.signal_unsubscribe(sub_id)
            except Exception as e:
                logger.debug(f"[EDS] Unsubscribe error (ignorable): {e}")
        if record["view_path"]:
            def disposed(conn, result, _data):
                try:
                    conn.call_finish(result)
                except Exception as e:
                    logger.debug(f"[EDS] View dispose error (ignorable): {e}")

            bus.call(
                record["bus_name"], record["view_path"], EDS_VIEW_IFACE,
                "Dispose", None, GLib.VariantType("()"),
                Gio.DBusCallFlags.NONE, EDS_CALL_TIMEOUT_MS, None, disposed, None)

    def start_view(self, uid):
        record = self.open_book(uid)
        reply = self.book_call(uid, "GetView",
                                GLib.Variant("(s)", (EDS_MATCH_ALL_QUERY,)),
                                GLib.VariantType("(o)"))
        record["view_path"] = reply.unpack()[0]

        bus = self.get_bus()
        for member, handler in (("ObjectsAdded", self.on_view_added),
                                ("ObjectsModified", self.on_view_modified),
                                ("ObjectsRemoved", self.on_view_removed)):
            record["sub_ids"].append(bus.signal_subscribe(
                record["bus_name"], EDS_VIEW_IFACE, member, record["view_path"],
                None, Gio.DBusSignalFlags.NONE, partial(handler, source_uid=uid)))

        bus.call_sync(
            record["bus_name"], record["view_path"], EDS_VIEW_IFACE,
            "SetFlags", GLib.Variant("(u)", (VIEW_FLAG_NOTIFY_INITIAL,)),
            GLib.VariantType("()"), Gio.DBusCallFlags.NONE,
            EDS_CALL_TIMEOUT_MS, None)
        bus.call_sync(
            record["bus_name"], record["view_path"], EDS_VIEW_IFACE,
            "Start", None, GLib.VariantType("()"),
            Gio.DBusCallFlags.NONE, EDS_CALL_TIMEOUT_MS, None)
        logger.info(f"[EDS] Live view started for {uid}")

    def vcards_from(self, params, source_uid):
        try:
            objects = list(params.unpack()[0])
        except Exception as e:
            logger.error(f"[EDS] Bad view payload for {source_uid}: {e}")
            return []
        return [item for item in objects if VCARD_BEGIN in item.upper()]

    def on_view_added(self, _conn, _sender, _path, _iface, _member, params, source_uid):
        vcards = self.vcards_from(params, source_uid)
        run_in_background(self.handle_backend_update, vcards, source_uid)

    def on_view_modified(self, _conn, _sender, _path, _iface, _member, params, source_uid):
        vcards = self.vcards_from(params, source_uid)
        run_in_background(self.handle_backend_update, vcards, source_uid)

    def on_view_removed(self, _conn, _sender, _path, _iface, _member, params, source_uid):
        try:
            uids = list(params.unpack()[0])
        except Exception as e:
            logger.error(f"[EDS] Bad removal payload for {source_uid}: {e}")
            return
        self.on_objects_removed(uids, source_uid)

    def start_live_sources(self):
        self.watch_registry()
        saved_config_json = self.gsettings_mgr.get_setting("address_book_sources") if self.gsettings_mgr else None
        saved_config = self.saved_config_entries()
        enabled = [item for item in saved_config if item.get("enabled", True)]

        if enabled and all("name" in item for item in enabled):
            try:
                with self.reload_lock:
                    for item in sorted(enabled, key=lambda x: x.get("rank", 999)):
                        self.init_wire_source(item["uid"], item.get("rank", 0), item["name"])
                self._applied_config_json = saved_config_json
                self.rebuild_lookup_map()
                self.is_ready = True
                GLib.idle_add(self.emit, "contacts-loaded")
                with self.books_lock:
                    open_books = len(self.books)
                logger.info(f"[EDS] {open_books} book(s) live over the wire")
                return
            except Exception as e:
                logger.warning(f"[EDS] Configured book setup failed, rediscovering: {e}")
                self.teardown_books()

        with self.reload_lock:
            self.load_sources_config_locked()

    def teardown_books(self):
        with self.books_lock:
            uids = list(self.books.keys())
        for uid in uids:
            self.close_book(uid)
        with self.sources_lock:
            self.sources = {}

    def init_wire_source(self, uid, rank, name):
        self.load_from_local_db(uid, rank)
        self.open_book(uid)
        comp_uids = [self.make_composite_uid(uid, real_uid) for real_uid in self.list_book_uids(uid)]
        if comp_uids or not self.has_cached_contacts(uid):
            self.db_ref.sync_deleted_contacts(uid, comp_uids)
        else:
            logger.warning(f"[EDS] Skipping empty deletion sweep for {uid}: backend may not be ready")
        self.start_view(uid)
        with self.sources_lock:
            self.sources[uid] = {"uid": uid, "name": name, "rank": rank}

    def list_book_uids(self, uid):
        reply = self.book_call(uid, "GetContactListUids",
                                GLib.Variant("(s)", (EDS_MATCH_ALL_QUERY,)),
                                GLib.VariantType("(as)"))
        return list(reply.unpack()[0])

    def reload(self):
        def task():
            if not self.owns_live_views:
                self.reload_cache_from_db()
                return
            with self.reload_lock:
                with self.sources_lock:
                    uids = list(self.sources.keys())
                for uid in uids:
                    self.remove_source(uid)
                self.invalidate_sources_info()
                self.load_sources_config_locked()

        run_in_background(task)

    def enabled_registry_books(self):
        return {uid: info for uid, info in self.registry_objects().items()
                if info["enabled"]}

    def load_sources_config_locked(self):
        try:
            self.watch_registry()
            registry_books = self.enabled_registry_books()
            default_uid = self.default_source_uid()

            saved_config = self.saved_config_entries()
            final_sources_list = []
            processed_uids = set()
            current_rank = 0

            for conf in saved_config:
                uid = conf["uid"]
                info = registry_books.get(uid)
                if not info:
                    continue
                is_def = (uid == default_uid)
                final_sources_list.append({
                    "uid": uid,
                    "name": info["name"],
                    "rank": current_rank,
                    "enabled": conf.get("enabled", True) or is_def,
                    "is_system_default": is_def,
                })
                processed_uids.add(uid)
                current_rank += 1

            fresh = [info for uid, info in registry_books.items() if uid not in processed_uids]
            fresh.sort(key=lambda x: (0 if x["uid"] == default_uid else 1, x["name"].lower()))
            for info in fresh:
                final_sources_list.append({
                    "uid": info["uid"],
                    "name": info["name"],
                    "rank": current_rank,
                    "enabled": True,
                    "is_system_default": (info["uid"] == default_uid),
                })
                current_rank += 1

            self.save_sources_config(final_sources_list)

            with self.sources_lock:
                self.sources = {}

            threads = []
            for item in final_sources_list:
                if item["enabled"]:
                    t = threading.Thread(target=self.init_wire_source_safe, args=(item,), daemon=True)
                    t.start()
                    threads.append(t)
            for t in threads:
                t.join()

            enabled_uids = {item["uid"] for item in final_sources_list if item["enabled"]}
            for uid in self.loaded_source_uids() - enabled_uids:
                logger.info(f"[EDS] Dropping cached contacts of unavailable source {uid}")
                self.remove_source(uid)

            self.rebuild_lookup_map()
            self.is_ready = True
            GLib.idle_add(self.emit, "contacts-loaded")
            self.emit_books_changed_if_moved()

        except Exception as e:
            logger.error(f"[EDS] Load Config Error: {e}")

    def init_wire_source_safe(self, item):
        try:
            self.init_wire_source(item["uid"], item["rank"], item["name"])
        except Exception as e:
            logger.error(f"[EDS] Source init failed for {item.get("uid")}: {e}")

    def save_sources_config(self, sources_list):
        to_save = []
        for item in sources_list:
            to_save.append({
                "uid": item["uid"],
                "name": item.get("name", item["uid"]),
                "enabled": item["enabled"],
                "rank": item["rank"]
            })
        try:
            config_json = json.dumps(to_save)
            self._applied_config_json = config_json
            self.gsettings_mgr.set_setting("address_book_sources", config_json)
        except Exception as e:
            logger.error(f"[EDS] Save Config Error: {e}")

    def has_cached_contacts(self, source_uid):
        with self.cache_lock:
            return any(v.get("source_uid") == source_uid for v in self.cache.values())

    def loaded_source_uids(self):
        with self.cache_lock:
            return set(self._cache_loaded_sources)

    def cache_entries_from_db(self, source_uid, rank):
        try:
            contacts = self.db_ref.get_cached_contacts(source_uid)
        except Exception as e:
            logger.error(f"[EDS] DB fetch error: {e}")
            return None

        updates = {}
        lookup_updates = {}
        for c in contacts:
            real_uid = c["uid"]
            if not real_uid:
                continue

            composite_uid = real_uid
            if ":" not in composite_uid:
                composite_uid = f"{source_uid}:{real_uid}"

            c["source_uid"] = source_uid
            updates[composite_uid] = c

            for p_data in c["phones"]:
                norm = normalize_number(p_data[0])
                if norm:
                    lookup_updates.setdefault(norm, []).append(
                        (rank, c["name"], source_uid, composite_uid))

        return updates, lookup_updates

    def load_from_local_db(self, source_uid, rank):
        with self.cache_lock:
            if source_uid in self._cache_loaded_sources:
                logger.debug(f"[EDS] Cache for {source_uid} already loaded, skipping")
                return
            self._cache_loaded_sources.add(source_uid)
            self._source_ranks[source_uid] = rank

        entries = self.cache_entries_from_db(source_uid, rank)
        if entries is None:
            with self.cache_lock:
                self._cache_loaded_sources.discard(source_uid)
            return

        updates, lookup_updates = entries
        with self.cache_lock:
            self.cache.update(updates)
            for norm, items in lookup_updates.items():
                self.lookup_map.setdefault(norm, []).extend(items)

        if updates:
            logger.info(f"[EDS] Loaded {len(updates)} contacts from cache for {source_uid}.")

    def reload_cache_from_db(self):
        self.invalidate_sources_info()
        ranks = {item["uid"]: item.get("rank", 0)
                 for item in self.saved_config_entries()
                 if item.get("enabled", True)}
        with self.reload_lock:
            cache = {}
            lookup_map = {}
            for source_uid, rank in ranks.items():
                entries = self.cache_entries_from_db(source_uid, rank)
                if entries is None:
                    logger.warning(f"[EDS] Keeping the previous contacts: {source_uid} could not be read")
                    return

                updates, lookup_updates = entries
                cache.update(updates)
                for norm, items in lookup_updates.items():
                    lookup_map.setdefault(norm, []).extend(items)

            with self.cache_lock:
                self.cache = cache
                self.lookup_map = lookup_map
                self._cache_loaded_sources = set(ranks.keys())
                self._source_ranks = dict(ranks)

        GLib.idle_add(self.emit, "contacts-loaded")

    def make_composite_uid(self, source_uid, real_uid):
        return f"{source_uid}:{real_uid}"

    def parse_composite_uid(self, composite_uid):
        if ":" in composite_uid:
            parts = composite_uid.split(":", 1)
            return parts[0], parts[1]
        return None, composite_uid

    def books_signature(self):
        return tuple(sorted(
            (item["uid"], bool(item.get("enabled")), item.get("rank"),
             bool(item.get("is_system_default")), item.get("status") or "")
            for item in self.get_sources_info()
        ))

    def emit_books_changed_if_moved(self):
        signature = self.books_signature()
        with self.books_lock:
            if signature == self._last_books_signature:
                return
            self._last_books_signature = signature
        GLib.idle_add(self.emit, "address-books-changed")

    def start_sources_fetch(self):
        with self.books_lock:
            if self._sources_fetch_running:
                return
            self._sources_fetch_running = True
        run_in_background(self.fetch_sources_from_provider)

    def fetch_sources_from_provider(self):
        try:
            provided = self.sources_provider() if self.sources_provider else None
        finally:
            with self.books_lock:
                self._sources_fetch_running = False
        if provided is not None:
            self._sources_info_cache = [dict(item) for item in provided]
            self.emit_books_changed_if_moved()

    def sources_info_async(self, on_ready):
        if self.owns_live_views or self._sources_info_cache is not None or not self.sources_provider:
            on_ready(self.get_sources_info())
            return

        def task():
            self.fetch_sources_from_provider()

        def done(_result):
            on_ready(self.get_sources_info())

        run_in_background(task, on_complete=done)

    def get_sources_info(self):
        if not self.owns_live_views:
            if self._sources_info_cache is None and self.sources_provider:
                self.start_sources_fetch()
            if self._sources_info_cache is not None:
                return [dict(item) for item in self._sources_info_cache]
            return []

        if self._sources_info_cache is not None:
            return [dict(item) for item in self._sources_info_cache]

        try:
            registry_books = self.enabled_registry_books()
            default_uid = self.default_source_uid()
            saved_config = self.saved_config_entries()

            result = []
            uids_processed = set()
            current_rank = 0

            for conf in saved_config:
                uid = conf["uid"]
                info = registry_books.get(uid)
                if not info:
                    continue
                is_def = (uid == default_uid)
                result.append(self.source_info_entry(info, current_rank,
                                                      conf.get("enabled", True) or is_def,
                                                      is_def, registry_books))
                uids_processed.add(uid)
                current_rank += 1

            for uid, info in registry_books.items():
                if uid in uids_processed:
                    continue
                result.append(self.source_info_entry(info, current_rank, True,
                                                      uid == default_uid, registry_books))
                current_rank += 1

            self._sources_info_cache = [dict(item) for item in result]
            return result

        except Exception as e:
            logger.error(f"[EDS] Get Sources Info Error: {e}")
            return []

    def source_info_entry(self, info, rank, enabled, is_def, registry_books):
        return {
            "uid": info["uid"],
            "name": info["name"],
            "rank": rank,
            "enabled": enabled,
            "is_system_default": is_def,
            "is_local": info["is_local"],
            "removable": info["removable"],
            "status": None if info["is_local"] else info["status"],
            "account": self.account_name_for(info, registry_books),
        }

    def account_name_for(self, info, registry_books):
        parent = info.get("parent") or ""
        if not parent or parent.endswith("-stable") or parent == "local":
            return ""
        parsed = registry_books.get(parent)
        if parsed:
            return parsed["name"]
        return ""

    def default_source_uid(self):
        schema_source = Gio.SettingsSchemaSource.get_default()
        schema = schema_source.lookup(EVOLUTION_SCHEMA_ID, True) if schema_source else None
        if schema and schema.has_key(DEFAULT_BOOK_KEY):
            settings = Gio.Settings.new(EVOLUTION_SCHEMA_ID)
            chosen = settings.get_string(DEFAULT_BOOK_KEY)
            if chosen:
                return chosen
        return SYSTEM_BOOK_UID

    def set_default_addressbook(self, uid):
        schema_source = Gio.SettingsSchemaSource.get_default()
        schema = schema_source.lookup(EVOLUTION_SCHEMA_ID, True) if schema_source else None
        if not schema or not schema.has_key(DEFAULT_BOOK_KEY):
            logger.warning("[EDS] No evolution settings schema; default book stays the system book")
            return False
        settings = Gio.Settings.new(EVOLUTION_SCHEMA_ID)
        settings.set_string(DEFAULT_BOOK_KEY, uid)
        self.invalidate_sources_info()
        if self.owns_live_views:
            self.emit_books_changed_if_moved()
        return True

    def refresh_backends(self):
        with self.sources_lock:
            uids = list(self.sources.keys())

        try:
            registry_books = self.registry_objects()
        except Exception as e:
            logger.error(f"[EDS] Registry read failed for refresh: {e}")
            return 0

        refreshed = 0
        for uid in uids:
            info = registry_books.get(uid)
            if not info or info["is_local"]:
                continue
            def refreshed_backend(bus, result, book_uid):
                try:
                    bus.call_finish(result)
                    logger.info(f"[EDS] Backend refresh started for {book_uid}")
                except Exception as e:
                    logger.warning(f"[EDS] Backend refresh failed for {book_uid}: {e}")

            self.get_bus().call(
                EDS_SOURCES_BUS_NAME, EDS_SOURCES_PATH, EDS_SOURCE_MANAGER_IFACE,
                "RefreshBackend", GLib.Variant("(s)", (uid,)),
                GLib.VariantType("()"), Gio.DBusCallFlags.NONE,
                EDS_CALL_TIMEOUT_MS, None, refreshed_backend, uid)
            refreshed += 1
        return refreshed

    def create_local_addressbook(self, name):
        uid = uuid.uuid4().hex
        keyfile = BOOK_KEYFILE_TEMPLATE.format(name=name)
        try:
            self.get_bus().call_sync(
                EDS_SOURCES_BUS_NAME, EDS_SOURCES_PATH, EDS_SOURCE_MANAGER_IFACE,
                "CreateSources", GLib.Variant("(a{ss})", ({uid: keyfile},)),
                GLib.VariantType("()"), Gio.DBusCallFlags.NONE,
                EDS_CALL_TIMEOUT_MS, None)
        except Exception as e:
            logger.error(f"[EDS] Create addressbook error: {e}")
            return False

        deadline = time.monotonic() + CREATE_APPEAR_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                if uid in self.registry_objects():
                    config = self.saved_config_entries()
                    next_rank = max((item.get("rank", 0) for item in config), default=-1) + 1
                    config.append({"uid": uid, "name": name, "enabled": True, "rank": next_rank})
                    self.save_sources_config(config)
                    self.invalidate_sources_info()
                    self.reload()
                    return True
            except Exception as e:
                logger.warning(f"[EDS] Registry read while awaiting new book: {e}")
            time.sleep(0.25)

        logger.error(f"[EDS] Created book {uid} never appeared in the registry")
        return False

    def update_sources_config(self, new_config_list):
        self.save_sources_config(new_config_list)
        self.invalidate_sources_info()
        if not self.owns_live_views:
            run_in_background(self.reload_cache_from_db)
            return
        run_in_background(self.update_sources_task, new_config_list,)

    def update_sources_task(self, new_config_list):
        with self.reload_lock:
            self.update_sources_task_locked(new_config_list)

    def update_sources_task_locked(self, new_config_list):
        try:
            registry_books = self.enabled_registry_books()
        except Exception as e:
            logger.error(f"[EDS] Registry read failed for config update: {e}")
            return

        to_enable = {}
        to_disable = []
        rank_changed = False

        new_conf_map = {item["uid"]: item for item in new_config_list}

        with self.sources_lock:
            current_uids = list(self.sources.keys())
            for uid in current_uids:
                if uid not in new_conf_map or not new_conf_map[uid].get("enabled", True):
                    to_disable.append(uid)
                else:
                    current_rank = self.sources[uid].get("rank")
                    new_rank = new_conf_map[uid].get("rank")
                    if current_rank != new_rank:
                        self.sources[uid]["rank"] = new_rank
                        rank_changed = True

            for item in new_config_list:
                uid = item["uid"]
                if item.get("enabled", True) and uid not in self.sources:
                    info = registry_books.get(uid)
                    if info:
                        to_enable[uid] = {"uid": uid, "name": info["name"],
                                          "rank": item["rank"]}

        if not to_enable and not to_disable and not rank_changed:
            return

        for uid in to_disable:
            self.remove_source(uid)

        for uid, item in to_enable.items():
            self.init_wire_source_safe(item)

        if rank_changed or to_enable or to_disable:
            self.rebuild_lookup_map()
            GLib.idle_add(self.emit, "contacts-loaded")

    def delete_addressbook(self, source_uid):
        if source_uid == SYSTEM_BOOK_UID:
            logger.warning("[EDS] Refusing to delete system-address-book")
            return False

        try:
            registry_books = self.registry_objects()
        except Exception as e:
            logger.error(f"[EDS] Registry read failed for delete: {e}")
            return False

        info = registry_books.get(source_uid)
        if not info:
            logger.warning(f"[EDS] Delete failed: {source_uid} not in the registry")
            return False
        if info["name"] == "Andromeda Contacts":
            logger.warning("[EDS] Refusing to delete Andromeda Contacts")
            return False
        if not info["removable"]:
            logger.warning(f"[EDS] {source_uid} does not offer removal")
            return False

        try:
            self.get_bus().call_sync(
                EDS_SOURCES_BUS_NAME, info["path"], EDS_SOURCE_REMOVABLE_IFACE,
                "Remove", None, GLib.VariantType("()"),
                Gio.DBusCallFlags.NONE, EDS_CALL_TIMEOUT_MS, None)
        except Exception as e:
            logger.error(f"[EDS] Delete addressbook error: {e}")
            return False

        self.invalidate_sources_info()
        new_config = [s for s in self.saved_config_entries() if s["uid"] != source_uid]
        self.save_sources_config(new_config)

        self.remove_source(source_uid)
        self.rebuild_lookup_map()

        try:
            with self.db_ref.lock:
                self.db_ref.conn_contacts.execute("DELETE FROM contacts WHERE source_uid=?", (source_uid,))
                self.db_ref.conn_contacts.commit()
        except Exception as e:
            logger.warning(f"[EDS] Failed to clear local db for source {source_uid}: {e}")

        GLib.idle_add(self.emit, "contacts-loaded")
        self.emit_books_changed_if_moved()
        return True

    def remove_source(self, uid):
        with self.sources_lock:
            self.sources.pop(uid, None)
        self.close_book(uid)

        with self.cache_lock:
            self._cache_loaded_sources.discard(uid)
            self._source_ranks.pop(uid, None)
            to_remove = [k for k, v in self.cache.items() if v.get("source_uid") == uid]
            for k in to_remove:
                del self.cache[k]
            for norm in list(self.lookup_map.keys()):
                remaining = [entry for entry in self.lookup_map[norm] if entry[2] != uid]
                if not remaining:
                    del self.lookup_map[norm]
                elif len(remaining) != len(self.lookup_map[norm]):
                    self.lookup_map[norm] = remaining

        logger.info(f"[EDS] Removed source: {uid}")

    def rebuild_lookup_map(self):
        with self.sources_lock:
            ranks = {uid: info.get("rank", 999) for uid, info in self.sources.items()}

        with self.cache_lock:
            self.lookup_map = {}

            for uid, contact in self.cache.items():
                source_uid = contact.get("source_uid")
                rank = ranks.get(source_uid, 999)

                if "phones" in contact:
                    for p_data in contact["phones"]:
                        norm = normalize_number(p_data[0])
                        if norm:
                            if norm not in self.lookup_map:
                                self.lookup_map[norm] = []
                            self.lookup_map[norm].append((rank, contact["name"], source_uid, uid))

    def handle_backend_update(self, vcards, source_uid):
        with self.sources_lock:
            source_info = self.sources.get(source_uid)
            rank = source_info["rank"] if source_info else None
        if source_info is None:
            return

        db_batch = []
        for vcard in vcards:
            data = parse_vcard_string(vcard, source_uid)
            uid = data.get("uid")
            if not uid:
                continue

            with self.cache_lock:
                existing = self.cache.get(uid)

                if (existing and
                        existing.get("name") == data.get("name") and
                        existing.get("phones") == data.get("phones") and
                        existing.get("emails") == data.get("emails") and
                        existing.get("vcard_hash") == data.get("vcard_hash")):
                    continue

                for p in existing.get("phones", []) if existing else []:
                    norm = normalize_number(p[0])
                    if norm and norm in self.lookup_map:
                        self.lookup_map[norm] = [x for x in self.lookup_map[norm] if x[3] != uid]
                        if not self.lookup_map[norm]:
                            del self.lookup_map[norm]

                self.cache[uid] = {
                    "uid": uid,
                    "source_uid": source_uid,
                    "name": data.get("name"),
                    "phones": data.get("phones", []),
                    "emails": data.get("emails", []),
                    "vcard_hash": data.get("vcard_hash"),
                    "is_fav": bool(data.get("is_fav")),
                }

                for p_data in data["phones"]:
                    norm = normalize_number(p_data[0])
                    if not norm:
                        continue
                    self.lookup_map.setdefault(norm, []).append((rank, data["name"], source_uid, uid))
                    if existing:
                        GLib.idle_add(lambda n=norm, fn=data["name"]: self.gsettings_mgr.update_special_list_names(n, fn) or False)

            db_batch.append(data)

        if db_batch:
            self.db_ref.upsert_contacts_batch(db_batch, source_uid)
            GLib.idle_add(self.emit, "contacts-loaded")

    def on_objects_removed(self, uids, source_uid):
        def task():
            removed_uids = []
            with self.cache_lock:
                for real_uid in uids:
                    uid = self.make_composite_uid(source_uid, real_uid)
                    contact = self.cache.pop(uid, None)
                    for p in contact.get("phones", []) if contact else []:
                        norm = normalize_number(p[0])
                        if norm and norm in self.lookup_map:
                            self.lookup_map[norm] = [x for x in self.lookup_map[norm] if x[3] != uid]
                            if not self.lookup_map[norm]:
                                del self.lookup_map[norm]
                    removed_uids.append(uid)
            for uid in removed_uids:
                self.db_ref.delete_contact(uid)
            GLib.idle_add(self.emit, "contacts-loaded")

        run_in_background(task)

    def get_display_name(self, number):
        if self.db_ref and self.db_ref.is_blocked(number, kind="any"):
            return _("Blocked Number")
        return self.get_contact_name(number)

    def remove_number_everywhere(self, number):
        norm = normalize_number(number)
        for c in self.search_contacts(norm):
            uid = c[0]

            with self.cache_lock:
                full_contact = self.cache.get(uid)

            should_delete = False
            if full_contact:
                phones = full_contact.get("phones", [])
                other_phones = [p for p in phones if normalize_number(p[0]) != norm]
                emails = full_contact.get("emails", [])

                has_other_fields = False
                vcard = self.get_contact_vcard(uid) or full_contact.get("vcard", "")
                if vcard:
                    for line in unfold_vcard(vcard).splitlines():
                        if ":" not in line:
                            continue
                        key = line.split(":", 1)[0].split(";")[0].upper()
                        if key in ["ORG", "TITLE", "ADR", "NOTE", "URL", "BDAY", "ANNIVERSARY"]:
                            has_other_fields = True
                            break

                if not other_phones and not emails and not has_other_fields:
                    should_delete = True

            if should_delete:
                logger.info(f"[EDS] Deleting contact {uid} (empty after removing {norm})")
                self.delete_contact(uid)
            else:
                self.remove_number_from_contact(uid, norm)

    def get_contact_name(self, number):
        norm = normalize_number(number)

        with self.cache_lock:
            candidates = self.lookup_map.get(norm, [])
            if candidates:
                candidates.sort(key=lambda x: x[0])
                return candidates[0][1]

        if any(c.isalpha() for c in str(number)):
            return "Unknown"

        return None

    def get_contact_vcard(self, uid):
        return self.db_ref.get_contact_vcard(uid)

    def search_contacts(self, query, limit=None, offset=0):
        if not self.db_ref:
            return []
        return self.db_ref.search_contacts_db(query, limit=limit, offset=offset)

    def read_only_source_uids(self):
        uids = set()
        for item in self.get_sources_info():
            if item.get("name") == "Andromeda Contacts":
                uids.add(item.get("uid"))
                continue
            if item.get("is_local"):
                continue
            status = (item.get("status") or "").strip().lower()
            if status and status != EDS_STATUS_CONNECTED:
                uids.add(item.get("uid"))
        return uids

    def read_only_reason(self, uid):
        for item in self.get_sources_info():
            if item.get("uid") != uid:
                continue
            if item.get("name") == "Andromeda Contacts":
                return _("Andromeda contacts come from the system and are read here only.")
            if item.get("is_local"):
                return ""
            status = (item.get("status") or "").strip().lower()
            if status and status != EDS_STATUS_CONNECTED:
                return _("{book} is not connected, so its contacts cannot be changed "
                         "until it is back.").format(book=item.get("name") or uid)
            return ""
        return ""

    def is_andromeda_source(self, source_uid):
        with self.sources_lock:
            info = self.sources.get(source_uid)
        return bool(info) and info.get("name") == "Andromeda Contacts"

    def writable_book_uid(self, source_uid=None):
        if source_uid:
            return source_uid

        default = self.default_source_uid()
        with self.sources_lock:
            if default and default in self.sources:
                return default
            for info in sorted(self.sources.values(), key=lambda x: x["rank"]):
                if info.get("name") != "Andromeda Contacts":
                    return info["uid"]
        return default

    def save_contact_with_reason(self, vcard_string, uid=None, source_uid=None):
        target = None
        if uid and ":" in uid:
            target = uid.split(":", 1)[0]
        elif source_uid:
            target = source_uid
        else:
            target = self.default_source_uid()
        if target and self.is_andromeda_source(target):
            return (False, "read-only")
        ok = self.save_contact(vcard_string, uid=uid, source_uid=source_uid)
        return (ok, "" if ok else "write-failed")

    def cleaned_vcard(self, vcard_string):
        cleaned_lines = []
        for line in vcard_string.splitlines():
            if not is_property(line, "TEL"):
                cleaned_lines.append(line)
                continue
            try:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    raw = parts[1].strip()
                    key_part = parts[0]

                    if "X-EVOLUTION-E164" in key_part.upper():
                        subparts = key_part.split(";")
                        new_subparts = [sp for sp in subparts
                                        if not sp.upper().startswith("X-EVOLUTION-E164")]
                        key_part = ";".join(new_subparts)

                    cleaned_lines.append(f"{key_part}:{raw}")
                else:
                    cleaned_lines.append(line)
            except Exception as e:
                logger.warning(f"[EDS] VCard line processing error: {e}")
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    def save_plan(self, vcard_string, uid=None, source_uid=None):
        final_vcard = self.cleaned_vcard(vcard_string)
        refused = (None, "", "")

        if not (uid and isinstance(uid, str) and uid.strip()):
            if source_uid and self.is_andromeda_source(source_uid):
                logger.warning("[EDS] Refusing to save new Andromeda Contact")
                return refused

            target_uid = self.writable_book_uid(source_uid)
            if not target_uid:
                logger.error("[EDS] Save failed: No writable book found.")
                return refused

            return (target_uid, final_vcard, "CreateContacts")

        s_uid, r_uid = self.parse_composite_uid(uid)
        if not s_uid:
            s_uid = source_uid
            r_uid = uid

        if not s_uid:
            with self.cache_lock:
                cached = self.cache.get(uid)
            if cached:
                s_uid = cached.get("source_uid")

        if not s_uid:
            logger.error(f"[EDS] Save failed: Could not determine source for UID {uid}")
            return refused

        if self.is_andromeda_source(s_uid):
            logger.warning(f"[EDS] Refusing to modify Andromeda Contact {uid}")
            return refused

        target_uid = self.writable_book_uid(s_uid)
        if not target_uid:
            logger.error("[EDS] Save failed: No writable book found.")
            return refused

        lines = [line for line in final_vcard.splitlines()
                 if not line.upper().startswith("UID:")]
        uid_line = f"UID:{r_uid}"
        end_index = next((i for i, line in enumerate(lines)
                          if line.strip().upper().startswith("END:VCARD")), None)
        if end_index is None:
            lines.append(uid_line)
            lines.append("END:VCARD")
        else:
            lines.insert(end_index, uid_line)

        return (target_uid, "\n".join(lines), "ModifyContacts")

    def save_contact_with_reason_async(self, vcard_string, callback, uid=None, source_uid=None):
        if uid and ":" in uid:
            target = uid.split(":", 1)[0]
        elif source_uid:
            target = source_uid
        else:
            target = self.default_source_uid()

        if target and self.is_andromeda_source(target):
            GLib.idle_add(callback, False, "read-only")
            return

        def done(ok):
            callback(ok, "" if ok else "write-failed")

        self.save_contact_async(vcard_string, done, uid=uid, source_uid=source_uid)

    def save_contact(self, vcard_string, uid=None, source_uid=None):
        target_uid, final_vcard, method = self.save_plan(vcard_string, uid, source_uid)
        if not target_uid:
            return False

        try:
            self.book_call(target_uid, method,
                           GLib.Variant("(asu)", ([final_vcard], 0)), None)
        except Exception as e:
            logger.error(f"[EDS] Save Error: {e}")
            return False

        logger.info(f"[EDS] {method} done for {target_uid}")
        return True

    def save_contact_async(self, vcard_string, callback, uid=None, source_uid=None):
        target_uid, final_vcard, method = self.save_plan(vcard_string, uid, source_uid)
        if not target_uid:
            GLib.idle_add(callback, False)
            return

        def done(_reply, error):
            if error:
                logger.error(f"[EDS] Save Error: {error}")
                callback(False)
                return
            logger.info(f"[EDS] {method} done for {target_uid}")
            callback(True)

        self.book_call_async(target_uid, method,
                             GLib.Variant("(asu)", ([final_vcard], 0)), None, done)

    def delete_contact(self, uid):
        s_uid, r_uid = self.delete_target(uid)
        if not s_uid:
            return False

        try:
            self.book_call(s_uid, "RemoveContacts",
                            GLib.Variant("(asu)", ([r_uid], 0)), None)
            logger.info(f"[EDS] Deleted: {uid}")
            return True
        except Exception as e:
            logger.error(f"[EDS] Delete Error: {e}")
            return False

    def delete_contact_async(self, uid, callback):
        s_uid, r_uid = self.delete_target(uid)
        if not s_uid:
            GLib.idle_add(callback, False)
            return

        def done(_reply, error):
            if error:
                logger.error(f"[EDS] Delete Error: {error}")
                callback(False)
                return
            logger.info(f"[EDS] Deleted: {uid}")
            callback(True)

        self.book_call_async(s_uid, "RemoveContacts",
                             GLib.Variant("(asu)", ([r_uid], 0)), None, done)

    def delete_target(self, uid):
        if not isinstance(uid, str) or not uid.strip():
            logger.error("[EDS] Delete failed: Invalid UID format")
            return (None, None)

        s_uid, r_uid = self.parse_composite_uid(uid)

        if not s_uid:
            with self.cache_lock:
                cached = self.cache.get(uid)
            if cached:
                s_uid = cached.get("source_uid")
                r_uid = uid
            else:
                logger.warning(f"[EDS] Delete warning: UID {uid} not found in cache and no source specified.")
                if ":" in uid:
                    parts = uid.split(":", 1)
                    s_uid = parts[0]
                    r_uid = parts[1]

        if not s_uid:
            logger.error(f"[EDS] Delete failed: Unknown source for {uid}")
            return (None, None)

        if self.is_andromeda_source(s_uid):
            logger.warning(f"[EDS] Refusing to delete Andromeda Contact {uid}")
            return (None, None)

        return (s_uid, r_uid)

    def delete_contacts(self, uids):
        if not uids:
            return True

        uids_by_source = {}
        for uid in uids:
            s_uid, r_uid = self.parse_composite_uid(uid)
            if not s_uid:
                with self.cache_lock:
                    cached = self.cache.get(uid)
                if cached:
                    s_uid = cached.get("source_uid")
                    r_uid = uid
                elif ":" in uid:
                    parts = uid.split(":", 1)
                    s_uid = parts[0]
                    r_uid = parts[1]

            if s_uid:
                if s_uid not in uids_by_source:
                    uids_by_source[s_uid] = []
                uids_by_source[s_uid].append(r_uid)
            else:
                logger.warning(f"[EDS] Batch delete skipped unknown source for {uid}")

        success = True
        for s_uid, r_uids in uids_by_source.items():
            if self.is_andromeda_source(s_uid):
                logger.warning(f"[EDS] Refusing to batch delete Andromeda Contacts from {s_uid}")
                success = False
                continue

            try:
                self.book_call(s_uid, "RemoveContacts",
                                GLib.Variant("(asu)", (r_uids, 0)), None)
                logger.info(f"[EDS] Batch deleted {len(r_uids)} contacts from {s_uid}")
            except Exception as e:
                logger.error(f"[EDS] Batch delete error for {s_uid}: {e}")
                success = False

        return success

    def delete_all_contacts(self, source_uid=None):
        with self.cache_lock:
            if source_uid:
                uids = [uid for uid, c in self.cache.items() if c.get("source_uid") == source_uid]
            else:
                uids = list(self.cache.keys())

        count = 0
        for uid in uids:
            if self.delete_contact(uid):
                count += 1

        target = source_uid if source_uid else "ALL SOURCES"
        logger.info(f"[EDS] Deleted {count} contacts from {target}.")
        return True

    def build_number_added_vcard(self, uid, number, label="Mobile"):
        with self.cache_lock:
            contact = self.cache.get(uid)

        if not contact:
            logger.warning(f"[EDS] Add Number Failed: Contact {uid} not found in cache")
            return None

        vcard = unfold_vcard(contact.get("vcard", ""))
        if not vcard:
            vcard = self.get_contact_vcard(uid)
            if not vcard:
                logger.warning(f"[EDS] Add Number Failed: No VCard for {uid}")
                return None
            vcard = unfold_vcard(vcard)

        lines = vcard.splitlines()
        new_lines = []
        inserted = False

        type_str = label.upper()
        new_line = f"TEL;TYPE={type_str}:{number}"

        for line in lines:
            if line.strip().upper() == "END:VCARD":
                new_lines.append(new_line)
                new_lines.append(line)
                inserted = True
            else:
                new_lines.append(line)

        if not inserted:
            new_lines.append(new_line)
            new_lines.append("END:VCARD")

        return "\n".join(new_lines)

    def remove_number_from_contact(self, uid, number):
        logger.info(f"[EDS] Removing number {number} from contact {uid}")
        with self.cache_lock:
            contact = self.cache.get(uid)

        if not contact:
            logger.warning(f"[EDS] Remove Number Failed: Contact {uid} not found in cache")
            return False

        vcard = unfold_vcard(contact.get("vcard", ""))
        if not vcard:
            vcard = self.get_contact_vcard(uid)
            if not vcard:
                logger.warning(f"[EDS] Remove Number Failed: No VCard for {uid}")
                return False
            vcard = unfold_vcard(vcard)

        norm_target = normalize_number(number)
        if not norm_target:
            logger.warning(f"[EDS] Remove Number Failed: Could not normalize {number}")
            return False

        lines = vcard.splitlines()
        new_lines = []

        def is_match(line):
            if not is_property(line, "TEL"):
                return False
            try:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    val = normalize_number(parts[1])
                    return val == norm_target
            except Exception as e:
                logger.debug(f"[EDS] Line parse error: {e}")
            return False

        for line in lines:
            if is_match(line):
                logger.info(f"[EDS] Removing matching line: {line}")
                continue
            new_lines.append(line)

        final_vcard = "\n".join(new_lines)
        return self.save_contact(final_vcard, uid=uid)
