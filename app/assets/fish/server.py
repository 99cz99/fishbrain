# server.py — Fishbrain 鱼脑的本地 API（HTTP + 轮询），给监管 app 用
# 阶段 0：骨架。暴露 状态/日志/聊天记录/记忆/设定/审批/命令，全部落到 memory.Store。
import base64
import hashlib
import json
import os
import re
import secrets
import hmac
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import Store
from brain import Brain
from autonomy import Autonomy
from qq_bot import STICKER_DIR
from memory_policy import action_outcome
from delivery import SendUnconfirmedError
from qq_config import delivery_config
from qq_control import QQController, QQBackend, ProcessManager, WebUIClient

# NapCat 二维码文件路径（proot-Debian 容器内，映射到 Termux 文件系统）
DEFAULT_QRCODE_PATH = "/data/data/com.termux/files/usr/var/lib/proot-distro/containers/debian/rootfs/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png"
# NapCat 配置目录（onebot11_<uin>.json 放这里，容器内 napcat/config/）
NAPCAT_CONFIG_DIR = "/data/data/com.termux/files/usr/var/lib/proot-distro/containers/debian/rootfs/opt/QQ/resources/app/app_launcher/napcat/config"
# NapCat WebUI（默认 127.0.0.1:6099）：快捷登录 + 设备指纹（webui.json 里存 token）
NAPCAT_WEBUI_URL = "http://127.0.0.1:6099"
NAPCAT_WEBUI_CONFIG = NAPCAT_CONFIG_DIR + "/webui.json"

# Termux 环境：鱼脑经 run-as 启动时 PATH 是系统 PATH，proot-distro 得手动找
TERMUX_BIN = "/data/data/com.termux/files/usr/bin"
PROOT_DISTRO = TERMUX_BIN + "/proot-distro"
QQ_WS_PORT = 8081
# QQ(NapCat) 启动/停止命令，跑在 proot-Debian 容器内
def _qq_start_cmd(uin=None):
    """拼 QQ 启动命令（跑 proot-Debian 容器内）。uin 为纯数字时追加 `-q <uin>` 走快速登录（掉线自愈）。"""
    ql = f"-q {str(uin).strip()} " if (uin is not None and str(uin).strip().isdigit()) else ""
    return ("mkdir -p /root/napcat && cd /opt/QQ && "
            "xvfb-run -e /root/napcat/xvfb.log -a ./qq --no-sandbox --disable-gpu "
            "--disable-dev-shm-usage --no-zygote --use-gl=swiftshader "
            + ql +
            ">> /root/napcat/qq.log 2>&1; "
            "echo QQ_EXIT_CODE=$? >> /root/napcat/qq.log")
# 停止交给 QQController：核对 PID/启动时间，先退出原生进程，再清理专属会话组。
# 两次自动拉起 QQ 的最小间隔（秒）：防频繁重登被 QQ 风控
QQ_RESTART_COOLDOWN = 120
# 鱼脑刚启动的宽限期（秒）：此窗口内 qq_keep_alive 不拉起/判断，给 QQ 的 WS 重连留时间
QQ_KEEPALIVE_GRACE_SECONDS = 120
# QQ 扫码二维码真实有效期（秒）：QQ 的码约 1~2 分钟就失效，别按 15 分钟判断
QQ_QR_VALID_SECONDS = 90
# QQ 登录态诊断：qq.log 宿主路径（鱼脑以 Termux UID 可直接读，与 qrcode 同机制）
QQ_LOG_PATH = ("/data/data/com.termux/files/usr/var/lib/proot-distro/containers/debian/"
               "rootfs/root/napcat/qq.log")
# 「进程活着但已掉线」的宽限期（秒）：避开 linuxqq 正常启动/扫码/NapCat 自愈窗口，别抢重启
QQ_STALE_GRACE_SECONDS = 300
# 「掉线但进程残留」时的自愈/日志节流间隔（秒）。
# 配了快捷登录号（qq_quick_login_uin）时是 -q 自愈的冷却；没配或属硬掉线时只是日志节流。
QQ_STALE_RESTART_COOLDOWN = 900
# 连续自动拉起 N 次仍无法稳定在线 → 熔断（区别 cooldown：cooldown 是单次间隔，熔断是连续失败后的长暂停）
QQ_AUTO_MAX_FAIL = 5
# 熔断时长（秒）
QQ_AUTO_CIRCUIT_SECONDS = 1800
# 持续在线这么久才视为「环境已恢复」，才清零熔断计数（覆盖「短暂在线又崩」）
QQ_AUTO_STABLE_SECONDS = 300
# onebot11_<uin>.json 只有在最近这个秒数内生成，才算「刚扫码登录」（旧文件跨会话残留，不算）
QQ_LOGIN_UIN_FRESH_SECONDS = 600


def _qq_env():
    env = os.environ.copy()
    env["PATH"] = TERMUX_BIN + ":" + TERMUX_BIN + "/applets:" + env.get("PATH", "")
    env["PROOT_NO_SECCOMP"] = "1"  # workaround proot 5.1.107.99 的 seccomp 回归导致段错误
    return env


def _qq_process_alive():
    scan = ProcessManager().scan()
    return any(row['role'] == 'main' for row in scan['rows'])


def _qq_qr_fresh(path):
    """二维码文件存在且是新生成的（<QQ_QR_VALID_SECONDS）= 正等扫码登录。"""
    try:
        return os.path.exists(path) and (time.time() - os.path.getmtime(path)) < QQ_QR_VALID_SECONDS
    except Exception:
        return False


def _sniff_ext(data):
    """从字节魔数嗅探图片扩展名。"""
    if data[:3] == b"GIF":
        return "gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    return "gif"


def _download_url(url):
    """下载图片 URL，返回 (bytes, ext)；失败/超限返回 (b'', '')。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            if len(data) > 5 * 1024 * 1024:
                return b"", ""
            return data, _sniff_ext(data)
    except Exception:
        return b"", ""


def _parse_qq_log_state():
    """解析 qq.log 尾部（约 64KB），归类 QQ 掉线根因（固定优先级，非「最近事件」）。
    优先级：NT 内核超时 > 账号离线 > 等扫码 > 风控 > 登录报错码。
    ErrCode 无官方语义、只中性保留码号；风控只看明确文案（人脸/安全验证/冻结等）。
    已知边界：64KB 尾里可能残留较旧信号（旧「Timeout」可能盖过新「待扫码」）。"""
    try:
        with open(QQ_LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", "ignore")
    except Exception:
        return "unknown"
    # 掉线触发源：QQ NT 内核超时（sendMsg/sendPacket timeout）
    if "Timeout: NTEvent" in tail or "sendPacket 超时" in tail:
        return "nt_timeout"
    # 掉线事件：NapCat 检测到账号离线
    if "账号已离线" in tail or "账号状态变更为离线" in tail:
        return "offline"
    # 登录服务断连（扫码后还没确认就掉线，登录失败死循环）
    if "登录服务已断开" in tail:
        return "login_service_dropped"
    # 等待扫码
    if "请扫描下面的二维码" in tail:
        return "waiting_scan"
    # 明确的风控/安全拦截文案（不是 ErrCode）
    if "人脸" in tail or "安全验证" in tail or "冻结" in tail or "为了你的账号安全" in tail:
        return "risk"
    # 登录服务报错：ErrCode 无官方定义，原样保留码号（取最近一次）
    codes = re.findall(r"ErrCode:\s*(\d+)", tail)
    if codes:
        return f"login_error_{codes[-1]}"
    return "unknown"


def load_config(path):
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


class Fish:
    def __init__(self, cfg):
        server_cfg = cfg.get("server", {})
        self.store = Store(server_cfg.get("db_path", "fish.db"))
        self.brain = Brain(
            get_config=lambda: self.store.get_settings().get("llm", {}),
            on_usage=self.store.add_usage,
        )
        self.qq_cfg = cfg.get("qq", {})
        self.heybox_cfg = cfg.get("heybox", {})
        self.heybox = None                  # 小黑盒适配器，_build_heybox 里注入
        self.started = time.time()
        self.state = "idle"       # idle / working / paused
        self.current_task = ""
        self._qq_auto = True              # 允许自动拉起 QQ（用户显式停后置 False）
        self._last_qq_auto_start = 0.0    # 上次自动拉起的时间戳（冷却用）
        self._qq_scan_logged = False      # 「需扫码」只记一次日志
        self._last_qq_online_ts = time.time()    # WS 上次在线时间（掉线宽限期基准）
        self._last_qq_stale_restart_ts = 0.0     # 上次因「掉线残留」重启的时间戳
        self._qq_auto_fail_count = 0        # 连续自动拉起但未稳定在线的次数
        self._qq_auto_circuit_until = 0.0   # 熔断截止时间戳（0=未熔断）
        self._qq_online_since = 0.0         # 当前在线起点（0=现在不在线）
        self._webui_cred_val = None         # WebUI /api/auth/login 的 Credential 缓存
        self._webui_cred_ts = 0.0
        self._self_healing = False          # 自动快捷登录是否在途
        self._self_healing_ts = 0.0
        self._last_guid_check = 0.0         # 上次设备指纹捕获/比对时间戳（节流）
        self._controller = None
        self._qq_controller().start()
        self.deployer = Deployer(self.store)
        self.autonomy = Autonomy(self.store, self.brain,
                                 request_approval=self.request_approval,
                                 qq_keep_alive=self.qq_keep_alive)
        self.autonomy.start()
        self.log("info", "status", "鱼脑已启动")

    def log(self, level, category, message):
        self.store.add_log(level, category, message)

    def request_approval(self, kind, summary):
        aid = self.store.add_approval(kind, summary)
        self.log("approval", "approval", f"请求审批[{kind}]: {summary}")
        return {"id": aid, "kind": kind, "summary": summary, "status": "pending"}

    def execute_approval(self, aid, approve=True):
        """审批同意后，按 kind 执行实际动作（发帖/评论等，后台线程跑）。"""
        ap = self.store.get_approval(aid)
        if not ap:
            return
        kind = ap.get("kind", "")
        platform = 'heybox' if kind.startswith('heybox_') else 'qq'
        if platform not in self.store.get_settings().get('enabled_platforms', []):
            raise ValueError('该平台已禁用，未执行动作')
        if kind not in ('friend','group','publish','heybox_post','heybox_comment'):
            raise ValueError('该审批类型没有可用执行器')
        if kind.startswith('heybox_') and not self.heybox:
            raise ValueError('小黑盒未接入，未执行动作')
        try:
            args = json.loads(ap.get("summary", "") or "{}")
        except Exception:
            args = {}
        if kind in ("friend", "group"):
            if not args.get("flag"):
                raise ValueError("旧申请缺少平台标识，请重新发起申请")
            if not self.qq_bot:
                raise ValueError("QQ 未连接")
            if kind == "friend":
                result = self.qq_bot.set_friend_add_request(args["flag"], approve=approve)
            else:
                result = self.qq_bot.set_group_add_request(args["flag"], args.get("sub_type", ""), approve=approve)
            if not result or result.get("retcode") != 0:
                raise ValueError("平台未确认申请处理结果，请先核实 QQ 状态再操作")
            self.store.set_execution_state(aid, 'confirmed')
        elif kind == "heybox_post" and self.heybox:
            self.log("info", "approval", f"执行发帖: {args.get('title', '')}")
            threading.Thread(target=self._execute_recorded_action,
                             args=(aid, kind, self.heybox.post, (args.get("title", ""), args.get("content", "")), 'heybox'),
                             daemon=True).start()
        elif kind == "heybox_comment" and self.heybox:
            self.log("info", "approval", f"执行评论: {args.get('content', '')}")
            threading.Thread(target=self._execute_recorded_action,
                             args=(aid, kind, self.heybox.comment, (args.get("link_id", 0), args.get("content", "")), 'heybox'),
                             daemon=True).start()
        elif kind == "publish":
            text = (ap.get("summary") or "").strip()
            if text and self.qq_bot:
                self.log("info", "approval", f"执行发空间动态: {text}")
                threading.Thread(target=self._execute_recorded_action,
                                 args=(aid, kind, self.qq_bot.send_qzone_mood, (text,), 'qq'), daemon=True).start()
            else:
                raise ValueError('发空间动态失败：内容为空或 QQ 未连接')

    def _execute_recorded_action(self, aid, kind, function, args, platform):
        self.store.set_execution_state(aid, 'running')
        outcome = 'failed_or_partial'
        try:
            if platform not in self.store.get_settings().get('enabled_platforms', []):
                raise ValueError('平台已禁用')
            outcome = action_outcome(function(*args))
        except SendUnconfirmedError:
            outcome = 'unconfirmed'
        except Exception as exc:
            self.log('error', 'approval', f'审批动作执行失败: {type(exc).__name__}')
        finally:
            self.store.set_execution_state(aid, outcome)
            # The approval authorizes public content; diagnostics and returned credentials are not evidence.
            self.store.record_action_result(kind, json.dumps(args, ensure_ascii=False), outcome, platform,
                                            scope='global', event_key=f'approval-result:{aid}')

    def _build_heybox(self):
        """按当前 token 重建小黑盒适配器（token 空则禁用）。

        SQLite 为权威；config.toml 旧值只在「从未在 app 里设置过」时回退一次并迁移落库，
        之后 app 里清空就能真正停用（不会再回退到 config.toml）。
        """
        raw = self.store.get_setting_raw("heybox_token")
        if raw is None:
            token = (self.heybox_cfg.get("token") or "").strip()
            if token:
                self.store.set_setting("heybox_token", token)   # 迁移：app 从此成为权威
        else:
            token = (raw or "").strip()
        if not token:
            self.heybox = None
            self.autonomy.heybox = None
            return
        try:
            from heybox import HeyboxBot
            bot = HeyboxBot(token, self.store, brain=self.brain, request_approval=self.request_approval)
            self.heybox = bot
            self.autonomy.heybox = bot
            if bot.heybox_id:
                self.log("info", "status", f"小黑盒已接入（{bot.heybox_id}）")
            else:
                self.log("error", "error", "小黑盒 token 无法解析 heybox_id")
        except Exception as e:
            self.heybox = None
            self.autonomy.heybox = None
            self.log("error", "error", f"小黑盒启动失败: {e}")

    def set_heybox_token(self, token):
        """app 保存小黑盒 token：写 SQLite + 热重载适配器。空串 = 停用。"""
        self.store.set_setting("heybox_token", (token or "").strip())
        self._build_heybox()
        return self.heybox.status() if self.heybox else {"enabled": False}

    def set_qq_quick_login_uin(self, uin):
        value = str(uin or '').strip()
        if value and not value.isdigit():
            return {'ok': False, 'code': 'invalid_account', 'error': 'QQ 账号应为数字'}
        self._qq_controller().set_target(value)
        return {'ok': True, 'uin_configured': bool(value)}

    def command(self, cmd, args):
        # 阶段 0 只记录；阶段 3 由 autonomy 真正执行
        if cmd == "stop":
            self.state = "paused"
            self.current_task = ""
            self.autonomy.pause()
            self.log("info", "status", "已叫停")
            return "已叫停"
        if cmd == "resume":
            self.state = "idle"
            self.autonomy.wake()
            self.log("info", "status", "已叫醒")
            return "已叫醒"
        if cmd == "restart":
            self.log("info", "status", "收到重启指令（阶段 3 接入）")
            return "重启待接入"
        self.current_task = cmd + (" " + " ".join(map(str, args)) if args else "")
        self.state = "working"
        self.log("info", "action", f"收到指令: {self.current_task}")
        return "已记录指令: " + self.current_task

    # ---- QQ 生命周期（NapCat/linuxqq 跑在 proot-Debian）----
    def _qq_controller(self):
        if getattr(self, '_controller', None) is None:
            root = os.path.dirname(os.path.abspath(__file__))
            backend = QQBackend(self, root, PROOT_DISTRO, _qq_start_cmd, _qq_env,
                                NAPCAT_CONFIG_DIR, self.qq_cfg.get('qrcode_path', DEFAULT_QRCODE_PATH))
            self._controller = QQController(self.store, backend)
        return self._controller

    def qq_start(self, reset_circuit=False, uin=None):
        return self._qq_controller().request('start', target=uin, manual=reset_circuit)

    def qq_stop(self):
        return self._qq_controller().request('stop')

    def _qq_really_online(self):
        """QQ 是否真实在线：账号已登录（self_id 存在）且 OneBot WS 建立。
        不用 TCP 探活（8081 端口）——账号掉线/NapCat 卡死时端口仍能连上。"""
        bot = getattr(self, "qq_bot", None)
        return bool(bot and bot.connected and bot.self_id and bot.account_online is not False)

    def _qq_stale(self, qr_path):
        """进程活着 + 账号/WS 已掉线 + 无新鲜二维码 = 掉线但进程残留（NapCat 卡死态）。"""
        return _qq_process_alive() and not self._qq_really_online() and not _qq_qr_fresh(qr_path)

    def _qq_self_heal_uin(self):
        return self._qq_controller().target()

    def qq_status(self):
        control = self._qq_controller()
        state = control.snapshot()
        op = state.get('operation') or {}
        bot = getattr(self, 'qq_bot', None)
        account_state = state.get('account_state', 'unknown')
        reason = ('waiting_scan' if state.get('qr') else
                  'offline' if account_state == 'offline' else 'unknown')
        return {**state, 'running': bool(state.get('running') or state.get('launching')),
                'online': bool(state.get('online')), 'qr': bool(state.get('qr')),
                'qr_age': state.get('qr_age', -1),
                'stale': bool(state.get('running') and not state.get('account_ready')
                              and not state.get('login_accepted') and not state.get('qr')),
                'login_reason': reason, 'login_uin': state.get('account') if state.get('account_ready') else None,
                'self_id': bot.self_id if bot else None,
                'quick_login_configured': bool(control.target()), 'device_guid': None,
                'last_error': op.get('error', ''), 'control_version': 2}

    def qq_api(self, action, params):
        """通用转发 OneBot API 调用（给 app / autonomy 用）。"""
        bot = getattr(self, "qq_bot", None)
        if not bot:
            return {"ok": False, "error": "QQ 未连接"}
        try:
            resp = bot.call_api(action, params)
            if resp is None:
                return {"ok": False, "error": "OneBot 未返回确认，执行结果未知", "response": None}
            return {"ok": resp.get("retcode") == 0, "response": resp}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def qq_refresh_qr(self):
        return self._qq_controller().request('refresh_qr')

    def qq_keep_alive(self):
        # Independent controller worker owns polling and recovery; never block the LLM loop.
        self._qq_controller().start()

    def _find_login_uin(self):
        """扫码登录后 NapCat 会生成 onebot11_<uin>.json（首次为空 WS），从文件名拿 QQ 号。
        只认最近 QQ_LOGIN_UIN_FRESH_SECONDS 秒内生成的文件——旧文件跨会话残留，会误判「已登录」。"""
        try:
            best_uin, best_mtime = None, 0.0
            for name in os.listdir(NAPCAT_CONFIG_DIR):
                if name.startswith("onebot11_") and name.endswith(".json"):
                    uin = name[len("onebot11_"):-len(".json")]
                    if uin.isdigit():
                        path = os.path.join(NAPCAT_CONFIG_DIR, name)
                        mtime = os.path.getmtime(path)
                        if mtime > best_mtime:
                            best_uin, best_mtime = uin, mtime
            if best_uin is not None and (time.time() - best_mtime) < QQ_LOGIN_UIN_FRESH_SECONDS:
                return best_uin
        except Exception:
            pass
        return None

    def apply_onebot(self):
        return self._qq_controller().request('configure')

    def _restart_qq(self, uin=None):
        return self._qq_controller().request('restart', target=uin)

    # ---- NapCat WebUI（127.0.0.1:6099）：快捷登录 + 设备指纹 ----
    def _webui_token(self):
        """读 WebUI token：webui.json → qq.log 的 'WebUi Token:' → 兜底 'napcat'。"""
        try:
            if os.path.exists(NAPCAT_WEBUI_CONFIG):
                with open(NAPCAT_WEBUI_CONFIG, "r", encoding="utf-8") as f:
                    tok = (json.loads(f.read()) or {}).get("token", "")
                    if tok:
                        return tok
        except Exception:
            pass
        # 全新安装时 webui.json 可能还没生成：扫 qq.log 启动打印
        try:
            with open(QQ_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 65536))
                tail = f.read()
            m = re.search(r"WebUi Token:\s*(\S+)", tail)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
        return "napcat"

    def _webui_request(self, path, body=None, cred=None):
        result = self._webui_client().raw(path, body, cred)
        return {'code': 0, 'data': result.get('data')} if result['ok'] else None

    def _webui_call(self, path, body=None):
        result = self._webui_client().call(path, body)
        return {'code': 0, 'data': result.get('data')} if result['ok'] else None

    def _webui_check_login(self):
        result = self._webui_client().call('/api/QQLogin/CheckLoginStatus', {})
        return result.get('data') if result['ok'] else None

    def _webui_quick_login_list(self):
        result = self._webui_client().call('/api/QQLogin/GetQuickLoginList', {})
        return result.get('data') if result['ok'] else None

    def _webui_client(self):
        controller = getattr(self, '_controller', None)
        if controller is not None:
            return controller.backend.webui
        # Read-only probes may construct Fish without starting the service.
        if not getattr(self, '_webui_transport', None):
            self._webui_transport = WebUIClient(self._webui_token)
        return self._webui_transport

    def _do_quick_login_heal(self, uin):
        return self._qq_controller().request('quick_login', target=uin)

    def qq_quick_login(self, uin=None):
        return self._qq_controller().request('quick_login', target=uin)

    def _refresh_device_fingerprint(self):
        # Platform identity is diagnostic only. Never apply Windows GUID writes to Linux.
        return {'supported': False, 'reason': 'use platform-specific read-only diagnostics'}

    # ---- 自省（nudge 一次 reflect）----
    def growth_evolve(self):
        self.autonomy.trigger_reflect()
        self.log("info", "growth", "收到手动自省请求")
        return {"ok": True}

    # ---- 清除人格 ----
    def reset_persona(self):
        """清空名字+设定+长期记忆+短期上下文，回到出厂空白（LLM/审批等保留）。"""
        with self.store._lock:
            self.store.set_settings({"persona": "", "name": "", "self_profile": {}, "life_state": {}})
            self.store.clear_memories()
            self.store.clear_short_term()
        self.log("info", "status", "人格已重置")
        return {"ok": True}

    # ---- 生命状态（它的「心」）----
    def life_state(self):
        st = self.autonomy.life.state()
        return {
            "mood": st["mood"],
            "valence": st.get("valence", 0.0),
            "arousal": st.get("arousal", 0.0),
            "dominance": st.get("dominance", 0.0),
            "attribution": st.get("attribution", 0.0),
            "next_wake_at": st["next_wake_at"],
            "last_wake_at": st["last_wake_at"],
            "paused": self.autonomy.is_paused(),
            "platforms_online": ["qq"] if self._qq_really_online() else [],
        }

    def life_wake(self):
        self.autonomy.wake()
        self.log("info", "status", "已叫醒")
        return {"ok": True}

    # ---- 表情包管理 ----
    def list_stickers(self):
        return {"stickers": [
            {"id": s["id"], "tag": s["tag"], "pinned": bool(s["pinned"]), "created_at": s["created_at"]}
            for s in self.store.get_stickers(limit=500)
        ]}

    def add_sticker_http(self, body):
        tag = str(body.get("tag", "") or "").strip()[:20]
        b64 = str(body.get("base64", "") or "").strip()
        url = str(body.get("url", "") or "").strip()
        ext = str(body.get("ext", "") or "").strip().lower()
        data = b""
        if b64:
            try:
                data = base64.b64decode(b64)
            except Exception:
                return {"ok": False, "error": "base64 解码失败"}
            if not ext:
                ext = _sniff_ext(data)
        elif url:
            data, ext = _download_url(url)
        else:
            return {"ok": False, "error": "需要 base64 或 url"}
        if not data:
            return {"ok": False, "error": "下载失败或空数据"}
        if len(data) > 5 * 1024 * 1024:
            return {"ok": False, "error": "超过 5MB"}
        if ext not in ("gif", "png", "jpg", "jpeg", "webp"):
            ext = _sniff_ext(data)
        sha = hashlib.sha256(data).hexdigest()
        if self.store.has_sticker(sha):
            return {"ok": False, "error": "已存在（重复）"}
        os.makedirs(STICKER_DIR, exist_ok=True)
        path = os.path.join(STICKER_DIR, sha + "." + ext)
        with open(path, "wb") as f:
            f.write(data)
        sid = self.store.add_sticker(tag, path, sha)
        return {"ok": True, "id": sid}

    def update_sticker_http(self, sid, body):
        tag = body.get("tag")
        if tag is not None:
            tag = str(tag).strip()[:20]
        pinned = body.get("pinned")
        if pinned is not None:
            pinned = bool(pinned)
        return {"ok": self.store.update_sticker(sid, tag=tag, pinned=pinned)}

    def delete_sticker_http(self, sid):
        return {"ok": self.store.delete_sticker(sid)}


class Deployer:
    """跑部署脚本（setup-qq.sh），解析进度标记，把状态写进 SQLite。"""

    def __init__(self, store):
        self.store = store
        self._thread = None
        self._proc = None

    def _set_status(self, **kw):
        d = self.store.get_settings().get("deploy_status")
        if not isinstance(d, dict):
            d = {"running": False, "step": "", "step_label": "", "done": False, "error": None, "progress": -1}
        d.update(kw)
        self.store.set_setting("deploy_status", d)

    def status(self):
        d = self.store.get_settings().get("deploy_status")
        if not isinstance(d, dict):
            d = {"running": False, "step": "", "step_label": "", "done": False, "error": None, "progress": -1}
        return {"status": d, "logs": self.store.get_logs("deploy", limit=40)}

    def start(self, script_path):
        if self._thread and self._thread.is_alive():
            return {"ok": False, "error": "已经在部署中"}
        if not os.path.exists(script_path):
            return {"ok": False, "error": f"脚本不存在: {script_path}"}
        self._set_status(running=True, step="", step_label="启动", done=False, error=None)
        self.store.add_log("info", "deploy", f"开始部署: {script_path}")
        self._thread = threading.Thread(target=self._run, args=(script_path,), daemon=True)
        self._thread.start()
        return {"ok": True}

    def _run(self, script_path):
        try:
            self._proc = subprocess.Popen(
                ["bash", script_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=_qq_env(),
            )
            for line in self._proc.stdout:
                self._parse_line(line.rstrip("\n"))
            self._proc.wait()
            if self._proc.returncode == 0:
                self._set_status(running=False, done=True, error=None)
                self.store.add_log("info", "deploy", "部署完成")
            else:
                d = self.store.get_settings().get("deploy_status") or {}
                err = d.get("error") or f"exit {self._proc.returncode}"
                self._set_status(running=False, done=False, error=err)
                self.store.add_log("error", "deploy", f"部署失败: {err}")
        except Exception as e:
            self._set_status(running=False, done=False, error=str(e))
            self.store.add_log("error", "deploy", f"部署异常: {e}")
        finally:
            self._proc = None

    def _parse_line(self, line):
        self.store.add_log("info", "deploy", line)
        if line.startswith("=== STEP: "):
            name = line[len("=== STEP: "):].rstrip(" =")
            self._set_status(step=name, step_label=name, progress=-1)
        elif line.startswith("=== ERROR: "):
            self._set_status(error=line[len("=== ERROR: "):])
        elif line.startswith("=== PROGRESS: "):
            rest = line[len("=== PROGRESS: "):].replace("===", "").strip()
            try:
                self._set_status(progress=int(rest))
            except ValueError:
                pass


class Handler(BaseHTTPRequestHandler):
    fish = None  # 启动时注入
    api_token = ""

    def _authorized(self):
        supplied = self.headers.get("Authorization", "")
        if self.api_token and hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + self.api_token).encode("utf-8")):
            return True
        self._send(401, {"error": "需要监管 app 授权"})
        return False

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _qs(self):
        return {k: v[0] for k, v in parse_qs(self.path.split("?", 1)[1]).items()} \
            if "?" in self.path else {}

    def do_GET(self):
        if not self._authorized():
            return
        f = self.fish
        path = self.path.split("?")[0].rstrip("/")
        q = self._qs()

        if path == "/status":
            s = f.store.get_settings()
            return self._send(200, {
                "name": s.get("name"), "state": f.state,
                "current_task": f.current_task,
                "uptime": int(time.time() - f.started),
                "platforms": s.get("enabled_platforms"),
                "memory_queue": f.store.memory_queue_status(),
            })
        if path == "/logs":
            return self._send(200, {"logs": f.store.get_logs(
                q.get("category"), q.get("level"), int(q.get("limit", 100)))})
        if path == "/chat":
            return self._send(200, {"chat": f.store.get_logs("chat", limit=int(q.get("limit", 100)))})
        if path == "/memories":
            return self._send(200, {"memories": f.store.get_memories(
                kind=q.get("kind"), subject=q.get("subject"), platform=q.get("platform"),
                limit=max(1, min(1000, int(q.get("limit", 100)))), offset=max(0, int(q.get("offset", 0))),
                include_inactive=True)})
        if path == "/settings":
            return self._send(200, f.store.get_settings())
        if path == "/approval/pending":
            return self._send(200, {"pending": f.store.get_pending_approvals()})
        if path == '/approval/recent':
            return self._send(200, {'approvals': f.store.get_recent_approvals()})
        if path == "/qrcode":
            qp = f.qq_cfg.get("qrcode_path", DEFAULT_QRCODE_PATH)
            try:
                with open(qp, "rb") as fp:
                    img = fp.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(img)))
                self.end_headers()
                self.wfile.write(img)
                return
            except Exception:
                return self._send(404, {"error": "no qrcode"})
        if path == "/llm":
            return self._send(200, f.store.get_settings().get("llm", {}))
        if path == "/usage":
            return self._send(200, f.store.get_usage())
        if path == "/qq/status":
            return self._send(200, f.qq_status())
        if path == "/heybox/status":
            return self._send(200, f.heybox.status() if f.heybox else {"enabled": False})
        if path == "/deploy/status":
            return self._send(200, f.deployer.status())
        if path == "/life/state":
            return self._send(200, f.life_state())
        if path == "/stickers":
            return self._send(200, f.list_stickers())
        if path.startswith("/stickers/") and path.endswith("/image"):
            try:
                sid = int(path.split("/")[2])
            except (IndexError, ValueError):
                return self._send(400, {"error": "bad id"})
            s = f.store.get_sticker(sid)
            if not s:
                return self._send(404, {"error": "not found"})
            try:
                with open(s["path"], "rb") as fp:
                    img = fp.read()
                ext = os.path.splitext(s["path"])[1].lstrip(".").lower()
                ctype = {"gif": "image/gif", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/gif")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(img)))
                self.end_headers()
                self.wfile.write(img)
                return
            except Exception:
                return self._send(404, {"error": "read failed"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return
        f = self.fish
        path = self.path.split("?")[0].rstrip("/")
        body = self._read_json()

        if path == "/command":
            msg = f.command(body.get("command", ""), body.get("args", []))
            return self._send(200, {"ok": True, "message": msg})
        if path == "/qq/start":
            return self._send(200, f.qq_start(reset_circuit=True))
        if path == "/qq/stop":
            return self._send(200, f.qq_stop())
        if path == "/qq/refresh_qr":
            return self._send(200, f.qq_refresh_qr())
        if path == "/qq/quick_login":
            return self._send(200, f.qq_quick_login(body.get("uin")))
        if path == "/qq/api":
            return self._send(200, f.qq_api(body.get("action", ""), body.get("params", {})))
        if path == "/qq/apply_onebot":
            return self._send(200, f.apply_onebot())
        if path == "/deploy/qq":
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup-qq.sh")
            return self._send(200, f.deployer.start(script))
        if path == "/life/wake":
            return self._send(200, f.life_wake())
        if path == "/heybox/sign":
            if 'heybox' not in f.store.get_settings().get('enabled_platforms', []):
                return self._send(409, {'error': '小黑盒已禁用'})
            return self._send(200, f.heybox.sign() if f.heybox else {"error": "小黑盒未接入"})
        if path == "/growth/evolve":
            return self._send(200, f.growth_evolve())
        if path == "/persona/reset":
            return self._send(200, f.reset_persona())

        # 审批：/approval/<id>/approve 或 /approval/<id>/reject
        if path.startswith("/approval/") and path.endswith(("/approve", "/reject")):
            parts = path.split("/")
            try:
                aid = int(parts[2])
            except (IndexError, ValueError):
                return self._send(400, {"error": "bad id"})
            action = parts[3]
            status = "approved" if action == "approve" else "rejected"
            if f.store.set_approval_status(aid, status):
                if action == 'reject':
                    f.store.set_execution_state(aid, 'not_required')
                f.log("info", "approval", f"审批[{aid}] {'已同意' if action == 'approve' else '已拒绝'}")
                ap = f.store.get_approval(aid)
                try:
                    if action == "approve" or ap.get("kind") in ("friend", "group"):
                        f.execute_approval(aid, approve=action == "approve")
                except Exception as e:
                    f.store.mark_approval_failed(aid)
                    f.store.set_execution_state(aid, 'unconfirmed' if ap.get('kind') in ('friend','group') else 'failed_or_partial')
                    f.log("error", "approval", str(e))
                    return self._send(409, {"ok": False, "error": str(e)})
                return self._send(200, {"ok": True, "status": status})
            return self._send(404, {"error": "not found"})

        if path == "/stickers":
            return self._send(200, f.add_sticker_http(body))

        return self._send(404, {"error": "not found"})

    def do_PUT(self):
        if not self._authorized():
            return
        f = self.fish
        path = self.path.split("?")[0].rstrip("/")
        body = self._read_json()
        if path.startswith("/memories/"):
            try:
                mid = f.store.update_memory(int(path.split("/")[-1]), body)
                return self._send(200, {"ok": True, "id": mid})
            except (ValueError, TypeError) as exc:
                return self._send(400, {"error": str(exc)})
        if path == "/settings":
            with f.store._lock:
                if 'enabled_platforms' in body and (not isinstance(body['enabled_platforms'], list)
                        or any(p not in ('qq','heybox','bilibili') for p in body['enabled_platforms'])):
                    return self._send(400, {'error':'平台设置格式无效'})
                if 'self_profile' in body:
                    if body.get('self_profile_revision') != f.store.get_settings()['self_profile_revision']:
                        return self._send(409, {'error': '画像已变化，请刷新后再保存'})
                    profile = body['self_profile']
                    if (not isinstance(profile, dict) or set(profile)-{'values','style','likes','boundaries'}
                            or any(not isinstance(v, list) or any(not isinstance(t, str) for t in v) for v in profile.values())):
                        return self._send(400, {'error': '画像格式无效'})
                updates = {k:v for k,v in body.items() if k not in ('self_profile_revision','growth_status','manual_self_profile','user_locked_settings',
                    'memory_epoch','life_state_revision','memory_policy_version','evolution_result',
                    'qq_desired_state','qq_login_target','qq_last_account','qq_last_operation','qq_operation','qq_generation','qq_runtime','qq_quick_login_uin')}
                if 'self_profile' in updates:
                    f.store.save_manual_profile(updates.pop('self_profile'))
                f.store.apply_manual_settings(updates)
                return self._send(200, f.store.get_settings())
        if path == "/llm":
            existing = f.store.get_settings().get("llm", {}) or {}
            merged = dict(existing)
            for k, v in (body or {}).items():
                # Omitted fields preserve old values; explicit empty strings clear them.
                merged[k] = v
            f.store.set_setting("llm", merged)
            return self._send(200, merged)
        if path == "/heybox":
            return self._send(200, f.set_heybox_token(body.get("token", "")))
        if path == "/qq/quick_login_uin":
            return self._send(200, f.set_qq_quick_login_uin(body.get("uin", "")))
        if path.startswith("/stickers/"):
            try:
                sid = int(path.split("/")[-1])
            except ValueError:
                return self._send(400, {"error": "bad id"})
            return self._send(200, f.update_sticker_http(sid, body))
        return self._send(404, {"error": "not found"})

    def do_DELETE(self):
        if not self._authorized():
            return
        f = self.fish
        path = self.path.split("?")[0].rstrip("/")
        if path.startswith("/memories/"):
            try:
                mid = int(path.split("/")[-1])
            except ValueError:
                return self._send(400, {"error": "bad id"})
            f.store.delete_memory(mid)
            return self._send(200, {"ok": True})
        if path.startswith("/stickers/"):
            try:
                sid = int(path.split("/")[-1])
            except ValueError:
                return self._send(400, {"error": "bad id"})
            return self._send(200, f.delete_sticker_http(sid))
        return self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass  # 静默访问日志


def _start_qq(fish, cfg):
    qq_cfg = cfg.get("qq", {})
    ws_url = qq_cfg.get("ws_url", "")
    if not ws_url:
        return
    try:
        from qq_bot import QQBot
        bot = QQBot(ws_url, fish.brain, fish.store,
                    request_approval=fish.request_approval,
                    consolidate=fish.autonomy.consolidate_memory)
        bot.start()
        fish.qq_bot = bot
        fish.autonomy.qq_bot = bot   # 主动冒泡要用
    except Exception as e:
        fish.log("error", "error", f"QQ 启动失败: {e}")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(here, ".api-token")
    try:
        with open(token_path, encoding="utf-8") as token_file:
            Handler.api_token = token_file.read().strip()
    except FileNotFoundError:
        pass
    if not Handler.api_token:
        Handler.api_token = secrets.token_urlsafe(32)
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as token_file:
            token_file.write(Handler.api_token)
    os.chmod(token_path, 0o600)
    # 24h 保活：拿 wakelock，防止屏幕关后被系统杀
    try:
        subprocess.run([TERMUX_BIN + "/termux-wake-lock"], env=_qq_env(), timeout=10)
    except Exception:
        pass
    cfg = load_config(os.path.join(here, "config.toml"))
    server_cfg = cfg.get("server", {})
    host = server_cfg.get("host", "127.0.0.1")
    port = int(server_cfg.get("port", 8765))
    Handler.fish = Fish(cfg)
    _start_qq(Handler.fish, cfg)
    Handler.fish._build_heybox()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Fishbrain 鱼脑 API 已启动: http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
