#!/usr/bin/env python3
"""Deterministic four-instrument Databento dispatcher soak evidence.

This is a local release-gate check.  It uses the real bounded dispatcher but
never opens a Databento connection, touches a database, or sends an order.
The workload is count-based (not wall-clock based), so the evidence is
repeatable while still exercising the production handoff threads.
"""
from __future__ import annotations

import json
import sys
import time
import types
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import databento_brain as db  # noqa: E402


INSTRUMENT_IDS = {"MGC": 11, "MNQ": 12, "MES": 13, "MYM": 14}
SUPPORTED_TRADE_RATE = 20
SUPPORTED_MBP_RATE = 10
SUPPORTED_DURATION_S = 0.6
SUPPORTED_TRADE_CYCLES = int(SUPPORTED_TRADE_RATE * SUPPORTED_DURATION_S)
SUPPORTED_MBP_CYCLES = int(SUPPORTED_MBP_RATE * SUPPORTED_DURATION_S)
OVERLOAD_RECORDS = 240


def _trade(iid: int, sequence: int, event: float):
    return types.SimpleNamespace(
        instrument_id=iid,
        price=int((100.0 + sequence / 100.0) * 1_000_000_000),
        size=1,
        ts_event=int(event * 1_000_000_000),
        side="A",
        symbol="",
        sequence=sequence,
    )


def _mbp(iid: int, event: float):
    return types.SimpleNamespace(
        instrument_id=iid,
        bid_px_00=100_000_000_000,
        ask_px_00=100_250_000_000,
        bid_sz_00=5,
        ask_sz_00=7,
        ts_event=int(event * 1_000_000_000),
        symbol="",
    )


def _brain():
    return db.DatabentoBrain(
        alert_history=deque(maxlen=100),
        cvd_by_ticker={},
        rvol_by_ticker={},
        auto_price_by_ticker={},
        current_price_by_ticker={},
        current_price_ts_by_ticker={},
        volume_spike_by_ticker={},
    )


def _wait_drained(brain, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (
            sum(brain._queue_depth_by_inst.values()) == 0
            and sum(brain._downstream_depth_by_inst.values()) == 0
        ):
            return time.monotonic()
        time.sleep(0.002)
    raise AssertionError("dispatcher did not drain within release-gate timeout")


def _metrics(before, after, brain, *, queue_depth_max=0, downstream_depth_max=0):
    states = {
        inst: dict((after.get("instruments") or {}).get(inst, {}).get("queue") or {})
        for inst in INSTRUMENT_IDS
    }
    return {
        "queue_depth_max": queue_depth_max,
        "downstream_depth_max": downstream_depth_max,
        "queue_enqueued": int((after.get("queue") or {}).get("enqueued") or 0),
        "queue_processed": int((after.get("queue") or {}).get("processed") or 0),
        "queue_dropped": int((after.get("queue") or {}).get("dropped") or 0),
        "downstream_enqueued": int((after.get("downstream") or {}).get("enqueued") or 0),
        "downstream_processed": int((after.get("downstream") or {}).get("processed") or 0),
        "downstream_dropped": int((after.get("downstream") or {}).get("dropped") or 0),
        "sdk_pressure": dict(after.get("transport") or {}),
        "application_pressure": {
            "queue_depth": int((after.get("queue") or {}).get("depth") or 0),
            "downstream_depth": int((after.get("downstream") or {}).get("depth") or 0),
            "downstream_unhealthy": bool((after.get("downstream") or {}).get("unhealthy")),
        },
        "source_age_s_max": max(
            (float(v["source_event_age_s"]) for v in states.values()
             if v.get("source_event_age_s") is not None), default=None,
        ),
        "processing_lag_s_max": max(
            (float(v["processing_lag_s"]) for v in states.values()
             if v.get("processing_lag_s") is not None), default=None,
        ),
        "freshness": {inst: v.get("freshness") for inst, v in states.items()},
        "downstream_stage": {
            "slowest_stage": (after.get("downstream") or {}).get("slowest_stage"),
            "slowest_stage_ms": float((after.get("downstream") or {}).get("slowest_stage_ms") or 0),
        },
        "before": {
            "queue_depth": int((before.get("queue") or {}).get("depth") or 0),
            "downstream_depth": int((before.get("downstream") or {}).get("depth") or 0),
            "sdk_pressure": dict(before.get("transport") or {}),
            "application_pressure": {
                "queue_depth": int((before.get("queue") or {}).get("depth") or 0),
                "downstream_depth": int((before.get("downstream") or {}).get("depth") or 0),
            },
        },
    }


def _run_supported():
    brain = _brain()
    brain._id_to_inst = {iid: inst for inst, iid in INSTRUMENT_IDS.items()}
    brain._on_trade = lambda rec: None
    brain._start_record_dispatcher()
    try:
        before = db.get_databento_status_snapshot()
        now = time.time()
        queue_depth_max = downstream_depth_max = 0
        # Fixed counts represent 20 trades/s and 10 MBP-1 updates/s per
        # instrument for a deterministic 0.6s supported soak.
        for cycle in range(SUPPORTED_TRADE_CYCLES):
            event = now - cycle / SUPPORTED_TRADE_RATE
            for inst, iid in INSTRUMENT_IDS.items():
                brain._dispatch_record(_trade(iid, cycle, event))
                if cycle < SUPPORTED_MBP_CYCLES:
                    brain._dispatch_record(_mbp(iid, event))
                generation = brain._active_dispatch_generation
                brain._enqueue_downstream(lambda _i, _p: None, inst, 100.0, generation)
                queue_depth_max = max(queue_depth_max, sum(brain._queue_depth_by_inst.values()))
                downstream_depth_max = max(
                    downstream_depth_max, sum(brain._downstream_depth_by_inst.values())
                )
        drained_at = _wait_drained(brain)
        after = db.get_databento_status_snapshot()
        report = _metrics(
            before, after, brain,
            queue_depth_max=queue_depth_max,
            downstream_depth_max=downstream_depth_max,
        )
        report.update({
            "trade_rate_per_instrument": SUPPORTED_TRADE_RATE,
            "mbp1_rate_per_instrument": SUPPORTED_MBP_RATE,
            "duration_s": SUPPORTED_DURATION_S,
            "trade_records_per_instrument": SUPPORTED_TRADE_CYCLES,
            "mbp1_records_per_instrument": SUPPORTED_MBP_CYCLES,
            "drain_time_s": round(max(0.0, drained_at - now), 4),
        })
        assert report["queue_dropped"] == 0
        assert report["downstream_dropped"] == 0
        assert all(value == "FRESH" for value in report["freshness"].values())
        return report
    finally:
        brain._stop_record_dispatcher()


def _run_overload():
    original_max = db.RECORD_QUEUE_MAX
    db.RECORD_QUEUE_MAX = 64
    brain = _brain()
    brain._id_to_inst = {11: "MGC"}
    brain._on_trade = lambda rec: time.sleep(0.01)
    brain._start_record_dispatcher()
    try:
        before = db.get_databento_status_snapshot()
        now = time.time()
        started = time.monotonic()
        queue_depth_max = 0
        for sequence in range(OVERLOAD_RECORDS):
            brain._dispatch_record(_trade(11, sequence, now))
            queue_depth_max = max(queue_depth_max, sum(brain._queue_depth_by_inst.values()))
        _wait_drained(brain)
        after = db.get_databento_status_snapshot()
        report = _metrics(before, after, brain, queue_depth_max=queue_depth_max)
        report.update({
            "records_attempted": OVERLOAD_RECORDS,
            "queue_limit": 64,
            "drain_time_s": round(max(0.0, time.monotonic() - started), 4),
        })
        assert report["queue_dropped"] > 0
        assert report["freshness"]["MGC"] == "UNAVAILABLE"
        return report
    finally:
        brain._stop_record_dispatcher()
        db.RECORD_QUEUE_MAX = original_max


def build_report():
    return {
        "schema": "databento-soak-v1",
        "workload": {
            "instruments": list(INSTRUMENT_IDS),
            "supported": {
                "trade_rate_per_instrument": SUPPORTED_TRADE_RATE,
                "mbp1_rate_per_instrument": SUPPORTED_MBP_RATE,
                "duration_s": SUPPORTED_DURATION_S,
                "trade_records_per_instrument": SUPPORTED_TRADE_CYCLES,
                "mbp1_records_per_instrument": SUPPORTED_MBP_CYCLES,
            },
            "overload": {"records": OVERLOAD_RECORDS, "queue_limit": 64},
        },
        "supported_load": _run_supported(),
        "intentional_overload": _run_overload(),
        "assertions": {
            "supported_zero_drop": True,
            "overload_fail_closed": True,
        },
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), indent=2, sort_keys=True))
    print("DATABENTO FOUR-INSTRUMENT SOAK OK", file=sys.stderr)