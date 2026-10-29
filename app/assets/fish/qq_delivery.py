"""Durable per-part delivery journal. Unknown submissions are never replayed."""
import hashlib
import json
import time
import uuid

from memory_policy import redact_secrets
from qq_faces import face_tag

SCHEMA = """
CREATE TABLE IF NOT EXISTS qq_outbox (
 id INTEGER PRIMARY KEY, batch TEXT NOT NULL, position INTEGER NOT NULL,
 account TEXT NOT NULL, session TEXT NOT NULL, signature TEXT,
 content TEXT NOT NULL, status TEXT NOT NULL, epoch INTEGER NOT NULL,
 created REAL NOT NULL, updated REAL NOT NULL, message_id TEXT, short_id INTEGER,
 UNIQUE(batch, position)
);
CREATE UNIQUE INDEX IF NOT EXISTS qq_outbox_message ON qq_outbox(account,message_id)
 WHERE message_id IS NOT NULL;
"""


def signature(segments):
    """Only match exact text/face/at sequences; remote image URLs are not stable."""
    if not isinstance(segments, list):
        return None
    normalized = []
    for seg in segments:
        kind, data = seg.get('type'), seg.get('data') or {}
        key = {'text': 'text', 'face': 'id', 'at': 'qq'}.get(kind)
        if key is None or key not in data:
            return None
        value = str(data[key])
        if kind == 'text' and normalized and normalized[-1][0] == 'text':
            normalized[-1][1] += value
        elif kind != 'text' or value:
            normalized.append([kind, value])
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode()).hexdigest()


def unit_text(segments):
    return ''.join(str(s['data'].get('text', '')) if s['type'] == 'text'
                   else face_tag(s['data'].get('id')) if s['type'] == 'face'
                   else f"[at:{s['data'].get('qq')}]" if s['type'] == 'at'
                   else '[图片]' for s in segments)


def context_text(content, status):
    if status == 'confirmed':
        return content
    labels = {'sending': '已提交发送，送达尚未确认，勿自动重发',
              'unconfirmed': '已提交发送，送达尚未确认，勿自动重发',
              'rejected': '平台拒绝发送，未送达', 'cancelled': '发送已取消'}
    return f"（发送状态：{labels.get(status, status)}）{content}"


class DeliveryStore:
    def init_delivery(self):
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            # A restarted process cannot know whether a submitted request executed.
            self._conn.execute("UPDATE qq_outbox SET status='unconfirmed' WHERE status='sending'")
            self._conn.execute("UPDATE qq_outbox SET status='cancelled' WHERE status='queued'")

    def delivery_plan(self, account, session, units, epoch):
        batch = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM qq_outbox WHERE id NOT IN (SELECT id FROM qq_outbox ORDER BY id DESC LIMIT 5000) "
                               "AND status IN ('confirmed','cancelled','rejected')")
            for i, unit in enumerate(units):
                self._conn.execute('INSERT INTO qq_outbox(batch,position,account,session,signature,content,status,epoch,created,updated) '
                                   'VALUES(?,?,?,?,?,?,\'queued\',?,?,?)',
                                   (batch,i,str(account or ''),session,signature(unit),redact_secrets(unit_text(unit)),epoch,now,now))
            return [r[0] for r in self._conn.execute('SELECT id FROM qq_outbox WHERE batch=? ORDER BY position',(batch,))]

    def delivery_state(self, rid, status, message_id=None):
        with self._lock, self._conn:
            row = self._conn.execute('SELECT session,content,epoch,short_id,status FROM qq_outbox WHERE id=?',(rid,)).fetchone()
            if not row or row[4] == 'confirmed':
                return False
            session, content, epoch, short_id, previous = row
            if message_id is not None and self._conn.execute(
                    'SELECT 1 FROM qq_outbox WHERE account=(SELECT account FROM qq_outbox WHERE id=?) AND message_id=? AND id!=?',
                    (rid,str(message_id),rid)).fetchone():
                return False
            valid_epoch = epoch == (self.get_setting_raw('memory_epoch') or 0)
            rendered = context_text(content,status)
            if status == 'sending' and previous == 'queued' and valid_epoch:
                cur = self._conn.execute("INSERT INTO short_term(session,role,content,platform,ts) VALUES(?,'assistant',?,'qq',?)",
                                         (session,rendered,time.strftime('%Y-%m-%d %H:%M:%S')))
                short_id = cur.lastrowid
                self._conn.execute('DELETE FROM short_term WHERE session=? AND id NOT IN '
                    '(SELECT id FROM short_term WHERE session=? ORDER BY id DESC LIMIT 200)',(session,session))
            elif short_id is not None and valid_epoch:
                # Never reinsert context that was forgotten/reset/trimmed.
                self._conn.execute('UPDATE short_term SET content=? WHERE id=?',(rendered,short_id))
            self._conn.execute('UPDATE qq_outbox SET status=?,updated=?,message_id=COALESCE(?,message_id),short_id=?, '
                               'created=CASE WHEN ? THEN ? ELSE created END WHERE id=?',
                               (status,time.time(),None if message_id is None else str(message_id),short_id,
                                status == 'sending' and previous == 'queued',time.time(),rid))
            return True

    def delivery_cancel(self, ids):
        with self._lock, self._conn:
            self._conn.executemany("UPDATE qq_outbox SET status='cancelled',updated=? WHERE id=? AND status='queued'",
                                   [(time.time(),rid) for rid in ids])

    def delivery_confirm_event(self, event):
        """Conservative match: own account, conversation, exact payload, fresh, unique."""
        sig = signature(event.get('message'))
        mid, account = event.get('message_id'), str(event.get('self_id') or '')
        if sig is None or mid is None or not account:
            return False
        kind = event.get('message_type')
        if kind not in ('private','group'):
            return False
        sender = event.get('sender') or {}
        if str(sender.get('user_id') or event.get('user_id') or '') != account:
            return False
        target = event.get('group_id') if kind == 'group' else event.get('target_id') or event.get('user_id')
        session = f'{kind}_{target}'
        try:
            stamp = float(event['time'])
        except (KeyError, TypeError, ValueError):
            return False
        now = time.time()
        if not now-180 <= stamp <= now+5:
            return False
        with self._lock:
            candidates = self._conn.execute("SELECT id FROM qq_outbox WHERE account=? AND session=? AND signature=? "
                "AND status IN ('sending','unconfirmed') AND created BETWEEN ? AND ? AND epoch=?",
                (account,session,sig,max(now-180,stamp-180),stamp+2,self.get_setting_raw('memory_epoch') or 0)).fetchall()
            if len(candidates) != 1:
                return False
            return self.delivery_state(candidates[0][0],'confirmed',mid)

    def delivery_status(self, rid):
        with self._lock:
            row = self._conn.execute('SELECT status FROM qq_outbox WHERE id=?',(rid,)).fetchone()
            return row[0] if row else None

    def delivery_counts(self):
        with self._lock:
            return dict(self._conn.execute('SELECT status,COUNT(*) FROM qq_outbox GROUP BY status'))

    def delivery_forget(self):
        # Called in the same transaction as memory invalidation.
        self._conn.execute("UPDATE qq_outbox SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END, "
                           "content='',signature=NULL,short_id=NULL")
