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
from loguru import logger

gi.require_version('Geoclue', '2.0')
from gi.repository import GLib, GObject, Geoclue

from ...backend.utils.system_utils import is_location_enabled, enable_location

from gettext import gettext as _


class LocationManager(GObject.Object):
    """
    Manages location fetching via libgeoclue with robust error handling,
    warm-up delays for cold starts, and safe signal cleanup.
    """

    def __init__(self):
        super().__init__()
        self.simple_client = None
        self.active_callback = None
        self.progress_callback = None
        self.timeout_source = None
        self.location_signal_id = None
        self.is_fetching = False
        self.best_location = None
        self.accuracy_threshold = 1000
        self.WARMUP_DELAY = 2

    def get_current_location(self, callback, progress_callback=None, accuracy_threshold=1000, timeout=60):
        """
        Fetch current location asynchronously using Geoclue.Simple.
        """
        if self.is_fetching:
            logger.warning("[Location] Already fetching location. Ignoring new request.")
            return

        self.active_callback = callback
        self.progress_callback = progress_callback
        self.accuracy_threshold = accuracy_threshold
        self.best_location = None
        self.is_fetching = True

        self._request_timeout = timeout

        if self._check_and_enable_location():
            if self.progress_callback:
                self.progress_callback(_("Initializing location services..."))

            logger.info(f"[Location] Warm-up required. Waiting {self.WARMUP_DELAY}s...")
            GLib.timeout_add_seconds(self.WARMUP_DELAY, self._start_geoclue_client)
        else:
            self._start_geoclue_client()

    def _check_and_enable_location(self):
        """
        Ensures system location is enabled via GSettings.
        Returns True if we had to enable it (requiring warm-up), False otherwise.
        """
        if not is_location_enabled():
            logger.info("[Location] System location is OFF. Enabling...")
            return enable_location()
        return False

    def _start_geoclue_client(self):
        """Initiates the Geoclue connection."""
        if not self.is_fetching:
            return False

        logger.info("[Location] Starting Geoclue Simple Client...")

        try:
            Geoclue.Simple.new(
                "io.furios.Telephony",
                Geoclue.AccuracyLevel.EXACT,
                None,
                self._on_simple_ready
            )

            self.timeout_source = GLib.timeout_add_seconds(
                self._request_timeout,
                self._on_timeout
            )

        except Exception as e:
            logger.error(f"[Location] Failed to start Geoclue client: {e}")
            self._schedule_finish(None, None, None)

        return False

    def _on_simple_ready(self, source, result):
        """Callback when Geoclue Simple client is ready."""
        if not self.is_fetching:
            return

        try:
            simple = Geoclue.Simple.new_finish(result)
        except Exception as e:
            logger.error(f"[Location] Geoclue creation failed (likely race condition): {e}")
            self._schedule_finish(None, None, None)
            return

        self.simple_client = simple
        logger.info("[Location] Geoclue client ready.")

        client = self.simple_client.get_client()
        if client:
            logger.debug(f"[Location] Client Active: {client.props.active}")
            if not client.props.active:
                if self.progress_callback:
                    self.progress_callback(_("Waiting for location service..."))

        location = self.simple_client.get_location()
        is_good_enough = False
        if location and (location.props.latitude != 0 or location.props.longitude != 0):
            logger.info("[Location] Immediate location available.")
            is_good_enough = self._process_location(location)

        if is_good_enough:
            return

        self.location_signal_id = self.simple_client.connect(
            "notify::location",
            self._on_location_updated
        )

        if self.progress_callback:
            self.progress_callback(_("Waiting for satellite fix..."))

    def _on_location_updated(self, client, param):
        """Handle location updates."""
        if not self.is_fetching:
            return

        location = client.get_location()
        logger.info("[Location] Location updated signal received.")
        self._process_location(location)

    def _process_location(self, location):
        """Extract coordinates and notify callback."""
        try:
            lat = location.props.latitude
            lon = location.props.longitude
            accuracy = location.props.accuracy

            logger.info(f"[Location] Fix obtained: {lat}, {lon} (acc: {accuracy}m)")

            if self.best_location is None or accuracy < self.best_location.props.accuracy:
                self.best_location = location

            if accuracy <= self.accuracy_threshold:
                self._schedule_finish(lat, lon, accuracy)
                return True

            return False

        except Exception as e:
            logger.error(f"[Location] Failed to read location properties: {e}")
            return False

    def _on_timeout(self):
        """Handle timeout if no location found."""
        self.timeout_source = None

        logger.warning("[Location] Timeout waiting for location.")

        if self.best_location:
            try:
                lat = self.best_location.props.latitude
                lon = self.best_location.props.longitude
                accuracy = self.best_location.props.accuracy
                logger.info(f"[Location] Returning best location found: acc={accuracy}m")
                self._schedule_finish(lat, lon, accuracy)
            except Exception as e:
                logger.error(f"[Location] Error processing best location: {e}")
                self._schedule_finish(None, None, None)
        else:
            self._schedule_finish(None, None, None)

        return False

    def _schedule_finish(self, lat, lon, acc):
        """
        Schedules the result dispatch on the idle loop.
        This ensures we are OUT of any signal handlers or stack frames
        before we trigger callbacks or destroy objects.
        """
        GLib.idle_add(self._dispatch_result, lat, lon, acc)

    def _dispatch_result(self, lat, lon, acc):
        """Executes callback and cleanup safely on the main loop."""
        if self.active_callback:
            try:
                self.active_callback(lat, lon, acc)
            except Exception as e:
                logger.error(f"[Location] User callback raised exception: {e}")

        self._cleanup()
        return False

    def _cleanup(self):
        """Reset state and stop client."""
        self.active_callback = None
        self.progress_callback = None
        self.is_fetching = False

        if self.timeout_source:
            GLib.source_remove(self.timeout_source)
            self.timeout_source = None

        if self.simple_client:
            client = self.simple_client
            self.simple_client = None

            if self.location_signal_id:
                try:
                    client.disconnect(self.location_signal_id)
                except Exception as e:
                    logger.warning(f"[Location] Failed to disconnect signal: {e}")

            self.location_signal_id = None

            def _destroy_client():
                nonlocal client
                client = None
                return False

            GLib.timeout_add(2000, _destroy_client)
