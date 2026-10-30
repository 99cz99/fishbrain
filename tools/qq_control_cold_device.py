"""One gated QQ cold-start experiment under Termux UID. Private snapshot stays on phone.

prepare: request a confirmed stop, snapshot the complete Debian HOME, native config
and machine identity, verify every regular member. start: verify that gate and launch
exactly once through the controller. observe: read-only summary, no login retries.
File backups cannot promise recovery of external/system-bound keys.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import time
import urllib.request

ROOT = Path('/data/data/com.termux/files/home/dfy')
CONTAINER = Path('/data/data/com.termux/files/usr/var/lib/proot-distro/containers/debian/rootfs')
CONFIG = CONTAINER/'opt/QQ/resources/app/app_launcher/napcat/config'
sys.path.insert(0,str(ROOT))
from qq_control import ProcessManager, WebUIClient


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def request(path, post=False):
    token=(ROOT/'.api-token').read_text().strip()
    req=urllib.request.Request('http://127.0.0.1:8765'+path,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},data=b'{}' if post else None)
    with urllib.request.urlopen(req,timeout=8) as response:
        return json.load(response)


def assert_stopped():
    scan=ProcessManager().scan()
    if not scan['known'] or scan['rows']:
        raise RuntimeError('QQ process/session exit has not been confirmed; backup/start gate closed')


def prepare():
    status=request('/qq/status')
    if status.get('control_version') != 2 or not status.get('account_ready'):
        raise RuntimeError('Need a confirmed authenticated account and controller v2')
    before=ProcessManager().scan()
    client=WebUIClient(lambda:json.loads((CONFIG/'webui.json').read_text()).get('token') or 'napcat')
    candidates=client.call('/api/QQLogin/GetQuickLoginList',{})
    if not candidates['ok']:
        raise RuntimeError('Native candidate query unavailable; no cold trial')
    scopes=[CONTAINER/'root', CONFIG, CONTAINER/'etc/machine-id', CONTAINER/'var/lib/dbus/machine-id']
    files=[p for scope in scopes for p in ([scope] if scope.is_file() else scope.rglob('*')) if p.is_file() and not p.is_symlink()]
    required=sum(p.stat().st_size for p in files)
    stat=os.statvfs(ROOT)
    if stat.f_bavail*stat.f_frsize < required*1.5+300*1024*1024:
        raise RuntimeError('Insufficient space for complete HOME snapshot with margin')
    backup=ROOT/'.backups'/('qq-cold-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=False,mode=0o700)
    op=request('/qq/stop',True)
    if not op.get('queued'):
        raise RuntimeError('Stop request was not queued')
    started=time.monotonic()
    for _ in range(25):
        current=request('/qq/status')
        job=current.get('operation') or {}
        if job.get('id') != op['operation']['id']:
            raise RuntimeError('Stop operation superseded; cold trial aborted')
        if job.get('status')=='completed':
            break
        if job.get('status') not in ('queued','running','waiting'):
            raise RuntimeError('Stop was not confirmed: '+str(job.get('code')))
        time.sleep(2)
    else:
        raise RuntimeError('Stop confirmation deadline exceeded')
    assert_stopped()
    print(json.dumps({'phase':'stopped','seconds':round(time.monotonic()-started,2),'backup':str(backup)}),flush=True)
    # Rebuild inventory after the final native writer exits. Include every HOME
    # file, hidden config, logs and legacy credential location, not just Login/.
    manifest={}
    for scope in scopes:
        for p in ([scope] if scope.is_file() else scope.rglob('*')):
            if p.is_file() and not p.is_symlink():
                manifest[str(p.relative_to(CONTAINER))]={'size':p.stat().st_size,'sha256':digest(p)}
    archive=backup/'debian-home-native.tar'
    with tarfile.open(archive,'w') as tar:
        for scope in scopes:
            tar.add(scope,arcname=str(scope.relative_to(CONTAINER)),recursive=True)
    os.chmod(archive,0o600)
    verified=set()
    with tarfile.open(archive,'r') as tar:
        for member in tar:
            if not member.isfile():
                continue
            expected=manifest[member.name]
            h=hashlib.sha256()
            with tar.extractfile(member) as source:
                for chunk in iter(lambda:source.read(1024*1024),b''):
                    h.update(chunk)
            if member.size != expected['size'] or h.hexdigest()!=expected['sha256']:
                raise RuntimeError('Snapshot verification failed')
            verified.add(member.name)
    if verified != set(manifest) or any(digest(CONTAINER/p)!=value['sha256'] for p,value in manifest.items()):
        raise RuntimeError('Snapshot/source changed during stopped verification')
    assert_stopped()
    evidence={'verified':True,'started':False,'before_processes':before['rows'],
              'account':status['account'], 'native_candidates':len(candidates.get('data') or []),
              'archive_sha256':digest(archive),'files':manifest,
              'boundary':'File snapshot only; no claim of portable system-bound key recovery'}
    gate=backup/'gate.json'
    gate.write_text(json.dumps(evidence),encoding='utf-8')
    os.chmod(gate,0o600)
    print(json.dumps({'phase':'snapshot_verified','backup':str(backup),'regular_files':len(manifest),
                      'archive_bytes':archive.stat().st_size,'no_native_processes':True}),flush=True)


def start(backup):
    backup=backup.resolve()
    if not backup.is_relative_to((ROOT/'.backups').resolve()):
        raise RuntimeError('Unexpected snapshot directory')
    gate=backup/'gate.json'
    evidence=json.loads(gate.read_text())
    if not evidence.get('verified') or evidence.get('started'):
        raise RuntimeError('No unused verified snapshot gate')
    assert_stopped()
    if digest(backup/'debian-home-native.tar')!=evidence['archive_sha256']:
        raise RuntimeError('Snapshot archive changed')
    if any(digest(CONTAINER/p)!=v['sha256'] for p,v in evidence['files'].items()):
        raise RuntimeError('Native profile changed after verification')
    # Consume the gate before submitting. A lost HTTP response never causes replay.
    evidence.update(started=True,start_at=time.time())
    gate.write_text(json.dumps(evidence),encoding='utf-8')
    result=request('/qq/start',True)
    evidence['start_operation']=result.get('operation')
    gate.write_text(json.dumps(evidence),encoding='utf-8')
    print(json.dumps({'phase':'start_submitted','ok':result.get('ok'),'operation':result.get('operation')}),flush=True)


def observe(backup):
    evidence=json.loads((backup/'gate.json').read_text()) if backup else {}
    status=request('/qq/status')
    scan=ProcessManager().scan()
    old={(r['pid'],r['start']) for r in evidence.get('before_processes',[])}
    rows=scan['rows']
    print(json.dumps({'running':status.get('running'),'account_ready':status.get('account_ready'),
                      'account_matches_before':status.get('account')==evidence.get('account'),
                      'online':status.get('online'),'login_phase':status.get('login_phase'),
                      'login_accepted':status.get('login_accepted'),'qr':status.get('qr'),
                      'desired_state':status.get('desired_state'),'operation':status.get('operation'),
                      'process_known':scan['known'],'processes':rows,
                      'no_old_process_identity':bool(rows) and not any((r['pid'],r['start']) in old for r in rows),
                      'elapsed':round(time.time()-evidence.get('start_at',time.time()),1)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','start','observe'))
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args()
    if args.action=='prepare': prepare()
    elif args.action=='start': start(args.backup)
    else: observe(args.backup)
