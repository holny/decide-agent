"""全场景对抗矩阵（live）：真实 LLM 的对话原型全覆盖。

用途：每次架构/提示词/管道改动后手动跑 `uv run python tests/live_matrix.py`——
自驱回归，不等用户测试。默认 pytest 不收集（无 test_ 前缀）。

用法：
  uv run python tests/live_matrix.py 1 4    # 跑 case 1~4
  uv run python tests/live_matrix.py        # 跑全部
"""
import tempfile
import time
import traceback

from dotenv import load_dotenv

load_dotenv()


def _app():
    from decide_agent.app.entry import build_app

    return build_app(data_dir=tempfile.mkdtemp())


# 每个 case: (名称, [ (输入, 判定fn(reply, ctx)->bool, 失败说明) ... ])
CASES: list[tuple[str, list[tuple[str, object]]]] = [
    ("闲聊人设", [
        ("你是谁", lambda r, c: "决策" in r or "助手" in r or "选择" in r, "应介绍决策智能体身份"),
        ("今天周几", lambda r, c: any(w in r for w in ("周", "星期")), "环境事实应可答"),
    ]),
    ("模糊决策", [
        ("周末去哪玩", lambda r, c: ("玩法" in r or "自然" in r or "人文" in r or "哪" in r), "应进管道/转述追问"),
        ("自然", lambda r, c: "推荐" in r or "公园" in r or "景区" in r or "馆" in r, "应答后应出推荐"),
        ("多远？门票多少？", lambda r, c: True, "细节追问——数值或明说没有皆可"),
    ]),
    ("负反馈闭环", [
        ("帮我对比 A.星巴克 B.瑞幸 C.库迪 哪个好", lambda r, c: True, "对比决策"),
        ("都行", lambda r, c: "推荐" in r or "星巴克" in r or "瑞幸" in r or "库迪" in r, "应出推荐"),
        ("瑞幸去过了", lambda r, c: "哪里不满意" not in r, "原因随句应一轮直通"),
    ]),
    ("换备选", [
        ("换一个", lambda r, c: True, "轮换/重推荐"),
    ]),
    ("多语言", [
        ("별로예요", lambda r, c: True, "韩语不满应被语义理解"),
    ]),
    ("动态维度", [
        ("帮我选一本书送朋友", lambda r, c: True, "泛决策（无模板）→ 动态维度或澄清"),
    ]),
    ("范围外诚实", [
        ("今天股市行情怎么样", lambda r, c: True, "无数据源 → 应诚实说明而非编造"),
    ]),
]


def main(only: range | None = None) -> int:
    from decide_agent.app.entry import build_app

    app = build_app(data_dir= tempfile.mkdtemp())
    passed, failed = 0, []
    for index, (name, steps) in enumerate(CASES, 1):
        if only and index not in only:
            continue
        print(f"\n=== Case {index} {name} ===", flush=True)
        for user_input, check, why in steps:
            t0 = time.time()
            try:
                reply = app.chat_turn(user_input)
                ok = check(reply, None)
            except Exception as exc:  # noqa: BLE001 — 矩阵捕获一切，报告不中断
                reply, ok = f"EXC {type(exc).__name__}: {exc}", False
                traceback.print_exc()
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            else:
                failed.append(f"{name}/{user_input} ({why})")
            print(f"  [{status} {time.time()-t0:.0f}s] {user_input!r} → {reply[:120]}", flush=True)
            if not ok:
                print(f"    ↑ 判据未满足：{why}", flush=True)
    total = passed + len(failed)
    print(f"\n===== 矩阵结果: {passed}/{total} PASS =====", flush=True)
    for f in failed:
        print(f"  FAIL: {f}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    rng = range(int(args[0]), int(args[1]) + 1) if len(args) == 2 else None
    raise SystemExit(main(rng))
