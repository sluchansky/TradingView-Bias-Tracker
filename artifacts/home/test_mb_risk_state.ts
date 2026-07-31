/**
 * test_mb_risk_state.ts — Risk State display tests
 *
 * Verifies:
 *  - computeRiskOpsState() classifies all operator risk states correctly
 *  - normalizeMainBrainPayload() populates risk_ops from prop_firm
 *  - no frontend recalculation of any trading threshold
 *  - other Main Brain normalizer fields unchanged
 *
 * Run: cd artifacts/home && npx tsx test_mb_risk_state.ts
 */

import { safeNum, computeRiskOpsState, normalizeMainBrainPayload } from './src/lib/mainBrainNormalizer.js';

// ── Tiny test harness ─────────────────────────────────────────────────────────
let total = 0, failed = 0;
const failures: string[] = [];
let _section = '';

function section(label: string): void {
  _section = label;
  console.log(`\n[${label}]`);
}
function check(name: string, pass: boolean, detail?: string): void {
  total++;
  if (pass) {
    console.log(`  ✓ ${name}`);
  } else {
    failed++;
    const msg = `  ✗ ${name}${detail ? ' — ' + detail : ''}`;
    failures.push(`[${_section}] ${msg}`);
    console.error(msg);
  }
}

// ── Fixtures ──────────────────────────────────────────────────────────────────
// Represents what prop_firm_status_view() returns when protection is ON and healthy.
function makePropFirm(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    enabled: true,
    db_ready: true,
    phase: 1,
    accounts: [{ id: 'acct1', name: 'Test Account', firm: 'MES', active: true }],
    account: { id: 'acct1', name: 'Test Account', firm: 'MES' },
    headline: 'Protection ON — Test Account (MES).',
    metrics: {
      pnl_today: -200.0,
      max_contracts: 3,
      daily_loss_limit: 1500,
      daily_loss_remaining: 1300,
      drawdown_floor: 48500,
      drawdown_remaining: 1500,
      open_contracts: 1,
    },
    phase2: [],
    last_decision: { decision: 'allow', reasons: [], warnings: [] },
    ...overrides,
  };
}

// ── T1: computeRiskOpsState — UNAVAILABLE ────────────────────────────────────
section('T1: UNAVAILABLE — prop_firm absent');

check('empty object → UNAVAILABLE',    computeRiskOpsState({}) === 'UNAVAILABLE');
check('null object → UNAVAILABLE',     computeRiskOpsState(null as unknown as Record<string,unknown>) === 'UNAVAILABLE');

// ── T2: computeRiskOpsState — PROT_OFF ───────────────────────────────────────
section('T2: PROT_OFF — protection explicitly disabled');

check('enabled=false → PROT_OFF',     computeRiskOpsState({ enabled: false }) === 'PROT_OFF');
check('enabled absent → PROT_OFF',    computeRiskOpsState({ enabled: undefined, metrics: {} }) === 'PROT_OFF');

// ── T3: computeRiskOpsState — NO_ACCOUNT ─────────────────────────────────────
section('T3: NO_ACCOUNT — protection ON but no active account');

check('account=null → NO_ACCOUNT',    computeRiskOpsState({ enabled: true, account: null }) === 'NO_ACCOUNT');
check('account absent → NO_ACCOUNT',  computeRiskOpsState({ enabled: true }) === 'NO_ACCOUNT');
check('account=string → NO_ACCOUNT',  computeRiskOpsState({ enabled: true, account: 'bad' }) === 'NO_ACCOUNT');

// ── T4: computeRiskOpsState — DAILY_LIMIT ────────────────────────────────────
section('T4: DAILY_LIMIT — daily loss budget exhausted');

const dailyLimitHit = makePropFirm({ metrics: {
  pnl_today: -1500, daily_loss_limit: 1500, daily_loss_remaining: 0, open_contracts: 0,
}});
check('dlr=0 → DAILY_LIMIT',  computeRiskOpsState(dailyLimitHit) === 'DAILY_LIMIT');

const dailyLimitExceeded = makePropFirm({ metrics: {
  pnl_today: -1600, daily_loss_limit: 1500, daily_loss_remaining: -100, open_contracts: 0,
}});
check('dlr<0 → DAILY_LIMIT',  computeRiskOpsState(dailyLimitExceeded) === 'DAILY_LIMIT');

// ── T5: computeRiskOpsState — DRAWDOWN ───────────────────────────────────────
section('T5: DRAWDOWN — floor breached');

const drawdownHit = makePropFirm({ metrics: {
  pnl_today: -3000, daily_loss_limit: 1500, daily_loss_remaining: 1500,
  drawdown_remaining: 0, open_contracts: 0,
}});
check('ddr=0 → DRAWDOWN',    computeRiskOpsState(drawdownHit) === 'DRAWDOWN');

const drawdownExceeded = makePropFirm({ metrics: {
  pnl_today: -3500, daily_loss_limit: 1500, daily_loss_remaining: 1500,
  drawdown_remaining: -200, open_contracts: 0,
}});
check('ddr<0 → DRAWDOWN',    computeRiskOpsState(drawdownExceeded) === 'DRAWDOWN');

// ── T6: computeRiskOpsState — CAUTION (daily budget) ─────────────────────────
section('T6: CAUTION — daily budget < 20 %');

// 19% remaining of $1500 limit = $285 remaining
const lowBudget = makePropFirm({ metrics: {
  pnl_today: -1215, daily_loss_limit: 1500, daily_loss_remaining: 285,
  drawdown_remaining: 1200, open_contracts: 1,
}});
check('dlr < 20 % of dll → CAUTION', computeRiskOpsState(lowBudget) === 'CAUTION');

// Exactly 20% = boundary — should NOT be CAUTION (< 20%, not ≤ 20%)
const boundaryBudget = makePropFirm({ metrics: {
  pnl_today: -1200, daily_loss_limit: 1500, daily_loss_remaining: 300,
  drawdown_remaining: 1200, open_contracts: 0,
}});
check('dlr = 20 % → NORMAL (not CAUTION)', computeRiskOpsState(boundaryBudget) === 'NORMAL');

// ── T7: computeRiskOpsState — CAUTION (config warnings) ──────────────────────
section('T7: CAUTION — configuration warnings (phase2)');

const configWarning = makePropFirm({
  phase2: ['Intraday trailing drawdown ($2000) — display only; switch to EOD.'],
});
check('phase2 non-empty → CAUTION', computeRiskOpsState(configWarning) === 'CAUTION');

// phase2 empty → NORMAL
const noWarning = makePropFirm({ phase2: [] });
check('phase2 empty → NORMAL',       computeRiskOpsState(noWarning) === 'NORMAL');

// ── T8: computeRiskOpsState — NORMAL ─────────────────────────────────────────
section('T8: NORMAL — all checks clear');

check('healthy fixture → NORMAL',    computeRiskOpsState(makePropFirm()) === 'NORMAL');

// NORMAL when daily_loss_limit missing (protection ON, no limit configured)
const noLimit = makePropFirm({ metrics: { pnl_today: -50, max_contracts: 3, open_contracts: 0 } });
check('no dll configured → NORMAL',  computeRiskOpsState(noLimit) === 'NORMAL');

// ── T9: active trade exposure ─────────────────────────────────────────────────
section('T9: active trade exposure read-through');

const withExposure = makePropFirm({ metrics: {
  pnl_today: -100, daily_loss_limit: 1500, daily_loss_remaining: 1400,
  drawdown_remaining: 1500, open_contracts: 2, max_contracts: 3,
}});
check('NORMAL with 2 open contracts',      computeRiskOpsState(withExposure) === 'NORMAL');

// ── T10: normalizeMainBrainPayload — risk_ops populated from prop_firm ────────
section('T10: normalizeMainBrainPayload → risk_ops');

const rawWithPropFirm = {
  _version: 'v1',
  generated_at: '2026-07-31T10:00:00Z',
  market: {},
  market_state: {},
  left_brain: {},
  verdict: {},
  strategy_scanner: {},
  active_trades: {},
  prop_firm: makePropFirm(),
};
const normalized = normalizeMainBrainPayload(rawWithPropFirm);
const ro = normalized.risk_ops as Record<string, unknown>;

check('risk_ops is present',                   ro != null);
check('risk_ops.state = NORMAL',               ro.state === 'NORMAL');
check('risk_ops.source = prop_guard',          ro.source === 'prop_guard');
check('risk_ops.enabled = true',               ro.enabled === true);
check('risk_ops.daily_pnl = -200',             ro.daily_pnl === -200);
check('risk_ops.daily_loss_limit = 1500',      ro.daily_loss_limit === 1500);
check('risk_ops.remaining_daily_loss = 1300',  ro.remaining_daily_loss === 1300);
check('risk_ops.drawdown_remaining = 1500',    ro.drawdown_remaining === 1500);
check('risk_ops.current_exposure = 1',         ro.current_exposure === 1);
check('risk_ops.max_contracts = 3',            ro.max_contracts === 3);
check('risk_ops.execution_allowed = true',     ro.execution_allowed === true);
check('risk_ops.blocked_reason = null',        ro.blocked_reason === null);
check('risk_ops.account_name present',         ro.account_name === 'Test Account');
check('risk_ops.account_firm present',         ro.account_firm === 'MES');
check('risk_ops.updated_at from generated_at', ro.updated_at === '2026-07-31T10:00:00Z');

// ── T11: risk_ops when prop_firm absent (pre-fix or connection error) ─────────
section('T11: risk_ops when prop_firm absent');

const rawNoPropFirm = {
  _version: 'v1',
  generated_at: '2026-07-31T10:00:00Z',
  market: {}, market_state: {}, left_brain: {}, verdict: {},
  strategy_scanner: {}, active_trades: {},
  // no prop_firm key
};
const normNone = normalizeMainBrainPayload(rawNoPropFirm);
const roNone   = normNone.risk_ops as Record<string, unknown>;
check('risk_ops present even without prop_firm',  roNone != null);
check('state = UNAVAILABLE when no prop_firm',    roNone.state === 'UNAVAILABLE');
check('source = none',                            roNone.source === 'none');
check('enabled = false',                          roNone.enabled === false);
check('execution_allowed truthy (fail-open)',      roNone.execution_allowed === true);

// ── T12: no frontend recalculation — reads backend-computed values only ───────
section('T12: no frontend recalculation');

// The key contract: daily_loss_remaining must come DIRECTLY from the payload,
// not be computed from pnl_today vs daily_loss_limit.
const asymmetric = makePropFirm({ metrics: {
  pnl_today: -100, daily_loss_limit: 1500,
  // Backend says 800 remaining (might include buffer/adjustments we don't recompute)
  daily_loss_remaining: 800,
  drawdown_remaining: 1500, open_contracts: 0,
}});
const normAsym = normalizeMainBrainPayload({ ...rawNoPropFirm, prop_firm: asymmetric });
const roAsym   = normAsym.risk_ops as Record<string, unknown>;
check('remaining_daily_loss = 800 (read from backend, not computed)',
      roAsym.remaining_daily_loss === 800);
// Must NOT equal 1500 - 100 = 1400 (frontend computation of dll - abs(pnl))
check('remaining_daily_loss ≠ dll - abs(pnl)',
      roAsym.remaining_daily_loss !== (1500 - 100));

// ── T13: execution_allowed semantics ─────────────────────────────────────────
section('T13: execution_allowed');

// OFF → fail-open (allowed=true since no rules enforced)
const pfOff = { enabled: false };
const normOff = normalizeMainBrainPayload({ ...rawNoPropFirm, prop_firm: pfOff });
check('PROT OFF → execution_allowed=true (fail-open)',
      (normOff.risk_ops as Record<string,unknown>).execution_allowed === true);

// ON + account → allowed
check('PROT ON + account → execution_allowed=true',
      (normAsym.risk_ops as Record<string,unknown>).execution_allowed === true);

// ON + no account → blocked
const pfNoAcct = { enabled: true, account: null, metrics: {}, phase2: [] };
const normNoAcct = normalizeMainBrainPayload({ ...rawNoPropFirm, prop_firm: pfNoAcct });
check('PROT ON + no account → execution_allowed=false',
      (normNoAcct.risk_ops as Record<string,unknown>).execution_allowed === false);

// ── T14: other normalizer fields unchanged ────────────────────────────────────
section('T14: other normalizer fields unchanged');

const fullNorm = normalizeMainBrainPayload(rawWithPropFirm);
check('market block still present',          fullNorm.market != null);
check('market_state block still present',    fullNorm.market_state != null);
check('verdict block still present',         fullNorm.verdict != null);
check('strategy_scanner block still present', fullNorm.strategy_scanner != null);
check('availability block still present',    fullNorm.availability != null);
check('candidate_preview block present',     fullNorm.candidate_preview != null);
check('risk_ops added without removing raw prop_firm',
      (fullNorm as Record<string,unknown>).prop_firm != null);

// ── T15: stale risk state — updated_at reflects payload timestamp ─────────────
section('T15: stale risk state — timestamp passthrough');

const staleRaw = {
  ...rawWithPropFirm,
  generated_at: '2026-07-30T03:00:00Z',   // old timestamp
  prop_firm: makePropFirm(),
};
const normStale = normalizeMainBrainPayload(staleRaw);
check('updated_at = payload generated_at',
      (normStale.risk_ops as Record<string,unknown>).updated_at === '2026-07-30T03:00:00Z');

// ── T16: blocked_reason filled from last_decision ────────────────────────────
section('T16: blocked_reason from last_decision');

const blockedFirm = makePropFirm({
  last_decision: {
    decision: 'block',
    reasons: ['Daily loss limit $1500 would be exceeded.'],
    warnings: [],
  },
});
const normBlocked = normalizeMainBrainPayload({ ...rawNoPropFirm, prop_firm: blockedFirm });
const roBlocked   = normBlocked.risk_ops as Record<string, unknown>;
// State is NORMAL from metrics alone (last_decision doesn't drive state classifier)
check('state based on metrics, not last_decision',  roBlocked.state === 'NORMAL');
// blocked_reason is surfaced as a diagnostic
check('blocked_reason filled when decision=block',
      roBlocked.blocked_reason === 'Daily loss limit $1500 would be exceeded.');

// allow decision → no blocked reason
const allowFirm = makePropFirm({ last_decision: { decision: 'allow', reasons: [], warnings: [] } });
const normAllow  = normalizeMainBrainPayload({ ...rawNoPropFirm, prop_firm: allowFirm });
check('blocked_reason=null when decision=allow',
      (normAllow.risk_ops as Record<string,unknown>).blocked_reason === null);

// ── Summary ───────────────────────────────────────────────────────────────────
console.log(`\n${'─'.repeat(60)}`);
console.log(`Risk State Tests: ${total - failed} passed, ${failed} failed`);
if (failures.length > 0) {
  console.log('\nFailures:');
  failures.forEach(f => console.error(f));
  process.exit(1);
}
