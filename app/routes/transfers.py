from __future__ import annotations

import logging
from typing import Any, Dict, List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db_pool, require_role
from app.models.book import TransferUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transfers", tags=["transfers"])


@router.get("")
async def list_transfers(
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT t.id, t.request_id, t.book_id, t.from_user, t.to_user,
               t.status, t.scheduled_at, t.completed_at, t.created_at,
               c.title, c.author, c.cover_url
        FROM transfers t
        JOIN books b ON t.book_id = b.id
        JOIN books_catalog c ON b.catalog_id = c.id
        WHERE t.from_user = $1 OR t.to_user = $1
        ORDER BY t.created_at DESC
        """,
        user["id"],
    )
    return [dict(r) for r in rows]


@router.patch("/{transfer_id}")
async def update_transfer(
    transfer_id: str,
    body: TransferUpdate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    transfer = await pool.fetchrow(
        "SELECT id, from_user, to_user, status FROM transfers WHERE id = $1",
        transfer_id,
    )
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if transfer["from_user"] != user["id"] and transfer["to_user"] != user["id"]:
        raise HTTPException(status_code=404, detail="Transfer not found")

    updates: List[str] = []
    values: List[Any] = []
    idx = 1

    if body.status is not None:
        updates.append(f"status = ${idx}")
        values.append(body.status)
        idx += 1

    if body.scheduled_at is not None:
        updates.append(f"scheduled_at = ${idx}")
        values.append(body.scheduled_at)
        idx += 1

    if not updates:
        return dict(transfer)

    values.append(transfer_id)
    updated = await pool.fetchrow(
        f"UPDATE transfers SET {', '.join(updates)} WHERE id = ${idx} "
        "RETURNING id, request_id, book_id, from_user, to_user, status, scheduled_at, completed_at, created_at",
        *values,
    )
    return dict(updated)


@router.post("/{transfer_id}/complete")
async def complete_transfer(
    transfer_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            transfer = await conn.fetchrow(
                "SELECT id, book_id, from_user, to_user, status FROM transfers WHERE id = $1 FOR UPDATE",
                transfer_id,
            )
            if not transfer:
                raise HTTPException(status_code=404, detail="Transfer not found")
            if transfer["from_user"] != user["id"] and transfer["to_user"] != user["id"]:
                raise HTTPException(status_code=404, detail="Transfer not found")
            if transfer["status"] == "transferred":
                raise HTTPException(status_code=409, detail="Transfer already completed")

            updated_transfer = await conn.fetchrow(
                """
                UPDATE transfers SET status = 'transferred', completed_at = NOW()
                WHERE id = $1
                RETURNING id, book_id, from_user, to_user, status, completed_at
                """,
                transfer_id,
            )

            loan = await conn.fetchrow(
                """
                INSERT INTO book_loans (book_id, lender_id, borrower_id, status)
                VALUES ($1, $2, $3, 'active')
                RETURNING id, book_id, lender_id, borrower_id, status, loaned_at
                """,
                str(transfer["book_id"]),
                str(transfer["from_user"]),
                str(transfer["to_user"]),
            )

            await conn.execute(
                "UPDATE books SET status = 'lent' WHERE id = $1",
                str(transfer["book_id"]),
            )

    return {**dict(updated_transfer), "loan": dict(loan)}
