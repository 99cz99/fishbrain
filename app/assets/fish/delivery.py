class SendUnconfirmedError(RuntimeError):
    """发送可能已执行，但未取得平台确认；调用方不得自动重试。"""


class SendCancelledError(RuntimeError):
    """回复过时或控制条件改变，停止尚未提交的段落。"""
