"""
test_center_card_replacement.py

Tests A-Z: verify that the large #blh-hero center card has been replaced
with the compact #main-brain-summary verdict card, all required elements are
present with correct data-testid attributes, old elements are removed, CSS is
updated, and downstream JS variable dependencies are preserved.

No server is started — we parse the HTML/CSS/JS template directly from app.py.
"""
import re
import os
import unittest

APP = os.path.join(os.path.dirname(__file__), "app.py")


def _load_source():
    with open(APP, "r", encoding="utf-8") as f:
        return f.read()


def _extract_mbs_card(src):
    """Return the HTML of #main-brain-summary (balanced tag walk)."""
    m = re.search(r'<div[^>]*id="main-brain-summary"[^>]*>', src)
    if not m:
        return ""
    start = m.start()
    depth = 0
    i = start
    while i < len(src):
        if src[i:i+4] == "<div":
            depth += 1
        elif src[i:i+6] == "</div>":
            depth -= 1
            if depth == 0:
                return src[start: i + 6]
        i += 1
    return ""


def _extract_center_js(src):
    """Return the JS section that updates the Main Brain Summary card."""
    m = re.search(
        r'// \u2500+ Main Brain Summary card \(replaces blh-hero.*?'
        r'(?=// \u2500+ Dark mode toggle)',
        src,
        re.DOTALL,
    )
    return m.group(0) if m else ""


SRC = _load_source()
MBS_CARD = _extract_mbs_card(SRC)
CENTER_JS = _extract_center_js(SRC)


# ─── HTML STRUCTURE: new card present ────────────────────────────────────────

class TestNewCardPresent(unittest.TestCase):

    def test_A_mbs_card_exists(self):
        """A: #main-brain-summary div exists in the dashboard template."""
        self.assertIn('id="main-brain-summary"', SRC,
                      "#main-brain-summary must be in dashboard HTML")
        self.assertTrue(len(MBS_CARD) > 0,
                        "#main-brain-summary must be a non-empty block")

    def test_B_testid_summary(self):
        """B: data-testid="main-brain-summary" attribute is present."""
        self.assertIn('data-testid="main-brain-summary"', MBS_CARD,
                      "main-brain-summary card must have its data-testid")

    def test_C_verdict_element(self):
        """C: #mbs-verdict with data-testid='main-brain-verdict' present."""
        self.assertIn('id="mbs-verdict"', MBS_CARD,
                      "#mbs-verdict must be in the summary card")
        self.assertIn('data-testid="main-brain-verdict"', MBS_CARD,
                      "mbs-verdict must carry its data-testid")

    def test_D_edge_element(self):
        """D: #mbs-edge with data-testid='main-brain-edge' present."""
        self.assertIn('id="mbs-edge"', MBS_CARD,
                      "#mbs-edge must be in the summary card")
        self.assertIn('data-testid="main-brain-edge"', MBS_CARD,
                      "mbs-edge must carry its data-testid")

    def test_E_gates_element(self):
        """E: #mbs-gates with data-testid='main-brain-gates' present."""
        self.assertIn('id="mbs-gates"', MBS_CARD,
                      "#mbs-gates must be in the summary card")
        self.assertIn('data-testid="main-brain-gates"', MBS_CARD,
                      "mbs-gates must carry its data-testid")

    def test_F_reason_element(self):
        """F: #mbs-reason with data-testid='main-brain-reason' present."""
        self.assertIn('id="mbs-reason"', MBS_CARD,
                      "#mbs-reason must be in the summary card")
        self.assertIn('data-testid="main-brain-reason"', MBS_CARD,
                      "mbs-reason must carry its data-testid")

    def test_G_stale_warn_element(self):
        """G: #mbs-stale-warn element present for data-stale display."""
        self.assertIn('id="mbs-stale-warn"', MBS_CARD,
                      "#mbs-stale-warn must be in the summary card")

    def test_H_reason_starts_hidden(self):
        """H: #mbs-reason starts with display:none (shown only on WAIT)."""
        m = re.search(
            r'id="mbs-reason"[^>]*style="[^"]*display:\s*none[^"]*"',
            MBS_CARD,
        )
        self.assertIsNotNone(m, "#mbs-reason must start hidden (display:none)")

    def test_I_no_talk_to_ai_button_in_card(self):
        """I: TALK TO AI button is NOT inside #main-brain-summary card."""
        self.assertNotIn('mbs-btn', MBS_CARD,
                         ".mbs-btn must not be in the summary card")
        self.assertNotIn('mbs-speak-btn', MBS_CARD,
                         "#mbs-speak-btn must not be in the summary card")
        self.assertNotIn('mbs-controls', MBS_CARD,
                         ".mbs-controls must not be in the summary card")

    def test_J_no_narrative_element(self):
        """J: Old narrative paragraph (#mbs-narrative) is not in the card."""
        self.assertNotIn('id="mbs-narrative"', MBS_CARD,
                         "#mbs-narrative must not be in the new compact card")

    def test_K_verdict_has_initial_color(self):
        """K: #mbs-verdict has an initial color style set (WAIT gray)."""
        self.assertIn('color:', MBS_CARD,
                      "#mbs-verdict must have an initial color style")

    def test_L_mbs_pill_css_used(self):
        """L: .mbs-pill CSS class is referenced in the card's gate pills."""
        self.assertIn('mbs-pill', SRC,
                      ".mbs-pill must be defined in CSS and used by gate pills")


# ─── OLD blh-hero REMOVED ────────────────────────────────────────────────────

class TestOldHeroRemoved(unittest.TestCase):

    def test_M_blh_hero_gone(self):
        """M: id='blh-hero' is not present in the template."""
        self.assertNotIn('<div id="blh-hero">', SRC,
                         "#blh-hero must not appear anywhere in the template")

    def test_N_blh_verdict_gone(self):
        """N: id='blh-verdict' (giant WAIT/READY headline) is removed."""
        self.assertNotIn('id="blh-verdict"', SRC,
                         "#blh-verdict must be removed")

    def test_O_blh_reasoning_text_gone(self):
        """O: id='blh-reasoning-text' (AI REASONING box) is removed."""
        self.assertNotIn('id="blh-reasoning-text"', SRC,
                         "#blh-reasoning-text must be removed")

    def test_P_blh_obs_tbl_gone(self):
        """P: KEY OBSERVATIONS table (.blh-obs-tbl) is removed."""
        self.assertNotIn('blh-obs-tbl', SRC,
                         ".blh-obs-tbl must be removed")

    def test_Q_blh_waveform_gone(self):
        """Q: id='blh-waveform' (animated waveform) is removed."""
        self.assertNotIn('id="blh-waveform"', SRC,
                         "#blh-waveform must be removed")

    def test_R_no_speak_btn_in_card(self):
        """R: Old speak button (#mbs-speak-btn) is not in the card."""
        self.assertNotIn('id="mbs-speak-btn"', SRC,
                         "#mbs-speak-btn must be removed from the new card")


# ─── CSS: new styles present ──────────────────────────────────────────────────

class TestNewCSS(unittest.TestCase):

    def test_S_mbs_card_css(self):
        """S: #main-brain-summary CSS rule is present."""
        self.assertIn('#main-brain-summary{', SRC,
                      "#main-brain-summary CSS must be defined")

    def test_T_mbs_verdict_css(self):
        """T: #mbs-verdict CSS is defined."""
        self.assertIn('#mbs-verdict{', SRC,
                      "#mbs-verdict CSS must be defined")

    def test_U_mbs_pill_css(self):
        """U: .mbs-pill CSS class is defined for gate pills."""
        self.assertIn('.mbs-pill{', SRC,
                      ".mbs-pill CSS must be defined")


# ─── CSS: Swing V2 .blh-pill preserved ───────────────────────────────────────

class TestBlhPillPreserved(unittest.TestCase):

    def test_V_blh_pill_css_preserved(self):
        """V: .blh-pill CSS is preserved (Swing V2 lifecycle badge uses it)."""
        self.assertIn('.blh-pill{', SRC,
                      ".blh-pill CSS must be preserved for sv2-lifecycle-badge")
        self.assertIn('.blh-pill.ok{', SRC,
                      ".blh-pill.ok must be preserved")

    def test_W_blh_hero_css_removed(self):
        """W: #blh-hero{ CSS rule is removed."""
        self.assertNotIn('#blh-hero{', SRC,
                         "#blh-hero CSS must be removed")

    def test_X_blhwave_keyframes_removed(self):
        """X: @keyframes blhWave (waveform animation) CSS is removed."""
        self.assertNotIn('@keyframes blhWave', SRC,
                         "@keyframes blhWave must be removed")


# ─── JS: center card update block ────────────────────────────────────────────

class TestCenterJS(unittest.TestCase):

    def test_Y_isactn_and_diag_vars_defined(self):
        """Y: isActn/hdiag/hcvdDir/hstructOk/hzoneOk/hvolReg defined (downstream use)."""
        for var in ("var isActn=", "var hdiag=", "var hcvdDir=",
                    "var hstructOk=", "var hzoneOk=", "var hvolReg="):
            self.assertIn(var, CENTER_JS,
                          f"{var.strip()} must be defined in center JS block")

    def test_Z_js_populates_verdict_edge_gates_reason(self):
        """Z: Center JS updates mbs-verdict, mbs-edge, mbs-gates, mbs-reason."""
        self.assertIn("mbs-verdict", CENTER_JS,
                      "center JS must update #mbs-verdict")
        self.assertIn("mbs-edge", CENTER_JS,
                      "center JS must update #mbs-edge")
        self.assertIn("mbs-gates", CENTER_JS,
                      "center JS must update #mbs-gates")
        self.assertIn("mbs-reason", CENTER_JS,
                      "center JS must update #mbs-reason")
        self.assertIn("getBrain(d)", CENTER_JS,
                      "center JS must call getBrain(d) for edge score/grade")


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
