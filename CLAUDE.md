# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Fishbrain (dfy)

一个 24h 在线、像真人一样的 AI 朋友（赛博生命体），住在安卓手机里，走协议/API 玩 QQ、小黑盒、B站。
完整设计 + 分阶段路线见计划文件 `qq-joyful-rossum.md`（用户全局 `~/.claude/plans/qq-joyful-rossum.md`，不入库；**权威设计文档**，本文件只记「怎么开发」）。

## 架构（big picture）

两个组件，都跑在**用户的安卓手机**上，`127.0.0.1` 本地回环：

```
手机 ──┬── 鱼脑 (Python · Termux)              核心 = 生命体
       │     server.py   HTTP API (127.0.0.1:8765)，给 app 用
       │     brain.py    调 LLM（openai/anthropic/gemini），chat + chat_with_tools（function calling）
       │     memory.py   SQLite：软设定/长期记忆/日志/审批/token 用量/生命周期状态
       │     memory_service.py 聊天语义召回、原话证据包、同会话读集与来源版本校验
       │     life.py     「心」：心跳。两段式——先定 drive（本能面），再定具体动作
       │     autonomy.py 自主循环：心跳调度 + 工具选择层(select_tools) + 分发(_route)
       │     qq_control.py QQController：独立线程统一调度生命周期、原生登录与 WebUI 控制
       │     qq_bot.py   OneBot v11 WS 客户端 + 工具注册表(get_tools/handle_tool，约 92 个 NapCat 工具)
       │     qq_delivery.py 分段发送日志（qq_outbox）、状态确认与上下文原位回写
       │     qq_faces.py   QQ 表情名字/编号解析与聊天历史标记，共用一份映射
       │     qq_media.py QQ 原图缓存、容器路径映射与网络图片兜底
       │     qq_config.py OneBot 发送配置迁移，保留已有凭据和其他连接
       │     delivery.py 共享发送异常（SendUnconfirmedError / SendCancelledError）
       │     heybox.py   小黑盒 HTTP API：签到/抽奖/领券/发帖/评论/浏览（契约同 qq_bot）
       │
       ├── 监管 app (Flutter · Android 8.0/API 26+ · 仅一个插件 image_picker)    6 tab：状态/聊天/审批/记忆/设定/部署
       │     连 http://127.0.0.1:8765
       │
       └── QQ = NapCat（proot-Debian 里的 linuxqq + NapCat 注入）
             OneBot v11 正向 WebSocket @ 127.0.0.1:8081
```

**守护**：鱼脑由 `deploy/run-fish.sh`（supervisor 循环）拉起——`server.py` 崩了 2s 自动重启；「启动/停止/重启」都是对这个 supervisor 的 pkill/拉起（见「常用命令」），**不是**直接 `python -u server.py`。

**生命体怎么「活」**：核心是 `life.py` 的心跳——它自己定下次醒来的时间（存 `life_state`，重启不丢），到点醒来先定「本能面 drive」（陪伴/表达/好奇/成长/存在/玩耍/none，多数时候 none=发呆），非 none 再按 drive 从工具注册表筛相关工具、用 function calling 选一个具体动作。

**能力接线方式（工具注册表，不是 prompt 枚举）**：核心状态工具在 `life._core_tools()`（update_mood/schedule_next_wake/set_drive）；高层动作在 `life.action_tools()`（publish/reflect）；平台(NapCat)工具在 `qq_bot.get_tools()`（约 92 个，返回 function-calling 定义，`handle_tool` 分发）。`autonomy.DRIVE_TOOLS` 是 drive→工具名映射，`select_tools(drive)` 筛选（现在全量；工具多了再加动态路由）。加新平台 = 写个模块暴露 `get_tools()` + `handle_tool()`，核心不改（`heybox.py` 就是照 `qq_bot.py` 写的现成例子）。**坑**：`brain.chat_with_tools` 的 function calling 在 openai 兼容接口使用原生工具调用；anthropic/gemini 使用普通文本接口返回 JSON 行动计划，经工具白名单和参数校验后执行，格式错误会明确报错并停止该轮行动。

**记忆与回复（核心行为，改这里先读这段）**——原则：**智能体记忆统一**（私聊/群聊/各平台都是同一个智能体的记忆，不按渠道隔离；参考 Generative Agents「统一存+按相关性取」/ MemGPT「工作记忆+召回记忆」/ aidevops「隐私边界」）。
- 记忆 v2：详见 `docs/memory-system.md`。`memory.py:Store` 继承 `memory_system.MemorySystem` 与 `qq_delivery.DeliveryStore`，区分事件/事实/暂定认识/未完事项/自身经历；使用稳定人物 ID、来源 ID、隐私继承、事实更新和删除墓碑。提炼在后台执行，反思不整批吸收事实。活跃事实目标 300 条，新记忆保护 24h、固定记录免淘汰，超额归档而非删除证据。旧 self/fact 洞察迁移时隔离，先通过 SQLite backup 备份。**主人在群里说话仍按 group 范围召回**；当前事件也要从长期召回及工作摘要中排除，防输入重复。每条记忆带 `emotion` 列（内容情感色彩 -1~1），召回时情绪明显的带标注（`memory.format_memory_line`）。
- 短期上下文 `memory.py:short_term`：**统一存**——`_on_message` 在回复决策**之前**就把所有消息（含非 @ 群消息）写进去、带来源标签（`[群 X] 某人:` / `[私聊] 某人:`）；`get_short_term(scope)` 按同一套隐私层级取「最近 N 条」（owner 全渠道、群只看本群、私聊只看本人）。**坑（已踩已修，别回退）**：当前消息既进了 short_term（带前缀）又作为 final user turn 单独 append，会让 LLM 看到同一条消息两遍、误以为「对方发了两遍」——拼 `chat_messages` 时要用写 short_term 时的**原文 `written_user`**（在图片/链接处理改 `text` 之前就存好）把 `history[-1]` 里这条剔掉，见 `qq_bot.py:_on_message`。
- 助手上下文按发送段维护：`qq_delivery.py` 在提交前保存发送状态，并写入带「未确认、勿自动重发」标记的助手上下文；确认后原位更新。尚未提交的尾段不进入聊天历史，不能再等整轮全部成功才回写，避免第一段已送达但模型以为自己没回答。人格成长仍只吸收已确认的实际回复，未知结果不视为成功行为。忘记/重置使旧发送内容和匹配信息失效，迟到回报不能重建已删除的上下文。
- 回复策略 `qq_bot.py:_should_reply_quick`：私聊/@我必回、@别人不回；**其它群消息交给 `brain.should_reply()`（LLM 语义判断「该不该回」，不是关键词）**。旧的「主动插话漏斗」（`Store.relevance` 2-gram + 概率 + follow-up 重叠）已删。
- `build_system_prompt` 注入 `life_state.mood`（让心情**隐式**影响语气、别直说）+ 一句「有自己的想法不是客服」的观点引导。心情是**四维 PAD+归因**：`life_state` 带 `valence`(-1..1)/`arousal`(0..1)/`dominance`(-1..1)/`attribution`(-1..1)，`mood` 标签由 `memory.mood_label` 从四维**唯一推导（不落盘）**；读时按**双时间常数 + 不对称**衰减回**有效基线**（`temperament` 气质基线 + 天气/昼夜偏移，`memory.decay_mood`/`get_life_state`；正情绪衰减慢、负情绪快速恢复），事件 nudge（`autonomy._nudge_mood`）走**方向性饱和**（只挡推向更极端、不挡恢复）；`mood_label` 负情绪高唤醒区用 dominance+attribution 三分「烦躁(愤怒)/内疚/害怕」。心情还经「回忆情绪明显的记忆」**情感传染**（`life.heartbeat`）、注入四维数值**影响 drive 选择**（`life._prompt`）。所有算法超参（快慢比例/正负 τ 缩放/传染强度/各 nudge 系数/最低强度阈值）存 settings `mood_params`，app「设定→算法超参」页可改，`memory.mood_param()` 读、缺键回退常量默认。
- 自身人格画像统一：`personality.py` 的 `personality_traits` 保存自动特质与私人依据；`settings.self_profile` 保存手动条目，`get_settings()` 合并后经 `render_self_profile()` 注入所有聊天和 life prompt。QQ 实际发送回复后记录同范围 self/experience，引用原始事件并增加反思积分；私有自身经历可以经提炼及单独的隐私/证据审查塑造公开特质，但私人故事、关系事实、人物及依据 ID 不进入公开提示词。新特质至少2个独立根事件，修改旧特质至少3个；删除依据仅撤销支持不足的自动条目。`growth_status` 提供进度、跳过原因及更新时间；画像保存须带 `self_profile_revision`，冲突返回409。自动一小时节流及 allow_self_evolve 门控，手动强制检查。`evolve()` 只改软设定；旧隔离洞察不自动恢复。详见 `docs/memory-system.md`。
- 回复走纯文本、可「分多条」：`_on_message` 只用 `chat`（无审批通道时）或 `chat_with_tools`（**只带 `publish` 发空间工具**，`send_text` 工具已删——说话=纯文本、人设活在语言层，工具只做真动作）；多段靠正文自然空行 → 按空行拆成多条；回复加 `temperature=1.1`/`frequency_penalty=0.4`/`presence_penalty=0.2`（只聊天路径，JSON/判断路径不加）；`send_text` 方法/主动消息超长按 `MAX_MSG_LEN=4000` 拆、片间 `MSG_SEND_DELAY=0.6s`，群聊只在第一条 `@`。

**QQ 数据流**：QQ 消息 → NapCat → OneBot WS(8081) → `qq_bot.py` → `brain.chat`/`chat_with_tools`（只带 `publish` 工具）→ 回复 → NapCat → QQ。

**HTTP 端点**（server.py）：
所有端点（含图片）均要求 `Authorization: Bearer <token>`，无授权返回 401。鱼脑首次启动在脚本目录生成 `.api-token`（0600）；app 通过已授权的 Termux 命令读取并仅在内存缓存，HTTP 不提供取令牌接口。不要打印令牌、完整回传 extras 或将令牌入库。升级此机制必须同时更新 APK 和鱼脑：先装新 APK，再更新并重启鱼脑。

- **GET**：`/status /logs /chat /memories /settings /llm /usage /approval/pending /approval/recent /qrcode /life/state /qq/status /heybox/status /deploy/status /stickers /stickers/<id>/image`
- **POST**：`/command /life/wake /qq/start /qq/stop /qq/refresh_qr /qq/quick_login /qq/api /qq/apply_onebot /deploy/qq /heybox/sign /growth/evolve /persona/reset /approval/<id>/approve /approval/<id>/reject /stickers`
- **PUT**：`/settings /llm /heybox /qq/quick_login_uin /stickers/<id> /memories/<id>`
- **DELETE**：`/memories/<id> /stickers/<id>`

**QQ 控制 API 契约（1.0.46 起）**：

- `/qq/start`、`/qq/stop`、`/qq/quick_login`、`/qq/refresh_qr`、`/qq/apply_onebot` 成功响应只表示任务受理。读取 `/qq/status.operation` 的 `id/kind/status/phase/code/error` 确认结果；`queued/running/waiting` 为在途，`unknown` 为结果未知，不能自动重放。认证操作完成也不等于 OneBot 已连通，分别检查 `account_ready` 与 `online`。
- 同目标、同类型的在途请求复用任务；停止可抢占。更换目标取消旧代次任务；明确停止后须先启动，不能由快捷登录或配置操作悄悄恢复。鱼脑重启后继续确认未知任务、恢复未完成停止，并保留原生拒绝结果。
- `qq_login_target` 是用户目标（兼容旧 `qq_quick_login_uin`），`qq_last_account` 是观察到的账号；不能用生命周期事件覆盖明确目标。`qq_desired_state`、`qq_last_operation` 等内部状态受 `/settings` 写入保护，目标通过 `PUT /qq/quick_login_uin` 修改。
- WebUI Credential 仅授权控制接口，进程会话变化时失效；仅明确鉴权拒绝允许重新鉴权一次。登录/配置写入超时不重试提交，原生账号状态未知也不能凭历史候选列表再次登录。只读/鉴权查询允许较长等待，单次调用按总预算限制；原生写入仍保持短超时。配置读取不持有副作用锁，最终写入前须再校验代次。Linux 不执行 Windows Registry20 的 GUID 修正；当前 `_refresh_device_fingerprint` 仅返回不支持诊断，不写设备身份。
- App `ApiException` 保留业务错误码/原因，登录页和部署页读取 `QQControlStatus`，区分排队、初始化、结果未知、QQ 已登录和 OneBot 已连接。不得恢复“已提交=成功”“已配置账号=正在自愈”或所有错误都显示“鱼脑在线吗”的文案。

## app（Flutter）UI 设计体系（Liquid Glass，改 UI 先读这段）

`app/lib/` 是 Liquid Glass 视觉体系（深/浅双主题，暗色为主），核心三个文件：
- `theme.dart`：设计令牌 `AppColors`（背景/表面/玻璃/语义色）、`AppTheme.light/dark`（完整 ThemeData）、`themeNotifier`（ThemeMode）。玻璃材质用 `AppTheme.glassSurface(brightness)`（半透明表面）、`glassHighlight(brightness)`（边缘高光）、`glassGradient(brightness)`（镜面渐变，顶部略亮模拟反光）。
- `widgets.dart`：共享组件 `SectionCard`（分组卡片）、`GlassBar`（底部玻璃条，`BackdropFilter` 真模糊）、`StatTile`（KPI 统计块）、`StatusPill`/`EmptyState`/`InfoRow`/`SectionHeader`。
- `main.dart`：底部导航是**自绘浮动胶囊**（`Row` 平铺 6 项 + `SizedBox(height:44)`），不是 M3 `NavigationBar`——M3 内部 SafeArea 会把底部安全区加在 height 上、且固定 32dp 指示胶囊，导致改高度无感（Flutter 已知问题 PR #150807）。

关键约定：
- **玻璃 ≠ 每处加模糊**：`BackdropFilter` 真模糊只用于底部导航条（内容从下方滚过）；内容卡片用 `glassGradient` 镜面渐变（贴纯色背景，逐卡模糊既无收益又卡顿）。
- 内容卡片一律走 `SectionCard`/`glassGradient`，别逐页手写 `Container(color:...)`。
- `SegmentedButton` 一律加 `showSelectedIcon: false`（iOS 无对勾）。
- 悬浮导航配合 `Scaffold(extendBody: true)`，各页 ListView 底部留白 ~120（内容不被玻璃条遮挡）。
- 设置页是 **drill-down**（顶层图标行 → 子页编辑），字段用「标签在上 + 输入框」，不是浮动 `labelText`。
- 状态页是仪表盘（大标题 hero + KPI 统计条 + 时间线）。
- 设置子页是独立路由，控件变更必须重建当前子页；仅刷新父设置页不会同步顶部路由。按子页提交字段，避免保存名字时覆盖后台生成的新自我画像。密码框必须 `maxLines: 1`，不能传 null。
- API 请求包含授权、20 秒连接/响应/读取超时，并检查 HTTP 200 中的业务失败；`ApiClient` 随 HomePage 释放。定时刷新采用单轮互斥，页面销毁后不回写 UI。设置加载失败提供重试入口。
- 记忆 API 使用 `limit`（最多 1000）和 `offset`，App 可加载更多，显示来源与状态，支持纠正、固定、完成/取消事项和忘记。忘记会清理原始依据、关联派生认识和相关短期上下文，保留去重墓碑。表情包缓存仍最多 12 个 Future，解码宽度 112。

## 常用命令

**鱼脑（Python，跑手机 Termux，也能本机测）**
```bash
cd fish
python server.py                                    # 起本地 API
python -m py_compile server.py brain.py memory.py memory_system.py memory_policy.py personality.py life.py autonomy.py qq_bot.py qq_control.py qq_delivery.py qq_faces.py qq_media.py qq_config.py heybox.py weather.py delivery.py
curl -H "Authorization: Bearer $(cat .api-token)" http://127.0.0.1:8765/status   # 同目录探活，别打开 shell xtrace
```

仓库根目录执行 `python -m unittest discover -s fish/tests -v` 跑 Python 回归；不使用真凭据或发送真实消息。QQ 发送与图片回归集中在 `fish/tests/test_qq_delivery.py` 和 `fish/tests/test_qq_transport.py`；生命周期回归在 `fish/tests/test_qq_control.py`，App 状态语义在 `app/test/qq_control_test.dart`。

**监管 app（Flutter，仅一个插件 image_picker）**
```bash
cd app
flutter analyze --no-pub
flutter test --no-pub
flutter build apk --release --no-pub
adb install -r build/app/outputs/flutter-apk/app-release.apk
adb shell pm grant com.dfy.dfy_app com.termux.permission.RUN_COMMAND   # 仅检查确认缺少权限时补授，更新安装通常保留权限
```

缺少依赖映射时先 `flutter pub get`，不要顺便升级依赖或改镜像来源。网络受限时可给本次 Gradle 命令传代理 JVM 属性，不把个人代理地址或全局设置写进仓库。**直接运行 Gradle 可能沿用 `android/local.properties` 的旧版本号**，最终打包优先使用 Flutter 构建入口，并核对 APK/手机的 versionName、versionCode。

Flutter 命令在联网版本检查阶段长时间无输出时，可在本次命令加全局参数 `--no-version-check`，例如 `flutter --no-version-check analyze --no-pub`；先确认本次进程和 SDK 锁状态，不删除缓存锁或终止其他任务的进程。此参数不跳过分析、测试或构建。

正式发布签名从未入库的 `app/android/key.properties` 加载（storeFile、storePassword、keyAlias、keyPassword）；正式构建设置 Gradle 属性 `productionRelease=true`，缺配置即失败。未提供发布材料时本地构建仍沿用原 Debug 签名以兼容设备升级，不能视为正式发布；不要自行生成新证书替换既有身份。

**部署鱼脑到手机**（改完 `fish/*.py` 后；Windows Git Bash 记得前缀 `MSYS_NO_PATHCONV=1`，否则 `/data/...` 被转成 `E:/Git/...`）：
```bash
MSYS_NO_PATHCONV=1 adb shell mkdir -p /data/local/tmp/dfy-update
MSYS_NO_PATHCONV=1 adb push fish/*.py /data/local/tmp/dfy-update/
MSYS_NO_PATHCONV=1 adb push deploy/run-fish.sh /data/local/tmp/dfy-update/
# 停鱼脑（supervisor + server.py 一起杀，再清 pid）
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux pkill -f '[r]un-fish.sh'"
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux pkill -f '[s]erver.py'"
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux rm -f /data/data/com.termux/files/home/dfy/.fish.pid"
# 仅复制本次暂存源码，避免混入 /data/local/tmp 下的诊断脚本；不覆盖配置、数据库或令牌
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux cp /data/local/tmp/dfy-update/*.py /data/local/tmp/dfy-update/run-fish.sh /data/data/com.termux/files/home/dfy/"
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux sh -c 'cd /data/data/com.termux/files/home/dfy && /data/data/com.termux/files/usr/bin/python -m py_compile *.py'"
# 起鱼脑（supervisor 循环：server.py 崩了自动拉起。别加 cd 前缀——会让后台 subshell 占着 adb 管道导致命令挂住；用绝对路径 + 整体重定向）
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux sh -c 'sh /data/data/com.termux/files/home/dfy/run-fish.sh >> /data/data/com.termux/files/home/dfy/server.log 2>&1 < /dev/null &'"
```

涉及发送表迁移或 OneBot 配置升级时，优先使用带备份/验证的更新脚本，代替上面的直接复制流程。以下在仓库根目录、Git Bash 执行；暂存目录只放本次文件，工具脚本不会复制进运行目录：

```bash
MSYS_NO_PATHCONV=1 adb shell mkdir -p /data/local/tmp/dfy-delivery-update
MSYS_NO_PATHCONV=1 adb push fish/*.py deploy/run-fish.sh deploy/setup-fish.sh deploy/setup-qq.sh deploy/onebot11.json tools/deploy_memory_update.py tools/deploy_qq_delivery_update.py /data/local/tmp/dfy-delivery-update/
MSYS_NO_PATHCONV=1 adb shell "run-as com.termux /data/data/com.termux/files/usr/bin/python /data/local/tmp/dfy-delivery-update/deploy_qq_delivery_update.py --stage /data/local/tmp/dfy-delivery-update"
```

该脚本要求更新前已有在线 QQ 会话；先备份源码/数据库，再按运行模块白名单切换并探活，随后备份、迁移实际账号配置。运行模块切换或健康检查失败会恢复备份；配置阶段失败会报告错误，需要检查保存的 OneBot 备份，不能视为整条流程已自动回滚。成功输出含 `backup` 路径；用 `deploy_memory_update.py --verify --backup <该路径>` 复核数据和配置保留。新增运行模块时同时更新此工具的 `MODULES` 白名单。首次添加发送表还会生成 `<数据库>.pre-qq-delivery-v1.db`；备份含私人内容，仅留手机本地。回退应成套恢复源码、数据库与实际 OneBot 配置，避免混装。

**QQ 控制器更新与受控验证**：

- `tools/deploy_qq_control_update.py` 成套替换 `server.py`、`qq_bot.py`、`qq_control.py`，先备份源码和数据库，核对旧/新哈希，重启鱼脑后确认原 QQ 会话身份未变。暂存目录需带通用部署工具及 `qq-control-manifest.json`；每次部署重新核对基线，不能直接复用历史清单覆盖其他改动。APK 与 Termux 源码须配套更新，单装 APK 不替换已运行源码。
- `tools/qq_control_cold_device.py prepare` 确认全部原生写入进程退出后，备份完整 Debian HOME、NapCat 配置和 machine-id，并逐文件校验；`start --backup <目录>` 先消耗单次门禁再发启动，响应丢失也不重放；`observe` 只读查询。在线复制、只备份 Login 或错位 Registry20 目录不能称为完整一致性凭据快照。
- `tools/verify_qq_control_device.py` 只读检查；`--exercise` 要求已在线，验证已登录短路及配置应用，不发送聊天。`recover_qq_scan_device.py` 已改为请求控制器换码，不再绕过调度杀进程或直接另启 proot。
- 原生快照含私人数据，只留手机本地。文件复制不能承诺恢复外部/系统绑定密钥；恢复数据库或原生资料须先停对应写入者并审查快照之后的数据，不能覆盖在线目录。详见 `docs/releases/v1.0.46.md`。

## 关键约定 / 坑（硬经验，别重踩）

- **端口固定**：鱼脑 8765、NapCat WS 8081、NapCat WebUI 6099，全 127.0.0.1。
- **部署向导区分安装与升级**：环境检查通过后才能安装；`allow-external-apps` 需用户在 Termux 设置。源码及脚本先写 `~/dfy/.update/`，环境安装和编译通过后才停止旧 supervisor/server、复制新代码并启动，最后检查带授权的 `/status`。已有安装跳过模型填写与 QQ 重装，保留 config.toml、fish.db、.api-token 和 QQ 资料；config.toml 只在不存在时创建。暂存失败不停止旧服务，向导切换失败会报告错误，尚无自动回滚。不能仅写文件再调用启动，否则 PID 守卫会保留旧代码。更新时一并暂存 `onebot11.json`，探活后提交 `/qq/apply_onebot`；新后端热加载 OneBot，网络连接可能短暂重建，但不为此重启原生 QQ。
- **向导资源清单需联动**：新增模块须同步 `app/assets/fish/`、`wizard.dart` 资源映射及 `tools/deploy_memory_update.py:MODULES`；当前 17 个运行模块包含 `qq_control.py`、`qq_faces.py`。使用同步脚本及 `--check` 校验全部模块。`WizardPage.assetLoader` 是测试注入点，生产默认读取 rootBundle。完整新装/升级向导仍需真机验证，不能以模拟命令测试替代。
- **`setup-qq.sh` / `onebot11.json` 必须与 `server.py` 同目录（Termux `~/dfy/`）**：`/deploy/qq` 读 `setup-qq.sh`、`/qq/apply_onebot` 读 `onebot11.json`。repo 里它们在 `deploy/`，app 部署向导会写入 Termux；纯 adb 手动部署时（下方命令只 push `fish/*.py` + `run-fish.sh`）需一并 push。
- **凭据 vs 软设定**：LLM 的 key/base_url/model 存 **SQLite settings**（app「设定」页改，`/llm` 读写，`brain.py` 每次动态读）；**别**再放 config.toml。小黑盒 token 也已移到 **SQLite settings**（app「设定」页填、`/heybox` PUT 热生效；config.toml 里的旧 token 只在「从未在 app 设置过」时首次迁移一次，之后 app 是权威、清空即停用）。B站 cookie 才放 config.toml。
- **小黑盒 hkey 签名 + 凭据**：app 接口（签到/发帖）走外部 hkey 服务 `https://hkey.qcciii.com/hkey`；web 接口（浏览/抽奖/领券）本地算 `makeWebHkey`（`heybox.py` 里 MD5+混淆，移植自 yowiv08/heybox，已用 Node 原版比对值一致）。凭据 `pkey=xxx;x_xhh_tokenid=xxx` 必须是**手机 app 版** pkey——网页版 `user_pkey` / 加速器的 pkey 都不行（签到会「请重新登录」）。
- **QQ 用 NapCat 不是 Lagrange**：Lagrange 签名服务器停服。NapCat 在 proot-Debian 跑 linuxqq + xvfb，flags：`--no-sandbox --disable-gpu --disable-dev-shm-usage --no-zygote --use-gl=swiftshader`。
- **proot 版本坑（血泪）**：proot **5.1.107.99 有段错误回归**（跑 linuxqq 就 `proot warning: signal 11` 崩），5.1.107.100 修复。`deploy/setup-qq.sh` 已 pin 5.1.107.100。**QQ 起不来、proot 报 signal 11，先查 proot 版本**。另外 proot 是动态链接 libtalloc/libtermux-exec，`pkg upgrade` 会升级这些依赖。
- **Python 升级坑**：Termux Python 3.13→3.14 会搞坏 `proot-distro` 的 shebang（`#!/.../python3.13` 指向被删的版本）+ 把 `websocket-client` 留在旧 site-packages。症状：proot-distro 报 `No such file or directory`、`import websocket` 失败。修：`pkg upgrade` + `pip install websocket-client`。
- **QQ 免扫码登录的证据边界**：设备同哈希 NapCat 包表明 WebUI `SetQuickLogin` 与 Shell `-q` 最终调用同一 NTQQ 原生快捷登录；WebUI Credential、账号选择与原生票据须分开诊断。1.0.46 已验证同一设备/UID/版本/资料与身份下，一次完整 QQ 进程退出后无需扫码恢复，两个 `global/nt_data/Login/.<account>` 文件仍为 0 字节。不能据此判定票据未落盘，空文件的具体角色也未证实；已撤回“device.json 已定因、必须 chroot”的旧断言。手机重启、强制回收及快照恢复尚未验证。详见 `docs/qq-login-chain-audit-2026-10-30.md` 和 `docs/releases/v1.0.46.md`。
- **QQ 操作统一调度**：`qq_control.QQController` 独立于 LLM 心跳线程，启动、停止、重启、快捷登录、换码及配置应用都经统一调度。停止可抢占；副作用前校验任务代次、目标账号、平台开关及明确停止意图。不得绕过控制器另起线程杀 QQ、启动 proot 或调用原生登录。初始化、重连、账号状态未知或动作结果未知时保留会话并查询，禁止自动重复登录或回退重启。`quick_login_configured` 只表示保存账号，`self_healing` 只表示有在途任务，都不保证认证成功。
- **`/qq/status` 当前在线证据**：`control_version=2`。1.0.47 起 `account_ready` 接受当前 WebUI 原生 ready/账号证据，或 WebUI 不可用时同一 QQ 进程会话内、身份匹配、90 秒内 OneBot 明确 online=true 的证据；`account_source` 标识 webui/onebot/none，`native_ready` 单独保留原生查询结论。`online` 还要求当前 OneBot 连接和身份匹配，明确离线及身份冲突不能判在线。OneBot 状态时间独立于任意收帧时间，断连/停止清除；新帧不能延长旧在线证据。WebUI 超时不能直接改写为账号离线，`control_error` 保留控制异常。原生查询证据超过 15 秒失效；旧身份、TCP 端口、配置文件和日志均不能作为登录成功证据。
- **QQ 自动拉起熔断**：控制器启动宽限 120 秒，自动启动间隔至少 120 秒，明确离线时快捷登录间隔至少 900 秒；自动启动预算累计达到 5 时熔断 1800 秒。业务稳定在线 300 秒后清零，接受手动操作也重置预算。明确停止、平台禁用、已有任务、等待扫码/验证或原生失败结果时不自动叠加启动。`qq_desired_state` 跨鱼脑重启保留；保活脱离 LLM 线程不等于解决 Android 低内存回收。
- **发 QQ 空间动态（publish 审批链路）**：`publish` 工具（`life.action_tools()` 和聊天场景都有）走审批 `kind="publish"`，summary 是**纯文本 detail（正文）非 JSON**；同意后 `execute_approval` 调 `qq_bot.send_qzone_mood(text)` → `call_api("send_qzone_msg")` 发说说。坑：NapCat action 名是 **snake_case `send_qzone_msg`**，不是 `SendQzoneMsg`；`send_qzone_mood` 里失败（retcode != 0）会 `add_log("error","qq",...)` 记录。
- **QQ 断连诊断信号优先级（别误判）**：「测试Packet 超时」和启动时的「等待网络连接/网络已连接」不能证明账号掉线；明确的账号离线事件才是账号状态证据。**recv 超时也不能直接判定 NapCat 卡死**：模板心跳 30 秒，实测正常间隔可超过 30 秒。当前空闲 30 秒后直接发送只读 `get_status`，再等 10 秒；期间收到有效 JSON 对象即恢复正常等待，否则关闭连接、5 秒后重连。探测不能调用会等待接收线程回填的 `call_api`，否则死锁；探测次数不是故障次数。空帧立即按连接关闭处理。账号状态/API 响应优先在接收线程处理，生命周期落库及耗时业务走 worker。详情见 `docs/qq-transport.md`。
- **发送确认与异常分类**：NapCat `timeout.baseTimeout` 至少 30000ms，鱼脑 `SEND_CONFIRM_TIMEOUT=45` 秒，未知后 `SEND_LATE_GRACE=30` 秒补等；自身消息可在等待 API 时提前确认，不必等满超时。`SendUnconfirmedError` 表示已提交但结果未知，记 warning/action，禁止外层改写为「发送失败」或自动重发；`SendCancelledError` 表示回复过时/控制条件改变，正常取消记 info/action。明确平台拒绝才记失败。`/qq/api.ok` 仍要求成功 API 响应；发送侧确认不等于对方已读。API 请求固定原连接、echo 唯一，迟到成功响应按原 echo 关联。日志落库失败回退 stderr，不能杀死传输线程。详情见 `docs/qq-transport.md` 和 `docs/qq-delivery-fix-2026-10-29.md`。
- **分段发送状态机不可回退**：`qq_outbox` 保存 queued/sending/unconfirmed/confirmed/rejected/cancelled；确认当前段后才继续尾段，未知段不重发。新会话消息、记忆版本/人设/平台开关/账号变化、停止信号或回复超时都会阻止旧尾段继续；较旧排队用户消息保留记忆，但已有更新消息时不再单独回复。进程重启将 sending→unconfirmed、queued→cancelled，不自动补发；窗口结束后迟到确认只补记状态/上下文，不恢复已取消尾段。`/qq/status.transport.delivery` 提供发送状态计数。
- **自身消息回报必须校验目标**：OneBot 启用 `reportSelfMessage=true`、`enableForcePushEvent=true`、`messagePostFormat="array"`，`message_sent` 仅用于确认，不能作为用户消息生成回复。匹配要求自身账号、目标会话、时间、精确 text/face/at 内容及唯一候选，message_id 去重；图片不靠内容猜测关联。私聊自身回报的 user_id 可能是自身账号，应读 target_id。NapCat 4.18.41 调试日志在补 target_id 之前打印，网络推送前才由原始 peerUin 补入，不能据日志中缺目标就猜会话。同账号手工向同目标发送完全相同内容仍有不可完全区分的边界。
- **戳一戳方向**：NapCat 私聊 `user_id` 是会话对象，`sender_id` 才是发起者、`target_id` 是目标；群聊以 `user_id` 为发起者。身份优先取事件 self_id，回退当前会话；缺字段记未知，别猜成被戳。group_id 缺失/0 为私聊，别输出「群 None」。自主戳动按 API 返回值区分确认/未知/拒绝，事件日志再记录实际方向。
- **好友/加群审批需保留平台参数**：kind 为 friend/group 的 summary 是 JSON（summary、flag、sub_type），界面展示其中 summary。同意与拒绝都调用对应 OneBot API；数据库以 pending 条件更新，防重复执行。平台未确认或旧记录缺 flag 时记录 failed 并返回错误，不静默显示成功、不自动重试；旧申请需重新发起。不要把 publish 的纯文本正文改成这套 JSON。
- **设置与人格重置契约**：PUT /llm 未提交的字段保留，明确提交空字符串表示清空（包括视觉模型回退）；不要恢复「所有空字符串都忽略」。人格重置同时清空 self_profile、life_state、长期记忆、短期上下文、名字和人设，防旧画像继续注入提示词；LLM 配置、审批等仍保留。
- **QQ 进程与二维码诊断**：使用 `ProcessManager` 验证同 UID 的可执行文件、PID/启动时间及专属 proot 会话组；不能仅用命令字符串含 QQ 判断主进程，禁止 `pkill -f` 广泛匹配 QQ。先退出原生主进程，再清理会话组，未确认退出禁止叠加启动。Android 外部 UID 的 hidepid 拒绝与本 UID 信息不可读须区分。`/qq/stop` 为 POST，只返回任务受理；二维码须属于当前会话且足够新，mtime 或日志都不能证明账号已登录。
- **OneBot 消息格式**：NapCat `messagePostFormat="array"`，消息是 CQ 段数组；`qq_bot._extract_text` 处理 text/image/face/at/reply 段，引用消息走 `get_msg`（`call_api` 用 echo 匹配响应，事件在独立 worker 线程避免死锁）。
- **回复支持 QQ 系统表情**：`qq_faces.py` 统一维护 `FACE_IDS` 与名字/编号转换，`qq_bot._to_segments` 同时接受 `[表情:名字]`（如 `[表情:呲牙]`）和旧上下文中的 `[表情:22]` 等 ASCII 非负整数编号，生成原生 `face` 段。`qq_delivery.unit_text` 回写聊天历史时优先使用中文名字，未收录名字的编号保留数字并可再次解析；平台负责校验编号。教学句从 `FACE_IDS.keys()` 生成，**别两处手改表情列表，也别回退成只识别名字**。face id 用 **NT QQ 新版编号**（鼓掌=99/坏笑=101/委屈=106），别用老版 PC QQ 编号（42/44/49 会发成「爱情/星星/拥抱」）。
- **分多条先看实际发送记录**：回复按正文空行拆成多条，同一段内表情和文字可以在同一条。模型自称“发送坏了”不构成故障证据；先看原始回复是否有空行，再核对同批次 `qq_outbox.position/status`，需要时只读调用 `get_msg` 验证平台上是否存在各条消息。
- **表情包本地图库**：收到 GIF（image 段）→ `_collect_sticker` SHA256 去重、写 `~/dfy/stickers/`、**自动收集用受控情绪词当默认标签**（`STICKER_EMOTIONS`，vision 同时产出描述+情绪词）、**手动标签自由文本** → SQLite `stickers` 表；回复写 `[表情包:描述]` **双向子串匹配**（`get_sticker_by_tag`：X in tag or tag in X）、读本地文件 **base64 发图**。只收 GIF、`MAX_STICKERS=200` FIFO 删最旧（跳过 pinned）、教学句 cap 30（固定优先）。
- **表情包 app 管理**：`/stickers`（GET 列表 / POST 添加 base64 或 url）、`/stickers/<id>`（PUT 改 tag/pinned、DELETE 删行删文件）、`/stickers/<id>/image`（出图）。app「设定→管理表情包」页（`sticker.dart`）可增删/固定/改标签（自由文本 + 情绪词快捷 chips）；相册上传用 `image_picker`（**已打破零第三方依赖**），选图不传宽高/quality 以保 GIF 动画。app 的 `_EMOTIONS` 只是 UI chips，Python `STICKER_EMOTIONS` 只用于自动收集。
- **图片 URL 别当网页抓**：`qq_bot._extract_urls` 必须跳过 `image` 段（图片 `data.url` 不是网页，`_fetch_url_content` 抓了会把 JPEG 原始字节当正文塞进上下文、变一堆二进制垃圾）。图片内容是走 `_get_image_bytes` + `_vision_or_ocr` 的，不是 URL 抓取。
- **图片失败先查 Rkey 和原图缓存**：Rkey 返回格式错误/TLS 失败会使图片 URL 无效，`get_image` 也可能返回 file not found。`qq_media.py` 优先按合法 MD5 标识定位 QQ `nt_data/Pic/*/Ori` 原图并校验文件 MD5，再尝试消息 URL、API 文件/URL，API 后再查缓存；URL 失败不能跳过本地文件。容器路径只映射到限定缓存目录，检查路径逃逸、图片头与 20 MiB 上限。错误日志仅记录来源、错误类型和 HTTP/API 状态，不打印签名 URL、Rkey 或私有路径。外部 Rkey 服务故障本身未修复；无缓存且远程不可用的新图仍可能失败。
- **OneBot 首次配置与升级迁移**：首次账号配置可能缺少 WS 8081；`QQBackend.configure()` 用 `qq_config.delivery_config` 合并当前配置和模板，保留令牌、其他连接及自定义设置，补齐发送回报/数组格式/事件推送和等待时间。通过 WebUI `GetConfig` 读取，变化时向 `SetConfig` 提交 `{"config": "序列化后的 JSON 字符串"}`，由 NapCat 保存并热加载网络；不得重启原生 QQ 来修 OneBot。写结果未知时只读回查，不重复写入。只有当前原生认证成立后才能配置，旧配置文件名及 `_find_login_uin` 不能作为认证依据。
- **QQ 起不来的另一个坑：`/root/napcat` 目录不存在**：`_qq_start_cmd()` 把日志写到 `/root/napcat/qq.log`、`xvfb.log`，但 `setup-qq.sh` 没建这个目录 → 重定向失败、`./qq` 根本没跑（症状：部署页「未运行」）。现在 `_qq_start_cmd()` 开头 `mkdir -p /root/napcat` 兜底，`setup-qq.sh` 也建了 `$DEBIAN_ROOTFS/root/napcat`。
- **NapCat 下载镜像会失效**：`setup-qq.sh` 里 GitHub 回退镜像 `ghproxy.net` 已死（502），现用 `gh-proxy.com` + `gh.llkk.cc` 双回退（`NAPCAT_ZIP_GH`/`NAPCAT_ZIP_GH2`）。这类第三方 GitHub 镜像经常挂，**下次 NapCat 下载失败先 `curl -I` 测这些 URL 还活着没**。linuxqq 的腾讯 CDN 固定版本 URL 也是「会过期」的老隐患。
- **app 起鱼脑走 TermuxRunner**：`app/lib/termux.dart` 经 MethodChannel `dfy/termux`（`MainActivity.kt`）`startForegroundService(intent)` 触发 Termux RunCommandService（是服务、不是广播）。**AndroidManifest 必须有 `FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_SPECIAL_USE` 权限**——之前「启动失败」就是漏了 FOREGROUND_SERVICE。冷启动命令前台阻塞、无 `&`（带 `&` 会被 Termux 清掉）。`run()` 发完即忘（起长驻 server.py 用）；`runAndWait()` 挂 `PendingIntent`（extra `com.termux.RUN_COMMAND_PENDING_INTENT`）+ 动态 BroadcastReceiver，回传 `result` bundle 的 `stdout`/`stderr`/`exitCode`（装环境等有限时长命令用）。**血泪：这个 PendingIntent 必须用 `FLAG_MUTABLE`，不能用 `FLAG_IMMUTABLE`**——IMMUTABLE 会让 Termux `send(code, resultIntent)` 的结果 extras 合并不进来，receiver 只能拿到 base intent 的 `sessionId`、拿不到 `result` bundle（`exitCode=-1` 静默误判失败）。结果 bundle 的 key 是 `"result"`（外层）、`"stdout"`/`"stderr"`/`"exitCode"`（内层）。
- **Termux 命令的两个前置，缺一静默失败**（非技术用户从 0 部署的头号坑）：① `allow-external-apps = true`（`~/.termux/termux.properties`，受 ContentProvider 保护、app 代写不了，只能引导用户在 Termux 里粘一行再 `termux-reload-settings`）；② `com.termux.permission.RUN_COMMAND` 权限（Android 设置→应用→权限，或 `pm grant`）。`MainActivity.preflight()` + 向导 Step 0「环境检查」现在会逐项显示 ✅/❌ 并给修复指引。
- **app 的「启动/停止/重启」只管鱼脑，不管 QQ**：`status.dart` 已把这三个按钮与 QQ 解耦（pkill `run-fish.sh`+`server.py` / 起 `run-fish.sh`，即 supervisor；不调 `qqStop`/`qqStart`）。QQ 的启停走「部署」页单独的「启动 QQ / 停止 QQ」。这样重启鱼脑不会连带重启 QQ、不用重新扫码。
- **RUN_COMMAND 权限是运行时权限，安装不自动给**：`com.termux.permission.RUN_COMMAND`（`prot=dangerous`）控制 app 能否叫 Termux 跑 shell。fresh install 默认**未授予**，app 触发 Termux 会静默失败——现在由向导 Step 0「环境检查」检测 +「授权限」弹窗兜底。`adb install -r`（更新）实测在 Android 15/16 **不会撤**已授的权限（旧笔记「会撤」是错的）；开发机手动补用 `pm grant com.dfy.dfy_app com.termux.permission.RUN_COMMAND`。
- **MSYS 路径转换坑（Windows Git Bash 下 adb 头号坑）**：`/data/...`、`/tmp/...` 这类 `/` 开头的参数会被 Git Bash 转成 `E:/Git/...` 再交给 adb，报 `run-as: exec failed ... No such file or directory`——**极像「run-as 不能 exec python」，其实是路径被转**。修法：命令前缀 `MSYS_NO_PATHCONV=1`，凡是 adb 命令带 `/data/...` 都加。
- **adb `run-as` 能 exec python、能 cd（这台机实测，别信「Android 16 收紧」）**：`run-as com.termux /data/data/com.termux/files/usr/bin/python --version` 返回 3.14.8，`run-as com.termux sh -c 'cd .../dfy && pwd'` 也成。所以起/重启鱼脑可直接走 adb（见「部署鱼脑」命令），不必只靠 app「启动」或手动 Termux。老的「收紧」印象是把上面的 MSYS 路径转换误判了。
- **Termux 没权限写 `/data/local/tmp`**（`drwxrwx--x shell:shell`，只有 adb/shell 能写；Termux 是普通 app UID 写不进去）。`setup-qq.sh` 下载 linuxqq/NapCat 必须写到 `$PREFIX/tmp`（`/data/data/com.termux/files/usr/tmp`），否则一下载就 Permission denied、脚本 `exit 1`——这正是「装 QQ 环境卡在安装 linuxqq」的根因。
- **`Store.get_logs(category, level, limit)` 第二参是 `level` 不是 `limit`**：按位置传会把 limit 当 level 过滤（`WHERE level=40` 永远空）。所以 `get_logs("deploy", 40)` 是错的，要 `get_logs("deploy", limit=40)`。踩过：server.py 的 Deployer 状态、`/chat`、life.py 的「最近发生的事」都曾因此返回空。改 `get_logs` 一律用 `limit=` 关键字。
- **`app/assets/fish/*.py` 要跟 `fish/` 同步**：那是「部署向导」写进 Termux 的嵌入副本。改完执行 `python tools/sync_fish_assets.py`，自动生成所有运行模块副本及 wizard.dart 清单；发布前 `python tools/sync_fish_assets.py --check`，不要只校验部分文件。`app/assets/deploy/` 同理同步 `setup-qq.sh`/`setup-fish.sh`/`onebot11.json`（`setup-fish.sh` 是装 Python 环境的分步脚本，`deploy/` 根目录也要留一份）。
- **版本号**：改 `app/` 代码或嵌入资源都 bump `app/pubspec.yaml` 的 `version`（x.y.z+n）。2026-10-31 当前为 `1.0.48+62`；APK 路径 `app/build/app/outputs/flutter-apk/app-release.apk`。
- **心跳恢复契约**：`Life.decide_action()` 返回含 `tool_calls` 与可选 `failed/cancelled` 标记的对象；首轮和读结果回喂都必须检查。失败退避保存合并最新心情，不能用完整情绪版本直接取消调度；明确叫醒用 `life_state.wake_revision`、记忆重置用 epoch 保护。详见 `docs/heartbeat-timeout-fix-2026-10-30.md`。

## 最近验证（2026-10-30）

- 后续按用户要求执行真实 App 冷启动链：状态页点启动鱼脑，部署页点启动 QQ，登录页确认在线；未通过 ADB shell/API 代发启动或登录。19:35:01 App 启动 QQ，19:35:51 首次采样确认 ready/OneBot 在线。本次初始上次任务 failed/interrupted，阻止 QQ 自动拉起，必须明确手动启动；不能把仅启动鱼脑等同于 QQ 也会恢复。此前最近的已在线会话页面验证不能替代这类 App 全链路测试。细节见 `docs/app-driven-start-login-2026-10-30.md`。

- `1.0.47+61` 修复用户再次报告的 App 登录状态误判：QQ 实际在线，旧 WebUI 2 秒等待反复超时；一次放宽等待的查询耗时 8.243 秒仍返回 ready，OneBot 实时状态及身份一致。新增明确在线证据时效/账号/会话绑定，控制慢查询与业务在线分离，App 当前在线优先于旧任务错误。186 项 Python、24 项 Flutter 及静态分析通过；手机配套更新仅重启鱼脑，保留原 QQ 进程，实际两页均显示在线，APK 和 17 模块一致。详见 `docs/releases/v1.0.47.md` 与 `docs/qq-login-slow-control-verification-2026-10-30.json`。

- GitHub 发布：`v1.0.46`，附 `app-release.apk` 与 `app-release.apk.sha256`；源码、测试及本轮修复/验收文档同步至仓库。APK SHA-256 为 `e5e6940de89e3bcb2277b573a87e70e49de8e94ac20f15c7dbb635765a157993`，发布说明须保留一次完整 QQ 冷启动与手机重启/强制回收未验证的区别。

- `1.0.46+60` 已配套更新 App 和 Termux；180 项 Python、23 项 Flutter 测试及静态分析通过。统一 QQController 独立于 LLM 线程，所有 QQ 操作异步串行，停止抢占并持久化；未知结果和原生初始化不重复登录，OneBot 配置热应用。17 个运行模块与 APK/设备一致。完整旧 QQ 退出并校验 5,572 文件快照后，新进程一次带 -q 启动，无再次扫码恢复同一账号 ready 及 OneBot 在线。两个 Login 文件仍为 0 字节，因此不能作为票据健康指标；手机重启/强制回收及快照恢复未验证。详见 `docs/releases/v1.0.46.md`。
- 该次完整冷启动停止确认约 14.14 秒，23.1 秒观察到新进程初始化，55.5 秒已确认同一账号 ready 和 OneBot 在线。最终 APK 与设备 `base.apk` 哈希一致，Termux 命令权限保留；真机「部署」「扫码登录」两页均显示“QQ 已登录，OneBot 已连接”。已登录快捷登录/配置任务完成时原生会话保持不变，SQLite quick_check 为 `ok`。脱敏证据见 `docs/qq-control-fix-verification-2026-10-30.json`；这些是当次验证结果，不代表实时在线监测。

- QQ 后续恢复：用户扫码后曾确认账号 online、WebUI ready 与 OneBot 在线。Login 目录空文件仍存在，但其角色及真实票据存储位置未证实，不能称为凭据未落盘。原停止流程会匹配启动器，WebUI 失败回退及阶段判断已由后续 1.0.46 修复。2026-10-30 的源码链路审查与 9 个隔离复现见 `docs/qq-login-chain-audit-2026-10-30.md`；该审查阶段未退出当前在线会话，后续冷启动验证见 1.0.46 记录。
- 启动确认后续修复：`1.0.45+59` 已安装；18 项 Flutter 测试及静态分析通过。手机从 App 点停止再启动，实测冷启动约 22 秒恢复在线，最后保持运行；不再固定等 3 秒就提示未确认。运行源码无需再次更新。详见 `docs/releases/v1.0.45.md`。
- `1.0.44+58` APK 已覆盖安装；手机三个心跳模块配套更新，全部 16 个运行模块与 APK 一致，配置、令牌和其余源码保留。数据库与更新前备份一致，528 条记忆，完整性检查为 ok。更新前鱼脑已停止，更新后保持停止。
- 164 项 Python 回归、15 项 Flutter 测试、Flutter 静态分析、APK 构建及手机隔离验证通过。未运行真实模型生成或发送消息；尚未验证安装后的在线心跳。没有修改手机后台策略或远端发布。
- 专用更新工具 `tools/deploy_heartbeat_update.py` 与只读验证工具 `tools/verify_heartbeat_update.py`；说明见 `docs/releases/v1.0.44.md`。同线程 QQ 保活延迟已在 1.0.46 通过独立控制器处理；系统低内存回收仍需独立验证。
- **不入库**：`fish/config.toml`、`fish/fish.db`、`.api-token`、`app/android/key.properties`、keystore、`__pycache__/`、构建输出（完整清单见 `.gitignore`）。

## 最近验证（2026-10-29）

- `1.0.43+57` release APK 已覆盖安装连接的手机，ADB 返回 Success，实际版本号核对一致；APK 内 16 个运行模块及部署模板逐字节匹配源码，忽略换行差异后手机运行源码也与安装资源一致。沿用现有本机构建签名，未更换证书。
- 145 项 Python 回归、14 项 Flutter 测试与 Flutter 静态分析通过。表情修复在手机上验证了名字/数字编号解析及上下文往返；该次鱼脑更新前后记忆均为 482 条，人格特质与表情包库逐行一致，审批和配置保留，SQLite quick_check 为 ok。
- 本轮手机仅替换 `qq_bot.py`、`qq_delivery.py`、`qq_faces.py`，其余源码哈希保持；专用脚本 `tools/deploy_qq_faces_update.py` 校验暂存哈希、备份并支持切换失败回退。备份数据库仅留手机。初查发现的四个旧模块哈希差异，经后续核对为换行差异。
- 未主动向 QQ 联系人发送测试消息；用户自然交流后，只读查询平台记录确认原生表情，以及同一轮回复的两条独立消息。安装后鱼脑 HTTP 200、QQ 在线、原有 Termux 权限仍为 granted。完整 UI 新装/升级流程及长期模型效果仍需验证。
- 本轮说明见 `docs/qq-face-fix-2026-10-29.md` 和 `docs/releases/v1.0.43.md`；先前发送确认及图片缓存修复见 `docs/qq-delivery-fix-2026-10-29.md` 和 `docs/qq-media-send-diagnosis-2026-10-29.md`。

## 2026-10-31 记忆召回修复

- 本地版本 1.0.48+62。新增 `memory_service.py`，安装资源和向导清单为 18 个运行模块。QQ 聊天分离当前问题与用户话题，使用现有模型做只读语义选择；角色来源、原话、时间、同会话读集及发送前版本校验配套。心跳/行动共用来源展开和范围校验。
- 人工纠正通过已有 event_key 的 manual 前缀标注；新提炼契约 version=2，要求言语性质及可匹配原话片段。无数据库 schema 迁移，旧记录未批量清理。目录超过预算标为 incomplete，供应商失败标为 unavailable，不能混同“用户没说过”。检索增加模型调用及 token 用量。
- 208 项 Python 回归、24 项 Flutter 测试、静态分析和 release 构建通过。手机隔离模型验证及历史聊天路径回放通过，无平台测试发送。专用更新脚本 `tools/deploy_memory_recall_update.py` 校验哈希、备份、保留 QQ 进程并支持源码回退。详细边界见 `docs/releases/v1.0.48.md`。
- 更新前 QQ 已离线，有未知结果的 quick_login；本次未重放登录。记忆修复与 QQ 登录恢复分开判断。完整 v3 设计及其 24 个场景尚未全部实现或验收，不能描述为全量重构完成。
- 本轮还定位到系统进程发出 Termux 强停请求，详见 `docs/disconnect-diagnosis-2026-10-30.md`；后台权限已经开启不代表后台清理已被排除，新增卡片锁定后尚需长期验证。不能把记忆修复或短时在线采样描述为后台强停问题已解决。
- 发布资产为 `v1.0.48` 的 `app-release.apk` 和 `app-release.apk.sha256`。新装/升级仍需配套更新 APK 与 Termux 运行代码；不能只覆盖 APK 就称鱼脑已升级。仓库仅保存脱敏诊断说明与匿名评测，原始聊天、数据库、令牌、配置和签名材料不入库。

## 待做（详见计划文件）

- `fish/` 里 `bilibili.py` 还没写（B站签到/发布）。小黑盒 `heybox.py` 已写（签到/抽奖/领券/发帖/评论/浏览；发帖/评论走审批，同意后 `server.execute_approval` 真正执行；读结果回喂走 `life.decide_action` 的 `extra_context`）。
- 部署向导端到端（阶段 B）：**已在真机 arm64 上走通**（装鱼脑 → 装 QQ → 扫码登录 → OneBot 配置 → 账号在线）。剩余：结果回传依赖较新 Termux（老 Termux 会静默失败）；QQ 恢复的剩余故障模式验证（见下一条）；B站还没接；小黑盒已接（token 走 app「设定」页配置、热生效，`/heybox` PUT）；凭据还差「手机 app 版 pkey」（网页版/加速器的 pkey 不被 app 接口认，签到会「请重新登录」）。
- QQ 恢复后续验证：一次完整进程重启免扫码已通过受控对照；继续分别验证手机重启、系统强制回收、长期稳定性和备份恢复。空文件角色及原生票据内部存储未证实，不再将“0 字节”作为故障指标。
- 记忆 v2 与人格成长仍需真实模型长期效果评测；本轮自动验证及真机部署状态见「最近验证」，详细边界见 `docs/memory-system.md`。
- 已确认但本轮未修：聊天中的明确风格要求没有持久保存入口；模型自身台词可能经 belief 提炼循环固化为人格，仅提示“不把自述当证据”未能可靠阻止。后续需增加来源约束及明确风格控制，不能把该现象描述为已解决。
- 工具多了加动态路由（`select_tools` 现在全量返回）。

## 注意

- `fish/config.toml` 是本机测试副本（空 key），真凭据在手机 Termux 的 `~/dfy/`。
- dfy 根目录一堆 `*.sh`/`*.py`（setup-napcat.sh、run-qq.sh、migrate_llm.py 等）是**一次性部署脚本**，不是源码；`lagrange/`、`napcat/` 是下载的二进制/安装包。
- 项目已公开在 github.com/99cz99/fishbrain（public）。CLAUDE.md 与所有提交文件都在这个公开仓库里，**以后编辑/提交别再写个人信息**（Windows 用户名、QQ 号、邮箱、`C:\Users\...` 绝对路径）。本仓库提交身份是 repo 级的 `99cz99` + GitHub noreply 邮箱（覆盖全局的 QQ 号邮箱）。
