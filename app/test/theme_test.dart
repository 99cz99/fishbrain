import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dfy_app/theme.dart';
import 'package:dfy_app/widgets.dart';

// 设计系统冒烟测试：验证浅色/深色主题能正常构建，共享组件能渲染、不抛异常。
void main() {
  testWidgets('浅色/深色主题 + 共享组件渲染正常', (tester) async {
    for (final theme in [AppTheme.light, AppTheme.dark]) {
      await tester.pumpWidget(
        MaterialApp(
          theme: theme,
          home: Scaffold(
            body: ListView(
              padding: const EdgeInsets.all(16),
              children: const [
                SectionHeader('环境'),
                SectionCard(children: [
                  InfoRow(label: '状态', value: '在线'),
                  StatusPill(color: AppColors.success, label: '在线'),
                ]),
                EmptyState(icon: Icons.inbox_outlined, title: '空态'),
              ],
            ),
          ),
        ),
      );

      expect(find.text('环境'), findsOneWidget);
      expect(find.text('在线'), findsNWidgets(2)); // InfoRow 值 + StatusPill 文案
      expect(find.text('空态'), findsOneWidget);

      // 校验主题关键项已生效
      final ctx = tester.element(find.byType(SectionCard));
      expect(Theme.of(ctx).brightness, theme.brightness);

      // 清理，避免两个主题间的状态残留
      await tester.pumpWidget(const SizedBox.shrink());
    }
  });
}
