import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory import Store, SCHEMA
from autonomy import Autonomy
from delivery import SendUnconfirmedError
from qq_bot import QQBot


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)

    def episode(self, text='喜欢喝茶', entity='qq:101', scope='private:101', key=None):
        mid = self.store.record_episode(entity, '小鱼', text, scope, event_key=key)
        self.store._conn.execute('UPDATE memories SET occurred_at=? WHERE id=?', (time.time()-1800, mid))
        self.store._conn.commit()
        return mid

    def fact(self, text='喜欢喝茶', entity='qq:101', scope='private:101', sources=None, **kwargs):
        return self.store.add_memory('fact', '小鱼', text, entity_id=entity, scope=scope, source_ids=sources, **kwargs)

    def test_reflection_never_widens_privacy_or_changes_entity(self):
        a, b = self.fact(), self.fact('每天泡茶')
        mid = self.store.add_reflection('可能喜爱茶文化', [a, b])
        row = self.store.get_memories(kind='belief')[0]
        self.assertEqual(row['scope'], 'private:101')
        self.assertEqual(row['entity_id'], 'qq:101')
        self.assertEqual(row['subject'], '小鱼')
        self.assertNotIn(mid, [r['id'] for r in self.store.retrieve('茶', context_scope='group:5')])
        self.assertEqual(len(self.store.get_memories(kind='fact')), 2)
        other = self.fact('其他人的茶', entity='qq:202')
        self.assertIsNone(self.store.add_reflection('混淆', [a, other]))

    def test_unknown_scope_fails_closed_and_no_irrelevant_padding(self):
        self.fact('abcdefgh', importance=10)
        self.assertEqual(self.store.retrieve('完全不相关', context_scope='owner'), [])
        self.assertEqual(self.store.retrieve('abcdefgh', context_scope='oops'), [])

    def test_same_nickname_different_people_and_rename(self):
        a = self.fact()
        b = self.fact(entity='qq:202')
        self.store.record_episode('qq:101', '新名字', '你好', 'private:101')
        rows = {r['id']: r for r in self.store.get_memories()}
        self.assertEqual(rows[a]['subject'], '新名字')
        self.assertEqual(rows[b]['subject'], '小鱼')

    def test_batch_update_keeps_history_and_invalidates_belief(self):
        a, b = self.fact(), self.fact('每天泡茶')
        belief = self.store.add_reflection('可能喜欢茶', [a, b])
        event = self.episode('现在不喝茶了')
        self.store.apply_episode_result(self.store.pending_episode_batch(), {'memories': [
            {'kind': 'fact', 'content': '现在不喝茶', 'source_ids': [event], 'replaces': [a]}]})
        rows = {r['id']: r for r in self.store.get_memories(include_inactive=True)}
        self.assertEqual(rows[a]['status'], 'superseded')
        self.assertEqual(rows[belief]['status'], 'superseded')
        self.assertEqual(rows[b]['status'], 'active')
        self.assertTrue(any(r['content']=='现在不喝茶' for r in rows.values()))

    def test_batch_cannot_replace_another_person_or_invent_sources(self):
        other = self.fact(entity='qq:202')
        event = self.episode()
        made = self.store.apply_episode_result(self.store.pending_episode_batch(), {'memories': [
            {'kind': 'fact', 'content': '错误来源', 'source_ids': [999]},
            {'kind': 'fact', 'content': '新的事实', 'source_ids': [event], 'replaces': [other]}]})
        self.assertEqual(made, 1)
        self.assertIn(other, [r['id'] for r in self.store.get_memories()])

    def test_forget_clears_ancestors_descendants_context_and_replay(self):
        event = self.episode(key='test-message')
        a = self.fact(sources=[event])
        b = self.fact('喜欢茶文化', sources=[event])
        belief = self.store.add_reflection('喜爱茶', [a, b])
        self.store.add_short_term('private_101', 'user', '喜欢喝茶')
        self.store.delete_memory(belief)
        self.assertEqual(self.store.get_memories(include_inactive=True), [])
        self.assertEqual(self.store.get_short_term('private:101'), [])
        self.assertEqual(self.store.working_context('private:101'), '')
        self.assertEqual(self.episode(key='test-message'), event)
        self.assertEqual(self.store.pending_episode_batch(), [])

    def test_deleted_inflight_batch_cannot_resurrect(self):
        event = self.episode()
        batch = self.store.pending_episode_batch()
        self.store.delete_memory(event)
        self.assertEqual(self.store.apply_episode_result(batch, {'memories': [
            {'kind': 'fact', 'content': '喜欢茶', 'source_ids': [event]}]}), 0)

    def test_correction_preserves_scope_and_does_not_keep_old_context(self):
        event = self.episode()
        a = self.fact(sources=[event])
        new = self.store.update_memory(a, {'content': '已经戒茶'})
        rows = self.store.get_memories()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], new)
        self.assertEqual(rows[0]['scope'], 'private:101')
        self.assertEqual(self.store.working_context('private:101'), '')

    def test_atomic_rollback_when_later_insert_fails(self):
        event = self.episode()
        batch = self.store.pending_episode_batch()
        insert = self.store._insert_memory
        calls = 0
        def faulty(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('disk full')
            return insert(*args, **kwargs)
        with patch.object(self.store, '_insert_memory', side_effect=faulty):
            with self.assertRaises(RuntimeError):
                self.store.apply_episode_result(batch, {'memories': [
                    {'kind':'fact', 'content':text, 'source_ids':[event]} for text in ('甲', '乙')]})
        self.assertEqual(self.store.get_memories(kind='fact'), [])
        self.assertEqual(len(self.store.pending_episode_batch()), 1)

    def test_intention_due_claim_cancel_and_unknown_send(self):
        self.store.set_setting('owner_qq', '101')
        mid = self.store.add_memory('intention', '主人', '询问结果', scope='owner', entity_id='qq:101', due_at=time.time()-10)
        bot = Mock()
        bot.send_text.side_effect = SendUnconfirmedError('no ack')
        auto = Autonomy(self.store, Mock(), qq_bot=bot)
        auto._do_talk('有结果了吗', mid)
        auto._do_talk('有结果了吗', mid)
        self.assertEqual(bot.send_text.call_count, 1)
        self.assertEqual(self.store.due_intentions('qq:101'), [])
        self.assertIn('unconfirmed', self.store.get_memories(kind='experience')[0]['content'])
        other = self.store.add_memory('intention', '主人', '另一个事项', scope='owner', entity_id='qq:101', due_at=time.time()-10)
        self.store.update_memory(other, {'status':'cancelled'})
        self.assertFalse(self.store.claim_intention(other, 'qq:101'))

    def test_recent_interaction_and_wrong_owner_prevent_followup(self):
        mid = self.store.add_memory('intention', '主人', '结果', scope='owner', entity_id='qq:101', due_at=time.time()-10)
        self.assertFalse(self.store.claim_intention(mid, 'qq:202'))
        self.store.record_episode('qq:101', '主人', '刚说过', 'owner')
        self.assertEqual(self.store.due_intentions('qq:101'), [])
        self.assertFalse(self.store.claim_intention(mid, 'qq:101'))

    def test_new_and_pinned_memories_protected_and_prune_committed(self):
        for i in range(302):
            self.fact(f'事件 {i}', occurred_at=time.time()-172800)
        self.assertEqual(len(self.store.get_memories(kind='fact', limit=1000)), 300)
        self.assertEqual(len(self.store.get_memories(kind='fact', limit=1000, include_inactive=True)), 302)

    def test_migration_quarantines_legacy_reflection_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'memory.db'
            db = sqlite3.connect(path)
            db.executescript(SCHEMA)
            db.execute("INSERT INTO memories(kind,subject,content,created_at) VALUES('fact','self','私聊洞察','2020-01-01 00:00:00')")
            db.execute("INSERT INTO settings VALUES('self_profile',?)", (json.dumps({'likes':['private']}),))
            db.commit()
            db.close()
            for _ in range(2):
                store = Store(path)
                self.assertEqual(store.get_memories(), [])
                self.assertEqual(store.get_memories(include_inactive=True)[0]['status'], 'quarantined')
                self.assertEqual(store.get_settings()['self_profile']['likes'], [])
                store.close()

    def test_owner_in_group_does_not_get_private_context(self):
        self.assertEqual(QQBot._memory_scope(None, 'group', 101, 5, {'owner_qq':'101'}), 'group:5')

    def test_enqueue_never_calls_llm_and_background_uses_original_evidence(self):
        brain = Mock()
        auto = Autonomy(self.store, brain)
        event = auto.consolidate_memory('小鱼', '明天告诉你结果', scope='private:101', entity_id='qq:101')
        brain.chat.assert_not_called()
        self.store._conn.execute('UPDATE memories SET occurred_at=?', (time.time()-60,))
        self.store._conn.commit()
        brain.chat.return_value = json.dumps({'memories':[{'kind':'fact','content':'等待结果','source_ids':[event]}]})
        auto.process_memory_batch()
        self.assertEqual(len(self.store.get_memories(kind='fact')), 1)
        self.assertEqual(self.store.pending_episode_batch(), [])

    def test_current_event_excluded_from_both_context_paths(self):
        event = self.episode()
        self.assertEqual(self.store.retrieve('喜欢喝茶', context_scope='private:101', exclude_ids=(event,)), [])
        self.assertEqual(self.store.working_context('private:101', exclude_ids=(event,)), '')

    def test_invalid_model_output_does_not_consume_events(self):
        self.episode()
        with self.assertRaises(ValueError):
            self.store.apply_episode_result(self.store.pending_episode_batch(), {})
        self.assertEqual(len(self.store.pending_episode_batch()), 1)

    def test_private_belief_text_is_not_directly_published_as_personality(self):
        a = self.store.add_memory('experience', 'self', '尝试说话', entity_id='self', scope='owner')
        b = self.store.add_memory('experience', 'self', '发送未确认', entity_id='self', scope='owner')
        self.store.add_reflection('可能关心别人', [a,b])
        brain = Mock()
        brain.chat.return_value = json.dumps({'traits': []})
        Autonomy(self.store, brain).distill_self_profile(force=True)
        self.assertEqual(self.store.get_settings()['self_profile']['values'], [])
        self.assertEqual(self.store.personality_traits(), [])

    def test_pending_events_and_claim_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'store.db'
            store = Store(path)
            event = store.record_episode('qq:101', '测试', '新的事件', 'owner')
            store._conn.execute('UPDATE memories SET occurred_at=?', (time.time()-1800,))
            store._conn.commit()
            mid = store.add_memory('intention', '测试', '等待结果', scope='owner', entity_id='qq:101', due_at=time.time()-1)
            self.assertTrue(store.claim_intention(mid, 'qq:101'))
            store.close()
            store = Store(path)
            self.assertEqual(store.pending_episode_batch()[0]['id'], event)
            self.assertFalse(store.claim_intention(mid, 'qq:101'))
            store.close()
