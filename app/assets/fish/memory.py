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

from memory_system import MemorySystem
from qq_delivery import DeliveryStore
from memory_policy import TRUST_RULES, person_code, redact_secrets, CONTROL_KEYS

# 软设定默认值（代码只给中性占位，用户在 app 里改）
DEFAULT_SETTINGS = {
    "name": "",                       # 名字（用户自设，默认空）
    "owner_qq": "",                   # 主人 QQ 号（主动冒泡发给谁）
    "qq_quick_login_uin": "",         # 鱼脑自己登录的 QQ 号（-q 快捷登录自愈用；_on_meta 登录时自动填）
    "qq_device_guid": "",             # QQ 设备指纹 GUID 基准（掉线自愈/防「新设备」用，Part B 写入）
    "city": "",                       # 所在城市（中文名，天气感知用；空=不启用）
    "temperament": {                  # 气质：心情衰减的基线 + 平复快慢（稳定层，心情在其上波动）
        "valence": 0.0,               # 情绪基调 -1(偏阴郁) ~ 1(偏阳光)
        "arousal": 0.0,               # 唤醒基调 0(沉静) ~ 1(活跃)
        "dominance": 0.0,             # 掌控感基调 -1(无力/顺从) ~ 1(掌控/有力)
        "attribution": 0.0,           # 归因基调 -1(偏外因) ~ 1(偏内因)
        "tau_hours": 4.0,             # 平复快慢：越小平复越快
    },
    "self_profile": {                 # 自我画像：结构化非情绪身份（价值观/风格/喜好/边界）
        "values": [],                 # 价值观/立场，≤4 条
        "style": [],                  # 说话风格/口头禅，≤3 条
        "likes": [],                  # 喜好/兴趣，≤4 条
        "boundaries": [],             # 边界/原则，≤3 条
    },
    "mood_params": {                  # 情绪算法超参（app「算法超参」页可改；缺键回退常量默认）
        "fast_ratio": 0.6,            # 双时间常数：快衰减占比
        "positive_tau_scale": 1.5,    # 正情绪 τ 缩放（越大越持久）
        "negative_tau_scale": 0.6,    # 负情绪 τ 缩放（越大越持久）
        "emotion_nudge_k": 0.05,      # 情感传染系数
        "emotion_nudge_cap": 0.1,     # 情感传染封顶
        "appraisal_nudge_k": 0.4,     # valence nudge 系数
        "appraisal_arousal_k": 0.3,   # arousal nudge 系数
        "appraisal_dominance_k": 0.4, # dominance nudge 系数
        "appraisal_attribution_k": 0.4,  # attribution nudge 系数
        "appraisal_min_intensity": 0.2,  # 低于此强度不 nudge
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

# 心情衰减：valence/arousal/dominance/attribution 读时按指数回基线，防心情卡死/螺旋
MOOD_BASELINE_VALENCE = 0.0
MOOD_BASELINE_AROUSAL = 0.0
MOOD_BASELINE_DOMINANCE = 0.0
MOOD_BASELINE_ATTRIBUTION = 0.0
MOOD_TAU_HOURS = 4.0

# 情绪衰减：双时间常数（快平复 + 慢残留）+ 不对称（正情绪慢、负情绪快）
MOOD_FAST_RATIO = 0.6          # 双时间常数：快衰减占比
MOOD_POSITIVE_TAU_SCALE = 1.5  # 正情绪衰减更慢（愉悦持续）
MOOD_NEGATIVE_TAU_SCALE = 0.6  # 负情绪衰减更快（快速恢复）

# 情感传染：心跳回忆情绪明显的记忆时，对当前 valence 的轻微 nudge
MEMORY_EMOTION_NUDGE_K = 0.05   # 每条 |emotion|≥0.3 记忆的 nudge 系数
MEMORY_EMOTION_NUDGE_CAP = 0.1  # 单次心跳情感传染的 valence 总幅度上限


def decay_mood(valence, arousal, dominance, attribution, mood_at, now,
               baseline_valence=MOOD_BASELINE_VALENCE,
               baseline_arousal=MOOD_BASELINE_AROUSAL,
               baseline_dominance=MOOD_BASELINE_DOMINANCE,
               baseline_attribution=MOOD_BASELINE_ATTRIBUTION,
               tau_hours=MOOD_TAU_HOURS,
               fast_ratio=MOOD_FAST_RATIO,
               positive_tau_scale=MOOD_POSITIVE_TAU_SCALE,
               negative_tau_scale=MOOD_NEGATIVE_TAU_SCALE):
    """心情数值按指数衰减回基线（只读时算、不落盘）。返回 (valence, arousal, dominance, attribution)。
    双时间常数（快平复 + 慢残留）；valence 走不对称 τ（正情绪慢、负情绪快）。"""
    if not mood_at:
        return valence, arousal, dominance, attribution
    dt = max(0.0, now - float(mood_at))
    if dt <= 0:
        return valence, arousal, dominance, attribution
    tau_fast = max(0.5, min(72.0, float(tau_hours))) * 3600.0
    tau_slow = tau_fast * 3.0
    k = fast_ratio * math.exp(-dt / tau_fast) + (1.0 - fast_ratio) * math.exp(-dt / tau_slow)
    v_scale = positive_tau_scale if valence >= baseline_valence else negative_tau_scale
    v_tau_fast = tau_fast * v_scale
    v_tau_slow = v_tau_fast * 3.0
    kv = fast_ratio * math.exp(-dt / v_tau_fast) + (1.0 - fast_ratio) * math.exp(-dt / v_tau_slow)
    v = baseline_valence + (valence - baseline_valence) * kv
    a = baseline_arousal + (arousal - baseline_arousal) * k
    d = baseline_dominance + (dominance - baseline_dominance) * k
    t = baseline_attribution + (attribution - baseline_attribution) * k
    return v, a, d, t


def mood_label(valence, arousal, dominance, attribution,
               baseline_valence=MOOD_BASELINE_VALENCE,
               baseline_arousal=MOOD_BASELINE_AROUSAL,
               baseline_dominance=MOOD_BASELINE_DOMINANCE,
               baseline_attribution=MOOD_BASELINE_ATTRIBUTION):
    """由四维数值唯一推导心情标签（相对气质基线）。valence 中性一律「如常」；
    负情绪高唤醒区用 dominance+attribution 区分「烦躁(愤怒) / 内疚 / 害怕(恐惧)」。"""
    dv = valence - baseline_valence
    da = arousal - baseline_arousal
    dd = dominance - baseline_dominance
    dat = attribution - baseline_attribution
    if abs(dv) < 0.15:
        return "如常"
    if dv > 0.4:
        return "开心"
    if dv > 0.15:
        return "不错"
    # dv < -0.15（负情绪）
    if da > 0.3:
        if dd > 0.15:
            return "烦躁"
        return "内疚" if dat > 0.15 else "害怕"
    if dv < -0.4:
        return "低落"
    return "有点闷"


def emotion_label(emotion):
    """记忆 emotion(-1~1) → 简短情绪词（召回标注用）；接近 0 返回空串。"""
    try:
        e = float(emotion or 0)
    except (TypeError, ValueError):
        return ""
    if abs(e) < 0.15:
        return ""
    if e > 0.4:
        return "开心"
    if e > 0.15:
        return "不错"
    if e < -0.4:
        return "低落"
    return "有点闷"


def format_memory_line(m, with_kind=False):
    """把一条记忆格式化成一行，情绪明显时带标注。"""
    kind = f"[{m.get('kind', '')}] " if with_kind else ""
    line = (f"- {kind}[{person_code(m.get('entity_id'))} 范围={m.get('scope', '未知')} "
            f"时间={m.get('created_at', '未知')} #{m.get('id', '?')}] "
            f"{m.get('subject', '')}: {redact_secrets(m.get('content', ''))}")
    emo = emotion_label(m.get("emotion", 0))
    if emo:
        line += f"（当时{emo}）"
    return line


def mood_param(settings, key, default):
    """读 settings['mood_params'][key]，缺失/非法回退 default。"""
    mp = settings.get("mood_params") or {}
    if not isinstance(mp, dict):
        return default
    try:
        return float(mp.get(key, default))
    except (TypeError, ValueError):
        return default


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
    """召回相似度：单字(1-gram) + 双字(2-gram) Jaccard + 子串，中文友好。"""
    q, c = _norm(query), _norm(content)
    if not q or not c:
        return 0.0
    q1, c1 = set(q), set(c)
    uni = len(q1 & c1) / len(q1 | c1)
    q2, c2 = _bigrams(q), _bigrams(c)
    bi = len(q2 & c2) / len(q2 | c2) if (q2 or c2) else 0.0
    sub = 1.0 if (q in c or c in q) else 0.0
    return min(1.0, 0.4 * uni + 0.3 * bi + 0.3 * sub)


class Store(MemorySystem, DeliveryStore):
    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(memories)")}
        if columns and str(db_path) != ':memory:' and not self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='qq_outbox'").fetchone():
            backup_path = str(db_path)+'.pre-qq-delivery-v1.db'
            if not os.path.exists(backup_path):
                backup = sqlite3.connect(backup_path)
                try:
                    self._conn.backup(backup)
                finally:
                    backup.close()
        approval_columns = {row[1] for row in self._conn.execute('PRAGMA table_info(approvals)')}
        if approval_columns and 'execution_state' not in approval_columns and str(db_path) != ':memory:':
            backup_path = str(db_path) + '.pre-link-v1.db'
            if not os.path.exists(backup_path):
                backup = sqlite3.connect(backup_path)
                try:
                    self._conn.backup(backup)
                finally:
                    backup.close()
        if columns and "status" not in columns and str(db_path) != ":memory:":
            backup_path = str(db_path) + ".pre-memory-v2.db"
            if not os.path.exists(backup_path):
                backup = sqlite3.connect(backup_path)
                try:
                    self._conn.backup(backup)
                finally:
                    backup.close()
        if columns and str(db_path) != ':memory:' and not self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='memory_terms'").fetchone():
            backup_path = str(db_path) + '.pre-memory-policy-v3.db'
            if not os.path.exists(backup_path):
                backup = sqlite3.connect(backup_path)
                try:
                    self._conn.backup(backup)
                finally:
                    backup.close()
        self._conn.executescript(SCHEMA)
        with self._conn:
            self._conn.execute("BEGIN")
            self._migrate()
        self._lock = threading.RLock()  # 可重入：add_memory/update_sticker 持锁时也会调 get_* 读方法
        self._apply_privacy_migration()
        self.init_delivery()

    def _apply_privacy_migration(self):
        from memory_policy import contains_secret
        with self._lock, self._conn:
            if self.get_setting_raw('memory_policy_version') == 3:
                return
            for mid, kind, content in self._conn.execute("SELECT id,kind,content FROM memories WHERE status!='deleted'").fetchall():
                cleaned = redact_secrets(content)
                if cleaned != content:
                    self._conn.execute('UPDATE memories SET content=? WHERE id=?', (cleaned, mid))
                    self._index_memory(mid, cleaned)
                    if kind in ('fact','belief','intention'):
                        self._invalidate_dependents([mid], 'quarantined')
            for table, column in (('short_term','content'), ('logs','message')):
                for rid, text in self._conn.execute(f'SELECT id,{column} FROM {table}').fetchall():
                    cleaned = redact_secrets(text)
                    if cleaned != text:
                        self._conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?', (cleaned, rid))
            self._conn.execute("INSERT OR REPLACE INTO settings VALUES('memory_policy_version','3')")

    def _migrate(self):
        acols = {r[1] for r in self._conn.execute('PRAGMA table_info(approvals)')}
        if 'execution_state' not in acols:
            self._conn.execute("ALTER TABLE approvals ADD COLUMN execution_state TEXT NOT NULL DEFAULT 'not_started'")
            self._conn.execute("UPDATE approvals SET execution_state='unconfirmed' WHERE status!='pending'")
        # A process restart cannot establish whether an in-flight external action completed.
        self._conn.execute("UPDATE approvals SET execution_state='unconfirmed' WHERE status='approved' AND execution_state IN ('running','not_started')")
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
        self._migrate_memory()
        # stickers 补 pinned 列（老库兼容）
        scols = [r[1] for r in self._conn.execute("PRAGMA table_info(stickers)")]
        if scols and "pinned" not in scols:
            self._conn.execute("ALTER TABLE stickers ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")

    # ---- 软设定 ----
    def get_settings(self):
        with self._lock:
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
            d['manual_self_profile'] = d.get('self_profile') or {}
            d['self_profile'] = self._effective_profile(d['manual_self_profile'])
            d['self_profile_revision'] = self.profile_revision({'profile': d['self_profile'], 'persona': d.get('persona', ''),
                                                               'manual': d['manual_self_profile'], 'vetoes': self.personality_vetoes()})
            return d

    def apply_manual_settings(self, updates):
        """Persist explicit user control and its lock atomically with respect to evolution."""
        with self._lock:
            locked = set(self.get_setting_raw('user_locked_settings') or [])
            locked.update(CONTROL_KEYS.intersection(updates))
            if locked:
                self.set_setting('user_locked_settings', sorted(locked))
            self.set_settings(updates)

    def apply_evolved_settings(self, updates, snapshot):
        with self._lock:
            current = self.get_settings()
            locked = set(current.get('user_locked_settings') or [])
            applied, skipped = {}, {}
            # Existing restrictions are protected even before the first editor save.
            for key, value in updates.items():
                if current.get(key) == value:
                    continue
                if (key in locked or current.get(key) != snapshot.get(key)
                        or key in ('approve_money', 'approve_publish', 'midnight_ok', 'enabled_platforms')):
                    skipped[key] = '用户控制或设置已变化'
                    continue
                if current.get(key) != value:
                    self.set_setting(key, value)
                    applied[key] = value
            return {'applied': applied, 'skipped': skipped}

    def set_setting(self, key, value):
        with self._lock:
            if key == 'life_state':
                revision = int(self.get_setting_raw('life_state_revision') or 0) + 1
                self._conn.execute("INSERT OR REPLACE INTO settings VALUES('life_state_revision',?)", (json.dumps(revision),))
            if key == 'self_profile':
                self._conn.execute("DELETE FROM personality_traits")
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
        with self._lock:
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
            base_d = float(temp.get("dominance", MOOD_BASELINE_DOMINANCE))
            base_t = float(temp.get("attribution", MOOD_BASELINE_ATTRIBUTION))
            tau = float(temp.get("tau_hours", MOOD_TAU_HOURS))
        except (TypeError, ValueError):
            base_v, base_a, base_d, base_t, tau = MOOD_BASELINE_VALENCE, MOOD_BASELINE_AROUSAL, MOOD_BASELINE_DOMINANCE, MOOD_BASELINE_ATTRIBUTION, MOOD_TAU_HOURS
        try:
            valence = float(d.get("valence", base_v))
            arousal = float(d.get("arousal", base_a))
            dominance = float(d.get("dominance", base_d))
            attribution = float(d.get("attribution", base_t))
        except (TypeError, ValueError):
            valence, arousal, dominance, attribution = base_v, base_a, base_d, base_t
        # 外部信号：天气 + 昼夜节律，偏移有效基线（decay 与 mood_label 都用）
        import weather
        city = settings.get("city", "")
        weather_offset = weather.get_weather_mood(city) if city else 0.0
        hour = time.localtime().tm_hour
        diurnal = 0.05 * math.sin((hour - 3) / 24 * 2 * math.pi)
        eff_v = base_v + weather_offset
        eff_a = base_a + diurnal
        fast_ratio = mood_param(settings, "fast_ratio", MOOD_FAST_RATIO)
        pos_scale = mood_param(settings, "positive_tau_scale", MOOD_POSITIVE_TAU_SCALE)
        neg_scale = mood_param(settings, "negative_tau_scale", MOOD_NEGATIVE_TAU_SCALE)
        valence, arousal, dominance, attribution = decay_mood(valence, arousal, dominance, attribution, d.get("mood_at", 0.0), time.time(), eff_v, eff_a, base_d, base_t, tau, fast_ratio, pos_scale, neg_scale)
        mood = mood_label(valence, arousal, dominance, attribution, eff_v, eff_a, base_d, base_t)
        return {
            "mood": mood,
            "valence": round(valence, 3),
            "arousal": round(arousal, 3),
            "dominance": round(dominance, 3),
            "attribution": round(attribution, 3),
            "next_wake_at": d.get("next_wake_at", 0.0),
            "last_wake_at": d.get("last_wake_at", 0.0),
        }

    def set_life_state(self, d):
        self.set_setting("life_state", d)

    def add_log(self, level, category, message):
        message = redact_secrets(message)
        with self._lock:
            self._conn.execute(
                "INSERT INTO logs(ts, level, category, message) VALUES(?, ?, ?, ?)",
                (_now(), level, category, message),
            )
            self._conn.commit()

    def get_logs(self, category=None, level=None, limit=100):
        with self._lock:
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
                (session, role, redact_secrets(content), platform, _now()),
            )
            self._conn.execute(
                "DELETE FROM short_term WHERE session=? AND id NOT IN "
                "(SELECT id FROM short_term WHERE session=? ORDER BY id DESC LIMIT 200)",
                (session, session),
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
        with self._lock:
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
        with self._lock:
            cols = ("id", "kind", "summary", "status", "created_at")
            return [dict(zip(cols, r)) for r in self._conn.execute(
                "SELECT id, kind, summary, status, created_at FROM approvals "
                "WHERE status = 'pending' ORDER BY id DESC")]

    def get_recent_approvals(self, limit=100):
        with self._lock:
            cols = ('id','kind','summary','status','created_at','execution_state')
            return [dict(zip(cols, r)) for r in self._conn.execute(
                "SELECT id,kind,summary,status,created_at,execution_state FROM approvals WHERE status='pending' OR id IN "
                "(SELECT id FROM approvals WHERE status!='pending' ORDER BY id DESC LIMIT ?) "
                "ORDER BY (status='pending') DESC,id DESC", (limit,))]

    def set_execution_state(self, aid, state):
        if state not in ('running','confirmed','unconfirmed','failed_or_partial','not_required'):
            raise ValueError('无效的执行状态')
        with self._lock, self._conn:
            self._conn.execute('UPDATE approvals SET execution_state=? WHERE id=?', (state, aid))

    def set_approval_status(self, aid, status):
        with self._lock:
            cur = self._conn.execute(
                "UPDATE approvals SET status = ? WHERE id = ? AND status = 'pending'",
                (status, aid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_approval(self, aid):
        with self._lock:
            cols = ("id", "kind", "summary", "status", "created_at")
            rows = [dict(zip(cols, r)) for r in self._conn.execute(
                "SELECT id, kind, summary, status, created_at FROM approvals WHERE id = ?", (aid,))]
            return rows[0] if rows else None

    def mark_approval_failed(self, aid):
        with self._lock:
            self._conn.execute("UPDATE approvals SET status = 'failed' WHERE id = ?", (aid,))
            self._conn.commit()

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
        with self._lock:
            return self._conn.execute("SELECT 1 FROM stickers WHERE sha256 = ?", (sha256,)).fetchone() is not None

    def get_stickers(self, limit=50):
        with self._lock:
            cols = ("id", "tag", "path", "sha256", "pinned", "created_at")
            return [dict(zip(cols, r)) for r in self._conn.execute(
                "SELECT id, tag, path, sha256, pinned, created_at FROM stickers ORDER BY pinned DESC, id DESC LIMIT ?", (limit,))]

    def get_sticker(self, sid):
        with self._lock:
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
        with self._lock:
            for path, t in self._conn.execute(
                "SELECT path, tag FROM stickers ORDER BY id DESC LIMIT 200").fetchall():
                t = t or ""
                if tag in t or t in tag:
                    return path
        return None

    def close(self):
        with self._lock:
            self._conn.close()
