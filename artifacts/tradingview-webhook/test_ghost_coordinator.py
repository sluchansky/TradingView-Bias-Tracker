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