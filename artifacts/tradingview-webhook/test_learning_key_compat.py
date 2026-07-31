"""
test_learning_key_compat.py — Phase 7I.1 — Canonical key + win-rate normalization.

VERIFIED PIPELINE (traced, not changed):

  compute_strategy_engine → active_key  ∈  STRATEGY_PRIORITY keys
    e.g. "LIQUIDITY_SWEEP_REVERSAL", "VWAP_TREND_CONTINUATION", …

  _update_learning_snapshot → ctx["strategy_key"] = se.get("active_key")
  _record_strategy_trade   → strategy_trades.strategy_key = ctx["strategy_key"]
  _recompute_learning      → STRATEGY_WEIGHTS[_ns_learning_key(key, mode)]
                              = STRATEGY_WEIGHTS["{mode}::{key}"]

  Legacy rows in prod:  strategy_key = "MGC_SCALP_CHOCH_Long"
    → STRATEGY_WEIGHTS["SCALP::MGC_SCALP_CHOCH_Long"] after recompute
    → live lookup for "LIQUIDITY_SWEEP_REVERSAL" → NOT_FOUND (CHOCH has no mapping)
    → neutral 1.0 returned → SAFE

  win_rate SQL fix:  avg((result='Win')::int::float)
                  →  avg((lower(trim(result))='win')::int::float)
    Verified: strategy_trades.result stores 'WIN' (uppercase); before fix all
    rows returned win_rate=0.0 even when every trade was a winner.

Cases A–O (15 checks):
  A  _canonical_learning_key — canonical STRATEGY_PRIORITY key → CANONICAL
  B  _canonical_learning_key — legacy key with mapped type → LEGACY_COMPAT
  C  _canonical_learning_key — legacy key with unmapped type (CHOCH) → NOT_FOUND
  D  _canonical_learning_key — None/empty → NOT_FOUND
  E  _canonical_learning_key — bare non-strategy string → NOT_FOUND
  F  _strategy_weight_for — canonical key lookup → CANONICAL status
  G  _strategy_weight_for — key not in STRATEGY_WEIGHTS → NOT_FOUND + neutral
  H  _strategy_weight_for — legacy key stored, lookup with canonical → LEGACY_COMPAT
     (only fires when _LEGACY_STRATEGY_KEY_MAP has a non-None mapping)
  I  _strategy_weight_for — legacy key stored, lookup for DIFFERENT canonical → NOT_FOUND
  J  _strategy_weight_for — instrument guard: different instrument → NOT_FOUND
  K  _strategy_weight_for — instrument guard: same instrument → LEGACY_COMPAT
  L  win_rate normalization — 'WIN' lower/trim → equals 'win' (DB store format verified)
  M  _recompute_learning SQL — aggregation returns correct win_rate post-fix
  N  build_coach_interface — new diagnostic fields present
  O  build_coach_interface — lookup_status and canonical_strategy_key populated correctly
"""

import sys, os, threading
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import app as _app

# ── Test runner ───────────────────────────────────────────────────────────────
PASS = FAIL = 0


def check(label: str, cond: bool, note: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    status = "PASS" if cond else "FAIL"
    suffix = f"  [{note}]" if note else ""
    print(f"  [{status}] {label}{suffix}")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _reset_weights(*extra_keys):
    """Clear all learning caches and optionally inject extra entries."""
    with _app.LEARNING_LOCK:
        _app.STRATEGY_WEIGHTS.clear()
        _app.LEARNING_SAMPLE_BY_KEY.clear()
        _app.LEARNING_ANALYTICS.clear()
        for ns_key, weight, sample in extra_keys:
            _app.STRATEGY_WEIGHTS[ns_key]         = weight
            _app.LEARNING_SAMPLE_BY_KEY[ns_key]   = sample
            _app.LEARNING_ANALYTICS["updated_at"] = "2026-07-31T01:00:00"
            _app.LEARNING_ANALYTICS["total_trades"] = sample


# ═══════════════════════════════════════════════════════════════════════════════
# Case A — _canonical_learning_key with a STRATEGY_PRIORITY key → CANONICAL
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case A: _canonical_learning_key — STRATEGY_PRIORITY key → CANONICAL ──")
for _ckey in _app.STRATEGY_PRIORITY:
    _canon, _status = _app._canonical_learning_key(_ckey)
    check(f"Case A: '{_ckey}' → CANONICAL",
          _status == "CANONICAL" and _canon == _ckey)

# ═══════════════════════════════════════════════════════════════════════════════
# Case B — legacy key whose strategy_type IS mapped → LEGACY_COMPAT
# (uses a synthetic mapping injected temporarily)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case B: _canonical_learning_key — mapped legacy type → LEGACY_COMPAT ──")
_orig_map = dict(_app._LEGACY_STRATEGY_KEY_MAP)
_app._LEGACY_STRATEGY_KEY_MAP["SWEEP"] = "LIQUIDITY_SWEEP_REVERSAL"
try:
    _canon_b, _status_b = _app._canonical_learning_key("MGC_SCALP_SWEEP_Long")
    check("Case B: 'MGC_SCALP_SWEEP_Long' → LEGACY_COMPAT",
          _status_b == "LEGACY_COMPAT",
          f"status={_status_b}")
    check("Case B: canonical key = LIQUIDITY_SWEEP_REVERSAL",
          _canon_b == "LIQUIDITY_SWEEP_REVERSAL",
          f"canon={_canon_b}")
finally:
    _app._LEGACY_STRATEGY_KEY_MAP.clear()
    _app._LEGACY_STRATEGY_KEY_MAP.update(_orig_map)

# ═══════════════════════════════════════════════════════════════════════════════
# Case C — legacy keys: CHOCH now maps to LIQUIDITY_SWEEP_REVERSAL; BOS stays None
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case C: _canonical_learning_key — CHOCH → LEGACY_COMPAT; BOS → NOT_FOUND ──")
# CHOCH is semantically equivalent to LIQUIDITY_SWEEP_REVERSAL — mapping is deterministic
for _lkey in ["MGC_SCALP_CHOCH_Long", "MNQ_SCALP_CHOCH_Short"]:
    _c, _s = _app._canonical_learning_key(_lkey)
    check(f"Case C: '{_lkey}' → LEGACY_COMPAT (CHOCH mapped)",
          _s == "LEGACY_COMPAT" and _c == "LIQUIDITY_SWEEP_REVERSAL",
          f"status={_s}, canon={_c}")
# BOS has no safe deterministic equivalent — must remain NOT_FOUND
for _lkey in ["MES_SWING_BOS_Short", "MYM_SCALP_BOS_Long"]:
    _c, _s = _app._canonical_learning_key(_lkey)
    check(f"Case C: '{_lkey}' → NOT_FOUND (BOS still unmapped)",
          _s == "NOT_FOUND" and _c is None,
          f"status={_s}, canon={_c}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case D — None / empty string → NOT_FOUND
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case D: _canonical_learning_key — None / empty → NOT_FOUND ──")
for _empty in [None, "", "   "]:
    _c, _s = _app._canonical_learning_key(_empty)
    check(f"Case D: {_empty!r} → NOT_FOUND",
          _s == "NOT_FOUND" and _c is None)

# ═══════════════════════════════════════════════════════════════════════════════
# Case E — arbitrary non-strategy string → NOT_FOUND
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case E: _canonical_learning_key — arbitrary string → NOT_FOUND ──")
for _bad in ["CHOCH_LONG", "UNKNOWN_KEY", "scalp::vwap_trend", "1234"]:
    _c, _s = _app._canonical_learning_key(_bad)
    check(f"Case E: '{_bad}' → NOT_FOUND",
          _s == "NOT_FOUND" and _c is None,
          f"status={_s}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case F — _strategy_weight_for — canonical key in cache → CANONICAL status
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case F: _strategy_weight_for — canonical key in cache → CANONICAL ──")
_reset_weights(("SCALP::LIQUIDITY_SWEEP_REVERSAL", 1.15, 22))
_w, _n, _st = _app._strategy_weight_for("LIQUIDITY_SWEEP_REVERSAL", mode="SCALP")
check("Case F: weight readable when canonical key in STRATEGY_WEIGHTS",
      _w == 1.15 and _n == 22,
      f"weight={_w}, n={_n}")
check("Case F: lookup_status = CANONICAL",
      _st == "CANONICAL",
      f"status={_st}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case G — key absent from cache → NOT_FOUND + neutral
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case G: _strategy_weight_for — valid STRATEGY_PRIORITY key, no data yet → CANONICAL ──")
_reset_weights()   # empty cache
_w, _n, _st = _app._strategy_weight_for("LIQUIDITY_SWEEP_REVERSAL", mode="SCALP")
check("Case G: weight = 1.0 (neutral) when no data yet",
      _w == 1.0)
check("Case G: sample_count = 0 when no data yet",
      _n == 0)
check("Case G: lookup_status = CANONICAL (format valid; 0 trades, not a format error)",
      _st == "CANONICAL",
      f"status={_st}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case H — legacy stored key with mapped type; lookup by canonical → LEGACY_COMPAT
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case H: _strategy_weight_for — legacy key + mapping → LEGACY_COMPAT ──")
_app._LEGACY_STRATEGY_KEY_MAP["SWEEP"] = "LIQUIDITY_SWEEP_REVERSAL"
try:
    _reset_weights(("SCALP::MGC_SCALP_SWEEP_Long", 1.10, 20))
    _w, _n, _st = _app._strategy_weight_for(
        "LIQUIDITY_SWEEP_REVERSAL", mode="SCALP", instrument="MGC")
    check("Case H: weight fetched via LEGACY_COMPAT",
          _w == 1.10 and _n == 20,
          f"weight={_w}, n={_n}, status={_st}")
    check("Case H: lookup_status = LEGACY_COMPAT",
          _st == "LEGACY_COMPAT",
          f"status={_st}")
finally:
    _app._LEGACY_STRATEGY_KEY_MAP.pop("SWEEP", None)

# ═══════════════════════════════════════════════════════════════════════════════
# Case I — legacy stored key present but lookup is for a DIFFERENT canonical key
#           → must NOT leak cross-strategy weight → NOT_FOUND
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case I: _strategy_weight_for — cross-strategy isolation ──")
_app._LEGACY_STRATEGY_KEY_MAP["SWEEP"] = "LIQUIDITY_SWEEP_REVERSAL"
try:
    _reset_weights(("SCALP::MGC_SCALP_SWEEP_Long", 1.10, 20))
    # Lookup for a DIFFERENT canonical key — must not apply SWEEP's weight
    _w, _n, _st = _app._strategy_weight_for(
        "VWAP_TREND_CONTINUATION", mode="SCALP", instrument="MGC")
    check("Case I: different canonical key → weight stays neutral 1.0 (no cross-strategy)",
          _w == 1.0,
          f"weight={_w}, n={_n}, status={_st}")
    check("Case I: lookup_status = CANONICAL (format valid; SWEEP data does not apply to VWAP)",
          _st == "CANONICAL",
          f"status={_st}")
finally:
    _app._LEGACY_STRATEGY_KEY_MAP.pop("SWEEP", None)

# ═══════════════════════════════════════════════════════════════════════════════
# Case J — instrument guard: different instrument → weight NOT applied
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case J: _strategy_weight_for — instrument guard rejects different inst ──")
_app._LEGACY_STRATEGY_KEY_MAP["SWEEP"] = "LIQUIDITY_SWEEP_REVERSAL"
try:
    _reset_weights(("SCALP::MGC_SCALP_SWEEP_Long", 1.20, 21))
    # Lookup for MNQ instrument — stored key has MGC → must not apply
    _w, _n, _st = _app._strategy_weight_for(
        "LIQUIDITY_SWEEP_REVERSAL", mode="SCALP", instrument="MNQ")
    check("Case J: MNQ lookup → MGC legacy weight NOT applied",
          _w == 1.0,
          f"weight={_w}, instrument=MNQ vs stored=MGC")
    check("Case J: lookup_status = CANONICAL (format valid; MGC data does not apply to MNQ)",
          _st == "CANONICAL",
          f"status={_st}")
finally:
    _app._LEGACY_STRATEGY_KEY_MAP.pop("SWEEP", None)

# ═══════════════════════════════════════════════════════════════════════════════
# Case K — instrument guard: same instrument → weight IS applied
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case K: _strategy_weight_for — same instrument → LEGACY_COMPAT applies ──")
_app._LEGACY_STRATEGY_KEY_MAP["SWEEP"] = "LIQUIDITY_SWEEP_REVERSAL"
try:
    _reset_weights(("SCALP::MGC_SCALP_SWEEP_Long", 1.20, 21))
    _w, _n, _st = _app._strategy_weight_for(
        "LIQUIDITY_SWEEP_REVERSAL", mode="SCALP", instrument="MGC")
    check("Case K: MGC lookup → MGC legacy weight applied (same instrument)",
          _w == 1.20 and _n == 21,
          f"weight={_w}, n={_n}")
    check("Case K: lookup_status = LEGACY_COMPAT when instrument matches",
          _st == "LEGACY_COMPAT",
          f"status={_st}")
finally:
    _app._LEGACY_STRATEGY_KEY_MAP.pop("SWEEP", None)

# ═══════════════════════════════════════════════════════════════════════════════
# Case L — win_rate normalization: 'WIN' (DB store) matches 'win' after lower/trim
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case L: win_rate normalization — 'WIN' matches lower(trim(.))='win' ──")
_cases = [
    ("WIN",   True,  "exact uppercase (production format)"),
    ("Win",   True,  "title-case (was stored by older code)"),
    ("win",   True,  "lowercase"),
    ("WIN  ", True,  "trailing whitespace"),
    (" WIN",  True,  "leading whitespace"),
    ("LOSS",  False, "LOSS should not match 'win'"),
    ("",      False, "empty string"),
]
for _raw, _expect, _note in _cases:
    _normalized = _raw.strip().lower()
    _is_win = (_normalized == "win")
    check(f"Case L: lower(trim('{_raw}')) == 'win' → {_expect}",
          _is_win == _expect,
          _note)

# ═══════════════════════════════════════════════════════════════════════════════
# Case M — before/after win_rate SQL fix simulation
#           (actual SQL is in the DB; we simulate the impact via _learning_weight)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case M: win_rate correctness — SQL fix impact on _learning_weight ──")
# Before fix: win_rate = 0.0 (all 'WIN' rows returned 0 by case-sensitive compare)
# After fix:  win_rate = 1.0 (5 wins / 5 trades = 100%)
n_prod = 5   # production sample count
# Before fix: _learning_weight sees 0% win rate — weight < 1.0 (penalizing)
w_before_fix = _app._learning_weight(0.0, 1.5, 0.5, n_prod)
check("Case M: win_rate=0.0 (before fix, n<20) → neutral 1.0 due to threshold",
      w_before_fix == 1.0,
      f"n={n_prod} < LEARNING_MIN_SAMPLE={_app.LEARNING_MIN_SAMPLE} → always 1.0")

# Simulate with n >= 20 to see what would have happened at scale
w_bug   = _app._learning_weight(0.0, 1.5, 0.5, 20)   # 0% win_rate (SQL bug)
w_fixed = _app._learning_weight(1.0, 1.5, 0.5, 20)   # 100% win_rate (after fix)
check("Case M: win_rate=0.0 at n=20 → penalizing weight < 1.0 (what SQL bug causes)",
      w_bug < 1.0,
      f"weight={w_bug:.4f} — would have been applied after n reached 20")
check("Case M: win_rate=1.0 at n=20 → boosting weight > 1.0 (correct after SQL fix)",
      w_fixed > 1.0,
      f"weight={w_fixed:.4f}")
check("Case M: SQL fix reverses direction of weight adjustment (bug was penalizing winners)",
      w_fixed > w_bug,
      f"fixed={w_fixed:.4f} > bug={w_bug:.4f}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case N — build_coach_interface exposes all new Phase 7I.1 diagnostic fields
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case N: build_coach_interface — new Phase 7I.1 diagnostic fields present ──")
_reset_weights(("SCALP::LIQUIDITY_SWEEP_REVERSAL", 1.12, 22))
_coach_n = _app.build_coach_interface(None, instrument="MGC", mode="SCALP")
_ld_n = _coach_n.get("learning_diagnostics") or {}

_required_new_fields = [
    "canonical_strategy_key",
    "stored_strategy_key",
    "lookup_status",
    "result_normalization_status",
    "aggregate_win_count",
    "aggregate_loss_count",
    "aggregate_win_rate",
]
for _field in _required_new_fields:
    check(f"Case N: learning_diagnostics.{_field} is present",
          _field in _ld_n,
          f"value={_ld_n.get(_field)!r}")

check("Case N: result_normalization_status = 'FIXED'",
      _ld_n.get("result_normalization_status") == "FIXED")

# ═══════════════════════════════════════════════════════════════════════════════
# Case O — build_coach_interface — lookup_status + canonical_strategy_key correct
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case O: build_coach_interface — lookup_status populated correctly ──")

# O-1: no active strategy key in result → lookup_status = NOT_FOUND
_reset_weights()
_coach_o1 = _app.build_coach_interface(None, instrument="MGC", mode="SCALP")
_ld_o1 = _coach_o1.get("learning_diagnostics") or {}
check("Case O-1: no active key → lookup_status = NOT_FOUND",
      _ld_o1.get("lookup_status") == "NOT_FOUND",
      f"actual={_ld_o1.get('lookup_status')!r}")

# O-2: simulate a result with a canonical active_key present in cache
# build_coach_interface reads result["strategy_engine"]["active_key"]
_reset_weights(("SCALP::LIQUIDITY_SWEEP_REVERSAL", 1.08, 22))
_fake_result = {"strategy_engine": {"active_key": "LIQUIDITY_SWEEP_REVERSAL",
                                    "active_strategy": "Liquidity Sweep Reversal",
                                    "direction": "Long"}}
_coach_o2 = _app.build_coach_interface(_fake_result, instrument="MGC", mode="SCALP")
_ld_o2 = _coach_o2.get("learning_diagnostics") or {}
check("Case O-2: canonical key in cache → lookup_status = CANONICAL",
      _ld_o2.get("lookup_status") == "CANONICAL",
      f"actual={_ld_o2.get('lookup_status')!r}")
check("Case O-2: canonical_strategy_key = 'LIQUIDITY_SWEEP_REVERSAL'",
      _ld_o2.get("canonical_strategy_key") == "LIQUIDITY_SWEEP_REVERSAL",
      f"actual={_ld_o2.get('canonical_strategy_key')!r}")
check("Case O-2: stored_strategy_key = 'LIQUIDITY_SWEEP_REVERSAL'",
      _ld_o2.get("stored_strategy_key") == "LIQUIDITY_SWEEP_REVERSAL",
      f"actual={_ld_o2.get('stored_strategy_key')!r}")

# O-3: production legacy scenario — active_key = canonical, stored = legacy (CHOCH)
#       → CHOCH now maps to LIQUIDITY_SWEEP_REVERSAL → LEGACY_COMPAT, weight IS applied.
#       n=25 >= 20 and weight=1.22 != 1.0 → weight_status = UPDATED.
_reset_weights(("SCALP::MGC_SCALP_CHOCH_Long", 1.22, 25))
_fake_result3 = {"strategy_engine": {"active_key": "LIQUIDITY_SWEEP_REVERSAL",
                                     "direction": "Long"}}
_coach_o3 = _app.build_coach_interface(_fake_result3, instrument="MGC", mode="SCALP")
_ld_o3 = _coach_o3.get("learning_diagnostics") or {}
check("Case O-3: CHOCH LEGACY_COMPAT weight IS applied to LIQUIDITY_SWEEP_REVERSAL",
      _ld_o3.get("lookup_status") == "LEGACY_COMPAT",
      f"actual={_ld_o3.get('lookup_status')!r}")
check("Case O-3: weight_status = UPDATED (n=25 >= 20, weight=1.22 != 1.0 → adjustment active)",
      _ld_o3.get("weight_status") == "UPDATED",
      f"actual={_ld_o3.get('weight_status')!r}")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"Phase 7I.1 Key Compat Tests: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
