# server.py — Fishbrain 鱼脑的本地 API（HTTP + 轮询），给监管 app 用
# 阶段 0：骨架。暴露 状态/日志/聊天记录/记忆/设定/审批/命令，全部落到 memory.Store。
import base64
import hashlib
import json
import os
import re
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
# 先 SIGTERM 优雅退出（让 QQ 把 keystore 写盘，登录态才持久），再 SIGKILL 兜底。
# 用 [q]/[X]/[x] 括号正则避免 pkill 匹配到自身的 bash -c 命令。
QQ_STOP_CMD = ("pkill -TERM -f '[q]q --no-sandbox'; sleep 15; "
               "pkill -9 -f '[q]q --no-sandbox'; pkill -9 -f '[X]vfb'; pkill -9 -f '[x]vfb-run'")
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
    """linuxqq/NapCat 进程是否活着（含「正在扫码登录」阶段，此时 8081 还没起）。"""
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
            except Exception:
                continue
            if "qq --no-sandbox" in cmd:
                return True
    except Exception:
        pass
    return False


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

    def execute_approval(self, aid):
        """审批同意后，按 kind 执行实际动作（发帖/评论等，后台线程跑）。"""
        ap = self.store.get_approval(aid)
        if not ap:
            return
        kind = ap.get("kind", "")
        try:
            args = json.loads(ap.get("summary", "") or "{}")
        except Exception:
            args = {}
        if kind == "heybox_post" and self.heybox:
            self.log("info", "approval", f"执行发帖: {args.get('title', '')}")
            threading.Thread(target=self.heybox.post,
                             args=(args.get("title", ""), args.get("content", "")),
                             daemon=True).start()
        elif kind == "heybox_comment" and self.heybox:
            self.log("info", "approval", f"执行评论: {args.get('content', '')}")
            threading.Thread(target=self.heybox.comment,
                             args=(args.get("link_id", 0), args.get("content", "")),
                             daemon=True).start()
        elif kind == "publish":
            text = (ap.get("summary") or "").strip()
            if text and self.qq_bot:
                self.log("info", "approval", f"执行发空间动态: {text}")
                threading.Thread(target=self.qq_bot.send_qzone_mood, args=(text,), daemon=True).start()
            else:
                self.log("error", "approval", "发空间动态失败：内容为空或 QQ 未连接")

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
        """app 保存快捷登录 QQ 号（掉线自愈用）。空串/非数字 = 停用自愈。"""
        val = str(uin or "").strip()
        self.store.set_setting("qq_quick_login_uin", val)
        return {"ok": True, "uin_configured": val.isdigit()}

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
    def qq_start(self, reset_circuit=False, uin=None):
        self._qq_auto = True  # 显式/自动启动后，恢复自动保活
        if reset_circuit:  # 仅显式/人工启动才清零熔断（自动拉起走 qq_start 不清，否则计数永远累加不到阈值）
            self._qq_auto_fail_count = 0
            self._qq_auto_circuit_until = 0.0
            self._qq_online_since = 0.0
        self._last_qq_online_ts = time.time()  # 启动 = 新的宽限基准，给 boot 300s 余量
        if _qq_process_alive():
            self.log("info", "status", "QQ 已在运行，跳过启动")
            return {"ok": True, "already": True}
        try:
            err_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qq_start_err.log")
            with open(err_path, "w") as f:
                subprocess.Popen(
                    [PROOT_DISTRO, "login", "debian", "--hostname", "qq-bot", "--", "bash", "-c", _qq_start_cmd(uin)],
                    env=_qq_env(), start_new_session=True,
                    stdout=f, stderr=subprocess.STDOUT,
                )
            tag = f"（快速登录 {uin}）" if uin else ""
            self.log("info", "status", f"已发起 QQ 启动（NapCat）{tag}")
            return {"ok": True, "already": False}
        except Exception as e:
            self.log("error", "error", f"QQ 启动失败: {e}")
            return {"ok": False, "error": str(e)}

    def qq_stop(self):
        self._qq_auto = False  # 用户显式停止，别再自动拉起
        try:
            subprocess.run(
                [PROOT_DISTRO, "login", "debian", "--", "bash", "-c", QQ_STOP_CMD],
                env=_qq_env(), timeout=20,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.log("info", "status", "已停止 QQ")
            return {"ok": True}
        except Exception as e:
            self.log("error", "error", f"QQ 停止失败: {e}")
            return {"ok": False, "error": str(e)}

    def _qq_really_online(self):
        """QQ 是否真实在线：账号已登录（self_id 存在）且 OneBot WS 建立。
        不用 TCP 探活（8081 端口）——账号掉线/NapCat 卡死时端口仍能连上。"""
        bot = getattr(self, "qq_bot", None)
        return bool(bot and bot.connected and bot.self_id)

    def _qq_stale(self, qr_path):
        """进程活着 + 账号/WS 已掉线 + 无新鲜二维码 = 掉线但进程残留（NapCat 卡死态）。"""
        return _qq_process_alive() and not self._qq_really_online() and not _qq_qr_fresh(qr_path)

    def _qq_self_heal_uin(self):
        """自愈用的快捷登录 QQ 号：读 settings（_on_meta 登录时自动填），纯数字才返回，否则 None。"""
        raw = self.store.get_setting_raw("qq_quick_login_uin")
        if raw is None:
            return None
        s = str(raw).strip()
        return s if s.isdigit() else None

    def qq_status(self):
        qr_path = self.qq_cfg.get("qrcode_path", DEFAULT_QRCODE_PATH)
        self_id = None
        if getattr(self, "qq_bot", None):
            self_id = self.qq_bot.self_id
        qr_age = -1
        try:
            if os.path.exists(qr_path):
                qr_age = int(time.time() - os.path.getmtime(qr_path))
        except Exception:
            qr_age = -1
        process_alive = _qq_process_alive()
        online = self._qq_really_online()
        qr_fresh = _qq_qr_fresh(qr_path)
        # 快捷登录在途标志：上线即清，另设 300s 兜底超时（-q 重启+登录可能较久）
        if self._self_healing and (online or (time.time() - self._self_healing_ts) > 300):
            self._self_healing = False
        raw_guid = self.store.get_setting_raw("qq_device_guid")
        device_guid = str(raw_guid).strip() if raw_guid else ""
        return {
            "running": process_alive,      # 进程活着（含扫码阶段）
            "qr": qr_fresh,                # 有新鲜二维码 = 待扫码
            "qr_age": qr_age,              # 二维码生成至今秒数（-1=还没有码）
            "online": online,              # 账号已登录且 WS 真实存活（非 TCP 探活）
            "stale": process_alive and not online and not qr_fresh,  # 掉线残留
            "login_reason": _parse_qq_log_state(),  # qq.log 最近一次登录事件归类
            "self_id": self_id,            # 登录后的 QQ 号
            "login_uin": self._find_login_uin(),  # 从 onebot11_<uin>.json 文件名探测到的 QQ 号
            "quick_login_configured": bool(self._qq_self_heal_uin()),  # 是否配了 -q 快捷登录号（只布尔不吐号）
            "auto_circuit": bool(self._qq_auto_circuit_until and time.time() < self._qq_auto_circuit_until),
            "auto_fail_count": self._qq_auto_fail_count,
            "self_healing": self._self_healing,   # 自动快捷登录是否在途
            "device_guid": device_guid,           # 缓存的最新设备 GUID（Part B 写入）
        }

    def qq_api(self, action, params):
        """通用转发 OneBot API 调用（给 app / autonomy 用）。"""
        bot = getattr(self, "qq_bot", None)
        if not bot:
            return {"ok": False, "error": "QQ 未连接"}
        try:
            resp = bot.call_api(action, params)
            return {"ok": True, "response": resp}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def qq_refresh_qr(self):
        """强制换新二维码：重启 QQ（自动刷新失效时的兜底）。"""
        self.log("info", "status", "重新出码：重启 QQ")
        self.qq_stop()
        time.sleep(3)
        return self.qq_start(reset_circuit=True)

    def qq_keep_alive(self):
        """自保本能：QQ 进程掉了就自动拉起（带风控护栏 + 扫码停手）。
        进程活着但已掉线（账号/WS 掉线、无新鲜码）时：配了快捷登录号且属软掉线则 -q 自愈，否则只记日志。
        由 autonomy 周期性调用；用户显式停 QQ 后不再自动拉起。"""
        if not self._qq_auto:
            return
        if time.time() - self.started < QQ_KEEPALIVE_GRACE_SECONDS:
            return  # 鱼脑刚启动，先别拉起/判断，给 QQ 的 WS 重连留时间
        now = time.time()
        # 记录上次真实在线时刻（掉线宽限期的基准）；持续稳定在线才清零熔断计数
        if self._qq_really_online():
            self._last_qq_online_ts = now
            if self._qq_online_since == 0.0:
                self._qq_online_since = now
            elif now - self._qq_online_since >= QQ_AUTO_STABLE_SECONDS:
                self._qq_auto_fail_count = 0
                self._qq_auto_circuit_until = 0.0
            # 每 600s 捕获/比对一次设备指纹（观测 + 纠偏，后台线程跑，不阻塞心跳）
            if now - self._last_guid_check >= 600:
                self._last_guid_check = now
                threading.Thread(target=self._refresh_device_fingerprint, daemon=True).start()
        else:
            self._qq_online_since = 0.0
        qr_path = self.qq_cfg.get("qrcode_path", DEFAULT_QRCODE_PATH)
        # 分支 1：进程完全没了 → 自动拉起（有快捷登录号则 -q 免扫码，否则等人扫码）
        if not _qq_process_alive():
            if self._qq_auto_circuit_until:
                if now < self._qq_auto_circuit_until:
                    return  # 熔断中，暂停自动拉起
                # 熔断期已过，重置计数重新尝试
                self._qq_auto_fail_count = 0
                self._qq_auto_circuit_until = 0.0
            if now - self._last_qq_auto_start < QQ_RESTART_COOLDOWN:
                return
            uin = self._qq_self_heal_uin()
            if _qq_qr_fresh(qr_path) and not uin:
                # 进程已死但留下新鲜二维码：只有没快捷登录号时才等人扫码（有号则 -q 免扫码重启）
                if not self._qq_scan_logged:
                    self._qq_scan_logged = True
                    self.log("info", "qq", "QQ 需要重新扫码登录，暂不自动重登（去 app 扫码）")
                return
            self._qq_scan_logged = False
            if not os.path.exists(PROOT_DISTRO):
                return  # 开发机没有 proot-distro，不折腾
            self._last_qq_auto_start = now
            self._qq_auto_fail_count += 1
            if self._qq_auto_fail_count >= QQ_AUTO_MAX_FAIL:
                self._qq_auto_circuit_until = now + QQ_AUTO_CIRCUIT_SECONDS
                self.log("warn", "qq",
                         f"QQ 连续 {self._qq_auto_fail_count} 次自动拉起仍无法稳定在线，熔断 {QQ_AUTO_CIRCUIT_SECONDS // 60} 分钟（可手动启动重置）")
                return
            tag = "（-q 快速登录）" if uin else ""
            self.log("info", "qq", f"检测到 QQ 离线，自动拉起…{tag}")
            return self.qq_start(uin=uin)
        # 分支 2：进程活着但掉线（WS 不通 + 无新鲜码）。
        # 配了快捷登录号且属「软掉线」时，用 -q 快速登录自愈一次；否则只按节流记日志，交回 qq_bot 的 5s 重连。
        if not self._qq_stale(qr_path):
            return
        if now - self._last_qq_online_ts < QQ_STALE_GRACE_SECONDS:
            return  # 可能还在正常启动/扫码/NapCat 自愈窗口，先别动作
        if self._qq_auto_circuit_until:
            if now < self._qq_auto_circuit_until:
                return  # 熔断中，暂停自动恢复
            self._qq_auto_fail_count = 0
            self._qq_auto_circuit_until = 0.0
        if now - self._last_qq_stale_restart_ts < QQ_STALE_RESTART_COOLDOWN:
            return  # 单次节流，避免频繁重登
        reason = _parse_qq_log_state()
        uin = self._qq_self_heal_uin()
        if uin and reason in ("offline", "login_service_dropped", "nt_timeout"):
            # 先确认账号真离线（别在「还登着 / 正在重连」时乱动）
            if not self._webui_account_offline():
                self._last_qq_stale_restart_ts = now
                self.log("info", "qq", "QQ 账号未真离线（WS 掉或重连中），等待自动重连")
                return
            # 账号真离线：先免重启 WebUI 快登，失败回退 -q 重启
            self._last_qq_stale_restart_ts = now
            self._qq_auto_fail_count += 1
            if self._qq_auto_fail_count >= QQ_AUTO_MAX_FAIL:
                self._qq_auto_circuit_until = now + QQ_AUTO_CIRCUIT_SECONDS
                self.log("warn", "qq",
                         f"QQ 连续 {self._qq_auto_fail_count} 次掉线自愈仍未稳定在线，熔断 {QQ_AUTO_CIRCUIT_SECONDS // 60} 分钟（可手动启动重置）")
                return
            self.log("info", "qq", f"QQ 掉线自愈：快捷登录（reason={reason}）")
            threading.Thread(target=self._do_quick_login_heal, args=(uin,), daemon=True).start()
            return
        self._last_qq_stale_restart_ts = now
        self.log("warn", "qq", "QQ 进程活着但 WS 掉线（NapCat 疑似卡住），不重启以保留登录态，等待自动重连")

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
        """扫码登录后：把 ~/dfy/onebot11.json 模板写成 NapCat 的 onebot11_<uin>.json，
        并重启 QQ 让 8081 生效（NapCat 首次生成的配置 websocketServers 是空的）。"""
        bot = getattr(self, "qq_bot", None)
        self_id = (bot.self_id if bot else None) or self._find_login_uin()
        if not self_id:
            return {"ok": False, "error": "QQ 还没登录（self_id 未知）"}
        here = os.path.dirname(os.path.abspath(__file__))
        template = os.path.join(here, "onebot11.json")
        if not os.path.exists(template):
            return {"ok": False, "error": "onebot11.json 模板不存在"}
        target = os.path.join(NAPCAT_CONFIG_DIR, f"onebot11_{self_id}.json")
        if self._onebot_has_ws(target):
            return {"ok": True, "target": target, "already": True}
        try:
            with open(template, "r", encoding="utf-8") as f:
                content = f.read()
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            return {"ok": False, "error": f"写 OneBot 配置失败: {e}"}
        self.log("info", "qq", f"已写 OneBot 配置 onebot11_{self_id}.json，重启 QQ 让 8081 生效")
        threading.Thread(target=self._restart_qq, daemon=True).start()
        return {"ok": True, "target": target, "restarting": True}

    def _onebot_has_ws(self, path):
        """目标 onebot11 配置里是否已经配了 8081 的 websocketServer（已配就跳过重写+重启）。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            servers = data.get("network", {}).get("websocketServers", [])
            return any(isinstance(s, dict) and s.get("port") == QQ_WS_PORT for s in servers)
        except Exception:
            return False

    def _restart_qq(self, uin=None):
        """后台优雅重启 QQ（stop→start），给 keystore 落盘留窗口；uin 非空则走 -q 快速登录。"""
        time.sleep(1)
        self.qq_stop()
        time.sleep(2)
        self.qq_start(uin=uin)

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
        """低层：POST/GET 到 NapCat WebUI，返回解析后的 JSON dict（失败/超时返回 None）。"""
        url = NAPCAT_WEBUI_URL + path
        try:
            req = urllib.request.Request(url, method="POST" if body is not None else "GET")
            req.add_header("Content-Type", "application/json")
            if cred:
                req.add_header("Authorization", "Bearer " + cred)
            data = json.dumps(body).encode("utf-8") if body is not None else None
            with urllib.request.urlopen(req, data=data, timeout=6) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def _webui_cred(self):
        """WebUI Bearer Credential（sha256(token+'.napcat') 登录），缓存 ~55 分钟。"""
        now = time.time()
        if self._webui_cred_val and (now - self._webui_cred_ts) < 3300:
            return self._webui_cred_val
        token = self._webui_token()
        h = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
        r = self._webui_request("/api/auth/login", {"hash": h}, cred=None)
        if not r or r.get("code") != 0:
            self._webui_cred_val = None
            return None
        self._webui_cred_val = ((r.get("data") or {}).get("Credential")) or None
        self._webui_cred_ts = now
        return self._webui_cred_val

    def _webui_call(self, path, body=None):
        """带鉴权调用 WebUI；拿不到 Credential（WebUI 未起/不可达）返回 None。"""
        cred = self._webui_cred()
        if not cred:
            return None
        return self._webui_request(path, body, cred=cred)

    def _webui_check_login(self):
        """CheckLoginStatus → data dict（isLogin/isOffline/loginPhase/...）；失败/不可达返回 None。"""
        r = self._webui_call("/api/QQLogin/CheckLoginStatus", {})
        return (r or {}).get("data") if (r and r.get("code") == 0) else None

    def _webui_quick_login_list(self):
        """GetQuickLoginList → 可快捷登录的 uin 字符串列表；不可达返回 []。"""
        r = self._webui_call("/api/QQLogin/GetQuickLoginList", {})
        if r and r.get("code") == 0:
            data = r.get("data")
            if isinstance(data, list):
                out = []
                for x in data:
                    if isinstance(x, dict):
                        u = x.get("uin") or x.get("Uin")
                        if u:
                            out.append(str(u))
                    else:
                        out.append(str(x))
                return out
        return []

    def _webui_set_quick_login(self, uin):
        """SetQuickLogin({"uin":...}) → 原始响应 dict；不可达返回 None。"""
        return self._webui_call("/api/QQLogin/SetQuickLogin", {"uin": str(uin)})

    def _webui_account_offline(self):
        """账号是否需要重登：True=真离线（未登录或已离线），False=还登着/正在重连/WebUI 不可达。"""
        st = self._webui_check_login()
        if not st:
            return False  # WebUI 不可达，别据此乱动
        if st.get("loginPhase") == "reconnecting":
            return False
        if st.get("isLogin") and not st.get("isOffline"):
            return False
        return True

    def _do_quick_login_heal(self, uin):
        """后台自愈：先免重启 WebUI 快登；失败/不在列表则回退 -q 重启。"""
        try:
            lst = self._webui_quick_login_list()
            if lst and str(uin) not in lst:
                self.log("info", "qq", f"WebUI 快捷登录列表不含 {uin}，回退 -q 重启")
                self._restart_qq(uin)
                return
            r = self._webui_set_quick_login(uin)
            if r and r.get("code") == 0:
                self._self_healing = True
                self._self_healing_ts = time.time()
                self.log("info", "qq", f"QQ 掉线自愈：WebUI 快捷登录已发起（uin={uin}）")
                return
            self.log("warn", "qq", f"WebUI 快捷登录失败: {(r or {}).get('message', '无响应')}，回退 -q 重启")
        except Exception as e:
            self.log("warn", "qq", f"WebUI 快捷登录不可用: {e}，回退 -q 重启")
        self._restart_qq(uin)

    def qq_quick_login(self, uin=None):
        """手动触发快捷登录（app「快速登录」按钮）。进程没活着→-q 拉起；活着且真离线→WebUI 快登（回退 -q）。"""
        uin = uin or self._qq_self_heal_uin()
        if not uin:
            return {"ok": False, "error": "没有可快捷登录的 QQ 号（qq_quick_login_uin 未设置）"}
        if not _qq_process_alive():
            threading.Thread(target=self._restart_qq, args=(uin,), daemon=True).start()
            return {"ok": True, "method": "restart", "uin": uin}
        st = self._webui_check_login()
        if st and st.get("isLogin") and not st.get("isOffline"):
            return {"ok": False, "error": "QQ 账号当前在线（只是 WS 掉），无需快捷登录"}
        threading.Thread(target=self._do_quick_login_heal, args=(uin,), daemon=True).start()
        return {"ok": True, "method": "webui", "uin": uin}

    def _webui_device_fingerprint(self):
        """读当前设备 GUID（WebUI 不可达返回 None）。"""
        r = self._webui_call("/api/QQLogin/GetDeviceGUID", {})
        guid = None
        if r and r.get("code") == 0:
            d = r.get("data")
            if isinstance(d, dict):
                guid = d.get("guid") or d.get("GUID") or d.get("deviceGuid")
            elif isinstance(d, str):
                guid = d
        return {"guid": str(guid).strip() if guid else None}

    def _refresh_device_fingerprint(self):
        """在线时捕获/比对设备 GUID：首次存基准，之后漂移→告警并尝试 SetDeviceGUID 纠偏（治「频繁掉线」根因）。"""
        fp = self._webui_device_fingerprint()
        guid = (fp or {}).get("guid")
        if not guid:
            return
        old = self.store.get_setting_raw("qq_device_guid")
        if old is None or str(old).strip() == "":
            self.store.set_setting("qq_device_guid", guid)
            self.log("info", "qq", f"已记录 QQ 设备指纹基准 GUID={guid}")
        elif str(old).strip() != guid:
            self.log("warn", "qq", f"设备指纹漂移：旧={old} 新={guid}，QQ 可能当新设备，尝试恢复")
            r = self._webui_call("/api/QQLogin/SetDeviceGUID", {"guid": str(old).strip()})
            if r and r.get("code") == 0:
                self.log("info", "qq", "已尝试恢复设备 GUID（下次重启生效）")

    # ---- 自省（nudge 一次 reflect）----
    def growth_evolve(self):
        self.autonomy.trigger_reflect()
        self.log("info", "growth", "收到手动自省请求")
        return {"ok": True}

    # ---- 清除人格 ----
    def reset_persona(self):
        """清空名字+设定+长期记忆+短期上下文，回到出厂空白（LLM/审批等保留）。"""
        self.store.set_settings({"persona": "", "name": ""})
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
            })
        if path == "/logs":
            return self._send(200, {"logs": f.store.get_logs(
                q.get("category"), q.get("level"), int(q.get("limit", 100)))})
        if path == "/chat":
            return self._send(200, {"chat": f.store.get_logs("chat", limit=int(q.get("limit", 100)))})
        if path == "/memories":
            return self._send(200, {"memories": f.store.get_memories(
                kind=q.get("kind"), subject=q.get("subject"), platform=q.get("platform"),
                limit=int(q.get("limit", 100)))})
        if path == "/settings":
            return self._send(200, f.store.get_settings())
        if path == "/approval/pending":
            return self._send(200, {"pending": f.store.get_pending_approvals()})
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
                f.log("info", "approval", f"审批[{aid}] {'已同意' if action == 'approve' else '已拒绝'}")
                if action == "approve":
                    f.execute_approval(aid)
                return self._send(200, {"ok": True, "status": status})
            return self._send(404, {"error": "not found"})

        if path == "/stickers":
            return self._send(200, f.add_sticker_http(body))

        return self._send(404, {"error": "not found"})

    def do_PUT(self):
        f = self.fish
        path = self.path.split("?")[0].rstrip("/")
        body = self._read_json()
        if path == "/settings":
            f.store.set_settings(body)
            return self._send(200, f.store.get_settings())
        if path == "/llm":
            existing = f.store.get_settings().get("llm", {}) or {}
            merged = dict(existing)
            for k, v in (body or {}).items():
                # 留空不覆盖：空字符串不覆盖已有的非空值（API Key 遮挡为空不会被清掉）
                if v == "" and existing.get(k):
                    continue
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
