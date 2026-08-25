"""Focused regression coverage for the bounded Databento record dispatcher.

No network connection or production state is used.  The suite intentionally
uses the real dispatcher internals so an unbounded intake queue, stale replay,
or cross-instrument counter leak cannot return unnoticed.
"""
from __future__ import annotations

import time
import types
import unittest
from collections import deque
from datetime import datetime, timezone

import databento_brain as db
from checks.databento_soak import (
    FakeSdkIteratorSource,
    SDK_OVERLOAD_BUFFER,
    SDK_OVERLOAD_RECORDS,
    build_report,
)


def _trade(iid: int, seq: int, *, ts: float | None = None):
    event = ts if ts is not None else time.time()
    return types.SimpleNamespace(
        instrument_id=iid,
        price=int((100.0 + seq) * 1_000_000_000),
        size=1,
        ts_event=int(event * 1_000_000_000),
        side="A",
        symbol="",
        sequence=seq,
    )


def _mbp(iid: int, *, event: float):
    return types.SimpleNamespace(
        instrument_id=iid,
        bid_px_00=int(100.0 * 1_000_000_000),
        ask_px_00=int(100.25 * 1_000_000_000),
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


class TestDatabentoQueueBackpressure(unittest.TestCase):
    def setUp(self):
        self.original_max = db.RECORD_QUEUE_MAX

    def tearDown(self):
        db.RECORD_QUEUE_MAX = self.original_max

    def _wait_empty(self, brain, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if (
                sum(brain._queue_depth_by_inst.values()) == 0
                and sum(brain._queue_inflight_by_inst.values()) == 0
                and sum(brain._queue_enqueued_by_inst.values())
                == sum(brain._queue_processed_by_inst.values())
                and sum(brain._downstream_depth_by_inst.values()) == 0
            ):
                return
            time.sleep(0.01)
        self.fail("bounded Databento queue did not drain")

    def test_sustained_four_instrument_load_drains_in_order(self):
        brain = _brain()
        ids = {"MGC": 11, "MNQ": 12, "MES": 13, "MYM": 14}
        brain._id_to_inst = {iid: inst for inst, iid in ids.items()}
        seen = []
        brain._on_trade = lambda rec: seen.append((brain._instrument_for_record(rec), rec.sequence))
        brain._start_record_dispatcher()
        try:
            for sequence in range(100):
                for iid in ids.values():
                    brain._dispatch_record(_trade(iid, sequence))
            self._wait_empty(brain)
            self.assertEqual(len(seen), 400)
            for inst in ids:
                observed = [sequence for got_inst, sequence in seen if got_inst == inst]
                self.assertEqual(observed, list(range(100)), inst)
                queue_state = db.DATABENTO_STATUS["instruments"][inst]["queue"]
                self.assertEqual(queue_state["queue_depth"], 0)
                self.assertGreaterEqual(queue_state["max_observed_queue_depth"], 0)
                self.assertEqual(queue_state["dropped"], 0)
                self.assertEqual(queue_state["freshness"], "FRESH")
        finally:
            brain._stop_record_dispatcher()

    def test_four_instrument_pressure_stays_bounded_and_fresh_without_overflow(self):
        """A busy but supported stream may backlog briefly without stale replay."""
        db.RECORD_QUEUE_MAX = 256
        brain = _brain()
        ids = {"MGC": 11, "MNQ": 12, "MES": 13, "MYM": 14}
        brain._id_to_inst = {iid: inst for inst, iid in ids.items()}
        processed = []

        def deliberately_busy(rec):
            time.sleep(0.001)
            processed.append((brain._instrument_for_record(rec), rec.sequence))

        brain._on_trade = deliberately_busy
        brain._start_record_dispatcher()
        try:
            max_depth = 0
            for sequence in range(40):
                for iid in ids.values():
                    brain._dispatch_record(_trade(iid, sequence))
                    max_depth = max(max_depth, sum(brain._queue_depth_by_inst.values()))

            self.assertGreater(max_depth, 0)
            self.assertLessEqual(max_depth, db.RECORD_QUEUE_MAX)
            self._wait_empty(brain)
            self.assertEqual(len(processed), 160)
            for inst in ids:
                queue_state = db.DATABENTO_STATUS["instruments"][inst]["queue"]
                self.assertEqual(queue_state["queue_depth"], 0)
                self.assertEqual(queue_state["dropped"], 0)
                self.assertEqual(queue_state["processing_lag_s"], 0.0)
                self.assertEqual(queue_state["freshness"], "FRESH")
        finally:
            brain._stop_record_dispatcher()

    def test_closed_bar_retains_actual_source_event_timestamp(self):
        brain = _brain()
        source_event = 1_787_599_259.75
        previous_public_bars = list(db.DATABENTO_BARS_BY_INST["MGC"])
        try:
            db.DATABENTO_BARS_BY_INST["MGC"].clear()
            brain._on_bar_close("MGC", {
                "ts": int(source_event // 60) * 60,
                "source_event_ts": source_event,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 3,
                "buy_volume": 2,
                "sell_volume": 1,
            })
            source_timestamp = db.DATABENTO_BARS_BY_INST["MGC"][-1]["source_timestamp"]
            assert source_timestamp == datetime.fromtimestamp(
                source_event, tz=timezone.utc,
            ).isoformat()
        finally:
            db.DATABENTO_BARS_BY_INST["MGC"].clear()
            db.DATABENTO_BARS_BY_INST["MGC"].extend(previous_public_bars)

    def test_overflow_is_bounded_and_never_claims_freshness(self):
        db.RECORD_QUEUE_MAX = 4
        brain = _brain()
        brain._id_to_inst = {11: "MGC"}
        original = brain._on_trade

        def slow(rec):
            time.sleep(0.05)
            original(rec)

        brain._on_trade = slow
        brain._start_record_dispatcher()
        try:
            for sequence in range(30):
                brain._dispatch_record(_trade(11, sequence))
            time.sleep(0.02)
            self.assertLessEqual(sum(brain._queue_depth_by_inst.values()), 4)
            self._wait_empty(brain)
            queue_state = db.DATABENTO_STATUS["instruments"]["MGC"]["queue"]
            self.assertGreater(queue_state["dropped"], 0)
            self.assertEqual(queue_state["freshness"], "UNAVAILABLE")
            self.assertEqual(db.DATABENTO_STATUS["queue"]["max_depth"], 4)
            self.assertLessEqual(
                db.DATABENTO_STATUS["queue"]["max_observed_depth"], 4
            )
        finally:
            brain._stop_record_dispatcher()

    def test_stale_mbp1_event_is_not_returned_as_current_quote(self):
        brain = _brain()
        brain._id_to_inst = {11: "MGC"}
        brain._on_mbp1(_mbp(11, event=time.time() - db.TOP_OF_BOOK_STALE_S - 1))
        self.assertIsNone(db.get_top_of_book_snapshot("MGC"))

    def test_disconnect_discards_unread_backlog(self):
        db.RECORD_QUEUE_MAX = 16
        brain = _brain()
        brain._id_to_inst = {11: "MGC"}
        entered = types.SimpleNamespace(value=False)

        def blocked(_rec):
            entered.value = True
            time.sleep(0.2)

        brain._on_trade = blocked
        brain._start_record_dispatcher()
        for sequence in range(12):
            brain._dispatch_record(_trade(11, sequence))
        deadline = time.time() + 1
        while not entered.value and time.time() < deadline:
            time.sleep(0.005)
        stopped_at = time.time()
        brain._stop_record_dispatcher()
        self.assertGreaterEqual(time.time() - stopped_at, 0.15)
        state = db.DATABENTO_STATUS["instruments"]["MGC"]["queue"]
        self.assertGreater(state["dropped"], 0)
        self.assertEqual(state["freshness"], "UNAVAILABLE")
        self.assertFalse(brain._record_worker.is_alive())

    def test_downstream_stage_timings_and_sdk_warning_telemetry(self):
        brain = _brain()
        brain._start_record_dispatcher()
        try:
            def measured_consumer(_inst, _price):
                time.sleep(0.002)

            brain._enqueue_downstream(
                measured_consumer, "MGC", 100.0, brain._active_dispatch_generation
            )
            self._wait_empty(brain)
            stage = db.DATABENTO_STATUS["downstream"]["stage_timings"]["measured_consumer"]
            self.assertEqual(stage["count"], 1)
            self.assertGreater(stage["max_ms"], 0.0)
            self.assertGreater(stage["total_ms"], 0.0)
            self.assertEqual(db.DATABENTO_STATUS["downstream"]["depth"], 0)

            brain._observe_sdk_queue_warning(
                "record queue is full; 358 record(s) to be processed"
            )
            transport = db.DATABENTO_STATUS["transport"]
            self.assertGreaterEqual(transport["sdk_queue_full_warnings"], 1)
            self.assertGreaterEqual(transport["sdk_queue_full_max_pending"], 358)
        finally:
            brain._stop_record_dispatcher()

    def test_sdk_iterator_buffer_pressure_is_separate_and_fail_closed(self):
        """SDK-buffer loss must be visible even when the app queue is healthy."""
        brain = _brain()
        brain._id_to_inst = {11: "MGC"}
        brain._on_trade = lambda _rec: None
        brain._start_record_dispatcher()
        try:
            source = FakeSdkIteratorSource(
                [
                    _trade(11, sequence, ts=time.time())
                    for sequence in range(SDK_OVERLOAD_RECORDS)
                ],
                buffer_limit=SDK_OVERLOAD_BUFFER,
            )
            brain._attach_sdk_queue_pressure_handler()
            try:
                brain._consume_feed_iterator(source)
            finally:
                brain._detach_sdk_queue_pressure_handler()
            self._wait_empty(brain)
            transport = db.get_databento_status_snapshot()["transport"]
            queue_state = db.DATABENTO_STATUS["instruments"]["MGC"]["queue"]
            self.assertGreater(source.records_dropped, 0)
            self.assertEqual(source.records_dropped, SDK_OVERLOAD_RECORDS - SDK_OVERLOAD_BUFFER)
            self.assertEqual(transport["sdk_records_dropped"], source.records_dropped)
            self.assertTrue(transport["sdk_unavailable"])
            self.assertEqual(queue_state["dropped"], 0)
            self.assertEqual(queue_state["freshness"], "UNAVAILABLE")
            self.assertEqual(queue_state["unavailable_reason"], "sdk_feed_buffer_pressure")
        finally:
            brain._stop_record_dispatcher()

    def test_native_structure_breaks_previously_confirmed_levels(self):
        """A BOS/CHOCH can only occur after, not inside, the pivot window."""
        brain = _brain()
        closes = [
            100, 101, 102, 103, 104, 110, 104, 103, 102, 101, 100, 105, 111,
            105, 100, 95, 90, 85, 112, 100, 105, 110, 115, 80,
        ]
        for index, close in enumerate(closes):
            bars = brain._bars["MGC"]
            bars.append({
                "ts": 1_700_000_000 + index * 60,
                "open": float(close),
                "high": float(close) + 0.25,
                "low": float(close) - 0.25,
                "close": float(close),
                "volume": 10,
            })
            brain._detect_structure("MGC", bars)
        alert_types = [
            alert.get("alert_type") for alert in brain._ah
            if isinstance(alert, dict) and alert.get("instrument") == "MGC"
        ]
        self.assertIn("BOS DEMAND", alert_types)
        self.assertIn("CHOCH SUPPLY", alert_types)

    def test_release_gate_soak_emits_pressure_and_drain_evidence(self):
        report = build_report()
        supported = report["supported_load"]
        overload = report["intentional_overload"]
        sdk_overload = report["sdk_iterator_overload"]
        self.assertEqual(supported["queue_dropped"], 0)
        self.assertEqual(supported["downstream_dropped"], 0)
        self.assertEqual(supported["sdk_pressure"]["records_dropped"], 0)
        self.assertFalse(supported["sdk_pressure"]["sdk_unavailable"])
        self.assertEqual(set(supported["freshness"]), {"MGC", "MNQ", "MES", "MYM"})
        self.assertTrue(all(v == "FRESH" for v in supported["freshness"].values()))
        for key in (
            "queue_depth_max", "sdk_pressure", "application_pressure",
            "source_age_s_max", "processing_lag_s_max", "downstream_stage",
            "drain_time_s",
        ):
            self.assertIn(key, supported)
        self.assertGreater(overload["queue_dropped"], 0)
        self.assertEqual(overload["sdk_pressure"]["sdk_records_dropped"], 0)
        self.assertFalse(overload["sdk_pressure"]["sdk_unavailable"])
        self.assertEqual(overload["freshness"]["MGC"], "UNAVAILABLE")
        self.assertGreater(sdk_overload["sdk_pressure"]["records_dropped"], 0)
        self.assertEqual(sdk_overload["queue_dropped"], 0)
        self.assertEqual(sdk_overload["application_pressure"]["queue_dropped"], 0)
        self.assertFalse(sdk_overload["application_pressure"]["pressure_detected"])
        self.assertTrue(sdk_overload["sdk_pressure"]["sdk_unavailable"])
        self.assertEqual(
            sdk_overload["freshness"]["MGC"], "UNAVAILABLE",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)