import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:dfy_app/api.dart';
import 'package:dfy_app/models.dart';
import 'package:dfy_app/pages/login.dart';

class QQApi extends ApiClient {
  Map<String, dynamic> state = {
    'control_version': 2,
    'running': true,
    'quick_login_configured': true,
    'login_uin': '123',
    'online': false,
    'account_ready': false,
  };
  int configurations = 0, logins = 0;
  bool rejectLogin = false;
  @override
  Future<Map<String, dynamic>> qqStatus() async => state;
  @override
  Future<Map<String, dynamic>> applyOnebot() async {
    configurations++;
    return {
      'ok': true,
      'operation': {'kind': 'configure', 'status': 'queued'},
    };
  }

  @override
  Future<Map<String, dynamic>> qqQuickLogin() async {
    logins++;
    if (rejectLogin) throw ApiException('needs_scan', '该账号需要扫码验证');
    state = {
      ...state,
      'operation': {
        'kind': 'quick_login',
        'status': 'waiting',
        'phase': 'confirming',
      },
    };
    return {'ok': true, 'queued': true, 'operation': state['operation']};
  }
}

void main() {
  test('current online status supersedes an old unknown login result but stopping remains visible', () {
    final data = <String, dynamic>{
      'control_version': 2,
      'account_ready': true,
      'online': true,
      'operation': {
        'kind': 'quick_login',
        'status': 'unknown',
        'error': '旧登录结果未知',
      },
    };
    expect(QQControlStatus(data).message, 'QQ 已登录，OneBot 已连接');
    data['operation'] = {
      'kind': 'stop',
      'status': 'running',
      'phase': 'stopping',
    };
    expect(QQControlStatus(data).message, contains('正在停止'));
  });
  test('native authentication and business availability are independent', () {
    final s = QQControlStatus({
      'control_version': 2,
      'account_ready': true,
      'online': false,
    });
    expect(s.authenticated, isTrue);
    expect(s.online, isFalse);
    expect(s.message, contains('等待 OneBot'));
  });
  testWidgets('old config evidence never applies OneBot or restarts QQ', (
    tester,
  ) async {
    final api = QQApi();
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: LoginPage(api: api)));
    await tester.pump();
    await tester.pump(const Duration(seconds: 6));
    expect(api.configurations, 0);
    expect(find.textContaining('自愈中'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });
  testWidgets(
    'ready account with broken transport configures once without login',
    (tester) async {
      final api = QQApi()..state['account_ready'] = true;
      addTearDown(api.close);
      await tester.pumpWidget(MaterialApp(home: LoginPage(api: api)));
      await tester.pump();
      await tester.pump(const Duration(seconds: 6));
      expect(api.configurations, 1);
      expect(api.logins, 0);
      expect(find.text('QQ 已登录，等待 OneBot 连接'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
  testWidgets('queued login disables another submission and shows pending', (
    tester,
  ) async {
    final api = QQApi();
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: LoginPage(api: api)));
    await tester.pump();
    await tester.tap(find.text('尝试快捷登录'));
    await tester.pump();
    expect(api.logins, 1);
    expect(find.textContaining('QQ 已登录'), findsNothing);
    final button = tester.widget<OutlinedButton>(
      find.widgetWithText(OutlinedButton, '尝试快捷登录'),
    );
    expect(button.onPressed, isNull);
    expect(find.textContaining('正在查询 QQ 状态'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
  });
  testWidgets(
    'business rejection keeps its reason instead of claiming fish is offline',
    (tester) async {
      final api = QQApi()..rejectLogin = true;
      addTearDown(api.close);
      await tester.pumpWidget(MaterialApp(home: LoginPage(api: api)));
      await tester.pump();
      await tester.tap(find.text('尝试快捷登录'));
      await tester.pump();
      expect(find.text('该账号需要扫码验证'), findsWidgets);
      expect(find.textContaining('鱼脑在线吗'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
}
