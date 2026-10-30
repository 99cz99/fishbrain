// api.dart — 连鱼脑本地 API（dart:io HttpClient，零外部依赖）
import 'dart:convert';
import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'models.dart';
import 'termux.dart';

class ApiException implements Exception {
  final String code;
  final String message;
  ApiException(this.code, this.message);
  @override
  String toString() => message;
}

String apiErrorMessage(Object error) {
  if (error is ApiException) return error.message;
  if (error is TimeoutException) return '请求超时，操作结果待确认，请查看当前状态';
  if (error is SocketException) return '无法连接鱼脑，请检查服务状态';
  return '操作未获确认，请查看状态后重试';
}

class ApiClient {
  final String base;
  final HttpClient _http = HttpClient();
  String? _token;
  Future<String?>? _loadingToken;
  static const requestTimeout = Duration(seconds: 20);

  Future<String?> _readToken() async {
    final result = await TermuxRunner().runAndWait(
      'cat /data/data/com.termux/files/home/dfy/.api-token',
      timeoutMs: 5000,
    );
    return result?.ok == true ? result!.stdout.trim() : null;
  }

  Future<String?> _authorization() async {
    if (_token?.isNotEmpty == true) return _token;
    _loadingToken ??= _readToken();
    try {
      return _token = await _loadingToken;
    } finally {
      _loadingToken = null;
    }
  }

  void close() => _http.close(force: true);

  ApiClient({this.base = 'http://127.0.0.1:8765'}) {
    _http.connectionTimeout = requestTimeout;
  }

  Future<dynamic> _request(String method, String path, {Object? body}) async {
    final uri = Uri.parse('$base$path');
    final token = await _authorization();
    final req = await _http.openUrl(method, uri).timeout(requestTimeout);
    if (token?.isNotEmpty == true) {
      req.headers.set('Authorization', 'Bearer $token');
    }
    req.headers.set('Content-Type', 'application/json');
    if (body != null) {
      final bytes = utf8.encode(jsonEncode(body));
      req.contentLength =
          bytes.length; // 显式设 Content-Length，避免 chunked 导致服务端读不到 body
      req.add(bytes);
    }
    final resp = await req.close().timeout(
      requestTimeout,
      onTimeout: () {
        req.abort();
        throw TimeoutException('请求超时');
      },
    );
    final text = await resp
        .transform(utf8.decoder)
        .join()
        .timeout(
          requestTimeout,
          onTimeout: () {
            req.abort();
            throw TimeoutException('读取响应超时');
          },
        );
    if (resp.statusCode == 401) _token = null;
    dynamic result;
    try {
      result = text.isEmpty ? null : jsonDecode(text);
    } on FormatException {
      throw ApiException(
        'invalid_response',
        '服务响应格式无效（HTTP ${resp.statusCode}）',
      );
    }
    if (result is Map && (result['ok'] == false || result['error'] != null)) {
      throw ApiException(
        '${result['code'] ?? 'rejected'}',
        '${result['error'] ?? '操作未获确认'}',
      );
    }
    if (resp.statusCode >= 400) {
      throw ApiException(
        'http_${resp.statusCode}',
        '服务请求失败（HTTP ${resp.statusCode}）',
      );
    }
    return result;
  }

  Future<Status> status() async =>
      Status.fromJson(await _request('GET', '/status') as Map<String, dynamic>);

  Future<List<LogEntry>> logs({String? category, int limit = 100}) async {
    final q = [
      if (category != null) 'category=$category',
      'limit=$limit',
    ].join('&');
    final j = await _request('GET', '/logs?$q') as Map<String, dynamic>;
    return (j['logs'] as List)
        .map((e) => LogEntry.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<LogEntry>> chat({int limit = 200}) async {
    final j =
        await _request('GET', '/chat?limit=$limit') as Map<String, dynamic>;
    return (j['chat'] as List)
        .map((e) => LogEntry.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<Memory>> memories({
    String? kind,
    String? subject,
    String? platform,
    int offset = 0,
  }) async {
    final q = [
      if (kind != null) 'kind=${Uri.encodeQueryComponent(kind)}',
      if (subject != null) 'subject=${Uri.encodeQueryComponent(subject)}',
      if (platform != null) 'platform=${Uri.encodeQueryComponent(platform)}',
    ].join('&');
    final j = await _request(
      'GET',
      '/memories?limit=1000&offset=$offset${q.isEmpty ? '' : '&$q'}',
    ) as Map<String, dynamic>;
    return (j['memories'] as List)
        .map((e) => Memory.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<void> deleteMemory(int id) async {
    await _request('DELETE', '/memories/$id');
  }

  Future<void> updateMemory(int id, Map<String, dynamic> changes) async {
    await _request('PUT', '/memories/$id', body: changes);
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

  Future<Map<String, dynamic>> qqQuickLogin() async =>
      await _request('POST', '/qq/quick_login') as Map<String, dynamic>;

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
      await _request('PUT', '/heybox', body: {'token': token})
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> lifeState() async =>
      await _request('GET', '/life/state') as Map<String, dynamic>;

  Future<Map<String, dynamic>> wake() async =>
      await _request('POST', '/life/wake') as Map<String, dynamic>;

  // 拉 NapCat 登录二维码（返回 PNG 字节）
  Future<Uint8List> qrcode() async {
    return _image('/qrcode');
  }

  Future<Uint8List> _image(String path) async {
    final token = await _authorization();
    final req = await _http
        .getUrl(Uri.parse('$base$path'))
        .timeout(requestTimeout);
    if (token?.isNotEmpty == true) {
      req.headers.set('Authorization', 'Bearer $token');
    }
    final resp = await req.close().timeout(
      requestTimeout,
      onTimeout: () {
        req.abort();
        throw TimeoutException('图片请求超时');
      },
    );
    if (resp.statusCode != 200) {
      await resp.drain<void>().timeout(requestTimeout);
      if (resp.statusCode == 401) _token = null;
      throw Exception('HTTP ${resp.statusCode}');
    }
    final bytes = await resp
        .fold<BytesBuilder>(BytesBuilder(), (b, chunk) => b..add(chunk))
        .timeout(
          requestTimeout,
          onTimeout: () {
            req.abort();
            throw TimeoutException('读取图片超时');
          },
        );
    return bytes.takeBytes();
  }

  // ---- 表情包管理 ----
  Future<List<Map<String, dynamic>>> stickers() async {
    final j = await _request('GET', '/stickers') as Map<String, dynamic>;
    return (j['stickers'] as List)
        .map((e) => e as Map<String, dynamic>)
        .toList();
  }

  Future<Uint8List> stickerImage(int id) async {
    return _image('/stickers/$id/image');
  }

  Future<Map<String, dynamic>> addSticker({
    String? base64,
    String? url,
    String ext = 'gif',
    required String tag,
  }) async {
    final body = <String, dynamic>{'tag': tag, 'ext': ext};
    if (base64 != null) body['base64'] = base64;
    if (url != null) body['url'] = url;
    return await _request('POST', '/stickers', body: body)
        as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateSticker(
    int id, {
    String? tag,
    bool? pinned,
  }) async {
    final body = <String, dynamic>{};
    if (tag != null) body['tag'] = tag;
    if (pinned != null) body['pinned'] = pinned;
    return await _request('PUT', '/stickers/$id', body: body)
        as Map<String, dynamic>;
  }

  Future<void> deleteSticker(int id) async {
    await _request('DELETE', '/stickers/$id');
  }

  Future<List<Approval>> pendingApprovals() async {
    final j =
        await _request('GET', '/approval/pending') as Map<String, dynamic>;
    return (j['pending'] as List)
        .map((e) => Approval.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<Approval>> recentApprovals() async {
    final j = await _request('GET', '/approval/recent') as Map<String, dynamic>;
    return (j['approvals'] as List)
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
    final j = await _request(
      'POST',
      '/command',
      body: {'command': cmd, 'args': args},
    ) as Map<String, dynamic>;
    return j['message'] as String? ?? '';
  }
}
