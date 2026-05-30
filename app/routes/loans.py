from __future__ import annotations

import logging
from typing import Any, Dict, List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db_pool, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/loans", tags=["loans"])


@router.get("")
async def list_loans(
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT bl.id, bl.book_id, bl.lender_id, bl.borrower_id,
               bl.status, bl.loaned_at, bl.due_date, bl.returned_at, bl.notes,
               c.title, c.author, c.cover_url
        FROM book_loans bl
        JOIN books b ON bl.book_id = b.id
        JOIN books_catalog c ON b.catalog_id = c.id
        WHERE bl.lender_id = $1 OR bl.borrower_id = $1
        ORDER BY bl.loaned_at DESC
        """,
        user["id"],
    )
    return [dict(r) for r in rows]


@router.get("/book/{book_id}/active")
async def active_loan_for_book(
    book_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT id, book_id, lender_id, borrower_id, status, loaned_at, due_date, returned_at, notes
        FROM book_loans
        WHERE book_id = $1 AND status = 'active'
        LIMIT 1
        """,
        book_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No active loan found for this book")
    return dict(row)


@router.patch("/{loan_id}/return")
async def return_loan(
    loan_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    loan = await pool.fetchrow(
        "SELECT id, lender_id, borrower_id, status FROM book_loans WHERE id = $1",
        loan_id,
    )
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan["lender_id"] != user["id"] and loan["borrower_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan["status"] != "active":
        raise HTTPException(status_code=409, detail="Loan is not active")

    updated = await pool.fetchrow(
        """
        UPDATE book_loans SET status = 'returned', returned_at = NOW()
        WHERE id = $1
        RETURNING id, book_id, lender_id, borrower_id, status, loaned_at, returned_at
        """,
        loan_id,
    )
    return dict(updated)
