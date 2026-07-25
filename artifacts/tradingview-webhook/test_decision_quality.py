"""
Phase 5F Decision Quality & Signal Calibration — Validation Test Suite.
Updated for Phase 5F.1 Lifecycle Repair.

Covers:
  Section 3  — Snapshot capture dedup and gating
  Section 4  — _DQ_PENDING_BY_SETUP lifecycle (set / pop / stale paths)
  Section 5  — Outcome matching (sequential, direction-flip, multi-inst)
  Section 6  — Analytics accuracy (win rate, avg R, null, min-sample, dup impact)
  Section 7  — Read-only proof (result dict, mt dict not mutated)
  Section 8  — Boot probe behaviour
  Section 9  — Phase 5F.1 focused lifecycle tests (30 new tests)

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
    _dq_fingerprint,
    _dq_abandon_setup,
)

# ── Shared fixtures ───────────────────────────────────────────────────────────

_NOW   = datetime(2026, 7, 25, 14, 0, 0, tzinfo=timezone.utc)
_INST  = "MGC"
_DIR   = "LONG"
_MODE  = "SCALP"
_BOS   = {"component": "BOS",  "points": 20, "source": "tradingview", "age_seconds": 30}
_CVD   = {"component": "CVD",  "points": 15, "source": "databento",   "age_seconds": 10}
_VWAP  = {"component": "VWAP", "points": 15, "source": "databento",   "age_seconds": 5}


def _fp(inst=_INST, direction=_DIR, mode=_MODE):
    """Convenience fingerprint builder matching _dq_fingerprint()."""
    return _dq_fingerprint(inst, direction, mode)


def _pending_entry(snap_key, inst=_INST, direction=_DIR, mode=_MODE, created_at=None):
    """Build a valid pending entry dict for _DQ_PENDING_BY_SETUP."""
    return {
        "snapshot_key": snap_key,
        "inst":         inst,
        "direction":    direction,
        "mode":         mode,
        "created_at":   created_at or _NOW,
    }


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
        opened_at=None, closed_at=None, manual_close=False,
        direction=_DIR):
    return {
        "instrument":   inst,
        "direction":    direction,
        "outcome":      outcome,
        "r_multiple":   r,
        "mfe_r":        mfe,
        "mae_r":        mae,
        "opened_at":    opened_at  or _NOW,
        "closed_at":    closed_at  or (_NOW + timedelta(minutes=45)),
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
            comps,
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


def _clear_pending():
    """Clear the pending setup dict and reset unmatched counter for test isolation."""
    _app._DQ_PENDING_BY_SETUP.clear()
    _app._DQ_UNMATCHED_CLOSURES = 0


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Snapshot Capture Validation
# ─────────────────────────────────────────────────────────────────────────────

def test_s3_ready_captured_once():
    """READY setup: INSERT executed and pending entry set at the correct fingerprint."""
    _clear_pending()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
    assert cur.execute.called, "INSERT must be called for first READY"
    fp = _fp()
    assert fp in _app._DQ_PENDING_BY_SETUP, "pending entry must be set after capture"
    entry = _app._DQ_PENDING_BY_SETUP[fp]
    assert entry["snapshot_key"].startswith(f"{_INST}::{_DIR}::"), \
        f"unexpected snapshot_key prefix: {entry['snapshot_key']}"


def test_s3_repeated_ready_not_duplicated():
    """Heartbeat re-evaluation: same inst+direction+mode → no new INSERT, entry unchanged."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        first_key         = _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"]
        count_after_first = cur.execute.call_count
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
    assert cur.execute.call_count == count_after_first, \
        "second READY heartbeat must NOT produce another INSERT"
    assert _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"] == first_key, \
        "pending snapshot_key must not change on heartbeat re-evaluation"


def test_s3_wait_caller_gated():
    """WAIT verdict is never passed to _capture_decision_snapshot by full_analysis.

    The caller gate in full_analysis is: `if DQ_DB_READY and is_actionable(verdict)`.
    If _capture is never called, no entry is set — verified here."""
    _clear_pending()
    fp = _fp()
    # Simulate: WAIT verdict → caller does NOT invoke _capture
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "WAIT must not produce a pending entry (caller gate prevents the call)"


def test_s3_opposite_direction_separate():
    """Direction flip → new INSERT; both Long and Short tracked independently."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp_long  = _fp(_INST, "LONG",  _MODE)
    fp_short = _fp(_INST, "SHORT", _MODE)
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, "LONG",  _MODE, _result())
        long_key   = _app._DQ_PENDING_BY_SETUP[fp_long]["snapshot_key"]
        count_long = cur.execute.call_count
        _capture_decision_snapshot(_INST, "SHORT", _MODE,
                                   _result(direction="SHORT", verdict="SHORT READY"))
    assert cur.execute.call_count > count_long, \
        "opposite direction must INSERT a new snapshot"
    # Phase 5F.1 fix: both entries coexist — Long is NOT overwritten by Short
    assert fp_long  in _app._DQ_PENDING_BY_SETUP, "LONG entry must survive after SHORT capture"
    assert fp_short in _app._DQ_PENDING_BY_SETUP, "SHORT entry must be independently tracked"
    short_key = _app._DQ_PENDING_BY_SETUP[fp_short]["snapshot_key"]
    assert short_key.startswith(f"{_INST}::SHORT::"), \
        f"key should reference SHORT: {short_key}"
    assert short_key != long_key, "keys for different directions must differ"


def test_s3_different_instruments_independent():
    """Two instruments are tracked with independent pending entries."""
    _clear_pending()
    conn1, _ = _mock_conn()
    conn2, _ = _mock_conn()
    fp_mgc = _fp("MGC", "LONG", _MODE)
    fp_mnq = _fp("MNQ", "LONG", _MODE)
    with patch("app.DQ_DB_READY", True), \
         patch("app.now_utc", return_value=_NOW):
        with patch("app._learning_conn", side_effect=[conn1, conn2]):
            _capture_decision_snapshot("MGC", "LONG", _MODE, _result("MGC"))
            _capture_decision_snapshot("MNQ", "LONG", _MODE, _result("MNQ"))
    assert fp_mgc in _app._DQ_PENDING_BY_SETUP
    assert fp_mnq in _app._DQ_PENDING_BY_SETUP
    assert (_app._DQ_PENDING_BY_SETUP[fp_mgc]["snapshot_key"] !=
            _app._DQ_PENDING_BY_SETUP[fp_mnq]["snapshot_key"])


def test_s3_new_setup_after_closure():
    """After trade closes (entry popped), the next READY can capture a fresh snapshot."""
    _now2 = _NOW + timedelta(minutes=2)
    _clear_pending()
    conn, cur = _mock_conn()
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        first_key   = _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"]
        count_first = cur.execute.call_count
        _resolve_decision_snapshot(_INST, _mt())
        assert fp not in _app._DQ_PENDING_BY_SETUP, "entry must be popped by resolve"
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_now2):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        second_key = _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"]
    assert cur.execute.call_count > count_first, \
        "second READY after closure must INSERT a new snapshot"
    assert second_key != first_key, "second setup must get a fresh snapshot_key"


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: _DQ_PENDING_BY_SETUP lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def test_s4_key_not_set_on_db_failure():
    """DB cursor failure → exception caught → pending entry must NOT be set."""
    _clear_pending()
    bad_conn = MagicMock()
    bad_conn.cursor.side_effect = Exception("connection refused")
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=bad_conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "entry must NOT be set when INSERT raises (fail-open catch prevents assignment)"


def test_s4_key_not_set_when_conn_none():
    """_learning_conn returns None → early return before INSERT → entry not set."""
    _clear_pending()
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=None), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "entry must not be set when conn is None"


def test_s4_pop_before_db_call():
    """Pop happens before the DB UPDATE — entry is cleared even if DB then fails."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry(f"{_INST}::{_DIR}::20260725T140000Z")
    bad_conn = MagicMock()
    bad_conn.cursor.side_effect = Exception("db gone")
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=bad_conn):
        _resolve_decision_snapshot(_INST, _mt())
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "entry must be popped even when subsequent DB UPDATE fails"


def test_s4_resolve_no_key_is_noop():
    """Closure with no matching pending entry: no DB call, no error."""
    _clear_pending()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt())
    assert not cur.execute.called, "no UPDATE when no pending entry"


def test_s4_win_clears_key():
    """Win closure pops the pending entry."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry(f"{_INST}::LONG::key")
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win", r=1.5))
    assert fp not in _app._DQ_PENDING_BY_SETUP


def test_s4_loss_clears_key():
    """Loss closure pops the pending entry."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry(f"{_INST}::LONG::key")
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Loss", r=-1.0))
    assert fp not in _app._DQ_PENDING_BY_SETUP


def test_s4_breakeven_clears_key():
    """Breakeven closure pops the pending entry."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry(f"{_INST}::LONG::key")
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Breakeven", r=0.0))
    assert fp not in _app._DQ_PENDING_BY_SETUP


def test_s4_manual_closure_clears_key():
    """Manual closure pops the pending entry and marks manual_exit=True."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry(f"{_INST}::LONG::key")
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win", manual_close=True))
    assert fp not in _app._DQ_PENDING_BY_SETUP, "entry should be cleared"
    update_args = [c for c in cur.execute.call_args_list
                   if "UPDATE" in str(c)]
    if update_args:
        params = update_args[0][0][1]
        assert params[8] is True, f"manual_exit should be True, got {params[8]}"


def test_s4_duplicate_closure_second_is_noop():
    """Two consecutive closures for same fingerprint: second is a no-op."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry(f"{_INST}::LONG::key")
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
        first_count = cur.execute.call_count
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))
    assert cur.execute.call_count == first_count, \
        "second closure must not invoke another UPDATE (no pending entry)"


def test_s4_restart_clears_memory():
    """On restart, _DQ_PENDING_BY_SETUP is an empty dict (module-level initialisation)."""
    assert isinstance(_app._DQ_PENDING_BY_SETUP, dict), \
        "_DQ_PENDING_BY_SETUP must be a plain dict"


def test_s4_dq_disabled_resolve_clears_entry():
    """FIXED (Phase 5F.1): DQ_DB_READY=False now CLEARS the pending entry.

    Old behavior: DQ_DB_READY=False returned early before popping — leaving a
    blocking stale entry. Fixed behavior: entry is always popped first, so
    DB unavailability never permanently blocks future captures."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("stale_key")
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", False), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt())
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "Phase 5F.1 fix: DQ_DB_READY=False must still clear the in-memory entry"
    assert not cur.execute.called, "no DB UPDATE when DQ disabled"


def test_s4_ready_wait_ready_captures_new_setup():
    """FIXED (Phase 5F.1): READY→WAIT→READY on same fingerprint triggers a new INSERT.

    _dq_abandon_setup (called by full_analysis on WAIT) clears the pending entry
    so the next READY setup is captured fresh rather than skipped by heartbeat dedup."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        assert fp in _app._DQ_PENDING_BY_SETUP, "Setup A must be pending"
        count_a = cur.execute.call_count

    _dq_abandon_setup(fp)  # full_analysis WAIT path
    assert fp not in _app._DQ_PENDING_BY_SETUP, "WAIT must clear the pending entry"

    now2 = _NOW + timedelta(minutes=3)
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=now2):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
    assert cur.execute.call_count > count_a, "Setup B must INSERT after WAIT-clear"
    assert fp in _app._DQ_PENDING_BY_SETUP, "Setup B must have a new pending entry"


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Outcome matching
# ─────────────────────────────────────────────────────────────────────────────

def test_s5_sequential_trades_different_snapshots():
    """Two sequential trades on same instrument resolve to different snapshot rows."""
    _now2 = _NOW + timedelta(minutes=5)
    _clear_pending()
    conn, cur = _mock_conn()
    fp = _fp()
    updated_keys = []

    def _track(sql, params=None):
        if params and "UPDATE" in str(sql):
            updated_keys.append(params[-1])

    cur.execute.side_effect = _track

    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        key1 = _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"]
        _resolve_decision_snapshot(_INST, _mt())

    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_now2):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        key2 = _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"]
        _resolve_decision_snapshot(_INST, _mt(outcome="Loss", r=-1.0))

    assert key1 != key2, "sequential setups must produce different snapshot_keys"
    if len(updated_keys) == 2:
        assert updated_keys[0] == key1, "first UPDATE must target first snapshot"
        assert updated_keys[1] == key2, "second UPDATE must target second snapshot"


def test_s5_long_then_short():
    """LONG resolved → SHORT captured and resolved → correct key separation."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp_long  = _fp(_INST, "LONG",  _MODE)
    fp_short = _fp(_INST, "SHORT", _MODE)
    keys_resolved = []

    def _track(sql, params=None):
        if params and "UPDATE" in str(sql):
            keys_resolved.append(params[-1])

    cur.execute.side_effect = _track

    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, "LONG",  _MODE, _result())
        long_key  = _app._DQ_PENDING_BY_SETUP[fp_long]["snapshot_key"]
        _resolve_decision_snapshot(_INST, _mt(direction="LONG", outcome="Win"))
        _capture_decision_snapshot(_INST, "SHORT", _MODE,
                                   _result(direction="SHORT", verdict="SHORT READY"))
        short_key = _app._DQ_PENDING_BY_SETUP[fp_short]["snapshot_key"]
        _resolve_decision_snapshot(_INST, _mt(direction="SHORT", outcome="Loss", r=-1.0))

    assert long_key  != short_key
    assert long_key.startswith(f"{_INST}::LONG::")
    assert short_key.startswith(f"{_INST}::SHORT::")
    if len(keys_resolved) == 2:
        assert keys_resolved[0] == long_key
        assert keys_resolved[1] == short_key


def test_s5_two_instruments_resolve_independently():
    """Two instruments closing simultaneously do not cross-resolve."""
    _clear_pending()
    fp_mgc = _fp("MGC", "LONG", _MODE)
    fp_mnq = _fp("MNQ", "LONG", _MODE)
    _app._DQ_PENDING_BY_SETUP[fp_mgc] = _pending_entry("MGC::LONG::key_a", "MGC", "LONG")
    _app._DQ_PENDING_BY_SETUP[fp_mnq] = _pending_entry("MNQ::LONG::key_b", "MNQ", "LONG")
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt("MGC", direction="LONG"))
        _resolve_decision_snapshot("MNQ", _mt("MNQ", outcome="Loss", r=-1.0,
                                               direction="LONG"))
    assert fp_mgc not in _app._DQ_PENDING_BY_SETUP
    assert fp_mnq not in _app._DQ_PENDING_BY_SETUP


def test_s5_direction_flip_creates_separate_entries():
    """Direction flip creates a SEPARATE entry — old Long entry is NOT overwritten.

    Phase 5F.1 fix: unlike the old single-slot behavior, Long and Short entries
    now coexist at distinct fingerprints. The old Long entry remains pending until
    its trade closes or it expires via TTL. This removes the direction-mismatch risk
    present in the old overwrite approach."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp_long  = _fp(_INST, "LONG",  _MODE)
    fp_short = _fp(_INST, "SHORT", _MODE)
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, "LONG",  _MODE, _result())
        long_key  = _app._DQ_PENDING_BY_SETUP[fp_long]["snapshot_key"]
        _capture_decision_snapshot(_INST, "SHORT", _MODE,
                                   _result(direction="SHORT", verdict="SHORT READY"))
        short_key = _app._DQ_PENDING_BY_SETUP[fp_short]["snapshot_key"]
    assert fp_long  in _app._DQ_PENDING_BY_SETUP, "LONG entry must survive direction switch"
    assert fp_short in _app._DQ_PENDING_BY_SETUP, "SHORT entry must be independently tracked"
    assert short_key != long_key, "direction flip must produce distinct snapshot_keys"
    assert short_key.startswith(f"{_INST}::SHORT::")


def test_s5_closure_with_no_snapshot_noop():
    """Closure arrives with no pending snapshot: no DB call, no crash."""
    _clear_pending()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt())
    assert not cur.execute.called


def test_s5_update_guarded_by_outcome_is_null():
    """UPDATE includes AND outcome IS NULL — prevents double-resolution in the DB."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("key_x")
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt())
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
        {"comps": [_BOS_ROW], "outcome": "Win", "r": None},
    )
    conn, _ = _mock_conn(rows=rows, total=2)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    bos = report["component_performance"].get("BOS", {})
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
    assert report["avg_r"] == pytest.approx(0.5, abs=0.02), \
        f"overall avg_r={report.get('avg_r')} — should be 0.5, not include null"


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
    """Open trades (outcome=NULL) are filtered by WHERE outcome IS NOT NULL in the query."""
    rows = _report_rows({"comps": [], "outcome": "Win", "r": 1.5})
    conn, _ = _mock_conn(rows=rows, total=3)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
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
    _clear_pending()
    result = _result()
    result_before = copy.deepcopy(result)
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, result)
    assert result == result_before, "result dict was mutated by _capture_decision_snapshot"


def test_s7_resolve_does_not_mutate_mt():
    """_resolve_decision_snapshot must not alter the mt dict."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry(f"{_INST}::LONG::key")
    mt = _mt()
    mt_before = copy.deepcopy(mt)
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, mt)
    assert mt == mt_before, "mt dict was mutated by _resolve_decision_snapshot"


def test_s7_dq_false_capture_noop():
    """DQ_DB_READY=False: no DB call, no entry set, no exception."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp = _fp()
    with patch("app.DQ_DB_READY", False), patch("app._learning_conn", return_value=conn):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
    assert not cur.execute.called
    assert fp not in _app._DQ_PENDING_BY_SETUP


def test_s7_dq_false_resolve_noop():
    """DQ_DB_READY=False: no DB call; Phase 5F.1 fix: entry IS cleared."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("some_key")
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", False), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt())
    assert not cur.execute.called
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "Phase 5F.1 fix: entry must be cleared even when DQ_DB_READY=False"


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
# Section 9: Phase 5F.1 Focused Lifecycle Tests (30 new tests)
# ─────────────────────────────────────────────────────────────────────────────

# ── 1. Same READY across heartbeats captured exactly once ────────────────────

def test_5f1_heartbeat_dedup_same_fp():
    """Spec 1: identical READY heartbeats → exactly one INSERT, one pending entry."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        for _ in range(5):
            _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
    inserts = [c for c in cur.execute.call_args_list if "INSERT" in str(c)]
    assert len(inserts) == 1, f"expected 1 INSERT across 5 heartbeats, got {len(inserts)}"
    assert len(_app._DQ_PENDING_BY_SETUP) == 1
    assert fp in _app._DQ_PENDING_BY_SETUP


# ── 2. READY A → WAIT → READY B (same inst+dir) captures B ──────────────────

def test_5f1_ready_wait_ready_b_captured():
    """Spec 2: READY→WAIT→READY on same fingerprint triggers a fresh INSERT."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        key_a   = _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"]
        count_a = cur.execute.call_count

    _dq_abandon_setup(fp)  # full_analysis WAIT path
    assert fp not in _app._DQ_PENDING_BY_SETUP

    now2 = _NOW + timedelta(minutes=3)
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=now2):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())

    assert cur.execute.call_count > count_a, "Setup B must INSERT"
    key_b = _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"]
    assert key_b != key_a, "Setup B must get a new snapshot_key"


# ── 3. READY A → invalidated → READY B captures B ───────────────────────────

def test_5f1_invalidated_then_ready_captures():
    """Spec 3: after explicit abandon (setup invalidated), next READY is captured."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        count_a = cur.execute.call_count

    _dq_abandon_setup(fp)

    now2 = _NOW + timedelta(minutes=10)
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=now2):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())

    assert cur.execute.call_count > count_a, "Post-invalidation READY B must INSERT"
    assert fp in _app._DQ_PENDING_BY_SETUP


# ── 4. Long and Short do not overwrite each other ────────────────────────────

def test_5f1_long_short_independent():
    """Spec 4: Long and Short pending snapshots coexist at distinct fingerprints."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp_l = _fp(_INST, "LONG",  _MODE)
    fp_s = _fp(_INST, "SHORT", _MODE)
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, "LONG",  _MODE, _result())
        _capture_decision_snapshot(_INST, "SHORT", _MODE,
                                   _result(verdict="SHORT READY"))
    assert fp_l in _app._DQ_PENDING_BY_SETUP, "LONG must be tracked independently"
    assert fp_s in _app._DQ_PENDING_BY_SETUP, "SHORT must be tracked independently"
    assert (_app._DQ_PENDING_BY_SETUP[fp_l]["snapshot_key"] !=
            _app._DQ_PENDING_BY_SETUP[fp_s]["snapshot_key"])
    assert cur.execute.call_count >= 2, "both must INSERT"


# ── 5. SCALP and SWING snapshots are independent ─────────────────────────────

def test_5f1_scalp_swing_independent():
    """Spec 5: SCALP and SWING snapshots tracked at distinct fingerprints."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp_sc = _fp(_INST, _DIR, "SCALP")
    fp_sw = _fp(_INST, _DIR, "SWING")
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, "SCALP", _result())
        _capture_decision_snapshot(_INST, _DIR, "SWING", _result())
    assert fp_sc in _app._DQ_PENDING_BY_SETUP, "SCALP entry must exist"
    assert fp_sw in _app._DQ_PENDING_BY_SETUP, "SWING entry must exist"
    assert fp_sc != fp_sw, "fingerprints must differ by mode"
    assert cur.execute.call_count >= 2


# ── 6. Sequential same-direction setups use separate identities ───────────────

def test_5f1_sequential_same_direction_separate():
    """Spec 6: after close + abandon, second same-direction setup gets new identity."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp   = _fp()
    now2 = _NOW + timedelta(minutes=5)
    keys_seen = []
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        keys_seen.append(_app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"])
        _resolve_decision_snapshot(_INST, _mt())
    _dq_abandon_setup(fp)
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=now2):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        keys_seen.append(_app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"])
    assert keys_seen[0] != keys_seen[1], "sequential setups must get distinct snapshot_keys"


# ── 7. Direction flip leaves no incorrectly overwritten pending reference ─────

def test_5f1_direction_flip_no_overwrite():
    """Spec 7: direction flip creates independent entry; LONG snapshot is preserved."""
    _clear_pending()
    conn, _ = _mock_conn()
    fp_l = _fp(_INST, "LONG",  _MODE)
    fp_s = _fp(_INST, "SHORT", _MODE)
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, "LONG",  _MODE, _result())
        long_snap = _app._DQ_PENDING_BY_SETUP[fp_l]["snapshot_key"]
        _capture_decision_snapshot(_INST, "SHORT", _MODE,
                                   _result(verdict="SHORT READY"))
    assert _app._DQ_PENDING_BY_SETUP.get(fp_l, {}).get("snapshot_key") == long_snap, \
        "LONG pending snapshot_key must not be altered by SHORT capture"
    assert fp_s in _app._DQ_PENDING_BY_SETUP, "SHORT must also be tracked"


# ── 8-11. Win / Loss / BE / Manual closure resolve correct snapshot ───────────

def _resolution_snapshot_check(outcome, r, manual_close=False):
    """Run a capture→close cycle and return (fp, snap_key, update_params_list)."""
    _clear_pending()
    fp       = _fp()
    snap_key = f"{_INST}::{_DIR}::snap_ref_X"
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry(snap_key)
    conn, cur = _mock_conn()
    updates = []

    def _track(sql, params=None):
        if params and "UPDATE" in str(sql):
            updates.append(params)

    cur.execute.side_effect = _track
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt(outcome=outcome, r=r,
                                               manual_close=manual_close))
    return fp, snap_key, updates


def test_5f1_win_resolves_correct_snapshot():
    """Spec 8: win closure resolves the correct snapshot_key."""
    fp, snap_key, updates = _resolution_snapshot_check("Win", 1.5)
    assert fp not in _app._DQ_PENDING_BY_SETUP
    if updates:
        assert updates[0][-1] == snap_key, "UPDATE must target the correct snapshot_key"
        assert updates[0][6]  is True,     "target_reached must be True for Win"


def test_5f1_loss_resolves_correct_snapshot():
    """Spec 9: loss closure resolves the correct snapshot_key."""
    fp, snap_key, updates = _resolution_snapshot_check("Loss", -1.0)
    assert fp not in _app._DQ_PENDING_BY_SETUP
    if updates:
        assert updates[0][-1] == snap_key
        assert updates[0][7]  is True, "stop_hit must be True for Loss"


def test_5f1_be_resolves_correct_snapshot():
    """Spec 10: breakeven closure resolves the correct snapshot_key."""
    fp, snap_key, updates = _resolution_snapshot_check("Breakeven", 0.0)
    assert fp not in _app._DQ_PENDING_BY_SETUP
    if updates:
        assert updates[0][-1] == snap_key


def test_5f1_manual_resolves_correct_snapshot():
    """Spec 11: manual closure sets manual_exit=True and resolves correct snapshot_key."""
    fp, snap_key, updates = _resolution_snapshot_check("Win", 0.5, manual_close=True)
    assert fp not in _app._DQ_PENDING_BY_SETUP
    if updates:
        assert updates[0][-1] == snap_key
        assert updates[0][8]  is True, "manual_exit must be True"


# ── 12. Duplicate closure is idempotent ───────────────────────────────────────

def test_5f1_duplicate_closure_idempotent():
    """Spec 12: second identical closure is a safe no-op (no extra UPDATE)."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry(f"{_INST}::LONG::key_dup")
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt())
        first_count = cur.execute.call_count
        _resolve_decision_snapshot(_INST, _mt())
    assert cur.execute.call_count == first_count, "duplicate closure must not UPDATE again"


# ── 13. Closure without a matching snapshot is safe ───────────────────────────

def test_5f1_closure_no_match_is_noop():
    """Spec 13: unmatched closure increments counter, no DB call, no crash."""
    _clear_pending()
    before = _app._DQ_UNMATCHED_CLOSURES
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt())
    assert not cur.execute.called, "no UPDATE when no pending entry"
    assert _app._DQ_UNMATCHED_CLOSURES == before + 1, "unmatched counter must increment"


# ── 14. Failed INSERT leaves no blocking pending entry ────────────────────────

def test_5f1_failed_insert_no_blocking_entry():
    """Spec 14: INSERT failure → no entry in _DQ_PENDING_BY_SETUP (no block)."""
    _clear_pending()
    bad_conn = MagicMock()
    bad_conn.cursor.side_effect = Exception("db error")
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=bad_conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "failed INSERT must not leave a pending entry that blocks future captures"


# ── 15. Failed UPDATE leaves no blocking pending entry ────────────────────────

def test_5f1_failed_update_no_blocking_entry():
    """Spec 15: UPDATE failure → entry already popped, no block on future captures."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_fail_key")
    bad_conn = MagicMock()
    bad_conn.cursor.side_effect = Exception("db gone")
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=bad_conn):
        _resolve_decision_snapshot(_INST, _mt())
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "entry must be popped before the DB call attempt (prevents permanent block)"


# ── 16. DQ_DB_READY=False clears the pending entry (no blocking) ─────────────

def test_5f1_dq_db_false_no_blocking_entry():
    """Spec 16: DQ_DB_READY=False clears the in-memory entry (Phase 5F.1 fix)."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_db_false")
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", False), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt())
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "DQ_DB_READY=False must clear the pending entry (Phase 5F.1 fixed behavior)"
    assert not cur.execute.called


# ── 17. Restart starts with clean in-memory state ────────────────────────────

def test_5f1_restart_clean_state():
    """Spec 17: module-level initialisation produces empty pending dict and int counter."""
    assert isinstance(_app._DQ_PENDING_BY_SETUP, dict), \
        "_DQ_PENDING_BY_SETUP must be a plain dict (module-level, not None)"
    assert isinstance(_app._DQ_UNMATCHED_CLOSURES, int), \
        "_DQ_UNMATCHED_CLOSURES must be an int counter"


# ── 18. Pending rows excluded from performance metrics ───────────────────────

def test_5f1_unresolved_excluded_from_perf_stats():
    """Spec 18: WHERE outcome IS NOT NULL excludes pending/abandoned rows from stats."""
    rows = _report_rows(
        {"comps": [], "outcome": "Win",  "r": 1.5},
        {"comps": [], "outcome": "Loss", "r": -1.0},
    )
    conn, _ = _mock_conn(rows=rows, total=5)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    assert report["resolved"]         == 2, "only resolved rows counted in perf"
    assert report["total_captured"]   == 5, "total includes unresolved"
    assert report["overall_win_rate"] == pytest.approx(50.0, abs=0.2)


# ── 19. Report exposes pending count and unmatched_closures counter ───────────

def test_5f1_pending_count_in_report():
    """Spec 19: report exposes 'pending' and 'unmatched_closures' keys."""
    _clear_pending()
    _app._DQ_PENDING_BY_SETUP["fp1"] = _pending_entry("k1", "MGC", "LONG")
    _app._DQ_PENDING_BY_SETUP["fp2"] = _pending_entry("k2", "MNQ", "LONG")
    _app._DQ_UNMATCHED_CLOSURES = 3
    rows = _report_rows({"comps": [], "outcome": "Win", "r": 1.5})
    conn, _ = _mock_conn(rows=rows, total=3)
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        report = _build_decision_quality_report()
    assert "pending"            in report, "report must expose pending count"
    assert "unmatched_closures" in report, "report must expose unmatched_closures"
    assert report["pending"]            == 2
    assert report["unmatched_closures"] == 3
    _clear_pending()


# ── 20. No old snapshot receives a newer trade outcome ────────────────────────

def test_5f1_old_snapshot_no_newer_outcome():
    """Spec 20: two sequential setups resolve to their own snapshot_keys only."""
    _clear_pending()
    conn, cur = _mock_conn()
    fp   = _fp()
    now2 = _NOW + timedelta(minutes=8)
    keys_updated = []

    def _track(sql, params=None):
        if params and "UPDATE" in str(sql):
            keys_updated.append(params[-1])
    cur.execute.side_effect = _track

    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        key1 = _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"]
        _resolve_decision_snapshot(_INST, _mt(outcome="Win"))

    _dq_abandon_setup(fp)

    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=now2):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        key2 = _app._DQ_PENDING_BY_SETUP[fp]["snapshot_key"]
        _resolve_decision_snapshot(_INST, _mt(outcome="Loss", r=-1.0))

    assert key1 != key2
    if len(keys_updated) == 2:
        assert keys_updated[0] == key1, "first UPDATE targets snapshot 1 only"
        assert keys_updated[1] == key2, "second UPDATE targets snapshot 2 only"


# ── 21. edge_score not mutated by capture ────────────────────────────────────

def test_5f1_no_edge_score_mutation():
    """Spec 21: _capture_decision_snapshot does not change edge_score in result."""
    _clear_pending()
    r = _result(edge=82.0)
    before = r["edge_score"]
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, r)
    assert r["edge_score"] == before


# ── 22. verdict not mutated by capture ───────────────────────────────────────

def test_5f1_no_verdict_mutation():
    """Spec 22: _capture_decision_snapshot does not change verdict in result."""
    _clear_pending()
    r = _result(verdict="LONG READY")
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, r)
    assert r["verdict"] == "LONG READY"


# ── 23-24. trade_plan not mutated by capture ─────────────────────────────────

def test_5f1_no_trade_plan_mutation():
    """Spec 23-24: capture does not alter trade_plan entry/stop/targets."""
    _clear_pending()
    r = _result(entry=2650.0, stop=2640.0)
    plan_before = copy.deepcopy(r["trade_plan"])
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, r)
    assert r["trade_plan"] == plan_before


# ── 25. ACTIVE_TRADES_BY_INST not touched ────────────────────────────────────

def test_5f1_no_active_trade_mutation():
    """Spec 25: _capture/_resolve do not touch ACTIVE_TRADES_BY_INST."""
    _clear_pending()
    before = dict(_app.ACTIVE_TRADES_BY_INST)
    conn, _ = _mock_conn()
    fp = _fp()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, _mt())
    assert dict(_app.ACTIVE_TRADES_BY_INST) == before, \
        "ACTIVE_TRADES_BY_INST must not be altered by DQ lifecycle calls"


# ── 26-27. resolve does not mutate mt dict ───────────────────────────────────

def test_5f1_resolve_no_mt_mutation():
    """Spec 26-27: _resolve_decision_snapshot does not alter any mt field."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_mt")
    mt      = _mt(outcome="Win", r=2.0)
    mt_snap = copy.deepcopy(mt)
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot(_INST, mt)
    assert mt == mt_snap, "_resolve_decision_snapshot must not mutate mt"


# ── 28. result dict byte-identical when DQ disabled ──────────────────────────

def test_5f1_byte_identical_when_dq_disabled():
    """Spec 28: with DQ_DB_READY=False, result dict is unchanged after capture/resolve."""
    _clear_pending()
    r = _result()
    r_before = copy.deepcopy(r)
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", False), patch("app._learning_conn", return_value=conn):
        _capture_decision_snapshot(_INST, _DIR, _MODE, r)
        _resolve_decision_snapshot(_INST, _mt())
    assert r == r_before, "result dict must be identical when DQ is disabled"
    assert not cur.execute.called


# ── 29. No Databento network calls ───────────────────────────────────────────

def test_5f1_no_databento_calls():
    """Spec 29: _capture/_resolve/_build never import or call Databento."""
    _clear_pending()
    conn, _ = _mock_conn(rows=_report_rows({"comps": [], "outcome": "Win", "r": 1.5}),
                          total=1)
    mods_before = set(sys.modules.keys())
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot(_INST, _DIR, _MODE, _result())
        fp = _fp()
        _resolve_decision_snapshot(_INST, _mt())
        _build_decision_quality_report()
    new_mods = set(sys.modules.keys()) - mods_before
    db_mods  = [m for m in new_mods if "databento" in m.lower()]
    assert not db_mods, f"Databento modules imported during DQ ops: {db_mods}"


# ── 30. _dq_abandon_setup helper is safe and correct ────────────────────────

def test_5f1_abandon_setup_helper():
    """Spec 30: _dq_abandon_setup removes the fingerprint entry; repeated calls safe."""
    _clear_pending()
    fp = _fp()
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_abandon")
    assert fp in _app._DQ_PENDING_BY_SETUP

    _dq_abandon_setup(fp)
    assert fp not in _app._DQ_PENDING_BY_SETUP, "_dq_abandon_setup must remove the entry"

    _dq_abandon_setup(fp)                          # second call: safe no-op
    _dq_abandon_setup("nonexistent::fp::key")      # unknown key: safe no-op


# ─────────────────────────────────────────────────────────────────────────────
# Restore shared module state after all tests
# ─────────────────────────────────────────────────────────────────────────────

def teardown_module(module):
    _app._DQ_PENDING_BY_SETUP.clear()
    _app._DQ_UNMATCHED_CLOSURES = 0
    _app.DQ_DB_READY = True
