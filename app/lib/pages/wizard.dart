// wizard.dart — 部署向导：装 Termux → 装鱼脑 → 填 key → 装 QQ → 扫码登录
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../termux.dart';
import '../theme.dart';
import '../widgets.dart';
import 'login.dart';

const String _home = '/data/data/com.termux/files/home';
const String _dfyDir = '$_home/dfy';

class WizardPage extends StatefulWidget {
  final ApiClient api;
  const WizardPage({super.key, required this.api});

  @override
  State<WizardPage> createState() => _WizardPageState();
}

class _WizardPageState extends State<WizardPage> {
  final TermuxRunner _termux = TermuxRunner();
  int _step = 0;
  bool _busy = false;
  String _msg = '';
  String _log = '';
  double _progress = -1;
  Map<String, dynamic>? _env;

  // step 2: LLM
  String _provider = 'openai';
  final _name = TextEditingController();
  final _key = TextEditingController();
  final _base = TextEditingController(text: 'https://api.deepseek.com/v1');
  final _model = TextEditingController(text: 'deepseek-chat');

  // step 3: QQ 部署进度
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    _checkEnv();
  }

  @override
  void dispose() {
    _poll?.cancel();
    _name.dispose();
    _key.dispose();
    _base.dispose();
    _model.dispose();
    super.dispose();
  }

  void _logLn(String s) {
    setState(() => _log = (_log.isEmpty ? '' : '$_log\n') + s);
  }

  String _tail(String s, [int max = 3000]) {
    if (s.length <= max) return s;
    return '…(输出过长已截断)\n${s.substring(s.length - max)}';
  }

  // ---- 环境预检（Termux 是否装、RUN_COMMAND 权限是否授）----
  Future<void> _checkEnv() async {
    final p = await _termux.preflight();
    if (!mounted) return;
    setState(() => _env = p);
  }

  Future<void> _grantPermission() async {
    await _termux.requestPermission();
    // 授权弹窗是异步的：轮询直到授权成功或超时，避免用户点完「允许」后界面还显示红 ✗。
    for (var i = 0; i < 30; i++) {
      await Future.delayed(const Duration(milliseconds: 500));
      final p = await _termux.preflight();
      if (p != null && p['runCommandGranted'] == true) break;
    }
    await _checkEnv();
  }

  Widget _envCheckCard() {
    final env = _env;
    return SectionCard(
      children: [
        Row(
          children: [
            const Icon(Icons.fact_check_outlined, size: 18),
            const SizedBox(width: 6),
            Text('环境检查', style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            IconButton(
              onPressed: _checkEnv,
              icon: const Icon(Icons.refresh),
              tooltip: '重新检查',
            ),
          ],
        ),
        const SizedBox(height: 8),
        _envRow(
          'Termux 已安装',
          env == null ? null : env['termuxInstalled'] == true,
          '去 GitHub/F-Droid 装 Termux 并打开一次（别用 Play 商店版）',
        ),
        _envRow(
          '「在 Termux 里运行命令」权限',
          env == null ? null : env['runCommandGranted'] == true,
          '点下面「授权限」，不行就去系统设置手动开',
        ),
        if (env != null && env['runCommandGranted'] != true) ...[
          const SizedBox(height: 8),
          FilledButton.tonalIcon(
            onPressed: _grantPermission,
            icon: const Icon(Icons.lock_open),
            label: const Text('授权限'),
          ),
          TextButton(
            onPressed: _termux.openAppSettings,
            child: const Text('去系统设置手动授权'),
          ),
        ],
      ],
    );
  }

  Widget _envRow(String label, bool? ok, String fixHint) {
    final IconData icon;
    final Color color;
    if (ok == null) {
      icon = Icons.hourglass_empty;
      color = Colors.grey;
    } else if (ok) {
      icon = Icons.check_circle;
      color = Colors.green;
    } else {
      icon = Icons.cancel;
      color = Colors.red;
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, color: color, size: 18),
              const SizedBox(width: 8),
              Expanded(child: Text(label)),
            ],
          ),
          if (ok == false)
            Padding(
              padding: const EdgeInsets.only(left: 26, top: 2),
              child: Text(
                fixHint,
                style: const TextStyle(color: Colors.red, fontSize: 12),
              ),
            ),
        ],
      ),
    );
  }

  // ---- Step 1: 装鱼脑 ----
  Future<void> _installFish() async {
    setState(() => _busy = true);
    _log = '';
    _logLn('写入鱼脑代码…');
    const files = {
      'server.py': 'assets/fish/server.py',
      'brain.py': 'assets/fish/brain.py',
      'memory.py': 'assets/fish/memory.py',
      'qq_bot.py': 'assets/fish/qq_bot.py',
      'life.py': 'assets/fish/life.py',
      'autonomy.py': 'assets/fish/autonomy.py',
      'heybox.py': 'assets/fish/heybox.py',
      'weather.py': 'assets/fish/weather.py',
    };
    var failed = 0;
    for (final e in files.entries) {
      final content = await rootBundle.loadString(e.value);
      final ok = await _termux.writeFile('$_dfyDir/${e.key}', content);
      if (!ok) {
        failed++;
        _logLn('写入 ${e.key} 失败');
      }
    }
    const cfg =
        '[qq]\nws_url = "ws://127.0.0.1:8081"\nbackend = "napcat"\n[heybox]\ntoken = ""\n';
    if (!await _termux.writeFile('$_dfyDir/config.toml', cfg)) {
      failed++;
      _logLn('写入 config.toml 失败');
    }
    final sh = await rootBundle.loadString('assets/deploy/setup-fish.sh');
    if (!await _termux.writeFile('$_dfyDir/setup-fish.sh', sh)) {
      failed++;
      _logLn('写入 setup-fish.sh 失败');
    }
    final rf = await rootBundle.loadString('assets/deploy/run-fish.sh');
    if (!await _termux.writeFile('$_dfyDir/run-fish.sh', rf)) {
      failed++;
      _logLn('写入 run-fish.sh 失败');
    }
    if (failed > 0) {
      _logLn('有文件写入失败：Termux 没装或权限没授（回上一步看「环境检查」）');
      if (mounted) setState(() => _busy = false);
      return;
    }

    _logLn('装 Python 运行环境（首次约 1-3 分钟，别关屏幕）…');
    final r = await _termux.runAndWait('bash setup-fish.sh', workdir: _dfyDir);
    if (r == null) {
      _logLn('命令没跑起来：Termux 没装或权限没授（回上一步看「环境检查」）');
      if (mounted) setState(() => _busy = false);
      return;
    }
    final combined = [
      r.stdout,
      r.stderr,
    ].where((s) => s.trim().isNotEmpty).join('\n');
    if (combined.isNotEmpty) _logLn(_tail(combined.trim()));
    if (!r.ok) {
      _logLn(
        '装环境失败（exit ${r.exitCode}）${r.errmsg.isNotEmpty ? '：${r.errmsg}' : ''}',
      );
      if (mounted) setState(() => _busy = false);
      return;
    }

    _logLn('环境就绪，起鱼脑…');
    await _termux.run('cd $_dfyDir && sh run-fish.sh', workdir: _dfyDir);
    await _waitForFish();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _waitForFish() async {
    for (var i = 0; i < 90; i++) {
      try {
        await widget.api.status();
        _logLn('鱼脑已在线 ✅');
        if (mounted) setState(() => _step = 2);
        return;
      } catch (_) {}
      await Future.delayed(const Duration(seconds: 2));
    }
    _logLn('鱼脑没起来，可能安装失败。请打开 Termux 看 server.log。');
  }

  // ---- Step 3: 装 QQ ----
  Future<void> _installQq() async {
    setState(() => _busy = true);
    _logLn('写部署脚本…');
    final sh = await rootBundle.loadString('assets/deploy/setup-qq.sh');
    await _termux.writeFile('$_dfyDir/setup-qq.sh', sh);
    final onebot = await rootBundle.loadString('assets/deploy/onebot11.json');
    await _termux.writeFile('$_dfyDir/onebot11.json', onebot);

    try {
      await widget.api.deployQq();
      _logLn('QQ 环境安装已启动（10-20 分钟，别关屏幕）…');
      _poll = Timer.periodic(const Duration(seconds: 2), (_) => _pollDeploy());
    } catch (e) {
      _logLn('启动部署失败：$e');
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pollDeploy() async {
    try {
      final d = await widget.api.deployStatus();
      final s = (d['status'] as Map?) ?? {};
      final logs = (d['logs'] as List?) ?? [];
      if (mounted) {
        setState(() {
          _msg = (s['step'] as String?) ?? '';
          _progress = (s['progress'] as num?)?.toDouble() ?? -1;
          if (s['running'] == false && s['done'] == true) {
            _poll?.cancel();
            _busy = false;
            _step = 4;
          } else if (s['running'] == false && s['error'] != null) {
            _poll?.cancel();
            _busy = false;
            _logLn('部署失败：${s['error']}');
          }
        });
      }
      // 显示最后几行日志
      final tail = logs
          .take(6)
          .map((l) => (l['message'] ?? ''))
          .toList()
          .reversed
          .join('\n');
      if (mounted && tail.isNotEmpty) setState(() => _log = tail);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('部署向导')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 120),
        children: [
          _stepIndicator(),
          const SizedBox(height: 16),
          ..._stepWidgets(),
        ],
      ),
    );
  }

  Widget _stepIndicator() {
    final theme = Theme.of(context);
    const total = 5;
    final value = (_step + 1) / total;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(2),
          child: LinearProgressIndicator(value: value, minHeight: 4),
        ),
        const SizedBox(height: 8),
        Text('第 ${_step + 1} 步，共 $total 步', style: theme.textTheme.labelSmall),
      ],
    );
  }

  List<Widget> _stepWidgets() {
    switch (_step) {
      case 0:
        return [
          Text('装 Termux', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text('Fishbrain 跑在 Termux 里，需要你先装 Termux：'),
          const SizedBox(height: 8),
          const Text(
            '1. 去 GitHub 搜 "termux-app"（或 F-Droid）下载 APK 安装。⚠ 别用 Play 商店版（已停更，pkg 是坏的）。',
          ),
          const Text('2. 装完打开一次 Termux。'),
          const Text('3. 在 Termux 里粘贴运行这一行，允许 app 控制它：'),
          const SizedBox(height: 8),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(10),
            ),
            child: const SelectableText(
              'echo "allow-external-apps = true" >> ~/.termux/termux.properties && termux-reload-settings',
              style: TextStyle(fontFamily: 'monospace', fontSize: 13),
            ),
          ),
          const SizedBox(height: 16),
          _envCheckCard(),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: () => setState(() => _step = 1),
            child: const Text('我装好 Termux 了'),
          ),
        ];
      case 1:
        return [
          Text('一键装鱼脑', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text('把鱼脑代码写进 Termux 并启动（需要联网装 Python）。'),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _busy ? null : _installFish,
            child: Text(_busy ? '装鱼脑中…' : '开始装鱼脑'),
          ),
          if (_busy) ...[
            const SizedBox(height: 16),
            const LinearProgressIndicator(),
          ],
          if (_log.isNotEmpty) ...[
            const SizedBox(height: 16),
            SectionHeader('安装日志'),
            _logBox(),
          ],
        ];
      case 2:
        return [
          Text(
            '起名 + 填 LLM key',
            style: Theme.of(context).textTheme.headlineSmall,
          ),
          const SizedBox(height: 8),
          const Text('给它起个名，并填上大模型服务的 key。'),
          const SizedBox(height: 16),
          SectionCard(
            children: [
              TextField(
                controller: _name,
                decoration: const InputDecoration(labelText: '名字（给你的赛博生命体起个名）'),
              ),
              const SizedBox(height: 12),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'openai', label: Text('OpenAI 兼容')),
                  ButtonSegment(value: 'anthropic', label: Text('Claude')),
                  ButtonSegment(value: 'gemini', label: Text('Gemini')),
                ],
                selected: {_provider},
                expandedInsets: EdgeInsets.zero,
                showSelectedIcon: false,
                onSelectionChanged: (s) {
                  setState(() {
                    _provider = s.first;
                    _base.text = switch (_provider) {
                      'anthropic' => 'https://api.anthropic.com',
                      'gemini' => 'https://generativelanguage.googleapis.com',
                      _ => 'https://api.deepseek.com/v1',
                    };
                  });
                },
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _key,
                obscureText: true,
                decoration: const InputDecoration(labelText: 'API Key'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _base,
                decoration: const InputDecoration(labelText: 'Base URL'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _model,
                decoration: const InputDecoration(labelText: '模型'),
              ),
            ],
          ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _busy ? null : _saveLlm,
            child: const Text('保存并继续'),
          ),
        ];
      case 3:
        return [
          Text('装 QQ 环境', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text('装 proot-Debian + linuxqq + NapCat（首次 10-20 分钟）。'),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _busy ? null : _installQq,
            child: Text(_busy ? '部署中…' : '开始装 QQ 环境'),
          ),
          if (_busy) ...[
            const SizedBox(height: 16),
            if (_progress >= 0)
              Row(
                children: [
                  Expanded(
                    child: LinearProgressIndicator(value: _progress / 100),
                  ),
                  const SizedBox(width: 8),
                  Text('$_progress%'),
                ],
              )
            else
              const LinearProgressIndicator(),
          ],
          if (_msg.isNotEmpty) ...[
            const SizedBox(height: 12),
            Text('当前步骤：$_msg'),
          ],
          if (_log.isNotEmpty) ...[
            const SizedBox(height: 16),
            SectionHeader('部署日志'),
            _logBox(),
          ],
        ];
      default:
        return [
          Text('扫码登录', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text('QQ 环境已就绪，去扫码登录就能开聊了。'),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: _openLogin,
            icon: const Icon(Icons.qr_code_scanner),
            label: const Text('扫码登录 QQ'),
          ),
        ];
    }
  }

  Widget _logBox() {
    final b = Theme.of(context).brightness;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        gradient: AppTheme.glassGradient(b),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppTheme.glassHighlight(b), width: 0.5),
      ),
      child: Text(
        _log,
        style: const TextStyle(
          fontFamily: 'monospace',
          fontSize: 12,
          height: 1.5,
        ),
      ),
    );
  }

  Future<void> _saveLlm() async {
    setState(() => _busy = true);
    try {
      if (_name.text.trim().isNotEmpty) {
        await widget.api.updateSettings({'name': _name.text.trim()});
      }
      await widget.api.updateLlm({
        'provider': _provider,
        'api_key': _key.text.trim(),
        'base_url': _base.text.trim(),
        'model': _model.text.trim(),
        'vision_model': '',
        'vision_base_url': '',
      });
      setState(() => _step = 3);
    } catch (_) {
      _logLn('保存 key 失败（鱼脑在线吗？）');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _openLogin() {
    Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => LoginPage(api: widget.api)));
  }
}
