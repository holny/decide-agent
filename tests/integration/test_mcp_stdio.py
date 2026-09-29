"""MCP stdio e2e (P4.5): 真实子进程 serve-mcp + 官方 ClientSession 工具调用闭环。

覆盖：make_decision 首调（require_action）→ continuation 续调（completed）→
learned_memory 落盘 + list_capabilities 工具。
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytest.importorskip("mcp")


@pytest.fixture()
def mcp_env(tmp_path):
    env = os.environ.copy()
    env.update({
        "DECIDE_AGENT__paths__data_dir": str(tmp_path),
        "DECIDE_AGENT__decision__decision_mode": "experience",
    })
    env.pop("TYPESAFE_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    return env


@pytest.fixture()
def server_script() -> str:
    script = Path(sys.executable).parent / "decide-agent"
    if not script.exists():  # 非 venv 环境兜底：模块方式拉起
        return None
    return str(script)


@pytest.mark.asyncio
async def test_mcp_stdio_full_loop(mcp_env, server_script, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if server_script is None:
        pytest.skip("decide-agent entrypoint script not found")

    async def _call_with_retry(session, name, args, attempts=2):
        """全套件负载下子进程冷启动+首判可能超时 → 错误负载透明失败或重试一次。"""
        import asyncio

        last = None
        for attempt in range(attempts):
            result = await asyncio.wait_for(session.call_tool(name, args), timeout=120)
            data = result.structuredContent or _parse_text(result)
            if not (isinstance(data, dict) and data.get("error")):
                return data
            last = data
            await asyncio.sleep(1)
        pytest.fail(f"tool error payload after {attempts} attempts: {last}", pytrace=False)
    params = StdioServerParameters(command=server_script, args=["serve-mcp"], env=mcp_env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        # ① 首调：question → require_action（taste 缺失追问）
        data = await _call_with_retry(
            session, "make_decision", {"question": "我想吃饭", "interactive": True},
        )
        assert data["status"] == "require_action", data
        assert data["pending"]["payload"]["dimension"] == "taste_match"
        decision_id = data["decision_id"]
        request_id = data["pending"]["request_id"]

        # ② continuation：回答追问 → completed + learned_memory
        data2 = await _call_with_retry(session, "make_decision", {
            "decision_id": decision_id, "request_id": request_id,
            "response": "辣", "scope": "always",
        })
        assert data2["status"] == "completed"
        assert data2["learned_memory"], "scope=always 应落 learned_memory"

        # ③ 双向验证：重启语义（新 store 实例）读回 owner 层
        stored = _probe_owner_memory(tmp_path)
        assert any(r.content == "taste_match:辣" for r in stored)

        # ④ list_capabilities 工具
        caps = await session.call_tool("list_capabilities", {})
        names = _cap_names(caps)
        assert {"location", "weather", "poi_search"} <= names


def _probe_owner_memory(tmp_path):
    from decide_agent.core.memory.manager import MemoryService, build_memory_store

    return MemoryService(
        build_memory_store(Path(tmp_path) / "evolved", "local"),
    ).export("local")


def _parse_text(result) -> dict:
    for block in result.content:
        if getattr(block, "type", "") == "text":
            return json.loads(block.text)
    raise AssertionError(f"no parsable content: {result}")


def _cap_names(result) -> set:
    data = result.structuredContent or _parse_text(result)
    if isinstance(data, list):
        return {item["name"] for item in data}
    if isinstance(data, dict):
        for key in ("capabilities", "result", "items"):  # FastMCP 结构化包装兼容
            value = data.get(key)
            if isinstance(value, list):
                return {item["name"] for item in value}
    return set()
