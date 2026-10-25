#!/bin/bash
# setup-fish.sh — 在 Termux 里一键装鱼脑运行环境（Python + websocket-client）
# 幂等：已装的部分自动跳过。关键步骤 echo "=== STEP: xxx ===" / "=== ERROR: xxx ===" 供 app 展示。
# 本脚本由 app 的部署向导通过 RUN_COMMAND 在 Termux 里跑（PATH 已含 /data/data/com.termux/files/usr/bin）。
# 注意：本脚本只装「环境」，不起 server.py——起服务由 app 单独发指令（把装环境和起服务解耦）。

set -e

TERMUX_BIN=/data/data/com.termux/files/usr/bin
PY="$TERMUX_BIN/python"

step() { echo "=== STEP: $1 ==="; }
done_() { echo "=== DONE: $1 ==="; }
fail() { echo "=== ERROR: $1 ==="; }

# 1. 检查 pkg 可用（Play 商店版 Termux 的 pkg 是坏的，这里能提前发现并给出明确指引）
step "检查包管理器 pkg"
if ! command -v pkg >/dev/null 2>&1; then
  fail "找不到 pkg：你可能装的是 Play 商店版 Termux（已停更）。请卸载后从 GitHub/F-Droid 装最新 Termux，再回来重试"
  exit 1
fi
done_ "pkg"

# 2. 更新包索引
step "更新包索引 pkg update"
if ! pkg update -y; then
  fail "pkg update 失败：检查网络，或 Termux 仓库没配好（首次可能需在 Termux 里手动跑一次 pkg update 同意源）"
  exit 1
fi
done_ "update"

# 3. 装 python
step "安装 Python"
if [ -x "$PY" ]; then
  echo "python 已存在，跳过"
else
  if ! pkg install -y python; then
    fail "pkg install python 失败：检查网络/仓库，或手动在 Termux 里跑 pkg install python"
    exit 1
  fi
fi
done_ "python"

# 4. 装 websocket-client
step "安装 websocket-client"
if "$PY" -m pip install websocket-client; then
  :
elif "$PY" -m pip install --break-system-packages websocket-client; then
  :
else
  fail "pip 安装 websocket-client 失败（网络或 pip 问题）"
  exit 1
fi
done_ "websocket"

# 5. 验证
step "验证 import websocket"
if ! "$PY" -c 'import websocket' 2>/dev/null; then
  fail "python 装好了但 import websocket 失败"
  exit 1
fi
done_ "verify"

echo "=== ALL DONE ==="
