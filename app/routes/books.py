from __future__ import annotations

import logging
from typing import Any, Dict, List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db_pool, require_role
from app.models.admin import ArchiveUpdate
from app.models.book import BookCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/books", tags=["books"])


def _shape(row: dict) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "condition": row["condition"],
        "archived": row["archived"],
        "created_at": row["created_at"],
        "catalog": {
            "id": row["catalog_id"],
            "title": row["title"],
            "author": row["author"],
            "cover_url": row["cover_url"],
            "isbn": row["isbn"],
        },
        "is_saved": row["is_saved"],
        "borrowed_by": row["borrowed_by"],
        "return_request_id": row["return_request_id"],
        "request_status": row["request_status"],
        "request_id": row["request_id"],
    }


ENRICHED_BOOKS_SQL = """
SELECT
  b.id, b.status, b.condition, b.created_at, b.user_id, b.archived,
  c.id AS catalog_id, c.title, c.author, c.cover_url, c.isbn,
  EXISTS(
    SELECT 1 FROM saved_books sb
    WHERE sb.book_id = b.id AND sb.user_id = $1
  ) AS is_saved,
  (SELECT borrower_id FROM book_loans bl
   WHERE bl.book_id = b.id AND bl.status = 'active' LIMIT 1
  ) AS borrowed_by,
  (SELECT id FROM return_requests rr
   WHERE rr.book_id = b.id AND rr.status = 'pending' LIMIT 1
  ) AS return_request_id,
  _br.status AS request_status,
  _br.id     AS request_id
FROM books b
JOIN books_catalog c ON b.catalog_id = c.id
LEFT JOIN LATERAL (
  SELECT id, status FROM book_requests
  WHERE book_id = b.id AND requested_by = $1 AND status = 'pending'
  LIMIT 1
) _br ON true
"""


@router.get("")
async def list_books(
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(ENRICHED_BOOKS_SQL + " ORDER BY b.created_at DESC", user["id"])
    return [_shape(dict(r)) for r in rows]


@router.post("")
async def create_book(
    body: BookCreate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO books (user_id, catalog_id, condition, status, archived)
            VALUES ($1, $2, $3, 'available', false)
            RETURNING id, user_id, catalog_id, condition, status, archived, created_at
            """,
            user["id"],
            str(body.catalog_id),
            body.condition,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail="You already have this book with the same condition listed.",
        )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    return dict(row)


@router.get("/{book_id}")
async def get_book(
    book_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    row = await pool.fetchrow(
        ENRICHED_BOOKS_SQL + " WHERE b.id = $2",
        user["id"],
        book_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    return _shape(dict(row))


@router.delete("/{book_id}")
async def delete_book(
    book_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, str]:
    deleted = await pool.fetchrow(
        "DELETE FROM books WHERE id = $1 AND user_id = $2 RETURNING id",
        book_id,
        user["id"],
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Book not found")
    return {"message": "Book deleted"}


@router.patch("/{book_id}/archive")
async def archive_book(
    book_id: str,
    body: ArchiveUpdate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    updated = await pool.fetchrow(
        "UPDATE books SET archived = $1 WHERE id = $2 AND user_id = $3 RETURNING id, archived",
        body.archived,
        book_id,
        user["id"],
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Book not found")
    return dict(updated)
