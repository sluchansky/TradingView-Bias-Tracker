#!/usr/bin/env python3
"""
test_brain_home.py — Phase 3B: Home Dashboard Simplification Tests (A-P)

Verifies that the Home operator view shows only the 9 required sections
and that competing/diagnostic displays have been moved or hidden.
All tests are source-level static analysis (no Flask server needed).
"""
import os
import re
import sys
import unittest

APP_PY = os.path.join(os.path.dirname(__file__), 'app.py')


def _src():
    with open(APP_PY, encoding='utf-8') as f:
        return f.read()


class TestHomeDashboardSimplification(unittest.TestCase):
    """Tests A-P per Phase 3B specification."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()
        # Pre-extract nav section bodies for fast lookups
        ov = re.search(r"overview:\s*\[(.*?)\]", cls.src, re.DOTALL)
        an = re.search(r"analysis:\s*\[(.*?)\]", cls.src, re.DOTALL)
        br = re.search(r"brain:\s*\[(.*?)\]", cls.src, re.DOTALL)
        cls.nav_overview = ov.group(1) if ov else ""
        cls.nav_analysis = an.group(1) if an else ""
        cls.nav_brain    = br.group(1) if br else ""

    # ── A: one primary verdict on Home ───────────────────────────────────────
    def test_a_one_primary_verdict(self):
        """A: #rec-gauge hidden means rec-badge is the single verdict display on Home."""
        self.assertIn('id="rec-gauge" class="rec-gauge" style="display:none"', self.src,
                      "A: #rec-gauge must carry display:none — Phase 3B")
        # Primary verdict badge still present exactly once
        self.assertEqual(self.src.count('id="rec-badge"'), 1,
                         "A: exactly one #rec-badge (primary verdict)")

    # ── B: one primary edge score on Home ────────────────────────────────────
    def test_b_one_primary_edge_score(self):
        """B: gauge hidden; rec-score-bar is the single score bar on Home."""
        self.assertIn('id="rec-gauge" class="rec-gauge" style="display:none"', self.src,
                      "B: gauge must be hidden so score bar is authoritative")
        self.assertIn('id="rec-score-bar"', self.src,
                      "B: primary score bar still present")

    # ── C: governor confidence not a competing Home score ────────────────────
    def test_c_governor_not_home_score(self):
        """C: mod-ai-decision-center (governor details) is in 'brain' section, not 'overview'."""
        self.assertNotIn("mod-ai-decision-center", self.nav_overview,
                         "C: ADC (governor confidence) must not be in overview nav section")
        self.assertIn("mod-ai-decision-center", self.nav_brain,
                      "C: ADC must remain accessible in brain nav section")

    # ── D: strict_score / gate debug not on Home ─────────────────────────────
    def test_d_gate_debug_removed_from_home(self):
        """D: raw gate debug booleans (zone_valid/vwap_confirmed) removed from renderDirView."""
        # The removed block contained this exact string
        self.assertNotIn(
            "debug \u00b7 zone_valid",
            self.src,
            "D: gate debug display string must not appear in dashboard JS"
        )
        # Sanity: gd variable (used for threshold) is still declared
        self.assertIn("const gd = (blk && blk.gate_debug)", self.src,
                      "D: gd variable still declared for edge threshold use")

    # ── E: legacy confidence hidden from Home ────────────────────────────────
    def test_e_legacy_confidence_hidden(self):
        """E: #rec-gauge (legacy probability/confidence semicircle) is display:none."""
        self.assertIn('id="rec-gauge" class="rec-gauge" style="display:none"', self.src,
                      "E: legacy confidence gauge hidden from Home view")

    # ── F: top reasons render exactly once ───────────────────────────────────
    def test_f_top_reasons_once(self):
        """F: #rec-checklist and #rec-reason appear exactly once in dashboard HTML."""
        self.assertEqual(self.src.count('id="rec-checklist"'), 1,
                         "F: #rec-checklist exactly once")
        self.assertEqual(self.src.count('id="rec-reason"'), 1,
                         "F: #rec-reason exactly once")
        # Brain contract reasons.top is consumed
        self.assertIn("bk.reasons.top", self.src, "F: bk.reasons.top is consumed")

    # ── G: top risks render exactly once ─────────────────────────────────────
    def test_g_top_risks_once(self):
        """G: #rec-risks element appears exactly once; bk.risks.top is rendered."""
        self.assertEqual(self.src.count('id="rec-risks"'), 1,
                         "G: #rec-risks element appears exactly once")
        self.assertIn("bk.risks.top", self.src, "G: bk.risks.top is rendered")
        # CSS for #rec-risks exists
        self.assertIn("#rec-risks{", self.src, "G: #rec-risks CSS rule present")

    # ── H: next action renders exactly once ──────────────────────────────────
    def test_h_next_action_once(self):
        """H: #rec-next-action element appears once; bk.decision.next_action rendered."""
        self.assertEqual(self.src.count('id="rec-next-action"'), 1,
                         "H: #rec-next-action element appears exactly once")
        self.assertIn("bk.decision.next_action", self.src,
                      "H: bk.decision.next_action consumed in JS")
        # CSS rule present
        self.assertIn("#rec-next-action{", self.src, "H: #rec-next-action CSS present")

    # ── I: null trade plan shows clear empty state ───────────────────────────
    def test_i_null_trade_plan_empty_state(self):
        """I: 'No active plan' empty-state text is present for null trade plan."""
        self.assertIn("No active plan", self.src,
                      "I: null trade-plan empty-state text present in JS")
        # bl-plan-panel HTML placeholder also present
        self.assertIn("id=\"blp-body\"", self.src, "I: blp-body plan panel present")

    # ── J: stale-price warning remains visible ───────────────────────────────
    def test_j_stale_price_visible(self):
        """J: #rec-fresh-warn element and stale-price rendering present."""
        self.assertIn('id="rec-fresh-warn"', self.src,
                      "J: #rec-fresh-warn element present in HTML")
        self.assertIn("Price data stale", self.src,
                      "J: stale price warning text in refreshRec JS")
        self.assertIn("bk.freshness.price_fresh", self.src,
                      "J: bk.freshness.price_fresh consumed")
        self.assertIn("#rec-fresh-warn{", self.src, "J: CSS for fresh-warn present")

    # ── K: score 0 remains visible ───────────────────────────────────────────
    def test_k_score_zero_visible(self):
        """K: score bar rendering clamps at 0 — not hidden when score is 0."""
        # The score bar uses Math.max(0, ...) ensuring 0 is a valid rendered width
        self.assertIn("Math.max(0,", self.src,
                      "K: score bar uses Math.max(0,…) to keep 0 visible")
        self.assertIn('id="rec-score-bar"', self.src, "K: primary score bar present")

    # ── L: direction null safe ───────────────────────────────────────────────
    def test_l_direction_null_safe(self):
        """L: jsReadyDir returns null for WAIT; buildLegacyFallback propagates it."""
        self.assertIn("jsReadyDir", self.src, "L: jsReadyDir helper present")
        # buildLegacyFallback uses jsReadyDir (rd) for the direction field
        m = re.search(r"function buildLegacyFallback.*?direction:\s*rd", self.src, re.DOTALL)
        self.assertIsNotNone(m, "L: buildLegacyFallback sets direction from jsReadyDir (may be null)")

    # ── M: avatar uses same brain object ─────────────────────────────────────
    def test_m_avatar_uses_brain_object(self):
        """M: renderModules/renderMBAvatar use getBrain(d); no independent state derivation."""
        self.assertIn("function getBrain(d)", self.src, "M: getBrain(d) accessor present")
        # renderModules starts with getBrain(d) call
        self.assertIn("function renderModules", self.src, "M: renderModules present")
        m = re.search(r"function renderModules\(d\)\s*\{.*?getBrain\(d\)", self.src, re.DOTALL)
        self.assertIsNotNone(m, "M: renderModules calls getBrain(d) — same brain object")

    # ── N: Cockpit navigation remains functional ─────────────────────────────
    def test_n_cockpit_link_functional(self):
        """N: /cockpit link present in the dashboard header."""
        self.assertIn('href="/cockpit"', self.src, "N: Cockpit link present and navigable")

    # ── O: no Brain Contract fields removed ──────────────────────────────────
    def test_o_brain_contract_fields_intact(self):
        """O: all required Brain Contract fields consumed or declared in dashboard JS."""
        # Fields accessed directly via bk.*
        direct_fields = [
            "bk.decision.verdict",
            "bk.decision.direction",
            "bk.decision.next_action",
            "bk.score.value",
            "bk.score.max",
            "bk.instrument",
            "bk.trade_plan",
            "bk.freshness",
            "bk.reasons.top",
            "bk.risks.top",
        ]
        for field in direct_fields:
            self.assertIn(field, self.src,
                          f"O: brain contract field '{field}' present in dashboard JS")
        # bk.decision.is_ready is expressed as jsIsActionable(v) in the JS — verify helper
        self.assertIn("jsIsActionable", self.src,
                      "O: jsIsActionable helper present (equivalent to bk.decision.is_ready)")
        # buildLegacyFallback declares is_ready field (contract schema intact)
        self.assertIn("is_ready:", self.src,
                      "O: is_ready field declared in buildLegacyFallback contract schema")

    # ── P: Phase 3A helpers intact ───────────────────────────────────────────
    def test_p_phase3a_helpers_intact(self):
        """P: Phase 3A helpers and key element IDs survive Phase 3B cleanup."""
        self.assertIn("function buildLegacyFallback(d)", self.src,
                      "P: buildLegacyFallback present")
        self.assertIn("function getBrain(d)", self.src, "P: getBrain present")
        self.assertIn("function renderGauge(d)", self.src, "P: renderGauge present")
        self.assertIn('id="bl-verdict-panel"', self.src,
                      "P: bl-verdict-panel (Phase 3A) present")
        self.assertIn('id="bl-risk-panel"', self.src,
                      "P: bl-risk-panel (Phase 3A) present")
        self.assertIn('id="rec-score-bar"', self.src,
                      "P: rec-score-bar (Phase 3A) present")


class TestNavSectionInvariants(unittest.TestCase):
    """Nav section contract: overview stays clean; mod-data-feed in analysis."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()
        ov = re.search(r"overview:\s*\[(.*?)\]", cls.src, re.DOTALL)
        an = re.search(r"analysis:\s*\[(.*?)\]", cls.src, re.DOTALL)
        cls.nav_overview = ov.group(1) if ov else ""
        cls.nav_analysis = an.group(1) if an else ""

    def test_mod_data_feed_not_in_overview(self):
        """mod-data-feed removed from overview (raw alert data is diagnostic)."""
        self.assertNotIn("'mod-data-feed'", self.nav_overview,
                         "mod-data-feed must not appear in overview section")

    def test_mod_data_feed_in_analysis(self):
        """mod-data-feed present in analysis section (still accessible, not deleted)."""
        self.assertIn("'mod-data-feed'", self.nav_analysis,
                      "mod-data-feed must be in analysis section")

    def test_mod_brain_in_overview(self):
        """mod-brain (avatar + brain summary) stays in overview."""
        self.assertIn("'mod-brain'", self.nav_overview,
                      "mod-brain must remain in overview section")

    def test_mod_real_results_in_overview(self):
        """mod-real-results stays in overview."""
        self.assertIn("'mod-real-results'", self.nav_overview,
                      "mod-real-results must remain in overview section")


if __name__ == '__main__':
    unittest.main(verbosity=2)
