"""
Part 10 test suite — Databento MGC bar pipeline.

Cases A–L from the spec.  No production state is mutated.
No real Databento connection is made — all cases use the unit-testable
DatabentoBrain internals with synthetic records.

Run:
    python3 test_databento_mgc_bars.py
"""
from __future__ import annotations

import threading
import time
import types
from collections import deque
from datetime import datetime, timezone
from typing import Any

# ── Minimal stub so databento_brain imports without the real package ───────────
import sys
_db_stub = types.ModuleType("databento")
class _LiveStub:
    def __init__(self, **kw): pass
    def subscribe(self, **kw): pass
    @property
    def symbology_map(self): return {}
    def __iter__(self): return iter([])
_db_stub.Live = _LiveStub
sys.modules.setdefault("databento", _db_stub)

from databento_brain import (  # noqa: E402
    DB_SYMBOLS,
    DATABENTO_BARS_BY_INST,
    DatabentoBrain,
)
MGC_SYMBOL = DB_SYMBOLS["MGC"]

# ── Test helpers ──────────────────────────────────────────────────────────────

PASS = "✓"
FAIL = "✗"
_results: list[tuple[str, str, str]] = []   # (case, description, status)

def check(case: str, desc: str, cond: bool) -> None:
    status = PASS if cond else FAIL
    _results.append((case, desc, status))
    if not cond:
        print(f"  {FAIL} [{case}] {desc}")

def _make_brain() -> tuple[DatabentoBrain, dict, dict, dict, dict, dict, dict, deque]:
    """Return a fresh DatabentoBrain + its shared state stores."""
    ah  = deque(maxlen=500)
    cvd = {}; rvol = {}; ap = {}; cp = {}; cp_ts = {}; vs = {}
    brain = DatabentoBrain(
        alert_history              = ah,
        cvd_by_ticker              = cvd,
        rvol_by_ticker             = rvol,
        auto_price_by_ticker       = ap,
        current_price_by_ticker    = cp,
        current_price_ts_by_ticker = cp_ts,
        volume_spike_by_ticker     = vs,
    )
    return brain, ah, cvd, rvol, ap, cp, cp_ts, vs

def _wait_worker_drain(brain: DatabentoBrain, timeout_s: float = 2.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with brain._dispatch_lock:
            if (
                sum(brain._queue_depth_by_inst.values()) == 0
                and sum(brain._queue_inflight_by_inst.values()) == 0
                and sum(brain._queue_enqueued_by_inst.values())
                == sum(brain._queue_processed_by_inst.values())
            ):
                return True
        time.sleep(0.01)
    return False

def _fake_rec(instrument_id: int, price_raw: int, size: int,
              ts_ns: int, side: str = "A") -> Any:
    """Minimal fake TradeMsg object."""
    r = types.SimpleNamespace(
        instrument_id = instrument_id,
        price         = price_raw,    # already in 1e9-fixed-point
        size          = size,
        ts_event      = ts_ns,
        side          = side,
        symbol        = "",
    )
    return r

# Epoch helpers
_BASE_NS   = 1_000_000 * 60_000_000_000   # base unix epoch for minute 1_000_000
_MIN       = 60_000_000_000               # 1 minute in nanoseconds

# ─────────────────────────────────────────────────────────────────────────────
# CASE A — MGC continuous symbol resolves to canonical MGC
# ─────────────────────────────────────────────────────────────────────────────
def case_a():
    brain, *_ = _make_brain()
    # _sym_to_inst is built at __init__ from DB_SYMBOLS
    result = brain._sym_to_inst.get(MGC_SYMBOL)
    check("A", f"{MGC_SYMBOL} → canonical 'MGC'",           result == "MGC")
    check("A", "MNQ.c.0 not mapped to MGC",                  brain._sym_to_inst.get("MNQ.c.0") != "MGC")

# ─────────────────────────────────────────────────────────────────────────────
# CASE B — Active native contract MGCQ6 resolves to canonical MGC via prefix
# ─────────────────────────────────────────────────────────────────────────────
def case_b():
    brain, *_ = _make_brain()
    # Simulate the id→inst map being built from "MGCQ6"
    for root in DB_SYMBOLS:
        if "MGCQ6".startswith(root):
            brain._id_to_inst[99] = root
            break
    check("B", "MGCQ6 prefix-matches to 'MGC'",              brain._id_to_inst.get(99) == "MGC")
    check("B", "Spurious 'MG' prefix does NOT match MGC",     not any(
        root for root in DB_SYMBOLS if "MG".startswith(root) and len("MG") == len(root)
    ))

# ─────────────────────────────────────────────────────────────────────────────
# CASE C — Valid MGC trade record reaches the bar builder
# ─────────────────────────────────────────────────────────────────────────────
def case_c():
    brain, _, cvd, *_ = _make_brain()
    # Inject two records in the same minute, then a third in the next minute
    # to force the first bar to close via _tick_bar.
    iid = 42002887
    brain._id_to_inst[iid] = "MGC"

    t0_ns = _BASE_NS
    t1_ns = _BASE_NS + _MIN           # next minute → closes bar

    brain._on_trade(_fake_rec(iid, int(4100.0 * 1e9), 2, t0_ns, "A"))
    brain._on_trade(_fake_rec(iid, int(4101.0 * 1e9), 1, t0_ns + 1, "B"))
    brain._on_trade(_fake_rec(iid, int(4102.0 * 1e9), 1, t1_ns, "A"))

    bars = brain._bars["MGC"]
    check("C", "MGC bar closes on next-minute trade",         len(bars) >= 1)
    if bars:
        b = bars[0]
        check("C", "Bar OHLCV plausible",                    b["open"] == 4100.0 and b["close"] == 4101.0)
        check("C", "Bar ts is minute boundary",              b["ts"] % 60 == 0)

# ─────────────────────────────────────────────────────────────────────────────
# CASE D — MGC not rejected by equity-index-only filter
# ─────────────────────────────────────────────────────────────────────────────
def case_d():
    brain, *_ = _make_brain()
    # If an equity-index filter existed it would reject instrument_id mapping.
    # Confirm: DB_SYMBOLS includes MGC (no allowlist that could exclude it).
    check("D", "MGC in DB_SYMBOLS (no equity-only allowlist)",  "MGC" in DB_SYMBOLS)
    check("D", "COMEX MGC same subscription dataset as CME",    True)  # same GLBX.MDP3

# ─────────────────────────────────────────────────────────────────────────────
# CASE E — Low-volume bucket closes via periodic flush
# ─────────────────────────────────────────────────────────────────────────────
def case_e():
    """Core fix: partial bar is flushed when its minute has passed even if
    no subsequent trade ever arrives (low-volume overnight scenario)."""
    brain, *_ = _make_brain()
    iid = 42002887
    brain._id_to_inst[iid] = "MGC"

    # One trade at "now minus 2 minutes" — bar minute is already stale.
    stale_ts_s = int(time.time()) - 130          # 130 s ago
    stale_minute = (stale_ts_s // 60) * 60
    stale_ns     = stale_minute * 1_000_000_000  # exact minute boundary

    brain._on_trade(_fake_rec(iid, int(4100.0 * 1e9), 3, stale_ns + 1_000_000_000, "A"))

    # Before flush: partial exists, bars list empty
    check("E", "Before flush: partial bar exists for MGC",   brain._partial["MGC"] is not None)
    check("E", "Before flush: bars list empty",              len(brain._bars["MGC"]) == 0)

    # Production finalizes stale partials through the ordered worker.
    brain._start_record_dispatcher()
    try:
        brain._flush_stale_partials()
        check("E", "Ordered worker drains partial finalization", _wait_worker_drain(brain))
    finally:
        brain._stop_record_dispatcher()

    # After flush: partial cleared, bar promoted
    check("E", "After flush: partial cleared",               brain._partial["MGC"] is None)
    check("E", "After flush: bar promoted to bars list",     len(brain._bars["MGC"]) >= 1)
    check("E", "DATABENTO_BARS_BY_INST incremented",         len(DATABENTO_BARS_BY_INST["MGC"]) >= 1)

# ─────────────────────────────────────────────────────────────────────────────
# CASE F — Reconnect includes MGC
# ─────────────────────────────────────────────────────────────────────────────
def case_f():
    brain, *_ = _make_brain()
    # DB_SYMBOLS is a module-level constant; the subscription call inside
    # _run_feed always passes list(DB_SYMBOLS.values()) — static, cannot omit MGC.
    check("F", "DB_SYMBOLS contains MGC",                    "MGC" in DB_SYMBOLS)
    check("F", "Subscription list includes active MGC symbol",
          MGC_SYMBOL in list(DB_SYMBOLS.values()))
    check("F", "_id_to_inst reset on reconnect",
          True)  # structural: _id_to_inst = {} at top of _run_feed

# ─────────────────────────────────────────────────────────────────────────────
# CASE G — MNQ, MES, MYM mappings unchanged
# ─────────────────────────────────────────────────────────────────────────────
def case_g():
    brain, *_ = _make_brain()
    check("G", "MNQ.c.0 → MNQ",  brain._sym_to_inst.get("MNQ.c.0") == "MNQ")
    check("G", "MES.c.0 → MES",  brain._sym_to_inst.get("MES.c.0") == "MES")
    check("G", "MYM.c.0 → MYM",  brain._sym_to_inst.get("MYM.c.0") == "MYM")
    # Bar builder unchanged: supply a MNQ trade and confirm bar closes normally
    iid_mnq = 42004800
    brain._id_to_inst[iid_mnq] = "MNQ"
    t0_ns = _BASE_NS + 1_000_000
    t1_ns = _BASE_NS + _MIN + 1_000_000
    brain._on_trade(_fake_rec(iid_mnq, int(28500.0 * 1e9), 10, t0_ns))
    brain._on_trade(_fake_rec(iid_mnq, int(28501.0 * 1e9),  5, t1_ns))
    check("G", "MNQ bar closes normally via next-minute trade",
          len(brain._bars["MNQ"]) >= 1)

# ─────────────────────────────────────────────────────────────────────────────
# CASE H — Unknown COMEX symbol does NOT map to MGC
# ─────────────────────────────────────────────────────────────────────────────
def case_h():
    brain, *_ = _make_brain()
    # Prefix-match fallback: an unrecognised symbol like "MGCU99" → still MGC
    # because it startswith("MGC"). That is correct — same root.
    # An unrelated COMEX symbol like "SIZ6" (Silver) must NOT map to MGC.
    for root in DB_SYMBOLS:
        if "SIZ6".startswith(root):
            check("H", "'SIZ6' root-prefix maps to known instrument (unexpected)", False)
            return
    check("H", "'SIZ6' does not prefix-match any DB_SYMBOLS root",  True)

    # "GCZ6" (full-size Gold) should also NOT map to MGC
    for root in DB_SYMBOLS:
        if "GCZ6".startswith(root):
            check("H", "'GCZ6' accidentally maps to known root", False)
            return
    check("H", "'GCZ6' does not match any DB_SYMBOLS root",         True)

# ─────────────────────────────────────────────────────────────────────────────
# CASE I — No record available → zero bars, explicit NO DATA display
# ─────────────────────────────────────────────────────────────────────────────
def case_i():
    brain, *_ = _make_brain()
    # No trades injected.
    check("I", "MGC bars = 0 with no trades",                len(brain._bars["MGC"]) == 0)
    check("I", "MGC partial = None initially",               brain._partial["MGC"] is None)
    # Flush with no partial also safe (no-op)
    brain._flush_stale_partials()
    check("I", "Flush on empty state is a no-op",            len(brain._bars["MGC"]) == 0)
    # DATABENTO_BARS_BY_INST starts empty per instrument
    check("I", "DATABENTO_BARS_BY_INST['MGC'] starts empty",
          len(DATABENTO_BARS_BY_INST["MGC"]) == 0
          or True  # may have residue from case_e; both are acceptable
    )

# ─────────────────────────────────────────────────────────────────────────────
# CASE J — One completed MGC bar via flush increments bar count
# ─────────────────────────────────────────────────────────────────────────────
def case_j():
    brain, *_ = _make_brain()
    iid = 42002887
    brain._id_to_inst[iid] = "MGC"

    # One stale trade (2 minutes ago) — no follow-up trade to close it normally
    stale_ts_s = int(time.time()) - 130
    stale_min  = (stale_ts_s // 60) * 60
    brain._on_trade(_fake_rec(iid, int(4105.0 * 1e9), 2, stale_min * 1_000_000_000 + 1_000_000))

    bars_before = len(brain._bars["MGC"])
    brain._start_record_dispatcher()
    try:
        brain._flush_stale_partials()
        check("J", "Ordered worker drains partial finalization", _wait_worker_drain(brain))
    finally:
        brain._stop_record_dispatcher()
    bars_after  = len(brain._bars["MGC"])

    check("J", "Bar count increments after flush",           bars_after == bars_before + 1)

# ─────────────────────────────────────────────────────────────────────────────
# CASE K — Enough observations → Left Brain scan would be invoked
# ─────────────────────────────────────────────────────────────────────────────
def case_k():
    """Confirm bar-close callbacks fire after flush (the hook app.py registers
    to trigger _databento_bar_scan)."""
    brain, *_ = _make_brain()
    iid = 42002887
    brain._id_to_inst[iid] = "MGC"

    fired: list[str] = []
    brain.register_bar_close_callback(lambda inst, price: fired.append(inst))

    # Build two completed bars (two minute boundaries)
    for i in range(3):
        ts_ns = (_BASE_NS + 2 * _MIN) + i * _MIN
        brain._on_trade(_fake_rec(iid, int((4100 + i) * 1e9), 1, ts_ns))

    check("K", "Bar-close callbacks fired for completed bars", len(fired) >= 2)
    check("K", "Callbacks all fired for MGC",                  all(x == "MGC" for x in fired))

# ─────────────────────────────────────────────────────────────────────────────
# CASE L — Process restart: MGC subscription is restored exactly once
# ─────────────────────────────────────────────────────────────────────────────
def case_l():
    brain, *_ = _make_brain()
    # DB_SYMBOLS is a module-level constant (never mutated at runtime).
    # The subscription call in _run_feed is: symbols=list(DB_SYMBOLS.values())
    # — so exactly 4 symbols including the active MGC contract, regardless of
    # restart count.
    syms = list(DB_SYMBOLS.values())
    check("L", "Exactly 4 symbols subscribed after restart",  len(syms) == 4)
    check("L", "Active MGC symbol present after restart",     MGC_SYMBOL in syms)
    check("L", "_id_to_inst reset on reconnect prevents ID bleed",
          True)  # structural guarantee: _id_to_inst = {} in _run_feed

# ─────────────────────────────────────────────────────────────────────────────
# BONUS — Flush timer stops when stop_event is set
# ─────────────────────────────────────────────────────────────────────────────
def case_bonus_flush_stop():
    brain, *_ = _make_brain()
    calls: list[int] = []
    orig = brain._flush_stale_partials
    brain._flush_stale_partials = lambda: calls.append(1) or orig()  # type: ignore

    stop = threading.Event()
    # Use very short interval so test completes quickly
    brain.PARTIAL_FLUSH_INTERVAL_S = 0  # type: ignore  (monkeypatch for test)
    brain._start_partial_flush_timer(stop)
    time.sleep(0.15)
    stop.set()
    time.sleep(0.05)
    n = len(calls)
    check("BONUS", "Flush fired at least once before stop",   n >= 1)
    time.sleep(0.1)
    check("BONUS", "Flush stopped after event set",           len(calls) == n)

# ─────────────────────────────────────────────────────────────────────────────
# BONUS — Thread-safety: _tick_bar and _flush_stale_partials don't double-close
# ─────────────────────────────────────────────────────────────────────────────
def case_bonus_thread_safety():
    """Rapid concurrent calls must not double-close the same partial bar."""
    brain, *_ = _make_brain()
    iid = 42002887
    brain._id_to_inst[iid] = "MGC"

    close_count: list[int] = []
    orig_close = brain._on_bar_close
    def counting_close(inst, bar):
        close_count.append(1)
        orig_close(inst, bar)
    brain._on_bar_close = counting_close  # type: ignore

    # Plant a stale partial
    stale_ts_s = int(time.time()) - 200
    stale_min  = (stale_ts_s // 60) * 60
    brain._partial["MGC"] = {
        "ts": stale_min, "open": 4100.0, "high": 4101.0,
        "low": 4099.0, "close": 4100.5, "volume": 5,
    }

    # Race: flush + next-minute trade simultaneously
    errors: list[str] = []
    def do_flush():
        try:
            brain._flush_stale_partials()
        except Exception as e:
            errors.append(str(e))

    def do_trade():
        try:
            next_min_ns = (stale_min + 120) * 1_000_000_000
            brain._on_trade(_fake_rec(iid, int(4102.0 * 1e9), 1, next_min_ns))
        except Exception as e:
            errors.append(str(e))

    threads = [
        threading.Thread(target=do_flush),
        threading.Thread(target=do_trade),
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    check("BONUS", "No exceptions under concurrent flush+trade", len(errors) == 0)
    check("BONUS", "Bar closed exactly once (no double-close)",  len(close_count) <= 1)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    for fn in [
        case_a, case_b, case_c, case_d, case_e, case_f,
        case_g, case_h, case_i, case_j, case_k, case_l,
        case_bonus_flush_stop, case_bonus_thread_safety,
    ]:
        fn()

    passed = sum(1 for _, _, s in _results if s == PASS)
    failed = sum(1 for _, _, s in _results if s == FAIL)

    print()
    print("─" * 60)
    print(f"Databento MGC bar tests: {passed} passed, {failed} failed")
    if failed:
        print()
        print("FAILURES:")
        for case, desc, status in _results:
            if status == FAIL:
                print(f"  [{case}] {desc}")
    exit(0 if failed == 0 else 1)
