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
             {"start": 1001.0, "high": 111.0, "low": 99.0, "close": 108.0},
             {"start": 1002.0, "high": 104.0, "low": 98.0, "close": 101.0},
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
             {"start": 1001.0, "high": 111.0, "low": 99.0, "close": 108.0},
             # No later level hit, so the watcher must leave it open.
             {"start": 1002.0, "high": 104.0, "low": 98.0, "close": 101.0},
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
        {"start": 1001.0, "high": 111.0, "low": 99.0, "close": 108.0},
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