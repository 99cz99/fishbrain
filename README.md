# Fishbrain（dfy）

一个 24h 在线、像真人一样的 AI 朋友（赛博生命体），住在安卓手机里，通过协议/API 使用 QQ 和小黑盒。B站接入仍在规划中。

最新版 **v1.0.40**（Android 8.0+）：[下载 APK](https://github.com/99cz99/fishbrain/releases/latest/download/app-release.apk) · [版本与升级说明](https://github.com/99cz99/fishbrain/releases/latest)。

## 架构

两个组件，都跑在**用户的安卓手机**上，`127.0.0.1` 本地回环：

```
手机 ──┬── 鱼脑 (Python · Termux)        核心 = 生命体
       │     server.py   HTTP API (127.0.0.1:8765)
       │     brain.py    调 LLM
       │     memory.py   SQLite 记忆
       │     memory_system.py 事件、事实、认识、待办与来源追溯
       │     personality.py 统一人格与可撤销的成长依据
       │     life.py     「心」：心跳，决定 drive 和动作
       │     autonomy.py 自主循环 + 工具选择
       │     qq_bot.py   OneBot v11 客户端
       │     heybox.py   小黑盒 API
       │
       ├── 监管 app (Flutter)            6 tab 控制台
       │     状态 / 聊天 / 审批 / 记忆 / 设定 / 部署
       │     Liquid Glass 视觉体系
       │
       └── QQ = NapCat (proot-Debian 里的 linuxqq + NapCat 注入)
             OneBot v11 WebSocket @ 127.0.0.1:8081
```

## 能力

- **自主生命体**：心跳驱动，自己决定下次醒来的时间和动作（陪伴/表达/好奇/成长等 drive）。
- **QQ 聊天**：私聊/群聊，LLM 语义判断该不该回，回复支持系统表情 + 本地表情包。
- **小黑盒**：签到/抽奖/领券/发帖/评论/浏览。
- **长期记忆**：统一存储事件、事实、暂定认识、未完事项和自身经历，按人物、范围、相关性与时间召回；支持纠正、固定、忘记及来源追溯，更新后的事实与历史证据区分使用。
- **人格成长**：从有来源的真实经历提炼特质，经独立隐私审查后形成统一画像；支持人工编辑、更新状态和版本冲突保护，相关依据失效时撤销自动条目。
- **隐私与控制**：群聊只读取本群及公开记忆，私聊按范围读取；常见秘密凭据在写入时脱敏，人工画像和行为限制优先于自动成长。凭据过滤和模型审查不能保证识别所有敏感表述。
- **监管 app**：查看状态、聊天记录、审批待办、记忆、设定、部署，浅色/深色主题。

## 安装与升级

运行环境为 Android 8.0+、Termux，以及支持工具调用的 OpenAI 兼容模型接口。QQ 使用 NapCat；小黑盒需要配置自己的凭据。接口令牌、数据库和平台登录信息保留在手机本地，不随安装包分发。

首次使用：安装 APK，在 app「部署」页完成 Termux 环境检查、鱼脑安装和 QQ 配置，再在「设定」页填写模型接口及个人设定。Termux 外部命令开关和 Android 授权按向导提示操作。

已有安装升级：

1. 覆盖安装最新版 APK，保留应用数据。
2. 在「部署」页执行鱼脑更新。**仅更新 APK 不会替换已经运行的 Python 鱼脑**；无需重装 QQ 或清除记忆。
3. 等待更新完成，再检查「状态」、QQ 连接及「记忆」页面。

本版新增记忆策略迁移：迁移前会在手机备份 SQLite 数据库，随后脱敏旧内容中的常见凭据、隔离含凭据的长期认识，保留其他记忆及原有隔离记录。备份仍含原始私人数据，应留在本机妥善保管；恢复方法见[记忆系统文档](docs/memory-system.md)。

语义理解、同义查询和长期人格质量仍需真实交流验证；目前不包含向量检索或公开基准成绩。

## 构建

```bash
# 在仓库根目录校验 Python 与安装资源
python tools/sync_fish_assets.py
python tools/sync_fish_assets.py --check
python -m unittest discover -s fish/tests -v

# 监管 app（Flutter）
cd app
flutter pub get
flutter analyze --no-pub
flutter test --no-pub
flutter build apk --release --no-pub
```

APK 输出至 `app/build/app/outputs/flutter-apk/app-release.apk`。Python 运行模块及部署向导清单由同步脚本统一生成，修改 `fish/*.py` 后必须重新同步再构建。

鱼脑使用 Python 3.11+，跑在手机 Termux 里，详见 [`CLAUDE.md`](CLAUDE.md) 的部署说明。发布构建沿用配置的签名；自建版本若更换签名，将无法直接覆盖现有安装，请勿随意更换签名身份。

## 安装包

最新 APK 与 SHA-256 校验文件见 [Releases](https://github.com/99cz99/fishbrain/releases/latest)。当前版本为 `1.0.40+54`，更新内容见[发布说明](docs/releases/v1.0.40.md)。

## 文档

- [记忆系统、隐私范围、成长与迁移](docs/memory-system.md)
- [v1.0.40 发布说明](docs/releases/v1.0.40.md)
- [架构、开发约定与部署说明](CLAUDE.md)
