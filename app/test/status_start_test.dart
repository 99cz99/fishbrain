import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:dfy_app/api.dart';
import 'package:dfy_app/models.dart';
import 'package:dfy_app/pages/status.dart';

class StartingApi extends ApiClient {
  bool sent = false;
  int failures = 5;
  int checks = 0;
  @override
  Future<Status> status() async {
    if (!sent || checks++ < failures) throw Exception('not ready');
    return Status.fromJson({'name': '启动验证', 'state': 'idle'});
  }

  @override
  Future<List<LogEntry>> logs({String? category, int limit = 100}) async => [];
  @override
  Future<Map<String, dynamic>> usage() async => {};
  @override
  Future<Map<String, dynamic>> lifeState() async => {};
}

void main() {
  const channel = MethodChannel('dfy/termux');
  for (final restarting in [false, true]) {
    testWidgets(
      'waits for slow ${restarting ? 'restart' : 'cold start'} readiness',
      (tester) async {
        await tester.binding.setSurfaceSize(const Size(900, 1800));
        addTearDown(() => tester.binding.setSurfaceSize(null));
        final api = StartingApi();
        addTearDown(api.close);
        var launches = 0;
        tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          channel,
          (call) async {
            if (call.method == 'preflight') {
              return {'termuxInstalled': true, 'runCommandGranted': true};
            }
            if (call.method == 'runCommand') {
              if (call.arguments['wait'] == true) {
                return {'exitCode': 0, 'stdout': '', 'stderr': ''};
              }
              api.sent = true;
              launches++;
              return true;
            }
            return null;
          },
        );
        addTearDown(
          () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
            channel,
            null,
          ),
        );
        await tester.pumpWidget(MaterialApp(home: StatusPage(api: api)));
        await tester.pump();
        await tester.tap(find.text(restarting ? '重启' : '启动'));
        await tester.pump();
        for (var i = 0; i < 4; i++) {
          await tester.pump(const Duration(seconds: 2));
        }
        expect(find.text('启动中…'), findsOneWidget);
        expect(launches, 1);
        expect(find.textContaining('启动尚未确认'), findsNothing);
        await tester.pump(const Duration(seconds: 2));
        await tester.pump();
        expect(find.text('启动中…'), findsNothing);
        expect(find.text('启动验证'), findsOneWidget);
        expect(
          find.text(restarting ? '鱼脑已重启（QQ 保持在线）' : '鱼脑已启动'),
          findsOneWidget,
        );
        await tester.pumpWidget(const SizedBox.shrink());
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets(
    'missing command permission gives a specific error without launching',
    (tester) async {
      final api = StartingApi();
      addTearDown(api.close);
      var launches = 0;
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(channel, (
        call,
      ) async {
        if (call.method == 'preflight') {
          return {'termuxInstalled': true, 'runCommandGranted': false};
        }
        launches++;
        return false;
      });
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          channel,
          null,
        ),
      );
      await tester.pumpWidget(MaterialApp(home: StatusPage(api: api)));
      await tester.pump();
      await tester.ensureVisible(find.text('启动'));
      await tester.tap(find.text('启动'));
      await tester.pump();
      expect(find.textContaining('缺少 Termux 命令权限'), findsOneWidget);
      expect(launches, 0);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
}
