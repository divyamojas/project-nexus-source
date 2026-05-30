from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_db_pool, require_role
from app.models.book import CatalogCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/search")
async def search_catalog(
    q: str = Query(..., min_length=1),
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, title, author, isbn, cover_url, created_at
        FROM books_catalog
        WHERE title ILIKE $1
        ORDER BY title
        LIMIT 5
        """,
        f"%{q}%",
    )
    return [dict(r) for r in rows]


@router.get("/lookup")
async def lookup_catalog(
    title: str = Query(...),
    author: str = Query(...),
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Optional[Dict[str, Any]]:
    row = await pool.fetchrow(
        """
        SELECT id, title, author, isbn, cover_url, created_at
        FROM books_catalog
        WHERE LOWER(title) = LOWER($1) AND LOWER(author) = LOWER($2)
        LIMIT 1
        """,
        title,
        author,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    return dict(row)


@router.post("")
async def create_catalog_entry(
    body: CatalogCreate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO books_catalog (title, author, isbn, cover_url, created_by)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, title, author, isbn, cover_url, created_by, created_at
            """,
            body.title,
            body.author,
            body.isbn,
            body.cover_url,
            user["id"],
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="ISBN already exists in catalog")

    return dict(row)


@router.get("/{catalog_id}")
async def get_catalog_entry(
    catalog_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    row = await pool.fetchrow(
        "SELECT id, title, author, isbn, cover_url, created_at FROM books_catalog WHERE id = $1",
        catalog_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    return dict(row)
