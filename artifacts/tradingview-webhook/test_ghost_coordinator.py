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


def test_coordinator_cannot_reach_execution_code():
    tree = ast.parse(Path(gc.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "app" not in imported
    assert not ({"traderspost", "pickmytrade", "broker"} & imported)