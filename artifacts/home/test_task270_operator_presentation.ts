/**
 * Focused frontend contract checks for Task #270.
 * Run: cd artifacts/home && pnpm exec tsx test_task270_operator_presentation.ts
 */
import { normalizeMainBrainPayload } from './src/lib/mainBrainNormalizer.js';
import { extractExplainData } from './src/lib/explainDecision.js';
import { extractStructureGuidance } from './src/lib/structureGuidance.js';

let passed = 0;
function check(name: string, actual: unknown, expected: unknown) {
  if (actual !== expected) {
    throw new Error(`${name}: expected ${String(expected)}, received ${String(actual)}`);
  }
  passed += 1;
}

const raw: Record<string, unknown> = {
  // Deliberately contradictory legacy values: the frontend must prefer op state.
  verdict: {
    direction: 'Long',
    readiness: 'WAIT',
    is_actionable: false,
    strict_reason: 'Long WAIT — legacy text',
    edge_score: 42,
    structure_guidance: {
      state: 'TREND_INITIAL', direction: 'Long', confirmed: false,
      next_event: 'BOS DEMAND', next_event_reason: 'legacy long guidance',
    },
  },
  voice: { narration: 'Short WAIT — structure confirmation required.' },
  operator_presentation: {
    verdict: 'WAIT',
    is_actionable: false,
    candidate_direction: 'Short',
    actionable_direction: null,
    candidate_label: 'Short candidate — WAIT',
    reasoning: 'Short WAIT — structure confirmation required.',
    waiting_for: [{ key: 'structure_cycle', label: 'Wait for BOS SUPPLY confirmation.', structure: true }],
    vwap: { side: 'BELOW', wording: 'Price is below VWAP.' },
    structure_guidance: {
      state: 'TREND_INITIAL', direction: 'Short', confirmed: false,
      next_event: 'BOS SUPPLY', next_event_reason: 'Wait for BOS SUPPLY confirmation.',
      summary: 'Short cycle is initial.',
    },
  },
  left_brain: { thesis: { direction: 'SHORT', status: 'ACTIVE' } },
  directions: {},
  decision_timeline: { events: [] },
};

const normalized = normalizeMainBrainPayload(raw);
const verdict = normalized.verdict as Record<string, unknown>;
const presentation = normalized.operator_presentation as Record<string, unknown>;
const explain = extractExplainData(normalized);
const structure = extractStructureGuidance(normalized);

check('candidate direction is backend owned', verdict.candidate_direction, 'Short');
check('candidate label is backend owned', verdict.candidate_label, 'Short candidate — WAIT');
check('WAIT is not actionable', verdict.is_actionable, false);
check('WAIT has no actionable side', verdict.actionable_direction, null);
check('reason is backend owned', verdict.strict_reason, 'Short WAIT — structure confirmation required.');
check('VWAP wording is backend owned', (presentation.vwap as Record<string, unknown>).wording, 'Price is below VWAP.');
check('Decision Clarity uses candidate side', explain.candidateDir, 'SHORT');
check('Decision Clarity keeps WAIT non-actionable', explain.isActionable, false);
check('Waiting For uses operator state', explain.missingConfirmations[0], 'Wait for BOS SUPPLY confirmation.');
check('Market Structure uses operator guidance', structure?.nextEvent, 'BOS SUPPLY');

console.log(`Task #270 frontend contract: ${passed} passed`);