"""Rules loader tests: first-hit search, validation, golden structure of repo skills/food."""
import json
from pathlib import Path

import pytest

from decide_agent.experience.rules_loader import (
    ExprRule,
    RulesInvalidError,
    RulesNotFoundError,
    StepsRule,
    TagSetRule,
    load_rules,
)

REPO_SKILLS = Path(__file__).resolve().parents[3] / "skills"


def test_load_repo_food_rules_structure():
    rules = load_rules("food", [REPO_SKILLS])
    assert rules.scene == "food"
    assert len(rules.rules) == 5
    assert isinstance(rules.rule_for("taste_match"), TagSetRule)
    assert isinstance(rules.rule_for("price"), StepsRule)
    assert isinstance(rules.rule_for("distance"), ExprRule)
    assert rules.rule_for("nope") is None


def test_missing_scene_raises():
    with pytest.raises(RulesNotFoundError):
        load_rules("no_such_scene", [REPO_SKILLS])


def test_invalid_yaml_raises(tmp_path):
    scene_dir = tmp_path / "bad" / "rules.jsonc"
    scene_dir.parent.mkdir(parents=True)
    scene_dir.write_text(
        "scene: bad\n"
        "rules:\n"
        "    - dimension: price\n"
        "      kind: steps\n"
        "      field: p\n"
        "      default: 80\n"
        "      steps:\n"
        "          - {le: 40}\n",
        encoding="utf-8",
    )
    with pytest.raises(RulesInvalidError):
        load_rules("bad", [tmp_path])


def test_first_hit_wins(tmp_path):
    (tmp_path / "food").mkdir()
    (tmp_path / "food" / "rules.jsonc").write_text(json.dumps({
        "scene": "food",
        "rules": [{"dimension": "price", "kind": "steps",
                   "field": "price_per_person", "default": 80,
                   "steps": [{"le": 10, "score": 1.0}], "otherwise": 0.0}],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    rules = load_rules("food", [tmp_path, REPO_SKILLS])
    assert isinstance(rules.rule_for("price"), StepsRule)  # tmp dir wins, 1 rule only
    assert len(rules.rules) == 1
