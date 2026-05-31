
import logging
from typing import Any, Dict

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.dependencies import get_db_pool, get_supabase, require_role
from app.models.book import ProfileUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def get_profile(
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT id, username, first_name, last_name, bio, avatar_url, role, approval_status, created_at
        FROM profiles
        WHERE id = $1
        """,
        user["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    return dict(row)


@router.put("/me")
async def update_profile(
    body: ProfileUpdate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    updates: list[str] = []
    values: list[Any] = []
    idx = 1

    if body.username is not None:
        updates.append(f"username = ${idx}")
        values.append(body.username)
        idx += 1
    if body.first_name is not None:
        updates.append(f"first_name = ${idx}")
        values.append(body.first_name)
        idx += 1
    if body.last_name is not None:
        updates.append(f"last_name = ${idx}")
        values.append(body.last_name)
        idx += 1
    if body.bio is not None:
        updates.append(f"bio = ${idx}")
        values.append(body.bio)
        idx += 1
    if body.avatar_url is not None:
        updates.append(f"avatar_url = ${idx}")
        values.append(body.avatar_url)
        idx += 1

    if not updates:
        row = await pool.fetchrow(
            "SELECT id, username, first_name, last_name, bio, avatar_url, role, approval_status, created_at FROM profiles WHERE id = $1",
            user["id"],
        )
        if not row:
            raise HTTPException(status_code=404, detail="Profile not found")
        return dict(row)

    values.append(user["id"])
    try:
        updated = await pool.fetchrow(
            f"UPDATE profiles SET {', '.join(updates)} WHERE id = ${idx} "
            "RETURNING id, username, first_name, last_name, bio, avatar_url, role, approval_status, created_at",
            *values,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Username already taken")

    if not updated:
        raise HTTPException(status_code=404, detail="Profile not found")
    return dict(updated)


@router.delete("/me")
async def delete_account(
    current_user: Dict[str, Any] = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
    supabase=Depends(get_supabase),
) -> Dict[str, str]:
    await pool.execute("DELETE FROM profiles WHERE id = $1", current_user["id"])
    try:
        supabase.auth.admin.delete_user(current_user["id"])
    except Exception as exc:
        logger.warning("Failed to delete Supabase user %s: %s", current_user["id"], exc)
    return {"message": "Account deleted"}


@router.delete("/me/data")
async def delete_own_data(
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, str]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM book_requests WHERE requested_by = $1 OR requested_to = $1",
                user["id"],
            )
            await conn.execute(
                "DELETE FROM book_loans WHERE lender_id = $1 OR borrower_id = $1",
                user["id"],
            )
            await conn.execute(
                "DELETE FROM books WHERE user_id = $1",
                user["id"],
            )
    return {"message": "Your data has been deleted"}
