// chat.dart — 聊天记录页
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';

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

  Future<void> _refresh() async {
    try {
      final c = await widget.api.chat(limit: 200);
      if (mounted) setState(() => _chat = c);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('聊天记录')),
      body: _chat.isEmpty
          ? const Center(child: Text('还没有聊天记录'))
          : ListView.builder(
              itemCount: _chat.length,
              itemBuilder: (_, i) {
                final c = _chat[i];
                return ListTile(
                  leading: const Icon(Icons.chat_bubble_outline),
                  title: Text(c.message),
                  subtitle: Text(c.ts),
                );
              },
            ),
    );
  }
}
