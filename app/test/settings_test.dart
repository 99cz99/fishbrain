import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:dfy_app/api.dart';
import 'package:dfy_app/pages/settings.dart';

class SettingsApi extends ApiClient {
  Map<String, dynamic>? saved;
  String revision = 'revision-1';
  String trait = 'existing';
  String growthState = 'unchanged';
  int reads = 0;
  bool fail = false;
  @override
  Future<Map<String, dynamic>> settings() async {
    reads++;
    if (fail) throw Exception('offline');
    return {
      'name': 'Fish',
      'self_profile_revision': revision,
      'growth_status': {'state': growthState},
      'self_profile': {
        'values': [trait],
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

  @override
  Future<Map<String, dynamic>> growthEvolve() async {
    growthState = 'running';
    return {'ok': true};
  }
}

void main() {
  testWidgets('opening profile reads latest value and saves its revision', (
    tester,
  ) async {
    final api = SettingsApi();
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: SettingsPage(api: api)));
    await tester.pumpAndSettle();
    api.trait = 'new trait';
    api.revision = 'revision-2';
    await tester.tap(find.text('自我画像'));
    await tester.pumpAndSettle();
    expect(find.text('new trait'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('保存'),
      250,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    expect(api.saved!['self_profile_revision'], 'revision-2');
  });

  testWidgets('self reflection polls completion and refreshes profile', (
    tester,
  ) async {
    final api = SettingsApi();
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: SettingsPage(api: api)));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('让它自省一次'), 250);
    await tester.tap(find.text('让它自省一次'));
    await tester.pump();
    expect(find.text('正在自省并检查人格成长…'), findsOneWidget);
    api.growthState = 'updated';
    api.trait = 'grown trait';
    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();
    expect(find.text('画像已更新'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('自我画像'), -250);
    await tester.tap(find.text('自我画像'));
    await tester.pumpAndSettle();
    expect(find.text('grown trait'), findsOneWidget);
  });

  testWidgets(
    'dirty draft retains old revision so server can reject stale save',
    (tester) async {
      final api = SettingsApi();
      addTearDown(api.close);
      await tester.pumpWidget(MaterialApp(home: SettingsPage(api: api)));
      await tester.pumpAndSettle();
      await tester.tap(find.text('自我画像'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'my draft');
      await tester.pageBack();
      await tester.pumpAndSettle();
      api.trait = 'background change';
      api.revision = 'revision-2';
      await tester.tap(find.text('自我画像'));
      await tester.pumpAndSettle();
      expect(find.text('my draft'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.text('保存'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('保存'));
      await tester.pumpAndSettle();
      expect(api.saved!['self_profile_revision'], 'revision-1');
    },
  );

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
    await tester.scrollUntilVisible(
      find.text('保存'),
      250,
      scrollable: find.byType(Scrollable).first,
    );
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
