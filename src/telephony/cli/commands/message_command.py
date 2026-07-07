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

"""CLI commands for Message."""

import json
from gi.repository import GLib, Gio
from telephony.cli.cli_utils import get_proxy


def cmd_sms(args):
    """Execute the sms command."""
    proxy = get_proxy()
    res = proxy.call_sync("SendSms", GLib.Variant("(ss)", (args.number, args.text)), Gio.DBusCallFlags.NONE, -1, None)
    success = res.unpack()[0]
    if success:
        print(f"SMS sent to {args.number}.")
    else:
        print(f"Failed to send SMS to {args.number}.")


def cmd_mms(args):
    """Execute the mms command."""
    proxy = get_proxy()
    atts = json.dumps(args.attachments) if args.attachments else "[]"
    res = proxy.call_sync("SendMms", GLib.Variant("(sss)", (args.number, args.text, atts)), Gio.DBusCallFlags.NONE, -1, None)
    success = res.unpack()[0]
    if success:
        print(f"MMS sent to {args.number}.")
    else:
        print(f"Failed to send MMS to {args.number}.")


def cmd_schedule_sms(args):
    """Execute the schedule sms command."""
    proxy = get_proxy()
    res = proxy.call_sync("ScheduleSms", GLib.Variant("(sss)", (args.number, args.text, args.scheduled_timestamp)), Gio.DBusCallFlags.NONE, -1, None)
    success = res.unpack()[0]
    if success:
        print(f"SMS scheduled to {args.number} at {args.scheduled_timestamp}.")
    else:
        print(f"Failed to schedule SMS to {args.number}.")


def cmd_schedule_mms(args):
    """Execute the schedule mms command."""
    proxy = get_proxy()
    atts = json.dumps(args.attachments) if args.attachments else "[]"
    res = proxy.call_sync("ScheduleMms", GLib.Variant("(ssss)", (args.number, args.text, atts, args.scheduled_timestamp)), Gio.DBusCallFlags.NONE, -1, None)
    success = res.unpack()[0]
    if success:
        print(f"MMS scheduled to {args.number} at {args.scheduled_timestamp}.")
    else:
        print(f"Failed to schedule MMS to {args.number}.")


def cmd_set_group_name(args):
    """Execute the set group name command."""
    proxy = get_proxy()
    res = proxy.call_sync("SetGroupName", GLib.Variant("(ss)", (args.recipients, args.new_name)), Gio.DBusCallFlags.NONE, -1, None)
    success = res.unpack()[0]
    if success:
        print(f"Group name updated to {args.new_name}.")
    else:
        print("Failed to update group name.")


def cmd_conversations(args):
    """Execute the conversations command."""
    proxy = get_proxy()
    res = proxy.call_sync("GetConversations", GLib.Variant("(i)", (args.limit,)), Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for c in data:
        print(f"Num: {c.get('number')} | Unread: {c.get('unread_count')} | Last: {c.get('body')} | Time: {c.get('timestamp')}")
    print("(Hint: use '--limit N' to see more results, default is 10)")


def cmd_messages(args):
    """Execute the messages command."""
    proxy = get_proxy()
    res = proxy.call_sync("GetMessages", GLib.Variant("(si)", (args.number, args.limit)), Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for m in data:
        dir_str = "->" if m.get('direction') == 'outgoing' else "<-"
        print(f"[{m.get('timestamp')}] {dir_str} {m.get('body')} ({m.get('status')})")
    print("(Hint: use '--limit N' to see more results, default is 10)")


def cmd_message_del(args):
    """Execute the message del command."""
    proxy = get_proxy()
    proxy.call_sync("DeleteMessage", GLib.Variant("(i)", (args.msg_id,)), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Deleted message {args.msg_id}.")


def cmd_conversation_del(args):
    """Execute the conversation del command."""
    proxy = get_proxy()
    proxy.call_sync("DeleteConversation", GLib.Variant("(s)", (args.number,)), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Deleted conversation {args.number}.")


def cmd_mark_read(args):
    """Execute the mark read command."""
    proxy = get_proxy()
    proxy.call_sync("MarkThreadAsRead", GLib.Variant("(s)", (args.number,)), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Marked conversation {args.number} as read.")


def cmd_missed_list(args):
    """Execute the missed list command."""
    proxy = get_proxy()
    res = proxy.call_sync("GetMissedMessages", None, Gio.DBusCallFlags.NONE, -1, None)
    data = json.loads(res.unpack()[0])
    for m in data:
        print(f"ID: {m.get('id')} | Number: {m.get('number')} | Time: {m.get('timestamp')} | Body: {m.get('body')}")


def cmd_missed_send(args):
    """Execute the missed send command."""
    proxy = get_proxy()
    proxy.call_sync("SendMissedMessage", GLib.Variant("(i)", (args.msg_id,)), Gio.DBusCallFlags.NONE, -1, None)
    print(f"Triggered sending for missed message {args.msg_id}.")


def setup_parsers(subparsers):
    """Setup argument parsers for message commands."""
    p_sms = subparsers.add_parser("sms", help="Send an SMS")
    p_sms.add_argument("number", type=str, help="Recipient number")
    p_sms.add_argument("text", type=str, help="Message content")
    p_sms.set_defaults(func=cmd_sms)

    p_mms = subparsers.add_parser("mms", help="Send an MMS")
    p_mms.add_argument("number", type=str, help="Recipient number(s), comma separated")
    p_mms.add_argument("text", type=str, help="Message content", nargs="?", default="")
    p_mms.add_argument("--attachments", type=str, nargs="+", help="Paths to file attachments")
    p_mms.set_defaults(func=cmd_mms)

    p_sched_sms = subparsers.add_parser("schedule-sms", help="Schedule an SMS")
    p_sched_sms.add_argument("number", type=str, help="Recipient number")
    p_sched_sms.add_argument("text", type=str, help="Message content")
    p_sched_sms.add_argument("scheduled_timestamp", type=str, help="Scheduled time (e.g. 'YYYY-MM-DD HH:MM')")
    p_sched_sms.set_defaults(func=cmd_schedule_sms)

    p_sched_mms = subparsers.add_parser("schedule-mms", help="Schedule an MMS")
    p_sched_mms.add_argument("number", type=str, help="Recipient number(s), comma separated")
    p_sched_mms.add_argument("text", type=str, help="Message content", nargs="?", default="")
    p_sched_mms.add_argument("scheduled_timestamp", type=str, help="Scheduled time (e.g. 'YYYY-MM-DD HH:MM')")
    p_sched_mms.add_argument("--attachments", type=str, nargs="+", help="Paths to file attachments")
    p_sched_mms.set_defaults(func=cmd_schedule_mms)

    p_sgn = subparsers.add_parser("set-group-name", help="Set custom name for a group chat")
    p_sgn.add_argument("recipients", type=str, help="Comma separated list of recipients or JSON array")
    p_sgn.add_argument("new_name", type=str, help="New group name")
    p_sgn.set_defaults(func=cmd_set_group_name)

    p_conv = subparsers.add_parser("conversations", help="List SMS conversations")
    p_conv.add_argument("--limit", type=int, default=10, help="Limit number of results")
    p_conv.set_defaults(func=cmd_conversations)

    p_msg = subparsers.add_parser("messages", help="List messages for a chat")
    p_msg.add_argument("number", type=str, help="Chat number")
    p_msg.add_argument("--limit", type=int, default=10, help="Limit number of results")
    p_msg.set_defaults(func=cmd_messages)

    p_mdel = subparsers.add_parser("message-del", help="Delete a specific message")
    p_mdel.add_argument("msg_id", type=int)
    p_mdel.set_defaults(func=cmd_message_del)

    p_cdel = subparsers.add_parser("conversation-del", help="Delete a conversation thread")
    p_cdel.add_argument("number", type=str)
    p_cdel.set_defaults(func=cmd_conversation_del)

    p_markread = subparsers.add_parser("mark-read", help="Mark a conversation as read")
    p_markread.add_argument("number", type=str)
    p_markread.set_defaults(func=cmd_mark_read)

    p_mlist = subparsers.add_parser("missed-messages", help="List scheduled messages that were missed")
    p_mlist.set_defaults(func=cmd_missed_list)

    p_msend = subparsers.add_parser("missed-send", help="Send a missed scheduled message")
    p_msend.add_argument("msg_id", type=int)
    p_msend.set_defaults(func=cmd_missed_send)
