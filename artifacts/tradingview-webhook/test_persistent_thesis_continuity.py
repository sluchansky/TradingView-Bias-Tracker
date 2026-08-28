"""Regression contract for mode-scoped, evidence-idempotent market theses."""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402


def _clear():
    with app.THESIS_LOCK:
        app.THESIS_BY_INST.clear()
        app.THESIS_TIMELINE_BY_INST.clear()


def _strict(
    evidence_id,
    *,
    score=80,
    direction="Long",
    ready=True,
    zone_valid=True,
    structure_state=None,
    missing=None,
    zone_broken=False,
):
    return {
        "evidence_id": evidence_id,
        "score": score,
        "direction": direction,
        "candidate": direction,
        "missing": missing or [],
        "zone_broken_active": zone_broken,
        "structure_state": structure_state or {
            "state": "TREND_CONFIRMED",
            "direction": direction,
        },
        "gate_debug": {
            "zone_valid": zone_valid,
            "vwap_confirmed": True,
            "structure_confirmed": True,
            "sweep_confirmed": False,
            "volume_confirmed": True,
            "session": True,
        },
        "_expected_ready": ready,
    }


def _apply(inst, mode, strict):
    verdict = f"{str(strict.get('direction')).upper()} READY" if strict.pop("_expected_ready") else "WAIT"
    return app._apply_thesis(inst, strict, verdict, mode=mode)


def test_identical_heartbeat_is_a_true_noop():
    _clear()
    strict = _strict("same-bar")
    _, first = _apply("MNQ", "SCALP", dict(strict))
    timeline_before = list(app.THESIS_TIMELINE_BY_INST["MNQ|SCALP"])
    _, second = _apply("MNQ", "SCALP", dict(strict))
    timeline_after = list(app.THESIS_TIMELINE_BY_INST["MNQ|SCALP"])
    assert second == first
    assert timeline_after == timeline_before


def test_instrument_and_mode_are_both_isolated():
    _clear()
    _, scalp = _apply("MNQ", "SCALP", _strict("mnq-scalp", direction="Long"))
    _, intraday = _apply(
        "MNQ", "INTRADAY_TREND",
        _strict("mnq-it", direction="Short", zone_valid=True),
    )
    _, mes = _apply("MES", "SCALP", _strict("mes-scalp", direction="Short"))
    assert scalp["direction"] == "Long"
    assert intraday["direction"] == "Short"
    assert mes["direction"] == "Short"
    assert len({scalp["thesisId"], intraday["thesisId"], mes["thesisId"]}) == 3
    assert set(app.THESIS_BY_INST) == {
        "MNQ|SCALP", "MNQ|INTRADAY_TREND", "MES|SCALP",
    }


def test_explicit_zone_invalidation_demotes_ready():
    _clear()
    _apply("MGC", "INTRADAY_TREND", _strict("healthy", zone_valid=True))
    broken = _strict(
        "zone-consumed", zone_valid=False, zone_broken=True, missing=["zone_valid"])
    verdict, snap = _apply("MGC", "INTRADAY_TREND", broken)
    assert verdict == "WAIT"
    assert snap["status"] == "INVALIDATED"
    assert snap["invalidationReason"] == "Zone consumed"


def test_reversal_requires_pending_then_distinct_structure_confirmation():
    _clear()
    _, original = _apply("MNQ", "SCALP", _strict("long", direction="Long"))
    verdict1, pending = _apply(
        "MNQ", "SCALP", _strict("short-candidate", score=90, direction="Short"))
    assert verdict1 == "WAIT"
    assert pending["status"] == "PENDING_REVERSAL"
    assert pending["direction"] == "Long"

    confirmed = _strict(
        "short-confirmed",
        score=90,
        direction="Short",
        structure_state={"state": "REVERSAL_CONFIRMED", "direction": "Short"},
    )
    verdict2, replacement = _apply("MNQ", "SCALP", confirmed)
    assert verdict2 == "WAIT"
    assert replacement["status"] == "FORMING"
    assert replacement["direction"] == "Short"
    assert replacement["replacesThesisId"] == original["thesisId"]
    assert replacement["replacedThesisFinalStatus"] == "INVALIDATED"


def test_strict_wait_never_becomes_ready_but_strict_ready_passes():
    _clear()
    ready_verdict, confirmed = _apply("MYM", "SCALP", _strict("ready"))
    assert ready_verdict == "LONG READY"
    assert confirmed["status"] == "CONFIRMED"

    waiting = _strict("new-wait", score=74, ready=False)
    wait_verdict, weakening = _apply("MYM", "SCALP", waiting)
    assert wait_verdict == "WAIT"
    assert weakening["status"] == "WEAKENING"
    assert weakening["entryStatus"] == "WAIT"


def test_restart_restores_stale_rows_for_each_mode(monkeypatch):
    _clear()
    stale_at = app.now_utc() - timedelta(hours=8)
    rows = [
        ("MNQ", "SCALP", {
            "thesisId": "th_scalp",
            "instrument": "MNQ",
            "mode": "SCALP",
            "direction": "Long",
            "status": "WEAKENING",
            "confidence": 64,
            "createdAt": stale_at.isoformat(),
        }, stale_at),
        ("MNQ", "INTRADAY_TREND", {
            "thesisId": "th_it",
            "instrument": "MNQ",
            "mode": "INTRADAY_TREND",
            "direction": "Short",
            "status": "FORMING",
            "confidence": 58,
            "createdAt": stale_at.isoformat(),
        }, stale_at),
    ]

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql):
            return None

        def fetchall(self):
            return rows

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(app, "THESIS_DB_READY", True)
    monkeypatch.setattr(app, "get_db_connection", lambda: Connection())
    app._restore_thesis_states()

    scalp = app.get_thesis_snapshot("MNQ", "SCALP")
    intraday = app.get_thesis_snapshot("MNQ", "INTRADAY_TREND")
    assert scalp["thesisId"] == "th_scalp"
    assert intraday["thesisId"] == "th_it"
    assert scalp["evidenceStaleOnRestore"] is True
    assert intraday["evidenceStaleOnRestore"] is True