// models.dart — 鱼脑 API 返回数据的模型
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

  Memory.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int? ?? 0,
        kind = j['kind'] as String? ?? '',
        subject = j['subject'] as String? ?? '',
        content = j['content'] as String? ?? '',
        platform = j['platform'] as String? ?? '',
        createdAt = j['created_at'] as String? ?? '';
}

class Approval {
  final int id;
  final String kind;
  final String summary;
  final String createdAt;
  final String status;

  Approval.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int? ?? 0,
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
