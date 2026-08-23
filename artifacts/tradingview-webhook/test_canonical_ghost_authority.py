"""Canonical Ghost Phase 1 shadow authority tests (no app/database imports)."""

from datetime import datetime, timezone

import canonical_ghost_authority as cga


def _record(**overrides):
    record = {
        "observation_id": "obs_legacy",
        "market_opportunity_id": "mop_shared",
        "source_system": "generic_ghost",
        "source_event_id": "ghost|MNQ|Long|STRAT|20260823|1",
        "signal_time": datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc),
        "strategy_name": "STRAT",
        "setup_family": "STRICT_SETUP",
        "direction": "Long",
        "entry": 21000,
        "stop": 20990,
        "targets": (21020,),
        "context": {
            "trading_mode": "SCALP",
            "legacy_obs_key": "ghost|MNQ|Long|STRAT|20260823|1",
            "legacy_table": "ghost_observations",
        },
    }
    record.update(overrides)
    return record


def test_one_legacy_opportunity_creates_one_canonical_observation():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    first = authority.observe_coordinator_submission(_record())
    second = authority.observe_coordinator_submission(_record())
    assert first["canonical_observation_id"] == second["canonical_observation_id"]
    report = authority.report()
    assert report["unique_canonical_opportunities"] == 1
    assert report["unique_canonical_observations"] == 1
    assert report["duplicate_events"] == 1


def test_scalp_and_intraday_are_explicitly_separated():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    scalp = authority.observe_coordinator_submission(_record())
    intraday = authority.observe_coordinator_submission(
        _record(context={
            "trading_mode": "INTRADAY_TREND",
            "legacy_obs_key": "ghost|MNQ|Long|IT|20260823|1",
            "legacy_table": "ghost_observations",
        })
    )
    assert scalp["canonical_opportunity_id"] != intraday["canonical_opportunity_id"]
    assert authority.report()["by_mode"] == {"INTRADAY_TREND": 1, "SCALP": 1}


def test_legacy_outcome_is_copied_not_resolved_or_rewritten():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.observe_coordinator_submission(_record())
    copied = authority.observe_legacy_outcome(
        source_system="generic_ghost",
        source_record_id="ghost|MNQ|Long|STRAT|20260823|1",
        raw_status="loss",
        close_reason="STOP_HIT",
        gross_r=-1.0,
        net_r=-1.12,
    )
    assert copied["normalized_outcome"] == "LOSS"
    report = authority.report()
    assert report["opportunities"][0]["canonical_outcome"] == "LOSS"
    assert report["opportunities"][0]["outcome_agreement"] == "NO_COMPARISON"


def test_restart_restore_preserves_deduplication_and_outcome_mapping():
    persisted = []
    first = cga.CanonicalGhostAuthority(enabled=True)
    first.configure(enabled=True, persistence_enabled=True, persist_fn=lambda row: persisted.append(dict(row)) or True)
    first.observe_coordinator_submission(_record())
    first.observe_legacy_outcome(
        source_system="generic_ghost",
        source_record_id="ghost|MNQ|Long|STRAT|20260823|1",
        raw_status="win",
        close_reason="TARGET_1",
        result_r=1.0,
    )

    restarted = cga.CanonicalGhostAuthority(enabled=True)
    assert restarted.restore(persisted) == 2
    duplicate = restarted.observe_coordinator_submission(_record())
    assert duplicate is not None
    report = restarted.report()
    assert report["unique_canonical_observations"] == 1
    assert report["opportunities"][0]["canonical_outcome"] == "WIN"


def test_noncanonical_modes_are_ignored_without_creating_authority():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    assert authority.observe_coordinator_submission(
        _record(context={"trading_mode": "SWING", "legacy_obs_key": "legacy"})
    ) is None
    assert authority.report()["unique_canonical_opportunities"] == 0


def test_other_system_is_a_reference_only_after_generic_authority_exists():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    unmatched = authority.observe_coordinator_submission(
        _record(source_system="scalp_live_sim", source_event_id="sim-1", context={
            "trading_mode": "SCALP", "legacy_record_id": "sim-1",
        })
    )
    assert unmatched is None
    authority.observe_coordinator_submission(_record())
    matched = authority.observe_coordinator_submission(
        _record(source_system="scalp_live_sim", source_event_id="sim-1", context={
            "trading_mode": "SCALP", "legacy_record_id": "sim-1",
        })
    )
    assert matched is not None
    report = authority.report()
    assert report["unique_canonical_opportunities"] == 1
    assert report["cross_source_match_count"] == 1
    assert report["opportunities"][0]["outcome_agreement"] == "NO_COMPARISON"


def test_failed_persistence_is_visible_and_retried():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.configure(enabled=True, persistence_enabled=True, persist_fn=lambda _row: False)
    authority.observe_coordinator_submission(_record())
    assert authority.report()["pending_persistence_events"] == 1
    persisted = []
    authority.configure(
        enabled=True, persistence_enabled=True,
        persist_fn=lambda row: persisted.append(dict(row)) or True,
    )
    assert authority.report()["pending_persistence_events"] == 0
    assert authority.report()["persistence_errors"] >= 1
    assert len(persisted) == 1