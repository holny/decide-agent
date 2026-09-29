"""POI 数据源适配层：capability 契约（poi_search{scene,location} → candidates[]）的厂商实现。

MCP 优先，但以「官方 remote 端点存在」为前提：高德走官方 remote MCP（Streamable HTTP）；
百度官方 MCP 是 stdio 本地包（无 remote）→ REST；Google 官方 MCP 为本地 stdio 组件 → REST；
Foursquare remote MCP 需 OAuth → REST；OSM 无官方 MCP → Overpass 裸 API。
厂商名只出现在本模块与 config（红线 3）。密钥经 env 引用，永不落盘；
任一源失败 → FallbackExecutor 换下一源 → 最终兜底本地 mock。
"""
import json
import logging
import math
import os
import re
from typing import ClassVar

import httpx

_log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- geo

_PI = math.pi
_A = 6378245.0
_EE = 0.00669342162296594323


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * _PI) + 320 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * _PI
    magic = 1 - _EE * math.sin(radlat) ** 2
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * _PI)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * _PI)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    glng, glat = wgs84_to_gcj02(lng, lat)
    return lng * 2 - glng, lat * 2 - glat  # 近似逆（精度 ~1m，足够距离计算）


def bd09_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    x = lng - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _PI * 3000.0 / 180.0)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _PI * 3000.0 / 180.0)
    return z * math.cos(theta), z * math.sin(theta)


def gcj02_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * _PI * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * _PI * 3000.0 / 180.0)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def wgs84_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    return gcj02_to_bd09(*wgs84_to_gcj02(lng, lat))


def bd09_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    return gcj02_to_wgs84(*bd09_to_gcj02(lng, lat))


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * 6371000 * math.asin(math.sqrt(a)))


# --------------------------------------------------------------------------- MCP client


def _parse_mcp_body(text: str):
    """Streamable HTTP 响应体：JSON 或 SSE 帧（data: {...}），取最后一个可解析帧。"""
    text = text.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    for line in reversed(text.splitlines()):
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
    return None


class McpClient:
    """最小同步 MCP client（Streamable HTTP）：initialize → tools/call。零外部依赖。"""

    def __init__(self, url: str, timeout_s: float = 12.0, transport: httpx.BaseTransport | None = None):
        self._url = url
        self._timeout_s = timeout_s
        self._transport = transport
        self._session_id: str | None = None
        self._initialized = False
        self._next_id = 0

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict):
        with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
            resp = client.post(self._url, json=payload, headers=self._headers())
            resp.raise_for_status()
            session_id = resp.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id
            return _parse_mcp_body(resp.text)

    def _rpc(self, method: str, params: dict | None = None, *, notify: bool = False):
        message: dict = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self._next_id += 1
            message["id"] = self._next_id
        body = self._post(message)
        if notify:
            return None
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(f"mcp error: {body['error']}")
        return (body or {}).get("result") if isinstance(body, dict) else None

    def initialize(self) -> dict:
        result = self._rpc("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "decide-agent", "version": "0.1.0"},
        })
        self._rpc("notifications/initialized", notify=True)
        self._initialized = True
        return result or {}

    def _ensure_session(self) -> None:
        if self._initialized:
            return
        try:
            self.initialize()
        except Exception:
            self._initialized = False
            raise

    def call(self, name: str, arguments: dict | None = None):
        self._ensure_session()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            raise TypeError(f"mcp tools/call returned no result: {result!r}")
        if result.get("isError"):
            raise RuntimeError(f"mcp tool error: {str(result.get('content'))[:300]}")
        structured = result.get("structuredContent")
        if isinstance(structured, (list, dict)):
            return structured
        texts = [part.get("text", "") for part in result.get("content", [])
                 if isinstance(part, dict) and part.get("type") == "text"]
        raw = "\n".join(texts).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def probe(self) -> bool:
        try:
            self.initialize()
            return True
        except Exception:  # noqa: BLE001 — probe 探活，任何失败=不可用
            return False


# --------------------------------------------------------------------------- 共用归一化


def _first_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("pois", "results", "places", "sights", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                inner = _first_list(value)
                if inner:
                    return inner
    return []


def _origin(inputs: dict) -> tuple[float, float]:
    """用户坐标 (lat, lng)：env.location（location 插件产出）。缺失 → raise → 换下一源。"""
    location = ((inputs.get("env") or {}).get("location")) or (inputs.get("location") or {})
    lat, lng = location.get("lat"), location.get("lng")
    if lat is None or lng is None:
        raise ValueError("no origin coordinates for poi search")
    return float(lat), float(lng)


def _keyword(inputs: dict, scene_query: dict) -> str:
    return str(scene_query.get(inputs.get("scene") or "", "美食"))


def _radius(inputs: dict, default_m: int) -> int:
    return int(inputs.get("radius_m") or default_m)


def _split_tags(raw: str) -> list[str]:
    return [t.strip() for t in re.split(r"[:：、;,/|]", raw or "") if len(t.strip()) >= 2]


def _candidate(cid: str, name: str, category: str, dist: int | None, tags: list[str], *,
               price=None, rating=None, wait=None, crowd=None, location=None) -> dict:
    cand: dict = {"id": cid, "name": name, "category": category, "tags": tags}
    if dist is not None:
        cand["distance_m"] = max(1, int(dist))
    if price is not None:
        cand["price_per_person"] = float(price)
    if rating is not None:
        cand["rating"] = max(0.0, min(5.0, float(rating)))
    if wait is not None:
        cand["wait_min"] = int(wait)
    if crowd is not None:
        cand["crowd_index"] = float(crowd)
    if location is not None:
        cand["location"] = location
    return cand


_GOOGLE_TAG_ZH = {
    "park": "公园", "museum": "博物馆", "zoo": "动物园", "amusement_park": "乐园",
    "tourist_attraction": "景点", "forest": "森林", "temple": "寺庙", "aquarium": "水族馆",
    "restaurant": "餐厅", "cafe": "咖啡", "bar": "酒吧", "food": "美食",
}
_FSQ_TAG_ZH = {
    "Park": "公园", "Museums": "博物馆", "Zoos": "动物园", "Theme Parks": "乐园",
    "Temple": "寺庙", "Restaurant": "餐厅", "Food": "美食", "Coffee Shop": "咖啡",
}
_GOOGLE_PRICE = {0: 0.0, 1: 30.0, 2: 60.0, 3: 120.0, 4: 250.0}


# --------------------------------------------------------------------------- 数据源


class PoiSource:
    """厂商源最小协议：search_poi(inputs, scene_query, radius_m) → list[candidate dict]。

    key 一律调用时读 env（懒读取）：boot 后配置 env 即生效，无需重启。
    """
    vendor = ""
    DEFAULT_KEY_ENV = ""

    def __init__(self, cfg: dict, transport: httpx.BaseTransport | None = None):
        self._cfg = cfg
        self._transport = transport
        self._key_env = cfg.get("key_env") or self.DEFAULT_KEY_ENV

    @classmethod
    def available(cls, cfg: dict) -> bool:
        return bool(os.environ.get(cfg.get("key_env") or cls.DEFAULT_KEY_ENV or ""))

    def _key(self) -> str:
        return os.environ.get(self._key_env, "")

    def search_poi(self, inputs: dict, scene_query: dict, radius_m: int) -> list[dict]:  # pragma: no cover
        raise NotImplementedError


class AmapSource(PoiSource):
    """高德官方 MCP（Streamable HTTP，key 挂 query）：maps_around_search。GCJ-02 → WGS-84。"""

    vendor = "amap"
    DEFAULT_KEY_ENV = "AMAP_KEY"

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self._timeout_s = float(cfg.get("timeout_s", 12.0))
        self._base_url = cfg.get("mcp_url", "https://mcp.amap.com/mcp")
        self._tool = (cfg.get("tool_names") or {}).get("poi_search", "maps_around_search")
        self._detail_tool = (cfg.get("tool_names") or {}).get("poi_detail", "maps_search_detail")
        self._mcp: McpClient | None = None

    def _client(self) -> McpClient:
        if self._mcp is None:
            key = self._key()
            url = self._base_url + (f"?key={key}" if key else "")
            self._mcp = McpClient(url, timeout_s=self._timeout_s, transport=self._transport)
        return self._mcp

    def search_poi(self, inputs: dict, scene_query: dict, radius_m: int) -> list[dict]:
        lat, lng = _origin(inputs)
        mcp = self._client()
        scene = str(inputs.get("scene") or "food")
        data = mcp.call(self._tool, {
            "keywords": _keyword(inputs, scene_query),
            "location": f"{lng},{lat}", "radius": str(radius_m),
        })
        # MCP around_search 返回精简字段（id/name/address/typecode）→ detail 补全
        # （location/distance/cost/rating/type），封顶 8 个候选控延迟（N+1 调用）。
        skeleton = [p for p in _first_list(data) if str(p.get("name") or "").strip()][:8]
        out = []
        for poi in skeleton:
            name = str(poi.get("name") or "").strip()
            detail = poi
            pid = poi.get("id")
            if pid:
                try:
                    full = mcp.call(self._detail_tool, {"id": str(pid)})
                    full_list = full if isinstance(full, list) else _first_list(full)
                    if not full_list and isinstance(full, dict) and full.get("id"):
                        full_list = [full]  # detail 为裸 dict（无列表包装键）
                    if full_list:
                        detail = full_list[0]
                except Exception as exc:  # noqa: BLE001 — detail 失败降级用 skeleton 字段
                    _log.warning("amap detail %s skipped: %s", pid, exc)
            raw_ll = str(detail.get("location") or "")
            location = None
            parts = raw_ll.split(",") if raw_ll else []
            if len(parts) == 2:
                try:
                    wlng, wlat = gcj02_to_wgs84(float(parts[0]), float(parts[1]))
                    location = {"lat": round(wlat, 6), "lng": round(wlng, 6)}
                except ValueError:
                    location = None
            dist = detail.get("distance")
            if dist in (None, "") and location:
                dist = haversine_m(lat, lng, location["lat"], location["lng"])
            cost = detail.get("cost")
            rating = detail.get("rating")
            out.append(_candidate(
                f"amap_{detail.get('id', name)}", name,
                "restaurant" if scene == "food" else "attraction",
                dist, _split_tags(str(detail.get("type") or "")),
                price=cost, rating=rating, location=location,
            ))
        return out


class BaiduSource(PoiSource):
    """百度地图 Place API v2（官方 MCP 为 stdio 本地包，无 remote 端点 → 服务端用 REST）。

    入参坐标需 BD-09：WGS-84 → BD-09；返回 POI 坐标 BD-09 → WGS-84。
    """

    vendor = "baidu"
    DEFAULT_KEY_ENV = "BAIDU_MAP_KEY"

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self._url = cfg.get("places_url", "https://api.map.baidu.com/place/v2/search")
        self._timeout_s = float(cfg.get("timeout_s", 10.0))

    def search_poi(self, inputs: dict, scene_query: dict, radius_m: int) -> list[dict]:
        lat, lng = _origin(inputs)
        blng, blat = wgs84_to_bd09(lng, lat)  # wgs84_to_bd09 返回 (lng, lat)
        with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
            resp = client.get(self._url, params={
                "query": _keyword(inputs, scene_query),
                "location": f"{blat},{blng}", "radius": radius_m,
                "output": "json", "ak": self._key(),
            })
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, dict) and data.get("status") not in (0, "0"):
            raise RuntimeError(f"baidu place error: status={data.get('status')} {data.get('message')}")
        scene = str(inputs.get("scene") or "food")
        out = []
        for poi in _first_list(data):
            name = str(poi.get("name") or "").strip()
            if not name:
                continue
            detail = poi.get("detail_info") or {}
            dist = poi.get("distance") or detail.get("distance")
            loc = poi.get("location") or {}
            location = None
            if loc.get("lat") is not None:
                wlng, wlat = bd09_to_wgs84(float(loc["lng"]), float(loc["lat"]))
                location = {"lat": round(wlat, 6), "lng": round(wlng, 6)}
            if dist in (None, "") and location:
                dist = haversine_m(lat, lng, location["lat"], location["lng"])
            out.append(_candidate(
                f"baidu_{poi.get('uid', name)}", name,
                "restaurant" if scene == "food" else "attraction",
                dist, _split_tags(str(detail.get("tag") or "")),
                price=detail.get("price"), rating=detail.get("rating"),
                location=location,
            ))
        return out


class GooglePlacesSource(PoiSource):
    """Google Places API (New) searchNearby（无官方 remote MCP；本地 stdio MCP 不适合服务端部署）。"""

    vendor = "google"
    DEFAULT_KEY_ENV = "GOOGLE_MAPS_KEY"

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self._url = cfg.get("places_url", "https://places.googleapis.com/v1/places:searchNearby")

    _TYPES: ClassVar[dict] = {"food": ["restaurant", "cafe"],
        "travel": ["park", "museum", "zoo", "amusement_park", "tourist_attraction"]}

    def search_poi(self, inputs: dict, scene_query: dict, radius_m: int) -> list[dict]:
        lat, lng = _origin(inputs)
        scene = str(inputs.get("scene") or "food")
        body = {
            "includedTypes": self._TYPES.get(scene, ["restaurant"]),
            "maxResultCount": 8,
            "locationRestriction": {"circle": {
                "center": {"latitude": lat, "longitude": lng}, "radius": min(radius_m, 50000)}},
        }
        with httpx.Client(timeout=float(self._cfg.get("timeout_s", 10.0)), transport=self._transport) as client:
            resp = client.post(self._url, json=body, headers={
                "X-Goog-Api-Key": self._key(),
                "X-Goog-FieldMask": "places.id,places.displayName,places.location,places.rating,"
                                    "places.priceLevel,places.types",
            })
            resp.raise_for_status()
            data = resp.json()
        out = []
        for place in _first_list(data):
            name = str(((place.get("displayName") or {}).get("text")) or "").strip()
            if not name:
                continue
            loc = place.get("location") or {}
            dist = haversine_m(lat, lng, loc.get("latitude", lat), loc.get("longitude", lng)) if loc else None
            tags = [_GOOGLE_TAG_ZH[t] for t in place.get("types", []) if t in _GOOGLE_TAG_ZH]
            out.append(_candidate(
                f"google_{place.get('id', name)}", name,
                "restaurant" if scene == "food" else "attraction",
                dist, tags,
                price=_GOOGLE_PRICE.get(place.get("priceLevel")),
                rating=place.get("rating"), location=loc or None,
            ))
        return out


class FoursquareSource(PoiSource):
    """Foursquare Places API v3（remote MCP 需 OAuth，裸 API 更稳）。

    认证双形态自适应：service API key 直挂 Authorization；OAuth access token 用 Bearer。
    """

    vendor = "foursquare"
    DEFAULT_KEY_ENV = "FOURSQUARE_KEY"
    _QUERY: ClassVar[dict] = {"food": "restaurant", "travel": "attractions"}

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self._url = cfg.get("places_url", "https://api.foursquare.com/v3/places/search")

    def search_poi(self, inputs: dict, scene_query: dict, radius_m: int) -> list[dict]:
        lat, lng = _origin(inputs)
        scene = str(inputs.get("scene") or "food")
        key = self._key()
        headers = {"Accept": "application/json"}
        if key.lower().startswith(("fsq", "twa")) or len(key) >= 40:
            headers["Authorization"] = f"Bearer {key}"  # OAuth access token 形态
        else:
            headers["Authorization"] = key  # service API key 形态
        with httpx.Client(timeout=float(self._cfg.get("timeout_s", 10.0)), transport=self._transport) as client:
            resp = client.get(self._url, params={
                "ll": f"{lat},{lng}", "radius": min(radius_m, 100000),
                "query": self._QUERY.get(scene, "restaurant"), "limit": 8,
            }, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        out = []
        for place in _first_list(data):
            name = str(place.get("name") or "").strip()
            if not name:
                continue
            geocodes = (place.get("geocodes") or {}).get("main") or {}
            dist = place.get("distance")
            if dist in (None, "") and geocodes:
                dist = haversine_m(lat, lng, geocodes["latitude"], geocodes["longitude"])
            tags = [_FSQ_TAG_ZH.get(c.get("name"), c.get("name"))
                    for c in place.get("categories", []) if c.get("name")]
            out.append(_candidate(
                f"fsq_{place.get('fsq_id', name)}", name,
                "restaurant" if scene == "food" else "attraction",
                dist, tags, location=geocodes or None,
            ))
        return out


class OverpassSource(PoiSource):
    """OSM Overpass（免费无 key；海外兜底）。WGS-84 原生。"""

    vendor = "osm"

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self._url = cfg.get("overpass_url", "https://overpass-api.de/api/interpreter")

    @classmethod
    def available(cls, cfg: dict) -> bool:
        return True  # Overpass 免费，无 key

    _FILTERS: ClassVar[dict] = {
        "food": '["amenity"~"^(restaurant|cafe|fast_food)$"]',
        "travel": '["tourism"~"^(attraction|zoo|museum|theme_park|viewpoint)$"]',
    }

    def search_poi(self, inputs: dict, scene_query: dict, radius_m: int) -> list[dict]:
        lat, lng = _origin(inputs)
        scene = str(inputs.get("scene") or "food")
        flt = self._FILTERS.get(scene, self._FILTERS["food"])
        query = f"[out:json][timeout:10];(node(around:{radius_m},{lat},{lng}){flt};);out 8;"
        with httpx.Client(timeout=float(self._cfg.get("timeout_s", 15.0)), transport=self._transport) as client:
            resp = client.post(self._url, data=query, headers={"Content-Type": "text/plain"})
            resp.raise_for_status()
            data = resp.json()
        out = []
        for node in (data or {}).get("elements", []):
            tags = node.get("tags") or {}
            name = str(tags.get("name") or "").strip()
            if not name:
                continue
            dist = haversine_m(lat, lng, node["lat"], node["lon"])
            cn_tags = [v for k, v in tags.items() if k in ("cuisine", "leisure", "tourism") and len(v) >= 2]
            out.append(_candidate(
                f"osm_{node.get('id', name)}", name,
                "restaurant" if scene == "food" else "attraction",
                dist, _split_tags(";".join(cn_tags)), location={"lat": node["lat"], "lng": node["lon"]},
            ))
        return out


# 厂商注册表 + region 默认链（厂商名仅存本模块与 config，红线 3）
# 默认链只含有 key 门槛的源：零 key = 无厂商源 = 纯 mock 兜底（内网/离线零网络调用）。
# OSM 免 key 但属公网——仅显式配置 providers:["osm",...] 时启用。
VENDOR_SOURCES: dict[str, type] = {
    "amap": AmapSource, "baidu": BaiduSource,
    "google": GooglePlacesSource, "foursquare": FoursquareSource, "osm": OverpassSource,
}
REGION_PROVIDERS = {
    "cn": ["amap", "baidu"],
    "global": ["google", "foursquare"],
}


class VendorPoiExecutor:
    """ToolExecutor：只服务 poi_search，按配置顺序尝试厂商源；其它能力 → NotImplementedError。"""

    def __init__(self, sources: list[PoiSource], scene_query: dict, radius_m: int) -> None:
        self._sources = sources
        self._scene_query = scene_query
        self._radius_m = radius_m

    @staticmethod
    def _key_of(source) -> str:
        return getattr(source, "_key", lambda: "")() or ""

    def execute(self, capability: str, inputs: dict):
        if capability != "poi_search":
            raise NotImplementedError("vendor executor serves poi_search only")
        errors: list[str] = []
        for source in self._sources:
            try:
                candidates = source.search_poi(inputs, self._scene_query, self._radius_m)
            except Exception as exc:  # noqa: BLE001 — 单源失败换下一源（L4）
                errors.append(f"{source.vendor}: {type(exc).__name__}: {exc}")
                continue
            if candidates:
                return candidates
            errors.append(f"{source.vendor}: empty result")
        raise RuntimeError("all poi sources failed: " + "; ".join(errors))


class FallbackExecutor:
    """按序尝试多个 executor；失败熔断该层（进程内），全部失败抛最后异常（scheduler 降级）。"""

    def __init__(self, executors: list) -> None:
        self._executors = executors
        self._disabled: dict[str, str] = {}

    def execute(self, capability: str, inputs: dict):
        last: Exception | None = None
        for executor in self._executors:
            name = type(executor).__name__
            if name in self._disabled:
                continue
            try:
                return executor.execute(capability, inputs)
            except NotImplementedError:
                continue  # 该层不服务此能力 → 换下一层（不熔断）
            except Exception as exc:  # noqa: BLE001 — 熔断降级（L4）
                self._disabled[name] = f"{type(exc).__name__}: {exc}"
                last = exc
        raise last or KeyError(f"no executor for '{capability}'")


def build_vendor_poi_executor(tools_cfg: dict, transport=None) -> VendorPoiExecutor | None:
    """装配：region 默认链 → 用户 providers 覆盖 → 无可用源（缺 key）返回 None（纯 mock 兜底）。"""
    poi_cfg = tools_cfg.get("poi_search") or {}
    vendor_cfg = tools_cfg.get("vendor") or {}
    providers = poi_cfg.get("providers")
    if providers is None:  # None = 按 region 默认；[] = 显式禁用厂商源（测试/纯 mock）
        providers = REGION_PROVIDERS.get(str(tools_cfg.get("region", "cn")), [])
    sources = []
    for name in providers:
        cls = VENDOR_SOURCES.get(name)
        cfg = vendor_cfg.get(name) or {}
        if cls is None or not cls.available(cfg):
            continue  # 缺 key / 未知厂商 → 跳过该源（L4）
        sources.append(cls(cfg, transport=transport))
    if not sources:
        return None
    return VendorPoiExecutor(
        sources, dict(poi_cfg.get("scene_query") or {}), int(poi_cfg.get("radius_m", 5000)),
    )
