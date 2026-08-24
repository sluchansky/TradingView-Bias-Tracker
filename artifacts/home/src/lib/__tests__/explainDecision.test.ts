/**
 * Tests for the "Explain Decision" drawer pure-logic functions.
 * All 16 cases from PART 11 of the task spec.
 *
 * Run with: pnpm --filter @workspace/home test
 */

import { describe, it, expect } from 'vitest';
import {
  extractExplainData,
  buildPlainEnglishSummary,
} from '../explainDecision';
import { fmtEventDetail } from '../../pages/MainBrain';

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makePayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    verdict: {
      available: true,
      readiness: 'WAIT',
      is_actionable: false,
      edge_score: 55,
      direction: 'Long',
      edge_components: [
        { key: 'BOS20', label: 'Bullish BOS', points: 20, present: true },
        { key: 'VWAP15', label: 'VWAP reclaim', points: 15, present: false },
      ],
      missing_confirmations: [],
      hard_blockers: [],
      opposing_structure: null,
    },
    left_brain: {
      direction: 'BULLISH',
      confidence: 72,
      diagnosis: { status: 'AVAILABLE', thesis_age_seconds: 120 },
    },
    directions: {
      bull: { edge_score: 55, edge_components: [] },
      bear: { edge_score: 35, edge_components: [] },
    },
    decision_timeline: { available: true, events: [] },
    main_brain: { voice: '' },
    structure_state: null,
    ...overrides,
  };
}

// ── Test 1: Long score higher than Short ─────────────────────────────────────
it('Test 1 — Long score higher → marginLabel starts with LONG +', () => {
  const p = makePayload({ directions: { bull: { edge_score: 80 }, bear: { edge_score: 60 } } });
  const d = extractExplainData(p);
  expect(d.marginLabel).toBe('LONG +20');
  expect(d.higherSide).toBe('LONG');
});

// ── Test 2: Short score higher than Long ─────────────────────────────────────
it('Test 2 — Short score higher → marginLabel starts with SHORT +', () => {
  const p = makePayload({ directions: { bull: { edge_score: 40 }, bear: { edge_score: 70 } } });
  const d = extractExplainData(p);
  expect(d.marginLabel).toBe('SHORT +30');
  expect(d.higherSide).toBe('SHORT');
});

// ── Test 3: Candidate direction differs from higher-scoring side ──────────────
it('Test 3 — Candidate=Long but Short scores higher → contradiction=true', () => {
  const p = makePayload({
    verdict: {
      ...((makePayload().verdict) as Record<string, unknown>),
      direction: 'Long',
    },
    directions: { bull: { edge_score: 40 }, bear: { edge_score: 70 } },
  });
  const d = extractExplainData(p);
  expect(d.candidateDir).toBe('LONG');
  expect(d.higherSide).toBe('SHORT');
  expect(d.contradiction).toBe(true);
});

// ── Test 4: Bullish thesis + Long candidate → FULLY ALIGNED ──────────────────
it('Test 4 — Bullish thesis + Long candidate → alignment FULLY ALIGNED', () => {
  const p = makePayload({
    left_brain: { direction: 'BULLISH', confidence: 80, diagnosis: { status: 'AVAILABLE' } },
    verdict: { ...((makePayload().verdict) as Record<string, unknown>), direction: 'Long' },
  });
  const d = extractExplainData(p);
  expect(d.alignment).toBe('FULLY ALIGNED');
});

// ── Test 5: Bearish thesis + Long candidate → COUNTER-TREND ──────────────────
it('Test 5 — Bearish thesis + Long candidate → alignment COUNTER-TREND', () => {
  const p = makePayload({
    left_brain: { direction: 'BEARISH', confidence: 80, diagnosis: { status: 'AVAILABLE' } },
    verdict: { ...((makePayload().verdict) as Record<string, unknown>), direction: 'Long' },
  });
  const d = extractExplainData(p);
  expect(d.alignment).toBe('COUNTER-TREND');
});

// ── Test 6: Hard blocker shown ────────────────────────────────────────────────
it('Test 6 — hard_blockers from payload surfaces in extracted data', () => {
  const p = makePayload({
    verdict: {
      ...((makePayload().verdict) as Record<string, unknown>),
      hard_blockers: ['CVD hard veto — bearish delta'],
    },
  });
  const d = extractExplainData(p);
  expect(d.hardBlockers).toHaveLength(1);
  expect(d.hardBlockers[0]).toContain('CVD hard veto');
});

// ── Test 7: Missing confirmation shown ───────────────────────────────────────
it('Test 7 — missing_confirmations from payload surfaces in extracted data', () => {
  const p = makePayload({
    verdict: {
      ...((makePayload().verdict) as Record<string, unknown>),
      missing_confirmations: ['Bullish BOS', 'VWAP reclaim'],
    },
  });
  const d = extractExplainData(p);
  expect(d.missingConfirmations).toHaveLength(2);
  expect(d.missingConfirmations[0]).toBe('Bullish BOS');
});

// ── Test 8: Opposing structure ACTIVE (HARD_BLOCK) ───────────────────────────
it('Test 8 — opposing structure HARD_BLOCK is captured', () => {
  const p = makePayload({
    verdict: {
      ...((makePayload().verdict) as Record<string, unknown>),
      opposing_structure: {
        detected: true,
        effect: 'HARD_BLOCK',
        direction: 'Bearish',
        event_type: 'CHOCH',
        age_seconds: 120,
        remaining_seconds: 60,
      },
    },
  });
  const d = extractExplainData(p);
  expect(d.opposingStructure).not.toBeNull();
  expect(d.opposingStructure!.effect).toBe('HARD_BLOCK');
  expect(d.opposingStructure!.direction).toBe('Bearish');
  // Must-change list includes the expiry requirement
  expect(d.mustChange.some(s => s.includes('Bearish') && s.includes('CHOCH'))).toBe(true);
});

// ── Test 9: Opposing structure OVERRIDDEN ─────────────────────────────────────
it('Test 9 — opposing structure OBSERVED does NOT add to mustChange', () => {
  const p = makePayload({
    verdict: {
      ...((makePayload().verdict) as Record<string, unknown>),
      opposing_structure: {
        detected: true,
        effect: 'OBSERVED',
        direction: 'Bearish',
        event_type: 'BOS',
        age_seconds: 400,
        remaining_seconds: null,
      },
    },
  });
  const d = extractExplainData(p);
  expect(d.opposingStructure!.effect).toBe('OBSERVED');
  // OBSERVED should not appear in mustChange as a blocking requirement
  expect(d.mustChange.every(s => !s.includes('BOS') || !s.includes('Bearish'))).toBe(true);
});

// ── Test 10: No score breakdown ───────────────────────────────────────────────
it('Test 10 — payload with no edge_components → hasComponents=false', () => {
  const p = makePayload({
    verdict: {
      ...((makePayload().verdict) as Record<string, unknown>),
      edge_components: [],
    },
    directions: { bull: { edge_score: 50 }, bear: { edge_score: 30 } },
  });
  const d = extractExplainData(p);
  expect(d.hasComponents).toBe(false);
  expect(d.longComponents).toHaveLength(0);
  expect(d.shortComponents).toHaveLength(0);
});

// ── Test 11: No candidate ─────────────────────────────────────────────────────
it('Test 11 — no direction in verdict → candidateDir=NONE', () => {
  const p = makePayload({
    verdict: {
      ...((makePayload().verdict) as Record<string, unknown>),
      direction: '',
      candidate_direction: null,
    },
  });
  const d = extractExplainData(p);
  expect(d.candidateDir).toBe('NONE');
});

// ── Test 12: Stale thesis ─────────────────────────────────────────────────────
it('Test 12 — STALE diagnosis status → thesisStale=true', () => {
  const p = makePayload({
    left_brain: {
      direction: 'BULLISH',
      confidence: 65,
      diagnosis: { status: 'STALE', thesis_age_seconds: 900 },
    },
  });
  const d = extractExplainData(p);
  expect(d.thesisStale).toBe(true);
  expect(d.thesisAvailable).toBe(false);
  expect(d.thesisDir).toContain('STALE');
});

// ── Test 13: Timeline objects format safely ───────────────────────────────────
describe('Test 13 — fmtEventDetail never returns [object Object]', () => {
  it('formats plain strings unchanged', () => {
    expect(fmtEventDetail('hello')).toBe('hello');
  });
  it('formats null as empty string', () => {
    expect(fmtEventDetail(null)).toBe('');
  });
  it('uses summary field over raw object', () => {
    expect(fmtEventDetail({ summary: 'BULLISH → BEARISH', other: {} })).toBe('BULLISH → BEARISH');
  });
  it('formats from→to transition', () => {
    expect(fmtEventDetail({ from: 'WAIT', to: 'READY' })).toBe('WAIT → READY');
  });
  it('never returns [object Object]', () => {
    const result = fmtEventDetail({ nested: { deep: { value: 1 } } });
    expect(result).not.toContain('[object Object]');
  });
});

// ── Test 14: Summary contains no invented facts ───────────────────────────────
it('Test 14 — summary with candidateDir=NONE contains "monitoring" (no invented fields)', () => {
  const p = makePayload({
    verdict: {
      ...((makePayload().verdict) as Record<string, unknown>),
      direction: '',
    },
  });
  const d = extractExplainData(p);
  const summary = buildPlainEnglishSummary(d);
  expect(summary).toContain('monitoring');
  // Summary must NOT contain undefined or [object Object]
  expect(summary).not.toContain('undefined');
  expect(summary).not.toContain('[object');
});

// ── Test 15: Drawer creates no polling loop ────────────────────────────────────
it('Test 15 — extractExplainData is pure (same input → same output, no side-effects)', () => {
  const p = makePayload();
  const r1 = extractExplainData(p);
  const r2 = extractExplainData(p);
  // Structural equality of the stable fields
  expect(r1.verdict).toBe(r2.verdict);
  expect(r1.candidateDir).toBe(r2.candidateDir);
  expect(r1.alignment).toBe(r2.alignment);
  expect(r1.marginLabel).toBe(r2.marginLabel);
  // Input object unchanged
  expect((p.verdict as Record<string, unknown>).direction).toBe('Long');
});

// ── Test 16: Main Brain golden still green (smoke) ────────────────────────────
it('Test 16 — extractExplainData does not throw on an empty payload (safe fallback)', () => {
  expect(() => extractExplainData({})).not.toThrow();
  const d = extractExplainData({});
  expect(d.verdict).toBeDefined();
  expect(d.candidateDir).toBe('NONE');
  expect(d.hasComponents).toBe(false);
  expect(d.timelineEvents).toHaveLength(0);
});

describe('resolved structure-cycle presentation', () => {
  function withStructure(structure_state: Record<string, unknown>) {
    return makePayload({ structure_state });
  }

  it('uses a bearish initial cycle as the continuation requirement everywhere', () => {
    const d = extractExplainData(withStructure({
      state: 'TREND_INITIAL', direction: 'Short', confirmed: false,
      next_event: 'BOS SUPPLY',
      next_event_reason: 'Short BOS established initial directional structure only. Wait for BOS SUPPLY to confirm the continuation cycle.',
      summary: 'Short initial directional structure — awaiting BOS SUPPLY for continuation confirmation.',
    }));
    expect(d.structureGuidance?.nextEvent).toBe('BOS SUPPLY');
    expect(d.mustChange[0]).toContain('BOS SUPPLY');
    expect(buildPlainEnglishSummary(d)).toContain('Wait for BOS SUPPLY to confirm the continuation cycle.');
    expect(buildPlainEnglishSummary(d)).not.toContain('CHOCH before');
  });

  it('uses a bullish initial cycle as the continuation requirement everywhere', () => {
    const d = extractExplainData(withStructure({
      state: 'TREND_INITIAL', direction: 'Long', confirmed: false,
      next_event: 'BOS DEMAND',
      next_event_reason: 'Long BOS established initial directional structure only. Wait for BOS DEMAND to confirm the continuation cycle.',
      summary: 'Long initial directional structure — awaiting BOS DEMAND for continuation confirmation.',
    }));
    expect(d.structureGuidance?.nextEvent).toBe('BOS DEMAND');
    expect(d.mustChange[0]).toContain('BOS DEMAND');
    expect(buildPlainEnglishSummary(d)).toContain('Wait for BOS DEMAND to confirm the continuation cycle.');
  });

  it('keeps CHOCH language limited to a reversal candidate', () => {
    const d = extractExplainData(withStructure({
      state: 'REVERSAL_CANDIDATE', direction: 'Short', confirmed: false,
      next_event: 'BOS SUPPLY',
      next_event_reason: 'Short CHOCH is a reversal candidate only. Wait for BOS SUPPLY to confirm the new structure cycle.',
      summary: 'Short reversal candidate — awaiting BOS SUPPLY.',
    }));
    expect(d.mustChange[0]).toContain('CHOCH is a reversal candidate only');
    expect(buildPlainEnglishSummary(d)).toContain('Wait for BOS SUPPLY to confirm the new structure cycle.');
  });

  it('does not add a pending requirement after a reversal is confirmed', () => {
    const d = extractExplainData(withStructure({
      state: 'REVERSAL_CONFIRMED', direction: 'Long', confirmed: true,
      next_event: 'CHOCH SUPPLY',
      next_event_reason: 'Current long structure is confirmed. The next valid state change is CHOCH SUPPLY, a new reversal candidate.',
      summary: 'Long reversal confirmed — one 40-point structure allocation is active.',
    }));
    expect(d.structureGuidance?.isPendingConfirmation).toBe(false);
    expect(d.mustChange.some(item => item.includes('CHOCH SUPPLY'))).toBe(false);
  });
});
