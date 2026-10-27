// settings.dart — 设定页：改人设/行为参数 + LLM 配置（软设定，存鱼脑 SQLite）
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import '../widgets.dart';
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
  String _temperamentValence = '0'; // '-0.3' | '0' | '0.3'
  String _temperamentTau = '4'; // '6' | '4' | '2'
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
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('已保存')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('保存失败')));
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
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('LLM 配置已保存')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('LLM 保存失败')));
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
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('小黑盒保存失败')));
      }
    }
  }

  Future<void> _signHeybox() async {
    try {
      final r = await widget.api.heyboxSign();
      final fail = (r['_error'] != null) || (r['_http_error'] != null);
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(fail ? '签到失败：$r' : '签到已执行：$r')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('签到失败（小黑盒接入了吗？）')));
      }
    }
  }

  Future<void> _evolveNow() async {
    try {
      await widget.api.growthEvolve();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('已让它自省，稍等几秒')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('自省失败')));
      }
    }
  }

  Future<void> _resetPersona() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清除赛博生命体人格？'),
        content: const Text(
          '会清空名字、设定、长期记忆和最近对话，回到出厂空白。LLM 配置和审批等不受影响。此操作不可撤销。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: AppColors.danger),
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
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('人格已清除')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('清除失败')));
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
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 120),
              children: [
                _sectionHeader('通用'),
                SectionCard(
                  padding: EdgeInsets.zero,
                  children: [
                    _navRow(
                      '身份',
                      '名字、QQ、城市、人设',
                      Icons.person_outline,
                      _openIdentity,
                    ),
                    _divider(),
                    _navRow(
                      '自我画像',
                      '价值观、风格、喜好、边界',
                      Icons.auto_awesome,
                      _openProfile,
                    ),
                    _divider(),
                    _navRow('行为', '聊天尺度、气质、权限开关', Icons.tune, _openBehavior),
                  ],
                ),

                const SizedBox(height: 20),
                _sectionHeader('外观'),
                SectionCard(
                  children: [
                    _labeledSegmented(
                      '主题',
                      _segmented<ThemeMode>(
                        value: themeNotifier.value,
                        segments: const [
                          ButtonSegment(
                            value: ThemeMode.light,
                            label: Text('浅色'),
                          ),
                          ButtonSegment(
                            value: ThemeMode.dark,
                            label: Text('深色'),
                          ),
                          ButtonSegment(
                            value: ThemeMode.system,
                            label: Text('跟随系统'),
                          ),
                        ],
                        onChanged: (v) => themeNotifier.value = v,
                      ),
                    ),
                  ],
                ),

                const SizedBox(height: 20),
                _sectionHeader('连接'),
                SectionCard(
                  padding: EdgeInsets.zero,
                  children: [
                    _navRow(
                      'LLM 配置',
                      '模型、API Key、Base URL',
                      Icons.settings_suggest_outlined,
                      _openLlm,
                    ),
                    _divider(),
                    _navRow(
                      '小黑盒',
                      _heyboxEnabled ? '已接入' : '未接入',
                      Icons.storefront_outlined,
                      _openHeybox,
                    ),
                  ],
                ),

                const SizedBox(height: 20),
                _sectionHeader('成长'),
                SectionCard(
                  children: [
                    OutlinedButton.icon(
                      onPressed: _evolveNow,
                      icon: const Icon(Icons.auto_awesome),
                      label: const Text('让它自省一次'),
                    ),
                    const SizedBox(height: 10),
                    OutlinedButton.icon(
                      onPressed: () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => StickerPage(api: widget.api),
                          ),
                        );
                      },
                      icon: const Icon(Icons.emoji_emotions_outlined),
                      label: const Text('管理表情包'),
                    ),
                  ],
                ),

                const SizedBox(height: 20),
                _sectionHeader('危险'),
                SectionCard(
                  children: [
                    OutlinedButton.icon(
                      onPressed: _resetPersona,
                      style: OutlinedButton.styleFrom(
                        foregroundColor: AppColors.danger,
                        side: const BorderSide(color: AppColors.danger),
                      ),
                      icon: const Icon(Icons.delete_forever),
                      label: const Text('清除赛博生命体人格'),
                    ),
                  ],
                ),
              ],
            ),
    );
  }

  // ---- 子页导航 ----
  void _openIdentity() => Navigator.push(
    context,
    MaterialPageRoute(builder: (_) => _identityPage()),
  );
  void _openProfile() => Navigator.push(
    context,
    MaterialPageRoute(builder: (_) => _profilePage()),
  );
  void _openBehavior() => Navigator.push(
    context,
    MaterialPageRoute(builder: (_) => _behaviorPage()),
  );
  void _openLlm() =>
      Navigator.push(context, MaterialPageRoute(builder: (_) => _llmPage()));
  void _openHeybox() =>
      Navigator.push(context, MaterialPageRoute(builder: (_) => _heyboxPage()));

  Widget _identityPage() {
    return _subPage('身份', [
      SectionCard(
        children: [
          _field(_name, '名字'),
          const SizedBox(height: 12),
          _field(
            _ownerQq,
            '主人 QQ 号（主动冒泡发给谁）',
            keyboardType: TextInputType.number,
          ),
          const SizedBox(height: 12),
          _field(_city, '城市（天气感知，中文名如「北京」，留空=不启用）'),
          const SizedBox(height: 12),
          _field(_persona, '设定（性格、口头禅都写这里）', maxLines: 10),
        ],
      ),
      const SizedBox(height: 16),
      FilledButton(onPressed: _save, child: const Text('保存')),
    ]);
  }

  Widget _profilePage() {
    return _subPage('自我画像', [
      SectionCard(
        children: [
          _field(_spValues, '价值观', maxLines: 4),
          const SizedBox(height: 12),
          _field(_spStyle, '说话风格', maxLines: 3),
          const SizedBox(height: 12),
          _field(_spLikes, '喜好', maxLines: 4),
          const SizedBox(height: 12),
          _field(_spBoundaries, '边界', maxLines: 3),
        ],
      ),
      const SizedBox(height: 16),
      FilledButton(onPressed: _save, child: const Text('保存')),
    ]);
  }

  Widget _behaviorPage() {
    return _subPage('行为', [
      SectionCard(
        children: [
          _labeledSegmented(
            '聊天尺度',
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'all', label: Text('放开')),
                ButtonSegment(value: 'moderate', label: Text('正常')),
                ButtonSegment(value: 'clean', label: Text('干净')),
              ],
              selected: {_chatScope},
              showSelectedIcon: false,
              onSelectionChanged: (s) => setState(() => _chatScope = s.first),
            ),
          ),
          const SizedBox(height: 12),
          _labeledSegmented(
            '气质（心情基线）',
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: '-0.3', label: Text('偏阴郁')),
                ButtonSegment(value: '0', label: Text('中性')),
                ButtonSegment(value: '0.3', label: Text('偏阳光')),
              ],
              selected: {_temperamentValence},
              showSelectedIcon: false,
              onSelectionChanged: (s) =>
                  setState(() => _temperamentValence = s.first),
            ),
          ),
          const SizedBox(height: 12),
          _labeledSegmented(
            '平复快慢',
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: '6', label: Text('慢')),
                ButtonSegment(value: '4', label: Text('中等')),
                ButtonSegment(value: '2', label: Text('快')),
              ],
              selected: {_temperamentTau},
              showSelectedIcon: false,
              onSelectionChanged: (s) =>
                  setState(() => _temperamentTau = s.first),
            ),
          ),
          const Divider(height: 24),
          _switch('半夜也能主动找我', _midnight, (v) => setState(() => _midnight = v)),
          _switch('花钱前先问我', _money, (v) => setState(() => _money = v)),
          _switch('对外发内容前先问我', _publish, (v) => setState(() => _publish = v)),
          _switch(
            '自成长模式',
            _selfEvolve,
            (v) => setState(() => _selfEvolve = v),
            subtitle: '开启后它会自己进化设定；关掉则只能你手动让它自省',
          ),
        ],
      ),
      const SizedBox(height: 16),
      FilledButton(onPressed: _save, child: const Text('保存')),
    ]);
  }

  Widget _llmPage() {
    return _subPage('LLM 配置', [
      SectionCard(
        children: [
          _segmented<String>(
            value: _llmProvider,
            segments: const [
              ButtonSegment(value: 'openai', label: Text('OpenAI 兼容')),
              ButtonSegment(value: 'anthropic', label: Text('Claude')),
              ButtonSegment(value: 'gemini', label: Text('Gemini')),
            ],
            onChanged: (v) {
              setState(() {
                _llmProvider = v;
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
          _field(_llmApiKey, 'API Key', obscure: true),
          const SizedBox(height: 12),
          _field(_llmBaseUrl, 'Base URL'),
          const SizedBox(height: 12),
          _field(_llmModel, '模型'),
          const SizedBox(height: 12),
          _field(_llmVisionModel, '视觉模型（留空 = 用上面的模型）'),
          const SizedBox(height: 12),
          _field(_llmVisionBaseUrl, '视觉模型 Base URL（留空 = 用上面的）'),
        ],
      ),
      const SizedBox(height: 16),
      FilledButton.tonal(onPressed: _saveLlm, child: const Text('保存 LLM 配置')),
    ]);
  }

  Widget _heyboxPage() {
    return _subPage('小黑盒', [
      SectionCard(
        children: [
          Text(
            _heyboxEnabled
                ? (_heyboxId.isEmpty
                      ? '已接入（未解析出 heybox_id，token 可能不是有效 app pkey）'
                      : '已接入（heybox_id: $_heyboxId）')
                : '未接入（未填 token）',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 12),
          _field(
            _heyboxToken,
            '小黑盒 token（手机 app 版 pkey，留空保存 = 停用）',
            obscure: true,
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: FilledButton.tonal(
                  onPressed: _saveHeyboxToken,
                  child: const Text('保存 token'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _heyboxEnabled ? _signHeybox : null,
                  icon: const Icon(Icons.event_available),
                  label: const Text('签到'),
                ),
              ),
            ],
          ),
        ],
      ),
    ]);
  }

  Widget _subPage(String title, List<Widget> children) {
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: children,
      ),
    );
  }

  Widget _navRow(
    String title,
    String subtitle,
    IconData icon,
    VoidCallback onTap,
  ) {
    final theme = Theme.of(context);
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          children: [
            Icon(icon, size: 22, color: theme.colorScheme.primary),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: theme.textTheme.bodyMedium),
                  const SizedBox(height: 2),
                  Text(
                    subtitle,
                    style: theme.textTheme.bodySmall,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            Icon(
              Icons.chevron_right,
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ],
        ),
      ),
    );
  }

  Widget _divider() => const Divider(height: 1, indent: 52);

  Widget _sectionHeader(String text) => SectionHeader(text);

  Widget _field(
    TextEditingController c,
    String label, {
    bool obscure = false,
    TextInputType? keyboardType,
    int? maxLines,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.labelMedium),
        const SizedBox(height: 8),
        TextField(
          controller: c,
          obscureText: obscure,
          keyboardType: keyboardType,
          maxLines: maxLines,
        ),
      ],
    );
  }

  Widget _labeledSegmented(String label, Widget control) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.labelMedium),
        const SizedBox(height: 8),
        control,
      ],
    );
  }

  Widget _segmented<T>({
    required T value,
    required List<ButtonSegment<T>> segments,
    required void Function(T) onChanged,
  }) {
    return SegmentedButton<T>(
      segments: segments,
      selected: {value},
      expandedInsets: EdgeInsets.zero,
      showSelectedIcon: false,
      onSelectionChanged: (s) => onChanged(s.first),
    );
  }

  Widget _switch(
    String title,
    bool value,
    ValueChanged<bool> onChanged, {
    String? subtitle,
  }) {
    return SwitchListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(title, style: Theme.of(context).textTheme.bodyMedium),
      subtitle: subtitle == null
          ? null
          : Text(subtitle, style: Theme.of(context).textTheme.bodySmall),
      value: value,
      onChanged: onChanged,
    );
  }
}
