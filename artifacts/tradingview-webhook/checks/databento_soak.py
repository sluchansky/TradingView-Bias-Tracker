#!/usr/bin/env python3
"""Deterministic four-instrument Databento dispatcher soak evidence.

This is a local release-gate check.  It uses the real bounded dispatcher but
never opens a Databento connection, touches a database, or sends an order.
The workload is count-based (not wall-clock based), so the evidence is
repeatable while still exercising the production handoff threads.
"""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
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
SDK_OVERLOAD_RECORDS = 240
SDK_OVERLOAD_BUFFER = 8


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


class FakeSdkIteratorSource:
    """A deterministic stand-in for the SDK's bounded iterator buffer.

    The producer fills the buffer before the consumer starts for release-gate
    runs.  This makes a small-buffer overload deterministic while still using
    the same iterator boundary that the live client uses in DatabentoBrain.
    Queue pressure is reported through the SDK session logger, just like the
    production telemetry handler observes it.
    """

    def __init__(self, records, *, buffer_limit: int, burst: bool = True):
        self._records = list(records)
        self.buffer_limit = int(buffer_limit)
        self._burst = bool(burst)
        self._buffer = queue.Queue(maxsize=self.buffer_limit)
        self._producer_done = threading.Event()
        self._started = False
        self.records_attempted = 0
        self.records_buffered = 0
        self.records_yielded = 0
        self.records_dropped = 0
        self.pressure_events = 0
        self.max_buffer_depth = 0

    def _produce(self):
        sdk_log = logging.getLogger("databento.live.session")
        try:
            for record in self._records:
                self.records_attempted += 1
                try:
                    self._buffer.put_nowait(record)
                except queue.Full:
                    self.records_dropped += 1
                    self.pressure_events += 1
                    sdk_log.warning(
                        "fake SDK iterator queue full; %d record(s) to be processed; "
                        "1 record(s) dropped",
                        self._buffer.qsize(),
                    )
                    continue
                self.records_buffered += 1
                self.max_buffer_depth = max(self.max_buffer_depth, self._buffer.qsize())
        finally:
            self._producer_done.set()

    def __iter__(self):
        if self._started:
            raise RuntimeError("fake SDK iterator can only be consumed once")
        self._started = True
        producer = threading.Thread(
            target=self._produce,
            daemon=True,
            name="fake-databento-sdk-producer",
        )
        producer.start()
        if self._burst:
            self._producer_done.wait()
        while not self._producer_done.is_set() or not self._buffer.empty():
            try:
                record = self._buffer.get(timeout=0.01)
            except queue.Empty:
                continue
            try:
                self.records_yielded += 1
                yield record
            finally:
                self._buffer.task_done()

    def telemetry(self) -> dict:
        return {
            "source": "fake_sdk_iterator",
            "buffer_limit": self.buffer_limit,
            "records_attempted": self.records_attempted,
            "records_buffered": self.records_buffered,
            "records_yielded": self.records_yielded,
            "records_dropped": self.records_dropped,
            "pressure_events": self.pressure_events,
            "max_buffer_depth": self.max_buffer_depth,
        }


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
            and sum(brain._queue_inflight_by_inst.values()) == 0
            and sum(brain._downstream_depth_by_inst.values()) == 0
        ):
            return time.monotonic()
        time.sleep(0.002)
    raise AssertionError("dispatcher did not drain within release-gate timeout")


def _metrics(
    before,
    after,
    brain,
    *,
    queue_depth_max=0,
    downstream_depth_max=0,
    sdk_source=None,
):
    states = {
        inst: dict((after.get("instruments") or {}).get(inst, {}).get("queue") or {})
        for inst in INSTRUMENT_IDS
    }
    sdk_pressure = dict(after.get("transport") or {})
    if sdk_source is not None:
        sdk_pressure.update(sdk_source)
    application_pressure = {
        "queue_depth": int((after.get("queue") or {}).get("depth") or 0),
        "downstream_depth": int((after.get("downstream") or {}).get("depth") or 0),
        "queue_dropped": int((after.get("queue") or {}).get("dropped") or 0),
        "downstream_dropped": int((after.get("downstream") or {}).get("dropped") or 0),
        "downstream_unhealthy": bool((after.get("downstream") or {}).get("unhealthy")),
        "pressure_detected": bool(
            (after.get("queue") or {}).get("dropped")
            or (after.get("downstream") or {}).get("dropped")
        ),
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
        "sdk_pressure": sdk_pressure,
        "application_pressure": application_pressure,
        "pressure_domains": {
            "sdk": dict(sdk_pressure),
            "application": dict(application_pressure),
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
    generation = None

    def on_trade(rec):
        inst = brain._instrument_for_record(rec)
        if inst:
            brain._enqueue_downstream(lambda _i, _p: None, inst, 100.0, generation)

    brain._on_trade = on_trade
    brain._start_record_dispatcher()
    generation = brain._active_dispatch_generation
    try:
        before = db.get_databento_status_snapshot()
        now = time.time()
        started = time.monotonic()
        queue_depth_max = downstream_depth_max = 0
        records = []
        # Fixed counts represent 20 trades/s and 10 MBP-1 updates/s per
        # instrument for a deterministic 0.6s supported soak.
        for cycle in range(SUPPORTED_TRADE_CYCLES):
            event = now - cycle / SUPPORTED_TRADE_RATE
            for inst, iid in INSTRUMENT_IDS.items():
                records.append(_trade(iid, cycle, event))
                if cycle < SUPPORTED_MBP_CYCLES:
                    records.append(_mbp(iid, event))
        source = FakeSdkIteratorSource(records, buffer_limit=len(records))
        brain._attach_sdk_queue_pressure_handler()
        try:
            brain._consume_feed_iterator(source)
        finally:
            brain._detach_sdk_queue_pressure_handler()
        queue_depth_max = max(queue_depth_max, brain._queue_max_observed_total)
        downstream_depth_max = max(
            downstream_depth_max, brain._downstream_max_observed_total
        )
        drained_at = _wait_drained(brain)
        after = db.get_databento_status_snapshot()
        report = _metrics(
            before, after, brain,
            queue_depth_max=queue_depth_max,
            downstream_depth_max=downstream_depth_max,
            sdk_source=source.telemetry(),
        )
        report.update({
            "trade_rate_per_instrument": SUPPORTED_TRADE_RATE,
            "mbp1_rate_per_instrument": SUPPORTED_MBP_RATE,
            "duration_s": SUPPORTED_DURATION_S,
            "trade_records_per_instrument": SUPPORTED_TRADE_CYCLES,
            "mbp1_records_per_instrument": SUPPORTED_MBP_CYCLES,
            "drain_time_s": round(max(0.0, drained_at - started), 4),
        })
        assert report["queue_dropped"] == 0
        assert report["downstream_dropped"] == 0
        assert report["sdk_pressure"]["records_dropped"] == 0
        assert report["sdk_pressure"]["sdk_unavailable"] is False
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


def _run_sdk_overload():
    """Overload only the upstream iterator buffer, not the app queue."""
    brain = _brain()
    brain._id_to_inst = {11: "MGC"}
    brain._on_trade = lambda rec: None
    brain._start_record_dispatcher()
    try:
        before = db.get_databento_status_snapshot()
        now = time.time()
        started = time.monotonic()
        source = FakeSdkIteratorSource(
            [_trade(11, sequence, now) for sequence in range(SDK_OVERLOAD_RECORDS)],
            buffer_limit=SDK_OVERLOAD_BUFFER,
        )
        brain._attach_sdk_queue_pressure_handler()
        try:
            brain._consume_feed_iterator(source)
        finally:
            brain._detach_sdk_queue_pressure_handler()
        drained_at = _wait_drained(brain)
        after = db.get_databento_status_snapshot()
        report = _metrics(
            before, after, brain, sdk_source=source.telemetry(),
        )
        report.update({
            "records_attempted": SDK_OVERLOAD_RECORDS,
            "sdk_buffer_limit": SDK_OVERLOAD_BUFFER,
            "drain_time_s": round(max(0.0, drained_at - started), 4),
        })
        assert report["sdk_pressure"]["records_dropped"] > 0
        assert report["queue_dropped"] == 0
        assert report["freshness"]["MGC"] == "UNAVAILABLE"
        return report
    finally:
        brain._stop_record_dispatcher()


def build_report():
    return {
        "schema": "databento-soak-v2",
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
            "sdk_overload": {
                "records": SDK_OVERLOAD_RECORDS,
                "iterator_buffer_limit": SDK_OVERLOAD_BUFFER,
            },
        },
        "supported_load": _run_supported(),
        "intentional_overload": _run_overload(),
        "sdk_iterator_overload": _run_sdk_overload(),
        "assertions": {
            "supported_zero_drop": True,
            "overload_fail_closed": True,
            "application_overload_fail_closed": True,
            "sdk_overload_fail_closed": True,
        },
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), indent=2, sort_keys=True))
    print("DATABENTO FOUR-INSTRUMENT SOAK OK", file=sys.stderr)