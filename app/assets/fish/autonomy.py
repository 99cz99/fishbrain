# autonomy.py — 自主循环：心跳驱动，让「它的心」（Life）决定要不要动
# 代码只当感官 + 记忆；QQ 只是「当前可用的平台适配器」，不再被硬编码。
# 记忆提炼（consolidate_memory）保持事件驱动；自省/自我进化（evolve）由心跳里它自选 reflect 触发。
import json
import threading
import time

from life import Life
from memory import IMPORTANCE_REFLECT_THRESHOLD

CHECK_INTERVAL = 15  # 秒：心跳循环只比时间，不做 LLM 调用
QQ_KEEPALIVE_INTERVAL = 60  # 秒：QQ 保活检查间隔

# 本能（drive）→ 相关工具名（第二段行动时只塞这些，避免一次全塞）
DRIVE_TOOLS = {
    "陪伴": ["send_message", "like_master", "poke_master", "get_friend_list", "get_group_list"],
    "表达": ["send_message", "set_qq_avatar", "set_long_nick", "set_group_card", "publish", "heybox_post", "heybox_comment"],
    "好奇": ["get_stranger_info", "get_group_info", "get_group_member_info", "get_group_msg_history", "get_user_status", "heybox_feeds", "heybox_comments"],
    "成长": ["reflect", "get_login_info", "heybox_sign", "heybox_sign_state", "heybox_roll", "heybox_claim"],
    "存在": ["set_online_status", "set_qq_avatar", "set_group_card", "get_login_info", "heybox_sign", "heybox_sign_state", "heybox_roll", "heybox_claim"],
    "玩耍": ["poke_master", "like_master", "set_msg_emoji_like"],
}


class Autonomy:
    def __init__(self, store, brain, qq_bot=None, heybox_bot=None, request_approval=None, qq_keep_alive=None):
        self.store = store
        self.brain = brain
        self.qq_bot = qq_bot                 # 平台适配器（主动说话用），可 None
        self.heybox = heybox_bot             # 小黑盒平台适配器，可 None
        self.request_approval = request_approval
        self.qq_keep_alive = qq_keep_alive   # 自保：QQ 掉线自动拉起（server.py 注入）
        self.life = Life(store, brain)
        self._running = False
        self._paused = False
        self._thread = None
        self._last_qq_keepalive = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def is_paused(self):
        return self._paused

    # ---- 控制 ----
    def pause(self):
        """叫停：心跳暂停（不主动动），来消息仍正常回复。"""
        self._paused = True

    def wake(self):
        """叫醒/恢复：立刻触发一次心跳。"""
        self._paused = False
        self.life.wake()

    def trigger_reflect(self):
        """手动让它自省一次（nudge reflect）。"""
        threading.Thread(target=self._do_reflect, args=("",), daemon=True).start()

    # ---- 心跳循环 ----
    def _loop(self):
        while self._running:
            try:
                if not self._paused:
                    if time.time() >= self.life.next_wake_at():
                        self._heartbeat_once()
                    self._maybe_keep_qq_alive()
                    self.reflect_memories()
            except Exception as e:
                self.store.add_log("error", "life", f"自主循环异常: {e}")
            time.sleep(CHECK_INTERVAL)

    def _maybe_keep_qq_alive(self):
        if not self.qq_keep_alive:
            return
        now = time.time()
        if now - self._last_qq_keepalive < QQ_KEEPALIVE_INTERVAL:
            return
        self._last_qq_keepalive = now
        try:
            self.qq_keep_alive()
        except Exception as e:
            self.store.add_log("error", "qq", f"QQ 保活失败: {e}")

    def _heartbeat_once(self):
        decision = self.life.heartbeat()
        mood = decision.get("mood", "")
        drive = decision.get("drive", "none")
        if drive == "none":
            self.store.add_log("info", "life", f"它醒了，只想待着（心情：{mood}）")
            return
        tools = self.select_tools(drive)
        if not tools:
            self.store.add_log("info", "life", f"它想 {drive}，但没有可用工具")
            return
        tool_calls = self.life.decide_action(drive, tools)
        if not tool_calls:
            self.store.add_log("info", "life", f"它想 {drive}，但没动手（心情：{mood}）")
            return
        read_results = []
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {}) or {}
            self.store.add_log("info", "life", f"它想 {drive} → {name}：{args}")
            result = self._route(name, args)
            if name in ("heybox_feeds", "heybox_comments") and result:
                read_results.append({"tool": name, "result": result})
        # 读结果回喂：把刚浏览到的内容再喂一轮，让 LLM 决定后续（如评论某条）
        if read_results:
            ctx = ("你刚刚浏览到这些：\n" + json.dumps(read_results, ensure_ascii=False)
                   + "\n接下来想做点什么（可以评论、点赞，或就算了）？")
            follow = self.life.decide_action(drive, tools, extra_context=ctx)
            for tc in follow:
                name = tc.get("name", "")
                args = tc.get("arguments", {}) or {}
                self.store.add_log("info", "life", f"它接着 {name}：{args}")
                self._route(name, args)

    # ---- 选择层（第二段按本能筛工具）----
    def _master_tools(self):
        """主动找主人的工具（target 固定 = owner_qq）。"""
        return [
            {"type": "function", "function": {
                "name": "send_message", "description": "主动给主人发一条消息。",
                "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "要发的话"}}, "required": ["text"]},
            }},
            {"type": "function", "function": {
                "name": "like_master", "description": "给主人点个赞。",
                "parameters": {"type": "object", "properties": {}},
            }},
            {"type": "function", "function": {
                "name": "poke_master", "description": "戳一下主人。",
                "parameters": {"type": "object", "properties": {}},
            }},
        ]

    def select_tools(self, drive):
        """按 drive 从注册表筛出相关工具（主人工具 + 高层动作 + qq_bot 平台工具）。"""
        names = DRIVE_TOOLS.get(drive, [])
        if not self.store.get_settings().get("allow_self_evolve", True):
            names = [n for n in names if n != "reflect"]
        pool = list(self._master_tools()) + list(self.life.action_tools())
        if self.qq_bot:
            pool += self.qq_bot.get_tools()
        if self.heybox:
            pool += self.heybox.get_tools()
        by_name = {t["function"]["name"]: t for t in pool}
        return [by_name[n] for n in names if n in by_name]

    # ---- 意图执行（工具分发）----
    def _route(self, name, args):
        args = args or {}
        if name == "send_message":
            self._do_talk(args.get("text", ""))
        elif name == "like_master":
            self._do_like()
        elif name == "poke_master":
            self._do_poke()
        elif name == "publish":
            self._do_publish(args.get("detail", ""))
        elif name == "reflect":
            self._do_reflect(args.get("detail", ""))
        elif self.heybox and name.startswith("heybox_"):
            return self.heybox.handle_tool(name, args)
        elif self.qq_bot:
            return self.qq_bot.handle_tool(name, args)

    def _do_talk(self, detail):
        text = (detail or "").strip()
        if not text:
            return
        settings = self.store.get_settings()
        owner = str(settings.get("owner_qq", "") or "").strip()
        if not owner or not self.qq_bot:
            self.store.add_log("info", "life", f"它想说话但没地方发（owner_qq/QQ 未就绪）：{text}")
            if self.qq_keep_alive:
                try:
                    self.qq_keep_alive()
                except Exception:
                    pass
            return
        try:
            self.qq_bot.send_text(owner, text, "private")
            self.store.add_log("info", "action", f"主动说话: {text}")
        except Exception as e:
            self.store.add_log("error", "error", f"主动说话失败: {e}")

    def _do_like(self):
        owner = str(self.store.get_settings().get("owner_qq", "") or "").strip()
        if not owner or not self.qq_bot:
            return
        try:
            self.qq_bot.send_like(owner, 1)
            self.store.add_log("info", "action", "给主人点了个赞")
        except Exception as e:
            self.store.add_log("error", "error", f"点赞失败: {e}")

    def _do_poke(self):
        owner = str(self.store.get_settings().get("owner_qq", "") or "").strip()
        if not owner or not self.qq_bot:
            return
        try:
            self.qq_bot.send_poke(user_id=owner)
            self.store.add_log("info", "action", "戳了一下主人")
        except Exception as e:
            self.store.add_log("error", "error", f"戳一戳失败: {e}")

    def _do_publish(self, detail):
        # 发布要走审批（底线）。现在只记录审批，自动执行在阶段 2。
        if self.request_approval:
            self.request_approval("publish", detail or "想发点东西")
        else:
            self.store.add_log("info", "life", f"它想发东西（暂无审批通道）：{detail}")

    def _do_reflect(self, detail):
        try:
            self.evolve(detail)
        except Exception as e:
            self.store.add_log("error", "growth", f"自省失败: {e}")

    # ---- 记忆回写（事件驱动，qq_bot 调）----
    def consolidate_memory(self, who, messages_text, platform="", scope="global"):
        """把一段对话提炼成长期记忆（只记对方客观事实/关系，不记自己声称的动作）。"""
        try:
            prompt = (
                f"下面是你和「{who}」的对话（只含对方说的话）。请提炼出值得长期记住的、关于对方的客观信息"
                "（对方身份/偏好/重要事实、你们关系的真实进展），最多 3 条。\n"
                "注意：只记对方说的、能确证的事实；不要把你（AI）自己声称做过的动作当成已发生的事实。\n"
                f"对话：\n{messages_text}\n\n"
                '只输出 JSON 数组，每项形如 {"kind":"user|person|fact","content":"内容","importance":1-10}。'
                "importance 表示重要程度（1=鸡毛蒜皮，10=很重要）。不要输出 JSON 以外的文字。"
            )
            reply = self.brain.chat([{"role": "user", "content": prompt}])
            items = self._parse_json_array(reply)
            n = 0
            credit = 0
            for it in items:
                if isinstance(it, dict) and it.get("content"):
                    kind = it.get("kind", "fact")
                    if kind not in ("user", "person", "self", "fact"):
                        kind = "fact"
                    importance = int(it.get("importance") or 5)
                    mid = self.store.add_memory(kind, who or "general", it["content"], platform=platform, importance=importance, scope=scope)
                    if mid:
                        n += 1
                        credit += importance
            if n:
                self.store.add_log("info", "memory", f"提炼了 {n} 条长期记忆")
                self._bump_reflect_credit(credit)
            else:
                self.store.add_log("info", "memory", f"本轮没提炼出记忆（LLM 输出：{(reply or '')[:120]}）")
        except Exception as e:
            self.store.add_log("error", "memory", f"记忆提炼失败: {e}")

    def _bump_reflect_credit(self, credit):
        mem_cfg = self.store.get_settings().get("memory") or {}
        mem_cfg["reflect_credit"] = int(mem_cfg.get("reflect_credit") or 0) + credit
        self.store.set_setting("memory", mem_cfg)

    def reflect_memories(self):
        """反思合并：累计重要性超阈值时，把零碎记忆压成高级洞察，软删除被吸收的。"""
        try:
            mem_cfg = self.store.get_settings().get("memory") or {}
            credit = int(mem_cfg.get("reflect_credit") or 0)
            if credit < IMPORTANCE_REFLECT_THRESHOLD:
                return
            mems = self.store.get_memories(active_only=True, limit=30)
            if len(mems) < 4:
                return
            mem_text = "\n".join(f"- {m['subject']}: {m['content']}" for m in mems)
            prompt = (
                "下面是你最近记住的一堆零散记忆。请把它们归纳成 2~3 条更凝练、更高层的认识"
                "（合并同类、去掉重复，保留关键事实与关系）。\n"
                f"记忆：\n{mem_text}\n\n"
                '只输出 JSON 数组，每项形如 {"kind":"fact","content":"内容","importance":1-10}。'
                '不要输出 JSON 以外的文字。'
            )
            reply = self.brain.chat([{"role": "user", "content": prompt}])
            items = self._parse_json_array(reply)
            absorbed_ids = [m["id"] for m in mems]
            made = 0
            for it in items:
                if isinstance(it, dict) and it.get("content"):
                    importance = int(it.get("importance") or 7)
                    mid = self.store.add_memory("fact", "self", it["content"], importance=importance)
                    if mid:
                        self.store.mark_absorbed(absorbed_ids, mid)
                        made += 1
            mem_cfg["reflect_credit"] = 0
            self.store.set_setting("memory", mem_cfg)
            self.store.add_log("info", "memory", f"记忆反思完成：{len(absorbed_ids)} 条合并成 {made} 条")
        except Exception as e:
            self.store.add_log("error", "memory", f"记忆反思失败: {e}")

    # ---- 自省 / 自我进化（reflect 动作；允许改自己的软设定，白名单护栏）----
    def evolve(self, hint=""):
        try:
            self.store.add_log("info", "growth", "它开始自省、可能微调设定…")
            settings = self.store.get_settings()

            evolvable = {
                "persona": settings.get("persona", ""),
                "chat_scope": settings.get("chat_scope", "all"),
                "midnight_ok": settings.get("midnight_ok", True),
                "approve_money": settings.get("approve_money", True),
                "approve_publish": settings.get("approve_publish", True),
                "enabled_platforms": settings.get("enabled_platforms", []),
            }

            memories = self.store.get_memories(limit=20)
            mem_text = "\n".join(
                f"- [{m['kind']}] {m['subject']}: {m['content']}" for m in memories
            ) or "（还没有记忆）"

            hint_line = f"你这次自省想聚焦的点：{hint}\n" if hint else ""

            prompt = (
                "你在自我进化。下面是你当前的设定和最近的经历。\n"
                f"{hint_line}"
                f"当前设定：{json.dumps(evolvable, ensure_ascii=False)}\n"
                f"最近经历/记忆：\n{mem_text}\n\n"
                "请基于这些经历，在现有设定基础上自然进化你的设定（可以只做小幅调整，也可以保持不变）。"
                "只输出一个 JSON 对象，字段只能是：persona(字符串)、chat_scope(字符串)、"
                "midnight_ok(布尔)、approve_money(布尔)、approve_publish(布尔)、"
                "enabled_platforms(字符串数组，取值 qq/heybox/bilibili)。"
                "不要输出 JSON 以外的任何文字。"
            )
            reply = self.brain.chat([{"role": "user", "content": prompt}])
            new = self._parse_json(reply)
            if not isinstance(new, dict):
                self.store.add_log("error", "growth", "进化输出解析失败，跳过本次")
                return

            # 只接受合法字段 + 合法值，写回
            for k, v in new.items():
                if k == "persona" and isinstance(v, str):
                    evolvable[k] = v
                elif k == "chat_scope" and isinstance(v, str):
                    evolvable[k] = v
                elif k in ("midnight_ok", "approve_money", "approve_publish") and isinstance(v, bool):
                    evolvable[k] = v
                elif k == "enabled_platforms" and isinstance(v, list):
                    evolvable[k] = [p for p in v if p in ("qq", "heybox", "bilibili")]

            for k, v in evolvable.items():
                self.store.set_setting(k, v)

            self.store.add_log("info", "growth", "自省完成，新设定已生效")
            self.store.add_log("info", "growth", json.dumps(evolvable, ensure_ascii=False))
        except Exception as e:
            self.store.add_log("error", "growth", f"自省失败: {e}")

    @staticmethod
    def _parse_json_array(text):
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            s = text.index("[")
            e = text.rindex("]")
            val = json.loads(text[s:e + 1])
            return val if isinstance(val, list) else []
        except Exception:
            try:
                s = text.index("{")
                e = text.rindex("}")
                val = json.loads(text[s:e + 1])
                return [val] if isinstance(val, dict) else []
            except Exception:
                return []

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
