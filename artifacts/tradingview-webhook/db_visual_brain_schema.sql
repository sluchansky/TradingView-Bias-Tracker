-- Visual Brain V1 — MNQ 1-Minute Stateful Market Observer
-- Stores one structured JSON market-state observation per minute.
-- Ghost outcome columns (p1m…p15m / mfe / mae) are backfilled by a background
-- thread once 15+ bars of forward data are available.
--
-- Apply via Replit DB tool or publish schema-diff.
-- NEVER run DDL from app.py (app-side rule: INSERT/SELECT only).
-- SHADOW / OBSERVATION ONLY — never touches gate, execution, or risk.

CREATE TABLE IF NOT EXISTS visual_brain_observations (
    -- Identity
    id                      SERIAL          PRIMARY KEY,
    timestamp               TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Instrument context
    instrument              TEXT            NOT NULL DEFAULT 'MNQ',

    -- Core market-state fields (validated enum values — see visual_brain.py)
    bias                    TEXT,           -- BULLISH | BEARISH | NEUTRAL
    market_state            TEXT,           -- TRENDING_UP | TRENDING_DOWN | RANGE | REVERSAL |
                                            -- BREAKOUT | BREAKDOWN | RETEST | CHOP | UNCLEAR
    short_term_structure    TEXT,           -- HH_HL | LH_LL | RANGE | TRANSITION | UNCLEAR
    last_event              TEXT,           -- LIQUIDITY_SWEEP | RECLAIM | REJECTION | BREAKOUT |
                                            -- BREAKDOWN | RETEST | FAILED_BREAKOUT |
                                            -- FAILED_BREAKDOWN | STRUCTURE_SHIFT | NONE
    action                  TEXT,           -- LONG_WATCH | SHORT_WATCH | WAIT | NO_TRADE
    confidence              INTEGER,        -- 0–100

    -- Support / resistance
    support_description     TEXT,
    support_price           NUMERIC(12, 4),
    resistance_description  TEXT,
    resistance_price        NUMERIC(12, 4),

    -- Entry conditions
    long_condition          TEXT,
    short_condition         TEXT,

    -- State-change tracking
    state_changed           BOOLEAN         NOT NULL DEFAULT FALSE,
    state_change_reason     TEXT,

    -- Narrative
    summary                 TEXT,

    -- Screenshot (NULL — screenshots are ephemeral; use object storage for retention)
    screenshot_path         TEXT,

    -- Full model response (raw JSON for debugging / replay)
    raw_json                JSONB,

    -- Forward outcome tracking (backfilled after 15 minutes)
    -- Values are % move relative to entry_price_at_obs, direction-adjusted
    -- (positive = favorable for the action taken, e.g. LONG_WATCH → price rose)
    p1m                     NUMERIC(10, 4),   -- close 1 bar after observation
    p3m                     NUMERIC(10, 4),   -- close 3 bars after
    p5m                     NUMERIC(10, 4),   -- close 5 bars after
    p10m                    NUMERIC(10, 4),   -- close 10 bars after
    p15m                    NUMERIC(10, 4),   -- close 15 bars after
    mfe                     NUMERIC(10, 4),   -- max favorable excursion % (bars 1–15)
    mae                     NUMERIC(10, 4),   -- max adverse excursion % (bars 1–15)
    outcome_resolved        BOOLEAN         NOT NULL DEFAULT FALSE,

    -- Snapshot of current price at observation time (source for outcome math)
    entry_price_at_obs      NUMERIC(12, 4)
);

-- Query patterns: newest observations per instrument, market-state aggregations,
-- state-shift filtering, and outcome analytics
CREATE INDEX IF NOT EXISTS idx_vbo_timestamp
    ON visual_brain_observations (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_vbo_inst_ts
    ON visual_brain_observations (instrument, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_vbo_market_state
    ON visual_brain_observations (market_state);

CREATE INDEX IF NOT EXISTS idx_vbo_last_event
    ON visual_brain_observations (last_event);

CREATE INDEX IF NOT EXISTS idx_vbo_outcome_pending
    ON visual_brain_observations (outcome_resolved, timestamp)
    WHERE outcome_resolved = FALSE;
