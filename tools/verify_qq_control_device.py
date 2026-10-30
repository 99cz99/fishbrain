"""Verify active QQ controller without sending messages or changing identity."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.request

ROOT=Path('/data/data/com.termux/files/home/dfy')
sys.path.insert(0,str(ROOT))
from qq_control import ProcessManager, WebUIClient
from qq_config import delivery_config
from server import NAPCAT_CONFIG_DIR, QQ_LOG_PATH


def request(path,post=False):
    token=(ROOT/'.api-token').read_text().strip()
    req=urllib.request.Request('http://127.0.0.1:8765'+path,headers={'Authorization':'Bearer '+token,
        'Content-Type':'application/json'},data=b'{}' if post else None)
    start=time.monotonic()
    with urllib.request.urlopen(req,timeout=8) as response:
        return json.load(response),round(time.monotonic()-start,3)


def identities():
    scan=ProcessManager().scan()
    if not scan['known']:
        raise RuntimeError('QQ process identities unavailable')
    return [r for r in scan['rows'] if r['role'] in ('main','launcher')]


def main(exercise,backup):
    first,_=request('/qq/status')
    before=identities()
    if not first.get('online') or not first.get('account_ready'):
        raise RuntimeError('Expected a currently authenticated and business-ready account')
    results={}
    if exercise:
        for name,path in (('hot_configuration','/qq/apply_onebot'),('already_authenticated_quick_login','/qq/quick_login')):
            result,latency=request(path,True)
            if not result.get('ok'):
                raise RuntimeError('Controller did not accept '+name)
            job=result.get('operation') or {}
            for _ in range(15):
                status,_=request('/qq/status')
                current=status.get('operation') or {}
                if current.get('id')!=job.get('id'):
                    raise RuntimeError('Verification job superseded')
                if current.get('status')=='completed':
                    break
                if current.get('status') not in ('queued','running','waiting'):
                    raise RuntimeError('Verification job not confirmed')
                time.sleep(1)
            else:
                raise RuntimeError('Verification job timed out')
            results[name]={'request_seconds':latency,'completed':True,
                           'native_session_preserved':before==identities()}
    status,_=request('/qq/status')
    client=WebUIClient(lambda:json.loads((Path(NAPCAT_CONFIG_DIR)/'webui.json').read_text()).get('token') or 'napcat')
    config=client.call('/api/OB11Config/GetConfig',{})
    wanted=delivery_config(config.get('data') or {},json.loads((ROOT/'onebot11.json').read_text()))
    list_result=client.call('/api/QQLogin/GetQuickLoginList',{})
    profile=Path(QQ_LOG_PATH).parents[2]/'root/.config/QQ'
    login=profile/'global/nt_data/Login'
    empty_files=sum(p.is_file() and p.stat().st_size==0 for p in login.rglob('*'))
    log_events=[]
    if backup:
        gate=json.loads((backup/'gate.json').read_text())
        relative='root/napcat/qq.log'
        offset=gate['files'].get(relative,{}).get('size',0)
        with Path(QQ_LOG_PATH).open('rb') as source:
            source.seek(offset if Path(QQ_LOG_PATH).stat().st_size>=offset else 0)
            text=source.read().decode('utf-8','replace')
        for line in text.splitlines():
            if any(word in line for word in ('快速登录','快捷登录','登录成功','QQ Is Logined','ErrCode','初始化完成')):
                clean=re.sub(r'https?://\S+','[url]',line)
                clean=re.sub(r'\d{5,}','<account>',clean)
                log_events.append(clean[:220])
    modules=tuple(p.name for p in ROOT.glob('*.py') if p.name in
        ('autonomy.py','brain.py','delivery.py','heybox.py','life.py','memory.py','memory_policy.py',
         'memory_system.py','personality.py','qq_bot.py','qq_config.py','qq_delivery.py','qq_faces.py',
         'qq_media.py','server.py','weather.py','qq_control.py'))
    with sqlite3.connect((ROOT/'fish.db').as_uri()+'?mode=ro',uri=True) as conn:
        integrity=conn.execute('PRAGMA quick_check').fetchone()[0]
    result={'control_version':status.get('control_version'),'account_ready':status.get('account_ready'),
            'account_matches_before':status.get('account')==first.get('account'),'online':status.get('online'),
            'process_known':status.get('process_known'),'session_preserved':before==identities(),
            'operation':status.get('operation'),'exercise':results,
            'onebot_config_matches_delivery_contract':config['ok'] and config.get('data')==wanted,
            'native_list_known':list_result['ok'],'native_candidates':len(list_result.get('data') or []) if list_result['ok'] else None,
            'empty_login_files':empty_files,'cold_start_login_events':log_events[-12:],'sqlite':integrity,
            'runtime_hashes':{name:hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in modules}}
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args()
    main(args.exercise,args.backup)
