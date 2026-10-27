// sticker.dart — 表情包管理页：列表 / 删除 / 固定 / 改标签 / 添加（相册 + URL）
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../api.dart';
import '../theme.dart';
import '../widgets.dart';

// 与 fish/qq_bot.py 的 STICKER_emotions 保持一致（受控情绪词）
const _emotions = [
  '开心',
  '生气',
  '惊讶',
  '可爱',
  '无语',
  '委屈',
  '得意',
  '感动',
  '搞笑',
  '害怕',
  '疑惑',
  '无奈',
];

class StickerPage extends StatefulWidget {
  final ApiClient api;
  const StickerPage({super.key, required this.api});

  @override
  State<StickerPage> createState() => _StickerPageState();
}

class _StickerPageState extends State<StickerPage> {
  List<Map<String, dynamic>> _stickers = [];
  final Map<int, Uint8List> _images = {};
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final list = await widget.api.stickers();
      if (!mounted) return;
      setState(() {
        _stickers = list;
        _loading = false;
      });
      for (final s in list) {
        final id = s['id'] as int;
        if (!_images.containsKey(id)) {
          _loadImage(id);
        }
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _loadImage(int id) async {
    try {
      final bytes = await widget.api.stickerImage(id);
      if (mounted) setState(() => _images[id] = bytes);
    } catch (_) {}
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _togglePin(Map<String, dynamic> s) async {
    final id = s['id'] as int;
    final next = !(s['pinned'] == true);
    try {
      await widget.api.updateSticker(id, pinned: next);
      if (mounted) setState(() => s['pinned'] = next);
    } catch (_) {
      _toast('操作失败');
    }
  }

  Future<void> _editTag(Map<String, dynamic> s) async {
    final chosen = await _pickTag(initial: (s['tag'] as String?) ?? '');
    if (chosen == null) return;
    try {
      await widget.api.updateSticker(s['id'] as int, tag: chosen);
      if (mounted) setState(() => s['tag'] = chosen);
    } catch (_) {
      _toast('操作失败');
    }
  }

  Future<void> _delete(Map<String, dynamic> s) async {
    final id = s['id'] as int;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除这个表情包？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: AppColors.danger),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.api.deleteSticker(id);
      if (mounted) {
        setState(() {
          _stickers.removeWhere((e) => e['id'] == id);
          _images.remove(id);
        });
      }
    } catch (_) {
      _toast('删除失败');
    }
  }

  Future<String?> _pickTag({String initial = ''}) {
    final ctrl = TextEditingController(text: initial);
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('标签（自由文本）'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: ctrl,
              decoration: const InputDecoration(labelText: '输入标签'),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: _emotions
                  .map(
                    (e) => ActionChip(
                      label: Text(e),
                      onPressed: () => ctrl.text = e,
                    ),
                  )
                  .toList(),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
            child: const Text('确定'),
          ),
        ],
      ),
    );
  }

  Future<void> _addFromGallery() async {
    try {
      final picked = await ImagePicker().pickImage(source: ImageSource.gallery);
      if (picked == null) return;
      final bytes = await picked.readAsBytes();
      if (bytes.length > 5 * 1024 * 1024) {
        _toast('超过 5MB');
        return;
      }
      final ext = picked.name.contains('.')
          ? picked.name.split('.').last.toLowerCase()
          : 'gif';
      final tag = await _pickTag();
      if (tag == null) return;
      final r = await widget.api.addSticker(
        base64: base64Encode(bytes),
        ext: ext,
        tag: tag,
      );
      _toast(r['ok'] == true ? '已添加' : '添加失败：${r['error'] ?? ''}');
      await _refresh();
    } catch (_) {
      _toast('选图失败');
    }
  }

  Future<void> _addFromUrl() async {
    final urlCtrl = TextEditingController();
    final url = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('添加表情包 URL'),
        content: TextField(
          controller: urlCtrl,
          decoration: const InputDecoration(labelText: '图片 URL'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, urlCtrl.text.trim()),
            child: const Text('下一步'),
          ),
        ],
      ),
    );
    if (url == null || url.isEmpty) return;
    final tag = await _pickTag();
    if (tag == null) return;
    try {
      final r = await widget.api.addSticker(url: url, tag: tag);
      _toast(r['ok'] == true ? '已添加' : '添加失败：${r['error'] ?? ''}');
      await _refresh();
    } catch (_) {
      _toast('添加失败');
    }
  }

  void _showAddMenu() {
    showModalBottomSheet<void>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(height: 8),
            ListTile(
              leading: const Icon(Icons.photo_library_outlined),
              title: const Text('从相册选择'),
              onTap: () {
                Navigator.pop(ctx);
                _addFromGallery();
              },
            ),
            ListTile(
              leading: const Icon(Icons.link),
              title: const Text('粘贴 URL'),
              onTap: () {
                Navigator.pop(ctx);
                _addFromUrl();
              },
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('表情包管理')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _stickers.isEmpty
          ? const EmptyState(
              icon: Icons.emoji_emotions_outlined,
              title: '还没有表情包',
              subtitle: '点右下角添加，它聊天时会用到这些图',
            )
          : ListView.builder(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 120),
              itemCount: _stickers.length,
              itemBuilder: (ctx, i) => Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _StickerRow(
                  s: _stickers[i],
                  img: _images[_stickers[i]['id'] as int],
                  onTogglePin: () => _togglePin(_stickers[i]),
                  onEditTag: () => _editTag(_stickers[i]),
                  onDelete: () => _delete(_stickers[i]),
                ),
              ),
            ),
      floatingActionButton: FloatingActionButton(
        onPressed: _showAddMenu,
        tooltip: '添加表情包',
        child: const Icon(Icons.add),
      ),
    );
  }
}

class _StickerRow extends StatelessWidget {
  final Map<String, dynamic> s;
  final Uint8List? img;
  final VoidCallback onTogglePin;
  final VoidCallback onEditTag;
  final VoidCallback onDelete;
  const _StickerRow({
    required this.s,
    required this.img,
    required this.onTogglePin,
    required this.onEditTag,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final pinned = s['pinned'] == true;
    final tag = (s['tag'] as String?)?.isNotEmpty == true
        ? s['tag'] as String
        : '无标签';
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 12, 6, 12),
      decoration: BoxDecoration(
        gradient: AppTheme.glassGradient(theme.brightness),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: AppTheme.glassHighlight(theme.brightness),
          width: 0.5,
        ),
      ),
      child: Row(
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: img != null
                ? Image.memory(
                    img!,
                    width: 56,
                    height: 56,
                    fit: BoxFit.cover,
                    gaplessPlayback: true,
                  )
                : Container(
                    width: 56,
                    height: 56,
                    color: theme.colorScheme.surfaceContainerHighest,
                    child: const Icon(Icons.image_outlined, size: 24),
                  ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  tag,
                  style: theme.textTheme.bodyLarge,
                  overflow: TextOverflow.ellipsis,
                ),
                if (pinned) ...[
                  const SizedBox(height: 4),
                  StatusPill(color: AppColors.warning, label: '已固定'),
                ],
              ],
            ),
          ),
          IconButton(
            icon: Icon(pinned ? Icons.push_pin : Icons.push_pin_outlined),
            color: pinned
                ? AppColors.warning
                : theme.colorScheme.onSurfaceVariant,
            tooltip: pinned ? '取消固定' : '固定',
            onPressed: onTogglePin,
          ),
          IconButton(
            icon: const Icon(Icons.edit_outlined, size: 22),
            color: theme.colorScheme.onSurfaceVariant,
            tooltip: '改标签',
            onPressed: onEditTag,
          ),
          IconButton(
            icon: const Icon(Icons.delete_outline, size: 22),
            color: AppColors.danger,
            tooltip: '删除',
            onPressed: onDelete,
          ),
        ],
      ),
    );
  }
}
