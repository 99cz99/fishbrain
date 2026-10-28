class SendUnconfirmedError(RuntimeError):
    """发送可能已执行，但未取得平台确认；调用方不得自动重试。"""
