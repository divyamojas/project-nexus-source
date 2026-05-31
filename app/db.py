from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any, Dict, List

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")


async def create_pool(database_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(database_url, min_size=2, max_size=10)


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
    # All queries are scoped to the 'public' schema only.
    # Supabase has thousands of functions/views in internal schemas (auth, storage,
    # extensions, graphql, pgsodium …) — querying pg_get_functiondef across all of
    # them causes startup to hang for minutes.
    async with pool.acquire() as conn:
        tables = await conn.fetch(
            """
            SELECT ist.table_schema, ist.table_name, ist.table_type,
                   ist.is_insertable_into AS is_insertable,
                   COALESCE(pt.rowsecurity, false) AS rls_enabled
            FROM information_schema.tables ist
            LEFT JOIN pg_tables pt
                   ON pt.schemaname = ist.table_schema
                  AND pt.tablename  = ist.table_name
            WHERE ist.table_schema = 'public'
            ORDER BY ist.table_name
            """
        )

        columns = await conn.fetch(
            """
            SELECT table_name, ordinal_position, column_name,
                   data_type, udt_name, is_nullable, column_default,
                   identity_generation AS identity, is_generated,
                   generation_expression, numeric_precision, numeric_scale,
                   datetime_precision, character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        )

        indexes = await conn.fetch(
            """
            SELECT tablename AS table_name, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
            ORDER BY tablename, indexname
            """
        )

        constraints = await conn.fetch(
            """
            SELECT tc.table_name, tc.constraint_name, tc.constraint_type,
                   kcu.column_name,
                   ccu.table_name  AS foreign_table_name,
                   ccu.column_name AS foreign_column_name,
                   rc.update_rule, rc.delete_rule,
                   cc.check_clause AS definition
            FROM information_schema.table_constraints tc
            LEFT JOIN information_schema.key_column_usage kcu
                   ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema    = kcu.table_schema
            LEFT JOIN information_schema.referential_constraints rc
                   ON tc.constraint_name  = rc.constraint_name
                  AND tc.table_schema     = rc.constraint_schema
            LEFT JOIN information_schema.constraint_column_usage ccu
                   ON rc.unique_constraint_name   = ccu.constraint_name
                  AND rc.unique_constraint_schema = ccu.constraint_schema
            LEFT JOIN information_schema.check_constraints cc
                   ON tc.constraint_name = cc.constraint_name
                  AND tc.table_schema    = cc.constraint_schema
            WHERE tc.table_schema = 'public'
            ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position
            """
        )

        triggers = await conn.fetch(
            """
            SELECT trigger_name,
                   event_object_table AS table_name,
                   event_manipulation, action_timing,
                   action_statement   AS definition
            FROM information_schema.triggers
            WHERE trigger_schema = 'public'
            ORDER BY trigger_name
            """
        )

        views = await conn.fetch(
            """
            SELECT table_name AS view_name,
                   pg_get_viewdef(
                       format('public.%I', table_name)::regclass, true
                   ) AS definition
            FROM information_schema.views
            WHERE table_schema = 'public'
            ORDER BY table_name
            """
        )

        # Only SQL/PL languages in public schema — skips thousands of Supabase
        # internal C/internal functions that would block on pg_get_functiondef.
        functions = await conn.fetch(
            """
            SELECT p.proname                        AS function_name,
                   pg_get_function_result(p.oid)    AS result_type,
                   pg_get_function_arguments(p.oid) AS arguments,
                   l.lanname                        AS language,
                   p.prokind,
                   p.prosecdef                      AS security_definer,
                   pg_get_functiondef(p.oid)        AS definition
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            JOIN pg_language  l ON l.oid = p.prolang
            WHERE n.nspname = 'public'
              AND l.lanname IN ('sql', 'plpgsql', 'plv8', 'plpython3u')
            ORDER BY p.proname
            """
        )

        sequences = await conn.fetch(
            """
            SELECT sequence_name, data_type, start_value,
                   minimum_value, maximum_value, increment, cycle_option
            FROM information_schema.sequences
            WHERE sequence_schema = 'public'
            ORDER BY sequence_name
            """
        )

        rls_policies = await conn.fetch(
            """
            SELECT tablename AS table_name, policyname,
                   permissive, roles, cmd, qual, with_check
            FROM pg_policies
            WHERE schemaname = 'public'
            ORDER BY tablename, policyname
            """
        )

    return {
        "tables":       [dict(r) for r in tables],
        "columns":      [dict(r) for r in columns],
        "indexes":      [dict(r) for r in indexes],
        "constraints":  [dict(r) for r in constraints],
        "triggers":     [dict(r) for r in triggers],
        "views":        [dict(r) for r in views],
        "functions":    [dict(r) for r in functions],
        "sequences":    [dict(r) for r in sequences],
        "rls_policies": [dict(r) for r in rls_policies],
    }


async def refresh_and_persist_snapshot(app_state: Any, pool: asyncpg.Pool) -> None:
    try:
        snapshot = await take_schema_snapshot(pool)
        app_state.schema_snapshot = snapshot
        with open("schema_snapshot.json", "w") as fh:
            json.dump(snapshot, fh, indent=2, default=str)
        logger.info("Schema snapshot refreshed")
    except Exception as exc:
        logger.warning("Schema snapshot refresh failed: %s", exc)


async def execute_raw(pool: asyncpg.Pool, sql: str) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        results = await conn.fetch(sql)
        return {"rows": [dict(r) for r in results], "count": len(results)}
