"""QQ lifecycle control: serialized operations, native-login evidence and bounded process cleanup."""
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

ACTIVE = ('queued', 'running', 'waiting')
BUSY_PHASES = ('initializing', 'reconnecting', 'qrcode_scanned', 'generating_qrcode')
KILL = getattr(signal, 'SIGKILL', 9)


class ControlError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class WebUIClient:
    def __init__(self, token, url='http://127.0.0.1:6099', timeout=2):
        self.token, self.url, self.timeout = token, url, timeout
        self.credential = None
        self.credential_at = 0
        self.lock = threading.RLock()

    def invalidate(self):
        self.credential, self.credential_at = None, 0

    def raw(self, path, body=None, credential=None, mutation=False, timeout=None):
        headers = {'Content-Type': 'application/json'}
        if credential:
            headers['Authorization'] = 'Bearer '+credential
        request = urllib.request.Request(self.url+path, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                result = json.load(response)
            if not isinstance(result, dict) or 'code' not in result:
                return {'ok': False, 'kind': 'protocol', 'message': 'WebUI 响应格式无效'}
            if result['code'] == 0:
                return {'ok': True, 'data': result.get('data')}
            text = str(result.get('message') or '')
            if any(s in text.lower() for s in ('unauthorized', 'authorization failed', 'token is invalid')):
                kind, message = 'auth', 'WebUI 控制凭证已失效'
            elif 'QQ Is Logined' in text:
                kind, message = 'already_authenticated', 'QQ 已接受登录，正在确认状态'
            else:
                kind = 'native'
                message = re.sub(r'https?://\S+', '[验证链接已隐藏]', text)[:180] or 'QQ 拒绝了该操作'
            return {'ok': False, 'kind': kind, 'message': message}
        except urllib.error.HTTPError as exc:
            return {'ok': False, 'kind': 'auth' if exc.code in (401, 403) else ('unknown' if mutation and exc.code >= 500 else 'http'),
                    'message': 'WebUI 鉴权失败' if exc.code in (401, 403) else f'WebUI HTTP {exc.code}'}
        except (OSError, ValueError) as exc:
            reason = getattr(exc, 'reason', exc)
            refused = getattr(reason, 'errno', None) == errno.ECONNREFUSED
            return {'ok': False, 'kind': 'unknown' if mutation and not refused else 'transport',
                    'message': '请求结果未知，正在查询 QQ 状态' if mutation and not refused else 'WebUI 暂时不可达'}

    def call(self, path, body=None, mutation=False):
        with self.lock:
            # Slow proot reads must not turn an online account into a failed
            # login. Keep native writes short and the whole control call bounded.
            deadline = time.monotonic()+max(12, self.timeout)
            def raw(endpoint, payload, credential=None, mutation=False):
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return {'ok': False, 'kind': 'transport', 'message': 'WebUI 查询等待超时'}
                timeout = min(self.timeout if mutation else max(8, self.timeout), remaining)
                return self.raw(endpoint, payload, credential, mutation, timeout=timeout)
            for attempt in range(2):
                if not self.credential or time.monotonic()-self.credential_at >= 3300:
                    digest = hashlib.sha256((self.token()+'.napcat').encode()).hexdigest()
                    auth = raw('/api/auth/login', {'hash': digest})
                    data = auth.get('data') or {}
                    if not auth['ok'] or not isinstance(data, dict) or not data.get('Credential'):
                        return auth if not auth['ok'] else {'ok': False, 'kind': 'protocol', 'message': 'WebUI 未返回控制凭证'}
                    self.credential, self.credential_at = data['Credential'], time.monotonic()
                result = raw(path, body, self.credential, mutation)
                if result.get('kind') != 'auth':
                    return result
                self.invalidate()
            return result


class ProcessManager:
    """Only identify actual QQ executables and verified dedicated proot sessions."""
    def __init__(self, proc=Path('/proc')):
        self.proc = Path(proc)

    def scan(self):
        rows, known = [], True
        if not self.proc.exists():
            return {'known': True, 'rows': []}
        for path in self.proc.iterdir():
            if not path.name.isdigit():
                continue
            # Android hidepid denies stat on foreign UIDs. These processes
            # cannot be Termux-owned targets; unreadable *owned* entries below
            # still fence destructive actions.
            try:
                owned = path.stat().st_uid == os.getuid()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            if not owned:
                continue
            try:
                args = (path/'cmdline').read_bytes().split(b'\0')
                stat = (path/'stat').read_text().rsplit(')', 1)[1].split()
                if stat[0] == 'Z' or not args[0]:
                    continue
                binary = Path(os.fsdecode(args[0])).name
                role = 'aux'
                if binary == 'qq' and b'--no-sandbox' in args and not any(a.startswith(b'--type=') for a in args):
                    role = 'main'
                elif (binary == 'proot' and int(path.name) == int(stat[2]) == int(stat[3])
                      and b'/opt/QQ' in b' '.join(args) and b'./qq' in b' '.join(args)):
                    role = 'launcher'
                rows.append({'pid': int(path.name), 'start': stat[19], 'group': int(stat[2]), 'role': role})
            except (FileNotFoundError, ProcessLookupError):
                pass
            except (OSError, ValueError, IndexError):
                known = False
        groups = {r['group'] for r in rows if r['role'] == 'launcher'}
        if any(r['role'] == 'main' and r['group'] not in groups for r in rows):
            known = False  # No verified QQ launcher/session; do not signal or launch alongside it.
        return {'known': known, 'rows': [r for r in rows if r['role'] != 'aux' or r['group'] in groups]}

    def signal(self, record, sig):
        current = self.scan()
        if not current['known']:
            raise ControlError('process_unknown', '无法确认 QQ 进程身份，未发送信号')
        if any(r == record for r in current['rows']):
            try:
                os.kill(record['pid'], sig)
            except ProcessLookupError:
                pass

    def signal_group(self, anchors, sig):
        current = self.scan()
        if not current['known']:
            raise ControlError('process_unknown', '无法确认 QQ 会话组身份')
        for group in {r['group'] for r in anchors if r['role'] == 'launcher'}:
            # A surviving original member fences process-group reuse.
            if any(r in current['rows'] and r['group'] == group for r in anchors):
                try:
                    os.killpg(group, sig)
                except ProcessLookupError:
                    pass


class QQBackend:
    def __init__(self, fish, root, proot, command, env, config_dir, qr_path):
        self.fish, self.root, self.proot = fish, Path(root), proot
        self.command, self.env = command, env
        self.config_dir, self.qr_path = Path(config_dir), Path(qr_path)
        self.process = ProcessManager()
        self.webui = WebUIClient(fish._webui_token)
        self.last_probe, self.cached = 0, {}
        self.process_key = None
        self.session_at = 0
        self.pending_config = None

    def observe(self):
        scan = self.process.scan()
        rows = scan['rows']
        key = tuple(sorted((r['pid'], r['start']) for r in rows if r['role'] in ('main', 'launcher')))
        if key != self.process_key:
            self.process_key, self.session_at = key, time.time()
            self.webui.invalidate()
            self.cached, self.last_probe = {}, 0
        running = any(r['role'] == 'main' for r in rows)
        launching = any(r['role'] == 'launcher' for r in rows)
        if (running or launching) and time.monotonic()-self.last_probe >= 4:
            self.last_probe = time.monotonic()
            result = self.webui.call('/api/QQLogin/CheckLoginStatus', {})
            data = result.get('data')
            if result['ok'] and isinstance(data, dict):
                self.cached = {'state': data, 'at': time.time()}
                if data.get('coreReady') or data.get('isLogin'):
                    info = self.webui.call('/api/QQLogin/GetQQLoginInfo', {})
                    if info['ok'] and isinstance(info.get('data'), dict):
                        self.cached['account'] = str(info['data'].get('uin') or '')
            else:
                self.cached = {'error': result.get('kind'), 'at': time.time()}
        state = self.cached.get('state') or {}
        if time.time()-self.cached.get('at', 0) > 15:
            state = {}
        phase = state.get('loginPhase', 'unknown')
        state_known = bool(state and isinstance(state.get('isLogin'), bool)
                           and isinstance(state.get('coreReady'), bool) and phase != 'unknown')
        account = self.cached.get('account', '') if state else ''
        ready = bool(state.get('isLogin') and state.get('coreReady') and account)
        bot = getattr(self.fish, 'qq_bot', None)
        transport = bot.diagnostics() if bot else {}
        connected = bool(bot and bot.connected and bot.self_id
                         and transport.get('last_received_at', 0)
                         and transport['last_received_at'] >= self.session_at)
        match = bool(ready and bot and str(bot.self_id) == account)
        native_ready = ready
        status_at = transport.get('account_status_at') or 0
        status_account = transport.get('account_status_self_id')
        status_fresh = bool(connected and status_at >= self.session_at
                            and 0 <= time.time()-status_at <= 90
                            and str(status_account) == str(bot.self_id))
        explicit_offline = bool(status_fresh and transport.get('account_online') is False)
        native_rejects = bool(state and (state.get('isOffline') is True or state.get('isLogin') is False))
        native_mismatch = bool(account and bot and str(bot.self_id) != account)
        onebot_ready = bool(status_fresh and transport.get('account_online') is True
                            and scan['known'] and sum(r['role']=='main' for r in rows)==1
                            and not native_rejects and not native_mismatch)
        source = 'webui' if ready else 'none'
        if not ready and onebot_ready:
            ready, account, match, source = True, str(bot.self_id), True, 'onebot'
        if explicit_offline:
            match = False  # A live explicit offline report always blocks business-online.
            if status_at >= self.cached.get('at', 0):
                ready, source = False, 'none'
        try:
            qr_at = self.qr_path.stat().st_mtime
            qr_age = int(time.time()-qr_at)
            qr = 0 <= qr_age < 90 and qr_at >= self.session_at-1
        except OSError:
            qr_age, qr = -1, False
        return {'process_known': scan['known'], 'processes': rows, 'running': running,
                'launching': launching, 'process_key': key, 'qr': qr, 'qr_age': qr_age,
                'account_state': 'ready' if ready else ('offline' if state.get('isOffline') or explicit_offline else phase),
                'account': account, 'account_ready': ready, 'login_phase': phase,
                'account_source': source, 'native_ready': native_ready,
                'login_state_known': state_known,
                'login_accepted': bool(state.get('qrLoginAccepted') or phase in BUSY_PHASES),
                'transport_state': 'connected' if connected else 'disconnected',
                'online': bool(ready and connected and match), 'transport': transport,
                'observed_at': time.time(), 'account_observed_at': status_at if source=='onebot' else self.cached.get('at'),
                'control_error': self.cached.get('error')}

    def spawn(self, target):
        self.webui.invalidate()
        with (self.root/'qq_start_err.log').open('ab') as log:
            subprocess.Popen([self.proot, 'login', 'debian', '--hostname', 'qq-bot', '--', 'bash', '-c', self.command(target)],
                             env=self.env(), stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)

    def configure(self, guard=None):
        from qq_config import delivery_config
        current = self.webui.call('/api/OB11Config/GetConfig', {})
        if not current['ok']:
            return current
        data = current.get('data')
        if not isinstance(data, dict):
            return {'ok': False, 'kind': 'protocol', 'message': 'OneBot 配置格式无效'}
        defaults = json.loads((self.root/'onebot11.json').read_text())
        wanted = delivery_config(data, defaults)
        self.pending_config = wanted
        if data == wanted:
            return {'ok': True, 'already': True}
        # Native config save/reload, never restart an accepted QQ login.
        write = lambda: self.webui.call('/api/OB11Config/SetConfig', {'config': json.dumps(wanted)}, mutation=True)
        return guard(write) if guard else write()

    def check_configuration(self):
        result = self.webui.call('/api/OB11Config/GetConfig', {})
        return bool(self.pending_config is not None and result['ok'] and result.get('data') == self.pending_config)


class QQController:
    def __init__(self, store, backend, clock=time.monotonic, wall=time.time):
        self.store, self.backend, self.clock, self.wall = store, backend, clock, wall
        self.lock, self.wake_event = threading.RLock(), threading.Event()
        self.instance, self.generation = uuid.uuid4().hex, 0
        self.desired = 'stopped' if store.get_setting_raw('qq_desired_state') == 'stopped' else 'running'
        self.operation, self.observation = None, {}
        self.born, self.last_auto, self.circuit_until, self.failures = clock(), 0, 0, 0
        self.thread = None
        self.stable_since = None
        previous = store.get_setting_raw('qq_last_operation')
        if isinstance(previous, dict) and previous.get('status') in (*ACTIVE, 'unknown'):
            self.operation = {**previous, 'id': self.instance+':0', 'generation': 0, 'target': self.target(),
                              'status': 'unknown', 'phase': 'reconciling', 'deadline': clock()+180}
        elif isinstance(previous, dict) and previous.get('status') in ('failed', 'needs_scan', 'needs_verification'):
            # A Fish service restart is not permission to replay a rejected login.
            self.operation = {**previous, 'id': self.instance+':0', 'generation': 0, 'target': self.target()}

    def target(self):
        value = self.store.get_setting_raw('qq_login_target')
        if value is None:
            value = self.store.get_setting_raw('qq_quick_login_uin')
        value = str(value or '').strip()
        return value if value.isdigit() else None

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def set_target(self, value):
        with self.lock:
            self.generation += 1
            self.store.set_setting('qq_login_target', value)
            self.store.set_setting('qq_quick_login_uin', value)
            if self.operation and self.operation['status'] in (*ACTIVE, 'unknown'):
                self.operation.update(status='cancelled', phase='cancelled', error='目标账号已变化')
                self._persist()
            self.wake_event.set()

    def request(self, kind, target=None, manual=True):
        with self.lock:
            if kind not in ('start', 'stop', 'restart', 'quick_login', 'refresh_qr', 'configure'):
                return {'ok': False, 'error': '未知 QQ 操作', 'code': 'invalid_operation'}
            if kind != 'stop' and 'qq' not in self.store.get_settings().get('enabled_platforms', []):
                return {'ok': False, 'error': 'QQ 平台已禁用', 'code': 'platform_disabled'}
            target = target or self.target()
            if target is not None and (not isinstance(target, str) or not target.isdigit()):
                return {'ok': False, 'error': 'QQ 账号格式无效', 'code': 'invalid_account'}
            old = self.operation
            if old and old['kind'] == kind == 'stop' and old['status'] in ACTIVE:
                return {'ok': True, 'queued': True, 'operation': self._public(old)}
            if old and old['status'] in (*ACTIVE, 'unknown') and kind != 'stop':
                if kind == old['kind'] and target == old.get('target'):
                    return {'ok': True, 'queued': old['status'] in ACTIVE, 'operation': self._public(old)}
                # An explicit destructive recovery may supersede an unknown result, never an executing job.
                if old['status'] != 'unknown' or kind not in ('restart', 'refresh_qr'):
                    return {'ok': False, 'error': '已有 QQ 操作进行中或结果未知，请先查看状态', 'code': 'operation_busy'}
            if kind in ('quick_login', 'configure') and self.desired == 'stopped':
                return {'ok': False, 'error': 'QQ 已被明确停止，请先启动', 'code': 'stopped'}
            if kind == 'quick_login' and not target:
                return {'ok': False, 'error': '未配置目标 QQ 账号，请扫码登录', 'code': 'needs_scan'}
            if old and old['status'] in ACTIVE:
                old.update(status='cancelled', phase='cancelled')
            if manual and kind in ('start', 'restart', 'refresh_qr', 'stop'):
                self.desired = 'stopped' if kind == 'stop' else 'running'
                self.store.set_setting('qq_desired_state', self.desired)
            if manual:
                self.failures, self.circuit_until = 0, 0
            self.generation += 1
            self.operation = {'id': f'{self.instance}:{self.generation}', 'generation': self.generation,
                'kind': kind, 'target': target, 'status': 'queued', 'phase': 'queued',
                'error': '', 'code': '', 'created_at': self.wall(), 'deadline': self.clock()+180}
            self._persist()
            self.wake_event.set()
            return {'ok': True, 'queued': True, 'operation': self._public(self.operation)}

    @staticmethod
    def _public(op):
        return {k: copy.deepcopy(op[k]) for k in ('id', 'kind', 'status', 'phase', 'error', 'code', 'created_at') if k in op}

    def snapshot(self):
        with self.lock:
            obs = {k: copy.deepcopy(v) for k, v in self.observation.items() if k not in ('processes', 'process_key')}
            op = self.operation
            obs.update(desired_state=self.desired, operation=self._public(op) if op else None,
                       self_healing=bool(op and op['status'] in ACTIVE),
                       auto_circuit=self.clock() < self.circuit_until, auto_fail_count=self.failures)
            return obs

    def _valid(self, op):
        return (self.operation is op and op['generation'] == self.generation
                and (op['kind'] == 'stop' or self.desired != 'stopped')
                and (op['kind'] == 'stop' or 'qq' in self.store.get_settings().get('enabled_platforms', [])))

    def _persist(self):
        if self.operation:
            self.store.set_setting('qq_last_operation', self._public(self.operation))

    def _act(self, op, fn):
        with self.lock:
            if not self._valid(op):
                raise ControlError('cancelled', 'QQ 操作已被更新的请求取消')
            return fn()

    def _finish(self, op, status='completed', code='', error=''):
        with self.lock:
            if self._valid(op):
                op.update(status=status, phase=status, code=code, error=error)
                self._persist()
                if status not in ('completed', 'cancelled'):
                    self.store.add_log('warning' if status in ('unknown', 'needs_scan') else 'error', 'qq', error)

    def _failure(self, op, result):
        kind = result.get('kind', 'failed')
        if kind in ('unknown', 'already_authenticated'):
            op.update(status='waiting', phase='confirming', deadline=self.clock()+180)
            op['unknown_effect'] = True
        else:
            self._finish(op, 'failed', kind, result.get('message', 'QQ 操作未成功'))

    def _begin_stop(self, op):
        scan = self.backend.process.scan()
        if not scan['known']:
            raise ControlError('process_unknown', '进程信息不可读，未停止或叠加启动 QQ')
        op.update(status='running', phase='stopping', anchors=scan['rows'], signalled=[], stop_at=self.clock())

    def _stop_tick(self, op):
        scan = self.backend.process.scan()
        if not scan['known']:
            raise ControlError('process_unknown', '无法确认 QQ 已退出，未叠加启动')
        rows = scan['rows']
        groups = {r['group'] for r in op['anchors'] if r['role'] == 'launcher'}
        mains = [r for r in rows if r['role'] == 'main']
        for record in mains:
            if record not in op['signalled']:
                self._act(op, lambda r=record: self.backend.process.signal(r, signal.SIGTERM))
                op['signalled'].append(record)
        elapsed = self.clock()-op['stop_at']
        if mains:
            if elapsed >= 15:
                for record in mains:
                    self._act(op, lambda r=record: self.backend.process.signal(r, KILL))
            if elapsed >= 22:
                raise ControlError('stop_unconfirmed', 'QQ 主进程仍未确认退出，已停止恢复操作')
            return
        leftovers = [r for r in rows if r['group'] in groups]
        if leftovers:
            if 'cleanup_at' not in op:
                op['cleanup_at'] = self.clock()
                self._act(op, lambda: self.backend.process.signal_group(op['anchors'], signal.SIGTERM))
            elif self.clock()-op['cleanup_at'] >= 2:
                self._act(op, lambda: self.backend.process.signal_group(op['anchors'], KILL))
            if elapsed >= 28:
                raise ControlError('stop_unconfirmed', 'QQ 会话组仍未退出，未启动新实例')
            return
        if any(r['role'] == 'launcher' for r in rows):
            raise ControlError('process_changed', 'QQ 启动器已变化，请重新确认状态')
        self.backend.webui.invalidate()
        if op['kind'] == 'stop':
            self._finish(op)
        else:
            self._launch(op, None if op['kind'] == 'refresh_qr' else op['target'])

    def _launch(self, op, target):
        scan = self.backend.process.scan()
        if not scan['known'] or any(r['role'] in ('main', 'launcher') for r in scan['rows']):
            raise ControlError('process_changed', 'QQ 进程未确认停止，未叠加启动')
        self._act(op, lambda: self.backend.spawn(target))
        op.update(status='waiting', phase='starting', deadline=self.clock()+180)

    def tick(self):
        obs = self.backend.observe()
        with self.lock:
            self.observation = obs
            op = self.operation
            if op and op['status'] in ACTIVE and op['kind'] != 'stop' and 'qq' not in self.store.get_settings().get('enabled_platforms', []):
                self.generation += 1
                op.update(status='cancelled', phase='cancelled', error='QQ 平台已禁用')
                self._persist()
        if op and op['status'] in (*ACTIVE, 'unknown'):
            try:
                self._advance(op, obs)
            except ControlError as exc:
                self._finish(op, 'cancelled' if exc.code == 'cancelled' else 'failed', exc.code, str(exc))
            except Exception as exc:
                self._finish(op, 'unknown' if op.get('config_submitted') else 'failed',
                             'controller_error', 'QQ 操作未获确认: '+type(exc).__name__)
            return
        if obs.get('online'):
            if self.stable_since is None:
                self.stable_since = self.clock()
            if self.clock()-self.stable_since >= 300:
                self.failures, self.circuit_until = 0, 0
        else:
            self.stable_since = None
        if (self.desired == 'stopped' or 'qq' not in self.store.get_settings().get('enabled_platforms', [])
                or self.clock()-self.born < 120 or self.clock() < self.circuit_until):
            return
        if op and op['status'] in ('failed', 'needs_scan', 'needs_verification'):
            return  # Human-visible outcome; never turn a rejected login into an automatic restart.
        if not obs['process_known'] or obs['login_accepted'] or obs['qr'] or obs['account_ready']:
            return
        if self.clock()-self.last_auto < 120:
            return
        if not obs['running'] and not obs['launching']:
            self.last_auto, self.failures = self.clock(), self.failures+1
            if self.failures >= 5:
                self.circuit_until = self.clock()+1800
                return
            self.request('start', manual=False)
        elif obs['account_state'] == 'offline' and self.clock()-self.last_auto >= 900:
            self.last_auto = self.clock()
            self.request('quick_login', manual=False)

    def _advance(self, op, obs):
        if not self._valid(op):
            return
        if op['phase'] == 'stopping':
            self._stop_tick(op)
            return
        kind = op['kind']
        if op['phase'] == 'checking' and obs.get('login_state_known'):
            op.update(status='queued', phase='queued')
        if op['phase'] == 'reconciling' and kind == 'stop':
            self._begin_stop(op)  # Resume the persisted stop intent using fresh process identities.
            return
        if op['phase'] == 'reconciling' and not obs['running'] and not obs['launching'] and obs['process_known']:
            self._finish(op, 'failed', 'interrupted', '上次 QQ 操作已中断，请检查状态后手动启动')
            return
        if op['status'] == 'queued':
            if kind in ('stop', 'restart'):
                self._begin_stop(op)
                return
            if obs['account_ready']:
                if op['target'] and obs.get('account') != op['target']:
                    raise ControlError('account_mismatch', '当前已登录账号与目标不同，请明确停止后再切换')
                if kind != 'configure':
                    self._finish(op)  # Already authenticated; do not restart to repair a transport.
                    return
            if obs['login_accepted']:
                op.update(status='waiting', phase='initializing')
            elif kind == 'refresh_qr':
                if not obs['running'] and not obs['launching']:
                    self._launch(op, None)
                else:
                    result = self._act(op, lambda: self.backend.webui.call('/api/QQLogin/RefreshQRcode', {}, mutation=True))
                    if result['ok']:
                        op.update(status='waiting', phase='generating_qrcode')
                    else:
                        self._failure(op, result)
                return
            elif kind == 'start':
                if not obs['running'] and not obs['launching']:
                    self._launch(op, op['target'])
                else:
                    op.update(status='waiting', phase='starting')
                return
            elif kind == 'quick_login':
                if not obs['running'] and not obs['launching']:
                    self._launch(op, op['target'])
                    return
                if not obs.get('login_state_known'):
                    op.update(status='waiting', phase='checking')
                    return  # Unknown account state cannot authorize a native login mutation.
                listed = self.backend.webui.call('/api/QQLogin/GetQuickLoginList', {})
                if not listed['ok']:
                    self._failure(op, listed)
                    return
                data = listed.get('data')
                if not isinstance(data, list):
                    raise ControlError('protocol', '快捷登录列表格式无效')
                users = [str(x.get('uin') or x.get('Uin')) if isinstance(x, dict) else str(x) for x in data]
                if op['target'] not in users:
                    self._finish(op, 'needs_scan', 'needs_scan', 'QQ 当前没有该账号的快捷登录候选，请扫码登录')
                    return
                result = self._act(op, lambda: self.backend.webui.call('/api/QQLogin/SetQuickLogin', {'uin': op['target']}, mutation=True))
                if not result['ok']:
                    self._failure(op, result)
                else:
                    op.update(status='waiting', phase='initializing', native_success=True)
                return
        if kind == 'configure' and obs['account_ready']:
            if op.get('config_submitted'):
                if self._act(op, self.backend.check_configuration):
                    self._finish(op)
                return
            if op['status'] == 'unknown':
                return
            op['config_submitted'] = True
            result = self.backend.configure(lambda write: self._act(op, write))
            if result['ok']:
                self._finish(op)
            else:
                # A timed-out configuration write must not be submitted again.
                self._finish(op, 'unknown' if result.get('kind') == 'unknown' else 'failed',
                             result.get('kind', 'failed'), result.get('message', '配置结果未确认'))
            return
        if obs['account_ready']:
            if op['target'] and obs.get('account') != op['target']:
                raise ControlError('account_mismatch', '登录结果账号与目标不同')
            self._finish(op)
        elif kind == 'refresh_qr' and obs['qr']:
            self._finish(op)
        elif obs['qr'] and op['phase'] in ('starting', 'confirming', 'initializing'):
            self._finish(op, 'needs_scan', 'needs_scan', 'QQ 已提供二维码，请扫码确认')
        elif self.clock() >= op['deadline'] and op['status'] != 'unknown':
            self._finish(op, 'unknown', 'result_unknown', '操作结果尚未确认，保留会话并继续观察；未重复登录')

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception as exc:
                self.store.add_log('error', 'qq', 'QQ 操作管理器检查失败: '+type(exc).__name__)
            self.wake_event.wait(2)
            self.wake_event.clear()
