from pathlib import Path
import json,sys,hashlib,sqlite3,urllib.request
stage=Path('/data/local/tmp/dfy-qq-delivery-20261029')
root=Path('/data/data/com.termux/files/home/dfy')
sys.path.insert(0,str(root))
import qq_media
from qq_delivery import signature
base=qq_media.ROOTFS/'opt/QQ/resources/app/app_launcher/napcat/logs'
for p in base.glob('*.log'):
 for line in p.read_text(errors='replace').splitlines():
  if line.startswith('10-29 09:35:39') and '转化为 OB11Message ' in line:
   d=json.loads(line.split('转化为 OB11Message ',1)[1])['arrayMsg']
   image=next(s for s in d['message'] if s['type']=='image')
   raw,mime=qq_media.cached_image(image['data']['file'])
   print('historical_image_recovered',len(raw),mime)
  if line.startswith('10-28 15:23:42') and '转化为 OB11Message ' in line:
   d=json.loads(line.split('转化为 OB11Message ',1)[1])['arrayMsg']
   print('native_self_receipt',dict(post_type=d['post_type'],sender_is_self=d['sender']['user_id']==d['self_id'],user_is_peer=d['user_id']!=d['self_id'],has_target='target_id' in d,signature_supported=signature(d['message']) is not None))
  if line.startswith('10-28 15:23:42') and 'RawMessage ' in line:
   raw=json.loads(line.split('RawMessage ',1)[1])
   print('adapter_target_source_usable',str(raw.get('peerUin','')).isdigit() and str(raw.get('peerUin'))!='0')
sources=[p for p in stage.glob('*.py') if (root/p.name).is_file() and p.name in
         ('autonomy.py','brain.py','delivery.py','heybox.py','life.py','memory.py','memory_policy.py',
          'memory_system.py','personality.py','qq_bot.py','qq_config.py','qq_delivery.py','qq_media.py','server.py','weather.py')]
print('installed_sources_match_staging',len(sources)==15 and all((root/p.name).read_bytes()==p.read_bytes() for p in sources))
token=(root/'.api-token').read_text().strip()
with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/qq/status',headers={'Authorization':'Bearer '+token}),timeout=10) as response:
 status=json.load(response)
print('live_status',json.dumps({k:status.get(k) for k in ('online','running','stale')}),json.dumps(status.get('transport')))
account=status.get('self_id')
if account:
 config=json.loads((qq_media.ROOTFS/f'opt/QQ/resources/app/app_launcher/napcat/config/onebot11_{account}.json').read_text())
 own=next(s for s in config['network']['websocketServers'] if s.get('port')==8081)
 print('onebot_config',dict(reportSelfMessage=own.get('reportSelfMessage'),enableForcePushEvent=own.get('enableForcePushEvent'),format=own.get('messagePostFormat'),baseTimeout=config['timeout']['baseTimeout']))
with sqlite3.connect(f'file:{root}/fish.db?mode=ro',uri=True) as conn:
 print('sqlite',conn.execute('PRAGMA quick_check').fetchone()[0])
 print('outbox_schema',bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='qq_outbox'").fetchone()))
 print('post_update_qq_errors',conn.execute("SELECT count(*) FROM logs WHERE ts>='2026-10-29 20:41:10' AND category='error' AND message LIKE 'QQ %'").fetchone()[0])
