-- 0001_initial_schema.sql
-- Initial schema for Leaflet (sourced from supabase_schema/ snapshot in project-nexus)
-- Run by: app/db.py apply_pending_migrations() on startup
-- NOTE: auth.users table is managed by Supabase; these tables reference it via auth.uid()
--       The schema_migrations table below is the backend's own migration tracker (separate
--       from Supabase's auth.schema_migrations)

-- ─── Migration tracker ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.schema_migrations (
  id         SERIAL      PRIMARY KEY,
  filename   TEXT        UNIQUE NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── profiles ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.profiles (
  id              UUID        NOT NULL DEFAULT auth.uid() PRIMARY KEY,
  username        TEXT        UNIQUE,
  avatar_url      TEXT,
  bio             TEXT,
  first_name      TEXT,
  last_name       TEXT,
  role            TEXT        NOT NULL DEFAULT 'user',
  approval_status TEXT        NOT NULL DEFAULT 'pending',
  created_at      TIMESTAMPTZ          DEFAULT TIMEZONE('utc', NOW())
);

-- ─── books_catalog ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.books_catalog (
  id         UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  title      TEXT        NOT NULL,
  author     TEXT,
  isbn       TEXT        UNIQUE,
  cover_url  TEXT,
  created_by UUID                 DEFAULT auth.uid()
                                  REFERENCES auth.users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ          DEFAULT NOW()
);

-- ─── books ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.books (
  id         UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id    UUID                 REFERENCES auth.users(id)      ON DELETE CASCADE,
  catalog_id UUID        NOT NULL REFERENCES public.books_catalog(id) ON DELETE CASCADE,
  condition  TEXT        NOT NULL,
  notes      TEXT,
  status     TEXT                 DEFAULT 'available',
  archived   BOOLEAN     NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ          DEFAULT NOW(),
  UNIQUE (catalog_id, condition, user_id)
);

-- ─── book_requests ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.book_requests (
  id           UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  book_id      UUID                 REFERENCES public.books(id)    ON DELETE CASCADE,
  requested_by UUID        NOT NULL DEFAULT auth.uid()
                                    REFERENCES auth.users(id)      ON DELETE CASCADE,
  requested_to UUID        NOT NULL REFERENCES auth.users(id)      ON DELETE CASCADE,
  status       TEXT                 DEFAULT 'pending',
  message      TEXT,
  created_at   TIMESTAMPTZ          DEFAULT NOW(),
  updated_at   TIMESTAMPTZ          DEFAULT NOW()
);

-- Partial unique index: one pending request per (book, requester)
CREATE UNIQUE INDEX IF NOT EXISTS unique_active_book_request
  ON public.book_requests (book_id, requested_by)
  WHERE status = 'pending';

CREATE UNIQUE INDEX IF NOT EXISTS unique_pending_request_constraint
  ON public.book_requests (book_id, requested_by, requested_to)
  WHERE status = 'pending';

-- ─── book_loans ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.book_loans (
  id          UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  book_id     UUID        NOT NULL REFERENCES public.books(id)    ON DELETE CASCADE,
  lender_id   UUID        NOT NULL REFERENCES auth.users(id)      ON DELETE CASCADE,
  borrower_id UUID        NOT NULL REFERENCES auth.users(id)      ON DELETE CASCADE,
  loaned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  due_date    TIMESTAMPTZ,
  status      TEXT        NOT NULL DEFAULT 'active',
  returned_at TIMESTAMPTZ,
  notes       TEXT
);

-- Only one active loan per book at a time
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_loan_per_book
  ON public.book_loans (book_id)
  WHERE status = 'active';

-- ─── return_requests ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.return_requests (
  id           UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  book_id      UUID        NOT NULL REFERENCES public.books(id)      ON DELETE CASCADE,
  loan_id      UUID        NOT NULL REFERENCES public.book_loans(id) ON DELETE CASCADE,
  requested_by UUID        NOT NULL REFERENCES auth.users(id)        ON DELETE CASCADE,
  requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status       TEXT        NOT NULL DEFAULT 'pending',
  resolved_at  TIMESTAMPTZ,
  notes        TEXT
);

-- Only one pending return per active loan
CREATE UNIQUE INDEX IF NOT EXISTS unique_active_return_request
  ON public.return_requests (loan_id)
  WHERE status = 'pending';

-- ─── transfers ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.transfers (
  id           UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  request_id   UUID                 REFERENCES public.book_requests(id) ON DELETE SET NULL,
  book_id      UUID                 REFERENCES public.books(id)         ON DELETE CASCADE,
  from_user    UUID                 REFERENCES auth.users(id)           ON DELETE CASCADE,
  to_user      UUID                 REFERENCES auth.users(id)           ON DELETE CASCADE,
  status       TEXT                 DEFAULT 'pending',
  scheduled_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ          DEFAULT NOW(),
  UNIQUE (book_id, from_user, to_user)
);

-- ─── book_reviews ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.book_reviews (
  id          UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  book_id     UUID                 REFERENCES public.books(id)     ON DELETE CASCADE,
  reviewer_id UUID                 REFERENCES auth.users(id)       ON DELETE CASCADE,
  rating      INTEGER,
  comment     TEXT,
  created_at  TIMESTAMPTZ          DEFAULT NOW(),
  UNIQUE (book_id, reviewer_id)
);

-- ─── user_reviews ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.user_reviews (
  id          UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  reviewer_id UUID                 REFERENCES auth.users(id)       ON DELETE CASCADE,
  reviewee_id UUID                 REFERENCES auth.users(id)       ON DELETE CASCADE,
  rating      INTEGER,
  comment     TEXT,
  created_at  TIMESTAMPTZ          DEFAULT NOW()
);

-- ─── saved_books ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.saved_books (
  id         UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id    UUID        NOT NULL REFERENCES auth.users(id)         ON DELETE CASCADE,
  book_id    UUID        NOT NULL REFERENCES public.books(id)       ON DELETE CASCADE,
  catalog_id UUID        NOT NULL REFERENCES public.books_catalog(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ          DEFAULT NOW(),
  UNIQUE (user_id, book_id),
  UNIQUE (user_id, catalog_id)
);

-- ─── crud_event_logs ─────────────────────────────────────────────────────────
-- Audit trail; id is bigint serial (matches existing schema)
CREATE TABLE IF NOT EXISTS public.crud_event_logs (
  id           BIGSERIAL   PRIMARY KEY,
  table_schema TEXT        NOT NULL,
  table_name   TEXT        NOT NULL,
  event_type   TEXT        NOT NULL,
  record_id    TEXT,
  actor_id     UUID                 REFERENCES auth.users(id) ON DELETE SET NULL,
  request_role TEXT,
  new_data     JSONB,
  old_data     JSONB,
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS crud_event_logs_table_sort_idx
  ON public.crud_event_logs (table_schema, table_name, event_type, occurred_at DESC, id DESC);

-- ─── feedback ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.feedback (
  id         UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  message    TEXT,
  email      TEXT,
  created_at TIMESTAMPTZ          DEFAULT NOW()
);

-- ─── Performance indexes ─────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_books_user_id       ON public.books (user_id);
CREATE INDEX IF NOT EXISTS idx_books_catalog_id    ON public.books (catalog_id);
CREATE INDEX IF NOT EXISTS idx_books_status        ON public.books (status);
CREATE INDEX IF NOT EXISTS idx_book_loans_book_id  ON public.book_loans (book_id);
CREATE INDEX IF NOT EXISTS idx_book_loans_lender   ON public.book_loans (lender_id);
CREATE INDEX IF NOT EXISTS idx_book_loans_borrower ON public.book_loans (borrower_id);
CREATE INDEX IF NOT EXISTS idx_book_requests_book  ON public.book_requests (book_id);
CREATE INDEX IF NOT EXISTS idx_book_requests_by    ON public.book_requests (requested_by);
CREATE INDEX IF NOT EXISTS idx_book_requests_to    ON public.book_requests (requested_to);
CREATE INDEX IF NOT EXISTS idx_transfers_book      ON public.transfers (book_id);
CREATE INDEX IF NOT EXISTS idx_transfers_from      ON public.transfers (from_user);
CREATE INDEX IF NOT EXISTS idx_transfers_to        ON public.transfers (to_user);
CREATE INDEX IF NOT EXISTS idx_saved_books_user    ON public.saved_books (user_id);
CREATE INDEX IF NOT EXISTS idx_return_requests_loan ON public.return_requests (loan_id);
