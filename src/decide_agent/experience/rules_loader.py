"""Rules loader: load & validate scene rules.jsonc (experience baselines).

Rule kinds (ARCHITECTURE §8: when/then, tag + interval semantics):
- tag_set: slot value -> tag-set intersection against candidate tags
- expr:    expression tree (linear / cond / blend / const)
- steps:   tiered `le` thresholds with an otherwise score

Loading: search_dirs are given in priority order, first hit wins. Invalid
files raise RulesInvalidError (caller decides skip-and-warn, plugin policy).
"""
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator


class RulesNotFoundError(LookupError):
    """No rules.jsonc for the scene in any search dir."""


class RulesInvalidError(ValueError):
    """rules.jsonc exists but fails validation."""


# ---------------------------------------------------------------- expressions


class LinearExpr(BaseModel):
    kind: Literal["linear"]
    field: str
    default: float = Field(description="value used when the field is missing")
    far: float = Field(gt=0, description="field value at which score reaches 0")


class ConstExpr(BaseModel):
    kind: Literal["const"]
    value: float


class BlendExpr(BaseModel):
    kind: Literal["blend"]
    base: float
    weight: float
    inner: "Expr"


class CondExpr(BaseModel):
    kind: Literal["cond"]
    field: str
    in_: list[str] = Field(alias="in")
    then_: "Expr" = Field(alias="then")
    else_: "Expr" = Field(alias="else")


Expr = Annotated[
    LinearExpr | ConstExpr | BlendExpr | CondExpr,
    Field(discriminator="kind"),
]
ExprAdapter: TypeAdapter = TypeAdapter(Expr)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def eval_expr(expr: Expr, inputs: dict[str, Any]) -> float:
    """Pure interpreter over flattened inputs. Missing numeric field -> its default."""
    kind = expr.kind
    if kind == "linear":
        raw = inputs.get(expr.field)
        value = expr.default if raw is None else float(raw)
        return _clamp01(1.0 - value / expr.far)
    if kind == "const":
        return _clamp01(expr.value)
    if kind == "blend":
        return _clamp01(expr.base + expr.weight * eval_expr(expr.inner, inputs))
    if kind == "cond":
        actual = str(inputs.get(expr.field, "") or "")
        branch = expr.then_ if actual in expr.in_ else expr.else_
        return eval_expr(branch, inputs)
    raise RulesInvalidError(f"unknown expr kind: {kind}")  # pragma: no cover


# --------------------------------------------------------------------- rules


class TagOutcome(BaseModel):
    score: float
    basis: str | None = None


class TagSetRule(BaseModel):
    kind: Literal["tag_set"]
    dimension: str
    slot: str
    table: dict[str, list[str]] = Field(description="slot value -> tag set to intersect")
    hit: TagOutcome
    miss: TagOutcome
    on_missing_slot: TagOutcome = Field(default_factory=lambda: TagOutcome(score=0.5))

    def evaluate(self, inputs: dict[str, Any], tags: set[str]) -> tuple[float, str | None]:
        """Missing slot -> neutral (unscorable this round); unknown value -> miss."""
        value = inputs.get(self.slot)
        if value is None or str(value) == "":
            return self.on_missing_slot.score, self.on_missing_slot.basis
        target = set(self.table.get(str(value), set()))
        if target & tags:
            return self.hit.score, self.hit.basis
        return self.miss.score, self.miss.basis


class StepsRule(BaseModel):
    kind: Literal["steps"]
    dimension: str
    field: str
    default: float = Field(description="value used when the field is missing")
    steps: list[dict] = Field(description="ordered [{le, score}] tiers")
    otherwise: float = 0.0

    @field_validator("steps")
    @classmethod
    def _tiers_sorted(cls, tiers: list[dict]) -> list[dict]:
        if any("le" not in t or "score" not in t for t in tiers):
            raise ValueError("each step needs {le, score}")
        return sorted(tiers, key=lambda t: t["le"])

    def evaluate(self, inputs: dict[str, Any]) -> float:
        raw = inputs.get(self.field)
        value = self.default if raw is None else float(raw)
        for tier in self.steps:
            if value <= tier["le"]:
                return _clamp01(float(tier["score"]))
        return _clamp01(self.otherwise)


class ExprRule(BaseModel):
    kind: Literal["expr"]
    dimension: str
    expr: Expr

    def evaluate(self, inputs: dict[str, Any]) -> float:
        return eval_expr(self.expr, inputs)


Rule = Annotated[TagSetRule | StepsRule | ExprRule, Field(discriminator="kind")]


class SceneRules(BaseModel):
    scene: str
    version: int = 1
    confidence: float = 0.65
    neutral_confidence: float = 0.35
    rules: list[Rule] = Field(default_factory=list)

    def rule_for(self, dimension: str) -> Rule | None:
        return next((r for r in self.rules if r.dimension == dimension), None)


def load_rules(scene: str, search_dirs: list[Path]) -> SceneRules:
    """First rules.jsonc found under <dir>/<scene>/rules.jsonc wins (dirs in priority order)."""
    for directory in search_dirs:
        path = Path(directory) / scene / "rules.jsonc"
        if not path.exists():
            continue
        try:
            from decide_agent.common.jsonc import load as load_jsonc

            data = load_jsonc(path)
            return SceneRules.model_validate(data)
        except Exception as exc:
            raise RulesInvalidError(f"{path}: {exc}") from exc
    raise RulesNotFoundError(f"no rules.jsonc for scene '{scene}' in {[str(d) for d in search_dirs]}")


BlendExpr.model_rebuild()
CondExpr.model_rebuild()
