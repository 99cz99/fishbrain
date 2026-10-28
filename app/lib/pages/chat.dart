// chat.dart — 聊天记录页
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';

class ChatPage extends StatefulWidget {
  final ApiClient api;
  const ChatPage({super.key, required this.api});

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  List<LogEntry> _chat = [];
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 2), (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  bool _refreshing = false;

  Future<void> _refresh() async {
    if (_refreshing || !mounted) return;
    _refreshing = true;
    try {
      try {
        final c = await widget.api.chat(limit: 200);
        if (mounted) setState(() => _chat = c);
      } catch (_) {}
    } finally {
      _refreshing = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('聊天记录')),
      body: _chat.isEmpty
          ? const EmptyState(
              icon: Icons.forum_outlined,
              title: '还没有聊天记录',
              subtitle: '它和别人的对话会实时出现在这里',
            )
          : ListView.builder(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 120),
              itemCount: _chat.length,
              itemBuilder: (_, i) {
                final c = _chat[i];
                return Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: _ChatRow(c: c),
                );
              },
            ),
    );
  }
}

class _ChatRow extends StatelessWidget {
  final LogEntry c;
  const _ChatRow({required this.c});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final b = theme.brightness;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        gradient: AppTheme.glassGradient(b),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppTheme.glassHighlight(b), width: 0.5),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(c.message, style: theme.textTheme.bodyMedium),
          const SizedBox(height: 4),
          Text(c.ts, style: theme.textTheme.labelSmall),
        ],
      ),
    );
  }
}
