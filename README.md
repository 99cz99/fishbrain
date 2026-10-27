# Fishbrain（dfy）

一个 24h 在线、像真人一样的 AI 朋友（赛博生命体），住在安卓手机里，走协议/API 玩 QQ、小黑盒、B站。

## 架构

两个组件，都跑在**用户的安卓手机**上，`127.0.0.1` 本地回环：

```
手机 ──┬── 鱼脑 (Python · Termux)        核心 = 生命体
       │     server.py   HTTP API (127.0.0.1:8765)
       │     brain.py    调 LLM
       │     memory.py   SQLite 记忆
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
- **长期记忆**：统一存储，按相关性召回，反思合并，隐私按 scope 过滤。
- **监管 app**：查看状态、聊天记录、审批待办、记忆、设定、部署，浅色/深色主题。

## 构建

```bash
# 监管 app（Flutter）
cd app
flutter analyze
flutter build apk --release
```

鱼脑是纯 Python，跑在手机 Termux 里，详见 [`CLAUDE.md`](CLAUDE.md) 的部署说明。

## 安装包

最新 APK 见 [Releases](https://github.com/99cz99/fishbrain/releases)。

## 文档

完整架构、开发约定与踩坑记录见 [`CLAUDE.md`](CLAUDE.md)。
