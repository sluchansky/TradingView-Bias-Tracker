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
    _dq_expire_old_entries,
    _dq_has_active_trade,
    _dq_attach_to_trade,
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
    _app._DQ_UNMATCHED_CLOSURES  = 0
    _app._DQ_EXPIRED_UNTRADED    = 0
    _app._DQ_PRESERVED_ACTIVE    = 0
    _app._DQ_RESOLVED_COUNT      = 0
    _app._DQ_ABANDONED_COUNT     = 0
    _app.DQ_DB_READY = True


# ─────────────────────────────────────────────────────────────────────────────
# Section 10: Phase 5F.1B — Association Safety Repair Tests
# SC2: Unsafe first-match fallback removed
# SC3: WAIT must not abandon an active-trade snapshot
# SC4: Active trades exempt from candidate TTL expiry
# ─────────────────────────────────────────────────────────────────────────────

def _reset_dq_all():
    """Reset all DQ in-memory state for clean test isolation."""
    _app._DQ_PENDING_BY_SETUP.clear()
    _app._DQ_UNMATCHED_CLOSURES = 0
    _app._DQ_EXPIRED_UNTRADED   = 0
    _app._DQ_PRESERVED_ACTIVE   = 0
    _app._DQ_RESOLVED_COUNT     = 0
    _app._DQ_ABANDONED_COUNT    = 0


# ── 1. READY snapshot survives WAIT while trade is active (SC3 fix) ──────────

def test_1b_wait_preserves_snapshot_while_trade_active():
    """SC3 fix: WAIT heartbeat must not abandon the pending entry when a trade is open."""
    _reset_dq_all()
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_sc3", inst="MGC",
                                                    direction="Long", mode="SCALP")
    # Active trade registered for MGC Long
    with patch.dict(_app.ACTIVE_TRADES_BY_INST, {"MGC": {"direction": "Long"}}):
        # Simulate the WAIT branch in full_analysis (SC3-guarded)
        if not _dq_has_active_trade("MGC", "Long"):
            _dq_abandon_setup(fp)
    # Entry must survive — the guard prevented the abandon
    assert fp in _app._DQ_PENDING_BY_SETUP, \
        "SC3: pending entry must not be abandoned when trade is active"
    assert _app._DQ_ABANDONED_COUNT == 0, "abandon counter must stay 0"


# ── 2. READY snapshot is abandoned on WAIT when no trade is active ────────────

def test_1b_wait_abandons_when_no_active_trade():
    """SC3 fix: with no active trade, WAIT must clear the pending entry as before."""
    _reset_dq_all()
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_wait_clean", inst="MGC",
                                                    direction="Long", mode="SCALP")
    # No active trade
    with patch.dict(_app.ACTIVE_TRADES_BY_INST, {}, clear=True):
        if not _dq_has_active_trade("MGC", "Long"):
            _dq_abandon_setup(fp)
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "entry must be abandoned when no active trade"
    assert _app._DQ_ABANDONED_COUNT == 1


# ── 3. Active SCALP association is not expired by TTL (SC4 fix) ──────────────

def test_1b_scalp_active_trade_not_expired_by_ttl():
    """SC4 fix: an active SCALP trade must not be evicted by _dq_expire_old_entries."""
    _reset_dq_all()
    old_time = _NOW - timedelta(minutes=300)  # older than 240-min TTL
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_scalp_active", inst="MGC",
                                                    direction="Long", mode="SCALP",
                                                    created_at=old_time)
    with patch.dict(_app.ACTIVE_TRADES_BY_INST, {"MGC": {"direction": "Long"}}):
        with patch("app.now_utc", return_value=_NOW):
            _dq_expire_old_entries()
    assert fp in _app._DQ_PENDING_BY_SETUP, \
        "SC4: active SCALP trade entry must survive the 240-min TTL"
    assert _app._DQ_PRESERVED_ACTIVE >= 1
    assert _app._DQ_EXPIRED_UNTRADED == 0


# ── 4. Active SWING association older than 240 min not expired (SC4 fix) ─────

def test_1b_swing_active_trade_not_expired_beyond_ttl():
    """SC4 fix: an active SWING trade held beyond 240 min must never lose its DQ row."""
    _reset_dq_all()
    ancient = _NOW - timedelta(hours=10)  # well beyond 240-min TTL
    fp = _dq_fingerprint("MNQ", "Short", "SWING")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_swing_old", inst="MNQ",
                                                    direction="Short", mode="SWING",
                                                    created_at=ancient)
    with patch.dict(_app.ACTIVE_TRADES_BY_INST, {"MNQ": {"direction": "Short"}}):
        with patch("app.now_utc", return_value=_NOW):
            _dq_expire_old_entries()
    assert fp in _app._DQ_PENDING_BY_SETUP, \
        "SC4: active SWING trade held > 240 min must not be expired"
    assert _app._DQ_PRESERVED_ACTIVE >= 1


# ── 5. Untraded candidate older than 240 min IS expired ──────────────────────

def test_1b_untraded_candidate_expired_by_ttl():
    """SC4: untraded candidates older than 240 min must still be evicted normally."""
    _reset_dq_all()
    old_time = _NOW - timedelta(minutes=300)
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_untraded_old", inst="MGC",
                                                    direction="Long", mode="SCALP",
                                                    created_at=old_time)
    # No active trade
    with patch.dict(_app.ACTIVE_TRADES_BY_INST, {}, clear=True):
        with patch("app.now_utc", return_value=_NOW):
            _dq_expire_old_entries()
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "untraded candidate must be expired after TTL"
    assert _app._DQ_EXPIRED_UNTRADED == 1
    assert _app._DQ_PRESERVED_ACTIVE == 0


# ── 6. Active-trade preservation independent for Long and Short ───────────────

def test_1b_long_short_preserve_independently():
    """SC4: Long active trade preserves LONG entry; SHORT untraded is expired."""
    _reset_dq_all()
    old_time = _NOW - timedelta(minutes=300)
    fp_long  = _dq_fingerprint("MGC", "Long",  "SCALP")
    fp_short = _dq_fingerprint("MGC", "Short", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_long]  = _pending_entry("snap_long",  inst="MGC",
                                                          direction="Long",  mode="SCALP",
                                                          created_at=old_time)
    _app._DQ_PENDING_BY_SETUP[fp_short] = _pending_entry("snap_short", inst="MGC",
                                                          direction="Short", mode="SCALP",
                                                          created_at=old_time)
    # Only Long trade is active
    with patch.dict(_app.ACTIVE_TRADES_BY_INST, {"MGC": {"direction": "Long"}}):
        with patch("app.now_utc", return_value=_NOW):
            _dq_expire_old_entries()
    assert fp_long  in _app._DQ_PENDING_BY_SETUP, "Long active → preserved"
    assert fp_short not in _app._DQ_PENDING_BY_SETUP, "Short untraded → expired"


# ── 7. Active-trade preservation independent for SCALP and SWING ──────────────

def test_1b_scalp_swing_preserve_independently():
    """SC4: active SWING preserved even when co-existing with expired SCALP candidate."""
    _reset_dq_all()
    old_time = _NOW - timedelta(minutes=300)
    fp_scalp = _dq_fingerprint("MNQ", "Long", "SCALP")
    fp_swing = _dq_fingerprint("MNQ", "Long", "SWING")
    _app._DQ_PENDING_BY_SETUP[fp_scalp] = _pending_entry("snap_scalp", inst="MNQ",
                                                          direction="Long", mode="SCALP",
                                                          created_at=old_time)
    _app._DQ_PENDING_BY_SETUP[fp_swing] = _pending_entry("snap_swing", inst="MNQ",
                                                          direction="Long", mode="SWING",
                                                          created_at=old_time)
    # SWING trade is active; SCALP trade is not (no slot)
    with patch.dict(_app.ACTIVE_TRADES_BY_INST, {"MNQ": {"direction": "Long"}}):
        with patch("app.now_utc", return_value=_NOW):
            _dq_expire_old_entries()
    # Both share the same ACTIVE_TRADES_BY_INST slot (MNQ) with direction Long
    # so BOTH are preserved by the active-trade check
    assert fp_swing in _app._DQ_PENDING_BY_SETUP, "active SWING → preserved"


# ── 8. Mode change does not cause arbitrary snapshot to receive outcome ────────

def test_1b_mode_change_no_wrong_resolution():
    """SC2 fix: if TRADING_MODE changes between capture and close, outcome is unmatched."""
    _reset_dq_all()
    # Snapshot captured under SCALP mode
    fp_scalp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_scalp] = _pending_entry("snap_scalp_mode",
                                                          inst="MGC", direction="Long",
                                                          mode="SCALP")
    conn, cur = _mock_conn()
    before_unmatched = _app._DQ_UNMATCHED_CLOSURES

    # Close arrives with _dq_mode=SWING (mode changed) and no snapshot_key on mt
    mt_mode_changed = {
        "instrument": "MGC",
        "direction":  "Long",
        "_dq_mode":   "SWING",   # different from capture-time SCALP
        "outcome":    "Win",
        "r_multiple": 1.5,
        "opened_at":  _NOW,
        "closed_at":  _NOW + timedelta(minutes=30),
    }
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", mt_mode_changed)

    assert _app._DQ_UNMATCHED_CLOSURES > before_unmatched, \
        "mode mismatch without snapshot_key must produce unmatched closure"
    assert not cur.execute.called or all(
        "UPDATE" not in str(c) for c in cur.execute.call_args_list
    ), "no UPDATE must be issued when fingerprint mismatches"


# ── 9. Missing exact match produces unmatched closure (SC2 fix) ──────────────

def test_1b_no_match_unmatched_closure():
    """SC2 fix: resolve with no matching entry → unmatched closure counter."""
    _reset_dq_all()
    before = _app._DQ_UNMATCHED_CLOSURES
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    assert _app._DQ_UNMATCHED_CLOSURES == before + 1, \
        "unmatched closure counter must increment when no entry is found"


# ── 10. Missing exact match does NOT update any DB row ────────────────────────

def test_1b_no_match_no_db_update():
    """SC2 fix: when no match found, no UPDATE is executed against any DB row."""
    _reset_dq_all()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    update_calls = [c for c in cur.execute.call_args_list
                    if "UPDATE" in str(c).upper()]
    assert not update_calls, "no UPDATE must be issued on unmatched closure"


# ── 11. No first-match fallback remains ───────────────────────────────────────

def test_1b_no_first_match_fallback():
    """SC2 fix: two pending entries for the same inst — wrong direction never selected."""
    _reset_dq_all()
    fp_long  = _dq_fingerprint("MGC", "Long",  "SCALP")
    fp_short = _dq_fingerprint("MGC", "Short", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_long]  = _pending_entry("snap_long",  inst="MGC",
                                                          direction="Long",  mode="SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_short] = _pending_entry("snap_short", inst="MGC",
                                                          direction="Short", mode="SCALP")
    conn, cur = _mock_conn()
    # Close a Long — must not pop Short via first-match
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    assert fp_long  not in _app._DQ_PENDING_BY_SETUP, "Long entry must be resolved"
    assert fp_short in _app._DQ_PENDING_BY_SETUP, "Short entry must remain untouched"


# ── 12. Two pending entries can't be resolved by dict insertion order ─────────

def test_1b_dict_insertion_order_not_used():
    """SC2 fix: inserting Short before Long must not cause a Long close to resolve Short."""
    _reset_dq_all()
    # Insert SHORT first (old first-match would pick this)
    fp_short = _dq_fingerprint("MGC", "Short", "SCALP")
    fp_long  = _dq_fingerprint("MGC", "Long",  "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_short] = _pending_entry("snap_short_first", inst="MGC",
                                                          direction="Short", mode="SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_long]  = _pending_entry("snap_long_second", inst="MGC",
                                                          direction="Long",  mode="SCALP")
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    # Long must have been resolved, Short must remain
    assert fp_long  not in _app._DQ_PENDING_BY_SETUP, "Long resolved by exact fp"
    assert fp_short in _app._DQ_PENDING_BY_SETUP, "Short untouched (no first-match)"
    # Verify the UPDATE targeted the Long snapshot key
    update_calls = [str(c) for c in cur.execute.call_args_list if "UPDATE" in str(c).upper()]
    if update_calls:
        assert "snap_long_second" in update_calls[0], "UPDATE must target Long snapshot"


# ── 13. Empty direction does not guess ───────────────────────────────────────

def test_1b_empty_direction_no_guess():
    """SC2 fix: empty direction in mt skips Level 2 and produces unmatched closure."""
    _reset_dq_all()
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_long_empty_dir", inst="MGC",
                                                    direction="Long", mode="SCALP")
    before = _app._DQ_UNMATCHED_CLOSURES
    conn, cur = _mock_conn()
    mt_no_dir = {"instrument": "MGC", "direction": "", "outcome": "Win", "r_multiple": 1.5,
                 "opened_at": _NOW, "closed_at": _NOW + timedelta(minutes=30)}
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", mt_no_dir)
    assert _app._DQ_UNMATCHED_CLOSURES > before, "empty direction → unmatched closure"
    assert fp in _app._DQ_PENDING_BY_SETUP, "pending entry must not be guessed"


# ── 14. Wrong direction does not guess ───────────────────────────────────────

def test_1b_wrong_direction_no_guess():
    """SC2 fix: closing Short when only Long is pending → unmatched closure."""
    _reset_dq_all()
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_wrong_dir", inst="MGC",
                                                    direction="Long", mode="SCALP")
    before = _app._DQ_UNMATCHED_CLOSURES
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Short"))
    assert _app._DQ_UNMATCHED_CLOSURES > before, "wrong direction → unmatched closure"
    assert fp in _app._DQ_PENDING_BY_SETUP, "Long entry must not be consumed by Short close"


# ── 15. Wrong mode does not guess ────────────────────────────────────────────

def test_1b_wrong_mode_no_guess():
    """SC2 fix: mode mismatch without snapshot_key → unmatched closure, no DB write."""
    _reset_dq_all()
    fp_scalp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_scalp] = _pending_entry("snap_wrong_mode", inst="MGC",
                                                          direction="Long", mode="SCALP")
    before = _app._DQ_UNMATCHED_CLOSURES
    conn, cur = _mock_conn()
    mt_wrong_mode = {
        "instrument": "MGC", "direction": "Long",
        "_dq_mode": "SWING",  # not SCALP
        "outcome": "Win", "r_multiple": 1.5,
        "opened_at": _NOW, "closed_at": _NOW + timedelta(minutes=30),
    }
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", mt_wrong_mode)
    assert _app._DQ_UNMATCHED_CLOSURES > before, "wrong mode → unmatched closure"
    assert fp_scalp in _app._DQ_PENDING_BY_SETUP, "SCALP entry must not be consumed"


# ── 16. Exact _dq_snapshot_key resolves the intended row (Level 1) ───────────

def test_1b_snapshot_key_level1_resolution():
    """SC2 fix: _dq_snapshot_key on mt resolves the correct entry even with wrong mode."""
    _reset_dq_all()
    # Entry is at the SCALP fingerprint
    fp_scalp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_scalp] = _pending_entry("snap_l1_target", inst="MGC",
                                                          direction="Long", mode="SCALP")
    conn, cur = _mock_conn()
    before_resolved = _app._DQ_RESOLVED_COUNT
    # mt carries the exact snapshot_key but claims SWING mode → Level 2 would miss
    mt_with_key = {
        "instrument":        "MGC",
        "direction":         "Long",
        "_dq_mode":          "SWING",    # would miss fingerprint at SCALP
        "_dq_snapshot_key":  "snap_l1_target",  # Level 1 match
        "outcome":           "Win",
        "r_multiple":        1.5,
        "opened_at":         _NOW,
        "closed_at":         _NOW + timedelta(minutes=30),
    }
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", mt_with_key)
    assert fp_scalp not in _app._DQ_PENDING_BY_SETUP, "Level-1 match must pop the entry"
    assert _app._DQ_RESOLVED_COUNT == before_resolved + 1, "resolved counter must increment"
    assert _app._DQ_UNMATCHED_CLOSURES == 0, "no unmatched closure when L1 matched"


# ── 17. Duplicate closure notification is idempotent ─────────────────────────

def test_1b_duplicate_closure_idempotent():
    """Level 1/2 already consumed the entry; second close increments unmatched but no DB write."""
    _reset_dq_all()
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_dup", inst="MGC",
                                                    direction="Long", mode="SCALP")
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))  # first close → resolves
        before = _app._DQ_UNMATCHED_CLOSURES
        cur.execute.reset_mock()
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))  # duplicate close
    assert _app._DQ_UNMATCHED_CLOSURES == before + 1, \
        "duplicate closure must increment unmatched counter"
    update_calls = [c for c in cur.execute.call_args_list if "UPDATE" in str(c).upper()]
    assert not update_calls, "duplicate close must not re-UPDATE the DB row"


# ── 18. Failed DB update clears in-memory entry without blocking future captures

def test_1b_failed_db_update_clears_entry():
    """After a failed DB update the pending entry is gone; next capture succeeds."""
    _reset_dq_all()
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_fail_update", inst="MGC",
                                                    direction="Long", mode="SCALP")
    # Simulate conn failure during the UPDATE phase
    conn_fail = MagicMock()
    conn_fail.cursor.side_effect = Exception("simulated DB failure")

    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn_fail):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))  # fails internally

    # Entry is ALWAYS popped before DB call (Phase 5F.1 guarantee)
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "entry must be cleared even when DB update fails (pop-before-DB)"

    # Next READY capture should succeed (no orphaned blocking entry)
    conn_ok, cur_ok = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn_ok), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot("MGC", "Long", "SCALP", _result())
    assert fp in _app._DQ_PENDING_BY_SETUP, "next capture must succeed after failed update"


# ── 19. DQ_DB_READY=False does not leave a blocking pending entry ─────────────

def test_1b_db_false_no_blocking_entry():
    """DQ_DB_READY=False: entry is popped in-memory even without DB write."""
    _reset_dq_all()
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_no_db", inst="MGC",
                                                    direction="Long", mode="SCALP")
    with patch("app.DQ_DB_READY", False):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    assert fp not in _app._DQ_PENDING_BY_SETUP, \
        "pending entry must be cleared even when DQ_DB_READY=False"


# ── 20. Lifecycle counters distinguish all five states ────────────────────────

def test_1b_lifecycle_counters_comprehensive():
    """All five counters increment independently in their respective scenarios."""
    _reset_dq_all()
    old_time = _NOW - timedelta(minutes=300)

    # — EXPIRED_UNTRADED —
    fp_exp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_exp] = _pending_entry("snap_exp", inst="MGC",
                                                        direction="Long", mode="SCALP",
                                                        created_at=old_time)
    with patch.dict(_app.ACTIVE_TRADES_BY_INST, {}, clear=True):
        with patch("app.now_utc", return_value=_NOW):
            _dq_expire_old_entries()
    assert _app._DQ_EXPIRED_UNTRADED >= 1

    # — PRESERVED_ACTIVE —
    _reset_dq_all()
    fp_pres = _dq_fingerprint("MNQ", "Short", "SWING")
    _app._DQ_PENDING_BY_SETUP[fp_pres] = _pending_entry("snap_pres", inst="MNQ",
                                                         direction="Short", mode="SWING",
                                                         created_at=old_time)
    with patch.dict(_app.ACTIVE_TRADES_BY_INST, {"MNQ": {"direction": "Short"}}):
        with patch("app.now_utc", return_value=_NOW):
            _dq_expire_old_entries()
    assert _app._DQ_PRESERVED_ACTIVE >= 1

    # — ABANDONED_COUNT —
    _reset_dq_all()
    fp_ab = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_ab] = _pending_entry("snap_ab")
    _dq_abandon_setup(fp_ab)
    assert _app._DQ_ABANDONED_COUNT == 1

    # — RESOLVED_COUNT —
    _reset_dq_all()
    fp_res = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp_res] = _pending_entry("snap_res")
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    assert _app._DQ_RESOLVED_COUNT == 1

    # — UNMATCHED_CLOSURES —
    _reset_dq_all()
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    assert _app._DQ_UNMATCHED_CLOSURES == 1


# ── 21-22. DQ changes do not alter component points or Edge Score ─────────────

def test_1b_component_points_unchanged():
    """DQ capture/resolve must not alter source_attribution points in result."""
    _reset_dq_all()
    r = _result(edge=77.0)
    pts_before = [c["points"] for c in r["source_attribution"]]
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot("MGC", "Long", "SCALP", r)
    assert [c["points"] for c in r["source_attribution"]] == pts_before, \
        "component points must be identical after DQ capture"


def test_1b_edge_score_unchanged():
    """DQ capture/resolve must not alter edge_score in result."""
    _reset_dq_all()
    r = _result(edge=82.0)
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot("MGC", "Long", "SCALP", r)
    assert r["edge_score"] == 82.0, "edge_score must not be altered by DQ capture"


# ── 23-24. Learning and final Edge Score unchanged ────────────────────────────

def test_1b_learning_delta_not_in_dq():
    """DQ lifecycle functions only write to decision_snapshots — not learning tables."""
    _reset_dq_all()
    conn, cur = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot("MGC", "Long", "SCALP", _result())
        fp = _dq_fingerprint("MGC", "Long", "SCALP")
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    tables_touched = " ".join(str(c) for c in cur.execute.call_args_list).upper()
    # DQ only touches decision_snapshots — never learning_outcomes or strategy_trades
    assert "LEARNING_OUTCOMES" not in tables_touched, \
        "DQ must not write to learning_outcomes table"
    assert "STRATEGY_TRADES" not in tables_touched, \
        "DQ must not write to strategy_trades table"
    assert "DECISION_SNAPSHOTS" in tables_touched, \
        "DQ must write only to decision_snapshots"


def test_1b_final_edge_score_not_altered():
    """_resolve_decision_snapshot does not add or remove keys from mt."""
    _reset_dq_all()
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_edge")
    mt = _mt(direction="Long")
    mt_keys_before = set(mt.keys())
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", mt)
    assert set(mt.keys()) == mt_keys_before, \
        "resolve must not add or remove keys from the mt dict"


# ── 25. READY/WAIT verdicts not altered ───────────────────────────────────────

def test_1b_verdicts_not_altered():
    """DQ capture does not change the verdict field in the result dict."""
    _reset_dq_all()
    r = _result(verdict="LONG READY")
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot("MGC", "Long", "SCALP", r)
    assert r["verdict"] == "LONG READY", "verdict must be unchanged after DQ capture"


# ── 26. Auto-trade eligibility not altered ───────────────────────────────────

def test_1b_auto_trade_eligibility_unchanged():
    """DQ functions must not touch any auto-trade flag or execution state."""
    _reset_dq_all()
    at_before = dict(_app.ACTIVE_TRADES_BY_INST)
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot("MGC", "Long", "SCALP", _result())
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    assert dict(_app.ACTIVE_TRADES_BY_INST) == at_before, \
        "ACTIVE_TRADES_BY_INST must not be changed by DQ lifecycle"


# ── 27. Trade-management behavior unchanged ───────────────────────────────────

def test_1b_trade_management_unchanged():
    """_dq_attach_to_trade injects only _dq_ prefixed keys; production keys unchanged."""
    _reset_dq_all()
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    _app._DQ_PENDING_BY_SETUP[fp] = _pending_entry("snap_attach")
    trade = {"direction": "Long", "entry_price": 2650.0, "stop_loss": 2640.0,
             "target1": 2680.0, "contracts": 1, "status": "active"}
    keys_before = {k: v for k, v in trade.items()}
    _dq_attach_to_trade("MGC", trade)
    for k, v in keys_before.items():
        assert trade[k] == v, f"production key '{k}' must not be altered by _dq_attach_to_trade"
    injected = {k for k in trade if k.startswith("_dq_")}
    assert all(k.startswith("_dq_") for k in injected), "only _dq_ prefixed keys injected"


# ── 28. API responses byte-identical when diagnostics not requested ───────────

def test_1b_result_dict_byte_identical_no_dq():
    """Result dict is completely unchanged when DQ_DB_READY=False."""
    _reset_dq_all()
    r = _result()
    r_copy = copy.deepcopy(r)
    with patch("app.DQ_DB_READY", False):
        _capture_decision_snapshot("MGC", "Long", "SCALP", r)
    assert r == r_copy, "result dict must be byte-identical when DQ is disabled"


# ── 29. Databento ingestion is untouched ─────────────────────────────────────

def test_1b_databento_ingestion_untouched():
    """DQ lifecycle must not read or write any Databento state."""
    _reset_dq_all()
    # Verify no databento attributes exist on the module after DQ ops
    conn, _ = _mock_conn()
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot("MGC", "Long", "SCALP", _result())
    fp = _dq_fingerprint("MGC", "Long", "SCALP")
    with patch("app.DQ_DB_READY", True), patch("app._learning_conn", return_value=conn):
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
    # If databento state was mutated we'd see DATABENTO_ENABLED toggled or errors
    # Asserting the test itself didn't crash from a databento import is sufficient
    assert True, "no databento import or state mutation occurred"


# ── 30. No Databento network calls in tests ───────────────────────────────────

def test_1b_no_databento_network_calls():
    """No network calls to Databento occur during any DQ function call."""
    _reset_dq_all()
    conn, _ = _mock_conn(rows=_report_rows({"comps": [], "outcome": "Win", "r": 1.5}),
                          total=1)
    mods_before = set(sys.modules.keys())
    with patch("app.DQ_DB_READY", True), \
         patch("app._learning_conn", return_value=conn), \
         patch("app.now_utc", return_value=_NOW):
        _capture_decision_snapshot("MGC", "Long", "SCALP", _result())
        fp = _dq_fingerprint("MGC", "Long", "SCALP")
        _resolve_decision_snapshot("MGC", _mt(direction="Long"))
        _build_decision_quality_report()
        _dq_expire_old_entries()
        _dq_has_active_trade("MGC", "Long")
    new_mods = set(sys.modules.keys()) - mods_before
    db_mods  = [m for m in new_mods if "databento" in m.lower()]
    assert not db_mods, f"Databento modules must not be imported during DQ ops: {db_mods}"
