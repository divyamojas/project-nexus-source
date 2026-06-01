from __future__ import annotations

import asyncio
import base64
import json as json_mod
import logging
import time
import uuid
from typing import Optional
from urllib.parse import parse_qs

import asyncpg
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_SKIP_PATHS = frozenset({
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/admin/logs",
    "/admin/logs/stats",
})

_SENSITIVE_KEYS = frozenset({
    "password", "token", "secret", "key", "access_token",
    "refresh_token", "authorization", "api_key", "client_secret",
})

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_MAX_BODY_BYTES  = 4_096   # 4 KB cap on captured request body
_MAX_ERROR_BYTES = 2_048   # 2 KB cap on captured error response body

_insert_count = 0
_PRUNE_EVERY = 100
_MAX_ROWS = 10_000


def _extract_user_id(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        segment = auth[7:].split(".")[1]
        segment += "=" * (-len(segment) % 4)
        payload = json_mod.loads(base64.b64decode(segment))
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception:
        return None


def _redact(obj, depth: int = 0) -> object:
    """Recursively redact sensitive keys from a dict/list."""
    if depth > 5:
        return obj
    if isinstance(obj, dict):
        return {
            k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else _redact(v, depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(item, depth + 1) for item in obj]
    return obj


def _parse_query_params(query_string: bytes) -> Optional[dict]:
    if not query_string:
        return None
    try:
        parsed = parse_qs(query_string.decode("utf-8"), keep_blank_values=True)
        # Flatten single-value lists for readability
        return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
    except Exception:
        return None


async def _read_body(receive: Receive) -> tuple[bytes, Receive]:
    """
    Drain the request body from `receive`, then return the raw bytes
    along with a replay callable so the route handler still gets the body.
    """
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        elif message["type"] == "http.disconnect":
            # Client disconnected before body was fully sent
            async def _disconnected():
                return {"type": "http.disconnect"}
            return b"".join(chunks), _disconnected

    raw = b"".join(chunks)
    sent = False

    async def replay():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": raw, "more_body": False}
        return {"type": "http.disconnect"}

    return raw, replay


def _parse_request_body(raw: bytes, content_type: str) -> Optional[dict]:
    if not raw or len(raw) > _MAX_BODY_BYTES:
        return None
    if "application/json" not in content_type:
        return None
    try:
        parsed = json_mod.loads(raw)
        return _redact(parsed) if isinstance(parsed, (dict, list)) else None
    except Exception:
        return None


def _parse_error_body(raw: bytes) -> Optional[dict]:
    if not raw:
        return None
    clipped = raw[:_MAX_ERROR_BYTES]
    try:
        parsed = json_mod.loads(clipped)
        return parsed if isinstance(parsed, (dict, list)) else {"raw": clipped.decode("utf-8", errors="replace")}
    except Exception:
        return {"raw": clipped.decode("utf-8", errors="replace")}


async def _write_log(
    pool: asyncpg.Pool,
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    user_id: Optional[str],
    ip_address: Optional[str],
    query_params: Optional[dict],
    request_body: Optional[dict],
    error_detail: Optional[dict],
    user_agent: Optional[str],
    request_id: str,
) -> None:
    global _insert_count
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.request_logs
                    (method, path, status_code, duration_ms, user_id, ip_address,
                     query_params, request_body, error_detail, user_agent, request_id)
                VALUES ($1, $2, $3, $4, $5::uuid, $6,
                        $7::jsonb, $8::jsonb, $9::jsonb, $10, $11)
                """,
                method, path, status_code, duration_ms, user_id, ip_address,
                json_mod.dumps(query_params) if query_params is not None else None,
                json_mod.dumps(request_body) if request_body is not None else None,
                json_mod.dumps(error_detail) if error_detail is not None else None,
                user_agent,
                request_id,
            )
            _insert_count += 1
            if _insert_count % _PRUNE_EVERY == 0:
                await conn.execute(
                    """
                    DELETE FROM public.request_logs
                    WHERE id <= (
                        SELECT id FROM public.request_logs
                        ORDER BY id DESC
                        LIMIT 1 OFFSET $1
                    )
                    """,
                    _MAX_ROWS,
                )
    except Exception as exc:
        logger.debug("Request log write failed: %s", exc)


class RequestLoggingMiddleware:
    """Pure ASGI middleware — avoids BaseHTTPMiddleware's response-buffering overhead."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        if path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        start = time.monotonic()
        method = request.method
        user_id = _extract_user_id(request)
        user_agent = request.headers.get("user-agent")

        forwarded = request.headers.get("x-forwarded-for", "")
        ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else None
        )

        query_params = _parse_query_params(scope.get("query_string", b""))

        # Read and replay body only for methods that carry payloads
        request_body: Optional[dict] = None
        if method in _BODY_METHODS:
            content_type = request.headers.get("content-type", "")
            raw_body, replay_receive = await _read_body(receive)
            request_body = _parse_request_body(raw_body, content_type)
            receive = replay_receive  # hand replay to the app

        status_code = 500
        error_chunks: list[bytes] = []
        capturing_error = False

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code, capturing_error
            if message["type"] == "http.response.start":
                status_code = message["status"]
                capturing_error = status_code >= 400
            elif message["type"] == "http.response.body" and capturing_error:
                error_chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            error_detail = _parse_error_body(b"".join(error_chunks)) if error_chunks else None
            pool = getattr(request.app.state, "db_pool", None)
            if pool:
                asyncio.create_task(
                    _write_log(
                        pool, method, path, status_code, duration_ms,
                        user_id, ip, query_params, request_body,
                        error_detail, user_agent, request_id,
                    )
                )
