"""Isolated phone recall/answer verification, never sends platform messages.

Uses the existing provider configuration on-device. Real DB is read-only; all
retrieval/access mutations go to RAM. --turn replays a historical user turn.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time

ROOT = Path('/data/data/com.termux/files/home/dfy')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--turn', type=int)
    parser.add_argument('--pipeline', action='store_true')
    parser.add_argument('--inventory', action='store_true')
    args = parser.parse_args()
    if args.inventory:
        import hashlib
        with closing(sqlite3.connect((ROOT/'fish.db').as_uri()+'?mode=ro',uri=True)) as db:
            print(json.dumps(dict(
                quick_check=db.execute('PRAGMA quick_check').fetchone()[0],
                memory_rows=db.execute('SELECT count(*) FROM memories').fetchone()[0],
                hashes={p.name:hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
                        for p in ROOT.glob('*.py')})),flush=True)
        return
    sys.path.insert(0, str(args.stage))
    from brain import Brain
    from memory import Store
    from memory_policy import data_message, redact_secrets
    from memory_service import MemoryService, READ_RULES
    with closing(sqlite3.connect((ROOT/'fish.db').as_uri()+'?mode=ro',uri=True)) as source:
        cfg = json.loads(source.execute("SELECT value FROM settings WHERE key='llm'").fetchone()[0])
        if args.turn:
            store = Store.__new__(Store)
            store._conn = sqlite3.connect(':memory:')
            store._lock = threading.RLock()
            source.backup(store._conn)
            conn = store._conn
            row = conn.execute('SELECT ts,session,content FROM short_term WHERE id=? AND role=\'user\'', (args.turn,)).fetchone()
            if not row:
                raise RuntimeError('Expected a user turn')
            ts, session, content = row
            event = conn.execute("SELECT id,content,scope,entity_id FROM memories WHERE kind='episode' AND created_at=? ORDER BY id DESC LIMIT 1", (ts,)).fetchone()
            if not event:
                raise RuntimeError('Expected original event')
            mid, query, scope, entity = event
            conn.execute('DELETE FROM memories WHERE created_at>?', (ts,))
            conn.commit()
            keys = ('role','content','session','ts')
            history = [dict(zip(keys,r)) for r in reversed(conn.execute(
                'SELECT role,content,session,ts FROM short_term WHERE id<? ORDER BY id DESC LIMIT 20', (args.turn,)).fetchall())]
            conn.execute('DELETE FROM short_term WHERE id>=?', (args.turn,))
            conn.commit()
            cases = [('historical',store,query,history,scope,entity,session,(mid,),None)]
        else:
            store = Store(':memory:')
            event = store.record_episode('qq:synthetic', '测试人物', '我弹古典吉他', 'owner')
            fact = store.add_memory('fact', '测试人物', '日常爱好是弹古典吉他',scope='owner',entity_id='qq:synthetic',source_ids=[event])
            store.record_self_experience('你没有说过自己会什么乐器，我不知道。','owner',event)
            cases = [('semantic_gap',store,'我会演奏哪一种乐器',[],'owner','qq:synthetic','private_synthetic',(),fact)]
    usage = []
    brain = Brain(lambda: cfg, lambda p,c: usage.append((p,c)))
    for name,store,query,history,scope,entity,session,exclude,expected in cases:
        try:
            service = MemoryService(store,brain)
            if args.pipeline:
                from unittest.mock import Mock, patch
                from qq_bot import QQBot
                bot = QQBot('ws://unused',brain,store,request_approval=Mock(),
                    consolidate=lambda *a, **kw: mid)
                bot._get_image_bytes = Mock(return_value=(None,None))
                bot._get_avatar_desc = Mock(return_value='')
                bot._send_segments = Mock()
                bot.self_id = 'isolated-agent'
                with patch('qq_bot.weather.get_weather',return_value=''):
                    for question in (query,'你从哪里知道的？请给出原话和时间。'):
                        bot._send_segments.reset_mock()
                        bot._on_message(dict(message_type='private',user_id=entity.split(':',1)[1],
                            message_id='isolated-replay',sender={'nickname':'测试人物'},
                            message=[{'type':'text','data':{'text':question}}]))
                        drafts = bot._send_segments.call_args.args[1] if bot._send_segments.called else []
                        output = {'case':'full_pipeline','sent_to_platform':False,
                            'draft':drafts, 'metadata_echo':any('[消息时间=' in s for s in drafts)}
                        print(json.dumps(output,ensure_ascii=True),flush=True)
                continue
            for index,question in enumerate((query,'你从哪里知道的？请给出原话和时间。')):
                started = time.monotonic()
                result = service.prepare(question,history,scope,entity,session,exclude_ids=exclude)
                data = service.data(result)
                answer = brain.chat([{'role':'system','content':READ_RULES+' 根据资料简短回答，不编造程序状态。'},
                    data_message(data), {'role':'user','content':question}],temperature=0)
                service.remember(result)
                output = dict(case=name,turn=index,status=result['status'],search_status=result['search_status'],
                    ids=list(result['_versions']),candidates=result['candidate_count'],
                    expected_fact_recalled=expected in result['_versions'] if expected else None,
                    seconds=round(time.monotonic()-started,2), answer=redact_secrets(answer))
                print(json.dumps(output,ensure_ascii=True),flush=True)
            print(json.dumps({'usage':usage}),flush=True)
            if not args.turn:
                from autonomy import Autonomy
                for text in ('你刚才那句话是什么意思', '我喜欢喝乌龙茶，下周三下午告诉你面试结果'):
                    with closing(sqlite3.connect(':memory:')):
                        writer_store = Store(':memory:')
                        try:
                            writer_store.record_episode('qq:synthetic','测试人物',text,'owner',occurred_at=time.time()-60)
                            Autonomy(writer_store,brain).process_memory_batch()
                            counts = writer_store._conn.execute("SELECT kind,count(*) FROM memories WHERE kind!='episode' GROUP BY kind").fetchall()
                            errors = writer_store.get_logs('memory',limit=1)
                            print(json.dumps({'case':'writer','input_kind':'question' if text.startswith('你') else 'statement_commitment',
                                'counts':counts,'error':any(r['level']=='error' for r in errors)},ensure_ascii=True),flush=True)
                        finally:
                            writer_store.close()
        finally:
            store.close()


if __name__ == '__main__':
    main()
