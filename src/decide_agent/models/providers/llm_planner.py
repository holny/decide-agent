"""LLM 对话规划器：Agent Loop 的大脑——每轮根据对话状态选择下一个动作。

输出协议（JSON，复用 LLMProvider 的 JSON-forced 传输）：
  {"action": "tool", "name": <工具名>, "args": {...}}
  {"action": "reply", "text": <给用户的话>}
"""
import json
import os
from typing import Any

import httpx

from decide_agent.core.agent.dialogue import MAX_TOOL_ROUNDS, REPLY_ACTION
from decide_agent.core.decision.availability import ProbeResult, key_probe

_PLANNER_SYSTEM = """你是 decide-agent 决策智能体——帮选择困难的用户做对比决策。
你是高度可成长的：每次交互都在积累用户偏好与经验（自进化），越用越懂用户。
每轮你会看到【对话状态】（历史+环境事实+经验参谋材料）和【用户输入】，输出恰好一个动作（JSON）。

可用工具：
- start_decision(question, scene_hint?, slots?)                开一次完整决策（收集→打分→推荐/追问）；
                                                               缺候选时管道会用模型知识生成或追问
- answer_pending(decision_id, request_id, value)              用户已回答上轮追问
- give_feedback(decision_id, complaint)                       用户对推荐不满（含原因：太贵/去过了/太远…）
- reset_decision(decision_id)                                 推倒重来
- experience_baseline(text) / recall_memory(query)            查经验参谋材料/用户偏好
- reply(text)                                                 直接回复用户（闲聊/转述追问/说明）

行为准则：
1. 用户表达任何决策/选择意图（哪怕模糊，如「周末去哪玩」「想换个手机」）→ 立即调用
   start_decision，question 照抄用户原话。candidates 可省略——候选/地点/偏好等缺失信息
   由管道自动收集与追问，绝不自己代劳追问选项，也不要求用户提供候选清单。
   例：用户说「周末去哪玩」→ 立即输出
   {"action": "tool", "name": "start_decision", "args": {"question": "周末去哪玩"}}
   禁止：先反问「你想去哪/有几个选项/预算多少」——那是管道内追问（ask_user）的职责。
   同理禁止：把需求描述拆成候选传给 start_decision（如「想办公，看美剧」≠ 候选「办公」「美剧」——
   那是用户需求，原样放进 question 即可）。
2. 工具返回 ask_user → reply 简短转述其问题与编号选项（可加一句自己的话）。
   工具返回 recommendation → reply 转述推荐与理由。
3. 转述推荐必须带【当前推荐】/工具结果里的**具体数值**：距离（X米/公里）、人均（¥Y）、
   评分、排队——不要只说「距离近/花费适中」这种没有数字的话。
   对比类需求（用户给了多个候选）→ 推荐与备选都要列：各自总分 + 2~3 个关键维度得分对比，
   材料里都有，照实列全。
   维度名称**逐字引用**材料 dimension_scores 的键（如「需求匹配」「价格成本」），
   禁止自创或改名（如「风险」「后悔度」——材料里没有的维度就是不存在）。
4. 用户追问细节（多远/多少钱/评分多少/排队多久）→ 从【当前推荐】材料的 details 里作答；
   材料里没有的字段就明说没有，不要编造。
5. 上下文有「待回答追问」→ 用户这条输入默认就是在回答该追问 → answer_pending（除非用户
   明确说「换话题/重新开始」）。把用户说的映射到选项原文（序号/同义词都算），给 value；
   追问的是开放式信息（用途/预算）时，value 照抄用户原话。
6. 用户对推荐不满/去过/太贵 → give_feedback（原因照抄原话）。
7. reset_decision 仅当用户明确要求重来。
8. 经验参谋材料（keyword_prior/known_preferences）仅作参考，与你的语义判断冲突时以你为准。
9. 事实性问题（今天周几/现在几点/我在哪）→ 直接用【环境】材料回答；材料没有才说明。
10. 闲聊/询问助手本身/与决策无关 → reply。介绍自己时强调：决策智能体、会记忆偏好、越用越懂用户。
11. 只输出 JSON，键名固定英文：{"action": "reply"|"tool", "text": str, "name": str, "args": object}

示例（务必遵循）：
用户输入：「周末去哪玩」
正确：{"action": "tool", "name": "start_decision", "args": {"question": "周末去哪玩"}}
错误：reply 反问「你想去哪？有几个选项？预算多少？」← 决策意图禁止先用 reply 索要信息
用户输入：「多远？多少钱？」（上下文有当前推荐）
正确：reply 直接引用材料数值回答；材料没有就明说没有
用户输入：「今天周几」（环境里有当前时间）
正确：reply 告知星期与日期
拿不准该不该调 start_decision 时，优先调它——管道会自己决定追问什么。"""

_REPLY_ALIASES = {"reply", "respond", "answer", "say", "回复", "回答", "respond_to_user"}
_TOOL_ALIASES = {"tool", "工具", "调用", "call", "function"}


def _normalize_action(data: dict) -> dict:
    """动作键名/取值归一化：模型可能输出中文键（动作/参数）或别名（respond）。"""
    action = str(data.get("action") or data.get("动作") or data.get("type") or "").strip().lower()
    text = data.get("text") or data.get("回复") or data.get("内容") or data.get("response")
    name = data.get("name") or data.get("工具") or data.get("tool") or data.get("工具名")
    args = data.get("args") or data.get("参数") or data.get("arguments") or {}
    if action in _TOOL_ALIASES or name:
        if name and str(name).strip().lower() in _REPLY_ALIASES:
            # 模型把 reply 当工具名输出 → 按 reply 处理
            reply_text = text or (args or {}).get("text") if isinstance(args, dict) else text
            if reply_text is None or not str(reply_text).strip():
                reply_text = "好的。"
            return {"action": "reply", "text": str(reply_text)}
        if name:
            return {"action": "tool", "name": str(name),
                    "args": dict(args) if isinstance(args, dict) else {}}
    if (action in _REPLY_ALIASES or (text and not action)) \
            and text is not None and str(text).strip():
        return {"action": "reply", "text": str(text)}
    return {}


_INSTRUCTION = "请输出下一个动作 JSON（键名固定英文：action/text/name/args）。"


class LLMDialoguePlanner:
    name = "llm_planner"

    def __init__(
        self, base_url: str, model: str, api_key_env: str | None = None,
        api_key: str | None = None, timeout_ms: int = 20000,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_ms / 1000
        self._transport = transport

    def probe(self) -> ProbeResult:
        return key_probe(self._api_key_env) if self._api_key_env else ProbeResult()

    def _key(self) -> str:
        return self._api_key or (os.environ.get(self._api_key_env, "") if self._api_key_env else "")

    def generate_candidates(self, question: str, context: str = "",
                            retry_hint: str = "") -> str:
        """LLM 知识作为候选源（general 无数据源场景兜底）：返回顿号分隔的候选串。

        空/过短结果 → 带 retry_hint 硬重试一次（M2 偶发抽风，重试实测有效）。
        """
        body: dict[str, Any] = {
            "model": self._model, "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content":
                    "你是候选生成器。只输出 JSON：{\"candidates\": \"候选1、候选2、候选3\"}。"
                    "根据用户决策需求生成 3~4 个真实存在的具体候选（不同定位/价位），顿号分隔，"
                    "只输出候选本身不带解释。"
                    "若需求模糊到无法判断任何品类（如『不知道买什么』），candidates 返回 \"NEED_CATEGORY\""
                    "——让主流程先追问品类；能判断品类（如『平板电脑，办公用』）就必须生成具体型号。"
                    + (f"\n注意：{retry_hint}" if retry_hint else "")},
                {"role": "user", "content": (
                    (f"决策主题/上下文：{context}\n" if context else "")
                    + f"用户需求：{question}")},
            ],
        }
        headers = {"Authorization": f"Bearer {self._key()}"} if self._key() else {}
        # 候选生成是推理重活：单独放宽到 ≥45s（M2 长思考常态 20~40s）
        with httpx.Client(timeout=max(self._timeout_s, 45.0), transport=self._transport) as client:
            resp = client.post(f"{self._base_url}/chat/completions", json=body, headers=headers)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        from decide_agent.models.providers.llm_provider import _extract_json

        try:
            data = _extract_json(content)
        except RuntimeError:
            data = {}
        candidates = str(data.get("candidates") or "").strip()
        if len(candidates) < 4 and not retry_hint:  # 空/过短 → 硬重试一次
            return self.generate_candidates(
                question, retry_hint="用户需求里的用途词（如 办公/追剧/质感）不是候选；"
                                     "必须输出 3~4 个真实存在的具体产品型号（如 iPad Pro、小米平板6S Pro）。")
        return candidates

    def plan(self, state: dict, user_input: str, observations: list[dict]) -> dict:
        """从 LLM 取下一个动作；任何失败 → reply 兜底（让用户知道出错了并给选项）。"""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": json.dumps(
                {"对话状态": state, "用户输入": user_input}, ensure_ascii=False) + "\n" + _INSTRUCTION},
        ]
        for obs in observations[-MAX_TOOL_ROUNDS:]:
            messages.append({"role": "assistant", "content": json.dumps(obs["action"], ensure_ascii=False)})
            messages.append({"role": "user", "content": "工具观察：" + json.dumps(
                obs["result"], ensure_ascii=False) + "\n" + _INSTRUCTION})
        body: dict[str, Any] = {
            "model": self._model, "messages": messages, "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._key()}"} if self._key() else {}
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                resp = client.post(f"{self._base_url}/chat/completions", json=body, headers=headers)
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 — 规划失败 → reply 兜底（L4）
            return {"action": REPLY_ACTION, "text": f"（规划通道异常：{exc}）你可以 /retry 或直接说候选。"}
        from decide_agent.models.providers.llm_provider import _extract_json

        try:
            data = _extract_json(content)
        except RuntimeError:
            return {"action": REPLY_ACTION, "text": content.strip()[:500] or "（空回复）"}
        normalized = _normalize_action(data)
        if normalized:
            return normalized
        return {"action": REPLY_ACTION, "text": "（未能理解下一步，请换个说法或 /retry。）"}
