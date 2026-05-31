
import logging
from typing import Any, Dict

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import get_db_pool, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackCreate(BaseModel):
    message: str


@router.post("")
async def submit_feedback(
    body: FeedbackCreate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    row = await pool.fetchrow(
        """
        INSERT INTO feedback (message, email)
        VALUES ($1, $2)
        RETURNING id, message, email, created_at
        """,
        body.message,
        user.get("email", ""),
    )
    return dict(row)
