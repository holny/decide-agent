"""HTTP API e2e (P3): decisions / SSE / respond / feedback / memory / auth / 重启恢复。

注入 experience 模式 DecideApp（确定性、零网络）；runtime_dir 用 tmp。
"""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from decide_agent.app.entry import build_app
from decide_agent.channel.http_adapter.app import create_http_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def decide(tmp_path):
    return build_app(data_dir=tmp_path, decision_mode="experience")


@pytest.fixture()
def client(decide, tmp_path):
    return TestClient(create_http_app(decide, data_dir=tmp_path, runtime_dir=tmp_path / "runtime"))


def _create(client, **kw) -> dict:
    body = {"question": "想吃辣", "scene_hint": "food", "interactive": True,
            "slots": {}, "weights": {
                "taste_match": 0.35, "distance": 0.2, "price": 0.2,
                "queue": 0.15, "weather_fit": 0.1},
            "candidates": [
                {"id": "a", "name": "蜀香居", "tags": ["辣"], "distance_m": 500,
                 "price_per_person": 65, "wait_min": 10},
                {"id": "b", "name": "清淡居", "tags": ["清淡"], "distance_m": 300,
                 "price_per_person": 30, "wait_min": 5},
            ], **kw}
    response = client.post("/v1/decisions", json=body)
    assert response.status_code == 200
    return response.json()


def test_healthz_and_full_decision_loop(client):
    assert client.get("/healthz").json() == {"ok": True}

    outcome = _create(client)
    assert outcome["status"] == "require_action"
    assert outcome["state"] == "input-required"
    decision_id = outcome["decision_id"]
    request_id = outcome["pending"]["request_id"]

    # SSE：已产生 stage + pending_request 事件，after_seq 续传
    events = client.get(f"/v1/decisions/{decision_id}/events?after_seq=-1&max_wait_s=0.3")
    assert events.status_code == 200
    body = events.text
    assert "pending_request" in body and "stage" in body

    final = client.post(f"/v1/decisions/{decision_id}/respond",
                        json={"request_id": request_id, "response": {"value": "辣", "scope": "once"}})
    assert final.status_code == 200
    data = final.json()
    assert data["status"] == "completed"
    assert data["result"]["recommendation"]["candidate"]["id"] == "a"


def test_respond_idempotent_and_unknown(client):
    outcome = _create(client)
    did, rid = outcome["decision_id"], outcome["pending"]["request_id"]
    first = client.post(f"/v1/decisions/{did}/respond",
                        json={"request_id": rid, "response": {"value": "辣"}})
    replay = client.post(f"/v1/decisions/{did}/respond",
                         json={"request_id": rid, "response": {"value": "辣"}})
    assert first.json() == replay.json()  # 幂等去重
    assert client.post(f"/v1/decisions/{did}/respond",
                       json={"request_id": "r-nope", "response": {}}).status_code == 404


def test_feedback_accept_marks_completed(client):
    outcome = _create(client, slots={"taste_match": "辣"})
    assert outcome["status"] == "completed"
    did = outcome["decision_id"]
    result = client.post(f"/v1/decisions/{did}/feedback", json={"action": "accept"})
    assert result.json() == {"decision_id": did, "accepted": True}


def test_feedback_reject_records_memory(client, tmp_path):
    outcome = _create(client, slots={"taste_match": "辣"})
    did = outcome["decision_id"]
    result = client.post(f"/v1/decisions/{did}/feedback",
                         json={"action": "adjust", "content": "下次要便宜的", "owner_id": "u9",
                               "scene": "food"})
    assert result.json()["recorded"] is True
    stored = client.get("/v1/memory/u9").json()
    assert any("下次要便宜的" in r["content"] for r in stored["records"])
    assert client.delete("/v1/memory/u9").json()["deleted"] >= 1


def test_memory_endpoints_empty(client):
    stored = client.get("/v1/memory/nobody").json()
    assert stored == {"owner_id": "nobody", "records": []}


def test_tools_and_capabilities(client):
    tools = client.get("/v1/tools").json()
    assert {"location", "weather", "poi_search"} <= {t["name"] for t in tools}
    caps = client.get("/v1/capabilities").json()
    assert "server" in caps["by_side"]


def test_unknown_decision_404(client):
    assert client.get("/v1/decisions/d-nope").status_code == 404
    assert client.post("/v1/decisions/d-nope/respond",
                       json={"request_id": "x", "response": {}}).status_code == 404


def test_auth_enforced_when_enabled(decide, tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_HTTP_KEY", "secret123")
    app = create_http_app(decide, data_dir=tmp_path, runtime_dir=tmp_path / "rt")
    app.state.http_auth = {"enabled": True, "api_key_env": "TEST_HTTP_KEY"}
    client = TestClient(app)
    assert client.get("/v1/tools").status_code == 401  # 无头
    assert client.get("/v1/tools", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/v1/tools", headers={"X-API-Key": "secret123"}).status_code == 200
    assert client.get("/v1/tools", headers={"Authorization": "Bearer secret123"}).status_code == 200
    assert client.get("/healthz").status_code == 200  # 健康检查不设防


def test_restart_restore_pending_decision(decide, tmp_path):
    """红线 13：挂起快照落 runtime/ → 新进程（新 kernel+app）恢复续答。"""
    runtime_dir = tmp_path / "runtime"
    first = TestClient(create_http_app(decide, data_dir=tmp_path, runtime_dir=runtime_dir))
    outcome = _create(first)
    did, rid = outcome["decision_id"], outcome["pending"]["request_id"]

    second = TestClient(create_http_app(decide, data_dir=tmp_path, runtime_dir=runtime_dir))
    final = second.post(f"/v1/decisions/{did}/respond",
                        json={"request_id": rid, "response": {"value": "辣"}})
    assert final.status_code == 200
    assert final.json()["status"] == "completed"
    assert final.json()["result"]["recommendation"]["candidate"]["id"] == "a"


def test_expired_pending_returns_expired_error(decide, tmp_path):
    """红线 13：过期 respond → expired 结构化错误（409 + DecisionError）。"""
    client = TestClient(create_http_app(decide, data_dir=tmp_path, runtime_dir=tmp_path / "rt"))
    outcome = _create(client)
    item = decide.kernel.pending.get(outcome["pending"]["request_id"])
    item.timeout_s = 1.0  # 挂起默认无超时；测试显式设置后模拟 sweeper 过期
    decide.kernel.pending.expire_due(now=time.time() + 10_000)
    response = client.post(
        f"/v1/decisions/{outcome['decision_id']}/respond",
        json={"request_id": outcome["pending"]["request_id"], "response": {"value": "辣"}})
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "decision.expired"
