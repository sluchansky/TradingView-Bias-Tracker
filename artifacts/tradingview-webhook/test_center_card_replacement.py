"""
test_center_card_replacement.py

Tests A-Z: verify that the large #blh-hero center card has been replaced
with the compact #main-brain-summary card, all required elements are present
with correct data-testid attributes, old elements are removed, CSS is updated,
and downstream JS variable dependencies are preserved.

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

    def test_C_narrative_element(self):
        """C: #mbs-narrative with data-testid='main-brain-narrative' present."""
        self.assertIn('id="mbs-narrative"', MBS_CARD,
                      "#mbs-narrative must be in the summary card")
        self.assertIn('data-testid="main-brain-narrative"', MBS_CARD,
                      "mbs-narrative must carry its data-testid")

    def test_D_next_action_element(self):
        """D: #mbs-next-action with data-testid='main-brain-next-action' present."""
        self.assertIn('id="mbs-next-action"', MBS_CARD,
                      "#mbs-next-action must be in the summary card")
        self.assertIn('data-testid="main-brain-next-action"', MBS_CARD,
                      "mbs-next-action must carry its data-testid")

    def test_E_invalidation_element(self):
        """E: #mbs-invalidation with data-testid='main-brain-invalidation' present."""
        self.assertIn('id="mbs-invalidation"', MBS_CARD,
                      "#mbs-invalidation must be in the summary card")
        self.assertIn('data-testid="main-brain-invalidation"', MBS_CARD,
                      "mbs-invalidation must carry its data-testid")

    def test_F_stale_warn_element(self):
        """F: #mbs-stale-warn element present for data-stale display."""
        self.assertIn('id="mbs-stale-warn"', MBS_CARD,
                      "#mbs-stale-warn must be in the summary card")

    def test_G_talk_button(self):
        """G: Talk-to-AI button with data-testid='main-brain-talk' present."""
        self.assertIn('data-testid="main-brain-talk"', MBS_CARD,
                      "Talk button must carry data-testid='main-brain-talk'")
        self.assertIn('TALK TO AI', MBS_CARD,
                      "Talk button must have text 'TALK TO AI'")

    def test_H_speak_button(self):
        """H: Speak button #mbs-speak-btn with data-testid='main-brain-speak' present."""
        self.assertIn('id="mbs-speak-btn"', MBS_CARD,
                      "#mbs-speak-btn must be in the summary card")
        self.assertIn('data-testid="main-brain-speak"', MBS_CARD,
                      "Speak button must carry data-testid='main-brain-speak'")

    def test_I_heading_text(self):
        """I: .mbs-heading div contains the text 'MAIN BRAIN'."""
        self.assertIn('mbs-heading', MBS_CARD,
                      ".mbs-heading must be present")
        self.assertIn('MAIN BRAIN', MBS_CARD,
                      "Heading text must be 'MAIN BRAIN'")

    def test_J_next_action_starts_hidden(self):
        """J: #mbs-next-action starts with display:none (revealed by JS when available)."""
        m = re.search(
            r'id="mbs-next-action"[^>]*style="[^"]*display:\s*none[^"]*"',
            MBS_CARD,
        )
        self.assertIsNotNone(
            m,
            "#mbs-next-action must have style='display:none' in HTML",
        )

    def test_K_invalidation_starts_hidden(self):
        """K: #mbs-invalidation starts with display:none (revealed by JS when available)."""
        m = re.search(
            r'id="mbs-invalidation"[^>]*style="[^"]*display:\s*none[^"]*"',
            MBS_CARD,
        )
        self.assertIsNotNone(
            m,
            "#mbs-invalidation must have style='display:none' in HTML",
        )

    def test_L_talk_button_scrolls_to_chat(self):
        """L: Talk button onclick scrolls to #mb-chat-input (mod-brain chat box)."""
        self.assertIn('mb-chat-input', MBS_CARD,
                      "Talk button onclick must reference mb-chat-input")
        self.assertIn('scrollIntoView', MBS_CARD,
                      "Talk button onclick must use scrollIntoView")

    def test_M_speak_button_uses_tts(self):
        """M: Speak button onclick uses window.speechSynthesis TTS."""
        self.assertIn('speechSynthesis', MBS_CARD,
                      "Speak button must use window.speechSynthesis")
        self.assertIn('SpeechSynthesisUtterance', MBS_CARD,
                      "Speak button must create a SpeechSynthesisUtterance")


# ─── OLD blh-hero REMOVED ────────────────────────────────────────────────────

class TestOldHeroRemoved(unittest.TestCase):

    def test_N_blh_hero_gone(self):
        """N: id='blh-hero' is not present in the HTML (replaced by mbs card)."""
        self.assertNotIn('<div id="blh-hero">', SRC,
                         "#blh-hero must not appear anywhere in the template")

    def test_O_blh_verdict_gone(self):
        """O: id='blh-verdict' (giant WAIT/READY headline) is removed."""
        self.assertNotIn('id="blh-verdict"', SRC,
                         "#blh-verdict must be removed (replaced by mbs-narrative)")

    def test_P_blh_reasoning_text_gone(self):
        """P: id='blh-reasoning-text' (AI REASONING box) is removed."""
        self.assertNotIn('id="blh-reasoning-text"', SRC,
                         "#blh-reasoning-text must be removed")

    def test_Q_blh_obs_tbl_gone(self):
        """Q: KEY OBSERVATIONS table (.blh-obs-tbl) is removed."""
        self.assertNotIn('blh-obs-tbl', SRC,
                         ".blh-obs-tbl must be removed (KEY OBSERVATIONS table)")

    def test_R_blh_waveform_gone(self):
        """R: id='blh-waveform' (animated waveform) is removed."""
        self.assertNotIn('id="blh-waveform"', SRC,
                         "#blh-waveform must be removed")


# ─── CSS: new styles present ──────────────────────────────────────────────────

class TestNewCSS(unittest.TestCase):

    def test_S_mbs_card_css(self):
        """S: #main-brain-summary CSS rule is present in source."""
        self.assertIn('#main-brain-summary{', SRC,
                      "#main-brain-summary CSS must be defined")

    def test_T_mbs_btn_css(self):
        """T: .mbs-btn and .mbs-controls CSS classes are defined in source."""
        self.assertIn('.mbs-btn{', SRC,
                      ".mbs-btn CSS must be defined")
        self.assertIn('.mbs-controls{', SRC,
                      ".mbs-controls CSS must be defined")


# ─── CSS: Swing V2 .blh-pill preserved ───────────────────────────────────────

class TestBlhPillPreserved(unittest.TestCase):

    def test_U_blh_pill_css_preserved(self):
        """U: .blh-pill CSS is preserved (Swing V2 lifecycle badge uses it)."""
        self.assertIn('.blh-pill{', SRC,
                      ".blh-pill CSS must be preserved for sv2-lifecycle-badge")
        self.assertIn('.blh-pill.ok{', SRC,
                      ".blh-pill.ok must be preserved")
        self.assertIn('.blh-pill.fail{', SRC,
                      ".blh-pill.fail must be preserved")
        self.assertIn('.blh-pill.warn{', SRC,
                      ".blh-pill.warn must be preserved")

    def test_V_blh_hero_css_removed(self):
        """V: #blh-hero{ CSS rule is removed."""
        self.assertNotIn('#blh-hero{', SRC,
                         "#blh-hero CSS must be removed")

    def test_W_blhwave_keyframes_removed(self):
        """W: @keyframes blhWave (waveform animation) CSS is removed."""
        self.assertNotIn('@keyframes blhWave', SRC,
                         "@keyframes blhWave must be removed")


# ─── JS: center card update block ────────────────────────────────────────────

class TestCenterJS(unittest.TestCase):

    def test_X_isactn_defined(self):
        """X: isActn is defined in the center JS block (downstream left-col uses it)."""
        self.assertIn("var isActn=", CENTER_JS,
                      "isActn must be defined in the center card JS block")

    def test_Y_shared_diag_vars_defined(self):
        """Y: hdiag/hcvdDir/hstructOk/hzoneOk/hvolReg defined for right-col use."""
        for var in ("var hdiag=", "var hcvdDir=", "var hstructOk=",
                    "var hzoneOk=", "var hvolReg="):
            self.assertIn(var, CENTER_JS,
                          f"{var.strip()} must be defined in center JS block")

    def test_Z_getbrain_called_in_center_block(self):
        """Z: getBrain(d) is called in the center card JS block (Brain Contract)."""
        self.assertIn("getBrain(d)", CENTER_JS,
                      "getBrain(d) must be called in the center card JS block")
        self.assertIn("mbs-narrative", CENTER_JS,
                      "center JS must update #mbs-narrative")
        self.assertIn("mbs-next-action", CENTER_JS,
                      "center JS must update #mbs-next-action")
        self.assertIn("mbs-stale-warn", CENTER_JS,
                      "center JS must update #mbs-stale-warn")


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
