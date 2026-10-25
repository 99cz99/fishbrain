import 'package:flutter/material.dart';

/// 全局主题模式（浅色/深色/跟随系统）。设定页改它，main.dart 监听它。
final ValueNotifier<ThemeMode> themeNotifier = ValueNotifier(ThemeMode.system);
