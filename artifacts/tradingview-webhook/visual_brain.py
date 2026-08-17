"""Visual Brain V1 — Multi-Instrument 1-Minute Stateful Market Observer.

Captures the live chart (or a locally-generated candlestick
chart from Databento bars) once per 60 seconds, sends the image plus a compact
text history to a vision-capable LLM, receives a strict JSON market state, and
persists every observation to visual_brain_observations.

SHADOW / OBSERVATION ONLY.
- Does NOT place trades.
- Does NOT modify SCALP / INTRADAY_TREND / SWING decision logic.
- Does NOT alter gate thresholds, edge scores, or execution paths.
- All writes FAIL-OPEN — a bug here cannot affect the money path.
- One model call maximum per 60 seconds; screenshot compressed to ≤800px wide.

Flag: VISUAL_BRAIN_ENABLED=true  (default OFF → byte-identical).
Instruments: VISUAL_BRAIN_SYMBOL=MNQ,MGC,MES,MYM  (comma-separated; default all 4).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
VISUAL_BRAIN_ENABLED   = os.getenv("VISUAL_BRAIN_ENABLED", "false").lower() in ("true", "1", "yes")
VISUAL_BRAIN_SYMBOL    = os.getenv("VISUAL_BRAIN_SYMBOL", "MNQ,MGC,MES,MYM")
# Parsed list — used by all multi-instrument logic; VISUAL_BRAIN_SYMBOL kept for compat
_VB_SYMBOLS: list = [s.strip().upper() for s in VISUAL_BRAIN_SYMBOL.split(",") if s.strip()]
VISUAL_BRAIN_INTERVAL  = max(60, int(os.getenv("VISUAL_BRAIN_INTERVAL_SECONDS", "60")))
_VB_MODEL              = "gpt-5.4"               # vision-capable; matches ASSISTANT_MODEL used throughout app.py
_VB_MAX_TOKENS         = 1200
_VB_IMAGE_MAX_PX       = 800                      # max width before JPEG downscale
_VB_HISTORY_LIMIT      = 10                       # last N state transitions passed to model
_VB_SCREENSHOT_TIMEOUT = 30                       # seconds per Playwright capture
_CHART_BARS_LOOKBACK   = 60                       # bars sent to chart renderer

# ── Module-level state ────────────────────────────────────────────────────────
VB_DB_READY          = False
_VB_LOCK             = threading.Lock()
# Per-instrument last observation cache (replaces single _LAST_OBSERVATION).
_LAST_OBSERVATION_BY_INST: dict = {}   # instrument → most recent parsed observation dict

# ── Injected dependencies (set by check_vb_db_ready() and start()) ────────────
# These are injected at boot time by app.py so that visual_brain.py never needs
# to `import app` itself.  When app.py runs as __main__, doing `import app`
# from a sub-module loads a SECOND copy of app.py under the name `app` with
# empty globals — breaking DB connections, price stores, and bar history.
#
# _db_conn_fn  : callable() → psycopg2 connection | None
# _price_store : dict reference {instrument: {"value": float}} (AUTO_PRICE_BY_TICKER)
# _vwap_store  : dict reference {instrument: {"value": float, ...}} (VWAP_BY_TICKER)
# _bars_fn     : callable(instrument: str) → list[dict]  (live Databento bars)
_db_conn_fn  : Optional[Callable] = None
_price_store : Optional[dict]     = None
_vwap_store  : Optional[dict]     = None
_bars_fn     : Optional[Callable] = None

# ── Cost tracking (resets at midnight ET) ────────────────────────────────────
_COST_LOCK           = threading.Lock()
_vb_calls_today      = 0
_vb_cost_today       = 0.0          # estimated USD
_vb_cost_reset_day   : Optional[str] = None   # "YYYY-MM-DD" in ET

# ── Estimated token costs for gpt-5.4 (per 1M tokens, USD) ──────────────────
# Vision input ≈ $0.15/1M; output ≈ $0.60/1M
_COST_PER_INPUT_TOK  = 0.00000015
_COST_PER_OUTPUT_TOK = 0.00000060


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_conn():
    """Return a Postgres connection or None (fail-open).

    Uses _db_conn_fn injected at start()/check_vb_db_ready() time.
    Never imports app — see module docstring for why.
    """
    if _db_conn_fn is None:
        return None
    try:
        return _db_conn_fn()
    except Exception as exc:
        logger.debug("visual_brain _get_conn: %s", exc)
        return None


def check_vb_db_ready(db_conn_fn: Optional[Callable] = None) -> None:
    """Probe visual_brain_observations; set VB_DB_READY. FAIL-OPEN.

    Args:
        db_conn_fn: callable returning a psycopg2 connection (injected from
                    app.py's _learning_conn).  If provided, stores it as the
                    module-level _db_conn_fn for all subsequent DB calls.
    """
    global VB_DB_READY, _db_conn_fn
    if db_conn_fn is not None:
        _db_conn_fn = db_conn_fn
    conn = _get_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM visual_brain_observations LIMIT 1")
            cur.fetchone()
        VB_DB_READY = True
        logger.info("[VISUAL_BRAIN] visual_brain_observations table ready")
    except Exception as exc:
        logger.warning("[VISUAL_BRAIN] DB probe failed — table missing?: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Cost tracking
# ─────────────────────────────────────────────────────────────────────────────

def _et_date_str() -> str:
    """Today's date string in ET (UTC-4 approximate)."""
    from datetime import timezone, timedelta  # noqa: PLC0415
    et = datetime.now(timezone(timedelta(hours=-4)))
    return et.strftime("%Y-%m-%d")


def _record_cost(input_tokens: int, output_tokens: int) -> None:
    global _vb_calls_today, _vb_cost_today, _vb_cost_reset_day
    today = _et_date_str()
    with _COST_LOCK:
        if _vb_cost_reset_day != today:
            _vb_calls_today = 0
            _vb_cost_today  = 0.0
            _vb_cost_reset_day = today
        _vb_calls_today += 1
        _vb_cost_today  += (input_tokens * _COST_PER_INPUT_TOK
                            + output_tokens * _COST_PER_OUTPUT_TOK)


def get_cost_summary() -> dict:
    """Return today's cost counters (for /visual-brain/cost route)."""
    today = _et_date_str()
    with _COST_LOCK:
        if _vb_cost_reset_day != today:
            return {"calls_today": 0, "cost_today_usd": 0.0, "date": today}
        return {
            "calls_today":    _vb_calls_today,
            "cost_today_usd": round(_vb_cost_today, 6),
            "date":           today,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Screenshot capture
# ─────────────────────────────────────────────────────────────────────────────

# TradingView public embed URLs per instrument — 1-minute chart, no login required.
# Playwright fallback only; primary path uses matplotlib from Databento bars.
_TV_EMBED_URLS: dict = {
    "MNQ": (
        "https://www.tradingview.com/chart/?symbol=CME_MINI%3ANQ1%21&interval=1"
        "&theme=dark&hide_side_toolbar=1&hide_top_toolbar=1&allow_symbol_change=0"
    ),
    "MGC": (
        "https://www.tradingview.com/chart/?symbol=COMEX%3AMG1%21&interval=1"
        "&theme=dark&hide_side_toolbar=1&hide_top_toolbar=1&allow_symbol_change=0"
    ),
    "MES": (
        "https://www.tradingview.com/chart/?symbol=CME_MINI%3AES1%21&interval=1"
        "&theme=dark&hide_side_toolbar=1&hide_top_toolbar=1&allow_symbol_change=0"
    ),
    "MYM": (
        "https://www.tradingview.com/chart/?symbol=CBOT_MINI%3AYM1%21&interval=1"
        "&theme=dark&hide_side_toolbar=1&hide_top_toolbar=1&allow_symbol_change=0"
    ),
}

def _compress_image(raw_png: bytes, max_px: int = _VB_IMAGE_MAX_PX) -> bytes:
    """Resize + JPEG-compress screenshot bytes; returns JPEG bytes."""
    from PIL import Image  # noqa: PLC0415
    img = Image.open(io.BytesIO(raw_png)).convert("RGB")
    w, h = img.size
    if w > max_px:
        ratio = max_px / w
        img = img.resize((max_px, int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def _generate_chart_from_bars(bars: list, instrument: str) -> bytes:
    """Fallback: render a matplotlib OHLCV candlestick chart from Databento bars.
    Returns JPEG bytes.  Used when Playwright screenshot fails.
    """
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    import matplotlib.patches as mpatches  # noqa: PLC0415

    if not bars:
        raise ValueError("no bars for chart generation")

    recent = bars[-_CHART_BARS_LOOKBACK:]
    times  = list(range(len(recent)))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("#050c1a")
    for ax in (ax1, ax2):
        ax.set_facecolor("#0b1628")
        ax.tick_params(colors="white")
        ax.spines[:].set_color("#1e3a5f")

    # ── Candlesticks ──
    for i, b in enumerate(recent):
        o, h, lo, c = b.get("open", 0), b.get("high", 0), b.get("low", 0), b.get("close", 0)
        col = "#22c55e" if c >= o else "#ef4444"
        ax1.plot([i, i], [lo, h], color=col, linewidth=0.8, zorder=1)
        ax1.add_patch(mpatches.FancyBboxPatch(
            (i - 0.3, min(o, c)), 0.6, abs(c - o) or 0.5,
            boxstyle="square,pad=0", linewidth=0, facecolor=col, zorder=2
        ))

    # ── VWAP line — cumulative from session open (all bars), not just the window ──
    # Computing from only the last 60 bars resets the running average and produces
    # a value that diverges significantly from the true session VWAP, especially
    # late in the trading day.  We accumulate from bar 0 (all bars) and only
    # *display* the portion that falls within the visible window.
    cum_pv = 0.0; cum_v = 0.0; all_vwap = []
    for b in bars:
        tp = (b.get("high", 0) + b.get("low", 0) + b.get("close", 0)) / 3.0
        v  = b.get("volume", 1) or 1
        cum_pv += tp * v; cum_v += v
        all_vwap.append(cum_pv / cum_v if cum_v else tp)
    vwap_vals = all_vwap[-len(recent):]      # trim to visible bars
    ax1.plot(times, vwap_vals, color="#38bdf8", linewidth=1.2, linestyle="--",
             label="VWAP", zorder=3)

    # ── Live VWAP overlay — authoritative value from VWAP_BY_TICKER ──────────
    # Shown as a solid horizontal annotation so the model can read the exact
    # level and cross-check it against the cumulative line above.
    if _vwap_store is not None:
        live_vwap_rec = _vwap_store.get(instrument) or {}
        live_vwap_val = live_vwap_rec.get("value")
        if live_vwap_val is not None:
            try:
                lv = float(live_vwap_val)
                ax1.axhline(lv, color="#7dd3fc", linewidth=0.9, linestyle="-",
                            alpha=0.55, zorder=2)
                ax1.text(len(recent) - 1, lv,
                         f" Live VWAP {lv:,.1f}",
                         color="#7dd3fc", fontsize=6.5, va="bottom", zorder=4)
            except (TypeError, ValueError):
                pass

    # ── Current price label ──
    last_close = recent[-1].get("close", 0) if recent else 0
    ax1.axhline(last_close, color="#f59e0b", linewidth=0.8, linestyle=":")
    ax1.text(len(recent) - 1, last_close, f" {last_close:,.1f}",
             color="#f59e0b", fontsize=7, va="center")

    ax1.set_title(f"{instrument} — 1m  ({len(recent)} bars)", color="white", fontsize=10)
    ax1.legend(fontsize=7, facecolor="#0b1628", labelcolor="white")

    # ── Volume bars ──
    for i, b in enumerate(recent):
        o, c = b.get("open", 0), b.get("close", 0)
        ax2.bar(i, b.get("volume", 0), color="#22c55e" if c >= o else "#ef4444",
                width=0.8, alpha=0.7)
    ax2.set_ylabel("Vol", color="#94a3b8", fontsize=7)

    plt.tight_layout(pad=0.4)
    buf = io.BytesIO()
    plt.savefig(buf, format="jpeg", dpi=90, bbox_inches="tight",
                facecolor="#050c1a")
    plt.close(fig)
    return buf.getvalue()


def capture_chart_screenshot(symbol: str = "MNQ", timeout_s: int = _VB_SCREENSHOT_TIMEOUT) -> bytes:
    """Capture MNQ chart.  Returns JPEG bytes.

    Strategy (in priority order):
    1. Matplotlib OHLCV chart from injected Databento bars — always available when
       live market data is flowing; no browser binary required.  This is the
       reliable primary path for server deployments.
    2. Playwright headless Chromium → TradingView embed — optional enhancement
       that adds the full exchange chart.  Requires `playwright install chromium`
       to be run after package install.  Skipped gracefully when the binary is
       absent; Strategy 1 is used instead.

    Raises RuntimeError when both strategies fail so the worker can catch and log.
    """
    errors = []

    # ── Strategy 1: Matplotlib from injected Databento bars (reliable primary) ─
    try:
        if _bars_fn is None:
            raise ValueError("bars_fn not injected — call start() before use")
        bars = _bars_fn(symbol)
        if not bars:
            raise ValueError("no live bars available yet")
        if len(bars) < 5:
            raise ValueError(f"only {len(bars)} bar(s) available — need ≥5 for meaningful analysis")
        return _generate_chart_from_bars(bars, symbol)
    except Exception as exc:
        errors.append(f"matplotlib: {exc}")
        logger.debug("[VISUAL_BRAIN] Matplotlib chart generation failed: %s", exc)

    # ── Strategy 2: Playwright headless Chromium → TradingView embed ──────────
    # Optional: only attempted when the Playwright binary is installed.
    # Install with: playwright install chromium-headless-shell
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-gpu", "--disable-setuid-sandbox"],
            )
            ctx  = browser.new_context(
                viewport={"width": 1024, "height": 600},
                java_script_enabled=True,
            )
            page = ctx.new_page()
            tv_url = _TV_EMBED_URLS.get(symbol, _TV_EMBED_URLS["MNQ"])
            if symbol not in _TV_EMBED_URLS:
                logger.debug("[VISUAL_BRAIN] %s has no TradingView URL; using MNQ fallback", symbol)
            page.goto(tv_url, timeout=timeout_s * 1000, wait_until="networkidle")
            try:
                page.wait_for_selector("canvas", timeout=15000)
                page.wait_for_timeout(3000)   # let chart render
            except Exception:
                page.wait_for_timeout(5000)   # fallback wait
            raw = page.screenshot(full_page=False, type="png")
            browser.close()
        return _compress_image(raw)
    except Exception as exc:
        errors.append(f"playwright: {exc}")
        logger.debug("[VISUAL_BRAIN] Playwright screenshot failed: %s", exc)

    raise RuntimeError(f"All screenshot strategies failed: {'; '.join(errors)}")


# ─────────────────────────────────────────────────────────────────────────────
# Visual analysis via GPT vision
# ─────────────────────────────────────────────────────────────────────────────

# Human-readable labels for each supported instrument — used in the system prompt
# so the model knows what asset it is looking at.
_INSTRUMENT_LABELS: dict = {
    "MNQ": "MNQ (Micro E-mini Nasdaq)",
    "MGC": "MGC (Micro Gold)",
    "MES": "MES (Micro E-mini S&P 500)",
    "MYM": "MYM (Micro E-mini Dow Jones)",
}


def _get_system_prompt(instrument: str) -> str:
    """Return the analysis system prompt with the correct instrument label."""
    label = _INSTRUMENT_LABELS.get(instrument, instrument)
    return f"""You are a disciplined institutional futures trader analyzing a 1-minute {label} chart image.

Your job: describe ONLY what you can directly observe in the chart. Never guess or predict specific future prices.

Analyze:
- Market structure (HH/HL = bullish; LH/LL = bearish; range)
- Higher highs, higher lows, lower highs, lower lows
- Consolidation, range, breakout, breakdown, failed breakout/breakdown
- Support and resistance interactions
- Liquidity sweeps, reclaims, rejections, retests
- Momentum expansion vs exhaustion
- Continuation vs reversal patterns
- Price location relative to visible VWAP and key levels
- Chop / indecision

Respond with ONLY valid JSON — no markdown fences, no commentary outside the JSON.

Required schema (all fields mandatory):
{{
  "instrument": "{instrument}",
  "timestamp": "<current ISO UTC>",
  "bias": "BULLISH|BEARISH|NEUTRAL",
  "market_state": "TRENDING_UP|TRENDING_DOWN|RANGE|REVERSAL|BREAKOUT|BREAKDOWN|RETEST|CHOP|UNCLEAR",
  "structure": {{
    "short_term": "HH_HL|LH_LL|RANGE|TRANSITION|UNCLEAR",
    "higher_low_intact": true,
    "lower_high_intact": false
  }},
  "last_event": "LIQUIDITY_SWEEP|RECLAIM|REJECTION|BREAKOUT|BREAKDOWN|RETEST|FAILED_BREAKOUT|FAILED_BREAKDOWN|STRUCTURE_SHIFT|NONE",
  "support": {{
    "visible": true,
    "description": "brief description or empty string",
    "approx_price": null
  }},
  "resistance": {{
    "visible": true,
    "description": "brief description or empty string",
    "approx_price": null
  }},
  "long_condition": "what would validate a long entry",
  "short_condition": "what would validate a short entry",
  "action": "LONG_WATCH|SHORT_WATCH|WAIT|NO_TRADE",
  "confidence": 72,
  "state_changed": true,
  "state_change_reason": "one sentence or empty string",
  "summary": "Maximum 2 concise sentences describing what you see."
}}

Rules:
- confidence: integer 0-100
- approx_price: null when unreadable — NEVER hallucinate a price
- state_changed: compare to the previous state summary provided; set true only if something meaningful shifted
- Respond ONLY with the JSON object, nothing else
"""

_VALID_BIASES      = {"BULLISH", "BEARISH", "NEUTRAL"}
_VALID_STATES      = {"TRENDING_UP","TRENDING_DOWN","RANGE","REVERSAL","BREAKOUT","BREAKDOWN","RETEST","CHOP","UNCLEAR"}
_VALID_EVENTS      = {"LIQUIDITY_SWEEP","RECLAIM","REJECTION","BREAKOUT","BREAKDOWN","RETEST",
                      "FAILED_BREAKOUT","FAILED_BREAKDOWN","STRUCTURE_SHIFT","NONE"}
_VALID_ACTIONS     = {"LONG_WATCH","SHORT_WATCH","WAIT","NO_TRADE"}
_VALID_SHORT_TERM  = {"HH_HL","LH_LL","RANGE","TRANSITION","UNCLEAR"}
_REQUIRED_KEYS     = {"instrument","timestamp","bias","market_state","structure",
                      "last_event","support","resistance","long_condition",
                      "short_condition","action","confidence","state_changed",
                      "state_change_reason","summary"}


def _build_history_text(recent_history: list[dict]) -> str:
    """Build a compact text summary of recent state transitions."""
    if not recent_history:
        return "No prior observations."
    lines = []
    for obs in recent_history[-_VB_HISTORY_LIMIT:]:
        ts_raw = obs.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts = dt.strftime("%H:%M")
        except Exception:
            ts = ts_raw[:5]
        state   = obs.get("market_state", "?")
        event   = obs.get("last_event", "")
        bias    = obs.get("bias", "?")
        action  = obs.get("action", "?")
        conf    = obs.get("confidence", "?")
        changed = "→ SHIFT" if obs.get("state_changed") else ""
        lines.append(f"{ts} | {bias} | {state} | {event} | {action} @{conf}% {changed}")
    return "\n".join(lines)


def _validate_observation(obs: dict) -> tuple[bool, str]:
    """Check required keys and valid enum values.  Returns (ok, reason)."""
    missing = _REQUIRED_KEYS - set(obs.keys())
    if missing:
        return False, f"missing keys: {missing}"
    if obs.get("bias") not in _VALID_BIASES:
        return False, f"invalid bias: {obs.get('bias')}"
    if obs.get("market_state") not in _VALID_STATES:
        return False, f"invalid market_state: {obs.get('market_state')}"
    if obs.get("last_event") not in _VALID_EVENTS:
        return False, f"invalid last_event: {obs.get('last_event')}"
    if obs.get("action") not in _VALID_ACTIONS:
        return False, f"invalid action: {obs.get('action')}"
    struct = obs.get("structure", {})
    if not isinstance(struct, dict):
        return False, "structure must be a dict"
    if struct.get("short_term") not in _VALID_SHORT_TERM:
        return False, f"invalid structure.short_term: {struct.get('short_term')}"
    conf = obs.get("confidence")
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 100):
        return False, f"invalid confidence: {conf}"
    return True, ""


def analyze_visual_market(
    screenshot_bytes: bytes,
    previous_state: Optional[dict],
    recent_history: list[dict],
    instrument: str = "MNQ",
) -> dict:
    """Send screenshot + compact history to GPT-4o vision; return parsed dict.

    Makes at most 2 API calls (1 retry on invalid JSON or schema violation).
    Logs token counts and estimated cost.
    Raises on total failure — caller wraps in FAIL-OPEN try/except.
    """
    api_key  = os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY", "")
    base_url = os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL", "https://api.openai.com/v1")

    if not api_key:
        raise RuntimeError("AI_INTEGRATIONS_OPENAI_API_KEY not set")

    from openai import OpenAI  # noqa: PLC0415
    client = OpenAI(api_key=api_key, base_url=base_url)

    now_utc = datetime.now(timezone.utc).isoformat()
    history_text = _build_history_text(recent_history)
    prev_summary = ""
    if previous_state:
        prev_summary = (
            f"Previous observation: bias={previous_state.get('bias')} "
            f"state={previous_state.get('market_state')} "
            f"event={previous_state.get('last_event')} "
            f"action={previous_state.get('action')} "
            f"conf={previous_state.get('confidence')}%\n"
            f"Previous summary: {previous_state.get('summary', '')}"
        )

    b64_img = base64.b64encode(screenshot_bytes).decode()

    user_content = [
        {
            "type": "text",
            "text": (
                f"Instrument: {instrument}\n"
                f"Current UTC time: {now_utc}\n\n"
                f"--- PREVIOUS STATE ---\n{prev_summary or 'First observation.'}\n\n"
                f"--- STATE HISTORY (newest last) ---\n{history_text}\n\n"
                "Analyze the attached chart image and respond with ONLY the JSON object."
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{b64_img}",
                "detail": "low",   # cost control: low detail = fewer tokens
            },
        },
    ]

    last_exc = None
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=_VB_MODEL,
                max_completion_tokens=_VB_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": _get_system_prompt(instrument)},
                    {"role": "user",   "content": user_content},
                ],
            )
            usage = resp.usage
            in_tok  = usage.prompt_tokens     if usage else 0
            out_tok = usage.completion_tokens if usage else 0
            _record_cost(in_tok, out_tok)
            logger.info(
                "[VISUAL_BRAIN] model=%s in_tok=%d out_tok=%d est_cost=$%.5f attempt=%d",
                _VB_MODEL, in_tok, out_tok,
                in_tok * _COST_PER_INPUT_TOK + out_tok * _COST_PER_OUTPUT_TOK,
                attempt + 1,
            )

            raw = resp.choices[0].message.content or ""
            # Strip markdown fences if model adds them despite instructions
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            obs = json.loads(raw)
            ok, reason = _validate_observation(obs)
            if not ok:
                raise ValueError(f"Schema violation: {reason}")
            obs["timestamp"] = now_utc   # authoritative server timestamp
            obs["instrument"] = instrument
            return obs

        except Exception as exc:
            last_exc = exc
            logger.warning("[VISUAL_BRAIN] analyze attempt %d failed: %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(1)

    raise RuntimeError(f"analyze_visual_market failed after 2 attempts: {last_exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Database persistence
# ─────────────────────────────────────────────────────────────────────────────

def _insert_observation(obs: dict, screenshot_path: Optional[str] = None) -> Optional[int]:
    """Insert one observation row; returns the new id or None on failure."""
    if not VB_DB_READY:
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        struct = obs.get("structure") or {}
        support = obs.get("support") or {}
        resistance = obs.get("resistance") or {}
        cur_price = None
        try:
            if _price_store is not None:
                inst = obs.get("instrument", "MNQ")
                cur_price = _price_store.get(inst, {}).get("value")
        except Exception:
            pass

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO visual_brain_observations
                  (timestamp, instrument, bias, market_state, short_term_structure,
                   last_event, action, confidence,
                   support_description, support_price,
                   resistance_description, resistance_price,
                   long_condition, short_condition,
                   state_changed, state_change_reason, summary,
                   screenshot_path, raw_json, entry_price_at_obs)
                VALUES
                  (%s, %s, %s, %s, %s,
                   %s, %s, %s,
                   %s, %s,
                   %s, %s,
                   %s, %s,
                   %s, %s, %s,
                   %s, %s, %s)
                RETURNING id
            """, (
                obs.get("timestamp"),
                obs.get("instrument", "MNQ"),
                obs.get("bias"),
                obs.get("market_state"),
                struct.get("short_term"),
                obs.get("last_event"),
                obs.get("action"),
                int(obs.get("confidence") or 0),
                support.get("description") or "",
                support.get("approx_price"),
                resistance.get("description") or "",
                resistance.get("approx_price"),
                obs.get("long_condition") or "",
                obs.get("short_condition") or "",
                bool(obs.get("state_changed")),
                obs.get("state_change_reason") or "",
                obs.get("summary") or "",
                screenshot_path,
                json.dumps(obs),
                cur_price,
            ))
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
    except Exception as exc:
        logger.warning("[VISUAL_BRAIN] DB insert failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# History + status reads
# ─────────────────────────────────────────────────────────────────────────────

def _flatten_obs(obs: dict) -> dict:
    """Flatten the nested model response to match the DB history row shape.

    The model returns nested structure/support/resistance objects; the DB
    stores flat columns (short_term_structure, support_description, etc.).
    This ensures get_last_observation() and get_history() return the same
    shape so the dashboard panel renders correctly from both sources.

    Safe to call on an already-flat dict — setdefault guards prevent
    overwriting values that are already present.
    """
    if not obs:
        return obs
    flat = dict(obs)
    struct = obs.get("structure") or {}
    sup    = obs.get("support")   or {}
    res    = obs.get("resistance") or {}

    # Remove nested objects and promote their keys to top level
    flat.pop("structure",  None)
    flat.pop("support",    None)
    flat.pop("resistance", None)

    flat.setdefault("short_term_structure",   struct.get("short_term"))
    flat.setdefault("support_description",    sup.get("description", ""))
    flat.setdefault("support_price",          sup.get("approx_price"))
    flat.setdefault("resistance_description", res.get("description", ""))
    flat.setdefault("resistance_price",       res.get("approx_price"))

    # DB-only fields absent in the raw model response — default to None/False
    for key in ("id", "screenshot_path", "p1m", "p3m", "p5m",
                "p10m", "p15m", "mfe", "mae"):
        flat.setdefault(key, None)
    flat.setdefault("outcome_resolved", False)
    return flat


def get_last_observation(instrument: str = "MNQ") -> Optional[dict]:
    """Return the most recent observation dict from cache or DB, flattened.

    Always returns the same key shape as get_history() so callers do not need
    to handle two different structures.
    """
    with _VB_LOCK:
        cached = _LAST_OBSERVATION_BY_INST.get(instrument)
        if cached:
            return _flatten_obs(dict(cached))
    if not VB_DB_READY:
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT raw_json FROM visual_brain_observations
                WHERE instrument = %s
                ORDER BY timestamp DESC LIMIT 1
            """, (instrument,))
            row = cur.fetchone()
            if row and row[0]:
                raw = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                return _flatten_obs(raw)
        return None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_history(instrument: str = "MNQ", limit: int = 20) -> list[dict]:
    """Return the last N observations from DB (newest first)."""
    if not VB_DB_READY:
        return []
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, timestamp, instrument, bias, market_state,
                       short_term_structure, last_event, action, confidence,
                       support_description, support_price,
                       resistance_description, resistance_price,
                       long_condition, short_condition,
                       state_changed, state_change_reason, summary,
                       screenshot_path, p1m, p3m, p5m, p10m, p15m,
                       mfe, mae, outcome_resolved
                FROM visual_brain_observations
                WHERE instrument = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (instrument, min(limit, 100)))
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            result = []
            for r in rows:
                row_dict = dict(zip(cols, r))
                # Coerce timestamp to ISO string
                ts = row_dict.get("timestamp")
                if hasattr(ts, "isoformat"):
                    row_dict["timestamp"] = ts.isoformat()
                # Coerce NUMERIC (Decimal) outcome fields to float so Flask can
                # serialize them as JSON numbers.  psycopg2 returns pg NUMERIC as
                # Python Decimal, which json.dumps() rejects and the TS client
                # receives as a quoted string, breaking .toFixed() calls.
                for key in ("support_price", "resistance_price",
                            "p1m", "p3m", "p5m", "p10m", "p15m", "mfe", "mae"):
                    v = row_dict.get(key)
                    if v is not None:
                        row_dict[key] = float(v)
                result.append(row_dict)
            return result
    except Exception as exc:
        logger.debug("[VISUAL_BRAIN] get_history: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Ghost outcome backfill watcher
# ─────────────────────────────────────────────────────────────────────────────

def _backfill_ghost_outcomes() -> None:
    """Backfill p1m/p3m/p5m/p10m/p15m/mfe/mae for unresolved observations.

    Uses injected _bars_fn and _price_store — never imports app or databento_brain
    directly (see module docstring for why).
    """
    if not VB_DB_READY:
        return
    if _bars_fn is None:
        return   # bars not injected yet; skip silently

    conn = _get_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            # Find unresolved rows older than 15 minutes
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
            cur.execute("""
                SELECT id, timestamp, instrument, action, entry_price_at_obs
                FROM visual_brain_observations
                WHERE outcome_resolved = FALSE
                  AND timestamp < %s
                ORDER BY timestamp DESC
                LIMIT 50
            """, (cutoff,))
            rows = cur.fetchall()

        for row_id, obs_ts, inst, action, entry_px in rows:
            if not entry_px:
                # No entry price recorded at observation time — skip
                conn2 = _get_conn()
                if conn2:
                    try:
                        with conn2.cursor() as c2:
                            c2.execute(
                                "UPDATE visual_brain_observations SET outcome_resolved=TRUE WHERE id=%s",
                                (row_id,)
                            )
                            conn2.commit()
                    except Exception:
                        pass
                    finally:
                        try: conn2.close()
                        except Exception: pass
                continue

            bars = _bars_fn(inst or "MNQ") if _bars_fn else []
            if not bars:
                continue

            # Find bars after the observation timestamp
            obs_epoch = obs_ts.timestamp() if hasattr(obs_ts, "timestamp") else 0
            future_bars = [
                b for b in bars
                if (b.get("ts_event") or b.get("ts", 0)) > obs_epoch
            ]
            if not future_bars:
                continue

            # Only compute directional P&L for actionable observations.
            # WAIT and NO_TRADE have no bias — storing them as "SHORT" would
            # corrupt research analytics.  Mark these resolved with NULL outcomes.
            if action not in ("LONG_WATCH", "SHORT_WATCH"):
                conn2 = _get_conn()
                if conn2:
                    try:
                        with conn2.cursor() as c2:
                            c2.execute(
                                "UPDATE visual_brain_observations "
                                "SET outcome_resolved=TRUE WHERE id=%s",
                                (row_id,)
                            )
                            conn2.commit()
                    except Exception:
                        pass
                    finally:
                        try: conn2.close()
                        except Exception: pass
                continue

            direction = 1 if action == "LONG_WATCH" else -1

            def _excursion(bars_slice: list) -> tuple[float, float]:
                """Return (favorable_pct, adverse_pct) for direction."""
                if not bars_slice:
                    return 0.0, 0.0
                highs  = [b.get("high", entry_px) for b in bars_slice]
                lows   = [b.get("low",  entry_px) for b in bars_slice]
                if direction == 1:
                    fav = (max(highs) - entry_px) / entry_px * 100
                    adv = (entry_px - min(lows))  / entry_px * 100
                else:
                    fav = (entry_px - min(lows))  / entry_px * 100
                    adv = (max(highs) - entry_px) / entry_px * 100
                return round(fav, 4), round(adv, 4)

            def _close_at(n_bars: int) -> Optional[float]:
                if len(future_bars) >= n_bars:
                    close = future_bars[n_bars - 1].get("close", entry_px)
                    return round((close - entry_px) / entry_px * direction * 100, 4)
                return None

            p1m  = _close_at(1)
            p3m  = _close_at(3)
            p5m  = _close_at(5)
            p10m = _close_at(10)
            p15m = _close_at(15)
            mfe, mae = _excursion(future_bars[:15])

            conn3 = _get_conn()
            if conn3:
                try:
                    with conn3.cursor() as c3:
                        c3.execute("""
                            UPDATE visual_brain_observations
                            SET p1m=%s, p3m=%s, p5m=%s, p10m=%s, p15m=%s,
                                mfe=%s, mae=%s, outcome_resolved=TRUE
                            WHERE id=%s
                        """, (p1m, p3m, p5m, p10m, p15m, mfe, mae, row_id))
                        conn3.commit()
                    logger.debug("[VISUAL_BRAIN] ghost outcome resolved: id=%d p1m=%s p15m=%s",
                                 row_id, p1m, p15m)
                except Exception as ue:
                    logger.debug("[VISUAL_BRAIN] ghost outcome update failed: %s", ue)
                    try: conn3.rollback()
                    except Exception: pass
                finally:
                    try: conn3.close()
                    except Exception: pass
    except Exception as exc:
        logger.debug("[VISUAL_BRAIN] _backfill_ghost_outcomes: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Main worker loop
# ─────────────────────────────────────────────────────────────────────────────

_VB_TIMERS: dict = {}   # instrument → active threading.Timer


def _vb_tick(instrument: str = "MNQ") -> None:
    """One observation cycle for one instrument: capture → analyze → persist → reschedule.

    Scheduling contract: `_schedule_next(instrument)` is called EXACTLY ONCE,
    exclusively in `finally`.  Early returns from failure paths must NOT call
    `_schedule_next()` themselves — doing so would double-schedule timers on every
    persistent failure, rapidly creating overlapping model work and runaway API cost.

    The enabled guard is checked BEFORE `try/finally` so that a disabled tick
    is a true no-op: no reschedule, no timers accumulated.  `_schedule_next()`
    itself is also a no-op when disabled, keeping the disabled path byte-identical.
    """
    global _VB_TIMERS

    if not VISUAL_BRAIN_ENABLED:
        return   # disabled → true no-op; no timer accumulated

    try:
        screenshot_bytes: Optional[bytes] = None

        # ── Capture ──────────────────────────────────────────────────────────
        # Screenshots are ephemeral analysis inputs — they are NOT persisted to
        # disk.  Saving delete=False temp files would exhaust local storage over
        # time.  screenshot_path is always stored as NULL; use object storage if
        # permanent retention is needed (see Task #189).
        try:
            screenshot_bytes = capture_chart_screenshot(instrument)
        except Exception as cap_exc:
            logger.warning("[VISUAL_BRAIN] screenshot failed: %s", cap_exc)

        if not screenshot_bytes:
            logger.warning("[VISUAL_BRAIN] skipping cycle — no screenshot")
            return   # finally → _schedule_next() fires exactly once

        # ── Fetch history ────────────────────────────────────────────────────
        recent_history = get_history(instrument, limit=_VB_HISTORY_LIMIT)
        # get_history returns newest-first; reverse for chronological model context
        recent_history.reverse()

        with _VB_LOCK:
            _cached_prev = _LAST_OBSERVATION_BY_INST.get(instrument)
            prev_state = dict(_cached_prev) if _cached_prev else None

        # ── Analyze ──────────────────────────────────────────────────────────
        try:
            obs = analyze_visual_market(
                screenshot_bytes=screenshot_bytes,
                previous_state=prev_state,
                recent_history=recent_history,
                instrument=instrument,
            )
        except Exception as analyze_exc:
            logger.warning("[VISUAL_BRAIN] analysis failed: %s", analyze_exc)
            return   # finally → _schedule_next() fires exactly once

        # ── Persist ──────────────────────────────────────────────────────────
        obs_id = _insert_observation(obs, screenshot_path=None)
        with _VB_LOCK:
            _LAST_OBSERVATION_BY_INST[instrument] = dict(obs)
        logger.info(
            "[VISUAL_BRAIN] obs id=%s bias=%s state=%s event=%s action=%s conf=%s%%",
            obs_id,
            obs.get("bias"),
            obs.get("market_state"),
            obs.get("last_event"),
            obs.get("action"),
            obs.get("confidence"),
        )

        # ── Ghost outcome backfill (non-blocking) ────────────────────────────
        threading.Thread(target=_backfill_ghost_outcomes, daemon=True).start()

    except Exception as exc:
        logger.error("[VISUAL_BRAIN] tick error (trading engine unaffected): %s", exc)

    finally:
        # Single reschedule point — always reached, never duplicated.
        _schedule_next(instrument)


def _schedule_next(instrument: str = "MNQ", delay: Optional[float] = None) -> None:
    """Schedule the next tick for `instrument`.

    `delay` overrides the interval for the initial staggered start.
    First-tick delays are VISUAL_BRAIN_INTERVAL + i*slot (≥60s) so all instruments
    wait for boot to complete before any screenshot/model work begins.
    When None (the default), uses VISUAL_BRAIN_INTERVAL so regular ticks fire at
    the configured cadence regardless of how long each analysis took.
    """
    global _VB_TIMERS
    if not VISUAL_BRAIN_ENABLED:
        return
    interval = delay if delay is not None else float(VISUAL_BRAIN_INTERVAL)
    t = threading.Timer(interval, _vb_tick, args=(instrument,))
    t.daemon = True
    t.start()
    _VB_TIMERS[instrument] = t


def start(
    db_conn_fn: Optional[Callable] = None,
    price_store: Optional[dict] = None,
    vwap_store: Optional[dict] = None,
    bars_fn: Optional[Callable] = None,
) -> None:
    """Start the Visual Brain worker.  Call once at boot if VISUAL_BRAIN_ENABLED.

    Args:
        db_conn_fn:  callable() → psycopg2 connection — injected from app.py's
                     _learning_conn so the REAL __main__ module connection pool
                     is used.  Never stored as a module-level `import app`.
        price_store: dict reference to AUTO_PRICE_BY_TICKER from app.py's
                     __main__ globals (not a copy — same object so prices update).
        vwap_store:  dict reference to VWAP_BY_TICKER from app.py's __main__
                     globals — used to overlay the authoritative session VWAP on
                     the chart so the model reads the correct level.
        bars_fn:     callable(instrument: str) → list[dict] wrapping
                     DATABENTO_BARS_BY_INST.get() from app.py's __main__ globals.
    """
    global _db_conn_fn, _price_store, _vwap_store, _bars_fn
    if db_conn_fn is not None:
        _db_conn_fn = db_conn_fn
    if price_store is not None:
        _price_store = price_store
    if vwap_store is not None:
        _vwap_store = vwap_store
    if bars_fn is not None:
        _bars_fn = bars_fn

    if not VISUAL_BRAIN_ENABLED:
        logger.info("[VISUAL_BRAIN] disabled (VISUAL_BRAIN_ENABLED not set) — byte-identical mode")
        return
    logger.info("[VISUAL_BRAIN] enabled — instruments: %s  interval=%ds",
                ", ".join(_VB_SYMBOLS), VISUAL_BRAIN_INTERVAL)
    # Each instrument's first tick is delayed by at least one full VISUAL_BRAIN_INTERVAL
    # (preserving the original "let boot complete first" safety margin) plus a
    # per-instrument slot offset that spreads all first ticks across the 60-second
    # window, preventing a concurrent model-call burst at startup.
    #
    # With n=4 and interval=60s → slots of 15s → delays: 60, 75, 90, 105.
    # With n=1 (single-instrument override) → delay: 60 (matches original behaviour).
    n = max(len(_VB_SYMBOLS), 1)
    slot = float(VISUAL_BRAIN_INTERVAL) / n
    for i, inst in enumerate(_VB_SYMBOLS):
        first_delay = float(VISUAL_BRAIN_INTERVAL) + float(i) * slot
        logger.info("[VISUAL_BRAIN] %s observer first_tick_delay=%.0fs", inst, first_delay)
        _schedule_next(inst, delay=first_delay)
