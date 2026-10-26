// status.dart — 状态页：看它在干嘛 + 活动流 + 控制（启动/停止/重启/下指令）
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../models.dart';
import '../termux.dart';

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
    if (epochSec <= 0) return '随时会醒';
    final diff =
        DateTime.fromMillisecondsSinceEpoch(epochSec * 1000).difference(DateTime.now());
    if (diff.inSeconds <= 0) return '马上';
    if (diff.inMinutes < 60) return '${diff.inMinutes} 分钟后';
    if (diff.inHours < 24) return '${diff.inHours} 小时后';
    return '${diff.inDays} 天后';
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _exportLogs() async {
    final visible = _logs.where((l) => !_onlyErrors || l.level == 'error').toList();
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
    await _termux.run("pkill -f '[s]erver.py'; pkill -f '[r]un-fish.sh'; rm -f /data/data/com.termux/files/home/dfy/.fish.pid");
    _snack('已停止（QQ 保持在线）');
    _refresh();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _restartFish() async {
    setState(() => _busy = true);
    await _termux.run("pkill -f '[s]erver.py'; pkill -f '[r]un-fish.sh'; rm -f /data/data/com.termux/files/home/dfy/.fish.pid");
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

  @override
  Widget build(BuildContext context) {
    final s = _status;
    return Scaffold(
      appBar: AppBar(title: const Text('状态')),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (!_connected)
              const Card(
                child: ListTile(
                  leading: Icon(Icons.cloud_off),
                  title: Text('连接不到鱼脑'),
                  subtitle: Text('确认鱼脑已在手机上运行（127.0.0.1:8765）'),
                ),
              ),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text((s?.name ?? '').isEmpty ? '未命名' : s!.name,
                        style: Theme.of(context).textTheme.headlineSmall),
                    const SizedBox(height: 8),
                    Text('状态：${_stateLabel(s?.state ?? 'idle')}'),
                    if ((s?.currentTask ?? '').isNotEmpty)
                      Text('当前任务：${s?.currentTask}'),
                    Text('运行时长：${_fmtUptime(s?.uptime ?? 0)}'),
                    if (_usage != null)
                      Text('Token 用量：${_usage!['total_tokens'] ?? 0}（调用 ${_usage!['calls'] ?? 0} 次）'),
                  ],
                ),
              ),
            ),
            if (_life != null)
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Icon(Icons.favorite, size: 18, color: Colors.pink),
                          const SizedBox(width: 6),
                          Text('它的心', style: Theme.of(context).textTheme.titleMedium),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text('心情：${(_life!.mood.isEmpty) ? '（还没醒过）' : _life!.mood}'),
                      Text('下次醒来：${_fmtNextWake(_life!.nextWakeAt)}'),
                      if (_life!.paused) const Text('（已叫停，不主动动）'),
                      const SizedBox(height: 8),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: FilledButton.tonalIcon(
                          onPressed: _wake,
                          icon: const Icon(Icons.wb_sunny_outlined),
                          label: const Text('叫醒'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: _busy ? null : _startFish,
                    icon: const Icon(Icons.play_arrow),
                    label: const Text('启动'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: FilledButton.tonalIcon(
                    onPressed: _busy ? null : _stopFish,
                    icon: const Icon(Icons.stop),
                    label: const Text('停止'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _busy ? null : _restartFish,
                    icon: const Icon(Icons.restart_alt),
                    label: const Text('重启'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _cmd,
                    decoration: const InputDecoration(
                      hintText: '给它下个指令…',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  onPressed: () {
                    if (_cmd.text.trim().isNotEmpty) {
                      _sendCommand(_cmd.text.trim());
                      _cmd.clear();
                    }
                  },
                  icon: const Icon(Icons.send),
                ),
              ],
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Text('活动', style: Theme.of(context).textTheme.titleMedium),
                const Spacer(),
                FilterChip(
                  label: const Text('只看报错'),
                  selected: _onlyErrors,
                  onSelected: (v) => setState(() => _onlyErrors = v),
                ),
                const SizedBox(width: 8),
                IconButton(
                  onPressed: _exportLogs,
                  icon: const Icon(Icons.copy),
                  tooltip: '导出日志',
                ),
              ],
            ),
            const SizedBox(height: 8),
            ..._logs.where((l) => !_onlyErrors || l.level == 'error').map((l) => ListTile(
                  dense: true,
                  leading: Icon(_iconFor(l.category), size: 20),
                  title: Text(l.message),
                  subtitle: Text(l.ts),
                )),
          ],
        ),
      ),
    );
  }
}
