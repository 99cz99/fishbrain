import copy
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory import Store
from qq_control import QQController, QQBackend, WebUIClient, ControlError, ProcessManager


class FakeBackend:
    def __init__(self):
        self.rows = []
        self.process = Mock()
        self.process.scan.side_effect = lambda: {'known': True, 'rows': copy.deepcopy(self.rows)}
        self.webui = Mock()
        self.webui.call.return_value = {'ok': True, 'data': ['123']}
        self.spawn = Mock()
        self.configure = Mock(return_value={'ok': True})
        self.check_configuration = Mock(return_value=False)
        self.obs = dict(process_known=True, running=True, launching=True, login_accepted=False,
                        login_state_known=True,
                        account_ready=False, account_state='offline', online=False, qr=False, account='123')
    def observe(self):
        return {**self.obs, 'processes': copy.deepcopy(self.rows)}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.store.set_setting('qq_quick_login_uin', '123')
        self.backend = FakeBackend()
        self.now = 1000
        self.control = QQController(self.store, self.backend, clock=lambda: self.now, wall=lambda: self.now)

    def test_request_is_queued_deduplicated_and_stop_supersedes_it(self):
        first = self.control.request('quick_login')
        self.assertEqual(first['operation']['id'], self.control.request('quick_login')['operation']['id'])
        self.backend.webui.call.assert_not_called()
        old = self.control.operation
        stop = self.control.request('stop')
        self.assertEqual(stop['operation']['id'], self.control.request('stop')['operation']['id'])
        with self.assertRaises(ControlError):
            self.control._act(old, lambda: self.backend.spawn('123'))
        self.backend.spawn.assert_not_called()
        self.assertEqual(self.store.get_setting_raw('qq_desired_state'), 'stopped')

    def test_initializing_and_already_online_do_not_submit_login(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                self.control.operation = None
                self.backend.obs.update(login_accepted=not ready, account_ready=ready)
                self.control.request('quick_login')
                self.control.tick()
        self.backend.webui.call.assert_not_called()
        self.backend.spawn.assert_not_called()

    def test_unknown_submission_is_not_replayed_and_late_ready_completes(self):
        self.backend.webui.call.side_effect = [
            {'ok': True, 'data': ['123']}, {'ok': False, 'kind': 'unknown', 'message': 'unknown'}]
        self.control.request('quick_login')
        self.control.tick()
        self.now += 200
        self.control.tick()
        self.assertEqual(self.control.snapshot()['operation']['status'], 'unknown')
        self.control.request('quick_login')
        self.control.tick()
        self.assertEqual(self.backend.webui.call.call_count, 2)
        self.backend.obs['account_ready'] = True
        self.control.tick()
        self.assertEqual(self.control.snapshot()['operation']['status'], 'completed')
        self.assertFalse(self.backend.obs['online'])  # Authentication and OneBot availability are separate.

    def test_known_empty_and_unknown_lists_do_not_submit_native_login(self):
        for response in ({'ok': True, 'data': []}, {'ok': False, 'kind': 'transport', 'message': 'unreachable'}):
            with self.subTest(response=response):
                self.control.operation = None
                self.backend.webui.call.reset_mock()
                self.backend.webui.call.return_value = response
                self.control.request('quick_login')
                self.control.tick()
                self.backend.webui.call.assert_called_once_with('/api/QQLogin/GetQuickLoginList', {})
                self.backend.spawn.assert_not_called()

    def test_failed_stop_does_not_launch_and_main_is_stopped_before_group(self):
        main = {'pid': 2, 'start': 'a', 'group': 1, 'role': 'main'}
        launcher = {'pid': 1, 'start': 'b', 'group': 1, 'role': 'launcher'}
        self.backend.rows = [main, launcher]
        self.control.request('restart')
        self.control.tick()
        self.control.tick()
        self.backend.process.signal.assert_called_once()
        self.backend.process.signal_group.assert_not_called()
        self.now += 23
        self.control.tick()
        self.assertEqual(self.control.snapshot()['operation']['code'], 'stop_unconfirmed')
        self.backend.spawn.assert_not_called()

    def test_confirmed_stop_then_start_preserves_user_running_intent(self):
        self.backend.obs.update(running=False, launching=False)
        self.control.request('restart')
        self.control.tick()
        self.control.tick()
        self.backend.spawn.assert_called_once_with('123')
        self.assertEqual(self.control.desired, 'running')

    def test_configuration_timeout_is_read_back_without_another_write(self):
        self.backend.obs['account_ready'] = True
        self.backend.configure.return_value = {'ok': False, 'kind': 'unknown', 'message': 'unknown'}
        self.control.request('configure')
        self.control.tick()
        self.control.tick()
        self.backend.configure.assert_called_once()
        self.backend.check_configuration.assert_called_once()
        self.backend.check_configuration.return_value = True
        self.control.tick()
        self.assertEqual(self.control.snapshot()['operation']['status'], 'completed')

    def test_target_change_and_platform_disable_fence_old_actions(self):
        self.control.request('quick_login')
        old = self.control.operation
        self.control.set_target('456')
        with self.assertRaises(ControlError):
            self.control._act(old, lambda: self.backend.spawn('123'))
        self.control.request('quick_login')
        self.store.set_setting('enabled_platforms', [])
        self.control.tick()
        self.assertEqual(self.control.snapshot()['operation']['status'], 'cancelled')
        self.backend.webui.call.assert_not_called()

    def test_service_restart_adopts_unknown_work_instead_of_replaying(self):
        self.control.request('quick_login')
        new = QQController(self.store, self.backend, clock=lambda: self.now, wall=lambda: self.now)
        self.assertNotEqual(self.control.instance, new.instance)
        self.assertEqual(new.snapshot()['operation']['status'], 'unknown')
        new.tick()
        self.backend.webui.call.assert_not_called()
        self.control.request('stop')
        stopped = QQController(self.store, self.backend)
        self.assertEqual(stopped.desired, 'stopped')

    def test_interrupted_stop_resumes_stop_instead_of_accepting_an_online_account(self):
        self.control.request('stop')
        self.backend.obs['account_ready'] = True
        new = QQController(self.store, self.backend)
        new.tick()
        self.assertEqual(new.snapshot()['operation']['phase'], 'stopping')
        self.backend.spawn.assert_not_called()

    def test_changed_target_cancels_unknown_job_and_allows_a_new_request(self):
        self.control.request('quick_login')
        self.control._finish(self.control.operation, 'unknown')
        self.control.set_target('456')
        self.assertTrue(self.control.request('quick_login')['queued'])
        self.assertEqual(self.control.operation['target'], '456')

    def test_unknown_native_status_does_not_authorize_login_with_cached_candidates(self):
        self.backend.obs['login_state_known'] = False
        self.control.request('quick_login')
        self.control.tick()
        self.backend.webui.call.assert_not_called()
        self.assertEqual(self.control.operation['phase'], 'checking')
        self.backend.obs['login_state_known'] = True
        self.control.tick()
        self.assertEqual(self.backend.webui.call.call_count, 2)

    def test_service_restart_preserves_native_rejection_and_never_auto_replays_it(self):
        self.control.request('quick_login')
        self.control._finish(self.control.operation, 'failed', 'native', 'rejected')
        new = QQController(self.store, self.backend, clock=lambda:self.now)
        self.now += 1000
        new.tick()
        self.backend.webui.call.assert_not_called()
        self.assertEqual(new.snapshot()['operation']['status'], 'failed')


class ProcessIdentityTests(unittest.TestCase):
    def test_wrapper_command_is_not_native_qq_and_only_dedicated_groups_are_tracked(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            def put(pid, args, group, session):
                p = proc/str(pid)
                p.mkdir()
                (p/'cmdline').write_bytes(b'\0'.join(x.encode() for x in args)+b'\0')
                fields = ['S']+['0']*20
                fields[2], fields[3], fields[19] = str(group), str(session), str(pid+100)
                (p/'stat').write_text(f'{pid} (qq) '+ ' '.join(fields))
            put(10, ['bash', '-c', 'cd /opt/QQ && ./qq --no-sandbox'], 5, 5)
            manager = ProcessManager(proc)
            with patch('qq_control.os.getuid', create=True, return_value=proc.stat().st_uid):
                self.assertEqual(manager.scan(), {'known': True, 'rows': []})
                put(20, ['proot', '-r', '/container', 'bash', '-c', 'cd /opt/QQ && ./qq --no-sandbox'], 20, 20)
                put(21, ['./qq', '--no-sandbox'], 20, 20)
                put(22, ['./qq', '--no-sandbox', '--type=renderer'], 20, 20)
                rows = manager.scan()
                self.assertTrue(rows['known'])
                self.assertEqual([r['role'] for r in rows['rows']], ['launcher','main','aux'])

    def test_reused_pid_and_process_group_are_never_signalled(self):
        manager = ProcessManager()
        old = {'pid': 1, 'start': 'a', 'group': 1, 'role': 'launcher'}
        manager.scan = Mock(return_value={'known': True, 'rows': [{**old, 'start': 'b'}]})
        with patch('qq_control.os.kill') as kill, patch('qq_control.os.killpg', create=True) as killpg:
            manager.signal(old, 15)
            manager.signal_group([old], 15)
            kill.assert_not_called()
            killpg.assert_not_called()


class WebUICacheTests(unittest.TestCase):
    def test_slow_read_budget_does_not_extend_native_write_timeout(self):
        client = WebUIClient(lambda:'dummy')
        client.credential = 'existing'
        import time
        client.credential_at = time.monotonic()
        client.raw = Mock(return_value={'ok':True,'data':{}})
        client.call('/read',{})
        self.assertEqual(client.raw.call_args.kwargs['timeout'],8)
        client.call('/action',{},mutation=True)
        self.assertEqual(client.raw.call_args.kwargs['timeout'],2)

    def test_unauthorized_reauthenticates_once_but_action_timeout_does_not(self):
        client = WebUIClient(lambda: 'dummy')
        client.credential = 'old'
        import time
        client.credential_at = time.monotonic()
        client.raw = Mock(side_effect=[{'ok': False, 'kind': 'auth'},
                                      {'ok': True, 'data': {'Credential': 'new'}},
                                      {'ok': True, 'data': []}])
        self.assertTrue(client.call('/read', {})['ok'])
        self.assertEqual(client.raw.call_count, 3)
        client.raw.reset_mock(side_effect=True)
        client.raw.return_value = {'ok': False, 'kind': 'unknown'}
        self.assertEqual(client.call('/action', {}, mutation=True)['kind'], 'unknown')
        client.raw.assert_called_once()


class StatusEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root=Path(self.directory.name)
        self.bot=Mock(connected=True,self_id='123')
        self.transport={'last_received_at':119,'account_status_at':119,
                        'account_status_self_id':'123','account_online':True}
        self.bot.diagnostics.side_effect=lambda:dict(self.transport)
        fish=Mock(qq_bot=self.bot)
        self.backend=QQBackend(fish,root,'proot',Mock(),Mock(),root,root/'qr.png')
        rows=[{'pid':1,'start':'a','group':1,'role':'launcher'},
              {'pid':2,'start':'b','group':1,'role':'main'}]
        self.backend.process=Mock(scan=Mock(return_value={'known':True,'rows':rows}))
        self.backend.process_key=((1,'a'),(2,'b'))
        self.backend.session_at=100
        self.backend.last_probe=120
        self.backend.cached={'error':'transport','at':117}

    def observe(self):
        with patch('qq_control.time.time',return_value=120),patch('qq_control.time.monotonic',return_value=120):
            return self.backend.observe()

    def test_control_timeout_does_not_hide_fresh_explicit_same_session_onebot_online(self):
        status=self.observe()
        self.assertTrue(status['account_ready'])
        self.assertTrue(status['online'])
        self.assertEqual(status['account_source'],'onebot')
        self.assertFalse(status['native_ready'])
        self.assertEqual(status['control_error'],'transport')

    def test_old_other_session_other_account_and_arbitrary_frames_cannot_prove_login(self):
        for values in ({'account_status_at':99},
                       {'account_status_self_id':'456'},
                       {'account_status_at':0,'last_received_at':120},
                       {'account_online':None}):
            with self.subTest(values=values):
                old=dict(self.transport)
                self.transport.update(values)
                self.assertFalse(self.observe()['account_ready'])
                self.transport=old
        self.backend.session_at=0
        self.transport['account_status_at']=20
        self.assertFalse(self.observe()['online'])  # A new arbitrary frame cannot refresh old account evidence.

    def test_explicit_offline_and_native_account_conflict_override_positive_history(self):
        self.backend.cached={'state':{'isLogin':True,'coreReady':True,'loginPhase':'ready'},'account':'123','at':115}
        self.transport['account_online']=False
        self.assertFalse(self.observe()['account_ready'])
        self.assertFalse(self.observe()['online'])
        self.transport['account_online']=True
        self.backend.cached={'state':{'isLogin':False,'isOffline':True,'coreReady':False,'loginPhase':'offline'},'at':119}
        self.assertFalse(self.observe()['online'])
        self.backend.cached={'state':{'isLogin':True,'coreReady':True,'loginPhase':'ready'},'account':'456','at':119}
        self.assertFalse(self.observe()['online'])

    def test_config_read_cannot_authorize_a_write_after_stop_supersedes_the_job(self):
        store=Store(':memory:')
        self.addCleanup(store.close)
        backend=FakeBackend()
        backend.obs['account_ready']=True
        controller=QQController(store,backend)
        write=Mock()
        def configure(guard):
            controller.request('stop')
            return guard(write)
        backend.configure.side_effect=configure
        controller.request('configure')
        controller.tick()
        write.assert_not_called()
        self.assertEqual(controller.operation['kind'],'stop')
        self.assertEqual(controller.operation['status'],'queued')


if __name__ == '__main__':
    unittest.main()
