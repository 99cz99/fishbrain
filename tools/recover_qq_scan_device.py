"""Compatibility entry point: request QR recovery through QQController only.

Does not stop Fish/QQ, copy a partial Login directory, change device identity or
spawn proot directly. Use qq_control_cold_device.py for a gated cold experiment.
"""
import json
from pathlib import Path
import urllib.request

root = Path('/data/data/com.termux/files/home/dfy')
token = (root/'.api-token').read_text().strip()
headers = {'Authorization':'Bearer '+token,'Content-Type':'application/json'}
request = urllib.request.Request('http://127.0.0.1:8765/qq/status',headers=headers)
with urllib.request.urlopen(request,timeout=8) as response:
    status = json.load(response)
if status.get('control_version') != 2:
    raise RuntimeError('Upgrade the paired QQ controller before recovery')
if status.get('account_ready') or status.get('online'):
    raise RuntimeError('QQ is already authenticated; repair transport without logging in again')
request = urllib.request.Request('http://127.0.0.1:8765/qq/refresh_qr',headers=headers,data=b'{}')
with urllib.request.urlopen(request,timeout=8) as response:
    result = json.load(response)
print(json.dumps({k:result.get(k) for k in ('ok','queued','operation','code','error')},ensure_ascii=True))
if not result.get('ok'):
    raise SystemExit(1)
