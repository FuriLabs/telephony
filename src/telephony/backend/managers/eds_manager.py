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

import gi
import threading
import json
from functools import partial
from gettext import gettext as _

from telephony.backend.utils.log_utils import logger

gi.require_version('EDataServer', '1.2')
gi.require_version('EBook', '1.2')
gi.require_version('EBookContacts', '1.2')
from gi.repository import EBook, EBookContacts, EDataServer, GLib, GObject

from ..utils.thread_utils import run_in_background
from ..utils.phone_utils import normalize_number
from ..utils.vcard_utils import parse_contact_safe, unfold_vcard

ADDRESS_BOOK_EXTENSION = "Address Book"
COLLECTION_EXTENSION = "Collection"
EBOOK_CONNECT_TIMEOUT_SECONDS = 5
EBOOK_CONNECT_NO_WAIT = GLib.MAXUINT32
LOCAL_BACKEND_NAMES = ("local",)
CONNECTION_STATUS_KEYS = {
    EDataServer.SourceConnectionStatus.CONNECTED: "connected",
    EDataServer.SourceConnectionStatus.CONNECTING: "connecting",
    EDataServer.SourceConnectionStatus.DISCONNECTED: "disconnected",
    EDataServer.SourceConnectionStatus.AWAITING_CREDENTIALS: "awaiting-credentials",
    EDataServer.SourceConnectionStatus.SSL_FAILED: "ssl-failed",
}


class EdsManager(GObject.Object):
    """
    Manages contact data via Evolution Data Server (EDS).
    Supports multiple address books with ranking.
    """
    __gsignals__ = {
        'contacts-loaded': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, owns_live_views=True):
        """Initialize the EDS manager."""
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
        self.registry = None
        self.is_ready = False
        self._sources_info_cache = None

    def set_db(self, db_manager, gsettings_mgr):
        """Set the database manager reference and start initialization."""
        self.db_ref = db_manager
        self.gsettings_mgr = gsettings_mgr

        run_in_background(self._load_cache_initial)
        run_in_background(self._init_backend)

    def _load_cache_initial(self):
        """Load contacts from local DB before EDS connection."""
        saved_config_json = self.gsettings_mgr.get_setting("address_book_sources") if self.gsettings_mgr else None
        saved_config = []
        if saved_config_json:
            try:
                saved_config = json.loads(saved_config_json)
            except Exception as e:
                logger.warning(f"[EDS] Failed to parse saved config: {e}")

        if saved_config:
            saved_config.sort(key=lambda x: x.get('rank', 999))

            def load_sources_async():
                with self.reload_lock:
                    for item in saved_config:
                        if item.get('enabled', True):
                            self._load_from_local_db(item['uid'], item.get('rank', 0))
                GLib.idle_add(self.emit, 'contacts-loaded')

            run_in_background(load_sources_async)

    def _init_backend(self):
        """Initialize EDS backend connection."""
        try:
            self.registry = EDataServer.SourceRegistry.new_sync(None)
        except Exception as e:
            logger.error(f"[EDS] Registry Init Error: {e}")
            return

        self.registry.connect("source-added", lambda *a: self.invalidate_sources_info())
        self.registry.connect("source-removed", self._on_source_removed)

        self._load_sources_config()

    def invalidate_sources_info(self):
        """Drop the cached sources info so the next query re-reads the registry."""
        self._sources_info_cache = None

    def _on_source_removed(self, registry, source):
        """Forget a book that was deleted elsewhere in the system.

        The registry reports the deletion to every process, but only the
        one that asked for it dropped the contacts, so the others kept
        offering to call numbers from a book that no longer exists.
        """
        self.invalidate_sources_info()
        uid = source.get_uid()

        with self.sources_lock:
            if uid not in self.sources:
                return

        def task():
            logger.info(f"[EDS] Address book {uid} was removed, dropping its contacts")
            self._remove_source(uid)
            GLib.idle_add(self.emit, 'contacts-loaded')

        run_in_background(task)

    def reload(self):
        """Tear down all source connections and reload configuration and contacts."""
        def task():
            with self.reload_lock:
                with self.sources_lock:
                    uids = list(self.sources.keys())
                for uid in uids:
                    self._remove_source(uid)
                self.invalidate_sources_info()
                self._load_sources_config_locked()

        run_in_background(task)

    def _enabled_registry_sources(self):
        """Return registry address book sources that are effectively enabled."""
        sources = self.registry.list_sources(ADDRESS_BOOK_EXTENSION)
        return [s for s in sources if self.registry.check_enabled(s)]

    def _load_sources_config(self):
        """Load configuration and initialize sources, serialized against reloads."""
        with self.reload_lock:
            self._load_sources_config_locked()

    def _load_sources_config_locked(self):
        """Load configuration and initialize sources; caller holds reload_lock."""
        try:
            all_sources = self._enabled_registry_sources()

            default_source = self.registry.ref_default_address_book()
            default_uid = default_source.get_uid() if default_source else None

            saved_config_json = self.gsettings_mgr.get_setting("address_book_sources")
            saved_config = []
            if saved_config_json:
                try:
                    saved_config = json.loads(saved_config_json)
                except Exception as e:
                    logger.warning(f"[EDS] Failed to parse saved config (backend): {e}")

            final_sources_list = []

            if not saved_config:
                temp_list = []
                for s in all_sources:
                    uid = s.get_uid()
                    name = s.get_display_name()
                    is_def = (uid == default_uid)
                    temp_list.append({'uid': uid, 'name': name, 'source_obj': s, 'is_def': is_def})

                temp_list.sort(key=lambda x: (0 if x['is_def'] else 1, x['name'].lower()))

                for idx, item in enumerate(temp_list):
                    final_sources_list.append({
                        'uid': item['uid'],
                        'name': item['name'],
                        'rank': idx,
                        'enabled': True,
                        'is_system_default': item['is_def'],
                        'source_obj': item['source_obj']
                    })
            else:

                processed_uids = set()

                current_rank = 0

                for conf in saved_config:
                    uid = conf['uid']
                    s_obj = next((s for s in all_sources if s.get_uid() == uid), None)
                    if s_obj:
                        is_def = (uid == default_uid)
                        enabled = conf.get('enabled', True)
                        if is_def:
                            enabled = True

                        final_sources_list.append({
                            'uid': uid,
                            'name': s_obj.get_display_name(),
                            'rank': current_rank,
                            'enabled': enabled,
                            'is_system_default': is_def,
                            'source_obj': s_obj
                        })
                        processed_uids.add(uid)
                        current_rank += 1

                new_sources = []
                for s in all_sources:
                    uid = s.get_uid()
                    if uid not in processed_uids:
                        is_def = (uid == default_uid)
                        new_sources.append({
                            'uid': uid,
                            'name': s.get_display_name(),
                            'source_obj': s,
                            'is_def': is_def
                        })

                new_sources.sort(key=lambda x: (0 if x['is_def'] else 1, x['name'].lower()))

                for item in new_sources:
                    final_sources_list.append({
                        'uid': item['uid'],
                        'name': item['name'],
                        'rank': current_rank,
                        'enabled': True,
                        'is_system_default': item['is_def'],
                        'source_obj': item['source_obj']
                    })
                    current_rank += 1

            self.save_sources_config(final_sources_list)

            with self.sources_lock:
                self.sources = {}

            threads = []
            for item in final_sources_list:
                if item['enabled']:
                    t = threading.Thread(target=self._init_source_safe, args=(item,), daemon=True)
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

        except Exception as e:
            logger.error(f"[EDS] Load Config Error: {e}")

    def save_sources_config(self, sources_list):
        """Save the current sources configuration to DB."""
        to_save = []
        for item in sources_list:
            to_save.append({
                'uid': item['uid'],
                'enabled': item['enabled'],
                'rank': item['rank']
            })
        try:
            self.gsettings_mgr.set_setting("address_book_sources", json.dumps(to_save))
        except Exception as e:
            logger.error(f"[EDS] Save Config Error: {e}")

    def _is_local_backend(self, source_obj):
        """Return True when the source uses a local backend with no connection."""
        try:
            extension = source_obj.get_extension(ADDRESS_BOOK_EXTENSION)
            return extension.get_backend_name() in LOCAL_BACKEND_NAMES
        except Exception as e:
            logger.debug(f"[EDS] Backend name lookup failed, assuming remote: {e}")
            return False

    def _has_cached_contacts(self, source_uid):
        """Return True when the in-memory cache holds contacts for the source."""
        with self.cache_lock:
            return any(v.get('source_uid') == source_uid for v in self.cache.values())

    def _init_source_safe(self, source_info):
        """Thread entry for source init that can never propagate an exception."""
        try:
            self._init_source(source_info)
        except Exception as e:
            logger.error(f"[EDS] Source init failed for {source_info.get('uid')}: {e}")

    def _init_source(self, source_info):
        """Connect to a single source and start monitoring.

        A window instance stops here with the mirror loaded: it reads
        contacts from the daemon's mirror and only needs a book client
        when a write happens, which connects lazily at that moment.
        Six per-book connects at every window start were the whole
        reason the syncing banner lingered.
        """
        uid = source_info['uid']
        source_obj = source_info['source_obj']
        logger.info(f"[EDS] Initializing source: {source_info['name']} (Rank {source_info['rank']})")

        self._load_from_local_db(uid, source_info['rank'])

        if not self.owns_live_views:
            with self.sources_lock:
                self.sources[uid] = source_info
            return

        try:
            is_local = self._is_local_backend(source_obj)
            wait_seconds = EBOOK_CONNECT_NO_WAIT if is_local else EBOOK_CONNECT_TIMEOUT_SECONDS
            client = EBook.BookClient.connect_sync(source_obj, wait_seconds, None)
            source_info['client'] = client

            try:
                success, uids = client.get_contacts_uids_sync('(contains "x-evolution-any-field" "")', None)
                connected = source_obj.get_connection_status() == EDataServer.SourceConnectionStatus.CONNECTED
                if success and (is_local or connected):
                    comp_uids = [self._make_composite_uid(uid, real_uid) for real_uid in uids]
                    if comp_uids or not self._has_cached_contacts(uid):
                        self.db_ref.sync_deleted_contacts(uid, comp_uids)
                    else:
                        logger.warning(f"[EDS] Skipping empty deletion sweep for {uid}: backend may not be ready")
                elif success:
                    logger.info(f"[EDS] Deferring deletion sweep for {uid}: backend not connected yet")
            except Exception as ex:
                logger.error(f"[EDS] Sync Deletes Failed for {uid}: {ex}")

            query = '(contains "x-evolution-any-field" "")'
            success, view = client.get_view_sync(query, None)

            if success:
                source_info['view'] = view
                source_info['view_handlers'] = [
                    view.connect("objects-added", partial(self._on_objects_added, source_uid=uid)),
                    view.connect("objects-modified", partial(self._on_objects_modified, source_uid=uid)),
                    view.connect("objects-removed", partial(self._on_objects_removed, source_uid=uid)),
                ]
                view.start()
                logger.info(f"[EDS] Live view started for {uid}")

            with self.sources_lock:
                self.sources[uid] = source_info

        except Exception as e:
            logger.error(f"[EDS] Client Connect Error for {uid}: {e}")

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
        with self.reload_lock:
            with self.cache_lock:
                ranks = dict(self._source_ranks)

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

        GLib.idle_add(self.emit, 'contacts-loaded')

    def _make_composite_uid(self, source_uid, real_uid):
        return f"{source_uid}:{real_uid}"

    def _parse_composite_uid(self, composite_uid):
        if ":" in composite_uid:
            parts = composite_uid.split(":", 1)
            return parts[0], parts[1]
        return None, composite_uid

    def get_sources_info(self):
        """Return list of all sources with their status/rank for Settings UI."""
        if self._sources_info_cache is not None:
            result = [dict(item) for item in self._sources_info_cache]
            for item in result:
                if item.get('is_local') or not self.registry:
                    continue
                source = self.registry.ref_source(item['uid'])
                if source:
                    item['status'] = CONNECTION_STATUS_KEYS.get(source.get_connection_status())
            return result

        try:
            all_sources = self._enabled_registry_sources()
            default_source = self.registry.ref_default_address_book()
            default_uid = default_source.get_uid() if default_source else None

            saved_config_json = self.gsettings_mgr.get_setting("address_book_sources")
            saved_config = []
            if saved_config_json:
                saved_config = json.loads(saved_config_json)

            result = []

            uids_processed = set()

            current_rank = 0
            for conf in saved_config:
                uid = conf['uid']
                s = next((x for x in all_sources if x.get_uid() == uid), None)
                if s:
                    is_def = (uid == default_uid)
                    is_local, removable, status_key, account = self._source_backend_info(s)
                    result.append({
                        'uid': uid,
                        'name': s.get_display_name(),
                        'rank': current_rank,
                        'enabled': conf.get('enabled', True) or is_def,
                        'is_system_default': is_def,
                        'is_local': is_local,
                        'removable': removable,
                        'status': status_key,
                        'account': account
                    })
                    uids_processed.add(uid)
                    current_rank += 1

            for s in all_sources:
                uid = s.get_uid()
                if uid not in uids_processed:
                    is_def = (uid == default_uid)
                    is_local, removable, status_key, account = self._source_backend_info(s)
                    result.append({
                        'uid': uid,
                        'name': s.get_display_name(),
                        'rank': current_rank,
                        'enabled': True,
                        'is_system_default': is_def,
                        'is_local': is_local,
                        'removable': removable,
                        'status': status_key,
                        'account': account
                    })
                    current_rank += 1

            self._sources_info_cache = [dict(item) for item in result]
            return result

        except Exception as e:
            logger.error(f"[EDS] Get Sources Info Error: {e}")
            return []

    def refresh_backends(self):
        """Ask every backend supporting refresh to re-sync with its remote store.

        Blocking, call from a worker: clients connect on first use.
        """
        with self.sources_lock:
            infos = list(self.sources.values())

        refreshed = 0
        for info in infos:
            uid = info['uid']
            client = self._ensure_client(info)
            if not client:
                continue
            try:
                if client.check_refresh_supported():
                    client.refresh_sync(None)
                    refreshed += 1
                    logger.info(f"[EDS] Backend refresh started for {uid}")
            except Exception as e:
                logger.warning(f"[EDS] Backend refresh failed for {uid}: {e}")
        return refreshed

    def sync_available_sources(self):
        """Reload when the registry's address book list differs from the config."""
        if not self.registry:
            return False
        try:
            registry_uids = {s.get_uid() for s in self._enabled_registry_sources()}
        except Exception as e:
            logger.error(f"[EDS] Registry listing failed: {e}")
            return False

        config_uids = set()
        saved_config_json = self.gsettings_mgr.get_setting("address_book_sources")
        if saved_config_json:
            try:
                config_uids = {item['uid'] for item in json.loads(saved_config_json)}
            except Exception as e:
                logger.warning(f"[EDS] Failed to parse saved config (sync check): {e}")

        if registry_uids == config_uids:
            return False

        logger.info("[EDS] Address book list changed, reloading sources")
        self.invalidate_sources_info()
        self.reload()
        return True

    def create_local_addressbook(self, name):
        """Create a new local address book source and reload the sources."""
        if not self.registry:
            return False
        try:
            source = EDataServer.Source.new(None, None)
            source.set_display_name(name)
            source.set_parent("local-stable")
            extension = source.get_extension(ADDRESS_BOOK_EXTENSION)
            extension.set_backend_name("local")
            self.registry.commit_source_sync(source, None)
            self.invalidate_sources_info()
            self.reload()
            return True
        except Exception as e:
            logger.error(f"[EDS] Create addressbook error: {e}")
            return False

    def _source_backend_info(self, source):
        """Return (is_local, removable, status_key, account) for a registry source."""
        try:
            extension = source.get_extension(ADDRESS_BOOK_EXTENSION)
            is_local = extension.get_backend_name() in LOCAL_BACKEND_NAMES
        except Exception as e:
            logger.debug(f"[EDS] Backend info lookup failed: {e}")
            is_local = False

        status_key = None
        if not is_local:
            status_key = CONNECTION_STATUS_KEYS.get(source.get_connection_status())

        return is_local, bool(source.get_removable()), status_key, self._source_account_name(source)

    def _source_account_name(self, source):
        """Return the display name of the account collection owning a source."""
        parent_uid = source.get_parent()
        if not parent_uid or not self.registry:
            return ""
        parent = self.registry.ref_source(parent_uid)
        if not parent:
            return ""
        if parent.has_extension(COLLECTION_EXTENSION):
            return parent.get_display_name() or ""
        if parent_uid.endswith("-stable"):
            return ""
        return parent.get_display_name() or ""

    def set_default_addressbook(self, uid):
        """Set the default address book source."""
        if not self.registry:
            return False

        source = self.registry.ref_source(uid)
        if source:
            self.registry.set_default_address_book(source)
            self.invalidate_sources_info()
            return True
        return False

    def update_sources_config(self, new_config_list):
        """Update configuration from Settings UI and reload."""
        self.save_sources_config(new_config_list)
        self.invalidate_sources_info()
        run_in_background(self._update_sources_task, new_config_list,)

    def _update_sources_task(self, new_config_list):
        """Intelligently update sources based on new configuration."""
        with self.reload_lock:
            self._update_sources_task_locked(new_config_list)

    def _update_sources_task_locked(self, new_config_list):
        """Apply a configuration update; caller holds reload_lock."""
        all_sources = self._enabled_registry_sources()

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
                    s_obj = next((s for s in all_sources if s.get_uid() == uid), None)
                    if s_obj:
                        to_enable[uid] = {
                            'uid': uid,
                            'name': s_obj.get_display_name(),
                            'rank': item['rank'],
                            'source_obj': s_obj,
                            'enabled': True
                        }

        if not to_enable and not to_disable and not rank_changed:
            return

        for uid in to_disable:
            self._remove_source(uid)

        for uid, info in to_enable.items():
            self._init_source(info)

        if rank_changed or to_enable or to_disable:
            self._rebuild_lookup_map()
            GLib.idle_add(self.emit, 'contacts-loaded')

    def delete_addressbook(self, source_uid):
        """Delete an entire address book source from the system registry."""
        if not self.registry:
            return False

        if source_uid == "system-address-book":
            logger.warning("[EDS] Refusing to delete system-address-book")
            return False

        try:
            source = self.registry.ref_source(source_uid)
            if source:
                name = source.get_display_name()
                if name == "Andromeda Contacts":
                    logger.warning("[EDS] Refusing to delete Andromeda Contacts")
                    return False

                source.remove_sync(None)
                self.invalidate_sources_info()

                saved_config_json = self.gsettings_mgr.get_setting("address_book_sources")
                if saved_config_json:
                    saved_config = json.loads(saved_config_json)
                    new_config = [s for s in saved_config if s['uid'] != source_uid]
                    self.gsettings_mgr.set_setting("address_book_sources", json.dumps(new_config))

                self._remove_source(source_uid)
                self._rebuild_lookup_map()

                try:
                    with self.db_ref.lock:
                        self.db_ref.conn_contacts.execute("DELETE FROM contacts WHERE source_uid=?", (source_uid,))
                        self.db_ref.conn_contacts.commit()
                except Exception as e:
                    logger.warning(f"[EDS] Failed to clear local db for source {source_uid}: {e}")

                GLib.idle_add(self.emit, 'contacts-loaded')
                return True
        except Exception as e:
            logger.error(f"[EDS] Delete addressbook error: {e}")

        return False

    def _remove_source(self, uid):
        """Stop monitoring a source and remove its contacts from cache and lookup map."""
        with self.sources_lock:
            info = self.sources.pop(uid, None)

        view = info.get('view') if info else None
        if view:
            for handler_id in info.get('view_handlers', []):
                try:
                    view.disconnect(handler_id)
                except Exception as e:
                    logger.debug(f"[EDS] View handler disconnect error (ignorable): {e}")
            try:
                view.stop()
            except Exception as e:
                logger.debug(f"[EDS] View stop error (ignorable): {e}")

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

    def _handle_backend_update(self, contacts, source_uid):
        """Handle updates from the EDS backend for a specific source."""
        with self.sources_lock:
            source_info = self.sources.get(source_uid)
            rank = source_info['rank'] if source_info else None
        if source_info is None:
            return

        db_batch = []
        for c in contacts:
            data = parse_contact_safe(c, source_uid)
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

            with self.cache_lock:
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

    def _on_objects_added(self, view, contacts, source_uid):
        """Handle objects added signal."""
        run_in_background(self._handle_backend_update, contacts, source_uid)

    def _on_objects_modified(self, view, contacts, source_uid):
        """Handle objects modified signal."""
        run_in_background(self._handle_backend_update, contacts, source_uid)

    def _on_objects_removed(self, view, uids, source_uid):
        """Handle objects removed signal."""
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

    def _is_andromeda_source(self, source_uid):
        """Return True when the source is the read-only Andromeda Contacts book."""
        with self.sources_lock:
            info = self.sources.get(source_uid)
        return bool(info) and info.get('name') == "Andromeda Contacts"

    def _ensure_client(self, info):
        """Return the source's book client, connecting on first use.

        Blocking, call from a worker. Windows carry no clients until a
        write happens, and the wait is bounded so a wedged factory
        surfaces as a logged failure instead of a silent hang.
        """
        client = info.get('client')
        if client:
            return client
        try:
            client = EBook.BookClient.connect_sync(
                info['source_obj'], EBOOK_CONNECT_TIMEOUT_SECONDS, None)
        except Exception as e:
            logger.error(f"[EDS] Lazy connect failed for {info['uid']}: {e}")
            return None
        with self.sources_lock:
            info['client'] = client
        return client

    def _get_writable_client(self, source_uid=None):
        """Get the client for source_uid, or the highest ranked when unspecified.

        Blocking, call from a worker: the client connects on first use.
        """
        with self.sources_lock:
            if source_uid:
                info = self.sources.get(source_uid)
            else:
                sorted_sources = sorted(self.sources.values(), key=lambda x: x['rank'])
                info = sorted_sources[0] if sorted_sources else None
        if info is None:
            logger.warning(f"[EDS] No source available for client request ({source_uid})")
            return None
        return self._ensure_client(info)

    def save_contact(self, vcard_string, uid=None, source_uid=None):
        """Save a contact from a VCard string."""
        lines = vcard_string.splitlines()
        cleaned_lines = []
        for line in lines:
            if line.startswith("TEL"):
                try:
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        raw = parts[1].strip()
                        key_part = parts[0]

                        if "X-EVOLUTION-E164" in key_part:
                            subparts = key_part.split(";")
                            new_subparts = [sp for sp in subparts if not sp.startswith("X-EVOLUTION-E164")]
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
            client = None

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

                if s_uid:
                    if self._is_andromeda_source(s_uid):
                        logger.warning(f"[EDS] Refusing to modify Andromeda Contact {uid}")
                        return False

                    client = self._get_writable_client(s_uid)
                    real_uid = r_uid
                else:
                    logger.error(f"[EDS] Save failed: Could not determine source for UID {uid}")
                    return False
            else:
                if source_uid and self._is_andromeda_source(source_uid):
                    logger.warning("[EDS] Refusing to save new Andromeda Contact")
                    return False

                client = self._get_writable_client(source_uid)

            if not client:
                logger.error("[EDS] Save failed: No writable client found.")
                return False

            if real_uid:
                lines = final_vcard.splitlines()
                lines = [line for line in lines if not line.upper().startswith("UID:")]
                uid_line = f"UID:{real_uid}"
                if "END:VCARD" in lines:
                    idx = lines.index("END:VCARD")
                    lines.insert(idx, uid_line)
                else:
                    lines.append(uid_line)
                    lines.append("END:VCARD")
                final_vcard = "\n".join(lines)
                contact = EBookContacts.Contact.new_from_vcard(final_vcard)
                client.modify_contact_sync(contact, EBookContacts.BookOperationFlags.NONE, None)
                logger.info(f"[EDS] Modified: {real_uid}")
            else:
                contact = EBookContacts.Contact.new_from_vcard(final_vcard)
                client.add_contact_sync(contact, EBookContacts.BookOperationFlags.NONE, None)
                logger.info("[EDS] Created new contact")
            return True
        except Exception as e:
            logger.error(f"[EDS] Save Error: {e}")
            return False

    def delete_contact(self, uid):
        """Delete a contact by UID."""
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
            client = self._get_writable_client(s_uid)
            if not client:
                logger.error(f"[EDS] Delete failed: No client for {s_uid}")
                return False

            client.remove_contact_by_uid_sync(r_uid, EBookContacts.BookOperationFlags.NONE, None)
            logger.info(f"[EDS] Deleted: {uid}")
            return True
        except Exception as e:
            logger.error(f"[EDS] Delete Error: {e}")
            return False

    def delete_contacts(self, uids):
        """Delete multiple contacts by UIDs (Batch Operation)."""
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
                client = self._get_writable_client(s_uid)
                if not client:
                    logger.error(f"[EDS] Batch delete failed: No client for {s_uid}")
                    success = False
                    continue

                client.remove_contacts_sync(r_uids, EBookContacts.BookOperationFlags.NONE, None)
                logger.info(f"[EDS] Batch deleted {len(r_uids)} contacts from {s_uid}")

            except Exception as e:
                logger.error(f"[EDS] Batch delete error for {s_uid}: {e}")
                success = False

        return success

    def delete_all_contacts(self, source_uid=None):
        """
        Delete all contacts (Dangerous).
        If source_uid is provided, only deletes from that source.
        """
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

    def add_number_to_contact(self, uid, number, label="Mobile"):
        """Add a phone number to an existing contact."""
        logger.info(f"[EDS] Adding number {number} to contact {uid}")
        with self.cache_lock:
            contact = self.cache.get(uid)

        if not contact:
            logger.warning(f"[EDS] Add Number Failed: Contact {uid} not found in cache")
            return False

        vcard = unfold_vcard(contact.get('vcard', ''))
        if not vcard:
            vcard = self.get_contact_vcard(uid)
            if not vcard:
                logger.warning(f"[EDS] Add Number Failed: No VCard for {uid}")
                return False
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

        final_vcard = "\n".join(new_lines)
        return self.save_contact(final_vcard, uid=uid)

    def remove_number_from_contact(self, uid, number):
        """Remove a phone number from an existing contact."""
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
            if not line.startswith("TEL"):
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
