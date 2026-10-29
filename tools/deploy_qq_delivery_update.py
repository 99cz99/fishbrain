"""Run under Termux UID: backed-up runtime update and preserving OneBot migration."""
from pathlib import Path
import argparse
import json
import os
import urllib.request

import deploy_memory_update as deploy

ROOT = deploy.ROOT
CONFIG = Path('/data/data/com.termux/files/usr/var/lib/proot-distro/containers/debian/rootfs/opt/QQ/resources/app/app_launcher/napcat/config')


def request(path, method='GET'):
    token = (ROOT/'.api-token').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:8765'+path, method=method,
                                 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
                                 data=b'{}' if method == 'POST' else None)
    with urllib.request.urlopen(req,timeout=15) as response:
        return json.load(response)


def install(stage):
    status = request('/qq/status')
    account = status.get('self_id')
    if not account or not str(account).isdigit() or not status.get('online'):
        raise RuntimeError('Expected existing online QQ session before migration')
    config_path = CONFIG/f'onebot11_{account}.json'
    original = config_path.read_bytes()
    before = json.loads(original)
    deploy.STAGE = stage
    backup = deploy.run()
    config_backup = backup/'onebot-active.json'
    config_backup.write_bytes(original)
    os.chmod(config_backup,0o600)
    result = request('/qq/apply_onebot','POST')
    if not result.get('ok'):
        raise RuntimeError('Runtime updated; OneBot migration failed, original config retained')
    from qq_config import delivery_config
    expected = delivery_config(before,json.loads((ROOT/'onebot11.json').read_text()))
    actual = json.loads(config_path.read_text())
    if actual != expected:
        # Don't overwrite concurrently edited settings. Leave the backup for explicit recovery.
        raise RuntimeError('OneBot post-update validation failed; inspect saved backup')
    print(json.dumps({'backup':str(backup),'onebot_migrated':True,
                      'existing_configuration_preserved':True,'qq_restarting':bool(result.get('restarting'))}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    args = parser.parse_args()
    install(args.stage)
