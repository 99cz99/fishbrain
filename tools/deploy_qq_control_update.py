"""Atomic paired QQ controller update under Termux UID; keep the current QQ process."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import tomllib
import urllib.request

import deploy_memory_update as deploy

TARGETS = ('server.py', 'qq_bot.py', 'qq_control.py')


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def request(path):
    token = (deploy.ROOT/'.api-token').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:8765'+path, headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def install(stage):
    root = deploy.ROOT
    manifest = json.loads((stage/'qq-control-manifest.json').read_text())
    if set(manifest['after']) != set(TARGETS) or set(manifest['before']) != set(TARGETS):
        raise RuntimeError('Expected the three coupled QQ modules')
    for name in TARGETS:
        if digest(stage/name) != manifest['after'][name]:
            raise RuntimeError('Staged source differs: '+name)
        expected = manifest['before'][name]
        if (expected is None and (root/name).exists()) or (expected is not None and digest(root/name) != expected):
            raise RuntimeError('Device source differs from reviewed baseline: '+name)
        compile((stage/name).read_text(), name, 'exec')
    from qq_control import ProcessManager
    process = ProcessManager()
    before_process = process.scan()
    if not before_process['known'] or not any(r['role'] == 'main' for r in before_process['rows']):
        raise RuntimeError('Expected an identifiable existing QQ process')
    before_status = request('/qq/status')
    if not before_status.get('online'):
        raise RuntimeError('Expected an online session before the non-disruptive update')
    config = tomllib.loads((root/'config.toml').read_text())
    db = (root/config.get('server',{}).get('db_path','fish.db')).resolve()
    if db.parent != root.resolve() or not db.is_file():
        raise RuntimeError('Database must be inside the installation')
    untouched = {p.name:digest(p) for p in root.glob('*.py') if p.name not in TARGETS}
    preserved = {name:digest(root/name) for name in ('config.toml','.api-token')}
    backup = root/'.backups'/('qq-control-update-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    deploy.stop()  # Fish only: no QQ process signal or native login action.
    changed = []
    try:
        for name in TARGETS:
            if (root/name).exists():
                shutil.copy2(root/name, backup/name)
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(backup/db.name)) as dest:
            source.backup(dest)
            database_before = hashlib.sha256('\n'.join(source.iterdump()).encode()).hexdigest()
            if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Database check failed')
        for name in TARGETS:
            tmp = root/(name+'.incoming')
            shutil.copyfile(stage/name,tmp)
            tmp.replace(root/name)
            changed.append(name)
        # Validate imports and state without invoking Fish() or a background controller.
        sys.path.insert(0,str(root))
        from server import Fish
        from memory import Store
        from qq_control import QQController
        from unittest.mock import Mock
        store = Store(':memory:')
        try:
            controller = QQController(store,Mock())
            result = controller.request('stop')
            if not result.get('queued') or controller.desired != 'stopped':
                raise RuntimeError('Controller smoke check failed')
        finally:
            store.close()
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as source:
            if hashlib.sha256('\n'.join(source.iterdump()).encode()).hexdigest() != database_before:
                raise RuntimeError('Database changed during offline validation')
        if any(digest(root/name) != expected for name,expected in {**untouched,**preserved}.items()):
            raise RuntimeError('Unrelated source or configuration changed')
        deploy.start()
        health = deploy.health(config)
        for _ in range(30):
            status = request('/qq/status')
            if status.get('control_version') == 2 and status.get('online'):
                break
            time.sleep(2)
        else:
            raise RuntimeError('New controller did not confirm the existing online session')
        after_process = process.scan()
        if after_process != before_process:
            # Aux workers may churn; main and launcher identities must stay the same.
            important = lambda scan: [r for r in scan['rows'] if r['role'] in ('main','launcher')]
            if not after_process['known'] or important(after_process) != important(before_process):
                raise RuntimeError('Existing QQ session identity changed during Fish update')
    except BaseException:
        deploy.stop()
        for name in changed:
            if (backup/name).exists():
                shutil.copy2(backup/name,root/name)
            else:
                (root/name).unlink(missing_ok=True)
        deploy.start()
        raise
    print(json.dumps({'backup':str(backup),'updated_modules':list(TARGETS),
                      'qq_session_preserved':True,'configuration_preserved':True,
                      'database_unchanged_before_restart':True,'health':health,
                      'account_ready':status.get('account_ready'),'online':status.get('online'),
                      'hashes':{name:digest(root/name) for name in TARGETS}}),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    install(parser.parse_args().stage)
