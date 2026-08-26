"""Canonical Ghost Phase 1 shadow authority tests (no app/database imports)."""

from datetime import datetime, timezone

import canonical_ghost_authority as cga
import ghost_coordinator as gc


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
        "instrument": "MNQ",
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


def test_reference_before_generic_authority_is_promoted_by_exact_opportunity_only():
    persisted = []
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: persisted.append(dict(row)) or True,
    )
    sim_key = "SCALP|MNQ|Long|20260823:1:1"
    generic_obs_key = "ghost|MNQ|Long|STRAT|20260823|1"
    reference = _record(
        observation_id="obs_dual",
        source_system="dual_mode_sim",
        source_event_id=sim_key,
        context={
            "trading_mode": "SCALP",
            "legacy_record_id": sim_key,
            "legacy_sim_key": sim_key,
            "legacy_table": "dual_sim_trades",
            "canonical_authority_id": generic_obs_key,
        },
    )

    assert authority.observe_coordinator_submission(reference) is None
    generic = authority.observe_coordinator_submission(_record(
        source_event_id=generic_obs_key,
    ))
    assert generic is not None

    report = authority.report()
    assert report["cross_source_match_count"] == 1
    assert report["unmatched_legacy_references"] == 0
    assert [row["event_type"] for row in persisted] == [
        "REFERENCE_UNMATCHED", "OBSERVED", "OBSERVED",
    ]

    restarted = cga.CanonicalGhostAuthority(enabled=True)
    assert restarted.restore(persisted) == 3
    assert restarted.observe_coordinator_submission(reference) is not None
    restarted_report = restarted.report()
    assert restarted_report["cross_source_match_count"] == 1
    assert restarted_report["unmatched_legacy_references"] == 0


def test_reference_anchor_never_crosses_canonical_modes():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.observe_coordinator_submission(_record())
    sim_key = "INTRADAY_TREND|MNQ|Long|20260823:1:1"
    assert authority.observe_coordinator_submission(
        _record(
            observation_id="obs_it_dual",
            source_system="dual_mode_sim",
            source_event_id=sim_key,
            context={
                "trading_mode": "INTRADAY_TREND",
                "legacy_record_id": sim_key,
                "legacy_sim_key": sim_key,
                "legacy_table": "dual_sim_trades",
                "canonical_authority_id": "ghost|MNQ|Long|STRAT|20260823|1",
            },
        )
    ) is None

    health = authority.health_report(now="2026-08-23T16:00:00+00:00")
    assert health["by_mode"]["SCALP"]["exact_id_match_count"] == 0
    assert health["by_mode"]["INTRADAY_TREND"]["exact_id_unmatched_count"] == 1


def test_reference_anchor_never_crosses_instruments_inside_one_coordinator_opportunity():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.observe_coordinator_submission(_record())

    assert authority.observe_coordinator_submission(
        _record(
            observation_id="obs_mgc_collision",
            source_system="dual_mode_sim",
            source_event_id="SCALP|MGC|collision",
            instrument="MGC",
            context={
                "trading_mode": "SCALP",
                "legacy_record_id": "SCALP|MGC|collision",
                "legacy_sim_key": "SCALP|MGC|collision",
                "legacy_table": "dual_sim_trades",
                "canonical_authority_id": "ghost|MNQ|Long|STRAT|20260823|1",
            },
        )
    ) is None

    health = authority.health_report(now="2026-08-23T16:00:00+00:00")
    assert health["by_mode"]["SCALP"]["exact_id_match_count"] == 0
    assert health["by_mode"]["SCALP"]["exact_id_unmatched_count"] == 1


def test_missing_instrument_cannot_create_or_authorize_canonical_links():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    blank_generic = _record(instrument="")
    blank_reference = _record(
        observation_id="obs_blank_ref",
        source_system="dual_mode_sim",
        source_event_id="SCALP|blank|1",
        instrument="",
        context={
            "trading_mode": "SCALP",
            "legacy_record_id": "SCALP|blank|1",
            "legacy_sim_key": "SCALP|blank|1",
            "legacy_table": "dual_sim_trades",
            "canonical_authority_id": "ghost|MNQ|Long|STRAT|20260823|1",
        },
    )

    assert authority.observe_coordinator_submission(blank_generic) is None
    assert authority.observe_coordinator_submission(blank_reference) is None
    assert authority.health_report()["intake_volume"] == 0


def test_dual_reference_requires_its_explicit_generic_obs_key():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.observe_coordinator_submission(_record())
    sim_key = "SCALP|MNQ|Long|20260823:2:2"
    assert authority.observe_coordinator_submission(
        _record(
            observation_id="obs_dual_without_anchor",
            source_system="dual_mode_sim",
            source_event_id=sim_key,
            context={
                "trading_mode": "SCALP",
                "legacy_record_id": sim_key,
                "legacy_table": "dual_sim_trades",
            },
        )
    ) is None
    assert authority.report()["cross_source_match_count"] == 0


def test_dual_reference_rejects_mismatched_sim_key_even_with_matching_alias():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.observe_coordinator_submission(_record())
    sim_key = "SCALP|MNQ|Long|20260823:bad-key"
    assert authority.observe_coordinator_submission(
        _record(
            observation_id="obs_dual_bad_sim_key",
            source_system="dual_mode_sim",
            source_event_id=sim_key,
            context={
                "trading_mode": "SCALP",
                "legacy_record_id": sim_key,
                "legacy_sim_key": "different-sim-key",
                "legacy_table": "dual_sim_trades",
                "canonical_authority_id": "ghost|MNQ|Long|STRAT|20260823|1",
            },
        )
    ) is None
    assert authority.report()["cross_source_match_count"] == 0


def test_dual_first_mismatched_sim_key_never_promotes_after_generic_arrives():
    persisted = []
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: persisted.append(dict(row)) or True,
    )
    source_event_id = "SCALP|MNQ|Long|20260823:dual-first"
    generic_obs_key = "ghost|MNQ|Long|STRAT|20260823|1"
    assert authority.observe_coordinator_submission(
        _record(
            observation_id="obs_dual_bad_before_generic",
            source_system="dual_mode_sim",
            source_event_id=source_event_id,
            context={
                "trading_mode": "SCALP",
                "legacy_record_id": source_event_id,
                "legacy_sim_key": "different-sim-key",
                "legacy_table": "dual_sim_trades",
                "canonical_authority_id": generic_obs_key,
            },
        )
    ) is None
    assert authority.observe_coordinator_submission(_record(
        source_event_id=generic_obs_key,
    )) is not None

    assert authority.report()["cross_source_match_count"] == 0
    assert authority.report()["unmatched_legacy_references"] == 1
    assert [row["event_type"] for row in persisted] == [
        "REFERENCE_UNMATCHED", "OBSERVED",
    ]


def test_dual_reference_is_exactly_linked_through_coordinator_anchor():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    generic_obs_key = "ghost|MNQ|Long|STRAT|20260823|1"
    sim_key = "SCALP|MNQ|Long|20260823:3:3"
    dual_request = gc.ObservationRequest(
        source_system="dual_mode_sim", source_event_id=sim_key, instrument="MNQ",
        timeframe="1m", setup_family="STRICT_SETUP", strategy_name="DUAL_SCALP",
        strategy_version="v1", direction="Long",
        signal_time=datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc),
        source_bar_time="2026-08-23T14:30:00+00:00", entry=21000, stop=20990,
        targets=(21020,), experiment_variant="SCALP",
        context={
            "trading_mode": "SCALP", "legacy_record_id": sim_key,
            "legacy_sim_key": sim_key,
            "legacy_table": "dual_sim_trades",
            "canonical_authority_id": generic_obs_key,
        },
    )
    generic_request = gc.ObservationRequest(
        source_system="generic_ghost", source_event_id=generic_obs_key, instrument="MNQ",
        timeframe="1m", setup_family="STRICT_SETUP", strategy_name="STRAT",
        strategy_version="v1", direction="Long",
        signal_time=datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc),
        source_bar_time="2026-08-23T14:30:00+00:00", entry=21000, stop=20990,
        targets=(21020,),
        context={
            "trading_mode": "SCALP", "legacy_obs_key": generic_obs_key,
            "legacy_table": "ghost_observations",
        },
    )
    dual = coordinator.submit(dual_request)
    generic = coordinator.submit(generic_request)
    assert dual.market_opportunity_id == generic.market_opportunity_id

    authority = cga.CanonicalGhostAuthority(enabled=True)
    assert authority.observe_coordinator_submission(_record(
        observation_id=dual.observation_id,
        market_opportunity_id=dual.market_opportunity_id,
        source_system="dual_mode_sim",
        source_event_id=sim_key,
        context=dual_request.context,
    )) is None
    assert authority.observe_coordinator_submission(_record(
        observation_id=generic.observation_id,
        market_opportunity_id=generic.market_opportunity_id,
        source_event_id=generic_obs_key,
        context=generic_request.context,
    )) is not None
    assert authority.report()["cross_source_match_count"] == 1
    assert authority.report()["unmatched_legacy_references"] == 0


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


def test_health_report_has_honest_empty_per_mode_state():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    health = authority.health_report(now="2026-08-23T16:00:00+00:00")

    assert health["read_only"] is True
    assert health["shadow_only"] is True
    assert health["strat_lab_included"] is False
    assert health["health_status"] == "NO_DATA"
    assert health["by_mode"]["SCALP"]["status"] == "NO_DATA"
    assert health["by_mode"]["INTRADAY_TREND"]["status"] == "NO_DATA"
    assert health["reconciliation"]["exact_id_match_coverage"] is None


def test_health_report_counts_duplicates_unresolved_and_overdue_without_resolving():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.observe_coordinator_submission(_record())
    authority.observe_coordinator_submission(_record())

    health = authority.health_report(
        now="2026-08-23T20:00:00+00:00",
        overdue_after_minutes=60,
    )
    scalp = health["by_mode"]["SCALP"]
    assert scalp["intake_volume"] == 1
    assert scalp["duplicate_count"] == 1
    assert scalp["deduplication_rate"] == 50.0
    assert scalp["unresolved_observations"] == 1
    assert scalp["stale_observations"] == 1
    assert scalp["overdue_observations"] == 1
    assert scalp["status"] == "ATTENTION"


def test_health_report_measures_exact_id_agreement_and_disagreement():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.observe_coordinator_submission(_record())
    authority.observe_coordinator_submission(
        _record(
            source_system="scalp_live_sim",
            source_event_id="sim-1",
            context={"trading_mode": "SCALP", "legacy_record_id": "sim-1"},
        )
    )
    authority.observe_legacy_outcome(
        source_system="generic_ghost",
        source_record_id="ghost|MNQ|Long|STRAT|20260823|1",
        raw_status="win",
        result_r=1.0,
    )
    authority.observe_legacy_outcome(
        source_system="scalp_live_sim",
        source_record_id="sim-1",
        raw_status="loss",
        result_r=-1.0,
    )

    health = authority.health_report(now="2026-08-23T16:00:00+00:00")
    scalp = health["by_mode"]["SCALP"]
    assert scalp["exact_id_match_count"] == 1
    assert scalp["exact_id_unmatched_count"] == 0
    assert scalp["exact_id_match_coverage"] == 100.0
    assert scalp["outcome_comparison_count"] == 1
    assert scalp["outcome_disagreement_count"] == 1
    assert health["outcomes"]["disagreement_count"] == 1


def test_health_report_exposes_persistence_failure_without_mutating_evidence():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    authority.configure(enabled=True, persistence_enabled=True, persist_fn=lambda _row: False)
    authority.observe_coordinator_submission(_record())

    health = authority.health_report(now="2026-08-23T16:00:00+00:00")
    scalp = health["by_mode"]["SCALP"]
    assert scalp["persistence_errors"] >= 1
    assert scalp["pending_persistence_events"] == 1
    assert health["persistence"]["pending_events"] == 1
    assert health["health_status"] == "ATTENTION"
    assert authority.report()["unique_canonical_observations"] == 1


def test_health_report_keeps_strategy_lab_out_of_canonical_outcome_authority():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    assert authority.observe_coordinator_submission(
        _record(context={"trading_mode": "STRATEGY_LAB", "legacy_obs_key": "lab-only"})
    ) is None

    health = authority.health_report(now="2026-08-23T16:00:00+00:00")
    assert health["strat_lab_included"] is False
    assert health["intake_volume"] == 0
    assert health["ignored_noncanonical_events"] == 1


def test_health_report_restores_durable_exact_id_matches_after_restart():
    persisted = []
    first = cga.CanonicalGhostAuthority(enabled=True)
    first.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: persisted.append(dict(row)) or True,
    )
    first.observe_coordinator_submission(_record())
    first.observe_coordinator_submission(
        _record(
            source_system="scalp_live_sim",
            source_event_id="sim-restore",
            context={"trading_mode": "SCALP", "legacy_record_id": "sim-restore"},
        )
    )
    first.observe_legacy_outcome(
        source_system="generic_ghost",
        source_record_id="ghost|MNQ|Long|STRAT|20260823|1",
        raw_status="win",
        result_r=1.0,
    )
    first.observe_legacy_outcome(
        source_system="scalp_live_sim",
        source_record_id="sim-restore",
        raw_status="win",
        result_r=1.0,
    )

    restarted = cga.CanonicalGhostAuthority(enabled=True)
    assert restarted.restore(persisted) == 4
    health = restarted.health_report(
        now="2026-08-23T16:00:00+00:00",
        durable_report={
            "db_ready": True,
            "by_mode": {
                "SCALP": {
                    "durable_event_count": 4,
                    "last_successful_write_at": "2026-08-23T15:00:00+00:00",
                    "last_reconciliation_at": "2026-08-23T15:00:00+00:00",
                },
                "INTRADAY_TREND": {},
            },
        },
    )
    scalp = health["by_mode"]["SCALP"]
    assert scalp["exact_id_match_count"] == 1
    assert scalp["exact_id_match_coverage"] == 100.0
    assert scalp["outcome_agreement_count"] == 1
    assert scalp["persistence_writes"] == 4
    assert health["persistence"]["durable_persisted_events"] == 4


def test_health_report_separates_coordinator_opportunities_from_gate_heartbeats():
    authority = cga.CanonicalGhostAuthority(enabled=True)
    health = authority.health_report(
        coordinator_report={
            "enabled": True,
            "opportunity_count": 3,
            "opportunity_observation_count": 4,
            "evaluation_checks": 19,
            "evaluation_heartbeats": 15,
            "evaluation_transitions": 4,
        }
    )

    assert health["coordinator"]["opportunity_count"] == 3
    assert health["coordinator"]["opportunity_observation_count"] == 4
    assert health["coordinator"]["evaluation_checks"] == 19
    assert health["coordinator"]["evaluation_heartbeats"] == 15
    assert health["coordinator"]["evaluation_transitions"] == 4


def test_health_report_restores_durable_unmatched_exact_id_reference_after_restart():
    persisted = []
    first = cga.CanonicalGhostAuthority(enabled=True)
    first.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: persisted.append(dict(row)) or True,
    )
    assert first.observe_coordinator_submission(
        _record(
            source_system="scalp_live_sim",
            source_event_id="sim-unmatched-restore",
            context={
                "trading_mode": "SCALP",
                "legacy_record_id": "sim-unmatched-restore",
            },
        )
    ) is None
    assert persisted[0]["event_type"] == "REFERENCE_UNMATCHED"

    restarted = cga.CanonicalGhostAuthority(enabled=True)
    assert restarted.restore(persisted) == 1
    health = restarted.health_report(
        durable_report={
            "db_ready": True,
            "by_mode": {
                "SCALP": {"durable_event_count": 1},
                "INTRADAY_TREND": {},
            },
        }
    )
    scalp = health["by_mode"]["SCALP"]
    assert scalp["exact_id_match_count"] == 0
    assert scalp["exact_id_unmatched_count"] == 1
    assert scalp["exact_id_match_coverage"] == 0.0
    assert scalp["exact_id_coverage_scope"] == "append_only_exact_id_reference_events"


def test_strict_link_health_fixture_proves_exact_durable_identity_boundaries():
    """The health fixture is deterministic, shadow-only, and database-free."""
    first = cga.run_strict_link_health_verification()
    second = cga.run_strict_link_health_verification()

    assert first == second
    assert first["ok"] is True
    assert first["status"] == "PASSED"
    assert first["read_only"] is True
    assert first["shadow_only"] is True
    assert first["fixture"] == "in_memory_exact_id_generic_and_dual_sim"
    assert first["checks"] == {
        "reference_retained_before_authority": True,
        "exact_reference_relinked_after_authority": True,
        "cross_instrument_reference_rejected": True,
        "cross_mode_reference_rejected": True,
        "malformed_reference_rejected": True,
        "missing_instrument_rejected": True,
        "unsupported_mode_rejected": True,
        "replay_suppressed": True,
        "reconciliation_copied": True,
        "restart_restore_complete": True,
        "strict_link_only": True,
        "persistence_healthy": True,
    }
    assert first["counters"] == {
        "matched": 1,
        "unmatched_retained": 4,
        "unresolved": 3,
        "relinked_after_authority": 1,
        "wrong_ledger_rejected": 2,
        "duplicates_replays_suppressed": 5,
        "persistence_errors": 0,
        "restart_errors": 0,
        "mode_isolation_rejected": 1,
        "instrument_isolation_rejected": 1,
        "malformed_references_rejected": 1,
        "missing_instrument_rejected": 2,
        "unsupported_mode_rejected": 1,
    }