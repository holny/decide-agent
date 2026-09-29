"""P1-8 packaging & offline acceptance: wheel contains builtin dirs; offline smoke is network-free."""
import sys
import zipfile
from pathlib import Path

import pytest

from decide_agent.core.decision.chain import ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.workflow.kernel import DecisionKernel
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.schemas.workflow import DecisionRequest

ROOT = Path(__file__).resolve().parents[2]
REPO_SKILLS = ROOT / "skills"
WEIGHTS = {
    "taste_match": 0.35, "distance": 0.20, "price": 0.20, "queue": 0.15, "weather_fit": 0.10,
}


@pytest.fixture(scope="module")
def wheel_path(tmp_path_factory) -> Path:
    import subprocess

    out_dir = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=ROOT, check=True, capture_output=True,
    )
    return next(out_dir.glob("decide_agent-*.whl"))


def test_wheel_contains_builtin_plugins_and_skills(wheel_path: Path):
    with zipfile.ZipFile(wheel_path) as zf:
        names = zf.namelist()
    assert "share/decide_agent/plugins/tools/location/tool.jsonc" in names
    assert "share/decide_agent/plugins/tools/poi_search/impl.py" in names
    assert "share/decide_agent/skills/food/rules.jsonc" in names
    assert "share/decide_agent/skills/food/info_needs.jsonc" in names


def test_offline_smoke_experience_chain_makes_no_network_calls():
    """断网冒烟（P1-8）：experience 模式全链零外联——audit hook 捕获任何 socket 尝试。"""
    attempts: list[tuple] = []

    def audit(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.bind"):
            attempts.append((event, args))

    sys.addaudithook(audit)
    engine = DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])]))
    kernel = DecisionKernel(engine)
    outcome = kernel.make_decision(DecisionRequest(
        question="想吃辣", scene_hint="food",
        candidates=[{"id": "a", "name": "蜀香居", "tags": ["辣"], "distance_m": 500,
                     "price_per_person": 65, "wait_min": 10}],
        slots={"taste_match": "辣"}, weights=WEIGHTS,
    ))
    assert outcome.status == "completed"
    assert attempts == [], f"experience 模式出现网络尝试: {attempts}"
