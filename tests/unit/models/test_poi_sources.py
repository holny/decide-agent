"""poi_sources 单测：MCP client / 厂商归一化 / 坐标转换 / 降级链 / region 装配。

全部走注入 transport（httpx.MockTransport），零网络。
"""
import json

import httpx

from decide_agent.models.providers.poi_sources import (
    AmapSource,
    FallbackExecutor,
    GooglePlacesSource,
    OverpassSource,
    VendorPoiExecutor,
    bd09_to_gcj02,
    build_vendor_poi_executor,
    gcj02_to_wgs84,
    haversine_m,
    wgs84_to_gcj02,
)

ORIGIN = (31.8206, 117.2272)  # 合肥
INPUTS = {"scene": "food", "env": {"location": {"lat": ORIGIN[0], "lng": ORIGIN[1]}}}


# --------------------------------------------------------------------------- geo

def test_gcj02_wgs84_roundtrip_bounded():
    glng, glat = wgs84_to_gcj02(117.2272, 31.8206)
    assert 0.001 < abs(glng - 117.2272) < 0.02  # 偏移量级 ~百米级
    wlng, wlat = gcj02_to_wgs84(glng, glat)
    assert abs(wlng - 117.2272) < 1e-4 and abs(wlat - 31.8206) < 1e-4


def test_bd09_to_gcj02_small_shift():
    glng, _glat = bd09_to_gcj02(116.404, 39.915)
    assert 0.001 < abs(glng - 116.404) < 0.02


def test_haversine_known_distance():
    # 合肥→北京 ~900km 量级
    assert 850_000 < haversine_m(31.82, 117.23, 39.90, 116.40) < 960_000


# --------------------------------------------------------------------------- MCP client + AmapSource

def _sse(text: str) -> str:
    return f"data: {json.dumps(text)}\n\n" if False else "data: " + json.dumps(text) + "\n\n"


def test_amap_mcp_flow_normalizes_pois():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "result": {"protocolVersion": "2025-03-26"}},
                                  headers={"Mcp-Session-Id": "sid-1"})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        assert body["method"] == "tools/call"
        assert body["params"]["arguments"]["location"] == f"{ORIGIN[1]},{ORIGIN[0]}"
        poi_text = json.dumps({"pois": [
            {"id": "B001", "name": "蜀香居", "type": "美食:中餐厅:川菜",
             "location": "117.2320,31.8250", "distance": "850", "cost": "65"},
        ]})
        return httpx.Response(200, text="data: " + json.dumps({
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"content": [{"type": "text", "text": poi_text}]},
        }) + "\n\n")

    transport = httpx.MockTransport(handler)
    source = AmapSource({"key_env": "AMAP_KEY_TEST", "timeout_s": 5}, transport=transport)
    import os
    os.environ["AMAP_KEY_TEST"] = "k-test"
    try:
        candidates = source.search_poi(INPUTS, {"food": "美食"}, 5000)
    finally:
        os.environ.pop("AMAP_KEY_TEST", None)
    assert calls[:2] == ["initialize", "notifications/initialized"]
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand["name"] == "蜀香居" and cand["distance_m"] == 850
    assert cand["price_per_person"] == 65.0
    assert "川菜" in cand["tags"]
    assert cand["location"]["lat"] != 31.8250 or cand["location"]["lng"] != 117.2320  # 已转 WGS-84


# --------------------------------------------------------------------------- Google / Overpass

def test_google_places_normalizes_types_to_zh_tags():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Goog-Api-Key"] == "g-key"
        return httpx.Response(200, json={"places": [
            {"id": "p1", "displayName": {"text": "City Park"},
             "location": {"latitude": ORIGIN[0] + 0.01, "longitude": ORIGIN[1]},
             "rating": 4.5, "priceLevel": 2, "types": ["park", "tourist_attraction"]},
        ]})

    source = GooglePlacesSource({"key_env": "GOOGLE_KEY_TEST", "timeout_s": 5},
                                transport=httpx.MockTransport(handler))
    import os
    os.environ["GOOGLE_KEY_TEST"] = "g-key"
    try:
        candidates = source.search_poi({"scene": "travel", "env": {"location": {"lat": ORIGIN[0],
                                     "lng": ORIGIN[1]}}}, {}, 5000)
    finally:
        os.environ.pop("GOOGLE_KEY_TEST", None)
    assert candidates[0]["tags"] == ["公园", "景点"]
    assert candidates[0]["price_per_person"] == 60.0
    assert candidates[0]["distance_m"] > 0


def test_overpass_parses_elements():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"elements": [
            {"id": 1, "lat": ORIGIN[0] + 0.005, "lon": ORIGIN[1],
             "tags": {"name": "老王面馆", "amenity": "restaurant", "cuisine": "noodle"}},
        ]})

    source = OverpassSource({"timeout_s": 5}, transport=httpx.MockTransport(handler))
    candidates = source.search_poi(INPUTS, {}, 5000)
    assert candidates[0]["name"] == "老王面馆"
    assert candidates[0]["distance_m"] > 400


# --------------------------------------------------------------------------- 降级链与装配

class _Boom:
    vendor = "boom"

    def search_poi(self, inputs, scene_query, radius_m):
        raise RuntimeError("network down")


class _Ok:
    vendor = "ok"

    def search_poi(self, inputs, scene_query, radius_m):
        return [{"id": "x", "name": "兜底店", "tags": []}]


def test_vendor_executor_falls_through_sources():
    executor = VendorPoiExecutor([_Boom(), _Ok()], {"food": "美食"}, 5000)
    candidates = executor.execute("poi_search", INPUTS)
    assert candidates[0]["name"] == "兜底店"


def test_vendor_executor_rejects_other_capabilities():
    executor = VendorPoiExecutor([_Ok()], {}, 5000)
    try:
        executor.execute("weather", {})
        raised = False
    except NotImplementedError:
        raised = True
    assert raised


def test_fallback_executor_delegates_and_circuit_breaks():
    class _Mock:
        def execute(self, capability, inputs):
            return ["mock_candidate"]

    class _V:
        def execute(self, capability, inputs):
            raise RuntimeError("down")

    executor = FallbackExecutor([_V(), _Mock()])
    assert executor.execute("weather", {}) == ["mock_candidate"]  # V 不服务 weather → 委托 mock
    assert executor.execute("poi_search", {}) == ["mock_candidate"]  # V 失败 → 熔断 → mock
    assert executor.execute("poi_search", {}) == ["mock_candidate"]  # V 已熔断不再尝试


def test_build_vendor_poi_executor_region_and_key_gate(monkeypatch):
    cfg = {"region": "cn", "poi_search": {}, "vendor": {}}
    monkeypatch.delenv("AMAP_KEY", raising=False)
    monkeypatch.delenv("BAIDU_MAP_KEY", raising=False)
    assert build_vendor_poi_executor(cfg) is None  # 缺 key → 无厂商源 → 纯 mock 兜底
    monkeypatch.setenv("AMAP_KEY", "k")
    executor = build_vendor_poi_executor(cfg)
    assert executor is not None and len(executor._sources) == 1
    cfg_global = {"region": "global", "poi_search": {}, "vendor": {}}
    monkeypatch.setenv("FOURSQUARE_KEY", "f")
    executor = build_vendor_poi_executor(cfg_global)
    assert [s.vendor for s in executor._sources] == ["foursquare"]
    cfg_override = {"region": "cn", "poi_search": {"providers": ["osm"]}, "vendor": {}}
    executor = build_vendor_poi_executor(cfg_override)
    assert [s.vendor for s in executor._sources] == ["osm"]  # osm 无 key 也可用
