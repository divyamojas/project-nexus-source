
import logging
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_db_pool, invalidate_role_cache, require_role
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
    invalidate_role_cache(target_id)
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
    invalidate_role_cache(target_id)
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
        SELECT bl.id, bl.book_id, bl.lender_id, bl.borrower_id,
               bl.status, bl.loaned_at, bl.due_date, bl.returned_at, bl.notes,
               c.title, c.author,
               b.book_source, b.library_id,
               l.name AS library_name, l.location AS library_location
        FROM book_loans bl
        JOIN books b ON bl.book_id = b.id
        JOIN books_catalog c ON b.catalog_id = c.id
        LEFT JOIN libraries l ON b.library_id = l.id
        ORDER BY bl.loaned_at DESC
        """
    )
    return [dict(r) for r in rows]


@router.get("/logs")
async def get_request_logs(
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    method: Optional[str] = Query(None),
    path: Optional[str] = Query(None),
    status_gte: Optional[int] = Query(None),
    status_lte: Optional[int] = Query(None),
    user_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
    conditions = ["1=1"]
    params: List[Any] = []

    def _p(val: Any) -> str:
        params.append(val)
        return f"${len(params)}"

    if method:
        conditions.append(f"method = {_p(method.upper())}")
    if path:
        conditions.append(f"path ILIKE {_p('%' + path + '%')}")
    if status_gte is not None:
        conditions.append(f"status_code >= {_p(status_gte)}")
    if status_lte is not None:
        conditions.append(f"status_code <= {_p(status_lte)}")
    if user_id:
        conditions.append(f"rl.user_id = {_p(user_id)}::uuid")

    where = " AND ".join(conditions)

    rows = await pool.fetch(
        f"""
        SELECT rl.id, rl.method, rl.path, rl.status_code,
               ROUND(rl.duration_ms::numeric, 2) AS duration_ms,
               rl.user_id, p.username, rl.ip_address, rl.created_at,
               rl.query_params, rl.request_body, rl.error_detail,
               rl.user_agent, rl.request_id
        FROM public.request_logs rl
        LEFT JOIN public.profiles p ON p.id = rl.user_id
        WHERE {where}
        ORDER BY rl.created_at DESC
        LIMIT {_p(limit)} OFFSET {_p(offset)}
        """,
        *params,
    )

    count_params: List[Any] = params[: len(params) - 2]
    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM public.request_logs rl WHERE {where}",
        *count_params,
    )

    def _row(r):
        d = dict(r)
        # asyncpg returns JSONB as strings; parse them so the API sends real JSON
        for col in ("query_params", "request_body", "error_detail"):
            if isinstance(d.get(col), str):
                import json as _j
                try:
                    d[col] = _j.loads(d[col])
                except Exception:
                    pass
        return d

    return {"total": total, "logs": [_row(r) for r in rows]}


@router.delete("/logs")
async def delete_request_logs(
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
    ids: Optional[str] = Query(None, description="Comma-separated log IDs; omit to delete by filter or all"),
    method: Optional[str] = Query(None),
    path: Optional[str] = Query(None),
    status_gte: Optional[int] = Query(None),
    status_lte: Optional[int] = Query(None),
    user_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """Delete log rows by explicit IDs, or by filter (all matching), or clear the entire table."""
    if ids:
        id_list = [int(i.strip()) for i in ids.split(",") if i.strip().isdigit()]
        if not id_list:
            raise HTTPException(status_code=400, detail="No valid IDs provided")
        deleted = await pool.fetchval(
            "WITH d AS (DELETE FROM public.request_logs WHERE id = ANY($1::bigint[]) RETURNING 1) SELECT COUNT(*) FROM d",
            id_list,
        )
    else:
        conditions = ["1=1"]
        params: List[Any] = []

        def _p(val: Any) -> str:
            params.append(val)
            return f"${len(params)}"

        if method:
            conditions.append(f"method = {_p(method.upper())}")
        if path:
            conditions.append(f"path ILIKE {_p('%' + path + '%')}")
        if status_gte is not None:
            conditions.append(f"status_code >= {_p(status_gte)}")
        if status_lte is not None:
            conditions.append(f"status_code <= {_p(status_lte)}")
        if user_id:
            conditions.append(f"user_id = {_p(user_id)}::uuid")
        where = " AND ".join(conditions)
        deleted = await pool.fetchval(
            f"WITH d AS (DELETE FROM public.request_logs WHERE {where} RETURNING 1) SELECT COUNT(*) FROM d",
            *params,
        )
    return {"deleted": deleted}


@router.get("/logs/stats")
async def get_request_log_stats(
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)                                                           AS total,
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour')    AS last_hour,
            COUNT(*) FILTER (WHERE status_code >= 500)                         AS errors_total,
            COUNT(*) FILTER (WHERE status_code >= 500
                              AND  created_at > NOW() - INTERVAL '1 hour')    AS errors_last_hour,
            ROUND(AVG(duration_ms)::numeric, 2)                               AS avg_duration_ms,
            ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP
                  (ORDER BY duration_ms)::numeric, 2)                         AS p95_duration_ms
        FROM public.request_logs
        """
    )

    top_paths = await pool.fetch(
        """
        SELECT path, COUNT(*) AS count,
               ROUND(AVG(duration_ms)::numeric, 2) AS avg_ms
        FROM public.request_logs
        WHERE created_at > NOW() - INTERVAL '1 hour'
        GROUP BY path
        ORDER BY count DESC
        LIMIT 10
        """
    )

    return {
        **dict(row),
        "top_paths": [dict(r) for r in top_paths],
    }


@router.post("/loans/{loan_id}/complete")
async def admin_complete_loan(
    loan_id: str,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            loan = await conn.fetchrow(
                "SELECT id, book_id, lender_id, borrower_id, status, loaned_at, returned_at FROM book_loans WHERE id = $1 FOR UPDATE",
                loan_id,
            )
            if not loan:
                raise HTTPException(status_code=404, detail="Loan not found")
            if loan["status"] == "returned":
                return dict(loan)
            updated = await conn.fetchrow(
                "UPDATE book_loans SET status = 'returned', returned_at = NOW() WHERE id = $1 "
                "RETURNING id, book_id, lender_id, borrower_id, status, returned_at",
                loan_id,
            )
            await conn.execute(
                "UPDATE books SET status = 'available' WHERE id = $1",
                str(updated["book_id"]),
            )
    return dict(updated)
