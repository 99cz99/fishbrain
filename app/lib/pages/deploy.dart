// deploy.dart — 部署/控制页：启动鱼脑、启动/停止 QQ、扫码登录
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
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
  QQControlStatus _qqControl = QQControlStatus({});
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

  bool _refreshing = false;

  Future<void> _refresh() async {
    if (_refreshing || !mounted) return;
    _refreshing = true;
    try {
      var fishUp = false;
      var qqRunning = false;
      var qqQr = false;
      var qqOnline = false;
      var qqStale = false;
      var control = QQControlStatus({});
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
        control = QQControlStatus(q);
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
        _qqControl = control;
        _qqAutoCircuit = qqAutoCircuit;
        _heyboxEnabled = heyboxEnabled;
      });
    } finally {
      _refreshing = false;
    }
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
      if (mounted) {
        setState(
          () => _qqControl = QQControlStatus({..._qqControl.data, ...r}),
        );
      }
      _snack('启动请求已提交，等待 QQ 状态确认');
    } catch (error) {
      _snack(apiErrorMessage(error));
    }
    await Future.delayed(const Duration(seconds: 3));
    _refresh();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _stopQq() async {
    setState(() => _busy = true);
    try {
      final r = await widget.api.qqStop();
      if (mounted) {
        setState(
          () => _qqControl = QQControlStatus({..._qqControl.data, ...r}),
        );
      }
      _snack('停止请求已提交，等待进程退出');
    } catch (error) {
      _snack(apiErrorMessage(error));
    }
    _refresh();
    if (mounted) setState(() => _busy = false);
  }

  String _qqStatusLabel() {
    if (_qqControl.busy) return '操作进行中';
    if (!_qqRunning) return '未运行';
    if (_qqOnline) return '在线';
    if (_qqControl.authenticated) return '已登录，待连接';
    if (_qqQr) return '待扫码';
    if (_qqStale) return '登录状态待确认';
    return '运行中';
  }

  Color _qqStatusColor() {
    if (_qqOnline) return AppColors.success;
    if (_qqQr || _qqStale) return AppColors.warning;
    return AppColors.textSecondaryLight;
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
              if (_qqControl.modern || (_qqRunning && _qqStale))
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: _hint(_qqControl.message, color: AppColors.warning),
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
                onPressed: (_busy || !_fishUp || _qqControl.busy)
                    ? null
                    : _startQq,
                icon: const Icon(Icons.chat),
                label: const Text('启动 QQ'),
              ),
              const SizedBox(height: 10),
              OutlinedButton.icon(
                onPressed:
                    (_busy ||
                        !_fishUp ||
                        _qqControl.stopping ||
                        (!_qqRunning && !_qqControl.busy))
                    ? null
                    : _stopQq,
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
                label: const Text('部署向导（安装 / 更新）'),
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
