import unittest

import app
from market_student import MarketStudentLedger


class FvgMarketStudentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.original_ledger = app._MARKET_STUDENT
        self.ledger = MarketStudentLedger()
        app._MARKET_STUDENT = self.ledger

    def tearDown(self):
        app._MARKET_STUDENT = self.original_ledger

    def test_exact_terminal_results_are_recorded_with_source_timestamp(self):
        for index, status in enumerate(("WIN", "LOSS", "NO_ENTRY"), start=1):
            result_id = f"fvg-result-{index}"
            record = {
                "result_id": result_id,
                "experiment_id": f"experiment-{index}",
                "opportunity_id": "opportunity-1",
                "instrument": "MNQ",
                "direction": "Long",
                "variant_name": f"variant-{index}",
                "planned_stop": 99.0,
                "planned_tp1": 102.0,
                "result": status,
                "exit_price": 102.0 if status == "WIN" else 99.0,
                "net_r": 1.0 if status == "WIN" else (-1.0 if status == "LOSS" else None),
                "exit_reason": f"terminal-{status.lower()}",
                "resolved_at": f"2026-08-26T14:0{index}:00+00:00",
            }

            observation = app._market_student_observe_gre_fvg_result(record)
            outcome = app._market_student_resolve_gre_fvg_result(record)

            self.assertIsNotNone(observation)
            self.assertIsNotNone(outcome)
            self.assertEqual(outcome["hypothesis_id"], observation["hypothesis_id"])
            self.assertEqual(outcome["status"], status)
            self.assertEqual(outcome["resolved_at"], record["resolved_at"])

        self.assertEqual(self.ledger.health()["outcome_count"], 3)


if __name__ == "__main__":
    unittest.main()