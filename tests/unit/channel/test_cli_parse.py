"""CLI 内联选项解析测试：用户直供「A. xxx」行 → 候选。"""
from types import SimpleNamespace

from typer.testing import CliRunner

from decide_agent.channel.cli.main import create_cli_app


class FakeDecide:
    def __init__(self) -> None:
        self.requests = []
        self.kernel = SimpleNamespace(entry_intent=lambda text: None)  # 默认无上下文意图

    def make_decision(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            status="completed", decision_id="d-x", pending=None,
            result=SimpleNamespace(scene="general", recommendation=None), learned_memory=[],
        )

    def render(self, outcome, fmt=None, language=None):
        return "ok"


def _run(text: str):
    fake = FakeDecide()
    result = CliRunner().invoke(create_cli_app(lambda: fake), ["chat"], input=text + "\nquit\n")
    assert result.exit_code == 0, result.output
    return fake.requests


def test_options_first_then_question_merges():
    """定稿语义：选项行先行挂起，问句行提交整块。"""
    requests = _run("A. 咖啡馆\nB. 书店\n周末去哪放松？\n")
    assert requests[0].question.startswith("周末去哪放松")
    assert requests[0].candidates == [
        {"id": "咖啡馆", "name": "咖啡馆"},
        {"id": "书店", "name": "书店"},
    ]


def test_inline_single_line_options():
    requests = _run("周末去哪放松？ A.咖啡馆 B.书店\n")
    assert requests[0].question.startswith("周末去哪放松")
    assert requests[0].candidates == [{"id": "咖啡馆", "name": "咖啡馆"}, {"id": "书店", "name": "书店"}]


def test_plain_text_stays_free_text():
    requests = _run("随便聊聊天气")
    assert requests[0].candidates == []
    assert requests[0].question == "随便聊聊天气"


def test_dissatisfaction_feedback_loop():
    """负反馈闭环：不行（无原因）→ 先试抽取（抽不出）→ 反问 → 原因进 give_feedback。"""
    fake = FakeDecide()
    completed_no_reason = SimpleNamespace(
        status="completed", decision_id="d-1", pending=None,
        result=SimpleNamespace(scene="travel", recommendation=SimpleNamespace(name="科技馆"),
                               alternatives=[]),
        learned_memory=[], missing_information=["未能从反馈中识别出可执行的调整"],
    )
    calls, feedbacks = [], []

    def make(request):
        calls.append(request)
        return completed_no_reason

    fake.make_decision = make
    fake.kernel = SimpleNamespace(
        entry_intent=lambda text: "dissatisfied" if text == "不行吧" else None,
        give_feedback=lambda did, text: (feedbacks.append((did, text)), completed_no_reason)[1],
    )
    result = CliRunner().invoke(
        create_cli_app(lambda: fake),
        ["chat"], input="周末去哪玩\n不行吧\n不想去博物馆\nquit\n",
    )
    assert result.exit_code == 0, result.output
    assert "哪里不满意" in result.output  # 抽不出原因 → 反问
    assert feedbacks == [("d-1", "不行吧"), ("d-1", "不想去博物馆")]  # 两轮都进闭环
    assert len(calls) == 1  # 不开新决策


def test_dissatisfaction_with_inline_reason_one_pass():
    """原因随句给出（"这个我去过了"）→ 一轮直通重推荐，不反问。"""
    fake = FakeDecide()
    refined = SimpleNamespace(
        status="completed", decision_id="d-1", pending=None,
        result=SimpleNamespace(scene="travel", recommendation=SimpleNamespace(name="逍遥津公园"),
                               alternatives=[]),
        learned_memory=[], missing_information=["已按反馈过滤 1 个候选"],
    )
    calls, feedbacks = [], []

    def make(request):
        calls.append(request)
        return SimpleNamespace(
            status="completed", decision_id="d-1", pending=None,
            result=SimpleNamespace(scene="travel", recommendation=SimpleNamespace(name="环城公园"),
                                   alternatives=[]),
            learned_memory=[], missing_information=[],
        )

    fake.make_decision = make
    fake.render = lambda outcome, fmt=None, language=None: f"rec:{outcome.result.recommendation.name}"
    fake.kernel = SimpleNamespace(
        entry_intent=lambda text: "dissatisfied" if "去过了" in text else None,
        give_feedback=lambda did, text: (feedbacks.append((did, text)), refined)[1],
    )
    result = CliRunner().invoke(
        create_cli_app(lambda: fake), ["chat"], input="周末去哪玩\n这个我去过了\nquit\n",
    )
    assert result.exit_code == 0, result.output
    assert "rec:逍遥津公园" in result.output  # 一轮直通出新推荐
    assert "哪里不满意" not in result.output  # 不反问
    assert feedbacks == [("d-1", "这个我去过了")]
    assert len(calls) == 1


def test_semantic_intent_fallback_multilingual():
    """本地短语未命中的语言（韩语）→ 语义意图兜底（链上模型判定）→ 正确路由。"""
    fake = FakeDecide()
    completed = SimpleNamespace(
        status="completed", decision_id="d-1", pending=None,
        result=SimpleNamespace(scene="travel", recommendation=SimpleNamespace(name="科技馆"),
                               alternatives=[]),
        learned_memory=[],
    )
    calls = []

    def make(request):
        calls.append(request)
        return completed

    fake.make_decision = make
    fake.kernel = SimpleNamespace(entry_intent=lambda text: "dissatisfied" if "안" in text else None)
    result = CliRunner().invoke(
        create_cli_app(lambda: fake), ["chat"], input="周末去哪玩\n안 돼요\nquit\n",
    )
    assert result.exit_code == 0, result.output
    assert "哪里不满意" in result.output  # 韩语负反馈被语义兜底接住
    assert len(calls) == 1


def test_swap_next_alternative():
    """「换一个」：推荐 ↔ 备选轮换（纯呈现层，不重打分）。"""
    fake = FakeDecide()

    def render(outcome, fmt=None, language=None):
        return f"rec:{outcome.result.recommendation.name}"

    fake.render = render

    def make(request):
        return SimpleNamespace(
            status="completed", decision_id="d-1", pending=None,
            result=SimpleNamespace(
                scene="travel",
                recommendation=SimpleNamespace(name="科技馆"),
                alternatives=[
                    SimpleNamespace(name="植物园", total_score=0.77, dimension_scores=[]),
                    SimpleNamespace(name="市博物馆", total_score=0.78, dimension_scores=[]),
                ],
            ),
            learned_memory=[],
        )

    fake.make_decision = make
    fake.kernel = SimpleNamespace(entry_intent=lambda text: "swap_next" if text == "换一个" else None)
    result = CliRunner().invoke(
        create_cli_app(lambda: fake), ["chat"], input="周末去哪玩\n换一个\nquit\n",
    )
    assert result.exit_code == 0, result.output
    assert "rec:植物园" in result.output  # 备选顶替为推荐
