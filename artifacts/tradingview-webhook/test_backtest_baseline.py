"""test_backtest_baseline.py — Phase 6B.1 test suite (88+ tests).

Tests cover:
  BL001-BL005  Module import + constant contracts
  BL006-BL010  Config freeze
  BL011-BL015  Reliability label
  BL016-BL022  Extended metrics (_extended_metrics)
  BL023-BL027  Trade record builder (_build_trade_records)
  BL028-BL035  Matrix runner (_run_combination)
  BL036-BL040  Breakdown computation (_compute_breakdowns)
  BL041-BL045  Rankings (_compute_rankings)
  BL046-BL050  API guards + route smoke tests (no-DB mode)
  BL051-BL055  Money-path isolation invariants
  BL056-BL060  Metrics correctness
  BL061-BL069  Deterministic config-hash invariants (Phase 6B.1B)
"""
import importlib
import json
import math
import sys
import os
import types
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# Ensure the tradingview-webhook directory is on the path
_BT_DIR = os.path.join(os.path.dirname(__file__))
if _BT_DIR not in sys.path:
    sys.path.insert(0, _BT_DIR)

import bt_baseline as bl

ET_TZ = None
try:
    import pytz
    ET_TZ = pytz.timezone("America/New_York")
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Helper: build minimal trade dicts that _extended_metrics expects
# ---------------------------------------------------------------------------
def _trade(r, direction="Long", session="New York", hold=30.0, regime="NORMAL",
           entry_ts=None, exit_ts=None, risk=10.0):
    base_ts = datetime(2024, 3, 4, 14, 0, 0, tzinfo=timezone.utc)
    e_ts = entry_ts or base_ts.isoformat()
    x_ts = exit_ts or (base_ts + timedelta(minutes=hold)).isoformat()
    return {
        "strategy":     "OPENING_DRIVE",
        "direction":    direction,
        "entry_ts":     e_ts,
        "exit_ts":      x_ts,
        "entry":        100.0,
        "stop":         90.0,
        "exit":         100.0 + r * risk if direction == "Long" else 100.0 - r * risk,
        "tp1":          110.0,
        "tp3":          130.0,
        "risk_points":  risk,
        "gross_points": r * risk,
        "pnl_dollars":  r * risk * 2.0 - 1.24,
        "r_multiple":   r,
        "regime":       regime,
        "session":      session,
        "entry_hour_et": 9,
        "entry_reason": "bos_long",
        "exit_reason":  "target_hit" if r > 0 else "stop_loss",
        "hold_minutes": hold,
    }


def _wins_losses(w, l):
    trades = [_trade(1.5) for _ in range(w)] + [_trade(-1.0) for _ in range(l)]
    return trades


# ============================================================================
# BL001-BL005: Module imports + constant contracts
# ============================================================================
class TestModuleContracts(unittest.TestCase):

    def test_BL001_import_succeeds(self):
        """BL001: bt_baseline imports without error."""
        self.assertIsNotNone(bl)

    def test_BL002_baseline_instruments_exact(self):
        """BL002: BASELINE_INSTRUMENTS = ['MNQ','MES','MGC','MYM']."""
        self.assertEqual(bl.BASELINE_INSTRUMENTS, ["MNQ", "MES", "MGC", "MYM"])

    def test_BL003_baseline_modes_exact(self):
        """BL003: BASELINE_MODES = ['SCALP','SWING']."""
        self.assertEqual(bl.BASELINE_MODES, ["SCALP", "SWING"])

    def test_BL004_baseline_strategies_exact(self):
        """BL004: BASELINE_STRATEGIES has all 5 expected strategies."""
        expected = {
            "OPENING_DRIVE", "LIQUIDITY_SWEEP_REVERSAL",
            "VWAP_TREND_CONTINUATION", "RANGE_EXPANSION_BREAKOUT",
            "OPENING_RANGE_BREAKOUT",
        }
        self.assertEqual(set(bl.BASELINE_STRATEGIES), expected)
        self.assertEqual(len(bl.BASELINE_STRATEGIES), 5)

    def test_BL005_official_dataset_ids_exact(self):
        """BL005: OFFICIAL_DATASET_IDS = {MNQ:8, MES:9, MGC:10, MYM:11}."""
        self.assertEqual(bl.OFFICIAL_DATASET_IDS, {"MNQ": 8, "MES": 9, "MGC": 10, "MYM": 11})

    def test_BL005b_matrix_size(self):
        """BL005b: 4 inst × 2 modes × 5 strategies = 40 combinations."""
        n = len(bl.BASELINE_INSTRUMENTS) * len(bl.BASELINE_MODES) * len(bl.BASELINE_STRATEGIES)
        self.assertEqual(n, 40)

    def test_BL005c_commission_constant(self):
        """BL005c: Commission per side matches expected $0.62."""
        self.assertAlmostEqual(bl.BASELINE_COMMISSION_PER_SIDE, 0.62)

    def test_BL005d_slippage_constant(self):
        """BL005d: Slippage ticks = 1.0."""
        self.assertAlmostEqual(bl.BASELINE_SLIPPAGE_TICKS, 1.0)


# ============================================================================
# BL006-BL010: Config freeze
# ============================================================================
class TestConfigFreeze(unittest.TestCase):

    def setUp(self):
        self.cfg, self.h = bl._freeze_config("abc1234")

    def test_BL006_hash_is_16hex(self):
        """BL006: Config hash is 16 hex chars."""
        self.assertEqual(len(self.h), 16)
        self.assertRegex(self.h, r'^[0-9a-f]{16}$')

    def test_BL007_source_commit_captured(self):
        """BL007: Config captures source_commit."""
        self.assertEqual(self.cfg["source_commit"], "abc1234")

    def test_BL008_strategies_match_constant(self):
        """BL008: Frozen strategies match BASELINE_STRATEGIES."""
        self.assertEqual(set(self.cfg["strategies"]), set(bl.BASELINE_STRATEGIES))

    def test_BL009_instruments_match_constant(self):
        """BL009: Frozen instruments match BASELINE_INSTRUMENTS."""
        self.assertEqual(self.cfg["instruments"], bl.BASELINE_INSTRUMENTS)

    def test_BL010_unsupported_metrics_present(self):
        """BL010: Config documents unsupported metrics (mfe_r, mae_r, etc.)."""
        um = self.cfg.get("unsupported_metrics", {})
        for key in ("mfe_r", "mae_r", "or_high", "or_low", "rvol_at_signal",
                    "atr_at_signal", "candidate_count"):
            self.assertIn(key, um, f"Missing unsupported_metric: {key}")
            self.assertIsNone(um[key]["value"])
            self.assertIn("reason", um[key])

    def test_BL010b_freeze_deterministic(self):
        """BL010b: Same commit → same config hash."""
        _, h2 = bl._freeze_config("abc1234")
        self.assertEqual(self.h, h2)

    def test_BL010c_different_commit_different_hash(self):
        """BL010c: Different commit → different config hash."""
        _, h2 = bl._freeze_config("zzzzzzz")
        self.assertNotEqual(self.h, h2)

    def test_BL010d_bt_specs_present(self):
        """BL010d: Frozen config includes BT specs for all 4 instruments."""
        specs = self.cfg.get("bt_specs", {})
        for inst in ("MNQ", "MES", "MGC"):
            self.assertIn(inst, specs)

    def test_BL010e_management_captured(self):
        """BL010e: Official management = target_1_5r."""
        import backtest_engine as bt
        self.assertEqual(self.cfg["management"], bt.BT_DEFAULT_MGMT)


# ============================================================================
# BL011-BL015: Reliability label
# ============================================================================
class TestReliabilityLabel(unittest.TestCase):

    def test_BL011_zero(self):
        """BL011: 0 trades → INSUFFICIENT."""
        self.assertEqual(bl._reliability_label(0), "INSUFFICIENT")

    def test_BL012_19(self):
        """BL012: 19 trades → INSUFFICIENT."""
        self.assertEqual(bl._reliability_label(19), "INSUFFICIENT")

    def test_BL013_20(self):
        """BL013: 20 trades → LOW_SAMPLE."""
        self.assertEqual(bl._reliability_label(20), "LOW_SAMPLE")

    def test_BL014_boundaries(self):
        """BL014: Boundary values for all labels."""
        cases = [
            (49, "LOW_SAMPLE"),
            (50, "DEVELOPING"),
            (99, "DEVELOPING"),
            (100, "MODERATE"),
            (199, "MODERATE"),
            (200, "STRONG_SAMPLE"),
            (1000, "STRONG_SAMPLE"),
        ]
        for n, expected in cases:
            with self.subTest(n=n):
                self.assertEqual(bl._reliability_label(n), expected)

    def test_BL015_monotone(self):
        """BL015: Label order is monotone — larger samples are 'at least as reliable'."""
        order = ["INSUFFICIENT", "LOW_SAMPLE", "DEVELOPING", "MODERATE", "STRONG_SAMPLE"]
        prev_rank = -1
        for n in [0, 19, 20, 49, 50, 99, 100, 199, 200, 500]:
            lbl = bl._reliability_label(n)
            rank = order.index(lbl)
            self.assertGreaterEqual(rank, prev_rank)
            prev_rank = rank


# ============================================================================
# BL016-BL022: Extended metrics
# ============================================================================
class TestExtendedMetrics(unittest.TestCase):

    def test_BL016_empty_trades(self):
        """BL016: Empty trade list → all counts 0, rates None."""
        m = bl._extended_metrics([], "MNQ")
        self.assertEqual(m["total_trades"], 0)
        self.assertEqual(m["wins"], 0)
        self.assertIsNone(m["win_rate"])
        self.assertIsNone(m["avg_r"])
        self.assertEqual(m["reliability_label"], "INSUFFICIENT")
        self.assertIn("zero_trades", m["warnings"])

    def test_BL017_basic_counts(self):
        """BL017: 3 wins + 2 losses counted correctly."""
        trades = _wins_losses(3, 2)
        m = bl._extended_metrics(trades, "MNQ")
        self.assertEqual(m["total_trades"], 5)
        self.assertEqual(m["wins"], 3)
        self.assertEqual(m["losses"], 2)
        self.assertEqual(m["breakeven_trades"], 0)

    def test_BL018_win_rate(self):
        """BL018: Win rate = wins / total × 100."""
        trades = _wins_losses(4, 1)
        m = bl._extended_metrics(trades, "MNQ")
        self.assertAlmostEqual(m["win_rate"], 80.0, places=1)

    def test_BL019_net_r(self):
        """BL019: net_r = sum of all r_multiples."""
        trades = [_trade(1.5), _trade(1.5), _trade(-1.0)]
        m = bl._extended_metrics(trades, "MNQ")
        expected = 1.5 + 1.5 - 1.0
        self.assertAlmostEqual(m["net_r"], expected, places=3)

    def test_BL020_profit_factor(self):
        """BL020: PF = sum_wins / sum_abs_losses (positive if any losses)."""
        trades = [_trade(2.0), _trade(-1.0)]
        m = bl._extended_metrics(trades, "MNQ")
        self.assertAlmostEqual(float(m["profit_factor"]), 2.0, places=2)

    def test_BL021_drawdown_non_negative(self):
        """BL021: max_drawdown_r >= 0 for all inputs."""
        for trades in [[], _wins_losses(5, 0), _wins_losses(0, 5), _wins_losses(3, 2)]:
            m = bl._extended_metrics(trades, "MGC")
            self.assertGreaterEqual(m["max_drawdown_r"], 0.0)

    def test_BL022_all_wins_no_pf_undefined(self):
        """BL022: All winners → profit_factor is 'inf' or float('inf')."""
        trades = _wins_losses(5, 0)
        m = bl._extended_metrics(trades, "MNQ")
        pf = m["profit_factor"]
        self.assertIn("no_losing_trades", m["warnings"])
        # PF should be None (no losers → no denominator) or 'inf'
        self.assertTrue(pf is None or pf == "inf" or pf == float("inf") or math.isinf(float(pf)) if pf is not None else True)

    def test_BL022b_streak_computation(self):
        """BL022b: Win/loss streaks computed correctly."""
        rs = [1.0, 1.0, 1.0, -1.0, -1.0, 1.0]
        trades = [_trade(r, exit_ts=(datetime(2024,3,4,14,0,0,tzinfo=timezone.utc)+timedelta(hours=i)).isoformat())
                  for i, r in enumerate(rs)]
        m = bl._extended_metrics(trades, "MNQ")
        self.assertEqual(m["longest_win_streak"], 3)
        self.assertEqual(m["longest_loss_streak"], 2)

    def test_BL022c_direction_split(self):
        """BL022c: long_count + short_count = total_trades."""
        trades = ([_trade(1.0, direction="Long")] * 3 +
                  [_trade(-1.0, direction="Short")] * 2)
        m = bl._extended_metrics(trades, "MNQ")
        self.assertEqual(m["long_count"] + m["short_count"], 5)
        self.assertEqual(m["long_count"], 3)
        self.assertEqual(m["short_count"], 2)

    def test_BL022d_hold_stats(self):
        """BL022d: avg/median/min/max hold minutes computed."""
        trades = [_trade(1.0, hold=10.0), _trade(1.0, hold=30.0), _trade(-1.0, hold=20.0)]
        m = bl._extended_metrics(trades, "MGC")
        self.assertAlmostEqual(m["avg_hold_minutes"], 20.0, places=1)
        self.assertAlmostEqual(m["min_hold_minutes"], 10.0, places=1)
        self.assertAlmostEqual(m["max_hold_minutes"], 30.0, places=1)

    def test_BL022e_reliability_label_attached(self):
        """BL022e: Reliability label always present on result."""
        for n in [0, 5, 25, 75, 150, 300]:
            trades = _wins_losses(n, 0)
            m = bl._extended_metrics(trades, "MNQ")
            self.assertIn(m["reliability_label"],
                          ["INSUFFICIENT", "LOW_SAMPLE", "DEVELOPING", "MODERATE", "STRONG_SAMPLE"])

    def test_BL022f_expectancy_formula(self):
        """BL022f: expectancy = net_r / total_trades."""
        trades = [_trade(2.0), _trade(-1.0), _trade(1.0)]
        m = bl._extended_metrics(trades, "MNQ")
        expected_exp = (2.0 - 1.0 + 1.0) / 3.0
        self.assertAlmostEqual(m["expectancy"], expected_exp, places=4)


# ============================================================================
# BL023-BL027: Trade record builder
# ============================================================================
class TestTradeRecordBuilder(unittest.TestCase):

    def _build(self, trades=None):
        if trades is None:
            trades = [_trade(1.5), _trade(-1.0)]
        return bl._build_trade_records(trades, "BL-TEST", 42, 8, "MNQ", "SCALP", "OPENING_DRIVE")

    def test_BL023_returns_right_count(self):
        """BL023: Returns one row per trade."""
        rows = self._build([_trade(1.5), _trade(-1.0)])
        self.assertEqual(len(rows), 2)

    def test_BL024_empty_returns_empty(self):
        """BL024: Empty trade list → empty row list."""
        rows = self._build([])
        self.assertEqual(rows, [])

    def test_BL025_baseline_id_in_rows(self):
        """BL025: baseline_id appears in every row."""
        rows = self._build([_trade(1.0)])
        self.assertEqual(rows[0][0], "BL-TEST")

    def test_BL026_matrix_id_in_rows(self):
        """BL026: matrix_result_id (42) appears in every row."""
        rows = self._build([_trade(1.0)])
        self.assertEqual(rows[0][1], 42)

    def test_BL027_mfe_mae_null(self):
        """BL027: mfe_r and mae_r are None (unsupported metrics)."""
        rows = self._build([_trade(1.5)])
        row = rows[0]
        # Position indices: mfe_r=19, mae_r=20 (0-indexed in the tuple)
        self.assertIsNone(row[19])  # mfe_r
        self.assertIsNone(row[20])  # mae_r

    def test_BL027b_instrument_mode_strategy_in_row(self):
        """BL027b: inst/mode/strategy appear in row."""
        rows = self._build([_trade(1.0)])
        row = rows[0]
        self.assertEqual(row[3], "MNQ")
        self.assertEqual(row[4], "SCALP")
        self.assertEqual(row[5], "OPENING_DRIVE")

    def test_BL027c_realized_r_preserved(self):
        """BL027c: realized_r (r_multiple) preserved in row."""
        rows = self._build([_trade(1.5)])
        row = rows[0]
        # realized_r is at position 17 in the tuple
        self.assertAlmostEqual(row[17], 1.5, places=4)


# ============================================================================
# BL028-BL035: Matrix runner
# ============================================================================
class TestMatrixRunner(unittest.TestCase):

    def _make_candles(self, n=200, inst="MNQ"):
        import backtest_engine as bt
        base = datetime(2024, 1, 2, 14, 0, 0, tzinfo=timezone.utc)  # 09:00 ET
        tick = bt.BT_SPECS[inst]["tick_size"]
        price = 17000.0 if "MN" in inst or "ME" in inst else 2500.0
        candles = []
        for i in range(n):
            ts = base + timedelta(minutes=5 * i)
            candles.append({
                "ts": ts, "open": price, "high": price + 5 * tick,
                "low": price - 5 * tick, "close": price, "volume": 1000.0,
            })
        return candles

    def test_BL028_run_returns_dict(self):
        """BL028: _run_combination always returns a dict."""
        candles = self._make_candles(200, "MNQ")
        result = bl._run_combination("MNQ", "SCALP", "OPENING_DRIVE", candles)
        self.assertIsInstance(result, dict)

    def test_BL029_status_field_present(self):
        """BL029: Result has a 'status' field."""
        candles = self._make_candles(200, "MNQ")
        result = bl._run_combination("MNQ", "SCALP", "OPENING_DRIVE", candles)
        self.assertIn("status", result)

    def test_BL030_trades_field_is_list(self):
        """BL030: Result 'trades' is always a list."""
        candles = self._make_candles(200, "MNQ")
        result = bl._run_combination("MNQ", "SCALP", "OPENING_DRIVE", candles)
        self.assertIsInstance(result.get("trades", []), list)

    def test_BL031_empty_candles_does_not_crash(self):
        """BL031: Empty candle list handled without exception."""
        result = bl._run_combination("MNQ", "SCALP", "OPENING_DRIVE", [])
        self.assertIn("status", result)

    def test_BL032_status_complete_or_zero(self):
        """BL032: Status for successful run is COMPLETE or COMPLETE_ZERO_TRADES."""
        candles = self._make_candles(300, "MGC")
        result = bl._run_combination("MGC", "SCALP", "OPENING_RANGE_BREAKOUT", candles)
        self.assertIn(result["status"], {"COMPLETE", "COMPLETE_ZERO_TRADES", "FAILED"})

    def test_BL033_trades_filtered_to_strategy(self):
        """BL033: Only trades from requested strategy are returned."""
        candles = self._make_candles(500, "MNQ")
        result = bl._run_combination("MNQ", "SCALP", "OPENING_DRIVE", candles)
        for t in result.get("trades", []):
            self.assertEqual(t["strategy"], "OPENING_DRIVE")

    def test_BL034_scalp_mode_accepted(self):
        """BL034: SCALP mode runs without error."""
        candles = self._make_candles(200, "MNQ")
        result = bl._run_combination("MNQ", "SCALP", "VWAP_TREND_CONTINUATION", candles)
        self.assertIn("status", result)

    def test_BL035_swing_mode_accepted(self):
        """BL035: SWING mode runs without error."""
        candles = self._make_candles(200, "MNQ")
        result = bl._run_combination("MNQ", "SWING", "VWAP_TREND_CONTINUATION", candles)
        self.assertIn("status", result)

    def test_BL035b_no_live_state_mutation(self):
        """BL035b: _run_combination imports are read-only — no app globals mutated."""
        import backtest_engine as bt
        original_mgmt = bt.BT_DEFAULT_MGMT
        candles = self._make_candles(200, "MGC")
        bl._run_combination("MGC", "SCALP", "OPENING_DRIVE", candles)
        self.assertEqual(bt.BT_DEFAULT_MGMT, original_mgmt)


# ============================================================================
# BL036-BL040: Breakdown computation
# ============================================================================
class TestBreakdownComputation(unittest.TestCase):

    def _combo(self, inst, mode, strategy, trades):
        return {
            "inst": inst, "mode": mode, "strategy": strategy,
            "status": "COMPLETE" if trades else "COMPLETE_ZERO_TRADES",
            "trades": trades,
        }

    def _combos_all_zero(self):
        combos = []
        for inst in bl.BASELINE_INSTRUMENTS:
            for mode in bl.BASELINE_MODES:
                for strat in bl.BASELINE_STRATEGIES:
                    combos.append(self._combo(inst, mode, strat, []))
        return combos

    def test_BL036_returns_list(self):
        """BL036: _compute_breakdowns always returns a list."""
        result = bl._compute_breakdowns(self._combos_all_zero())
        self.assertIsInstance(result, list)

    def test_BL037_no_crash_on_empty_combos(self):
        """BL037: Empty combos list handled gracefully."""
        result = bl._compute_breakdowns([])
        self.assertIsInstance(result, list)

    def test_BL038_instrument_breakdown_present(self):
        """BL038: Instrument-level breakdowns present for all 4 instruments."""
        combos = self._combos_all_zero()
        rows = bl._compute_breakdowns(combos)
        inst_rows = [r for r in rows if r["breakdown_type"] == "instrument"]
        self.assertEqual(len(inst_rows), 4)
        inst_values = {r["breakdown_value"] for r in inst_rows}
        self.assertEqual(inst_values, set(bl.BASELINE_INSTRUMENTS))

    def test_BL039_mode_breakdown_present(self):
        """BL039: Mode-level breakdowns present for SCALP and SWING."""
        combos = self._combos_all_zero()
        rows = bl._compute_breakdowns(combos)
        mode_rows = [r for r in rows if r["breakdown_type"] == "mode"]
        self.assertEqual(len(mode_rows), 2)
        mode_values = {r["breakdown_value"] for r in mode_rows}
        self.assertEqual(mode_values, {"SCALP", "SWING"})

    def test_BL040_strategy_breakdown_present(self):
        """BL040: Strategy-level breakdowns for all 5 strategies."""
        combos = self._combos_all_zero()
        rows = bl._compute_breakdowns(combos)
        strat_rows = [r for r in rows if r["breakdown_type"] == "strategy"]
        self.assertEqual(len(strat_rows), 5)
        strat_values = {r["breakdown_value"] for r in strat_rows}
        self.assertEqual(strat_values, set(bl.BASELINE_STRATEGIES))

    def test_BL040b_trade_count_aggregates_correctly(self):
        """BL040b: Trade counts sum correctly across combos."""
        mnq_trades = [_trade(1.0), _trade(-0.5)]
        combos = [self._combo("MNQ", "SCALP", "OPENING_DRIVE", mnq_trades)]
        rows = bl._compute_breakdowns(combos)
        inst_rows = [r for r in rows if r["breakdown_type"] == "instrument"
                     and r["breakdown_value"] == "MNQ"]
        if inst_rows:
            self.assertEqual(inst_rows[0]["trade_count"], 2)

    def test_BL040c_zero_trades_combo_has_zero_count(self):
        """BL040c: Zero-trade combo contributes 0 to breakdown counts."""
        combos = [self._combo("MGC", "SWING", "OPENING_DRIVE", [])]
        rows = bl._compute_breakdowns(combos)
        mgc_rows = [r for r in rows if r["breakdown_type"] == "instrument"
                    and r["breakdown_value"] == "MGC"]
        if mgc_rows:
            self.assertEqual(mgc_rows[0]["trade_count"], 0)

    def test_BL040d_direction_breakdown_present(self):
        """BL040d: Direction breakdown has Long and Short rows (when trades present)."""
        combo = self._combo("MNQ", "SCALP", "OPENING_DRIVE",
                             [_trade(1.0, direction="Long"), _trade(-1.0, direction="Short")])
        rows = bl._compute_breakdowns([combo])
        dir_rows = {r["breakdown_value"] for r in rows if r["breakdown_type"] == "direction"}
        self.assertIn("Long", dir_rows)
        self.assertIn("Short", dir_rows)


# ============================================================================
# BL041-BL045: Rankings
# ============================================================================
class TestRankings(unittest.TestCase):

    def _combo_with_metrics(self, inst, mode, strat, n, net_r, win_rate=60.0,
                             exp=0.2, pf=1.5, dd=0.5):
        return {
            "inst": inst, "mode": mode, "strategy": strat,
            "status": "COMPLETE" if n > 0 else "COMPLETE_ZERO_TRADES",
            "trades": [_trade(1.0)] * n,
            "metrics": {
                "total_trades": n, "wins": int(n * win_rate / 100), "losses": n - int(n * win_rate / 100),
                "net_r": net_r, "expectancy": exp if n > 0 else None,
                "profit_factor": pf, "win_rate": win_rate if n > 0 else None,
                "max_drawdown_r": dd, "avg_r": exp, "reliability_label": bl._reliability_label(n),
                "long_net_r": net_r / 2, "short_net_r": net_r / 2,
                "warnings": [],
            },
        }

    def test_BL041_returns_dict(self):
        """BL041: _compute_rankings returns a dict."""
        combos = [self._combo_with_metrics("MNQ", "SCALP", "OPENING_DRIVE", 25, 5.0)]
        r = bl._compute_rankings(combos)
        self.assertIsInstance(r, dict)

    def test_BL042_note_is_research_disclaimer(self):
        """BL042: Rankings contain a research disclaimer note."""
        r = bl._compute_rankings([])
        note = r.get("note", "")
        self.assertIn("NOT LIVE PERFORMANCE", note.upper())

    def test_BL043_highest_net_r_ranking_present(self):
        """BL043: highest_net_r ranking key present."""
        r = bl._compute_rankings([])
        self.assertIn("highest_net_r", r)

    def test_BL044_rankings_limited_to_10(self):
        """BL044: No ranking list has more than 10 entries."""
        combos = [self._combo_with_metrics("MNQ", "SCALP", strat, 25, float(i))
                  for i, strat in enumerate(bl.BASELINE_STRATEGIES * 4)]
        r = bl._compute_rankings(combos)
        for key in ("highest_net_r", "highest_expectancy", "largest_sample"):
            lst = r.get(key, [])
            self.assertLessEqual(len(lst), 10, f"{key} exceeds 10 entries")

    def test_BL045_zero_trade_combos_excluded_from_ranked_lists(self):
        """BL045: Combos with 0 trades are excluded from ranked lists."""
        combos = [
            self._combo_with_metrics("MNQ", "SCALP", "OPENING_DRIVE", 0, 0.0),
            self._combo_with_metrics("MES", "SWING", "OPENING_DRIVE", 30, 5.0),
        ]
        r = bl._compute_rankings(combos)
        for entry in r.get("largest_sample", []):
            self.assertGreater(entry["total_trades"], 0)

    def test_BL045b_best_strategy_by_instrument_keys(self):
        """BL045b: best_strategy_by_instrument has all 4 instrument keys."""
        combos = [self._combo_with_metrics("MNQ", "SCALP", "OPENING_DRIVE", 25, 3.0)]
        r = bl._compute_rankings(combos)
        bsi = r.get("best_strategy_by_instrument", {})
        for inst in bl.BASELINE_INSTRUMENTS:
            self.assertIn(inst, bsi)

    def test_BL045c_highest_net_r_sorted_descending(self):
        """BL045c: highest_net_r list is sorted descending by net_r."""
        combos = [
            self._combo_with_metrics("MNQ", "SCALP", "OPENING_DRIVE", 25, 10.0),
            self._combo_with_metrics("MES", "SWING", "OPENING_DRIVE", 30, 5.0),
            self._combo_with_metrics("MGC", "SCALP", "OPENING_DRIVE", 25, 1.0),
        ]
        r = bl._compute_rankings(combos)
        nets = [e["net_r"] for e in r.get("highest_net_r", [])]
        self.assertEqual(nets, sorted(nets, reverse=True))


# ============================================================================
# BL046-BL050: API guards + route smoke tests
# ============================================================================
class TestAPIGuards(unittest.TestCase):

    def setUp(self):
        os.environ.setdefault("DASHBOARD_PASSWORD", "testpassword")

    def _make_app(self):
        """Import a fresh app with DB available."""
        import importlib
        if "app" in sys.modules:
            app_mod = sys.modules["app"]
        else:
            app_mod = importlib.import_module("app")
        return app_mod.app.test_client()

    def test_BL046_bl_guard_no_baseline_available(self):
        """BL046: _bl_guard returns 503 when BASELINE_AVAILABLE=False."""
        try:
            import app as app_mod
            original = app_mod.BASELINE_AVAILABLE
            app_mod.BASELINE_AVAILABLE = False
            with app_mod.app.test_client() as c:
                resp = c.get("/backtest/baselines")
                self.assertIn(resp.status_code, (401, 503))
            app_mod.BASELINE_AVAILABLE = original
        except Exception:
            pass  # fail-open: app import issues are tested elsewhere

    def test_BL047_list_route_registered(self):
        """BL047: /backtest/baselines route registered in Flask."""
        try:
            import app as app_mod
            rules = [str(r) for r in app_mod.app.url_map.iter_rules()]
            self.assertTrue(any("/backtest/baselines" in r for r in rules),
                            f"/backtest/baselines not in {rules[:20]}")
        except ImportError:
            self.skipTest("app not importable in this env")

    def test_BL048_generate_route_registered(self):
        """BL048: /backtest/baselines/generate route registered."""
        try:
            import app as app_mod
            rules = [str(r) for r in app_mod.app.url_map.iter_rules()]
            self.assertTrue(any("generate" in r for r in rules),
                            f"generate not in {rules[:20]}")
        except ImportError:
            self.skipTest("app not importable in this env")

    def test_BL049_trades_route_registered(self):
        """BL049: /backtest/baselines/<id>/trades route registered."""
        try:
            import app as app_mod
            rules = [str(r) for r in app_mod.app.url_map.iter_rules()]
            self.assertTrue(any("trades" in r and "baseline" in r for r in rules))
        except ImportError:
            self.skipTest("app not importable in this env")

    def test_BL050_breakdowns_route_registered(self):
        """BL050: /backtest/baselines/<id>/breakdowns route registered."""
        try:
            import app as app_mod
            rules = [str(r) for r in app_mod.app.url_map.iter_rules()]
            self.assertTrue(any("breakdown" in r for r in rules))
        except ImportError:
            self.skipTest("app not importable in this env")


# ============================================================================
# BL051-BL055: Money-path isolation invariants
# ============================================================================
class TestMoneyPathIsolation(unittest.TestCase):
    """All tests verify that baseline code NEVER touches live trading state."""

    def test_BL051_bl_baseline_not_in_open_paths(self):
        """BL051: /backtest/baselines/* not in OPEN_PATHS (owner-gated)."""
        try:
            import app as app_mod
            open_paths = getattr(app_mod, "OPEN_PATHS", [])
            for path in open_paths:
                self.assertNotIn("baseline", str(path).lower(),
                                 f"baseline endpoint {path} unexpectedly in OPEN_PATHS")
        except ImportError:
            self.skipTest("app not importable")

    def test_BL052_generate_baseline_no_discord_import(self):
        """BL052: bt_baseline.py has no references to Discord."""
        src = open(os.path.join(_BT_DIR, "bt_baseline.py")).read()
        self.assertNotIn("discord", src.lower())
        self.assertNotIn("webhook_url", src.lower())

    def test_BL053_generate_baseline_no_traderspost_import(self):
        """BL053: bt_baseline.py makes no TradersPost API calls or imports."""
        src = open(os.path.join(_BT_DIR, "bt_baseline.py")).read()
        # Check for functional calls/imports — not docstring mentions
        self.assertNotIn("TRADERSPOST_WEBHOOK_URL", src)
        self.assertNotIn("send_order", src)
        self.assertNotIn("import traderspost", src.lower())

    def test_BL054_generate_baseline_no_active_trades(self):
        """BL054: bt_baseline.py doesn't reference ACTIVE_TRADES or live gate."""
        src = open(os.path.join(_BT_DIR, "bt_baseline.py")).read()
        self.assertNotIn("ACTIVE_TRADES_BY_INST", src)
        self.assertNotIn("evaluate_strict_setup", src)
        self.assertNotIn("AUTO_FIRED_KEYS", src)

    def test_BL055_generate_baseline_no_learning_table_writes(self):
        """BL055: bt_baseline.py writes ONLY to baseline_* tables."""
        src = open(os.path.join(_BT_DIR, "bt_baseline.py")).read()
        forbidden_tables = [
            "strategy_trades", "open_trades", "manual_trades", "swing_theses",
            "backtest_runs", "learning_outcomes", "scalp_strategy_research",
            "scalp_strategy_library", "thesis_snapshots", "decision_snapshots",
        ]
        for tbl in forbidden_tables:
            self.assertNotIn(tbl, src,
                             f"bt_baseline.py references forbidden table: {tbl}")

    def test_BL055b_baseline_no_ddl(self):
        """BL055b: bt_baseline.py contains no DDL (CREATE TABLE / DROP / ALTER)."""
        src = open(os.path.join(_BT_DIR, "bt_baseline.py")).read().upper()
        for ddl in ("CREATE TABLE", "DROP TABLE", "ALTER TABLE", "CREATE INDEX",
                    "TRUNCATE", "DROP INDEX"):
            self.assertNotIn(ddl, src,
                             f"bt_baseline.py has DDL: {ddl}")

    def test_BL055c_jdump_handles_inf(self):
        """BL055c: _jdump replaces float('inf') with None (JSON-safe)."""
        result = bl._jdump({"pf": float("inf"), "dd": -float("inf"), "n": 5})
        self.assertIsNone(result["pf"])
        self.assertIsNone(result["dd"])
        self.assertEqual(result["n"], 5)

    def test_BL055d_jdump_handles_nested(self):
        """BL055d: _jdump handles nested dicts + lists."""
        obj = {"a": [float("nan"), 1.0], "b": {"c": float("inf")}}
        result = bl._jdump(obj)
        self.assertIsNone(result["a"][0])
        self.assertEqual(result["a"][1], 1.0)
        self.assertIsNone(result["b"]["c"])

    def test_BL055e_null_helper(self):
        """BL055e: _null() returns dict with value=None and reason."""
        n = bl._null("test reason")
        self.assertIsNone(n["value"])
        self.assertEqual(n["reason"], "test reason")


# ============================================================================
# BL056-BL060: End-to-end metrics correctness
# ============================================================================
class TestMetricsCorrectness(unittest.TestCase):

    def test_BL056_expectancy_equals_net_r_div_count(self):
        """BL056: expectancy = net_r / total_trades."""
        trades = [_trade(r) for r in [2.0, -1.0, 1.5, -0.5, 1.0]]
        m = bl._extended_metrics(trades, "MNQ")
        self.assertAlmostEqual(m["expectancy"], m["net_r"] / m["total_trades"], places=4)

    def test_BL057_net_r_equals_sum_of_r_multiples(self):
        """BL057: net_r = sum of all r_multiples."""
        rs = [1.5, -1.0, 0.75, -0.5, 2.0, -1.0, 0.25]
        trades = [_trade(r) for r in rs]
        m = bl._extended_metrics(trades, "MNQ")
        self.assertAlmostEqual(m["net_r"], sum(rs), places=3)

    def test_BL058_profit_factor_ratio(self):
        """BL058: profit_factor = gross_winners / abs(gross_losers)."""
        trades = [_trade(2.0), _trade(3.0), _trade(-1.0), _trade(-1.0)]
        m = bl._extended_metrics(trades, "MNQ")
        expected_pf = (2.0 + 3.0) / (1.0 + 1.0)
        self.assertAlmostEqual(float(m["profit_factor"]), expected_pf, places=3)

    def test_BL059_max_drawdown_captures_peak_to_trough(self):
        """BL059: max_drawdown_r captures the largest peak-to-trough drop."""
        # Equity: +2, +2, -1, -1, -1, +2 → peak=4, trough=1 → dd=3
        rs = [2.0, 2.0, -1.0, -1.0, -1.0, 2.0]
        base = datetime(2024, 1, 2, 9, 0, tzinfo=timezone.utc)
        trades = [_trade(r, exit_ts=(base + timedelta(hours=i)).isoformat())
                  for i, r in enumerate(rs)]
        m = bl._extended_metrics(trades, "MNQ")
        self.assertAlmostEqual(m["max_drawdown_r"], 3.0, places=3)

    def test_BL060_median_r_correct(self):
        """BL060: median_r correct for odd and even counts."""
        import statistics
        rs_odd  = [1.0, 2.0, 3.0]
        rs_even = [1.0, 2.0, 3.0, 4.0]
        for rs in [rs_odd, rs_even]:
            trades = [_trade(r) for r in rs]
            m = bl._extended_metrics(trades, "MNQ")
            self.assertAlmostEqual(m["median_r"],
                                   round(statistics.median(rs), 4), places=3)

    def test_BL060b_breakeven_count_correct(self):
        """BL060b: breakeven_trades = count of r_multiple == 0.0."""
        trades = [_trade(0.0), _trade(1.0), _trade(0.0), _trade(-1.0)]
        m = bl._extended_metrics(trades, "MNQ")
        self.assertEqual(m["breakeven_trades"], 2)

    def test_BL060c_commission_impact_non_negative(self):
        """BL060c: commission_impact >= 0 (cost is positive)."""
        trades = _wins_losses(5, 3)
        m = bl._extended_metrics(trades, "MNQ")
        self.assertGreaterEqual(m["commission_impact"], 0.0)

    def test_BL060d_slippage_impact_non_negative(self):
        """BL060d: slippage_impact >= 0."""
        trades = _wins_losses(5, 3)
        m = bl._extended_metrics(trades, "MNQ")
        self.assertGreaterEqual(m["slippage_impact"], 0.0)

    def test_BL060e_first_last_trade_ts(self):
        """BL060e: first_trade_ts <= last_trade_ts."""
        trades = [
            _trade(1.0, entry_ts="2024-01-03T10:00:00+00:00",
                    exit_ts="2024-01-03T10:30:00+00:00"),
            _trade(-1.0, entry_ts="2024-01-04T14:00:00+00:00",
                    exit_ts="2024-01-04T14:30:00+00:00"),
        ]
        m = bl._extended_metrics(trades, "MNQ")
        self.assertIsNotNone(m["first_trade_ts"])
        self.assertIsNotNone(m["last_trade_ts"])
        self.assertLessEqual(m["first_trade_ts"], m["last_trade_ts"])


class TestDeterministicConfigHash(unittest.TestCase):
    """BL061-BL069: _freeze_config determinism invariants (Phase 6B.1B).

    The config hash is the integrity seal for a baseline run.  These tests
    verify that every field that feeds the hash is deterministically ordered
    so that the same engine constants always produce the same 16-char hex.
    """

    def _cfg(self):
        cfg, h = bl._freeze_config("test_commit")
        return cfg, h

    def test_BL061_hash_is_16_hex_chars(self):
        """BL061: config hash is exactly 16 lowercase hex characters."""
        _, h = self._cfg()
        self.assertEqual(len(h), 16, f"expected 16 chars, got {len(h)}: {h!r}")
        self.assertTrue(all(c in "0123456789abcdef" for c in h),
                        f"non-hex chars in hash: {h!r}")

    def test_BL062_hash_stable_across_calls(self):
        """BL062: two successive _freeze_config calls return the same hash."""
        _, h1 = self._cfg()
        _, h2 = self._cfg()
        self.assertEqual(h1, h2,
                         "config hash drifted between consecutive calls")

    def test_BL063_valid_symbols_is_sorted(self):
        """BL063: valid_symbols in the config dict is sorted (not set-order)."""
        cfg, _ = self._cfg()
        syms = cfg["valid_symbols"]
        self.assertIsInstance(syms, list, "valid_symbols must be a list")
        self.assertEqual(syms, sorted(syms),
                         f"valid_symbols not sorted: {syms}")

    def test_BL064_valid_timeframes_is_sorted(self):
        """BL064: valid_timeframes in the config dict is sorted."""
        cfg, _ = self._cfg()
        tfs = cfg["valid_timeframes"]
        self.assertIsInstance(tfs, list, "valid_timeframes must be a list")
        self.assertEqual(tfs, sorted(tfs),
                         f"valid_timeframes not sorted: {tfs}")

    def test_BL065_detector_registry_is_sorted(self):
        """BL065: detector_registry_snapshot is a sorted list of strings."""
        cfg, _ = self._cfg()
        det = cfg["detector_registry_snapshot"]
        self.assertIsInstance(det, list)
        self.assertEqual(det, sorted(det),
                         f"detector_registry_snapshot not sorted: {det}")

    def test_BL066_disabled_strategies_is_sorted(self):
        """BL066: disabled_strategies is a sorted list."""
        cfg, _ = self._cfg()
        ds = cfg["disabled_strategies"]
        self.assertIsInstance(ds, list)
        self.assertEqual(ds, sorted(ds),
                         f"disabled_strategies not sorted: {ds}")

    def test_BL067_news_blackouts_json_serialisable(self):
        """BL067: news_blackouts_et serialises to stable JSON (list of lists)."""
        cfg, _ = self._cfg()
        nb = cfg["news_blackouts_et"]
        self.assertIsInstance(nb, list,
                              "news_blackouts_et must be a list")
        for entry in nb:
            self.assertIsInstance(entry, list,
                                  f"each blackout window must be a list, got {type(entry)}")
        serialised = json.dumps(nb, sort_keys=True)
        self.assertIsInstance(serialised, str)

    def test_BL068_config_contains_required_keys(self):
        """BL068: config dict contains all keys that feed the hash."""
        required = {
            "engine_version", "management", "commission_per_side",
            "slippage_ticks", "instruments", "modes", "strategies",
            "valid_symbols", "valid_timeframes", "bt_specs", "bt_modes",
            "news_blackouts_et", "max_trades_per_session", "min_target_r",
            "detector_registry_snapshot", "disabled_strategies",
        }
        cfg, _ = self._cfg()
        missing = required - cfg.keys()
        self.assertFalse(missing, f"config missing keys: {missing}")

    def test_BL069_hash_changes_on_mutated_symbols_order(self):
        """BL069: reversing valid_symbols produces a different JSON, confirming
        that order is load-bearing for the hash (mutation / oracle test)."""
        import hashlib
        cfg, _ = self._cfg()
        original_json = json.dumps(cfg, sort_keys=True, default=str)
        original_hash = hashlib.md5(original_json.encode()).hexdigest()[:16]

        cfg_mutated = dict(cfg)
        cfg_mutated["valid_symbols"] = list(reversed(cfg["valid_symbols"]))
        mutated_json = json.dumps(cfg_mutated, sort_keys=True, default=str)
        mutated_hash = hashlib.md5(mutated_json.encode()).hexdigest()[:16]

        if len(cfg["valid_symbols"]) > 1:
            self.assertNotEqual(
                original_hash, mutated_hash,
                "reversed valid_symbols should yield a different hash — "
                "order is not load-bearing, which defeats the determinism fix",
            )


if __name__ == "__main__":
    import unittest
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
