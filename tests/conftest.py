"""共享测试夹具。

封闭性约定：单测/集成/e2e 一律钉在内置 mock 工具数据上——真实厂商源（高德/百度等）
靠 .env key 激活，会让测试外呼公网（慢 + QPS 抖动 + 断言不可复现）。
需要真实源的用例请显式覆盖本夹具或在 tests/integration 单独标注 live。
"""
import pytest


@pytest.fixture(autouse=True)
def _force_mock_poi(monkeypatch):
    """禁用真实 POI 厂商源 → 恒走内置 mock（providers=[] 显式禁用语义）。"""
    monkeypatch.setenv("DECIDE_AGENT__TOOLS__POI_SEARCH__PROVIDERS", "[]")


@pytest.fixture(autouse=True)
def _deterministic_geo_weather(monkeypatch):
    """定位/天气插件直连公网免费源（ip-api / Open-Meteo）——沙箱限流即抖。

    stub httpx.get：这两个域返回确定性数据，其余 URL 透传真实 httpx.get。
    """
    import httpx

    real_get = httpx.get

    def fake_get(url, *args, **kwargs):
        url_text = str(url)
        if "ip-api.com" in url_text:
            return httpx.Response(200, json={
                "status": "success", "lat": 31.8206, "lon": 117.2272,
                "city": "Hefei", "regionName": "Anhui",
            }, request=httpx.Request("GET", str(url)))
        if "api.open-meteo.com" in url_text:
            return httpx.Response(200, json={
                "current_weather": {"temperature": 20.0, "windspeed": 8.0, "weathercode": 61},
            }, request=httpx.Request("GET", str(url)))
        return real_get(url, *args, **kwargs)

    monkeypatch.setattr(httpx, "get", fake_get)
