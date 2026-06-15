import sys, types, importlib

# Monkeypatch requests BEFORE importing app so no real network calls happen.
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

def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise SystemExit("FAILED: " + name)

# 1) Band mapping
m = app._trade_strength_from_score
check("74 -> None", m(74) is None)
check("75 -> Possible", m(75) == "Possible Trade")
check("89 -> Possible", m(89) == "Possible Trade")
check("90 -> Strong", m(90) == "Strong Trade")
check("94 -> Strong", m(94) == "Strong Trade")
check("95 -> A+", m(95) == "A+ Setup")
check("100 -> A+", m(100) == "A+ Setup")

# 2) Display labels
check("A+ display", app._strength_display("A+ Setup") == "🔥 A+ SETUP")
check("Strong display", app._strength_display("Strong Trade") == "🟢 STRONG TRADE")
check("Possible display", app._strength_display("Possible Trade") == "🟡 POSSIBLE TRADE")

# 3) _entry_trade_strength recognises A+ explicit + derives from edge
check("entry explicit A+", app._entry_trade_strength({"trade_strength": "A+ Setup"}) == "A+ Setup")
check("entry derive A+", app._entry_trade_strength({"edge_score": 97}) == "A+ Setup")
check("entry derive Strong", app._entry_trade_strength({"edge_score": 92}) == "Strong Trade")

# 4) Card embed color + label for A+
aplus_entry = {
    "id": 1, "symbol": "MGC", "direction": "Long", "verdict": "READY",
    "datetime": "2026-06-15T14:00:00+00:00", "trade_strength": "A+ Setup",
    "edge_score": 97, "entry_zone": "2958.0–2959.0", "stop_loss": "2957.0",
    "target1": "2963.0", "target2": "2968.0",
}
emb = app._build_trade_card_embed(aplus_entry, "test")
check("A+ embed color", emb["color"] == 0xFF4500)
check("A+ embed author label", "🔥 A+ SETUP" in emb["author"]["name"])
check("A+ embed desc label", "🔥 A+ SETUP" in emb["description"])

strong_entry = dict(aplus_entry, trade_strength="Strong Trade", edge_score=92)
check("Strong embed color", app._build_trade_card_embed(strong_entry, "t")["color"] == 0x2ECC71)
poss_entry = dict(aplus_entry, trade_strength="Possible Trade", edge_score=80)
check("Possible embed color", app._build_trade_card_embed(poss_entry, "t")["color"] == 0xF1C40F)

# 5) compute_performance_stats has 3 strength bands
entries = [
    {"outcome": "WIN", "pnl_dollars": 100.0, "trade_strength": "A+ Setup", "edge_score": 97, "direction": "Long", "symbol": "MGC"},
    {"outcome": "LOSS", "pnl_dollars": -50.0, "trade_strength": "Strong Trade", "edge_score": 92, "direction": "Short", "symbol": "MNQ"},
    {"outcome": "WIN", "pnl_dollars": 60.0, "trade_strength": "Possible Trade", "edge_score": 80, "direction": "Long", "symbol": "MGC"},
]
stats = app.compute_performance_stats(entries)
bs = stats["by_strength"]
check("by_strength has A+", "A+ Setup" in bs)
check("by_strength has Strong", "Strong Trade" in bs)
check("by_strength has Possible", "Possible Trade" in bs)
check("A+ has 1 win", bs["A+ Setup"]["wins"] == 1)

# 6) EOD stats aplus_count
app.JOURNAL.clear()
for e in entries:
    e2 = dict(e)
    e2.setdefault("datetime", app.datetime.now(app.timezone.utc).isoformat())
    app.JOURNAL.append(e2)
eod = app._compute_eod_stats()
check("eod aplus_count key", "aplus_count" in eod)

# 7) A+ channel mirror routing: with env set, posts to A+ channel too
_posts.clear()
app.DISCORD_WEBHOOK_URL = "https://main.example/hook"
app.DISCORD_APLUS_WEBHOOK_URL = "https://aplus.example/hook"
app.send_live_ready_card(aplus_entry, "MGC")
urls = [p["url"] for p in _posts]
check("A+ posts to main", "https://main.example/hook" in urls)
check("A+ mirrors to aplus channel", "https://aplus.example/hook" in urls)

# fallback: no A+ channel -> only main, no duplicate
_posts.clear()
app.DISCORD_APLUS_WEBHOOK_URL = ""
app.send_live_ready_card(aplus_entry, "MGC")
urls = [p["url"] for p in _posts]
check("no A+ channel -> single post", urls == ["https://main.example/hook"])

# Possible setup never mirrors even if A+ channel set
_posts.clear()
app.DISCORD_APLUS_WEBHOOK_URL = "https://aplus.example/hook"
app.send_live_ready_card(poss_entry, "MGC")
urls = [p["url"] for p in _posts]
check("Possible not mirrored to A+", "https://aplus.example/hook" not in urls)

print("ALL T1 TESTS PASSED")
