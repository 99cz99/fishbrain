"""Run under Termux's UID after staging only runtime *.py in STAGE.

Stops Fishbrain, snapshots SQLite and sources on the device, installs and verifies,
and restores that snapshot if validation fails. Never prints settings or tokens.
"""
from pathlib import Path
import hashlib
import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.request

ROOT = Path('/data/data/com.termux/files/home/dfy')
STAGE = Path('/data/local/tmp/dfy-memory-20261029')
MODULES = ('autonomy.py','brain.py','delivery.py','heybox.py','life.py','memory.py',
           'memory_policy.py','memory_system.py','personality.py','qq_bot.py','qq_config.py',
           'qq_delivery.py','qq_faces.py','qq_media.py','server.py','weather.py','qq_control.py')
DEPLOY_FILES = ('run-fish.sh','setup-fish.sh','setup-qq.sh','onebot11.json')


def stop():
    for pattern in ('[r]un-fish.sh','[s]erver.py'):
        subprocess.run(['/system/bin/pkill','-f',pattern],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (ROOT/'.fish.pid').unlink(missing_ok=True)
    time.sleep(.3)
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args=(proc/'cmdline').read_bytes().split(b'\0')
            if any(arg in (b'server.py',str(ROOT/'server.py').encode(),str(ROOT/'run-fish.sh').encode()) for arg in args):
                raise RuntimeError('Fishbrain process did not stop')
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            pass


def start():
    with (ROOT/'server.log').open('ab') as log:
        subprocess.Popen(['/system/bin/sh',str(ROOT/'run-fish.sh')],cwd=ROOT,
                         stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)


def health(config):
    token=(ROOT/'.api-token').read_text().strip()
    port=config.get('server',{}).get('port',8765)
    for _ in range(20):
        try:
            req=urllib.request.Request(f'http://127.0.0.1:{port}/status',headers={'Authorization':'Bearer '+token})
            with urllib.request.urlopen(req,timeout=2) as response:
                data=json.load(response)
                status={'http':response.status,'state':data.get('state'),'memory_queue':data.get('memory_queue')}
            for endpoint in ('/life/state','/approval/recent'):
                req=urllib.request.Request(f'http://127.0.0.1:{port}{endpoint}',headers={'Authorization':'Bearer '+token})
                with urllib.request.urlopen(req,timeout=3) as response:
                    json.load(response)
                    status[endpoint]=response.status
            return status
        except Exception:
            time.sleep(1)
    raise RuntimeError('Authenticated health check failed')


def run():
    if not ROOT.is_dir() or not (ROOT/'run-fish.sh').is_file():
        raise RuntimeError('Expected existing Fishbrain installation')
    config=tomllib.loads((ROOT/'config.toml').read_text())
    db=Path(config.get('server',{}).get('db_path','fish.db'))
    db=(ROOT/db).resolve()
    if db.parent != ROOT.resolve() or not db.is_file():
        raise RuntimeError('Database must already exist inside the installation')
    for name in MODULES:
        if not (STAGE/name).is_file():
            raise RuntimeError('Staged module missing: '+name)
        # Compile into Termux-owned temporary files; staging is shell-owned.
        compile((STAGE/name).read_text(),name,'exec')
    for name in DEPLOY_FILES:
        if not (STAGE/name).is_file():
            raise RuntimeError('Staged deployment asset missing: '+name)
    backup=ROOT/'.backups'/('link-update-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=False)
    os.chmod(backup,0o700)
    stop()
    switched=False
    try:
        for file in list(ROOT.glob('*.py'))+[ROOT/name for name in DEPLOY_FILES]:
            if file.exists():
                shutil.copy2(file,backup/file.name)
        with sqlite3.connect(db) as source, sqlite3.connect(backup/db.name) as destination:
            source.backup(destination)
            before=source.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
            approvals_before=source.execute('SELECT COUNT(*) FROM approvals').fetchone()[0]
        switched=True
        for name in MODULES + DEPLOY_FILES:
            tmp=ROOT/(name+'.incoming')
            shutil.copyfile(STAGE/name,tmp)
            tmp.replace(ROOT/name)
        for name in MODULES:
            py_compile.compile(str(ROOT/name),doraise=True)
        os.chdir(ROOT)
        sys.path.insert(0,str(ROOT))
        from memory import Store
        store=Store(db)
        try:
            after=store._conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
            if after != before:
                raise RuntimeError('Migration changed memory row count')
            version=store.get_setting_raw('memory_policy_version')
            quarantined=store._conn.execute("SELECT COUNT(*) FROM memories WHERE status='quarantined'").fetchone()[0]
            schema=store._conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('memory_terms','memory_history','memory_retry','personality_vetoes')").fetchone()[0]
            if version != 3 or schema != 4:
                raise RuntimeError('Migration contract failed')
            if ('execution_state' not in {r[1] for r in store._conn.execute('PRAGMA table_info(approvals)')}
                    or store._conn.execute('SELECT COUNT(*) FROM approvals').fetchone()[0] != approvals_before
                    or store._conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok'):
                raise RuntimeError('Approval migration or database integrity check failed')
        finally:
            store.close()
        start()
        status=health(config)
        hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in MODULES + DEPLOY_FILES}
        print(json.dumps(dict(backup=str(backup),memories_before=before,memories_after=after,
                              quarantined=quarantined,policy_version=version,health=status,hashes=hashes)))
        return backup
    except BaseException:
        stop()
        if switched:
            for file in backup.iterdir():
                if file.suffix == '.py' or file.name in DEPLOY_FILES:
                    shutil.copy2(file,ROOT/file.name)
            for name in MODULES + DEPLOY_FILES:
                if not (backup/name).exists():
                    (ROOT/name).unlink(missing_ok=True)
            if (backup/db.name).exists():
                for suffix in ('-wal','-shm','-journal'):
                    Path(str(db)+suffix).unlink(missing_ok=True)
                shutil.copy2(backup/db.name,db)
        start()
        raise


def verify(backup=None):
    config=tomllib.loads((ROOT/'config.toml').read_text())
    token=(ROOT/'.api-token').read_text().strip()
    port=config.get('server',{}).get('port',8765)
    checks={}
    for endpoint in ('/status','/life/state','/qq/status','/approval/recent'):
        req=urllib.request.Request(f'http://127.0.0.1:{port}{endpoint}',headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=15) as response:
            data=json.load(response)
            checks[endpoint]={'http':response.status}
            if endpoint=='/qq/status':
                checks[endpoint]['online']=data.get('online')
                transport=data.get('transport') or {}
                checks[endpoint]['transport']={key:transport.get(key) for key in
                    ('receiver_alive','worker_alive','queue_size','health_probe_count','account_online')}
    db=(ROOT/config.get('server',{}).get('db_path','fish.db')).resolve()
    with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as conn:
        checks['sqlite']=conn.execute('PRAGMA quick_check').fetchone()[0]
        checks['memory_rows']=conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
        checks['policy_version']=json.loads(conn.execute("SELECT value FROM settings WHERE key='memory_policy_version'").fetchone()[0])
        checks['approval_execution_state']='execution_state' in {r[1] for r in conn.execute('PRAGMA table_info(approvals)')}
        if backup:
            if not backup.resolve().is_relative_to((ROOT/'.backups').resolve()):
                raise RuntimeError('Backup must be inside installation backup directory')
            keys={'llm','heybox_token','owner_qq','city','persona','name','self_profile','user_locked_settings',
                  'enabled_platforms','midnight_ok','approve_money','approve_publish','allow_self_evolve',
                  'temperament','mood_params','chat_scope','qq_quick_login_uin'}
            with sqlite3.connect(f'file:{backup/db.name}?mode=ro',uri=True) as before:
                original={k:v for k,v in before.execute('SELECT key,value FROM settings') if k in keys}
                current={k:v for k,v in conn.execute('SELECT key,value FROM settings') if k in keys}
                checks['configuration_preserved']=original==current
                checks['approval_rows_preserved']=before.execute('SELECT COUNT(*) FROM approvals').fetchone()[0]==conn.execute('SELECT COUNT(*) FROM approvals').fetchone()[0]
                if not checks['configuration_preserved'] or not checks['approval_rows_preserved']:
                    raise RuntimeError('Configuration or approval count changed; inspect before further actions')
    print(json.dumps(checks))


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify',action='store_true')
    parser.add_argument('--stage',type=Path,default=STAGE)
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args()
    STAGE=args.stage
    verify(args.backup) if args.verify else run()
