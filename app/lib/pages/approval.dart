// approval.dart — 审批页：待审批请求 + 同意/拒绝
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';

class ApprovalPage extends StatefulWidget {
  final ApiClient api;
  const ApprovalPage({super.key, required this.api});

  @override
  State<ApprovalPage> createState() => _ApprovalPageState();
}

class _ApprovalPageState extends State<ApprovalPage> {
  List<Approval> _pending = [];
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
      final p = await widget.api.pendingApprovals();
      if (mounted) setState(() => _pending = p);
    } catch (_) {}
  }

  Future<void> _act(int id, String action) async {
    try {
      if (action == 'approve') {
        await widget.api.approve(id);
      } else {
        await widget.api.reject(id);
      }
      _refresh();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('审批')),
      body: _pending.isEmpty
          ? const Center(child: Text('没有待审批的请求'))
          : ListView.builder(
              itemCount: _pending.length,
              itemBuilder: (_, i) {
                final a = _pending[i];
                return Card(
                  child: ListTile(
                    title: Text(a.summary),
                    subtitle: Text('${a.kind} · ${a.createdAt}'),
                    trailing: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        IconButton(
                          onPressed: () => _act(a.id, 'approve'),
                          icon: const Icon(Icons.check_circle, color: Colors.green),
                        ),
                        IconButton(
                          onPressed: () => _act(a.id, 'reject'),
                          icon: const Icon(Icons.cancel, color: Colors.red),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
    );
  }
}
