"""End-to-end full_analysis smoke: confirms the SETUP BUILDING verdict mapping
(non-actionable, no trade plan) and the camelCase diagnostics fields, driven with
monkeypatched data sources so no live feed/zone state is required."""
import sys
import app

FAILS = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)

now = app.now_utc().isoformat()

# ── Deterministic data sources ───────────────────────────────────────────────
app.get_vwap            = lambda t, max_age_min=None: (99.95, "ok")
app.get_volatility      = lambda t: {"atr_pts": 1.0, "blocked": False, "score_adj": 0,
                                     "regime": "NORMAL", "display": "Normal"}
app.get_price_context   = lambda t: ({}, [], [99.96])
app.get_nearest_levels  = lambda price, sup, dem: (None, 99.96)
app.market_session_status = lambda now=None: {
    "open": True, "status": "open", "next_open_et": None, "next_open": None, "reason": ""}
app.ZONE_MITIGATED_FLAG = True
app.MITIGATED_PRICES    = [{"price": 99.96, "ts": now}]
app.CVD_BY_TICKER = {}
app.RVOL_BY_TICKER = {}
app.VOLUME_SPIKE_BY_TICKER = {}

def set_history(include_sweep):
    h = [
        {"alert_type": "BOS DEMAND",   "instrument": "MGC", "ticker": "MGC", "timestamp": now},
        {"alert_type": "CHOCH DEMAND", "instrument": "MGC", "ticker": "MGC", "timestamp": now},
        {"alert_type": "MGC BULLISH CONFIRMATION", "instrument": "MGC", "ticker": "MGC", "timestamp": now},
    ]
    if include_sweep:
        h.append({"alert_type": "MGC BULLISH SWEEP", "instrument": "MGC", "ticker": "MGC", "timestamp": now})
    app.ALERT_HISTORY = h

DIAG_KEYS = ["edgeScore", "requiredEdgeScore", "failedGates", "scoreBreakdown",
             "zoneAge", "zoneConsumed", "vwapDistance", "cvdState", "readyStrength"]

# ── SCALP SETUP BUILDING (no sweep -> edge 55) ───────────────────────────────
app.TRADING_MODE = "SCALP"
set_history(include_sweep=False)
a = app.full_analysis(current_price_override=100.0, ticker_override="MGC")
check("SETUP BUILDING verdict", a["verdict"] == "SETUP BUILDING")
check("SETUP BUILDING is NOT actionable", not app.is_actionable(a["verdict"]))
check("SETUP BUILDING has NO trade plan", not a["trade_plan"]["trade_plan"])
check("SETUP BUILDING alert_level", a.get("alert_level") == "SETUP BUILDING")
diag = a.get("alert_diagnostics") or {}
check("alert_diagnostics has all camelCase fields", all(k in diag for k in DIAG_KEYS))
check("diag edgeScore == 55", diag.get("edgeScore") == 55)
check("diag requiredEdgeScore == 60", diag.get("requiredEdgeScore") == 60)
check("diag readyStrength None (non-actionable)", diag.get("readyStrength") is None)

# ── SCALP READY (with sweep -> edge 70) ──────────────────────────────────────
set_history(include_sweep=True)
a = app.full_analysis(current_price_override=100.0, ticker_override="MGC")
check("READY verdict actionable", app.is_actionable(a["verdict"]) and a["verdict"] == "LONG READY")
check("READY has trade plan", a["trade_plan"]["trade_plan"])
diag = a.get("alert_diagnostics") or {}
check("READY diag edgeScore == 70", diag.get("edgeScore") == 70)
check("READY diag failedGates empty", not diag.get("failedGates"))
check("READY diag cvdState reflects state", "cvdState" in diag)

# ── SWING: same building inputs -> WAIT, never SETUP BUILDING ────────────────
app.TRADING_MODE = "SWING"
set_history(include_sweep=False)
a = app.full_analysis(current_price_override=100.0, ticker_override="MGC")
check("SWING building inputs -> WAIT (no SETUP BUILDING)", a["verdict"] == "WAIT")

print()
if FAILS:
    print("FULL SMOKE FAILED:", len(FAILS), "->", FAILS)
    sys.exit(1)
print("ALL FULL-ANALYSIS SMOKE CHECKS PASSED")
