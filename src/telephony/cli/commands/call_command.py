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

"""CLI commands for Call."""

import json
from gi.repository import GLib, Gio
from telephony.cli.cli_utils import get_proxy

DIAL_REPLY_TIMEOUT_MS = 30000


def cmd_dial(args):
    """Execute the dial command."""
    proxy = get_proxy()
    res = proxy.call_sync("Dial", GLib.Variant("(sb)", (args.number, False)), Gio.DBusCallFlags.NONE, DIAL_REPLY_TIMEOUT_MS, None)
    success, message = res.unpack()
    if success:
        print(f"Dialing {args.number}...")
    else:
        print(f"Failed to dial {args.number}: {message}")


def cmd_answer(args):
    """Execute the answer command."""
    proxy = get_proxy()
    proxy.call_sync("Answer", GLib.Variant("(s)", (args.path or "",)), Gio.DBusCallFlags.NONE, -1, None)
    print("Answered call.")


def cmd_hangup(args):
    """Execute the hangup command."""
    proxy = get_proxy()
    proxy.call_sync("Hangup", GLib.Variant("(s)", (args.path or "",)), Gio.DBusCallFlags.NONE, -1, None)
    print("Hung up call.")


def cmd_hangup_all(args):
    """Execute the hangup all command."""
    proxy = get_proxy()
    proxy.call_sync("HangupAll", None, Gio.DBusCallFlags.NONE, -1, None)
    print("Hung up all calls.")


def cmd_swap(args):
    """Execute the swap command."""
    proxy = get_proxy()
    proxy.call_sync("SwapCalls", None, Gio.DBusCallFlags.NONE, -1, None)
    print("Swapped calls.")


def cmd_mute(args):
    """Execute the mute command."""
    proxy = get_proxy()
    proxy.call_sync("MuteMic", None, Gio.DBusCallFlags.NONE, -1, None)
    print("Muted microphone.")


def cmd_unmute(args):
    """Execute the unmute command."""
    proxy = get_proxy()
    proxy.call_sync("UnmuteMic", None, Gio.DBusCallFlags.NONE, -1, None)
    print("Unmuted microphone.")


def cmd_speaker(args):
    """Execute the speaker command."""
    proxy = get_proxy()
    enable = str(args.enable).lower() in ['true', '1', 'yes']
    proxy.call_sync("SetSpeakerphone", GLib.Variant("(b)", (enable,)), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Set speakerphone to {enable}.")


def cmd_dtmf(args):
    """Execute the dtmf command."""
    proxy = get_proxy()
    proxy.call_sync("SendDtmf", GLib.Variant("(s)", (args.tones,)), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Sent DTMF tones: {args.tones}")


def cmd_decline_sms(args):
    """Execute the decline sms command."""
    proxy = get_proxy()
    proxy.call_sync("DeclineWithSms", GLib.Variant("(ss)", (args.path or "", args.text)), Gio.DBusCallFlags.NONE, -1, None)
    print("Declined call with SMS.")


def cmd_history(args):
    """Execute the history command."""
    proxy = get_proxy()
    res = proxy.call_sync("GetCallHistory", GLib.Variant("(i)", (args.limit,)), Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for c in data:
        print(f"ID: {c.get('id')} | Num: {c.get('number')} | Name: {c.get('name')} | Dir: {c.get('direction')} | Dur: {c.get('duration')}s | Time: {c.get('timestamp')}")
    print("(Hint: use '--limit N' to see more results, default is 10)")


def cmd_history_clear(args):
    """Execute the history clear command."""
    proxy = get_proxy()
    proxy.call_sync("ClearCallHistory", None, Gio.DBusCallFlags.NONE, -1, None)
    print("Cleared call history.")


def cmd_active(args):
    """Execute the active command."""
    proxy = get_proxy()
    res = proxy.call_sync("GetActiveCalls", None, Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for c in data:
        print(f"Path: {c.get('path')} | Num: {c.get('number')} | State: {c.get('state')} | Dir: {c.get('direction')}")


def setup_parsers(subparsers):
    """Setup argument parsers for call commands."""
    p_dial = subparsers.add_parser("dial", help="Dial a number")
    p_dial.add_argument("number", type=str, help="Phone number to dial")
    p_dial.set_defaults(func=cmd_dial)

    p_answer = subparsers.add_parser("answer", help="Answer incoming call")
    p_answer.add_argument("--path", type=str, help="Specific call path", default="")
    p_answer.set_defaults(func=cmd_answer)

    p_hangup = subparsers.add_parser("hangup", help="Hangup call")
    p_hangup.add_argument("--path", type=str, help="Specific call path", default="")
    p_hangup.set_defaults(func=cmd_hangup)

    p_hangup_all = subparsers.add_parser("hangup-all", help="Hangup all calls")
    p_hangup_all.set_defaults(func=cmd_hangup_all)

    p_swap = subparsers.add_parser("swap", help="Swap active and held calls")
    p_swap.set_defaults(func=cmd_swap)

    p_mute = subparsers.add_parser("mute", help="Mute microphone")
    p_mute.set_defaults(func=cmd_mute)

    p_unmute = subparsers.add_parser("unmute", help="Unmute microphone")
    p_unmute.set_defaults(func=cmd_unmute)

    p_speaker = subparsers.add_parser("speaker", help="Toggle speakerphone")
    p_speaker.add_argument("enable", type=str, help="True/False or 1/0")
    p_speaker.set_defaults(func=cmd_speaker)

    p_dtmf = subparsers.add_parser("dtmf", help="Send DTMF tones")
    p_dtmf.add_argument("tones", type=str)
    p_dtmf.set_defaults(func=cmd_dtmf)

    p_decline_sms = subparsers.add_parser("decline-sms", help="Decline a call with an SMS")
    p_decline_sms.add_argument("text", type=str)
    p_decline_sms.add_argument("--path", type=str, default="")
    p_decline_sms.set_defaults(func=cmd_decline_sms)

    p_history = subparsers.add_parser("history", help="Get call history")
    p_history.add_argument("--limit", type=int, default=10, help="Limit number of results")
    p_history.set_defaults(func=cmd_history)

    p_hclear = subparsers.add_parser("history-clear", help="Clear call history")
    p_hclear.set_defaults(func=cmd_history_clear)

    p_active = subparsers.add_parser("active", help="List active calls")
    p_active.set_defaults(func=cmd_active)
