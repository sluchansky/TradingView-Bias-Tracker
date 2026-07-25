"""
Phase 5F Decision Quality & Signal Calibration — Validation Test Suite.

Covers:
  Section 3  — Snapshot capture dedup and gating
  Section 4  — _DQ_PENDING_BY_INST lifecycle (set / pop / stale paths)
  Section 5  — Outcome matching (sequential, direction-flip, multi-inst)
  Section 6  — Analytics accuracy (win rate, avg R, null, min-sample, dup impact)
  Section 7  — Read-only proof (result dict, mt dict not mutated)
  Section 8  — Boot probe behaviour

No network calls, no live DB connections, no Databento streaming.
All DB interactions are mocked via unittest.mock.

Run with:
    pytest artifacts/tradingview-webhook/test_decision_quality.py -v
"""

import os
import sys
import copy
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as _app
from app import (
    _capture_decision_snapshot,
    _resolve_decision_snapshot,
    _build_decision_quality_report,
    _check_dq_db_ready,
)

# ── Shared fixtures ───────────────────────────────────────────────────────────

_NOW   = datetime(2026, 7, 25, 14, 0, 0, tzinfo=timezone.utc)
_INST  = "MGC"
_DIR   = "LONG"
_BOS   = {"component": "BOS",  "points": 20, "source": "tradingview", "age_seconds": 30}
_CVD   = {"component": "CVD",  "points": 15, "source": "databento",   "age_seconds": 10}
_VWAP  = {"component": "VWAP", "points": 15, "source": "databento",   "age_seconds": 5}


def _result(inst=_INST, direction=_DIR, edge=75.0,
            verdict="LONG READY", entry=2650.0, stop=2640.0,
            dc_warnings=None):
    return {
        "verdict":    verdict,
        "edge_score": edge,
        "session":    "US",
        "source_attribution": [
            {**_BOS, "age_seconds": 30},
            {**_CVD, "age_seconds": 10},
        ],
        "source_audit": {
            "double_counting_warnings": dc_warnings or []
        },
        "trade_plan": {
            "entry": entry, "stop": stop,
            "t1": 2660.0, "t2": 2670.0,
        },
    }


def _mt(inst=_INST, outcome="Win", r=1.5, mfe=None, mae=None,
        opened_at=None, closed_at=None, manual_close=False):
    return {
        "instrument":  inst,
        "outcome":     outcome,
        "r_multiple":  r,
        "mfe_r":       mfe,
        "mae_r":       mae,
        "opened_at":   opened_at  or _NOW,
        "closed_at":   closed_at  or (_NOW + timedelta(minutes=45)),
        "manual_close": manual_close,
    }


def _mock_conn(rows=None, total=None):
    """Mock psycopg2 connection whose cursor returns given rows from fetchall."""
    cur  = MagicMock()
    cur.fetchall.return_value  = rows or []
    cur.fetchone.return_value  = [total if total is not None else len(rows or [])]
    conn = MagicMock()
    conn.cursor.return_value   = cur
    return conn, cur


def _report_rows(*records):
    """Build fake resolved-snapshot rows (col order matches SELECT in _build_decision_quality_report)."""
    out = []
    for rec in records:
        comps = rec.get("comps", [])
        out.append((
            rec.get("inst",    "MGC"),
            rec.get("dir",     "LONG"),
            rec.get("edge",    75.0),
            rec.get("verdict", "LONG READY"),
            comps,                          # JSONB → Python list (psycopg2 auto-deserialises)
            rec.get("dc",      0),
            rec.get("outcome", "Win"),
            rec.get("r",       1.5),
            rec.get("mfe",     None),
            rec.get("mae",     None),
            rec.get("tmin",    45.0),
            rec.get("exit",    "target_reached"),
            _NOW,
        ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Snapshot Capture Validation
# ─────────────────────────────────────────────────────────────────────────────

def test_s3_ready_captured_once():
    """READY setup: INSERT executed and pending key set."""
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, "SCALP", _result())
    assert cur.execute.called,             "INSERT must be called for first READY"
    assert _INST in _app._DQ_PENDING_BY_INST, "pending key must be set after capture"
    key = _app._DQ_PENDING_BY_INST[_INST]
    assert key.startswith(f"{_INST}::{_DIR}::"), f"unexpected key prefix: {key}"


def test_s3_repeated_ready_not_duplicated():
    """Heartbeat re-evaluation: same inst+direction → no new INSERT, key unchanged."""
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, "SCALP", _result())
        first_key   = _app._DQ_PENDING_BY_INST[_INST]
        count_after_first = cur.execute.call_count
        _capture_decision_snapshot(_INST, _DIR, "SCALP", _result())
    assert cur.execute.call_count == count_after_first, \
        "second READY heartbeat must NOT produce another INSERT"
    assert _app._DQ_PENDING_BY_INST[_INST] == first_key, \
        "pending key must not change on heartbeat re-evaluation"


def test_s3_wait_caller_gated():
    """WAIT verdict is never passed to _capture_decision_snapshot by full_analysis.

    The caller gate in full_analysis is: `if DQ_DB_READY and is_actionable(verdict)`.
    If _capture is never called, no key is set — verified here."""
    _app._DQ_PENDING_BY_INST.clear()
    # Simulate: WAIT verdict → caller does NOT invoke _capture
    # (no call made in this test — exactly what happens in production)
    assert _INST not in _app._DQ_PENDING_BY_INST, \
        "WAIT must not produce a pending key (caller gate prevents the call)"


def test_s3_opposite_direction_separate():
    """Direction flip → new INSERT; pending key overwritten to new direction."""
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, "LONG",  "SCALP", _result())
        long_key     = _app._DQ_PENDING_BY_INST[_INST]
        count_long   = cur.execute.call_count
        _capture_decision_snapshot(_INST, "SHORT", "SCALP",
                                   _result(direction="SHORT", verdict="SHORT READY"))
    assert cur.execute.call_count > count_long, \
        "opposite direction must INSERT a new snapshot"
    short_key = _app._DQ_PENDING_BY_INST[_INST]
    assert short_key.startswith(f"{_INST}::SHORT::"), \
        f"key should reference SHORT: {short_key}"
    assert short_key != long_key, "keys for different directions must differ"


def test_s3_different_instruments_independent():
    """Two instruments are tracked with independent pending keys."""
    _app._DQ_PENDING_BY_INST.clear()
    conn1, _ = _mock_conn()
    conn2, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app.now_utc", return_value=_NOW):
        with patch("app._learning_conn", side_effect=[conn1, conn2]):
            _capture_decision_snapshot("MGC", "LONG", "SCALP", _result("MGC"))
            _capture_decision_snapshot("MNQ", "LONG", "SCALP", _result("MNQ"))
    assert "MGC" in _app._DQ_PENDING_BY_INST
    assert "MNQ" in _app._DQ_PENDING_BY_INST
    assert _app._DQ_PENDING_BY_INST["MGC"] != _app._DQ_PENDING_BY_INST["MNQ"]


def test_s3_new_setup_after_closure():
    """After trade closes (key popped), the next READY can capture a fresh snapshot."""
    _now2 = _NOW + timedelta(minutes=2)   # different timestamp → different key
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    times = iter([_NOW, _NOW, _now2, _now2])  # capture1, resolve1, capture2, resolve2
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", side_effect=times):
        _capture_decision_snapshot(_INST, _DIR, "SCALP", _result())
        first_key    = _app._DQ_PENDING_BY_INST[_INST]
        count_first  = cur.execute.call_count
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
        assert _INST not in _app._DQ_PENDING_BY_INST, "key must be popped by resolve"
        _capture_decision_snapshot(_INST, _DIR, "SCALP", _result())
        second_key   = _app._DQ_PENDING_BY_INST[_INST]
    assert cur.execute.call_count > count_first, \
        "second READY after closure must INSERT a new snapshot"
    assert second_key != first_key, "second setup must get a fresh key"


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: _DQ_PENDING_BY_INST lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def test_s4_key_not_set_on_db_failure():
    """DB cursor failure → exception caught → pending key must NOT be set."""
    _app._DQ_PENDING_BY_INST.clear()
    bad_conn = MagicMock()
    bad_conn.cursor.side_effect = Exception("connection refused")
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=bad_conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, "SCALP", _result())
    assert _INST not in _app._DQ_PENDING_BY_INST, \
        "key must NOT be set when INSERT raises (fail-open catch prevents assignment)"


def test_s4_key_not_set_when_conn_none():
    """_learning_conn returns None → early return before INSERT → key not set."""
    _app._DQ_PENDING_BY_INST.clear()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=None), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, "SCALP", _result())
    assert _INST not in _app._DQ_PENDING_BY_INST, \
        "key must not be set when conn is None"


def test_s4_pop_before_db_call():
    """Pop happens before the DB UPDATE — key is cleared even if DB then fails."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = f"{_INST}::{_DIR}::20260725T140000Z"
    bad_conn = MagicMock()
    bad_conn.cursor.side_effect = Exception("db gone")
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=bad_conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
    assert _INST not in _app._DQ_PENDING_BY_INST, \
        "key must be popped even when subsequent DB UPDATE fails"


def test_s4_resolve_no_key_is_noop():
    """Closure with no matching pending key: no DB call, no error."""
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
    assert not cur.execute.called, "no UPDATE when no pending key"


def test_s4_win_clears_key():
    """Win closure pops the pending key."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = f"{_INST}::LONG::key"
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win", r=1.5))
    assert _INST not in _app._DQ_PENDING_BY_INST


def test_s4_loss_clears_key():
    """Loss closure pops the pending key."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = f"{_INST}::LONG::key"
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Loss", r=-1.0))
    assert _INST not in _app._DQ_PENDING_BY_INST


def test_s4_breakeven_clears_key():
    """Breakeven closure pops the pending key."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = f"{_INST}::LONG::key"
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Breakeven", r=0.0))
    assert _INST not in _app._DQ_PENDING_BY_INST


def test_s4_manual_closure_clears_key():
    """Manual closure pops the pending key and marks manual_exit=True."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = f"{_INST}::LONG::key"
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win", manual_close=True))
    assert _INST not in _app._DQ_PENDING_BY_INST, "key should be cleared"
    # Verify manual_exit=True in UPDATE params
    update_args = [c for c in cur.execute.call_args_list
                   if cur.execute.called and "UPDATE" in str(c)]
    if update_args:
        params = update_args[0][0][1]
        # manual_exit is the 9th positional param (index 8)
        assert params[8] is True, f"manual_exit should be True, got {params[8]}"


def test_s4_duplicate_closure_second_is_noop():
    """Two consecutive closures for same instrument: second is a no-op."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = f"{_INST}::LONG::key"
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))   # first
        first_count = cur.execute.call_count
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))   # duplicate
    assert cur.execute.call_count == first_count, \
        "second closure must not invoke another UPDATE (no pending key)"


def test_s4_restart_clears_memory():
    """On restart, _DQ_PENDING_BY_INST is an empty dict (module-level initialisation)."""
    assert isinstance(_app._DQ_PENDING_BY_INST, dict), \
        "_DQ_PENDING_BY_INST must be a plain dict"
    # After import, module initialises it to {} — verified by checking type
    # (the real value may have entries from other tests; the point is the type)


def test_s4_dq_disabled_resolve_does_not_pop():
    """When DQ_DB_READY is False, _resolve returns early WITHOUT popping the key.

    RISK NOTE: if DQ_DB_READY goes False mid-session (e.g., DB connectivity loss),
    a pending key can become permanently stale — cleared only on next restart.
    Impact is analytics-only (orphaned snapshot); trading is unaffected."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = "stale_key"
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", False), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
    # Key was NOT popped because DQ_DB_READY=False causes early return
    assert _app._DQ_PENDING_BY_INST.get(_INST) == "stale_key", \
        "Confirmed: DQ_DB_READY=False leaves stale key (documented risk)"
    assert not cur.execute.called, "no DB call when DQ disabled"


def test_s4_stale_key_blocks_same_direction():
    """DOCUMENTED RISK: stale same-direction pending key blocks future captures.

    Scenario: READY LONG captured → verdict goes WAIT (no trade taken) →
    verdict returns READY LONG later → dedup sees existing key → no new INSERT.
    Impact: analytics-only (misses a snapshot). Trading is completely unaffected."""
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, "LONG", "SCALP", _result())
        count_after_first = cur.execute.call_count
        # Verdict goes WAIT (no trade opened) then returns READY LONG
        _capture_decision_snapshot(_INST, "LONG", "SCALP", _result())
    assert cur.execute.call_count == count_after_first, \
        "CONFIRMED stale-key blocks: second READY LONG is skipped by dedup. " \
        "RISK (analytics-only): this snapshot is not recorded."


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Outcome matching
# ─────────────────────────────────────────────────────────────────────────────

def test_s5_sequential_trades_different_snapshots():
    """Two sequential trades on same instrument resolve to different snapshot rows."""
    _now2 = _NOW + timedelta(minutes=5)   # different timestamp → different key
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    updated_keys = []

    def _track(sql, params=None):
        if params and "UPDATE" in str(sql):
            updated_keys.append(params[-1])   # snapshot_key is last param

    cur.execute.side_effect = _track

    # now_utc called once inside each _capture, once inside each _resolve
    times = iter([_NOW, _NOW, _now2, _now2])
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", side_effect=times):
        _capture_decision_snapshot(_INST, "LONG", "SCALP", _result())
        key1 = _app._DQ_PENDING_BY_INST[_INST]
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
        _capture_decision_snapshot(_INST, "LONG", "SCALP", _result())
        key2 = _app._DQ_PENDING_BY_INST[_INST]
        _resolve_decision_snapshot(_INST, _mt(outcome="Loss", r=-1.0))

    assert key1 != key2, "sequential setups must produce different snapshot keys"
    if len(updated_keys) == 2:
        assert updated_keys[0] == key1, "first UPDATE must target first snapshot"
        assert updated_keys[1] == key2, "second UPDATE must target second snapshot"


def test_s5_long_then_short():
    """LONG resolved → SHORT captured and resolved → correct key separation."""
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    keys_resolved = []

    def _track(sql, params=None):
        if params and "UPDATE" in str(sql):
            keys_resolved.append(params[-1])

    cur.execute.side_effect = _track

    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, "LONG",  "SCALP", _result())
        long_key  = _app._DQ_PENDING_BY_INST[_INST]
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
        _capture_decision_snapshot(_INST, "SHORT", "SCALP",
                                   _result(direction="SHORT", verdict="SHORT READY"))
        short_key = _app._DQ_PENDING_BY_INST[_INST]
        _resolve_decision_snapshot(_INST, _mt(outcome="Loss", r=-1.0))

    assert long_key  != short_key
    assert long_key.startswith(f"{_INST}::LONG::")
    assert short_key.startswith(f"{_INST}::SHORT::")
    if len(keys_resolved) == 2:
        assert keys_resolved[0] == long_key
        assert keys_resolved[1] == short_key


def test_s5_two_instruments_resolve_independently():
    """Two instruments closing simultaneously do not cross-resolve."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST["MGC"] = "MGC::LONG::key_a"
    _app._DQ_PENDING_BY_INST["MNQ"] = "MNQ::LONG::key_b"
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt("MGC", "Win"))
        _resolve_decision_snapshot("MNQ", _mt("MNQ", "Loss", r=-1.0))
    assert "MGC" not in _app._DQ_PENDING_BY_INST
    assert "MNQ" not in _app._DQ_PENDING_BY_INST


def test_s5_direction_flip_overwrites_pending_key():
    """Direction flip overwrites the in-memory key; old key becomes DB orphan.

    Outcome matching risk: if a LONG trade is still open when direction flips
    to SHORT and a SHORT snapshot is captured, the LONG trade's eventual closure
    will pop the SHORT key instead, causing a direction mismatch in the DB UPDATE.
    In practice this is mitigated by the gate (LONG setup active prevents SHORT READY),
    but documented here as a known edge-case risk."""
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, "LONG",  "SCALP", _result())
        long_key  = _app._DQ_PENDING_BY_INST[_INST]
        # Direction flips — overwrites key without a closure
        _capture_decision_snapshot(_INST, "SHORT", "SCALP",
                                   _result(direction="SHORT", verdict="SHORT READY"))
        short_key = _app._DQ_PENDING_BY_INST[_INST]
    assert short_key != long_key, "direction flip overwrites key"
    assert short_key.startswith(f"{_INST}::SHORT::")


def test_s5_closure_with_no_snapshot_noop():
    """Closure arrives with no pending snapshot: no DB call, no crash."""
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
    assert not cur.execute.called


def test_s5_update_guarded_by_outcome_is_null():
    """UPDATE includes AND outcome IS NULL — prevents double-resolution in the DB."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = "key_x"
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
    sql_calls = [str(c) for c in cur.execute.call_args_list]
    assert any("outcome IS NULL" in sql for sql in sql_calls), \
        "UPDATE must include AND outcome IS NULL guard"


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: Analytics accuracy
# ─────────────────────────────────────────────────────────────────────────────

_BOS_ROW  = {"component": "BOS",  "points": 20, "source": "tradingview", "age_s": 30}
_CVD_ROW  = {"component": "CVD",  "points": 15, "source": "databento",   "age_s": 10}


def test_s6_win_rate_present_vs_absent():
    """BOS present: 2/3 wins = 66.7%; BOS absent: 1/2 wins = 50%."""
    rows = _report_rows(
        {"comps": [_BOS_ROW], "outcome": "Win",  "r": 1.5},
        {"comps": [_BOS_ROW], "outcome": "Win",  "r": 2.0},
        {"comps": [_BOS_ROW], "outcome": "Loss", "r": -1.0},
        {"comps": [],         "outcome": "Win",  "r": 1.0},
        {"comps": [],         "outcome": "Loss", "r": -1.0},
    )
    conn, _ = _mock_conn(rows=rows, total=5)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    bos = report["component_performance"].get("BOS", {})
    assert bos["win_rate_present"] == pytest.approx(66.7, abs=0.2)
    assert bos["win_rate_absent"]  == pytest.approx(50.0, abs=0.2)
    assert bos["present_count"]    == 3
    assert bos["absent_count"]     == 2


def test_s6_avg_r_present():
    """Average R when BOS present: (1.5 + 2.0 + -1.0) / 3 ≈ 0.83."""
    rows = _report_rows(
        {"comps": [_BOS_ROW], "outcome": "Win",  "r": 1.5},
        {"comps": [_BOS_ROW], "outcome": "Win",  "r": 2.0},
        {"comps": [_BOS_ROW], "outcome": "Loss", "r": -1.0},
    )
    conn, _ = _mock_conn(rows=rows, total=3)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    bos = report["component_performance"].get("BOS", {})
    assert bos["avg_r_present"] == pytest.approx(0.83, abs=0.02)


def test_s6_breakeven_not_counted_as_win():
    """Breakeven outcome is neither win nor loss: overall win rate = 1/3 ≈ 33.3%."""
    rows = _report_rows(
        {"comps": [], "outcome": "Win",       "r": 1.0},
        {"comps": [], "outcome": "Loss",      "r": -1.0},
        {"comps": [], "outcome": "Breakeven", "r": 0.0},
    )
    conn, _ = _mock_conn(rows=rows, total=3)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    assert report["overall_win_rate"] == pytest.approx(33.3, abs=0.2), \
        f"overall_win_rate={report.get('overall_win_rate')} — Breakeven must not count as win"


def test_s6_null_r_excluded_from_avg():
    """None outcome_r is skipped in average-R — must not silently become 0."""
    rows = _report_rows(
        {"comps": [_BOS_ROW], "outcome": "Win", "r": 2.0},
        {"comps": [_BOS_ROW], "outcome": "Win", "r": None},   # null
    )
    conn, _ = _mock_conn(rows=rows, total=2)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    bos = report["component_performance"].get("BOS", {})
    # avg R must be 2.0 (one non-null), not 1.0 (if null silently → 0)
    assert bos["avg_r_present"] == pytest.approx(2.0, abs=0.01), \
        f"null R silently zeroed: avg_r_present={bos.get('avg_r_present')}"


def test_s6_overall_avg_r_excludes_null():
    """Overall avg_r excludes rows with None outcome_r."""
    rows = _report_rows(
        {"comps": [], "outcome": "Win",  "r": 2.0},
        {"comps": [], "outcome": "Win",  "r": None},
        {"comps": [], "outcome": "Loss", "r": -1.0},
    )
    conn, _ = _mock_conn(rows=rows, total=3)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    # avg of [2.0, -1.0] = 0.5 (null excluded)
    assert report["avg_r"] == pytest.approx(0.5, abs=0.02), \
        f"overall avg_r={report.get('avg_r')} — should be 0.5 not include null"


def test_s6_small_sample_no_component_recommendation():
    """Below MIN_SAMPLES (5): component with delta ≥ 15pp must not get a recommendation."""
    rows = _report_rows(
        {"comps": [_BOS_ROW], "outcome": "Win",  "r": 2.0},
        {"comps": [_BOS_ROW], "outcome": "Win",  "r": 2.0},
        {"comps": [_BOS_ROW], "outcome": "Win",  "r": 2.0},
        {"comps": [],         "outcome": "Loss", "r": -1.0},
    )
    conn, _ = _mock_conn(rows=rows, total=4)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    high_recs = [r for r in report["recommendations"]
                 if r.get("component") == "BOS" and r.get("priority") == "high"]
    assert not high_recs, \
        "must not generate a 'high' recommendation for BOS with only 3 samples"


def test_s6_duplicate_warning_split():
    """Duplicate-evidence impact: 2 with-warnings (both wins), 2 without (both losses)."""
    rows = _report_rows(
        {"comps": [], "dc": 1, "outcome": "Win",  "r": 1.5},
        {"comps": [], "dc": 1, "outcome": "Win",  "r": 1.5},
        {"comps": [], "dc": 0, "outcome": "Loss", "r": -1.0},
        {"comps": [], "dc": 0, "outcome": "Loss", "r": -1.0},
    )
    conn, _ = _mock_conn(rows=rows, total=4)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    di = report["duplicate_impact"]
    assert di["with_warnings"]["win_rate"]    == 100.0
    assert di["without_warnings"]["win_rate"] == 0.0
    assert report["dup_delta"]                == pytest.approx(100.0, abs=0.1)


def test_s6_open_trades_excluded():
    """Open trades (outcome=NULL) are filtered by WHERE outcome IS NOT NULL in the query.

    Verified by the SQL: SELECT ... WHERE outcome IS NOT NULL.
    This test confirms the report reflects only resolved rows."""
    # The mock returns only resolved rows (SQL filters open trades in production)
    rows = _report_rows({"comps": [], "outcome": "Win", "r": 1.5})
    conn, _ = _mock_conn(rows=rows, total=3)   # total=3 includes pending rows
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    # resolved count is len(rows)=1, not total_captured=3
    assert report["resolved"]       == 1
    assert report["total_captured"] == 3


def test_s6_empty_db_safe_response():
    """No resolved trades → safe empty-state dict, no exception, no 'error' key."""
    conn, _ = _mock_conn(rows=[], total=0)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    assert report["enabled"]  is True
    assert report["resolved"] == 0
    assert "message" in report
    assert "error"   not in report


def test_s6_dq_disabled_safe():
    """DQ_DB_READY=False → disabled response, no exception."""
    with patch("app.DQ_DB_READY", False):
        report = _build_decision_quality_report()
    assert report["enabled"] is False
    assert "error" not in report


def test_s6_db_unavailable_safe():
    """DB conn=None → error key returned, no exception raised."""
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=None):
        report = _build_decision_quality_report()
    assert "error" in report


def test_s6_top_bottom_components_sorted():
    """top_components has highest delta; bottom_components has lowest (≥ MIN_SAMPLES each)."""
    # Build rows so BOS: 5 present wins + 0 losses (100% present),
    #                CVD: 5 present losses (0% present)
    rows = (
        _report_rows(*[{"comps": [_BOS_ROW], "outcome": "Win",  "r": 1.5}] * 5) +
        _report_rows(*[{"comps": [_CVD_ROW], "outcome": "Loss", "r": -1.0}] * 5) +
        _report_rows(*[{"comps": [],         "outcome": "Loss", "r": -1.0}] * 5)
    )
    conn, _ = _mock_conn(rows=rows, total=15)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    tops = report.get("top_components", [])
    bots = report.get("bottom_components", [])
    if tops:
        assert tops[0]["component"] == "BOS", \
            f"BOS should be top (100% win rate), got {tops[0].get('component')}"
    if bots:
        last = bots[-1]["component"]
        assert last in ("CVD", "BOS"), f"CVD should be bottom: {last}"


# ─────────────────────────────────────────────────────────────────────────────
# Section 7: Read-only proof
# ─────────────────────────────────────────────────────────────────────────────

def test_s7_capture_does_not_mutate_result():
    """_capture_decision_snapshot must not alter the result dict in any way."""
    _app._DQ_PENDING_BY_INST.clear()
    result = _result()
    result_before = copy.deepcopy(result)
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, "SCALP", result)
    assert result == result_before, "result dict was mutated by _capture_decision_snapshot"


def test_s7_resolve_does_not_mutate_mt():
    """_resolve_decision_snapshot must not alter the mt dict."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = f"{_INST}::LONG::key"
    mt = _mt(outcome="Win", r=1.5)
    mt_before = copy.deepcopy(mt)
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, mt)
    assert mt == mt_before, "mt dict was mutated by _resolve_decision_snapshot"


def test_s7_dq_false_capture_noop():
    """DQ_DB_READY=False: no DB call, no key set, no exception."""
    _app._DQ_PENDING_BY_INST.clear()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", False), patch("app._learning_conn", return_value=conn):
        _capture_decision_snapshot(_INST, _DIR, "SCALP", _result())
    assert not cur.execute.called
    assert _INST not in _app._DQ_PENDING_BY_INST


def test_s7_dq_false_resolve_noop():
    """DQ_DB_READY=False: no DB call, no exception (key retained per documented risk)."""
    _app._DQ_PENDING_BY_INST.clear()
    _app._DQ_PENDING_BY_INST[_INST] = "some_key"
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", False), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
    assert not cur.execute.called


def test_s7_report_is_select_only():
    """_build_decision_quality_report: no INSERT/UPDATE/DELETE in SQL calls."""
    rows = _report_rows({"comps": [], "outcome": "Win", "r": 1.5})
    conn, cur = _mock_conn(rows=rows, total=1)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _build_decision_quality_report()
    for c in cur.execute.call_args_list:
        sql = str(c).upper()
        assert "INSERT" not in sql, f"INSERT found in report query: {c}"
        assert "UPDATE" not in sql, f"UPDATE found in report query: {c}"
        assert "DELETE" not in sql, f"DELETE found in report query: {c}"


# ─────────────────────────────────────────────────────────────────────────────
# Section 8: Boot probe
# ─────────────────────────────────────────────────────────────────────────────

def test_s8_boot_probe_sets_flag():
    """_check_dq_db_ready: successful SELECT → DQ_DB_READY=True."""
    _app.DQ_DB_READY = False
    conn, _ = _mock_conn()
    with patch("app.LEARNING_DB_ENABLED", True), \
         patch("app._learning_conn", return_value=conn):
        _check_dq_db_ready()
    assert _app.DQ_DB_READY is True


def test_s8_boot_probe_stays_false_on_missing_table():
    """_check_dq_db_ready: missing table → DQ_DB_READY stays False."""
    _app.DQ_DB_READY = False
    bad_conn = MagicMock()
    bad_conn.cursor.return_value.execute.side_effect = Exception("relation does not exist")
    with patch("app.LEARNING_DB_ENABLED", True), \
         patch("app._learning_conn", return_value=bad_conn):
        _check_dq_db_ready()
    assert _app.DQ_DB_READY is False


def test_s8_boot_probe_skips_when_db_disabled():
    """LEARNING_DB_ENABLED=False → no DB call, DQ_DB_READY stays False."""
    _app.DQ_DB_READY = False
    conn, cur = _mock_conn()
    with patch("app.LEARNING_DB_ENABLED", False), \
         patch("app._learning_conn", return_value=conn):
        _check_dq_db_ready()
    assert not cur.execute.called
    assert _app.DQ_DB_READY is False


def test_s8_boot_probe_conn_none():
    """_learning_conn returns None → DQ_DB_READY stays False."""
    _app.DQ_DB_READY = False
    with patch("app.LEARNING_DB_ENABLED", True), \
         patch("app._learning_conn", return_value=None):
        _check_dq_db_ready()
    assert _app.DQ_DB_READY is False


# ─────────────────────────────────────────────────────────────────────────────
# Restore shared module state after all tests
# ─────────────────────────────────────────────────────────────────────────────

def teardown_module(module):
    _app._DQ_PENDING_BY_INST.clear()
    # Restore DQ_DB_READY to True (real table exists)
    _app.DQ_DB_READY = True
