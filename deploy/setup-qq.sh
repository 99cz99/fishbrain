#!/bin/bash
# setup-qq.sh — 在 Termux 里一键装 QQ 环境（proot-Debian + linuxqq + NapCat）
# 幂等：已装的部分自动跳过。关键步骤 echo "=== STEP: xxx ===" 供鱼脑编排器解析进度。
# 本脚本由鱼脑 deploy 编排器在 Termux 里跑（PATH 已含 /data/data/com.termux/files/usr/bin）。

set -e

TERMUX_BIN=/data/data/com.termux/files/usr/bin
PREFIX=/data/data/com.termux/files/usr
DEBIAN_ROOTFS=$PREFIX/var/lib/proot-distro/containers/debian/rootfs
QQ_APP=$DEBIAN_ROOTFS/opt/QQ/resources/app
NAP_DIR=$QQ_APP/app_launcher/napcat
TMP="$PREFIX/tmp"
mkdir -p "$TMP"
mkdir -p "$DEBIAN_ROOTFS/root/napcat"

# ---- 下载源（版本会变，需定期更新；可用环境变量覆盖）----
QQ_DEB_URL="${QQ_DEB_URL:-https://qqdl.gtimg.cn/qqfile/QQNT/9.9.33/release/3f89efc5/QQ_3.2.32_260812_arm64_01.deb}"
NAPCAT_ZIP_URL="${NAPCAT_ZIP_URL:-https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip}"
NAPCAT_ZIP_GH="${NAPCAT_ZIP_GH:-https://gh-proxy.com/https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip}"
NAPCAT_ZIP_GH2="${NAPCAT_ZIP_GH2:-https://gh.llkk.cc/https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip}"

step() { echo "=== STEP: $1 ==="; }
done_() { echo "=== DONE: $1 ==="; }
fail() { echo "=== ERROR: $1 ==="; }

# 带进度下载：每 1% 输出 "=== PROGRESS: <pct> ===" 供鱼脑解析
download_with_progress() {
  local url="$1" dest="$2"
  python3 - "$url" "$dest" <<'PYEOF'
import sys, urllib.request
url, dest = sys.argv[1], sys.argv[2]
try:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        done = 0
        last_pct = -1
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total > 0:
                pct = int(done * 100 / total)
                if pct != last_pct:
                    print(f"=== PROGRESS: {pct} ===", flush=True)
                    last_pct = pct
except Exception as e:
    print(f"=== ERROR: 下载失败 {url}: {e} ===", flush=True)
    sys.exit(1)
PYEOF
}

# 1. proot + proot-distro
step "安装 proot/proot-distro"
if [ -x "$TERMUX_BIN/proot-distro" ]; then
  echo "proot-distro 已存在，跳过"
else
  pkg update -y
  # 5.1.107.99 有段错误回归（跑 linuxqq 崩），先 pin 到 5.1.107.100 热修复版；拿不到就退回最新
  pkg install -y proot=5.1.107.100 proot-distro || pkg install -y proot proot-distro
fi
done_ "proot"

# 2. Debian rootfs
step "安装 Debian 容器"
if [ -d "$DEBIAN_ROOTFS/etc" ]; then
  echo "Debian 已存在，跳过"
else
  proot-distro install debian
fi
done_ "debian"

# 3. Debian 依赖（xvfb 虚拟屏 + Electron 运行库）
step "装 Debian 依赖"
proot-distro login debian -- bash -c "DEBIAN_FRONTEND=noninteractive apt-get update -y && apt-get install -y xvfb libgbm1 libasound2 unzip"
done_ "apt-deps"

# 4. linuxqq
step "安装 linuxqq"
if [ -f "$DEBIAN_ROOTFS/opt/QQ/qq" ]; then
  echo "linuxqq 已安装，跳过"
else
  echo "下载 linuxqq: $QQ_DEB_URL"
  download_with_progress "$QQ_DEB_URL" "$TMP/linuxqq.deb" \
    || { fail "下载 linuxqq 失败"; exit 1; }
  cp "$TMP/linuxqq.deb" "$DEBIAN_ROOTFS/root/linuxqq.deb"
  proot-distro login debian -- bash -c "dpkg -i /root/linuxqq.deb; DEBIAN_FRONTEND=noninteractive apt-get install -f -y"
fi
done_ "linuxqq"

# 5. NapCat 注入
step "安装 NapCat"
if [ -f "$NAP_DIR/napcat.mjs" ]; then
  echo "NapCat 已注入，跳过"
else
  echo "下载 NapCat.Shell.zip"
  download_with_progress "$NAPCAT_ZIP_URL" "$TMP/NapCat.Shell.zip" \
    || download_with_progress "$NAPCAT_ZIP_GH" "$TMP/NapCat.Shell.zip" \
    || download_with_progress "$NAPCAT_ZIP_GH2" "$TMP/NapCat.Shell.zip" \
    || { fail "下载 NapCat 失败"; exit 1; }
  mkdir -p "$NAP_DIR"
  rm -rf "$TMP/napcat_extract"
  mkdir -p "$TMP/napcat_extract"
  (cd "$TMP" && unzip -q -o NapCat.Shell.zip -d napcat_extract)
  cp -a "$TMP/napcat_extract/." "$NAP_DIR/"
  cat > "$QQ_APP/loadNapCat.js" <<'EOF'
const path = require('node:path');
const { pathToFileURL } = require('node:url');
import(pathToFileURL(path.join(__dirname, 'app_launcher/napcat/napcat.mjs')).href);
EOF
  sed -i 's|"main": ".*"|"main": "./loadNapCat.js"|' "$QQ_APP/package.json"
fi
done_ "napcat"

# 6. OneBot WS 配置（登录后按 uin 写入 onebot11_<uin>.json，见 deploy/onebot11.json 模板）
step "OneBot 配置占位"
echo "onebot11_<uin>.json 需在扫码登录、拿到 QQ 号后写入（见向导后续步骤）"
done_ "onebot-placeholder"

echo "=== ALL DONE ==="
