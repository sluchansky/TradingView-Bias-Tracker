"""Confidence Integrity Fix — Phase 1 tests.

Asserts:
  A) The integer edge_score passed into build_strict_trade_plan equals
     result["edge_score"] after full_analysis returns.
  B) full_analysis(ticker_override=inst) returns result["active_ticker"] == inst
     for each of MNQ, MES, MYM, MGC.
  C) The internal result["confidence"] field still exists and is a non-negative integer.
  D) EDGE_COMPONENTS weights match known values:
     BOS=20, CHOCH=20, VWAP=15, Sweep=15, Volume=15, CVD=15, Session=10.
  E) /status payload exposes legacy_confidence (not confidence) + legacy_confidence_valid=False.
  F) /status payload exposes edge_score_source, edge_score_instrument,
     edge_score_generated_at, edge_score_components.

Runnable two ways:
  pytest test_confidence_integrity.py
  python3 test_confidence_integrity.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("TRAINING_MODE_ENABLED", "0")
os.environ.setdefault("DECISION_PIPELINE_V2_ENABLED", "0")

import app  # noqa: E402


# ── Test D: EDGE_COMPONENTS weights ──────────────────────────────────────────

def test_edge_components_weights_unchanged():
    """D) No EDGE_COMPONENTS weights changed."""
    expected = [
        ("bos_confirmed",   "BOS Confirmed",       20),
        ("choch_confirmed", "CHOCH Confirmed",      20),
        ("vwap_confirmed",  "VWAP Confirmation",    15),
        ("liquidity_sweep", "Liquidity Sweep",      15),
        ("volume_confirmed","Volume Confirmation",  15),
        ("cvd_confirmed",   "CVD Agreement",        15),
        ("preferred_session","Session Bonus",       10),
    ]
    assert list(app.EDGE_COMPONENTS) == expected, (
        "EDGE_COMPONENTS weights changed — expected %r, got %r" % (expected, list(app.EDGE_COMPONENTS))
    )
    assert app.EDGE_SCORE_MAX == 110


# ── Test C: result["confidence"] still present ────────────────────────────────

def test_result_confidence_field_exists():
    """C) Internal result["confidence"] still exists and is a non-negative integer."""
    result = app.full_analysis()
    assert "confidence" in result, "result['confidence'] key missing"
    conf = result["confidence"]
    assert isinstance(conf, int), "confidence must be an int, got %s" % type(conf)
    assert conf >= 0, "confidence must be non-negative, got %d" % conf


# ── Test A: edge_score parity between build_strict_trade_plan and result ──────

_CAPTURED_EDGE_SCORE_ARG = []

def _make_capturing_wrapper(original_fn):
    """Wrap build_strict_trade_plan to capture the edge_score kwarg."""
    def wrapper(*args, **kwargs):
        _CAPTURED_EDGE_SCORE_ARG.append(kwargs.get("edge_score"))
        return original_fn(*args, **kwargs)
    return wrapper


def test_build_strict_trade_plan_receives_authoritative_edge_score():
    """A) The integer edge_score passed into build_strict_trade_plan equals result["edge_score"]."""
    _CAPTURED_EDGE_SCORE_ARG.clear()
    original = app.build_strict_trade_plan
    app.build_strict_trade_plan = _make_capturing_wrapper(original)
    try:
        result = app.full_analysis()
    finally:
        app.build_strict_trade_plan = original

    if not _CAPTURED_EDGE_SCORE_ARG:
        import warnings
        warnings.warn(
            "build_strict_trade_plan was not called (no actionable setup) — "
            "edge_score parity check skipped; verifying result has edge_score key."
        )
        assert "edge_score" in result
        return

    captured = _CAPTURED_EDGE_SCORE_ARG[0]
    final = result["edge_score"]
    assert captured == final, (
        "edge_score mismatch: build_strict_trade_plan received %r, "
        "result['edge_score'] is %r" % (captured, final)
    )


# ── Test B: active_ticker per instrument ──────────────────────────────────────

def test_active_ticker_per_instrument():
    """B) full_analysis(ticker_override=inst) returns result['active_ticker'] == inst."""
    for inst in ("MNQ", "MES", "MYM", "MGC"):
        result = app.full_analysis(ticker_override=inst)
        got = result.get("active_ticker")
        assert got == inst, (
            "ticker_override=%r → active_ticker=%r (expected %r)" % (inst, got, inst)
        )


# ── Test E: /status payload keys ──────────────────────────────────────────────

def test_status_payload_legacy_confidence():
    """E) _build_status_payload exposes legacy_confidence (not confidence) and legacy_confidence_valid=False."""
    payload = app._build_status_payload(None)
    assert "confidence" not in payload, (
        "payload must NOT contain 'confidence'; found %r" % payload.get("confidence")
    )
    assert "legacy_confidence" in payload, "payload missing 'legacy_confidence'"
    assert payload["legacy_confidence_valid"] is False, (
        "legacy_confidence_valid must be False, got %r" % payload.get("legacy_confidence_valid")
    )


# ── Test F: edge-score trace keys in /status payload ─────────────────────────

def test_status_payload_edge_score_trace():
    """F) _build_status_payload exposes all four edge_score trace fields."""
    payload = app._build_status_payload(None)
    assert payload.get("edge_score_source") == "EDGE_COMPONENTS", (
        "edge_score_source wrong: %r" % payload.get("edge_score_source")
    )
    assert "edge_score_instrument" in payload, "edge_score_instrument missing"
    assert "edge_score_generated_at" in payload, "edge_score_generated_at missing"
    ts = payload["edge_score_generated_at"]
    assert isinstance(ts, str) and len(ts) > 0, "edge_score_generated_at must be a non-empty string"
    assert "edge_score_components" in payload, "edge_score_components missing"


def test_status_payload_edge_score_instrument_matches_ticker():
    """edge_score_instrument reflects the requested ticker."""
    for inst in ("MNQ", "MGC"):
        payload = app._build_status_payload(inst)
        assert payload.get("edge_score_instrument") == inst, (
            "ticker=%r → edge_score_instrument=%r" % (inst, payload.get("edge_score_instrument"))
        )


# ── built-in runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print("PASS  %s" % name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FAIL  %s: %s: %s" % (name, type(exc).__name__, exc))
    print("\n%d passed, %d failed of %d" % (passed, failed, len(tests)))
    sys.exit(1 if failed else 0)
