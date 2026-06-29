"""
Scalp LIVE Strategy Simulation — PAPER / RESEARCH / DISPLAY ONLY.

Walled off from the live money path exactly like ``scalp_research`` and
``backtest_engine``. This module is PURE: it imports nothing from ``app.py``,
opens no trades, and touches no broker / learning / managed-trade state. It only:

  • maps each TESTABLE research-library scalp strategy to a LIVE-context detector
    (a pure function of a single normalized live-context dict ``lctx``), and
  • computes the entry / stop / target geometry for a detected candidate
    (1:1 R, live-consistent — never bigger so the paper sim can't flatter itself).

``app.py`` owns ALL side effects: the optional, default-OFF observer that INSERTs
an OPEN paper trade into the SEPARATE ``scalp_strategy_sim_trades`` table, and the
parallel paper watcher that resolves it. NOTHING here is ever registered into the
live gate, STRATEGY_SCORERS, priority/control, auto-trade or /traderspost, and it
never feeds the learning engine.

Fidelity:
  • "clean"       — the live context expresses the strategy's real trigger.
  • "approximate" — the live context only has a PROXY for the trigger (e.g. a
                    zone stands in for an FVG/order-block, OHLCV-derived CVD, or a
                    single-bar live read stands in for a multi-bar pattern).
The fidelity label rides with every paper trade so the dashboard can disclose it.
"""

# 1:1 R keeps the paper sim live-consistent (the live money path also targets 1R
# for SCALP primary). A bigger R would let the sim flatter itself vs. live results.
SIM_RR = 1.0

# Default stop distance (in ATR) when no nearby zone provides a structural stop.
STOP_ATR_MULT = 1.0

# Buffer (in ATR) placed beyond a zone when a zone IS used for the stop.
ZONE_BUFFER_ATR = 0.10

# The 16 TESTABLE research-library scalp strategies that get a live detector.
TESTABLE_KEYS = (
    "vwap_pullback_continuation",
    "vwap_reclaim_fail",
    "opening_range_breakout",
    "opening_range_fakeout",
    "liquidity_sweep_reversal",
    "failed_breakdown_breakout",
    "micro_pullback_scalp",
    "ema_9_20_continuation",
    "fvg_continuation",
    "order_block_rejection",
    "prior_high_low_sweep",
    "session_high_low_reclaim",
    "volume_climax_reversal",
    "cvd_divergence_scalp",
    "range_edge_mean_reversion",
    "compression_breakout",
)

# Strategies that are NOT yet testable live (no faithful proxy in the live context)
# — kept here so the dashboard can keep showing them as "pending", never opened.
PENDING_KEYS = (
    "trendline_break_retest",
    "delta_exhaustion_reversal",
    "news_volatility_fade",
)

# Per-strategy fidelity of the LIVE detector.
FIDELITY = {
    "vwap_pullback_continuation": "clean",
    "vwap_reclaim_fail":          "approximate",
    "opening_range_breakout":     "clean",
    "opening_range_fakeout":      "approximate",
    "liquidity_sweep_reversal":   "clean",
    "failed_breakdown_breakout":  "approximate",
    "micro_pullback_scalp":       "approximate",
    "ema_9_20_continuation":      "approximate",
    "fvg_continuation":           "approximate",
    "order_block_rejection":      "approximate",
    "prior_high_low_sweep":       "clean",
    "session_high_low_reclaim":   "approximate",
    "volume_climax_reversal":     "clean",
    "cvd_divergence_scalp":       "approximate",
    "range_edge_mean_reversion":  "clean",
    "compression_breakout":       "clean",
}


# ── Detectors ────────────────────────────────────────────────────────────────
# Each detector is a PURE function of the normalized live-context dict ``l`` and
# returns ``(direction, reason)`` or ``None``. ``direction`` is "Long"/"Short".
# Detectors NEVER size or open a trade — geometry is applied separately, and the
# app-side observer owns all persistence. Every read is ``l.get(...)`` so a
# missing/None field can only mean "no signal", never an exception.

def _det_vwap_pullback_continuation(l):
    if not l.get("vwap_ok") or not l.get("near_vwap"):
        return None
    if l.get("structure_long") and l.get("price_above_vwap") and l.get("has_bull_confirm"):
        return ("Long", "Uptrend pullback to VWAP held & confirmed")
    if l.get("structure_short") and l.get("price_below_vwap") and l.get("has_bear_confirm"):
        return ("Short", "Downtrend pullback to VWAP held & confirmed")
    return None


def _det_vwap_reclaim_fail(l):
    # No prior-bar VWAP cross live → proxy with a fresh CHOCH back across VWAP on volume.
    if not l.get("vwap_ok") or not l.get("volume_ok"):
        return None
    if l.get("has_choch_demand") and l.get("price_above_vwap"):
        return ("Long", "VWAP reclaim proxy: bullish CHOCH back above VWAP on volume")
    if l.get("has_choch_supply") and l.get("price_below_vwap"):
        return ("Short", "VWAP-loss proxy: bearish CHOCH back below VWAP on volume")
    return None


def _det_opening_range_breakout(l):
    price = l.get("price")
    if not l.get("or_complete") or price is None or not l.get("vwap_ok"):
        return None
    oh, ol = l.get("or_high"), l.get("or_low")
    if oh is not None and price > oh and l.get("price_above_vwap") and l.get("volume_ok"):
        return ("Long", "Opening-range breakout above %.2f w/ volume" % oh)
    if ol is not None and price < ol and l.get("price_below_vwap") and l.get("volume_ok"):
        return ("Short", "Opening-range breakdown below %.2f w/ volume" % ol)
    return None


def _det_opening_range_fakeout(l):
    price = l.get("price")
    if not (l.get("in_opening_window") and l.get("or_complete")) or price is None:
        return None
    oh, ol = l.get("or_high"), l.get("or_low")
    if oh is not None and l.get("has_bear_sweep") and price < oh and l.get("price_below_vwap"):
        return ("Short", "Failed OR breakout proxy — swept above %.2f, back inside" % oh)
    if ol is not None and l.get("has_bull_sweep") and price > ol and l.get("price_above_vwap"):
        return ("Long", "Failed OR breakdown proxy — swept below %.2f, back inside" % ol)
    return None


def _det_liquidity_sweep_reversal(l):
    if not l.get("vwap_ok"):
        return None
    if l.get("has_bull_sweep") and (l.get("price_above_vwap") or l.get("near_vwap")):
        return ("Long", "Sell-side liquidity swept & reclaimed toward VWAP")
    if l.get("has_bear_sweep") and (l.get("price_below_vwap") or l.get("near_vwap")):
        return ("Short", "Buy-side liquidity swept & reclaimed toward VWAP")
    return None


def _det_failed_breakdown_breakout(l):
    price = l.get("price")
    rh, rl = l.get("range_high"), l.get("range_low")
    if price is None:
        return None
    if l.get("has_bull_sweep") and l.get("structure_long") and rl is not None and price > rl:
        return ("Long", "Failed-breakdown proxy — range low swept then reclaimed")
    if l.get("has_bear_sweep") and l.get("structure_short") and rh is not None and price < rh:
        return ("Short", "Failed-breakout proxy — range high swept then rejected")
    return None


def _det_micro_pullback_scalp(l):
    rvol = l.get("rvol")
    if rvol is None or rvol < 1.5:
        return None
    if l.get("structure_long") and l.get("price_above_vwap") and l.get("has_bull_confirm"):
        return ("Long", "Momentum micro-pullback resumed (RVOL %.1f)" % rvol)
    if l.get("structure_short") and l.get("price_below_vwap") and l.get("has_bear_confirm"):
        return ("Short", "Momentum micro-pullback resumed (RVOL %.1f)" % rvol)
    return None


def _det_ema_9_20_continuation(l):
    # EMA stack isn't available live → proxy with a trending regime + VWAP pullback.
    if l.get("regime") != "TRENDING" or not l.get("near_vwap"):
        return None
    if l.get("structure_long") and l.get("price_above_vwap"):
        return ("Long", "EMA-stack proxy: trending pullback continuation above VWAP")
    if l.get("structure_short") and l.get("price_below_vwap"):
        return ("Short", "EMA-stack proxy: trending pullback continuation below VWAP")
    return None


def _det_fvg_continuation(l):
    price, atr = l.get("price"), l.get("atr")
    if price is None or not atr:
        return None
    dem, sup = l.get("nearest_demand"), l.get("nearest_supply")
    if l.get("structure_long") and dem is not None and abs(price - dem) <= 0.6 * atr \
            and l.get("has_bull_confirm"):
        return ("Long", "Pullback into demand imbalance held (FVG zone proxy)")
    if l.get("structure_short") and sup is not None and abs(price - sup) <= 0.6 * atr \
            and l.get("has_bear_confirm"):
        return ("Short", "Pullback into supply imbalance held (FVG zone proxy)")
    return None


def _det_order_block_rejection(l):
    price, atr = l.get("price"), l.get("atr")
    if price is None or not atr:
        return None
    dem, sup = l.get("nearest_demand"), l.get("nearest_supply")
    if sup is not None and abs(price - sup) <= 0.5 * atr \
            and (l.get("structure_short") or l.get("price_below_vwap")):
        return ("Short", "Rejection from supply order-block (zone proxy)")
    if dem is not None and abs(price - dem) <= 0.5 * atr \
            and (l.get("structure_long") or l.get("price_above_vwap")):
        return ("Long", "Rejection from demand order-block (zone proxy)")
    return None


def _det_prior_high_low_sweep(l):
    if l.get("has_bull_sweep") and l.get("structure_long"):
        return ("Long", "Prior swing-low swept & reclaimed (structure aligned)")
    if l.get("has_bear_sweep") and l.get("structure_short"):
        return ("Short", "Prior swing-high swept & rejected (structure aligned)")
    return None


def _det_session_high_low_reclaim(l):
    price = l.get("price")
    rh, rl = l.get("range_high"), l.get("range_low")
    if price is None:
        return None
    if l.get("has_bear_sweep") and l.get("price_below_vwap") and rh is not None and price < rh:
        return ("Short", "Session-high sweep then reclaim lower (range proxy)")
    if l.get("has_bull_sweep") and l.get("price_above_vwap") and rl is not None and price > rl:
        return ("Long", "Session-low sweep then reclaim higher (range proxy)")
    return None


def _det_volume_climax_reversal(l):
    rvol = l.get("rvol")
    if rvol is None or rvol < 2.5:
        return None
    if l.get("has_bull_sweep"):
        return ("Long", "Volume-climax sell-off absorbed (RVOL %.1f)" % rvol)
    if l.get("has_bear_sweep"):
        return ("Short", "Volume-climax up-thrust rejected (RVOL %.1f)" % rvol)
    return None


def _det_cvd_divergence_scalp(l):
    cs = l.get("cvd_state")
    if cs == "bullish" and l.get("price_below_vwap"):
        return ("Long", "Bullish CVD divergence vs weak price (OHLCV-derived proxy)")
    if cs == "bearish" and l.get("price_above_vwap"):
        return ("Short", "Bearish CVD divergence vs strong price (OHLCV-derived proxy)")
    return None


def _det_range_edge_mean_reversion(l):
    price, atr = l.get("price"), l.get("atr")
    if price is None or not atr or l.get("regime") != "RANGING":
        return None
    rh, rl = l.get("range_high"), l.get("range_low")
    tol = 0.25 * atr
    if rh is not None and abs(price - rh) <= tol:
        return ("Short", "Fade at range high (mean reversion)")
    if rl is not None and abs(price - rl) <= tol:
        return ("Long", "Fade at range low (mean reversion)")
    return None


def _det_compression_breakout(l):
    if not l.get("range_tight") or not l.get("volume_ok"):
        return None
    if l.get("broke_range_high"):
        return ("Long", "Compression breakout above the coil")
    if l.get("broke_range_low"):
        return ("Short", "Compression breakdown below the coil")
    return None


# Registry: strategy_key → detector. Ordered to match TESTABLE_KEYS.
LIVE_SIM_DETECTORS = {
    "vwap_pullback_continuation": _det_vwap_pullback_continuation,
    "vwap_reclaim_fail":          _det_vwap_reclaim_fail,
    "opening_range_breakout":     _det_opening_range_breakout,
    "opening_range_fakeout":      _det_opening_range_fakeout,
    "liquidity_sweep_reversal":   _det_liquidity_sweep_reversal,
    "failed_breakdown_breakout":  _det_failed_breakdown_breakout,
    "micro_pullback_scalp":       _det_micro_pullback_scalp,
    "ema_9_20_continuation":      _det_ema_9_20_continuation,
    "fvg_continuation":           _det_fvg_continuation,
    "order_block_rejection":      _det_order_block_rejection,
    "prior_high_low_sweep":       _det_prior_high_low_sweep,
    "session_high_low_reclaim":   _det_session_high_low_reclaim,
    "volume_climax_reversal":     _det_volume_climax_reversal,
    "cvd_divergence_scalp":       _det_cvd_divergence_scalp,
    "range_edge_mean_reversion":  _det_range_edge_mean_reversion,
    "compression_breakout":       _det_compression_breakout,
}


def _missing_confirmations(key, l):
    """Best-effort, DISPLAY-ONLY hints for why a strategy did NOT fire, derived from
    the same live-context flags its detector reads. Returns a list of short human
    strings. PURE + never raises. This is a diagnostic aid only — the authoritative
    pass/fail/direction/reason always come from the real detector."""
    if not isinstance(l, dict):
        return ["live context"]
    g = l.get
    atr = g("atr")
    vwap_ok = g("vwap_ok"); near = g("near_vwap")
    above = g("price_above_vwap"); below = g("price_below_vwap")
    sl = g("structure_long"); ss = g("structure_short")
    bullc = g("has_bull_confirm"); bearc = g("has_bear_confirm")
    bulls = g("has_bull_sweep"); bears = g("has_bear_sweep")
    vol = g("volume_ok"); rvol = g("rvol"); regime = g("regime")
    dem = g("nearest_demand"); sup = g("nearest_supply")
    m = []
    if key == "vwap_pullback_continuation":
        if not vwap_ok: m.append("VWAP data")
        if not near: m.append("pullback to VWAP")
        if not (sl or ss): m.append("trend structure")
        if not (bullc or bearc): m.append("entry confirmation")
    elif key == "vwap_reclaim_fail":
        if not vwap_ok: m.append("VWAP data")
        if not vol: m.append("volume")
        if not (g("has_choch_demand") or g("has_choch_supply")): m.append("CHOCH across VWAP")
    elif key == "opening_range_breakout":
        if not g("or_complete"): m.append("opening range")
        if not vwap_ok: m.append("VWAP data")
        if not vol: m.append("breakout volume")
        m.append("price beyond OR high/low on the VWAP side")
    elif key == "opening_range_fakeout":
        if not g("in_opening_window"): m.append("opening window")
        if not g("or_complete"): m.append("opening range")
        if not (bulls or bears): m.append("OR sweep + reversal")
    elif key == "liquidity_sweep_reversal":
        if not vwap_ok: m.append("VWAP data")
        if not (bulls or bears): m.append("liquidity sweep")
    elif key == "failed_breakdown_breakout":
        if not (bulls or bears): m.append("range sweep")
        if not (sl or ss): m.append("structure reclaim")
    elif key == "micro_pullback_scalp":
        if rvol is None or rvol < 1.5: m.append("RVOL >= 1.5")
        if not (sl or ss): m.append("trend structure")
        if not (bullc or bearc): m.append("entry confirmation")
    elif key == "ema_9_20_continuation":
        if regime != "TRENDING": m.append("trending regime")
        if not near: m.append("VWAP pullback")
        if not (sl or ss): m.append("trend structure")
    elif key == "fvg_continuation":
        if not atr: m.append("ATR")
        if dem is None and sup is None: m.append("imbalance / FVG zone")
        if not (bullc or bearc): m.append("hold confirmation")
    elif key == "order_block_rejection":
        if not atr: m.append("ATR")
        if dem is None and sup is None: m.append("order-block zone")
        m.append("price tagging the zone")
    elif key == "prior_high_low_sweep":
        if not (bulls or bears): m.append("swing sweep")
        if not (sl or ss): m.append("aligned structure")
    elif key == "session_high_low_reclaim":
        if not (bulls or bears): m.append("session sweep")
        if not (above or below): m.append("VWAP-side reclaim")
    elif key == "volume_climax_reversal":
        if rvol is None or rvol < 2.5: m.append("RVOL >= 2.5 climax")
        if not (bulls or bears): m.append("absorption sweep")
    elif key == "cvd_divergence_scalp":
        if g("cvd_state") not in ("bullish", "bearish"): m.append("CVD divergence")
        if not (above or below): m.append("price vs VWAP stretch")
    elif key == "range_edge_mean_reversion":
        if not atr: m.append("ATR")
        if regime != "RANGING": m.append("ranging regime")
        m.append("price at a range edge")
    elif key == "compression_breakout":
        if not g("range_tight"): m.append("tight compression")
        if not vol: m.append("breakout volume")
        if not (g("broke_range_high") or g("broke_range_low")): m.append("range break")
    if not m:
        m.append("setup trigger")
    return m


def diagnose_strategies(lctx):
    """DISPLAY-ONLY: run EVERY testable detector and return the full 16-strategy vote
    roster. Pass/fail/direction/reason come from the REAL detector (single source of
    truth); a strategy that did not fire gets a best-effort 'missing confirmations'
    hint from the same live-context flags. PURE + FAIL-OPEN: a detector that raises is
    treated as 'no signal', never propagated. Opens / sizes / sends nothing."""
    votes = []
    if not isinstance(lctx, dict):
        lctx = {}
    for key, fn in LIVE_SIM_DETECTORS.items():
        try:
            sig = fn(lctx)
        except Exception:
            sig = None
        if sig:
            direction, reason = sig
            votes.append({
                "strategy_key": key, "direction": direction, "passed": True,
                "reason": reason, "missing": [],
                "fidelity": FIDELITY.get(key, "approximate"),
            })
        else:
            try:
                missing = _missing_confirmations(key, lctx)
            except Exception:
                missing = []
            votes.append({
                "strategy_key": key, "direction": "Neutral", "passed": False,
                "reason": "Trigger conditions not met", "missing": missing,
                "fidelity": FIDELITY.get(key, "approximate"),
            })
    return votes


def _geometry(direction, price, atr, dem, sup):
    """Pure entry/stop/target geometry for a detected candidate. Entry = current
    price (market paper fill); stop = a nearby structural zone if one sits within
    2 ATR on the correct side, else ``STOP_ATR_MULT`` ATR away; target = entry ±
    ``SIM_RR`` × risk. Returns ``None`` (no trade) if ATR is missing/0 or the
    geometry is degenerate — the sim NEVER fabricates a fill it can't size."""
    if price is None or atr is None or atr <= 0:
        return None
    buf = ZONE_BUFFER_ATR * atr
    if direction == "Long":
        stop = price - STOP_ATR_MULT * atr
        if dem is not None and dem < price and (price - dem) <= 2.0 * atr:
            stop = min(stop, dem - buf)
        risk = price - stop
        if risk <= 0:
            return None
        target = price + SIM_RR * risk
    else:
        stop = price + STOP_ATR_MULT * atr
        if sup is not None and sup > price and (sup - price) <= 2.0 * atr:
            stop = max(stop, sup + buf)
        risk = stop - price
        if risk <= 0:
            return None
        target = price - SIM_RR * risk
    return {"entry": round(price, 4), "stop": round(stop, 4),
            "target": round(target, 4), "risk": round(risk, 4), "rr": SIM_RR}


def build_candidates(lctx):
    """Run EVERY testable detector over the normalized live context and return a
    list of fully-sized candidate dicts. PURE + FAIL-OPEN: a detector that raises
    is skipped, never propagated. Opens nothing — the app-side observer decides
    whether/where to persist. A candidate dict:

        {strategy_key, direction, entry_reason, fidelity,
         entry, stop, target, risk, rr}
    """
    out = []
    if not isinstance(lctx, dict):
        return out
    price = lctx.get("price")
    atr = lctx.get("atr")
    dem = lctx.get("nearest_demand")
    sup = lctx.get("nearest_supply")
    for key, fn in LIVE_SIM_DETECTORS.items():
        try:
            sig = fn(lctx)
        except Exception:
            sig = None
        if not sig:
            continue
        direction, reason = sig
        # Conflict guard: never open against a confirmed opposite structure.
        if direction == "Long" and lctx.get("structure_short") and not lctx.get("structure_long"):
            continue
        if direction == "Short" and lctx.get("structure_long") and not lctx.get("structure_short"):
            continue
        geo = _geometry(direction, price, atr, dem, sup)
        if geo is None:
            continue
        cand = {"strategy_key": key, "direction": direction,
                "entry_reason": reason, "fidelity": FIDELITY.get(key, "approximate")}
        cand.update(geo)
        out.append(cand)
    return out


if __name__ == "__main__":
    # Self-test smoke (no DB, no app import): registry shape + a couple of detections.
    assert len(LIVE_SIM_DETECTORS) == 16, len(LIVE_SIM_DETECTORS)
    assert set(LIVE_SIM_DETECTORS) == set(TESTABLE_KEYS)
    assert set(FIDELITY) == set(TESTABLE_KEYS)
    assert len(PENDING_KEYS) == 3
    # Cross-check against the research library if it's importable (both pure modules).
    try:
        import scalp_research as _sr
        lib_keys = {e.get("strategy_key") for e in _sr.STRATEGY_LIBRARY}
        assert set(TESTABLE_KEYS).issubset(lib_keys), \
            set(TESTABLE_KEYS) - lib_keys
        assert set(PENDING_KEYS).issubset(lib_keys), set(PENDING_KEYS) - lib_keys
    except ImportError:
        pass

    long_ctx = {
        "price": 2000.0, "atr": 2.0, "vwap_ok": True, "near_vwap": True,
        "price_above_vwap": True, "structure_long": True, "has_bull_confirm": True,
        "nearest_demand": 1998.0, "nearest_supply": 2006.0,
    }
    cands = build_candidates(long_ctx)
    assert any(c["strategy_key"] == "vwap_pullback_continuation" and c["direction"] == "Long"
               for c in cands), cands
    for c in cands:
        assert c["rr"] == SIM_RR
        assert c["target"] > c["entry"] > c["stop"]  # all detections above are Long

    # ATR missing → no candidate can be sized.
    assert build_candidates({"price": 2000.0, "vwap_ok": True, "near_vwap": True,
                             "price_above_vwap": True, "structure_long": True,
                             "has_bull_confirm": True}) == []

    # diagnose_strategies: full 16-vote roster, pass/fail authoritative from detectors.
    votes = diagnose_strategies(long_ctx)
    assert len(votes) == 16, len(votes)
    assert all({"strategy_key", "direction", "passed", "reason", "missing"} <= set(v)
               for v in votes), votes
    assert any(v["passed"] for v in votes), "no passing votes on the long context"
    assert all(isinstance(v["missing"], list) for v in votes if not v["passed"])
    # An empty context fires nothing → 16 failed votes, each with a missing hint.
    empty_votes = diagnose_strategies({})
    assert len(empty_votes) == 16 and not any(v["passed"] for v in empty_votes)
    assert all(v["missing"] for v in empty_votes), "failed votes must carry a hint"

    print("scalp_live_sim self-test OK — %d detectors, sample candidates=%d"
          % (len(LIVE_SIM_DETECTORS), len(cands)))
