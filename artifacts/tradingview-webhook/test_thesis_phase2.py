"""Phase 2 thesis tests — Timeline, Discord dedup, DB, routes, trade card.

Drives Phase 2 helpers directly — pure-function, no network/threads/DB unless
explicitly testing the fail-open path (where we set THESIS_DB_READY=False or
_THESIS_DISCORD_ALERTS_ENABLED=False to verify the guard works).

Covers 17 cases:
  P2-01  Timeline records a NEUTRAL→FORMING_LONG transition event
  P2-02  Timeline records event when confidence jumps >= 5 pts (no status change)
  P2-03  Timeline SKIPS event when confidence delta < 5 and status unchanged
  P2-04  _should_notify_thesis returns True on first FORMING transition
  P2-05  _should_notify_thesis dedup — returns False for same sig
  P2-06  _should_notify_thesis detects confirmed direction flip (new thesisId)
  P2-07  _should_notify_thesis returns False for non-notifiable transition (READY stay)
  P2-08  _maybe_send_thesis_notification fail-open when Discord disabled
  P2-09  _thesis_post_update fail-open with a bad/empty snap
  P2-10  /thesis route returns 200 JSON with "thesis" key
  P2-11  /thesis/<inst>/history returns events list for the instrument
  P2-12  DB persist path skips when THESIS_DB_READY=False (no crash)
  P2-13  _restore_thesis_states is a no-op when THESIS_DB_READY=False
  P2-14  _ms_to_human converts ms correctly (seconds / minutes / hours)
  P2-15  _ev_label maps known codes and falls back for unknown codes
  P2-16  THESIS_DISCORD_ALERTS_ENABLED=False suppresses notification
  P2-17  _apply_thesis still returns correct adj_verdict after Phase 2 wiring

Runnable two ways:
  pytest test_thesis_phase2.py
  python3 test_thesis_phase2.py
"""

import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────

def _clear(inst="MGC"):
    with app.THESIS_LOCK:
        for key in list(app.THESIS_BY_INST):
            if key == inst or str(key).startswith(inst + "|"):
                app.THESIS_BY_INST.pop(key, None)
        for key in list(app.THESIS_TIMELINE_BY_INST):
            if key == inst or str(key).startswith(inst + "|"):
                app.THESIS_TIMELINE_BY_INST.pop(key, None)
        app.THESIS_NOTIF_LAST_BY_INST.pop(inst, None)


def _strict(score=80, direction="Long", zone_valid=True, vwap_ok=True,
            struct_ok=True, sweep_ok=True, vol_ok=True,
            session_ok=False, missing=None, zone_broken=False):
    gd = {
        "zone_valid":          zone_valid,
        "vwap_confirmed":      vwap_ok,
        "structure_confirmed": struct_ok,
        "sweep_confirmed":     sweep_ok,
        "volume_confirmed":    vol_ok,
        "session":             session_ok,
    }
    return {
        "score":             score,
        "direction":         direction,
        "candidate":         direction,
        "gate_debug":        gd,
        "missing":           missing or [],
        "zone_broken_active": zone_broken,
    }


def _run(verdict, score=80, direction="Long", inst="MGC", **kw):
    s = _strict(score=score, direction=direction, **kw)
    return app._apply_thesis(inst, s, verdict)


def _snap(status, direction="Long", confidence=75, thesis_id="th_testid",
          evidence_for=None, evidence_against=None, age_ms=5000):
    return {
        "thesisId":        thesis_id,
        "status":          status,
        "direction":       direction,
        "confidence":      confidence,
        "evidenceFor":     evidence_for or ["STRUCTURE_BULLISH", "CVD_ALIGNED"],
        "evidenceAgainst": evidence_against or [],
        "thesisAgeMs":     age_ms,
        "lastUpdatedAt":   app.now_utc().isoformat(),
        "reasonCodes":     ["STRUCTURE_BULLISH"],
    }


# ── P2-01: Timeline records NEUTRAL→FORMING transition ───────────────────────

def test_p2_01_timeline_records_forming_transition():
    saved_mode = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        _clear()
        # First call: NEUTRAL → FORMING
        _run("LONG READY", score=65, sweep_ok=False)
        with app.THESIS_LOCK:
            events = list(app.THESIS_TIMELINE_BY_INST.get("MGC|SCALP") or [])
        assert len(events) >= 1, "Expected at least one timeline event"
        ev = events[0]
        assert ev["newStatus"] != "NEUTRAL", f"Expected non-NEUTRAL, got {ev['newStatus']}"
    finally:
        app.TRADING_MODE = saved_mode
        _clear()


# ── P2-02: Timeline records event on confidence jump >= 5 ────────────────────

def test_p2_02_timeline_records_confidence_jump():
    try:
        prev = _snap("FORMING_LONG", confidence=50)
        new  = _snap("FORMING_LONG", confidence=58)
        # delta = 8 >= 5 → should record
        with app.THESIS_LOCK:
            from collections import deque
            app.THESIS_TIMELINE_BY_INST["MGC|SCALP"] = deque(maxlen=250)
        app._record_thesis_event("MGC", prev, new)
        with app.THESIS_LOCK:
            events = list(app.THESIS_TIMELINE_BY_INST["MGC|SCALP"])
        assert len(events) == 1
        assert events[0]["prevConfidence"] == 50
        assert events[0]["newConfidence"] == 58
    finally:
        _clear()


# ── P2-03: Timeline skips event on trivial change ────────────────────────────

def test_p2_03_post_update_skips_trivial_change():
    """_thesis_post_update with delta < 5 and same status → no new event."""
    try:
        with app.THESIS_LOCK:
            from collections import deque
            app.THESIS_TIMELINE_BY_INST["MGC|SCALP"] = deque(maxlen=250)
        prev = _snap("FORMING_LONG", confidence=65)
        new  = _snap("FORMING_LONG", confidence=67)   # delta = 2 < 5
        app._thesis_post_update("MGC", prev, new)
        with app.THESIS_LOCK:
            events = list(app.THESIS_TIMELINE_BY_INST.get("MGC|SCALP") or [])
        assert len(events) == 0, f"Expected 0 events for trivial change, got {len(events)}"
    finally:
        _clear()


# ── P2-04: Notification on first FORMING transition ──────────────────────────

def test_p2_04_notify_on_first_forming():
    """_should_notify_thesis returns True for NEUTRAL → FORMING_LONG."""
    _clear()
    prev = _snap("NEUTRAL", confidence=0)
    new  = _snap("FORMING_LONG", confidence=60)
    assert app._should_notify_thesis("MGC", prev, new) is True
    _clear()


# ── P2-05: Dedup — same sig returns False ────────────────────────────────────

def test_p2_05_notify_dedup():
    """Second call for same (thesisId, prevStatus, newStatus) → False."""
    _clear()
    prev = _snap("NEUTRAL", confidence=0, thesis_id="th_abc123")
    new  = _snap("FORMING_LONG", confidence=60, thesis_id="th_abc123")
    # Stamp the dedup entry as if we already sent this notification
    sig = "th_abc123|NEUTRAL|FORMING_LONG"
    app.THESIS_NOTIF_LAST_BY_INST["MGC"] = {"sig": sig, "ts": app.now_utc().isoformat()}
    result = app._should_notify_thesis("MGC", prev, new)
    assert result is False, "Dedup should suppress repeated notification"
    _clear()


# ── P2-06: Notify on confirmed direction flip ─────────────────────────────────

def test_p2_06_notify_on_direction_flip():
    """New thesisId + opposite direction → notification even outside normal table."""
    _clear()
    prev = _snap("READY_LONG",   direction="Long",  thesis_id="th_old111")
    new  = _snap("FORMING_SHORT", direction="Short", thesis_id="th_new222")
    result = app._should_notify_thesis("MGC", prev, new)
    assert result is True, "Direction flip with new thesisId should trigger notify"
    _clear()


# ── P2-07: No notify for non-listed transition ────────────────────────────────

def test_p2_07_no_notify_non_listed_transition():
    """FORMING_LONG → FORMING_LONG with same thesisId should NOT notify."""
    _clear()
    prev = _snap("FORMING_LONG", thesis_id="th_same", confidence=60)
    new  = _snap("FORMING_LONG", thesis_id="th_same", confidence=64)
    result = app._should_notify_thesis("MGC", prev, new)
    assert result is False
    _clear()


# ── P2-08: _maybe_send_thesis_notification fail-open ─────────────────────────

def test_p2_08_maybe_notify_fail_open():
    """Disabling Discord live should NOT crash — returns silently."""
    saved = app.DISCORD_LIVE_ENABLED
    try:
        app.DISCORD_LIVE_ENABLED = False
        _clear()
        prev = _snap("NEUTRAL", confidence=0)
        new  = _snap("FORMING_LONG", confidence=65)
        # Must not raise
        app._maybe_send_thesis_notification("MGC", prev, new)
    finally:
        app.DISCORD_LIVE_ENABLED = saved
        _clear()


# ── P2-09: _thesis_post_update fail-open on bad snap ─────────────────────────

def test_p2_09_post_update_fail_open():
    """Empty/None snap must not crash _thesis_post_update."""
    # Should silently return, no exception
    app._thesis_post_update("MGC", {}, {})
    app._thesis_post_update("MGC", None or {}, None or {})


# ── P2-10: /thesis route returns all-instruments JSON ─────────────────────────

def test_p2_10_thesis_route_all_instruments():
    """GET /thesis → 200 JSON with 'ok' and 'thesis' keys."""
    with app.app.test_client() as client:
        resp = client.get("/thesis")
    assert resp.status_code == 200, f"/thesis returned {resp.status_code}"
    data = resp.get_json()
    assert data.get("ok") is True
    assert "thesis" in data
    assert "instruments" in data
    for inst in data["instruments"]:
        assert inst in data["thesis"], f"{inst} missing from thesis dict"


# ── P2-11: /thesis/<inst>/history route ──────────────────────────────────────

def test_p2_11_thesis_history_route():
    """GET /thesis/MGC/history → 200 with 'events' list."""
    # Seed a synthetic event
    from collections import deque
    ev = {
        "ts": app.now_utc().isoformat(), "thesisId": "th_test",
        "direction": "Long", "prevStatus": "NEUTRAL", "newStatus": "FORMING_LONG",
        "prevConfidence": 0, "newConfidence": 60,
        "reasonCodes": [], "primaryReason": "", "invalidationReason": None,
    }
    with app.THESIS_LOCK:
        app.THESIS_TIMELINE_BY_INST["MGC|SCALP"] = deque([ev], maxlen=250)

    with app.app.test_client() as client:
        resp = client.get("/thesis/MGC/history")
    assert resp.status_code == 200, f"/thesis/MGC/history returned {resp.status_code}"
    data = resp.get_json()
    assert data.get("ok") is True
    assert isinstance(data.get("events"), list)
    assert len(data["events"]) >= 1
    assert data["events"][0]["newStatus"] == "FORMING_LONG"
    _clear()


# ── P2-12: DB persist skips when THESIS_DB_READY=False ───────────────────────

def test_p2_12_persist_skips_when_db_not_ready():
    """_persist_thesis_state must be a no-op and not crash when DB not ready."""
    saved = app.THESIS_DB_READY
    try:
        app.THESIS_DB_READY = False
        snap = _snap("FORMING_LONG")
        # Must not raise even without a real DB
        app._persist_thesis_state("MGC", snap)
    finally:
        app.THESIS_DB_READY = saved


# ── P2-13: Restore is no-op when THESIS_DB_READY=False ───────────────────────

def test_p2_13_restore_no_op_when_db_not_ready():
    """_restore_thesis_states does nothing when THESIS_DB_READY=False."""
    saved = app.THESIS_DB_READY
    try:
        app.THESIS_DB_READY = False
        before = dict(app.THESIS_BY_INST)
        app._restore_thesis_states()
        after = dict(app.THESIS_BY_INST)
        assert before == after, "Restore should not change THESIS_BY_INST when DB not ready"
    finally:
        app.THESIS_DB_READY = saved


# ── P2-14: _ms_to_human ──────────────────────────────────────────────────────

def test_p2_14_ms_to_human():
    assert app._ms_to_human(0) == "—"
    assert app._ms_to_human(None) == "—"
    assert app._ms_to_human(-1000) == "—"
    assert app._ms_to_human(5000) == "5s"
    assert app._ms_to_human(90000) == "1m 30s"
    assert app._ms_to_human(3600000) == "1h 0m"
    assert app._ms_to_human(5400000) == "1h 30m"


# ── P2-15: _ev_label ─────────────────────────────────────────────────────────

def test_p2_15_ev_label():
    assert app._ev_label("STRUCTURE_BULLISH") == "Bullish structure"
    assert app._ev_label("CVD_ALIGNED") == "CVD confirms direction"
    assert app._ev_label("SWEEP_CONFIRMED") == "Liquidity sweep confirmed"
    # Unknown code should fall back to title-cased version
    unknown = app._ev_label("MY_CUSTOM_CODE")
    assert "Custom" in unknown or "custom" in unknown.lower(), \
        f"Unexpected fallback: {unknown!r}"


# ── P2-16: Discord disabled flag ─────────────────────────────────────────────

def test_p2_16_discord_disabled_suppresses_notification():
    """When _THESIS_DISCORD_ALERTS_ENABLED=False, _maybe_send never fires."""
    saved = app._THESIS_DISCORD_ALERTS_ENABLED
    enqueued = []
    saved_enqueue = app._enqueue_slow
    try:
        app._THESIS_DISCORD_ALERTS_ENABLED = False
        app._enqueue_slow = lambda fn: enqueued.append(fn)
        _clear()
        prev = _snap("NEUTRAL", confidence=0)
        new  = _snap("FORMING_LONG", confidence=65)
        app._maybe_send_thesis_notification("MGC", prev, new)
        assert len(enqueued) == 0, "No enqueue should happen when Discord alerts OFF"
    finally:
        app._THESIS_DISCORD_ALERTS_ENABLED = saved
        app._enqueue_slow = saved_enqueue
        _clear()


# ── P2-17: _apply_thesis still correct after Phase 2 wiring ─────────────────

def test_p2_17_apply_thesis_correct_after_phase2():
    """Phase 2 additions (post-update hook) must not affect the returned verdict."""
    saved_mode = app.TRADING_MODE
    # Disable Discord + DB to keep test pure
    saved_discord = app._THESIS_DISCORD_ALERTS_ENABLED
    saved_db      = app.THESIS_DB_READY
    try:
        app.TRADING_MODE = "SCALP"
        app._THESIS_DISCORD_ALERTS_ENABLED = False
        app.THESIS_DB_READY = False
        _clear()
        adj, t = _run("LONG READY", score=80)
        assert "READY" in adj, f"Expected READY, got {adj!r}"
        assert t["status"] == "CONFIRMED"
        assert t["confidence"] == 80
    finally:
        app.TRADING_MODE = saved_mode
        app._THESIS_DISCORD_ALERTS_ENABLED = saved_discord
        app.THESIS_DB_READY = saved_db
        _clear()


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys

    tests = [
        test_p2_01_timeline_records_forming_transition,
        test_p2_02_timeline_records_confidence_jump,
        test_p2_03_post_update_skips_trivial_change,
        test_p2_04_notify_on_first_forming,
        test_p2_05_notify_dedup,
        test_p2_06_notify_on_direction_flip,
        test_p2_07_no_notify_non_listed_transition,
        test_p2_08_maybe_notify_fail_open,
        test_p2_09_post_update_fail_open,
        test_p2_10_thesis_route_all_instruments,
        test_p2_11_thesis_history_route,
        test_p2_12_persist_skips_when_db_not_ready,
        test_p2_13_restore_no_op_when_db_not_ready,
        test_p2_14_ms_to_human,
        test_p2_15_ev_label,
        test_p2_16_discord_disabled_suppresses_notification,
        test_p2_17_apply_thesis_correct_after_phase2,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    _sys.exit(0 if failed == 0 else 1)
