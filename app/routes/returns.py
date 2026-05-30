from __future__ import annotations

import logging
from typing import Any, Dict

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db_pool, require_role
from app.models.book import ReturnCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/returns", tags=["returns"])


@router.post("")
async def create_return_request(
    body: ReturnCreate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    loan = await pool.fetchrow(
        """
        SELECT id, book_id, lender_id, borrower_id, status
        FROM book_loans
        WHERE book_id = $1 AND status = 'active'
        LIMIT 1
        """,
        str(body.book_id),
    )
    if not loan:
        raise HTTPException(status_code=404, detail="No active loan found for this book")

    if loan["borrower_id"] != user["id"] and loan["lender_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="No active loan found for this book")

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO return_requests (book_id, loan_id, requested_by, status)
            VALUES ($1, $2, $3, 'pending')
            RETURNING id, book_id, loan_id, requested_by, status, requested_at
            """,
            str(loan["book_id"]),
            str(loan["id"]),
            user["id"],
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="A pending return request already exists for this loan")

    return dict(row)


@router.post("/{return_id}/approve")
async def approve_return(
    return_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            ret = await conn.fetchrow(
                """
                SELECT rr.id, rr.book_id, rr.loan_id, rr.status,
                       bl.lender_id, bl.borrower_id
                FROM return_requests rr
                JOIN book_loans bl ON rr.loan_id = bl.id
                WHERE rr.id = $1
                FOR UPDATE OF rr
                """,
                return_id,
            )
            if not ret:
                raise HTTPException(status_code=404, detail="Return request not found")
            if ret["lender_id"] != user["id"]:
                raise HTTPException(status_code=404, detail="Return request not found")
            if ret["status"] != "pending":
                raise HTTPException(status_code=409, detail="Return request is no longer pending")

            updated_return = await conn.fetchrow(
                """
                UPDATE return_requests SET status = 'approved', resolved_at = NOW()
                WHERE id = $1
                RETURNING id, book_id, loan_id, status, resolved_at
                """,
                return_id,
            )

            await conn.execute(
                "UPDATE book_loans SET status = 'returned', returned_at = NOW() WHERE id = $1",
                str(ret["loan_id"]),
            )

            await conn.execute(
                "UPDATE books SET status = 'available' WHERE id = $1",
                str(ret["book_id"]),
            )

    return dict(updated_return)
