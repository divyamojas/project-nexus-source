
import logging
import re
from typing import Any, Dict, List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings
from app.db import apply_pending_migrations, execute_raw, refresh_and_persist_snapshot
from app.dependencies import get_db_pool, require_role
from app.models.schema import (
    AddColumnRequest,
    CreateIndexRequest,
    FunctionDDLRequest,
    RLSPolicyCreate,
    RLSPolicyUpdate,
    RawSqlRequest,
    ToggleRLSRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schema", tags=["schema"])

_IDENT_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_$]*$')
_TYPE_RE   = re.compile(r'^[a-zA-Z][a-zA-Z0-9_ (),\[\]]*$')


def _qi(name: str) -> str:
    """Validate and double-quote a PostgreSQL identifier to prevent injection."""
    if not _IDENT_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid identifier: {name!r}")
    return f'"{name}"'


def _safe_type(t: str) -> str:
    t = t.strip()
    if not _TYPE_RE.match(t):
        raise HTTPException(status_code=400, detail=f"Invalid data type: {t!r}")
    return t


def _roles_sql(roles: List[str]) -> str:
    return "PUBLIC" if not roles else ", ".join(_qi(r) for r in roles)


# ─── Read / utility ───────────────────────────────────────────────────────────

@router.get("/snapshot")
async def get_snapshot(
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
) -> Dict[str, Any]:
    snapshot = getattr(request.app.state, "schema_snapshot", None)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="Schema snapshot not available")
    return snapshot


@router.post("/refresh")
async def refresh_snapshot(
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": "Schema snapshot refreshed"}


@router.post("/migrate")
async def run_migrations(
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    applied = await apply_pending_migrations(pool)
    return {"applied": applied, "count": len(applied)}


@router.post("/sql")
async def run_raw_sql(
    body: RawSqlRequest,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    if not settings.ENABLE_RAW_SQL:
        raise HTTPException(status_code=503, detail="Raw SQL execution is disabled")
    try:
        result = await execute_raw(pool, body.sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")
    return result


# ─── Column management ────────────────────────────────────────────────────────

@router.post("/tables/{table_name}/columns")
async def add_column(
    table_name: str,
    body: AddColumnRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    t   = _qi(table_name)
    col = _qi(body.column_name)
    dtype = _safe_type(body.data_type)

    sql = f"ALTER TABLE public.{t} ADD COLUMN {col} {dtype}"
    if not body.nullable:
        sql += " NOT NULL"
    if body.default is not None:
        if not body.default.strip():
            raise HTTPException(400, "Default expression must not be blank when provided")
        sql += f" DEFAULT {body.default}"

    try:
        async with pool.acquire() as conn:
            await conn.execute(sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": f"Column {body.column_name!r} added to {table_name!r}"}


@router.delete("/tables/{table_name}/columns/{column_name}")
async def drop_column(
    table_name: str,
    column_name: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    t   = _qi(table_name)
    col = _qi(column_name)
    sql = f"ALTER TABLE public.{t} DROP COLUMN {col}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": f"Column {column_name!r} dropped from {table_name!r}"}


# ─── RLS toggle ───────────────────────────────────────────────────────────────

@router.patch("/tables/{table_name}/rls")
async def toggle_rls(
    table_name: str,
    body: ToggleRLSRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    t    = _qi(table_name)
    verb = "ENABLE" if body.enabled else "DISABLE"
    sql  = f"ALTER TABLE public.{t} {verb} ROW LEVEL SECURITY"
    try:
        async with pool.acquire() as conn:
            await conn.execute(sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": f"RLS {'enabled' if body.enabled else 'disabled'} on {table_name!r}"}


# ─── RLS policy CRUD ─────────────────────────────────────────────────────────

@router.post("/rls")
async def create_rls_policy(
    body: RLSPolicyCreate,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    t = _qi(body.table_name)
    p = _qi(body.policy_name)

    permissive = body.permissive.upper()
    if permissive not in ("PERMISSIVE", "RESTRICTIVE"):
        raise HTTPException(400, "permissive must be PERMISSIVE or RESTRICTIVE")

    command = body.command.upper()
    if command not in ("ALL", "SELECT", "INSERT", "UPDATE", "DELETE"):
        raise HTTPException(400, "command must be ALL, SELECT, INSERT, UPDATE, or DELETE")

    sql = (
        f"CREATE POLICY {p} ON public.{t} "
        f"AS {permissive} FOR {command} TO {_roles_sql(body.roles)}"
    )
    if body.using:
        sql += f" USING ({body.using})"
    if body.with_check:
        sql += f" WITH CHECK ({body.with_check})"

    try:
        async with pool.acquire() as conn:
            await conn.execute(sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": f"Policy {body.policy_name!r} created on {body.table_name!r}"}


@router.put("/rls/{table_name}/{policy_name}")
async def update_rls_policy(
    table_name: str,
    policy_name: str,
    body: RLSPolicyUpdate,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    snapshot = getattr(request.app.state, "schema_snapshot", None)
    current: dict | None = None
    if snapshot:
        for pol in snapshot.get("rls_policies", []):
            if pol["table_name"] == table_name and pol["policyname"] == policy_name:
                current = pol
                break
    if current is None:
        raise HTTPException(404, f"Policy {policy_name!r} on {table_name!r} not found in snapshot")

    t = _qi(table_name)
    p = _qi(policy_name)

    permissive = (body.permissive or current["permissive"] or "PERMISSIVE").upper()

    _CMD_MAP = {"r": "SELECT", "a": "INSERT", "w": "UPDATE", "d": "DELETE", "*": "ALL"}
    current_cmd = _CMD_MAP.get((current.get("cmd") or "*").lower(), "ALL")
    command = (body.command or current_cmd).upper()

    roles   = body.roles if body.roles is not None else (current.get("roles") or [])
    using   = body.using   if body.using   is not None else current.get("qual")
    with_check = body.with_check if body.with_check is not None else current.get("with_check")

    new_sql = (
        f"CREATE POLICY {p} ON public.{t} "
        f"AS {permissive} FOR {command} TO {_roles_sql(roles)}"
    )
    if using:
        new_sql += f" USING ({using})"
    if with_check:
        new_sql += f" WITH CHECK ({with_check})"

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"DROP POLICY {p} ON public.{t}")
                await conn.execute(new_sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": f"Policy {policy_name!r} updated on {table_name!r}"}


@router.delete("/rls/{table_name}/{policy_name}")
async def delete_rls_policy(
    table_name: str,
    policy_name: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    t = _qi(table_name)
    p = _qi(policy_name)
    sql = f"DROP POLICY {p} ON public.{t}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": f"Policy {policy_name!r} dropped from {table_name!r}"}


# ─── Functions ────────────────────────────────────────────────────────────────

@router.post("/functions")
async def upsert_function(
    body: FunctionDDLRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    if not body.definition.strip():
        raise HTTPException(400, "Function definition must not be empty")
    try:
        async with pool.acquire() as conn:
            await conn.execute(body.definition)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": "Function created/updated"}


@router.delete("/functions/{schema_name}/{function_name}")
async def drop_function(
    schema_name: str,
    function_name: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    s = _qi(schema_name)
    f = _qi(function_name)
    sql = f"DROP FUNCTION IF EXISTS {s}.{f}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": f"Function {schema_name}.{function_name} dropped"}


# ─── Indexes ──────────────────────────────────────────────────────────────────

@router.post("/tables/{table_name}/indexes")
async def create_index(
    table_name: str,
    body: CreateIndexRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    t    = _qi(table_name)
    idx  = _qi(body.index_name)
    cols = ", ".join(_qi(c) for c in body.columns)
    unique = "UNIQUE " if body.unique else ""

    sql = f"CREATE {unique}INDEX {idx} ON public.{t} ({cols})"
    if body.where_clause:
        sql += f" WHERE ({body.where_clause})"

    try:
        async with pool.acquire() as conn:
            await conn.execute(sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": f"Index {body.index_name!r} created on {table_name!r}"}


@router.delete("/indexes/{index_name}")
async def drop_index(
    index_name: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_role("super_admin")),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> Dict[str, Any]:
    idx = _qi(index_name)
    sql = f"DROP INDEX IF EXISTS public.{idx}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {exc}")

    await refresh_and_persist_snapshot(request.app.state, pool)
    return {"message": f"Index {index_name!r} dropped"}
