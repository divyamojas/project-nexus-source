
import logging
from typing import Any, Dict, List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db_pool, require_role
from app.models.book import LibraryBookAdd, LibraryCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["library"])


@router.get("")
async def list_libraries(
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, name, city, location, managed_by, is_self_service, created_at
        FROM libraries
        ORDER BY city, name
        """
    )
    return [dict(r) for r in rows]


@router.post("")
async def create_library(
    body: LibraryCreate,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO libraries (name, city, location, managed_by, is_self_service)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, name, city, location, managed_by, is_self_service, created_at
        """,
        body.name,
        body.city,
        body.location,
        user["id"],
        body.is_self_service,
    )
    return dict(row)


@router.get("/{library_id}")
async def get_library(
    library_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    lib = await pool.fetchrow(
        "SELECT id, name, city, location, managed_by, is_self_service, created_at FROM libraries WHERE id = $1",
        library_id,
    )
    if not lib:
        raise HTTPException(status_code=404, detail="Library not found")

    books = await pool.fetch(
        """
        SELECT b.id, b.status, b.condition, b.created_at, b.archived,
               c.id AS catalog_id, c.title, c.author, c.cover_url, c.isbn
        FROM books b
        JOIN books_catalog c ON b.catalog_id = c.id
        WHERE b.library_id = $1 AND b.archived = false
        ORDER BY c.title
        """,
        library_id,
    )
    return {**dict(lib), "books": [dict(b) for b in books]}


@router.post("/{library_id}/books")
async def add_book_to_library(
    library_id: str,
    body: LibraryBookAdd,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    lib = await pool.fetchrow(
        "SELECT id, managed_by FROM libraries WHERE id = $1",
        library_id,
    )
    if not lib:
        raise HTTPException(status_code=404, detail="Library not found")

    catalog = await pool.fetchrow(
        "SELECT id FROM books_catalog WHERE id = $1",
        str(body.catalog_id),
    )
    if not catalog:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    row = await pool.fetchrow(
        """
        INSERT INTO books (user_id, catalog_id, condition, book_source, library_id, status, archived)
        VALUES ($1, $2, $3, 'library', $4, 'available', false)
        ON CONFLICT (catalog_id, condition, user_id)
        DO UPDATE SET archived = false, library_id = EXCLUDED.library_id, book_source = 'library'
        RETURNING id, user_id, catalog_id, condition, book_source, library_id, status, archived, created_at
        """,
        str(lib["managed_by"]),
        str(body.catalog_id),
        body.condition,
        library_id,
    )
    return dict(row)


@router.delete("/{library_id}/books/{book_id}")
async def remove_book_from_library(
    library_id: str,
    book_id: str,
    user: Dict[str, Any] = Depends(require_role("admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    updated = await pool.fetchrow(
        """
        UPDATE books SET archived = true
        WHERE id = $1 AND library_id = $2
        RETURNING id, archived
        """,
        book_id,
        library_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Book not found in this library")
    return dict(updated)


@router.post("/books/{book_id}/pickup")
async def pickup_library_book(
    book_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            book = await conn.fetchrow(
                """
                SELECT b.id, b.status, b.user_id, b.library_id, b.archived,
                       l.is_self_service
                FROM books b
                JOIN libraries l ON b.library_id = l.id
                WHERE b.id = $1
                FOR UPDATE OF b
                """,
                book_id,
            )
            if not book:
                raise HTTPException(status_code=404, detail="Book not found")
            if not book["is_self_service"]:
                raise HTTPException(
                    status_code=400,
                    detail="Use the Reserve flow for admin-managed libraries",
                )
            if book["archived"]:
                raise HTTPException(status_code=409, detail="Book is archived")
            if book["status"] != "available":
                raise HTTPException(status_code=409, detail="Book is not available")
            if book["user_id"] == user["id"]:
                raise HTTPException(status_code=400, detail="You manage this library book")

            # Mark any pending reservation by this user as accepted
            await conn.execute(
                """
                UPDATE book_requests SET status = 'accepted', updated_at = NOW()
                WHERE book_id = $1 AND requested_by = $2 AND status = 'pending'
                """,
                book_id,
                user["id"],
            )

            loan = await conn.fetchrow(
                """
                INSERT INTO book_loans (book_id, lender_id, borrower_id, status)
                VALUES ($1, $2, $3, 'active')
                RETURNING id, book_id, lender_id, borrower_id, status, loaned_at
                """,
                book_id,
                str(book["user_id"]),
                user["id"],
            )
            await conn.execute(
                "UPDATE books SET status = 'lent' WHERE id = $1",
                book_id,
            )

    return dict(loan)
