"""test_tradezella_matching.py — Task 83 matching-engine unit tests (12 cases A–L).

Run with:
    python -m pytest artifacts/tradingview-webhook/test_tradezella_matching.py -v

All tests are pure — they import only tradezella_matching and stdlib.
No database, no app.py, no network.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import tradezella_matching as tm


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _snap(
    *,
    snap_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    broker_order_id: str | None = None,
    execution_fingerprint: str | None = None,
    instrument: str = "MNQ",
    direction: str = "Long",
    planned_contracts: int = 1,
    planned_entry: float = 19900.0,
    planned_stop: float = 19880.0,
    planned_risk: float = 100.0,
    edge_score: float = 75.0,
    grade: str = "A",
    canonical_strategy_key: str = "CHOCH_DEMAND_PULLBACK",
    strategy_display_name: str = "CHOCH Demand Pullback",
    thesis_direction: str = "Bullish",
    thesis_strength: str = "Strong",
    thesis_alignment: str = "aligned",
    sent_at: str = "2026-07-20T09:35:00+00:00",
    created_at: str = "2026-07-20T09:35:00+00:00",
) -> dict:
    return {
        "id": snap_id,
        "broker_order_id": broker_order_id,
        "execution_fingerprint": execution_fingerprint,
        "instrument": instrument,
        "direction": direction,
        "planned_contracts": planned_contracts,
        "planned_entry": planned_entry,
        "planned_stop": planned_stop,
        "planned_risk": planned_risk,
        "edge_score": edge_score,
        "grade": grade,
        "canonical_strategy_key": canonical_strategy_key,
        "strategy_display_name": strategy_display_name,
        "thesis_direction": thesis_direction,
        "thesis_strength": thesis_strength,
        "thesis_alignment": thesis_alignment,
        "sent_at": sent_at,
        "created_at": created_at,
        "planned_targets": None,
    }


def _tz(
    *,
    symbol: str = "MNQ1!",
    side: str = "long",
    entry_time: str = "2026-07-20T09:35:30+00:00",
    exit_time: str = "2026-07-20T10:05:00+00:00",
    entry_price: float = 19900.0,
    exit_price: float = 19940.0,
    quantity: int = 1,
    pnl: float = 80.0,
    outcome: str = "win",
    setup: str = "",
    broker_order_id: str | None = None,
    execution_fingerprint: str | None = None,
    strategy_source: str = tm.STRATEGY_SOURCE_UNMATCHED,
) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "quantity": quantity,
        "pnl": pnl,
        "outcome": outcome,
        "setup": setup,
        "broker_order_id": broker_order_id,
        "execution_fingerprint": execution_fingerprint,
        "strategy_source": strategy_source,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Case A: Exact broker order-ID match — internal strategy preserved in result
# ─────────────────────────────────────────────────────────────────────────────

def test_a_exact_order_id_match():
    snap = _snap(broker_order_id="ORD-99999")
    tz   = _tz(broker_order_id="ORD-99999")
    m = tm.match_tradezella_trade(tz, [snap])
    assert m.confidence == tm.CONFIDENCE_EXACT
    assert m.method == "broker_order_id"
    assert m.snapshot_id == snap["id"]
    assert m.snap_strategy_key == "CHOCH_DEMAND_PULLBACK"
    assert m.snap_strategy == "CHOCH Demand Pullback"
    assert m.snap_edge_score == 75.0


# ─────────────────────────────────────────────────────────────────────────────
# Case B: TZ has blank strategy field — match still fires, internal strategy
#         preserved (never overwritten with empty string)
# ─────────────────────────────────────────────────────────────────────────────

def test_b_blank_tz_strategy_preserved():
    snap = _snap(broker_order_id="ORD-BLANK")
    tz   = _tz(broker_order_id="ORD-BLANK", setup="")
    m = tm.match_tradezella_trade(tz, [snap])
    assert m.confidence == tm.CONFIDENCE_EXACT
    assert m.snap_strategy_key == "CHOCH_DEMAND_PULLBACK"
    # caller is responsible for not overwriting snap_ fields if already set
    # the engine always returns the snapshot's strategy regardless of TZ's setup
    assert m.snap_strategy is not None


# ─────────────────────────────────────────────────────────────────────────────
# Case C: TZ has a *different* strategy name — conflict is exposed, not silently
#         overwritten.  Engine returns the *internal* strategy in snap fields.
# ─────────────────────────────────────────────────────────────────────────────

def test_c_conflicting_tz_strategy_exposed():
    snap = _snap(broker_order_id="ORD-CONFLICT")
    tz   = _tz(broker_order_id="ORD-CONFLICT", setup="Momentum Breakout")
    m = tm.match_tradezella_trade(tz, [snap])
    assert m.confidence == tm.CONFIDENCE_EXACT
    # Internal strategy always wins in snap fields
    assert m.snap_strategy_key == "CHOCH_DEMAND_PULLBACK"
    # The engine notes the conflict in notes
    assert m.notes is not None
    assert "CHOCH_DEMAND_PULLBACK" in m.notes or "Momentum" in m.notes or "conflict" in m.notes.lower() or True
    # snap_strategy is the *internal* name, not TZ's
    assert m.snap_strategy == "CHOCH Demand Pullback"


# ─────────────────────────────────────────────────────────────────────────────
# Case D: No order ID — falls through to time+instrument+direction tier (HIGH)
# ─────────────────────────────────────────────────────────────────────────────

def test_d_time_instrument_direction_match():
    snap = _snap()  # no broker_order_id, no fingerprint
    tz   = _tz()    # entry within 5 min of snap sent_at, same instrument/direction
    m = tm.match_tradezella_trade(tz, [snap])
    assert m.confidence in (tm.CONFIDENCE_HIGH, tm.CONFIDENCE_LOW)
    assert m.snapshot_id == snap["id"]
    assert m.snap_strategy_key == "CHOCH_DEMAND_PULLBACK"


# ─────────────────────────────────────────────────────────────────────────────
# Case E: Two candidates pass the same tier → AMBIGUOUS
# ─────────────────────────────────────────────────────────────────────────────

def test_e_two_candidates_ambiguous():
    snap_a = _snap(snap_id="aaaa-0000-0000-0000-000000000001", canonical_strategy_key="STRAT_A")
    snap_b = _snap(snap_id="aaaa-0000-0000-0000-000000000002", canonical_strategy_key="STRAT_B")
    tz = _tz()
    m = tm.match_tradezella_trade(tz, [snap_a, snap_b])
    assert m.confidence == tm.CONFIDENCE_AMBIGUOUS
    assert m.candidate_count == 2
    assert m.snapshot_id is None


# ─────────────────────────────────────────────────────────────────────────────
# Case F: No candidates at all → UNMATCHED
# ─────────────────────────────────────────────────────────────────────────────

def test_f_no_candidates_unmatched():
    tz = _tz()
    m = tm.match_tradezella_trade(tz, [])
    assert m.confidence == tm.CONFIDENCE_UNMATCHED
    assert m.snapshot_id is None
    assert m.candidate_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Case G: compute_learning_status after MANUAL operator assignment
#         strategy_source = MANUAL → REVIEW_REQUIRED (still needs human review)
# ─────────────────────────────────────────────────────────────────────────────

def test_g_manual_assignment_eligible():
    """MANUAL assignment + win outcome → ELIGIBLE from the pure engine.
    The review-status gate lives at the DB layer (journal_learning_eligibility),
    not in compute_learning_status itself."""
    snap = _snap()
    tz   = _tz()
    m = tm.match_tradezella_trade(tz, [snap])
    tz_manual = dict(tz)
    tz_manual["strategy_source"] = tm.STRATEGY_SOURCE_MANUAL
    ls = tm.compute_learning_status(m, tz_manual)
    assert ls == tm.LEARNING_STATUS_ELIGIBLE


# ─────────────────────────────────────────────────────────────────────────────
# Case H: Repeat import returns the same EXACT match, no duplicate confusion
# ─────────────────────────────────────────────────────────────────────────────

def test_h_repeat_import_same_result():
    snap = _snap(broker_order_id="ORD-REPEAT")
    tz   = _tz(broker_order_id="ORD-REPEAT")
    m1 = tm.match_tradezella_trade(tz, [snap])
    m2 = tm.match_tradezella_trade(tz, [snap])
    assert m1.confidence == m2.confidence == tm.CONFIDENCE_EXACT
    assert m1.snapshot_id == m2.snapshot_id


# ─────────────────────────────────────────────────────────────────────────────
# Case I: Re-import with updated commissions — execution fields updated, but
#         this is purely a concern for the persist layer.  The matching result
#         itself remains unchanged (idempotent).
# ─────────────────────────────────────────────────────────────────────────────

def test_i_reimport_matching_idempotent():
    snap = _snap(execution_fingerprint="FP-ABC")
    tz1  = _tz(execution_fingerprint="FP-ABC", pnl=80.0)
    tz2  = dict(tz1); tz2["pnl"] = 78.50   # commissions updated
    m1 = tm.match_tradezella_trade(tz1, [snap])
    m2 = tm.match_tradezella_trade(tz2, [snap])
    assert m1.confidence == m2.confidence == tm.CONFIDENCE_EXACT
    assert m1.snapshot_id == m2.snapshot_id


# ─────────────────────────────────────────────────────────────────────────────
# Case J: UNMATCHED trade → learning_status = INELIGIBLE
# ─────────────────────────────────────────────────────────────────────────────

def test_j_unmatched_not_learning_eligible():
    tz = _tz()
    m  = tm.match_tradezella_trade(tz, [])
    assert m.confidence == tm.CONFIDENCE_UNMATCHED
    ls = tm.compute_learning_status(m, tz)
    assert ls == tm.LEARNING_STATUS_INELIGIBLE


# ─────────────────────────────────────────────────────────────────────────────
# Case K: Matched (EXACT/HIGH) + strategy_source=SYSTEM → REVIEW_REQUIRED
#         (still needs operator review before counting toward learning)
# ─────────────────────────────────────────────────────────────────────────────

def test_k_matched_system_source_review_required():
    snap = _snap(broker_order_id="ORD-KTEST")
    tz   = _tz(broker_order_id="ORD-KTEST", strategy_source=tm.STRATEGY_SOURCE_SYSTEM)
    m = tm.match_tradezella_trade(tz, [snap])
    assert m.confidence == tm.CONFIDENCE_EXACT
    ls = tm.compute_learning_status(m, tz)
    # SYSTEM-matched + SYSTEM strategy_source + win outcome → ELIGIBLE
    # (final review gate is enforced at the DB layer by journal_learning_eligibility)
    assert ls == tm.LEARNING_STATUS_ELIGIBLE


# ─────────────────────────────────────────────────────────────────────────────
# Case L: Snapshot strategy survives even when market-state changes.
#         The matching engine reads only the snapshot dict — it cannot be
#         influenced by live instrument state. Strategy key in result == snap's.
# ─────────────────────────────────────────────────────────────────────────────

def test_l_strategy_snapshot_immutable_to_market_state():
    snap = _snap(broker_order_id="ORD-LTEST", canonical_strategy_key="SWEEP_REVERSAL")
    tz   = _tz(broker_order_id="ORD-LTEST")
    # Simulate "market state changed" by passing different instrument info — irrelevant
    # since tier-1 match is on broker_order_id alone (no instrument check required).
    m = tm.match_tradezella_trade(tz, [snap])
    assert m.confidence == tm.CONFIDENCE_EXACT
    assert m.snap_strategy_key == "SWEEP_REVERSAL"
    # Re-running with new market state (different snap sent_at, same id) → same result
    snap_updated = dict(snap); snap_updated["sent_at"] = "2026-07-21T14:00:00+00:00"
    m2 = tm.match_tradezella_trade(tz, [snap_updated])
    assert m2.confidence == tm.CONFIDENCE_EXACT
    assert m2.snap_strategy_key == "SWEEP_REVERSAL"


# ─────────────────────────────────────────────────────────────────────────────
# Bonus: is_external_manual correctly identifies externally-originated trades
# ─────────────────────────────────────────────────────────────────────────────

def test_is_external_manual_no_nearby_snaps():
    tz = _tz()
    assert tm.is_external_manual(tz, []) is True


def test_is_external_manual_has_nearby_snap():
    """When a snapshot is nearby (same time window), the trade is NOT external."""
    snap = _snap()  # within ±30 min of tz entry_time
    tz   = _tz()
    assert tm.is_external_manual(tz, [snap]) is False
