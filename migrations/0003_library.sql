-- 0003_library.sql
-- Adds Office Library support: company-owned books at fixed physical locations.
-- Library books use a simplified 3-step flow (reserve/pickup/return) that
-- bypasses the P2P transfer step, since the pickup location is fixed and known.
--
-- Also adds city + office_location to profiles as Phase 2 prep (no logic wired yet).

-- ─── libraries ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.libraries (
  id              UUID        NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  name            TEXT        NOT NULL,
  city            TEXT        NOT NULL,
  location        TEXT        NOT NULL,        -- e.g. "4th Floor, Bay 3"
  managed_by      UUID        NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  is_self_service BOOLEAN     NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ          DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_libraries_city       ON public.libraries (city);
CREATE INDEX IF NOT EXISTS idx_libraries_managed_by ON public.libraries (managed_by);

-- ─── books: library columns ──────────────────────────────────────────────────
ALTER TABLE public.books
  ADD COLUMN IF NOT EXISTS book_source TEXT NOT NULL DEFAULT 'personal'
    CHECK (book_source IN ('personal', 'library')),
  ADD COLUMN IF NOT EXISTS library_id UUID
    REFERENCES public.libraries(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_books_book_source ON public.books (book_source);
CREATE INDEX IF NOT EXISTS idx_books_library_id  ON public.books (library_id);

-- Enforce: library books must have library_id; personal books must not
ALTER TABLE public.books
  ADD CONSTRAINT chk_library_consistency CHECK (
    (book_source = 'library' AND library_id IS NOT NULL)
    OR
    (book_source = 'personal' AND library_id IS NULL)
  );

-- ─── profiles: Phase 2 prep columns ─────────────────────────────────────────
-- city and office_location enable same-city P2P filtering (Phase 2).
-- No application logic reads these yet.
ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS city            TEXT,
  ADD COLUMN IF NOT EXISTS office_location TEXT;
