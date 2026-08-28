"""MES SCALP diagnostic-integrity regressions.

Display/observability only: no database, network, scheduler, alert, or execution
path is started by these tests.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402


def _mes_short_alerts():
    ts = app.now_utc().isoformat()
    return [
        {"alert_type": "BOS SUPPLY", "instrument": "MES", "ticker": "MES", "timestamp": ts},
        {"alert_type": "CHOCH SUPPLY", "instrument": "MES", "ticker": "MES", "timestamp": ts},
        {"alert_type": "MES BEARISH SWEEP", "instrument": "MES", "ticker": "MES", "timestamp": ts},
    ]


def test_mes_scalp_strict_gate_does_not_require_zone():
    saved_mode = app.TRADING_MODE
    app.TRADING_MODE = "SCALP"
    try:
        result = app.evaluate_strict_setup(
            current_price=5990.0,
            ticker="MES",
            vwap=6000.0,
            vwap_status="ok",
            nearest_supply=6010.0,
            nearest_demand=5980.0,
            bullish=0,
            bearish=2,
            confidence=50,
            alert_history=_mes_short_alerts(),
            volatility={"status": "ok", "atr_pts": 10.0, "regime": "NORMAL",
                        "label": "Normal", "ratio": 1.0},
            session={"preferred": False},
            cooldown_active=False,
        )["directions"]["Short"]
    finally:
        app.TRADING_MODE = saved_mode

    gd = result["gate_debug"]
    assert gd["require_zone"] is False
    assert gd["zone_valid"] is False
    assert "zone_valid" not in gd["failed_conditions"]
    # This fixture has Edge 50 under the state-aware structure allocation, so it
    # correctly remains WAIT for unrelated strict requirements.
    assert result["ready"] is False
    assert gd["edge_score"] == 50


def test_mes_structure_is_instrument_isolated():
    saved_mode = app.TRADING_MODE
    app.TRADING_MODE = "SCALP"
    try:
        result = app.evaluate_strict_setup(
            current_price=5990.0,
            ticker="MES",
            vwap=6000.0,
            vwap_status="ok",
            nearest_supply=6010.0,
            nearest_demand=5980.0,
            bullish=0,
            bearish=2,
            confidence=50,
            alert_history=[
                {**row, "instrument": "MNQ", "ticker": "MNQ"}
                for row in _mes_short_alerts()
            ],
            volatility={"status": "ok", "atr_pts": 10.0, "regime": "NORMAL",
                        "label": "Normal", "ratio": 1.0},
            session={"preferred": False},
            cooldown_active=False,
        )["directions"]["Short"]
    finally:
        app.TRADING_MODE = saved_mode

    assert result["gate_debug"]["structure_confirmed"] is False
    assert "structure_confirmed" in result["gate_debug"]["failed_conditions"]
    assert result["ready"] is False


def test_eval_window_separates_heartbeats_webhooks_and_candidates():
    rows = [
        {"trigger": "heartbeat", "instrument": "MES", "direction": "Short",
         "verdict": "WAIT", "marketOpen": True},
        {"trigger": "heartbeat", "instrument": "MES", "direction": "Short",
         "verdict": "WAIT", "marketOpen": True},
        {"trigger": "webhook", "instrument": "MES", "direction": "Short",
         "verdict": "WAIT", "isDuplicate": False, "marketOpen": True},
        {"trigger": "webhook", "instrument": "MES", "direction": "Short",
         "verdict": "SHORT READY", "isDuplicate": True, "marketOpen": True},
        {"trigger": "heartbeat", "instrument": "MNQ", "direction": None,
         "verdict": "MARKET CLOSED", "marketOpen": False},
    ]

    summary = app._eval_metrics_scope_summary(rows)
    assert summary["recorded_evaluations"] == 5
    assert summary["heartbeat_evaluations"] == 3
    assert summary["webhook_evaluations"] == 2
    assert summary["nonduplicate_webhook_evaluations"] == 1
    assert summary["duplicate_webhook_evaluations"] == 1
    assert summary["candidate_evaluations"] == 4
    assert summary["actionable_evaluations"] == 1
    assert summary["market_closed_rows"] == 1
    assert summary["by_instrument"]["MES"]["recorded_evaluations"] == 4
    assert summary["by_instrument"]["MES"]["heartbeat_evaluations"] == 2


def test_hard_blocker_counter_excludes_non_required_zone_context():
    saved_counters = {}
    with app.COUNTERS_LOCK:
        for key, value in app.COUNTERS.items():
            saved_counters[key] = dict(value) if isinstance(value, dict) else value
        for key, value in app.COUNTERS.items():
            app.COUNTERS[key] = {} if isinstance(value, dict) else 0

    now = datetime.now(timezone.utc)
    analysis = {
        "verdict": "WAIT",
        "market_open": True,
        "strict_reason": "Waiting for structure and edge.",
        "gate_debug": {
            "candidate": "Short",
            "require_zone": False,
            "zone_valid": False,
            "zoneValid": True,
            "location_ok": False,
            "require_location": False,
            "vwap_confirmed": True,
            "structure_confirmed": False,
            "volume_ok": True,
            "edge_ok": False,
            "edge_score": 20,
            "ready_threshold": 60,
            "failed_conditions": ["structure_confirmed", "edge_score(20<60)"],
        },
    }
    try:
        app._record_eval_metrics(
            analysis, now, now, now, 1.0, None, "MES",
            trigger="webhook", signal_type="MES BEARISH SWEEP",
            is_duplicate=False,
        )
        with app.COUNTERS_LOCK:
            hard = dict(app.COUNTERS["hard_blocker_reasons"])
            raw = dict(app.COUNTERS["rejection_reasons"])
        assert hard == {"structure_confirmed": 1, "edge_score_low": 1}
        assert "zone_valid" not in hard
        assert "location" not in hard
        assert raw["zone_valid"] == 1
        assert raw["location"] == 1
    finally:
        with app.COUNTERS_LOCK:
            app.COUNTERS.clear()
            app.COUNTERS.update(saved_counters)
        with app.EVAL_METRICS_LOCK:
            app.EVAL_METRICS.clear()


def test_diagnostics_copy_distinguishes_hard_blockers_from_raw_context():
    html = app.DIAGNOSTICS_LIVE_HTML
    assert "Hard blockers (non-duplicate webhooks)" in html
    assert "WAIT observations (heartbeat-inclusive)" in html
    assert "Raw context gaps (not necessarily blockers)" in html
    assert "completed-bar opportunity IDs are not tracked here" in html