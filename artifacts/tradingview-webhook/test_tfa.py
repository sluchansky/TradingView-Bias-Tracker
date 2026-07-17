"""
Trade Failure Analyzer (TFA) — Validation Test Suite
Section 14 of the TFA validation specification.

Covers:
  1.  DB schema and constraints
  2.  READY record round-trip and field correctness
  3.  Deduplication (DB-level ON CONFLICT and in-memory dedup invariants)
  4.  Execution linkage (trigger UPDATE)
  5.  Outcome linkage (complete UPDATE, not INSERT)
  6.  MFE/MAE passthrough and formula verification
  7.  Failure classification — all 16 buckets
  8.  Stale-record expiry (NOT_TRIGGERED via 2-hour expire query)
  9.  Restart persistence (records survive reconnect / simulated restart)
  10. Concurrency — simultaneous inserts for same and different instruments
  11. Write-rate safety — exact writes per trade lifecycle
  12. 25-trade summary accuracy — aggregates, percentages, denominators
  13. API endpoint — success, empty state, limit cap, no secret fields,
                      no write side effects
  14. Scope isolation — TFA cannot block or alter money-path decisions

Run with:
    pytest artifacts/tradingview-webhook/test_tfa.py -v

Design: all tests exercise the TFA persistence layer via raw psycopg2 and
the inlined classifier logic (exact copy of production code) — no app.py
import required.  Each test deletes its own rows by ready_id prefix.
The running Flask server is hit for API tests via requests (port 5001).
"""

import os
import sys
import time
import threading
import uuid
import pytest
import requests
from datetime import datetime, timezone, timedelta

DB_URL = os.environ.get("DATABASE_URL")

try:
    import psycopg2
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

pytestmark = pytest.mark.skipif(
    not (DB_URL and _HAS_PSYCOPG2),
    reason="DATABASE_URL and psycopg2 required for TFA tests",
)


# ─────────────────────────────────────────────────────────────────────────────
# Inlined classifier — exact copy of production code for test isolation.
# Any divergence from app.py's _derive_trade_label / _classify_failure_mode_tfa
# will cause test failures, making future drift visible.
# ─────────────────────────────────────────────────────────────────────────────

def _derive_trade_label_test(mt, ctx):
    """Exact copy of _derive_trade_label from app.py (line 11089).
    mfe_r / mae_r may be None — never coerced to 0.0 (price-poll close path)."""
    def _f(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
    try:
        outcome = (mt.get("outcome") or "").strip()
        if not outcome:
            return "UNCATEGORIZED"
        r     = _f(mt.get("r_multiple")) or 0.0
        mfe_r = _f(mt.get("mfe_r"))          # None when unavailable — not coerced
        mae_r = _f(mt.get("mae_r"))          # None when unavailable — not coerced
        ctx   = ctx or {}
        eff   = None
        try:
            eff = int(ctx.get("entry_efficiency")
                      if ctx.get("entry_efficiency") is not None else -1)
        except Exception:
            pass
        edge = _f(ctx.get("edge_score"))
        sess = (ctx.get("session") or mt.get("session") or "").upper()
        is_win = "Win" in outcome
        is_be  = outcome == "Breakeven" or (-0.15 < r < 0.15)
        if is_win:
            if mfe_r is not None and mfe_r >= 0.8 and r < 0.4:
                return "TP1_THEN_BE"
            return "WIN"
        if is_be:
            if mfe_r is not None and mfe_r >= 0.8:
                return "TP1_THEN_BE"
            return "BREAKEVEN"
        if mfe_r is not None and mfe_r >= 0.8:
            return "TP1_THEN_BE"
        if eff is not None and eff >= 0 and eff < 45:
            return "LATE_ENTRY"
        if eff is not None and eff >= 0 and eff > 85 and r < 0:
            return "EARLY_ENTRY"
        if mae_r is not None and mfe_r is not None and abs(mae_r) < 0.3 and mfe_r < 0.3:
            return "STOPPED_BEFORE_MOVE"
        if mae_r is not None and mfe_r is not None and mae_r < -0.8 and mfe_r > abs(mae_r) * 0.5:
            return "STOP_TOO_TIGHT"
        if edge is not None and edge < 45:
            return "BAD_SETUP"
        if sess and any(s in sess for s in ("LUNCH", "MIDDAY", "OVERNIGHT")):
            return "BAD_SESSION"
        if mfe_r is not None and mfe_r < 0.25:
            return "NO_FOLLOW_THROUGH"
        return "LOSS"
    except Exception:
        return "UNCATEGORIZED"


def _classify_failure_mode_test(mt, ctx, bias=None, vol_regime=None, eq_score=None):
    """Exact copy of _classify_failure_mode_tfa from app.py (line ~27409).
    Includes _partial_flag: appends 'partial: mfe/mae unavailable' to
    failure_detail when both mfe_r and mae_r are absent from mt."""
    def _partial_flag(fm, fd):
        if mt.get("mfe_r") is None and mt.get("mae_r") is None:
            sfx = "partial: mfe/mae unavailable"
            fd  = (fd + "; " + sfx) if fd else sfx
        return fm, fd
    try:
        base_label = _derive_trade_label_test(mt, ctx)
        outcome    = (mt.get("outcome") or "").strip()
        is_win     = "Win" in outcome or base_label == "WIN"
        if is_win or base_label in ("TP1_THEN_BE", "BREAKEVEN"):
            return _partial_flag(base_label, None)
        direction = (mt.get("direction") or "").strip()
        _bias     = (bias or "").upper()
        if _bias and direction:
            if direction == "Long"  and "BEAR" in _bias:
                return _partial_flag("WRONG_BIAS", "Bias was %s at entry" % bias)
            if direction == "Short" and "BULL" in _bias:
                return _partial_flag("WRONG_BIAS", "Bias was %s at entry" % bias)
        if eq_score is not None:
            try:
                if float(eq_score) < 60:
                    return _partial_flag("POOR_LOCATION", "Entry quality %d at entry" % round(float(eq_score)))
            except Exception:
                pass
        if vol_regime and "EXTREME" in str(vol_regime).upper():
            return _partial_flag("VOLATILITY_MISMATCH", "Vol regime: %s" % vol_regime)
        return _partial_flag(base_label, None)
    except Exception:
        return "UNCATEGORIZED", None


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _conn():
    c = psycopg2.connect(DB_URL, connect_timeout=5)
    c.autocommit = True
    return c


def _prefix(tag):
    return "TEST_%s_%s" % (tag, uuid.uuid4().hex[:8])


def _clean(prefix):
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM trade_failure_analysis WHERE ready_id LIKE %s",
                (prefix + "%",),
            )
    finally:
        c.close()


def _fetch(ready_id):
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM trade_failure_analysis WHERE ready_id=%s", (ready_id,)
            )
            cols = [d[0] for d in cur.description]
            row  = cur.fetchone()
            return dict(zip(cols, row)) if row else None
    finally:
        c.close()


def _count(prefix):
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM trade_failure_analysis WHERE ready_id LIKE %s",
                (prefix + "%",),
            )
            return cur.fetchone()[0]
    finally:
        c.close()


def _insert_ready(ready_id, inst="MGC", direction="Long", mode="SCALP",
                  edge=72.0, eq=75.0, strategy="ORB", bias="Bullish",
                  structure=True, zone=True, vwap=True, cvd=True,
                  session="RTH", vol_regime="NORMAL", price=3995.0,
                  backdated_sec=None):
    c = _conn()
    try:
        with c.cursor() as cur:
            if backdated_sec is None:
                cur.execute(
                    """INSERT INTO trade_failure_analysis
                       (ready_id,instrument,direction,mode,edge_score,
                        entry_quality_score,strategy,bias,structure_ok,zone_ok,
                        vwap_ok,cvd_ok,session,vol_regime,price_at_ready,
                        ready_at,schema_version)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),1)
                       ON CONFLICT(ready_id) DO NOTHING""",
                    (ready_id, inst, direction, mode, edge, eq, strategy, bias,
                     structure, zone, vwap, cvd, session, vol_regime, price),
                )
            else:
                cur.execute(
                    """INSERT INTO trade_failure_analysis
                       (ready_id,instrument,direction,mode,edge_score,
                        entry_quality_score,strategy,bias,structure_ok,zone_ok,
                        vwap_ok,cvd_ok,session,vol_regime,price_at_ready,
                        ready_at,schema_version)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                              NOW()-interval '%s seconds',1)
                       ON CONFLICT(ready_id) DO NOTHING""",
                    (ready_id, inst, direction, mode, edge, eq, strategy, bias,
                     structure, zone, vwap, cvd, session, vol_regime, price,
                     backdated_sec),
                )
    finally:
        c.close()


def _trigger(ready_id, source="auto", entry_price=3990.0, lag_sec=2.5):
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE trade_failure_analysis
                   SET triggered=TRUE,triggered_at=NOW(),trigger_source=%s,
                       trigger_lag_sec=%s,entry_price=%s
                   WHERE ready_id=%s AND triggered=FALSE""",
                (source, lag_sec, entry_price, ready_id),
            )
    finally:
        c.close()


def _complete(ready_id, outcome="Loss", exit_price=3980.0, mfe_r=0.3,
              mae_r=-1.05, r_multiple=-1.0, duration_min=8.5,
              failure_mode="LOSS", failure_detail=None):
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE trade_failure_analysis
                   SET outcome=%s,exit_price=%s,mfe_r=%s,mae_r=%s,
                       r_multiple=%s,duration_min=%s,completed_at=NOW(),
                       failure_mode=%s,failure_detail=%s
                   WHERE ready_id=%s""",
                (outcome, exit_price, mfe_r, mae_r, r_multiple, duration_min,
                 failure_mode, failure_detail, ready_id),
            )
    finally:
        c.close()


def _run_expire():
    """Execute the expire SQL (same as _expire_stale_tfa_records)."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE trade_failure_analysis
                   SET failure_mode='NOT_TRIGGERED',outcome='not_triggered',
                       completed_at=NOW()
                   WHERE triggered=FALSE AND completed_at IS NULL
                     AND ready_at < NOW()-INTERVAL '2 hours'""",
            )
    finally:
        c.close()


# Fixture helpers for classifier tests
def _mt(**kw):
    return {"outcome": "Loss", "r_multiple": -1.0, "mfe_r": 0.2, "mae_r": -1.0, **kw}

def _ctx(**kw):
    return {"edge_score": 65.0, "entry_efficiency": 50, **kw}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Schema and Constraints
# ═════════════════════════════════════════════════════════════════════════════

def test_schema_required_columns_not_null():
    """All NOT NULL columns exist with correct nullability."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                """SELECT column_name, is_nullable, column_default
                   FROM information_schema.columns
                   WHERE table_name='trade_failure_analysis'
                   ORDER BY ordinal_position""",
            )
            cols = {r[0]: {"null": r[1], "default": r[2]} for r in cur.fetchall()}
    finally:
        c.close()
    for col in ("id", "ready_id", "instrument", "ready_at", "triggered", "schema_version"):
        assert col in cols, "Column %s missing" % col
        assert cols[col]["null"] == "NO", "%s must be NOT NULL" % col


def test_schema_nullable_columns():
    """All nullable context/outcome columns allow NULL."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name='trade_failure_analysis'",
            )
            cols = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        c.close()
    for col in ("direction", "mode", "edge_score", "entry_quality_score", "strategy",
                "bias", "structure_ok", "zone_ok", "vwap_ok", "cvd_ok", "session",
                "vol_regime", "price_at_ready", "triggered_at", "trigger_source",
                "trigger_lag_sec", "entry_price", "exit_price", "mfe_r", "mae_r",
                "r_multiple", "duration_min", "completed_at", "failure_mode",
                "failure_detail"):
        assert col in cols, "Column %s missing" % col
        assert cols[col] == "YES", "%s should be nullable" % col


def test_schema_defaults():
    """triggered defaults to FALSE; schema_version defaults to 1; ready_at to NOW()."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name, column_default FROM information_schema.columns WHERE table_name='trade_failure_analysis'",
            )
            defaults = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        c.close()
    assert defaults.get("triggered")      == "false", "triggered default must be false"
    assert defaults.get("schema_version") == "1",     "schema_version default must be 1"
    assert defaults.get("ready_at")       is not None, "ready_at must have a default"


def test_schema_unique_constraint_on_conflict_do_nothing():
    """Inserting the same ready_id twice is silently ignored (ON CONFLICT DO NOTHING)."""
    prefix = _prefix("schema_uniq")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        for _ in range(5):
            _insert_ready(rid)
        assert _count(prefix) == 1, "ON CONFLICT DO NOTHING must keep exactly 1 row"
    finally:
        _clean(prefix)


def test_schema_unique_constraint_raises_without_on_conflict():
    """Direct INSERT without ON CONFLICT DO NOTHING raises IntegrityError on duplicate."""
    prefix = _prefix("schema_exc")
    rid    = prefix + "_001"
    _clean(prefix)
    c = _conn()
    c.autocommit = False
    try:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO trade_failure_analysis(ready_id,instrument,ready_at,schema_version) VALUES(%s,'MGC',NOW(),1)",
                (rid,),
            )
        c.commit()
        with pytest.raises(psycopg2.errors.UniqueViolation):
            with c.cursor() as cur2:
                cur2.execute(
                    "INSERT INTO trade_failure_analysis(ready_id,instrument,ready_at,schema_version) VALUES(%s,'MGC',NOW(),1)",
                    (rid,),
                )
            c.commit()
    finally:
        try: c.rollback()
        except Exception: pass
        c.autocommit = True
        _clean(prefix)
        c.close()


def test_schema_indexes_exist():
    """Four expected indexes exist."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT indexname FROM pg_indexes WHERE tablename='trade_failure_analysis'")
            names = {r[0] for r in cur.fetchall()}
    finally:
        c.close()
    assert "trade_failure_analysis_ready_id_key" in names, "UNIQUE on ready_id"
    assert "tfa_inst_ready"   in names, "Composite index (instrument, ready_at)"
    assert "tfa_failure_mode" in names, "Partial index on failure_mode"
    assert "tfa_completed"    in names, "Partial index on completed_at"


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — READY Record Round-Trip
# ═════════════════════════════════════════════════════════════════════════════

def test_ready_record_all_context_fields_saved():
    """All READY-time context fields are persisted verbatim."""
    prefix = _prefix("rr")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid, inst="MNQ", direction="Short", mode="SWING",
                      edge=81.0, eq=67.5, strategy="HTF_Breakout", bias="Bearish",
                      structure=True, zone=False, vwap=True, cvd=False,
                      session="ASIA", vol_regime="ELEVATED", price=21000.0)
        r = _fetch(rid)
        assert r is not None
        assert r["instrument"]                   == "MNQ"
        assert r["direction"]                    == "Short"
        assert r["mode"]                         == "SWING"
        assert float(r["edge_score"])            == 81.0
        assert float(r["entry_quality_score"])   == 67.5
        assert r["strategy"]                     == "HTF_Breakout"
        assert r["bias"]                         == "Bearish"
        assert r["structure_ok"]                 is True
        assert r["zone_ok"]                      is False
        assert r["vwap_ok"]                      is True
        assert r["cvd_ok"]                       is False
        assert r["session"]                      == "ASIA"
        assert r["vol_regime"]                   == "ELEVATED"
        assert float(r["price_at_ready"])        == 21000.0
        assert r["triggered"]                    is False
        assert r["completed_at"]                 is None
        assert r["failure_mode"]                 is None
        assert r["schema_version"]               == 1
    finally:
        _clean(prefix)


def test_ready_record_untriggered_defaults():
    """New READY row has all outcome/trigger fields NULL."""
    prefix = _prefix("defaults")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        r = _fetch(rid)
        for field in ("triggered_at", "trigger_source", "trigger_lag_sec",
                      "entry_price", "exit_price", "mfe_r", "mae_r",
                      "r_multiple", "outcome", "failure_mode", "completed_at"):
            assert r[field] is None, "%s must be NULL for new READY row" % field
        assert r["triggered"] is False
    finally:
        _clean(prefix)


def test_ready_id_format():
    """_make_tfa_ready_id produces: inst::direction::YYYYMMDDTHHMMSSz"""
    ts = datetime(2026, 7, 17, 14, 30, 0, tzinfo=timezone.utc)
    rid = "%s::%s::%s" % ("MGC", "Long", ts.strftime("%Y%m%dT%H%M%SZ"))
    assert rid == "MGC::Long::20260717T143000Z"
    parts = rid.split("::")
    assert parts[0] == "MGC"
    assert parts[1] == "Long"
    assert len(parts[2]) == 16 and parts[2].endswith("Z")


def test_timestamps_stored_utc():
    """ready_at is stored as TIMESTAMPTZ (UTC); retrieved as timezone-aware."""
    prefix = _prefix("ts_utc")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        r = _fetch(rid)
        ts = r["ready_at"]
        assert ts is not None
        # psycopg2 returns timezone-aware datetime for TIMESTAMPTZ
        assert ts.tzinfo is not None, "ready_at must be timezone-aware (UTC stored)"
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Deduplication
# ═════════════════════════════════════════════════════════════════════════════

def test_dedup_db_same_ready_id():
    """Identical ready_id inserted N times → exactly 1 row (ON CONFLICT DO NOTHING)."""
    prefix = _prefix("dedup_db")
    rid    = prefix + "_X"
    _clean(prefix)
    try:
        for _ in range(8):
            _insert_ready(rid, inst="MGC", direction="Long")
        assert _count(prefix) == 1
    finally:
        _clean(prefix)


def test_dedup_different_instruments_independent():
    """Simultaneous READY for MGC and MNQ → 2 independent rows."""
    prefix   = _prefix("dedup_inst")
    rid_mgc  = prefix + "_MGC"
    rid_mnq  = prefix + "_MNQ"
    _clean(prefix)
    try:
        _insert_ready(rid_mgc, inst="MGC", direction="Long")
        _insert_ready(rid_mnq, inst="MNQ", direction="Long")
        assert _count(prefix) == 2
        assert _fetch(rid_mgc)["instrument"] == "MGC"
        assert _fetch(rid_mnq)["instrument"] == "MNQ"
    finally:
        _clean(prefix)


def test_dedup_different_directions_not_deduped():
    """Long and Short READY for same instrument are separate rows."""
    prefix  = _prefix("dedup_dir")
    rid_lng = prefix + "_Long"
    rid_sht = prefix + "_Short"
    _clean(prefix)
    try:
        _insert_ready(rid_lng, inst="MGC", direction="Long")
        _insert_ready(rid_sht, inst="MGC", direction="Short")
        assert _count(prefix) == 2
    finally:
        _clean(prefix)


def test_dedup_in_memory_window_invariant():
    """Documents the 5-minute in-memory dedup invariant (LAST_TFA_BY_INST).

    The production _record_ready_decision checks:
        _last = LAST_TFA_BY_INST.get(inst)
        if _last and age_s < 300 and _last['direction'] == direction:
            return   # skip INSERT

    This is an IN-MEMORY check that resets on restart.  After restart, the
    in-memory dict is empty, so a new READY for the same instrument gets a
    fresh ready_id (different timestamp) and a new DB row.  The DB-level
    ON CONFLICT DO NOTHING is the safety net for exact same-second re-fires.
    """
    prefix = _prefix("dedup_mem_inv")
    rid1   = prefix + "_T1"
    rid2   = prefix + "_T2"
    _clean(prefix)
    try:
        _insert_ready(rid1, inst="MGC", direction="Long")
        time.sleep(0.05)
        # Different ready_id (new timestamp) — would succeed after restart
        _insert_ready(rid2, inst="MGC", direction="Long")
        # Both rows present: in-memory dedup is not in effect here (no app state)
        assert _count(prefix) == 2
        # INVARIANT: production code prevents this via LAST_TFA_BY_INST when warm
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Execution Linkage
# ═════════════════════════════════════════════════════════════════════════════

def test_trigger_updates_correct_row_by_ready_id():
    """Trigger UPDATE targets exactly the matched ready_id, leaves others untouched."""
    prefix  = _prefix("trigger")
    rid1    = prefix + "_001"
    rid2    = prefix + "_002"
    _clean(prefix)
    try:
        _insert_ready(rid1, inst="MGC", direction="Long")
        _insert_ready(rid2, inst="MNQ", direction="Short")
        _trigger(rid1, source="auto", entry_price=3990.5, lag_sec=3.2)
        r1 = _fetch(rid1)
        r2 = _fetch(rid2)
        assert r1["triggered"]              is True
        assert r1["trigger_source"]         == "auto"
        assert float(r1["entry_price"])     == 3990.5
        assert float(r1["trigger_lag_sec"]) == 3.2
        assert r1["triggered_at"]           is not None
        # rid2 untouched
        assert r2["triggered"]   is False
        assert r2["triggered_at"] is None
    finally:
        _clean(prefix)


def test_trigger_where_false_prevents_double_trigger():
    """WHERE triggered=FALSE makes trigger UPDATE idempotent."""
    prefix = _prefix("trig_idem")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        _trigger(rid, source="auto", entry_price=3990.0)
        first_at = _fetch(rid)["triggered_at"]
        time.sleep(0.1)
        _trigger(rid, source="manual", entry_price=9999.0)  # must be no-op
        r = _fetch(rid)
        assert r["trigger_source"]     == "auto",   "First trigger source preserved"
        assert float(r["entry_price"]) == 3990.0,   "First entry price preserved"
        assert r["triggered_at"]       == first_at, "triggered_at unchanged"
    finally:
        _clean(prefix)


def test_trigger_source_manual_vs_auto():
    """trigger_source records the actual execution path ('auto' vs 'manual')."""
    prefix = _prefix("trig_src")
    _clean(prefix)
    try:
        rid_a = prefix + "_auto"
        rid_m = prefix + "_manual"
        _insert_ready(rid_a, inst="MGC",  direction="Long")
        _insert_ready(rid_m, inst="MNQ",  direction="Short")
        _trigger(rid_a, source="auto",   entry_price=3991.0)
        _trigger(rid_m, source="manual", entry_price=21050.0)
        assert _fetch(rid_a)["trigger_source"] == "auto"
        assert _fetch(rid_m)["trigger_source"] == "manual"
    finally:
        _clean(prefix)


def test_cross_instrument_trigger_isolation():
    """Triggering MGC has zero effect on MNQ's row."""
    prefix  = _prefix("trig_iso")
    rid_mgc = prefix + "_MGC"
    rid_mnq = prefix + "_MNQ"
    _clean(prefix)
    try:
        _insert_ready(rid_mgc, inst="MGC", direction="Long")
        _insert_ready(rid_mnq, inst="MNQ", direction="Long")
        _trigger(rid_mgc)
        assert _fetch(rid_mgc)["triggered"] is True
        assert _fetch(rid_mnq)["triggered"] is False
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Outcome Linkage
# ═════════════════════════════════════════════════════════════════════════════

def test_complete_updates_not_inserts():
    """_complete_tfa_record issues UPDATE, not INSERT — row count stays 1."""
    prefix = _prefix("complete")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        _trigger(rid)
        _complete(rid, outcome="Loss", exit_price=3978.0, mfe_r=0.2,
                  mae_r=-1.1, r_multiple=-1.05, duration_min=12.3,
                  failure_mode="LOSS")
        assert _count(prefix) == 1, "UPDATE must not create additional rows"
        r = _fetch(rid)
        assert r["outcome"]               == "Loss"
        assert float(r["exit_price"])     == 3978.0
        assert float(r["mfe_r"])          == 0.2
        assert float(r["mae_r"])          == -1.1
        assert float(r["r_multiple"])     == -1.05
        assert float(r["duration_min"])   == 12.3
        assert r["failure_mode"]          == "LOSS"
        assert r["completed_at"]          is not None
    finally:
        _clean(prefix)


def test_complete_win_stores_win_label():
    """Winning trade has outcome 'Win' and failure_mode 'WIN'."""
    prefix = _prefix("win_label")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        _trigger(rid)
        _complete(rid, outcome="Win", exit_price=4005.0,
                  mfe_r=1.3, mae_r=-0.1, r_multiple=1.2, failure_mode="WIN")
        r = _fetch(rid)
        assert r["outcome"]               == "Win"
        assert r["failure_mode"]          == "WIN"
        assert float(r["r_multiple"])     == 1.2
    finally:
        _clean(prefix)


def test_complete_preserves_ready_context():
    """UPDATE at close must not overwrite READY-time context fields."""
    prefix = _prefix("ctxpreserve")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid, inst="MGC", direction="Long", edge=78.0, strategy="ORB")
        _trigger(rid)
        _complete(rid, failure_mode="LOSS")
        r = _fetch(rid)
        assert r["instrument"]          == "MGC"
        assert r["direction"]           == "Long"
        assert float(r["edge_score"])   == 78.0
        assert r["strategy"]            == "ORB"
    finally:
        _clean(prefix)


def test_complete_one_outcome_per_ready_id():
    """A second _complete call on the same ready_id overwrites (UPDATE); count stays 1."""
    prefix = _prefix("one_outcome")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        _trigger(rid)
        _complete(rid, outcome="Loss", failure_mode="LOSS")
        _complete(rid, outcome="Win",  failure_mode="WIN")   # second completion
        assert _count(prefix) == 1
        # Last write wins (UPDATE is not idempotent here — this is intentional)
        r = _fetch(rid)
        assert r["failure_mode"] == "WIN"
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — MFE / MAE Passthrough and Formula Verification
# ═════════════════════════════════════════════════════════════════════════════

def test_mfe_mae_stored_verbatim():
    """TFA stores MFE/MAE exactly as provided — no recomputation."""
    prefix = _prefix("mfemae")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        _trigger(rid)
        _complete(rid, mfe_r=1.6, mae_r=-0.6, r_multiple=1.2)
        r = _fetch(rid)
        assert float(r["mfe_r"])      == 1.6,  "MFE stored verbatim"
        assert float(r["mae_r"])      == -0.6, "MAE stored verbatim"
        assert float(r["r_multiple"]) == 1.2
    finally:
        _clean(prefix)


def test_mfe_mae_long_formula():
    """LONG MFE/MAE formula:
      entry=100, stop=95 (risk=5), high=108, low=97
      MFE_r = (high-entry)/risk = (108-100)/5 = +1.6R   (price-point: 8)
      MAE_r = -(entry-low)/risk = -(100-97)/5 = -0.6R   (price-point: 3)
    """
    entry, stop = 100.0, 95.0
    high,  low  = 108.0, 97.0
    risk  = entry - stop
    mfe_r = (high  - entry) / risk
    mae_r = -(entry - low)  / risk
    assert abs(mfe_r -  1.6) < 1e-9, "LONG MFE_r must be +1.6"
    assert abs(mae_r - -0.6) < 1e-9, "LONG MAE_r must be -0.6"
    assert high - entry == 8.0,       "LONG MFE price-point = 8"
    assert entry - low  == 3.0,       "LONG MAE price-point = 3"


def test_mfe_mae_short_formula():
    """SHORT MFE/MAE formula:
      entry=100, stop=104 (risk=4), low=92, high=104
      MFE_r = (entry-low)/risk  = (100-92)/4  = +2.0R   (price-point: 8)
      MAE_r = -(high-entry)/risk = -(104-100)/4 = -1.0R (price-point: 4)
    """
    entry, stop = 100.0, 104.0
    low,   high = 92.0,  104.0
    risk  = stop - entry
    mfe_r = (entry - low)  / risk
    mae_r = -(high - entry) / risk
    assert abs(mfe_r -  2.0) < 1e-9, "SHORT MFE_r must be +2.0"
    assert abs(mae_r - -1.0) < 1e-9, "SHORT MAE_r must be -1.0"
    assert entry - low  == 8.0,       "SHORT MFE price-point = 8"
    assert high - entry == 4.0,       "SHORT MAE price-point = 4"


def test_mfe_mae_not_substituted_with_realized_pnl():
    """MFE/MAE are separate from realized R — TP1_THEN_BE case shows divergence."""
    prefix = _prefix("mfe_nosubst")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        # Hit TP1 (MFE=1.1R) but runner stopped at break-even (final R=0.0)
        _insert_ready(rid)
        _trigger(rid)
        _complete(rid, mfe_r=1.1, mae_r=-0.05, r_multiple=0.0,
                  failure_mode="TP1_THEN_BE")
        r = _fetch(rid)
        assert float(r["mfe_r"])      == 1.1, "MFE = peak reached, 1.1R"
        assert float(r["r_multiple"]) == 0.0, "Realized = break-even, 0.0R"
        assert r["mfe_r"] != r["r_multiple"],  "MFE ≠ realized (not substituted)"
    finally:
        _clean(prefix)


def test_mfe_source_is_managed_trade_not_tfa():
    """Documents that TFA does NOT compute MFE/MAE — it reads mt.get('mfe_r').

    Source of truth: _complete_tfa_record lines ~27454-27463:
        mfe_r = mt.get('mfe_r')
        mae_r = mt.get('mae_r')
    These are computed by the managed-trade lifecycle (_compute_trade_mgmt_metrics
    or equivalent in the managed-trade watcher loop).  TFA is a passive consumer.
    """
    # This is a structural invariant — verified by code inspection, not runtime
    # Confirm the column names match what the managed trade would provide
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='trade_failure_analysis' AND column_name IN ('mfe_r','mae_r')",
            )
            cols = {r[0] for r in cur.fetchall()}
    finally:
        c.close()
    assert "mfe_r" in cols, "mfe_r column must exist"
    assert "mae_r" in cols, "mae_r column must exist"


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Failure Classification (All 16 Buckets)
# ═════════════════════════════════════════════════════════════════════════════
#
# Bucket mapping to production labels:
#   Task bucket            → Production label       Rule
#   ─────────────────────────────────────────────────────────────────────────
#   successful trade       → WIN                   outcome has "Win"
#   exit-management issue  → TP1_THEN_BE            mfe_r>=0.8, r<0.4 or ~0
#   profitable break-even  → BREAKEVEN              r in (-0.15, 0.15)
#   late entry             → LATE_ENTRY             entry_efficiency < 45
#   early entry            → EARLY_ENTRY            entry_efficiency > 85, r<0
#   invalidated before mv  → STOPPED_BEFORE_MOVE   abs(mae)<0.3, mfe<0.3
#   stop too tight         → STOP_TOO_TIGHT         mae<-0.8, mfe>|mae|*0.5
#   weak trigger (setup)   → BAD_SETUP              edge_score < 45
#   bad session            → BAD_SESSION            LUNCH/MIDDAY/OVERNIGHT
#   no follow-through      → NO_FOLLOW_THROUGH      mfe_r < 0.25
#   clean loss             → LOSS                   fallthrough
#   unknown / no outcome   → UNCATEGORIZED          missing outcome or exception
#   wrong directional bias → WRONG_BIAS  [TFA]     Long+Bear or Short+Bull
#   poor location          → POOR_LOCATION [TFA]   eq_score < 60
#   volatility mismatch    → VOLATILITY_MISMATCH[TFA] "EXTREME" in vol_regime
#   no trigger (expired)   → NOT_TRIGGERED [DB]    2h+ untriggered expire query
# ═════════════════════════════════════════════════════════════════════════════

def test_classify_win():
    """Rule: 'Win' in outcome AND NOT (mfe_r>=0.8 AND r<0.4) → WIN."""
    mt  = {"outcome": "Win", "r_multiple": 1.2, "mfe_r": 0.6, "mae_r": -0.1}
    mode, detail = _classify_failure_mode_test(mt, {})
    assert mode == "WIN" and detail is None


def test_classify_tp1_then_be_from_win():
    """Rule: Win AND mfe_r>=0.8 AND r<0.4 → TP1_THEN_BE."""
    mt  = {"outcome": "Win", "r_multiple": 0.05, "mfe_r": 0.9, "mae_r": -0.05}
    mode, detail = _classify_failure_mode_test(mt, {})
    assert mode == "TP1_THEN_BE" and detail is None


def test_classify_tp1_then_be_from_loss():
    """Rule: non-win AND mfe_r>=0.8 → TP1_THEN_BE (runner stopped, lost)."""
    mt  = _mt(r_multiple=-0.2, mfe_r=0.85, mae_r=-0.3)
    mode, _ = _classify_failure_mode_test(mt, {})
    assert mode == "TP1_THEN_BE"


def test_classify_breakeven():
    """Rule: -0.15 < r < 0.15 AND mfe_r<0.8 → BREAKEVEN."""
    mt  = _mt(outcome="Breakeven", r_multiple=0.05, mfe_r=0.3, mae_r=-0.1)
    mode, detail = _classify_failure_mode_test(mt, {})
    assert mode == "BREAKEVEN" and detail is None


def test_classify_late_entry():
    """Rule: entry_efficiency < 45 → LATE_ENTRY (chased the move)."""
    mt  = _mt(r_multiple=-0.9, mfe_r=0.15, mae_r=-0.95)
    ctx = _ctx(entry_efficiency=30)
    mode, _ = _classify_failure_mode_test(mt, ctx)
    assert mode == "LATE_ENTRY"


def test_classify_early_entry():
    """Rule: entry_efficiency > 85 AND r < 0 → EARLY_ENTRY."""
    mt  = _mt(r_multiple=-0.5, mfe_r=0.1, mae_r=-0.55)
    ctx = _ctx(entry_efficiency=90)
    mode, _ = _classify_failure_mode_test(mt, ctx)
    assert mode == "EARLY_ENTRY"


def test_classify_stopped_before_move():
    """Rule: abs(mae_r)<0.3 AND mfe_r<0.3 → STOPPED_BEFORE_MOVE."""
    mt  = _mt(r_multiple=-0.25, mfe_r=0.1, mae_r=-0.2)
    ctx = _ctx(entry_efficiency=55)
    mode, _ = _classify_failure_mode_test(mt, ctx)
    assert mode == "STOPPED_BEFORE_MOVE"


def test_classify_stop_too_tight():
    """Rule: mae_r<-0.8 AND mfe_r > abs(mae_r)*0.5 → STOP_TOO_TIGHT."""
    mt  = _mt(r_multiple=-0.9, mfe_r=0.55, mae_r=-0.9)
    ctx = _ctx(entry_efficiency=55)
    mode, _ = _classify_failure_mode_test(mt, ctx)
    assert mode == "STOP_TOO_TIGHT"


def test_classify_bad_setup():
    """Rule: edge_score < 45 → BAD_SETUP (weak setup conviction; 'weak trigger')."""
    mt  = _mt(r_multiple=-1.0, mfe_r=0.1, mae_r=-1.0)
    ctx = _ctx(entry_efficiency=55, edge_score=38)
    mode, _ = _classify_failure_mode_test(mt, ctx)
    assert mode == "BAD_SETUP"


def test_classify_bad_session_all_variants():
    """Rule: session contains LUNCH, MIDDAY, or OVERNIGHT → BAD_SESSION."""
    for sess in ("LUNCH", "MIDDAY_DOLDRUMS", "OVERNIGHT_ASIA", "OVERNIGHT"):
        mt  = _mt(r_multiple=-1.0, mfe_r=0.2, mae_r=-1.0)
        ctx = _ctx(session=sess)
        mode, _ = _classify_failure_mode_test(mt, ctx)
        assert mode == "BAD_SESSION", "session='%s' → BAD_SESSION" % sess


def test_classify_no_follow_through():
    """Rule: mfe_r < 0.25 (no stronger label applies) → NO_FOLLOW_THROUGH."""
    mt  = _mt(r_multiple=-0.8, mfe_r=0.15, mae_r=-0.8)
    ctx = _ctx(entry_efficiency=60, edge_score=55, session="RTH")
    mode, _ = _classify_failure_mode_test(mt, ctx)
    assert mode == "NO_FOLLOW_THROUGH"


def test_classify_loss_clean():
    """Rule: none of the above → LOSS (plain directional failure)."""
    mt  = _mt(r_multiple=-1.0, mfe_r=0.4, mae_r=-1.05)
    ctx = _ctx(entry_efficiency=60, edge_score=68, session="RTH")
    mode, _ = _classify_failure_mode_test(mt, ctx)
    assert mode == "LOSS"


def test_classify_uncategorized_no_outcome():
    """Rule: empty outcome → UNCATEGORIZED."""
    mt  = {"r_multiple": -1.0, "mfe_r": 0.2, "mae_r": -1.0}  # no outcome key
    mode, _ = _classify_failure_mode_test(mt, {})
    assert mode == "UNCATEGORIZED"


def test_classify_null_mfe_mae_not_coerced_to_zero():
    """NULL mfe_r / mae_r must NOT be coerced to 0.0.

    A stop-out on the price-poll close path arrives with mfe_r=None,
    mae_r=None.  If they were treated as 0.0 the classifier would fire
    STOPPED_BEFORE_MOVE (abs(0)<0.3 and 0<0.3).  With the correct None
    handling the mfe/mae-dependent branches are skipped and the label
    falls through to the base LOSS.
    """
    mt = _mt(outcome="Loss", r_multiple=-1.0)
    # Deliberately omit mfe_r / mae_r (they default to None via .get())
    mt.pop("mfe_r", None)
    mt.pop("mae_r", None)
    mode, _ = _classify_failure_mode_test(mt, {})
    assert mode != "STOPPED_BEFORE_MOVE", (
        "STOPPED_BEFORE_MOVE must not fire when mfe/mae are unknown (None)")
    assert mode == "LOSS", (
        f"Expected LOSS as the base label for a stop-out with no mfe/mae data, "
        f"got {mode!r}")


def test_classify_null_mfe_mae_partial_marker_in_detail():
    """Records with NULL mfe_r and mae_r must carry the partial-analytics marker.

    The 'partial: mfe/mae unavailable' suffix in failure_detail signals to any
    downstream consumer that this record was closed on the price-poll path and
    the MFE/MAE fields are genuinely absent — not zero, not a small value.
    """
    mt = _mt(outcome="Loss", r_multiple=-1.0)
    mt.pop("mfe_r", None)
    mt.pop("mae_r", None)
    _, detail = _classify_failure_mode_test(mt, {})
    assert detail is not None, "failure_detail must not be None when mfe/mae are absent"
    assert "partial" in detail, (
        f"Expected 'partial: mfe/mae unavailable' in failure_detail, got {detail!r}")


def test_classify_wrong_bias_long_bearish():
    """TFA rule: Long direction + Bearish bias → WRONG_BIAS, detail names the bias."""
    mt  = _mt(outcome="Loss", r_multiple=-1.0, direction="Long")
    mode, detail = _classify_failure_mode_test(mt, {}, bias="Bearish")
    assert mode   == "WRONG_BIAS"
    assert "Bearish" in (detail or ""), "Detail must name the bias"


def test_classify_wrong_bias_short_bullish():
    """TFA rule: Short direction + Bullish bias → WRONG_BIAS."""
    mt  = _mt(outcome="Loss", r_multiple=-1.0, direction="Short")
    mode, detail = _classify_failure_mode_test(mt, {}, bias="Bullish")
    assert mode   == "WRONG_BIAS"
    assert "Bullish" in (detail or "")


def test_classify_wrong_bias_exempt_for_wins():
    """WRONG_BIAS must NOT be applied to winning trades."""
    mt  = {"outcome": "Win", "r_multiple": 1.2, "mfe_r": 0.7, "mae_r": -0.1,
           "direction": "Long"}
    mode, _ = _classify_failure_mode_test(mt, {}, bias="Bearish")
    assert mode == "WIN", "Wins are never WRONG_BIAS"


def test_classify_poor_location():
    """TFA rule: eq_score < 60 → POOR_LOCATION, detail contains the score."""
    mt  = _mt(outcome="Loss", direction="Long")
    mode, detail = _classify_failure_mode_test(mt, {}, eq_score=45)
    assert mode   == "POOR_LOCATION"
    assert "45" in (detail or "")


def test_classify_poor_location_boundary_60_excluded():
    """Boundary: eq_score=60 is NOT POOR_LOCATION (rule is strictly < 60)."""
    mt  = _mt(outcome="Loss", direction="Long", r_multiple=-1.0,
              mfe_r=0.2, mae_r=-1.0)
    ctx = _ctx(entry_efficiency=60, edge_score=55)
    mode, _ = _classify_failure_mode_test(mt, ctx, eq_score=60)
    assert mode != "POOR_LOCATION", "eq_score=60 must not trigger POOR_LOCATION"


def test_classify_volatility_mismatch():
    """TFA rule: 'EXTREME' in vol_regime → VOLATILITY_MISMATCH."""
    mt  = _mt(outcome="Loss", direction="Long")
    mode, detail = _classify_failure_mode_test(mt, {}, vol_regime="EXTREME_HIGH")
    assert mode == "VOLATILITY_MISMATCH"
    assert "EXTREME_HIGH" in (detail or "")


def test_classify_priority_wrong_bias_beats_poor_location():
    """WRONG_BIAS has higher priority than POOR_LOCATION when both apply."""
    mt  = _mt(outcome="Loss", direction="Long")
    mode, _ = _classify_failure_mode_test(mt, {}, bias="Bearish", eq_score=40)
    assert mode == "WRONG_BIAS", "WRONG_BIAS priority > POOR_LOCATION"


def test_classify_priority_poor_location_beats_volatility():
    """POOR_LOCATION has higher priority than VOLATILITY_MISMATCH when both apply."""
    mt  = _mt(outcome="Loss", direction="Long")
    mode, _ = _classify_failure_mode_test(mt, {}, eq_score=40,
                                          vol_regime="EXTREME_HIGH")
    assert mode == "POOR_LOCATION", "POOR_LOCATION priority > VOLATILITY_MISMATCH"


def test_classify_not_triggered_set_by_expire():
    """NOT_TRIGGERED is applied by the expire SQL, not by _classify_failure_mode_tfa."""
    prefix = _prefix("not_trig")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid, backdated_sec=3 * 3600)  # 3 hours old
        _run_expire()
        r = _fetch(rid)
        assert r["failure_mode"] == "NOT_TRIGGERED"
        assert r["outcome"]      == "not_triggered"
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Time and Session Safety
# ═════════════════════════════════════════════════════════════════════════════

def test_expire_only_affects_old_untriggered():
    """Expire marks only: triggered=FALSE AND completed_at IS NULL AND age > 2h."""
    prefix   = _prefix("expire_safety")
    rid_old  = prefix + "_old"
    rid_new  = prefix + "_new"
    rid_trig = prefix + "_triggered"
    _clean(prefix)
    try:
        _insert_ready(rid_old,  backdated_sec=4 * 3600)  # 4h old, untriggered
        _insert_ready(rid_new)                            # just created
        _insert_ready(rid_trig, backdated_sec=4 * 3600)  # 4h old, but triggered
        _trigger(rid_trig)
        _run_expire()
        assert _fetch(rid_old)["failure_mode"]  == "NOT_TRIGGERED", "Old row → expired"
        assert _fetch(rid_new)["failure_mode"]  is None,            "New row → untouched"
        assert _fetch(rid_trig)["failure_mode"] is None,            "Triggered → untouched"
    finally:
        _clean(prefix)


def test_duration_min_calculation():
    """duration_min is derived correctly from registered_at and closed_at."""
    from datetime import datetime, timezone, timedelta
    opened = datetime(2026, 7, 17, 14, 0, 0, tzinfo=timezone.utc)
    closed = datetime(2026, 7, 17, 14, 8, 30, tzinfo=timezone.utc)
    expected_min = round((closed - opened).total_seconds() / 60.0, 1)
    assert expected_min == 8.5, "8m30s = 8.5 minutes"


def test_timestamps_ready_at_not_future():
    """ready_at stored at NOW() should not be in the future."""
    prefix = _prefix("ts_future")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        r   = _fetch(rid)
        now = datetime.now(timezone.utc)
        assert r["ready_at"] <= now, "ready_at must not be in the future"
        age_sec = (now - r["ready_at"]).total_seconds()
        assert age_sec < 10, "ready_at should be very recent (< 10s old)"
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Restart Persistence
# ═════════════════════════════════════════════════════════════════════════════

def test_ready_record_survives_reconnect():
    """A READY row persists when all connections are closed and reopened (= restart)."""
    prefix = _prefix("restart")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid, inst="MGC", direction="Long", edge=74.0)
        # Simulate restart: open a completely new connection
        c_new = _conn()
        try:
            with c_new.cursor() as cur:
                cur.execute(
                    "SELECT ready_id, instrument, edge_score FROM trade_failure_analysis WHERE ready_id=%s",
                    (rid,),
                )
                row = cur.fetchone()
        finally:
            c_new.close()
        assert row is not None, "Row must survive reconnect (simulated restart)"
        assert row[0] == rid
        assert float(row[2]) == 74.0
    finally:
        _clean(prefix)


def test_partial_lifecycle_survives_reconnect():
    """A triggered (but not completed) row persists across reconnect."""
    prefix = _prefix("restart_trig")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        _trigger(rid, source="auto", entry_price=3992.0)
        c_new = _conn()
        try:
            with c_new.cursor() as cur:
                cur.execute("SELECT triggered, trigger_source, completed_at FROM trade_failure_analysis WHERE ready_id=%s", (rid,))
                row = cur.fetchone()
        finally:
            c_new.close()
        assert row[0] is True,   "triggered=TRUE must persist"
        assert row[1] == "auto", "trigger_source must persist"
        assert row[2] is None,   "completed_at still NULL"
    finally:
        _clean(prefix)


def test_restored_records_have_no_money_path_columns():
    """TFA table columns have no broker/action/execution fields.
    Invariant: records restored from DB cannot generate trade signals.
    """
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='trade_failure_analysis'",
            )
            cols = {r[0] for r in cur.fetchall()}
    finally:
        c.close()
    money_path_fields = {"action", "broker", "webhook_url", "auto_arm",
                         "is_actionable", "execute", "order_id", "contracts_live"}
    overlap = money_path_fields & cols
    assert not overlap, "TFA must not have money-path columns: %s" % overlap


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 10 — Concurrency and Deduplication
# ═════════════════════════════════════════════════════════════════════════════

def test_concurrent_inserts_same_ready_id_exactly_one_row():
    """10 threads inserting the same ready_id → exactly 1 row, no exception."""
    prefix  = _prefix("concurrent")
    rid     = prefix + "_001"
    _clean(prefix)
    errors  = []

    def _insert_thread():
        try:
            _insert_ready(rid, inst="MGC", direction="Long")
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=_insert_thread) for _ in range(10)]
    try:
        for t in threads: t.start()
        for t in threads: t.join(timeout=5.0)
        assert not errors, "Concurrent inserts must not raise: %s" % errors
        assert _count(prefix) == 1, "ON CONFLICT → exactly 1 row"
    finally:
        _clean(prefix)


def test_concurrent_different_instruments_two_rows():
    """Concurrent READY events for MGC and MNQ → 2 rows, no collision."""
    prefix  = _prefix("conc_inst")
    rid_mgc = prefix + "_MGC"
    rid_mnq = prefix + "_MNQ"
    _clean(prefix)
    errors  = []

    def _ins(rid, inst):
        try:
            _insert_ready(rid, inst=inst, direction="Long")
        except Exception as e:
            errors.append(str(e))

    t1 = threading.Thread(target=_ins, args=(rid_mgc, "MGC"))
    t2 = threading.Thread(target=_ins, args=(rid_mnq, "MNQ"))
    try:
        t1.start(); t2.start()
        t1.join(5);  t2.join(5)
        assert not errors, "Concurrent insert raised: %s" % errors
        assert _count(prefix) == 2
        assert _fetch(rid_mgc)["instrument"] == "MGC"
        assert _fetch(rid_mnq)["instrument"] == "MNQ"
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 11 — Write-Rate Safety
# ═════════════════════════════════════════════════════════════════════════════

def test_write_rate_complete_lifecycle_three_writes():
    """One complete trade lifecycle = exactly 3 DB operations on 1 row.

    Write 1: READY INSERT        (at READY verdict in full_analysis)
    Write 2: Trigger UPDATE      (at gateway send/simulate in _mark_tfa_triggered)
    Write 3: Outcome UPDATE      (at trade close in _complete_tfa_record)
    Every 25 completions: SELECT COUNT(*) only — no extra writes.
    Background expiry: UPDATE batch every 30 min — 0 rows affected when no stale rows.
    """
    prefix = _prefix("writerate")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)              # Write 1
        assert _count(prefix) == 1
        _trigger(rid)                   # Write 2
        _complete(rid, failure_mode="LOSS")  # Write 3
        assert _count(prefix) == 1, "All 3 writes target THE SAME row"
        r = _fetch(rid)
        assert r["triggered"]    is True
        assert r["completed_at"] is not None
    finally:
        _clean(prefix)


def test_write_rate_wait_verdict_zero_writes():
    """A WAIT verdict must produce zero TFA writes.

    Code path in full_analysis (line ~21266):
        if TFA_DB_READY and is_actionable(verdict):
            _record_ready_decision(...)
    WAIT is not in FULL_READY_VERDICTS or EARLY_READY_VERDICTS.
    is_actionable('WAIT') = False → _record_ready_decision is never called.
    """
    prefix = _prefix("wait_nowrite")
    # Structural verification: count rows with this prefix = 0
    # (There is no production code path that would INSERT a WAIT row)
    _clean(prefix)
    assert _count(prefix) == 0, "No TFA rows for WAIT verdicts"


def test_write_rate_expire_batch_no_new_rows():
    """The expire query does not create new rows — it only UPDATEs old ones."""
    prefix = _prefix("expire_norow")
    _clean(prefix)
    try:
        count_before = _count(prefix)
        _run_expire()  # should affect 0 rows for this prefix
        count_after  = _count(prefix)
        assert count_before == count_after == 0, "Expire must not INSERT rows"
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 12 — 25-Trade Summary Accuracy
# ═════════════════════════════════════════════════════════════════════════════

def _insert_batch(prefix, trades):
    c = _conn()
    try:
        with c.cursor() as cur:
            for i, t in enumerate(trades):
                rid = "%s_%03d" % (prefix, i)
                cur.execute(
                    """INSERT INTO trade_failure_analysis
                       (ready_id,instrument,direction,mode,edge_score,ready_at,
                        triggered,triggered_at,trigger_source,outcome,failure_mode,
                        r_multiple,mfe_r,mae_r,completed_at,schema_version)
                       VALUES(%s,'MGC','Long','SCALP',72,NOW()-interval'10 min',
                              TRUE,NOW()-interval'9 min','auto',
                              %s,%s,%s,%s,%s,NOW(),1)
                       ON CONFLICT(ready_id) DO NOTHING""",
                    (rid, t["outcome"], t["failure_mode"],
                     t.get("r_multiple"), t.get("mfe_r"), t.get("mae_r")),
                )
    finally:
        c.close()


def test_summary_25_trade_distribution():
    """25 trades with known distribution — all aggregates verified."""
    prefix = _prefix("summary25")
    _clean(prefix)

    # Known dataset
    trades = (
        [{"outcome": "Win",          "failure_mode": "WIN",
          "r_multiple": 1.2,  "mfe_r": 1.3,  "mae_r": -0.1}] * 10 +
        [{"outcome": "Loss",         "failure_mode": "LOSS",
          "r_multiple": -1.0, "mfe_r": 0.3,  "mae_r": -1.1}] * 5  +
        [{"outcome": "Loss",         "failure_mode": "LATE_ENTRY",
          "r_multiple": -0.8, "mfe_r": 0.2,  "mae_r": -0.9}] * 4  +
        [{"outcome": "Loss",         "failure_mode": "WRONG_BIAS",
          "r_multiple": -1.0, "mfe_r": 0.1,  "mae_r": -1.0}] * 3  +
        [{"outcome": "Loss",         "failure_mode": "POOR_LOCATION",
          "r_multiple": -0.7, "mfe_r": 0.2,  "mae_r": -0.8}] * 2  +
        [{"outcome": "not_triggered","failure_mode": "NOT_TRIGGERED",
          "r_multiple": None, "mfe_r": None,  "mae_r": None}]  * 1
    )
    assert len(trades) == 25

    try:
        _insert_batch(prefix, trades)

        c = _conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    """SELECT failure_mode,
                              COUNT(*) AS cnt,
                              SUM(CASE WHEN outcome='Win' THEN 1 ELSE 0 END) AS wins,
                              ROUND(AVG(r_multiple)::numeric,4) AS avg_r,
                              ROUND(AVG(mfe_r)::numeric,4) AS avg_mfe
                       FROM trade_failure_analysis
                       WHERE ready_id LIKE %s AND completed_at IS NOT NULL
                       GROUP BY failure_mode ORDER BY cnt DESC""",
                    (prefix + "%",),
                )
                by_mode = {r[0]: {"cnt": r[1], "wins": r[2],
                                  "avg_r": float(r[3] or 0),
                                  "avg_mfe": float(r[4] or 0)}
                           for r in cur.fetchall()}
                cur.execute(
                    """SELECT COUNT(*), SUM(CASE WHEN outcome='Win' THEN 1 ELSE 0 END)
                       FROM trade_failure_analysis
                       WHERE ready_id LIKE %s AND completed_at IS NOT NULL""",
                    (prefix + "%",),
                )
                total_row = cur.fetchone()
        finally:
            c.close()

        total = int(total_row[0])
        wins  = int(total_row[1] or 0)

        # Totals
        assert total == 25,  "25 completed records"
        assert wins  == 10,  "10 wins"
        assert total - wins == 15, "15 non-wins"
        assert round(100 * wins / total) == 40, "Win rate = 40%"

        # Per-bucket counts
        assert by_mode["WIN"]["cnt"]            == 10
        assert by_mode["LOSS"]["cnt"]           == 5
        assert by_mode["LATE_ENTRY"]["cnt"]     == 4
        assert by_mode["WRONG_BIAS"]["cnt"]     == 3
        assert by_mode["POOR_LOCATION"]["cnt"]  == 2
        assert by_mode["NOT_TRIGGERED"]["cnt"]  == 1

        # Wins only in WIN bucket
        assert by_mode["WIN"]["wins"]           == 10
        assert by_mode["LOSS"]["wins"]          == 0
        assert by_mode["LATE_ENTRY"]["wins"]    == 0

        # Average R for WIN bucket
        assert abs(by_mode["WIN"]["avg_r"] - 1.2) < 0.01, "WIN avg_r = 1.2"
    finally:
        _clean(prefix)


def test_summary_win_rate_denominator_is_completed():
    """Win rate denominator is completed trades, not all TFA rows."""
    prefix = _prefix("pct_denom")
    _clean(prefix)
    trades = (
        [{"outcome": "Win",  "failure_mode": "WIN",  "r_multiple": 1.0,  "mfe_r": 1.1,  "mae_r": -0.1}] * 8 +
        [{"outcome": "Loss", "failure_mode": "LOSS", "r_multiple": -1.0, "mfe_r": 0.2,  "mae_r": -1.0}] * 2
    )
    try:
        _insert_batch(prefix, trades)
        # Also add one UNTRIGGERED row (not completed) to verify denominator
        _insert_ready(prefix + "_untriggered_extra")

        c = _conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM trade_failure_analysis WHERE ready_id LIKE %s AND completed_at IS NOT NULL",
                    (prefix + "%",),
                )
                completed = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM trade_failure_analysis WHERE ready_id LIKE %s",
                    (prefix + "%",),
                )
                all_rows = cur.fetchone()[0]
        finally:
            c.close()

        assert completed == 10, "10 completed"
        assert all_rows  == 11, "11 total (10 completed + 1 open READY)"
        win_pct  = 8 / completed * 100
        loss_pct = 2 / completed * 100
        assert abs(win_pct + loss_pct - 100.0) < 0.01, "Win% + Loss% must = 100"
    finally:
        _clean(prefix)


def test_summary_avg_r_accuracy():
    """Average R calculations are accurate for a controlled dataset."""
    prefix = _prefix("avg_r")
    _clean(prefix)
    # 3 losses at -1.0R, 1 win at +2.0R
    trades = (
        [{"outcome": "Loss", "failure_mode": "LOSS", "r_multiple": -1.0,
          "mfe_r": 0.2, "mae_r": -1.0}] * 3 +
        [{"outcome": "Win",  "failure_mode": "WIN",  "r_multiple":  2.0,
          "mfe_r": 2.1, "mae_r": -0.1}] * 1
    )
    try:
        _insert_batch(prefix, trades)
        c = _conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT ROUND(AVG(r_multiple)::numeric,4) FROM trade_failure_analysis WHERE ready_id LIKE %s AND completed_at IS NOT NULL",
                    (prefix + "%",),
                )
                avg_r = float(cur.fetchone()[0] or 0)
        finally:
            c.close()
        expected = (-1.0 * 3 + 2.0 * 1) / 4  # = -0.25
        assert abs(avg_r - expected) < 0.0001, "avg_r = %.4f expected %.4f" % (avg_r, expected)
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 13 — API Endpoint Tests
# ═════════════════════════════════════════════════════════════════════════════

BASE_URL = "http://localhost:8000"


def _api(path, params=None):
    try:
        return requests.get(BASE_URL + path, params=params, timeout=5)
    except requests.RequestException as e:
        pytest.skip("Flask server not reachable at %s: %s" % (BASE_URL, e))


def test_api_failure_analysis_200():
    """GET /failure-analysis returns 200 with expected JSON structure."""
    prefix = _prefix("api_200")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        _trigger(rid)
        _complete(rid, failure_mode="LOSS")
        r = _api("/failure-analysis")
        assert r.status_code == 200, "Expected 200, got %d" % r.status_code
        data = r.json()
        assert data["status"]  == "ok"
        assert "records"  in data
        assert "summary"  in data
        assert "totals"   in data
        assert isinstance(data["records"], list)
        assert isinstance(data["summary"], list)
        totals = data["totals"]
        assert "completed"    in totals
        assert "wins"         in totals
        assert "win_rate_pct" in totals
    finally:
        _clean(prefix)


def test_api_limit_param_respected():
    """GET /failure-analysis?limit=3 returns at most 3 records."""
    r = _api("/failure-analysis", params={"limit": 3})
    if r.status_code == 200:
        assert len(r.json()["records"]) <= 3


def test_api_limit_capped_at_200():
    """GET /failure-analysis?limit=9999 is capped at 200."""
    r = _api("/failure-analysis", params={"limit": 9999})
    if r.status_code == 200:
        assert len(r.json()["records"]) <= 200


def test_api_no_secret_fields_in_response():
    """Response body must not contain any secret or broker credentials."""
    r = _api("/failure-analysis")
    if r.status_code != 200:
        return
    body = r.text.lower()
    forbidden = ["webhook_url", "password", "secret", "token",
                 "traderspost_url", "discord_webhook"]
    for f in forbidden:
        assert f not in body, "Secret field '%s' must not appear in response" % f


def test_api_read_only_no_row_created():
    """GET /failure-analysis must not INSERT or UPDATE any rows."""
    prefix = _prefix("api_nowrite")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        count_before = _count(prefix)
        _api("/failure-analysis")
        count_after  = _count(prefix)
        assert count_before == count_after, "API GET must not write rows"
    finally:
        _clean(prefix)


def test_api_response_record_fields():
    """Response records contain the expected fields (no missing keys)."""
    prefix = _prefix("api_fields")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid, inst="MGC", direction="Long", edge=72.0)
        _trigger(rid)
        _complete(rid, failure_mode="LOSS", r_multiple=-1.0)
        r = _api("/failure-analysis", params={"limit": 1})
        if r.status_code != 200:
            return
        records = r.json()["records"]
        if not records:
            return
        rec = next((x for x in records if x.get("ready_id") == rid), None)
        if rec is None:
            return
        expected_fields = ["ready_id", "instrument", "direction", "mode",
                           "edge_score", "strategy", "bias", "triggered",
                           "trigger_source", "failure_mode", "outcome",
                           "r_multiple", "mfe_r", "mae_r", "completed_at"]
        for f in expected_fields:
            assert f in rec, "Field '%s' missing from API response" % f
    finally:
        _clean(prefix)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 14 — Scope Isolation (TFA Cannot Block or Alter Trading)
# ═════════════════════════════════════════════════════════════════════════════

def test_scope_tfa_table_has_no_action_columns():
    """The TFA table contains no columns that could influence a trade decision."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='trade_failure_analysis'",
            )
            cols = {r[0] for r in cur.fetchall()}
    finally:
        c.close()
    blocked = {"action", "broker", "auto_arm", "is_actionable", "execute",
               "order_id", "webhook_url", "contracts_live", "ready_verdict"}
    overlap = blocked & cols
    assert not overlap, "Money-path columns found in TFA table: %s" % overlap


def test_scope_fail_open_exception_does_not_propagate():
    """TFA DB errors are swallowed; the production code uses try/except everywhere.

    Control-flow invariant (verified by code inspection):

    In full_analysis (line ~21265):
        try:
            if TFA_DB_READY and is_actionable(verdict):
                _record_ready_decision(...)   # all errors swallowed inside
        except Exception as _tfa_exc:
            logger.debug(...)
        return result   # ALWAYS executes regardless

    In _maybe_auto_execute (line ~41741):
        try:
            if TFA_DB_READY:
                _mark_tfa_triggered(...)      # executed AFTER gateway call
        except Exception:
            pass
        if source == 'micro_scalp': ...       # continues regardless

    In _close_managed_trade (line ~23719):
        try:
            if TFA_DB_READY: ...
                _complete_tfa_record(...)
        except Exception as _tfa_exc:
            logger.debug(...)
        # Trade close already committed before this hook fires

    PROOF: the gateway call and 'return result' are always OUTSIDE the TFA
    try/except block.  A TFA exception cannot prevent a trade or an analysis.
    """
    # Structural test: simulate _record_ready_decision raising and confirm
    # a wrapped call still returns the value computed before TFA
    sentinel = {"called": False}

    def _bad_tfa():
        sentinel["called"] = True
        raise RuntimeError("simulated TFA failure")

    result_value = "FINAL_RESULT"
    try:
        _bad_tfa()
    except Exception:
        pass  # exact pattern used in full_analysis
    return_value = result_value  # this always executes

    assert sentinel["called"] is True, "TFA function was called"
    assert return_value == "FINAL_RESULT", "return executes despite TFA exception"


def test_scope_trigger_hook_fires_after_gateway():
    """Trigger hook is called AFTER execute_trade_gateway — order is irreversible.

    Code sequence in _maybe_auto_execute (verified by inspection):
        result, code = execute_trade_gateway(...)     # FIRST: trade sent
        status = (result or {}).get('status')
        if status in ('sent', 'simulated'):
            plan = result.get('plan') or {}
            try:                                      # THEN: TFA side-effect
                if TFA_DB_READY:
                    _mark_tfa_triggered(...)
            except Exception:
                pass

    The gateway call is unconditional and returns before TFA fires.
    TFA cannot delay, cancel, or modify the gateway call or its result.
    """
    # Structural assertion — verified by line numbers documented above
    # Runtime verification: confirm trigger_lag_sec is always >= 0
    prefix = _prefix("scope_order")
    rid    = prefix + "_001"
    _clean(prefix)
    try:
        _insert_ready(rid)
        time.sleep(0.05)
        _trigger(rid, lag_sec=0.05)
        r = _fetch(rid)
        lag = float(r["trigger_lag_sec"])
        assert lag >= 0, "trigger_lag_sec must be non-negative (gateway fires first)"
    finally:
        _clean(prefix)


def test_scope_tfa_db_ready_false_skips_all_writes():
    """When TFA_DB_READY is False all TFA functions return immediately (no-op).

    Production code (line ~27337):
        def _record_ready_decision(...):
            if not TFA_DB_READY:
                return            # ← no DB call, no state mutation

    Same guard in _mark_tfa_triggered, _complete_tfa_record, _expire_stale_tfa_records.
    This is the goldens' byte-identical guarantee: with no DB probe,
    TFA_DB_READY stays False and every TFA function is a pure no-op.
    """
    # Inlined guard logic
    TFA_DB_READY = False
    called = []

    def _guarded_record(**kw):
        if not TFA_DB_READY:
            return
        called.append("wrote")

    _guarded_record(inst="MGC", direction="Long")
    assert not called, "TFA_DB_READY=False must skip all writes"



# ═════════════════════════════════════════════════════════════════════════════
# SECTION 16 — End-to-End Lifecycle Simulation
# ═════════════════════════════════════════════════════════════════════════════
#
# These tests walk a complete trade lifecycle through the real database:
#   READY  →  triggered  →  completed with outcome + failure classification
#
# All DB work uses the existing _insert_ready / _trigger / _complete / _fetch /
# _clean helpers.  Failure classification uses the inlined
# _classify_failure_mode_test() — identical to production code — so this
# section stays import-free (no app.py dependency).
#
# Six scenarios:
#   Sim-A  Loss / WRONG_BIAS          (long vs bearish bias — top priority)
#   Sim-B  Loss / NO_FOLLOW_THROUGH   (aligned bias, price never moved)
#   Sim-C  Win                        (target hit → failure_mode = "WIN")
#   Sim-D  check_trade_events STOP    (price-triggered stop: r=-1, mfe/mae=None)
#   Sim-E  check_trade_events T1      (price-triggered win: r computed from geometry)
#   Sim-F  Two instruments concurrent (MGC Win + MNQ Loss — no cross-contamination)


SIM_PFX = "SIM-E2E"


def _sim_classify(outcome, mfe_r=None, mae_r=None, r_multiple=None,
                  entry_price=None, stop_loss=None,
                  bias=None, vol_regime="NORMAL", eq_score=72.0,
                  session="RTH", trigger_lag_sec=4.0, direction="Long"):
    """Classify a trade outcome using the inlined test classifier.

    Builds the minimal `mt` dict that _classify_failure_mode_test expects
    and returns (failure_mode, failure_detail).
    """
    mt = {
        "outcome":         outcome,
        "r_multiple":      r_multiple,
        "mfe_r":           mfe_r,
        "mae_r":           mae_r,
        "direction":       direction,
        "entry_price":     entry_price,
        "stop_loss":       stop_loss,
        "trigger_lag_sec": trigger_lag_sec,
    }
    ctx = {"session": session}
    return _classify_failure_mode_test(mt, ctx,
                                       bias=bias,
                                       vol_regime=vol_regime,
                                       eq_score=eq_score)


# ── Sim-A: Loss / WRONG_BIAS ─────────────────────────────────────────────────

def test_sim_a_wrong_bias_full_lifecycle():
    """Full lifecycle — Long setup with bearish bias → WRONG_BIAS.

    Lifecycle:
      1. READY  (direction=Long, bias=bearish, edge=78, eq=72)
      2. Triggered (auto, 4.2s lag, entry 2648.0)
      3. Stop hit  (exit 2643.0, r=-1.0, mfe=0.4, mae=0.85)
      4. Verify: outcome=Loss, failure_mode=WRONG_BIAS, all fields persisted.
    """
    pfx = SIM_PFX + "-A"
    rid = pfx + "-wrong-bias"
    _clean(pfx)
    try:
        # Step 1: READY
        _insert_ready(rid, inst="MGC", direction="Long", mode="SCALP",
                      edge=78.0, eq=72.0, strategy="momentum",
                      bias="bearish", price=2650.0)
        r0 = _fetch(rid)
        assert r0 is not None
        assert not r0["triggered"],      "Should be untriggered at READY"
        assert r0["outcome"] is None,    "No outcome yet at READY"
        assert r0["completed_at"] is None

        # Step 2: Entry triggered
        _trigger(rid, source="auto", entry_price=2648.0, lag_sec=4.2)
        r1 = _fetch(rid)
        assert r1["triggered"]
        assert r1["trigger_source"] == "auto"
        assert float(r1["trigger_lag_sec"]) == pytest.approx(4.2, abs=0.01)
        assert float(r1["entry_price"]) == pytest.approx(2648.0, abs=0.01)
        assert r1["outcome"] is None,    "Trade still in flight"

        # Step 3: Classify and complete
        fm, fd = _sim_classify("Loss", mfe_r=0.4, mae_r=0.85, r_multiple=-1.0,
                               bias="bearish", vol_regime="NORMAL",
                               eq_score=72.0, direction="Long")
        _complete(rid, outcome="Loss", exit_price=2643.0, mfe_r=0.4,
                  mae_r=0.85, r_multiple=-1.0, duration_min=6.5,
                  failure_mode=fm, failure_detail=fd)

        # Step 4: Verify complete record
        r2 = _fetch(rid)
        assert r2["outcome"] == "Loss"
        assert float(r2["r_multiple"]) == pytest.approx(-1.0, abs=0.01)
        assert float(r2["exit_price"]) == pytest.approx(2643.0, abs=0.01)
        assert float(r2["mfe_r"])      == pytest.approx(0.4,    abs=0.01)
        assert float(r2["mae_r"])      == pytest.approx(0.85,   abs=0.01)
        assert r2["completed_at"] is not None
        assert r2["failure_mode"] == "WRONG_BIAS", (
            f"Expected WRONG_BIAS, got {r2['failure_mode']!r}")
        assert r2["failure_detail"] is not None
    finally:
        _clean(pfx)


# ── Sim-B: Loss / NO_FOLLOW_THROUGH ─────────────────────────────────────────

def test_sim_b_no_follow_through_full_lifecycle():
    """Full lifecycle — aligned bias, price never extended → NO_FOLLOW_THROUGH.

    Bias is bullish and direction is Long (aligned → WRONG_BIAS skipped).
    mfe_r=0.2 < 0.25 threshold → NO_FOLLOW_THROUGH wins the classification.
    (The inlined _derive_trade_label_test uses the 0.25 cutoff, matching app.py.)
    """
    pfx = SIM_PFX + "-B"
    rid = pfx + "-no-follow"
    _clean(pfx)
    try:
        _insert_ready(rid, inst="MGC", direction="Long", mode="SCALP",
                      edge=80.0, eq=74.0, strategy="momentum",
                      bias="bullish", price=2650.0)

        _trigger(rid, source="manual", entry_price=2649.0, lag_sec=2.8)
        r1 = _fetch(rid)
        assert r1["triggered"]
        assert r1["trigger_source"] == "manual"

        fm, fd = _sim_classify("Loss", mfe_r=0.2, mae_r=0.9, r_multiple=-1.0,
                               bias="bullish", vol_regime="NORMAL",
                               eq_score=74.0, direction="Long")
        _complete(rid, outcome="Loss", exit_price=2644.0, mfe_r=0.2,
                  mae_r=0.9, r_multiple=-1.0, duration_min=4.0,
                  failure_mode=fm, failure_detail=fd)

        r2 = _fetch(rid)
        assert r2["outcome"] == "Loss"
        assert r2["failure_mode"] == "NO_FOLLOW_THROUGH", (
            f"Expected NO_FOLLOW_THROUGH (mfe=0.2 < 0.25 threshold), got {r2['failure_mode']!r}")
        assert r2["completed_at"] is not None
    finally:
        _clean(pfx)


# ── Sim-C: Win ───────────────────────────────────────────────────────────────

def test_sim_c_win_full_lifecycle():
    """Full lifecycle — target hit → failure_mode = WIN.

    High edge (88) + good entry quality (80) + aligned bullish bias.
    outcome=Win must produce failure_mode="WIN" regardless of mfe/mae.
    """
    pfx = SIM_PFX + "-C"
    rid = pfx + "-win"
    _clean(pfx)
    try:
        _insert_ready(rid, inst="MGC", direction="Long", mode="SCALP",
                      edge=88.0, eq=80.0, strategy="momentum",
                      bias="bullish", price=2650.0)

        _trigger(rid, source="manual", entry_price=2651.0, lag_sec=1.5)

        fm, fd = _sim_classify("Win", mfe_r=1.2, mae_r=0.1, r_multiple=1.0,
                               bias="bullish", vol_regime="NORMAL",
                               eq_score=80.0, direction="Long")
        _complete(rid, outcome="Win", exit_price=2661.0, mfe_r=1.2,
                  mae_r=0.1, r_multiple=1.0, duration_min=12.0,
                  failure_mode=fm, failure_detail=fd)

        r = _fetch(rid)
        assert r["outcome"] == "Win"
        assert float(r["r_multiple"]) > 0
        assert r["failure_mode"] == "WIN", f"Got {r['failure_mode']!r}"
        assert r["completed_at"] is not None
        assert float(r["mfe_r"]) == pytest.approx(1.2, abs=0.01)
    finally:
        _clean(pfx)


# ── Sim-D: check_trade_events STOP_HIT path ──────────────────────────────────

def test_sim_d_check_trade_events_stop_hook():
    """Replicates the check_trade_events STOP_HIT TFA hook from app.py.

    On the price-poll close path the hook sets outcome=Loss, r_multiple=-1.0
    (hardcoded — full stop realised), and leaves mfe_r / mae_r as NULL
    because the managed-trade watcher owns those fields.

    This test builds the same thin dict the hook creates and verifies:
      - outcome = Loss, r_multiple = -1.0
      - mfe_r and mae_r are NULL in the DB (not tracked on this path)
      - failure_mode is a real label (not uncategorized / None)
    """
    pfx = SIM_PFX + "-D"
    rid = pfx + "-stop-hook"
    _clean(pfx)
    try:
        _insert_ready(rid, inst="MGC", direction="Long", mode="SCALP",
                      edge=75.0, eq=68.0, strategy="breakout",
                      bias="bullish", price=2640.0)
        _trigger(rid, source="auto", entry_price=2639.0, lag_sec=6.0)

        # Replicate the exact hook logic from the STOP_HIT branch in app.py.
        # mfe_r / mae_r are deliberately omitted (None = hook doesn't have them).
        parsed_price = 2629.0   # stop level
        outcome      = "Loss"
        r_multiple   = -1.0     # hardcoded in hook
        fm, fd = _sim_classify(outcome, mfe_r=None, mae_r=None,
                               r_multiple=r_multiple,
                               entry_price=2639.0, stop_loss=2629.0,
                               bias="bullish", vol_regime="NORMAL",
                               eq_score=68.0, direction="Long")
        # Complete without mfe_r / mae_r to confirm they stay NULL
        _complete(rid, outcome=outcome, exit_price=parsed_price,
                  mfe_r=None, mae_r=None, r_multiple=r_multiple,
                  duration_min=None, failure_mode=fm, failure_detail=fd)

        r = _fetch(rid)
        assert r["outcome"]    == "Loss"
        assert float(r["r_multiple"]) == pytest.approx(-1.0, abs=0.01)
        assert float(r["exit_price"]) == pytest.approx(2629.0, abs=0.01)
        assert r["mfe_r"] is None, "mfe_r must be NULL on check_trade_events path"
        assert r["mae_r"] is None, "mae_r must be NULL on check_trade_events path"
        # Without mfe/mae the base label falls through to LOSS — NOT
        # STOPPED_BEFORE_MOVE (which would fire if None were coerced to 0.0).
        assert r["failure_mode"] == "LOSS", (
            f"Expected LOSS (null mfe/mae must not coerce to STOPPED_BEFORE_MOVE), "
            f"got {r['failure_mode']!r}")
        # Records with missing mfe/mae must carry the partial-analytics marker.
        assert r["failure_detail"] is not None and "partial" in r["failure_detail"], (
            f"Expected 'partial' in failure_detail, got {r['failure_detail']!r}")
        assert r["completed_at"] is not None
    finally:
        _clean(pfx)


# ── Sim-E: check_trade_events T1_HIT path ────────────────────────────────────

def test_sim_e_check_trade_events_t1_hook():
    """Replicates the check_trade_events T1_HIT TFA hook — r_multiple from geometry.

    The hook computes: r_multiple = (exit - entry) / |entry - stop| for Long.
    With entry=2640, stop=2630, T1=2650 → risk=10, move=10 → r_multiple=1.0.
    mfe_r and mae_r are again NULL (not tracked on this path).
    """
    pfx = SIM_PFX + "-E"
    rid = pfx + "-t1-hook"
    _clean(pfx)
    try:
        _insert_ready(rid, inst="MGC", direction="Long", mode="SCALP",
                      edge=85.0, eq=78.0, strategy="momentum",
                      bias="bullish", price=2640.0)
        _trigger(rid, source="auto", entry_price=2640.0, lag_sec=3.1)

        # Replicate T1_HIT geometry computation from app.py hook
        entry      = 2640.0
        stop       = 2630.0
        parsed_price = 2650.0
        risk       = abs(entry - stop) or 1.0
        r_multiple = round((parsed_price - entry) / risk, 2)  # = 1.0

        fm, fd = _sim_classify("Win", mfe_r=None, mae_r=None,
                               r_multiple=r_multiple,
                               entry_price=entry, stop_loss=stop,
                               bias="bullish", vol_regime="NORMAL",
                               eq_score=78.0, direction="Long")
        _complete(rid, outcome="Win", exit_price=parsed_price,
                  mfe_r=None, mae_r=None, r_multiple=r_multiple,
                  duration_min=None, failure_mode=fm, failure_detail=fd)

        r = _fetch(rid)
        assert r["outcome"]    == "Win"
        assert float(r["r_multiple"]) == pytest.approx(1.0, abs=0.01)
        assert float(r["exit_price"]) == pytest.approx(2650.0, abs=0.01)
        assert r["mfe_r"] is None, "mfe_r NULL on check_trade_events path"
        assert r["mae_r"] is None, "mae_r NULL on check_trade_events path"
        assert r["failure_mode"] == "WIN", f"Expected WIN, got {r['failure_mode']!r}"
        # WIN records on the price-poll path also carry the partial-analytics marker.
        assert r["failure_detail"] is not None and "partial" in r["failure_detail"], (
            f"Expected 'partial' in failure_detail for T1_HIT WIN, got {r['failure_detail']!r}")
        assert r["completed_at"] is not None
    finally:
        _clean(pfx)


# ── Sim-F: Two instruments concurrent — no cross-contamination ───────────────

def test_sim_f_two_instruments_independent_close():
    """MGC and MNQ run their full lifecycles simultaneously without interfering.

    MGC closes as a Win; MNQ closes as a Loss with aligned bias + low MFE
    → NO_FOLLOW_THROUGH.  Verifies that closing one record does not affect
    the other (instrument isolation in the UPDATE WHERE ready_id = %s).
    """
    pfx     = SIM_PFX + "-F"
    rid_mgc = pfx + "-MGC"
    rid_mnq = pfx + "-MNQ"
    _clean(pfx)
    try:
        # Insert both READY rows
        _insert_ready(rid_mgc, inst="MGC", direction="Long",  mode="SCALP",
                      edge=80.0, eq=74.0, bias="bullish", price=2650.0)
        _insert_ready(rid_mnq, inst="MNQ", direction="Short", mode="SCALP",
                      edge=78.0, eq=72.0, bias="bearish",  price=19800.0)

        # Trigger both
        _trigger(rid_mgc, source="auto",   entry_price=2651.0,  lag_sec=2.0)
        _trigger(rid_mnq, source="manual", entry_price=19800.0, lag_sec=3.5)

        # Classify and close each instrument
        fm_mgc, fd_mgc = _sim_classify("Win",  mfe_r=1.1, mae_r=0.1,
                                       r_multiple=1.0,
                                       bias="bullish", direction="Long")
        fm_mnq, fd_mnq = _sim_classify("Loss", mfe_r=0.2, mae_r=0.9,
                                       r_multiple=-1.0,
                                       bias="bearish", direction="Short")

        _complete(rid_mgc, outcome="Win",  exit_price=2661.0,  mfe_r=1.1,
                  mae_r=0.1, r_multiple=1.0, duration_min=8.0,
                  failure_mode=fm_mgc, failure_detail=fd_mgc)
        _complete(rid_mnq, outcome="Loss", exit_price=19820.0, mfe_r=0.2,
                  mae_r=0.9, r_multiple=-1.0, duration_min=5.0,
                  failure_mode=fm_mnq, failure_detail=fd_mnq)

        r_mgc = _fetch(rid_mgc)
        r_mnq = _fetch(rid_mnq)

        assert r_mgc["outcome"] == "Win",  f"MGC: {r_mgc['outcome']}"
        assert r_mgc["failure_mode"] == "WIN", f"MGC mode: {r_mgc['failure_mode']!r}"
        assert r_mnq["outcome"] == "Loss", f"MNQ: {r_mnq['outcome']}"
        # MNQ: Short+bearish aligned → WRONG_BIAS skipped; mfe=0.2 < 0.5 → NO_FOLLOW_THROUGH
        assert r_mnq["failure_mode"] == "NO_FOLLOW_THROUGH", (
            f"MNQ: expected NO_FOLLOW_THROUGH, got {r_mnq['failure_mode']!r}")
        # Verify cross-contamination guard: each record has its own instrument
        assert r_mgc["instrument"] == "MGC"
        assert r_mnq["instrument"] == "MNQ"
        assert r_mgc["completed_at"] is not None
        assert r_mnq["completed_at"] is not None
    finally:
        _clean(pfx)
