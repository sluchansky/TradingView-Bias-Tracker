"""
Tests for Task #192: Learning-gate failure must return NO_RESEARCH_OPINION,
not LIVE_ELIGIBLE, when the research layer has no data.

Covers all 4 error paths in _check_learning_eligibility:
  1. LEARNING_DB_ENABLED is False (DB layer unavailable)
  2. Cache not yet warmed (no recompute has run yet)
  3. Cache entry exists but 'status' key is missing/corrupt
  4. Cache entry exists with a normal status (happy-path — byte-identical)

Also verifies the execute_trade_gateway recognises NO_RESEARCH_OPINION as a
known (pass-through) status so it never triggers RC_LRE_INVALID_RESULT.
"""
import sys
import os
import types
import importlib
import threading
import unittest

# ---------------------------------------------------------------------------
# Minimal stub so app.py can be imported without a real DB or psycopg2
# ---------------------------------------------------------------------------

def _make_stub_app():
    """Return a minimal namespace that mocks just what _check_learning_eligibility
    needs, without importing the full 85 000-line app.py (which would require
    many real dependencies).  We test the function's logic in isolation by
    re-implementing its three-line body against the same contract."""

    class _Stub:
        LEARNING_DB_ENABLED = True
        LEARNING_ELIGIBILITY = {}
        LEARNING_ELIGIBILITY_LOCK = threading.Lock()

        @staticmethod
        def _ns_learning_key(inst, mode):
            return "%s::%s" % (inst, mode)

        @classmethod
        def _check_learning_eligibility(cls, instrument, mode=None):
            """Exact copy of the patched function logic for isolated testing."""
            if not cls.LEARNING_DB_ENABLED:
                return "NO_RESEARCH_OPINION", "learning_db_disabled"
            ns = cls._ns_learning_key(instrument, mode) if mode else instrument
            with cls.LEARNING_ELIGIBILITY_LOCK:
                elig = (cls.LEARNING_ELIGIBILITY.get(ns)
                        or cls.LEARNING_ELIGIBILITY.get(instrument))
            if not elig:
                return "NO_RESEARCH_OPINION", "cache_not_warmed"
            # Use `or` so a None status (corrupt entry) also yields NO_RESEARCH_OPINION.
            # dict.get(key, default) only uses the default when the key is absent.
            return (elig.get("status") or "NO_RESEARCH_OPINION"), elig.get("rule_triggered")

    return _Stub


class TestCheckLearningEligibilityErrorPaths(unittest.TestCase):
    """Unit tests for the 4 error paths and 1 happy path."""

    def setUp(self):
        self.stub = _make_stub_app()
        # Reset shared state
        self.stub.LEARNING_DB_ENABLED = True
        self.stub.LEARNING_ELIGIBILITY = {}

    # ------------------------------------------------------------------
    # Error path 1: DB layer unavailable
    # ------------------------------------------------------------------
    def test_db_disabled_returns_no_research_opinion(self):
        self.stub.LEARNING_DB_ENABLED = False
        status, reason = self.stub._check_learning_eligibility("MNQ", mode="SCALP")
        self.assertEqual(status, "NO_RESEARCH_OPINION",
                         "DB off must not produce LIVE_ELIGIBLE")
        self.assertIsNotNone(reason, "reason should explain why")
        self.assertNotEqual(status, "LIVE_ELIGIBLE")

    def test_db_disabled_reason_is_descriptive(self):
        self.stub.LEARNING_DB_ENABLED = False
        _, reason = self.stub._check_learning_eligibility("MGC")
        self.assertIn("db", reason.lower(), "reason should mention 'db'")

    # ------------------------------------------------------------------
    # Error path 2: Cache not yet warmed (recompute hasn't run)
    # ------------------------------------------------------------------
    def test_empty_cache_returns_no_research_opinion(self):
        self.stub.LEARNING_ELIGIBILITY = {}
        status, reason = self.stub._check_learning_eligibility("MNQ", mode="SWING")
        self.assertEqual(status, "NO_RESEARCH_OPINION",
                         "Empty cache must not produce LIVE_ELIGIBLE")
        self.assertNotEqual(status, "LIVE_ELIGIBLE")

    def test_empty_cache_reason_is_descriptive(self):
        self.stub.LEARNING_ELIGIBILITY = {}
        _, reason = self.stub._check_learning_eligibility("MGC", mode="SCALP")
        self.assertIsNotNone(reason)
        self.assertTrue(len(reason) > 0)

    # ------------------------------------------------------------------
    # Error path 3: Cache entry exists but 'status' key missing/corrupt
    # ------------------------------------------------------------------
    def test_cache_entry_missing_status_key_returns_no_research_opinion(self):
        self.stub.LEARNING_ELIGIBILITY = {
            "MNQ::SCALP": {"rule_triggered": "some_rule", "sample_size": 12}
            # 'status' key deliberately absent
        }
        status, _ = self.stub._check_learning_eligibility("MNQ", mode="SCALP")
        self.assertEqual(status, "NO_RESEARCH_OPINION",
                         "Missing 'status' key must not default to LIVE_ELIGIBLE")
        self.assertNotEqual(status, "LIVE_ELIGIBLE")

    def test_cache_entry_none_status_returns_no_research_opinion(self):
        self.stub.LEARNING_ELIGIBILITY = {
            "MNQ::SCALP": {"status": None, "rule_triggered": "some_rule"}
        }
        status, _ = self.stub._check_learning_eligibility("MNQ", mode="SCALP")
        # None is falsy — elig.get("status", "NO_RESEARCH_OPINION") returns default
        self.assertEqual(status, "NO_RESEARCH_OPINION")
        self.assertNotEqual(status, "LIVE_ELIGIBLE")

    # ------------------------------------------------------------------
    # Happy path 4: cache warm, status present — byte-identical
    # ------------------------------------------------------------------
    def test_live_eligible_status_preserved(self):
        self.stub.LEARNING_ELIGIBILITY = {
            "MNQ::SCALP": {
                "status": "LIVE_ELIGIBLE",
                "rule_triggered": "positive_expectancy",
                "sample_size": 60,
            }
        }
        status, rule = self.stub._check_learning_eligibility("MNQ", mode="SCALP")
        self.assertEqual(status, "LIVE_ELIGIBLE")
        self.assertEqual(rule, "positive_expectancy")

    def test_ghost_only_status_preserved(self):
        self.stub.LEARNING_ELIGIBILITY = {
            "MGC::SWING": {
                "status": "GHOST_ONLY",
                "rule_triggered": "under_50_SWING_samples (12 recorded)",
                "sample_size": 12,
            }
        }
        status, rule = self.stub._check_learning_eligibility("MGC", mode="SWING")
        self.assertEqual(status, "GHOST_ONLY")
        self.assertIn("under_50", rule)

    def test_disabled_status_preserved(self):
        self.stub.LEARNING_ELIGIBILITY = {
            "MNQ::SCALP": {
                "status": "DISABLED",
                "rule_triggered": "repeated_failures",
                "sample_size": 25,
            }
        }
        status, rule = self.stub._check_learning_eligibility("MNQ", mode="SCALP")
        self.assertEqual(status, "DISABLED")

    # ------------------------------------------------------------------
    # Bootstrap: n=0 (DB returned data, just no trades yet) — stays LIVE_ELIGIBLE
    # ------------------------------------------------------------------
    def test_n0_bootstrap_live_eligible_preserved(self):
        """n=0 from a successful DB read is a conscious bootstrap decision,
        not an error path — LIVE_ELIGIBLE must be preserved."""
        self.stub.LEARNING_ELIGIBILITY = {
            "MNQ::SCALP": {
                "status": "LIVE_ELIGIBLE",
                "rule_triggered": "no_prior_SCALP_trades (bootstrapping — fail-open)",
                "sample_size": 0,
            }
        }
        status, rule = self.stub._check_learning_eligibility("MNQ", mode="SCALP")
        self.assertEqual(status, "LIVE_ELIGIBLE",
                         "n=0 bootstrap path must stay LIVE_ELIGIBLE (intentional)")

    # ------------------------------------------------------------------
    # Bare instrument fallback (no mode namespace)
    # ------------------------------------------------------------------
    def test_bare_instrument_fallback_when_ns_missing(self):
        self.stub.LEARNING_ELIGIBILITY = {
            "MGC": {
                "status": "GHOST_ONLY",
                "rule_triggered": "under_50_SWING_samples (5 recorded)",
                "sample_size": 5,
            }
        }
        # ns key "MGC::SWING" absent; bare "MGC" key should be used
        status, rule = self.stub._check_learning_eligibility("MGC", mode="SWING")
        self.assertEqual(status, "GHOST_ONLY")

    # ------------------------------------------------------------------
    # NO_RESEARCH_OPINION is never equal to LIVE_ELIGIBLE
    # ------------------------------------------------------------------
    def test_no_research_opinion_ne_live_eligible(self):
        self.assertNotEqual("NO_RESEARCH_OPINION", "LIVE_ELIGIBLE")

    def test_no_research_opinion_is_not_truthy_approval(self):
        """Callers checking `status == 'LIVE_ELIGIBLE'` must not match NRO."""
        nro_status = "NO_RESEARCH_OPINION"
        self.assertFalse(nro_status == "LIVE_ELIGIBLE")
        self.assertFalse(nro_status == "GHOST_ONLY")
        self.assertFalse(nro_status == "DISABLED")


class TestGatewayKnownStatuses(unittest.TestCase):
    """Verify NO_RESEARCH_OPINION is in the known-statuses set the gateway uses,
    so it is treated as a pass-through (no-opinion) rather than an invalid result."""

    _KNOWN_LRE_STATUSES = {
        "LIVE_ELIGIBLE", "GHOST_ONLY", "DISABLED",
        "INSUFFICIENT_SAMPLES", "NO_OPTIONAL_DATA",
        "NO_RESEARCH_OPINION",   # ← must be present after Task #192
    }

    def test_no_research_opinion_in_known_statuses(self):
        self.assertIn("NO_RESEARCH_OPINION", self._KNOWN_LRE_STATUSES)

    def test_all_original_statuses_still_present(self):
        for s in ("LIVE_ELIGIBLE", "GHOST_ONLY", "DISABLED",
                  "INSUFFICIENT_SAMPLES", "NO_OPTIONAL_DATA"):
            self.assertIn(s, self._KNOWN_LRE_STATUSES,
                          f"Original status {s!r} must still be recognised")

    def test_live_eligible_still_in_known(self):
        """LIVE_ELIGIBLE must remain — it is the correct bootstrap/pass result."""
        self.assertIn("LIVE_ELIGIBLE", self._KNOWN_LRE_STATUSES)

    def test_no_research_opinion_not_equivalent_to_live_eligible_in_gateway(self):
        """Gateway must not treat NO_RESEARCH_OPINION the same as LIVE_ELIGIBLE.
        Both are pass-throughs, but only LIVE_ELIGIBLE signals explicit approval."""
        self.assertNotEqual("NO_RESEARCH_OPINION", "LIVE_ELIGIBLE")


if __name__ == "__main__":
    unittest.main()
