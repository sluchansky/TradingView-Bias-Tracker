-- Durable market-thesis continuity, scoped by instrument and trading mode.
-- Apply out-of-band through the database schema workflow; app.py remains DDL-free.

CREATE TABLE IF NOT EXISTS hysteresis_thesis (
    instrument  TEXT        NOT NULL,
    mode        TEXT        NOT NULL DEFAULT 'SCALP',
    thesis_id   TEXT        NOT NULL,
    direction   TEXT,
    status      TEXT        NOT NULL,
    confidence  INTEGER     NOT NULL,
    data        JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (instrument, mode)
);

CREATE TABLE IF NOT EXISTS hysteresis_thesis_events (
    event_id          TEXT        PRIMARY KEY,
    instrument        TEXT        NOT NULL,
    mode              TEXT        NOT NULL,
    thesis_id         TEXT,
    previous_thesis_id TEXT,
    prev_status       TEXT        NOT NULL,
    new_status        TEXT        NOT NULL,
    evidence_epoch    TEXT,
    transition_index  SMALLINT    NOT NULL DEFAULT 0,
    data              JSONB       NOT NULL,
    occurred_at       TIMESTAMPTZ NOT NULL,
    recorded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE hysteresis_thesis_events
    ADD COLUMN IF NOT EXISTS transition_index SMALLINT NOT NULL DEFAULT 0;

UPDATE hysteresis_thesis_events
SET mode = CASE
        WHEN UPPER(mode) = 'SWING' THEN 'INTRADAY_TREND'
        ELSE UPPER(mode)
    END,
    data = CASE
        WHEN UPPER(COALESCE(data->>'mode', mode)) = 'SWING'
        THEN jsonb_set(data, '{mode}', '"INTRADAY_TREND"'::jsonb, TRUE)
        ELSE data
    END;

ALTER TABLE hysteresis_thesis
    ADD COLUMN IF NOT EXISTS mode TEXT;

UPDATE hysteresis_thesis
SET mode = COALESCE(NULLIF(mode, ''), COALESCE(NULLIF(data->>'mode', ''), 'SCALP'))
WHERE mode IS NULL OR mode = '';

-- SWING is the historical name for the INTRADAY_TREND continuity bucket.
-- Refuse to guess when both aliases exist; operators must reconcile that
-- instrument explicitly instead of this migration silently deleting evidence.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM hysteresis_thesis AS swing_row
          JOIN hysteresis_thesis AS intraday_row
            ON intraday_row.instrument = swing_row.instrument
         WHERE UPPER(swing_row.mode) = 'SWING'
           AND UPPER(intraday_row.mode) = 'INTRADAY_TREND'
    ) THEN
        RAISE EXCEPTION
            'hysteresis_thesis contains both SWING and INTRADAY_TREND rows for one instrument';
    END IF;
END
$$;

UPDATE hysteresis_thesis
SET mode = CASE
        WHEN UPPER(mode) = 'SWING' THEN 'INTRADAY_TREND'
        ELSE UPPER(mode)
    END,
    data = CASE
        WHEN UPPER(COALESCE(data->>'mode', mode)) = 'SWING'
        THEN jsonb_set(data, '{mode}', '"INTRADAY_TREND"'::jsonb, TRUE)
        ELSE data
    END;

ALTER TABLE hysteresis_thesis
    ALTER COLUMN mode SET DEFAULT 'SCALP',
    ALTER COLUMN mode SET NOT NULL;

DO $$
DECLARE
    pk_name TEXT;
BEGIN
    SELECT con.conname
      INTO pk_name
      FROM pg_constraint con
      JOIN pg_class rel ON rel.oid = con.conrelid
     WHERE rel.relname = 'hysteresis_thesis'
       AND con.contype = 'p'
     LIMIT 1;

    IF pk_name IS NOT NULL AND NOT EXISTS (
        SELECT 1
          FROM pg_constraint con
          JOIN pg_class rel ON rel.oid = con.conrelid
          JOIN unnest(con.conkey) WITH ORDINALITY AS keys(attnum, ord) ON TRUE
          JOIN pg_attribute att
            ON att.attrelid = rel.oid AND att.attnum = keys.attnum
         WHERE rel.relname = 'hysteresis_thesis'
           AND con.contype = 'p'
         GROUP BY con.oid
        HAVING array_agg(att.attname::TEXT ORDER BY keys.ord)
               = ARRAY['instrument', 'mode']::TEXT[]
    ) THEN
        EXECUTE format(
            'ALTER TABLE hysteresis_thesis DROP CONSTRAINT %I',
            pk_name
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint con
          JOIN pg_class rel ON rel.oid = con.conrelid
          JOIN unnest(con.conkey) WITH ORDINALITY AS keys(attnum, ord) ON TRUE
          JOIN pg_attribute att
            ON att.attrelid = rel.oid AND att.attnum = keys.attnum
         WHERE rel.relname = 'hysteresis_thesis'
           AND con.contype = 'p'
         GROUP BY con.oid
        HAVING array_agg(att.attname::TEXT ORDER BY keys.ord)
               = ARRAY['instrument', 'mode']::TEXT[]
    ) THEN
        ALTER TABLE hysteresis_thesis
            ADD PRIMARY KEY (instrument, mode);
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'hysteresis_thesis'::regclass
           AND conname = 'hysteresis_thesis_mode_check'
    ) THEN
        ALTER TABLE hysteresis_thesis
            ADD CONSTRAINT hysteresis_thesis_mode_check
            CHECK (mode IN ('SCALP', 'INTRADAY_TREND'));
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'hysteresis_thesis_events'::regclass
           AND conname = 'hysteresis_thesis_events_mode_check'
    ) THEN
        ALTER TABLE hysteresis_thesis_events
            ADD CONSTRAINT hysteresis_thesis_events_mode_check
            CHECK (mode IN ('SCALP', 'INTRADAY_TREND'));
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_hysteresis_thesis_events_scope_time
    ON hysteresis_thesis_events (
        instrument,
        mode,
        occurred_at DESC,
        transition_index DESC,
        event_id DESC
    );