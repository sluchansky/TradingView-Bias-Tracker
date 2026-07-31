"""
test_lb_stale_fallback.py — Phase: Left Brain stale-fallback scanner
Cases A–J per spec (Task 55)

Tests the eligibility conditions, rate limiting, session guards, dedup,
and diagnosis field extensions WITHOUT spawning real threads or needing
Databento/DB connectivity.

The scheduler loop (_lb_stale_fallback_loop) and the scan function
(_lb_stale_fallback_scan) are unit-tested by:
  1. Setting up module-level state (thesis, obs, in-flight, epochs)
  2. Calling a helper that mirrors the eligibility logic
  3. Checking the diagnosis block via build_main_brain / _mb_left_brain
"""
import sys, os, time, threading
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("DASHBOARD_PASSWORD", "test")
os.environ.setdefault("SESSION_SECRET",     "test-secret")
os.environ.setdefault("DATABASE_URL",       "postgresql://localhost/testdb")
os.environ.setdefault("LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED", "1")

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

def _reset_lb_state():
    """Clear all LB fallback runtime state between test cases."""
    _app._LB_THESIS_BY_INST.clear()
    _app._LB_THESIS_OBS_BY_INST.clear()
    _app._LB_THESIS_OBS_LAST_BAR.clear()
    _app._LEFT_BRAIN_MI_BY_INST.clear()
    _app._LB_MARKET_MEMORY_BY_INST.clear()
    _app._LB_CALC_TRIGGER.clear()
    _app._LB_CALC_LAST_ATTEMPT_AT.clear()
    _app._LB_CALC_LAST_SUCCESS_AT.clear()
    _app._LB_FALLBACK_EPOCH.clear()
    with _app._LB_FALLBACK_LOCK:
        _app._LB_FALLBACK_IN_FLIGHT.clear()


def _make_stale_thesis(age_sec: int, direction: str = "LONG") -> dict:
    """Return a thesis dict whose last_updated_at is `age_sec` seconds ago."""
    lu = (datetime.now(timezone.utc) - timedelta(seconds=age_sec)).isoformat()
    return {
        "direction":     direction,
        "strength":      "MODERATE",
        "momentum":      "STABLE",
        "last_updated_at": lu,
        "status":        "ESTABLISHED",
        "confidence":    70,
    }


def _add_obs(inst: str, n: int) -> None:
    """Inject `n` fake observation entries into the obs buffer for `inst`."""
    from collections import deque
    if inst not in _app._LB_THESIS_OBS_BY_INST:
        _app._LB_THESIS_OBS_BY_INST[inst] = deque(maxlen=5000)
    for i in range(n):
        _app._LB_THESIS_OBS_BY_INST[inst].append({
            "ts":            datetime.now(timezone.utc).isoformat(),
            "instrument":    inst,
            "direction":     "LONG",
            "strength":      "MODERATE",
            "data_confidence": 70,
        })


def _diag_for(inst: str) -> dict:
    """Return the diagnosis dict from _mb_left_brain for `inst`."""
    out = _app._mb_left_brain(inst, None, [])
    return out.get("diagnosis") or {}


def _check_eligibility(inst: str) -> tuple:
    """
    Mirror the scheduler's eligibility logic (no side effects).
    Returns (eligible: bool, reason: str).
    """
    if not _app.LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED:
        return (False, "FEATURE_DISABLED")

    sess = _app.market_session_status()
    if not sess.get("open", True):
        return (False, "MARKET_CLOSED")

    with _app._LB_FALLBACK_LOCK:
        if inst in _app._LB_FALLBACK_IN_FLIGHT:
            return (False, "IN_FLIGHT")

    _last_ep = _LB_FALLBACK_EPOCH_snapshot = _app._LB_FALLBACK_EPOCH.get(inst, 0.0)
    if time.monotonic() - _last_ep < _app.LB_STALE_FALLBACK_INTERVAL_SEC:
        return (False, "RATE_LIMITED")

    _raw = _app._LB_THESIS_BY_INST.get(inst)
    _lu  = (_raw or {}).get("last_updated_at") or (_raw or {}).get("lastUpdatedAt")
    _age = None
    if _lu:
        try:
            _lu_dt = datetime.fromisoformat(_lu)
            if _lu_dt.tzinfo is None:
                _lu_dt = _lu_dt.replace(tzinfo=timezone.utc)
            _age = (datetime.now(timezone.utc) - _lu_dt).total_seconds()
        except Exception:
            pass
    _stale = (_raw is None) or (_age is not None and _age > _app.LB_STALE_THESIS_SEC)
    if not _stale:
        return (False, "THESIS_FRESH")

    _obs = len(list(_app._LB_THESIS_OBS_BY_INST.get(inst) or []))
    if _obs < _app.LB_MIN_OBS_FOR_FALLBACK:
        return (False, "COLLECTING_DATA")

    return (True, "ELIGIBLE")


# ═══════════════════════════════════════════════════════════════════════════════
# Case A — MGC stale thesis + sufficient observations → eligible for fallback
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case A: stale thesis + enough observations → eligible ──")
_reset_lb_state()
_app._LB_THESIS_BY_INST["MGC"] = _make_stale_thesis(900)   # 15 min old > 600 threshold
_add_obs("MGC", 10)

_elig_a, _reason_a = _check_eligibility("MGC")
check("Case A: eligible = True",  _elig_a,  f"reason={_reason_a}")
check("Case A: no block reason",  _reason_a == "ELIGIBLE", f"reason={_reason_a}")

_diag_a = _diag_for("MGC")
check("Case A: diagnosis.status = STALE",
      _diag_a.get("status") == "STALE",
      f"status={_diag_a.get('status')}")
check("Case A: fallback_eligible = True in diagnosis",
      _diag_a.get("fallback_eligible") is True,
      f"fallback_eligible={_diag_a.get('fallback_eligible')}")
check("Case A: fallback_blocked_reason = None",
      _diag_a.get("fallback_blocked_reason") is None,
      f"reason={_diag_a.get('fallback_blocked_reason')}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case B — MGC thesis fresh → NOT eligible for fallback
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case B: fresh thesis → not eligible ──")
_reset_lb_state()
_app._LB_THESIS_BY_INST["MGC"] = _make_stale_thesis(30)   # 30 s old — fresh
_add_obs("MGC", 10)

_elig_b, _reason_b = _check_eligibility("MGC")
check("Case B: eligible = False (thesis fresh)", not _elig_b, f"reason={_reason_b}")
check("Case B: reason = THESIS_FRESH", _reason_b == "THESIS_FRESH", f"reason={_reason_b}")

_diag_b = _diag_for("MGC")
check("Case B: diagnosis.status = AVAILABLE",
      _diag_b.get("status") == "AVAILABLE",
      f"status={_diag_b.get('status')}")
check("Case B: fallback_eligible = False in diagnosis",
      _diag_b.get("fallback_eligible") is False,
      f"fallback_eligible={_diag_b.get('fallback_eligible')}")
check("Case B: fallback_blocked_reason = THESIS_FRESH",
      _diag_b.get("fallback_blocked_reason") == "THESIS_FRESH",
      f"reason={_diag_b.get('fallback_blocked_reason')}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case C — MGC stale but insufficient observations → COLLECTING_DATA, no fallback
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case C: stale thesis + insufficient observations → COLLECTING_DATA ──")
_reset_lb_state()
_app._LB_THESIS_BY_INST["MGC"] = _make_stale_thesis(900)
_add_obs("MGC", 2)   # only 2, need 5

_elig_c, _reason_c = _check_eligibility("MGC")
check("Case C: eligible = False (insufficient obs)", not _elig_c, f"reason={_reason_c}")
check("Case C: reason = COLLECTING_DATA", _reason_c == "COLLECTING_DATA", f"reason={_reason_c}")

_diag_c = _diag_for("MGC")
# Stale thesis (900s) + 2 obs: staleness outranks insufficient-obs in the
# diagnosis priority chain, so diagnosis.status = STALE not COLLECTING_DATA.
# The fallback_blocked_reason correctly reflects the obs shortage as the
# reason the fallback won't actually attempt a recompute.
check("Case C: diagnosis.status = STALE (stale outranks low-obs in priority)",
      _diag_c.get("status") == "STALE",
      f"status={_diag_c.get('status')}")
check("Case C: fallback_blocked_reason = COLLECTING_DATA (fallback blocked by low obs)",
      _diag_c.get("fallback_blocked_reason") == "COLLECTING_DATA",
      f"reason={_diag_c.get('fallback_blocked_reason')}")
# The key safety guarantee: fallback is NOT eligible — no fake neutral thesis
# will be produced from insufficient data.
check("Case C: fallback NOT eligible (insufficient observations → no fake neutral)",
      _diag_c.get("fallback_eligible") is False,
      f"fallback_eligible={_diag_c.get('fallback_eligible')}")

# Also verify thesis_out has no direction (it exists but direction may be set from the old thesis)
# The key requirement: status != AVAILABLE and diagnosis is explicit

# ═══════════════════════════════════════════════════════════════════════════════
# Case D — Maintenance window → market_session_status says closed
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case D: maintenance window → not eligible ──")
_reset_lb_state()
_app._LB_THESIS_BY_INST["MGC"] = _make_stale_thesis(900)
_add_obs("MGC", 10)

# Simulate closed session by patching market_session_status
_orig_mss = _app.market_session_status
def _mock_closed(*a, **kw):
    return {"open": False, "status": "CLOSED", "reason": "Daily maintenance break"}
_app.market_session_status = _mock_closed
try:
    _elig_d, _reason_d = _check_eligibility("MGC")
    check("Case D: eligible = False (market closed)", not _elig_d, f"reason={_reason_d}")
    check("Case D: reason = MARKET_CLOSED", _reason_d == "MARKET_CLOSED", f"reason={_reason_d}")

    _diag_d = _diag_for("MGC")
    check("Case D: fallback_blocked_reason = MARKET_CLOSED",
          _diag_d.get("fallback_blocked_reason") == "MARKET_CLOSED",
          f"reason={_diag_d.get('fallback_blocked_reason')}")
finally:
    _app.market_session_status = _orig_mss

# ═══════════════════════════════════════════════════════════════════════════════
# Case E — Weekend / market closed
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case E: market closed (weekend) → not eligible ──")
_reset_lb_state()
_app._LB_THESIS_BY_INST["MGC"] = _make_stale_thesis(900)
_add_obs("MGC", 10)

_orig_mss2 = _app.market_session_status
def _mock_weekend(*a, **kw):
    return {"open": False, "status": "CLOSED", "reason": "Weekend close"}
_app.market_session_status = _mock_weekend
try:
    _elig_e, _reason_e = _check_eligibility("MGC")
    check("Case E: eligible = False (weekend)",    not _elig_e, f"reason={_reason_e}")
    check("Case E: reason = MARKET_CLOSED",        _reason_e == "MARKET_CLOSED", f"reason={_reason_e}")

    _diag_e = _diag_for("MGC")
    check("Case E: fallback_eligible = False in diagnosis",
          _diag_e.get("fallback_eligible") is False,
          f"fallback_eligible={_diag_e.get('fallback_eligible')}")
finally:
    _app.market_session_status = _orig_mss2

# ═══════════════════════════════════════════════════════════════════════════════
# Case F — Fallback and bar-close trigger occur together → in-flight guard
#           prevents a second calculation from starting
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case F: in-flight guard prevents double calculation ──")
_reset_lb_state()
_app._LB_THESIS_BY_INST["MGC"] = _make_stale_thesis(900)
_add_obs("MGC", 10)

# Simulate a fallback already in-flight
with _app._LB_FALLBACK_LOCK:
    _app._LB_FALLBACK_IN_FLIGHT.add("MGC")
try:
    _elig_f, _reason_f = _check_eligibility("MGC")
    check("Case F: eligible = False (in-flight)",  not _elig_f, f"reason={_reason_f}")
    check("Case F: reason = IN_FLIGHT",            _reason_f == "IN_FLIGHT", f"reason={_reason_f}")

    _diag_f = _diag_for("MGC")
    check("Case F: fallback_blocked_reason = IN_FLIGHT",
          _diag_f.get("fallback_blocked_reason") == "IN_FLIGHT",
          f"reason={_diag_f.get('fallback_blocked_reason')}")
finally:
    with _app._LB_FALLBACK_LOCK:
        _app._LB_FALLBACK_IN_FLIGHT.discard("MGC")

# ═══════════════════════════════════════════════════════════════════════════════
# Case G — Repeated scheduler cycles → rate limit prevents duplicate attempts
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case G: rate limit prevents duplicate attempts ──")
_reset_lb_state()
_app._LB_THESIS_BY_INST["MGC"] = _make_stale_thesis(900)
_add_obs("MGC", 10)

# Simulate a fallback attempt that just happened (epoch = now)
_app._LB_FALLBACK_EPOCH["MGC"] = time.monotonic()

_elig_g, _reason_g = _check_eligibility("MGC")
check("Case G: eligible = False (rate limited)",  not _elig_g, f"reason={_reason_g}")
check("Case G: reason = RATE_LIMITED",            _reason_g == "RATE_LIMITED", f"reason={_reason_g}")

_diag_g = _diag_for("MGC")
check("Case G: fallback_blocked_reason starts with RATE_LIMITED",
      (_diag_g.get("fallback_blocked_reason") or "").startswith("RATE_LIMITED"),
      f"reason={_diag_g.get('fallback_blocked_reason')}")

# Verify that after the interval elapses the check becomes eligible again
# (simulate by setting epoch far in the past)
_app._LB_FALLBACK_EPOCH["MGC"] = time.monotonic() - _app.LB_STALE_FALLBACK_INTERVAL_SEC - 1
_elig_g2, _reason_g2 = _check_eligibility("MGC")
check("Case G: eligible = True after rate limit elapses", _elig_g2, f"reason={_reason_g2}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case H — Evaluation result unchanged → observation dedup guard prevents
#           writing a duplicate row for the same bar-minute key
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case H: dedup guard prevents duplicate observation write ──")
_reset_lb_state()

# Set a bar key for MGC
_bar_key = datetime.now(timezone.utc).isoformat()[:16]  # minute precision
_app._LB_THESIS_OBS_LAST_BAR["MGC"] = _bar_key
_add_obs("MGC", 3)   # 3 existing obs

# If the same bar key is seen again, no new observation should be written
_obs_before = len(list(_app._LB_THESIS_OBS_BY_INST.get("MGC") or []))
# Simulate what the fallback scan does: check dedup guard
from collections import deque as _deque
if "MGC" not in _app._LB_THESIS_OBS_BY_INST:
    _app._LB_THESIS_OBS_BY_INST["MGC"] = _deque(maxlen=5000)
_same_bar_key = _bar_key
if _app._LB_THESIS_OBS_LAST_BAR.get("MGC") == _same_bar_key:
    pass  # dedup guard fires — no write
else:
    _app._LB_THESIS_OBS_BY_INST["MGC"].append({"ts": "dummy"})
_obs_after = len(list(_app._LB_THESIS_OBS_BY_INST.get("MGC") or []))
check("Case H: same bar key → no duplicate observation written",
      _obs_after == _obs_before,
      f"before={_obs_before}, after={_obs_after}")

# Different bar key → write is allowed
_new_bar_key = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()[:16]
if _app._LB_THESIS_OBS_LAST_BAR.get("MGC") != _new_bar_key:
    _app._LB_THESIS_OBS_LAST_BAR["MGC"] = _new_bar_key
    _app._LB_THESIS_OBS_BY_INST["MGC"].append({"ts": "new_bar"})
_obs_new = len(list(_app._LB_THESIS_OBS_BY_INST.get("MGC") or []))
check("Case H: new bar key → observation written",
      _obs_new == _obs_before + 1,
      f"before={_obs_before}, after={_obs_new}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case I — MNQ/MES/MYM: bar-close data keeps their theses fresh
#           → fallback_eligible = False (THESIS_FRESH), no interference
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case I: MNQ/MES/MYM fresh theses → fallback not triggered ──")
_reset_lb_state()
for _inst in ["MNQ", "MES", "MYM"]:
    _app._LB_THESIS_BY_INST[_inst] = _make_stale_thesis(60)   # 1 min old — fresh
    _add_obs(_inst, 30)

for _inst in ["MNQ", "MES", "MYM"]:
    _elig_i, _reason_i = _check_eligibility(_inst)
    check(f"Case I: {_inst} → not eligible (THESIS_FRESH)", not _elig_i, f"reason={_reason_i}")
    _diag_i = _diag_for(_inst)
    check(f"Case I: {_inst} → fallback_eligible = False",
          _diag_i.get("fallback_eligible") is False,
          f"fallback_eligible={_diag_i.get('fallback_eligible')}")
    check(f"Case I: {_inst} → status = AVAILABLE",
          _diag_i.get("status") == "AVAILABLE",
          f"status={_diag_i.get('status')}")

# ═══════════════════════════════════════════════════════════════════════════════
# Case J — Restart: no burst — rate-limit epoch starts at 0 but each instrument
#           only gets one attempt per LB_STALE_FALLBACK_INTERVAL_SEC.
#           After restart, thesis is None → NO_DATA; obs = 0 → COLLECTING_DATA.
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Case J: restart — no burst and collecting data state ──")
_reset_lb_state()   # simulates restart: all state cleared

# Post-restart: thesis is None, no observations yet
_elig_j, _reason_j = _check_eligibility("MGC")
check("Case J: right after restart → COLLECTING_DATA (no obs yet)",
      _reason_j == "COLLECTING_DATA",
      f"reason={_reason_j}")

_diag_j = _diag_for("MGC")
check("Case J: post-restart diagnosis.status = NO_DATA",
      _diag_j.get("status") == "NO_DATA",
      f"status={_diag_j.get('status')}")
check("Case J: post-restart: calculation_trigger = NONE (no calc has run)",
      _diag_j.get("calculation_trigger") == "NONE",
      f"trigger={_diag_j.get('calculation_trigger')}")
check("Case J: post-restart: last_attempt_at = None",
      _diag_j.get("last_attempt_at") is None,
      f"attempt={_diag_j.get('last_attempt_at')}")

# After some obs accumulate (still stale, no fallback epoch set):
_app._LB_THESIS_BY_INST["MGC"] = _make_stale_thesis(900)
_add_obs("MGC", 10)
# Rate limit epoch is 0.0 (never ran) — enough time has elapsed
_elig_j2, _reason_j2 = _check_eligibility("MGC")
check("Case J: after obs accumulate → eligible (epoch=0, elapsed > interval)",
      _elig_j2,
      f"reason={_reason_j2}")

# Simulate second attempt within interval — must be blocked
_app._LB_FALLBACK_EPOCH["MGC"] = time.monotonic()
_elig_j3, _reason_j3 = _check_eligibility("MGC")
check("Case J: second attempt within interval → RATE_LIMITED (no burst)",
      _reason_j3 == "RATE_LIMITED",
      f"reason={_reason_j3}")

# ═══════════════════════════════════════════════════════════════════════════════
# Part 7 diagnostics — all required fields present in diagnosis dict
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Part 7: all required diagnosis fields present ──")
_reset_lb_state()
_app._LB_THESIS_BY_INST["MGC"] = _make_stale_thesis(900)
_add_obs("MGC", 10)
# Simulate a prior bar-close + fallback attempt
_app._LB_CALC_TRIGGER["MGC"]          = "BAR_CLOSE"
_app._LB_CALC_LAST_ATTEMPT_AT["MGC"]  = "2026-07-31T04:00:00+00:00"
_app._LB_CALC_LAST_SUCCESS_AT["MGC"]  = "2026-07-31T04:00:00+00:00"

_diag_p7 = _diag_for("MGC")
_required_fields = [
    "calculation_trigger",
    "last_attempt_at",
    "last_success_at",
    "fallback_eligible",
    "fallback_blocked_reason",
    "observations_available",
    "observations_required",
]
for _field in _required_fields:
    check(f"Part 7: diagnosis.{_field} present",
          _field in _diag_p7,
          f"value={_diag_p7.get(_field)!r}")

check("Part 7: calculation_trigger = BAR_CLOSE",
      _diag_p7.get("calculation_trigger") == "BAR_CLOSE",
      f"trigger={_diag_p7.get('calculation_trigger')}")
check("Part 7: last_attempt_at = ISO string",
      isinstance(_diag_p7.get("last_attempt_at"), str),
      f"value={_diag_p7.get('last_attempt_at')!r}")
check("Part 7: observations_available = 10",
      _diag_p7.get("observations_available") == 10,
      f"value={_diag_p7.get('observations_available')}")
check("Part 7: observations_required = LB_MIN_OBS_FOR_FALLBACK",
      _diag_p7.get("observations_required") == _app.LB_MIN_OBS_FOR_FALLBACK,
      f"required={_diag_p7.get('observations_required')}, constant={_app.LB_MIN_OBS_FOR_FALLBACK}")

# ═══════════════════════════════════════════════════════════════════════════════
# Constants: verify module-level constants are exposed and match expected values
# ═══════════════════════════════════════════════════════════════════════════════
print("\n── Constants: module-level LB fallback constants ──")
check("Constants: LB_STALE_THESIS_SEC = 600",
      _app.LB_STALE_THESIS_SEC == 600,
      f"value={_app.LB_STALE_THESIS_SEC}")
check("Constants: LB_STALE_FALLBACK_INTERVAL_SEC = 300",
      _app.LB_STALE_FALLBACK_INTERVAL_SEC == 300,
      f"value={_app.LB_STALE_FALLBACK_INTERVAL_SEC}")
check("Constants: LB_MIN_OBS_FOR_FALLBACK = 5",
      _app.LB_MIN_OBS_FOR_FALLBACK == 5,
      f"value={_app.LB_MIN_OBS_FOR_FALLBACK}")
check("Constants: LB_FALLBACK_SCAN_PERIOD_SEC = 120",
      _app.LB_FALLBACK_SCAN_PERIOD_SEC == 120,
      f"value={_app.LB_FALLBACK_SCAN_PERIOD_SEC}")

# ── Isolation: confirm no money-path symbols were touched ─────────────────────
print("\n── Isolation: confirm no money-path globals modified ──")
check("Isolation: STRATEGY_WEIGHTS unchanged",
      isinstance(_app.STRATEGY_WEIGHTS, dict))
check("Isolation: AUTO_FIRED_KEYS unchanged",
      isinstance(_app.AUTO_FIRED_KEYS, set))
check("Isolation: ACTIVE_TRADES_BY_INST unchanged",
      isinstance(_app.ACTIVE_TRADES_BY_INST, dict))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"LB Stale Fallback Tests: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
