"""Read-only monitor for a login driven entirely through App buttons."""
import json
from pathlib import Path
import re
import sys
import time
import urllib.request

ROOT=Path('/data/data/com.termux/files/home/dfy')
sys.path.insert(0,str(ROOT))
from server import QQ_LOG_PATH
from qq_control import ProcessManager

token=(ROOT/'.api-token').read_text().strip()
logs=[ROOT/'server.log',Path(QQ_LOG_PATH),ROOT/'qq_start_err.log']
offsets={p:p.stat().st_size if p.exists() else 0 for p in logs}
start=time.monotonic()
samples=int(sys.argv[1]) if len(sys.argv)>1 else 37
for i in range(samples):
    result={'elapsed':round(time.monotonic()-start,1),'time':time.strftime('%Y-%m-%d %H:%M:%S')}
    try:
        req=urllib.request.Request('http://127.0.0.1:8765/qq/status',headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=3) as response: state=json.load(response)
        result['state']={k:state.get(k) for k in ('running','account_ready','account_source','native_ready','online',
            'login_phase','login_accepted','control_error','qr','qr_age','desired_state','operation')}
        transport=state.get('transport') or {}
        result['transport']={k:transport.get(k) for k in ('receiver_alive','worker_alive','last_received_at','account_status_at','account_online')}
    except Exception as exc:
        result['api_error']=type(exc).__name__
    scan=ProcessManager().scan()
    result['processes']=[r for r in scan['rows'] if r['role'] in ('main','launcher')]
    result['fish_processes']=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try:
            args=(p/'cmdline').read_bytes().split(b'\0')
            if any(a in (b'server.py',str(ROOT/'server.py').encode(),str(ROOT/'run-fish.sh').encode()) for a in args):
                result['fish_processes'].append({'pid':int(p.name),'command':Path(args[0].decode()).name})
        except (OSError,IndexError): pass
    result['new_events']=[]
    for p in logs:
        if not p.exists(): continue
        with p.open('rb') as source:
            source.seek(offsets[p] if p.stat().st_size>=offsets[p] else 0)
            text=source.read().decode('utf-8','replace')
            offsets[p]=source.tell()
        for line in text.splitlines():
            if any(s in line for s in ('登录','初始化','ErrCode','Error','error','Traceback','QQ_EXIT','signal','Permission')):
                line=re.sub(r'\x1b\[[0-9;]*m','',line)
                line=re.sub(r'https?://\S+','[url]',line)
                line=re.sub(r'\d{5,}','[id]',line)
                result['new_events'].append({'file':p.name,'text':line[:240]})
    print(json.dumps(result,ensure_ascii=True),flush=True)
    if i<samples-1: time.sleep(5)
