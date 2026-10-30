"""Recall contracts: scoped semantic candidates, raw evidence and invalidation."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brain import Brain
from memory import Store, _overlap
from memory_service import MemoryService
from memory_policy import person_code
from autonomy import Autonomy
from life import Life


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.brain = Mock()
        self.service = MemoryService(self.store, self.brain)

    def event(self, text='我弹古典吉他', entity='qq:101', scope='owner'):
        return self.store.record_episode(entity, '测试人物', text, scope)

    def fact(self, event, text='日常爱好是弹古典吉他', entity='qq:101', scope='owner'):
        return self.store.add_memory('fact', '测试人物', text, entity_id=entity,
                                     scope=scope, source_ids=[event])

    def prepare(self, query='我会演奏哪一种乐器', **kw):
        return self.service.prepare(query, kw.pop('history', []), kw.pop('scope','owner'),
            kw.pop('entity_id','qq:101'), kw.pop('session','private_101'), **kw)

    def choose(self, ids, intent='facts'):
        self.brain.select_memory.return_value = dict(ids=ids, intent=intent)

    def test_semantic_directory_does_not_require_lexical_overlap(self):
        event = self.event()
        fact = self.fact(event)
        self.assertEqual(_overlap('我会演奏哪一种乐器', '日常爱好是弹古典吉他'), 0)
        self.choose([fact])
        result = self.prepare()
        self.assertEqual(result['status'], 'supported')
        self.assertEqual([r['id'] for r in result['memories']], [fact])
        self.assertIn(event, [r['id'] for r in result['evidence']])
        call = self.brain.select_memory.call_args
        self.assertIn(fact, [r['id'] for r in call.args[2]])
        self.assertEqual(call.kwargs['current_person'], person_code('qq:101'))

    def test_assistant_background_does_not_change_query_or_user_topic(self):
        event = self.event()
        self.choose([event])
        self.prepare(history=[{'role':'user','content':'我在练乐器','session':'private_101'},
            {'role':'assistant','content':'ASSISTANT_GUESS_MARKER'*30},
            {'role':'user','content':'OTHER_SESSION','session':'group_3'}])
        call = self.brain.select_memory.call_args
        self.assertEqual(call.args[0], '我会演奏哪一种乐器')
        self.assertEqual(call.args[1], ['我在练乐器'])

    def test_fact_question_excludes_assistant_experience_even_if_selected(self):
        event = self.event()
        experience = self.store.record_self_experience('你没说过自己喜欢什么', 'owner', event)
        self.choose([experience], 'facts')
        result = self.prepare()
        self.assertEqual(result['memories'], [])
        self.assertNotIn('你没说过', str(self.service.data(result)))

    def test_conversation_question_keeps_roles_and_original_evidence(self):
        event = self.event()
        experience = self.store.record_self_experience('好好练琴', 'owner', event)
        self.choose([experience], 'conversation')
        result = self.prepare('你上次怎么回我的')
        data = self.service.data(result)
        self.assertEqual(data['memories'][0]['source_type'], 'assistant_experience')
        self.assertEqual(data['original_evidence'][0]['source_type'], 'user_statement')

    def test_source_followup_has_previous_readset_without_copying_text(self):
        event = self.event()
        fact = self.fact(event)
        self.choose([fact])
        first = self.prepare()
        self.service.remember(first)
        self.choose([event], 'source')
        result = self.prepare('你从哪里知道的')
        self.assertIn(event, self.brain.select_memory.call_args.args[3])
        self.assertIn(fact, self.brain.select_memory.call_args.args[3])
        self.assertIn(event, result['_versions'])
        self.assertEqual(set(self.service._readsets[first['_key']][1]), {fact,event})

    def test_group_scope_never_sends_owner_evidence_to_selector(self):
        private = self.event('PRIVATE_MARKER')
        public = self.event('PUBLIC_MARKER', scope='group:3')
        self.choose([public])
        result = self.prepare(scope='group:3', session='group_3')
        self.assertNotIn('PRIVATE_MARKER', str(self.brain.select_memory.call_args))
        self.assertNotIn(private, result['_versions'])

    def test_current_event_and_transitive_derivatives_excluded(self):
        event = self.event()
        fact = self.fact(event)
        derived = self.store.add_memory('fact','测试人物','二次派生',scope='owner',
            entity_id='qq:101',source_ids=[fact])
        self.choose([])
        result = self.prepare(exclude_ids=(event,))
        self.assertEqual(result['memories'], [])
        self.brain.select_memory.assert_not_called()
        self.assertEqual(result['visible_count'], 0)

    def test_archival_does_not_make_valid_fact_invisible(self):
        event = self.event()
        fact = self.fact(event)
        self.store._conn.execute("UPDATE memories SET status='archived' WHERE id IN (?,?)", (event,fact))
        self.store._conn.commit()
        self.choose([fact])
        self.assertIn(fact, self.prepare()['_versions'])

    def test_delete_invalidates_bundle_and_previous_readset(self):
        event = self.event()
        fact = self.fact(event)
        self.choose([fact])
        bundle = self.prepare()
        self.service.remember(bundle)
        self.store.delete_memory(fact)
        self.assertFalse(self.service.valid(bundle))
        self.choose([])
        after = self.prepare('你从哪里知道的')
        self.assertEqual(after['memories'], [])
        self.assertNotIn(event, after['_versions'])

    def test_fact_revision_invalidates_inflight_answer_but_access_does_not(self):
        event = self.event()
        fact = self.fact(event)
        self.choose([fact])
        bundle = self.prepare()
        self.store.retrieve('古典吉他', context_scope='owner')
        self.assertTrue(self.service.valid(bundle))
        self.store._conn.execute('UPDATE memories SET content=? WHERE id=?', ('修订后的事实',fact))
        self.store._conn.commit()
        self.assertFalse(self.service.valid(bundle))

    def test_unknown_private_source_is_not_expanded(self):
        source = self.event('PRIVATE_MARKER', scope='private:202', entity='qq:202')
        row = self.fact(self.event('公开陈述',scope='global'), text='公开错误投影',scope='global')
        self.store._conn.execute('UPDATE memories SET source_ids=? WHERE id=?', (json.dumps([source]),row))
        self.store._conn.commit()
        self.choose([row])
        result = self.prepare(scope='global')
        self.assertEqual(result['memories'], [])
        self.assertNotIn('PRIVATE_MARKER', str(self.service.data(result)))
        self.assertNotIn(row, [r['id'] for r in self.brain.select_memory.call_args.args[2]])
        self.assertNotIn('PRIVATE_MARKER', str(self.brain.select_memory.call_args))

    def test_budget_and_provider_failure_distinguished_from_absence(self):
        self.event()
        self.service.DIRECTORY_CHARS = 1
        self.choose([])
        self.assertEqual(self.prepare()['status'], 'incomplete')
        self.service.DIRECTORY_CHARS = 24000
        self.brain.select_memory.side_effect = TimeoutError()
        self.assertEqual(self.prepare()['status'], 'unavailable')

    def test_invalid_selected_ids_never_fetch_arbitrary_records(self):
        self.event()
        self.choose([999999])
        bundle = self.prepare()
        self.assertEqual(bundle['status'], 'unavailable')
        self.assertNotIn(999999, bundle['_versions'])

    def test_legacy_record_has_explicit_unverified_source(self):
        mid = self.store.add_memory('user','旧昵称','喜欢茶',scope='owner')
        self.choose([mid])
        result = self.service.data(self.prepare('以前的记录有什么依据'))
        self.assertEqual(result['memories'][0]['source_type'], 'legacy_unverified')
        self.assertEqual(result['original_evidence'], [])

    def test_manual_correction_is_authoritative_without_inventing_chat_quote(self):
        event = self.event()
        fact = self.fact(event)
        replacement = self.store.update_memory(fact, {'content':'现在练钢琴'})
        self.choose([replacement])
        result = self.service.data(self.prepare('我现在练什么乐器'))
        self.assertEqual(result['memories'][0]['source_type'], 'manual_assertion')
        self.assertEqual(result['original_evidence'], [])
        self.assertNotIn('古典吉他', str(result))

    def test_legacy_source_chain_does_not_masquerade_as_original_user_statement(self):
        old = self.store.add_memory('fact','测试人物','旧的推断',scope='owner',entity_id='qq:101')
        derived = self.fact(old, '派生推断')
        self.choose([derived])
        result = self.service.data(self.prepare('以前有什么依据'))
        self.assertEqual(result['memories'][0]['source_type'], 'legacy_unverified')

    def test_short_history_metadata_preserves_time_and_session(self):
        self.store.add_short_term('private_101','user','你好')
        plain = self.store.get_short_term('private:101')
        rich = self.store.get_short_term('private:101', with_metadata=True)
        self.assertEqual(set(plain[0]), {'role','content'})
        self.assertEqual(rich[0]['session'],'private_101')
        self.assertTrue(rich[0]['ts'])

    def test_neighbor_context_retains_short_answer_topic_and_roles(self):
        previous = self.event('我在练乐器')
        event = self.event('古典吉他')
        fact = self.fact(event)
        self.choose([fact])
        data = self.service.data(self.prepare())
        context = next(r for r in data['original_evidence'] if r['id'] == previous)
        self.assertEqual(context['source_type'], 'adjacent_context')

    def test_new_writer_rejects_question_intention_and_fabricated_quote(self):
        event = self.event('你刚才是什么意思')
        rows = self.store._rows('id=?',(event,),1)
        made = self.store.apply_episode_result(rows, {'schema_version':2,'memories':[
            dict(kind='intention',content='等待解释',source_ids=[event],speech_act='question',quote='你刚才是什么意思'),
            dict(kind='fact',content='虚构的爱好',source_ids=[event],speech_act='assertion',quote='我喜欢跑步')]})
        self.assertEqual(made,0)
        self.assertEqual(self.store.get_memories(kind='intention'),[])

    def test_new_writer_accepts_sourced_statement_and_real_commitment(self):
        event = self.event('我喜欢茶，明天下午告诉你结果')
        rows = self.store._rows('id=?',(event,),1)
        made = self.store.apply_episode_result(rows, {'schema_version':2,'memories':[
            dict(kind='fact',content='喜欢茶',source_ids=[event],speech_act='assertion',quote='我喜欢茶'),
            dict(kind='intention',content='明天下午告知结果',source_ids=[event],speech_act='commitment',quote='明天下午告诉你结果')]})
        self.assertEqual(made,2)

    def test_heartbeat_and_action_expand_sources_with_the_same_visibility(self):
        event = self.event()
        fact = self.fact(event)
        life = Life(self.store, self.brain)
        life._context_ids = [fact]
        private = life._action_prompt(self.store.get_settings(),'陪伴',target_scope='owner')
        self.assertIn('我弹古典吉他',private)
        self.assertNotIn('我弹古典吉他',life._action_prompt(self.store.get_settings(),'表达',target_scope='global'))

    def test_semantic_timeout_not_forwarded_to_provider_payload(self):
        for provider, response in (
            ('openai', {'choices':[{'message':{'content':'{"intent":"facts","ids":[]}'}}]}),
            ('anthropic', {'content':[{'text':'{"intent":"facts","ids":[]}'}]}),
            ('gemini', {'candidates':[{'content':{'parts':[{'text':'{"intent":"facts","ids":[]}'}]}}]})):
            brain = Brain(lambda: {'provider':provider,'base_url':'https://test.invalid'})
            with patch.object(brain, '_post_raw', return_value=response) as post:
                self.assertEqual(brain.select_memory('问题',[],[])['ids'], [])
                self.assertEqual(post.call_args.kwargs['timeout'], 15)
                self.assertNotIn('_request_timeout', str(post.call_args.args[2]))


if __name__ == '__main__':
    unittest.main()
