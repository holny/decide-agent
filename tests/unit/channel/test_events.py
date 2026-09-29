"""EventStore tests: seq monotonic, after_seq replay, isolation per decision."""
from decide_agent.channel.shared.events import EventStore, EventType


def test_seq_monotonic_and_replay():
    store = EventStore()
    store.append("d1", EventType.STAGE, {"state": "collecting"})
    store.append("d1", EventType.PENDING_REQUEST, {"request_id": "r1"})
    store.append("d1", EventType.COMPLETED, {})
    assert [e.seq for e in store.replay("d1")] == [0, 1, 2]
    assert store.last_seq("d1") == 2
    after = store.replay("d1", after_seq=1)  # 断线续传：after_seq 之后
    assert [e.seq for e in after] == [2] and after[0].type is EventType.COMPLETED


def test_isolation_and_empty():
    store = EventStore()
    store.append("d1", EventType.STAGE)
    assert store.replay("d2") == [] and store.last_seq("d2") == -1
    store.append("d2", EventType.STAGE)
    assert store.replay("d1")[0].decision_id == "d1"
