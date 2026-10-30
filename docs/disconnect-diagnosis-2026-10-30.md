# 晚间断连只读诊断（2026-10-30）

时间为手机 Asia/Shanghai。用户描述约 19–21 点发生断连，Termux 仍开着。23:52–23:56 通过 ADB 读取设备，未启动或停止服务，未修改后台设置。

## 已确认

- 数据库记录 19:44:15 OneBot 连接丢失，随后连接本地端口被拒绝；19:46:31 鱼脑启动，19:47:17 QQ 重新建立 OneBot 会话。因此更早的 QQ 断连曾恢复，不能将这一段直接归因于之后的强停。
- 最后业务日志为 19:56:56 的记忆事件，之前 19:55:34 仍有聊天事件。
- `dumpsys activity exit-info com.termux` 记录 19:58:11.140 Termux 退出：reason=10 USER REQUESTED、subreason=21 FORCE STOP，description 包含 `forceStopPackageByUid 1000`。这是系统执行强停的证据；枚举名称不能证明用户亲手点击，也不能单凭 UID 1000 定位具体系统组件。
- 检查时 Termux 包 `stopped=true`，没有 TermuxService。鱼脑 server、supervisor、QQ 进程均不存在，API 连接拒绝，pid 文件引用的进程已不存在。
- 检查时可见的 Termux 进程由 23:49:41 的 `preStart_down` 启动，是 cached empty 状态。它不能证明原终端服务仍运行。
- Termux 已在 deviceidle 用户白名单，RUN_IN_BACKGROUND 与 RUN_ANY_IN_BACKGROUND 默认为 allow；仅添加同一个 Doze 白名单不足以解决此次强停。
- SQLite `PRAGMA quick_check` 为 ok，未发现数据库结构损坏；没有逐条检查记忆完整性。

## 相关历史与限制

Termux 当日 18:26、16:35 以及更早多次退出被标记为 LOW_MEMORY / single-cleaner。监管 App 的历史退出也出现 FORCE STOP / single-cleaner。这表明设备存在清理后台的历史，但不能据此断言 19:58 必然由同一清理器触发。现有 logcat 已无法提供 19:58 的完整调用链。

`max_phantom_processes=32` 是当前配置，但未取得此次 phantom 进程清理事件，不能把默认限制认定为原因。server.log 含多次历史异常，不能不看时间就作为本次崩溃证据。

守护脚本与鱼脑同属 Termux，系统强停整个包后，两者都无法继续执行恢复循环。当前未修改源码或全局 Android 进程限制，也未恢复运行。后续应检查厂商后台清理/自启动保护，再通过 App 恢复服务并进行覆盖锁屏及清理场景的观察。不要将之前一分钟后台采样描述为长期稳定性验证。

只读工具：`tools/check_disconnect_device.py`，输出时间、日志分类、脱敏连接事件及数据库完整性，不输出聊天正文、配置或凭据。

## 10 月 31 日 00:02 补充核对

用户确认后台高耗电、自启动均已开启，不能继续将缺少这两项权限当作原因。进一步核对强停记录的调用 PID 6010：当前进程名为 `com.vivo.sps:rms`，UID 1000；`ps` 与 ActivityManager 的进程开始时间均表明其已连续运行超过七天，跨越 10 月 30 日 19:58 的故障时间。因此可将该次强停请求定位到 vivo 的这一系统进程，但尚未确定具体触发规则或用户动作。

00:02 检查发现设备状态已改变：TermuxService 为 `isForeground=true`，包为 `stopped=false`；鱼脑 supervisor 与 server 进程存在，API HTTP 200、uptime 35 秒。本轮助手只做读取，没有代用户启动。当前前台服务正常不能证明故障前也始终正常。

尚未核实最近任务卡片的厂商“锁定”状态；普通 `mLockTaskAuth` 字段是 Android 屏幕固定机制，不能作为 vivo 后台卡片锁定的证据。未禁用 vivo 系统组件，未改变 phantom 进程配置。

随后用户确认刚刚锁定 Termux 后台卡片。00:03:55 只读状态确认 QQ 原生 ready、OneBot online，鱼脑与守护在运行。后台锁定是新增的保护措施；是否足以避免同类强停仍需长期及锁屏场景验证，不能将正常一分钟视为已根治。
