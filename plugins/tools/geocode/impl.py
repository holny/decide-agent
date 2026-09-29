"""Nominatim geocoding: OpenStreetMap, free, no key. Address → coordinates."""
import httpx

_HEADERS = {"User-Agent": "decide-agent/0.2"}


def execute(inputs: dict) -> dict:
    address = inputs.get("address", "")
    if not address:
        raise RuntimeError("address required")
    resp = httpx.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1},
        headers=_HEADERS, timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise RuntimeError(f"no result for: {address}")
    r = results[0]
    return {
        "lat": float(r["lat"]),
        "lng": float(r["lon"]),
        "display_name": r.get("display_name", ""),
    }
