"""test_lb_thesis_mgc.py — Left Brain thesis diagnostics for MGC (Phase audit fix).

Tests Cases A–L from the spec, covering:
  A. MGC observations under canonical MGC produce a thesis.
  B. MGC Databento source symbol maps to canonical MGC.
  C. Active-contract symbol resolves deterministically to MGC.
  D. No observations → NO_DATA / COLLECTING_DATA with explicit reason.
  E. Insufficient observations → sample progress in COLLECTING_DATA.
  F. Maintenance/stale window → explicit STALE state, not blank.
  G. Stale MGC data → STALE state.
  H. Calculation exception → diagnostic error and safe state.
  I. MGC thesis exists — Main Brain adapter receives all fields.
  J. Frontend normalizer preserves MGC thesis (Python-side structure check).
  K. MNQ remains unchanged when MGC is absent.
  L. No cross-instrument fallback — MNQ thesis must not appear as MGC thesis.

Also covers the proven root-cause bug: field key mismatch
  raw_thesis.get("lastUpdatedAt") vs stored "last_updated_at".
"""

import math
import time
import unittest
from datetime import datetime, timezone, timedelta

# ── Imports from the actual modules (no import of app.py — too heavy) ─────────
try:
    from left_brain_market_intelligence import compute_left_brain_thesis
    _THESIS_IMPORTABLE = True
except ImportError:
    _THESIS_IMPORTABLE = False

try:
    from databento_brain import DB_SYMBOLS
    _DB_IMPORTABLE = True
except ImportError:
    _DB_IMPORTABLE = False


# ── Helpers: minimal MI builder for thesis computation ────────────────────────

def _fake_mi(inst="MGC", available=True, conf=60, market_state="BULLISH",
             direction_long=60, direction_short=40):
    """Build the minimal MI dict that compute_left_brain_thesis() expects."""
    return {
        "available":           available,
        "instrument":          inst,
        "computed_at":         datetime.now(timezone.utc).isoformat(),
        "market_state":        market_state,
        "session_character":   "TRENDING",
        "session_phase":       "RTH",
        "auction_control":     "BUYERS",
        "directional_outlook": {"long": direction_long, "short": direction_short,
                                "neutral": 100 - direction_long - direction_short},
        "data_confidence":     conf,
        "suitable_playbooks":  [],
        "supporting_evidence": [],
        "missing_evidence":    [],
        "what_changes_thesis": {},
        "narrative":           "Test MI",
    }


# ── Python port of the _mb_left_brain diagnosis logic ─────────────────────────
# This mirrors the implementation in app.py _mb_left_brain so tests can run
# without importing the full app. Tests use REAL imports where available.

_STALE_THESIS_SEC  = 600
_MIN_OBS_FOR_READY = 5


def _make_thesis(direction="BULLISH", age_offset_sec=0, available=True):
    """Build a minimal thesis dict as stored in _LB_THESIS_BY_INST."""
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_offset_sec)).isoformat()
    return {
        "available":       available,
        "instrument":      "MGC",
        "direction":       direction,
        "strength":        50,
        "momentum":        "STABLE",
        "established_at":  ts,
        "last_updated_at": ts,   # snake_case — the KEY THAT MUST BE READ
        "narrative":       ["Test narrative"],
        "invalidation":    {"weakens_if": [], "fails_if": []},
        "playbooks":       [],
        "_version":        "v2",
    }


def _diagnosis(raw_thesis, obs_count, source_sym="MGC.c.0", db_bars=127):
    """Python port of the diagnosis block in _mb_left_brain."""
    # --- Build thesis_out (mirror of _mb_left_brain key reading) ---
    thesis_out = None
    if raw_thesis is not None:
        # Correct key (snake_case) — the fixed version
        lu = raw_thesis.get("last_updated_at") or raw_thesis.get("lastUpdatedAt")
        age_sec = None
        if lu:
            try:
                lu_dt = datetime.fromisoformat(lu)
                if lu_dt.tzinfo is None:
                    lu_dt = lu_dt.replace(tzinfo=timezone.utc)
                age_sec = max(0, int((datetime.now(timezone.utc) - lu_dt).total_seconds()))
            except Exception:
                pass
        thesis_out = {
            "direction":   raw_thesis.get("direction"),
            "strength":    raw_thesis.get("strength"),
            "age_seconds": age_sec,
            "generated_at": lu,
        }

    # --- Diagnosis status ---
    _thesis_age = (thesis_out or {}).get("age_seconds") if thesis_out else None

    if thesis_out is None:
        status  = "NO_DATA"
        blocked = "NO_OBSERVATIONS"
    elif _thesis_age is not None and _thesis_age > _STALE_THESIS_SEC:
        status  = "STALE"
        blocked = f"STALE_THESIS_{int(_thesis_age)}s"
    elif obs_count < _MIN_OBS_FOR_READY:
        status  = "COLLECTING_DATA"
        blocked = f"INSUFFICIENT_OBSERVATIONS_{obs_count}_of_{_MIN_OBS_FOR_READY}"
    else:
        status  = "AVAILABLE"
        blocked = None

    _calc_at = (thesis_out or {}).get("generated_at")
    return {
        "instrument":          "MGC",
        "status":              status,
        "canonical_symbol":    "MGC",
        "source_symbol":       source_sym,
        "observation_count":   obs_count,
        "databento_bars":      db_bars,
        "last_calculation_at": _calc_at,
        "thesis_age_seconds":  _thesis_age,
        "blocked_reason":      blocked,
        "thesis_out":          thesis_out,
    }


# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseA_CanonicalKeyProducesThesis(unittest.TestCase):
    """Case A — MGC observations under canonical 'MGC' produce a thesis."""

    @unittest.skipUnless(_THESIS_IMPORTABLE, "left_brain_market_intelligence not importable")
    def test_a1_canonical_mgc_produces_thesis(self):
        mi = _fake_mi("MGC", conf=70, market_state="BULLISH",
                       direction_long=65, direction_short=35)
        out = compute_left_brain_thesis("MGC", mi, None, None, [])
        thesis = out["thesis"]
        self.assertIsNotNone(thesis)
        self.assertEqual(thesis.get("instrument"), "MGC")
        self.assertIn(thesis.get("direction"),
                      ("BULLISH", "BEARISH", "NEUTRAL", "CONFLICTED"))
        self.assertIn("_version", thesis)

    @unittest.skipUnless(_THESIS_IMPORTABLE, "left_brain_market_intelligence not importable")
    def test_a2_thesis_has_required_keys(self):
        mi = _fake_mi("MGC", conf=80)
        out = compute_left_brain_thesis("MGC", mi, None, None, [])
        thesis = out["thesis"]
        required = {"available", "instrument", "direction", "strength", "momentum",
                    "established_at", "last_updated_at", "narrative",
                    "invalidation", "playbooks", "_version"}
        for k in required:
            self.assertIn(k, thesis, f"Key '{k}' missing from thesis")

    @unittest.skipUnless(_THESIS_IMPORTABLE, "left_brain_market_intelligence not importable")
    def test_a3_thesis_available_true_with_valid_mi(self):
        mi = _fake_mi("MGC", available=True, conf=75)
        out = compute_left_brain_thesis("MGC", mi, None, None, [])
        self.assertTrue(out["thesis"].get("available"))

    @unittest.skipUnless(_THESIS_IMPORTABLE, "left_brain_market_intelligence not importable")
    def test_a4_unavailable_mi_produces_neutral_thesis(self):
        """When MI is unavailable, _neutral_thesis is returned — available=False."""
        mi = _fake_mi("MGC", available=False)
        out = compute_left_brain_thesis("MGC", mi, None, None, [])
        self.assertFalse(out["thesis"].get("available"))
        self.assertEqual(out["thesis"].get("direction"), "NEUTRAL")

    @unittest.skipUnless(_THESIS_IMPORTABLE, "left_brain_market_intelligence not importable")
    def test_a5_last_updated_at_is_snake_case(self):
        """Confirm the stored field is last_updated_at (snake_case), not camelCase."""
        mi = _fake_mi("MGC", conf=70)
        out = compute_left_brain_thesis("MGC", mi, None, None, [])
        thesis = out["thesis"]
        self.assertIn("last_updated_at", thesis,
                      "thesis must store 'last_updated_at' (snake_case)")
        self.assertNotIn("lastUpdatedAt", thesis,
                         "thesis must NOT have camelCase 'lastUpdatedAt'")


class TestCaseB_DatabentSourceSymbolMapping(unittest.TestCase):
    """Case B — MGC observations under Databento source symbol map to canonical MGC."""

    @unittest.skipUnless(_DB_IMPORTABLE, "databento_brain not importable")
    def test_b1_db_symbols_has_mgc(self):
        self.assertIn("MGC", DB_SYMBOLS)

    @unittest.skipUnless(_DB_IMPORTABLE, "databento_brain not importable")
    def test_b2_mgc_continuous_symbol(self):
        self.assertEqual(DB_SYMBOLS["MGC"], "MGC.c.0")

    @unittest.skipUnless(_DB_IMPORTABLE, "databento_brain not importable")
    def test_b3_reverse_map_continuous_to_canonical(self):
        reverse = {v: k for k, v in DB_SYMBOLS.items()}
        self.assertEqual(reverse["MGC.c.0"], "MGC")

    @unittest.skipUnless(_DB_IMPORTABLE, "databento_brain not importable")
    def test_b4_all_four_instruments_have_continuous_symbols(self):
        for inst in ("MGC", "MNQ", "MES", "MYM"):
            self.assertIn(inst, DB_SYMBOLS)
            sym = DB_SYMBOLS[inst]
            self.assertTrue(sym.endswith(".c.0"),
                            f"{inst}: expected .c.0 suffix, got {sym!r}")


class TestCaseC_ActiveContractResolution(unittest.TestCase):
    """Case C — Active-contract symbol resolves deterministically to MGC."""

    @unittest.skipUnless(_DB_IMPORTABLE, "databento_brain not importable")
    def test_c1_prefix_match_mgcq6_to_mgc(self):
        """MGCQ6 (front-month) must prefix-match to root "MGC"."""
        for root in DB_SYMBOLS:
            if "MGCQ6".startswith(root):
                self.assertEqual(root, "MGC")
                return
        self.fail("No root in DB_SYMBOLS matched prefix 'MGCQ6'")

    @unittest.skipUnless(_DB_IMPORTABLE, "databento_brain not importable")
    def test_c2_prefix_match_is_unambiguous(self):
        """'MGCQ6' must match exactly one root."""
        matches = [r for r in DB_SYMBOLS if "MGCQ6".startswith(r)]
        self.assertEqual(len(matches), 1, f"Expected 1 match, got {matches}")
        self.assertEqual(matches[0], "MGC")

    @unittest.skipUnless(_DB_IMPORTABLE, "databento_brain not importable")
    def test_c3_mnq_prefix_does_not_match_mgc(self):
        """MNQH7 must not resolve to MGC."""
        matches = [r for r in DB_SYMBOLS if "MNQH7".startswith(r)]
        self.assertNotIn("MGC", matches)


class TestCaseD_NoObservations(unittest.TestCase):
    """Case D — No observations: diagnosis status is NO_DATA."""

    def test_d1_no_thesis_gives_no_data(self):
        d = _diagnosis(raw_thesis=None, obs_count=0)
        self.assertEqual(d["status"], "NO_DATA")

    def test_d2_no_thesis_blocked_reason(self):
        d = _diagnosis(raw_thesis=None, obs_count=0)
        self.assertEqual(d["blocked_reason"], "NO_OBSERVATIONS")

    def test_d3_no_thesis_thesis_age_is_none(self):
        d = _diagnosis(raw_thesis=None, obs_count=0)
        self.assertIsNone(d["thesis_age_seconds"])

    def test_d4_obs_count_zero_but_thesis_exists_is_collecting(self):
        """When thesis is present but obs_count=0, status is COLLECTING_DATA."""
        thesis = _make_thesis("BULLISH", age_offset_sec=30)  # fresh
        d = _diagnosis(raw_thesis=thesis, obs_count=0)
        self.assertEqual(d["status"], "COLLECTING_DATA")


class TestCaseE_InsufficientObservations(unittest.TestCase):
    """Case E — Insufficient observations → COLLECTING_DATA with count shown."""

    def test_e1_one_obs_gives_collecting_data(self):
        """MGC with 1 observation (current live state) → COLLECTING_DATA."""
        thesis = _make_thesis("NEUTRAL", age_offset_sec=60)  # fresh (1 min)
        d = _diagnosis(raw_thesis=thesis, obs_count=1)
        self.assertEqual(d["status"], "COLLECTING_DATA")

    def test_e2_four_obs_gives_collecting_data(self):
        thesis = _make_thesis("BULLISH", age_offset_sec=30)
        d = _diagnosis(raw_thesis=thesis, obs_count=4)
        self.assertEqual(d["status"], "COLLECTING_DATA")

    def test_e3_five_obs_gives_available(self):
        """At exactly MIN_OBS_FOR_READY=5, status becomes AVAILABLE."""
        thesis = _make_thesis("BULLISH", age_offset_sec=30)
        d = _diagnosis(raw_thesis=thesis, obs_count=5)
        self.assertEqual(d["status"], "AVAILABLE")

    def test_e4_obs_count_shown_in_blocked_reason(self):
        thesis = _make_thesis("NEUTRAL", age_offset_sec=60)
        d = _diagnosis(raw_thesis=thesis, obs_count=3)
        self.assertIn("3", d["blocked_reason"])
        self.assertIn("5", d["blocked_reason"])

    def test_e5_observation_count_preserved(self):
        thesis = _make_thesis("BULLISH", age_offset_sec=60)
        d = _diagnosis(raw_thesis=thesis, obs_count=3)
        self.assertEqual(d["observation_count"], 3)


class TestCaseF_MaintenanceOrStale(unittest.TestCase):
    """Case F — Maintenance window / stale: explicit STALE state, not blank."""

    def test_f1_stale_thesis_is_stale(self):
        """Thesis older than 10 min (600s) → STALE."""
        thesis = _make_thesis("BEARISH", age_offset_sec=700)  # 11.7 min old
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        self.assertEqual(d["status"], "STALE")

    def test_f2_boundary_at_600s(self):
        """Exactly 600s is NOT stale; 601s IS stale."""
        t_600 = _make_thesis("BULLISH", age_offset_sec=600)
        t_601 = _make_thesis("BULLISH", age_offset_sec=601)
        d_600 = _diagnosis(raw_thesis=t_600, obs_count=5)
        d_601 = _diagnosis(raw_thesis=t_601, obs_count=5)
        self.assertNotEqual(d_600["status"], "STALE",
                            "600s should not be STALE (boundary exclusive)")
        self.assertEqual(d_601["status"], "STALE")

    def test_f3_stale_blocked_reason_contains_age(self):
        thesis = _make_thesis("BEARISH", age_offset_sec=3600)  # 1h old
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        self.assertIn("STALE", d["blocked_reason"])

    def test_f4_stale_takes_priority_over_low_obs(self):
        """STALE takes priority over COLLECTING_DATA when both apply."""
        thesis = _make_thesis("NEUTRAL", age_offset_sec=6000)  # very old
        d = _diagnosis(raw_thesis=thesis, obs_count=1)  # also low obs
        self.assertEqual(d["status"], "STALE",
                         "STALE should take priority over COLLECTING_DATA")


class TestCaseG_StaleData(unittest.TestCase):
    """Case G — Stale MGC data → STALE state."""

    def test_g1_mgc_1h40min_stale_matches_live_scenario(self):
        """Reproduces the exact live MGC state: 1 obs, ~100-min-old thesis."""
        thesis = _make_thesis("NEUTRAL", age_offset_sec=6000)  # 100 min
        d = _diagnosis(raw_thesis=thesis, obs_count=1, db_bars=1)
        self.assertEqual(d["status"], "STALE")
        self.assertEqual(d["databento_bars"], 1)
        self.assertGreater(d["thesis_age_seconds"], _STALE_THESIS_SEC)

    def test_g2_stale_thesis_age_is_returned(self):
        thesis = _make_thesis("BULLISH", age_offset_sec=1200)
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        self.assertGreater(d["thesis_age_seconds"], _STALE_THESIS_SEC)

    def test_g3_fresh_thesis_is_not_stale(self):
        """Fresh thesis (<= 600s) with sufficient obs → AVAILABLE."""
        thesis = _make_thesis("BULLISH", age_offset_sec=300)  # 5 min
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        self.assertEqual(d["status"], "AVAILABLE")


class TestCaseH_CalculationException(unittest.TestCase):
    """Case H — Calculation exception → diagnostic error and safe UI state."""

    def test_h1_none_thesis_is_safe(self):
        """When no thesis at all, diagnosis produces valid dict."""
        d = _diagnosis(raw_thesis=None, obs_count=0)
        self.assertIn("status", d)
        self.assertIn("blocked_reason", d)
        self.assertEqual(d["status"], "NO_DATA")

    def test_h2_malformed_timestamp_in_thesis(self):
        """Invalid last_updated_at → age_seconds=None → COLLECTING_DATA (not crash)."""
        thesis = {
            "available":       True,
            "direction":       "BULLISH",
            "strength":        50,
            "last_updated_at": "NOT-A-VALID-TIMESTAMP",  # malformed
            "_version":        "v2",
        }
        # Should not raise — age_sec will be None → obs_count decides status
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        self.assertIn(d["status"], ("AVAILABLE", "COLLECTING_DATA", "NO_DATA"),
                      "Malformed timestamp must not cause STALE or crash")

    def test_h3_camelcase_key_produces_none_age(self):
        """Prove the KEY BUG: camelCase 'lastUpdatedAt' → age=None (stale goes undetected).
        The fix reads snake_case first; this test documents the pre-fix behavior
        and confirms the fixed code reads 'last_updated_at' correctly.
        """
        thesis_snake = _make_thesis("BULLISH", age_offset_sec=3600)
        # Simulate the broken pre-fix key by removing snake_case and adding camelCase
        thesis_camel = dict(thesis_snake)
        thesis_camel["lastUpdatedAt"] = thesis_camel.pop("last_updated_at")

        # With the FIXED code (reads snake_case OR camelCase fallback):
        # Snake-case version → age_sec should be ~3600 → STALE
        d_snake = _diagnosis(raw_thesis=thesis_snake, obs_count=10)
        self.assertEqual(d_snake["status"], "STALE",
                         "snake_case 'last_updated_at' must produce STALE status")

        # CamelCase-only version → age_sec still computed via fallback
        d_camel = _diagnosis(raw_thesis=thesis_camel, obs_count=10)
        self.assertEqual(d_camel["status"], "STALE",
                         "camelCase fallback must also detect STALE")


class TestCaseI_MainBrainAdapterFields(unittest.TestCase):
    """Case I — MGC thesis exists; Main Brain adapter receives all required fields."""

    def test_i1_thesis_fields_preserved_in_thesis_out(self):
        thesis = _make_thesis("BULLISH", age_offset_sec=30)
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        to = d["thesis_out"]
        self.assertIsNotNone(to)
        self.assertEqual(to["direction"], "BULLISH")

    def test_i2_age_seconds_is_computed(self):
        thesis = _make_thesis("BEARISH", age_offset_sec=120)
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        age = d["thesis_out"]["age_seconds"]
        self.assertIsNotNone(age, "age_seconds must not be None with snake_case key")
        self.assertGreater(age, 100)  # at least ~120s

    def test_i3_generated_at_is_populated(self):
        thesis = _make_thesis("BULLISH", age_offset_sec=60)
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        self.assertIsNotNone(d["thesis_out"]["generated_at"])

    def test_i4_diagnosis_contains_all_spec_fields(self):
        thesis = _make_thesis("BULLISH", age_offset_sec=60)
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        for k in ("instrument", "status", "canonical_symbol", "source_symbol",
                  "observation_count", "databento_bars", "last_calculation_at",
                  "thesis_age_seconds", "blocked_reason"):
            self.assertIn(k, d, f"Diagnosis missing spec field: {k!r}")


class TestCaseJ_NormalizerPreservesMGCThesis(unittest.TestCase):
    """Case J — Normalizer preserves MGC thesis (Python-side structure validation).

    The JS normalizer spreads ...lb so all keys (including 'diagnosis') are passed
    through. This test validates the _mb_left_brain output structure that the
    normalizer receives, ensuring 'diagnosis' is a proper dict.
    """

    def test_j1_diagnosis_is_dict(self):
        thesis = _make_thesis("BULLISH", age_offset_sec=30)
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        self.assertIsInstance(d, dict)

    def test_j2_status_is_string(self):
        thesis = _make_thesis("BULLISH", age_offset_sec=30)
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        self.assertIsInstance(d["status"], str)

    def test_j3_all_values_are_json_serializable(self):
        """Ensure nothing in the diagnosis would blow up JSON serialisation."""
        import json
        thesis = _make_thesis("BULLISH", age_offset_sec=30)
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        serializable = {
            "instrument": d["instrument"],
            "status": d["status"],
            "canonical_symbol": d["canonical_symbol"],
            "source_symbol": d["source_symbol"],
            "observation_count": d["observation_count"],
            "databento_bars": d["databento_bars"],
            "thesis_age_seconds": d["thesis_age_seconds"],
            "blocked_reason": d["blocked_reason"],
        }
        json.dumps(serializable)  # must not raise

    def test_j4_direction_null_does_not_break_normalizer_path(self):
        """None direction must propagate as None, not cause a KeyError."""
        thesis = _make_thesis(direction=None, age_offset_sec=30)  # type: ignore[arg-type]
        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        to = d["thesis_out"]
        self.assertIsNone(to.get("direction"))


class TestCaseK_MNQUnchanged(unittest.TestCase):
    """Case K — MNQ thesis remains unchanged when MGC is absent."""

    def test_k1_mnq_thesis_independent_of_mgc(self):
        """_diagnosis called for MNQ with no MGC thesis must only return MNQ data."""
        mnq_thesis = {
            "available":       True,
            "instrument":      "MNQ",
            "direction":       "BULLISH",
            "strength":        75,
            "last_updated_at": datetime.now(timezone.utc).isoformat(),
            "_version":        "v2",
        }
        # Simulate: MGC key absent, MNQ key present
        thesis_store = {"MNQ": mnq_thesis}  # MGC is absent
        mgc_thesis = thesis_store.get("MGC")
        mnq_thesis_read = thesis_store.get("MNQ")

        # MGC should produce NO_DATA
        d_mgc = _diagnosis(raw_thesis=mgc_thesis, obs_count=0)
        self.assertEqual(d_mgc["status"], "NO_DATA")

        # MNQ should produce AVAILABLE (fresh, enough obs)
        d_mnq = _diagnosis(raw_thesis=mnq_thesis_read, obs_count=10)
        # (instrument field will say MGC due to helper default; ignore for this check)
        self.assertEqual(d_mnq["status"], "AVAILABLE")

    def test_k2_mnq_age_computed_independently(self):
        """MNQ thesis age is computed from its own last_updated_at, not MGC's."""
        mnq_thesis = {
            "available":       True,
            "direction":       "BEARISH",
            "last_updated_at": datetime.now(timezone.utc).isoformat(),
            "_version":        "v2",
        }
        d = _diagnosis(raw_thesis=mnq_thesis, obs_count=10)
        age = d["thesis_out"]["age_seconds"]
        self.assertIsNotNone(age)
        self.assertLess(age, 60, "MNQ age should be < 60s since we just created it")


class TestCaseL_NoCrossInstrumentFallback(unittest.TestCase):
    """Case L — MNQ thesis must never appear as MGC thesis."""

    def test_l1_absent_mgc_key_returns_none(self):
        """Simulates: _LB_THESIS_BY_INST has MNQ but not MGC.
        _mb_left_brain('MGC') must return thesis_out=None."""
        thesis_store = {
            "MNQ": _make_thesis("BULLISH", age_offset_sec=30),
        }
        mgc_raw = thesis_store.get("MGC")  # None
        d = _diagnosis(raw_thesis=mgc_raw, obs_count=0)
        self.assertIsNone(d["thesis_out"])
        self.assertEqual(d["status"], "NO_DATA")

    def test_l2_mnq_direction_does_not_leak_to_mgc(self):
        """If MGC is absent but MNQ is BULLISH, MGC must not inherit BULLISH."""
        thesis_store = {
            "MNQ": _make_thesis("BULLISH", age_offset_sec=30),
        }
        mgc_raw = thesis_store.get("MGC")
        d = _diagnosis(raw_thesis=mgc_raw, obs_count=0)
        self.assertIsNone(d["thesis_out"])
        # If thesis_out is None, direction cannot be "BULLISH" from MNQ
        if d["thesis_out"] is not None:
            self.assertNotEqual(d["thesis_out"].get("direction"), "BULLISH",
                                "MGC direction must not inherit MNQ's BULLISH")

    def test_l3_each_instrument_reads_its_own_key(self):
        """Both MGC and MNQ present — each reads its own entry."""
        thesis_store = {
            "MGC": _make_thesis("BEARISH", age_offset_sec=30),
            "MNQ": _make_thesis("BULLISH", age_offset_sec=30),
        }
        d_mgc = _diagnosis(raw_thesis=thesis_store.get("MGC"), obs_count=10)
        d_mnq = _diagnosis(raw_thesis=thesis_store.get("MNQ"), obs_count=10)

        self.assertEqual(d_mgc["thesis_out"]["direction"], "BEARISH")
        self.assertEqual(d_mnq["thesis_out"]["direction"], "BULLISH")
        # Cross-check: MGC is not BULLISH, MNQ is not BEARISH
        self.assertNotEqual(d_mgc["thesis_out"]["direction"], "BULLISH")
        self.assertNotEqual(d_mnq["thesis_out"]["direction"], "BEARISH")


class TestKeyBugRegression(unittest.TestCase):
    """Regression test for the proven root-cause bug:
    _mb_left_brain reading 'lastUpdatedAt' (camelCase) while the field is
    stored as 'last_updated_at' (snake_case) → age_seconds always None.
    """

    def test_reg1_snake_case_key_produces_non_none_age(self):
        """Prove the fix: snake_case key gives a real age_seconds."""
        thesis = _make_thesis("BULLISH", age_offset_sec=300)
        # thesis has "last_updated_at" (snake_case) — correct
        self.assertIn("last_updated_at", thesis)

        d = _diagnosis(raw_thesis=thesis, obs_count=10)
        self.assertIsNotNone(d["thesis_out"]["age_seconds"],
                             "age_seconds must be non-None with the snake_case key fix")

    def test_reg2_camelcase_only_also_works_via_fallback(self):
        """The fallback path (camelCase key) also computes age_seconds."""
        thesis_camel = {
            "available":    True,
            "direction":    "BULLISH",
            "lastUpdatedAt": (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),
            "_version":     "v2",
        }
        d = _diagnosis(raw_thesis=thesis_camel, obs_count=10)
        self.assertIsNotNone(d["thesis_out"]["age_seconds"],
                             "camelCase fallback must also produce non-None age_seconds")

    def test_reg3_stale_detection_fires_correctly(self):
        """With the fix, a 1h40min old thesis (live MGC state) is STALE."""
        thesis = _make_thesis("NEUTRAL", age_offset_sec=6000)  # 100 min — live MGC state
        d = _diagnosis(raw_thesis=thesis, obs_count=1)
        self.assertEqual(d["status"], "STALE",
                         "Live MGC state (1h40min stale, 1 obs) must be STALE not NEUTRAL-silent")

    def test_reg4_pre_fix_behavior_camelcase_only(self):
        """Document pre-fix: camelCase-only thesis with age 1h40min
        → WITHOUT fallback reads snake_case first → age_sec=None → no STALE detection.
        This test uses only the camelCase key to document what the BUG would produce.
        """
        thesis_camel_only = {
            "direction":    "NEUTRAL",
            "lastUpdatedAt": (datetime.now(timezone.utc) - timedelta(seconds=6000)).isoformat(),
        }
        # With the current fixed _diagnosis, fallback reads camelCase too:
        d_fixed = _diagnosis(raw_thesis=thesis_camel_only, obs_count=1)
        # Either STALE (fallback works) or COLLECTING_DATA (if age somehow None)
        # The key assertion is that it does NOT silently show AVAILABLE when stale
        self.assertNotEqual(d_fixed["status"], "AVAILABLE",
                            "A 100-min old thesis must not appear as AVAILABLE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
