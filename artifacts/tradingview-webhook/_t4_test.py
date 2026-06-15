import requests
from datetime import datetime, timezone, timedelta
_posts = []
def _fake_post(url, *a, **k):
    _posts.append({"url": url, "json": k.get("json")})
    class R:
        status_code = 204
        text = ""
    return R()
requests.post = _fake_post

import app

app.DISCORD_WEBHOOK_URL = "https://mgc.example/hook"
app.DISCORD_MNQ_WEBHOOK_URL = "https://mnq.example/hook"
app.DISCORD_JOURNAL_WEBHOOK_URL = "https://journal.example/hook"

def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond:
        raise SystemExit("FAILED: " + name)

def ts(days_ago, hour=14):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0).isoformat()

app.JOURNAL.clear()
app.JOURNAL_KEYS.clear()

# Build a representative week of entries (newest-first like the real JOURNAL).
# Wins/losses/breakeven across MGC+MNQ, long+short, with edge scores + strengths.
app.JOURNAL.extend([
    # MGC A+ long win, +$300, r_multiple 3.0, 2 days ago
    {"id": 1, "symbol": "MGC", "datetime": ts(2), "direction": "Long",
     "outcome": "Win", "pnl_dollars": 300.0, "r_multiple": 3.0,
     "edge_score": 97, "trade_strength": "A+ Setup"},
    # MNQ strong short loss, -$100, 3 days ago
    {"id": 2, "symbol": "MNQ", "datetime": ts(3), "direction": "Short",
     "outcome": "Loss", "pnl_dollars": -100.0, "r_multiple": -1.0,
     "edge_score": 92, "trade_strength": "Strong Trade"},
    # MGC possible long breakeven, $0, 4 days ago
    {"id": 3, "symbol": "MGC", "datetime": ts(4), "direction": "Long",
     "outcome": "Breakeven", "pnl_dollars": 0.0, "r_multiple": 0.0,
     "edge_score": 80, "trade_strength": "Possible Trade"},
    # MNQ A+ long win, +$500, 1 day ago
    {"id": 4, "symbol": "MNQ", "datetime": ts(1), "direction": "Long",
     "outcome": "Win", "pnl_dollars": 500.0, "r_multiple": 5.0,
     "edge_score": 96, "trade_strength": "A+ Setup"},
    # Pending (still open) — should be counted as a setup but not decided
    {"id": 5, "symbol": "MGC", "datetime": ts(0), "direction": "Short",
     "outcome": "Pending", "edge_score": 88, "trade_strength": "Possible Trade"},
    # OLD entry outside the 7-day window — must be excluded
    {"id": 6, "symbol": "MGC", "datetime": ts(20), "direction": "Long",
     "outcome": "Win", "pnl_dollars": 999.0, "r_multiple": 9.0,
     "edge_score": 99, "trade_strength": "A+ Setup"},
])

s = app._compute_weekly_stats()

# ── Window filtering ──
check("total_setups excludes old", s["total_setups"] == 5, s["total_setups"])
# ── W/L/BE ──
check("wins", s["wins"] == 2, s["wins"])
check("losses", s["losses"] == 1, s["losses"])
check("breakevens", s["breakevens"] == 1, s["breakevens"])
check("decided", s["decided"] == 3, s["decided"])
# win rate = 2/3 = 66.7%
check("win_rate", abs(s["win_rate"] - (2/3*100)) < 0.1, s["win_rate"])
# ── Net P&L: 300 - 100 + 0 + 500 = 700 (old 999 excluded) ──
check("net_pnl", s["net_pnl"] == 700.0, s["net_pnl"])
# ── Net R: 3 - 1 + 0 + 5 = 7 ──
check("net_r", s["net_r"] == 7.0, s["net_r"])
# ── Profit factor: gross_win 800 / gross_loss 100 = 8.0 ──
check("profit_factor", abs(s["profit_factor"] - 8.0) < 1e-6, s["profit_factor"])
# ── Best setup = highest-edge win (MGC A+ edge 97 > MNQ A+ edge 96) ──
check("best setup id", s["best"]["id"] == 1, s["best"])
# ── Worst setup = the loss (id 2) ──
check("worst setup id", s["worst"]["id"] == 2, s["worst"])
# ── Best/worst instrument by net pnl: MNQ = +400, MGC = +300 ──
check("best_instrument", s["best_instrument"] == "MNQ", s["best_instrument"])
check("worst_instrument", s["worst_instrument"] == "MGC", s["worst_instrument"])
check("inst_pnl MNQ", s["inst_pnl"]["MNQ"] == 400.0, s["inst_pnl"])
check("inst_pnl MGC", s["inst_pnl"]["MGC"] == 300.0, s["inst_pnl"])
# ── Direction net pnl: Long = 300+0+500=800, Short = -100 ──
check("best_direction Long", s["best_direction"] == "Long", s["best_direction"])
check("worst_direction Short", s["worst_direction"] == "Short", s["worst_direction"])
# ── Strength counts (across all setups incl pending) ──
check("aplus_count", s["aplus_count"] == 2, s["aplus_count"])
check("strong_count", s["strong_count"] == 1, s["strong_count"])
check("possible_count", s["possible_count"] == 2, s["possible_count"])  # id3 + id5
# ── Avg edge over READY entries (all have strengths): (97+92+80+96+88)/5 = 90.6 ──
check("avg_edge", abs(s["avg_edge_score"] - 90.6) < 0.1, s["avg_edge_score"])
# ── by_strength A+ split present + correct (2 wins, 0 loss) ──
ap = s["by_strength"]["A+ Setup"]
check("A+ by_strength wins", ap["wins"] == 2, ap)
check("A+ by_strength win_rate 100", abs(ap["win_rate"] - 100.0) < 1e-6, ap)

# ════════════════════════════════════════════════════════════════════════════
# Embed builds with all sections and posts to all 3 channels
_posts.clear()
returned = app._send_weekly_report()
check("send returns stats", returned["net_pnl"] == 700.0)
check("posted to 3 channels", len(_posts) == 3, len(_posts))
urls = {p["url"] for p in _posts}
check("mgc channel", "https://mgc.example/hook" in urls)
check("mnq channel", "https://mnq.example/hook" in urls)
check("journal channel", "https://journal.example/hook" in urls)
embed = _posts[0]["json"]["embeds"][0]
names = [f["name"] for f in embed["fields"]]
for required in ["Total setups", "Win rate", "Profit Factor", "Avg R", "Net R",
                 "Net P&L", "🎯 By Trade Strength", "🔼 Best setup", "🔽 Worst setup",
                 "🔥 A+ setups", "Best instrument", "Best direction"]:
    check(f"embed has '{required}'", required in names, names)
check("title", embed["title"] == "🗓️ Weekly Performance Report")

# ════════════════════════════════════════════════════════════════════════════
# Empty-window safety: no entries -> no crash, sensible defaults
app.JOURNAL.clear()
app.JOURNAL_KEYS.clear()
s2 = app._compute_weekly_stats()
check("empty total", s2["total_setups"] == 0)
check("empty win_rate None", s2["win_rate"] is None)
check("empty net_pnl None", s2["net_pnl"] is None)
check("empty net_r 0", s2["net_r"] == 0.0)
check("empty best None", s2["best"] is None)
_posts.clear()
app._send_weekly_report()  # must not raise on empty
check("empty report posts", len(_posts) == 3)

# ════════════════════════════════════════════════════════════════════════════
# Scheduler computes a future Friday fire time without raising
import threading
_orig_timer = threading.Timer
_captured = {}
class _FakeTimer:
    def __init__(self, delay, fn):
        _captured["delay"] = delay
        _captured["fn"] = fn
    def start(self):
        _captured["started"] = True
threading.Timer = _FakeTimer
try:
    app._schedule_weekly_report()
    check("scheduler set a timer", _captured.get("started") is True)
    check("scheduler delay positive", _captured["delay"] > 0, _captured["delay"])
    check("scheduler delay <= 7d", _captured["delay"] <= 7*24*3600 + 1, _captured["delay"])
finally:
    threading.Timer = _orig_timer

print("ALL T4 TESTS PASSED")
