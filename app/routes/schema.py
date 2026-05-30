from __future__ import annotations

import logging
from typing import Any, Dict

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings
from app.db import apply_pending_migrations, execute_raw
from app.dependencies import get_db_pool, require_role
from app.models.schema import RawSqlRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schema", tags=["schema"])


@router.get("/snapshot")
async def get_snapshot(
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
) -> Dict[str, Any]:
    snapshot = getattr(request.app.state, "schema_snapshot", None)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="Schema snapshot not available")
    return snapshot


@router.post("/migrate")
async def run_migrations(
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    applied = await apply_pending_migrations(pool)
    return {"applied": applied, "count": len(applied)}


@router.post("/sql")
async def run_raw_sql(
    body: RawSqlRequest,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    if not settings.ENABLE_RAW_SQL:
        raise HTTPException(status_code=503, detail="Raw SQL execution is disabled")
    try:
        result = await execute_raw(pool, body.sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")
    return result
