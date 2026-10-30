"""Read-only memory/chat diagnosis on the device; never copy the private DB."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading

ROOT = Path('/data/data/com.termux/files/home/dfy')
sys.path.insert(0, str(ROOT))
from memory_policy import redact_secrets

token = (ROOT/'.api-token').read_text().strip()


def clean(value):
    if isinstance(value, str):
        value = redact_secrets(value).replace(token, '[token]')
        value = re.sub(r'https?://\S+', '[url]', value)
        return re.sub(r'(?<!\d)\d{5,}(?!\d)', '[id]', value)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    return value


def emit(label, value):
    print(label, json.dumps(value if label == 'module_hashes' else clean(value), ensure_ascii=False), flush=True)


with sqlite3.connect((ROOT/'fish.db').as_uri()+'?mode=ro', uri=True) as conn:
    conn.row_factory = sqlite3.Row
    def rows(sql, args=()):
        return [dict(r) for r in conn.execute(sql, args)]
    if len(sys.argv) > 1 and sys.argv[1] == 'replay':
        from memory import Store, _overlap
        for turn in rows('SELECT id,ts,content FROM short_term WHERE id IN (1320,1325,1329,1332) ORDER BY id'):
            # All retrieval writes (last_access) go to a disposable in-memory copy.
            copy = sqlite3.connect(':memory:')
            conn.backup(copy)
            copy.execute('DELETE FROM memories WHERE created_at>?', (turn['ts'],))
            copy.commit()
            store = Store.__new__(Store)
            store._conn, store._lock = copy, threading.RLock()
            event = copy.execute("SELECT id,entity_id,scope,content FROM memories WHERE kind='episode' AND created_at=? ORDER BY id DESC LIMIT 1", (turn['ts'],)).fetchone()
            if not event:
                continue
            mid, entity, scope, text = event
            history = rows('SELECT role,content FROM (SELECT id,role,content FROM short_term WHERE id<=? ORDER BY id DESC LIMIT 20) ORDER BY id', (turn['id'],))
            if history and history[-1]['role'] == 'user' and history[-1]['content'] == turn['content']:
                history.pop()
            query = '\n'.join(h['content'][-300:] for h in history[-2:])+'\n'+text
            selected = store.retrieve(query, k=8, context_scope=scope, entity_id=entity, exclude_ids=(mid,))
            emit('replay', {'time':turn['ts'], 'query':query, 'memories':[{k:r[k] for k in ('id','kind','content','source_ids')} for r in selected], 'weapon_overlap':[{ 'id':r['id'], 'overlap':round(_overlap(query,r['content']),4)} for r in store._rows('id IN (486,637,640,634,639)')], 'working':store.working_context(scope,entity,exclude_ids=(mid,))})
            copy.close()
        sys.exit(0)
    emit('memory_counts', rows('SELECT kind,status,processed,count(*) AS n FROM memories GROUP BY kind,status,processed'))
    emit('recent_chat', rows("SELECT id,ts,role,content,session FROM short_term WHERE ts>='2026-10-30' ORDER BY id"))
    emit('recent_memory_errors', rows("SELECT ts,category,level,message FROM logs WHERE category IN ('memory','growth') ORDER BY id DESC LIMIT 25"))
    emit('weapon_memories', rows("SELECT id,kind,status,content,source_ids,created_at,scope,entity_id,subject FROM memories WHERE content LIKE '%太刀%' OR content LIKE '%怪猎%' OR content LIKE '%怪物猎人%' ORDER BY id"))
    emit('pending', rows("SELECT id,created_at,processed FROM memories WHERE kind='episode' AND processed=0 AND status='active' ORDER BY id LIMIT 12"))
    emit('module_hashes', {name: hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for name in ('memory.py','memory_system.py','qq_bot.py','brain.py')})
