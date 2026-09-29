"""Open-Meteo weather: free, no key, global coverage, WMO weather codes."""
import httpx

_WMO = {0: "晴", 1: "多云", 2: "阴", 3: "阴", 45: "雾", 48: "雾凇",
        51: "毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
        71: "小雪", 73: "中雪", 75: "大雪", 80: "阵雨", 95: "雷暴"}


def execute(inputs: dict) -> dict:
    lat = inputs.get("lat", 31.19)
    lng = inputs.get("lng", 121.44)
    resp = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lng, "current_weather": "true"},
        timeout=5,
    )
    resp.raise_for_status()
    cw = resp.json().get("current_weather", {})
    code = cw.get("weathercode", 0)
    return {
        "temperature": cw.get("temperature", 0.0),
        "windspeed": cw.get("windspeed", 0.0),
        "weathercode": code,
        "condition": _WMO.get(code, "未知"),
        "is_rainy": code in (51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99),
    }
