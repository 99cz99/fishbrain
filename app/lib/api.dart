// api.dart — 连鱼脑本地 API（dart:io HttpClient，零外部依赖）
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'models.dart';

class ApiClient {
  final String base;
  final HttpClient _http = HttpClient();

  ApiClient({this.base = 'http://127.0.0.1:8765'});

  Future<dynamic> _request(String method, String path, {Object? body}) async {
    final uri = Uri.parse('$base$path');
    final req = await _http.openUrl(method, uri);
    req.headers.set('Content-Type', 'application/json');
    if (body != null) {
      final bytes = utf8.encode(jsonEncode(body));
      req.contentLength = bytes.length; // 显式设 Content-Length，避免 chunked 导致服务端读不到 body
      req.add(bytes);
    }
    final resp = await req.close();
    final text = await resp.transform(utf8.decoder).join();
    if (resp.statusCode >= 400) {
      throw Exception('HTTP ${resp.statusCode}: $text');
    }
    return text.isEmpty ? null : jsonDecode(text);
  }

  Future<Status> status() async =>
      Status.fromJson(await _request('GET', '/status') as Map<String, dynamic>);

  Future<List<LogEntry>> logs({String? category, int limit = 100}) async {
    final q = [if (category != null) 'category=$category', 'limit=$limit'].join('&');
    final j = await _request('GET', '/logs?$q') as Map<String, dynamic>;
    return (j['logs'] as List)
        .map((e) => LogEntry.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<LogEntry>> chat({int limit = 200}) async {
    final j = await _request('GET', '/chat?limit=$limit') as Map<String, dynamic>;
    return (j['chat'] as List)
        .map((e) => LogEntry.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<Memory>> memories({String? kind, String? subject, String? platform}) async {
    final q = [
      if (kind != null) 'kind=$kind',
      if (subject != null) 'subject=$subject',
      if (platform != null) 'platform=$platform',
    ].join('&');
    final j = await _request('GET', '/memories${q.isEmpty ? '' : '?$q'}') as Map<String, dynamic>;
    return (j['memories'] as List)
        .map((e) => Memory.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<void> deleteMemory(int id) async {
    await _request('DELETE', '/memories/$id');
  }

  Future<Map<String, dynamic>> settings() async =>
      await _request('GET', '/settings') as Map<String, dynamic>;

  Future<Map<String, dynamic>> updateSettings(Map<String, dynamic> s) async =>
      await _request('PUT', '/settings', body: s) as Map<String, dynamic>;

  Future<Map<String, dynamic>> llm() async =>
      await _request('GET', '/llm') as Map<String, dynamic>;

  Future<Map<String, dynamic>> updateLlm(Map<String, dynamic> llm) async =>
      await _request('PUT', '/llm', body: llm) as Map<String, dynamic>;

  Future<Map<String, dynamic>> usage() async =>
      await _request('GET', '/usage') as Map<String, dynamic>;

  Future<Map<String, dynamic>> qqStatus() async =>
      await _request('GET', '/qq/status') as Map<String, dynamic>;

  Future<Map<String, dynamic>> qqStart() async =>
      await _request('POST', '/qq/start') as Map<String, dynamic>;

  Future<Map<String, dynamic>> qqStop() async =>
      await _request('POST', '/qq/stop') as Map<String, dynamic>;

  Future<Map<String, dynamic>> qqRefreshQr() async =>
      await _request('POST', '/qq/refresh_qr') as Map<String, dynamic>;

  Future<Map<String, dynamic>> applyOnebot() async =>
      await _request('POST', '/qq/apply_onebot') as Map<String, dynamic>;

  Future<Map<String, dynamic>> deployQq() async =>
      await _request('POST', '/deploy/qq') as Map<String, dynamic>;

  Future<Map<String, dynamic>> deployStatus() async =>
      await _request('GET', '/deploy/status') as Map<String, dynamic>;

  Future<Map<String, dynamic>> growthEvolve() async =>
      await _request('POST', '/growth/evolve') as Map<String, dynamic>;

  Future<Map<String, dynamic>> resetPersona() async =>
      await _request('POST', '/persona/reset') as Map<String, dynamic>;

  Future<Map<String, dynamic>> heyboxStatus() async =>
      await _request('GET', '/heybox/status') as Map<String, dynamic>;

  Future<Map<String, dynamic>> heyboxSign() async =>
      await _request('POST', '/heybox/sign') as Map<String, dynamic>;

  Future<Map<String, dynamic>> setHeyboxToken(String token) async =>
      await _request('PUT', '/heybox', body: {'token': token}) as Map<String, dynamic>;

  Future<Map<String, dynamic>> lifeState() async =>
      await _request('GET', '/life/state') as Map<String, dynamic>;

  Future<Map<String, dynamic>> wake() async =>
      await _request('POST', '/life/wake') as Map<String, dynamic>;

  // 拉 NapCat 登录二维码（返回 PNG 字节）
  Future<Uint8List> qrcode() async {
    final uri = Uri.parse('$base/qrcode');
    final req = await _http.openUrl('GET', uri);
    final resp = await req.close();
    if (resp.statusCode != 200) {
      throw Exception('HTTP ${resp.statusCode}');
    }
    final bytes = <int>[];
    await for (final chunk in resp) {
      bytes.addAll(chunk);
    }
    return Uint8List.fromList(bytes);
  }

  Future<List<Approval>> pendingApprovals() async {
    final j = await _request('GET', '/approval/pending') as Map<String, dynamic>;
    return (j['pending'] as List)
        .map((e) => Approval.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<void> approve(int id) async {
    await _request('POST', '/approval/$id/approve');
  }

  Future<void> reject(int id) async {
    await _request('POST', '/approval/$id/reject');
  }

  Future<String> command(String cmd, [List<String> args = const []]) async {
    final j = await _request('POST', '/command',
        body: {'command': cmd, 'args': args}) as Map<String, dynamic>;
    return j['message'] as String? ?? '';
  }
}
