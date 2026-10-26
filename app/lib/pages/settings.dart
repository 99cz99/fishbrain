// settings.dart — 设定页：改人设/行为参数 + LLM 配置（软设定，存鱼脑 SQLite）
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'sticker.dart';

class SettingsPage extends StatefulWidget {
  final ApiClient api;
  const SettingsPage({super.key, required this.api});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final _name = TextEditingController();
  final _ownerQq = TextEditingController();
  final _city = TextEditingController();
  final _persona = TextEditingController();
  final _spValues = TextEditingController();
  final _spStyle = TextEditingController();
  final _spLikes = TextEditingController();
  final _spBoundaries = TextEditingController();
  final _llmApiKey = TextEditingController();
  final _llmBaseUrl = TextEditingController();
  final _llmModel = TextEditingController();
  final _llmVisionModel = TextEditingController();
  final _llmVisionBaseUrl = TextEditingController();
  final _heyboxToken = TextEditingController();
  String _llmProvider = 'openai';
  String _chatScope = 'all';
  String _temperamentValence = '0';   // '-0.3' | '0' | '0.3'
  String _temperamentTau = '4';       // '6' | '4' | '2'
  bool _midnight = true;
  bool _money = true;
  bool _publish = true;
  bool _selfEvolve = true;
  bool _heyboxEnabled = false;
  String _heyboxId = '';
  bool _loaded = false;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _name.dispose();
    _ownerQq.dispose();
    _city.dispose();
    _persona.dispose();
    _spValues.dispose();
    _spStyle.dispose();
    _spLikes.dispose();
    _spBoundaries.dispose();
    _llmApiKey.dispose();
    _llmBaseUrl.dispose();
    _llmModel.dispose();
    _llmVisionModel.dispose();
    _llmVisionBaseUrl.dispose();
    _heyboxToken.dispose();
    super.dispose();
  }

  List<String> _splitLines(String s) =>
      s.split('\n').map((e) => e.trim()).where((e) => e.isNotEmpty).toList();

  Future<void> _refresh() async {
    try {
      final s = await widget.api.settings();
      final llm = await widget.api.llm();
      Map<String, dynamic> hb = const {};
      try {
        hb = await widget.api.heyboxStatus();
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _name.text = s['name'] ?? '';
        _ownerQq.text = s['owner_qq'] ?? '';
        _city.text = s['city'] ?? '';
        Map<String, dynamic> temp = const {};
        final t = s['temperament'];
        if (t is Map) {
          temp = Map<String, dynamic>.from(t);
        }
        final tv = (temp['valence'] as num?)?.toDouble() ?? 0.0;
        final tt = (temp['tau_hours'] as num?)?.toDouble() ?? 4.0;
        _temperamentValence = tv <= -0.15 ? '-0.3' : (tv >= 0.15 ? '0.3' : '0');
        _temperamentTau = tt >= 5.5 ? '6' : (tt <= 2.5 ? '2' : '4');
        _persona.text = s['persona'] ?? '';
        Map<String, dynamic> sp = const {};
        final spt = s['self_profile'];
        if (spt is Map) {
          sp = Map<String, dynamic>.from(spt);
        }
        _spValues.text = (sp['values'] as List?)?.join('\n') ?? '';
        _spStyle.text = (sp['style'] as List?)?.join('\n') ?? '';
        _spLikes.text = (sp['likes'] as List?)?.join('\n') ?? '';
        _spBoundaries.text = (sp['boundaries'] as List?)?.join('\n') ?? '';
        _chatScope = s['chat_scope'] ?? 'all';
        _midnight = s['midnight_ok'] ?? true;
        _money = s['approve_money'] ?? true;
        _publish = s['approve_publish'] ?? true;
        _selfEvolve = s['allow_self_evolve'] ?? true;
        _llmApiKey.text = llm['api_key'] ?? '';
        _llmBaseUrl.text = llm['base_url'] ?? '';
        _llmModel.text = llm['model'] ?? '';
        _llmVisionModel.text = llm['vision_model'] ?? '';
        _llmVisionBaseUrl.text = llm['vision_base_url'] ?? '';
        _llmProvider = llm['provider'] ?? 'openai';
        _heyboxEnabled = hb['enabled'] == true;
        _heyboxId = (hb['heybox_id'] as String?) ?? '';
        _loaded = true;
      });
    } catch (_) {}
  }

  Future<void> _save() async {
    final body = {
      'name': _name.text,
      'owner_qq': _ownerQq.text.trim(),
      'city': _city.text.trim(),
      'temperament': {
        'valence': double.parse(_temperamentValence),
        'arousal': 0.0,
        'tau_hours': double.parse(_temperamentTau),
      },
      'self_profile': {
        'values': _splitLines(_spValues.text),
        'style': _splitLines(_spStyle.text),
        'likes': _splitLines(_spLikes.text),
        'boundaries': _splitLines(_spBoundaries.text),
      },
      'persona': _persona.text,
      'chat_scope': _chatScope,
      'midnight_ok': _midnight,
      'approve_money': _money,
      'approve_publish': _publish,
      'allow_self_evolve': _selfEvolve,
    };
    try {
      await widget.api.updateSettings(body);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已保存')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('保存失败')));
      }
    }
  }

  Future<void> _saveLlm() async {
    final body = {
      'provider': _llmProvider,
      'api_key': _llmApiKey.text,
      'base_url': _llmBaseUrl.text,
      'model': _llmModel.text,
      'vision_model': _llmVisionModel.text,
      'vision_base_url': _llmVisionBaseUrl.text,
    };
    try {
      await widget.api.updateLlm(body);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('LLM 配置已保存')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('LLM 保存失败')));
      }
    }
  }

  Future<void> _saveHeyboxToken() async {
    try {
      final r = await widget.api.setHeyboxToken(_heyboxToken.text.trim());
      if (mounted) {
        _heyboxToken.clear();
        _refresh();
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(r['enabled'] == true ? '小黑盒已接入' : '已停用小黑盒')),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('小黑盒保存失败')));
      }
    }
  }

  Future<void> _signHeybox() async {
    try {
      final r = await widget.api.heyboxSign();
      final fail = (r['_error'] != null) || (r['_http_error'] != null);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(fail ? '签到失败：$r' : '签到已执行：$r')),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('签到失败（小黑盒接入了吗？）')));
      }
    }
  }

  Future<void> _evolveNow() async {
    try {
      await widget.api.growthEvolve();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已让它自省，稍等几秒')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('自省失败')));
      }
    }
  }

  Future<void> _resetPersona() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清除赛博生命体人格？'),
        content: const Text('会清空名字、设定、长期记忆和最近对话，回到出厂空白。LLM 配置和审批等不受影响。此操作不可撤销。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('清除'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.api.resetPersona();
      await _refresh();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('人格已清除')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('清除失败')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('设定')),
      body: !_loaded
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                TextField(
                  controller: _name,
                  decoration: const InputDecoration(
                      labelText: '名字', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _ownerQq,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                      labelText: '主人 QQ 号（主动冒泡发给谁）', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _city,
                  decoration: const InputDecoration(
                      labelText: '城市（天气感知，中文名如「北京」，留空=不启用）',
                      border: OutlineInputBorder()),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _persona,
                  maxLines: 4,
                  decoration: const InputDecoration(
                      labelText: '设定（性格、口头禅都写这里）', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 24),
                Text('自我画像（每行一条，会自动演化）', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 12),
                TextField(
                  controller: _spValues,
                  maxLines: 3,
                  decoration: const InputDecoration(
                      labelText: '价值观', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _spStyle,
                  maxLines: 2,
                  decoration: const InputDecoration(
                      labelText: '说话风格', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _spLikes,
                  maxLines: 3,
                  decoration: const InputDecoration(
                      labelText: '喜好', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _spBoundaries,
                  maxLines: 2,
                  decoration: const InputDecoration(
                      labelText: '边界', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 16),
                Text('主题', style: Theme.of(context).textTheme.bodySmall),
                SegmentedButton<ThemeMode>(
                  segments: const [
                    ButtonSegment(value: ThemeMode.light, label: Text('浅色')),
                    ButtonSegment(value: ThemeMode.dark, label: Text('深色')),
                    ButtonSegment(value: ThemeMode.system, label: Text('跟随系统')),
                  ],
                  selected: {themeNotifier.value},
                  onSelectionChanged: (s) => themeNotifier.value = s.first,
                ),
                const SizedBox(height: 16),
                Text('聊天尺度', style: Theme.of(context).textTheme.bodySmall),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: 'all', label: Text('放开')),
                    ButtonSegment(value: 'moderate', label: Text('正常')),
                    ButtonSegment(value: 'clean', label: Text('干净')),
                  ],
                  selected: {_chatScope},
                  onSelectionChanged: (s) => setState(() => _chatScope = s.first),
                ),
                const SizedBox(height: 16),
                Text('气质（心情基线）', style: Theme.of(context).textTheme.bodySmall),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: '-0.3', label: Text('偏阴郁')),
                    ButtonSegment(value: '0', label: Text('中性')),
                    ButtonSegment(value: '0.3', label: Text('偏阳光')),
                  ],
                  selected: {_temperamentValence},
                  onSelectionChanged: (s) => setState(() => _temperamentValence = s.first),
                ),
                const SizedBox(height: 12),
                Text('平复快慢', style: Theme.of(context).textTheme.bodySmall),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: '6', label: Text('慢')),
                    ButtonSegment(value: '4', label: Text('中等')),
                    ButtonSegment(value: '2', label: Text('快')),
                  ],
                  selected: {_temperamentTau},
                  onSelectionChanged: (s) => setState(() => _temperamentTau = s.first),
                ),
                SwitchListTile(
                  title: const Text('半夜也能主动找我'),
                  value: _midnight,
                  onChanged: (v) => setState(() => _midnight = v),
                ),
                SwitchListTile(
                  title: const Text('花钱前先问我'),
                  value: _money,
                  onChanged: (v) => setState(() => _money = v),
                ),
                SwitchListTile(
                  title: const Text('对外发内容前先问我'),
                  value: _publish,
                  onChanged: (v) => setState(() => _publish = v),
                ),
                SwitchListTile(
                  title: const Text('自成长模式'),
                  subtitle: const Text('开启后它会自己进化设定；关掉则只能你手动让它自省'),
                  value: _selfEvolve,
                  onChanged: (v) => setState(() => _selfEvolve = v),
                ),
                const SizedBox(height: 24),
                FilledButton(onPressed: _save, child: const Text('保存设定')),
                const SizedBox(height: 24),
                OutlinedButton.icon(
                  onPressed: _evolveNow,
                  icon: const Icon(Icons.auto_awesome),
                  label: const Text('让它自省一次'),
                ),
                const SizedBox(height: 24),
                OutlinedButton.icon(
                  onPressed: () {
                    Navigator.push(context, MaterialPageRoute(builder: (_) => StickerPage(api: widget.api)));
                  },
                  icon: const Icon(Icons.emoji_emotions_outlined),
                  label: const Text('管理表情包'),
                ),
                const SizedBox(height: 32),
                const Divider(),
                Text('LLM 配置', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 12),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: 'openai', label: Text('OpenAI 兼容')),
                    ButtonSegment(value: 'anthropic', label: Text('Claude')),
                    ButtonSegment(value: 'gemini', label: Text('Gemini')),
                  ],
                  selected: {_llmProvider},
                  onSelectionChanged: (s) {
                    setState(() {
                      _llmProvider = s.first;
                      if (_llmBaseUrl.text.isEmpty) {
                        _llmBaseUrl.text = switch (_llmProvider) {
                          'anthropic' => 'https://api.anthropic.com',
                          'gemini' => 'https://generativelanguage.googleapis.com',
                          _ => 'https://api.deepseek.com/v1',
                        };
                      }
                    });
                  },
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _llmApiKey,
                  obscureText: true,
                  decoration: const InputDecoration(
                      labelText: 'API Key', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _llmBaseUrl,
                  decoration: const InputDecoration(
                      labelText: 'Base URL', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _llmModel,
                  decoration: const InputDecoration(
                      labelText: '模型', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _llmVisionModel,
                  decoration: const InputDecoration(
                      labelText: '视觉模型（留空 = 用上面的模型）', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _llmVisionBaseUrl,
                  decoration: const InputDecoration(
                      labelText: '视觉模型 Base URL（留空 = 用上面的）', border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                FilledButton.tonal(onPressed: _saveLlm, child: const Text('保存 LLM 配置')),
                const SizedBox(height: 32),
                const Divider(),
                Text('小黑盒', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 8),
                Text(_heyboxEnabled
                    ? (_heyboxId.isEmpty
                        ? '已接入（未解析出 heybox_id，token 可能不是有效 app pkey）'
                        : '已接入（heybox_id: $_heyboxId）')
                    : '未接入（未填 token）'),
                const SizedBox(height: 8),
                TextField(
                  controller: _heyboxToken,
                  obscureText: true,
                  decoration: const InputDecoration(
                      labelText: '小黑盒 token（手机 app 版 pkey，留空保存 = 停用）',
                      border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: FilledButton.tonal(
                          onPressed: _saveHeyboxToken, child: const Text('保存小黑盒 token')),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _heyboxEnabled ? _signHeybox : null,
                        icon: const Icon(Icons.event_available),
                        label: const Text('签到'),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 32),
                const Divider(),
                OutlinedButton.icon(
                  onPressed: _resetPersona,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.red,
                    side: const BorderSide(color: Colors.red),
                  ),
                  icon: const Icon(Icons.delete_forever),
                  label: const Text('清除赛博生命体人格'),
                ),
              ],
            ),
    );
  }
}
