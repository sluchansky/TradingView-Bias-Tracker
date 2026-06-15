import requests
_posts = []
def _fake_post(url, *a, **k):
    _posts.append({"url": url, "json": k.get("json")})
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

# ── build_strict_trade_plan math for MGC + MNQ, long + short ──
# Long: anchor=nearest_demand; Short: anchor=nearest_supply.
def plan(direction, ticker, demand=None, supply=None, price=0.0):
    return app.build_strict_trade_plan(direction, ticker, price,
                                       nearest_demand=demand, nearest_supply=supply)

# MGC Long: demand=2958, buf=1, tp1=5/tp2=10/tp3=15
p = plan("Long", "MGC", demand=2958.0)
check("MGC long is plan", p["trade_plan"] is True)
# entry=(2958+2959)/2=2958.5, stop=2957, t1=2963, t2=2968, t3=2973
check("MGC long entry_zone", p["entry_zone"] == "2958.00–2959.00", p["entry_zone"])
check("MGC long stop", p["stop_loss"] == "2957.00", p["stop_loss"])
check("MGC long t1", p["target1"] == "2963.00", p["target1"])
check("MGC long t2", p["target2"] == "2968.00", p["target2"])
check("MGC long t3", p["target3"] == "2973.00", p["target3"])
# be=t1=2963, partial=(t1+t2)/2=2965.5, runner=t3=2973
check("MGC long be", p["be_level"] == "2963.00", p["be_level"])
check("MGC long partial", p["partial_level"] == "2965.50", p["partial_level"])
check("MGC long runner", p["runner_target"] == "2973.00", p["runner_target"])
# risk=abs(2958.5-2957)=1.5, reward=abs(2973-2958.5)=14.5, rr=14.5/1.5=9.67
check("MGC long risk_points", abs(p["risk_points"] - 1.5) < 1e-6, p["risk_points"])
check("MGC long reward_points", abs(p["reward_points"] - 14.5) < 1e-6, p["reward_points"])
check("MGC long rr_num", abs(p["rr_num"] - round(14.5/1.5, 2)) < 1e-6, p["rr_num"])
check("MGC long invalidation mentions below+demand",
      "below" in p["max_invalidation"] and "demand" in p["max_invalidation"], p["max_invalidation"])
m = p["management"]
check("MGC long mgmt numeric entry", abs(m["entry"] - 2958.5) < 1e-6, m["entry"])
check("MGC long mgmt tp3", abs(m["tp3"] - 2973.0) < 1e-6, m["tp3"])
check("MGC long mgmt entry_lo", abs(m["entry_lo"] - 2958.0) < 1e-6)

# MGC Short: supply=2958, buf=1 -> lo=2957, hi=2958, entry=2957.5, stop=2959,
# t1=2953, t2=2948, t3=2943; be=2953, partial=2950.5, runner=2943
p = plan("Short", "MGC", supply=2958.0)
check("MGC short is plan", p["trade_plan"] is True)
check("MGC short stop", p["stop_loss"] == "2959.00", p["stop_loss"])
check("MGC short t3", p["target3"] == "2943.00", p["target3"])
check("MGC short partial", p["partial_level"] == "2950.50", p["partial_level"])
check("MGC short invalidation mentions above+supply",
      "above" in p["max_invalidation"] and "supply" in p["max_invalidation"], p["max_invalidation"])

# MNQ Long: demand=20000, buf=5, tp1=20/tp2=40/tp3=60 -> entry=20002.5, stop=19995,
# t1=20020, t2=20040, t3=20060; uses 1 decimal fmt
p = plan("Long", "MNQ", demand=20000.0)
check("MNQ long is plan", p["trade_plan"] is True)
check("MNQ long entry_zone", p["entry_zone"] == "20000.0–20005.0", p["entry_zone"])
check("MNQ long t3", p["target3"] == "20060.0", p["target3"])
# risk=abs(20002.5-19995)=7.5, reward=abs(20060-20002.5)=57.5
check("MNQ long risk", abs(p["risk_points"] - 7.5) < 1e-6, p["risk_points"])
check("MNQ long reward", abs(p["reward_points"] - 57.5) < 1e-6, p["reward_points"])

# MNQ Short: supply=20000 -> entry=19997.5, stop=20005, t3=19940
p = plan("Short", "MNQ", supply=20000.0)
check("MNQ short t3", p["target3"] == "19940.0", p["target3"])

# ── no_plan parity: missing anchor returns all additive keys present (None) ──
np_ = plan("Long", "MGC", demand=None)
check("no_plan trade_plan False", np_["trade_plan"] is False)
for k in ("target3", "be_level", "partial_level", "runner_target", "risk_points",
          "reward_points", "rr_num", "max_invalidation", "management"):
    check(f"no_plan has key {k}", k in np_)
    check(f"no_plan {k} is None", np_[k] is None)

# ── existing keys unchanged shape ──
p = plan("Long", "MGC", demand=2958.0)
for k in ("trade_plan", "reason", "entry_zone", "stop_loss", "target1", "target2",
          "rr", "direction", "instrument", "point_value"):
    check(f"existing key present {k}", k in p)

# ── card render includes 📋 Trade Management with TP3/BE/Partial/Runner/RR ──
entry = {
    "id": 1, "symbol": "MGC", "direction": "Long", "verdict": "READY",
    "datetime": "2026-06-15T14:00:00+00:00", "trade_strength": "A+ Setup",
    "edge_score": 97, "entry_zone": p["entry_zone"], "stop_loss": p["stop_loss"],
    "target1": p["target1"], "target2": p["target2"],
    "target3": p["target3"], "be_level": p["be_level"],
    "partial_level": p["partial_level"], "runner_target": p["runner_target"],
    "risk_points": p["risk_points"], "reward_points": p["reward_points"],
    "rr_num": p["rr_num"], "max_invalidation": p["max_invalidation"],
}
emb = app._build_trade_card_embed(entry, "test")
names = [f["name"] for f in emb["fields"]]
check("card has Trade Management field", "📋 Trade Management" in names)
mgmt = [f for f in emb["fields"] if f["name"] == "📋 Trade Management"][0]["value"]
check("mgmt shows TP3", "TP3" in mgmt and p["target3"] in mgmt)
check("mgmt shows BE", "BE" in mgmt and p["be_level"] in mgmt)
check("mgmt shows Partial", "Partial" in mgmt)
check("mgmt shows Runner", "Runner" in mgmt)
check("mgmt shows R:R", "R:R" in mgmt)
check("mgmt shows invalidation", "Invalidation" in mgmt)

# ── card without management plan: field omitted, no crash ──
entry_nomgmt = {
    "id": 2, "symbol": "MGC", "direction": "Long", "verdict": "READY",
    "datetime": "2026-06-15T14:00:00+00:00", "trade_strength": "Possible Trade",
    "edge_score": 80, "entry_zone": "1–2", "stop_loss": "0.5",
    "target1": "3", "target2": "4",
}
emb2 = app._build_trade_card_embed(entry_nomgmt, "t")
check("no-mgmt card omits field", "📋 Trade Management" not in [f["name"] for f in emb2["fields"]])

print("ALL T2 TESTS PASSED")
