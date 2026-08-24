"""Native structure startup warm-up tests (no network or live feed)."""
from __future__ import annotations

import sys
import time
import types
from collections import deque
from datetime import datetime, timezone

_db = types.ModuleType("databento")
sys.modules.setdefault("databento", _db)

from databento_brain import (  # noqa: E402
    DB_SYMBOLS,
    DATABENTO_BARS_BY_INST,
    DATABENTO_STATUS,
    STRUCTURE_WARMUP_BARS,
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
    unordered[-1]["ts"] = unordered[-2]["ts"]
    assert brain()._validated_history(unordered, now)[1] == "malformed_or_non_monotonic_history"

    _, reason = brain()._validated_history(bars(STRUCTURE_WARMUP_BARS - 1), now)
    assert reason == "insufficient_closed_history"


def test_replay_reconstructs_only_native_state_and_never_emits_live_signals():
    volatility = {}
    b = DatabentoBrain(
        alert_history=deque(maxlen=500), cvd_by_ticker={}, rvol_by_ticker={},
        auto_price_by_ticker={}, current_price_by_ticker={},
        current_price_ts_by_ticker={}, volume_spike_by_ticker={},
        volatility_by_ticker=volatility,
    )
    received = []
    b.register_bar_close_callback(lambda *_: received.append("bar"))
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