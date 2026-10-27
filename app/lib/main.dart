// main.dart — Fishbrain 监管 app 入口
import 'package:flutter/material.dart';

import 'api.dart';
import 'pages/approval.dart';
import 'pages/chat.dart';
import 'pages/deploy.dart';
import 'pages/memory.dart';
import 'pages/settings.dart';
import 'pages/status.dart';
import 'theme.dart';
import 'widgets.dart';

void main() => runApp(const DfyApp());

class DfyApp extends StatelessWidget {
  const DfyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: themeNotifier,
      builder: (context, mode, _) {
        return MaterialApp(
          title: 'Fishbrain',
          debugShowCheckedModeBanner: false,
          theme: AppTheme.light,
          darkTheme: AppTheme.dark,
          themeMode: mode,
          home: const HomePage(),
        );
      },
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final ApiClient _api = ApiClient();
  int _index = 0;

  static const _tabs = <(IconData, String)>[
    (Icons.monitor_heart_outlined, '状态'),
    (Icons.chat_bubble_outline, '聊天'),
    (Icons.fact_check_outlined, '审批'),
    (Icons.psychology_outlined, '记忆'),
    (Icons.tune, '设定'),
    (Icons.rocket_launch_outlined, '部署'),
  ];

  @override
  Widget build(BuildContext context) {
    final pages = [
      StatusPage(api: _api),
      ChatPage(api: _api),
      ApprovalPage(api: _api),
      MemoryPage(api: _api),
      SettingsPage(api: _api),
      DeployPage(api: _api),
    ];
    return Scaffold(
      extendBody: true,
      body: pages[_index],
      bottomNavigationBar: Padding(
        padding: EdgeInsets.fromLTRB(
          16,
          0,
          16,
          10 + MediaQuery.paddingOf(context).bottom,
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(26),
          child: GlassBar(
            child: SizedBox(
              height: 44,
              child: Row(
                children: [
                  for (var i = 0; i < _tabs.length; i++)
                    Expanded(child: _tabItem(i)),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _tabItem(int i) {
    final scheme = Theme.of(context).colorScheme;
    final selected = i == _index;
    final color = selected ? scheme.primary : scheme.onSurfaceVariant;
    return InkWell(
      onTap: () => setState(() => _index = i),
      borderRadius: BorderRadius.circular(20),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(_tabs[i].$1, size: 22, color: color),
          const SizedBox(height: 2),
          Text(
            _tabs[i].$2,
            style: TextStyle(
              fontSize: 10,
              fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
              color: color,
            ),
          ),
        ],
      ),
    );
  }
}
