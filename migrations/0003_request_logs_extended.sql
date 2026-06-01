-- 0003_request_logs_extended.sql
-- Extend request_logs with rich context for Kibana-style monitoring.

ALTER TABLE public.request_logs
  ADD COLUMN IF NOT EXISTS query_params  JSONB,
  ADD COLUMN IF NOT EXISTS request_body  JSONB,
  ADD COLUMN IF NOT EXISTS error_detail  JSONB,
  ADD COLUMN IF NOT EXISTS user_agent    TEXT,
  ADD COLUMN IF NOT EXISTS request_id    TEXT;

CREATE INDEX IF NOT EXISTS idx_request_logs_request_id ON public.request_logs (request_id);
