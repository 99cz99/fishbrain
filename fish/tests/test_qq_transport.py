import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import websocket
except ImportError:
    # Transport is mocked: tests need no network or websocket-client install.
    sys.modules['websocket'] = Mock()
    sys.modules['websocket'].WebSocketTimeoutException = type('WebSocketTimeoutException', (Exception,), {})
    sys.modules['websocket'].WebSocketException = type('WebSocketException', (Exception,), {})

from qq_bot import QQBot
from memory import Store
from server import Fish
from autonomy import Autonomy
from delivery import SendUnconfirmedError


class TransportTests(unittest.TestCase):
    def make_bot(self):
        store = Mock()
        store.get_settings.return_value = {'enabled_platforms': ['qq']}
        return QQBot('ws://localhost:8081', Mock(), store)

    def test_poke_direction_and_channel(self):
        bot = self.make_bot()
        bot.self_id = 100
        cases = [
            ({'user_id': 200, 'sender_id': 100, 'target_id': 200}, '我 戳了戳 200（私聊）'),
            ({'user_id': 200, 'sender_id': 200, 'target_id': 100}, '200 戳了戳 我（私聊）'),
            ({'user_id': 200, 'target_id': 100, 'group_id': 300}, '200 戳了戳 我（群 300）'),
            ({'user_id': 200, 'target_id': 400, 'group_id': 300}, '200 戳了戳 400（群 300）'),
            ({'user_id': 200, 'sender_id': 100, 'target_id': 400, 'group_id': 300}, '200 戳了戳 400（群 300）'),
            ({'user_id': 200}, '未知用户 戳了戳 未知用户（私聊）'),
            ({'sender_id': '100', 'target_id': '100', 'group_id': '0'}, '我 戳了戳 我（私聊）'),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                bot._on_notice({'notice_type': 'notify', 'sub_type': 'poke', **fields})
                self.assertEqual(bot.store.add_log.call_args.args[2], '[QQ] ' + expected)

    def test_idle_timeout_probes_without_reconnecting(self):
        import qq_bot
        bot = self.make_bot()
        bot._running = True
        ws = Mock()
        frames = iter([qq_bot.websocket.WebSocketTimeoutException('idle'),
                       json.dumps({'echo': qq_bot.WS_HEALTH_ECHO, 'retcode': 0})])
        def recv():
            try:
                frame = next(frames)
            except StopIteration:
                bot._running = False
                return ''
            if isinstance(frame, Exception):
                raise frame
            return frame
        ws.recv.side_effect = recv
        with patch('qq_bot.websocket.create_connection', return_value=ws) as connect:
            bot._loop()
        bot._worker_thread.join(2)
        connect.assert_called_once()
        self.assertEqual(json.loads(ws.send.call_args.args[0])['action'], 'get_status')
        self.assertEqual(ws.settimeout.call_args_list[0].args, (10,))
        self.assertEqual(ws.settimeout.call_args_list[1].args, (30,))
        self.assertEqual(bot._event_queue.qsize(), 0)

    def test_unresponsive_idle_probe_disconnects(self):
        import qq_bot
        bot = self.make_bot()
        bot._running = True
        ws = Mock()
        ws.recv.side_effect = qq_bot.websocket.WebSocketTimeoutException('idle')
        def backoff(seconds):
            bot._running = False
        bot._stop_event.wait = backoff
        with patch('qq_bot.websocket.create_connection', return_value=ws):
            bot._loop()
        bot._worker_thread.join(2)
        self.assertEqual(ws.recv.call_count, 2)
        self.assertFalse(bot.connected)
        self.assertTrue(any('ConnectionError' in call.args[2] for call in bot.store.add_log.call_args_list))

    def test_account_status_uses_explicit_platform_boolean(self):
        bot = self.make_bot()
        bot.self_id = 123
        bot._update_account_state({'online': False})
        self.assertFalse(bot.account_online)
        self.assertEqual(bot.self_id, 123)
        bot._update_account_state({'online': 'true'})
        self.assertFalse(bot.account_online)
        bot._update_account_state({'online': True})
        self.assertTrue(bot.account_online)

    def test_http_online_respects_account_offline(self):
        fish = Fish.__new__(Fish)
        fish.qq_bot = self.make_bot()
        fish.qq_bot.connected = True
        fish.qq_bot.self_id = 123
        self.assertTrue(fish._qq_really_online())
        fish.qq_bot._update_account_state({'online': False})
        self.assertFalse(fish._qq_really_online())
        fish.qq_bot._update_account_state({'online': True})
        self.assertTrue(fish._qq_really_online())

    def test_http_api_ok_requires_platform_confirmation(self):
        fish = Fish.__new__(Fish)
        fish.qq_bot = Mock()
        for response in (None, {'retcode': 1}):
            fish.qq_bot.call_api.return_value = response
            self.assertFalse(fish.qq_api('get_status', {})['ok'])
        fish.qq_bot.call_api.return_value = {'retcode': 0}
        self.assertTrue(fish.qq_api('get_status', {})['ok'])

    def test_delayed_lifecycle_does_not_restore_stale_identity(self):
        bot = self.make_bot()
        bot._on_meta({'meta_event_type': 'lifecycle', 'sub_type': 'connect', 'self_id': 123})
        self.assertIsNone(bot.self_id)
        bot.store.set_setting.assert_called_once_with('qq_quick_login_uin', '123')

    def test_api_request_keeps_original_socket_across_reconnect(self):
        bot = self.make_bot()
        original, replacement = Mock(), Mock()
        bot._ws = original
        def send(raw):
            bot._ws = replacement
            echo = json.loads(raw)['echo']
            entry = bot._pending[echo]
            entry['response'] = {'retcode': 0}
            entry['event'].set()
        original.send.side_effect = send
        self.assertEqual(bot.call_api('get_status', {}), {'retcode': 0})
        original.send.assert_called_once()
        replacement.send.assert_not_called()
        self.assertEqual(bot._pending, {})

    def test_closed_socket_clears_online_before_reconnect(self):
        bot = self.make_bot()
        bot._running = True
        bot.self_id = 123
        ws = Mock()
        ws.recv.return_value = ''
        def backoff(seconds):
            self.assertFalse(bot.connected)
            self.assertIsNone(bot.self_id)
            bot._running = False
        bot._stop_event.wait = backoff
        with patch('qq_bot.websocket.create_connection', return_value=ws):
            bot._loop()
        bot._worker_thread.join(2)
        ws.recv.assert_called_once()
        ws.close.assert_called_once()

    def test_worker_survives_event_and_database_failure(self):
        bot = self.make_bot()
        bot._running = True
        bot.store.add_log.side_effect = RuntimeError('database unavailable')
        bot._event_queue.put({'post_type': 'message'})
        bot._event_queue.put({'post_type': 'message'})
        handled = []
        def handle(event):
            handled.append(event)
            if len(handled) == 1:
                raise ValueError('bad event')
            bot._running = False
        bot._on_event = handle
        with self.assertLogs(level='ERROR'):
            bot._worker()
        self.assertEqual(len(handled), 2)
        self.assertIsNone(bot._processing_since)
        self.assertEqual(bot._event_queue.unfinished_tasks, 0)

    def test_api_response_received_while_message_worker_busy(self):
        bot = self.make_bot()
        entered = threading.Event()
        release = threading.Event()
        response = threading.Event()
        frames = iter([
            json.dumps({'post_type': 'message'}),
            json.dumps({'post_type': 'meta_event', 'meta_event_type': 'lifecycle',
                        'sub_type': 'connect', 'self_id': 123}),
        ])
        ws = Mock()
        def recv():
            try:
                return next(frames)
            except StopIteration:
                response.wait(2)
                return json.dumps({'echo': 'dfy_1', 'retcode': 0}) if bot._running else ''
        def send(raw):
            self.assertEqual(json.loads(raw)['echo'], 'dfy_1')
            response.set()
        ws.recv.side_effect = recv
        ws.send.side_effect = send
        def handle(event):
            entered.set()
            release.wait(2)
        bot._on_event = handle
        with patch('qq_bot.websocket.create_connection', return_value=ws):
            bot.start()
            try:
                self.assertTrue(entered.wait(2))
                result = bot.call_api('get_status', {})
                self.assertEqual(result['retcode'], 0)
                self.assertEqual(bot.self_id, 123)
            finally:
                bot.stop()
                response.set()
                release.set()
                bot._thread.join(2)
                bot._worker_thread.join(2)

    def test_send_requires_successful_api_ack(self):
        bot = self.make_bot()
        for result in (None, {'retcode': 1, 'message': 'offline'}):
            bot.call_api = Mock(return_value=result)
            with self.assertRaises(RuntimeError):
                bot.send_msg(123, [])
        bot.call_api = Mock(return_value={'retcode': 0})
        self.assertEqual(bot.send_msg(123, [], 'group'), {'retcode': 0})
        bot.call_api.assert_called_once_with('send_group_msg', {'group_id': 123, 'message': []}, timeout=45)

    def test_send_timeout_retcode_is_unconfirmed(self):
        bot = self.make_bot()
        bot.call_api = Mock(return_value={'retcode': 1200, 'message': 'Timeout: NTEvent sendMsg', 'wording': ''})
        with self.assertRaises(SendUnconfirmedError):
            bot.send_msg(123, [])
        bot.call_api = Mock(return_value={'retcode': 1, 'message': 'rejected'})
        with self.assertRaises(RuntimeError):
            bot.send_msg(123, [])

    def test_autonomous_unconfirmed_send_is_warning_without_retry(self):
        agent = Autonomy.__new__(Autonomy)
        agent._paused = False
        agent.store = Mock()
        agent.store.get_settings.return_value = {'owner_qq': '123', 'enabled_platforms': ['qq']}
        agent.qq_bot = Mock()
        agent.qq_bot.send_text.side_effect = SendUnconfirmedError('no ack')
        agent._do_talk('hello')
        agent.qq_bot.send_text.assert_called_once()
        level, category, message = agent.store.add_log.call_args.args
        self.assertEqual((level, category), ('warning', 'action'))
        self.assertIn('未获确认', message)
        self.assertNotIn('失败', message)

    def test_autonomous_rejected_send_still_reports_failure(self):
        agent = Autonomy.__new__(Autonomy)
        agent._paused = False
        agent.store = Mock()
        agent.store.get_settings.return_value = {'owner_qq': '123', 'enabled_platforms': ['qq']}
        agent.qq_bot = Mock()
        agent.qq_bot.send_text.side_effect = RuntimeError('retcode=1')
        agent._do_talk('hello')
        self.assertEqual(agent.store.add_log.call_args.args[0], 'error')

    def test_autonomous_poke_checks_confirmation(self):
        for response, level, text in [
            (None, 'warning', '未获确认'),
            ({'retcode': 0}, 'info', '请求已获平台确认'),
            ({'retcode': 1}, 'error', '被平台拒绝'),
        ]:
            with self.subTest(response=response):
                agent = Autonomy.__new__(Autonomy)
                agent._paused = False
                agent.store = Mock()
                agent.store.get_settings.return_value = {'owner_qq': '100', 'enabled_platforms': ['qq']}
                agent.qq_bot = Mock()
                agent.qq_bot.send_poke.return_value = response
                agent._do_poke()
                agent.qq_bot.send_poke.assert_called_once_with(user_id='100')
                args = agent.store.add_log.call_args.args
                self.assertEqual(args[0], level)
                self.assertIn(text, args[2])

    def test_concurrent_store_read_write(self):
        store = Store(':memory:')
        errors = []
        def access():
            try:
                for i in range(100):
                    store.add_log('info', 'chat', str(i))
                    store.get_logs(limit=10)
                    store.get_sticker(i)
                    store.get_settings()
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=access) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(store.get_logs(limit=1000)), 800)
        store.close()


if __name__ == '__main__':
    unittest.main()
