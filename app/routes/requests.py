from __future__ import annotations

import logging
from typing import Any, Dict, List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db_pool, require_role
from app.models.book import RequestCreate, RequestStatusUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["requests"])


@router.post("")
async def create_request(
    body: RequestCreate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    book = await pool.fetchrow(
        "SELECT id, user_id, status, archived FROM books WHERE id = $1",
        str(body.book_id),
    )
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if book["user_id"] == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot request your own book")
    if book["status"] != "available":
        raise HTTPException(status_code=409, detail="Book is not available")
    if book["archived"]:
        raise HTTPException(status_code=409, detail="Book is archived")

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO book_requests (book_id, requested_by, requested_to, status, message)
            VALUES ($1, $2, $3, 'pending', $4)
            RETURNING id, book_id, requested_by, requested_to, status, message, created_at
            """,
            str(body.book_id),
            user["id"],
            str(book["user_id"]),
            body.message,
        )
    except asyncpg.UniqueViolationError:
        existing = await pool.fetchrow(
            """
            SELECT id, book_id, requested_by, requested_to, status, message, created_at
            FROM book_requests
            WHERE book_id = $1 AND requested_by = $2 AND status = 'pending'
            """,
            str(body.book_id),
            user["id"],
        )
        return dict(existing)

    return dict(row)


@router.get("/incoming")
async def incoming_requests(
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT br.id, br.book_id, br.requested_by, br.requested_to,
               br.status, br.message, br.created_at, br.updated_at,
               b.catalog_id, c.title, c.author, c.cover_url
        FROM book_requests br
        JOIN books b ON br.book_id = b.id
        JOIN books_catalog c ON b.catalog_id = c.id
        WHERE br.requested_to = $1 AND br.status = 'pending'
        ORDER BY br.created_at DESC
        """,
        user["id"],
    )
    return [dict(r) for r in rows]


@router.get("/outgoing")
async def outgoing_requests(
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT br.id, br.book_id, br.requested_by, br.requested_to,
               br.status, br.message, br.created_at, br.updated_at,
               b.catalog_id, c.title, c.author, c.cover_url
        FROM book_requests br
        JOIN books b ON br.book_id = b.id
        JOIN books_catalog c ON b.catalog_id = c.id
        WHERE br.requested_by = $1 AND br.status = 'pending'
        ORDER BY br.created_at DESC
        """,
        user["id"],
    )
    return [dict(r) for r in rows]


@router.get("/book/{book_id}")
async def requests_for_book(
    book_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, book_id, requested_by, requested_to, status, message, created_at, updated_at
        FROM book_requests
        WHERE book_id = $1
        ORDER BY created_at DESC
        """,
        book_id,
    )
    return [dict(r) for r in rows]


@router.patch("/{request_id}/status")
async def update_request_status(
    request_id: str,
    body: RequestStatusUpdate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    allowed_statuses = {"accepted", "rejected", "cancelled"}
    if body.status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Status must be one of: {', '.join(sorted(allowed_statuses))}",
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            req = await conn.fetchrow(
                "SELECT id, book_id, requested_by, requested_to, status FROM book_requests WHERE id = $1 FOR UPDATE",
                request_id,
            )
            if not req:
                raise HTTPException(status_code=404, detail="Request not found")

            if body.status == "cancelled":
                if req["requested_by"] != user["id"]:
                    raise HTTPException(status_code=404, detail="Request not found")
            else:
                if req["requested_to"] != user["id"]:
                    raise HTTPException(status_code=404, detail="Request not found")

            if req["status"] != "pending":
                raise HTTPException(status_code=409, detail="Request is no longer pending")

            updated = await conn.fetchrow(
                """
                UPDATE book_requests SET status = $1, updated_at = NOW()
                WHERE id = $2
                RETURNING id, book_id, requested_by, requested_to, status, message, created_at, updated_at
                """,
                body.status,
                request_id,
            )

            if body.status == "accepted":
                await conn.execute(
                    "UPDATE books SET status = 'scheduled' WHERE id = $1",
                    str(req["book_id"]),
                )
                await conn.execute(
                    """
                    INSERT INTO transfers (request_id, book_id, from_user, to_user, status)
                    VALUES ($1, $2, $3, $4, 'pending')
                    ON CONFLICT (book_id, from_user, to_user) DO NOTHING
                    """,
                    request_id,
                    str(req["book_id"]),
                    str(req["requested_to"]),
                    str(req["requested_by"]),
                )

    return dict(updated)
