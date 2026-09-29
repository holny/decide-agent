"""LLM fallback provider: OpenAI/Anthropic-compatible chat with JSON-forced output.

角色纪律（红线 6）：LLM 永不参与“决定”——仅作判断②级兜底与核心外说话。
传输失败 raise → 链降级（L4）；无 key -> probe UNAVAILABLE（链自动跳过）。
"""
import json
import os
import re
from typing import Any

import httpx

from decide_agent.core.decision.availability import ProbeResult, key_probe
from decide_agent.schemas.question import TypedAnswer, TypedQuestion

_SYSTEM_PROMPTS = {
    "classify": "你是意图分类器。只输出 JSON：{\"value\": 场景名, \"distribution\": {场景: 概率}, \"confidence\": 0~1}",
    "score": "你是打分器。只输出 JSON：{\"value\": 0~1 的分数, \"confidence\": 0~1, \"basis\": \"简短依据\"}",
    "extract": "你是槽位抽取器。只输出 JSON：{\"value\": 抽取值或 null, \"confidence\": 0~1}。"
               "当 slot 为 feedback_filter（用户对推荐的负反馈）时，value 输出调整约束对象："
               "{\"exclude\": [要排除的关键词数组], \"max_price\": 人均上限数字或 null, "
               "\"max_distance_m\": 距离上限米数或 null}（没有的约束省略键；与调整无关则 value 为 null）。"
               "当 slot 为 dimensions（为一次对比决策生成评估维度）时，value 输出数组："
               "[{\"name\": \"维度名（2~6字，如 口味/价格/便携性/内容质量）\", \"weight\": 0~1 权重}]，"
               "3~5 个、权重和≈1；维度必须贴合该次决策语境——不适用该决策的常规维度直接省略"
               "（如请人帮忙选书可以没有价格维度）。",
    "verify": "你是校验器。只输出 JSON：{\"value\": true 或 false, \"confidence\": 0~1, \"basis\": \"依据\"}",
}


_SCENE_HINTS = {
    "chat": "闲聊、问候、询问助手自身、事实性提问（如今天是周几）等非决策类话题",
    "general": "真实的对比选择需求，但不在其他专门场景里（如选电脑、选礼物）",
}


def _classify_user_content(question: TypedQuestion) -> str:
    """classify 附带场景说明与对话上下文（与决策模型侧同源）。"""
    import json as _json

    body = _json.dumps(question.model_dump(mode="json"), ensure_ascii=False)
    scenes = (question.context or {}).get("scenes") or []
    descriptions = (question.context or {}).get("scene_descriptions") or {}
    hints = []
    for scene in scenes:
        desc = descriptions.get(scene) or _SCENE_HINTS.get(scene)
        if desc:
            hints.append(f"{scene}：{desc}")
    out = body if not hints else body + "\n场景说明：" + "；".join(hints)
    conversation = (question.context or {}).get("conversation_context")
    if conversation:
        out += "\n对话上下文：" + str(conversation)
    return out


def _extract_json(content: str) -> dict:
    """从 LLM 输出提取 JSON 对象（剥离 <think> 推理前缀等噪声）。"""
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE).strip()
    start = cleaned.find("{")
    if start == -1:
        raise RuntimeError(f"llm content has no JSON object: {content[:200]!r}")
    depth = 0
    for index in range(start, len(cleaned)):
        if cleaned[index] == "{":
            depth += 1
        elif cleaned[index] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:index + 1])
    raise RuntimeError(f"llm content JSON not closed: {content[:200]!r}")


class LLMProvider:
    name = "llm"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key_env: str | None = None,
        api_key: str | None = None,
        timeout_ms: int = 8000,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key  # 直接传 key（opencode 发现/测试用）
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_ms / 1000
        self._transport = transport

    def probe(self) -> ProbeResult:
        return key_probe(self._api_key_env)

    def answer(self, question: TypedQuestion) -> TypedAnswer:
        key = self._api_key or (os.environ.get(self._api_key_env) if self._api_key_env else None)
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPTS[question.shape.value]},
                {"role": "user", "content": (
                    _classify_user_content(question) if question.shape.value == "classify"
                    else json.dumps(question.model_dump(mode="json"), ensure_ascii=False)
                )},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                response = client.post(f"{self._base_url}/chat/completions", json=body, headers=headers)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            raise RuntimeError(f"llm transport failed: {type(exc).__name__}: {exc}") from exc
        data = _extract_json(content)
        return TypedAnswer(
            shape=question.shape,
            value=data.get("value"),
            distribution=data.get("distribution"),
            confidence=float(data.get("confidence", 0.0)),
            basis=data.get("basis"),
            provider=self.name,
        )

    def _request_preview(self, question: TypedQuestion) -> dict[str, Any]:
        return {"model": self._model, "messages": _SYSTEM_PROMPTS[question.shape.value]}
