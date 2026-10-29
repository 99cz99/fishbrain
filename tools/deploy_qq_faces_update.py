"""Targeted Termux update: replace only the three QQ face codec modules."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import tomllib

import deploy_memory_update as deploy

TARGETS = ('qq_bot.py', 'qq_delivery.py', 'qq_faces.py')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(stage):
    root = deploy.ROOT
    manifest = json.loads((stage/'face-manifest.json').read_text())
    if set(manifest['after']) != set(TARGETS) or set(manifest['before']) != set(TARGETS[:2]):
        raise RuntimeError('Expected exactly the face codec files')
    for name in TARGETS:
        if digest(stage/name) != manifest['after'][name]:
            raise RuntimeError('Staged hash mismatch: '+name)
        compile((stage/name).read_text(), name, 'exec')
    for name, expected in manifest['before'].items():
        if digest(root/name) != expected:
            raise RuntimeError('Running source differs from tested baseline: '+name)
    if (root/'qq_faces.py').exists():
        raise RuntimeError('New module already exists; inspect before updating')
    untouched = {p.name: digest(p) for p in root.glob('*.py') if p.name not in TARGETS}
    config = tomllib.loads((root/'config.toml').read_text())
    db = (root/config.get('server', {}).get('db_path', 'fish.db')).resolve()
    if db.parent != root.resolve() or not db.is_file():
        raise RuntimeError('Expected existing database inside the installation')
    backup = root/'.backups'/('face-update-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    deploy.stop()
    changed = []
    try:
        for name in TARGETS[:2]:
            shutil.copy2(root/name, backup/name)
        with sqlite3.connect(db) as source, sqlite3.connect(backup/db.name) as destination:
            source.backup(destination)
        # Deliberately do not call deploy.run(): all other modules stay in place.
        for name in TARGETS:
            tmp = root/(name+'.incoming')
            shutil.copyfile(stage/name, tmp)
            tmp.replace(root/name)
            changed.append(name)
        if any(digest(root/name) != value for name, value in untouched.items()):
            raise RuntimeError('Unrelated source changed')
        sys.path.insert(0, str(root))
        from qq_bot import QQBot, cq_face
        from qq_delivery import unit_text
        bot = QQBot.__new__(QQBot)
        for token, fid in [('白眼', 22), ('22', 22), ('4', 4), ('23', 23), ('24', 24), ('29', 29), ('0', 0)]:
            if bot._to_segments(f'[表情:{token}]') != [cq_face(fid)]:
                raise RuntimeError('Installed face parser verification failed')
            if bot._to_segments(unit_text([cq_face(fid)])) != [cq_face(fid)]:
                raise RuntimeError('Installed face history round trip failed')
        if unit_text([cq_face(22)]) != '[表情:白眼]':
            raise RuntimeError('History must prefer face names')
        deploy.start()
        health = deploy.health(config)
    except BaseException:
        deploy.stop()
        for name in changed:
            if (backup/name).exists():
                shutil.copy2(backup/name, root/name)
            else:
                (root/name).unlink(missing_ok=True)
        deploy.start()
        raise
    print(json.dumps({'backup': str(backup), 'updated_modules': list(TARGETS),
                      'unrelated_modules_preserved': True, 'face_codec_verified': True,
                      'health': health, 'hashes': {n: digest(root/n) for n in TARGETS}}))
    deploy.verify(backup)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    install(parser.parse_args().stage)
