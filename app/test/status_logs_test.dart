import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:dfy_app/api.dart';
import 'package:dfy_app/models.dart';
import 'package:dfy_app/pages/status.dart';
import 'package:dfy_app/theme.dart';

class StatusLogsApi extends ApiClient {
  @override
  Future<Status> status() async => Status.fromJson({'state': 'idle'});
  @override
  Future<Map<String, dynamic>> usage() async => {};
  @override
  Future<Map<String, dynamic>> lifeState() async => {};
  @override
  Future<List<LogEntry>> logs({String? category, int limit = 100}) async => [
    for (final level in ['info', 'warning', 'error'])
      LogEntry.fromJson({
        'level': level,
        'category': 'life',
        'message': 'test-$level',
        'ts': '2026-10-30 12:00:00',
      }),
  ];
}

void main() {
  testWidgets(
    'issue filter, severity icons and export include heartbeat warnings',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(900, 1800));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final api = StatusLogsApi();
      addTearDown(api.close);
      String copied = '';
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (call) async {
          if (call.method == 'Clipboard.setData') {
            copied = call.arguments['text'] as String;
          }
          return null;
        },
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          SystemChannels.platform,
          null,
        ),
      );
      await tester.pumpWidget(MaterialApp(home: StatusPage(api: api)));
      await tester.pump();
      expect(find.text('test-info'), findsOneWidget);
      final warning = tester.widget<Icon>(
        find.byIcon(Icons.warning_amber_rounded),
      );
      expect(warning.color, AppColors.warning);
      expect(
        tester.widget<Icon>(find.byIcon(Icons.error_outline)).color,
        AppColors.danger,
      );
      await tester.tap(find.text('只看异常'));
      await tester.pump();
      expect(find.text('test-info'), findsNothing);
      expect(find.text('test-warning'), findsOneWidget);
      expect(find.text('test-error'), findsOneWidget);
      await tester.tap(find.byTooltip('导出日志'));
      await tester.pump();
      expect(copied, contains('[warning/life] test-warning'));
      expect(copied, contains('[error/life] test-error'));
      expect(copied, isNot(contains('test-info')));
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
}
