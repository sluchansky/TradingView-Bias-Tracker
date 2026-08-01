"""Immutable trade-context snapshot captured at send time.

Pure module — no Flask imports, no DB access, no global state.
All functions are fail-open (never raise); missing keys yield None.

Called from app.py inside bare try/except wrappers that never block the
trade path.  DB persistence lives in app.py (_persist_trade_snapshot).

Public API:
    build_trade_snapshot(result, instrument, mode, source, contracts, ...) -> dict
    compute_execution_fingerprint(instrument, direction, entry_price, contracts, sent_at) -> str
    audit_broker_response(resp_text) -> dict
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def build_trade_snapshot(result, instrument, mode, source, contracts,
                         direction=None, entry=None, stop=None,
                         t1=None, t2=None, broker_out=None):
    """Extract all 28 required snapshot fields from a full_analysis result dict.

    Returns a flat dict ready for INSERT into internal_trade_snapshots.
    Any missing or unparseable field yields None — never raises.

    Parameters
    ----------
    result      : full_analysis() return dict
    instrument  : canonical instrument string (e.g. "MNQ")
    mode        : execution mode ("traderspost", "paper", "manual_only", …)
    source      : gateway source label ("manual", "auto", "paper", …)
    contracts   : number of contracts (int)
    direction   : "Long" or "Short" — resolved by the gateway before calling
    entry/stop/t1/t2 : resolved price floats from the gateway's trade plan
    broker_out  : dict populated by audit_broker_response() on 2xx (may be None)
    """
    now = datetime.now(timezone.utc)
    broker_out = broker_out or {}

    # ── Strategy identity ────────────────────────────────────────────────────
    canonical_strategy_key = strategy_display_name = setup_name = None
    try:
        se = (result.get("strategy_engine") or {})
        canonical_strategy_key = se.get("active_key")
        strategy_display_name  = se.get("active_strategy")
        setup_name             = se.get("setup_type")
    except Exception:
        pass

    # ── Thesis fields ────────────────────────────────────────────────────────
    thesis_direction = thesis_strength = thesis_alignment = None
    try:
        # Try thesis_tracker → top-level thesis → left_brain.thesis (in priority)
        blk = (result.get("thesis_tracker")
               or result.get("thesis")
               or (result.get("left_brain") or {}).get("thesis")
               or {})
        if isinstance(blk, dict):
            thesis_direction = blk.get("direction")
            thesis_strength  = (blk.get("strength")
                                or blk.get("confidence")
                                or blk.get("momentum"))
            thesis_alignment = blk.get("alignment")
    except Exception:
        pass

    # ── Playbook ─────────────────────────────────────────────────────────────
    playbook = None
    try:
        mb = result.get("main_brain") or {}
        playbook = (mb.get("playbook")
                    or (mb.get("voice") or {}).get("playbook")
                    or (mb.get("narrative") or {}).get("playbook"))
    except Exception:
        pass

    # ── Confirmations / blockers ─────────────────────────────────────────────
    confirmations = blockers = None
    try:
        gd = result.get("gate_debug") or {}
        ad = result.get("alert_diagnostics") or {}
        # Use is-not-None rather than truthiness: an empty list is a valid value.
        _c = result.get("confirmations")
        if _c is None:
            _c = gd.get("confirmations")
        if _c is None:
            _c = ad.get("confirmations")
        confirmations = _c
        _b = result.get("blockers")
        if _b is None:
            _b = gd.get("blockers")
        if _b is None:
            _b = result.get("strict_reason")
        blockers = _b
        if confirmations is not None and not isinstance(confirmations, list):
            confirmations = [str(confirmations)]
        if blockers is not None and not isinstance(blockers, list):
            blockers = [str(blockers)]
    except Exception:
        pass

    # ── Opposing structure / risk state ──────────────────────────────────────
    opposing_structure = None
    try:
        v = result.get("opposing_structure")
        opposing_structure = str(v) if v else None
    except Exception:
        pass

    risk_state = None
    try:
        v = result.get("risk_state")
        risk_state = str(v) if v else None
    except Exception:
        pass

    # ── Planned targets ───────────────────────────────────────────────────────
    planned_targets = None
    try:
        if t1 is not None or t2 is not None:
            planned_targets = {
                "t1": _safe_float(t1),
                "t2": _safe_float(t2),
            }
    except Exception:
        pass

    # ── Planned risk (points from entry to stop) ──────────────────────────────
    planned_risk = None
    try:
        if entry is not None and stop is not None:
            planned_risk = round(abs(float(entry) - float(stop)), 4)
    except Exception:
        pass

    # ── Readiness / actionable from verdict ───────────────────────────────────
    verdict   = str(result.get("verdict") or "")
    readiness = verdict or None
    actionable = bool(verdict and "READY" in verdict.upper())

    # ── Execution fingerprint ─────────────────────────────────────────────────
    fingerprint = compute_execution_fingerprint(
        instrument, direction or "", entry, contracts, now)

    # ── Contract / account from result or INSTRUMENT_SPECS ───────────────────
    contract = (_safe_str(result.get("broker_symbol"))
                or _safe_str(result.get("contract")))
    account  = _safe_str(result.get("account_id"))

    return {
        "internal_trade_id":      str(uuid.uuid4()),
        "signal_id":              _safe_str(result.get("signal_id")),
        "execution_fingerprint":  fingerprint,
        "instrument":             instrument,
        "contract":               contract,
        "account":                account,
        "mode":                   mode,
        "direction":              direction,
        "canonical_strategy_key": canonical_strategy_key,
        "strategy_display_name":  strategy_display_name,
        "setup_name":             setup_name,
        "playbook":               playbook,
        "thesis_direction":       thesis_direction,
        "thesis_strength":        _safe_str(thesis_strength),
        "thesis_alignment":       _safe_str(thesis_alignment),
        "edge_score":             _safe_float(result.get("edge_score")),
        "grade":                  _safe_str(result.get("grade")),
        "readiness":              readiness,
        "actionable":             actionable,
        "confirmations":          confirmations,
        "blockers":               blockers,
        "opposing_structure":     opposing_structure,
        "risk_state":             risk_state,
        "planned_entry":          _safe_float(entry),
        "planned_stop":           _safe_float(stop),
        "planned_targets":        planned_targets,
        "planned_risk":           planned_risk,
        "planned_contracts":      _safe_int(contracts),
        "source":                 source,
        "broker_order_id":        broker_out.get("order_id"),
        "broker_signal_id":       broker_out.get("signal_id"),
        "broker_metadata":        broker_out if broker_out else None,
        "created_at":             now,
        "sent_at":                now,
    }


def compute_execution_fingerprint(instrument, direction, entry_price, contracts, sent_at):
    """Return a 32-char deterministic hex fingerprint for this send event.

    sha256( instrument | direction | entry_2dp | contracts | sent_at_second )

    Truncated to 32 chars for compactness while remaining collision-resistant
    for the matching use-case (same instrument/direction/entry/contracts/second).
    Returns None on any error.
    """
    try:
        ts = (sent_at.replace(microsecond=0).isoformat()
              if isinstance(sent_at, datetime) else str(sent_at)[:19])
        ep = f"{float(entry_price):.2f}" if entry_price is not None else "0.00"
        ct = str(int(contracts)) if contracts is not None else "1"
        raw = f"{instrument}|{direction}|{ep}|{ct}|{ts}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]
    except Exception:
        return None


def audit_broker_response(resp_text):
    """Parse a broker HTTP response body for order/signal IDs.

    Returns a flat dict with any recognized identifiers.  Stores the full
    parsed JSON in ``raw_response`` for forward-compatibility.
    Never raises — returns {} on any parse error.
    """
    out = {}
    try:
        if not resp_text:
            return out
        data = json.loads(resp_text)
        if not isinstance(data, dict):
            return out
        # TradersPost / PickMyTrade ID keys (checked in priority order)
        for src, dst in [
            ("orderId",    "order_id"),
            ("order_id",   "order_id"),
            ("tradeId",    "order_id"),
            ("id",         "order_id"),
            ("signalId",   "signal_id"),
            ("signal_id",  "signal_id"),
            ("webhookId",  "webhook_id"),
            ("webhook_id", "webhook_id"),
        ]:
            val = data.get(src)
            if val is not None and str(val).strip():
                out.setdefault(dst, str(val))
        out["raw_response"] = data
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Private helpers (used only within this module)
# ---------------------------------------------------------------------------

def _safe_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_str(v):
    try:
        return str(v) if v is not None else None
    except Exception:
        return None
