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
    """Contact store speaking Evolution Data Server's D-Bus wire.

    The registry is read through the Sources5 ObjectManager and books
    are opened through the AddressBookFactory, so no process ever maps
    the EDS client libraries. Supports multiple address books with
    ranking; a window instance loads the local mirror only and opens a
    book lazily when it writes.
    """
    __gsignals__ = {
        'contacts-loaded': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'address-books-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, owns_live_views=True):
        """Initialize the manager; nothing touches the bus yet."""
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
        self.registry_watched = False
        self._registry_sub_ids = []
        self._registry_paths = {}
        self.is_ready = False
        self._sources_info_cache = None
        self._sources_fetch_running = False
        self._last_books_signature = None
        self._applied_config_json = None

    def set_db(self, db_manager, gsettings_mgr):
        """Set the database manager reference and start initialization."""
        self.db_ref = db_manager
        self.gsettings_mgr = gsettings_mgr
        self.gsettings_mgr.gsettings.connect(
            "changed::address-book-sources", self._on_sources_config_changed)

        run_in_background(self._load_cache_initial)
        if self.owns_live_views:
            run_in_background(self._start_live_sources)

    def _on_sources_config_changed(self, _settings, _key):
        """Reload when another process rewrote the book configuration.

        dconf reports the change to every process; the one that wrote
        it already reloaded and recognizes its own value, so only the
        others rebuild, and the daemon's live views follow a settings
        change without a restart.
        """
        current = self.gsettings_mgr.get_setting("address_book_sources")
        if current == self._applied_config_json:
            return
        logger.info("[EDS] Address book configuration changed elsewhere, reloading")
        self.reload()

    def _load_cache_initial(self):
        """Load contacts from local DB before EDS connection."""
        saved_config = self._saved_config_entries()
        if not saved_config:
            return
        saved_config.sort(key=lambda x: x.get('rank', 999))

        def load_sources_async():
            with self.reload_lock:
                for item in saved_config:
                    if item.get('enabled', True):
                        self._load_from_local_db(item['uid'], item.get('rank', 0))
            if not self.owns_live_views:
                self._rebuild_lookup_map()
                self.is_ready = True
            GLib.idle_add(self.emit, 'contacts-loaded')

        run_in_background(load_sources_async)

    def _saved_config_entries(self):
        """Return the saved book configuration as a list, empty when unset."""
        saved_json = self.gsettings_mgr.get_setting("address_book_sources") if self.gsettings_mgr else None
        if not saved_json:
            return []
        try:
            return json.loads(saved_json)
        except Exception as e:
            logger.warning(f"[EDS] Saved config unreadable: {e}")
            return []

    def _get_bus(self):
        """Return the session bus connection, connecting on first use."""
        if self.bus is None:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return self.bus

    def _registry_objects(self):
        """Read every address book source from the registry; blocking, call from a worker.

        Returns uid keyed dicts parsed from each source's keyfile Data
        property, and refreshes the path map the removal signal needs.
        """
        reply = self._get_bus().call_sync(
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
            parsed = self._parse_source_data(props.get("Data", ""))
            if parsed is None or not parsed['is_addressbook']:
                continue
            books[uid] = {
                'uid': uid,
                'path': path,
                'name': parsed['name'] or uid,
                'enabled': parsed['enabled'],
                'parent': parsed['parent'],
                'backend': parsed['backend'],
                'is_local': parsed['backend'] in LOCAL_BACKEND_NAMES,
                'removable': EDS_SOURCE_REMOVABLE_IFACE in ifaces,
                'writable': EDS_SOURCE_WRITABLE_IFACE in ifaces,
                'status': (props.get("ConnectionStatus") or None),
            }
        self._registry_paths = paths
        return books

    def _parse_source_data(self, data):
        """Parse a source's keyfile Data property into plain fields."""
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
            'name': section.get("DisplayName", ""),
            'enabled': section.get("Enabled", "true").lower() == "true",
            'parent': section.get("Parent", ""),
            'backend': book.get("BackendName", "") if book else None,
            'is_addressbook': parser.has_section("Address Book"),
        }

    def _watch_registry(self):
        """Follow registry additions and removals; safe to call twice."""
        if self.registry_watched:
            return
        self.registry_watched = True
        bus = self._get_bus()
        self._registry_sub_ids.append(bus.signal_subscribe(
            EDS_SOURCES_BUS_NAME, OBJECT_MANAGER_IFACE, "InterfacesAdded",
            EDS_SOURCES_PATH, None, Gio.DBusSignalFlags.NONE,
            self._on_registry_added))
        self._registry_sub_ids.append(bus.signal_subscribe(
            EDS_SOURCES_BUS_NAME, OBJECT_MANAGER_IFACE, "InterfacesRemoved",
            EDS_SOURCES_PATH, None, Gio.DBusSignalFlags.NONE,
            self._on_registry_removed))

    def _on_registry_added(self, _conn, _sender, _path, _iface, _member, params):
        """A source appeared somewhere in the system.

        An address book that shows up while the daemon runs — a sync
        account being set up, a book made in another app — is unknown
        to the loaded sources, so it would neither stream its contacts
        nor be recognized as read-only. Discovering it once brings it
        under the same rules as the books present at startup.
        """
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
        parsed = self._parse_source_data(props.get("Data", ""))
        if not uid or parsed is None or not parsed['is_addressbook']:
            return
        with self.sources_lock:
            known = uid in self.sources
        if known:
            return
        logger.info(f"[EDS] Address book {uid} appeared, discovering it")
        self.reload()

    def _on_registry_removed(self, _conn, _sender, _path, _iface, _member, params):
        """Forget a book that was deleted elsewhere in the system.

        The registry reports the deletion to every process, but only the
        one that asked for it dropped the contacts, so the others kept
        offering to call numbers from a book that no longer exists.
        """
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
            self._remove_source(uid)
            GLib.idle_add(self.emit, 'contacts-loaded')
            self._emit_books_changed_if_moved()

        run_in_background(task)

    def invalidate_sources_info(self):
        """Refresh the cached sources info.

        A window keeps its last list visible and refills it off the main
        thread, so a reload never flashes the settings page empty; the
        daemon drops its cache so the next read re-reads the registry.
        """
        if not self.owns_live_views and self.sources_provider:
            self._start_sources_fetch()
            return
        self._sources_info_cache = None

    def _book_lock(self, uid):
        """Return the lock that serializes opening and closing one book."""
        with self.books_lock:
            lock = self._book_locks.get(uid)
            if lock is None:
                lock = threading.Lock()
                self._book_locks[uid] = lock
            return lock

    def _open_book(self, uid):
        """Open a book through the factory; blocking, call from a worker.

        Returns the cached record when the book is already open; a
        record holds the bus name and object path every book call needs.
        Opening is serialized per book, so two writers racing on the
        same book cannot each open it and leave one connection orphaned
        with its live view untracked.
        """
        with self._book_lock(uid):
            return self._open_book_locked(uid)

    def _open_book_locked(self, uid):
        """Open one book; caller holds that book's lock."""
        with self.books_lock:
            record = self.books.get(uid)
        if record:
            return record
        bus = self._get_bus()
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
        record = {'uid': uid, 'path': book_path, 'bus_name': book_bus_name,
                  'view_path': None, 'sub_ids': []}
        with self.books_lock:
            self.books[uid] = record
        logger.info(f"[EDS] Book open for {uid} at {book_path}")
        return record

    def _book_call(self, uid, method, params, reply_type):
        """Call a method on a book, reopening once if its process died.

        Blocking, call from a worker. The factory parks books in
        subprocesses that can go away between calls; a second attempt
        through a fresh OpenAddressBook covers exactly that. Only a
        dead connection retries — a reply the server may have already
        acted on must not run twice, or a create without a stated uid
        would duplicate the contact.
        """
        record = self._open_book(uid)
        try:
            return self._get_bus().call_sync(
                record['bus_name'], record['path'], EDS_BOOK_IFACE,
                method, params, reply_type, Gio.DBusCallFlags.NONE,
                EDS_CALL_TIMEOUT_MS, None)
        except GLib.Error as e:
            if not self._is_gone_error(e):
                raise
            logger.warning(f"[EDS] {method} on {uid} lost its book, reopening: {e}")
            with self._book_lock(uid):
                current = None
                with self.books_lock:
                    current = self.books.get(uid)
                if current is record or current is None:
                    self._close_book_locked(uid)
                    current = self._open_book_locked(uid)
            return self._get_bus().call_sync(
                current['bus_name'], current['path'], EDS_BOOK_IFACE,
                method, params, reply_type, Gio.DBusCallFlags.NONE,
                EDS_CALL_TIMEOUT_MS, None)

    def _is_gone_error(self, error):
        """Return True when the error means the book's process is gone."""
        gone = ("org.freedesktop.DBus.Error.ServiceUnknown",
                "org.freedesktop.DBus.Error.NameHasNoOwner",
                "org.freedesktop.DBus.Error.Disconnected")
        try:
            return Gio.DBusError.get_remote_error(error) in gone
        except Exception as e:
            logger.debug(f"[EDS] Error name lookup failed: {e}")
            return False

    def _close_book(self, uid):
        """Drop a book's view subscriptions and forget the record."""
        with self._book_lock(uid):
            self._close_book_locked(uid)

    def _close_book_locked(self, uid):
        """Close one book; caller holds that book's lock."""
        with self.books_lock:
            record = self.books.pop(uid, None)
        if not record:
            return
        bus = self._get_bus()
        for sub_id in record['sub_ids']:
            try:
                bus.signal_unsubscribe(sub_id)
            except Exception as e:
                logger.debug(f"[EDS] Unsubscribe error (ignorable): {e}")
        if record['view_path']:
            try:
                bus.call_sync(
                    record['bus_name'], record['view_path'], EDS_VIEW_IFACE,
                    "Dispose", None, GLib.VariantType("()"),
                    Gio.DBusCallFlags.NONE, EDS_CALL_TIMEOUT_MS, None)
            except Exception as e:
                logger.debug(f"[EDS] View dispose error (ignorable): {e}")

    def _start_view(self, uid):
        """Open the live view and stream the book; blocking, call from a worker.

        The initial burst arrives as ObjectsAdded like any change, so
        one code path serves full sync and live updates alike.
        """
        record = self._open_book(uid)
        reply = self._book_call(uid, "GetView",
                                GLib.Variant("(s)", (EDS_MATCH_ALL_QUERY,)),
                                GLib.VariantType("(o)"))
        record['view_path'] = reply.unpack()[0]

        bus = self._get_bus()
        for member, handler in (("ObjectsAdded", self._on_view_added),
                                ("ObjectsModified", self._on_view_modified),
                                ("ObjectsRemoved", self._on_view_removed)):
            record['sub_ids'].append(bus.signal_subscribe(
                record['bus_name'], EDS_VIEW_IFACE, member, record['view_path'],
                None, Gio.DBusSignalFlags.NONE, partial(handler, source_uid=uid)))

        bus.call_sync(
            record['bus_name'], record['view_path'], EDS_VIEW_IFACE,
            "SetFlags", GLib.Variant("(u)", (VIEW_FLAG_NOTIFY_INITIAL,)),
            GLib.VariantType("()"), Gio.DBusCallFlags.NONE,
            EDS_CALL_TIMEOUT_MS, None)
        bus.call_sync(
            record['bus_name'], record['view_path'], EDS_VIEW_IFACE,
            "Start", None, GLib.VariantType("()"),
            Gio.DBusCallFlags.NONE, EDS_CALL_TIMEOUT_MS, None)
        logger.info(f"[EDS] Live view started for {uid}")

    def _vcards_from(self, params, source_uid):
        """Unpack the vcards from a view's object signal payload.

        The signal declares one array of strings, but the server packs
        each contact as its vcard followed by its uid, so the bare uids
        are dropped here; taking them for vcards used to store a
        nameless, numberless contact beside every real one.
        """
        try:
            objects = list(params.unpack()[0])
        except Exception as e:
            logger.error(f"[EDS] Bad view payload for {source_uid}: {e}")
            return []
        return [item for item in objects if VCARD_BEGIN in item.upper()]

    def _on_view_added(self, _conn, _sender, _path, _iface, _member, params, source_uid):
        """Route vcards streaming in from a wire view."""
        vcards = self._vcards_from(params, source_uid)
        run_in_background(self._handle_backend_update, vcards, source_uid)

    def _on_view_modified(self, _conn, _sender, _path, _iface, _member, params, source_uid):
        """Route vcards modified on a wire view."""
        vcards = self._vcards_from(params, source_uid)
        run_in_background(self._handle_backend_update, vcards, source_uid)

    def _on_view_removed(self, _conn, _sender, _path, _iface, _member, params, source_uid):
        """Route removed contact uids from a wire view."""
        try:
            uids = list(params.unpack()[0])
        except Exception as e:
            logger.error(f"[EDS] Bad removal payload for {source_uid}: {e}")
            return
        self._on_objects_removed(uids, source_uid)

    def _start_live_sources(self):
        """Bring up live contact monitoring; blocking, call from a worker.

        A saved configuration that carries book names opens the books
        directly and skips the registry read; a first run or a stale
        nameless configuration goes through discovery once and persists
        what it found, so the next start takes the direct path.
        """
        self._watch_registry()
        saved_config_json = self.gsettings_mgr.get_setting("address_book_sources") if self.gsettings_mgr else None
        saved_config = self._saved_config_entries()
        enabled = [item for item in saved_config if item.get('enabled', True)]

        if enabled and all('name' in item for item in enabled):
            try:
                with self.reload_lock:
                    for item in sorted(enabled, key=lambda x: x.get('rank', 999)):
                        self._init_wire_source(item['uid'], item.get('rank', 0), item['name'])
                self._applied_config_json = saved_config_json
                self._rebuild_lookup_map()
                self.is_ready = True
                GLib.idle_add(self.emit, 'contacts-loaded')
                with self.books_lock:
                    open_books = len(self.books)
                logger.info(f"[EDS] {open_books} book(s) live over the wire")
                return
            except Exception as e:
                logger.warning(f"[EDS] Configured book setup failed, rediscovering: {e}")
                self._teardown_books()

        with self.reload_lock:
            self._load_sources_config_locked()

    def _teardown_books(self):
        """Close every open book and forget the source table."""
        with self.books_lock:
            uids = list(self.books.keys())
        for uid in uids:
            self._close_book(uid)
        with self.sources_lock:
            self.sources = {}

    def _init_wire_source(self, uid, rank, name):
        """Open one book, sweep deletions and start its live view.

        Blocking, call from a worker. The sweep compares the book's uid
        list against the mirror so contacts deleted while the daemon was
        away disappear, except when an empty answer meets a filled
        mirror, which reads as a backend that is not ready rather than
        an emptied book.
        """
        self._load_from_local_db(uid, rank)
        self._open_book(uid)
        comp_uids = [self._make_composite_uid(uid, real_uid) for real_uid in self._list_book_uids(uid)]
        if comp_uids or not self._has_cached_contacts(uid):
            self.db_ref.sync_deleted_contacts(uid, comp_uids)
        else:
            logger.warning(f"[EDS] Skipping empty deletion sweep for {uid}: backend may not be ready")
        self._start_view(uid)
        with self.sources_lock:
            self.sources[uid] = {'uid': uid, 'name': name, 'rank': rank}

    def _list_book_uids(self, uid):
        """Return every contact uid in a book; blocking, call from a worker."""
        reply = self._book_call(uid, "GetContactListUids",
                                GLib.Variant("(s)", (EDS_MATCH_ALL_QUERY,)),
                                GLib.VariantType("(as)"))
        return list(reply.unpack()[0])

    def reload(self):
        """Tear down all source connections and reload configuration and contacts."""
        def task():
            if not self.owns_live_views:
                self.reload_cache_from_db()
                return
            with self.reload_lock:
                with self.sources_lock:
                    uids = list(self.sources.keys())
                for uid in uids:
                    self._remove_source(uid)
                self.invalidate_sources_info()
                self._load_sources_config_locked()

        run_in_background(task)

    def _enabled_registry_books(self):
        """Return enabled address book sources read from the registry."""
        return {uid: info for uid, info in self._registry_objects().items()
                if info['enabled']}

    def _load_sources_config_locked(self):
        """Discover books, merge the saved order and go live; caller holds reload_lock.

        Blocking, call from a worker. Books the configuration knows keep
        their saved order and enablement; newly discovered books join
        after them, the default book first, and the merged result is
        persisted so the next start can skip discovery.
        """
        try:
            self._watch_registry()
            registry_books = self._enabled_registry_books()
            default_uid = self._default_source_uid()

            saved_config = self._saved_config_entries()
            final_sources_list = []
            processed_uids = set()
            current_rank = 0

            for conf in saved_config:
                uid = conf['uid']
                info = registry_books.get(uid)
                if not info:
                    continue
                is_def = (uid == default_uid)
                final_sources_list.append({
                    'uid': uid,
                    'name': info['name'],
                    'rank': current_rank,
                    'enabled': conf.get('enabled', True) or is_def,
                    'is_system_default': is_def,
                })
                processed_uids.add(uid)
                current_rank += 1

            fresh = [info for uid, info in registry_books.items() if uid not in processed_uids]
            fresh.sort(key=lambda x: (0 if x['uid'] == default_uid else 1, x['name'].lower()))
            for info in fresh:
                final_sources_list.append({
                    'uid': info['uid'],
                    'name': info['name'],
                    'rank': current_rank,
                    'enabled': True,
                    'is_system_default': (info['uid'] == default_uid),
                })
                current_rank += 1

            self.save_sources_config(final_sources_list)

            with self.sources_lock:
                self.sources = {}

            threads = []
            for item in final_sources_list:
                if item['enabled']:
                    t = threading.Thread(target=self._init_wire_source_safe, args=(item,), daemon=True)
                    t.start()
                    threads.append(t)
            for t in threads:
                t.join()

            enabled_uids = {item['uid'] for item in final_sources_list if item['enabled']}
            for uid in self.loaded_source_uids() - enabled_uids:
                logger.info(f"[EDS] Dropping cached contacts of unavailable source {uid}")
                self._remove_source(uid)

            self._rebuild_lookup_map()
            self.is_ready = True
            GLib.idle_add(self.emit, 'contacts-loaded')
            self._emit_books_changed_if_moved()

        except Exception as e:
            logger.error(f"[EDS] Load Config Error: {e}")

    def _init_wire_source_safe(self, item):
        """Thread entry for source init that can never propagate an exception."""
        try:
            self._init_wire_source(item['uid'], item['rank'], item['name'])
        except Exception as e:
            logger.error(f"[EDS] Source init failed for {item.get('uid')}: {e}")

    def save_sources_config(self, sources_list):
        """Save the current sources configuration to settings."""
        to_save = []
        for item in sources_list:
            to_save.append({
                'uid': item['uid'],
                'name': item.get('name', item['uid']),
                'enabled': item['enabled'],
                'rank': item['rank']
            })
        try:
            config_json = json.dumps(to_save)
            self._applied_config_json = config_json
            self.gsettings_mgr.set_setting("address_book_sources", config_json)
        except Exception as e:
            logger.error(f"[EDS] Save Config Error: {e}")

    def _has_cached_contacts(self, source_uid):
        """Return True when the in-memory cache holds contacts for the source."""
        with self.cache_lock:
            return any(v.get('source_uid') == source_uid for v in self.cache.values())

    def loaded_source_uids(self):
        """Return a snapshot of the source uids whose contacts are loaded."""
        with self.cache_lock:
            return set(self._cache_loaded_sources)

    def _cache_entries_from_db(self, source_uid, rank):
        """Read one source out of the local mirror; blocking, call from a worker.

        Returns the cache and lookup entries the source holds, or None
        when the mirror could not be read.
        """
        try:
            contacts = self.db_ref.get_cached_contacts(source_uid)
        except Exception as e:
            logger.error(f"[EDS] DB fetch error: {e}")
            return None

        updates = {}
        lookup_updates = {}
        for c in contacts:
            real_uid = c['uid']
            if not real_uid:
                continue

            composite_uid = real_uid
            if ":" not in composite_uid:
                composite_uid = f"{source_uid}:{real_uid}"

            c['source_uid'] = source_uid
            updates[composite_uid] = c

            for p_data in c['phones']:
                norm = normalize_number(p_data[0])
                if norm:
                    lookup_updates.setdefault(norm, []).append(
                        (rank, c['name'], source_uid, composite_uid))

        return updates, lookup_updates

    def _load_from_local_db(self, source_uid, rank):
        """Load contacts from local database cache for a specific source once."""
        with self.cache_lock:
            if source_uid in self._cache_loaded_sources:
                logger.debug(f"[EDS] Cache for {source_uid} already loaded, skipping")
                return
            self._cache_loaded_sources.add(source_uid)
            self._source_ranks[source_uid] = rank

        entries = self._cache_entries_from_db(source_uid, rank)
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
        """Rebuild the contacts from the local mirror; blocking, call from a worker.

        A window instance watches no address book, so the owner saying
        that the mirror moved is the only reason it has to read it
        again. The rebuilt maps replace the old ones in one step, so a
        lookup running meanwhile never sees a half-empty cache. It waits
        for a load already in flight, whose contacts would otherwise be
        thrown away by the rebuild that started without them.
        """
        self.invalidate_sources_info()
        ranks = {item['uid']: item.get('rank', 0)
                 for item in self._saved_config_entries()
                 if item.get('enabled', True)}
        with self.reload_lock:
            cache = {}
            lookup_map = {}
            for source_uid, rank in ranks.items():
                entries = self._cache_entries_from_db(source_uid, rank)
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

        GLib.idle_add(self.emit, 'contacts-loaded')

    def _make_composite_uid(self, source_uid, real_uid):
        return f"{source_uid}:{real_uid}"

    def _parse_composite_uid(self, composite_uid):
        if ":" in composite_uid:
            parts = composite_uid.split(":", 1)
            return parts[0], parts[1]
        return None, composite_uid

    def _books_signature(self):
        """A hashable snapshot of the book set for change detection.

        Only the fields the settings list draws matter: which books
        exist, their order, whether they are enabled and which is the
        default. Contact content is deliberately absent, so a contact
        edit never counts as a book change.
        """
        return tuple(sorted(
            (item['uid'], bool(item.get('enabled')), item.get('rank'),
             bool(item.get('is_system_default')))
            for item in self.get_sources_info()
        ))

    def _emit_books_changed_if_moved(self):
        """Emit address-books-changed only when the book set actually moved.

        Blocking, call from a worker: it reads the sources info to build
        the signature.
        """
        signature = self._books_signature()
        with self.books_lock:
            if signature == self._last_books_signature:
                return
            self._last_books_signature = signature
        GLib.idle_add(self.emit, 'address-books-changed')

    def _start_sources_fetch(self):
        """Kick a one-at-a-time background refresh of the window's sources cache."""
        with self.books_lock:
            if self._sources_fetch_running:
                return
            self._sources_fetch_running = True
        run_in_background(self._fetch_sources_from_provider)

    def _fetch_sources_from_provider(self):
        """Fill the sources cache from the daemon; blocking, runs on a worker.

        A window learns the book list only here, so this is where a
        window decides the books changed: it emits address-books-changed
        when the freshly fetched set differs from the last, and the
        settings list, listening to that alone, never wakes for a mere
        contact edit.
        """
        try:
            provided = self.sources_provider() if self.sources_provider else None
        finally:
            with self.books_lock:
                self._sources_fetch_running = False
        if provided is not None:
            self._sources_info_cache = [dict(item) for item in provided]
            self._emit_books_changed_if_moved()

    def sources_info_async(self, on_ready):
        """Hand over the book list once there is a list to hand over.

        A window's first read is empty on purpose: the books live in the
        daemon and the cache fills in the background. Drawing an empty
        list is harmless because the refill redraws it, but a caller
        that decides something from the answer reads the emptiness as a
        phone with no address books at all.
        """
        if self.owns_live_views or self._sources_info_cache is not None or not self.sources_provider:
            on_ready(self.get_sources_info())
            return

        def task():
            self._fetch_sources_from_provider()

        def done(_result):
            on_ready(self.get_sources_info())

        run_in_background(task, on_complete=done)

    def get_sources_info(self):
        """Return the sources info for the Settings UI, never blocking the caller.

        A window returns its cached copy at once and fetches a fresh one
        from the daemon in the background, emitting contacts-loaded when
        it lands, so the settings page never freezes waiting on a busy
        daemon. Only the daemon itself reads the registry here.
        """
        if not self.owns_live_views:
            if self._sources_info_cache is None and self.sources_provider:
                self._start_sources_fetch()
            if self._sources_info_cache is not None:
                return [dict(item) for item in self._sources_info_cache]
            return []

        if self._sources_info_cache is not None:
            return [dict(item) for item in self._sources_info_cache]

        try:
            registry_books = self._enabled_registry_books()
            default_uid = self._default_source_uid()
            saved_config = self._saved_config_entries()

            result = []
            uids_processed = set()
            current_rank = 0

            for conf in saved_config:
                uid = conf['uid']
                info = registry_books.get(uid)
                if not info:
                    continue
                is_def = (uid == default_uid)
                result.append(self._source_info_entry(info, current_rank,
                                                      conf.get('enabled', True) or is_def,
                                                      is_def, registry_books))
                uids_processed.add(uid)
                current_rank += 1

            for uid, info in registry_books.items():
                if uid in uids_processed:
                    continue
                result.append(self._source_info_entry(info, current_rank, True,
                                                      uid == default_uid, registry_books))
                current_rank += 1

            self._sources_info_cache = [dict(item) for item in result]
            return result

        except Exception as e:
            logger.error(f"[EDS] Get Sources Info Error: {e}")
            return []

    def _source_info_entry(self, info, rank, enabled, is_def, registry_books):
        """Shape one registry book into the sources info dict the UI reads."""
        return {
            'uid': info['uid'],
            'name': info['name'],
            'rank': rank,
            'enabled': enabled,
            'is_system_default': is_def,
            'is_local': info['is_local'],
            'removable': info['removable'],
            'status': None if info['is_local'] else info['status'],
            'account': self._account_name_for(info, registry_books),
        }

    def _account_name_for(self, info, registry_books):
        """Return the owning account's display name for a remote book."""
        parent = info.get('parent') or ""
        if not parent or parent.endswith("-stable") or parent == "local":
            return ""
        parsed = registry_books.get(parent)
        if parsed:
            return parsed['name']
        return ""

    def _default_source_uid(self):
        """Return the uid of the address book marked default.

        Evolution keeps the choice in its own settings schema; a system
        without that schema means nobody ever chose, and EDS itself then
        treats the system book as the default.
        """
        schema_source = Gio.SettingsSchemaSource.get_default()
        schema = schema_source.lookup(EVOLUTION_SCHEMA_ID, True) if schema_source else None
        if schema and schema.has_key(DEFAULT_BOOK_KEY):
            settings = Gio.Settings.new(EVOLUTION_SCHEMA_ID)
            chosen = settings.get_string(DEFAULT_BOOK_KEY)
            if chosen:
                return chosen
        return SYSTEM_BOOK_UID

    def set_default_addressbook(self, uid):
        """Set the default address book source."""
        schema_source = Gio.SettingsSchemaSource.get_default()
        schema = schema_source.lookup(EVOLUTION_SCHEMA_ID, True) if schema_source else None
        if not schema or not schema.has_key(DEFAULT_BOOK_KEY):
            logger.warning("[EDS] No evolution settings schema; default book stays the system book")
            return False
        settings = Gio.Settings.new(EVOLUTION_SCHEMA_ID)
        settings.set_string(DEFAULT_BOOK_KEY, uid)
        self.invalidate_sources_info()
        if self.owns_live_views:
            self._emit_books_changed_if_moved()
        return True

    def refresh_backends(self):
        """Ask every remote backend to re-sync with its store.

        Blocking, call from a worker. Local books have nothing to
        refresh, so only the remote ones are asked.
        """
        with self.sources_lock:
            uids = list(self.sources.keys())

        try:
            registry_books = self._registry_objects()
        except Exception as e:
            logger.error(f"[EDS] Registry read failed for refresh: {e}")
            return 0

        refreshed = 0
        for uid in uids:
            info = registry_books.get(uid)
            if not info or info['is_local']:
                continue
            try:
                self._get_bus().call_sync(
                    EDS_SOURCES_BUS_NAME, EDS_SOURCES_PATH, EDS_SOURCE_MANAGER_IFACE,
                    "RefreshBackend", GLib.Variant("(s)", (uid,)),
                    GLib.VariantType("()"), Gio.DBusCallFlags.NONE,
                    EDS_CALL_TIMEOUT_MS, None)
                refreshed += 1
                logger.info(f"[EDS] Backend refresh started for {uid}")
            except Exception as e:
                logger.warning(f"[EDS] Backend refresh failed for {uid}: {e}")
        return refreshed

    def create_local_addressbook(self, name):
        """Create a new local address book and reload; blocking, call from a worker.

        The registry answers CreateSources before it serves the source,
        so the appearance is awaited; a book that never appears reports
        failure instead of a phantom success.
        """
        uid = uuid.uuid4().hex
        keyfile = BOOK_KEYFILE_TEMPLATE.format(name=name)
        try:
            self._get_bus().call_sync(
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
                if uid in self._registry_objects():
                    config = self._saved_config_entries()
                    next_rank = max((item.get('rank', 0) for item in config), default=-1) + 1
                    config.append({'uid': uid, 'name': name, 'enabled': True, 'rank': next_rank})
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
        """Update configuration from Settings UI and reload.

        A window only records the choice: persisting it changes the
        dconf key the daemon watches, and the daemon opens or closes
        the books in its own process, so enabling an online book never
        stalls a window on the network. The daemon path applies the
        change to its live books directly.
        """
        self.save_sources_config(new_config_list)
        self.invalidate_sources_info()
        if not self.owns_live_views:
            run_in_background(self.reload_cache_from_db)
            return
        run_in_background(self._update_sources_task, new_config_list,)

    def _update_sources_task(self, new_config_list):
        """Apply a configuration update; blocking, call from a worker."""
        with self.reload_lock:
            self._update_sources_task_locked(new_config_list)

    def _update_sources_task_locked(self, new_config_list):
        """Apply a configuration update; caller holds reload_lock."""
        try:
            registry_books = self._enabled_registry_books()
        except Exception as e:
            logger.error(f"[EDS] Registry read failed for config update: {e}")
            return

        to_enable = {}
        to_disable = []
        rank_changed = False

        new_conf_map = {item['uid']: item for item in new_config_list}

        with self.sources_lock:
            current_uids = list(self.sources.keys())
            for uid in current_uids:
                if uid not in new_conf_map or not new_conf_map[uid].get('enabled', True):
                    to_disable.append(uid)
                else:
                    current_rank = self.sources[uid].get('rank')
                    new_rank = new_conf_map[uid].get('rank')
                    if current_rank != new_rank:
                        self.sources[uid]['rank'] = new_rank
                        rank_changed = True

            for item in new_config_list:
                uid = item['uid']
                if item.get('enabled', True) and uid not in self.sources:
                    info = registry_books.get(uid)
                    if info:
                        to_enable[uid] = {'uid': uid, 'name': info['name'],
                                          'rank': item['rank']}

        if not to_enable and not to_disable and not rank_changed:
            return

        for uid in to_disable:
            self._remove_source(uid)

        for uid, item in to_enable.items():
            self._init_wire_source_safe(item)

        if rank_changed or to_enable or to_disable:
            self._rebuild_lookup_map()
            GLib.idle_add(self.emit, 'contacts-loaded')

    def delete_addressbook(self, source_uid):
        """Delete an entire address book source; blocking, call from a worker.

        The system book and Andromeda are refused here as well as at the
        interface layer; the wire itself backs the first refusal by
        never offering Remove on the system book.
        """
        if source_uid == SYSTEM_BOOK_UID:
            logger.warning("[EDS] Refusing to delete system-address-book")
            return False

        try:
            registry_books = self._registry_objects()
        except Exception as e:
            logger.error(f"[EDS] Registry read failed for delete: {e}")
            return False

        info = registry_books.get(source_uid)
        if not info:
            logger.warning(f"[EDS] Delete failed: {source_uid} not in the registry")
            return False
        if info['name'] == "Andromeda Contacts":
            logger.warning("[EDS] Refusing to delete Andromeda Contacts")
            return False
        if not info['removable']:
            logger.warning(f"[EDS] {source_uid} does not offer removal")
            return False

        try:
            self._get_bus().call_sync(
                EDS_SOURCES_BUS_NAME, info['path'], EDS_SOURCE_REMOVABLE_IFACE,
                "Remove", None, GLib.VariantType("()"),
                Gio.DBusCallFlags.NONE, EDS_CALL_TIMEOUT_MS, None)
        except Exception as e:
            logger.error(f"[EDS] Delete addressbook error: {e}")
            return False

        self.invalidate_sources_info()
        new_config = [s for s in self._saved_config_entries() if s['uid'] != source_uid]
        self.save_sources_config(new_config)

        self._remove_source(source_uid)
        self._rebuild_lookup_map()

        try:
            with self.db_ref.lock:
                self.db_ref.conn_contacts.execute("DELETE FROM contacts WHERE source_uid=?", (source_uid,))
                self.db_ref.conn_contacts.commit()
        except Exception as e:
            logger.warning(f"[EDS] Failed to clear local db for source {source_uid}: {e}")

        GLib.idle_add(self.emit, 'contacts-loaded')
        self._emit_books_changed_if_moved()
        return True

    def _remove_source(self, uid):
        """Stop monitoring a source and remove its contacts from cache and lookup map."""
        with self.sources_lock:
            self.sources.pop(uid, None)
        self._close_book(uid)

        with self.cache_lock:
            self._cache_loaded_sources.discard(uid)
            self._source_ranks.pop(uid, None)
            to_remove = [k for k, v in self.cache.items() if v.get('source_uid') == uid]
            for k in to_remove:
                del self.cache[k]
            for norm in list(self.lookup_map.keys()):
                remaining = [entry for entry in self.lookup_map[norm] if entry[2] != uid]
                if not remaining:
                    del self.lookup_map[norm]
                elif len(remaining) != len(self.lookup_map[norm]):
                    self.lookup_map[norm] = remaining

        logger.info(f"[EDS] Removed source: {uid}")

    def _rebuild_lookup_map(self):
        """Rebuild the phone number lookup map based on current cache and ranks."""
        with self.sources_lock:
            ranks = {uid: info.get('rank', 999) for uid, info in self.sources.items()}

        with self.cache_lock:
            self.lookup_map = {}

            for uid, contact in self.cache.items():
                source_uid = contact.get('source_uid')
                rank = ranks.get(source_uid, 999)

                if 'phones' in contact:
                    for p_data in contact['phones']:
                        norm = normalize_number(p_data[0])
                        if norm:
                            if norm not in self.lookup_map:
                                self.lookup_map[norm] = []
                            self.lookup_map[norm].append((rank, contact['name'], source_uid, uid))

    def _handle_backend_update(self, vcards, source_uid):
        """Fold a batch of vcards from a view into cache, lookup and mirror."""
        with self.sources_lock:
            source_info = self.sources.get(source_uid)
            rank = source_info['rank'] if source_info else None
        if source_info is None:
            return

        db_batch = []
        for vcard in vcards:
            data = parse_vcard_string(vcard, source_uid)
            uid = data.get('uid')
            if not uid:
                continue

            with self.cache_lock:
                existing = self.cache.get(uid)

                if (existing and
                        existing.get('name') == data.get('name') and
                        existing.get('phones') == data.get('phones') and
                        existing.get('emails') == data.get('emails') and
                        existing.get('vcard_hash') == data.get('vcard_hash')):
                    continue

                for p in existing.get('phones', []) if existing else []:
                    norm = normalize_number(p[0])
                    if norm and norm in self.lookup_map:
                        self.lookup_map[norm] = [x for x in self.lookup_map[norm] if x[3] != uid]
                        if not self.lookup_map[norm]:
                            del self.lookup_map[norm]

                self.cache[uid] = {
                    'uid': uid,
                    'source_uid': source_uid,
                    'name': data.get('name'),
                    'phones': data.get('phones', []),
                    'emails': data.get('emails', []),
                    'vcard_hash': data.get('vcard_hash'),
                    'is_fav': bool(data.get('is_fav')),
                }

                for p_data in data['phones']:
                    norm = normalize_number(p_data[0])
                    if not norm:
                        continue
                    self.lookup_map.setdefault(norm, []).append((rank, data['name'], source_uid, uid))
                    if existing:
                        GLib.idle_add(lambda n=norm, fn=data['name']: self.gsettings_mgr.update_special_list_names(n, fn) or False)

            db_batch.append(data)

        if db_batch:
            self.db_ref.upsert_contacts_batch(db_batch, source_uid)
            GLib.idle_add(self.emit, 'contacts-loaded')

    def _on_objects_removed(self, uids, source_uid):
        """Drop removed contacts from cache, lookup and mirror."""
        def task():
            removed_uids = []
            with self.cache_lock:
                for real_uid in uids:
                    uid = self._make_composite_uid(source_uid, real_uid)
                    contact = self.cache.pop(uid, None)
                    for p in contact.get('phones', []) if contact else []:
                        norm = normalize_number(p[0])
                        if norm and norm in self.lookup_map:
                            self.lookup_map[norm] = [x for x in self.lookup_map[norm] if x[3] != uid]
                            if not self.lookup_map[norm]:
                                del self.lookup_map[norm]
                    removed_uids.append(uid)
            for uid in removed_uids:
                self.db_ref.delete_contact(uid)
            GLib.idle_add(self.emit, 'contacts-loaded')

        run_in_background(task)

    def get_display_name(self, number):
        """Return the user-facing name for a number, honoring the blocklist."""
        if self.db_ref and self.db_ref.is_blocked(number):
            return _("Blocked Number")
        return self.get_contact_name(number)

    def remove_number_everywhere(self, number):
        """Remove a number from all contacts, deleting contacts left otherwise empty."""
        norm = normalize_number(number)
        for c in self.search_contacts(norm):
            uid = c[0]

            with self.cache_lock:
                full_contact = self.cache.get(uid)

            should_delete = False
            if full_contact:
                phones = full_contact.get('phones', [])
                other_phones = [p for p in phones if normalize_number(p[0]) != norm]
                emails = full_contact.get('emails', [])

                has_other_fields = False
                vcard = self.get_contact_vcard(uid) or full_contact.get('vcard', '')
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
        """Look up a contact name by phone number."""
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
        """Get the vCard for a contact (from DB)."""
        return self.db_ref.get_contact_vcard(uid)

    def search_contacts(self, query, limit=None, offset=0):
        """Search for contacts by name or number from the local contacts DB."""
        if not self.db_ref:
            return []
        return self.db_ref.search_contacts_db(query, limit=limit, offset=offset)

    def read_only_source_uids(self):
        """Return the uids of books that refuse writes; blocking, call from a worker.

        A window with a deferred backend has no registry names in
        self.sources, so this falls back to the sources info the
        daemon provides, which always carries the names.
        """
        uids = set()
        with self.sources_lock:
            named = {uid: info.get('name') for uid, info in self.sources.items() if info.get('name')}
        if named:
            return {uid for uid, name in named.items() if name == "Andromeda Contacts"}
        for item in self.get_sources_info():
            if item.get('name') == "Andromeda Contacts":
                uids.add(item.get('uid'))
        return uids

    def _is_andromeda_source(self, source_uid):
        """Return True when the source is the read-only Andromeda Contacts book."""
        with self.sources_lock:
            info = self.sources.get(source_uid)
        return bool(info) and info.get('name') == "Andromeda Contacts"

    def _writable_book_uid(self, source_uid=None):
        """Resolve which book a write lands in.

        A requested book is honored as requested: the factory opens any
        registry book by uid, live view or not, and a book the registry
        does not know fails the write honestly instead of quietly
        landing somewhere else. Only a write with no stated target goes
        to the user's default book, and the last-resort rank order
        never falls onto a read-only book.
        """
        if source_uid:
            return source_uid

        default = self._default_source_uid()
        with self.sources_lock:
            if default and default in self.sources:
                return default
            for info in sorted(self.sources.values(), key=lambda x: x['rank']):
                if info.get('name') != "Andromeda Contacts":
                    return info['uid']
        return default

    def save_contact_with_reason(self, vcard_string, uid=None, source_uid=None):
        """Save a contact and say why a refusal happened; blocking, call from a worker."""
        target = None
        if uid and ':' in uid:
            target = uid.split(':', 1)[0]
        elif source_uid:
            target = source_uid
        else:
            target = self._default_source_uid()
        if target and self._is_andromeda_source(target):
            return (False, "read-only")
        ok = self.save_contact(vcard_string, uid=uid, source_uid=source_uid)
        return (ok, "" if ok else "write-failed")

    def save_contact(self, vcard_string, uid=None, source_uid=None):
        """Save a contact from a VCard string; blocking, call from a worker."""
        lines = vcard_string.splitlines()
        cleaned_lines = []
        for line in lines:
            if is_property(line, "TEL"):
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
            else:
                cleaned_lines.append(line)

        final_vcard = "\n".join(cleaned_lines)

        try:
            real_uid = None
            target_uid = None

            if uid and isinstance(uid, str) and uid.strip():
                s_uid, r_uid = self._parse_composite_uid(uid)
                if not s_uid:
                    s_uid = source_uid
                    r_uid = uid

                if not s_uid:
                    with self.cache_lock:
                        cached = self.cache.get(uid)
                    if cached:
                        s_uid = cached.get('source_uid')

                if not s_uid:
                    logger.error(f"[EDS] Save failed: Could not determine source for UID {uid}")
                    return False

                if self._is_andromeda_source(s_uid):
                    logger.warning(f"[EDS] Refusing to modify Andromeda Contact {uid}")
                    return False

                target_uid = self._writable_book_uid(s_uid)
                real_uid = r_uid
            else:
                if source_uid and self._is_andromeda_source(source_uid):
                    logger.warning("[EDS] Refusing to save new Andromeda Contact")
                    return False

                target_uid = self._writable_book_uid(source_uid)

            if not target_uid:
                logger.error("[EDS] Save failed: No writable book found.")
                return False

            if real_uid:
                lines = final_vcard.splitlines()
                lines = [line for line in lines if not line.upper().startswith("UID:")]
                uid_line = f"UID:{real_uid}"
                end_index = next((i for i, line in enumerate(lines)
                                  if line.strip().upper().startswith("END:VCARD")), None)
                if end_index is None:
                    lines.append(uid_line)
                    lines.append("END:VCARD")
                else:
                    lines.insert(end_index, uid_line)
                final_vcard = "\n".join(lines)
                self._book_call(target_uid, "ModifyContacts",
                                GLib.Variant("(asu)", ([final_vcard], 0)), None)
                logger.info(f"[EDS] Modified: {real_uid}")
            else:
                self._book_call(target_uid, "CreateContacts",
                                GLib.Variant("(asu)", ([final_vcard], 0)), None)
                logger.info("[EDS] Created new contact")
            return True
        except Exception as e:
            logger.error(f"[EDS] Save Error: {e}")
            return False

    def delete_contact(self, uid):
        """Delete a contact by UID; blocking, call from a worker."""
        if not isinstance(uid, str) or not uid.strip():
            logger.error("[EDS] Delete failed: Invalid UID format")
            return False

        s_uid, r_uid = self._parse_composite_uid(uid)

        if not s_uid:
            with self.cache_lock:
                cached = self.cache.get(uid)
            if cached:
                s_uid = cached.get('source_uid')
                r_uid = uid
            else:
                logger.warning(f"[EDS] Delete warning: UID {uid} not found in cache and no source specified.")
                if ":" in uid:
                    parts = uid.split(":", 1)
                    s_uid = parts[0]
                    r_uid = parts[1]

        if not s_uid:
            logger.error(f"[EDS] Delete failed: Unknown source for {uid}")
            return False

        if self._is_andromeda_source(s_uid):
            logger.warning(f"[EDS] Refusing to delete Andromeda Contact {uid}")
            return False

        try:
            self._book_call(s_uid, "RemoveContacts",
                            GLib.Variant("(asu)", ([r_uid], 0)), None)
            logger.info(f"[EDS] Deleted: {uid}")
            return True
        except Exception as e:
            logger.error(f"[EDS] Delete Error: {e}")
            return False

    def delete_contacts(self, uids):
        """Delete multiple contacts by UIDs; blocking, call from a worker."""
        if not uids:
            return True

        uids_by_source = {}
        for uid in uids:
            s_uid, r_uid = self._parse_composite_uid(uid)
            if not s_uid:
                with self.cache_lock:
                    cached = self.cache.get(uid)
                if cached:
                    s_uid = cached.get('source_uid')
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
            if self._is_andromeda_source(s_uid):
                logger.warning(f"[EDS] Refusing to batch delete Andromeda Contacts from {s_uid}")
                success = False
                continue

            try:
                self._book_call(s_uid, "RemoveContacts",
                                GLib.Variant("(asu)", (r_uids, 0)), None)
                logger.info(f"[EDS] Batch deleted {len(r_uids)} contacts from {s_uid}")
            except Exception as e:
                logger.error(f"[EDS] Batch delete error for {s_uid}: {e}")
                success = False

        return success

    def delete_all_contacts(self, source_uid=None):
        """Delete all contacts, or only one source's; blocking, call from a worker."""
        with self.cache_lock:
            if source_uid:
                uids = [uid for uid, c in self.cache.items() if c.get('source_uid') == source_uid]
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
        """Return the vcard for a contact with one number added, or None.

        Pure read from cache and mirror, no write: a window builds the
        vcard here and hands it to the daemon to save, keeping the
        daemon the only writer of a book.
        """
        with self.cache_lock:
            contact = self.cache.get(uid)

        if not contact:
            logger.warning(f"[EDS] Add Number Failed: Contact {uid} not found in cache")
            return None

        vcard = unfold_vcard(contact.get('vcard', ''))
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

    def add_number_to_contact(self, uid, number, label="Mobile"):
        """Add a phone number to an existing contact; blocking, call from a worker.

        Daemon-side path: builds the vcard and writes it. Windows build
        the vcard with build_number_added_vcard and save through the
        daemon instead.
        """
        logger.info(f"[EDS] Adding number {number} to contact {uid}")
        final_vcard = self.build_number_added_vcard(uid, number, label)
        if not final_vcard:
            return False
        return self.save_contact(final_vcard, uid=uid)

    def remove_number_from_contact(self, uid, number):
        """Remove a phone number from an existing contact; blocking, call from a worker."""
        logger.info(f"[EDS] Removing number {number} from contact {uid}")
        with self.cache_lock:
            contact = self.cache.get(uid)

        if not contact:
            logger.warning(f"[EDS] Remove Number Failed: Contact {uid} not found in cache")
            return False

        vcard = unfold_vcard(contact.get('vcard', ''))
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
