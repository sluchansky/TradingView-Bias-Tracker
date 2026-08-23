#!/usr/bin/env python3
"""Bounded, no-network smoke for Canonical Ghost projection health.

This exercises the existing shadow-only authority and evidence projection with
an in-memory durable sink.  It never imports the application, opens a database,
starts a workflow, evaluates a trade, or reaches an execution path.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from canonical_ghost_authority import CanonicalGhostAuthority
from canonical_ghost_evidence import CanonicalGhostEvidence


def submission(mode: str, suffix: str) -> dict:
    identity = f"smoke|MNQ|Long|{mode}|{suffix}"
    return {
        "observation_id": f"coord-{suffix}",
        "market_opportunity_id": f"market-{suffix}",
        "source_system": "generic_ghost",
        "source_event_id": identity,
        "instrument": "MNQ",
        "timeframe": "1m",
        "setup_family": "STRICT_SETUP",
        "strategy_name": "SMOKE",
        "strategy_version": "smoke",
        "direction": "Long",
        "signal_time": datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc),
        "source_bar_time": "2026-08-23T14:30:00+00:00",
        "entry": 21000.0,
        "stop": 20990.0,
        "targets": (21020.0,),
        "context": {
            "trading_mode": mode,
            "legacy_obs_key": identity,
            "legacy_table": "ghost_observations",
        },
    }


def run() -> dict:
    authority_rows: list[dict] = []
    evidence_rows: list[dict] = []
    authority = CanonicalGhostAuthority(enabled=True)
    evidence = CanonicalGhostEvidence(enabled=True)
    authority.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: authority_rows.append(dict(row)) or True,
    )
    evidence.configure(
        enabled=True,
        persistence_enabled=True,
        persist_fn=lambda row: evidence_rows.append(dict(row)) or True,
    )

    source_records = {}
    for mode, suffix in (("SCALP", "scalp"), ("INTRADAY_TREND", "intraday")):
        record = submission(mode, suffix)
        observed = authority.observe_coordinator_submission(record)
        assert observed and observed["trading_mode"] == mode
        projected = evidence.observe_submission(record, observed)
        assert projected and projected["trading_mode"] == mode
        source_records[mode] = record

        # Replayed input must collapse to the same canonical observation/evidence.
        assert authority.observe_coordinator_submission(record)["event_id"] == observed["event_id"]
        assert evidence.observe_submission(record, observed)["evidence_id"] == projected["evidence_id"]

        outcome = authority.observe_legacy_outcome(
            source_system="generic_ghost",
            source_record_id=record["source_event_id"],
            raw_status="WIN",
            close_reason="TARGET_1",
            gross_r=1.0,
            net_r=0.9,
            result_r=0.9,
            event_at="2026-08-23T14:35:00+00:00",
        )
        assert outcome and outcome["trading_mode"] == mode
        assert evidence.observe_outcome(outcome)["result_state"] == "TERMINAL"

    restarted_authority = CanonicalGhostAuthority(enabled=True)
    restarted_evidence = CanonicalGhostEvidence(enabled=True)
    assert restarted_authority.restore(authority_rows) == len(authority_rows)
    assert restarted_evidence.restore(evidence_rows) == len(evidence_rows)

    for mode, record in source_records.items():
        replay = restarted_authority.observe_coordinator_submission(record)
        assert replay and replay["trading_mode"] == mode
        assert restarted_evidence.observe_submission(record, replay)

    authority_report = restarted_authority.report()
    evidence_report = restarted_evidence.report()
    assert authority_report["unique_canonical_observations"] == 2
    assert evidence_report["records"] == 2
    assert evidence_report["unmatched_records"] == 0
    assert evidence_report["pending_records"] == 0
    assert evidence_report["errors"] == 0
    assert evidence_report["persistence_errors"] == 0
    for mode in ("SCALP", "INTRADAY_TREND"):
        assert evidence_report["by_mode"][mode]["records"] == 1
        assert evidence_report["by_mode"][mode]["terminal_records"] == 1

    classifier = CanonicalGhostEvidence(enabled=True)
    valid_unmatched = classifier.observe_unmatched(
        submission("SCALP", "classified-unmatched"),
        reason="canonical_authority_observation_unmatched",
    )
    assert valid_unmatched and valid_unmatched["trading_mode"] == "SCALP"
    assert classifier.observe_unmatched(
        {
            "source_system": "scalp_live_sim",
            "source_event_id": "noncanonical",
            "context": {"trading_mode": "SWING"},
        },
        reason="canonical_authority_observation_unmatched",
    ) is None
    classification_report = classifier.report()
    assert classification_report["unmatched_records"] == 1
    assert classification_report["errors"] == 0
    assert classification_report["unsupported_mode_rejections"] == 1

    return {
        "authority_records": authority_report["unique_canonical_observations"],
        "evidence_records": evidence_report["records"],
        "modes": {
            mode: evidence_report["by_mode"][mode]
            for mode in ("SCALP", "INTRADAY_TREND")
        },
        "pending_records": evidence_report["pending_records"],
        "projection_errors": evidence_report["errors"],
        "classification_control": {
            "valid_unmatched_records": classification_report["unmatched_records"],
            "unsupported_mode_rejections": classification_report[
                "unsupported_mode_rejections"
            ],
            "projection_errors": classification_report["errors"],
        },
    }


if __name__ == "__main__":
    print(run())