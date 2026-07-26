-- bt_baseline_migrations.sql
-- Idempotent schema migrations for baseline tables.
-- Run once against the target database (dev or prod) before calling
-- generate_baseline().  Each statement uses IF NOT EXISTS so it is safe
-- to re-run at any time.
--
-- Tables are created by the DBA / database tool before first use.
-- This file handles column additions introduced after initial release.

-- Phase 6B.1 — initial_risk_r
-- Stores the per-trade risk denominator used by the backtest engine so that
-- realized_r values can be audited independently of entry/stop geometry.
ALTER TABLE baseline_trades
    ADD COLUMN IF NOT EXISTS initial_risk_r NUMERIC;
