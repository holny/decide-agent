"""P5/P6 tests: unified manifest, scene templates, intent-miss log, dataset export."""
import json
from pathlib import Path

from decide_agent.plugins.manifest import discover_plugin_dirs

ROOT = Path(__file__).resolve().parents[3]
REPO_SKILLS = ROOT / "skills"
REPO_PLUGINS = ROOT / "plugins" / "tools"


def test_manifest_discovers_scene_templates_and_tools():
    templates = discover_plugin_dirs([REPO_SKILLS], kind="scene_template")
    assert {"food", "travel", "exam", "civil-service", "general"} <= set(templates)
    tools = discover_plugin_dirs([REPO_PLUGINS], kind="tool_server")
    assert {"location", "weather", "poi_search"} <= set(tools)


def test_invalid_plugin_kind_skipped(tmp_path, caplog):
    bad = tmp_path / "weird"
    bad.mkdir()
    (bad / "plugin.jsonc").write_text(json.dumps({"kind": "alien", "name": "weird"}), encoding="utf-8")
    (bad / "skill.jsonc").write_text(json.dumps({"name": "weird"}), encoding="utf-8")
    assert discover_plugin_dirs([tmp_path], kind="scene_template") == {}
    assert any("skip invalid plugin" in r.message for r in caplog.records)


def test_new_scene_classify_via_experience_provider():
    from decide_agent.experience.provider import ExperienceProvider

    provider = ExperienceProvider(search_dirs=[REPO_SKILLS])
    result = provider.answer(__import__(
        "decide_agent.schemas.question", fromlist=["TypedQuestion"]).TypedQuestion(
        shape="classify", text="五一去哪玩", context={}))
    assert result.value == "travel"
    result2 = provider.answer(__import__(
        "decide_agent.schemas.question", fromlist=["TypedQuestion"]).TypedQuestion(
        shape="classify", text="国考报名纠结岗位", context={}))
    assert result2.value == "civil-service"


def test_intent_miss_logger(tmp_path):
    from decide_agent.core.decision.chain import ProviderChain
    from decide_agent.core.decision.engine import DecisionEngine
    from decide_agent.core.workflow.kernel import DecisionKernel
    from decide_agent.experience.provider import ExperienceProvider
    from decide_agent.schemas.workflow import DecisionRequest

    misses: list[dict] = []
    engine = DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])]))
    kernel = DecisionKernel(engine, miss_logger=misses.append)
    kernel.make_decision(DecisionRequest(question="量子纠缠怎么解释"))  # 无场景命中 → miss
    assert len(misses) == 1
    assert misses[0]["text"] == "量子纠缠怎么解释"
    assert misses[0]["source"] in ("no_hit", "low_confidence")


def test_dataset_export(tmp_path):
    from decide_agent.experience.dataset import export_dataset
    from decide_agent.schemas.judgment import JudgmentRecord

    src = tmp_path / "judgments.jsonl"
    good = JudgmentRecord(
        ts="2026-09-24T00:00:00+00:00", scene="food", shape="score",
        provider="experience", outcome="ok",
        question={"shape": "score", "candidate": "蜀香居", "dimension": "taste_match"},
        answer={"value": 0.9, "confidence": 0.65, "basis": "命中口味"}, confidence=0.65,
    )
    low = good.model_copy(update={
        "id": "m2",
        "answer": {"value": 0.5, "confidence": 0.3},
    })
    src.write_text(good.model_dump_json() + "\n" + low.model_dump_json() + "\n", "utf-8")
    result = export_dataset([src], tmp_path / "sft.jsonl")
    assert result == {"total": 2, "exported": 1, "skipped": 1, "out": str(tmp_path / "sft.jsonl")}
    sample = json.loads((tmp_path / "sft.jsonl").read_text("utf-8").splitlines()[0])
    assert sample["messages"][0]["role"] == "system"
    assert json.loads(sample["messages"][2]["content"])["value"] == 0.9
