"""MICRO SCALP MODE — pure liquidity-moment detection engine (DISPLAY + GHOST ONLY).

Completely walled off from the money path, exactly like scalp_live_sim.py:
  * imports NOTHING from app.py — consumes one flat, read-only context dict,
  * never sends orders, never touches the gate / verdict / trade_plan / edge score,
  * every read is l.get(...) with a safe default — a missing key can never raise,
  * evaluate() ALWAYS returns the stable schema block (fail-open to WAIT/NO TRADE).

The engine hunts short-term liquidity "moments", not full strategy confirmations:
  1. Liquidity sweep      — prior high/low taken (sweep alert or a price-trail poke
                            beyond the recent range that snapped back inside).
  2. Trap                 — who is stuck? A high sweep that failed to continue traps
                            LONGS (short bias); a failed low sweep traps SHORTS.
                            No trapped traders  ==>  NO TRADE (hard rule).
  3. Absorption           — effort without result: volume spike / elevated RVOL (or a
                            CVD push) while price makes no progress. Evidence booster.
  4. Micro trigger        — fast confirmation only (micro CHOCH / delta flip / VWAP
                            reclaim-reject / close back inside range). NEVER the slow
                            5-minute confirmation. Required for a TAKE verdict.
  5. Geometry             — structure-based stop behind the sweep extreme (clamped to
                            the per-instrument volatility cap), fixed micro targets
                            (MNQ: TP1 +5 / TP2 +10 / runner), BE only after TP1.

GHOST lifecycle helpers at the bottom are pure per-bar resolvers the app-side watcher
uses to track trades the bot WOULD have taken (no orders — analysis + logging only).
"""

# ── Tunables (points/seconds; the app passes per-instrument specs in the ctx) ──
SWEEP_TTL_SEC          = 900    # a sweep older than 15 min is no longer a "moment"
SWEEP_FORMING_SEC      = 120    # younger than this = still forming (WAIT, not NO TRADE)
TRAP_MIN_AGE_SEC       = 30     # sweep must be at least this old to judge follow-through
TRIGGER_TTL_SEC        = 300    # micro trigger evidence must be fresher than 5 min
VOLUME_FRESH_SEC       = 600    # volume spike counts as fresh for 10 min
CVD_FRESH_SEC          = 900    # CVD state counts as fresh for 15 min
TRAIL_WINDOW_SEC       = 300    # price-trail window used for progress / reclaim reads
STALL_ATR_FRACTION     = 0.40   # "no progress" = trail range < this fraction of ATR
STOP_BUFFER_ATR_FRAC   = 0.25   # stop buffer beyond the sweep extreme (fraction of ATR)

# Fallback micro specs if the app doesn't supply one for the instrument (MNQ scale).
_DEFAULT_SPECS = {"tp1": 5.0, "tp2": 10.0, "runner": 20.0,
                  "stop_min": 3.0, "stop_max": 8.0, "tick": 0.25}

_SCHEMA_KEYS = (
    "enabled", "available", "instrument", "generated_at", "market_open",
    "liquidity_event", "sweep_direction", "sweep_price", "sweep_age_sec",
    "trapped_side", "absorption", "micro_trigger", "trigger_reason",
    "verdict", "direction", "setup_type", "entry_reason", "invalidation_level",
    "suggested_entry", "suggested_stop", "stop_points", "tp1", "tp2",
    "runner_target", "evidence", "summary",
)


def neutral(reason="Micro Scalp Mode is OFF.", instrument=None, enabled=False,
            generated_at=None):
    """Stable neutral block — the schema contract for the overlay. Never raises."""
    return {
        "enabled":           bool(enabled),
        "available":         False,
        "instrument":        instrument,
        "generated_at":      generated_at,
        "market_open":       None,
        "liquidity_event":   False,
        "sweep_direction":   "NONE",
        "sweep_price":       None,
        "sweep_age_sec":     None,
        "trapped_side":      "NONE",
        "absorption":        False,
        "micro_trigger":     False,
        "trigger_reason":    None,
        "verdict":           "NO TRADE",
        "direction":         None,
        "setup_type":        None,
        "entry_reason":      None,
        "invalidation_level": None,
        "suggested_entry":   None,
        "suggested_stop":    None,
        "stop_points":       None,
        "tp1":               None,
        "tp2":               None,
        "runner_target":     None,
        "evidence":          [],
        "summary":           reason,
    }


def _f(v):
    """Float or None — the only numeric coercion used anywhere in this module."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _round_tick(px, tick):
    if px is None:
        return None
    t = _f(tick) or 0.01
    try:
        return round(round(px / t) * t, 4)
    except (TypeError, ValueError, ZeroDivisionError):
        return round(px, 4)


def _trail_window(l, window_sec=TRAIL_WINDOW_SEC):
    """Trail points inside the window, oldest→newest. Each point {price, epoch}."""
    now_ep = _f(l.get("now_epoch"))
    out = []
    for p in (l.get("price_trail") or []):
        px, ep = _f(p.get("price")), _f(p.get("epoch"))
        if px is None or ep is None:
            continue
        if now_ep is not None and (now_ep - ep) > window_sec:
            continue
        out.append({"price": px, "epoch": ep})
    out.sort(key=lambda p: p["epoch"])
    return out


# ── 1. Liquidity sweep detection ─────────────────────────────────────────────
def detect_liquidity_event(l):
    """Newest fresh sweep — from sweep webhooks first, then a price-trail poke
    beyond the recent range that came back inside. Returns the schema fragment."""
    out = {"liquidity_event": False, "sweep_direction": "NONE", "sweep_price": None,
           "sweep_age_sec": None, "evidence": []}
    best = None
    for s in (l.get("sweeps") or []):
        age = _f(s.get("age_sec"))
        d = s.get("direction")
        if age is None or age > SWEEP_TTL_SEC or d not in ("high", "low"):
            continue
        if best is None or age < best["age"]:
            best = {"dir": d, "price": _f(s.get("price")), "age": age, "src": "sweep alert"}
    if best is None:
        # Fallback: trail poke beyond the range that snapped back inside.
        price = _f(l.get("price"))
        rh, rl = _f(l.get("range_high")), _f(l.get("range_low"))
        trail = _trail_window(l, SWEEP_TTL_SEC)
        now_ep = _f(l.get("now_epoch"))
        if price is not None and trail:
            if rh is not None and price < rh:
                pokes = [p for p in trail if p["price"] > rh]
                if pokes:
                    top = max(pokes, key=lambda p: p["price"])
                    age = (now_ep - top["epoch"]) if now_ep is not None else None
                    best = {"dir": "high", "price": top["price"], "age": age,
                            "src": "price poked above range high then came back inside"}
            if best is None and rl is not None and price > rl:
                pokes = [p for p in trail if p["price"] < rl]
                if pokes:
                    bot = min(pokes, key=lambda p: p["price"])
                    age = (now_ep - bot["epoch"]) if now_ep is not None else None
                    best = {"dir": "low", "price": bot["price"], "age": age,
                            "src": "price poked below range low then came back inside"}
    if best is None:
        return out
    out["liquidity_event"] = True
    out["sweep_direction"] = "HIGH SWEEP" if best["dir"] == "high" else "LOW SWEEP"
    out["sweep_price"]     = best["price"]
    out["sweep_age_sec"]   = round(best["age"], 1) if best["age"] is not None else None
    out["evidence"].append("Liquidity event: %s (%s)" % (out["sweep_direction"], best["src"]))
    return out


# ── 2. Trap detection ────────────────────────────────────────────────────────
def detect_trap(l, sweep):
    """Who is stuck? A HIGH sweep whose follow-through failed traps LONGS (short
    bias); a failed LOW sweep traps SHORTS (long bias). Requires the price to be
    back on the wrong side of the swept level — that IS the failure to continue."""
    out = {"trapped_side": "NONE", "evidence": [], "forming": False}
    if not sweep.get("liquidity_event"):
        return out
    price = _f(l.get("price"))
    swept = _f(sweep.get("sweep_price"))
    age   = _f(sweep.get("sweep_age_sec"))
    if price is None or swept is None:
        return out
    if age is not None and age < TRAP_MIN_AGE_SEC:
        out["forming"] = True           # too fresh to judge follow-through yet
        return out
    cvd       = (l.get("cvd_state") or "").lower()
    cvd_fresh = ((_f(l.get("cvd_age_sec")) or 1e9) <= CVD_FRESH_SEC)
    if sweep["sweep_direction"] == "HIGH SWEEP":
        if price < swept:               # took the highs, now back below = longs stuck
            out["trapped_side"] = "LONGS"
            out["evidence"].append(
                "Longs trapped: highs swept at %.2f but price failed to continue (now %.2f)"
                % (swept, price))
            if cvd == "bearish" and cvd_fresh:
                out["evidence"].append("CVD bearish while price is back below the sweep")
    else:                               # LOW SWEEP
        if price > swept:               # took the lows, now back above = shorts stuck
            out["trapped_side"] = "SHORTS"
            out["evidence"].append(
                "Shorts trapped: lows swept at %.2f but price failed to continue (now %.2f)"
                % (swept, price))
            if cvd == "bullish" and cvd_fresh:
                out["evidence"].append("CVD bullish while price is back above the sweep")
    return out


# ── 3. Absorption detection ──────────────────────────────────────────────────
def detect_absorption(l):
    """Effort without result: fresh volume (spike or elevated RVOL) or a fresh CVD
    push while the recent trail shows almost no price progress vs ATR."""
    out = {"absorption": False, "evidence": []}
    atr = _f(l.get("atr_pts"))
    trail = _trail_window(l)
    if atr is None or atr <= 0 or len(trail) < 3:
        return out
    prices = [p["price"] for p in trail]
    progress = max(prices) - min(prices)
    stalled = progress < (STALL_ATR_FRACTION * atr)
    if not stalled:
        return out
    vol_age = _f(l.get("volume_spike_age_sec"))
    rvol    = _f(l.get("rvol"))
    cvd     = (l.get("cvd_state") or "").lower()
    cvd_fresh = ((_f(l.get("cvd_age_sec")) or 1e9) <= CVD_FRESH_SEC)
    effort = []
    if vol_age is not None and vol_age <= VOLUME_FRESH_SEC:
        effort.append("volume spike %.0fs ago" % vol_age)
    if rvol is not None and rvol >= 1.5:
        effort.append("RVOL %.2f" % rvol)
    if cvd in ("bullish", "bearish") and cvd_fresh:
        effort.append("CVD pushing %s" % cvd)
    if not effort:
        return out
    out["absorption"] = True
    out["evidence"].append(
        "Absorption: %s but price stalled (%.2f pts range vs ATR %.2f)"
        % (" + ".join(effort), progress, atr))
    return out


# ── 4. Micro trigger ─────────────────────────────────────────────────────────
def detect_micro_trigger(l, trade_dir, sweep):
    """Fast confirmation in the TRADE direction. Priority order:
    fresh micro event (CHOCH / delta flip / VWAP reclaim / sweep-reclaim webhook)
    -> derived VWAP reclaim/reject from the trail -> close back inside range."""
    out = {"micro_trigger": False, "trigger_reason": None, "evidence": []}
    if trade_dir not in ("Long", "Short"):
        return out
    # a) Fresh aligned micro event from the webhook stream.
    for ev in (l.get("micro_events") or []):
        age = _f(ev.get("age_sec"))
        if age is None or age > TRIGGER_TTL_SEC:
            continue
        if ev.get("direction") != trade_dir:
            continue
        cat = ev.get("cat") or "micro event"
        out["micro_trigger"] = True
        out["trigger_reason"] = "%s (%s, %.0fs ago)" % (cat, trade_dir, age)
        out["evidence"].append("Micro trigger: fresh %s in trade direction" % cat)
        return out
    # b) Derived VWAP reclaim (Long) / rejection-loss (Short) from the price trail.
    vwap  = _f(l.get("vwap"))
    price = _f(l.get("price"))
    trail = _trail_window(l)
    if vwap is not None and price is not None and trail:
        if trade_dir == "Long" and price > vwap and any(p["price"] < vwap for p in trail):
            out["micro_trigger"] = True
            out["trigger_reason"] = "VWAP reclaim (crossed above %.2f)" % vwap
            out["evidence"].append("Micro trigger: VWAP reclaimed in the last few minutes")
            return out
        if trade_dir == "Short" and price < vwap and any(p["price"] > vwap for p in trail):
            out["micro_trigger"] = True
            out["trigger_reason"] = "VWAP rejection (lost %.2f)" % vwap
            out["evidence"].append("Micro trigger: VWAP lost/rejected in the last few minutes")
            return out
    # c) Close back inside the range after the sweep (the reclaim of the failed level).
    rh, rl = _f(l.get("range_high")), _f(l.get("range_low"))
    age = _f(sweep.get("sweep_age_sec"))
    if price is not None and age is not None and age <= TRIGGER_TTL_SEC:
        if trade_dir == "Short" and rh is not None and price < rh \
                and sweep.get("sweep_direction") == "HIGH SWEEP":
            out["micro_trigger"] = True
            out["trigger_reason"] = "Back inside range (below %.2f) after the high sweep" % rh
            out["evidence"].append("Micro trigger: price closed back inside the range")
        elif trade_dir == "Long" and rl is not None and price > rl \
                and sweep.get("sweep_direction") == "LOW SWEEP":
            out["micro_trigger"] = True
            out["trigger_reason"] = "Back inside range (above %.2f) after the low sweep" % rl
            out["evidence"].append("Micro trigger: price closed back inside the range")
    return out


# ── 5. Geometry (structure-based stop, fixed micro targets, BE after TP1) ────
def build_geometry(l, trade_dir, sweep_price):
    """Entry at market, stop behind the sweep extreme (never random), clamped to
    the per-instrument min/max stop. Targets are the fixed micro TPs."""
    price = _f(l.get("price"))
    if price is None or trade_dir not in ("Long", "Short"):
        return None
    specs = dict(_DEFAULT_SPECS)
    for k, v in (l.get("specs") or {}).items():
        fv = _f(v)
        if fv is not None:
            specs[k] = fv
    tick = specs.get("tick") or 0.25
    atr  = _f(l.get("atr_pts")) or 0.0
    buf  = max(STOP_BUFFER_ATR_FRAC * atr, 2 * tick)
    sgn  = 1.0 if trade_dir == "Long" else -1.0
    swept = _f(sweep_price)
    if swept is not None:
        stop = swept - buf if trade_dir == "Long" else swept + buf
        note = "behind the sweep extreme"
    else:
        stop = price - sgn * specs["stop_max"]
        note = "volatility cap (no sweep anchor)"
    risk = (price - stop) * sgn
    if risk > specs["stop_max"]:
        stop = price - sgn * specs["stop_max"]
        risk = specs["stop_max"]
        note += ", clamped to max stop"
    elif risk < specs["stop_min"]:
        stop = price - sgn * specs["stop_min"]
        risk = specs["stop_min"]
        note += ", widened to min stop"
    return {
        "suggested_entry": _round_tick(price, tick),
        "suggested_stop":  _round_tick(stop, tick),
        "stop_points":     round(risk, 2),
        "stop_note":       note,
        "tp1":             _round_tick(price + sgn * specs["tp1"], tick),
        "tp2":             _round_tick(price + sgn * specs["tp2"], tick),
        "runner_target":   _round_tick(price + sgn * specs["runner"], tick),
    }


# ── Orchestrator ─────────────────────────────────────────────────────────────
def evaluate(l):
    """Full micro-scalp read for one instrument. ALWAYS returns the stable schema
    block; any internal surprise degrades to a safe WAIT/NO TRADE verdict."""
    out = neutral("", l.get("inst"), enabled=True, generated_at=l.get("generated_at"))
    out["available"]   = True
    out["market_open"] = bool(l.get("market_open"))
    if not out["market_open"]:
        out["summary"] = "Market closed — no live liquidity to hunt."
        return out

    sweep = detect_liquidity_event(l)
    out.update({k: sweep[k] for k in
                ("liquidity_event", "sweep_direction", "sweep_price", "sweep_age_sec")})
    out["evidence"].extend(sweep["evidence"])
    if not sweep["liquidity_event"]:
        out["summary"] = "No liquidity moment — no sweep of prior highs/lows detected."
        return out

    trap = detect_trap(l, sweep)
    out["trapped_side"] = trap["trapped_side"]
    out["evidence"].extend(trap["evidence"])
    if trap["trapped_side"] == "NONE":
        if trap.get("forming"):
            out["verdict"] = "WAIT"
            out["summary"] = ("%s just happened — watching whether the move fails and "
                              "traps traders." % out["sweep_direction"])
        else:
            out["summary"] = ("%s but nobody is trapped — price kept going. No trade."
                              % out["sweep_direction"])
        return out

    trade_dir = "Short" if trap["trapped_side"] == "LONGS" else "Long"
    out["direction"] = trade_dir

    absorption = detect_absorption(l)
    out["absorption"] = absorption["absorption"]
    out["evidence"].extend(absorption["evidence"])

    trigger = detect_micro_trigger(l, trade_dir, sweep)
    out["micro_trigger"]  = trigger["micro_trigger"]
    out["trigger_reason"] = trigger["trigger_reason"]
    out["evidence"].extend(trigger["evidence"])

    setup_type = ("SWEEP_ABSORPTION_REVERSAL" if out["absorption"]
                  else "SWEEP_TRAP_REVERSAL")
    out["setup_type"] = setup_type

    geo = build_geometry(l, trade_dir, sweep.get("sweep_price"))
    if geo:
        out["suggested_entry"]    = geo["suggested_entry"]
        out["suggested_stop"]     = geo["suggested_stop"]
        out["stop_points"]        = geo["stop_points"]
        out["tp1"]                = geo["tp1"]
        out["tp2"]                = geo["tp2"]
        out["runner_target"]      = geo["runner_target"]
        out["invalidation_level"] = geo["suggested_stop"]

    if not trigger["micro_trigger"]:
        out["verdict"] = "WAIT"
        out["summary"] = ("%s trapped after the %s — waiting for a micro trigger "
                          "(micro CHOCH / VWAP reclaim / close back inside range)."
                          % (trap["trapped_side"].title(), out["sweep_direction"].lower()))
        return out
    if geo is None:
        out["verdict"] = "WAIT"
        out["summary"] = "Trigger fired but no clean price to anchor the trade — waiting."
        return out

    out["verdict"] = "TAKE"
    out["entry_reason"] = ("%s swept -> %s trapped -> %s. Stop %s."
                          % (("Highs" if out["sweep_direction"] == "HIGH SWEEP" else "Lows"),
                             trap["trapped_side"].lower(), trigger["trigger_reason"],
                             geo["stop_note"]))
    out["summary"] = ("TAKE %s @ %.2f — %s" %
                      (trade_dir.upper(), geo["suggested_entry"], out["entry_reason"]))
    return out


# ── GHOST lifecycle: pure per-bar resolver (the app watcher applies updates) ──
def ghost_bar_update(row, bar):
    """Evaluate ONE open ghost trade against ONE 1-minute bar. Pure — returns an
    updates dict; the caller persists it. Conventions (worst-case, same as the
    proven paper sims): STOP is checked before targets inside a bar; SHORT R is
    (entry - exit) / risk so losses stay negative both directions; BE only after
    TP1. Keys returned: mfe_points / mae_points always; optionally tp1_hit,
    new_stop (BE), outcome ('win'|'loss'|'breakeven'), exit_price, result_r."""
    high, low = _f(bar.get("high")), _f(bar.get("low"))
    entry     = _f(row.get("entry"))
    stop      = _f(row.get("stop"))
    tp1, tp2  = _f(row.get("tp1")), _f(row.get("tp2"))
    risk      = _f(row.get("risk_points"))
    if None in (high, low, entry, stop, tp1, tp2):
        return None
    if not risk or risk <= 0:
        risk = abs(entry - stop) or None
        if risk is None:
            return None
    is_long = (row.get("direction") == "Long")
    sgn = 1.0 if is_long else -1.0
    fav = (high - entry) if is_long else (entry - low)       # best move in our favor
    adv = (entry - low) if is_long else (high - entry)       # worst move against us
    upd = {
        "mfe_points": round(max(_f(row.get("mfe_points")) or 0.0, fav, 0.0), 4),
        "mae_points": round(max(_f(row.get("mae_points")) or 0.0, adv, 0.0), 4),
    }
    stop_hit = (low <= stop) if is_long else (high >= stop)
    tp1_done = bool(row.get("tp1_hit"))
    if stop_hit:                                              # worst-case: stop first
        exit_px = stop
        r = (exit_px - entry) / risk * sgn
        upd.update({"outcome": ("breakeven" if tp1_done else "loss"),
                    "exit_price": round(exit_px, 4), "result_r": round(r, 4)})
        return upd
    if not tp1_done:
        tp1_hit_now = (high >= tp1) if is_long else (low <= tp1)
        if tp1_hit_now:
            upd.update({"tp1_hit": True, "new_stop": entry})  # BE only after TP1
            tp1_done = True
    if tp1_done:
        tp2_hit = (high >= tp2) if is_long else (low <= tp2)
        if tp2_hit:
            r = (tp2 - entry) / risk * sgn
            upd.update({"outcome": "win", "exit_price": round(tp2, 4),
                        "result_r": round(r, 4)})
    return upd
