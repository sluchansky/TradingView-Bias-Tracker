/**
 * test_phase7c3_normalizer.ts — Phase 7C.3 Normalizer Contract Tests
 *
 * Tests normalizeMainBrainPayload() and its helpers against the documented
 * Phase 7C.1/7C.2 contract.
 *
 * Run:
 *   cd artifacts/home && npx tsx test_phase7c3_normalizer.ts
 *
 * Zero production changes.  The test imports the real normalizer from
 * src/lib/mainBrainNormalizer.ts (pure TypeScript, no React, no DOM).
 */

import {
  safeStr,
  safeNum,
  extractAvail,
  mapStrategyResult,
  normalizeMainBrainPayload,
} from './src/lib/mainBrainNormalizer.js';
import { selectStructureCycleDisplay } from './src/lib/structureGuidance.js';

// ── Test harness ─────────────────────────────────────────────────────────────
let pass = 0;
let fail = 0;
let section = '';

function startSection(name: string) {
  section = name;
  console.log(`\n[${name}]`);
}

function check(desc: string, ok: boolean) {
  if (ok) {
    pass++;
  } else {
    fail++;
    console.error(`  ✗ FAIL [${section}]: ${desc}`);
  }
}

// ── Fixtures ─────────────────────────────────────────────────────────────────

/** Representative live-schema fixture (sanitised; no production values). */
const FULL_FIXTURE: Record<string, unknown> = {
  _version: 'v1',
  generated_at: '2026-07-30T17:00:00+00:00',
  voice: {
    available: true,
    headline: 'Long · edge 45 WAIT',
    narration: 'MGC consolidating — waiting for BOS DEMAND to confirm continuation.',
    reason: null,
  },
  market: {
    session: { open: true, status: 'OPEN', reason: '', next_open_et: '', next_open: null },
    selected_instrument: 'MGC',
    trading_mode: 'SCALP',
    execution_mode: 'traderspost',
  },
  market_state: {
    bias: 'Neutral',
    regime: { regime: 'TRENDING', reason: 'Aligned structure' },
    risk_state: null,
  },
  left_brain: {
    available: true,
    thesis: {
      direction: 'LONG',
      confidence: 0.72,
      narrative: ['Bullish bias on 1H.', 'VWAP reclaim.'],
      generated_at: '2026-07-30T16:50:00+00:00',
      status: 'ACTIVE',
      strength: 'STRONG',
      age_seconds: 600,
    },
  },
  verdict: {
    direction: 'Long',
    readiness: 'WAIT',
    edge_score: 45,
    edge_max: 110,
    grade: 'WAIT',
    is_actionable: false,
    strict_reason: 'Long WAIT — failed: structure_confirmed, edge_score(45<75).',
    failed_conditions: ['structure_confirmed', 'edge_score(45<75)'],
    edge_components: [
      { key: 'bos_confirmed',   label: 'Bullish BOS',          points: 20, present: false },
      { key: 'choch_confirmed', label: 'Bullish CHOCH',         points: 20, present: false },
      { key: 'vwap_confirmed',  label: 'VWAP Reclaim',          points: 15, present: true  },
      { key: 'liquidity_sweep', label: 'Liquidity Sweep',       points: 15, present: false },
      { key: 'volume_confirmed',label: 'Volume Confirmation',   points: 15, present: true  },
      { key: 'cvd_confirmed',   label: 'CVD Confirms Long',     points: 15, present: true  },
      { key: 'preferred_session',label: 'Session Bonus',        points: 10, present: false },
    ],
    score_breakdown: [
      { label: 'VWAP Reclaim', points: 15 },
      { label: 'Volume Confirmation', points: 15 },
      { label: 'CVD Confirms Long', points: 15 },
    ],
    failed_confirmations: ['Bullish BOS', 'Bullish CHOCH', 'Liquidity Sweep', 'Session Bonus'],
    risks: ['Choppy conditions'],
    structure_guidance: {
      state: 'TREND_INITIAL',
      direction: 'Long',
      confirmed: false,
      next_event: 'BOS DEMAND',
      next_event_reason: 'Long BOS established initial directional structure only. Wait for BOS DEMAND to confirm the continuation cycle.',
      summary: 'Long initial directional structure — awaiting BOS DEMAND for continuation confirmation.',
    },
  },
  strategy_scanner: {
    selected: 'VWAP_TREND_CONTINUATION',
    entry: null,
    stop: null,
    targets: [],
    risk_reward: null,
    ranked_strategies: [
      { strategy_key: 'OPENING_DRIVE',            label: 'Opening Drive',           result: 'skipped',   completeness: 75, eligible: false, skip_reason: 'outside_session', direction: 'Long' },
      { strategy_key: 'LIQUIDITY_SWEEP_REVERSAL',  label: 'Liquidity Sweep Reversal',result: 'no_signal', completeness: 0,  eligible: true,  skip_reason: null,              direction: 'Long' },
      { strategy_key: 'VWAP_TREND_CONTINUATION',   label: 'VWAP Trend Continuation', result: 'no_signal', completeness: 40, eligible: true,  skip_reason: null,              direction: 'Long' },
      { strategy_key: 'RANGE_EXPANSION_BREAKOUT',  label: 'Range Expansion Breakout',result: 'no_signal', completeness: 0,  eligible: true,  skip_reason: null,              direction: 'Long' },
      { strategy_key: 'OPENING_RANGE_BREAKOUT',    label: 'Opening Range Breakout',  result: 'no_signal', completeness: 0,  eligible: true,  skip_reason: null,              direction: 'Long' },
    ],
  },
  active_trades: [],
  alerts: [
    { ts: '2026-07-30T16:55:00+00:00', ticker: 'MGC1!', alert_type: 'BOS', verdict: 'LONG_STRUCTURE' },
  ],
  journal: {
    available: true,
    summary: { total_trades: 3, win_rate: 0.667, avg_r: 0.85 },
    recent_trades: [
      { symbol: 'MGC', strategy: 'VWAP_TREND', outcome: 'WIN', r: 1.2 },
    ],
  },
  availability: {
    left_brain:       { available: true },
    strategy_scanner: { available: true },
    coach:            { available: false },
    journal:          { available: true },
    timeline:         { available: true },
    alerts:           { available: true },
    active_trades:    { available: true },
    execution_gateway:{ available: true },
    performance:      { available: false },
    market:           { available: true },
    market_state:     { available: true },
    system_status:    { available: true },
  },
  decision_timeline: {
    events: [
      { ts: '2026-07-30T16:55:00+00:00', label: 'Thesis updated', event_type: 'THESIS_TRANSITION' },
    ],
  },
  system_status: {
    db_ready: true,
    databento_ready: false,
    broker_ready: true,
    learning_ready: true,
  },
  performance: { sample: 47 },
};

/** Degraded fixture — partial data, missing voice, unknown broker state. */
const DEGRADED_FIXTURE: Record<string, unknown> = {
  _version: 'v1',
  market: {
    session: { status: null },
    selected_instrument: null,
  },
  market_state: {
    regime: null,
  },
  left_brain: {
    available: true,
    thesis: null,
  },
  verdict: {
    edge_score: 0,
    grade: null,
    readiness: 'WAIT',
    is_actionable: false,
    // no edge_components, no score_breakdown
  },
  strategy_scanner: {
    selected: null,
    ranked_strategies: [],
  },
  active_trades: [],
  alerts: [],
  journal: { available: false, summary: {}, recent_trades: [] },
  availability: {},
  system_status: {},
  // no voice, no decision_timeline
};

// ── ────────────────────────────────────────────────────────────────────────────
// TASK 2 — NORMALIZER CONTRACT TESTS
// ── ────────────────────────────────────────────────────────────────────────────

startSection('T2: Normalizer contract — market');
{
  const p = normalizeMainBrainPayload(FULL_FIXTURE);
  const mkt = p.market as Record<string, unknown>;
  check('session_status flattened from market.session.status', mkt.session_status === 'OPEN');
  check('instrument renamed from selected_instrument', mkt.instrument === 'MGC');
  check('original market fields preserved (trading_mode)', mkt.trading_mode === 'SCALP');
}

startSection('T2: Normalizer contract — market_state');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const ms = p.market_state as Record<string, unknown>;
  check('regime extracted from {regime, reason} object', ms.regime === 'TRENDING');
  check('other market_state fields preserved (bias)', ms.bias === 'Neutral');

  // Regime as plain string passes through unchanged
  const p2  = normalizeMainBrainPayload({ ...FULL_FIXTURE, market_state: { regime: 'MEAN_REVERTING' } });
  const ms2 = p2.market_state as Record<string, unknown>;
  check('plain-string regime passes through unmodified', ms2.regime === 'MEAN_REVERTING');
}

startSection('T2: Normalizer contract — left_brain thesis flattening');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const lb = p.left_brain as Record<string, unknown>;
  check('direction flattened from thesis.direction',    lb.direction  === 'LONG');
  check('confidence flattened from thesis.confidence',  lb.confidence === 0.72);
  check('strength mapped to momentum label',            lb.momentum   === 'STRONG');
  check('age_seconds flattened',                        lb.age_seconds === 600);
  check('generated_at flattened',                       lb.generated_at === '2026-07-30T16:50:00+00:00');
  check('original lb.available preserved',              lb.available  === true);
}

startSection('T2: Normalizer contract — left_brain missing/null thesis');
{
  const noLb = normalizeMainBrainPayload({ ...FULL_FIXTURE, left_brain: undefined });
  const lb1  = noLb.left_brain as Record<string, unknown>;
  check('missing left_brain does not crash', lb1 != null);
  check('missing left_brain direction is null', lb1.direction === null);

  const nullThesis = normalizeMainBrainPayload({ ...FULL_FIXTURE, left_brain: { available: true, thesis: null } });
  const lb2 = nullThesis.left_brain as Record<string, unknown>;
  check('null thesis does not crash', lb2 != null);
  check('null thesis direction is null', lb2.direction === null);
  check('null thesis momentum is null', lb2.momentum === null);
}

startSection('T2: Normalizer contract — strategy scanner');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const sc = p.strategy_scanner as Record<string, unknown>;
  const strats = sc.strategies as Record<string, unknown>[];

  check('selected renamed to selected_strategy', sc.selected_strategy === 'VWAP_TREND_CONTINUATION');
  check('ranked_strategies mapped to strategies array', Array.isArray(strats));
  check('strategies count matches input', strats.length === 5);

  const vwap = strats.find(s => s.strategy_key === 'VWAP_TREND_CONTINUATION');
  check('strategy_key preserved on item', vwap?.strategy_key === 'VWAP_TREND_CONTINUATION');
  check('key alias added from strategy_key', vwap?.key === 'VWAP_TREND_CONTINUATION');
  check('name alias added from label', vwap?.name === 'VWAP Trend Continuation');
  check('readiness mapped: no_signal → NO SIGNAL', vwap?.readiness === 'NO SIGNAL');

  const drive = strats.find(s => s.strategy_key === 'OPENING_DRIVE');
  check('readiness mapped: skipped → SKIP', drive?.readiness === 'SKIP');
}

startSection('T2: Normalizer contract — trade_plan wrapper');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const sc = p.strategy_scanner as Record<string, unknown>;
  const tp = sc.trade_plan as Record<string, unknown>;
  check('trade_plan wrapper constructed', tp != null && typeof tp === 'object');
  check('trade_plan.entry defaults to null when absent', tp.entry === null);
  check('trade_plan.rr defaults to null when absent', tp.rr === null);

  const withPlan = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    strategy_scanner: {
      ...(FULL_FIXTURE.strategy_scanner as Record<string, unknown>),
      entry: 2485.5, stop: 2480.0, targets: [2492.0, 2500.0], risk_reward: 2.5,
    },
  });
  const tp2 = (withPlan.strategy_scanner as Record<string, unknown>).trade_plan as Record<string, unknown>;
  check('trade_plan.entry populated when present', tp2.entry === 2485.5);
  check('trade_plan.target_1 from targets[0]',     tp2.target_1 === 2492.0);
  check('trade_plan.target_2 from targets[1]',     tp2.target_2 === 2500.0);
  check('trade_plan.rr from risk_reward',          tp2.rr === 2.5);
}

startSection('T2: Normalizer contract — active_trades wrapping');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const at = p.active_trades as Record<string, unknown>;
  check('bare [] wrapped in {available, trades}', at.available === true && Array.isArray(at.trades));
  check('empty array produces empty trades list', (at.trades as unknown[]).length === 0);

  const withTrade = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    active_trades: [{ instrument: 'MGC', contracts: 2, direction: 'Long' }],
  });
  const at2   = withTrade.active_trades as Record<string, unknown>;
  const trades = at2.trades as Record<string, unknown>[];
  check('single trade wrapped correctly', trades.length === 1);
  check('contracts aliased to quantity', trades[0].quantity === 2);
}

startSection('T2: Normalizer contract — alerts wrapping');
{
  const p   = normalizeMainBrainPayload(FULL_FIXTURE);
  const al  = p.alerts as Record<string, unknown>;
  const items = al.items as Record<string, unknown>[];
  check('bare [] wrapped in {available, items}',  al.available === true && Array.isArray(items));
  check('one alert mapped',                        items.length === 1);
  check('ts → timestamp alias',                    items[0].timestamp === '2026-07-30T16:55:00+00:00');
  check('ticker → instrument, stripping 1!',       items[0].instrument === 'MGC');
  check('alert_type → message',                    items[0].message    === 'BOS');
}

startSection('T2: Normalizer contract — journal flattening');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const jn = p.journal as Record<string, unknown>;
  check('available preserved',             jn.available     === true);
  check('today_count from summary.total_trades', jn.today_count === 3);
  check('today_win_rate scaled ×100 from 0-1',   jn.today_win_rate === 66.7);
  check('today_avg_r from summary.avg_r',         jn.today_avg_r   === 0.85);
  check('recent_closed from recent_trades',       Array.isArray(jn.recent_closed));

  const closed = (jn.recent_closed as Record<string, unknown>[]);
  check('symbol → instrument alias in trade',    closed[0].instrument === 'MGC');
  check('strategy → setup alias in trade',       closed[0].setup      === 'VWAP_TREND');
}

startSection('T2: Normalizer contract — availability objects → booleans');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const av = p.availability as Record<string, unknown>;
  check('left_brain {available:true} → true',    av.left_brain        === true);
  check('coach {available:false} → false',        av.coach             === false);
  check('timeline → decision_timeline key',       av.decision_timeline === true);
  check('performance {available:false} → false',  av.performance       === false);
  check('absent availability key defaults true (fail-open)',
    normalizeMainBrainPayload({ ...FULL_FIXTURE, availability: {} })
      .availability != null &&
    ((normalizeMainBrainPayload({ ...FULL_FIXTURE, availability: {} }).availability as Record<string, unknown>).left_brain === true)
  );
}

startSection('T2: Normalizer contract — voice extraction');
{
  // Dict with narration
  const p1  = normalizeMainBrainPayload(FULL_FIXTURE);
  const mb1 = p1.main_brain as Record<string, unknown>;
  check('voice dict → narration extracted', mb1.voice === 'MGC consolidating — waiting for BOS DEMAND to confirm continuation.');
  const structure = mb1.structure_guidance as Record<string, unknown>;
  check('structure guidance passes through without recomputation', structure.next_event === 'BOS DEMAND');
  const initialCycle = selectStructureCycleDisplay(normalizeMainBrainPayload({
    verdict: {
      structure_state: {
        state: 'TREND_INITIAL', allocation_points: 20, active_event: 'BOS DEMAND',
      },
      structure_guidance: {
        state: 'TREND_INITIAL', next_event: 'BOS DEMAND',
        next_event_reason: 'Wait for BOS DEMAND to confirm the continuation cycle.',
      },
    },
  }));
  check('initial cycle retains +20 credit and active event after normalization',
    initialCycle.allocation_points === 20 && initialCycle.active_event === 'BOS DEMAND');
  const confirmedCycle = selectStructureCycleDisplay(normalizeMainBrainPayload({
    verdict: {
      structure_state: {
        state: 'REVERSAL_CONFIRMED', allocation_points: 40, last_event: 'BOS DEMAND',
      },
      structure_guidance: {
        state: 'REVERSAL_CONFIRMED', next_event: 'CHOCH SUPPLY',
        next_event_reason: 'Current long structure is confirmed.',
      },
    },
  }));
  check('confirmed cycle retains +40 credit and last event after normalization',
    confirmedCycle.allocation_points === 40 && confirmedCycle.last_event === 'BOS DEMAND');

  // Dict with only headline
  const p2 = normalizeMainBrainPayload({ ...FULL_FIXTURE, voice: { headline: 'Long WAIT', narration: '' } });
  const mb2 = p2.main_brain as Record<string, unknown>;
  check('voice dict fallback to headline when narration empty', mb2.voice === 'Long WAIT');

  // Plain string
  const p3 = normalizeMainBrainPayload({ ...FULL_FIXTURE, voice: 'Direct string voice' });
  const mb3 = p3.main_brain as Record<string, unknown>;
  check('plain-string voice passes through', mb3.voice === 'Direct string voice');

  // Missing voice
  const p4 = normalizeMainBrainPayload({ ...FULL_FIXTURE, voice: undefined });
  const mb4 = p4.main_brain as Record<string, unknown>;
  check('missing voice produces null (not crash or empty string)', mb4.voice === null);
}

startSection('T2: Normalizer contract — backend aliases (gateway, db, broker, databento)');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const ss = p.system_status as Record<string, unknown>;
  check('db_ready passed through',         ss.db_ready         === true);
  check('databento_ready passed through',  ss.databento_ready  === false);
  check('broker_ready passed through',     ss.broker_ready     === true);
  check('false remains false (databento)', ss.databento_ready  !== true);
  check('false does not become null',      ss.databento_ready  !== null);
}

startSection('T2: Normalizer contract — mutation guard');
{
  const original = JSON.parse(JSON.stringify(FULL_FIXTURE));
  normalizeMainBrainPayload(FULL_FIXTURE);
  check('raw.market not mutated',          JSON.stringify((FULL_FIXTURE.market as Record<string, unknown>).session_status) === JSON.stringify((original.market as Record<string, unknown>).session_status));
  check('raw.verdict.edge_grade absent before norm', (FULL_FIXTURE.verdict as Record<string, unknown>).edge_grade === undefined);
  check('raw.strategy_scanner.strategies absent before norm',
    (FULL_FIXTURE.strategy_scanner as Record<string, unknown>).strategies === undefined);
  check('raw.active_trades still bare array',  Array.isArray(FULL_FIXTURE.active_trades));
}

startSection('T2: Normalizer contract — unknown fields passthrough');
{
  const withExtra = normalizeMainBrainPayload({ ...FULL_FIXTURE, __custom_field: 'hello', _version: 'v2' });
  check('unknown extra field passes through', (withExtra as Record<string, unknown>).__custom_field === 'hello');
  check('_version passed through',             withExtra._version === 'v2');
}

// ── ────────────────────────────────────────────────────────────────────────────
// TASK 3 — VERDICT TRANSPARENCY TESTS
// ── ────────────────────────────────────────────────────────────────────────────

startSection('T3: Verdict — edge_grade alias');
{
  const p = normalizeMainBrainPayload(FULL_FIXTURE);
  const v = p.verdict as Record<string, unknown>;
  check('edge_grade added as alias for grade', v.edge_grade === 'WAIT');
  check('original grade still present',        v.grade      === 'WAIT');
}

startSection('T3: Verdict — edge_components passthrough');
{
  const p    = normalizeMainBrainPayload(FULL_FIXTURE);
  const v    = p.verdict as Record<string, unknown>;
  const comps = v.edge_components as Record<string, unknown>[];
  check('edge_components is an array',         Array.isArray(comps));
  check('all 7 components preserved',          comps.length === 7);
  check('bos_confirmed component has key',     comps[0].key   === 'bos_confirmed');
  check('bos_confirmed component has label',   comps[0].label === 'Bullish BOS');
  check('bos_confirmed component has points',  comps[0].points === 20);
  check('bos_confirmed component present=false', comps[0].present === false);
  check('vwap_confirmed component present=true', comps[2].present === true);

  // Count satisfied vs unsatisfied from fixture
  const satisfied = comps.filter(c => c.present === true).length;
  const unsatisfied = comps.filter(c => c.present === false).length;
  check('3 satisfied components (VWAP, Volume, CVD)', satisfied === 3);
  check('4 unsatisfied components',                   unsatisfied === 4);
}

startSection('T3: Verdict — score_breakdown passthrough');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const v  = p.verdict as Record<string, unknown>;
  const sb = v.score_breakdown as Record<string, unknown>[];
  check('score_breakdown is array',              Array.isArray(sb));
  check('3 score_breakdown items from fixture',  sb.length === 3);
  check('score_breakdown[0].label present',      sb[0].label === 'VWAP Reclaim');
  check('score_breakdown[0].points present',     sb[0].points === 15);
}

startSection('T3: Verdict — failed_confirmations passthrough');
{
  const p   = normalizeMainBrainPayload(FULL_FIXTURE);
  const v   = p.verdict as Record<string, unknown>;
  const fc  = v.failed_confirmations as string[];
  check('failed_confirmations is array',            Array.isArray(fc));
  check('4 failed_confirmations from fixture',      fc.length === 4);
  check('first failed_confirmation is Bullish BOS', fc[0] === 'Bullish BOS');
}

startSection('T3: Verdict — risks passthrough');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const v  = p.verdict as Record<string, unknown>;
  const r  = v.risks as string[];
  check('risks is array', Array.isArray(r));
  check('risk item preserved', r[0] === 'Choppy conditions');
}

startSection('T3: Verdict — missing edge_breakdown fields degrade safely');
{
  const stripped = {
    ...FULL_FIXTURE,
    verdict: { readiness: 'WAIT', edge_score: 35, grade: 'WAIT', is_actionable: false },
  };
  const p   = normalizeMainBrainPayload(stripped);
  const v   = p.verdict as Record<string, unknown>;
  check('missing edge_components → empty array not crash', Array.isArray(v.edge_components) && (v.edge_components as unknown[]).length === 0);
  check('missing score_breakdown → empty array',           Array.isArray(v.score_breakdown) && (v.score_breakdown as unknown[]).length === 0);
  check('missing failed_confirmations → empty array',      Array.isArray(v.failed_confirmations) && (v.failed_confirmations as unknown[]).length === 0);
  check('missing risks → empty array',                     Array.isArray(v.risks) && (v.risks as unknown[]).length === 0);
  check('edge_score preserved',                            v.edge_score === 35);
}

startSection('T3: Verdict — malformed individual components do not crash');
{
  const withBadComps = {
    ...FULL_FIXTURE,
    verdict: {
      ...(FULL_FIXTURE.verdict as Record<string, unknown>),
      edge_components: [null, undefined, 42, 'bad', { key: 'ok', present: true, points: 10 }],
    },
  };
  let threw = false;
  try { normalizeMainBrainPayload(withBadComps); } catch { threw = true; }
  check('malformed edge_components do not throw during normalization', !threw);
}

startSection('T3: Verdict — brain reasoning / voice');
{
  const p   = normalizeMainBrainPayload(FULL_FIXTURE);
  const mb  = p.main_brain as Record<string, unknown>;
  check('voice narration is a string', typeof mb.voice === 'string');
  check('voice string is non-empty',   (mb.voice as string).length > 0);

  // Empty narration + empty headline → null
  const p2  = normalizeMainBrainPayload({ ...FULL_FIXTURE, voice: { narration: '', headline: '' } });
  const mb2 = p2.main_brain as Record<string, unknown>;
  check('empty narration + empty headline → null (not empty string)', mb2.voice === null);

  // Missing voice → null
  const { voice: _v, ...noVoice } = FULL_FIXTURE as Record<string, unknown>;
  const p3  = normalizeMainBrainPayload(noVoice);
  const mb3 = p3.main_brain as Record<string, unknown>;
  check('missing voice → null, not crash', mb3.voice === null);
}

// ── ────────────────────────────────────────────────────────────────────────────
// TASK 4 — STRATEGY SCANNER TESTS
// ── ────────────────────────────────────────────────────────────────────────────

startSection('T4: Scanner — readiness mapping');
{
  check('mapStrategyResult ready → READY',       mapStrategyResult('ready')     === 'READY');
  check('mapStrategyResult skipped → SKIP',      mapStrategyResult('skipped')   === 'SKIP');
  check('mapStrategyResult no_signal → NO SIGNAL', mapStrategyResult('no_signal') === 'NO SIGNAL');
  check('mapStrategyResult empty → —',           mapStrategyResult('')          === '—');
  check('mapStrategyResult unknown uppercased',  mapStrategyResult('forming')   === 'FORMING');
}

startSection('T4: Scanner — normalization of individual strategies');
{
  const p       = normalizeMainBrainPayload(FULL_FIXTURE);
  const sc      = p.strategy_scanner as Record<string, unknown>;
  const strats  = sc.strategies as Record<string, unknown>[];

  // OPENING_DRIVE: ineligible with skip_reason
  const od = strats.find(s => s.strategy_key === 'OPENING_DRIVE')!;
  check('eligible=false preserved',            od.eligible === false);
  check('skip_reason preserved',               od.skip_reason === 'outside_session');
  check('mode_compatible null for non-mode skip', od.mode_compatible === null);

  // mode-related skip → mode_compatible=false
  const modeSkip = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    strategy_scanner: {
      ...(FULL_FIXTURE.strategy_scanner as Record<string, unknown>),
      ranked_strategies: [{
        strategy_key: 'TEST_KEY', label: 'Test', result: 'skipped',
        completeness: 0, eligible: false, skip_reason: 'mode_mismatch',
      }],
    },
  });
  const mStrats = (modeSkip.strategy_scanner as Record<string, unknown>).strategies as Record<string, unknown>[];
  check('mode-related skip → mode_compatible=false', mStrats[0].mode_compatible === false);
}

startSection('T4: Scanner — completeness edge cases');
{
  function makeScanner(completeness: number | null, eligible = true) {
    return normalizeMainBrainPayload({
      ...FULL_FIXTURE,
      strategy_scanner: {
        ...(FULL_FIXTURE.strategy_scanner as Record<string, unknown>),
        ranked_strategies: [{
          strategy_key: 'T', label: 'T', result: 'no_signal',
          completeness, eligible,
        }],
      },
    });
  }

  const s0 = (makeScanner(0).strategy_scanner  as Record<string, unknown>).strategies as Record<string, unknown>[];
  const s100 = (makeScanner(100).strategy_scanner as Record<string, unknown>).strategies as Record<string, unknown>[];
  const sNull = (makeScanner(null).strategy_scanner as Record<string, unknown>).strategies as Record<string, unknown>[];
  const sNeg  = (makeScanner(-5).strategy_scanner  as Record<string, unknown>).strategies as Record<string, unknown>[];

  check('completeness=0 preserved in strategy item',    s0[0].completeness   === 0);
  check('completeness=100 preserved in strategy item',  s100[0].completeness === 100);
  check('completeness=null preserved (not coerced)',     sNull[0].completeness === null);
  check('completeness=-5 preserved (raw value kept)',    sNeg[0].completeness === -5);
  check('raw trading values not mutated (eligible flag)', s0[0].eligible === true);
}

startSection('T4: Scanner — empty and missing states');
{
  const emptyList = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    strategy_scanner: { selected: null, ranked_strategies: [] },
  });
  const sc1 = emptyList.strategy_scanner as Record<string, unknown>;
  check('empty ranked_strategies → empty strategies array', (sc1.strategies as unknown[]).length === 0);
  check('missing selected → selected_strategy is null',     sc1.selected_strategy === null);

  const noScanner = normalizeMainBrainPayload({ ...FULL_FIXTURE, strategy_scanner: undefined });
  const sc2 = noScanner.strategy_scanner as Record<string, unknown>;
  check('missing strategy_scanner does not crash', sc2 != null);
  check('missing scanner strategies is empty array', Array.isArray(sc2.strategies) && (sc2.strategies as unknown[]).length === 0);
}

startSection('T4: Scanner — missing strategy name is safe');
{
  const noName = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    strategy_scanner: {
      ...(FULL_FIXTURE.strategy_scanner as Record<string, unknown>),
      ranked_strategies: [{ strategy_key: 'ANON', label: undefined, result: 'no_signal', completeness: 30, eligible: true }],
    },
  });
  const strats = (noName.strategy_scanner as Record<string, unknown>).strategies as Record<string, unknown>[];
  check('undefined label → name is undefined (not crash)', strats[0] != null);
  check('key still set from strategy_key', strats[0].key === 'ANON');
}

// ── ────────────────────────────────────────────────────────────────────────────
// TASK 5 — TRUTHFULNESS AND NON-FABRICATION TESTS
// ── ────────────────────────────────────────────────────────────────────────────

startSection('T5: Truthfulness — no edge components invented');
{
  const noComps = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    verdict: { readiness: 'WAIT', edge_score: 0, grade: 'WAIT', is_actionable: false },
  });
  const v    = noComps.verdict as Record<string, unknown>;
  const comps = v.edge_components as unknown[];
  check('absent edge_components → empty array, not invented list', comps.length === 0);
}

startSection('T5: Truthfulness — missing readiness never defaults to READY');
{
  const noReady = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    verdict: { edge_score: 45 },
  });
  const v = noReady.verdict as Record<string, unknown>;
  check('missing readiness is not READY', v.readiness !== 'READY');
  check('is_actionable is not true when absent', v.is_actionable !== true);
}

startSection('T5: Truthfulness — missing broker state not coerced to ready');
{
  const noBroker = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    system_status: { db_ready: true },  // broker_ready absent
  });
  const ss = noBroker.system_status as Record<string, unknown>;
  check('absent broker_ready is not true',  ss.broker_ready !== true);
  check('absent broker_ready is undefined', ss.broker_ready === undefined);
}

startSection('T5: Truthfulness — false Databento not coerced to live');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const ss = p.system_status as Record<string, unknown>;
  check('databento_ready=false stays false', ss.databento_ready === false);
  check('databento_ready false is not null', ss.databento_ready !== null);
}

startSection('T5: Truthfulness — missing active_trades no phantom trade');
{
  const noTrades = normalizeMainBrainPayload({ ...FULL_FIXTURE, active_trades: [] });
  const at = noTrades.active_trades as Record<string, unknown>;
  check('empty active_trades → trades array is empty', (at.trades as unknown[]).length === 0);

  const missingAt = normalizeMainBrainPayload({ ...FULL_FIXTURE, active_trades: undefined });
  const at2 = missingAt.active_trades as Record<string, unknown>;
  check('missing active_trades → trades array is empty', (at2.trades as unknown[]).length === 0);
}

startSection('T5: Truthfulness — empty journal no sample records');
{
  const noJournal = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    journal: { available: false, summary: {}, recent_trades: [] },
  });
  const jn = noJournal.journal as Record<string, unknown>;
  check('empty journal recent_closed is empty array', (jn.recent_closed as unknown[]).length === 0);
  check('no today_count invented when summary empty',  jn.today_count === null || jn.today_count === undefined);
  check('no today_win_rate invented',                  jn.today_win_rate === null);
  check('available=false preserved truthfully',        jn.available === false);
}

startSection('T5: Truthfulness — ineligible strategy not Best Opportunity');
{
  // This verifies the normalizer preserves eligible=false so the panel can filter correctly
  const p      = normalizeMainBrainPayload(FULL_FIXTURE);
  const sc     = p.strategy_scanner as Record<string, unknown>;
  const strats = sc.strategies as Record<string, unknown>[];

  const ineligible = strats.filter(s => s.eligible === false);
  check('ineligible strategies marked eligible=false', ineligible.length > 0);
  check('OPENING_DRIVE remains ineligible after normalization',
    strats.find(s => s.strategy_key === 'OPENING_DRIVE')?.eligible === false);
}

startSection('T5: Truthfulness — no component shown as satisfied without backend confirmation');
{
  const allAbsent = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    verdict: {
      readiness: 'WAIT', edge_score: 0, grade: 'WAIT', is_actionable: false,
      edge_components: [
        { key: 'bos_confirmed', label: 'Bullish BOS', points: 20, present: false },
      ],
    },
  });
  const v     = allAbsent.verdict as Record<string, unknown>;
  const comps = v.edge_components as Record<string, unknown>[];
  check('component with present=false not changed to present=true', comps[0].present === false);
}

// ── ────────────────────────────────────────────────────────────────────────────
// TASK 7 — DISPLAY TOTAL CONSISTENCY
// ── ────────────────────────────────────────────────────────────────────────────

startSection('T7: Display total consistency');
{
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const v  = p.verdict as Record<string, unknown>;

  // The canonical relationship: edge_score is provided by backend.
  // Component points sum to a "raw" total that may differ from edge_score if
  // adjustments (score_breakdown with negative entries) are applied.
  // The UI must use backend-provided edge_score, NOT recompute from components.
  const comps      = v.edge_components as Record<string, unknown>[];
  const presentPts = comps.filter(c => c.present === true).reduce((acc, c) => acc + ((c.points as number) ?? 0), 0);
  const backendScore = v.edge_score as number;

  // Document the relationship: present-component sum may differ from final score
  // due to risk adjustments (score_breakdown negative entries).
  check('backend edge_score is authoritative (45)',               backendScore === 45);
  check('present component points sum = 45 (3×15)',               presentPts === 45);
  check('edge_max is 110',                                        (v.edge_max as number) === 110);
  check('score_breakdown items do not include negative values in this fixture',
    (v.score_breakdown as Record<string, unknown>[]).every(i => (i.points as number) >= 0));

  // In a fixture with a risk adjustment, the sum would differ — verify structure is preserved
  const withAdj = normalizeMainBrainPayload({
    ...FULL_FIXTURE,
    verdict: {
      ...(FULL_FIXTURE.verdict as Record<string, unknown>),
      edge_score: 40,  // -5 for location mismatch
      score_breakdown: [
        { label: 'VWAP Reclaim', points: 15 },
        { label: 'Location mismatch', points: -5 },
      ],
    },
  });
  const v2  = withAdj.verdict as Record<string, unknown>;
  const sb2 = v2.score_breakdown as Record<string, unknown>[];
  check('adjusted edge_score preserved without recomputation', v2.edge_score === 40);
  check('negative adjustment in score_breakdown preserved',    sb2.some(i => (i.points as number) < 0));
}

// ── ────────────────────────────────────────────────────────────────────────────
// TASK 8 — ACCESSIBILITY AND OPERATOR READABILITY
// ── ────────────────────────────────────────────────────────────────────────────

startSection('T8: Accessibility — readiness states have text representation');
{
  // Verify mapStrategyResult produces readable strings, not empty/boolean
  check('READY is text', typeof mapStrategyResult('ready') === 'string' && mapStrategyResult('ready').length > 0);
  check('WAIT is text (mapStrategyResult no_signal)', typeof mapStrategyResult('no_signal') === 'string');
  check('SKIP is text', mapStrategyResult('skipped') === 'SKIP');

  // Verify readiness in normalized output is a string, not boolean/null
  const p  = normalizeMainBrainPayload(FULL_FIXTURE);
  const sc = p.strategy_scanner as Record<string, unknown>;
  const strats = sc.strategies as Record<string, unknown>[];
  check('all strategy readiness values are strings', strats.every(s => typeof s.readiness === 'string'));
  check('no strategy readiness is empty string', strats.every(s => (s.readiness as string).length > 0));
}

startSection('T8: Accessibility — satisfied vs failed have symbol markers (contract check)');
{
  // The panel uses ✓/✗ symbols + present boolean — verify the data contract
  const p     = normalizeMainBrainPayload(FULL_FIXTURE);
  const comps = (p.verdict as Record<string, unknown>).edge_components as Record<string, unknown>[];
  check('each component has a present boolean (for ✓/✗ symbols)', comps.every(c => typeof c.present === 'boolean'));
  check('each component has a label (for readable text)',          comps.every(c => typeof c.label === 'string'));
  check('each component has points (for numeric display)',         comps.every(c => typeof c.points === 'number'));
}

startSection('T8: Accessibility — numeric percentage available for progress bars');
{
  const p      = normalizeMainBrainPayload(FULL_FIXTURE);
  const strats = (p.strategy_scanner as Record<string, unknown>).strategies as Record<string, unknown>[];
  // completeness must be numeric so the panel can render `{comp}%`
  const hasNumericComp = strats.filter(s => s.eligible !== false).every(s =>
    s.completeness == null || typeof s.completeness === 'number'
  );
  check('eligible strategy completeness values are numeric or null', hasNumericComp);
}

startSection('T8: Accessibility — missing data has readable fallback text');
{
  // extractAvail: absent → fail-open (true)
  check('absent availability → true (fail-open)',  extractAvail(undefined) === true);
  check('absent availability → true (null)',        extractAvail(null)      === true);
  // safeStr: null/undefined → '—'
  check('safeStr null → —',      safeStr(null)      === '—');
  check('safeStr undefined → —', safeStr(undefined) === '—');
  check('safeStr empty → —',     safeStr('')        === '—');
  // safeNum: non-numeric → null
  check('safeNum undefined → null', safeNum(undefined) === null);
  check('safeNum NaN → null',       safeNum(NaN)        === null);
  check('safeNum Infinity → null',  safeNum(Infinity)   === null);
}

startSection('T8: Accessibility — panel section headings contract');
{
  // Verify the data fields panels depend on for their titles exist in normalized payload
  const p = normalizeMainBrainPayload(FULL_FIXTURE);
  check('verdict.readiness available for Verdict panel badge',   (p.verdict as Record<string, unknown>).readiness != null);
  check('strategy_scanner.selected_strategy for Scanner badge', (p.strategy_scanner as Record<string, unknown>).selected_strategy === 'VWAP_TREND_CONTINUATION');
  check('main_brain.voice for Reasoning section',               (p.main_brain as Record<string, unknown>).voice != null);
}

// ── ────────────────────────────────────────────────────────────────────────────
// TASK 9 — INTEGRATION FIXTURES
// ── ────────────────────────────────────────────────────────────────────────────

startSection('T9: Full fixture — all major sections populate simultaneously');
{
  const p = normalizeMainBrainPayload(FULL_FIXTURE);

  // Market section
  check('[full] market.session_status populated', (p.market as Record<string, unknown>).session_status === 'OPEN');
  check('[full] market.instrument populated',     (p.market as Record<string, unknown>).instrument === 'MGC');

  // Left Brain
  check('[full] lb.direction populated',   (p.left_brain as Record<string, unknown>).direction === 'LONG');
  check('[full] lb.confidence populated',  (p.left_brain as Record<string, unknown>).confidence === 0.72);

  // Verdict
  const v = p.verdict as Record<string, unknown>;
  check('[full] verdict.edge_score present',          v.edge_score === 45);
  check('[full] verdict.edge_components 7 items',     (v.edge_components as unknown[]).length === 7);
  check('[full] verdict.score_breakdown 3 items',     (v.score_breakdown as unknown[]).length === 3);
  check('[full] verdict.failed_confirmations present',(v.failed_confirmations as unknown[]).length === 4);

  // Strategy scanner
  const sc = p.strategy_scanner as Record<string, unknown>;
  check('[full] strategy_scanner.selected_strategy present', sc.selected_strategy === 'VWAP_TREND_CONTINUATION');
  check('[full] 5 strategies normalized',                    (sc.strategies as unknown[]).length === 5);

  // Active trades
  check('[full] active_trades.available=true', (p.active_trades as Record<string, unknown>).available === true);

  // Journal
  const jn = p.journal as Record<string, unknown>;
  check('[full] journal.today_count=3',      jn.today_count === 3);
  check('[full] journal.today_win_rate=66.7', jn.today_win_rate === 66.7);

  // Availability
  const av = p.availability as Record<string, unknown>;
  check('[full] availability.coach=false',    av.coach === false);
  check('[full] availability.left_brain=true', av.left_brain === true);

  // Timeline
  const tl = p.decision_timeline as Record<string, unknown>;
  check('[full] timeline.events[0].timestamp mapped', (tl.events as Record<string, unknown>[])[0].timestamp === '2026-07-30T16:55:00+00:00');

  // Brain voice
  check('[full] main_brain.voice is string', typeof (p.main_brain as Record<string, unknown>).voice === 'string');
}

startSection('T9: Degraded fixture — page still renders safely and truthfully');
{
  let threw = false;
  let p: Record<string, unknown>;
  try {
    p = normalizeMainBrainPayload(DEGRADED_FIXTURE);
  } catch (e) {
    threw = true;
    p = {};
  }
  check('[degraded] normalization does not throw', !threw);

  const mkt = p.market as Record<string, unknown>;
  check('[degraded] market.session_status defaults to UNKNOWN', mkt.session_status === 'UNKNOWN');
  check('[degraded] market.instrument defaults to empty string', mkt.instrument === '');

  const ms = p.market_state as Record<string, unknown>;
  check('[degraded] null regime passes through as null', ms.regime === null);

  const lb = p.left_brain as Record<string, unknown>;
  check('[degraded] null thesis direction is null', lb.direction === null);
  check('[degraded] null thesis momentum is null',  lb.momentum === null);

  const v = p.verdict as Record<string, unknown>;
  check('[degraded] verdict.edge_components is empty array',      Array.isArray(v.edge_components) && (v.edge_components as unknown[]).length === 0);
  check('[degraded] verdict.failed_confirmations is empty array', Array.isArray(v.failed_confirmations));

  const sc = p.strategy_scanner as Record<string, unknown>;
  check('[degraded] empty scanner.strategies', (sc.strategies as unknown[]).length === 0);

  const mb = p.main_brain as Record<string, unknown>;
  check('[degraded] missing voice → null', mb.voice === null);

  const jn = p.journal as Record<string, unknown>;
  check('[degraded] journal.available=false preserved', jn.available === false);
  check('[degraded] empty recent_closed',               (jn.recent_closed as unknown[]).length === 0);

  const av = p.availability as Record<string, unknown>;
  check('[degraded] empty availability object all fail-open (true)', av.left_brain === true);
}

// ── ────────────────────────────────────────────────────────────────────────────
// HELPERS UNIT TESTS
// ── ────────────────────────────────────────────────────────────────────────────

startSection('Helpers — safeStr');
{
  check('safeStr string value',           safeStr('hello')    === 'hello');
  check('safeStr number → string',        safeStr(42)         === '42');
  check('safeStr null → fallback',        safeStr(null)       === '—');
  check('safeStr undefined → fallback',   safeStr(undefined)  === '—');
  check('safeStr empty → fallback',       safeStr('')         === '—');
  check('safeStr "null" → fallback',      safeStr('null')     === '—');
  check('safeStr custom fallback',        safeStr(null, 'N/A') === 'N/A');
  check('safeStr false → "false"',        safeStr(false)      === 'false');
}

startSection('Helpers — safeNum');
{
  check('safeNum integer',    safeNum(42)       === 42);
  check('safeNum float',      safeNum(3.14)     === 3.14);
  check('safeNum string int', safeNum('7')      === 7);
  check('safeNum null → null',      safeNum(null)       === null);
  check('safeNum undefined → null', safeNum(undefined)  === null);
  check('safeNum NaN → null',       safeNum(NaN)        === null);
  check('safeNum Infinity → null',  safeNum(Infinity)   === null);
  check('safeNum "abc" → null',     safeNum('abc')      === null);
  check('safeNum 0 is valid',       safeNum(0)          === 0);
}

startSection('Helpers — extractAvail');
{
  check('extractAvail true',                    extractAvail(true)               === true);
  check('extractAvail false',                   extractAvail(false)              === false);
  check('extractAvail null → fail-open true',   extractAvail(null)               === true);
  check('extractAvail undefined → true',        extractAvail(undefined)          === true);
  check('extractAvail {available:true}',        extractAvail({ available: true }) === true);
  check('extractAvail {available:false}',       extractAvail({ available: false }) === false);
  check('extractAvail {} → available=true (absent=fail-open)', extractAvail({}) === true);
}

// ── ────────────────────────────────────────────────────────────────────────────
// SUMMARY
// ── ────────────────────────────────────────────────────────────────────────────

console.log(`\n${'='.repeat(64)}`);
console.log(`  TOTAL: ${pass + fail} checks — ${pass} passed, ${fail} failed`);
if (fail === 0) {
  console.log(`  PASS  all Phase 7C.3 normalizer contract checks passed`);
} else {
  console.log(`  FAIL  ${fail} check(s) failed — see above for details`);
}
console.log('='.repeat(64));

if (fail > 0) process.exit(1);
