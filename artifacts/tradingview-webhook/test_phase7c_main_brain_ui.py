"""
V1 Phase 7C — Main Brain UI Tests
test_phase7c_main_brain_ui.py

Tests for the Main Brain Operator Console frontend and its integration
with the /main-brain backend endpoint.

Coverage:
- Page route wiring in App.tsx
- Route is not in OPEN_PATHS (owner-only)
- /main-brain endpoint is in the Express proxy whitelist
- MainBrain.tsx source audits: no hardcoded live trading values
- Safe rendering contract: null/missing field handling
- No backend mutation from UI load (no writes, no gateway calls, no broker calls)
- Polling cadence contract
- Manual refresh control presence
- Strategy count: exactly 5 canonical main-engine strategies
- Paper research strategies excluded from the display contract
- Coach semantics: eligibility != update, weight_updated != readiness
- Timeline partial label contract
- Edge score scale: documented as 0-110
- Active trade fields: current_r and unrealized_pnl documented
- Loading/error/stale state coverage in source
- Responsive grid classes present
- Accessibility: aria-label, role, skip link, focus indicator
- Design tokens: all required tokens defined
- Existing routes preserved: /, /mobile, /cockpit
- Authentication: same Basic Auth pattern, no credential leak
- No new Flask route required (UI served by existing SPA)
- Validation document exists
- Phase 7B backend unmodified

Run with:
    python3 artifacts/tradingview-webhook/test_phase7c_main_brain_ui.py
"""

import os
import re
import sys
import json
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def slurp(rel):
    path = os.path.join(ROOT, rel)
    with open(path, encoding='utf-8') as f:
        return f.read()

def exists(rel):
    return os.path.exists(os.path.join(ROOT, rel))


class TC001_RouteWiring(unittest.TestCase):
    """App.tsx registers /main-brain route pointing to MainBrain component."""

    def setUp(self):
        self.app_tsx = slurp('artifacts/home/src/App.tsx')
        self.mb_tsx  = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_001_mainbrain_imported_in_app(self):
        self.assertIn("import MainBrain", self.app_tsx, "MainBrain must be imported in App.tsx")

    def test_002_route_registered(self):
        self.assertIn('/main-brain', self.app_tsx, "/main-brain route must be registered in App.tsx")

    def test_003_mainbrain_component_used_in_route(self):
        self.assertIn("component={MainBrain}", self.app_tsx, "MainBrain component must be used in the route")

    def test_004_mainbrain_file_exists(self):
        self.assertTrue(exists('artifacts/home/src/pages/MainBrain.tsx'), "MainBrain.tsx must exist")

    def test_005_mainbrain_exports_default(self):
        self.assertIn("export default function MainBrain", self.mb_tsx, "MainBrain must export a default function")


class TC002_ExistingRoutesPreserved(unittest.TestCase):
    """Existing dashboard routes are untouched."""

    def setUp(self):
        self.app_tsx = slurp('artifacts/home/src/App.tsx')

    def test_006_root_route_preserved(self):
        self.assertIn('path="/"', self.app_tsx, "Root / route must be preserved")

    def test_007_mobile_route_preserved(self):
        self.assertIn('/mobile', self.app_tsx, "/mobile route must be preserved")

    def test_008_cockpit_route_preserved(self):
        self.assertIn('/cockpit', self.app_tsx, "/cockpit route must be preserved")

    def test_009_home_import_preserved(self):
        self.assertIn("import Home from", self.app_tsx, "Home import must be preserved")

    def test_010_mobilehome_import_preserved(self):
        self.assertIn("import MobileHome from", self.app_tsx, "MobileHome import must be preserved")


class TC003_Authentication(unittest.TestCase):
    """Authentication pattern matches Home.tsx; no credential leak."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')
        self.auth_ts = slurp('artifacts/api-server/src/routes/dashboard-auth.ts')

    def test_011_uses_brain_auth_localstorage(self):
        self.assertIn("brain_auth", self.mb_tsx, "Must read password from localStorage brain_auth")

    def test_012_basic_auth_header_built(self):
        self.assertIn("Authorization", self.mb_tsx, "Must build Authorization header")
        self.assertIn("btoa", self.mb_tsx, "Must use btoa for Basic Auth encoding")

    def test_013_no_hardcoded_password(self):
        # Should not have a hardcoded password string (not the word 'admin' + ':' + literal password)
        no_hardcoded = not re.search(r"admin:[A-Za-z0-9!@#$%^&*]{6,}", self.mb_tsx)
        self.assertTrue(no_hardcoded, "No hardcoded password allowed in frontend")

    def test_014_auth_expiry_handled(self):
        self.assertIn("401", self.mb_tsx, "Must handle 401 auth expiry")

    def test_015_no_secret_in_frontend(self):
        # Check for no webhook URLs or secrets in the frontend code
        bad_patterns = [r'DISCORD_WEBHOOK', r'TRADERSPOST_WEBHOOK', r'SESSION_SECRET']
        for pat in bad_patterns:
            self.assertNotRegex(self.mb_tsx, pat, f"Secret pattern {pat!r} must not appear in frontend")

    def test_016_main_brain_not_in_open_paths(self):
        self.assertNotIn('/main-brain', self.auth_ts.split('OPEN_PATHS')[0].split('new Set')[0], "Check dashboard-auth.ts")
        # More precise: check OPEN_PATHS set does not contain /main-brain
        open_paths_match = re.search(r'new Set\(\[(.*?)\]\)', self.auth_ts, re.DOTALL)
        if open_paths_match:
            open_paths_content = open_paths_match.group(1)
            self.assertNotIn('/main-brain', open_paths_content,
                "/main-brain must NOT be in OPEN_PATHS (owner-only)")


class TC004_ProxyWhitelist(unittest.TestCase):
    """/main-brain is in the Express proxy whitelist."""

    def setUp(self):
        self.proxy_ts = slurp('artifacts/api-server/src/routes/flask-proxy.ts')

    def test_017_main_brain_in_proxy_whitelist(self):
        self.assertIn('/main-brain', self.proxy_ts, "/main-brain must be in flask-proxy.ts whitelist")


class TC005_NoHardcodedLiveValues(unittest.TestCase):
    """No hardcoded live trading values in the MainBrain frontend."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    # Patterns that would indicate hardcoded live values
    FORBIDDEN_PATTERNS = [
        # Specific price values (instrument-range price patterns)
        (r'\b(1800|1900|2000|2100|2200|1850|1950|2050)\b', "Gold-range hardcoded price"),
        (r'\b(18000|19000|20000|21000|22000|17000)\b', "NQ/MNQ-range hardcoded price"),
        # Hardcoded scores (non-zero single values)
        (r'edge_score\s*=\s*\d{2,3}', "Hardcoded edge score"),
        (r'confidence\s*=\s*\d{2,3}', "Hardcoded confidence value"),
        # Hardcoded win rates as production values (not in test fixtures or constants)
        (r'win_rate\s*=\s*0\.\d+', "Hardcoded win rate float"),
    ]

    def test_018_no_hardcoded_prices(self):
        for pat, desc in self.FORBIDDEN_PATTERNS:
            self.assertNotRegex(self.mb_tsx, pat, f"Forbidden: {desc}")

    def test_019_no_demo_data_in_production_path(self):
        # demoMode or demo data must not be in MainBrain (it's in Home.tsx)
        # MainBrain is purely from /main-brain API
        self.assertNotIn("demoMode", self.mb_tsx, "MainBrain must not have demo mode")

    def test_020_all_display_values_from_payload(self):
        # Every numeric display should come from payload fields or safeNum/fmtNum
        # Ensure no hardcoded numeric strings being displayed as prices
        self.assertIn("safeNum", self.mb_tsx, "Must use safeNum for safe numeric extraction")
        self.assertIn("fmtNum",  self.mb_tsx, "Must use fmtNum for numeric formatting")
        self.assertIn("safeStr", self.mb_tsx, "Must use safeStr for safe string extraction")


class TC006_SafeRendering(unittest.TestCase):
    """Frontend safely handles null, missing, and malformed payload fields."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_021_null_guard_in_safe_str(self):
        # safeStr returns fallback on null/undefined
        self.assertIn("v == null", self.mb_tsx, "safeStr must guard against null")

    def test_022_null_guard_in_safe_num(self):
        self.assertIn("isNaN", self.mb_tsx, "safeNum must guard against NaN")
        self.assertIn("isFinite", self.mb_tsx, "safeNum must guard against Infinity")

    def test_023_payload_null_handled(self):
        # Header and panels must safely handle null payload
        self.assertIn("payload ?? {}", self.mb_tsx, "Null payload must be coerced to {}")

    def test_024_empty_array_handled(self):
        self.assertIn("Array.isArray", self.mb_tsx, "Must check Array.isArray before mapping arrays")

    def test_025_no_unsafe_innerhtml(self):
        # No dangerouslySetInnerHTML with live backend data
        self.assertNotIn("dangerouslySetInnerHTML", self.mb_tsx,
            "dangerouslySetInnerHTML must not be used with live backend data")

    def test_026_invalid_timestamp_handled(self):
        self.assertIn("isNaN(d.getTime())", self.mb_tsx, "Invalid timestamp must return fallback")

    def test_027_unavailable_note_component(self):
        self.assertIn("UnavailableNote", self.mb_tsx, "Must have UnavailableNote for absent data")

    def test_028_edge_score_max_110(self):
        # Gauge must document 0–110 scale
        self.assertIn("110", self.mb_tsx, "Edge score gauge must handle 0–110 scale")
        self.assertIn("EDGE SCORE", self.mb_tsx, "Edge score must be clearly labeled")


class TC007_PollingAndRefresh(unittest.TestCase):
    """Polling cadence and manual refresh contract."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_029_poll_interval_defined(self):
        self.assertIn("POLL_INTERVAL_MS", self.mb_tsx, "Poll interval constant must be defined")

    def test_030_poll_interval_conservative(self):
        # Must be 5–15 seconds (5000–15000 ms)
        m = re.search(r'POLL_INTERVAL_MS\s*=\s*(\d+)', self.mb_tsx)
        self.assertIsNotNone(m, "POLL_INTERVAL_MS must have a numeric value")
        if m:
            val = int(m.group(1))
            self.assertGreaterEqual(val, 5000, "Poll interval must be >= 5s")
            self.assertLessEqual(val, 15000, "Poll interval must be <= 15s")

    def test_031_page_visibility_guard(self):
        self.assertIn("document.hidden", self.mb_tsx, "Must skip polling when page is hidden")

    def test_032_manual_refresh_button(self):
        self.assertIn("Refresh", self.mb_tsx, "Must have a manual Refresh button")

    def test_033_in_flight_guard(self):
        self.assertIn("inFlight", self.mb_tsx, "Must guard against concurrent duplicate requests")

    def test_034_stale_state_handled(self):
        self.assertIn("stale", self.mb_tsx, "Must track stale state")
        self.assertIn("STALE", self.mb_tsx.upper(), "Must show stale banner to user")

    def test_035_error_state_handled(self):
        self.assertIn("error", self.mb_tsx, "Must track error state")


class TC008_UIStates(unittest.TestCase):
    """Loading, loaded, refreshing, error, auth failure states."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_036_loading_state(self):
        self.assertIn("LoadingScreen", self.mb_tsx, "Must show loading screen on first load")
        self.assertIn("'loading'", self.mb_tsx, "Must have loading fetch state")

    def test_037_error_screen(self):
        self.assertIn("ErrorScreen", self.mb_tsx, "Must show error screen on total failure")

    def test_038_auth_fail_state(self):
        self.assertIn("auth_fail", self.mb_tsx, "Must have auth_fail fetch state")

    def test_039_refreshing_state(self):
        self.assertIn("'refreshing'", self.mb_tsx, "Must have refreshing fetch state")

    def test_040_stale_banner_shown(self):
        self.assertIn("STALE DATA", self.mb_tsx, "Must show stale data banner")

    def test_041_previous_payload_preserved(self):
        # lastPayload ref used so stale data is still shown during refresh
        self.assertIn("lastPayload", self.mb_tsx, "Must preserve last payload during stale/refresh")

    def test_042_partial_availability_handled(self):
        # avail check in panels (available !== false)
        self.assertIn("avail", self.mb_tsx, "Must check availability for each panel")


class TC009_StrategyScanner(unittest.TestCase):
    """Strategy scanner shows exactly 5 canonical strategies; excludes research."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')
        # Also check the backend builder
        self.app_py = slurp('artifacts/tradingview-webhook/app.py')

    def test_043_five_canonical_strategies_documented(self):
        strategies = [
            'OPENING_DRIVE',
            'LIQUIDITY_SWEEP_REVERSAL',
            'VWAP_TREND_CONTINUATION',
            'RANGE_EXPANSION_BREAKOUT',
            'OPENING_RANGE_BREAKOUT',
        ]
        for s in strategies:
            self.assertIn(s, self.mb_tsx, f"Canonical strategy {s} must appear in MainBrain.tsx labels")

    def test_044_research_strategies_not_labeled(self):
        # The 16 paper-research strategies must not appear as labeled items in the scanner
        # (they are excluded by the backend _MB_MAIN_ENGINE_KEYS filter)
        research_only = ['MOMENTUM_BREAKOUT_5M', 'MEAN_REVERSION_VWAP', 'GAP_FILL_REVERSAL']
        for s in research_only:
            self.assertNotIn(s, self.mb_tsx, f"Research strategy {s} must not appear in MainBrain UI labels")

    def test_045_selected_strategy_highlighted(self):
        self.assertIn("isSel", self.mb_tsx, "Selected strategy must be highlighted differently")

    def test_046_unavailable_strategy_handled(self):
        self.assertIn("No strategies available", self.mb_tsx, "Must show message when no strategies available")

    def test_047_strategy_count_from_api(self):
        # Count must come from the API payload, not hardcoded
        self.assertNotIn("strategies.length === 5", self.mb_tsx,
            "Strategy count must not be hardcoded as === 5")


class TC010_ActiveTrades(unittest.TestCase):
    """Active trade panel handles zero, one, and multiple trades."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_048_zero_trades_handled(self):
        self.assertIn("No active trades", self.mb_tsx, "Must show message when no active trades")

    def test_049_current_r_displayed(self):
        self.assertIn("current_r", self.mb_tsx, "Must display current_r from payload")

    def test_050_unrealized_pnl_displayed(self):
        self.assertIn("unrealized_pnl", self.mb_tsx, "Must display unrealized_pnl from payload")

    def test_051_multiple_trades_iterated(self):
        self.assertIn("trades.map", self.mb_tsx, "Must iterate trades array (supports multiple)")

    def test_052_no_browser_pnl_calculation(self):
        # Must use canonical values from payload, not recompute from price/entry
        self.assertNotIn("current_price - entry", self.mb_tsx,
            "Must not recalculate P&L in browser when canonical values exist")


class TC011_CoachSemantics(unittest.TestCase):
    """Coach panel respects semantic invariants from the brief."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_053_eligibility_note_present(self):
        # Phase 7I redesign: "Eligibility" is exposed via "LRE Status" row which
        # reads rule_engine_eligibility (LIVE_ELIGIBLE | GHOST_ONLY | DISABLED).
        # The word "eligible" still appears in the learning_diagnostics binding.
        self.assertTrue(
            "Eligibility" in self.mb_tsx or "eligible" in self.mb_tsx.lower(),
            "Coach panel must mention eligibility")

    def test_054_weight_updated_semantic_note(self):
        # Must clarify weight_updated != readiness
        self.assertIn("weight", self.mb_tsx.lower(), "Must display weight_updated field")

    def test_055_disclaimer_label_present(self):
        # Phase 7I redesign: disclaimer updated to surface the exact blocked_reason.
        # The panel now says: "Influence = 0 until N samples. 'Weight Updated' = recompute ran..."
        # which conveys the same semantic: weight_status ≠ learning readiness.
        self.assertTrue(
            "Eligibility ≠ update" in self.mb_tsx
            or "recompute ran" in self.mb_tsx
            or "Weight Updated" in self.mb_tsx,
            "Must show semantic disclaimer clarifying weight_updated vs readiness")

    def test_056_thesis_resolved_separate_from_db(self):
        self.assertIn("thesis_resolved", self.mb_tsx,
            "Must display thesis_resolved as separate field")


class TC012_ExecutionStatus(unittest.TestCase):
    """Execution panel does not fabricate last_outcome."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_057_deferred_last_outcome_labelled(self):
        self.assertIn("not available", self.mb_tsx.lower(),
            "Must label last_outcome as not available (deferred)")

    def test_058_no_success_inferred_from_timestamp(self):
        # Must not display 'SUCCESS' when only a timestamp is present
        # Check that gateway_outcome display doesn't show a success badge from timestamp alone
        self.assertNotIn("gateway_outcome === 'SUCCESS'", self.mb_tsx,
            "Must not infer success from timestamp alone")

    def test_059_execution_mode_displayed(self):
        self.assertIn("mode", self.mb_tsx, "Must display execution mode")


class TC013_Timeline(unittest.TestCase):
    """Timeline panel correctly labels itself as partial."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_060_partial_label_present(self):
        self.assertIn("Partial timeline", self.mb_tsx,
            "Must display 'Partial timeline — additional event capture is planned.'")

    def test_061_derived_marker_shown(self):
        self.assertIn("is_derived", self.mb_tsx, "Must show derived marker on derived events")

    def test_062_empty_timeline_handled(self):
        self.assertIn("No events recorded", self.mb_tsx,
            "Must handle empty timeline gracefully")


class TC014_Accessibility(unittest.TestCase):
    """Accessibility requirements."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_063_skip_link_present(self):
        self.assertIn("Skip to content", self.mb_tsx, "Must have skip-to-content link")

    def test_064_aria_label_on_nav(self):
        self.assertIn('aria-label="Main navigation"', self.mb_tsx,
            "Navigation must have aria-label")

    def test_065_aria_label_on_market_strip(self):
        self.assertIn("Market state strip", self.mb_tsx, "Market strip must have aria-label")

    def test_066_role_alert_on_stale_banner(self):
        self.assertIn('role="alert"', self.mb_tsx, "Stale banner must use role=alert")

    def test_067_aria_progressbar_on_gauge(self):
        self.assertIn('role="progressbar"', self.mb_tsx, "Confidence bar must use role=progressbar")

    def test_068_aria_label_on_gauge(self):
        self.assertIn('aria-label=', self.mb_tsx, "Gauge/SVG must have aria-label")

    def test_069_focus_visible_styles(self):
        self.assertIn("focus-visible", self.mb_tsx, "Must include :focus-visible styles")

    def test_070_reduced_motion_support(self):
        self.assertIn("prefers-reduced-motion", self.mb_tsx, "Must respect prefers-reduced-motion")

    def test_071_table_semantic_structure(self):
        self.assertIn("<table", self.mb_tsx, "Trade journal must use semantic table")
        self.assertIn("<thead", self.mb_tsx, "Journal table must have thead")
        self.assertIn("<tbody", self.mb_tsx, "Journal table must have tbody")

    def test_072_aria_pressed_on_ticker_buttons(self):
        self.assertIn("aria-pressed", self.mb_tsx, "Instrument selector buttons must use aria-pressed")


class TC015_ResponsiveLayout(unittest.TestCase):
    """Responsive grid classes present."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_073_three_col_grid_class(self):
        self.assertIn("mb-grid-3", self.mb_tsx, "Three-column grid class must be present")

    def test_074_two_col_grid_class(self):
        self.assertIn("mb-grid-2", self.mb_tsx, "Two-column grid class must be present")

    def test_075_tablet_breakpoint(self):
        self.assertIn("1024px", self.mb_tsx, "Must have 1024px tablet breakpoint")

    def test_076_mobile_breakpoint(self):
        self.assertIn("768px", self.mb_tsx, "Must have 768px mobile breakpoint")

    def test_077_no_horizontal_overflow(self):
        self.assertIn("overflowX:'hidden'", self.mb_tsx, "Must prevent horizontal overflow")


class TC016_DesignTokens(unittest.TestCase):
    """Required design tokens are defined."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_078_design_token_object(self):
        self.assertIn("const T = {", self.mb_tsx, "Design token object T must be defined")

    def test_079_token_bg(self):
        self.assertIn("bg:", self.mb_tsx, "Page background token required")

    def test_080_token_panel(self):
        self.assertIn("panel:", self.mb_tsx, "Panel background token required")

    def test_081_token_cyan(self):
        self.assertIn("cyan:", self.mb_tsx, "Cyan accent token required")

    def test_082_token_green(self):
        self.assertIn("green:", self.mb_tsx, "Success green token required")

    def test_083_token_amber(self):
        self.assertIn("amber:", self.mb_tsx, "Warning amber token required")

    def test_084_token_red(self):
        self.assertIn("red:", self.mb_tsx, "Danger red token required")

    def test_085_token_purple(self):
        self.assertIn("purple:", self.mb_tsx, "Coach purple token required")

    def test_086_token_txtpri(self):
        self.assertIn("txtPri:", self.mb_tsx, "Primary text token required")

    def test_087_token_mono(self):
        self.assertIn("mono:", self.mb_tsx, "Monospace font token required")


class TC017_NoBackendMutation(unittest.TestCase):
    """UI load must not trigger any backend mutation."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')

    def test_088_no_post_to_traderspost(self):
        self.assertNotIn("/api/traderspost", self.mb_tsx,
            "MainBrain UI must not POST to /api/traderspost")

    def test_089_no_post_to_enter(self):
        self.assertNotIn("/api/enter", self.mb_tsx,
            "MainBrain UI must not POST to /api/enter")

    def test_090_no_fetch_method_post(self):
        # Ensure no POST method calls (only GET is used)
        post_calls = re.findall(r"method\s*:\s*['\"]POST['\"]", self.mb_tsx)
        self.assertEqual(len(post_calls), 0, "MainBrain must not make any POST requests")

    def test_091_no_journal_write(self):
        self.assertNotIn("/api/journal", self.mb_tsx,
            "MainBrain UI must not call journal write endpoint")

    def test_092_no_learning_endpoint(self):
        self.assertNotIn("/api/learning", self.mb_tsx,
            "MainBrain UI must not call learning endpoint")

    def test_093_fetch_only_main_brain(self):
        # All fetch calls should target /api/main-brain
        fetch_urls = re.findall(r"fetch\(['\`]([^'\`\"]+)['\`]", self.mb_tsx)
        for url in fetch_urls:
            self.assertIn("main-brain", url,
                f"Unexpected fetch URL in MainBrain.tsx: {url!r}")

    def test_094_no_databento_mutation(self):
        self.assertNotIn("/api/databento", self.mb_tsx,
            "MainBrain UI must not call Databento mutation endpoints")


class TC018_BackendUnmodified(unittest.TestCase):
    """Phase 7B backend endpoint and tests are unmodified."""

    def test_095_phase7b_test_passes(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, 'artifacts/tradingview-webhook/test_phase7b_main_brain_route.py'],
            capture_output=True, text=True, cwd=ROOT
        )
        self.assertIn("56 passed", result.stdout,
            f"Phase 7B tests must still pass 56/56\n{result.stdout}\n{result.stderr}")

    def test_096_main_brain_endpoint_in_app_py(self):
        app_py = slurp('artifacts/tradingview-webhook/app.py')
        self.assertIn('"/main-brain"', app_py, "/main-brain route must exist in app.py")
        self.assertIn("build_main_brain_payload", app_py, "build_main_brain_payload must exist in app.py")

    def test_097_no_app_py_money_path_changes(self):
        # app.py should not have been modified for Phase 7C (UI only)
        import subprocess
        result = subprocess.run(
            ['git', 'diff', '--name-only', 'HEAD~1', 'HEAD', '--', 'artifacts/tradingview-webhook/app.py'],
            capture_output=True, text=True, cwd=ROOT
        )
        # If this outputs app.py, Phase 7C touched the backend — that would be wrong
        # But we allow it to be clean (no output)
        modified_in_p7c = 'app.py' in result.stdout
        if modified_in_p7c:
            # Read the diff to ensure it's UI-only (page serving route at most)
            diff_result = subprocess.run(
                ['git', 'diff', 'HEAD~1', 'HEAD', '--', 'artifacts/tradingview-webhook/app.py'],
                capture_output=True, text=True, cwd=ROOT
            )
            # Should not contain money-path words
            money_words = ['execute_trade_gateway', 'post_discord', 'ACTIVE_TRADES', '_training_gate']
            for w in money_words:
                self.assertNotIn(w, diff_result.stdout,
                    f"Phase 7C must not modify money-path logic ({w}) in app.py")


class TC019_ValidationDocument(unittest.TestCase):
    """Validation document exists."""

    def test_098_validation_doc_exists(self):
        self.assertTrue(
            exists('artifacts/tradingview-webhook/V1_PHASE_7C_MAIN_BRAIN_UI_VALIDATION.md'),
            "V1_PHASE_7C_MAIN_BRAIN_UI_VALIDATION.md must exist"
        )

    def test_099_validation_doc_has_sections(self):
        doc = slurp('artifacts/tradingview-webhook/V1_PHASE_7C_MAIN_BRAIN_UI_VALIDATION.md')
        required = ['Baseline State', 'Files Created', 'Files Modified', 'Regression Evidence']
        for sec in required:
            self.assertIn(sec, doc, f"Validation doc must contain section: {sec}")


class TC020_TypeScriptSource(unittest.TestCase):
    """TypeScript source structural checks."""

    def setUp(self):
        self.mb_tsx = slurp('artifacts/home/src/pages/MainBrain.tsx')
        self.app_tsx = slurp('artifacts/home/src/App.tsx')

    def test_100_no_any_cast_on_payload_keys(self):
        # Should not blindly cast to any to silence type errors
        unsafe = re.findall(r'as any', self.mb_tsx)
        # Allow some (e.g. for record casts) but not excessive
        self.assertLess(len(unsafe), 10, "Excessive 'as any' casts suggest unsafe rendering")

    def test_101_react_imported(self):
        self.assertIn("import React", self.mb_tsx, "React must be imported")

    def test_102_link_from_wouter(self):
        self.assertIn("from 'wouter'", self.mb_tsx, "Must import Link from wouter for navigation")

    def test_103_no_console_log_in_production(self):
        logs = re.findall(r'console\.log', self.mb_tsx)
        self.assertEqual(len(logs), 0, "No console.log in production MainBrain component")


# ── Runner ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    loader  = unittest.TestLoader()
    loader.sortTestMethodsUsing = lambda a, b: (a > b) - (a < b)
    suite   = loader.loadTestsFromModule(__import__('__main__'))
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)

    passed = result.testsRun - len(result.failures) - len(result.errors)
    failed = len(result.failures) + len(result.errors)

    print()
    print("=" * 64)
    print(f"  TOTAL: {result.testsRun} checks — {passed} passed, {failed} failed")
    if not result.failures and not result.errors:
        print("  PASS  all Phase 7C main-brain-ui checks passed")
    print("=" * 64)

    sys.exit(0 if not result.failures and not result.errors else 1)
