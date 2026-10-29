import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:dfy_app/api.dart';
import 'package:dfy_app/models.dart';
import 'package:dfy_app/pages/approval.dart';

class ApprovalApi extends ApiClient {
  String state = 'unconfirmed';
  String authorizationStatus = 'approved';
  int approvals = 0;
  @override
  Future<List<Approval>> recentApprovals() async => [
    Approval.fromJson({
      'id': 1,
      'kind': 'publish',
      'summary': '测试动态',
      'status': authorizationStatus,
      'execution_state': state,
    }),
  ];
  @override
  Future<void> approve(int id) async {
    approvals++;
    authorizationStatus = 'approved';
    state = 'running';
  }
}

void main() {
  testWidgets('unknown execution remains visible without a repeat button', (
    tester,
  ) async {
    final api = ApprovalApi();
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: ApprovalPage(api: api)));
    await tester.pump();
    expect(find.textContaining('执行结果未确认'), findsOneWidget);
    expect(find.text('同意'), findsNothing);
    api.state = 'failed_or_partial';
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(find.textContaining('执行失败或部分完成'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('approval authorization and running state are separate', (
    tester,
  ) async {
    final api = ApprovalApi()..authorizationStatus = 'pending';
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: ApprovalPage(api: api)));
    await tester.pump();
    await tester.tap(find.text('同意'));
    await tester.pump();
    await tester.pump();
    expect(api.approvals, 1);
    expect(find.textContaining('正在执行'), findsOneWidget);
    expect(find.text('同意'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
}
