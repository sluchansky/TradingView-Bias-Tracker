/**
 * explainDecision.ts
 *
 * Pure functions that power the "Explain Decision" drawer.
 * All inputs come from the normalised Main Brain payload `p`.
 * No side-effects, no API calls, no trading computations.
 * Exported so they can be unit-tested independently of React.
 */
import {
  extractStructureGuidance,
  structureWaitingText,
  type StructureGuidance,
} from './structureGuidance';

// ── Minimal helpers (mirrors of safeStr/safeNum in MainBrain.tsx) ─────────────
function safeStr(v: unknown, fallback = ''): string {
  if (v == null || v === '' || v === 'null') return fallback;
  return String(v);
}
function safeNum(v: unknown): number | null {
  const n = Number(v);
  return v != null && !isNaN(n) && isFinite(n) ? n : null;
}

// ── Public types ──────────────────────────────────────────────────────────────

export interface ScoreComponent {
  key: string;
  label: string;
  points: number;
  present: boolean;
}

export interface OpposingStructure {
  effect: string;           // HARD_BLOCK | SCORE_AWARE | OBSERVED | NONE
  direction: string;        // Bearish | Bullish | Long | Short
  eventType: string;        // BOS | CHOCH | ...
  ageSeconds: number | null;
  remainingSeconds: number | null;
}

export interface ExplainTimelineEvent {
  eventType: string;
  eventLabel: string;
  timestamp: unknown;
  source: string;
  details: unknown;
}

export type CandidateDir = 'LONG' | 'SHORT' | 'NONE';
export type HigherSide    = 'LONG' | 'SHORT' | 'TIED';
export type AlignmentLabel =
  | 'FULLY ALIGNED'
  | 'COUNTER-TREND'
  | 'NEUTRAL'
  | 'CONFLICTED'
  | 'UNKNOWN';

export interface ExplainData {
  // Summary header
  verdict: string;               // READY | WAIT | BLOCKED | MANAGING | …
  isActionable: boolean;
  candidateDir: CandidateDir;
  thesisDir: string;             // display-ready label
  thesisAvailable: boolean;
  thesisStale: boolean;
  thesisConfidence: number | null;
  alignment: AlignmentLabel;

  // Long vs Short
  longScore: number;
  shortScore: number;
  candidateScore: number;
  marginLabel: string;           // "LONG +N" | "SHORT +N" | "TIED"
  higherSide: HigherSide;
  contradiction: boolean;        // candidate dir ≠ higher-score dir

  // Score components per direction
  longComponents: ScoreComponent[];
  shortComponents: ScoreComponent[];
  hasComponents: boolean;

  // Blockers
  hardBlockers: string[];
  missingConfirmations: string[];
  opposingStructure: OpposingStructure | null;
  structureGuidance: StructureGuidance | null;

  // Derived "to-do" list
  mustChange: string[];

  // Timeline
  timelineEvents: ExplainTimelineEvent[];

  // Brain voice
  brainVoice: string;
}

// ── extractExplainData ────────────────────────────────────────────────────────
/**
 * Extracts and normalises all data needed by the Explain Decision drawer from
 * the existing Main Brain payload.  Pure — safe to call multiple times with
 * the same input.
 */
export function extractExplainData(p: Record<string, unknown>): ExplainData {
  const v    = (p.verdict       ?? {}) as Record<string, unknown>;
  const op   = (p.operator_presentation ?? {}) as Record<string, unknown>;
  const lb   = (p.left_brain    ?? {}) as Record<string, unknown>;
  const dirs = (p.directions    ?? {}) as Record<string, unknown>;
  const tl   = (p.decision_timeline ?? {}) as Record<string, unknown>;
  const mb   = (p.main_brain    ?? {}) as Record<string, unknown>;
  const structureGuidance = extractStructureGuidance(p);

  // ── Verdict ───────────────────────────────────────────────────────────────
  const readiness    = safeStr(op.verdict ?? v.readiness ?? v.readiness_label, 'WAIT');
  const isActionable = op.is_actionable === true || (op.is_actionable == null && v.is_actionable === true);
  const score        = safeNum(v.edge_score) ?? 0;
  const rawDir       = safeStr(op.candidate_direction ?? v.direction ?? v.candidate_direction, '');
  const candidateDir: CandidateDir =
    /^long$/i.test(rawDir) ? 'LONG' :
    /^short$/i.test(rawDir) ? 'SHORT' : 'NONE';

  // ── Left Brain thesis ─────────────────────────────────────────────────────
  const lbDiag          = (lb.diagnosis ?? {}) as Record<string, unknown>;
  const diagStatus      = safeStr(lbDiag.status, 'NO_DATA');
  const thesisAvailable = diagStatus === 'AVAILABLE';
  const thesisStale     = diagStatus === 'STALE';
  const thesisRawDir    = safeStr(lb.direction, '');
  const thesisConfidence = safeNum(lb.confidence);

  let thesisDirLabel: string;
  if (!thesisAvailable && !thesisStale) {
    thesisDirLabel = diagStatus === 'COLLECTING_DATA' ? 'COLLECTING DATA' : 'UNAVAILABLE';
  } else if (thesisStale) {
    thesisDirLabel = thesisRawDir ? `${thesisRawDir.toUpperCase()} (STALE)` : 'STALE';
  } else {
    thesisDirLabel = thesisRawDir.toUpperCase() || 'NEUTRAL';
  }

  // ── Alignment ─────────────────────────────────────────────────────────────
  let alignment: AlignmentLabel;
  const tNorm = thesisRawDir.toUpperCase();
  if (candidateDir === 'NONE') {
    alignment = 'UNKNOWN';
  } else if (!thesisAvailable && !thesisStale) {
    alignment = 'UNKNOWN';
  } else if (tNorm === 'NEUTRAL' || tNorm === '') {
    alignment = 'NEUTRAL';
  } else if (
    (candidateDir === 'LONG'  && /bull/i.test(tNorm)) ||
    (candidateDir === 'SHORT' && /bear/i.test(tNorm))
  ) {
    alignment = 'FULLY ALIGNED';
  } else if (
    (candidateDir === 'LONG'  && /bear/i.test(tNorm)) ||
    (candidateDir === 'SHORT' && /bull/i.test(tNorm))
  ) {
    alignment = 'COUNTER-TREND';
  } else {
    alignment = 'CONFLICTED';
  }

  // ── Scores ────────────────────────────────────────────────────────────────
  const bull = (dirs.bull ?? {}) as Record<string, unknown>;
  const bear = (dirs.bear ?? {}) as Record<string, unknown>;
  const longScore  = safeNum(bull.edge_score) ?? (candidateDir === 'LONG'  ? score : 0);
  const shortScore = safeNum(bear.edge_score) ?? (candidateDir === 'SHORT' ? score : 0);

  let marginLabel: string;
  let higherSide: HigherSide;
  const diff = Math.round(longScore - shortScore);
  if (diff > 0) {
    marginLabel = `LONG +${diff}`;
    higherSide  = 'LONG';
  } else if (diff < 0) {
    marginLabel = `SHORT +${Math.abs(diff)}`;
    higherSide  = 'SHORT';
  } else {
    marginLabel = 'TIED';
    higherSide  = 'TIED';
  }
  const contradiction =
    candidateDir !== 'NONE' && higherSide !== 'TIED' && (higherSide as string) !== candidateDir;

  // ── Score components ──────────────────────────────────────────────────────
  const toComps = (raw: unknown): ScoreComponent[] => {
    if (!Array.isArray(raw)) return [];
    return (raw as Record<string, unknown>[]).map(c => ({
      key:     safeStr(c.key, ''),
      label:   safeStr(c.label, safeStr(c.key, '').replace(/_/g, ' ')),
      points:  safeNum(c.points) ?? 0,
      present: c.present === true,
    }));
  };

  const mainComps   = toComps(v.edge_components);
  const bullComps   = toComps(bull.edge_components);
  const bearComps   = toComps(bear.edge_components);

  const longComponents  = bullComps.length > 0 ? bullComps  : (candidateDir === 'LONG'  ? mainComps : []);
  const shortComponents = bearComps.length > 0 ? bearComps  : (candidateDir === 'SHORT' ? mainComps : []);
  const hasComponents   = mainComps.length > 0 || bullComps.length > 0 || bearComps.length > 0;

  // ── Blockers ──────────────────────────────────────────────────────────────
  const hardBlockers = Array.isArray(v.hard_blockers)
    ? (v.hard_blockers as unknown[]).map(String).filter(Boolean) : [];
  const presentationWaiting = Array.isArray(op.waiting_for)
    ? (op.waiting_for as Record<string, unknown>[]).map(item => safeStr(item.label ?? item.key, '')).filter(Boolean)
    : [];
  const missingConfirmations = presentationWaiting.length > 0 ? presentationWaiting : Array.isArray(v.missing_confirmations)
    ? (v.missing_confirmations as unknown[]).map(String).filter(Boolean) : [];

  // ── Opposing structure ────────────────────────────────────────────────────
  const osRaw = v.opposing_structure as Record<string, unknown> | null | undefined;
  const opposingStructure: OpposingStructure | null =
    osRaw != null && osRaw.detected === true
      ? {
          effect:           safeStr(osRaw.effect, 'NONE'),
          direction:        safeStr(osRaw.direction, ''),
          eventType:        safeStr(osRaw.event_type, ''),
          ageSeconds:       safeNum(osRaw.age_seconds),
          remainingSeconds: safeNum(osRaw.remaining_seconds),
        }
      : null;

  // ── Must-change list ──────────────────────────────────────────────────────
  const mustChange: string[] = [];
  if (structureGuidance?.isPendingConfirmation) {
    mustChange.push(structureWaitingText(structureGuidance));
  }
  for (const m of missingConfirmations) mustChange.push(`${m} must confirm`);
  if (
    opposingStructure &&
    opposingStructure.effect !== 'NONE' &&
    opposingStructure.effect !== 'OBSERVED'
  ) {
    mustChange.push(
      `Opposing ${opposingStructure.direction} ${opposingStructure.eventType} must expire or be overridden`
    );
  }
  for (const b of hardBlockers) mustChange.push(`${b} must clear`);
  if (!isActionable && candidateDir !== 'NONE' && mustChange.length === 0) {
    mustChange.push('Edge score must reach the readiness threshold');
  }

  // ── Timeline (last 10, deduped, most-recent first) ────────────────────────
  const rawEvents = Array.isArray(tl.events)
    ? (tl.events as Record<string, unknown>[]) : [];
  const seen = new Set<string>();
  const timelineEvents: ExplainTimelineEvent[] = rawEvents
    .filter(e => {
      const fp = `${e.event_type}::${e.timestamp}::${e.event_label}`;
      if (seen.has(fp)) return false;
      seen.add(fp);
      return true;
    })
    .slice(-10)
    .reverse()
    .map(e => ({
      eventType:  safeStr(e.event_type, ''),
      eventLabel: safeStr(e.event_label ?? e.event_type, '—'),
      timestamp:  e.timestamp,
      source:     safeStr(e.source, ''),
      details:    e.details ?? null,
    }));

  return {
    verdict: readiness.toUpperCase(),
    isActionable,
    candidateDir,
    thesisDir: thesisDirLabel,
    thesisAvailable,
    thesisStale,
    thesisConfidence,
    alignment,
    longScore,
    shortScore,
    candidateScore: candidateDir === 'LONG' ? longScore : candidateDir === 'SHORT' ? shortScore : score,
    marginLabel,
    higherSide,
    contradiction,
    longComponents,
    shortComponents,
    hasComponents,
    hardBlockers,
    missingConfirmations,
    opposingStructure,
    structureGuidance,
    mustChange,
    timelineEvents,
    brainVoice: safeStr(mb.voice ?? op.reasoning, ''),
  };
}

// ── buildPlainEnglishSummary ──────────────────────────────────────────────────
/**
 * Deterministic template-based plain-English summary.
 * Never invents facts — only uses fields from ExplainData.
 */
export function buildPlainEnglishSummary(d: ExplainData): string {
  const structureNote = d.structureGuidance?.isPendingConfirmation
    ? ` Structure cycle: ${d.structureGuidance.reason}`
    : '';

  // Thesis unavailable / collecting
  if (!d.thesisAvailable && !d.thesisStale) {
    if (d.thesisDir === 'COLLECTING DATA') {
      return `Left Brain is still collecting bar-close data. No directional thesis yet — the system is in monitoring mode.${structureNote}`;
    }
    return `Market thesis is ${d.thesisDir.toLowerCase()}. The system is waiting for Left Brain data before confirming a directional bias.${structureNote}`;
  }

  // No active candidate
  if (d.candidateDir === 'NONE') {
    const thesisNote = d.thesisAvailable
      ? ` Market thesis is ${d.thesisDir.toLowerCase()}.` : '';
    return `No active trade candidate.${thesisNote} The system is monitoring but no setup meets the entry criteria yet.${structureNote}`;
  }

  const cand  = d.candidateDir.toLowerCase();
  const other = cand === 'long' ? 'short' : 'long';
  const thesis = d.thesisDir.toLowerCase();

  // READY + fully aligned
  if (d.isActionable && d.alignment === 'FULLY ALIGNED') {
    return `The ${cand} setup is READY and aligned with the ${thesis} market thesis. Edge score is ${d.candidateScore}. No hard blocks are active.`;
  }

  // READY + counter-trend
  if (d.isActionable && d.alignment === 'COUNTER-TREND') {
    return `The ${cand} setup is READY but counter-trend — the market thesis is ${thesis}. Trading against the prevailing bias carries additional risk.`;
  }

  // READY + neutral thesis
  if (d.isActionable) {
    return `The ${cand} setup is READY with an edge score of ${d.candidateScore}. Market thesis is ${thesis}.`;
  }

  // Hard block active
  if (d.hardBlockers.length > 0) {
    const b = d.hardBlockers[0];
    return `A hard block is active: ${b}. The ${cand} setup cannot become READY until this clears.${structureNote}`;
  }

  // Opposing structure blocking
  if (
    d.opposingStructure &&
    d.opposingStructure.effect !== 'NONE' &&
    d.opposingStructure.effect !== 'OBSERVED'
  ) {
    const os = d.opposingStructure;
    return `Opposing ${os.direction.toLowerCase()} ${os.eventType} is blocking the ${cand} candidate. The system is waiting for the opposing signal to expire.`;
  }

  // Counter-trend waiting
  if (d.alignment === 'COUNTER-TREND') {
    const missStr = d.missingConfirmations.length > 0
      ? ` and still missing ${d.missingConfirmations[0].toLowerCase()}` : '';
    return `The market thesis is ${thesis} but a ${cand} setup is forming. It is counter-trend${missStr}.${structureNote} The system is correctly waiting rather than recommending a ${d.candidateDir} entry.`;
  }

  if (d.structureGuidance?.isPendingConfirmation) {
    return `The ${cand} candidate is building. ${d.structureGuidance.reason}`;
  }

  // Missing confirmations
  if (d.missingConfirmations.length > 0) {
    const miss = d.missingConfirmations[0];
    const scoreNote = d.marginLabel !== 'TIED'
      ? ` The ${d.higherSide.toLowerCase()} side currently leads (${d.marginLabel}).` : '';
    return `The ${cand} candidate is building but still missing ${miss}.${scoreNote} The system is waiting for confirmation.`;
  }

  // Score contradiction
  if (d.contradiction) {
    const higherScore = d.higherSide === 'LONG' ? d.longScore : d.shortScore;
    const lowerScore  = d.higherSide === 'LONG' ? d.shortScore : d.longScore;
    return `The ${cand} direction is the active candidate, but the ${d.higherSide.toLowerCase()} side currently has a higher score (${higherScore} vs ${lowerScore}). The system has selected ${d.candidateDir} based on strategy context.`;
  }

  // Default waiting
  const scoreNote = d.candidateScore > 0 ? ` Current edge score: ${d.candidateScore}.` : '';
  const missNote  = d.missingConfirmations.length > 0
    ? ` Still waiting for: ${d.missingConfirmations.join(', ')}.` : '';
  return `The ${cand} setup is building.${scoreNote}${missNote} The system is monitoring for entry conditions.`;
}
