"""Measure indexed recall with synthetic data only; does not open the user's database."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'fish'))
from memory import Store


def run(count):
    store = Store(':memory:')
    try:
        with store._lock, store._conn:
            wanted = store._insert_memory('fact', '测试人物', '天文观测专属记录', scope='owner', entity_id='qq:test')
            for i in range(count):
                store._insert_memory('episode', '测试人物', f'普通日常聊天记录{i}', scope='owner', entity_id='qq:test')
        samples=[]
        for _ in range(20):
            start=time.perf_counter()
            rows=store.retrieve('天文观测专属记录', context_scope='owner')
            samples.append((time.perf_counter()-start)*1000)
            assert wanted in {row['id'] for row in rows}, 'Old relevant memory lost behind recent chatter'
        return dict(rows=count+1, median_ms=round(sorted(samples)[len(samples)//2],3), max_ms=round(max(samples),3), old_fact_found=True)
    finally:
        store.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows',type=int,nargs='+',default=[1000,10000])
    print(json.dumps([run(max(1,count)) for count in parser.parse_args().rows]))
