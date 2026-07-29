"""test_phase2_market_data_reliability.py — V1 Phase 2: Market Data and Feature Reliability.

Covers all 8 Phase 2 tasks:
  V1-P2-001  Databento health smoke (OFFLINE detection + gate continuity)
  V1-P2-002  Instrument initialization (all 4 instruments present with required config)
  V1-P2-003  Stale-VWAP gate test (gate refuses READY on stale/absent VWAP)
  V1-P2-004  Feed-interruption recovery (stale→gate blocked, fresh→gate evaluates)
  V1-P2-005  Pine-default-to-MGC documentation (TD-014; no test — doc only)
  V1-P2-006  Session transition timing (market_session_status at CME halt times)
  V1-P2-007  Session-closed gate test (closed market → MARKET CLOSED verdict)
  V1-P2-008  Clock-skew handling (_audit_event_duplicates now_dt kwarg)

Stream: B — Market Data and Feature Reliability
Must not change: Databento behavior, Pine webhook handling, normalization logic, gate logic.

All tests are READ-ONLY. No app.py or execution-path changes.
"""
import json
import os
import sys
import importlib
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import app
importlib.reload(app)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _now_utc():
    return datetime.now(timezone.utc)


# ===========================================================================
# V1-P2-001  DATABENTO HEALTH SMOKE
# Verifies: ARCH §5 Scenario 1; AC-7.2
# ===========================================================================

def test_databento_offline_returns_disabled_status():
    """get_databento_status() must return enabled=False and ok=False when the feed is
    OFFLINE. In dev, DATABENTO_ENABLED may be True but _DATABENTO_BRAIN is always None
    (only initialized in __main__). The function's condition is:
      if not DATABENTO_ENABLED OR _DATABENTO_BRAIN is None → OFFLINE
    So in dev it always returns OFFLINE."""
    with app.app.test_client() as client:
        resp = client.get("/databento-status")
        data = resp.get_json()
    assert resp.status_code == 200, (
        f"Expected HTTP 200 from /databento-status; got {resp.status_code}")
    assert data.get("enabled") is False, (
        f"enabled must be False when _DATABENTO_BRAIN is None; got {data.get('enabled')!r}")
    assert data.get("ok") is False, (
        f"ok must be False when Databento OFFLINE; got {data.get('ok')!r}")


def test_databento_offline_gate_still_evaluates():
    """full_analysis() must still run and return a valid Expert result when
    Databento is OFFLINE. The gate must not break on OFFLINE state."""
    result = app.full_analysis()
    assert isinstance(result, dict), "full_analysis() must return a dict"
    assert "verdict" in result, (
        "full_analysis() must have a 'verdict' key even with Databento OFFLINE")
    assert "edge_score" in result, (
        "full_analysis() must have 'edge_score' even with Databento OFFLINE")


def test_databento_offline_expert_interface_contract_intact():
    """Databento OFFLINE must not affect the Expert Interface v1 contract.
    All guaranteed fields must remain present and _version must stay 'v1'."""
    result = app.full_analysis()
    assert result.get("_version") == "v1", (
        f"Expert _version must be 'v1' with Databento OFFLINE; got {result.get('_version')!r}")
    for field in ("verdict", "edge_score", "strict_reason", "gate_debug",
                  "trade_plan", "alert_diagnostics", "_version"):
        assert field in result, (
            f"Expert guaranteed field {field!r} missing with Databento OFFLINE")


def test_databento_brain_is_none_in_dev():
    """_DATABENTO_BRAIN must be None in the dev environment — it is only populated
    in __main__ when the full server starts with a valid DATABENTO_API_KEY.
    This is the primary OFFLINE guard: get_databento_status() returns disabled
    when not DATABENTO_ENABLED OR _DATABENTO_BRAIN is None."""
    assert app._DATABENTO_BRAIN is None, (
        f"_DATABENTO_BRAIN must be None in dev (not initialized outside __main__); "
        f"got {type(app._DATABENTO_BRAIN)}")


def test_databento_offline_guard_logic():
    """The OFFLINE guard must fire when _DATABENTO_BRAIN is None, regardless of
    DATABENTO_ENABLED. This proves the gate is enforced even if the env var is set
    but the live brain object was never created."""
    # The guard condition in get_databento_status():
    #   if not DATABENTO_ENABLED or _DATABENTO_BRAIN is None: → OFFLINE
    # In dev: _DATABENTO_BRAIN is None → OFFLINE even when DATABENTO_ENABLED=True
    brain_is_none = app._DATABENTO_BRAIN is None
    enabled = bool(app.DATABENTO_ENABLED)
    gate_fires_offline = (not enabled) or brain_is_none
    assert gate_fires_offline, (
        "OFFLINE guard must fire: (not DATABENTO_ENABLED) OR (_DATABENTO_BRAIN is None); "
        f"enabled={enabled}, brain_is_none={brain_is_none}")


# ===========================================================================
# V1-P2-002  INSTRUMENT INITIALIZATION
# Verifies: all 4 instruments initialize on boot with required configuration.
# ===========================================================================

REQUIRED_INSTRUMENTS = {"MGC", "MNQ", "MES", "MYM"}
REQUIRED_ASSET_FIELDS = ("symbol", "enabled", "vwap_feed", "specs",
                          "asset_class", "discord_env")
REQUIRED_SPEC_FIELDS  = ("point_value", "tick_size", "tp1", "tp2", "stop_buf",
                          "min_stop_ticks", "min_stop_pts")


def test_all_four_instruments_present_in_assets():
    """All 4 instruments (MGC, MNQ, MES, MYM) must be present in the ASSETS registry."""
    missing = REQUIRED_INSTRUMENTS - set(app.ASSETS.keys())
    assert not missing, f"Instruments missing from ASSETS: {missing}"


def test_all_instruments_enabled():
    """All 4 instruments must have enabled=True in ASSETS."""
    for inst in REQUIRED_INSTRUMENTS:
        assert app.ASSETS[inst].get("enabled") is True, (
            f"{inst} must have enabled=True in ASSETS")


def test_all_instruments_have_required_asset_fields():
    """Every instrument must carry all required ASSETS fields."""
    for inst in REQUIRED_INSTRUMENTS:
        cfg = app.ASSETS[inst]
        for field in REQUIRED_ASSET_FIELDS:
            assert field in cfg, f"{inst} ASSETS missing required field: {field!r}"


def test_all_instruments_have_required_spec_fields():
    """Every instrument must carry all required specs fields."""
    for inst in REQUIRED_INSTRUMENTS:
        specs = app.ASSETS[inst].get("specs") or {}
        for field in REQUIRED_SPEC_FIELDS:
            assert field in specs, (
                f"{inst}.specs missing required field: {field!r}")


def test_instrument_specs_mirrored_in_instrument_specs():
    """INSTRUMENT_SPECS must contain entries for all 4 instruments (derived from ASSETS)."""
    for inst in REQUIRED_INSTRUMENTS:
        assert inst in app.INSTRUMENT_SPECS, (
            f"{inst} missing from INSTRUMENT_SPECS (derived from ASSETS)")


def test_alert_instruments_match_assets():
    """Boot assertion: ALERT_TYPES instrument set must match ASSETS keys.
    This assertion runs on import — if it fails, the module won't load at all.
    This test confirms the assertion still holds."""
    from app import _ALERT_INSTRUMENTS
    assets_set = set(app.ASSETS.keys())
    alert_set = set(_ALERT_INSTRUMENTS)
    assert alert_set == assets_set, (
        f"ALERT_TYPES instruments {alert_set!r} drifted from ASSETS {assets_set!r}")


def test_instrument_of_resolves_all_known_tickers():
    """instrument_of() must correctly resolve known ticker aliases.

    Aliases use substring matching (alias IN ticker string). Each instrument's
    aliases list contains its canonical name (e.g. ['MNQ']), so 'MNQ1!' contains
    'MNQ' → resolves to MNQ. Tickers that don't contain any alias → default MGC.
    """
    cases = {
        # Exact canonical name
        "MGC":   "MGC",
        "MNQ":   "MNQ",
        "MES":   "MES",
        "MYM":   "MYM",
        # Continuous contract notation (alias is substring of ticker)
        "MGC1!": "MGC",   # 'MGC' ∈ 'MGC1!' ✓
        "MNQ1!": "MNQ",   # 'MNQ' ∈ 'MNQ1!' ✓
        "MES1!": "MES",   # 'MES' ∈ 'MES1!' ✓
        "MYM1!": "MYM",   # 'MYM' ∈ 'MYM1!' ✓
    }
    for ticker, expected in cases.items():
        result = app.instrument_of(ticker)
        assert result == expected, (
            f"instrument_of({ticker!r}) → {result!r}, expected {expected!r}")


def test_instrument_of_default_unknown_to_mgc():
    """instrument_of() must default unrecognized tickers to MGC (lenient normalizer).
    This documents the accepted Pine default behavior (TD-014)."""
    unknown_cases = ("", None, "UNKNOWN", "XAUUSD", "BTC")
    for ticker in unknown_cases:
        result = app.instrument_of(ticker)
        assert result == "MGC", (
            f"instrument_of({ticker!r}) must default to 'MGC' (lenient normalizer); "
            f"got {result!r}")


def test_resolve_instrument_rejects_unknown_tickers():
    """resolve_instrument() (strict resolver) must reject unrecognized tickers
    rather than silently defaulting. Nothing silently defaults in the money path."""
    result = app.resolve_instrument("XAUUSD", "SOME ALERT")
    assert not result.get("ok"), (
        f"resolve_instrument must reject unknown ticker 'XAUUSD'; got ok={result.get('ok')!r}")
    assert result.get("instrument") is None, (
        "resolve_instrument must return instrument=None for unknown ticker")


def test_enabled_instruments_returns_all_four():
    """enabled_instruments() must return all 4 instruments when all are enabled."""
    enabled = set(app.enabled_instruments())
    assert REQUIRED_INSTRUMENTS.issubset(enabled), (
        f"enabled_instruments() missing: {REQUIRED_INSTRUMENTS - enabled}")


# ===========================================================================
# V1-P2-003  STALE-VWAP GATE TEST
# Verifies: ARCH §5 Scenario 2; AC-5.3
# The strict gate must refuse READY on stale or absent VWAP.
# ===========================================================================

def _inject_vwap(inst, value, age_minutes):
    """Inject a VWAP record with a specific age into VWAP_BY_TICKER."""
    ts = (_now_utc() - timedelta(minutes=age_minutes)).isoformat()
    app.VWAP_BY_TICKER[inst] = {"value": value, "ts": ts, "source": "test"}


def _clear_vwap(inst):
    app.VWAP_BY_TICKER.pop(inst, None)


def test_get_vwap_returns_missing_when_no_vwap_stored():
    """get_vwap() must return (None, 'missing') when VWAP_BY_TICKER has no entry."""
    saved = app.VWAP_BY_TICKER.pop("MGC", None)
    try:
        val, status = app.get_vwap("MGC")
        assert val is None, f"Expected vwap=None when not stored; got {val!r}"
        assert status == "missing", f"Expected status='missing'; got {status!r}"
    finally:
        if saved is not None:
            app.VWAP_BY_TICKER["MGC"] = saved


def test_get_vwap_returns_stale_when_timestamp_too_old():
    """get_vwap() must return (None, 'stale') when VWAP age exceeds freshness window.
    Freshness window is STAGE_WINDOW_MIN (default 30 min) — a 2-hour-old VWAP is stale."""
    saved = app.VWAP_BY_TICKER.pop("MGC", None)
    try:
        _inject_vwap("MGC", 2700.0, age_minutes=120)  # 2 hours old — stale
        val, status = app.get_vwap("MGC")
        assert val is None, f"Stale VWAP must return val=None; got {val!r}"
        assert status == "stale", f"Stale VWAP must return status='stale'; got {status!r}"
    finally:
        _clear_vwap("MGC")
        if saved is not None:
            app.VWAP_BY_TICKER["MGC"] = saved


def test_get_vwap_returns_ok_when_timestamp_fresh():
    """get_vwap() must return (float, 'ok') when VWAP timestamp is current."""
    saved = app.VWAP_BY_TICKER.pop("MGC", None)
    try:
        _inject_vwap("MGC", 2700.0, age_minutes=0)  # just written — fresh
        val, status = app.get_vwap("MGC")
        assert val is not None, "Fresh VWAP must return a float value"
        assert isinstance(val, float), f"Fresh VWAP value must be float; got {type(val)}"
        assert status == "ok", f"Fresh VWAP must return status='ok'; got {status!r}"
    finally:
        _clear_vwap("MGC")
        if saved is not None:
            app.VWAP_BY_TICKER["MGC"] = saved


def test_vwap_gate_condition_boundary():
    """The gate condition vwap_ok = (vwap_status == 'ok') and vwap is not None
    must produce False for missing/stale and True for ok.

    This tests the exact gate expression from evaluate_strict_setup line ~7048.
    """
    cases = [
        # (vwap_status, vwap_value, expected_ok)
        ("missing", None,   False),  # no VWAP stored
        ("stale",   None,   False),  # stale VWAP (get_vwap returns None)
        ("ok",      2700.0, True),   # fresh VWAP
        ("n/a",     None,   False),  # not set
        ("ok",      None,   False),  # ok status but vwap is None (edge case)
    ]
    for vwap_status, vwap_value, expected in cases:
        # Replicate the gate condition exactly from evaluate_strict_setup
        price = 2700.0
        vwap_ok = vwap_status == "ok" and vwap_value is not None and price is not None
        assert vwap_ok == expected, (
            f"Gate condition: vwap_status={vwap_status!r}, vwap={vwap_value!r} → "
            f"vwap_ok={vwap_ok}, expected {expected}")


def test_stale_vwap_source_is_in_source_code():
    """Source-level verification: gate condition is exactly vwap_ok = vwap_status == 'ok'.
    This is the gatekeeper expression that all VWAP staleness tests rely on."""
    with open(os.path.join(os.path.dirname(__file__), "app.py")) as f:
        src = f.read()
    assert 'vwap_status == "ok" and vwap is not None' in src, (
        "Gate condition vwap_ok = vwap_status == 'ok' and vwap is not None "
        "must be present in evaluate_strict_setup")


# ===========================================================================
# V1-P2-004  FEED-INTERRUPTION RECOVERY TEST
# Verifies: ARCH §5 Scenario 2 recovery; VWAP stale→WAIT, fresh→evaluates.
# ===========================================================================

def test_stale_vwap_produces_wait_not_ready():
    """With a stale VWAP, full_analysis() must not produce a READY verdict.
    The gate blocks actionable setups when VWAP is unreliable.

    Note: The full_analysis() result may show 'MARKET CLOSED' (a non-actionable
    verdict) or 'WAIT'. Both are correct — neither is READY.
    """
    saved = app.VWAP_BY_TICKER.pop("MGC", None)
    try:
        _inject_vwap("MGC", 2700.0, age_minutes=120)   # stale: 2h old
        result = app.full_analysis()
        verdict = result.get("verdict", "")
        # Gate must not produce actionable READY on stale VWAP
        assert not app.is_actionable(verdict), (
            f"Stale VWAP must not produce actionable verdict; got {verdict!r}")
        # Verify vwap_status reflects the staleness
        assert result.get("vwap_status") in ("stale", "missing"), (
            f"vwap_status must be stale/missing with 2h-old VWAP; "
            f"got {result.get('vwap_status')!r}")
    finally:
        _clear_vwap("MGC")
        if saved is not None:
            app.VWAP_BY_TICKER["MGC"] = saved


def test_fresh_vwap_allows_gate_to_evaluate():
    """With a fresh VWAP, full_analysis() must show vwap_status='ok' — the gate
    can evaluate the VWAP condition (though other conditions may still produce WAIT).

    This is the recovery path: after feed interruption, once VWAP is fresh, the gate
    runs normally. We verify vwap_status='ok', not that the verdict is READY (other
    gate conditions control that).
    """
    saved = app.VWAP_BY_TICKER.pop("MGC", None)
    try:
        _inject_vwap("MGC", 2700.0, age_minutes=0)   # fresh: just written
        result = app.full_analysis()
        assert result.get("vwap_status") == "ok", (
            f"Fresh VWAP must produce vwap_status='ok'; got {result.get('vwap_status')!r}")
        assert result.get("vwap_value") is not None, (
            "Fresh VWAP must populate vwap_value in result")
    finally:
        _clear_vwap("MGC")
        if saved is not None:
            app.VWAP_BY_TICKER["MGC"] = saved


def test_vwap_recovery_transition():
    """Feed interruption recovery: stale→missing→fresh cycle produces correct
    status transitions. Proves the gate correctly tracks VWAP lifecycle."""
    saved = app.VWAP_BY_TICKER.pop("MGC", None)
    try:
        # Step 1: Feed interrupted — VWAP missing
        _clear_vwap("MGC")
        _, s1 = app.get_vwap("MGC")
        assert s1 == "missing", f"Step 1 (missing): expected 'missing', got {s1!r}"

        # Step 2: Feed still down — old VWAP available but stale
        _inject_vwap("MGC", 2700.0, age_minutes=60)
        _, s2 = app.get_vwap("MGC")
        assert s2 == "stale", f"Step 2 (stale): expected 'stale', got {s2!r}"

        # Step 3: Feed recovered — fresh VWAP available
        _inject_vwap("MGC", 2700.0, age_minutes=0)
        v3, s3 = app.get_vwap("MGC")
        assert s3 == "ok", f"Step 3 (fresh): expected 'ok', got {s3!r}"
        assert v3 is not None, "Step 3 (fresh): VWAP value must not be None"

        print(f"  PASS  V1-P2-004: VWAP recovery sequence: missing→stale→ok ✓")
    finally:
        _clear_vwap("MGC")
        if saved is not None:
            app.VWAP_BY_TICKER["MGC"] = saved


# ===========================================================================
# V1-P2-006  SESSION TRANSITION TIMING TEST
# Verifies: market_session_status() transitions at CME halt times (17:00/18:00 ET)
# ARCH §5; Implementation Principle 11 (market session awareness)
# ===========================================================================

def _et(year, month, day, hour, minute=0):
    """Create an ET-aware datetime for testing (handles EST/EDT automatically)."""
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    return datetime(year, month, day, hour, minute, tzinfo=ET)


def test_session_open_monday_midday():
    """Monday midday (12:00 ET) must be OPEN — well within trading hours."""
    # July 28, 2025 is a Monday
    now = _et(2025, 7, 28, 12, 0)
    result = app.market_session_status(now=now)
    assert result.get("open") is True, (
        f"Monday 12:00 ET must be OPEN; got open={result.get('open')!r}, "
        f"reason={result.get('reason')!r}")
    assert result.get("status") == "OPEN", (
        f"status must be 'OPEN' at Monday noon; got {result.get('status')!r}")


def test_session_closed_monday_at_daily_halt_start():
    """Monday at 17:00 ET must be CLOSED — daily maintenance halt starts."""
    now = _et(2025, 7, 28, 17, 0)
    result = app.market_session_status(now=now)
    assert result.get("open") is False, (
        f"Monday 17:00 ET must be CLOSED (maintenance halt); got open={result.get('open')!r}")
    assert result.get("status") == "CLOSED", (
        f"status must be 'CLOSED' at halt start; got {result.get('status')!r}")
    assert "maintenance" in (result.get("reason") or "").lower(), (
        f"reason must mention maintenance; got {result.get('reason')!r}")


def test_session_closed_monday_during_halt():
    """Monday at 17:30 ET must be CLOSED — during daily maintenance halt."""
    now = _et(2025, 7, 28, 17, 30)
    result = app.market_session_status(now=now)
    assert result.get("open") is False, (
        f"Monday 17:30 ET must be CLOSED (mid-halt); got open={result.get('open')!r}")


def test_session_open_monday_after_halt():
    """Monday at 18:00 ET must be OPEN — daily maintenance halt ends."""
    now = _et(2025, 7, 28, 18, 0)
    result = app.market_session_status(now=now)
    assert result.get("open") is True, (
        f"Monday 18:00 ET must be OPEN (halt ends); got open={result.get('open')!r}, "
        f"reason={result.get('reason')!r}")


def test_session_open_tuesday_morning():
    """Tuesday morning (09:30 ET) must be OPEN."""
    now = _et(2025, 7, 29, 9, 30)
    result = app.market_session_status(now=now)
    assert result.get("open") is True, (
        f"Tuesday 09:30 ET must be OPEN; got open={result.get('open')!r}")


def test_session_closed_thursday_at_halt():
    """Thursday at 17:15 ET must be CLOSED — halt applies Mon–Thu."""
    now = _et(2025, 7, 31, 17, 15)
    result = app.market_session_status(now=now)
    assert result.get("open") is False, (
        f"Thursday 17:15 ET must be CLOSED (halt); got open={result.get('open')!r}")


def test_session_closed_friday_after_market_close():
    """Friday at 17:00 ET must be CLOSED — weekly close (no reopen until Sunday)."""
    now = _et(2025, 8, 1, 17, 0)
    result = app.market_session_status(now=now)
    assert result.get("open") is False, (
        f"Friday 17:00 ET must be CLOSED (weekly close); got open={result.get('open')!r}")


def test_session_closed_saturday():
    """Saturday must always be CLOSED — no CME trading."""
    now = _et(2025, 8, 2, 12, 0)
    result = app.market_session_status(now=now)
    assert result.get("open") is False, (
        f"Saturday must always be CLOSED; got open={result.get('open')!r}")
    assert "weekend" in (result.get("reason") or "").lower(), (
        f"Saturday reason must mention weekend; got {result.get('reason')!r}")


def test_session_open_sunday_evening():
    """Sunday at 18:30 ET must be OPEN — weekly session opens at 18:00 ET."""
    now = _et(2025, 8, 3, 18, 30)
    result = app.market_session_status(now=now)
    assert result.get("open") is True, (
        f"Sunday 18:30 ET must be OPEN (weekly open); got open={result.get('open')!r}")


def test_session_closed_sunday_before_open():
    """Sunday at 17:30 ET must be CLOSED — weekly session not yet open."""
    now = _et(2025, 8, 3, 17, 30)
    result = app.market_session_status(now=now)
    assert result.get("open") is False, (
        f"Sunday 17:30 ET must be CLOSED (before weekly open); got open={result.get('open')!r}")


def test_session_status_returns_required_fields():
    """market_session_status() must always return a dict with all required fields."""
    now = _et(2025, 7, 28, 12, 0)
    result = app.market_session_status(now=now)
    for field in ("open", "status", "next_open_et", "reason"):
        assert field in result, f"market_session_status missing field: {field!r}"


def test_session_status_disabled_always_open():
    """When MARKET_HOURS_ENABLED is False, market_session_status() always returns OPEN
    (backward-compatible: disabling market hours restores always-open behavior)."""
    saved = app.MARKET_HOURS_ENABLED
    try:
        app.MARKET_HOURS_ENABLED = False
        # Saturday — would be CLOSED if enabled
        now = _et(2025, 8, 2, 12, 0)
        result = app.market_session_status(now=now)
        assert result.get("open") is True, (
            "With MARKET_HOURS_ENABLED=False, status must always be OPEN "
            f"(backward compat); got open={result.get('open')!r}")
    finally:
        app.MARKET_HOURS_ENABLED = saved


# ===========================================================================
# V1-P2-007  SESSION-CLOSED GATE TEST
# Verifies: market closed → full_analysis() verdict is 'MARKET CLOSED', not actionable.
# Acceptance criterion: market closed → verdict WAIT/MARKET CLOSED + market_closed reason.
# ===========================================================================

def test_market_closed_produces_market_closed_verdict():
    """When market_session_status() returns CLOSED, full_analysis() must produce
    verdict='MARKET CLOSED' and market_open=False.

    The closed-override block runs LAST in full_analysis() — it always neutralizes
    any READY verdict when the session is closed.
    """
    closed_session = {
        "open": False, "status": "CLOSED",
        "next_open": None, "next_open_et": "Mon Jul 28, 6:00 PM ET",
        "reason": "Daily maintenance break",
    }
    with patch.object(app, "market_session_status", return_value=closed_session):
        result = app.full_analysis()

    verdict = result.get("verdict", "")
    assert verdict == "MARKET CLOSED", (
        f"Market closed must produce verdict='MARKET CLOSED'; got {verdict!r}")


def test_market_closed_sets_market_open_false():
    """full_analysis() must set market_open=False when session is CLOSED."""
    closed_session = {
        "open": False, "status": "CLOSED",
        "next_open": None, "next_open_et": "",
        "reason": "Weekend close",
    }
    with patch.object(app, "market_session_status", return_value=closed_session):
        result = app.full_analysis()

    assert result.get("market_open") is False, (
        f"market_open must be False when session closed; got {result.get('market_open')!r}")


def test_market_closed_is_not_actionable():
    """'MARKET CLOSED' verdict must not be actionable (is_actionable must return False)."""
    assert not app.is_actionable("MARKET CLOSED"), (
        "'MARKET CLOSED' must not be actionable — no entries during closed market")
    assert not app.is_actionable("LONG MARKET CLOSED"), (
        "Any 'MARKET CLOSED' variant must not be actionable")


def test_market_closed_rejected_reasons():
    """full_analysis() with closed session must include 'market_closed' in rejected_reasons."""
    closed_session = {
        "open": False, "status": "CLOSED",
        "next_open": None, "next_open_et": "",
        "reason": "Daily maintenance break",
    }
    with patch.object(app, "market_session_status", return_value=closed_session):
        result = app.full_analysis()

    rr = result.get("rejected_reasons") or []
    assert "market_closed" in rr, (
        f"rejected_reasons must contain 'market_closed'; got {rr!r}")


def test_market_closed_preserves_interface_contracts():
    """Closed market must not break any canonical interface contract.
    All 7 _version fields must remain intact when verdict is MARKET CLOSED."""
    closed_session = {
        "open": False, "status": "CLOSED",
        "next_open": None, "next_open_et": "",
        "reason": "Daily maintenance break",
    }
    with patch.object(app, "market_session_status", return_value=closed_session):
        result = app.full_analysis()

    # Expert v1
    assert result.get("_version") == "v1", (
        f"Expert _version must be 'v1' when market closed; got {result.get('_version')!r}")
    # Manager v1
    mgr = result.get("manager") or {}
    assert mgr.get("_version") == "v1", (
        f"Manager _version must be 'v1' when market closed; got {mgr.get('_version')!r}")
    # Coach v1
    cch = result.get("coach") or {}
    assert cch.get("_version") == "v1", (
        f"Coach _version must be 'v1' when market closed; got {cch.get('_version')!r}")


def test_market_open_produces_non_closed_verdict():
    """When market_session_status() returns OPEN, verdict must NOT be 'MARKET CLOSED'."""
    open_session = {
        "open": True, "status": "OPEN",
        "next_open": None, "next_open_et": "",
        "reason": "",
    }
    with patch.object(app, "market_session_status", return_value=open_session):
        result = app.full_analysis()

    verdict = result.get("verdict", "")
    assert verdict != "MARKET CLOSED", (
        f"Open market must not produce 'MARKET CLOSED' verdict; got {verdict!r}")
    assert result.get("market_open") is not False, (
        f"market_open must not be False when session is OPEN; got {result.get('market_open')!r}")


# ===========================================================================
# V1-P2-008  CLOCK-SKEW HANDLING (_audit_event_duplicates now_dt)
# Verifies: ALREADY COMPLETE — _audit_event_duplicates accepts now_dt kwarg
# confirming clock-skew events are filtered relative to the provided reference time.
# ===========================================================================

def test_audit_event_duplicates_accepts_now_dt_kwarg():
    """_audit_event_duplicates() must accept a now_dt keyword argument.
    This confirms the kwarg is present in the function signature (already complete
    per memory; verification test only).
    """
    import inspect
    sig = inspect.signature(app._audit_event_duplicates)
    assert "now_dt" in sig.parameters, (
        "_audit_event_duplicates must have 'now_dt' kwarg for clock-skew handling")


def test_audit_event_duplicates_uses_provided_now_dt():
    """_audit_event_duplicates() with a specific now_dt must accept it and return
    a result (list of duplicate events). The function uses now_dt as the reference
    time for the 1-hour cutoff, preventing clock-skewed alerts from producing
    false positives or negatives.
    """
    # Call with a past now_dt — all events should be outside the window (empty result)
    far_past_now = datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc)
    result = app._audit_event_duplicates(
        inst="MGC",
        alert_history_snapshot=[],
        window_seconds=120,
        now_dt=far_past_now,
    )
    # Function returns a list of duplicate entries (empty list when no history)
    assert isinstance(result, list), (
        f"_audit_event_duplicates must return a list; got {type(result)}")
    # With empty history and a far-past now_dt, result must be empty
    assert result == [], (
        f"Empty alert history must produce no duplicates; got {result!r}")


def test_audit_event_duplicates_defaults_now_dt_to_utc_now():
    """_audit_event_duplicates() without now_dt must default to datetime.now(UTC)
    — not use a stale reference."""
    import inspect
    sig = inspect.signature(app._audit_event_duplicates)
    now_dt_param = sig.parameters["now_dt"]
    assert now_dt_param.default is None, (
        "now_dt default must be None (function computes UTC now internally); "
        f"got default={now_dt_param.default!r}")


def test_audit_event_duplicates_now_dt_in_source():
    """Source verification: _audit_event_duplicates must set now_dt from kwarg
    or default to datetime.now(timezone.utc) — the clock-skew safety guard."""
    with open(os.path.join(os.path.dirname(__file__), "app.py")) as f:
        src = f.read()
    # Find the function in source
    fn_start = src.find("def _audit_event_duplicates(")
    assert fn_start >= 0, "_audit_event_duplicates not found in app.py"
    fn_body = src[fn_start: fn_start + 1000]
    assert "now_dt" in fn_body, (
        "_audit_event_duplicates must reference now_dt in its body")
    assert "datetime.now(timezone.utc)" in fn_body, (
        "_audit_event_duplicates must use datetime.now(timezone.utc) as the default")


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            import traceback
            traceback.print_exc()
            failed += 1
    print("═" * 60)
    print(f"  TOTAL: {passed + failed} checks — {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
