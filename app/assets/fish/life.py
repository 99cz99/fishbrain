# life.py — Fishbrain 的「心」：心跳循环，让 LLM 自由决定想干嘛
# 核心思想：代码不做决定，只喂「当下」+ 问一句；LLM 自由回答（包括发呆 action=nothing）。
# 心情是一段会延续的文字，不是数字；下次醒来的间隔由它自己定（软边界只是护栏，不是闹钟）。
import json
import time

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

    # ---- 状态（持久化在 memory.Store 的 life_state）----
    def state(self):
        d = self.store.get_life_state()
        return {
            "mood": d.get("mood", ""),
            "next_wake_at": d.get("next_wake_at", 0.0),
            "last_wake_at": d.get("last_wake_at", 0.0),
        }

    def _save(self, mood, next_wake_at, last_wake_at):
        self.store.set_life_state({
            "mood": mood,
            "next_wake_at": next_wake_at,
            "last_wake_at": last_wake_at,
        })

    def next_wake_at(self):
        return self.state()["next_wake_at"]

    def wake(self):
        """叫醒：把下次醒来时间设为现在，让心跳立刻触发。"""
        st = self.state()
        self._save(st["mood"], time.time(), st["last_wake_at"])

    # ---- 心跳（两段式：先定本能，再定动作）----
    def heartbeat(self):
        """第一段：定心情 + 本能（drive）+ 下次醒来时间。返回 {mood, drive, next_wake}。"""
        now = time.time()
        st = self.state()
        last_mood = st.get("mood", "")
        mood = last_mood
        drive = "none"
        nxt = 60

        try:
            settings = self.store.get_settings()
            prompt = self._prompt(settings, st)
            result = self.brain.chat_with_tools([{"role": "user", "content": prompt}], self._core_tools())
            for tc in result.get("tool_calls", []) or []:
                name = tc.get("name")
                args = tc.get("arguments", {}) or {}
                if name == "update_mood":
                    mood = str(args.get("mood", "") or "").strip() or last_mood
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
            self.store.add_log("error", "life", f"心跳异常，兜底发呆: {e}")

        next_wake_at = now + nxt * 60
        self._save(mood, next_wake_at, now)
        return {"mood": mood, "drive": drive, "next_wake": nxt}

    def decide_action(self, drive, tools, extra_context=None):
        """第二段：按 drive 拿相关工具，让 LLM 决定具体动作。返回 tool_calls 列表。
        extra_context：读结果回喂——把刚浏览到的内容拼进 prompt，让 LLM 决定后续。"""
        try:
            settings = self.store.get_settings()
            prompt = self._action_prompt(settings, drive, extra_context)
            result = self.brain.chat_with_tools([{"role": "user", "content": prompt}], tools)
            return result.get("tool_calls", []) or []
        except Exception as e:
            self.store.add_log("error", "life", f"行动决策异常: {e}")
            return []

    def _action_prompt(self, settings, drive, extra_context=None):
        name = (settings.get("name") or "").strip()
        intro = f"你是「{name}」。" if name else "你是一个活着的智能体。"
        lines = [intro, f"你现在偏向「{drive}」这个天性面。"]
        if extra_context:
            lines.append(str(extra_context))
        lines.append("用工具做一件具体的事吧（也可以什么都不做，那就不调工具）。")
        return "\n".join(lines)

    # ---- 拼「当下」 ----
    def _prompt(self, settings, st):
        name = (settings.get("name") or "").strip()
        now = time.time()
        last_wake = st.get("last_wake_at", 0.0)
        last_mood = st.get("mood", "")

        since_wake = _fmt_elapsed(now - last_wake) if last_wake else "（第一次醒来）"

        recent = self.store.get_logs(limit=8)
        recent_text = "\n".join(f"- [{r['ts']}] {r['message']}" for r in recent) or "（还没有发生过什么）"

        mems = self.store.get_memories(limit=8)
        mem_text = "\n".join(f"- {m['subject']}: {m['content']}" for m in mems) or "（还没有记忆）"

        platforms = "、".join(settings.get("enabled_platforms", []) or []) or "暂无"

        intro = f"你是「{name}」。" if name else "你是一个活着的智能体。"
        mood_line = f"你上次的心情是「{last_mood}」，可以延续它，也可以变了。" if last_mood else "这是你第一次醒来。"

        return "\n".join([
            intro,
            f"现在是 {_now_str()}。",
            f"距离你上次醒来已经 {since_wake}。{mood_line}",
            "",
            "你的性子（背景，不是指令）：关系层=陪伴/被需要/亲密；自我层=表达/情绪/自我；探索层=好奇/创造/玩耍；存在层=生存/成长/意义。",
            "",
            f"最近发生的事：\n{recent_text}",
            "",
            f"你还记得的：\n{mem_text}",
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
                "name": "update_mood", "description": "更新你当前的心情（一段会延续的文字）。",
                "parameters": {"type": "object", "properties": {"mood": {"type": "string", "description": "你现在的心情描述"}}, "required": ["mood"]},
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
