"""Observe QR freshness and connection state; does not request login or restart."""
import json
from pathlib import Path
import time
import urllib.request

root = Path('/data/data/com.termux/files/home/dfy')
token = (root/'.api-token').read_text().strip()
for attempt in range(15):
    request = urllib.request.Request('http://127.0.0.1:8765/qq/status', headers={'Authorization': 'Bearer '+token})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.load(response)
    except OSError:
        print('fish_starting', flush=True)
        time.sleep(3)
        continue
    print(json.dumps({key: data.get(key) for key in ('running', 'online', 'qr', 'qr_age', 'self_healing')}) , flush=True)
    if data.get('qr') or data.get('online'):
        break
    time.sleep(3)
