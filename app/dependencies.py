from __future__ import annotations

from typing import Any, Dict

import asyncpg
from fastapi import Depends, HTTPException, Request

from app.auth import get_current_user  # re-export

ROLE_ORDER = {"user": 0, "admin": 1, "super_admin": 2}


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
        row = await pool.fetchrow(
            "SELECT role, approval_status FROM profiles WHERE id = $1",
            current_user["id"],
        )
        if not row:
            raise HTTPException(status_code=401, detail="Profile not found")

        role: str = row["role"]
        if ROLE_ORDER.get(role, -1) < ROLE_ORDER.get(minimum_role, 0):
            raise HTTPException(status_code=403, detail="Insufficient role")

        if minimum_role == "user" and row["approval_status"] != "approved":
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
