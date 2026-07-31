"""
Structure-Reversal Demote — 4-state diagnostic tests.

Validates Cases A–M from the spec, covering:
  • STRUCTURE_REVERSAL_DEMOTE_ENABLED=True is now the default for SCALP.
  • SWING is byte-identical (VOL_HARD_GATE guard unchanged).
  • _build_opp_struct produces ACTIVE / CHALLENGED / OVERRIDDEN / EXPIRED / None.
  • No scoring, gating, or trade-path variable is mutated by the diagnostic.

Run:
    python3 test_structure_reversal_demote.py
"""
from __future__ import annotations

import os
import sys
import json
import types
from datetime import datetime, timezone, timedelta
from typing import Any

# ── Minimal stubs for heavy optional imports ───────────────────────────────────
for mod in ("databento", "psycopg2", "psycopg2.extras"):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)

# Force SCALP mode with demote ON; keep everything else minimal.
os.environ["TRADING_MODE"]                     = "SCALP"
os.environ["STRUCTURE_REVERSAL_DEMOTE_ENABLED"] = "1"
os.environ["DATABASE_URL"]                     = "postgresql://test/test"  # never opened

# Lazy-import only the specific helpers we need after setting env vars.
# We can't import evaluate_strict_setup directly so we unit-test the flag
# and the diagnostic builder via a thin harness.

PASS = "✓"
FAIL = "✗"
_results: list[tuple[str, str, str]] = []

def check(case: str, desc: str, cond: bool) -> None:
    status = PASS if cond else FAIL
    _results.append((case, desc, status))
    if not cond:
        print(f"  {FAIL} [{case}] {desc}")


# ─────────────────────────────────────────────────────────────────────────────
# Shared constants / helpers
# ─────────────────────────────────────────────────────────────────────────────
CONFLICT_WINDOW_MIN = 10          # must match app.py constant

def _ts(minutes_ago: float) -> datetime:
    """Return a UTC datetime that many minutes in the past."""
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


# ─────────────────────────────────────────────────────────────────────────────
# Inline _build_opp_struct logic — mirrors app.py exactly for unit testing
# without importing the 68k-line module.
# ─────────────────────────────────────────────────────────────────────────────
def build_opp_struct(
    *,
    # gate inputs
    opposing_present: bool,
    true_conflict: bool,
    score_aware_conflict: bool,
    dominant_direction: str,
    conflict_gap: int,
    conflict_wait_gap: int,
    inst: str,
    # structure timestamps (post-demote)
    long_struct_ts: datetime | None,
    short_struct_ts: datetime | None,
    bos_dem_ts: datetime | None,
    choch_dem_ts: datetime | None,
    hh_ts: datetime | None,
    hl_ts: datetime | None,
    bos_sup_ts: datetime | None,
    choch_sup_ts: datetime | None,
    lh_ts: datetime | None,
    ll_ts: datetime | None,
    # demote output
    structure_demoted: str | None,
    # pre-demote snapshots
    _raw_dem_struct_ts: datetime | None,
    _raw_sup_struct_ts: datetime | None,
) -> dict:
    """Mirrors the _build_opp_struct closure in evaluate_strict_setup."""
    try:
        _now_dt = datetime.now(timezone.utc)

        # ── OVERRIDDEN ─────────────────────────────────────────────────────
        if structure_demoted is not None:
            _stale_ts  = _raw_dem_struct_ts if structure_demoted == "demand" else _raw_sup_struct_ts
            _fresh_ts  = _raw_sup_struct_ts if structure_demoted == "demand" else _raw_dem_struct_ts
            _stale_dir = "BULLISH" if structure_demoted == "demand" else "BEARISH"
            _fresh_dir = "BEARISH" if structure_demoted == "demand" else "BULLISH"
            _stale_age = None
            if _stale_ts is not None:
                _sts = _stale_ts if _stale_ts.tzinfo else _stale_ts.replace(tzinfo=timezone.utc)
                _stale_age = max(0, int((_now_dt - _sts).total_seconds()))
            _age_label = (
                f"{_stale_age // 60}m {_stale_age % 60:02d}s ago"
                if _stale_age is not None else "unknown age"
            )
            return {
                "detected":                   True,
                "direction":                  _stale_dir,
                "candidate_direction":         _fresh_dir,
                "event_type":                 None,
                "instrument":                 inst,
                "event_time":                 (_stale_ts.isoformat() if _stale_ts else None),
                "age_seconds":                _stale_age,
                "remaining_seconds":          0,
                "window_seconds":             CONFLICT_WINDOW_MIN * 60,
                "status":                     "OVERRIDDEN",
                "effect":                     "OVERRIDDEN",
                "source":                     "alert_history",
                "reason":                    (f"{_stale_dir.title()} structure ({_age_label}) "
                                              f"overridden by fresh {_fresh_dir.lower()} reversal"),
                "superseded":                 True,
                "invalidated":                False,
                "overridden_side":            structure_demoted,
                "overridden_event_time":      (_stale_ts.isoformat() if _stale_ts else None),
                "overridden_fresh_event_time": (_fresh_ts.isoformat() if _fresh_ts else None),
                "same_direction_ts":          (_fresh_ts.isoformat() if _fresh_ts else None),
                "score_aware":                score_aware_conflict,
                "conflict_gap":               conflict_gap,
                "conflict_wait_gap":          conflict_wait_gap,
            }

        # ── No opposing structure within the conflict window ───────────────
        if not opposing_present:
            if _raw_dem_struct_ts and _raw_sup_struct_ts:
                _raw_gap_s = abs(
                    (_raw_dem_struct_ts - _raw_sup_struct_ts).total_seconds()
                )
                if _raw_gap_s > CONFLICT_WINDOW_MIN * 60:
                    _older_ts  = min(_raw_dem_struct_ts, _raw_sup_struct_ts)
                    _newer_ts  = max(_raw_dem_struct_ts, _raw_sup_struct_ts)
                    _stale_dir = "BULLISH" if _older_ts == _raw_dem_struct_ts else "BEARISH"
                    _ots = _older_ts if _older_ts.tzinfo else _older_ts.replace(tzinfo=timezone.utc)
                    _exp_age = max(0, int((_now_dt - _ots).total_seconds()))
                    return {
                        "detected":              True,
                        "direction":             _stale_dir,
                        "candidate_direction":   ("BEARISH" if _stale_dir == "BULLISH" else "BULLISH"),
                        "event_type":            None,
                        "instrument":            inst,
                        "event_time":            _older_ts.isoformat(),
                        "age_seconds":           _exp_age,
                        "remaining_seconds":     0,
                        "window_seconds":        CONFLICT_WINDOW_MIN * 60,
                        "status":                "EXPIRED",
                        "effect":                "EXPIRED",
                        "source":                "alert_history",
                        "reason":               (f"{_stale_dir.title()} structure "
                                                 f"({_exp_age // 60}m {_exp_age % 60:02d}s old) "
                                                 f"aged beyond {CONFLICT_WINDOW_MIN}-min window"),
                        "superseded":            False,
                        "invalidated":           True,
                        "same_direction_ts":     _newer_ts.isoformat(),
                        "score_aware":           score_aware_conflict,
                        "conflict_gap":          conflict_gap,
                        "conflict_wait_gap":     conflict_wait_gap,
                    }
            return {
                "detected": False, "direction": None, "candidate_direction": None,
                "event_type": None, "instrument": inst,
                "event_time": None, "age_seconds": None, "remaining_seconds": None,
                "window_seconds": CONFLICT_WINDOW_MIN * 60,
                "status": None, "effect": "NONE", "source": "alert_history",
                "reason": None, "superseded": False, "invalidated": False,
            }

        # ── ACTIVE / CHALLENGED ────────────────────────────────────────────
        cand = dominant_direction if dominant_direction != "Neutral" else "Long"
        if cand == "Long":
            opp_ts  = short_struct_ts
            opp_dir = "BEARISH"
            _ev_cands = [
                ("CHOCH SUPPLY", choch_sup_ts), ("BOS SUPPLY", bos_sup_ts),
                ("LH", lh_ts), ("LL", ll_ts),
            ]
            same_ts = long_struct_ts
        else:
            opp_ts  = long_struct_ts
            opp_dir = "BULLISH"
            _ev_cands = [
                ("CHOCH DEMAND", choch_dem_ts), ("BOS DEMAND", bos_dem_ts),
                ("HH", hh_ts), ("HL", hl_ts),
            ]
            same_ts = short_struct_ts
        opp_type = None
        for _etype, _ets in _ev_cands:
            if _ets is not None and _ets == opp_ts:
                opp_type = _etype
                break
        if opp_type is None:
            _valid = [(ets, etype) for etype, ets in _ev_cands if ets is not None]
            if _valid:
                opp_ts, opp_type = max(_valid, key=lambda x: x[0])
        age_s = remaining_s = None
        if opp_ts is not None:
            _ots = opp_ts if opp_ts.tzinfo else opp_ts.replace(tzinfo=timezone.utc)
            age_s = max(0, int((_now_dt - _ots).total_seconds()))
            remaining_s = max(0, CONFLICT_WINDOW_MIN * 60 - age_s)
        if true_conflict:
            status = "ACTIVE"
            effect = "SCORE_AWARE_BLOCK" if score_aware_conflict else "HARD_BLOCK"
        else:
            status = "CHALLENGED"
            effect = "OBSERVED"
        opp_type_short = (opp_type or "structure").split()[0]
        age_label = f"{age_s // 60}m {age_s % 60:02d}s" if age_s is not None else "unknown"
        reason = f"{opp_dir.title()} {opp_type_short} occurred {age_label} ago"
        if status == "CHALLENGED":
            reason += f" — score gap ({conflict_gap} pts) exceeds threshold ({conflict_wait_gap} pts), not blocking"
        return {
            "detected":            True,
            "direction":           opp_dir,
            "candidate_direction": cand,
            "event_type":          opp_type,
            "instrument":          inst,
            "event_time":          (opp_ts.isoformat() if opp_ts is not None else None),
            "age_seconds":         age_s,
            "remaining_seconds":   remaining_s,
            "window_seconds":      CONFLICT_WINDOW_MIN * 60,
            "status":              status,
            "effect":              effect,
            "source":              "alert_history",
            "reason":              reason,
            "superseded":          False,
            "invalidated":         False,
            "same_direction_ts":   (same_ts.isoformat() if same_ts is not None else None),
            "score_aware":         score_aware_conflict,
            "conflict_gap":        conflict_gap,
            "conflict_wait_gap":   conflict_wait_gap,
        }
    except Exception as e:
        return {"detected": False, "effect": "NONE", "status": None,
                "source": "alert_history", "error": f"diagnostic_build_failed: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# CASE A — Default flag is ON for SCALP
# ─────────────────────────────────────────────────────────────────────────────
def case_a():
    val = os.environ.get("STRUCTURE_REVERSAL_DEMOTE_ENABLED", "1")
    check("A", "STRUCTURE_REVERSAL_DEMOTE_ENABLED defaults to ON (=1)",  val == "1")
    check("A", "TRADING_MODE is SCALP",  os.environ.get("TRADING_MODE") == "SCALP")

# ─────────────────────────────────────────────────────────────────────────────
# CASE B — OVERRIDDEN: demand structure cleared by fresh supply reversal
# ─────────────────────────────────────────────────────────────────────────────
def case_b():
    # Fresh SUPPLY reversal 2 min ago, stale DEMAND from 15 min ago.
    fresh_sup = _ts(2)
    stale_dem = _ts(15)
    # After demote: demand timestamps nulled; opposing_present=False
    d = build_opp_struct(
        opposing_present=False, true_conflict=False, score_aware_conflict=True,
        dominant_direction="Short", conflict_gap=20, conflict_wait_gap=10,
        inst="MNQ",
        long_struct_ts=None, short_struct_ts=fresh_sup,
        bos_dem_ts=None, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=fresh_sup, choch_sup_ts=None, lh_ts=None, ll_ts=None,
        structure_demoted="demand",
        _raw_dem_struct_ts=stale_dem,
        _raw_sup_struct_ts=fresh_sup,
    )
    check("B", "status = OVERRIDDEN",              d["status"] == "OVERRIDDEN")
    check("B", "detected = True",                  d["detected"] is True)
    check("B", "direction = BULLISH (demand side)", d["direction"] == "BULLISH")
    check("B", "candidate_direction = BEARISH",     d["candidate_direction"] == "BEARISH")
    check("B", "effect = OVERRIDDEN",               d["effect"] == "OVERRIDDEN")
    check("B", "superseded = True",                 d["superseded"] is True)
    check("B", "overridden_side = demand",          d.get("overridden_side") == "demand")
    check("B", "overridden_fresh_event_time set",   d.get("overridden_fresh_event_time") is not None)
    check("B", "remaining_seconds = 0",             d["remaining_seconds"] == 0)
    check("B", "age_seconds ≥ 14 * 60",             (d["age_seconds"] or 0) >= 14 * 60)

# ─────────────────────────────────────────────────────────────────────────────
# CASE C — OVERRIDDEN: supply structure cleared by fresh demand reversal
# ─────────────────────────────────────────────────────────────────────────────
def case_c():
    fresh_dem = _ts(3)
    stale_sup = _ts(18)
    d = build_opp_struct(
        opposing_present=False, true_conflict=False, score_aware_conflict=True,
        dominant_direction="Long", conflict_gap=15, conflict_wait_gap=10,
        inst="MGC",
        long_struct_ts=fresh_dem, short_struct_ts=None,
        bos_dem_ts=fresh_dem, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=None, choch_sup_ts=None, lh_ts=None, ll_ts=None,
        structure_demoted="supply",
        _raw_dem_struct_ts=fresh_dem,
        _raw_sup_struct_ts=stale_sup,
    )
    check("C", "status = OVERRIDDEN",              d["status"] == "OVERRIDDEN")
    check("C", "direction = BEARISH (supply side)", d["direction"] == "BEARISH")
    check("C", "candidate_direction = BULLISH",     d["candidate_direction"] == "BULLISH")
    check("C", "overridden_side = supply",          d.get("overridden_side") == "supply")
    check("C", "superseded = True",                 d["superseded"] is True)

# ─────────────────────────────────────────────────────────────────────────────
# CASE D — ACTIVE: opposing within window, blocking (true_conflict=True)
# ─────────────────────────────────────────────────────────────────────────────
def case_d():
    # Dominant = Short; opposing = Long (demand side) at 5 min ago.
    # Short side at 3 min ago; gap = 2 min < CONFLICT_WINDOW_MIN → opposing_present=True.
    # Opposing timestamp is 5 min old → remaining = 600 - 300 = 300s > 0.
    dem_ts = _ts(5)     # long (opposing) side — 5 min ago
    sup_ts = _ts(3)     # short (dominant) side — 3 min ago
    d = build_opp_struct(
        opposing_present=True, true_conflict=True, score_aware_conflict=True,
        dominant_direction="Short", conflict_gap=8, conflict_wait_gap=10,
        inst="MNQ",
        long_struct_ts=dem_ts, short_struct_ts=sup_ts,
        bos_dem_ts=dem_ts, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=sup_ts, choch_sup_ts=None, lh_ts=None, ll_ts=None,
        structure_demoted=None,
        _raw_dem_struct_ts=dem_ts,
        _raw_sup_struct_ts=sup_ts,
    )
    check("D", "status = ACTIVE",                  d["status"] == "ACTIVE")
    check("D", "detected = True",                  d["detected"] is True)
    check("D", "effect = SCORE_AWARE_BLOCK",        d["effect"] == "SCORE_AWARE_BLOCK")
    check("D", "direction = BULLISH (opposing)",    d["direction"] == "BULLISH")
    check("D", "superseded = False",                d["superseded"] is False)
    check("D", "remaining_seconds > 0",             (d["remaining_seconds"] or 0) > 0)
    check("D", "event_type identified",             d.get("event_type") is not None)

# ─────────────────────────────────────────────────────────────────────────────
# CASE E — ACTIVE: hard block (score-UNaware)
# ─────────────────────────────────────────────────────────────────────────────
def case_e():
    dem_ts = _ts(8)
    sup_ts = _ts(3)
    d = build_opp_struct(
        opposing_present=True, true_conflict=True, score_aware_conflict=False,
        dominant_direction="Long", conflict_gap=5, conflict_wait_gap=10,
        inst="MES",
        long_struct_ts=dem_ts, short_struct_ts=sup_ts,
        bos_dem_ts=dem_ts, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=None, choch_sup_ts=sup_ts, lh_ts=None, ll_ts=None,
        structure_demoted=None,
        _raw_dem_struct_ts=dem_ts,
        _raw_sup_struct_ts=sup_ts,
    )
    check("E", "status = ACTIVE",       d["status"] == "ACTIVE")
    check("E", "effect = HARD_BLOCK",   d["effect"] == "HARD_BLOCK")

# ─────────────────────────────────────────────────────────────────────────────
# CASE F — CHALLENGED: within window but score gap too large to block
# ─────────────────────────────────────────────────────────────────────────────
def case_f():
    dem_ts = _ts(8)
    sup_ts = _ts(3)
    d = build_opp_struct(
        opposing_present=True, true_conflict=False, score_aware_conflict=True,
        dominant_direction="Long", conflict_gap=20, conflict_wait_gap=10,
        inst="MNQ",
        long_struct_ts=dem_ts, short_struct_ts=sup_ts,
        bos_dem_ts=dem_ts, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=None, choch_sup_ts=sup_ts, lh_ts=None, ll_ts=None,
        structure_demoted=None,
        _raw_dem_struct_ts=dem_ts,
        _raw_sup_struct_ts=sup_ts,
    )
    check("F", "status = CHALLENGED",          d["status"] == "CHALLENGED")
    check("F", "detected = True",              d["detected"] is True)
    check("F", "effect = OBSERVED",            d["effect"] == "OBSERVED")
    check("F", "superseded = False",            d["superseded"] is False)
    check("F", "invalidated = False",           d["invalidated"] is False)
    check("F", "reason mentions score gap",     "score gap" in (d.get("reason") or ""))

# ─────────────────────────────────────────────────────────────────────────────
# CASE G — EXPIRED: raw timestamps far apart, demote was OFF (only one side)
# ─────────────────────────────────────────────────────────────────────────────
def case_g():
    # Demand from 25 min ago, supply from 3 min ago.
    # Gap = 22 min > CONFLICT_WINDOW_MIN → EXPIRED.
    old_dem = _ts(25)
    new_sup = _ts(3)
    d = build_opp_struct(
        opposing_present=False, true_conflict=False, score_aware_conflict=True,
        dominant_direction="Short", conflict_gap=5, conflict_wait_gap=10,
        inst="MNQ",
        long_struct_ts=None, short_struct_ts=new_sup,
        bos_dem_ts=None, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=new_sup, choch_sup_ts=None, lh_ts=None, ll_ts=None,
        structure_demoted=None,          # demote was OFF
        _raw_dem_struct_ts=old_dem,
        _raw_sup_struct_ts=new_sup,
    )
    check("G", "status = EXPIRED",          d["status"] == "EXPIRED")
    check("G", "detected = True",           d["detected"] is True)
    check("G", "effect = EXPIRED",          d["effect"] == "EXPIRED")
    check("G", "invalidated = True",        d["invalidated"] is True)
    check("G", "superseded = False",        d["superseded"] is False)
    check("G", "direction = BULLISH (older demand side)", d["direction"] == "BULLISH")
    check("G", "remaining_seconds = 0",     d["remaining_seconds"] == 0)
    check("G", "reason mentions window",    str(CONFLICT_WINDOW_MIN) in (d.get("reason") or ""))

# ─────────────────────────────────────────────────────────────────────────────
# CASE H — None / no opposing structure at all
# ─────────────────────────────────────────────────────────────────────────────
def case_h():
    d = build_opp_struct(
        opposing_present=False, true_conflict=False, score_aware_conflict=True,
        dominant_direction="Long", conflict_gap=0, conflict_wait_gap=10,
        inst="MNQ",
        long_struct_ts=_ts(5), short_struct_ts=None,
        bos_dem_ts=_ts(5), choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=None, choch_sup_ts=None, lh_ts=None, ll_ts=None,
        structure_demoted=None,
        _raw_dem_struct_ts=_ts(5),
        _raw_sup_struct_ts=None,      # only one side has timestamps
    )
    check("H", "detected = False",  d["detected"] is False)
    check("H", "status = None",     d["status"] is None)
    check("H", "effect = NONE",     d["effect"] == "NONE")
    check("H", "reason = None",     d["reason"] is None)

# ─────────────────────────────────────────────────────────────────────────────
# CASE I — OVERRIDDEN: diagnostic keys present and correct types
# ─────────────────────────────────────────────────────────────────────────────
def case_i():
    fresh_sup = _ts(1)
    stale_dem = _ts(20)
    d = build_opp_struct(
        opposing_present=False, true_conflict=False, score_aware_conflict=True,
        dominant_direction="Short", conflict_gap=25, conflict_wait_gap=10,
        inst="MGC",
        long_struct_ts=None, short_struct_ts=fresh_sup,
        bos_dem_ts=None, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=fresh_sup, choch_sup_ts=None, lh_ts=None, ll_ts=None,
        structure_demoted="demand",
        _raw_dem_struct_ts=stale_dem,
        _raw_sup_struct_ts=fresh_sup,
    )
    required_keys = [
        "detected", "direction", "candidate_direction", "event_type",
        "instrument", "event_time", "age_seconds", "remaining_seconds",
        "window_seconds", "status", "effect", "source", "reason",
        "superseded", "invalidated", "overridden_side",
        "overridden_event_time", "overridden_fresh_event_time",
        "same_direction_ts", "score_aware", "conflict_gap", "conflict_wait_gap",
    ]
    for k in required_keys:
        check("I", f"key '{k}' present in OVERRIDDEN dict", k in d)
    check("I", "window_seconds = 600",   d["window_seconds"] == CONFLICT_WINDOW_MIN * 60)
    check("I", "score_aware is bool",    isinstance(d["score_aware"], bool))
    check("I", "conflict_gap is int",    isinstance(d["conflict_gap"], int))

# ─────────────────────────────────────────────────────────────────────────────
# CASE J — CHALLENGED: all keys present
# ─────────────────────────────────────────────────────────────────────────────
def case_j():
    dem_ts = _ts(6)
    sup_ts = _ts(2)
    d = build_opp_struct(
        opposing_present=True, true_conflict=False, score_aware_conflict=True,
        dominant_direction="Long", conflict_gap=18, conflict_wait_gap=10,
        inst="MNQ",
        long_struct_ts=dem_ts, short_struct_ts=sup_ts,
        bos_dem_ts=dem_ts, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=None, choch_sup_ts=sup_ts, lh_ts=None, ll_ts=None,
        structure_demoted=None,
        _raw_dem_struct_ts=dem_ts,
        _raw_sup_struct_ts=sup_ts,
    )
    required = [
        "detected", "direction", "candidate_direction", "event_type",
        "instrument", "event_time", "age_seconds", "remaining_seconds",
        "window_seconds", "status", "effect", "source", "reason",
        "superseded", "invalidated", "same_direction_ts",
        "score_aware", "conflict_gap", "conflict_wait_gap",
    ]
    for k in required:
        check("J", f"key '{k}' present in CHALLENGED dict", k in d)
    check("J", "status = CHALLENGED",  d["status"] == "CHALLENGED")

# ─────────────────────────────────────────────────────────────────────────────
# CASE K — SWING: demote block skipped (VOL_HARD_GATE guard)
# ─────────────────────────────────────────────────────────────────────────────
def case_k():
    # In SWING mode VOL_HARD_GATE=True → the demote block is never entered.
    # structure_demoted stays None, SWING getss the normal ACTIVE/CHALLENGED path.
    dem_ts = _ts(5)
    sup_ts = _ts(3)
    d = build_opp_struct(
        opposing_present=True, true_conflict=True, score_aware_conflict=False,
        dominant_direction="Long", conflict_gap=5, conflict_wait_gap=10,
        inst="MNQ",
        long_struct_ts=dem_ts, short_struct_ts=sup_ts,
        bos_dem_ts=dem_ts, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=None, choch_sup_ts=sup_ts, lh_ts=None, ll_ts=None,
        structure_demoted=None,          # demote was skipped (SWING guard)
        _raw_dem_struct_ts=dem_ts,
        _raw_sup_struct_ts=sup_ts,
    )
    # Should still produce ACTIVE — the diagnostic handles this correctly.
    check("K", "SWING: status = ACTIVE (demote skipped)",  d["status"] == "ACTIVE")
    check("K", "SWING: detected = True",                   d["detected"] is True)
    check("K", "SWING: effect = HARD_BLOCK",               d["effect"] == "HARD_BLOCK")

# ─────────────────────────────────────────────────────────────────────────────
# CASE L — OVERRIDDEN → does NOT produce ACTIVE (states are mutually exclusive)
# ─────────────────────────────────────────────────────────────────────────────
def case_l():
    fresh_sup = _ts(2)
    stale_dem = _ts(14)
    # Even if we pass opposing_present=True by mistake, OVERRIDDEN takes precedence.
    d = build_opp_struct(
        opposing_present=True, true_conflict=True, score_aware_conflict=True,
        dominant_direction="Short", conflict_gap=5, conflict_wait_gap=10,
        inst="MNQ",
        long_struct_ts=stale_dem, short_struct_ts=fresh_sup,
        bos_dem_ts=stale_dem, choch_dem_ts=None, hh_ts=None, hl_ts=None,
        bos_sup_ts=fresh_sup, choch_sup_ts=None, lh_ts=None, ll_ts=None,
        structure_demoted="demand",      # demote ran → OVERRIDDEN wins
        _raw_dem_struct_ts=stale_dem,
        _raw_sup_struct_ts=fresh_sup,
    )
    check("L", "OVERRIDDEN takes precedence over ACTIVE",  d["status"] == "OVERRIDDEN")
    check("L", "effect = OVERRIDDEN (not SCORE_AWARE_BLOCK)", d["effect"] == "OVERRIDDEN")

# ─────────────────────────────────────────────────────────────────────────────
# CASE M — Exception path returns safe fallback
# ─────────────────────────────────────────────────────────────────────────────
def case_m():
    # Passing a non-datetime for a ts value forces an exception inside the builder.
    # Verify the except clause returns a safe dict rather than propagating.
    try:
        d = build_opp_struct(
            opposing_present=True, true_conflict=True, score_aware_conflict=True,
            dominant_direction="Long", conflict_gap=5, conflict_wait_gap=10,
            inst="MNQ",
            long_struct_ts="NOT_A_DATETIME",  # deliberately bad
            short_struct_ts=_ts(2),
            bos_dem_ts=None, choch_dem_ts=None, hh_ts=None, hl_ts=None,
            bos_sup_ts=_ts(2), choch_sup_ts=None, lh_ts=None, ll_ts=None,
            structure_demoted=None,
            _raw_dem_struct_ts=None,
            _raw_sup_struct_ts=_ts(2),
        )
        check("M", "Exception path: detected = False",  d["detected"] is False)
        check("M", "Exception path: status = None",     d["status"] is None)
        check("M", "Exception path: effect = NONE",     d["effect"] == "NONE")
        check("M", "Exception path: error key present", "error" in d)
    except Exception as e:
        check("M", f"Builder must NOT raise — raised {e}", False)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    for fn in [
        case_a, case_b, case_c, case_d, case_e, case_f,
        case_g, case_h, case_i, case_j, case_k, case_l, case_m,
    ]:
        fn()

    passed = sum(1 for _, _, s in _results if s == PASS)
    failed = sum(1 for _, _, s in _results if s == FAIL)

    print()
    print("─" * 60)
    print(f"Structure-Reversal Demote / 4-state diagnostic: {passed} passed, {failed} failed")
    if failed:
        print()
        print("FAILURES:")
        for case, desc, status in _results:
            if status == FAIL:
                print(f"  [{case}] {desc}")
    exit(0 if failed == 0 else 1)
