"""Request a new QR once, without exposing credentials or retrying login."""
import json
from pathlib import Path
import urllib.request

root = Path('/data/data/com.termux/files/home/dfy')
token = (root/'.api-token').read_text().strip()
req = urllib.request.Request('http://127.0.0.1:8765/qq/refresh_qr', data=b'{}',
                             headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=15) as response:
    data = json.load(response)
    print(json.dumps({key: data.get(key) for key in ('ok', 'queued', 'operation', 'code', 'error')}, ensure_ascii=False))
