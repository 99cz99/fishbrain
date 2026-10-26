// sticker.dart — 表情包管理页：列表 / 删除 / 固定 / 改标签 / 添加（相册 + URL）
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../api.dart';

// 与 fish/qq_bot.py 的 STICKER_emotions 保持一致（受控情绪词）
const _emotions = ['开心', '生气', '惊讶', '可爱', '无语', '委屈', '得意', '感动', '搞笑', '害怕', '疑惑', '无奈'];

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
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
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
            TextField(controller: ctrl, decoration: const InputDecoration(labelText: '输入标签')),
            const SizedBox(height: 12),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: _emotions
                  .map((e) => ActionChip(label: Text(e), onPressed: () => ctrl.text = e))
                  .toList(),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, ctrl.text.trim()), child: const Text('确定')),
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
      final ext = picked.name.contains('.') ? picked.name.split('.').last.toLowerCase() : 'gif';
      final tag = await _pickTag();
      if (tag == null) return;
      final r = await widget.api.addSticker(base64: base64Encode(bytes), ext: ext, tag: tag);
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
        content: TextField(controller: urlCtrl, decoration: const InputDecoration(labelText: '图片 URL')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, urlCtrl.text.trim()), child: const Text('下一步')),
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
            ListTile(
              leading: const Icon(Icons.photo_library),
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
              ? const Center(child: Text('还没有表情包，点右下角添加'))
              : ListView.builder(
                  itemCount: _stickers.length,
                  itemBuilder: (ctx, i) {
                    final s = _stickers[i];
                    final id = s['id'] as int;
                    final img = _images[id];
                    return ListTile(
                      leading: img != null
                          ? ClipRRect(
                              borderRadius: BorderRadius.circular(6),
                              child: Image.memory(img, width: 48, height: 48, fit: BoxFit.cover, gaplessPlayback: true),
                            )
                          : const SizedBox(width: 48, height: 48, child: Icon(Icons.image)),
                      title: Text((s['tag'] as String?)?.isNotEmpty == true ? s['tag'] as String : '无标签'),
                      subtitle: s['pinned'] == true ? const Text('已固定') : null,
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          IconButton(
                            icon: Icon(s['pinned'] == true ? Icons.push_pin : Icons.push_pin_outlined),
                            color: s['pinned'] == true ? Colors.orange : null,
                            onPressed: () => _togglePin(s),
                          ),
                          IconButton(icon: const Icon(Icons.edit), onPressed: () => _editTag(s)),
                          IconButton(
                            icon: const Icon(Icons.delete, color: Colors.red),
                            onPressed: () => _delete(s),
                          ),
                        ],
                      ),
                    );
                  },
                ),
      floatingActionButton: FloatingActionButton(
        onPressed: _showAddMenu,
        child: const Icon(Icons.add),
      ),
    );
  }
}
