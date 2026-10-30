"""Read-only outage timeline. Do not emit messages, account IDs or credentials."""
import json
from pathlib import Path
import sqlite3
import time
import re

ROOT = Path('/data/data/com.termux/files/home/dfy')


def emit(kind, value):
    print(json.dumps({'kind': kind, 'value': value}, ensure_ascii=True))


emit('time', time.strftime('%Y-%m-%d %H:%M:%S %z'))
for name in ('server.log', 'qq.log', 'qq_start_err.log', '.fish.pid'):
    p = ROOT / name
    if not p.exists():
        continue
    emit('file', {'name': name, 'size': p.stat().st_size,
                  'modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.stat().st_mtime))})
    if name == 'server.log':
        text = p.read_text(errors='replace')
        emit('server_markers', {marker: text.count(marker) for marker in
                               ('BrokenPipeError', 'Traceback', 'KeyboardInterrupt',
                                'Killed', 'Terminated', 'Not found; no service started')})
        emit('server_final_line_type', next((label for marker, label in
            (('Not found; no service started', 'service_not_found'),
             ('BrokenPipeError', 'broken_pipe'), ('Killed', 'killed'),
             ('Terminated', 'terminated')) if marker in text.splitlines()[-1]), 'other') if text.splitlines() else 'empty')

with sqlite3.connect((ROOT/'fish.db').as_uri()+'?mode=ro', uri=True) as conn:
    emit('database_check', conn.execute('PRAGMA quick_check').fetchone()[0])
    emit('last_log_times', conn.execute(
        'SELECT category,level,count(*),max(ts) FROM logs GROUP BY category,level').fetchall())
    emit('recent_event_types', conn.execute(
        "SELECT ts,category,level,CASE "
        "WHEN message LIKE '%QQ 连接断开%' THEN 'qq_disconnected' "
        "WHEN message LIKE '%启动%' THEN 'startup' "
        "WHEN message LIKE '%停止%' THEN 'stop' "
        "WHEN message LIKE '%超时%' THEN 'timeout' "
        "WHEN message LIKE '%上线%' OR message LIKE '%已连接%' THEN 'connected' "
        "ELSE 'other' END FROM logs ORDER BY id DESC LIMIT 35").fetchall())
    token = (ROOT/'.api-token').read_text().strip()
    rows = conn.execute(
        "SELECT ts,category,message FROM logs WHERE ts >= '2026-10-30 19:39:00' "
        "AND category IN ('status','qq','error') ORDER BY id DESC LIMIT 45").fetchall()
    safe = []
    for ts, category, message in rows:
        message = message.replace(token, '[redacted]') if token else message
        message = re.sub(r'https?://\S+', '[url]', message)
        message = re.sub(r'[A-Za-z0-9_-]{32,}', '[redacted]', message)
        message = re.sub(r'\d{5,}', '[id]', message)
        safe.append([ts, category, message[:250]])
    emit('connection_events', safe)
