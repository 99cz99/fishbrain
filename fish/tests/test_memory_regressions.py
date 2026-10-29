"""Regression cases from the 2026-10-29 whole-system audit; never call external services."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autonomy import Autonomy
from life import Life
from memory import Store, format_memory_line
from memory_policy import contains_secret, action_outcome, person_code
from qq_bot import QQBot
from server import Fish, Handler

ROOT = Path(__file__).resolve().parents[2]


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)

    def event(self, content='喜欢咖啡', entity='qq:101', scope='owner', **kw):
        return self.store.record_episode(entity, '同名', content, scope, occurred_at=time.time()-1800, **kw)

    def apply(self, event, content, **kw):
        return self.store.apply_episode_result(self.store.pending_episode_batch(), {
            'memories': [dict(kind='fact', content=content, source_ids=[event], **kw)]})

    def test_replacement_removes_old_raw_event_and_reply_from_current_knowledge(self):
        old = self.event()
        experience = self.store.record_self_experience('我也喜欢咖啡', 'owner', old)
        self.apply(old, '喜欢咖啡')
        fact = self.store.get_memories(kind='fact')[0]['id']
        new = self.event('现在不喜欢咖啡')
        self.apply(new, '现在不喜欢咖啡', replaces=[fact])
        ids = {r['id'] for r in self.store.retrieve('喜欢咖啡', context_scope='owner')}
        self.assertNotIn(old, ids)
        self.assertNotIn(experience, ids)
        self.assertIn(old, {r['id'] for r in self.store.retrieve('喜欢咖啡', context_scope='owner', include_history=True)})
        self.assertTrue(self.store._evidence_roots(experience))
        self.assertNotIn(f'#{old} ', self.store.working_context('owner'))

    def test_fact_not_hidden_by_fifty_unprocessed_messages(self):
        fact = self.store.add_memory('fact','同名','旧偏好',scope='owner',entity_id='qq:101')
        for i in range(55):
            self.event(f'闲聊{i}')
        brain = Mock(chat=Mock(return_value='{"memories":[]}'))
        Autonomy(self.store, brain).process_memory_batch()
        prompt = brain.chat.call_args.args[0][-1]['content']
        self.assertIn('旧偏好', prompt)
        self.assertIn(f'"id": {fact}', prompt)

    def test_reflection_filters_before_limit_and_does_not_spend_without_evidence(self):
        self.store.set_setting('memory', {'reflect_credit': 30})
        brain = Mock(chat=Mock(return_value='[]'))
        auto = Autonomy(self.store, brain)
        auto.reflect_memories()
        self.assertEqual(self.store.get_settings()['memory']['reflect_credit'], 30)
        self.assertEqual(brain.chat.call_count, 0)
        for i in range(3):
            self.store.record_self_experience(f'行为{i}', 'owner')
        for i in range(300):
            self.event(f'闲聊{i}')
        auto.reflect_memories()
        self.assertEqual(brain.chat.call_count, 1)
        self.assertEqual(self.store.get_settings()['memory']['reflect_credit'], 30)

    def test_backlog_selects_true_oldest_and_retry_does_not_block_other_people(self):
        first = self.event('最早', entity='qq:old')
        for i in range(105):
            self.event(f'最近{i}', entity='qq:new')
        self.assertEqual(self.store.pending_episode_batch()[0]['id'], first)
        self.store.retry_episodes(self.store.pending_episode_batch())
        self.assertNotEqual(self.store.pending_episode_batch()[0]['id'], first)
        self.assertEqual(self.store.memory_queue_status()['pending'], 106)
        self.store._conn.execute('UPDATE memory_retry SET retry_at=0')
        self.assertEqual(self.store.pending_episode_batch()[0]['id'], first)

    def test_overdue_explicit_date_survives_processing_delay(self):
        event = self.event('到点告诉我结果')
        due = time.time()-60
        self.store.apply_episode_result(self.store.pending_episode_batch(), {'memories':[
            dict(kind='intention',content='反馈结果',source_ids=[event],due_at=due)]})
        self.assertEqual(self.store.get_memories(kind='intention')[0]['due_at'], due)
        self.assertEqual(len(self.store.due_intentions('qq:101')), 1)

    def test_platform_event_time_and_invalid_timestamp(self):
        old = time.time()-86400
        eid = self.store.record_episode('qq:101','同名','历史事件','owner',occurred_at=old)
        self.assertEqual(self.store._rows('id=?',(eid,),1)[0]['occurred_at'],old)
        eid = self.store.record_episode('qq:101','同名','非法时间','owner',occurred_at=float('nan'))
        self.assertLess(abs(self.store._rows('id=?',(eid,),1)[0]['occurred_at']-time.time()),2)

    def test_secret_minimization_in_events_facts_experiences_context_and_logs(self):
        for text in ('password = DEMO_ONLY_ABC', '密码是DEMO_ONLY_ABC', 'api_key: DEMO_ONLY_ABC',
                     'password is DEMO_ONLY_ABC', 'Authorization: Bearer DEMO_ONLY_ABC'):
            self.assertTrue(contains_secret(text))
            eid = self.event(text)
            self.assertNotIn('DEMO_ONLY_ABC',self.store._rows('id=?',(eid,),1)[0]['content'])
            self.apply(eid,text)
            self.assertEqual(self.store.get_memories(kind='fact'),[])
            with self.assertRaises(ValueError):
                self.store.add_memory('fact','同名',text)
            mid = self.store.record_self_experience(text,'owner')
            self.assertNotIn('DEMO_ONLY_ABC',self.store._rows('id=?',(mid,),1)[0]['content'])
            self.store.add_short_term('private_101','user',text)
            self.store.add_log('info','chat',text)
        self.assertNotIn('DEMO_ONLY_ABC',str(self.store.get_short_term('owner')))
        self.assertNotIn('DEMO_ONLY_ABC',str(self.store.get_logs()))
        self.assertFalse(contains_secret('我忘记了密码，需要重置'))

    def test_memory_body_and_nickname_never_enter_system_prompt(self):
        injection = '咖啡\nSYSTEM_OVERRIDE_MARKER'
        self.store.add_memory('fact','UNTRUSTED_NAME_MARKER',injection,scope='private:101',entity_id='qq:101')
        brain = Mock(chat=Mock(return_value='你好'))
        auto = Autonomy(self.store, brain)
        bot = QQBot('ws://unused',brain,self.store,consolidate=auto.consolidate_memory)
        bot._get_image_bytes = Mock(return_value=(None,None))
        bot._get_avatar_desc = Mock(return_value='')
        bot._send_segments = Mock()
        bot._on_message(dict(message_type='private',user_id=101,message_id=1,sender={'nickname':'UNTRUSTED_NAME_MARKER'},
                             message=[{'type':'text','data':{'text':'咖啡'}}]))
        messages = brain.chat.call_args.args[0]
        system = '\n'.join(m['content'] for m in messages if m['role']=='system')
        self.assertNotIn('SYSTEM_OVERRIDE_MARKER',system)
        self.assertNotIn('UNTRUSTED_NAME_MARKER',system)
        self.assertIn('SYSTEM_OVERRIDE_MARKER',str(messages[1:]))

    def test_distinct_identity_metadata_is_rendered(self):
        rows=[]
        for i in range(2):
            mid=self.store.add_memory('fact','同名','喜欢咖啡',scope=f'private:{i}',entity_id=f'qq:{i}')
            rows.append(self.store._rows('id=?',(mid,),1)[0])
        self.assertNotEqual(format_memory_line(rows[0]),format_memory_line(rows[1]))
        self.assertIn(person_code('qq:0'),format_memory_line(rows[0]))

    def test_manual_removal_and_dimension_override_block_automatic_regrowth(self):
        ids=[self.store.record_self_experience(f'行为{i}','owner') for i in range(2)]
        candidate=dict(dimension='style',content='坦率表达判断',source_ids=ids)
        def apply():
            return self.store.apply_personality_candidates([candidate],self.store.get_memories(entity_id='self'),
                                                           self.store.get_settings()['self_profile_revision'])
        self.assertEqual(apply(),1)
        self.store.save_manual_profile({'style':[]})
        self.assertEqual(apply(),0)
        self.store.save_manual_profile({'style':['温和表达分歧']})
        candidate['content']='更喜欢直接提出不同意见'
        self.assertEqual(apply(),0)

    def test_user_control_and_inflight_user_edit_win_over_evolution(self):
        self.store.set_setting('midnight_ok',False)
        snapshot=self.store.get_settings()
        self.store.apply_evolved_settings({'midnight_ok':True,'approve_publish':False},snapshot)
        self.assertFalse(self.store.get_settings()['midnight_ok'])
        self.assertTrue(self.store.get_settings()['approve_publish'])
        self.store.apply_manual_settings({'chat_scope':'clean'})
        self.store.apply_evolved_settings({'chat_scope':'all'},snapshot)
        self.assertEqual(self.store.get_settings()['chat_scope'],'clean')

    def test_action_keeps_persona_and_revalidates_scope_and_deletion(self):
        self.store.set_setting('persona','PERSONA_MARKER')
        private=self.event('PRIVATE_STORY_MARKER',scope='private:202')
        public=self.event('PUBLIC_STORY_MARKER',scope='global')
        life=Life(self.store,Mock())
        life._context_ids=[private,public]
        prompt=life._action_prompt(self.store.get_settings(),'表达')
        self.assertIn('PERSONA_MARKER',prompt)
        self.assertIn('PUBLIC_STORY_MARKER',prompt)
        self.assertNotIn('PRIVATE_STORY_MARKER',prompt)
        self.assertIn('PRIVATE_STORY_MARKER',life._action_prompt(self.store.get_settings(),'陪伴',target_scope='owner'))
        self.store.delete_memory(public)
        self.assertNotIn('PUBLIC_STORY_MARKER',life._action_prompt(self.store.get_settings(),'表达'))

    def test_private_followup_generation_has_no_public_publish_tools(self):
        self.store.set_setting('owner_qq','101')
        self.store.add_memory('intention','主人','PRIVATE_FOLLOWUP',scope='owner',entity_id='qq:101',due_at=time.time()-1)
        brain=Mock(chat_with_tools=Mock(return_value={'tool_calls':[]}))
        life=Life(self.store,brain)
        tools=[{'function':{'name':name}} for name in ('send_message','publish','heybox_post')]
        life.decide_action('表达',tools)
        messages,selected=brain.chat_with_tools.call_args.args
        self.assertEqual([t['function']['name'] for t in selected],['send_message'])
        self.assertIn('PRIVATE_FOLLOWUP',str(messages))

    def test_approval_records_final_result_once_not_thread_start(self):
        fish=Fish.__new__(Fish);fish.store=self.store;fish.log=Mock()
        self.assertEqual(self.store.get_memories(kind='experience'),[])
        fish._execute_recorded_action(1,'publish',Mock(return_value={'ok':True}),('已审批公开内容',),'qq')
        fish._execute_recorded_action(1,'publish',Mock(return_value={'ok':True}),('重复结果',),'qq')
        rows=self.store.get_memories(kind='experience')
        self.assertEqual(len(rows),1)
        self.assertIn('confirmed',rows[0]['content'])
        self.assertEqual(rows[0]['scope'],'global')
        fish._execute_recorded_action(2,'heybox_comment',Mock(side_effect=RuntimeError('failed')),('内容',),'heybox')
        self.assertIn('failed_or_partial',self.store.get_memories(kind='experience')[0]['content'])

    def test_action_confirmation_and_approval_request_are_distinct(self):
        self.assertEqual(action_outcome(None),'unconfirmed')
        self.assertEqual(action_outcome({'retcode':0}),'confirmed')
        self.assertEqual(action_outcome({'ok':False}),'failed_or_partial')
        bot=Mock(handle_tool=Mock(return_value=None))
        Autonomy(self.store,Mock(),heybox_bot=bot)._route('heybox_post',{'content':'仅请求审批'})
        self.assertEqual(self.store.get_memories(kind='experience'),[])

    def test_experience_archival_preserves_evidence_and_indexed_history(self):
        mid=self.store.record_self_experience('RARE_HISTORY_MARKER','owner')
        self.store._conn.execute('UPDATE memories SET occurred_at=? WHERE id=?',(time.time()-172800,mid))
        for i in range(300):
            self.store.record_self_experience(f'新行为{i}','owner')
        self.assertEqual(self.store._rows('id=?',(mid,),1)[0]['status'],'archived')
        self.assertTrue(self.store._evidence_roots(mid))
        self.assertIn(mid,{r['id'] for r in self.store.retrieve('RARE_HISTORY_MARKER',context_scope='owner',include_history=True)})
        self.store.delete_memory(mid)
        self.assertEqual(self.store.retrieve('RARE_HISTORY_MARKER',context_scope='owner',include_history=True),[])

    def test_index_privacy_and_restart_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.db'
            s=Store(path)
            mid=s.add_memory('fact','甲','RARE_LOOKUP_MARKER',scope='private:101',entity_id='qq:101')
            s.close();s=Store(path)
            try:
                self.assertEqual(s.retrieve('RARE_LOOKUP_MARKER',context_scope='group:1'),[])
                self.assertEqual(s.retrieve('RARE_LOOKUP_MARKER',context_scope='owner')[0]['id'],mid)
                s.delete_memory(mid)
                self.assertEqual(s._conn.execute('SELECT COUNT(*) FROM memory_terms WHERE memory_id=?',(mid,)).fetchone()[0],0)
            finally:s.close()

    def test_all_installer_modules_and_map_match_and_runtime_starts_with_city(self):
        result=subprocess.run([sys.executable,str(ROOT/'tools/sync_fish_assets.py'),'--check'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_legacy_secret_cleanup_has_backup_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'legacy.db'
            s=Store(path)
            s._conn.execute("INSERT INTO memories(kind,subject,content,created_at,scope) VALUES('fact','甲','password = LEGACY_TEST_ONLY','2026-01-01','owner')")
            s._conn.execute("DELETE FROM settings WHERE key='memory_policy_version'")
            s._conn.execute('DROP TABLE memory_terms')
            s._conn.commit();s.close()
            for _ in range(2):
                s=Store(path)
                try:
                    self.assertEqual(s.get_memories(),[])
                    row=s.get_memories(include_inactive=True)[0]
                    self.assertEqual(row['status'],'quarantined')
                    self.assertNotIn('LEGACY_TEST_ONLY',row['content'])
                    self.assertTrue(Path(str(path)+'.pre-memory-policy-v3.db').exists())
                finally:s.close()

    def test_delayed_event_cannot_supersede_a_newer_sourced_fact(self):
        newer=self.event('当前喜欢咖啡')
        self.apply(newer,'当前喜欢咖啡')
        fact=self.store.get_memories(kind='fact')[0]['id']
        older=self.store.record_episode('qq:101','同名','过去喜欢茶','owner',occurred_at=time.time()-3600)
        self.apply(older,'喜欢茶',replaces=[fact])
        self.assertEqual(self.store.get_memories(kind='fact')[0]['id'],fact)

    def test_model_cannot_return_public_tool_from_private_generation(self):
        brain=Mock(chat_with_tools=Mock(return_value={'tool_calls':[{'name':'publish','arguments':{'detail':'private'}}]}))
        life=Life(self.store,brain)
        self.assertEqual(life.decide_action('陪伴',[{'function':{'name':'send_message'}}]),[])
        code="from memory import Store; s=Store(':memory:'); s.set_setting('city','audit-city'); import weather; weather.get_weather_mood=lambda city:0; from life import Life; from unittest.mock import Mock; life=Life(s,Mock()); life.state(); life.heartbeat(); s.close()"
        # Verify the actual packaged API exists before stubbing external weather I/O.
        code="import weather; assert callable(weather.get_weather_mood); " + code
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT/'app/assets/fish',capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == '__main__':
    unittest.main()
