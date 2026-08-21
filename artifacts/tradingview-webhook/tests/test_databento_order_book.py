"""Focused MBP-1 integration tests using synthetic Databento records only."""

from __future__ import annotations

import os
import sys
import time
import types
from collections import deque
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import databento_brain as dbb  # noqa: E402


def _brain():
    return dbb.DatabentoBrain(
        alert_history=deque(maxlen=50),
        cvd_by_ticker={},
        rvol_by_ticker={},
        auto_price_by_ticker={},
        current_price_by_ticker={},
        current_price_ts_by_ticker={},
        volume_spike_by_ticker={},
    )


def _mbp1(iid, bid_px, ask_px, bid_sz, ask_sz):
    return types.SimpleNamespace(
        instrument_id=iid,
        bid_px_00=int(bid_px * 1_000_000_000),
        ask_px_00=int(ask_px * 1_000_000_000),
        bid_sz_00=bid_sz,
        ask_sz_00=ask_sz,
        ts_event=int(time.time() * 1_000_000_000),
    )


@pytest.fixture(autouse=True)
def _clear_book_state():
    dbb.clear_top_of_book_snapshots()
    dbb.DATABENTO_STATUS["order_book"] = {
        "schema": "mbp-1",
        "enabled": True,
        "subscription": "pending",
        "last_update": None,
        "updates": 0,
    }
    yield
    dbb.clear_top_of_book_snapshots()


def test_mbp1_updates_only_its_canonical_instrument():
    brain = _brain()
    brain._id_to_inst.update({101: "MNQ", 202: "MGC"})

    brain._on_mbp1(_mbp1(101, 20000.0, 20000.25, 80, 20))
    mnq = dbb.get_top_of_book_snapshot("MNQ", max_age_s=5)

    assert mnq is not None
    assert mnq["instrument"] == "MNQ"
    assert mnq["bid_price"] == 20000.0
    assert mnq["ask_price"] == 20000.25
    assert mnq["bid_size"] == 80
    assert mnq["ask_size"] == 20
    assert dbb.get_top_of_book_snapshot("MGC", max_age_s=5) is None


def test_invalid_quote_is_ignored_and_freshness_expires():
    brain = _brain()
    brain._id_to_inst[101] = "MNQ"

    brain._on_mbp1(_mbp1(101, 20000.0, 20000.25, 50, 50))
    now = time.time()
    assert dbb.get_top_of_book_snapshot("MNQ", now_epoch=now, max_age_s=5) is not None
    assert dbb.get_top_of_book_snapshot("MNQ", now_epoch=now + 6, max_age_s=5) is None

    dbb.clear_top_of_book_snapshots()
    brain._on_mbp1(_mbp1(101, 20000.0, 19999.75, 50, 50))
    assert dbb.get_top_of_book_snapshot("MNQ", max_age_s=5) is None


def test_mixed_live_session_subscribes_to_trades_and_mbp1(monkeypatch):
    instances = []

    class FakeLive:
        def __init__(self, **_kwargs):
            self.subscriptions = []
            instances.append(self)

        def subscribe(self, **kwargs):
            self.subscriptions.append(kwargs)

        def add_callback(self, _callback):
            return None

        def __iter__(self):
            return iter(())

    class FakeHistorical:
        def __init__(self, **_kwargs):
            self.symbology = types.SimpleNamespace(resolve=lambda **_kw: {"result": {}})

    fake_db = types.SimpleNamespace(Live=FakeLive, Historical=FakeHistorical)
    monkeypatch.setitem(sys.modules, "databento", fake_db)
    monkeypatch.setenv("DATABENTO_API_KEY", "test-only-key")
    monkeypatch.setattr(dbb, "DATABENTO_MBP1_ENABLED", True)

    brain = _brain()
    brain._id_to_inst[101] = "MNQ"
    brain._on_mbp1(_mbp1(101, 20000.0, 20000.25, 80, 20))
    assert dbb.get_top_of_book_snapshot("MNQ", max_age_s=5) is not None

    # _run_feed begins every connection by clearing an older session's quote.
    brain._run_feed()

    schemas = [item["schema"] for item in instances[0].subscriptions]
    assert schemas == ["trades", "mbp-1"]
    assert dbb.DATABENTO_STATUS["order_book"]["subscription"] == "active"
    assert dbb.get_top_of_book_snapshot("MNQ", max_age_s=5) is None


def test_mbp1_subscription_failure_preserves_trade_feed(monkeypatch):
    instances = []

    class FakeLive:
        def __init__(self, **_kwargs):
            self.subscriptions = []
            instances.append(self)

        def subscribe(self, **kwargs):
            self.subscriptions.append(kwargs)
            if kwargs["schema"] == "mbp-1":
                raise RuntimeError("market depth unavailable")

        def add_callback(self, _callback):
            return None

        def __iter__(self):
            return iter(())

    class FakeHistorical:
        def __init__(self, **_kwargs):
            self.symbology = types.SimpleNamespace(resolve=lambda **_kw: {"result": {}})

    fake_db = types.SimpleNamespace(Live=FakeLive, Historical=FakeHistorical)
    monkeypatch.setitem(sys.modules, "databento", fake_db)
    monkeypatch.setenv("DATABENTO_API_KEY", "test-only-key")
    monkeypatch.setattr(dbb, "DATABENTO_MBP1_ENABLED", True)

    _brain()._run_feed()

    assert instances[0].subscriptions[0]["schema"] == "trades"
    assert instances[0].subscriptions[1]["schema"] == "mbp-1"
    assert dbb.DATABENTO_STATUS["order_book"]["subscription"] == "unavailable"