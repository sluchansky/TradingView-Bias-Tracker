"""
FVG Engine — Fair Value Gap / Inverse FVG Scanner
Step A: Shadow / Diagnostic mode — DISPLAY ONLY.
=================================================
Runs all-day on the Databento 1-minute bar stream for MGC, MNQ, MES, MYM.

Safety contract:
  * NEVER modifies gate verdicts, edge scores, position sizes, or execution.
  * NEVER sends broker orders or Discord alerts from this module.
  * Always returns well-formed dicts — callers need no null-checks.
  * Fail-open: any error in detection/lifecycle is logged and suppressed.
  * When FVG_ENGINE_ENABLED=0, all public APIs return empty/neutral results.

Architecture:
  * process_bar_close(inst, bars) — called by app.py on every Databento bar-close.
  * Each call (1) detects new FVGs in the latest 3 candles, (2) updates lifecycle
    of all known zones for that instrument using the most recent bar.
  * In-memory store: FVG_ZONES_BY_INST (dict[str, list[dict]]), protected by _LOCK.
  * DB persistence: SELECT/INSERT/UPDATE only — fvg_zones table created out-of-band.
  * Shadow practice rows written per sequence outcome for Part 16 analytics.
"""
from __future__ import annotations

import os
import uuid
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# ── Feature flag ──────────────────────────────────────────────────────────────
FVG_ENGINE_ENABLED = os.environ.get("FVG_ENGINE_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off"
)

# ── Configuration (all overridable via env) ───────────────────────────────────
FVG_MIN_SIZE_ATR        = float(os.environ.get("FVG_MIN_SIZE_ATR",         "0.08"))  # gap >= 8% of ATR
FVG_MIN_SIZE_POINTS     = float(os.environ.get("FVG_MIN_SIZE_POINTS",      "0.0"))   # absolute min (0 = ATR only)
FVG_DISPLACEMENT_MIN    = float(os.environ.get("FVG_DISPLACEMENT_MIN",     "1.2"))   # mid-candle body/ATR
FVG_MAX_AGE_BARS        = int(os.environ.get("FVG_MAX_AGE_BARS",           "90"))    # bars before EXPIRED
FVG_MITIGATION_PCT      = float(os.environ.get("FVG_MITIGATION_PCT",       "0.50"))  # 50% fill → MITIGATED
FVG_MAX_ZONES_PER_INST  = int(os.environ.get("FVG_MAX_ZONES_PER_INST",     "30"))    # cap per instrument
FVG_ATR_PERIOD          = int(os.environ.get("FVG_ATR_PERIOD",             "14"))    # bars for ATR calc
FVG_DB_ENABLED          = True  # follows LEARNING_DB_ENABLED at runtime (checked lazily)

# ── Lifecycle status constants ────────────────────────────────────────────────
ST_ACTIVE    = "ACTIVE"
ST_TOUCHED   = "TOUCHED"
ST_MITIGATED = "MITIGATED"
ST_HOLDING   = "HOLDING"
ST_FAILED    = "FAILED"
ST_INVERTED  = "INVERTED"
ST_RETESTED  = "RETESTED"
ST_EXPIRED   = "EXPIRED"

TERMINAL_STATUSES  = frozenset({ST_FAILED, ST_EXPIRED, ST_RETESTED})
TRADEABLE_STATUSES = frozenset({ST_ACTIVE, ST_TOUCHED, ST_HOLDING, ST_INVERTED})

# ── In-memory store ───────────────────────────────────────────────────────────
FVG_ZONES_BY_INST: Dict[str, List[Dict[str, Any]]] = {}   # inst → list of zone dicts
_LOCK = threading.Lock()
_DB_READY = False
_BAR_COUNTS: Dict[str, int] = {}   # inst → total bars seen (for age tracking)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compute_atr(bars: List[Dict], period: int = FVG_ATR_PERIOD) -> float:
    """Simple average true range using high-low of last `period` bars."""
    relevant = bars[-period:] if len(bars) >= period else bars
    if not relevant:
        return 1.0  # safe fallback — never 0
    total = sum((b.get("high", 0) - b.get("low", 0)) for b in relevant)
    return max(total / len(relevant), 1e-6)


def _make_zone(
    inst: str,
    direction: str,
    lower: float,
    upper: float,
    bar_ts: Any,
    displacement_strength: float,
    size_atr: float,
    parent_fvg_id: Optional[str] = None,
    ifvg_direction: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a canonical FVG zone dict (matches the DB schema)."""
    midpoint = round((lower + upper) / 2, 6)
    now = _now_utc()
    return {
        "fvg_id":               str(uuid.uuid4()),
        "instrument":           inst,
        "direction":            direction,
        "created_at":           now,
        "bar_ts":               str(bar_ts),
        "lower":                round(lower, 6),
        "upper":                round(upper, 6),
        "midpoint":             midpoint,
        "size_points":          round(upper - lower, 6),
        "size_atr":             round(size_atr, 4),
        "source_timeframe":     "1m",
        "displacement_strength": round(displacement_strength, 4),
        "status":               ST_ACTIVE,
        "touch_count":          0,
        "first_touch_at":       None,
        "mitigated_at":         None,
        "invalidated_at":       None,
        "inverted_at":          None,
        "parent_fvg_id":        parent_fvg_id,
        "ifvg_direction":       ifvg_direction,  # set when this zone IS an IFVG
        "rank_score":           0.0,
        "rank_components":      {},
        "updated_at":           now,
        "bar_age":              0,   # incremented each bar — not persisted
        "_persisted":           False,
    }

# ── Detection ─────────────────────────────────────────────────────────────────

def _detect_new_fvgs(
    inst: str,
    bars: List[Dict],
    atr: float,
    known_ts: set,
) -> List[Dict]:
    """
    Scan the last 3 completed bars for new FVG patterns.

    Bullish FVG:  bar[-3].high < bar[-1].low   → gap [bar[-3].high, bar[-1].low]
    Bearish FVG:  bar[-3].low  > bar[-1].high  → gap [bar[-1].high, bar[-3].low]

    The middle candle (bar[-2]) must show displacement (body >= FVG_DISPLACEMENT_MIN * ATR).
    Gap must meet minimum size filters.
    Deduplication: use bar[-1].ts as the canonical fingerprint for this gap.
    """
    if len(bars) < 3:
        return []

    b1, b2, b3 = bars[-3], bars[-2], bars[-1]

    # Dedup guard — each 3-candle pattern identified by its anchor bar ts
    ts_key = str(b3.get("ts", ""))
    if ts_key in known_ts:
        return []

    h1 = _safe_float(b1.get("high"))
    l1 = _safe_float(b1.get("low"))
    l3 = _safe_float(b3.get("low"))
    h3 = _safe_float(b3.get("high"))
    o2 = _safe_float(b2.get("open"))
    c2 = _safe_float(b2.get("close"))

    if None in (h1, l1, l3, h3, o2, c2):
        return []

    mid_body = abs(c2 - o2)
    displacement = mid_body / atr if atr else 0.0

    new_zones: List[Dict] = []

    # ── Bullish FVG: gap UP (candle 3 low > candle 1 high) ───────────────────
    if h1 < l3:
        gap_size = l3 - h1
        gap_atr  = gap_size / atr if atr else 0.0
        if (gap_size >= FVG_MIN_SIZE_POINTS and
                gap_atr  >= FVG_MIN_SIZE_ATR and
                displacement >= FVG_DISPLACEMENT_MIN and
                c2 > o2):   # displacement candle must be bullish
            zone = _make_zone(
                inst=inst,
                direction="BULLISH",
                lower=h1,
                upper=l3,
                bar_ts=b3.get("ts"),
                displacement_strength=displacement,
                size_atr=gap_atr,
            )
            new_zones.append(zone)
            logger.info(
                "FVG detected: %s BULLISH [%.4f–%.4f] size=%.2f pts (%.2f ATR) disp=%.2fx",
                inst, h1, l3, gap_size, gap_atr, displacement,
            )

    # ── Bearish FVG: gap DOWN (candle 1 low > candle 3 high) ─────────────────
    if l1 > h3:
        gap_size = l1 - h3
        gap_atr  = gap_size / atr if atr else 0.0
        if (gap_size >= FVG_MIN_SIZE_POINTS and
                gap_atr  >= FVG_MIN_SIZE_ATR and
                displacement >= FVG_DISPLACEMENT_MIN and
                c2 < o2):   # displacement candle must be bearish
            zone = _make_zone(
                inst=inst,
                direction="BEARISH",
                lower=h3,
                upper=l1,
                bar_ts=b3.get("ts"),
                displacement_strength=displacement,
                size_atr=gap_atr,
            )
            new_zones.append(zone)
            logger.info(
                "FVG detected: %s BEARISH [%.4f–%.4f] size=%.2f pts (%.2f ATR) disp=%.2fx",
                inst, h3, l1, gap_size, gap_atr, displacement,
            )

    return new_zones


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def _update_zone_lifecycle(
    zone: Dict,
    bar: Dict,
    atr: float,
    bar_age: int,
) -> Optional[Dict]:
    """
    Apply one completed bar to a zone's lifecycle.

    Returns a new IFVG zone dict if this bar triggers an inversion,
    otherwise None.

    Rules (see module docstring):
      Bullish FVG [lower, upper]: price returns from ABOVE.
        TOUCHED  when bar.low  <= zone.upper
        MITIGATED when bar.low  <= zone.midpoint
        FAILED   when bar.close < zone.lower  (closed below full zone)
        HOLDING  when TOUCHED and bar.close > zone.upper  (rejected back up)

      Bearish FVG [lower, upper]: price returns from BELOW.
        TOUCHED  when bar.high >= zone.lower
        MITIGATED when bar.high >= zone.midpoint
        FAILED   when bar.close > zone.upper  (closed above full zone)
        HOLDING  when TOUCHED and bar.close < zone.lower (rejected back down)

      INVERTED zone (IFVG): reverse touch logic applies.
        Bullish IFVG [lower, upper] — retest from below:
          RETESTED when bar.high >= zone.lower
        Bearish IFVG [lower, upper] — retest from above:
          RETESTED when bar.low  <= zone.upper
    """
    if zone["status"] in TERMINAL_STATUSES:
        return None

    zone["bar_age"] = bar_age
    now = _now_utc()

    bar_open  = _safe_float(bar.get("open"))
    bar_high  = _safe_float(bar.get("high"))
    bar_low   = _safe_float(bar.get("low"))
    bar_close = _safe_float(bar.get("close"))

    if None in (bar_open, bar_high, bar_low, bar_close):
        return None

    lower   = zone["lower"]
    upper   = zone["upper"]
    mid     = zone["midpoint"]
    status  = zone["status"]
    direction = zone["direction"]
    ifvg_dir  = zone.get("ifvg_direction")

    new_ifvg: Optional[Dict] = None

    # ── Expiry ────────────────────────────────────────────────────────────────
    if bar_age >= FVG_MAX_AGE_BARS:
        zone["status"]       = ST_EXPIRED
        zone["updated_at"]   = now
        _db_update_zone(zone)
        return None

    # ── INVERTED zone (IFVG) — only track retest ──────────────────────────────
    if status == ST_INVERTED:
        if ifvg_dir == "BULLISH":
            # Bullish IFVG: failed bearish FVG → price burst ABOVE the zone.
            # Retest = price drops back down and touches zone from above (bar.low <= upper).
            if bar_low <= upper:
                zone["status"]     = ST_RETESTED
                zone["updated_at"] = now
                logger.info("FVG IFVG RETESTED: %s %s IFVG [%.4f–%.4f]",
                            zone["instrument"], ifvg_dir, lower, upper)
                _db_update_zone(zone)
        elif ifvg_dir == "BEARISH":
            # Bearish IFVG: failed bullish FVG → price broke DOWN through the zone.
            # Retest = price rallies back up and touches zone from below (bar.high >= lower).
            if bar_high >= lower:
                zone["status"]     = ST_RETESTED
                zone["updated_at"] = now
                logger.info("FVG IFVG RETESTED: %s %s IFVG [%.4f–%.4f]",
                            zone["instrument"], ifvg_dir, lower, upper)
                _db_update_zone(zone)
        return None   # IFVGs don't spawn further inversions

    # ── Bullish FVG lifecycle ─────────────────────────────────────────────────
    if direction == "BULLISH" and ifvg_dir is None:
        # FAILED: bar closed below zone bottom
        if bar_close < lower:
            zone["status"]        = ST_FAILED
            zone["invalidated_at"] = now
            zone["updated_at"]    = now
            logger.info("FVG FAILED: %s BULLISH [%.4f–%.4f] close=%.4f",
                        zone["instrument"], lower, upper, bar_close)
            _db_update_zone(zone)
            # → Create a BEARISH IFVG from the failed bullish zone
            new_ifvg = _make_zone(
                inst=zone["instrument"],
                direction="BEARISH",         # IFVG trade direction is opposite
                lower=lower,
                upper=upper,
                bar_ts=bar.get("ts"),
                displacement_strength=zone["displacement_strength"],
                size_atr=zone["size_atr"],
                parent_fvg_id=zone["fvg_id"],
                ifvg_direction="BEARISH",    # marks this as an IFVG
            )
            new_ifvg["status"] = ST_INVERTED
            new_ifvg["inverted_at"] = now
            logger.info("FVG INVERTED: %s BEARISH IFVG created from failed BULLISH [%.4f–%.4f]",
                        zone["instrument"], lower, upper)
            return new_ifvg

        # Price interacts with zone
        entered_zone = bar_low <= upper

        if entered_zone:
            if status == ST_ACTIVE:
                zone["status"]       = ST_TOUCHED
                zone["touch_count"]  = zone.get("touch_count", 0) + 1
                zone["first_touch_at"] = zone.get("first_touch_at") or now
                zone["updated_at"]   = now

            # MITIGATED: bar low reached midpoint
            if bar_low <= mid and status in (ST_ACTIVE, ST_TOUCHED):
                zone["status"]       = ST_MITIGATED
                zone["mitigated_at"] = now
                zone["updated_at"]   = now

            # HOLDING: bar closed back ABOVE the zone after touching it.
            # Use zone["status"] (not local `status`) — the status may have been
            # promoted from ACTIVE→TOUCHED→MITIGATED earlier in this same bar.
            if zone["status"] in (ST_TOUCHED, ST_MITIGATED) and bar_close > upper:
                zone["status"]     = ST_HOLDING
                zone["updated_at"] = now
                logger.info("FVG HOLDING: %s BULLISH [%.4f–%.4f] close=%.4f",
                            zone["instrument"], lower, upper, bar_close)

        _db_update_zone(zone)

    # ── Bearish FVG lifecycle ─────────────────────────────────────────────────
    elif direction == "BEARISH" and ifvg_dir is None:
        # FAILED: bar closed above zone top
        if bar_close > upper:
            zone["status"]        = ST_FAILED
            zone["invalidated_at"] = now
            zone["updated_at"]    = now
            logger.info("FVG FAILED: %s BEARISH [%.4f–%.4f] close=%.4f",
                        zone["instrument"], lower, upper, bar_close)
            _db_update_zone(zone)
            # → Create a BULLISH IFVG from the failed bearish zone
            new_ifvg = _make_zone(
                inst=zone["instrument"],
                direction="BULLISH",
                lower=lower,
                upper=upper,
                bar_ts=bar.get("ts"),
                displacement_strength=zone["displacement_strength"],
                size_atr=zone["size_atr"],
                parent_fvg_id=zone["fvg_id"],
                ifvg_direction="BULLISH",
            )
            new_ifvg["status"] = ST_INVERTED
            new_ifvg["inverted_at"] = now
            logger.info("FVG INVERTED: %s BULLISH IFVG created from failed BEARISH [%.4f–%.4f]",
                        zone["instrument"], lower, upper)
            return new_ifvg

        # Price interacts with zone from below
        entered_zone = bar_high >= lower

        if entered_zone:
            if status == ST_ACTIVE:
                zone["status"]       = ST_TOUCHED
                zone["touch_count"]  = zone.get("touch_count", 0) + 1
                zone["first_touch_at"] = zone.get("first_touch_at") or now
                zone["updated_at"]   = now

            # MITIGATED: bar high reached midpoint
            if bar_high >= mid and status in (ST_ACTIVE, ST_TOUCHED):
                zone["status"]       = ST_MITIGATED
                zone["mitigated_at"] = now
                zone["updated_at"]   = now

            # HOLDING: bar closed back BELOW the zone after touching.
            # Use zone["status"] (not local `status`) — the status may have been
            # promoted from ACTIVE→TOUCHED→MITIGATED earlier in this same bar.
            if zone["status"] in (ST_TOUCHED, ST_MITIGATED) and bar_close < lower:
                zone["status"]     = ST_HOLDING
                zone["updated_at"] = now
                logger.info("FVG HOLDING: %s BEARISH [%.4f–%.4f] close=%.4f",
                            zone["instrument"], lower, upper, bar_close)

        _db_update_zone(zone)

    return new_ifvg


# ── Ranking ───────────────────────────────────────────────────────────────────

def _rank_zone(
    zone: Dict,
    current_price: float,
    atr: float,
    vwap: Optional[float],
) -> Dict:
    """
    Compute a transparent rank score for a zone. All component values exposed.
    Higher score = higher priority candidate.

    Components (max 100):
      size_atr_score   (0-20): larger gap relative to ATR = more significant
      freshness_score  (0-20): newer zones ranked higher
      first_touch      (0-15): first touch more significant than repeat
      holding_bonus    (0-15): zone showing HOLDING confirmed
      vwap_score       (0-15): zone on same side as VWAP bias
      proximity_score  (0-15): zone closer to current price (not too close)
    """
    if atr <= 0:
        atr = 1.0

    components: Dict[str, float] = {}

    # Size relative to ATR (capped at 2x ATR = full 20 pts)
    size_atr = zone.get("size_atr", 0)
    components["size_atr_score"] = round(min(size_atr / 2.0, 1.0) * 20, 2)

    # Freshness (decays over age — 0 bars = 20 pts, FVG_MAX_AGE_BARS = 0 pts)
    age = zone.get("bar_age", 0)
    age_frac = max(0.0, 1.0 - age / max(FVG_MAX_AGE_BARS, 1))
    components["freshness_score"] = round(age_frac * 20, 2)

    # First touch bonus
    tc = zone.get("touch_count", 0)
    if tc == 0:
        components["first_touch_score"] = 15.0
    elif tc == 1:
        components["first_touch_score"] = 8.0
    else:
        components["first_touch_score"] = 2.0

    # Holding confirmation bonus
    components["holding_bonus"] = 15.0 if zone.get("status") == ST_HOLDING else 0.0

    # VWAP alignment
    direction = zone.get("direction")
    ifvg_dir  = zone.get("ifvg_direction")
    trade_dir = ifvg_dir or direction   # IFVGs trade in ifvg_direction
    if vwap and current_price:
        price_above_vwap = current_price > vwap
        aligned = (trade_dir == "BULLISH" and price_above_vwap) or \
                  (trade_dir == "BEARISH" and not price_above_vwap)
        components["vwap_score"] = 15.0 if aligned else 0.0
    else:
        components["vwap_score"] = 7.5  # neutral when VWAP unavailable

    # Proximity to current price (sweet spot: 0.5-3x ATR away)
    mid = zone.get("midpoint", current_price)
    dist = abs(current_price - mid)
    dist_atr = dist / atr
    if 0.3 <= dist_atr <= 3.0:
        prox = 1.0 - abs(dist_atr - 1.5) / 1.5   # peaks at 1.5x ATR away
        components["proximity_score"] = round(max(0.0, prox) * 15, 2)
    else:
        components["proximity_score"] = 0.0

    total = round(sum(components.values()), 2)
    zone["rank_score"]      = total
    zone["rank_components"] = components
    return zone


# ── DB persistence ────────────────────────────────────────────────────────────

def _learning_conn():
    """Mirror of app.py's _learning_conn — lazy import to avoid circular deps."""
    try:
        import os as _os
        import psycopg2 as _pg2                # noqa: PLC0415
        url = _os.environ.get("DATABASE_URL", "")
        if not url:
            return None
        return _pg2.connect(url, connect_timeout=5)
    except Exception:
        return None


def check_fvg_db_ready() -> bool:
    """Probe fvg_zones table. Sets _DB_READY. Called from app.py boot."""
    global _DB_READY
    conn = _learning_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM fvg_zones LIMIT 1")
            cur.fetchone()
        _DB_READY = True
        logger.info("fvg_zones table ready")
        return True
    except Exception as exc:
        logger.warning("fvg_zones table unavailable (FVG persistence disabled): %s", exc)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _db_insert_zone(zone: Dict) -> None:
    """Insert a new FVG zone row. Fail-open. Runs in a daemon thread."""
    if not _DB_READY:
        return

    def _run():
        conn = _learning_conn()
        if conn is None:
            return
        try:
            import json as _json   # noqa: PLC0415
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fvg_zones
                        (fvg_id, instrument, direction, created_at, bar_index,
                         lower_bound, upper_bound, midpoint, size_points, size_atr,
                         source_timeframe, displacement_strength, status,
                         touch_count, parent_fvg_id, ifvg_direction,
                         rank_score, rank_components, updated_at)
                    VALUES
                        (%s, %s, %s, now(), %s,
                         %s, %s, %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, now())
                    ON CONFLICT (fvg_id) DO NOTHING
                    """,
                    (
                        zone["fvg_id"], zone["instrument"], zone["direction"],
                        None,
                        zone["lower"], zone["upper"], zone["midpoint"],
                        zone["size_points"], zone["size_atr"],
                        zone["source_timeframe"], zone["displacement_strength"],
                        zone["status"],
                        zone["touch_count"],
                        zone.get("parent_fvg_id"),
                        zone.get("ifvg_direction"),
                        zone.get("rank_score", 0),
                        _json.dumps(zone.get("rank_components", {})),
                    ),
                )
                conn.commit()
            zone["_persisted"] = True
        except Exception as exc:
            logger.debug("FVG DB insert error: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


def _db_update_zone(zone: Dict) -> None:
    """Update status/lifecycle fields for an existing FVG zone. Fail-open.
    Captures a snapshot of the relevant fields immediately (before the lock is
    released) so the daemon thread sees a consistent view even if the zone dict
    is mutated by a later bar-close while the thread is still waiting to run."""
    if not _DB_READY or not zone.get("_persisted"):
        return
    # Snapshot now — called while _LOCK is held
    snap = {
        "fvg_id":        zone["fvg_id"],
        "status":        zone["status"],
        "touch_count":   zone.get("touch_count", 0),
        "first_touch_at": zone.get("first_touch_at"),
        "mitigated_at":  zone.get("mitigated_at"),
        "invalidated_at": zone.get("invalidated_at"),
        "inverted_at":   zone.get("inverted_at"),
        "rank_score":    zone.get("rank_score", 0),
    }

    def _run():
        conn = _learning_conn()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fvg_zones SET
                        status           = %s,
                        touch_count      = %s,
                        first_touch_at   = %s,
                        mitigated_at     = %s,
                        invalidated_at   = %s,
                        inverted_at      = %s,
                        rank_score       = %s,
                        updated_at       = now()
                    WHERE fvg_id = %s
                    """,
                    (
                        snap["status"],
                        snap["touch_count"],
                        snap["first_touch_at"],
                        snap["mitigated_at"],
                        snap["invalidated_at"],
                        snap["inverted_at"],
                        snap["rank_score"],
                        snap["fvg_id"],
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.debug("FVG DB update error: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


# ── Core processing ───────────────────────────────────────────────────────────

def process_bar_close(
    inst: str,
    bars: List[Dict],
    vwap: Optional[float] = None,
) -> None:
    """
    Main entry point — called on every Databento 1m bar close.

    1. Compute ATR from recent bars.
    2. Detect new FVGs in the latest 3 candles.
    3. Update lifecycle of all existing zones for this instrument.
    4. Prune terminal/expired zones beyond cap.
    5. Rank remaining active zones.
    """
    if not FVG_ENGINE_ENABLED:
        return
    if not bars or len(bars) < 3:
        return

    try:
        atr          = _compute_atr(bars)
        current_bar  = bars[-1]
        current_price = _safe_float(current_bar.get("close")) or 0.0

        with _LOCK:
            zones = FVG_ZONES_BY_INST.setdefault(inst, [])

            # Track bar count per instrument for age calculation
            _BAR_COUNTS[inst] = _BAR_COUNTS.get(inst, 0) + 1

            # 1. Build set of known bar_ts anchors for deduplication
            known_ts = {z["bar_ts"] for z in zones}

            # 2. Detect new FVG zones
            new_zones = _detect_new_fvgs(inst, bars, atr, known_ts)
            new_zone_ids: set = set()
            for z in new_zones:
                zones.append(z)
                _db_insert_zone(z)
                new_zone_ids.add(z["fvg_id"])

            # 3. Update lifecycle of all non-terminal zones.
            #    Skip zones born on THIS bar — a zone must not be touched by the
            #    same candle that created it (the creation bar IS the gap itself).
            new_ifvgs: List[Dict] = []
            for zone in zones:
                if zone["status"] in TERMINAL_STATUSES:
                    continue
                if zone["fvg_id"] in new_zone_ids:
                    continue  # just born — lifecycle begins from next bar
                zone["bar_age"] = zone.get("bar_age", 0) + 1
                ifvg = _update_zone_lifecycle(zone, current_bar, atr, zone["bar_age"])
                if ifvg is not None:
                    new_ifvgs.append(ifvg)

            # 4. Add any spawned IFVGs
            for ifvg in new_ifvgs:
                zones.append(ifvg)
                _db_insert_zone(ifvg)

            # 5. Rank non-terminal zones
            for zone in zones:
                if zone["status"] not in TERMINAL_STATUSES:
                    _rank_zone(zone, current_price, atr, vwap)

            # 6. Prune: keep at most FVG_MAX_ZONES_PER_INST total, preferring active
            if len(zones) > FVG_MAX_ZONES_PER_INST:
                # Sort: active first (by rank_score), then terminal by age
                active   = [z for z in zones if z["status"] not in TERMINAL_STATUSES]
                terminal = [z for z in zones if z["status"] in TERMINAL_STATUSES]
                active.sort(key=lambda z: z.get("rank_score", 0), reverse=True)
                terminal.sort(key=lambda z: z.get("bar_age", 0), reverse=True)
                keep = (active + terminal)[:FVG_MAX_ZONES_PER_INST]
                FVG_ZONES_BY_INST[inst] = keep

    except Exception as exc:
        logger.debug("FVG process_bar_close error (%s): %s", inst, exc)


# ── Public query API ──────────────────────────────────────────────────────────

def get_zones(inst: str, include_terminal: bool = False) -> List[Dict]:
    """Return a snapshot of zones for one instrument. Never raises."""
    if not FVG_ENGINE_ENABLED:
        return []
    try:
        with _LOCK:
            zones = list(FVG_ZONES_BY_INST.get(inst, []))
        if not include_terminal:
            zones = [z for z in zones if z["status"] not in TERMINAL_STATUSES]
        # Return copies — callers must not mutate
        return [{k: v for k, v in z.items() if not k.startswith("_")} for z in zones]
    except Exception:
        return []


def get_chart_zones(inst: str) -> List[Dict]:
    """
    Return minimal zone dicts suitable for chart overlay rendering.
    Includes both active and recently terminal zones (for visual history).
    """
    if not FVG_ENGINE_ENABLED:
        return []
    try:
        with _LOCK:
            zones = list(FVG_ZONES_BY_INST.get(inst, []))
        result = []
        for z in zones:
            result.append({
                "fvg_id":       z["fvg_id"],
                "direction":    z["direction"],
                "ifvg_direction": z.get("ifvg_direction"),
                "lower":        z["lower"],
                "upper":        z["upper"],
                "midpoint":     z["midpoint"],
                "status":       z["status"],
                "touch_count":  z.get("touch_count", 0),
                "bar_age":      z.get("bar_age", 0),
                "created_at":   z.get("created_at"),
                "rank_score":   z.get("rank_score", 0),
            })
        return result
    except Exception:
        return []


def get_best_zone(inst: str, direction: str) -> Optional[Dict]:
    """
    Return the highest-ranked active zone for the given inst+direction.
    direction: 'BULLISH' or 'BEARISH'.
    For IFVGs the ifvg_direction field determines trade direction.
    """
    if not FVG_ENGINE_ENABLED:
        return None
    try:
        with _LOCK:
            zones = list(FVG_ZONES_BY_INST.get(inst, []))
        candidates = [
            z for z in zones
            if z["status"] not in TERMINAL_STATUSES
            and (z.get("ifvg_direction") or z["direction"]) == direction
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda z: z.get("rank_score", 0))
        return {k: v for k, v in best.items() if not k.startswith("_")}
    except Exception:
        return None


def get_summary() -> Dict[str, Any]:
    """
    Return per-instrument FVG summary for the Main Brain scanner panel.
    Shape: { inst: { active_count, ifvg_count, best_bullish, best_bearish, all_active } }
    """
    if not FVG_ENGINE_ENABLED:
        return {"enabled": False}

    try:
        result: Dict[str, Any] = {"enabled": True}
        with _LOCK:
            snapshot = {inst: list(zones) for inst, zones in FVG_ZONES_BY_INST.items()}

        for inst, zones in snapshot.items():
            active   = [z for z in zones if z["status"] not in TERMINAL_STATUSES]
            ifvg     = [z for z in active if z.get("ifvg_direction")]
            plain    = [z for z in active if not z.get("ifvg_direction")]

            def _best(lst: list, d: str):
                cands = [z for z in lst if (z.get("ifvg_direction") or z["direction"]) == d]
                if not cands:
                    return None
                b = max(cands, key=lambda z: z.get("rank_score", 0))
                return {k: v for k, v in b.items() if not k.startswith("_")}

            result[inst] = {
                "active_fvg_count":  len(plain),
                "active_ifvg_count": len(ifvg),
                "best_bullish":      _best(active, "BULLISH"),
                "best_bearish":      _best(active, "BEARISH"),
                "all_active": [
                    {k: v for k, v in z.items() if not k.startswith("_")}
                    for z in sorted(active, key=lambda z: z.get("rank_score", 0), reverse=True)
                ],
            }

        return result
    except Exception as exc:
        logger.debug("FVG get_summary error: %s", exc)
        return {"enabled": True, "error": str(exc)}


def reset_instrument(inst: str) -> None:
    """Clear all in-memory zones for one instrument. Test/admin use only."""
    with _LOCK:
        FVG_ZONES_BY_INST.pop(inst, None)
        _BAR_COUNTS.pop(inst, None)


def reset_all() -> None:
    """Clear all in-memory state. Test/admin use only."""
    with _LOCK:
        FVG_ZONES_BY_INST.clear()
        _BAR_COUNTS.clear()
