import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:dfy_app/api.dart';
import 'package:dfy_app/models.dart';
import 'package:dfy_app/pages/wizard.dart';

class WizardApi extends ApiClient {
  int applied = 0;
  @override
  Future<Map<String, dynamic>> applyOnebot() async {
    applied++;
    return {'ok': true, 'already': true};
  }

  @override
  Future<Status> status() async => Status.fromJson({});
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('dfy/termux');

  testWidgets(
    'upgrade stages and checks before stopping and skips model setup',
    (tester) async {
      final commands = <String>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
            if (call.method == 'preflight') {
              return {'termuxInstalled': true, 'runCommandGranted': true};
            }
            final args = call.arguments as Map;
            commands.add(args['command'] as String);
            if (args['wait'] == false) return true;
            return {'exitCode': 0, 'stdout': '', 'stderr': ''};
          });
      addTearDown(
        () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(channel, null),
      );
      final api = WizardApi();
      addTearDown(api.close);
      await tester.pumpWidget(
        MaterialApp(
          home: WizardPage(
            api: api,
            assetLoader: (_) async => '# test fixture',
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('我装好 Termux 了'));
      await tester.tap(find.text('我装好 Termux 了'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('开始装鱼脑'));
      await tester.pumpAndSettle();
      expect(find.text('鱼脑更新完成'), findsOneWidget);
      expect(api.applied, 1);
      expect(commands.any((c) => c.contains('.update/onebot11.json')), isTrue);
      final checked = commands.indexWhere((c) => c.contains('py_compile'));
      final stopped = commands.indexWhere((c) => c.contains('pkill'));
      final activated = commands.indexWhere((c) => c.startsWith('cp '));
      expect(checked, greaterThanOrEqualTo(0));
      expect(stopped, greaterThan(checked));
      expect(activated, greaterThan(stopped));
      expect(commands.any((c) => c.contains('.update/server.py')), isTrue);
      expect(
        commands.any((c) => c.contains('.update/memory_system.py')),
        isTrue,
      );
      expect(commands.any((c) => c.contains('.update/personality.py')), isTrue);
      expect(
        commands.where((c) => c.contains('config.toml')).single,
        contains('if [ ! -e'),
      );
      expect(
        commands.any((c) => c.contains('qqStop') || c.contains('qqStart')),
        isFalse,
      );
    },
  );
}
