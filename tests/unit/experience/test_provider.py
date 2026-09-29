"""ExperienceProvider golden tests: numbers must equal P0 heuristics exactly.

P0 reference (decision/engine.py score_dimension, pre-refactor):
  taste: hit 0.9 / miss 0.3 / no-pref 0.5(unscorable)
  distance: 1 - d/4000, d missing -> 1000
  price: <=40:0.9 <=70:0.7 <=100:0.5 else 0.2, missing -> 80
  queue: 1 - w/40, missing -> 0
  weather_fit: rain -> 1-d/3000 ; else 0.5 + 0.5*(1-d/3000)
Known P0 quirk NOT carried over: `x or default` also mapped 0 -> default;
here only None/missing maps to default (0 keeps its true value).
"""
from pathlib import Path

import pytest

from decide_agent.experience.provider import ExperienceProvider, UnsupportedShape
from decide_agent.experience.rules_loader import RulesNotFoundError
from decide_agent.schemas.question import TypedQuestion

REPO_SKILLS = Path(__file__).resolve().parents[3] / "skills"


def provider() -> ExperienceProvider:
    return ExperienceProvider(search_dirs=[REPO_SKILLS])


def q(dimension: str, *, candidate=None, env=None, slots=None) -> TypedQuestion:
    return TypedQuestion(
        shape="score", scene="food", dimension=dimension,
        context={"candidate": candidate or {}, "env": env or {}, "slots": slots or {}},
    )


def score(provider_, question) -> float:
    answer = provider_.answer(question)
    assert answer.provider == "experience"
    return float(answer.value)


def test_taste_golden():
    p = provider()
    assert score(p, q("taste_match", candidate={"tags": ["辣"]}, slots={"taste_match": "辣"})) == 0.9
    assert score(p, q("taste_match", candidate={"tags": ["粤菜"]}, slots={"taste_match": "辣"})) == 0.3
    assert score(p, q("taste_match", candidate={"tags": ["辣"]}, slots={"taste_match": "酸"})) == 0.3
    no_pref = p.answer(q("taste_match", candidate={"tags": ["辣"]}))
    assert float(no_pref.value) == 0.5
    assert no_pref.confidence == pytest.approx(0.35)  # neutral: low confidence (§5.2)
    assert no_pref.degraded is True


def test_distance_golden():
    p = provider()
    assert score(p, q("distance", candidate={"distance_m": 500})) == pytest.approx(0.875)
    assert score(p, q("distance", candidate={})) == pytest.approx(0.75)  # default 1000
    assert score(p, q("distance", candidate={"distance_m": 5000})) == 0.0  # clamped
    assert score(p, q("distance", candidate={"distance_m": 0})) == 1.0  # 0 is a real value


def test_price_golden():
    p = provider()
    assert score(p, q("price", candidate={"price_per_person": 30})) == 0.9
    assert score(p, q("price", candidate={"price_per_person": 65})) == 0.7
    assert score(p, q("price", candidate={"price_per_person": 90})) == 0.5
    assert score(p, q("price", candidate={"price_per_person": 120})) == 0.2
    assert score(p, q("price", candidate={})) == 0.5  # default 80 -> <=100 tier


def test_queue_golden():
    p = provider()
    assert score(p, q("queue", candidate={"wait_min": 10})) == pytest.approx(0.75)
    assert score(p, q("queue", candidate={})) == 1.0  # default 0
    assert score(p, q("queue", candidate={"wait_min": 60})) == 0.0


def test_weather_fit_golden():
    p = provider()
    assert score(p, q("weather_fit", candidate={"distance_m": 1500}, env={"weather": "小雨"})) == pytest.approx(0.5)
    assert score(p, q("weather_fit", candidate={"distance_m": 1500}, env={"weather": "晴"})) == pytest.approx(0.75)
    assert score(p, q("weather_fit", candidate={"distance_m": 1500})) == pytest.approx(0.75)  # no weather -> else


def test_unknown_dimension_neutral_low_confidence():
    p = provider()
    answer = p.answer(q(" ambiance "))
    assert float(answer.value) == 0.5
    assert answer.confidence == pytest.approx(0.35)
    assert answer.degraded is True


def test_unsupported_shape_raises():
    with pytest.raises(UnsupportedShape):
        provider().answer(TypedQuestion(shape="verify", scene="food"))


def test_probe_and_missing_scene():
    assert provider().probe().availability == "available"
    empty = ExperienceProvider(search_dirs=[])
    assert empty.probe().availability == "degraded"
    with pytest.raises(RulesNotFoundError):  # -> chain degrades (L4)
        provider().answer(TypedQuestion(shape="score", scene="no_such_scene", dimension="price"))


def test_extract_slots():
    p = provider()
    q = TypedQuestion(shape="extract", text="我想吃点清淡的，预算80以内，近一点", scene="food")
    answer = p.answer(q)
    import json
    slots = json.loads(answer.value)
    assert slots["taste_match"] == "清淡"
    assert slots["budget"] == "80"
    assert slots["distance_pref"] == "近"
    assert answer.confidence == 0.8


def test_extract_no_match():
    p = provider()
    q = TypedQuestion(shape="extract", text="量子纠缠怎么解释", scene="food")
    answer = p.answer(q)
    assert answer.value is None and answer.confidence == 0.2
