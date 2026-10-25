# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Fishbrain (dfy)

一个 24h 在线、像真人一样的 AI 朋友（赛博生命体），住在安卓手机里，走协议/API 玩 QQ、小黑盒、B站。
完整设计 + 分阶段路线见计划文件 `qq-joyful-rossum.md`（本地 `.claude/plans/` 下，不入库；**权威设计文档**，本文件只记「怎么开发」）。

## 架构（big picture）

两个组件，都跑在**用户的安卓手机**上，`127.0.0.1` 本地回环：

```
手机 ──┬── 鱼脑 (Python · Termux)              核心 = 生命体
       │     server.py   HTTP API (127.0.0.1:8765)，给 app 用
       │     brain.py    调 LLM（openai/anthropic/gemini），chat + chat_with_tools（function calling）
       │     memory.py   SQLite：软设定/长期记忆/日志/审批/token 用量/生命周期状态
       │     life.py     「心」：心跳。两段式——先定 drive（本能面），再定具体动作
       │     autonomy.py 自主循环：心跳调度 + 工具选择层(select_tools) + 分发(_route)
       │     qq_bot.py   OneBot v11 WS 客户端 + 工具注册表(get_tools/handle_tool，约 92 个 NapCat 工具)
       │     heybox.py   小黑盒 HTTP API：签到/抽奖/领券/发帖/评论/浏览（契约同 qq_bot）
       │
       ├── 监管 app (Flutter · 零第三方依赖)    6 tab：状态/聊天/审批/记忆/设定/部署
       │     连 http://127.0.0.1:8765
       │
       └── QQ = NapCat（proot-Debian 里的 linuxqq + NapCat 注入）
             OneBot v11 正向 WebSocket @ 127.0.0.1:8081
```

**生命体怎么「活」**：核心是 `life.py` 的心跳——它自己定下次醒来的时间（存 `life_state`，重启不丢），到点醒来先定「本能面 drive」（陪伴/表达/好奇/成长/存在/玩耍/none，多数时候 none=发呆），非 none 再按 drive 从工具注册表筛相关工具、用 function calling 选一个具体动作。

**能力接线方式（工具注册表，不是 prompt 枚举）**：核心状态工具在 `life._core_tools()`（update_mood/schedule_next_wake/set_drive）；高层动作在 `life.action_tools()`（publish/reflect）；平台(NapCat)工具在 `qq_bot.get_tools()`（约 92 个，返回 function-calling 定义，`handle_tool` 分发）。`autonomy.DRIVE_TOOLS` 是 drive→工具名映射，`select_tools(drive)` 筛选（现在全量；工具多了再加动态路由）。加新平台 = 写个模块暴露 `get_tools()` + `handle_tool()`，核心不改（`heybox.py` 就是照 `qq_bot.py` 写的现成例子）。

**记忆与回复（核心行为，改这里先读这段）**：
- 长期记忆 `memory.py`：`memories` 表每条带 `importance`(1-10)、`scope`(owner / private:{id} / group:{id} / global，**软隔离不是硬过滤**)、`absorbed`/`absorbed_by`(反思合并)。聊天召回走 `retrieve(query, k, context_scope)`，打分 `0.5×recency + 3.0×relevance + 2.0×importance + 1.0×scope亲和`（relevance 用字符 2-gram `_overlap`）。写入 `consolidate_memory` **只喂「对方说」不喂「我回」**（结构性堵 LLM 谎话固化），`reflect_memories` 把零碎记忆压成高级洞察、旧记忆软删除，`MAX_MEMORIES=300` 超了淘汰「importance×访问衰减」最低的。
- 回复策略 `qq_bot.py:_should_reply`：私聊必回、群 @必回、群里其他人走「主动插话漏斗」（预筛→相关性`Store.relevance`→概率→冷却 10 分钟最多 2 次）；群回复默认只 `@` 不 `cq_reply`，续接话题 3 分钟窗口。调参常量在 qq_bot.py 顶部（RELEVANCE_MIN / PROACTIVE_* / FOLLOWUP_WINDOW_SEC）。
- `build_system_prompt` 注入 `life_state.mood`（让回复带心情）+ 一句「有自己的想法不是客服」的观点引导。
- 回复可「分多条」：`_on_message` 走 `chat_with_tools`（带 `send_text` + `publish` 工具，但 deepseek-flash 思考模式**不支持强制 `tool_choice`**、模型基本不调工具），实际靠「模型返回带空行的多段文字 → **按空行拆成多条**」发出；`send_text`/主动消息超长按 `MAX_MSG_LEN=4000` 拆、片间 `MSG_SEND_DELAY=0.6s`，群聊只在第一条 `@`。

**QQ 数据流**：QQ 消息 → NapCat → OneBot WS(8081) → `qq_bot.py` → `brain.chat_with_tools`（带 `send_text`/`publish` 工具）→ 回复 → NapCat → QQ。

**HTTP 端点**（server.py）：`/status /logs /chat /memories /settings /llm /usage /approval/pending /command /qrcode /life/state /life/wake /qq/start /qq/stop /qq/status /qq/api /qq/refresh_qr /qq/apply_onebot /deploy/qq /deploy/status /heybox /heybox/status /heybox/sign /growth/evolve /persona/reset`。

## 常用命令

**鱼脑（Python，跑手机 Termux，也能本机测）**
```bash
cd fish
python server.py                                    # 起本地 API
python -m py_compile server.py brain.py memory.py life.py autonomy.py qq_bot.py heybox.py   # 语法检查
curl 127.0.0.1:8765/status                          # 探活
```

**监管 app（Flutter，零第三方依赖）**
```bash
cd app
flutter analyze
flutter build apk --release
adb install -r build/app/outputs/flutter-apk/app-release.apk
adb shell pm grant com.dfy.dfy_app com.termux.permission.RUN_COMMAND   # 重装后必做
```

**部署鱼脑到手机**（改完 `fish/*.py` 后；Windows Git Bash 记得前缀 `MSYS_NO_PATHCONV=1`，否则 `/data/...` 被转成 `E:/Git/...`）：
```bash
MSYS_NO_PATHCONV=1 adb push fish/*.py /data/local/tmp/
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux cp /data/local/tmp/*.py /data/data/com.termux/files/home/dfy/"
# 起/重启鱼脑（run-as 实测能 exec python，见下方「MSYS 路径转换坑」）
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux pkill -f 'python.*server.py'"
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux sh -c 'cd /data/data/com.termux/files/home/dfy && /data/data/com.termux/files/usr/bin/python -u server.py > server.log 2>&1 < /dev/null &'"
```

## 关键约定 / 坑（硬经验，别重踩）

- **端口固定**：鱼脑 8765、NapCat WS 8081、NapCat WebUI 6099，全 127.0.0.1。
- **凭据 vs 软设定**：LLM 的 key/base_url/model 存 **SQLite settings**（app「设定」页改，`/llm` 读写，`brain.py` 每次动态读）；**别**再放 config.toml。小黑盒 token 也已移到 **SQLite settings**（app「设定」页填、`/heybox` PUT 热生效；config.toml 里的旧 token 只在「从未在 app 设置过」时首次迁移一次，之后 app 是权威、清空即停用）。B站 cookie 才放 config.toml。
- **小黑盒 hkey 签名 + 凭据**：app 接口（签到/发帖）走外部 hkey 服务 `https://hkey.qcciii.com/hkey`；web 接口（浏览/抽奖/领券）本地算 `makeWebHkey`（`heybox.py` 里 MD5+混淆，移植自 yowiv08/heybox，已用 Node 原版比对值一致）。凭据 `pkey=xxx;x_xhh_tokenid=xxx` 必须是**手机 app 版** pkey——网页版 `user_pkey` / 加速器的 pkey 都不行（签到会「请重新登录」）。
- **QQ 用 NapCat 不是 Lagrange**：Lagrange 签名服务器停服。NapCat 在 proot-Debian 跑 linuxqq + xvfb，flags：`--no-sandbox --disable-gpu --disable-dev-shm-usage --no-zygote --use-gl=swiftshader`。
- **proot 版本坑（血泪）**：proot **5.1.107.99 有段错误回归**（跑 linuxqq 就 `proot warning: signal 11` 崩），5.1.107.100 修复。`deploy/setup-qq.sh` 已 pin 5.1.107.100。**QQ 起不来、proot 报 signal 11，先查 proot 版本**。另外 proot 是动态链接 libtalloc/libtermux-exec，`pkg upgrade` 会升级这些依赖。
- **Python 升级坑**：Termux Python 3.13→3.14 会搞坏 `proot-distro` 的 shebang（`#!/.../python3.13` 指向被删的版本）+ 把 `websocket-client` 留在旧 site-packages。症状：proot-distro 报 `No such file or directory`、`import websocket` 失败。修：`pkg upgrade` + `pip install websocket-client`。
- **QQ 登录不持久（keystore 落不了盘的根因已定位）**：keystore 在 `~/.config/QQ/global/nt_data/Login/.{uin}`，0 字节=没落盘。**实测 SIGTERM + `sleep 15` 优雅退出后 keystore 仍是 0 字节**——根因不是「没走到优雅退出」，而是 **proot 里 device.json（设备指纹）生成不出**（linuxqq 生成设备指纹要读硬件信息，ptrace 模拟下 syscall 失败），keystore 是加密凭据、拿不到 device.json 的设备密钥就写不进。佐证：mmkv（不加密普通状态）能正常写盘，keystore（加密）写不进。根治靠 chroot 绕开 proot。试过伪造 `/sys` 的 MAC、固定 hostname（`proot-distro login --hostname`）**均无效**——device.json 需**完整硬件序列号**（CPU/磁盘/主板 serial），proot 里 `/proc` 透传宿主（ARM 无 serial 字段）、`/sys` 空，读不到且伪造不了。QQ 风控会拦（人脸/`ErrCode 3`/「已登录」），**别频繁重登**。
- **QQ 掉线自愈 + 诊断字段**：`qq_keep_alive` 除了「进程没了自动拉起」，还会在「进程活着但 WS 8081 掉线、无新鲜二维码」（`_qq_stale`）持续超过 `QQ_STALE_GRACE_SECONDS=300`、且过 `QQ_STALE_RESTART_COOLDOWN=300` 后自动优雅重启 QQ（`_restart_qq`）。`/qq/status` 新增 `stale`（bool）与 `login_reason`（`nt_timeout`/`offline`/`waiting_scan`/`risk`/`login_error_N`/`unknown`，由 `_parse_qq_log_state` 解析 `/root/napcat/qq.log` 尾部得出）；ErrCode 无官方语义、只中性保留码号，风控只看明确文案（人脸/安全验证/冻结等）；app 部署页/登录页据此显示「掉线（自愈中）+ 原因」。
- **QQ 自动拉起熔断**：`qq_keep_alive` 连续 `QQ_AUTO_MAX_FAIL=5` 次自动拉起仍无法稳定在线 → 熔断 `QQ_AUTO_CIRCUIT_SECONDS=1800` 秒不拉起；只有「持续稳定在线 `QQ_AUTO_STABLE_SECONDS=300` 秒」或「用户显式启动 QQ」才清零。`/qq/status` 暴露 `auto_circuit`/`auto_fail_count`，app 部署页显示「已熔断」提示。注意：`qq_start(reset_circuit=False)` 是参数化的——只有 `/qq/start` 端点、`qq_refresh_qr` 传 `reset_circuit=True` 清零，自动拉起路径不传（否则计数永远累加不到阈值）。
- **发 QQ 空间动态（publish 审批链路）**：`publish` 工具（`life.action_tools()` 和聊天场景都有）走审批 `kind="publish"`，summary 是**纯文本 detail（正文）非 JSON**；同意后 `execute_approval` 调 `qq_bot.send_qzone_mood(text)` → `call_api("send_qzone_msg")` 发说说。坑：NapCat action 名是 **snake_case `send_qzone_msg`**，不是 `SendQzoneMsg`；`send_qzone_mood` 里失败（retcode != 0）会 `add_log("error","qq",...)` 记录。
- **QQ 断连诊断信号优先级（血泪，别误判）**：查 qq.log / 鱼脑日志时这几个信号极易混淆——①「测试Packet 超时」是**误报**，每次登录都有、超时后 QQ 照常收发消息，不是断连；②「等待网络连接/网络已连接」是**启动噪音**，每次 QQ 重启都有，不是断连；③「账号状态变更为离线」才是**账号真掉线**；④ 鱼脑侧 `QQ 连接断开...Connection timed out`（recv 30 秒超时）或 `Connection refused` 才是**断连重连**，根因是 **NapCat 在 proot 里间歇性卡住**（8081 WS 服务时好时坏）。「Connection timed out」= NapCat 心跳（30s 一次）中断 30 秒，可据此拿到卡住的精确时间点。
- **诊断 QQ 的两个小坑**：① 判断 QQ 进程用 `ps -A -o CMD`（完整参数），`ps -o NAME` 只显示进程名 `qq`、会漏掉 `qq --no-sandbox` 参数导致误判「QQ 没在跑」；② `/qq/stop` 是 **POST** 端点（GET 返回 `{"error":"not found"}`），curl 要带 `-X POST`；③ 判断 QQ 状态用 `qrcode.png` 的 mtime，**别信 qq.log 文本**——NapCat 的文本日志在 proot 里会停写、只剩 hash 乱码，容易误判「QQ 卡住」而实际已出码。
- **OneBot 消息格式**：NapCat `messagePostFormat="array"`，消息是 CQ 段数组；`qq_bot._extract_text` 处理 text/image/face/at/reply 段，引用消息走 `get_msg`（`call_api` 用 echo 匹配响应，事件在独立 worker 线程避免死锁）。
- **NapCat 首次登录的 OneBot 配置是空的**：`onebot11_<uin>.json` 首次由 NapCat 自动生成时 `websocketServers` 是 `[]`（不监听 8081），所以扫码登录后 qq_bot 还连不上、一直「连接断开 5 秒重连」。**必须登录后写 8081 模板 + 重启 QQ 才生效**——`server.py:apply_onebot` 从配置目录文件名探测 uin（`_find_login_uin` 只认最近 `QQ_LOGIN_UIN_FRESH_SECONDS=600` 秒内生成的文件，旧文件跨会话残留不算「已登录」）、写 `~/dfy/onebot11.json` 模板、后台重启 QQ（`_restart_qq`），`_onebot_has_ws` 判断已配好就跳过。这是「账号在线」之前的最后一个坎。
- **QQ 起不来的另一个坑：`/root/napcat` 目录不存在**：`QQ_START_CMD` 把日志写到 `/root/napcat/qq.log`、`xvfb.log`，但 `setup-qq.sh` 没建这个目录 → 重定向失败、`./qq` 根本没跑（症状：部署页「未运行」）。现在 `QQ_START_CMD` 开头 `mkdir -p /root/napcat` 兜底，`setup-qq.sh` 也建了 `$DEBIAN_ROOTFS/root/napcat`。
- **NapCat 下载镜像会失效**：`setup-qq.sh` 里 GitHub 回退镜像 `ghproxy.net` 已死（502），现用 `gh-proxy.com` + `gh.llkk.cc` 双回退（`NAPCAT_ZIP_GH`/`NAPCAT_ZIP_GH2`）。这类第三方 GitHub 镜像经常挂，**下次 NapCat 下载失败先 `curl -I` 测这些 URL 还活着没**。linuxqq 的腾讯 CDN 固定版本 URL 也是「会过期」的老隐患。
- **app 起鱼脑走 TermuxRunner**：`app/lib/termux.dart` 经 MethodChannel `dfy/termux`（`MainActivity.kt`）`startForegroundService(intent)` 触发 Termux RunCommandService（是服务、不是广播）。**AndroidManifest 必须有 `FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_SPECIAL_USE` 权限**——之前「启动失败」就是漏了 FOREGROUND_SERVICE。冷启动命令前台阻塞、无 `&`（带 `&` 会被 Termux 清掉）。`run()` 发完即忘（起长驻 server.py 用）；`runAndWait()` 挂 `PendingIntent`（extra `com.termux.RUN_COMMAND_PENDING_INTENT`）+ 动态 BroadcastReceiver，回传 `result` bundle 的 `stdout`/`stderr`/`exitCode`（装环境等有限时长命令用）。**血泪：这个 PendingIntent 必须用 `FLAG_MUTABLE`，不能用 `FLAG_IMMUTABLE`**——IMMUTABLE 会让 Termux `send(code, resultIntent)` 的结果 extras 合并不进来，receiver 只能拿到 base intent 的 `sessionId`、拿不到 `result` bundle（`exitCode=-1` 静默误判失败）。结果 bundle 的 key 是 `"result"`（外层）、`"stdout"`/`"stderr"`/`"exitCode"`（内层）。
- **Termux 命令的两个前置，缺一静默失败**（非技术用户从 0 部署的头号坑）：① `allow-external-apps = true`（`~/.termux/termux.properties`，受 ContentProvider 保护、app 代写不了，只能引导用户在 Termux 里粘一行再 `termux-reload-settings`）；② `com.termux.permission.RUN_COMMAND` 权限（Android 设置→应用→权限，或 `pm grant`）。`MainActivity.preflight()` + 向导 Step 0「环境检查」现在会逐项显示 ✅/❌ 并给修复指引。
- **app 的「启动/停止/重启」只管鱼脑，不管 QQ**：`status.dart` 已把这三个按钮与 QQ 解耦（只 `pkill server.py`/起 server.py，不调 `qqStop`/`qqStart`）。QQ 的启停走「部署」页单独的「启动 QQ / 停止 QQ」。这样重启鱼脑不会连带重启 QQ、不用重新扫码。
- **RUN_COMMAND 权限是运行时权限，安装不自动给**：`com.termux.permission.RUN_COMMAND`（`prot=dangerous`）控制 app 能否叫 Termux 跑 shell。fresh install 默认**未授予**，app 触发 Termux 会静默失败——现在由向导 Step 0「环境检查」检测 +「授权限」弹窗兜底。`adb install -r`（更新）实测在 Android 15/16 **不会撤**已授的权限（旧笔记「会撤」是错的）；开发机手动补用 `pm grant com.dfy.dfy_app com.termux.permission.RUN_COMMAND`。
- **MSYS 路径转换坑（Windows Git Bash 下 adb 头号坑）**：`/data/...`、`/tmp/...` 这类 `/` 开头的参数会被 Git Bash 转成 `E:/Git/...` 再交给 adb，报 `run-as: exec failed ... No such file or directory`——**极像「run-as 不能 exec python」，其实是路径被转**。修法：命令前缀 `MSYS_NO_PATHCONV=1`，凡是 adb 命令带 `/data/...` 都加。
- **adb `run-as` 能 exec python、能 cd（这台机实测，别信「Android 16 收紧」）**：`run-as com.termux /data/data/com.termux/files/usr/bin/python --version` 返回 3.14.8，`run-as com.termux sh -c 'cd .../dfy && pwd'` 也成。所以起/重启鱼脑可直接走 adb（见「部署鱼脑」命令），不必只靠 app「启动」或手动 Termux。老的「收紧」印象是把上面的 MSYS 路径转换误判了。
- **Termux 没权限写 `/data/local/tmp`**（`drwxrwx--x shell:shell`，只有 adb/shell 能写；Termux 是普通 app UID 写不进去）。`setup-qq.sh` 下载 linuxqq/NapCat 必须写到 `$PREFIX/tmp`（`/data/data/com.termux/files/usr/tmp`），否则一下载就 Permission denied、脚本 `exit 1`——这正是「装 QQ 环境卡在安装 linuxqq」的根因。
- **`Store.get_logs(category, level, limit)` 第二参是 `level` 不是 `limit`**：按位置传会把 limit 当 level 过滤（`WHERE level=40` 永远空）。所以 `get_logs("deploy", 40)` 是错的，要 `get_logs("deploy", limit=40)`。踩过：server.py 的 Deployer 状态、`/chat`、life.py 的「最近发生的事」都曾因此返回空。改 `get_logs` 一律用 `limit=` 关键字。
- **`app/assets/fish/*.py` 要跟 `fish/` 同步**：那是「部署向导」写进 Termux 的嵌入副本（含 life.py/autonomy.py）。改完 `cp fish/*.py app/assets/fish/`；`app/assets/deploy/` 同理同步 `setup-qq.sh`/`setup-fish.sh`/`onebot11.json`（`setup-fish.sh` 是装 Python 环境的分步脚本，`deploy/` 根目录也要留一份）。
- **版本号**：改 `app/` 代码 bump `app/pubspec.yaml` 的 `version`（x.y.z+n）。
- **不入库**：`fish/config.toml`、`fish/fish.db`、`__pycache__/`（完整清单见 `.gitignore`）。

## 待做（详见计划文件）

- `fish/` 里 `bilibili.py` 还没写（B站签到/发布）。小黑盒 `heybox.py` 已写（签到/抽奖/领券/发帖/评论/浏览；发帖/评论走审批，同意后 `server.execute_approval` 真正执行；读结果回喂走 `life.decide_action` 的 `extra_context`）。
- 部署向导端到端（阶段 B）：**已在真机 arm64 上走通**（装鱼脑 → 装 QQ → 扫码登录 → OneBot 配置 → 账号在线）。剩余：结果回传依赖较新 Termux（老 Termux 会静默失败）；QQ 登录持久化（下面那条）；B站还没接；小黑盒已接（token 走 app「设定」页配置、热生效，`/heybox` PUT）；凭据还差「手机 app 版 pkey」（网页版/加速器的 pkey 不被 app 接口认，签到会「请重新登录」）。
- QQ 登录持久化（老硬伤）。
- 工具多了加动态路由（`select_tools` 现在全量返回）。

## 注意

- `fish/config.toml` 是本机测试副本（空 key），真凭据在手机 Termux 的 `~/dfy/`。
- dfy 根目录一堆 `*.sh`/`*.py`（setup-napcat.sh、run-qq.sh、migrate_llm.py 等）是**一次性部署脚本**，不是源码；`lagrange/`、`napcat/` 是下载的二进制/安装包。
- 项目已公开在 github.com/99cz99/fishbrain（public）。CLAUDE.md 与所有提交文件都在这个公开仓库里，**以后编辑/提交别再写个人信息**（Windows 用户名、QQ 号、邮箱、`C:\Users\...` 绝对路径）。本仓库提交身份是 repo 级的 `99cz99` + GitHub noreply 邮箱（覆盖全局的 QQ 号邮箱）。
