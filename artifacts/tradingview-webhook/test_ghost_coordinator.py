"""Phase 1 Central Ghost Coordinator unit tests (no app or database imports)."""

from datetime import datetime, timezone
import ast
from pathlib import Path

import ghost_coordinator as gc


def _request(**overrides):
    data = {
        "source_system": "generic_ghost",
        "source_event_id": "MNQ|bar-101|Long",
        "instrument": "MNQ",
        "timeframe": "1m",
        "setup_family": "STRICT_SETUP",
        "strategy_name": "BASELINE",
        "strategy_version": "v1",
        "direction": "Long",
        "signal_time": datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc),
        "source_bar_time": "2026-08-21T14:30:00+00:00",
        "entry": 21000,
        "stop": 20990,
        "targets": (21020, 21030),
        "context": {"edge": 80},
    }
    data.update(overrides)
    return gc.ObservationRequest(**data)


def test_disabled_mode_is_a_noop():
    coordinator = gc.CentralGhostCoordinator(enabled=False)
    result = coordinator.submit(_request())
    assert result.ignored is True
    assert coordinator.report()["unique_observations"] == 0


def test_deterministic_ids_and_deduplication():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    first = coordinator.submit(_request())
    second = coordinator.submit(_request())
    assert first.accepted and first.observation_id
    assert second.duplicate is True
    report = coordinator.report()
    assert report["unique_market_opportunities"] == 1
    assert report["unique_observations"] == 1
    assert report["duplicate_submissions"] == 1


def test_durable_health_totals_are_separate_from_restored_session_window():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    coordinator.configure(
        enabled=True,
        health_aggregate_fn=lambda: {
            "db_ready": True,
            "complete": True,
            "scope": "all_durable_rows",
            "opportunity_count": 120,
            "observation_count": 240,
            "evaluation_checks": 300,
            "evaluation_heartbeats": 60,
            "evaluation_transitions": 240,
            "telemetry_event_count": 12,
        },
    )

    assert coordinator.submit(_request()).accepted
    report = coordinator.report()

    assert report["opportunity_count"] == 1
    assert report["opportunity_observation_count"] == 1
    assert report["restored_session_counts"]["opportunity_count"] == 1
    assert report["restored_session_counts"]["observation_count"] == 1
    assert report["durable_totals"]["opportunity_count"] == 120
    assert report["durable_totals"]["observation_count"] == 240
    assert report["health_totals"] == {
        "source": "durable",
        "complete": True,
        "opportunity_count": 120,
        "observation_count": 240,
        "evaluation_checks": 300,
        "evaluation_heartbeats": 60,
        "evaluation_transitions": 240,
        "telemetry_event_count": 12,
    }

    # The aggregate path is reporting-only; the restored in-memory identity
    # remains the source of exact duplicate detection.
    duplicate = coordinator.submit(_request())
    assert duplicate.duplicate is True


def test_health_totals_fall_back_to_restored_session_when_aggregate_is_unavailable():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    coordinator.configure(
        enabled=True,
        health_aggregate_fn=lambda: {
            "db_ready": False,
            "complete": False,
            "error": "database unavailable",
        },
    )
    assert coordinator.submit(_request()).accepted

    report = coordinator.report()
    assert report["health_totals"]["source"] == "restored_session"
    assert report["health_totals"]["complete"] is False
    assert report["health_totals"]["opportunity_count"] == 1
    assert report["health_totals"]["observation_count"] == 1


def _gate_evaluation_request(**overrides):
    context = {
        "coordinator_evaluation_kind": "gate_check",
        "coordinator_opportunity_key": "SCALP|BASELINE|MNQ|Long|20260821",
        "coordinator_evaluation_fingerprint": "state-a",
    }
    context.update(overrides.pop("context", {}))
    data = {
        "source_system": "gate_effectiveness",
        "source_event_id": "SCALP|BASELINE|MNQ|Long|BLOCKED|202608211430",
        "instrument": "MNQ",
        "timeframe": "1m",
        "setup_family": "STRICT_SETUP",
        "strategy_name": "BASELINE",
        "strategy_version": "v1",
        "direction": "Long",
        "signal_time": "2026-08-21T14:30:00+00:00",
        "source_bar_time": "2026-08-21T14:30:00+00:00",
        "entry": 21000,
        "stop": 20990,
        "targets": (21020, 21030),
        "context": context,
        "experiment_variant": "SCALP",
    }
    data.update(overrides)
    return gc.ObservationRequest(**data)


def test_unchanged_gate_evaluations_are_heartbeats_not_observations():
    writes = []
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    coordinator.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda kind, row: writes.append((kind, dict(row))) or True,
    )

    first = coordinator.submit(_gate_evaluation_request())
    heartbeat = coordinator.submit(_gate_evaluation_request(
        source_event_id="SCALP|BASELINE|MNQ|Long|BLOCKED|202608211440",
        signal_time="2026-08-21T14:40:00+00:00",
        source_bar_time="2026-08-21T14:40:00+00:00",
    ))

    assert first.accepted and not first.heartbeat
    assert heartbeat.duplicate and heartbeat.heartbeat
    assert heartbeat.observation_id == first.observation_id
    assert heartbeat.market_opportunity_id == first.market_opportunity_id
    report = coordinator.report()
    assert report["opportunity_count"] == 1
    assert report["opportunity_observation_count"] == 1
    assert report["evaluation_checks"] == 2
    assert report["evaluation_heartbeats"] == 1
    assert report["evaluation_transitions"] == 1
    assert report["source_systems"][0]["evaluation_heartbeats"] == 1
    assert [kind for kind, _ in writes] == ["observation", "evaluation_heartbeat"]
    assert writes[-1][1]["evaluation_heartbeat_count"] == 1


def test_gate_state_transition_keeps_opportunity_and_adds_auditable_observation():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    first = coordinator.submit(_gate_evaluation_request())
    transitioned = coordinator.submit(_gate_evaluation_request(
        source_event_id="SCALP|BASELINE|MNQ|Long|ALLOWED|202608211450",
        context={"coordinator_evaluation_fingerprint": "state-b"},
    ))

    assert transitioned.accepted
    assert transitioned.duplicate is False
    assert transitioned.market_opportunity_id == first.market_opportunity_id
    assert transitioned.observation_id != first.observation_id
    report = coordinator.report()
    assert report["opportunity_count"] == 1
    assert report["opportunity_observation_count"] == 2
    assert report["evaluation_checks"] == 2
    assert report["evaluation_heartbeats"] == 0
    assert report["evaluation_transitions"] == 2


def test_gate_state_reversion_is_a_new_auditable_transition():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    first = coordinator.submit(_gate_evaluation_request())
    coordinator.submit(_gate_evaluation_request(
        source_event_id="SCALP|BASELINE|MNQ|Long|ALLOWED|202608211450",
        context={"coordinator_evaluation_fingerprint": "state-b"},
    ))
    reverted = coordinator.submit(_gate_evaluation_request(
        source_event_id="SCALP|BASELINE|MNQ|Long|BLOCKED|202608211500",
        context={"coordinator_evaluation_fingerprint": "state-a"},
    ))

    assert reverted.duplicate is False
    assert reverted.observation_id not in {first.observation_id}
    assert coordinator.report()["opportunity_observation_count"] == 3
    assert coordinator.report()["evaluation_transitions"] == 3


def test_gate_heartbeat_restore_preserves_counts_without_replaying_storage():
    writes = []
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    coordinator.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda kind, row: writes.append((kind, dict(row))) or True,
    )
    coordinator.submit(_gate_evaluation_request())
    coordinator.submit(_gate_evaluation_request(
        source_event_id="SCALP|BASELINE|MNQ|Long|BLOCKED|202608211440",
    ))

    restored = gc.CentralGhostCoordinator(enabled=True)
    assert restored.restore([writes[0][1]]) == 1
    report = restored.report()
    assert report["evaluation_checks"] == 2
    assert report["evaluation_heartbeats"] == 1
    assert report["evaluation_transitions"] == 1


def test_gate_state_transition_sequence_survives_restore():
    persisted = []
    first = gc.CentralGhostCoordinator(enabled=True)
    first.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda kind, row: persisted.append((kind, dict(row))) or True,
    )
    first.submit(_gate_evaluation_request())
    first.submit(_gate_evaluation_request(
        source_event_id="SCALP|BASELINE|MNQ|Long|ALLOWED|202608211450",
        context={"coordinator_evaluation_fingerprint": "state-b"},
    ))

    restored = gc.CentralGhostCoordinator(enabled=True)
    assert restored.restore([row for kind, row in persisted if kind == "observation"]) == 2
    reverted = restored.submit(_gate_evaluation_request(
        source_event_id="SCALP|BASELINE|MNQ|Long|BLOCKED|202608211500",
        context={"coordinator_evaluation_fingerprint": "state-a"},
    ))

    assert reverted.duplicate is False
    assert restored.report()["opportunity_observation_count"] == 3


def test_gate_opportunity_key_remains_instrument_scoped():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    mnq = coordinator.submit(_gate_evaluation_request())
    mgc = coordinator.submit(_gate_evaluation_request(
        instrument="MGC",
        context={
            "coordinator_opportunity_key": "SCALP|BASELINE|MGC|Long|20260821",
        },
    ))

    assert mnq.market_opportunity_id != mgc.market_opportunity_id
    assert coordinator.report()["opportunity_count"] == 2


def test_variants_do_not_collapse_into_one_observation():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    baseline = coordinator.submit(_request(experiment_variant="BASELINE"))
    variant = coordinator.submit(_request(experiment_variant="TP_2R"))
    assert baseline.market_opportunity_id == variant.market_opportunity_id
    assert baseline.observation_id != variant.observation_id
    assert coordinator.report()["unique_observations"] == 2


def test_multiple_sources_match_one_canonical_opportunity():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    coordinator.submit(_request(source_system="generic_ghost"))
    coordinator.submit(_request(source_system="gate_effectiveness"))
    report = coordinator.report()
    assert report["cross_system_match_count"] == 1
    assert report["cross_system_opportunities"][0]["source_systems"] == [
        "gate_effectiveness", "generic_ghost"
    ]


def test_explicit_generic_anchor_links_only_the_declared_comparison_source():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    generic = coordinator.submit(_request(
        source_system="generic_ghost",
        source_event_id="ghost|MNQ|Long|BASELINE|20260821|1",
        context={"canonical_authority_id": "ghost|MNQ|Long|BASELINE|20260821|1"},
    ))
    simulator = coordinator.submit(_request(
        source_system="dual_mode_sim",
        source_event_id="SCALP|MNQ|Long|20260821:1:1",
        strategy_name="DUAL_SCALP",
        experiment_variant="SCALP",
        context={
            "legacy_record_id": "SCALP|MNQ|Long|20260821:1:1",
            "legacy_sim_key": "SCALP|MNQ|Long|20260821:1:1",
            "canonical_authority_id": "ghost|MNQ|Long|BASELINE|20260821|1",
        },
    ))

    assert generic.market_opportunity_id == simulator.market_opportunity_id
    assert generic.observation_id != simulator.observation_id
    report = coordinator.report()
    assert report["cross_system_match_count"] == 1


def test_explicit_anchor_never_cross_links_other_instruments():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    generic = coordinator.submit(_request(
        source_event_id="ghost|MNQ|Long|BASELINE|20260821|1",
        context={"canonical_authority_id": "ghost|MNQ|Long|BASELINE|20260821|1"},
    ))
    other_instrument = coordinator.submit(_request(
        source_system="dual_mode_sim",
        source_event_id="SCALP|MGC|Long|20260821:1:1",
        instrument="MGC",
        strategy_name="DUAL_SCALP",
        experiment_variant="SCALP",
        context={
            "legacy_record_id": "SCALP|MGC|Long|20260821:1:1",
            "legacy_sim_key": "SCALP|MGC|Long|20260821:1:1",
            "canonical_authority_id": "ghost|MNQ|Long|BASELINE|20260821|1",
        },
    ))

    assert generic.market_opportunity_id != other_instrument.market_opportunity_id
    assert coordinator.report()["cross_system_match_count"] == 0


def test_only_dual_mode_sim_can_use_the_generic_authority_anchor():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    generic = coordinator.submit(_request(
        source_event_id="ghost|MNQ|Long|BASELINE|20260821|1",
        context={"canonical_authority_id": "irrelevant-for-generic"},
    ))
    foreign = coordinator.submit(_request(
        source_system="scalp_live_sim",
        source_event_id="paper|MNQ|Long|20260821|1",
        strategy_name="PAPER_SCALP",
        context={
            "legacy_record_id": "paper|MNQ|Long|20260821|1",
            "canonical_authority_id": "ghost|MNQ|Long|BASELINE|20260821|1",
        },
    ))

    assert generic.market_opportunity_id != foreign.market_opportunity_id
    assert coordinator.report()["cross_system_match_count"] == 0


def test_generic_anchor_mismatch_is_ignored_not_used_as_a_market_identity():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    generic = coordinator.submit(_request(
        source_event_id="ghost|MNQ|Long|BASELINE|20260821|1",
        context={"canonical_authority_id": "wrong-generic-id"},
    ))
    simulator = coordinator.submit(_request(
        source_system="dual_mode_sim",
        source_event_id="SCALP|MNQ|Long|20260821:1:1",
        strategy_name="DUAL_SCALP",
        experiment_variant="SCALP",
        context={
            "legacy_record_id": "SCALP|MNQ|Long|20260821:1:1",
            "legacy_sim_key": "SCALP|MNQ|Long|20260821:1:1",
            "canonical_authority_id": "wrong-generic-id",
        },
    ))

    assert generic.market_opportunity_id != simulator.market_opportunity_id
    assert coordinator.report()["cross_system_match_count"] == 0


def test_dual_anchor_requires_matching_legacy_sim_key_not_legacy_record_id():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    generic = coordinator.submit(_request(
        source_event_id="ghost|MNQ|Long|BASELINE|20260821|1",
    ))
    simulator = coordinator.submit(_request(
        source_system="dual_mode_sim",
        source_event_id="SCALP|MNQ|Long|20260821:1:1",
        strategy_name="DUAL_SCALP",
        experiment_variant="SCALP",
        context={
            "legacy_record_id": "SCALP|MNQ|Long|20260821:1:1",
            "legacy_sim_key": "different-sim-key",
            "canonical_authority_id": "ghost|MNQ|Long|BASELINE|20260821|1",
        },
    ))

    assert generic.market_opportunity_id != simulator.market_opportunity_id
    assert coordinator.report()["cross_system_match_count"] == 0


def test_short_geometry_and_malformed_geometry_are_handled_safely():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    short = coordinator.submit(_request(direction="Short", entry=21000, stop=21010, targets=(20980,)))
    malformed = coordinator.submit(_request(entry=21000, stop=21000))
    assert short.accepted is True
    assert malformed.accepted is False
    report = coordinator.report()
    assert report["malformed_or_rejected"] == 1
    assert report["source_systems"][0]["errors"] == 1


def test_nontrade_visual_event_never_creates_a_trade_opportunity():
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    result = coordinator.record_observational_event("visual_brain", "MNQ|image-1")
    assert result.accepted is True
    report = coordinator.report()
    assert report["visual_or_nontrade_events"] == 1
    assert report["unique_market_opportunities"] == 0


def test_opt_in_persistence_and_restore_keep_shadow_evidence():
    writes = []
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    coordinator.configure(
        enabled=True, persistence_enabled=True,
        persist_fn=lambda kind, record: writes.append((kind, dict(record))) or True,
    )
    accepted = coordinator.submit(_request())
    coordinator.record_observational_event("visual_brain", "MNQ|image-1")
    assert accepted.accepted is True
    assert [kind for kind, _ in writes] == ["observation", "telemetry"]

    restored = gc.CentralGhostCoordinator(enabled=False)
    assert restored.restore([writes[0][1]]) == 1
    assert restored.restore_telemetry([writes[1][1]]) == 1
    report = restored.report()
    assert report["unique_observations"] == 1
    assert report["visual_or_nontrade_events"] == 1
    assert report["restored_observations"] == 1


def test_intake_only_reconfigure_does_not_disable_existing_persistence():
    persisted = []
    coordinator = gc.CentralGhostCoordinator(enabled=False)
    coordinator.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda kind, row: persisted.append((kind, row)) or True,
    )
    coordinator.configure(enabled=True)

    result = coordinator.submit(_request())

    assert result.accepted is True
    assert coordinator.report()["persistence_enabled"] is True
    assert [kind for kind, _ in persisted] == ["observation"]


def test_route_fans_out_once_per_destination_and_filters_sources():
    delivered = []
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    coordinator.register_delivery("generic-ledger", lambda row: delivered.append(("generic", row)),
                                  sources=("generic_ghost",))
    coordinator.register_delivery("all-research", lambda row: delivered.append(("all", row)))

    first, deliveries = coordinator.route(_request())
    second, duplicate_deliveries = coordinator.route(_request())

    assert first.accepted is True
    assert [item.destination for item in deliveries] == ["generic-ledger", "all-research"]
    assert all(item.delivered for item in deliveries)
    assert second.duplicate is True
    assert duplicate_deliveries == ()
    assert [name for name, _ in delivered] == ["generic", "all"]
    report = coordinator.report()
    assert report["delivery_attempts"] == 2
    assert report["delivery_successes"] == 2
    assert report["delivery_failures"] == 0


def test_route_isolates_delivery_failure_and_does_not_reject_intake():
    delivered = []
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    coordinator.register_delivery("broken", lambda _: (_ for _ in ()).throw(RuntimeError("sink down")))
    coordinator.register_delivery("healthy", lambda row: delivered.append(row["observation_id"]))

    result, deliveries = coordinator.route(_request())

    assert result.accepted is True
    assert [item.destination for item in deliveries] == ["broken", "healthy"]
    assert deliveries[0].error == "sink down"
    assert deliveries[1].delivered is True
    assert len(delivered) == 1
    report = coordinator.report()
    assert report["unique_observations"] == 1
    assert report["delivery_failures"] == 1
    assert report["delivery_successes"] == 1


def test_telemetry_route_fans_out_without_creating_trade_geometry():
    delivered = []
    coordinator = gc.CentralGhostCoordinator(enabled=True)
    coordinator.register_delivery("visual-receipt", lambda row: delivered.append(row),
                                  sources=("visual_brain",))

    result, deliveries = coordinator.route_observational_event("visual_brain", "MNQ|image-1")

    assert result.accepted is True
    assert deliveries[0].delivered is True
    assert delivered[0]["kind"] == "telemetry"
    assert coordinator.report()["unique_market_opportunities"] == 0


def test_disabled_router_never_delivers():
    delivered = []
    coordinator = gc.CentralGhostCoordinator(enabled=False)
    coordinator.register_delivery("should-not-run", lambda row: delivered.append(row))

    result, deliveries = coordinator.route(_request())

    assert result.ignored is True
    assert deliveries == ()
    assert delivered == []
    assert coordinator.report()["delivery_attempts"] == 0


def test_coordinator_cannot_reach_execution_code():
    tree = ast.parse(Path(gc.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "app" not in imported
    assert not ({"traderspost", "pickmytrade", "broker", "execution", "gateway", "psycopg2"} & imported)