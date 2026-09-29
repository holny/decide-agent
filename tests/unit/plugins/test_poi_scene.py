"""poi_search 场景感知 mock：travel→景点 / food→餐厅 / 缺省→餐厅（向后兼容）。"""
import importlib.util
from pathlib import Path

IMPL = Path(__file__).resolve().parents[3] / "plugins" / "tools" / "poi_search" / "impl.py"
_spec = importlib.util.spec_from_file_location("poi_impl", IMPL)
poi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(poi)


def test_travel_scene_returns_attractions():
    out = poi.execute({"scene": "travel"})
    assert out and all(c["category"] == "attraction" for c in out)
    assert all("crowd_index" in c for c in out)
    tags = {t for c in out for t in c["tags"]}
    assert {"山水", "博物馆", "乐园"} <= tags or {"山水", "博物馆", "动物园"} <= tags  # 自然/人文/亲子 全覆盖


def test_food_scene_returns_restaurants():
    out = poi.execute({"scene": "food"})
    assert out and all(c["category"] == "restaurant" for c in out)


def test_missing_scene_backwards_compatible():
    assert poi.execute({})[0]["category"] == "restaurant"
    assert poi.execute({})[0]["name"] == "蜀香居"
