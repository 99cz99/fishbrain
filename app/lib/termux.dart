// termux.dart — 通过 Termux 的 RUN_COMMAND 广播在 Termux 里执行 shell 命令
// 用途：冷启动鱼脑、把鱼脑代码/部署脚本写进 Termux
// run()：发完即忘（用于长驻进程，如起 server.py）。
// runAndWait()：等命令结束回传 stdout/stderr/exitCode（用于有限时长的安装命令）。
import 'dart:convert';

import 'package:flutter/services.dart';

class TermuxResult {
  final int exitCode;
  final String stdout;
  final String stderr;
  final String errmsg;
  TermuxResult({
    required this.exitCode,
    required this.stdout,
    required this.stderr,
    this.errmsg = '',
  });
  bool get ok => exitCode == 0;
}

class TermuxRunner {
  static const _channel = MethodChannel('dfy/termux');

  /// 发完即忘，返回 intent 是否成功发出（不等命令结束、不拿输出）。
  Future<bool> run(String command, {String? workdir}) async {
    try {
      return await _channel.invokeMethod<bool>('runCommand', {
            'command': command,
            'workdir': workdir,
            'wait': false,
          }) ??
          false;
    } on PlatformException {
      return false;
    }
  }

  /// 等命令跑完，回传 stdout/stderr/exitCode。命令失败或 Termux 未装返回 null。
  /// [timeoutMs] 超时（默认 30 分钟，装环境等长命令用；写文件等秒级命令传小值）。
  Future<TermuxResult?> runAndWait(String command,
      {String? workdir, int? timeoutMs}) async {
    try {
      final m = await _channel.invokeMethod<Map<dynamic, dynamic>>('runCommand', {
        'command': command,
        'workdir': workdir,
        'wait': true,
        'timeoutMs': timeoutMs,
      });
      if (m == null) return null;
      return TermuxResult(
        exitCode: (m['exitCode'] as num?)?.toInt() ?? -1,
        stdout: (m['stdout'] as String?) ?? '',
        stderr: (m['stderr'] as String?) ?? '',
        errmsg: (m['errmsg'] as String?) ?? '',
      );
    } on PlatformException {
      return null;
    }
  }

  /// 把 [content] 写到 Termux 里的 [path]（base64 中转，规避 shell 转义）。
  /// 返回是否写成功（命令 exitCode == 0）。
  Future<bool> writeFile(String path, String content) async {
    final b64 = base64Encode(utf8.encode(content));
    final dir = path.contains('/') ? path.substring(0, path.lastIndexOf('/')) : '.';
    final r = await runAndWait(
      'mkdir -p "$dir" && echo "$b64" | base64 -d > "$path"',
      timeoutMs: 30000,
    );
    return r?.ok ?? false;
  }

  /// 预检：Termux 是否装、RUN_COMMAND 权限是否授。失败返回 null。
  Future<Map<String, dynamic>?> preflight() async {
    try {
      final m = await _channel.invokeMethod<Map<dynamic, dynamic>>('preflight');
      return m?.map((k, v) => MapEntry('$k', v));
    } on PlatformException {
      return null;
    }
  }

  Future<void> requestPermission() async {
    try {
      await _channel.invokeMethod('requestPermission');
    } on PlatformException {
      // 忽略，后续 preflight 再查
    }
  }

  Future<void> openAppSettings() async {
    try {
      await _channel.invokeMethod('openAppSettings');
    } on PlatformException {
      // 忽略
    }
  }
}
