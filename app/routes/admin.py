from __future__ import annotations

import logging
from typing import Any, Dict, List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db_pool, require_role
from app.models.admin import ArchiveUpdate, ApprovalUpdate, RoleUpdate, StatsResponse
from app.models.book import RequestStatusUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats")
async def get_stats(
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> StatsResponse:
    row = await pool.fetchrow(
        """
        SELECT
          (SELECT COUNT(*) FROM profiles)      AS users,
          (SELECT COUNT(*) FROM books)          AS books,
          (SELECT COUNT(*) FROM book_requests)  AS requests,
          (SELECT COUNT(*) FROM book_loans)     AS loans
        """
    )
    return StatsResponse(
        users=row["users"],
        books=row["books"],
        requests=row["requests"],
        loans=row["loans"],
    )


@router.get("/users")
async def list_users(
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT p.id, p.username, p.first_name, p.last_name, p.bio, p.avatar_url,
               p.role, p.approval_status, p.created_at, au.email
        FROM profiles p
        JOIN auth.users au ON au.id = p.id
        ORDER BY p.created_at DESC
        """
    )
    return [dict(r) for r in rows]


@router.put("/users/{target_id}/role")
async def update_user_role(
    target_id: str,
    body: RoleUpdate,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    if target_id == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot change your own role")

    valid_roles = {"user", "admin", "super_admin"}
    if body.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(sorted(valid_roles))}")

    updated = await pool.fetchrow(
        "UPDATE profiles SET role = $1 WHERE id = $2 RETURNING id, username, role, approval_status",
        body.role,
        target_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(updated)


@router.put("/users/{target_id}/approval")
async def update_user_approval(
    target_id: str,
    body: ApprovalUpdate,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    valid_statuses = {"pending", "approved", "rejected"}
    if body.approval_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid approval_status. Must be one of: {', '.join(sorted(valid_statuses))}",
        )

    updated = await pool.fetchrow(
        "UPDATE profiles SET approval_status = $1 WHERE id = $2 RETURNING id, username, role, approval_status",
        body.approval_status,
        target_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(updated)


@router.delete("/users/{target_id}")
async def delete_user(
    target_id: str,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, str]:
    if target_id == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    deleted = await pool.fetchrow("DELETE FROM profiles WHERE id = $1 RETURNING id", target_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted"}


@router.get("/books")
async def list_all_books(
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT b.id, b.user_id, b.catalog_id, b.condition, b.status, b.archived, b.created_at,
               c.title, c.author, c.isbn, c.cover_url
        FROM books b
        JOIN books_catalog c ON b.catalog_id = c.id
        ORDER BY b.created_at DESC
        """
    )
    return [dict(r) for r in rows]


@router.patch("/books/{book_id}/archive")
async def admin_archive_book(
    book_id: str,
    body: ArchiveUpdate,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    updated = await pool.fetchrow(
        "UPDATE books SET archived = $1 WHERE id = $2 RETURNING id, archived",
        body.archived,
        book_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Book not found")
    return dict(updated)


@router.get("/requests")
async def list_all_requests(
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, book_id, requested_by, requested_to, status, message, created_at, updated_at
        FROM book_requests
        ORDER BY created_at DESC
        """
    )
    return [dict(r) for r in rows]


@router.patch("/requests/{request_id}/status")
async def admin_update_request_status(
    request_id: str,
    body: RequestStatusUpdate,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    if not body.status:
        raise HTTPException(status_code=400, detail="status is required")

    updated = await pool.fetchrow(
        "UPDATE book_requests SET status = $1, updated_at = NOW() WHERE id = $2 "
        "RETURNING id, book_id, requested_by, requested_to, status, updated_at",
        body.status,
        request_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Request not found")
    return dict(updated)


@router.get("/loans")
async def list_all_loans(
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, book_id, lender_id, borrower_id, status, loaned_at, due_date, returned_at, notes
        FROM book_loans
        ORDER BY loaned_at DESC
        """
    )
    return [dict(r) for r in rows]


@router.post("/loans/{loan_id}/complete")
async def admin_complete_loan(
    loan_id: str,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchrow(
                "UPDATE book_loans SET status = 'returned', returned_at = NOW() WHERE id = $1 "
                "RETURNING id, book_id, lender_id, borrower_id, status, returned_at",
                loan_id,
            )
            if not updated:
                raise HTTPException(status_code=404, detail="Loan not found")
            await conn.execute(
                "UPDATE books SET status = 'available' WHERE id = $1",
                str(updated["book_id"]),
            )
    return dict(updated)
