from __future__ import annotations

import logging
from typing import Any, Dict, List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db_pool, require_role
from app.models.book import SavedBookCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/saved", tags=["saved"])


@router.get("")
async def list_saved(
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT
          sb.id, sb.book_id, sb.catalog_id, sb.created_at,
          b.status AS book_status, b.condition, b.user_id AS owner_id, b.archived,
          c.title, c.author, c.cover_url,
          _br.id     AS outgoing_request_id,
          _br.status AS outgoing_request_status
        FROM saved_books sb
        JOIN books b ON sb.book_id = b.id
        JOIN books_catalog c ON sb.catalog_id = c.id
        LEFT JOIN LATERAL (
          SELECT id, status FROM book_requests
          WHERE book_id = sb.book_id AND requested_by = $1 AND status = 'pending'
          LIMIT 1
        ) _br ON true
        WHERE sb.user_id = $1
        ORDER BY sb.created_at DESC
        """,
        user["id"],
    )
    return [dict(r) for r in rows]


@router.post("")
async def save_book(
    body: SavedBookCreate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    book = await pool.fetchrow(
        "SELECT id FROM books WHERE id = $1",
        str(body.book_id),
    )
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO saved_books (user_id, book_id, catalog_id)
            VALUES ($1, $2, $3)
            RETURNING id, user_id, book_id, catalog_id, created_at
            """,
            user["id"],
            str(body.book_id),
            str(body.catalog_id),
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Book already saved")

    return dict(row)


@router.delete("/{book_id}")
async def unsave_book(
    book_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, str]:
    deleted = await pool.fetchrow(
        "DELETE FROM saved_books WHERE user_id = $1 AND book_id = $2 RETURNING id",
        user["id"],
        book_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved book not found")
    return {"message": "Book removed from saved list"}
