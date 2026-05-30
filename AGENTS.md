# Agents Guide — project-nexus-source (Leaflet Backend)

## Purpose
FastAPI backend for Leaflet. Owns all data routes, JWT verification, and RBAC.
Backend is source of truth for all data; Supabase handles auth + storage.

## Out of Scope
- Frontend rendering (project-nexus-light)
- Docker orchestration (project-nexus root)
- Supabase project configuration
- Realtime subscriptions (frontend connects directly)
- File upload proxy (frontend uploads directly to Supabase Storage)

## Key Files
| File | Role |
|------|------|
| `app/main.py` | App factory, CORS, lifespan, router registration |
| `app/auth.py` | JWT verification: JWKS → HS256 → remote fallback |
| `app/db.py` | asyncpg pool, migration runner, schema snapshot |
| `app/dependencies.py` | `get_current_user`, `require_role(role)` |
| `app/routes/*.py` | Route handlers (one file per resource) |
| `app/models/*.py` | Pydantic v2 request/response models |
| `migrations/0001_initial_schema.sql` | All public tables (sourced from supabase_schema/) |
| `scripts/db.js` | Dev CLI: `schema` dumps live DB schema; `update` applies update.sql |

## Run (from root via Docker)
```bash
cd .. && ./start.sh          # starts all services incl. api profile
./start.sh --logs=api        # tail backend logs
./start.sh --attach=api      # shell into container
```

## Test
```bash
cd .. && ./start.sh --test
# or:
docker compose --profile api run --rm api python -m pytest tests/ -v
```

## Rules
- No ORM — asyncpg parameterized queries only
- `user_id` always from JWT, never from request body/query params
- Invalid/missing JWT → 401; owned-resource-not-found → 404 (not 403)
- Cannot delete/demote yourself via admin routes
- All config from `.env` — no hardcoded secrets
