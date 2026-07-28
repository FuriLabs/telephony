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

from .datetime_utils import format_timestamp
import json

from loguru import logger

from .phone_utils import normalize_number
from gi.repository import GLib


class DbMessagesUtils:
    def _normalize_chat_id(self, number_or_list):
        """Normalize a recipient or recipient list to the stored conversation id."""
        if isinstance(number_or_list, list):
            cleaned = [normalize_number(n, permissive=True) for n in number_or_list]
            return ",".join(sorted(cleaned))
        if "," in str(number_or_list):
            return number_or_list
        return normalize_number(number_or_list, permissive=True)

    def conversation_exists(self, number_or_list):
        """Check whether a conversation already exists for the given recipients."""
        try:
            chat_id = self._normalize_chat_id(number_or_list)
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT 1 FROM messages WHERE remote_number=? AND status != 'draft' LIMIT 1", (chat_id,))
                return c.fetchone() is not None
        except Exception as e:
            logger.error(f"[DB] Conversation exists check error: {e}")
            return False

    def get_conversation_ids(self):
        """Return all distinct conversation ids."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT DISTINCT remote_number FROM messages WHERE status != 'draft'")
                return [r[0] for r in c.fetchall() if r[0]]
        except Exception as e:
            logger.error(f"[DB] Get Conversation Ids Error: {e}")
            return []

    def add_message(self, remote_number, direction, body, status="unread", subject=None, attachments=[], sender=None, scheduled_timestamp=None):
        """Add a message to the database."""
        if not remote_number or not direction:
            logger.warning("[DB] Cannot add message: missing remote_number or direction.")
            return None
        try:
            norm_number = ""
            if isinstance(remote_number, list):
                cleaned = [normalize_number(n, permissive=True) for n in remote_number]
                norm_number = ",".join(sorted(cleaned))
            elif "," in str(remote_number):
                norm_number = remote_number
            else:
                norm_number = normalize_number(remote_number, permissive=True)

            att_json = json.dumps(attachments) if attachments else "[]"
            msg_type = 'mms' if (subject or attachments) else 'sms'

            if not sender:
                sender = "Me" if direction == "outgoing" else norm_number

            now_str = format_timestamp()
            final_ts = now_str
            if status == "scheduled" and scheduled_timestamp:
                final_ts = scheduled_timestamp

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''INSERT INTO messages
                             (remote_number, direction, body, status, timestamp, type, subject, attachments, sender, scheduled_timestamp)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (norm_number, direction, body, status, final_ts, msg_type, subject, att_json, sender, scheduled_timestamp))
                self.conn_messages.commit()
                rowid = c.lastrowid

            self.invalidate_cache("conversations")
            GLib.idle_add(self.emit, 'messages-updated', norm_number, "insert")
            return rowid
        except Exception as e:
            logger.error(f"[DB] Add Message Error: {e}")
            return None

    def get_missed_scheduled_messages(self, timestamp_limit):
        """Retrieve scheduled messages that were missed (older than limit)."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''SELECT id, remote_number, body, subject, attachments, scheduled_timestamp
                             FROM messages
                             WHERE status='scheduled' AND scheduled_timestamp < ?''', (timestamp_limit,))
                return c.fetchall()
        except Exception as e:
            logger.error(f"[DB] Get Missed Scheduled Messages Error: {e}")
            return []

    def get_recent_scheduled_messages(self, start_ts, end_ts):
        """Retrieve scheduled messages within a time window [start, end]."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''SELECT id, remote_number, body, subject, attachments, scheduled_timestamp
                             FROM messages
                             WHERE status='scheduled' AND scheduled_timestamp >= ? AND scheduled_timestamp <= ?''', (start_ts, end_ts))
                return c.fetchall()
        except Exception as e:
            logger.error(f"[DB] Get Recent Scheduled Messages Error: {e}")
            return []

    def get_next_scheduled_timestamp(self):
        """Get the timestamp of the next upcoming scheduled message."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT MIN(scheduled_timestamp) FROM messages WHERE status='scheduled'")
                row = c.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"[DB] Get Next Scheduled Timestamp Error: {e}")
            return None

    def get_message_details(self, msg_id):
        """Retrieve details of a single message."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''SELECT id, remote_number, body, subject, attachments, status
                             FROM messages WHERE id=?''', (msg_id,))
                return c.fetchone()
        except Exception as e:
            logger.error(f"[DB] Get Message Details Error: {e}")
            return None

    def delete_scheduled_messages(self, ids_list):
        """Delete multiple scheduled messages by ID."""
        if not ids_list:
            return True
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                placeholders = ",".join("?" * len(ids_list))
                c.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids_list)
                self.conn_messages.commit()
                GLib.idle_add(self.emit, 'messages-updated', "", "delete")
                return True
        except Exception as e:
            logger.error(f"[DB] Delete Scheduled Messages Error: {e}")
            return False

    def update_message_schedule(self, msg_id, status="sent", timestamp=None):
        """Update message status or reschedule it."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                if status == "scheduled" and timestamp:
                    c.execute("UPDATE messages SET status=?, scheduled_timestamp=? WHERE id=?", (status, timestamp, msg_id))
                elif status == "sent":
                    now_str = format_timestamp()
                    c.execute("UPDATE messages SET status=?, timestamp=?, scheduled_timestamp=NULL WHERE id=?", (status, now_str, msg_id))
                else:
                    c.execute("UPDATE messages SET status=? WHERE id=?", (status, msg_id))
                c.execute("SELECT remote_number FROM messages WHERE id=?", (msg_id,))
                row = c.fetchone()
                self.conn_messages.commit()
                GLib.idle_add(self.emit, 'messages-updated', row[0] if row else "", "status")
                return True
        except Exception as e:
            logger.error(f"[DB] Update Schedule Error: {e}")
            return False

    def get_conversations(self, query=None, limit=50, offset=0, filter_type="all"):
        """Retrieve a list of recent conversations."""
        try:
            if offset == 0 and not query and self._conversations_cache is not None and filter_type == "all":
                if len(self._conversations_cache) >= limit or len(self._conversations_cache) < 51:
                    return self._conversations_cache[:limit]

            with self.lock:
                c = self.conn_messages.cursor()

                base_query = '''
                    SELECT m.remote_number, m.body, m.timestamp,
                           (SELECT COUNT(*) FROM messages WHERE remote_number = m.remote_number AND status = 'unread' AND direction='incoming') as unread_count,
                           m.id,
                           m.status
                    FROM messages m
                    WHERE m.id IN (
                        SELECT id FROM messages m2
                        WHERE m2.remote_number = m.remote_number
                        ORDER BY m2.timestamp DESC, m2.id DESC LIMIT 1
                    )
                '''

                if filter_type == "unread":
                    base_query += " AND (SELECT COUNT(*) FROM messages WHERE remote_number = m.remote_number AND status = 'unread' AND direction='incoming') > 0"
                elif filter_type == "group":
                    base_query += " AND m.remote_number LIKE '%,%'"
                elif filter_type == "individual":
                    base_query += " AND m.remote_number NOT LIKE '%,%'"
                elif filter_type == "alphabetical":
                    base_query += " AND m.remote_number GLOB '*[a-zA-Z]*'"

                base_query += " ORDER BY m.timestamp DESC, m.id DESC"

                needs_contact_filter = filter_type in ("saved", "unknown")

                if not needs_contact_filter:
                    base_query += " LIMIT ? OFFSET ?"
                    c.execute(base_query, (limit, offset))
                    rows = c.fetchall()
                    return rows

                c.execute(base_query)
                all_rows = c.fetchall()

                filtered_rows = []
                contact_map = self.get_contacts_lookup_map()

                for r in all_rows:
                    num = str(r[0])
                    is_saved = False
                    if "," in num:
                        recipients = [n.strip() for n in num.split(',')]
                        for rc in recipients:
                            norm = normalize_number(rc)
                            if norm in contact_map:
                                is_saved = True
                                break
                    else:
                        norm = normalize_number(num)
                        if norm in contact_map:
                            is_saved = True

                    if filter_type == "saved" and is_saved:
                        filtered_rows.append(r)
                    elif filter_type == "unknown" and not is_saved:
                        filtered_rows.append(r)

                return filtered_rows[offset:offset+limit]
        except Exception as e:
            logger.error(f"[DB] Get Conversations Error: {e}")
            return []

    def search_messages(self, query, chat_id=None, limit=50, offset=0):
        """Search messages using FTS."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()

                words = query.split()
                fts_query = " AND ".join([f"{w}*" for w in words])

                sql = """
                    SELECT m.remote_number, m.body, m.timestamp, m.id
                    FROM messages_search fts
                    JOIN messages m ON fts.rowid = m.id
                    WHERE messages_search MATCH ?
                """
                params = [fts_query]

                if chat_id:
                    if "," in str(chat_id):
                        norm_id = chat_id
                    else:
                        norm_id = normalize_number(chat_id, permissive=True)

                    sql += " AND m.remote_number = ?"
                    params.append(norm_id)

                sql += " ORDER BY rank LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                c.execute(sql, params)
                return c.fetchall()
        except Exception as e:
            logger.error(f"[DB] Search Error: {e}")
            try:
                c = self.conn_messages.cursor()
                sql = "SELECT remote_number, body, timestamp, id FROM messages WHERE (body LIKE ? OR remote_number LIKE ?)"
                like_query = f"%{query}%"
                params = [like_query, like_query]

                if chat_id:
                    sql += " AND remote_number = ?"
                    params.append(chat_id)

                sql += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                c.execute(sql, params)
                return c.fetchall()
            except Exception as e:
                logger.error(f"[DB] Fallback Search Error: {e}")
                return []

    def get_message_offset(self, msg_id):
        """Returns the number of messages newer than msg_id in its conversation."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT remote_number, timestamp FROM messages WHERE id=?", (msg_id,))
                target = c.fetchone()
                if not target:
                    return 0
                remote_number, target_ts = target
                c.execute("SELECT COUNT(*) FROM messages WHERE remote_number=? AND timestamp > ?", (remote_number, target_ts))
                row = c.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"[DB] Get Message Offset Error: {e}")
            return 0

    def get_chat_messages_around(self, msg_id, limit_before=20, limit_after=None):
        """Retrieve context messages around a specific message ID."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()

                c.execute("SELECT remote_number, timestamp FROM messages WHERE id=?", (msg_id,))
                target = c.fetchone()
                if not target:
                    return []

                remote_number, target_ts = target

                c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                             FROM messages
                             WHERE remote_number=? AND timestamp <= ? AND id != ?
                             ORDER BY timestamp DESC, id DESC LIMIT ?''',
                          (remote_number, target_ts, msg_id, limit_before))
                before = c.fetchall()

                if limit_after is not None:
                    c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                                 FROM messages
                                 WHERE remote_number=? AND timestamp >= ? AND id != ?
                                 ORDER BY timestamp ASC, id ASC LIMIT ?''',
                              (remote_number, target_ts, msg_id, limit_after))
                else:
                    c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                                 FROM messages
                                 WHERE remote_number=? AND timestamp >= ? AND id != ?
                                 ORDER BY timestamp ASC, id ASC''',
                              (remote_number, target_ts, msg_id))
                after = c.fetchall()

                c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                             FROM messages WHERE id=?''', (msg_id,))
                current = c.fetchall()

                full_list = before[::-1] + current + after

                return [
                    (
                        r[0], r[1], r[2], r[3], r[4], r[5],
                        json.loads(r[6]) if r[6] else [],
                        r[7] if len(r) > 7 else "Unknown",
                        r[8] if len(r) > 8 else None
                    )
                    for r in full_list
                ]
        except Exception as e:
            logger.error(f"[DB] Get Context Error: {e}")
            return []

    def get_chat_messages(self, number, limit=100, offset=0):
        """Retrieve messages for a specific conversation."""
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute('''SELECT id, direction, body, timestamp, status, subject, attachments, sender, scheduled_timestamp
                             FROM messages WHERE remote_number=?
                             ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?''', (norm_number, limit, offset))

                raw_rows = c.fetchall()
                return [
                    (
                        r[0], r[1], r[2], r[3], r[4], r[5],
                        json.loads(r[6]) if r[6] else [],
                        r[7] if len(r) > 7 else "Unknown",
                        r[8] if len(r) > 8 else None
                    )
                    for r in raw_rows
                ]
        except Exception as e:
            logger.error(f"[DB] Get Chat Error: {e}")
            return []

    def mark_conversation_read(self, number):
        """Mark a conversation as read."""
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("UPDATE messages SET status='read' WHERE remote_number=? AND status='unread' AND direction='incoming'", (norm_number,))
                changed = c.rowcount
                self.conn_messages.commit()

            if changed > 0:
                self.invalidate_cache("conversations")
                GLib.idle_add(self.emit, 'messages-updated', norm_number, "status")
        except Exception as e:
            logger.error(f"[DB] Mark Read Error: {e}")

    def mark_conversation_unread_from_message(self, number, message_id):
        """Mark a message and all newer messages in the conversation as unread."""
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT timestamp FROM messages WHERE id=?", (message_id,))
                row = c.fetchone()
                if not row:
                    return

                target_ts = row[0]
                c.execute("UPDATE messages SET status='unread' WHERE remote_number=? AND direction='incoming' AND status='read' AND (timestamp > ? OR (timestamp = ? AND id >= ?))", (norm_number, target_ts, target_ts, message_id))
                self.conn_messages.commit()
            self.invalidate_cache("conversations")
            GLib.idle_add(self.emit, 'messages-updated', norm_number, "status")
        except Exception as e:
            logger.error(f"[DB] Mark Unread Error: {e}")

    def get_unread_count(self, number):
        """Count unread incoming messages for a single conversation."""
        try:
            if isinstance(number, list):
                cleaned = [normalize_number(n, permissive=True) for n in number]
                norm_number = ",".join(sorted(cleaned))
            elif "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT COUNT(*) FROM messages WHERE remote_number=? AND status='unread' AND direction='incoming'", (norm_number,))
                row = c.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"[DB] Get Unread Count Error: {e}")
            return 0

    def get_total_unread_count(self):
        """Get the global count of unread messages."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT COUNT(*) FROM messages WHERE status='unread' AND direction='incoming'")
                row = c.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"[DB] Get Total Unread Error: {e}")
            return 0

    def delete_message(self, msg_id):
        """Delete a single message."""
        try:
            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("SELECT remote_number FROM messages WHERE id=?", (msg_id,))
                row = c.fetchone()
                c.execute("DELETE FROM messages WHERE id=?", (msg_id,))
                self.conn_messages.commit()
                self.invalidate_cache("conversations")
                GLib.idle_add(self.emit, 'messages-updated', row[0] if row else "", "delete")
                return True
        except Exception as e:
            logger.error(f"[DB] Delete Msg Error: {e}")
            return False

    def delete_drafts(self, number):
        """Delete all drafts for a conversation."""
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("DELETE FROM messages WHERE remote_number=? AND status='draft'", (norm_number,))
                self.conn_messages.commit()
                self.invalidate_cache("conversations")
                GLib.idle_add(self.emit, 'messages-updated', norm_number, "draft")
                return True
        except Exception as e:
            logger.error(f"[DB] Delete Drafts Error: {e}")
            return False

    def delete_conversation(self, number):
        """Delete an entire conversation."""
        try:
            if "," in str(number):
                norm_number = number
            else:
                norm_number = normalize_number(number, permissive=True)

            with self.lock:
                c = self.conn_messages.cursor()
                c.execute("DELETE FROM messages WHERE remote_number=?", (norm_number,))
                self.conn_messages.commit()
                self.invalidate_cache("conversations")
                GLib.idle_add(self.emit, 'messages-updated', norm_number, "delete")
                return True
        except Exception as e:
            logger.error(f"[DB] Delete Conv Error: {e}")
            return False
