"""从一句话自然语言中抽取用户直供候选（v4 §5.1 extract 的规则层）。

判别核心：选择意图词（选哪个/哪个好/二选一…）或 ≥2 个字母标记（A. B.）——
  「面馆、日料、川菜馆选哪个」→ 选项清单，切
  「想吃辣、预算100、近一点」→ 需求清单（无意图词），不切 ✓

中文层级算法：
  1) 按句终标点拆句，取含选择意图词的句子
  2) 按逗号拆子句，定位意图词子句
  3) 候选来源（优先级）：含顿号的最后一个子句（顿号=同级列举）>
     意图词子句按「还是/或者/vs」切（意图词处截断）
  字母标记分支：A./B. 分段，标记前文本 = 问句。
规则先行（L1）；切错由链降级与披露兜底，语义级确认归 decision_model（P4+）。
"""
import re

CHOICE_HINTS = (
    "帮我选", "选哪个", "哪个好", "怎么选", "如何选", "纠结选", "挑哪个",
    "二选一", "三选一", "多选一", "选一个", "选个", "该选",
)
MARKER_RE = re.compile(r"(?:^|[\s，,；;])([A-Za-z])[.、:)：]\s*")
ENUM_SPLIT_RE = re.compile(r"[、]")
SPLIT_RE = re.compile(r"[、，,；;]|\s+")
ALT_SPLIT_RE = re.compile(r"还是|或者|或是|vs|VS|Vs")
NON_OPTION_RE = re.compile(r"预算|人均以内|以内|左右|附近|近一点|近点|远点|要吃|想吃|希望|尽量|最好|别太")

# 第三方决策对象：「帮我朋友/帮我妈/帮我同事张三」——「帮我选」不算（那还是用户自己）
SUBJECT_RE = re.compile(
    r"帮(?:我|我们)?(?:的)?(?!和|与|跟)"
    r"(朋友|爸妈|爸爸|妈妈|妈|老婆|老公|对象|女朋友|男朋友|同事|领导|老板|客户"
    r"|孩子|儿子|女儿|室友|家人|家里人|[\u4e00-\u9fa5]{1,6}?)"
    r"(?=选|挑|找|推荐|看|参考|决定)"
)


def extract_subject(text: str) -> str | None:
    """识别决策对象：帮朋友/家人/同事/具名第三者选择 → 返回对象称谓；用户本人 → None。"""
    match = SUBJECT_RE.search(text)
    if match is None:
        return None
    subject = match.group(1)
    # 后过滤：「帮我和女朋友」类是共同决策（交给 joint），不是第三方；「我/我们」= 用户本人
    if subject in ("我", "我们") or subject.startswith(("我和", "我们和", "和")):
        return None
    return subject


_CLEAN_CHARS = set(" 那家那个这家?？!！，,；;　")


def _clean(part: str) -> str:
    # 注意：不剥「的」——"辣的/清淡的"是合法候选名（只剥两端，不动内部字符）
    start, end = 0, len(part)
    while start < end and part[start] in _CLEAN_CHARS:
        start += 1
    while end > start and part[end - 1] in _CLEAN_CHARS:
        end -= 1
    return part[start:end]


def _from_markers(text: str) -> tuple[str, list[str]] | None:
    """字母标记分段：标记前文本=问句，标记后各段=候选。"""
    matches = list(MARKER_RE.finditer(text))
    if len(matches) < 2:
        return None
    question = text[: matches[0].start()].strip(" ，,；;。?？")
    options = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        option = _clean(text[start:end])
        if len(option) >= 1:
            options.append(option)
    if len(options) < 2:
        return None
    return (question or "请从以下选项中帮我选择"), options


def extract_inline_candidates(text: str) -> tuple[str, list[str]] | None:
    text = text.strip()

    # 分支一：字母标记（A. B. C.）
    by_markers = _from_markers(text)
    if by_markers is not None:
        return by_markers

    # 分支二：选择意图词
    hint = next((h for h in CHOICE_HINTS if h in text), None)
    if hint is None:
        return None
    sentences = re.split(r"[?？!！。]", text)
    sentence = next((s for s in reversed(sentences) if hint in s and s.strip()), None)
    if sentence is None:
        return None
    clauses = [c.strip() for c in re.split(r"[，,]", sentence) if c.strip()]
    hint_index = next(
        (i for i, c in enumerate(clauses) if hint in c), len(clauses) - 1,
    )
    hint_clause = clauses[hint_index]
    hint_pos = hint_clause.rfind(hint)
    base_clause = hint_clause[:hint_pos] if hint_pos >= 0 else hint_clause

    candidates: list[str] | None = None
    prefix_clauses: list[str] = []
    enum_clauses = [i for i, c in enumerate(clauses) if "、" in c]
    if enum_clauses:  # ① 顿号 = 同级列举（优先）
        source = clauses[enum_clauses[-1]]
        candidates = [p for p in ENUM_SPLIT_RE.split(source)]
        prefix_clauses = clauses[: enum_clauses[-1]]
    elif base_clause:  # ② 意图词在子句中后部：前部按「还是/或者/vs」或全分隔切
        split_re = ALT_SPLIT_RE if ALT_SPLIT_RE.search(base_clause) else SPLIT_RE
        candidates = [p for p in split_re.split(base_clause)]
        prefix_clauses = clauses[:hint_index]
    elif hint_index + 1 < len(clauses):  # ③ 意图词在子句开头：候选在后一子句
        next_clause = clauses[hint_index + 1]
        split_re = ALT_SPLIT_RE if ALT_SPLIT_RE.search(next_clause) else SPLIT_RE
        candidates = [p for p in split_re.split(next_clause)]
        prefix_clauses = clauses[:hint_index]
    elif hint_index > 0:  # ④ 候选在前一子句
        prev = clauses[hint_index - 1]
        split_re = ALT_SPLIT_RE if ALT_SPLIT_RE.search(prev) else SPLIT_RE
        candidates = [p for p in split_re.split(prev)]
        prefix_clauses = clauses[: hint_index - 1]

    parts = [_clean(p) for p in candidates or []]
    parts = [p for p in parts if len(p) >= 2 and not NON_OPTION_RE.search(p)]
    if len(parts) < 2:
        return None

    prefix = "，".join(prefix_clauses).strip(" ，,；;。?？")
    return (prefix or text), parts


JOINT_RE = re.compile(
    r"帮(?:我|我们)和(?:我)?(?:的)?"
    r"(朋友|爸妈|爸爸|妈妈|女朋友|男朋友|对象|老婆|老公|同事|领导|客户|孩子|室友|家人"
    r"|[\u4e00-\u9fa5A-Za-z0-9]{1,8}?)"
    r"(?:一起|俩|两个)?(?=选|挑|找|看|决定|参考|考虑)"
)


def extract_joint_party(text: str) -> str | None:
    """识别共同决策：「帮我和xx选」→ 返回 xx（用户与 xx 共同的决策）。

    优先级：第三方 SUBJECT_RE 先于本函数（「帮朋友选」≠「帮我和朋友选」）。
    """
    match = JOINT_RE.search(text)
    if match is None:
        return None
    other = match.group(1)
    return other if other not in ("我", "我们") else None
