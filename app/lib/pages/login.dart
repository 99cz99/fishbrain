// QQ login observations and queued operations are shown separately.
import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../theme.dart';

class LoginPage extends StatefulWidget {
  final ApiClient api;
  const LoginPage({super.key, required this.api});
  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  Uint8List? _qr;
  QQControlStatus _status = QQControlStatus({});
  bool _loading = true, _refreshing = false, _busy = false;
  bool _appliedOnebot = false;
  String _error = '';
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _tick();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _tick());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _tick() async {
    if (_refreshing || !mounted) return;
    _refreshing = true;
    try {
      final q = await widget.api.qqStatus();
      final status = QQControlStatus(q);
      Uint8List? qr;
      if (q['qr'] == true && !status.authenticated && !status.busy) {
        qr = await widget.api.qrcode();
      }
      if (!mounted) return;
      setState(() {
        _status = status;
        _loading = false;
        _qr = qr;
        _error = '';
      });
      // Old per-account config filenames cannot establish current authentication.
      if (status.modern &&
          status.authenticated &&
          !status.online &&
          !status.busy &&
          status.operation['status'] != 'unknown' &&
          !_appliedOnebot) {
        _appliedOnebot = true;
        try {
          final result = await widget.api.applyOnebot();
          if (mounted) {
            setState(() => _status = QQControlStatus({...q, ...result}));
          }
        } catch (error) {
          if (mounted) setState(() => _error = apiErrorMessage(error));
        }
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _loading = false;
          _qr = null;
          _error = apiErrorMessage(error);
        });
      }
    } finally {
      _refreshing = false;
    }
  }

  Future<void> _submit(bool quick) async {
    if (_busy || _status.busy) return;
    setState(() => _busy = true);
    try {
      final result = quick
          ? await widget.api.qqQuickLogin()
          : await widget.api.qqRefreshQr();
      if (!mounted) return;
      setState(() {
        _status = QQControlStatus({..._status.data, ...result});
        _error = '';
      });
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('操作已提交，等待 QQ 状态确认')));
    } catch (error) {
      if (mounted) {
        setState(() => _error = apiErrorMessage(error));
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(_error)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    _tick();
  }

  @override
  Widget build(BuildContext context) {
    final blocked = _busy || _status.busy;
    final authenticated = _status.authenticated && _error.isEmpty;
    final qrAge = (_status.data['qr_age'] as num?)?.toInt() ?? -1;
    return Scaffold(
      appBar: AppBar(title: const Text('扫码登录 QQ')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      authenticated
                          ? Icons.check_circle
                          : Icons.qr_code_scanner,
                      color: authenticated
                          ? AppColors.success
                          : AppColors.textSecondaryLight,
                      size: 64,
                    ),
                    const SizedBox(height: 16),
                    Text(
                      _error.isNotEmpty ? _error : _status.message,
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    if (_qr != null && !authenticated) ...[
                      const SizedBox(height: 16),
                      Container(
                        color: Colors.white,
                        padding: const EdgeInsets.all(16),
                        child: Image.memory(
                          _qr!,
                          width: 220,
                          height: 220,
                          gaplessPlayback: true,
                        ),
                      ),
                      const SizedBox(height: 12),
                      Text('二维码 ${qrAge < 0 ? 0 : qrAge} 秒前生成，请尽快扫码授权'),
                    ],
                    if (!authenticated) ...[
                      const SizedBox(height: 20),
                      OutlinedButton.icon(
                        onPressed: blocked ? null : () => _submit(false),
                        icon: const Icon(Icons.qr_code),
                        label: const Text('重新出码'),
                      ),
                      if (_status.data['quick_login_configured'] == true) ...[
                        const SizedBox(height: 8),
                        OutlinedButton.icon(
                          onPressed: blocked ? null : () => _submit(true),
                          icon: const Icon(Icons.bolt),
                          label: const Text('尝试快捷登录'),
                        ),
                      ],
                    ],
                  ],
                ),
              ),
            ),
    );
  }
}
