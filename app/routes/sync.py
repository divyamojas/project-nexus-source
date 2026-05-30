from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.config import settings
from app.dependencies import require_role
from app.services.s3_sync import sync_full

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status")
async def sync_status(
    user: Dict[str, Any] = Depends(require_role("user")),
) -> Dict[str, Any]:
    return {
        "enabled": settings.s3_enabled,
        "status": "ok",
    }


@router.post("/full")
async def full_sync(
    user: Dict[str, Any] = Depends(require_role("user")),
) -> Dict[str, Any]:
    if not settings.s3_enabled:
        return {"enabled": False, "message": "S3 sync is disabled"}

    await sync_full()
    return {"enabled": True, "message": "Full sync triggered"}
