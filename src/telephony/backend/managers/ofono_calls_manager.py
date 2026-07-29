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
import time

from gi.repository import GLib, Gio
from loguru import logger

from gettext import gettext as _
from ..utils.phone_utils import normalize_number
from ..utils.thread_utils import run_in_background

DIAL_COOLDOWN_SECONDS = 2.0


class OfonoCallsManager:
    def _sync_existing_calls(self):
        """Sync existing calls from the modem."""
        if not self.voice_proxy:
            return
        try:
            ret = self.voice_proxy.call_sync("GetCalls", None, Gio.DBusCallFlags.NONE, -1, None)
            calls = ret.unpack()[0]
            for path, props in calls:
                self._add_call(path, props)
        except Exception as e:
            logger.error(f"Sync calls error: {e}")

    def _dial_cooldown_remaining(self):
        """Return the seconds left before the modem accepts a new dial."""
        remaining = DIAL_COOLDOWN_SECONDS - (time.time() - self._last_call_end)
        return remaining if remaining > 0 else 0

    def _deferred_dial(self, number, hide_id):
        """Retry a dial in the background once the modem cooldown has passed."""
        run_in_background(self.dial, number, hide_id=hide_id)
        return False

    def dial(self, number, hide_id=False):
        """Initiate an outgoing call."""
        if not self.voice_proxy:
            self.emit('action-error', _("Modem not ready"))
            return False

        remaining = self._dial_cooldown_remaining()
        if remaining > 0:
            logger.info(f"[OfonoManager] Waiting {remaining:.1f}s after the previous call before dialing")
            if threading.current_thread() is threading.main_thread():
                GLib.timeout_add(int(remaining * 1000) + 100, self._deferred_dial, number, hide_id)
                return True
            time.sleep(remaining)

        if len(self.active_calls) > 0:
            try:
                ret = self.voice_proxy.call_sync("GetCalls", None, Gio.DBusCallFlags.NONE, -1, None)
                real_calls = ret.unpack()[0]
                if len(real_calls) == 0:
                    for path in list(self.active_calls.keys()):
                        self._force_remove(path)
            except Exception as e:
                logger.error(f"[OfonoManager] Sanity check failed: {e}")

        if len(self.active_calls) > 0:
            self.emit('action-error', _("Cannot dial while in another call"))
            return False

        try:
            clean_num = normalize_number(number)

            self.emit('notification-cleared', clean_num)

            clir = "enabled" if hide_id else "default"
            self.voice_proxy.call_sync("Dial", GLib.Variant("(ss)", (clean_num, clir)), Gio.DBusCallFlags.NONE, -1, None)
            return True
        except Exception as e:
            self.emit('action-error', _("Dial Error: {e}").format(e=e))
            return False

    def answer_call(self, target_path):
        """Answer an incoming call."""
        other_dialing = None
        other_active = False

        for path, data in self.active_calls.items():
            if path == target_path:
                continue
            state = data.get('state')
            if state in ['dialing', 'alerting']:
                other_dialing = path
            if state == 'active':
                other_active = True

        if other_dialing:
            self.hangup_call(other_dialing)
            GLib.timeout_add(500, lambda: self._execute_answer(target_path))
            return

        if other_active:
            self.swap_calls()
            return

        self._execute_answer(target_path)

    def _execute_answer(self, path):
        """Internal helper to answer a call."""
        try:
            if path in self.active_calls:
                proxy = self.active_calls[path].get('proxy')
                if proxy:
                    proxy.call_sync("Answer", None, Gio.DBusCallFlags.NONE, -1, None)
                return

            call = self._get_proxy("org.ofono.VoiceCall", path)
            if call:
                call.call_sync("Answer", None, Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.debug(f"[OfonoManager] Answer failed for {path}: {e}")
            err_str = str(e)
            if any(x in err_str for x in ["UnknownObject", "Operation failed", "InProgress", "Failed"]):
                self._force_remove(path)

    def hangup_call(self, path):
        """Hangup a specific call."""
        try:
            if path in self.active_calls:
                proxy = self.active_calls[path].get('proxy')
                if proxy:
                    proxy.call_sync("Hangup", None, Gio.DBusCallFlags.NONE, -1, None)
                    return

            call = self._get_proxy("org.ofono.VoiceCall", path)
            if call:
                call.call_sync("Hangup", None, Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.debug(f"[OfonoManager] Hangup failed for {path}: {e}")
            err_str = str(e)
            if any(x in err_str for x in ["UnknownObject", "Operation failed", "InProgress", "Failed"]):
                self._force_remove(path)

    def hangup_all(self):
        """Hangup all active calls."""
        if self.voice_proxy:
            try:
                self.voice_proxy.call_sync("HangupAll", None, Gio.DBusCallFlags.NONE, -1, None)
            except Exception as e:
                logger.debug(f"[OfonoManager] HangupAll failed, falling back to per-call hangup: {e}")
                for path in list(self.active_calls.keys()):
                    self.hangup_call(path)

    def swap_calls(self):
        """Swap active and held calls."""
        if not self.voice_proxy:
            return
        try:
            self.voice_proxy.call_sync("SwapCalls", None, Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.error(f"SwapCalls failed: {e}")

    def _force_remove(self, path):
        """Forcefully remove a call from the active list."""
        if path in self.active_calls:
            self._remove_call(path)

    def send_dtmf(self, tones):
        """Send DTMF tones during a call."""
        if not self.voice_proxy:
            return
        try:
            self.voice_proxy.call_sync("SendTones", GLib.Variant("(s)", (tones,)), Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Send DTMF failed: {e}")

    def mute(self, muted=True):
        """Mute or unmute the modem volume."""
        if not self.vol_proxy:
            return
        try:
            self.vol_proxy.call_sync("SetProperty", GLib.Variant("(sv)", ("Muted", GLib.Variant("b", muted))), Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logger.error(f"[OfonoManager] Mute failed: {e}")
