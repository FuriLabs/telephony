# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import os
import subprocess
from telephony.shared.utils.log_utils import logger

IOS_SMS_DB_HASH = "3d0d7e5fb2ce288813306e4d4636395e047a3d28"
IOS_CALLS_DB_HASH = "5a4935c78a5255723f707230a451d79c60df7ea8"


class IOSBackupExtractor:

    @staticmethod
    def check_connection():
        try:
            result = subprocess.run(["ideviceinfo"], capture_output=True, text=True)
            if result.returncode == 0:
                return True, True, ""

            err = result.stderr.lower()
            if "lockdownd" in err or "code -19" in err or "code -21" in err:
                return True, False, "Device is connected but not trusted. Please unlock your iPhone and tap 'Trust'."

            return False, False, "No iOS device detected. Ensure it is plugged in."
        except Exception as e:
            return False, False, str(e)

    @staticmethod
    def run_backup(dest_dir):
        try:
            logger.info(f"[IOS Extractor] Starting backup to {dest_dir}...")
            result = subprocess.run(
                ["idevicebackup2", "backup", dest_dir],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                logger.info("[IOS Extractor] Backup completed successfully.")
                return True, ""
            else:
                logger.error(f"[IOS Extractor] Backup failed: {result.stderr}")
                return False, result.stderr
        except Exception as e:
            logger.error(f"[IOS Extractor] Exception during backup: {e}")
            return False, str(e)

    @staticmethod
    def find_databases(backup_dir):
        sms_path = None
        calls_path = None
        manifest_path = None

        for root, _, files in os.walk(backup_dir):
            if "Manifest.db" in files:
                manifest_path = os.path.join(root, "Manifest.db")

            if IOS_SMS_DB_HASH in files:
                sms_path = os.path.join(root, IOS_SMS_DB_HASH)
            if IOS_CALLS_DB_HASH in files:
                calls_path = os.path.join(root, IOS_CALLS_DB_HASH)

            possible_sms = os.path.join(root, IOS_SMS_DB_HASH[:2], IOS_SMS_DB_HASH)
            if os.path.exists(possible_sms):
                sms_path = possible_sms

            possible_calls = os.path.join(root, IOS_CALLS_DB_HASH[:2], IOS_CALLS_DB_HASH)
            if os.path.exists(possible_calls):
                calls_path = possible_calls

        return sms_path, calls_path, manifest_path
