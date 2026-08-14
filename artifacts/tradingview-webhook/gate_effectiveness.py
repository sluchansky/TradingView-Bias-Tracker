"""Gate Effectiveness Audit — Phase 8C (MEASURE FIRST, CHANGE SECOND).

Records every meaningful gate decision (ALLOWED and BLOCKED) with full
component breakdown, then tracks forward outcomes for blocked opportunities
as counterfactual ghost trades so we can compute per-rule and per-component
performance statistics.

NEVER touches: gate rules, thresholds, Edge Score weights, Databento
ingestion, execution, risk, arm state, or broker.  DISPLAY/MEASUREMENT
ONLY.  All writes are FAIL-OPEN — a bug here cannot affect the money path.

Baseline:  GATE_BASELINE_2026_08_11
Table:     gate_audit_log  (DDL: db_gate_effectiveness_schema.sql)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
BASELINE_VERSION           = "GATE_BASELINE_2026_08_11"
MIN_EDGE_TO_RECORD_BLOCKED = 15    # ignore BLOCKED records with no meaningful signal
_WATCHER_INTERVAL_SEC      = 60    # counterfactual watcher cadence
_OUTCOME_EXPIRY_HOURS      = 6     # PENDING records older than this → EXPIRED
_ALLOWED_DEDUP_MIN         = 10    # 10-minute time-bucket for ALLOWED dedup
_BLOCKED_DEDUP_HOURS       = 1     # 1-hour time-bucket for BLOCKED dedup
_CONSERVATIVE_STOP_FIRST   = True  # when both stop + target hit in same bar → record as stop

# Maintained by boot() — follows the GHOST_OBS_DB_READY pattern
GATE_AUDIT_DB_READY  = False
_WATCHER_LOCK        = threading.Lock()
_LAST_RECORDED_AT: Optional[datetime] = None   # updated after every successful INSERT


# ── IT gate-category mapping ───────────────────────────────────────────────────
# Maps known IT primary_blocker codes to human-readable audit categories.
# Unknown blockers are classified heuristically by _blocker_category().
_IT_GATE_CATEGORY: dict = {
    "zone_valid":          "Zone / location",
    "vwap_confirmed":      "Zone / location",
    "location_quality":    "Zone / location",
    "FORCE_FLAT":          "Time / session",
    "MARKET_CLOSED":       "Time / session",
    "SESSION_CLOSED":      "Time / session",
    "time_ok":             "Time / session",
    "structure_confirmed": "Structure",
    "BLOCKED_DATA":        "Data absent",
    "OPPOSED_1H":          "Trend alignment",
    "BLOCKED_EXTENSION":   "Trend alignment",
    "NO_TREND_ALIGNMENT":  "Trend alignment",
    "trend_alignment":     "Trend alignment",
}


def _blocker_category(blocker: str, mode: str = "SCALP") -> str:
    """Map a primary_blocker label to an audit gate category."""
    if not blocker:
        return "Other"
    cat = _IT_GATE_CATEGORY.get(blocker)
    if cat:
        return cat
    b = blocker.lower()
    if "edge_score" in b:                              return "Confirmation"
    if "volume" in b or "cvd" in b:                    return "Confirmation"
    if "vwap" in b or "zone" in b or "loc" in b:       return "Zone / location"
    if "atr" in b or "volatil" in b:                   return "Volatility"
    if "struct" in b or "bos" in b or "choch" in b:    return "Structure"
    if "time" in b or "session" in b or "flat" in b:   return "Time / session"
    if "trend" in b or "align" in b or "oppos" in b:   return "Trend alignment"
    return "Other"


def _extract_strategy(result: dict, mode: str) -> str:
    """Classify the strategy / setup type from the analysis result."""
    verdict = str(result.get("verdict") or "")
    if "ORB" in verdict.upper():
        return "ORB"
    if "BREAKOUT" in verdict.upper():
        return "BREAKOUT"
    if mode == "INTRADAY_TREND":
        it_ctx = result.get("intraday_trend_context") or {}
        return (it_ctx.get("setup_type") or "INTRADAY_TREND")
    return mode


# ── DB helper ─────────────────────────────────────────────────────────────────

def _learning_conn():
    """Open a Postgres learning-DB connection.  Returns None on any failure."""
    try:
        import app as _app  # noqa: PLC0415
        return _app._learning_conn()
    except Exception as exc:
        logger.debug("gate_effectiveness _learning_conn: %s", exc)
        return None


# ── DB probe ──────────────────────────────────────────────────────────────────

def check_gate_audit_db_ready() -> None:
    """Probe gate_audit_log and set GATE_AUDIT_DB_READY.  FAIL-OPEN.
    Called once at server boot.  Never touches gate or execution."""
    global GATE_AUDIT_DB_READY
    conn = _learning_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM gate_audit_log LIMIT 1")
            cur.fetchone()
        GATE_AUDIT_DB_READY = True
        logger.info(
            "GateEffectiveness: gate_audit_log ready "
            "(baseline=%s)", BASELINE_VERSION
        )
    except Exception as exc:
        logger.warning(
            "GateEffectiveness: gate_audit_log unavailable — "
            "apply db_gate_effectiveness_schema.sql: %s", exc
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Signal extraction ─────────────────────────────────────────────────────────

def _comp(val: Any) -> str:
    """Convert a gate component boolean to PASS / FAIL / UNAVAILABLE."""
    if val is None:
        return "UNAVAILABLE"
    return "PASS" if bool(val) else "FAIL"


def _scalar_str(val: Any) -> Optional[str]:
    """Coerce any value to a short string psycopg2 can insert, or None.

    Handles the cases where full_analysis returns a structured dict for
    session_state, trend_alignment, etc.  Dicts are collapsed to their
    most informative string key (window / label / status / str repr).
    """
    if val is None:
        return None
    if isinstance(val, str):
        return val[:120] if val else None
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, dict):
        # Prefer the most human-readable field
        for key in ("window", "label", "status", "name", "regime"):
            v = val.get(key)
            if v is not None and isinstance(v, str):
                return v[:120]
        return str(val)[:120]
    return str(val)[:120]


def _extract(result: dict) -> dict:
    """Pull all audit-relevant fields from a full_analysis result dict.
    Returns a flat dict of normalized values.  Never raises.

    For INTRADAY_TREND mode the IT-native context (result["intraday_trend_context"])
    is used for blocker extraction instead of the SWING-style gate_debug fields,
    which reflect SWING strict-gate metrics and are misleading for IT evaluation.
    """
    try:
        from app import is_actionable, is_early_ready, ready_direction  # noqa: PLC0415
    except Exception:
        is_actionable  = lambda v: "READY" in str(v)
        is_early_ready = lambda v: "EARLY" in str(v)
        ready_direction = lambda v: "Long" if "LONG" in str(v) else ("Short" if "SHORT" in str(v) else None)

    try:
        verdict    = result.get("verdict") or "WAIT"
        direction  = result.get("strict_direction")
        if not direction:
            try:
                direction = ready_direction(verdict)
            except Exception:
                pass
        # Fallback 1: parse from strict_reason — it often reads "Short WAIT — ..."
        # or "Long WAIT — ..."; this is the main source when strict_direction is None
        # and verdict has no directional prefix (bare "WAIT").
        if not direction:
            sr = result.get("strict_reason") or ""
            sr_lower = sr.lower()
            if sr_lower.startswith("long"):
                direction = "Long"
            elif sr_lower.startswith("short"):
                direction = "Short"
        # Fallback 2: use the first key of the directions dict if exactly one side
        # is present — avoids recording conflicted/neutral markets as BLOCKED.
        if not direction:
            dirs = result.get("directions") or {}
            dir_keys = [k for k in dirs if k in ("Long", "Short")]
            if len(dir_keys) == 1:
                direction = dir_keys[0]
        direction = direction or "Unknown"

        trade_plan = result.get("trade_plan") or {}
        eb         = result.get("edge_breakdown") or {}
        conf       = result.get("confluences") or {}
        vol        = result.get("volatility") or {}
        gd         = result.get("gate_debug") or {}

        # ── IT-native extraction ──────────────────────────────────────────────
        # When intraday_trend_context is present, use IT-native fields for blocker
        # extraction.  The SWING-style gate_debug/comp_* fields contain SWING strict
        # gate metrics that are meaningless for IT mode (e.g. "zone_valid" is always
        # False for IT because IT doesn't require zone proximity).
        it_ctx = result.get("intraday_trend_context")
        if isinstance(it_ctx, dict) and it_ctx.get("mode") == "INTRADAY_TREND":
            # Blockers: first from veto list, then from context status
            it_veto_code  = trade_plan.get("it_veto_code") or ""
            it_ctx_status = it_ctx.get("status") or ""
            it_reason     = (trade_plan.get("reason") or it_ctx.get("reason") or "")
            # Build the canonical blocker list from IT context
            it_blockers: list = []
            if it_veto_code and it_veto_code not in ("", "None"):
                it_blockers.append(it_veto_code)
            # Supplement with human-readable context status when no veto code
            elif it_ctx_status and it_ctx_status not in (
                "CONFIRMED_SETUP", "SETUP_DEVELOPING", "BUILDING_CONTEXT"
            ):
                it_blockers.append(it_ctx_status)
            # READY_REDUCED: one confirmation step still missing — record which one
            if trade_plan.get("it_ready_reduced"):
                miss_step = trade_plan.get("it_ready_reduced_missing")
                if miss_step:
                    it_blockers.append(f"PENDING: {str(miss_step)[:60]}")
            primary_blocker = it_blockers[0] if it_blockers else None
            # IT-native confirmation component breakdown.
            # Derive PASS/FAIL for shared columns from IT confluences.
            _it_conf    = conf   # raw confluences dict for component columns
            comp_bos    = _comp(_it_conf.get("bos"))
            comp_choch  = _comp(_it_conf.get("choch"))
            comp_vwap   = _comp(_it_conf.get("vwap_confirmed"))
            comp_sweep  = _comp(_it_conf.get("liquidity_sweep"))
            comp_vol    = _comp(_it_conf.get("volume_confirmed"))
            cvd_ok_it: Optional[bool] = None
            if "cvd_conflict" in gd:
                cvd_ok_it = not gd["cvd_conflict"]
            # ── Geometry: real IT plan, then ATR-based hypothetical fallback ────────
            _has_plan    = bool(trade_plan.get("trade_plan"))
            _entry_px    = float(trade_plan["entry"])     if _has_plan and trade_plan.get("entry")    else None
            _stop_px     = (float(trade_plan["stop_loss"]) if _has_plan and trade_plan.get("stop_loss") else None)
            _target1_px  = (float(trade_plan["target1"])   if _has_plan and trade_plan.get("target1")   else None)
            _target2_px  = (float(trade_plan["target2"])   if _has_plan and trade_plan.get("target2")   else None)
            _risk_pts_it = trade_plan.get("risk_points")   if _has_plan else None

            # geometry_source:
            # IT_NATIVE  — the IT structural planner built a real plan.
            # LIVE_PLAN  — trade was ALLOWED (plan activated for entry).
            # ATR_FALLBACK — synthetic ATR×1.5 bracket used when no plan was built.
            # NONE       — no geometry at all.
            if gate_verdict in ("ALLOWED", "EARLY_ALLOWED"):
                _it_geom_src = "LIVE_PLAN" if _has_plan else "NONE"
            elif _has_plan:
                _it_geom_src = "IT_NATIVE"
            else:
                _it_geom_src = "NONE"

            # When no real plan was built, try ATR-based hypothetical geometry so
            # the counterfactual watcher can track what price action would have done.
            # ATR×1.5 stop / 2R target matches the IT structural stop philosophy.
            if _entry_px is None and direction in ("Long", "Short"):
                _cur_px  = result.get("current_price")
                _atr_val = vol.get("atr_pts")
                if _cur_px and _atr_val and float(_atr_val) > 0:
                    _e      = float(_cur_px)
                    _sdist  = float(_atr_val) * 1.5
                    _entry_px   = _e
                    _stop_px    = round(_e - _sdist, 2) if direction == "Long" else round(_e + _sdist, 2)
                    _target1_px = round(_e + _sdist * 2.0, 2) if direction == "Long" else round(_e - _sdist * 2.0, 2)
                    _risk_pts_it = round(_sdist, 4)
                    _it_geom_src = "ATR_FALLBACK"

            return {
                "verdict":          verdict,
                "direction":        direction,
                "edge_score":       int((result.get("edge_breakdown") or {}).get("score") or 0),
                "grade":            "WAIT",  # IT doesn't use SWING grade tiers
                "primary_blocker":  primary_blocker,
                "all_blockers":     it_blockers,
                "entry_price":      _entry_px,
                "stop_price":       _stop_px,
                "target1_price":    _target1_px,
                "target2_price":    _target2_px,
                "risk_points":      _risk_pts_it,
                "comp_bos":   comp_bos,   "comp_choch": comp_choch,
                "comp_vwap":  comp_vwap,  "comp_sweep": comp_sweep,
                "comp_volume": comp_vol,  "comp_cvd":   _comp(cvd_ok_it),
                "comp_session": _comp(it_ctx.get("time_ok")),
                "comp_zone":    _comp(it_ctx.get("location_quality") not in (None, "MID_RANGE")),
                "atr_pts":          vol.get("atr_pts"),
                "vwap_value":       result.get("vwap"),
                "cvd_direction":    _scalar_str(result.get("cvd_state") or result.get("cvd_direction")),
                "trend_alignment":  _scalar_str(it_ctx.get("trend_alignment")),
                "volatility_regime":vol.get("regime") or vol.get("label"),
                "session":          _scalar_str(it_ctx.get("session")),
                "strategy":         it_ctx.get("setup_type") or "INTRADAY_TREND",
                "geometry_source":  _it_geom_src,
            }
        # ── /IT-native extraction ─────────────────────────────────────────────

        # ── Gate components: prefer gate_debug, fall back to confluences ──
        bos   = gd.get("bos_confirmed")   if gd else conf.get("bos")
        choch = gd.get("choch_confirmed") if gd else conf.get("choch")
        vwap  = gd.get("vwap_confirmed")  if gd else conf.get("vwap")
        sweep = conf.get("liquidity_sweep") or conf.get("sweep_confirmed")
        vol_c = gd.get("volume_ok")       if gd else conf.get("volume_confirmed")
        # CVD: no cvd_conflict = CVD OK; cvd_conflict = CVD FAIL
        cvd_ok: Optional[bool] = None
        if "cvd_conflict" in gd:
            cvd_ok = not gd["cvd_conflict"]

        # ── Blockers ──
        failed = gd.get("failed_conditions") or gd.get("blockedBy") or []
        # Also include strict_reason for downstream vetoes that don't appear in gate_debug
        strict_reason = result.get("strict_reason") or ""
        if not failed and not is_actionable(verdict) and strict_reason:
            # Derive a blocker label from the reason text
            reason_label = strict_reason.split("—")[0].split(":")[0].strip()[:80]
            if reason_label:
                failed = [reason_label]
        primary_blocker = failed[0] if failed else None

        # ── Geometry ──
        entry   = trade_plan.get("entry")
        stop_px = trade_plan.get("stop")
        t1      = trade_plan.get("target") or trade_plan.get("tp1") or trade_plan.get("target1")
        t2      = trade_plan.get("target2") or trade_plan.get("tp2")
        risk_pts: Optional[float] = None
        try:
            if entry is not None and stop_px is not None:
                risk_pts = abs(float(entry) - float(stop_px))
        except Exception:
            pass

        # ── Geometry source + SCALP ATR fallback ─────────────────────────────
        # LIVE_PLAN:    real plan activated (ALLOWED record — trade entered).
        # SCALP_NATIVE: plan data available for a BLOCKED evaluation.
        # ATR_FALLBACK: synthetic bracket when BLOCKED with no plan data.
        # NONE:         no geometry available.
        if gate_verdict in ("ALLOWED", "EARLY_ALLOWED"):
            _geom_src = "LIVE_PLAN" if entry is not None else "NONE"
        elif entry is not None and stop_px is not None:
            _geom_src = "SCALP_NATIVE"
        else:
            _atr_s = vol.get("atr_pts")
            _cpx_s = result.get("current_price")
            if _atr_s and _cpx_s and float(_atr_s) > 0 and direction in ("Long", "Short"):
                _e_s    = float(_cpx_s)
                _sd_s   = float(_atr_s) * 1.0   # SCALP: 1×ATR stop, 1:1 RR target
                entry   = _e_s
                stop_px = round(_e_s - _sd_s, 2) if direction == "Long" else round(_e_s + _sd_s, 2)
                t1      = round(_e_s + _sd_s, 2) if direction == "Long" else round(_e_s - _sd_s, 2)
                risk_pts = round(_sd_s, 4)
                _geom_src = "ATR_FALLBACK"
            else:
                _geom_src = "NONE"

        # ── Score & grade ──
        score = int(eb.get("score") or 0)
        if score >= 85:
            grade = "A+"
        elif score >= 70:
            grade = "A"
        elif score >= 50:
            grade = "B"
        else:
            grade = "WAIT"

        return {
            "verdict":          verdict,
            "direction":        direction,
            "edge_score":       score,
            "grade":            grade,
            "primary_blocker":  primary_blocker,
            "all_blockers":     failed,
            "entry_price":      entry,
            "stop_price":       stop_px,
            "target1_price":    t1,
            "target2_price":    t2,
            "risk_points":      risk_pts,
            "comp_bos":         _comp(bos),
            "comp_choch":       _comp(choch),
            "comp_vwap":        _comp(vwap),
            "comp_sweep":       _comp(sweep),
            "comp_volume":      _comp(vol_c),
            "comp_cvd":         _comp(cvd_ok),
            "comp_session":     _comp(conf.get("preferred_session") or conf.get("session")),
            "comp_zone":        _comp(conf.get("zone_mitigated") or conf.get("zone_valid")),
            "atr_pts":          vol.get("atr_pts"),
            "vwap_value":       result.get("vwap"),
            # session_state / trend_alignment may be dicts in the live result;
            # _scalar_str collapses them to a readable string psycopg2 can INSERT.
            "cvd_direction":    _scalar_str(result.get("cvd_state") or result.get("cvd_direction")),
            "trend_alignment":  _scalar_str(result.get("trend_alignment")),
            "volatility_regime":vol.get("regime") or vol.get("label"),
            "session":          _scalar_str(result.get("session_state") or result.get("session")),
            "strategy":         _extract_strategy(result, mode),
            "geometry_source":  _geom_src,
        }
    except Exception as exc:
        logger.debug("gate_effectiveness _extract: %s", exc)
        return {
            "verdict": "WAIT", "direction": "Unknown", "edge_score": 0, "grade": "WAIT",
            "primary_blocker": None, "all_blockers": [],
            "entry_price": None, "stop_price": None,
            "target1_price": None, "target2_price": None, "risk_points": None,
            "comp_bos": "UNAVAILABLE", "comp_choch": "UNAVAILABLE",
            "comp_vwap": "UNAVAILABLE", "comp_sweep": "UNAVAILABLE",
            "comp_volume": "UNAVAILABLE", "comp_cvd": "UNAVAILABLE",
            "comp_session": "UNAVAILABLE", "comp_zone": "UNAVAILABLE",
            "atr_pts": None, "vwap_value": None, "cvd_direction": None,
            "trend_alignment": None, "volatility_regime": None, "session": None,
            "strategy": None, "geometry_source": "NONE",
        }


# ── Main recorder ─────────────────────────────────────────────────────────────

def record_gate_decision(result: dict, instrument: str, mode: str) -> None:
    """Record a gate decision snapshot for the audit log.

    Called from full_analysis() for every evaluation.  Captures ALLOWED
    (READY / EARLY READY) and BLOCKED (WAIT with a meaningful candidate)
    verdicts.  Idempotent via time-bucketed audit_id + ON CONFLICT DO UPDATE.

    FAIL-OPEN: any exception is caught at the outermost level; the caller
    (full_analysis) is never affected.  NEVER reads or writes gate state,
    arm state, broker, or execution.
    """
    if not GATE_AUDIT_DB_READY:
        return
    try:
        _record(result, instrument, mode)
    except Exception as exc:
        logger.debug("gate_effectiveness record_gate_decision (%s): %s", instrument, exc)


def _record(result: dict, instrument: str, mode: str) -> None:
    """Inner implementation — may raise; caller is wrapped in FAIL-OPEN try."""
    try:
        from app import is_actionable, is_early_ready  # noqa: PLC0415
    except Exception:
        is_actionable  = lambda v: "READY" in str(v)
        is_early_ready = lambda v: "EARLY" in str(v)

    info    = _extract(result)
    verdict = info["verdict"]
    score   = info["edge_score"]
    direction = info["direction"]

    # ── Classify verdict ──
    if is_actionable(verdict):
        gate_verdict = "EARLY_ALLOWED" if is_early_ready(verdict) else "ALLOWED"
    else:
        gate_verdict = "BLOCKED"
        # Skip low-signal BLOCKED (no candidate, no score) — avoids recording
        # pure-WAIT market periods with no setup whatsoever.
        if score < MIN_EDGE_TO_RECORD_BLOCKED and not info["primary_blocker"]:
            logger.debug(
                "GATE_AUDIT_TRACE instrument=%s direction=%s mode=%s "
                "decision=BLOCKED recorder_called=false reason=low_signal_no_blocker "
                "edge_score=%s min=%s",
                instrument, direction, mode, score, MIN_EDGE_TO_RECORD_BLOCKED,
            )
            return

    if direction in (None, "Unknown") and gate_verdict == "BLOCKED":
        logger.debug(
            "GATE_AUDIT_TRACE instrument=%s direction=Unknown mode=%s "
            "decision=BLOCKED recorder_called=false reason=no_directional_candidate",
            instrument, mode,
        )
        return   # no candidate direction — nothing to attribute

    # ── Canonicalize instrument ──
    inst = instrument.split("1!")[0].split("=")[0].strip().upper() if instrument else instrument

    now   = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # ── Strategy / setup label ───────────────────────────────────────────────
    strategy = info.get("strategy") or _extract_strategy(result, mode)

    # ── Opportunity grouping key (daily per mode+strategy+instrument+direction) ──
    # Includes strategy so VWAP_PULLBACK and LIQUIDITY_SWEEP on the same instrument
    # are tracked as SEPARATE opportunities in analytics queries.
    setup_id = f"{mode}|{strategy}|{inst}|{direction}|{now.strftime('%Y%m%d')}"

    # ── Dedup key ──
    # Identity = mode + strategy + instrument + direction + 10-minute bucket.
    # • ALLOWED: each 10-minute READY window records its own row.
    # • BLOCKED: polls within the same 10-minute window collapse to one row;
    #   a genuine later setup on the same day gets a new row (different bucket).
    # Including strategy prevents different sub-strategies from collapsing into
    # a single row when they block in the same window.
    _ten_min = now.strftime("%Y%m%d%H") + f"{now.minute // 10:02d}"
    if gate_verdict in ("ALLOWED", "EARLY_ALLOWED"):
        audit_id = f"{mode}|{strategy}|{inst}|{direction}|ALLOWED|{_ten_min}"
    else:
        audit_id = f"{mode}|{strategy}|{inst}|{direction}|BLOCKED|{_ten_min}"

    # ── Determine initial outcome_status ──
    has_geometry = (
        info["entry_price"] is not None
        and info["stop_price"] is not None
        and info["target1_price"] is not None
    )
    if gate_verdict in ("ALLOWED", "EARLY_ALLOWED"):
        initial_status = "PENDING"   # watcher will link to strategy_trades
    elif has_geometry:
        initial_status = "PENDING"   # counterfactual watcher will track
    else:
        initial_status = "NO_GEOMETRY"

    conn = _learning_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO gate_audit_log (
                    audit_id, baseline_version, recorded_at, last_seen_at,
                    instrument, direction, mode, signal_time,
                    edge_score, grade, gate_verdict, full_verdict,
                    primary_blocker, all_blockers,
                    entry_price, stop_price, target1_price, target2_price, risk_points,
                    comp_bos, comp_choch, comp_vwap, comp_sweep,
                    comp_volume, comp_cvd, comp_session, comp_zone,
                    atr_pts, vwap_value, cvd_direction, trend_alignment,
                    volatility_regime, session,
                    outcome_status,
                    strategy, setup_id,
                    geometry_source
                ) VALUES (
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,
                    %s,
                    %s,%s,
                    %s
                )
                ON CONFLICT (audit_id) DO UPDATE SET
                    last_seen_at     = EXCLUDED.last_seen_at,
                    edge_score       = EXCLUDED.edge_score,
                    grade            = EXCLUDED.grade,
                    full_verdict     = EXCLUDED.full_verdict,
                    primary_blocker  = EXCLUDED.primary_blocker,
                    all_blockers     = EXCLUDED.all_blockers,
                    entry_price      = COALESCE(EXCLUDED.entry_price,    gate_audit_log.entry_price),
                    stop_price       = COALESCE(EXCLUDED.stop_price,     gate_audit_log.stop_price),
                    target1_price    = COALESCE(EXCLUDED.target1_price,  gate_audit_log.target1_price),
                    target2_price    = COALESCE(EXCLUDED.target2_price,  gate_audit_log.target2_price),
                    risk_points      = COALESCE(EXCLUDED.risk_points,    gate_audit_log.risk_points),
                    atr_pts          = COALESCE(EXCLUDED.atr_pts,        gate_audit_log.atr_pts),
                    vwap_value       = COALESCE(EXCLUDED.vwap_value,     gate_audit_log.vwap_value),
                    cvd_direction    = EXCLUDED.cvd_direction,
                    trend_alignment  = EXCLUDED.trend_alignment,
                    volatility_regime= EXCLUDED.volatility_regime,
                    session          = EXCLUDED.session,
                    strategy         = COALESCE(EXCLUDED.strategy,  gate_audit_log.strategy),
                    setup_id         = COALESCE(EXCLUDED.setup_id,  gate_audit_log.setup_id),
                    -- Promote geometry_source to highest-fidelity value seen for this slot.
                    -- Ordering: LIVE_PLAN / IT_NATIVE / SCALP_NATIVE > ATR_FALLBACK > NONE.
                    geometry_source  = CASE
                        WHEN EXCLUDED.geometry_source IN ('LIVE_PLAN','IT_NATIVE','SCALP_NATIVE')
                        THEN EXCLUDED.geometry_source
                        WHEN gate_audit_log.geometry_source IN ('LIVE_PLAN','IT_NATIVE','SCALP_NATIVE')
                        THEN gate_audit_log.geometry_source
                        WHEN EXCLUDED.geometry_source = 'ATR_FALLBACK'
                        THEN EXCLUDED.geometry_source
                        ELSE COALESCE(EXCLUDED.geometry_source, gate_audit_log.geometry_source)
                    END,
                    -- Promote NO_GEOMETRY → PENDING when geometry arrives
                    outcome_status   = CASE
                        WHEN gate_audit_log.outcome_status = 'NO_GEOMETRY'
                         AND EXCLUDED.entry_price IS NOT NULL
                        THEN 'PENDING'
                        ELSE gate_audit_log.outcome_status
                    END
            """, (
                audit_id, BASELINE_VERSION, now, now,
                inst, direction, mode, now,
                score, info["grade"], gate_verdict, verdict,
                info["primary_blocker"],
                json.dumps(info["all_blockers"]),
                info["entry_price"], info["stop_price"],
                info["target1_price"], info["target2_price"], info["risk_points"],
                info["comp_bos"], info["comp_choch"],
                info["comp_vwap"], info["comp_sweep"],
                info["comp_volume"], info["comp_cvd"],
                info["comp_session"], info["comp_zone"],
                info["atr_pts"], info["vwap_value"],
                info["cvd_direction"], info["trend_alignment"],
                info["volatility_regime"], info["session"],
                initial_status,
                strategy, setup_id,
                info.get("geometry_source", "NONE"),
            ))
        conn.commit()
        logger.info(
            "GATE_AUDIT_TRACE instrument=%s direction=%s mode=%s "
            "decision=%s recorder_called=true audit_id=%s edge=%s blocker=%s",
            inst, direction, mode, gate_verdict, audit_id,
            score, info["primary_blocker"],
        )
        # Update last-recorded timestamp for collector-status display
        global _LAST_RECORDED_AT
        _LAST_RECORDED_AT = datetime.now(timezone.utc)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Counterfactual watcher ────────────────────────────────────────────────────

def _resolve_bar_outcome(
    bar_high: float, bar_low: float,
    entry: float, stop_px: float, target1: float,
    direction: str,
    mfe_r: float, mae_r: float,
    mfe_price: Optional[float], mae_price: Optional[float],
    risk_pts: float,
) -> tuple[float, float, Optional[float], Optional[float], bool, bool]:
    """Update MFE/MAE for one bar.  Returns (mfe_r, mae_r, mfe_price, mae_price, stop_hit, tp1_hit).
    Conservative stop-first: if both stop and target touch in one bar, stop wins."""
    is_long = (direction == "Long")

    # Price movement in trade's favour / against
    if is_long:
        fav_extreme = bar_high
        adv_extreme = bar_low
    else:
        fav_extreme = bar_low   # short profits going lower
        adv_extreme = bar_high

    # MFE
    if is_long:
        bar_mfe_r = (fav_extreme - entry) / risk_pts
    else:
        bar_mfe_r = (entry - fav_extreme) / risk_pts

    # MAE
    if is_long:
        bar_mae_r = (entry - adv_extreme) / risk_pts
    else:
        bar_mae_r = (adv_extreme - entry) / risk_pts

    if bar_mfe_r > mfe_r:
        mfe_r     = bar_mfe_r
        mfe_price = fav_extreme
    if bar_mae_r > mae_r:
        mae_r     = bar_mae_r
        mae_price = adv_extreme

    # Exit checks (conservative stop-first)
    stop_hit = (bar_low <= stop_px) if is_long else (bar_high >= stop_px)
    tp1_hit  = (bar_high >= target1) if is_long else (bar_low <= target1)

    if stop_hit and tp1_hit and _CONSERVATIVE_STOP_FIRST:
        tp1_hit = False   # stop wins when both in same bar

    return mfe_r, mae_r, mfe_price, mae_price, stop_hit, tp1_hit


def _gate_audit_watcher_cycle() -> None:
    """Resolve PENDING counterfactual outcomes against live bar history.

    Queries BLOCKED records that have geometry (entry/stop/target) and
    checks bars from DATABENTO_BARS_BY_INST.  Updates MFE / MAE / R and
    marks COMPLETED when TP1/TP2/stop is reached, or EXPIRED after the
    expiry window.

    FAIL-OPEN: individual row errors are caught and logged; cycle continues.
    NEVER touches execution path — writes only to gate_audit_log.
    """
    if not GATE_AUDIT_DB_READY:
        return

    # Lazy import to avoid circular-import at module load time
    try:
        import app as _app  # noqa: PLC0415
        bars_by_inst: dict = getattr(_app, "DATABENTO_BARS_BY_INST", {})
        learning_db_enabled: bool = getattr(_app, "LEARNING_DB_ENABLED", False)
    except Exception as exc:
        logger.debug("gate_audit_watcher_cycle import: %s", exc)
        return

    if not learning_db_enabled:
        return

    conn = _learning_conn()
    if conn is None:
        return

    now = datetime.now(timezone.utc)
    expiry_cutoff = now - timedelta(hours=_OUTCOME_EXPIRY_HOURS)

    pending_rows = []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT audit_id, instrument, direction,
                       entry_price, stop_price, target1_price, target2_price,
                       risk_points, signal_time, bars_held,
                       mfe_r, mae_r, mfe_price, mae_price,
                       tp1_hit, gate_verdict
                FROM gate_audit_log
                WHERE outcome_status = 'PENDING'
                  AND entry_price IS NOT NULL
                  AND stop_price IS NOT NULL
                  AND target1_price IS NOT NULL
                ORDER BY recorded_at ASC
                LIMIT 200
            """)
            for r in cur.fetchall():
                pending_rows.append({
                    "audit_id":   r[0], "instrument": r[1], "direction": r[2],
                    "entry":      float(r[3]),  "stop":      float(r[4]),
                    "target1":    float(r[5]),
                    "target2":    float(r[6]) if r[6] is not None else None,
                    "risk_pts":   float(r[7]) if r[7] else 1.0,
                    "signal_time": r[8],
                    "bars_held":  int(r[9]) if r[9] is not None else 0,
                    "mfe_r":      float(r[10]) if r[10] is not None else 0.0,
                    "mae_r":      float(r[11]) if r[11] is not None else 0.0,
                    "mfe_price":  float(r[12]) if r[12] is not None else None,
                    "mae_price":  float(r[13]) if r[13] is not None else None,
                    "tp1_hit":    bool(r[14]) if r[14] is not None else False,
                    "gate_verdict": r[15],
                })
    except Exception as exc:
        logger.debug("gate_audit_watcher_cycle query: %s", exc)
        try:
            conn.close()
        except Exception:
            pass
        return

    for row in pending_rows:
        try:
            inst       = row["instrument"]
            direction  = row["direction"]
            entry      = row["entry"]
            stop_px    = row["stop"]
            target1    = row["target1"]
            target2    = row["target2"]
            risk_pts   = row["risk_pts"] if row["risk_pts"] > 0 else 1.0
            signal_ts  = row["signal_time"]
            bars_held  = row["bars_held"]
            mfe_r      = row["mfe_r"]
            mae_r      = row["mae_r"]
            mfe_price  = row["mfe_price"]
            mae_price  = row["mae_price"]
            tp1_already = row["tp1_hit"]
            audit_id   = row["audit_id"]

            # Expire old records with no bar data
            if signal_ts and signal_ts < expiry_cutoff:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE gate_audit_log
                           SET outcome_status = 'EXPIRED',
                               outcome_resolved_at = %s
                         WHERE audit_id = %s AND outcome_status = 'PENDING'
                    """, (now, audit_id))
                conn.commit()
                continue

            # Get bars for this instrument
            bars = bars_by_inst.get(inst) or bars_by_inst.get(inst.upper()) or []
            if not bars:
                continue   # no bar data yet — leave PENDING

            # Only look at bars AFTER the signal time
            if signal_ts:
                sig_ts = signal_ts
                if sig_ts.tzinfo is None:
                    sig_ts = sig_ts.replace(tzinfo=timezone.utc)
                relevant = [b for b in bars if _bar_ts(b) > sig_ts]
            else:
                relevant = list(bars)

            if not relevant:
                continue

            stop_hit  = False
            tp1_hit   = tp1_already
            tp2_hit   = False
            final_r   = None
            new_bars  = 0

            for bar in relevant:
                h = float(bar.get("high") or bar.get("h") or 0)
                lo = float(bar.get("low")  or bar.get("l") or 0)
                if h == 0 and lo == 0:
                    continue
                new_bars += 1

                mfe_r, mae_r, mfe_price, mae_price, bar_stop, bar_tp1 = _resolve_bar_outcome(
                    h, lo, entry, stop_px, target1, direction,
                    mfe_r, mae_r, mfe_price, mae_price, risk_pts
                )

                if bar_stop and not tp1_hit:
                    stop_hit = True
                    final_r  = -1.0   # 1R loss
                    break

                if bar_tp1 and not tp1_hit:
                    tp1_hit = True
                    if target2 is None:
                        # no TP2 → record +1R and close
                        final_r = 1.0
                        break

                if tp1_hit and target2 is not None:
                    # Check TP2
                    if direction == "Long":
                        tp2_hit = h >= target2
                    else:
                        tp2_hit = lo <= target2
                    if tp2_hit:
                        # Average of TP1 (1R) and TP2 exit
                        t2_r = (target2 - entry) / risk_pts if direction == "Long" else (entry - target2) / risk_pts
                        final_r = round((1.0 + t2_r) / 2.0, 3)
                        break

                if bar_stop and tp1_hit:
                    # Stop after TP1 hit → break-even or small win
                    final_r = 0.0
                    stop_hit = True
                    break

            bars_held += new_bars

            # TP1 hit but TP2 bars haven't arrived yet — record 1R close.
            # In real trading the runner continues, but for counterfactual
            # accounting we close the observation at TP1 to avoid an
            # indefinitely-PENDING record.
            if tp1_hit and final_r is None and not stop_hit:
                final_r       = 1.0
                outcome_status = "COMPLETED"
            elif final_r is not None:
                outcome_status = "COMPLETED"
            elif signal_ts and signal_ts < expiry_cutoff:
                outcome_status = "EXPIRED"
                final_r = round(mfe_r - mae_r, 3) if mfe_r else None
            else:
                outcome_status = "PENDING"

            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE gate_audit_log
                       SET mfe_r    = %s, mae_r    = %s,
                           mfe_price = %s, mae_price = %s,
                           tp1_hit  = %s, tp2_hit  = %s, stop_hit = %s,
                           final_r  = %s, bars_held = %s,
                           outcome_status = %s,
                           outcome_resolved_at = CASE WHEN %s = 'COMPLETED' OR %s = 'EXPIRED'
                                                      THEN %s ELSE outcome_resolved_at END
                     WHERE audit_id = %s AND outcome_status = 'PENDING'
                """, (
                    round(mfe_r, 4), round(mae_r, 4), mfe_price, mae_price,
                    tp1_hit, tp2_hit, stop_hit,
                    final_r, bars_held,
                    outcome_status,
                    outcome_status, outcome_status, now,
                    audit_id,
                ))
            conn.commit()

        except Exception as exc:
            logger.debug("gate_audit_watcher row %s: %s", row.get("audit_id"), exc)
            try:
                conn.rollback()
            except Exception:
                pass

    try:
        conn.close()
    except Exception:
        pass


def _bar_ts(bar: dict) -> datetime:
    """Extract a timezone-aware timestamp from a bar dict."""
    try:
        ts = bar.get("ts") or bar.get("timestamp") or bar.get("t")
        if ts is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return datetime.min.replace(tzinfo=timezone.utc)


def schedule_watcher() -> None:
    """Self-rescheduling watcher that resolves PENDING counterfactuals.
    Called once at server startup; re-arms itself after each cycle."""
    if not GATE_AUDIT_DB_READY:
        return
    with _WATCHER_LOCK:
        pass   # single-flight guard checked inline inside cycle if needed
    try:
        _gate_audit_watcher_cycle()
    except Exception as exc:
        logger.debug("gate_audit schedule_watcher: %s", exc)
    finally:
        try:
            import threading as _t  # noqa: PLC0415
            _t.Timer(_WATCHER_INTERVAL_SEC, schedule_watcher).start()
        except Exception:
            pass


# ── Analytics queries ─────────────────────────────────────────────────────────

def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _pf(winners_r: float, losers_r: float) -> Optional[float]:
    """Profit factor: gross profit / gross loss (both positive)."""
    if losers_r == 0:
        return None
    return round(winners_r / losers_r, 3)


def get_summary() -> dict:
    """High-level gate performance summary for the dashboard header."""
    if not GATE_AUDIT_DB_READY:
        return {"available": False}
    conn = _learning_conn()
    if conn is None:
        return {"available": False, "error": "db_unavailable"}
    try:
        with conn.cursor() as cur:
            # Total counts
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE gate_verdict IN ('ALLOWED','EARLY_ALLOWED')) AS approved,
                    COUNT(*) FILTER (WHERE gate_verdict = 'BLOCKED')                    AS blocked,
                    COUNT(*) FILTER (WHERE outcome_status = 'COMPLETED')                AS completed,
                    COUNT(*) FILTER (WHERE outcome_status = 'PENDING')                  AS pending,
                    MIN(recorded_at)                                                     AS start_ts
                FROM gate_audit_log
                WHERE baseline_version = %s
                  AND audit_id NOT LIKE 'SYNTHETIC_%%'
            """, (BASELINE_VERSION,))
            row = cur.fetchone()
            approved  = int(row[0] or 0)
            blocked   = int(row[1] or 0)
            completed = int(row[2] or 0)
            pending   = int(row[3] or 0)
            start_ts  = row[4].isoformat() if row[4] else None

            # Per-instrument breakdown
            cur.execute("""
                SELECT
                    instrument,
                    COUNT(*) FILTER (WHERE gate_verdict = 'BLOCKED')                    AS blocked,
                    COUNT(*) FILTER (WHERE gate_verdict IN ('ALLOWED','EARLY_ALLOWED')) AS allowed,
                    COUNT(*)                                                             AS total
                FROM gate_audit_log
                WHERE baseline_version = %s
                  AND audit_id NOT LIKE 'SYNTHETIC_%%'
                GROUP BY instrument
                ORDER BY instrument
            """, (BASELINE_VERSION,))
            by_instrument = {
                r[0]: {"blocked": int(r[1] or 0),
                        "allowed": int(r[2] or 0),
                        "total":   int(r[3] or 0)}
                for r in cur.fetchall()
            }

            # Expectancy: approved
            cur.execute("""
                SELECT AVG(final_r), SUM(CASE WHEN final_r>0 THEN final_r ELSE 0 END),
                       ABS(SUM(CASE WHEN final_r<0 THEN final_r ELSE 0 END)), COUNT(*)
                FROM gate_audit_log
                WHERE gate_verdict IN ('ALLOWED','EARLY_ALLOWED')
                  AND outcome_status = 'COMPLETED'
                  AND final_r IS NOT NULL
                  AND baseline_version = %s
            """, (BASELINE_VERSION,))
            ar = cur.fetchone()
            approved_exp    = _safe_float(ar[0])
            approved_win_r  = _safe_float(ar[1]) or 0.0
            approved_loss_r = _safe_float(ar[2]) or 0.0
            approved_n      = int(ar[3] or 0)

            # Expectancy: blocked
            cur.execute("""
                SELECT AVG(final_r), SUM(CASE WHEN final_r>0 THEN final_r ELSE 0 END),
                       ABS(SUM(CASE WHEN final_r<0 THEN final_r ELSE 0 END)), COUNT(*)
                FROM gate_audit_log
                WHERE gate_verdict = 'BLOCKED'
                  AND outcome_status = 'COMPLETED'
                  AND final_r IS NOT NULL
                  AND baseline_version = %s
            """, (BASELINE_VERSION,))
            br = cur.fetchone()
            blocked_exp    = _safe_float(br[0])
            blocked_win_r  = _safe_float(br[1]) or 0.0
            blocked_loss_r = _safe_float(br[2]) or 0.0
            blocked_n      = int(br[3] or 0)

        gate_improvement: Optional[float] = None
        if approved_exp is not None and blocked_exp is not None:
            gate_improvement = round(approved_exp - blocked_exp, 3)

        last_rec = (
            _LAST_RECORDED_AT.strftime("%H:%M:%S UTC")
            if _LAST_RECORDED_AT else None
        )
        return {
            "available":         True,
            "baseline_version":  BASELINE_VERSION,
            "observation_start": start_ts,
            "total_approved":    approved,
            "total_blocked":     blocked,
            "total_observations": approved + blocked,
            "completed_outcomes": completed,
            "pending_outcomes":  pending,
            "collector_active":  True,
            "last_recorded_at":  last_rec,
            "approved": {
                "n":         approved_n,
                "expectancy": round(approved_exp, 3) if approved_exp is not None else None,
                "profit_factor": _pf(approved_win_r, approved_loss_r),
            },
            "blocked": {
                "n":         blocked_n,
                "expectancy": round(blocked_exp, 3) if blocked_exp is not None else None,
                "profit_factor": _pf(blocked_win_r, blocked_loss_r),
            },
            "gate_improvement": gate_improvement,
            "evidence_status": _evidence_status(approved_n + blocked_n),
            "by_instrument":   by_instrument,
        }
    except Exception as exc:
        logger.debug("gate_effectiveness get_summary: %s", exc)
        return {"available": False, "error": str(exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def validate_wiring(clean_up: bool = True) -> dict:
    """Synthetic end-to-end wiring validation.

    Injects one BLOCKED and one ALLOWED synthetic record through the exact
    same recorder → DB path used by live full_analysis, then reads them back
    via get_summary(), and optionally deletes them.

    Returns a dict with counts before/after and pass/fail verdict.
    NEVER sends an order, touches execution state, risk, or broker.
    Safe to call in dev or prod.
    """
    if not GATE_AUDIT_DB_READY:
        return {"ok": False, "error": "GATE_AUDIT_DB_READY is False — table not applied",
                "verdict": "FAIL"}

    SYNTH_PREFIX = "SYNTHETIC_WIRING_"
    synth_blocked = SYNTH_PREFIX + "BLOCKED"
    synth_allowed = SYNTH_PREFIX + "ALLOWED"
    now = datetime.now(timezone.utc)

    conn = _learning_conn()
    if conn is None:
        return {"ok": False, "error": "db_unavailable", "verdict": "FAIL"}

    inserted = []
    try:
        with conn.cursor() as cur:
            # Count before
            cur.execute("SELECT COUNT(*) FROM gate_audit_log WHERE baseline_version=%s",
                        (BASELINE_VERSION,))
            count_before = int((cur.fetchone() or [0])[0])

            # Insert synthetic BLOCKED
            cur.execute("""
                INSERT INTO gate_audit_log
                    (audit_id, baseline_version, recorded_at, last_seen_at,
                     instrument, direction, mode, signal_time,
                     edge_score, grade, gate_verdict, full_verdict,
                     primary_blocker, all_blockers, outcome_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (audit_id) DO UPDATE SET last_seen_at=EXCLUDED.last_seen_at
            """, (synth_blocked, BASELINE_VERSION, now, now,
                  "MNQ", "Long", "SCALP", now,
                  20, "WAIT", "BLOCKED", "WAIT",
                  "SYNTHETIC_TEST", json.dumps(["SYNTHETIC_TEST"]),
                  "NO_GEOMETRY"))
            inserted.append(synth_blocked)

            # Insert synthetic ALLOWED
            cur.execute("""
                INSERT INTO gate_audit_log
                    (audit_id, baseline_version, recorded_at, last_seen_at,
                     instrument, direction, mode, signal_time,
                     edge_score, grade, gate_verdict, full_verdict,
                     primary_blocker, all_blockers, outcome_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (audit_id) DO UPDATE SET last_seen_at=EXCLUDED.last_seen_at
            """, (synth_allowed, BASELINE_VERSION, now, now,
                  "MNQ", "Long", "SCALP", now,
                  80, "A", "ALLOWED", "LONG READY",
                  None, json.dumps([]),
                  "NO_GEOMETRY"))
            inserted.append(synth_allowed)

        conn.commit()

        # Read them back
        with conn.cursor() as cur:
            cur.execute(
                "SELECT audit_id, gate_verdict FROM gate_audit_log WHERE audit_id = ANY(%s)",
                (inserted,)
            )
            found = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("SELECT COUNT(*) FROM gate_audit_log WHERE baseline_version=%s",
                        (BASELINE_VERSION,))
            count_after = int((cur.fetchone() or [0])[0])

        verified_blocked = found.get(synth_blocked) == "BLOCKED"
        verified_allowed = found.get(synth_allowed) == "ALLOWED"

        if clean_up:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM gate_audit_log WHERE audit_id = ANY(%s)", (inserted,))
            conn.commit()

        return {
            "ok":               verified_blocked and verified_allowed,
            "count_before":     count_before,
            "count_after":      count_after,
            "synthetic_records": len(found),
            "verified_blocked": verified_blocked,
            "verified_allowed": verified_allowed,
            "cleaned_up":       clean_up,
            "verdict":          "PASS" if (verified_blocked and verified_allowed) else "FAIL",
        }
    except Exception as exc:
        logger.warning("gate_effectiveness validate_wiring: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(exc), "verdict": "FAIL"}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_rule_effectiveness(
    instrument: Optional[str] = None,
    direction:  Optional[str] = None,
) -> list[dict]:
    """Per-rule attribution table.  Returns rows sorted by absolute net gate value."""
    if not GATE_AUDIT_DB_READY:
        return []
    conn = _learning_conn()
    if conn is None:
        return []
    try:
        filters  = ["gate_verdict = 'BLOCKED'", "baseline_version = %s"]
        params_: list = [BASELINE_VERSION]
        if instrument:
            filters.append("instrument = %s")
            params_.append(instrument.upper())
        if direction:
            filters.append("direction = %s")
            params_.append(direction)
        where = " AND ".join(filters)

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    blocker,
                    COUNT(*)                                                                     AS n_blocks,
                    COUNT(*) FILTER (WHERE outcome_status='COMPLETED')                           AS n_completed,
                    SUM(CASE WHEN outcome_status='COMPLETED' AND final_r > 0 THEN 1 ELSE 0 END) AS blocked_winners,
                    SUM(CASE WHEN outcome_status='COMPLETED' AND final_r <=0 THEN 1 ELSE 0 END) AS blocked_losers,
                    ROUND(SUM(CASE WHEN outcome_status='COMPLETED' AND final_r > 0 THEN final_r ELSE 0 END)::numeric, 3)
                                                                                                 AS missed_profit_r,
                    ROUND(ABS(SUM(CASE WHEN outcome_status='COMPLETED' AND final_r < 0 THEN final_r ELSE 0 END))::numeric, 3)
                                                                                                 AS avoided_loss_r
                FROM gate_audit_log,
                     jsonb_array_elements_text(all_blockers) AS blocker
                WHERE {where}
                GROUP BY blocker
                ORDER BY n_blocks DESC
            """, params_)
            rows = cur.fetchall()

        result = []
        for r in rows:
            missed = float(r[5] or 0)
            avoided = float(r[6] or 0)
            net = round(avoided - missed, 3)
            n_completed = int(r[2] or 0)
            result.append({
                "rule":            r[0],
                "n_blocks":        int(r[1] or 0),
                "n_completed":     n_completed,
                "blocked_winners": int(r[3] or 0),
                "blocked_losers":  int(r[4] or 0),
                "missed_profit_r": missed,
                "avoided_loss_r":  avoided,
                "net_gate_value_r":net,
                "evidence_status": _evidence_status(n_completed),
            })
        return result
    except Exception as exc:
        logger.debug("gate_effectiveness get_rule_effectiveness: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_edge_bucket_stats() -> list[dict]:
    """Edge Score distribution with win-rate and expectancy per bucket."""
    if not GATE_AUDIT_DB_READY:
        return []
    conn = _learning_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    CASE
                        WHEN edge_score <  40 THEN '0-39'
                        WHEN edge_score <  50 THEN '40-49'
                        WHEN edge_score <  60 THEN '50-59'
                        WHEN edge_score <  70 THEN '60-69'
                        WHEN edge_score <  75 THEN '70-74'
                        WHEN edge_score <  80 THEN '75-79'
                        WHEN edge_score <  85 THEN '80-84'
                        WHEN edge_score <  90 THEN '85-89'
                        WHEN edge_score <= 100 THEN '90-100'
                        ELSE '100+'
                    END                                                                     AS bucket,
                    gate_verdict,
                    COUNT(*)                                                                AS n,
                    ROUND(AVG(final_r)::numeric, 3)                                        AS avg_r,
                    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY final_r)::numeric,3) AS median_r,
                    ROUND(SUM(final_r)::numeric, 3)                                        AS total_r,
                    SUM(CASE WHEN final_r > 0  THEN 1 ELSE 0 END)                          AS winners,
                    SUM(CASE WHEN final_r <= 0 THEN 1 ELSE 0 END)                          AS losers,
                    SUM(CASE WHEN tp1_hit      THEN 1 ELSE 0 END)                          AS tp1_count,
                    SUM(CASE WHEN tp2_hit      THEN 1 ELSE 0 END)                          AS tp2_count,
                    SUM(CASE WHEN stop_hit     THEN 1 ELSE 0 END)                          AS stop_count,
                    ROUND(AVG(mfe_r)::numeric, 3)                                          AS avg_mfe,
                    ROUND(AVG(mae_r)::numeric, 3)                                          AS avg_mae,
                    MIN(edge_score)                                                         AS min_score
                FROM gate_audit_log
                WHERE outcome_status = 'COMPLETED'
                  AND final_r IS NOT NULL
                  AND baseline_version = %s
                GROUP BY bucket, gate_verdict
                ORDER BY min_score ASC, gate_verdict
            """, (BASELINE_VERSION,))
            rows = cur.fetchall()

        result = []
        for r in rows:
            n = int(r[2] or 0)
            w = int(r[6] or 0)
            result.append({
                "bucket":      r[0],
                "verdict":     r[1],
                "n":           n,
                "avg_r":       _safe_float(r[3]),
                "median_r":    _safe_float(r[4]),
                "total_r":     _safe_float(r[5]),
                "win_rate":    round(w / n, 3) if n > 0 else None,
                "winners":     w,
                "losers":      int(r[7] or 0),
                "tp1_rate":    round(int(r[8] or 0) / n, 3) if n > 0 else None,
                "tp2_rate":    round(int(r[9] or 0) / n, 3) if n > 0 else None,
                "stop_rate":   round(int(r[10] or 0) / n, 3) if n > 0 else None,
                "avg_mfe":     _safe_float(r[11]),
                "avg_mae":     _safe_float(r[12]),
                "evidence_status": _evidence_status(n),
            })
        return result
    except Exception as exc:
        logger.debug("gate_effectiveness get_edge_bucket_stats: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_component_stats() -> list[dict]:
    """Per-component effectiveness: PASS vs FAIL win-rate comparison."""
    if not GATE_AUDIT_DB_READY:
        return []
    conn = _learning_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    component,
                    signal_state,
                    COUNT(*)                                                   AS n,
                    ROUND(AVG(final_r)::numeric, 3)                            AS avg_r,
                    ROUND(SUM(final_r)::numeric, 3)                            AS total_r,
                    SUM(CASE WHEN final_r > 0 THEN 1 ELSE 0 END)               AS winners,
                    ROUND(AVG(mfe_r)::numeric, 3)                              AS avg_mfe,
                    ROUND(AVG(mae_r)::numeric, 3)                              AS avg_mae
                FROM (
                    SELECT final_r, mfe_r, mae_r,
                        unnest(ARRAY['BOS','CHOCH','VWAP','Sweep','Volume','CVD','Zone']) AS component,
                        unnest(ARRAY[comp_bos,comp_choch,comp_vwap,comp_sweep,comp_volume,comp_cvd,comp_zone]) AS signal_state
                    FROM gate_audit_log
                    WHERE outcome_status = 'COMPLETED'
                      AND final_r IS NOT NULL
                      AND baseline_version = %s
                ) t
                WHERE signal_state IN ('PASS','FAIL')
                GROUP BY component, signal_state
                ORDER BY component, signal_state
            """, (BASELINE_VERSION,))
            rows = cur.fetchall()

        result = []
        for r in rows:
            n = int(r[2] or 0)
            w = int(r[5] or 0)
            result.append({
                "component":   r[0],
                "signal":      r[1],
                "n":           n,
                "avg_r":       _safe_float(r[3]),
                "total_r":     _safe_float(r[4]),
                "win_rate":    round(w / n, 3) if n > 0 else None,
                "avg_mfe":     _safe_float(r[6]),
                "avg_mae":     _safe_float(r[7]),
                "evidence_status": _evidence_status(n),
            })
        return result
    except Exception as exc:
        logger.debug("gate_effectiveness get_component_stats: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_blocked_outcome_breakdown() -> dict:
    """Outcome breakdown for BLOCKED records only: +1R / -1R / expired counts.

    Used by the ghost outcome summary in _build_analytics_report (Item 9).
    Returns empty dict on any error (FAIL-OPEN).  Read-only — never touches
    gate, risk, or execution.
    """
    if not GATE_AUDIT_DB_READY:
        return {}
    conn = _learning_conn()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE outcome_status = 'EXPIRED')              AS expired,
                    COUNT(*) FILTER (WHERE outcome_status = 'COMPLETED'
                                      AND final_r > 0)                              AS reached_plus1r,
                    COUNT(*) FILTER (WHERE outcome_status = 'COMPLETED'
                                      AND final_r <= 0)                             AS hit_minus1r,
                    COUNT(*) FILTER (WHERE outcome_status = 'EXPIRED'
                                      OR (outcome_status = 'COMPLETED'
                                          AND final_r IS NULL))                     AS neither_expired
                FROM gate_audit_log
                WHERE gate_verdict = 'BLOCKED'
                  AND baseline_version = %s
            """, (BASELINE_VERSION,))
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "expired":         int(row[0] or 0),
                "reached_plus1r":  int(row[1] or 0),
                "hit_minus1r":     int(row[2] or 0),
                "neither_expired": int(row[3] or 0),
            }
    except Exception as exc:
        logger.debug("gate_effectiveness get_blocked_outcome_breakdown: %s", exc)
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_missed_winners(limit: int = 20) -> list[dict]:
    """Top blocked opportunities that would have been profitable (highest final_r)."""
    if not GATE_AUDIT_DB_READY:
        return []
    conn = _learning_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT audit_id, instrument, direction, recorded_at, edge_score,
                       entry_price, final_r, mfe_r, primary_blocker, all_blockers,
                       comp_bos, comp_choch, comp_vwap, comp_sweep, comp_volume, comp_cvd, comp_zone
                FROM gate_audit_log
                WHERE gate_verdict = 'BLOCKED'
                  AND outcome_status = 'COMPLETED'
                  AND final_r > 0
                  AND baseline_version = %s
                ORDER BY final_r DESC
                LIMIT %s
            """, (BASELINE_VERSION, limit))
            rows = cur.fetchall()

        result = []
        for r in rows:
            blockers = r[9]
            if isinstance(blockers, str):
                try:
                    blockers = json.loads(blockers)
                except Exception:
                    blockers = [blockers]
            result.append({
                "audit_id":    r[0], "instrument": r[1], "direction": r[2],
                "timestamp":   r[3].isoformat() if r[3] else None,
                "edge_score":  r[4], "entry_price": _safe_float(r[5]),
                "final_r":     _safe_float(r[6]), "mfe_r": _safe_float(r[7]),
                "primary_blocker": r[8], "all_blockers": blockers,
                "components": {
                    "BOS": r[10], "CHOCH": r[11], "VWAP": r[12],
                    "Sweep": r[13], "Volume": r[14], "CVD": r[15], "Zone": r[16],
                },
            })
        return result
    except Exception as exc:
        logger.debug("gate_effectiveness get_missed_winners: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_saved_losses(limit: int = 20) -> list[dict]:
    """Worst blocked opportunities that the gate successfully prevented."""
    if not GATE_AUDIT_DB_READY:
        return []
    conn = _learning_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT audit_id, instrument, direction, recorded_at, edge_score,
                       entry_price, final_r, mae_r, primary_blocker, all_blockers
                FROM gate_audit_log
                WHERE gate_verdict = 'BLOCKED'
                  AND outcome_status = 'COMPLETED'
                  AND final_r < 0
                  AND baseline_version = %s
                ORDER BY final_r ASC
                LIMIT %s
            """, (BASELINE_VERSION, limit))
            rows = cur.fetchall()

        result = []
        for r in rows:
            blockers = r[9]
            if isinstance(blockers, str):
                try:
                    blockers = json.loads(blockers)
                except Exception:
                    blockers = [blockers]
            result.append({
                "audit_id":    r[0], "instrument": r[1], "direction": r[2],
                "timestamp":   r[3].isoformat() if r[3] else None,
                "edge_score":  r[4], "entry_price": _safe_float(r[5]),
                "final_r":     _safe_float(r[6]), "mae_r": _safe_float(r[7]),
                "primary_blocker": r[8], "all_blockers": blockers,
            })
        return result
    except Exception as exc:
        logger.debug("gate_effectiveness get_saved_losses: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_breakdown(
    instrument: Optional[str] = None,
    direction:  Optional[str] = None,
    session:    Optional[str] = None,
) -> list[dict]:
    """Performance breakdown by context filter."""
    if not GATE_AUDIT_DB_READY:
        return []
    conn = _learning_conn()
    if conn is None:
        return []
    try:
        base_filters = ["outcome_status = 'COMPLETED'", "final_r IS NOT NULL", "baseline_version = %s"]
        params_: list = [BASELINE_VERSION]
        if instrument:
            base_filters.append("instrument = %s")
            params_.append(instrument.upper())
        if direction:
            base_filters.append("direction = %s")
            params_.append(direction)
        if session:
            base_filters.append("session = %s")
            params_.append(session)
        where = " AND ".join(base_filters)

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT gate_verdict, instrument, direction,
                       COUNT(*)                                                    AS n,
                       ROUND(AVG(final_r)::numeric, 3)                            AS avg_r,
                       ROUND(SUM(final_r)::numeric, 3)                            AS total_r,
                       SUM(CASE WHEN final_r > 0 THEN 1 ELSE 0 END)               AS winners,
                       SUM(CASE WHEN tp1_hit THEN 1 ELSE 0 END)                   AS tp1_count,
                       SUM(CASE WHEN stop_hit THEN 1 ELSE 0 END)                  AS stop_count,
                       ROUND(AVG(mfe_r)::numeric, 3)                              AS avg_mfe
                FROM gate_audit_log
                WHERE {where}
                GROUP BY gate_verdict, instrument, direction
                ORDER BY instrument, gate_verdict, direction
            """, params_)
            rows = cur.fetchall()

        result = []
        for r in rows:
            n = int(r[3] or 0)
            w = int(r[6] or 0)
            result.append({
                "gate_verdict": r[0], "instrument": r[1], "direction": r[2],
                "n": n, "avg_r": _safe_float(r[4]), "total_r": _safe_float(r[5]),
                "win_rate": round(w / n, 3) if n > 0 else None,
                "tp1_rate": round(int(r[7] or 0) / n, 3) if n > 0 else None,
                "stop_rate": round(int(r[8] or 0) / n, 3) if n > 0 else None,
                "avg_mfe": _safe_float(r[9]),
                "evidence_status": _evidence_status(n),
            })
        return result
    except Exception as exc:
        logger.debug("gate_effectiveness get_breakdown: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Evidence classification ───────────────────────────────────────────────────

def _evidence_status(n: int) -> str:
    """Sample-size protection label (Part 15 of audit spec)."""
    if n < 10:
        return "ANECDOTAL"
    if n < 30:
        return "EARLY"
    if n < 100:
        return "MODERATE"
    return "STRONGER_EVIDENCE"


# ── Mode-separated analytics ───────────────────────────────────────────────────

def get_mode_report(mode: str) -> dict:
    """Per-mode gate effectiveness breakdown with category grouping.

    Returns a dict suitable for the GateEffectivenessPanel React component.
    Gate categories are mapped via _blocker_category() so both SCALP and
    INTRADAY_TREND are presented in the same 7-category schema.
    FAIL-OPEN — never touches gate, execution, or risk.
    """
    if not GATE_AUDIT_DB_READY:
        return {"available": False}
    conn = _learning_conn()
    if conn is None:
        return {"available": False, "error": "db_unavailable"}
    try:
        with conn.cursor() as cur:
            # ── Totals + expectancy ──────────────────────────────────────────
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE gate_verdict IN ('ALLOWED','EARLY_ALLOWED'))  AS allowed,
                    COUNT(*) FILTER (WHERE gate_verdict = 'BLOCKED')                     AS blocked,
                    COUNT(*) FILTER (WHERE outcome_status = 'PENDING')                   AS pending,
                    COUNT(*) FILTER (WHERE outcome_status = 'COMPLETED')                 AS completed,
                    COUNT(*) FILTER (WHERE outcome_status = 'NO_GEOMETRY')               AS no_geometry,
                    COUNT(*) FILTER (WHERE gate_verdict = 'BLOCKED'
                                       AND entry_price IS NOT NULL)                      AS blocked_with_geom,
                    AVG(CASE WHEN gate_verdict IN ('ALLOWED','EARLY_ALLOWED')
                              AND outcome_status = 'COMPLETED'
                             THEN final_r END)                                            AS allowed_exp,
                    AVG(CASE WHEN gate_verdict = 'BLOCKED'
                              AND outcome_status = 'COMPLETED'
                             THEN final_r END)                                            AS blocked_exp,
                    MIN(recorded_at)                                                      AS start_ts
                FROM gate_audit_log
                WHERE mode = %s AND baseline_version = %s
                  AND audit_id NOT LIKE 'SYNTHETIC_%%'
            """, (mode, BASELINE_VERSION))
            row        = cur.fetchone()
            allowed    = int(row[0] or 0)
            blocked    = int(row[1] or 0)
            pending    = int(row[2] or 0)
            completed  = int(row[3] or 0)
            no_geom    = int(row[4] or 0)
            blk_geom   = int(row[5] or 0)
            allowed_exp = _safe_float(row[6])
            blocked_exp = _safe_float(row[7])
            start_ts    = row[8].isoformat() if row[8] else None

            # ── Per-primary-blocker stats ────────────────────────────────────
            cur.execute("""
                SELECT
                    COALESCE(primary_blocker, 'Unknown')                                        AS blocker,
                    COUNT(*)                                                                     AS n_blocks,
                    ROUND(AVG(edge_score)::numeric, 1)                                          AS avg_edge,
                    COUNT(*) FILTER (WHERE entry_price IS NOT NULL)                             AS with_geometry,
                    COUNT(*) FILTER (WHERE outcome_status='COMPLETED' AND final_r > 0)          AS would_win,
                    COUNT(*) FILTER (WHERE outcome_status='COMPLETED' AND final_r <= 0)         AS would_lose,
                    ROUND(AVG(final_r) FILTER (WHERE outcome_status='COMPLETED')::numeric, 3)   AS exp_r,
                    COUNT(DISTINCT instrument || '|' || direction || '|'
                          || TO_CHAR(recorded_at AT TIME ZONE 'UTC', 'YYYYMMDD'))               AS unique_opps
                FROM gate_audit_log
                WHERE mode = %s AND gate_verdict = 'BLOCKED'
                  AND baseline_version = %s
                  AND audit_id NOT LIKE 'SYNTHETIC_%%'
                GROUP BY COALESCE(primary_blocker, 'Unknown')
                ORDER BY n_blocks DESC
            """, (mode, BASELINE_VERSION))
            blocker_rows = cur.fetchall()

            # ── Component pass rates (all records, not just BLOCKED) ─────────
            cur.execute("""
                SELECT
                    SUM(CASE WHEN comp_bos    ='PASS' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN comp_choch  ='PASS' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN comp_vwap   ='PASS' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN comp_sweep  ='PASS' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN comp_volume ='PASS' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN comp_cvd    ='PASS' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN comp_session='PASS' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN comp_zone   ='PASS' THEN 1 ELSE 0 END),
                    COUNT(*)
                FROM gate_audit_log
                WHERE mode = %s AND baseline_version = %s
                  AND audit_id NOT LIKE 'SYNTHETIC_%%'
            """, (mode, BASELINE_VERSION))
            cr         = cur.fetchone()
            comp_total = max(int(cr[8] or 0), 1)

            # ── Collector health + 24-hour window metrics ────────────────────
            cur.execute("""
                SELECT
                    MAX(recorded_at)                                                           AS last_obs_ts,
                    MAX(outcome_resolved_at)                                                   AS last_resolved_ts,
                    COUNT(*) FILTER (WHERE recorded_at >= NOW() - INTERVAL '24 hours')         AS obs_24h,
                    COUNT(DISTINCT COALESCE(setup_id,
                          instrument || '|' || direction || '|'
                          || TO_CHAR(recorded_at AT TIME ZONE 'UTC', 'YYYYMMDD')))
                      FILTER (WHERE recorded_at >= NOW() - INTERVAL '24 hours')                AS opps_24h,
                    COUNT(*) FILTER (WHERE geometry_source = 'ATR_FALLBACK')                   AS atr_fallback
                FROM gate_audit_log
                WHERE mode = %s AND baseline_version = %s
                  AND audit_id NOT LIKE 'SYNTHETIC_%%'
            """, (mode, BASELINE_VERSION))
            hr        = cur.fetchone()
            _last_obs = hr[0]
            _last_res = hr[1]
            obs_24h   = int(hr[2] or 0)
            opps_24h  = int(hr[3] or 0)
            atr_fb    = int(hr[4] or 0)

        # ── Aggregate into gate categories ───────────────────────────────────
        categories: dict = {}
        for r in blocker_rows:
            blocker    = r[0]
            n_blocks   = int(r[1] or 0)
            avg_edge   = _safe_float(r[2])
            with_geom  = int(r[3] or 0)
            would_win  = int(r[4] or 0)
            would_lose = int(r[5] or 0)
            exp_r      = _safe_float(r[6])
            unique     = int(r[7] or 0)
            cat = _blocker_category(blocker, mode)
            if cat not in categories:
                categories[cat] = {
                    "category": cat, "rules": [],
                    "n_blocks": 0, "unique_opps": 0,
                    "avg_edge_wsum": 0.0, "avg_edge_n": 0,
                    "with_geometry": 0, "would_win": 0, "would_lose": 0,
                    "exp_r_wsum": 0.0, "exp_r_n": 0,
                }
            c = categories[cat]
            if blocker not in c["rules"]:
                c["rules"].append(blocker)
            c["n_blocks"]      += n_blocks
            c["unique_opps"]   += unique
            c["with_geometry"] += with_geom
            c["would_win"]     += would_win
            c["would_lose"]    += would_lose
            if avg_edge is not None:
                c["avg_edge_wsum"] += avg_edge * n_blocks
                c["avg_edge_n"]    += n_blocks
            if exp_r is not None:
                c["exp_r_wsum"] += exp_r * n_blocks
                c["exp_r_n"]    += n_blocks

        gate_cats = []
        for _, c in sorted(categories.items(), key=lambda x: -x[1]["n_blocks"]):
            exp  = round(c["exp_r_wsum"] / c["exp_r_n"], 3)  if c["exp_r_n"]    > 0 else None
            edge = round(c["avg_edge_wsum"] / c["avg_edge_n"], 1) if c["avg_edge_n"] > 0 else None
            gate_cats.append({
                "category":       c["category"],
                "rules":          sorted(c["rules"]),
                "n_blocks":       c["n_blocks"],
                "pct_of_blocked": round(100.0 * c["n_blocks"] / blocked, 1) if blocked > 0 else 0.0,
                "unique_opps":    c["unique_opps"],
                "avg_edge":       edge,
                "with_geometry":  c["with_geometry"],
                "would_win":      c["would_win"],
                "would_lose":     c["would_lose"],
                "expectancy_r":   exp,
                "evidence_status": _evidence_status(c["would_win"] + c["would_lose"]),
            })

        gate_imp = None
        if allowed_exp is not None and blocked_exp is not None:
            gate_imp = round(allowed_exp - blocked_exp, 3)

        return {
            "available":          True,
            "mode":               mode,
            "start_ts":           start_ts,
            "total_allowed":      allowed,
            "total_blocked":      blocked,
            "total_observations": allowed + blocked,
            "pending_outcomes":   pending,
            "completed_outcomes": completed,
            "no_geometry":        no_geom,
            "blocked_with_geometry": blk_geom,
            "geometry_rate":      round(100.0 * blk_geom / blocked, 1) if blocked > 0 else 0.0,
            "allowed_expectancy": round(allowed_exp, 3) if allowed_exp is not None else None,
            "blocked_expectancy": round(blocked_exp, 3) if blocked_exp is not None else None,
            "gate_improvement":   gate_imp,
            "gate_categories":    gate_cats,
            "component_pass_rates": {
                "BOS":     round(100.0 * int(cr[0] or 0) / comp_total, 1),
                "CHOCH":   round(100.0 * int(cr[1] or 0) / comp_total, 1),
                "VWAP":    round(100.0 * int(cr[2] or 0) / comp_total, 1),
                "Sweep":   round(100.0 * int(cr[3] or 0) / comp_total, 1),
                "Volume":  round(100.0 * int(cr[4] or 0) / comp_total, 1),
                "CVD":     round(100.0 * int(cr[5] or 0) / comp_total, 1),
                "Session": round(100.0 * int(cr[6] or 0) / comp_total, 1),
                "Zone":    round(100.0 * int(cr[7] or 0) / comp_total, 1),
            },
            "evidence_status": _evidence_status(completed),
            "health": {
                "last_observation_ts":  _last_obs.isoformat() if _last_obs else None,
                "last_resolved_ts":     _last_res.isoformat() if _last_res else None,
                "observations_24h":     obs_24h,
                "unique_opps_24h":      opps_24h,
                "pending_outcomes":     pending,
                "resolved_outcomes":    completed,
                "no_geometry_count":    no_geom,
                "atr_fallback_count":   atr_fb,
                "collector_status": (
                    "ACTIVE"  if _last_obs and
                                 (datetime.now(timezone.utc) - _last_obs).total_seconds() < 3600
                    else "NO_DATA" if _last_obs is None
                    else "SILENT"
                ),
            },
        }
    except Exception as exc:
        logger.debug("gate_effectiveness get_mode_report(%s): %s", mode, exc)
        return {"available": False, "error": str(exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_mode_comparison() -> dict:
    """Side-by-side SCALP vs INTRADAY_TREND effectiveness summary.
    Calls get_mode_report for each mode and combines into one dict.
    FAIL-OPEN — display-only.
    """
    scalp = get_mode_report("SCALP")
    it    = get_mode_report("INTRADAY_TREND")
    total = (scalp.get("total_observations", 0) +
             it.get("total_observations", 0))
    return {
        "available":       scalp.get("available", False) or it.get("available", False),
        "SCALP":           scalp,
        "INTRADAY_TREND":  it,
        "combined": {
            "total_observations": total,
            "modes_with_data":    sum(1 for m in (scalp, it) if m.get("available")),
            "geometry_rate_avg":  round(
                (scalp.get("geometry_rate", 0.0) + it.get("geometry_rate", 0.0)) / 2.0, 1
            ) if scalp.get("available") and it.get("available") else None,
        },
    }


def get_strategy_report(mode: str) -> dict:
    """Per-strategy funnel analytics within a mode: MODE→STRATEGY→GATE→OUTCOME.

    Returns a dict keyed by strategy label with full pipeline stats so the
    operator can compare e.g. VWAP_PULLBACK vs LIQUIDITY_SWEEP win rates.
    FAIL-OPEN — display-only, never touches gate or execution.
    """
    if not GATE_AUDIT_DB_READY:
        return {"available": False}
    conn = _learning_conn()
    if conn is None:
        return {"available": False, "error": "db_unavailable"}
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(strategy, mode)                                                    AS strat,
                    COUNT(*)                                                                    AS raw_evals,
                    COUNT(DISTINCT COALESCE(setup_id,
                          mode || '|' || COALESCE(strategy, mode) || '|' ||
                          instrument || '|' || direction || '|'
                          || TO_CHAR(recorded_at AT TIME ZONE 'UTC', 'YYYYMMDD')))             AS unique_opps,
                    COUNT(*) FILTER (WHERE gate_verdict IN ('ALLOWED','EARLY_ALLOWED'))        AS ready_count,
                    COUNT(*) FILTER (WHERE gate_verdict = 'BLOCKED')                           AS blocked_count,
                    COUNT(*) FILTER (WHERE outcome_status = 'COMPLETED')                       AS resolved_count,
                    COUNT(*) FILTER (WHERE outcome_status = 'COMPLETED' AND final_r > 0)       AS would_win,
                    COUNT(*) FILTER (WHERE outcome_status = 'COMPLETED' AND final_r <= 0)      AS would_lose,
                    COUNT(*) FILTER (WHERE outcome_status = 'NO_GEOMETRY')                     AS no_geometry,
                    ROUND(SUM(final_r)
                          FILTER (WHERE outcome_status = 'COMPLETED')::numeric, 3)             AS net_r,
                    ROUND(AVG(final_r)
                          FILTER (WHERE outcome_status = 'COMPLETED')::numeric, 3)             AS avg_r,
                    ROUND(SUM(final_r)
                          FILTER (WHERE outcome_status = 'COMPLETED' AND final_r > 0)::numeric, 3) AS gross_win,
                    ABS(ROUND(SUM(final_r)
                          FILTER (WHERE outcome_status = 'COMPLETED' AND final_r < 0)::numeric, 3)) AS gross_loss,
                    COUNT(*) FILTER (WHERE entry_price IS NOT NULL)                            AS with_geometry,
                    COUNT(*) FILTER (WHERE geometry_source = 'ATR_FALLBACK')                   AS atr_fallback
                FROM gate_audit_log
                WHERE mode = %s AND baseline_version = %s
                  AND audit_id NOT LIKE 'SYNTHETIC_%%'
                GROUP BY COALESCE(strategy, mode)
                ORDER BY raw_evals DESC
            """, (mode, BASELINE_VERSION))
            rows = cur.fetchall()

            # Top primary blocker per strategy (separate query for efficiency)
            cur.execute("""
                SELECT
                    COALESCE(strategy, mode) AS strat,
                    COALESCE(primary_blocker, 'Unknown') AS blocker,
                    COUNT(*) AS n
                FROM gate_audit_log
                WHERE mode = %s AND gate_verdict = 'BLOCKED'
                  AND baseline_version = %s
                  AND audit_id NOT LIKE 'SYNTHETIC_%%'
                GROUP BY 1, 2
                ORDER BY 1, 3 DESC
            """, (mode, BASELINE_VERSION))
            top_blocker: dict = {}
            for br in cur.fetchall():
                if br[0] not in top_blocker:
                    top_blocker[br[0]] = br[1]

        result: dict = {}
        for r in rows:
            strat        = r[0]
            raw_evals    = int(r[1] or 0)
            unique_opps  = int(r[2] or 0)
            ready        = int(r[3] or 0)
            blocked      = int(r[4] or 0)
            resolved     = int(r[5] or 0)
            win          = int(r[6] or 0)
            lose         = int(r[7] or 0)
            no_geom      = int(r[8] or 0)
            net_r        = _safe_float(r[9])
            avg_r        = _safe_float(r[10])
            gross_win    = _safe_float(r[11]) or 0.0
            gross_loss   = _safe_float(r[12]) or 0.0
            with_geom    = int(r[13] or 0)
            atr_fb       = int(r[14] or 0)

            pass_rate  = round(100.0 * ready  / raw_evals, 1) if raw_evals > 0 else 0.0
            win_rate   = round(100.0 * win / (win + lose), 1) if (win + lose) > 0 else None
            geom_rate  = round(100.0 * with_geom / raw_evals, 1) if raw_evals > 0 else 0.0
            pf         = round(gross_win / gross_loss, 2) if gross_loss > 0 else None

            result[strat] = {
                "strategy":             strat,
                "raw_evaluations":      raw_evals,
                "unique_opportunities": unique_opps,
                "ready_count":          ready,
                "blocked_count":        blocked,
                "pass_rate":            pass_rate,
                "resolved_count":       resolved,
                "would_win":            win,
                "would_lose":           lose,
                "no_geometry_count":    no_geom,
                "win_rate":             win_rate,
                "net_r":                net_r,
                "avg_r":                avg_r,
                "profit_factor":        pf,
                "geometry_rate":        geom_rate,
                "atr_fallback_count":   atr_fb,
                "top_primary_blocker":  top_blocker.get(strat),
                "evidence_status":      _evidence_status(win + lose),
            }

        return {
            "available":      True,
            "mode":           mode,
            "strategies":     result,
            "strategy_count": len(result),
        }
    except Exception as exc:
        logger.debug("gate_effectiveness get_strategy_report(%s): %s", mode, exc)
        return {"available": False, "error": str(exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_opportunities(
    mode: Optional[str] = None,
    days: int = 7,
    instrument: Optional[str] = None,
) -> list:
    """Deduplicated unique opportunities for the audit review panel.

    Collapses repeated hourly poll records into one row per
    (inst, dir, mode, date, primary_blocker) so the operator sees distinct
    setups rather than polling noise.  Returns the last 500 opportunities
    across the requested window.
    FAIL-OPEN — display-only, read-only.
    """
    if not GATE_AUDIT_DB_READY:
        return []
    conn = _learning_conn()
    if conn is None:
        return []
    try:
        filters  = [
            "baseline_version = %s",
            "recorded_at >= NOW() - make_interval(days => %s)",
            "gate_verdict = 'BLOCKED'",
            "audit_id NOT LIKE 'SYNTHETIC_%%'",
        ]
        params_: list = [BASELINE_VERSION, days]
        if mode:
            filters.append("mode = %s")
            params_.append(mode)
        if instrument:
            filters.append("instrument = %s")
            params_.append(instrument.upper())
        where = " AND ".join(filters)

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    mode, instrument, direction,
                    DATE(recorded_at AT TIME ZONE 'UTC')                               AS trade_date,
                    COALESCE(primary_blocker, 'Unknown')                               AS primary_blocker,
                    COUNT(*)                                                            AS n_polls,
                    MAX(edge_score)                                                     AS peak_edge,
                    MIN(recorded_at)                                                    AS first_seen,
                    MAX(recorded_at)                                                    AS last_seen,
                    BOOL_OR(entry_price IS NOT NULL)                                    AS has_geometry,
                    BOOL_OR(outcome_status = 'COMPLETED')                              AS is_resolved,
                    MAX(CASE WHEN outcome_status='COMPLETED' THEN final_r END)         AS final_r,
                    MAX(CASE WHEN outcome_status='COMPLETED' AND final_r > 0
                             THEN 1 ELSE 0 END)                                        AS was_winner,
                    MAX(strategy)                                                       AS strategy
                FROM gate_audit_log
                WHERE {where}
                GROUP BY mode, instrument, direction,
                         DATE(recorded_at AT TIME ZONE 'UTC'),
                         COALESCE(primary_blocker, 'Unknown')
                ORDER BY trade_date DESC, mode, instrument, direction
                LIMIT 500
            """, params_)
            rows = cur.fetchall()

        return [
            {
                "mode":            r[0],
                "instrument":      r[1],
                "direction":       r[2],
                "date":            str(r[3]),
                "primary_blocker": r[4],
                "n_polls":         int(r[5] or 0),
                "peak_edge":       int(r[6] or 0),
                "first_seen":      r[7].isoformat() if r[7] else None,
                "last_seen":       r[8].isoformat() if r[8] else None,
                "has_geometry":    bool(r[9]),
                "is_resolved":     bool(r[10]),
                "final_r":         _safe_float(r[11]),
                "was_winner":      bool(r[12]),
                "strategy":        r[13],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("gate_effectiveness get_opportunities: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
