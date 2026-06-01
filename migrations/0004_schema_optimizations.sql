-- 0004_schema_optimizations.sql
-- Comprehensive schema hardening: FKs, NOT NULL, CHECK constraints, UNIQUE,
-- updated_at trigger, pg_trgm extension, and composite indexes aligned to
-- actual query patterns in the route handlers.
-- All DDL is idempotent (safe to re-run on a DB where parts already applied).

-- ─── 1. profiles → auth.users FK ────────────────────────────────────────────
-- Ensures auth user deletion cascades to profile deletion.
-- Clean up any orphaned profiles first so the FK addition never fails.
DELETE FROM public.profiles p
  WHERE NOT EXISTS (SELECT 1 FROM auth.users u WHERE u.id = p.id);

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_profiles_auth_user'
      AND conrelid = 'public.profiles'::regclass
  ) THEN
    ALTER TABLE public.profiles
      ADD CONSTRAINT fk_profiles_auth_user
      FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE;
  END IF;
END $$;

-- ─── 2. Enable RLS on book_loans ─────────────────────────────────────────────
-- The 02_rls.sql file has policies for book_loans but never called ENABLE.
-- Including it here as a backup so it takes effect even before the RLS script runs.
ALTER TABLE public.book_loans ENABLE ROW LEVEL SECURITY;

-- ─── 3. NOT NULL fixups ──────────────────────────────────────────────────────
-- Delete truly invalid rows (these columns should never be NULL in a valid system).

DELETE FROM public.transfers
  WHERE book_id IS NULL OR from_user IS NULL OR to_user IS NULL;

ALTER TABLE public.transfers
  ALTER COLUMN book_id  SET NOT NULL,
  ALTER COLUMN from_user SET NOT NULL,
  ALTER COLUMN to_user  SET NOT NULL;

DELETE FROM public.book_reviews
  WHERE book_id IS NULL OR reviewer_id IS NULL OR rating IS NULL;

ALTER TABLE public.book_reviews
  ALTER COLUMN book_id     SET NOT NULL,
  ALTER COLUMN reviewer_id SET NOT NULL,
  ALTER COLUMN rating      SET NOT NULL;

DELETE FROM public.user_reviews
  WHERE reviewer_id IS NULL OR reviewee_id IS NULL OR rating IS NULL;

ALTER TABLE public.user_reviews
  ALTER COLUMN reviewer_id SET NOT NULL,
  ALTER COLUMN reviewee_id SET NOT NULL,
  ALTER COLUMN rating      SET NOT NULL;

-- books.user_id should never be NULL; delete orphaned rows then lock it down.
DELETE FROM public.books WHERE user_id IS NULL;
ALTER TABLE public.books ALTER COLUMN user_id SET NOT NULL;

-- books.status has DEFAULT 'available' but is nullable; lock it down.
UPDATE public.books SET status = 'available' WHERE status IS NULL;
ALTER TABLE public.books ALTER COLUMN status SET NOT NULL;

-- book_requests.status has DEFAULT 'pending' but is nullable.
UPDATE public.book_requests SET status = 'pending' WHERE status IS NULL;
ALTER TABLE public.book_requests ALTER COLUMN status SET NOT NULL;

-- transfers.status has DEFAULT 'pending' but is nullable.
UPDATE public.transfers SET status = 'pending' WHERE status IS NULL;
ALTER TABLE public.transfers ALTER COLUMN status SET NOT NULL;

-- ─── 4. CHECK constraints ────────────────────────────────────────────────────

DO $$ BEGIN
  -- profiles.role
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_profile_role' AND conrelid = 'public.profiles'::regclass) THEN
    ALTER TABLE public.profiles ADD CONSTRAINT chk_profile_role
      CHECK (role IN ('user', 'admin', 'super_admin'));
  END IF;

  -- profiles.approval_status
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_profile_approval' AND conrelid = 'public.profiles'::regclass) THEN
    ALTER TABLE public.profiles ADD CONSTRAINT chk_profile_approval
      CHECK (approval_status IN ('pending', 'approved', 'rejected'));
  END IF;

  -- books.status
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_books_status' AND conrelid = 'public.books'::regclass) THEN
    ALTER TABLE public.books ADD CONSTRAINT chk_books_status
      CHECK (status IN ('available', 'scheduled', 'lent'));
  END IF;

  -- book_requests.status
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_book_requests_status' AND conrelid = 'public.book_requests'::regclass) THEN
    ALTER TABLE public.book_requests ADD CONSTRAINT chk_book_requests_status
      CHECK (status IN ('pending', 'accepted', 'rejected', 'cancelled'));
  END IF;

  -- book_loans.status
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_book_loans_status' AND conrelid = 'public.book_loans'::regclass) THEN
    ALTER TABLE public.book_loans ADD CONSTRAINT chk_book_loans_status
      CHECK (status IN ('active', 'returned'));
  END IF;

  -- return_requests.status
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_return_requests_status' AND conrelid = 'public.return_requests'::regclass) THEN
    ALTER TABLE public.return_requests ADD CONSTRAINT chk_return_requests_status
      CHECK (status IN ('pending', 'approved'));
  END IF;

  -- transfers.status
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_transfers_status' AND conrelid = 'public.transfers'::regclass) THEN
    ALTER TABLE public.transfers ADD CONSTRAINT chk_transfers_status
      CHECK (status IN ('pending', 'confirmed', 'transferred'));
  END IF;

  -- book_reviews.rating range
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_book_review_rating' AND conrelid = 'public.book_reviews'::regclass) THEN
    ALTER TABLE public.book_reviews ADD CONSTRAINT chk_book_review_rating
      CHECK (rating BETWEEN 1 AND 5);
  END IF;

  -- user_reviews.rating range
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_user_review_rating' AND conrelid = 'public.user_reviews'::regclass) THEN
    ALTER TABLE public.user_reviews ADD CONSTRAINT chk_user_review_rating
      CHECK (rating BETWEEN 1 AND 5);
  END IF;

  -- user_reviews: no self-review
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_user_review_no_self' AND conrelid = 'public.user_reviews'::regclass) THEN
    ALTER TABLE public.user_reviews ADD CONSTRAINT chk_user_review_no_self
      CHECK (reviewer_id != reviewee_id);
  END IF;

  -- book_loans: lender and borrower must be different people
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_loan_no_self' AND conrelid = 'public.book_loans'::regclass) THEN
    ALTER TABLE public.book_loans ADD CONSTRAINT chk_loan_no_self
      CHECK (lender_id != borrower_id);
  END IF;
END $$;

-- ─── 5. UNIQUE: one review per user pair ─────────────────────────────────────
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uniq_user_review' AND conrelid = 'public.user_reviews'::regclass) THEN
    ALTER TABLE public.user_reviews
      ADD CONSTRAINT uniq_user_review UNIQUE (reviewer_id, reviewee_id);
  END IF;
END $$;

-- ─── 6. FK on request_logs.user_id ──────────────────────────────────────────
-- ON DELETE SET NULL preserves the log row while clearing the user reference.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_request_logs_user' AND conrelid = 'public.request_logs'::regclass) THEN
    -- Null out any dangling references before adding the FK.
    UPDATE public.request_logs rl
      SET user_id = NULL
      WHERE user_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM auth.users u WHERE u.id = rl.user_id);

    ALTER TABLE public.request_logs
      ADD CONSTRAINT fk_request_logs_user
      FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE SET NULL;
  END IF;
END $$;

-- ─── 7. Trigger: auto-update book_requests.updated_at ───────────────────────
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS book_requests_set_updated_at ON public.book_requests;
CREATE TRIGGER book_requests_set_updated_at
  BEFORE UPDATE ON public.book_requests
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ─── 8. pg_trgm + catalog ILIKE search indexes ───────────────────────────────
-- Speeds up existing ILIKE '%q%' queries on title/author without any backend change.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- GIN trigram indexes: power the ILIKE '%q%' search in GET /catalog/search
CREATE INDEX IF NOT EXISTS idx_books_catalog_title_trgm
  ON public.books_catalog USING GIN (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_books_catalog_author_trgm
  ON public.books_catalog USING GIN (author gin_trgm_ops);

-- Functional indexes: power the LOWER(col) = LOWER($1) lookup in GET /catalog/lookup
CREATE INDEX IF NOT EXISTS idx_books_catalog_title_lower
  ON public.books_catalog (LOWER(title));

CREATE INDEX IF NOT EXISTS idx_books_catalog_author_lower
  ON public.books_catalog (LOWER(author));

-- Trigram index on request_logs.path for admin ILIKE filter (GET /admin/logs)
CREATE INDEX IF NOT EXISTS idx_request_logs_path_trgm
  ON public.request_logs USING GIN (path gin_trgm_ops);

-- ─── 9. Composite / partial indexes for real query patterns ──────────────────

-- GET /requests/outgoing  (requested_by + status filter)
CREATE INDEX IF NOT EXISTS idx_book_requests_by_status
  ON public.book_requests (requested_by, status);

-- GET /requests/incoming  (requested_to + status filter)
CREATE INDEX IF NOT EXISTS idx_book_requests_to_status
  ON public.book_requests (requested_to, status);

-- GET /saved  (user_id filter + created_at DESC sort)
CREATE INDEX IF NOT EXISTS idx_saved_books_user_created
  ON public.saved_books (user_id, created_at DESC);

-- GET /books?source=personal|library  (book_source filter + created_at DESC sort)
-- Covers the source-filtered variant; the existing idx_books_book_source becomes redundant.
CREATE INDEX IF NOT EXISTS idx_books_source_created
  ON public.books (book_source, created_at DESC);

-- GET /reviews/book/{id}  (book_id filter + created_at DESC sort)
CREATE INDEX IF NOT EXISTS idx_book_reviews_book_created
  ON public.book_reviews (book_id, created_at DESC);

-- GET /reviews/user/{id}  (reviewee_id filter + created_at DESC sort)
CREATE INDEX IF NOT EXISTS idx_user_reviews_reviewee_created
  ON public.user_reviews (reviewee_id, created_at DESC);

-- GET /transfers  (from_user or to_user filter + created_at DESC sort)
CREATE INDEX IF NOT EXISTS idx_transfers_from_created
  ON public.transfers (from_user, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_transfers_to_created
  ON public.transfers (to_user, created_at DESC);

-- GET /admin/logs  (status_code range filter + created_at DESC sort)
CREATE INDEX IF NOT EXISTS idx_request_logs_status_created
  ON public.request_logs (status_code, created_at DESC);

-- ENRICHED_BOOKS_SQL subquery: EXISTS(saved_books WHERE book_id=b.id AND user_id=$1)
-- The existing idx_saved_books_user covers user_id alone; this covers the EXISTS check.
CREATE INDEX IF NOT EXISTS idx_saved_books_book_user
  ON public.saved_books (book_id, user_id);

-- ENRICHED_BOOKS_SQL subquery: SELECT id FROM return_requests WHERE book_id=b.id AND status='pending'
-- No index existed for this; it caused a scan of return_requests per book row.
CREATE INDEX IF NOT EXISTS idx_return_requests_book_pending
  ON public.return_requests (book_id) WHERE status = 'pending';
