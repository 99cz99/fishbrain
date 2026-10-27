// approval.dart — 审批页：待审批请求 + 同意/拒绝
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';

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
          ? const EmptyState(
              icon: Icons.fact_check_outlined,
              title: '没有待审批的请求',
              subtitle: '它想对外做什么时，会在这里等你拍板',
            )
          : ListView.builder(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 120),
              itemCount: _pending.length,
              itemBuilder: (_, i) => Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: _ApprovalCard(a: _pending[i], onAct: _act),
              ),
            ),
    );
  }
}

class _ApprovalCard extends StatelessWidget {
  final Approval a;
  final Future<void> Function(int id, String action) onAct;
  const _ApprovalCard({required this.a, required this.onAct});

  String _kindLabel(String k) => switch (k) {
    'publish' => '发空间',
    'money' => '花钱',
    _ => k,
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SectionCard(
      children: [
        Row(
          children: [
            StatusPill(color: AppColors.warning, label: _kindLabel(a.kind)),
            const Spacer(),
            Text(a.createdAt, style: theme.textTheme.labelSmall),
          ],
        ),
        const SizedBox(height: 12),
        Text(a.summary, style: theme.textTheme.bodyLarge),
        const SizedBox(height: 14),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: () => onAct(a.id, 'reject'),
                icon: const Icon(Icons.close),
                label: const Text('拒绝'),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: FilledButton.icon(
                onPressed: () => onAct(a.id, 'approve'),
                icon: const Icon(Icons.check),
                label: const Text('同意'),
              ),
            ),
          ],
        ),
      ],
    );
  }
}
