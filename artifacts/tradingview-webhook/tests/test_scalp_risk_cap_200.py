"""
Regression tests: SCALP max-risk cap raised from $100 → $200.

Verifies:
  1.  SCALP default MAX_RISK_DOLLARS == 200
  2.  SCALP max_risk_cap() == 200 (no env override)
  3.  MNQ 50-pt stop  ($100/contract) → ≥1 contract (not over_cap)
  4.  MNQ 75-pt stop  ($150/contract) → exactly 1 contract (not over_cap)
  5.  MNQ 100-pt stop ($200/contract) → exactly 1 contract (at cap, not over)
  6.  MNQ 101-pt stop ($202/contract) → 0 contracts (over_cap)
  7.  SWING MAX_RISK_DOLLARS unchanged at 500
  8.  SWING max_risk_cap() == 500 when TRADING_MODE=SWING
  9.  INTRADAY_TREND MAX_RISK_DOLLARS unchanged at 500
  10. MICRO_SCALP: not in MODES dict — falls back to SCALP (effective cap = 200)
  11. Env MAX_RISK_DOLLARS_PER_TRADE overrides SCALP default when set at call time
"""

import os
import sys
import unittest

# ── Locate the webhook app package ───────────────────────────────────────────
_HERE    = os.path.dirname(__file__)
_APP_DIR = os.path.dirname(_HERE)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

_ENV_KEYS = ("TRADING_MODE", "MAX_RISK_DOLLARS_PER_TRADE", "MAX_RISK_DOLLARS")


def _load_app(trading_mode="SCALP", extra_env=None):
    """
    (Re-)import app with a clean module cache and the requested TRADING_MODE.
    Returns the imported module. Restores the env to pre-call state afterward.
    """
    saved = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
    os.environ["TRADING_MODE"] = trading_mode
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)

    for name in list(sys.modules):
        if "app" in name and "test" not in name:
            del sys.modules[name]

    import app as _app  # noqa: PLC0415

    # Restore env
    os.environ.pop("TRADING_MODE", None)
    for k in (extra_env or {}):
        os.environ.pop(k, None)
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v

    return _app


# ── Module-level fixtures (import once; tests read but don't mutate them) ────
_SCALP = _load_app("SCALP")
_SWING = _load_app("SWING")


class TestScalpRiskCapConfig(unittest.TestCase):
    """Config-layer checks — no DB, no env overrides needed."""

    def setUp(self):
        # Guarantee MAX_RISK_DOLLARS_PER_TRADE is absent for cap-reading tests.
        self._saved_override = os.environ.pop("MAX_RISK_DOLLARS_PER_TRADE", None)

    def tearDown(self):
        os.environ.pop("MAX_RISK_DOLLARS_PER_TRADE", None)
        if self._saved_override is not None:
            os.environ["MAX_RISK_DOLLARS_PER_TRADE"] = self._saved_override

    # 1 ── SCALP config dict value = 200 ─────────────────────────────────────
    def test_scalp_modes_dict_value_is_200(self):
        val = _SCALP.MODES["SCALP"]["MAX_RISK_DOLLARS"]
        self.assertEqual(val, 200,
                         f"MODES['SCALP']['MAX_RISK_DOLLARS'] must be 200, got {val}")

    # 2 ── max_risk_cap() returns 200 for SCALP (no env override) ─────────────
    def test_scalp_max_risk_cap_is_200(self):
        cap = _SCALP.max_risk_cap()
        self.assertEqual(cap, 200,
                         f"SCALP max_risk_cap() must return 200, got {cap}")

    # 7 ── SWING config dict value unchanged at 500 ───────────────────────────
    def test_swing_modes_dict_value_unchanged_at_500(self):
        val = _SCALP.MODES["SWING"]["MAX_RISK_DOLLARS"]
        self.assertEqual(val, 500,
                         f"MODES['SWING']['MAX_RISK_DOLLARS'] must remain 500, got {val}")

    # 8 ── max_risk_cap() == 500 when loaded under TRADING_MODE=SWING ─────────
    def test_swing_max_risk_cap_is_500(self):
        cap = _SWING.max_risk_cap()
        self.assertEqual(cap, 500,
                         f"SWING max_risk_cap() must return 500, got {cap}")

    # 9 ── INTRADAY_TREND config dict value unchanged at 500 ──────────────────
    def test_intraday_trend_modes_dict_value_is_500(self):
        val = _SCALP.MODES["INTRADAY_TREND"]["MAX_RISK_DOLLARS"]
        self.assertEqual(val, 500,
                         f"MODES['INTRADAY_TREND']['MAX_RISK_DOLLARS'] must remain 500, got {val}")

    # 10 ── MICRO_SCALP: not a standalone MODES entry; falls back to SCALP ────
    def test_micro_scalp_not_in_modes_uses_scalp_fallback(self):
        self.assertNotIn("MICRO_SCALP", _SCALP.MODES,
                         "MICRO_SCALP must not have its own MODES entry "
                         "(cfg_for falls back to MODES['SCALP'])")
        effective = _SCALP.MODES.get("MICRO_SCALP", _SCALP.MODES["SCALP"])["MAX_RISK_DOLLARS"]
        self.assertEqual(effective, 200,
                         f"MICRO_SCALP effective MAX_RISK_DOLLARS via SCALP fallback must be 200, got {effective}")


class TestScalpRiskContractSizing(unittest.TestCase):
    """
    Sizing correctness via _risk_capped_contracts().

    MNQ: point_value = $2.00/pt.
    SCALP max_risk_cap() = $200 (default, no env override).
    budget = min(account × risk_pct, $200).
    """

    def setUp(self):
        self._saved_override = os.environ.pop("MAX_RISK_DOLLARS_PER_TRADE", None)

    def tearDown(self):
        os.environ.pop("MAX_RISK_DOLLARS_PER_TRADE", None)
        if self._saved_override is not None:
            os.environ["MAX_RISK_DOLLARS_PER_TRADE"] = self._saved_override

    def _size(self, stop_pts, instrument="MNQ", account=100_000, risk_pct=0.005):
        spec = _SCALP.INSTRUMENT_SPECS[instrument]   # already-flat dict
        cap  = _SCALP.max_risk_cap()                 # 200 (no env override in setUp)
        return _SCALP._risk_capped_contracts(
            stop_dist=stop_pts,
            point_value=spec["point_value"],
            account_size=account,
            risk_pct=risk_pct,
            hard_cap_dollars=cap,
        )

    # 3 ── 50 pts × $2 = $100/contract → 2 contracts fit in $200 budget ──────
    def test_mnq_50pt_stop_eligible(self):
        res = self._size(50.0)
        self.assertFalse(res["over_cap"],
                         "MNQ 50-pt stop ($100/contract) must NOT be over_cap with $200 cap")
        self.assertGreaterEqual(res["contracts"], 1,
                                "MNQ 50-pt stop must produce ≥1 contract")

    # 4 ── 75 pts × $2 = $150/contract → floor(200/150) = 1 contract ─────────
    def test_mnq_75pt_stop_eligible_1_contract(self):
        res = self._size(75.0)
        self.assertFalse(res["over_cap"],
                         "MNQ 75-pt stop ($150/contract) must NOT be over_cap with $200 cap")
        self.assertEqual(res["contracts"], 1,
                         "MNQ 75-pt stop must produce exactly 1 contract")

    # 5 ── 100 pts × $2 = $200/contract == cap → floor(200/200) = 1 ──────────
    def test_mnq_100pt_stop_exactly_at_cap(self):
        res = self._size(100.0)
        self.assertFalse(res["over_cap"],
                         "MNQ 100-pt stop ($200/contract) must NOT be over_cap at exact cap")
        self.assertEqual(res["contracts"], 1,
                         "MNQ 100-pt stop must produce exactly 1 contract at the cap")

    # 6 ── 101 pts × $2 = $202/contract > $200 → 0 contracts ─────────────────
    def test_mnq_101pt_stop_over_cap(self):
        res = self._size(101.0)
        self.assertTrue(res["over_cap"],
                        "MNQ 101-pt stop ($202/contract) must be over_cap with $200 cap")
        self.assertEqual(res["contracts"], 0,
                         "MNQ 101-pt stop must produce 0 contracts when over_cap")

    # 11 ── Env override takes precedence over the $200 SCALP default ─────────
    def test_env_override_takes_precedence_over_200_default(self):
        """
        With MAX_RISK_DOLLARS_PER_TRADE=150 in the env at call time,
        max_risk_cap() must return 150, not 200.
        setUp already cleared any prior value; we set+clear around the call.
        """
        os.environ["MAX_RISK_DOLLARS_PER_TRADE"] = "150"
        try:
            cap = _SCALP.max_risk_cap()
        finally:
            os.environ.pop("MAX_RISK_DOLLARS_PER_TRADE", None)
        self.assertEqual(cap, 150,
                         f"MAX_RISK_DOLLARS_PER_TRADE=150 must override SCALP $200 default, got {cap}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
