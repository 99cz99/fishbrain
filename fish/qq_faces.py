"""Shared QQ face names and the text notation used in replies and history."""

FACE_IDS = {
    "惊讶": 0, "得意": 4, "流泪": 5, "睡": 8, "大哭": 9, "发怒": 11, "调皮": 12,
    "呲牙": 13, "微笑": 14, "难过": 15, "酷": 16, "抓狂": 18, "偷笑": 20, "可爱": 21,
    "白眼": 22, "困": 25, "憨笑": 28, "疑问": 32, "晕": 34, "擦汗": 40, "鼓掌": 99,
    "坏笑": 101, "委屈": 106, "亲亲": 52, "玫瑰": 63, "爱心": 66, "心碎": 67, "赞": 76,
    "胜利": 79, "抱拳": 118, "泪奔": 173, "斜眼笑": 178, "笑哭": 182,
}
FACE_NAMES = {str(fid): name for name, fid in FACE_IDS.items()}


def face_id(token):
    """Accept names and legacy numeric history tags; the platform validates IDs."""
    token = token.strip()
    if token in FACE_IDS:
        return FACE_IDS[token]
    if token.isascii() and token.isdecimal():
        try:
            return int(token)
        except ValueError:
            pass
    return None


def face_tag(fid):
    fid = str(fid)
    return f"[表情:{FACE_NAMES.get(fid, fid)}]"
