"""Regression tests for retained-bar paper simulation resolution.

These tests deliberately exercise both ledgers through the same app watcher
orchestration while stubbing only the database and market-data boundaries.
Paper simulation must remain isolated from live execution and learning writes.
"""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from unittest.mock import MagicMock, patch

import pytest

import app
import databento_brain as dbbrain
import profitability_engine as pe


def _db_for_rows(rows):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


def _row(row_id=1, opened_at=None, entry_epoch=1000.0):
    return (
        row_id, "MGC", "Long", 100.0, 95.0, 110.0, 2.0,
        entry_epoch, opened_at or datetime.now(timezone.utc), {},
    )


def _captured_bar(
    start,
    *,
    high=104.0,
    low=98.0,
    close=101.0,
    sequence=1,
    session_id="capture-a",
    session_started=900.0,
):
    return {
        "start": float(start),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "capture_session_id": session_id,
        "capture_session_started_at": float(session_started),
        "capture_sequence": int(sequence),
    }


class _ConcurrentPaperSimDB:
    """Small two-connection stand-in for PostgreSQL's conditional UPDATE."""

    def __init__(self, row, history):
        self.row = row
        self.history = history
        self.status = "open"
        self.terminal_writes = []
        self._lock = Lock()
        self.read_barrier = Barrier(2)

    def connect(self):
        return _ConcurrentPaperSimConnection(self)


class _ConcurrentPaperSimConnection:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _ConcurrentPaperSimCursor(self.database)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class _ConcurrentPaperSimCursor:
    def __init__(self, database):
        self.database = database
        self.rowcount = -1
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT id, symbol, direction"):
            self.database.read_barrier.wait(timeout=5)
            self._rows = [self.database.row]
            return
        if normalized.startswith("UPDATE "):
            assert "AND status IN ('open','resolving')" in normalized
            with self.database._lock:
                if self.database.status in {"open", "resolving"}:
                    self.database.status = params[0]
                    self.database.terminal_writes.append(params)
                    self.rowcount = 1
                else:
                    self.rowcount = 0
            return
        raise AssertionError(f"unexpected SQL in concurrency regression: {normalized}")

    def fetchall(self):
        return list(self._rows)


def test_shared_single_leg_resolver_is_stop_first_and_directionally_symmetric():
    assert pe.resolve_single_leg_paper_bar(
        "Long", bar_high=112, bar_low=94, entry=100, stop=95, target=110
    ) == ("loss", "ambiguous", 95.0, -1.0)
    assert pe.resolve_single_leg_paper_bar(
        "Short", bar_high=101, bar_low=88, entry=100, stop=105, target=90
    ) == ("win", "tp1", 90.0, 2.0)


def test_scalp_watcher_resolves_an_earlier_retained_bar():
    opened = datetime.now(timezone.utc) - timedelta(minutes=2)
    row = _row(opened_at=opened)
    conn = _db_for_rows([row])
    closed = []

    def close(*args, **kwargs):
        closed.append((args, kwargs))
        return True

    with patch.object(app, "SCALP_SIM_DB_READY", True), \
         patch.object(app, "_learning_conn", return_value=conn), \
         patch.object(app, "_fetch_completed_bars", return_value=[
             _captured_bar(1001, high=111, low=99, close=108, sequence=1),
             _captured_bar(1002, sequence=2),
         ]), \
         patch.object(app, "_scalp_sim_close", side_effect=close):
        cycle = app._watch_scalp_sim_trades()

    assert cycle["closed_rows"] == 1
    assert closed[0][0][1:4] == ("win", 110.0, 2.0)
    assert closed[0][0][4]["resolution"] == "shared_market_bar_resolver"
    assert closed[0][0][4]["bar_start"] == 1001.0


def test_dual_watcher_reuses_scalp_shared_outcome_and_same_bar_guard():
    opened = datetime.now(timezone.utc) - timedelta(minutes=2)
    row = _row(opened_at=opened, entry_epoch=1001.0)
    conn = _db_for_rows([row])
    closed = []

    with patch.object(app, "DUAL_SIM_DB_READY", True), \
         patch.object(app, "_learning_conn", return_value=conn), \
         patch.object(app, "_fetch_completed_bars", return_value=[
             # This bar would hit the target, but it is the entry bar.
             _captured_bar(1001, high=111, low=99, close=108, sequence=1),
             # No later level hit, so the watcher must leave it open.
             _captured_bar(1002, sequence=2),
         ]), \
         patch.object(app, "_dual_sim_close", side_effect=lambda *a, **k: closed.append(a) or True):
        cycle = app._watch_dual_sim_trades()

    assert cycle["closed_rows"] == 0
    assert closed == []
    assert app._dual_sim_outcome({
        "direction": "Long", "entry": 100.0, "stop": 95.0, "target": 110.0,
    }, {
        "start": 1001.0, "high": 111.0, "low": 99.0, "close": 108.0,
    }) == ("win", 110.0, 2.0)


@pytest.mark.parametrize(
    "watcher_name, ready_name, max_hold_name",
    [
        ("_watch_scalp_sim_trades", "SCALP_SIM_DB_READY", "SCALP_SIM_MAX_HOLD_HOURS"),
        ("_watch_dual_sim_trades", "DUAL_SIM_DB_READY", "DUAL_SIM_MAX_HOLD_HOURS"),
    ],
)
@pytest.mark.parametrize("unresolved", [False, True])
def test_two_instances_have_one_terminal_claim_and_no_learning_duplicate(
    watcher_name, ready_name, max_hold_name, unresolved
):
    opened = datetime.now(timezone.utc) - (
        timedelta(hours=48) if unresolved else timedelta(minutes=2)
    )
    row = _row(opened_at=opened, entry_epoch=1000.0)
    history = [] if unresolved else [
        _captured_bar(1001, high=111, low=99, close=108),
    ]
    database = _ConcurrentPaperSimDB(row, history)

    def run_watcher():
        return getattr(app, watcher_name)()

    with patch.object(app, ready_name, True), \
         patch.object(app, max_hold_name, 1), \
         patch.object(app, "_learning_conn", side_effect=database.connect), \
         patch.object(app, "_fetch_completed_bars", return_value=history), \
         patch.object(app, "_record_strategy_trade") as record_learning:
        with ThreadPoolExecutor(max_workers=2) as pool:
            cycles = list(pool.map(lambda _instance: run_watcher(), range(2)))

    terminal_key = "unresolved_rows" if unresolved else "closed_rows"
    assert sorted(cycle[terminal_key] for cycle in cycles) == [0, 1]
    assert len(database.terminal_writes) == 1
    assert database.status == ("unresolved" if unresolved else "closed")
    assert database.terminal_writes[0][1] == (
        "unresolved" if unresolved else "win"
    )
    assert not record_learning.called


@pytest.mark.parametrize("watcher_name, ready_name, close_name, max_hold_name", [
    ("_watch_scalp_sim_trades", "SCALP_SIM_DB_READY", "_scalp_sim_close",
     "SCALP_SIM_MAX_HOLD_HOURS"),
    ("_watch_dual_sim_trades", "DUAL_SIM_DB_READY", "_dual_sim_close",
     "DUAL_SIM_MAX_HOLD_HOURS"),
])
def test_stale_missing_market_data_is_explicitly_unresolved(
    watcher_name, ready_name, close_name, max_hold_name
):
    opened = datetime.now(timezone.utc) - timedelta(hours=48)
    row = _row(opened_at=opened, entry_epoch=1000.0)
    conn = _db_for_rows([row])
    marked = []

    with patch.object(app, ready_name, True), \
         patch.object(app, "_learning_conn", return_value=conn), \
         patch.object(app, "_fetch_completed_bars", return_value=[]), \
         patch.object(app, close_name, side_effect=lambda *a, **k: marked.append((a, k)) or True):
        cycle = getattr(app, watcher_name)()

    assert cycle["unresolved_rows"] == 1
    assert cycle["unresolved_reasons"] == {"market_bars_unavailable": 1}
    assert marked[0][0][1:4] == ("unresolved", None, None)
    assert marked[0][1]["status"] == "unresolved"
    assert marked[0][0][4]["resolution_health"]["reason"] == "market_bars_unavailable"
    assert marked[0][0][4]["resolution_health"]["age_hours"] >= 48


def test_retention_window_covers_both_configured_max_holds():
    assert app.PAPER_SIM_BAR_RETENTION_HOURS >= (
        max(app.SCALP_SIM_MAX_HOLD_HOURS, app.DUAL_SIM_MAX_HOLD_HOURS)
        + app.PAPER_SIM_BAR_RETENTION_BUFFER_HOURS
    )


def test_completed_bar_fetch_merges_durable_and_live_history_chronologically():
    live_store = {
        "MGC": [
            {
                **_captured_bar(1020, sequence=2),
                "ts": 1020,
                "capture_kind": "live_completed",
            },
            {
                **_captured_bar(1030, high=105, low=99, close=102, sequence=3),
                "ts": 1030,
                "capture_kind": "live_completed",
            },
        ],
    }
    durable = [
        _captured_bar(1000, high=102, low=97, close=100, sequence=1),
        _captured_bar(1020, high=103, low=97, close=99, sequence=2),
    ]

    with patch.object(dbbrain, "DATABENTO_BARS_BY_INST", live_store), \
         patch.object(app, "_fetch_persisted_completed_bars", return_value=durable):
        bars = app._fetch_completed_bars("MGC")

    assert [bar["start"] for bar in bars] == [1000.0, 1020.0, 1030.0]
    assert bars[1]["close"] == 101.0  # live overlap wins before async persist


def test_managed_trade_latest_bar_never_reads_pre_restart_durable_history():
    with patch.object(app, "_fetch_completed_bars", return_value=[]) as fetch:
        assert app._fetch_latest_bar("MGC") is None
    fetch.assert_called_once_with("MGC", include_durable=False)


def test_completed_bar_persist_is_idempotent_and_prunes_to_retention_window():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch.object(app, "PAPER_SIM_BARS_DB_READY", True), \
         patch.object(app, "_learning_conn", return_value=conn):
        persisted = app._persist_completed_paper_sim_bar("MGC", {
            "ts": 1000,
            "open": 100,
            "high": 103,
            "low": 98,
            "close": 101,
            "volume": 25,
            "capture_session_id": "capture-a",
            "capture_session_started_at": 900,
            "capture_sequence": 1,
        })

    assert persisted is True
    statements = [" ".join(call.args[0].split()) for call in cursor.execute.call_args_list]
    assert "ON CONFLICT (instrument, bar_start) DO NOTHING" in statements[0]
    assert statements[1].startswith(
        "DELETE FROM paper_sim_market_bars WHERE bar_start < now()"
    )
    conn.commit.assert_called_once()


@pytest.mark.parametrize("watcher_name, ready_name, close_name, max_hold_name", [
    ("_watch_scalp_sim_trades", "SCALP_SIM_DB_READY", "_scalp_sim_close",
     "SCALP_SIM_MAX_HOLD_HOURS"),
    ("_watch_dual_sim_trades", "DUAL_SIM_DB_READY", "_dual_sim_close",
     "DUAL_SIM_MAX_HOLD_HOURS"),
])
def test_interior_capture_gap_never_becomes_a_definitive_outcome(
    watcher_name, ready_name, close_name, max_hold_name
):
    row = _row(opened_at=datetime.now(timezone.utc) - timedelta(hours=48))
    conn = _db_for_rows([row])
    writes = []
    history = [
        _captured_bar(1001, sequence=1),
        # Sequence 2 is absent; this later target cannot prove what happened first.
        _captured_bar(1003, high=111, low=99, close=108, sequence=3),
    ]

    with patch.object(app, ready_name, True), \
         patch.object(app, max_hold_name, 1), \
         patch.object(app, "_learning_conn", return_value=conn), \
         patch.object(app, "_fetch_completed_bars", return_value=history), \
         patch.object(
             app, close_name, side_effect=lambda *a, **k: writes.append((a, k)) or True
         ):
        cycle = getattr(app, watcher_name)()

    assert cycle["closed_rows"] == 0
    assert cycle["unresolved_rows"] == 1
    assert cycle["unresolved_reasons"] == {"market_history_discontinuous": 1}
    assert writes[0][0][1:4] == ("unresolved", None, None)
    assert writes[0][0][4]["resolution_health"]["reason"] == (
        "market_history_discontinuous"
    )


@pytest.mark.parametrize("watcher_name, ready_name, close_name, max_hold_name", [
    ("_watch_scalp_sim_trades", "SCALP_SIM_DB_READY", "_scalp_sim_close",
     "SCALP_SIM_MAX_HOLD_HOURS"),
    ("_watch_dual_sim_trades", "DUAL_SIM_DB_READY", "_dual_sim_close",
     "DUAL_SIM_MAX_HOLD_HOURS"),
])
@pytest.mark.parametrize("hit_bar", [
    _captured_bar(1001, high=111, low=99, close=108, sequence=7),
    _captured_bar(1001, high=104, low=94, close=96, sequence=7),
])
def test_missing_first_post_entry_bar_never_becomes_a_definitive_outcome(
    watcher_name, ready_name, close_name, max_hold_name, hit_bar
):
    row = _row(opened_at=datetime.now(timezone.utc) - timedelta(hours=48))
    conn = _db_for_rows([row])
    writes = []
    history = [
        _captured_bar(999, sequence=5),
        # Sequence 6 was the first post-entry bar and is missing.
        hit_bar,
    ]

    with patch.object(app, ready_name, True), \
         patch.object(app, max_hold_name, 1), \
         patch.object(app, "_learning_conn", return_value=conn), \
         patch.object(app, "_fetch_completed_bars", return_value=history), \
         patch.object(
             app, close_name, side_effect=lambda *a, **k: writes.append((a, k)) or True
         ):
        cycle = getattr(app, watcher_name)()

    assert cycle["closed_rows"] == 0
    assert cycle["unresolved_rows"] == 1
    assert cycle["unresolved_reasons"] == {
        "market_history_entry_boundary_discontinuous": 1
    }
    assert writes[0][0][1:4] == ("unresolved", None, None)


def test_missing_pre_entry_boundary_is_explicitly_untrusted():
    trusted, reason = app._paper_sim_trusted_history(
        {"entry_epoch": 1000.0},
        [_captured_bar(1001, sequence=2)],
    )
    assert trusted == []
    assert reason == "market_history_entry_boundary_unavailable"


@pytest.mark.parametrize("watcher_name, ready_name, close_name", [
    ("_watch_scalp_sim_trades", "SCALP_SIM_DB_READY", "_scalp_sim_close"),
    ("_watch_dual_sim_trades", "DUAL_SIM_DB_READY", "_dual_sim_close"),
])
def test_outcome_before_a_later_capture_gap_remains_provable(
    watcher_name, ready_name, close_name
):
    row = _row(opened_at=datetime.now(timezone.utc) - timedelta(minutes=2))
    conn = _db_for_rows([row])
    writes = []
    history = [
        _captured_bar(1001, high=111, low=99, close=108, sequence=1),
        _captured_bar(1003, sequence=3),
    ]

    with patch.object(app, ready_name, True), \
         patch.object(app, "_learning_conn", return_value=conn), \
         patch.object(app, "_fetch_completed_bars", return_value=history), \
         patch.object(
             app, close_name, side_effect=lambda *a, **k: writes.append((a, k)) or True
         ):
        cycle = getattr(app, watcher_name)()

    assert cycle["closed_rows"] == 1
    assert cycle["unresolved_rows"] == 0
    assert writes[0][0][1:4] == ("win", 110.0, 2.0)


def test_in_memory_historical_replay_is_not_paper_resolution_history():
    replay_store = {
        "MGC": [{
            "ts": 1000,
            "high": 111,
            "low": 99,
            "close": 108,
            "capture_kind": "historical_replay",
        }],
    }
    with patch.object(dbbrain, "DATABENTO_BARS_BY_INST", replay_store), \
         patch.object(app, "_fetch_persisted_completed_bars", return_value=[]):
        assert app._fetch_completed_bars("MGC") == []


def test_databento_completed_bar_callback_excludes_historical_replay():
    brain = dbbrain.DatabentoBrain(
        alert_history=[],
        cvd_by_ticker={},
        rvol_by_ticker={},
        auto_price_by_ticker={},
        current_price_by_ticker={},
        current_price_ts_by_ticker={},
        volume_spike_by_ticker={},
        volatility_by_ticker={},
        vwap_by_ticker={},
    )
    received = []
    brain.register_completed_bar_callback(
        lambda instrument, bar: received.append((instrument, bar))
    )
    dbbrain.DATABENTO_BARS_BY_INST["MGC"].clear()
    replay_bar = {
        "ts": 1000.0, "open": 100.0, "high": 102.0, "low": 99.0,
        "close": 101.0, "volume": 10, "buy_volume": 6, "sell_volume": 4,
    }
    live_bar = {
        **replay_bar, "ts": 1060.0, "open": 101.0, "high": 103.0,
        "low": 100.0, "close": 102.0,
    }
    try:
        brain._on_bar_close("MGC", replay_bar, replay=True)
        assert received == []
        brain._on_bar_close("MGC", live_bar, replay=False)
        assert len(received) == 1
        assert received[0][0] == "MGC"
        assert received[0][1]["ts"] == 1060.0
    finally:
        dbbrain.DATABENTO_BARS_BY_INST["MGC"].clear()