# heybox.py — 小黑盒（Heybox）HTTP API：签到/抽奖养号 + 发帖/评论（发帖走审批）
# 依赖：Python 标准库 only（hashlib/urllib/json/re/base64/random/time）
# hkey 签名算法移植自开源项目 yowiv08/heybox（signature.js / cookie.js / constants.js）
import base64
import hashlib
import json
import random
import re
import time
import urllib.parse
import urllib.request

API_BASE = "https://api.xiaoheihe.cn"
HKEY_API = "https://hkey.qcciii.com/hkey"   # 外部 hkey 服务（app 接口签名用）

# 通用 UA / profile
APP_UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2272.118 Safari/537.36 ApiMaxJia/1.0"
APP_REFERER = "http://api.maxjia.com/"
WEB_UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.192 Mobile Safari/537.36")
WEB_VERSION = "999.0.4"

APP_PROFILE = {
    "os_type": "Android", "x_os_type": "Android", "x_client_type": "mobile",
    "os_version": "12", "dw": "360", "channel": "heybox", "x_app": "heybox",
    "time_zone": "Asia/Shanghai", "device_info": "HBP-AL00",
}
WEB_PROFILE = {
    "app": "heybox", "os_type": "web", "x_app": "heybox", "x_client_type": "web",
    "x_os_type": "Android", "x_client_version": "1.3.389",
}

# ---- hkey 本地算法（web 接口，移植 signature.js）----
_ALPHABET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"


def _mul2(v):
    return (255 & ((v << 1) ^ 27)) if (128 & v) else (v << 1)


def _mix_x(v):
    return _mul2(v) ^ v


def _mix_k(v):
    return _mix_x(_mul2(v))


def _mix_c(v):
    return _mix_k(_mix_x(_mul2(v)))


def _mix_s(v):
    return _mix_c(v) ^ _mix_k(v) ^ _mix_x(v)


def _mix_array(a):
    out0 = _mix_s(a[0]) ^ _mix_c(a[1]) ^ _mix_k(a[2]) ^ _mix_x(a[3])
    out1 = _mix_x(a[0]) ^ _mix_s(a[1]) ^ _mix_c(a[2]) ^ _mix_k(a[3])
    out2 = _mix_k(a[0]) ^ _mix_x(a[1]) ^ _mix_s(a[2]) ^ _mix_c(a[3])
    out3 = _mix_c(a[0]) ^ _mix_k(a[1]) ^ _mix_x(a[2]) ^ _mix_s(a[3])
    a[0], a[1], a[2], a[3] = out0, out1, out2, out3
    return a


def _remap_by_alphabet(s, alphabet, end_offset):
    base = alphabet[:end_offset]
    return "".join(base[ord(c) % len(base)] for c in s)


def _remap_full(s, alphabet):
    return "".join(alphabet[ord(c) % len(alphabet)] for c in s)


def _interleave(parts):
    out, max_len = [], max(len(p) for p in parts)
    for i in range(max_len):
        for p in parts:
            if i < len(p):
                out.append(p[i])
    return "".join(out)


def _normalize_path(path):
    return "/" + "/".join([p for p in str(path).split("/") if p]) + "/"


def _make_web_hkey(path, time_sec, nonce):
    time_part = _remap_by_alphabet(str(time_sec), _ALPHABET, -2)
    path_part = _remap_full(_normalize_path(path), _ALPHABET)
    nonce_part = _remap_full(nonce, _ALPHABET)
    mixed = _interleave([time_part, path_part, nonce_part])[:20]
    digest = hashlib.md5(mixed.encode("utf-8")).hexdigest()
    arr = [ord(c) for c in digest[-6:]]
    _mix_array(arr)  # 只处理前 4 个，后 2 个保持不变
    suffix = str(sum(arr) % 100)
    if len(suffix) < 2:
        suffix = "0" + suffix
    return _remap_by_alphabet(digest[:5], _ALPHABET, -4) + suffix


def _make_web_nonce():
    raw = f"{int(time.time() * 1000)}{random.random()}{random.randbytes(8).hex()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def make_web_signature(path, version=WEB_VERSION):
    time_sec = int(time.time())
    nonce = _make_web_nonce()
    return {"version": version, "hkey": _make_web_hkey(path, time_sec + 1, nonce),
            "_time": str(time_sec), "nonce": nonce}


# ---- 账号解析（移植 cookie.js）----
def _pick_cookie(cookie, key):
    for raw in str(cookie or "").split(";"):
        item = raw.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        if k.strip() == key:
            return v.strip()
    return ""


def decode_pkey_to_user_id(cookie):
    """从 cookie 里的 pkey 解析 heybox_id。"""
    pkey = _pick_cookie(cookie, "pkey")
    if not pkey:
        return ""
    try:
        encoded = urllib.parse.unquote(pkey)
    except Exception:
        return ""
    compact = re.sub(r"_+$", "", encoded) or encoded
    padded = compact + "=" * ((4 - len(compact) % 4) % 4)
    b64 = padded.replace("-", "+").replace("_", "/")
    try:
        plain = base64.b64decode(b64).decode("utf-8", "ignore")
    except Exception:
        return ""
    m = re.search(r"_(\d{5,})", plain)
    return m.group(1) if m else ""


def make_imei(cookie):
    pkey = _pick_cookie(cookie, "pkey")
    if not pkey:
        return ""
    return hashlib.md5(pkey.encode("utf-8")).hexdigest()[:16]


# ---- HTTP helper ----
def _http(url, method="GET", headers=None, params=None, data=None, timeout=20):
    """发请求，返回解析后的 JSON dict。"""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers=headers or {})
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=body, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8", "ignore")[:500]}
    except Exception as e:
        return {"_error": str(e)}


class HeyboxBot:
    """小黑盒平台适配器。契约同 QQBot：暴露 get_tools() + handle_tool()。"""

    def __init__(self, token, store, brain=None, request_approval=None):
        self.token = (token or "").strip()
        self.store = store
        self.brain = brain
        self.request_approval = request_approval   # 发帖/评论走审批
        self.heybox_id = decode_pkey_to_user_id(self.token) if self.token else ""
        self.imei = make_imei(self.token) if self.token else ""
        self.device_info = APP_PROFILE["device_info"]

    def _log(self, level, msg):
        if self.store:
            self.store.add_log(level, "heybox", msg)

    def status(self):
        return {"enabled": bool(self.token), "heybox_id": self.heybox_id,
                "imei": self.imei[:6] + "..." if self.imei else "", "token_set": bool(self.token)}

    # ---- app 接口 hkey（走外部服务）----
    def _request_app_hkey(self, path, time_sec=None):
        time_sec = time_sec or int(time.time())
        params = {"mode": "request", "path": path, "time": str(time_sec),
                  "imei": self.imei, "heybox_id": self.heybox_id}
        r = _http(HKEY_API, params=params, headers={"User-Agent": APP_UA, "Accept": "application/json"})
        data = (r or {}).get("result") or r or {}
        hkey = str(data.get("hkey") or "")
        version = str(data.get("version") or "")
        build = str(data.get("build") or "")
        if not hkey or not version:
            raise RuntimeError(f"hkey 服务返回异常: {data}")
        return {"hkey": hkey, "version": version, "build": build, "time": str(time_sec)}

    def _app_signed_params(self, path, hkey_result):
        return {
            "heybox_id": self.heybox_id, "imei": self.imei,
            "device_info": self.device_info,
            "nonce": "".join(random.choices("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=32)),
            "hkey": hkey_result["hkey"], "os_type": APP_PROFILE["os_type"],
            "x_os_type": APP_PROFILE["x_os_type"], "x_client_type": APP_PROFILE["x_client_type"],
            "os_version": APP_PROFILE["os_version"], "version": hkey_result["version"],
            "build": hkey_result["build"], "_time": hkey_result["time"], "dw": APP_PROFILE["dw"],
            "channel": APP_PROFILE["channel"], "x_app": APP_PROFILE["x_app"],
            "time_zone": APP_PROFILE["time_zone"],
        }

    def _web_signed_params(self, path):
        sig = make_web_signature(path)
        return {**WEB_PROFILE, "version": sig["version"], "hkey": sig["hkey"],
                "_time": sig["_time"], "nonce": sig["nonce"], "heybox_id": self.heybox_id}

    def _app_get(self, path, extra=None):
        hkey = self._request_app_hkey(path)
        params = self._app_signed_params(path, hkey)
        if extra:
            params.update(extra)
        headers = {"User-Agent": APP_UA, "Referer": APP_REFERER, "Cookie": self.token,
                   "Accept": "application/json"}
        return _http(API_BASE + path, params=params, headers=headers)

    def _web_cookie(self):
        """web 接口的 cookie：在 token 上补 x_pkey / x_heybox_id。"""
        pkey = _pick_cookie(self.token, "pkey")
        if not pkey:
            return self.token
        return f"{self.token};x_pkey={pkey};x_heybox_id={self.heybox_id}"

    def _web_get(self, path, extra=None):
        params = self._web_signed_params(path)
        if extra:
            params.update(extra)
        headers = {"User-Agent": WEB_UA, "Cookie": self._web_cookie(), "Accept": "application/json"}
        return _http(API_BASE + path, params=params, headers=headers)

    def _web_post(self, path, query=None, data=None):
        params = self._web_signed_params(path)
        if query:
            params.update(query)
        headers = {"User-Agent": WEB_UA, "Cookie": self._web_cookie(), "Accept": "application/json"}
        return _http(API_BASE + path, method="POST", headers=headers, params=params, data=data)

    # ---- 业务动作 ----
    def sign(self):
        """每日签到。"""
        r = self._app_get("/task/sign_v3/sign")
        if "_error" in r or "_http_error" in r:
            self._log("error", f"签到请求失败: {r}")
            return r
        self._log("info", f"小黑盒签到: {r}")
        return r

    def sign_state(self):
        return self._app_get("/task/sign_v3/get_sign_state")

    def _app_post(self, path, data):
        """app 接口 POST：URL 带签名参数 + JSON body。"""
        hkey = self._request_app_hkey(path)
        params = self._app_signed_params(path, hkey)
        headers = {"User-Agent": APP_UA, "Referer": APP_REFERER, "Cookie": self.token,
                   "Accept": "application/json"}
        return _http(API_BASE + path, method="POST", headers=headers, params=params, data=data)

    def _fetch_game_topic_id(self, app_id):
        r = self._app_get("/bbs/app/link/game/comments",
                          extra={"api_version": "4", "offset": "0", "limit": "30", "appid": str(app_id)})
        try:
            links = (r or {}).get("result", {}).get("links", []) or []
            if links and links[0].get("topics"):
                return str(links[0]["topics"][0].get("topic_id") or "")
        except Exception:
            pass
        return ""

    def post(self, title, content, app_id=None):
        """发帖（审批同意后真正执行）。返回结果 dict。"""
        if not app_id:
            r = self._app_get("/game/all_recommend/v2", extra={"offset": "0", "limit": "1"})
            try:
                res = (r or {}).get("result", {}) or {}
                items = res.get("games") or res.get("game_list") or res.get("list") or []
                if items:
                    first = items[0] or {}
                    app_id = first.get("app_id") or first.get("game_id") or first.get("appid")
            except Exception:
                app_id = None
        if not app_id:
            return {"ok": False, "error": "拿不到可发的游戏"}
        topic_id = self._fetch_game_topic_id(app_id)
        if not topic_id:
            return {"ok": False, "error": f"拿不到话题 topic_id（app_id={app_id}）"}
        text = json.dumps([{"checked": False, "text": content, "type": "text"}], ensure_ascii=False)
        body = {"draft": "0", "topic_ids": topic_id, "link_tag": "27", "text": text,
                "title": title, "desc": content}
        r = self._app_post("/bbs/app/api/link/post", body)
        if "_error" in r or "_http_error" in r:
            self._log("error", f"发帖失败: {r}")
            return {"ok": False, "result": r}
        self._log("info", f"小黑盒发帖成功: {r}")
        return {"ok": True, "result": r}

    def comment(self, link_id, content):
        """评论（端点待验证）。返回结果 dict。"""
        text = json.dumps([{"checked": False, "text": content, "type": "text"}], ensure_ascii=False)
        body = {"link_id": str(link_id), "text": text}
        r = self._app_post("/bbs/app/api/link/comment", body)
        if "_error" in r or "_http_error" in r:
            self._log("error", f"评论失败: {r}")
            return {"ok": False, "result": r}
        self._log("info", f"小黑盒评论成功: {r}")
        return {"ok": True, "result": r}

    def feeds(self, limit=20):
        """浏览首页帖子流，返回精简后的帖子列表。"""
        params = {"pull": "1", "last_pull": "1", "is_first": "0", "list_ver": "2",
                  "has_cache": "1", "netmode": "wifi"}
        r = self._app_get("/bbs/app/feeds", extra=params)
        if "_error" in r or "_http_error" in r:
            self._log("error", f"浏览帖子失败: {r}")
            return {"ok": False, "result": r}
        posts = []
        try:
            links = (r or {}).get("result", {}).get("links", []) or []
            for link in links[:limit]:
                posts.append({
                    "link_id": link.get("link_id") or link.get("linkid") or "",
                    "title": link.get("title", ""),
                    "desc": (link.get("description") or link.get("desc") or "")[:120],
                })
        except Exception:
            pass
        self._log("info", f"浏览了 {len(posts)} 条帖子")
        return {"ok": True, "posts": posts}

    def comments(self, link_id):
        """看某帖评论（端点待验证）。返回结果 dict。"""
        r = self._app_get("/bbs/app/link/comment_list", extra={"link_id": str(link_id)})
        if "_error" in r or "_http_error" in r:
            self._log("error", f"看评论失败: {r}")
            return {"ok": False, "result": r}
        rows = []
        try:
            res = (r or {}).get("result", {}) or {}
            src = res.get("comments") or res.get("list") or res.get("links") or []
            for c in src[:30]:
                rows.append(c.get("content") or c.get("text") or str(c))
        except Exception:
            pass
        self._log("info", f"看了 {len(rows)} 条评论")
        return {"ok": True, "comments": rows}

    def roll(self):
        """参与 0 元抽奖（简化版：找第一个活动就 join）。"""
        r = self._web_get("/store/roll_room_v2/room_list", extra={"limit": "5", "offset": "0"})
        award_id = ""
        try:
            items = (r or {}).get("result", []) or []
            for it in items:
                aid = it.get("award_id") or it.get("awardId") or it.get("id")
                if aid:
                    award_id = str(aid)
                    break
        except Exception:
            pass
        if not award_id:
            self._log("error", f"抽奖：找不到活动 {r}")
            return {"ok": False, "result": r}
        r2 = self._web_post("/store/roll_room_v2/join", query={"osType": "web"}, data={"award_id": award_id})
        if "_error" in r2 or "_http_error" in r2:
            self._log("error", f"抽奖失败: {r2}")
            return {"ok": False, "result": r2}
        self._log("info", f"参与抽奖 award_id={award_id}: {r2}")
        return {"ok": True, "result": r2}

    def claim(self):
        """领券（简化版：找第一个可领的就 get）。"""
        q = {"primary_id": "", "sub_id": "", "cate": "game", "app_version": WEB_VERSION,
             "limit": "20", "lastval": "", "need_is_get": "0"}
        r = self._web_get("/mall/coupon/center/detail/", extra=q)
        item_id, session = "", ""
        try:
            res = (r or {}).get("result", {}) or {}
            session = str(res.get("session", ""))
            for g in (res.get("games", []) or []):
                iid = g.get("id") or g.get("item_id")
                if iid:
                    item_id = str(iid)
                    break
        except Exception:
            pass
        if not item_id:
            self._log("error", f"领券：找不到可领的券 {r}")
            return {"ok": False, "result": r}
        r2 = self._web_get("/mall/coupon/center/get/", extra={"item_id": item_id, "session": session})
        if "_error" in r2 or "_http_error" in r2:
            self._log("error", f"领券失败: {r2}")
            return {"ok": False, "result": r2}
        self._log("info", f"领券 item_id={item_id}: {r2}")
        return {"ok": True, "result": r2}

    # ---- 工具注册（照 qq_bot._t 格式）----
    def _t(self, name, desc, params):
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
        T = self._t
        return [
            T("heybox_sign", "小黑盒每日签到（养号）。", {}),
            T("heybox_sign_state", "查小黑盒今日签到状态。", {}),
            T("heybox_post", "在小黑盒发帖（会先走审批，同意后才真正发出）。",
              {"title": {"type": "string", "description": "标题"},
               "content": {"type": "string", "description": "正文"}}),
            T("heybox_comment", "在小黑盒评论某条帖子（会先走审批，同意后才真正发出）。",
              {"link_id": {"type": "integer", "description": "帖子 link_id"},
               "content": {"type": "string", "description": "评论内容"}}),
            T("heybox_feeds", "浏览小黑盒首页帖子（看看大家在聊什么）。", {}),
            T("heybox_comments", "看小黑盒某条帖子的评论。",
              {"link_id": {"type": "integer", "description": "帖子 link_id"}}),
            T("heybox_roll", "参与小黑盒 0 元抽奖（免费）。", {}),
            T("heybox_claim", "领小黑盒优惠券（免费）。", {}),
        ]

    def handle_tool(self, name, args):
        args = args or {}
        try:
            if name == "heybox_sign":
                return self.sign()
            if name == "heybox_sign_state":
                return self.sign_state()
            if name in ("heybox_post", "heybox_comment"):
                # 发帖/评论走审批：只建审批，不立即执行（同意后由 server 执行）
                if self.request_approval:
                    self.request_approval(name, json.dumps(args, ensure_ascii=False))
                    self._log("info", f"已提交审批 [{name}]: {args}")
                else:
                    self._log("info", f"想 {name}（暂无审批通道）: {args}")
                return None
            if name == "heybox_feeds":
                return self.feeds()
            if name == "heybox_comments":
                return self.comments(args.get("link_id", 0))
            if name == "heybox_roll":
                return self.roll()
            if name == "heybox_claim":
                return self.claim()
        except Exception as e:
            self._log("error", f"工具 {name} 执行失败: {e}")
        return None
