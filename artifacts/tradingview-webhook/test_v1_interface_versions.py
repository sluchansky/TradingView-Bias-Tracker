"""test_v1_interface_versions.py — V1 Phase 1 + V1-P6 Interface Version Contract Tests.

Verifies that each proven-canonical component interface output carries the
correct _version field, that all ARCH §7 required fields remain present, and
that each field has the correct *semantic meaning* (not just presence).

Interface status (current):
  V1-P1-001  Expert             v1   COMPLETE   full_analysis() result
  V1-P1-002  Left Brain         v2   COMPLETE   _neutral_thesis() + compute_left_brain_thesis()
  V1-P1-003  Partner            v1   COMPLETE   compute_main_brain() + _main_brain_neutral()
  V1-P1-004  Manager            v1   COMPLETE   build_manager_interface()
  V1-P1-005  Execution Gateway  v1   COMPLETE   execute_trade_gateway() success returns
  V1-P1-006  Journal            v1   COMPLETE   _build_card_entry() entry dict
  V1-P1-007  Coach              v1   COMPLETE   build_coach_interface()

Semantic correctness audit (after 4e322c8):
  weight_updated:   LEARNING_ANALYTICS["updated_at"] (recompute completion timestamp),
                    NOT LEARNING_ANALYTICS["ready"] (which is total_trades > 0).
  thesis_resolved:  False during ordinary full_analysis() — no thesis resolution
                    event occurs outside trade-close; no global "last resolve ran"
                    flag exists; THESIS_TRACKER_DB_READY is NOT the right source.
  active_trade:     Shallow-copied before return — consumers cannot mutate global state.
  managed_trade:    Shallow-copied before return — consumers cannot mutate global state.

Isolation invariants:
  _active_trade_mgmt_block() must NOT carry _version (display helper, not Manager)
  result["learning_score_influence"] must NOT carry _version (edge modifier, not Coach)
"""
import json
import os
import sys
import importlib
import unittest.mock

# ---------------------------------------------------------------------------
# Bootstrap — same pattern as test_brain_contract.py
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(__file__))
import app
importlib.reload(app)

import left_brain_market_intelligence as lb
importlib.reload(lb)

_SRC_PATH = os.path.join(os.path.dirname(__file__), "app.py")

def _app_src():
    with open(_SRC_PATH) as f:
        return f.read()


# ===========================================================================
# V1-P1-002  LEFT BRAIN  (v2)
# ===========================================================================

def test_lb_neutral_thesis_version():
    """_neutral_thesis() must carry _version == 'v2'."""
    thesis = lb._neutral_thesis("MGC")
    assert isinstance(thesis, dict), "_neutral_thesis must return a dict"
    assert thesis.get("_version") == "v2", (
        f"_neutral_thesis _version {thesis.get('_version')!r} != 'v2'")


def test_lb_neutral_thesis_required_fields():
    """_neutral_thesis() must contain all ARCH §7 guaranteed fields."""
    thesis = lb._neutral_thesis("MGC")
    for field in ("available", "direction", "narrative", "stability", "timeline"):
        assert field in thesis, f"_neutral_thesis missing required field: {field!r}"


def test_lb_compute_thesis_degraded_version():
    """compute_left_brain_thesis() degraded path (mi unavailable) must carry _version == 'v2'."""
    out = lb.compute_left_brain_thesis("MGC", {"available": False}, None, None, [])
    thesis = out.get("thesis", {})
    assert thesis.get("_version") == "v2", (
        f"compute_left_brain_thesis degraded thesis _version {thesis.get('_version')!r} != 'v2'")


def test_lb_compute_thesis_error_path_version():
    """compute_left_brain_thesis() error fallback must still carry _version == 'v2'.

    Passing None as mi triggers the fallback to _neutral_thesis.
    """
    out = lb.compute_left_brain_thesis("MNQ", None, None, None, [])
    thesis = out.get("thesis", {})
    assert thesis.get("_version") == "v2", (
        f"compute_left_brain_thesis error-path thesis _version {thesis.get('_version')!r} != 'v2'")


def test_lb_version_serializes():
    """Left Brain thesis must round-trip through JSON without losing _version."""
    thesis = lb._neutral_thesis("MGC")
    loaded = json.loads(json.dumps(thesis))
    assert loaded.get("_version") == "v2", "Left Brain _version lost after JSON round-trip"


# ===========================================================================
# V1-P1-001  EXPERT  (v1)
# ===========================================================================

def test_expert_version():
    """full_analysis() must carry _version == 'v1'."""
    result = app.full_analysis()
    assert isinstance(result, dict), "full_analysis must return a dict"
    assert result.get("_version") == "v1", (
        f"full_analysis _version {result.get('_version')!r} != 'v1'")


def test_expert_required_fields():
    """full_analysis() must contain all ARCH §7 Expert guaranteed fields."""
    result = app.full_analysis()
    for field in ("verdict", "edge_score", "strict_reason",
                  "gate_debug", "trade_plan", "alert_diagnostics"):
        assert field in result, f"full_analysis missing required Expert field: {field!r}"


def test_expert_version_type():
    """full_analysis() _version must be a string."""
    result = app.full_analysis()
    assert isinstance(result.get("_version"), str), (
        f"full_analysis _version must be str, got {type(result.get('_version'))}")


def test_expert_version_serializes():
    """full_analysis() _version must survive JSON serialization."""
    result = app.full_analysis()
    assert json.dumps({"_version": result.get("_version")}) == '{"_version": "v1"}'


# ===========================================================================
# V1-P1-003  PARTNER  (v1)
# ===========================================================================

def test_partner_neutral_version():
    """_main_brain_neutral() must carry _version == 'v1'."""
    mb = app._main_brain_neutral()
    assert isinstance(mb, dict), "_main_brain_neutral must return a dict"
    assert mb.get("_version") == "v1", (
        f"_main_brain_neutral _version {mb.get('_version')!r} != 'v1'")


def test_partner_neutral_required_fields():
    """_main_brain_neutral() must contain all ARCH §7 Partner guaranteed fields."""
    mb = app._main_brain_neutral()
    for field in ("status", "headline", "market_brain", "strategy_brain",
                  "risk_brain", "trade_manager", "favored_direction", "reason"):
        assert field in mb, f"_main_brain_neutral missing required field: {field!r}"


def test_partner_compute_version():
    """compute_main_brain() must carry _version == 'v1'."""
    result = app.full_analysis()
    mb = app.compute_main_brain(result)
    assert isinstance(mb, dict), "compute_main_brain must return a dict"
    assert mb.get("_version") == "v1", (
        f"compute_main_brain _version {mb.get('_version')!r} != 'v1'")


def test_partner_version_type():
    """Partner _version must be a string in both paths."""
    assert isinstance(app._main_brain_neutral().get("_version"), str), (
        "Partner neutral _version must be str")
    assert isinstance(app.compute_main_brain(app.full_analysis()).get("_version"), str), (
        "Partner compute _version must be str")


# ===========================================================================
# V1-P1-005  EXECUTION GATEWAY  (v1)  — source inspection
# ===========================================================================

def _gateway_fn_src():
    src = _app_src()
    start = src.find("def execute_trade_gateway(")
    assert start >= 0, "execute_trade_gateway not found in app.py"
    next_def = src.find("\ndef ", start + 1)
    end = next_def if (0 < next_def - start < 200_000) else start + 55_000
    return src[start:end]


def test_gateway_manual_required_version_in_source():
    gw = _gateway_fn_src()
    assert '"status": "manual_required"' in gw
    idx = gw.find('"status": "manual_required"')
    assert '"_version": "v1"' in gw[idx:idx + 400]


def test_gateway_simulated_version_in_source():
    gw = _gateway_fn_src()
    assert '"status": "simulated"' in gw
    idx = gw.find('"status": "simulated"')
    assert '"_version": "v1"' in gw[idx:idx + 400]


def test_gateway_sent_version_in_source():
    gw = _gateway_fn_src()
    assert '"status": "sent"' in gw
    idx = gw.find('"status": "sent"')
    assert '"_version": "v1"' in gw[idx:idx + 400]


def test_gateway_version_count():
    gw = _gateway_fn_src()
    count = gw.count('"_version": "v1"')
    assert count == 3, f"execute_trade_gateway must have 3 _version insertions, found {count}"


def test_gateway_version_not_in_broker_payload():
    src = _app_src()
    for fn_name in ("def adapt_traderspost(", "def adapt_pickmytrade("):
        start = src.find(fn_name)
        if start < 0:
            continue
        assert '"_version"' not in src[start:start + 2000]


# ===========================================================================
# V1-P1-006  JOURNAL  (v1)  — source inspection
# ===========================================================================

def _journal_fn_src():
    src = _app_src()
    start = src.find("def _build_card_entry(")
    assert start >= 0
    next_def = src.find("\ndef ", start + 1)
    end = next_def if (0 < next_def - start < 50_000) else start + 12_000
    return src[start:end]


def test_journal_version_in_source():
    assert 'entry["_version"] = "v1"' in _journal_fn_src()


def test_journal_version_before_return():
    fn_src = _journal_fn_src()
    vi = fn_src.find('entry["_version"] = "v1"')
    ri = fn_src.find("return entry", vi)
    assert vi >= 0 and ri >= 0 and vi < ri


def test_journal_version_single_seam():
    assert _journal_fn_src().count('entry["_version"]') == 1


# ===========================================================================
# V1-P1-004  MANAGER  (v1) — COMPLETE
#
# Structural tests: required fields, types, version.
# Semantic tests: instrument scoping, mutability isolation, training_gate meaning,
#                 auto_trade_enabled type, no execution side effects.
# ===========================================================================

def test_manager_build_returns_dict():
    result = app.full_analysis()
    assert isinstance(app.build_manager_interface(result), dict)


def test_manager_version():
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    assert mgr.get("_version") == "v1"


def test_manager_version_type():
    assert isinstance(app.build_manager_interface(app.full_analysis()).get("_version"), str)


def test_manager_required_fields():
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    for field in ("gateway_debug", "active_trade", "managed_trade",
                  "training_gate", "auto_trade_enabled"):
        assert field in mgr, f"build_manager_interface missing field: {field!r}"


def test_manager_gateway_debug_is_dict():
    assert isinstance(app.build_manager_interface(app.full_analysis()).get("gateway_debug"), dict)


def test_manager_active_trade_type():
    at = app.build_manager_interface(app.full_analysis()).get("active_trade")
    assert at is None or isinstance(at, dict)


def test_manager_managed_trade_type():
    mt = app.build_manager_interface(app.full_analysis()).get("managed_trade")
    assert mt is None or isinstance(mt, dict)


def test_manager_training_gate_has_enabled():
    tg = app.build_manager_interface(app.full_analysis()).get("training_gate") or {}
    assert "enabled" in tg


def test_manager_auto_trade_enabled_is_dict():
    assert isinstance(app.build_manager_interface(app.full_analysis()).get("auto_trade_enabled"), dict)


def test_manager_version_serializes():
    mgr = app.build_manager_interface(app.full_analysis())
    assert json.dumps({"_version": mgr.get("_version")}) == '{"_version": "v1"}'


def test_manager_in_full_analysis():
    result = app.full_analysis()
    assert "manager" in result
    assert isinstance(result["manager"], dict)
    assert result["manager"].get("_version") == "v1"


# -----------------------------------------------------------------
# SEMANTIC: instrument scoping is strict (no cross-instrument leak)
# -----------------------------------------------------------------

def test_manager_active_trade_scoped_to_requested_instrument():
    """With no active trade for MGC and an injected trade for MNQ,
    build_manager_interface(result, 'MGC') must return active_trade=None.

    Proves instrument scoping is strict — no fallback to another instrument.
    """
    result = app.full_analysis()
    # Ensure no existing MGC trade interferes
    with app.ACTIVE_TRADES_LOCK:
        mgc_saved = app.ACTIVE_TRADES_BY_INST.pop("MGC", None)
        mnq_saved = app.ACTIVE_TRADES_BY_INST.pop("MNQ", None)
        # Inject a trade for MNQ only
        app.ACTIVE_TRADES_BY_INST["MNQ"] = {
            "instrument": "MNQ", "direction": "Long",
            "entry_price": 20000.0, "stop_loss": 19980.0,
        }
    try:
        mgr = app.build_manager_interface(result, "MGC")
        assert mgr["active_trade"] is None, (
            "active_trade must be None for MGC when only MNQ has a trade; "
            "no cross-instrument fallback allowed")
    finally:
        with app.ACTIVE_TRADES_LOCK:
            app.ACTIVE_TRADES_BY_INST.pop("MNQ", None)
            if mgc_saved is not None:
                app.ACTIVE_TRADES_BY_INST["MGC"] = mgc_saved
            if mnq_saved is not None:
                app.ACTIVE_TRADES_BY_INST["MNQ"] = mnq_saved


def test_manager_active_trade_no_fallback_to_other_instrument():
    """build_manager_interface requesting 'MNQ' must not return MGC trade.

    Confirms the None return is instrument-specific, not a global 'no trades' check.
    """
    result = app.full_analysis()
    with app.ACTIVE_TRADES_LOCK:
        mnq_saved = app.ACTIVE_TRADES_BY_INST.pop("MNQ", None)
        mgc_saved = app.ACTIVE_TRADES_BY_INST.pop("MGC", None)
        app.ACTIVE_TRADES_BY_INST["MGC"] = {
            "instrument": "MGC", "direction": "Short",
            "entry_price": 2700.0, "stop_loss": 2720.0,
        }
    try:
        mgr = app.build_manager_interface(result, "MNQ")
        assert mgr["active_trade"] is None, (
            "build_manager_interface for MNQ must not return MGC trade")
    finally:
        with app.ACTIVE_TRADES_LOCK:
            app.ACTIVE_TRADES_BY_INST.pop("MGC", None)
            if mgc_saved is not None:
                app.ACTIVE_TRADES_BY_INST["MGC"] = mgc_saved
            if mnq_saved is not None:
                app.ACTIVE_TRADES_BY_INST["MNQ"] = mnq_saved


# -----------------------------------------------------------------
# SEMANTIC: returned trade dicts are copies — cannot mutate globals
# -----------------------------------------------------------------

def test_manager_active_trade_is_copy_not_live_reference():
    """Mutating the returned active_trade dict must NOT affect global ACTIVE_TRADES_BY_INST.

    active_trade_snapshot() makes a shallow copy of the outer dict but inner trade
    dicts are shared references.  build_manager_interface must wrap in dict() so
    consumers cannot mutate global state.
    """
    result = app.full_analysis()
    original_trade = {"instrument": "MGC", "direction": "Long", "entry_price": 2700.0}
    with app.ACTIVE_TRADES_LOCK:
        saved = app.ACTIVE_TRADES_BY_INST.pop("MGC", None)
        app.ACTIVE_TRADES_BY_INST["MGC"] = original_trade
    try:
        mgr = app.build_manager_interface(result, "MGC")
        at = mgr.get("active_trade")
        assert at is not None, "Should have returned the injected trade"
        # Mutate the returned dict
        at["entry_price"] = 9999.0
        at["_poisoned"] = True
        # Verify the global is untouched
        with app.ACTIVE_TRADES_LOCK:
            live = app.ACTIVE_TRADES_BY_INST.get("MGC") or {}
        assert live.get("entry_price") != 9999.0, (
            "Mutating returned active_trade must not alter global ACTIVE_TRADES_BY_INST")
        assert "_poisoned" not in live, (
            "Injected key in returned active_trade must not appear in global state")
    finally:
        with app.ACTIVE_TRADES_LOCK:
            app.ACTIVE_TRADES_BY_INST.pop("MGC", None)
            if saved is not None:
                app.ACTIVE_TRADES_BY_INST["MGC"] = saved


def test_manager_managed_trade_is_copy_not_live_reference():
    """Mutating the returned managed_trade dict must NOT affect MANAGED_TRADES_BY_KEY.

    build_manager_interface must shallow-copy managed trades before returning.
    """
    result = app.full_analysis()
    _key = ("MGC", "Long", 2700.0, "2026-07-29")
    original_mt = {"instrument": "MGC", "direction": "Long",
                   "entry": 2700.0, "closed": False, "key": _key}
    saved = app.MANAGED_TRADES_BY_KEY.pop(_key, None)
    app.MANAGED_TRADES_BY_KEY[_key] = original_mt
    try:
        mgr = app.build_manager_interface(result, "MGC")
        mt = mgr.get("managed_trade")
        assert mt is not None, "Should have returned the injected managed trade"
        # Mutate
        mt["entry"] = 9999.0
        mt["_poisoned"] = True
        live = app.MANAGED_TRADES_BY_KEY.get(_key) or {}
        assert live.get("entry") != 9999.0, (
            "Mutating returned managed_trade must not alter global MANAGED_TRADES_BY_KEY")
        assert "_poisoned" not in live, (
            "Injected key in returned managed_trade must not appear in global state")
    finally:
        app.MANAGED_TRADES_BY_KEY.pop(_key, None)
        if saved is not None:
            app.MANAGED_TRADES_BY_KEY[_key] = saved


# -----------------------------------------------------------------
# SEMANTIC: auto_trade_enabled type and training_gate meaning
# -----------------------------------------------------------------

def test_manager_auto_trade_values_are_bools():
    """Every value in auto_trade_enabled must be a Python bool (not int, not None)."""
    mgr = app.build_manager_interface(app.full_analysis())
    auto = mgr.get("auto_trade_enabled", {})
    assert auto, "auto_trade_enabled must be non-empty (has instruments)"
    for inst, val in auto.items():
        assert isinstance(val, bool), (
            f"auto_trade_enabled[{inst!r}] = {val!r} is {type(val)} not bool")


def test_manager_auto_trade_covers_all_assets():
    """auto_trade_enabled must contain an entry for every registered ASSET."""
    mgr = app.build_manager_interface(app.full_analysis())
    auto = mgr.get("auto_trade_enabled", {})
    for inst in app.ASSETS:
        assert inst in auto, f"auto_trade_enabled missing instrument {inst!r}"


def test_manager_training_gate_meaning_is_arm_status_not_gate_verdict():
    """training_gate['enabled'] must match training_mode_enabled() — it is the arm
    status (env-var driven), not the per-call gate verdict from _training_gate().

    In the test environment TRAINING_MODE_ENABLED is unset → enabled = False.
    """
    mgr = app.build_manager_interface(app.full_analysis())
    tg = mgr.get("training_gate") or {}
    expected = app.training_mode_enabled()
    assert tg.get("enabled") == expected, (
        f"training_gate.enabled {tg.get('enabled')!r} != training_mode_enabled() {expected!r}")


# -----------------------------------------------------------------
# SEMANTIC: builder calls do not trigger execution or change state
# -----------------------------------------------------------------

def test_manager_builder_does_not_change_active_trade_count():
    """Calling build_manager_interface() must not add or remove active trades."""
    result = app.full_analysis()
    with app.ACTIVE_TRADES_LOCK:
        count_before = len(app.ACTIVE_TRADES_BY_INST)
    app.build_manager_interface(result)
    app.build_manager_interface(result)
    with app.ACTIVE_TRADES_LOCK:
        count_after = len(app.ACTIVE_TRADES_BY_INST)
    assert count_before == count_after, (
        f"Active trade count changed after builder calls: {count_before} → {count_after}")


def test_manager_builder_does_not_trigger_execution():
    """Calling build_manager_interface() must not touch the execution gateway.

    Verifies _TRADERSPOST_LAST (last broker call record) is unchanged before/after.
    """
    result = app.full_analysis()
    before = dict(app._TRADERSPOST_LAST)
    app.build_manager_interface(result)
    after = dict(app._TRADERSPOST_LAST)
    assert before == after, (
        "build_manager_interface must not alter _TRADERSPOST_LAST (execution gateway record)")


# Isolation invariant
def test_manager_atm_block_no_version():
    atm = app._active_trade_mgmt_block()
    if atm is None:
        return
    assert "_version" not in atm, "_active_trade_mgmt_block must not carry _version"


def test_manager_canonical_fields_absent_from_atm_block():
    atm = app._active_trade_mgmt_block()
    if atm is None:
        return
    for field in ("gateway_debug", "active_trade", "managed_trade",
                  "training_gate", "auto_trade_enabled"):
        assert field not in atm


# ===========================================================================
# V1-P1-007  COACH  (v1) — COMPLETE
#
# Structural tests: required fields, types, version.
# Semantic tests: weight_updated meaning, thesis_resolved meaning,
#                 learning_influence source, rule_engine_eligibility read-only.
# ===========================================================================

def test_coach_build_returns_dict():
    result = app.full_analysis()
    assert isinstance(app.build_coach_interface(result), dict)


def test_coach_version():
    result = app.full_analysis()
    assert app.build_coach_interface(result).get("_version") == "v1"


def test_coach_version_type():
    assert isinstance(app.build_coach_interface(app.full_analysis()).get("_version"), str)


def test_coach_required_fields():
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    for field in ("weight_updated", "thesis_resolved",
                  "learning_influence", "rule_engine_eligibility"):
        assert field in cch, f"build_coach_interface missing field: {field!r}"


def test_coach_weight_updated_is_bool():
    cch = app.build_coach_interface(app.full_analysis())
    assert isinstance(cch.get("weight_updated"), bool)


def test_coach_thesis_resolved_is_bool():
    cch = app.build_coach_interface(app.full_analysis())
    assert isinstance(cch.get("thesis_resolved"), bool)


def test_coach_learning_influence_is_float():
    li = app.build_coach_interface(app.full_analysis()).get("learning_influence")
    assert isinstance(li, float), f"learning_influence must be float, got {type(li)}"


def test_coach_learning_influence_range():
    li = app.build_coach_interface(app.full_analysis()).get("learning_influence", 0.0)
    assert -15.0 <= li <= 15.0, f"learning_influence {li} out of [-15, 15]"


def test_coach_rule_engine_eligibility_valid():
    elig = app.build_coach_interface(app.full_analysis()).get("rule_engine_eligibility")
    assert elig in {"GHOST_ONLY", "LIVE_ELIGIBLE", "DISABLED"}


def test_coach_version_serializes():
    cch = app.build_coach_interface(app.full_analysis())
    assert json.dumps({"_version": cch.get("_version")}) == '{"_version": "v1"}'


def test_coach_in_full_analysis():
    result = app.full_analysis()
    assert "coach" in result
    assert isinstance(result["coach"], dict)
    assert result["coach"].get("_version") == "v1"


# -----------------------------------------------------------------
# SEMANTIC: weight_updated — learning-ready ≠ weight_updated
# -----------------------------------------------------------------

def test_coach_learning_ready_does_not_imply_weight_updated():
    """LEARNING_ANALYTICS["ready"] = True must NOT cause weight_updated = True.

    "ready" means total_trades > 0 — it says nothing about whether the
    strategy_weights recompute (_recompute_learning) has ever run.
    The authoritative signal is LEARNING_ANALYTICS["updated_at"]; presence of
    "ready" = True without "updated_at" must produce weight_updated = False.
    """
    result = app.full_analysis()
    with app.LEARNING_LOCK:
        saved = dict(app.LEARNING_ANALYTICS)
        # Set ready=True, total_trades=50 — but NO updated_at
        app.LEARNING_ANALYTICS.clear()
        app.LEARNING_ANALYTICS.update({"enabled": True, "ready": True,
                                        "total_trades": 50})
    try:
        cch = app.build_coach_interface(result)
        assert cch["weight_updated"] is False, (
            "weight_updated must be False when LEARNING_ANALYTICS has no 'updated_at' "
            "(ready=True does not prove the recompute ran)")
    finally:
        with app.LEARNING_LOCK:
            app.LEARNING_ANALYTICS.clear()
            app.LEARNING_ANALYTICS.update(saved)


def test_coach_learning_enabled_does_not_imply_weight_updated():
    """LEARNING_DB_ENABLED = True must not imply weight_updated = True.

    At boot, LEARNING_ANALYTICS = {"enabled": True, "ready": False, "total_trades": 0}
    with no "updated_at".  weight_updated must be False until _recompute_learning()
    completes and sets "updated_at".
    """
    result = app.full_analysis()
    with app.LEARNING_LOCK:
        saved = dict(app.LEARNING_ANALYTICS)
        app.LEARNING_ANALYTICS.clear()
        # Boot state — no updated_at, enabled but not yet run
        app.LEARNING_ANALYTICS.update({"enabled": True, "ready": False, "total_trades": 0})
    try:
        cch = app.build_coach_interface(result)
        assert cch["weight_updated"] is False, (
            "weight_updated must be False when LEARNING_ANALYTICS has no 'updated_at' "
            "(DB-enabled alone does not prove the recompute ran)")
    finally:
        with app.LEARNING_LOCK:
            app.LEARNING_ANALYTICS.clear()
            app.LEARNING_ANALYTICS.update(saved)


def test_coach_insufficient_samples_do_not_imply_weight_updated():
    """Zero or insufficient trades must not produce weight_updated = True.

    "ready" is False when total_trades = 0; no "updated_at" → weight_updated = False.
    Proves sample sufficiency is not the criterion.
    """
    result = app.full_analysis()
    with app.LEARNING_LOCK:
        saved = dict(app.LEARNING_ANALYTICS)
        app.LEARNING_ANALYTICS.clear()
        app.LEARNING_ANALYTICS.update({"enabled": True, "ready": False,
                                        "total_trades": 0})
    try:
        cch = app.build_coach_interface(result)
        assert cch["weight_updated"] is False, (
            "weight_updated must be False when no trades and no 'updated_at'")
    finally:
        with app.LEARNING_LOCK:
            app.LEARNING_ANALYTICS.clear()
            app.LEARNING_ANALYTICS.update(saved)


def test_coach_weight_updated_false_when_recompute_not_run():
    """In the test environment no _recompute_learning() call has occurred.

    The default LEARNING_ANALYTICS has no 'updated_at' key, so weight_updated must be False.
    This is the direct, uncontrived test: the test environment has no DB, so no recompute ran.
    """
    # Test env: LEARNING_ANALYTICS = {"enabled": True, "ready": False, "total_trades": 0}
    # No "updated_at" field → weight_updated must be False.
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    with app.LEARNING_LOCK:
        has_updated_at = "updated_at" in app.LEARNING_ANALYTICS
    if not has_updated_at:
        assert cch["weight_updated"] is False, (
            "weight_updated must be False when LEARNING_ANALYTICS has no 'updated_at'")


def test_coach_recompute_event_sets_weight_updated_true():
    """Simulating a completed _recompute_learning() (setting 'updated_at') must
    cause weight_updated = True.

    This proves the correct authoritative source: the recompute completion timestamp,
    not DB readiness or trade count.
    """
    result = app.full_analysis()
    with app.LEARNING_LOCK:
        saved = dict(app.LEARNING_ANALYTICS)
        # Simulate a completed recompute: updated_at is the key signal
        app.LEARNING_ANALYTICS.clear()
        app.LEARNING_ANALYTICS.update({
            "enabled": True, "ready": True, "total_trades": 10,
            "updated_at": "2026-07-29T10:00:00+00:00",
        })
    try:
        cch = app.build_coach_interface(result)
        assert cch["weight_updated"] is True, (
            "weight_updated must be True when LEARNING_ANALYTICS contains 'updated_at' "
            "(the recompute-completion timestamp)")
    finally:
        with app.LEARNING_LOCK:
            app.LEARNING_ANALYTICS.clear()
            app.LEARNING_ANALYTICS.update(saved)


# -----------------------------------------------------------------
# SEMANTIC: thesis_resolved — DB readiness ≠ thesis resolved
# -----------------------------------------------------------------

def test_coach_thesis_db_readiness_does_not_imply_thesis_resolved():
    """THESIS_TRACKER_DB_READY = True must NOT cause thesis_resolved = True.

    DB readiness means the table exists and is accessible — it says nothing about
    whether a thesis_snapshots resolve event occurred.
    thesis_resolved must be False during ordinary full_analysis().
    """
    result = app.full_analysis()
    # Save and temporarily set to True to test isolation
    saved = app.THESIS_TRACKER_DB_READY
    try:
        app.THESIS_TRACKER_DB_READY = True
        cch = app.build_coach_interface(result)
        assert cch["thesis_resolved"] is False, (
            "thesis_resolved must be False even when THESIS_TRACKER_DB_READY = True; "
            "DB readiness ≠ thesis resolution event")
    finally:
        app.THESIS_TRACKER_DB_READY = saved


def test_coach_thesis_resolved_false_during_ordinary_analysis():
    """thesis_resolved must be False during full_analysis() regardless of thesis state.

    No thesis_snapshots resolve event occurs during full_analysis() — it only occurs
    during trade-close processing.  The ARCH defines False as 'resolve did not run.'
    No global 'last resolve ran' flag exists in the codebase.
    """
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    assert cch["thesis_resolved"] is False, (
        "thesis_resolved must be False during ordinary full_analysis() — "
        "no thesis resolution event occurs outside a trade-close")


def test_coach_active_thesis_does_not_imply_thesis_resolved():
    """An active (unresolved) in-memory thesis must NOT set thesis_resolved = True.

    THESIS_BY_INST tracks the CURRENT confidence-based thesis for an instrument;
    thesis_resolved refers to the thesis_snapshots DB resolution event, which is
    separate and only triggered at trade close.
    """
    result = app.full_analysis()
    # Inject a fake active thesis for MGC
    with app.THESIS_LOCK:
        saved_thesis = app.THESIS_BY_INST.get("MGC")
        app.THESIS_BY_INST["MGC"] = {
            "direction": "Long", "confidence": 75, "resolved": False,
            "narrative": "Bullish structure", "stability": 0.8,
        }
    try:
        cch = app.build_coach_interface(result, instrument="MGC")
        assert cch["thesis_resolved"] is False, (
            "thesis_resolved must be False even when an active thesis exists in THESIS_BY_INST; "
            "thesis_resolved refers to the thesis_snapshots DB resolution event, not in-memory state")
    finally:
        with app.THESIS_LOCK:
            if saved_thesis is not None:
                app.THESIS_BY_INST["MGC"] = saved_thesis
            else:
                app.THESIS_BY_INST.pop("MGC", None)


# -----------------------------------------------------------------
# SEMANTIC: learning_influence source and shape
# -----------------------------------------------------------------

def test_coach_learning_influence_matches_lsi_delta():
    """learning_influence must equal the active-direction delta from LSI block.

    _ls_dir_summary populates "delta" from gd.get("learning_score_delta", 0).
    The Coach must read this same value, not recompute it.
    """
    result = app.full_analysis()
    lsi = result.get("learning_score_influence") or {}
    long_d  = float((lsi.get("Long")  or {}).get("delta") or 0.0)
    short_d = float((lsi.get("Short") or {}).get("delta") or 0.0)
    expected = long_d if long_d != 0.0 else short_d
    cch = app.build_coach_interface(result)
    assert cch["learning_influence"] == expected, (
        f"learning_influence {cch['learning_influence']} != expected {expected} "
        f"(Long.delta={long_d}, Short.delta={short_d})")


def test_coach_learning_influence_not_a_nested_object():
    """learning_influence must be a scalar float, not the full LSI object.

    The ARCH specifies 'float: ±15 modifier for next edge score'.
    """
    cch = app.build_coach_interface(app.full_analysis())
    li = cch.get("learning_influence")
    assert isinstance(li, (int, float)) and not isinstance(li, bool), (
        f"learning_influence must be a numeric scalar, got {type(li)}: {li!r}")
    assert not isinstance(li, dict), "learning_influence must not be a nested object"


# -----------------------------------------------------------------
# SEMANTIC: rule_engine_eligibility is a cache read, not a recompute
# -----------------------------------------------------------------

def test_coach_rule_engine_eligibility_not_recalculated():
    """Calling build_coach_interface() twice must return consistent eligibility
    without changing LEARNING_ELIGIBILITY cache state.

    Proves the builder reads existing state rather than launching a background
    recompute or modifying the cache.
    """
    result = app.full_analysis()
    with app.LEARNING_ELIGIBILITY_LOCK:
        cache_before = dict(app.LEARNING_ELIGIBILITY)
    cch1 = app.build_coach_interface(result)
    cch2 = app.build_coach_interface(result)
    with app.LEARNING_ELIGIBILITY_LOCK:
        cache_after = dict(app.LEARNING_ELIGIBILITY)
    assert cch1["rule_engine_eligibility"] == cch2["rule_engine_eligibility"], (
        "Repeated calls must produce consistent rule_engine_eligibility")
    assert cache_before == cache_after, (
        "build_coach_interface must not modify LEARNING_ELIGIBILITY cache")


# -----------------------------------------------------------------
# SEMANTIC: repeated reads do not write to learning or thesis storage
# -----------------------------------------------------------------

def test_coach_repeated_reads_do_not_write():
    """Calling build_coach_interface() multiple times must not change any
    learning or thesis global state.

    Verifies: LEARNING_ANALYTICS unchanged, THESIS_BY_INST unchanged,
    STRATEGY_WEIGHTS unchanged, LEARNING_ELIGIBILITY unchanged.
    """
    result = app.full_analysis()

    with app.LEARNING_LOCK:
        la_before = dict(app.LEARNING_ANALYTICS)
        sw_before = dict(app.STRATEGY_WEIGHTS)

    with app.THESIS_LOCK:
        th_before = dict(app.THESIS_BY_INST)

    with app.LEARNING_ELIGIBILITY_LOCK:
        le_before = dict(app.LEARNING_ELIGIBILITY)

    for _ in range(3):
        app.build_coach_interface(result)

    with app.LEARNING_LOCK:
        assert dict(app.LEARNING_ANALYTICS) == la_before, (
            "build_coach_interface must not modify LEARNING_ANALYTICS")
        assert dict(app.STRATEGY_WEIGHTS) == sw_before, (
            "build_coach_interface must not modify STRATEGY_WEIGHTS")

    with app.THESIS_LOCK:
        assert dict(app.THESIS_BY_INST) == th_before, (
            "build_coach_interface must not modify THESIS_BY_INST")

    with app.LEARNING_ELIGIBILITY_LOCK:
        assert dict(app.LEARNING_ELIGIBILITY) == le_before, (
            "build_coach_interface must not modify LEARNING_ELIGIBILITY")


# Isolation invariants
def test_coach_lsi_no_version():
    lsi = app.full_analysis().get("learning_score_influence") or {}
    assert "_version" not in lsi, "learning_score_influence must not carry _version"


def test_coach_canonical_fields_absent():
    lsi = app.full_analysis().get("learning_score_influence") or {}
    for field in ("weight_updated", "thesis_resolved",
                  "learning_influence", "rule_engine_eligibility"):
        assert field not in lsi


def test_coach_lsi_existing_fields_intact():
    lsi = app.full_analysis().get("learning_score_influence") or {}
    for field in ("enabled", "armed", "max_delta"):
        assert field in lsi


# ===========================================================================
# CROSS-INTERFACE MATRIX — all 7 interfaces COMPLETE
# ===========================================================================

def test_cross_interface_version_matrix():
    """Full version matrix for all 7 V1-P1 interfaces — all COMPLETE.

    Also validates semantic isolation: non-canonical objects remain version-free.
    """
    failures = []

    # Left Brain: v2
    lb_thesis = lb._neutral_thesis("MGC")
    if lb_thesis.get("_version") != "v2":
        failures.append(f"Left Brain: expected v2, got {lb_thesis.get('_version')!r}")

    # Expert: v1
    fa = app.full_analysis()
    if fa.get("_version") != "v1":
        failures.append(f"Expert: expected v1, got {fa.get('_version')!r}")

    # Partner: v1 (both paths)
    mb_neutral = app._main_brain_neutral()
    if mb_neutral.get("_version") != "v1":
        failures.append(f"Partner neutral: expected v1, got {mb_neutral.get('_version')!r}")
    mb_compute = app.compute_main_brain(fa)
    if mb_compute.get("_version") != "v1":
        failures.append(f"Partner compute: expected v1, got {mb_compute.get('_version')!r}")

    # Manager: v1 (canonical builder) + isolation
    mgr = app.build_manager_interface(fa)
    if mgr.get("_version") != "v1":
        failures.append(f"Manager: expected v1, got {mgr.get('_version')!r}")
    if fa.get("manager", {}).get("_version") != "v1":
        failures.append("Manager: full_analysis result['manager']._version != v1")
    atm = app._active_trade_mgmt_block()
    if atm is not None and "_version" in atm:
        failures.append("Manager isolation: _active_trade_mgmt_block must NOT carry _version")

    # Execution Gateway: v1 in all 3 success returns (source)
    gw = _gateway_fn_src()
    gw_count = gw.count('"_version": "v1"')
    if gw_count < 3:
        failures.append(f"Execution Gateway: expected >=3 _version sites, found {gw_count}")

    # Journal: v1 in _build_card_entry (source)
    if 'entry["_version"] = "v1"' not in _journal_fn_src():
        failures.append("Journal: _build_card_entry missing _version v1")

    # Coach: v1 (canonical builder) + isolation
    cch = app.build_coach_interface(fa)
    if cch.get("_version") != "v1":
        failures.append(f"Coach: expected v1, got {cch.get('_version')!r}")
    if fa.get("coach", {}).get("_version") != "v1":
        failures.append("Coach: full_analysis result['coach']._version != v1")
    lsi = fa.get("learning_score_influence") or {}
    if "_version" in lsi:
        failures.append("Coach isolation: learning_score_influence must NOT carry _version")

    assert not failures, "Cross-interface version matrix failures:\n" + "\n".join(failures)


# ===========================================================================
# V1-P6-006  LEARNING-ENGINE RESILIENCE
#
# ROADMAP V1-P6-006: Coach-unavailable resilience.
#
# The learning subsystem is fail-open by design.  These tests provide the
# automated proof that an exception inside the learning block cannot cascade
# into a broken /status response or a false WAIT verdict.
#
# Strategy:
#   • Patch _recompute_learning to raise — confirms the background recompute is
#     decoupled from the synchronous evaluation path (full_analysis never calls
#     _recompute_learning inline; it only schedules it via _maybe_recompute_learning
#     → Thread, so a crash there cannot affect a live evaluation in progress).
#   • Corrupt LEARNING_ANALYTICS (set to None) — forces an AttributeError inside
#     build_coach_interface's internal LEARNING_ANALYTICS.get() call, exercising
#     the existing fail-open except block end-to-end.
#   • Both injection paths confirm: full_analysis() returns a valid Expert dict,
#     the Expert verdict and edge_score are unchanged, and /status returns HTTP 200.
# ===========================================================================

_V1_P6_006_EXPERT_FIELDS = ("verdict", "edge_score", "strict_reason",
                             "gate_debug", "trade_plan", "alert_diagnostics")


def test_v1_p6_006_recompute_exception_decoupled_from_full_analysis():
    """V1-P6-006 — _recompute_learning() raising must not affect full_analysis().

    _recompute_learning() is only ever called from a daemon Thread spawned by
    _maybe_recompute_learning().  A crash there is isolated to that thread;
    the synchronous full_analysis() eval path does not call it inline and must
    return a complete, valid Expert dict regardless.
    """
    with unittest.mock.patch.object(
        app, "_recompute_learning",
        side_effect=RuntimeError("DB unavailable — V1-P6-006 injection"),
    ):
        # The patch is on the module-level function; full_analysis does NOT call
        # _recompute_learning inline, so patching it must have zero effect.
        result = app.full_analysis()

    assert isinstance(result, dict), "_recompute_learning crash must not prevent a dict return"
    assert result.get("_version") == "v1", (
        "_version must still be 'v1' when _recompute_learning raises")
    for field in _V1_P6_006_EXPERT_FIELDS:
        assert field in result, (
            f"Expert field {field!r} missing after _recompute_learning exception")


def test_v1_p6_006_learning_analytics_corruption_does_not_break_full_analysis():
    """V1-P6-006 — corrupting LEARNING_ANALYTICS (→ None) must not crash full_analysis().

    Setting LEARNING_ANALYTICS to None forces an AttributeError on .get() inside
    build_coach_interface's lock block, exercising the fail-open except path.
    full_analysis must still return a dict with all Expert fields intact.
    """
    with app.LEARNING_LOCK:
        saved = dict(app.LEARNING_ANALYTICS)
        # Replace with None — causes AttributeError on LEARNING_ANALYTICS.get(...)
        app.LEARNING_ANALYTICS = None  # type: ignore[assignment]
    try:
        result = app.full_analysis()
        assert isinstance(result, dict), (
            "full_analysis must return a dict even when LEARNING_ANALYTICS is None")
        assert result.get("_version") == "v1", (
            "_version must still be 'v1' when LEARNING_ANALYTICS is corrupted")
        for field in _V1_P6_006_EXPERT_FIELDS:
            assert field in result, (
                f"Expert field {field!r} missing after LEARNING_ANALYTICS corruption")
    finally:
        with app.LEARNING_LOCK:
            app.LEARNING_ANALYTICS = saved  # type: ignore[assignment]


def test_v1_p6_006_coach_fail_open_when_analytics_raises():
    """V1-P6-006 — build_coach_interface fail-open returns a valid v1 dict when
    LEARNING_ANALYTICS.get() raises AttributeError.

    Directly tests the fail-open except branch: the returned dict must carry
    _version='v1' and all four Coach contract fields with safe types.
    """
    with app.LEARNING_LOCK:
        saved = dict(app.LEARNING_ANALYTICS)
        app.LEARNING_ANALYTICS = None  # type: ignore[assignment]
    try:
        result = app.full_analysis()
        cch = app.build_coach_interface(result)
        assert isinstance(cch, dict), "build_coach_interface must return a dict on exception"
        assert cch.get("_version") == "v1", (
            "fail-open path must still return _version='v1'")
        assert isinstance(cch.get("weight_updated"), bool), (
            "fail-open weight_updated must be bool")
        assert isinstance(cch.get("thesis_resolved"), bool), (
            "fail-open thesis_resolved must be bool")
        assert isinstance(cch.get("learning_influence"), (int, float)), (
            "fail-open learning_influence must be numeric")
        assert cch.get("rule_engine_eligibility") in {
            "GHOST_ONLY", "LIVE_ELIGIBLE", "DISABLED"
        }, "fail-open rule_engine_eligibility must be a valid sentinel"
    finally:
        with app.LEARNING_LOCK:
            app.LEARNING_ANALYTICS = saved  # type: ignore[assignment]


def test_v1_p6_006_expert_verdict_unchanged_when_learning_crashes():
    """V1-P6-006 — Expert verdict and edge_score must be identical whether or not
    the learning engine crashes.

    Baseline: full_analysis() with normal LEARNING_ANALYTICS.
    Crash path: full_analysis() with LEARNING_ANALYTICS = None.

    The learning subsystem's sole money-path effect is a bounded ±15 Edge Score
    nudge (LEARNING_SCORE_ENABLED flag, default OFF in tests).  With the flag off
    the learning nudge is 0 and the verdict must be byte-identical.  With the flag
    on, a crash falls back to 0 nudge — so the verdict can only be MORE conservative
    (never READY when baseline is WAIT).  Either way, the gate is not broken.

    We assert byte-identity here because the test environment has no DB and the
    learning score flag is OFF, so the crash produces zero delta.
    """
    # Baseline
    baseline = app.full_analysis()
    baseline_verdict    = baseline.get("verdict")
    baseline_edge_score = baseline.get("edge_score")

    # Corrupt LEARNING_ANALYTICS
    with app.LEARNING_LOCK:
        saved = dict(app.LEARNING_ANALYTICS)
        app.LEARNING_ANALYTICS = None  # type: ignore[assignment]
    try:
        crashed = app.full_analysis()
    finally:
        with app.LEARNING_LOCK:
            app.LEARNING_ANALYTICS = saved  # type: ignore[assignment]

    assert crashed.get("verdict") == baseline_verdict, (
        f"Expert verdict changed after learning crash: "
        f"{baseline_verdict!r} → {crashed.get('verdict')!r}; "
        "the gate must be unaffected by learning engine exceptions")
    assert crashed.get("edge_score") == baseline_edge_score, (
        f"edge_score changed after learning crash: "
        f"{baseline_edge_score!r} → {crashed.get('edge_score')!r}; "
        "edge scoring must be unaffected by learning engine exceptions")


def test_v1_p6_006_status_200_when_learning_analytics_corrupt():
    """V1-P6-006 — GET /status must return HTTP 200 even when LEARNING_ANALYTICS
    is corrupted (None).

    _learning_engine_view() is called inside the /status route; it guards its
    own LEARNING_ANALYTICS read.  If it or any other learning path raises, the
    response must still be a valid 200 (not a 500).
    """
    with app.LEARNING_LOCK:
        saved = dict(app.LEARNING_ANALYTICS)
        app.LEARNING_ANALYTICS = None  # type: ignore[assignment]
    try:
        client = app.app.test_client()
        resp = client.get("/status")
        assert resp.status_code == 200, (
            f"/status returned HTTP {resp.status_code} when LEARNING_ANALYTICS is None; "
            "expected 200 — the learning engine crash must not kill the status endpoint")
    finally:
        with app.LEARNING_LOCK:
            app.LEARNING_ANALYTICS = saved  # type: ignore[assignment]


def test_v1_p6_006_status_200_when_recompute_patched_to_raise():
    """V1-P6-006 — GET /status must return HTTP 200 even when _recompute_learning
    is patched to always raise RuntimeError.

    Confirms that the recompute being broken does not affect the /status response.
    """
    with unittest.mock.patch.object(
        app, "_recompute_learning",
        side_effect=RuntimeError("DB unavailable — V1-P6-006 injection"),
    ):
        client = app.app.test_client()
        resp = client.get("/status")
    assert resp.status_code == 200, (
        f"/status returned HTTP {resp.status_code} when _recompute_learning raises; "
        "expected 200")


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            failed += 1
    print("═" * 60)
    print(f"  TOTAL: {passed + failed} checks — {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
