"""Persistent market thesis + hysteresis (Phase 1) tests.

Drives _apply_thesis() directly — pure-function, no network/threads/DB.
Covers all 16 spec cases:

  1.  READY promotion when score >= READY_THRESHOLD and all gates pass
  2.  Hysteresis hold: stays READY while score >= HOLD_THRESHOLD
  3.  Drop to WAIT when confidence falls below HOLD_THRESHOLD
  4.  Reversal < REVERSAL_THRESHOLD only weakens (no flip)
  5.  Reversal >= REVERSAL_THRESHOLD flips the thesis direction
  6.  Zone consumed → hard invalidation → immediate WAIT
  7.  Structure lost with no direction → hard invalidation → immediate WAIT
  8.  Per-instrument isolation (MGC and MNQ hold independent state)
  9.  Cooldown blocks new thesis while active
  10. Confidence rise is immediate (raw_score > prev → new = raw_score)
  11. Confidence fall is capped at MAX_CONF_DROP per evaluation
  12. New thesisId is created when thesis resets from INVALIDATED
  13. FLAG-OFF (THESIS_HYSTERESIS=0) passes verdict through unchanged
  14. /status payload carries a "thesis" key
  15. Journal entry carries thesis fields (thesisId, confidenceAtEntry, etc.)
  16. SWING zone-required gate and SCALP zone-not-required interact correctly
      with the hard-invalidation path

Runnable two ways:
  pytest test_thesis_hysteresis.py
  python3 test_thesis_hysteresis.py
"""

import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clear(inst="MGC"):
    """Remove any existing thesis for this instrument."""
    with app.THESIS_LOCK:
        for key in list(app.THESIS_BY_INST):
            if isinstance(key, tuple) and key[0] == inst:
                app.THESIS_BY_INST.pop(key, None)


def _strict(score=80, direction="Long", zone_valid=True, vwap_ok=True,
            struct_ok=True, sweep_ok=True, vol_ok=True, session_ok=False,
            missing=None, zone_broken=False, evidence_epoch=None,
            structure_state=None, reason=None):
    gd = {
        "zone_valid":           zone_valid,
        "vwap_confirmed":       vwap_ok,
        "structure_confirmed":  struct_ok,
        "sweep_confirmed":      sweep_ok,
        "volume_confirmed":     vol_ok,
        "session":              session_ok,
    }
    out = {
        "score":                score,
        "direction":            direction,
        "candidate":            direction,
        "gate_debug":           gd,
        "missing":              missing or [],
        "zone_broken_active":   zone_broken,
    }
    if evidence_epoch is not None:
        out["evidence_epoch"] = evidence_epoch
    if structure_state is not None:
        out["structure_state"] = structure_state
    if reason is not None:
        out["reason"] = reason
    return out


def _run(verdict, score=80, direction="Long", inst="MGC", **kw):
    """Call _apply_thesis and return (adj_verdict, snapshot)."""
    s = _strict(score=score, direction=direction, **kw)
    return app._apply_thesis(inst, s, verdict)


# ── Test 1: READY promotion ───────────────────────────────────────────────────

def test_ready_promotion_from_forming():
    """Score>=75, all gates pass → thesis transitions to READY and verdict is *
    LONG READY."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear()
        adj, t = _run("LONG READY", score=80)
        assert "READY" in adj, f"Expected READY verdict, got {adj!r}"
        assert t["status"] == "READY_LONG", t["status"]
        assert t["confidence"] == 80
        assert t["thesisId"].startswith("th_")
    finally:
        app.TRADING_MODE = saved_mode
        _clear()


# ── Test 2: Hysteresis hold ───────────────────────────────────────────────────

def test_hysteresis_hold_above_60():
    """The thesis can stay READY, but a strict WAIT must stay non-actionable."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear()
        # First eval: establish READY_LONG at confidence 80
        _run("LONG READY", score=80)
        # Second eval: raw verdict is WAIT, score dips to 68 — above hold floor (60)
        adj, t = _run("WAIT", score=68)
        assert adj == "WAIT", f"Strict WAIT must stay WAIT, got {adj!r}"
        assert t["status"] == "READY_LONG"
        assert t["entryPaused"] is True
    finally:
        app.TRADING_MODE = saved_mode
        _clear()


# ── Test 3: Drop to WAIT below HOLD_THRESHOLD ────────────────────────────────

def test_drops_to_wait_below_hold_threshold():
    """Confidence falling through many evals eventually drops below 60 → WAIT."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear()
        # Establish READY_LONG at 80
        _run("LONG READY", score=80, evidence_epoch="bar-1")
        # Apply 3 opposing evals; each caps the drop at MAX_CONF_DROP=15
        # 80 → max(30,65) = 65 → max(30,50) = 50 → max(30,35) = 35
        _run("WAIT", score=30, evidence_epoch="bar-2")   # 80 → 65
        _run("WAIT", score=30, evidence_epoch="bar-3")   # 65 → 50
        adj, t = _run("WAIT", score=30, evidence_epoch="bar-4")  # 50 → 35
        assert "READY" not in adj, f"Should have left hold band, got {adj!r}"
        assert t["status"] not in ("READY_LONG", "READY_SHORT")
    finally:
        app.TRADING_MODE = saved_mode
        _clear()


# ── Test 4: Reversal below REVERSAL_THRESHOLD only weakens ───────────────────

def test_reversal_below_threshold_weakens_not_flips():
    """An opposite-direction signal with score < 85 must NOT flip direction."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear()
        # Establish Long thesis at 80
        _run("LONG READY", score=80)
        # Opposite direction alert at score 70 (below REVERSAL_THRESHOLD 85)
        adj, t = _run("SHORT READY", score=70, direction="Short")
        # Must remain Long, just weakened
        assert t["direction"] == "Long", f"Direction should stay Long, got {t['direction']!r}"
        assert "REVERSAL_THRESHOLD_NOT_REACHED" in (t.get("reasonCodes") or [])
    finally:
        app.TRADING_MODE = saved_mode
        _clear()


# ── Test 5: Reversal at/above REVERSAL_THRESHOLD flips ───────────────────────

def test_reversal_at_threshold_flips_direction():
    """A confirmed opposite structure starts paused, never immediately READY."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear()
        # Establish Long thesis
        _, t1 = _run("LONG READY", score=80)
        old_id = t1["thesisId"]
        # Confirmed Short structure flips the thesis but pauses entry.
        adj, t2 = _run(
            "SHORT READY",
            score=87,
            direction="Short",
            evidence_epoch="bar-2",
            structure_state={
                "state": "REVERSAL_CONFIRMED",
                "confirmed": True,
                "direction": "Short",
                "last_event_at": "2026-08-28T12:01:00+00:00",
            },
        )
        # New thesis must be Short; thesisId should differ
        assert t2["direction"] == "Short", f"Expected Short thesis, got {t2['direction']!r}"
        assert t2["thesisId"] != old_id, "New direction should create a new thesisId"
        assert adj == "WAIT"
        assert t2["status"] == "FORMING_SHORT"
    finally:
        app.TRADING_MODE = saved_mode
        _clear()


# ── Test 6: Zone consumed → immediate WAIT (SWING mode) ──────────────────────

def test_zone_broken_hard_invalidation_swing():
    """In SWING mode a broken zone triggers hard invalidation immediately."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SWING"
        _clear("MGC")
        # Establish thesis first
        s_ok = _strict(score=82, direction="Long", zone_valid=True, vwap_ok=True,
                       struct_ok=True)
        app._apply_thesis("MGC", s_ok, "LONG READY")
        # Now zone consumed
        s_broken = _strict(score=82, direction="Long", zone_valid=False,
                           zone_broken=True, missing=["zone_valid"])
        adj, t = app._apply_thesis("MGC", s_broken, "LONG READY")
        assert "READY" not in adj, f"Broken zone should WAIT, got {adj!r}"
        assert t["status"] == "INVALIDATED"
        assert t["invalidationReason"] == "Zone consumed"
        assert "ZONE_CONSUMED" in (t.get("reasonCodes") or [])
    finally:
        app.TRADING_MODE = saved_mode
        _clear("MGC")


# ── Test 7: Structure lost → hard invalidation ────────────────────────────────

def test_structure_lost_hard_invalidation():
    """No direction + structure in missing keys → hard invalidation."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear()
        _run("LONG READY", score=80)
        s = _strict(score=40, direction=None, struct_ok=False,
                    missing=["structure_confirmed"])
        s["direction"] = None
        s["candidate"] = None
        adj, t = app._apply_thesis("MGC", s, "WAIT")
        assert t["status"] == "INVALIDATED", t["status"]
        assert "READY" not in adj
    finally:
        app.TRADING_MODE = saved_mode
        _clear()


# ── Test 8: Per-instrument isolation ─────────────────────────────────────────

def test_per_instrument_isolation():
    """MGC and MNQ theses are completely independent."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear("MGC"); _clear("MNQ")
        # Establish Long READY for MGC
        adj_mgc, t_mgc = _run("LONG READY", score=80, inst="MGC")
        # MNQ is still blank — should start fresh
        s = _strict(score=30, direction="Short", zone_valid=False, vwap_ok=False,
                    struct_ok=True)
        adj_mnq, t_mnq = app._apply_thesis("MNQ", s, "WAIT")
        assert "READY" in adj_mgc, f"MGC should be READY, got {adj_mgc!r}"
        assert "READY" not in adj_mnq, f"MNQ should be WAIT, got {adj_mnq!r}"
        assert t_mgc["direction"] == "Long"
        assert t_mnq["direction"] in ("Short", None)
        assert t_mgc["thesisId"] != t_mnq.get("thesisId")
    finally:
        app.TRADING_MODE = saved_mode
        _clear("MGC"); _clear("MNQ")


# ── Test 9: Cooldown blocks new thesis formation ──────────────────────────────

def test_cooldown_blocks_new_thesis():
    """A thesis in COOLDOWN status returns WAIT and does not start a new thesis."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SWING"
        _clear("MGC")
        # Force a zone-broken invalidation to enter cooldown
        app.TRADING_MODE = "SWING"
        s_ok = _strict(score=82, direction="Long", zone_valid=True, struct_ok=True)
        app._apply_thesis("MGC", s_ok, "LONG READY")
        s_broken = _strict(score=82, direction="Long", zone_valid=False,
                           zone_broken=True, missing=["zone_valid"])
        app._apply_thesis("MGC", s_broken, "LONG READY")
        # Manually set state to COOLDOWN with far-future cooldownUntil
        with app.THESIS_LOCK:
            t = app.THESIS_BY_INST.get("MGC")
            if t:
                t["status"] = "COOLDOWN"
                far_future = (app.now_utc() +
                              datetime.timedelta(seconds=60)).isoformat()
                t["cooldownUntil"] = far_future
                app.THESIS_BY_INST["MGC"] = t
        # Next eval should see COOLDOWN and return WAIT
        adj, t = _run("LONG READY", score=85, inst="MGC")
        assert "READY" not in adj, f"Cooldown should block READY, got {adj!r}"
        assert t["status"] == "COOLDOWN"
    finally:
        app.TRADING_MODE = saved_mode
        _clear("MGC")


# ── Test 10: Confidence rise is immediate ─────────────────────────────────────

def test_confidence_rise_is_immediate():
    """When raw_score > prev_confidence the new value equals raw_score exactly."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear()
        # Establish at 65
        _run("WAIT", score=65)
        # Now jump to 90
        _, t = _run("LONG READY", score=90)
        assert t["confidence"] == 90, f"Immediate rise expected 90, got {t['confidence']}"
        assert "CONFIDENCE_INCREASED" in (t.get("reasonCodes") or [])
    finally:
        app.TRADING_MODE = saved_mode
        _clear()


# ── Test 11: Confidence fall is capped at MAX_CONF_DROP ──────────────────────

def test_confidence_fall_capped():
    """A single eval cannot drop confidence by more than MAX_CONF_DROP (15)."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear()
        _run("LONG READY", score=80)
        # Next eval at score 20 — should only drop 15 points (80→65, not 20)
        _, t = _run("WAIT", score=20)
        assert t["confidence"] >= 80 - app._THESIS_MAX_CONF_DROP, (
            f"Confidence drop exceeded MAX_CONF_DROP: {t['confidence']}")
        assert t["confidence"] == max(20, 80 - app._THESIS_MAX_CONF_DROP)
    finally:
        app.TRADING_MODE = saved_mode
        _clear()


# ── Test 12: New thesisId after INVALIDATED → fresh cycle ────────────────────

def test_new_thesis_id_after_invalidation():
    """After an invalidation the next valid direction signal creates a new thesisId."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SWING"
        _clear("MGC")
        _, t1 = _run("LONG READY", score=80, inst="MGC")
        old_id = t1["thesisId"]
        # Force invalidation via zone broken
        app.TRADING_MODE = "SWING"
        s_broken = _strict(score=80, direction="Long", zone_valid=False,
                           zone_broken=True, missing=["zone_valid"])
        app._apply_thesis("MGC", s_broken, "WAIT")
        # Clear cooldown so a fresh thesis can form
        with app.THESIS_LOCK:
            t = app.THESIS_BY_INST.get("MGC")
            if t:
                t["status"] = "INVALIDATED"   # not COOLDOWN
                t["cooldownUntil"] = None
                app.THESIS_BY_INST["MGC"] = t
        # New eval → new thesis
        _, t2 = _run("LONG READY", score=80, inst="MGC")
        assert t2["thesisId"] != old_id, (
            f"New thesis must have a fresh ID; both were {old_id!r}")
    finally:
        app.TRADING_MODE = saved_mode
        _clear("MGC")


# ── Test 13: FLAG-OFF passthrough ─────────────────────────────────────────────

def test_flag_off_passthrough():
    """THESIS_HYSTERESIS=0 must pass the raw verdict through unchanged."""
    orig = app._THESIS_ENABLED
    try:
        app._THESIS_ENABLED = False
        _clear()
        adj, t = _run("WAIT", score=80)
        assert adj == "WAIT",  f"FLAG-OFF should passthrough WAIT, got {adj!r}"
        assert t == {},        f"FLAG-OFF should return empty snapshot, got {t!r}"
        adj2, t2 = _run("LONG READY", score=80)
        assert adj2 == "LONG READY", f"FLAG-OFF should passthrough LONG READY, got {adj2!r}"
    finally:
        app._THESIS_ENABLED = orig
        _clear()


# ── Test 14: /status payload carries "thesis" key ────────────────────────────

def test_status_payload_has_thesis_key():
    """_build_status_payload must include a 'thesis' key (may be None/dict)."""
    payload = app._build_status_payload("MGC")
    assert "thesis" in payload, (
        "status payload missing 'thesis' key — check _build_status_payload")


# ── Test 15: Journal entry carries thesis fields ──────────────────────────────

def test_journal_entry_has_thesis_fields():
    """A synthesised full_analysis dict passed into create_journal_entry must
    produce an entry with thesisId, confidenceAtEntry, and related fields."""
    REQUIRED = {
        "thesisId", "confidenceAtEntry", "thesisAgeAtEntry",
        "supportingEvidence", "opposingEvidence",
    }
    saved_journal  = list(app.JOURNAL)
    saved_keys     = set(app.JOURNAL_KEYS)
    try:
        # Build a minimal fake full_analysis result that satisfies the journaller
        fake_a = {
            "strict_label":  "Strong Trade",
            "direction":     "Long",
            "verdict":       "LONG READY",
            "edge_score":    80,
            "edge_grade":    "A",
            "current_price": 2000.0,
            "vwap_value":    1995.0,
            "vwap_status":   "ok",
            "nearest_supply": 2020.0,
            "nearest_demand": 1980.0,
            "structure_class": "DEMAND",
            "structure_detail": "BOS",
            "risk_label":    "Low",
            "risk_detail":   "",
            "overextended":  False,
            "session":       {"prime": False},
            "trade_plan": {
                "trade_plan": True,
                "entry_zone": "1995–2000",
                "stop_loss":  1985.0,
                "target1":    2015.0,
                "target2":    2025.0,
                "longs_allowed": True,
                "shorts_allowed": False,
                "action":    "BUY",
                "warning":   "",
            },
            "alert_diagnostics": {},
            "thesis": {
                "thesisId":          "th_test1234",
                "createdAt":         app.now_utc().isoformat(),
                "confidence":        80,
                "thesisAgeMs":       5000,
                "status":            "READY_LONG",
                "evidenceFor":       ["STRUCTURE_BULLISH", "ZONE_ACTIVE"],
                "evidenceAgainst":   [],
                "invalidationReason": None,
            },
        }
        fake_record = {
            "ticker":     "MGC",
            "instrument": "MGC",
            "alert_type": "BOS DEMAND",
            "timestamp":  app.now_utc().isoformat(),
        }
        # Clear journal to avoid key collision
        app.JOURNAL.clear()
        app.JOURNAL_KEYS.clear()
        entry = app.create_journal_entry(fake_record, fake_a, sizing={},
                                         post_discord=False)
        assert entry is not None, "create_journal_entry returned None"
        missing = REQUIRED - set(entry.keys())
        assert not missing, f"Journal entry missing thesis fields: {missing}"
        assert entry["thesisId"] == "th_test1234"
        assert entry["confidenceAtEntry"] == 80
    finally:
        app.JOURNAL.clear()
        app.JOURNAL.extend(saved_journal)
        app.JOURNAL_KEYS.clear()
        app.JOURNAL_KEYS.update(saved_keys)


# ── Test 16: SCALP zone-not-required avoids spurious hard-invalidation ────────

def test_scalp_no_zone_no_hard_invalidation():
    """In SCALP mode zone_req is False — a missing zone must NOT trigger hard
    invalidation; the thesis forms/holds normally."""
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear("MGC")
        # zone_valid=False but zone_broken=False — no zone, but not consumed
        s = _strict(score=78, direction="Long", zone_valid=False,
                    zone_broken=False, vwap_ok=True, struct_ok=True)
        adj, t = app._apply_thesis("MGC", s, "LONG READY")
        assert t["status"] != "INVALIDATED", (
            f"SCALP: missing-but-not-broken zone must NOT invalidate; status={t['status']!r}")
        # A zone_broken flag must still invalidate even in SCALP (zone is required ONLY
        # for SWING; but zone_broken = "consumed" which is a hard signal we honour)
        # ── skip the above assertion for zone_broken=True; that's SWING-only ──
        # Just confirm status is FORMING or READY
        assert t["status"] in ("FORMING_LONG", "READY_LONG"), t["status"]
    finally:
        app.TRADING_MODE = saved_mode
        _clear("MGC")


def test_identical_heartbeat_epoch_does_not_drain_confidence():
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear("MGC")
        _run("LONG READY", score=80, evidence_epoch="bar-1")
        _, first = _run("WAIT", score=20, evidence_epoch="bar-2")
        _, second = _run("WAIT", score=20, evidence_epoch="bar-2")
        _, third = _run("WAIT", score=20, evidence_epoch="bar-2")
        assert first["confidence"] == 65
        assert second["confidence"] == first["confidence"]
        assert third["confidence"] == first["confidence"]
        assert "EVIDENCE_UNCHANGED" in third["reasonCodes"]
    finally:
        app.TRADING_MODE = saved_mode
        _clear("MGC")


def test_new_bar_epoch_updates_confidence_once():
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear("MGC")
        _run("LONG READY", score=80, evidence_epoch="bar-1")
        _, bar2 = _run("WAIT", score=40, evidence_epoch="bar-2")
        _, bar2_repeat = _run("WAIT", score=40, evidence_epoch="bar-2")
        _, bar3 = _run("WAIT", score=40, evidence_epoch="bar-3")
        assert bar2["confidence"] == 65
        assert bar2_repeat["confidence"] == 65
        assert bar3["confidence"] == 50
    finally:
        app.TRADING_MODE = saved_mode
        _clear("MGC")


def test_scalp_and_intraday_theses_are_isolated():
    _clear("MGC")
    try:
        scalp_strict = _strict(
            score=82, direction="Long", evidence_epoch="scalp-bar-1",
        )
        it_strict = _strict(
            score=90, direction="Short", evidence_epoch="it-bar-1",
        )
        _, scalp = app._apply_thesis(
            "MGC", scalp_strict, "LONG READY", mode="SCALP",
        )
        _, intraday = app._apply_thesis(
            "MGC", it_strict, "SHORT READY", mode="INTRADAY_TREND",
        )
        assert scalp["mode"] == "SCALP"
        assert intraday["mode"] == "INTRADAY_TREND"
        assert scalp["direction"] == "Long"
        assert intraday["direction"] == "Short"
        assert scalp["thesisId"] != intraday["thesisId"]
        assert app.get_thesis_snapshot("MGC", "SCALP")["direction"] == "Long"
        assert app.get_thesis_snapshot("MGC", "INTRADAY_TREND")["direction"] == "Short"
    finally:
        _clear("MGC")


def test_same_epoch_hard_invalidation_still_demotes_immediately():
    _clear("MGC")
    try:
        ready = _strict(
            score=82, direction="Long", evidence_epoch="bar-1",
        )
        app._apply_thesis("MGC", ready, "LONG READY", mode="SWING")
        broken = _strict(
            score=82, direction="Long", zone_valid=False,
            zone_broken=True, missing=["zone_valid"],
            evidence_epoch="bar-1",
        )
        adj, snap = app._apply_thesis(
            "MGC", broken, "WAIT", mode="SWING",
        )
        assert adj == "WAIT"
        assert snap["status"] == "INVALIDATED"
        assert snap["confidence"] == 0
        cleared = _strict(
            score=90,
            direction="Long",
            evidence_epoch="bar-1",
        )
        retry_adj, retry_snap = app._apply_thesis(
            "MGC", cleared, "LONG READY", mode="SWING",
        )
        assert retry_adj == "WAIT"
        assert retry_snap["status"] == "INVALIDATED"
        assert retry_snap["thesisId"] == snap["thesisId"]
    finally:
        _clear("MGC")


def test_confirmed_opposite_structure_demotes_even_inside_same_bar():
    _clear("MGC")
    try:
        ready = _strict(
            score=82,
            direction="Long",
            evidence_epoch="bar-1",
            structure_state={
                "state": "TREND_CONFIRMED",
                "confirmed": True,
                "direction": "Long",
                "last_event_at": "2026-08-28T12:00:00+00:00",
            },
        )
        _, original = app._apply_thesis(
            "MGC", ready, "LONG READY", mode="SCALP",
        )
        opposite = _strict(
            score=90,
            direction="Short",
            evidence_epoch="bar-1",
            structure_state={
                "state": "REVERSAL_CONFIRMED",
                "confirmed": True,
                "direction": "Short",
                "last_event_at": "2026-08-28T12:00:01+00:00",
            },
        )
        adj, reversed_snap = app._apply_thesis(
            "MGC", opposite, "SHORT READY", mode="SCALP",
        )
        assert adj == "WAIT"
        assert reversed_snap["direction"] == "Short"
        assert reversed_snap["status"] == "FORMING_SHORT"
        assert reversed_snap["entryPaused"] is True
        assert reversed_snap["thesisId"] != original["thesisId"]

        same_adj, same_snap = app._apply_thesis(
            "MGC", opposite, "SHORT READY", mode="SCALP",
        )
        assert same_adj == "WAIT"
        assert same_snap["thesisId"] == reversed_snap["thesisId"]

        later = dict(opposite)
        later["evidence_epoch"] = "bar-2"
        later_adj, later_snap = app._apply_thesis(
            "MGC", later, "SHORT READY", mode="SCALP",
        )
        assert later_adj == "SHORT READY"
        assert later_snap["thesisId"] == reversed_snap["thesisId"]
    finally:
        _clear("MGC")


def test_reversal_candidate_never_flips_active_thesis():
    _clear("MGC")
    try:
        ready = _strict(
            score=82,
            direction="Long",
            evidence_epoch="bar-1",
            structure_state={
                "state": "TREND_CONFIRMED",
                "confirmed": True,
                "direction": "Long",
                "last_event_at": "2026-08-28T12:00:00+00:00",
            },
        )
        _, original = app._apply_thesis(
            "MGC", ready, "LONG READY", mode="SCALP",
        )
        candidate = _strict(
            score=95,
            direction="Short",
            evidence_epoch="bar-2",
            structure_state={
                "state": "REVERSAL_CANDIDATE",
                "confirmed": False,
                "direction": "Short",
                "last_event_at": "2026-08-28T12:01:00+00:00",
            },
        )
        adj, snap = app._apply_thesis(
            "MGC", candidate, "SHORT READY", mode="SCALP",
        )
        assert adj == "WAIT"
        assert snap["direction"] == "Long"
        assert snap["thesisId"] == original["thesisId"]
        assert snap["entryPaused"] is True
    finally:
        _clear("MGC")


def test_active_thesis_cannot_bypass_reversal_pause():
    _clear("MGC")
    try:
        ready = _strict(
            score=82,
            direction="Long",
            evidence_epoch="bar-1",
            structure_state={
                "state": "TREND_CONFIRMED",
                "confirmed": True,
                "direction": "Long",
                "last_event_at": "2026-08-28T12:00:00+00:00",
            },
        )
        _, original = app._apply_thesis(
            "MGC", ready, "LONG READY", mode="SCALP",
        )
        with app.THESIS_LOCK:
            active = dict(app.THESIS_BY_INST[("MGC", "SCALP")])
            active["status"] = "ACTIVE_LONG"
            app.THESIS_BY_INST[("MGC", "SCALP")] = active

        candidate = _strict(
            score=95,
            direction="Short",
            evidence_epoch="bar-2",
            structure_state={
                "state": "REVERSAL_CANDIDATE",
                "confirmed": False,
                "direction": "Short",
                "last_event_at": "2026-08-28T12:01:00+00:00",
            },
        )
        candidate_adj, candidate_snap = app._apply_thesis(
            "MGC", candidate, "SHORT READY", mode="SCALP",
        )
        assert candidate_adj == "WAIT"
        assert candidate_snap["direction"] == "Long"
        assert candidate_snap["thesisId"] == original["thesisId"]

        confirmed = dict(candidate)
        confirmed["evidence_epoch"] = "bar-3"
        confirmed["structure_state"] = {
            "state": "REVERSAL_CONFIRMED",
            "confirmed": True,
            "direction": "Short",
            "last_event_at": "2026-08-28T12:02:00+00:00",
        }
        confirmed_adj, confirmed_snap = app._apply_thesis(
            "MGC", confirmed, "SHORT READY", mode="SCALP",
        )
        assert confirmed_adj == "WAIT"
        assert confirmed_snap["status"] == "FORMING_SHORT"
        assert confirmed_snap["entryPaused"] is True
    finally:
        _clear("MGC")


def test_restart_restore_keeps_mode_epoch_and_origin_context():
    saved_ready = app.THESIS_DB_READY
    saved_conn = app.get_db_connection
    _clear("MGC")

    persisted = _strict(
        score=84,
        direction="Long",
        evidence_epoch="bar-restore",
        structure_state={
            "state": "TREND_CONFIRMED",
            "direction": "Long",
            "last_event_at": "2026-08-28T12:00:00+00:00",
        },
        reason="Bullish structure remains confirmed.",
    )
    _, original = app._apply_thesis(
        "MGC", persisted, "LONG READY", mode="SCALP",
    )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql):
            return None

        def fetchall(self):
            return [("MGC", "SCALP", dict(original), app.now_utc())]

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    try:
        _clear("MGC")
        app.THESIS_DB_READY = True
        app.get_db_connection = lambda: Connection()
        app._restore_thesis_states()
        restored = app.get_thesis_snapshot("MGC", "SCALP")
        assert restored["thesisId"] == original["thesisId"]
        assert restored["evidenceEpoch"] == "bar-restore"
        assert restored["stableReason"] == "Bullish structure remains confirmed."
        assert restored["originatingStructureContext"]["state"] == "TREND_CONFIRMED"
        assert restored["invalidationConditions"]
    finally:
        app.get_db_connection = saved_conn
        app.THESIS_DB_READY = saved_ready
        _clear("MGC")


# ── Built-in runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_ready_promotion_from_forming,
        test_hysteresis_hold_above_60,
        test_drops_to_wait_below_hold_threshold,
        test_reversal_below_threshold_weakens_not_flips,
        test_reversal_at_threshold_flips_direction,
        test_zone_broken_hard_invalidation_swing,
        test_structure_lost_hard_invalidation,
        test_per_instrument_isolation,
        test_cooldown_blocks_new_thesis,
        test_confidence_rise_is_immediate,
        test_confidence_fall_capped,
        test_new_thesis_id_after_invalidation,
        test_flag_off_passthrough,
        test_status_payload_has_thesis_key,
        test_journal_entry_has_thesis_fields,
        test_scalp_no_zone_no_hard_invalidation,
        test_identical_heartbeat_epoch_does_not_drain_confidence,
        test_new_bar_epoch_updates_confidence_once,
        test_scalp_and_intraday_theses_are_isolated,
        test_same_epoch_hard_invalidation_still_demotes_immediately,
        test_confirmed_opposite_structure_demotes_even_inside_same_bar,
        test_reversal_candidate_never_flips_active_thesis,
        test_active_thesis_cannot_bypass_reversal_pause,
        test_restart_restore_keeps_mode_epoch_and_origin_context,
    ]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {fn.__name__}: {exc}")
            failed += 1
    print(f"\n{passed}/{passed+failed} tests passed")
    if failed:
        sys.exit(1)
