// deploy.dart — 部署/控制页：启动鱼脑、启动/停止 QQ、扫码登录
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../termux.dart';
import '../theme.dart';
import '../widgets.dart';
import 'login.dart';
import 'wizard.dart';

class DeployPage extends StatefulWidget {
  final ApiClient api;
  const DeployPage({super.key, required this.api});

  @override
  State<DeployPage> createState() => _DeployPageState();
}

class _DeployPageState extends State<DeployPage> {
  final TermuxRunner _termux = TermuxRunner();
  bool _fishUp = false;
  bool _qqRunning = false;
  bool _qqQr = false;
  bool _qqOnline = false;
  bool _qqStale = false;
  String _loginReason = 'unknown';
  bool _qqAutoCircuit = false;
  bool _heyboxEnabled = false;
  bool _busy = false;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    var fishUp = false;
    var qqRunning = false;
    var qqQr = false;
    var qqOnline = false;
    var qqStale = false;
    var loginReason = 'unknown';
    var qqAutoCircuit = false;
    var heyboxEnabled = false;
    try {
      await widget.api.status();
      fishUp = true;
    } catch (_) {}
    try {
      final q = await widget.api.qqStatus();
      qqRunning = q['running'] == true;
      qqQr = q['qr'] == true;
      qqOnline = q['online'] == true;
      qqStale = q['stale'] == true;
      loginReason = (q['login_reason'] as String?) ?? 'unknown';
      qqAutoCircuit = q['auto_circuit'] == true;
    } catch (_) {}
    try {
      final hb = await widget.api.heyboxStatus();
      heyboxEnabled = hb['enabled'] == true;
    } catch (_) {}
    if (!mounted) return;
    setState(() {
      _fishUp = fishUp;
      _qqRunning = qqRunning;
      _qqQr = qqQr;
      _qqOnline = qqOnline;
      _qqStale = qqStale;
      _loginReason = loginReason;
      _qqAutoCircuit = qqAutoCircuit;
      _heyboxEnabled = heyboxEnabled;
    });
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _startFish() async {
    setState(() => _busy = true);
    final ok = await _termux.run(
      'sh /data/data/com.termux/files/home/dfy/run-fish.sh',
      workdir: '/data/data/com.termux/files/home/dfy',
    );
    _snack(ok ? '已发送启动指令，稍等几秒' : '启动指令发送失败（Termux 装了吗？）');
    await Future.delayed(const Duration(seconds: 3));
    _refresh();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _startQq() async {
    setState(() => _busy = true);
    try {
      final r = await widget.api.qqStart();
      _snack(r['already'] == true ? 'QQ 已经在跑了' : '已发起启动 QQ，稍等十几秒');
    } catch (_) {
      _snack('启动 QQ 失败（鱼脑在线吗？）');
    }
    await Future.delayed(const Duration(seconds: 3));
    _refresh();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _stopQq() async {
    setState(() => _busy = true);
    try {
      await widget.api.qqStop();
      _snack('已停止 QQ');
    } catch (_) {
      _snack('停止 QQ 失败');
    }
    _refresh();
    if (mounted) setState(() => _busy = false);
  }

  String _qqStatusLabel() {
    if (!_qqRunning) return '未运行';
    if (_qqOnline) return '在线';
    if (_qqQr) return '待扫码';
    if (_qqStale) return '掉线（自愈中）';
    return '运行中';
  }

  Color _qqStatusColor() {
    if (_qqOnline) return AppColors.success;
    if (_qqQr || _qqStale) return AppColors.warning;
    return AppColors.textSecondaryLight;
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

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('部署')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 120),
        children: [
          SectionHeader('组件状态'),
          SectionCard(
            children: [
              _statusRow(
                '鱼脑 server.py',
                color: _fishUp
                    ? AppColors.success
                    : AppColors.textSecondaryLight,
                value: _fishUp ? '在线' : '未运行',
              ),
              const Divider(height: 20),
              _statusRow(
                'QQ / NapCat',
                color: _qqStatusColor(),
                value: _qqStatusLabel(),
              ),
              const Divider(height: 20),
              _statusRow(
                '小黑盒',
                color: _heyboxEnabled
                    ? AppColors.success
                    : AppColors.textSecondaryLight,
                value: _heyboxEnabled ? '已接入' : '未接入',
              ),
              if (_qqRunning && _qqQr && !_qqOnline)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: _hint('有待扫码登录的二维码，点「扫码登录」', color: AppColors.warning),
                ),
              if (_qqRunning && _qqStale)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: _hint(
                    'QQ 掉线（${_reasonLabel(_loginReason)}），鱼脑会自动重启重连',
                    color: AppColors.warning,
                  ),
                ),
              if (_qqAutoCircuit)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: _hint(
                    '自动拉起已熔断：QQ 连续拉起失败，鱼脑已暂停自动重连。点「启动 QQ」重置，并排查 proot 环境',
                    color: AppColors.danger,
                  ),
                ),
            ],
          ),

          const SizedBox(height: 20),
          SectionHeader('操作'),
          SectionCard(
            children: [
              if (!_fishUp) ...[
                FilledButton.icon(
                  onPressed: _busy ? null : _startFish,
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('启动鱼脑'),
                ),
                const SizedBox(height: 10),
              ],
              FilledButton.tonalIcon(
                onPressed: (_busy || !_fishUp) ? null : _startQq,
                icon: const Icon(Icons.chat),
                label: const Text('启动 QQ'),
              ),
              const SizedBox(height: 10),
              OutlinedButton.icon(
                onPressed: (_busy || !_qqRunning) ? null : _stopQq,
                icon: const Icon(Icons.stop),
                label: const Text('停止 QQ'),
              ),
              const SizedBox(height: 10),
              FilledButton.icon(
                onPressed: (_busy || !_qqRunning) ? null : _openLogin,
                icon: const Icon(Icons.qr_code_scanner),
                label: const Text('扫码登录 QQ'),
              ),
              const SizedBox(height: 10),
              OutlinedButton.icon(
                onPressed: _busy ? null : _openWizard,
                icon: const Icon(Icons.auto_fix_high),
                label: const Text('部署向导（全新部署）'),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            '「启动/停止」这里只控制 QQ，鱼脑的启停去「状态」页操作',
            style: theme.textTheme.labelSmall,
          ),
        ],
      ),
    );
  }

  Widget _statusRow(
    String label, {
    required Color color,
    required String value,
  }) {
    return Row(
      children: [
        Expanded(
          child: Text(label, style: Theme.of(context).textTheme.bodyMedium),
        ),
        StatusPill(color: color, label: value),
      ],
    );
  }

  Widget _hint(String text, {required Color color}) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(Icons.info_outline, size: 16, color: color),
        const SizedBox(width: 6),
        Expanded(
          child: Text(text, style: TextStyle(color: color, fontSize: 13)),
        ),
      ],
    );
  }

  void _openLogin() {
    Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => LoginPage(api: widget.api)));
  }

  void _openWizard() {
    Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => WizardPage(api: widget.api)));
  }
}
