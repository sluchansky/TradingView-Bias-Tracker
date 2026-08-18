-- SCALP Feedback Loop Repair — Phase 7 / 9 Schema Patch
-- Adds shadow_cohort and session_bucket columns to gate_audit_log.
-- Also ensures geometry_source column exists (added post-Phase-8C without a
-- schema file update).
--
-- Apply via Replit DB tool by running each ALTER statement individually,
-- or use: executeSql for each statement separately (NOT via file-split).
-- NEVER run DDL from app.py (app-side rule: INSERT/SELECT only).
-- DISPLAY/MEASUREMENT ONLY — never touches gate, execution, or risk.
--
-- NOTE FOR FUTURE APPLIES: Run each statement below individually.
-- The comment blocks before each ALTER are documentation only.

-- geometry_source: fidelity label for counterfactual geometry origin.
-- Already inserted by gate_effectiveness.py; column must exist in the table.
ALTER TABLE gate_audit_log ADD COLUMN IF NOT EXISTS geometry_source TEXT;

-- shadow_cohort: Phase 7 research classification for BLOCKED SCALP records.
--   EDGE35_OTHER_GATES_PASS     — edge was the sole blocker; all others passed
--   VOLUME_ONLY_BLOCK_1030_1200 — volume blocked in the 10:30-12:00 ET window
--   SHORT_CVD_ONLY_BLOCK        — Short direction; CVD was the sole blocker
--   NULL                        — does not belong to any tracked cohort
ALTER TABLE gate_audit_log ADD COLUMN IF NOT EXISTS shadow_cohort TEXT;

-- session_bucket: canonical ET time-of-day label (Phase 9).
-- Populated by _et_session_bucket() so all reports agree on bucket boundaries.
--   Overnight | 08:00-09:30 | 09:30-10:30 | 10:30-12:00 | 12:00-14:00
--   14:00-15:45 | 15:45-16:00 | 16:00-17:00 | Evening
ALTER TABLE gate_audit_log ADD COLUMN IF NOT EXISTS session_bucket TEXT;

-- Indexes for cohort and bucket analytics
CREATE INDEX IF NOT EXISTS idx_gal_shadow_cohort
    ON gate_audit_log (shadow_cohort)
    WHERE shadow_cohort IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_gal_session_bucket
    ON gate_audit_log (session_bucket);

CREATE INDEX IF NOT EXISTS idx_gal_geometry_source
    ON gate_audit_log (geometry_source);

-- Convenience composite index for cohort research queries
CREATE INDEX IF NOT EXISTS idx_gal_cohort_outcome
    ON gate_audit_log (shadow_cohort, outcome_status)
    WHERE shadow_cohort IS NOT NULL;
