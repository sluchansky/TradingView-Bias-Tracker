"""Native structure startup warm-up tests (no network or live feed)."""
from __future__ import annotations

import sys
import time
import types
from collections import deque
from datetime import datetime, timezone

_db = types.ModuleType("databento")
sys.modules.setdefault("databento", _db)

import databento_brain as dbbrain  # noqa: E402
from databento_brain import (  # noqa: E402
    DB_SYMBOLS,
    DATABENTO_BARS_BY_INST,
    DATABENTO_STATUS,
    STRUCTURE_WARMUP_BARS,
    STRUCTURE_WARMUP_MAX_AGE_HOURS,
    DatabentoBrain,
    get_databento_status_snapshot,
)


def brain():
    return DatabentoBrain(
        alert_history=deque(maxlen=500), cvd_by_ticker={}, rvol_by_ticker={},
        auto_price_by_ticker={}, current_price_by_ticker={},
        current_price_ts_by_ticker={}, volume_spike_by_ticker={},
    )


def bars(count=STRUCTURE_WARMUP_BARS, *, newest_age_s=7200):
    latest = int(time.time() - newest_age_s)
    start = latest - (count - 1) * 60
    return [
        {
            "ts": start + i * 60, "open": 100 + i, "high": 101 + i,
            "low": 99 + i, "close": 100.5 + i, "volume": 10,
            "buy_volume": 0, "sell_volume": 0,
        }
        for i in range(count)
    ]


def test_warmup_accepts_real_closed_ordered_bounded_history():
    candidate, reason = brain()._validated_history(bars(120), time.time())
    assert reason is None
    assert len(candidate) == STRUCTURE_WARMUP_BARS
    assert all(a["ts"] < b["ts"] for a, b in zip(candidate, candidate[1:]))


def test_warmup_rejects_future_non_monotonic_and_insufficient_history():
    now = time.time()
    future = bars()
    future[-1]["ts"] = now + 60
    assert brain()._validated_history(future, now)[1] == "malformed_or_non_monotonic_history"

    unordered = bars()
    unordered[20], unordered[21] = unordered[21], unordered[20]
    assert brain()._validated_history(unordered, now)[1] == "malformed_or_non_monotonic_history"

    _, reason = brain()._validated_history(bars(STRUCTURE_WARMUP_BARS - 1), now)
    assert reason == "insufficient_closed_history"

    stale = bars(newest_age_s=STRUCTURE_WARMUP_MAX_AGE_HOURS * 3600 + 1)
    assert brain()._validated_history(stale, now)[1] == "history_too_stale"

    malformed = bars()
    malformed[10]["close"] = malformed[10]["high"] + 1
    assert brain()._validated_history(malformed, now)[1] == "malformed_or_non_monotonic_history"


def test_replay_reconstructs_only_native_state_and_never_emits_live_signals(monkeypatch):
    volatility = {}
    b = DatabentoBrain(
        alert_history=deque(maxlen=500), cvd_by_ticker={}, rvol_by_ticker={},
        auto_price_by_ticker={}, current_price_by_ticker={},
        current_price_ts_by_ticker={}, volume_spike_by_ticker={},
        volatility_by_ticker=volatility,
    )
    received = []
    b.register_bar_close_callback(lambda *_: received.append("bar"))
    structure_signals = []
    b.register_structure_signal_callback(lambda *args: structure_signals.append(args))
    # Force a detector-level BOS emission during replay. The suppression guard
    # must keep it out of ALERT_HISTORY and prevent normal analysis fan-out.
    monkeypatch.setattr(
        b, "_detect_structure",
        lambda inst, _bars: b._inject_alert(inst, "BOS DEMAND", _bars[-1]["close"]),
    )
    before_mnq = len(DATABENTO_BARS_BY_INST["MNQ"])
    b._suppress_replay_signals = True
    try:
        for bar in bars():
            b._on_bar_close("MGC", bar, replay=True)
    finally:
        b._suppress_replay_signals = False

    assert len(b._bars["MGC"]) == STRUCTURE_WARMUP_BARS
    assert not b._bars["MNQ"]
    assert len(DATABENTO_BARS_BY_INST["MNQ"]) == before_mnq
    assert not received
    assert not structure_signals
    assert not b._ah
    assert volatility["MGC"]["ts"] == datetime.fromtimestamp(
        bars()[-1]["ts"], tz=timezone.utc
    ).isoformat()


def test_warmup_status_is_a_json_safe_per_instrument_operator_surface():
    b = brain()
    b._set_warmup_state("MGC", "WARMING_UP", reason="fetching_closed_history")
    b._set_warmup_state("MNQ", "UNAVAILABLE", reason="insufficient_closed_history")
    snapshot = get_databento_status_snapshot()
    assert snapshot["structure_warmup"]["MGC"]["state"] == "WARMING_UP"
    assert snapshot["structure_warmup"]["MNQ"]["state"] == "UNAVAILABLE"
    assert snapshot["structure_warmup"]["MGC"] is not DATABENTO_STATUS["structure_warmup"]["MGC"]


def test_restart_empty_state_warms_all_four_instruments_independently(monkeypatch):
    """Exercise the actual startup loop with Databento-shaped Historical rows."""
    class FakeStore:
        def __init__(self, inst):
            self.inst = inst

        def to_df(self):
            return self

        def iterrows(self):
            offset = list(DB_SYMBOLS).index(self.inst) * 1000
            for bar in bars():
                ts = datetime.fromtimestamp(bar["ts"], tz=timezone.utc)
                yield ts, {
                    "open": bar["open"] + offset, "high": bar["high"] + offset,
                    "low": bar["low"] + offset, "close": bar["close"] + offset,
                    "volume": bar["volume"],
                }

    class FakeTimeseries:
        def get_range(self, *, symbols, **_kwargs):
            symbol = symbols[0]
            inst = next(k for k, v in DB_SYMBOLS.items() if v == symbol)
            return FakeStore(inst)

    class FakeHistorical:
        def __init__(self, **_kwargs):
            self.timeseries = FakeTimeseries()

    _db.Historical = FakeHistorical
    monkeypatch.setattr(dbbrain.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")

    b = brain()
    callbacks = []
    b.register_bar_close_callback(lambda *args: callbacks.append(args))
    reconnect_called = []
    monkeypatch.setattr(b, "_reconnect_loop", lambda: reconnect_called.append(True))

    b._warmup_then_connect()

    assert reconnect_called == [True]
    assert callbacks == []
    assert not b._ah
    for inst in DB_SYMBOLS:
        status = DATABENTO_STATUS["structure_warmup"][inst]
        assert status["state"] == "READY"
        assert status["seeded_closed_bar_count"] == STRUCTURE_WARMUP_BARS
        assert status["newest_historical_source_timestamp"]
        assert status["warmup_started_at"]
        assert status["warmup_completed_at"]
        assert status["warmup_duration_ms"] is not None
        assert status["completion_reason"] == "sufficient_valid_closed_history"
        assert status["failure_reason"] is None
        assert len(b._bars[inst]) == STRUCTURE_WARMUP_BARS


def test_invalid_history_is_unavailable_per_instrument_without_leaking(monkeypatch):
    """Each malformed historical condition is terminal only for its instrument."""
    now = time.time()
    invalid_cases = [
        ("empty_history", [], 0),
        ("insufficient_closed_history", bars(STRUCTURE_WARMUP_BARS - 1),
         STRUCTURE_WARMUP_BARS - 1),
        ("history_too_stale", bars(
            newest_age_s=STRUCTURE_WARMUP_MAX_AGE_HOURS * 3600 + 1
        ), 0),
        ("malformed_or_non_monotonic_history", [
            *bars()[:10], bars()[11], bars()[10], *bars()[12:]
        ], 0),
        ("malformed_or_non_monotonic_history", [
            *bars()[:10],
            {**bars()[10], "high": float("nan")},
            *bars()[11:],
        ], 0),
    ]

    class FakeStore:
        def __init__(self, inst, rows):
            self.inst = inst
            self.rows = rows

        def to_df(self):
            return self

        def iterrows(self):
            for bar in self.rows:
                yield datetime.fromtimestamp(bar["ts"], tz=timezone.utc), {
                    key: bar[key] for key in ("open", "high", "low", "close", "volume")
                }

    monkeypatch.setattr(dbbrain.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")

    for expected_reason, invalid_rows, expected_observed_bars in invalid_cases:
        class FakeTimeseries:
            def get_range(self, *, symbols, **_kwargs):
                inst = next(k for k, v in DB_SYMBOLS.items() if v == symbols[0])
                return FakeStore(
                    inst, invalid_rows if inst == "MNQ" else bars()
                )

        class FakeHistorical:
            def __init__(self, **_kwargs):
                self.timeseries = FakeTimeseries()

        _db.Historical = FakeHistorical
        b = brain()
        reconnect_called = []
        monkeypatch.setattr(b, "_reconnect_loop", lambda: reconnect_called.append(True))
        b._warmup_then_connect()

        assert reconnect_called == [True]
        failed = DATABENTO_STATUS["structure_warmup"]["MNQ"]
        assert failed["state"] == "UNAVAILABLE"
        assert failed["failure_reason"] == expected_reason
        assert failed["seeded_closed_bar_count"] == 0
        assert failed["observed_closed_bar_count"] == expected_observed_bars
        assert failed["newest_historical_source_timestamp"] is None
        assert failed["warmup_duration_ms"] is not None
        assert not b._bars["MNQ"]
        for inst in ("MGC", "MES", "MYM"):
            assert DATABENTO_STATUS["structure_warmup"][inst]["state"] == "READY"
            assert len(b._bars[inst]) == STRUCTURE_WARMUP_BARS