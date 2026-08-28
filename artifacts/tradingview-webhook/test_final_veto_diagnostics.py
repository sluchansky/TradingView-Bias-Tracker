"""Focused contract checks for final READY->WAIT veto observability.

Run:
  python3 artifacts/tradingview-webhook/test_final_veto_diagnostics.py

These tests call only display/serialization helpers. They do not execute a
trade, change a threshold, access a database, or start a scheduler.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import app  # noqa: E402
import authoritative_verdict_history as avh  # noqa: E402


class TestFinalVetoDiagnostics(unittest.TestCase):
    @staticmethod
    def _legacy_wait_it_strict(entry=21_000.0):
        """A genuine legacy-IT WAIT that the native pipeline may still advance."""
        strict = app.evaluate_strict_setup(
            entry, "MNQ1!", entry - 5, "above", entry + 30, entry - 10,
            40, 10, 55, [], volatility={"atr_pts": 50, "regime": "normal"},
            session=None, cooldown_active=False,
        )
        strict.update(
            label="WAIT", score=70, direction="Long",
            reason="legacy IT gate waits", missing=["edge_score"],
            candidate="Long", readiness="FULL",
        )
        return strict

    @staticmethod
    def _native_it_context(entry=21_000.0):
        return {
            "instrument": "MNQ", "context_1h": "ALIGNED",
            "extension_state": "NORMAL", "time_ok": True,
            "location_quality": "GOOD", "setup_family": "TREND_PULLBACK",
            "confirmation_complete": True, "confirmation_missing": [],
            "structural_stop_valid": True, "structural_stop_level": entry - 20,
            "structural_stop_pts": 20, "structural_stop_source": "test",
            "data_freshness_ok": True, "stale_timeframes": [],
            "cooldown_remaining": 0, "daily_trade_count": 0, "daily_trade_cap": 3,
            "session_levels": {
                "overnight_high": entry + 50, "overnight_low": None,
                "asia_high": None, "asia_low": None, "prior_high": None,
                "prior_low": None, "major_15m_swing_highs": [entry + 50],
                "major_15m_swing_lows": [],
            },
            "daily_levels": {}, "intraday_bias": "Long",
            "trend_alignment": "BULLISH",
        }

    def _native_it_runtime_patches(self, strict, context):
        entry = 21_000.0
        return (
            patch.object(app, "evaluate_strict_setup", return_value=strict),
            patch.object(app, "compute_intraday_trend_context", return_value=context),
            patch.object(app, "get_vwap", return_value=(entry - 5, "above")),
            patch.object(
                app, "get_volatility",
                return_value={"atr_pts": 50, "regime": "normal", "label": "Normal"},
            ),
            patch.object(
                app, "market_session_status",
                return_value={
                    "open": True, "status": "OPEN", "next_open": None,
                    "next_open_et": None, "reason": "test session",
                },
            ),
        )

    def test_scalp_quality_codes_stay_individual_and_operator_visible(self):
        vetoes = app._final_veto_records("scalp_quality", [
            ("quality", "setup quality 60 < 70"),
            ("room", "only 0.8R room to the opposing zone (need 1.25R)"),
            ("opposing_zone", "price entering the opposing zone"),
        ])
        presentation = app._build_operator_presentation({
            "active_ticker": "MNQ",
            "strict_label": "WAIT",
            "strict_reason": "SCALP filter: setup quality 60 < 70.",
            "strict_direction": "Long",
            "strict_missing": [],
            "strict_blockers": [],
            "final_veto_reasons": vetoes,
            "current_price": 21450,
            "vwap_value": 21440,
            "vwap_status": "ok",
            "structure_state": {
                "state": "REVERSAL_CONFIRMED",
                "confirmed": True,
                "next_event": "BOS continuation",
                "next_event_reason": "Reversal is confirmed; wait for continuation.",
            },
        })

        self.assertEqual([item["code"] for item in vetoes],
                         ["quality", "room", "opposing_zone"])
        self.assertEqual([item["stage"] for item in vetoes],
                         ["scalp_quality", "scalp_quality", "scalp_quality"])
        self.assertEqual(presentation["strict_blockers"], [])
        self.assertEqual(presentation["final_veto_reasons"], vetoes)
        self.assertFalse(presentation["is_actionable"])
        # A confirmed reversal remains confirmed; final veto diagnostics do not
        # create a new structure requirement.
        self.assertTrue(presentation["structure_guidance"]["confirmed"])

    def test_legacy_result_without_final_veto_field_remains_safe(self):
        presentation = app._build_operator_presentation({
            "active_ticker": "MGC",
            "strict_label": "WAIT",
            "strict_reason": "Waiting for confirmation.",
            "strict_missing": ["structure_confirmed"],
        })

        self.assertEqual(presentation["strict_blockers"], ["structure_confirmed"])
        self.assertEqual(presentation["final_veto_reasons"], [])

    def test_native_intraday_ready_to_wait_veto_is_structured_and_persistable(self):
        """IT bypasses legacy strict readiness, so capture from the live verdict."""
        vetoes = []
        app._record_final_veto_if_ready(vetoes, "LONG READY", "intraday_trend_entry", [
            ("daily_cap", "daily trade limit reached"),
        ])
        result = {
            "verdict": "WAIT",
            "strict_label": "WAIT",
            "strict_reason": "INTRADAY TREND filter: daily trade limit reached.",
            "strict_direction": "Long",
            "strict_missing": [],
            "strict_blockers": [],
            "final_veto_reasons": vetoes,
            "edge_score": 96,
        }
        presentation = app._build_operator_presentation(result)
        snapshot = avh._build_snapshot(result, "MNQ", "INTRADAY_TREND", {})

        self.assertEqual(vetoes[0]["stage"], "intraday_trend_entry")
        self.assertEqual(vetoes[0]["code"], "daily_cap")
        self.assertEqual(presentation["final_veto_reasons"], vetoes)
        self.assertEqual(snapshot["final_veto_reasons"], vetoes)
        self.assertEqual(snapshot["strict_blockers"], [])

    def test_native_it_runtime_veto_ignores_legacy_ready_label(self):
        """End-to-end: native IT can become READY from legacy WAIT, then be vetoed."""
        original_mode = app.TRADING_MODE
        strict = self._legacy_wait_it_strict()
        context = self._native_it_context()
        try:
            app.TRADING_MODE = "INTRADAY_TREND"
            patches = self._native_it_runtime_patches(strict, context)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patch.object(
                app, "_it_entry_veto_reasons",
                return_value=[("daily_cap", "daily trade limit reached")],
            ):
                result = app.full_analysis(
                    current_price_override=21_000.0, ticker_override="MNQ")
        finally:
            app.TRADING_MODE = original_mode

        self.assertEqual(result["verdict"], "WAIT")
        self.assertEqual(result["final_veto_reasons"], [{
            "stage": "intraday_trend_entry",
            "code": "daily_cap",
            "reason": "daily trade limit reached",
        }])
        self.assertEqual(result["operator_presentation"]["final_veto_reasons"],
                         result["final_veto_reasons"])

    def test_enforced_thesis_wait_has_its_own_final_reason(self):
        vetoes = []
        app._record_final_veto_if_ready(vetoes, "SHORT READY", "thesis_enforcement", [
            ("thesis_block", "Thesis enforcement blocked the ready setup."),
        ])
        result = {
            "verdict": "WAIT",
            "strict_label": "WAIT",
            "strict_reason": "Thesis enforcement blocked the ready setup.",
            "strict_direction": "Short",
            "strict_missing": [],
            "strict_blockers": [],
            "final_veto_reasons": vetoes,
        }

        presentation = app._build_operator_presentation(result)
        self.assertEqual(presentation["verdict"], "WAIT")
        self.assertEqual(presentation["final_veto_reasons"][0]["stage"],
                         "thesis_enforcement")

    def test_enforced_thesis_runtime_veto_syncs_returned_verdict(self):
        """End-to-end: the enforced thesis veto updates the actual returned result."""
        original_mode = app.TRADING_MODE
        original_enforcement = app._THESIS_ENFORCEMENT_MODE
        strict = self._legacy_wait_it_strict()
        context = self._native_it_context()
        try:
            app.TRADING_MODE = "INTRADAY_TREND"
            app._THESIS_ENFORCEMENT_MODE = "enforced"
            patches = self._native_it_runtime_patches(strict, context)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patch.object(
                app, "_it_entry_veto_reasons", return_value=[],
            ), patch.object(
                app, "_apply_thesis",
                side_effect=lambda _i, _s, verdict, *_, **__: (verdict, {}),
            ), patch.object(
                app, "_compute_thesis_gate",
                return_value={"action": "BLOCK", "reason": "Thesis conflicts with setup."},
            ):
                result = app.full_analysis(
                    current_price_override=21_000.0, ticker_override="MNQ")
        finally:
            app.TRADING_MODE = original_mode
            app._THESIS_ENFORCEMENT_MODE = original_enforcement

        self.assertEqual(result["verdict"], "WAIT")
        self.assertEqual(result["strict_label"], "WAIT")
        self.assertEqual(result["final_veto_reasons"], [{
            "stage": "thesis_enforcement",
            "code": "thesis_block",
            "reason": "Thesis conflicts with setup.",
        }])


if __name__ == "__main__":
    unittest.main()