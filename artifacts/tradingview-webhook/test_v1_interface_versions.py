"""test_v1_interface_versions.py — V1 Phase 1 Interface Version Contract Tests.

Verifies that each proven-canonical component interface output carries the
correct _version field and that all ARCH §7 required fields remain present.

Interface status (current):
  V1-P1-001  Expert             v1   COMPLETE   full_analysis() result
  V1-P1-002  Left Brain         v2   COMPLETE   _neutral_thesis() + compute_left_brain_thesis()
  V1-P1-003  Partner            v1   COMPLETE   compute_main_brain() + _main_brain_neutral()
  V1-P1-004  Manager            v1   COMPLETE   build_manager_interface()
  V1-P1-005  Execution Gateway  v1   COMPLETE   execute_trade_gateway() success returns
  V1-P1-006  Journal            v1   COMPLETE   _build_card_entry() entry dict
  V1-P1-007  Coach              v1   COMPLETE   build_coach_interface()

Isolation invariants for Manager and Coach:
  - _active_trade_mgmt_block() is a display helper — must NOT carry _version
  - result["learning_score_influence"] is the edge-scoring modifier block — must NOT carry _version
  Both non-canonical objects remain untouched by this batch.

No modifications to scoring, gate, execution, or safety logic anywhere in this file.
Tests are read-only consumers of the existing interfaces.
"""
import json
import os
import re
import sys
import importlib

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
    # Only serialize the version field to avoid any non-serializable objects in full payload
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
#
# execute_trade_gateway() requires broker configuration and a full trading
# context to call live. Source inspection is used as documented in the
# corrective validation report. It verifies the three normal-path return dicts.
# ===========================================================================

def _gateway_fn_src():
    """Extract source text of execute_trade_gateway() from app.py.

    The function is ~900 lines; the three success returns appear well past
    the first 6 000 characters.  We use 55 000 chars to guarantee coverage.
    """
    src = _app_src()
    start = src.find("def execute_trade_gateway(")
    assert start >= 0, "execute_trade_gateway not found in app.py"
    # Locate the next top-level def after the function to bound the extraction
    next_def = src.find("\ndef ", start + 1)
    end = next_def if (0 < next_def - start < 200_000) else start + 55_000
    return src[start:end]


def test_gateway_manual_required_version_in_source():
    """execute_trade_gateway() manual_required return must carry _version == 'v1'."""
    gw = _gateway_fn_src()
    assert '"status": "manual_required"' in gw, "manual_required path not found in gateway source"
    # The _version must appear AFTER the manual_required status in the same return block
    idx = gw.find('"status": "manual_required"')
    block = gw[idx:idx + 400]
    assert '"_version": "v1"' in block, (
        "execute_trade_gateway manual_required path missing _version v1")


def test_gateway_simulated_version_in_source():
    """execute_trade_gateway() simulated (paper) return must carry _version == 'v1'."""
    gw = _gateway_fn_src()
    assert '"status": "simulated"' in gw, "simulated path not found in gateway source"
    idx = gw.find('"status": "simulated"')
    block = gw[idx:idx + 400]
    assert '"_version": "v1"' in block, (
        "execute_trade_gateway simulated path missing _version v1")


def test_gateway_sent_version_in_source():
    """execute_trade_gateway() sent return must carry _version == 'v1'."""
    gw = _gateway_fn_src()
    assert '"status": "sent"' in gw, "sent path not found in gateway source"
    idx = gw.find('"status": "sent"')
    block = gw[idx:idx + 400]
    assert '"_version": "v1"' in block, (
        "execute_trade_gateway sent path missing _version v1")


def test_gateway_version_count():
    """execute_trade_gateway() must have exactly 3 _version insertions (one per success path)."""
    gw = _gateway_fn_src()
    count = gw.count('"_version": "v1"')
    assert count == 3, (
        f"execute_trade_gateway must have 3 '_version' insertions, found {count}")


def test_gateway_version_not_in_broker_payload():
    """_version must NOT appear in the broker payload builder functions."""
    src = _app_src()
    for fn_name in ("def adapt_traderspost(", "def adapt_pickmytrade("):
        start = src.find(fn_name)
        if start < 0:
            continue
        fn_src = src[start:start + 2000]
        assert '"_version"' not in fn_src, (
            f"{fn_name} must not include _version in broker payload")


# ===========================================================================
# V1-P1-006  JOURNAL  (v1)  — source inspection
#
# _build_card_entry() requires a full full_analysis() result plus optional
# webhook record. Source inspection confirms the single versioning seam.
# ===========================================================================

def _journal_fn_src():
    """Extract source text of _build_card_entry() from app.py.

    The function is ~160 lines; we locate the next top-level def to bound it.
    """
    src = _app_src()
    start = src.find("def _build_card_entry(")
    assert start >= 0, "_build_card_entry not found in app.py"
    next_def = src.find("\ndef ", start + 1)
    end = next_def if (0 < next_def - start < 50_000) else start + 12_000
    return src[start:end]


def test_journal_version_in_source():
    """_build_card_entry() must assign _version == 'v1' to entry before returning."""
    fn_src = _journal_fn_src()
    assert 'entry["_version"] = "v1"' in fn_src, (
        "_build_card_entry missing entry[\"_version\"] = \"v1\"")


def test_journal_version_before_return():
    """_build_card_entry() _version assignment must precede 'return entry'."""
    fn_src = _journal_fn_src()
    version_idx = fn_src.find('entry["_version"] = "v1"')
    return_idx  = fn_src.find("return entry", version_idx)
    assert version_idx >= 0, "_version assignment not found in _build_card_entry"
    assert return_idx  >= 0, "'return entry' not found after _version assignment"
    assert version_idx < return_idx, (
        "_version assignment must come before 'return entry' in _build_card_entry")


def test_journal_version_single_seam():
    """_build_card_entry() must have exactly one _version assignment (no duplicates)."""
    fn_src = _journal_fn_src()
    count = fn_src.count('entry["_version"]')
    assert count == 1, (
        f"_build_card_entry has {count} _version assignment(s); expected exactly 1")


# ===========================================================================
# V1-P1-004  MANAGER  (v1)  — COMPLETE
#
# build_manager_interface() is the canonical Manager Interface.
# ARCH §7 guaranteed fields: gateway_debug, active_trade, managed_trade,
# training_gate, auto_trade_enabled, _version.
#
# Isolation invariant: _active_trade_mgmt_block() is a separate display helper
# and must NOT carry _version or Manager Interface fields.
# ===========================================================================

def test_manager_build_returns_dict():
    """build_manager_interface() must return a dict."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    assert isinstance(mgr, dict), "build_manager_interface must return a dict"


def test_manager_version():
    """build_manager_interface() must carry _version == 'v1'."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    assert mgr.get("_version") == "v1", (
        f"build_manager_interface _version {mgr.get('_version')!r} != 'v1'")


def test_manager_version_type():
    """build_manager_interface() _version must be a str."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    assert isinstance(mgr.get("_version"), str), (
        f"build_manager_interface _version must be str, got {type(mgr.get('_version'))}")


def test_manager_required_fields():
    """build_manager_interface() must contain all ARCH §7 Manager guaranteed fields."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    for field in ("gateway_debug", "active_trade", "managed_trade",
                  "training_gate", "auto_trade_enabled"):
        assert field in mgr, f"build_manager_interface missing required field: {field!r}"


def test_manager_gateway_debug_is_dict():
    """build_manager_interface() gateway_debug must be a dict."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    assert isinstance(mgr.get("gateway_debug"), dict), (
        "build_manager_interface gateway_debug must be a dict")


def test_manager_active_trade_type():
    """build_manager_interface() active_trade must be dict or None."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    at = mgr.get("active_trade")
    assert at is None or isinstance(at, dict), (
        f"build_manager_interface active_trade must be dict|None, got {type(at)}")


def test_manager_managed_trade_type():
    """build_manager_interface() managed_trade must be dict or None."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    mt = mgr.get("managed_trade")
    assert mt is None or isinstance(mt, dict), (
        f"build_manager_interface managed_trade must be dict|None, got {type(mt)}")


def test_manager_training_gate_has_enabled():
    """build_manager_interface() training_gate must contain an 'enabled' key."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    tg = mgr.get("training_gate") or {}
    assert "enabled" in tg, (
        "build_manager_interface training_gate must contain 'enabled' key")


def test_manager_auto_trade_enabled_is_dict():
    """build_manager_interface() auto_trade_enabled must be a dict."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    assert isinstance(mgr.get("auto_trade_enabled"), dict), (
        "build_manager_interface auto_trade_enabled must be a dict")


def test_manager_version_serializes():
    """build_manager_interface() _version must survive JSON serialization."""
    result = app.full_analysis()
    mgr = app.build_manager_interface(result)
    assert json.dumps({"_version": mgr.get("_version")}) == '{"_version": "v1"}'


def test_manager_in_full_analysis():
    """full_analysis() must include 'manager' key with _version == 'v1'."""
    result = app.full_analysis()
    assert "manager" in result, "full_analysis result missing 'manager' key"
    mgr = result["manager"]
    assert isinstance(mgr, dict), "full_analysis result['manager'] must be a dict"
    assert mgr.get("_version") == "v1", (
        f"full_analysis result['manager']._version {mgr.get('_version')!r} != 'v1'")


# Isolation invariant: _active_trade_mgmt_block() must NOT carry _version
def test_manager_atm_block_no_version():
    """_active_trade_mgmt_block() must NOT carry _version.

    It is a display helper, not the canonical Manager Interface.
    The canonical Manager Interface is build_manager_interface() (V1-P1-004).
    """
    atm = app._active_trade_mgmt_block()
    if atm is None:
        return  # flag OFF → None is the correct return; no _version possible
    assert "_version" not in atm, (
        "_active_trade_mgmt_block must not carry _version — "
        "it is not the canonical Manager Interface")


def test_manager_canonical_fields_absent_from_atm_block():
    """_active_trade_mgmt_block() must not contain Manager Interface required fields.

    Confirms this object is architecturally separate from the Manager contract.
    """
    atm = app._active_trade_mgmt_block()
    if atm is None:
        return
    manager_fields = ("gateway_debug", "active_trade", "managed_trade",
                      "training_gate", "auto_trade_enabled")
    for field in manager_fields:
        assert field not in atm, (
            f"_active_trade_mgmt_block unexpectedly contains Manager field {field!r}")


# ===========================================================================
# V1-P1-007  COACH  (v1)  — COMPLETE
#
# build_coach_interface() is the canonical Coach Interface.
# ARCH §7 guaranteed fields: weight_updated, thesis_resolved,
# learning_influence, rule_engine_eligibility, _version.
#
# Isolation invariant: result["learning_score_influence"] is the edge-scoring
# modifier block and must NOT carry _version or Coach Interface fields.
# ===========================================================================

def test_coach_build_returns_dict():
    """build_coach_interface() must return a dict."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    assert isinstance(cch, dict), "build_coach_interface must return a dict"


def test_coach_version():
    """build_coach_interface() must carry _version == 'v1'."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    assert cch.get("_version") == "v1", (
        f"build_coach_interface _version {cch.get('_version')!r} != 'v1'")


def test_coach_version_type():
    """build_coach_interface() _version must be a str."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    assert isinstance(cch.get("_version"), str), (
        f"build_coach_interface _version must be str, got {type(cch.get('_version'))}")


def test_coach_required_fields():
    """build_coach_interface() must contain all ARCH §7 Coach guaranteed fields."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    for field in ("weight_updated", "thesis_resolved",
                  "learning_influence", "rule_engine_eligibility"):
        assert field in cch, f"build_coach_interface missing required field: {field!r}"


def test_coach_weight_updated_is_bool():
    """build_coach_interface() weight_updated must be a bool."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    assert isinstance(cch.get("weight_updated"), bool), (
        f"build_coach_interface weight_updated must be bool, "
        f"got {type(cch.get('weight_updated'))}")


def test_coach_thesis_resolved_is_bool():
    """build_coach_interface() thesis_resolved must be a bool."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    assert isinstance(cch.get("thesis_resolved"), bool), (
        f"build_coach_interface thesis_resolved must be bool, "
        f"got {type(cch.get('thesis_resolved'))}")


def test_coach_learning_influence_is_float():
    """build_coach_interface() learning_influence must be a float."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    li = cch.get("learning_influence")
    assert isinstance(li, float), (
        f"build_coach_interface learning_influence must be float, got {type(li)}")


def test_coach_learning_influence_range():
    """build_coach_interface() learning_influence must be in [-15.0, 15.0]."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    li = cch.get("learning_influence", 0.0)
    assert -15.0 <= li <= 15.0, (
        f"build_coach_interface learning_influence {li} out of [-15, 15] range")


def test_coach_rule_engine_eligibility_valid():
    """build_coach_interface() rule_engine_eligibility must be a valid status string."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    elig = cch.get("rule_engine_eligibility")
    valid = {"GHOST_ONLY", "LIVE_ELIGIBLE", "DISABLED"}
    assert elig in valid, (
        f"build_coach_interface rule_engine_eligibility {elig!r} not in {valid}")


def test_coach_version_serializes():
    """build_coach_interface() _version must survive JSON serialization."""
    result = app.full_analysis()
    cch = app.build_coach_interface(result)
    assert json.dumps({"_version": cch.get("_version")}) == '{"_version": "v1"}'


def test_coach_in_full_analysis():
    """full_analysis() must include 'coach' key with _version == 'v1'."""
    result = app.full_analysis()
    assert "coach" in result, "full_analysis result missing 'coach' key"
    cch = result["coach"]
    assert isinstance(cch, dict), "full_analysis result['coach'] must be a dict"
    assert cch.get("_version") == "v1", (
        f"full_analysis result['coach']._version {cch.get('_version')!r} != 'v1'")


# Isolation invariant: result["learning_score_influence"] must NOT carry _version
def test_coach_lsi_no_version():
    """result['learning_score_influence'] must NOT carry _version.

    It is the edge-scoring modifier block, not the canonical Coach Interface.
    The canonical Coach Interface is build_coach_interface() (V1-P1-007).
    """
    result = app.full_analysis()
    lsi = result.get("learning_score_influence") or {}
    assert "_version" not in lsi, (
        "learning_score_influence must not carry _version — "
        "it is not the canonical Coach Interface")


def test_coach_canonical_fields_absent():
    """Coach Interface required fields must be absent from learning_score_influence.

    Confirms this object is architecturally separate from the Coach contract.
    """
    result = app.full_analysis()
    lsi = result.get("learning_score_influence") or {}
    coach_fields = ("weight_updated", "thesis_resolved",
                    "learning_influence", "rule_engine_eligibility")
    for field in coach_fields:
        assert field not in lsi, (
            f"learning_score_influence unexpectedly contains Coach field {field!r}")


def test_coach_lsi_existing_fields_intact():
    """learning_score_influence existing fields must remain present (no regression)."""
    result = app.full_analysis()
    lsi = result.get("learning_score_influence") or {}
    for field in ("enabled", "armed", "max_delta"):
        assert field in lsi, (
            f"learning_score_influence missing existing field {field!r} after this batch")


# ===========================================================================
# CROSS-INTERFACE MATRIX
# ===========================================================================

def test_cross_interface_version_matrix():
    """Full version matrix for all 7 V1-P1 interfaces — all COMPLETE.

    Manager and Coach are now COMPLETE (build_manager_interface() and
    build_coach_interface() exist and carry _version == 'v1').
    Non-canonical objects (_active_trade_mgmt_block, learning_score_influence)
    must remain free of _version.
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

    # Manager: v1 (canonical builder) + isolation (display helper has no _version)
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

    # Coach: v1 (canonical builder) + isolation (LSI block has no _version)
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
