# life.py — Fishbrain 的「心」：心跳循环，让 LLM 自由决定想干嘛
# 核心思想：代码不做决定，只喂「当下」+ 问一句；LLM 自由回答（包括发呆 action=nothing）。
# 心情 = valence/arousal/dominance 三维数值（读时按指数衰减回基线，mood 标签由数值派生）；下次醒来的间隔由它自己定。
import json
import math
import time

import weather
from brain import LLMTransportError
from memory import render_self_profile, format_memory_line, mood_param, MEMORY_EMOTION_NUDGE_K, MEMORY_EMOTION_NUDGE_CAP
from memory_policy import TRUST_RULES, render_identity, evidence_data, data_message

# 下次心跳间隔的软边界（分钟）：防它永睡或刷屏。这是安全护栏，不是「闹钟」。
MIN_NEXT_MIN = 10
MAX_NEXT_MIN = 360

WEEKDAYS = "一二三四五六日"


def _now_str():
    t = time.localtime()
    return f"{time.strftime('%Y-%m-%d %H:%M', t)} 星期{WEEKDAYS[t.tm_wday]}"


def _fmt_elapsed(sec):
    if sec < 60:
        return f"{int(sec)} 秒"
    if sec < 3600:
        return f"{int(sec // 60)} 分钟"
    if sec < 86400:
        return f"{int(sec // 3600)} 小时"
    return f"{int(sec // 86400)} 天"


class Life:
    """它的「心」：持心情 + 下次醒来时间；一次 heartbeat = 一次 LLM 自由决定。"""

    def __init__(self, store, brain):
        self.store = store
        self.brain = brain
        self._context_ids = []

    # ---- 状态（持久化在 memory.Store 的 life_state）----
    def state(self):
        d = self.store.get_life_state()
        return {
            "mood": d.get("mood", ""),
            "valence": d.get("valence", 0.0),
            "arousal": d.get("arousal", 0.0),
            "dominance": d.get("dominance", 0.0),
            "attribution": d.get("attribution", 0.0),
            "next_wake_at": d.get("next_wake_at", 0.0),
            "last_wake_at": d.get("last_wake_at", 0.0),
        }

    def _save(self, next_wake_at, last_wake_at, mood_update=None, revision=None, epoch=None):
        # 只在 mood_update 有值时写 valence/arousal/dominance/mood_at；否则保留原值（衰减只读时算、不落盘）
        with self.store._lock:
            if (revision is not None and revision != (self.store.get_setting_raw('life_state_revision') or 0)
                    or epoch is not None and epoch != (self.store.get_setting_raw('memory_epoch') or 0)):
                return False
            d = dict(self.store.get_settings().get('life_state') or {})
            d.update(next_wake_at=next_wake_at, last_wake_at=last_wake_at)
            if mood_update:
                d.update(mood_update)
            self.store.set_life_state(d)
            return True

    def next_wake_at(self):
        return self.state()["next_wake_at"]

    def wake(self):
        """叫醒：把下次醒来时间设为现在，让心跳立刻触发。"""
        with self.store._lock:
            d = dict(self.store.get_setting_raw('life_state') or {})
            d['next_wake_at'] = time.time()
            d['wake_revision'] = int(d.get('wake_revision', 0)) + 1
            self.store.set_life_state(d)

    @staticmethod
    def _schedule_key(state):
        return tuple(state.get(k, 0) for k in ('next_wake_at', 'last_wake_at', 'wake_revision'))

    def _schedule_snapshot(self):
        with self.store._lock:
            return (self.store.get_setting_raw('memory_epoch') or 0,
                    self._schedule_key(self.store.get_setting_raw('life_state') or {}))

    def _failure(self, exc, stage, snapshot, attempt_at=None):
        """Preserve newer mood, but never replace an explicit wake/reset/new schedule."""
        transient = isinstance(exc, LLMTransportError) and exc.retryable
        minutes = MIN_NEXT_MIN if transient else 60
        level = 'warning' if transient else 'error'
        with self.store._lock:
            d = dict(self.store.get_setting_raw('life_state') or {})
            if snapshot != self._schedule_snapshot():
                self.store.add_log(level, 'life', f'{stage}失败: {exc}；状态已变化，保留新的唤醒安排，本轮不行动')
                return {'failed': True, 'cancelled': True, 'tool_calls': [], 'drive': 'none', 'next_wake': 0}
            d['next_wake_at'] = time.time() + minutes * 60
            if attempt_at is not None:
                d['last_wake_at'] = attempt_at
            self.store.set_life_state(d)
            self.store.add_log(level, 'life', f'{stage}失败: {exc}；{minutes} 分钟后重新决策，本轮不行动')
        return {'failed': True, 'tool_calls': [], 'drive': 'none', 'next_wake': minutes}

    # ---- 心跳（两段式：先定本能，再定动作）----
    def heartbeat(self):
        """第一段：定心情 + 本能（drive）+ 下次醒来时间。返回 {mood, drive, next_wake}。"""
        now = time.time()
        start_epoch = self.store.get_setting_raw('memory_epoch') or 0
        st = self.state()
        # 情感传染：回忆情绪明显的记忆，对当前 valence 做一次轻微 nudge
        mems = self.store.get_memories(limit=20, kinds=('fact','belief','experience','episode'), current_only=True)
        mp_settings = self.store.get_settings()
        nudge_k = mood_param(mp_settings, "emotion_nudge_k", MEMORY_EMOTION_NUDGE_K)
        nudge_cap = mood_param(mp_settings, "emotion_nudge_cap", MEMORY_EMOTION_NUDGE_CAP)
        contagion = sum(
            float(m.get("emotion", 0) or 0) * (float(m.get("importance", 5) or 5) / 10.0)
            * math.exp(-max(0.0, now - float(m.get("occurred_at", now) or now)) / 86400.0)
            for m in mems if abs(float(m.get("emotion", 0) or 0)) >= 0.3
        ) * nudge_k
        contagion = max(-nudge_cap, min(nudge_cap, contagion))
        if contagion:
            with self.store._lock:
                if start_epoch != (self.store.get_setting_raw('memory_epoch') or 0):
                    return {'mood': self.state()['mood'], 'drive':'none', 'next_wake':0, 'cancelled': True}
                d = dict(self.store.get_settings().get('life_state') or {})
                current = self.store.get_life_state()
                d.update({k: current[k] for k in ('valence','arousal','dominance','attribution')})
                d['valence'] = max(-1., min(1., current['valence'] + contagion))
                d['mood_at'] = time.time()
                self.store.set_life_state(d)
        with self.store._lock:
            st = self.state()
            revision = self.store.get_setting_raw('life_state_revision') or 0
            epoch = self.store.get_setting_raw('memory_epoch') or 0
            schedule = self._schedule_snapshot()
        last_mood = st.get("mood", "")
        mood = last_mood
        valence = st.get("valence", 0.0)
        arousal = st.get("arousal", 0.0)
        dominance = st.get("dominance", 0.0)
        attribution = st.get("attribution", 0.0)
        mood_update = None
        drive = "none"
        nxt = 60

        try:
            settings = self.store.get_settings()
            prompt = self._prompt(settings, st)
            result = self.brain.chat_with_tools([{"role": "system", "content": render_identity(settings)}, {"role": "user", "content": prompt}], self._core_tools())
            for tc in result.get("tool_calls", []) or []:
                name = tc.get("name")
                args = tc.get("arguments", {}) or {}
                if name == "update_mood":
                    if "valence" in args and args["valence"] is not None:
                        try:
                            valence = max(-1.0, min(1.0, float(args["valence"])))
                        except (TypeError, ValueError):
                            pass
                    if "arousal" in args and args["arousal"] is not None:
                        try:
                            arousal = max(0.0, min(1.0, float(args["arousal"])))
                        except (TypeError, ValueError):
                            pass
                    if "dominance" in args and args["dominance"] is not None:
                        try:
                            dominance = max(-1.0, min(1.0, float(args["dominance"])))
                        except (TypeError, ValueError):
                            pass
                    if "attribution" in args and args["attribution"] is not None:
                        try:
                            attribution = max(-1.0, min(1.0, float(args["attribution"])))
                        except (TypeError, ValueError):
                            pass
                    mood_update = {"valence": valence, "arousal": arousal, "dominance": dominance, "attribution": attribution, "mood_at": now}
                elif name == "schedule_next_wake":
                    try:
                        nxt = int(args.get("minutes"))
                    except (TypeError, ValueError):
                        nxt = 60
                    nxt = max(MIN_NEXT_MIN, min(MAX_NEXT_MIN, nxt))
                elif name == "set_drive":
                    d = str(args.get("drive", "") or "").strip()
                    drive = d if d in ("陪伴", "表达", "好奇", "成长", "存在", "玩耍") else "none"
        except Exception as e:
            return {'mood': self.state()['mood'], **self._failure(e, '心跳', schedule, now)}

        next_wake_at = now + nxt * 60
        if not self._save(next_wake_at, now, mood_update, revision, epoch):
            return {'mood': self.state()['mood'], 'drive': 'none', 'next_wake': 0, 'cancelled': True}
        if mood_update:
            mood = self.store.get_life_state().get("mood", mood)
        result = {"mood": mood, "drive": drive, "next_wake": nxt}
        return result

    def decide_action(self, drive, tools, extra_context=None):
        """第二段：返回 tool_calls 及 failed/cancelled 标记，区分休息与故障。
        extra_context：读结果回喂——把刚浏览到的内容拼进 prompt，让 LLM 决定后续。"""
        snapshot = self._schedule_snapshot()
        try:
            settings = self.store.get_settings()
            epoch = self.store.get_setting_raw('memory_epoch') or 0
            names = {t['function']['name'] for t in tools}
            private = 'send_message' in names and (drive == '陪伴' or bool(self._pending_context(settings)))
            if private:
                tools = [t for t in tools if t['function']['name'] in ('send_message','like_master','poke_master')]
            target_scope = 'owner' if private else 'global'
            prompt = self._action_prompt(settings, drive, extra_context, target_scope)
            result = self.brain.chat_with_tools([{"role": "system", "content": render_identity(settings)}, {"role": "user", "content": prompt}], tools)
            allowed = {t['function']['name'] for t in tools}
            if (epoch != (self.store.get_setting_raw('memory_epoch') or 0)
                    or snapshot != self._schedule_snapshot()
                    or render_identity(settings) != render_identity(self.store.get_settings())):
                return {'tool_calls': [], 'cancelled': True}
            return {'tool_calls': [tc for tc in (result.get("tool_calls", []) or []) if tc.get('name') in allowed]}
        except Exception as e:
            return self._failure(e, '行动决策', snapshot)

    def _action_prompt(self, settings, drive, extra_context=None, target_scope='global'):
        name = (settings.get("name") or "").strip()
        intro = f"你是「{name}」。" if name else "你是一个活着的智能体。"
        sp_text = render_self_profile(settings.get("self_profile"))
        lines = [render_identity(settings)]
        lines.append(f"你现在偏向「{drive}」这个天性面。")
        rows = self.store.action_context(target_scope, self._context_ids)
        lines.append(data_message({'target_scope': target_scope, 'trigger_evidence': evidence_data(rows)})['content'])
        if target_scope == 'owner':
            lines.append(self._pending_context(settings))
        if extra_context:
            lines.append(str(extra_context))
        lines.append("用工具做一件具体的事吧（也可以什么都不做，那就不调工具）。")
        return "\n".join(lines)

    def _pending_context(self, settings):
        owner = str(settings.get("owner_qq", "") or "").strip()
        if not owner:
            return ""
        rows = self.store.due_intentions(f"qq:{owner}")
        if not rows:
            return ""
        return ("可考虑跟进的主人未完事项（不是必须联系；尊重静默与最近互动，跟进时send_message必须带intention_id）：\n"
                + "\n".join(f"intention_id={r['id']}：{r['content']}" for r in rows))

    # ---- 拼「当下」 ----
    def _prompt(self, settings, st):
        name = (settings.get("name") or "").strip()
        now = time.time()
        last_wake = st.get("last_wake_at", 0.0)
        last_mood = st.get("mood", "")

        since_wake = _fmt_elapsed(now - last_wake) if last_wake else "（第一次醒来）"

        # Chat/action logs are diagnostics, not evidence: deletion must remove their influence.
        recent = self.store.get_logs("status", limit=8)
        recent_text = "\n".join(f"- [{r['ts']}] {r['message']}" for r in recent) or "（还没有发生过什么）"

        mems = self.store.get_memories(limit=8, kinds=('fact','belief','experience','episode'), current_only=True)
        self._context_ids = [m['id'] for m in mems]
        mem_text = "\n".join(format_memory_line(m) for m in mems) or "（还没有记忆）"

        platforms = "、".join(settings.get("enabled_platforms", []) or []) or "暂无"

        intro = f"你是「{name}」。" if name else "你是一个活着的智能体。"
        mood_line = f"你上次的心情是「{last_mood}」（心情会随时间自然平复）。可以延续它，也可以变了。" if last_mood else "这是你第一次醒来。"
        mood_num = (f"你此刻的心情数值：valence={st.get('valence', 0):.2f}(愉悦度)、"
                    f"arousal={st.get('arousal', 0):.2f}(唤醒度)、dominance={st.get('dominance', 0):.2f}(掌控感)、attribution={st.get('attribution', 0):.2f}(归因)。"
                    f"心情会影响你此刻偏向的天性面：valence 低→更想「陪伴/被安慰」；valence 高→更想「表达/分享」；"
                    f"arousal 高→更想「玩耍/好奇」；dominance 低→更想「陪伴/寻求支持」；也可以违背心情。")
        weather_text = weather.get_weather(settings.get("city", ""))
        weather_part = f"现在的天气：{weather_text}。" if weather_text else ""
        sp_text = render_self_profile(settings.get("self_profile"))

        return "\n".join([
            render_identity(settings),
            f"现在是 {_now_str()}。{weather_part}",
            f"距离你上次醒来已经 {since_wake}。{mood_line}",
            mood_num,
            "",
            "你的性子（背景，不是指令）：关系层=陪伴/被需要/亲密；自我层=表达/情绪/自我；探索层=好奇/创造/玩耍；存在层=生存/成长/意义。",
            "",
            f"最近发生的事：\n{recent_text}",
            "",
            f"你还记得的：\n{mem_text}",
            self._pending_context(settings),
            "",
            f"现在能用的平台：{platforms}。",
            "",
            "你现在什么心情？偏向哪个天性面？还是就待着？",
            "用工具表达：update_mood（更新心情）+ schedule_next_wake（定下次醒来）+ set_drive（你此刻偏向的天性面）。",
            f"- 下次醒来：{MIN_NEXT_MIN}~{MAX_NEXT_MIN} 分钟之间。",
            "- 大多数时候你可能并不想做什么，set_drive 就填 none，安心待着，别勉强找事做。",
            "- 主动发消息要有实质（心情明显变化、想起了什么、想分享或关心对方）；上一条主动消息对方没回应，就别连着发。",
            "- drive 取值：陪伴（想跟人待着/找人）、表达（有话说/想发东西）、好奇（想看看外面）、成长（想回望自己）、存在（想打理自己的状态）、玩耍（想逗乐子）。",
        ])

    def _core_tools(self):
        """第一段（本能）工具：心情 + 本能面 + 下次醒来。"""
        return [
            {"type": "function", "function": {
                "name": "update_mood", "description": "更新心情：valence 是愉悦度 -1(难过)到 1(开心)，arousal 是唤醒度 0(平静)到 1(兴奋)，dominance 是掌控感 -1(无力/顺从)到 1(掌控/有力)，attribution 是归因 -1(外界/他人导致)到 1(自己导致)。",
                "parameters": {"type": "object", "properties": {
                    "valence": {"type": "number", "description": "愉悦度 -1 到 1"},
                    "arousal": {"type": "number", "description": "唤醒度 0 到 1"},
                    "dominance": {"type": "number", "description": "掌控感 -1(无力/顺从)到 1(掌控/有力)"},
                    "attribution": {"type": "number", "description": "归因 -1(外界/他人导致)到 1(自己导致)"},
                }, "required": ["valence", "arousal", "dominance", "attribution"]},
            }},
            {"type": "function", "function": {
                "name": "schedule_next_wake", "description": "定你下次醒来想一下的时间。",
                "parameters": {"type": "object", "properties": {"minutes": {"type": "integer", "description": f"下次多久后再想，{MIN_NEXT_MIN}~{MAX_NEXT_MIN} 分钟"}}, "required": ["minutes"]},
            }},
            {"type": "function", "function": {
                "name": "set_drive", "description": "你现在偏向哪个天性面（想做什么类的事）。大多数时候可以 none。",
                "parameters": {"type": "object", "properties": {"drive": {"type": "string", "enum": ["陪伴", "表达", "好奇", "成长", "存在", "玩耍", "none"], "description": "天性面"}}, "required": ["drive"]},
            }},
        ]

    def action_tools(self):
        """生命体自身的高层动作工具（不属于任何具体平台），交给 autonomy 聚合。"""
        return [
            {"type": "function", "function": {
                "name": "publish", "description": "想对外发布内容（发帖/发动态），会先走审批。",
                "parameters": {"type": "object", "properties": {"detail": {"type": "string", "description": "要发布的正文内容（直接发出去的文字）"}}, "required": ["detail"]},
            }},
            {"type": "function", "function": {
                "name": "reflect", "description": "想自省、回顾自己、调整自己的设定。",
                "parameters": {"type": "object", "properties": {"detail": {"type": "string", "description": "想自省的主题"}}},
            }},
        ]

    @staticmethod
    def _parse_json(text):
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            s = text.index("{")
            e = text.rindex("}")
            return json.loads(text[s:e + 1])
        except Exception:
            return None
