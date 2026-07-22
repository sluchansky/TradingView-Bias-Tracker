"""
tests/test_brain_panel_simplification.py

Tests A-R: verify that the #mod-brain panel retains only the
canonical set (avatar, narrative, next-action, invalidation, chat/speak)
and that duplicated content has been removed.

No server is started — we parse the HTML/JS template directly from app.py.
"""
import re
import sys
import os
import unittest

APP = os.path.join(os.path.dirname(__file__), "app.py")


def _load_source():
    with open(APP, "r", encoding="utf-8") as f:
        return f.read()


def _extract_brain_panel(src):
    """Return the HTML string of #mod-brain (first occurrence)."""
    m = re.search(
        r'<div[^>]*id=["\']mod-brain["\'][^>]*>(.*?)</div><!--\s*/\s*#mod-brain\s*-->',
        src,
        re.DOTALL,
    )
    if not m:
        m = re.search(
            r'(<div[^>]*id=["\']mod-brain["\'][^>]*>)',
            src,
            re.DOTALL,
        )
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
                    return src[start : i + 6]
            i += 1
        return ""
    return m.group(0)


def _extract_render_main_brain(src):
    """Return the JS body of renderMainBrain function."""
    m = re.search(r'function renderMainBrain\(d\)\{(.*?)^\}', src,
                  re.DOTALL | re.MULTILINE)
    return m.group(0) if m else ""


SRC = _load_source()
BRAIN_HTML = _extract_brain_panel(SRC)
RENDER_JS = _extract_render_main_brain(SRC)


class TestBrainPanelRemovals(unittest.TestCase):
    """Tests A-G: confirm duplicated elements are gone from #mod-brain."""

    def test_A_no_brain_intel_grid(self):
        """A: brain-intel 4-question grid is removed."""
        self.assertNotIn('class="brain-intel"', BRAIN_HTML,
                         "brain-intel grid must be removed from #mod-brain")

    def test_B_no_mb_market_visible(self):
        """B: WHAT I SEE list is removed (mb-market not a real list)."""
        if 'id="mb-market"' in BRAIN_HTML:
            self.assertIn('display:none', BRAIN_HTML,
                          "mb-market must be hidden or removed from #mod-brain")

    def test_C_no_mb_unified(self):
        """C: DECISION / unified block is removed."""
        self.assertNotIn('id="mb-unified"', BRAIN_HTML,
                         "mb-unified must be removed from #mod-brain")

    def test_D_no_brain_feed_wrap(self):
        """D: LIVE THINKING feed wrapper is removed."""
        self.assertNotIn('class="brain-feed-wrap"', BRAIN_HTML,
                         "brain-feed-wrap must be removed from #mod-brain")

    def test_E_no_brain_details_toggle(self):
        """E: Details collapse button is removed."""
        self.assertNotIn('brain-details-btn', BRAIN_HTML,
                         "brain-details-btn must be removed from #mod-brain")

    def test_F_no_brain_details_div(self):
        """F: brain-details collapsible content panel is removed."""
        self.assertNotIn('id="brain-details"', BRAIN_HTML,
                         "brain-details div must be removed from #mod-brain")

    def test_G_no_brain_ctx_row(self):
        """G: context strip row (ticker · mode · verdict) is removed."""
        self.assertNotIn('brain-ctx-row', BRAIN_HTML,
                         "brain-ctx-row must be removed from #mod-brain")


class TestBrainPanelRetentions(unittest.TestCase):
    """Tests H-N: confirm required elements remain in #mod-brain."""

    def test_H_avatar_orb_present(self):
        """H: avatar orb is present."""
        self.assertIn('id="mb-orb"', BRAIN_HTML,
                      "mb-orb avatar must remain in #mod-brain")

    def test_I_dicebear_avatar_present(self):
        """I: custom inline SVG avatar element is present."""
        self.assertIn('id="mb-dicebear-avatar"', BRAIN_HTML,
                      "mb-dicebear-avatar must remain in #mod-brain")

    def test_J_brain_caption_present(self):
        """J: brain narrative caption element is present."""
        self.assertIn('id="mb-caption"', BRAIN_HTML,
                      "mb-caption (brain narrative) must remain in #mod-brain")

    def test_K_next_action_element_present(self):
        """K: next-action wrap and value elements are present."""
        self.assertIn('id="mb-next-action-wrap"', BRAIN_HTML,
                      "mb-next-action-wrap must be in #mod-brain")
        self.assertIn('id="mb-next-action"', BRAIN_HTML,
                      "mb-next-action must be in #mod-brain")

    def test_L_invalidation_element_present(self):
        """L: invalidation wrap and value elements are present."""
        self.assertIn('id="mb-inval-wrap"', BRAIN_HTML,
                      "mb-inval-wrap must be in #mod-brain")
        self.assertIn('id="mb-inval-text"', BRAIN_HTML,
                      "mb-inval-text must be in #mod-brain")

    def test_M_talk_to_ai_present(self):
        """M: Talk to AI chat input and send button present."""
        self.assertIn('id="mb-chat-input"', BRAIN_HTML,
                      "mb-chat-input must remain in #mod-brain")
        self.assertIn('id="mb-chat-send"', BRAIN_HTML,
                      "mb-chat-send must remain in #mod-brain")

    def test_N_speak_control_present(self):
        """N: Speak / voice controls present."""
        self.assertIn('id="mb-speak-inp"', BRAIN_HTML,
                      "mb-speak-inp (Speak) must remain in #mod-brain")
        self.assertIn('id="mb-voice-toggle"', BRAIN_HTML,
                      "mb-voice-toggle must remain in #mod-brain")


class TestBrainPanelLayout(unittest.TestCase):
    """Tests O-R: layout structure and rendering correctness."""

    def test_O_brain_surface_2col_layout(self):
        """O: new 2-col brain-surface wrapper is present."""
        self.assertIn('class="brain-surface"', BRAIN_HTML,
                      "brain-surface 2-col wrapper must be in #mod-brain")

    def test_P_brain_av_side_present(self):
        """P: brain-av-side (avatar column) is present."""
        self.assertIn('class="brain-av-side"', BRAIN_HTML,
                      "brain-av-side must be in #mod-brain")

    def test_Q_brain_narr_side_present(self):
        """Q: brain-narr-side (narrative column) is present."""
        self.assertIn('class="brain-narr-side"', BRAIN_HTML,
                      "brain-narr-side must be in #mod-brain")

    def test_R_renderMainBrain_uses_getBrain_not_new_calc(self):
        """R: renderMainBrain calls getBrain(d) for compact block — no new scoring."""
        self.assertIn('getBrain(d)', RENDER_JS,
                      "renderMainBrain must use getBrain(d) for brain contract values")
        self.assertNotIn('_anFill(\'mb-market\'', RENDER_JS,
                         "mb-market _anFill must be removed from renderMainBrain")
        self.assertNotIn('_anFill(\'mb-strategy\'', RENDER_JS,
                         "mb-strategy _anFill must be removed from renderMainBrain")
        self.assertNotIn('_anFill(\'mb-risk\'', RENDER_JS,
                         "mb-risk _anFill must be removed from renderMainBrain")
        self.assertNotIn('_anFill(\'mb-tm\'', RENDER_JS,
                         "mb-tm _anFill must be removed from renderMainBrain")
        self.assertNotIn('mb-unified', RENDER_JS,
                         "mb-unified rendering must be removed from renderMainBrain")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
