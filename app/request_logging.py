from __future__ import annotations

import asyncio
import base64
import json as json_mod
import logging
import time
from typing import Optional

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


async def _write_log(
    pool: asyncpg.Pool,
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    user_id: Optional[str],
    ip_address: Optional[str],
) -> None:
    global _insert_count
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.request_logs
                    (method, path, status_code, duration_ms, user_id, ip_address)
                VALUES ($1, $2, $3, $4, $5::uuid, $6)
                """,
                method, path, status_code, duration_ms, user_id, ip_address,
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

        start = time.monotonic()
        user_id = _extract_user_id(request)
        forwarded = request.headers.get("x-forwarded-for", "")
        ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else None
        )

        status_code = 500

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            pool = getattr(request.app.state, "db_pool", None)
            if pool:
                asyncio.create_task(
                    _write_log(pool, request.method, path,
                               status_code, duration_ms, user_id, ip)
                )
