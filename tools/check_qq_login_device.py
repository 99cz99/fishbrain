"""Read-only QQ login diagnostics; omit account IDs, tokens and login URLs."""
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import time
import urllib.request
import urllib.error
import hashlib

root = Path('/data/data/com.termux/files/home/dfy')
sys.path.insert(0, str(root))
from server import Fish, QQ_LOG_PATH, _qq_process_alive


def emit(label, data):
    print(label, json.dumps(data, ensure_ascii=False), flush=True)


def clean(text):
    text = re.sub(r'https?://\S+', '[url]', str(text))
    text = re.sub(r'\b\d{5,}\b', '[id]', text)
    text = re.sub(r'[A-Za-z0-9_\-]{40,}', '[redacted]', text)
    return text[:350]


token = (root/'.api-token').read_text().strip()
req = urllib.request.Request('http://127.0.0.1:8765/qq/status', headers={'Authorization': 'Bearer '+token})
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.load(r)
    emit('qq_status', {k: data.get(k) for k in ('running', 'online', 'qr', 'qr_age', 'stale',
                                              'login_reason', 'quick_login_configured', 'self_healing', 'transport')})
emit('process_alive_heuristic', _qq_process_alive())
processes = []
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if proc.stat().st_uid != os.getuid():
            continue
        args = (proc/'cmdline').read_bytes().split(b'\0')
        base = Path(args[0].decode(errors='replace')).name
        if base in ('qq', 'QQ', 'proot', 'proot-distro', 'Xvfb', 'xvfb-run', 'bash'):
            stat = (proc/'status').read_text()
            state = re.search(r'^State:\s*(.*)', stat, re.M).group(1)
            processes.append({'pid': int(proc.name), 'binary': base, 'state': state,
                              'ppid': re.search(r'^PPid:\s*(\d+)', stat, re.M).group(1),
                              'pgid': (proc/'stat').read_text().rsplit(')', 1)[1].split()[2],
                              'qq_command_substring': b'qq --no-sandbox' in b' '.join(args)})
    except OSError:
        pass
emit('processes', processes)
for port in (6099, 8081):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            emit('port_'+str(port), 'open')
    except OSError:
        emit('port_'+str(port), 'closed')
with sqlite3.connect((root/'fish.db').as_uri()+'?mode=ro', uri=True) as conn:
    row = conn.execute("SELECT value FROM settings WHERE key='qq_quick_login_uin'").fetchone()
    uin = str(json.loads(row[0])) if row else ''
    login_root = Path(QQ_LOG_PATH).parents[2]/'root/.config/QQ/global/nt_data/Login'
    emit('login_cache_files', [{'name': clean(str(p.relative_to(login_root))), 'size': p.stat().st_size}
          for p in login_root.rglob('*') if p.is_file()][:35] if login_root.exists() else [])
    emit('recent_login_events', [[ts, level, clean(message)] for ts, level, message in conn.execute(
        "SELECT ts,level,message FROM logs WHERE message LIKE '%快捷登录%' OR message LIKE '%快速登录%' "
        "OR message LIKE '%QQ 停止失败%' OR message LIKE '%QQ 启动失败%' ORDER BY id DESC LIMIT 15")])
probe = Fish.__new__(Fish)
probe._webui_cred_val = None
probe._webui_cred_ts = 0
started = time.monotonic()
try:
    body = json.dumps({'hash': hashlib.sha256((probe._webui_token()+'.napcat').encode()).hexdigest()}).encode()
    req = urllib.request.Request('http://127.0.0.1:6099/api/auth/login', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=8) as r:
        response = json.load(r)
        emit('webui_auth', {'http': r.status, 'code': response.get('code'),
                            'seconds': round(time.monotonic()-started, 2),
                            'data_keys': list((response.get('data') or {}).keys())})
except Exception as exc:
    emit('webui_auth', {'error': type(exc).__name__, 'seconds': round(time.monotonic()-started, 2)})
try:
    state = probe._webui_check_login()
    emit('webui_login', {k: state.get(k) for k in ('isLogin', 'isOffline', 'loginPhase')} if state else None)
    users = probe._webui_quick_login_list()
    emit('quick_login_cache', {'known': users is not None, 'count': len(users) if users is not None else None,
                              'configured_account_present': uin in users if users is not None else None})
except Exception as exc:
    emit('webui_probe_error', type(exc).__name__)
for p in (root/'qq_start_err.log', Path(QQ_LOG_PATH), Path(QQ_LOG_PATH).with_name('xvfb.log')):
    if not p.exists():
        emit(p.name, {'exists': False})
        continue
    with p.open('rb') as f:
        f.seek(max(0, p.stat().st_size-15000))
        lines = f.read().decode('utf-8', 'replace').splitlines()
    emit(p.name, {'modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.stat().st_mtime)),
                  'tail': [clean(line) for line in lines if any(x in line.lower() for x in
                    ('error', 'failed', 'login', '登录', 'quick', 'proot', 'permission', 'not found'))][-18:]})
