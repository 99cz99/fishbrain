"""Read-only verification of a paired heartbeat update; keeps private data on device."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tomllib

ROOT = Path('/data/data/com.termux/files/home/dfy')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--hashes', type=Path, required=True)
parser.add_argument('--backup', type=Path, required=True)
args = parser.parse_args()
if not args.backup.resolve().is_relative_to((ROOT/'.backups').resolve()):
    raise RuntimeError('Expected installation backup path')
expected = json.loads(args.hashes.read_text())
actual = {p.name: hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
          for p in ROOT.glob('*.py')}
if any(actual.get(name) != value for name, value in expected.items()):
    raise RuntimeError('Device runtime differs from packaged sources')
for name in expected:
    compile((ROOT/name).read_text(), name, 'exec')
cfg = tomllib.loads((ROOT/'config.toml').read_text())
db = (ROOT/cfg.get('server', {}).get('db_path', 'fish.db')).resolve()
with closing(sqlite3.connect(db.as_uri()+'?mode=ro', uri=True)) as current, \
     closing(sqlite3.connect((args.backup/db.name).as_uri()+'?mode=ro', uri=True)) as before:
    identical = list(current.iterdump()) == list(before.iterdump())
    if not identical or current.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise RuntimeError('Database changed or integrity check failed')
    print(json.dumps({'runtime_modules_match_package': len(expected), 'compile': 'ok',
                      'sqlite': 'ok', 'database_identical_to_backup': identical,
                      'memory_rows': current.execute('SELECT count(*) FROM memories').fetchone()[0]}))
