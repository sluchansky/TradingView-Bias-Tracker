"""
test_learning_strategy_key.py — Phase 7I.2
Learning Engine strategy-key mismatch fix: Cases A–M

Tests:
  A. _build_canonical_learning_key produces correct pipe-format keys
  B. _canonical_learning_key recognises 4-part pipe keys → CANONICAL
  C. _canonical_learning_key: CHOCH legacy key → LEGACY_COMPAT (now mapped)
  D. _canonical_learning_key: BOS legacy key → NOT_FOUND (still unmapped)
  E. _canonical_learning_key: already-canonical key → CANONICAL
  F. _strategy_weight_for: 4-part key found in cache → CANONICAL
  G. _strategy_weight_for: valid STRATEGY_PRIORITY key, no data → CANONICAL (n=0)
  H. _strategy_weight_for: CHOCH legacy weight → LEGACY_COMPAT on lookup
  I. _strategy_weight_for: instrument guard prevents cross-instrument bleed
  J. _ns_learning_key: 4-part keys pass through unchanged
  K. _ns_learning_key: bare keys get mode prefix
  L. Coach diagnostic: CHOCH legacy data → INSUFFICIENT_SAMPLES (not KEY_NOT_FOUND)
  M. Canonical key build round-trip via _strategy_weight_for Step-1 lookup
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# ── bootstrap ────────────────────────────────────────────────────────────────
os.environ.setdefault("DASHBOARD_PASSWORD", "test")
os.environ.setdefault("SESSION_SECRET",     "test-secret")
os.environ.setdefault("DATABASE_URL",       "postgresql://localhost/testdb")

import importlib
_app = importlib.import_module("app")

PASS = 0
FAIL = 0

def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        print(f"  ✓ {label}")
        PASS += 1
    else:
        print(f"  ✗ {label}" + (f" [{extra}]" if extra else ""))
        FAIL += 1

# ── helpers ───────────────────────────────────────────────────────────────────
def _reset_weights(*entries):
    """Replace STRATEGY_WEIGHTS + LEARNING_SAMPLE_BY_KEY with `entries`.
    Each entry is (key, weight, n).  Call with no args to clear."""
    with _app.LEARNING_LOCK:
        _app.STRATEGY_WEIGHTS.clear()
        _app.LEARNING_SAMPLE_BY_KEY.clear()
        for key, w, n in entries:
            _app.STRATEGY_WEIGHTS[key] = float(w)
            _app.LEARNING_SAMPLE_BY_KEY[key] = int(n)


# ═══════════════════════════════════════════════════════════════════════════════
# Case A — _build_canonical_learning_key output format
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case A: _build_canonical_learning_key — pipe-format output ──")

_cases_a = [
    ("MGC",  "SCALP",      "LIQUIDITY_SWEEP_REVERSAL", "Long",  "MGC|SCALP|LIQUIDITY_SWEEP_REVERSAL|LONG"),
    ("MNQ",  "SCALP",      "VWAP_TREND_CONTINUATION",  "Short", "MNQ|SCALP|VWAP_TREND_CONTINUATION|SHORT"),
    ("MGC",  "swing",      "VWAP_TREND_CONTINUATION",  "Long",  "MGC|SWING|VWAP_TREND_CONTINUATION|LONG"),
    ("mnq",  "MICRO_SCALP","ORB_BREAKOUT",             "Long",  "MNQ|MICRO_SCALP|ORB_BREAKOUT|LONG"),
    ("MGC",  "SCALP",      None,                        "Long",  "MGC|SCALP|UNKNOWN|LONG"),
    (None,   "SCALP",      "LIQUIDITY_SWEEP_REVERSAL", None,   "UNKNOWN|SCALP|LIQUIDITY_SWEEP_REVERSAL|UNKNOWN"),
]
for inst, mode, strat, direction, expected in _cases_a:
    result = _app._build_canonical_learning_key(inst, mode, strat, direction)
    check(f"Case A: ({inst},{mode},{strat},{direction}) → '{expected}'",
          result == expected,
          f"got={result!r}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case B — _canonical_learning_key: 4-part pipe key → CANONICAL
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case B: _canonical_learning_key — 4-part pipe format → CANONICAL ──")

for strat in list(_app.STRATEGY_PRIORITY):
    k4 = _app._build_canonical_learning_key("MGC", "SCALP", strat, "Long")
    _c, _s = _app._canonical_learning_key(k4)
    check(f"Case B: '{k4}' → CANONICAL",
          _s == "CANONICAL" and _c == k4,
          f"status={_s}, canon={_c}")

# Pipe key with non-existent strategy dimension → NOT_FOUND
_bad_pipe = "MGC|SCALP|NONEXISTENT_STRATEGY|LONG"
_c, _s = _app._canonical_learning_key(_bad_pipe)
check("Case B: unknown strategy in pipe format → NOT_FOUND",
      _s == "NOT_FOUND" and _c is None,
      f"status={_s}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case C — _canonical_learning_key: CHOCH legacy key → LEGACY_COMPAT
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case C: _canonical_learning_key — CHOCH legacy key → LEGACY_COMPAT ──")

for _lkey in ["MGC_SCALP_CHOCH_Long", "MNQ_SCALP_CHOCH_Short",
              "MES_SWING_CHOCH_Long", "MYM_SCALP_CHOCH_Short"]:
    _c, _s = _app._canonical_learning_key(_lkey)
    check(f"Case C: '{_lkey}' → LEGACY_COMPAT",
          _s == "LEGACY_COMPAT" and _c == "LIQUIDITY_SWEEP_REVERSAL",
          f"status={_s}, canon={_c}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case D — _canonical_learning_key: BOS still NOT_FOUND (no safe equivalent)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case D: _canonical_learning_key — BOS (unmapped) → NOT_FOUND ──")

for _lkey in ["MGC_SCALP_BOS_Long", "MNQ_SCALP_BOS_Short",
              "MES_SWING_BOS_Short", "MYM_SCALP_BOS_Long"]:
    _c, _s = _app._canonical_learning_key(_lkey)
    check(f"Case D: '{_lkey}' → NOT_FOUND (BOS unmapped)",
          _s == "NOT_FOUND" and _c is None,
          f"status={_s}, canon={_c}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case E — _canonical_learning_key: bare STRATEGY_PRIORITY key → CANONICAL
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case E: _canonical_learning_key — bare STRATEGY_PRIORITY keys → CANONICAL ──")

for _sk in list(_app.STRATEGY_PRIORITY):
    _c, _s = _app._canonical_learning_key(_sk)
    check(f"Case E: '{_sk}' → CANONICAL",
          _s == "CANONICAL" and _c == _sk,
          f"status={_s}, canon={_c}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case F — _strategy_weight_for: 4-part key found in cache via Step-1
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case F: _strategy_weight_for — 4-part key in cache → CANONICAL (Step 1) ──")

k4_mgc = _app._build_canonical_learning_key("MGC", "SCALP", "LIQUIDITY_SWEEP_REVERSAL", "Long")
k4_mnq = _app._build_canonical_learning_key("MNQ", "SCALP", "LIQUIDITY_SWEEP_REVERSAL", "Long")
_reset_weights((k4_mgc, 1.18, 30), (k4_mnq, 0.90, 25))

_wm, _nm, _sm = _app._strategy_weight_for(
    "LIQUIDITY_SWEEP_REVERSAL", mode="SCALP", instrument="MGC", direction="Long")
check("Case F: MGC weight from 4-part key = 1.18",
      _wm == 1.18, f"weight={_wm}")
check("Case F: MGC sample_count = 30",
      _nm == 30, f"n={_nm}")
check("Case F: MGC lookup_status = CANONICAL",
      _sm == "CANONICAL", f"status={_sm}")

_wn, _nn, _sn = _app._strategy_weight_for(
    "LIQUIDITY_SWEEP_REVERSAL", mode="SCALP", instrument="MNQ", direction="Long")
check("Case F: MNQ weight from 4-part key = 0.90 (different instrument, different weight)",
      _wn == 0.90, f"weight={_wn}")
check("Case F: MNQ lookup_status = CANONICAL",
      _sn == "CANONICAL", f"status={_sn}")

# Sanity: MGC weight must NOT bleed to MNQ
check("Case F: MGC weight does not bleed to MNQ",
      _wn != _wm, f"MGC={_wm}, MNQ={_wn}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case G — _strategy_weight_for: valid STRATEGY_PRIORITY key, no data → CANONICAL n=0
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case G: _strategy_weight_for — valid key, zero data → CANONICAL, n=0 ──")

_reset_weights()   # empty cache
for _sk in list(_app.STRATEGY_PRIORITY):
    _w, _n, _st = _app._strategy_weight_for(_sk, mode="SCALP")
    check(f"Case G: '{_sk}' empty cache → CANONICAL (not NOT_FOUND)",
          _st == "CANONICAL", f"status={_st}")
    check(f"Case G: '{_sk}' n=0",
          _n == 0, f"n={_n}")
    check(f"Case G: '{_sk}' weight=1.0 (neutral)",
          _w == 1.0, f"weight={_w}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case H — _strategy_weight_for: CHOCH legacy data → LEGACY_COMPAT
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case H: _strategy_weight_for — CHOCH legacy weight → LEGACY_COMPAT ──")

# Production scenario: 6 trades closed with old strategy_key format
_reset_weights(("SCALP::MGC_SCALP_CHOCH_Long", 1.08, 6),
               ("SCALP::MNQ_SCALP_CHOCH_Long", 1.05, 4))

_wm, _nm, _sm = _app._strategy_weight_for(
    "LIQUIDITY_SWEEP_REVERSAL", mode="SCALP", instrument="MGC")
check("Case H: MGC CHOCH weight fetched via LEGACY_COMPAT",
      _sm == "LEGACY_COMPAT", f"status={_sm}")
check("Case H: MGC weight = 1.08",
      abs(_wm - 1.08) < 1e-9, f"weight={_wm}")
check("Case H: MGC sample_count = 6",
      _nm == 6, f"n={_nm}")

_wn, _nn, _sn = _app._strategy_weight_for(
    "LIQUIDITY_SWEEP_REVERSAL", mode="SCALP", instrument="MNQ")
check("Case H: MNQ CHOCH weight fetched via LEGACY_COMPAT",
      _sn == "LEGACY_COMPAT", f"status={_sn}")
check("Case H: MNQ weight = 1.05",
      abs(_wn - 1.05) < 1e-9, f"weight={_wn}")

# CRITICAL: weights must not be swapped across instruments
check("Case H: MGC weight ≠ MNQ weight (no cross-instrument bleed)",
      abs(_wm - _wn) > 0.001, f"MGC={_wm}, MNQ={_wn}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case I — _strategy_weight_for: instrument guard blocks cross-instrument leak
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case I: _strategy_weight_for — instrument guard (no cross-inst leak) ──")

# Only MGC data in cache; lookup for MNQ must stay neutral
_reset_weights(("SCALP::MGC_SCALP_CHOCH_Long", 1.30, 20))

_w, _n, _st = _app._strategy_weight_for(
    "LIQUIDITY_SWEEP_REVERSAL", mode="SCALP", instrument="MNQ")
check("Case I: MNQ lookup with only MGC cache → weight stays 1.0",
      _w == 1.0, f"weight={_w}")
check("Case I: MNQ lookup_status = CANONICAL (valid key, just no MNQ data)",
      _st == "CANONICAL", f"status={_st}")
check("Case I: sample_count = 0 (MGC sample does not count for MNQ)",
      _n == 0, f"n={_n}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case J — _ns_learning_key: 4-part pipe keys pass through unchanged
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case J: _ns_learning_key — 4-part keys unchanged; bare keys get prefix ──")

k4 = "MGC|SCALP|LIQUIDITY_SWEEP_REVERSAL|LONG"
check("Case J: 4-part key → unchanged",
      _app._ns_learning_key(k4, "SCALP") == k4,
      f"got={_app._ns_learning_key(k4, 'SCALP')!r}")

check("Case J: bare key + mode → prefixed",
      _app._ns_learning_key("LIQUIDITY_SWEEP_REVERSAL", "SCALP") == "SCALP::LIQUIDITY_SWEEP_REVERSAL",
      f"got={_app._ns_learning_key('LIQUIDITY_SWEEP_REVERSAL', 'SCALP')!r}")

check("Case J: bare key, no mode → unchanged",
      _app._ns_learning_key("LIQUIDITY_SWEEP_REVERSAL", None) == "LIQUIDITY_SWEEP_REVERSAL",
      f"got={_app._ns_learning_key('LIQUIDITY_SWEEP_REVERSAL', None)!r}")

check("Case J: legacy key + mode → prefixed (no double-wrap)",
      _app._ns_learning_key("MGC_SCALP_CHOCH_Long", "SCALP") == "SCALP::MGC_SCALP_CHOCH_Long")

# ═══════════════════════════════════════════════════════════════════════════════
# Case K — _ns_learning_key: bare keys get mode prefix (modes are valid)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case K: _ns_learning_key — mode prefix added for recognised modes ──")

for _mode in list(_app._VALID_LEARNING_MODES):
    _key = "SOME_STRATEGY"
    _expected = "%s::%s" % (_mode, _key)
    _got = _app._ns_learning_key(_key, _mode)
    check(f"Case K: mode={_mode!r} → prefixed",
          _got == _expected, f"got={_got!r}")

check("Case K: unknown mode → no prefix",
      _app._ns_learning_key("SOME_STRATEGY", "MYSTERY_MODE") == "SOME_STRATEGY")

# ═══════════════════════════════════════════════════════════════════════════════
# Case L — build_coach_interface: CHOCH legacy data → INSUFFICIENT_SAMPLES,
#           not KEY_NOT_FOUND (the key fix resolves the production symptom)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case L: build_coach_interface — CHOCH data → INSUFFICIENT_SAMPLES ──")

# Production: 5-trade sample from CHOCH era; active_key = LIQUIDITY_SWEEP_REVERSAL
_reset_weights(("SCALP::MGC_SCALP_CHOCH_Long", 1.0, 5))  # n=5, weight=1.0

# Simulate a recompute having run (sets _recompute_ran=True inside build_coach_interface).
# Without this, the coach returns NOT_ELIGIBLE (a test-env artifact, not a production state).
_orig_la = dict(_app.LEARNING_ANALYTICS)
_app.LEARNING_ANALYTICS["updated_at"] = "2026-07-31T00:00:00"
try:
    _fake_result_l = {"strategy_engine": {"active_key": "LIQUIDITY_SWEEP_REVERSAL",
                                          "active_strategy": "Liquidity Sweep Reversal",
                                          "direction": "Long"}}
    _coach_l = _app.build_coach_interface(_fake_result_l, instrument="MGC", mode="SCALP")
    _ld_l = _coach_l.get("learning_diagnostics") or {}

    check("Case L: lookup_status = LEGACY_COMPAT (CHOCH resolves to LIQUIDITY_SWEEP_REVERSAL)",
          _ld_l.get("lookup_status") == "LEGACY_COMPAT",
          f"actual={_ld_l.get('lookup_status')!r}")

    # n=5 < LEARNING_MIN_SAMPLE → weight_status must be INSUFFICIENT_SAMPLES, never KEY_NOT_FOUND
    check("Case L: weight_status = INSUFFICIENT_SAMPLES (n=5 < threshold, key IS resolvable)",
          _ld_l.get("weight_status") == "INSUFFICIENT_SAMPLES",
          f"actual={_ld_l.get('weight_status')!r}")

    check("Case L: weight_status is NOT KEY_NOT_FOUND (the fix works)",
          _ld_l.get("weight_status") != "KEY_NOT_FOUND",
          f"weight_status={_ld_l.get('weight_status')!r}")
finally:
    _app.LEARNING_ANALYTICS.clear()
    _app.LEARNING_ANALYTICS.update(_orig_la)

# ═══════════════════════════════════════════════════════════════════════════════
# Case M — 4-part canonical key round-trip: build → store → lookup
#          Simulates what _update_learning_snapshot writes and _recompute_learning
#          reads, then verifies _strategy_weight_for finds it via Step-1.
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case M: 4-part key round-trip — build → store → lookup ──")

_m_inst  = "MGC"
_m_mode  = "SCALP"
_m_strat = "LIQUIDITY_SWEEP_REVERSAL"
_m_dir   = "Long"

# Build the key (simulates _update_learning_snapshot)
_m_k4 = _app._build_canonical_learning_key(_m_inst, _m_mode, _m_strat, _m_dir)
check("Case M: key format is pipe-delimited",
      "|" in _m_k4 and _m_k4.count("|") == 3,
      f"key={_m_k4!r}")

# Store it (simulates what _recompute_learning writes to STRATEGY_WEIGHTS)
_reset_weights((_m_k4, 1.20, 22))

# Lookup via _strategy_weight_for (all 4 dimensions) — Step 1 must hit
_mw, _mn, _ms = _app._strategy_weight_for(
    _m_strat, mode=_m_mode, instrument=_m_inst, direction=_m_dir)
check("Case M: Step-1 lookup hits the 4-part key",
      _ms == "CANONICAL", f"status={_ms}")
check("Case M: correct weight returned",
      abs(_mw - 1.20) < 1e-9, f"weight={_mw}")
check("Case M: correct sample count returned",
      _mn == 22, f"n={_mn}")

# Reverse: querying a DIFFERENT direction must NOT return the Long weight
_m_k4_short = _app._build_canonical_learning_key(_m_inst, _m_mode, _m_strat, "Short")
check("Case M: Short direction finds different (absent) slot",
      _m_k4_short != _m_k4,
      f"long={_m_k4!r}, short={_m_k4_short!r}")

_sw, _sn2, _ss = _app._strategy_weight_for(
    _m_strat, mode=_m_mode, instrument=_m_inst, direction="Short")
# No Short data in cache; Step-4 fires → CANONICAL n=0
check("Case M: Short lookup → CANONICAL n=0 (no Short data; not NOT_FOUND)",
      _ss == "CANONICAL" and _sn2 == 0,
      f"status={_ss}, n={_sn2}")
check("Case M: Short weight stays neutral 1.0 (no Long→Short bleed)",
      _sw == 1.0, f"weight={_sw}")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"Phase 7I.2 Learning Key Fix Tests: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
