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

# Configure deterministic webhook URLs so routing is observable.
app.DISCORD_WEBHOOK_URL = "https://mgc.example/hook"
app.DISCORD_MNQ_WEBHOOK_URL = "https://mnq.example/hook"
app.DISCORD_JOURNAL_WEBHOOK_URL = "https://journal.example/hook"

def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond:
        raise SystemExit("FAILED: " + name)

def fresh_plan(direction, ticker, demand=None, supply=None):
    p = app.build_strict_trade_plan(direction, ticker, 0.0,
                                    nearest_demand=demand, nearest_supply=supply)
    assert p["trade_plan"], p
    return p

def make_entry(p, ticker, strength="A+ Setup", edge=97, jid=1):
    return {
        "id": jid, "symbol": ticker, "instrument": app.instrument_of(ticker),
        "datetime": "2026-06-15T14:00:00+00:00",
        "direction": p["direction"], "verdict": "READY",
        "trade_strength": strength, "edge_score": edge,
        "entry_zone": p["entry_zone"], "stop_loss": p["stop_loss"],
        "target1": p["target1"], "target2": p["target2"], "target3": p["target3"],
        "be_level": p["be_level"], "partial_level": p["partial_level"],
        "runner_target": p["runner_target"], "risk_points": p["risk_points"],
        "reward_points": p["reward_points"], "rr_num": p["rr_num"],
        "max_invalidation": p["max_invalidation"], "management_plan": p["management"],
        "outcome": "Pending",
    }

# ── Helper to fully reset watcher state between scenarios ──
def reset():
    app.MANAGED_TRADES_BY_KEY.clear()
    app.JOURNAL.clear()
    app.JOURNAL_KEYS.clear()
    _posts.clear()

# ════════════════════════════════════════════════════════════════════════════
# Scenario 1: MGC Long full progression TP1 -> TP2 -> TP3 (terminal Win)
# entry=2958.5 stop=2957 tp1=2963 partial=2965.5 tp2=2968 tp3=2973
reset()
p = fresh_plan("Long", "MGC", demand=2958.0)
e = make_entry(p, "MGC", jid=1)
app.JOURNAL.insert(0, e)
mt = app._register_managed_trade(e, "MGC")
check("S1 registered", mt is not None and not mt["closed"])

# bar that hits TP1 + partial only (high=2966 < tp2 2968)
app._evaluate_managed_trade_levels(mt, {"high": 2966.0, "low": 2960.0, "close": 2965.0})
check("S1 TP1 sent", "TP1" in mt["events_sent"])
check("S1 be_active", mt["be_active"] is True)
check("S1 PARTIAL sent", "PARTIAL" in mt["events_sent"])
check("S1 not TP2", "TP2" not in mt["events_sent"])
check("S1 still open", not mt["closed"])
check("S1 mfe tracked", abs(mt["mfe"] - (2966.0 - 2958.5)) < 1e-6, mt["mfe"])

# next bar hits TP2 (high=2969) but not TP3
app._evaluate_managed_trade_levels(mt, {"high": 2969.0, "low": 2967.0, "close": 2968.5})
check("S1 TP2 sent", "TP2" in mt["events_sent"])
check("S1 still open after TP2", not mt["closed"])

# next bar hits TP3 -> terminal Win
app._evaluate_managed_trade_levels(mt, {"high": 2974.0, "low": 2970.0, "close": 2973.5})
check("S1 TP3 sent", "TP3" in mt["events_sent"])
check("S1 closed Win", mt["closed"] and mt["outcome"] == "Win", mt.get("outcome"))
# R = (2973 - 2958.5)/1.5 = 14.5/1.5 = 9.67
check("S1 r_multiple", abs(mt["r_multiple"] - round(14.5/1.5, 2)) < 1e-6, mt["r_multiple"])
check("S1 pnl_dollars", abs(mt["pnl_dollars"] - round(14.5*10.0, 2)) < 1e-6, mt["pnl_dollars"])
# journal mutated
check("S1 journal outcome Win", e["outcome"] == "Win", e["outcome"])
check("S1 journal r_multiple", e.get("r_multiple") == mt["r_multiple"])
check("S1 journal mgmt updates present", len(e.get("management_updates", [])) >= 3)
# outcome recognised by analytics
check("S1 outcome_state win", app._outcome_state(e["outcome"], e.get("pnl_dollars")) == "win")

# ── idempotent: further bars do not change a closed trade ──
prev = dict(mt)
app._evaluate_managed_trade_levels(mt, {"high": 9999.0, "low": 0.0, "close": 50.0})
check("S1 closed stays closed", mt["outcome"] == prev["outcome"] and mt["closed"])

# ════════════════════════════════════════════════════════════════════════════
# Scenario 2: MGC Long stop hit before any target (terminal Loss)
reset()
p = fresh_plan("Long", "MGC", demand=2958.0)
e = make_entry(p, "MGC", jid=1)
app.JOURNAL.insert(0, e)
mt = app._register_managed_trade(e, "MGC")
app._evaluate_managed_trade_levels(mt, {"high": 2959.0, "low": 2956.5, "close": 2957.0})  # low<=2957 stop
check("S2 closed Loss", mt["closed"] and mt["outcome"] == "Loss", mt.get("outcome"))
check("S2 r ~ -1", abs(mt["r_multiple"] - (-1.0)) < 0.01, mt["r_multiple"])
check("S2 journal Loss", e["outcome"] == "Loss")
check("S2 mae tracked", mt["mae"] >= (2958.5 - 2956.5) - 1e-6, mt["mae"])

# ════════════════════════════════════════════════════════════════════════════
# Scenario 3: BE exit — TP1 hit, then pullback to entry (Breakeven, not Loss)
reset()
p = fresh_plan("Long", "MGC", demand=2958.0)
e = make_entry(p, "MGC", jid=1)
app.JOURNAL.insert(0, e)
mt = app._register_managed_trade(e, "MGC")
app._evaluate_managed_trade_levels(mt, {"high": 2963.5, "low": 2961.0, "close": 2963.0})  # TP1 -> be
check("S3 be_active", mt["be_active"] and not mt["closed"])
# pullback below entry (2958.5) but above original stop (2957): low=2958
app._evaluate_managed_trade_levels(mt, {"high": 2962.0, "low": 2958.0, "close": 2958.2})
check("S3 closed Breakeven", mt["closed"] and mt["outcome"] == "Breakeven", mt.get("outcome"))
check("S3 r ~ 0", abs(mt["r_multiple"]) < 0.01, mt["r_multiple"])
check("S3 journal BE state", app._outcome_state(e["outcome"], e.get("pnl_dollars")) == "breakeven")

# ════════════════════════════════════════════════════════════════════════════
# Scenario 4: MGC Short progression TP1 -> TP3 (Win)
# supply=2958 -> entry=2957.5 stop=2959 tp1=2953 partial=2950.5 tp2=2948 tp3=2943
reset()
p = fresh_plan("Short", "MGC", supply=2958.0)
e = make_entry(p, "MGC", jid=1)
app.JOURNAL.insert(0, e)
mt = app._register_managed_trade(e, "MGC")
app._evaluate_managed_trade_levels(mt, {"high": 2956.0, "low": 2952.0, "close": 2953.0})  # low<=tp1 2953
check("S4 TP1 short", "TP1" in mt["events_sent"] and mt["be_active"])
app._evaluate_managed_trade_levels(mt, {"high": 2949.0, "low": 2942.0, "close": 2943.0})  # sweeps tp2+tp3
check("S4 closed Win short", mt["closed"] and mt["outcome"] == "Win", mt.get("outcome"))
check("S4 short r positive", mt["r_multiple"] > 0)

# ════════════════════════════════════════════════════════════════════════════
# Scenario 5: short stop precedence — ambiguous bar resolves to Loss
reset()
p = fresh_plan("Short", "MGC", supply=2958.0)
e = make_entry(p, "MGC", jid=1)
app.JOURNAL.insert(0, e)
mt = app._register_managed_trade(e, "MGC")
# bar spans both stop (2959) and tp1 (2953): high=2960, low=2952 -> stop precedence
app._evaluate_managed_trade_levels(mt, {"high": 2960.0, "low": 2952.0, "close": 2955.0})
check("S5 stop precedence Loss", mt["closed"] and mt["outcome"] == "Loss", mt.get("outcome"))

# ════════════════════════════════════════════════════════════════════════════
# Scenario 6: dedupe across repost — re-register does not reset progress/events
reset()
p = fresh_plan("Long", "MGC", demand=2958.0)
e = make_entry(p, "MGC", jid=1)
app.JOURNAL.insert(0, e)
mt = app._register_managed_trade(e, "MGC")
app._evaluate_managed_trade_levels(mt, {"high": 2964.0, "low": 2961.0, "close": 2963.5})  # TP1
check("S6 TP1 sent", "TP1" in mt["events_sent"])
mt2 = app._register_managed_trade(e, "MGC")  # repost
check("S6 same object", mt2 is mt, "repost must refresh, not replace")
check("S6 events preserved", "TP1" in mt2["events_sent"])
check("S6 single managed trade", len(app.MANAGED_TRADES_BY_KEY) == 1)

# ════════════════════════════════════════════════════════════════════════════
# Scenario 7: routing — MNQ updates/outcome go to MNQ channel + journal
reset()
p = fresh_plan("Long", "MNQ", demand=20000.0)
e = make_entry(p, "MNQ", jid=1)
app.JOURNAL.insert(0, e)
mt = app._register_managed_trade(e, "MNQ")
_posts.clear()
app._evaluate_managed_trade_levels(mt, {"high": 20021.0, "low": 20010.0, "close": 20020.5})  # TP1 update
update_urls = [pp["url"] for pp in _posts]
check("S7 TP1 update to MNQ channel", "https://mnq.example/hook" in update_urls, update_urls)
_posts.clear()
# Drive to TP3 (20060) for terminal outcome posting
app._evaluate_managed_trade_levels(mt, {"high": 20061.0, "low": 20055.0, "close": 20060.5})
outcome_urls = [pp["url"] for pp in _posts]
check("S7 outcome to MNQ channel", "https://mnq.example/hook" in outcome_urls)
check("S7 outcome to journal channel", "https://journal.example/hook" in outcome_urls)

# ════════════════════════════════════════════════════════════════════════════
# Scenario 8: no management plan -> no managed trade registered (fail-open)
reset()
e_nomgmt = {"id": 1, "symbol": "MGC", "instrument": "MGC", "direction": "Long",
            "verdict": "READY", "trade_strength": "Possible Trade"}
mt = app._register_managed_trade(e_nomgmt, "MGC")
check("S8 no plan -> None", mt is None and len(app.MANAGED_TRADES_BY_KEY) == 0)

# ════════════════════════════════════════════════════════════════════════════
# Scenario 9: send_live_ready_card registers a managed trade + LAST_READY snapshot
reset()
app.LAST_READY_BY_TICKER.clear()
p = fresh_plan("Long", "MGC", demand=2958.0)
e = make_entry(p, "MGC", jid=1)
app.send_live_ready_card(e, "MGC")
check("S9 managed trade via card", len(app.MANAGED_TRADES_BY_KEY) == 1)
check("S9 LAST_READY snapshot set", app.LAST_READY_BY_TICKER.get("MGC") is e)

print("ALL T3 TESTS PASSED")
