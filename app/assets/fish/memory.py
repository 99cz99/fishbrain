# memory.py — Fishbrain 的记忆与设定存储（SQLite，本地，永久保留）
# 表：
#   settings   软设定（名字/性格/频率/底线开关，app 可改）
#   memories   长期记忆（记住用户、每个好友/群、自身经历）
#   logs       活动 + 聊天记录（监管 app 的「状态」和「聊天记录」都从这里读）
#   short_term 短期上下文（最近对话，让回复接得上）
import json
import math
import os
import re
import sqlite3
import threading
import time

# 软设定默认值（代码只给中性占位，用户在 app 里改）
DEFAULT_SETTINGS = {
    "name": "",                       # 名字（用户自设，默认空）
    "owner_qq": "",                   # 主人 QQ 号（主动冒泡发给谁）
    "city": "",                       # 所在城市（中文名，天气感知用；空=不启用）
    "temperament": {                  # 气质：心情衰减的基线 + 平复快慢（稳定层，心情在其上波动）
        "valence": 0.0,               # 情绪基调 -1(偏阴郁) ~ 1(偏阳光)
        "arousal": 0.0,               # 唤醒基调 0(沉静) ~ 1(活跃)
        "tau_hours": 4.0,             # 平复快慢：越小平复越快
    },
    "self_profile": {                 # 自我画像：结构化非情绪身份（价值观/风格/喜好/边界）
        "values": [],                 # 价值观/立场，≤4 条
        "style": [],                  # 说话风格/口头禅，≤3 条
        "likes": [],                  # 喜好/兴趣，≤4 条
        "boundaries": [],             # 边界/原则，≤3 条
    },
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
MAX_STICKERS = 200              # 表情包库上限（超了删最旧的 + 删文件）

# 心情衰减：valence/arousal 读时按指数回基线，防心情卡死/螺旋
MOOD_BASELINE_VALENCE = 0.0
MOOD_BASELINE_AROUSAL = 0.0
MOOD_TAU_HOURS = 4.0


def decay_mood(valence, arousal, mood_at, now,
               baseline_valence=MOOD_BASELINE_VALENCE,
               baseline_arousal=MOOD_BASELINE_AROUSAL,
               tau_hours=MOOD_TAU_HOURS):
    """心情数值按指数衰减回基线（只读时算、不落盘）。返回 (valence, arousal)。"""
    if not mood_at:
        return valence, arousal
    dt = max(0.0, now - float(mood_at))
    if dt <= 0:
        return valence, arousal
    tau = max(0.5, min(72.0, float(tau_hours))) * 3600.0
    k = math.exp(-dt / tau)
    v = baseline_valence + (valence - baseline_valence) * k
    a = baseline_arousal + (arousal - baseline_arousal) * k
    return v, a


def render_self_profile(sp):
    """把自我画像拼成一行紧凑中文；全空返回空串。"""
    if not isinstance(sp, dict):
        return ""
    labels = (("values", "价值观"), ("style", "说话风格"), ("likes", "喜好"), ("boundaries", "边界"))
    parts = []
    for key, label in labels:
        items = sp.get(key) or []
        if items:
            parts.append(f"{label}：{'、'.join(str(x) for x in items)}")
    if not parts:
        return ""
    return "你对自己的认识（逐渐形成）：" + "；".join(parts) + "。"


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
CREATE TABLE IF NOT EXISTS stickers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tag TEXT NOT NULL,        -- 表情包标签（受控情绪词）
  path TEXT NOT NULL,       -- 本地文件路径
  sha256 TEXT NOT NULL,     -- 内容哈希（去重）
  pinned INTEGER NOT NULL DEFAULT 0,  -- 固定：1=不淘汰
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
        # stickers 补 pinned 列（老库兼容）
        scols = [r[1] for r in self._conn.execute("PRAGMA table_info(stickers)")]
        if scols and "pinned" not in scols:
            self._conn.execute("ALTER TABLE stickers ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")

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
        settings = self.get_settings()
        d = settings.get("life_state")
        if not isinstance(d, dict):
            d = {}
        temp = settings.get("temperament") or {}
        if not isinstance(temp, dict):
            temp = {}
        try:
            base_v = float(temp.get("valence", MOOD_BASELINE_VALENCE))
            base_a = float(temp.get("arousal", MOOD_BASELINE_AROUSAL))
            tau = float(temp.get("tau_hours", MOOD_TAU_HOURS))
        except (TypeError, ValueError):
            base_v, base_a, tau = MOOD_BASELINE_VALENCE, MOOD_BASELINE_AROUSAL, MOOD_TAU_HOURS
        try:
            valence = float(d.get("valence", base_v))
            arousal = float(d.get("arousal", base_a))
        except (TypeError, ValueError):
            valence, arousal = base_v, base_a
        valence, arousal = decay_mood(valence, arousal, d.get("mood_at", 0.0), time.time(), base_v, base_a, tau)
        mood = d.get("mood", "")
        if abs(valence - base_v) < 0.15 and abs(arousal - base_a) < 0.3:
            mood = "如常"
        return {
            "mood": mood,
            "valence": round(valence, 3),
            "arousal": round(arousal, 3),
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
        """三维召回（recency+relevance+importance），按 context_scope 硬过滤隐私：
        owner/None → 全量；group:{id} → 本群+global；private:{id} → 本人+global。"""
        with self._lock:
            now = time.time()
            cond = "absorbed = 0"
            args = []
            if context_scope and context_scope.startswith(("group:", "private:")):
                cond += " AND scope IN (?, 'global', '')"
                args.append(context_scope)
            rows = self._conn.execute(
                f"SELECT id, kind, subject, content, importance, last_access, created_at, scope "
                f"FROM memories WHERE {cond} ORDER BY id DESC LIMIT ?",
                (*args, candidate_limit),
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
                score = 0.5 * recency + 3.0 * relevance + 2.0 * imp
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

    def get_short_term(self, scope=None, limit=20):
        """短期上下文：按 context_scope 隐私层级取最近 N 条。
        owner/None → 全渠道；group:{id} → 本群；private:{id} → 本人。"""
        with self._lock:
            if scope and scope != "owner":
                session = scope.replace(":", "_", 1)
                rows = self._conn.execute(
                    "SELECT role, content FROM short_term WHERE session = ? ORDER BY id DESC LIMIT ?",
                    (session, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT role, content FROM short_term ORDER BY id DESC LIMIT ?",
                    (limit,),
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

    # ---- 表情包库 ----
    def add_sticker(self, tag, path, sha256):
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO stickers(tag, path, sha256, created_at) VALUES(?, ?, ?, ?)",
                (tag or "", path, sha256, _now()),
            )
            # FIFO 淘汰：超上限删最旧（连同文件，防孤儿）
            cnt = self._conn.execute("SELECT COUNT(*) FROM stickers").fetchone()[0]
            if cnt > MAX_STICKERS:
                for row in self._conn.execute(
                    "SELECT id, path FROM stickers WHERE pinned = 0 ORDER BY id ASC LIMIT ?", (cnt - MAX_STICKERS,)):
                    try:
                        os.remove(row[1])
                    except Exception:
                        pass
                    self._conn.execute("DELETE FROM stickers WHERE id = ?", (row[0],))
            self._conn.commit()
            return cur.lastrowid

    def has_sticker(self, sha256):
        return self._conn.execute("SELECT 1 FROM stickers WHERE sha256 = ?", (sha256,)).fetchone() is not None

    def get_stickers(self, limit=50):
        cols = ("id", "tag", "path", "sha256", "pinned", "created_at")
        return [dict(zip(cols, r)) for r in self._conn.execute(
            "SELECT id, tag, path, sha256, pinned, created_at FROM stickers ORDER BY pinned DESC, id DESC LIMIT ?", (limit,))]

    def get_sticker(self, sid):
        cols = ("id", "tag", "path", "sha256", "pinned", "created_at")
        rows = [dict(zip(cols, r)) for r in self._conn.execute(
            "SELECT id, tag, path, sha256, pinned, created_at FROM stickers WHERE id = ?", (sid,))]
        return rows[0] if rows else None

    def update_sticker(self, sid, tag=None, pinned=None):
        """部分更新：只改提供的字段（tag=None 表示不改，pinned=None 表示不改）。"""
        with self._lock:
            cur = self.get_sticker(sid)
            if not cur:
                return False
            new_tag = cur["tag"] if tag is None else (tag or "")
            new_pinned = cur["pinned"] if pinned is None else (1 if pinned else 0)
            self._conn.execute(
                "UPDATE stickers SET tag = ?, pinned = ? WHERE id = ?", (new_tag, new_pinned, sid))
            self._conn.commit()
            return True

    def delete_sticker(self, sid):
        """删行 + 删文件。返回是否删到。"""
        with self._lock:
            cur = self.get_sticker(sid)
            if not cur:
                return False
            try:
                os.remove(cur["path"])
            except Exception:
                pass
            self._conn.execute("DELETE FROM stickers WHERE id = ?", (sid,))
            self._conn.commit()
            return True

    def get_sticker_by_tag(self, tag):
        """双向子串匹配（标签含 X 或 X 含标签），返回最近一条的本地路径；无则 None。"""
        tag = (tag or "").strip()
        if not tag:
            return None
        for path, t in self._conn.execute(
            "SELECT path, tag FROM stickers ORDER BY id DESC LIMIT 200").fetchall():
            t = t or ""
            if tag in t or t in tag:
                return path
        return None

    def close(self):
        self._conn.close()
