import requests
def _fake_post(url, *a, **k):
    class R:
        status_code = 204
        text = ""
    return R()
requests.post = _fake_post

import app

def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond:
        raise SystemExit("FAILED: " + name)

# ════════════════════════════════════════════════════════════════════════════
# 1) _build_why_explanation on a rich READY card entry (Long, Strong, has risk)
entry = {
    "symbol": "MGC", "instrument": "MGC", "direction": "Long",
    "verdict": "READY", "edge_score": 92, "edge_grade": "A+",
    "trade_strength": "Strong Trade",
    "trade_thesis": "Long MGC: demand reclaim with bullish CHOCH above VWAP.",
    "why_qualifies": "Long confirmed — BOS demand, bullish CHOCH, 5m bullish candle, price above VWAP.",
    "setup_notes": "Price reacted at demand.\nReclaimed VWAP.",
    "score_breakdown": [
        {"label": "BOS Demand", "points": 25},
        {"label": "Bullish CHOCH", "points": 25},
        {"label": "Confirmation Candle", "points": 15},
        {"label": "VWAP Reclaim", "points": 10},
        {"label": "Confirmed Zone Reaction", "points": 5},
    ],
    "risk_adjustments": [{"label": "Nearby Resistance", "points": -4}],
    "entry_zone": "2958.0–2959.0", "stop_loss": 2957.0,
    "target1": 2963.0, "target2": 2968.0, "target3": 2973.0,
    "max_invalidation": "Close below 2957.0",
}
exp = app._build_why_explanation(entry)
check("1 direction", exp["direction"] == "Long")
check("1 edge", exp["edge_score"] == 92)
check("1 strength", exp["trade_strength"] == "Strong Trade")
check("1 gate has 4", set(exp["passed_conditions"]) ==
      {"BOS Demand", "Bullish CHOCH", "Confirmation Candle", "VWAP Reclaim"}, exp["passed_conditions"])
check("1 confluences", exp["confluences"] == ["Confirmed Zone Reaction"], exp["confluences"])
check("1 risks", exp["risks"] == ["Nearby Resistance"], exp["risks"])
check("1 invalidation", exp["invalidation"] == "Close below 2957.0")
check("1 targets", exp["targets"] == [2963.0, 2968.0, 2973.0])
# improvements: edge<95 -> missing bonuses + resolve risk
check("1 improvements has resolve risk", "Resolve risk: Nearby Resistance" in exp["improvements"], exp["improvements"])
check("1 improvements has liquidity sweep", "Add confluence: Liquidity Sweep" in exp["improvements"])
# Confirmed Zone Reaction already present -> NOT in improvements
check("1 improvements omits present confluence",
      "Add confluence: Confirmed Zone Reaction" not in exp["improvements"], exp["improvements"])

# ════════════════════════════════════════════════════════════════════════════
# 2) A+ entry (edge>=95) -> no improvements; short direction gate labels
entry_aplus = {
    "symbol": "MNQ", "instrument": "MNQ", "direction": "Short",
    "verdict": "READY", "edge_score": 97, "trade_strength": "A+ Setup",
    "score_breakdown": [
        {"label": "BOS Supply", "points": 25},
        {"label": "Bearish CHOCH", "points": 25},
        {"label": "Confirmation Candle", "points": 15},
        {"label": "VWAP Rejection", "points": 10},
    ],
    "risk_adjustments": [],
    "max_invalidation": "Close above 20010",
}
exp2 = app._build_why_explanation(entry_aplus)
check("2 short gate labels", set(exp2["passed_conditions"]) ==
      {"BOS Supply", "Bearish CHOCH", "Confirmation Candle", "VWAP Rejection"}, exp2["passed_conditions"])
check("2 aplus no improvements", exp2["improvements"] == [], exp2["improvements"])
check("2 strength aplus", exp2["trade_strength"] == "A+ Setup")

# ════════════════════════════════════════════════════════════════════════════
# 3) Defensive: empty entry doesn't crash
exp3 = app._build_why_explanation({})
check("3 empty direction default", exp3["direction"] == "—")
check("3 empty passed empty", exp3["passed_conditions"] == [])
check("3 empty targets none", exp3["targets"] == [None, None, None])

# ════════════════════════════════════════════════════════════════════════════
# 4) Endpoint via Flask test client — snapshot path
client = app.app.test_client()
app.LAST_READY_BY_TICKER.clear()
app.LAST_READY_BY_TICKER["MGC"] = entry
r = client.get("/why?ticker=MGC")
check("4 status 200", r.status_code == 200, r.status_code)
j = r.get_json()
check("4 source snapshot", j["source"] == "snapshot", j.get("source"))
check("4 instrument MGC", j["instrument"] == "MGC")
check("4 edge 92", j["edge_score"] == 92)
check("4 ticker carried", j["ticker"] == "MGC")

# path variant
r_path = client.get("/why/MGC")
check("4 path variant 200", r_path.status_code == 200)
check("4 path variant snapshot", r_path.get_json()["source"] == "snapshot")

# ════════════════════════════════════════════════════════════════════════════
# 5) Endpoint live-fallback path (no snapshot) — must not crash, returns source=live
app.LAST_READY_BY_TICKER.clear()
r2 = client.get("/why?ticker=MNQ")
check("5 status 200", r2.status_code == 200, r2.status_code)
j2 = r2.get_json()
check("5 source live", j2.get("source") == "live", j2)
check("5 instrument MNQ", j2.get("instrument") == "MNQ")
# Should have either a valid explanation (direction key) or graceful error
check("5 has direction or error", ("direction" in j2) or ("error" in j2), j2)

print("ALL T5 TESTS PASSED")
