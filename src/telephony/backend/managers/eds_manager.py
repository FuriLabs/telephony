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

from .eds_sources_manager import EdsSourcesMixin
from .eds_cache_manager import EdsCacheMixin
from .eds_events_manager import EdsEventsMixin
from .eds_contacts_manager import EdsContactsMixin

from ...backend.utils.thread_utils import run_in_background

import gi
import threading
import json
from loguru import logger

gi.require_version('EDataServer', '1.2')
gi.require_version('EBook', '1.2')
from gi.repository import GLib, GObject


class EdsManager(GObject.Object, EdsSourcesMixin, EdsCacheMixin, EdsEventsMixin, EdsContactsMixin):
    """
    Manages contact data via Evolution Data Server (EDS).
    Supports multiple address books with ranking.
    """
    __gsignals__ = {
        'contacts-loaded': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'contact-added': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'contact-removed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'contact-modified': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'source-switched': (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self):
        """Initialize the EDS manager."""
        super().__init__()
        self.sources = {}
        self.sources_lock = threading.Lock()

        self.cache = {}
        self.cache_lock = threading.Lock()

        self.lookup_map = {}

        self.db_ref = None
        self.gsettings_mgr = None
        self.registry = None
        self.is_ready = False

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
                for item in saved_config:
                    if item.get('enabled', True):
                        self._load_from_local_db(item['uid'], item.get('rank', 0))
                GLib.idle_add(self.emit, 'contacts-loaded')

            run_in_background(load_sources_async)
