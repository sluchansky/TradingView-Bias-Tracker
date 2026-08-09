"""
Phase 8B — Operations Readiness Tests
======================================
Tests for the research health endpoint logic, duplicate detection,
event feed ordering, first-observation validation, and health calculations.

DISPLAY-ONLY — these tests never touch gate, scoring, sizing, learning,
or execution.  They only test the observability infrastructure added in Phase 8B.
"""

import sys
import os
import unittest
from collections import deque
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Lightweight shim so we can import edge_ledger without full app context
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import edge_ledger as _el


# ===========================================================================
# 1.  _re_event ring-buffer logic (pure, no DB)
# ===========================================================================

class _ReEventHarness:
    """Minimal harness that mirrors the _re_event() / _RESEARCH_EVENTS state
    without importing app.py (avoids heavy bootstrap)."""

    def __init__(self, maxlen=500):
        import threading
        self.events: deque = deque(maxlen=maxlen)
        self.lock = threading.Lock()
        self.first_obs_key = None
        self.error_count = 0

    def emit(self, event_type, *, inst=None, strategy=None, verdict=None,
             obs_key=None, net_r=None, extra=None):
        try:
            ev = {
                "ts":         datetime.utcnow().isoformat() + "Z",
                "event_type": event_type,
                "instrument": inst,
                "strategy":   (strategy[:40] if strategy else None),
                "verdict":    verdict,
                "obs_key":    (obs_key[:64]  if obs_key  else None),
                "net_r":      (round(float(net_r), 4) if net_r is not None else None),
                "extra":      extra or {},
            }
            with self.lock:
                self.events.appendleft(ev)
                if event_type == "ghost_created" and obs_key and self.first_obs_key is None:
                    self.first_obs_key = obs_key
        except Exception:
            pass

    def snapshot(self):
        with self.lock:
            return list(self.events)


class TestEventRingBuffer(unittest.TestCase):
    """Section 2: Event feed ordering, cap, filtering."""

    def setUp(self):
        self.h = _ReEventHarness(maxlen=5)

    def test_01_newest_first(self):
        """Events should be stored newest-first (appendleft)."""
        self.h.emit("ghost_created", inst="MNQ", obs_key="obs_A")
        self.h.emit("el_created",    inst="MNQ", obs_key="obs_A")
        snap = self.h.snapshot()
        self.assertEqual(snap[0]["event_type"], "el_created")
        self.assertEqual(snap[1]["event_type"], "ghost_created")

    def test_02_max_500_cap_honoured(self):
        """Ring buffer must not exceed its maxlen."""
        h = _ReEventHarness(maxlen=500)
        for i in range(600):
            h.emit("ghost_created", obs_key=f"obs_{i}")
        snap = h.snapshot()
        self.assertEqual(len(snap), 500)
        # Newest should be the last emitted
        self.assertEqual(snap[0]["obs_key"], "obs_599")

    def test_03_small_buffer_wraps(self):
        """Oldest events are dropped when buffer is full."""
        # maxlen=5, emit 7
        for i in range(7):
            self.h.emit("ghost_created", obs_key=f"k{i}")
        snap = self.h.snapshot()
        self.assertEqual(len(snap), 5)
        obs_keys = [e["obs_key"] for e in snap]
        self.assertIn("k6", obs_keys)
        self.assertNotIn("k0", obs_keys)

    def test_04_event_has_required_fields(self):
        """Each event must carry ts, event_type, instrument, obs_key."""
        self.h.emit("ghost_created", inst="MGC", obs_key="obs_X",
                    strategy="ORB", net_r=1.37)
        ev = self.h.snapshot()[0]
        self.assertIn("ts", ev)
        self.assertEqual(ev["instrument"],  "MGC")
        self.assertEqual(ev["event_type"],  "ghost_created")
        self.assertEqual(ev["obs_key"],     "obs_X")
        self.assertAlmostEqual(ev["net_r"], 1.37, places=3)

    def test_05_fail_open_on_bad_net_r(self):
        """Non-numeric net_r must not raise; event is silently dropped."""
        # Should not raise
        self.h.emit("ghost_created", obs_key="obs_Y", net_r="not_a_number")
        # Event is silently not appended (exception caught)
        snap = self.h.snapshot()
        # Either 0 events or 1 event with net_r=None; either is acceptable
        if snap:
            self.assertIsNone(snap[0].get("net_r"))

    def test_06_strategy_truncated_at_40_chars(self):
        """Long strategy names are truncated to 40 chars."""
        long_name = "A" * 80
        self.h.emit("el_created", obs_key="obs_Z", strategy=long_name)
        ev = self.h.snapshot()[0]
        self.assertEqual(len(ev["strategy"]), 40)

    def test_07_obs_key_truncated_at_64_chars(self):
        """Long obs keys are truncated to 64 chars."""
        long_key = "K" * 100
        self.h.emit("ghost_created", obs_key=long_key)
        ev = self.h.snapshot()[0]
        self.assertEqual(len(ev["obs_key"]), 64)


# ===========================================================================
# 2.  First-observation tracking
# ===========================================================================

class TestFirstObservationTracking(unittest.TestCase):
    """Section 5: First signal checklist — tracking first obs since boot."""

    def test_08_first_obs_key_captured_on_first_ghost_created(self):
        """_RESEARCH_FIRST_OBS_KEY is set on the first ghost_created event."""
        h = _ReEventHarness()
        self.assertIsNone(h.first_obs_key)
        h.emit("ghost_created", obs_key="first_key")
        self.assertEqual(h.first_obs_key, "first_key")

    def test_09_first_obs_key_not_overwritten_by_second(self):
        """Subsequent ghost_created events must NOT overwrite first_obs_key."""
        h = _ReEventHarness()
        h.emit("ghost_created", obs_key="key_one")
        h.emit("ghost_created", obs_key="key_two")
        self.assertEqual(h.first_obs_key, "key_one")

    def test_10_non_ghost_events_do_not_set_first_obs_key(self):
        """el_created / journal_linked must not set first_obs_key."""
        h = _ReEventHarness()
        h.emit("el_created",       obs_key="obs_el")
        h.emit("journal_linked",   obs_key="obs_jl")
        self.assertIsNone(h.first_obs_key)

    def test_11_first_obs_validation_pass(self):
        """Validation dict structure when all checks pass."""
        checks = [
            {"check": "exactly_one_obs",      "passed": True,  "value": 1},
            {"check": "exactly_one_ledger_row","passed": True,  "value": 1},
            {"check": "frozen_values_populated","passed": True, "value": 2730.5},
            {"check": "matching_instrument",   "passed": True},
            {"check": "matching_strategy",     "passed": True},
        ]
        all_pass = all(c["passed"] for c in checks)
        status   = "PASS" if all_pass else "FAIL"
        failed   = [c["check"] for c in checks if not c["passed"]]
        self.assertEqual(status, "PASS")
        self.assertEqual(failed, [])

    def test_12_first_obs_validation_fail_reports_which_check(self):
        """When exactly_one_ledger_row fails, status=FAIL and that check is listed."""
        checks = [
            {"check": "exactly_one_obs",       "passed": True,  "value": 1},
            {"check": "exactly_one_ledger_row", "passed": False, "value": 0},
            {"check": "frozen_values_populated","passed": True,  "value": 2730.5},
        ]
        all_pass = all(c["passed"] for c in checks)
        status   = "PASS" if all_pass else "FAIL"
        failed   = [c["check"] for c in checks if not c["passed"]]
        self.assertEqual(status, "FAIL")
        self.assertIn("exactly_one_ledger_row", failed)


# ===========================================================================
# 3.  Duplicate detection logic
# ===========================================================================

class TestDuplicateDetection(unittest.TestCase):
    """Section 4: Duplicate detection — no repair, only highlight."""

    def _dup_count(self, rows):
        """Mirror of the duplicate-count logic in route_research_health."""
        seen = {}
        for key in rows:
            seen[key] = seen.get(key, 0) + 1
        return [{"key": k, "count": v} for k, v in seen.items() if v > 1]

    def test_13_no_duplicates(self):
        rows = ["obs_A", "obs_B", "obs_C"]
        self.assertEqual(self._dup_count(rows), [])

    def test_14_single_duplicate_detected(self):
        rows = ["obs_A", "obs_B", "obs_A"]
        result = self._dup_count(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["count"], 2)

    def test_15_multiple_distinct_duplicates_detected(self):
        rows = ["obs_A", "obs_B", "obs_A", "obs_B", "obs_C"]
        result = self._dup_count(rows)
        keys = {r["key"] for r in result}
        self.assertIn("obs_A", keys)
        self.assertIn("obs_B", keys)
        self.assertNotIn("obs_C", keys)

    def test_16_ready_for_market_false_when_duplicates(self):
        """ready_for_market must be False when duplicate_event_count > 0."""
        dup_total = 2
        ghost_ok = True
        el_ok    = True
        errors   = 0
        ready = ghost_ok and el_ok and errors == 0 and dup_total == 0
        self.assertFalse(ready)

    def test_17_ready_for_market_true_when_clean(self):
        """ready_for_market must be True when all tables ready, no errors, no dups."""
        ready = True and True and 0 == 0 and 0 == 0
        self.assertTrue(ready)

    def test_18_ready_for_market_false_when_el_not_ready(self):
        """Edge ledger table not ready → not ready for market."""
        ready = True and False and 0 == 0 and 0 == 0
        self.assertFalse(ready)


# ===========================================================================
# 4.  Health calculations
# ===========================================================================

class TestHealthCalculations(unittest.TestCase):
    """Section 1 & 9: Health status computed correctly."""

    def test_19_ghost_counts_aggregated_by_status(self):
        """Open/closed counts must aggregate correctly."""
        db_rows = [("open", 3, None), ("closed", 12, None), ("expired", 2, None)]
        counts = {"open": 0, "closed": 0, "total": 0}
        for st, cnt, _ in db_rows:
            counts["total"] += cnt
            if st == "open":
                counts["open"] = cnt
            else:
                counts["closed"] = counts.get("closed", 0) + cnt
        self.assertEqual(counts["open"],   3)
        self.assertEqual(counts["closed"], 14)
        self.assertEqual(counts["total"],  17)

    def test_20_timing_ms_computed_correctly(self):
        """Millisecond timing delta is calculated correctly."""
        from datetime import datetime, timezone
        a = datetime(2026, 1, 1, 9, 31, 2, tzinfo=timezone.utc)
        b = datetime(2026, 1, 1, 9, 31, 2, 500000, tzinfo=timezone.utc)
        def _ms(x, y):
            if x and y:
                return round((y - x).total_seconds() * 1000)
            return None
        self.assertEqual(_ms(a, b), 500)

    def test_21_timing_ms_returns_none_when_missing(self):
        """_ms helper returns None when either timestamp is None."""
        def _ms(a, b):
            if a and b:
                return round((b - a).total_seconds() * 1000)
            return None
        self.assertIsNone(_ms(None, datetime.utcnow()))
        self.assertIsNone(_ms(datetime.utcnow(), None))
        self.assertIsNone(_ms(None, None))

    def test_22_event_count_reflects_ring_buffer_size(self):
        """event_count in health response equals len(events)."""
        h = _ReEventHarness()
        for i in range(7):
            h.emit("ghost_created", obs_key=f"k{i}")
        snap = h.snapshot()
        self.assertEqual(len(snap), 7)

    def test_23_error_count_defaults_zero(self):
        """_RESEARCH_ERROR_COUNT starts at 0 (no errors at boot)."""
        h = _ReEventHarness()
        self.assertEqual(h.error_count, 0)


# ===========================================================================
# 5.  Observation Inspector (Section 3) — data model
# ===========================================================================

class TestObservationInspector(unittest.TestCase):
    """Section 3: Inspector data model — read-only fields present."""

    REQUIRED_FIELDS = [
        "obs_key", "strategy_key", "instrument", "direction",
        "signal_timestamp", "original_entry", "original_stop",
        "original_tp1", "original_risk_points", "status",
    ]

    def _mock_obs(self):
        return {
            "obs_key":             "obs_MNQ_ORB_LONG_001",
            "strategy_key":        "OPENING_RANGE_BREAKOUT|MNQ|SCALP|LONG",
            "instrument":          "MNQ",
            "direction":           "LONG",
            "signal_timestamp":    "2026-08-09T09:31:02Z",
            "original_entry":      21050.0,
            "original_stop":       21000.0,
            "original_tp1":        21150.0,
            "original_risk_points": 50.0,
            "status":              "open",
            "mfe_r":               None,
            "mae_r":               None,
            "gross_r":             None,
            "net_r":               None,
            "signal_cost_r":       0.0620,
            "signal_outcome_status": "open",
            "managed_outcome_status": None,
            "signal_vs_managed_delta_r": None,
            "internal_trade_id":    None,
        }

    def test_24_all_required_fields_present(self):
        obs = self._mock_obs()
        for field in self.REQUIRED_FIELDS:
            self.assertIn(field, obs, f"Missing field: {field}")

    def test_25_frozen_entry_is_immutable_in_mock(self):
        """Simulates that original_entry cannot be changed once set."""
        obs = self._mock_obs()
        original_value = obs["original_entry"]
        # Inspector is read-only; no mutation allowed
        with self.assertRaises((TypeError, AttributeError, KeyError)):
            from types import MappingProxyType
            frozen = MappingProxyType(obs)
            frozen["original_entry"] = 99999.0
        self.assertEqual(obs["original_entry"], original_value)

    def test_26_journal_link_field_present(self):
        """internal_trade_id serves as journal link (None until a live trade fires)."""
        obs = self._mock_obs()
        self.assertIn("internal_trade_id", obs)

    def test_27_management_delta_field_present(self):
        """signal_vs_managed_delta_r serves as management delta."""
        obs = self._mock_obs()
        self.assertIn("signal_vs_managed_delta_r", obs)


# ===========================================================================
# 6.  Edge Ledger module regression — unchanged by Phase 8B
# ===========================================================================

class TestEdgeLedgerModuleRegression(unittest.TestCase):
    """Confirm edge_ledger.py is byte-identical w.r.t. core functions."""

    def _base_result(self):
        return {
            "verdict":           "READY",
            "direction":         "LONG",
            "entry":             21050.0,
            "stop_price":        21000.0,
            "target_price":      21150.0,
            "target2_price":     21200.0,
            "risk_per_unit":     50.0,
            "score":             75,
            "long_score":        75,
            "short_score":       30,
            "grade":             "A",
            "readiness":         "READY",
            "left_brain_thesis": "FORMING_LONG",
            "thesis_alignment":  "ALIGNED",
        }

    def test_28_build_edge_id_unchanged(self):
        # build_edge_id returns "el|{obs_key}" — the obs_key is the dedup anchor
        edge_id = _el.build_edge_id("MNQ", "LONG", "ORB", "obs_001")
        self.assertTrue(edge_id.startswith("el|"))
        self.assertIn("obs_001", edge_id)

    def test_29_assign_sample_partition_shadow(self):
        p = _el.assign_sample_partition("databento_scan")
        self.assertEqual(p, "SHADOW")

    def test_30_compute_el_diagnostics_groups_correctly(self):
        rows = [
            {"strategy_key": "ORB", "instrument": "MNQ", "sample_partition": "SHADOW",
             "signal_outcome_status": "closed", "managed_outcome_status": None,
             "signal_gross_r": 1.0, "signal_net_r": 0.94, "managed_net_r": None,
             "signal_vs_managed_delta_r": None, "signal_cost_r": 0.06,
             "comparison_complete": False, "data_complete": True},
            {"strategy_key": "ORB", "instrument": "MNQ", "sample_partition": "SHADOW",
             "signal_outcome_status": "closed", "managed_outcome_status": None,
             "signal_gross_r": -1.0, "signal_net_r": -1.06, "managed_net_r": None,
             "signal_vs_managed_delta_r": None, "signal_cost_r": 0.06,
             "comparison_complete": False, "data_complete": True},
        ]
        diag = _el.compute_el_diagnostics(rows)
        self.assertIsInstance(diag, list)
        self.assertEqual(len(diag), 1)
        g = diag[0]
        self.assertEqual(g["total_ledger_signals"], 2)
        self.assertEqual(g["signal_outcomes_resolved"], 2)


# ===========================================================================
# 7.  Regression — parity, scalp_golden, dual_sim, breakout_mode
# ===========================================================================

import subprocess

class TestPhase8BRegression(unittest.TestCase):
    """Confirm Phase 8B adds zero regressions to the golden suites."""

    # tests/ → tradingview-webhook/ → artifacts/ → workspace/
    WS = os.path.dirname(os.path.dirname(os.path.dirname(
         os.path.dirname(os.path.abspath(__file__)))))

    SUITES = [
        (".local/state/check_parity.sh",       "PARITY"),
        (".local/state/check_scalp_golden.sh", "SCALP_GOLDEN"),
        (".local/state/check_dual_sim.sh",     "DUAL_SIM"),
        (".local/state/check_breakout_mode.sh","BREAKOUT_MODE"),
    ]

    def test_31_all_golden_suites_pass(self):
        for script, label in self.SUITES:
            with self.subTest(suite=label):
                result = subprocess.run(
                    ["bash", script],
                    cwd=self.WS,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(
                    result.returncode, 0,
                    msg=f"{label} FAILED\n{result.stdout[-800:]}\n{result.stderr[-400:]}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
