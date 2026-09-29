"""Narrator i18n: 输出语言文案表（zh/en）。kernel 披露行 v1 仍为 zh（限制已知）。"""

STRINGS = {
    "zh": {
        "recommend": "推荐：{name}（综合 {score}，{band}）",
        "alternative": "备选：{name}（{score}，{band}）{reason}",
        "reason": "理由：{reason}",
        "band_full": "，{band}",
        "band_very": "非常匹配",
        "band_good": "比较匹配",
        "band_ok": "一般",
        "band_low": "不太匹配",
        "verdict_strong": "很匹配",
        "verdict_decent": "不错",
        "verdict_fair": "一般",
        "pref_suffix": "；符合你的偏好（{prefs}）",
        "scale": "综合得分 0~1，越高越符合你的需求；本次推荐在可比候选中排第 1，匹配度：{band}。",
        "accept": "可以直接说「接受」，或补充信息（如预算、忌口）让结果更准。",
        "note": "说明：{items}",
        "cannot": "未能给出推荐：{reason}",
        "disclaimer": "⚠ 以上建议仅供参考，不构成专业意见。涉及医疗、法律、金融等重大决策，请咨询相关领域专业人士。",
        "pareto_note": "帕累托前沿：以下候选不可被同时超越（均为某种权衡下的最优选择）",
        "dominated_note": "已排除 {count} 个被支配候选（存在每维均不优于其他候选的选项）",
        "low_hint": "提示：匹配度偏低——当前选项可能与你的需求不太契合，建议补充更多信息或换一批选项。",
        "chat_reply": "我是决策助手，擅长帮你在选项之间做选择（如「周末去哪玩」「两个 offer 选哪个」）。"
                      "闲聊不是我的强项——有决策需求随时抛给我！",
    },
    "en": {
        "recommend": "Recommendation: {name} (overall {score}, {band})",
        "alternative": "Alternative: {name} ({score}, {band}) {reason}",
        "reason": "Why: {reason}",
        "band_full": ", {band}",
        "band_very": "excellent match",
        "band_good": "good match",
        "band_ok": "fair",
        "band_low": "poor match",
        "verdict_strong": "strong match",
        "verdict_decent": "decent",
        "verdict_fair": "fair",
        "pref_suffix": "; matches your preferences ({prefs})",
        "scale": "Overall score ranges 0-1 (higher = better fit); this recommendation ranks #1 among comparable options, fit: {band}.",
        "accept": "You can say 'accept', or add details (budget, restrictions) to refine the result.",
        "note": "Notes: {items}",
        "cannot": "No recommendation possible: {reason}",
        "disclaimer": "⚠ These suggestions are for reference only. For major decisions involving medical, legal, or financial matters, please consult a professional.",
        "pareto_note": "Pareto frontier: the following candidates cannot be simultaneously dominated",
        "dominated_note": "{count} dominated candidates excluded",
        "low_hint": "Tip: fit is low - these options may not match your needs well; consider adding details or new options.",
        "chat_reply": "I'm a decision assistant - I help you choose between options (e.g. 'where to go this "
                      "weekend', 'which offer to take'). Small talk isn't my forte - throw a decision at me!",
    },
}


def strings(language: str) -> dict:
    return STRINGS.get(language, STRINGS["zh"])


CLI_STRINGS = {
    "zh": {
        "prompt": "你",
        "agent_label": "Decide-Agent",
        "banner": "Decide-Agent — 输入想法开始（quit 退出；Enter 提交当前输入）",
        "banner_options": "  用户直供选项三种写法：① 单行内联「周末去哪？ A.咖啡馆 B.书店」\n"
                          "  ② 选项行「A.咖啡馆」「B.书店」后再输入问句行\n"
                          "  ③ 先 /opt 咖啡馆,书店 再输入问句",
        "quit_words": {"quit", "exit", "q", "退出"},
    },
    "en": {
        "prompt": "You",
        "agent_label": "Decide-Agent",
        "banner": "Decide-Agent — Type to start (quit to exit; Enter to submit)",
        "banner_options": "  Three ways to provide options: ① Inline 'Weekend? A.cafe B.bookstore'\n"
                          "  ② Option lines 'A.cafe' 'B.bookstore' then question line\n"
                          "  ③ /opt cafe,bookstore first, then your question",
        "quit_words": {"quit", "exit", "q"},
    },
}


def cli_strings(language: str) -> dict:
    return CLI_STRINGS.get(language, CLI_STRINGS["zh"])
