"""P6-2 微调数据集导出器：judgments JSONL → SFT 数据集（Laya 微调链路输入）。

格式：每行 {"messages":[{"role":"system",...},{"role":"user",...},{"role":"assistant",...}]}
仅取 outcome=ok 且 value 为数值的 score 判定（置信 ≥ 阈值的才值得学）。
"""
import json
from pathlib import Path

from decide_agent.schemas.judgment import JudgmentRecord

DEFAULT_CONFIDENCE_FLOOR = 0.6
SYSTEM_PROMPT = (
    "你是决策引擎的判断器。给定场景与原子问题（候选×维度打分），"
    "输出 JSON：{\"value\": 0~1, \"confidence\": 0~1, \"basis\": \"依据\"}"
)


def load_judgments(paths: list[Path]) -> list[JudgmentRecord]:
    records: list[JudgmentRecord] = []
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                records.append(JudgmentRecord.model_validate_json(line))
    return records


def to_sft_sample(record: JudgmentRecord) -> dict | None:
    """单条判定 → SFT 样本；不合格（非 ok / 非数值 / 低置信）返回 None。"""
    if record.outcome != "ok" or record.shape != "score":
        return None
    answer = record.answer or {}
    if not isinstance(answer.get("value"), int | float):
        return None
    if float(answer.get("confidence", 0.0)) < DEFAULT_CONFIDENCE_FLOOR:
        return None
    user_payload = {
        "scene": record.scene,
        "question": record.question,
    }
    assistant_payload = {
        "value": answer["value"],
        "confidence": answer.get("confidence", 0.6),
        "basis": answer.get("basis", ""),
    }
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(assistant_payload, ensure_ascii=False)},
        ],
    }


def export_dataset(
    judgment_paths: list[Path],
    out_path: Path,
    *,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
) -> dict:
    """导出并统计；返回 {total, exported, skipped, out}。"""
    records = load_judgments(judgment_paths)
    exported = 0
    lines: list[str] = []
    for record in records:
        if float((record.answer or {}).get("confidence", 0.0)) < confidence_floor:
            continue
        sample = to_sft_sample(record)
        if sample is None:
            continue
        lines.append(json.dumps(sample, ensure_ascii=False))
        exported += 1
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), "utf-8")
    return {
        "total": len(records),
        "exported": exported,
        "skipped": len(records) - exported,
        "out": str(out_path),
    }
