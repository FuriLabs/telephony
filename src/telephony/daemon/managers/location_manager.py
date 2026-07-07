# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import gi
from telephony.shared.utils.log_utils import logger

from gi.repository import GLib, GObject

from telephony.shared.utils.system_utils import is_location_enabled, enable_location
from telephony.shared.constants import APP_ID

from gettext import gettext as _

ACCURACY_THRESHOLD_METERS = 1000
WARMUP_DELAY_SECONDS = 2


class LocationManager(GObject.Object):

    def __init__(self):
        super().__init__()
        self.simple_client = None
        self.active_callback = None
        self.progress_callback = None
        self.timeout_source = None
        self.location_signal_id = None
        self.is_fetching = False
        self.best_location = None
        self.accuracy_threshold = ACCURACY_THRESHOLD_METERS
        self.WARMUP_DELAY = WARMUP_DELAY_SECONDS

    def get_current_location(self, callback, progress_callback=None, accuracy_threshold=1000, timeout=60):
        if self.is_fetching:
            logger.warning("[Location] Already fetching location. Ignoring new request.")
            return

        self.active_callback = callback
        self.progress_callback = progress_callback
        self.accuracy_threshold = accuracy_threshold
        self.best_location = None
        self.is_fetching = True

        self._request_timeout = timeout

        if self.check_and_enable_location():
            if self.progress_callback:
                self.progress_callback(_("Initializing location services..."))

            logger.info(f"[Location] Warm-up required. Waiting {self.WARMUP_DELAY}s...")
            GLib.timeout_add_seconds(self.WARMUP_DELAY, self.start_geoclue_client)
        else:
            self.start_geoclue_client()

    def check_and_enable_location(self):
        if not is_location_enabled():
            logger.info("[Location] System location is OFF. Enabling...")
            return enable_location()
        return False

    def start_geoclue_client(self):
        if not self.is_fetching:
            return False

        logger.info("[Location] Starting Geoclue Simple Client...")

        gi.require_version("Geoclue", "2.0")
        from gi.repository import Geoclue

        try:
            Geoclue.Simple.new(
                APP_ID,
                Geoclue.AccuracyLevel.EXACT,
                None,
                self.on_simple_ready
            )

            self.timeout_source = GLib.timeout_add_seconds(
                self._request_timeout,
                self.on_timeout
            )

        except Exception as e:
            logger.error(f"[Location] Failed to start Geoclue client: {e}")
            self.schedule_finish(None, None, None)

        return False

    def on_simple_ready(self, source, result):
        if not self.is_fetching:
            return

        gi.require_version("Geoclue", "2.0")
        from gi.repository import Geoclue

        try:
            simple = Geoclue.Simple.new_finish(result)
        except Exception as e:
            logger.error(f"[Location] Geoclue creation failed (likely race condition): {e}")
            self.schedule_finish(None, None, None)
            return

        self.simple_client = simple
        logger.info("[Location] Geoclue client ready.")

        client = self.simple_client.get_client()
        if not client:
            logger.warning("[Location] Geoclue returned no client, finishing without a fix.")
            self.schedule_finish(None, None, None)
            return

        logger.debug(f"[Location] Client Active: {client.props.active}")

        if not client.props.active and self.progress_callback:
            self.progress_callback(_("Waiting for location service..."))

        location = self.simple_client.get_location()
        is_good_enough = False
        if location and (location.props.latitude != 0 or location.props.longitude != 0):
            logger.info("[Location] Immediate location available.")
            is_good_enough = self.process_location(location)

        if is_good_enough:
            return

        self.location_signal_id = self.simple_client.connect(
            "notify::location",
            self.on_location_updated
        )

        if self.progress_callback:
            self.progress_callback(_("Waiting for satellite fix..."))

    def on_location_updated(self, client, param):
        if not self.is_fetching:
            return

        location = client.get_location()
        logger.info("[Location] Location updated signal received.")
        self.process_location(location)

    def process_location(self, location):
        try:
            lat = location.props.latitude
            lon = location.props.longitude
            accuracy = location.props.accuracy

            logger.info(f"[Location] Fix obtained: {lat}, {lon} (acc: {accuracy}m)")

            if self.best_location is None or accuracy < self.best_location.props.accuracy:
                self.best_location = location

            if accuracy <= self.accuracy_threshold:
                self.schedule_finish(lat, lon, accuracy)
                return True

            return False

        except Exception as e:
            logger.error(f"[Location] Failed to read location properties: {e}")
            return False

    def on_timeout(self):
        self.timeout_source = None

        logger.warning("[Location] Timeout waiting for location.")

        if self.best_location:
            try:
                lat = self.best_location.props.latitude
                lon = self.best_location.props.longitude
                accuracy = self.best_location.props.accuracy
                logger.info(f"[Location] Returning best location found: acc={accuracy}m")
                self.schedule_finish(lat, lon, accuracy)
            except Exception as e:
                logger.error(f"[Location] Error processing best location: {e}")
                self.schedule_finish(None, None, None)
        else:
            self.schedule_finish(None, None, None)

        return False

    def schedule_finish(self, lat, lon, acc):
        GLib.idle_add(self.dispatch_result, lat, lon, acc)

    def dispatch_result(self, lat, lon, acc):
        if self.active_callback:
            try:
                self.active_callback(lat, lon, acc)
            except Exception as e:
                logger.error(f"[Location] User callback raised exception: {e}")

        self.cleanup()
        return False

    def cleanup(self):
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

            def destroy_client():
                nonlocal client
                client = None
                return False

            GLib.timeout_add(2000, destroy_client)
