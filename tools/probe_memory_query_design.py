"""Synthetic design experiment, not an end-to-end memory quality benchmark.

No device database, credentials, network, model calls or outgoing messages.
Compare the existing query formula with a current-question-only variant.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'fish'))
from memory import Store, _overlap


def run(case):
    store = Store(':memory:')
    try:
        event = store.record_episode('qq:synthetic', '测试人物', case['statement'], 'owner')
        fact = store.add_memory('fact', '测试人物', case['fact'], scope='owner',
                                entity_id='qq:synthetic', source_ids=[event])
        store.record_self_experience(case['assistant_history'], 'owner', event)
        result = {'case': case['id'], 'variants': {}}
        for name, query in [('existing', case['user_history']+'\n'+case['assistant_history']+'\n'+case['question']),
                            ('question_only', case['question'])]:
            selected = store.retrieve(query, context_scope='owner', entity_id='qq:synthetic')
            result['variants'][name] = {
                'direct_fact_in_context': fact in {r['id'] for r in selected},
                'fact_similarity': round(_overlap(query, case['fact']), 4),
                'kinds': [r['kind'] for r in selected]}
        return result
    finally:
        store.close()


if __name__ == '__main__':
    cases = [
        dict(id='background_dilution', statement='我喜欢喝乌龙茶', fact='喜欢喝乌龙茶',
             user_history='开始做一组记忆测试，看看你之前到底有没有保存，先别说其他事情。',
             assistant_history='可以，你直接提问吧。需要提前说明，我不会刻意猜测你的个人情况，如果资料中没有对应的记录，我会明确告诉你，而不是为了显得熟悉就随便编一个答案。',
             question='我喜欢喝什么饮料'),
        dict(id='semantic_gap', statement='我弹古典吉他', fact='日常爱好是弹古典吉他',
             user_history='再问你一个问题', assistant_history='好，你问吧。',
             question='我会演奏哪一种乐器'),
    ]
    print(json.dumps({'note': 'Synthetic retrieval probe only; no LLM answer was evaluated.',
                      'results': [run(case) for case in cases]}, ensure_ascii=False, indent=2))
