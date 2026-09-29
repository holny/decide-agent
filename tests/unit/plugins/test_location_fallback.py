"""Location 多源回退：ip-api 失败 → freeipapi(https)。零真实网络（monkeypatch httpx.get）。"""
import importlib.util
from pathlib import Path

import httpx
import pytest

IMPL = Path(__file__).resolve().parents[3] / "plugins" / "tools" / "location" / "impl.py"
_spec = importlib.util.spec_from_file_location("location_impl", IMPL)
location = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(location)


def _patch_get(monkeypatch, responses: list[tuple[int, dict]]):
    idx = {"i": 0}

    def fake_get(url, *args, **kwargs):
        i = idx["i"]
        idx["i"] += 1
        status, payload = responses[min(i, len(responses) - 1)]
        return httpx.Response(status, json=payload,
                              request=httpx.Request("GET", str(url)))

    monkeypatch.setattr(httpx, "get", fake_get)


def test_first_source_success(monkeypatch):
    _patch_get(monkeypatch, [(200, {"status": "success", "lat": 31.82, "lon": 117.23,
        "city": "Hefei", "regionName": "Anhui"})])
    out = location.execute({})
    assert out["city"] == "Hefei" and out["lat"] == 31.82 and out["accuracy"] == "ip"


def test_fallback_to_freeipapi_when_ipapi_rate_limited(monkeypatch):
    _patch_get(monkeypatch, [
        (429, {"message": "rate limited"}),
        (200, {"latitude": 31.82, "longitude": 117.23,
               "cityName": "Hefei", "regionName": "Anhui"}),
    ])
    out = location.execute({})
    assert out["city"] == "Hefei" and out["lat"] == 31.82


def test_all_sources_failed_raises(monkeypatch):
    _patch_get(monkeypatch, [(429, {})])
    with pytest.raises(RuntimeError, match="all geo sources failed"):
        location.execute({})


def test_existing_location_short_circuits(monkeypatch):
    called = {"n": 0}

    def fake_get(url, *args, **kwargs):
        called["n"] += 1
        return httpx.Response(200, json={"status": "success", "lat": 9, "lon": 9})

    monkeypatch.setattr(httpx, "get", fake_get)
    out = location.execute({"env": {"location": {"lat": 1.0, "lng": 2.0}}})
    assert out == {"lat": 1.0, "lng": 2.0}
    assert called["n"] == 0  # 直供坐标不外呼
