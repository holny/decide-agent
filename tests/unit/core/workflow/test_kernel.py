"""Kernel tests: 指纹续算 + 挂起恢复幂等 (P1-4 gate) + 生命周期编排."""
import json
from pathlib import Path

from decide_agent.core.decision.chain import ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.memory.manager import MemoryService, build_memory_store
from decide_agent.core.workflow.kernel import DecisionKernel
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.schemas.memory import MemoryKind
from decide_agent.schemas.workflow import DecisionRequest

REPO_SKILLS = Path(__file__).resolve().parents[4] / "skills"

WEIGHTS = {
    "taste_match": 0.35, "distance": 0.20, "price": 0.20, "queue": 0.15, "weather_fit": 0.10,
}
SHU = {"id": "蜀香居", "name": "蜀香居", "tags": ["辣"], "distance_m": 500,
       "price_per_person": 65, "wait_min": 10}
LA_MEI = {"id": "辣妹子", "name": "辣妹子", "tags": ["辣"], "distance_m": 900,
          "price_per_person": 55, "wait_min": 20}


def kernel(**kw) -> DecisionKernel:
    engine = DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])]))
    defaults = {
        "weights_resolver": lambda s: (
            {"fit": 0.3, "price": 0.3, "quality": 0.25, "service": 0.15}
            if s == "general" else WEIGHTS
        ),
        "gate_threshold": 0.7,
        "question_resolver": lambda s, d: {"text": "x"} if d == "preference_match" else None,
    }
    defaults.update(kw)
    return DecisionKernel(engine, **defaults)


def request(**kw) -> DecisionRequest:
    base: dict = {
        "question": "想吃辣", "scene_hint": "food", "candidates": [SHU, LA_MEI],
        "env": {"weather": "小雨"}, "slots": {"taste_match": "辣"}, "weights": WEIGHTS,
        "interactive": False, "ask_budget": 1.0,
    }
    base.update(kw)
    return DecisionRequest(**base)


def test_completed_flow_golden_ranking():
    outcome = kernel().make_decision(request())
    assert outcome.status == "completed"
    assert outcome.state == "suggested"
    assert outcome.result.recommendation.candidate.id == "蜀香居"
    assert outcome.result.recommendation.total_score == 0.8258  # golden: rain, 500m, 65元, 10min
    assert outcome.result.confidence == 0.9  # no missing, 2 candidates -> -0.1
    # 帕累托披露可能有也可能没有（取决于候选数据）  # 无帕累托排除是正常的（2候选无被支配）
    assert outcome.pending is None


def test_low_confidence_interactive_asks_question():
    outcome = kernel().make_decision(
        request(slots={}, interactive=True),
    )
    assert outcome.status == "require_action"
    assert outcome.state == "input-required"
    pending = outcome.pending
    assert pending.kind == "question"
    assert pending.payload["dimension"] == "taste_match"
    assert pending.budget_cost == 1.0
    assert outcome.budget_left == 0.0


def test_non_interactive_missing_slots_silent_degrade():
    outcome = kernel().make_decision(request(slots={}))
    assert outcome.status == "completed"  # 静默降级，永不挂起（宿主一行接入承诺）
    assert any("taste_match" in line for line in outcome.missing_information)


def test_respond_merges_slots_and_completes():
    k = kernel()
    asked = k.make_decision(request(slots={}, interactive=True))
    assert asked.status == "require_action"
    final = k.respond(asked.decision_id, asked.pending.request_id, {"value": "辣", "scope": "once"})
    assert final.status == "completed"
    assert final.result.recommendation.candidate.id == "蜀香居"
    assert any('帕累托' in m for m in final.missing_information)  # 帕累托披露是有信息量的  # taste now scored 0.9, no longer missing
    assert final.fingerprint_skips == []  # slots changed -> re-scored, nothing reused


def test_respond_idempotent_replay():
    k = kernel()
    asked = k.make_decision(request(slots={}, interactive=True))
    first = k.respond(asked.decision_id, asked.pending.request_id, {"value": "辣"})
    replay = k.respond(asked.decision_id, asked.pending.request_id, {"value": "辣"})
    assert replay.model_dump() == first.model_dump()  # 重复应答：原样重放（幂等去重）


def test_fingerprint_skip_on_identical_reanalysis():
    k = kernel()
    asked = k.make_decision(request(slots={}, interactive=True))
    # respond with no usable value -> no slot merge -> identical stage payloads -> all Skip
    outcome = k.respond(asked.decision_id, asked.pending.request_id, {"value": None})
    assert outcome.status == "completed"
    assert len(outcome.fingerprint_skips) == 10  # 2 candidates x 5 dims reused


def test_ask_budget_exhausted_silent_degrade():
    outcome = kernel().make_decision(
        request(slots={}, interactive=True, ask_budget=0.0),
    )
    assert outcome.status == "completed"  # 预算尽 -> 静默降级同路径
    assert any("taste_match" in line for line in outcome.missing_information)


def test_empty_candidates_block_to_clarify():
    asked = kernel().make_decision(request(candidates=[], interactive=True))
    assert asked.status == "require_action"
    assert asked.pending.payload.get("clarify") is True

    silent = kernel().make_decision(request(candidates=[], interactive=False))
    assert silent.status == "completed"
    assert any("候选为空" in line for line in silent.missing_information)


def test_unknown_scene_falls_to_general_and_degrades():
    outcome = kernel().make_decision(request(scene_hint="no_such_scene"))
    assert outcome.status == "completed"  # rules missing -> all neutral, no crash (L4)
    assert len(outcome.missing_information) == 5  # all dims disclosed
    assert outcome.result.recommendation is not None  # still ranks (all 0.5 -> tie order)


def test_user_provided_options_general_mode():
    """§10.2③：用户直供选项 → general 模式 → collect 跳过 → 仍完整出推荐。"""
    k = kernel()
    outcome = k.make_decision(request(
        scene_hint="general",
        weights={"preference_match": 0.3, "risk": 0.25, "cost": 0.25, "feasibility": 0.2},
        candidates=["咖啡馆", "书店", "公园"],
        slots={"preference_match": "安静"},
    ))
    assert outcome.status == "completed"
    # 候选未被 collect 覆盖（用户给的三个原样保留）
    assert outcome.result.recommendation.candidate.id in {"咖啡馆", "书店", "公园"}
    assert len(outcome.result.alternatives) == 2


def test_user_options_missing_preference_triggers_ask():
    """全中性=全平局 → 任何偏好都翻转排序（p≈1）→ L5 追问「你更看重什么」是对的。"""
    k = kernel()
    outcome = k.make_decision(request(
        scene_hint="general", interactive=True,
        weights={"preference_match": 0.3, "risk": 0.25, "cost": 0.25, "feasibility": 0.2},
        candidates=["咖啡馆", "书店"],
    ))
    assert outcome.status == "require_action"
    assert outcome.pending.payload["dimension"] == "preference_match"
    assert outcome.pending.payload["flip_probability"] > 0.5  # 全平局 → 必翻


def test_third_party_memory_persists_under_subject_and_reused(tmp_path):
    """帮朋友：偏好记在朋友名下（subject 命名空间）→ 下次帮同一位复用，不再追问。"""
    import json

    from decide_agent.core.memory.store import JsonMemoryStore

    def subject_factory(subject: str):
        store = JsonMemoryStore(
            tmp_path / "evolved" / "users" / "u1" / "subjects" / subject / "memory.json")
        return MemoryService(store)

    memory = MemoryService(build_memory_store(tmp_path / "evolved", "u1"))
    k1 = DecisionKernel(
        DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])])),
        memory=memory, owner_id="u1",
        subject_memory_factory=subject_factory,
        weights_resolver=lambda s: WEIGHTS, gate_threshold=0.7,
    )
    cands = [
        {"id": "a", "name": "蜀香居", "tags": ["辣"], "distance_m": 500,
         "price_per_person": 65, "wait_min": 10},
        {"id": "b", "name": "清淡居", "tags": ["清淡"], "distance_m": 300,
         "price_per_person": 30, "wait_min": 5},
    ]
    asked = k1.make_decision(DecisionRequest(
        question="帮我朋友选餐厅", scene_hint="food",
        interactive=True, slots={}, candidates=cands,
    ))
    assert asked.status == "require_action"  # 不知道朋友口味 → 追问
    final = k1.respond(asked.decision_id, asked.pending.request_id,
                       {"value": "辣", "scope": "always"})
    assert final.status == "completed"
    assert final.learned_memory[0]["scope"] == "user"  # 记在朋友名下（subject 命名空间内长期）
    assert final.learned_memory[0]["source"] == "feedback:third_party"

    # 朋友记忆落在 subject 命名空间文件
    subject_file = (tmp_path / "evolved" / "users" / "u1"
                    / "subjects" / "朋友" / "memory.json")
    assert subject_file.exists()
    stored = json.loads(subject_file.read_text("utf-8"))["records"]
    assert any(r["content"] == "taste_match:辣" for r in stored)

    # 用户自己的 memory.json 零污染
    user_records = memory.export("u1")
    assert all("taste_match" not in r.content for r in user_records)

    # ★ 复用：新内核实例（重启语义）帮同一位朋友 → slots 已播种 → 不追问直接完成
    k2 = DecisionKernel(
        DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])])),
        memory=memory, owner_id="u1",
        subject_memory_factory=subject_factory,
        weights_resolver=lambda s: WEIGHTS, gate_threshold=0.7,
    )
    again = k2.make_decision(DecisionRequest(
        question="帮我朋友选餐厅", scene_hint="food",
        interactive=True, slots={}, candidates=cands,
    ))
    assert again.status == "completed"  # 朋友的口味已记住 → 不再追问
    assert again.result.recommendation.candidate.id == "a"  # 辣 → 蜀香居

    # 换一位对象（妈）→ 命名空间隔离 → 仍需追问
    k3 = DecisionKernel(
        DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])])),
        memory=memory, owner_id="u1",
        subject_memory_factory=subject_factory,
        weights_resolver=lambda s: WEIGHTS, gate_threshold=0.7,
    )
    for_mom = k3.make_decision(DecisionRequest(
        question="帮我妈选餐厅", scene_hint="food",
        interactive=True, slots={}, candidates=cands,
    ))
    assert for_mom.status == "require_action"  # 妈的口味独立，不复用朋友的


def test_joint_decision_dual_memory_with_conflict(tmp_path):
    """帮我和xx：双方记忆召回 + 冲突维度中性披露 + 用户层不误写。"""
    from decide_agent.core.memory.manager import MemoryService, build_memory_store

    def factory(subject: str):
        from decide_agent.core.memory.store import JsonMemoryStore

        return MemoryService(
            JsonMemoryStore(tmp_path / "evolved" / "users" / "u1" / "subjects" / subject / "memory.json"))

    # 预置：用户偏好辣；女朋友偏好清淡
    user_mem = MemoryService(build_memory_store(tmp_path / "evolved", "u1"))
    user_mem.remember("u1", "taste_match:辣", MemoryKind.PREFERENCE, "food",
                      source="declare", confidence=0.9, now=1.0)
    gf_mem = factory("女朋友")
    gf_mem.remember("女朋友", "taste_match:清淡", MemoryKind.PREFERENCE, "food",
                    source="declare", confidence=0.9, now=1.0)

    k = DecisionKernel(
        DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])])),
        memory=user_mem, owner_id="u1",
        subject_memory_factory=factory,
        weights_resolver=lambda s: WEIGHTS, gate_threshold=0.7,
    )
    outcome = k.make_decision(DecisionRequest(
        question="帮我和女朋友选餐厅", scene_hint="food",
        interactive=True, candidates=[
            {"id": "a", "name": "蜀香居", "tags": ["辣"], "distance_m": 500,
             "price_per_person": 65, "wait_min": 10},
            {"id": "b", "name": "清淡居", "tags": ["清淡"], "distance_m": 300,
             "price_per_person": 30, "wait_min": 5},
        ],
    ))
    assert any("你与女朋友" in m and "不一致" in m for m in outcome.missing_information)
    # 冲突学习暂缓：joint 决策不新增写入（预置的 1 条声明记忆除外）
    assert len(user_mem.export("u1")) == 1  # 零新增（v2 前学习暂缓，诚实标注）

    # 无冲突 joint（偏好一致）→ 正常计入
    gf_mem.remember("女朋友", "taste_match:辣", MemoryKind.PREFERENCE, "food",
                    source="declare", confidence=0.9, now=2.0)
    outcome2 = k.make_decision(DecisionRequest(
        question="帮我和女朋友选火锅", scene_hint="food",
        interactive=True, candidates=[
            {"id": "a", "name": "蜀香居", "tags": ["辣"], "distance_m": 500,
             "price_per_person": 65, "wait_min": 10},
            {"id": "b", "name": "清汤居", "tags": ["清淡"], "distance_m": 300,
             "price_per_person": 30, "wait_min": 5},
        ],
    ))
    assert not any("不一致" in m for m in outcome2.missing_information)


def test_clarify_then_user_options_complete(tmp_path):
    """你实测暴露的缺口：澄清 → 用户给「联想、戴尔、苹果」→ 切分候选 → 完成推荐。"""
    memory = MemoryService(build_memory_store(tmp_path / "evolved", "u1"))
    k = DecisionKernel(
        DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])])),
        memory=memory, owner_id="u1",
        weights_resolver=lambda s: {
            "preference_match": 0.4, "risk": 0.3, "cost": 0.3,
        },
        question_resolver=lambda s, d: {"text": "你更看重什么？"} if d == "preference_match" else None,
        gate_threshold=0.7,
    )
    asked = k.make_decision(DecisionRequest(
        question="我想买个电脑", scene_hint="general", interactive=True,
        weights={"preference_match": 0.4, "risk": 0.3, "cost": 0.3},
        candidates=[],  # 候选空 → 澄清
    ))
    assert asked.status == "require_action"
    assert asked.pending.payload["clarify"] is True

    mid = k.respond(asked.decision_id, asked.pending.request_id,
                    {"value": "来  联想、戴尔、苹果"})
    # 通用模式维度全中性 → L5 追问偏好（第三轮）
    assert mid.status == "require_action"
    assert mid.pending.payload["dimension"] == "preference_match"

    final = k.respond(mid.decision_id, mid.pending.request_id,
                      {"value": "性价比高"})
    assert final.status == "completed"  # 预算尽 → 静默收尾
    assert final.result.recommendation.candidate.id == "联想"  # 切分后首候选
    assert len(final.result.alternatives) == 2  # 戴尔、苹果


def test_non_answer_not_treated_as_candidate(tmp_path):
    """「不知道」不该变成候选——澄清后用户说不知道 → 不硬推。"""
    k = kernel()
    asked = k.make_decision(DecisionRequest(
        question="帮我买个礼物", scene_hint="general", interactive=True,
        weights={"preference_match": 0.3, "risk": 0.25, "cost": 0.25, "feasibility": 0.2},
        candidates=[],
    ))
    assert asked.status == "require_action"  # 澄清
    final = k.respond(asked.decision_id, asked.pending.request_id,
                      {"value": "不知道"})
    # 不应该把"不知道"当候选推荐出来
    if final.result and final.result.recommendation:
        assert final.result.recommendation.candidate.name != "不知道"


def test_chat_input_short_circuits_no_candidates_ask():
    """非决策闲聊（chat 场景）→ 短路边 completed，不追问候选。"""
    k = kernel(scenes=["food", "general", "chat"])
    for text in ["你好", "你和claude code哪个厉害", "谢谢", "我是谁"]:
        outcome = k.make_decision(DecisionRequest(
            question=text, interactive=True, candidates=[],
        ))
        assert outcome.status == "completed", text
        assert outcome.result.scene == "chat", text
        assert outcome.result.confidence >= 0.5, text
        assert outcome.pending is None, text


def test_chat_scene_not_shadow_real_decision():
    """真决策输入不被 chat 关键词截胡：food 命中优先于 chat。"""
    k = kernel(scenes=["food", "general", "chat"])
    outcome = k.make_decision(DecisionRequest(
        question="想吃辣", scene_hint=None, interactive=False,
        candidates=[SHU, LA_MEI], env={"weather": "小雨"},
        slots={"taste_match": "辣"}, weights=WEIGHTS,
    ))
    assert outcome.status == "completed"
    assert outcome.result.scene != "chat"
    assert outcome.result.recommendation is not None


def test_chat_reply_text_rendering():
    """chat 短路边 text 渲染 → 友好回应而非「未能给出推荐」。"""
    from decide_agent.core.narrator.renderer import OutputFormat, render

    k = kernel(scenes=["food", "general", "chat"])
    outcome = k.make_decision(DecisionRequest(
        question="你好", interactive=False, candidates=[],
    ))
    text = render(outcome, OutputFormat.TEXT)
    assert "决策" in text
    assert "未能给出推荐" not in text


def test_numbered_reply_maps_to_option_text():
    """渲染层编号选项 ↔ 输入侧序号应答：回「1」= 第一个选项原文。"""
    from decide_agent.core.narrator.renderer import OutputFormat, render

    resolve = DecisionKernel._resolve_numbered_answer
    payload = {"question": {"options": ["自然", "人文", "亲子"]}}
    assert resolve(payload, {"value": "2"})["value"] == "人文"
    assert resolve(payload, {"value": "选3"})["value"] == "亲子"
    assert resolve(payload, {"value": "9"})["value"] == "9"  # 越界透传
    assert resolve(payload, {"value": "想吃辣"})["value"] == "想吃辣"  # 自由文本透传
    assert resolve({"question": {}}, {"value": "1"})["value"] == "1"  # 无选项透传

    from decide_agent.core.memory.manager import MemoryService, build_memory_store
    from decide_agent.schemas.memory import MemoryScope

    memory = MemoryService(build_memory_store(__import__("pathlib").Path(
        __import__("tempfile").mkdtemp()), "local"))
    memory.remember("local", "taste_match:辣", MemoryKind.PREFERENCE, "food",
                    scope=MemoryScope.USER, source="test", confidence=0.7, now=1.0)
    k = kernel(question_resolver=lambda s, d: (
        {"text": "偏好口味？", "options": ["辣", "清淡"]} if d == "taste_match" else None),
        memory=memory, owner_id="local")
    asked = k.make_decision(request(slots={}, interactive=True))
    assert asked.status == "require_action"
    text = render(asked, OutputFormat.TEXT)
    assert "1. 辣" in text and "2. 清淡" in text
    assert "建议：辣" in text  # 经验参谋：历史偏好 → 建议项渲染
    final = k.respond(asked.decision_id, asked.pending.request_id, {"value": "1"})
    assert final.status == "completed"
    assert final.result.recommendation.candidate.id == "蜀香居"  # 序号1=辣 → 与 golden 一致


def test_clarify_example_scene_aware():
    """澄清示例随场景：travel 提景点，general 中性——不再出现写死的笔记本品牌。"""
    k = kernel()
    asked_travel = k.make_decision(DecisionRequest(
        question="周末去哪玩", scene_hint="travel", interactive=True, candidates=[],
    ))
    assert asked_travel.status == "require_action"
    assert "科技馆" in asked_travel.pending.payload["question"]["text"]

    asked_general = k.make_decision(DecisionRequest(
        question="帮我选一个", scene_hint="general", interactive=True, candidates=[],
    ))
    text = asked_general.pending.payload["question"]["text"]
    assert "联想" not in text  # P0 遗留的笔记本示例已移除
    assert "品类" in text and "生成候选" in text  # general：可自动生成候选


def test_general_clarify_confirms_intent():
    """general 澄清先确认意图（提问/闲聊 vs 对比选择），不硬要候选。"""
    k = kernel(scenes=["food", "general", "chat"])
    asked = k.make_decision(DecisionRequest(
        question="帮我选一个", scene_hint=None, interactive=True, candidates=[],
    ))
    assert asked.status == "require_action"
    text = asked.pending.payload["question"]["text"]
    assert "品类" in text  # 确认式话术：告知可生成候选
    assert "闲聊" in text  # 但决策路径仍清楚可达


def test_entry_intent_low_confidence_returns_none():
    """experience 层对意图伪场景无关键词（全 0.2）→ 低把握 → None（安全按新决策处理）。"""
    k = kernel(scenes=["food", "general", "chat"])
    assert k.entry_intent("周末去哪玩") is None


def test_keep_by_feedback_filter_predicates():
    keep = DecisionKernel._keep_by_feedback
    cand = {"name": "市博物馆", "tags": ["人文", "古迹"], "price_per_person": 30.0, "distance_m": 800}
    assert not keep(cand, {"exclude": ["博物馆"]})  # 名称排除
    assert not keep(cand, {"exclude": ["古迹"]})  # 标签排除
    assert not keep(cand, {"max_price": 20})  # 价格上限
    assert not keep(cand, {"max_distance_m": 500})  # 距离上限
    assert keep(cand, {"exclude": ["游乐场"], "max_price": 50, "max_distance_m": 2000})
    assert keep({"name": "科技馆", "tags": []}, {"max_price": None})  # 无价格数据不误杀


def test_give_feedback_unextractable_discloses_and_reanalyzes():
    """反馈抽不出可执行约束 → 不假装变推荐，披露说明且重算不崩（fingerprint skip 零成本）。"""
    k = kernel()
    first = k.make_decision(request())
    assert first.status == "completed" and first.result.recommendation is not None

    second = k.give_feedback(first.decision_id, "不想去博物馆")  # experience 规则抽不出过滤约束
    assert second.status == "completed"
    assert second.result.recommendation.candidate.id == "蜀香居"
    assert any("未能从反馈中识别" in line for line in second.missing_information)


def test_give_feedback_constraint_without_effect_discloses():
    """约束与候选数据无交集 → 明说未筛掉，不静默。"""
    k = kernel()
    first = k.make_decision(request())
    second = k.give_feedback(first.decision_id, "预算50")  # experience 抽出 budget，但过滤键不认识
    assert second.status == "completed"
    assert any("未筛掉候选" in line for line in second.missing_information)


def test_a拍_location_options_from_memory(tmp_path):
    """自动定位失败 → A拍追问带「上次位置」选项+建议项（经验参谋）。"""
    from decide_agent.core.memory.manager import MemoryService, build_memory_store
    from decide_agent.schemas.collect import CollectorOutcome
    from decide_agent.schemas.memory import MemoryScope

    memory = MemoryService(build_memory_store(tmp_path, "local"))
    memory.remember("local", "location:合肥", MemoryKind.PREFERENCE, "global",
                    scope=MemoryScope.USER, source="test", confidence=0.7, now=1.0)

    class StubCollector:
        def collect(self, scene, request, skip=None):
            return CollectorOutcome(needs=[{
                "phase": "collect", "capability": "location", "slot": "location",
                "purpose": "用户位置",
                "question": {"text": "请提供用户位置信息", "allow_free_text": True},
            }])

    k = DecisionKernel(
        DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])])),
        collector=StubCollector(), memory=memory, owner_id="local",
        weights_resolver=lambda s: WEIGHTS, gate_threshold=0.7,
        scenes=["food", "travel", "general", "chat"],
    )
    asked = k.make_decision(DecisionRequest(
        question="周末去哪玩", scene_hint="travel", interactive=True, candidates=[],
    ))
    q = asked.pending.payload["question"]
    assert "合肥" in q["options"]  # 上次位置成为选项
    assert q["recommended"] == "合肥"  # 建议项

    # 应答后记忆回写（下次继续带选项）
    k.respond(asked.decision_id, asked.pending.request_id, {"value": "上海"})
    contents = [r.content for r in memory.export("local")]
    assert any(c == "location:上海" for c in contents)


def test_b拍_recommended_from_memory():
    """经验参谋：历史口味偏好 → B拍问题带建议项。"""
    from decide_agent.core.memory.manager import MemoryService, build_memory_store
    from decide_agent.schemas.memory import MemoryScope

    memory = MemoryService(build_memory_store(__import__("pathlib").Path(
        __import__("tempfile").mkdtemp()), "local"))
    memory.remember("local", "taste_match:辣", MemoryKind.PREFERENCE, "food",
                    scope=MemoryScope.USER, source="test", confidence=0.7, now=1.0)

    k = kernel(question_resolver=lambda s, d: (
        {"text": "偏好口味？", "options": ["辣", "清淡"]} if d == "taste_match" else None),
        memory=memory, owner_id="local",
    )
    asked = k.make_decision(request(slots={}, interactive=True))
    q = asked.pending.payload["question"]
    assert q["recommended"] == "辣"


class RubricEngine(DecisionEngine):
    """extract 按脚本返回动态维度（其余形状走真实链）。"""

    def __init__(self, chain, rubric):
        super().__init__(chain)
        self._rubric = rubric

    def extract(self, scene, text, slot):
        from decide_agent.core.decision.chain import ChainResult
        from decide_agent.schemas.question import QuestionShape, TypedAnswer

        if slot == "dimensions":
            return ChainResult(answer=TypedAnswer(
                shape=QuestionShape.EXTRACT, value=json.dumps(self._rubric), confidence=0.9))
        return super().extract(scene, text, slot)


def test_general_dynamic_dimensions_inferred_from_context():
    """general 无模板场景 → 模型按语境推断维度（送书没有价格维度），权重归一化。"""

    chain = ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])])
    engine = RubricEngine(chain, [
        {"name": "内容质量", "weight": 0.4},
        {"name": "对方兴趣", "weight": 0.4},
        {"name": "心意表达", "weight": 0.2},
    ])
    k = DecisionKernel(engine, weights_resolver=lambda s: {
        "fit": 0.3, "price": 0.3, "quality": 0.25, "service": 0.15},
        gate_threshold=0.7, scenes=["food", "general", "chat"])
    outcome = k.make_decision(DecisionRequest(
        question="帮我选一本书送朋友", scene_hint="general", interactive=False,
        candidates=[{"id": "书A", "name": "书A"}, {"id": "书B", "name": "书B"}],
    ))
    dims = {d.dimension for d in outcome.result.recommendation.dimension_scores}
    assert dims == {"内容质量", "对方兴趣", "心意表达"}  # 模板维度被动态维度替换
    total = sum(d.weight for d in outcome.result.recommendation.dimension_scores)
    assert abs(total - 1.0) < 0.01  # 权重归一化


def test_general_dimension_inference_failure_falls_back_to_template():
    """推断失败（extract 无果）→ 回落通用模板维度，行为不回归。"""
    k = kernel(scenes=["food", "general", "chat"])  # experience extract 对书无模式 → None
    outcome = k.make_decision(DecisionRequest(
        question="帮我选一本书送朋友", scene_hint="general", interactive=False,
        candidates=[{"id": "书A", "name": "书A"}, {"id": "书B", "name": "书B"}],
    ))
    dims = {d.dimension for d in outcome.result.recommendation.dimension_scores}
    assert dims == {"fit", "price", "quality", "service"}  # 通用模板兜底


def test_reject_routes_scene_data_driven():
    """P8：拒绝路径数据化——场景 exact_reject 生效，内置默认保留。"""
    from decide_agent.experience.reject_routes import load_reject_routes

    routes = load_reject_routes("general", [REPO_SKILLS])
    assert "不知道" in routes["exact_reject"]  # 内置默认
    assert "都还可以" in routes["exact_reject"]  # 场景追加
    assert any(r["when"] == "requirement-description-as-candidate" for r in routes["routes"])

    k = kernel(
        scenes=["food", "general", "chat"],
        reject_routes_loader=lambda s: routes,
        question_resolver=lambda s, d: {"text": "偏好？", "options": ["辣", "清淡"]},
    )
    asked = k.make_decision(request(slots={}, interactive=True))
    replied = k.respond(asked.decision_id, asked.pending.request_id, {"value": "都还可以"})
    # 场景级拒绝词生效：不当候选（后续行为不限——关键是不被当选项）
    if replied.result is not None and replied.result.recommendation is not None:
        assert replied.result.recommendation.candidate.name != "都还可以"


def test_reject_routes_segment_filter_in_extraction():
    """场景 segment_reject 参与内联候选片段过滤。"""
    from decide_agent.experience.reject_routes import load_reject_routes

    routes = load_reject_routes("general", [REPO_SKILLS])
    k = kernel(
        scenes=["food", "general", "chat"],
        reject_routes_loader=lambda s: routes,
    )
    outcome = k.make_decision(DecisionRequest(
        question="帮我对比 主要是想办公，顺便看看美剧",
        scene_hint="general", interactive=False,
    ))
    # 「主要是想办公」「顺便看看美剧」被拒绝 → 误判候选不再进入管道
    if outcome.result is not None and outcome.result.recommendation is not None:
        assert outcome.result.recommendation.candidate.name not in ("主要是想办公", "顺便看看美剧")
