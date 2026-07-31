-- Migration 001: journal_attachments table
-- Screenshot metadata for the Journal Review feature.
-- GCS holds the actual files; this table stores only the metadata and object key.
--
-- Idempotent: safe to apply on an environment where the table already exists.
-- INSERT/SELECT only from application code — no runtime DDL after this migration runs.

CREATE TABLE IF NOT EXISTS journal_attachments (
  id          SERIAL       NOT NULL,
  source      TEXT         NOT NULL,
  trade_id    INTEGER      NOT NULL,
  stage       TEXT         NOT NULL DEFAULT 'review_markup',
  filename    TEXT         NOT NULL,
  storage_key TEXT         NOT NULL,
  mime_type   TEXT         NOT NULL,
  size_bytes  INTEGER      NOT NULL,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

  CONSTRAINT journal_attachments_pkey PRIMARY KEY (id),

  -- Each GCS object maps to exactly one metadata row.
  CONSTRAINT journal_attachments_storage_key_key UNIQUE (storage_key),

  -- Valid trade origins.
  CONSTRAINT chk_attach_source  CHECK (source IN ('system', 'tradzella')),

  -- Valid lifecycle stages.
  CONSTRAINT chk_attach_stage   CHECK (stage IN (
    'before_entry', 'at_entry', 'during', 'after_exit', 'review_markup'
  )),

  -- Valid image types accepted by the upload route.
  CONSTRAINT chk_attach_mime    CHECK (mime_type IN (
    'image/png', 'image/jpeg', 'image/webp', 'image/gif'
  )),

  -- File size must be positive and within the 5 MB upload limit.
  CONSTRAINT chk_attach_size    CHECK (size_bytes > 0 AND size_bytes <= 5242880)
);

-- Fast lookup by (source, trade_id) for per-trade list and COUNT queries.
CREATE INDEX IF NOT EXISTS idx_journal_attach_trade
  ON journal_attachments (source, trade_id, created_at);
