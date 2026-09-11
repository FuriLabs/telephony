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

import json
from datetime import datetime, timedelta
from telephony.shared.utils.log_utils import logger

from telephony.shared.utils.thread_utils import run_in_background
from gi.repository import GLib

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MISSED_OVERDUE_MINUTES = 5


class ScheduleManager:
    """
    Manages scheduling of messages using smart GLib timeouts.
    Instead of polling, it calculates the sleep time until the next event.
    """

    def __init__(self, db_manager, ofono_manager, mms_manager):
        self.db = db_manager
        self.ofono = ofono_manager
        self.mms = mms_manager
        self._timer_id = None
        self._scan_running = False
        self._rescan_queued = False

    def start(self):
        """Start the scheduler logic."""
        logger.info("[ScheduleManager] Starting scheduler...")
        self.schedule_next_run()

    def schedule_next_run(self):
        """Scan and send due messages off the main thread, then arm the timer."""
        if self._scan_running:
            self._rescan_queued = True
            return

        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

        self._scan_running = True

        def scan():
            self.check_and_send_pending()
            return self.db.get_next_scheduled_timestamp()

        run_in_background(scan, on_complete=self.arm_next_timer, on_error=self.on_scan_error)

    def on_scan_error(self, error):
        """Re-arm the scheduler after a failed scan."""
        self._scan_running = False
        logger.error(f"[ScheduleManager] Scheduled send scan failed, retrying in 60s: {error}")
        self._timer_id = GLib.timeout_add_seconds(60, self.on_timeout_run)

    def arm_next_timer(self, next_ts_str):
        """Arm the wakeup timer for the next scheduled message; main thread."""
        self._scan_running = False
        if self._rescan_queued:
            self._rescan_queued = False
            self.schedule_next_run()
            return

        if not next_ts_str:
            logger.debug("[ScheduleManager] No future messages found. Scheduler idle.")
            return

        try:
            next_dt = datetime.strptime(next_ts_str, DATE_FORMAT)
            now = datetime.now()

            seconds_until = (next_dt - now).total_seconds()

            if seconds_until <= 0:
                cutoff_time = now - timedelta(minutes=MISSED_OVERDUE_MINUTES)
                if next_dt < cutoff_time:
                    logger.debug(f"[ScheduleManager] Next message is old/missed ({next_ts_str}). Waiting 60s.")
                    seconds_until = 60
                else:
                    seconds_until = 0.5

            if seconds_until < 0.5:
                seconds_until = 0.5

            logger.info(f"[ScheduleManager] Next message at {next_ts_str}. Sleeping for {seconds_until:.2f}s")
            self._timer_id = GLib.timeout_add(int(seconds_until * 1000), self.on_timeout_run)

        except Exception as e:
            logger.error(f"[ScheduleManager] Date parse error for {next_ts_str}, retrying in 60s: {e}")
            self._timer_id = GLib.timeout_add_seconds(60, self.on_timeout_run)

    def on_timeout_run(self):
        """Callback for the GLib timer."""
        self._timer_id = None
        self.schedule_next_run()
        return False

    def check_and_send_pending(self):
        """Send the messages that came due while nothing was listening.

        A phone that was off, or a modem that was not ready yet, leaves
        a message sitting past its time. It is still sent when the gap
        is small; past that the reader is asked first, because a message
        arriving long after it was meant to is a surprise, not a
        delivery. The two sides use the same threshold, so nothing falls
        between being sent and being offered.
        """
        count = 0
        if not self.ofono or not self.ofono.msg_proxy:
            logger.info("[ScheduleManager] Modem not ready, skipping send.")
            return 0

        now = datetime.now()
        start_time = now - timedelta(minutes=MISSED_OVERDUE_MINUTES)

        now_str = now.strftime(DATE_FORMAT)
        start_str = start_time.strftime(DATE_FORMAT)

        due_messages = self.db.get_recent_scheduled_messages(start_str, now_str)

        if due_messages:
            logger.info(f"[ScheduleManager] Found {len(due_messages)} due messages.")
            for msg in due_messages:
                self.process_message(msg)
                count += 1

        return count

    def process_message(self, msg):
        """Send a single message."""
        mid, number, body, subject, attachments_json, scheduled_ts = msg
        logger.info(f"[ScheduleManager] Sending scheduled message {mid} to {number}")

        attachments = []
        if attachments_json:
            try:
                attachments = json.loads(attachments_json)
            except Exception as e:
                logger.warning(f"[ScheduleManager] Failed to parse attachments JSON: {e}")

        self.db.update_message_schedule(mid, status="sending")

        is_group = "," in number
        if is_group or attachments or subject:
            if self.mms:
                targets = [n.strip() for n in number.split(",")] if is_group else [number]
                self.mms.send_mms_tracked(targets, body, attachments, mid)
            else:
                logger.warning("[ScheduleManager] MMS manager not available")
                self.db.update_message_status(mid, "failed")
        else:
            if self.ofono:
                self.ofono.send_sms_tracked(number, body, mid)
            else:
                logger.warning("[ScheduleManager] Ofono manager not available")
                self.db.update_message_status(mid, "failed")

    def add_cron(self, message_id, timestamp_str):
        """
        Triggered when a new message is scheduled.
        Recalculates the schedule immediately.
        """
        logger.info(f"[ScheduleManager] Message {message_id} added. Rescheduling.")
        self.schedule_next_run()

    def remove_cron(self, message_id):
        """
        Triggered when a scheduled message is removed/sent.
        Recalculates the schedule immediately.
        """
        logger.info(f"[ScheduleManager] Message {message_id} removed. Rescheduling.")
        self.schedule_next_run()

    def get_missed_messages(self, buffer_minutes=MISSED_OVERDUE_MINUTES):
        """Return the scheduled messages that are past due.

        The window asks this to offer them, and the send path asks it
        again to honour the offer, so both must mean the same thing by
        overdue or the button answers for a message that was never in
        the list.
        """
        now = datetime.now()
        cutoff_time = now - timedelta(minutes=buffer_minutes)
        cutoff_str = cutoff_time.strftime(DATE_FORMAT)
        return self.db.get_missed_scheduled_messages(cutoff_str)
