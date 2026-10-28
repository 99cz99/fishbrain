import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:dfy_app/api.dart';
import 'package:dfy_app/models.dart';
import 'package:dfy_app/pages/memory.dart';

class MemoryApi extends ApiClient {
  Map<String, dynamic>? changed;
  bool deleted = false;
  bool fail = false;
  String kind = 'intention';
  @override
  Future<List<Memory>> memories({
    String? kind,
    String? subject,
    String? platform,
    int offset = 0,
  }) async {
    if (fail) throw Exception('offline');
    return deleted
        ? []
        : [
            Memory.fromJson({
              'id': 8,
              'kind': this.kind,
              'subject': '测试人物',
              'content': '周五问面试结果',
              'scope': 'owner',
              'entity_id': 'qq:test',
              'source_ids': [1, 2],
              'status': changed?['status'] ?? 'active',
            }),
          ];
  }

  @override
  Future<void> updateMemory(int id, Map<String, dynamic> changes) async {
    changed = changes;
  }

  @override
  Future<void> deleteMemory(int id) async {
    deleted = true;
  }
}

void main() {
  testWidgets('memory shows provenance and can cancel followup', (
    tester,
  ) async {
    final api = MemoryApi();
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: MemoryPage(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('依据：#1、#2'), findsOneWidget);
    await tester.tap(find.byTooltip('管理记忆'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('取消事项'));
    await tester.pumpAndSettle();
    expect(api.changed, {'status': 'cancelled'});
    expect(find.textContaining('已取消'), findsOneWidget);
  });

  testWidgets('forget requires explicit confirmation and refreshes', (
    tester,
  ) async {
    final api = MemoryApi()..kind = 'fact';
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: MemoryPage(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('管理记忆'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('忘记'));
    await tester.pumpAndSettle();
    expect(api.deleted, isFalse);
    expect(find.text('忘记这条记忆？'), findsOneWidget);
    await tester.tap(find.text('忘记'));
    await tester.pumpAndSettle();
    expect(api.deleted, isTrue);
    expect(find.text('它还没记住什么'), findsOneWidget);
  });

  testWidgets('memory fetch error is visible and retry works', (tester) async {
    final api = MemoryApi()..fail = true;
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: MemoryPage(api: api)));
    await tester.pumpAndSettle();
    expect(find.textContaining('读取记忆失败'), findsOneWidget);
    api.fail = false;
    await tester.tap(find.textContaining('读取记忆失败'));
    await tester.pumpAndSettle();
    expect(find.text('周五问面试结果'), findsOneWidget);
  });
}
