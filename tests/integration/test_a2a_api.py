"""A2A e2e (P6): 官方 a2a-sdk JSON-RPC message/send → input-required → 续答 completed。"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("a2a")

from decide_agent.app.entry import build_app
from decide_agent.channel.a2a.server import build_a2a_app

ROOT = Path(__file__).resolve().parents[2]
_jsonrpc_id = 0


def _client(tmp_path):
    decide = build_app(data_dir=tmp_path, decision_mode="experience")
    return TestClient(build_a2a_app(decide, url="http://testserver/"))


def _send(client, text, *, task_id=None, context_id="ctx-1"):
    global _jsonrpc_id
    _jsonrpc_id += 1
    message = {
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
        "messageId": f"m-{_jsonrpc_id}",
        "contextId": context_id,
    }
    if task_id:
        message["taskId"] = task_id
    response = client.post("/", json={
        "jsonrpc": "2.0", "id": _jsonrpc_id,
        "method": "message/send",
        "params": {"message": message},
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    return payload["result"]


def test_agent_card_served(tmp_path):
    client = _client(tmp_path)
    card = client.get("/.well-known/agent-card.json")
    if card.status_code != 200:
        card = client.get("/.well-known/agent.json")
    assert card.status_code == 200
    assert card.json()["name"] == "decide-agent"


def test_message_send_full_loop(tmp_path):
    client = _client(tmp_path)
    # ① 首调：缺口味 → input-required
    task = _send(client, "我想吃饭")
    assert task["status"]["state"] == "input-required", task
    assert task["metadata"]["request_id"]
    # 提示语可读（prompts.jsonc 模板）
    message_text = task["status"]["message"]["parts"][0]["root"]["text"] \
        if "root" in task["status"]["message"]["parts"][0] else \
        task["status"]["message"]["parts"][0]["text"]
    assert "吃辣" in message_text

    # ② 续答（同 taskId）→ completed + DecisionReport artifact
    final = _send(client, "想吃辣", task_id=task["id"])
    assert final["status"]["state"] == "completed"
    artifacts = final.get("artifacts") or []
    assert any(a["name"] == "decision_report" for a in artifacts)
    part = next(a for a in artifacts if a["name"] == "decision_report")["parts"][0]
    root = part.get("root") or {}
    payload = root.get("data") or part.get("data")
    if payload is None:
        payload = json.loads(root.get("text") or part.get("text"))
    assert payload["scene"] == "food"
    assert payload["recommendation"]["candidate"]["name"]


def test_cancellation(tmp_path):
    client = _client(tmp_path)
    task = _send(client, "我想吃饭")
    _jsonrpc_id = 100
    response = client.post("/", json={
        "jsonrpc": "2.0", "id": 101, "method": "tasks/cancel",
        "params": {"id": task["id"]},
    })
    assert response.status_code == 200
    assert response.json()["result"]["status"]["state"] == "canceled"


