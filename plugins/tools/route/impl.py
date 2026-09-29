"""OSRM route: free demo server, driving profile, returns distance + duration."""
import httpx


def execute(inputs: dict) -> dict:
    from_lng = inputs.get("from_lng", 0)
    from_lat = inputs.get("from_lat", 0)
    to_lng = inputs.get("to_lng", 0)
    to_lat = inputs.get("to_lat", 0)
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{from_lng},{from_lat};{to_lng},{to_lat}"
        f"?overview=false"
    )
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    route = resp.json().get("routes", [{}])[0]
    return {
        "distance_m": round(route.get("distance", 0), 1),
        "duration_s": round(route.get("duration", 0), 1),
        "duration_min": round(route.get("duration", 0) / 60, 1),
    }
