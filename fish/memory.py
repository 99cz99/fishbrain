# memory.py — Fishbrain 的记忆与设定存储（SQLite，本地，永久保留）
# 表：
#   settings   软设定（名字/性格/频率/底线开关，app 可改）
#   memories   长期记忆（记住用户、每个好友/群、自身经历）
#   logs       活动 + 聊天记录（监管 app 的「状态」和「聊天记录」都从这里读）
#   short_term 短期上下文（最近对话，让回复接得上）
import json
import re
import sqlite3
import threading
import time

# 软设定默认值（代码只给中性占位，用户在 app 里改）
DEFAULT_SETTINGS = {
    "name": "",                       # 名字（用户自设，默认空）
    "owner_qq": "",                   # 主人 QQ 号（主动冒泡发给谁）
    "persona": "",                    # 设定（性格+口头禅都写这里，自由文本）
    "chat_scope": "all",              # all = 对谁都放开
    "midnight_ok": True,              # 半夜也可以主动找你
    "approve_money": True,            # 底线：花钱先问
    "approve_publish": True,          # 底线：对外发内容先问
    "allow_self_evolve": True,        # 自成长开关：心跳里能不能自动进化设定
    "enabled_platforms": ["qq", "heybox", "bilibili"],
    "heybox_token": "",             # 小黑盒 token（app 设定页填，热生效）
    "llm": {
        "provider": "openai",      # openai / anthropic / gemini
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "vision_model": "",
        "vision_base_url": "",
    },
}

# 记忆系统配置
MAX_MEMORIES = 300              # 长期记忆总量上限（超出淘汰价值最低的）
IMPORTANCE_REFLECT_THRESHOLD = 30  # 累计重要性越过它触发反思合并

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,       -- user / person / self / fact
  subject TEXT NOT NULL,    -- 谁：user / 人名 / self / 主题
  content TEXT NOT NULL,    -- 说了什么
  platform TEXT NOT NULL DEFAULT '',  -- 平台：qq / heybox / bilibili
  created_at TEXT NOT NULL,
  importance INTEGER NOT NULL DEFAULT 5,  -- 重要性 1-10
  last_access REAL NOT NULL DEFAULT 0,    -- 最后被召回的时间戳
  absorbed INTEGER NOT NULL DEFAULT 0,    -- 0=活跃 1=已被反思合并
  absorbed_by INTEGER,                    -- 吸收它的洞察 id
  scope TEXT NOT NULL DEFAULT 'global'    -- 归属：owner / private:{id} / group:{id} / global
);
CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  level TEXT NOT NULL,
  category TEXT NOT NULL,   -- status / chat / action / approval / error
  message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS short_term (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session TEXT NOT NULL,
  role TEXT NOT NULL,       -- user / assistant
  content TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT '',  -- 平台：qq / heybox / bilibili
  ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,       -- publish / money / friend_request / group_request / ...
  summary TEXT NOT NULL,
  status TEXT NOT NULL,     -- pending / approved / rejected
  created_at TEXT NOT NULL
);
"""


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---- 文本相似度（字符 2-gram，中文友好，纯标准库）----
def _norm(s):
    return re.sub(r"\s+", "", str(s)).lower()


def _bigrams(s):
    s = _norm(s)
    if len(s) < 2:
        return {s}
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _similar(a, b, threshold=0.6):
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return False
    return len(ga & gb) / len(ga | gb) >= threshold


def _overlap(query, content):
    q, c = _bigrams(query), _bigrams(content)
    if not q or not c:
        return 0.0
    return len(q & c) / len(q)


class Store:
    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        self._lock = threading.Lock()

    def _migrate(self):
        # 老库补 platform 列
        for table in ("memories", "short_term"):
            cols = [r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")]
            if "platform" not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN platform TEXT NOT NULL DEFAULT ''")
        # 记忆新列（importance / last_access / absorbed / absorbed_by）
        mcols = [r[1] for r in self._conn.execute("PRAGMA table_info(memories)")]
        if "importance" not in mcols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN importance INTEGER NOT NULL DEFAULT 5")
        if "last_access" not in mcols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN last_access REAL NOT NULL DEFAULT 0")
        if "absorbed" not in mcols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN absorbed INTEGER NOT NULL DEFAULT 0")
        if "absorbed_by" not in mcols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN absorbed_by INTEGER")
        if "scope" not in mcols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'global'")

    # ---- 软设定 ----
    def get_settings(self):
        d = dict(DEFAULT_SETTINGS)
        for key, value in self._conn.execute("SELECT key, value FROM settings"):
            try:
                value = json.loads(value)
            except Exception:
                pass
            # 嵌套 dict（如 llm）深合并：存了不完整子项时，缺的键回退默认值
            if isinstance(value, dict) and isinstance(d.get(key), dict):
                merged = dict(d[key])
                merged.update(value)
                d[key] = merged
            else:
                d[key] = value
        return d

    def set_setting(self, key, value):
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            self._conn.commit()

    def set_settings(self, d):
        for k, v in d.items():
            self.set_setting(k, v)

    def get_setting_raw(self, key):
        """读原始 settings 表，区分「键不存在」与「值为空串」；不存在返回 None。"""
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return row[0]

    # ---- 生命状态（它的「心」：心情 + 心跳时间，持久化，重启不丢）----
    def get_life_state(self):
        d = self.get_settings().get("life_state")
        if not isinstance(d, dict):
            d = {}
        return {
            "mood": d.get("mood", ""),
            "next_wake_at": d.get("next_wake_at", 0.0),
            "last_wake_at": d.get("last_wake_at", 0.0),
        }

    def set_life_state(self, d):
        self.set_setting("life_state", d)

    # ---- 长期记忆 ----
    def add_memory(self, kind, subject, content, platform="", importance=5, scope="global"):
        with self._lock:
            # 去重：同 kind+subject+scope 下，与已有记忆太相似就跳过
            for m in self.get_memories(kind=kind, subject=subject, scope=scope, limit=50):
                if _similar(m["content"], content):
                    return None
            cur = self._conn.execute(
                "INSERT INTO memories(kind, subject, content, platform, created_at, importance, scope) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (kind, subject, content, platform, _now(), int(importance or 5), scope),
            )
            self._conn.commit()
            self._prune()
            return cur.lastrowid

    def _prune(self):
        """上限淘汰：删掉「重要性×最近访问衰减」综合价值最低的（不持有锁，由 add_memory 调用）。"""
        cnt = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if cnt <= MAX_MEMORIES:
            return
        now = time.time()
        rows = self._conn.execute("SELECT id, importance, last_access FROM memories").fetchall()

        def value(row):
            _, imp, la = row
            imp_n = (int(imp or 5) or 5) / 10.0
            la = float(la or 0)
            if la <= 0:
                return imp_n * 0.1  # 从没被召回，几乎垫底
            age = max(0.0, now - la)
            return imp_n * (2 ** (-age / (7 * 24 * 3600)))  # 7 天半衰

        rows.sort(key=value)
        for r in rows[:cnt - MAX_MEMORIES]:
            self._conn.execute("DELETE FROM memories WHERE id = ?", (r[0],))

    def get_memories(self, kind=None, subject=None, platform=None, limit=100, active_only=False, scope=None):
        q = "SELECT id, kind, subject, content, platform, created_at FROM memories"
        cond, args = [], []
        if kind:
            cond.append("kind = ?"); args.append(kind)
        if subject:
            cond.append("subject = ?"); args.append(subject)
        if platform:
            cond.append("platform = ?"); args.append(platform)
        if scope:
            cond.append("scope = ?"); args.append(scope)
        if active_only:
            cond.append("absorbed = 0")
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
        cols = ("id", "kind", "subject", "content", "platform", "created_at")
        return [dict(zip(cols, r)) for r in self._conn.execute(q, args)]

    def retrieve(self, query, k=8, candidate_limit=50, context_scope=None):
        """三维召回 + scope 软偏好（不硬过滤），取 top-k 活跃记忆。"""
        with self._lock:
            now = time.time()
            rows = self._conn.execute(
                "SELECT id, kind, subject, content, importance, last_access, created_at, scope "
                "FROM memories WHERE absorbed = 0 ORDER BY id DESC LIMIT ?",
                (candidate_limit,),
            ).fetchall()
            if not rows:
                return []
            scored = []
            for rid, kind, subject, content, importance, last_access, created_at, scope in rows:
                try:
                    ts = time.mktime(time.strptime(created_at, "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    ts = now
                age = max(0.0, now - ts)
                recency = 2 ** (-age / (7 * 24 * 3600))  # 7 天半衰
                relevance = min(1.0, _overlap(query, content) + 0.3 * _overlap(query, subject))
                imp = (int(importance or 5) or 5) / 10.0
                # scope 软偏好：眼前这个人的 +1，主人/通用 0，别人的 -0.5（不挡在门外）
                if context_scope and scope == context_scope:
                    affinity = 1.0
                elif scope in ("owner", "global", ""):
                    affinity = 0.0
                else:
                    affinity = -0.5
                score = 0.5 * recency + 3.0 * relevance + 2.0 * imp + 1.0 * affinity
                scored.append((score, rid, kind, subject, content, scope))
            scored.sort(key=lambda x: -x[0])
            top = scored[:k]
            if top:
                self._conn.execute(
                    f"UPDATE memories SET last_access = ? WHERE id IN ({','.join('?' * len(top))})",
                    (now, *[t[1] for t in top]),
                )
                self._conn.commit()
            return [{"id": t[1], "kind": t[2], "subject": t[3], "content": t[4], "scope": t[5]} for t in top]

    def relevance(self, query, candidate_limit=50):
        """返回 query 与记忆池最相关一条的 overlap 分（0~1），用于判断「这个话题我熟不熟」。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT content, subject FROM memories WHERE absorbed = 0 ORDER BY id DESC LIMIT ?",
                (candidate_limit,),
            ).fetchall()
            best = 0.0
            for content, subject in rows:
                r = _overlap(query, content) + 0.3 * _overlap(query, subject)
                if r > best:
                    best = r
            return min(1.0, best)

    def mark_absorbed(self, memory_ids, by_id):
        with self._lock:
            for mid in memory_ids:
                self._conn.execute(
                    "UPDATE memories SET absorbed = 1, absorbed_by = ? WHERE id = ?", (by_id, mid)
                )
            self._conn.commit()

    def delete_memory(self, mid):
        with self._lock:
            self._conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
            self._conn.commit()

    def clear_memories(self):
        with self._lock:
            self._conn.execute("DELETE FROM memories")
            self._conn.commit()

    # ---- 活动 + 聊天日志 ----
    def add_log(self, level, category, message):
        with self._lock:
            self._conn.execute(
                "INSERT INTO logs(ts, level, category, message) VALUES(?, ?, ?, ?)",
                (_now(), level, category, message),
            )
            self._conn.commit()

    def get_logs(self, category=None, level=None, limit=100):
        q = "SELECT id, ts, level, category, message FROM logs"
        cond, args = [], []
        if category:
            cond.append("category = ?"); args.append(category)
        if level:
            cond.append("level = ?"); args.append(level)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
        cols = ("id", "ts", "level", "category", "message")
        return [dict(zip(cols, r)) for r in self._conn.execute(q, args)]

    # ---- 短期上下文 ----
    def add_short_term(self, session, role, content, platform=""):
        with self._lock:
            self._conn.execute(
                "INSERT INTO short_term(session, role, content, platform, ts) VALUES(?, ?, ?, ?, ?)",
                (session, role, content, platform, _now()),
            )
            self._conn.commit()

    def get_short_term(self, session, limit=20):
        rows = self._conn.execute(
            "SELECT role, content FROM short_term WHERE session = ? ORDER BY id DESC LIMIT ?",
            (session, limit),
        ).fetchall()
        return [dict(zip(("role", "content"), r)) for r in rows][::-1]

    def clear_short_term(self):
        with self._lock:
            self._conn.execute("DELETE FROM short_term")
            self._conn.commit()

    # ---- token 用量（累计，存 settings["usage"]） ----
    def add_usage(self, prompt_tokens, completion_tokens):
        d = self.get_usage()
        d["prompt_tokens"] += int(prompt_tokens or 0)
        d["completion_tokens"] += int(completion_tokens or 0)
        d["total_tokens"] += int(prompt_tokens or 0) + int(completion_tokens or 0)
        d["calls"] += 1
        self.set_setting("usage", d)

    def get_usage(self):
        d = self.get_settings().get("usage")
        if not isinstance(d, dict):
            d = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        return d

    # ---- 审批（持久化） ----
    def add_approval(self, kind, summary):
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO approvals(kind, summary, status, created_at) VALUES(?, ?, 'pending', ?)",
                (kind, summary, _now()),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_pending_approvals(self):
        cols = ("id", "kind", "summary", "status", "created_at")
        return [dict(zip(cols, r)) for r in self._conn.execute(
            "SELECT id, kind, summary, status, created_at FROM approvals "
            "WHERE status = 'pending' ORDER BY id DESC")]

    def set_approval_status(self, aid, status):
        with self._lock:
            cur = self._conn.execute(
                "UPDATE approvals SET status = ? WHERE id = ? AND status = 'pending'",
                (status, aid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_approval(self, aid):
        cols = ("id", "kind", "summary", "status", "created_at")
        rows = [dict(zip(cols, r)) for r in self._conn.execute(
            "SELECT id, kind, summary, status, created_at FROM approvals WHERE id = ?", (aid,))]
        return rows[0] if rows else None

    def close(self):
        self._conn.close()
