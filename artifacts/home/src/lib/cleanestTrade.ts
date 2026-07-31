/**
 * cleanestTrade.ts — Canonical cleanest-trade ranking utility
 *
 * Exact port of pickCleanestSetup() from the legacy dashboard JS.
 *
 * Rules:
 *  - Pure functions only: no DOM, no fetch, no React, no side-effects.
 *  - No new ranking algorithm — identical comparison to the old home page.
 *  - Testable with any runner (tsx, jest, python port, etc.).
 *
 * Ranking algorithm (mirrors pickCleanestSetup):
 *   1. Actionable (READY / EARLY READY) beats non-actionable (WAIT).
 *   2. Within the same tier: higher Edge Score wins.
 *   3. Still tied (same act AND same edge): first in iteration order wins
 *      (stable: MGC_SCALP > MGC_SWING > MNQ_SCALP > MNQ_SWING > ...).
 */

// ── Constants ─────────────────────────────────────────────────────────────────

export const SCAN_INSTRUMENTS = ['MGC', 'MNQ', 'MES', 'MYM'] as const;
export const SCAN_MODES       = ['SCALP', 'SWING']            as const;

export type ScanInstrument = typeof SCAN_INSTRUMENTS[number];
export type ScanMode       = typeof SCAN_MODES[number];

/** The exact set of verdict strings treated as "actionable" by jsIsActionable(). */
const ACTIONABLE_VERDICTS = new Set([
  'LONG READY',
  'SHORT READY',
  'LONG EARLY READY',
  'SHORT EARLY READY',
]);

// ── Types ─────────────────────────────────────────────────────────────────────

/** Shape of a raw /status?ticker=X&mode=Y JSON response (partial — only the
 *  fields this module reads). Callers may pass the full response; extra keys
 *  are ignored. */
export interface StatusRecord {
  verdict?:        string;
  edge_score?:     number | null;
  short_score?:    number | null;
  long_score?:     number | null;
  strict_reason?:  string | null;
  active_ticker?:  string;
  generated_at?:   string;
  brain?: {
    decision?: { verdict?: string; direction?: string | null };
    score?:    { value?: number; grade?: string };
  };
  trade_plan?: Record<string, unknown>;
  directions?: Record<string, {
    potential_plan?: Record<string, unknown>;
    edge_score?:     number | null;
    missing?:        string[];
    [key: string]:   unknown;
  }>;
  strategy_engine?:    { active_strategy?: string };
  opposing_structure?: unknown;
  strict_missing?:     string[];
}

export interface RankInput {
  instrument: ScanInstrument;
  mode:       ScanMode;
  record:     StatusRecord | null;
}

export interface CleanestCandidate {
  instrument: ScanInstrument;
  mode:       ScanMode;
  direction:  'Long' | 'Short';
  /** 1 = actionable (READY/EARLY READY); 0 = non-actionable (WAIT) */
  act:   0 | 1;
  edge:  number;
  verdict: string;
  record:  StatusRecord;
}

// ── Pure helpers ──────────────────────────────────────────────────────────────

/** Mirror of jsIsActionable(): true when the verdict is READY or EARLY READY. */
export function isActionableVerdict(v: unknown): boolean {
  return ACTIONABLE_VERDICTS.has(String(v ?? ''));
}

/** Mirror of getBrain(d).decision.verdict: reads d.brain first, then d.verdict. */
export function getVerdictFromRecord(d: StatusRecord): string {
  return String(d.brain?.decision?.verdict ?? d.verdict ?? 'WAIT');
}

/** Mirror of getBrain(d).score.value: reads d.brain.score.value then d.edge_score. */
export function getEdgeFromRecord(d: StatusRecord): number {
  const v = d.brain?.score?.value ?? d.edge_score;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

/** Mirror of the direction-selection logic in pickCleanestSetup:
 *    - Actionable: brain.decision.direction (or parse from verdict string).
 *    - WAIT:       short_score > long_score → 'Short', else 'Long'. */
export function getDirectionFromRecord(d: StatusRecord): 'Long' | 'Short' {
  const v = getVerdictFromRecord(d);
  if (isActionableVerdict(v)) {
    const bdir = d.brain?.decision?.direction;
    if (bdir === 'Short') return 'Short';
    if (bdir === 'Long')  return 'Long';
    // Fallback: parse from verdict string
    if (/LONG/.test(v))  return 'Long';
    if (/SHORT/.test(v)) return 'Short';
  }
  const ls = Number(d.long_score  ?? 0);
  const ss = Number(d.short_score ?? 0);
  return ss > ls ? 'Short' : 'Long';
}

// ── Canonical ranking ─────────────────────────────────────────────────────────

/**
 * Apply the canonical cleanest-trade ranking to a list of status records.
 *
 * Exact port of the comparison block in pickCleanestSetup():
 *   const better = (cand.act !== best.act) ? (cand.act > best.act)
 *                                          : (cand.edge > best.edge);
 *
 * Null records (failed fetches) are silently skipped.
 * Returns null when every record is null (all fetches failed).
 */
export function rankCandidates(inputs: RankInput[]): CleanestCandidate | null {
  let best: CleanestCandidate | null = null;
  for (const { instrument, mode, record } of inputs) {
    if (!record) continue;
    const v    = getVerdictFromRecord(record);
    const act  = (isActionableVerdict(v) ? 1 : 0) as 0 | 1;
    const edge = getEdgeFromRecord(record);
    const cand: CleanestCandidate = {
      instrument, mode, act, edge, verdict: v,
      direction: getDirectionFromRecord(record),
      record,
    };
    if (!best) { best = cand; continue; }
    // Identical comparison to pickCleanestSetup
    const better = cand.act !== best.act ? cand.act > best.act : cand.edge > best.edge;
    if (better) best = cand;
  }
  return best;
}

// ── Plan extraction ───────────────────────────────────────────────────────────

/**
 * Extract the canonical trade-plan dict from a /status record for a given direction.
 *
 * Priority:
 *   1. top-level trade_plan (READY path)
 *   2. directions[dir].potential_plan (POTENTIAL path)
 *
 * Never re-calculates values — reads verbatim from the record.
 * Returns null when no plan is available.
 */
export function getPlanFromRecord(
  record: StatusRecord,
  direction: 'Long' | 'Short',
): Record<string, unknown> | null {
  // READY: top-level trade_plan has trade_plan:true
  const tp = record.trade_plan;
  if (tp && tp.trade_plan === true) return tp as Record<string, unknown>;
  // POTENTIAL: per-direction potential_plan
  const blk = (record.directions ?? {})[direction];
  if (blk?.potential_plan && blk.potential_plan.trade_plan === true) {
    return blk.potential_plan as Record<string, unknown>;
  }
  return null;
}

/**
 * Derive a human-readable list of ranking reasons for the "Why ranked first"
 * section. Based exclusively on existing canonical fields — no new logic.
 */
export function getRankingReasons(
  winner: CleanestCandidate,
  allInputs: RankInput[],
): string[] {
  const reasons: string[] = [];
  const allEdges = allInputs
    .filter(i => i.record !== null)
    .map(i => getEdgeFromRecord(i.record!));
  const maxEdge = allEdges.length ? Math.max(...allEdges) : 0;

  if (winner.act === 1) {
    reasons.push(`Actionable setup — verdict: ${winner.verdict}`);
    // Check whether any other candidate was also actionable
    const otherActionable = allInputs.some(i =>
      i.record &&
      (i.instrument !== winner.instrument || i.mode !== winner.mode) &&
      isActionableVerdict(getVerdictFromRecord(i.record))
    );
    if (!otherActionable) {
      reasons.push('Only actionable candidate in the current scan');
    }
  } else {
    reasons.push('No actionable (READY/EARLY) setup found — best available WAIT shown');
  }

  reasons.push(`Highest Edge Score in scan: ${winner.edge} / 110`);
  if (winner.edge === maxEdge && winner.edge > 0) {
    reasons.push('Edge Score leads all scanned instruments × modes');
  }

  const plan = getPlanFromRecord(winner.record, winner.direction);
  if (plan) {
    if (plan.stop_valid === true) reasons.push('Risk plan valid — stop distance confirmed');
    if (plan.entry_zone)          reasons.push(`Entry zone anchored: ${String(plan.entry_zone)}`);
  } else {
    reasons.push('No entry/stop plan available for this candidate');
  }

  const sr = winner.record.strict_reason;
  if (sr) reasons.push(`Blocker: ${sr}`);

  return reasons;
}
