"""Golden case 回归（P8，Ontology 实践）：带完整证据链的确定性快照。

每个 tests/golden/*.json = 输入 + 预期判定链（场景/推荐/逐维得分/证据分级）。
知识、模板、提示词任何变更后运行——能精确指出哪条判断被改坏，而非只看输入输出。
"""
import json
from pathlib import Path

import pytest

from decide_agent.core.decision.chain import ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.workflow.kernel import DecisionKernel
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.schemas.workflow import DecisionRequest

REPO_SKILLS = Path(__file__).resolve().parents[4] / "skills"
GOLDEN_DIR = Path(__file__).resolve().parents[3] / "golden"


def _kernel() -> DecisionKernel:
    return DecisionKernel(
        DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])])),
        weights_resolver=lambda s: (
            {"fit": 0.3, "price": 0.3, "quality": 0.25, "service": 0.15}
            if s == "general" else
            {"taste_match": 0.35, "distance": 0.2, "price": 0.2, "queue": 0.15, "weather_fit": 0.1}
        ),
        gate_threshold=0.7,
        scenes=["food", "travel", "general", "chat"],
    )


GOLDEN_FILES = sorted(GOLDEN_DIR.glob("*.json"))


@pytest.mark.parametrize("golden_path", GOLDEN_FILES, ids=[p.stem for p in GOLDEN_FILES])
def test_golden_case_evidence_chain(golden_path: Path):
    data = json.loads(golden_path.read_text("utf-8"))
    outcome = _kernel().make_decision(DecisionRequest(**data["input"]))
    expected = data["expected"]

    assert outcome.status == "completed"
    assert outcome.result.scene == expected["scene"]

    rec = outcome.result.recommendation
    assert rec is not None
    assert rec.candidate.name == expected["recommendation"]

    if "total_score" in expected:
        assert abs(rec.total_score - expected["total_score"]) < 1e-3

    dims = {d.dimension: round(d.score, 3) for d in rec.dimension_scores}
    assert dims == expected["dimension_scores"], "逐维得分被改坏——定位变更的规则/模板"

    # 证据链（P8 proof_scope）：结论范围必须逐维一致
    assert outcome.result.evidence_scope == expected["evidence_scope"], (
        "证据分级被改坏——确认是有意的行为变更再更新 golden")
