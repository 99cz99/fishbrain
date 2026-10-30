// models.dart — 鱼脑 API 返回数据的模型
class QQControlStatus {
  final Map<String, dynamic> data;
  QQControlStatus(this.data);
  Map get operation =>
      data['operation'] is Map ? data['operation'] as Map : const {};
  bool get modern => data['control_version'] == 2;
  bool get busy =>
      const ['queued', 'running', 'waiting'].contains(operation['status']);
  bool get stopping => busy && operation['kind'] == 'stop';
  bool get online => data['online'] == true;
  bool get authenticated => modern ? data['account_ready'] == true : online;
  String get message {
    // Current positive evidence supersedes an old login error. Stop/restart
    // work still takes priority while it is actively changing the session.
    if (!stopping && operation['phase'] != 'stopping') {
      if (online) return 'QQ 已登录，OneBot 已连接';
      if (authenticated) return 'QQ 已登录，等待 OneBot 连接';
    }
    final error = operation['error'];
    if (error is String && error.isNotEmpty) return error;
    if (busy) {
      switch (operation['phase']) {
        case 'stopping':
          return '正在停止 QQ，等待进程退出';
        case 'starting':
          return '正在启动 QQ，等待登录结果';
        case 'initializing':
          return 'QQ 正在初始化，请等待状态确认';
        case 'confirming':
        case 'checking':
          return '请求结果待确认，正在查询 QQ 状态';
        case 'generating_qrcode':
          return '正在生成新二维码';
        default:
          return 'QQ 操作已排队，等待状态确认';
      }
    }
    if (operation['status'] == 'unknown') return '上次操作结果待确认，请查看当前状态';
    if (online) return 'QQ 已登录，OneBot 已连接';
    if (authenticated) return 'QQ 已登录，等待 OneBot 连接';
    if (data['desired_state'] == 'stopped') return 'QQ 已明确停止自动启动';
    if (data['qr'] == true) return '等待扫码授权';
    if (data['login_accepted'] == true) return 'QQ 正在初始化，请等待状态确认';
    if (data['running'] != true) return 'QQ 未运行';
    if (data['account_state'] == 'offline') return 'QQ 账号离线，可尝试快捷登录或扫码';
    if (data['control_error'] != null) return '暂时无法查询 QQ 登录状态';
    return 'QQ 运行中，登录状态待确认';
  }
}

class Status {
  final String name;
  final String state;
  final String currentTask;
  final int uptime;
  final List<dynamic> platforms;

  Status.fromJson(Map<String, dynamic> j)
    : name = j['name'] as String? ?? '',
      state = j['state'] as String? ?? 'idle',
      currentTask = j['current_task'] as String? ?? '',
      uptime = j['uptime'] as int? ?? 0,
      platforms = (j['platforms'] as List?) ?? const [];
}

class LogEntry {
  final int id;
  final String ts;
  final String level;
  final String category;
  final String message;

  bool get isIssue => level == 'error' || level == 'warning';

  LogEntry.fromJson(Map<String, dynamic> j)
    : id = j['id'] as int? ?? 0,
      ts = j['ts'] as String? ?? '',
      level = j['level'] as String? ?? '',
      category = j['category'] as String? ?? '',
      message = j['message'] as String? ?? '';
}

class Memory {
  final int id;
  final String kind;
  final String subject;
  final String content;
  final String platform;
  final String createdAt;
  final String scope;
  final String entityId;
  final String status;
  final List<int> sourceIds;
  final bool pinned;

  Memory.fromJson(Map<String, dynamic> j)
    : id = j['id'] as int? ?? 0,
      kind = j['kind'] as String? ?? '',
      subject = j['subject'] as String? ?? '',
      content = j['content'] as String? ?? '',
      platform = j['platform'] as String? ?? '',
      createdAt = j['created_at'] as String? ?? '',
      scope = j['scope'] as String? ?? '',
      entityId = j['entity_id'] as String? ?? '',
      status = j['status'] as String? ?? 'active',
      sourceIds = (j['source_ids'] as List? ?? []).whereType<int>().toList(),
      pinned = j['pinned'] == 1 || j['pinned'] == true;
}

class Approval {
  final String executionState;
  final int id;
  final String kind;
  final String summary;
  final String createdAt;
  final String status;

  Approval.fromJson(Map<String, dynamic> j)
    : executionState = j['execution_state'] as String? ?? 'not_started',
      id = j['id'] as int? ?? 0,
      kind = j['kind'] as String? ?? '',
      summary = j['summary'] as String? ?? '',
      createdAt = j['created_at'] as String? ?? '',
      status = j['status'] as String? ?? 'pending';
}

class LifeState {
  final String mood;
  final int nextWakeAt;
  final int lastWakeAt;
  final bool paused;
  final List<dynamic> platformsOnline;

  LifeState.fromJson(Map<String, dynamic> j)
    : mood = j['mood'] as String? ?? '',
      nextWakeAt = (j['next_wake_at'] as num?)?.toInt() ?? 0,
      lastWakeAt = (j['last_wake_at'] as num?)?.toInt() ?? 0,
      paused = j['paused'] as bool? ?? false,
      platformsOnline = (j['platforms_online'] as List?) ?? const [];
}
