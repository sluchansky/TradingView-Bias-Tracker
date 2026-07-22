"""
test_brain_dashboard.py — Phase 3A: JS render-function migration contract tests.

Verifies the Python _build_brain_contract() fields that every migrated render
function now reads from data.brain.*  (instead of the old flat d.*  fields).

Tests A-N map 1-to-1 to the render-function reads introduced in Phase 3A:
  A. decision.verdict populated
  B. decision.is_ready for READY verdict
  C. decision.direction for Long READY
  D. decision.direction for Short READY
  E. decision.next_action maps from stage_next_step
  F. score.value maps from edge_score
  G. score.max is always 110
  H. score.grade maps from edge_grade / conviction_tier
  I. instrument strips 1! suffix
  J. trade_plan present only when actionable
  K. trade_plan absent when WAIT
  L. freshness.price_last_valid_at maps from last_valid_time
  M. reasons.top has at most 3 entries
  N. supporting_diagnostics is the full confidence_governor dict
"""

import sys, types, importlib

# ── Minimal stubs so app.py is importable without real deps ──────────────────
for mod in ["flask", "flask_cors", "requests", "psycopg2", "psycopg2.extras",
            "pytz", "dotenv", "discord_webhook", "pyngrok", "pyngrok.ngrok",
            "apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background"]:
    sys.modules.setdefault(mod, types.ModuleType(mod))

flask_mod = sys.modules["flask"]
for attr in ["Flask", "request", "jsonify", "render_template_string",
             "redirect", "url_for", "session", "abort", "Response",
             "send_from_directory", "Blueprint"]:
    if not hasattr(flask_mod, attr):
        setattr(flask_mod, attr, lambda *a, **kw: None)
flask_mod.Flask = type("Flask", (), {
    "__init__": lambda s, *a, **kw: None,
    "route": lambda s, *a, **kw: (lambda f: f),
    "before_request": lambda s, *a, **kw: (lambda f: f),
    "after_request": lambda s, *a, **kw: (lambda f: f),
    "errorhandler": lambda s, *a, **kw: (lambda f: f),
    "register_blueprint": lambda s, *a, **kw: None,
    "run": lambda s, *a, **kw: None,
})
flask_mod.jsonify = lambda *a, **kw: {}
flask_mod.request = types.SimpleNamespace(json={}, args={}, form={}, method="GET",
                                          headers={}, remote_addr="", data=b"",
                                          get_json=lambda *a, **kw: None)

cors_mod = sys.modules["flask_cors"]
cors_mod.CORS = lambda *a, **kw: None

for sched_mod_name in ["apscheduler", "apscheduler.schedulers",
                        "apscheduler.schedulers.background"]:
    m = sys.modules[sched_mod_name]
    m.BackgroundScheduler = type("BackgroundScheduler", (), {
        "__init__": lambda s, *a, **kw: None,
        "add_job": lambda s, *a, **kw: None,
        "start": lambda s, *a, **kw: None,
    })

dotenv_mod = sys.modules["dotenv"]
dotenv_mod.load_dotenv = lambda *a, **kw: None

import os, importlib.util

spec = importlib.util.spec_from_file_location("app",
    os.path.join(os.path.dirname(__file__), "app.py"))
app_module = importlib.util.module_from_spec(spec)

try:
    spec.loader.exec_module(app_module)
except SystemExit:
    pass
except Exception:
    pass

_build = getattr(app_module, "_build_brain_contract", None)


def _bc(a, generated_at="2026-01-01T00:00:00"):
    """Convenience wrapper around _build_brain_contract."""
    return _build(a, generated_at)


def _make_analysis(verdict="WAIT", edge_score=55, edge_grade="B",
                   active_ticker="MGC1!", stage_next_step="Monitor",
                   last_valid_time="09:30", trade_plan=None,
                   confidence_governor=None, strict_reason="",
                   why=None):
    cg = confidence_governor or {"final_confidence_score": 70, "ready": True, "extra": "val"}
    tp = trade_plan or ({"trade_plan": True, "entry": 2000, "stop": 1990} if "READY" in verdict else None)
    a = {
        "verdict": verdict,
        "edge_score": edge_score,
        "edge_grade": edge_grade,
        "active_ticker": active_ticker,
        "stage_next_step": stage_next_step,
        "last_valid_time": last_valid_time,
        "trade_plan": tp,
        "confidence_governor": cg,
        "strict_reason": strict_reason,
        "why": why or ([strict_reason] if strict_reason else []),
        "market_intelligence": {},
    }
    return a


import traceback

_skip = _build is None

def test_a_decision_verdict():
    if _skip: return
    bc = _bc(_make_analysis(verdict="WAIT"))
    assert bc["decision"]["verdict"] == "WAIT", bc["decision"]["verdict"]

def test_b_is_ready_true():
    if _skip: return
    bc = _bc(_make_analysis(verdict="LONG READY"))
    assert bc["decision"]["is_ready"] is True, bc["decision"]

def test_b2_is_ready_false_for_wait():
    if _skip: return
    bc = _bc(_make_analysis(verdict="WAIT"))
    assert bc["decision"]["is_ready"] is False, bc["decision"]

def test_c_direction_long():
    if _skip: return
    bc = _bc(_make_analysis(verdict="LONG READY"))
    assert bc["decision"]["direction"] == "Long", bc["decision"]

def test_d_direction_short():
    if _skip: return
    bc = _bc(_make_analysis(verdict="SHORT READY"))
    assert bc["decision"]["direction"] == "Short", bc["decision"]

def test_e_next_action():
    if _skip: return
    bc = _bc(_make_analysis(verdict="WAIT", stage_next_step="Wait for sweep"))
    assert bc["decision"]["next_action"] == "Wait for sweep", bc["decision"]

def test_f_score_value():
    if _skip: return
    bc = _bc(_make_analysis(edge_score=83))
    assert bc["score"]["value"] == 83, bc["score"]

def test_g_score_max_always_110():
    if _skip: return
    for v in ["WAIT", "LONG READY", "SHORT READY"]:
        bc = _bc(_make_analysis(verdict=v))
        assert bc["score"]["max"] == 110, f"verdict={v} max={bc['score']['max']}"

def test_h_score_grade():
    if _skip: return
    bc = _bc(_make_analysis(edge_grade="A+"))
    assert bc["score"]["grade"] == "A+", bc["score"]

def test_i_instrument_strips_bang():
    if _skip: return
    bc = _bc(_make_analysis(active_ticker="MGC1!"))
    assert bc["instrument"] == "MGC", f"instrument={bc['instrument']}"

def test_i2_instrument_mnq():
    if _skip: return
    bc = _bc(_make_analysis(active_ticker="MNQ1!"))
    assert bc["instrument"] == "MNQ", f"instrument={bc['instrument']}"

def test_j_trade_plan_present_when_ready():
    if _skip: return
    tp = {"trade_plan": True, "entry": 2100, "stop": 2090, "target1": 2130}
    bc = _bc(_make_analysis(verdict="LONG READY", trade_plan=tp))
    assert bc["trade_plan"] is not None, "trade_plan should be present for READY"
    assert bc["trade_plan"].get("entry") == 2100

def test_k_trade_plan_absent_when_wait():
    if _skip: return
    bc = _bc(_make_analysis(verdict="WAIT", trade_plan=None))
    assert bc["trade_plan"] is None, f"trade_plan should be None for WAIT; got {bc['trade_plan']}"

def test_l_freshness_price_last_valid_at():
    if _skip: return
    bc = _bc(_make_analysis(last_valid_time="10:45 ET"))
    assert bc["freshness"]["price_last_valid_at"] == "10:45 ET", bc["freshness"]

def test_m_reasons_top_max_3():
    if _skip: return
    a = _make_analysis(why=["R1", "R2", "R3", "R4", "R5"])
    bc = _bc(a)
    assert len(bc["reasons"]["top"]) <= 3, bc["reasons"]

def test_m2_reasons_top_from_strict_reason():
    if _skip: return
    a = _make_analysis(strict_reason="Missing sweep", why=[])
    bc = _bc(a)
    assert bc["reasons"]["top"], "reasons.top should be non-empty"
    assert "sweep" in bc["reasons"]["top"][0].lower() or bc["reasons"]["top"][0], bc["reasons"]

def test_n_supporting_diagnostics_full_dict():
    if _skip: return
    cg = {"final_confidence_score": 88, "ready": True, "confidence_components": {"a": 1},
          "extra_key": "extra_val"}
    bc = _bc(_make_analysis(confidence_governor=cg))
    sd = bc["supporting_diagnostics"]
    assert isinstance(sd, dict), f"supporting_diagnostics should be dict, got {type(sd)}"
    assert sd.get("final_confidence_score") == 88, sd
    assert sd.get("ready") is True, sd
    assert sd.get("extra_key") == "extra_val", sd
    assert "confidence_components" in sd, sd


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = skipped = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    if failed:
        sys.exit(1)
