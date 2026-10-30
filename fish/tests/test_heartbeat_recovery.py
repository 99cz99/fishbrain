"""Exercise heartbeat recovery without contacting providers or sending messages."""
import io
import http.client
from contextlib import closing
from pathlib import Path
import sys
import ssl
import socket
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autonomy import Autonomy
from brain import Brain, LLMTransportError
from life import Life
from memory import Store


class HeartbeatRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)

    def test_transport_failures_are_classified_without_leaking_urls(self):
        for error in (urllib.error.URLError(TimeoutError('timed out')),
                      TimeoutError('timed out'), ConnectionResetError('reset'),
                      urllib.error.URLError('https://private.example/secret')):
            with self.subTest(error=type(error).__name__), patch(
                    'brain.urllib.request.urlopen', side_effect=error) as request:
                with self.assertRaises(LLMTransportError) as caught:
                    Brain()._post_raw('https://example.test', {}, {})
                self.assertIn('60 秒', str(caught.exception))
                self.assertNotIn('secret', str(caught.exception))
                request.assert_called_once()

    def test_read_timeout_is_also_classified(self):
        response = Mock()
        response.read.side_effect = TimeoutError('timed out')
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with patch('brain.urllib.request.urlopen', return_value=context):
            with self.assertRaises(LLMTransportError):
                Brain()._post_raw('https://example.test', {}, {})

    def test_interrupted_response_reads_are_transport_failures(self):
        for error in (ssl.SSLEOFError('TLS EOF'), ssl.SSLZeroReturnError('TLS closed'),
                      http.client.IncompleteRead(b'partial', 20)):
            with self.subTest(error=type(error).__name__):
                response = Mock()
                response.read.side_effect = error
                context = Mock()
                context.__enter__ = Mock(return_value=response)
                context.__exit__ = Mock(return_value=False)
                with patch('brain.urllib.request.urlopen', return_value=context) as request:
                    with self.assertRaises(LLMTransportError):
                        Brain()._post_raw('https://example.test', {}, {})
                request.assert_called_once()

    def test_real_brain_routes_timeout_to_heartbeat_recovery_for_all_providers(self):
        for provider in ('openai', 'anthropic', 'gemini'):
            with self.subTest(provider=provider):
                brain = Brain(lambda: {'provider': provider, 'base_url': 'https://example.test'})
                life = Life(self.store, brain)
                with patch.object(life, '_prompt', return_value='test'), patch(
                        'brain.urllib.request.urlopen', side_effect=urllib.error.URLError(
                            TimeoutError('timed out'))) as request:
                    result = life.heartbeat()
                self.assertTrue(result['failed'])
                self.assertEqual(result['next_wake'], 10)
                request.assert_called_once()

    def test_concurrent_wake_is_preserved_and_not_logged_as_idle(self):
        for error in (None, LLMTransportError('LLM 请求超时')):
            with self.subTest(failed=bool(error)):
                auto = Autonomy(self.store, Mock())
                def during_model(*args, **kwargs):
                    auto.life.wake()
                    if error:
                        raise error
                    return {'tool_calls': []}
                auto.brain.chat_with_tools.side_effect = during_model
                with patch.object(auto.life, '_prompt', return_value='test'), patch.object(
                        self.store, 'add_log') as log:
                    auto._heartbeat_once()
                self.assertFalse(any('只想待着' in call.args[2] for call in log.call_args_list))
                self.assertFalse(any('分钟后' in call.args[2] for call in log.call_args_list))
                self.assertLessEqual(auto.life.next_wake_at(), time.time())

    def test_failed_request_cannot_overwrite_appraisal_or_memory_reset(self):
        for change in ('appraisal', 'reset'):
            with self.subTest(change=change):
                self.store.set_life_state({'valence': 0.1})
                auto = Autonomy(self.store, Mock())
                def during_model(*args, **kwargs):
                    if change == 'reset':
                        self.store.clear_memories()
                        self.store.set_life_state({})
                    else:
                        auto._nudge_mood({'desirability': 1, 'intensity': 1}, time.time())
                    raise LLMTransportError('LLM 请求超时')
                auto.brain.chat_with_tools.side_effect = during_model
                with patch.object(auto.life, '_prompt', return_value='test'):
                    result = auto.life.heartbeat()
                raw = self.store.get_setting_raw('life_state')
                if change == 'reset':
                    self.assertTrue(result['cancelled'])
                    self.assertEqual(raw, {})
                else:
                    self.assertTrue(result['failed'])
                    self.assertFalse(result.get('cancelled', False))
                    self.assertGreater(raw['valence'], 0.1)
                    self.assertGreater(raw['next_wake_at'], time.time() + 590)
                    self.assertGreater(auto.life.next_wake_at(), time.time())

    def test_recovery_schedule_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'fish.db'
            with closing(Store(str(path))) as store:
                life = Life(store, Mock(chat_with_tools=Mock(side_effect=LLMTransportError('timeout'))))
                with patch.object(life, '_prompt', return_value='test'):
                    life.heartbeat()
                expected = life.next_wake_at()
            with closing(Store(str(path))) as reopened:
                self.assertEqual(reopened.get_life_state()['next_wake_at'], expected)

    def test_partial_invalid_plan_does_not_commit_mood_or_trigger_action(self):
        self.store.set_life_state({'valence': 0.2})
        before = self.store.get_setting_raw('life_state').copy()
        brain = Mock()
        brain.chat_with_tools.return_value = {'tool_calls': [
            {'name': 'update_mood', 'arguments': {'valence': 0.9}},
            {'name': 'set_drive', 'arguments': {'drive': '表达'}}, None]}
        life = Life(self.store, brain)
        with patch.object(life, '_prompt', return_value='test'):
            result = life.heartbeat()
        self.assertTrue(result['failed'])
        self.assertEqual(result['drive'], 'none')
        self.assertEqual(self.store.get_setting_raw('life_state')['valence'], before['valence'])

    def test_http_errors_keep_existing_behavior(self):
        error = urllib.error.HTTPError('https://example.test', 401, 'Unauthorized',
                                       {}, io.BytesIO(b'{"error":{"message":"invalid key"}}'))
        with patch('brain.urllib.request.urlopen', side_effect=error) as request:
            with self.assertRaises(RuntimeError) as caught:
                Brain()._post_raw('https://example.test', {}, {})
        self.assertNotIsInstance(caught.exception, LLMTransportError)
        self.assertIn('HTTP 401', str(caught.exception))
        request.assert_called_once()

    def test_certificate_failure_uses_error_level_and_long_interval(self):
        brain = Brain(lambda: {'base_url': 'https://example.test'})
        life = Life(self.store, brain)
        with patch.object(life, '_prompt', return_value='test'), patch(
                'brain.urllib.request.urlopen', side_effect=urllib.error.URLError(
                    ssl.SSLCertVerificationError('certificate verify failed'))):
            result = life.heartbeat()
        self.assertEqual(result['next_wake'], 60)
        log = self.store.get_logs('life', limit=1)[0]
        self.assertEqual(log['level'], 'error')
        self.assertIn('SSLCertVerificationError', log['message'])

    def test_permanent_and_temporary_transport_errors_have_different_recovery(self):
        for error, retryable in (
                (ssl.SSLCertVerificationError('certificate'), False),
                (ssl.SSLError('protocol mismatch'), False),
                (urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, 'host not found')), False),
                (urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, 'temporary DNS failure')), True)):
            with self.subTest(error=type(error).__name__), patch('brain.urllib.request.urlopen', side_effect=error):
                with self.assertRaises(LLMTransportError) as caught:
                    Brain()._post_raw('https://example.test', {}, {})
                self.assertEqual(caught.exception.retryable, retryable)

    def test_action_failure_reschedules_without_replaying_tools(self):
        for following in (False, True):
            with self.subTest(following=following):
                brain = Mock()
                auto = Autonomy(self.store, brain)
                self.store.set_life_state({'next_wake_at': time.time()+7200, 'valence': 0.1})
                auto.life.heartbeat = Mock(return_value={'drive': '好奇'})
                auto.select_tools = Mock(return_value=[{'function': {'name': 'heybox_feeds'}}])
                auto._route = Mock(return_value={'read': 'result'})
                def fail(*args, **kwargs):
                    auto._nudge_mood({'desirability': 1, 'intensity': 1}, time.time())
                    raise LLMTransportError('timeout')
                brain.chat_with_tools.side_effect = ([{'tool_calls': [
                    {'name': 'heybox_feeds', 'arguments': {}}]}, LLMTransportError('timeout')]
                    if following else fail)
                with patch.object(auto.life, '_action_prompt', return_value='test'):
                    auto._heartbeat_once()
                self.assertGreater(auto.life.next_wake_at(), time.time()+590)
                self.assertLess(auto.life.next_wake_at(), time.time()+610)
                self.assertEqual(auto._route.call_count, 1 if following else 0)
                self.assertFalse(any('但没动手' in row['message'] for row in self.store.get_logs('life')))

    def test_action_noop_and_cancellation_are_distinct(self):
        brain = Mock(chat_with_tools=Mock(return_value={'tool_calls': []}))
        life = Life(self.store, brain)
        with patch.object(life, '_action_prompt', return_value='test'):
            self.assertEqual(life.decide_action('好奇', []), {'tool_calls': []})
            def wake(*args, **kwargs):
                life.wake()
                return {'tool_calls': []}
            brain.chat_with_tools.side_effect = wake
            result = life.decide_action('好奇', [])
        self.assertTrue(result['cancelled'])

    def test_repeated_appraisal_cannot_bypass_retry_delay(self):
        auto = Autonomy(self.store, Mock())
        self.store.set_life_state({'next_wake_at': time.time()-60})
        def fail(*args, **kwargs):
            auto._nudge_mood({'desirability': 1, 'intensity': 1}, time.time())
            raise LLMTransportError('timeout')
        auto.brain.chat_with_tools.side_effect = fail
        with patch.object(auto.life, '_prompt', return_value='test'):
            for _ in range(3):
                if auto.life.next_wake_at() <= time.time():
                    auto._heartbeat_once()
        auto.brain.chat_with_tools.assert_called_once()

    def test_explicit_wake_at_same_timestamp_still_cancels_failure(self):
        life = Life(self.store, Mock())
        with patch('life.time.time', return_value=1000):
            life.wake()
            snapshot = life._schedule_snapshot()
            life.wake()
            result = life._failure(LLMTransportError('timeout'), '心跳', snapshot)
        self.assertTrue(result['cancelled'])
        self.assertEqual(life.next_wake_at(), 1000)

    def test_failed_heartbeat_schedules_recovery_from_request_completion(self):
        brain = Mock(chat_with_tools=Mock(side_effect=LLMTransportError('LLM 请求超时')))
        life = Life(self.store, brain)
        with patch.object(life, '_prompt', return_value='test'):
            def fail(*args, **kwargs):
                # Simulate a slow request; only the retry clock uses completion time.
                clock.return_value = 1120
                raise LLMTransportError('LLM 请求超时')
            with patch('life.time.time', return_value=1000) as clock:
                brain.chat_with_tools.side_effect = fail
                result = life.heartbeat()
        self.assertTrue(result['failed'])
        self.assertEqual(result['drive'], 'none')
        self.assertEqual(result['next_wake'], 10)
        self.assertEqual(life.next_wake_at(), 1720)
        brain.chat_with_tools.assert_called_once()
        # A later successful call resumes normal model-selected scheduling.
        brain.chat_with_tools.side_effect = None
        brain.chat_with_tools.return_value = {'tool_calls': [
            {'name': 'schedule_next_wake', 'arguments': {'minutes': 35}}]}
        with patch.object(life, '_prompt', return_value='test'):
            recovered = life.heartbeat()
        self.assertFalse(recovered.get('failed', False))
        self.assertEqual(recovered['next_wake'], 35)

    def test_model_errors_do_not_use_network_retry_interval(self):
        life = Life(self.store, Mock(chat_with_tools=Mock(side_effect=ValueError('bad JSON'))))
        with patch.object(life, '_prompt', return_value='test'):
            result = life.heartbeat()
        self.assertTrue(result['failed'])
        self.assertEqual(result['next_wake'], 60)

    def test_failed_decision_does_not_claim_intentional_idling_or_act(self):
        auto = Autonomy(self.store, Mock())
        auto.life.heartbeat = Mock(return_value={'drive': 'none', 'failed': True})
        auto.select_tools = Mock()
        with patch.object(self.store, 'add_log') as log:
            auto._heartbeat_once()
        auto.select_tools.assert_not_called()
        log.assert_not_called()


if __name__ == '__main__':
    unittest.main()
