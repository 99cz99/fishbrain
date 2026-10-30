"""Install the three coupled heartbeat modules, preserving the service's running state."""
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
from unittest.mock import Mock, patch

import deploy_memory_update as deploy

TARGETS = ('brain.py', 'life.py', 'autonomy.py')


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def running():
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args = (proc/'cmdline').read_bytes().split(b'\0')
            if any(arg in (b'server.py', str(deploy.ROOT/'server.py').encode(),
                           str(deploy.ROOT/'run-fish.sh').encode()) for arg in args):
                return True
        except OSError:
            pass
    return False


def smoke():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(deploy.ROOT))
    from brain import Brain, LLMTransportError
    from life import Life
    from memory import Store
    from autonomy import Autonomy
    with patch('brain.urllib.request.urlopen', side_effect=TimeoutError('timed out')):
        try:
            Brain()._post_raw('https://example.test', {}, {})
        except LLMTransportError as exc:
            assert exc.retryable
        else:
            raise RuntimeError('Transport classification failed')
    store = Store(':memory:')
    try:
        life = Life(store, Mock())
        store.set_life_state({'valence': 0.1, 'next_wake_at': time.time()-10})
        snapshot = life._schedule_snapshot()
        Autonomy(store, Mock())._nudge_mood({'desirability': 1, 'intensity': 1}, time.time())
        result = life._failure(LLMTransportError('test timeout'), '心跳', snapshot)
        assert result['next_wake'] == 10 and not result.get('cancelled')
        assert life.next_wake_at() > time.time()+590
        life.brain.chat_with_tools.return_value = {'tool_calls': []}
        with patch.object(life, '_action_prompt', return_value='test'):
            assert life.decide_action('好奇', []) == {'tool_calls': []}
        snapshot = life._schedule_snapshot()
        life.wake()
        assert life._failure(LLMTransportError('timeout'), '心跳', snapshot)['cancelled']
    finally:
        store.close()


def install(stage):
    root = deploy.ROOT
    manifest = json.loads((stage/'heartbeat-manifest.json').read_text())
    if any(set(manifest[key]) != set(TARGETS) for key in ('before', 'after')):
        raise RuntimeError('Expected exactly the three heartbeat modules')
    for name in TARGETS:
        if digest(stage/name) != manifest['after'][name]:
            raise RuntimeError('Staged hash mismatch: '+name)
        if digest(root/name) != manifest['before'][name]:
            raise RuntimeError('Device source differs from reviewed baseline: '+name)
        compile((stage/name).read_text(), name, 'exec')
    untouched = {p.name: digest(p) for p in root.glob('*.py') if p.name not in TARGETS}
    config = tomllib.loads((root/'config.toml').read_text())
    db = (root/config.get('server', {}).get('db_path', 'fish.db')).resolve()
    if db.parent != root.resolve() or not db.is_file():
        raise RuntimeError('Expected existing database inside the installation')
    preserved = {name: digest(root/name) for name in ('config.toml', '.api-token')}
    was_running = running()
    backup = root/'.backups'/('heartbeat-update-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    if was_running:
        deploy.stop()
    changed = []
    try:
        for name in TARGETS:
            shutil.copy2(root/name, backup/name)
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro', uri=True)) as source, closing(sqlite3.connect(backup/db.name)) as dest:
            source.backup(dest)
            database_before = hashlib.sha256('\n'.join(source.iterdump()).encode()).hexdigest()
            if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Database integrity check failed')
        for name in TARGETS:
            tmp = root/(name+'.incoming')
            shutil.copyfile(stage/name, tmp)
            tmp.replace(root/name)
            changed.append(name)
        smoke()
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro', uri=True)) as current:
            if hashlib.sha256('\n'.join(current.iterdump()).encode()).hexdigest() != database_before:
                raise RuntimeError('Database contents changed during offline validation')
        if any(digest(root/name) != value for name, value in {**untouched, **preserved}.items()):
            raise RuntimeError('Unrelated source or configuration changed')
        health = None
        if was_running:
            deploy.start()
            health = deploy.health(config)
    except BaseException:
        if was_running:
            deploy.stop()
        for name in changed:
            shutil.copy2(backup/name, root/name)
        if was_running:
            deploy.start()
        raise
    print(json.dumps({'backup': str(backup), 'updated_modules': list(TARGETS),
                      'configuration_preserved': True, 'unrelated_modules_preserved': True,
                      'database_preserved_before_restart': True,
                      'device_smoke_passed': True, 'was_running': was_running,
                      'running_after': running(), 'health': health,
                      'hashes': {name: digest(root/name) for name in TARGETS}}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    install(parser.parse_args().stage)
