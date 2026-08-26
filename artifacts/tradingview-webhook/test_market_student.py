import unittest
import threading

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


class _ClaimCursor:
    def __init__(self, state):
        self.state = state
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        if "INSERT INTO market_student_ready_alerts" in sql:
            key = params[0]
            with self.state["lock"]:
                if key in self.state["claims"]:
                    self.row = None
                else:
                    self.state["claims"].add(key)
                    self.row = (key,)

    def fetchone(self):
        return self.row


class _ClaimConnection:
    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _ClaimCursor(self.state)

    def commit(self):
        self.state["commits"] += 1

    def rollback(self):
        self.state["rollbacks"] += 1

    def close(self):
        pass


class _RestoreCursor:
    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql):
        if "FROM market_student_observations" in sql:
            self.rows = [(
                "observation-restored", "MNQ", "SCALP", "visual_brain",
                "visual-row-42", "2026-08-26T14:00:00+00:00",
                "fingerprint-restored", {"verdict": "LONG READY"},
            )]
        elif "FROM market_student_hypotheses" in sql:
            self.rows = [(
                "hypothesis-restored", "observation-restored", "MNQ", "SCALP",
                {
                    "direction": "LONG", "entry_price": 100.0,
                    "invalidation_price": 98.0, "targets": [104.0],
                },
                "2026-08-26T14:00:00+00:00",
            )]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows


class _RestoreConnection:
    def cursor(self):
        return _RestoreCursor()

    def close(self):
        pass


class _LargeRestoreCursor:
    ROWS = 1001

    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql):
        if "FROM market_student_observations" in sql:
            self.rows = [
                (
                    f"observation-{index}", "MNQ", "SCALP", "gre_fvg_result",
                    f"result-{index}", "2026-08-26T14:00:00+00:00",
                    f"fingerprint-{index}", {"verdict": "LONG READY"},
                )
                for index in range(self.ROWS)
            ]
        elif "FROM market_student_hypotheses" in sql:
            self.rows = [
                (
                    f"hypothesis-{index}", f"observation-{index}", "MNQ", "SCALP",
                    {
                        "strategy": "FVG_REVISIT:BASELINE",
                        "direction": "LONG", "entry_price": 100.0,
                        "invalidation_price": 99.0, "targets": [102.0],
                    },
                    "2026-08-26T14:00:00+00:00",
                )
                for index in range(self.ROWS)
            ]
        elif "FROM market_student_outcomes" in sql:
            self.rows = [
                (
                    f"outcome-{index}", f"hypothesis-{index}", "WIN",
                    {"normalized_net_r": 1.0}, "2026-08-26T14:05:00+00:00",
                )
                for index in range(self.ROWS)
            ]
        elif "FROM market_student_reconciliations" in sql:
            self.rows = [
                (
                    f"reconciliation-{index}", "gre_fvg_result", f"result-{index}",
                    f"hypothesis-{index}", "MNQ", "SCALP", {}, True,
                )
                for index in range(self.ROWS)
            ]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows


class _LargeRestoreConnection:
    def cursor(self):
        return _LargeRestoreCursor()

    def close(self):
        pass


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

    def test_exact_source_outcome_resolves_after_restore(self):
        ledger = MarketStudentLedger(db_conn_fn=_RestoreConnection)
        ledger.configure(persistence_enabled=True)
        restored = ledger.restore()
        ledger.configure(persistence_enabled=False)

        self.assertEqual(restored["observations"], 1)
        self.assertEqual(restored["hypotheses"], 1)
        outcome = ledger.record_outcome_by_source(
            "visual_brain", "visual-row-42", status="WIN", exit_price=104
        )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome["hypothesis_id"], "hypothesis-restored")
        self.assertEqual(outcome["normalized"]["normalized_gross_r"], 2.0)
        self.assertEqual(ledger.health()["outcome_count"], 1)

    def test_strategy_lab_keeps_more_than_one_thousand_restored_links(self):
        ledger = MarketStudentLedger(db_conn_fn=_LargeRestoreConnection)
        ledger.configure(persistence_enabled=True)

        restored = ledger.restore()
        report = ledger.strategy_lab_report(min_closed_sample=30)

        self.assertEqual(restored["reconciliations"], 1001)
        self.assertEqual(len(ledger._reconciliation), 1001)
        self.assertEqual(len(report["strategies"]), 1)
        evidence = report["strategies"][0]["evidence"]
        self.assertEqual(evidence["canonical_reconciled"], 1001)
        self.assertEqual(evidence["legacy_unvalidated"], 0)
        self.assertTrue(evidence["complete_canonical"])

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

    def test_ready_alert_claim_is_atomic_across_instances(self):
        state = {
            "claims": set(), "lock": threading.Lock(),
            "commits": 0, "rollbacks": 0,
        }
        ledgers = [
            MarketStudentLedger(db_conn_fn=lambda: _ClaimConnection(state))
            for _ in range(2)
        ]
        rows = []
        for ledger in ledgers:
            ledger.configure(persistence_enabled=True)
            rows.append(ledger.observe(
                ready_result(), "MNQ", "SCALP",
                source_timestamp="2026-08-26T14:00:00+00:00",
                source_system="generic_ghost", source_event_id="atomic-alert",
            ))
        claims = [None, None]
        threads = [
            threading.Thread(
                target=lambda i=i: claims.__setitem__(
                    i, ledgers[i].claim_ready_alert("MNQ", "SCALP", rows[i])
                )
            )
            for i in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(value is not None for value in claims), 1)
        self.assertEqual(len(state["claims"]), 1)

    def test_ready_alert_is_not_sent_without_durable_claim_storage(self):
        ledger = MarketStudentLedger()
        row = ledger.observe(
            ready_result(), "MNQ", "SCALP",
            source_timestamp="2026-08-26T14:00:00+00:00",
        )
        self.assertIsNone(ledger.claim_ready_alert("MNQ", "SCALP", row))
        self.assertEqual(ledger.alert_health()["claim_unavailable"], 1)

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