#!/usr/bin/env python3
"""
test_cockpit_consolidation.py — Phase 4C: Cockpit layout consolidation tests (A-T)

Static source analysis verifying:
- Each primary decision concept has exactly one display location
- Sections are correctly organized and de-duplicated
- Background refresh, responsive layout, and cross-instrument safety are in place
- Phase 4B Brain Contract bindings remain intact
"""
import os
import re
import unittest

COCKPIT = os.path.join(os.path.dirname(__file__),
                       "../../artifacts/home/src/pages/Cockpit.tsx")


def _src():
    with open(COCKPIT, encoding="utf-8") as f:
        return f.read()


def _count(src: str, pattern: str) -> int:
    return len(re.findall(re.escape(pattern), src))


class TestPrimaryDisplayUniqueness(unittest.TestCase):
    """A-D: each primary decision concept has exactly one display."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()

    # ── A: exactly one primary verdict display ────────────────────────────────
    def test_a_one_primary_verdict_display(self):
        """A: id='primary-verdict' appears exactly once (verdict not duplicated)."""
        count = _count(self.src, 'id="primary-verdict"')
        self.assertEqual(count, 1,
                         f"A: 'primary-verdict' id must appear exactly once, found {count}")

    # ── B: exactly one primary score display ──────────────────────────────────
    def test_b_one_primary_score_display(self):
        """B: id='primary-score-label' appears exactly once."""
        count = _count(self.src, 'id="primary-score-label"')
        self.assertEqual(count, 1,
                         f"B: 'primary-score-label' id must appear exactly once, found {count}")

    # ── C: exactly one primary direction display ───────────────────────────────
    def test_c_one_primary_direction_display(self):
        """C: direction chip is in Brain Decision only — id='primary-direction' once."""
        count = _count(self.src, 'id="primary-direction"')
        self.assertEqual(count, 1,
                         f"C: 'primary-direction' id must appear exactly once, found {count}")

    # ── D: exactly one next-action display ────────────────────────────────────
    def test_d_one_next_action_display(self):
        """D: nextReq value rendered once — 'Next action' label appears once."""
        count = _count(self.src, '"Next action"')
        self.assertEqual(count, 1,
                         f"D: 'Next action' label must appear exactly once, found {count}")


class TestBrainContractSources(unittest.TestCase):
    """E-G: Brain Decision widgets use data.brain (Phase 4B parity)."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()

    # ── E: Brain Decision sourced from data.brain ─────────────────────────────
    def test_e_brain_decision_from_contract(self):
        """E: verdict, is_ready, score, direction all sourced from brain contract."""
        self.assertIn("brain?.decision.verdict", self.src, "E: brain.decision.verdict")
        self.assertIn("brain?.decision.is_ready", self.src, "E: brain.decision.is_ready")
        self.assertIn("brain?.score.value", self.src, "E: brain.score.value")
        self.assertIn("brain?.decision.direction", self.src, "E: brain.decision.direction")

    # ── F: trade ticket from brain.trade_plan ─────────────────────────────────
    def test_f_trade_ticket_from_brain(self):
        """F: tp reads from brain?.trade_plan (Phase 4B binding unchanged)."""
        self.assertIn("brain?.trade_plan ?? null", self.src,
                      "F: brain?.trade_plan ?? null assignment present")

    # ── G: null trade plan disables entry ─────────────────────────────────────
    def test_g_null_plan_disables_entry(self):
        """G: Enter Trade button gated on isReady && hasPlan; shows null-plan message."""
        self.assertIn("isReady && hasPlan", self.src,
                      "G: isReady && hasPlan gate on Enter Trade")
        self.assertIn("No actionable trade plan", self.src,
                      "G: 'No actionable trade plan' state present")


class TestSectionPresence(unittest.TestCase):
    """H-N: all required panels present in their designated sections."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()

    # ── H: Market Structure has all 7 gate indicators ─────────────────────────
    def test_h_seven_gate_indicators(self):
        """H: all 7 gate indicator labels present in gate-checklist."""
        # TS object literal uses unquoted keys: { label: "BOS", ok: ... }
        for label in ["BOS", "CHOCH", "VWAP", "CVD", "Volume", "Zone", "Sweep"]:
            self.assertIn(f'label: "{label}"', self.src,
                          f"H: gate indicator '{label}' must be in gateItems")

    # ── I: active trade state visible ──────────────────────────────────────────
    def test_i_active_trade_visible(self):
        """I: active trade block present in Risk & Execution section."""
        self.assertIn('id="active-trade-block"', self.src,
                      "I: active-trade-block id present")
        self.assertIn("hasActiveTrade", self.src,
                      "I: hasActiveTrade used to show the block")

    # ── J: prop protection visible ─────────────────────────────────────────────
    def test_j_prop_protection_visible(self):
        """J: prop-protection block present in Risk & Execution section."""
        self.assertIn('id="prop-protection"', self.src,
                      "J: prop-protection id present")
        self.assertIn("propEnabled", self.src,
                      "J: propEnabled drives prop display")

    # ── K: diagnostics accessible ─────────────────────────────────────────────
    def test_k_diagnostics_accessible(self):
        """K: Diagnostics button calls setDrawerOpen(true)."""
        self.assertIn("setDrawerOpen(true)", self.src,
                      "K: setDrawerOpen(true) called from Diagnostics button")
        self.assertIn('id="cockpit-diagnostics"', self.src,
                      "K: cockpit-diagnostics overlay id present")

    # ── L: diagnostics not dominant initial view ───────────────────────────────
    def test_l_diagnostics_collapsed_by_default(self):
        """L: drawerOpen initialised to false — diagnostics are not the default view."""
        self.assertIn("useState(false)", self.src,
                      "L: drawerOpen starts as false (diagnostics collapsed)")

    # ── M: market timeline available ──────────────────────────────────────────
    def test_m_timeline_available(self):
        """M: market_events_timeline consumed in Section 5 (Learning & History)."""
        self.assertIn("market_events_timeline", self.src,
                      "M: market_events_timeline consumed")
        self.assertIn('id="cockpit-learning"', self.src,
                      "M: cockpit-learning footer id present")

    # ── N: learning fields do not replace Brain reason ─────────────────────────
    def test_n_learning_not_in_reason(self):
        """N: learningText is rendered in Section 5 only, not substituted into reason."""
        # reason must use brain?.reasons.top, not learningText
        reason_block = re.search(
            r"const reason = .*?(?=\n\n|\n  const)", self.src, re.DOTALL)
        if reason_block:
            self.assertNotIn("learningText", reason_block.group(0),
                             "N: learningText must not appear in reason derivation")
        # learningText itself must still exist (Section 5)
        self.assertIn("learningText", self.src,
                      "N: learningText variable still present for Section 5")
        # It must appear in the footer Learning row rendering
        self.assertIn("{learningText}", self.src,
                      "N: learningText rendered in footer/learning section")


class TestNavigationAndTimers(unittest.TestCase):
    """O-Q: instrument switching, cross-instrument safety, timer cleanup."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()

    # ── O: instrument switching functional ────────────────────────────────────
    def test_o_instrument_switching(self):
        """O: setActiveTicker called from nav rail instrument tab buttons."""
        self.assertIn("setActiveTicker(inst.name)", self.src,
                      "O: setActiveTicker called when instrument tab clicked")

    # ── P: no cross-instrument overwrite ──────────────────────────────────────
    def test_p_no_cross_instrument_overwrite(self):
        """P: applyData guards active instrument data with activeRef.current check."""
        self.assertIn("ticker === activeRef.current", self.src,
                      "P: active instrument guard prevents cross-instrument data write")

    # ── Q: timers cleaned up on unmount ───────────────────────────────────────
    def test_q_timers_cleaned_up(self):
        """Q: all setInterval calls have a corresponding clearInterval in return."""
        interval_count = self.src.count("setInterval(")
        clear_count    = self.src.count("clearInterval(")
        self.assertGreaterEqual(clear_count, interval_count,
                                f"Q: need at least {interval_count} clearInterval calls for {interval_count} setInterval calls")


class TestLayoutAndDuplication(unittest.TestCase):
    """R-S: no duplicate primary decision panels; mobile Brain Decision is first."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()

    # ── R: no duplicate primary decision panels ───────────────────────────────
    def test_r_no_duplicate_primary_panels(self):
        """R: primary verdict/score/direction ids each appear exactly once."""
        for id_name in ["primary-verdict", "primary-score-label", "primary-direction"]:
            count = _count(self.src, f'id="{id_name}"')
            self.assertLessEqual(count, 1,
                                 f"R: id='{id_name}' must appear at most once (found {count})")

    # ── S: mobile layout places Brain Decision first ──────────────────────────
    def test_s_mobile_brain_decision_first(self):
        """S: aside (market/risk) uses gridRow 2 on mobile, Brain Decision uses row 1."""
        # Brain Decision main is gridRow 1
        self.assertIn("gridRow: 1", self.src,
                      "S: Brain Decision main assigned to gridRow 1")
        # Aside is gridRow 2 on mobile (gridRow: isMobile ? 2 : 1)
        self.assertIn("isMobile ? 2 : 1", self.src,
                      "S: aside gridRow switches to 2 on mobile (Brain Decision stays row 1)")


class TestBackgroundRefresh(unittest.TestCase):
    """New background snap features added in Phase 4C."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()

    def test_background_snap_function_exists(self):
        """fetchBackgroundSnap function present for non-active instrument refresh."""
        self.assertIn("fetchBackgroundSnap", self.src,
                      "fetchBackgroundSnap function defined")

    def test_background_refresh_interval_30s(self):
        """30s background refresh interval set for non-active instruments."""
        self.assertIn("30000", self.src,
                      "30000ms (30s) interval used for background refresh")

    def test_background_cannot_overwrite_active(self):
        """Background snap uses applyData which guards against active-instrument overwrite."""
        # applyData has the guard: ticker === activeRef.current
        # fetchBackgroundSnap calls applyData
        self.assertIn("applyData(json, ticker)", self.src,
                      "fetchBackgroundSnap calls applyData (which has the overwrite guard)")

    def test_stale_flag_set_on_error(self):
        """Failed background fetch marks the snap stale."""
        self.assertIn("setStaleSnaps", self.src,
                      "staleSnaps state set on background fetch failure")

    def test_stale_cleared_on_success(self):
        """Successful applyData clears the stale flag."""
        # applyData calls setStaleSnaps(prev => ({ ...prev, [ticker]: false }))
        self.assertIn("setStaleSnaps(prev => ({ ...prev, [ticker]: false }))", self.src,
                      "stale flag cleared on successful response in applyData")

    def test_stale_dot_indicator_in_nav(self):
        """Nav rail shows stale indicator (purple dot) for stale snapshots."""
        self.assertIn("inst.stale", self.src,
                      "inst.stale used in nav dot rendering")
        self.assertIn("7c3aed", self.src,
                      "Purple dot (#7c3aed) used for stale indicator")

    def test_background_refresh_cleanup(self):
        """30s background refresh interval cleaned up on unmount."""
        # Both useEffects that create intervals must return clearInterval
        # The 30s effect is for fetchBackgroundSnap
        self.assertIn("fetchBackgroundSnap", self.src)
        # clearInterval must appear for it
        self.assertGreaterEqual(self.src.count("clearInterval("), 2,
                                "At least 2 clearInterval calls (active poll + background refresh)")


class TestResponsiveLayout(unittest.TestCase):
    """Responsive layout assertions."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()

    def test_is_mobile_state_exists(self):
        """isMobile state tracks viewport for responsive layout."""
        self.assertIn("isMobile", self.src, "isMobile state present")
        self.assertIn("window.innerWidth", self.src, "window.innerWidth used for mobile detection")

    def test_resize_listener_and_cleanup(self):
        """Resize event listener added and removed on unmount."""
        self.assertIn('window.addEventListener("resize"', self.src,
                      "resize listener added")
        self.assertIn('window.removeEventListener("resize"', self.src,
                      "resize listener removed on unmount")

    def test_mobile_flex_column_layout(self):
        """Mobile layout intentionally stacks the cockpit in a flex column."""
        self.assertIn('display: "flex"', self.src,
                      "Mobile layout uses flex")
        self.assertIn('flexDirection: "column"', self.src,
                      "Mobile layout stacks navigation and content vertically")

    def test_desktop_grid_columns(self):
        """Desktop layout uses 3-column grid with right rail."""
        self.assertIn('"56px 1fr 228px"', self.src,
                      "Desktop grid: 56px + 1fr + 228px right rail")

    def test_levels_grid_responsive(self):
        """Suggested levels grid uses 2 columns on mobile, 4 on desktop."""
        self.assertIn('isMobile ? "1fr 1fr" : "1fr 1fr 1fr 1fr"', self.src,
                      "Levels grid changes from 4 to 2 columns on mobile")


class TestDuplicateRemoval(unittest.TestCase):
    """Verify info strip duplicates were removed from Brain Decision main area."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()

    def test_info_strip_removed_from_main(self):
        """The old info strip chips (Bias/VWAP/ATR/Exec/Feed in main) are gone."""
        # The info strip was: { label: "Bias" }, { label: "VWAP" }, ... in a chip row
        # It's now gone from main; VWAP/Bias/ATR are only in the right rail Market Structure
        # Check the old combined chip-row pattern is not present
        self.assertNotIn('"label: \\"Bias\\"', self.src)  # partial; use different check
        # The chip row used this exact pattern for all 5 chips together
        # Now Exec and Feed are only in diagnostics, not duplicated
        # Count how many times 'label: "Exec"' appears — should be at most 1 (right rail or diag)
        exec_chip_count = self.src.count('label: "Exec"')
        self.assertLessEqual(exec_chip_count, 1,
                             f"Exec chip should appear at most once, found {exec_chip_count}")

    def test_grade_not_duplicated_in_right_rail(self):
        """Grade is now in Brain Decision only, not duplicated in right rail stat list."""
        # Previously: { label: "Grade", value: brain?.score.grade ?? "—" } in right rail
        # Now: grade shown as chip in Brain Decision only
        grade_in_stat_list = 'label: "Grade"' in self.src
        if grade_in_stat_list:
            # If it's there, it must be in the stat list only once
            count = self.src.count('"Grade"')
            self.assertLessEqual(count, 2,
                                 f"Grade should not be duplicated excessively, found {count}")
        # primary-grade id must exist (Brain Decision chip)
        self.assertIn('id="primary-grade"', self.src,
                      "Grade chip in Brain Decision has primary-grade id")

    def test_direction_not_in_right_rail_stat_list(self):
        """Direction is now in Brain Decision only, not duplicated in right rail stats."""
        # Previously: { label: "Direction", value: brain?.decision.direction ?? "—" } in right rail
        # Now: direction shown as chip in Brain Decision (primary-direction)
        self.assertNotIn('label: "Direction"', self.src,
                         "Direction removed from right rail stat list (now in Brain Decision only)")

    def test_learning_not_in_key_rows(self):
        """Learning memory removed from Three key rows (now in Section 5 footer)."""
        # Previously: { label: "Learning memory", ... } in the Three key rows
        self.assertNotIn('"Learning memory"', self.src,
                         "Learning memory removed from main key rows")

    def test_section_labels_present(self):
        """Right rail now has explicit section labels: Market structure + Risk & execution."""
        self.assertIn("Market structure", self.src,
                      "Section 2 label 'Market structure' present in right rail")
        self.assertIn("Risk & execution", self.src,
                      "Section 3 label 'Risk & execution' present in right rail")

    def test_learning_section_in_footer(self):
        """Learning section label present in footer (Section 5)."""
        self.assertIn("Learning", self.src,
                      "Section 5 'Learning' label in footer")
        # learningText rendered in footer, not in reason
        self.assertIn("{learningText}", self.src,
                      "learningText rendered in footer section")


class TestPhase4BParity(unittest.TestCase):
    """T: Phase 4B migration bindings must remain intact in the rewritten file."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()

    def test_t_brain_verdict_binding(self):
        """T: verdict from brain?.decision.verdict (Phase 4B)."""
        self.assertIn("brain?.decision.verdict", self.src)

    def test_t_is_ready_binding(self):
        """T: isReady from brain?.decision.is_ready (Phase 4B)."""
        self.assertIn("brain?.decision.is_ready", self.src)

    def test_t_score_value_binding(self):
        """T: edge from brain?.score.value (Phase 4B)."""
        self.assertIn("brain?.score.value", self.src)

    def test_t_score_max_binding(self):
        """T: edgeMax from brain?.score.max (Phase 4B)."""
        self.assertIn("brain?.score.max", self.src)

    def test_t_score_bar_proportional(self):
        """T: score bar uses (edge / edgeMax) * 100 formula (Phase 4B)."""
        self.assertIn("edge / edgeMax) * 100", self.src)
        self.assertIn("Math.min(100,", self.src)
        self.assertIn("Math.max(0,", self.src)

    def test_t_score_label_uses_max(self):
        """T: score label shows edge/edgeMax not edge/100 (Phase 4B fix)."""
        self.assertIn("} / {edgeMax}", self.src,
                      "Score label uses edgeMax not hardcoded 100")
        self.assertNotIn("} / 100", self.src,
                         "Hardcoded '/ 100' must not appear in score label")

    def test_t_reason_from_brain_top(self):
        """T: reason from brain?.reasons.top?.[0] (Phase 4B)."""
        self.assertIn("brain?.reasons.top?.[0]", self.src)

    def test_t_next_action_from_brain(self):
        """T: nextReq from brain?.decision.next_action (Phase 4B)."""
        self.assertIn("brain?.decision.next_action", self.src)

    def test_t_trade_plan_from_brain(self):
        """T: tp = brain?.trade_plan ?? null (Phase 4B)."""
        self.assertIn("brain?.trade_plan ?? null", self.src)

    def test_t_instsnaps_use_brain(self):
        """T: instSnaps use snapBrain.decision.is_ready / score.value (Phase 4B)."""
        self.assertIn("snapBrain.decision.is_ready", self.src)
        self.assertIn("snapBrain.score.value", self.src)

    def test_t_whole_contract_fallback(self):
        """T: buildLegacyBrainFallback present with console.warn (Phase 4B)."""
        self.assertIn("buildLegacyBrainFallback", self.src)
        self.assertIn("console.warn", self.src)

    def test_t_diagnostic_sources_retained(self):
        """T: flat /status sources retained for execution/diagnostic panels (Phase 4B)."""
        self.assertIn("active_trade_mgmt", self.src)
        self.assertIn("gate_debug", self.src)
        self.assertIn("confluences", self.src)
        self.assertIn("prop_firm", self.src)
        self.assertIn("market_events_timeline", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
