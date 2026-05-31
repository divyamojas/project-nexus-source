
import logging
from typing import Any, Dict, List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_db_pool, require_role
from app.models.book import BookReviewCreate, UserReviewCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/book/{book_id}")
async def get_book_reviews(
    book_id: str,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, book_id, reviewer_id, rating, comment, created_at
        FROM book_reviews
        WHERE book_id = $1
        ORDER BY created_at DESC
        """,
        book_id,
    )
    return [dict(r) for r in rows]


@router.post("/book")
async def create_book_review(
    body: BookReviewCreate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO book_reviews (book_id, reviewer_id, rating, comment)
            VALUES ($1, $2, $3, $4)
            RETURNING id, book_id, reviewer_id, rating, comment, created_at
            """,
            str(body.book_id),
            user["id"],
            body.rating,
            body.comment,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="You have already reviewed this book")
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=404, detail="Book not found")

    return dict(row)


@router.get("/user/{user_id}")
async def get_user_reviews(
    user_id: str,
    current_user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, reviewee_id, reviewer_id, rating, comment, created_at
        FROM user_reviews
        WHERE reviewee_id = $1
        ORDER BY created_at DESC
        """,
        user_id,
    )
    return [dict(r) for r in rows]


@router.post("/user")
async def create_user_review(
    body: UserReviewCreate,
    user: Dict[str, Any] = Depends(require_role("user")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    if str(body.reviewee_id) == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot review yourself")

    row = await pool.fetchrow(
        """
        INSERT INTO user_reviews (reviewee_id, reviewer_id, rating, comment)
        VALUES ($1, $2, $3, $4)
        RETURNING id, reviewee_id, reviewer_id, rating, comment, created_at
        """,
        str(body.reviewee_id),
        user["id"],
        body.rating,
        body.comment,
    )
    return dict(row)
