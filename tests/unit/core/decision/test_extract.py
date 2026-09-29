"""一句话选项抽取测试（v4 §5.1 extract 规则层）。"""
from decide_agent.core.decision.extract import extract_inline_candidates


def test_enum_clause_with_pause_mark():
    result = extract_inline_candidates(
        "周五团建吃饭，人均40的老字号面馆、150的日料omakase、80的川菜馆，选哪个？")
    assert result is not None
    question, options = result
    assert question == "周五团建吃饭"  # 背景保留为问句
    assert options == ["人均40的老字号面馆", "150的日料omakase", "80的川菜馆"]


def test_alternative_or_split():
    _question, options = extract_inline_candidates("辣的还是清淡的选哪个好")
    assert options == ["辣的", "清淡的"]

    _question, options = extract_inline_candidates("投资 A 基金还是 B 岗位，帮我选")
    assert options == ["投资 A 基金", "B 岗位"]


def test_letter_markers_without_intent_word():
    result = extract_inline_candidates("周末去哪？ A.咖啡馆 B.书店")
    assert result is not None
    assert result[1] == ["咖啡馆", "书店"]


def test_hint_at_clause_start_options_in_next_clause():
    result = extract_inline_candidates("帮我选个编程语言，Python 还是 Go 还是 Rust")
    assert result is not None
    assert result[1] == ["Python", "Go", "Rust"]


def test_requirement_list_is_not_options():
    """关键负例：需求清单（无选择意图词）绝不误切。"""
    assert extract_inline_candidates("想吃辣、预算100、近一点") is None
    assert extract_inline_candidates("量子纠缠怎么解释") is None
    assert extract_inline_candidates("今天天气不错") is None
