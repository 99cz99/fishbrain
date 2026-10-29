"""One public personality, with private, revocable evidence kept out of prompts."""
import hashlib
import json
import re
import time
from memory_policy import redact_secrets

CAPS = {"values": 4, "style": 3, "likes": 4, "boundaries": 3}


class PersonalityStore:
    def _migrate_personality(self):
        self._conn.execute("""CREATE TABLE IF NOT EXISTS personality_traits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dimension TEXT NOT NULL,
            content TEXT NOT NULL, source_ids TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL)""")
        self._conn.execute("CREATE TABLE IF NOT EXISTS personality_vetoes(dimension TEXT, content TEXT, PRIMARY KEY(dimension,content))")
        self._conn.execute("INSERT OR IGNORE INTO personality_vetoes SELECT dimension,content FROM personality_traits WHERE status='manual_removed'")

    def personality_vetoes(self):
        with self._lock:
            return [dict(dimension=r[0], content=r[1]) for r in self._conn.execute('SELECT dimension,content FROM personality_vetoes')]

    def personality_traits(self):
        with self._lock:
            return [dict(id=r[0], dimension=r[1], content=r[2], source_ids=json.loads(r[3]))
                    for r in self._conn.execute(
                        "SELECT id,dimension,content,source_ids FROM personality_traits WHERE status='active' ORDER BY id")]

    def _effective_profile(self, manual):
        profile = {d: list(manual.get(d) or []) for d in CAPS}
        for row in self.personality_traits():
            if row['content'] not in profile[row['dimension']]:
                profile[row['dimension']].append(row['content'])
        return profile

    @staticmethod
    def profile_revision(profile):
        return hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def save_manual_profile(self, profile):
        """Unedited automatic entries keep their provenance when saving the editor."""
        with self._lock, self._conn:
            derived = self.personality_traits()
            for row in derived:
                if row['content'] not in profile.get(row['dimension'], []):
                    self._conn.execute("UPDATE personality_traits SET status='manual_removed' WHERE id=?", (row['id'],))
                    self._conn.execute('INSERT OR IGNORE INTO personality_vetoes VALUES(?,?)', (row['dimension'], row['content']))
            manual = {d: [text for text in profile.get(d, [])
                          if not any(r['dimension'] == d and r['content'] == text for r in derived)] for d in CAPS}
            self._conn.execute("INSERT OR REPLACE INTO settings VALUES('self_profile',?)", (json.dumps(manual, ensure_ascii=False),))
            # Explicitly re-adding text in the editor is the only way to lift an exact veto.
            for dim, texts in profile.items():
                for text in texts:
                    self._conn.execute('DELETE FROM personality_vetoes WHERE dimension=? AND content=?', (dim, text))

    def record_self_experience(self, content, scope, event_id=None, platform='qq', event_key=None):
        """Record only observed/sent behaviour; an input episode is never made public."""
        with self._lock, self._conn:
            if event_key and self._rows('event_key=?', (event_key,), 1):
                return None
            sources = []
            if event_id is not None:
                rows = self._rows("id=? AND kind='episode' AND status='active'", (event_id,), 1)
                if not rows or rows[0]['scope'] != scope:
                    return None
                content = '请求驱动的回复（不是独立偏好证据）；对方原话（私人语境，不代表我的喜好）：' + rows[0]['content'] + '\n实际回复：' + content
                sources = [event_id]
                if self._rows("kind='experience' AND entity_id='self' AND source_ids=?", (json.dumps(sources),), 1):
                    return None
            mid = self._insert_memory('experience', 'self', content, platform, 5, scope,
                                      entity_id='self', source_ids=sources, event_key=event_key)
            cfg = self.get_settings().get('memory') or {}
            cfg['reflect_credit'] = int(cfg.get('reflect_credit') or 0) + 5
            self._conn.execute("INSERT OR REPLACE INTO settings VALUES('memory',?)", (json.dumps(cfg),))
            self._prune()
            return mid

    def record_action_result(self, action, content, outcome, platform, scope='owner', event_key=None):
        if outcome not in ('confirmed', 'unconfirmed', 'failed_or_partial'):
            raise ValueError('无效的动作结果')
        return self.record_self_experience(
            f'实际动作={action}；结果={outcome}（确认不表示被阅读）；内容：{redact_secrets(content)}',
            scope, platform=platform, event_key=event_key)

    def _evidence_roots(self, mid):
        # Archiving limits recall, but does not revoke the historical evidence.
        rows = self._rows("id=? AND status IN ('active','archived')", (mid,), 1)
        if not rows:
            return set()
        row = rows[0]
        if row['kind'] == 'episode' or not row['source_ids']:
            return {mid}
        roots = set()
        for sid in row['source_ids']:
            found = self._evidence_roots(sid)
            if not found:
                return set()
            roots.update(found)
        return roots

    def apply_personality_candidates(self, candidates, evidence, revision):
        """Validate again after model calls, including deletion/reset/manual-edit races."""
        with self._lock, self._conn:
            if self.get_settings()['self_profile_revision'] != revision:
                return 0
            available = {r['id']: r for r in evidence}
            current = {r['id']: r for r in self.personality_traits()}
            changed = 0
            for item in candidates:
                dim, content, sources = item.get('dimension'), item.get('content'), item.get('source_ids')
                replaces = item.get('replaces', [])
                if (not isinstance(dim, str) or dim not in CAPS or not isinstance(content, str) or not 2 <= len(content) <= 60
                        or re.search(r'[\d@\n]|https?://|私聊|主人|账号|地址|密码|秘密|约定', content)
                        or not isinstance(sources, list) or len(set(s for s in sources if type(s) is int)) < 2
                        or any(type(s) is not int or s not in available for s in sources)
                        or not isinstance(replaces, list)
                        or any(type(r) is not int or r not in current or current[r]['dimension'] != dim for r in replaces)):
                    continue
                live = [self._rows("id=? AND status='active' AND entity_id='self' AND kind IN ('experience','belief')", (s,), 1) for s in sources]
                if any(not r or r[0]['content'] != available[s]['content'] for s, r in zip(sources, live)):
                    continue
                roots = set().union(*(self._evidence_roots(s) for s in sources))
                if len(roots) < (3 if replaces else 2):
                    continue
                manual = self.get_setting_raw('self_profile') or {}
                from memory import _similar
                if (manual.get(dim) or any(v['dimension'] == dim and _similar(v['content'], content, .8)
                                          for v in self.personality_vetoes())):
                    continue
                duplicate = next((r for r in current.values() if r['dimension'] == dim and r['content'] == content), None)
                if duplicate:
                    merged = sorted(set(duplicate['source_ids']) | set(sources))
                    self._conn.execute('UPDATE personality_traits SET source_ids=? WHERE id=?', (json.dumps(merged), duplicate['id']))
                    duplicate['source_ids'] = merged
                    continue
                count = len(manual.get(dim, [])) + sum(r['dimension'] == dim for r in current.values())
                if count - len(set(replaces)) >= CAPS[dim]:
                    continue
                for rid in set(replaces):
                    self._conn.execute("UPDATE personality_traits SET status='superseded' WHERE id=?", (rid,))
                    del current[rid]
                cur = self._conn.execute("INSERT INTO personality_traits(dimension,content,source_ids,created_at) VALUES(?,?,?,?)",
                                         (dim, content.strip(), json.dumps(sorted(set(sources))), time.time()))
                current[cur.lastrowid] = dict(id=cur.lastrowid, dimension=dim, content=content.strip(), source_ids=sources)
                changed += 1
            return changed

    def _invalidate_personality(self, invalid):
        for row in self.personality_traits():
            remaining = [s for s in row['source_ids'] if s not in invalid and self._evidence_roots(s)]
            roots = set().union(*(self._evidence_roots(s) for s in remaining))
            if len(remaining) < 2 or len(roots) < 2:
                self._conn.execute("UPDATE personality_traits SET status='invalid' WHERE id=?", (row['id'],))
            else:
                self._conn.execute('UPDATE personality_traits SET source_ids=? WHERE id=?', (json.dumps(remaining), row['id']))
