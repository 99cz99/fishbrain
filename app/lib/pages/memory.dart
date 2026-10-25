// memory.dart — 记忆页：看它记了什么 + 删除 + 按人/平台筛选
import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';

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
        if (_platformFilter != '__all__' && m.platform != _platformFilter) return false;
        if (_subjectFilter != '__all__' && m.subject != _subjectFilter) return false;
        return true;
      }).toList();

  IconData _iconFor(String k) => switch (k) {
        'user' => Icons.person_outline,
        'person' => Icons.people_outline,
        'self' => Icons.pets_outlined,
        _ => Icons.lightbulb_outline,
      };

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
      if (m.platform.isNotEmpty) platforms[m.platform] = _platformLabel(m.platform);
      if (m.subject.isNotEmpty) subjects[m.subject] = m.subject;
    }
    final mems = _filtered;

    return Scaffold(
      appBar: AppBar(title: const Text('记忆')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _filterRow('平台', _platformFilter, platforms, (v) => setState(() => _platformFilter = v)),
                if (subjects.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  _filterRow('人', _subjectFilter, subjects, (v) => setState(() => _subjectFilter = v)),
                ],
              ],
            ),
          ),
          const SizedBox(height: 8),
          Expanded(
            child: mems.isEmpty
                ? const Center(child: Text('它还没记住什么'))
                : ListView.builder(
                    itemCount: mems.length,
                    itemBuilder: (_, i) {
                      final m = mems[i];
                      return ListTile(
                        leading: Icon(_iconFor(m.kind)),
                        title: Text(m.content),
                        subtitle: Text('${m.kind} · ${m.subject} · ${_platformLabel(m.platform)} · ${m.createdAt}'),
                        trailing: IconButton(
                          onPressed: () => _delete(m),
                          icon: const Icon(Icons.delete_outline),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }

  Widget _filterRow(String label, String selected, Map<String, String> options,
      void Function(String) onSelect) {
    return Wrap(
      crossAxisAlignment: WrapCrossAlignment.center,
      spacing: 8,
      children: [
        Text('$label：', style: Theme.of(context).textTheme.bodySmall),
        FilterChip(
          label: const Text('全部'),
          selected: selected == '__all__',
          onSelected: (_) => onSelect('__all__'),
        ),
        ...options.entries.map((e) => FilterChip(
              label: Text(e.value),
              selected: selected == e.key,
              onSelected: (_) => onSelect(e.key),
            )),
      ],
    );
  }
}
