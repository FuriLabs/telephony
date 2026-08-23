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

from telephony.shared.utils.datetime_utils import parse_timestamp

from datetime import datetime
from gi.repository import GObject
from telephony.shared.utils.log_utils import logger
from telephony.client.utils.locale_utils import get_date_format, get_time_format

from gettext import gettext as _


INCOMING_KINDS = ("incoming", "missed", "rejected")
OUTGOING_KINDS = ("outgoing", "cancelled")


def call_direction_text(kind):
    """Say which way the call went.

    One column carries both the direction and how the call turned out,
    so a call that was never answered reads as cancelled or missed
    rather than as the direction it was.
    """
    if kind in INCOMING_KINDS:
        return _("Incoming")
    if kind in OUTGOING_KINDS:
        return _("Outgoing")
    return _("Unknown")


def call_outcome_text(kind, reason=None):
    """Say how the call turned out, which is the rest of that column.

    An outgoing call that never connected is only given up on when the
    giving up happened here. The same row is written when the other end
    declined it or the network could not put it through, and the reason
    is the only thing telling those apart, which is what the marks on
    the history row already go by.
    """
    if kind == "cancelled":
        if reason == "remote":
            return _("Declined at the other end")
        if reason == "network":
            return _("The network could not connect it")
        return _("Given up before it connected")

    outcomes = {
        "incoming": _("Answered"),
        "outgoing": _("Answered"),
        "missed": _("Not answered"),
        "rejected": _("Declined here"),
    }
    return outcomes.get(kind, _("Unknown"))


def call_ending_text(reason, kind=None):
    """Say how a call ended, from the modem or from what is known here.

    ofono reports one of local, remote or network, and says itself that
    not every modem provides it. A call that was never answered says so
    from what the history already knows, and a call that was answered
    without a reason says plainly that nothing was reported, rather
    than leaving the row out and looking like an oversight.

    Remote is not the same as the person hanging up. The binder plugin
    answers remote for a dozen network causes as well, no route, out of
    order, preemption and barring among them, so this says the call
    ended at the other end and leaves who did it alone.
    """
    endings = {
        "local": _("Ended on this phone"),
        "remote": _("Ended at the other end"),
        "network": _("Dropped by the network"),
    }
    if reason in endings:
        return endings[reason]

    unanswered = {
        "missed": _("Nobody answered here"),
        "rejected": _("Declined on this phone"),
        "cancelled": _("Hung up here before it connected"),
    }
    if kind in unanswered:
        return unanswered[kind]

    return _("The modem did not say")


class CallItem(GObject.Object):
    """
    Model representing a single call history item.
    """

    def __init__(self, call_id, number, name, direction, duration, full_ts, display_time, is_saved, is_divider=False, label=None, anonymous=False, multiparty=False, transferred=False, disconnect_reason=None):
        """
        Initialize the call item.
        """
        super().__init__()
        self.id = call_id
        self.number = number
        self.name = name
        self.direction = direction
        self.duration = duration
        self.full_ts = full_ts
        self.display_time = display_time
        self.duration_str = self.fmt_dur(duration)
        self.is_saved = is_saved
        self.is_divider = is_divider
        self.label = label
        self.anonymous = anonymous
        self.multiparty = multiparty
        self.transferred = transferred
        self.disconnect_reason = disconnect_reason

    def fmt_dur(self, seconds):
        """
        Format duration in seconds to a human-readable string.
        """
        try:
            s = int(seconds)
        except (ValueError, TypeError):
            logger.debug(f"[Model] Invalid duration: {seconds}")
            return _("0s")

        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return _("{h}h {m}m").format(h=h, m=m)
        if m > 0:
            return _("{m}m {s}s").format(m=m, s=s)
        return _("{s}s").format(s=s)


class ContactItem(GObject.Object):
    """
    Model representing a contact.
    """

    def __init__(self, uid, first_name, last_name, phone, email, is_favorite=False, source_uid=None):
        """
        Initialize the contact item.
        """
        super().__init__()
        self.uid = uid
        self.first_name = first_name or ""
        self.last_name = last_name or ""
        self.phone = phone or ""
        self.email = email or ""
        self.is_favorite = is_favorite
        self.source_uid = source_uid

        if self.first_name and self.last_name:
            self.full_name = f"{self.first_name} {self.last_name}"
        elif self.first_name:
            self.full_name = self.first_name
        elif self.last_name:
            self.full_name = self.last_name
        else:
            self.full_name = "Unknown"


class ConversationItem(GObject.Object):
    """
    Model representing a conversation thread.
    """

    def __init__(self, number, name, last_msg, timestamp, unread_count, status=None, is_load_more=False):
        """
        Initialize the conversation item.
        """
        super().__init__()
        self.number = number
        self.name = name
        self.last_msg = last_msg
        self.timestamp = timestamp
        self.unread_count = unread_count
        self.status = status
        self.is_load_more = is_load_more

        try:
            if timestamp:
                dt = parse_timestamp(timestamp)
                now = datetime.now()
                if dt.date() == now.date():
                    self.display_time = dt.strftime(get_time_format())
                else:
                    self.display_time = dt.strftime(get_date_format())
            else:
                self.display_time = ""
        except Exception as e:
            logger.debug(f"[Model] Conversation timestamp error: {e}")
            self.display_time = ""


class MessageItem(GObject.Object):
    """
    Model representing a single message.
    """

    def __init__(self, msg_id, direction, body, timestamp, status, is_divider=False, subject=None, attachments=None, sender=None, scheduled_timestamp=None):
        super().__init__()
        self.id = msg_id
        self.direction = direction
        self.body = body or ""
        self.timestamp = timestamp
        self.status = status
        self.is_divider = is_divider

        self.sender = sender if sender else "Unknown"

        self.subject = subject
        self.attachments = attachments or []
        self.scheduled_timestamp = scheduled_timestamp
        self.cached_markup = None
        self._display_time = None

    @property
    def display_time(self):
        if self._display_time is not None:
            return self._display_time
        try:
            if not self.is_divider and self.timestamp:
                dt = parse_timestamp(self.timestamp)
                self._display_time = dt.strftime(f"{get_time_format()} {get_date_format()}")
            else:
                self._display_time = ""
        except Exception as e:
            logger.debug(f"[Model] Message timestamp error: {e}")
            self._display_time = ""
        return self._display_time
