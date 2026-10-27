// login.dart — QQ 扫码登录页：显示 NapCat 二维码，轮询登录状态
import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api.dart';

class LoginPage extends StatefulWidget {
  final ApiClient api;
  const LoginPage({super.key, required this.api});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  Uint8List? _qr;
  bool _loading = true;
  bool _loggedIn = false;
  String _error = '';
  int _qrAge = -1; // 二维码生成至今秒数（/qq/status 的 qr_age，-1=还没有码）
  bool _appliedOnebot = false;
  bool _configuring = false; // 已扫码登录、正在写 OneBot 配置并重启 QQ
  int _configuringAt = 0; // _configuring 置位时刻（ms），超时后强制复位
  Timer? _timer;
  bool _quickLoginConfigured = false; // /qq/status 的 quick_login_configured
  bool _selfHealing = false; // /qq/status 的 self_healing（快捷登录在途）

  @override
  void initState() {
    super.initState();
    _tick();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _tick());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _tick() async {
    bool running = false;
    bool qr = false;
    bool online = false;
    bool stale = false;
    String loginReason = 'unknown';
    try {
      final q = await widget.api.qqStatus();
      running = q['running'] == true;
      qr = q['qr'] == true;
      online = q['online'] == true;
      stale = q['stale'] == true;
      loginReason = (q['login_reason'] as String?) ?? 'unknown';
      _qrAge = (q['qr_age'] as num?)?.toInt() ?? -1;
      _quickLoginConfigured = q['quick_login_configured'] == true;
      _selfHealing = q['self_healing'] == true;
      final loginUin = (q['login_uin'] as String?) ?? '';
      // 扫码登录成功（NapCat 已生成 onebot11_<uin>.json）但 OneBot WS 还没连上 → 写配置 + 重启 QQ
      if (loginUin.isNotEmpty && !online && !_appliedOnebot) {
        _appliedOnebot = true;
        _configuring = true;
        _configuringAt = DateTime.now().millisecondsSinceEpoch;
        _applyOnebot();
      }
      if (online || qr) {
        _configuring = false; // 重启完成：已在线或已出码
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _qr = null;
          _loggedIn = false;
          _error = '连不上鱼脑（先回「部署」页启动鱼脑和 QQ）';
        });
      }
      return;
    }

    if (online) {
      if (mounted) {
        setState(() {
          _loading = false;
          _qr = null;
          _loggedIn = true;
          _error = '';
        });
      }
      if (!_appliedOnebot) {
        _appliedOnebot = true;
        _applyOnebot();
      }
      return;
    }

    if (_configuring &&
        DateTime.now().millisecondsSinceEpoch - _configuringAt > 90000) {
      _configuring = false; // 重启超时（约 90s），透出真实状态而非永久卡在「配置中」
    }
    if (_configuring) {
      if (mounted) {
        setState(() {
          _loading = false;
          _qr = null;
          _loggedIn = false;
          _error = 'QQ 已登录，正在写入 OneBot 配置并重启 QQ，请稍候（约 1 分钟）…';
        });
      }
      return;
    }

    if (!running) {
      if (mounted) {
        setState(() {
          _loading = false;
          _qr = null;
          _loggedIn = false;
          _error = 'QQ 还没启动';
        });
      }
      return;
    }

    if (!qr) {
      if (mounted) {
        setState(() {
          _loading = false;
          _qr = null;
          _loggedIn = false;
          if (stale) {
            _error = _selfHealing
                ? 'QQ 掉线，正在快捷登录自愈…'
                : (_quickLoginConfigured
                    ? 'QQ 掉线（${_reasonLabel(loginReason)}），正在尝试快捷登录自愈…'
                    : 'QQ 掉线（${_reasonLabel(loginReason)}），鱼脑会自动重连/出码，请稍候');
          } else {
            _error = _qrAge >= 0
                ? '二维码已过期（$_qrAge 秒前生成），点「重新出码」换个新的'
                : 'QQ 运行中，还没出二维码，稍等…';
          }
        });
      }
      return;
    }

    try {
      final bytes = await widget.api.qrcode();
      if (mounted) {
        setState(() {
          _qr = bytes;
          _loading = false;
          _loggedIn = false;
          _error = '';
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _qr = null;
          _error = '读不到二维码（NapCat 还没生成，稍等重试）';
        });
      }
    }
  }

  Future<void> _applyOnebot() async {
    try {
      await widget.api.applyOnebot();
    } catch (_) {}
  }

  String _reasonLabel(String reason) {
    if (reason.startsWith('login_error_')) {
      return '登录报错(码${reason.substring('login_error_'.length)})';
    }
    switch (reason) {
      case 'nt_timeout':
        return 'QQ 内核超时';
      case 'offline':
        return '账号离线';
      case 'waiting_scan':
        return '待扫码';
      case 'login_service_dropped':
        return '登录服务断连';
      case 'risk':
        return '风控';
      default:
        return '未知';
    }
  }

  Future<void> _restartQq() async {
    try {
      await widget.api.qqRefreshQr();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('已重启 QQ，约 20 秒后出新码')),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('重新出码失败（鱼脑在线吗？）')),
        );
      }
    }
    _tick();
  }

  Future<void> _quickLogin() async {
    try {
      final r = await widget.api.qqQuickLogin();
      if (mounted) {
        final err = (r['error'] as String?) ?? '';
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(err.isNotEmpty ? err : '已发起快捷登录')),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('快捷登录失败（鱼脑在线吗？）')),
        );
      }
    }
    _tick();
  }

  Widget _quickLoginButton() {
    return OutlinedButton.icon(
      onPressed: _quickLogin,
      icon: const Icon(Icons.bolt),
      label: const Text('快速登录（免扫码）'),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('扫码登录 QQ')),
      body: Center(
        child: _loading
            ? const CircularProgressIndicator()
            : _loggedIn
                ? const Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.check_circle, color: Colors.green, size: 64),
                      SizedBox(height: 16),
                      Text('QQ 已登录', style: TextStyle(fontSize: 18)),
                    ],
                  )
                : _qr != null
                    ? Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Text('用手机 QQ 扫这个码登录'),
                          const SizedBox(height: 16),
                          Image.memory(_qr!, width: 240, height: 240),
                          const SizedBox(height: 8),
                          Text('二维码 ${_qrAge >= 0 ? _qrAge : 0} 秒前生成 · 约 2 分钟内有效，请尽快扫'),
                          const SizedBox(height: 8),
                          const Text('扫码后勾「下次免确认」，登录成功会自动跳转'),
                          const SizedBox(height: 16),
                          OutlinedButton.icon(
                            onPressed: _restartQq,
                            icon: const Icon(Icons.restart_alt),
                            label: const Text('重新出码（重启 QQ）'),
                          ),
                          if (_quickLoginConfigured) ...[
                            const SizedBox(height: 8),
                            _quickLoginButton(),
                          ],
                        ],
                      )
                    : Padding(
                        padding: const EdgeInsets.all(24),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(_error.isEmpty ? '等待二维码…' : _error,
                                textAlign: TextAlign.center),
                            if (_qrAge >= 0) ...[
                              const SizedBox(height: 16),
                              OutlinedButton.icon(
                                onPressed: _restartQq,
                                icon: const Icon(Icons.restart_alt),
                                label: const Text('重新出码（重启 QQ）'),
                              ),
                            ],
                            if (_quickLoginConfigured) ...[
                              const SizedBox(height: 8),
                              _quickLoginButton(),
                            ],
                          ],
                        ),
                      ),
      ),
    );
  }
}
