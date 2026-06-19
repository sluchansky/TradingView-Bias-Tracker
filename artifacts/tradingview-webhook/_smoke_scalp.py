"""Smoke test for the SCALP-only gate redesign. Imports app (server boot guarded by
__main__) and drives evaluate_strict_setup + helpers with crafted in-memory state.
Verifies: SCALP READY>=60 / Strong>=75 / SETUP BUILDING 50-59 / CVD soft;
SWING byte-unchanged (cvd hard, no soft mods, no SETUP BUILDING, READY>=80);
gate==display score parity; SETUP BUILDING never actionable."""
import sys
import app

FAILS = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)

now = app.now_utc().isoformat()

def mk_history(include_sweep=True):
    h = [
        {"alert_type": "BOS DEMAND",   "instrument": "MGC", "ticker": "MGC", "timestamp": now},
        {"alert_type": "CHOCH DEMAND", "instrument": "MGC", "ticker": "MGC", "timestamp": now},
        {"alert_type": "MGC BULLISH CONFIRMATION", "instrument": "MGC", "ticker": "MGC", "timestamp": now},
    ]
    if include_sweep:
        h.append({"alert_type": "MGC BULLISH SWEEP", "instrument": "MGC", "ticker": "MGC", "timestamp": now})
    return h

def reset_state():
    app.ZONE_MITIGATED_FLAG = True
    app.MITIGATED_PRICES = [{"price": 99.96, "ts": now}]
    app.CVD_BY_TICKER = {}
    app.RVOL_BY_TICKER = {}
    app.VOLUME_SPIKE_BY_TICKER = {}

PRICE, VWAP, DEMAND = 100.0, 99.95, 99.96

def run(mode, include_sweep=True, session_pref=False, cvd=None, cooldown=False):
    app.TRADING_MODE = mode
    reset_state()
    if cvd:
        app.CVD_BY_TICKER = {"MGC": {"state": cvd}}
    return app.evaluate_strict_setup(
        PRICE, "MGC", VWAP, "ok",
        None, DEMAND,
        5, 0, 75, mk_history(include_sweep),
        volatility={}, session={"preferred": session_pref},
        cooldown_active=cooldown,
    )

SIG_KEYS = ("bos_confirmed", "choch_confirmed", "vwap_confirmed",
            "liquidity_sweep", "volume_confirmed", "cvd_confirmed", "preferred_session")

def parity(res):
    gd = res["gate_debug"]
    sig = {k: bool(gd.get(k)) for k in SIG_KEYS}
    # gate_debug exposes the session flag as "session_pref"; the scorer signal key
    # is "preferred_session" (matches EDGE_COMPONENTS). Map it for reconstruction.
    sig["preferred_session"] = bool(gd.get("session_pref"))
    recomputed, _ = app.compute_trade_edge_components(sig, gd.get("edge_modifiers"))
    return recomputed == gd["edge_score"]

# ── is_actionable ────────────────────────────────────────────────────────────
check("is_actionable(SETUP BUILDING) == False", not app.is_actionable("SETUP BUILDING"))
check("is_actionable(LONG READY) == True", app.is_actionable("LONG READY"))
check("SETUP BUILDING not in FULL/EARLY verdict sets",
      "SETUP BUILDING" not in app.FULL_READY_VERDICTS
      and "SETUP BUILDING" not in app.EARLY_READY_VERDICTS)

# ── cfg knobs ────────────────────────────────────────────────────────────────
app.TRADING_MODE = "SCALP"
check("SCALP READY==60", app.cfg("EDGE_READY_THRESHOLD") == 60)
check("SCALP FULL_READY==60", app.cfg("EDGE_FULL_READY_THRESHOLD") == 60)
check("SCALP STRONG==75", app.cfg("EDGE_STRONG_THRESHOLD") == 75)
check("SCALP SETUP_BUILDING==50", app.cfg("EDGE_SETUP_BUILDING_THRESHOLD") == 50)
check("SCALP soft modifiers ON", app.cfg("GATE_SOFT_MODIFIERS") is True)
check("SCALP CVD hard OFF", app.cfg("GATE_CVD_HARD") is False)
check("SCALP zone/vwap/structure hard ON",
      app.cfg("GATE_REQUIRE_ZONE") and app.cfg("GATE_REQUIRE_VWAP") and app.cfg("GATE_REQUIRE_STRUCTURE"))
app.TRADING_MODE = "SWING"
check("SWING STRONG==80", app.cfg("EDGE_STRONG_THRESHOLD") == 80)
check("SWING SETUP_BUILDING is None", app.cfg("EDGE_SETUP_BUILDING_THRESHOLD") is None)
check("SWING soft modifiers OFF", app.cfg("GATE_SOFT_MODIFIERS") is False)
check("SWING CVD hard ON", app.cfg("GATE_CVD_HARD") is True)

# ── compute_trade_edge_components: modifiers + clamp ─────────────────────────
s_all = {k: True for k in SIG_KEYS}
base, _ = app.compute_trade_edge_components(s_all)
check("scorer full base == 110 (clamped max)", base == app.EDGE_SCORE_MAX == 110)
s_part = {"bos_confirmed": True, "choch_confirmed": True, "vwap_confirmed": True, "liquidity_sweep": True}
sc, _ = app.compute_trade_edge_components(s_part)
check("scorer BOS+CHOCH+VWAP+Sweep == 70", sc == 70)
sc_m, _ = app.compute_trade_edge_components(s_part, [{"label": "CVD conflict", "points": -10}])
check("scorer applies -10 modifier (70->60)", sc_m == 60)
sc_clamp, _ = app.compute_trade_edge_components({"bos_confirmed": True}, [{"label": "x", "points": -50}])
check("scorer clamps negative to 0", sc_clamp == 0)

# ── SCALP behavioral bands ───────────────────────────────────────────────────
r = run("SCALP")  # base 70
check("SCALP edge==70 (BOS+CHOCH+VWAP+Sweep)", r["gate_debug"]["edge_score"] == 70)
check("SCALP READY -> Possible Trade @70", r["label"] == "Possible Trade" and r["direction"] == "Long")
check("SCALP parity @70", parity(r))

r = run("SCALP", session_pref=True)  # 70+10=80
check("SCALP edge==80 with session", r["gate_debug"]["edge_score"] == 80)
check("SCALP Strong Trade @>=75", r["label"] == "Strong Trade" and r["direction"] == "Long")
check("SCALP parity @80", parity(r))

r = run("SCALP", include_sweep=False)  # 20+20+15=55, reaction via confirmation
check("SCALP edge==55 (no sweep)", r["gate_debug"]["edge_score"] == 55)
check("SCALP SETUP BUILDING @55", r["label"] == "Setup Building" and r["direction"] is None)
check("SCALP SETUP BUILDING has candidate Long", r.get("candidate") == "Long")
check("SCALP parity @55", parity(r))

r = run("SCALP", cvd="bearish")  # 70 - 10 (cvd soft) = 60
check("SCALP CVD soft: edge 70->60", r["gate_debug"]["edge_score"] == 60)
check("SCALP CVD soft still READY (not blocked)", r["label"] in ("Possible Trade", "Strong Trade") and r["direction"] == "Long")
mods = [m["label"] for m in (r["gate_debug"].get("edge_modifiers") or [])]
check("SCALP CVD conflict is a modifier, not a failed gate", "CVD conflict" in mods and "cvd_conflict" not in (r.get("missing") or []))
check("SCALP parity with CVD modifier", parity(r))

r = run("SCALP", cooldown=True)  # 70 - 5 = 65
check("SCALP cooldown soft: edge 70->65", r["gate_debug"]["edge_score"] == 65)
check("SCALP cooldown still READY", r["label"] in ("Possible Trade", "Strong Trade"))
mods = [m["label"] for m in (r["gate_debug"].get("edge_modifiers") or [])]
check("SCALP cooldown is a modifier", any("Cooldown" in m for m in mods))

# ── SWING unchanged ──────────────────────────────────────────────────────────
r = run("SWING")  # base 70, no soft mods
check("SWING edge==70 (pure sum, no mods)", r["gate_debug"]["edge_score"] == 70)
check("SWING no edge_modifiers (SWING off)", not (r["gate_debug"].get("edge_modifiers") or []))
check("SWING @70 < 80 -> WAIT (not READY)", r["label"] == "WAIT")

r = run("SWING", session_pref=True)  # 80
check("SWING edge==80 -> READY Strong Trade", r["gate_debug"]["edge_score"] == 80 and r["label"] == "Strong Trade")

r = run("SWING", cvd="bearish")  # cvd HARD veto
check("SWING CVD hard veto -> WAIT", r["label"] == "WAIT")
check("SWING CVD conflict IS a failed gate", "cvd_conflict" in (r.get("missing") or []))

r = run("SWING", include_sweep=False)  # 55, SETUP BUILDING disabled
check("SWING never emits SETUP BUILDING (-> WAIT)", r["label"] == "WAIT")

r = run("SWING", cooldown=True)  # cooldown must NOT affect SWING score
check("SWING cooldown ignored (edge stays 70)", r["gate_debug"]["edge_score"] == 70)

print()
if FAILS:
    print("SMOKE FAILED:", len(FAILS), "checks ->", FAILS)
    sys.exit(1)
print("ALL SMOKE CHECKS PASSED")
