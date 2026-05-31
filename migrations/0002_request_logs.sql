-- 0002_request_logs.sql
-- Request/response audit log for the Monitoring tab in the super_admin panel.

CREATE TABLE IF NOT EXISTS public.request_logs (
    id          BIGSERIAL    PRIMARY KEY,
    method      TEXT         NOT NULL,
    path        TEXT         NOT NULL,
    status_code INTEGER,
    duration_ms NUMERIC(10,2),
    user_id     UUID,
    ip_address  TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON public.request_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_request_logs_path       ON public.request_logs (path);
CREATE INDEX IF NOT EXISTS idx_request_logs_status     ON public.request_logs (status_code);
CREATE INDEX IF NOT EXISTS idx_request_logs_user_id    ON public.request_logs (user_id);
