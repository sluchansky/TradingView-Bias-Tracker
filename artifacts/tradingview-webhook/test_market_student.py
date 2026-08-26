import unittest

from market_student import (
    HORIZONS,
    MarketStudentLedger,
    build_forecast_contract,
    normalize_r,
    resolve_terminal_outcome,
)


def ready_result(direction="Long", probability=70):
    return {
        "verdict": f"{direction.upper()} READY",
        "strict_direction": direction,
        "confidence": 82,
        "probability": probability,
        "strategy_key": "SWEEP_RECLAIM",
        "strategy_version": "test-v1",
        "trade_plan": {
            "entry": 100,
            "stop": 98 if direction == "Long" else 102,
            "target": 104 if direction == "Long" else 96,
            "target2": 106 if direction == "Long" else 94,
        },
    }


class ForecastContractTests(unittest.TestCase):
    def test_each_lane_has_an_explicit_horizon(self):
        expected = {
            "SCALP": "minutes",
            "INTRADAY_TREND": "session",
            "SWING": "multi_session",
        }
        for mode, label in expected.items():
            contract = build_forecast_contract(
                ready_result(), "MNQ", mode, source_timestamp="2026-08-26T14:00:00+00:00"
            )
            self.assertEqual(contract.horizon, label)
            self.assertEqual(contract.expiry_minutes, HORIZONS[mode]["expiry_minutes"])
            self.assertTrue(contract.checkpoints_minutes)
            self.assertEqual(contract.entry_price, 100.0)

    def test_contract_preserves_forecast_inputs(self):
        contract = build_forecast_contract(
            ready_result(), "MGC", "SCALP", source_timestamp="2026-08-26T14:00:00+00:00"
        )
        self.assertEqual(contract.direction, "LONG")
        self.assertEqual(contract.invalidation_price, 98.0)
        self.assertEqual(contract.targets, (104.0, 106.0))
        self.assertEqual(contract.expected_move, 4.0)
        self.assertEqual(contract.data_watermark, "2026-08-26T14:00:00+00:00")


class LedgerTests(unittest.TestCase):
    def test_unchanged_wait_heartbeat_is_suppressed(self):
        ledger = MarketStudentLedger(wait_heartbeat_seconds=300)
        wait = {"verdict": "WAIT", "strict_reason": "Need structure"}
        first = ledger.observe(
            wait, "MNQ", "SCALP",
            source_timestamp="2026-08-26T14:00:00+00:00",
        )
        second = ledger.observe(
            wait, "MNQ", "SCALP",
            source_timestamp="2026-08-26T14:00:01+00:00",
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(ledger.health()["stats"]["wait_heartbeats_suppressed"], 1)

    def test_hypotheses_are_immutable_and_exactly_deduped(self):
        ledger = MarketStudentLedger()
        first = ledger.observe(
            ready_result(), "MNQ", "SCALP",
            source_timestamp="2026-08-26T14:00:00+00:00",
            source_system="generic_ghost", source_event_id="exact-1",
        )
        duplicate = ledger.observe(
            ready_result(), "MNQ", "SCALP",
            source_timestamp="2026-08-26T14:00:00+00:00",
            source_system="generic_ghost", source_event_id="exact-1",
        )
        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        self.assertEqual(ledger.health()["hypothesis_count"], 1)

    def test_outcome_requires_one_exact_source_match(self):
        ledger = MarketStudentLedger()
        row = ledger.observe(
            ready_result(), "MNQ", "SCALP",
            source_timestamp="2026-08-26T14:00:00+00:00",
            source_system="generic_ghost", source_event_id="obs-1",
        )
        self.assertIsNotNone(row)
        self.assertIsNone(
            ledger.record_outcome_by_source("generic_ghost", "nearby-id", status="WIN")
        )
        outcome = ledger.record_outcome_by_source(
            "generic_ghost", "obs-1", status="WIN", exit_price=104
        )
        self.assertEqual(outcome["normalized"]["normalized_gross_r"], 2.0)

    def test_reconciliation_rejects_cross_instrument_link(self):
        ledger = MarketStudentLedger()
        row = ledger.observe(
            ready_result(), "MNQ", "SCALP",
            source_timestamp="2026-08-26T14:00:00+00:00",
            source_system="generic_ghost", source_event_id="obs-1",
        )
        self.assertFalse(ledger.reconcile(
            source_system="generic_ghost", source_record_id="obs-1",
            hypothesis_id=row["hypothesis_id"], instrument="MGC", mode="SCALP",
        ))

    def test_ready_alert_transition_has_independent_dedupe(self):
        ledger = MarketStudentLedger()
        row = ledger.observe(
            ready_result(), "MNQ", "SCALP",
            source_timestamp="2026-08-26T14:00:00+00:00",
        )
        self.assertTrue(ledger.meaningful_ready_transition("MNQ", "SCALP", row))
        self.assertFalse(ledger.meaningful_ready_transition("MNQ", "SCALP", row))

    def test_strategy_lab_never_auto_promotes(self):
        ledger = MarketStudentLedger()
        row = ledger.observe(
            ready_result(), "MNQ", "SCALP",
            source_timestamp="2026-08-26T14:00:00+00:00",
            source_system="generic_ghost", source_event_id="obs-1",
        )
        ledger.record_outcome(row["hypothesis_id"], status="WIN", exit_price=104)
        report = ledger.strategy_lab_report(min_closed_sample=1)
        self.assertFalse(report["automatic_promotion"])
        self.assertFalse(report["strategies"][0]["promotion_eligible"])
        self.assertTrue(report["strategies"][0]["promotion_guards"]["manual_review_required"])


class OutcomeTests(unittest.TestCase):
    def test_normalized_r_preserves_source_and_derived_values(self):
        value = normalize_r(
            direction="LONG", entry=100, stop=98, exit_price=104,
            gross_r=None, cost_r=0.1, net_r=None,
        )
        self.assertEqual(value["derived_gross_r"], 2.0)
        self.assertEqual(value["normalized_net_r"], 1.9)

    def test_same_bar_stop_and_target_is_conservatively_loss(self):
        value = resolve_terminal_outcome(
            direction="LONG", entry=100, stop=98, targets=(104,),
            bars=({"high": 105, "low": 97},),
        )
        self.assertEqual(value["outcome"], "LOSS")
        self.assertEqual(value["reason"], "STOP_FIRST")

    def test_missing_contract_is_ambiguous(self):
        value = resolve_terminal_outcome(
            direction="NEUTRAL", entry=None, stop=None, targets=(), bars=()
        )
        self.assertEqual(value["outcome"], "AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()