"""DecisionKernel: the make_decision lifecycle — 意图→收集→判断→决策→呈现 (fully sync).

编排者：唯一允许 import 各领域模块的层（经构造注入）。职责只有流程与状态：
- 阶段指纹续算（相同输入 Skip 复用，指纹命中在 outcome.fingerprint_skips 披露）
- 挂起/恢复：gate 不自信或沙盒翻转 → PendingRequest(question) → input-required；
  respond 双键幂等（重复应答原样重放）；过期 → 不合并信息、降级顺延
- ask_budget 计费：预算尽 → 静默降级同路径（缺口进 missing_information）
- 候选空 → block_to_clarify（唯一硬结局，interactive 时转澄清）
呈现（narrator）与收集（CollectScheduler）为可选阶段：P1-5/P1-6 接入。
"""
import json
import re
import time
from collections.abc import Callable
from typing import Any, ClassVar, Protocol

from decide_agent.common.ids import new_decision_id
from decide_agent.common.log import get_logger
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.decision.extract import extract_joint_party, extract_subject
from decide_agent.core.decision.pareto import pareto_filter
from decide_agent.core.decision.sandbox import flip_probabilities
from decide_agent.core.decision.synthesis import synthesize
from decide_agent.core.workflow.budget import Budgets
from decide_agent.core.workflow.fingerprint import StageFingerprint
from decide_agent.core.workflow.pending import PendingRegistry
from decide_agent.core.workflow.state_machine import DecisionState, StateMachine
from decide_agent.schemas.candidate import Candidate
from decide_agent.schemas.collect import CollectorOutcome
from decide_agent.schemas.decision import DecisionResult, DimensionScore, Recommendation
from decide_agent.schemas.memory import MemoryKind, MemoryRecord, MemoryScope
from decide_agent.schemas.question import Question, QuestionShape, TypedQuestion
from decide_agent.schemas.workflow import (
    DecisionOutcome,
    DecisionRequest,
    PendingKind,
    PendingRequest,
)


class Collector(Protocol):
    """collect 能力最小协议（TemplateCollector/CollectScheduler 实现，P1-5）。"""

    def collect(
        self, scene: str, request: DecisionRequest, skip: set[str] | None = None,
    ) -> CollectorOutcome: ...


CHAT_SCENE = "chat"  # 非决策闲聊伪场景：classify 命中 → kernel 短路边（不收集/不评分）

# 入口意图（连续对话上下文下由链上 classify 语义判定——决策模型优先，任意语言）
ENTRY_INTENTS: ClassVar[dict[str, str]] = {
    "new_decision": "提出一个新的对比选择需求，或继续补充新决策的信息（如：周末去哪玩 / 帮我对比 A和B / 笔记本电脑）",
    "swap_next": "想换下一个备选推荐（如：换一个 / 下一个 / 다음）",
    "dissatisfied": "对当前推荐不满，想说明原因或要求调整（如：不行 / 太贵了 / 去过了 / 별로예요）",
    "answer_pending": "正在回答助手上一轮的追问（给出选项序号/同义词，如：自然 / 1 / 办公用）",
    "fact_question": "事实性提问（今天周几 / 现在几点 / 你是谁 / 我在哪）",
    "chat": "与当前决策无关的闲聊",
}

_NUM_REPLY = re.compile(r"^\s*(?:选|选择)?\s*(\d{1,2})\s*[.、。]?\s*$")  # 纯序号应答（narrator 编号选项）


class _Session:
    """Per-decision in-process state (runtime/ snapshot candidate; persistence P3)."""

    def __init__(self, request: DecisionRequest) -> None:
        self.request = request
        self.machine = StateMachine()
        self.fingerprint = StageFingerprint()
        self.scene: str | None = None
        self.candidates: list[dict] = []
        self.score_cache: dict[tuple[str, str], object] = {}
        self.ask_left = request.ask_budget
        self.collect_disclosed: list[str] = []
        self.learned_memory: list[dict] = []
        self.inferred_weights: dict | None = None  # 模型推断的动态维度（general 场景）
        self.subject: str | None = None
        self.joint_with: str | None = None
        self.slots_seeded_from: str | None = None


_log = get_logger("kernel")


class DecisionKernel:
    def __init__(
        self,
        engine: DecisionEngine,
        collector: Collector | None = None,
        budgets: Budgets | None = None,
        weights_resolver: Callable[[str], dict[str, float]] | None = None,
        question_resolver: Callable[[str, str], dict | None] | None = None,
        memory=None,
        owner_id: str = "local",
        miss_logger: Callable[[dict], None] | None = None,
        scenes: list[str] | None = None,
        user_dims_resolver: Callable[[str], frozenset[str]] | None = None,
        subject_memory_factory: Callable[[str], Any] | None = None,
        *,
        gate_threshold: float = 0.6,
        reject_routes_loader: Callable[[str], dict] | None = None,
        penalty_per_missing: float = 0.40,
        question_timeout_s: float | None = None,
        clock: callable = time.time,  # type: ignore[valid-type]
    ) -> None:
        self._engine = engine
        self._collector = collector
        self._budgets = budgets
        self._weights_resolver = weights_resolver
        self._question_resolver = question_resolver
        self._memory = memory  # MemoryService（P2）：scope session/always 应答落记忆
        self._owner_id = owner_id
        self._miss_logger = miss_logger  # P6-4：意图 miss → 进化模板需求来源
        self._subject_memory_factory = subject_memory_factory  # 第三方记忆：记在对象名下，复用
        self._scenes = scenes or []
        self._user_dims_resolver = user_dims_resolver
        self._threshold = gate_threshold
        self._penalty = penalty_per_missing
        self._reject_routes_loader = reject_routes_loader
        self._question_timeout_s = question_timeout_s
        self._clock = clock
        self._pending = PendingRegistry()
        self._sessions: dict[str, _Session] = {}
        self._replay: dict[str, DecisionOutcome] = {}

    @property
    def pending(self) -> PendingRegistry:
        """Channel sweeper entry: kernel.pending.expire_due(now)."""
        return self._pending

    def sweep(self, now: float | None = None) -> list[str]:
        """过期挂起清理（通道 sweeper 周期调用）；返回过期 request_id。"""
        return self._pending.expire_due(now)

    def pending_item(self, request_id: str):
        """通道读取挂起详情（decision_id 等）；未知 request_id 抛 KeyError。"""
        return self._pending.get(request_id)

    # ------------------------------------------------------------------ 持久化（红线 13）

    def snapshot(self, decision_id: str) -> dict:
        """挂起态决策快照：runtime/ 落盘由通道层负责，内核只产出纯数据。"""
        session = self._sessions.get(decision_id)
        if session is None:
            raise KeyError(f"unknown decision: {decision_id}")
        return {
            "request": session.request.model_dump(mode="json"),
            "scene": session.scene,
            "candidates": session.candidates,
            "state": session.machine.state.value,
            "ask_left": session.ask_left,
            "collect_disclosed": session.collect_disclosed,
            "learned_memory": session.learned_memory,
            "fingerprints": {
                stage: digest
                for stage, digest in session.fingerprint._hashes.items()
            },
            "pending": [p.model_dump(mode="json") for p in self._pending.pending_for(decision_id)],
        }

    def restore(self, decision_id: str, snap: dict) -> None:
        """进程重启后恢复挂起会话（score 缓存不恢复——重算即得，结果不变）。"""
        request = DecisionRequest.model_validate(snap["request"])
        request.decision_id = decision_id
        session = _Session(request)
        session.machine = StateMachine(initial=DecisionState(snap["state"]))
        session.scene = snap["scene"]
        session.candidates = snap["candidates"]
        session.ask_left = snap["ask_left"]
        session.collect_disclosed = snap["collect_disclosed"]
        session.learned_memory = snap["learned_memory"]
        session.fingerprint.restore(snap["fingerprints"])
        self._sessions[decision_id] = session
        for item in snap["pending"]:
            self._pending.restore(PendingRequest.model_validate(item))

    def entry_intent(self, text: str, context: str = "") -> str | None:
        """入口意图语义判定（链上 classify，决策模型优先，任意语言）。

        context 携带待回答追问/当前推荐摘要，供模型区分 answer_pending 与 new_decision。
        返回 ENTRY_INTENTS 键之一；低把握或链全败 → None（调用方按 new_decision 处理）。
        """
        result = self._engine.classify(
            text, scene_list=list(ENTRY_INTENTS),
            scene_descriptions=dict(ENTRY_INTENTS),
            context_text=context,
        )
        label = _top_label(result)
        if label is None or label not in ENTRY_INTENTS:
            return None
        if not result.ok or not result.answer or float(result.answer.confidence) < 0.5:
            return None
        return label

    def give_feedback(self, decision_id: str, complaint: str) -> DecisionOutcome:
        """负反馈最小闭环（P4-5）：语义抽取调整约束 → 过滤候选 → 重算推荐。

        抽取契约（extract/feedback_filter，LLM 层语义判定）：
          {"exclude": [排除关键词], "max_price": 人均上限, "max_distance_m": 距离上限}
        抽取不到可执行约束 → 不假装变推荐，披露说明请更具体（L4）。
        """
        session = self._sessions.get(decision_id)
        if session is None:
            raise KeyError(f"unknown decision: {decision_id}")
        disclosed: list[str] = []
        spec = self._extract_feedback_filter(session, complaint)
        if spec:
            before = len(session.candidates or [])
            kept = [c for c in (session.candidates or []) if self._keep_by_feedback(c, spec)]
            if kept and len(kept) < before:
                session.request.candidates = kept
                session.candidates = kept
                session.fingerprint = StageFingerprint()
                session.score_cache = {}
                disclosed.append(f"已按反馈过滤 {before - len(kept)} 个候选")
            elif not kept:
                disclosed.append("反馈过滤掉了全部候选，已保留原候选——换个说法或直接给候选")
            else:
                disclosed.append("反馈未筛掉候选（当前数据下无匹配项）——试试更具体的说法")
        else:
            disclosed.append("未能从反馈中识别出可执行的调整——试试「太贵 / 太远 / 不想去博物馆」，或直接给候选")
        session.machine = StateMachine()  # COMPLETED 是终态 → 重建机器走分析段
        session.machine.transition(DecisionState.COLLECTING)
        session.machine.transition(DecisionState.ANALYZING)
        outcome = self._analyze(decision_id, session, [])
        if disclosed:
            outcome.missing_information = [*disclosed, *outcome.missing_information]
        return outcome

    def _extract_feedback_filter(self, session: _Session, complaint: str) -> dict | None:
        result = self._engine.extract(session.scene or "general", complaint, slot="feedback_filter")
        raw = result.answer.value if result.ok and result.answer else None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = None
        return raw if isinstance(raw, dict) and raw else None

    @staticmethod
    def _keep_by_feedback(cand: dict, spec: dict) -> bool:
        blob = f"{cand.get('name', '')}{''.join(cand.get('tags') or [])}"
        for word in spec.get("exclude") or []:
            if str(word) and str(word) in blob:
                return False
        max_price = spec.get("max_price")
        if max_price is not None and cand.get("price_per_person") is not None \
                and float(cand["price_per_person"]) > float(max_price):
            return False
        max_dist = spec.get("max_distance_m")
        return not (
            max_dist is not None and cand.get("distance_m") is not None
            and int(cand["distance_m"]) > int(max_dist)
        )

    def reset(self, decision_id: str) -> DecisionOutcome:
        """推倒重来：清空当前决策的偏好/候选，从头开始（保留 decision_id）。"""
        session = self._sessions.get(decision_id)
        if session is None:
            raise KeyError(f"unknown decision: {decision_id}")
        session.request.slots = {}
        session.request.candidates = []
        session.request.weights = {}  # 陈旧权重会短路动态维度推断
        session.candidates = []
        session.inferred_weights = None
        session.collect_disclosed = []
        session.score_cache = {}
        session.fingerprint = StageFingerprint()
        session.ask_left = session.request.ask_budget
        session.machine = StateMachine()
        session.machine.transition(DecisionState.COLLECTING)
        session.scene = None
        outcome = self.make_decision(session.request)
        return outcome

    def accept(self, decision_id: str) -> bool:
        """宿主接受推荐：suggested → completed（feedback 端点编排用）。"""
        session = self._sessions.get(decision_id)
        if session is None:
            raise KeyError(f"unknown decision: {decision_id}")
        session.machine.transition(DecisionState.COMPLETED)
        return True

    def make_decision(self, request: DecisionRequest) -> DecisionOutcome:
        did = request.decision_id or new_decision_id()
        request.decision_id = did
        session = self._sessions.setdefault(did, _Session(request))
        sm = session.machine
        sm.transition(DecisionState.COLLECTING)
        skips: list[str] = []

        # ⓪⁻ 决策对象识别：帮朋友/家人/同事等第三方选择 → 用户记忆不套用（隐私+正确性）
        subject = request.subject or extract_subject(request.question)
        session.subject = subject
        joint_with = request.joint_with or (
            None if subject else extract_joint_party(request.question)
        )
        session.joint_with = joint_with

        # 共同决策（帮我和xx）：双方记忆召回播种；冲突维度按中性 + 披露；学习暂缓（v2）
        if joint_with:
            for owner_id in (self._owner_id, joint_with):
                if owner_id == self._owner_id:
                    svc = self._memory  # 用户本人记忆（users/{owner}/memory.json）
                elif self._subject_memory_factory is not None:
                    svc = self._subject_memory_factory(owner_id)  # 对方记忆（subjects/）
                else:
                    continue
                try:
                    records = svc.export(owner_id)
                except Exception as exc:  # noqa: BLE001 — 记忆召回失败不阻断（L4）
                    _log.warning("memory recall skipped for %s: %s", owner_id, exc)
                    continue
                latest: dict[str, MemoryRecord] = {}
                for record in sorted(records, key=lambda r: r.created_at, reverse=True):
                    if record.kind is not MemoryKind.PREFERENCE \
                            or record.status.value != "active" \
                            or ":" not in record.content:
                        continue
                    dim = record.content.partition(":")[0]
                    latest.setdefault(dim, record)  # 同维度取最新一条（偏好会演进）
                for dim, record in latest.items():
                    value = record.content.partition(":")[2]
                    if dim in session.request.slots:
                        if session.request.slots[dim] != value:
                            disclosed_hint = (
                                f"你与{joint_with}在「{dim}」偏好不一致"
                                f"（{session.request.slots[dim]} vs {value}），该维度按中性处理"
                            )
                            session.collect_disclosed.append(disclosed_hint)
                            session.request.slots[dim] = " ".join(
                                sorted({session.request.slots[dim], value}),
                            )
                    else:
                        session.request.slots[dim] = value
            if joint_with:
                session.collect_disclosed.append(
                    f"共同决策（你与{joint_with}）：双方已知偏好均已计入；"
                    "共同偏好的持续学习将在后续版本支持",
                )

        # 第三方记忆召回：把对象名下的已知偏好播种进 slots（下次帮同一位朋友不再追问）
        if subject and self._subject_memory_factory is not None:
            svc = self._subject_memory_factory(subject)
            try:
                records = svc.export(subject)
            except Exception as exc:  # noqa: BLE001 — 记忆召回失败不阻断（L4，同 306 行先例）
                _log.warning("subject memory recall skipped for %s: %s", subject, exc)
                records = []
            for record in records:
                if record.kind is MemoryKind.PREFERENCE and record.status.value == "active" \
                        and ":" in record.content:
                    dim, _, value = record.content.partition(":")
                    session.request.slots.setdefault(dim, value)
            session.slots_seeded_from = subject

        # ⓪ 用户一句话自带选项 → 抽取为候选（§10.2③ extract 规则层）
        if not request.candidates:
            from decide_agent.core.decision.extract import extract_inline_candidates

            reject_cfg = (
                self._reject_routes_loader("general")
                if self._reject_routes_loader else None
            )
            parsed = extract_inline_candidates(
                request.question,
                reject_segments=(reject_cfg or {}).get("segment_reject"),
            )
            if parsed is not None:
                from decide_agent.schemas.workflow import normalize_candidates

                question_text, options = parsed
                request.question = question_text
                request.candidates = normalize_candidates(options)
                session.collect_disclosed.append(
                    "已从输入识别候选：" + " / ".join(options),
                )

        # ① 意图：hint 优先；否则 classify（指纹续算），失败/低置信兜底 general
        scene = request.scene_hint
        confidence = 0.0
        if scene is None:
            payload = {"q": request.question}
            if session.fingerprint.should_skip("classify", payload):
                skips.append("classify")
                scene = session.scene
            else:
                result = self._engine.classify(request.question, scene_list=list(self._scenes))
                scene = _top_label(result)
                if scene is not None and result.ok and result.answer:
                    best = float(result.answer.confidence)
                    if best < 0.5:
                        scene = None  # 低置信 → 走 general
                confidence = float(result.answer.confidence) if result.ok and result.answer else 0.0
                if self._miss_logger is not None and (scene is None or confidence < 0.5):
                    self._miss_logger({
                        "ts": self._clock(), "text": request.question,
                        "top": scene, "confidence": confidence,
                        "source": "no_hit" if scene is None else "low_confidence",
                    })
                session.fingerprint.update("classify", payload)
        if scene is None:
            scene = "general"
        session.scene = scene  # A拍早退分支也需要 scene（respond 重收集依赖）

        # ①⁺ 非决策闲聊（chat 场景）→ 短路边：友好回应，不进收集/评分，不追问候选
        # 用户已给候选（内联/直供）= 明确决策意图 → chat 判定视为模型误判，不短路
        if scene == CHAT_SCENE and not request.candidates:
            return self._chat_reply(did, session, skips, confidence)

        # ①⁺⁺ general 无模板场景 → 模型按决策语境动态推断评估维度（权重）；
        # 推断失败回落通用模板。维度贴合语境：送书可以没有价格，比咖啡不必有售后。
        if scene == "general" and not request.weights:
            dim_payload = {"q": request.question, "slot": "dimensions"}
            if session.fingerprint.should_skip("dimensions", dim_payload):
                if session.inferred_weights:
                    request.weights = session.inferred_weights
            else:
                raw = None
                result = self._engine.extract(request.question, "general", slot="dimensions")
                if result.ok and result.answer is not None:
                    raw = result.answer.value
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        raw = None
                weights: dict[str, float] = {}
                if isinstance(raw, list):
                    for item in raw:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("name") or "").strip()
                        weight = item.get("weight")
                        if not (1 <= len(name) <= 8) or name in weights or weight is None:
                            continue
                        try:
                            weights[name] = max(0.05, min(0.95, float(weight)))
                        except (TypeError, ValueError):
                            continue
                if len(weights) >= 2:
                    total = sum(weights.values()) or 1.0
                    request.weights = {k: round(v / total, 3) for k, v in weights.items()}
                    session.inferred_weights = request.weights
                    session.collect_disclosed.append(
                        "评估维度（模型按语境推断）：" + "、".join(request.weights))
                session.fingerprint.update("dimensions", dim_payload)

        # ② 收集（可选阶段）：caller 自带候选 → skip；inline_tools 声明 → 跳过对应内置
        if request.inline_tools:
            session.collect_disclosed.append(
                "请求内联工具：" + " / ".join(request.inline_tools) + "（由调用方提供数据）",
            )
        candidates = request.candidates
        if not candidates and self._collector is not None:
            # inline_tools 声明的能力 → 由调用方提供，collect 跳过对应工具
            skip_caps = set(request.inline_tools)
            collected = self._collector.collect(scene, request, skip=skip_caps)
            candidates = collected.candidates
            if collected.env:
                request.env = {**request.env, **collected.env}
            session.collect_disclosed.extend(collected.disclosed)
            # A拍：阻塞缺口（ask_once 失败）收集启动即问，不空转
            if collected.needs and request.interactive and session.ask_left > 0:
                session.ask_left -= 1.0
                sm.transition(DecisionState.ASKING)
                need = dict(collected.needs[0])
                question = dict(need.get("question") or {})
                slot = str(need.get("slot") or need.get("capability") or "")
                known = self._memory_dimension_values(f"{slot}:")[:3]
                if known:  # 记忆有历史取值 → 追问带选项 + 建议项（经验参谋）
                    question["options"] = [*known, *(question.get("options") or [])]
                    question["recommended"] = known[0]
                    if slot == "location":
                        question["text"] = (
                            f"自动定位不可用，{question.get('text', '请提供位置')}（可直接输入城市名）")
                from decide_agent.schemas.question import Question

                need["question"] = Question(
                    text=str(question.get("text") or f"请提供{need.get('purpose') or slot}信息"),
                    options=[str(o) for o in question.get("options", [])],
                    recommended=question.get("recommended"),
                    allow_free_text=bool(question.get("allow_free_text", True)),
                    reason="自动定位/收集失败，需人工补充" if slot == "location" else None,
                ).model_dump()
                pending = self._pending.create(
                    did, PendingKind.QUESTION, payload={**need},
                    timeout_s=self._question_timeout_s, budget_cost=1.0, clock=self._clock(),
                )
                sm.transition(DecisionState.INPUT_REQUIRED)
                return DecisionOutcome(
                    decision_id=did, status="require_action", state=sm.state.value,
                    pending=pending, missing_information=list(session.collect_disclosed),
                    fingerprint_skips=skips, budget_left=session.ask_left,
                    learned_memory=list(session.learned_memory),
                )
        session.scene, session.candidates = scene, candidates

        if not candidates:
            return self._block_to_clarify(did, session, skips)

        sm.transition(DecisionState.ANALYZING)
        return self._analyze(did, session, skips)

    def respond(self, decision_id: str, request_id: str, response: dict) -> DecisionOutcome:
        """Idempotent: duplicate request_id replays the first post-respond outcome."""
        replay = self._replay.get(request_id)
        if replay is not None:
            return replay
        session = self._sessions.get(decision_id)
        if session is None:
            raise KeyError(f"unknown decision: {decision_id}")
        item = self._pending.get(request_id)
        if item.decision_id != decision_id:
            raise KeyError(f"request {request_id} not under decision {decision_id}")
        from decide_agent.core.workflow.pending import PendingExpiredError

        if item.status.value == "expired":
            raise PendingExpiredError(request_id)  # 红线 13：expired 结构化错误

        if item.status.value == "pending":
            self._pending.respond(request_id, response)
            if item.kind is PendingKind.QUESTION and response.get("value") is not None:
                response = self._resolve_numbered_answer(item.payload, response)
                if item.payload.get("phase") == "clarify":
                    # 澄清应答：先判断是选项清单还是新问题/新需求
                    value = str(response.get("value") or "")
                    options = []
                    for line in value.splitlines():
                        clean = re.sub(r"^[A-Za-z][.、:)：]\s*", "", line.strip())
                        if len(clean) >= 2:
                            options.append(clean)
                    if len(options) < 2:  # 单行 fallback：按顿号/逗号切
                        options = [
                            seg for seg in (s.strip() for s in re.split(r"[、，,;；]+", value))
                            if len(seg.strip()) >= 2
                        ]
                    if options:  # 引导词清洗："来 联想" → "联想"
                        options[0] = re.sub(
                            r"^(?:来|比如|例如|像是|有|包括|就是)\s*", "", options[0],
                        ).strip() or options[0]

                    # 非应答过滤（reject_routes.exact_reject，数据化）："不知道/随便" 不是候选
                    reject_cfg = (
                        self._reject_routes_loader(session.scene or "general")
                        if self._reject_routes_loader else None
                    )
                    exact_reject = set((reject_cfg or {}).get("exact_reject") or [])
                    options = [o for o in options if o not in exact_reject]

                    # 只切出 1 段且含请求语义 → 不是候选清单，是新问题 → 开新决策
                    is_new_question = (
                        len(options) <= 1
                        and re.search(r"帮|帮我买|帮我找|推荐|想买|想找|想要", value)
                    )
                    if is_new_question:
                        # 新问题（如"帮我买个衣服"）→ 开新决策
                        session.candidates = []  # 清空旧候选
                        session.machine.transition(DecisionState.COLLECTING)
                        new_outcome = self.make_decision(DecisionRequest(
                            question=value, interactive=session.request.interactive,
                        ))
                        self._replay[request_id] = new_outcome
                        return new_outcome

                    from decide_agent.schemas.workflow import normalize_candidates

                    session.candidates = normalize_candidates(options)
                    session.collect_disclosed.append(
                        "已识别候选：" + " / ".join(options),
                    )
                    session.machine.transition(DecisionState.ANALYZING)
                    outcome = self._analyze(decision_id, session, [])
                    self._replay[request_id] = outcome
                    return outcome
                if item.payload.get("phase") == "collect":
                    # A拍应答：补充阻塞输入后重新收集
                    slot = item.payload.get("slot")
                    if slot:
                        session.request.slots[slot] = response.get("value")
                        if slot == "location" and self._memory is not None and response.get("value"):
                            # 位置答一次记一辈子：下次自动定位失败时追问直接带「上次位置」选项
                            try:
                                self._memory.remember(
                                    self._owner_id, f"location:{response.get('value')}",
                                    MemoryKind.PREFERENCE, "global",
                                    scope=MemoryScope.USER, source="collect:location",
                                    confidence=0.7, now=self._clock(),
                                )
                            except Exception as exc:  # noqa: BLE001 — 记忆失败不阻断（L4）
                                _log.warning("location memory skipped: %s", exc)
                    session.machine.transition(DecisionState.COLLECTING)
                    if self._collector is not None:
                        collected = self._collector.collect(session.scene, session.request)
                        session.candidates = collected.candidates
                        if collected.env:
                            session.request.env = {**session.request.env, **collected.env}
                        session.collect_disclosed.extend(collected.disclosed)
                    if not session.candidates:
                        return self._block_to_clarify(decision_id, session, [])
                    session.machine.transition(DecisionState.ANALYZING)
                    outcome = self._analyze(decision_id, session, [])
                    self._replay[request_id] = outcome
                    return outcome
                dimension = item.payload.get("dimension")
                if dimension:
                    session.request.slots[dimension] = response.get("value")
                    scope = response.get("scope", "once")
                    session.request.slots[f"{dimension}__scope"] = scope
                    learned = self._learn_from_answer(dimension, response, session)
                    if learned:
                        session.learned_memory.extend(learned)
        # expired → 降级顺延：不合并信息，直接重算（缺口走 missing_information）

        session.machine.transition(DecisionState.ANALYZING)
        outcome = self._analyze(decision_id, session, [])
        self._replay[request_id] = outcome
        return outcome

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _evidence_class(result) -> str:
        """维度证据分级（P8 proof_scope）：结论范围机器可读。"""
        if result is None or not getattr(result, "ok", False):
            return "missing"
        answer = result.answer
        if answer is None or getattr(answer, "degraded", False):
            return "degraded"
        provider = getattr(result, "provider", "") or ""
        if provider == "experience":
            try:
                return "rule-hit" if float(answer.confidence) >= 0.5 else "neutral"
            except (TypeError, ValueError):
                return "neutral"
        return "model-judged"

    @staticmethod
    def _resolve_numbered_answer(payload: dict, response: dict) -> dict:
        """纯序号应答（「1」「选2」）→ 选项原文；非序号/越界原样透传（序号不吞自由输入）。"""
        match = _NUM_REPLY.match(str(response.get("value") or ""))
        options = ((payload.get("question") or {}).get("options")) or []
        if match is None or not options:
            return response
        index = int(match.group(1)) - 1
        if not 0 <= index < len(options):
            return response
        return {**response, "value": str(options[index])}

    def _analyze(self, did: str, session: _Session, skips: list[str]) -> DecisionOutcome:
        request = session.request
        weights = request.weights
        if not weights and self._weights_resolver is not None:
            weights = self._weights_resolver(session.scene or "general")
        user_dims = (
            self._user_dims_resolver(session.scene or "general")
            if self._user_dims_resolver else frozenset()
        )
        fps = session.fingerprint
        missing: set[str] = set()
        ds_by_cand: dict[str, list[DimensionScore]] = {}
        cand_by_id: dict[str, dict] = {}
        disclosed: list[str] = list(session.collect_disclosed)
        budget = self._budgets.get("decide").start() if self._budgets else None

        # 批量打分（P4 官方批量协议）：指纹分流 → 剩余一次批量，结果按 (cid, dim) 回填
        results_map: dict[tuple[str, str], object] = {}
        todo_questions: list[TypedQuestion] = []
        todo_keys: list[tuple[str, str]] = []
        for cand in session.candidates:
            cid = str(cand.get("id", cand.get("name", "")))
            cand_by_id[cid] = cand
            for dim in weights:
                payload = {"dim": dim, "candidate": cand, "env": request.env, "slots": request.slots}
                key = (cid, dim)
                if fps.should_skip(f"score:{cid}:{dim}", payload) and key in session.score_cache:
                    results_map[key] = session.score_cache[key]
                    skips.append(f"score:{cid}:{dim}")
                elif budget is not None and budget.expired():
                    results_map[key] = None
                    disclosed.append("决策时钟预算耗尽，部分维度按中性分处理")
                else:
                    todo_questions.append(TypedQuestion(
                        shape=QuestionShape.SCORE, scene=session.scene or "general",
                        dimension=dim,
                        context={"candidate": cand, "env": request.env, "slots": request.slots},
                    ))
                    todo_keys.append(key)
                fps.update(f"score:{cid}:{dim}", payload)
        if todo_questions:
            if budget is not None and budget.expired():
                for key in todo_keys:
                    results_map[key] = None
                disclosed.append("决策时钟预算耗尽，部分维度按中性分处理")
            else:
                for key, result in zip(
                    todo_keys, self._engine.score_questions(todo_questions, state=request.question),
                ):
                    results_map[key] = result
                    session.score_cache[key] = result

        for cand in session.candidates:
            cid = str(cand.get("id", cand.get("name", "")))
            cand_by_id[cid] = cand
            ds: list[DimensionScore] = []
            for dim in weights:
                result = results_map.get((cid, dim))
                if result is None or not getattr(result, "ok", False):
                    missing.add(dim)
                    ds.append(DimensionScore(dimension=dim, score=0.5, weight=weights[dim]))
                    continue
                answer = result.answer
                value = float(answer.value) if isinstance(answer.value, int | float) else 0.5
                if dim in user_dims and dim not in request.slots:
                    missing.add(dim)  # 模板声明需用户输入的维度：槽位缺失即缺信息（P4 真实模式）
                elif answer.degraded:
                    missing.add(dim)
                ds.append(DimensionScore(
                    dimension=dim, score=round(value, 3), weight=weights[dim], reason=answer.basis,
                ))
            ds_by_cand[cid] = ds

        totals = {cid: synthesize(ds) for cid, ds in ds_by_cand.items()}
        ranked = sorted(totals, key=totals.get, reverse=True)  # type: ignore[arg-type]

        # 帕累托前沿过滤（淘汰被支配候选）
        frontier_ids, dominated_ids = pareto_filter(session.candidates, ds_by_cand)
        ranked = [cid for cid in ranked if cid in frontier_ids]

        # proof_scope（P8）：胜出候选各维度的证据分级——结论不超过证据范围
        evidence_scope: dict[str, str] = {}
        if ranked:
            rec_cid = ranked[0]
            for ds_item in ds_by_cand[rec_cid]:
                evidence_scope[ds_item.dimension] = self._evidence_class(
                    results_map.get((rec_cid, ds_item.dimension)))

        from decide_agent.core.decision.gating import evaluate

        verdict = evaluate(sorted(missing), len(session.candidates), self._threshold, self._penalty)
        flips = dict(flip_probabilities(ds_by_cand, weights, sorted(missing)))
        risky = [dim for dim, p in flips.items() if p >= 0.5]  # P4-3 实算：翻车概率阈值
        if len(risky) > 1 and self._question_resolver is not None:
            # 概率并列（如全平局）时，优先问模板声明过话术的维度（问得有意义）
            risky.sort(key=lambda d: 0 if self._question_resolver(session.scene or "general", d) else 1)
        disclosed += [f"维度 {d} 信息缺失，按中性分处理" for d in sorted(missing)]

        # 重大决策免责（红线 21）
        _RISK_KEYWORDS = ("医疗", "法律", "投资", "手术", "理财", "保险", "股票", "基金")
        if any(kw in (request.question or "") for kw in _RISK_KEYWORDS):
            disclosed.append(
                "⚠ 以上建议仅供参考，不构成专业意见。涉及重大决策请咨询专业人士。"
            )

        # ⑤ 询问机制（L5）：gate 不自信 或 沙盒翻车 → interactive 且预算内才问
        want_ask = verdict.should_ask or bool(risky)
        sm = session.machine
        if want_ask and request.interactive and session.ask_left > 0 \
                and (risky or verdict.missing_dimensions):  # 双空（候选数罚分触发）→ 不追问，静默降级
            dimension = risky[0] if risky else verdict.missing_dimensions[0]
            session.ask_left -= 1.0
            sm.transition(DecisionState.INPUT_REQUIRED)  # B拍：分析后提问，直接挂起
            ask_reason = (
                f"沙盒翻车概率 {flips.get(dimension, 0.0):.0%}" if dimension in flips
                else "该维度缺少偏好信息"
            )
            template = (
                self._question_resolver(session.scene, dimension)
                if self._question_resolver else None
            ) or {}
            options = [str(o) for o in template.get("options", [])]
            recommended = None
            for value in self._memory_dimension_values(f"{dimension}:"):  # 经验参谋：历史偏好建议
                match = next((o for o in options if o == value or value in o or o in value), None)
                if match is not None:
                    recommended = match
                    break
            pending = self._pending.create(
                did, PendingKind.QUESTION,
                payload={
                    "dimension": dimension,
                    "flip_probability": flips.get(dimension, 0.0),
                    "question": Question(
                        text=str(template.get("text") or f"关于「{dimension}」你更在意什么？"),
                        options=options,
                        recommended=recommended,
                        allow_free_text=bool(template.get("allow_free_text", True)),
                        reason=ask_reason,
                    ).model_dump(),
                    "scope_choices": ["once", "session", "always"],
                },
                timeout_s=self._question_timeout_s,
                budget_cost=1.0,
                clock=self._clock(),
            )
            return DecisionOutcome(
                decision_id=did, status="require_action", state=sm.state.value,
                pending=pending, missing_information=disclosed,
                fingerprint_skips=skips, budget_left=session.ask_left,
                learned_memory=list(session.learned_memory),
            )

        # ⑥ 静默降级 → completed（narrator P1-6 接管呈现）
        recs = [
            Recommendation(
                candidate=_to_candidate(cid, cand_by_id),
                total_score=totals[cid],
                dimension_scores=ds_by_cand[cid],
                reason=f"综合得分 {totals[cid]}",
            )
            for cid in ranked[:3]
        ]

        # Reflexion 自省：检查首选在高权重维度上是否有明显不匹配（低分 + 依据含「不匹配」双条件，
        # 防止 provider 垃圾 basis / 量尺回显误触发高分「不匹配」）
        if recs:
            mismatches = [
                d for d in recs[0].dimension_scores
                if d.reason and "不匹配" in d.reason and d.weight >= 0.3 and d.score < 0.45
            ]
            if mismatches:
                disclosed.append(
                    "注意：首选候选在高权重维度存在不匹配（"
                    + "、".join(d.dimension for d in mismatches)
                    + "），建议权衡后再做决定"
                )

        if dominated_ids:
            disclosed.append(
                f"帕累托前沿：已排除 {len(dominated_ids)} 个被支配候选"
                f"（存在每维均不优于其他候选的选项）"
            )
        sm.transition(DecisionState.SUGGESTED)
        result = DecisionResult(
            scene=session.scene or "general",
            recommendation=recs[0] if recs else None,
            alternatives=recs[1:3],
            confidence=verdict.confidence,
            filtered_out=[],
            evidence_scope=evidence_scope,
        )
        return DecisionOutcome(
            decision_id=did, status="completed", state=sm.state.value,
            result=result, missing_information=disclosed,
            fingerprint_skips=skips, budget_left=session.ask_left,
            learned_memory=list(session.learned_memory),
        )

    def _learn_from_answer(self, dimension: str, response: dict, session: _Session) -> list[dict]:
        """scope session/always 的应答落记忆（P2-5）。

        第三方决策（帮朋友/同事/xxx）：偏好属于对方 → 只进临时记忆（ephemeral，
        不落用户长期层）——防止用户记忆被他人偏好污染（隐私+正确性）。
        """
        if self._memory is None or response.get("scope") not in ("session", "always"):
            return []
        if session.joint_with:
            return []  # 共同决策偏好学习暂缓（v2）：归属不清（我的还是 ta 的）
        if session.subject:
            # 第三方偏好 → 记在对象名下（独立命名空间，下次帮同一位复用）
            if self._subject_memory_factory is None:
                return []
            svc = self._subject_memory_factory(session.subject)
            record = svc.remember(
                session.subject,
                f"{dimension}:{response.get('value')}",
                MemoryKind.PREFERENCE,
                session.scene or "global",
                scope=MemoryScope.USER,
                source="feedback:third_party",
                confidence=0.7,
                now=self._clock(),
            )
            return [record.model_dump(mode="json")] if record else []
        record = self._memory.remember(
            self._owner_id,
            f"{dimension}:{response.get('value')}",
            MemoryKind.PREFERENCE,
            session.scene or "global",
            scope=MemoryScope.SESSION if response.get("scope") == "session" else MemoryScope.USER,
            source="feedback",
            confidence=0.7,
            now=self._clock(),
        )
        return [record.model_dump(mode="json")] if record else []

    def _chat_reply(self, did: str, session: _Session, skips: list[str], confidence: float) -> DecisionOutcome:
        """非决策闲聊 → 友好回应（narrator 按 scene=chat 渲染），不追问、不评分。"""
        sm = session.machine
        sm.transition(DecisionState.ANALYZING)
        sm.transition(DecisionState.SUGGESTED)
        return DecisionOutcome(
            decision_id=did, status="completed", state=sm.state.value,
            result=DecisionResult(scene=CHAT_SCENE, confidence=confidence),
            missing_information=[], fingerprint_skips=skips,
            budget_left=session.ask_left, learned_memory=list(session.learned_memory),
        )

    # 澄清示例按场景给（通用兜底走中性示例；P0 遗留的笔记本示例只适用电子消费）
    _CLARIFY_EXAMPLES: ClassVar[dict[str, str]] = {
        "food": "火锅、日料、烧烤",
        "travel": "科技馆、植物园、爬山",
        "exam": "清华、北大、复旦",
        "civil-service": "税务、公安、海关",
        "general": "咖啡、书店、电影",
    }

    def _memory_dimension_values(self, prefix: str) -> list[str]:
        """经验参谋：从用户记忆召回某维度/槽位的历史取值（作追问建议项，非门槛）。"""
        if self._memory is None:
            return []
        try:
            records = self._memory.export(self._owner_id)
        except Exception as exc:  # noqa: BLE001 — 参谋缺位不阻断（L4）
            _log.warning("memory recommend skipped: %s", exc)
            return []
        values: list[str] = []
        for record in records:
            record = record.model_dump(mode="json") if hasattr(record, "model_dump") else record
            if str(record.get("status", "active")) != "active":
                continue
            content = str(record.get("content", ""))
            if content.startswith(prefix):
                value = content[len(prefix):].strip()
                if value and value not in values:
                    values.append(value)
        return values

    def _block_to_clarify(self, did: str, session: _Session, skips: list[str]) -> DecisionOutcome:
        """候选空 → block_to_clarify（降级四策略唯一硬结局）。"""
        sm = session.machine
        if session.request.interactive:
            sm.transition(DecisionState.ASKING)
            scene = session.scene or "general"
            if scene == "general":
                # general = 无模板场景：缺的是品类/选项 → 告知可自动生成候选，别硬要清单
                text = ("告诉我你想选的品类和大致需求（如：平板电脑、办公用、预算两千），"
                        "我可以生成候选帮你对比；如果只是想提问或闲聊，直接说也行。")
            else:
                example = self._CLARIFY_EXAMPLES.get(scene, "选项1、选项2")
                text = f"没有找到可比较的候选，请提供你的选项（用顿号或逗号分隔，如：{example}）"
            pending = self._pending.create(
                did, PendingKind.QUESTION,
                payload={"clarify": True, "phase": "clarify", "question": Question(
                    text=text,
                    allow_free_text=True, reason="候选为空，无法决策",
                ).model_dump()},
                timeout_s=self._question_timeout_s, budget_cost=1.0, clock=self._clock(),
            )
            sm.transition(DecisionState.INPUT_REQUIRED)
            return DecisionOutcome(
                decision_id=did, status="require_action", state=sm.state.value,
                pending=pending, budget_left=session.ask_left, fingerprint_skips=skips,
            )
        sm.transition(DecisionState.ANALYZING)
        sm.transition(DecisionState.SUGGESTED)
        return DecisionOutcome(
            decision_id=did, status="completed", state=sm.state.value,
            result=DecisionResult(scene=session.scene or "general", confidence=0.0),
            missing_information=["候选为空，无法给出推荐"], fingerprint_skips=skips,
        )


def _top_label(classify_result) -> str | None:
    if not classify_result.ok:
        return None
    dist = classify_result.answer.distribution or {}
    if not dist:
        return None
    return max(dist, key=dist.get)  # type: ignore[arg-type,return-value]


def _to_candidate(cid: str, cand_by_id: dict[str, dict]) -> Candidate:
    raw = cand_by_id.get(cid, {})
    try:
        return Candidate.model_validate(raw)
    except Exception:  # noqa: BLE001 — caller dicts are arbitrary; degrade to minimal candidate
        return Candidate(id=cid, name=str(raw.get("name", cid)))
