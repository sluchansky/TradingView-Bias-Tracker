"""Operator repair tests for terminal paper-simulation rows."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app
import pandas as pd
import pytest


def _verified_payload(bars):
    return {
        "verified": True,
        "source": app.PAPER_SIM_HISTORICAL_SOURCE,
        "bars": bars,
    }


def _bar(start, sequence, *, high=104.0, low=98.0, close=101.0):
    return {
        "instrument": "MGC",
        "start": float(start),
        "open": 100.0,
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "capture_kind": "historical_verified",
        "source": app.PAPER_SIM_HISTORICAL_SOURCE,
        "capture_session_id": "verified-backfill-a",
        "capture_session_started_at": 900.0,
        "capture_sequence": sequence,
    }


class _RepairDB:
    def __init__(self, ledger="scalp"):
        self.ledger = ledger
        self.status = "unresolved"
        self.updates = []
        self.commits = 0
        self.rollbacks = 0
        self.context = {
            "resolution": "unresolved",
            "resolution_health": {
                "status": "unresolved",
                "reason": "market_history_discontinuous",
                "age_hours": 48.0,
                "max_hold_hours": 8,
            },
        }
        self.server_bars = [
            (
                "MGC", datetime.fromtimestamp(999, tz=timezone.utc),
                100.0, 104.0, 98.0, 101.0, 0,
                app.PAPER_SIM_HISTORICAL_SOURCE, "verified-backfill-a",
                datetime.fromtimestamp(900, tz=timezone.utc), 1,
            ),
            (
                "MGC", datetime.fromtimestamp(1001, tz=timezone.utc),
                100.0, 111.0, 99.0, 108.0, 0,
                app.PAPER_SIM_HISTORICAL_SOURCE, "verified-backfill-a",
                datetime.fromtimestamp(900, tz=timezone.utc), 2,
            ),
        ]

    def connect(self):
        return _RepairConnection(self)


class _RepairConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return _RepairCursor(self.database)

    def commit(self):
        self.database.commits += 1

    def rollback(self):
        self.database.rollbacks += 1

    def close(self):
        return None


class _RepairCursor:
    def __init__(self, database):
        self.database = database
        self.rowcount = -1
        self._row = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT id, strategy_key") or \
                normalized.startswith("SELECT id, mode"):
            opened = datetime.now(timezone.utc) - timedelta(hours=48)
            closed = datetime.now(timezone.utc) - timedelta(hours=40)
            self._row = (
                7,
                "vwap_pullback_continuation"
                if self.database.ledger == "scalp" else "SCALP",
                "MGC", "Long",
                self.database.status, 100.0, 95.0, 110.0, 2.0,
                1000.0, opened, closed, dict(self.database.context),
            )
            return
        if normalized.startswith("SELECT instrument, bar_start"):
            self._rows = list(self.database.server_bars)
            return
        if normalized.startswith("UPDATE scalp_strategy_sim_trades") or \
                normalized.startswith("UPDATE dual_sim_trades"):
            assert "WHERE id=%s AND status='unresolved'" in normalized
            if self.database.status != "unresolved":
                self.rowcount = 0
                return
            self.database.updates.append((normalized, params))
            if "SET status='closed'" in normalized:
                self.database.status = "closed"
            self.rowcount = 1
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def test_historical_repair_rejects_unverified_or_wrong_instrument_bars():
    bars, reason = app._validate_verified_historical_bars({
        "source": app.PAPER_SIM_HISTORICAL_SOURCE,
        "bars": [_bar(999, 1)],
    }, "MGC")
    assert bars == []
    assert reason == "historical_bars_not_verified"

    wrong = _bar(999, 1)
    wrong["instrument"] = "MNQ"
    bars, reason = app._validate_verified_historical_bars(
        _verified_payload([wrong]), "MGC"
    )
    assert bars == []
    assert reason == "historical_bar_instrument_mismatch"

    missing_provenance = _bar(999, 1)
    missing_provenance.pop("source")
    bars, reason = app._validate_verified_historical_bars(
        _verified_payload([missing_provenance]), "MGC"
    )
    assert bars == []
    assert reason == "historical_bar_source_not_verified"


def test_unresolved_projection_exposes_reason_age_and_max_hold():
    view = app._paper_sim_unresolved_view({
        "id": 9,
        "symbol": "MGC",
        "direction": "Long",
        "opened_at": datetime.now(timezone.utc) - timedelta(hours=48),
        "closed_at": datetime.now(timezone.utc),
        "context": {
            "resolution_health": {
                "reason": "market_bars_unavailable",
                "age_hours": 48.25,
                "max_hold_hours": 8,
            },
        },
    }, "scalp", 8)
    assert view["unresolved_reason"] == "market_bars_unavailable"
    assert view["unresolved_age_hours"] == 48.25
    assert view["max_hold_hours"] == 8
    assert view["research_only"] is True


def test_fabricated_client_ohlc_cannot_resolve_without_server_verified_rows():
    database = _RepairDB()
    database.server_bars = []
    forged = _verified_payload([
        _bar(999, 1, high=9999.0, low=1.0, close=9000.0),
        _bar(1001, 2, high=9999.0, low=1.0, close=9000.0),
    ])
    with patch.object(app, "SCALP_SIM_DB_READY", True), \
         patch.object(app, "PAPER_SIM_BARS_DB_READY", True), \
         patch.object(app, "_learning_conn", side_effect=database.connect):
        result = app._paper_sim_reprocess_unresolved("scalp", 7, forged)

    assert result["ok"] is False
    assert result["status_code"] == 422
    assert result["error"] == "historical_bar_not_server_verified"
    assert database.updates == []
    assert database.status == "unresolved"


def test_databento_backfill_persists_server_verified_immutable_rows():
    frame = pd.DataFrame(
        [
            {"open": 100.0, "high": 104.0, "low": 98.0, "close": 101.0, "volume": 5},
            {"open": 101.0, "high": 111.0, "low": 99.0, "close": 108.0, "volume": 7},
        ],
        index=pd.to_datetime([999, 1001], unit="s", utc=True),
    )
    store = SimpleNamespace(to_df=lambda **_kwargs: frame)
    get_range = MagicMock(return_value=store)
    historical = SimpleNamespace(timeseries=SimpleNamespace(get_range=get_range))
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    from databento_brain import DB_SYMBOLS
    start_dt = datetime.fromtimestamp(998, tz=timezone.utc)
    session_id = "db-hist-" + app.hashlib.sha256(
        f"MGC|{DB_SYMBOLS['MGC']}|{start_dt.isoformat()}".encode("utf-8")
    ).hexdigest()[:32]
    cursor.fetchall.return_value = [
        (
            "MGC", datetime.fromtimestamp(999, tz=timezone.utc),
            100.0, 104.0, 98.0, 101.0, 5,
            app.PAPER_SIM_HISTORICAL_SOURCE, session_id, start_dt, 1,
        ),
        (
            "MGC", datetime.fromtimestamp(1001, tz=timezone.utc),
            101.0, 111.0, 99.0, 108.0, 7,
            app.PAPER_SIM_HISTORICAL_SOURCE, session_id, start_dt, 2,
        ),
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch.object(app, "PAPER_SIM_BARS_DB_READY", True), \
         patch.object(app, "_learning_conn", return_value=conn):
        batch, error = app._paper_sim_download_verified_history(
            "MGC", 998, 1002, historical_client=historical
        )

    assert error is None
    assert isinstance(batch, app._VerifiedHistoricalBatch)
    assert len(batch.bars) == 2
    assert all(
        bar["source"] == app.PAPER_SIM_HISTORICAL_SOURCE
        and bar["capture_kind"] == "historical_verified"
        for bar in batch.bars
    )
    sql, params = cursor.executemany.call_args.args
    assert "ON CONFLICT (instrument, bar_start) DO NOTHING" in sql
    assert all(row[7] == app.PAPER_SIM_HISTORICAL_SOURCE for row in params)
    conn.commit.assert_called_once()


def test_databento_backfill_fails_closed_on_persistence_conflict():
    frame = pd.DataFrame(
        [{"open": 100.0, "high": 104.0, "low": 98.0, "close": 101.0, "volume": 5}],
        index=pd.to_datetime([999], unit="s", utc=True),
    )
    store = SimpleNamespace(to_df=lambda **_kwargs: frame)
    historical = SimpleNamespace(
        timeseries=SimpleNamespace(get_range=MagicMock(return_value=store))
    )
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    cursor.fetchall.return_value = []  # Conflicting timestamp was not historical-verified.
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch.object(app, "PAPER_SIM_BARS_DB_READY", True), \
         patch.object(app, "_learning_conn", return_value=conn):
        batch, error = app._paper_sim_download_verified_history(
            "MGC", 998, 1002, historical_client=historical
        )

    assert batch == []
    assert error == "historical_backfill_persist_failed"
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_lag_truncated_history_cannot_create_an_early_expiry():
    database = _RepairDB()
    database.server_bars = [
        (
            "MGC", datetime.fromtimestamp(999, tz=timezone.utc),
            100.0, 104.0, 98.0, 101.0, 0,
            app.PAPER_SIM_HISTORICAL_SOURCE, "verified-backfill-a",
            datetime.fromtimestamp(900, tz=timezone.utc), 1,
        ),
        (
            "MGC", datetime.fromtimestamp(1001, tz=timezone.utc),
            100.0, 104.0, 98.0, 101.0, 0,
            app.PAPER_SIM_HISTORICAL_SOURCE, "verified-backfill-a",
            datetime.fromtimestamp(900, tz=timezone.utc), 2,
        ),
    ]
    payload = _verified_payload([_bar(999, 1), _bar(1001, 2)])

    with patch.object(app, "SCALP_SIM_DB_READY", True), \
         patch.object(app, "PAPER_SIM_BARS_DB_READY", True), \
         patch.object(app, "_learning_conn", side_effect=database.connect):
        result = app._paper_sim_reprocess_unresolved("scalp", 7, payload)

    assert result["ok"] is True
    assert result["processed"] is False
    assert result["status"] == "unresolved"
    assert result["reason"] == "historical_window_incomplete"
    assert database.status == "unresolved"
    assert len(database.updates) == 1
    assert "SET status='closed'" not in database.updates[0][0]


def test_server_fetched_batch_can_reprocess_without_client_supplied_ohlc():
    database = _RepairDB()
    batch = app._VerifiedHistoricalBatch([
        _bar(999, 1),
        _bar(1001, 2, high=111.0, low=99.0, close=108.0),
    ])
    prepare = MagicMock(return_value=(batch, None, 200))
    market_student_write = MagicMock()

    with patch.object(app, "SCALP_SIM_DB_READY", True), \
         patch.object(app, "PAPER_SIM_BARS_DB_READY", True), \
         patch.object(app, "_paper_sim_prepare_verified_backfill", prepare), \
         patch.object(app, "_learning_conn", side_effect=database.connect), \
         patch.object(app._MARKET_STUDENT, "record_outcome_by_source", market_student_write):
        result = app._paper_sim_reprocess_unresolved(
            "scalp", 7, {"fetch_verified_history": True}
        )

    assert result["ok"] is True
    assert result["processed"] is True
    assert result["result"] == "win"
    prepare.assert_called_once_with("scalp", 7)
    market_student_write.assert_not_called()


def test_reprocess_is_idempotent_preserves_audit_and_never_writes_learning():
    database = _RepairDB()
    payload = _verified_payload([
        _bar(999, 1),
        _bar(1001, 2, high=111.0, low=99.0, close=108.0),
    ])
    market_student_write = MagicMock()
    broker_write = MagicMock()

    with patch.object(app, "SCALP_SIM_DB_READY", True), \
         patch.object(app, "PAPER_SIM_BARS_DB_READY", True), \
         patch.object(app, "_learning_conn", side_effect=database.connect), \
         patch.object(app._MARKET_STUDENT, "record_outcome_by_source", market_student_write), \
         patch.object(app, "_send_broker_order", broker_write):
        first = app._paper_sim_reprocess_unresolved("scalp", 7, payload)
        second = app._paper_sim_reprocess_unresolved("scalp", 7, payload)

    assert first["ok"] is True
    assert first["processed"] is True
    assert first["result"] == "win"
    assert first["research_only"] is True
    assert second["ok"] is True
    assert second["processed"] is False
    assert second["idempotent"] is True
    assert len(database.updates) == 1
    update_payload = database.updates[0][1]
    context_json = update_payload[3]
    assert '"event": "unresolved_terminalized"' in context_json
    assert '"reason": "market_history_discontinuous"' in context_json
    assert '"event": "reprocess"' in context_json
    market_student_write.assert_not_called()
    broker_write.assert_not_called()


@pytest.mark.parametrize(
    "ledger, ready_name",
    [
        ("scalp", "SCALP_SIM_DB_READY"),
        ("dual", "DUAL_SIM_DB_READY"),
    ],
)
def test_reprocess_stays_out_of_live_and_managed_trade_boundaries(
    ledger, ready_name
):
    """A verified retry in either paper ledger cannot create a live trade."""
    database = _RepairDB(ledger)
    payload = _verified_payload([
        _bar(999, 1),
        _bar(1001, 2, high=111.0, low=99.0, close=108.0),
    ])

    with patch.object(app, ready_name, True), \
         patch.object(app, "PAPER_SIM_BARS_DB_READY", True), \
         patch.object(app, "_learning_conn", side_effect=database.connect), \
         patch.object(app, "_send_broker_order") as broker_send, \
         patch.object(app, "execute_trade_gateway") as gateway, \
         patch.object(app, "_execute_trade_gateway_inner") as gateway_inner, \
         patch.object(app, "_register_managed_trade") as managed_open, \
         patch.object(app, "_close_managed_trade") as managed_close, \
         patch.object(app, "_record_strategy_trade") as strategy_write, \
         patch.object(
             app._MARKET_STUDENT, "record_outcome_by_source"
         ) as learning_write:
        first = app._paper_sim_reprocess_unresolved(ledger, 7, payload)
        second = app._paper_sim_reprocess_unresolved(ledger, 7, payload)

    assert first["ok"] is True
    assert first["processed"] is True
    assert first["research_only"] is True
    assert second["ok"] is True
    assert second["idempotent"] is True
    broker_send.assert_not_called()
    gateway.assert_not_called()
    gateway_inner.assert_not_called()
    managed_open.assert_not_called()
    managed_close.assert_not_called()
    strategy_write.assert_not_called()
    learning_write.assert_not_called()