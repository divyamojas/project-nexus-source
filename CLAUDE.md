# project-nexus-source — Leaflet Backend

## Current State
FastAPI backend for Leaflet (community book-sharing). Owns all data routes, JWT verification,
and RBAC. Supabase handles auth; Postgres is accessed directly via asyncpg (no ORM).

See the root `project-nexus/CLAUDE.md` for the complete cross-repo contract (auth flow,
API surface, data shapes, env vars, universal rules).

---

## Repo Boundaries

**Owns:**
- All FastAPI route handlers
- JWT verification (Supabase JWKS / HS256 / remote fallback)
- RBAC enforcement (`require_role` dependency)
- asyncpg DB queries (no ORM)
- Database migration files (numbered SQL)
- Schema snapshot at startup
- `supabase_schema/` — live schema JSON snapshots (tables, columns, indexes, RLS, functions, triggers, views, sequences)
- `scripts/db.js` — Node.js dev utility: `node db.js schema` refreshes `supabase_schema/`; `node db.js update` applies `update.sql` (smart parser, duplicate-safe) then refreshes

**Does NOT own:**
- Frontend rendering (in `project-nexus-light`)
- Docker orchestration (in `project-nexus`)
- Supabase project configuration

---

## Full File Map

```
app/
  main.py              FastAPI app factory, CORS middleware, lifespan hook, router registration
  auth.py              JWT verification via HTTPBearer; get_current_user dependency
  db.py                asyncpg pool factory, migration runner, schema snapshot helper
  dependencies.py      get_supabase(), get_db_pool(), get_current_user, require_role(role)
  routes/
    __init__.py
    auth.py            POST /auth/signup|login|logout|reset-password  GET /auth/me
    books.py           GET|POST /books  GET|DELETE|PATCH /books/{id}  PATCH /books/{id}/archive
    catalog.py         GET /catalog/search  GET /catalog/lookup  POST /catalog  GET /catalog/{id}
    requests.py        POST /requests  GET /requests/incoming|outgoing  GET /requests/book/{id}
                       PATCH /requests/{id}/status
    loans.py           GET /loans  GET /loans/book/{id}/active  PATCH /loans/{id}/return
    transfers.py       GET /transfers  PATCH /transfers/{id}  POST /transfers/{id}/complete
    returns.py         POST /returns  POST /returns/{id}/approve
    saved.py           GET|POST /saved  DELETE /saved/{book_id}
    reviews.py         GET|POST /reviews/book/{id}  GET|POST /reviews/user/{id}
    users.py           GET|PUT|DELETE /users/me  DELETE /users/me/data
    admin.py           /admin/stats|users|books|requests|loans (admin role required)
    schema.py          /schema/snapshot|migrate|sql (super_admin only)
    sync.py            /sync/status|full (gated by S3_SYNC_ENABLED)
    legal.py           GET /legal/privacy|terms (no auth)
    feedback.py        POST /feedback
  models/
    __init__.py
    auth.py            SignupRequest, LoginRequest, ResetPasswordRequest, TokenResponse, UserIdentity
    book.py            BookCreate, BookResponse, CatalogEntry, CatalogCreate, RequestCreate,
                       RequestStatusUpdate, LoanResponse, TransferUpdate, ReturnCreate,
                       SavedBookCreate, ReviewCreate
    admin.py           UserAdminView, RoleUpdate, ApprovalUpdate, StatsResponse
    schema.py          SchemaSnapshot, MigrationResult, RawSqlRequest
  services/
    __init__.py
    s3_sync.py         fire-and-forget S3 helpers; all functions are no-ops when S3_SYNC_ENABLED=false
migrations/
  0001_initial_schema.sql   CREATE TABLE for all public tables; indexes; schema_migrations tracker
supabase_schema/             Live DB schema snapshots (refreshed by scripts/getSchema.js)
  columns.json               Column definitions
  constraints.json           FK / UK constraints
  indexes.json               All indexes (including partial)
  current_RLS.json           Row-level security policies
  functions_ddl.json         Stored procedure DDL
  triggers.json              Trigger definitions
  views_ddl.json             View definitions
  sequences_ddl.json         Sequences
  tables.json                Table list
  getSchemaDump.sql          SQL template used by getSchema.js
  update.sql                 Scratch file for updateDB.js (cleared after apply)
scripts/                     Node.js dev utilities — NOT part of the Docker app
  db.js                      Single CLI: `schema` dumps schema; `update` applies update.sql then dumps
  package.json               Deps: pg + dotenv  (run: cd scripts && npm install)
tests/
  __init__.py
  test_auth.py         pytest — real DB, no mocks
  test_books.py
.env.example
requirements.txt
Dockerfile
.dockerignore
.gitignore
CLAUDE.md
AGENTS.md
```

---

## Stack

| Package | Version | Purpose |
|---------|---------|---------|
| Python | 3.11 | runtime |
| fastapi | 0.115.0 | web framework |
| uvicorn[standard] | 0.30.0 | ASGI server |
| pydantic | 2.7.0 | request/response models |
| pydantic-settings | 2.3.0 | env var loading |
| asyncpg | 0.29.0 | PostgreSQL client (NO ORM) |
| supabase | 2.5.0 | Supabase Python SDK (auth proxy) |
| python-jose[cryptography] | 3.3.0 | JWT decode/verify |
| httpx | 0.27.0 | async HTTP (JWKS fetch) |
| boto3 | 1.34.0 | S3 (optional) |
| python-multipart | 0.0.9 | form data |
| pytest | 8.2.0 | tests |
| pytest-asyncio | 0.23.0 | async test support |

---

## Startup Sequence (`app/main.py` lifespan)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Supabase AsyncClient
    app.state.supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # 2. asyncpg pool (degraded if DB unavailable)
    try:
        app.state.db_pool = await asyncpg.create_pool(DATABASE_URL)
    except Exception as e:
        logger.warning(f"DB unavailable: {e} — starting degraded")
        app.state.db_pool = None

    # 3. Ensure migrations table exists
    if app.state.db_pool:
        await ensure_migrations_table(app.state.db_pool)
        # 4. Apply pending migrations
        await apply_pending_migrations(app.state.db_pool)
        # 5. Write schema snapshot
        snapshot = await take_schema_snapshot(app.state.db_pool)
        app.state.schema_snapshot = snapshot
        with open("schema_snapshot.json", "w") as f:
            json.dump(snapshot, f, indent=2, default=str)

    yield

    if app.state.db_pool:
        await app.state.db_pool.close()
```

---

## auth.py — JWT Verification

```python
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer())
) -> dict:
    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "")

        if alg in ("RS256", "ES256"):
            payload = await verify_with_jwks(token)
        elif alg == "HS256" and settings.JWT_SECRET:
            payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        else:
            # Remote verification via Supabase
            payload = await verify_with_supabase(token)

        return {"id": payload["sub"], "email": payload.get("email", "")}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
```

---

## dependencies.py — RBAC

```python
ROLE_ORDER = {"user": 0, "admin": 1, "super_admin": 2}

def require_role(minimum_role: str):
    async def dependency(
        current_user: dict = Depends(get_current_user),
        pool = Depends(get_db_pool),
    ):
        row = await pool.fetchrow(
            "SELECT role, approval_status FROM profiles WHERE id = $1",
            current_user["id"]
        )
        if not row:
            raise HTTPException(401, "Profile not found")

        role = row["role"]
        if ROLE_ORDER.get(role, -1) < ROLE_ORDER.get(minimum_role, 0):
            raise HTTPException(403, "Insufficient role")

        # User-level routes also require approval
        if minimum_role == "user" and row["approval_status"] != "approved":
            raise HTTPException(403, "Account pending approval")

        return {**current_user, "role": role}
    return dependency

# Usage in routes:
# @router.get("/books")
# async def list_books(user=Depends(require_role("user")), pool=Depends(get_db_pool)):
```

---

## DB Query Patterns

Always use parameterized queries. Never interpolate user input into SQL.

```python
# CORRECT
rows = await pool.fetch(
    "SELECT * FROM books WHERE user_id = $1 AND archived = $2",
    user["id"], False
)

# WRONG — never do this
rows = await pool.fetch(f"SELECT * FROM books WHERE user_id = '{user['id']}'")
```

### Enriched books query (`GET /books`)
The books list endpoint must join multiple tables to return per-user context:

```sql
SELECT
  b.id, b.status, b.condition, b.created_at, b.user_id, b.archived,
  c.id   AS catalog_id, c.title, c.author, c.cover_url,
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
  (SELECT status FROM book_requests br
   WHERE br.book_id = b.id AND br.requested_by = $1 AND br.status = 'pending' LIMIT 1
  ) AS request_status,
  (SELECT id FROM book_requests br
   WHERE br.book_id = b.id AND br.requested_by = $1 AND br.status = 'pending' LIMIT 1
  ) AS request_id
FROM books b
JOIN books_catalog c ON b.catalog_id = c.id
-- $1 = current_user["id"]
```

---

## Key Business Logic

### Accept borrow request (`PATCH /requests/{id}/status` with `status=accepted`)
```python
# 1. Fetch request row
# 2. UPDATE book_requests SET status='accepted' WHERE id=$1
# 3. UPDATE books SET status='scheduled' WHERE id=<book_id>
# 4. INSERT INTO transfers (request_id, book_id, from_user, to_user, status)
#    VALUES ($1, $2, <requested_to>, <requested_by>, 'pending')
```

### Complete transfer (`POST /transfers/{id}/complete`)
```python
# 1. Fetch transfer row
# 2. UPDATE transfers SET status='transferred', completed_at=NOW() WHERE id=$1
# 3. INSERT INTO book_loans (book_id, lender_id, borrower_id, status)
#    VALUES (<book_id>, <from_user>, <to_user>, 'active')
# 4. UPDATE books SET status='lent' WHERE id=<book_id>
```

### Approve return request (`POST /returns/{id}/approve`)
```python
# 1. Fetch return_request row
# 2. UPDATE return_requests SET status='approved', resolved_at=NOW() WHERE id=$1
# 3. UPDATE book_loans SET status='returned', returned_at=NOW() WHERE id=<loan_id>
# 4. UPDATE books SET status='available' WHERE id=<book_id>
```

---

## Migrations (`app/db.py`)

- Files: `migrations/0001_name.sql`, `migrations/0002_name.sql`, etc.
- Tracking table: `schema_migrations(id SERIAL, filename TEXT UNIQUE, applied_at TIMESTAMPTZ)`
- On startup: read all `migrations/*.sql` sorted by filename; run those not in `schema_migrations`
- Each migration runs in a transaction; failure rolls back and logs a warning (app continues)
- `POST /schema/sql` (super_admin): runs `execute_raw(pool, sql)` — arbitrary SQL

---

## CORS Configuration

`CORS_ORIGINS` env var — comma-separated list of allowed origins.
Default for development: `["*"]` (restrict in production).

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Environment Variables

```env
SUPABASE_URL=https://xxxx.supabase.co          # Required
SUPABASE_SERVICE_KEY=eyJ...                    # Required — server-side only, never expose
SUPABASE_PUBLISHABLE_KEY=eyJ...                # For client-compatible token verification
DATABASE_URL=postgresql://user:pass@host/db    # Required
JWT_SECRET=                                    # Optional — for HS256 tokens
CORS_ORIGINS=http://localhost:3000             # Comma-separated
S3_SYNC_ENABLED=false
S3_BUCKET_NAME=
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
```

---

## Rules
- **No ORM** — asyncpg only; raw parameterized SQL
- **`user_id` always from JWT** — never from request body or query params
- **Invalid/missing JWT → 401**
- **Owned-resource-not-found → 404** (not 403)
- **Cannot delete/demote yourself** via admin routes
- **All config from `.env`** — no hardcoded secrets
- **Tests use real DB** — no mocks
- **No TypeScript** — Python only

---

## Out of Scope
- Realtime subscriptions (Supabase Realtime connects directly from the frontend)
- File storage uploads (frontend uploads directly to Supabase Storage using the public anon key)
- Email sending (Supabase handles transactional emails)
- Frontend rendering
- Docker orchestration
