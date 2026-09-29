"""SystemOneAdapter: 云端/自托管判断模型（POST {endpoint}/v1/systemone）。

协议（SystemOne 官方 OpenAPI；Laya-serve 同源兼容）：
  POST /v1/systemone
    {state: 内容本体, model?: 别名, questions: {name: Question}}
    Question 三型：choice{instructions,criteria:{名:描述}} /
                   score{instructions,criteria:[有序等级]} / noul{instructions,criteria?{true,false}}
    → {model, answers: {name: Answer}, usage}
    ChoiceAnswer{choice,confidence,probabilities} / ScoreAnswer{score(期望等级),confidence,legend}
    / NoulAnswer{noul(yes 概率)}

四问题包映射（P4-2）：classify=choice(场景) / score=score(rubric) / extract=choice(槽位)
/ verify=noul(断言)。score 期望等级归一化：value = score / (levels-1)。
厂商名只在本模块与 config（红线 3）。密钥永不落盘（api_key_env 引用 env）。
"""
import json
import os
from typing import Any

import httpx

from decide_agent.core.decision.availability import ProbeResult, key_probe
from decide_agent.schemas.question import TypedAnswer, TypedQuestion

DEFAULT_SCORE_RUBRIC = ["非常不匹配", "不匹配", "一般", "匹配", "非常匹配"]


class SystemOneAdapter:
    name = "decision_model"

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key_env: str | None = None,
        timeout_ms: int = 5000,
        transport: httpx.BaseTransport | None = None,
        score_rubric: list[str] | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key_env = api_key_env
        self._model = model  # 官方必填：模型名属外部配置
        self._timeout_s = timeout_ms / 1000
        self._transport = transport
        self._rubric = list(score_rubric or DEFAULT_SCORE_RUBRIC)

    def probe(self) -> ProbeResult:
        return key_probe(self._api_key_env)

    # ------------------------------------------------------------------ transport

    def answer(self, question: TypedQuestion) -> TypedAnswer:
        return self.answer_many([question], state=None)[0]

    def answer_many(self, questions: list[TypedQuestion], *, state: str | None = None) -> list[TypedAnswer]:
        """官方批量：一次 POST 携带多题（同 state，候选细节在各题 instructions）。"""
        if not questions:
            return []
        key = os.environ.get(self._api_key_env) if self._api_key_env else None
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        names = [f"q{i}" for i in range(len(questions))]
        body: dict[str, Any] = {
            "state": state or questions[0].text or questions[0].scene,
            "questions": {name: self._typed_question(q)[1] for name, q in zip(names, questions)},
            "model": self._model,
        }
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                response = client.post(f"{self._endpoint}/v1/systemone", json=body, headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise RuntimeError(f"systemone transport failed: {type(exc).__name__}: {exc}") from exc
        answers = data.get("answers") or {}
        out: list[TypedAnswer] = []
        for name, question in zip(names, questions):
            answer = answers.get(name)
            if answer is None:
                raise RuntimeError(f"systemone response missing answer {name}: {data!r}")
            out.append(self._parse_answer(answer, question))
        return out

    # ------------------------------------------------------------------ protocol

    def _request_payload(self, question: TypedQuestion) -> dict[str, Any]:
        state, question_body = self._typed_question(question)
        body: dict[str, Any] = {"state": state, "questions": {"q0": question_body}}
        if self._model:
            body["model"] = self._model
        return body

    def _typed_question(self, question: TypedQuestion) -> tuple[str, dict[str, Any]]:
        shape = question.shape.value
        candidate = question.context.get("candidate")
        candidate_text = (
            json.dumps(candidate, ensure_ascii=False)
            if isinstance(candidate, dict) else str(candidate or "")
        )
        if shape == "classify":
            scenes = question.context.get("scenes") or ["general"]
            descriptions = question.context.get("scene_descriptions") or {}
            conversation = question.context.get("conversation_context") or ""
            state = (f"{conversation}\n用户输入：{question.text or ''}"
                     if conversation else question.text or "")
            criteria = {
                s: descriptions.get(s)
                or ("闲聊、问候、询问助手自身、观点提问等非决策类话题" if s == "chat"
                    else f"用户需求属于「{s}」场景")
                for s in scenes
            }
            return state, {
                "type": "choice",
                "instructions": "结合对话上下文，判断用户这条输入最符合哪个意图？只依据内容选择。",
                "criteria": criteria,
            }
        if shape == "score":
            return candidate, {
                "type": "score",
                "instructions": (
                    f"评估候选 {candidate_text} 在「{question.dimension}」维度的匹配程度。"
                    f"用户偏好：{json.dumps(question.context.get('slots') or {}, ensure_ascii=False)}；"
                    f"环境：{json.dumps(question.context.get('env') or {}, ensure_ascii=False)}；"
                    "分数越高越匹配。"
                ),
                "criteria": self._rubric,
            }
        if shape == "extract":
            options = question.context.get("options") or []
            return question.text or "", {
                "type": "choice",
                "instructions": f"从内容中抽取槽位「{question.slot}」的取值。",
                "criteria": {str(o): f"该槽位取值为 {o}" for o in options} or {"none": "无法抽取"},
            }
        if shape == "verify":
            return question.context.get("candidate_text", "") or question.text or "", {
                "type": "noul",
                "instructions": str(question.text or ""),
                "criteria": {"true": "断言成立", "false": "断言不成立"},
            }
        raise RuntimeError(f"unsupported shape for systemone: {shape}")

    def _parse_answer(self, answer: dict, question: TypedQuestion) -> TypedAnswer:
        if "confidence" not in answer and answer.get("type") != "noul":
            raise RuntimeError(f"systemone answer missing confidence: {answer!r}")
        answer_type = answer.get("type")
        if answer_type == "score":
            levels = max(1, len(self._rubric) - 1)
            return TypedAnswer(
                shape=question.shape,
                value=round(float(answer["score"]) / levels, 3),
                confidence=float(answer["confidence"]),
                # legend 是量尺回显（如 {"0": "非常不匹配"...}），非判断依据——
                # 混入 basis 会污染下游文本匹配（如 Reflexion 的「不匹配」检测）
                basis=None,
                provider=self.name,
            )
        if answer_type == "choice":
            return TypedAnswer(
                shape=question.shape,
                value=answer.get("choice"),
                distribution=answer.get("probabilities"),
                confidence=float(answer["confidence"]),
                provider=self.name,
            )
        if answer_type == "noul":
            probability = float(answer["noul"])
            return TypedAnswer(
                shape=question.shape,
                value=probability,
                confidence=probability,  # noul 无独立置信：以概率自校准
                basis=None,
                provider=self.name,
            )
        raise RuntimeError(f"unknown answer type: {answer_type}")

    def _request_payload_preview(self, question: TypedQuestion) -> dict[str, Any]:
        return self._request_payload(question)
