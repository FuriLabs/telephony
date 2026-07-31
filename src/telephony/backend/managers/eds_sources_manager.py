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
gi.require_version('EBook', '1.2')
from gi.repository import EBook

import threading

from functools import partial

from ..utils.thread_utils import run_in_background
import json

gi.require_version('EDataServer', '1.2')
from gi.repository import EDataServer, GLib
from loguru import logger
from ..utils.phone_utils import normalize_number

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


class EdsSourcesManager:
    def _init_backend(self):
        """Initialize EDS backend connection."""
        try:
            self.registry = EDataServer.SourceRegistry.new_sync(None)
        except Exception as e:
            logger.error(f"[EDS] Registry Init Error: {e}")
            return

        self.registry.connect("source-added", lambda *a: self.invalidate_sources_info())
        self.registry.connect("source-removed", lambda *a: self.invalidate_sources_info())

        self._load_sources_config()

    def invalidate_sources_info(self):
        """Drop the cached sources info so the next query re-reads the registry."""
        self._sources_info_cache = None

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
        """Connect to a single source and start monitoring."""
        uid = source_info['uid']
        source_obj = source_info['source_obj']
        logger.info(f"[EDS] Initializing source: {source_info['name']} (Rank {source_info['rank']})")

        self._load_from_local_db(uid, source_info['rank'])

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

    def _load_from_local_db(self, source_uid, rank):
        """Load contacts from local database cache for a specific source once."""
        with self.cache_lock:
            if source_uid in self._cache_loaded_sources:
                logger.debug(f"[EDS] Cache for {source_uid} already loaded, skipping")
                return
            self._cache_loaded_sources.add(source_uid)

        try:
            contacts = self.db_ref.get_cached_contacts(source_uid)
        except Exception as e:
            logger.error(f"[EDS] DB fetch error: {e}")
            with self.cache_lock:
                self._cache_loaded_sources.discard(source_uid)
            return

        count = 0
        updates = {}
        lookup_updates = {}
        for c in contacts:
            real_uid = c['uid']
            if real_uid:
                composite_uid = real_uid
                if ":" not in composite_uid:
                    composite_uid = f"{source_uid}:{real_uid}"

                c['source_uid'] = source_uid
                updates[composite_uid] = c

                for p_data in c['phones']:
                    norm = normalize_number(p_data[0])
                    if norm:
                        if norm not in lookup_updates:
                            lookup_updates[norm] = []
                        lookup_updates[norm].append((rank, c['name'], source_uid, composite_uid))
                count += 1

        with self.cache_lock:
            self.cache.update(updates)
            for norm, entries in lookup_updates.items():
                if norm not in self.lookup_map:
                    self.lookup_map[norm] = []
                self.lookup_map[norm].extend(entries)

        if count > 0:
            logger.info(f"[EDS] Loaded {count} contacts from cache for {source_uid}.")

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
        """Ask every backend supporting refresh to re-sync with its remote store."""
        with self.sources_lock:
            clients = [(uid, info.get('client')) for uid, info in self.sources.items()]

        refreshed = 0
        for uid, client in clients:
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
