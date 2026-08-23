"""Durable canonical evidence tests (pure shadow component; no app/database imports)."""

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import canonical_ghost_authority as authority
from canonical_ghost_evidence import CanonicalGhostEvidence


def _submission():
    return {
        "observation_id": "coord_obs_1",
        "market_opportunity_id": "market_1",
        "source_system": "generic_ghost",
        "source_event_id": "ghost|MNQ|Long|STRAT|1",
        "instrument": "MNQ",
        "timeframe": "1m",
        "setup_family": "STRICT_SETUP",
        "strategy_name": "STRAT",
        "strategy_version": "1",
        "direction": "Long",
        "signal_time": datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc),
        "source_bar_time": "2026-08-23T14:30:00+00:00",
        "entry": 21000.0,
        "stop": 20990.0,
        "targets": (21020.0,),
        "context": {
            "trading_mode": "SCALP",
            "legacy_obs_key": "ghost|MNQ|Long|STRAT|1",
            "legacy_table": "ghost_observations",
        },
    }


def _observed_event():
    return {
        "event_type": "OBSERVED",
        "canonical_opportunity_id": "cgo_1",
        "canonical_observation_id": "cobs_1",
        "coordinator_market_opportunity_id": "market_1",
        "trading_mode": "SCALP",
        "source_system": "generic_ghost",
        "source_record_id": "ghost|MNQ|Long|STRAT|1",
        "legacy_table": "ghost_observations",
        "event_at": "2026-08-23T14:30:00+00:00",
        "payload": {"entry": 21000.0, "stop": 20990.0, "targets": [21020.0]},
    }


def _outcome(**changes):
    event = {
        "event_type": "OUTCOME_RESOLVED",
        "canonical_opportunity_id": "cgo_1",
        "canonical_observation_id": "cobs_1",
        "coordinator_market_opportunity_id": "market_1",
        "trading_mode": "SCALP",
        "source_system": "generic_ghost",
        "source_record_id": "ghost|MNQ|Long|STRAT|1",
        "raw_status": "WIN",
        "raw_close_reason": "TARGET_1",
        "normalized_outcome": "WIN",
        "gross_r": 1.0,
        "cost_r": 0.1,
        "net_r": 0.9,
        "result_r": 0.9,
        "exit_price": 21020.0,
        "mfe_r": 1.1,
        "mae_r": -0.2,
        "bars_held": 5,
        "event_at": "2026-08-23T14:35:00+00:00",
        "payload": {"legacy_ghost_id": 1},
    }
    event.update(changes)
    return event


def test_exact_submission_creates_one_durable_evidence_record():
    evidence = CanonicalGhostEvidence(enabled=True)
    first = evidence.observe_submission(_submission(), _observed_event())
    second = evidence.observe_submission(_submission(), _observed_event())

    assert first["evidence_id"] == second["evidence_id"]
    assert evidence.report()["records"] == 1
    assert evidence.report()["duplicates"] == 1


def test_matched_record_freezes_resolver_metadata_and_provenance():
    evidence = CanonicalGhostEvidence(enabled=True)
    first = evidence.observe_submission(_submission(), _observed_event())

    assert first["resolver_name"] == "generic_ghost_observation_lifecycle"
    assert first["resolver_version"] == "legacy-generic-ghost-v1"
    assert first["provenance_fingerprint"].startswith("cgp_")
    assert first["evidence_schema_version"] == "2"

    altered = _submission()
    altered["entry"] = 21001.0
    assert evidence.observe_submission(altered, _observed_event()) is None
    assert evidence.report()["records"] == 1
    assert evidence.report()["errors"] == 1


def test_terminal_without_submission_becomes_one_durable_unmatched_record():
    writes = []
    evidence = CanonicalGhostEvidence(enabled=True)
    evidence.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: writes.append(dict(row)) or True,
    )

    first = evidence.observe_outcome(_outcome())
    duplicate = evidence.observe_outcome(_outcome())

    assert first["record_kind"] == "UNMATCHED"
    assert first["result_state"] == "UNMATCHED"
    assert first["unmatched_reason"] == "terminal_outcome_without_observation"
    assert duplicate["evidence_id"] == first["evidence_id"]
    assert evidence.report()["unmatched_records"] == 1
    assert writes[0]["record_kind"] == "UNMATCHED"

    restarted = CanonicalGhostEvidence(enabled=True)
    assert restarted.restore(writes) == len(writes)
    assert restarted.observe_outcome(_outcome())["evidence_id"] == first["evidence_id"]
    assert restarted.report()["unmatched_records"] == 1


def test_unmatched_then_exact_replay_has_one_exact_link_after_restart():
    writes = []
    evidence = CanonicalGhostEvidence(enabled=True)
    evidence.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: writes.append(dict(row)) or True,
    )
    missing_chain = dict(_observed_event())
    missing_chain["canonical_observation_id"] = ""
    unmatched = evidence.observe_submission(_submission(), missing_chain)
    matched = evidence.observe_submission(_submission(), _observed_event())

    assert unmatched["record_kind"] == "UNMATCHED"
    assert matched["record_kind"] == "MATCHED"
    assert evidence.report()["unresolved_unmatched_records"] == 0

    restarted = CanonicalGhostEvidence(enabled=True)
    assert restarted.restore(writes) == len(writes)
    report = restarted.report()
    assert report["records"] == 1
    assert report["unmatched_records"] == 1
    assert report["unresolved_unmatched_records"] == 0


def test_restart_repairs_exact_link_after_crash_between_matched_and_unmatched_writes():
    writes = []
    evidence = CanonicalGhostEvidence(enabled=True)
    evidence.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: writes.append(dict(row)) or True,
    )
    unmatched = evidence.observe_outcome(_outcome())
    matched = evidence.observe_submission(_submission(), _observed_event())

    # The crash window: the matched record committed, but the later unmatched
    # link update did not. App restore intentionally loads matched rows first.
    restart_rows = [writes[1], writes[0]]
    restarted = CanonicalGhostEvidence(enabled=True)
    assert restarted.restore(restart_rows) == 2

    assert restarted._unmatched[unmatched["evidence_id"]]["matched_evidence_id"] == matched["evidence_id"]
    assert restarted.report()["unresolved_unmatched_records"] == 0


def test_restart_marks_dangling_or_wrong_identity_link_unresolved():
    evidence = CanonicalGhostEvidence(enabled=True)
    matched = evidence.observe_submission(_submission(), _observed_event())
    dangling = evidence.observe_outcome(_outcome(
        canonical_observation_id="",
        source_record_id="other-source-result",
    ))
    dangling["matched_evidence_id"] = matched["evidence_id"]

    restarted = CanonicalGhostEvidence(enabled=True)
    assert restarted.restore([matched, dangling]) == 2

    assert restarted.report()["unresolved_unmatched_records"] == 1
    assert restarted.report()["errors"] == 1
    assert not restarted._unmatched[dangling["evidence_id"]]["matched_evidence_id"]


def test_app_persistence_rejects_database_provenance_conflict():
    source = Path(__file__).with_name("app.py").read_text()
    tree = ast.parse(source)
    persist_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_canonical_evidence_persist_record"
    )
    namespace = {
        "CANONICAL_EVIDENCE_DB_READY": True,
        "json": json,
    }
    exec(compile(ast.Module(body=[persist_node], type_ignores=[]), "app.py", "exec"), namespace)

    class Cursor:
        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, _params):
            self.rowcount = 0 if query.lstrip().startswith("INSERT") else 1

        @staticmethod
        def fetchone():
            return ("conflicting-fingerprint",)

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        @staticmethod
        def close():
            pass

    connection = Connection()
    namespace["_learning_conn"] = lambda: connection
    namespace["logger"] = type("Logger", (), {"debug": staticmethod(lambda *_args: None)})()
    evidence = CanonicalGhostEvidence(enabled=True)
    record = evidence.observe_submission(_submission(), _observed_event())

    assert namespace["_canonical_evidence_persist_record"](record) is False
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_failed_matched_persistence_never_creates_a_phantom_unmatched_link():
    writes = []
    evidence = CanonicalGhostEvidence(enabled=True)

    def reject_matched(row):
        if row["record_kind"] == "MATCHED":
            return False
        writes.append(dict(row))
        return True

    evidence.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=reject_matched,
    )
    unmatched = evidence.observe_outcome(_outcome())
    matched = evidence.observe_submission(_submission(), _observed_event())

    assert evidence.report()["unresolved_unmatched_records"] == 1
    assert all(
        not row.get("matched_evidence_id")
        for row in writes
        if row["evidence_id"] == unmatched["evidence_id"]
    )

    evidence.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: writes.append(dict(row)) or True,
    )
    assert evidence.report()["unresolved_unmatched_records"] == 0
    assert any(
        row["evidence_id"] == unmatched["evidence_id"]
        and row.get("matched_evidence_id") == matched["evidence_id"]
        for row in writes
    )


def test_app_persistence_rejects_unmatched_link_without_durable_target():
    source = Path(__file__).with_name("app.py").read_text()
    tree = ast.parse(source)
    persist_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_canonical_evidence_persist_record"
    )
    namespace = {
        "CANONICAL_EVIDENCE_DB_READY": True,
        "json": json,
    }
    exec(compile(ast.Module(body=[persist_node], type_ignores=[]), "app.py", "exec"), namespace)

    class Cursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def execute(_query, _params):
            pass

        @staticmethod
        def fetchone():
            return None

    class Connection:
        def __init__(self):
            self.rollbacks = 0

        @staticmethod
        def cursor():
            return Cursor()

        @staticmethod
        def commit():
            raise AssertionError("phantom unmatched link must not commit")

        def rollback(self):
            self.rollbacks += 1

        @staticmethod
        def close():
            pass

    connection = Connection()
    namespace["_learning_conn"] = lambda: connection
    namespace["logger"] = type("Logger", (), {"debug": staticmethod(lambda *_args: None)})()
    unmatched = CanonicalGhostEvidence(enabled=True).observe_outcome(_outcome())
    unmatched["matched_evidence_id"] = "cgev_not_durable"

    assert namespace["_canonical_evidence_persist_record"](unmatched) is False
    assert connection.rollbacks == 1


def test_scalp_and_intraday_have_distinct_replay_safe_evidence_identities():
    evidence = CanonicalGhostEvidence(enabled=True)
    scalp = evidence.observe_submission(_submission(), _observed_event())

    intraday_submission = _submission()
    intraday_submission["source_event_id"] = "ghost|MNQ|Long|IT|1"
    intraday_submission["context"] = dict(
        intraday_submission["context"],
        trading_mode="INTRADAY_TREND",
        legacy_obs_key="ghost|MNQ|Long|IT|1",
    )
    intraday_event = dict(
        _observed_event(),
        canonical_opportunity_id="cgo_it_1",
        canonical_observation_id="cobs_it_1",
        trading_mode="INTRADAY_TREND",
        source_record_id="ghost|MNQ|Long|IT|1",
    )
    intraday = evidence.observe_submission(intraday_submission, intraday_event)

    assert scalp["evidence_id"] != intraday["evidence_id"]
    assert evidence.report()["by_mode"]["SCALP"]["records"] == 1
    assert evidence.report()["by_mode"]["INTRADAY_TREND"]["records"] == 1


def test_terminal_outcome_is_one_versioned_snapshot_and_duplicate_replays_collapse():
    evidence = CanonicalGhostEvidence(enabled=True)
    evidence.observe_submission(_submission(), _observed_event())
    first = evidence.observe_outcome(_outcome())
    duplicate = evidence.observe_outcome(_outcome())

    assert first["result_state"] == "TERMINAL"
    assert first["outcome_version"].startswith("cov_")
    assert duplicate["outcome_version"] == first["outcome_version"]
    assert evidence.report()["records"] == 1
    assert evidence.report()["by_mode"]["SCALP"]["terminal_records"] == 1


def test_newer_terminal_correction_replaces_snapshot_with_deterministic_version():
    evidence = CanonicalGhostEvidence(enabled=True)
    evidence.observe_submission(_submission(), _observed_event())
    first = evidence.observe_outcome(_outcome())
    corrected = evidence.observe_outcome(_outcome(
        raw_status="LOSS",
        raw_close_reason="STOP_HIT",
        normalized_outcome="LOSS",
        gross_r=-1.0,
        net_r=-1.1,
        result_r=-1.1,
        event_at="2026-08-23T14:36:00+00:00",
    ))

    assert corrected["outcome_version"] != first["outcome_version"]
    assert corrected["normalized_outcome"] == "LOSS"
    assert evidence.observe_outcome(_outcome())["normalized_outcome"] == "LOSS"


def test_restart_restore_and_failed_write_retry_preserve_exact_record():
    writes = []
    evidence = CanonicalGhostEvidence(enabled=True)
    evidence.configure(enabled=True, persistence_enabled=True, persist_fn=lambda _row: False)
    evidence.observe_submission(_submission(), _observed_event())
    assert evidence.report()["pending_records"] == 1
    evidence.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: writes.append(dict(row)) or True,
    )
    evidence.observe_outcome(_outcome())

    restarted = CanonicalGhostEvidence(enabled=True)
    assert restarted.restore(writes) == 2
    assert restarted.observe_submission(_submission(), _observed_event())["evidence_id"] == writes[0]["evidence_id"]
    assert restarted.report()["records"] == 1
    assert restarted.report()["by_mode"]["SCALP"]["terminal_records"] == 1


def test_only_explicit_canonical_generic_authority_is_eligible():
    evidence = CanonicalGhostEvidence(enabled=True)
    noncanonical = dict(_observed_event(), trading_mode="SWING")
    assert evidence.observe_submission(_submission(), noncanonical) is None
    reference = dict(_observed_event(), source_system="scalp_live_sim")
    assert evidence.observe_submission(_submission(), reference) is None
    assert evidence.report()["records"] == 0