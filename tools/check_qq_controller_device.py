"""Read-only deployment preflight. Never prints native identity or credentials."""
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
from qq_control import ProcessManager, WebUIClient

ROOT = Path('/data/data/com.termux/files/home/dfy')
CONTAINER = Path('/data/data/com.termux/files/usr/var/lib/proot-distro/containers/debian/rootfs')
scan = ProcessManager().scan()
errors = []
for path in Path('/proc').iterdir():
    if not path.name.isdigit():
        continue
    owned = False
    try:
        owned = path.stat().st_uid == os.getuid()
        if owned:
            (path/'cmdline').read_bytes()
            (path/'stat').read_text()
    except OSError as exc:
        errors.append({'pid': path.name, 'owned': owned, 'error': type(exc).__name__})
token = (ROOT/'.api-token').read_text().strip()
req = urllib.request.Request('http://127.0.0.1:8765/qq/status', headers={'Authorization': 'Bearer '+token})
with urllib.request.urlopen(req, timeout=5) as response:
    status = json.load(response)
profile = CONTAINER/'root/.config/QQ'
files = list(profile.rglob('*'))
size = sum(p.stat().st_size for p in files if p.is_file())
identity = {}
for name in ('etc/machine-id', 'var/lib/dbus/machine-id', 'root/.config/QQ/global/nt_data/msf/machine-info'):
    path = CONTAINER/name
    identity[name] = {'exists': path.exists(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
config = CONTAINER/'opt/QQ/resources/app/app_launcher/napcat/config'
client = WebUIClient(lambda: json.loads((config/'webui.json').read_text()).get('token') or 'napcat')
native = client.call('/api/QQLogin/CheckLoginStatus', {})
info = client.call('/api/QQLogin/GetQQLoginInfo', {})
home_files = [p for p in (CONTAINER/'root').rglob('*') if p.is_file()]
native_summary = {k:(native.get('data') or {}).get(k) for k in ('isLogin','coreReady','isOffline','loginPhase')}
print(json.dumps({'process': scan, 'status': {k:status.get(k) for k in ('running','online','qr','control_version','account_ready','operation')},
                  'process_read_errors': {'owned': sum(x['owned'] for x in errors), 'other': sum(not x['owned'] for x in errors)},
                  'profile_exists': profile.is_dir(), 'profile_bytes': size, 'profile_entries': len(files),
                  'home_bytes': sum(p.stat().st_size for p in home_files),
                  'native': native_summary, 'info_ok': info['ok'], 'info_keys': list((info.get('data') or {}).keys()),
                  'profile_symlinks': sum(p.is_symlink() for p in files),
                  'free_bytes': os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize,
                  'identity': identity,
                  'source_hashes': {p.name: hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
                                    for p in ROOT.glob('*.py')}}))
