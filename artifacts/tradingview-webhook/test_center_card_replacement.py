"""
test_center_card_replacement.py

Tests A-Z + AA-AR: verify the compact #main-brain-summary card is correctly
wired to the Brain Contract — HTML structure, CSS, JS bindings, data safety.

No server is started — all tests parse the HTML/CSS/JS template from app.py.
"""
import re
import os
import unittest

APP = os.path.join(os.path.dirname(__file__), "app.py")


def _load_source():
    with open(APP, "r", encoding="utf-8") as f:
        return f.read()


def _extract_mbs_card(src):
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


# ─── A–L: original structural tests (updated for full card) ──────────────────

class TestNewCardPresent(unittest.TestCase):

    def test_A_mbs_card_exists(self):
        """A: #main-brain-summary div exists in the dashboard template."""
        self.assertIn('id="main-brain-summary"', SRC)
        self.assertTrue(len(MBS_CARD) > 0)

    def test_B_testid_summary(self):
        """B: data-testid="main-brain-summary" present."""
        self.assertIn('data-testid="main-brain-summary"', MBS_CARD)

    def test_C_verdict_element(self):
        """C: #mbs-verdict with data-testid='main-brain-verdict' present."""
        self.assertIn('id="mbs-verdict"', MBS_CARD)
        self.assertIn('data-testid="main-brain-verdict"', MBS_CARD)

    def test_D_edge_element(self):
        """D: #mbs-edge with data-testid='main-brain-edge' present."""
        self.assertIn('id="mbs-edge"', MBS_CARD)
        self.assertIn('data-testid="main-brain-edge"', MBS_CARD)

    def test_E_gates_element(self):
        """E: #mbs-gates with data-testid='main-brain-gates' present."""
        self.assertIn('id="mbs-gates"', MBS_CARD)
        self.assertIn('data-testid="main-brain-gates"', MBS_CARD)

    def test_F_reason_element(self):
        """F: #mbs-reason with data-testid='main-brain-reason' present."""
        self.assertIn('id="mbs-reason"', MBS_CARD)
        self.assertIn('data-testid="main-brain-reason"', MBS_CARD)

    def test_G_stale_warn_element(self):
        """G: #mbs-stale-warn element present."""
        self.assertIn('id="mbs-stale-warn"', MBS_CARD)

    def test_H_reason_starts_hidden(self):
        """H: #mbs-reason starts with display:none."""
        m = re.search(
            r'id="mbs-reason"[^>]*style="[^"]*display:\s*none[^"]*"',
            MBS_CARD,
        )
        self.assertIsNotNone(m)

    def test_I_no_unwanted_mbs_btn(self):
        """I: Old .mbs-btn and .mbs-controls classes not in card (new controls use .mbs-ctrl-btn)."""
        self.assertNotIn('class="mbs-btn"', MBS_CARD)

    def test_J_no_narrative_object_object(self):
        """J: Literal '[object Object]' must not appear in the center card JS or HTML."""
        self.assertNotIn('[object Object]', MBS_CARD,
                         "[object Object] must not appear in card HTML")
        self.assertNotIn('[object Object]', CENTER_JS,
                         "[object Object] must not appear in center card JS")

    def test_K_verdict_has_initial_color(self):
        """K: #mbs-verdict has an initial color style set."""
        self.assertIn('color:', MBS_CARD)

    def test_L_mbs_pill_css_used(self):
        """L: .mbs-pill CSS class is referenced in source."""
        self.assertIn('mbs-pill', SRC)


# ─── Spec tests A–R (narrative, next-action, invalidation, controls) ─────────

class TestNarrativeElement(unittest.TestCase):

    def test_specA_narrative_node_exists(self):
        """specA: narrative DOM node exists with correct data-testid."""
        self.assertIn('id="mbs-narrative"', MBS_CARD,
                      "#mbs-narrative must be in the card HTML")
        self.assertIn('data-testid="main-brain-narrative"', MBS_CARD,
                      "data-testid=main-brain-narrative must be present")

    def test_specB_narrative_js_populates(self):
        """specB: JS populates mbs-narrative from getBrain(d).reasons.top."""
        self.assertIn("mbs-narrative", CENTER_JS)
        self.assertIn("bkTop", CENTER_JS,
                      "JS must read brain.reasons.top array")
        self.assertIn("safeR", CENTER_JS,
                      "JS must filter reasons array to safe strings")

    def test_specC_string_reason_renders(self):
        """specC: JS filters reasons to typeof==='string' before display."""
        self.assertIn("typeof r==='string'", CENTER_JS)

    def test_specD_object_reason_skipped(self):
        """specD: No implicit object stringification for reasons (no String(r) on raw reason)."""
        self.assertNotIn("String(r)", CENTER_JS,
                         "Raw String(reason_item) would produce [object Object]")

    def test_specE_fallback_narrative(self):
        """specE: Fallback narrative text when no parts available."""
        self.assertIn("Brain analysis is available", CENTER_JS)

    def test_specF_next_action_shows(self):
        """specF: #mbs-next-wrap is shown when bkNext is non-empty."""
        self.assertIn("mbs-next-wrap", CENTER_JS)
        self.assertIn("mNwrap.style.display=''", CENTER_JS)

    def test_specG_next_action_hides(self):
        """specG: #mbs-next-wrap is hidden and text cleared when bkNext absent."""
        self.assertIn("mNwrap.style.display='none'", CENTER_JS)
        self.assertIn("mNact.textContent=''", CENTER_JS)

    def test_specH_invalidation_shows(self):
        """specH: #mbs-inv-wrap is shown when bkInv is non-empty."""
        self.assertIn("mbs-inv-wrap", CENTER_JS)
        self.assertIn("mIwrap.style.display=''", CENTER_JS)

    def test_specI_invalidation_hides(self):
        """specI: #mbs-inv-wrap hidden and cleared when no invalidation."""
        self.assertIn("mIwrap.style.display='none'", CENTER_JS)
        self.assertIn("mItext.textContent=''", CENTER_JS)

    def test_specJ_talk_to_ai_visible(self):
        """specJ: Talk to AI button present in card with correct testid."""
        self.assertIn('data-testid="main-brain-talk"', MBS_CARD)
        self.assertIn('Talk to AI', MBS_CARD)

    def test_specK_speak_visible(self):
        """specK: Speak button present in card with correct testid."""
        self.assertIn('data-testid="main-brain-speak"', MBS_CARD)
        self.assertIn('Speak', MBS_CARD)
        self.assertIn('id="mbs-speak-btn"', MBS_CARD)

    def test_specL_all_required_selectors_in_card(self):
        """specL: All required data-testid selectors are in the card HTML."""
        for sel in [
            'data-testid="main-brain-summary"',
            'data-testid="main-brain-verdict"',
            'data-testid="main-brain-narrative"',
            'data-testid="main-brain-next-action"',
            'data-testid="main-brain-invalidation"',
            'data-testid="main-brain-talk"',
            'data-testid="main-brain-speak"',
        ]:
            self.assertIn(sel, MBS_CARD, f"Missing selector: {sel}")

    def test_specM_js_finds_all_dom_nodes(self):
        """specM: JS getElementById calls for every required node."""
        for node_id in [
            'mbs-verdict', 'mbs-edge', 'mbs-gates', 'mbs-reason',
            'mbs-narrative', 'mbs-next-wrap', 'mbs-next-action',
            'mbs-inv-wrap', 'mbs-invalidation', 'mbs-stale-warn',
            'mbs-meter-fill', 'mbs-meter-label',
        ]:
            self.assertIn(f"getElementById('{node_id}')", CENTER_JS,
                          f"JS must getElementById('{node_id}')")

    def test_specN_content_not_clipped_css(self):
        """specN: #main-brain-summary CSS does not hide overflow."""
        mbs_css = re.search(r'#main-brain-summary\{[^}]+\}', SRC)
        self.assertIsNotNone(mbs_css)
        css_text = mbs_css.group(0)
        self.assertNotIn('overflow:hidden', css_text)
        self.assertNotIn('max-height', css_text)

    def test_specO_narrative_text_color_visible(self):
        """specO: #mbs-narrative CSS has a non-transparent color."""
        m = re.search(r'#mbs-narrative\{([^}]+)\}', SRC)
        self.assertIsNotNone(m, "#mbs-narrative CSS must be defined")
        self.assertIn('color:', m.group(1),
                      "#mbs-narrative must have an explicit text color")

    def test_specP_stale_clear_on_update(self):
        """specP: Narrative textContent assignment always overwrites stale value."""
        self.assertIn("mNarr.textContent=", CENTER_JS,
                      "Narrative must be set with textContent= (not +=) to clear stale")

    def test_specQ_old_hero_absent(self):
        """specQ: Old #blh-hero center card is completely removed."""
        self.assertNotIn('<div id="blh-hero">', SRC)
        self.assertNotIn('id="blh-verdict"', SRC)
        self.assertNotIn('id="blh-reasoning-text"', SRC)

    def test_specR_controls_in_card_not_floating(self):
        """specR: Talk/Speak controls are inside #main-brain-summary, not separate."""
        self.assertIn('data-testid="main-brain-talk"', MBS_CARD,
                      "Talk button must be inside #main-brain-summary")
        self.assertIn('data-testid="main-brain-speak"', MBS_CARD,
                      "Speak button must be inside #main-brain-summary")


# ─── M–Z: CSS + JS invariants ─────────────────────────────────────────────────

class TestNewCSS(unittest.TestCase):

    def test_M_mbs_card_css(self):
        """M: #main-brain-summary CSS rule is present."""
        self.assertIn('#main-brain-summary{', SRC)

    def test_N_mbs_verdict_css(self):
        """N: #mbs-verdict CSS is defined."""
        self.assertIn('#mbs-verdict{', SRC)

    def test_O_mbs_pill_css(self):
        """O: .mbs-pill CSS class is defined."""
        self.assertIn('.mbs-pill{', SRC)

    def test_P_mbs_controls_css(self):
        """P: .mbs-controls and .mbs-ctrl-btn CSS are defined."""
        self.assertIn('.mbs-controls{', SRC)
        self.assertIn('.mbs-ctrl-btn{', SRC)

    def test_Q_mbs_narrative_css(self):
        """Q: #mbs-narrative CSS is defined."""
        self.assertIn('#mbs-narrative{', SRC)

    def test_R_meter_css(self):
        """R: #mbs-meter-wrap / #mbs-meter-fill CSS are defined."""
        self.assertIn('#mbs-meter-wrap{', SRC)
        self.assertIn('#mbs-meter-fill{', SRC)


class TestBlhPillPreserved(unittest.TestCase):

    def test_S_blh_pill_css_preserved(self):
        """S: .blh-pill CSS is preserved (Swing V2 lifecycle badge uses it)."""
        self.assertIn('.blh-pill{', SRC)
        self.assertIn('.blh-pill.ok{', SRC)

    def test_T_blh_hero_css_removed(self):
        """T: #blh-hero{ CSS rule is removed."""
        self.assertNotIn('#blh-hero{', SRC)

    def test_U_blhwave_keyframes_removed(self):
        """U: @keyframes blhWave CSS is removed."""
        self.assertNotIn('@keyframes blhWave', SRC)


class TestCenterJS(unittest.TestCase):

    def test_V_getBrain_called(self):
        """V: getBrain(d) is called in center JS block."""
        self.assertIn("getBrain(d)", CENTER_JS)

    def test_W_brain_contract_reads(self):
        """W: JS reads canonical brain.decision, brain.reasons.top, brain.instrument."""
        self.assertIn("bk2.decision", CENTER_JS)
        self.assertIn("bk2.reasons", CENTER_JS)
        self.assertIn("bk2.instrument", CENTER_JS)

    def test_X_isactn_and_gate_vars(self):
        """X: isActn / hdiag / gate vars defined for pill + reason section."""
        for var in ("var isActn=", "var hdiag=", "var hcvdDir=",
                    "var hstructOk=", "var hzoneOk="):
            self.assertIn(var, CENTER_JS, f"{var} must be defined")

    def test_Y_vol_pill_present(self):
        """Y: VOL pill is in the gate pills array."""
        self.assertIn("'VOL'", CENTER_JS)

    def test_Z_speak_uses_speech_synthesis(self):
        """Z: Speak button handler uses window.speechSynthesis."""
        self.assertIn('speechSynthesis', MBS_CARD,
                      "Speak button must use Web Speech API")
        self.assertIn('SpeechSynthesisUtterance', MBS_CARD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
