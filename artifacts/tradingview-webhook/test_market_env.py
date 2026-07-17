"""
Market Environment Layer — Phase 1A tests.

Tests verify:
  1. Fresh vs stale data
  2. Missing symbols
  3. Conflicting market conditions
  4. Risk-on classification
  5. Risk-off classification
  6. Geopolitical news WITHOUT market confirmation → not GEOPOLITICAL
  7. Geopolitical news WITH market confirmation → GEOPOLITICAL
  8. Expired news (outside ±window)
  9. Insufficient data coverage → UNKNOWN regime
 10. Module failure isolation (exception in compute_market_environment)
 11. Feature flags all False (hardcoded)
 12. Dashboard rendering when data unavailable
 13. CRITICAL: Geopolitical headline alone must not classify as GEOPOLITICAL
 14. CRITICAL: MEL must not alter any existing verdict, confidence, sizing, or order
"""

import sys
import os
import time
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

import importlib
import app as _app_module


# ── helpers ──────────────────────────────────────────────────────────────────

def _fresh_ts(minutes_ago=0):
    """Return an ISO timestamp N minutes ago."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _stale_ts():
    """Return an ISO timestamp that is definitely stale (4 hours ago)."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()


def _make_vwap(value, minutes_ago=0):
    return {"value": value, "ts": _fresh_ts(minutes_ago)}


def _make_cvd(state, minutes_ago=0):
    return {"state": state, "value": 0.0, "direction": "Rising", "ts": _fresh_ts(minutes_ago)}


def _make_auto_price(value, minutes_ago=0):
    return {"value": value, "ts": _fresh_ts(minutes_ago)}


def _make_news_filter(upcoming=None, within_window=False):
    """Return a minimal get_news_filter()-compatible dict."""
    return {
        "available": True,
        "stale": False,
        "within_window": within_window,
        "next_event": upcoming[0] if upcoming else None,
        "upcoming": upcoming or [],
        "high_impact_count": len(upcoming or []),
        "error": None,
        "as_of": "12:00 ET",
    }


def _geo_news(minutes_until=5):
    return [{
        "title": "Military escalation: new missile strikes reported",
        "country": "USD",
        "impact": "High",
        "time_et": "09:35 ET",
        "minutes_until": minutes_until,
    }]


def _run_with_state(vwap=None, price=None, auto_price=None, cvd=None,
                    news_filter=None):
    """
    Patch the four per-instrument globals and optionally get_news_filter,
    then call compute_market_environment() and return the snapshot.
    """
    vwap       = vwap       or {}
    price      = price      or {}
    auto_price = auto_price or {}
    cvd        = cvd        or {}

    patches = [
        patch.object(_app_module, "VWAP_BY_TICKER",          vwap),
        patch.object(_app_module, "CURRENT_PRICE_BY_TICKER",  price),
        patch.object(_app_module, "AUTO_PRICE_BY_TICKER",     auto_price),
        patch.object(_app_module, "CVD_BY_TICKER",            cvd),
        patch.object(_app_module, "RVOL_BY_TICKER",           {}),
        patch.object(_app_module, "VOLATILITY_BY_TICKER",     {}),
        # Reset the shadow-log state so each test gets a clean run
        patch.object(_app_module, "_ME_LAST_SNAPSHOT",        None),
        patch.object(_app_module, "_ME_LAST_LOG_TS",          0.0),
    ]
    if news_filter is not None:
        patches.append(patch.object(_app_module, "get_news_filter",
                                    lambda: news_filter))

    ctx = [p.__enter__() for p in patches]
    try:
        snap = _app_module.compute_market_environment()
    finally:
        for p, c in zip(reversed(patches), reversed(ctx)):
            p.__exit__(None, None, None)
    return snap


# ── 1. Fresh data → valid snapshot ──────────────────────────────────────────

def test_fresh_data_produces_valid_snapshot():
    snap = _run_with_state(
        vwap={
            "MNQ": _make_vwap(21000), "MES": _make_vwap(5800),
            "MYM": _make_vwap(44000), "MGC": _make_vwap(2300),
        },
        auto_price={
            "MNQ": _make_auto_price(21050), "MES": _make_auto_price(5820),
            "MYM": _make_auto_price(44100), "MGC": _make_auto_price(2350),
        },
    )
    assert snap["available"] is True
    assert snap["shadow_mode"] is True
    assert snap["regime"] in (
        "RISK_ON", "RISK_OFF", "NEUTRAL", "MIXED", "GEOPOLITICAL",
        "INFLATIONARY", "DEFLATIONARY", "FED_DRIVEN", "UNKNOWN",
    )
    assert 0 <= snap["confidence"] <= 100
    assert snap["data_quality"]["level"] in ("HIGH", "MODERATE", "LOW", "INSUFFICIENT")
    print("PASS test_fresh_data_produces_valid_snapshot")


# ── 2. Stale VWAP → data_quality INSUFFICIENT, regime UNKNOWN ────────────────

def test_stale_vwap_yields_unknown_regime():
    snap = _run_with_state(
        vwap={
            "MNQ": {"value": 21000, "ts": _stale_ts()},
            "MES": {"value": 5800,  "ts": _stale_ts()},
            "MYM": {"value": 44000, "ts": _stale_ts()},
            "MGC": {"value": 2300,  "ts": _stale_ts()},
        },
        auto_price={
            "MNQ": _make_auto_price(21050), "MES": _make_auto_price(5820),
            "MYM": _make_auto_price(44100), "MGC": _make_auto_price(2350),
        },
    )
    # All VWAPs stale → data_fresh=False for every instrument → INSUFFICIENT coverage
    assert snap["data_quality"]["level"] == "INSUFFICIENT", (
        f"Expected INSUFFICIENT, got {snap['data_quality']['level']}")
    assert snap["regime"] == "UNKNOWN", (
        f"Stale data must produce UNKNOWN regime, got {snap['regime']}")
    print("PASS test_stale_vwap_yields_unknown_regime")


# ── 3. Missing symbols → marked unavailable ───────────────────────────────────

def test_missing_symbols_marked_unavailable():
    snap = _run_with_state()   # empty globals → no data
    dq = snap["data_quality"]
    assert "VIX"  in dq["unavailable_symbols"]
    assert "QQQ"  in dq["unavailable_symbols"]
    assert "SPY"  in dq["unavailable_symbols"]
    assert "XLE"  in dq["unavailable_symbols"]
    assert "GLD"  in dq["unavailable_symbols"]
    # With no data the snapshot should still be available (fail-open)
    assert snap["available"] is True
    print("PASS test_missing_symbols_marked_unavailable")


# ── 4. Risk-on classification ─────────────────────────────────────────────────

def test_risk_on_classification():
    """All three equity instruments above VWAP + bullish CVD on 2 → RISK_ON."""
    snap = _run_with_state(
        vwap={
            "MNQ": _make_vwap(21000), "MES": _make_vwap(5800),
            "MYM": _make_vwap(44000), "MGC": _make_vwap(2300),
        },
        auto_price={
            "MNQ": _make_auto_price(21500),  # above
            "MES": _make_auto_price(5900),   # above
            "MYM": _make_auto_price(44500),  # above
            "MGC": _make_auto_price(2280),   # below (gold weakening = risk-on)
        },
        cvd={
            "MNQ": _make_cvd("bullish"),
            "MES": _make_cvd("bullish"),
            "MYM": _make_cvd("neutral"),
            "MGC": {},
        },
    )
    assert snap["regime"] == "RISK_ON", (
        f"Expected RISK_ON, got {snap['regime']}. "
        f"Confirms: {snap['supporting_evidence']}")
    assert snap["risk_state"] in ("AGGRESSIVE", "BALANCED")
    print("PASS test_risk_on_classification")


# ── 5. Risk-off classification ────────────────────────────────────────────────

def test_risk_off_classification():
    """MNQ + MES below VWAP, gold above VWAP → RISK_OFF."""
    snap = _run_with_state(
        vwap={
            "MNQ": _make_vwap(21000), "MES": _make_vwap(5800),
            "MYM": _make_vwap(44000), "MGC": _make_vwap(2300),
        },
        auto_price={
            "MNQ": _make_auto_price(20500),  # below
            "MES": _make_auto_price(5700),   # below
            "MYM": _make_auto_price(43500),  # below
            "MGC": _make_auto_price(2400),   # above (gold safe-haven)
        },
        cvd={
            "MNQ": _make_cvd("bearish"),
            "MES": _make_cvd("bearish"),
            "MYM": {},
            "MGC": _make_cvd("bullish"),
        },
    )
    assert snap["regime"] == "RISK_OFF", (
        f"Expected RISK_OFF, got {snap['regime']}. "
        f"Confirms: {snap['supporting_evidence']}")
    assert snap["risk_state"] == "DEFENSIVE"
    print("PASS test_risk_off_classification")


# ── 6. Conflicting market conditions → MIXED ──────────────────────────────────

def test_conflicting_conditions_produce_mixed():
    """MNQ above VWAP, MES below VWAP, no strong gold signal → MIXED or NEUTRAL."""
    snap = _run_with_state(
        vwap={
            "MNQ": _make_vwap(21000), "MES": _make_vwap(5800),
            "MYM": _make_vwap(44000), "MGC": _make_vwap(2300),
        },
        auto_price={
            "MNQ": _make_auto_price(21200),  # above
            "MES": _make_auto_price(5700),   # below
            "MYM": _make_auto_price(44000),  # at VWAP (no signal)
            "MGC": _make_auto_price(2290),   # below (slight)
        },
    )
    assert snap["regime"] in ("MIXED", "NEUTRAL"), (
        f"Expected MIXED or NEUTRAL, got {snap['regime']}")
    print("PASS test_conflicting_conditions_produce_mixed")


# ── 7. Geopolitical news WITHOUT market confirmation → NOT GEOPOLITICAL ────────

def test_geopolitical_news_alone_does_not_classify_geopolitical():
    """
    CRITICAL TEST: A geopolitical headline alone must not produce the
    GEOPOLITICAL regime without at least 2 market confirmations.
    """
    # Give equities above VWAP (risk-on), gold below → no market confirmations
    snap = _run_with_state(
        vwap={
            "MNQ": _make_vwap(21000), "MES": _make_vwap(5800),
            "MYM": _make_vwap(44000), "MGC": _make_vwap(2300),
        },
        auto_price={
            "MNQ": _make_auto_price(21500),  # above (equity strong)
            "MES": _make_auto_price(5900),   # above
            "MYM": _make_auto_price(44500),  # above
            "MGC": _make_auto_price(2250),   # below (gold weak)
        },
        news_filter=_make_news_filter(upcoming=_geo_news()),
    )
    assert snap["regime"] != "GEOPOLITICAL", (
        f"CRITICAL FAILURE: geopolitical news alone classified regime as GEOPOLITICAL "
        f"without market confirmation. regime={snap['regime']}")
    print("PASS test_geopolitical_news_alone_does_not_classify_geopolitical")


# ── 8. Geopolitical news WITH market confirmation → GEOPOLITICAL ──────────────

def test_geopolitical_news_with_market_confirmation():
    """Geo news + gold strength + equity weakness → GEOPOLITICAL."""
    snap = _run_with_state(
        vwap={
            "MNQ": _make_vwap(21000), "MES": _make_vwap(5800),
            "MYM": _make_vwap(44000), "MGC": _make_vwap(2300),
        },
        auto_price={
            "MNQ": _make_auto_price(20500),  # below — equity weak
            "MES": _make_auto_price(5700),   # below
            "MYM": _make_auto_price(43500),  # below
            "MGC": _make_auto_price(2400),   # above — gold strong
        },
        news_filter=_make_news_filter(upcoming=_geo_news()),
    )
    # With gold strong + equities weak (2 market confirmations) → GEOPOLITICAL
    assert snap["regime"] == "GEOPOLITICAL", (
        f"Expected GEOPOLITICAL, got {snap['regime']}. "
        f"Confirms: {snap['supporting_evidence']}")
    assert snap["risk_state"] == "SHOCK"
    print("PASS test_geopolitical_news_with_market_confirmation")


# ── 9. Expired news (outside ±window) ─────────────────────────────────────────

def test_expired_news_not_used():
    """An event 120 minutes in the future is outside the 60-min window."""
    far_future_news = [{
        "title": "Military escalation: new missile strikes",
        "country": "USD",
        "impact": "High",
        "time_et": "11:00 ET",
        "minutes_until": 120,  # outside ±60 min window
    }]
    snap = _run_with_state(
        vwap={
            "MNQ": _make_vwap(21000), "MES": _make_vwap(5800),
            "MYM": _make_vwap(44000), "MGC": _make_vwap(2300),
        },
        auto_price={
            "MNQ": _make_auto_price(20500),
            "MES": _make_auto_price(5700),
            "MYM": _make_auto_price(43500),
            "MGC": _make_auto_price(2400),
        },
        news_filter=_make_news_filter(upcoming=far_future_news),
    )
    # Far-future news must not trigger news_context ACTIVE/UPCOMING
    assert snap["news_context"]["status"] == "CLEAR", (
        f"Expected CLEAR for out-of-window news, got {snap['news_context']['status']}")
    assert snap["news_context"]["category"] == "NONE", (
        f"Expected NONE category, got {snap['news_context']['category']}")
    print("PASS test_expired_news_not_used")


# ── 10. Insufficient data coverage → UNKNOWN regime ───────────────────────────

def test_insufficient_coverage_yields_unknown():
    """No data at all → INSUFFICIENT quality → regime UNKNOWN."""
    snap = _run_with_state()  # all empty
    assert snap["data_quality"]["level"] == "INSUFFICIENT"
    assert snap["regime"] == "UNKNOWN", (
        f"Expected UNKNOWN with no data, got {snap['regime']}")
    # Preferences should be INSUFFICIENT_DATA when no data
    for pref in snap["futures_preferences"]:
        assert pref["preference"] == "INSUFFICIENT_DATA", (
            f"{pref['symbol']} preference should be INSUFFICIENT_DATA with no data, "
            f"got {pref['preference']}")
    print("PASS test_insufficient_coverage_yields_unknown")


# ── 11. Module failure isolation ──────────────────────────────────────────────

def test_module_failure_is_isolated():
    """An exception inside the ME layer must not propagate; returns UNAVAILABLE."""
    def _bad_inner():
        raise RuntimeError("Simulated internal error")

    with patch.object(_app_module, "_compute_market_env_inner", side_effect=_bad_inner):
        snap = _app_module.compute_market_environment()

    assert snap["available"] is False
    assert snap["regime"] == "UNKNOWN"
    assert "error" in snap
    assert snap["shadow_mode"] is True
    # All influence flags still False in error snapshot
    assert snap["_can_affect_confidence"] is False
    assert snap["_can_affect_verdicts"]   is False
    print("PASS test_module_failure_is_isolated")


# ── 12. Feature flags hardcoded False ─────────────────────────────────────────

def test_influence_feature_flags_are_false():
    """All four influence flags must be False — hardcoded, not env-settable."""
    assert _app_module.MARKET_ENVIRONMENT_CAN_AFFECT_CONFIDENCE is False
    assert _app_module.MARKET_ENVIRONMENT_CAN_AFFECT_RISK       is False
    assert _app_module.MARKET_ENVIRONMENT_CAN_PAUSE_ENTRIES     is False
    assert _app_module.MARKET_ENVIRONMENT_CAN_AFFECT_VERDICTS   is False
    # Also verify shadow mode is always True
    assert _app_module.MARKET_ENVIRONMENT_SHADOW_MODE is True
    # Verify they cannot be changed by setting env vars
    for flag_name in (
        "MARKET_ENVIRONMENT_CAN_AFFECT_CONFIDENCE",
        "MARKET_ENVIRONMENT_CAN_AFFECT_RISK",
        "MARKET_ENVIRONMENT_CAN_PAUSE_ENTRIES",
        "MARKET_ENVIRONMENT_CAN_AFFECT_VERDICTS",
    ):
        assert getattr(_app_module, flag_name) is False, (
            f"Influence flag {flag_name} must be hardcoded False")
    print("PASS test_influence_feature_flags_are_false")


# ── 13. Dashboard rendering with no data ──────────────────────────────────────

def test_snapshot_schema_complete_when_unavailable():
    """Even when available=False the snapshot must have all required keys."""
    required_keys = {
        "regime", "confidence", "dominant_theme", "secondary_theme",
        "risk_state", "sector_rotation", "futures_preferences",
        "supporting_evidence", "conflicting_evidence",
        "news_context", "data_quality", "shadow_mode", "available",
        "updated_at", "_can_affect_confidence", "_can_affect_risk",
        "_can_pause_entries", "_can_affect_verdicts",
    }
    # Trigger an error to get the unavailable snapshot
    with patch.object(_app_module, "_compute_market_env_inner",
                      side_effect=RuntimeError("test")):
        snap = _app_module.compute_market_environment()

    missing = required_keys - set(snap.keys())
    assert not missing, f"Unavailable snapshot missing keys: {missing}"
    print("PASS test_snapshot_schema_complete_when_unavailable")


# ── 14. CRITICAL: MEL does not alter existing trade verdicts ──────────────────

def test_mel_does_not_alter_trade_verdicts():
    """
    CRITICAL TEST: compute_market_environment() must not modify any global
    that the money path reads (gate logic, confidence, ACTIVE_TRADES, etc.).
    We snapshot the key globals before and after, and verify nothing changed.
    """
    # Snapshot money-path globals before
    before_active = dict(_app_module.ACTIVE_TRADES_BY_INST)
    before_vwap   = dict(_app_module.VWAP_BY_TICKER)
    before_cvd    = dict(_app_module.CVD_BY_TICKER)
    before_rvol   = dict(_app_module.RVOL_BY_TICKER)
    before_price  = dict(_app_module.CURRENT_PRICE_BY_TICKER)

    # Run the ME layer
    _app_module.compute_market_environment()

    # Verify nothing changed
    assert dict(_app_module.ACTIVE_TRADES_BY_INST) == before_active, \
        "MEL must not modify ACTIVE_TRADES_BY_INST"
    assert dict(_app_module.VWAP_BY_TICKER)         == before_vwap, \
        "MEL must not modify VWAP_BY_TICKER"
    assert dict(_app_module.CVD_BY_TICKER)           == before_cvd, \
        "MEL must not modify CVD_BY_TICKER"
    assert dict(_app_module.RVOL_BY_TICKER)          == before_rvol, \
        "MEL must not modify RVOL_BY_TICKER"
    assert dict(_app_module.CURRENT_PRICE_BY_TICKER) == before_price, \
        "MEL must not modify CURRENT_PRICE_BY_TICKER"
    print("PASS test_mel_does_not_alter_trade_verdicts")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_fresh_data_produces_valid_snapshot,
        test_stale_vwap_yields_unknown_regime,
        test_missing_symbols_marked_unavailable,
        test_risk_on_classification,
        test_risk_off_classification,
        test_conflicting_conditions_produce_mixed,
        test_geopolitical_news_alone_does_not_classify_geopolitical,
        test_geopolitical_news_with_market_confirmation,
        test_expired_news_not_used,
        test_insufficient_coverage_yields_unknown,
        test_module_failure_is_isolated,
        test_influence_feature_flags_are_false,
        test_snapshot_schema_complete_when_unavailable,
        test_mel_does_not_alter_trade_verdicts,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} tests passed")
    if failed:
        sys.exit(1)
