from __future__ import annotations

import glob
import logging
import os
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")


async def create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    return pool


async def ensure_migrations_table(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.schema_migrations (
                id         SERIAL      PRIMARY KEY,
                filename   TEXT        UNIQUE NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


async def apply_pending_migrations(pool: asyncpg.Pool) -> List[str]:
    pattern = os.path.join(MIGRATIONS_DIR, "*.sql")
    files = sorted(glob.glob(pattern))
    applied: List[str] = []

    async with pool.acquire() as conn:
        existing = await conn.fetch("SELECT filename FROM public.schema_migrations")
        done = {row["filename"] for row in existing}

    for filepath in files:
        filename = os.path.basename(filepath)
        if filename in done:
            logger.debug("Migration already applied: %s", filename)
            continue

        logger.info("Applying migration: %s", filename)
        with open(filepath, "r", encoding="utf-8") as fh:
            sql = fh.read()

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO public.schema_migrations (filename) VALUES ($1) "
                        "ON CONFLICT (filename) DO NOTHING",
                        filename,
                    )
            applied.append(filename)
            logger.info("Migration applied: %s", filename)
        except Exception as exc:
            logger.warning("Migration %s failed (skipping): %s", filename, exc)

    return applied


async def take_schema_snapshot(pool: asyncpg.Pool) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        tables = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
            """
        )

        columns = await conn.fetch(
            """
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        )

        indexes = await conn.fetch(
            """
            SELECT indexname, tablename, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
            ORDER BY tablename, indexname
            """
        )

    snapshot: Dict[str, Any] = {
        "tables": [dict(r) for r in tables],
        "columns": [dict(r) for r in columns],
        "indexes": [dict(r) for r in indexes],
    }
    return snapshot


async def execute_raw(pool: asyncpg.Pool, sql: str) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        results = await conn.fetch(sql)
        return {"rows": [dict(r) for r in results], "count": len(results)}
