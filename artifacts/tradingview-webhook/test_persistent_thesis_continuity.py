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
    evidence_epoch,
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
        "evidence_epoch": evidence_epoch,
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
    timeline_before = list(app.THESIS_TIMELINE_BY_INST[("MNQ", "SCALP")])
    _, second = _apply("MNQ", "SCALP", dict(strict))
    timeline_after = list(app.THESIS_TIMELINE_BY_INST[("MNQ", "SCALP")])
    assert second["thesisId"] == first["thesisId"]
    assert second["confidence"] == first["confidence"]
    assert second["status"] == first["status"]
    assert second["evidenceEpoch"] == first["evidenceEpoch"]
    assert timeline_after == timeline_before


def test_mode_scoped_store_update_preserves_canonical_keys():
    store = app._ModeScopedThesisStore()
    store.update({
        ("MNQ", "SCALP"): {"thesisId": "scalp"},
        ("MNQ", "SWING"): {"thesisId": "intraday"},
    })
    assert store.get(("MNQ", "SCALP"))["thesisId"] == "scalp"
    assert store.get(("MNQ", "INTRADAY_TREND"))["thesisId"] == "intraday"
    assert ("MNQ", "SWING") not in set(store)


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
        ("MNQ", "SCALP"), ("MNQ", "INTRADAY_TREND"), ("MES", "SCALP"),
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


def test_reversal_requires_confirmed_structure_then_newer_entry_epoch():
    _clear()
    _, original = _apply("MNQ", "SCALP", _strict("long", direction="Long"))
    verdict1, pending = _apply(
        "MNQ", "SCALP", _strict("short-candidate", score=90, direction="Short"))
    assert verdict1 == "WAIT"
    assert pending["status"] == "READY_LONG"
    assert pending["direction"] == "Long"
    assert pending["entryStatus"] == "WAIT"

    confirmed = _strict(
        "short-confirmed",
        score=90,
        direction="Short",
        structure_state={
            "state": "REVERSAL_CONFIRMED",
            "confirmed": True,
            "direction": "Short",
            "last_event_at": "2026-08-28T12:01:00+00:00",
        },
    )
    verdict2, replacement = _apply("MNQ", "SCALP", confirmed)
    assert verdict2 == "WAIT"
    assert replacement["status"] == "FORMING_SHORT"
    assert replacement["direction"] == "Short"
    assert replacement["thesisId"] != original["thesisId"]
    assert replacement["entryStatus"] == "WAIT"
    assert replacement["reversalDetectedEpoch"] == "short-confirmed"
    assert replacement["replacesThesisId"] == original["thesisId"]
    timeline = list(app.THESIS_TIMELINE_BY_INST[("MNQ", "SCALP")])
    assert [event["newStatus"] for event in timeline[:2]] == [
        "FORMING_SHORT", "INVALIDATED",
    ]
    assert timeline[0]["previousThesisId"] == original["thesisId"]
    assert timeline[1]["thesisId"] == original["thesisId"]
    assert timeline[1]["invalidationReason"] == "Confirmed opposite structure"

    later = _strict(
        "short-next-bar",
        score=90,
        direction="Short",
        structure_state={
            "state": "TREND_CONFIRMED",
            "confirmed": True,
            "direction": "Short",
            "last_event_at": "2026-08-28T12:02:00+00:00",
        },
    )
    verdict3, ready = _apply("MNQ", "SCALP", later)
    assert verdict3 == "SHORT READY"
    assert ready["status"] == "READY_SHORT"
    assert ready["entryStatus"] == "READY"


def test_strict_wait_never_becomes_ready_but_strict_ready_passes():
    _clear()
    ready_verdict, confirmed = _apply("MYM", "SCALP", _strict("ready"))
    assert ready_verdict == "LONG READY"
    assert confirmed["status"] == "READY_LONG"
    assert confirmed["entryStatus"] == "READY"

    waiting = _strict("new-wait", score=74, ready=False)
    wait_verdict, weakening = _apply("MYM", "SCALP", waiting)
    assert wait_verdict == "WAIT"
    assert weakening["status"] == "READY_LONG"
    assert weakening["entryPaused"] is True
    assert weakening["entryStatus"] == "WAIT"


def test_restart_restores_recent_rows_for_each_mode(monkeypatch):
    _clear()
    restored_at = app.now_utc() - timedelta(minutes=5)
    rows = [
        ("MNQ", "SCALP", {
            "thesisId": "th_scalp",
            "instrument": "MNQ",
            "mode": "SCALP",
            "direction": "Long",
            "status": "READY_LONG",
            "confidence": 64,
            "entryStatus": "READY",
            "entryPaused": False,
            "createdAt": restored_at.isoformat(),
        }, restored_at),
        ("MNQ", "SWING", {
            "thesisId": "th_it",
            "instrument": "MNQ",
            "mode": "SWING",
            "direction": "Short",
            "status": "FORMING_SHORT",
            "confidence": 58,
            "createdAt": restored_at.isoformat(),
        }, restored_at),
    ]

    class Cursor:
        query = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql):
            self.query = sql

        def fetchall(self):
            if "hysteresis_thesis_events" in self.query:
                return []
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
    assert scalp["mode"] == "SCALP"
    assert intraday["mode"] == "INTRADAY_TREND"
    assert scalp["entryStatus"] == "WAIT"
    assert intraday["entryStatus"] == "WAIT"
    assert scalp["entryPaused"] is True
    assert scalp["restoredAwaitingFreshEvaluation"] is True
    assert app._evaluate_thesis_alignment(scalp, "Long") == "NO_THESIS"

    verdict, refreshed = _apply(
        "MNQ", "SCALP", _strict("post-restart-fresh", direction="Long")
    )
    assert verdict == "LONG READY"
    assert refreshed["entryStatus"] == "READY"
    assert refreshed["entryPaused"] is False
    assert refreshed["restoredAwaitingFreshEvaluation"] is False


def test_restart_rehydrates_bounded_mode_scoped_history(monkeypatch):
    _clear()
    restored_at = app.now_utc() - timedelta(minutes=3)
    snapshots = [
        ("MNQ", "SCALP", {
            "thesisId": "th_scalp_new",
            "instrument": "MNQ",
            "mode": "SCALP",
            "direction": "Short",
            "status": "FORMING_SHORT",
            "confidence": 88,
            "createdAt": restored_at.isoformat(),
        }, restored_at),
        ("MNQ", "INTRADAY_TREND", {
            "thesisId": "th_it",
            "instrument": "MNQ",
            "mode": "INTRADAY_TREND",
            "direction": "Long",
            "status": "READY_LONG",
            "confidence": 82,
            "createdAt": restored_at.isoformat(),
        }, restored_at),
    ]
    events = [
        ("MNQ", "SCALP", {
            "eventId": "forming",
            "ts": restored_at.isoformat(),
            "mode": "SCALP",
            "previousThesisId": "th_scalp_old",
            "thesisId": "th_scalp_new",
            "prevStatus": "INVALIDATED",
            "newStatus": "FORMING_SHORT",
            "transitionIndex": 1,
        }),
        ("MNQ", "SCALP", {
            "eventId": "invalidated",
            "ts": restored_at.isoformat(),
            "mode": "SCALP",
            "previousThesisId": "th_scalp_old",
            "thesisId": "th_scalp_old",
            "prevStatus": "READY_LONG",
            "newStatus": "INVALIDATED",
            "transitionIndex": 0,
        }),
        ("MNQ", "INTRADAY_TREND", {
            "eventId": "it-ready",
            "ts": restored_at.isoformat(),
            "mode": "INTRADAY_TREND",
            "thesisId": "th_it",
            "prevStatus": "FORMING_LONG",
            "newStatus": "READY_LONG",
            "transitionIndex": 0,
        }),
    ]

    class Cursor:
        query = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql):
            self.query = sql

        def fetchall(self):
            if "hysteresis_thesis_events" in self.query:
                return events
            return snapshots

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(app, "THESIS_DB_READY", True)
    monkeypatch.setattr(app, "get_db_connection", lambda: Connection())
    app._restore_thesis_states()

    scalp_history = list(
        app.THESIS_TIMELINE_BY_INST[("MNQ", "SCALP")]
    )
    it_history = list(
        app.THESIS_TIMELINE_BY_INST[("MNQ", "INTRADAY_TREND")]
    )
    assert [event["eventId"] for event in scalp_history] == [
        "forming", "invalidated",
    ]
    assert [event["eventId"] for event in it_history] == ["it-ready"]
    assert app.get_thesis_snapshot("MNQ", "SCALP")["thesisId"] == "th_scalp_new"
    assert app.get_thesis_snapshot(
        "MNQ", "INTRADAY_TREND"
    )["thesisId"] == "th_it"


def test_swing_aliases_the_intraday_thesis():
    _clear()
    _, swing = _apply("MGC", "SWING", _strict("swing-bar", direction="Long"))
    intraday = app.get_thesis_snapshot("MGC", "INTRADAY_TREND")
    assert swing["mode"] == "INTRADAY_TREND"
    assert intraday["thesisId"] == swing["thesisId"]
    assert ("MGC", "SWING") not in set(app.THESIS_BY_INST)
    assert ("MGC", "INTRADAY_TREND") in set(app.THESIS_BY_INST)


def test_swing_persistence_writes_the_canonical_mode(monkeypatch):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            calls.append((sql, params))

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(app, "THESIS_DB_READY", True)
    monkeypatch.setattr(app, "get_db_connection", lambda: Connection())
    snap = {
        "thesisId": "th_swing",
        "instrument": "MGC",
        "mode": "SWING",
        "direction": "Long",
        "status": "READY_LONG",
        "confidence": 82,
    }
    app._persist_thesis_state("MGC", snap, mode="SWING")
    assert calls
    assert calls[0][1][1] == "INTRADAY_TREND"


def test_transition_persistence_is_append_only_and_mode_scoped(monkeypatch):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            calls.append((sql, params))

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(app, "THESIS_DB_READY", True)
    monkeypatch.setattr(app, "get_db_connection", lambda: Connection())
    event = {
        "eventId": "thev_test",
        "ts": app.now_utc().isoformat(),
        "mode": "SWING",
        "previousThesisId": "th_old",
        "thesisId": "th_new",
        "prevStatus": "INVALIDATED",
        "newStatus": "FORMING_SHORT",
        "evidenceEpoch": "bar-2",
        "transitionIndex": 1,
    }
    app._persist_thesis_event("MNQ", event)
    assert len(calls) == 1
    sql, params = calls[0]
    assert "ON CONFLICT (event_id) DO NOTHING" in sql
    assert params[2] == "INTRADAY_TREND"
    assert params[4] == "th_old"
    assert params[8] == 1


def test_event_identity_ignores_processing_time_for_replay():
    _clear()
    previous = {
        "thesisId": "th_old",
        "mode": "SCALP",
        "direction": "Long",
        "status": "READY_LONG",
        "confidence": 82,
    }
    first = {
        "thesisId": "th_new",
        "mode": "SCALP",
        "direction": "Short",
        "status": "FORMING_SHORT",
        "confidence": 88,
        "evidenceEpoch": "bar-2",
        "structureEpoch": "structure-2",
        "lastUpdatedAt": "2026-08-28T12:00:00+00:00",
    }
    replay = dict(first)
    replay["lastUpdatedAt"] = "2026-08-28T12:05:00+00:00"
    event_1 = app._record_thesis_event(
        "MNQ", previous, first, "SCALP", transition_index=1,
    )
    event_2 = app._record_thesis_event(
        "MNQ", previous, replay, "SCALP", transition_index=1,
    )
    assert event_1["eventId"] == event_2["eventId"]
    assert len(app.THESIS_TIMELINE_BY_INST[("MNQ", "SCALP")]) == 1


def test_transition_bundle_commits_events_and_snapshot_atomically(monkeypatch):
    event_sql_calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            event_sql_calls.append((sql, params))

    class Connection:
        committed = False
        rolled_back = False

        def cursor(self):
            return Cursor()

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            return None

    connection = Connection()
    monkeypatch.setattr(app, "THESIS_DB_READY", True)
    monkeypatch.setattr(app, "get_db_connection", lambda: connection)
    snap = {
        "thesisId": "th_new",
        "mode": "SCALP",
        "direction": "Short",
        "status": "FORMING_SHORT",
        "confidence": 88,
    }
    events = [
        {
            "eventId": "invalidated",
            "mode": "SCALP",
            "thesisId": "th_old",
            "prevStatus": "READY_LONG",
            "newStatus": "INVALIDATED",
            "transitionIndex": 0,
        },
        {
            "eventId": "forming",
            "mode": "SCALP",
            "previousThesisId": "th_old",
            "thesisId": "th_new",
            "prevStatus": "INVALIDATED",
            "newStatus": "FORMING_SHORT",
            "transitionIndex": 1,
        },
    ]
    assert app._persist_thesis_transition_bundle(
        "MNQ", snap, "SCALP", events,
    ) is True
    assert connection.committed is True
    assert connection.rolled_back is False
    assert len(event_sql_calls) == 3
    assert "hysteresis_thesis_events" in event_sql_calls[0][0]
    assert "hysteresis_thesis_events" in event_sql_calls[1][0]
    assert "INSERT INTO hysteresis_thesis" in event_sql_calls[2][0]


def test_transition_bundle_rolls_back_at_every_write_boundary(monkeypatch):
    snap = {
        "thesisId": "th_new",
        "mode": "SCALP",
        "direction": "Short",
        "status": "FORMING_SHORT",
        "confidence": 88,
    }
    events = [
        {
            "eventId": "invalidated",
            "mode": "SCALP",
            "thesisId": "th_old",
            "prevStatus": "READY_LONG",
            "newStatus": "INVALIDATED",
            "transitionIndex": 0,
        },
        {
            "eventId": "forming",
            "mode": "SCALP",
            "previousThesisId": "th_old",
            "thesisId": "th_new",
            "prevStatus": "INVALIDATED",
            "newStatus": "FORMING_SHORT",
            "transitionIndex": 1,
        },
    ]

    for fail_on_write in (1, 2, 3):
        class Cursor:
            writes = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, _sql, _params):
                self.writes += 1
                if self.writes == fail_on_write:
                    raise RuntimeError("injected persistence failure")

        class Connection:
            committed = False
            rolled_back = False

            def cursor(self):
                return Cursor()

            def commit(self):
                self.committed = True

            def rollback(self):
                self.rolled_back = True

            def close(self):
                return None

        connection = Connection()
        monkeypatch.setattr(app, "THESIS_DB_READY", True)
        monkeypatch.setattr(app, "get_db_connection", lambda: connection)
        assert app._persist_thesis_transition_bundle(
            "MNQ", snap, "SCALP", events,
        ) is False
        assert connection.committed is False
        assert connection.rolled_back is True


def test_thesis_route_projects_requested_mode_and_both_scopes():
    _clear()
    _, scalp = _apply("MNQ", "SCALP", _strict("route-scalp", direction="Long"))
    _, intraday = _apply(
        "MNQ",
        "INTRADAY_TREND",
        _strict("route-it", direction="Short"),
    )
    response = app.app.test_client().get("/thesis?mode=SCALP")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "SCALP"
    assert payload["thesis"]["MNQ"]["thesisId"] == scalp["thesisId"]
    assert (
        payload["thesisByMode"]["MNQ"]["INTRADAY_TREND"]["thesisId"]
        == intraday["thesisId"]
    )
