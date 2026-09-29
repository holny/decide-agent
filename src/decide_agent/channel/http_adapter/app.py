"""HTTP adapter (P3): decisions / SSE events / respond / feedback / memory / capabilities.

马甲纪律：只经 app.entry 触达内核；挂起快照落 runtime/decisions/（重启可恢复续答，
红线 13）；事件在调用边界追加 + sweeper 周期补发 expired（§5.4）。
memory 端点按 owner 现建存储（路径前缀隔离，P2-6）。
"""
import asyncio
import logging
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from decide_agent.channel.http_adapter.auth import api_key_dependency


def _sanitize_owner_id(raw: str) -> str:
    """owner_id 进文件系统路径（evolved/users/{owner_id}/）→ 剥离路径穿越与非法字符。"""
    import re as _re

    return _re.sub(r"[^\w\u4e00-\u9fa5-]", "_", raw).strip("_") or "anonymous"
from decide_agent.channel.shared.app_like import DecisionAppLike
from decide_agent.channel.shared.events import EventStore, EventType
from decide_agent.channel.shared.snapshots import DecisionSnapshotStore
from decide_agent.channel.shared.sweeper import PendingSweeper
from decide_agent.config.loader import get_data_dir, get_http_auth
from decide_agent.config.paths import Paths
from decide_agent.core.memory.manager import MemoryService, build_memory_store
from decide_agent.core.workflow.pending import PendingExpiredError
from decide_agent.schemas.memory import MemoryKind
from decide_agent.schemas.workflow import DecisionRequest

log = logging.getLogger(__name__)


def create_http_app(
    decide: DecisionAppLike,
    *,
    data_dir: Path | None = None,
    runtime_dir: Path | None = None,
) -> FastAPI:
    """decide 必须由装配根注入（app/main.py 或测试）——通道禁 import app。"""
    paths = Paths.resolve(data_dir or get_data_dir())
    runtime = Path(runtime_dir) if runtime_dir else paths.root / "runtime"
    evolved_dir = paths.root / "evolved"

    app = FastAPI(title="decide-agent", version="0.2.0")
    app.state.decide = decide
    app.state.events = EventStore()
    app.state.snapshots = DecisionSnapshotStore(runtime)
    app.state.http_auth = get_http_auth()
    app.state.evolved_dir = evolved_dir

    def memory_for(owner_id: str) -> MemoryService:
        return MemoryService(
            build_memory_store(evolved_dir, owner_id), raw_dir=runtime / "raw",
        )

    # 重启恢复：runtime/decisions/*.json → kernel 会话重建（红线 13）
    for snap in app.state.snapshots.load_all():
        if not snap.get("kernel"):
            continue
        try:
            decide.kernel.restore(snap["outcome"]["decision_id"], snap["kernel"])
        except Exception as exc:  # noqa: BLE001 — 不可恢复 → respond 走 expired 路径
            print(f"[http] snapshot restore skipped: {exc}")
            continue

    sweeper = PendingSweeper(decide, app.state.events).start()

    @app.on_event("shutdown")
    def _stop_sweeper() -> None:
        sweeper.stop()

    # ---------------------------------------------------------------- health

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    # ---------------------------------------------------------------- decisions

    @app.post("/v1/decisions", dependencies=[Depends(api_key_dependency)])
    def create_decision(body: dict) -> dict:
        request = DecisionRequest.model_validate({
            key: value for key, value in body.items() if key in DecisionRequest.model_fields
        })
        outcome = decide.make_decision(request)
        _emit_outcome(app.state.events, outcome)
        _persist(app, outcome)
        return outcome.model_dump(mode="json")

    @app.get("/v1/decisions/{decision_id}", dependencies=[Depends(api_key_dependency)])
    def get_decision(decision_id: str) -> dict:
        outcome = app.state.snapshots.load_outcome(decision_id)
        if outcome is None:
            raise HTTPException(404, detail="unknown decision")
        return outcome.model_dump(mode="json")

    @app.post("/v1/decisions/{decision_id}/respond", dependencies=[Depends(api_key_dependency)])
    def respond(decision_id: str, body: dict) -> dict:
        request_id = body.get("request_id")
        if not request_id:
            raise HTTPException(422, detail="request_id required")
        try:
            outcome = decide.respond(decision_id, request_id, body.get("response") or {})
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc).strip("'")) from exc
        except PendingExpiredError as exc:
            raise HTTPException(409, detail={"error": exc.error.model_dump()}) from exc
        _emit_outcome(app.state.events, outcome)
        _persist(app, outcome)
        return outcome.model_dump(mode="json")

    @app.post("/v1/decisions/{decision_id}/feedback", dependencies=[Depends(api_key_dependency)])
    def feedback(decision_id: str, body: dict) -> dict:
        action = body.get("action")
        if action == "accept":
            try:
                decide.kernel.accept(decision_id)
            except KeyError as exc:
                raise HTTPException(404, detail=str(exc).strip("'")) from exc
            _emit(app.state.events, decision_id, EventType.COMPLETED, {"accepted": True})
            _attribute_feedback(app, decision_id, body)
            return {"decision_id": decision_id, "accepted": True}
        if action in ("reject", "adjust"):
            content = str(body.get("content", ""))[:200]
            owner_id = _sanitize_owner_id(str(body.get("owner_id", "anonymous")))
            if content:
                memory_for(owner_id).remember(
                    owner_id, content, MemoryKind.PREFERENCE,
                    str(body.get("scene", "global")),
                    source="feedback", confidence=0.7, now=time.time(),
                )
            return {"decision_id": decision_id, "recorded": bool(content)}
        raise HTTPException(422, detail="action must be accept|reject|adjust")

    # ---------------------------------------------------------------- SSE

    @app.get("/v1/decisions/{decision_id}/events", dependencies=[Depends(api_key_dependency)])
    async def events(decision_id: str, after_seq: int = -1, max_wait_s: float = 30.0) -> StreamingResponse:
        event_store: EventStore = app.state.events

        async def stream():
            cursor = after_seq
            terminal = False
            deadline = asyncio.get_event_loop().time() + max(0.1, max_wait_s)
            while not terminal:
                batch = event_store.replay(decision_id, cursor)
                for envelope in batch:
                    cursor = envelope.seq
                    if envelope.type in (EventType.COMPLETED, EventType.EXPIRED):
                        terminal = True
                    yield f"data: {envelope.model_dump_json()}\n\n"
                if terminal or asyncio.get_event_loop().time() > deadline:
                    return
                if not batch:
                    await asyncio.sleep(0.05)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # ---------------------------------------------------------------- memory (P2-6 隐私)

    @app.get("/v1/memory/{owner_id}", dependencies=[Depends(api_key_dependency)])
    def export_memory(owner_id: str) -> dict:
        records = memory_for(owner_id).export(owner_id)
        return {"owner_id": owner_id, "records": [r.model_dump(mode="json") for r in records]}

    @app.delete("/v1/memory/{owner_id}", dependencies=[Depends(api_key_dependency)])
    def forget_memory(owner_id: str, record_id: str | None = None) -> dict:
        return {"owner_id": owner_id, "deleted": memory_for(owner_id).forget(owner_id, record_id)}

    # ---------------------------------------------------------------- discovery

    @app.get("/v1/tools", dependencies=[Depends(api_key_dependency)])
    def tools() -> list[dict]:
        return [c.model_dump() for c in decide.list_capabilities()]

    @app.get("/v1/capabilities", dependencies=[Depends(api_key_dependency)])
    def capabilities() -> dict:
        items = [c.model_dump() for c in decide.list_capabilities()]
        by_side: dict[str, list[str]] = {}
        for item in items:
            by_side.setdefault(item["side"], []).append(item["name"])
        return {"capabilities": items, "by_side": by_side}

    return app


def _emit(events: EventStore, decision_id: str, event_type: EventType, payload: dict | None = None) -> None:
    events.append(decision_id, event_type, payload)


def _emit_outcome(events: EventStore, outcome) -> None:
    _emit(events, outcome.decision_id, EventType.STAGE, {"state": outcome.state})
    if outcome.status == "require_action" and outcome.pending is not None:
        _emit(events, outcome.decision_id, EventType.PENDING_REQUEST,
              outcome.pending.model_dump(mode="json"))
    if outcome.status == "completed" and outcome.state == "completed":
        _emit(events, outcome.decision_id, EventType.COMPLETED,
              outcome.result.model_dump(mode="json") if outcome.result else {})


def _attribute_feedback(app: FastAPI, decision_id: str, body: dict) -> None:
    """P4-5 反馈归因：接受 → top-2 维度获得正向调制（owner 分区存储）。"""
    from decide_agent.core.weights.modulation import (
        WeightModulationStore,
        attribute_feedback,
    )

    outcome = app.state.snapshots.load_outcome(decision_id)
    if outcome is None or outcome.result is None or outcome.result.recommendation is None:
        return
    owner_id = str(body.get("owner_id", "anonymous"))
    scene = outcome.result.scene
    dims = [
        {"dimension": ds.dimension, "score": ds.score, "weight": ds.weight}
        for ds in outcome.result.recommendation.dimension_scores
    ]
    store = WeightModulationStore(app.state.evolved_dir, owner_id)
    attribute_feedback(store, scene, dims, accepted=True)


def _persist(app: FastAPI, outcome) -> None:
    """挂起/完成均落快照：重启后 respond 可续（score 缓存不恢复→重算，结果不变）。"""
    try:
        snap = app.state.decide.kernel.snapshot(outcome.decision_id)
    except KeyError:
        return
    app.state.snapshots.save(outcome, snap)
