"""Inspect login chain evidence without restarting QQ or exposing credential contents."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

sys.dont_write_bytecode = True
ROOT = Path('/data/data/com.termux/files/home/dfy')
sys.path.insert(0, str(ROOT))
from server import Fish, NAPCAT_CONFIG_DIR, QQ_LOG_PATH


def emit(label, data):
    print(label, json.dumps(data, ensure_ascii=False), flush=True)


def alias(value):
    return re.sub(r'\d{5,}', '<account>', str(value))


profile = Path(QQ_LOG_PATH).parents[2]/'root/.config/QQ'
emit('device_time', time.strftime('%Y-%m-%d %H:%M:%S %z'))
try:
    token = (ROOT/'.api-token').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:8765/qq/status', headers={'Authorization': 'Bearer '+token})
    with urllib.request.urlopen(req, timeout=5) as response:
        data = json.load(response)
    emit('fish_qq_status', {key: data.get(key) for key in ('running', 'online', 'qr', 'stale', 'self_healing')})
except Exception as exc:
    emit('fish_status_error', type(exc).__name__)
bundle = Path(NAPCAT_CONFIG_DIR).parent/'napcat.mjs'
emit('bundle', {'exists': bundle.exists(), 'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest() if bundle.exists() else None})
qqpackage = Path(NAPCAT_CONFIG_DIR).parents[2]/'package.json'
if qqpackage.exists():
    info = json.loads(qqpackage.read_text())
    emit('qq_version', {k: info.get(k) for k in ('version', 'platform')})

files = []
inodes = {}
seen = 0
for parent, directories, names in os.walk(profile):
    directories[:] = [name for name in directories if name not in
        ('Cache', 'CacheStorage', 'Code Cache', 'GPUCache', 'Pic', 'Video', 'Emoji', 'nt_db', 'RichMedia')]
    for name in names:
        seen += 1
        if seen > 20000:
            break
        p = Path(parent)/name
        relative = str(p.relative_to(profile))
        relevant = (name in ('device.json', 'machine-info', 'machineid-info', 'machine-guid', 'deviceInfo.json')
                    or 'keystore' in name.lower() or '/Login/' in '/'+relative.replace('\\', '/'))
        if not relevant:
            continue
        try:
            stat = p.stat()
            key = alias(relative)
            files.append({'path': key, 'size': stat.st_size, 'mode': oct(stat.st_mode & 0o777),
                          'modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime)),
                          'symlink': p.is_symlink()})
            inodes[stat.st_ino] = key
        except OSError:
            pass
emit('profile_file_metadata', files[:100])

descriptors = []
qq_processes = 0
descriptor_errors = 0
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if proc.stat().st_uid != os.getuid():
            continue
        args = (proc/'cmdline').read_bytes().split(b'\0')
        if Path(args[0].decode(errors='replace')).name != 'qq':
            continue
        qq_processes += 1
        for fd in (proc/'fd').iterdir():
            try:
                target = os.readlink(fd)
                if '/Login/' not in target and 'keystore' not in target.lower():
                    continue
                details = (proc/'fdinfo'/fd.name).read_text()
                inode = re.search(r'^ino:\s*(\d+)', details, re.M)
                descriptors.append({'fd': int(fd.name), 'path': alias(target.split('/.config/QQ/')[-1]),
                                    'lock_record': any(line.startswith('lock:') for line in details.splitlines()),
                                    'inode_matches_inventory': bool(inode and int(inode.group(1)) in inodes)})
            except OSError:
                descriptor_errors += 1
    except OSError:
        descriptor_errors += 1
emit('open_login_descriptors', descriptors)
emit('descriptor_scan', {'qq_processes': qq_processes, 'read_errors': descriptor_errors})
try:
    locks = Path('/proc/locks').read_text().splitlines()
    emit('inventory_locks', [{'path': key, 'has_advisory_lock': any(':'+str(inode)+' ' in line for line in locks)}
                            for inode, key in inodes.items()])
except OSError as exc:
    emit('lock_inventory_error', type(exc).__name__)

probe = Fish.__new__(Fish)
probe._webui_cred_val = None
probe._webui_cred_ts = 0
if '--metadata-only' in sys.argv:
    sys.exit(0)
status = probe._webui_check_login()
emit('webui_status', {k: status.get(k) for k in ('isLogin', 'isOffline', 'loginPhase', 'coreReady', 'qrLoginAccepted')} if status else None)
if status is None:
    emit('remaining_control_probes', 'skipped: status/auth unavailable; avoid repeated authentication')
    sys.exit(0)
users = probe._webui_call('/api/QQLogin/GetQuickLoginList', {})
emit('quick_login_list', {'request_code': (users or {}).get('code'), 'count': len((users or {}).get('data') or [])})
new_users = probe._webui_call('/api/QQLogin/GetQuickLoginListNew', {})
rows = (new_users or {}).get('data') or []
emit('new_login_list', {'request_code': (new_users or {}).get('code'), 'count': len(rows),
                        'row_keys': sorted(rows[0].keys()) if rows and isinstance(rows[0], dict) else []})
auto = probe._webui_call('/api/QQLogin/GetQuickLoginQQ', {})
emit('webui_auto_account', {'request_code': (auto or {}).get('code'), 'configured': bool((auto or {}).get('data'))})
platform = probe._webui_call('/api/QQLogin/GetPlatformInfo', {})
emit('platform', (platform or {}).get('data'))
guid = probe._webui_call('/api/QQLogin/GetDeviceGUID', {})
emit('get_device_guid', {'request_code': (guid or {}).get('code'), 'data_type': type((guid or {}).get('data')).__name__,
                          'available': bool((guid or {}).get('data'))})
