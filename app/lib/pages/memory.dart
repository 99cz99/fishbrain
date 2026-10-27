// memory.dart — 记忆页：看它记了什么 + 删除 + 按人/平台筛选
import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';

class MemoryPage extends StatefulWidget {
  final ApiClient api;
  const MemoryPage({super.key, required this.api});

  @override
  State<MemoryPage> createState() => _MemoryPageState();
}

class _MemoryPageState extends State<MemoryPage> {
  List<Memory> _all = [];
  String _platformFilter = '__all__';
  String _subjectFilter = '__all__';

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final m = await widget.api.memories();
      if (mounted) setState(() => _all = m);
    } catch (_) {}
  }

  Future<void> _delete(Memory m) async {
    try {
      await widget.api.deleteMemory(m.id);
      _refresh();
    } catch (_) {}
  }

  List<Memory> get _filtered => _all.where((m) {
    if (_platformFilter != '__all__' && m.platform != _platformFilter) {
      return false;
    }
    if (_subjectFilter != '__all__' && m.subject != _subjectFilter) {
      return false;
    }
    return true;
  }).toList();

  String _platformLabel(String p) => switch (p) {
    'qq' => 'QQ',
    'heybox' => '小黑盒',
    'bilibili' => 'B站',
    '' => '未知',
    _ => p,
  };

  @override
  Widget build(BuildContext context) {
    final platforms = <String, String>{};
    final subjects = <String, String>{};
    for (final m in _all) {
      if (m.platform.isNotEmpty) {
        platforms[m.platform] = _platformLabel(m.platform);
      }
      if (m.subject.isNotEmpty) {
        subjects[m.subject] = m.subject;
      }
    }
    final mems = _filtered;

    return Scaffold(
      appBar: AppBar(title: const Text('记忆')),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (platforms.isNotEmpty || subjects.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (platforms.isNotEmpty)
                    _filterRow(
                      '平台',
                      _platformFilter,
                      platforms,
                      (v) => setState(() => _platformFilter = v),
                    ),
                  if (subjects.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    _filterRow(
                      '人',
                      _subjectFilter,
                      subjects,
                      (v) => setState(() => _subjectFilter = v),
                    ),
                  ],
                ],
              ),
            ),
          Expanded(
            child: mems.isEmpty
                ? const EmptyState(
                    icon: Icons.psychology_outlined,
                    title: '它还没记住什么',
                    subtitle: '聊得越多，它越了解你',
                  )
                : ListView.builder(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 120),
                    itemCount: mems.length,
                    itemBuilder: (_, i) {
                      final m = mems[i];
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: _MemoryRow(m: m, onDelete: () => _delete(m)),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }

  Widget _filterRow(
    String label,
    String selected,
    Map<String, String> options,
    void Function(String) onSelect,
  ) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(top: 10),
          child: Text(
            label,
            style: Theme.of(context).textTheme.labelSmall
                ?.copyWith(fontSize: 13),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              FilterChip(
                label: const Text('全部'),
                selected: selected == '__all__',
                onSelected: (_) => onSelect('__all__'),
              ),
              ...options.entries.map(
                (e) => FilterChip(
                  label: Text(e.value),
                  selected: selected == e.key,
                  onSelected: (_) => onSelect(e.key),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _MemoryRow extends StatelessWidget {
  final Memory m;
  final VoidCallback onDelete;
  const _MemoryRow({required this.m, required this.onDelete});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final b = theme.brightness;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
      decoration: BoxDecoration(
        gradient: AppTheme.glassGradient(b),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppTheme.glassHighlight(b), width: 0.5),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(
              switch (m.kind) {
                'user' => Icons.person_outline,
                'person' => Icons.people_outline,
                'self' => Icons.pets_outlined,
                _ => Icons.lightbulb_outline,
              },
              size: 20,
              color: theme.colorScheme.primary,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(m.content, style: theme.textTheme.bodyMedium),
                const SizedBox(height: 4),
                Text(
                  '${m.kind} · ${m.subject.isEmpty ? '—' : m.subject} · ${m.platform.isEmpty ? '未知' : m.platform} · ${m.createdAt}',
                  style: theme.textTheme.labelSmall,
                ),
              ],
            ),
          ),
          IconButton(
            onPressed: onDelete,
            icon: const Icon(Icons.delete_outline, size: 20),
            color: theme.colorScheme.onSurfaceVariant,
            tooltip: '删除',
          ),
        ],
      ),
    );
  }
}
