"""Deterministic four-instrument Databento dispatcher release soak.

This is intentionally local-only: it drives the real record worker, bar builder,
VWAP/CVD/RVOL state, native structure detector, and bounded downstream observer
queue without opening a Databento connection or touching Flask, the broker, or a
database.  The default 45 second / 1,200 records-per-second rate is over five
times the recent aggregate production event rate that exposed the original
backpressure incident.

Run from the repository root:
    python artifacts/tradingview-webhook/scripts/databento_four_instrument_soak.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import types
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import databento_brain as db  # noqa: E402


INSTRUMENTS = ("MGC", "MNQ", "MES", "MYM")
INSTRUMENT_IDS = {"MGC": 101, "MNQ": 102, "MES": 103, "MYM": 104}
BASE_PRICES = {"MGC": 4700.0, "MNQ": 21000.0, "MES": 5200.0, "MYM": 39000.0}
POINT_SWINGS = {"MGC": 4.0, "MNQ": 48.0, "MES": 12.0, "MYM": 90.0}


def _trade(iid: int, price: float, sequence: int, event_ts: float, side: str):
    return types.SimpleNamespace(
        instrument_id=iid,
        price=int(round(price * 1_000_000_000)),
        size=1 + (sequence % 3),
        ts_event=int(event_ts * 1_000_000_000),
        side=side,
        symbol="",
        sequence=sequence,
    )


def _wait_drained(brain: db.DatabentoBrain, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with brain._dispatch_lock:
            authoritative_done = (
                sum(brain._queue_depth_by_inst.values()) == 0
                and sum(brain._queue_inflight_by_inst.values()) == 0
                and sum(brain._queue_enqueued_by_inst.values())
                == sum(brain._queue_processed_by_inst.values())
            )
            downstream_done = sum(brain._downstream_depth_by_inst.values()) == 0
        if authoritative_done and downstream_done:
            return True
        time.sleep(0.01)
    return False


def _price(inst: str, bar_index: int, tick_in_bar: int, ticks_per_bar: int) -> float:
    """A smooth trend/reversal sequence with repeated closed-bar pivots."""
    wave = math.sin(bar_index / 3.0) + 0.45 * math.sin(bar_index / 1.35)
    micro = ((tick_in_bar / max(1, ticks_per_bar - 1)) - 0.5) * 0.18
    return BASE_PRICES[inst] + POINT_SWINGS[inst] * (wave + micro)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument("--records-per-second", type=int, default=1200)
    parser.add_argument("--bars-per-instrument", type=int, default=80)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    if args.seconds < 5 or args.records_per_second < 100:
        parser.error("use at least 5 seconds and 100 records per second")

    total_target = int(round(args.seconds * args.records_per_second))
    source_span_s = max(90.0 * 60.0, args.seconds * 2.0)
    expected = Counter(INSTRUMENTS[index % len(INSTRUMENTS)] for index in range(total_target))
    ticks_per_bar = max(8, min(
        min(expected.values()) // max(1, args.bars_per_instrument),
        200,
    ))

    alert_history = deque(maxlen=5000)
    cvd, rvol, auto_price, current_price, current_price_ts = {}, {}, {}, {}, {}
    volume_spike, volatility, vwap = {}, {}, {}
    brain = db.DatabentoBrain(
        alert_history=alert_history,
        cvd_by_ticker=cvd,
        rvol_by_ticker=rvol,
        auto_price_by_ticker=auto_price,
        current_price_by_ticker=current_price,
        current_price_ts_by_ticker=current_price_ts,
        volume_spike_by_ticker=volume_spike,
        volatility_by_ticker=volatility,
        vwap_by_ticker=vwap,
    )
    brain._id_to_inst = {iid: inst for inst, iid in INSTRUMENT_IDS.items()}

    callback_calls = Counter()

    def chart_vwap_cvd_rvol_consumer(inst: str, _price_value: float) -> None:
        callback_calls["chart_vwap_cvd_rvol_consumer"] += 1
        bar = db.DATABENTO_BARS_BY_INST[inst][-1]
        assert bar.get("source_timestamp")
        # The callback starts on the first completed bar. RVOL legitimately has
        # a warm-up window, so sample the real stores without treating an early
        # missing rolling value as an observer failure.
        _ = cvd.get(inst), rvol.get(inst), vwap.get(inst)

    def native_bos_choch_consumer(inst: str, _price_value: float) -> None:
        callback_calls["native_bos_choch_consumer"] += 1
        # The real detector has already run before the callback is handed off.
        assert len(brain._bars[inst]) >= 1

    brain.register_bar_close_callback(chart_vwap_cvd_rvol_consumer)
    brain.register_bar_close_callback(native_bos_choch_consumer)

    for inst in INSTRUMENTS:
        db.DATABENTO_BARS_BY_INST[inst].clear()

    started_wall = time.time()
    started_mono = time.monotonic()
    # End source time at the scheduled end of the run. This is a paced feed:
    # the final source event must be fresh when the producer finishes, rather
    # than carrying the start-wall-clock timestamp through the whole soak.
    source_start = started_wall + args.seconds - source_span_s
    sequences = Counter()
    brain._start_record_dispatcher()
    try:
        for index in range(total_target):
            inst = INSTRUMENTS[index % len(INSTRUMENTS)]
            sequence = sequences[inst]
            sequences[inst] += 1
            bar_index, tick_in_bar = divmod(sequence, ticks_per_bar)
            event_ts = source_start + source_span_s * ((index + 1) / total_target)
            brain._dispatch_record(_trade(
                INSTRUMENT_IDS[inst],
                _price(inst, bar_index, tick_in_bar, ticks_per_bar),
                sequence,
                event_ts,
                "A" if sequence % 2 == 0 else "B",
            ))
            target_elapsed = (index + 1) / args.records_per_second
            sleep_s = started_mono + target_elapsed - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)

        produced_elapsed_s = time.monotonic() - started_mono
        drained = _wait_drained(brain, timeout_s=max(20.0, args.seconds))
        snapshot = db.get_databento_status_snapshot()
        source_checks = {
            inst: {
                "chart_bars": len(db.DATABENTO_BARS_BY_INST[inst]),
                "vwap_present": inst in vwap,
                "cvd_present": inst in cvd,
                "rvol_present": inst in rvol,
                "native_structure_bars": len(brain._bars[inst]),
            }
            for inst in INSTRUMENTS
        }
        structures = {
            inst: sum(
                1 for alert in alert_history
                if isinstance(alert, dict)
                and str(alert.get("ticker") or "").upper().startswith(inst)
                and ("BOS" in str(alert.get("alert_type") or "")
                     or "CHOCH" in str(alert.get("alert_type") or ""))
            )
            for inst in INSTRUMENTS
        }
        per_instrument = {
            inst: {
                "expected_enqueued": expected[inst],
                **dict((snapshot["instruments"].get(inst) or {}).get("queue") or {}),
                **source_checks[inst],
                "native_bos_choch_events": structures[inst],
            }
            for inst in INSTRUMENTS
        }
        transport = dict(snapshot.get("transport") or {})
        downstream = dict(snapshot.get("downstream") or {})
        partial_flush = dict(snapshot.get("partial_flush") or {})
        report = {
            "name": "four_instrument_databento_release_soak",
            "duration_s": round(produced_elapsed_s, 3),
            "target_duration_s": args.seconds,
            "records_target": total_target,
            "records_per_second_target": args.records_per_second,
            "records_per_second_actual": round(total_target / max(produced_elapsed_s, 0.001), 3),
            "source_span_s": round(source_span_s, 3),
            "ticks_per_bar": ticks_per_bar,
            "drained": drained,
            "authoritative_queue": dict(snapshot.get("queue") or {}),
            "downstream_queue": downstream,
            "transport": transport,
            "partial_flush": partial_flush,
            "callback_calls": dict(callback_calls),
            "per_instrument": per_instrument,
        }

        failures = []
        if not drained:
            failures.append("queues_did_not_drain")
        if report["records_per_second_actual"] < args.records_per_second * 0.90:
            failures.append("producer_missed_supported_rate")
        if int(report["authoritative_queue"].get("dropped") or 0) != 0:
            failures.append("authoritative_records_dropped")
        if int(downstream.get("dropped") or 0) != 0:
            failures.append("downstream_records_dropped")
        if int(report["authoritative_queue"].get("depth") or 0) != 0:
            failures.append("authoritative_queue_not_empty")
        if int(downstream.get("depth") or 0) != 0:
            failures.append("downstream_queue_not_empty")
        if int(transport.get("sdk_queue_full_warnings") or 0) != 0:
            failures.append("sdk_queue_full_warning")
        if int(partial_flush.get("backlog_caused") or 0) != 0:
            failures.append("backlog_caused_partial_flush")
        for inst, metrics in per_instrument.items():
            if metrics.get("enqueued") != expected[inst] or metrics.get("processed") != expected[inst]:
                failures.append("%s_authoritative_count_mismatch" % inst)
            if metrics.get("dropped") != 0:
                failures.append("%s_records_dropped" % inst)
            if not all(metrics[key] for key in (
                "chart_bars", "vwap_present", "cvd_present", "rvol_present",
                "native_structure_bars",
            )):
                failures.append("%s_consumer_not_current" % inst)
            if int(metrics.get("native_bos_choch_events") or 0) < 1:
                failures.append("%s_native_bos_choch_not_observed" % inst)
            if (metrics.get("source_event_age_s") is None
                    or metrics["source_event_age_s"] > 3.0):
                failures.append("%s_source_event_stale" % inst)
            if float(metrics.get("processing_lag_s") or 0.0) > 1.0:
                failures.append("%s_processing_lag_high" % inst)
        report["passed"] = not failures
        report["failures"] = failures
        rendered = json.dumps(report, indent=2, sort_keys=True)
        print(rendered)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(rendered + "\n", encoding="utf-8")
        return 0 if report["passed"] else 1
    finally:
        brain._stop_record_dispatcher()


if __name__ == "__main__":
    raise SystemExit(main())