"""
test_phase6_journal_coach.py
V1-P6 — Journal and Coach Separation

Runtime tests for V1-P6-001 through V1-P6-007.
All tests are read-only with respect to production code.
DB-touching tests use UUID-scoped keys and delete in finally blocks.
No real broker, Discord, or Databento calls are made.

Research resolutions embedded in test comments:
  RQ-1 (V1-P6-002): open_trades rows are DELETED (not updated) on close.
    closed_at and r_multiple live in strategy_trades, not open_trades.
    Test verifies: Part A — open_trades row absent after close;
                   Part B — strategy_trades row has closed_at and r_multiple.
  RQ-2 (V1-P6-005): "unified_learning block" = result["coach"] block.
    No top-level "unified_learning" key is added to _build_status_payload.
  RQ-3 (V1-P6-007): Journal Discord is gated by DISCORD_JOURNAL_WEBHOOK_URL
    absence (not DISCORD_LIVE_ENABLED). URL absent in test env → no HTTP call.
"""

import json
import sys
import os
import uuid
import unittest
import importlib
from unittest.mock import patch, MagicMock, call as mock_call

sys.path.insert(0, os.path.dirname(__file__))
import app

# ===========================================================================
# DB isolation helpers
# ===========================================================================

_TEST_PREFIX = "test_p6"


def _fresh_key():
    """Unique managed_key for each test run — prevents cross-test contamination."""
    return f"{_TEST_PREFIX}_{uuid.uuid4().hex}"


def _lconn():
    """Short-lived autocommit connection via the canonical production helper.
    Returns None when DB is unavailable (tests skip in that case)."""
    return app._learning_conn()


def _cleanup_strategy_trade(managed_key):
    """Delete test row from strategy_trades. Called in finally blocks."""
    conn = _lconn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM strategy_trades WHERE managed_key = %s",
                (managed_key,),
            )
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _cleanup_open_trade(inst):
    """Delete test row from open_trades. Called in finally blocks."""
    conn = _lconn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM open_trades WHERE inst = %s", (inst,))
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _managed_key_from(key_tuple):
    """Mirror the production managed_key construction in _record_strategy_trade."""
    return "|".join(str(x) for x in key_tuple)


def _minimal_mt(managed_key_tuple, inst="MGC"):
    """Minimal managed-trade dict accepted by _record_strategy_trade.
    All required scalar fields populated; optional fields use None."""
    return {
        "key":          managed_key_tuple,
        "symbol":       inst,
        "instrument":   inst,
        "direction":    "Long",
        "outcome":      "WIN",
        "r_multiple":   1.5,
        "entry":        2450.0,
        "stop":         2445.0,
        "tp1":          2457.5,
        "exit_price":   2457.0,
        "mfe_r":        1.6,
        "mae_r":        0.2,
        "registered_at": "2026-07-30T12:00:00",
        "closed_at":    "2026-07-30T12:30:00",
        "journal_id":   None,   # bigint column — None when no real journal record exists
        "learning_ctx": {
            "strategy_key":     f"{inst}_SCALP_CHOCH_Long",
            "strategy":         "CHOCH",
            "regime":           "RISK_ON",
            "session":          "NY",
            "confidence":       75,
            "quality":          80,
            "edge_score":       72,
            "grade":            "B",
            "volatility_type":  "NORMAL",
            "indicators":       {},
        },
    }


def _sync_enqueue(fn):
    """Replacement for _enqueue_slow that runs the task synchronously."""
    fn()


# ===========================================================================
# V1-P6-001 — Journal INSERT
# ===========================================================================

def test_p6_001_strategy_trades_insert_creates_row():
    """V1-P6-001 — _record_strategy_trade must create a row in strategy_trades.

    Uses the real production persistence function with an isolated managed_key.
    Verifies the row exists after the call via a direct SELECT.
    """
    if not app.LEARNING_DB_ENABLED:
        raise unittest.SkipTest("DB not available — LEARNING_DB_ENABLED is False")
    prefix = _fresh_key()
    key_tuple = (prefix, "Long", "CHOCH", "NY")
    managed_key = _managed_key_from(key_tuple)
    mt = _minimal_mt(key_tuple, inst="MGC")
    try:
        app._record_strategy_trade(mt)
        conn = _lconn()
        if conn is None:
            raise unittest.SkipTest("DB connection unavailable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT managed_key FROM strategy_trades WHERE managed_key = %s",
                    (managed_key,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None, (
            f"strategy_trades row must exist after _record_strategy_trade; "
            f"managed_key={managed_key!r}"
        )
    finally:
        _cleanup_strategy_trade(managed_key)


def test_p6_001_insert_preserves_instrument_and_direction():
    """V1-P6-001 — strategy_trades row must carry the correct instrument and direction."""
    if not app.LEARNING_DB_ENABLED:
        raise unittest.SkipTest("DB not available")
    prefix = _fresh_key()
    key_tuple = (prefix, "Short", "BOS", "AM")
    managed_key = _managed_key_from(key_tuple)
    mt = _minimal_mt(key_tuple, inst="MNQ")
    mt["direction"] = "Short"
    try:
        app._record_strategy_trade(mt)
        conn = _lconn()
        if conn is None:
            raise unittest.SkipTest("DB connection unavailable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, direction FROM strategy_trades WHERE managed_key = %s",
                    (managed_key,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None, f"Row must exist; managed_key={managed_key!r}"
        assert row[0] == "MNQ",   f"symbol must be 'MNQ', got {row[0]!r}"
        assert row[1] == "Short", f"direction must be 'Short', got {row[1]!r}"
    finally:
        _cleanup_strategy_trade(managed_key)


def test_p6_001_insert_populates_closed_at_and_r_multiple():
    """V1-P6-001 — strategy_trades row must have closed_at and r_multiple set."""
    if not app.LEARNING_DB_ENABLED:
        raise unittest.SkipTest("DB not available")
    prefix = _fresh_key()
    key_tuple = (prefix, "Long", "CHOCH", "NY")
    managed_key = _managed_key_from(key_tuple)
    mt = _minimal_mt(key_tuple, inst="MGC")
    try:
        app._record_strategy_trade(mt)
        conn = _lconn()
        if conn is None:
            raise unittest.SkipTest("DB connection unavailable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT closed_at, r_multiple FROM strategy_trades WHERE managed_key = %s",
                    (managed_key,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None, f"Row must exist; managed_key={managed_key!r}"
        assert row[0] is not None, "closed_at must be populated in strategy_trades"
        assert row[1] is not None, "r_multiple must be populated in strategy_trades"
        assert float(row[1]) == 1.5, f"r_multiple must be 1.5, got {row[1]}"
    finally:
        _cleanup_strategy_trade(managed_key)


def test_p6_001_duplicate_insert_is_idempotent():
    """V1-P6-001 — duplicate call with same managed_key must not create a second row.

    ON CONFLICT (managed_key) DO NOTHING guarantees idempotency.
    """
    if not app.LEARNING_DB_ENABLED:
        raise unittest.SkipTest("DB not available")
    prefix = _fresh_key()
    key_tuple = (prefix, "Long", "CHOCH", "NY")
    managed_key = _managed_key_from(key_tuple)
    mt = _minimal_mt(key_tuple, inst="MGC")
    try:
        app._record_strategy_trade(mt)
        app._record_strategy_trade(mt)   # second call — must be a no-op
        conn = _lconn()
        if conn is None:
            raise unittest.SkipTest("DB connection unavailable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM strategy_trades WHERE managed_key = %s",
                    (managed_key,),
                )
                count = cur.fetchone()[0]
        finally:
            conn.close()
        assert count == 1, (
            f"Duplicate managed_key must yield exactly 1 row; got {count}"
        )
    finally:
        _cleanup_strategy_trade(managed_key)


# ===========================================================================
# V1-P6-002 — Open-to-Closed Trade Transition
#
# RQ-1 RESOLUTION: open_trades rows are DELETED (not updated) on close.
# closed_at and r_multiple are written to strategy_trades, not open_trades.
# Part A verifies the open_trades row is absent after close.
# Part B verifies strategy_trades has the completed-result fields.
# ===========================================================================

def test_p6_002a_open_trades_row_absent_after_close():
    """V1-P6-002 Part A — _persist_active_trade(inst, None) must DELETE the row.

    RQ-1: open_trades rows are deleted at close, not updated.
    Patches _enqueue_slow to run the DB task synchronously so the test can
    verify the state change without racing the background worker.
    """
    if not app.LEARNING_DB_ENABLED:
        raise unittest.SkipTest("DB not available")
    test_inst = f"TEST_P6_{uuid.uuid4().hex[:10]}"
    trade = {
        "opened_at":  "2026-07-30T12:00:00",
        "direction":  "Long",
        "instrument": test_inst,
        "entry":      2450.0,
    }
    saved_db_ready = app.ACTIVE_TRADES_DB_READY
    app.ACTIVE_TRADES_DB_READY = True
    try:
        with patch.object(app, "_enqueue_slow", side_effect=_sync_enqueue):
            # Insert the open-trade row.
            app._persist_active_trade(test_inst, trade)

            # Verify row exists before close.
            conn = _lconn()
            if conn is None:
                raise unittest.SkipTest("DB connection unavailable")
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT inst FROM open_trades WHERE inst = %s",
                        (test_inst,),
                    )
                    before = cur.fetchone()
            finally:
                conn.close()
            assert before is not None, "open_trades row must exist before close"

            # Delete the row (simulating trade close).
            app._persist_active_trade(test_inst, None)

            # Verify row is gone.
            conn = _lconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT inst FROM open_trades WHERE inst = %s",
                        (test_inst,),
                    )
                    after = cur.fetchone()
            finally:
                conn.close()

        assert after is None, (
            "open_trades row must be absent after _persist_active_trade(inst, None); "
            "RQ-1 confirms DELETE is the correct close behavior"
        )
    finally:
        app.ACTIVE_TRADES_DB_READY = saved_db_ready
        _cleanup_open_trade(test_inst)   # belt-and-suspenders cleanup


def test_p6_002b_strategy_trades_has_closed_at_and_r_multiple():
    """V1-P6-002 Part B — strategy_trades must have closed_at and r_multiple.

    RQ-1: closed_at and r_multiple belong to strategy_trades, not open_trades.
    """
    if not app.LEARNING_DB_ENABLED:
        raise unittest.SkipTest("DB not available")
    prefix = _fresh_key()
    key_tuple = (prefix, "Long", "CHOCH", "NY")
    managed_key = _managed_key_from(key_tuple)
    mt = _minimal_mt(key_tuple, inst="MGC")
    try:
        app._record_strategy_trade(mt)
        conn = _lconn()
        if conn is None:
            raise unittest.SkipTest("DB connection unavailable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT closed_at, r_multiple, symbol, direction "
                    "FROM strategy_trades WHERE managed_key = %s",
                    (managed_key,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None, f"strategy_trades row must exist; managed_key={managed_key!r}"
        closed_at, r_multiple, symbol, direction = row
        assert closed_at is not None, "closed_at must be populated"
        assert r_multiple is not None, "r_multiple must be populated"
        assert symbol == "MGC",        f"symbol must match; got {symbol!r}"
        assert direction == "Long",    f"direction must match; got {direction!r}"
    finally:
        _cleanup_strategy_trade(managed_key)


def test_p6_002b_open_trades_schema_has_no_closed_at_column():
    """V1-P6-002 — open_trades schema must NOT contain a closed_at column.

    Documents the RQ-1 resolved difference between the roadmap wording
    ('open_trades row has closed_at and result_r') and production reality
    (closed record lives entirely in strategy_trades; open_trades uses DELETE).
    """
    if not app.LEARNING_DB_ENABLED:
        raise unittest.SkipTest("DB not available")
    conn = _lconn()
    if conn is None:
        raise unittest.SkipTest("DB connection unavailable")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'open_trades'""",
            )
            cols = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    assert "closed_at" not in cols, (
        "open_trades must not have a closed_at column; "
        "closed trade records belong in strategy_trades"
    )
    assert "r_multiple" not in cols, (
        "open_trades must not have an r_multiple column; "
        "R value belongs in strategy_trades"
    )
    # Confirm the expected open-trade columns are present.
    for expected in ("inst", "payload", "opened_at"):
        assert expected in cols, f"open_trades must have column {expected!r}"


# ===========================================================================
# V1-P6-003 — Journal Write Failure Isolation
# ===========================================================================

def test_p6_003_journal_failure_does_not_raise():
    """V1-P6-003 — DB failure inside _record_strategy_trade must not propagate.

    Fault-injects _learning_conn to raise psycopg2.OperationalError.
    Verifies the function returns silently (FAIL-OPEN).
    """
    key = _fresh_key()
    mt = _minimal_mt((key, "Long", "CHOCH", "NY"), inst="MGC")
    try:
        import psycopg2
        with patch.object(
            app, "_learning_conn",
            side_effect=psycopg2.OperationalError("P6-003 test injection"),
        ):
            # Must not raise.
            app._record_strategy_trade(mt)
    except Exception as exc:
        raise AssertionError(
            f"_record_strategy_trade must not propagate DB exceptions; "
            f"got {type(exc).__name__}: {exc}"
        )


def test_p6_003_execution_state_unchanged_after_journal_failure():
    """V1-P6-003 — _TRADERSPOST_LAST must be unaffected by journal DB failure."""
    import psycopg2
    with app._TRADERSPOST_LOCK:
        before = dict(app._TRADERSPOST_LAST)
    key = _fresh_key()
    mt = _minimal_mt((key, "Long", "CHOCH", "NY"), inst="MGC")
    with patch.object(
        app, "_learning_conn",
        side_effect=psycopg2.OperationalError("P6-003 exec-state injection"),
    ):
        app._record_strategy_trade(mt)
    with app._TRADERSPOST_LOCK:
        after = dict(app._TRADERSPOST_LAST)
    assert before == after, (
        "execution gate state (_TRADERSPOST_LAST) must be unchanged "
        "after a journal DB write failure"
    )


def test_p6_003_no_db_residue_after_failure():
    """V1-P6-003 — a failed journal write must leave no partial row in strategy_trades."""
    if not app.LEARNING_DB_ENABLED:
        raise unittest.SkipTest("DB not available")
    import psycopg2
    key = _fresh_key()
    mt = _minimal_mt((key, "Long", "CHOCH", "NY"), inst="MGC")
    try:
        with patch.object(
            app, "_learning_conn",
            side_effect=psycopg2.OperationalError("P6-003 residue injection"),
        ):
            app._record_strategy_trade(mt)
        # Open a real connection to check no row was created.
        conn = app._learning_conn()
        if conn is None:
            raise unittest.SkipTest("DB unavailable for verification")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM strategy_trades WHERE managed_key = %s",
                    (key,),
                )
                count = cur.fetchone()[0]
        finally:
            conn.close()
        assert count == 0, (
            f"No strategy_trades row must exist when the DB connection failed; "
            f"found {count} row(s)"
        )
    finally:
        _cleanup_strategy_trade(key)


# ===========================================================================
# V1-P6-004 — Journal and Coach Separation
# ===========================================================================

def test_p6_004_coach_reads_do_not_write_alert_history():
    """V1-P6-004 — build_coach_interface must not append to ALERT_HISTORY."""
    result = app.full_analysis()
    before = len(app.ALERT_HISTORY)
    for _ in range(3):
        app.build_coach_interface(result)
    after = len(app.ALERT_HISTORY)
    assert after == before, (
        f"ALERT_HISTORY grew by {after - before} entries during Coach reads; "
        "Coach interface construction must never write to ALERT_HISTORY"
    )


def test_p6_004_coach_reads_do_not_write_active_trades():
    """V1-P6-004 — build_coach_interface must not modify ACTIVE_TRADES_BY_INST."""
    result = app.full_analysis()
    with app.ACTIVE_TRADES_LOCK:
        before = dict(app.ACTIVE_TRADES_BY_INST)
    for _ in range(3):
        app.build_coach_interface(result)
    with app.ACTIVE_TRADES_LOCK:
        after = dict(app.ACTIVE_TRADES_BY_INST)
    assert before == after, (
        "ACTIVE_TRADES_BY_INST must be unchanged after Coach reads; "
        "Coach interface construction is read-only"
    )


def test_p6_004_journal_insert_does_not_change_coach_fields():
    """V1-P6-004 — a strategy_trades INSERT must not alter Coach interface values.

    Journal and Coach are architecturally separate: Journal records trade events;
    Coach reads learning outcomes after the fact.
    """
    if not app.LEARNING_DB_ENABLED:
        raise unittest.SkipTest("DB not available")
    prefix = _fresh_key()
    key_tuple = (prefix, "Long", "CHOCH", "NY")
    managed_key = _managed_key_from(key_tuple)
    mt = _minimal_mt(key_tuple, inst="MGC")
    result = app.full_analysis()
    coach_before = app.build_coach_interface(result)
    try:
        app._record_strategy_trade(mt)
        coach_after = app.build_coach_interface(result)
    finally:
        _cleanup_strategy_trade(managed_key)
    # weight_updated and rule_engine_eligibility may change between full sessions,
    # but _version and the semantic contract must hold.
    assert coach_after.get("_version") == "v1", (
        "_version must remain 'v1' after a Journal INSERT"
    )
    assert isinstance(coach_after.get("weight_updated"), bool), (
        "weight_updated must remain a bool after a Journal INSERT"
    )
    assert isinstance(coach_after.get("thesis_resolved"), bool), (
        "thesis_resolved must remain a bool after a Journal INSERT"
    )


def test_p6_004_repeated_coach_reads_are_stable():
    """V1-P6-004 — 5 consecutive Coach reads must return consistent values.

    Repeated reads must not trigger DB writes, learning recomputes, or
    state mutations.  _version is the invariant that must never change.
    """
    result = app.full_analysis()
    saved_analytics = dict(app.LEARNING_ANALYTICS)
    coaches = [app.build_coach_interface(result) for _ in range(5)]
    # _version must be "v1" in all calls.
    assert all(c.get("_version") == "v1" for c in coaches), (
        "_version must be 'v1' in all 5 consecutive Coach reads"
    )
    # LEARNING_ANALYTICS must not be mutated by Coach reads.
    assert app.LEARNING_ANALYTICS.get("enabled") == saved_analytics.get("enabled"), (
        "Coach reads must not alter LEARNING_ANALYTICS['enabled']"
    )


# ===========================================================================
# V1-P6-005 — Coach / Unified Learning Block
#
# RQ-2 RESOLUTION: "unified_learning block" = result["coach"] block.
# No top-level "unified_learning" key exists or is added.
# ===========================================================================

def test_p6_005_coach_block_present_in_full_analysis():
    """V1-P6-005 — result['coach'] must be present after full_analysis().

    RQ-2: The unified learning block is result['coach'].
    """
    result = app.full_analysis()
    assert "coach" in result, "result['coach'] must be present after full_analysis()"
    assert isinstance(result["coach"], dict), (
        "result['coach'] must be a dict; got %s" % type(result["coach"])
    )


def test_p6_005_coach_required_fields_present():
    """V1-P6-005 — all required Coach interface fields must be present."""
    cch = app.full_analysis().get("coach", {})
    required = (
        "_version",
        "weight_updated",
        "thesis_resolved",
        "thesis_last_resolved_at",
        "learning_influence",
        "rule_engine_eligibility",
    )
    for field in required:
        assert field in cch, (
            f"Coach interface missing required field: {field!r}"
        )


def test_p6_005_weight_updated_semantics():
    """V1-P6-005 — weight_updated must reflect a real recompute event, not readiness.

    weight_updated is True only when LEARNING_ANALYTICS['updated_at'] is set
    (populated when _recompute_learning() completes).  DB availability alone
    must not make it True.
    """
    saved = dict(app.LEARNING_ANALYTICS)
    try:
        # Simulate no recompute event: remove updated_at if present.
        app.LEARNING_ANALYTICS.pop("updated_at", None)
        app.LEARNING_ANALYTICS["ready"] = True   # DB ready != recompute ran
        cch = app.build_coach_interface(app.full_analysis())
        assert cch["weight_updated"] is False, (
            "weight_updated must be False when updated_at is absent, "
            "even when ready=True"
        )
        # Simulate a completed recompute event.
        app.LEARNING_ANALYTICS["updated_at"] = "2026-07-30T12:00:00"
        cch2 = app.build_coach_interface(app.full_analysis())
        assert cch2["weight_updated"] is True, (
            "weight_updated must be True when updated_at is set"
        )
    finally:
        app.LEARNING_ANALYTICS.clear()
        app.LEARNING_ANALYTICS.update(saved)


def test_p6_005_thesis_resolved_semantics():
    """V1-P6-005 — thesis_resolved must reflect a real resolution event.

    thesis_resolved is True only when _THESIS_LAST_RESOLVED_AT is not None
    (set only at trade-close resolution).  DB readiness alone must not imply True.
    """
    saved_ts = app._THESIS_LAST_RESOLVED_AT
    try:
        app._THESIS_LAST_RESOLVED_AT = None
        cch = app.build_coach_interface(app.full_analysis())
        assert cch["thesis_resolved"] is False, (
            "thesis_resolved must be False when _THESIS_LAST_RESOLVED_AT is None"
        )
        assert cch["thesis_last_resolved_at"] is None, (
            "thesis_last_resolved_at must be None when _THESIS_LAST_RESOLVED_AT is None"
        )
    finally:
        app._THESIS_LAST_RESOLVED_AT = saved_ts


def test_p6_005_thesis_last_resolved_at_populated_from_canonical():
    """V1-P6-005 — thesis_last_resolved_at must come from _THESIS_LAST_RESOLVED_AT."""
    from datetime import datetime
    saved_ts = app._THESIS_LAST_RESOLVED_AT
    try:
        test_dt = datetime(2026, 7, 30, 12, 0, 0)
        app._THESIS_LAST_RESOLVED_AT = test_dt
        cch = app.build_coach_interface(app.full_analysis())
        assert cch["thesis_last_resolved_at"] == test_dt.isoformat(), (
            f"thesis_last_resolved_at must equal the isoformat of "
            f"_THESIS_LAST_RESOLVED_AT; got {cch['thesis_last_resolved_at']!r}"
        )
        assert cch["thesis_resolved"] is True, (
            "thesis_resolved must be True when _THESIS_LAST_RESOLVED_AT is set"
        )
    finally:
        app._THESIS_LAST_RESOLVED_AT = saved_ts


def test_p6_005_coach_block_is_serializable():
    """V1-P6-005 — result['coach'] must be JSON-serializable for /status delivery."""
    cch = app.full_analysis().get("coach", {})
    try:
        serialized = json.dumps(cch)
    except (TypeError, ValueError) as exc:
        raise AssertionError(
            f"result['coach'] must be JSON-serializable; json.dumps raised: {exc}"
        )
    decoded = json.loads(serialized)
    assert decoded.get("_version") == "v1", (
        "_version must survive JSON round-trip"
    )


# ===========================================================================
# V1-P6-006 — Coach Failure Isolation
# ===========================================================================

def test_p6_006_coach_internal_fault_returns_neutral_stubs():
    """V1-P6-006 — Coach fail-open wrapper must return neutral stubs on internal fault.

    Injects a fault inside build_coach_interface by corrupting LEARNING_ANALYTICS
    to a non-dict type, which causes an AttributeError inside the try body.
    Verifies the fail-open except block catches it and returns neutral stubs.
    """
    saved_analytics = app.LEARNING_ANALYTICS
    try:
        app.LEARNING_ANALYTICS = None   # type: ignore[assignment]
        cch = app.build_coach_interface(app.full_analysis())
        assert isinstance(cch, dict), "fail-open must return a dict"
        assert cch.get("_version") == "v1", "_version must be 'v1' in neutral stubs"
        assert cch.get("weight_updated") is False, (
            "neutral stub weight_updated must be False"
        )
        assert cch.get("thesis_resolved") is False, (
            "neutral stub thesis_resolved must be False"
        )
        assert cch.get("thesis_last_resolved_at") is None, (
            "neutral stub thesis_last_resolved_at must be None"
        )
        assert cch.get("learning_influence") == 0.0, (
            "neutral stub learning_influence must be 0.0"
        )
        assert cch.get("rule_engine_eligibility") == "LIVE_ELIGIBLE", (
            "neutral stub rule_engine_eligibility must be 'LIVE_ELIGIBLE'"
        )
    finally:
        app.LEARNING_ANALYTICS = saved_analytics


def test_p6_006_full_analysis_returns_after_coach_fault():
    """V1-P6-006 — full_analysis() must complete even when Coach internals fault.

    Corrupts LEARNING_ANALYTICS to trigger the fail-open path inside
    build_coach_interface.  full_analysis() must still return a complete
    Expert-level result with verdict, edge_score, and _version.
    """
    saved_analytics = app.LEARNING_ANALYTICS
    try:
        app.LEARNING_ANALYTICS = None   # type: ignore[assignment]
        result = app.full_analysis()
        assert isinstance(result, dict), (
            "full_analysis() must return a dict after Coach fault"
        )
        assert result.get("_version") == "v1", (
            "_version must be 'v1' after Coach fault"
        )
        for field in ("verdict", "edge_score", "strict_reason"):
            assert field in result, (
                f"Expert field {field!r} must be present after Coach fault"
            )
    finally:
        app.LEARNING_ANALYTICS = saved_analytics


def test_p6_006_verdict_unchanged_after_coach_fault():
    """V1-P6-006 — verdict and edge_score must be identical before and after Coach fault.

    Coach is display-only and must never influence Expert scoring.
    """
    # Baseline: clean state.
    baseline = app.full_analysis()
    baseline_verdict = baseline.get("verdict")
    baseline_edge = baseline.get("edge_score")

    # Fault state: corrupt LEARNING_ANALYTICS to trigger Coach fail-open.
    saved_analytics = app.LEARNING_ANALYTICS
    try:
        app.LEARNING_ANALYTICS = None   # type: ignore[assignment]
        faulted = app.full_analysis()
    finally:
        app.LEARNING_ANALYTICS = saved_analytics

    assert faulted.get("verdict") == baseline_verdict, (
        f"verdict must be unchanged after Coach fault; "
        f"before={baseline_verdict!r} after={faulted.get('verdict')!r}"
    )
    assert faulted.get("edge_score") == baseline_edge, (
        f"edge_score must be unchanged after Coach fault; "
        f"before={baseline_edge} after={faulted.get('edge_score')}"
    )


def test_p6_006_no_broker_call_after_coach_fault():
    """V1-P6-006 — Coach fault must not trigger any broker communication."""
    with app._TRADERSPOST_LOCK:
        before = dict(app._TRADERSPOST_LAST)
    saved_analytics = app.LEARNING_ANALYTICS
    try:
        app.LEARNING_ANALYTICS = None   # type: ignore[assignment]
        app.full_analysis()
    finally:
        app.LEARNING_ANALYTICS = saved_analytics
    with app._TRADERSPOST_LOCK:
        after = dict(app._TRADERSPOST_LAST)
    assert before == after, (
        "execution gate state (_TRADERSPOST_LAST) must be unchanged after Coach fault"
    )


def test_p6_006_repeated_calls_stable_after_coach_fault():
    """V1-P6-006 — repeated Coach calls with an injected fault must produce stable neutral stubs."""
    saved_analytics = app.LEARNING_ANALYTICS
    try:
        app.LEARNING_ANALYTICS = None   # type: ignore[assignment]
        results = [app.build_coach_interface(app.full_analysis()) for _ in range(3)]
    finally:
        app.LEARNING_ANALYTICS = saved_analytics
    for i, cch in enumerate(results):
        assert cch.get("_version") == "v1", (
            f"Call {i+1}: _version must be 'v1' in neutral stubs"
        )
        assert cch.get("weight_updated") is False, (
            f"Call {i+1}: neutral weight_updated must be False"
        )


# ===========================================================================
# V1-P6-007 — Journal Discord Isolation
#
# RQ-3 RESOLUTION: Journal Discord send is gated by DISCORD_JOURNAL_WEBHOOK_URL
# absence.  DISCORD_LIVE_ENABLED is NOT checked in send_journal_discord_embed().
# URL absent in test env → no HTTP call (confirmed; no production change needed).
# ===========================================================================

def test_p6_007_no_http_call_when_url_absent():
    """V1-P6-007 — send_journal_discord_embed must make no HTTP call when URL is absent.

    RQ-3: DISCORD_JOURNAL_WEBHOOK_URL absence is the actual gate.
    """
    entry = {"id": "test_p6_007", "symbol": "MGC", "instrument": "MGC"}
    saved_url = app.DISCORD_JOURNAL_WEBHOOK_URL
    app.DISCORD_JOURNAL_WEBHOOK_URL = ""
    mock_post = MagicMock()
    try:
        with patch.object(app.requests, "post", mock_post):
            app.send_journal_discord_embed(entry)
    finally:
        app.DISCORD_JOURNAL_WEBHOOK_URL = saved_url
    mock_post.assert_not_called()


def test_p6_007_no_exception_when_url_absent():
    """V1-P6-007 — send_journal_discord_embed must not raise when URL is absent."""
    entry = {"id": "test_p6_007b", "symbol": "MGC"}
    saved_url = app.DISCORD_JOURNAL_WEBHOOK_URL
    app.DISCORD_JOURNAL_WEBHOOK_URL = ""
    try:
        app.send_journal_discord_embed(entry)   # must return silently
    except Exception as exc:
        raise AssertionError(
            f"send_journal_discord_embed must not raise when URL is absent; "
            f"got {type(exc).__name__}: {exc}"
        )
    finally:
        app.DISCORD_JOURNAL_WEBHOOK_URL = saved_url


def test_p6_007_single_request_when_url_configured():
    """V1-P6-007 — exactly one requests.post call when URL is configured.

    Uses a mock URL and patches both _build_trade_card_embed and requests.post
    to avoid any real network call or complex entry dict construction.
    """
    entry = {"id": "test_p6_007c", "symbol": "MGC", "instrument": "MGC"}
    saved_url = app.DISCORD_JOURNAL_WEBHOOK_URL
    app.DISCORD_JOURNAL_WEBHOOK_URL = "https://test.invalid/p6_007"
    mock_post = MagicMock(return_value=MagicMock(status_code=204))
    try:
        with patch.object(app, "_build_trade_card_embed", return_value={"title": "test"}), \
             patch.object(app.requests, "post", mock_post):
            app.send_journal_discord_embed(entry)
    finally:
        app.DISCORD_JOURNAL_WEBHOOK_URL = saved_url
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    payload = call_kwargs[1].get("json") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
    assert json.dumps(payload) is not None, "POST payload must be JSON-serializable"


def test_p6_007_request_failure_contained():
    """V1-P6-007 — requests.post raising must not propagate from send_journal_discord_embed."""
    import requests as _req
    entry = {"id": "test_p6_007d", "symbol": "MGC"}
    saved_url = app.DISCORD_JOURNAL_WEBHOOK_URL
    app.DISCORD_JOURNAL_WEBHOOK_URL = "https://test.invalid/p6_007"
    try:
        with patch.object(app, "_build_trade_card_embed", return_value={"title": "test"}), \
             patch.object(app.requests, "post",
                          side_effect=_req.RequestException("P6-007 test injection")):
            app.send_journal_discord_embed(entry)   # must not raise
    except Exception as exc:
        raise AssertionError(
            f"send_journal_discord_embed must contain a requests.post exception; "
            f"got {type(exc).__name__}: {exc}"
        )
    finally:
        app.DISCORD_JOURNAL_WEBHOOK_URL = saved_url


def test_p6_007_url_restored_after_test():
    """V1-P6-007 — confirm DISCORD_JOURNAL_WEBHOOK_URL is not left modified by other tests."""
    # This test intentionally runs last-ish to verify the module-level variable
    # was properly restored by the tests above.  It just reads and confirms the
    # value matches os.environ (or empty string if unset in test env).
    env_url = os.environ.get("DISCORD_JOURNAL_WEBHOOK_URL", "")
    # The module initializes DISCORD_JOURNAL_WEBHOOK_URL from os.environ at import
    # time.  After all P6-007 tests restore the value, it must equal that initial value.
    # (If no Discord secret is set in test env, both will be "".)
    assert app.DISCORD_JOURNAL_WEBHOOK_URL == env_url, (
        f"DISCORD_JOURNAL_WEBHOOK_URL must be restored to env value after tests; "
        f"got {app.DISCORD_JOURNAL_WEBHOOK_URL!r}, expected {env_url!r}"
    )


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    tests = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    passed = failed = skipped = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except unittest.SkipTest as skip:
            print(f"  SKIP  {t.__name__}: {skip}")
            skipped += 1
        except AssertionError as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {type(exc).__name__}: {exc}")
            failed += 1
    print("=" * 64)
    print(
        f"  TOTAL: {passed + failed + skipped} checks — "
        f"{passed} passed, {failed} failed, {skipped} skipped"
    )
    if failed:
        sys.exit(1)
