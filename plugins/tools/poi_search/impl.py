"""Mock POI search: scene-aware deterministic candidates (food → restaurants, travel → attractions)."""
import random

MOCK_RESTAURANTS = [
    ("蜀香居", 0.85, ["川菜", "辣", "鸳鸯锅"], 45, 65, 4.6, 10),
    ("江边烤鱼", 0.80, ["烤鱼", "辣", "江景"], 55, 78, 4.7, 25),
    ("老王面馆", 0.70, ["面食", "清淡", "老字号"], 15, 28, 4.3, 5),
    ("粤味轩", 0.65, ["粤菜", "清淡", "精致"], 40, 90, 4.5, 15),
    ("辣妹子炒菜", 0.75, ["湘菜", "辣", "家常"], 20, 50, 4.4, 8),
    ("清淡居粥铺", 0.40, ["粥", "清淡", "养生"], 10, 25, 4.1, 3),
    ("川味 Corner", 0.82, ["川菜", "辣", "年轻"], 30, 55, 4.2, 12),
    ("素食主义", 0.30, ["素食", "清淡", "健康"], 25, 45, 4.0, 4),
]

# tags 对齐 travel/rules.jsonc interest tag_set（自然/人文/亲子 三组全覆盖）
# 字段：(name, dist_km, tags, wait_min, price, rating, crowd_index)
MOCK_ATTRACTIONS = [
    ("滨湖湿地公园", 0.80, ["山水", "湖", "自然", "公园"], 20, 35, 4.5, 55),
    ("植物园", 0.65, ["公园", "森林", "自然"], 10, 20, 4.3, 40),
    ("市博物馆", 0.75, ["博物馆", "人文", "古迹"], 15, 0, 4.6, 60),
    ("古城老街", 0.70, ["古镇", "人文", "古迹"], 30, 45, 4.2, 80),
    ("科技馆", 0.60, ["科技馆", "亲子", "人文"], 25, 30, 4.4, 65),
    ("野生动物园", 0.90, ["动物园", "亲子", "自然"], 35, 90, 4.7, 75),
]


def execute(inputs: dict) -> list[dict]:
    scene = str((inputs or {}).get("scene") or "")
    rng = random.Random(42)
    if scene == "travel":
        candidates = []
        for i, (name, dist_km, tags, wait, price, rating, crowd) in enumerate(MOCK_ATTRACTIONS):
            candidates.append({
                "id": f"poi_{i:03d}",
                "name": name,
                "category": "attraction",
                "distance_m": int(dist_km * 1000) + rng.randint(0, 99),
                "price_per_person": float(price),
                "rating": rating,
                "wait_min": wait,
                "crowd_index": crowd + rng.randint(0, 4),
                "tags": tags,
            })
        return candidates
    candidates = []
    for i, (name, dist_km, tags, wait, price, rating, _q) in enumerate(MOCK_RESTAURANTS):
        candidates.append({
            "id": f"poi_{i:03d}",
            "name": name,
            "category": "restaurant",
            "distance_m": int(dist_km * 1000) + rng.randint(0, 99),
            "price_per_person": float(price),
            "rating": rating,
            "wait_min": wait,
            "tags": tags,
        })
    return candidates
