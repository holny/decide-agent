"""HTTP auth: API Key / Bearer，config http.auth 可配（默认关闭，红线 15）。"""
import os
import secrets

from fastapi import HTTPException, Request


def api_key_dependency(request: Request) -> None:
    """FastAPI 依赖：enabled 时校验 X-API-Key 或 Authorization: Bearer。"""
    auth = getattr(request.app.state, "http_auth", None)
    if not auth or not auth.get("enabled"):
        return
    expected = os.environ.get(auth.get("api_key_env") or "", "")
    if not expected:  # 配置了启用但 env 缺失 → 服务端配置错误
        raise HTTPException(status_code=500, detail="auth enabled but api key env missing")
    provided = request.headers.get("x-api-key") or _bearer(request)
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else None
