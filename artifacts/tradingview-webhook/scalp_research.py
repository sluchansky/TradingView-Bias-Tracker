"""
Scalping Strategy Research Engine — RESEARCH / SIMULATION / DISPLAY ONLY.

This module is walled off from the live money path EXACTLY like backtest_engine.
It imports ONLY backtest_engine (pure, read-only historical simulation helpers)
and NOTHING from app.py's live trading path. Nothing here is ever registered
into backtest_engine.DETECTORS / STRATEGY_DEFS / STRATEGY_ORDER, the live gate,
the multi-strategy engine, auto-trade, or /traderspost.

What it does:
  • Holds a catalog (STRATEGY_LIBRARY) of named professional scalp strategies.
  • Defines rule-based research detectors for the strategies that are expressible
    from the OHLCV-derived backtest snapshot. Strategies whose required data does
    NOT exist in that snapshot are catalogued honestly as data/detector pending —
    we NEVER fabricate examples or stats for them.
  • Scans historical datasets for examples and computes win%/avgR/drawdown/best
    session per strategy, per instrument, reusing backtest_engine's simulation.
  • Compares new strategies against the existing live engine strategies.
  • Produces ADVISORY-ONLY promotion recommendations (never mutates live config).

All new strategies stay in WATCH / SIMULATION. Promotion to live is a manual,
out-of-band human decision — this engine only recommends.
"""
from datetime import datetime, timezone

import backtest_engine as bt

# Research uses the live-consistent 1:1 exit model so simulated stats reflect the
# real live edge (SCALP primary TP = 1R today), not an inflated R target.
RESEARCH_MGMT = "target_1r"
RESEARCH_MODE = "SCALP"

# Promotion is ADVISORY ONLY. These thresholds gate the recommendation text; they
# never change live_status to anything actionable and never touch the money path.
MIN_PROMO_TRADES = 40      # need enough examples before recommending anything
MIN_PROMO_PF     = 1.30    # profit factor floor
TOP_N            = 6       # best/worst ranking size


# ════════════════════════════════════════════════════════════════════════════
# RESEARCH DETECTORS — (snaps, i) -> (direction, entry_reason) | None
# Pure functions of the causal snapshot list up to & including bar i. They may
# look back at prior bars (unlike backtest_engine's single-bar detectors), but
# never forward. Faithful, rule-based versions of each named strategy. Proxies
# (zone-based FVG/OB, OHLCV-derived CVD) are labelled in their entry_reason.
# ════════════════════════════════════════════════════════════════════════════
def _atr_ok(s):
    return s["atr"] is not None and s["atr"] > 0


def d_vwap_pullback_continuation(snaps, i):
    s = snaps[i]
    if not _atr_ok(s) or s["vwap"] is None:
        return None
    near = abs(s["close"] - s["vwap"]) <= 0.6 * s["atr"]
    if s["trend"] > 0 and s["close"] > s["vwap"] and near and s["confirm_bull"]:
        return ("Long", "Uptrend VWAP pullback held & confirmed")
    if s["trend"] < 0 and s["close"] < s["vwap"] and near and s["confirm_bear"]:
        return ("Short", "Downtrend VWAP pullback held & confirmed")
    return None


def d_vwap_reclaim_fail(snaps, i):
    if i < 1:
        return None
    s, p = snaps[i], snaps[i - 1]
    if s["vwap"] is None or p["vwap"] is None:
        return None
    if p["close"] < p["vwap"] and s["close"] > s["vwap"] and (s["rvol"] or 0) >= 1.2:
        return ("Long", "VWAP reclaim from below on rising volume")
    if p["close"] > p["vwap"] and s["close"] < s["vwap"] and (s["rvol"] or 0) >= 1.2:
        return ("Short", "VWAP loss/failure from above on rising volume")
    return None


def d_opening_range_breakout(snaps, i):
    s = snaps[i]
    if not s["or_complete"] or s["or_high"] is None or s["vwap"] is None:
        return None
    if s["close"] > s["or_high"] and s["close"] > s["vwap"] and s["volume_confirmed"]:
        return ("Long", "Opening-range breakout above %.2f w/ volume" % s["or_high"])
    if s["close"] < s["or_low"] and s["close"] < s["vwap"] and s["volume_confirmed"]:
        return ("Short", "Opening-range breakdown below %.2f w/ volume" % s["or_low"])
    return None


def d_opening_range_fakeout(snaps, i):
    if i < 1:
        return None
    s, p = snaps[i], snaps[i - 1]
    if not s["or_complete"] or s["or_high"] is None or s["vwap"] is None:
        return None
    if p["high"] > s["or_high"] and s["close"] < s["or_high"] and s["close"] < s["vwap"]:
        return ("Short", "Failed OR breakout — poke above then close back inside")
    if p["low"] < s["or_low"] and s["close"] > s["or_low"] and s["close"] > s["vwap"]:
        return ("Long", "Failed OR breakdown — poke below then close back inside")
    return None


def d_liquidity_sweep_reversal(snaps, i):
    s = snaps[i]
    if not _atr_ok(s) or s["vwap"] is None:
        return None
    if s["sweep_bull"] and s["close"] > s["vwap"] - s["atr"]:
        return ("Long", "Sell-side liquidity swept & reclaimed")
    if s["sweep_bear"] and s["close"] < s["vwap"] + s["atr"]:
        return ("Short", "Buy-side liquidity swept & reclaimed")
    return None


def d_failed_breakdown_breakout(snaps, i):
    if i < 1:
        return None
    s, p = snaps[i], snaps[i - 1]
    if s["range_low"] is None or s["range_high"] is None:
        return None
    if p["low"] < s["range_low"] and s["close"] > s["range_low"]:
        return ("Long", "Failed breakdown — range low swept then reclaimed")
    if p["high"] > s["range_high"] and s["close"] < s["range_high"]:
        return ("Short", "Failed breakout — range high swept then rejected")
    return None


def d_micro_pullback_scalp(snaps, i):
    if i < 2:
        return None
    s, p = snaps[i], snaps[i - 1]
    if not _atr_ok(s) or s["vwap"] is None:
        return None
    if s["trend"] > 0 and s["close"] > s["vwap"] and p["close"] < p["open"] and s["close"] > p["high"]:
        return ("Long", "Micro pullback in uptrend resumed")
    if s["trend"] < 0 and s["close"] < s["vwap"] and p["close"] > p["open"] and s["close"] < p["low"]:
        return ("Short", "Micro pullback in downtrend resumed")
    return None


def d_ema_9_20_continuation(snaps, i):
    if i < 1:
        return None
    s, p = snaps[i], snaps[i - 1]
    e9, e20 = s.get("ema9"), s.get("ema20")
    if e9 is None or e20 is None:
        return None
    if e9 > e20 and s["trend"] >= 0 and p["low"] <= e9 and s["close"] > e9:
        return ("Long", "9>20 EMA stack, pullback to 9EMA reclaimed")
    if e9 < e20 and s["trend"] <= 0 and p["high"] >= e9 and s["close"] < e9:
        return ("Short", "9<20 EMA stack, pullback to 9EMA rejected")
    return None


def d_fvg_continuation(snaps, i):
    # APPROXIMATION: OHLCV has no precise FVG object; demand/supply zones proxy
    # the imbalance that price retraces into.
    s = snaps[i]
    if s["trend"] > 0 and s["demand_zone"] is not None and \
            s["low"] <= s["demand_zone"] and s["close"] > s["demand_zone"] and s["confirm_bull"]:
        return ("Long", "Pullback into demand imbalance held (FVG proxy)")
    if s["trend"] < 0 and s["supply_zone"] is not None and \
            s["high"] >= s["supply_zone"] and s["close"] < s["supply_zone"] and s["confirm_bear"]:
        return ("Short", "Pullback into supply imbalance held (FVG proxy)")
    return None


def d_order_block_rejection(snaps, i):
    # APPROXIMATION: order blocks proxied by the nearest demand/supply zone.
    s = snaps[i]
    if s["supply_zone"] is not None and s["high"] >= s["supply_zone"] and \
            s["close"] < s["supply_zone"] and s["trend"] <= 0:
        return ("Short", "Rejection from supply order-block (zone proxy)")
    if s["demand_zone"] is not None and s["low"] <= s["demand_zone"] and \
            s["close"] > s["demand_zone"] and s["trend"] >= 0:
        return ("Long", "Rejection from demand order-block (zone proxy)")
    return None


def d_prior_high_low_sweep(snaps, i):
    s = snaps[i]
    if s["swing_high"] is None or s["swing_low"] is None:
        return None
    if s["high"] > s["swing_high"] and s["close"] < s["swing_high"]:
        return ("Short", "Prior swing-high swept & rejected")
    if s["low"] < s["swing_low"] and s["close"] > s["swing_low"]:
        return ("Long", "Prior swing-low swept & reclaimed")
    return None


def d_session_high_low_reclaim(snaps, i):
    if i < 1:
        return None
    s, p = snaps[i], snaps[i - 1]
    sh, sl = p.get("sess_high"), p.get("sess_low")
    if sh is None or sl is None:
        return None
    if s["high"] > sh and s["close"] < sh:
        return ("Short", "Session high swept then reclaimed lower")
    if s["low"] < sl and s["close"] > sl:
        return ("Long", "Session low swept then reclaimed higher")
    return None


def d_volume_climax_reversal(snaps, i):
    if i < 1:
        return None
    s, p = snaps[i], snaps[i - 1]
    if (s["rvol"] or 0) < 2.5 or s["vwap"] is None:
        return None
    if s["close"] < s["open"] and p["close"] > p["open"] and s["close"] < s["vwap"]:
        return ("Short", "Volume climax up-thrust rejected")
    if s["close"] > s["open"] and p["close"] < p["open"] and s["close"] > s["vwap"]:
        return ("Long", "Volume climax sell-off absorbed")
    return None


def d_cvd_divergence_scalp(snaps, i):
    # APPROXIMATION: cvd here is OHLCV-derived, not true order-flow delta.
    if i < 3:
        return None
    s, p = snaps[i], snaps[i - 3]
    cv, pcv = s.get("cvd"), p.get("cvd")
    if cv is None or pcv is None:
        return None
    if s["low"] < p["low"] and cv > pcv and s["close"] > s["open"]:
        return ("Long", "Bullish CVD divergence — price LL, CVD HL (approx)")
    if s["high"] > p["high"] and cv < pcv and s["close"] < s["open"]:
        return ("Short", "Bearish CVD divergence — price HH, CVD LH (approx)")
    return None


def d_range_edge_mean_reversion(snaps, i):
    s = snaps[i]
    if s["range_high"] is None or s["range_low"] is None or not _atr_ok(s):
        return None
    if s["regime"] != "RANGING":
        return None
    tol = 0.25 * s["atr"]
    if abs(s["close"] - s["range_high"]) <= tol and s["close"] < s["open"]:
        return ("Short", "Fade at range high (mean reversion)")
    if abs(s["close"] - s["range_low"]) <= tol and s["close"] > s["open"]:
        return ("Long", "Fade at range low (mean reversion)")
    return None


def d_compression_breakout(snaps, i):
    s = snaps[i]
    if s["range_width"] is None or not _atr_ok(s):
        return None
    if s["range_width"] > 1.0 * s["atr"]:
        return None
    if s["close"] > s["range_high"] and s["volume_confirmed"]:
        return ("Long", "Compression breakout above coil")
    if s["close"] < s["range_low"] and s["volume_confirmed"]:
        return ("Short", "Compression breakdown below coil")
    return None


RESEARCH_DETECTORS = {
    "vwap_pullback_continuation": d_vwap_pullback_continuation,
    "vwap_reclaim_fail":          d_vwap_reclaim_fail,
    "opening_range_breakout":     d_opening_range_breakout,
    "opening_range_fakeout":      d_opening_range_fakeout,
    "liquidity_sweep_reversal":   d_liquidity_sweep_reversal,
    "failed_breakdown_breakout":  d_failed_breakdown_breakout,
    "micro_pullback_scalp":       d_micro_pullback_scalp,
    "ema_9_20_continuation":      d_ema_9_20_continuation,
    "fvg_continuation":           d_fvg_continuation,
    "order_block_rejection":      d_order_block_rejection,
    "prior_high_low_sweep":       d_prior_high_low_sweep,
    "session_high_low_reclaim":   d_session_high_low_reclaim,
    "volume_climax_reversal":     d_volume_climax_reversal,
    "cvd_divergence_scalp":       d_cvd_divergence_scalp,
    "range_edge_mean_reversion":  d_range_edge_mean_reversion,
    "compression_breakout":       d_compression_breakout,
}

# Existing LIVE engine strategies — run through the SAME research simulation so
# new candidates are compared apples-to-apples against what already trades.
EXISTING_BASELINE = {
    "OPENING_DRIVE":            "Opening Drive (live)",
    "LIQUIDITY_SWEEP_REVERSAL": "Liquidity Sweep Reversal (live)",
    "VWAP_TREND_CONTINUATION":  "VWAP Trend Continuation (live)",
    "RANGE_EXPANSION_BREAKOUT": "Range Expansion Breakout (live)",
}


def _wrap_single_bar(fn):
    return lambda snaps, i: fn(snaps[i])


# ════════════════════════════════════════════════════════════════════════════
# STRATEGY LIBRARY — the catalog. 19 named professional scalp strategies.
# backtest_status: "testable" (has a research detector) | "data_pending" |
# "detector_pending". live_status starts "watch" for ALL — never live here.
# ════════════════════════════════════════════════════════════════════════════
def _s(key, name, source_type, market_type, timeframe, setup_rules, entry_trigger,
       stop_logic, target_logic, required_confirmations, avoid_conditions,
       example_chart_pattern, confidence_level, backtest_status):
    return {
        "strategy_key": key, "strategy_name": name, "source_type": source_type,
        "market_type": market_type, "timeframe": timeframe, "setup_rules": setup_rules,
        "entry_trigger": entry_trigger, "stop_logic": stop_logic, "target_logic": target_logic,
        "required_confirmations": required_confirmations, "avoid_conditions": avoid_conditions,
        "example_chart_pattern": example_chart_pattern, "confidence_level": confidence_level,
        "backtest_status": backtest_status, "live_status": "watch",
    }


_MKT = "MGC, MNQ"
_TF = "1m–5m"

STRATEGY_LIBRARY = [
    _s("vwap_pullback_continuation", "VWAP Pullback Continuation", "trend / institutional", _MKT, _TF,
       "Established intraday trend with price above (long) / below (short) VWAP; price pulls back toward VWAP without losing it.",
       "Confirmed reversal candle at VWAP in the trend direction (BOS/CHOCH or confirmation candle).",
       "Beyond the pullback swing / opposite side of VWAP plus buffer.",
       "1R primary (live-consistent); optional runner once trend extends.",
       "Trend alignment, price respecting VWAP, volume confirmation.",
       "Chop with no clear trend; price oscillating across VWAP.",
       "Higher-low sequence tagging VWAP then pushing to new high.",
       "high", "testable"),
    _s("vwap_reclaim_fail", "VWAP Reclaim / Fail", "trend / mean-reversion", _MKT, _TF,
       "Price crosses back through VWAP after trading on the other side — reclaim (long) or loss/fail (short).",
       "Close back across VWAP on rising relative volume.",
       "Other side of VWAP / reclaim candle low (long) or high (short).",
       "1R primary; target prior swing on continuation.",
       "Rising RVOL on the cross; momentum in the cross direction.",
       "Low-volume drift across VWAP; news spike whipsaw.",
       "Price loses VWAP, snaps back above and holds.",
       "medium", "testable"),
    _s("opening_range_breakout", "Opening Range Breakout (ORB)", "session / momentum", _MKT, _TF,
       "After the opening range forms, price breaks the OR high (long) or low (short) on the same side as VWAP.",
       "Close beyond OR extreme with volume confirmation.",
       "Opposite OR extreme / structure stop.",
       "1R primary; measured-move runner on strong drives.",
       "Volume confirmation, VWAP alignment, opening window.",
       "Pre-OR completion; thin pre-market; range-bound open.",
       "Tight opening range then expansion candle through the high.",
       "high", "testable"),
    _s("opening_range_fakeout", "Opening Range Fakeout", "session / reversal", _MKT, _TF,
       "Price pokes beyond the OR extreme then closes back inside — trapped breakout traders.",
       "Close back inside the OR after a wick beyond it, against VWAP-extended side.",
       "Beyond the fakeout wick extreme.",
       "1R primary; target opposite OR edge / VWAP.",
       "Failed close beyond OR, reversal candle, VWAP context.",
       "Clean trending opens; strong one-way drive.",
       "Wick above OR high, close back inside, roll over.",
       "medium", "testable"),
    _s("liquidity_sweep_reversal", "Liquidity Sweep Reversal", "smart-money / reversal", _MKT, _TF,
       "Stop run beyond a swing/level (sweep) that immediately reverses and reclaims.",
       "Sweep flag fires and price reclaims toward VWAP.",
       "Beyond the sweep extreme plus buffer.",
       "1R primary; runner to opposing liquidity.",
       "Sweep + reclaim; VWAP proximity.",
       "Sweep that keeps going (real breakout); no reclaim.",
       "Spike below support, instant reclaim, V-reversal.",
       "high", "testable"),
    _s("failed_breakdown_breakout", "Failed Breakdown / Failed Breakout", "reversal", _MKT, _TF,
       "Range edge breaks but price fails to follow through and reclaims the range.",
       "Reclaim of the range edge after a break attempt.",
       "Beyond the failed break extreme.",
       "1R primary; target opposite range edge.",
       "Reclaim candle; failure of follow-through.",
       "Genuine range expansion with momentum.",
       "Break below range, snap back inside, squeeze up.",
       "medium", "testable"),
    _s("trendline_break_retest", "Trendline Break & Retest", "price-action", _MKT, _TF,
       "Diagonal trendline breaks, price retests the broken line and continues in the break direction.",
       "Reaction candle at the retest of the broken trendline.",
       "Beyond the retest swing.",
       "1R primary; measured move of the prior channel.",
       "Clean trendline with 3+ touches; clean break + retest.",
       "Poorly-defined trendlines; low-quality touches.",
       "Ascending line breaks down, retests from below, drops.",
       "medium", "detector_pending"),
    _s("micro_pullback_scalp", "Micro Pullback Scalp", "momentum", _MKT, _TF,
       "In a strong momentum leg, a 1–2 bar shallow pullback then immediate continuation.",
       "Break of the prior bar high (long) / low (short) after a micro pullback.",
       "Below the micro-pullback low (long).",
       "1R primary; quick scalp exit.",
       "Strong trend, VWAP alignment, shallow pullback.",
       "Deep pullbacks; trend exhaustion; chop.",
       "Three green bars, one small red, new high.",
       "medium", "testable"),
    _s("ema_9_20_continuation", "9/20 EMA Continuation Scalp", "trend", _MKT, _TF,
       "9 over 20 EMA (long) stack; price pulls back to the 9 EMA and resumes.",
       "Reclaim of the 9 EMA in the trend direction after the pullback.",
       "Below the 20 EMA / pullback swing.",
       "1R primary; runner while EMAs stay stacked.",
       "EMA stack alignment, trend, pullback to 9 EMA.",
       "EMAs flat/tangled; counter-trend.",
       "Price rides 9 EMA, dips to it, bounces.",
       "medium", "testable"),
    _s("fvg_continuation", "FVG Continuation Scalp", "smart-money", _MKT, _TF,
       "Trend with a fair-value gap (imbalance); price retraces into it and continues. PROXY: demand/supply zone stands in for the FVG (OHLCV has no precise gap object).",
       "Reaction candle inside the imbalance zone in the trend direction.",
       "Beyond the far side of the imbalance zone.",
       "1R primary; runner to the next imbalance.",
       "Trend alignment; reaction inside the zone; confirmation.",
       "No clear imbalance; counter-trend fills.",
       "Gap up, pull back into the gap, continue up.",
       "medium", "testable"),
    _s("order_block_rejection", "Order Block Rejection", "smart-money", _MKT, _TF,
       "Price taps a supply/demand order block and rejects. PROXY: nearest demand/supply zone stands in for the order block.",
       "Rejection candle at the block (close back out of the zone).",
       "Beyond the order block.",
       "1R primary; target opposing block.",
       "Clean block; rejection wick; trend context.",
       "Mid-range blocks; already-mitigated zones.",
       "Rally into supply, long wick, reverse down.",
       "medium", "testable"),
    _s("prior_high_low_sweep", "Prior High/Low Sweep", "liquidity", _MKT, _TF,
       "Price sweeps a prior swing high/low then rejects back inside.",
       "Reclaim of the prior swing level after the sweep wick.",
       "Beyond the sweep extreme.",
       "1R primary; runner to the opposing swing.",
       "Clear prior swing; rejection after the sweep.",
       "Strong trend that runs prior levels and holds.",
       "Wick over yesterday's high, close back below, fade.",
       "medium", "testable"),
    _s("session_high_low_reclaim", "Session High/Low Reclaim", "session / liquidity", _MKT, _TF,
       "Price breaks the running session high/low then reclaims back inside (failed session breakout).",
       "Close back inside the session range after the break.",
       "Beyond the session extreme wick.",
       "1R primary; target session VWAP / opposite edge.",
       "Session extreme break + reclaim; volume.",
       "Clean session-trend days that hold breaks.",
       "Spike to new session high, reclaim down, sell.",
       "medium", "testable"),
    _s("volume_climax_reversal", "Volume Climax Reversal", "exhaustion / reversal", _MKT, _TF,
       "A climactic high-RVOL bar against the prior move signals exhaustion and reverses.",
       "Reversal candle on a >2.5x RVOL climax bar.",
       "Beyond the climax bar extreme.",
       "1R primary; quick exhaustion scalp.",
       "RVOL spike; reversal close; VWAP context.",
       "Trending high-volume continuation (not climax).",
       "Huge red bar after a rally, next bar reverses up.",
       "medium", "testable"),
    _s("cvd_divergence_scalp", "CVD Divergence Scalp", "order-flow", _MKT, _TF,
       "Price makes a new extreme but cumulative delta does not (divergence). PROXY: CVD here is OHLCV-derived, not true order-flow delta.",
       "Reversal candle confirming the divergence.",
       "Beyond the divergent price extreme.",
       "1R primary; target prior swing.",
       "Clear price/CVD divergence; reversal confirmation.",
       "Trending tape where CVD confirms price.",
       "Price lower low, CVD higher low, bounce.",
       "low", "testable"),
    _s("delta_exhaustion_reversal", "Delta Exhaustion Reversal", "order-flow", _MKT, _TF,
       "Aggressive delta dries up at an extreme (absorption / exhaustion) and price reverses. Requires true footprint/delta, which the OHLCV backtest does not contain.",
       "Absorption / delta-flip signal at the extreme.",
       "Beyond the exhaustion extreme.",
       "1R primary.",
       "Footprint delta exhaustion; absorption.",
       "No order-flow data available.",
       "Heavy selling absorbed at lows, reversal.",
       "low", "data_pending"),
    _s("range_edge_mean_reversion", "Range Edge Mean Reversion", "mean-reversion", _MKT, _TF,
       "In a ranging regime, fade the range extremes back toward the middle/VWAP.",
       "Rejection candle at the range edge.",
       "Beyond the range edge.",
       "1R primary; target range mid / VWAP.",
       "Ranging regime; rejection at the edge.",
       "Trending/expansion regimes; breakouts.",
       "Price hits range top, stalls, reverts to mid.",
       "medium", "testable"),
    _s("compression_breakout", "Compression Breakout", "volatility", _MKT, _TF,
       "Volatility contracts into a tight coil, then expands with a breakout.",
       "Close beyond the coil with volume confirmation.",
       "Opposite side of the coil.",
       "1R primary; measured move of the coil.",
       "Tight range vs ATR; volume on the break.",
       "Already-extended price; no contraction.",
       "Narrowing bars, then one expansion candle out.",
       "high", "testable"),
    _s("news_volatility_fade", "News Volatility Fade (after first spike)", "event / volatility", _MKT, _TF,
       "After the first sharp spike on a high-impact news release, fade the overextension once it stalls. Requires a high-impact economic calendar feed, which is not wired into the backtest.",
       "Reversal candle after the initial spike stalls.",
       "Beyond the spike extreme.",
       "1R primary; target pre-spike level.",
       "Confirmed news time; spike + stall; reversal.",
       "No econ-calendar feed available for backtest.",
       "News spike up, stalls, fades back to pre-news.",
       "low", "data_pending"),
]

LIBRARY_BY_KEY = {e["strategy_key"]: e for e in STRATEGY_LIBRARY}


# ════════════════════════════════════════════════════════════════════════════
# SIMULATION — R-based (live-consistent 1:1) adaptation of
# backtest_engine.simulate_strategy that accepts a (snaps, i) detector. Reuses
# bt.bt_stop_plan / bt._walk_managed / bt._strategy_metrics. No money path.
# ════════════════════════════════════════════════════════════════════════════
def _enrich(snaps):
    """Add research-only derived fields (EMA 9/20 + running session extremes) to
    each snapshot. Operates on the freshly-built snapshot list for a research run
    (never the live path), so in-place mutation is safe and isolated."""
    k9, k20 = 2.0 / (9 + 1), 2.0 / (20 + 1)
    ema9 = ema20 = None
    cur_bucket = None
    sh = sl = None
    for s in snaps:
        c = s["close"]
        ema9 = c if ema9 is None else (c * k9 + ema9 * (1 - k9))
        ema20 = c if ema20 is None else (c * k20 + ema20 * (1 - k20))
        s["ema9"], s["ema20"] = ema9, ema20
        try:
            day_key = bt._session_for_et(s["et"])[1] or s["et"].date().isoformat()
        except Exception:
            day_key = s["et"].date().isoformat()
        bucket = (s["session"], day_key)
        if bucket != cur_bucket:
            cur_bucket, sh, sl = bucket, s["high"], s["low"]
        else:
            sh, sl = max(sh, s["high"]), min(sl, s["low"])
        s["sess_high"], s["sess_low"] = sh, sl
    return snaps


def simulate_research(snaps, candles, detector, spec, mode=RESEARCH_MODE,
                      slippage_ticks=1.0, commission_per_side=0.62,
                      management=RESEARCH_MGMT,
                      max_trades_per_session=bt.MAX_TRADES_PER_SESSION,
                      min_target_r=bt.MIN_TARGET_R,
                      news_blackouts_et=bt.NEWS_BLACKOUTS_ET):
    """Replay one research strategy over the snapshots. Mirrors the R-based path
    of backtest_engine.simulate_strategy: entry on the bar AFTER a close-confirmed
    signal (next-bar open ± slippage), worst-case same-bar fill in _walk_managed,
    one open position at a time, the same causal no-trade filters. Returns a list
    of closed-trade dicts compatible with bt._strategy_metrics."""
    trades = []
    n = len(snaps)
    tick = spec["tick_size"]
    pv = spec["point_value"]
    slip = slippage_ticks * tick
    session_counts = {}
    commission = commission_per_side * 2.0
    i = 0
    while i < n - 1:
        s = snaps[i]
        sig = detector(snaps, i)
        if not sig:
            i += 1
            continue
        direction, entry_reason = sig
        # Conflict guard: skip if opposite same-bar structure break.
        if direction == "Long" and s["choch_short"]:
            i += 1
            continue
        if direction == "Short" and s["choch_long"]:
            i += 1
            continue
        et_sig = s["et"]
        h_et = et_sig.hour + et_sig.minute / 60.0
        if news_blackouts_et and any(lo <= h_et < hi for lo, hi in news_blackouts_et):
            i += 1
            continue
        try:
            day_key = bt._session_for_et(et_sig)[1] or et_sig.date().isoformat()
        except Exception:
            day_key = et_sig.date().isoformat()
        sess_key = (s["session"] or "Off-hours", day_key)
        if max_trades_per_session and session_counts.get(sess_key, 0) >= max_trades_per_session:
            i += 1
            continue
        entry_bar = i + 1
        raw_entry = candles[entry_bar]["open"]
        entry = raw_entry + slip if direction == "Long" else raw_entry - slip
        plan = bt.bt_stop_plan(direction, entry, s["demand_zone"], s["supply_zone"],
                               spec, s["atr"], mode, s["regime"])
        if plan is None:
            i += 1
            continue
        stop = plan["stop"]
        risk = plan["risk_points"]
        # R-based entry reference: live fixed 1:1 baseline (same gate as the engine's
        # R-based models). min_target_r <= 1.0 admits; above 1.0 filters by design.
        if min_target_r and 1.0 < min_target_r:
            i += 1
            continue
        (exit_price, exit_bar, exit_reason, r_gross) = bt._walk_managed(
            candles, entry_bar, direction, entry, stop, risk, slip, management)
        comm_r = (commission / (risk * pv)) if risk > 0 else 0.0
        r_mult = r_gross - comm_r
        gross_pts = r_gross * risk
        pnl_dollars = r_gross * risk * pv - commission
        entry_ts = candles[entry_bar]["ts"]
        exit_ts = candles[exit_bar]["ts"]
        hold_min = (exit_ts - entry_ts).total_seconds() / 60.0
        trades.append({
            "direction": direction,
            "entry_ts": entry_ts.isoformat(), "exit_ts": exit_ts.isoformat(),
            "entry": round(entry, 4), "stop": round(stop, 4), "exit": round(exit_price, 4),
            "risk_points": round(risk, 4), "gross_points": round(gross_pts, 4),
            "pnl_dollars": round(pnl_dollars, 2), "r_multiple": round(r_mult, 4),
            "regime": s["regime"], "session": s["session"] or "Off-hours",
            "entry_hour_et": entry_ts.astimezone(bt.ET_TZ).hour,
            "entry_reason": entry_reason, "exit_reason": exit_reason,
            "hold_minutes": round(hold_min, 1),
        })
        session_counts[sess_key] = session_counts.get(sess_key, 0) + 1
        i = max(exit_bar, entry_bar) + 1
    return trades


def _best_session(trades):
    if not trades:
        return None
    by = {}
    for t in trades:
        by.setdefault(t["session"], []).append(t["r_multiple"])
    best = max(by, key=lambda k: sum(by[k]))
    return best


def _pf_num(pf):
    """Convert the _strategy_metrics profit_factor (number | '∞' | None) to a
    comparable float (inf for '∞', None for undefined)."""
    if pf is None:
        return None
    if isinstance(pf, (int, float)):
        return float(pf)
    if pf == "∞":
        return float("inf")
    try:
        return float(pf)
    except (TypeError, ValueError):
        return None


def _metrics(trades):
    m = bt._strategy_metrics(trades)
    m["best_session"] = _best_session(trades)
    return m


# ════════════════════════════════════════════════════════════════════════════
# RESEARCH ORCHESTRATION
# ════════════════════════════════════════════════════════════════════════════
def _prepare(datasets):
    """datasets: list of {"symbol","dataset_id","candles","window"}. Compute +
    enrich snapshots once per dataset. Skips empty/too-small sets (fail-open)."""
    prepared = []
    for d in datasets:
        candles = d.get("candles") or []
        if len(candles) < 50:
            continue
        try:
            snaps = bt.compute_indicators(candles, RESEARCH_MODE)
            _enrich(snaps)
        except Exception:
            continue
        prepared.append({"symbol": d["symbol"], "dataset_id": d.get("dataset_id"),
                         "window": d.get("window"), "candles": candles, "snaps": snaps})
    return prepared


def _summ(m):
    """Compact metric summary for serialization / dashboard."""
    return {
        "total_trades": m["total_trades"], "win_rate": m["win_rate"],
        "avg_r": m["avg_r"], "net_r": m["net_r"], "profit_factor": m["profit_factor"],
        "max_drawdown_r": m["max_drawdown_r"], "best_session": m.get("best_session"),
        "best_hour_label": m.get("best_hour_label"), "tradable": m["tradable"],
    }


def run_research(datasets, mgmt=RESEARCH_MGMT):
    """Run the full research pass. Returns {"generated_at", "rows" (DB upserts),
    "view" (dashboard-ready)}. Pure / read-only / fail-open."""
    gen_at = datetime.now(timezone.utc).isoformat()
    prepared = _prepare(datasets)
    rows = []
    per_strat = {}

    def _run_one(key, name, detector, source_type, confidence, is_live):
        ps = {"strategy_key": key, "strategy_name": name, "source_type": source_type,
              "confidence_level": confidence, "is_live": is_live, "by_symbol": {},
              "combined": None}
        combined_trades = []
        for d in prepared:
            spec = bt.BT_SPECS.get(d["symbol"], bt.BT_SPECS["MGC"])
            try:
                trades = simulate_research(d["snaps"], d["candles"], detector, spec,
                                           RESEARCH_MODE, management=mgmt)
            except Exception:
                trades = []
            m = _metrics(trades)
            ps["by_symbol"][d["symbol"]] = {
                "dataset_id": d["dataset_id"], "window": d["window"], "metrics": _summ(m)}
            combined_trades.extend(trades)
            rows.append({
                "strategy_key": key, "symbol": d["symbol"], "dataset_id": d["dataset_id"],
                "mode": RESEARCH_MODE, "management": mgmt, "total_trades": m["total_trades"],
                "win_rate": m["win_rate"], "avg_r": m["avg_r"], "net_r": m["net_r"],
                "profit_factor": _pf_num(m["profit_factor"]) if _pf_num(m["profit_factor"]) != float("inf") else None,
                "max_drawdown_r": m["max_drawdown_r"], "best_session": m.get("best_session"),
                "best_hour_label": m.get("best_hour_label"),
                "expectancy": m["avg_r"], "tradable": m["tradable"], "sample_window": d["window"],
            })
        cm = _metrics(combined_trades)
        ps["combined"] = _summ(cm)
        per_strat[key] = ps
        return ps

    # Research candidates (only those with a detector get simulated).
    for entry in STRATEGY_LIBRARY:
        key = entry["strategy_key"]
        det = RESEARCH_DETECTORS.get(key)
        if det is None:
            per_strat[key] = {
                "strategy_key": key, "strategy_name": entry["strategy_name"],
                "source_type": entry["source_type"], "confidence_level": entry["confidence_level"],
                "is_live": False, "by_symbol": {}, "combined": None,
                "backtest_status": entry["backtest_status"]}
            continue
        _run_one(key, entry["strategy_name"], det, entry["source_type"],
                 entry["confidence_level"], False)

    # Existing live strategies — same engine, for comparison.
    for k, label in EXISTING_BASELINE.items():
        _run_one(k, label, _wrap_single_bar(bt.DETECTORS[k]), "live-engine", "live", True)

    view = _build_view(per_strat, gen_at, prepared, mgmt)
    return {"generated_at": gen_at, "rows": rows, "view": view}


def _is_promotable(combined, by_symbol):
    """ADVISORY ONLY. Returns (bool, reason). Never mutates anything."""
    if not combined or combined["total_trades"] < MIN_PROMO_TRADES:
        n = combined["total_trades"] if combined else 0
        return (False, "only %d examples (need %d)" % (n, MIN_PROMO_TRADES))
    if not combined["tradable"]:
        return (False, "negative expectancy on the sample")
    if (combined["avg_r"] or 0) <= 0:
        return (False, "average R not positive")
    pf = _pf_num(combined["profit_factor"])
    if pf is not None and pf != float("inf") and pf < MIN_PROMO_PF:
        return (False, "profit factor %.2f below %.2f" % (pf, MIN_PROMO_PF))
    for sym, d in by_symbol.items():
        if (d["metrics"]["net_r"] or 0) <= 0:
            return (False, "not consistent — %s net R is negative" % sym)
    return (True, "%d examples, PF %s, avg R %s, positive on all tested instruments" % (
        combined["total_trades"], combined["profit_factor"], combined["avg_r"]))


def _build_view(per_strat, gen_at, prepared, mgmt):
    datasets_meta = [{"symbol": d["symbol"], "dataset_id": d["dataset_id"],
                      "window": d["window"], "bars": len(d["candles"])} for d in prepared]

    # Catalog (library) — merge static catalog with computed status.
    library = []
    for entry in STRATEGY_LIBRARY:
        library.append(dict(entry))

    # Strategies being tested = research candidates with a detector + >=1 trade.
    tested, ranking, pending, promotions = [], [], [], []
    promote_keys = set()
    for entry in STRATEGY_LIBRARY:
        key = entry["strategy_key"]
        ps = per_strat.get(key, {})
        det = RESEARCH_DETECTORS.get(key)
        if det is None:
            pending.append({
                "strategy_key": key, "strategy_name": entry["strategy_name"],
                "source_type": entry["source_type"], "confidence_level": entry["confidence_level"],
                "backtest_status": entry["backtest_status"],
                "note": "defined; awaiting data/detector; no stats yet"})
            continue
        combined = ps.get("combined")
        tested.append({
            "strategy_key": key, "strategy_name": entry["strategy_name"],
            "source_type": entry["source_type"], "confidence_level": entry["confidence_level"],
            "backtest_status": ("tested" if combined and combined["total_trades"] > 0
                                else entry["backtest_status"]),
            "combined": combined, "by_symbol": ps.get("by_symbol", {})})
        ok, reason = _is_promotable(combined, ps.get("by_symbol", {}))
        if ok:
            promote_keys.add(key)
            promotions.append({
                "strategy_key": key, "strategy_name": entry["strategy_name"],
                "recommendation": "WATCH \u2192 recommend manual review for promotion",
                "reason": reason, "combined": combined})

    # Ranking across BOTH research candidates and live strategies (labelled).
    for key, ps in per_strat.items():
        combined = ps.get("combined")
        if not combined or combined["total_trades"] <= 0:
            continue
        ranking.append({
            "strategy_key": key, "strategy_name": ps.get("strategy_name", key),
            "is_live": ps.get("is_live", False), "combined": combined})
    ranking_sorted = sorted(
        ranking, key=lambda r: (r["combined"]["net_r"] if r["combined"]["net_r"] is not None else -1e9),
        reverse=True)
    best = ranking_sorted[:TOP_N]
    worst = list(reversed(ranking_sorted[-TOP_N:])) if len(ranking_sorted) > TOP_N else \
        list(reversed(ranking_sorted))

    # live_status labels for the catalog (advisory): recommended / simulation / watch.
    live_status_map = {}
    for entry in STRATEGY_LIBRARY:
        key = entry["strategy_key"]
        if RESEARCH_DETECTORS.get(key) is None:
            live_status_map[key] = "watch"
        elif key in promote_keys:
            live_status_map[key] = "recommended"
        else:
            live_status_map[key] = "simulation"
    for lib in library:
        lib["live_status"] = live_status_map.get(lib["strategy_key"], "watch")

    return {
        "ready": True, "generated_at": gen_at, "management": mgmt,
        "datasets": datasets_meta,
        "counts": {
            "total": len(STRATEGY_LIBRARY),
            "testable": sum(1 for e in STRATEGY_LIBRARY if RESEARCH_DETECTORS.get(e["strategy_key"])),
            "pending": len(pending),
            "in_simulation": sum(1 for v in live_status_map.values() if v == "simulation"),
            "recommended": len(promote_keys),
        },
        "library": library,
        "tested": tested,
        "best": best,
        "worst": worst,
        "pending": pending,
        "promotions": promotions,
        "live_status_map": live_status_map,
        "safety_note": "Research/simulation only. New strategies never auto-trade live; "
                       "promotion is a manual human decision.",
    }


if __name__ == "__main__":
    # Import smoke only (no DB here). Verifies the module loads + catalog integrity.
    assert len(STRATEGY_LIBRARY) == 19, len(STRATEGY_LIBRARY)
    keys = [e["strategy_key"] for e in STRATEGY_LIBRARY]
    assert len(keys) == len(set(keys)), "duplicate strategy_key"
    for e in STRATEGY_LIBRARY:
        assert e["live_status"] == "watch"
        assert e["backtest_status"] in ("testable", "data_pending", "detector_pending")
    for k in RESEARCH_DETECTORS:
        assert k in LIBRARY_BY_KEY, k
    print("scalp_research OK:", len(STRATEGY_LIBRARY), "strategies,",
          len(RESEARCH_DETECTORS), "detectors,",
          sum(1 for e in STRATEGY_LIBRARY if e["backtest_status"] != "testable"), "pending")
