"""Evidence-backed memory. SQLite only; all callers share Store's reentrant lock."""
import json
import time
import math
from memory_policy import contains_secret, redact_secrets

from personality import PersonalityStore


FIELDS = ("id kind subject content platform created_at importance emotion last_access absorbed "
          "absorbed_by scope entity_id status source_ids occurred_at processed due_at "
          "last_attempt pinned event_key").split()


class MemorySystem(PersonalityStore):
    def _migrate_memory(self):
        self._migrate_personality()
        columns = {r[1] for r in self._conn.execute("PRAGMA table_info(memories)")}
        definitions = {
            "entity_id": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "source_ids": "TEXT NOT NULL DEFAULT '[]'",
            "occurred_at": "REAL NOT NULL DEFAULT 0",
            "processed": "INTEGER NOT NULL DEFAULT 1",
            "due_at": "REAL NOT NULL DEFAULT 0",
            "last_attempt": "REAL NOT NULL DEFAULT 0",
            "pinned": "INTEGER NOT NULL DEFAULT 0",
            "event_key": "TEXT",
            "emotion": "REAL NOT NULL DEFAULT 0",
        }
        first = "status" not in columns
        for name, definition in definitions.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS memory_event_key ON memories(event_key)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS memory_scope_entity ON memories(scope, entity_id, status)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS short_term_session ON short_term(session, id)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS memory_history(memory_id INTEGER PRIMARY KEY, replaced_by INTEGER NOT NULL)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS memory_retry(memory_id INTEGER PRIMARY KEY, attempts INTEGER NOT NULL, retry_at REAL NOT NULL)")
        indexed = self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='memory_terms'").fetchone()
        self._conn.execute("CREATE TABLE IF NOT EXISTS memory_terms(term TEXT, memory_id INTEGER, PRIMARY KEY(term,memory_id)) WITHOUT ROWID")
        self._conn.execute("CREATE INDEX IF NOT EXISTS memory_terms_id ON memory_terms(memory_id)")
        self._conn.execute("CREATE TRIGGER IF NOT EXISTS memory_terms_clear AFTER UPDATE OF content ON memories BEGIN DELETE FROM memory_terms WHERE memory_id=NEW.id; END")
        self._conn.execute("CREATE INDEX IF NOT EXISTS memory_pending ON memories(kind,status,processed,occurred_at,id)")
        if not indexed:
            for mid, content in self._conn.execute("SELECT id,content FROM memories WHERE status!='deleted'").fetchall():
                self._index_memory(mid, content)
            for row in self._rows("status='superseded' AND absorbed_by IS NOT NULL", limit=None):
                self._mark_history(row, row['absorbed_by'])
        if first:
            # Old reflections lost their provenance. Preserve for review, never recall them.
            suspect = self._conn.execute("SELECT COUNT(*) FROM memories WHERE subject='self' AND kind='fact'").fetchone()[0]
            self._conn.execute("UPDATE memories SET status='quarantined' WHERE subject='self' AND kind='fact'")
            self._conn.execute("UPDATE memories SET absorbed=0, absorbed_by=NULL WHERE subject!='self'")
            self._conn.execute("UPDATE memories SET entity_id='qq:' || substr(scope,9) WHERE scope LIKE 'private:%' AND platform='qq'")
            if suspect:
                self._conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('self_profile','{}')")

    def _rows(self, where="1", args=(), limit=1000, offset=0, oldest=False):
        rows = self._conn.execute(
            f"SELECT {','.join(FIELDS)} FROM memories WHERE {where} ORDER BY id {'ASC' if oldest else 'DESC'} LIMIT ? OFFSET ?",
            (*args, -1 if limit is None else max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(zip(FIELDS, row))
            item["source_ids"] = json.loads(item["source_ids"])
            result.append(item)
        return result

    @staticmethod
    def _search_terms(text):
        text = ''.join(str(text).lower().split())
        return set(text) | {text[i:i+2] for i in range(len(text)-1)}

    def _index_memory(self, mid, content):
        self._conn.executemany('INSERT OR IGNORE INTO memory_terms VALUES(?,?)',
                               ((term, mid) for term in self._search_terms(content)))

    def _mark_history(self, target, replacement):
        # Preserve historical roots for personality provenance, exclude their raw text from current knowledge.
        pending, seen = list(target['source_ids']), set()
        while pending:
            sid = pending.pop()
            if sid in seen:
                continue
            seen.add(sid)
            rows = self._rows('id=?', (sid,), 1)
            if rows:
                pending.extend(rows[0]['source_ids'])
                if rows[0]['kind'] == 'episode':
                    self._conn.execute('INSERT OR REPLACE INTO memory_history VALUES(?,?)', (sid, replacement))
        for row in self._rows("kind='experience' AND status IN ('active','archived')", limit=None):
            if seen.intersection(row['source_ids']):
                self._conn.execute('INSERT OR REPLACE INTO memory_history VALUES(?,?)', (row['id'], replacement))

    @staticmethod
    def _visibility(scope):
        # None is an internal owner view; malformed public scopes fail closed.
        if scope in (None, "owner"):
            return "1", []
        if scope == "global":
            return "scope='global'", []
        if isinstance(scope, str) and scope.startswith(("private:", "group:")):
            return "scope IN (?, 'global')", [scope]
        return "0", []

    def _insert_memory(self, kind, subject, content, platform="", importance=5, scope="global",
                       entity_id="", source_ids=None, occurred_at=None, processed=1,
                       due_at=0, event_key=None, pinned=False, emotion=0.0):
        if not isinstance(content, str) or not content.strip():
            raise ValueError("记忆内容不能为空")
        if kind in ('fact', 'belief', 'intention') and contains_secret(content):
            raise ValueError('敏感凭据不能写入长期认识')
        content = redact_secrets(content)
        importance = max(1, min(10, int(importance)))
        emotion = max(-1.0, min(1.0, float(emotion or 0)))
        sources = list(dict.fromkeys(source_ids or []))
        for sid in sources:
            source = self._rows("id=? AND status='active'", (sid,), 1)
            if (not source or source[0]["scope"] != scope
                    or (source[0]["entity_id"] != entity_id
                        and not (kind == "experience" and entity_id == "self" and source[0]["kind"] == "episode"))):
                raise ValueError("记忆依据已失效或超出人物/隐私范围")
        cur = self._conn.execute(
            "INSERT INTO memories(kind,subject,content,platform,created_at,importance,emotion,scope,"
            "entity_id,source_ids,occurred_at,processed,due_at,event_key,pinned) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (kind, subject, content.strip()[:6000], platform, time.strftime("%Y-%m-%d %H:%M:%S"),
             importance, emotion, scope, entity_id, json.dumps(sources), occurred_at or time.time(),
             processed, due_at, event_key, int(pinned)),
        )
        self._index_memory(cur.lastrowid, content.strip()[:6000])
        return cur.lastrowid

    def add_memory(self, kind, subject, content, platform="", importance=5, scope="global", **kwargs):
        with self._lock, self._conn:
            # Only exact duplicates: similar wording can express opposite preferences.
            duplicate = self._rows("kind=? AND entity_id=? AND subject=? AND scope=? AND content=? AND status='active'",
                                   (kind, kwargs.get("entity_id", ""), subject, scope, content), 1)
            if duplicate:
                return None
            mid = self._insert_memory(kind, subject, content, platform, importance, scope, **kwargs)
            self._prune()
            return mid

    def _prune(self):
        from memory import MAX_MEMORIES
        # Archive, don't destroy evidence. Protect new/pinned memories and open intentions.
        episodes = self._rows("status='active' AND kind='episode' AND processed=1 AND pinned=0", limit=None)
        for row in episodes[500:]:
            if time.time()-row["occurred_at"] > 86400:
                self._conn.execute("UPDATE memories SET status='archived' WHERE id=?", (row["id"],))
        experiences = self._rows("status='active' AND kind='experience' AND pinned=0", limit=None)
        for row in experiences[300:]:
            if time.time()-row['occurred_at'] > 86400:
                self._conn.execute("UPDATE memories SET status='archived' WHERE id=?", (row['id'],))
        rows = self._rows("status='active' AND kind IN ('fact','user','person','belief')", limit=None)
        excess = len(rows) - MAX_MEMORIES
        if excess <= 0:
            return
        now = time.time()
        candidates = [r for r in rows if not r["pinned"] and now-r["occurred_at"] > 86400]
        candidates.sort(key=lambda r: r["importance"] * 2 ** (-max(0, now-max(r["last_access"], r["occurred_at"])) / 604800))
        for row in candidates[:excess]:
            self._conn.execute("UPDATE memories SET status='archived' WHERE id=?", (row["id"],))

    def get_memories(self, kind=None, subject=None, platform=None, limit=100, active_only=False,
                     scope=None, entity_id=None, offset=0, include_inactive=False, kinds=None, current_only=False):
        with self._lock:
            conditions, args = ["status='active'" if not include_inactive or active_only else "status!='deleted'"], []
            if kinds:
                conditions.append('kind IN (' + ','.join('?' for _ in kinds) + ')')
                args.extend(kinds)
            if current_only:
                conditions.append('id NOT IN (SELECT memory_id FROM memory_history)')
            for column, value in (("kind", kind), ("subject", subject), ("platform", platform),
                                  ("scope", scope), ("entity_id", entity_id)):
                if value is not None:
                    conditions.append(f"{column}=?")
                    args.append(value)
            return self._rows(" AND ".join(conditions), args, limit, offset)

    def retrieve(self, query, k=8, candidate_limit=None, context_scope=None, speaker=None, entity_id=None, exclude_ids=(), include_history=False, kinds=None, strict_entity=False):
        from memory import _overlap
        with self._lock:
            where, args = self._visibility(context_scope)
            if strict_entity:
                where += ' AND entity_id=?'
                args.append(entity_id)
            if kinds:
                where += ' AND kind IN (' + ','.join('?' for _ in kinds) + ')'
                args.extend(kinds)
            terms = sorted(self._search_terms(query))[:800]
            if not terms:
                return []
            state = "status IN ('active','archived')" if include_history else "status='active' AND id NOT IN (SELECT memory_id FROM memory_history)"
            exclude = [i for i in exclude_ids if type(i) is int]
            if exclude:
                state += ' AND id NOT IN (' + ','.join('?' for _ in exclude) + ')'
            cap = max(k, min(1000, candidate_limit or 256))
            ids = self._conn.execute(
                f"SELECT id FROM memories JOIN memory_terms ON memory_id=id WHERE ({where}) AND {state} "
                f"AND term IN ({','.join('?' for _ in terms)}) GROUP BY id "
                "ORDER BY COUNT(*) DESC, CASE kind WHEN 'fact' THEN 2 WHEN 'intention' THEN 1 ELSE 0 END DESC, pinned DESC, id DESC LIMIT ?", (*args, *exclude, *terms, cap)).fetchall()
            rows = self._rows('id IN (' + ','.join('?' for _ in ids) + ')', [r[0] for r in ids]) if ids else []
            scored = []
            for row in rows:
                if row["id"] in exclude_ids or set(exclude_ids).intersection(row['source_ids']):
                    continue
                relevance = _overlap(query, row["content"])
                if relevance < 0.08:
                    continue
                entity_match = bool(entity_id and row["entity_id"] == entity_id)
                age = max(0, time.time()-row["occurred_at"])
                priority = {'fact': .5, 'intention': .4, 'belief': -.2, 'episode': -.3}.get(row['kind'], 0)
                score = 3*relevance + .5*2**(-age/604800) + .6*row["importance"]/10 + .3*entity_match + priority
                scored.append((score, row))
            scored.sort(key=lambda pair: (-pair[0], -pair[1]["id"]))
            selected, length = [], 0
            for _, row in scored:
                if len(selected) >= k:
                    break
                if length + len(row["content"]) > 4000:
                    continue
                selected.append(row)
                length += len(row["content"])
            with self._conn:
                for row in selected:
                    self._conn.execute("UPDATE memories SET last_access=? WHERE id=?", (time.time(), row["id"]))
            return selected

    def record_episode(self, entity_id, subject, content, scope, platform="qq", event_key=None, eligible=True, occurred_at=None):
        try:
            occurred_at = float(occurred_at)
            if not math.isfinite(occurred_at) or occurred_at <= 0 or occurred_at > time.time()+300:
                occurred_at = None
        except (TypeError, ValueError, OverflowError):
            occurred_at = None
        with self._lock, self._conn:
            if event_key:
                old = self._rows("event_key=?", (event_key,), 1)
                if old:
                    return old[0]["id"]  # Tombstones also deduplicate replayed events.
            self._conn.execute("UPDATE memories SET subject=? WHERE entity_id=? AND entity_id!='' AND status!='deleted'", (subject, entity_id))
            return self._insert_memory("episode", subject, content, platform, 4, scope,
                                       entity_id=entity_id, event_key=event_key, processed=int(not eligible), occurred_at=occurred_at)

    def pending_episode_batch(self, now=None):
        now = now or time.time()
        with self._lock:
            where = "kind='episode' AND status='active' AND processed=0 AND occurred_at<? AND id NOT IN (SELECT memory_id FROM memory_retry WHERE retry_at>?)"
            rows = self._rows(where, (now-30, now), 1, oldest=True)
            if not rows:
                return []
            first = rows[0]
            return self._rows(where + ' AND entity_id=? AND scope=?',
                              (now-30, now, first['entity_id'], first['scope']), 10, oldest=True)

    def retry_episodes(self, episodes):
        with self._lock, self._conn:
            for row in episodes:
                old = self._conn.execute('SELECT attempts FROM memory_retry WHERE memory_id=?', (row['id'],)).fetchone()
                attempts = old[0]+1 if old else 1
                self._conn.execute('INSERT OR REPLACE INTO memory_retry VALUES(?,?,?)',
                                   (row['id'], attempts, time.time()+min(3600, 30*2**min(attempts, 7))))

    def memory_queue_status(self):
        with self._lock:
            count, oldest = self._conn.execute("SELECT COUNT(*),MIN(occurred_at) FROM memories WHERE kind='episode' AND status='active' AND processed=0").fetchone()
            return dict(pending=count, oldest_wait_seconds=max(0, time.time()-oldest) if oldest else 0)

    def apply_episode_result(self, episodes, result):
        """Model proposes; the store enforces source, identity and update constraints atomically."""
        if not isinstance(result, dict) or not isinstance(result.get("memories"), list):
            raise ValueError("无效的记忆整理结果")
        with self._lock, self._conn:
            available = {r["id"]: r for r in episodes}
            for sid in available:
                if not self._rows("id=? AND status='active' AND processed=0", (sid,), 1):
                    return 0  # Deleted/corrected while the model was running.
            made = 0
            for item in result.get("memories", [])[:6]:
                if not isinstance(item, dict) or item.get("kind") not in ("fact", "intention"):
                    continue
                sources = item.get("source_ids")
                if not isinstance(sources, list) or not sources or any(type(s) is not int or s not in available for s in sources):
                    continue
                base = available[sources[0]]
                if any((available[s]["scope"], available[s]["entity_id"]) != (base["scope"], base["entity_id"]) for s in sources):
                    continue
                content = item.get("content")
                if not isinstance(content, str) or not content.strip() or len(content) > 1000:
                    continue
                if contains_secret(content):
                    continue
                replaces = item.get("replaces", [])
                if not isinstance(replaces, list):
                    continue
                targets = []
                for rid in replaces:
                    if type(rid) is not int:
                        continue
                    found = self._rows("id=? AND scope=? AND entity_id=? AND status='active' AND kind IN ('fact','intention')",
                                       (rid, base["scope"], base["entity_id"]), 1)
                    targets.extend(found)
                targets = [target for target in targets if target["kind"] == item["kind"]]
                source_time = max(available[s]['occurred_at'] for s in sources)
                # A delayed/retried old event must not rewrite a newer, sourced version.
                if any(target['occurred_at'] > source_time for target in targets):
                    continue
                action = item.get("state", "active")
                if action in ("done", "cancelled") and item["kind"] == "intention":
                    for target in targets:
                        if target["kind"] == "intention":
                            self._conn.execute("UPDATE memories SET status=? WHERE id=?", (action, target["id"]))
                    continue
                due = item.get("due_at", 0)
                try:
                    due = float(due or 0)
                    importance = max(1, min(10, int(item.get("importance", 5))))
                except (TypeError, ValueError, OverflowError):
                    continue
                try:
                    emotion = max(-1.0, min(1.0, float(item.get("emotion", 0) or 0)))
                except (TypeError, ValueError, OverflowError):
                    emotion = 0.0
                # Only explicit, bounded future follow-ups; unknown dates stay conversational.
                if not math.isfinite(due) or not time.time()-7*86400 < due < time.time()+366*86400:
                    due = 0
                duplicate = self._rows("kind=? AND content=? AND scope=? AND entity_id=? AND status='active'",
                                       (item["kind"], content.strip(), base["scope"], base["entity_id"]), 1)
                if duplicate and not targets:
                    continue
                mid = self._insert_memory(item["kind"], base["subject"], content, base["platform"], importance,
                                          base["scope"], entity_id=base["entity_id"], source_ids=sources, due_at=due,
                                          emotion=emotion, occurred_at=source_time)
                for target in targets:
                    self._mark_history(target, mid)
                    self._invalidate_dependents([target["id"]], "superseded")
                    self._conn.execute("UPDATE memories SET status='superseded', absorbed_by=? WHERE id=?", (mid, target["id"]))
                made += 1
            for sid in available:
                self._conn.execute("UPDATE memories SET processed=1 WHERE id=?", (sid,))
                self._conn.execute('DELETE FROM memory_retry WHERE memory_id=?', (sid,))
            self._prune()
            return made

    def add_reflection(self, content, sources):
        with self._lock, self._conn:
            rows = [self._rows("id=? AND status='active'", (sid,), 1) for sid in sources]
            if len(rows) < 2 or any(not r for r in rows):
                return None
            base = rows[0][0]
            if any((r[0]["scope"], r[0]["entity_id"]) != (base["scope"], base["entity_id"]) for r in rows):
                return None
            if any(r[0]["kind"] not in ("fact", "experience") for r in rows):
                return None
            existing = self._rows("kind='belief' AND status='active' AND scope=? AND entity_id=?", (base["scope"], base["entity_id"]))
            if any(set(r["source_ids"]) == set(sources) for r in existing):
                return None
            return self._insert_memory("belief", base["subject"], content, base["platform"], 6, base["scope"],
                                       entity_id=base["entity_id"], source_ids=sources)

    def working_context(self, scope, entity_id=None, exclude_ids=()):
        # Extractive summary is rebuilt on every read; no stale derived summary cache.
        where, args = self._visibility(scope)
        where += ' AND id NOT IN (SELECT memory_id FROM memory_history)'
        if entity_id:
            where += " AND entity_id=?"
            args.append(entity_id)
        with self._lock:
            rows = self._rows(f"({where}) AND status='active' AND kind='episode'", args, 4)
            rows += self._rows(f"({where}) AND status='active' AND kind='intention'", args, 3)
            rows = [r for r in rows if r['id'] not in exclude_ids and not set(exclude_ids).intersection(r['source_ids'])]
        return "\n".join(f"[{r['kind']} #{r['id']} {r['created_at']} {'已尝试联系，不重复追问' if r['last_attempt'] else ''}] {r['content'][:350]}" for r in reversed(rows))

    def action_context(self, scope, ids=()):
        where, args = self._visibility(scope)
        if ids:
            where += ' AND id IN (' + ','.join('?' for _ in ids) + ')'
            args.extend(ids)
        with self._lock:
            return self._rows(f"({where}) AND status='active' AND id NOT IN (SELECT memory_id FROM memory_history) AND kind!='intention'", args, 8)

    def due_intentions(self, entity_id, scope="owner", now=None):
        now = now or time.time()
        with self._lock:
            if self._rows("kind='episode' AND entity_id=? AND scope=? AND status='active' AND occurred_at>?",
                          (entity_id, scope, now-900), 1):
                return []
            # Only the owner's own private/owner events can motivate owner follow-ups.
            return self._rows("kind='intention' AND status='active' AND entity_id=? AND scope=? "
                              "AND due_at>0 AND due_at<=? AND due_at>=? AND last_attempt=0",
                              (entity_id, scope, now, now-7*86400), 5)

    def claim_intention(self, mid, entity_id):
        with self._lock, self._conn:
            if mid not in {r["id"] for r in self.due_intentions(entity_id)}:
                return False
            cur = self._conn.execute("UPDATE memories SET last_attempt=? WHERE id=? AND kind='intention' "
                                     "AND entity_id=? AND scope='owner' AND status='active' AND last_attempt=0 "
                                     "AND due_at>0 AND due_at<=? AND due_at>=?",
                                     (time.time(), mid, entity_id, time.time(), time.time()-7*86400))
            return cur.rowcount == 1

    def _invalidate_dependents(self, ids, status):
        invalid = set(ids)
        rows = self._rows("status!='deleted'", limit=None)
        changed = True
        while changed:
            changed = False
            for row in rows:
                if row["id"] not in invalid and invalid.intersection(row["source_ids"]):
                    invalid.add(row["id"])
                    changed = True
        for mid in invalid:
            self._conn.execute("UPDATE memories SET status=?, processed=1 WHERE id=?", (status, mid))
        self._invalidate_personality(invalid)
        return invalid

    def _forget_memory(self, mid):
        row = self._rows("id=?", (mid,), 1)
        if not row:
            return
        self._bump_memory_epoch()
        # Traverse all ancestors, including a belief's original episode evidence.
        ancestors, pending = set(), [mid]
        while pending:
            rid = pending.pop()
            if rid in ancestors:
                continue
            ancestors.add(rid)
            source = self._rows("id=?", (rid,), 1)
            if source:
                pending.extend(source[0]["source_ids"])
        ids = self._invalidate_dependents(ancestors, "deleted")
        for rid in ids:
            self._conn.execute("UPDATE memories SET content='', subject='' WHERE id=?", (rid,))
        # Conversation context must not immediately teach the forgotten fact again.
        scope = row[0]["scope"]
        if scope in ("owner", "global"):
            self._conn.execute("DELETE FROM short_term")
        else:
            self._conn.execute("DELETE FROM short_term WHERE session=?", (scope.replace(":", "_", 1),))

    def delete_memory(self, mid):
        with self._lock, self._conn:
            self._forget_memory(mid)

    def update_memory(self, mid, changes):
        if not isinstance(changes, dict) or not changes or set(changes)-{"content", "pinned", "status"}:
            raise ValueError("无效的记忆更新字段")
        with self._lock, self._conn:
            found = self._rows("id=? AND status!='deleted'", (mid,), 1)
            if not found:
                raise ValueError("记忆不存在")
            row = found[0]
            if "content" in changes:
                content = changes["content"]
                if not isinstance(content, str) or not content.strip() or len(content) > 1000:
                    raise ValueError("内容应为 1~1000 字")
                # A human correction is authoritative, not a new model inference.
                self._forget_memory(mid)
                kind = 'intention' if row['kind'] == 'intention' else 'fact'
                replacement = self._insert_memory(kind, row["subject"], content, row["platform"], row["importance"],
                                           row["scope"], entity_id=row["entity_id"], pinned=bool(row["pinned"]),
                                           due_at=row['due_at'] if kind == 'intention' else 0)
                if kind == 'intention':
                    self._conn.execute('UPDATE memories SET status=?,last_attempt=? WHERE id=?',
                                       (row['status'], row['last_attempt'], replacement))
                return replacement
            if "pinned" in changes:
                self._conn.execute("UPDATE memories SET pinned=? WHERE id=?", (int(changes["pinned"] is True), mid))
            if "status" in changes:
                if row["kind"] != "intention" or changes["status"] not in ("done", "cancelled"):
                    raise ValueError("只允许完成或取消未完事项")
                self._conn.execute("UPDATE memories SET status=? WHERE id=?", (changes["status"], mid))
            return mid

    def clear_memories(self):
        with self._lock, self._conn:
            self._bump_memory_epoch()
            self._conn.execute("DELETE FROM personality_traits")
            self._conn.execute('DELETE FROM personality_vetoes')
            self._conn.execute('DELETE FROM memory_terms')
            self._conn.execute('DELETE FROM short_term')
            self._conn.execute("UPDATE memories SET status='deleted',content='',subject='',processed=1")
            self._conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('self_profile','{}')")

    def _bump_memory_epoch(self):
        self.delivery_forget()
        epoch = int(self.get_setting_raw('memory_epoch') or 0) + 1
        self._conn.execute("INSERT OR REPLACE INTO settings VALUES('memory_epoch',?)", (json.dumps(epoch),))
