"""DecisionEngine: the four atomic question packets over one provider chain.

判断获取（本类）+ 决策合成（synthesis/gating/sandbox 纯函数）同属决策域。
纯逻辑、无网络 I/O：网络在 models/providers（适配层），规则在 experience（核心域）。

四形状（v4 §5.1）：classify（意图分布）/ score（候选×维度）/ extract（槽位）/ verify（校验）。
"""
from decide_agent.core.decision.chain import ChainResult, ProviderChain
from decide_agent.schemas.question import QuestionShape, TypedQuestion


class DecisionEngine:
    def __init__(self, chain: ProviderChain) -> None:
        self._chain = chain

    def classify(
        self, text: str, scene_list: list[str], scene_hint: str = "",
        scene_descriptions: dict[str, str] | None = None,
        context_text: str = "",
    ) -> ChainResult:
        context: dict = {"scenes": scene_list}
        if scene_descriptions:
            context["scene_descriptions"] = scene_descriptions  # 场景语义边界，供模型判定
        if context_text:
            context["conversation_context"] = context_text  # 连续对话：待答追问/当前推荐摘要
        question = TypedQuestion(
            shape=QuestionShape.CLASSIFY, scene=scene_hint, text=text, context=context,
        )
        return self._chain.answer(question)

    def score_one(
        self,
        scene: str,
        candidate: dict,
        dimension: str,
        *,
        env: dict | None = None,
        slots: dict | None = None,
    ) -> ChainResult:
        question = TypedQuestion(
            shape=QuestionShape.SCORE, scene=scene, dimension=dimension,
            context={"candidate": candidate, "env": env or {}, "slots": slots or {}},
        )
        return self._chain.answer(question)

    def score_batch(
        self,
        scene: str,
        candidates: list[dict],
        dimensions: list[str],
        *,
        env: dict | None = None,
        slots: dict | None = None,
    ) -> dict[tuple[str, str], ChainResult]:
        """候选×维度原子问题批量（即用即弃；规则缓存在 provider 内复用）。"""
        results: dict[tuple[str, str], ChainResult] = {}
        for candidate in candidates:
            cid = str(candidate.get("id", ""))
            for dimension in dimensions:
                results[(cid, dimension)] = self.score_one(
                    scene, candidate, dimension, env=env, slots=slots,
                )
        return results

    def score_questions(
        self, questions: list[TypedQuestion], *, state: str | None = None,
    ) -> list[ChainResult]:
        """批量入口：链上支持 answer_many 的 provider 一次多题（真实模式 5×提速）。"""
        return self._chain.answer_batch(questions, state=state)

    def extract(self, scene: str, text: str, slot: str) -> ChainResult:
        question = TypedQuestion(
            shape=QuestionShape.EXTRACT, scene=scene, text=text, slot=slot,
        )
        return self._chain.answer(question)

    def verify(self, scene: str, slot: str, claim: str) -> ChainResult:
        question = TypedQuestion(
            shape=QuestionShape.VERIFY, scene=scene, slot=slot, text=claim,
        )
        return self._chain.answer(question)
