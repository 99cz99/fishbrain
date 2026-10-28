import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:dfy_app/api.dart';
import 'package:dfy_app/models.dart';
import 'package:dfy_app/pages/chat.dart';

class SlowChatApi extends ApiClient {
  int calls = 0;
  final response = Completer<List<LogEntry>>();
  @override
  Future<List<LogEntry>> chat({int limit = 200}) {
    calls++;
    return response.future;
  }
}

void main() {
  testWidgets('polls do not overlap and disposal accepts late completion', (tester) async {
    final api = SlowChatApi();
    addTearDown(api.close);
    await tester.pumpWidget(MaterialApp(home: ChatPage(api: api)));
    await tester.pump(const Duration(seconds: 8));
    expect(api.calls, 1);
    await tester.pumpWidget(const SizedBox.shrink());
    api.response.complete([]);
    await tester.pump();
    expect(tester.takeException(), isNull);
  });
}
