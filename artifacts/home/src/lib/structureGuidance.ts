/**
 * Display-only projection of the resolved market-structure cycle.
 *
 * The backend resolver remains the authority for state transitions and the
 * next valid event. This module deliberately does not infer BOS/CHOCH rules.
 */

export interface StructureGuidance {
  state: string;
  direction: string;
  confirmed: boolean;
  nextEvent: string;
  reason: string;
  summary: string;
  isPendingConfirmation: boolean;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value != null && typeof value === 'object'
    ? value as Record<string, unknown>
    : {};
}

function asText(value: unknown): string {
  return value == null || value === '' || value === 'null' ? '' : String(value);
}

/**
 * Combines cycle metadata with the additive copy projection. The canonical
 * state supplies credit/event metadata; guidance may override only with an
 * explicit resolver-owned value. This keeps older partial projections from
 * making the operator card lose its resolved cycle credit.
 */
export function selectStructureCycleDisplay(p: Record<string, unknown>): Record<string, unknown> {
  const verdict = asRecord(p.verdict);
  const canonical = asRecord(verdict.structure_state ?? p.structure_state);
  const guidance = asRecord(verdict.structure_guidance ?? p.structure_guidance);
  return { ...canonical, ...guidance };
}

/**
 * Selects the resolver-owned presentation contract from a normalized payload.
 * Supports the additive `structure_guidance` field while retaining compatibility
 * with the existing `structure_state` payload shape.
 */
export function extractStructureGuidance(p: Record<string, unknown>): StructureGuidance | null {
  const raw = selectStructureCycleDisplay(p);
  const state = asText(raw.state);
  const nextEvent = asText(raw.next_event);
  const reason = asText(raw.next_event_reason);
  if (!state || !nextEvent || !reason) return null;

  const confirmed = raw.confirmed === true;
  return {
    state,
    direction: asText(raw.direction),
    confirmed,
    nextEvent,
    reason,
    summary: asText(raw.summary),
    isPendingConfirmation: !confirmed && (
      state === 'TREND_INITIAL' || state === 'REVERSAL_CANDIDATE'
    ),
  };
}

/** One operator-facing requirement, copied from resolver-owned guidance. */
export function structureWaitingText(guidance: StructureGuidance): string {
  return `${guidance.nextEvent} — ${guidance.reason}`;
}