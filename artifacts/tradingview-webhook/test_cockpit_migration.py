#!/usr/bin/env python3
"""
test_cockpit_migration.py — Phase 4B: Cockpit Brain Contract migration tests (A-X)

Static source analysis on Cockpit.tsx verifying that operator-decision fields
now read from data.brain and that client-side re-derivations are removed.
No code is executed; all checks are source pattern assertions.
"""
import os
import re
import unittest

COCKPIT = os.path.join(os.path.dirname(__file__),
                       "../../artifacts/home/src/pages/Cockpit.tsx")
APP_PY  = os.path.join(os.path.dirname(__file__), "app.py")


def _ck():
    with open(COCKPIT, encoding="utf-8") as f:
        return f.read()


def _ap():
    with open(APP_PY, encoding="utf-8") as f:
        return f.read()


class TestCockpitBrainMigration(unittest.TestCase):
    """Tests A-X per Phase 4B specification."""

    @classmethod
    def setUpClass(cls):
        cls.src = _ck()
        cls.app = _ap()

    # ── A: verdict from brain ─────────────────────────────────────────────────
    def test_a_verdict_from_brain(self):
        """A: verdict is assigned from brain.decision.verdict."""
        self.assertIn("brain?.decision.verdict", self.src,
                      "A: brain?.decision.verdict used for verdict")

    # ── B: flat verdict does not override ────────────────────────────────────
    def test_b_flat_verdict_not_primary(self):
        """B: data?.verdict not used as direct verdict assignment in component."""
        # The component-level assignment must use brain, not data?.verdict
        self.assertNotIn('verdict  = data?.verdict', self.src,
                         "B: verdict must not be assigned from data?.verdict")

    # ── C: readiness from brain.decision.is_ready ────────────────────────────
    def test_c_readiness_from_brain(self):
        """C: isReady assigned from brain?.decision.is_ready."""
        self.assertIn("brain?.decision.is_ready", self.src,
                      "C: brain?.decision.is_ready used for isReady")

    # ── D: READY not derived from verdict text in component ──────────────────
    def test_d_ready_not_from_verdict_text(self):
        """D: isReady = verdict.includes('READY') removed from component derivation."""
        self.assertNotIn("isReady  = verdict.includes", self.src,
                         "D: isReady must not be derived from verdict text in component")

    # ── E: score from brain.score.value ──────────────────────────────────────
    def test_e_score_from_brain(self):
        """E: edge score assigned from brain?.score.value."""
        self.assertIn("brain?.score.value", self.src,
                      "E: brain?.score.value used for edge score")

    # ── F: flat edge_score does not override ─────────────────────────────────
    def test_f_flat_score_not_primary(self):
        """F: data?.edge_score not used as primary edge in component body."""
        self.assertNotIn("edge     = data?.edge_score", self.src,
                         "F: edge must not be assigned from data?.edge_score in component")

    # ── G: score bar uses brain.score.max ────────────────────────────────────
    def test_g_score_bar_uses_max(self):
        """G: score bar percentage uses edgeMax (from brain.score.max)."""
        self.assertIn("edgeMax", self.src, "G: edgeMax variable present")
        self.assertIn("brain?.score.max", self.src, "G: brain?.score.max used to set edgeMax")
        self.assertIn("/ edgeMax", self.src, "G: score bar divides by edgeMax")

    # ── H: 110/110 renders at 100% ───────────────────────────────────────────
    def test_h_score_110_at_100_pct(self):
        """H: Math.min(100, ...) clamps score bar at 100%."""
        self.assertIn("Math.min(100,", self.src,
                      "H: Math.min(100,...) clamps score bar at 100%")

    # ── I: 55/110 renders at 50% ─────────────────────────────────────────────
    def test_i_score_55_at_50_pct(self):
        """I: score bar formula (value/max)*100 renders 55/110 as 50%."""
        # Verify the proportional formula is present
        self.assertIn("edge / edgeMax) * 100", self.src,
                      "I: (edge / edgeMax) * 100 proportional formula present")

    # ── J: score 0 renders correctly ─────────────────────────────────────────
    def test_j_score_zero_renders(self):
        """J: Math.max(0, ...) prevents negative bar width at score 0."""
        self.assertIn("Math.max(0,", self.src,
                      "J: Math.max(0,...) prevents negative bar width")

    # ── K: grade from brain.score.grade ──────────────────────────────────────
    def test_k_grade_from_brain(self):
        """K: edge grade reads from brain?.score.grade."""
        self.assertIn("brain?.score.grade", self.src,
                      "K: brain?.score.grade used for grade display")

    # ── L: direction from brain.decision.direction ───────────────────────────
    def test_l_direction_from_brain(self):
        """L: direction reads from brain?.decision.direction."""
        self.assertIn("brain?.decision.direction", self.src,
                      "L: brain?.decision.direction used for direction display")

    # ── M: null direction safe ────────────────────────────────────────────────
    def test_m_null_direction_safe(self):
        """M: direction has ?? fallback to prevent null rendering as 'null'."""
        # Direction is normalized from the operator presentation, then rendered
        # null-safely rather than reading the retired direct expression.
        self.assertIn("const decisionDirection = operator", self.src)
        self.assertIn('decisionDirection ?? "—"', self.src,
                      "M: normalized direction has a null-safe fallback")

    # ── N: reason from brain.reasons.top[0] ──────────────────────────────────
    def test_n_reason_from_brain(self):
        """N: primary reason reads brain.reasons.top[0]."""
        self.assertIn("brain?.reasons.top", self.src,
                      "N: brain?.reasons.top used for reason")

    # ── O: legacy narrative not in reason assignment ──────────────────────────
    def test_o_no_legacy_narrative_in_reason(self):
        """O: main_brain_voice not in the reason assignment chain."""
        # The reason line must not include main_brain_voice
        reason_block = re.search(
            r"const reason = .*?(?=\n\n|\n  const)", self.src, re.DOTALL)
        if reason_block:
            self.assertNotIn("main_brain_voice", reason_block.group(0),
                             "O: main_brain_voice must not appear in reason assignment")
        else:
            # Fallback: check the specific pattern is absent
            self.assertNotIn("safeStr(data?.main_brain_voice)", self.src,
                             "O: safeStr(data?.main_brain_voice) must not be in reason chain")

    # ── P: next action from brain.decision.next_action ───────────────────────
    def test_p_next_action_from_brain(self):
        """P: nextReq reads from brain?.decision.next_action."""
        self.assertIn("brain?.decision.next_action", self.src,
                      "P: brain?.decision.next_action used for nextReq")

    # ── Q: trade plan from brain.trade_plan ──────────────────────────────────
    def test_q_trade_plan_from_brain(self):
        """Q: tp reads from brain?.trade_plan."""
        self.assertIn("brain?.trade_plan", self.src,
                      "Q: brain?.trade_plan used for tp")

    # ── R: null trade plan clears stale levels ────────────────────────────────
    def test_r_null_trade_plan_clears(self):
        """R: tp = brain?.trade_plan ?? null — null when brain.trade_plan is null."""
        self.assertIn("brain?.trade_plan ?? null", self.src,
                      "R: brain?.trade_plan ?? null assignment present")

    # ── S: Enter Trade disabled when is_ready false ───────────────────────────
    def test_s_enter_trade_gated_on_plan(self):
        """S: Enter Trade button gated on isReady && hasPlan."""
        self.assertIn("isReady && hasPlan", self.src,
                      "S: Enter Trade gated on isReady && hasPlan")
        self.assertIn("No actionable trade plan", self.src,
                      "S: 'No actionable trade plan' state present for null plan")

    # ── T: diagnostics score uses brain max ───────────────────────────────────
    def test_t_diagnostics_score_uses_max(self):
        """T: Diagnostics drawer score label shows value/edgeMax not /100."""
        self.assertIn("/ ${edgeMax}", self.src,
                      "T: diagnostics drawer shows edge/edgeMax not /100")
        self.assertNotIn("/ 100`", self.src,
                         "T: hard-coded '/ 100' removed from diagnostics display")

    # ── U: instrument snapshots use brain readiness ───────────────────────────
    def test_u_instsnaps_use_brain(self):
        """U: applyData instSnaps use snapBrain.decision.is_ready and score.value."""
        self.assertIn("snapBrain.decision.is_ready", self.src,
                      "U: snapBrain.decision.is_ready used in instSnaps")
        self.assertIn("snapBrain.score.value", self.src,
                      "U: snapBrain.score.value used in instSnaps edge")

    # ── V: whole-contract fallback only when brain absent ─────────────────────
    def test_v_whole_contract_fallback(self):
        """V: buildLegacyBrainFallback exists and is the sole fallback path."""
        self.assertIn("function buildLegacyBrainFallback", self.src,
                      "V: buildLegacyBrainFallback function present")
        # Used as whole-contract fallback: data.brain ?? buildLegacyBrainFallback(data)
        self.assertIn("data.brain ?? buildLegacyBrainFallback(data)", self.src,
                      "V: data.brain ?? buildLegacyBrainFallback(data) fallback pattern")
        # Must emit a warning when used
        self.assertIn("console.warn", self.src,
                      "V: console.warn emitted in legacy fallback")

    # ── W: execution/diagnostic panels retain current sources ─────────────────
    def test_w_diagnostic_sources_unchanged(self):
        """W: gate_debug, active_trade_mgmt, confluences, prop_firm still read flat."""
        self.assertIn("active_trade_mgmt", self.src,
                      "W: active_trade_mgmt still read from flat /status")
        self.assertIn("gate_debug", self.src,
                      "W: gate_debug still read from flat /status")
        self.assertIn("confluences", self.src,
                      "W: confluences still read from flat /status")
        self.assertIn("prop_firm", self.src,
                      "W: prop_firm still read from flat /status")
        self.assertIn("market_events_timeline", self.src,
                      "W: market_events_timeline still read from flat /status")

    # ── X: no backend behavior changes ────────────────────────────────────────
    def test_x_backend_unchanged(self):
        """X: app.py scoring/gate/execution patterns are unchanged."""
        # Key brain contract builder still present in backend
        self.assertIn("_build_brain_contract", self.app,
                      "X: _build_brain_contract still in app.py")
        # Key gate function still present
        self.assertIn("evaluate_strict_setup", self.app,
                      "X: evaluate_strict_setup still in app.py")
        # Execution gateway still present
        self.assertIn("execute_trade_gateway", self.app,
                      "X: execute_trade_gateway still in app.py")


class TestBrainContractType(unittest.TestCase):
    """Structural checks on the BrainContract TypeScript type."""

    @classmethod
    def setUpClass(cls):
        cls.src = _ck()

    def test_brain_contract_type_defined(self):
        """BrainContract type is defined before StatusData."""
        bc_pos = self.src.find("type BrainContract = {")
        sd_pos = self.src.find("type StatusData = {")
        self.assertGreater(bc_pos, -1, "BrainContract type defined")
        self.assertGreater(sd_pos, -1, "StatusData type defined")
        self.assertLess(bc_pos, sd_pos, "BrainContract defined before StatusData")

    def test_status_data_has_brain_field(self):
        """StatusData includes brain?: BrainContract field."""
        self.assertIn("brain?: BrainContract;", self.src,
                      "StatusData has optional brain field typed as BrainContract")

    def test_brain_contract_fields(self):
        """BrainContract type includes all required decision/score/reasons/trade_plan fields."""
        for field in ["is_ready", "next_action", "score", "reasons", "trade_plan", "grade"]:
            self.assertIn(field, self.src,
                          f"BrainContract type includes '{field}' field")

    def test_fallback_uses_brain_contract_type(self):
        """buildLegacyBrainFallback return type is BrainContract."""
        self.assertIn("): BrainContract {", self.src,
                      "buildLegacyBrainFallback has BrainContract return type")


class TestSafetyStates(unittest.TestCase):
    """Verify correct safety-state handling in the migrated component."""

    @classmethod
    def setUpClass(cls):
        cls.src = _ck()

    def test_score_zero_safe(self):
        """Score 0 handled: Math.max(0, ...) prevents negative width."""
        self.assertIn("Math.max(0,", self.src)

    def test_score_max_zero_safe(self):
        """edgeMax = 0 handled: ternary guard prevents division by zero."""
        self.assertIn("edgeMax > 0 ?", self.src,
                      "Division-by-zero guard: edgeMax > 0 ? ... : 0")

    def test_brain_null_safe(self):
        """brain = null (loading state) handled by optional chaining."""
        self.assertIn("brain?.decision.verdict ?? \"—\"", self.src,
                      "Null brain: verdict fallback to '—'")
        self.assertIn("brain?.decision.is_ready ?? false", self.src,
                      "Null brain: isReady fallback to false")
        self.assertIn("brain?.score.value ?? 0", self.src,
                      "Null brain: edge fallback to 0")

    def test_no_plan_empty_state(self):
        """Null trade plan shows 'No actionable trade plan' not stale levels."""
        self.assertIn("No actionable trade plan", self.src)
        self.assertIn("No active plan", self.src,
                      "Trade ticket also shows null-plan state")

    def test_null_direction_no_false_render(self):
        """Null direction renders as '—' not the string 'null'."""
        self.assertIn("decisionDirection ?? \"—\"", self.src)

    def test_empty_reasons_no_crash(self):
        """Empty reasons.top[0] → empty string, not crash."""
        # brain?.reasons.top?.[0] with optional chaining handles empty array
        self.assertIn("brain?.reasons.top?.[0]", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
