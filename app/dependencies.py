from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import asyncpg
from fastapi import Depends, HTTPException, Request

from app.auth import get_current_user  # re-export

ROLE_ORDER = {"user": 0, "admin": 1, "super_admin": 2}

_ROLE_CACHE_TTL = 60  # seconds

# {user_id: (role, approval_status, cached_at)}
_role_cache: Dict[str, Tuple[str, str, float]] = {}


def invalidate_role_cache(user_id: str) -> None:
    _role_cache.pop(user_id, None)


def _get_cached_role(user_id: str) -> Optional[Tuple[str, str]]:
    entry = _role_cache.get(user_id)
    if entry and (time.monotonic() - entry[2]) < _ROLE_CACHE_TTL:
        return entry[0], entry[1]
    _role_cache.pop(user_id, None)
    return None


def _set_cached_role(user_id: str, role: str, approval_status: str) -> None:
    _role_cache[user_id] = (role, approval_status, time.monotonic())


def get_supabase(request: Request):
    return request.app.state.supabase


def get_db_pool(request: Request) -> asyncpg.Pool:
    pool = request.app.state.db_pool
    if pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return pool


def require_role(minimum_role: str):
    async def dependency(
        current_user: Dict[str, Any] = Depends(get_current_user),
        pool: asyncpg.Pool = Depends(get_db_pool),
    ) -> Dict[str, Any]:
        user_id = current_user["id"]
        cached = _get_cached_role(user_id)

        if cached:
            role, approval_status = cached
        else:
            row = await pool.fetchrow(
                "SELECT role, approval_status FROM profiles WHERE id = $1",
                user_id,
            )
            if not row:
                raise HTTPException(status_code=401, detail="Profile not found")
            role, approval_status = row["role"], row["approval_status"]
            _set_cached_role(user_id, role, approval_status)

        if ROLE_ORDER.get(role, -1) < ROLE_ORDER.get(minimum_role, 0):
            raise HTTPException(status_code=403, detail="Insufficient role")

        if minimum_role == "user" and approval_status != "approved":
            raise HTTPException(status_code=403, detail="Account pending approval")

        return {**current_user, "role": role}

    return dependency


__all__ = [
    "get_supabase",
    "get_db_pool",
    "get_current_user",
    "require_role",
    "ROLE_ORDER",
]
