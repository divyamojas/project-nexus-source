from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from supabase import create_client

from app.limiter import limiter
from app.request_logging import RequestLoggingMiddleware

from app.config import settings
from app.db import (
    apply_pending_migrations,
    create_pool,
    ensure_migrations_table,
    refresh_and_persist_snapshot,
)
from app.routes import (
    admin,
    auth,
    books,
    catalog,
    feedback,
    legal,
    library,
    loans,
    requests,
    returns,
    reviews,
    saved,
    schema,
    sync,
    transfers,
    users,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Supabase client
    app.state.supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    logger.info("Supabase client initialised")

    # 2. asyncpg pool (degraded if DB unavailable)
    try:
        app.state.db_pool = await create_pool(settings.DATABASE_URL)
        logger.info("Database pool created")
    except Exception as exc:
        logger.warning("DB unavailable: %s — starting in degraded mode", exc)
        app.state.db_pool = None

    # 3–4. Migrations (blocking — must finish before serving requests)
    app.state.schema_snapshot = None
    if app.state.db_pool:
        try:
            await ensure_migrations_table(app.state.db_pool)
            logger.info("Migrations table ensured")

            applied = await apply_pending_migrations(app.state.db_pool)
            if applied:
                logger.info("Applied migrations: %s", applied)
        except Exception as exc:
            logger.warning("Startup migrations failed: %s", exc)

        # 5. Schema snapshot — fire-and-forget so it never delays startup
        async def _snapshot_task():
            await refresh_and_persist_snapshot(app.state, app.state.db_pool)
            logger.info("Schema snapshot written")

        asyncio.create_task(_snapshot_task())

    yield

    if app.state.db_pool:
        await app.state.db_pool.close()
        logger.info("Database pool closed")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Leaflet API",
        description="Community book-sharing platform backend",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(books.router)
    app.include_router(catalog.router)
    app.include_router(library.router)
    app.include_router(requests.router)
    app.include_router(loans.router)
    app.include_router(transfers.router)
    app.include_router(returns.router)
    app.include_router(saved.router)
    app.include_router(reviews.router)
    app.include_router(users.router)
    app.include_router(admin.router)
    app.include_router(schema.router)
    app.include_router(legal.router)
    app.include_router(feedback.router)
    app.include_router(sync.router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "service": "leaflet-api"}

    return app


app = create_app()
