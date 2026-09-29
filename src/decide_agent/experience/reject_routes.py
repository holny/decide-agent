"""reject_routes：显式拒绝路径（P8，Ontology 实践落地）。

"哪些路径看似合理但不能走"是场景知识——数据化进模板（L1），不散落代码。
两级结构：
  exact_reject   澄清应答整值拒绝（"不知道/随便"不是候选）
  segment_reject 候选片段过滤（含"预算/左右"等需求词的片段不是候选）
  routes         文档化拒绝路径（供维护者与 Agent 提示词参考，不参与运行时判定）

加载顺序：内置默认 ← 场景 reject_routes.jsonc（覆盖合并同名键）。
"""
from pathlib import Path

from decide_agent.common.jsonc import load as load_jsonc

DEFAULTS: dict = {
    "exact_reject": [
        "不知道", "不确定", "随便", "都行", "都好", "没有", "没想好", "无所谓",
    ],
    "segment_reject": [
        "预算", "人均以内", "以内", "左右", "附近", "近一点", "近点", "远点",
        "要吃", "想吃", "希望", "尽量", "最好", "别太",
    ],
    "routes": [
        {
            "when": "requirement-description-as-candidate",
            "reject": "需求描述不是候选",
            "detail": "含用途/偏好描述的句子（如 办公用、看看美剧）不是具体选项，应作为需求生成候选",
        },
    ],
}


def _merged(scene_data: dict) -> dict:
    merged = {
        "exact_reject": list(DEFAULTS["exact_reject"]),
        "segment_reject": list(DEFAULTS["segment_reject"]),
        "routes": list(DEFAULTS["routes"]),
    }
    for key, merged_items in merged.items():
        extra = scene_data.get(key)
        if isinstance(extra, list):
            for item in extra:
                if item not in merged_items:
                    merged_items.append(item)
    return merged


def load_reject_routes(scene: str, search_dirs: list[Path]) -> dict:
    """场景 reject_routes 合并内置默认；strip 后的正则用于片段过滤。"""
    scene_data: dict = {}
    for directory in search_dirs:
        path = Path(directory) / scene / "reject_routes.jsonc"
        if not path.exists():
            continue
        try:
            scene_data = load_jsonc(path) or {}
        except Exception:  # noqa: BLE001 — 数据缺陷不阻断（L4）
            break
    return _merged(scene_data if isinstance(scene_data, dict) else {})
