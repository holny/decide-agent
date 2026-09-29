"""ID generation: decision_id / request_id (uuid-backed, collision-safe)."""
import uuid


def new_decision_id() -> str:
    return f"d-{uuid.uuid4().hex[:12]}"


def new_request_id() -> str:
    return f"r-{uuid.uuid4().hex[:12]}"
