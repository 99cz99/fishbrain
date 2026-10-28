import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:dfy_app/api.dart';
import 'package:dfy_app/pages/settings.dart';

class SettingsApi extends ApiClient {
  Map<String, dynamic>? saved;
  bool fail = false;
  @override
  Future<Map<String, dynamic>> settings() async {
    if (fail) throw Exception('offline');
    return {
      'name': 'Fish',
      'self_profile': {
        'values': ['existing'],
      },
    };
  }

  @override
  Future<Map<String, dynamic>> llm() async => {};
  @override
  Future<Map<String, dynamic>> heyboxStatus() async => {'enabled': false};
  @override
  Future<Map<String, dynamic>> updateSettings(
    Map<String, dynamic> value,
  ) async {
    saved = value;
    return value;
  }
}

void main() {
  testWidgets('password fields are single line', (tester) async {
    final api = SettingsApi();
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: SettingsPage(api: api)));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('LLM 配置'));
    await tester.tap(find.text('LLM 配置'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    final fields = tester.widgetList<TextField>(find.byType(TextField));
    expect(fields.where((f) => f.obscureText).single.maxLines, 1);
  });

  testWidgets('behavior route immediately rebuilds and saves only its fields', (
    tester,
  ) async {
    final api = SettingsApi();
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: SettingsPage(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('行为'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('半夜也能主动找我'));
    await tester.tap(find.text('半夜也能主动找我'));
    await tester.pumpAndSettle();
    final tile = tester.widget<SwitchListTile>(
      find.widgetWithText(SwitchListTile, '半夜也能主动找我'),
    );
    expect(tile.value, isFalse);
    await tester.ensureVisible(find.text('保存'));
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    expect(api.saved!['midnight_ok'], isFalse);
    expect(api.saved!.containsKey('self_profile'), isFalse);
    expect(api.saved!.containsKey('name'), isFalse);
  });

  testWidgets('failed loading can retry', (tester) async {
    final api = SettingsApi()..fail = true;
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: SettingsPage(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('重试连接'), findsOneWidget);
    api.fail = false;
    await tester.tap(find.text('重试连接'));
    await tester.pumpAndSettle();
    expect(find.text('身份'), findsOneWidget);
  });
}
