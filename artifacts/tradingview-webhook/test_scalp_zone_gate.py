"""SCALP soft-zone gate parity tests.

Proves the SCALP-only zone demotion (GATE_REQUIRE_ZONE False) while guaranteeing
SWING stays byte-for-byte:

  • SCALP fires a structure+VWAP+Edge READY with NO valid trade-side zone (the live
    PROD bug: bearish trends scored Edge 60-65 but WAITed forever on "zone valid").
  • SWING still hard-gates zone (zone_valid remains a required gate on identical input).
  • The requirement-5 diagnostic aliases (zoneState / zoneValid / bosState /
    chochState / vwapState / edgeScore / blockedBy) are present and correct, and the
    zone STATE machine reports Fresh/Tested/Consumed.
  • A CONSUMED (mitigated-near) zone does NOT block the SCALP gate.

Pure-function: drives evaluate_strict_setup() directly with a synthetic alert
history (importing app.py is side-effect-free — schedulers only start under
__main__). Volatility + levels are passed explicitly; no network/threads/DB.

Runnable two ways:
  • pytest test_scalp_zone_gate.py
  • python3 test_scalp_zone_gate.py   (built-in runner, no pytest needed)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402


def _vol(status="ok", atr=2.0, regime="NORMAL", label="Normal", ratio=1.0):
    return {"status": status, "atr_pts": atr, "regime": regime,
            "label": label, "ratio": ratio}


def _short_setup_alerts():
    """Bearish setup: BOS+CHOCH supply structure + a bearish sweep, all recent and
    instrument-scoped to MGC. Edge = BOS20 + CHOCH20 + VWAP15 + Sweep15 = 70 (>= the
    SCALP full-READY floor of 60, < the SWING floor of 80). No mitigation flag is set
    so the strict zone gate (mitigated AND reaction) is INVALID."""
    ts = app.now_utc().isoformat()
    return [
        {"alert_type": "CHOCH SUPPLY",      "instrument": "MGC", "ticker": "MGC", "timestamp": ts},
        {"alert_type": "BOS SUPPLY",        "instrument": "MGC", "ticker": "MGC", "timestamp": ts},
        {"alert_type": "MGC BEARISH SWEEP", "instrument": "MGC", "ticker": "MGC", "timestamp": ts},
    ]


def _eval_short(mode, mitigated_near=False):
    saved_mode = app.TRADING_MODE
    saved_flag = dict(app.MITIGATED_FLAG_BY_TICKER)
    saved_rvol = dict(app.RVOL_BY_TICKER)
    saved_fn = app.is_near_mitigated_zone
    app.TRADING_MODE = mode
    app.MITIGATED_FLAG_BY_TICKER.clear()
    # The state-aware structure cycle allocates one active structure component,
    # not additive BOS+CHOCH points. Supply explicit fresh RVOL confirmation so
    # this fixture reaches the threshold and continues to isolate zone behavior.
    app.RVOL_BY_TICKER["MGC"] = {"value": 2.0}
    if mitigated_near:
        app.MITIGATED_FLAG_BY_TICKER["MGC"] = True
        app.is_near_mitigated_zone = lambda price, ticker: (True, None)
    try:
        return app.evaluate_strict_setup(
            current_price=1990.0, ticker="MGC", vwap=2000.0, vwap_status="ok",
            nearest_supply=2010.0, nearest_demand=1980.0,
            bullish=0, bearish=2, confidence=50,
            alert_history=_short_setup_alerts(),
            volatility=_vol(), session=None, cooldown_active=False)
    finally:
        app.TRADING_MODE = saved_mode
        app.is_near_mitigated_zone = saved_fn
        app.MITIGATED_FLAG_BY_TICKER.clear()
        app.MITIGATED_FLAG_BY_TICKER.update(saved_flag)
        app.RVOL_BY_TICKER.clear()
        app.RVOL_BY_TICKER.update(saved_rvol)


# ── Config flip ─────────────────────────────────────────────────────────────

def test_cfg_zone_gate_scalp_off_swing_on():
    saved = app.TRADING_MODE
    try:
        app.TRADING_MODE = "SCALP"
        assert app.cfg("GATE_REQUIRE_ZONE") is False
        app.TRADING_MODE = "SWING"
        assert app.cfg("GATE_REQUIRE_ZONE") is True
    finally:
        app.TRADING_MODE = saved


# ── Core money-path behaviour ───────────────────────────────────────────────

def test_scalp_fires_short_without_valid_zone():
    short = _eval_short("SCALP")["directions"]["Short"]
    gd = short["gate_debug"]
    assert gd["zone_valid"] is False             # strict zone invalid (no mitigation)
    assert "zone_valid" not in short["missing"]  # zone DEMOTED — not a failed gate
    assert gd["edge_score"] >= 60                # BOS+CHOCH+VWAP+Sweep = 70
    assert short["ready"] is True                # READY despite an invalid zone


def test_swing_still_requires_zone():
    short = _eval_short("SWING")["directions"]["Short"]
    gd = short["gate_debug"]
    assert gd["zone_valid"] is False
    assert "zone_valid" in short["missing"]      # zone STILL a hard gate in SWING
    assert short["ready"] is False               # WAIT — zone required


# ── Requirement-5 diagnostic aliases + zone STATE machine ───────────────────

def test_scalp_diagnostic_aliases_present_and_correct():
    gd = _eval_short("SCALP")["directions"]["Short"]["gate_debug"]
    for k in ("zoneState", "zoneValid", "bosState", "chochState",
              "vwapState", "edgeScore", "blockedBy"):
        assert k in gd, f"missing diagnostic alias: {k}"
    assert gd["bosState"] is True
    assert gd["chochState"] is True
    assert gd["vwapState"] is True               # price 1990 < VWAP 2000
    assert isinstance(gd["edgeScore"], int) and gd["edgeScore"] == gd["edge_score"]
    assert isinstance(gd["blockedBy"], list) and gd["blockedBy"] == gd["failed_conditions"]
    # nearest_supply present + un-consumed -> Fresh (1% away > SCALP NEAR_PCT 0.6%)
    assert gd["zoneState"] == "Fresh"
    assert gd["zoneValid"] is True               # SCALP zoneValid = present & un-consumed


def test_swing_zonevalid_mirrors_strict_gate():
    gd = _eval_short("SWING")["directions"]["Short"]["gate_debug"]
    # SWING zoneValid mirrors the strict mitigated+reaction gate (False here), while
    # the state machine is still computed for display.
    assert gd["zoneValid"] is False
    assert gd["zoneState"] == "Fresh"


def test_consumed_zone_state_and_scalp_not_blocked():
    short = _eval_short("SCALP", mitigated_near=True)["directions"]["Short"]
    gd = short["gate_debug"]
    assert gd["zoneState"] == "Consumed"         # mitigated-near -> consumed
    assert gd["zoneValid"] is False              # consumed -> soft-invalid
    assert "zone_valid" not in short["missing"]  # consumed zone must NOT block SCALP
    assert short["ready"] is True


# ── Setup-state invalidation (display-only) is mode-aware ───────────────────

def _setup_state(mode, a, dispatched_ready=True, is_duplicate=False):
    saved_mode = app.TRADING_MODE
    saved_state = dict(app.SETUP_STATE)
    app.TRADING_MODE = mode
    app.SETUP_STATE.clear()
    try:
        app._update_setup_state("MGC", a, dispatched_ready=dispatched_ready,
                                is_duplicate=is_duplicate)
        return dict(app.SETUP_STATE.get("MGC") or {})
    finally:
        app.TRADING_MODE = saved_mode
        app.SETUP_STATE.clear()
        app.SETUP_STATE.update(saved_state)


def test_setup_state_scalp_consumed_zone_not_invalidated():
    # SCALP: a freshly-dispatched READY whose zone is consumed must stay ACTIVE,
    # never flip to INVALIDATED (zone demoted to a non-blocking signal).
    a = {"verdict": "SHORT READY", "zone_mitigated_near": True, "gate_candidate": "Short"}
    st = _setup_state("SCALP", a, dispatched_ready=True)
    assert st.get("state") == "ACTIVE"
    assert st.get("direction") == "Short"


def test_setup_state_swing_consumed_zone_invalidates():
    # SWING parity: a consumed zone still INVALIDATES the live setup state.
    a = {"verdict": "SHORT READY", "zone_mitigated_near": True, "gate_candidate": "Short"}
    st = _setup_state("SWING", a, dispatched_ready=True)
    assert st.get("state") == "INVALIDATED"


# ── Webhook money-path branch decision (the live leak the architect caught) ──
#
# The consumed-zone short-circuit in _process_webhook_alert() must fire in SWING
# (zone hard-gate) and be BYPASSED in SCALP so a demoted-zone READY still reaches
# the EARLY/journal/READY-card/auto-trade dispatch path. We monkeypatch
# full_analysis to feed a controlled "actionable SHORT + consumed zone" verdict
# and raise a sentinel at the first un-wrapped fall-through call (_setup_risk_mult)
# so the test never touches Discord/DB/broker — it only proves WHICH branch ran.

class _Sentinel(Exception):
    pass


def _run_webhook(mode):
    saved_mode = app.TRADING_MODE
    saved_state = dict(app.SETUP_STATE)
    app.TRADING_MODE = mode
    flags = {"skip": False, "fellthrough": False}
    a = {"verdict": "SHORT READY", "zone_mitigated_near": True,
         "mitigated_zone_price": 2010.0, "structure_class": None,
         "trade_plan": {"trade_plan": True}}
    orig = {}

    def patch(name, fn):
        orig[name] = getattr(app, name)
        setattr(app, name, fn)

    def _raise(*p, **k):
        raise _Sentinel()

    patch("full_analysis", lambda **kw: dict(a))
    patch("send_zone_mitigated_message",
          lambda *p, **k: flags.__setitem__("skip", True))
    patch("_record_diagnostic", lambda *p, **k: None)
    patch("_record_eval_metrics", lambda *p, **k: None)
    patch("_update_setup_state", lambda *p, **k: None)
    patch("_maybe_dispatch_early_alert",
          lambda *p, **k: flags.__setitem__("fellthrough", True))
    patch("_maybe_send_setup_building_alert", lambda *p, **k: None)
    patch("_setup_risk_mult", _raise)
    try:
        try:
            app._process_webhook_alert(
                record={"ticker": "MGC", "instrument": "MGC"},
                parsed_price=1990.0, resolved_inst="MGC",
                normalized="MGC BEARISH SWEEP", account_size=50000.0,
                risk_pct=1.0, profile_name="default",
                webhook_received_at=app.now_utc())
        except _Sentinel:
            pass
    finally:
        app.TRADING_MODE = saved_mode
        for k, v in orig.items():
            setattr(app, k, v)
        app.SETUP_STATE.clear()
        app.SETUP_STATE.update(saved_state)
    return flags


def test_webhook_scalp_consumed_zone_falls_through_to_dispatch():
    flags = _run_webhook("SCALP")
    assert flags["skip"] is False          # consumed-zone short-circuit NOT taken
    assert flags["fellthrough"] is True    # reaches the EARLY / dispatch path


def test_webhook_swing_consumed_zone_short_circuits():
    flags = _run_webhook("SWING")
    assert flags["skip"] is True           # SWING still skips on a consumed zone
    assert flags["fellthrough"] is False


# ── EARLY teaser branch decision (display-only; zone demoted in SCALP) ───────
#
# _maybe_dispatch_early_alert() skips the intrabar ⚡EARLY teaser when the zone is
# broken. That broken-zone skip is a ZONE HARD-GATE behaviour: it must HOLD in SWING
# and be BYPASSED in SCALP (zone demoted) so the teaser still fires. We feed a single-
# direction active setup (one side has sweep+structure) with a broken zone, then raise
# a sentinel at the first call AFTER the broken-zone check (_early_alert_url) to prove
# WHICH branch ran — no Discord/DB is touched. An ACTIVE_TRADE still suppresses the
# teaser in every mode, so it is held None here.

def _run_early(mode):
    saved = {}

    def patch(name, fn):
        saved[name] = getattr(app, name)
        setattr(app, name, fn)

    def _raise(*p, **k):
        raise _Sentinel()

    saved_mode = app.TRADING_MODE
    saved_enabled = app.EARLY_ALERTS_ENABLED
    saved_active = dict(app.ACTIVE_TRADES_BY_INST)
    saved_eet = dict(app.EARLY_EVENT_TIMES)
    saved_anchor = dict(app.LAST_EARLY_ANCHOR)
    saved_at = dict(app.LAST_EARLY_AT)
    app.TRADING_MODE = mode
    app.EARLY_ALERTS_ENABLED = True
    app.ACTIVE_TRADES_BY_INST.pop("MGC", None)
    app.LAST_EARLY_ANCHOR.clear()
    app.LAST_EARLY_AT.clear()
    ts = app.now_utc()
    times = {
        "Short": {"sweep": ts, "choch": ts, "displacement": ts,
                  "structure": ts, "event_start": ts},
        "Long":  {"sweep": None, "choch": None, "displacement": None,
                  "structure": None, "event_start": None},
    }
    patch("_early_event_times", lambda inst, hist: times)
    patch("_early_alert_url", _raise)   # first call AFTER the broken-zone check
    a = {"verdict": "SHORT WAIT", "zone_broken_active": True,
         "market_open": True, "active_ticker": "MGC"}
    record = {"ticker": "MGC", "instrument": "MGC"}
    reached_dispatch = False
    try:
        try:
            app._maybe_dispatch_early_alert(a, record)
        except _Sentinel:
            reached_dispatch = True
    finally:
        app.TRADING_MODE = saved_mode
        app.EARLY_ALERTS_ENABLED = saved_enabled
        app.ACTIVE_TRADES_BY_INST.clear()
        app.ACTIVE_TRADES_BY_INST.update(saved_active)
        for k, v in saved.items():
            setattr(app, k, v)
        app.EARLY_EVENT_TIMES.clear()
        app.EARLY_EVENT_TIMES.update(saved_eet)
        app.LAST_EARLY_ANCHOR.clear()
        app.LAST_EARLY_ANCHOR.update(saved_anchor)
        app.LAST_EARLY_AT.clear()
        app.LAST_EARLY_AT.update(saved_at)
    return reached_dispatch


def test_early_scalp_broken_zone_does_not_suppress_teaser():
    # SCALP: zone demoted -> a broken zone must NOT mute the EARLY teaser; the
    # function proceeds past the zone check toward dispatch.
    assert _run_early("SCALP") is True


def test_early_swing_broken_zone_suppresses_teaser():
    # SWING parity: a broken zone still suppresses the EARLY teaser.
    assert _run_early("SWING") is False


# ── built-in runner (no pytest dependency) ──────────────────────────────────

if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed of {len(tests)}")
    sys.exit(1 if failed else 0)
