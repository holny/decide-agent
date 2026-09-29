"""端到端功能测试（真实 Jev 模式）：MCP / HTTP / A2A 全链路 + 自进化模块。

用法：TYPESAFE_API_KEY 已配置时
  DECIDE_AGENT__decision__provider_timeouts__decide=180 uv run python scripts/functional_test.py
全部通过输出 ALL CHECKS PASSED；任一失败 exit 1。
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = str(ROOT / ".venv" / "bin" / "decide-agent")
WEIGHTS = {
    "taste_match": 0.35, "distance": 0.2, "price": 0.2, "queue": 0.15, "weather_fit": 0.1,
}
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


def env_for(work: Path, **extra) -> dict:
    env = os.environ.copy()
    env.update({
        "DECIDE_AGENT__paths__data_dir": str(work),
        "DECIDE_AGENT__decision__provider_timeouts__decide": "180",
        "DECIDE_AGENT__decision__provider_timeouts__collect": "60",
        "DECIDE_AGENT__decision__provider_timeouts__analysis": "60",
    })
    env.update(extra)
    return env


def http_json(url: str, payload: dict | None = None, method: str | None = None, timeout: int = 240):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


# ------------------------------------------------------------------ [1] kernel

def check_kernel_real(work: Path) -> dict:
    print("[1/8] kernel 真实全链（classify→collect→批量 score→合成）")
    from decide_agent.app.entry import build_app

    decide = build_app(data_dir=work)
    outcome = decide.make_decision(__import__(
        "decide_agent.schemas.workflow", fromlist=["DecisionRequest"]).DecisionRequest(
        question="我想吃饭，想吃辣，预算100以内，最好近一点", interactive=False))
    result = outcome.result
    check("kernel: completed", outcome.status == "completed")
    judgments = (work / "evolved" / "users" / "local" / "judgments.jsonl")
    providers_used = {
        json.loads(line)["provider"]
        for line in judgments.read_text("utf-8").splitlines() if line.strip()
    } if judgments.exists() else set()
    check("kernel: provider=decision_model", "decision_model" in providers_used,
          f"providers={providers_used}")
    check("kernel: 置信>0.5", bool(result and result.confidence > 0.5),
          f"confidence={result.confidence if result else None}")
    return {"decision_id": outcome.decision_id, "scene": result.scene if result else ""}


# ------------------------------------------------------------------ [2] HTTP

def check_http_real(work: Path) -> None:
    print("[2/8] HTTP 真实全链（serve-http 子进程）")
    port = 8231
    proc = subprocess.Popen(
        [SCRIPT, "serve-http", "--port", str(port)],
        env=env_for(work), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(30):
            try:
                http_json(f"{base}/healthz", timeout=5)
                break
            except Exception:  # noqa: BLE001 — 服务就绪轮询
                time.sleep(0.5)
        outcome = http_json(f"{base}/v1/decisions", {
            "question": "我想吃饭，想吃辣，不要等太久", "interactive": True,
        })
        check("http: require_action", outcome["status"] == "require_action", str(outcome)[:200])
        final = http_json(f"{base}/v1/decisions/{outcome['decision_id']}/respond", {
            "request_id": outcome["pending"]["request_id"],
            "response": {"value": "辣", "scope": "always"},
        })
        check("http: respond completed", final["status"] == "completed", str(final)[:200])
        check("http: 真实评分非中性",
              any(ds["score"] not in (0.5, 0.0)
                  for ds in final["result"]["recommendation"]["dimension_scores"]))
        stored = http_json(f"{base}/v1/decisions/{outcome['decision_id']}")
        check("http: 快照可查", stored["decision_id"] == outcome["decision_id"])
        feedback = http_json(f"{base}/v1/decisions/{outcome['decision_id']}/feedback",
                             {"action": "accept"})
        check("http: feedback accept", feedback.get("accepted") is True)
    finally:
        proc.terminate()


# ------------------------------------------------------------------ [3] MCP

async def _mcp_flow(work: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=SCRIPT, args=["serve-mcp"], env=env_for(work),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            first = await session.call_tool(
                "make_decision", {"question": "我想吃饭，想吃辣", "interactive": True},
            )
            data = first.structuredContent or json.loads(first.content[0].text)
            check("mcp: require_action", data["status"] == "require_action", str(data)[:200])
            second = await session.call_tool("make_decision", {
                "decision_id": data["decision_id"],
                "request_id": data["pending"]["request_id"],
                "response": "辣", "scope": "always",
            })
            data2 = second.structuredContent or json.loads(second.content[0].text)
            check("mcp: continuation completed", data2["status"] == "completed", str(data2)[:200])
            caps = await session.call_tool("list_capabilities", {})
            cap_data = caps.structuredContent or json.loads(caps.content[0].text)
            if isinstance(cap_data, dict):
                cap_data = cap_data.get("result") or cap_data.get("capabilities") or []
            check("mcp: list_capabilities", {c["name"] for c in cap_data} >= {"location", "weather", "poi_search"})


def check_mcp_real(work: Path) -> None:
    print("[3/8] MCP 真实全链（stdio 子进程，auto 模式）")
    asyncio.run(_mcp_flow(work))


# ------------------------------------------------------------------ [4] A2A

def check_a2a_real(work: Path) -> None:
    print("[4/8] A2A 真实全链（serve-a2a 子进程）")
    port = 8232
    proc = subprocess.Popen(
        [SCRIPT, "serve-a2a", "--port", str(port)],
        env=env_for(work), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(30):
            try:
                http_json(f"{base}/.well-known/agent-card.json", timeout=5)
                break
            except Exception:  # noqa: BLE001 — 服务就绪轮询
                time.sleep(0.5)
        task = http_json(base, {
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "我想吃饭，想吃辣"}],
                                   "messageId": "m1", "contextId": "c1"}},
        })
        result = task["result"]
        check("a2a: input-required", result["status"]["state"] == "input-required", str(result)[:200])
        final = http_json(base, {
            "jsonrpc": "2.0", "id": 2, "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "辣"}],
                                   "messageId": "m2", "contextId": "c1", "taskId": result["id"]}},
        })
        final_task = final["result"]
        check("a2a: 续答 completed", final_task["status"]["state"] == "completed", str(final_task)[:200])
        check("a2a: decision_report artifact",
              any(a["name"] == "decision_report" for a in final_task.get("artifacts") or []))
    finally:
        proc.terminate()


# ------------------------------------------------------------------ [5-8] 自进化

def check_evolution(work: Path) -> None:
    print("[5/8] 自进化：learned_memory 落 owner 层")
    from decide_agent.core.memory.manager import MemoryService, build_sqlite_store

    records = MemoryService(build_sqlite_store(work / "evolved", "local")).export("local")
    check("evolution: scope=always 落库", any("taste_match" in r.content for r in records),
          str([r.content for r in records]))

    print("[6/8] 自进化：recorder 判断流水（真实 provider=decision_model）")
    from decide_agent.experience.dataset import export_dataset

    judgments = work / "evolved" / "users" / "local" / "judgments.jsonl"
    assert judgments.exists(), "judgments.jsonl 缺失"
    stats = export_dataset([judgments], work / "sft.jsonl")
    check("evolution: 判定流水含 decision_model 且可导出 SFT",
          stats["exported"] >= 1, str(stats))

    print("[7/8] 自进化：影子双跑（decision_model vs experience）+ 三指标")
    from decide_agent.app.bootstrap import build_provider_chain
    from decide_agent.experience.metrics import (
        ShadowRunner,
        agreement,
        coverage,
        drift,
        snapshot_metrics,
    )
    from decide_agent.schemas.question import TypedQuestion

    shadow = ShadowRunner(build_provider_chain())
    q = TypedQuestion(shape="score", scene="food", dimension="taste_match",
                      context={"candidate": {"name": "蜀香居", "tags": ["辣"]},
                               "slots": {"taste_match": "辣"}})
    result = shadow.compare(q)
    check("evolution: 影子双跑双 provider 出答",
          len(result["scored"]) >= 2 and "decision_model" in result["answers"], str(result)[:200])
    metrics = snapshot_metrics(
        work / "evolved" / "users" / "local" / "metrics.json",
        coverage_v=coverage([]), agreement_v=agreement([result]), drift_v=drift([]),
    )
    check("evolution: metrics.json 三指标", "experience_agreement" in metrics)

    print("[8/8] 自进化：WeightModulation 反馈归因 + 四层解析生效")
    from decide_agent.core.weights.modulation import (
        WeightModulationStore,
        attribute_feedback,
    )
    from decide_agent.core.weights.resolver import resolve

    store = WeightModulationStore(work / "evolved", "local")
    attribute_feedback(store, "food",
                       [{"dimension": "taste_match", "score": 0.975, "weight": 0.35},
                        {"dimension": "price", "score": 0.983, "weight": 0.2}],
                       accepted=True)
    eff = resolve("food", WEIGHTS, store.all("food"))
    check("evolution: 反馈归因调制生效", "taste_match" in eff.modulated,
          str(eff.disclosure))


def main() -> None:
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("需要 TYPESAFE_API_KEY（Jev 云端）")
        sys.exit(2)
    with tempfile.TemporaryDirectory(prefix="decide-func-") as tmp:
        work = Path(tmp)
        check_kernel_real(work)
        check_http_real(work)
        check_mcp_real(work)
        check_a2a_real(work)
        check_evolution(work)
    failed = [name for name, ok, _ in CHECKS if not ok]
    print(f"\n{'=' * 50}\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
