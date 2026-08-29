import { expect, test, type Page, type Route } from '@playwright/test';

const password = process.env.DASHBOARD_PASSWORD;
if (!password) {
  throw new Error('DASHBOARD_PASSWORD is required for the authenticated operator-console regression.');
}

type DecisionState = 'WAIT' | 'READY';

function operatorPresentation(state: DecisionState) {
  const ready = state === 'READY';
  return {
    verdict: state,
    is_actionable: ready,
    candidate_direction: ready ? 'Long' : 'Short',
    actionable_direction: ready ? 'Long' : null,
    candidate_label: ready ? 'LONG CANDIDATE — READY' : 'SHORT CANDIDATE — WAIT',
    reasoning: ready
      ? 'READY_CANONICAL_REASON: all strict gates aligned'
      : 'WAIT_CANONICAL_REASON: structure confirmation pending',
    vwap: {
      side: ready ? 'ABOVE' : 'BELOW',
      wording: ready
        ? 'Price above VWAP — bullish regime'
        : 'Price below VWAP — bearish regime',
    },
    regime_wording: ready ? 'Bullish regime' : 'Bearish regime',
    waiting_for: ready ? [] : [{ key: 'structure', label: 'Structure confirmation', structure: true }],
  };
}

function statusFixture(state: DecisionState) {
  const op = operatorPresentation(state);
  const ready = state === 'READY';
  return {
    status: 'running',
    verdict: state,
    is_actionable: ready,
    strict_direction: op.candidate_direction,
    strict_reason: op.reasoning,
    operator_presentation: op,
    vwap_presentation: op.vwap,
    regime_wording: op.regime_wording,
    active_ticker: 'MNQ',
    trading_mode: 'SCALP',
    market_status: 'OPEN',
    current_price: ready ? 20125 : 19875,
    vwap_value: 20000,
    edge_score: ready ? 82 : 68,
    edge_grade: ready ? 'A' : 'B',
    main_brain: {
      status: ready ? 'WAIT' : 'READY',
      favored_direction: ready ? 'SHORT' : 'LONG',
      direction: ready ? 'SHORT' : 'LONG',
      wait_reason: 'CONTRADICTORY_LEGACY_REASON',
      what_now: 'CONTRADICTORY_LEGACY_REASON',
      edge_score: ready ? 82 : 68,
      edge_grade: ready ? 'A' : 'B',
    },
    brain: {
      decision: {
        verdict: ready ? 'WAIT' : 'READY',
        is_ready: !ready,
        direction: ready ? 'Short' : null,
        next_action: null,
      },
      score: { value: ready ? 82 : 68, max: 110, grade: ready ? 'A' : 'B' },
      reasons: { top: ['CONTRADICTORY_LEGACY_REASON'] },
      trade_plan: null,
    },
    confluences: { vwap_value: 20000 },
  };
}

function mainBrainFixture(state: DecisionState) {
  const op = operatorPresentation(state);
  const ready = state === 'READY';
  return {
    market: {
      selected_instrument: 'MNQ',
      trading_mode: 'SCALP',
      current_price: ready ? 20125 : 19875,
      vwap_value: 20000,
      session: { status: 'OPEN' },
    },
    verdict: {
      readiness: ready ? 'WAIT' : 'READY',
      is_actionable: !ready,
      direction: ready ? 'Short' : 'Long',
      strict_reason: 'CONTRADICTORY_LEGACY_REASON',
      edge_score: ready ? 82 : 68,
      edge_grade: ready ? 'A' : 'B',
      edge_max: 110,
      edge_components: [],
    },
    operator_presentation: op,
    main_brain: { voice: 'CONTRADICTORY_LEGACY_REASON' },
    candidate_preview: { status: ready ? 'POTENTIAL' : 'READY', direction: ready ? 'Short' : 'Long' },
    strategy_scanner: {},
    left_brain: {},
    thesis: {
      direction: ready ? 'LONG' : 'LONG',
      confidence: ready ? 61 : 53,
      lifecycle_status: ready ? 'CONFIRMED' : 'FORMING',
      mode: 'SCALP',
      reason: ready ? 'Long thesis confirmed.' : 'Long thesis remains active while confirmation develops.',
    },
    active_trades: { trades: [] },
  };
}

async function installAuthenticatedFixture(page: Page, current: { state: DecisionState }) {
  let authenticatedCalls = 0;
  const mockAuth = process.env.OPERATOR_CONSOLE_MOCK_AUTH === '1';
  if (mockAuth) {
    await page.route('**/api/operator-console-auth-check', route => route.fulfill({ status: 204 }));
  }
  const handler = async (route: Route) => {
    const request = route.request();
    const authorization = request.headers().authorization;
    if (!authorization) {
      await route.continue();
      return;
    }
    if (!mockAuth) {
      const authCheck = await page.request.get('/api/operator-console-auth-check', {
        headers: { Authorization: authorization },
      });
      if (authCheck.status() !== 204) {
        throw new Error(`Protected development endpoint rejected the dashboard credential (${authCheck.status()}).`);
      }
    }
    authenticatedCalls += 1;
    const fixture = request.url().includes('/api/main-brain')
      ? mainBrainFixture(current.state)
      : statusFixture(current.state);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture) });
  };
  await page.route('**/api/status**', handler);
  await page.route('**/api/main-brain**', handler);
  await page.route('**/api/databento-status**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      enabled: true,
      status: {
        connected: true,
        status: 'LIVE',
        last_ts: new Date().toISOString(),
        instruments: {
          MNQ: { price: 20000, vwap: 20000 },
          MGC: { price: 20000, vwap: 20000 },
        },
      },
    }),
  }));
  await page.route('**/api/databento-bars**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      enabled: true,
      bars: [{
        ts: new Date().toISOString(),
        timestamp: new Date().toISOString(),
        time: Date.now(),
        open: 20000,
        high: 20001,
        low: 19999,
        close: 20000,
        volume: 10,
        vwap: 20000,
      }],
    }),
  }));
  return () => expect(authenticatedCalls, 'the browser must cross the real password gate').toBeGreaterThan(0);
}

async function login(page: Page, path: string) {
  await page.goto(path);
  const input = page.locator('input[type="password"]').first();
  if (await input.isVisible()) {
    await input.fill(password);
    await page.locator('button[type="submit"], [data-testid="main-brain-login-submit"]').first().click();
  }
}

const consumers = [
  {
    name: 'Main Brain',
    path: '/',
    wait: async (page: Page) => {
      await page.getByTestId('link-desk').click();
      await expect(page.getByTestId('active-persistent-thesis-headline')).toHaveText('LONG');
      await expect(page.getByTestId('authoritative-entry-status')).toContainText('WAIT');
      await expect(page.getByTestId('live-entry-candidate')).toHaveText('SHORT 68/110 — WAIT');
      await expect(page.getByTestId('opposing-candidate-note')).toHaveText(
        'Opposing SHORT candidate detected; thesis remains LONG.',
      );
      await expect(page.getByTestId('main-brain-candidate-label').first()).toHaveText('SHORT CANDIDATE — WAIT');
      await expect(page.getByText('WAIT_CANONICAL_REASON: structure confirmation pending', { exact: false }).first()).toBeVisible();
      await expect(page.getByTestId('main-brain-vwap')).toHaveText('Price below VWAP — bearish regime');
    },
    ready: async (page: Page) => {
      await page.getByTestId('link-desk').click();
      await expect(page.getByTestId('active-persistent-thesis-headline')).toHaveText('LONG');
      await expect(page.getByTestId('authoritative-entry-status')).toContainText('READY');
      await expect(page.getByTestId('live-entry-candidate')).toHaveText('LONG 82/110 — READY');
      await expect(page.getByTestId('opposing-candidate-note')).toHaveCount(0);
      await expect(page.getByTestId('main-brain-candidate-label').first()).toHaveText('LONG CANDIDATE — READY');
      await expect(page.getByText('READY_CANONICAL_REASON: all strict gates aligned', { exact: false }).first()).toBeVisible();
      await expect(page.getByTestId('main-brain-vwap')).toHaveText('Price above VWAP — bullish regime');
    },
  },
  {
    name: 'Legacy home',
    path: '/legacy',
    wait: async (page: Page) => {
      await expect(page.getByTestId('legacy-candidate-label')).toHaveText('SHORT CANDIDATE — WAIT');
      await expect(page.getByTestId('legacy-reason')).toContainText('WAIT_CANONICAL_REASON');
      await expect(page.getByTestId('legacy-vwap')).toHaveText('Price below VWAP — bearish regime');
    },
    ready: async (page: Page) => {
      await expect(page.getByTestId('legacy-candidate-label')).toHaveText('LONG CANDIDATE — READY');
      await expect(page.getByTestId('legacy-reason')).toContainText('READY_CANONICAL_REASON');
      await expect(page.getByTestId('legacy-vwap')).toHaveText('Price above VWAP — bullish regime');
    },
  },
  {
    name: 'Mobile',
    path: '/mobile',
    wait: async (page: Page) => {
      await expect(page.getByTestId('mobile-decision')).toContainText('WAIT');
      await expect(page.getByTestId('mobile-candidate-label')).toHaveText('SHORT CANDIDATE — WAIT');
      await expect(page.getByTestId('mobile-vwap')).toHaveText('Price below VWAP — bearish regime');
    },
    ready: async (page: Page) => {
      await expect(page.getByTestId('mobile-decision')).toContainText('LONG READY');
      await expect(page.getByTestId('mobile-candidate-label')).toHaveText('LONG CANDIDATE — READY');
      await expect(page.getByTestId('mobile-vwap')).toHaveText('Price above VWAP — bullish regime');
    },
  },
  {
    name: 'Cockpit',
    path: '/cockpit',
    wait: async (page: Page) => {
      await expect(page.getByTestId('cockpit-decision')).toHaveText('WAIT');
      await expect(page.getByTestId('cockpit-direction')).toHaveText('Short');
      await expect(page.getByTestId('cockpit-reason')).toContainText('WAIT_CANONICAL_REASON');
      await expect(page.getByText('Price below VWAP — bearish regime', { exact: true })).toBeVisible();
    },
    ready: async (page: Page) => {
      await expect(page.getByTestId('cockpit-decision')).toHaveText('READY');
      await expect(page.getByTestId('cockpit-direction')).toHaveText('Long');
      await expect(page.getByTestId('cockpit-reason')).toContainText('READY_CANONICAL_REASON');
      await expect(page.getByText('Price above VWAP — bullish regime', { exact: true })).toBeVisible();
    },
  },
] as const;

for (const consumer of consumers) {
  test(`${consumer.name} keeps WAIT and READY presentation authoritative`, async ({ page }) => {
    const current = { state: 'WAIT' as DecisionState };
    const assertAuthenticated = await installAuthenticatedFixture(page, current);
    await login(page, consumer.path);
    await consumer.wait(page);
    assertAuthenticated();

    current.state = 'READY';
    await page.reload();
    await consumer.ready(page);
    await expect(page.getByText('CONTRADICTORY_LEGACY_REASON', { exact: true })).toHaveCount(0);
  });
}
