"""Location tool: IP geolocation, multi-source fallback (L1).

源顺序：ip-api(http, 免费) → freeipapi(https, 免费)——前者被网络策略挡/限流时走后者。
调用方已提供坐标（inputs.env.location）→ 直接透传不请求（省外网调用，VPN 出口亦不漂移）。
全部源失败 → raise → scheduler 捕获 → ask_once 追问用户（此时 kernel 会附上次位置选项）。
"""
import httpx


def _from_ipapi(data: dict) -> dict | None:
    if data.get("status") != "success":
        return None
    return {
        "lat": float(data["lat"]),
        "lng": float(data["lon"]),
        "city": data.get("city", ""),
        "district": data.get("regionName", ""),
        "accuracy": "ip",
    }


def _from_freeipapi(data: dict) -> dict | None:
    if data.get("latitude") is None:
        return None
    return {
        "lat": float(data["latitude"]),
        "lng": float(data["longitude"]),
        "city": data.get("cityName", ""),
        "district": data.get("regionName", ""),
        "accuracy": "ip",
    }


_SOURCES = [
    ("http://ip-api.com/json/", _from_ipapi),
    ("https://freeipapi.com/api/json", _from_freeipapi),
]


def execute(inputs: dict) -> dict:
    existing = ((inputs or {}).get("env") or {}).get("location")
    if isinstance(existing, dict) and existing.get("lat") is not None:
        return existing
    errors: list[str] = []
    for url, parser in _SOURCES:
        try:
            resp = httpx.get(url, timeout=5)
            resp.raise_for_status()
            parsed = parser(resp.json())
        except Exception as exc:  # noqa: BLE001 — 单源失败换下一源（L4）
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        if parsed is not None:
            return parsed
        errors.append(f"{url}: unparseable response")
    raise RuntimeError("all geo sources failed: " + "; ".join(errors))
