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
          theme: ThemeData(colorSchemeSeed: Colors.teal, useMaterial3: true),
          darkTheme: ThemeData(
            colorSchemeSeed: Colors.teal,
            brightness: Brightness.dark,
            useMaterial3: true,
          ),
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
      body: pages[_index],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.monitor_heart_outlined), label: '状态'),
          NavigationDestination(icon: Icon(Icons.chat_bubble_outline), label: '聊天'),
          NavigationDestination(icon: Icon(Icons.fact_check_outlined), label: '审批'),
          NavigationDestination(icon: Icon(Icons.psychology_outlined), label: '记忆'),
          NavigationDestination(icon: Icon(Icons.tune), label: '设定'),
          NavigationDestination(icon: Icon(Icons.rocket_launch_outlined), label: '部署'),
        ],
      ),
    );
  }
}
