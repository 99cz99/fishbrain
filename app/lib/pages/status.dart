// status.dart — 状态页：看它在干嘛 + 活动流 + 控制（启动/停止/重启/下指令）
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../models.dart';
import '../termux.dart';
import '../theme.dart';
import '../widgets.dart';

class StatusPage extends StatefulWidget {
  final ApiClient api;
  const StatusPage({super.key, required this.api});

  @override
  State<StatusPage> createState() => _StatusPageState();
}

class _StatusPageState extends State<StatusPage> {
  Status? _status;
  List<LogEntry> _logs = [];
  bool _connected = false;
  Map<String, dynamic>? _usage;
  LifeState? _life;
  Timer? _timer;
  final TextEditingController _cmd = TextEditingController();
  final TermuxRunner _termux = TermuxRunner();
  bool _busy = false;
  bool _onlyErrors = false;

  @override
  void initState() {
    super.initState();
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 4), (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _cmd.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final s = await widget.api.status();
      final logs = await widget.api.logs(limit: 20);
      Map<String, dynamic>? usage;
      try {
        usage = await widget.api.usage();
      } catch (_) {}
      LifeState? life;
      try {
        life = LifeState.fromJson(await widget.api.lifeState());
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _status = s;
        _logs = logs;
        _usage = usage;
        _life = life;
        _connected = true;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _connected = false);
    }
  }

  Future<void> _sendCommand(String cmd) async {
    try {
      await widget.api.command(cmd);
      _refresh();
    } catch (_) {}
  }

  Future<void> _wake() async {
    try {
      await widget.api.wake();
      _snack('已叫醒它');
      _refresh();
    } catch (_) {
      _snack('叫醒失败');
    }
  }

  String _fmtNextWake(int epochSec) {
    if (epochSec <= 0) return '随时';
    final diff = DateTime.fromMillisecondsSinceEpoch(epochSec * 1000)
        .difference(DateTime.now());
    if (diff.inSeconds <= 0) return '现在';
    if (diff.inSeconds < 60) return '${diff.inSeconds}s';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m';
    if (diff.inHours < 24) return '${diff.inHours}h';
    return '${diff.inDays}d';
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _exportLogs() async {
    final visible = _logs
        .where((l) => !_onlyErrors || l.level == 'error')
        .toList();
    final text = visible
        .map((l) => '${l.ts} [${l.level}/${l.category}] ${l.message}')
        .join('\n');
    await Clipboard.setData(ClipboardData(text: text));
    _snack('已复制 ${visible.length} 条日志到剪贴板');
  }

  Future<void> _startFish() async {
    setState(() => _busy = true);
    final ok = await _termux.run(
      'sh /data/data/com.termux/files/home/dfy/run-fish.sh',
      workdir: '/data/data/com.termux/files/home/dfy',
    );
    await Future.delayed(const Duration(seconds: 3));
    try {
      await widget.api.qqStart();
      _snack(ok ? '已启动，QQ 稍等十几秒' : '启动指令发送失败（Termux 装了吗？）');
    } catch (_) {
      _snack(ok ? '鱼脑已启动（QQ 启动失败）' : '启动指令发送失败（Termux 装了吗？）');
    }
    _refresh();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _stopFish() async {
    setState(() => _busy = true);
    await _termux.run(
      "pkill -f '[s]erver.py'; pkill -f '[r]un-fish.sh'; rm -f /data/data/com.termux/files/home/dfy/.fish.pid",
    );
    _snack('已停止（QQ 保持在线）');
    _refresh();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _restartFish() async {
    setState(() => _busy = true);
    await _termux.run(
      "pkill -f '[s]erver.py'; pkill -f '[r]un-fish.sh'; rm -f /data/data/com.termux/files/home/dfy/.fish.pid",
    );
    await Future.delayed(const Duration(seconds: 1));
    await _termux.run(
      'sh /data/data/com.termux/files/home/dfy/run-fish.sh',
      workdir: '/data/data/com.termux/files/home/dfy',
    );
    await Future.delayed(const Duration(seconds: 3));
    _snack('已重启（QQ 保持在线）');
    _refresh();
    if (mounted) setState(() => _busy = false);
  }

  String _stateLabel(String s) => switch (s) {
    'idle' => '空闲',
    'working' => '干活中',
    'paused' => '已暂停',
    _ => s,
  };

  Color _stateColor(String s) => switch (s) {
    'working' => AppColors.primaryLight,
    'paused' => AppColors.warning,
    'idle' => AppColors.success,
    _ => AppColors.textSecondaryLight,
  };

  String _fmtUptime(int sec) {
    final h = sec ~/ 3600, m = (sec % 3600) ~/ 60, s = sec % 60;
    return h > 0 ? '${h}h ${m}m' : '${m}m ${s}s';
  }

  IconData _iconFor(String c) => switch (c) {
    'chat' => Icons.chat_bubble_outline,
    'action' => Icons.touch_app_outlined,
    'approval' => Icons.fact_check_outlined,
    'error' => Icons.error_outline,
    _ => Icons.circle,
  };

  Color _iconColorFor(String c) => switch (c) {
    'error' => AppColors.danger,
    'approval' => AppColors.warning,
    'action' => AppColors.primaryLight,
    _ => AppColors.textSecondaryLight,
  };

  @override
  Widget build(BuildContext context) {
    final s = _status;
    final visible = _logs
        .where((l) => !_onlyErrors || l.level == 'error')
        .toList();
    return Scaffold(
      appBar: AppBar(title: const Text('状态')),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 120),
          children: [
            if (!_connected) ...[
              SectionCard(
                padding: const EdgeInsets.all(16),
                children: [
                  Row(
                    children: [
                      const Icon(Icons.cloud_off, color: AppColors.danger),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '连接不到鱼脑',
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            Text(
                              '确认鱼脑已在手机上运行（127.0.0.1:8765）',
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ],
              ),
              const SizedBox(height: 12),
            ],

            // 生命体画像：大标题 hero + 心情 + KPI 统计条
            SectionCard(
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        (s?.name ?? '').isEmpty ? '未命名' : s!.name,
                        style: Theme.of(context).textTheme.headlineLarge,
                      ),
                    ),
                    StatusPill(
                      color: _stateColor(s?.state ?? 'idle'),
                      label: _stateLabel(s?.state ?? 'idle'),
                    ),
                  ],
                ),
                if (_life != null) ...[
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      const Icon(
                        Icons.favorite,
                        size: 16,
                        color: AppColors.danger,
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          _life!.mood.isEmpty
                              ? '还没醒过'
                              : '${_life!.mood}${_life!.paused ? ' · 已叫停' : ''}',
                          style: Theme.of(context).textTheme.bodyMedium,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                ],
                if ((s?.currentTask ?? '').isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(
                    '正在：${s!.currentTask}',
                    style: Theme.of(context).textTheme.bodySmall,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: StatTile(
                        label: '时长',
                        value: _fmtUptime(s?.uptime ?? 0),
                        icon: Icons.schedule,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: StatTile(
                        label: 'Token',
                        value: '${_usage?['total_tokens'] ?? 0}',
                        icon: Icons.data_usage,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: StatTile(
                        label: '下次',
                        value: _life != null
                            ? _fmtNextWake(_life!.nextWakeAt)
                            : '—',
                        icon: Icons.alarm,
                      ),
                    ),
                  ],
                ),
                if (_life != null) ...[
                  const SizedBox(height: 12),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: FilledButton.tonalIcon(
                      onPressed: _wake,
                      icon: const Icon(Icons.wb_sunny_outlined),
                      label: const Text('叫醒'),
                    ),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 12),

            // 控制
            Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: _busy ? null : _startFish,
                    icon: const Icon(Icons.play_arrow),
                    label: const Text('启动'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: FilledButton.tonalIcon(
                    onPressed: _busy ? null : _stopFish,
                    icon: const Icon(Icons.stop),
                    label: const Text('停止'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _busy ? null : _restartFish,
                    icon: const Icon(Icons.restart_alt),
                    label: const Text('重启'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // 下指令
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _cmd,
                    textInputAction: TextInputAction.send,
                    onSubmitted: (v) {
                      if (v.trim().isNotEmpty) {
                        _sendCommand(v.trim());
                        _cmd.clear();
                      }
                    },
                    decoration: const InputDecoration(hintText: '给它下个指令…'),
                  ),
                ),
                const SizedBox(width: 10),
                IconButton.filled(
                  onPressed: () {
                    if (_cmd.text.trim().isNotEmpty) {
                      _sendCommand(_cmd.text.trim());
                      _cmd.clear();
                    }
                  },
                  icon: const Icon(Icons.arrow_upward),
                ),
              ],
            ),

            const SizedBox(height: 20),

            // 活动流
            Row(
              children: [
                Text('活动', style: Theme.of(context).textTheme.titleMedium),
                const Spacer(),
                FilterChip(
                  label: const Text('只看报错'),
                  selected: _onlyErrors,
                  onSelected: (v) => setState(() => _onlyErrors = v),
                ),
                const SizedBox(width: 6),
                IconButton(
                  onPressed: _exportLogs,
                  icon: const Icon(Icons.copy_outlined),
                  tooltip: '导出日志',
                ),
              ],
            ),
            const SizedBox(height: 4),
            if (visible.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 24),
                child: EmptyState(icon: Icons.inbox_outlined, title: '暂无活动'),
              )
            else
              SectionCard(
                padding: const EdgeInsets.symmetric(vertical: 6),
                children: [for (final l in visible) _logTile(l)],
              ),
          ],
        ),
      ),
    );
  }

  Widget _logTile(LogEntry l) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            _iconFor(l.category),
            size: 20,
            color: _iconColorFor(l.category),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l.message, style: Theme.of(context).textTheme.bodyMedium),
                const SizedBox(height: 2),
                Text(l.ts, style: Theme.of(context).textTheme.labelSmall),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
