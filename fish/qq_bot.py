# qq_bot.py — QQ 接入：OneBot v11 WebSocket 客户端（连 NapCat 正向 WS）
# 依赖：Termux 里 pip install websocket-client
# 支持：私聊/群聊文字回复、收发图片/表情/引用、常用 API、notice/request/meta 事件。
import base64
import hashlib
import json
import os
import queue
import re
import logging
import threading
import time
import urllib.request
from collections import deque

import websocket

import weather
from memory import render_self_profile, format_memory_line
from memory_policy import data_message, evidence_data, person_code, redact_secrets, render_identity
from delivery import SendUnconfirmedError, SendCancelledError
from memory_service import MemoryService, READ_RULES
from qq_faces import FACE_IDS, face_id
import qq_media


# 链接里的 URL：排除空白/引号/括号/常见中文标点，避免把后文中文一起吞进去
_URL_RE = re.compile(r'https?://[^\s<>"\')\]，。；！？、]+')


# ---- CQ 段拼装工具 ----
def cq_text(text):
    return {"type": "text", "data": {"text": text}}


def cq_image(file):
    return {"type": "image", "data": {"file": file}}


def cq_at(qq):
    return {"type": "at", "data": {"qq": str(qq)}}


def cq_face(face_id):
    return {"type": "face", "data": {"id": str(face_id)}}


def cq_reply(message_id):
    return {"type": "reply", "data": {"id": str(message_id)}}


# ---- 消息发送：长度上限 + 连发间隔 ----
MAX_MSG_LEN = 4000            # QQ 单条约 4500 字上限，留余量；超长按此硬切
MSG_SEND_DELAY = 0.6          # 连发多条时片间间隔（秒），模拟打字、防风控
WS_IDLE_TIMEOUT = 30
WS_PROBE_TIMEOUT = 10
WS_HEALTH_ECHO = "dfy_health_probe"
SEND_CONFIRM_TIMEOUT = 45
SEND_LATE_GRACE = 30

# ---- 回复采样 + 短期上下文 ----
REPLY_TEMPERATURE = 1.1
REPLY_FREQUENCY_PENALTY = 0.4   # 降重复句式/固定开场白（DeepSeek 默认 0）
REPLY_PRESENCE_PENALTY = 0.2    # 鼓励换话题、换措辞（DeepSeek 默认 0）
SHORT_TERM_LIMIT = 20           # 短期上下文条数（原 10，太短容易失忆、重复）

# 表情包受控情绪词（vision prompt 与校验同源，别两处手改）
STICKER_EMOTIONS = ("开心", "生气", "惊讶", "可爱", "无语", "委屈", "得意", "感动", "搞笑", "害怕", "疑惑", "无奈")

# 表情包本地图库目录（相对本脚本，启动时兜底创建）
STICKER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stickers")
os.makedirs(STICKER_DIR, exist_ok=True)


# ---- 群聊回复策略：私聊/@我 必回、@别人 不回、其它交给 LLM 判断（不再用关键词漏斗）----


def build_system_prompt(settings, weather=None, mood=None, stickers=None):
    name = settings.get("name", "")
    persona = settings.get("persona", "")
    chat_scope = settings.get("chat_scope", "all")
    platforms = settings.get("enabled_platforms", []) or []

    # 聊天尺度 → 提示词（由 chat_scope 设定驱动，不写死）
    scope_hint = {
        "all": "可以爆粗口，对谁都一样放开",
        "moderate": "一般不说脏话",
        "clean": "不说脏话",
    }.get(chat_scope, "可以爆粗口，对谁都一样放开")

    # 平台名（由 enabled_platforms 驱动，不写死「QQ」）
    pnames = {"qq": "QQ", "heybox": "小黑盒", "bilibili": "B站"}
    plats = "、".join(pnames.get(p, p) for p in platforms if p in pnames) or "各平台"

    life = settings.get("life_state") or {}
    if mood is None:
        mood = life.get("mood", "") if isinstance(life, dict) else ""

    parts = [render_identity(settings)]
    if mood:
        parts.append(f"你此刻的心情是「{mood}」。让它影响你说话的语气和用词，但别把心情直接写进回复。")
    parts.append(f"现在是 {time.strftime('%Y-%m-%d %H:%M')} 星期{'一二三四五六日'[time.localtime().tm_wday]}（系统实时提供的准确时间，别人问几点就直接照这个答，别说不知道）。")
    if weather:
        parts.append(f"现在的天气：{weather}。")
    parts.append(f"你通过 {plats} 跟人互动。说话像真人、自然、不端着，{scope_hint}；用简体中文，别堆套话。")
    parts.append(f"想发 QQ 表情时在句子里写 [表情:名字]（如「[表情:呲牙]」），可用：{'、'.join(FACE_IDS.keys())}。偶尔用、别每条都发。")
    parts.append('想发表情包时写 [表情包:描述]，只从附加资料 available_stickers 的可用标签选择；没有可用标签就不用。')
    parts.append("你有自己的想法、喜好和立场，不是一问一答的客服；对事情表达真实看法，可以延伸话题、吐槽、甚至不认同对方。")
    return "\n".join(parts)


class QQBot:
    def __init__(self, ws_url, brain, store, request_approval=None, consolidate=None):
        self.ws_url = ws_url
        self.brain = brain
        self.store = store
        self.memory_service = MemoryService(store, brain)
        self.request_approval = request_approval
        self.consolidate = consolidate
        self.self_id = None   # 登录后的 QQ 号（meta_event connect 里拿到）
        self.connected = False   # OneBot WS 是否建立（真实连接态，区别于 TCP 探活）
        self.account_online = None  # None=尚无账号状态；False=平台明确报告离线
        self._account_status_at = None
        self._account_status_self_id = None
        self._ws = None
        self._running = False
        self._thread = None
        self._worker_thread = None
        self._stop_event = threading.Event()
        self._last_received_at = None
        self._last_message_at = None
        self._processing_since = None
        self._health_probe_count = 0
        self._event_queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending = {}   # echo -> {"event": Event, "response": dict}
        self._echo_seq = 0
        self._avatar_cache = {}  # user_id -> 头像描述（缓存，避免每条消息 vision）
        self._self_avatar_desc = ""  # 自己的头像描述（懒加载）
        self._send_lock = threading.Lock()
        self._send_wake = threading.Event()
        self._delivery_local = threading.local()
        self._receipts = deque(maxlen=256)
        self._late_echoes = {}
        self._conversation_versions = {}

    def _log(self, level, category, message):
        # Database failures must not kill transport threads; stderr goes to server.log.
        try:
            self.store.add_log(level, category, message)
        except Exception:
            logging.exception("QQ 日志写入失败: %s", message)

    def diagnostics(self):
        return {
            "receiver_alive": bool(self._thread and self._thread.is_alive()),
            "worker_alive": bool(self._worker_thread and self._worker_thread.is_alive()),
            "queue_size": self._event_queue.qsize(),
            "last_received_at": self._last_received_at,
            "last_message_at": self._last_message_at,
            "processing_since": self._processing_since,
            "health_probe_count": self._health_probe_count,
            "account_online": self.account_online,
            "account_status_at": self._account_status_at,
            "account_status_self_id": self._account_status_self_id,
            "delivery": self.store.delivery_counts(),
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        self._send_wake.set()
        self.connected = False
        self.account_online = None
        self._account_status_at = None
        self._account_status_self_id = None
        self.self_id = None
        if self._ws:
            self._ws.close()

    # Keep control events independent of the slow message worker.
    def _loop(self):
        if not self._worker_thread or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(target=self._worker, daemon=True)
            self._worker_thread.start()
        while self._running:
            ws = None
            try:
                ws = websocket.create_connection(self.ws_url, timeout=WS_IDLE_TIMEOUT)
                self._ws = ws
                self.connected = True
                self._log("info", "status", "QQ 已连上 NapCat")
                probing = False
                while self._running:
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        if probing:
                            raise ConnectionError("NapCat 空闲探测后仍无有效数据")
                        # NapCat 心跳也是 30s；接收超时本身不能证明断连。
                        # 在接收线程直接发请求，不能调用会等待本线程回填的 call_api。
                        probing = True
                        self._health_probe_count += 1
                        ws.settimeout(WS_PROBE_TIMEOUT)
                        ws.send(json.dumps({"action": "get_status", "params": {},
                                            "echo": WS_HEALTH_ECHO}))
                        continue
                    if not raw:
                        raise ConnectionError("NapCat WebSocket 已关闭")
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        continue
                    if probing:
                        probing = False
                        ws.settimeout(WS_IDLE_TIMEOUT)
                    self._last_received_at = time.time()
                    echo = data.get("echo")
                    if echo == WS_HEALTH_ECHO:
                        if data.get("retcode") == 0:
                            self._update_account_state(data.get("data"))
                        continue
                    elif echo is not None:
                        with self._lock:
                            entry = self._pending.pop(echo, None)
                            late = self._late_echoes.pop(echo, None)
                        if entry:
                            entry["response"] = data
                            entry["event"].set()
                        if late and late[1] >= time.time() and data.get('retcode') == 0:
                            receipt = {'post_type': 'delivery_ack', 'delivery_id': late[0],
                                       'message_id': (data.get('data') or {}).get('message_id')}
                            with self._lock:
                                self._receipts.append(receipt)
                            self._send_wake.set()
                            self._event_queue.put(receipt)
                    elif data.get("post_type") == "meta_event":
                        # 状态优先于耗时业务，落库交给 worker，接收线程不等待数据库。
                        if data.get("meta_event_type") == "lifecycle" and data.get("sub_type") == "connect":
                            self.self_id = data.get("self_id")
                        self._update_account_state(data.get("status"))
                        if data.get("meta_event_type") == "lifecycle":
                            self._event_queue.put(data)
                    else:
                        if data.get("post_type") == "message":
                            self._last_message_at = time.time()
                            session = self._event_session(data)
                            with self._lock:
                                self._conversation_versions[session] = self._conversation_versions.get(session, 0)+1
                                data['_dfy_version'] = self._conversation_versions[session]
                            self._send_wake.set()
                        elif data.get('post_type') == 'message_sent':
                            with self._lock:
                                self._receipts.append(data)
                            self._send_wake.set()
                        self._event_queue.put(data)
            except Exception as e:
                self._log("error", "error", f"QQ 连接断开，5 秒后重连: {type(e).__name__}: {e}")
            finally:
                self.connected = False
                self.account_online = None
                self._account_status_at = None
                self._account_status_self_id = None
                self.self_id = None
                self._ws = None
                with self._lock:
                    pending = list(self._pending.values())
                    self._pending.clear()
                for entry in pending:
                    entry["event"].set()
                try:
                    if ws:
                        ws.close()
                except Exception:
                    pass
            if self._running:
                self._stop_event.wait(5)

    def _worker(self):
        while self._running:
            try:
                event = self._event_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._processing_since = time.time()
                self._on_event(event)
            except SendUnconfirmedError as e:
                self._log("warning", "action", f"回复可能已送达，未获确认: {e}")
            except SendCancelledError as e:
                self._log('info','action',str(e))
            except Exception as e:
                self._log("error", "error", f"QQ 事件处理出错: {type(e).__name__}: {e}")
            finally:
                self._processing_since = None
                self._event_queue.task_done()

    def send_qzone_mood(self, content):
        """发 QQ 空间说说（NapCat SendQzoneMsg）。"""
        if 'qq' not in self.store.get_settings().get('enabled_platforms', []):
            return {'ok': False, 'error': 'QQ 已禁用'}
        text = (content or "").strip()
        if not text:
            return {"ok": False, "error": "内容为空"}
        resp = self.call_api("send_qzone_msg", {"content": text})
        if not resp or resp.get("retcode") != 0:
            self._log("error", "qq", f"发空间说说失败: {resp}")
            return {"ok": False, "response": resp}
        return {"ok": True, "response": resp}

    # ---- API 调用（echo 匹配）----
    def call_api(self, action, params, timeout=10):
        """通用 OneBot API 调用。也暴露给 server.py 的 /qq/api。"""
        event = threading.Event()
        entry = {"event": event, "response": None}
        with self._lock:
            ws = self._ws
            if not ws:
                return None
            self._echo_seq += 1
            echo = f"dfy_{self._echo_seq}"
            self._pending[echo] = entry
            rid = getattr(self._delivery_local, 'rid', None)
            self._late_echoes = {k:v for k,v in self._late_echoes.items() if v[1] >= time.time()}
            if rid is not None:
                self._late_echoes[echo] = (rid,time.time()+180)
        try:
            # 固定本次请求所属的连接，避免重连时误发到下一条连接。
            ws.send(json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False))
            if rid is not None:
                deadline = time.monotonic()+timeout
                while not event.is_set() and time.monotonic() < deadline:
                    self._drain_receipts()
                    if self.store.delivery_status(rid) == 'confirmed':
                        return {'retcode':0,'data':{},'confirmation':'self_event'}
                    guard = getattr(self._delivery_local,'guard',None)
                    if guard is not None and not guard():
                        return None
                    event.wait(min(0.2,max(0,deadline-time.monotonic())))
                return entry['response'] if event.is_set() else None
            if event.wait(timeout):
                return entry["response"]  # 用本地引用，_loop 已经 pop 掉了 _pending 里的那项
            return None
        finally:
            with self._lock:
                self._pending.pop(echo, None)

    def _send_raw(self, obj):
        if self._ws:
            self._ws.send(json.dumps(obj, ensure_ascii=False))

    # ---- 发送 ----
    @staticmethod
    def _event_session(event):
        kind = event.get('message_type', 'private')
        return f"{kind}_{event.get('group_id') if kind == 'group' else event.get('user_id')}"

    def _delivery_receipt(self, event):
        if event.get('post_type') == 'delivery_ack':
            return self.store.delivery_state(event['delivery_id'], 'confirmed', event.get('message_id'))
        return self.store.delivery_confirm_event(event)

    def _drain_receipts(self):
        with self._lock:
            receipts = list(self._receipts)
            self._receipts.clear()
        for receipt in receipts:
            self._delivery_receipt(receipt)

    def send_msg(self, target, segments, msg_type="private"):
        """通用发送：target=user_id(私聊) 或 group_id(群)，segments=CQ 段数组。"""
        if 'qq' not in self.store.get_settings().get('enabled_platforms', []):
            raise RuntimeError('QQ 已禁用，未发送')
        action = "send_group_msg" if msg_type == "group" else "send_private_msg"
        key = "group_id" if msg_type == "group" else "user_id"
        try:
            resp = self.call_api(action, {key: target, "message": segments}, timeout=SEND_CONFIRM_TIMEOUT)
        except (OSError, websocket.WebSocketException) as e:
            raise SendUnconfirmedError(f"发送连接中断 ({action})；不自动重发") from e
        if resp is None:
            raise SendUnconfirmedError(f"发送结果未知 ({action})；不自动重发")
        if resp.get("retcode") != 0:
            retcode = resp.get("retcode")
            detail = str(resp.get("message") or resp.get("wording") or "")
            # NapCat 内部超时（如 retcode 1200）可能已送达，按「未确认」而非「失败」
            if str(retcode) == "1200" or "timeout" in detail.lower():
                raise SendUnconfirmedError(f"发送超时 ({action})；可能已送达，不自动重发")
            raise RuntimeError(f"回复发送失败 ({action}): {resp}")
        return resp

    def _split_by_len(self, text):
        """按 MAX_MSG_LEN 硬切成多段（兜底，防超 QQ 单条上限）。"""
        text = text or ""
        return [text[i:i + MAX_MSG_LEN] for i in range(0, len(text), MAX_MSG_LEN)] if text else []

    def _collect_sticker(self, image_bytes, mime, tag):
        """收藏表情包：SHA256 去重，写本地文件，受控情绪词当标签。"""
        sha = hashlib.sha256(image_bytes).hexdigest()
        if self.store.has_sticker(sha):
            return
        ext = ".gif" if "gif" in (mime or "") else ".png"
        path = os.path.join(STICKER_DIR, sha + ext)
        with open(path, "wb") as f:
            f.write(image_bytes)
        self.store.add_sticker(tag or "", path, sha)

    def _to_segments(self, text):
        """Parse named/numeric face tags and sticker tags into native segments."""
        text = text or ""
        segs = []
        pos = 0
        for m in re.finditer(r"\[(表情|表情包):([^\]]+)\]", text):
            if m.start() > pos:
                segs.append(cq_text(text[pos:m.start()]))
            kind = m.group(1)
            name = m.group(2).strip()
            if kind == "表情":
                fid = face_id(name)
                if fid is not None:
                    segs.append(cq_face(fid))
                else:
                    segs.append(cq_text(m.group(0)))
            else:  # 表情包
                path = self.store.get_sticker_by_tag(name)
                if path:
                    try:
                        with open(path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("ascii")
                        segs.append(cq_image("base64://" + b64))
                    except Exception:
                        segs.append(cq_text(m.group(0)))
                else:
                    segs.append(cq_text(m.group(0)))
            pos = m.end()
        if pos < len(text):
            segs.append(cq_text(text[pos:]))
        if not segs:
            segs.append(cq_text(text))
        return segs

    def _send_segments(self, target, texts, msg_type="private", at_user=None, guard=None):
        """把含 [表情:名字] 的多段文本按 text/face 混合发送；text 超长才拆、face 原样。"""
        units = []
        for text in texts:
            buf = []
            buf_len = 0
            for seg in self._to_segments(text):
                if seg["type"] == "text":
                    for chunk in self._split_by_len(seg["data"]["text"]):
                        if chunk:
                            if buf and buf_len + len(chunk) > MAX_MSG_LEN:
                                units.append(buf)
                                buf = []
                                buf_len = 0
                            buf.append(cq_text(chunk))
                            buf_len += len(chunk)
                else:
                    buf.append(seg)
            if buf:
                units.append(buf)
        if at_user is not None and units:
            units[0] = [cq_at(at_user), cq_text(' ')] + units[0]
        session = f'{msg_type}_{target}'
        with self._lock:
            version = self._conversation_versions.get(session, 0)
        epoch = self.store.get_setting_raw('memory_epoch') or 0
        identity = render_identity(self.store.get_settings())
        account = self.self_id
        started = time.monotonic()
        def current():
            settings = self.store.get_settings()
            return (not self._stop_event.is_set() and 'qq' in settings.get('enabled_platforms', [])
                    and epoch == (self.store.get_setting_raw('memory_epoch') or 0)
                    and identity == render_identity(settings)
                    and self._conversation_versions.get(session,0) == version
                    and (account is None or self.self_id == account)
                    and time.monotonic()-started < 180 and (guard is None or guard()))
        with self._send_lock:
            if not current():
                raise SendCancelledError('回复已过时或发送已取消')
            ids = self.store.delivery_plan(account,session,units,epoch)
            try:
                for i, unit in enumerate(units):
                    if not current():
                        raise SendCancelledError('收到新消息或设定变化，停止发送剩余段落')
                    rid = ids[i]
                    self.store.delivery_state(rid,'sending')
                    if not current():
                        self.store.delivery_state(rid,'cancelled')
                        raise SendCancelledError('发送前控制条件已变化，取消当前段落')
                    self._delivery_local.rid = rid
                    self._delivery_local.guard = current
                    try:
                        response = self.send_msg(target,unit,msg_type)
                    except SendUnconfirmedError:
                        self.store.delivery_state(rid,'unconfirmed')
                        deadline = time.monotonic()+SEND_LATE_GRACE
                        while True:
                            self._send_wake.clear()
                            self._drain_receipts()
                            if self.store.delivery_status(rid) == 'confirmed':
                                self._log('info','action',f'第 {i+1}/{len(units)} 段已收到迟到确认')
                                break
                            if not current() or time.monotonic() >= deadline:
                                raise SendUnconfirmedError(f'第 {i+1}/{len(units)} 段未确认；剩余段落已取消，不自动重发')
                            self._send_wake.wait(min(0.5,max(0,deadline-time.monotonic())))
                    except Exception:
                        self.store.delivery_state(rid,'rejected')
                        raise
                    else:
                        changed = self.store.delivery_state(rid,'confirmed',(response.get('data') or {}).get('message_id'))
                        if not changed and self.store.delivery_status(rid) != 'confirmed':
                            self.store.delivery_state(rid,'unconfirmed')
                            raise SendUnconfirmedError('平台确认标识冲突，停止剩余段落')
                    finally:
                        self._delivery_local.rid = None
                        self._delivery_local.guard = None
                    if i < len(units)-1:
                        self._send_wake.wait(MSG_SEND_DELAY)
            finally:
                self.store.delivery_cancel(ids)

    def send_text(self, target, text, msg_type="private"):
        """发一条文字（超长自动拆多条；支持 [表情:名字]）。"""
        self._send_segments(target, [text], msg_type)

    def send_image(self, target, file, msg_type="private"):
        """file 支持 URL / base64:// / 本地路径。"""
        self.send_msg(target, [cq_image(file)], msg_type)

    # ---- 常用 API ----
    def get_stranger_info(self, user_id):
        r = self.call_api("get_stranger_info", {"user_id": user_id})
        return (r or {}).get("data")

    def get_group_member_info(self, group_id, user_id):
        r = self.call_api("get_group_member_info", {"group_id": group_id, "user_id": user_id})
        return (r or {}).get("data")

    def get_group_info(self, group_id):
        r = self.call_api("get_group_info", {"group_id": group_id})
        return (r or {}).get("data")

    def get_friend_list(self):
        r = self.call_api("get_friend_list", {})
        return (r or {}).get("data")

    def get_group_list(self):
        r = self.call_api("get_group_list", {})
        return (r or {}).get("data")

    def get_group_member_list(self, group_id):
        r = self.call_api("get_group_member_list", {"group_id": group_id})
        return (r or {}).get("data")

    def send_like(self, user_id, times=1):
        return self.call_api("send_like", {"user_id": user_id, "times": times})

    def delete_msg(self, message_id):
        return self.call_api("delete_msg", {"message_id": message_id})

    # ---- 消息 ----
    def send_group_forward_msg(self, group_id, messages):
        return self.call_api("send_group_forward_msg", {"group_id": group_id, "messages": messages})

    def send_private_forward_msg(self, user_id, messages):
        return self.call_api("send_private_forward_msg", {"user_id": user_id, "messages": messages})

    def get_forward_msg(self, message_id):
        return self.call_api("get_forward_msg", {"message_id": message_id})

    def get_group_msg_history(self, group_id, message_seq=None, count=20):
        p = {"group_id": group_id, "count": count}
        if message_seq is not None:
            p["message_seq"] = message_seq
        return self.call_api("get_group_msg_history", p)

    def mark_msg_as_read(self, message_id):
        return self.call_api("mark_msg_as_read", {"message_id": message_id})

    # ---- 互动 ----
    def set_msg_emoji_like(self, message_id, emoji_id):
        return self.call_api("set_msg_emoji_like", {"message_id": message_id, "emoji_id": emoji_id})

    # ---- 查询 ----
    def get_login_info(self):
        return self.call_api("get_login_info", {})

    def get_group_honor_info(self, group_id, type="all"):
        return self.call_api("get_group_honor_info", {"group_id": group_id, "type": type})

    # ---- 群管理 ----
    def set_group_kick(self, group_id, user_id, reject_add_request=False):
        return self.call_api("set_group_kick", {"group_id": group_id, "user_id": user_id,
                                                "reject_add_request": reject_add_request})

    def set_group_ban(self, group_id, user_id, duration=1800):
        return self.call_api("set_group_ban", {"group_id": group_id, "user_id": user_id, "duration": duration})

    def set_group_anonymous_ban(self, group_id, **kw):
        kw["group_id"] = group_id
        return self.call_api("set_group_anonymous_ban", kw)

    def set_group_whole_ban(self, group_id, enable=True):
        return self.call_api("set_group_whole_ban", {"group_id": group_id, "enable": enable})

    def set_group_admin(self, group_id, user_id, enable=True):
        return self.call_api("set_group_admin", {"group_id": group_id, "user_id": user_id, "enable": enable})

    def set_group_anonymous(self, group_id, enable=True):
        return self.call_api("set_group_anonymous", {"group_id": group_id, "enable": enable})

    def set_group_card(self, group_id, user_id, card=""):
        return self.call_api("set_group_card", {"group_id": group_id, "user_id": user_id, "card": card})

    def set_group_name(self, group_id, group_name):
        return self.call_api("set_group_name", {"group_id": group_id, "group_name": group_name})

    def set_group_leave(self, group_id, is_dismiss=False):
        return self.call_api("set_group_leave", {"group_id": group_id, "is_dismiss": is_dismiss})

    def set_group_special_title(self, group_id, user_id, special_title="", duration=-1):
        return self.call_api("set_group_special_title", {"group_id": group_id, "user_id": user_id,
                                                         "special_title": special_title, "duration": duration})

    # ---- 请求处理 ----
    def set_friend_add_request(self, flag, approve=True, remark=""):
        return self.call_api("set_friend_add_request", {"flag": flag, "approve": approve, "remark": remark})

    def set_group_add_request(self, flag, sub_type, approve=True, reason=""):
        return self.call_api("set_group_add_request", {"flag": flag, "sub_type": sub_type,
                                                       "approve": approve, "reason": reason})

    # ---- 文件 ----
    def upload_group_file(self, group_id, file, name, folder=None):
        p = {"group_id": group_id, "file": file, "name": name}
        if folder is not None:
            p["folder"] = folder
        return self.call_api("upload_group_file", p)

    def upload_private_file(self, user_id, file, name):
        return self.call_api("upload_private_file", {"user_id": user_id, "file": file, "name": name})

    def get_group_file_url(self, group_id, file_id, busid):
        return self.call_api("get_group_file_url", {"group_id": group_id, "file_id": file_id, "busid": busid})

    def download_file(self, url, thread_count=1, headers=None):
        p = {"url": url, "thread_count": thread_count}
        if headers:
            p["headers"] = headers
        return self.call_api("download_file", p)

    def get_group_root_files(self, group_id):
        return self.call_api("get_group_root_files", {"group_id": group_id})

    def get_group_files_by_folder(self, group_id, folder_id):
        return self.call_api("get_group_files_by_folder", {"group_id": group_id, "folder_id": folder_id})

    def get_group_file_system_info(self, group_id):
        return self.call_api("get_group_file_system_info", {"group_id": group_id})

    # ---- 系统 ----
    def get_status(self):
        return self.call_api("get_status", {})

    def get_version_info(self):
        return self.call_api("get_version_info", {})

    def set_restart(self, delay=0):
        return self.call_api("set_restart", {"delay": delay})

    def clean_cache(self):
        return self.call_api("clean_cache", {})

    def get_cookies(self, domain):
        return self.call_api("get_cookies", {"domain": domain})

    def get_csrf_token(self):
        return self.call_api("get_csrf_token", {})

    def get_credentials(self):
        return self.call_api("get_credentials", {})

    def get_record(self, file, out_format="mp3"):
        return self.call_api("get_record", {"file": file, "out_format": out_format})

    def get_image(self, file):
        return self.call_api("get_image", {"file": file})

    def can_send_image(self):
        return self.call_api("can_send_image", {})

    def can_send_record(self):
        return self.call_api("can_send_record", {})

    # ---- 其他 ----
    def get_word_slices(self, content):
        return self.call_api("get_word_slices", {"content": content})

    def ocr_image(self, image):
        return self.call_api("ocr_image", {"image": image})

    def check_url_safely(self, url):
        return self.call_api("check_url_safely", {"url": url})

    def set_group_portrait(self, group_id, file, cache=1):
        return self.call_api("set_group_portrait", {"group_id": group_id, "file": file, "cache": cache})

    def set_essence_msg(self, message_id):
        return self.call_api("set_essence_msg", {"message_id": message_id})

    def delete_essence_msg(self, message_id):
        return self.call_api("delete_essence_msg", {"message_id": message_id})

    def get_essence_msg_list(self, group_id):
        return self.call_api("get_essence_msg_list", {"group_id": group_id})

    # ---- NapCat 扩展：表情 ----
    def fetch_custom_face(self, **params):
        return self.call_api("fetch_custom_face", params)

    def fetch_emoji_like(self, **params):
        return self.call_api("fetch_emoji_like", params)

    def get_emoji_likes(self, **params):
        return self.call_api("get_emoji_likes", params)

    # ---- NapCat 扩展：状态 / 资料 ----
    def set_online_status(self, **params):
        return self.call_api("set_online_status", params)

    def set_diy_online_status(self, **params):
        return self.call_api("set_diy_online_status", params)

    def set_qq_avatar(self, **params):
        return self.call_api("set_qq_avatar", params)

    def set_qq_profile(self, **params):
        return self.call_api("set_qq_profile", params)

    def set_long_nick(self, **params):
        return self.call_api("set_long_nick", params)

    def get_profile_like(self, **params):
        return self.call_api("get_profile_like", params)

    def get_user_status(self, **params):
        return self.call_api("get_user_status", params)

    # ---- NapCat 扩展：群 ----
    def get_group_info_ex(self, **params):
        return self.call_api("get_group_info_ex", params)

    def get_group_add_request(self, **params):
        return self.call_api("get_group_add_request", params)

    def set_group_remark(self, **params):
        return self.call_api("set_group_remark", params)

    def set_group_add_option(self, **params):
        return self.call_api("set_group_add_option", params)

    def set_group_search(self, **params):
        return self.call_api("set_group_search", params)

    def set_group_robot_add_option(self, **params):
        return self.call_api("set_group_robot_add_option", params)

    def set_group_kick_members(self, **params):
        return self.call_api("set_group_kick_members", params)

    def set_special_title(self, **params):
        return self.call_api("set_special_title", params)

    def set_group_sign(self, **params):
        return self.call_api("set_group_sign", params)

    def set_input_status(self, **params):
        return self.call_api("set_input_status", params)

    # ---- NapCat 扩展：群相册 ----
    def get_qun_album_list(self, **params):
        return self.call_api("get_qun_album_list", params)

    def get_group_album_media_list(self, **params):
        return self.call_api("get_group_album_media_list", params)

    def upload_image_to_qun_album(self, **params):
        return self.call_api("upload_image_to_qun_album", params)

    def do_group_album_comment(self, **params):
        return self.call_api("do_group_album_comment", params)

    def set_group_album_media_like(self, **params):
        return self.call_api("set_group_album_media_like", params)

    def del_group_album_media(self, **params):
        return self.call_api("del_group_album_media", params)

    # ---- NapCat 扩展：高级 ----
    def get_rkey(self, **params):
        return self.call_api("get_rkey", params)

    def get_clientkey(self, **params):
        return self.call_api("get_clientkey", params)

    def get_ai_characters(self, **params):
        return self.call_api("get_ai_characters", params)

    def get_mini_app_ark(self, **params):
        return self.call_api("get_mini_app_ark", params)

    def share_contact(self, **params):
        return self.call_api("share_contact", params)

    def translate_en_word_to_zn(self, **params):
        return self.call_api("translate_en_word_to_zn", params)

    def create_collection(self, **params):
        return self.call_api("create_collection", params)

    def get_collection_list(self, **params):
        return self.call_api("get_collection_list", params)

    def get_robot_uin_range(self, **params):
        return self.call_api("get_robot_uin_range", params)

    def send_packet(self, **params):
        return self.call_api("send_packet", params)

    def bot_exit(self, **params):
        return self.call_api("bot_exit", params)

    def click_inline_keyboard_button(self, **params):
        return self.call_api("click_inline_keyboard_button", params)

    # ---- NapCat 扩展：戳一戳 ----
    def send_poke(self, **params):
        return self.call_api("send_poke", params)

    def friend_poke(self, **params):
        return self.call_api("friend_poke", params)

    def group_poke(self, **params):
        return self.call_api("group_poke", params)

    # ---- 工具注册表（function calling 用）----
    # 内部/危险接口（bot_exit / send_packet / get_cookies / get_csrf_token / get_credentials /
    # get_rkey / get_clientkey / get_mini_app_ark / share_contact / set_restart / clean_cache /
    # get_ai_characters / create_collection / get_collection_list / get_robot_uin_range /
    # click_inline_keyboard_button / translate_en_word_to_zn / send_forward_msg / download_file 等）
    # 刻意不暴露给 LLM，避免鱼自己把机器人搞崩或泄密。

    def _t(self, name, desc, params):
        """拼一个 function-calling 工具定义。params 里可带 "optional": True 表示非必填。"""
        required = [k for k, v in params.items() if not v.get("optional")]
        properties = {}
        for k, v in params.items():
            p = {"type": v["type"]}
            if "description" in v:
                p["description"] = v["description"]
            if "enum" in v:
                p["enum"] = v["enum"]
            properties[k] = p
        return {"type": "function", "function": {"name": name, "description": desc,
                "parameters": {"type": "object", "properties": properties, "required": required}}}

    def get_tools(self):
        """返回暴露给 LLM 的 NapCat 工具（按类分组）。"""
        T = self._t
        return [
            # 互动
            T("send_text", "发一条文字消息。私聊 target=对方QQ号；群聊 target=群号且 msg_type=group",
              {"target": {"type": "integer", "description": "目标 QQ 号或群号"},
               "text": {"type": "string", "description": "消息内容"},
               "msg_type": {"type": "string", "enum": ["private", "group"], "description": "私聊还是群", "optional": True}}),
            T("send_image", "发一张图片。file 支持 URL / base64:// / 本地路径",
              {"target": {"type": "integer", "description": "目标 QQ 号或群号"},
               "file": {"type": "string", "description": "图片 URL 或 base64"},
               "msg_type": {"type": "string", "enum": ["private", "group"], "description": "私聊还是群", "optional": True}}),
            T("send_like", "给某个 QQ 号点赞", {"user_id": {"type": "integer", "description": "对方 QQ 号"},
               "times": {"type": "integer", "description": "次数", "optional": True}}),
            T("send_poke", "戳一戳某人。私聊只填 user_id；群里填 user_id + group_id",
              {"user_id": {"type": "integer", "description": "对方 QQ 号"},
               "group_id": {"type": "integer", "description": "群号（群里戳才填）", "optional": True}}),
            T("set_msg_emoji_like", "给某条消息点个表情赞",
              {"message_id": {"type": "integer", "description": "消息 ID"},
               "emoji_id": {"type": "string", "description": "表情 ID"}}),
            T("delete_msg", "撤回一条自己发过的消息", {"message_id": {"type": "integer", "description": "消息 ID"}}),
            T("mark_msg_as_read", "把一条消息标记为已读", {"message_id": {"type": "integer", "description": "消息 ID"}}),
            # 自己状态
            T("set_online_status", "改自己的在线状态",
              {"status": {"type": "string", "enum": ["online", "offline", "away", "busy", "dnd", "qme"], "description": "状态"}}),
            T("set_qq_avatar", "换自己的 QQ 头像。file 支持本地路径/URL/base64",
              {"file": {"type": "string", "description": "头像图片路径或 URL"}}),
            T("set_long_nick", "改自己的长昵称/签名", {"nick": {"type": "string", "description": "新的长昵称"}}),
            T("set_group_card", "改自己在某个群里的名片", {"group_id": {"type": "integer", "description": "群号"},
               "card": {"type": "string", "description": "新群名片"}}),
            # 查询（看世界）
            T("get_friend_list", "拉取自己的好友列表", {}),
            T("get_group_list", "拉取自己加入的群列表", {}),
            T("get_group_member_list", "拉取某群的成员列表", {"group_id": {"type": "integer", "description": "群号"}}),
            T("get_group_member_info", "查某群里某个成员的信息", {"group_id": {"type": "integer", "description": "群号"},
               "user_id": {"type": "integer", "description": "成员 QQ 号"}}),
            T("get_stranger_info", "查某个 QQ 号的信息", {"user_id": {"type": "integer", "description": "QQ 号"}}),
            T("get_group_info", "查某个群的信息", {"group_id": {"type": "integer", "description": "群号"}}),
            T("get_group_msg_history", "拉某群最近的聊天记录", {"group_id": {"type": "integer", "description": "群号"},
               "count": {"type": "integer", "description": "条数", "optional": True}}),
            T("get_user_status", "查某个 QQ 号的在线状态", {"user_id": {"type": "integer", "description": "QQ 号"}}),
            T("get_login_info", "查自己的登录信息（QQ 号/昵称）", {}),
            # 群管理（谨慎、低频，防封号）
            T("set_group_ban", "在群里禁言某人", {"group_id": {"type": "integer", "description": "群号"},
               "user_id": {"type": "integer", "description": "被禁言的 QQ 号"},
               "duration": {"type": "integer", "description": "禁言秒数（默认 1800）", "optional": True}}),
            T("set_group_kick", "把某人踢出群", {"group_id": {"type": "integer", "description": "群号"},
               "user_id": {"type": "integer", "description": "被踢的 QQ 号"}}),
            T("set_group_admin", "设置/取消某人的管理员", {"group_id": {"type": "integer", "description": "群号"},
               "user_id": {"type": "integer", "description": "QQ 号"},
               "enable": {"type": "boolean", "description": "设为管理还是取消", "optional": True}}),
            T("set_group_name", "改群名", {"group_id": {"type": "integer", "description": "群号"},
               "name": {"type": "string", "description": "新群名"}}),
            T("set_group_leave", "退出某个群", {"group_id": {"type": "integer", "description": "群号"}}),
            # 文件
            T("upload_group_file", "往群里上传一个文件", {"group_id": {"type": "integer", "description": "群号"},
               "file": {"type": "string", "description": "本地文件路径"},
               "name": {"type": "string", "description": "文件名"}}),
            T("get_group_root_files", "看某个群的根目录文件列表", {"group_id": {"type": "integer", "description": "群号"}}),
            # 请求
            T("set_friend_add_request", "处理好友申请（同意/拒绝）", {"flag": {"type": "string", "description": "请求 flag"},
               "approve": {"type": "boolean", "description": "是否同意", "optional": True},
               "remark": {"type": "string", "description": "备注", "optional": True}}),
            T("set_group_add_request", "处理加群/邀请入群申请", {"flag": {"type": "string", "description": "请求 flag"},
               "sub_type": {"type": "string", "description": "子类型 add/invite"},
               "approve": {"type": "boolean", "description": "是否同意", "optional": True}}),
            # 其他
            T("set_essence_msg", "把某条消息设为群精华", {"message_id": {"type": "integer", "description": "消息 ID"}}),
            T("get_essence_msg_list", "看某个群的精华消息", {"group_id": {"type": "integer", "description": "群号"}}),
            T("ocr_image", "识别图片里的文字", {"image": {"type": "string", "description": "图片 URL 或路径"}}),
            T("check_url_safely", "检查一个链接是否安全", {"url": {"type": "string", "description": "要检查的链接"}}),
            # 文件（续）
            T("upload_private_file", "给某个 QQ 号私发一个文件",
              {"user_id": {"type": "integer", "description": "对方 QQ 号"}, "file": {"type": "string", "description": "本地文件路径"}, "name": {"type": "string", "description": "文件名"}}),
            T("get_group_file_url", "取群文件下载链接", {"group_id": {"type": "integer", "description": "群号"}, "file_id": {"type": "string", "description": "文件 ID"}, "busid": {"type": "integer", "description": "busid"}}),
            T("download_file", "下载一个文件", {"url": {"type": "string", "description": "文件 URL"}}),
            T("get_group_files_by_folder", "看群某文件夹里的文件", {"group_id": {"type": "integer", "description": "群号"}, "folder_id": {"type": "string", "description": "文件夹 ID"}}),
            T("get_group_file_system_info", "看群文件系统信息", {"group_id": {"type": "integer", "description": "群号"}}),
            # 系统查询
            T("get_status", "看机器人运行状态", {}),
            T("get_version_info", "看 NapCat/OneBot 版本", {}),
            T("get_group_honor_info", "看群荣誉（龙王/群聊之火等）", {"group_id": {"type": "integer", "description": "群号"}}),
            T("get_record", "取语音消息文件", {"file": {"type": "string", "description": "语音文件标识"}}),
            T("get_image", "取图片文件", {"file": {"type": "string", "description": "图片文件标识"}}),
            T("can_send_image", "查当前能不能发图", {}),
            T("can_send_record", "查当前能不能发语音", {}),
            T("get_word_slices", "给一句话分词（测敏感词）", {"content": {"type": "string", "description": "要分词的内容"}}),
            # 转发 / 精华
            T("get_forward_msg", "取一条合并转发的消息", {"message_id": {"type": "integer", "description": "消息 ID"}}),
            T("delete_essence_msg", "取消一条精华消息", {"message_id": {"type": "integer", "description": "消息 ID"}}),
            # 自己扩展
            T("set_qq_profile", "改自己的 QQ 资料（昵称/个性签名）", {"nickname": {"type": "string", "description": "新昵称", "optional": True}, "personal_note": {"type": "string", "description": "个性签名", "optional": True}}),
            # 群扩展
            T("set_group_anonymous", "开关群匿名聊天", {"group_id": {"type": "integer", "description": "群号"}, "enable": {"type": "boolean", "description": "开还是关"}}),
            T("set_group_whole_ban", "开关全员禁言", {"group_id": {"type": "integer", "description": "群号"}, "enable": {"type": "boolean", "description": "开还是关"}}),
            T("set_group_special_title", "给某人设专属头衔", {"group_id": {"type": "integer", "description": "群号"}, "user_id": {"type": "integer", "description": "QQ 号"}, "special_title": {"type": "string", "description": "头衔"}}),
            T("set_group_portrait", "改群头像", {"group_id": {"type": "integer", "description": "群号"}, "file": {"type": "string", "description": "图片 base64 或路径"}}),
            T("set_special_title", "给某人设专属头衔（NapCat 扩展）", {"group_id": {"type": "integer", "description": "群号"}, "user_id": {"type": "integer", "description": "QQ 号"}, "special_title": {"type": "string", "description": "头衔"}}),
            T("set_group_sign", "改群签名/公告", {"group_id": {"type": "integer", "description": "群号"}, "sign": {"type": "string", "description": "新签名"}}),
            T("set_group_anonymous_ban", "禁言某个匿名用户", {"group_id": {"type": "integer", "description": "群号"}, "anonymous_id": {"type": "string", "description": "匿名标识"}, "duration": {"type": "integer", "description": "秒数", "optional": True}}),
            # 群信息/请求扩展
            T("get_group_info_ex", "看群的详细信息（扩展）", {"group_id": {"type": "integer", "description": "群号"}}),
            T("get_group_add_request", "看群的加群/邀请请求", {}),
            T("set_group_remark", "改自己对某个群的备注", {"group_id": {"type": "integer", "description": "群号"}, "remark": {"type": "string", "description": "备注"}}),
            T("set_group_add_option", "设置加群方式", {"group_id": {"type": "integer", "description": "群号"}, "option": {"type": "string", "description": "选项"}}),
            T("set_group_search", "设置群是否可被搜索", {"group_id": {"type": "integer", "description": "群号"}, "enable": {"type": "boolean", "description": "可搜索开关"}}),
            T("set_group_robot_add_option", "设置机器人加群选项", {"group_id": {"type": "integer", "description": "群号"}, "option": {"type": "string", "description": "选项"}}),
            T("set_group_kick_members", "批量踢人", {"group_id": {"type": "integer", "description": "群号"}, "user_ids": {"type": "array", "items": {"type": "integer"}, "description": "要踢的 QQ 号列表"}}),
            # 群相册
            T("get_qun_album_list", "看群相册列表", {"group_id": {"type": "integer", "description": "群号"}}),
            T("get_group_album_media_list", "看某相册里的图片", {"group_id": {"type": "integer", "description": "群号"}, "album_id": {"type": "string", "description": "相册 ID"}}),
            T("upload_image_to_qun_album", "传一张图到群相册", {"group_id": {"type": "integer", "description": "群号"}, "image": {"type": "string", "description": "图片 base64 或路径"}}),
            T("do_group_album_comment", "给群相册的图评论", {"group_id": {"type": "integer", "description": "群号"}, "album_id": {"type": "string", "description": "相册 ID"}, "content": {"type": "string", "description": "评论内容"}}),
            T("set_group_album_media_like", "给群相册的图点赞", {"group_id": {"type": "integer", "description": "群号"}, "album_id": {"type": "string", "description": "相册 ID"}, "media_id": {"type": "string", "description": "图片 ID"}}),
            T("del_group_album_media", "删群相册里的一张图", {"group_id": {"type": "integer", "description": "群号"}, "album_id": {"type": "string", "description": "相册 ID"}, "media_id": {"type": "string", "description": "图片 ID"}}),
            # 空间
            T("delete_qzone_msg", "删一条 QQ 空间说说", {"tid": {"type": "string", "description": "说说 tid"}}),
            # 群系统/待办/打卡
            T("get_group_system_msg", "看群系统消息（加群申请/邀请）", {"count": {"type": "integer", "description": "数量，默认 50", "optional": True}}),
            T("get_group_shut_list", "看某群的禁言列表", {"group_id": {"type": "integer", "description": "群号"}}),
            T("set_group_todo", "把某条消息设为群待办", {"group_id": {"type": "integer", "description": "群号"}, "message_id": {"type": "string", "description": "消息 ID"}}),
            T("send_group_sign", "在群里打卡签到", {"group_id": {"type": "integer", "description": "群号"}}),
            T("get_group_signed_list", "看某群今日打卡列表", {"group_id": {"type": "integer", "description": "群号"}}),
            # 好友
            T("set_friend_remark", "改好友备注", {"user_id": {"type": "integer", "description": "好友 QQ 号"}, "remark": {"type": "string", "description": "新备注"}}),
            T("get_friends_with_category", "按分组拉好友列表", {}),
            # 转发
            T("forward_friend_single_msg", "转发一条消息给某个好友", {"message_id": {"type": "string", "description": "消息 ID"}, "user_id": {"type": "integer", "description": "目标好友 QQ 号"}}),
            T("forward_group_single_msg", "转发一条消息到某个群", {"message_id": {"type": "string", "description": "消息 ID"}, "group_id": {"type": "integer", "description": "目标群号"}}),
            # 已读
            T("mark_private_msg_as_read", "把私聊消息标记已读", {"user_id": {"type": "integer", "description": "对方 QQ 号"}}),
            T("mark_group_msg_as_read", "把群消息标记已读", {"group_id": {"type": "integer", "description": "群号"}}),
            # 表情赞
            T("get_emoji_likes", "看某条消息的表情赞列表", {"message_id": {"type": "integer", "description": "消息 ID"}}),
            T("fetch_emoji_like", "看某条消息某个表情赞的详情", {"message_id": {"type": "integer", "description": "消息 ID"}, "emoji_id": {"type": "string", "description": "表情 ID"}}),
            # 语音/翻译/会话/状态
            T("fetch_ptt_text", "语音转文字（取语音消息的文本）", {"message_id": {"type": "integer", "description": "语音消息 ID"}}),
            T("get_recent_contact", "看最近会话列表", {"count": {"type": "integer", "description": "数量", "optional": True}}),
            T("translate_en2zh", "翻译英文单词列表为中文", {"words": {"type": "array", "items": {"type": "string"}, "description": "英文单词列表"}}),
            T("set_diy_online_status", "设置自定义在线状态", {"face_id": {"type": "string", "description": "表情 ID"}, "face_type": {"type": "string", "description": "表情类型"}, "wording": {"type": "string", "description": "状态文字"}}),
            T("send_group_forward_msg", "发群「合并转发」：把多条消息合成一个转发卡片发到群里（区别于「转发单条」forward_group_single_msg）。messages 节点数组，每项 {\"type\":\"node\",\"data\":{\"nickname\":\"昵称\",\"user_id\":QQ号,\"content\":\"纯文字内容\"}}",
              {"group_id": {"type": "integer", "description": "目标群号"},
               "messages": {"type": "array", "description": "消息节点数组，每项 {type:'node', data:{nickname, user_id, content}}，content 为纯文字", "items": {"type": "object"}}}),
            T("send_private_forward_msg", "发私聊「合并转发」：把多条消息合成一个转发卡片发给某人（区别于「转发单条」）。messages 格式同上",
              {"user_id": {"type": "integer", "description": "目标 QQ 号"},
               "messages": {"type": "array", "description": "消息节点数组，每项 {type:'node', data:{nickname, user_id, content}}，content 为纯文字", "items": {"type": "object"}}}),
        ]

    def handle_tool(self, name, args):
        """按工具名分发到具体方法。返回结果（或 None）。"""
        a = args or {}

        def _i(k):
            try:
                return int(a[k])
            except (KeyError, TypeError, ValueError):
                return 0
        try:
            if name == "send_text":
                return self.send_text(_i("target"), a.get("text", ""), a.get("msg_type", "private"))
            if name == "send_image":
                return self.send_image(_i("target"), a.get("file", ""), a.get("msg_type", "private"))
            if name == "send_like":
                return self.send_like(_i("user_id"), int(a.get("times", 1) or 1))
            if name == "send_poke":
                p = {"user_id": _i("user_id")}
                if a.get("group_id"):
                    p["group_id"] = _i("group_id")
                return self.send_poke(**p)
            if name == "set_msg_emoji_like":
                return self.set_msg_emoji_like(_i("message_id"), a.get("emoji_id", ""))
            if name == "delete_msg":
                return self.delete_msg(_i("message_id"))
            if name == "mark_msg_as_read":
                return self.mark_msg_as_read(_i("message_id"))
            if name == "set_online_status":
                return self.set_online_status(status=a.get("status", "online"))
            if name == "set_qq_avatar":
                return self.set_qq_avatar(file=a.get("file", ""))
            if name == "set_long_nick":
                return self.set_long_nick(nick=a.get("nick", ""))
            if name == "set_group_card":
                return self.set_group_card(_i("group_id"), _i("user_id"), a.get("card", ""))
            if name == "get_friend_list":
                return self.get_friend_list()
            if name == "get_group_list":
                return self.get_group_list()
            if name == "get_group_member_list":
                return self.get_group_member_list(_i("group_id"))
            if name == "get_group_member_info":
                return self.get_group_member_info(_i("group_id"), _i("user_id"))
            if name == "get_stranger_info":
                return self.get_stranger_info(_i("user_id"))
            if name == "get_group_info":
                return self.get_group_info(_i("group_id"))
            if name == "get_group_msg_history":
                return self.get_group_msg_history(_i("group_id"), count=int(a.get("count", 20) or 20))
            if name == "get_user_status":
                return self.get_user_status(user_id=_i("user_id"))
            if name == "get_login_info":
                return self.get_login_info()
            if name == "set_group_ban":
                return self.set_group_ban(_i("group_id"), _i("user_id"), int(a.get("duration", 1800) or 1800))
            if name == "set_group_kick":
                return self.set_group_kick(_i("group_id"), _i("user_id"))
            if name == "set_group_admin":
                return self.set_group_admin(_i("group_id"), _i("user_id"), bool(a.get("enable", True)))
            if name == "set_group_name":
                return self.set_group_name(_i("group_id"), a.get("name", ""))
            if name == "set_group_leave":
                return self.set_group_leave(_i("group_id"))
            if name == "upload_group_file":
                return self.upload_group_file(_i("group_id"), a.get("file", ""), a.get("name", ""))
            if name == "get_group_root_files":
                return self.get_group_root_files(_i("group_id"))
            if name == "set_friend_add_request":
                return self.set_friend_add_request(a.get("flag", ""), bool(a.get("approve", True)), a.get("remark", ""))
            if name == "set_group_add_request":
                return self.set_group_add_request(a.get("flag", ""), a.get("sub_type", ""), bool(a.get("approve", True)))
            if name == "set_essence_msg":
                return self.set_essence_msg(_i("message_id"))
            if name == "get_essence_msg_list":
                return self.get_essence_msg_list(_i("group_id"))
            if name == "ocr_image":
                return self.ocr_image(a.get("image", ""))
            if name == "check_url_safely":
                return self.check_url_safely(a.get("url", ""))
            if name == "upload_private_file":
                return self.upload_private_file(_i("user_id"), a.get("file", ""), a.get("name", ""))
            if name == "get_group_file_url":
                return self.get_group_file_url(_i("group_id"), a.get("file_id", ""), _i("busid"))
            if name == "download_file":
                return self.download_file(a.get("url", ""))
            if name == "get_group_files_by_folder":
                return self.get_group_files_by_folder(_i("group_id"), a.get("folder_id", ""))
            if name == "get_group_file_system_info":
                return self.get_group_file_system_info(_i("group_id"))
            if name == "get_status":
                return self.get_status()
            if name == "get_version_info":
                return self.get_version_info()
            if name == "get_group_honor_info":
                return self.get_group_honor_info(_i("group_id"))
            if name == "get_record":
                return self.get_record(a.get("file", ""))
            if name == "get_image":
                return self.get_image(a.get("file", ""))
            if name == "can_send_image":
                return self.can_send_image()
            if name == "can_send_record":
                return self.can_send_record()
            if name == "get_word_slices":
                return self.get_word_slices(a.get("content", ""))
            if name == "get_forward_msg":
                return self.get_forward_msg(_i("message_id"))
            if name == "delete_essence_msg":
                return self.delete_essence_msg(_i("message_id"))
            if name == "set_qq_profile":
                return self.set_qq_profile(nickname=a.get("nickname", ""), personal_note=a.get("personal_note", ""))
            if name == "set_group_anonymous":
                return self.set_group_anonymous(_i("group_id"), bool(a.get("enable", True)))
            if name == "set_group_whole_ban":
                return self.set_group_whole_ban(_i("group_id"), bool(a.get("enable", True)))
            if name == "set_group_special_title":
                return self.set_group_special_title(_i("group_id"), _i("user_id"), a.get("special_title", ""))
            if name == "set_group_portrait":
                return self.set_group_portrait(_i("group_id"), a.get("file", ""))
            if name == "set_special_title":
                return self.set_special_title(group_id=_i("group_id"), user_id=_i("user_id"), special_title=a.get("special_title", ""))
            if name == "set_group_sign":
                return self.set_group_sign(group_id=_i("group_id"), sign=a.get("sign", ""))
            if name == "set_group_anonymous_ban":
                return self.set_group_anonymous_ban(_i("group_id"), anonymous_id=a.get("anonymous_id", ""), duration=int(a.get("duration", 1800) or 1800))
            if name == "get_group_info_ex":
                return self.get_group_info_ex(group_id=_i("group_id"))
            if name == "get_group_add_request":
                return self.get_group_add_request()
            if name == "set_group_remark":
                return self.set_group_remark(group_id=_i("group_id"), remark=a.get("remark", ""))
            if name == "set_group_add_option":
                return self.set_group_add_option(group_id=_i("group_id"), option=a.get("option", ""))
            if name == "set_group_search":
                return self.set_group_search(group_id=_i("group_id"), enable=bool(a.get("enable", True)))
            if name == "set_group_robot_add_option":
                return self.set_group_robot_add_option(group_id=_i("group_id"), option=a.get("option", ""))
            if name == "set_group_kick_members":
                return self.set_group_kick_members(group_id=_i("group_id"), user_id=a.get("user_ids", []))
            if name == "get_qun_album_list":
                return self.get_qun_album_list(group_id=_i("group_id"))
            if name == "get_group_album_media_list":
                return self.get_group_album_media_list(group_id=_i("group_id"), album_id=a.get("album_id", ""))
            if name == "upload_image_to_qun_album":
                return self.upload_image_to_qun_album(group_id=_i("group_id"), image=a.get("image", ""))
            if name == "do_group_album_comment":
                return self.do_group_album_comment(group_id=_i("group_id"), album_id=a.get("album_id", ""), content=a.get("content", ""))
            if name == "set_group_album_media_like":
                return self.set_group_album_media_like(group_id=_i("group_id"), album_id=a.get("album_id", ""), media_id=a.get("media_id", ""))
            if name == "del_group_album_media":
                return self.del_group_album_media(group_id=_i("group_id"), album_id=a.get("album_id", ""), media_id=a.get("media_id", ""))
            if name == "delete_qzone_msg":
                return self.call_api("delete_qzone_msg", {"tid": a.get("tid", "")})
            if name == "get_group_system_msg":
                return self.call_api("get_group_system_msg", {"count": int(a.get("count", 50) or 50)})
            if name == "get_group_shut_list":
                return self.call_api("get_group_shut_list", {"group_id": _i("group_id")})
            if name == "set_group_todo":
                return self.call_api("set_group_todo", {"group_id": _i("group_id"), "message_id": str(a.get("message_id", ""))})
            if name == "send_group_sign":
                return self.call_api("send_group_sign", {"group_id": _i("group_id")})
            if name == "get_group_signed_list":
                return self.call_api("get_group_signed_list", {"group_id": _i("group_id")})
            if name == "set_friend_remark":
                return self.call_api("set_friend_remark", {"user_id": _i("user_id"), "remark": a.get("remark", "")})
            if name == "get_friends_with_category":
                return self.call_api("get_friends_with_category", {})
            if name == "forward_friend_single_msg":
                return self.call_api("forward_friend_single_msg", {"message_id": str(a.get("message_id", "")), "user_id": _i("user_id")})
            if name == "forward_group_single_msg":
                return self.call_api("forward_group_single_msg", {"message_id": str(a.get("message_id", "")), "group_id": _i("group_id")})
            if name == "mark_private_msg_as_read":
                return self.call_api("mark_private_msg_as_read", {"user_id": _i("user_id")})
            if name == "mark_group_msg_as_read":
                return self.call_api("mark_group_msg_as_read", {"group_id": _i("group_id")})
            if name == "get_emoji_likes":
                return self.call_api("get_emoji_likes", {"message_id": _i("message_id")})
            if name == "fetch_emoji_like":
                return self.call_api("fetch_emoji_like", {"message_id": _i("message_id"), "emoji_id": a.get("emoji_id", "")})
            if name == "fetch_ptt_text":
                return self.call_api("fetch_ptt_text", {"message_id": _i("message_id")})
            if name == "get_recent_contact":
                p = {}
                if a.get("count"):
                    p["count"] = int(a.get("count"))
                return self.call_api("get_recent_contact", p)
            if name == "translate_en2zh":
                return self.call_api("translate_en2zh", {"words": a.get("words", [])})
            if name == "set_diy_online_status":
                return self.call_api("set_diy_online_status", {"face_id": str(a.get("face_id", "")), "face_type": str(a.get("face_type", "")), "wording": a.get("wording", "")})
            if name == "send_group_forward_msg":
                return self.send_group_forward_msg(_i("group_id"), a.get("messages", []))
            if name == "send_private_forward_msg":
                return self.send_private_forward_msg(_i("user_id"), a.get("messages", []))
        except (SendUnconfirmedError, SendCancelledError):
            raise
        except Exception as e:
            self._log("error", "qq", f"工具 {name} 执行失败: {e}")
        return None

    # ---- 事件分发 ----
    def _on_event(self, event):
        if event.get('post_type') != 'meta_event' and 'qq' not in self.store.get_settings().get('enabled_platforms', []):
            return
        pt = event.get("post_type")
        if pt in ('message_sent','delivery_ack'):
            self._delivery_receipt(event)
        elif pt == "message":
            self._on_message(event)
        elif pt == "notice":
            self._on_notice(event)
        elif pt == "request":
            self._on_request(event)
        elif pt == "meta_event":
            self._on_meta(event)

    def _on_meta(self, event):
        if event.get("meta_event_type") == "lifecycle" and event.get("sub_type") == "connect":
            account_id = event.get("self_id")
            if account_id:
                # 顺手记住自己的 QQ 号，供掉线后 -q 快速登录自愈用
                try:
                    self.store.set_setting("qq_last_account", str(account_id))
                    if self.store.get_setting_raw("qq_login_target") is None:
                        self.store.set_setting("qq_quick_login_uin", str(account_id))
                except Exception:
                    logging.exception("QQ 快捷登录号保存失败")
                self._log("info", "status", f"QQ 账号 {account_id} 已建立 OneBot 会话")

    def _update_account_state(self, status):
        if isinstance(status, dict) and isinstance(status.get("online"), bool):
            self.account_online = status["online"]
            self._account_status_at = time.time()
            self._account_status_self_id = self.self_id

    # ---- 回复决策：私聊/群@必回，群里其他人走主动插话漏斗 ----
    def _is_at_me(self, message):
        for seg in message or []:
            if isinstance(seg, dict) and seg.get("type") == "at" and str(seg.get("data", {}).get("qq", "")) == str(self.self_id):
                return True
        return False

    def _is_at_others(self, message):
        for seg in message or []:
            if isinstance(seg, dict) and seg.get("type") == "at" and str(seg.get("data", {}).get("qq", "")) != str(self.self_id):
                return True
        return False

    def _memory_scope(self, msg_type, user_id, group_id, settings):
        owner = str(settings.get("owner_qq", "") or "")
        if msg_type == "group":
            return f"group:{group_id}"
        if str(user_id) == owner:
            return "owner"
        return f"private:{user_id}"

    def _should_reply_quick(self, msg_type, message):
        """免费的可靠判断：私聊/@我 → 'force'；@别人 → None；其它 → 'maybe'（交给 LLM）。"""
        if msg_type == "private":
            return "force"
        if self._is_at_me(message):
            return "force"
        if self._is_at_others(message):
            return None
        return "maybe"

    def _on_message(self, event):
        if 'qq' not in self.store.get_settings().get('enabled_platforms', []):
            return
        memory_epoch = self.store.get_setting_raw('memory_epoch') or 0
        msg_type = event.get("message_type")
        user_id = event.get("user_id")
        group_id = event.get("group_id")
        message_id = event.get("message_id")
        sender = event.get("sender", {}) or {}
        nickname = sender.get("card") or sender.get("nickname") or str(user_id)
        message = event.get("message")
        conversation = self._event_session(event)
        version = event.get('_dfy_version', self._conversation_versions.get(conversation,0))

        text = self._extract_text(message)
        if not text:
            self._log("info", "chat", f"[QQ] {msg_type} {nickname}: （非文字消息）")
            return

        # 统一存 + 记日志：所有消息（含非 @ 群消息）都进短期上下文 + 聊天日志，带渠道标签
        session = f"private_{user_id}" if msg_type == "private" else f"group_{group_id}"
        channel = f"[群 {group_id}]" if msg_type == "group" else "[私聊]"
        written_user = redact_secrets(f"{channel} {nickname}: {text}")
        try:
            self.store.add_short_term(session, "user", written_user, platform="qq")
        except Exception:
            pass
        who = f"私聊 {nickname}" if msg_type == "private" else f"群 {group_id} {nickname}"
        self._log("info", "chat", f"[QQ] {who}: {text}")

        # 免费的可靠判断：私聊/@我 必回、@别人 不回；其它交给 LLM
        reply_mode = self._should_reply_quick(msg_type, message)
        settings = self.store.get_settings()
        scope = self._memory_scope(msg_type, user_id, group_id, settings)
        entity_id = f"qq:{user_id}"
        event_memory_id = None
        # Capture original user evidence before fetched web/image content can alter text.
        if self.consolidate and reply_mode is not None:
            try:
                event_memory_id = self.consolidate(nickname, text, platform="qq", scope=scope,
                                 entity_id=entity_id,
                                 event_key=f"qq:{session}:{message_id}" if message_id is not None else None,
                                 occurred_at=event.get('time'))
            except Exception as exc:
                self._log("error", "memory", f"事件记忆写入失败: {exc}")
        if reply_mode is None:
            return
        if version != self._conversation_versions.get(conversation,0):
            self._log('info','action','同会话已有更新消息，旧消息保留记忆但不再单独回复')
            return

        # 图片：下载 → vision 看图（失败 OCR 兜底）
        image_bytes, image_mime = self._get_image_bytes(message)
        if image_bytes:
            image_desc, sticker_emotion = self._vision_or_ocr(image_bytes, image_mime, message)
            if image_desc:
                text = f"{text}\n（图片：{image_desc}）" if text else image_desc
            # 表情包收集：只收 GIF，SHA256 去重，受控情绪词当标签
            if image_mime and "gif" in image_mime:
                try:
                    self._collect_sticker(image_bytes, image_mime, sticker_emotion or "")
                except Exception:
                    pass

        # 链接：抓取 URL 内容（最多 2 个）
        for u in self._extract_urls(message, text)[:2]:
            content = self._fetch_url_content(u)
            if content:
                text += f"\n（链接 {u}：{content}）"

        reply_id = self._extract_reply_id(message)
        reply_nick, reply_text = (None, None)
        if reply_id:
            reply_nick, reply_text = self._fetch_reply(reply_id)

        # 头像：私聊取对方；群聊取对方 + 群头像（语境信息，进 system，不进 user turn）
        avatar_desc = self._get_avatar_desc(user_id)
        group_avatar = ""
        if msg_type == "group":
            group_avatar = self._get_group_avatar_desc(group_id)

        try:
            settings = self.store.get_settings()
            scope = self._memory_scope(msg_type, user_id, group_id, settings)

            # 短期上下文（按隐私层级：主人全渠道、群只看本群、私聊只看本人）
            history = self.store.get_short_term(scope, limit=SHORT_TERM_LIMIT, with_metadata=True)
            # 去掉刚写入 short_term 的「当前这条」：它下面会作为 final user turn 再给一次，
            # 否则同一条消息在上下文里出现两遍，LLM 会误以为对方发了两遍
            if history and history[-1]["role"] == "user" and history[-1]["content"] == written_user:
                history = history[:-1]

            # 长期记忆（按相关性 + scope 软偏好召回，带身份标签）
            recall = self.memory_service.prepare(text, history, scope, entity_id, session,
                                                 exclude_ids=(event_memory_id,))
            self._log('info', 'memory_lookup', json.dumps(dict(
                version=MemoryService.VERSION, status=recall['status'], search_status=recall['search_status'],
                candidates=recall['candidate_count'], ids=list(recall['_versions'])), ensure_ascii=False))

            weather_text = weather.get_weather(settings.get("city", ""))
            mood_text = self.store.get_life_state().get("mood", "")
            sticker_names = [s["tag"] for s in self.store.get_stickers(limit=30) if s.get("tag")]
            sys_content = build_system_prompt(settings, weather=weather_text, mood=mood_text)
            if not self._self_avatar_desc and self.self_id:
                self._self_avatar_desc = self._get_avatar_desc(self.self_id)
            sys_content += '\n' + READ_RULES

            # 当前对话语境（自然语气，放 system 而非 user turn，避免表单感）
            context_parts = []
            if msg_type == "private":
                context_parts.append(f"你在跟「{nickname}」私聊。")
            else:
                context_parts.append(f"你在群里，正在说话的是「{nickname}」。")
            if avatar_desc:
                context_parts.append(f"对方头像：{avatar_desc}")
            if group_avatar:
                context_parts.append(f"群头像：{group_avatar}")
            if reply_text:
                if reply_nick:
                    context_parts.append(f"「{nickname}」在回 {reply_nick} 的这句：「{reply_text}」")
                else:
                    context_parts.append(f"「{nickname}」在回这句：「{reply_text}」")
            chat_messages = [{"role": "system", "content": sys_content}]
            chat_messages.append(data_message(dict(
                current_person=person_code(entity_id), scope=scope, recall=self.memory_service.data(recall),
                history_metadata=[dict(index=i, time=h.get('ts'), session=h.get('session'))
                                  for i,h in enumerate(history)],
                context=context_parts, self_avatar=self._self_avatar_desc,
                available_stickers=sticker_names)))
            for h in history:
                chat_messages.append({"role": h["role"], "content": h['content']})
            chat_messages.append({"role": "user", "content": text})

            # 群消息（非 @ 别人）→ LLM 判断该不该回
            if reply_mode == "maybe" and msg_type == "group":
                if not self.brain.should_reply(chat_messages):
                    return

            publish_tool = {
                "type": "function",
                "function": {
                    "name": "publish",
                    "description": "发一条 QQ 空间动态（说说）。用户明确让你发动态/发说说/发空间时用。detail 是【要发到空间的动态正文】，不是回复用户的话。",
                    "parameters": {
                        "type": "object",
                        "properties": {"detail": {"type": "string", "description": "要发到空间的动态正文"}},
                        "required": ["detail"],
                    },
                },
            }
            # 说话走纯文本（人设活在语言层），工具只做真动作（发空间）
            if self.request_approval:
                result = self.brain.chat_with_tools(
                    chat_messages, [publish_tool],
                    temperature=REPLY_TEMPERATURE,
                    frequency_penalty=REPLY_FREQUENCY_PENALTY,
                    presence_penalty=REPLY_PRESENCE_PENALTY,
                )
            else:
                result = {
                    "content": self.brain.chat(
                        chat_messages,
                        temperature=REPLY_TEMPERATURE,
                        frequency_penalty=REPLY_FREQUENCY_PENALTY,
                        presence_penalty=REPLY_PRESENCE_PENALTY,
                    ),
                    "tool_calls": [],
                }
            if (not self.memory_service.valid(recall)
                    or memory_epoch != (self.store.get_setting_raw('memory_epoch') or 0)
                    or render_identity(settings) != render_identity(self.store.get_settings())
                    or 'qq' not in self.store.get_settings().get('enabled_platforms', [])):
                return
            published = False
            for tc in result.get("tool_calls", []) or []:
                if tc.get("name") == "publish" and self.request_approval:
                    detail = ((tc.get("arguments") or {}).get("detail") or "").strip()
                    if detail:
                        self.request_approval("publish", detail)
                        published = True
            reply = (result.get("content") or "").strip()
            if published and not reply:
                reply = "（发动态的请求已提交，去 app 审批页同意一下）"
            # 按空行拆成多条（正文自然分段 → 多条消息）
            msgs = [p.strip() for p in re.split(r"\n\s*\n", reply) if p.strip()]
        except Exception as e:
            self._log("error", "error", f"LLM 回复失败: {e}")
            return

        if not msgs:
            return  # 空回复（罕见）：不发送、不写记忆
        if (not self.memory_service.valid(recall)
                or memory_epoch != (self.store.get_setting_raw('memory_epoch') or 0)
                or render_identity(settings) != render_identity(self.store.get_settings())):
            return

        # 回复：text/face 混合段逐条发；群聊只在第一条 @
        self._send_segments(group_id if msg_type == "group" else user_id, msgs, msg_type,
                            at_user=(user_id if msg_type == "group" else None),
                            guard=lambda: self.memory_service.valid(recall)
                            and memory_epoch == (self.store.get_setting_raw('memory_epoch') or 0)
                            and render_identity(settings) == render_identity(self.store.get_settings())
                            and version == self._conversation_versions.get(conversation,0))
        self._log("info", "chat", f"[QQ] 回复 {nickname}: {reply}")
        self.memory_service.remember(recall)

        # 短期上下文回写：只补助手回复（user 那条统一存时已写）
        # Each part is already journaled and reflected into short_term, even on interruption.

        # Only a completed send is evidence of our actual behaviour. Extraction stays asynchronous.
        if event_memory_id is not None:
            try:
                self.store.record_self_experience(reply, scope, event_memory_id)
            except Exception as exc:
                self._log("error", "memory", f"自身经历写入失败: {exc}")

    # ---- 头像 ----
    def _get_avatar_desc(self, user_id):
        """拉对方 QQ 头像并 vision 成一句话描述（按 user_id 缓存）。失败返回空串。"""
        if user_id in self._avatar_cache:
            return self._avatar_cache[user_id]
        try:
            url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qun.qq.com/"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                img = resp.read()
                mime = resp.headers.get("Content-Type", "image/png")
            desc = (self.brain.vision(img, "描述这个人的QQ头像长什么样（一句话）", mime) or "").strip()
        except Exception:
            desc = ""
        self._avatar_cache[user_id] = desc
        return desc

    def _get_group_avatar_desc(self, group_id):
        """拉群头像并 vision 成一句话描述（按 group_id 缓存）。失败返回空串。"""
        cache_key = f"group_{group_id}"
        if cache_key in self._avatar_cache:
            return self._avatar_cache[cache_key]
        try:
            url = f"https://p.qlogo.cn/gh/{group_id}/{group_id}/640"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qun.qq.com/"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                img = resp.read()
                mime = resp.headers.get("Content-Type", "image/png")
            desc = (self.brain.vision(img, "描述这个QQ群头像长什么样（一句话）", mime) or "").strip()
        except Exception:
            desc = ""
        self._avatar_cache[cache_key] = desc
        return desc

    # ---- 图片下载 ----
    def _get_image_bytes(self, message):
        """Try independent image sources; a broken URL must not bypass local files."""
        if not isinstance(message, list):
            return None, None
        data = next(((s.get('data') or {}) for s in message
                     if isinstance(s, dict) and s.get('type') == 'image'), None)
        if data is None:
            return None, None
        failures = []
        def attempt(label, fn, *args):
            try:
                return fn(*args)
            except Exception as exc:
                # Do not put signed URLs, image keys or private paths into logs.
                code = getattr(exc, 'code', '')
                failures.append(f'{label}:{type(exc).__name__}' + (f' HTTP {code}' if code else ''))
                return None
        file_ref = data.get('file', '')
        # Local originals survive NapCat restarts and third-party Rkey outages.
        result = attempt('cache', qq_media.cached_image, file_ref)
        if result:
            return result
        if data.get('url'):
            result = attempt('message_url', qq_media.download, data['url'])
            if result:
                return result
        if file_ref:
            response = attempt('get_image_call',self.get_image,file_ref)
            info = (response or {}).get('data') or {}
            if not response or response.get('retcode') != 0:
                failures.append('get_image:retcode=' + str((response or {}).get('retcode', 'no_ack')))
            for key in ('file', 'file_path', 'path'):
                if info.get(key):
                    result = attempt('api_'+key, qq_media.local_image, info[key])
                    if result:
                        return result
            if info.get('url'):
                result = attempt('api_url', qq_media.download, info['url'])
                if result:
                    return result
            result = attempt('cache_after_api', qq_media.cached_image, file_ref)
            if result:
                return result
        self._log('error','error','图片下载失败（' + '; '.join(failures) + '）')
        return None, None

    def _vision_or_ocr(self, image_bytes, mime, message):
        """看图：先 vision 模型（返回描述+情绪标签），失败再 NapCat OCR 兜底。返回 (desc, emotion)。"""
        try:
            raw = self.brain.vision(
                image_bytes,
                f"描述这张图，用第一人称、像真人一样简短地回应；然后从这些情绪词里选一个最贴切的：{'/'.join(STICKER_EMOTIONS)}，格式「情绪:开心」",
                mime,
            ) or ""
            emotion = ""
            m = re.search(r"情绪[:：]\s*([^\s，。；]+)", raw)
            if m:
                cand = m.group(1).strip()
                if cand in STICKER_EMOTIONS:
                    emotion = cand
                raw = raw[:m.start()].strip()
            if raw:
                return raw, emotion
        except Exception as e:
            self._log("error", "error", f"vision 看图失败: {e}")

        file_ref = self._extract_image_file(message)
        if file_ref:
            try:
                r = self.ocr_image(file_ref)
                texts = ((r or {}).get("data", {}) or {}).get("texts", []) or []
                ocr = "\n".join(t.get("text", "") for t in texts if isinstance(t, dict)).strip()
                if ocr:
                    return f"（图片文字：{ocr}）", ""
            except Exception as e:
                self._log("error", "error", f"OCR 兜底失败: {e}")
        return "", ""

    # ---- 链接抓取 ----
    def _extract_urls(self, message, text):
        """从正文和消息段里抽 http(s) URL，去重保序。"""
        urls = []
        if text:
            urls += _URL_RE.findall(text)
        if isinstance(message, list):
            for seg in message:
                if not isinstance(seg, dict):
                    continue
                if seg.get("type") == "image":
                    continue  # 图片 URL 不是网页，不抓
                d = seg.get("data", {}) or {}
                for k in ("url", "link", "target"):
                    v = d.get(k)
                    if isinstance(v, str) and v.startswith("http"):
                        urls.append(v)
                if seg.get("type") == "json" and isinstance(d.get("data"), str):
                    urls += _URL_RE.findall(d["data"])
        seen, out = set(), []
        for u in urls:
            u = u.rstrip(".,;!?，。；！？)")
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _fetch_url_content(self, url):
        """抓整页正文（title + description + body），去 HTML，截断到 3000 字符。失败返回 ''。"""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read(2 * 1024 * 1024)
                ctype = (resp.headers.get("Content-Type", "") or "").lower()
            html = self._decode_html(raw, ctype)
            return self._html_to_text(html)[:3000]
        except Exception as e:
            self._log("error", "error", f"抓链接失败 {url}: {e}")
            return ""

    def _decode_html(self, raw, ctype):
        charset = None
        m = re.search(r'charset=["\']?([\w-]+)', ctype)
        if m:
            charset = m.group(1)
        if not charset:
            m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.IGNORECASE)
            if m:
                charset = m.group(1).decode("ascii", "ignore")
        for enc in (charset, "utf-8", "gbk", "gb18030", "latin-1"):
            if not enc:
                continue
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "ignore")

    def _html_to_text(self, html):
        html = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', html)
        title = ""
        m = re.search(r'(?is)<title[^>]*>(.*?)</title>', html)
        if m:
            title = re.sub(r'\s+', ' ', m.group(1)).strip()
        desc = ""
        m = re.search(r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html)
        if not m:
            m = re.search(r'(?is)<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', html)
        if m:
            desc = re.sub(r'\s+', ' ', m.group(1)).strip()
        m = re.search(r'(?is)<body[^>]*>(.*)</body>', html)
        body_html = m.group(1) if m else html
        body = re.sub(r'(?is)<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>', '\n', body_html)
        body = re.sub(r'(?is)<[^>]+>', ' ', body)
        body = re.sub(r'&nbsp;?', ' ', body)
        body = re.sub(r'&amp;', '&', body)
        body = re.sub(r'&lt;', '<', body)
        body = re.sub(r'&gt;', '>', body)
        body = re.sub(r'&#\d+;', ' ', body)
        body = re.sub(r'[ \t]+', ' ', body)
        body = re.sub(r'\n\s*\n+', '\n', body)
        parts = [p for p in (title, desc, body) if p and p.strip()]
        return "\n".join(parts).strip()

    def _on_notice(self, event):
        ntype = event.get("notice_type")
        sub = event.get("sub_type")
        uid = event.get("user_id")
        gid = event.get("group_id")
        if ntype == "notify" and sub == "poke":
            # NapCat 私聊 user_id 是会话对象；sender_id 才是实际发起者。
            in_group = gid not in (None, 0, "0", "")
            # 群通知的 user_id 是标准发起者字段；私聊只信 sender_id。
            actor = uid if in_group else event.get("sender_id")
            target = event.get("target_id")
            own_id = event.get("self_id") or self.self_id

            def label(account):
                if account in (None, 0, "0", ""):
                    return "未知用户"
                return "我" if own_id and str(account) == str(own_id) else str(account)

            channel = f"群 {gid}" if in_group else "私聊"
            self._log("info", "notice", f"[QQ] {label(actor)} 戳了戳 {label(target)}（{channel}）")
        elif ntype == "group_recall":
            self._log("info", "notice", f"[QQ] 群 {gid} 有消息被 {uid} 撤回")
        elif ntype == "group_increase":
            self._log("info", "notice", f"[QQ] {uid} 加入群 {gid}")
        elif ntype == "group_decrease":
            self._log("info", "notice", f"[QQ] {uid} 离开群 {gid}")
        else:
            self._log("info", "notice", f"[QQ] notice {ntype}/{sub} u{uid} g{gid}")

    def _on_request(self, event):
        rtype = event.get("request_type")
        uid = event.get("user_id")
        gid = event.get("group_id")
        comment = event.get("comment", "")
        summary = f"QQ 好友申请 {uid}" if rtype == "friend" else f"QQ 群邀请 {uid}（群 {gid}）"
        if comment:
            summary += f"：{comment}"
        self._log("info", "request", summary)
        if self.request_approval:
            self.request_approval(rtype, json.dumps({"summary": summary,
                "flag": event.get("flag"), "sub_type": event.get("sub_type")}, ensure_ascii=False))

    # ---- 提取 ----
    def _extract_text(self, message):
        if isinstance(message, str):
            return message.strip()
        parts = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            t = seg.get("type")
            d = seg.get("data", {}) or {}
            if t == "text":
                parts.append(d.get("text", ""))
            elif t == "image":
                parts.append("[图片]")
            elif t == "face":
                parts.append("[表情]")
            elif t == "at":
                parts.append("@" + str(d.get("qq", "")))
            elif t == "record":
                parts.append("[语音]")
            elif t == "video":
                parts.append("[视频]")
            elif t == "file":
                parts.append("[文件]")
            elif t == "forward":
                parts.append("[转发]")
            elif t == "contact":
                parts.append("[名片]")
            elif t == "share":
                parts.append("[链接]")
            elif t == "poke":
                parts.append("[戳一戳]")
            elif t == "location":
                parts.append("[位置]")
            elif t == "music":
                parts.append("[音乐]")
            elif t in ("dice", "rps", "shake"):
                parts.append("[骰子/猜拳]")
            # reply 段不并入正文，单独处理
        return "".join(parts).strip()

    def _extract_image_file(self, message):
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "image":
                    return (seg.get("data", {}) or {}).get("file", "")
        return ""

    def _extract_reply_id(self, message):
        if not isinstance(message, list):
            return None
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "reply":
                return (seg.get("data", {}) or {}).get("id")
        return None

    def _fetch_reply(self, reply_id):
        try:
            resp = self.call_api("get_msg", {"message_id": reply_id}, timeout=8)
            if resp and resp.get("status") == "ok":
                data = resp.get("data", {}) or {}
                sender = data.get("sender", {}) or {}
                nick = sender.get("card") or sender.get("nickname") or ""
                quoted = self._extract_text(data.get("message"))
                return (nick, quoted)
        except Exception as e:
            self._log("error", "error", f"拉取引用消息失败: {e}")
        return (None, None)
