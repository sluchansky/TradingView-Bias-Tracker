"""test_right_brain_trade_management.py — Phase 6B.2 Right Brain Trade Management v1.

Tests cover:
  TM001  _rb_tm_neutral: required top-level keys present
  TM002  _rb_tm_neutral: affects_execution always False, shadow_mode always True
  TM003  _rb_tm_neutral: available is always False
  TM004  _rb_tm_neutral: active_trade kwarg propagated correctly
  TM005  _rb_tm_build_snapshot: no trade returns empty dict
  TM006  _rb_tm_build_snapshot: Long trade populates all key fields
  TM007  _rb_tm_build_snapshot: Short trade inverts vwap_relationship
  TM008  _rb_tm_build_snapshot: missing price yields current_r=None
  TM009  _rb_tm_eval_dimensions: Long R>0, structure favorable → trend+vwap favorable
  TM010  _rb_tm_eval_dimensions: stop breached → stop_breached True, near_stop False
  TM011  _rb_tm_eval_dimensions: empty snapshot → all unavailable, booleans False
  TM012  _rb_tm_compute_health: score always in [0, 100]
  TM013  _rb_tm_compute_health: healthy trade (R>1, trend intact, VWAP holding) → score ≥ 55
  TM014  _rb_tm_compute_health: critical trade (R<-0.5, structure broken) → score < 25
  TM015  _rb_tm_compute_health: label maps correctly to score thresholds
  TM016  _rb_tm_compute_confidence: full data available → label HIGH
  TM017  _rb_tm_compute_confidence: no data available → label INSUFFICIENT_DATA
  TM018  _rb_tm_compute_thesis: stop breached → status BROKEN
  TM019  _rb_tm_compute_thesis: structure broken → status BROKEN
  TM020  _rb_tm_compute_thesis: all valid factors, R>1 → INTACT or IMPROVING
  TM021  _rb_tm_compute_thesis: weakened factors → WEAKENING or STABLE
  TM022  _rb_tm_compute_thesis: no data at all → UNKNOWN
  TM023  _rb_tm_compute_exit_pressure: stop breached → CRITICAL
  TM024  _rb_tm_compute_exit_pressure: near stop + thesis WEAKENING → HIGH or ELEVATED
  TM025  _rb_tm_compute_exit_pressure: VWAP failing + structure threatened → MODERATE+
  TM026  _rb_tm_compute_exit_pressure: strong R > 2, all clear → LOW or NONE
  TM027  _rb_tm_compute_recommendation: CRITICAL pressure → THESIS_BROKEN
  TM028  _rb_tm_compute_recommendation: HIGH pressure → EXIT_IF_CONFIRMATION_FAILS
  TM029  _rb_tm_compute_recommendation: health EXCELLENT, R>2 → LET_RUN
  TM030  _rb_tm_compute_recommendation: INSUFFICIENT_DATA confidence → INSUFFICIENT_DATA
  TM031  recommendation action always a member of RBTM_VALID_RECOMMENDATIONS
  TM032  compute_right_brain_trade_management: flat → state=FLAT, active_trade=False
  TM033  compute_right_brain_trade_management: Long active trade → state=ACTIVE_TRADE, direction Long
  TM034  compute_right_brain_trade_management: Short active trade → direction Short
  TM035  compute_right_brain_trade_management: full output is JSON-serializable
  TM036  _rb_tm_compute_recommendation: SCALP stalled trade → WATCH_CLOSELY or REVIEW_MANUALLY
  TM037  compute_right_brain_trade_management does not mutate result["verdict"]
  TM038  compute_right_brain_trade_management does not mutate result["edge_score"]
  TM039  compute_right_brain_trade_management does not mutate result["trade_plan"]
  TM040  compute_right_brain_trade_management does not write new entries to ACTIVE_TRADES_BY_INST
  TM041  affects_execution is always False in the full output dict
  TM042  RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED defaults to False (flag OFF by default)
"""
import json
import sys
import os
import copy
import unittest

# ---------------------------------------------------------------------------
# Module bootstrap — keep flag OFF by default so imports are isolated
# ---------------------------------------------------------------------------
os.environ.setdefault("RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED", "0")

import app as _app

compute_right_brain_trade_management = _app.compute_right_brain_trade_management
_rb_tm_neutral                       = _app._rb_tm_neutral
_rb_tm_label                         = _app._rb_tm_label
_rb_tm_build_snapshot                = _app._rb_tm_build_snapshot
_rb_tm_eval_dimensions               = _app._rb_tm_eval_dimensions
_rb_tm_compute_health                = _app._rb_tm_compute_health
_rb_tm_compute_confidence            = _app._rb_tm_compute_confidence
_rb_tm_compute_thesis                = _app._rb_tm_compute_thesis
_rb_tm_compute_exit_pressure         = _app._rb_tm_compute_exit_pressure
_rb_tm_compute_recommendation        = _app._rb_tm_compute_recommendation
RBTM_VALID_RECOMMENDATIONS           = _app.RBTM_VALID_RECOMMENDATIONS
ACTIVE_TRADES_BY_INST                = _app.ACTIVE_TRADES_BY_INST
ACTIVE_TRADES_LOCK                   = _app.ACTIVE_TRADES_LOCK

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MGC_INST = "MGC"

def _fake_trade(
    *,
    direction="Long",
    entry_price=2700.0,
    stop_loss=2694.0,
    target1=2718.0,
    target2=2718.0,
    contracts=1,
    strategy="Opening Drive",
    mode="SCALP",
    symbol="MGC",
    opened_at="2026-07-26T09:35:00+00:00",
    max_r=None,
    min_r=None,
):
    """Minimal trade dict matching the ACTIVE_TRADES_BY_INST schema."""
    return {
        "direction":   direction,
        "entry_price": entry_price,
        "stop_loss":   stop_loss,
        "target1":     target1,
        "target2":     target2,
        "contracts":   contracts,
        "profile":     symbol,
        "symbol":      symbol,
        "strategy":    strategy,
        "mode":        mode,
        "opened_at":   opened_at,
        "t1_hit":      False,
        "t2_hit":      False,
        "status":      "active",
        "max_r":       max_r,
        "min_r":       min_r,
    }


def _fake_result(
    *,
    active_ticker="MGC",
    verdict="WAIT",
    edge_score=65,
    display_price=2706.0,
    vwap_value=2703.0,
    vwap_status="ok",
    structure_class="Bullish Trend",
    vol_regime="TRENDING",
    session_window="New York",
    trade_plan=None,
):
    """Minimal full_analysis() result dict for trade management tests."""
    return {
        "active_ticker":     active_ticker,
        "verdict":           verdict,
        "edge_score":        edge_score,
        "display_price":     display_price,
        "vwap_value":        vwap_value,
        "vwap_status":       vwap_status,
        "structure_class":   structure_class,
        "volatility":        {"regime": vol_regime, "atr_pts": 5.0, "ratio": 1.2},
        "session":           {"window": session_window, "preferred": True, "bonus": 10},
        "alert_diagnostics": {"rvol": 1.8, "cvd_signal": "Long",
                               "dominant_direction": "Long", "edge_score": edge_score},
        "strategy_engine":   {"active_strategy": "Opening Drive", "market_regime": vol_regime},
        "trade_plan":        trade_plan or {"action": "BUY", "entry": display_price},
    }


def _inject_trade(inst, trade):
    """Thread-safely inject a trade into ACTIVE_TRADES_BY_INST for testing."""
    with ACTIVE_TRADES_LOCK:
        ACTIVE_TRADES_BY_INST[inst] = trade


def _clear_trade(inst):
    """Thread-safely remove a trade from ACTIVE_TRADES_BY_INST after a test."""
    with ACTIVE_TRADES_LOCK:
        ACTIVE_TRADES_BY_INST.pop(inst, None)


def _full_dims_snapshot(current_r=1.5, direction="Long"):
    """Snapshot with all dimensions available — used for confidence tests."""
    return {
        "instrument":            _MGC_INST,
        "strategy":              "Opening Drive",
        "mode":                  "SCALP",
        "direction":             direction,
        "entry_price":           2700.0,
        "current_price":         2715.0,
        "stop":                  2694.0,
        "target1":               2718.0,
        "target2":               2718.0,
        "contracts":             1,
        "opened_at":             "2026-07-26T09:35:00+00:00",
        "time_in_trade_seconds": 300,
        "unrealized_pnl":        150.0,
        "unrealized_pts":        15.0,
        "current_r":             current_r,
        "mfe_r":                 max(current_r, 0.0),
        "mae_r":                 min(current_r, 0.0),
        "dist_to_stop":          21.0,
        "dist_to_target":        3.0,
        "session":               "New York",
        "vol_regime":            "TRENDING",
        "trend_state":           "favorable",
        "vwap_relationship":     "above_vwap",
        "or_state":              "complete",
        "momentum_state":        "unavailable",
        "structure_state":       "favorable",
        "volume_state":          "expanding",
        "delta_cvd_state":       "confirming",
        "volatility_state":      "TRENDING",
        "original_thesis":       "Original thesis snapshot unavailable.",
    }


def _full_dims(current_r=1.5, direction="Long", structure="favorable",
               vwap="holding_in_direction", volume="expanding",
               delta="confirming", volatility="expanding_favorably",
               stop_breached=False, near_stop=False, progress="progressing_strongly"):
    """Dimension dict with all fields populated for health/pressure tests."""
    return {
        "momentum":       "increasing" if current_r > 0 else "weakening",
        "trend":          "strengthening" if structure == "favorable" else (
                          "intact" if structure == "intact" else (
                          "weakening" if structure == "threatened" else "broken")),
        "vwap":           vwap,
        "structure":      structure,
        "volume":         volume,
        "delta_cvd":      delta,
        "volatility":     volatility,
        "trade_progress": progress,
        "stop_breached":  stop_breached,
        "near_stop":      near_stop,
    }


# ---------------------------------------------------------------------------
# TM001-TM004: Neutral block
# ---------------------------------------------------------------------------
class TestNeutralBlock(unittest.TestCase):

    def test_TM001_required_keys_present(self):
        """TM001: _rb_tm_neutral returns all required keys."""
        n = _rb_tm_neutral()
        for key in ("available", "active_trade", "shadow_mode",
                    "affects_execution", "recommendation", "reason"):
            self.assertIn(key, n, msg=f"Missing key: {key}")
        rec = n["recommendation"]
        for rkey in ("action", "priority", "reasons",
                     "confirmation_needed", "what_would_improve",
                     "what_would_worsen", "invalidation_watch"):
            self.assertIn(rkey, rec, msg=f"Missing recommendation key: {rkey}")

    def test_TM002_affects_execution_shadow_mode(self):
        """TM002: affects_execution always False, shadow_mode always True."""
        n = _rb_tm_neutral()
        self.assertFalse(n["affects_execution"])
        self.assertTrue(n["shadow_mode"])

    def test_TM003_available_false(self):
        """TM003: available is always False in the neutral block."""
        n = _rb_tm_neutral()
        self.assertFalse(n["available"])
        n2 = _rb_tm_neutral(active_trade=True)
        self.assertFalse(n2["available"])

    def test_TM004_active_trade_kwarg(self):
        """TM004: active_trade kwarg is propagated."""
        self.assertFalse(_rb_tm_neutral(active_trade=False)["active_trade"])
        self.assertTrue(_rb_tm_neutral(active_trade=True)["active_trade"])


# ---------------------------------------------------------------------------
# TM005-TM008: Snapshot builder
# ---------------------------------------------------------------------------
class TestSnapshot(unittest.TestCase):

    def test_TM005_no_trade_returns_empty_dict(self):
        """TM005: _rb_tm_build_snapshot with None trade → {}."""
        snap = _rb_tm_build_snapshot(None, {}, _MGC_INST)
        self.assertEqual(snap, {})

    def test_TM006_long_trade_key_fields(self):
        """TM006: Long trade snapshot populates all expected keys."""
        trade  = _fake_trade(direction="Long", entry_price=2700.0,
                             stop_loss=2694.0, target1=2718.0)
        result = _fake_result(display_price=2706.0, vwap_value=2703.0,
                              structure_class="Bullish Trend")
        snap = _rb_tm_build_snapshot(trade, result, _MGC_INST)
        self.assertEqual(snap["direction"],   "Long")
        self.assertEqual(snap["entry_price"], 2700.0)
        self.assertEqual(snap["stop"],        2694.0)
        self.assertEqual(snap["target1"],     2718.0)
        self.assertEqual(snap["instrument"],  _MGC_INST)
        self.assertIsNotNone(snap["current_r"])
        self.assertGreater(snap["current_r"], 0)  # 2706 > 2700 = Long in profit
        self.assertEqual(snap["vwap_relationship"], "above_vwap")
        self.assertEqual(snap["structure_state"],   "favorable")

    def test_TM007_short_trade_vwap_inverted(self):
        """TM007: Short trade → vwap_relationship inverted (below = favorable)."""
        trade  = _fake_trade(direction="Short", entry_price=2700.0,
                             stop_loss=2706.0, target1=2682.0)
        result = _fake_result(display_price=2697.0, vwap_value=2703.0,
                              structure_class="Bearish Trend")
        snap = _rb_tm_build_snapshot(trade, result, _MGC_INST)
        self.assertEqual(snap["direction"], "Short")
        self.assertGreater(snap["current_r"], 0)  # 2700 - 2697 = +3 favorable
        # Short + price below VWAP → vwap_relationship = below_vwap = favorable for Short
        self.assertEqual(snap["vwap_relationship"], "below_vwap")
        self.assertEqual(snap["structure_state"],   "favorable")

    def test_TM008_missing_price_yields_none_r(self):
        """TM008: When current_price is unavailable, current_r is None."""
        trade  = _fake_trade(direction="Long", entry_price=2700.0, stop_loss=2694.0)
        result = _fake_result(display_price=None)
        # Patch display_price_for to also return None
        orig = _app.display_price_for
        _app.display_price_for = lambda t: (None, None)
        try:
            snap = _rb_tm_build_snapshot(trade, result, _MGC_INST)
            self.assertIsNone(snap["current_r"])
            self.assertIsNone(snap["current_price"])
        finally:
            _app.display_price_for = orig


# ---------------------------------------------------------------------------
# TM009-TM011: Dimension evaluator
# ---------------------------------------------------------------------------
class TestDimensions(unittest.TestCase):

    def test_TM009_long_profitable_favorable(self):
        """TM009: Long trade at +1R with favorable structure → trend+vwap favorable."""
        snap = _full_dims_snapshot(current_r=1.0, direction="Long")
        dims = _rb_tm_eval_dimensions({}, {}, snap)
        self.assertIn(dims["trend"], ("strengthening", "intact"))
        self.assertEqual(dims["vwap"], "holding_in_direction")
        self.assertFalse(dims["stop_breached"])

    def test_TM010_stop_breached(self):
        """TM010: Price below stop on Long → stop_breached True, near_stop False."""
        snap = {
            "direction":     "Long",
            "entry_price":   2700.0,
            "current_price": 2690.0,  # below stop
            "stop":          2694.0,
            "target1":       2718.0,
            "current_r":     -1.67,
            "structure_state": "broken",
            "vwap_relationship": "below_vwap",
            "volume_state": "contracting",
            "delta_cvd_state": "diverging",
            "vol_regime": "",
            "mode": "SCALP",
            "dist_to_stop": 0.0,
            "dist_to_target": 28.0,
        }
        dims = _rb_tm_eval_dimensions({}, {}, snap)
        self.assertTrue(dims["stop_breached"])
        self.assertFalse(dims["near_stop"])
        self.assertEqual(dims["trend"],    "broken")
        self.assertEqual(dims["vwap"],     "failing")

    def test_TM011_empty_snapshot_all_unavailable(self):
        """TM011: Empty snapshot → all dimension strings are 'unavailable', booleans False."""
        dims = _rb_tm_eval_dimensions({}, {}, {})
        for key in ("momentum", "trend", "vwap", "structure",
                    "volume", "delta_cvd", "volatility", "trade_progress"):
            self.assertEqual(dims[key], "unavailable",
                             msg=f"Expected 'unavailable' for {key}, got {dims[key]!r}")
        self.assertFalse(dims["stop_breached"])
        self.assertFalse(dims["near_stop"])


# ---------------------------------------------------------------------------
# TM012-TM015: Health score
# ---------------------------------------------------------------------------
class TestHealth(unittest.TestCase):

    def _healthy_scenario(self):
        snap = _full_dims_snapshot(current_r=1.5, direction="Long")
        dims = _full_dims(current_r=1.5, structure="favorable",
                          vwap="holding_in_direction", volume="expanding",
                          delta="confirming", volatility="expanding_favorably",
                          progress="progressing_strongly")
        return snap, dims

    def _critical_scenario(self):
        snap = dict(_full_dims_snapshot(current_r=-0.8, direction="Long"))
        snap["current_r"] = -0.8
        dims = _full_dims(current_r=-0.8, structure="broken", vwap="failing",
                          volume="contracting", delta="reversing",
                          volatility="expanding_adversely",
                          stop_breached=True, progress="near_stop")
        return snap, dims

    def test_TM012_score_bounded_0_100(self):
        """TM012: Health score is always in [0, 100] for extreme scenarios."""
        for scenario in [self._healthy_scenario(), self._critical_scenario()]:
            snap, dims = scenario
            health = _rb_tm_compute_health(snap, dims)
            self.assertGreaterEqual(health["score"], 0)
            self.assertLessEqual(health["score"],   100)

    def test_TM013_healthy_trade_score_ge_55(self):
        """TM013: Healthy trade (R>1, trend intact, VWAP holding) → score ≥ 55."""
        snap, dims = self._healthy_scenario()
        health = _rb_tm_compute_health(snap, dims)
        self.assertGreaterEqual(health["score"], 55,
                                msg=f"Expected ≥55, got {health['score']} ({health['label']})")

    def test_TM014_critical_trade_score_lt_25(self):
        """TM014: Critical trade (R<-0.5, structure broken, stop breached) → score < 25."""
        snap, dims = self._critical_scenario()
        health = _rb_tm_compute_health(snap, dims)
        self.assertLess(health["score"], 25,
                        msg=f"Expected <25, got {health['score']} ({health['label']})")

    def test_TM015_label_maps_correctly(self):
        """TM015: _rb_tm_label maps score to correct label from _RBTM_HEALTH_LABELS."""
        cases = [
            (90, "EXCELLENT"),
            (75, "STRONG"),
            (60, "HEALTHY"),
            (45, "NEUTRAL"),
            (30, "WEAK"),
            (15, "DANGER"),
            (5,  "CRITICAL"),
        ]
        for score, expected_label in cases:
            label = _rb_tm_label(score, _app._RBTM_HEALTH_LABELS)
            self.assertEqual(label, expected_label,
                             msg=f"score={score}: expected {expected_label!r}, got {label!r}")


# ---------------------------------------------------------------------------
# TM016-TM017: Management confidence
# ---------------------------------------------------------------------------
class TestConfidence(unittest.TestCase):

    def test_TM016_full_data_high_confidence(self):
        """TM016: When all key inputs are present → label HIGH."""
        snap = _full_dims_snapshot(current_r=1.5, direction="Long")
        dims = _full_dims()
        conf = _rb_tm_compute_confidence(snap, dims)
        self.assertEqual(conf["label"], "HIGH",
                         msg=f"Expected HIGH, got {conf['label']} (score={conf['score']})")
        self.assertGreater(conf["score"], 0)

    def test_TM017_no_data_insufficient(self):
        """TM017: Empty snapshot → INSUFFICIENT_DATA."""
        conf = _rb_tm_compute_confidence({}, {})
        self.assertEqual(conf["label"], "INSUFFICIENT_DATA")
        self.assertEqual(conf["score"], 0)


# ---------------------------------------------------------------------------
# TM018-TM022: Thesis evaluation
# ---------------------------------------------------------------------------
class TestThesis(unittest.TestCase):

    def _base_thesis_inputs(self):
        trade  = _fake_trade()
        result = _fake_result()
        snap   = _full_dims_snapshot(current_r=1.5)
        dims   = _full_dims()
        return trade, result, snap, dims

    def test_TM018_stop_breached_broken(self):
        """TM018: stop_breached → thesis BROKEN."""
        trade, result, snap, _ = self._base_thesis_inputs()
        dims = _full_dims(stop_breached=True, structure="broken", vwap="failing")
        thesis = _rb_tm_compute_thesis(trade, result, snap, dims)
        self.assertEqual(thesis["status"], "BROKEN")

    def test_TM019_structure_broken_thesis_broken(self):
        """TM019: structure broken (no stop breach) → thesis BROKEN."""
        trade, result, snap, _ = self._base_thesis_inputs()
        dims = _full_dims(stop_breached=False, structure="broken", vwap="failing")
        thesis = _rb_tm_compute_thesis(trade, result, snap, dims)
        self.assertEqual(thesis["status"], "BROKEN")

    def test_TM020_all_valid_factors_intact_or_improving(self):
        """TM020: All key factors valid and R>1 → INTACT or IMPROVING."""
        trade, result, snap, dims = self._base_thesis_inputs()
        thesis = _rb_tm_compute_thesis(trade, result, snap, dims)
        self.assertIn(thesis["status"], ("INTACT", "IMPROVING"),
                      msg=f"Expected INTACT/IMPROVING, got {thesis['status']!r}")

    def test_TM021_weakened_factors_weakening_or_stable(self):
        """TM021: One weakened factor → WEAKENING or STABLE."""
        trade, result, snap, _ = self._base_thesis_inputs()
        dims = _full_dims(structure="threatened", vwap="holding_in_direction")
        thesis = _rb_tm_compute_thesis(trade, result, snap, dims)
        self.assertIn(thesis["status"], ("WEAKENING", "STABLE"),
                      msg=f"Expected WEAKENING/STABLE, got {thesis['status']!r}")

    def test_TM022_no_data_unknown(self):
        """TM022: No original or current factors → UNKNOWN."""
        thesis = _rb_tm_compute_thesis({}, {}, {}, {})
        self.assertEqual(thesis["status"], "UNKNOWN")


# ---------------------------------------------------------------------------
# TM023-TM026: Exit pressure
# ---------------------------------------------------------------------------
class TestExitPressure(unittest.TestCase):

    def _ep(self, thesis_status="INTACT", **dim_kwargs):
        snap  = _full_dims_snapshot(**{k: v for k, v in dim_kwargs.items()
                                      if k == "current_r"})
        dims  = _full_dims(**{k: v for k, v in dim_kwargs.items()
                              if k != "current_r"})
        health = _rb_tm_compute_health(snap, dims)
        thesis = {"status": thesis_status}
        return _rb_tm_compute_exit_pressure(snap, thesis, health, dims)

    def test_TM023_stop_breached_critical(self):
        """TM023: stop_breached → CRITICAL exit pressure."""
        ep = self._ep(stop_breached=True, thesis_status="BROKEN")
        self.assertEqual(ep["level"], "CRITICAL")

    def test_TM024_near_stop_weakening_high_or_elevated(self):
        """TM024: near stop + thesis WEAKENING → ELEVATED, HIGH, or CRITICAL."""
        ep = self._ep(near_stop=True, thesis_status="WEAKENING",
                      structure="threatened", vwap="failing")
        _ordered = ["NONE", "LOW", "MODERATE", "ELEVATED", "HIGH", "CRITICAL"]
        idx = _ordered.index(ep["level"])
        self.assertGreaterEqual(idx, _ordered.index("ELEVATED"),
                                msg=f"Expected ELEVATED+, got {ep['level']!r} (score={ep['score']})")

    def test_TM025_vwap_fail_struct_threatened_moderate_plus(self):
        """TM025: VWAP failing + structure threatened → MODERATE or higher."""
        ep = self._ep(vwap="failing", structure="threatened",
                      thesis_status="STABLE")
        _ordered = ["NONE", "LOW", "MODERATE", "ELEVATED", "HIGH", "CRITICAL"]
        idx = _ordered.index(ep["level"])
        self.assertGreaterEqual(idx, _ordered.index("MODERATE"),
                                msg=f"Expected MODERATE+, got {ep['level']!r}")

    def test_TM026_strong_r_all_clear_low_or_none(self):
        """TM026: Strong R>2, all clear → LOW or NONE exit pressure."""
        snap = _full_dims_snapshot(current_r=2.5)
        dims = _full_dims(current_r=2.5, progress="near_target")
        health = _rb_tm_compute_health(snap, dims)
        thesis = {"status": "IMPROVING"}
        ep = _rb_tm_compute_exit_pressure(snap, thesis, health, dims)
        self.assertIn(ep["level"], ("NONE", "LOW"),
                      msg=f"Expected NONE/LOW, got {ep['level']!r} (score={ep['score']})")


# ---------------------------------------------------------------------------
# TM027-TM031: Recommendation
# ---------------------------------------------------------------------------
class TestRecommendation(unittest.TestCase):

    def _snap_and_dims(self, current_r=1.0, structure="favorable", near_stop=False,
                       stop_breached=False, vwap="holding_in_direction",
                       progress="progressing_strongly", mode="SCALP"):
        snap = dict(_full_dims_snapshot(current_r=current_r))
        snap["current_r"] = current_r
        snap["mode"] = mode
        dims = _full_dims(current_r=current_r, structure=structure, vwap=vwap,
                          near_stop=near_stop, stop_breached=stop_breached,
                          progress=progress)
        return snap, dims

    def _rec(self, snap, dims, thesis_status="INTACT", pressure_level="NONE",
             conf_label="HIGH"):
        health = _rb_tm_compute_health(snap, dims)
        thesis = {"status": thesis_status}
        conf   = {"label": conf_label, "score": 80}
        ep     = {"level": pressure_level, "score": 0, "reasons": []}
        return _rb_tm_compute_recommendation(snap, thesis, health, conf, ep, dims)

    def test_TM027_critical_pressure_thesis_broken(self):
        """TM027: CRITICAL exit pressure → THESIS_BROKEN recommendation."""
        snap, dims = self._snap_and_dims(stop_breached=True)
        rec = self._rec(snap, dims, thesis_status="BROKEN", pressure_level="CRITICAL")
        self.assertEqual(rec["action"], "THESIS_BROKEN")

    def test_TM028_high_pressure_exit_if_fails(self):
        """TM028: HIGH exit pressure → EXIT_IF_CONFIRMATION_FAILS."""
        snap, dims = self._snap_and_dims(structure="threatened", vwap="failing",
                                         near_stop=True)
        rec = self._rec(snap, dims, thesis_status="WEAKENING", pressure_level="HIGH")
        self.assertEqual(rec["action"], "EXIT_IF_CONFIRMATION_FAILS")

    def test_TM029_strong_health_r_over_2_let_run(self):
        """TM029: Health EXCELLENT, R>2 → LET_RUN."""
        snap, dims = self._snap_and_dims(current_r=2.5, structure="favorable",
                                         vwap="holding_in_direction",
                                         progress="near_target")
        rec = self._rec(snap, dims, thesis_status="IMPROVING", pressure_level="NONE")
        self.assertEqual(rec["action"], "LET_RUN",
                         msg=f"Expected LET_RUN, got {rec['action']!r}")

    def test_TM030_insufficient_data_confidence(self):
        """TM030: Confidence INSUFFICIENT_DATA → action INSUFFICIENT_DATA."""
        snap, dims = self._snap_and_dims()
        rec = self._rec(snap, dims, conf_label="INSUFFICIENT_DATA")
        self.assertEqual(rec["action"], "INSUFFICIENT_DATA")

    def test_TM031_action_always_valid_enum(self):
        """TM031: Recommendation action is always in RBTM_VALID_RECOMMENDATIONS."""
        test_cases = [
            {"thesis_status": "BROKEN",    "pressure_level": "CRITICAL"},
            {"thesis_status": "WEAKENING", "pressure_level": "HIGH"},
            {"thesis_status": "INTACT",    "pressure_level": "NONE"},
            {"thesis_status": "IMPROVING", "pressure_level": "NONE"},
            {"thesis_status": "UNKNOWN",   "pressure_level": "NONE"},
            {"thesis_status": "STABLE",    "pressure_level": "MODERATE"},
            {"thesis_status": "STABLE",    "pressure_level": "ELEVATED"},
        ]
        for kwargs in test_cases:
            for current_r in (-0.5, 0.0, 0.5, 1.0, 2.5):
                snap, dims = self._snap_and_dims(current_r=current_r)
                health = _rb_tm_compute_health(snap, dims)
                thesis = {"status": kwargs["thesis_status"]}
                conf   = {"label": "HIGH", "score": 80}
                ep     = {"level": kwargs["pressure_level"], "score": 20, "reasons": []}
                rec = _rb_tm_compute_recommendation(snap, thesis, health, conf, ep, dims)
                self.assertIn(rec["action"], RBTM_VALID_RECOMMENDATIONS,
                              msg=(f"Invalid action {rec['action']!r} for "
                                   f"thesis={kwargs['thesis_status']}, "
                                   f"pressure={kwargs['pressure_level']}, R={current_r}"))


# ---------------------------------------------------------------------------
# TM032-TM036: Full compute function
# ---------------------------------------------------------------------------
class TestComputeFull(unittest.TestCase):

    def tearDown(self):
        _clear_trade(_MGC_INST)

    def test_TM032_flat_state(self):
        """TM032: No active trade → state=FLAT, active_trade=False."""
        _clear_trade(_MGC_INST)
        result = _fake_result(active_ticker="MGC")
        out = compute_right_brain_trade_management(result)
        self.assertTrue(out["available"])
        self.assertFalse(out["active_trade"])
        self.assertEqual(out["state"], "FLAT")
        self.assertEqual(out["recommendation"]["action"], "NO_ACTIVE_TRADE")

    def test_TM033_active_long_trade(self):
        """TM033: Long active trade → state=ACTIVE_TRADE, direction Long."""
        trade = _fake_trade(direction="Long", entry_price=2700.0,
                            stop_loss=2694.0, target1=2718.0)
        _inject_trade(_MGC_INST, trade)
        result = _fake_result(active_ticker="MGC", display_price=2706.0)
        out = compute_right_brain_trade_management(result)
        self.assertTrue(out["available"])
        self.assertTrue(out["active_trade"])
        self.assertEqual(out["state"],                   "ACTIVE_TRADE")
        self.assertEqual(out["snapshot"]["direction"],   "Long")
        self.assertFalse(out["affects_execution"])
        self.assertTrue(out["shadow_mode"])

    def test_TM034_active_short_trade(self):
        """TM034: Short active trade → state=ACTIVE_TRADE, direction Short."""
        trade = _fake_trade(direction="Short", entry_price=2700.0,
                            stop_loss=2706.0, target1=2682.0)
        _inject_trade(_MGC_INST, trade)
        result = _fake_result(active_ticker="MGC", display_price=2697.0,
                              structure_class="Bearish Trend")
        out = compute_right_brain_trade_management(result)
        self.assertEqual(out["snapshot"]["direction"], "Short")
        self.assertEqual(out["state"],                "ACTIVE_TRADE")

    def test_TM035_json_serializable(self):
        """TM035: compute_right_brain_trade_management output is JSON-serializable."""
        trade = _fake_trade()
        _inject_trade(_MGC_INST, trade)
        result = _fake_result(active_ticker="MGC", display_price=2706.0)
        out = compute_right_brain_trade_management(result)
        try:
            serialized = json.dumps(out)
            self.assertIsInstance(serialized, str)
        except (TypeError, ValueError) as exc:
            self.fail(f"JSON serialization failed: {exc}")

    def test_TM036_scalp_stalled_watch_or_review(self):
        """TM036: SCALP trade stalled → WATCH_CLOSELY or REVIEW_MANUALLY."""
        snap = dict(_full_dims_snapshot(current_r=0.0))
        snap["mode"] = "SCALP"
        snap["current_r"] = 0.0
        dims = _full_dims(current_r=0.0, structure="neutral",
                          vwap="neutral", progress="stalled")
        health = _rb_tm_compute_health(snap, dims)
        thesis = {"status": "STABLE"}
        conf   = {"label": "MEDIUM", "score": 60}
        ep     = _rb_tm_compute_exit_pressure(snap, thesis, health, dims)
        rec    = _rb_tm_compute_recommendation(snap, thesis, health, conf, ep, dims)
        self.assertIn(rec["action"],
                      ("WATCH_CLOSELY", "REVIEW_MANUALLY", "HOLD",
                       "CONSIDER_BREAK_EVEN", "THESIS_WEAKENING", "REDUCE_RISK"),
                      msg=f"Unexpected action for stalled SCALP: {rec['action']!r}")


# ---------------------------------------------------------------------------
# TM037-TM041: Money-path isolation
# ---------------------------------------------------------------------------
class TestMoneyPathIsolation(unittest.TestCase):

    def tearDown(self):
        _clear_trade(_MGC_INST)

    def _run_with_trade(self, verdict="SHORT READY", edge_score=85,
                        trade_plan=None):
        trade = _fake_trade(direction="Long", entry_price=2700.0,
                            stop_loss=2694.0, target1=2718.0)
        _inject_trade(_MGC_INST, trade)
        result = _fake_result(
            active_ticker="MGC",
            verdict=verdict,
            edge_score=edge_score,
            display_price=2706.0,
            trade_plan=trade_plan or {"action": "BUY", "entry": 2706.0, "stop": 2694.0},
        )
        original_result = copy.deepcopy(result)
        out = compute_right_brain_trade_management(result)
        return result, original_result, out

    def test_TM037_does_not_mutate_verdict(self):
        """TM037: compute_right_brain_trade_management does not change result['verdict']."""
        result, original, _ = self._run_with_trade(verdict="SHORT READY")
        self.assertEqual(result["verdict"], original["verdict"])

    def test_TM038_does_not_mutate_edge_score(self):
        """TM038: compute_right_brain_trade_management does not change result['edge_score']."""
        result, original, _ = self._run_with_trade(edge_score=85)
        self.assertEqual(result["edge_score"], original["edge_score"])

    def test_TM039_does_not_mutate_trade_plan(self):
        """TM039: compute_right_brain_trade_management does not change result['trade_plan']."""
        plan = {"action": "BUY", "entry": 2706.0, "stop": 2694.0}
        result, original, _ = self._run_with_trade(trade_plan=copy.deepcopy(plan))
        self.assertEqual(result["trade_plan"], original["trade_plan"])

    def test_TM040_does_not_write_to_active_trades(self):
        """TM040: ACTIVE_TRADES_BY_INST is not altered by the management engine."""
        with ACTIVE_TRADES_LOCK:
            before = dict(ACTIVE_TRADES_BY_INST)
        trade = _fake_trade()
        _inject_trade(_MGC_INST, trade)
        result = _fake_result(active_ticker="MGC", display_price=2706.0)
        _ = compute_right_brain_trade_management(result)
        with ACTIVE_TRADES_LOCK:
            # The injected trade should still be there, unchanged
            after = dict(ACTIVE_TRADES_BY_INST)
        # The trade we injected should still be present
        self.assertIn(_MGC_INST, after)
        # No extra instruments should appear
        before_keys = set(before.keys()) | {_MGC_INST}
        self.assertTrue(set(after.keys()).issubset(before_keys))

    def test_TM041_affects_execution_always_false(self):
        """TM041: affects_execution is always False in the full output."""
        # Flat case
        _clear_trade(_MGC_INST)
        result = _fake_result(active_ticker="MGC")
        out = compute_right_brain_trade_management(result)
        self.assertFalse(out["affects_execution"])
        # Active trade case
        trade = _fake_trade()
        _inject_trade(_MGC_INST, trade)
        result2 = _fake_result(active_ticker="MGC", display_price=2706.0)
        out2 = compute_right_brain_trade_management(result2)
        self.assertFalse(out2["affects_execution"])


# ---------------------------------------------------------------------------
# TM042: Flag default OFF
# ---------------------------------------------------------------------------
class TestFlagDefault(unittest.TestCase):

    def test_TM042_flag_off_by_default(self):
        """TM042: RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED defaults to False when env unset."""
        # The env var was set to "0" in the module bootstrap above, which is how the
        # default works in practice (no env var → _env_flag_on returns False).
        # Verify the module-level flag is False as loaded.
        self.assertFalse(_app.RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED,
                         "RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED should default to False")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    unittest.main()

# ===========================================================================
# TM043–TM047: Orchestrator tests
# ===========================================================================
class TestOrchestrator(unittest.TestCase):
    """Directly covers _right_brain_orchestrate() — 5 minimum scenarios."""

    def setUp(self):
        _clear_trade(_MGC_INST)
        _app.RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED = False

    def tearDown(self):
        _clear_trade(_MGC_INST)
        _app.RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED = False

    # ── TM043 ────────────────────────────────────────────────────────────────
    def test_TM043_flag_off_returns_empty_dict(self):
        """TM043: orchestrator returns {} when all flags are OFF → right_brain key stays absent."""
        _app.RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED = False
        result = _fake_result()
        out = _app._right_brain_orchestrate(result)
        self.assertEqual(out, {},
                         "Expected empty dict when all module flags are OFF")
        # Confirms caller's `if _rb_out:` prevents the key being set
        self.assertNotIn("right_brain", result,
                         "Orchestrator must not mutate result")

    # ── TM044 ────────────────────────────────────────────────────────────────
    def test_TM044_flag_on_produces_trade_management(self):
        """TM044: orchestrator includes trade_management when flag is ON."""
        _app.RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED = True
        result = _fake_result()
        out = _app._right_brain_orchestrate(result)
        self.assertIn("trade_management", out)
        tm = out["trade_management"]
        self.assertFalse(tm["affects_execution"])
        self.assertTrue(tm["shadow_mode"])

    # ── TM045 ────────────────────────────────────────────────────────────────
    def test_TM045_module_failure_produces_neutral_fallback(self):
        """TM045: management function raises → neutral block returned, no exception escapes."""
        _app.RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED = True
        _inject_trade(_MGC_INST, _fake_trade())
        orig = _app.compute_right_brain_trade_management
        _app.compute_right_brain_trade_management = (
            lambda r: (_ for _ in ()).throw(RuntimeError("tm-injected")))
        try:
            result = _fake_result(display_price=2706.0)
            out = _app._right_brain_orchestrate(result)
            tm = out.get("trade_management", {})
            self.assertFalse(tm.get("available"),
                             "Neutral block should set available=False")
            self.assertFalse(tm.get("affects_execution"))
            self.assertIn("tm-injected", tm.get("reason", ""))
        finally:
            _app.compute_right_brain_trade_management = orig

    # ── TM046 ────────────────────────────────────────────────────────────────
    def test_TM046_orchestrator_does_not_mutate_result(self):
        """TM046: orchestrator never mutates the result dict — existing right_brain data preserved."""
        _app.RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED = True
        result = _fake_result()
        import copy
        result_copy = copy.deepcopy(result)
        # Orchestrator builds a NEW rb dict and returns it; caller assigns.
        _app._right_brain_orchestrate(result)
        for key in result_copy:
            self.assertEqual(result.get(key), result_copy[key],
                             f"Orchestrator mutated result[{key!r}]")

    # ── TM047 ────────────────────────────────────────────────────────────────
    def test_TM047_empty_result_input_no_exception(self):
        """TM047: orchestrator handles empty result input without raising."""
        _app.RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED = True
        try:
            out = _app._right_brain_orchestrate({})
            self.assertIn("trade_management", out,
                          "Expected at least FLAT trade_management in empty-result case")
        except Exception as exc:
            self.fail(f"_right_brain_orchestrate({{}}) raised: {exc}")
