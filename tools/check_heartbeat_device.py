"""Read-only Termux heartbeat diagnostics; never print tokens or chat contents."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import tomllib
import urllib.request
import urllib.error

ROOT = Path('/data/data/com.termux/files/home/dfy')
config = tomllib.loads((ROOT/'config.toml').read_text())
token = (ROOT/'.api-token').read_text().strip()
port = config.get('server', {}).get('port', 8765)


def emit(label, value):
    print(label, json.dumps(value, ensure_ascii=False), flush=True)


def clean(value):
    value = str(value).replace(token, '[redacted]')
    value = re.sub(r'https?://\S+', '[url]', value)
    value = re.sub(r'(?<!\d)\d{5,}(?!\d)', '[id]', value)
    return value[:400]


def get(endpoint):
    request = urllib.request.Request(f'http://127.0.0.1:{port}{endpoint}',
                                     headers={'Authorization': 'Bearer '+token})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


emit('device_time', time.strftime('%Y-%m-%d %H:%M:%S %z'))
for endpoint, keys in (
        ('/status', ('state', 'uptime', 'memory_queue')),
        ('/qq/status', ('online', 'running', 'stale', 'transport')),
        ('/life/state', ('paused', 'next_wake_at', 'last_wake_at', 'platforms_online'))):
    try:
        data = get(endpoint)
        result = {key: data.get(key) for key in keys}
        if endpoint == '/life/state':
            for key in ('next_wake_at', 'last_wake_at'):
                result[key] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data.get(key) or 0))
        emit(endpoint, result)
    except Exception as exc:
        emit(endpoint, {'error_type': type(exc).__name__})

db = (ROOT/config.get('server', {}).get('db_path', 'fish.db')).resolve()
with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as conn:
    emit('database_check', conn.execute('PRAGMA quick_check').fetchone()[0])
    emit('error_summary_since_2026_10_29', conn.execute(
        "SELECT category,level,count(*),max(ts) FROM logs WHERE ts>='2026-10-29' "
        "AND level IN ('error','warning') GROUP BY category,level").fetchall())
    rows = conn.execute("SELECT ts,level,message FROM logs WHERE category='life' "
                        "ORDER BY id DESC LIMIT 18").fetchall()
    emit('recent_life', [[ts, level, clean(message)] for ts, level, message in rows])
    emit('heartbeat_failures', conn.execute(
        "SELECT ts FROM logs WHERE category='life' AND "
        "(message LIKE '%心跳异常%' OR message LIKE '%心跳暂缓%' OR message LIKE '%心跳失败%') "
        "ORDER BY id DESC LIMIT 30").fetchall())
    emit('recent_network_errors', [[ts, cat, clean(message)] for ts, cat, message in conn.execute(
        "SELECT ts,category,message FROM logs WHERE level IN ('error','warning') "
        "AND (message LIKE '%timed out%' OR message LIKE '%urlopen%' OR message LIKE '%HTTP %') "
        "ORDER BY id DESC LIMIT 15")])

emit('module_hashes', {name: hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                       for name in ('brain.py', 'life.py', 'autonomy.py')})
emit('recovery_patch_installed', 'LLMTransportError' in (ROOT/'brain.py').read_text())

processes = []
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if proc.stat().st_uid != os.getuid():
            continue
        args = (proc/'cmdline').read_bytes().split(b'\0')
        for label, signatures in (
                ('fish_server', (b'server.py', str(ROOT/'server.py').encode())),
                ('fish_supervisor', (b'run-fish.sh', str(ROOT/'run-fish.sh').encode())),
                ('qq', (b'/opt/QQ/qq', b'./qq'))):
            if any(arg in signatures for arg in args):
                processes.append({'pid': int(proc.name), 'kind': label})
    except (OSError, PermissionError):
        pass
emit('runtime_processes', processes)

with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as conn:
    row = conn.execute("SELECT value FROM settings WHERE key='llm'").fetchone()
    llm = json.loads(row[0]) if row else {}
    emit('last_status_events', [[ts, clean(message)] for ts, message in conn.execute(
        "SELECT ts,message FROM logs WHERE category='status' ORDER BY id DESC LIMIT 8")])
    emit('qq_error_groups', conn.execute(
        "SELECT CASE WHEN message LIKE 'QQ 停止失败:%' THEN 'stop_timeout' "
        "WHEN message LIKE 'QQ 连接断开%' THEN 'connection_lost' ELSE 'other' END,"
        "count(*),min(ts),max(ts) FROM logs WHERE category='error' AND ts>='2026-10-29' "
        "GROUP BY 1").fetchall())

# A read-only provider request checks connectivity/auth without model token usage.
if llm.get('provider', 'openai') == 'openai' and llm.get('base_url'):
    started = time.monotonic()
    request = urllib.request.Request(llm['base_url'].rstrip('/')+'/models',
                                     headers={'Authorization': 'Bearer '+llm.get('api_key', '')})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            response.read(256)
            emit('provider_connectivity', {'http': response.status,
                                          'seconds': round(time.monotonic()-started, 2)})
    except urllib.error.HTTPError as exc:
        emit('provider_connectivity', {'http': exc.code, 'seconds': round(time.monotonic()-started, 2)})
    except Exception as exc:
        emit('provider_connectivity', {'error_type': type(exc).__name__,
                                      'seconds': round(time.monotonic()-started, 2)})
