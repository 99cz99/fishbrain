"""Cross-module regression tests; no network, device or real model calls."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import websocket
except ImportError:
    module = Mock()
    module.WebSocketException = type('WebSocketException', (Exception,), {})
    module.WebSocketTimeoutException = type('WebSocketTimeoutException', (module.WebSocketException,), {})
    sys.modules['websocket'] = module
from autonomy import Autonomy
from brain import Brain
from life import Life
from memory import Store
from qq_bot import QQBot, build_system_prompt
from server import Fish


class LinkConflicts(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.qq = Mock()
        self.qq.get_tools.return_value = [{'function': {'name':'get_login_info'}}]
        self.hb = Mock()
        self.hb.get_tools.return_value = [{'function': {'name': n}} for n in
            ('heybox_sign','heybox_feeds','heybox_comments','heybox_post','heybox_comment')]
        self.auto = Autonomy(self.store, Mock(), self.qq, self.hb)

    def test_disabled_platform_filters_and_rechecks_execution(self):
        self.store.set_setting('enabled_platforms', [])
        self.assertEqual([t['function']['name'] for t in self.auto.select_tools('成长')], ['reflect'])
        self.auto._route('heybox_sign', {})
        self.auto._route('send_message', {'text':'hello'})
        self.hb.handle_tool.assert_not_called()
        self.qq.send_text.assert_not_called()
        bot = QQBot('', Mock(), self.store)
        bot._on_message({'message_type':'private'})
        with self.assertRaises(RuntimeError):
            bot.send_msg(1, [])

    def test_quiet_hours_cover_all_owner_contact(self):
        self.store.set_settings({'owner_qq':'101','midnight_ok':False})
        with patch('autonomy.time.localtime', return_value=Mock(tm_hour=2)):
            self.auto._do_talk('hello'); self.auto._do_poke(); self.auto._do_like()
        self.qq.send_text.assert_not_called()
        self.qq.send_poke.assert_not_called()
        self.qq.send_like.assert_not_called()
        with patch('autonomy.time.localtime', return_value=Mock(tm_hour=8)):
            self.auto._do_poke()
        self.qq.send_poke.assert_called_once()

    def test_json_provider_plan_is_validated(self):
        tools = [{'function': {'name':'act','parameters': {'type':'object','properties': {
            'n': {'type':'integer'}}, 'required':['n']}}}]
        for provider in ('anthropic','gemini'):
            brain = Brain(lambda: {'provider':provider})
            brain.chat = Mock(return_value='{"content":"","tool_calls":[{"name":"act","arguments":{"n":2}}]}')
            self.assertEqual(brain.chat_with_tools([],tools)['tool_calls'][0]['arguments'], {'n':2})
            for raw in ('ordinary text','{"tool_calls":[{"name":"unknown","arguments":{}}]}',
                        '{"tool_calls":[{"name":"act","arguments":{"n":true}}]}',
                        '{"tool_calls":[{"name":"act","arguments":{}}]}'):
                brain.chat.return_value = raw
                with self.assertRaises(ValueError):
                    brain.chat_with_tools([],tools)

    def test_read_followup_can_comment_but_private_reads_cannot_publish(self):
        for tool,args,expected,forbidden in (
            ('heybox_feeds',{},'heybox_comment',None),
            ('get_group_msg_history',{'group_id':5},'send_message','publish')):
            self.auto.life.heartbeat = Mock(return_value={'drive':'好奇'})
            self.auto.life.decide_action = Mock(side_effect=[{'tool_calls':[{'name':tool,'arguments':args}]},{'tool_calls':[]}])
            self.auto._route = Mock(return_value={'synthetic':'result'})
            self.auto._heartbeat_once()
            call = self.auto.life.decide_action.call_args
            names = [t['function']['name'] for t in call.args[1]]
            self.assertIn(expected,names)
            if forbidden:
                self.assertNotIn(forbidden,names)
                self.assertNotIn('heybox_comment',names)
            self.assertIn('synthetic',call.kwargs['extra_context'])

    def test_approval_execution_result_is_queryable_without_replay(self):
        f = Fish.__new__(Fish); f.store = self.store; f.log = Mock()
        for result,state in (({'ok':False},'failed_or_partial'),(None,'unconfirmed'),({'ok':True},'confirmed')):
            aid = self.store.add_approval('publish','approved text')
            self.store.set_approval_status(aid,'approved')
            f._execute_recorded_action(aid,'publish',Mock(return_value=result),('text',),'qq')
            self.assertEqual(self.store.get_recent_approvals()[0]['execution_state'],state)
            self.assertFalse(self.store.set_approval_status(aid,'approved'))

    def test_restart_marks_running_action_unknown_without_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'test.db'
            store = Store(path)
            aid = store.add_approval('publish','text')
            store.set_approval_status(aid,'approved'); store.set_execution_state(aid,'running'); store.close()
            store = Store(path)
            try:
                self.assertEqual(store.get_recent_approvals()[0]['execution_state'],'unconfirmed')
                self.assertFalse(store.set_approval_status(aid,'approved'))
            finally:
                store.close()

    def test_intention_edit_preserves_schedule_and_attempt(self):
        mid = self.store.add_memory('intention','owner','meeting',scope='owner',entity_id='qq:101',due_at=time.time()-1)
        self.assertTrue(self.store.claim_intention(mid,'qq:101'))
        replacement = self.store.update_memory(mid,{'content':'revised meeting'})
        row = self.store._rows('id=?',(replacement,),1)[0]
        self.assertEqual(row['kind'],'intention')
        self.assertGreater(row['due_at'],0)
        self.assertGreater(row['last_attempt'],0)
        self.assertEqual(self.store.due_intentions('qq:101'),[])

    def test_old_event_does_not_rewind_current_mood(self):
        now = time.time()
        self.store.set_life_state({'valence':.8,'arousal':.5,'mood_at':now})
        self.auto._nudge_mood({'desirability':1,'intensity':1},now-86400)
        self.assertGreaterEqual(self.store.get_setting_raw('life_state')['mood_at'],now)
        self.assertGreater(self.store.get_life_state()['valence'],.79)

    def test_heartbeat_cannot_overwrite_appraisal_wake_or_reset(self):
        for change in ('appraisal','wake','reset'):
            self.store.set_life_state({'valence':0,'dominance':0,'mood_at':time.time()})
            brain = Mock(); life = Life(self.store,brain)
            def during_model(*args,**kwargs):
                if change == 'appraisal':
                    self.auto._nudge_mood({'desirability':1,'intensity':1,'dominance':1},time.time())
                elif change == 'wake':
                    life.wake()
                else:
                    self.store.clear_memories(); self.store.set_life_state({})
                return {'tool_calls':[{'name':'update_mood','arguments':{'arousal':.6}},
                                      {'name':'set_drive','arguments':{'drive':'表达'}}]}
            brain.chat_with_tools.side_effect = during_model
            with patch('life.weather.get_weather',return_value=''):
                result = life.heartbeat()
            self.assertEqual(result['drive'],'none')
            raw = self.store.get_setting_raw('life_state')
            if change == 'appraisal':
                self.assertAlmostEqual(raw['dominance'],.4,places=6)
            elif change == 'reset':
                self.assertEqual(raw,{})
            else:
                self.assertLessEqual(raw['next_wake_at'],time.time())

    def test_human_correction_rejects_old_but_accepts_new_explicit_event(self):
        old = self.store.record_episode('qq:101','test','old preference','owner',occurred_at=time.time()-3600)
        original = self.store.add_memory('fact','test','initial',scope='owner',entity_id='qq:101')
        manual = self.store.update_memory(original,{'content':'human correction'})
        proposal = lambda sid: {'memories':[dict(kind='fact',content='new preference',source_ids=[sid],replaces=[manual])]}
        self.assertEqual(self.store.apply_episode_result(self.store.pending_episode_batch(),proposal(old)),0)
        new = self.store.record_episode('qq:101','test','new preference','owner')
        self.assertEqual(self.store.apply_episode_result(self.store._rows('id=?',(new,),1),proposal(new)),1)

    def test_deleted_batch_cannot_change_mood(self):
        self.store.record_episode('qq:101','test','event','owner',occurred_at=time.time()-60)
        def after_delete(messages):
            self.store.clear_memories(); self.store.set_life_state({})
            return '{"schema_version":2,"memories":[],"appraisal":{"desirability":1,"intensity":1}}'
        self.auto.brain.chat = Mock(side_effect=after_delete)
        self.auto.process_memory_batch()
        self.assertEqual(self.store.get_setting_raw('life_state'),{})

    def test_evolution_reports_real_changes(self):
        self.store.apply_manual_settings({'chat_scope':'clean','midnight_ok':False})
        self.auto.brain.chat.return_value = '{"chat_scope":"all","midnight_ok":true}'
        self.auto.evolve()
        result = self.store.get_setting_raw('evolution_result')
        self.assertEqual(result['applied'],{})
        self.assertIn('chat_scope',result['skipped'])
        self.assertEqual(json.loads(self.store.get_logs('growth',limit=1)[0]['message']),result)

    def test_sticker_protocol_is_static_and_labels_are_data(self):
        prompt = build_system_prompt(self.store.get_settings(),stickers=['UNTRUSTED_LABEL'])
        self.assertIn('[表情包:',prompt)
        self.assertNotIn('UNTRUSTED_LABEL',prompt)

    def test_output_constraints_reach_chat_and_autonomous_messages(self):
        settings = self.store.get_settings(); life = Life(self.store,Mock())
        clean = life._action_prompt(dict(settings,chat_scope='clean'),'陪伴',target_scope='owner')
        unrestricted = life._action_prompt(dict(settings,chat_scope='all'),'陪伴',target_scope='owner')
        self.assertNotEqual(clean,unrestricted)
        self.assertIn('不说脏话',clean)
        self.assertIn('不说脏话',build_system_prompt(dict(settings,chat_scope='clean')))

    def test_legacy_approval_migration_backs_up_and_preserves_rows(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'legacy.db'
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE approvals(id INTEGER PRIMARY KEY,kind TEXT,summary TEXT,status TEXT,created_at TEXT)')
            conn.execute("INSERT INTO approvals VALUES(1,'publish','old text','approved','2026-01-01')")
            conn.commit(); conn.close()
            store = Store(path)
            try:
                self.assertEqual(store.get_recent_approvals()[0]['execution_state'],'unconfirmed')
                self.assertEqual(store.get_recent_approvals()[0]['summary'],'old text')
                self.assertTrue(Path(str(path)+'.pre-link-v1.db').exists())
            finally:
                store.close()

    def test_disable_after_plan_stops_execution_and_approval(self):
        f = Fish.__new__(Fish); f.store = self.store; f.log = Mock()
        aid = self.store.add_approval('publish','text')
        self.store.set_approval_status(aid,'approved')
        self.store.set_setting('enabled_platforms',[])
        send = Mock()
        f._execute_recorded_action(aid,'publish',send,('text',),'qq')
        send.assert_not_called()
        self.assertEqual(self.store.get_recent_approvals()[0]['execution_state'],'failed_or_partial')

    def test_recent_history_limit_never_hides_pending_approvals(self):
        for i in range(102):
            self.store.add_approval('publish',str(i))
        self.assertEqual(len(self.store.get_recent_approvals()),102)
        aid = self.store.add_approval('publish','completed')
        self.store.set_approval_status(aid,'approved')
        self.assertEqual(len(self.store.get_recent_approvals(limit=0)),102)

    def test_edited_constraints_cancel_inflight_action_plan(self):
        def plan(*args,**kwargs):
            self.store.apply_manual_settings({'chat_scope':'clean'})
            return {'tool_calls':[{'name':'send_message','arguments':{'text':'old plan'}}]}
        brain = Mock(chat_with_tools=Mock(side_effect=plan))
        self.assertTrue(Life(self.store,brain).decide_action('陪伴',[{'function':{'name':'send_message'}}])['cancelled'])


if __name__ == '__main__':
    unittest.main()
