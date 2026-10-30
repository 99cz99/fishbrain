"""Read-only comparison of current WebUI and OneBot status evidence. No login actions."""
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

ROOT = Path('/data/data/com.termux/files/home/dfy')
sys.path.insert(0, str(ROOT))
from server import Fish
from qq_control import WebUIClient, ProcessManager


def emit(label, value):
    print(json.dumps({'probe': label, **value}, ensure_ascii=True), flush=True)


def api(path, body=None):
    token = (ROOT/'.api-token').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:8765'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    start=time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=16) as response:
            return json.load(response), round(time.monotonic()-start,3)
    except Exception as exc:
        return {'error_type':type(exc).__name__},round(time.monotonic()-start,3)


status,seconds=api('/qq/status')
transport=status.get('transport') or {}
status['transport']={k:v for k,v in transport.items() if k!='account_status_self_id'}
status['transport']['account_status_identity_matches']=str(transport.get('account_status_self_id'))==str(status.get('self_id'))
emit('fish_status',{'seconds':seconds,'state':{k:status.get(k) for k in (
    'running','account_ready','online','account_state','login_phase','login_state_known',
    'login_accepted','control_error','observed_at','account_observed_at','operation','transport')}})
onebot,seconds=api('/qq/api',{'action':'get_status','params':{}})
data=(onebot.get('response') or {}).get('data') or {}
emit('onebot_status',{'seconds':seconds,'ok':onebot.get('ok'),'retcode':(onebot.get('response') or {}).get('retcode'),
                      'online':data.get('online'),'good':data.get('good'),'error_type':onebot.get('error_type')})
info,seconds=api('/qq/api',{'action':'get_login_info','params':{}})
info_data=(info.get('response') or {}).get('data') or {}
emit('onebot_identity',{'seconds':seconds,'ok':info.get('ok'),'has_account':bool(info_data.get('user_id')),
                       'matches_current_connection':str(info_data.get('user_id'))==str(status.get('self_id'))})
probe=Fish.__new__(Fish)
client=WebUIClient(probe._webui_token,timeout=15)
for path in ('/api/QQLogin/CheckLoginStatus','/api/QQLogin/GetQQLoginInfo'):
    start=time.monotonic()
    result=client.call(path,{})
    data=result.get('data') or {}
    safe={k:data.get(k) for k in ('isLogin','isOffline','coreReady','loginPhase','qrLoginAccepted')} if path.endswith('CheckLoginStatus') else {
        'has_account':bool(data.get('uin')),'matches_current_connection':str(data.get('uin'))==str(status.get('self_id'))}
    emit(path,{'seconds':round(time.monotonic()-start,3),'ok':result['ok'],'kind':result.get('kind'),'state':safe})
scan=ProcessManager().scan()
for row in scan['rows']:
    if row['role'] not in ('main','launcher'):
        continue
    text=(Path('/proc')/str(row['pid'])/'status').read_text()
    fields={k:re.search(r'^'+k+r':\s*(.*)$',text,re.M).group(1) for k in ('State','VmRSS','Threads')}
    emit('process',{'role':row['role'],**fields})
try:
    emit('load',{'average':os.getloadavg()})
except (OSError, AttributeError):
    pass
