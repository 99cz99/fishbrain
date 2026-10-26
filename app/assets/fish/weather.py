# weather.py — 天气感知：Open-Meteo（免费、无需 key、中文城市名直查）
# 两步：城市名 → 经纬度（geocoding，缓存 24h）→ 当前天气（forecast，缓存 30min）。
# 天气码用 WMO 标准映射成中文。任何一步失败返回 None（调用方静默省略，不崩不刷屏）。
import json
import time
import urllib.parse
import urllib.request

_UA = {"User-Agent": "fishbrain/1.0"}
_GEO_TTL = 24 * 3600        # 城市坐标缓存 24 小时
_WX_TTL = 1800              # 天气缓存 30 分钟
_TIMEOUT = 5                # 秒

_geo_cache = {}    # {city: {"ts": float, "lat": float, "lon": float}}
_wx_cache = {}     # {(lat, lon): {"ts": float, "text": str}}

# WMO 天气码 → 中文（Open-Meteo current.weather_code）
_WMO = {
    0: "晴", 1: "大致晴朗", 2: "多云", 3: "阴",
    4: "霾", 5: "烟",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "阵雪",
    95: "雷雨", 96: "雷雨伴小冰雹", 99: "雷雨伴大冰雹",
}


def _get_json(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _geocode(city):
    now = time.time()
    hit = _geo_cache.get(city)
    if hit and now - hit["ts"] < _GEO_TTL:
        return hit["lat"], hit["lon"]
    url = ("https://geocoding-api.open-meteo.com/v1/search?"
           + urllib.parse.urlencode({"name": city, "count": 1, "language": "zh"}))
    data = _get_json(url)
    results = data.get("results") or []
    if not results:
        return None, None
    lat = float(results[0]["latitude"])
    lon = float(results[0]["longitude"])
    _geo_cache[city] = {"ts": now, "lat": lat, "lon": lon}
    return lat, lon


def get_weather(city):
    """返回一句话中文天气，如「多云 16°C（体感 13°C）」；拿不到返回 None。"""
    city = (city or "").strip()
    if not city:
        return None
    try:
        lat, lon = _geocode(city)
        if lat is None:
            return None
        key = (round(lat, 2), round(lon, 2))
        now = time.time()
        hit = _wx_cache.get(key)
        if hit and now - hit["ts"] < _WX_TTL:
            return hit["text"]
        url = ("https://api.open-meteo.com/v1/forecast?"
               + urllib.parse.urlencode({
                   "latitude": lat, "longitude": lon,
                   "current": "temperature_2m,apparent_temperature,weather_code",
                   "timezone": "auto",
               }))
        data = _get_json(url)
        cur = data.get("current") or {}
        code = cur.get("weather_code")
        temp = cur.get("temperature_2m")
        feels = cur.get("apparent_temperature")
        if code is None and temp is None:
            return None
        parts = []
        desc = _WMO.get(int(code), "") if code is not None else ""
        if desc:
            parts.append(desc)
        if temp is not None:
            parts.append(f"{round(temp)}°C")
        if not parts:
            return None
        text = " ".join(parts)
        if feels is not None and temp is not None and round(feels) != round(temp):
            text += f"（体感 {round(feels)}°C）"
        _wx_cache[key] = {"ts": now, "text": text}
        return text
    except Exception:
        return None
