import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory import Store
from autonomy import Autonomy
from qq_bot import QQBot, build_system_prompt
from server import Handler


class PersonalityTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)

    def experiences(self, count=2):
        ids = []
        for i in range(count):
            event = self.store.record_episode('qq:101', '私人名字', f'私人事件{i}，请坦率表达意见', 'owner')
            ids.append(self.store.record_self_experience(f'我提出了自己的不同看法{i}', 'owner', event))
        return ids

    def candidate(self, ids, text='愿意坦率表达自己的判断', **extra):
        return dict(dimension='style', content=text, source_ids=ids, replaces=[], **extra)

    def publish(self, ids):
        return self.store.apply_personality_candidates([self.candidate(ids)],
                    self.store.get_memories(entity_id='self'), self.store.get_settings()['self_profile_revision'])

    def brain(self, candidate, approved=True):
        brain = Mock()
        brain.chat.side_effect = [json.dumps({'traits':[candidate]}), json.dumps({'reviews':[
            dict(index=0, trait_only=True, no_private=approved, supported=True, compatible=True)]})]
        return brain

    def test_private_experiences_shape_one_public_personality_without_private_story(self):
        ids = self.experiences()
        auto = Autonomy(self.store, self.brain(self.candidate(ids)))
        auto.distill_self_profile(force=True)
        settings = self.store.get_settings()
        self.assertEqual(settings['growth_status']['state'], 'updated')
        prompt = build_system_prompt(settings)
        self.assertIn('愿意坦率表达自己的判断', prompt)
        self.assertNotIn('私人事件', prompt)
        self.assertNotIn('私人名字', prompt)
        self.assertEqual(self.store.retrieve('私人事件', context_scope='group:5'), [])
        self.assertEqual(self.store.get_memories(kind='experience')[0]['scope'], 'owner')

    def test_privacy_reviewer_rejection_is_fail_closed(self):
        ids = self.experiences()
        Autonomy(self.store, self.brain(self.candidate(ids), False)).distill_self_profile(True)
        self.assertEqual(self.store.get_settings()['self_profile']['style'], [])

    def test_identifiers_rejected_even_if_model_approves(self):
        ids = self.experiences()
        for text in ('喜欢私人名字', '密码是123456', '喜欢https://example.com'):
            Autonomy(self.store, self.brain(self.candidate(ids, text))).distill_self_profile(True)
            self.assertEqual(self.store.get_settings()['self_profile']['style'], [])

    def test_malformed_review_does_not_publish_or_claim_success(self):
        ids = self.experiences()
        brain = self.brain(self.candidate(ids))
        brain.chat.side_effect = [json.dumps({'traits':[self.candidate(ids)]}), 'invalid']
        Autonomy(self.store, brain).distill_self_profile(True)
        self.assertEqual(self.store.get_settings()['growth_status']['state'], 'error')
        self.assertEqual(self.store.personality_traits(), [])

    def test_deletion_during_review_cannot_resurrect(self):
        ids = self.experiences()
        def answer(messages):
            if '公开人格隐私审查器' in messages[0]['content']:
                self.store.delete_memory(ids[0])
                return json.dumps({'reviews':[dict(index=0,trait_only=True,no_private=True,supported=True,compatible=True)]})
            return json.dumps({'traits':[self.candidate(ids)]})
        brain = Mock(chat=Mock(side_effect=answer))
        Autonomy(self.store, brain).distill_self_profile(True)
        self.assertEqual(self.store.personality_traits(), [])

    def test_unrelated_correction_preserves_manual_and_derived_traits(self):
        ids = self.experiences()
        self.store.set_setting('self_profile', {'values':['手动设定']})
        self.assertEqual(self.publish(ids), 1)
        unrelated = self.store.add_memory('fact','别人','原事实',entity_id='qq:202',scope='owner')
        self.store.update_memory(unrelated, {'content':'纠正事实'})
        self.assertEqual(self.store.get_settings()['self_profile']['style'], ['愿意坦率表达自己的判断'])
        self.assertEqual(self.store.get_settings()['self_profile']['values'], ['手动设定'])

    def test_delete_one_of_three_sources_keeps_trait_then_retracts_when_insufficient(self):
        ids = self.experiences(3)
        self.publish(ids)
        self.store.delete_memory(ids[0])
        self.assertEqual(len(self.store.personality_traits()), 1)
        self.store.delete_memory(ids[1])
        self.assertEqual(self.store.get_settings()['self_profile']['style'], [])

    def test_archiving_evidence_does_not_revoke_personality_on_unrelated_deletion(self):
        ids = self.experiences()
        self.publish(ids)
        self.store._conn.execute("UPDATE memories SET status='archived' WHERE kind='episode'")
        self.store._conn.commit()
        unrelated = self.store.add_memory('fact','别人','无关事实',scope='owner',entity_id='qq:202')
        self.store.delete_memory(unrelated)
        self.assertEqual(len(self.store.personality_traits()),1)
        event = self.store.get_memories(kind='experience')[0]['source_ids'][0]
        self.store.delete_memory(event)
        self.assertEqual(self.store.personality_traits(),[])

    def test_experience_credit_dedup_and_deleted_event(self):
        self.experiences()
        self.assertEqual(self.store.get_settings()['memory']['reflect_credit'], 10)
        event = self.store.record_episode('qq:101','私人名字','测试','owner')
        self.assertIsNotNone(self.store.record_self_experience('实际回复','owner',event))
        self.assertIsNone(self.store.record_self_experience('重复回复','owner',event))
        self.store.delete_memory(event)
        self.assertIsNone(self.store.record_self_experience('迟到回复','owner',event))

    def test_actual_reply_path_records_experience_only_after_confirmed_send(self):
        self.store.set_setting('owner_qq','101')
        brain = Mock()
        brain.chat.return_value = '我有一个不同看法'
        auto = Autonomy(self.store, brain)
        bot = QQBot('ws://localhost:8081',brain,self.store,consolidate=auto.consolidate_memory)
        bot._get_image_bytes = Mock(return_value=(None,None))
        bot._get_avatar_desc = Mock(return_value='')
        bot._send_segments = Mock()
        event = dict(message_type='private',user_id=101,message_id=1,
                     sender={'nickname':'私人名字'},message=[{'type':'text','data':{'text':'说说你的意见'}}])
        bot._on_message(event)
        experiences = self.store.get_memories(kind='experience',entity_id='self')
        self.assertEqual(len(experiences),1)
        self.assertIn('我有一个不同看法',experiences[0]['content'])
        self.assertEqual(experiences[0]['scope'],'owner')
        bot._send_segments.side_effect = RuntimeError('send rejected')
        event['message_id'] = 2
        with self.assertRaises(RuntimeError):
            bot._on_message(event)
        self.assertEqual(len(self.store.get_memories(kind='experience',entity_id='self')),1)

    def test_packaged_runtime_matches_primary_sources_and_includes_new_module(self):
        root = Path(__file__).resolve().parents[2]
        for name in ('personality.py','memory.py','memory_system.py','autonomy.py','qq_bot.py','server.py'):
            self.assertEqual((root/'fish'/name).read_bytes(), (root/'app/assets/fish'/name).read_bytes(),name)

    def test_same_episode_and_invented_evidence_do_not_count_twice(self):
        ids = self.experiences()
        evidence = self.store.get_memories(entity_id='self')
        revision = self.store.get_settings()['self_profile_revision']
        for sources in ([ids[0],ids[0]], [ids[0],99999]):
            self.assertEqual(self.store.apply_personality_candidates([self.candidate(sources)], evidence, revision), 0)

    def test_manual_edit_and_persona_edit_cancel_inflight_generation(self):
        ids = self.experiences()
        revision = self.store.get_settings()['self_profile_revision']
        evidence = self.store.get_memories(entity_id='self')
        self.store.set_setting('persona','新的底层设定')
        self.assertEqual(self.store.apply_personality_candidates([self.candidate(ids)], evidence, revision), 0)
        revision = self.store.get_settings()['self_profile_revision']
        self.store.set_setting('self_profile', {'style':['手动风格']})
        self.assertEqual(self.store.apply_personality_candidates([self.candidate(ids)], evidence, revision), 0)

    def test_revision_conflict_returns_409_without_overwriting(self):
        ids = self.experiences()
        stale = self.store.get_settings()['self_profile_revision']
        self.publish(ids)
        handler = Handler.__new__(Handler)
        handler.fish = Mock(store=self.store)
        handler.path = '/settings'
        handler._authorized = Mock(return_value=True)
        handler._read_json = Mock(return_value={'self_profile':{},'self_profile_revision':stale})
        handler._send = Mock()
        handler.do_PUT()
        self.assertEqual(handler._send.call_args.args[0],409)
        self.assertEqual(len(self.store.personality_traits()),1)

    def test_saving_editor_does_not_turn_unedited_automatic_traits_into_manual_traits(self):
        ids = self.experiences()
        self.publish(ids)
        profile = self.store.get_settings()['self_profile']
        profile['values'] = ['手动价值观']
        self.store.save_manual_profile(profile)
        self.store.delete_memory(ids[0])
        self.assertEqual(self.store.get_settings()['self_profile']['style'], [])
        self.assertEqual(self.store.get_settings()['self_profile']['values'], ['手动价值观'])

    def test_personality_and_sources_survive_restart_reset_does_not_resurrect(self):
        with tempfile.TemporaryDirectory() as folder:
            s = Store(Path(folder)/'memory.db')
            ids = [s.record_self_experience(f'自身行为{i}', 'owner') for i in range(2)]
            s.apply_personality_candidates([self.candidate(ids)], s.get_memories(entity_id='self'), s.get_settings()['self_profile_revision'])
            s.close()
            s = Store(Path(folder)/'memory.db')
            try:
                self.assertEqual(len(s.get_settings()['self_profile']['style']),1)
                evidence = s.get_memories(entity_id='self')
                revision = s.get_settings()['self_profile_revision']
                s.clear_memories()
                self.assertEqual(s.apply_personality_candidates([self.candidate(ids)], evidence, revision), 0)
                self.assertEqual(s.get_settings()['self_profile']['style'],[])
            finally:
                s.close()

    def test_revising_trait_requires_three_independent_experiences(self):
        ids = self.experiences(3)
        self.publish(ids[:2])
        old = self.store.personality_traits()[0]['id']
        evidence = self.store.get_memories(entity_id='self')
        change = self.candidate(ids[:2], '更倾向温和地表达分歧')
        change['replaces'] = [old]
        revision = self.store.get_settings()['self_profile_revision']
        self.assertEqual(self.store.apply_personality_candidates([change],evidence,revision),0)
        change['source_ids'] = ids
        self.assertEqual(self.store.apply_personality_candidates([change],evidence,revision),1)
        self.assertEqual(self.store.get_settings()['self_profile']['style'], ['更倾向温和地表达分歧'])

    def test_insufficient_evidence_explains_skip_and_does_not_call_model(self):
        brain = Mock()
        auto = Autonomy(self.store, brain)
        auto.distill_self_profile()
        auto.distill_self_profile()
        brain.chat.assert_not_called()
        self.assertEqual(self.store.get_settings()['growth_status']['state'], 'insufficient_evidence')
