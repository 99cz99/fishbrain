import sys
from pathlib import Path
from unittest.mock import Mock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import websocket
except ImportError:
    module = Mock()
    module.WebSocketException = type('WebSocketException', (Exception,), {})
    module.WebSocketTimeoutException = type('WebSocketTimeoutException', (module.WebSocketException,), {})
    sys.modules['websocket'] = module

from server import Handler, Fish
from memory import Store


class AppContractTests(unittest.TestCase):
    def test_authorization_fails_closed(self):
        handler = Handler.__new__(Handler)
        handler.api_token = 'test-only-token'
        handler._send = Mock()
        for supplied in ('', 'Bearer wrong', 'test-only-token'):
            handler.headers = {'Authorization': supplied}
            self.assertFalse(handler._authorized())
        handler.headers = {'Authorization': 'Bearer test-only-token'}
        self.assertTrue(handler._authorized())
        handler.api_token = ''
        self.assertFalse(handler._authorized())

    def test_every_http_method_rejects_before_accessing_data(self):
        for method in ('do_GET', 'do_POST', 'do_PUT', 'do_DELETE'):
            handler = Handler.__new__(Handler)
            handler.api_token = 'test-only-token'
            handler.headers = {}
            handler._send = Mock()
            handler._read_json = Mock(side_effect=AssertionError('must not read body'))
            getattr(handler, method)()
            handler._send.assert_called_once_with(401, {'error': '需要监管 app 授权'})

    def test_explicit_empty_llm_fields_clear_old_values(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        store.set_setting('llm', {'api_key': 'test-only', 'vision_model': 'old'})
        handler = Handler.__new__(Handler)
        handler.fish = Mock(store=store)
        handler.path = '/llm'
        handler._authorized = Mock(return_value=True)
        handler._read_json = Mock(return_value={'vision_model': ''})
        handler._send = Mock()
        handler.do_PUT()
        cfg = store.get_settings()['llm']
        self.assertEqual(cfg['vision_model'], '')
        self.assertEqual(cfg['api_key'], 'test-only')

    def test_reset_clears_derived_profile(self):
        fish = Fish.__new__(Fish)
        fish.store = Store(':memory:')
        self.addCleanup(fish.store.close)
        fish.store.set_setting('self_profile', {'values': ['old']})
        fish.store.set_setting('life_state', {'mood': 'old'})
        fish.log = Mock()
        fish.reset_persona()
        self.assertEqual(fish.store.get_settings()['self_profile']['values'], [])
        self.assertEqual(fish.store.get_settings()['life_state'], {})

    def test_friend_request_executes_both_decisions(self):
        fish = Fish.__new__(Fish)
        fish.store = Mock()
        fish.store.get_approval.return_value = {'kind': 'friend', 'summary': '{"flag":"request-id"}'}
        fish.qq_bot = Mock()
        fish.qq_bot.set_friend_add_request.return_value = {'retcode': 0}
        for decision in (True, False):
            fish.execute_approval(1, approve=decision)
            fish.qq_bot.set_friend_add_request.assert_called_with('request-id', approve=decision)

    def test_legacy_request_does_not_fake_success(self):
        fish = Fish.__new__(Fish)
        fish.store = Mock()
        fish.store.get_approval.return_value = {'kind': 'friend', 'summary': 'legacy text'}
        with self.assertRaises(ValueError):
            fish.execute_approval(1)
