"""TradeZella journal import + analysis engine (PURE — no app imports).

Phase 1 of the "TradeZella integration for bot review" feature. This module is the
single source of CSV parsing + journal analytics. It is deliberately walled off from
the live trading money path: it imports nothing from app.py, never touches the strict
gate / scoring / Discord / broker, and only transforms data.

Two public entry points:
  - parse_tradezella_csv(raw, source_tz=...) -> {"ok", "trades", "row_count", ...}
  - analyze_journal(trades) -> metrics dict (win rate, PF, expectancy, best/worst
    session/symbol/setup, common failure pattern, late-entry / wrong-stop /
    wrong-target / small-winner heuristics, recommendation)

All heuristics are clearly labelled and degrade to "needs data" when their inputs
(MFE / MAE / R multiple) are absent, rather than fabricating a verdict.
"""

import csv
import hashlib
import io
import json
import re
import statistics
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
    _ET = None

# --------------------------------------------------------------------------- #
# Header alias matching                                                        #
# --------------------------------------------------------------------------- #
# Headers are normalised to lowercase alphanumeric-only tokens ("Net P&L" ->
# "netpl", "R-Multiple" -> "rmultiple") so TradeZella's exact column names AND
# common broker synonyms both resolve. Order within each list is preference order.

_ALIASES = {
    "symbol": ["symbol", "ticker", "instrument", "contract", "asset"],
    "side": ["side", "direction", "positionside", "longshort", "tradetype",
             "type", "buysell"],
    "entry_time": ["entrytime", "entrydate", "entrydatetime", "opendate",
                   "opentime", "opendatetime", "opened", "openedat",
                   "dateopened", "timeopened", "openttime"],
    "exit_time": ["exittime", "exitdate", "exitdatetime", "closedate",
                  "closetime", "closedatetime", "closed", "closedat",
                  "dateclosed", "timeclosed"],
    "entry_price": ["entryprice", "avgentry", "averageentry", "avgentryprice",
                    "averageentryprice", "entryavg", "avgentryprc",
                    "openprice", "buyprice", "entry"],
    "exit_price": ["exitprice", "avgexit", "averageexit", "avgexitprice",
                   "averageexitprice", "exitavg", "closeprice", "sellprice",
                   "exit"],
    "quantity": ["quantity", "qty", "contracts", "numberofcontracts", "size",
                 "shares", "positionsize", "volume", "lots"],
    # Net P&L preferred; gross only as a fallback (handled in extraction).
    "pnl_net": ["netpnl", "netpl", "netprofit", "netreturn", "realizedpnl",
                "realizedpl", "pnlnet", "netgain", "netresult"],
    "pnl_any": ["pnl", "pl", "profitloss", "profit", "return", "gainloss",
                "result", "grosspnl", "grosspl", "grossprofit"],
    "fees": ["fees", "fee", "commission", "commissions", "commissionfees",
             "commissionsfees", "commissionandfees", "totalfees",
             "totalcommission", "costs"],
    "setup": ["setup", "setups", "playbook", "strategy", "strategies",
              "tags", "tag", "category"],
    "mistake": ["mistake", "mistakes", "error", "errors"],
    "notes": ["notes", "note", "comment", "comments", "journal", "description",
              "review"],
    "screenshots": ["screenshot", "screenshots", "image", "images", "imageurl",
                    "imageurls", "attachment", "attachments", "chartimage",
                    "chartimages", "chartlink", "chartlinks"],
    "mfe": ["mfe", "maxfavorableexcursion", "maximumfavorableexcursion",
            "maxfavorable", "maxfav"],
    "mae": ["mae", "maxadverseexcursion", "maximumadverseexcursion",
            "maxadverse", "maxadv"],
    "r_multiple": ["rmultiple", "rmult", "realizedr", "rvalue", "rratio",
                   "initialrmultiple", "rr", "r"],
}

_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p",
    "%m/%d/%y %H:%M:%S", "%m/%d/%y %H:%M", "%m/%d/%y",
    "%m-%d-%Y %H:%M:%S", "%m-%d-%Y %H:%M", "%m-%d-%Y",
    "%b %d, %Y %H:%M:%S", "%b %d, %Y %I:%M %p", "%b %d, %Y",
    "%B %d, %Y %H:%M:%S", "%B %d, %Y",
    "%d %b %Y %H:%M:%S", "%d %b %Y",
)

# Hold-duration thresholds for inferring trading mode when the CSV omits it.
_SCALP_MAX_MIN = 45.0      # <= 45 min hold -> SCALP
_SWING_MIN_MIN = 240.0     # >= 4 h hold   -> SWING (between -> unknown/None)

_MISTAKE_KEYWORDS = (
    "late", "chase", "chasing", "fomo", "early", "revenge", "oversize",
    "overtrade", "overtrading", "no stop", "moved stop", "no plan", "fear",
    "greed", "hesitation", "impatient", "tilt", "breakeven", "cut early",
    "let it run", "against trend", "counter trend", "news",
)


def _norm_header(h):
    return re.sub(r"[^a-z0-9]", "", (h or "").strip().lower())


def _build_header_map(fieldnames):
    """normalised-header -> original-header (first occurrence wins)."""
    out = {}
    for original in (fieldnames or []):
        key = _norm_header(original)
        if key and key not in out:
            out[key] = original
    return out


def _pick(row, header_map, field):
    """Return the raw cell value for a canonical field, or None."""
    for alias in _ALIASES.get(field, []):
        if alias in header_map:
            val = row.get(header_map[alias])
            if val is not None and str(val).strip() != "":
                return str(val).strip()
    return None


def _parse_float(s):
    if s is None:
        return None
    txt = str(s).strip()
    if txt == "" or txt.lower() in ("n/a", "na", "none", "null", "-", "--"):
        return None
    neg = False
    if txt.startswith("(") and txt.endswith(")"):
        neg = True
        txt = txt[1:-1]
    txt = txt.replace("$", "").replace(",", "").replace("%", "").replace("R", "")
    txt = txt.replace("+", "").strip()
    if txt in ("", "-", "."):
        return None
    try:
        val = float(txt)
    except ValueError:
        return None
    return -val if neg else val


def _parse_dt(s, source_tz=None):
    """Parse a flexible datetime string into an aware UTC datetime, or None."""
    if s is None:
        return None
    txt = str(s).strip()
    if txt == "":
        return None
    tz = source_tz if source_tz is not None else _ET
    # Epoch seconds / millis.
    if re.fullmatch(r"\d{10}", txt):
        try:
            return datetime.fromtimestamp(int(txt), tz=timezone.utc)
        except (ValueError, OSError):
            pass
    if re.fullmatch(r"\d{13}", txt):
        try:
            return datetime.fromtimestamp(int(txt) / 1000.0, tz=timezone.utc)
        except (ValueError, OSError):
            pass
    iso = txt.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        dt = None
    if dt is None:
        for fmt in _DT_FORMATS:
            try:
                dt = datetime.strptime(txt, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        if tz is not None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_side(raw):
    if raw is None:
        return None
    t = str(raw).strip().lower()
    if t in ("long", "buy", "b", "bought", "l"):
        return "long"
    if t in ("short", "sell", "s", "sold"):
        return "short"
    if "long" in t or "buy" in t:
        return "long"
    if "short" in t or "sell" in t:
        return "short"
    return t or None


def session_bucket_et(dt_utc):
    """Bucket an aware UTC datetime into an ET trading session label."""
    if dt_utc is None:
        return None
    try:
        et = dt_utc.astimezone(_ET) if _ET is not None else dt_utc
    except Exception:
        return None
    h = et.hour
    if 18 <= h or h < 3:
        return "Overnight (Asia)"
    if 3 <= h < 8:
        return "London"
    if 8 <= h < 12:
        return "NY AM"
    if 12 <= h < 17:
        return "NY PM"
    return "Maintenance"


def _session_day_et(dt_utc):
    if dt_utc is None:
        return None
    try:
        et = dt_utc.astimezone(_ET) if _ET is not None else dt_utc
    except Exception:
        return None
    return et.date().isoformat()


def _infer_mode(entry_dt, exit_dt):
    if entry_dt is None or exit_dt is None:
        return None
    mins = (exit_dt - entry_dt).total_seconds() / 60.0
    if mins <= 0:
        return None
    if mins <= _SCALP_MAX_MIN:
        return "SCALP"
    if mins >= _SWING_MIN_MIN:
        return "SWING"
    return None


def _dedupe_key(symbol, side, entry_dt, exit_dt, entry_price, exit_price,
                quantity, pnl):
    def _n(x):
        if x is None:
            return ""
        if isinstance(x, float):
            return "{:.6f}".format(x)
        return str(x)

    parts = [
        (symbol or "").upper(),
        (side or ""),
        entry_dt.isoformat() if entry_dt else "",
        exit_dt.isoformat() if exit_dt else "",
        _n(entry_price), _n(exit_price), _n(quantity), _n(pnl),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_tradezella_csv(raw, source_tz=None):
    """Parse a TradeZella (or generic broker) trades CSV into canonical records.

    Returns {"ok": bool, "error"?, "trades": [...], "row_count", "skipped",
             "fields_present": {...}, "warnings": [...]}.
    Each trade dict carries: symbol, side, entry_time/exit_time (ISO UTC),
    entry_price, exit_price, quantity, pnl, fees, setup, mistake, notes,
    screenshots, mfe, mae, r_multiple, mode, session_bucket, session_day,
    outcome, dedupe_key, raw_row (dict).
    """
    if not raw or not str(raw).strip():
        return {"ok": False, "error": "empty upload body"}
    try:
        reader = csv.DictReader(io.StringIO(raw))
        fieldnames = reader.fieldnames
    except Exception as exc:  # pragma: no cover - csv rarely raises on init
        return {"ok": False, "error": "could not read CSV: {}".format(exc)}
    if not fieldnames:
        return {"ok": False, "error": "CSV has no header row"}

    header_map = _build_header_map(fieldnames)
    # Require at least a symbol-ish and a P&L-ish column to be a usable journal.
    has_symbol = any(a in header_map for a in _ALIASES["symbol"])
    has_pnl = (any(a in header_map for a in _ALIASES["pnl_net"]) or
               any(a in header_map for a in _ALIASES["pnl_any"]))
    if not has_symbol and not has_pnl:
        return {"ok": False,
                "error": ("CSV does not look like a trade export "
                          "(no symbol or P&L column found)")}

    trades = []
    skipped = 0
    warnings = []
    seen_keys = set()

    for row in reader:
        if not row:
            continue
        symbol = _pick(row, header_map, "symbol")
        if symbol:
            symbol = symbol.strip().upper()
        side = _normalize_side(_pick(row, header_map, "side"))
        entry_dt = _parse_dt(_pick(row, header_map, "entry_time"), source_tz)
        exit_dt = _parse_dt(_pick(row, header_map, "exit_time"), source_tz)
        entry_price = _parse_float(_pick(row, header_map, "entry_price"))
        exit_price = _parse_float(_pick(row, header_map, "exit_price"))
        quantity = _parse_float(_pick(row, header_map, "quantity"))
        pnl = _parse_float(_pick(row, header_map, "pnl_net"))
        if pnl is None:
            pnl = _parse_float(_pick(row, header_map, "pnl_any"))
        fees = _parse_float(_pick(row, header_map, "fees"))
        setup = _pick(row, header_map, "setup")
        mistake = _pick(row, header_map, "mistake")
        notes = _pick(row, header_map, "notes")
        screenshots = _pick(row, header_map, "screenshots")
        mfe = _parse_float(_pick(row, header_map, "mfe"))
        mae = _parse_float(_pick(row, header_map, "mae"))
        r_multiple = _parse_float(_pick(row, header_map, "r_multiple"))

        # A row with no symbol AND no P&L AND no prices is junk (blank line etc.)
        if (symbol is None and pnl is None and entry_price is None
                and exit_price is None):
            skipped += 1
            continue

        if pnl is None:
            outcome = "unknown"
        elif pnl > 0:
            outcome = "win"
        elif pnl < 0:
            outcome = "loss"
        else:
            outcome = "scratch"

        mode = _infer_mode(entry_dt, exit_dt)
        bucket = session_bucket_et(entry_dt)
        sday = _session_day_et(entry_dt)
        key = _dedupe_key(symbol, side, entry_dt, exit_dt, entry_price,
                          exit_price, quantity, pnl)
        if key in seen_keys:
            skipped += 1
            continue
        seen_keys.add(key)

        trades.append({
            "symbol": symbol,
            "side": side,
            "entry_time": entry_dt.isoformat() if entry_dt else None,
            "exit_time": exit_dt.isoformat() if exit_dt else None,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": pnl,
            "fees": fees,
            "setup": setup,
            "mistake": mistake,
            "notes": notes,
            "screenshots": screenshots,
            "mfe": mfe,
            "mae": mae,
            "r_multiple": r_multiple,
            "mode": mode,
            "session_bucket": bucket,
            "session_day": sday,
            "outcome": outcome,
            "dedupe_key": key,
            "raw_row": {k: v for k, v in row.items() if k is not None},
        })

    fields_present = {
        f: (any(a in header_map for a in aliases))
        for f, aliases in _ALIASES.items()
    }
    if not trades:
        warnings.append("no usable trade rows were found")

    return {
        "ok": True,
        "trades": trades,
        "row_count": len(trades),
        "skipped": skipped,
        "fields_present": fields_present,
        "columns": list(fieldnames),
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# Analysis                                                                     #
# --------------------------------------------------------------------------- #

def _mean(xs):
    return statistics.fmean(xs) if xs else None


def _group_perf(trades, keyfn):
    """Group decided trades by key -> {count, wins, losses, net, win_rate, avg}."""
    groups = {}
    for t in trades:
        if t.get("outcome") not in ("win", "loss"):
            continue
        k = keyfn(t)
        if not k:
            continue
        g = groups.setdefault(k, {"count": 0, "wins": 0, "losses": 0,
                                  "net": 0.0})
        g["count"] += 1
        if t["outcome"] == "win":
            g["wins"] += 1
        else:
            g["losses"] += 1
        g["net"] += t.get("pnl") or 0.0
    for k, g in groups.items():
        g["win_rate"] = (g["wins"] / g["count"]) if g["count"] else 0.0
        g["avg"] = (g["net"] / g["count"]) if g["count"] else 0.0
    return groups


def _best_worst(groups, min_count=3):
    """Pick best/worst group by net P&L, preferring groups with >= min_count."""
    if not groups:
        return None, None
    eligible = {k: g for k, g in groups.items() if g["count"] >= min_count}
    pool = eligible if eligible else groups
    best = max(pool.items(), key=lambda kv: kv[1]["net"])
    worst = min(pool.items(), key=lambda kv: kv[1]["net"])

    def _fmt(item):
        k, g = item
        return {
            "label": k, "count": g["count"], "wins": g["wins"],
            "losses": g["losses"], "net_pnl": round(g["net"], 2),
            "win_rate": round(g["win_rate"], 4), "avg_pnl": round(g["avg"], 2),
            "low_sample": g["count"] < min_count,
        }

    return _fmt(best), _fmt(worst)


def _common_failure_pattern(trades):
    losers = [t for t in trades if t.get("outcome") == "loss"]
    if not losers:
        return {"available": False,
                "detail": "no losing trades to analyse"}
    setup_counts = {}
    for t in losers:
        s = (t.get("setup") or "").strip()
        if s:
            setup_counts[s] = setup_counts.get(s, 0) + 1
    top_setup = None
    if setup_counts:
        label, n = max(setup_counts.items(), key=lambda kv: kv[1])
        top_setup = {"setup": label, "count": n,
                     "share": round(n / len(losers), 4)}
    kw_counts = {}
    for t in losers:
        blob = " ".join([str(t.get("mistake") or ""),
                         str(t.get("notes") or "")]).lower()
        for kw in _MISTAKE_KEYWORDS:
            if kw in blob:
                kw_counts[kw] = kw_counts.get(kw, 0) + 1
    top_mistake = None
    if kw_counts:
        label, n = max(kw_counts.items(), key=lambda kv: kv[1])
        top_mistake = {"keyword": label, "count": n,
                       "share": round(n / len(losers), 4)}
    return {
        "available": True,
        "loser_count": len(losers),
        "top_losing_setup": top_setup,
        "top_mistake_keyword": top_mistake,
    }


def _late_entry_signal(trades, winners, losers):
    """Heuristic: large adverse excursion right after entry => late / chasing."""
    pairs = [(abs(t["mae"]), abs(t.get("mfe") or 0.0))
             for t in trades
             if t.get("mae") is not None]
    note_hits = sum(
        1 for t in trades
        if any(k in (str(t.get("mistake") or "") + " "
                     + str(t.get("notes") or "")).lower()
               for k in ("late", "chase", "chasing", "fomo"))
    )
    if not pairs and note_hits == 0:
        return {"available": False,
                "detail": "needs MFE/MAE data or note tags to assess entry timing"}
    ratios = [mae / (mae + mfe) for mae, mfe in pairs if (mae + mfe) > 0]
    med = statistics.median(ratios) if ratios else None
    late = False
    reasons = []
    if med is not None and med > 0.45:
        late = True
        reasons.append(
            "median adverse-excursion share {:.0%} of total range (>45%)".format(med))
    if note_hits:
        late = True
        reasons.append("{} trade(s) tagged late/chase/FOMO".format(note_hits))
    return {
        "available": True,
        "entries_late": late,
        "median_mae_share": round(med, 4) if med is not None else None,
        "note_hits": note_hits,
        "detail": ("; ".join(reasons) if reasons
                   else "entries generally taken before adverse excursion"),
    }


def _stops_signal(losers):
    """Heuristic: loser R distribution => stops too wide/loose."""
    r_losers = [t["r_multiple"] for t in losers if t.get("r_multiple") is not None]
    if not r_losers:
        return {"available": False,
                "detail": "needs R-multiple data to assess stop placement"}
    avg_loss_r = _mean(r_losers)
    worse_than_1r = sum(1 for r in r_losers if r < -1.15)
    too_loose = (avg_loss_r is not None and avg_loss_r < -1.15) or \
                (worse_than_1r / len(r_losers) > 0.30)
    return {
        "available": True,
        "avg_loser_r": round(avg_loss_r, 3) if avg_loss_r is not None else None,
        "losers_beyond_1r": worse_than_1r,
        "stops_too_wide": too_loose,
        "detail": ("losers average {:.2f}R — stops may be too wide / not honoured"
                   .format(avg_loss_r) if too_loose
                   else "loser sizes are within ~1R — stop discipline looks ok"),
    }


def _targets_signal(winners):
    """Heuristic: winners give back much of MFE => targets too tight / late exits."""
    caps = []
    for t in winners:
        mfe = t.get("mfe")
        pnl = t.get("pnl")
        if mfe and mfe > 0 and pnl is not None:
            caps.append(max(0.0, min(1.0, pnl / mfe)))
    if not caps:
        return {"available": False,
                "detail": "needs MFE data to assess target capture"}
    avg_cap = _mean(caps)
    too_tight = avg_cap is not None and avg_cap < 0.45
    return {
        "available": True,
        "avg_capture": round(avg_cap, 4) if avg_cap is not None else None,
        "targets_too_tight": too_tight,
        "detail": ("winners capture only {:.0%} of their peak move — targets "
                   "may be too tight or exits late".format(avg_cap) if too_tight
                   else "winners capture a healthy share of their peak move"),
    }


def analyze_journal(trades):
    """Compute the full review from a list of canonical trade dicts."""
    trades = list(trades or [])
    total = len(trades)
    decided = [t for t in trades if t.get("outcome") in ("win", "loss")]
    winners = [t for t in decided if t["outcome"] == "win"]
    losers = [t for t in decided if t["outcome"] == "loss"]
    n_dec = len(decided)

    win_rate = (len(winners) / n_dec) if n_dec else None
    win_pnls = [t.get("pnl") or 0.0 for t in winners]
    loss_pnls = [t.get("pnl") or 0.0 for t in losers]
    avg_winner = _mean(win_pnls)
    avg_loser = _mean(loss_pnls)
    gross_profit = sum(win_pnls) if win_pnls else 0.0
    gross_loss = abs(sum(loss_pnls)) if loss_pnls else 0.0
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 3)
    elif gross_profit > 0:
        profit_factor = None  # undefined (no losses) -> shown as inf upstream
    else:
        profit_factor = 0.0
    net_pnl = round(gross_profit - gross_loss, 2)
    expectancy = _mean([t.get("pnl") or 0.0 for t in decided])
    r_vals = [t["r_multiple"] for t in decided if t.get("r_multiple") is not None]
    expectancy_r = _mean(r_vals)

    payoff = None
    if avg_winner is not None and avg_loser not in (None, 0.0):
        payoff = abs(avg_winner) / abs(avg_loser)
    breakeven_payoff = None
    if win_rate not in (None, 0.0, 1.0):
        breakeven_payoff = (1.0 - win_rate) / win_rate
    winners_too_small = None
    small_detail = "needs winners and losers to compare"
    if payoff is not None and breakeven_payoff is not None:
        winners_too_small = payoff < breakeven_payoff
        small_detail = (
            "avg winner is {:.2f}x avg loser vs {:.2f}x needed to break even at "
            "this win rate — winners are too small".format(payoff, breakeven_payoff)
            if winners_too_small else
            "winner/loser payoff ({:.2f}x) clears the {:.2f}x breakeven for this "
            "win rate".format(payoff, breakeven_payoff))
    elif avg_winner is not None and avg_loser is not None and avg_loser != 0:
        winners_too_small = abs(avg_winner) < abs(avg_loser)
        small_detail = ("avg winner {:.2f} vs avg loser {:.2f}"
                        .format(avg_winner, avg_loser))

    sessions = _group_perf(decided, lambda t: t.get("session_bucket"))
    symbols = _group_perf(decided, lambda t: t.get("symbol"))
    setups = _group_perf(decided, lambda t: (t.get("setup") or "").strip() or None)
    best_session, worst_session = _best_worst(sessions)
    best_symbol, worst_symbol = _best_worst(symbols)
    best_setup, worst_setup = _best_worst(setups)

    late = _late_entry_signal(trades, winners, losers)
    stops = _stops_signal(losers)
    targets = _targets_signal(winners)
    failure = _common_failure_pattern(decided)

    recommendation = _build_recommendation(
        total, n_dec, win_rate, profit_factor, gross_loss, gross_profit,
        winners_too_small, small_detail, late, stops, targets, failure,
        worst_setup, best_setup)

    return {
        "trade_count": total,
        "decided_count": n_dec,
        "wins": len(winners),
        "losses": len(losers),
        "scratches": sum(1 for t in trades if t.get("outcome") == "scratch"),
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "avg_winner": round(avg_winner, 2) if avg_winner is not None else None,
        "avg_loser": round(avg_loser, 2) if avg_loser is not None else None,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": net_pnl,
        "profit_factor": profit_factor,
        "profit_factor_infinite": (gross_loss == 0 and gross_profit > 0),
        "expectancy": round(expectancy, 2) if expectancy is not None else None,
        "expectancy_r": round(expectancy_r, 3) if expectancy_r is not None else None,
        "avg_r": round(expectancy_r, 3) if expectancy_r is not None else None,
        "payoff_ratio": round(payoff, 3) if payoff is not None else None,
        "breakeven_payoff": (round(breakeven_payoff, 3)
                             if breakeven_payoff is not None else None),
        "best_session": best_session, "worst_session": worst_session,
        "best_symbol": best_symbol, "worst_symbol": worst_symbol,
        "best_setup": best_setup, "worst_setup": worst_setup,
        "common_failure_pattern": failure,
        "entries_late": late,
        "stops_assessment": stops,
        "targets_assessment": targets,
        "winners_too_small": {"available": winners_too_small is not None,
                              "flag": winners_too_small, "detail": small_detail},
        "recommendation": recommendation,
    }


def _build_recommendation(total, n_dec, win_rate, profit_factor, gross_loss,
                          gross_profit, winners_too_small, small_detail, late,
                          stops, targets, failure, worst_setup, best_setup):
    if total == 0:
        return "No TradeZella trades imported yet — upload a CSV to get a review."
    if n_dec == 0:
        return ("Imported {} trade(s) but none have a decisive P&L to score yet."
                .format(total))
    tips = []
    if winners_too_small:
        tips.append("let winners run — your payoff is below breakeven for this win rate")
    if late.get("available") and late.get("entries_late"):
        tips.append("entries look late/chased — wait for pullbacks into your level")
    if stops.get("available") and stops.get("stops_too_wide"):
        tips.append("tighten or honour stops — losers are running past 1R")
    if targets.get("available") and targets.get("targets_too_tight"):
        tips.append("targets may be too tight — you give back most of the move")
    if (failure.get("available") and failure.get("top_losing_setup")
            and failure["top_losing_setup"]["share"] >= 0.4):
        tips.append("cut the '{}' setup — it drives most of your losses"
                    .format(failure["top_losing_setup"]["setup"]))
    if worst_setup and worst_setup.get("net_pnl", 0) < 0 and not worst_setup.get("low_sample"):
        tips.append("'{}' is your worst setup (net {:.0f})"
                    .format(worst_setup["label"], worst_setup["net_pnl"]))
    pf_txt = ("PF infinite" if (gross_loss == 0 and gross_profit > 0)
              else ("PF {:.2f}".format(profit_factor)
                    if profit_factor is not None else "PF n/a"))
    wr_txt = "win rate {:.0%}".format(win_rate) if win_rate is not None else "win rate n/a"
    if not tips:
        if best_setup and best_setup.get("net_pnl", 0) > 0:
            return ("Solid journal ({}, {}). Lean into your '{}' setup — keep doing "
                    "what works.".format(wr_txt, pf_txt, best_setup["label"]))
        return "Solid journal ({}, {}). Keep executing your plan.".format(wr_txt, pf_txt)
    return "{}, {}. Focus: ".format(wr_txt, pf_txt) + "; ".join(tips[:3]) + "."


def build_reviews(analysis):
    """DISPLAY-ONLY presenter: fold the analyze_journal output into two trader-facing
    review blocks — an ENTRY-quality review (are entries late / chased?) and an
    EXIT-management review (are targets too tight / stops wrong / winners too small?).

    Pure, no side effects, no money-path coupling. Reuses the already-computed
    heuristics in `analysis`; never recomputes from raw trades. Returns a stable
    schema even when under-sampled (available=False, verdict="needs data")."""
    a = analysis or {}

    # ── Entry-quality review (timing / location) ─────────────────────────────
    late = a.get("entries_late") or {}
    failure = a.get("common_failure_pattern") or {}
    entry_signals = []
    entry_flag = False
    entry_available = bool(late.get("available"))
    if entry_available:
        if late.get("entries_late"):
            entry_flag = True
        if late.get("detail"):
            entry_signals.append(late["detail"])
    tl = failure.get("top_losing_setup") if failure.get("available") else None
    if tl and (tl.get("share") or 0) >= 0.4 and tl.get("setup"):
        entry_signals.append(
            "most losses cluster in the '{}' setup ({:.0%} of losers)".format(
                tl["setup"], tl.get("share") or 0))
    entry_verdict = ("needs data" if not entry_available
                     else ("review entries" if entry_flag else "entries look healthy"))
    entry_headline = ("Upload MFE/MAE or note tags to assess entry timing."
                      if not entry_available
                      else ("Entries look late or chased — wait for pullbacks into "
                            "your level." if entry_flag
                            else "Entry timing looks disciplined."))
    entry_review = {
        "available": entry_available,
        "flag": entry_flag,
        "verdict": entry_verdict,
        "headline": entry_headline,
        "median_mae_share": late.get("median_mae_share"),
        "note_hits": late.get("note_hits"),
        "signals": entry_signals,
    }

    # ── Exit-management review (targets / stops / R realized) ─────────────────
    targets = a.get("targets_assessment") or {}
    stops = a.get("stops_assessment") or {}
    small = a.get("winners_too_small") or {}
    exit_signals = []
    exit_flag = False
    if targets.get("available"):
        if targets.get("targets_too_tight"):
            exit_flag = True
        if targets.get("detail"):
            exit_signals.append(targets["detail"])
    if stops.get("available"):
        if stops.get("stops_too_wide"):
            exit_flag = True
        if stops.get("detail"):
            exit_signals.append(stops["detail"])
    if small.get("available"):
        if small.get("flag"):
            exit_flag = True
        if small.get("detail"):
            exit_signals.append(small["detail"])
    exit_available = any(blk.get("available") for blk in (targets, stops, small))
    exit_verdict = ("needs data" if not exit_available
                    else ("review exits" if exit_flag else "exits look healthy"))
    exit_headline = ("Upload R-multiple / MFE data to assess exit management."
                     if not exit_available
                     else ("Exit management is leaking edge — see the signals below."
                           if exit_flag
                           else "Exit management looks healthy."))
    exit_review = {
        "available": exit_available,
        "flag": exit_flag,
        "verdict": exit_verdict,
        "headline": exit_headline,
        "avg_capture": targets.get("avg_capture"),
        "avg_loser_r": stops.get("avg_loser_r"),
        "signals": exit_signals,
    }

    return {"entry_review": entry_review, "exit_review": exit_review}
