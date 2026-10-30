"""Read-only startup diagnostics without printing tokens, settings or chat contents."""
import json
import os
from pathlib import Path
import re
import time
import urllib.request

root = Path('/data/data/com.termux/files/home/dfy')


def emit(label, value):
    print(label, json.dumps(value, ensure_ascii=False), flush=True)


emit('time', time.strftime('%Y-%m-%d %H:%M:%S'))
properties = Path('/data/data/com.termux/files/home/.termux/termux.properties')
lines = properties.read_text().splitlines() if properties.exists() else []
emit('allow_external_apps', [line.strip() for line in lines if re.match(r'^\s*allow-external-apps\s*=', line)])
pidfile = root/'.fish.pid'
if pidfile.exists():
    pid = pidfile.read_text().strip()
    args = (Path('/proc')/pid/'cmdline').read_bytes().split(b'\0') if pid.isdigit() and (Path('/proc')/pid/'cmdline').exists() else []
    emit('supervisor_pid_guard', {'pid': pid if pid.isdigit() else 'invalid',
                                  'exists': bool(args), 'process': [Path(a.decode(errors='replace')).name for a in args[:3]]})
processes = []
for p in Path('/proc').iterdir():
    if not p.name.isdigit():
        continue
    try:
        if p.stat().st_uid != os.getuid():
            continue
        args = (p/'cmdline').read_bytes().split(b'\0')
        if any(a in (b'server.py', str(root/'server.py').encode(), str(root/'run-fish.sh').encode()) for a in args):
            processes.append({'pid': int(p.name), 'args': [Path(a.decode(errors='replace')).name for a in args[:3]]})
    except OSError:
        pass
emit('runtime_processes', processes)
for name in ('server.log', 'run-fish.sh', '.fish.pid'):
    p = root/name
    if p.exists():
        emit(name, {'size': p.stat().st_size, 'modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.stat().st_mtime))})
log = root/'server.log'
if log.exists():
    with log.open('rb') as f:
        f.seek(max(0, log.stat().st_size-30000))
        tail = f.read().decode('utf-8', 'replace').splitlines()
    errors = []
    for line in tail:
        if re.match(r'^(Traceback|\w*(Error|Exception):|/.*run-fish.sh:|sh:|.*python:)', line):
            line = re.sub(r'https?://\S+', '[url]', line)
            line = re.sub(r'[A-Za-z0-9_\-]{40,}', '[redacted]', line)
            errors.append(line[:250])
    emit('startup_errors', errors[-15:])
token = (root/'.api-token').read_text().strip()
try:
    req = urllib.request.Request('http://127.0.0.1:8765/status', headers={'Authorization': 'Bearer '+token})
    with urllib.request.urlopen(req, timeout=3) as r:
        data = json.load(r)
        emit('api', {'http': r.status, 'uptime': data.get('uptime')})
except Exception as e:
    emit('api', {'error': type(e).__name__, 'errno': getattr(getattr(e, 'reason', e), 'errno', None)})
