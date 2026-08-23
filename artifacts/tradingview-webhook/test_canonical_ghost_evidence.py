"""Durable canonical evidence tests (pure shadow component; no app/database imports)."""

from datetime import datetime, timezone

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