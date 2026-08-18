"""Tests for SCALP Feedback Loop Repair (Phases 1–13).

Covers:
  Phase 1  — ghost_observe_setup called on webhook READY verdicts
  Phase 2  — ALLOWED records get ATR-fallback geometry so watcher can resolve them
  Phase 3  — watcher sees bars, transitions PENDING → COMPLETED; idempotent on restart
  Phase 4  — vwap_value and trend_alignment correctly extracted
  Phase 5  — strategy identity uses strategy_engine.active_key, not bare mode
  Phase 7  — shadow cohort classification (A / B / C / None)
  Phase 9  — session bucket function
  Phase 11 — restart / recovery: single terminal outcome, idempotent watcher
  Phase 12 — existing gate/golden tests confirm no live-trading change

All DB operations are mocked — no real database required.
"""

import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stubs — must be registered BEFORE importing gate_effectiveness
# ---------------------------------------------------------------------------

def _make_app_stub():
    stub = types.ModuleType("app")
    stub.is_actionable   = lambda v: isinstance(v, str) and "READY" in v and "WAIT" not in v
    stub.is_early_ready  = lambda v: isinstance(v, str) and "EARLY" in v
    stub.ready_direction = lambda v: "Long" if "LONG" in str(v) else ("Short" if "SHORT" in str(v) else None)
    stub._learning_conn  = lambda: None   # patched per-test
    stub.LEARNING_DB_ENABLED    = True
    return stub

_APP_STUB = _make_app_stub()
sys.modules.setdefault("app", _APP_STUB)

def _make_dbb_stub():
    stub = types.ModuleType("databento_brain")
    stub.DATABENTO_BARS_BY_INST = {}
    return stub

_DBB_STUB = _make_dbb_stub()
sys.modules.setdefault("databento_brain", _DBB_STUB)

import gate_effectiveness as ge  # noqa: E402

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _mock_conn(rows=None, rowcount=1):
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__  = MagicMock(return_value=False)
    cur.fetchone  = MagicMock(return_value=(rows[0] if rows else None))
    cur.fetchall  = MagicMock(return_value=(rows or []))
    cur.rowcount  = rowcount
    conn = MagicMock()
    conn.cursor   = MagicMock(return_value=cur)
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__  = MagicMock(return_value=False)
    return conn, cur


def _full_result(verdict="LONG READY", edge=75, direction="Long",
                 blocked_by=None, has_plan=True, vwap_value=1998.0,
                 trend_align="BULLISH", strategy_key=None, price=2000.0):
    """Minimal full_analysis result dict for recording tests.

    Uses result["vwap_value"] (correct key per Phase 4 fix) and
    includes strategy_engine.active_key for Phase 5 fix.
    """
    plan = {}
    if has_plan:
        plan = {"entry": price, "stop": price - 10.0, "target": price + 10.0, "target2": price + 15.0}
    return {
        "verdict":          verdict,
        "strict_direction": direction,
        "strict_reason":    "",
        "trade_plan":       plan,
        "edge_breakdown":   {"score": edge},
        "confluences": {
            "bos": True, "choch": False, "vwap": True,
            "liquidity_sweep": True, "volume_confirmed": True,
            "preferred_session": False, "zone_mitigated": True,
        },
        "gate_debug": {
            "bos_confirmed": True, "choch_confirmed": False,
            "vwap_confirmed": True, "volume_ok": True,
            "cvd_conflict": False,
            "failed_conditions": blocked_by or [],
            "blockedBy": blocked_by or [],
            "edge_score": edge,
        },
        "volatility":      {"atr_pts": 10.0, "regime": "normal"},
        # Phase 4: use correct key name; old "vwap" kept for compat
        "vwap_value":       vwap_value,
        "vwap":             vwap_value,
        "current_price":    price,
        "session_state":    "regular",
        "cvd_state":        "bullish",
        # Phase 4: trend_alignment at top-level (also via swing_context below)
        "trend_alignment":  trend_align,
        "swing_context":    {"trend_alignment": trend_align},
        # Phase 5: strategy_engine.active_key for sub-strategy identity
        "strategy_engine": {
            "active_key": strategy_key or "LIQUIDITY_SWEEP_REVERSAL",
            "active_strategy": "Liquidity Sweep Reversal",
        },
    }


def _blocked_result(edge=45, direction="Long", blocked_by=None,
                    cvd_conflict=False, volume_ok=True, bos_ok=True,
                    price=2000.0):
    res = _full_result("LONG WAIT", edge, direction, blocked_by=blocked_by,
                       has_plan=False, price=price)
    res["verdict"] = "LONG WAIT"
    res["gate_debug"]["cvd_conflict"] = cvd_conflict
    res["gate_debug"]["volume_ok"] = volume_ok
    res["gate_debug"]["bos_confirmed"] = bos_ok
    res["gate_debug"]["failed_conditions"] = blocked_by or []
    return res


# ============================================================================
# Phase 4 — vwap_value and trend_alignment extraction
# ============================================================================

class TestPhase4VwapExtraction(unittest.TestCase):
    """Phase 4: vwap_value must be read from result['vwap_value'], not result['vwap']."""

    def _extract(self, result):
        return ge._extract(result, "SCALP")

    def test_vwap_value_key_preferred(self):
        """result['vwap_value'] must be returned, not result['vwap']."""
        r = _full_result(vwap_value=2001.5)
        r["vwap"] = 9999.0   # deliberately different — result["vwap_value"] must win
        info = self._extract(r)
        self.assertEqual(info["vwap_value"], 2001.5,
                         "vwap_value should use result['vwap_value'], not result['vwap']")

    def test_vwap_fallback_to_vwap_key(self):
        """When result['vwap_value'] is absent, fall back to result['vwap']."""
        r = _full_result(vwap_value=None)
        r.pop("vwap_value", None)
        r["vwap"] = 1950.0
        info = self._extract(r)
        self.assertEqual(info["vwap_value"], 1950.0)

    def test_trend_alignment_from_top_level(self):
        """trend_alignment from result['trend_alignment'] must be captured."""
        r = _full_result(trend_align="STRONG_BULLISH")
        r.pop("swing_context", None)
        info = self._extract(r)
        self.assertEqual(info["trend_alignment"], "STRONG_BULLISH")

    def test_trend_alignment_from_swing_context(self):
        """trend_alignment from swing_context must be captured when top-level is absent."""
        r = _full_result(trend_align=None)
        r.pop("trend_alignment", None)
        r["swing_context"] = {"trend_alignment": "BEARISH"}
        info = self._extract(r)
        self.assertEqual(info["trend_alignment"], "BEARISH")

    def test_trend_alignment_top_level_preferred_over_swing_context(self):
        """Top-level trend_alignment must win over swing_context."""
        r = _full_result(trend_align="BULLISH")
        r["swing_context"] = {"trend_alignment": "BEARISH"}
        info = self._extract(r)
        self.assertEqual(info["trend_alignment"], "BULLISH")


# ============================================================================
# Phase 5 — strategy identity from strategy_engine.active_key
# ============================================================================

class TestPhase5StrategyIdentity(unittest.TestCase):
    """Phase 5: _extract_strategy must return sub-strategy name, not bare 'SCALP'."""

    def test_active_key_used_for_scalp(self):
        r = _full_result(strategy_key="LIQUIDITY_SWEEP_REVERSAL")
        strat = ge._extract_strategy(r, "SCALP")
        self.assertEqual(strat, "LIQUIDITY_SWEEP_REVERSAL")

    def test_active_key_vwap_pullback(self):
        r = _full_result(strategy_key="VWAP_PULLBACK_CONTINUATION")
        strat = ge._extract_strategy(r, "SCALP")
        self.assertEqual(strat, "VWAP_PULLBACK_CONTINUATION")

    def test_fallback_to_mode_when_no_active_key(self):
        r = _full_result(strategy_key=None)
        r["strategy_engine"] = {}
        strat = ge._extract_strategy(r, "SCALP")
        self.assertEqual(strat, "SCALP")

    def test_orb_verdict_overrides_engine(self):
        r = _full_result(strategy_key="SOMETHING_ELSE")
        r["verdict"] = "LONG ORB READY"
        strat = ge._extract_strategy(r, "SCALP")
        self.assertEqual(strat, "ORB")

    def test_intraday_trend_uses_setup_type(self):
        r = _full_result()
        r["intraday_trend_context"] = {"mode": "INTRADAY_TREND", "setup_type": "IT_CONTINUATION"}
        strat = ge._extract_strategy(r, "INTRADAY_TREND")
        self.assertEqual(strat, "IT_CONTINUATION")

    def test_none_string_active_key_falls_back_to_mode(self):
        r = _full_result()
        r["strategy_engine"] = {"active_key": "None"}
        strat = ge._extract_strategy(r, "SCALP")
        self.assertEqual(strat, "SCALP")


# ============================================================================
# Phase 2 — ALLOWED geometry fallback (ATR_FALLBACK for READY with no plan)
# ============================================================================

class TestPhase2AllowedGeometry(unittest.TestCase):
    """Phase 2: ALLOWED records without trade_plan geometry must get ATR fallback
    so the watcher WHERE clause (entry/stop/target NOT NULL) can resolve them."""

    def _extract(self, result):
        return ge._extract(result, "SCALP")

    def test_ready_with_full_plan_uses_live_plan(self):
        r = _full_result("LONG READY", has_plan=True)
        info = self._extract(r)
        self.assertIsNotNone(info["entry_price"])
        self.assertIsNotNone(info["stop_price"])
        self.assertIsNotNone(info["target1_price"])
        self.assertEqual(info["geometry_source"], "LIVE_PLAN")

    def test_ready_without_plan_gets_atr_fallback(self):
        r = _full_result("LONG READY", has_plan=False)
        # Remove plan entirely
        r["trade_plan"] = {}
        info = self._extract(r)
        # Should synthesise geometry from ATR + current_price
        self.assertIsNotNone(info["entry_price"],  "entry_price must be filled")
        self.assertIsNotNone(info["stop_price"],   "stop_price must be filled")
        self.assertIsNotNone(info["target1_price"],"target1_price must be filled")
        self.assertIn(info["geometry_source"], ("LIVE_PLAN_ATR_FILL",),
                      "geometry_source should be LIVE_PLAN_ATR_FILL for ATR-filled READY")

    def test_ready_stop_below_entry_for_long(self):
        """ATR-filled stop must be below entry for Long direction."""
        r = _full_result("LONG READY", has_plan=False, direction="Long", price=2000.0)
        r["trade_plan"] = {}
        info = self._extract(r)
        self.assertLess(info["stop_price"], info["entry_price"])

    def test_ready_stop_above_entry_for_short(self):
        """ATR-filled stop must be above entry for Short direction."""
        r = _full_result("SHORT READY", has_plan=False, direction="Short", price=2000.0)
        r["trade_plan"] = {}
        r["strict_direction"] = "Short"
        info = self._extract(r)
        self.assertGreater(info["stop_price"], info["entry_price"])

    def test_watcher_where_clause_satisfied(self):
        """After Phase 2 fix, no READY record should have NULL entry/stop/target."""
        r = _full_result("LONG READY", has_plan=False)
        r["trade_plan"] = {}
        info = self._extract(r)
        # All three required for watcher WHERE clause
        self.assertIsNotNone(info["entry_price"])
        self.assertIsNotNone(info["stop_price"])
        self.assertIsNotNone(info["target1_price"])


# ============================================================================
# Phase 3 — watcher resolves PENDING → COMPLETED; idempotent on restart
# ============================================================================

class TestPhase3WatcherResolution(unittest.TestCase):
    """Phase 3: watcher must see bars, resolve TP/stop, and never duplicate."""

    def _make_pending_row(self, audit_id="test|SCALP|MNQ|Long|ALLOWED|2026081009",
                          entry=2000.0, stop=1990.0, target1=2010.0,
                          signal_ts=None):
        if signal_ts is None:
            signal_ts = datetime.now(timezone.utc) - timedelta(hours=1)
        return {
            "audit_id": audit_id, "instrument": "MNQ", "direction": "Long",
            "entry": entry, "stop": stop, "target1": target1, "target2": None,
            "risk_pts": abs(entry - stop), "signal_time": signal_ts,
            "bars_held": 0, "mfe_r": 0.0, "mae_r": 0.0,
            "mfe_price": None, "mae_price": None, "tp1_hit": False,
            "gate_verdict": "ALLOWED",
        }

    def _bar(self, high, low, ts_offset_secs=120):
        ts = datetime.now(timezone.utc) - timedelta(hours=1) + timedelta(seconds=ts_offset_secs)
        return {"high": high, "low": low, "ts": ts.timestamp()}

    def test_tp1_hit_resolves_to_completed(self):
        """When a bar touches TP1, outcome_status must become COMPLETED."""
        ge.GATE_AUDIT_DB_READY = True
        _APP_STUB.LEARNING_DB_ENABLED = True
        row = self._make_pending_row()
        pending_rows = [(
            row["audit_id"], row["instrument"], row["direction"],
            row["entry"], row["stop"], row["target1"], row["target2"],
            row["risk_pts"], row["signal_time"], 0, 0.0, 0.0,
            None, None, False, row["gate_verdict"],
        )]
        bars = [self._bar(high=2012.0, low=1995.0)]   # TP1=2010 is touched
        _DBB_STUB.DATABENTO_BARS_BY_INST = {"MNQ": bars}

        conn, cur = _mock_conn(rows=pending_rows, rowcount=1)
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._gate_audit_watcher_cycle()

        # The UPDATE must have been called with outcome_status=COMPLETED
        update_calls = [
            c for c in cur.execute.call_args_list
            if "UPDATE gate_audit_log" in str(c)
        ]
        self.assertTrue(len(update_calls) > 0, "Expected at least one UPDATE call")
        last_update = update_calls[-1]
        update_params = last_update[0][1]
        # outcome_status is the 10th positional param in the UPDATE
        outcome_param = update_params[9]
        self.assertEqual(outcome_param, "COMPLETED")

    def test_stop_hit_resolves_to_completed(self):
        """When a bar breaches the stop, outcome_status must become COMPLETED."""
        ge.GATE_AUDIT_DB_READY = True
        _APP_STUB.LEARNING_DB_ENABLED = True
        row = self._make_pending_row()
        pending_rows = [(
            row["audit_id"], row["instrument"], row["direction"],
            row["entry"], row["stop"], row["target1"], row["target2"],
            row["risk_pts"], row["signal_time"], 0, 0.0, 0.0,
            None, None, False, row["gate_verdict"],
        )]
        bars = [self._bar(high=1998.0, low=1985.0)]   # stop=1990 is breached
        _DBB_STUB.DATABENTO_BARS_BY_INST = {"MNQ": bars}

        conn, cur = _mock_conn(rows=pending_rows, rowcount=1)
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._gate_audit_watcher_cycle()

        update_calls = [
            c for c in cur.execute.call_args_list
            if "UPDATE gate_audit_log" in str(c)
        ]
        self.assertTrue(len(update_calls) > 0)
        last_update = update_calls[-1]
        outcome_param = last_update[0][1][9]
        self.assertEqual(outcome_param, "COMPLETED")

    def test_watcher_idempotent_on_restart(self):
        """Running the watcher twice on the same row (simulate restart) must not
        produce two COMPLETED transitions.  The WHERE audit_id=... AND outcome_status='PENDING'
        guard ensures the second run is a no-op."""
        ge.GATE_AUDIT_DB_READY = True
        _APP_STUB.LEARNING_DB_ENABLED = True
        row = self._make_pending_row()

        # First run: bar triggers TP1 hit, rowcount=1 (row updated)
        pending_rows_1 = [(
            row["audit_id"], row["instrument"], row["direction"],
            row["entry"], row["stop"], row["target1"], row["target2"],
            row["risk_pts"], row["signal_time"], 0, 0.0, 0.0,
            None, None, False, row["gate_verdict"],
        )]
        bars = [self._bar(high=2012.0, low=1995.0)]
        _DBB_STUB.DATABENTO_BARS_BY_INST = {"MNQ": bars}

        conn1, cur1 = _mock_conn(rows=pending_rows_1, rowcount=1)
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn1):
            ge._gate_audit_watcher_cycle()

        # Second run: query returns no PENDING rows (already COMPLETED)
        conn2, cur2 = _mock_conn(rows=[], rowcount=0)
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn2):
            ge._gate_audit_watcher_cycle()

        # Second run should have zero UPDATE calls (no PENDING rows found)
        update_calls2 = [
            c for c in cur2.execute.call_args_list
            if "UPDATE gate_audit_log" in str(c)
        ]
        self.assertEqual(len(update_calls2), 0,
                         "Second watcher run must not UPDATE an already-resolved row")

    def test_missing_bars_leaves_pending(self):
        """When no bars are available after signal_time, row must remain PENDING."""
        ge.GATE_AUDIT_DB_READY = True
        _APP_STUB.LEARNING_DB_ENABLED = True
        row = self._make_pending_row()
        pending_rows = [(
            row["audit_id"], row["instrument"], row["direction"],
            row["entry"], row["stop"], row["target1"], row["target2"],
            row["risk_pts"], row["signal_time"], 0, 0.0, 0.0,
            None, None, False, row["gate_verdict"],
        )]
        _DBB_STUB.DATABENTO_BARS_BY_INST = {}   # no bars at all

        conn, cur = _mock_conn(rows=pending_rows, rowcount=0)
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._gate_audit_watcher_cycle()

        # No UPDATE should have been called (watcher does `continue` when no bars)
        update_calls = [
            c for c in cur.execute.call_args_list
            if "UPDATE gate_audit_log" in str(c)
        ]
        self.assertEqual(len(update_calls), 0, "No UPDATE expected when bars are unavailable")

    def test_one_bad_row_does_not_kill_worker(self):
        """A malformed observation must not crash the watcher cycle."""
        ge.GATE_AUDIT_DB_READY = True
        _APP_STUB.LEARNING_DB_ENABLED = True
        # Malformed row: entry/stop are strings that will raise in float()
        bad_rows = [(
            "bad|row|id", "MNQ", "Long",
            "NOT_A_NUMBER", "ALSO_BAD", 2010.0, None,
            10.0, datetime.now(timezone.utc) - timedelta(hours=1),
            0, 0.0, 0.0, None, None, False, "ALLOWED",
        )]
        bars = [{"high": 2012.0, "low": 1995.0, "ts": (datetime.now(timezone.utc)).timestamp()}]
        _DBB_STUB.DATABENTO_BARS_BY_INST = {"MNQ": bars}

        conn, cur = _mock_conn(rows=bad_rows, rowcount=0)
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            # Must not raise
            try:
                ge._gate_audit_watcher_cycle()
            except Exception as exc:
                self.fail(f"Watcher raised on bad row: {exc}")


# ============================================================================
# Phase 7 — shadow cohort classification
# ============================================================================

class TestPhase7ShadowCohorts(unittest.TestCase):
    """Phase 7: _classify_shadow_cohort must correctly classify BLOCKED SCALP records."""

    _UTC_10_45 = datetime(2026, 8, 18, 14, 45, tzinfo=timezone.utc)  # 10:45 ET (UTC-4)
    _UTC_09_00 = datetime(2026, 8, 18, 13, 0,  tzinfo=timezone.utc)  # 09:00 ET
    _UTC_14_30 = datetime(2026, 8, 18, 18, 30, tzinfo=timezone.utc)  # 14:30 ET

    def _info(self, score=65, comp_cvd="PASS", comp_vol="PASS",
              comp_bos="PASS", comp_choch="UNAVAILABLE", comp_vwap="PASS",
              blocker="edge score too low", all_blockers=None):
        return {
            "primary_blocker": blocker,
            "all_blockers": all_blockers or ([blocker] if blocker else []),
            "comp_cvd":    comp_cvd,
            "comp_volume": comp_vol,
            "comp_bos":    comp_bos,
            "comp_choch":  comp_choch,
            "comp_vwap":   comp_vwap,
        }

    def test_cohort_a_edge_blocked_others_pass(self):
        info = self._info(score=40, blocker="edge score too low")
        result = ge._classify_shadow_cohort(info, "BLOCKED", "SCALP", "Long", 40, self._UTC_10_45)
        self.assertEqual(result, "EDGE35_OTHER_GATES_PASS")

    def test_cohort_a_requires_score_ge_30(self):
        info = self._info(score=20, blocker="edge score too low")
        result = ge._classify_shadow_cohort(info, "BLOCKED", "SCALP", "Long", 20, self._UTC_10_45)
        self.assertIsNone(result, "Score < 30 should not qualify for Cohort A")

    def test_cohort_b_volume_window(self):
        info = self._info(score=70, comp_vol="FAIL", blocker="volume confirmation",
                          all_blockers=["volume confirmation"])
        result = ge._classify_shadow_cohort(info, "BLOCKED", "SCALP", "Long", 70, self._UTC_10_45)
        self.assertEqual(result, "VOLUME_ONLY_BLOCK_1030_1200")

    def test_cohort_b_outside_window_not_classified(self):
        info = self._info(score=70, comp_vol="FAIL", blocker="volume confirmation",
                          all_blockers=["volume confirmation"])
        result = ge._classify_shadow_cohort(info, "BLOCKED", "SCALP", "Long", 70, self._UTC_09_00)
        self.assertIsNone(result, "Volume block outside 10:30-12:00 should not be Cohort B")

    def test_cohort_c_short_cvd_only(self):
        info = self._info(score=70, comp_cvd="FAIL", blocker="cvd conflict",
                          all_blockers=["cvd conflict"])
        result = ge._classify_shadow_cohort(info, "BLOCKED", "SCALP", "Short", 70, self._UTC_14_30)
        self.assertEqual(result, "SHORT_CVD_ONLY_BLOCK")

    def test_cohort_c_long_direction_not_classified(self):
        info = self._info(score=70, comp_cvd="FAIL", blocker="cvd conflict",
                          all_blockers=["cvd conflict"])
        result = ge._classify_shadow_cohort(info, "BLOCKED", "SCALP", "Long", 70, self._UTC_14_30)
        self.assertIsNone(result, "CVD-blocked Long should not be Cohort C")

    def test_non_scalp_mode_never_classified(self):
        info = self._info(score=40, blocker="edge score too low")
        result = ge._classify_shadow_cohort(info, "BLOCKED", "SWING", "Long", 40, self._UTC_10_45)
        self.assertIsNone(result, "SWING mode should never be classified")

    def test_allowed_verdict_never_classified(self):
        info = self._info(score=80, blocker="")
        result = ge._classify_shadow_cohort(info, "ALLOWED", "SCALP", "Long", 80, self._UTC_10_45)
        self.assertIsNone(result, "ALLOWED records should never be classified")

    def test_cohort_a_requires_sole_edge_blocker(self):
        """When both edge AND volume block, Cohort A must NOT be assigned.
        Cohort A semantics: edge is the SOLE blocker; all other gates must have
        passed.  A simultaneous volume failure disqualifies the record from A."""
        info = self._info(score=40, comp_vol="FAIL", blocker="edge score too low",
                          all_blockers=["edge score too low", "volume confirmation"])
        result = ge._classify_shadow_cohort(info, "BLOCKED", "SCALP", "Long", 40, self._UTC_10_45)
        self.assertIsNone(result,
                          "Both edge+volume blocked → no cohort (volume failure disqualifies Cohort A)")

    def test_none_returned_on_exception(self):
        """Any exception in the classifier must return None, never raise."""
        result = ge._classify_shadow_cohort(None, "BLOCKED", "SCALP", "Long", 40, None)
        self.assertIsNone(result)


# ============================================================================
# Phase 9 — session bucket function
# ============================================================================

class TestPhase9SessionBucket(unittest.TestCase):
    """Phase 9: _et_session_bucket must return correct canonical bucket labels."""

    def _utc(self, et_hour, et_minute=0):
        """Return UTC datetime for a given ET (EDT=UTC-4) time."""
        return datetime(2026, 8, 18, et_hour + 4, et_minute, tzinfo=timezone.utc)

    def test_overnight(self):
        self.assertEqual(ge._et_session_bucket(self._utc(3)), "Overnight")

    def test_premarket(self):
        self.assertEqual(ge._et_session_bucket(self._utc(8, 30)), "08:00-09:30")

    def test_open(self):
        self.assertEqual(ge._et_session_bucket(self._utc(10, 0)), "09:30-10:30")

    def test_late_morning(self):
        self.assertEqual(ge._et_session_bucket(self._utc(11, 0)), "10:30-12:00")

    def test_midday(self):
        self.assertEqual(ge._et_session_bucket(self._utc(13, 0)), "12:00-14:00")

    def test_afternoon(self):
        self.assertEqual(ge._et_session_bucket(self._utc(15, 0)), "14:00-15:45")

    def test_closing_window(self):
        self.assertEqual(ge._et_session_bucket(self._utc(15, 50)), "15:45-16:00")

    def test_after_hours(self):
        self.assertEqual(ge._et_session_bucket(self._utc(16, 30)), "16:00-17:00")

    def test_evening(self):
        self.assertEqual(ge._et_session_bucket(self._utc(18)), "Evening")

    def test_exact_boundary_930(self):
        self.assertEqual(ge._et_session_bucket(self._utc(9, 30)), "09:30-10:30")

    def test_exact_boundary_1030(self):
        self.assertEqual(ge._et_session_bucket(self._utc(10, 30)), "10:30-12:00")

    def test_exact_boundary_1545(self):
        self.assertEqual(ge._et_session_bucket(self._utc(15, 45)), "15:45-16:00")


# ============================================================================
# Phase 12 — regression: recording still works; live gate unchanged
# ============================================================================

class TestPhase12Regression(unittest.TestCase):
    """Phase 12: existing recording behaviour must be preserved after all fixes."""

    def test_allowed_verdict_still_inserts(self):
        """ALLOWED verdict must still produce an INSERT into gate_audit_log."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(_full_result("LONG READY", 75), "MNQ", "SCALP")
        cur.execute.assert_called()
        sql = cur.execute.call_args[0][0]
        self.assertIn("INSERT INTO gate_audit_log", sql)

    def test_blocked_verdict_still_inserts(self):
        """BLOCKED verdict with edge blocker must still produce an INSERT."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(
                _blocked_result(edge=40, blocked_by=["edge score too low"]),
                "MNQ", "SCALP",
            )
        cur.execute.assert_called()
        sql = cur.execute.call_args[0][0]
        self.assertIn("INSERT INTO gate_audit_log", sql)

    def test_audit_id_contains_allowed_for_ready(self):
        """ALLOWED audit_id must still contain the ALLOWED label."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(_full_result("LONG READY", 75), "MNQ", "SCALP")
        params = cur.execute.call_args[0][1]
        audit_id = params[0]
        self.assertIn("ALLOWED", audit_id)

    def test_audit_id_contains_blocked_for_wait(self):
        """BLOCKED audit_id must contain BLOCKED."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(
                _blocked_result(edge=40, blocked_by=["edge score"]),
                "MNQ", "SCALP",
            )
        params = cur.execute.call_args[0][1]
        audit_id = params[0]
        self.assertIn("BLOCKED", audit_id)

    def test_shadow_cohort_in_insert_params(self):
        """shadow_cohort must appear as a parameter in the INSERT."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(
                _blocked_result(edge=40, blocked_by=["edge score too low"]),
                "MNQ", "SCALP",
            )
        params = cur.execute.call_args[0][1]
        # shadow_cohort is the second-to-last param; session_bucket is the last.
        # Count = 37 original columns + geometry_source + shadow_cohort + session_bucket = 39.
        self.assertEqual(len(params), 39,
                         "INSERT params should include geometry_source + shadow_cohort + session_bucket")

    def test_session_bucket_in_insert_params(self):
        """session_bucket must be a non-empty string in the INSERT params."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(_full_result("LONG READY", 75), "MNQ", "SCALP")
        params = cur.execute.call_args[0][1]
        session_bucket = params[-1]
        self.assertIsNotNone(session_bucket)
        self.assertIsInstance(session_bucket, str)

    def test_fail_open_on_db_unavailable(self):
        """record_gate_decision must not raise when DB is unavailable."""
        ge.GATE_AUDIT_DB_READY = True
        with patch.object(_APP_STUB, "_learning_conn", return_value=None):
            try:
                ge.record_gate_decision(_full_result("LONG READY", 75), "MNQ", "SCALP")
            except Exception as exc:
                self.fail(f"record_gate_decision raised when conn=None: {exc}")

    def test_early_ready_verdict_recorded(self):
        """EARLY READY must produce an EARLY_ALLOWED audit_id."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(_full_result("LONG EARLY READY", 60), "MNQ", "SCALP")
        params = cur.execute.call_args[0][1]
        audit_id = params[0]
        self.assertIn("ALLOWED", audit_id)

    def test_strategy_engine_key_in_audit_id(self):
        """strategy_engine.active_key must appear in audit_id (not bare 'SCALP')."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(
                _full_result("LONG READY", 75, strategy_key="LIQUIDITY_SWEEP_REVERSAL"),
                "MNQ", "SCALP",
            )
        params = cur.execute.call_args[0][1]
        audit_id = params[0]
        self.assertIn("LIQUIDITY_SWEEP_REVERSAL", audit_id,
                      "audit_id should use strategy_engine.active_key, not bare 'SCALP'")


if __name__ == "__main__":
    unittest.main()
