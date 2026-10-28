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
  String? _error;
  bool _loading = false;
  bool _more = false;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh({bool more = false}) async {
    if (_loading) return;
    setState(() => _loading = true);
    try {
      final m = await widget.api.memories(offset: more ? _all.length : 0);
      if (mounted) {
        setState(() {
          _all = more ? [..._all, ...m] : m;
          _more = m.length == 1000;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = '读取记忆失败：$e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _delete(Memory m) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('忘记这条记忆？'),
        content: const Text(
          '会同时清理关联来源、由它生成的认识及相关短期上下文，避免再次提炼出已删除内容。同一来源中的其他事实也可能被移除。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('忘记'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    try {
      await widget.api.deleteMemory(m.id);
      if (mounted) await _refresh();
    } catch (e) {
      _showError(e);
    }
  }

  void _showError(Object e) {
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _change(Memory m, Map<String, dynamic> changes) async {
    try {
      await widget.api.updateMemory(m.id, changes);
      if (mounted) await _refresh();
    } catch (e) {
      _showError(e);
    }
  }

  Future<void> _edit(Memory m) async {
    final controller = TextEditingController(text: m.content);
    final value = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('纠正记忆'),
        content: TextField(
          controller: controller,
          minLines: 2,
          maxLines: 6,
          maxLength: 1000,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () {
              if (controller.text.trim().isNotEmpty) {
                Navigator.pop(ctx, controller.text.trim());
              }
            },
            child: const Text('保存'),
          ),
        ],
      ),
    );
    // The route's closing animation may still use its controller.
    await Future<void>.delayed(const Duration(milliseconds: 300));
    controller.dispose();
    if (value != null && mounted) await _change(m, {'content': value});
  }

  List<Memory> get _filtered => _all.where((m) {
    if (_platformFilter != '__all__' && m.platform != _platformFilter) {
      return false;
    }
    if (_subjectFilter != '__all__' &&
        (m.entityId.isEmpty ? m.subject : m.entityId) != _subjectFilter) {
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
        subjects[m.entityId.isEmpty ? m.subject : m.entityId] =
            m.entityId.isEmpty ? m.subject : '${m.subject} (${m.entityId})';
      }
    }
    final mems = _filtered;

    return Scaffold(
      appBar: AppBar(
        title: const Text('记忆'),
        actions: [
          IconButton(
            onPressed: _loading ? null : () => _refresh(),
            icon: const Icon(Icons.refresh),
            tooltip: '刷新',
          ),
        ],
      ),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_loading) const LinearProgressIndicator(),
          if (_error != null)
            TextButton(
              onPressed: () => _refresh(),
              child: Text('$_error · 重试'),
            ),
          if (_more)
            TextButton(
              onPressed: _loading ? null : () => _refresh(more: true),
              child: const Text('加载更多记忆'),
            ),
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
                        child: _MemoryRow(
                          m: m,
                          onDelete: () => _delete(m),
                          onEdit: () => _edit(m),
                          onChange: (changes) => _change(m, changes),
                        ),
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
  final VoidCallback onEdit;
  final void Function(Map<String, dynamic>) onChange;
  const _MemoryRow({
    required this.m,
    required this.onDelete,
    required this.onEdit,
    required this.onChange,
  });

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
                  '${_kind(m.kind)} · ${m.subject.isEmpty ? '—' : m.subject} · ${m.platform.isEmpty ? '未知' : m.platform} · ${m.createdAt}',
                  style: theme.textTheme.labelSmall,
                ),
                Text(
                  '${_status(m.status)} · 范围：${m.scope} · ${m.entityId}',
                  style: theme.textTheme.labelSmall,
                ),
                if (m.sourceIds.isNotEmpty)
                  Text(
                    '依据：${m.sourceIds.map((id) => '#$id').join('、')}',
                    style: theme.textTheme.labelSmall,
                  ),
                if (m.pinned) const Text('已固定'),
              ],
            ),
          ),
          PopupMenuButton<String>(
            tooltip: '管理记忆',
            onSelected: (value) {
              if (value == 'edit') onEdit();
              if (value == 'delete') onDelete();
              if (value == 'pin') onChange({'pinned': !m.pinned});
              if (value == 'done' || value == 'cancelled') {
                onChange({'status': value});
              }
            },
            itemBuilder: (_) => [
              if (m.kind != 'intention')
                const PopupMenuItem(value: 'edit', child: Text('纠正')),
              PopupMenuItem(
                value: 'pin',
                child: Text(m.pinned ? '取消固定' : '固定'),
              ),
              if (m.kind == 'intention' && m.status == 'active') ...[
                const PopupMenuItem(value: 'done', child: Text('标记完成')),
                const PopupMenuItem(value: 'cancelled', child: Text('取消事项')),
              ],
              const PopupMenuItem(value: 'delete', child: Text('忘记')),
            ],
          ),
        ],
      ),
    );
  }

  String _kind(String value) => switch (value) {
    'episode' => '共同经历',
    'fact' => '事实',
    'belief' => '暂定认识',
    'intention' => '未完事项',
    'experience' => '自身经历',
    _ => value,
  };
  String _status(String value) => switch (value) {
    'active' => '有效',
    'quarantined' => '待核实（不参与召回）',
    'superseded' => '已更新',
    'archived' => '已归档',
    'done' => '已完成',
    'cancelled' => '已取消',
    _ => value,
  };
}
