/**
 * V1 Phase 7C — Main Brain Operator Console
 *
 * Operator console sourced from GET /api/main-brain plus authenticated,
 * deliberate operator actions. Explanation panels are read-only; execution,
 * journal, research-repair, and configuration controls use existing guarded
 * server endpoints and never bypass the backend safety boundaries.
 * Auth: same Basic Auth pattern as Home.tsx (localStorage brain_auth).
 * Polling: 7 s (reduced when hidden). Manual refresh control included.
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link, useLocation, useParams } from 'wouter';
import { LiveMarketChart } from '../components/LiveMarketChart';
import { normalizeMainBrainPayload } from '../lib/mainBrainNormalizer';
import { NAV_ITEMS, KNOWN_SECTIONS, SECTION_LABELS } from '../lib/navItems';
import { TRAINING_LANES, normalizeTrainingSection } from '../lib/trainingLanes';
import { audioManager, SoundEvent } from '../lib/audioManager';
import { loadQueue, saveQueue, upsertAlert } from '../lib/globalAlerts';
import { announceDashboardAuth } from '../lib/dashboardAuth';
import {
  rankCandidates, getPlanFromRecord, getRankingReasons,
  isActionableVerdict,
  SCAN_INSTRUMENTS, SCAN_MODES,
  type StatusRecord, type CleanestCandidate, type RankInput,
} from '../lib/cleanestTrade';
import {
  extractExplainData, buildPlainEnglishSummary,
  type ScoreComponent,
} from '../lib/explainDecision';
import {
  extractStructureGuidance, selectStructureCycleDisplay, structureWaitingText,
} from '../lib/structureGuidance';
import {
  visualBrainConfidence, visualBrainText, visualBrainToken,
} from '../lib/visualBrainModes';
import { getTopOfBookPresentation } from '../lib/topOfBookPresentation';
import {
  classifyDatabentoFreshness,
  classifyVisualBrainFreshness,
  formatFreshnessAge,
  latestBarTimestampMs,
} from '../lib/marketDataFreshness';

// ── Design tokens ────────────────────────────────────────────────────────────
const T = {
  bg:        '#050c1a',
  panel:     '#0b1628',
  panelAlt:  '#0e1d36',
  border:    'rgba(255,255,255,0.07)',
  borderMid: 'rgba(255,255,255,0.12)',
  cyan:      '#38bdf8',
  blue:      '#3b82f6',
  green:     '#22c55e',
  amber:     '#f59e0b',
  red:       '#ef4444',
  purple:    '#a855f7',
  txtPri:    '#f8fafc',
  txtSec:    'rgba(248,250,252,0.82)',
  txtMuted:  'rgba(248,250,252,0.55)',
  mono:      "'JetBrains Mono','Menlo',monospace",
} as const;

// ── Helpers ──────────────────────────────────────────────────────────────────
function safeStr(v: unknown, fallback = '—'): string {
  if (v == null || v === '' || v === 'null') return fallback;
  return String(v);
}
function safeNum(v: unknown): number | null {
  const n = Number(v);
  return (v != null && !isNaN(n) && isFinite(n)) ? n : null;
}
function fmtNum(v: unknown, dec = 2): string {
  const n = safeNum(v);
  return n != null ? n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) : '—';
}
function fmtTs(v: unknown): string {
  if (!v) return '—';
  try {
    const d = new Date(String(v));
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'Etc/GMT+4' });
  } catch { return '—'; }
}
function fmtAge(v: unknown): string {
  if (!v) return '';
  try {
    const s = Math.floor((Date.now() - new Date(String(v)).getTime()) / 1000);
    if (isNaN(s) || s < 0) return '';
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return `${Math.floor(s / 3600)}h ago`;
  } catch { return ''; }
}
function dirColor(d: unknown): string {
  const s = String(d ?? '').toLowerCase();
  if (/long|bull/.test(s)) return T.green;
  if (/short|bear/.test(s)) return T.red;
  return T.txtSec;
}
function readinessColor(r: unknown): string {
  const s = String(r ?? '').toUpperCase();
  if (s === 'READY') return T.green;
  if (s === 'BUILDING' || s === 'FORMING') return T.amber;
  if (s === 'MANAGING') return T.cyan;
  return T.txtSec;
}
function statusDot(ok: boolean | null): React.ReactNode {
  const col = ok == null ? T.amber : ok ? T.green : T.red;
  return <span aria-hidden style={{ display:'inline-block', width:7, height:7, borderRadius:'50%', background:col, boxShadow:`0 0 6px ${col}55`, flexShrink:0 }} />;
}
function escTxt(s: string): string {
  // textContent-safe — no innerHTML usage on live data
  return s;
}

// ── AI response escape (XSS contract) ────────────────────────────────────────
// React renders JSX text nodes without innerHTML so this is a pass-through that
// documents the XSS-safe rendering contract.  If dangerouslySetInnerHTML is
// ever introduced, use a full sanitiser here.
function aiEsc(s: string): string { return s; }

// ── Streaming reveal hook ─────────────────────────────────────────────────────
function useStream(text: string, charPerTick = 10): { text: string; live: boolean } {
  const [shown, setShown] = useState('');
  const [live,  setLive]  = useState(false);
  const prevRef = useRef('');
  useEffect(() => {
    if (text === prevRef.current) return;
    prevRef.current = text;
    if (!text) { setShown(''); setLive(false); return; }
    setLive(true);
    let i = shown.length;
    const id = setInterval(() => {
      i = Math.min(i + charPerTick, text.length);
      setShown(text.slice(0, i));
      if (i >= text.length) { clearInterval(id); setLive(false); }
    }, 16);
    return () => clearInterval(id);
  }, [text]); // eslint-disable-line react-hooks/exhaustive-deps
  return { text: shown, live };
}

// ── Ask AI types ──────────────────────────────────────────────────────────────
type MbMsg     = { id: number; role: 'user' | 'brain'; text: string };
type MbMemTag   = 'chat' | 'insight';
type MbMemEntry = { t: number; tag: MbMemTag; text: string };

// ── Conversation memory (shared localStorage key with Home.tsx) ───────────────
// Same brain_mem_YYYY-MM-DD key keeps both pages in the same session context.
function _mbMemKey(): string {
  const d = new Date();
  return `brain_mem_${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function useMbConvMemory() {
  const [entries, setEntries] = useState<MbMemEntry[]>(() => {
    try { const r = localStorage.getItem(_mbMemKey()); return r ? (JSON.parse(r) as MbMemEntry[]) : []; }
    catch { return []; }
  });

  const addEntry = useCallback((tag: MbMemTag, text: string) => {
    const entry: MbMemEntry = { t: Date.now(), tag, text: text.slice(0, 200) };
    setEntries(prev => {
      const next = [...prev, entry].slice(-60);
      try { localStorage.setItem(_mbMemKey(), JSON.stringify(next)); } catch {}
      return next;
    });
  }, []);

  const context = useMemo((): string => {
    const PERSONA = [
      '[ANALYST VOICE — apply strictly]',
      'You are a senior institutional futures trader narrating the tape live.',
      'Direct and concise; present tense, active voice.',
      'For every answer: (1) what you see, (2) why it matters, (3) what changes your read.',
      'Never use filler or disclaimers. Analysis only — never place or modify trades.',
      '---',
    ].join('\n');
    if (entries.length === 0) return PERSONA + '\n';
    const TAG: Record<MbMemTag, string> = { chat: 'YOU', insight: 'BRAIN' };
    const lines = entries.slice(-20).map(e => {
      const hh = new Date(e.t).toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Etc/GMT+4',
      });
      return `${hh} [${TAG[e.tag]}] ${e.text}`;
    });
    return PERSONA + '\n[TODAY\'S SESSION]\n' + lines.join('\n') + '\n---\n';
  }, [entries]);

  return { context, addEntry };
}

// ── Main Brain context builder ────────────────────────────────────────────────
// Produces a compact text snapshot of the currently-displayed Main Brain state.
// Only reads from the already-normalised `p` payload — never computes trading values.
// This prefix is prepended to the user's question so the AI knows exactly
// what the operator sees.
function buildMbContext(p: Record<string, unknown>, ticker: string): string {
  const mkt     = (p.market     ?? {}) as Record<string, unknown>;
  const verdict = (p.verdict    ?? {}) as Record<string, unknown>;
  const thesis  = (p.thesis ?? {}) as Record<string, unknown>;
  const advisory = (p.left_brain ?? {}) as Record<string, unknown>;
  const cp      = (p.candidate_preview ?? {}) as Record<string, unknown>;
  const eb      = (p.edge_breakdown    ?? {}) as Record<string, unknown>;
  const scanner = (p.strategy_scanner  ?? {}) as Record<string, unknown>;
  const mb      = (p.main_brain        ?? {}) as Record<string, unknown>;
  const mbSig   = (mb.signals          ?? {}) as Record<string, unknown>;
  const at      = (p.active_trades     ?? {}) as Record<string, unknown>;
  const coach   = (p.coach             ?? {}) as Record<string, unknown>;
  const sys     = (p.system_status     ?? {}) as Record<string, unknown>;
  const propFirm    = (p.prop_firm ?? {}) as Record<string, unknown>;
  const propMetrics = (propFirm.metrics ?? {}) as Record<string, unknown>;
  const fundamental = (p.fundamental_context ?? {}) as Record<string, unknown>;

  const edgeScore = Math.round(safeNum(eb.score ?? eb.total) ?? 0);
  const edgeGrade = safeStr(eb.grade ?? eb.label, '');

  const thDir    = safeStr(thesis.direction ?? thesis.thesis_direction, '');
  const thStr    = safeStr(thesis.strength  ?? thesis.thesis_strength, '');
  const thAge    = safeStr(thesis.age_display ?? thesis.age, '');
  const thStatus = safeStr(thesis.status    ?? thesis.thesis_status, '');
  const thEntry  = safeStr(thesis.entryStatus ?? thesis.entry_status, 'WAIT');
  const thReason = safeStr(thesis.reason, '');
  const advDir   = safeStr(advisory.direction, '');
  const advConf  = safeStr(advisory.confidence, '');

  const vReadiness = verdict.is_actionable === true
    ? 'READY'
    : safeStr(verdict.readiness_label, 'WAIT');
  const vDir     = safeStr(verdict.direction ?? verdict.candidate_direction, '');
  const vCandSt  = safeStr(verdict.candidate_status, '');
  const vBlock   = Array.isArray(verdict.hard_blockers)
    ? (verdict.hard_blockers as string[]).filter(Boolean).join(', ')
    : safeStr(verdict.hard_blockers, '');
  const vMissing = Array.isArray(verdict.missing_confirmations)
    ? (verdict.missing_confirmations as string[]).filter(Boolean).join(', ')
    : safeStr(verdict.missing_confirmations, '');

  const activeSt = safeStr(
    scanner.active_strategy ?? scanner.selected_strategy ?? mbSig.strategy, '');
  const trades   = Array.isArray(at.trades) ? (at.trades as Record<string, unknown>[]) : [];
  const tradeSum = trades.length > 0
    ? trades.map(t => `${safeStr(t.direction)} ${safeStr(t.instrument)} @ ${safeStr(t.entry_price)}`).join(', ')
    : 'None';
  const coachWt  = safeStr(coach.weight_status ?? coach.weight_label, '');
  const coachN   = safeStr(coach.sample_count  ?? coach.n, '');

  const tsET = new Date().toLocaleTimeString('en-US', {
    hour12: false, hour: '2-digit', minute: '2-digit', timeZone: 'Etc/GMT+4',
  });

  const lines: string[] = [
    `[MAIN BRAIN — ${ticker} — ${tsET} UTC-4]`,
    `Instrument: ${ticker} | Mode: ${safeStr(mkt.trading_mode, '—')} | Session: ${safeStr(mkt.session_status, '—')}`,
    '',
    'ACTIVE PERSISTENT THESIS — DETERMINISTIC CONTINUITY',
    `Direction: ${thDir || '—'} | Strength: ${thStr || '—'} | Status: ${thStatus || '—'} | Age: ${thAge || '—'}`,
    `Entry Status: ${thEntry} | Reason: ${thReason || '—'}`,
    '',
    'CURRENT MARKET CANDIDATE — STRICT EVALUATOR',
    `Readiness: ${vReadiness} | Edge: ${edgeScore}/110 | Grade: ${edgeGrade || '—'}`,
    `Candidate Direction: ${vDir || '—'} | Candidate Status: ${vCandSt || '—'}`,
    '',
    'ADVISORY / RESEARCH — NOT ENTRY AUTHORITY',
    `Left Brain: ${advDir || '—'} | Confidence: ${advConf || '—'}`,
    '',
    'FUNDAMENTALS — SHADOW ONLY',
    `Status: ${safeStr(fundamental.status, 'NOT ENABLED')} | Event: ${safeStr(fundamental.event_name, 'None')}`,
    `Phase: ${safeStr(fundamental.event_phase, 'NONE')} | Minutes: ${safeStr(fundamental.minutes_to_event, '—')} | Reason: ${safeStr(fundamental.reason, '—')}`,
    '',
    'STRATEGY',
    `Active: ${activeSt || '—'}`,
    '',
    'BLOCKERS',
    `Hard Blockers: ${vBlock || 'None'}`,
    `Missing Confirmations: ${vMissing || 'None'}`,
    '',
    'TRADE PREVIEW',
    cp.entry_zone != null
      ? `Entry: ${safeStr(cp.entry_zone)} | Stop: ${safeStr(cp.stop_loss)} | TP: ${safeStr(cp.take_profit)} | R:R ${safeStr(cp.risk_reward)}`
      : 'No candidate developing',
    '',
    'RISK',
    // Read operator risk fields from prop_firm.metrics (backend-calculated, not recalculated here)
    `P&L Today: $${safeStr(propMetrics.pnl_today, '—')} | Limit: $${safeStr(propMetrics.daily_loss_limit, '—')} | Remaining: $${safeStr(propMetrics.daily_loss_remaining, '—')}`,
    `Exposure: ${safeStr(propMetrics.open_contracts, '—')} contracts | Max: ${safeStr(propMetrics.max_contracts, '—')}`,
    propFirm.enabled !== true ? 'Prop Protection: OFF' : `Prop Protection: ON (${safeStr((propFirm.account as Record<string,unknown> | null | undefined)?.name ?? propFirm.headline, 'active')})`,
    '',
    'ACTIVE TRADES',
    tradeSum,
    '',
    'LEARNING',
    `Weight Status: ${coachWt || '—'} | Samples: ${coachN || '—'}`,
    '',
    'SYSTEM',
    `DB: ${sys.db_ready ? 'OK' : 'ERROR'} | Learning: ${sys.learning_ready ? 'OK' : 'ERROR'}`,
    '---',
    '',
  ];
  return lines.join('\n');
}

// ── Clock hook ────────────────────────────────────────────────────────────────
function useClock() {
  const [t, setT] = useState('');
  useEffect(() => {
    const tick = () => setT(new Date().toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'Etc/GMT+4',
    }) + ' UTC-4');
    tick(); const id = setInterval(tick, 1000); return () => clearInterval(id);
  }, []);
  return t;
}

// ── Auth header (same pattern as Home.tsx) ────────────────────────────────────
function getAuthHeader(): Record<string, string> {
  try {
    const p = localStorage.getItem('brain_auth') || '';
    return p ? { 'Authorization': 'Basic ' + btoa('admin:' + p) } : {};
  } catch { return {}; }
}

// ── Immutable verdict history (review-only) ───────────────────────────────────
type VerdictHistoryEvent = {
  event_id: string | number;
  observation_key: string;
  previous_observation_key: string | null;
  recorded_at: string | null;
  source_timestamp: string | null;
  verdict: string;
  wait_ready_state: string;
  actionable: boolean;
  blocked: boolean;
  score: number | null;
  grade: string | null;
  confidence: number | null;
  candidate_direction: string | null;
  actionable_direction: string | null;
  blockers: string[];
  waiting_for: string[];
  chain_status: 'ROOT' | 'WINDOW_START' | 'CONTIGUOUS' | 'BROKEN';
  chain_expected_previous: string | null;
};
type VerdictHistoryResponse = {
  ok: boolean;
  available: boolean;
  read_only: boolean;
  observer_only: boolean;
  instrument: string;
  mode: string;
  count: number;
  chain: { status: 'EMPTY' | 'VALID' | 'PARTIAL' | 'BROKEN'; roots: number; contiguous: number; breaks: number; partial: boolean };
  jump: {
    status: 'NONE' | 'RESOLVED' | 'NOT_FOUND' | 'UNAVAILABLE' | 'INVALID';
    requested_event_id: string | number | null;
    requested_timestamp: string | null;
    resolved_event_id: number | null;
    resolved_recorded_at: string | null;
  };
  events: VerdictHistoryEvent[];
  page: {
    before_event_id: number | null;
    through_event_id: number | null;
    resume_before_event_id: number | null;
    resume_through_event_id: number | null;
    first_event_id: number | null;
    last_event_id: number | null;
    has_older: boolean;
    has_newer: boolean;
    older_before_event_id: number | null;
    newer_boundary_status: 'LATEST' | 'CONTIGUOUS' | 'BROKEN' | 'ROOT' | 'EMPTY';
    newer_boundary_event_id: number | null;
  };
  error?: string;
};

const HISTORY_INSTRUMENTS = ['MGC', 'MNQ', 'MES', 'MYM'] as const;
const HISTORY_MODES = ['SCALP', 'INTRADAY_TREND'] as const;

type VerdictHistoryJump = { eventId: string | null; timestamp: string | null };
type VerdictHistoryUrlState = {
  instrument: string;
  mode: string;
  jump: VerdictHistoryJump;
  error: string | null;
};

function readVerdictHistoryUrl(): VerdictHistoryUrlState {
  const latest: VerdictHistoryUrlState = {
    instrument: 'MGC',
    mode: 'SCALP',
    jump: { eventId: null, timestamp: null },
    error: null,
  };
  try {
    const query = new URLSearchParams(window.location.search);
    const rawInstrument = query.get('instrument')?.trim().toUpperCase() || null;
    const rawMode = query.get('mode')?.trim().toUpperCase() || null;
    const eventId = query.get('event_id')?.trim() || null;
    const timestamp = query.get('timestamp')?.trim() || null;
    const hasIncidentUrlState = [rawInstrument, rawMode, eventId, timestamp].some(Boolean);
    if (!hasIncidentUrlState) return latest;

    const instrument = rawInstrument && (HISTORY_INSTRUMENTS as readonly string[]).includes(rawInstrument)
      ? rawInstrument
      : latest.instrument;
    const mode = rawMode && (HISTORY_MODES as readonly string[]).includes(rawMode)
      ? rawMode
      : latest.mode;
    let error: string | null = null;
    if (!rawInstrument || !rawMode) {
      error = 'The shared incident link is incomplete. It must include instrument, mode, and one exact incident locator.';
    } else if (instrument !== rawInstrument) {
      error = 'The shared incident link has an invalid instrument.';
    } else if (mode !== rawMode) {
      error = 'The shared incident link must use a canonical mode.';
    } else if (Boolean(eventId) === Boolean(timestamp)) {
      error = 'The shared incident link must include exactly one event ID or UTC timestamp.';
    } else if (eventId && !/^[1-9]\d*$/.test(eventId)) {
      error = 'The shared incident link has an invalid event ID.';
    }
    return {
      instrument,
      mode,
      jump: { eventId, timestamp },
      error,
    };
  } catch {
    return { ...latest, error: 'The shared incident link could not be read.' };
  }
}

function writeVerdictHistoryUrl(instrument: string, mode: string, eventId: number | string): void {
  try {
    const query = new URLSearchParams(window.location.search);
    query.set('instrument', instrument);
    query.set('mode', mode);
    query.set('event_id', String(eventId));
    query.delete('timestamp');
    const search = query.toString();
    window.history.replaceState(
      null,
      '',
      `${window.location.pathname}${search ? `?${search}` : ''}${window.location.hash}`,
    );
  } catch {
    // URL state is a convenience for reopening an audit, never a prerequisite.
  }
}

function pushVerdictHistoryLocatorUrl(
  instrument: string,
  mode: string,
  jump: VerdictHistoryJump,
): void {
  try {
    const query = new URLSearchParams(window.location.search);
    query.set('instrument', instrument);
    query.set('mode', mode);
    if (jump.eventId) {
      query.set('event_id', jump.eventId);
      query.delete('timestamp');
    } else if (jump.timestamp) {
      query.set('timestamp', jump.timestamp);
      query.delete('event_id');
    } else {
      query.delete('event_id');
      query.delete('timestamp');
    }
    const search = query.toString();
    window.history.pushState(
      null,
      '',
      `${window.location.pathname}${search ? `?${search}` : ''}${window.location.hash}`,
    );
  } catch {
    // URL state is a convenience for reopening an audit, never a prerequisite.
  }
}

function clearVerdictHistoryUrl(push = false): void {
  try {
    const query = new URLSearchParams(window.location.search);
    query.delete('instrument');
    query.delete('mode');
    query.delete('event_id');
    query.delete('timestamp');
    const search = query.toString();
    const write = push ? window.history.pushState : window.history.replaceState;
    write.call(
      window.history,
      null,
      '',
      `${window.location.pathname}${search ? `?${search}` : ''}${window.location.hash}`,
    );
  } catch {
    // URL state is a convenience for reopening an audit, never a prerequisite.
  }
}

function useVerdictHistory(
  instrument: string,
  mode: string,
  limit: number,
  beforeEventId: number | null,
  throughEventId: number | null,
  jump: VerdictHistoryJump,
  urlError: string | null,
) {
  const [state, setState] = useState<{ data: VerdictHistoryResponse | null; loading: boolean; error: string | null }>({
    data: null, loading: !urlError, error: urlError,
  });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    if (urlError) {
      setState({ data: null, loading: false, error: urlError });
      return () => { cancelled = true; };
    }
    setState(s => ({ ...s, loading: true, error: null }));
    const query = new URLSearchParams({ instrument, mode, limit: String(limit) });
    if (jump.eventId) {
      query.set('event_id', jump.eventId);
    } else if (jump.timestamp) {
      query.set('timestamp', jump.timestamp);
    } else if (beforeEventId != null && Number.isInteger(beforeEventId) && beforeEventId > 0) {
      query.set('before_event_id', String(beforeEventId));
    } else if (throughEventId != null && Number.isInteger(throughEventId) && throughEventId > 0) {
      query.set('through_event_id', String(throughEventId));
    }
    fetch(`/api/authoritative-verdict-history?${query.toString()}`, {
      credentials: 'include', headers: getAuthHeader(),
    }).then(async response => {
      const body = await response.json().catch(() => null) as VerdictHistoryResponse | null;
      if (!response.ok) throw new Error(body?.error || `History unavailable (${response.status})`);
      if (!body) throw new Error('History unavailable');
      if (!body.available || body.ok === false) throw new Error(body.error || 'History unavailable');
      return body;
    }).then(data => {
      if (!cancelled) setState({ data, loading: false, error: null });
    }).catch(error => {
      if (!cancelled) setState({ data: null, loading: false, error: error instanceof Error ? error.message : 'History unavailable' });
    });
    return () => { cancelled = true; };
  }, [instrument, mode, limit, beforeEventId, throughEventId, jump.eventId, jump.timestamp, revision, urlError]);
  return { ...state, refresh: () => setRevision(value => value + 1) };
}

function historyDate(value: string | null): string {
  if (!value) return 'Timestamp unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Timestamp unavailable' : date.toLocaleString('en-US', {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false, timeZoneName: 'short',
  });
}

function historyErrorLabel(error: string | null, jump: VerdictHistoryJump, instrument: string, mode: string): string {
  if (error?.startsWith('The shared incident link')) return error;
  if (jump.eventId || jump.timestamp) {
    if (error === 'jump_target_not_found') {
      return `No incident found in ${instrument} · ${mode} for that exact locator.`;
    }
    if (error === 'invalid_event_id' || error === 'invalid_timestamp' || error === 'invalid_jump') {
      return 'The incident locator is invalid. Enter one positive event ID or one UTC timestamp.';
    }
    return 'The incident history is unavailable for that locator. No live or alternate data is shown.';
  }
  return error || 'History unavailable';
}

function historyErrorTestId(error: string | null, jump: VerdictHistoryJump): string {
  if (error?.startsWith('The shared incident link')) return 'status-history-jump-invalid';
  if (!jump.eventId && !jump.timestamp) return 'status-history-unavailable';
  if (error === 'jump_target_not_found') return 'status-history-jump-not-found';
  if (error === 'invalid_event_id' || error === 'invalid_timestamp' || error === 'invalid_jump') {
    return 'status-history-jump-invalid';
  }
  return 'status-history-jump-unavailable';
}

const VerdictHistoryPage: React.FC = () => {
  const [initialUrlState] = useState<VerdictHistoryUrlState>(() => readVerdictHistoryUrl());
  const [instrument, setInstrument] = useState<string>(initialUrlState.instrument);
  const [mode, setMode] = useState<string>(initialUrlState.mode);
  const [limit, setLimit] = useState(100);
  const [beforeEventId, setBeforeEventId] = useState<number | null>(null);
  const [throughEventId, setThroughEventId] = useState<number | null>(null);
  const [cursorStack, setCursorStack] = useState<Array<{ before: number | null; through: number | null }>>([]);
  const [pageNumber, setPageNumber] = useState(0);
  const [jumpEventId, setJumpEventId] = useState(initialUrlState.jump.eventId ?? '');
  const [jumpTimestamp, setJumpTimestamp] = useState(initialUrlState.jump.timestamp ?? '');
  const [jumpValidation, setJumpValidation] = useState<string | null>(null);
  const [urlError, setUrlError] = useState<string | null>(initialUrlState.error);
  const [jump, setJump] = useState<VerdictHistoryJump>(initialUrlState.jump);
  const [copyState, setCopyState] = useState<'idle' | 'copying' | 'success' | 'unavailable'>('idle');
  const { data, loading, error, refresh } = useVerdictHistory(
    instrument, mode, limit, beforeEventId, throughEventId, jump, urlError,
  );
  useEffect(() => {
    const handlePopState = () => {
      const next = readVerdictHistoryUrl();
      setInstrument(next.instrument);
      setMode(next.mode);
      setBeforeEventId(null);
      setThroughEventId(null);
      setCursorStack([]);
      setPageNumber(0);
      setJumpEventId(next.jump.eventId ?? '');
      setJumpTimestamp(next.jump.timestamp ?? '');
      setJumpValidation(null);
      setUrlError(next.error);
      setJump(next.jump);
      setCopyState('idle');
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);
  useEffect(() => {
    if (data?.jump?.status === 'RESOLVED' && data.jump.resolved_event_id != null) {
      writeVerdictHistoryUrl(instrument, mode, data.jump.resolved_event_id);
    }
  }, [data?.jump?.status, data?.jump?.resolved_event_id, instrument, mode]);
  const resetPagination = (pushUrl = false) => {
    setCursorStack([]);
    setBeforeEventId(null);
    setThroughEventId(null);
    setPageNumber(0);
    setJump({ eventId: null, timestamp: null });
    setJumpValidation(null);
    setUrlError(null);
    setCopyState('idle');
    clearVerdictHistoryUrl(pushUrl);
  };
  const submitJump = () => {
    const eventValue = jumpEventId.trim();
    const timestampValue = jumpTimestamp.trim();
    if (Boolean(eventValue) === Boolean(timestampValue)) {
      setJumpValidation('Enter either an exact event ID or a UTC timestamp, not both.');
      return;
    }
    setJumpValidation(null);
    setUrlError(null);
    pushVerdictHistoryLocatorUrl(instrument, mode, {
      eventId: eventValue || null,
      timestamp: timestampValue || null,
    });
    setCursorStack([]);
    setBeforeEventId(null);
    setThroughEventId(null);
    setPageNumber(0);
    setCopyState('idle');
    setJump({
      eventId: eventValue || null,
      timestamp: timestampValue || null,
    });
  };
  const clearJump = () => {
    setJumpEventId('');
    setJumpTimestamp('');
    resetPagination(true);
  };
  const copyIncidentLink = async () => {
    const resolvedEventId = data?.jump?.status === 'RESOLVED' ? data.jump.resolved_event_id : null;
    if (resolvedEventId == null) {
      setCopyState('unavailable');
      return;
    }
    setCopyState('copying');
    try {
      const url = new URL(window.location.href);
      const urlInstrument = url.searchParams.get('instrument')?.trim().toUpperCase();
      const urlMode = url.searchParams.get('mode')?.trim().toUpperCase();
      const urlEventId = url.searchParams.get('event_id')?.trim();
      if (
        urlInstrument !== instrument
        || urlMode !== mode
        || urlEventId !== String(resolvedEventId)
        || url.searchParams.has('timestamp')
      ) {
        throw new Error('Resolved incident URL is not canonical');
      }
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(url.toString());
      setCopyState('success');
    } catch {
      setCopyState('unavailable');
    }
  };
  const loadOlder = () => {
    if (!data?.page.has_older || data.page.older_before_event_id == null || loading) return;
    setCursorStack(stack => [...stack, {
      before: data.page.resume_before_event_id,
      through: data.page.resume_through_event_id,
    }]);
    setJump({ eventId: null, timestamp: null });
    setBeforeEventId(data.page.older_before_event_id);
    setThroughEventId(null);
    setPageNumber(page => page + 1);
  };
  const loadNewer = () => {
    if (!data?.page.has_newer || loading) return;
    if (cursorStack.length > 0) {
      const priorCursor = cursorStack[cursorStack.length - 1];
      setCursorStack(stack => stack.slice(0, -1));
      setBeforeEventId(priorCursor.before);
      setThroughEventId(priorCursor.through);
      setPageNumber(page => Math.max(0, page - 1));
    } else if (throughEventId != null || jump.eventId != null || jump.timestamp != null) {
      setJump({ eventId: null, timestamp: null });
      setBeforeEventId(null);
      setThroughEventId(null);
    }
  };
  const chain = data?.chain;
  const chainColor = chain?.status === 'VALID' ? T.green : chain?.status === 'BROKEN' ? T.red : chain?.status === 'PARTIAL' ? T.amber : T.txtMuted;
  const boundaryStatus = data?.page?.newer_boundary_status ?? 'EMPTY';
  const boundaryColor = boundaryStatus === 'BROKEN' ? T.red : boundaryStatus === 'CONTIGUOUS' ? T.green : boundaryStatus === 'ROOT' ? T.cyan : T.txtMuted;
  const olderBoundaryStatus = data?.events?.[0]?.chain_status ?? 'EMPTY';
  const olderBoundaryColor = olderBoundaryStatus === 'BROKEN' ? T.red : olderBoundaryStatus === 'WINDOW_START' ? T.amber : olderBoundaryStatus === 'ROOT' ? T.cyan : T.green;
  const newerBoundaryLabel = boundaryStatus === 'CONTIGUOUS'
    ? 'CONTIGUOUS · VERIFIED'
    : boundaryStatus === 'BROKEN'
      ? 'BROKEN · CONTINUITY NOT VERIFIED'
      : boundaryStatus === 'LATEST'
        ? 'LATEST SNAPSHOT'
        : boundaryStatus;
  const olderBoundaryLabel = olderBoundaryStatus === 'WINDOW_START'
    ? 'CONTIGUOUS · VERIFIED TO OUTSIDE LINK'
    : olderBoundaryStatus === 'BROKEN'
      ? 'BROKEN · CONTINUITY NOT VERIFIED'
      : olderBoundaryStatus === 'ROOT'
        ? 'ROOT · VERIFIED'
        : olderBoundaryStatus;
  return (
    <div data-testid="page-verdict-history" style={{ maxWidth: 1320, margin: '0 auto' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:16, marginBottom:16, flexWrap:'wrap' }}>
        <div>
          <div style={{ color:T.cyan, fontSize:9, fontWeight:800, letterSpacing:'0.16em', marginBottom:6 }}>AUDIT SURFACE / OBSERVER ONLY</div>
          <h1 data-testid="heading-verdict-history" style={{ margin:0, fontSize:26, letterSpacing:'-0.03em', fontWeight:800 }}>Verdict history</h1>
          <p style={{ margin:'7px 0 0', color:T.txtMuted, fontSize:12 }}>Immutable final decisions. This view cannot influence trading.</p>
        </div>
        <div data-testid="status-read-only" style={{ border:`1px solid ${T.cyan}44`, background:`${T.cyan}0d`, color:T.cyan, borderRadius:6, padding:'7px 10px', fontSize:9, fontWeight:800, letterSpacing:'0.1em' }}>READ ONLY · IMMUTABLE</div>
      </div>
      <div data-testid="history-filters" style={{ display:'flex', gap:8, alignItems:'end', flexWrap:'wrap', padding:'11px 12px', marginBottom:12, background:T.panel, border:`1px solid ${T.border}`, borderRadius:9 }}>
        <label style={{ display:'grid', gap:5, color:T.txtMuted, fontSize:9, fontWeight:700, letterSpacing:'0.08em' }}>INSTRUMENT
          <select data-testid="select-history-instrument" value={instrument} onChange={event => { resetPagination(); setInstrument(event.target.value); }} style={{ ...historySelectStyle, color:T.txtPri }}>
            {HISTORY_INSTRUMENTS.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label style={{ display:'grid', gap:5, color:T.txtMuted, fontSize:9, fontWeight:700, letterSpacing:'0.08em' }}>MODE
          <select data-testid="select-history-mode" value={mode} onChange={event => { resetPagination(); setMode(event.target.value); }} style={{ ...historySelectStyle, color:T.txtPri }}>
            {HISTORY_MODES.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label style={{ display:'grid', gap:5, color:T.txtMuted, fontSize:9, fontWeight:700, letterSpacing:'0.08em' }}>EVENT LIMIT
          <select data-testid="select-history-limit" value={limit} onChange={event => { resetPagination(); setLimit(Number(event.target.value)); }} style={{ ...historySelectStyle, color:T.txtPri }}>
            {[50, 100, 250, 500].map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <button data-testid="button-refresh-verdict-history" onClick={refresh} style={{ ...historyButtonStyle, marginLeft:'auto' }}>Refresh history</button>
      </div>
      <div data-testid="history-jump-controls" style={{ display:'flex', gap:8, alignItems:'end', flexWrap:'wrap', padding:'11px 12px', marginBottom:12, background:T.panel, border:`1px solid ${T.border}`, borderRadius:9 }}>
        <div style={{ minWidth:180, marginRight:4 }}>
          <div style={{ color:T.cyan, fontSize:9, fontWeight:800, letterSpacing:'0.1em' }}>JUMP TO INCIDENT</div>
          <div style={{ color:T.txtMuted, fontSize:9, marginTop:4 }}>Uses immutable history in the selected scope only.</div>
        </div>
        <label style={{ display:'grid', gap:5, color:T.txtMuted, fontSize:9, fontWeight:700, letterSpacing:'0.08em' }}>EXACT EVENT ID
          <input data-testid="input-history-jump-event-id" type="number" min="1" step="1" inputMode="numeric" value={jumpEventId} onChange={event => setJumpEventId(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') submitJump(); }} placeholder="e.g. 12457" style={{ ...historySelectStyle, minWidth:150, color:T.txtPri }} />
        </label>
        <label style={{ display:'grid', gap:5, color:T.txtMuted, fontSize:9, fontWeight:700, letterSpacing:'0.08em' }}>RECORDED AT OR AFTER (UTC)
          <input data-testid="input-history-jump-timestamp" type="datetime-local" value={jumpTimestamp} onChange={event => setJumpTimestamp(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') submitJump(); }} style={{ ...historySelectStyle, minWidth:210, color:T.txtPri }} />
        </label>
        <button data-testid="button-jump-verdict-history" onClick={submitJump} disabled={loading} style={{ ...historyButtonStyle, opacity:loading ? 0.45 : 1, cursor:loading ? 'wait' : 'pointer' }}>Open incident window</button>
        {(jump.eventId || jump.timestamp) && <button data-testid="button-clear-verdict-history-jump" onClick={clearJump} style={{ ...historyButtonStyle, borderColor:T.borderMid, color:T.txtSec, background:T.panelAlt }}>Return to latest</button>}
        {jumpValidation && <div data-testid="status-history-jump-invalid" role="status" style={{ flexBasis:'100%', color:T.amber, fontSize:10 }}>{jumpValidation}</div>}
      </div>
      {loading ? <HistorySkeleton /> : error ? (
        <div data-testid={historyErrorTestId(error, jump)} role="status" style={historyEmptyStyle}>
          <strong style={{ color:T.amber }}>{jump.eventId || jump.timestamp || error.startsWith('The shared incident link') ? 'Incident lookup did not resolve' : 'History unavailable'}</strong>
          <span style={{ color:T.txtMuted, marginTop:6 }}>{historyErrorLabel(error, jump, instrument, mode)}</span>
          <button data-testid="button-retry-verdict-history" onClick={refresh} style={{ ...historyButtonStyle, marginTop:14 }}>Retry</button>
        </div>
      ) : !data?.events?.length ? (
        <div data-testid="status-history-empty" role="status" style={historyEmptyStyle}>
          <strong style={{ color:T.txtPri }}>No immutable verdicts recorded</strong>
          <span style={{ color:T.txtMuted, marginTop:6 }}>History is available, but no events exist for this selection.</span>
        </div>
      ) : (
        <>
           {data.jump?.status === 'RESOLVED' && data.jump.resolved_event_id != null && <div data-testid="history-jump-resolution" style={{ display:'flex', justifyContent:'space-between', gap:12, alignItems:'center', flexWrap:'wrap', marginBottom:12, padding:'10px 12px', background:`${T.cyan}0d`, border:`1px solid ${T.cyan}44`, borderRadius:7 }}>
             <div><span style={{ color:T.cyan, fontSize:9, fontWeight:800, letterSpacing:'0.09em' }}>INCIDENT ANCHOR RESOLVED</span><div style={{ color:T.txtPri, fontSize:12, marginTop:4 }}>Event {data.jump.resolved_event_id} · {historyDate(data.jump.resolved_recorded_at ?? data.events[data.events.length - 1]?.recorded_at ?? null)}</div></div>
             <div style={{ display:'flex', alignItems:'center', gap:10, flexWrap:'wrap' }}>
               <div style={{ color:T.txtMuted, fontSize:9 }}>Timestamp jumps select the first immutable event recorded at or after the requested UTC time.</div>
               <button data-testid="button-copy-verdict-history" type="button" onClick={() => { void copyIncidentLink(); }} disabled={copyState === 'copying'} style={{ ...historyButtonStyle, opacity:copyState === 'copying' ? 0.55 : 1, cursor:copyState === 'copying' ? 'wait' : 'pointer' }}>
                 {copyState === 'copying' ? 'Copying…' : 'Copy incident link'}
               </button>
               {copyState === 'success' && <span data-testid="status-history-copy-success" role="status" style={{ color:T.green, fontSize:9, fontWeight:700 }}>Incident link copied.</span>}
               {copyState === 'unavailable' && <span data-testid="status-history-copy-unavailable" role="status" style={{ color:T.amber, fontSize:9, fontWeight:700 }}>Clipboard unavailable. Copy the URL from the address bar.</span>}
             </div>
          </div>}
          <div data-testid="history-chain-summary" style={{ display:'grid', gridTemplateColumns:'minmax(180px,1.2fr) repeat(4,1fr)', gap:8, marginBottom:12 }}>
            <div style={{ ...historyMetricStyle, borderColor:`${chainColor}55` }}><span>CHAIN INTEGRITY</span><b data-testid="value-chain-status" style={{ color:chainColor }}>{chain?.status ?? 'EMPTY'}</b></div>
            <div style={historyMetricStyle}><span>EVENTS</span><b data-testid="value-history-count">{data.count}</b></div>
            <div style={historyMetricStyle}><span>ROOTS</span><b>{chain?.roots ?? 0}</b></div>
            <div style={historyMetricStyle}><span>CONTIGUOUS</span><b>{chain?.contiguous ?? 0}</b></div>
            <div style={historyMetricStyle}><span>BREAKS</span><b style={{ color:(chain?.breaks ?? 0) > 0 ? T.red : T.txtPri }}>{chain?.breaks ?? 0}</b></div>
          </div>
           <div data-testid="history-pagination" style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, flexWrap:'wrap', padding:'9px 11px', marginBottom:12, background:T.panel, border:`1px solid ${boundaryStatus === 'BROKEN' ? T.red + '66' : T.border}`, borderRadius:7 }}>
             <div style={{ display:'flex', alignItems:'center', gap:9, flexWrap:'wrap' }}>
                <span data-testid="history-page-indicator" style={{ color:T.txtPri, fontSize:10, fontWeight:800, letterSpacing:'0.08em' }}>{pageNumber === 0 ? (data.jump?.status === 'RESOLVED' ? 'INCIDENT WINDOW' : throughEventId == null ? 'LATEST' : 'LATEST SNAPSHOT') : `OLDER PAGE ${pageNumber}`}</span>
                <div data-testid="history-chain-boundaries" style={{ display:'flex', gap:9, flexWrap:'wrap' }}>
                  <span data-testid="history-older-boundary" style={{ color:olderBoundaryColor, fontSize:9, fontWeight:800, letterSpacing:'0.07em' }}>OLDER BOUNDARY: {olderBoundaryLabel}</span>
                  <span data-testid="history-newer-boundary" style={{ color:boundaryColor, fontSize:9, fontWeight:800, letterSpacing:'0.07em' }}>NEWER BOUNDARY: {newerBoundaryLabel}</span>
                </div>
             </div>
             <div style={{ display:'flex', gap:6 }}>
               <button data-testid="button-newer-verdict-history" onClick={loadNewer} disabled={loading || !data.page.has_newer} aria-label="Load newer verdict history page" style={{ ...historyButtonStyle, opacity: loading || !data.page.has_newer ? 0.4 : 1, cursor: loading || !data.page.has_newer ? 'not-allowed' : 'pointer' }}>Newer page</button>
               <button data-testid="button-older-verdict-history" onClick={loadOlder} disabled={loading || !data.page.has_older || data.page.older_before_event_id == null} aria-label="Load older verdict history page" style={{ ...historyButtonStyle, opacity: loading || !data.page.has_older || data.page.older_before_event_id == null ? 0.4 : 1, cursor: loading || !data.page.has_older || data.page.older_before_event_id == null ? 'not-allowed' : 'pointer' }}>Older page</button>
             </div>
           </div>
          <div data-testid="verdict-history-timeline" style={{ display:'grid', gap:7 }}>
            {data.events.map((event, index) => <VerdictHistoryRow key={`${event.event_id}-${index}`} event={event} index={index} />)}
          </div>
        </>
      )}
    </div>
  );
};

const historySelectStyle: React.CSSProperties = { minWidth:130, background:T.panelAlt, border:`1px solid ${T.borderMid}`, borderRadius:5, padding:'8px 9px', fontFamily:T.mono, fontSize:11, outline:'none' };
const historyButtonStyle: React.CSSProperties = { border:`1px solid ${T.cyan}55`, background:`${T.cyan}14`, color:T.cyan, borderRadius:5, padding:'8px 12px', fontSize:10, fontWeight:800, cursor:'pointer' };
const historyEmptyStyle: React.CSSProperties = { minHeight:180, display:'flex', flexDirection:'column', justifyContent:'center', alignItems:'center', textAlign:'center', background:T.panel, border:`1px solid ${T.border}`, borderRadius:9, fontSize:12 };
const historyMetricStyle: React.CSSProperties = { display:'grid', gap:5, background:T.panel, border:`1px solid ${T.border}`, borderRadius:7, padding:'10px 11px', minWidth:0 };

const VerdictHistoryRow: React.FC<{ event: VerdictHistoryEvent; index: number }> = ({ event, index }) => {
  const continuityColor = event.chain_status === 'CONTIGUOUS' ? T.green : event.chain_status === 'BROKEN' ? T.red : event.chain_status === 'WINDOW_START' ? T.amber : T.cyan;
  const verdictColor = event.actionable ? T.green : event.blocked ? T.amber : T.txtSec;
  return <article data-testid={`row-verdict-history-${event.event_id}`} style={{ display:'grid', gridTemplateColumns:'26px minmax(190px,1.1fr) minmax(150px,0.8fr) minmax(200px,1.2fr) minmax(170px,1fr)', gap:12, alignItems:'center', padding:'11px 12px', background:T.panel, border:`1px solid ${event.chain_status === 'BROKEN' ? T.red + '66' : T.border}`, borderRadius:7, animation:'mbHistoryIn 0.25s ease both', animationDelay:`${Math.min(index, 8) * 25}ms` }}>
    <div aria-hidden style={{ height:'100%', minHeight:42, borderLeft:`2px solid ${continuityColor}`, position:'relative' }}><span style={{ position:'absolute', top:0, left:-5, width:8, height:8, borderRadius:'50%', background:continuityColor }} /></div>
    <div><div style={{ color:T.txtMuted, fontSize:9, letterSpacing:'0.05em' }}>{historyDate(event.recorded_at)}</div><div data-testid={`text-observation-${event.event_id}`} title={event.observation_key} style={{ color:T.txtPri, fontFamily:T.mono, fontSize:10, marginTop:5, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{event.observation_key}</div><div style={{ color:T.txtMuted, fontSize:9, marginTop:3 }}>Event {event.event_id}</div></div>
    <div><div style={{ color:verdictColor, fontSize:15, fontWeight:800, letterSpacing:'0.02em' }}>{safeStr(event.verdict)}</div><div style={{ color:T.txtMuted, fontSize:10, marginTop:4 }}>{safeStr(event.wait_ready_state)} · {event.actionable ? 'Actionable' : event.blocked ? 'Blocked' : 'Not actionable'}</div><div style={{ color:dirColor(event.actionable_direction ?? event.candidate_direction), fontSize:10, marginTop:3 }}>{safeStr(event.actionable_direction ?? event.candidate_direction)}</div></div>
    <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:7 }}><div><small style={historyLabelStyle}>SCORE</small><b style={historyValueStyle}>{event.score == null ? '—' : event.score}</b></div><div><small style={historyLabelStyle}>GRADE</small><b style={historyValueStyle}>{safeStr(event.grade)}</b></div><div><small style={historyLabelStyle}>CONF.</small><b style={historyValueStyle}>{event.confidence == null ? '—' : event.confidence}</b></div><div style={{ gridColumn:'1 / -1', color:T.txtMuted, fontSize:9 }}>Source {historyDate(event.source_timestamp)}</div></div>
    <div>
      <div style={{ color:continuityColor, fontSize:9, fontWeight:800, letterSpacing:'0.08em' }}>{event.chain_status.replace('_', ' ')} LINK</div>
      <div style={{ color:T.txtMuted, fontFamily:T.mono, fontSize:9, marginTop:5, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }} title={event.previous_observation_key ?? 'Root event'}>{event.chain_status === 'ROOT' ? 'Root observation' : `prev ${safeStr(event.previous_observation_key)}`}</div>
      {event.chain_status === 'WINDOW_START' && <div style={{ color:T.amber, fontSize:9, marginTop:4 }}>Earlier link is outside this window</div>}
      {event.chain_status === 'BROKEN' && <div style={{ color:T.red, fontSize:9, marginTop:4 }}>Expected {safeStr(event.chain_expected_previous)}</div>}
      <div data-testid={`blockers-verdict-history-${event.event_id}`} style={{ marginTop:7 }}>
        <small style={historyLabelStyle}>BLOCKERS</small>
        <div style={{ color:event.blockers.length ? T.amber : T.txtMuted, fontSize:9, marginTop:3, lineHeight:1.45, overflowWrap:'anywhere' }}>{event.blockers.map(value => safeStr(value)).join(' · ') || 'None recorded'}</div>
      </div>
      <div data-testid={`waiting-verdict-history-${event.event_id}`} style={{ marginTop:6 }}>
        <small style={historyLabelStyle}>WAITING FOR</small>
        <div style={{ color:T.txtMuted, fontSize:9, marginTop:3, lineHeight:1.45, overflowWrap:'anywhere' }}>{event.waiting_for.map(value => safeStr(value)).join(' · ') || 'None recorded'}</div>
      </div>
    </div>
  </article>;
};
const historyLabelStyle: React.CSSProperties = { display:'block', color:T.txtMuted, fontSize:8, letterSpacing:'0.08em' };
const historyValueStyle: React.CSSProperties = { display:'block', color:T.txtPri, fontFamily:T.mono, fontSize:12, marginTop:3 };
const HistorySkeleton: React.FC = () => <div data-testid="status-history-loading" aria-label="Loading verdict history" style={{ display:'grid', gap:7 }}>{[1,2,3,4].map(item => <div key={item} style={{ height:78, borderRadius:7, background:`linear-gradient(90deg, ${T.panel} 25%, ${T.panelAlt} 50%, ${T.panel} 75%)`, backgroundSize:'200% 100%', animation:'mbHistoryShimmer 1.4s ease-in-out infinite' }} />)}</div>;

// ── Payload normalizer ────────────────────────────────────────────────────────
// Imported from src/lib/mainBrainNormalizer.ts (pure TypeScript, testable).
// See that module for full documentation and Phase 7C.3 contract tests.

// ── Data fetching hook ────────────────────────────────────────────────────────
type FetchState = 'idle' | 'loading' | 'loaded' | 'refreshing' | 'stale' | 'auth_fail' | 'error';

interface MainBrainState {
  payload: Record<string, unknown> | null;
  fetchState: FetchState;
  lastOk: number | null;
  error: string | null;
  isAuthFail: boolean;
}

const POLL_INTERVAL_MS = 7000;
const STALE_THRESHOLD_MS = 30000;

function useMainBrain(ticker: string): MainBrainState & { refresh: () => void } {
  const [state, setState] = useState<MainBrainState>({
    payload: null, fetchState: 'idle', lastOk: null, error: null, isAuthFail: false,
  });
  const inFlight = useRef(false);
  const lastPayload = useRef<Record<string, unknown> | null>(null);
  // After a 401/403, auto-polling stops until the operator manually refreshes.
  // Without this guard the poll would reset fetchState to 'loading' every 7 s,
  // causing the page to oscillate between "Connecting" and "AUTH REQUIRED"
  // instead of settling on the auth-failure screen.
  const authFailedRef = useRef(false);

  const fetchNow = useCallback(async (reason: 'poll' | 'manual') => {
    if (inFlight.current) return;
    // Auto-polls do not retry after an auth failure — only an explicit manual
    // refresh (e.g. clicking the Refresh button) is allowed to retry.
    if (authFailedRef.current && reason !== 'manual') return;
    inFlight.current = true;
    setState(s => ({
      ...s,
      fetchState: s.fetchState === 'loaded' || s.fetchState === 'stale'
        ? (reason === 'manual' ? 'refreshing' : s.fetchState)
        : 'loading',
    }));
    try {
      const url = `/api/main-brain${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ''}`;
      const r = await fetch(url, { credentials: 'include', headers: getAuthHeader() });
      if (r.status === 401 || r.status === 403) {
        announceDashboardAuth(false);
        authFailedRef.current = true;
        setState(s => ({ ...s, fetchState: 'auth_fail', isAuthFail: true, error: 'Authentication required' }));
        inFlight.current = false; return;
      }
      if (!r.ok) {
        setState(s => ({
          ...s,
          fetchState: lastPayload.current ? 'stale' : 'error',
          error: `Server error ${r.status}`,
        }));
        inFlight.current = false; return;
      }
      let data: Record<string, unknown>;
      try { data = await r.json(); } catch {
        setState(s => ({ ...s, fetchState: lastPayload.current ? 'stale' : 'error', error: 'Invalid JSON response' }));
        inFlight.current = false; return;
      }
      const normalized = normalizeMainBrainPayload(data);
      lastPayload.current = normalized;
      authFailedRef.current = false; // clear on successful auth
      announceDashboardAuth(true, getAuthHeader()['Authorization'] ?? '');
      setState({ payload: normalized, fetchState: 'loaded', lastOk: Date.now(), error: null, isAuthFail: false });
    } catch (e) {
      setState(s => ({
        ...s,
        fetchState: lastPayload.current ? 'stale' : 'error',
        error: 'Network error',
      }));
    } finally { inFlight.current = false; }
  }, [ticker]);

  // Initial load
  useEffect(() => { fetchNow('manual'); }, [fetchNow]);

  // Polling — paused when page is hidden
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return;
      const now = Date.now();
      setState(s => {
        if (s.lastOk && now - s.lastOk > STALE_THRESHOLD_MS && s.fetchState === 'loaded') {
          return { ...s, fetchState: 'stale' };
        }
        return s;
      });
      fetchNow('poll');
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchNow]);

  return { ...state, payload: state.payload ?? lastPayload.current, refresh: () => fetchNow('manual') };
}

// ── Reusable components ───────────────────────────────────────────────────────
const Panel: React.FC<{ title: string; badge?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; style?: React.CSSProperties; id?: string }> = ({ title, badge, right, children, style, id }) => (
  <section id={id} className="mb-panel" style={{ background: T.panel, border: `1px solid ${T.border}`, borderRadius: 10, overflow:'hidden', ...style }} aria-label={title}>
    <div className="mb-panel-header" style={{ display:'flex', alignItems:'center', gap:8, padding:'10px 14px', borderBottom:`1px solid ${T.border}`, background:'rgba(255,255,255,0.015)' }}>
      <span style={{ fontSize:10, fontWeight:700, letterSpacing:'0.1em', textTransform:'uppercase', color:T.txtSec, flex:1 }}>{title}</span>
      {badge}
      {right}
    </div>
    <div className="mb-panel-body" style={{ padding:'12px 14px' }}>{children}</div>
  </section>
);

const Badge: React.FC<{ label: string; color?: string }> = ({ label, color = T.txtMuted }) => (
  <span style={{ fontSize:9.5, fontWeight:700, letterSpacing:'0.08em', color, border:`1px solid ${color}44`, borderRadius:4, padding:'2px 6px', textTransform:'uppercase' }}>{label}</span>
);

const KV: React.FC<{ label: string; value: React.ReactNode; mono?: boolean; valueColor?: string }> = ({ label, value, mono, valueColor }) => (
  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'4px 0', borderBottom:`1px solid ${T.border}` }}>
    <span style={{ fontSize:10.5, color:T.txtMuted }}>{label}</span>
    <span style={{ fontSize:11, fontWeight:600, color: valueColor ?? T.txtPri, fontFamily: mono ? T.mono : undefined }}>{value ?? '—'}</span>
  </div>
);

const Pill: React.FC<{ text: string; color: string }> = ({ text, color }) => (
  <span style={{ display:'inline-flex', alignItems:'center', gap:5, fontSize:10, fontWeight:700, letterSpacing:'0.07em',
    color, background:`${color}18`, border:`1px solid ${color}44`, borderRadius:5, padding:'2px 8px', textTransform:'uppercase' }}>
    {text}
  </span>
);

const UnavailableNote: React.FC<{ msg?: string }> = ({ msg }) => (
  <div style={{ textAlign:'center', padding:'20px 0', color:T.txtMuted, fontSize:11 }}>
    {msg ?? 'Data unavailable'}
  </div>
);

// ── Databento MBP-1 pressure (read-only display) ───────────────────────────────
// The server redacts bid/ask sizes as soon as a quote becomes stale.  This panel
// only renders that contract; it does not derive, score, or submit trading data.
const TopOfBookPressurePanel: React.FC<{ p: Record<string, unknown>; ticker: string }> = ({ p, ticker }) => {
  const book = getTopOfBookPresentation((p.top_of_book ?? {}) as Record<string, unknown>);
  const { state, bid, ask, imbalance, age, history, cumulativePressure, averageImbalance, historySamples } = book;
  const isUsable = book.live;
  const displayImbalance = imbalance ?? 0;
  const side = displayImbalance > 0 ? 'BID HEAVY' : displayImbalance < 0 ? 'ASK HEAVY' : 'BALANCED';
  const pressureColor = displayImbalance > 0 ? T.green : displayImbalance < 0 ? T.red : T.txtSec;
  const stateColor = state === 'LIVE' ? T.green : state === 'STALE' ? T.amber : T.txtMuted;
  const ageLabel = age == null ? '—' : age < 1 ? '<1s' : `${age.toFixed(age < 10 ? 1 : 0)}s`;
  const hasTimeline = history.length > 1;
  const average = averageImbalance ?? 0;
  const cumulative = cumulativePressure ?? 0;
  const historyPath = history.map((point, index) => {
    const x = history.length === 1 ? 160 : (index / (history.length - 1)) * 320;
    const y = 28 - (Math.max(-1, Math.min(1, point.imbalance)) * 24);
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
  const historyColor = average > 0.04 ? T.green : average < -0.04 ? T.red : T.txtSec;
  const historySide = average > 0.04 ? 'BID PRESSURE' : average < -0.04 ? 'ASK PRESSURE' : 'BALANCED';

  return (
    <Panel
      id="top-of-book-pressure"
      title={`Live Bid / Ask Pressure · ${ticker}`}
      badge={<Badge label={state} color={stateColor} />}
    >
      {!isUsable ? (
        <div role="status" style={{ padding:'5px 0 2px', color: state === 'STALE' ? T.amber : T.txtMuted, fontSize:11, lineHeight:1.5 }}>
          {state === 'STALE'
            ? `Quote is stale (${ageLabel}); bid and ask sizes are intentionally hidden until the feed refreshes.`
            : book.message}
        </div>
      ) : (
        <>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
            <div style={{ background:`${T.green}12`, border:`1px solid ${T.green}2f`, borderRadius:7, padding:'8px 10px' }}>
              <div style={{ fontSize:9, letterSpacing:'0.08em', color:T.txtMuted, fontWeight:700 }}>BID SIZE</div>
              <div style={{ marginTop:3, color:T.green, fontFamily:T.mono, fontSize:19, fontWeight:800 }}>{fmtNum(bid, 0)}</div>
            </div>
            <div style={{ background:`${T.red}12`, border:`1px solid ${T.red}2f`, borderRadius:7, padding:'8px 10px' }}>
              <div style={{ fontSize:9, letterSpacing:'0.08em', color:T.txtMuted, fontWeight:700 }}>ASK SIZE</div>
              <div style={{ marginTop:3, color:T.red, fontFamily:T.mono, fontSize:19, fontWeight:800 }}>{fmtNum(ask, 0)}</div>
            </div>
          </div>
          <div style={{ marginTop:10, display:'flex', alignItems:'center', gap:8 }}>
            <div style={{ flex:1, height:6, borderRadius:999, background:`${T.red}4d`, overflow:'hidden' }} aria-label={`Normalized book imbalance ${(displayImbalance * 100).toFixed(1)} percent`}>
              <div style={{
                height:'100%', width:`${Math.max(0, Math.min(100, ((displayImbalance + 1) / 2) * 100))}%`,
                background:pressureColor, transition:'width 0.35s ease',
              }} />
            </div>
            <span style={{ fontFamily:T.mono, fontSize:11, fontWeight:800, color:pressureColor, minWidth:52, textAlign:'right' }}>
              {displayImbalance > 0 ? '+' : ''}{(displayImbalance * 100).toFixed(1)}%
            </span>
          </div>
          <div style={{ marginTop:7, display:'flex', justifyContent:'space-between', gap:8, fontSize:10 }}>
            <span style={{ color:pressureColor, fontWeight:700, letterSpacing:'0.05em' }}>{side}</span>
            <span style={{ color:T.txtMuted }}>Quote age {ageLabel}</span>
          </div>
        </>
      )}
      <div style={{ marginTop:12, paddingTop:10, borderTop:`1px solid ${T.border}` }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:8, marginBottom:7 }}>
          <span style={{ color:T.txtSec, fontSize:9, fontWeight:800, letterSpacing:'0.08em' }}>5-MINUTE BOOK PRESSURE</span>
          <span style={{ color:T.txtMuted, fontSize:9 }}>{historySamples || history.length} samples · 1s intervals</span>
        </div>
        {hasTimeline ? (
          <>
            <svg viewBox="0 0 320 56" preserveAspectRatio="none" width="100%" height="62" role="img" aria-label={`Five-minute displayed-liquidity imbalance history: ${historySide.toLowerCase()}`}>
              <defs>
                <linearGradient id={`book-pressure-fill-${ticker}`} x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor={historyColor} stopOpacity="0.34" />
                  <stop offset="100%" stopColor={historyColor} stopOpacity="0.02" />
                </linearGradient>
              </defs>
              <line x1="0" y1="28" x2="320" y2="28" stroke={T.borderMid} strokeDasharray="3 3" />
              <path d={`${historyPath} L320 28 L0 28 Z`} fill={`url(#book-pressure-fill-${ticker})`} />
              <path d={historyPath} fill="none" stroke={historyColor} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <div style={{ marginTop:3, display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
              <div>
                <div style={{ color:T.txtMuted, fontSize:9, letterSpacing:'0.06em' }}>CUMULATIVE</div>
                <div style={{ marginTop:2, color:historyColor, fontFamily:T.mono, fontWeight:800, fontSize:13 }}>
                  {cumulative > 0 ? '+' : ''}{cumulative.toFixed(2)}
                </div>
              </div>
              <div style={{ textAlign:'right' }}>
                <div style={{ color:T.txtMuted, fontSize:9, letterSpacing:'0.06em' }}>AVERAGE</div>
                <div style={{ marginTop:2, color:historyColor, fontFamily:T.mono, fontWeight:800, fontSize:13 }}>
                  {average > 0 ? '+' : ''}{(average * 100).toFixed(1)}% · {historySide}
                </div>
              </div>
            </div>
          </>
        ) : (
          <div style={{ color:T.txtMuted, fontSize:10, lineHeight:1.5 }}>
            {state === 'LIVE' ? 'History is building from fresh quotes.' : 'A fresh MBP-1 quote is needed before the timeline can build.'}
          </div>
        )}
      </div>
      <div style={{ marginTop:10, color:T.txtMuted, fontSize:9.5, lineHeight:1.45 }}>
        Displayed resting liquidity, not executed trade flow · advisory only and never changes a trade decision or execution.
      </div>
    </Panel>
  );
};

// ── Edge score gauge (0–110 scale) ────────────────────────────────────────────
const EdgeGauge: React.FC<{ score: number | null; max?: number }> = ({ score, max = 110 }) => {
  const pct = score != null ? Math.min(score / max, 1) : 0;
  const col = score == null ? T.txtMuted : score >= 85 ? T.green : score >= 70 ? '#4ade80' : score >= 50 ? T.amber : T.red;
  return (
    <div style={{ textAlign:'center' }}>
      <div style={{ position:'relative', width:90, height:90, margin:'0 auto' }}>
        <svg width={90} height={90} aria-label={`Edge score ${score ?? 'unavailable'} out of ${max}`}>
          <circle cx={45} cy={45} r={36} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={8} />
          <circle cx={45} cy={45} r={36} fill="none" stroke={col} strokeWidth={8}
            strokeDasharray={`${pct * 2 * Math.PI * 36} ${2 * Math.PI * 36}`}
            strokeLinecap="round" transform="rotate(-90 45 45)"
            style={{ transition:'stroke-dasharray 0.6s ease' }} />
        </svg>
        <div style={{ position:'absolute', inset:0, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center' }}>
          <span style={{ fontSize:22, fontWeight:800, color:col, fontFamily:T.mono, lineHeight:1 }}>
            {score != null ? Math.round(score) : '—'}
          </span>
          <span style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.1em' }}>/ {max}</span>
        </div>
      </div>
      <div style={{ fontSize:9, color:T.txtMuted, marginTop:4 }}>EDGE SCORE</div>
    </div>
  );
};

// NAV_ITEMS and related constants are imported from ../lib/navItems

// ── Mobile navigation drawer ──────────────────────────────────────────────────
const MobileNavDrawer: React.FC<{
  open: boolean;
  onClose: () => void;
  systemOk: boolean;
}> = ({ open, onClose, systemOk }) => {
  const [location] = useLocation();
  const dialogRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusableSelector = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const focusFirstControl = window.requestAnimationFrame(() => {
      dialogRef.current?.querySelector<HTMLElement>(focusableSelector)?.focus();
    });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [])
        .filter(control => !control.hasAttribute('disabled'));
      if (!controls.length) {
        event.preventDefault();
        return;
      }
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFirstControl);
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', handleKeyDown);
      previousFocus?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="mb-mobile-nav-overlay"
      onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}
      aria-hidden={!open}
    >
      <aside ref={dialogRef} id="main-brain-mobile-menu" className="mb-mobile-nav-drawer" role="dialog" aria-modal="true" aria-label="Main Brain navigation">
        <div className="mb-mobile-nav-head">
          <div>
            <div style={{ fontSize: 13, color: T.txtPri, fontWeight: 800 }}>Main Brain</div>
            <div style={{ fontSize: 9, color: T.cyan, letterSpacing: '0.1em', marginTop: 2 }}>OPERATOR CONSOLE</div>
          </div>
          <button className="mb-mobile-nav-close" onClick={onClose} autoFocus aria-label="Close navigation menu">×</button>
        </div>

        <div className="mb-mobile-nav-section-label">Workspace</div>
        <nav aria-label="Main Brain sections" className="mb-mobile-nav-items">
          {NAV_ITEMS.map(item => {
            const isActive = item.id === 'main-brain'
              ? location === '/' || location === item.path
              : location === item.path;
            return (
              <Link
                key={item.id}
                to={item.path}
                onClick={onClose}
                className="mb-mobile-nav-item"
                data-testid={`link-${item.id}`}
                aria-current={isActive ? 'page' : undefined}
                style={{
                  background: isActive ? `${T.cyan}14` : 'transparent',
                  borderColor: isActive ? `${T.cyan}44` : T.border,
                  color: isActive ? T.cyan : T.txtSec,
                }}
              >
                <span aria-hidden="true" style={{ fontSize: 18, width: 24, textAlign: 'center' }}>{item.icon}</span>
                <span>{item.label}</span>
                {isActive && <span style={{ marginLeft: 'auto', fontSize: 12 }}>●</span>}
              </Link>
            );
          })}
        </nav>

        <div className="mb-mobile-nav-section-label">Other destinations</div>
        <div className="mb-mobile-nav-items">
          <a href="/dashboard" onClick={onClose} className="mb-mobile-nav-item"><span aria-hidden="true">▦</span><span>Dashboard</span></a>
          <a href="/cockpit" onClick={onClose} className="mb-mobile-nav-item"><span aria-hidden="true">◈</span><span>Cockpit</span></a>
          <a href="/manual" onClick={onClose} className="mb-mobile-nav-item"><span aria-hidden="true">📖</span><span>Operator Manual</span></a>
          <a href="https://vwap-pullback-indicator.replit.app" onClick={onClose} target="_blank" rel="noopener noreferrer" className="mb-mobile-nav-item">
            <span aria-hidden="true">↗</span><span>VWAP Pullback</span>
          </a>
        </div>

        <div className="mb-mobile-nav-system">
          {statusDot(systemOk)}
          <span>{systemOk ? 'System ready' : 'System status pending'}</span>
        </div>
      </aside>
    </div>
  );
};

// ── Sidenav ───────────────────────────────────────────────────────────────────
const SideNav: React.FC<{ systemOk: boolean }> = ({ systemOk }) => {
  const [location] = useLocation();
  return (
    <nav className="mb-desktop-sidenav" aria-label="Main navigation" style={{
      width: 58, flexShrink: 0, background: '#040d1e', borderRight: `1px solid ${T.border}`,
      display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '12px 0',
      position: 'sticky', top: 0, height: '100vh', overflowY: 'auto',
    }}>
      {/* Logo */}
      <div style={{ marginBottom: 20, textAlign:'center' }}>
        <div style={{ width:36, height:36, borderRadius:10, background:'linear-gradient(135deg,#1e3a5f,#0f2040)',
          border:`1px solid ${T.cyan}44`, display:'flex', alignItems:'center', justifyContent:'center',
          fontSize:16, boxShadow:`0 0 12px ${T.cyan}22` }}>
          🧠
        </div>
        <div style={{ fontSize:7, color:T.cyan, fontWeight:700, letterSpacing:'0.1em', marginTop:4, lineHeight:1 }}>V1</div>
      </div>

      {NAV_ITEMS.map(item => {
        const isActive = item.id === 'main-brain'
          ? location === '/' || location === item.path
          : location === item.path;
        const col = isActive ? T.cyan : T.txtSec;
        return (
          <Link key={item.id} to={item.path} style={{ textDecoration:'none', marginBottom:4 }}
            data-testid={`link-${item.id}`}
            aria-label={item.label} aria-current={isActive ? 'page' : undefined}>
            <div title={item.label} style={{
              display:'flex', flexDirection:'column', alignItems:'center', gap:3,
              padding:'8px 6px', borderRadius:8, width:46, cursor:'pointer',
              background: isActive ? `${T.cyan}14` : 'transparent',
              border: isActive ? `1px solid ${T.cyan}33` : '1px solid transparent',
              opacity: 1,
              transition:'all 0.15s',
            }}>
              <span style={{ fontSize:15, lineHeight:1, color:col }}>{item.icon}</span>
              <span style={{ fontSize:7.5, fontWeight:700, letterSpacing:'0.06em', color:col, textAlign:'center', lineHeight:1.2 }}>
                {item.label.toUpperCase()}
              </span>
            </div>
          </Link>
        );
      })}

      {/* Secondary: manual + legacy dashboard links */}
      <div style={{ marginTop:'auto', display:'flex', flexDirection:'column', alignItems:'center', gap:4, width:'100%' }}>
        <div style={{ width:32, height:1, background:T.border, marginBottom:4 }} />

        {/* Operator Manual */}
        <a href="/manual" title="Operator Manual" style={{ textDecoration:'none' }}>
          <div style={{
            display:'flex', flexDirection:'column', alignItems:'center', gap:3,
            padding:'6px 4px', borderRadius:6, width:46, cursor:'pointer',
            border:'1px solid transparent', opacity:0.6,
            transition:'opacity 0.15s',
          }}
            onMouseEnter={e => (e.currentTarget as HTMLDivElement).style.opacity='1'}
            onMouseLeave={e => (e.currentTarget as HTMLDivElement).style.opacity='0.6'}
          >
            <span style={{ fontSize:13, lineHeight:1, color:T.txtMuted }}>📖</span>
            <span style={{ fontSize:6.5, fontWeight:700, letterSpacing:'0.06em', color:T.txtMuted, textAlign:'center', lineHeight:1.2 }}>
              MANUAL
            </span>
          </div>
        </a>

        <a href="/dashboard" title="System Dashboard (legacy)" style={{ textDecoration:'none' }}>
          <div style={{
            display:'flex', flexDirection:'column', alignItems:'center', gap:3,
            padding:'6px 4px', borderRadius:6, width:46, cursor:'pointer',
            border:'1px solid transparent', opacity:0.55,
            transition:'opacity 0.15s',
          }}
            onMouseEnter={e => (e.currentTarget as HTMLDivElement).style.opacity='1'}
            onMouseLeave={e => (e.currentTarget as HTMLDivElement).style.opacity='0.55'}
          >
            <span style={{ fontSize:11, lineHeight:1, color:T.txtMuted }}>⚙</span>
            <span style={{ fontSize:6.5, fontWeight:700, letterSpacing:'0.06em', color:T.txtMuted, textAlign:'center', lineHeight:1.2 }}>
              SYSTEM{'\n'}DASHBOARD
            </span>
          </div>
        </a>

        {/* System health dot */}
        <div style={{ paddingTop:8, display:'flex', flexDirection:'column', alignItems:'center', gap:4 }}>
          {statusDot(systemOk)}
          <span style={{ fontSize:7, color:T.txtMuted, letterSpacing:'0.06em' }}>SYS</span>
        </div>
      </div>
    </nav>
  );
};

// ── Market State Strip ────────────────────────────────────────────────────────
// ── Operator risk state classifier ──────────────────────────────────────────
// Derives a concise display state from the prop_firm block that
// build_main_brain_payload() now includes.  Reads only existing backend-
// calculated fields — never recalculates any trading threshold.
//
// Display labels match the operator states documented in the spec:
//   UNAVAILABLE   — payload key absent (backend pre-fix, or connection issue)
//   PROT OFF      — prop guard explicitly disabled
//   NO ACCOUNT    — protection ON but no active account; live orders blocked
//   DAILY LIMIT   — daily_loss_remaining ≤ 0 (limit hit or exceeded)
//   DRAWDOWN      — drawdown_remaining ≤ 0 (floor breached)
//   CAUTION       — < 20 % of daily budget remaining OR config warnings
//   NORMAL        — all checks clear
function computeOperatorRisk(p: Record<string, unknown>): {
  label:  string;
  detail: string | null;
  color:  string;
} {
  const prop = (p.prop_firm ?? p.risk_ops ?? null) as Record<string, unknown> | null;

  // Key absent — backend hasn't provided data yet (pre-fix deployment or error)
  if (prop === null || typeof prop !== 'object' || Object.keys(prop).length === 0) {
    return { label: 'UNAVAILABLE', detail: null, color: T.txtMuted };
  }

  const enabled = prop.enabled === true;
  if (!enabled) {
    return { label: 'PROT OFF', detail: null, color: T.txtMuted };
  }

  const account = (prop.account ?? null) as Record<string, unknown> | null;
  if (!account || typeof account !== 'object') {
    return { label: 'NO ACCOUNT', detail: 'Live orders blocked', color: T.red };
  }

  // Read backend-computed metrics — no recalculation here
  const metrics = (prop.metrics ?? {}) as Record<string, unknown>;
  const phase2  = Array.isArray(prop.phase2) ? (prop.phase2 as string[]) : [];
  const dll     = safeNum(metrics.daily_loss_limit);
  const dlr     = safeNum(metrics.daily_loss_remaining);
  const ddr     = safeNum(metrics.drawdown_remaining);

  // Hard blocks — budget exhausted (backend computed)
  if (dll !== null && dlr !== null && dlr <= 0) {
    return { label: 'DAILY LIMIT', detail: 'Daily limit reached', color: T.red };
  }
  if (ddr !== null && ddr <= 0) {
    return { label: 'DRAWDOWN', detail: 'Floor breached', color: T.red };
  }

  // Caution zone: < 20 % of daily budget remaining (display-only threshold)
  if (dll !== null && dlr !== null && dll > 0 && dlr < dll * 0.20) {
    return { label: 'CAUTION', detail: `$${Math.round(dlr)} remaining`, color: T.amber };
  }

  // Prop configuration warnings (e.g. balance missing, intraday drawdown display-only)
  if (phase2.length > 0) {
    const msg = phase2[0];
    const truncated = msg.length > 38 ? msg.slice(0, 38) + '…' : msg;
    return { label: 'CAUTION', detail: truncated, color: T.amber };
  }

  // All clear — optionally show remaining budget as reassurance
  const detail = (dll !== null && dlr !== null)
    ? `$${Math.round(dlr)} of $${Math.round(dll)} remaining`
    : null;
  return { label: 'NORMAL', detail, color: T.green };
}

const MarketStrip: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const mkt   = (p.market       ?? {}) as Record<string, unknown>;
  const ms    = (p.market_state  ?? {}) as Record<string, unknown>;
  const sys   = (p.system_status ?? {}) as Record<string, unknown>;
  const risk  = computeOperatorRisk(p);

  // Cards support an optional `detail` sub-label shown below the value.
  type Card = { label: string; value: string; color: string; detail?: string | null };
  const cards: Card[] = [
    { label:'MARKET',      value: safeStr(mkt.session_status, 'UNKNOWN'),  color: /open/i.test(String(mkt.session_status)) ? T.green : T.amber },
    { label:'INSTRUMENT',  value: safeStr(mkt.instrument, '—'),             color: T.cyan },
    { label:'MODE',        value: safeStr(mkt.trading_mode, '—'),           color: T.blue },
    { label:'EXECUTION',   value: safeStr(mkt.execution_mode, '—'),         color: T.amber },
    { label:'REGIME',      value: safeStr(ms.regime, '—'),                  color: T.txtSec },
    { label:'RISK STATE',  value: risk.label,                               color: risk.color, detail: risk.detail },
    { label:'DATABENTO',   value: sys.databento_ready ? 'LIVE' : 'OFFLINE', color: sys.databento_ready ? T.green : T.red },
  ];

  return (
    <div className="mb-market-strip-items" style={{ display:'flex', gap:8, overflowX:'auto', paddingBottom:2 }} role="region" aria-label="Market state strip">
      {cards.map(c => (
        <div key={c.label} style={{
          flexShrink:0, background:T.panel, border:`1px solid ${T.border}`, borderRadius:8,
          padding:'8px 12px', minWidth:90, textAlign:'center',
        }}>
          <div style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.1em', marginBottom:4 }}>{c.label}</div>
          <div style={{ fontSize:12.5, fontWeight:700, color:c.color, fontFamily:T.mono, whiteSpace:'nowrap' }}>{c.value}</div>
          {c.detail && (
            <div style={{ fontSize:9, color:T.txtMuted, marginTop:3, fontFamily:T.mono,
              whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', maxWidth:120 }}>
              {c.detail}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

// ── Timeline event detail formatter (Part 2) ─────────────────────────────────
// Converts any timeline `details` value to a human-readable string.
// Priority: known semantic fields → transition pattern → safe JSON fallback.
// NEVER returns `[object Object]`.
export function fmtEventDetail(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) {
    const parts = (v as unknown[])
      .filter(x => x != null)
      .map(x => typeof x === 'object' ? fmtEventDetail(x) : String(x))
      .filter(Boolean);
    return parts.join(' · ') || '';
  }
  if (typeof v === 'object' && v !== null) {
    const o = v as Record<string, unknown>;
    // Preferred semantic fields — highest priority first
    if (typeof o.summary     === 'string' && o.summary)     return o.summary;
    if (typeof o.message     === 'string' && o.message)     return o.message;
    if (typeof o.reason      === 'string' && o.reason)      return o.reason;
    if (typeof o.description === 'string' && o.description) return o.description;
    if (typeof o.label       === 'string' && o.label)       return o.label;
    // Transition-style: from/to or prev/new
    if (o.from        != null && o.to         != null) return `${o.from} → ${o.to}`;
    if (o.prev_status != null && o.new_status != null) return `${o.prev_status} → ${o.new_status}`;
    if (o.previous_state != null && o.new_state != null) return `${o.previous_state} → ${o.new_state}`;
    // Single-field scalar extractions
    if (typeof o.direction  === 'string' && o.direction)  return o.direction;
    if (typeof o.status     === 'string' && o.status)     return o.status;
    if (typeof o.event_type === 'string' && o.event_type) return o.event_type;
    // Safe compact JSON fallback — scalars only, max 6 keys, truncated to 120 chars
    try {
      const keys = Object.keys(o)
        .filter(k => {
          const val = o[k];
          return val != null && typeof val !== 'object' && !Array.isArray(val);
        })
        .slice(0, 6);
      if (keys.length === 0) return '';
      const subset: Record<string, unknown> = {};
      for (const k of keys) subset[k] = o[k];
      const json = JSON.stringify(subset);
      return json.length > 120 ? json.slice(0, 120) + '…' : json;
    } catch {
      return '[complex object]';
    }
  }
  return String(v);
}

// ── Thesis Panel ──────────────────────────────────────────────────────────────
// Displays the Left Brain Dynamic Thesis with explicit states:
//   AVAILABLE     — fresh, computed thesis (direction may legitimately be NEUTRAL)
//   STALE         — thesis exists but older than 10 min; shows last-known direction
//   COLLECTING_DATA — fewer than 5 bar-close observations; shows progress
//   NO_DATA       — thesis never computed (no Databento bar-close yet)
//   ERROR         — adapter exception; shows unavailable
//
// NEVER shows "NEUTRAL" as a silent fallback — NEUTRAL is only shown when the
// diagnosis.status is AVAILABLE and the market is genuinely contested.
// ── FVG Scanner Panel (Step A — SHADOW/DISPLAY-ONLY) ─────────────────────────
// Shows active Fair Value Gap / Inverse FVG zones per instrument.
// ── FVG/IFVG Scanner + Sequence Panel ─────────────────────────────────────────
// Shows FVG zone summary (Step A) and live sequence state machine (Step B).
// SHADOW/DISPLAY-ONLY. NEVER touches gate, scoring, sizing, or execution.

const SEQ_STATE_COLOR: Record<string, string> = {
  RETURN_PENDING:   '#64748b',
  TOUCHED:          '#f59e0b',
  HOLD_PENDING:     '#f59e0b',
  HOLD_CONFIRMED:   '#10b981',
  STRUCTURE_PENDING:'#6366f1',
  MOMENTUM_PENDING: '#8b5cf6',
  ENTRY_WINDOW:     '#06b6d4',
  SHADOW_READY:     '#22c55e',
  INVERTED:         '#ec4899',
  RETEST_PENDING:   '#f59e0b',
  RETESTED:         '#06b6d4',
  EXPIRED:          '#374151',
  INVALIDATED:      '#ef4444',
};

const SEQ_STATE_LABEL: Record<string, string> = {
  RETURN_PENDING:   'Waiting for price to return',
  TOUCHED:          'Zone touched',
  HOLD_PENDING:     'Hold forming…',
  HOLD_CONFIRMED:   'Hold confirmed ✓',
  STRUCTURE_PENDING:'Awaiting structure signal',
  MOMENTUM_PENDING: 'Awaiting momentum',
  ENTRY_WINDOW:     'Entry window open',
  SHADOW_READY:     '✦ SHADOW READY',
  INVERTED:         'FVG inverted (IFVG)',
  RETEST_PENDING:   'Awaiting IFVG retest',
  RETESTED:         'Retest confirmed ✓',
  EXPIRED:          'Expired',
  INVALIDATED:      'Invalidated',
};

const SEQUENCE_STEPS_CONT = [
  'RETURN_PENDING', 'TOUCHED', 'HOLD_CONFIRMED',
  'STRUCTURE_PENDING', 'MOMENTUM_PENDING', 'ENTRY_WINDOW', 'SHADOW_READY',
];
const SEQUENCE_STEPS_REV = [
  'INVERTED', 'RETEST_PENDING', 'RETESTED', 'HOLD_CONFIRMED',
  'STRUCTURE_PENDING', 'MOMENTUM_PENDING', 'ENTRY_WINDOW', 'SHADOW_READY',
];

const EW_COLOR: Record<string, string> = {
  ENTRY_AVAILABLE: '#22c55e',
  ENTRY_LATE:      '#f59e0b',
  ENTRY_CHASING:   '#ef4444',
  ENTRY_EXPIRED:   '#6b7280',
};

type SeqRec = Record<string, unknown>;

function SeqCard({ seq }: { seq: SeqRec }) {
  const dir      = seq['direction'] as string;
  const family   = seq['setup_family'] as string;
  const state    = seq['current_state'] as string;
  const isPrimary = !!seq['is_primary'];
  const isShadow  = state === 'SHADOW_READY';
  const isReversal= family === 'IFVG_REVERSAL';
  const dirColor  = dir === 'BULLISH' ? '#10b981' : '#ef4444';
  const dirArrow  = dir === 'BULLISH' ? '▲' : '▼';
  const sColor    = SEQ_STATE_COLOR[state] ?? '#6b7280';
  const steps     = isReversal ? SEQUENCE_STEPS_REV : SEQUENCE_STEPS_CONT;
  const stateIdx  = steps.indexOf(state);

  const lower     = typeof seq['zone_lower']  === 'number' ? (seq['zone_lower']  as number).toFixed(2) : '—';
  const upper     = typeof seq['zone_upper']  === 'number' ? (seq['zone_upper']  as number).toFixed(2) : '—';
  const nextEvt   = seq['next_required_event'] as string | undefined;
  const momCount  = seq['momentum_checks_passed'] as number | undefined;
  const momTotal  = seq['momentum_checks_total']  as number | undefined;
  const ewLabel   = seq['entry_window_label']  as string | undefined;
  const ew        = seq['entry_window']        as SeqRec | undefined;

  const exWhy    = (seq['explain_why'] ?? {}) as SeqRec;
  const plan     = seq['shadow_plan'] as SeqRec | undefined;

  const [showDetail, setShowDetail] = React.useState(false);

  return (
    <div style={{
      borderRadius: 7, padding: '10px 12px',
      background: isShadow ? 'rgba(34,197,94,0.06)' : 'rgba(255,255,255,0.04)',
      border: `1px solid ${sColor}${isPrimary ? '80' : '30'}`,
      marginBottom: 8,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <span style={{ fontSize: 10, fontWeight: 700, color: dirColor }}>{dirArrow}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color: '#c4cfe4' }}>
          {dir === 'BULLISH' ? 'LONG' : 'SHORT'}
        </span>
        {isReversal && (
          <span style={{ fontSize: 9, color: '#ec4899', fontWeight: 700, letterSpacing: '0.05em' }}>
            IFVG
          </span>
        )}
        {isPrimary && (
          <span style={{ fontSize: 9, color: '#6366f1', fontWeight: 700, letterSpacing: '0.04em' }}>
            PRIMARY
          </span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 10, color: sColor, fontWeight: 700 }}>
          {isShadow ? '✦ SHADOW READY' : (SEQ_STATE_LABEL[state] ?? state)}
        </span>
      </div>

      {/* Zone bounds */}
      <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 6, fontVariantNumeric: 'tabular-nums' }}>
        Zone {lower} – {upper}
      </div>

      {/* Step progress bar */}
      <div style={{ display: 'flex', gap: 3, marginBottom: 8, flexWrap: 'wrap' }}>
        {steps.map((step, i) => {
          const done    = stateIdx > i;
          const current = stateIdx === i;
          const sc      = SEQ_STATE_COLOR[step] ?? '#64748b';
          return (
            <div key={step} title={SEQ_STATE_LABEL[step] ?? step} style={{
              height: 5, flex: 1, minWidth: 12,
              borderRadius: 3,
              background: done    ? sc :
                          current ? `${sc}cc` :
                          'rgba(255,255,255,0.08)',
              transition: 'background 0.3s',
            }} />
          );
        })}
      </div>

      {/* Momentum count */}
      {momCount !== undefined && momTotal !== undefined && (
        <div style={{ fontSize: 10, color: '#64748b', marginBottom: 4 }}>
          Momentum: {momCount}/{momTotal} checks passed
        </div>
      )}

      {/* Entry window */}
      {ewLabel && (
        <div style={{ fontSize: 10, color: EW_COLOR[ewLabel] ?? '#64748b', marginBottom: 4, fontWeight: 600 }}>
          Entry: {ewLabel.replace('ENTRY_', '')}
          {ew && typeof ew['seconds_open'] === 'number' && (
            <span style={{ fontWeight: 400, color: '#475569', marginLeft: 6 }}>
              {Math.round(ew['seconds_open'] as number)}s open
            </span>
          )}
        </div>
      )}

      {/* Next required event */}
      {nextEvt && !isShadow && (
        <div style={{ fontSize: 10, color: '#475569', marginBottom: 4 }}>
          Next: <span style={{ color: '#94a3b8' }}>{nextEvt}</span>
        </div>
      )}

      {/* Shadow plan snippet */}
      {isShadow && plan && (
        <div style={{ marginTop: 6, padding: '6px 8px', borderRadius: 5,
          background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)',
          fontSize: 10, color: '#86efac' }}>
          <div style={{ fontWeight: 700, marginBottom: 3, color: '#22c55e' }}>Shadow Plan</div>
          <div style={{ fontVariantNumeric: 'tabular-nums', color: '#94a3b8' }}>
            Entry {typeof plan['entry'] === 'number' ? (plan['entry'] as number).toFixed(2) : '—'}
            {' · Stop '}  {typeof plan['stop']   === 'number' ? (plan['stop']   as number).toFixed(2) : '—'}
            {' · 1R '}    {typeof plan['target1'] === 'number' ? (plan['target1'] as number).toFixed(2) : '—'}
          </div>
          <div style={{ marginTop: 3, color: '#6b7280' }}>
            Shadow-only · production_ready=false
          </div>
        </div>
      )}

      {/* Explain-why toggle */}
      <div
        style={{ marginTop: 6, fontSize: 10, color: '#475569', cursor: 'pointer',
          textDecoration: 'underline', textUnderlineOffset: 2 }}
        onClick={() => setShowDetail(v => !v)}
      >
        {showDetail ? '▲ hide analysis' : '▼ why?'}
      </div>
      {showDetail && (
        <div style={{ marginTop: 6, fontSize: 10, color: '#64748b',
          padding: '6px 8px', borderRadius: 5,
          background: 'rgba(0,0,0,0.25)', border: '1px solid rgba(255,255,255,0.06)' }}>
          {!!(exWhy['why_ready'])  && <div style={{ color: '#22c55e', marginBottom: 3 }}>✓ {String(exWhy['why_ready'])}</div>}
          {!!(exWhy['why_not_ready']) && <div style={{ color: '#f59e0b', marginBottom: 3 }}>⊘ {String(exWhy['why_not_ready'])}</div>}
          {!!(exWhy['why_exists']) && <div style={{ color: '#64748b' }}>ℹ {String(exWhy['why_exists'])}</div>}
        </div>
      )}
    </div>
  );
}

// Data from p.fvg_summary (Step A) + p.fvg_sequences (Step B).
// Panel hidden when key absent (engine disabled). NEVER touches gate or execution.
const FVGScannerPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  if (!('fvg_summary' in p)) return null;
  const summary = (p.fvg_summary ?? {}) as Record<string, unknown>;
  if (!summary['enabled']) return null;

  // Step B sequence data (optional — falls back gracefully when absent)
  const seqData = (p.fvg_sequences ?? {}) as Record<string, unknown>;

  const STATUS_COLOR: Record<string, string> = {
    ACTIVE:    '#6366f1',
    TOUCHED:   '#f59e0b',
    HOLDING:   '#10b981',
    MITIGATED: '#8b5cf6',
    INVERTED:  '#ec4899',
    RETESTED:  '#06b6d4',
    FAILED:    '#ef4444',
    EXPIRED:   '#6b7280',
  };
  const STATUS_LABEL: Record<string, string> = {
    ACTIVE:    'Active',
    TOUCHED:   'Touched',
    HOLDING:   'Holding ✓',
    MITIGATED: '50% Fill',
    INVERTED:  'Inverted (IFVG)',
    RETESTED:  'Retested',
    FAILED:    'Failed',
    EXPIRED:   'Expired',
  };

  const instruments = Object.keys(summary).filter(k => k !== 'enabled' && k !== 'error');
  if (!instruments.length) return (
    <div className="mod" style={{ padding: '14px 16px', color: '#94a3b8', fontSize: 13 }}>
      <strong style={{ color: '#c4cfe4', fontSize: 12, letterSpacing: '0.06em', textTransform: 'uppercase' }}>FVG Scanner</strong>
      <div style={{ marginTop: 8 }}>No FVG zones detected yet — waiting for bar data</div>
    </div>
  );

  return (
    <div className="mod" id="fvg-scanner-panel">
      <div className="mod-h" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 11, opacity: 0.5 }}>◆</span>
        <span>FVG / IFVG Scanner + Sequences</span>
        <span style={{ marginLeft: 'auto', fontSize: 10, color: '#6366f1', letterSpacing: '0.05em' }}>SHADOW</span>
      </div>
      <div style={{ padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        {instruments.map(inst => {
          const data      = summary[inst] as Record<string, unknown>;
          const allActive = (data['all_active'] as unknown[]) ?? [];
          const fvgCount  = (data['active_fvg_count'] as number) ?? 0;
          const ifvgCount = (data['active_ifvg_count'] as number) ?? 0;
          const bestBull  = data['best_bullish'] as Record<string, unknown> | null;
          const bestBear  = data['best_bearish'] as Record<string, unknown> | null;

          // Step B: sequences for this instrument
          const instSeqData = (seqData[inst] ?? {}) as Record<string, unknown>;
          const primarySeqs = (instSeqData['primary_sequences'] as SeqRec[]) ?? [];
          const allSeqs     = (instSeqData['all_sequences']     as SeqRec[]) ?? [];
          const nonPrimary  = allSeqs.filter(s => !s['is_primary']);
          const hasShadowReady = primarySeqs.some(s => s['current_state'] === 'SHADOW_READY');

          const renderZoneCard = (zone: Record<string, unknown> | null, label: string) => {
            if (!zone) return (
              <div style={{ flex: 1, padding: '8px 10px', borderRadius: 6,
                background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)',
                color: '#475569', fontSize: 11 }}>
                <div style={{ fontSize: 10, fontWeight: 600, marginBottom: 4, letterSpacing: '0.06em',
                  textTransform: 'uppercase', color: '#475569' }}>{label}</div>
                No zone
              </div>
            );
            const dir   = (zone['ifvg_direction'] || zone['direction']) as string;
            const lower = typeof zone['lower'] === 'number' ? zone['lower'].toFixed(2) : '—';
            const upper = typeof zone['upper'] === 'number' ? zone['upper'].toFixed(2) : '—';
            const mid   = typeof zone['midpoint'] === 'number' ? zone['midpoint'].toFixed(2) : '—';
            const status= (zone['status'] as string) ?? 'ACTIVE';
            const age   = (zone['bar_age'] as number) ?? 0;
            const score = typeof zone['rank_score'] === 'number' ? zone['rank_score'].toFixed(0) : '—';
            const isIfvg = !!zone['ifvg_direction'];
            const sColor = STATUS_COLOR[status] ?? '#6b7280';
            const dirColor = dir === 'BULLISH' ? '#10b981' : '#ef4444';
            const dirArrow = dir === 'BULLISH' ? '▲' : '▼';
            return (
              <div style={{ flex: 1, padding: '8px 10px', borderRadius: 6,
                background: 'rgba(255,255,255,0.04)',
                border: `1px solid ${sColor}40` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
                    textTransform: 'uppercase', color: '#94a3b8' }}>{label}</span>
                  {isIfvg && <span style={{ fontSize: 9, color: '#ec4899', fontWeight: 700,
                    letterSpacing: '0.05em' }}>IFVG</span>}
                  <span style={{ marginLeft: 'auto', fontSize: 10, color: dirColor,
                    fontWeight: 700 }}>{dirArrow} {dir === 'BULLISH' ? 'LONG' : 'SHORT'}</span>
                </div>
                <div style={{ fontSize: 11, color: '#c4cfe4', marginBottom: 4, fontVariantNumeric: 'tabular-nums' }}>
                  {lower} – {upper}
                  <span style={{ color: '#64748b', marginLeft: 4 }}>mid {mid}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 10, color: sColor, fontWeight: 600 }}>
                    ● {STATUS_LABEL[status] ?? status}
                  </span>
                  <span style={{ fontSize: 10, color: '#475569', marginLeft: 'auto' }}>
                    {age}b · {score}pts
                  </span>
                </div>
              </div>
            );
          };

          return (
            <div key={inst}>
              {/* Instrument header */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#c4cfe4', letterSpacing: '0.04em' }}>
                  {inst}
                </span>
                <span style={{ fontSize: 10, color: '#64748b' }}>
                  {fvgCount} FVG{fvgCount !== 1 ? 's' : ''}
                  {ifvgCount > 0 && ` · ${ifvgCount} IFVG`}
                </span>
                {!fvgCount && !ifvgCount && (
                  <span style={{ fontSize: 10, color: '#475569', fontStyle: 'italic' }}>no active zones</span>
                )}
                {hasShadowReady && (
                  <span style={{ marginLeft: 'auto', fontSize: 10, color: '#22c55e', fontWeight: 700,
                    letterSpacing: '0.04em' }}>✦ SHADOW READY</span>
                )}
              </div>

              {/* Step A: zone cards */}
              <div style={{ display: 'flex', gap: 8, marginBottom: primarySeqs.length ? 10 : 0 }}>
                {renderZoneCard(bestBull, 'Best Long')}
                {renderZoneCard(bestBear, 'Best Short')}
              </div>
              {allActive.length > 2 && !primarySeqs.length && (
                <div style={{ marginTop: 6, fontSize: 10, color: '#475569', paddingLeft: 2 }}>
                  +{allActive.length - 2} more active zone{allActive.length - 2 !== 1 ? 's' : ''} by rank
                </div>
              )}

              {/* Step B: primary sequence cards */}
              {primarySeqs.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 10, color: '#475569', fontWeight: 600,
                    letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 6 }}>
                    Active Sequences
                  </div>
                  {primarySeqs.map((seq, i) => (
                    <SeqCard key={(seq['sequence_id'] as string) ?? i} seq={seq} />
                  ))}
                  {nonPrimary.length > 0 && (
                    <div style={{ fontSize: 10, color: '#374151', marginTop: 2 }}>
                      +{nonPrimary.length} secondary sequence{nonPrimary.length !== 1 ? 's' : ''} tracked
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ── Volatility Intelligence Panel (Phase VI-E — DISPLAY-ONLY) ────────────────
// Shows VIX regime, direction, risk tone, and per-instrument context.
// Data from p.volatility_intelligence (injected at full_analysis seam).
// Flag-gated: panel is hidden when enabled=false or key absent.
const VolatilityIntelligencePanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  // Show whenever the key is present (module enabled in backend); hide only when key is absent.
  if (!('volatility_intelligence' in p)) return null;
  const vi = (p.volatility_intelligence ?? {}) as Record<string, unknown>;

  const vix        = (vi.vix ?? {}) as Record<string, unknown>;
  const price      = safeNum(vix.price);
  const hasData    = Boolean(vi.enabled) && price != null;
  const changePct  = safeNum(vix.change_pct);
  const regime     = safeStr(vi.regime, 'UNKNOWN');
  const direction  = safeStr(vi.direction, 'UNKNOWN');
  const velocity   = safeStr(vi.velocity, 'UNKNOWN');
  const riskTone   = safeStr(vi.risk_tone, 'UNKNOWN');
  const eqContext  = safeStr(vi.equity_context, 'UNKNOWN');
  const confidence = safeNum(vi.confidence) ?? 0;
  const dataStatus = safeStr(vi.data_status, 'UNAVAILABLE');
  const warnings   = (vi.warnings ?? []) as string[];
  const reasons    = (vi.reasons  ?? []) as string[];
  const instCtx    = (vi.instrument_context ?? {}) as Record<string, Record<string, unknown>>;

  const regimeColor: Record<string, string> = {
    CALM:     '#34d399',
    NORMAL:   '#60a5fa',
    ELEVATED: '#fbbf24',
    EXTREME:  '#ef4444',
    UNKNOWN:  '#6b7280',
  };
  const toneColor: Record<string, string> = {
    RISK_ON:            '#34d399',
    NEUTRAL:            '#60a5fa',
    RISK_OFF_PRESSURE:  '#fbbf24',
    RISK_OFF_SHOCK:     '#ef4444',
    UNKNOWN:            '#6b7280',
  };
  const dirIcon: Record<string, string> = {
    RISING: '↑', FALLING: '↓', FLAT: '→', UNKNOWN: '?',
  };
  const statusBg = dataStatus === 'ERROR' || dataStatus === 'UNAVAILABLE'
    ? 'rgba(239,68,68,0.08)' : 'rgba(255,255,255,0.03)';

  const activeInst = safeStr(p.instrument, '') || safeStr(p.ticker, '');
  const thisInst   = instCtx[activeInst] as Record<string, unknown> | undefined;
  const relevance  = thisInst ? safeStr(thisInst.relevance, '') : '';
  const instNote   = thisInst ? safeStr(thisInst.note, '') : '';
  const instConf   = thisInst ? (safeNum(thisInst.confidence) ?? 0) : 0;

  return (
    <div className="mod" id="vol-intelligence-panel" style={{ background: statusBg }}>
      <div className="mod-h" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 11, color: '#9ca3af', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
          VIX · Volatility Intelligence
        </span>
        <span style={{
          marginLeft: 'auto', fontSize: 10, padding: '1px 6px',
          borderRadius: 4, fontWeight: 700, letterSpacing: '0.05em',
          background: `${regimeColor[regime] ?? '#6b7280'}22`,
          color: regimeColor[regime] ?? '#6b7280',
          border: `1px solid ${regimeColor[regime] ?? '#6b7280'}44`,
        }}>
          {regime}
        </span>
        <span style={{
          fontSize: 10, padding: '1px 6px', borderRadius: 4,
          color: '#6b7280', border: '1px solid rgba(255,255,255,0.08)',
        }}>
          {dataStatus}
        </span>
      </div>

      {/* No-data state — module active but no price yet */}
      {!hasData && (
        <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ fontSize: 11, color: '#6b7280', fontFamily: 'monospace' }}>
            {dataStatus === 'ERROR'
              ? '⚠ VIX fetch failed — market may be closed or API limit reached'
              : '📡 VIX module active — waiting for first fetch'}
          </div>
          <div style={{ fontSize: 10, color: '#4b5563', marginTop: 3 }}>
            {dataStatus === 'ERROR'
              ? 'Data will resume automatically when US session opens (9:30 AM ET)'
              : 'Alpha Vantage fetches VIX every hour during US session'}
          </div>
        </div>
      )}

      {/* Main VIX reading */}
      {hasData && <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
        <div>
          <div style={{ fontSize: 24, fontWeight: 800, color: regimeColor[regime] ?? '#9ca3af', lineHeight: 1 }}>
            {price != null ? price.toFixed(2) : '—'}
          </div>
          <div style={{ fontSize: 10, color: '#6b7280', marginTop: 2 }}>VIX · delayed</div>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <span style={{
              fontSize: 11, padding: '2px 7px', borderRadius: 4, fontWeight: 600,
              background: 'rgba(255,255,255,0.05)', color: '#d1d5db',
            }}>
              {dirIcon[direction] ?? '?'} {direction}
              {velocity !== 'UNKNOWN' ? ` · ${velocity}` : ''}
            </span>
            <span style={{
              fontSize: 11, padding: '2px 7px', borderRadius: 4, fontWeight: 600,
              background: `${toneColor[riskTone] ?? '#6b7280'}18`,
              color: toneColor[riskTone] ?? '#6b7280',
            }}>
              {riskTone.replace(/_/g, ' ')}
            </span>
            {changePct != null && (
              <span style={{
                fontSize: 11, padding: '2px 7px', borderRadius: 4,
                color: changePct > 0 ? '#f87171' : '#34d399',
                background: changePct > 0 ? 'rgba(248,113,113,0.1)' : 'rgba(52,211,153,0.1)',
                fontWeight: 600,
              }}>
                {changePct > 0 ? '+' : ''}{changePct.toFixed(2)}%
              </span>
            )}
          </div>
          {/* Equity context */}
          {eqContext !== 'UNKNOWN' && (
            <div style={{ fontSize: 10, color: '#6b7280', marginTop: 4 }}>
              {eqContext.replace(/_/g, ' ')}
            </div>
          )}
        </div>
        {/* Confidence */}
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: confidence >= 70 ? '#60a5fa' : '#6b7280' }}>
            {confidence}%
          </div>
          <div style={{ fontSize: 9, color: '#6b7280' }}>conf</div>
        </div>
      </div>}

      {/* Per-instrument context for the active instrument */}
      {hasData && thisInst && relevance && (
        <div style={{
          marginTop: 8, padding: '5px 8px', borderRadius: 5,
          background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)',
          fontSize: 10, color: '#9ca3af',
        }}>
          <span style={{ fontWeight: 700, color: '#d1d5db' }}>{activeInst}</span>
          {' '}{relevance.replace(/_/g, ' ')} relevance
          {instConf > 0 ? ` · ${instConf}% conf` : ''}
          {instNote ? <div style={{ marginTop: 3, fontStyle: 'italic', color: '#6b7280' }}>{instNote}</div> : null}
        </div>
      )}

      {/* Reasons */}
      {reasons.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {reasons.map((r, i) => (
            <div key={i} style={{ fontSize: 10, color: '#6b7280', padding: '1px 0' }}>· {r}</div>
          ))}
        </div>
      )}

      {/* Warnings */}
      {warnings.length > 0 && (
        <div style={{ marginTop: 5 }}>
          {warnings.map((w, i) => (
            <div key={i} style={{
              fontSize: 10, color: '#fbbf24',
              padding: '2px 6px', borderRadius: 3,
              background: 'rgba(251,191,36,0.08)', marginBottom: 2,
            }}>⚠ {w}</div>
          ))}
        </div>
      )}
    </div>
  );
};

// ── MTF Trend Alignment Panel (Phase 8B.1 — DISPLAY-ONLY) ───────────────────
// Fetches 4H/15M Databento-derived trend states every 30 s.
// Purely informational — no gate, no scoring, no execution.
interface MTFTf {
  trend: string;
  strength?: string | null;
  last_closed_bar?: string | null;
  bar_count: number;
  stale?: boolean;
  freshness?: string;
  age_seconds?: number | null;
  source?: string;
  unavailable_reason?: string | null;
}
interface MTFState {
  instrument: string;
  four_hour: MTFTf;
  fifteen_minute: MTFTf;
  alignment: string;
  alignment_freshness?: string;
  updated_at?: string | null;
  source?: string;
  note?: string;
  error?: string;
}
const MTF_TREND_COLOR: Record<string, string> = {
  BULLISH:     '#22c55e',
  BEARISH:     '#ef4444',
  NEUTRAL:     '#f59e0b',
  STALE:       '#6b7280',
  UNAVAILABLE: '#374151',
};
const MTF_ALIGN_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  ALIGNED_LONG:  { bg: 'rgba(34,197,94,0.12)',   color: '#22c55e', label: '✓ ALIGNED LONG'  },
  ALIGNED_SHORT: { bg: 'rgba(239,68,68,0.12)',   color: '#ef4444', label: '✓ ALIGNED SHORT' },
  CONFLICTING:   { bg: 'rgba(245,158,11,0.12)',  color: '#f59e0b', label: '⚠ CONFLICTING'   },
  MIXED:         { bg: 'rgba(107,114,128,0.12)', color: '#9ca3af', label: '○ MIXED'         },
  STALE:         { bg: 'rgba(107,114,128,0.10)', color: '#6b7280', label: '◈ STALE'         },
  UNAVAILABLE:   { bg: 'rgba(107,114,128,0.10)', color: '#6b7280', label: '— UNAVAILABLE'   },
};
function mtfFmtTs(iso?: string | null): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' }) + ' UTC';
  } catch { return ''; }
}
function mtfFmtAge(age?: number | null): string {
  if (age == null || !Number.isFinite(age)) return 'age unknown';
  if (age < 60) return `age ${Math.floor(age)}s`;
  if (age < 3600) return `age ${Math.floor(age / 60)}m`;
  return `age ${(age / 3600).toFixed(age >= 86400 ? 0 : 1)}h`;
}

function mtfUnavailable(ticker: string, reason: string): MTFState {
  const tf: MTFTf = {
    trend: 'UNAVAILABLE', bar_count: 0, stale: false, freshness: 'UNAVAILABLE',
    age_seconds: null, source: 'databento_1m_resample_closed_bars',
    unavailable_reason: reason,
  };
  return {
    instrument: ticker || 'MNQ', four_hour: tf, fifteen_minute: { ...tf },
    alignment: 'UNAVAILABLE', alignment_freshness: 'UNAVAILABLE',
    source: 'databento_1m_resample', error: reason,
  };
}

const MTFTrendPanel: React.FC<{ ticker: string }> = ({ ticker }) => {
  const [state, setState] = useState<MTFState | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch(
          `/api/market/trend-alignment?instrument=${encodeURIComponent(ticker || 'MNQ')}`,
          { credentials: 'include', headers: getAuthHeader() },
        );
        if (!r.ok) {
          if (!cancelled) setState(mtfUnavailable(ticker, `request_failed_${r.status}`));
          return;
        }
        const j = await r.json() as MTFState;
        if (!cancelled) setState(j);
      } catch {
        if (!cancelled) setState(mtfUnavailable(ticker, 'request_unavailable'));
      }
    };
    load();
    const id = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [ticker]);

  const fh  = state?.four_hour      ?? { trend: '—', bar_count: 0 };
  const fm  = state?.fifteen_minute ?? { trend: '—', bar_count: 0 };
  const aln = state?.alignment      ?? '—';
  const alnStyle = MTF_ALIGN_STYLE[aln] ?? { bg: 'rgba(107,114,128,0.08)', color: '#4b5563', label: aln };

  const TfRow: React.FC<{ label: string; tf: MTFTf }> = ({ label, tf }) => {
    const arrow = tf.trend === 'BULLISH' ? '↑ ' : tf.trend === 'BEARISH' ? '↓ ' : tf.trend === 'NEUTRAL' ? '— ' : '';
    const col   = MTF_TREND_COLOR[tf.trend] ?? '#6b7280';
    const ts    = mtfFmtTs(tf.last_closed_bar);
    const unavailable = tf.trend === 'UNAVAILABLE';
    const freshness = tf.freshness === 'STALE' ? 'STALE · UNAVAILABLE'
      : unavailable ? 'UNAVAILABLE' : 'CURRENT';
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 9, fontWeight: 700, color: T.txtMuted, letterSpacing: '0.06em', minWidth: 26 }}>{label}</span>
        <span style={{ fontWeight: 700, fontSize: 13, color: col, fontFamily: T.mono }}>
          {arrow}{tf.trend || '—'}
        </span>
        {tf.strength && (
          <span style={{ fontSize: 9, color: '#9ca3af' }}>— {tf.strength}</span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 9, color: unavailable && tf.freshness === 'STALE' ? '#f59e0b' : T.txtMuted }}>
          {freshness} · {mtfFmtAge(tf.age_seconds)} · {ts || `${tf.bar_count} bars`}
        </span>
      </div>
    );
  };

  return (
    <div style={{
      background: T.panel, border: `1px solid ${T.border}`, borderRadius: 10,
      padding: '10px 14px', marginBottom: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: T.txtMuted, textTransform: 'uppercase' }}>
          Shadow Multi-Timeframe Trend
        </span>
        <span style={{
          marginLeft: 'auto', fontSize: 10, fontWeight: 700,
          padding: '2px 9px', borderRadius: 999,
          background: alnStyle.bg, color: alnStyle.color,
          border: `1px solid ${alnStyle.color}33`,
        }}>
          {alnStyle.label}
        </span>
      </div>
      <TfRow label="4H"  tf={fh} />
      <TfRow label="15M" tf={fm} />
      <div style={{ marginTop: 7, paddingTop: 7, borderTop: `1px solid ${T.border}`, fontSize: 9, color: T.txtMuted, lineHeight: 1.45 }}>
        Source: <span style={{ color: T.txtPri }}>{state?.source ?? 'databento_1m_resample'}</span> · closed bars only · shadow/display-only.
        <br />
        This panel never overrides the strict gate, execution state, or Visual Brain. Stale directional values are intentionally hidden as unavailable.
      </div>
    </div>
  );
};

// ── Canonical Databento Market State Panel — shadow/observation only ──────────
// Agreement status → color + label
const _agreeColor = (s: string) =>
  s === 'MATCH'      ? '#34d399' :
  s === 'SMALL_DIFF' ? '#fbbf24' :
  s === 'LARGE_DIFF' ? '#ef4444' :
  s === 'STALE'      ? '#f97316' :
  s === 'WAITING'    ? '#6b7280' : '#4b5563';

const _agreeLabel = (s: string) =>
  s === 'MATCH'      ? '✓ MATCH' :
  s === 'SMALL_DIFF' ? '~ SMALL' :
  s === 'LARGE_DIFF' ? '✕ LARGE' :
  s === 'STALE'      ? '⚠ STALE' :
  s === 'WAITING'    ? '… WAITING' :
  s === 'UNAVAILABLE'? '— N/A' : s;

const _freshBadge = (f: string) =>
  f === 'HEALTHY' ? { color: '#34d399', label: 'LIVE' } :
  f === 'STALE'   ? { color: '#f97316', label: 'STALE' } :
                    { color: '#4b5563', label: '—' };

interface _InstrumentCompRow {
  inst:        string;
  vc:          Record<string, unknown>;
  warmup:      Record<string, unknown>;
  trend:       Record<string, unknown>;
}

const _InstrumentRow: React.FC<{ row: _InstrumentCompRow }> = ({ row }) => {
  const { inst, vc, warmup } = row;
  const warm = warmup as Record<string, unknown>;
  const warming = !warm.complete;
  const agreeStatus = String(vc.agreement_status ?? 'UNAVAILABLE');
  const legFresh    = _freshBadge(String(vc.legacy_freshness ?? ''));
  const dbFresh     = _freshBadge(String(vc.databento_freshness ?? ''));

  const legVwap = vc.legacy_vwap != null ? Number(vc.legacy_vwap).toFixed(2) : '—';
  const dbVwap  = vc.databento_vwap != null ? Number(vc.databento_vwap).toFixed(2) : '—';
  const absDiff = vc.absolute_difference != null ? Math.abs(Number(vc.absolute_difference)).toFixed(2) : '—';
  const tickDiff= vc.tick_difference != null ? Number(vc.tick_difference).toFixed(1) + ' tk' : '—';
  const sessStart = vc.session_start
    ? String(vc.session_start).slice(11, 16) + 'z'
    : '—';
  const sampVol = vc.sample_volume != null
    ? Number(vc.sample_volume).toLocaleString(undefined, { maximumFractionDigits: 0 })
    : '—';

  const cellStyle: React.CSSProperties = {
    padding: '5px 8px', fontSize: 11, whiteSpace: 'nowrap',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
    fontVariantNumeric: 'tabular-nums',
  };
  const instCell: React.CSSProperties = {
    ...cellStyle, fontWeight: 700, color: '#c4b5fd', width: 40,
  };

  return (
    <tr>
      <td style={instCell}>{inst}</td>
      {warming ? (
        <td colSpan={8} style={{ ...cellStyle, color: '#4b5563' }}>
          Warming up… {String(warm.bars_available ?? 0)}/{String(warm.bars_required ?? 0)} bars
        </td>
      ) : (
        <>
          <td style={{ ...cellStyle, color: '#9ca3af' }}>{legVwap}</td>
          <td style={{ ...cellStyle, color: '#a5f3fc' }}>{dbVwap}</td>
          <td style={{ ...cellStyle, color: absDiff === '—' ? '#4b5563' : '#d1d5db' }}>{absDiff}</td>
          <td style={{ ...cellStyle, color: tickDiff === '—' ? '#4b5563' : '#d1d5db' }}>{tickDiff}</td>
          <td style={{ ...cellStyle }}>
            <span style={{ color: legFresh.color, fontWeight: 600, fontSize: 10 }}>{legFresh.label}</span>
            {' / '}
            <span style={{ color: dbFresh.color, fontWeight: 600, fontSize: 10 }}>{dbFresh.label}</span>
          </td>
          <td style={{ ...cellStyle, fontWeight: 700, color: _agreeColor(agreeStatus) }}>
            {_agreeLabel(agreeStatus)}
          </td>
          <td style={{ ...cellStyle, color: '#6b7280' }}>{sampVol}</td>
          <td style={{ ...cellStyle, color: '#6b7280' }}>{sessStart}</td>
        </>
      )}
    </tr>
  );
};

const _promotionBadge = (status: string) => {
  if (status === 'VALIDATING') return { color: '#34d399', bg: 'rgba(52,211,153,0.12)', label: '✓ VALIDATING' };
  return { color: '#6b7280', bg: 'transparent', label: 'SHADOW' };
};

const _MetricsRow: React.FC<{ inst: string; vc: Record<string, unknown> }> = ({ inst, vc }) => {
  const n       = vc.sample_count   != null ? Number(vc.sample_count)   : 0;
  const acc     = vc.acceptable_count   != null ? Number(vc.acceptable_count)   : 0;
  const unacc   = vc.unacceptable_count != null ? Number(vc.unacceptable_count) : 0;
  const cons    = vc.consecutive_acceptable != null ? Number(vc.consecutive_acceptable) : 0;
  const longest = vc.longest_consecutive_acceptable != null ? Number(vc.longest_consecutive_acceptable) : 0;
  const avg     = vc.avg_tick_diff    != null ? Number(vc.avg_tick_diff).toFixed(2)    : '—';
  const med     = vc.median_tick_diff != null ? Number(vc.median_tick_diff).toFixed(2) : '—';
  const p95     = vc.p95_tick_diff    != null ? Number(vc.p95_tick_diff).toFixed(2)    : '—';
  const max     = vc.max_tick_diff    != null ? Number(vc.max_tick_diff).toFixed(2)    : '—';
  const pct     = vc.pct_within_tolerance != null ? `${Number(vc.pct_within_tolerance).toFixed(0)}%` : '—';
  const promoStatus = String(vc.promotion_status ?? 'SHADOW');
  const badge   = _promotionBadge(promoStatus);

  const cellStyle: React.CSSProperties = {
    padding: '3px 8px', fontSize: 10, color: '#6b7280',
    borderBottom: '1px solid rgba(255,255,255,0.03)',
    fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap',
  };
  return (
    <>
      <tr>
        <td style={{ ...cellStyle, color: '#4b5563', fontWeight: 600 }} rowSpan={2}>{inst}</td>
        <td style={cellStyle}>{n} total</td>
        <td style={{ ...cellStyle, color: '#34d399' }}>{acc} ok</td>
        <td style={{ ...cellStyle, color: unacc > 0 ? '#f87171' : '#4b5563' }}>{unacc} bad</td>
        <td style={cellStyle}>cur {cons}</td>
        <td style={{ ...cellStyle, color: longest >= 50 ? '#34d399' : '#6b7280' }}>lng {longest}</td>
        <td style={cellStyle}>
          <span style={{
            background: badge.bg, color: badge.color,
            fontWeight: 700, fontSize: 9, padding: '1px 5px', borderRadius: 3,
          }}>{badge.label}</span>
        </td>
        <td style={cellStyle}>{pct} in tol</td>
      </tr>
      <tr>
        <td colSpan={3} style={{ ...cellStyle, color: '#4b5563' }}>
          avg {avg} · med {med} · p95 {p95} · max {max} ticks
        </td>
        <td colSpan={4} style={{ ...cellStyle, color: '#4b5563' }}>
          need longest≥50 to promote
        </td>
      </tr>
    </>
  );
};

const CanonicalStatePanel: React.FC = () => {
  const [data,    setData]    = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [err,     setErr]     = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch('/api/canonical-market-state', {
        credentials: 'include',
        headers: getAuthHeader(),
      })
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(d => { if (!cancelled) { setData(d); setLoading(false); setErr(null); } })
        .catch(e => { if (!cancelled) { setErr(String(e)); setLoading(false); } });
    };
    load();
    const id = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (loading || err || !data?.ok) return null;

  const states = (data.states ?? {}) as Record<string, Record<string, unknown>>;
  const INSTS  = ['MGC', 'MNQ', 'MES', 'MYM'];

  const rows: _InstrumentCompRow[] = INSTS.map(inst => {
    const s = (states[inst] ?? {}) as Record<string, unknown>;
    return {
      inst,
      vc:     (s.vwap_comparison ?? {}) as Record<string, unknown>,
      warmup: (s.warmup ?? {}) as Record<string, unknown>,
      trend:  (s.trend ?? {}) as Record<string, unknown>,
    };
  });

  const panelStyle: React.CSSProperties = {
    background: 'rgba(99,102,241,0.05)',
    border: '1px solid rgba(99,102,241,0.15)',
    borderRadius: 10, padding: '12px 14px', marginTop: 10,
    overflowX: 'auto',
  };
  const thStyle: React.CSSProperties = {
    padding: '4px 8px', fontSize: 10, color: '#6b7280', fontWeight: 600,
    borderBottom: '1px solid rgba(255,255,255,0.08)', whiteSpace: 'nowrap',
    letterSpacing: '0.04em', textAlign: 'left' as const,
  };

  return (
    <div style={panelStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: '#818cf8', fontWeight: 700, letterSpacing: '0.06em' }}>
          DATABENTO SHADOW VWAP · 4-INSTRUMENT COMPARISON
        </span>
        <span style={{ fontSize: 10, color: '#4b5563', fontStyle: 'italic' }}>shadow only · 30s poll</span>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={thStyle}>INST</th>
            <th style={thStyle}>Legacy VWAP</th>
            <th style={thStyle}>Shadow VWAP</th>
            <th style={thStyle}>|Δ|</th>
            <th style={thStyle}>Ticks</th>
            <th style={thStyle}>Freshness</th>
            <th style={thStyle}>Agreement</th>
            <th style={thStyle}>Vol (sess)</th>
            <th style={thStyle}>Sess start</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => <_InstrumentRow key={row.inst} row={row} />)}
        </tbody>
      </table>

      <div style={{ marginTop: 10, borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 8 }}>
        <div style={{ fontSize: 10, color: '#818cf8', fontWeight: 700, marginBottom: 5, letterSpacing: '0.05em' }}>
          SHADOW HIGHER-TIMEFRAME TREND — NEVER OVERRIDES STRICT GATE OR VISUAL BRAIN
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(130px, 1fr))', gap: 6 }}>
          {rows.map(row => {
            const trend = row.trend;
            const health = String(trend.health ?? 'UNAVAILABLE');
            const stale = health === 'STALE';
            const calcError = health === 'CALCULATION_ERROR';
            const t15 = String(trend.trend_15m ?? 'UNAVAILABLE');
            const t4h = String(trend.trend_4h ?? 'UNAVAILABLE');
            const age15 = trend.trend_15m_age_seconds == null ? 'age unknown' : mtfFmtAge(Number(trend.trend_15m_age_seconds));
            const age4 = trend.trend_4h_age_seconds == null ? 'age unknown' : mtfFmtAge(Number(trend.trend_4h_age_seconds));
            return (
              <div key={row.inst} style={{ padding: '6px 7px', borderRadius: 5, background: 'rgba(255,255,255,0.02)', fontSize: 10 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <strong style={{ color: '#a5b4fc' }}>{row.inst}</strong>
                  <span style={{ color: calcError ? '#f87171' : stale ? '#f59e0b' : '#6b7280' }}>
                    {calcError ? 'CALC ERROR · UNAVAILABLE' : stale ? 'STALE · UNAVAILABLE' : health}
                  </span>
                </div>
                <div style={{ color: '#9ca3af' }}>15M {t15} · {age15}</div>
                <div style={{ color: '#9ca3af' }}>4H {t4h} · {age4}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Comparison metrics sub-table */}
      <div style={{ marginTop: 10, borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 8 }}>
        <div style={{ fontSize: 10, color: '#4b5563', fontWeight: 600, marginBottom: 4, letterSpacing: '0.05em' }}>
          ROLLING COMPARISON METRICS (in-memory, resets on restart)
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {rows
              .filter(r => Number(r.vc.sample_count ?? 0) > 0)
              .map(r => <_MetricsRow key={r.inst} inst={r.inst} vc={r.vc} />)
            }
            {rows.every(r => Number(r.vc.sample_count ?? 0) === 0) && (
              <tr><td colSpan={8} style={{ padding: '4px 8px', fontSize: 10, color: '#4b5563' }}>
                No comparisons recorded yet — accumulating…
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Source provenance legend */}
      <div style={{ marginTop: 8, fontSize: 9, color: '#374151', lineHeight: 1.5 }}>
        Legacy VWAP: Yahoo Finance auto-fetch · Shadow VWAP: Databento 1m bars · CVD/RVOL: Databento primary · Trend/FVG: Databento · Zones/ORB: TradingView (legacy, not promotable)
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
const ThesisPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const lb    = (p.left_brain ?? {}) as Record<string, unknown>;
  const diag  = (lb.diagnosis ?? {}) as Record<string, unknown>;

  // ── Diagnosis-driven state ─────────────────────────────────────────────────
  const diagStatus  = safeStr(diag.status, 'NO_DATA');
  const isAvailable = diagStatus === 'AVAILABLE';
  const isStale     = diagStatus === 'STALE';
  const isCollect   = diagStatus === 'COLLECTING_DATA';
  const isNoData    = diagStatus === 'NO_DATA';
  const isError     = diagStatus === 'ERROR';

  const dir     = safeStr(lb.direction, '');
  const conf    = safeNum(lb.confidence);
  // narrative may be a list of strings (new format) or a plain string (fallback)
  const _rawNarr = lb.narrative;
  const narrLines: string[] = Array.isArray(_rawNarr)
    ? (_rawNarr as unknown[]).map(x => String(x)).filter(Boolean)
    : safeStr(_rawNarr, '') ? [safeStr(_rawNarr, '')] : [];
  // Display label: CONFLICTED is internal jargon — surface it as NO EDGE
  const displayDir = dir === 'CONFLICTED' ? 'NO EDGE' : (dir || '—');
  const age     = fmtAge(lb.generated_at);
  const dCol    = dirColor(dir);

  const obsCount    = safeNum(diag.observation_count) ?? 0;
  const dbBars      = safeNum(diag.databento_bars) ?? 0;
  const ageSec      = safeNum(diag.thesis_age_seconds);
  const ageMin      = ageSec != null ? Math.round(ageSec / 60) : null;
  const calcAt      = fmtTs(diag.last_calculation_at);
  const sourceSym   = safeStr(diag.source_symbol, '');

  // Task #56: true when the thesis is the silent _neutral_thesis() fallback (available=False)
  // rather than a real NEUTRAL market read. The operator sees "NEUTRAL" either way —
  // this flag lets us show a DATA QUALITY LOW sub-badge instead of implying genuine conflict.
  const isMiFallback = Boolean(diag.thesis_is_mi_fallback);

  // Badge shown in the panel header
  const badge = isAvailable
    ? isMiFallback
      ? <div style={{ display: 'flex', gap: 4 }}>
          <Badge label={displayDir} color={dCol} />
          <Badge label="DATA QUALITY LOW" color={T.amber} />
        </div>
      : <Badge label={displayDir} color={dCol} />
    : isStale
      ? <Badge label="STALE" color={T.amber} />
      : isCollect
        ? <Badge label="COLLECTING DATA" color={T.txtSec} />
        : <Badge label="UNAVAILABLE" color={T.txtMuted} />;

  return (
    <Panel title="Left Brain Thesis" badge={badge}>

      {/* ── NO_DATA — thesis never computed ─── */}
      {(isNoData || isError) && (
        <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
          <UnavailableNote msg={isError
            ? 'Thesis engine error — check diagnostics'
            : 'No thesis yet — awaiting first Databento bar-close scan'} />
          {sourceSym && (
            <div style={{ fontSize:9, color:T.txtMuted }}>
              Source: {sourceSym} → Databento bars received: {dbBars}
            </div>
          )}
        </div>
      )}

      {/* ── COLLECTING DATA — fewer than 5 observations ─── */}
      {isCollect && (
        <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
          <div style={{ fontSize:11, color:T.txtSec }}>
            LEFT BRAIN COLLECTING DATA
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:8 }}>
            <div style={{ flex:1, height:5, background:'rgba(255,255,255,0.07)', borderRadius:3, overflow:'hidden' }}>
              <div style={{ height:'100%', width:`${Math.min((obsCount / 5) * 100, 100)}%`,
                background:T.cyan, borderRadius:3, transition:'width 0.5s ease' }} />
            </div>
            <span style={{ fontSize:10, color:T.cyan, fontFamily:T.mono, whiteSpace:'nowrap' }}>
              {obsCount} / 5 observations
            </span>
          </div>
          {dbBars > 0 && (
            <div style={{ fontSize:9.5, color:T.txtMuted }}>
              Databento bars: {dbBars} · Source: {sourceSym}
            </div>
          )}
          {calcAt !== '—' && (
            <div style={{ fontSize:9.5, color:T.txtMuted }}>Last scan: {calcAt}</div>
          )}
        </div>
      )}

      {/* ── STALE — thesis older than 10 minutes ─── */}
      {isStale && (
        <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
          <div style={{ background:`${T.amber}12`, border:`1px solid ${T.amber}44`, borderRadius:8, padding:'10px 14px' }}>
            <div style={{ fontSize:10, fontWeight:700, color:T.amber, letterSpacing:'0.08em', marginBottom:4 }}>
              THESIS STALE
            </div>
            <div style={{ fontSize:11, color:T.txtSec }}>
              Last successful calculation:{' '}
              {ageMin != null ? `${ageMin} min ago` : age}
            </div>
            {dir && dir !== 'NEUTRAL' && (
              <div style={{ fontSize:10, color:T.txtMuted, marginTop:4 }}>
                Direction when last calculated:{' '}
                <span style={{ color:dCol, fontWeight:700 }}>{dir}</span>
              </div>
            )}
          </div>
          <div style={{ fontSize:9, color:T.txtMuted }}>
            Databento bars: {dbBars} · Source: {sourceSym}
          </div>
          <div style={{ fontSize:9, color:T.txtMuted }}>
            Low-volume overnight periods (e.g. 12 AM–6 AM ET on Micro Gold)
            may reduce bar-close frequency and delay thesis updates.
          </div>
        </div>
      )}

      {/* ── AVAILABLE — fresh, real thesis ─── */}
      {isAvailable && (
        <>
          {/* DATA QUALITY LOW note — fallback NEUTRAL (no bar-close data), not genuine conflict */}
          {isMiFallback && (
            <div style={{
              background: `${T.amber}10`, border: `1px solid ${T.amber}40`,
              borderRadius: 7, padding: '8px 12px', marginBottom: 10,
            }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: T.amber,
                            letterSpacing: '0.07em', marginBottom: 3 }}>
                DATA QUALITY LOW
              </div>
              <div style={{ fontSize: 10.5, color: T.txtSec, lineHeight: 1.45 }}>
                NEUTRAL reflects insufficient bar-close data — not genuine market conflict.
                Directional thesis will update once more bars close.
              </div>
            </div>
          )}

          {/* Direction + confidence bar */}
          <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:12 }}>
            <div>
              <div style={{ fontSize:22, fontWeight:800, color:dCol, lineHeight:1 }}>{displayDir}</div>
              <div style={{ fontSize:9.5, color:T.txtMuted, marginTop:2 }}>{age}</div>
            </div>
            {conf != null && (
              <div style={{ flex:1 }}>
                <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
                  <span style={{ fontSize:9, color:T.txtMuted }}>CONFIDENCE</span>
                  <span style={{ fontSize:11, fontWeight:700, color:dCol, fontFamily:T.mono }}>{conf} / 100</span>
                </div>
                <div style={{ height:5, background:'rgba(255,255,255,0.07)', borderRadius:3, overflow:'hidden' }}>
                  <div style={{ height:'100%', width:`${Math.min(conf ?? 0, 100)}%`, background:dCol,
                    borderRadius:3, transition:'width 0.5s ease' }} role="progressbar"
                    aria-valuenow={conf ?? 0} aria-valuemin={0} aria-valuemax={100}
                    aria-label="Thesis confidence" />
                </div>
              </div>
            )}
          </div>

          {/* Narrative — render each bullet on its own line */}
          {narrLines.length > 0 && (
            <div style={{ display:'flex', flexDirection:'column', gap:5, marginBottom:10,
              borderLeft:`2px solid ${dCol}55`, paddingLeft:10 }}>
              {narrLines.map((line, i) => (
                <div key={i} style={{ fontSize:11, color:T.txtSec, lineHeight:1.5 }}>{line}</div>
              ))}
            </div>
          )}

          {/* Meta */}
          <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginTop:4 }}>
            {lb.status  != null && <Badge label={String(lb.status)}  color={T.txtSec} />}
            {lb.momentum != null && <Badge label={String(lb.momentum)} color={T.amber} />}
          </div>
        </>
      )}
    </Panel>
  );
};

// ── Learning status helpers ────────────────────────────────────────────────────
const WEIGHT_STATUS_LABEL: Record<string, string> = {
  UPDATED:              'UPDATED',
  NO_CHANGE:            'NO CHANGE',
  INSUFFICIENT_SAMPLES: 'COLLECTING DATA',
  NOT_ELIGIBLE:         'NOT ELIGIBLE',
  KEY_NOT_FOUND:        'KEY MISMATCH',
  DISABLED:             'DISABLED',
  DB_DISABLED:          'DISABLED',
  DIAGNOSTIC_BUILD_FAILED: 'UNAVAILABLE',
  COACH_BUILD_FAILED:   'UNAVAILABLE',
};
const WEIGHT_STATUS_COLOR: Record<string, string> = {
  UPDATED:              '#22c55e',
  NO_CHANGE:            '#94a3b8',
  INSUFFICIENT_SAMPLES: '#f59e0b',
  NOT_ELIGIBLE:         '#64748b',
  KEY_NOT_FOUND:        '#ef4444',
  DISABLED:             '#64748b',
  DB_DISABLED:          '#64748b',
};

// ── Explain Decision Drawer ───────────────────────────────────────────────────
// Read-only drawer that explains the current Main Brain decision using only
// existing payload fields.  No new polling, no trading-logic changes.
const ExplainDecisionDrawer: React.FC<{
  p: Record<string, unknown>;
  onClose: () => void;
}> = ({ p, onClose }) => {
  const d       = extractExplainData(p);
  const summary = buildPlainEnglishSummary(d);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const verdictCol = d.isActionable ? T.green : /block/i.test(d.verdict) ? T.red : T.amber;
  const candCol    = d.candidateDir === 'LONG' ? T.green : d.candidateDir === 'SHORT' ? T.red : T.txtMuted;
  const alignCol   = d.alignment === 'FULLY ALIGNED' ? T.green
    : d.alignment === 'COUNTER-TREND' ? T.red
    : d.alignment === 'NEUTRAL' ? T.amber : T.txtMuted;

  const SectionHead: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <div style={{
      fontSize:9, fontWeight:700, letterSpacing:'0.12em', color:T.cyan,
      textTransform:'uppercase', marginBottom:10,
      paddingBottom:6, borderBottom:`1px solid rgba(56,189,248,0.15)`,
    }}>{children}</div>
  );

  const renderComps = (comps: ScoreComponent[], title: string, col: string) => {
    if (comps.length === 0) return (
      <div>
        <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.08em', marginBottom:4 }}>{title.toUpperCase()}</div>
        <div style={{ fontSize:10, color:T.txtMuted, fontStyle:'italic' }}>SCORE BREAKDOWN UNAVAILABLE</div>
      </div>
    );
    return (
      <div>
        <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.08em', marginBottom:6 }}>{title.toUpperCase()}</div>
        {comps.map((c, i) => (
          <div key={i} style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4 }}>
            <span style={{ fontSize:11, color:c.present ? col : 'rgba(255,255,255,0.18)', width:14, flexShrink:0, lineHeight:1 }}>
              {c.present ? '+' : '−'}
            </span>
            <span style={{ fontSize:10, color:c.present ? T.txtSec : T.txtMuted, flex:1 }}>{c.label}</span>
            <span style={{ fontSize:9.5, fontFamily:T.mono, color:c.present ? col : T.txtMuted, flexShrink:0 }}>
              {c.present ? `+${c.points}` : '—'}
            </span>
          </div>
        ))}
      </div>
    );
  };

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        aria-hidden
        style={{
          position:'fixed', inset:0,
          background:'rgba(0,0,0,0.65)',
          backdropFilter:'blur(2px)',
          zIndex:2000,
        }}
      />
      {/* Drawer panel */}
      <div
        role="dialog"
        aria-label="Explain Decision"
        aria-modal="true"
        style={{
          position:'fixed', top:0, right:0, bottom:0,
          width:'min(520px,100vw)',
          background:T.panel,
          borderLeft:`1px solid ${T.borderMid}`,
          zIndex:2001,
          overflowY:'auto',
          display:'flex', flexDirection:'column',
        }}
      >
        {/* Sticky header */}
        <div style={{
          position:'sticky', top:0, zIndex:1,
          background:T.panel, borderBottom:`1px solid ${T.border}`,
          padding:'14px 18px', display:'flex', alignItems:'center', gap:10,
        }}>
          <span style={{ fontSize:11, fontWeight:700, letterSpacing:'0.12em', color:T.cyan, flex:1 }}>
            EXPLAIN DECISION
          </span>
          <button
            onClick={onClose}
            aria-label="Close explain decision drawer"
            style={{
              background:'none', border:'none', cursor:'pointer',
              color:T.txtMuted, fontSize:18, lineHeight:1, padding:'2px 6px', borderRadius:4,
            }}
          >✕</button>
        </div>

        {/* Body */}
        <div style={{ padding:'18px 18px 32px', flex:1, display:'flex', flexDirection:'column', gap:22 }}>

          {/* ── 1. Decision Summary (4-quadrant) ── */}
          <div>
            <SectionHead>Decision Summary</SectionHead>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
              {[
                { label:'Current Verdict', value:d.verdict,      col:verdictCol },
                { label:'Best Candidate',  value:d.candidateDir, col:candCol    },
                { label:'Market Thesis',   value:d.thesisDir,    col:dirColor(d.thesisDir) },
                { label:'Alignment',       value:d.alignment,    col:alignCol   },
              ].map(({ label, value, col }) => (
                <div key={label} style={{
                  padding:'10px 12px', borderRadius:8,
                  background:`${col}0d`, border:`1px solid ${col}33`,
                }}>
                  <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', marginBottom:4 }}>
                    {label.toUpperCase()}
                  </div>
                  <div style={{ fontSize:12, fontWeight:800, color:col, letterSpacing:'0.04em', wordBreak:'break-word' }}>
                    {value}
                  </div>
                </div>
              ))}
            </div>
            {d.contradiction && (
              <div style={{
                marginTop:8, padding:'7px 10px',
                background:`${T.amber}0d`, border:`1px solid ${T.amber}40`, borderRadius:6,
                fontSize:10, color:T.amber,
              }}>
                ⚠ Candidate ({d.candidateDir}) differs from higher-scoring side ({d.higherSide})
              </div>
            )}
          </div>

          {/* ── 2. Plain-English Summary ── */}
          <div>
            <SectionHead>Plain-English Summary</SectionHead>
            <div style={{
              padding:'10px 12px',
              background:'rgba(56,189,248,0.04)', border:`1px solid rgba(56,189,248,0.14)`,
              borderLeft:`3px solid ${T.cyan}55`, borderRadius:7,
              fontSize:11, color:T.txtSec, lineHeight:1.6,
            }}>
              {summary}
            </div>
          </div>

          {/* ── 3. Long vs Short Scores ── */}
          <div>
            <SectionHead>Long vs Short</SectionHead>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, marginBottom:10 }}>
              {([['LONG', d.longScore, T.green], ['SHORT', d.shortScore, T.red]] as const).map(([side, sc, col]) => {
                const isCandidate = d.candidateDir === side;
                const isHigher    = d.higherSide === side;
                return (
                  <div key={side} style={{
                    padding:'10px 12px', borderRadius:8,
                    background:`${col}0d`, border:`1px solid ${col}${isCandidate ? '55' : '22'}`,
                  }}>
                    <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', marginBottom:3 }}>{side} SCORE</div>
                    <div style={{ fontSize:22, fontWeight:800, color:col, fontFamily:T.mono, lineHeight:1 }}>{sc}</div>
                    <div style={{ display:'flex', gap:4, flexWrap:'wrap', marginTop:5 }}>
                      {isCandidate && (
                        <span style={{ fontSize:8.5, color:col, border:`1px solid ${col}55`, borderRadius:3, padding:'1px 5px', fontWeight:700 }}>
                          SELECTED
                        </span>
                      )}
                      {isHigher && d.higherSide !== 'TIED' && (
                        <span style={{ fontSize:8.5, color:T.amber, border:`1px solid ${T.amber}55`, borderRadius:3, padding:'1px 5px', fontWeight:700 }}>
                          HIGHER
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            <div style={{
              padding:'7px 12px', borderRadius:7,
              background:'rgba(255,255,255,0.03)', border:`1px solid ${T.border}`,
              display:'flex', alignItems:'center', gap:8,
            }}>
              <span style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.08em' }}>DECISION MARGIN</span>
              <span style={{
                fontSize:12, fontWeight:700, fontFamily:T.mono, marginLeft:'auto',
                color: d.marginLabel === 'TIED' ? T.txtSec
                  : d.marginLabel.startsWith('LONG') ? T.green : T.red,
              }}>
                {d.marginLabel}
              </span>
            </div>
          </div>

          {/* ── 4. Why Each Side Scored ── */}
          <div>
            <SectionHead>Why Each Side Scored</SectionHead>
            {!d.hasComponents ? (
              <div style={{ fontSize:10, color:T.txtMuted, fontStyle:'italic' }}>SCORE BREAKDOWN UNAVAILABLE</div>
            ) : (
              <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
                {renderComps(d.longComponents,  'Why Long Scored',  T.green)}
                {renderComps(d.shortComponents, 'Why Short Scored', T.red)}
              </div>
            )}
          </div>

          {/* ── 5. Why Waiting ── */}
          <div>
            <SectionHead>Why Waiting</SectionHead>
            {d.isActionable ? (
              <div style={{ fontSize:10, color:T.green, fontWeight:600 }}>✓ NO HARD BLOCK — SETUP IS READY</div>
            ) : d.hardBlockers.length === 0 && d.missingConfirmations.length === 0 && !d.opposingStructure ? (
              <div style={{ fontSize:10, color:T.amber }}>NO HARD BLOCK — WAITING FOR CONFIRMATION</div>
            ) : (
              <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
                {d.hardBlockers.map((b, i) => (
                  <div key={i} style={{
                    display:'flex', gap:8, padding:'6px 10px',
                    background:`${T.red}0d`, border:`1px solid ${T.red}33`, borderRadius:6,
                  }}>
                    <span style={{ color:T.red, flexShrink:0, fontSize:11 }}>⊘</span>
                    <span style={{ fontSize:10, color:T.txtSec }}>{b}</span>
                  </div>
                ))}
                {d.missingConfirmations.map((m, i) => (
                  <div key={i} style={{
                    display:'flex', gap:8, padding:'6px 10px',
                    background:'rgba(255,255,255,0.02)', border:`1px solid ${T.border}`, borderRadius:6,
                  }}>
                    <span style={{ color:T.amber, flexShrink:0, fontSize:11 }}>○</span>
                    <span style={{ fontSize:10, color:T.txtSec }}>{m} missing</span>
                  </div>
                ))}
                {d.opposingStructure && d.opposingStructure.effect !== 'NONE' && d.opposingStructure.effect !== 'OBSERVED' && (
                  <div style={{
                    display:'flex', flexDirection:'column', gap:3, padding:'8px 10px',
                    background:`${T.red}0a`, border:`1px solid ${T.red}40`, borderRadius:6,
                    borderLeft:`3px solid ${T.red}`,
                  }}>
                    <div style={{ fontSize:10, color:T.red, fontWeight:700 }}>OPPOSING STRUCTURE ACTIVE</div>
                    <div style={{ fontSize:9.5, color:T.txtSec }}>
                      {d.opposingStructure.direction} {d.opposingStructure.eventType}
                      {d.opposingStructure.remainingSeconds != null && (
                        <> · {Math.ceil(d.opposingStructure.remainingSeconds / 60)}m remaining</>
                      )}
                    </div>
                  </div>
                )}
                {d.opposingStructure && (d.opposingStructure.effect === 'OBSERVED' || d.opposingStructure.effect === 'NONE') && (
                  <div style={{
                    padding:'6px 10px', borderRadius:6,
                    background:`${T.green}08`, border:`1px solid ${T.green}25`,
                    fontSize:9.5, color:T.txtMuted,
                  }}>
                    Opposing {d.opposingStructure.direction} {d.opposingStructure.eventType} — OVERRIDDEN / not blocking
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── 6. To Become Ready ── */}
          <div>
            <SectionHead>To Become Ready</SectionHead>
            {d.mustChange.length === 0 ? (
              <div style={{ fontSize:10, color: d.isActionable ? T.green : T.txtMuted }}>
                {d.isActionable ? '✓ Already READY' : 'Requirements unknown — check diagnostics'}
              </div>
            ) : (
              <ol style={{ margin:0, paddingLeft:18, display:'flex', flexDirection:'column', gap:4 }}>
                {d.mustChange.map((item, i) => (
                  <li key={i} style={{ fontSize:10, color:T.txtSec, lineHeight:1.5 }}>{item}</li>
                ))}
              </ol>
            )}
          </div>

          {/* ── 7. Recent Decision Changes (timeline) ── */}
          <div>
            <SectionHead>Recent Decision Changes</SectionHead>
            {d.timelineEvents.length === 0 ? (
              <div style={{ fontSize:10, color:T.txtMuted }}>No timeline events recorded this session</div>
            ) : (
              <div>
                {d.timelineEvents.map((e, i) => {
                  const detailTxt = e.details != null ? fmtEventDetail(e.details) : '';
                  return (
                    <div key={i} style={{ display:'flex', gap:10, paddingBottom:8, position:'relative' }}>
                      <div style={{ display:'flex', flexDirection:'column', alignItems:'center', flexShrink:0 }}>
                        <div style={{ width:7, height:7, borderRadius:'50%',
                          background:e.eventType === 'THESIS_TRANSITION' ? T.amber : T.cyan, marginTop:2 }} />
                        {i < d.timelineEvents.length - 1 && (
                          <div style={{ width:1, flex:1, background:T.border, marginTop:3 }} />
                        )}
                      </div>
                      <div style={{ flex:1, minWidth:0 }}>
                        <div style={{ fontSize:10, fontWeight:600, color:T.txtPri, marginBottom:1 }}>{e.eventLabel}</div>
                        <div style={{ fontSize:9, color:T.txtMuted }}>
                          {fmtTs(e.timestamp)}{e.source ? ` · ${e.source}` : ''}
                        </div>
                        {detailTxt && (
                          <div style={{ fontSize:9.5, color:T.txtSec, marginTop:2 }}>{detailTxt}</div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

        </div>
      </div>
    </>
  );
};

// ── Verdict Panel ─────────────────────────────────────────────────────────────
const VerdictPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const [showExplain, setShowExplain] = useState(false);

  const v          = (p.verdict ?? {}) as Record<string, unknown>;
  const op         = (p.operator_presentation ?? {}) as Record<string, unknown>;
  const avail      = v.available !== false;
  const score      = safeNum(v.edge_score) ?? 0;
  const scoreMax   = safeNum(v.edge_max) ?? 110;
  const grade      = safeStr(v.edge_grade, '');
  const ready      = safeStr(op.verdict ?? v.readiness, '');
  const rCol       = readinessColor(ready);
  const isReady    = op.is_actionable === true || (op.is_actionable == null && v.is_actionable === true);
  const direction  = safeStr(op.candidate_direction ?? v.direction, '');

  // Rich component list {key, label, points, present} — Phase 7C.2
  const edgeComps  = Array.isArray(v.edge_components)
    ? v.edge_components as Record<string, unknown>[]
    : [];

  // Fallback to old dict format if rich list not available
  const fallbackComps = (edgeComps.length === 0)
    ? (v.components as Record<string, number> | null)
    : null;

  const missingComps = edgeComps.filter(c => c.present === false);
  const structureGuidance = extractStructureGuidance(p);
  const opWaiting = Array.isArray(op.waiting_for)
    ? (op.waiting_for as Record<string, unknown>[]).map(item => ({
        label: safeStr(item.label ?? item.key, ''),
        points: 0,
        structure: item.structure === true,
      })).filter(item => item.label)
    : [];
  const waitingFor = opWaiting.length > 0 ? opWaiting : structureGuidance?.isPendingConfirmation
    ? [{ label: structureWaitingText(structureGuidance), points: 0, structure: true }, ...missingComps.map(c => ({
        label: safeStr(c.label, safeStr(c.key, '').replace(/_/g, ' ')),
        points: safeNum(c.points) ?? 0,
        structure: false,
      }))]
    : missingComps.map(c => ({
        label: safeStr(c.label, safeStr(c.key, '').replace(/_/g, ' ')),
        points: safeNum(c.points) ?? 0,
        structure: false,
      }));
  const structure = selectStructureCycleDisplay(p);
  const structureState = safeStr(structure.state, 'NO_STRUCTURE');
  const structureDir   = safeStr(structure.direction, '');
  const structureEvent = safeStr(structure.last_event ?? structure.active_event, '');
  const nextStructure  = safeStr(structure.next_event, '');
  const structureNote  = safeStr(structure.next_event_reason ?? structure.summary, '');
  const structureConfirmed = structure.confirmed === true;
  const structurePoints = safeNum(structure.allocation_points) ?? 0;
  const structureColor = structureConfirmed ? T.green
    : structureState === 'TREND_INITIAL' ? T.cyan
    : structureState === 'REVERSAL_CANDIDATE' ? T.amber
    : T.txtMuted;
  const structureCreditLabel = structureConfirmed
    ? `CONFIRMED · +${structurePoints} STRUCTURE`
    : structureState === 'TREND_INITIAL'
      ? `INITIAL · +${structurePoints} STRUCTURE`
      : structureState === 'REVERSAL_CANDIDATE'
        ? `REVERSAL CANDIDATE · +${structurePoints} STRUCTURE`
        : 'AWAITING STRUCTURE · +0 STRUCTURE';

  // Repeat the backend-owned strict explanation so the prose cannot contradict
  // the Decision Clarity fields.
  const explanation = safeStr(op.reasoning ?? v.strict_reason, '');

  const explainBtn = (
    <button
      onClick={() => setShowExplain(true)}
      style={{
        background:'rgba(56,189,248,0.08)', border:`1px solid rgba(56,189,248,0.3)`,
        borderRadius:5, cursor:'pointer', padding:'2px 8px',
        fontSize:9, fontWeight:700, letterSpacing:'0.08em', color:T.cyan,
        lineHeight:1.5, transition:'background 0.15s',
      }}
      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(56,189,248,0.16)'; }}
      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(56,189,248,0.08)'; }}
    >
      EXPLAIN
    </button>
  );

  return (
    <>
      {showExplain && <ExplainDecisionDrawer p={p} onClose={() => setShowExplain(false)} />}
      <Panel title="Verdict" badge={<Badge label={ready || 'UNKNOWN'} color={rCol} />} right={explainBtn}>
      {!avail ? <UnavailableNote msg="Verdict unavailable" /> : (
        <div>
          {/* Score header */}
          <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:12 }}>
            <EdgeGauge score={score} />
            <div style={{ flex:1, minWidth:0 }}>
              <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginBottom:6 }}>
                {direction && <Pill text={direction.toUpperCase()} color={dirColor(direction)} />}
                {grade && <Pill text={`Grade ${grade}`} color={score >= 70 ? T.green : score >= 50 ? T.amber : T.red} />}
                <Pill text={isReady ? 'ACTIONABLE' : 'NOT ACTIONABLE'} color={isReady ? T.green : T.red} />
              </div>
              {op.candidate_label != null && (
                <div data-testid="main-brain-candidate-label" style={{ fontSize:9.5, color:T.txtSec, marginBottom:4 }}>
                  {safeStr(op.candidate_label, '')}
                </div>
              )}
              {((op.vwap ?? {}) as Record<string, unknown>).wording != null && (
                <div data-testid="main-brain-vwap" style={{ fontSize:9.5, color:T.cyan, marginBottom:4 }}>
                  {safeStr(((op.vwap ?? {}) as Record<string, unknown>).wording, '')}
                </div>
              )}
              <div style={{ fontFamily:T.mono, fontSize:12, color:T.txtSec }}>
                <span style={{ color:T.cyan, fontWeight:700 }}>{score}</span>
                <span style={{ color:T.txtMuted }}> / {scoreMax}</span>
              </div>
            </div>
          </div>

          {/* ── State-aware Market Structure ─────────────────────────────── */}
          <div style={{
            marginBottom:12, padding:'8px 10px', borderRadius:7,
            background: structureConfirmed ? 'rgba(34,197,94,0.05)' : 'rgba(245,158,11,0.05)',
            border:`1px solid ${structureConfirmed ? 'rgba(34,197,94,0.17)' : 'rgba(245,158,11,0.17)'}`,
          }}>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:8, marginBottom:5 }}>
              <span style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.08em' }}>MARKET STRUCTURE CYCLE</span>
              <span style={{ fontSize:9, color:structureColor, fontWeight:700, letterSpacing:'0.05em' }}>
                {structureState.replace(/_/g, ' ')}
              </span>
            </div>
            <div style={{ display:'flex', flexWrap:'wrap', alignItems:'center', gap:'4px 8px', fontSize:10 }}>
              {structureDir && <span style={{ color:dirColor(structureDir), fontWeight:700 }}>{structureDir.toUpperCase()}</span>}
              {structureEvent && <span style={{ color:T.txtSec, fontFamily:T.mono }}>{structureEvent}</span>}
              <span style={{ color:structureColor, marginLeft:'auto' }}>
                {structureCreditLabel}
              </span>
            </div>
            {nextStructure && (
              <div style={{ marginTop:6, fontSize:10, color:T.txtPri, lineHeight:1.45 }}>
                <span style={{ color:T.txtMuted }}>NEXT VALID EVENT&nbsp;</span>
                <span style={{ color:T.cyan, fontFamily:T.mono, fontWeight:700 }}>{nextStructure}</span>
              </div>
            )}
            {structureNote && <div style={{ marginTop:3, fontSize:9.5, color:T.txtMuted, lineHeight:1.4 }}>{structureNote}</div>}
          </div>

          {/* ── Edge Score Breakdown (rich) ──────────────────────────────── */}
          {edgeComps.length > 0 && (
            <div style={{ marginBottom:12 }}>
              <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.08em', marginBottom:7, display:'flex', justifyContent:'space-between' }}>
                <span>EDGE SCORE BREAKDOWN</span>
                <span style={{ fontFamily:T.mono }}>pts / {scoreMax}</span>
              </div>
              {edgeComps.map((c, i) => {
                const lbl    = safeStr(c.label, safeStr(c.key, '').replace(/_/g, ' '));
                const pts    = safeNum(c.points) ?? 0;
                const here   = c.present === true;
                const barPct = here ? Math.round(pts / scoreMax * 100) : 0;
                return (
                  <div key={i} style={{ display:'flex', alignItems:'center', gap:6, marginBottom:5 }}>
                    <span style={{ fontSize:12, width:14, textAlign:'center', flexShrink:0, color: here ? T.green : 'rgba(255,255,255,0.18)', lineHeight:1 }}>
                      {here ? '✓' : '✗'}
                    </span>
                    <span style={{ fontSize:10, color: here ? T.txtSec : 'rgba(255,255,255,0.3)', flex:1, minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                      {lbl}
                    </span>
                    <div style={{ width:60, height:4, background:'rgba(255,255,255,0.06)', borderRadius:2, flexShrink:0 }}>
                      <div style={{ height:'100%', width:`${barPct}%`, background: here ? T.green : 'transparent', borderRadius:2, transition:'width 0.3s ease' }} />
                    </div>
                    <span style={{ fontSize:9.5, fontFamily:T.mono, color: here ? T.green : 'rgba(255,255,255,0.2)', width:24, textAlign:'right', flexShrink:0 }}>
                      {here ? `+${pts}` : '—'}
                    </span>
                  </div>
                );
              })}
              <div style={{ display:'flex', justifyContent:'flex-end', borderTop:`1px solid ${T.border}`, paddingTop:5, marginTop:2 }}>
                <span style={{ fontSize:9, color:T.txtMuted, fontFamily:T.mono }}>
                  TOTAL&nbsp;&nbsp;<span style={{ color:T.cyan, fontWeight:700 }}>{score}</span>
                </span>
              </div>
            </div>
          )}

          {/* ── Fallback: old dict format ────────────────────────────────── */}
          {fallbackComps && Object.keys(fallbackComps).length > 0 && (
            <div style={{ marginBottom:10 }}>
              <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.08em', marginBottom:5 }}>COMPONENTS</div>
              {Object.entries(fallbackComps).slice(0, 7).map(([k, sc]) => (
                <div key={k} style={{ display:'flex', alignItems:'center', gap:6, marginBottom:3 }}>
                  <span style={{ fontSize:9.5, color:T.txtMuted, minWidth:100 }}>{k.replace(/_/g,' ')}</span>
                  <div style={{ flex:1, height:4, background:'rgba(255,255,255,0.06)', borderRadius:2 }}>
                    <div style={{ height:'100%', width:`${Math.min(Number(sc ?? 0)/20*100, 100)}%`, background:T.cyan, borderRadius:2 }} />
                  </div>
                  <span style={{ fontSize:9.5, fontWeight:700, color:T.cyan, fontFamily:T.mono, minWidth:18, textAlign:'right' }}>{sc}</span>
                </div>
              ))}
            </div>
          )}

          {/* ── Waiting For checklist ────────────────────────────────────── */}
          {waitingFor.length > 0 && (
            <div style={{ marginBottom:10, padding:'8px 10px', background:'rgba(239,68,68,0.05)', borderRadius:7, border:`1px solid rgba(239,68,68,0.14)` }}>
              <div style={{ fontSize:9, color:T.red, letterSpacing:'0.08em', marginBottom:6 }}>WAITING FOR</div>
              {waitingFor.map((item, i) => {
                return (
                  <div key={i} style={{ display:'flex', alignItems:'center', gap:7, marginBottom:3 }}>
                    <span style={{ fontSize:9, color:T.red, opacity:0.6 }}>•</span>
                    <span style={{ fontSize:10, color: item.structure ? T.cyan : `${T.txtPri}99`, flex:1 }}>{item.label}</span>
                    {item.points > 0 && <span style={{ fontSize:9, color:T.txtMuted, fontFamily:T.mono, flexShrink:0 }}>+{item.points} pts</span>}
                  </div>
                );
              })}
            </div>
          )}

          {/* ── Opposing-Structure Block Diagnostic ──────────────────────── */}
          {(() => {
            const os = v.opposing_structure as Record<string, unknown> | null | undefined;
            if (os === undefined) return null; // server hasn't sent the field yet
            const detected  = os != null && os.detected === true;
            const effect    = safeStr((os ?? {}).effect, 'NONE');
            const isBlocking = detected && effect !== 'NONE' && effect !== 'OBSERVED';
            const ageS      = safeNum((os ?? {}).age_seconds);
            const remS      = safeNum((os ?? {}).remaining_seconds);
            const fmtTime   = (s: number | null): string => {
              if (s == null) return '—';
              return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
            };
            const evType    = safeStr((os ?? {}).event_type, '');
            const oppDir    = safeStr((os ?? {}).direction, '');
            const candDir   = safeStr((os ?? {}).candidate_direction, '');
            const instrName = safeStr((os ?? {}).instrument, '');
            const sameTs    = safeStr((os ?? {}).same_direction_ts, '');

            if (!detected || !isBlocking) {
              return (
                <div style={{ marginBottom:10, padding:'7px 10px', background:'rgba(34,197,94,0.05)', borderRadius:7, border:`1px solid rgba(34,197,94,0.15)` }}>
                  <div style={{ fontSize:9, color:T.green, letterSpacing:'0.08em', fontWeight:700 }}>NO HARD ALERT BLOCK</div>
                  {detected && effect === 'OBSERVED' && (
                    <div style={{ fontSize:9.5, color:T.txtMuted, marginTop:4 }}>
                      Opposing {oppDir.toLowerCase()} {evType} detected — dominant side clear (not blocking)
                      {ageS != null && `, age ${fmtTime(ageS)}`}
                    </div>
                  )}
                </div>
              );
            }

            const effectLabel = effect === 'HARD_BLOCK' ? 'Hard block' : 'Score-aware block';
            return (
              <div style={{ marginBottom:10, padding:'9px 10px', background:'rgba(239,68,68,0.07)', borderRadius:7, border:`1px solid rgba(239,68,68,0.28)`, borderLeft:`3px solid ${T.red}` }}>
                <div style={{ fontSize:9, color:T.red, letterSpacing:'0.08em', fontWeight:700, marginBottom:6 }}>BLOCKING ALERT</div>
                <div style={{ fontSize:11, color:T.txtPri, fontWeight:600, marginBottom:4 }}>
                  Recent {oppDir.toLowerCase()} {evType}
                </div>
                <div style={{ display:'grid', gridTemplateColumns:'auto 1fr', gap:'3px 10px', fontSize:9.5 }}>
                  {ageS  != null && <><span style={{ color:T.txtMuted }}>Age</span>          <span style={{ color:T.txtSec, fontFamily:T.mono }}>{fmtTime(ageS)}</span></>}
                  {remS  != null && <><span style={{ color:T.txtMuted }}>Window remaining</span> <span style={{ color:T.amber, fontFamily:T.mono }}>{fmtTime(remS)}</span></>}
                  {candDir        && <><span style={{ color:T.txtMuted }}>Candidate</span>    <span style={{ color:T.txtSec }}>{candDir}</span></>}
                  <><span style={{ color:T.txtMuted }}>Effect</span>           <span style={{ color:T.red }}>{effectLabel}</span></>
                  {instrName      && <><span style={{ color:T.txtMuted }}>Source</span>       <span style={{ color:T.txtSec }}>{instrName} structure cache</span></>}
                  {sameTs         && <><span style={{ color:T.txtMuted }}>Same-dir struct</span> <span style={{ color:T.green, fontFamily:T.mono, fontSize:9 }}>{sameTs.substring(11,19)} UTC</span></>}
                </div>
              </div>
            );
          })()}

          {/* ── Brain Reasoning ──────────────────────────────────────────── */}
          {explanation && (
            <div style={{ padding:'8px 10px', background:'rgba(56,189,248,0.04)', borderRadius:7, border:`1px solid rgba(56,189,248,0.12)`, borderLeft:`3px solid ${T.cyan}33` }}>
              <div style={{ fontSize:9, color:T.cyan, letterSpacing:'0.08em', marginBottom:5 }}>BRAIN REASONING</div>
              <div style={{ fontSize:10.5, color:T.txtSec, lineHeight:1.55, fontStyle:'italic' }}>
                "{explanation}"
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
    </>
  );
};

// ── Fundamental Context — Phase 1 scheduled-event shadow display ─────────────
const FundamentalContextPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const fc = (p.fundamental_context ?? {}) as Record<string, unknown>;
  if (Object.keys(fc).length === 0) return null;

  const status = safeStr(fc.status, 'UNKNOWN').toUpperCase();
  const phase  = safeStr(fc.event_phase, 'NONE').toUpperCase();
  const mins   = safeNum(fc.minutes_to_event);
  const impact = safeStr(fc.impact, '');
  const color  = status === 'EVENT_RISK' ? T.amber
    : status === 'TAILWIND' ? T.green
    : status === 'HEADWIND' ? T.red
    : status === 'NEUTRAL' ? T.cyan
    : T.txtMuted;
  const eventTime = (() => {
    if (!fc.scheduled_at) return '—';
    try {
      return new Date(String(fc.scheduled_at)).toLocaleString('en-US', {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        hour12: true, timeZone: 'America/New_York',
      }) + ' ET';
    } catch { return '—'; }
  })();
  const timing = mins == null ? '—'
    : mins > 0 ? `in ${mins}m`
    : mins < 0 ? `${Math.abs(mins)}m ago`
    : 'now';

  return (
    <Panel
      title="Fundamental Context"
      badge={<Badge label={status.replace(/_/g, ' ')} color={color} />}
      style={{ height: '100%' }}
    >
      <div style={{ fontSize:9, lineHeight:1.45, color:T.txtMuted, marginBottom:10 }}>
        Scheduled-event context only. It cannot change the technical verdict.
      </div>
      <div style={{ padding:'9px 10px', borderRadius:7, background:`${color}0d`,
        border:`1px solid ${color}33`, marginBottom:9 }}>
        <div style={{ fontSize:11, fontWeight:700, color:T.txtPri, marginBottom:4, lineHeight:1.3 }}>
          {safeStr(fc.event_name, 'No in-scope event nearby')}
        </div>
        <div style={{ display:'flex', flexWrap:'wrap', gap:'4px 10px', fontSize:9.5 }}>
          <span style={{ color }}>PHASE · {phase}</span>
          <span style={{ color:T.txtSec }}>{timing}</span>
          {impact && <span style={{ color:T.txtMuted }}>{impact} IMPACT</span>}
        </div>
      </div>
      <KV label="Scheduled (ET)" value={eventTime} mono />
      <KV label="Source" value={safeStr(fc.source, 'Unavailable')} />
      <KV label="Cache" value={fc.stale === true ? 'STALE / UNAVAILABLE' : 'CACHED'} valueColor={fc.stale === true ? T.amber : T.green} />
      <div style={{ marginTop:9, fontSize:9, color:T.txtMuted, lineHeight:1.45 }}>
        {safeStr(fc.reason, 'No additional context')}
      </div>
      <div style={{ marginTop:8, fontSize:8.5, color:T.purple, letterSpacing:'0.05em', fontWeight:700 }}>
        SHADOW ONLY · NO VERDICT, RISK, OR EXECUTION EFFECT
      </div>
    </Panel>
  );
};

// ── Strategy Scanner Panel ────────────────────────────────────────────────────
const STRATEGY_LABELS: Record<string, string> = {
  // ── Live engine (STRATEGY_PRIORITY) ─────────────────────────────────
  OPENING_DRIVE:            'Opening Drive',
  LIQUIDITY_SWEEP_REVERSAL: 'Liquidity Sweep',
  VWAP_TREND_CONTINUATION:  'VWAP Continuation',
  RANGE_EXPANSION_BREAKOUT: 'Range Expansion',
  OPENING_RANGE_BREAKOUT:   'ORB',
  // ── Research-library graduates — scanner emits UPPERCASE, advisory lowercase ──
  COMPRESSION_BREAKOUT:         'Compression Breakout',
  VWAP_PULLBACK_CONTINUATION:   'VWAP Pullback',
  ORDER_BLOCK_REJECTION:        'OB Rejection',
  VWAP_RECLAIM_FAIL:            'VWAP Reclaim/Fail',
  // lowercase aliases (advisory votes; kept for parity while sim data accumulates)
  compression_breakout:         'Compression Breakout',
  vwap_pullback_continuation:   'VWAP Pullback',
  order_block_rejection:        'OB Rejection',
  vwap_reclaim_fail:            'VWAP Reclaim/Fail',
};

function completenessHint(pct: number, skipReason: string): string {
  if (skipReason) return skipReason.replace(/_/g, ' ');
  if (pct >= 80)  return 'Very close — watching for final trigger';
  if (pct >= 60)  return 'Setup building — needs a few more confirms';
  if (pct >= 40)  return 'Forming — incomplete setup';
  if (pct >= 20)  return 'Early stage — monitoring';
  return 'No signal yet';
}

const ScannerPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const sc       = (p.strategy_scanner ?? {}) as Record<string, unknown>;
  const verdict  = (p.verdict ?? {}) as Record<string, unknown>;
  const avail    = sc.available !== false;
  const sel      = safeStr(sc.selected_strategy, '');
  const strats   = Array.isArray(sc.strategies) ? sc.strategies as Record<string, unknown>[] : [];
  const isReady  = verdict.is_actionable === true;

  // Best candidate: highest completeness among eligible strategies
  const eligibles = strats.filter(s => s.eligible !== false);
  const best = eligibles.reduce<Record<string, unknown> | null>((acc, s) => {
    const aComp = safeNum(acc?.completeness) ?? 0;
    const sComp = safeNum(s.completeness) ?? 0;
    return sComp > aComp ? s : acc;
  }, null);
  const bestComp = safeNum(best?.completeness) ?? 0;
  const bestKey  = safeStr(best?.strategy_key ?? best?.key, '');
  const bestName = STRATEGY_LABELS[bestKey] ?? safeStr(best?.name, bestKey);

  return (
    <Panel title="Strategy Scanner" badge={sel ? <Badge label={STRATEGY_LABELS[sel] ?? sel} color={T.cyan} /> : undefined}>
      {!avail ? <UnavailableNote /> : strats.length === 0 ? <UnavailableNote msg="No strategies available" /> : (
        <div>

          {/* ── Best Opportunity card ─────────────────────────────────── */}
          {!isReady && best != null && bestComp > 0 && (
            <div style={{ marginBottom:12, padding:'10px 11px', background:`${T.cyan}08`, borderRadius:8, border:`1px solid ${T.cyan}20` }}>
              <div style={{ fontSize:9, color:T.cyan, letterSpacing:'0.08em', marginBottom:6 }}>BEST OPPORTUNITY</div>
              <div style={{ fontWeight:700, fontSize:11.5, color:T.txtPri, marginBottom:6, lineHeight:1.2 }}>
                {bestName}
              </div>
              <div style={{ display:'flex', alignItems:'center', gap:7, marginBottom:5 }}>
                <div style={{ flex:1, height:5, background:'rgba(255,255,255,0.07)', borderRadius:3 }}>
                  <div style={{ height:'100%', width:`${bestComp}%`, background:T.cyan, borderRadius:3, transition:'width 0.4s ease' }} />
                </div>
                <span style={{ fontSize:10.5, fontFamily:T.mono, color:T.cyan, fontWeight:700, flexShrink:0 }}>
                  {bestComp}%
                </span>
              </div>
              <div style={{ fontSize:9.5, color:T.txtMuted, lineHeight:1.4 }}>
                {completenessHint(bestComp, safeStr(best?.skip_reason, ''))}
              </div>
            </div>
          )}

          {/* ── Strategy rows ─────────────────────────────────────────── */}
          {strats.map((s, i) => {
            const key    = safeStr(s.key, '');
            const name   = STRATEGY_LABELS[key] ?? safeStr(s.name, key);
            const rdy    = safeStr(s.readiness, '');
            const isSel  = key === sel;
            const rCol   = readinessColor(rdy);
            const comp   = safeNum(s.completeness) ?? 0;
            const isElig = s.eligible !== false;
            const skipR  = safeStr(s.skip_reason, '');
            const dir    = safeStr(s.direction, '');
            const barCol = rdy === 'READY' ? T.green
              : isSel  ? T.cyan
              : isElig ? T.amber
              : 'rgba(255,255,255,0.12)';

            return (
              <div key={key || i} style={{
                padding:'8px 10px', marginBottom:5,
                background: isSel ? `${T.cyan}0e` : 'rgba(255,255,255,0.02)',
                borderRadius:7, border:`1px solid ${isSel ? T.cyan + '30' : T.border}`,
                opacity: isElig ? 1 : 0.5,
              }}>
                {/* Name + direction + badge row */}
                <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:5 }}>
                  {isSel && <span style={{ fontSize:9, color:T.cyan, flexShrink:0 }}>▶</span>}
                  <span style={{ fontSize:11, fontWeight:isSel ? 700 : 500, color:isSel ? T.cyan : isElig ? T.txtPri : T.txtMuted, flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                    {name}
                  </span>
                  {dir && <span style={{ fontSize:9, color:dirColor(dir), flexShrink:0 }}>{dir.toUpperCase()}</span>}
                  <Badge label={rdy || '—'} color={rCol} />
                  {s.mode_compatible === false && <Badge label="MODE" color={T.amber} />}
                </div>
                {/* Progress bar row */}
                <div style={{ display:'flex', alignItems:'center', gap:7 }}>
                  <div style={{ flex:1, height:4, background:'rgba(255,255,255,0.06)', borderRadius:2 }}>
                    <div style={{ height:'100%', width:`${comp}%`, background:barCol, borderRadius:2, transition:'width 0.4s ease' }} />
                  </div>
                  <span style={{ fontSize:9, fontFamily:T.mono, color:barCol, fontWeight:700, minWidth:32, textAlign:'right', flexShrink:0 }}>
                    {isElig ? `${comp}%` : skipR ? skipR.replace(/_/g,' ') : 'SKIP'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
};

// ── Consensus Panel ──────────────────────────────────────────────────────────
// Replaces the 3-panel top row (Thesis + Verdict + Scanner) with a single
// synthesised read: tallies weighted directional votes across all available
// signals, surfaces the one best setup that matches the majority view, and
// flags when the active trade plan is running against the consensus.
// ─────────────────────────────────────────────────────────────────────────────

interface ConsensusSignal { label: string; direction: 'LONG' | 'SHORT'; weight: number; }
interface ConsensusResult {
  direction: 'LONG' | 'SHORT' | 'DIVIDED';
  longVotes: number; shortVotes: number; totalVotes: number;
  confidence: number;
  signals: ConsensusSignal[];
}

function computeConsensus(p: Record<string, unknown>): ConsensusResult {
  const signals: ConsensusSignal[] = [];

  // Left Brain direction — 1 vote
  const lb = (p.left_brain ?? {}) as Record<string, unknown>;
  const lbDir = safeStr(lb.direction, '').toUpperCase() as 'LONG' | 'SHORT' | string;
  if (lbDir === 'LONG' || lbDir === 'SHORT')
    signals.push({ label: 'Left Brain', direction: lbDir, weight: 1 });

  // Verdict direction — 2 votes when actionable (it is the gate signal), 1 otherwise
  const vrd = (p.verdict ?? {}) as Record<string, unknown>;
  const vDir = safeStr(vrd.direction, '').toUpperCase() as 'LONG' | 'SHORT' | string;
  if (vDir === 'LONG' || vDir === 'SHORT')
    signals.push({ label: 'Gate Verdict', direction: vDir, weight: vrd.is_actionable === true ? 2 : 1 });

  // Candidate preview direction — 1 vote (only if it differs from verdict to avoid double-counting)
  const cp = (p.candidate_preview ?? {}) as Record<string, unknown>;
  const cpDir = safeStr(cp.direction ?? '', '').toUpperCase() as 'LONG' | 'SHORT' | string;
  if ((cpDir === 'LONG' || cpDir === 'SHORT') && cpDir !== vDir)
    signals.push({ label: 'Best Candidate', direction: cpDir, weight: 1 });

  // Each eligible strategy with a direction — 1 vote each
  const sc = (p.strategy_scanner ?? {}) as Record<string, unknown>;
  const strats = Array.isArray(sc.strategies) ? sc.strategies as Record<string, unknown>[] : [];
  for (const s of strats) {
    if (s.eligible === false) continue;
    const sDir = safeStr(s.direction, '').toUpperCase() as 'LONG' | 'SHORT' | string;
    if (sDir !== 'LONG' && sDir !== 'SHORT') continue;
    const key  = safeStr(s.key ?? s.strategy_key, '');
    const name = STRATEGY_LABELS[key] ?? safeStr(s.name, key || 'Strategy');
    signals.push({ label: name, direction: sDir, weight: 1 });
  }

  // Research advisory votes — now graduated to live engine (STRATEGY_PRIORITY), so
  // they already vote via sc.strategies above. Advisory votes kept as a tiebreaker
  // ONLY when the live scanner hasn't fired them (different threshold — advisory fires
  // on any lctx pass; scanner requires fully_met). To avoid double-counting, skip any
  // key whose UPPERCASE equivalent already voted in the scanner loop above.
  const scannedKeys = new Set(signals.map(s => s.label));
  const RESEARCH_VOTERS = new Set([
    'compression_breakout',
    'vwap_pullback_continuation',
    'order_block_rejection',
    'vwap_reclaim_fail',
  ]);
  const adv   = (p.scalp_strategy_advisory ?? {}) as Record<string, unknown>;
  const votes = Array.isArray(adv.votes) ? adv.votes as Record<string, unknown>[] : [];
  for (const v of votes) {
    const key = safeStr(v.strategy_key, '');
    if (!RESEARCH_VOTERS.has(key)) continue;
    if (!v.passed) continue;
    const raw  = safeStr(v.direction, '');
    const vDir = (raw.toUpperCase() === 'LONG' || raw === 'Long') ? 'LONG'
               : (raw.toUpperCase() === 'SHORT' || raw === 'Short') ? 'SHORT'
               : null;
    if (!vDir) continue;
    const label = STRATEGY_LABELS[key] ?? safeStr(v.name, key);
    if (scannedKeys.has(label)) continue;  // live scanner already voted this strategy
    signals.push({ label, direction: vDir, weight: 1 });
  }

  let longVotes = 0, shortVotes = 0;
  for (const sig of signals) {
    if (sig.direction === 'LONG') longVotes += sig.weight;
    else shortVotes += sig.weight;
  }
  const totalVotes = longVotes + shortVotes;
  if (totalVotes === 0) return { direction: 'DIVIDED', longVotes: 0, shortVotes: 0, totalVotes: 0, confidence: 0, signals };
  const maxV = Math.max(longVotes, shortVotes);
  const confidence = Math.round((maxV / totalVotes) * 100);
  // 65% threshold: a direction must hold a clear supermajority or we call it
  // DIVIDED. Raised from 60% to reduce noise when votes are close (e.g. 4 vs 3).
  const direction: 'LONG' | 'SHORT' | 'DIVIDED' =
    confidence < 65 ? 'DIVIDED'
    : longVotes > shortVotes ? 'LONG' : 'SHORT';
  return { direction, longVotes, shortVotes, totalVotes, confidence, signals };
}

const ConsensusPanel: React.FC<{ p: Record<string, unknown>; consensus: ConsensusResult }> = ({ p, consensus }) => {
  const [minorityOpen, setMinorityOpen] = useState(false);
  const { direction: cDir, longVotes, shortVotes, totalVotes, confidence, signals } = consensus;

  const sc     = (p.strategy_scanner ?? {}) as Record<string, unknown>;
  const strats = Array.isArray(sc.strategies) ? sc.strategies as Record<string, unknown>[] : [];

  const cColor  = cDir === 'LONG' ? T.green : cDir === 'SHORT' ? T.red : T.amber;
  const confCol = confidence >= 75 ? T.green : confidence >= 55 ? T.amber : T.red;

  // Best setup whose direction matches consensus
  const eligibles = strats.filter(s => s.eligible !== false);
  const matching  = cDir !== 'DIVIDED'
    ? eligibles.filter(s => safeStr(s.direction, '').toUpperCase() === cDir)
    : eligibles;
  const bestMatch = matching.reduce<Record<string, unknown> | null>((acc, s) => {
    const aComp = safeNum(acc?.completeness) ?? 0;
    const sComp = safeNum(s.completeness)   ?? 0;
    return sComp > aComp ? s : acc;
  }, null);

  // Minority signals
  const minority = cDir !== 'DIVIDED' ? signals.filter(s => s.direction !== cDir) : [];

  // Vote bar percentages
  const longPct  = totalVotes > 0 ? Math.round((longVotes  / totalVotes) * 100) : 50;
  const shortPct = 100 - longPct;

  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{
        background: T.panel, borderRadius: 10, padding: '16px 18px',
        border: `1px solid ${cColor}35`,
        boxShadow: `0 0 32px ${cColor}10, inset 0 0 0 1px rgba(255,255,255,0.03)`,
      }}>

        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:14 }}>
          <span style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.12em', fontWeight:700 }}>
            MARKET CONSENSUS
          </span>
          <span style={{ marginLeft:'auto', fontSize:9, color:T.txtMuted, fontFamily:T.mono }}>
            {totalVotes > 0 ? `${totalVotes} weighted signal${totalVotes !== 1 ? 's' : ''}` : 'No signals yet'}
          </span>
        </div>

        {/* ── Main 3-column body ─────────────────────────────────────────── */}
        <div style={{ display:'grid', gridTemplateColumns:'1.1fr 1.3fr 1fr', gap:16, alignItems:'start' }}
             className="mb-grid-3">

          {/* Column 1 — Direction hero + vote bars */}
          <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
            {/* Big direction label + confidence */}
            <div style={{ display:'flex', alignItems:'baseline', gap:10, flexWrap:'wrap' }}>
              <span style={{
                fontSize: 36, fontWeight: 900, letterSpacing: '-0.03em',
                color: cColor, lineHeight: 1, fontFamily: "'Inter', system-ui, sans-serif",
              }}>
                {cDir === 'DIVIDED' ? '—' : cDir}
              </span>
              {totalVotes > 0 && (
                <span style={{ fontSize: 15, fontWeight: 700, color: confCol, letterSpacing: '-0.01em' }}>
                  {confidence}%
                </span>
              )}
            </div>

            {/* Flavour text */}
            <div style={{ fontSize: 10, color: T.txtMuted, lineHeight: 1.45 }}>
              {cDir === 'DIVIDED'
                ? 'Signals are split — no clear bias yet'
                : cDir === 'LONG'
                  ? `${longVotes} of ${totalVotes} signal points favour Long`
                  : `${shortVotes} of ${totalVotes} signal points favour Short`
              }
            </div>

            {/* LONG / SHORT vote bars */}
            {totalVotes > 0 && (
              <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
                {([['LONG', T.green, longPct, longVotes], ['SHORT', T.red, shortPct, shortVotes]] as [string, string, number, number][]).map(([lbl, col, pct, v]) => (
                  <div key={lbl} style={{ display:'flex', alignItems:'center', gap:7 }}>
                    <span style={{ fontSize:8.5, color:col, width:36, textAlign:'right', flexShrink:0, letterSpacing:'0.06em', fontWeight:700 }}>{lbl}</span>
                    <div style={{ flex:1, height:5, background:'rgba(255,255,255,0.07)', borderRadius:3 }}>
                      <div style={{ height:'100%', width:`${pct}%`, background:col, borderRadius:3, transition:'width 0.5s ease' }} />
                    </div>
                    <span style={{ fontSize:9, color:col, fontFamily:T.mono, fontWeight:700, minWidth:16, flexShrink:0 }}>{v}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Column 2 — Best matching setup */}
          <div>
            <div style={{ fontSize:9, color:cColor, letterSpacing:'0.09em', marginBottom:8, fontWeight:700 }}>
              {cDir === 'DIVIDED' ? 'HIGHEST COMPLETENESS' : `BEST ${cDir} SETUP`}
            </div>
            {bestMatch ? (
              <div style={{
                background:`${cColor}09`, border:`1px solid ${cColor}28`,
                borderRadius:9, padding:'11px 13px',
              }}>
                <div style={{ fontWeight:700, fontSize:12.5, color:T.txtPri, marginBottom:9, lineHeight:1.25 }}>
                  {STRATEGY_LABELS[safeStr(bestMatch.key ?? bestMatch.strategy_key, '')] ?? safeStr(bestMatch.name, '—')}
                </div>
                {/* Completeness bar */}
                <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:8 }}>
                  <div style={{ flex:1, height:6, background:'rgba(255,255,255,0.07)', borderRadius:3 }}>
                    <div style={{
                      height:'100%', width:`${safeNum(bestMatch.completeness) ?? 0}%`,
                      background:cColor, borderRadius:3, transition:'width 0.5s ease',
                    }} />
                  </div>
                  <span style={{ fontSize:11, color:cColor, fontFamily:T.mono, fontWeight:700, flexShrink:0 }}>
                    {safeNum(bestMatch.completeness) ?? 0}%
                  </span>
                </div>
                {/* Badges row */}
                <div style={{ display:'flex', gap:5, flexWrap:'wrap', alignItems:'center' }}>
                  <Badge label={safeStr(bestMatch.readiness, 'WAIT')} color={readinessColor(safeStr(bestMatch.readiness, ''))} />
                  {!!(bestMatch.direction) && (
                    <Badge label={safeStr(bestMatch.direction, '').toUpperCase()} color={dirColor(safeStr(bestMatch.direction, ''))} />
                  )}
                  {safeStr(bestMatch.skip_reason, '') && (
                    <span style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.04em' }}>
                      {safeStr(bestMatch.skip_reason, '').replace(/_/g,' ')}
                    </span>
                  )}
                </div>
                {/* Hint */}
                {safeNum(bestMatch.completeness) !== 100 && (
                  <div style={{ marginTop:7, fontSize:9, color:T.txtMuted, lineHeight:1.4 }}>
                    {completenessHint(safeNum(bestMatch.completeness) ?? 0, safeStr(bestMatch.skip_reason, ''))}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ fontSize:10, color:T.txtMuted, padding:'10px 0' }}>
                No {cDir !== 'DIVIDED' ? cDir.toLowerCase() + ' ' : ''}strategies available yet
              </div>
            )}
          </div>

          {/* Column 3 — Signal tally */}
          <div>
            <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.09em', marginBottom:8, fontWeight:700 }}>
              SIGNALS
            </div>
            {signals.length === 0 ? (
              <span style={{ fontSize:10, color:T.txtMuted }}>Waiting for signals…</span>
            ) : (
              <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
                {signals.map((sig, i) => {
                  const col       = sig.direction === 'LONG' ? T.green : T.red;
                  const isAligned = cDir === 'DIVIDED' || sig.direction === cDir;
                  return (
                    <div key={i} style={{
                      display:'flex', alignItems:'center', gap:6,
                      opacity: isAligned ? 1 : 0.38,
                      padding:'3px 0',
                    }}>
                      <span style={{ fontSize:8, color:col, flexShrink:0, lineHeight:1 }}>
                        {sig.direction === 'LONG' ? '▲' : '▼'}
                      </span>
                      <span style={{ fontSize:10, color:isAligned ? T.txtPri : T.txtMuted, flex:1,
                                     overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', lineHeight:1.3 }}>
                        {sig.label}
                      </span>
                      {sig.weight > 1 && (
                        <span style={{ fontSize:8, color:T.txtMuted, flexShrink:0 }}>×{sig.weight}</span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* ── Minority view ──────────────────────────────────────────────── */}
        {minority.length > 0 && (
          <div style={{ marginTop:12, borderTop:`1px solid ${T.border}`, paddingTop:10 }}>
            <button
              onClick={() => setMinorityOpen(o => !o)}
              style={{ background:'none', border:'none', cursor:'pointer', padding:0,
                       display:'flex', alignItems:'center', gap:6 }}
            >
              <span style={{ fontSize:9, color:T.txtMuted }}>{minorityOpen ? '▾' : '▸'}</span>
              <span style={{ fontSize:9.5, color:T.txtMuted }}>
                {minority.length} signal{minority.length !== 1 ? 's' : ''}{' '}
                {cDir === 'LONG' ? 'see SHORT' : 'see LONG'}
                {!minorityOpen ? ' — tap to see' : ''}
              </span>
            </button>
            {minorityOpen && (
              <div style={{ marginTop:8, display:'flex', flexWrap:'wrap', gap:5 }}>
                {minority.map((sig, i) => {
                  const col = sig.direction === 'LONG' ? T.green : T.red;
                  return (
                    <span key={i} style={{
                      fontSize:9.5, color:col, background:`${col}10`,
                      border:`1px solid ${col}25`, borderRadius:4, padding:'3px 8px',
                    }}>
                      {sig.direction === 'LONG' ? '▲' : '▼'} {sig.label}
                    </span>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// ── Trade Plan Panel ──────────────────────────────────────────────────────────
// ── Cleanest Trade Available ──────────────────────────────────────────────────

type CleanestScanResult = {
  candidate:     CleanestCandidate | null;
  error:         string | null;
  allInputs:     RankInput[];
  scannedAt:     number;       // ms since epoch when scan completed
  scanStartedAt: number;       // ms since epoch when scan was initiated
  /** True when one or more of the 8 status requests failed. */
  isPartial: boolean;
  succeeded: number;           // how many of total responded OK
  total:     number;           // total attempted (normally 8)
  failed:    Array<{ instrument: string; mode: string }>;
};

// ── Cleanest Trade Modal ──────────────────────────────────────────────────────
const CleanestTradeModal: React.FC<{
  open:             boolean;
  onClose:          () => void;
  scanResult:       CleanestScanResult | null;
  activeInstrument: string;
  hasActiveTrade:   boolean;
}> = ({ open, onClose, scanResult, activeInstrument, hasActiveTrade }) => {
  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const cand  = scanResult?.candidate ?? null;
  const err   = scanResult?.error ?? null;
  const allInputs = scanResult?.allInputs ?? [];
  const age   = scanResult ? fmtAge(new Date(scanResult.scannedAt).toISOString()) : 'n/a';

  const plan  = cand ? getPlanFromRecord(cand.record, cand.direction) : null;
  const reasons = cand ? getRankingReasons(cand, allInputs) : [];

  const isReady = cand != null && cand.act === 1;
  const isPot   = cand != null && cand.act === 0;

  const titleColor = isReady ? T.green : isPot ? T.amber : T.txtMuted;
  const titleLabel = isReady ? '✓ READY TO TRADE'
                  : isPot   ? '⏳ POTENTIAL — NOT READY'
                  : err     ? '✗ SCAN UNAVAILABLE'
                             : '— NO QUALIFYING TRADE';

  // Active trade conflict: same instrument AND same direction as candidate
  const conflictWarning =
    hasActiveTrade && cand &&
    cand.instrument === activeInstrument;

  // Edge grade
  const edge  = cand?.edge ?? null;
  const grade = edge == null ? '—'
              : edge >= 85  ? 'A+'
              : edge >= 70  ? 'A'
              : edge >= 50  ? 'B'
              : 'WAIT';
  const gradeColor = edge == null ? T.txtMuted
                   : edge >= 85   ? T.green
                   : edge >= 70   ? '#4ade80'
                   : edge >= 50   ? T.amber
                   : T.red;

  // Strategy name
  const stratName = safeStr(
    (cand?.record.strategy_engine as Record<string, unknown> | undefined)?.active_strategy, '—'
  );

  // Blockers
  const blocker = safeStr(cand?.record.strict_reason, '');

  // Opposing structure
  const opp   = cand?.record.opposing_structure;
  const oppStr = opp && typeof opp === 'object' && (opp as Record<string, unknown>).side
    ? `${(opp as Record<string, unknown>).side} structure (${safeStr((opp as Record<string, unknown>).type, '?')})`
    : opp ? String(opp) : 'None detected';

  // Data freshness from the record
  const freshness = safeStr(cand?.record.generated_at, '');

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Cleanest Trade Available"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position:'fixed', inset:0, zIndex:999,
        background:'rgba(3,11,26,0.85)', backdropFilter:'blur(4px)',
        display:'flex', alignItems:'center', justifyContent:'center',
        padding:'20px 12px',
      }}
    >
      <div style={{
        background:'#08162a', border:`1px solid ${T.border}`, borderRadius:14,
        width:'100%', maxWidth:560, maxHeight:'90vh', overflowY:'auto',
        display:'flex', flexDirection:'column', boxShadow:`0 20px 60px rgba(0,0,0,0.7)`,
      }}>
        {/* Header */}
        <div style={{
          display:'flex', alignItems:'center', gap:10, padding:'14px 18px',
          borderBottom:`1px solid ${T.border}`,
          position:'sticky', top:0, background:'#08162a', zIndex:2,
        }}>
          <div style={{ flex:1 }}>
            <div style={{ fontSize:9, fontWeight:700, letterSpacing:'0.12em', color:T.txtMuted, textTransform:'uppercase', marginBottom:2 }}>
              Cleanest Trade Available
            </div>
            <div style={{ fontSize:13, fontWeight:800, color:titleColor, letterSpacing:'0.04em' }}>
              {titleLabel}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ background:'transparent', border:`1px solid ${T.border}`, color:T.txtSec,
              borderRadius:6, width:28, height:28, cursor:'pointer', fontSize:14, lineHeight:1,
              display:'flex', alignItems:'center', justifyContent:'center' }}
          >×</button>
        </div>

        {/* Body */}
        <div style={{ padding:'16px 18px', display:'flex', flexDirection:'column', gap:14 }}>

          {/* Error / empty */}
          {err && (
            <div style={{ background:`${T.red}12`, border:`1px solid ${T.red}33`, borderRadius:8,
              padding:'12px 14px', fontSize:11, color:T.red }}>
              {err}
            </div>
          )}
          {!err && !cand && (
            <div style={{ textAlign:'center', padding:'20px 0', color:T.txtMuted, fontSize:12 }}>
              No qualifying setup found across all scanned instruments and modes.
            </div>
          )}

          {/* Candidate summary */}
          {cand && (
            <>
              {/* ─ Active trade conflict ─ */}
              {conflictWarning && (
                <div style={{ background:`${T.amber}14`, border:`1px solid ${T.amber}44`, borderRadius:8,
                  padding:'10px 14px', fontSize:11, color:T.amber, display:'flex', gap:8, alignItems:'center' }}>
                  <span>⚠</span>
                  <span>An active {activeInstrument} trade is already open. This preview is informational only — it will not execute.</span>
                </div>
              )}

              {/* ─ Instrument / mode / direction header ─ */}
              <div style={{ background:`${T.panel}`, border:`1px solid ${T.border}`, borderRadius:10,
                padding:'12px 14px', display:'flex', alignItems:'center', gap:12, flexWrap:'wrap' }}>
                <div>
                  <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom:3 }}>Instrument · Mode</div>
                  <div style={{ fontSize:18, fontWeight:800, color:T.cyan, letterSpacing:'0.04em' }}>
                    {cand.instrument}
                    <span style={{ fontSize:11, fontWeight:600, color:T.txtSec, marginLeft:8 }}>{cand.mode}</span>
                  </div>
                </div>
                <div style={{ width:1, height:36, background:T.border }} />
                <div>
                  <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom:3 }}>Direction</div>
                  <Pill text={cand.direction} color={dirColor(cand.direction)} />
                </div>
                <div style={{ width:1, height:36, background:T.border }} />
                <div>
                  <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom:3 }}>Status</div>
                  <Pill text={isReady ? 'READY' : 'POTENTIAL'} color={isReady ? T.green : T.amber} />
                </div>
                <div style={{ marginLeft:'auto', textAlign:'center' }}>
                  <div style={{ fontSize:22, fontWeight:800, color:gradeColor, fontFamily:T.mono }}>{Math.round(cand.edge)}</div>
                  <div style={{ fontSize:8, color:T.txtMuted, letterSpacing:'0.1em' }}>/ 110</div>
                  <div style={{ fontSize:10, fontWeight:700, color:gradeColor, marginTop:2 }}>Grade {grade}</div>
                </div>
              </div>

              {/* ─ Strategy ─ */}
              {stratName !== '—' && (
                <div style={{ fontSize:11, color:T.txtSec }}>
                  <span style={{ color:T.txtMuted, fontSize:10 }}>Strategy: </span>{stratName}
                </div>
              )}

              {/* ─ Trade plan ─ */}
              {plan ? (
                <div>
                  <div style={{ fontSize:9.5, fontWeight:700, letterSpacing:'0.1em', textTransform:'uppercase',
                    color:T.txtMuted, marginBottom:8, display:'flex', alignItems:'center', gap:6 }}>
                    Trade Plan
                    {isPot && <span style={{ color:T.amber, fontWeight:600, fontSize:8.5 }}>(preview — not yet READY)</span>}
                  </div>
                  {plan.entry_zone  != null && <KV label="Entry Zone"    value={String(plan.entry_zone)}  mono valueColor={T.cyan}  />}
                  {plan.stop_loss   != null && <KV label="Stop Loss"     value={String(plan.stop_loss)}   mono valueColor={T.red}   />}
                  {plan.target1     != null && <KV label="Target 1"      value={String(plan.target1)}     mono valueColor={T.green} />}
                  {plan.target2     != null && <KV label="Target 2"      value={String(plan.target2)}     mono valueColor={T.green} />}
                  {plan.rr          != null && <KV label="R : R"         value={String(plan.rr)}          mono />}
                  {plan.risk_points != null && <KV label="Risk Points"   value={`${plan.risk_points} pts`} mono />}
                  {plan.risk_dollars_per_contract != null && (
                    <KV label="Risk / Contract"
                      value={`$${Number(plan.risk_dollars_per_contract).toFixed(2)}`}
                      mono valueColor={T.amber} />
                  )}
                  {plan.reward_points != null && plan.point_value != null && (
                    <KV label="Expected Profit / Contract"
                      value={`$${(Number(plan.reward_points) * Number(plan.point_value)).toFixed(2)}`}
                      mono valueColor={T.green} />
                  )}
                  {plan.atr_pts != null && <KV label="ATR (pts)"      value={`${plan.atr_pts}`}  mono />}
                </div>
              ) : (
                <div style={{ fontSize:11, color:T.txtMuted, fontStyle:'italic' }}>
                  No entry/stop plan available for this candidate.
                </div>
              )}

              {/* ─ Blockers ─ */}
              {blocker && (
                <div style={{ background:`${T.amber}10`, border:`1px solid ${T.amber}33`, borderRadius:8,
                  padding:'10px 14px' }}>
                  <div style={{ fontSize:9, color:T.amber, fontWeight:700, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom:4 }}>
                    Current Blockers
                  </div>
                  <div style={{ fontSize:11, color:T.txtSec }}>{blocker}</div>
                </div>
              )}

              {/* ─ Opposing structure ─ */}
              <KV label="Opposing Structure" value={oppStr} />

              {/* ─ Why this ranked first ─ */}
              <div style={{ background:`${T.cyan}08`, border:`1px solid ${T.cyan}22`, borderRadius:8, padding:'12px 14px' }}>
                <div style={{ fontSize:9, fontWeight:700, letterSpacing:'0.1em', textTransform:'uppercase', color:T.cyan, marginBottom:8 }}>
                  Why this candidate ranked first
                </div>
                <ul style={{ margin:0, padding:'0 0 0 16px', display:'flex', flexDirection:'column', gap:4 }}>
                  {reasons.map((r, i) => (
                    <li key={i} style={{ fontSize:10.5, color:T.txtSec, lineHeight:1.5 }}>{r}</li>
                  ))}
                </ul>
              </div>
            </>
          )}

          {/* ─ Partial scan banner ─ */}
          {scanResult?.isPartial && (
            <div style={{ background:`${T.amber}12`, border:`1px solid ${T.amber}44`, borderRadius:8,
              padding:'10px 14px', fontSize:11 }}>
              <div style={{ fontWeight:700, color:T.amber, letterSpacing:'0.06em', marginBottom:4 }}>
                ⚠ PARTIAL SCAN — {scanResult.succeeded} of {scanResult.total} markets evaluated
              </div>
              <div style={{ color:T.txtSec, fontSize:10 }}>
                {scanResult.failed.map(f => `${f.instrument} ${f.mode}`).join(' · ')} unavailable
              </div>
              <div style={{ color:T.txtMuted, fontSize:9.5, marginTop:4 }}>
                Ranking is based on the {scanResult.succeeded} responding candidates only.
                Re-scan when connectivity improves to evaluate the full universe.
              </div>
            </div>
          )}

          {/* ─ Scan metadata / freshness ─ */}
          {(() => {
            const nowMs = Date.now();
            const candidateAge = (cand?.record?.generated_at)
              ? Math.round((nowMs - new Date(cand.record.generated_at).getTime()) / 1000)
              : null;
            const isStale  = candidateAge != null && candidateAge > 30;
            const scanComplete = scanResult?.isPartial === false ? 'COMPLETE' : scanResult?.isPartial ? 'PARTIAL' : '—';
            const scanDuration = scanResult
              ? `${Math.round(scanResult.scannedAt - scanResult.scanStartedAt)}ms`
              : '—';
            return (
              <div style={{ borderTop:`1px solid ${T.border}`, paddingTop:10, display:'flex', flexDirection:'column', gap:4 }}>
                <div style={{ display:'flex', justifyContent:'space-between', fontSize:9, color:T.txtMuted }}>
                  <span>Scan: <strong style={{ color:T.txtSec }}>{scanComplete}</strong></span>
                  <span>Duration: {scanDuration}</span>
                  <span>Completed {age}</span>
                </div>
                {cand && (
                  <div style={{ display:'flex', justifyContent:'space-between', fontSize:9 }}>
                    <span style={{ color:T.txtMuted }}>
                      Data snapshot:{' '}
                      {freshness
                        ? <span style={{ color: isStale ? T.amber : T.txtSec }}>{freshness}</span>
                        : <span style={{ color:T.txtMuted }}>—</span>
                      }
                    </span>
                    {candidateAge != null && (
                      <span style={{ color: isStale ? T.amber : T.txtMuted, fontWeight: isStale ? 700 : 400 }}>
                        {isStale ? '⚠ STALE' : `${candidateAge}s old`}
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      </div>
    </div>
  );
};

// ── Cleanest Trade Button strip ────────────────────────────────────────────────
// ── Mode Overview Panel ───────────────────────────────────────────────────────
/** Persistent always-on strip showing the best setup in each trading mode
 *  (SCALP / INTRADAY / SWING) for the currently selected instrument.
 *  Display-only — no execution, no gate changes. Polls every 30 s. */
const ModeOverviewPanel: React.FC<{ ticker: string; authHeader: string }> = ({ ticker, authHeader }) => {
  type ModeRow = { verdict: string; edge: number; reason: string | null; ok: boolean };
  const [rows, setRows]           = React.useState<Record<string, ModeRow>>({});
  const [loading, setLoading]     = React.useState(false);
  const [fetchedAt, setFetchedAt] = React.useState<number | null>(null);

  const MODES_CFG = [
    { key: 'SCALP',          label: 'Scalp'    },
    { key: 'INTRADAY_TREND', label: 'Intraday' },
    { key: 'SWING',          label: 'Swing'    },
  ] as const;

  const ACTIONABLE_MO = new Set([
    'LONG READY', 'SHORT READY',
    'LONG EARLY READY', 'SHORT EARLY READY',
    'LONG READY_REDUCED', 'SHORT READY_REDUCED',
  ]);

  const doFetch = React.useCallback(async () => {
    if (!ticker) return;
    setLoading(true);
    const results = await Promise.allSettled(
      MODES_CFG.map(({ key }) =>
        fetch(`/api/status?ticker=${encodeURIComponent(ticker)}&mode=${key}`, {
          headers: { Authorization: authHeader },
        }).then(r => (r.ok ? r.json() : null)).catch(() => null)
      )
    );
    const next: Record<string, ModeRow> = {};
    MODES_CFG.forEach(({ key }, i) => {
      const d = results[i].status === 'fulfilled' ? (results[i] as PromiseFulfilledResult<unknown>).value : null;
      if (!d || typeof d !== 'object') {
        next[key] = { verdict: 'WAIT', edge: 0, reason: null, ok: false };
        return;
      }
      const rec = d as Record<string, unknown>;
      const brain = rec.brain as Record<string, unknown> | undefined;
      const dec   = (brain?.decision as Record<string, unknown> | undefined);
      const scr   = (brain?.score   as Record<string, unknown> | undefined);
      const verdict = String(dec?.verdict ?? rec.verdict ?? 'WAIT');
      const edge    = Number(scr?.value   ?? rec.edge_score ?? 0);
      const reason  = typeof rec.strict_reason === 'string' ? rec.strict_reason : null;
      next[key] = { verdict, edge, reason, ok: true };
    });
    setRows(next);
    setLoading(false);
    setFetchedAt(Date.now());
  }, [ticker, authHeader]); // eslint-disable-line react-hooks/exhaustive-deps

  React.useEffect(() => {
    doFetch();
    const id = window.setInterval(doFetch, 30_000);
    return () => window.clearInterval(id);
  }, [doFetch]);

  // Pick the single best row (actionable first, then highest edge)
  const bestKey = MODES_CFG.reduce<string | null>((best, { key }) => {
    const r = rows[key];
    if (!r?.ok) return best;
    if (!best)  return key;
    const br      = rows[best];
    const candAct = ACTIONABLE_MO.has(r.verdict)  ? 1 : 0;
    const bestAct = ACTIONABLE_MO.has(br.verdict) ? 1 : 0;
    if (candAct !== bestAct) return candAct > bestAct ? key : best;
    return r.edge > br.edge ? key : best;
  }, null);

  const verdictCol = (v: string): string => {
    if (/READY_REDUCED/.test(v)) return T.cyan;
    if (/EARLY/.test(v))         return T.amber;
    if (/READY/.test(v))         return T.green;
    return T.txtMuted;
  };
  const verdictLabel = (v: string): string => {
    const dir = /LONG/.test(v) ? '▲ ' : /SHORT/.test(v) ? '▼ ' : '';
    if (/READY_REDUCED/.test(v)) return `${dir}READY ½`;
    if (/EARLY READY/.test(v))   return `${dir}EARLY`;
    if (/READY/.test(v))         return `${dir}READY`;
    return 'WAIT';
  };

  const anyData = Object.keys(rows).length > 0;

  return (
    <div style={{
      marginBottom: 12,
      border:       `1px solid ${T.border}`,
      borderRadius: 10,
      overflow:     'hidden',
      background:   `${T.bg}`,
    }}>
      {/* Header */}
      <div style={{
        padding:       '6px 12px',
        borderBottom:  `1px solid ${T.border}`,
        display:       'flex',
        justifyContent:'space-between',
        alignItems:    'center',
        background:    `${T.border}28`,
      }}>
        <span style={{ fontSize:10, fontWeight:700, letterSpacing:'0.08em', color:T.txtMuted }}>
          MODE OVERVIEW · {ticker}
        </span>
        <span style={{ fontSize:9, color:`${T.txtMuted}70` }}>
          {loading ? 'scanning…' : fetchedAt ? `${Math.round((Date.now() - fetchedAt) / 1000)}s ago` : '—'}
        </span>
      </div>

      {/* Mode rows */}
      {MODES_CFG.map(({ key, label }, idx) => {
        const r      = rows[key];
        const isBest = key === bestKey && r?.ok && ACTIONABLE_MO.has(r?.verdict ?? '');
        const isAct  = r ? ACTIONABLE_MO.has(r.verdict) : false;
        const col    = r ? verdictCol(r.verdict) : T.txtMuted;
        const isLast = idx === MODES_CFG.length - 1;
        return (
          <div key={key} style={{
            display:             'grid',
            gridTemplateColumns: '60px 82px 54px 1fr',
            alignItems:          'center',
            columnGap:           8,
            padding:             '8px 12px',
            borderBottom:        isLast ? 'none' : `1px solid ${T.border}40`,
            background:          isBest ? `${col}0c` : 'transparent',
            transition:          'background 0.3s',
          }}>
            {/* Col 1 — Mode label */}
            <span style={{
              fontSize:      9.5,
              fontWeight:    700,
              color:         T.txtMuted,
              letterSpacing: '0.07em',
              textTransform: 'uppercase',
            }}>{label}</span>

            {/* Col 2 — Verdict */}
            <span style={{
              fontSize:   11,
              fontWeight: 700,
              color:      r?.ok ? col : `${T.txtMuted}60`,
            }}>
              {!anyData ? '—' : !r?.ok ? 'N/A' : verdictLabel(r.verdict)}
            </span>

            {/* Col 3 — Edge score */}
            <span style={{ fontSize:10, color: r?.ok ? col : T.txtMuted, opacity:0.75 }}>
              {r?.ok && r.edge > 0 ? `${Math.round(r.edge)}/110` : '—'}
            </span>

            {/* Col 4 — Reason or Best badge */}
            <span style={{
              fontSize:     9.5,
              color:        isBest ? col : T.txtMuted,
              overflow:     'hidden',
              textOverflow: 'ellipsis',
              whiteSpace:   'nowrap',
              opacity:      isBest ? 1 : 0.65,
              fontWeight:   isBest ? 700 : 400,
            }}>
              {isBest
                ? '★ BEST'
                : (!isAct && r?.reason ? r.reason : '')}
            </span>
          </div>
        );
      })}
    </div>
  );
};


// ── Gate Effectiveness Audit Panel ───────────────────────────────────────────
// Polls /api/gate-effectiveness/mode-report for SCALP and INTRADAY_TREND and
// presents the per-gate category breakdown table + component pass rates.
// DISPLAY-ONLY — never touches gate rules, thresholds, or execution.
// ── Visual Brain Panel ────────────────────────────────────────────────────────
const VisualBrainPanel: React.FC<{ authHeader: string }> = ({ authHeader }) => {
  type ModeAssessment = {
    posture: unknown; setup_status: unknown; confidence: unknown;
    validation: unknown; invalidation: unknown; reason: unknown;
    timeframe_alignment?: unknown; market_phase?: unknown; session_level?: unknown;
    thesis_quality?: unknown; structural_stop?: unknown; target_context?: unknown;
  };
  type MarketTimeframe = {
    bars?: number; bias?: string; confidence?: number; last_close?: number | null;
  };
  type MarketContext = {
    source?: string; bias?: string; alignment?: string; price?: number | null;
    vwap?: number | null; session_high?: number | null; session_low?: number | null;
    timeframes?: Record<string, MarketTimeframe>;
  };
  type VBObs = {
    timestamp: string; instrument: string; bias: string; market_state: string;
    short_term_structure: string | null; last_event: string; action: string;
    confidence: number; support_description: string; support_price: number | null;
    resistance_description: string; resistance_price: number | null;
    long_condition: string; short_condition: string;
    state_changed: boolean; state_change_reason: string; summary: string;
    p1m: number | null; p3m: number | null; p5m: number | null;
    p10m: number | null; p15m: number | null; mfe: number | null; mae: number | null;
    outcome_resolved: boolean;
    mode_assessments?: Partial<Record<'scalp' | 'intraday_trend' | 'swing', ModeAssessment>>;
    market_context?: MarketContext;
  };
  type AllStatus = {
    enabled: boolean; db_ready: boolean; symbols: string[];
    instruments: Record<string, { observation: VBObs | null }>;
    cost: { calls_today: number; cost_today_usd: number };
  };

  const [allStatus, setAllStatus] = React.useState<AllStatus | null>(null);
  const [activeTab, setActiveTab] = React.useState<string>('');
  const [hist, setHist]     = React.useState<VBObs[]>([]);
  const [histInst, setHistInst] = React.useState<string>('');
  const [lastFetch, setLastFetch] = React.useState<number | null>(null);
  const [open, setOpen]     = React.useState(true);
  const [histOpen, setHistOpen] = React.useState(false);
  const [endpointError, setEndpointError] = React.useState<string | null>(null);

  // Fetch all-status + history for active tab
  const load = React.useCallback(async () => {
    if (!open) return;
    try {
      const hdr = { Authorization: authHeader };
      const sr = await fetch('/api/visual-brain/all-status', { headers: hdr })
        .then(r => r.ok ? r.json() : null).catch(() => null);
      if (!sr) {
        setAllStatus(null);
        setHist([]);
        setLastFetch(null);
        setEndpointError('Visual Brain status endpoint is unavailable');
        return;
      }
      setAllStatus(sr as AllStatus);
      setLastFetch(Date.now());
      setEndpointError(null);
      // Set initial tab to first symbol with data, or first symbol
      setActiveTab(prev => {
        if (prev) return prev;
        const syms: string[] = (sr as AllStatus).symbols ?? [];
        return syms[0] ?? '';
      });
    } catch {
      setAllStatus(null);
      setHist([]);
      setLastFetch(null);
      setEndpointError('Visual Brain status endpoint is unavailable');
    }
  }, [open, authHeader]);

  // Fetch history when active tab changes
  const loadHist = React.useCallback(async (inst: string) => {
    if (!inst || !open) return;
    try {
      const hdr = { Authorization: authHeader };
      const hr = await fetch(`/api/visual-brain/history?instrument=${encodeURIComponent(inst)}&limit=10`, { headers: hdr })
        .then(r => r.ok ? r.json() : null).catch(() => null);
      if (hr) {
        setHist((hr as Record<string, unknown>).history as VBObs[] ?? []);
        setHistInst(inst);
      } else {
        setHist([]);
        setHistInst(inst);
      }
    } catch {
      setHist([]);
      setHistInst(inst);
    }
  }, [open, authHeader]);

  React.useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  React.useEffect(() => {
    if (activeTab && activeTab !== histInst) loadHist(activeTab);
  }, [activeTab, histInst, loadHist]);

  // When all-status refreshes, reload history if tab changed
  React.useEffect(() => {
    if (activeTab) loadHist(activeTab);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allStatus]);

  const biasColor = (b: string | null | undefined) => {
    const s = (b ?? '').toUpperCase();
    if (s === 'BULLISH') return T.green;
    if (s === 'BEARISH') return T.red;
    return T.amber;
  };
  const actionColor = (a: string | null | undefined) => {
    const s = (a ?? '').toUpperCase();
    if (s === 'LONG_WATCH') return T.green;
    if (s === 'SHORT_WATCH') return T.red;
    if (s === 'WAIT') return T.amber;
    return T.txtMuted;
  };
  const stateColor = (s: string | null | undefined) => {
    const v = (s ?? '').toUpperCase();
    if (['TRENDING_UP', 'BREAKOUT', 'RECLAIM'].some(x => v.includes(x))) return T.green;
    if (['TRENDING_DOWN', 'BREAKDOWN', 'REVERSAL'].some(x => v.includes(x))) return T.red;
    if (v === 'CHOP' || v === 'UNCLEAR') return T.txtMuted;
    return T.cyan;
  };
  const modePostureColor = (posture: unknown) => {
    const p = visualBrainText(posture, '').toUpperCase();
    if (p === 'LONG_BIAS') return T.green;
    if (p === 'SHORT_BIAS') return T.red;
    return T.txtMuted;
  };
  const modeSetupColor = (status: unknown) => {
    const s = visualBrainText(status, '').toUpperCase();
    if (s === 'TRIGGER_READY') return T.green;
    if (s === 'FORMING') return T.amber;
    return T.txtMuted;
  };
  const fmtPct = (v: number | null | undefined) =>
    v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(3)}%`;
  const fmtContextPrice = (v: number | null | undefined) =>
    typeof v === 'number' && Number.isFinite(v)
      ? v.toLocaleString('en-US', { maximumFractionDigits: 2 })
      : '—';

  const enabled  = allStatus?.enabled ?? null;
  const dbReady  = allStatus?.db_ready ?? false;
  const symbols  = allStatus?.symbols ?? [];
  const obsMap   = allStatus?.instruments ?? {};
  const rawObs   = activeTab ? (obsMap[activeTab]?.observation ?? null) : null;
  const obsFreshness = classifyVisualBrainFreshness(rawObs?.timestamp);
  // Never present a persisted observation as current after its observation window.
  const obs      = obsFreshness.current ? rawObs : null;
  const transitions = hist.filter(h => h.state_changed);
  const modeAssessments = obs?.mode_assessments ?? {};
  const marketContext = obs?.market_context;
  const tfBars = (timeframe: string) =>
    marketContext?.timeframes?.[timeframe]?.bars ?? 0;
  const modeDataReadiness = (mode: 'scalp' | 'intraday_trend' | 'swing') => {
    if (mode === 'scalp') {
      return {
        label: tfBars('1m') >= 5 && tfBars('5m') >= 2 ? 'LIVE 1m / 5m' : 'WARMING UP',
        ready: tfBars('1m') >= 5 && tfBars('5m') >= 2,
        detail: `${tfBars('1m')}× 1m · ${tfBars('5m')}× 5m bars`,
      };
    }
    if (mode === 'intraday_trend') {
      return {
        label: tfBars('15m') >= 3 && tfBars('1h') >= 2 ? 'HTF READY' : 'HTF WARMING',
        ready: tfBars('15m') >= 3 && tfBars('1h') >= 2,
        detail: `${tfBars('15m')}× 15m · ${tfBars('1h')}× 1h bars`,
      };
    }
    return {
      label: tfBars('1h') >= 4 && tfBars('4h') >= 3 && tfBars('1D') >= 2 ? 'SWING READY' : 'SWING WARMING',
      ready: tfBars('1h') >= 4 && tfBars('4h') >= 3 && tfBars('1D') >= 2,
      detail: `${tfBars('1h')}× 1h · ${tfBars('4h')}× 4h · ${tfBars('1D')}× 1D bars`,
    };
  };

  const ModeAssessmentCard: React.FC<{
    label: string; assessment?: ModeAssessment; details: Array<[string, unknown]>;
  }> = ({ label, assessment, details }) => {
    if (!assessment) {
      return (
        <div style={{ background: T.panelAlt, borderRadius: 8, padding: '10px 11px', border: `1px solid ${T.border}` }}>
          <div style={{ fontSize: 9, fontWeight: 700, color: T.txtSec, letterSpacing: '0.08em' }}>{label}</div>
          <div style={{ fontSize: 10, color: T.txtMuted, lineHeight: 1.45, marginTop: 8 }}>
            Available on new observations.
          </div>
        </div>
      );
    }
    const postureColor = modePostureColor(assessment.posture);
    const setupColor = modeSetupColor(assessment.setup_status);
    const posture = visualBrainToken(assessment.posture);
    const setupStatus = visualBrainToken(assessment.setup_status);
    return (
      <div style={{ background: T.panelAlt, borderRadius: 8, padding: '10px 11px',
        border: `1px solid ${postureColor}33`, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 8 }}>
          <span style={{ fontSize: 9, fontWeight: 800, color: T.txtSec, letterSpacing: '0.08em', flex: 1 }}>{label}</span>
          <Badge label={setupStatus} color={setupColor} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 10, marginBottom: 7 }}>
          <span style={{ color: postureColor, fontWeight: 700 }}>{posture}</span>
          <span style={{ color: T.txtMuted }}>{visualBrainConfidence(assessment.confidence)}</span>
        </div>
        {details.filter(([, value]) => visualBrainText(value, '')).map(([name, value]) => (
          <div key={name} style={{ fontSize: 9.5, color: T.txtMuted, marginBottom: 3, lineHeight: 1.35 }}>
            <span style={{ color: T.txtSec }}>{name}: </span>{visualBrainText(value)}
          </div>
        ))}
        <div style={{ marginTop: 7, paddingTop: 7, borderTop: `1px solid ${T.border}`, fontSize: 9.5, color: T.txtSec, lineHeight: 1.4 }}>
          {visualBrainText(assessment.reason)}
        </div>
        <div style={{ marginTop: 7, fontSize: 9.5, color: T.green, lineHeight: 1.35 }}>
          <span style={{ color: T.txtMuted }}>Validate: </span>{visualBrainText(assessment.validation)}
        </div>
        <div style={{ marginTop: 4, fontSize: 9.5, color: T.red, lineHeight: 1.35 }}>
          <span style={{ color: T.txtMuted }}>Invalidates: </span>{visualBrainText(assessment.invalidation)}
        </div>
      </div>
    );
  };

  const ModeLens: React.FC<{
    mode: 'scalp' | 'intraday_trend' | 'swing';
    label: string;
    horizon: string;
    assessment?: ModeAssessment;
  }> = ({ mode, label, horizon, assessment }) => {
    const posture = visualBrainToken(assessment?.posture);
    const status = visualBrainToken(assessment?.setup_status);
    const postureColor = modePostureColor(assessment?.posture);
    const setupColor = modeSetupColor(assessment?.setup_status);
    const readiness = modeDataReadiness(mode);
    return (
      <div style={{
        minWidth: 0, padding: '10px 11px', borderRadius: 8,
        background: `${postureColor}0b`,
        border: `1px solid ${postureColor}55`,
        borderLeft: `3px solid ${postureColor}`,
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 7 }}>
          <span style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', color: T.txtPri }}>{label}</span>
          <span style={{ fontSize: 8.5, color: T.txtMuted, letterSpacing: '0.04em' }}>{horizon}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 800, color: setupColor }}>{status}</span>
          <span style={{ fontSize: 10, fontWeight: 800, color: postureColor }}>{posture}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginTop: 6 }}>
          <span style={{ fontSize: 9.5, color: T.txtSec }}>{visualBrainConfidence(assessment?.confidence)}</span>
          <span style={{ fontSize: 8.5, color: readiness.ready ? T.green : T.amber, textAlign: 'right' }}>
            {readiness.label}
          </span>
        </div>
        <div style={{ marginTop: 4, fontSize: 8.5, color: T.txtMuted }}>{readiness.detail}</div>
      </div>
    );
  };

  // Compact bias chip for tabs — shows bias of each instrument at a glance
  const biasDot = (inst: string) => {
    const o = obsMap[inst]?.observation;
    if (!o || !classifyVisualBrainFreshness(o.timestamp).current) return null;
    const col = biasColor(o.bias);
    return <span style={{ width: 6, height: 6, borderRadius: '50%', background: col,
      display: 'inline-block', marginLeft: 4, flexShrink: 0 }} />;
  };

  return (
    <section id="mod-visual-brain" style={{
      background: T.panel, border: `1px solid ${T.border}`, borderRadius: 10,
      overflow: 'hidden', marginBottom: 12,
    }} aria-label="Visual Brain">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px',
        borderBottom: `1px solid ${T.border}`, background: 'rgba(255,255,255,0.015)',
        cursor: 'pointer' }}
        onClick={() => setOpen(o => !o)}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em',
          textTransform: 'uppercase', color: T.txtSec, flex: 1 }}>
          👁 Visual Brain
          {symbols.length > 0 && (
            <span style={{ color: T.txtMuted, fontWeight: 400 }}> — {symbols.join(' · ')}</span>
          )}
        </span>
        {enabled !== null && (
          <Badge label={enabled ? 'ENABLED' : 'DISABLED'}
            color={enabled ? T.green : T.txtMuted} />
        )}
        {dbReady && <Badge label="DB OK" color={T.green} />}
        {rawObs && <Badge label={`DATA ${obsFreshness.state}`} color={obsFreshness.current ? T.green : T.amber} />}
        {allStatus?.cost && (
          <span style={{ fontSize: 9, color: T.txtMuted }}>
            {allStatus.cost.calls_today} calls · ${allStatus.cost.cost_today_usd.toFixed(4)}
          </span>
        )}
        {lastFetch && (
          <span style={{ fontSize: 9, color: T.txtMuted }}>{fmtAge(new Date(lastFetch).toISOString())}</span>
        )}
        <span style={{ fontSize: 11, color: T.txtMuted }}>{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div style={{ padding: '12px 14px' }}>
          {enabled === null ? (
            <div style={{ textAlign: 'center', padding: '20px 0', color: T.txtMuted, fontSize: 11 }}>
              Loading…
            </div>
          ) : enabled === false ? (
            <div style={{ textAlign: 'center', padding: '20px 0', color: T.txtMuted, fontSize: 11 }}>
              Visual Brain is disabled.<br />
              <span style={{ fontSize: 10 }}>Set <code style={{ fontFamily: T.mono, color: T.cyan }}>VISUAL_BRAIN_ENABLED=true</code> to activate.</span>
            </div>
          ) : (
            <>
              {/* ── Instrument tabs ── */}
              {symbols.length > 1 && (
                <div style={{ display: 'flex', gap: 4, marginBottom: 12, borderBottom: `1px solid ${T.border}`, paddingBottom: 8 }}>
                  {symbols.map(inst => {
                    const isActive = inst === activeTab;
                    const instObs = obsMap[inst]?.observation;
                    const instBias = instObs && classifyVisualBrainFreshness(instObs.timestamp).current ? instObs.bias : null;
                    return (
                      <button key={inst}
                        onClick={() => setActiveTab(inst)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 5,
                          padding: '4px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 11,
                          fontWeight: isActive ? 700 : 500,
                          background: isActive ? `${T.cyan}18` : 'transparent',
                          border: isActive ? `1px solid ${T.cyan}55` : `1px solid ${T.border}`,
                          color: isActive ? T.cyan : T.txtSec,
                          transition: 'all 0.15s',
                        }}>
                        {inst}
                        {biasDot(inst)}
                        {instBias && (
                          <span style={{ fontSize: 8.5, color: biasColor(instBias), marginLeft: 2 }}>
                            {instBias.slice(0, 4)}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}

              {!obs ? (
                <div style={{ textAlign: 'center', padding: '20px 0', color: T.txtMuted, fontSize: 11 }}>
                  {rawObs && !obsFreshness.current ? (
                    <>
                      <div style={{ color: T.amber, fontWeight: 700 }}>OBSERVATION {obsFreshness.state}</div>
                      <div style={{ marginTop: 5 }}>
                        Source: {rawObs.market_context?.source ?? 'Visual Brain'} · Freshness: {formatFreshnessAge(obsFreshness.ageMs)}.
                        Bias, state, and action are hidden until a current observation arrives.
                      </div>
                    </>
                  ) : endpointError ? endpointError : dbReady ? `Awaiting first observation for ${activeTab || 'instrument'}…` : 'DB table not ready — apply migration first.'}
                </div>
              ) : (
                <>
                  {/* ── Main state row ── */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 12 }}>
                    {/* Bias */}
                    <div style={{ background: T.panelAlt, borderRadius: 8, padding: '10px 12px',
                      border: `1px solid ${biasColor(obs.bias)}33`, textAlign: 'center' }}>
                      <div style={{ fontSize: 8.5, color: T.txtMuted, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 4 }}>Bias</div>
                      <div style={{ fontSize: 18, fontWeight: 800, color: biasColor(obs.bias) }}>{obs.bias ?? '—'}</div>
                    </div>
                    {/* State */}
                    <div style={{ background: T.panelAlt, borderRadius: 8, padding: '10px 12px',
                      border: `1px solid ${stateColor(obs.market_state)}33`, textAlign: 'center' }}>
                      <div style={{ fontSize: 8.5, color: T.txtMuted, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 4 }}>State</div>
                      <div style={{ fontSize: 11, fontWeight: 700, color: stateColor(obs.market_state) }}>
                        {(obs.market_state ?? '—').replace(/_/g, ' ')}
                      </div>
                      {obs.short_term_structure && (
                        <div style={{ fontSize: 9, color: T.txtSec, marginTop: 2 }}>
                          {obs.short_term_structure.replace(/_/g, '/')}
                        </div>
                      )}
                    </div>
                    {/* Action + Confidence */}
                    <div style={{ background: T.panelAlt, borderRadius: 8, padding: '10px 12px',
                      border: `1px solid ${actionColor(obs.action)}33`, textAlign: 'center' }}>
                      <div style={{ fontSize: 8.5, color: T.txtMuted, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 4 }}>Action</div>
                      <div style={{ fontSize: 13, fontWeight: 800, color: actionColor(obs.action) }}>
                        {(obs.action ?? '—').replace(/_/g, ' ')}
                      </div>
                      <div style={{ fontSize: 10, color: actionColor(obs.action), marginTop: 2 }}>
                        {obs.confidence ?? 0}% conf
                      </div>
                    </div>
                  </div>

                  {/* ── Mode lenses — lead with the independent mode reads ── */}
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 7 }}>
                      <span style={{ fontSize: 9.5, fontWeight: 800, color: T.txtSec,
                        letterSpacing: '0.09em', textTransform: 'uppercase' }}>
                        Mode Assessment Contrast
                      </span>
                      <Badge label="ADVISORY ONLY" color={T.purple} />
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 8 }}>
                      <ModeLens mode="scalp" label="SCALP" horizon="1m → 5m" assessment={modeAssessments.scalp} />
                      <ModeLens mode="intraday_trend" label="INTRADAY TREND" horizon="15m → 1h" assessment={modeAssessments.intraday_trend} />
                      <ModeLens mode="swing" label="SWING" horizon="1h → 4h → 1D" assessment={modeAssessments.swing} />
                    </div>
                    {!modeDataReadiness('intraday_trend').ready || !modeDataReadiness('swing').ready ? (
                      <div style={{ marginTop: 7, padding: '6px 8px', borderRadius: 5, background: `${T.amber}0d`,
                        border: `1px solid ${T.amber}2c`, fontSize: 9.5, color: T.txtMuted, lineHeight: 1.4 }}>
                        Higher-timeframe assessments are deliberately conservative while native bars rebuild after a restart.
                        SCALP can read current price action first; INTRADAY TREND and SWING gain conviction as their larger windows fill.
                      </div>
                    ) : null}
                  </div>

                  {/* ── Last Event ── */}
                  <KV label="Last Event" value={
                    <Pill text={(obs.last_event ?? 'NONE').replace(/_/g, ' ')}
                      color={obs.last_event === 'NONE' ? T.txtMuted : T.cyan} />
                  } />

                  {/* ── Support / Resistance ── */}
                  <KV label="Support" value={
                    <span style={{ color: T.green, fontSize: 11 }}>
                      {obs.support_description || '—'}
                      {obs.support_price != null && ` @ ${obs.support_price.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 2 })}`}
                    </span>
                  } />
                  <KV label="Resistance" value={
                    <span style={{ color: T.red, fontSize: 11 }}>
                      {obs.resistance_description || '—'}
                      {obs.resistance_price != null && ` @ ${obs.resistance_price.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 2 })}`}
                    </span>
                  } />

                  {/* ── Conditions ── */}
                  {obs.long_condition && (
                    <KV label="Long When" value={<span style={{ color: T.green, fontSize: 10.5 }}>{obs.long_condition}</span>} />
                  )}
                  {obs.short_condition && (
                    <KV label="Short When" value={<span style={{ color: T.red, fontSize: 10.5 }}>{obs.short_condition}</span>} />
                  )}

                  {/* ── Summary ── */}
                  {obs.summary && (
                    <div style={{ margin: '10px 0', padding: '8px 10px', background: T.panelAlt,
                      borderRadius: 6, borderLeft: `3px solid ${T.cyan}`, fontSize: 11, color: T.txtSec,
                      lineHeight: 1.5 }}>
                      {obs.summary}
                    </div>
                  )}

                  {/* ── Native multi-timeframe context — deterministic, display-only ── */}
                  {marketContext && (
                    <div style={{ marginTop: 12, padding: '10px', background: T.panelAlt,
                      borderRadius: 8, border: `1px solid ${T.border}` }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 9, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 9.5, fontWeight: 700, color: T.txtSec,
                          letterSpacing: '0.08em', textTransform: 'uppercase', marginRight: 'auto' }}>
                          Native Multi-Timeframe Context
                        </span>
                        <Badge label={`DATA ${visualBrainToken(marketContext.bias)}`}
                          color={biasColor(marketContext.bias)} />
                        <Badge label={visualBrainToken(marketContext.alignment)}
                          color={marketContext.alignment === 'ALIGNED' ? T.green : T.txtMuted} />
                      </div>
                      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 9, fontSize: 9.5, color: T.txtMuted }}>
                        <span>Price <strong style={{ color: T.txtSec }}>{fmtContextPrice(marketContext.price)}</strong></span>
                        <span>VWAP <strong style={{ color: T.cyan }}>{fmtContextPrice(marketContext.vwap)}</strong></span>
                        <span>Session H <strong style={{ color: T.red }}>{fmtContextPrice(marketContext.session_high)}</strong></span>
                        <span>Session L <strong style={{ color: T.green }}>{fmtContextPrice(marketContext.session_low)}</strong></span>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, minmax(80px, 1fr))', gap: 5 }}>
                        {['1m', '5m', '15m', '1h', '4h', '1D'].map(tf => {
                          const reading = marketContext.timeframes?.[tf];
                          const tfBias = reading?.bias ?? 'UNKNOWN';
                          return (
                            <div key={tf} style={{ minWidth: 0, padding: '6px 5px', borderRadius: 5,
                              background: T.panel, border: `1px solid ${biasColor(tfBias)}2b`, textAlign: 'center' }}>
                              <div style={{ fontSize: 8.5, color: T.txtMuted, marginBottom: 3 }}>{tf}</div>
                              <div style={{ fontSize: 9, fontWeight: 800, color: biasColor(tfBias), whiteSpace: 'nowrap' }}>
                                {tfBias}
                              </div>
                              <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 3 }}>
                                {typeof reading?.bars === 'number' ? `${reading.bars} bars` : '—'}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* ── Advisory mode assessments — never used for trading decisions ── */}
                  <div style={{ marginTop: 12 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 7 }}>
                      <span style={{ fontSize: 9.5, fontWeight: 700, color: T.txtSec,
                        letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                        Mode Assessments
                      </span>
                      <Badge label="ADVISORY ONLY" color={T.purple} />
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(215px, 1fr))', gap: 8 }}>
                      <ModeAssessmentCard
                        label="SCALP"
                        assessment={modeAssessments.scalp}
                        details={[]}
                      />
                      <ModeAssessmentCard
                        label="INTRADAY TREND"
                        assessment={modeAssessments.intraday_trend}
                        details={[
                          ['15m / 1h', visualBrainToken(modeAssessments.intraday_trend?.timeframe_alignment)],
                          ['Phase', visualBrainToken(modeAssessments.intraday_trend?.market_phase)],
                          ['Session level', modeAssessments.intraday_trend?.session_level],
                        ]}
                      />
                      <ModeAssessmentCard
                        label="SWING"
                        assessment={modeAssessments.swing}
                        details={[
                          ['1h / 4h / Daily', visualBrainToken(modeAssessments.swing?.timeframe_alignment)],
                          ['Thesis', modeAssessments.swing?.thesis_quality],
                          ['Structural stop', modeAssessments.swing?.structural_stop],
                          ['Target context', modeAssessments.swing?.target_context],
                        ]}
                      />
                    </div>
                  </div>

                  {/* ── State change notice ── */}
                  {obs.state_changed && obs.state_change_reason && (
                    <div style={{ marginBottom: 8, padding: '6px 10px', background: `${T.amber}12`,
                      borderRadius: 6, border: `1px solid ${T.amber}33`, fontSize: 10.5, color: T.amber }}>
                      ⚡ {obs.state_change_reason}
                    </div>
                  )}

                  {/* ── Ghost outcomes (when resolved) ── */}
                  {obs.outcome_resolved && (
                    <div style={{ marginTop: 8, display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 4 }}>
                      {([['1m', obs.p1m], ['3m', obs.p3m], ['5m', obs.p5m], ['10m', obs.p10m], ['15m', obs.p15m]] as [string, number | null][]).map(([lbl, val]) => (
                        <div key={lbl} style={{ textAlign: 'center', background: T.panelAlt, borderRadius: 5, padding: '4px 2px' }}>
                          <div style={{ fontSize: 8, color: T.txtMuted }}>{lbl}</div>
                          <div style={{ fontSize: 10.5, fontWeight: 700, color: val == null ? T.txtMuted : val > 0 ? T.green : T.red }}>
                            {fmtPct(val)}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* ── Last Updated ── */}
                  <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 9, color: T.txtMuted }}>
                      {activeTab} · Last Updated: {fmtTs(obs.timestamp)} ET
                    </span>
                    <span style={{ fontSize: 9, color: T.txtMuted }}>Source: {obs.market_context?.source ?? 'Visual Brain'} · {fmtAge(obs.timestamp)}</span>
                  </div>

                  {/* ── State History ── */}
                  <div style={{ marginTop: 10 }}>
                    <div onClick={() => setHistOpen(h => !h)}
                      style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                        padding: '4px 0', borderTop: `1px solid ${T.border}` }}>
                      <span style={{ fontSize: 9.5, fontWeight: 700, color: T.txtSec,
                        letterSpacing: '0.08em', textTransform: 'uppercase', flex: 1 }}>
                        State History — {activeTab}
                      </span>
                      <Badge label={`${transitions.length} shifts`} color={T.cyan} />
                      <span style={{ fontSize: 10, color: T.txtMuted }}>{histOpen ? '▲' : '▼'}</span>
                    </div>
                    {histOpen && (
                      <div style={{ marginTop: 6, maxHeight: 200, overflowY: 'auto' }}>
                        {transitions.length === 0 ? (
                          <div style={{ fontSize: 10, color: T.txtMuted, textAlign: 'center', padding: '8px 0' }}>
                            No state transitions yet
                          </div>
                        ) : transitions.map((row, i) => (
                          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8,
                            padding: '4px 6px', borderRadius: 4, marginBottom: 2,
                            background: i === 0 ? `${T.cyan}08` : 'transparent',
                            borderLeft: i === 0 ? `2px solid ${T.cyan}` : `2px solid transparent` }}>
                            <span style={{ fontSize: 9.5, color: T.txtMuted, fontFamily: T.mono, flexShrink: 0 }}>
                              {fmtTs(row.timestamp)}
                            </span>
                            <Pill text={(row.last_event ?? 'NONE').replace(/_/g, ' ')}
                              color={biasColor(row.bias)} />
                            <span style={{ fontSize: 9.5, color: stateColor(row.market_state) }}>
                              {(row.market_state ?? '').replace(/_/g, ' ')}
                            </span>
                            {row.state_change_reason && (
                              <span style={{ fontSize: 9, color: T.txtMuted, flex: 1,
                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                — {row.state_change_reason}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
};

// ── Research Operations Panel ─────────────────────────────────────────────────
// DISPLAY-ONLY. Shows research engine health, observation pipeline, evidence
// state breakdown, and READY_FOR_REVIEW queue.  Fetches /api/research-ops every
// 30 s.  Fail-open: never touches gate, scoring, or execution.
const ResearchOpsPanel: React.FC<{ authHeader: string }> = ({ authHeader }) => {
  type EngineInfo = {
    enabled: boolean; running: boolean; label: string;
    opportunities_today?: number; total_experiments?: number;
    active_ghost_trades?: number; version?: string; error?: string;
    total_opportunities?: number;
  };
  type ResearchOps = {
    ok: boolean; ts: string; boot_ts: string; error_count: number;
    engines: { gre: EngineInfo; fvg: EngineInfo; scalp: EngineInfo; it: EngineInfo };
    ghost: { observations_today: number; closed_today: number; total_open: number; last_created_at: string | null };
    evidence_states: Record<string, number>;
    ready_for_review: Array<{ experiment_id: string; variant_name: string; instrument: string; direction: string; strategy_family: string; trading_date: string | null }>;
    ready_for_review_count: number;
    healthy: boolean; needs_attention: boolean; db_error?: string;
  };

  const [data, setData]     = React.useState<ResearchOps | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [age, setAge]       = React.useState<number | null>(null);
  const [err, setErr]       = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch('/api/research-ops', {
        credentials: 'include',
        headers: authHeader ? { Authorization: authHeader } : {},
      });
      if (!r.ok) { setErr(`HTTP ${r.status}`); return; }
      const j = await r.json() as ResearchOps;
      setData(j);
      setErr(null);
      setAge(Date.now());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [authHeader]);

  React.useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  // ── Helpers ────────────────────────────────────────────────────────────────
  const fmtAgeMs = (ts: string | null): string => {
    if (!ts) return '—';
    try {
      const secs = Math.round((Date.now() - new Date(ts).getTime()) / 1000);
      if (secs < 60)  return `${secs}s ago`;
      if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
      return `${Math.round(secs / 3600)}h ago`;
    } catch { return '—'; }
  };

  const EngineChip: React.FC<{ info: EngineInfo }> = ({ info }) => {
    const ok  = info.enabled && info.running;
    const off = !info.enabled;
    const col = ok ? T.green : off ? T.txtMuted : T.amber;
    const dot = ok ? '●' : off ? '○' : '◌';
    const lbl = ok ? 'Running' : off ? 'Off' : 'Not running';
    return (
      <div style={{ display:'flex', flexDirection:'column', gap:2, padding:'7px 10px',
        background:`${col}0f`, border:`1px solid ${col}28`, borderRadius:7 }}>
        <div style={{ display:'flex', alignItems:'center', gap:5 }}>
          <span style={{ color:col, fontSize:9 }}>{dot}</span>
          <span style={{ fontSize:10, fontWeight:700, color:T.txtSec, letterSpacing:'0.06em' }}>{info.label}</span>
          <span style={{ marginLeft:'auto', fontSize:9, color:col, letterSpacing:'0.04em' }}>{lbl}</span>
        </div>
        {info.opportunities_today != null && (
          <div style={{ fontSize:9, color:T.txtMuted }}>
            Opps today: <span style={{ color:T.txtSec }}>{info.opportunities_today}</span>
            {info.active_ghost_trades != null && (
              <span>  ·  Active: <span style={{ color:T.cyan }}>{info.active_ghost_trades}</span></span>
            )}
          </div>
        )}
        {info.error && (
          <div style={{ fontSize:8.5, color:T.amber, marginTop:1 }}>⚠ {info.error}</div>
        )}
      </div>
    );
  };

  const EVIDENCE_LABELS: Record<string, { short: string; col: string }> = {
    INSUFFICIENT_DATA: { short: 'Insufficient',  col: T.txtMuted },
    OBSERVING:         { short: 'Observing',      col: '#60a5fa' },
    VALIDATING:        { short: 'Validating',     col: T.amber },
    PROMISING:         { short: 'Promising',      col: '#a78bfa' },
    READY_FOR_REVIEW:  { short: 'Ready ★',        col: T.green },
    REJECTED:          { short: 'Rejected',       col: T.red },
    RETIRED:           { short: 'Retired',        col: T.txtMuted },
  };

  const ghost    = data?.ghost;
  const engines  = data?.engines;
  const ev       = data?.evidence_states ?? {};
  const rfr      = data?.ready_for_review ?? [];
  const healthy  = data?.healthy ?? false;
  const hasAttn  = (data?.needs_attention) ?? false;

  return (
    <section
      id="research-ops-panel"
      aria-label="Research Operations"
      style={{ background:T.panel, border:`1px solid ${T.border}`, borderRadius:10, overflow:'hidden' }}
    >
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div style={{ display:'flex', alignItems:'center', gap:8, padding:'10px 14px',
        borderBottom:`1px solid ${T.border}`, background:'rgba(255,255,255,0.015)' }}>
        <span style={{ fontSize:10, fontWeight:700, letterSpacing:'0.1em', textTransform:'uppercase', color:T.txtSec, flex:1 }}>
          Research Operations
        </span>
        {data && (
          <span style={{ display:'flex', alignItems:'center', gap:5,
            padding:'3px 8px', borderRadius:5,
            background: healthy ? 'rgba(34,197,94,0.1)' : hasAttn ? 'rgba(245,158,11,0.1)' : 'rgba(100,116,139,0.1)',
            border: `1px solid ${healthy ? 'rgba(34,197,94,0.28)' : hasAttn ? 'rgba(245,158,11,0.28)' : 'rgba(100,116,139,0.28)'}`,
          }}>
            <span style={{ fontSize:8, color: healthy ? T.green : hasAttn ? T.amber : T.txtMuted }}>
              {healthy ? '●' : '◌'}
            </span>
            <span style={{ fontSize:9, fontWeight:700, letterSpacing:'0.06em',
              color: healthy ? T.green : hasAttn ? T.amber : T.txtMuted }}>
              {healthy ? 'HEALTHY' : hasAttn ? 'NEEDS ATTENTION' : 'PENDING'}
            </span>
          </span>
        )}
        {age && (
          <span style={{ fontSize:9, color:T.txtMuted }}>{fmtAgeMs(new Date(age).toISOString())}</span>
        )}
        <button onClick={load} disabled={loading}
          style={{ background:'transparent', border:'none', color:T.cyan, cursor:'pointer', fontSize:11, padding:'2px 4px', opacity: loading ? 0.4 : 1 }}
          title="Refresh">↺</button>
      </div>

      <div style={{ padding:'12px 14px', display:'flex', flexDirection:'column', gap:14 }}>
        {err ? (
          <div style={{ padding:'10px 12px', background:'rgba(239,68,68,0.08)', borderRadius:7, fontSize:11, color:T.red }}>
            ⚠ Could not load research status: {err}
          </div>
        ) : !data ? (
          <div style={{ fontSize:11, color:T.txtMuted, padding:8 }}>Loading research status…</div>
        ) : (
          <>
            {/* ── Engine grid ─────────────────────────────────────────────── */}
            <div>
              <div style={{ fontSize:9, fontWeight:700, letterSpacing:'0.1em', color:T.txtMuted, textTransform:'uppercase', marginBottom:6 }}>
                Engine Status
              </div>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:6 }}>
                {engines && (
                  <>
                    <EngineChip info={engines.gre} />
                    <EngineChip info={engines.fvg} />
                    <EngineChip info={engines.scalp} />
                    <EngineChip info={engines.it} />
                  </>
                )}
              </div>
            </div>

            {/* ── Observation summary ─────────────────────────────────────── */}
            <div>
              <div style={{ fontSize:9, fontWeight:700, letterSpacing:'0.1em', color:T.txtMuted, textTransform:'uppercase', marginBottom:6 }}>
                Today's Activity
              </div>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:4 }}>
                {[
                  { label:'Obs today',     value: ghost?.observations_today ?? '—' },
                  { label:'Closed today',  value: ghost?.closed_today ?? '—' },
                  { label:'Open trades',   value: ghost?.total_open ?? '—' },
                ].map(({ label, value }) => (
                  <div key={label} style={{ padding:'6px 8px', background:'rgba(255,255,255,0.025)', borderRadius:6 }}>
                    <div style={{ fontSize:9, color:T.txtMuted, marginBottom:2 }}>{label}</div>
                    <div style={{ fontSize:14, fontWeight:700, color:T.txtSec, fontFamily:T.mono }}>{value}</div>
                  </div>
                ))}
              </div>
              <div style={{ marginTop:6, display:'grid', gridTemplateColumns:'1fr 1fr', gap:4 }}>
                <div style={{ padding:'5px 8px', background:'rgba(255,255,255,0.025)', borderRadius:6, fontSize:10 }}>
                  <span style={{ color:T.txtMuted }}>Last DB write: </span>
                  <span style={{ color: data.error_count > 0 ? T.amber : T.txtSec }}>
                    {fmtAgeMs(ghost?.last_created_at ?? null)}
                  </span>
                </div>
                <div style={{ padding:'5px 8px', background: data.error_count > 0 ? 'rgba(239,68,68,0.08)' : 'rgba(255,255,255,0.025)', borderRadius:6, fontSize:10 }}>
                  <span style={{ color:T.txtMuted }}>Errors: </span>
                  <span style={{ color: data.error_count > 0 ? T.red : T.green, fontWeight:700 }}>
                    {data.error_count}
                  </span>
                </div>
              </div>
            </div>

            {/* ── Evidence pipeline ───────────────────────────────────────── */}
            {Object.keys(ev).length > 0 && (
              <div>
                <div style={{ fontSize:9, fontWeight:700, letterSpacing:'0.1em', color:T.txtMuted, textTransform:'uppercase', marginBottom:6 }}>
                  Evidence Pipeline
                </div>
                <div style={{ display:'flex', flexDirection:'column', gap:3 }}>
                  {Object.entries(ev)
                    .sort(([a], [b]) => {
                      const ORDER = ['INSUFFICIENT_DATA','OBSERVING','VALIDATING','PROMISING','READY_FOR_REVIEW','REJECTED','RETIRED'];
                      return ORDER.indexOf(a) - ORDER.indexOf(b);
                    })
                    .map(([state, count]) => {
                      const meta = EVIDENCE_LABELS[state] ?? { short: state, col: T.txtMuted };
                      const total = Object.values(ev).reduce((a, b) => a + b, 0);
                      const pct   = total > 0 ? Math.round(count / total * 100) : 0;
                      return (
                        <div key={state} style={{ display:'flex', alignItems:'center', gap:6 }}>
                          <span style={{ width:90, fontSize:9, color:meta.col, letterSpacing:'0.02em' }}>{meta.short}</span>
                          <div style={{ flex:1, height:5, background:'rgba(255,255,255,0.06)', borderRadius:3, overflow:'hidden' }}>
                            <div style={{ height:'100%', width:`${pct}%`, background:meta.col, opacity:0.7, borderRadius:3, transition:'width 0.4s ease' }} />
                          </div>
                          <span style={{ width:28, fontSize:10, fontWeight:700, color:meta.col, textAlign:'right', fontFamily:T.mono }}>{count}</span>
                        </div>
                      );
                    })
                  }
                </div>
              </div>
            )}

            {/* ── Needs Attention queue ───────────────────────────────────── */}
            {rfr.length > 0 && (
              <div>
                <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:6, padding:'5px 10px',
                  background:'rgba(34,197,94,0.06)', border:'1px solid rgba(34,197,94,0.2)',
                  borderRadius:6 }}>
                  <span style={{ fontSize:9, color:T.green }}>●</span>
                  <span style={{ fontSize:9, fontWeight:700, letterSpacing:'0.1em', color:T.green, textTransform:'uppercase' }}>
                    Needs Attention — {rfr.length} item{rfr.length !== 1 ? 's' : ''}
                  </span>
                </div>
                <div style={{ display:'flex', flexDirection:'column', gap:4 }}>
                  {rfr.map(ex => (
                    <div key={ex.experiment_id}
                      style={{ padding:'6px 10px', background:'rgba(34,197,94,0.04)',
                        border:'1px solid rgba(34,197,94,0.15)', borderRadius:6 }}>
                      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:2 }}>
                        <span style={{ fontSize:10, fontWeight:700, color:T.green, fontFamily:T.mono }}>
                          {ex.variant_name}
                        </span>
                        <span style={{ fontSize:9, color:T.txtMuted }}>
                          {ex.instrument} · {ex.direction}
                        </span>
                      </div>
                      <div style={{ fontSize:9, color:T.txtMuted }}>
                        {ex.strategy_family}
                        {ex.trading_date && ` · ${ex.trading_date}`}
                      </div>
                      <div style={{ fontSize:8.5, color:T.green, marginTop:2, letterSpacing:'0.06em' }}>
                        READY FOR REVIEW — requires human operator action
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ── DB error ────────────────────────────────────────────────── */}
            {data.db_error && (
              <div style={{ padding:'7px 10px', background:'rgba(239,68,68,0.07)', border:'1px solid rgba(239,68,68,0.2)', borderRadius:6, fontSize:9.5, color:T.red }}>
                DB error: {data.db_error}
              </div>
            )}

            {/* ── Boot info ───────────────────────────────────────────────── */}
            <div style={{ fontSize:8.5, color:T.txtMuted, borderTop:`1px solid ${T.border}`, paddingTop:8 }}>
              Boot: {fmtAgeMs(data.boot_ts)} · Events in buffer: {data ? '(see /research-events)' : '—'}
            </div>
          </>
        )}
      </div>
    </section>
  );
};

// ── Training lanes ────────────────────────────────────────────────────────────
// All three lane views are GET-only presentation layers. They reuse the legacy
// research writers, outcome resolvers, and coordinator reports as their source
// of truth; no lane can promote a strategy or change an execution setting.
type JsonRecord = Record<string, unknown>;

async function getReadOnlyJson(path: string, authHeader: string): Promise<JsonRecord | null> {
  try {
    const response = await fetch(path, {
      credentials: 'include',
      headers: authHeader ? { Authorization: authHeader } : {},
    });
    if (!response.ok) return null;
    const body = await response.json();
    return body && typeof body === 'object' ? body as JsonRecord : null;
  } catch {
    return null;
  }
}

const TrainingReadOnlyBanner: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{
    marginBottom: 12, padding: '9px 12px', borderRadius: 8,
    background: `${T.cyan}0d`, border: `1px solid ${T.cyan}2c`,
    color: T.txtSec, fontSize: 10.5, lineHeight: 1.45,
  }}>
    <strong style={{ color: T.cyan, letterSpacing: '0.06em', fontSize: 9.5 }}>DISPLAY-ONLY RESEARCH</strong>
    <span> · {children}</span>
  </div>
);

const TrainingStat: React.FC<{ label: string; value: React.ReactNode; color?: string }> = ({ label, value, color = T.txtPri }) => (
  <div style={{ padding: '8px 9px', borderRadius: 7, background: `${T.border}20`, minWidth: 0 }}>
    <div style={{ fontSize: 8.5, color: T.txtMuted, letterSpacing: '0.06em', textTransform: 'uppercase' }}>{label}</div>
    <div style={{ marginTop: 3, fontSize: 15, color, fontFamily: T.mono, fontWeight: 750, overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</div>
  </div>
);

type PaperSimUnresolvedRow = {
  id: number;
  ledger: 'scalp' | 'dual';
  strategy_key?: string | null;
  mode?: string | null;
  instrument?: string | null;
  direction?: string | null;
  unresolved_reason?: string | null;
  unresolved_age_hours?: number | null;
  max_hold_hours?: number | null;
  unresolved_at?: string | null;
  resolution_audit?: JsonRecord[];
  reprocess_available?: boolean;
  reprocess_blocked_reason?: string | null;
};

const PaperSimulationRepairPanel: React.FC<{ authHeader: string }> = ({ authHeader }) => {
  const [rows, setRows] = React.useState<PaperSimUnresolvedRow[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [repairAvailable, setRepairAvailable] = React.useState(true);
  const [selectedKey, setSelectedKey] = React.useState('');
  const [barsText, setBarsText] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [message, setMessage] = React.useState<{ text: string; ok: boolean } | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const response = await getReadOnlyJson('/api/paper-sim/unresolved?limit=50', authHeader);
      setRows(Array.isArray(response?.rows) ? response.rows as PaperSimUnresolvedRow[] : []);
      setRepairAvailable(response?.reprocess_available !== false);
    } finally {
      setLoading(false);
    }
  }, [authHeader]);

  React.useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  const selected = rows.find(row => `${row.ledger}:${row.id}` === selectedKey) ?? null;

  const reprocess = async () => {
    if (!selected) return;
    let bars: unknown[] | null = null;
    if (barsText.trim()) {
      try {
        const parsed = JSON.parse(barsText);
        const parsedBars = Array.isArray(parsed) ? parsed : (parsed as JsonRecord)?.bars;
        if (!Array.isArray(parsedBars) || parsedBars.length === 0) throw new Error('empty bars');
        bars = parsedBars;
      } catch {
        setMessage({ ok: false, text: 'Paste a non-empty JSON array of verified historical-bar references.' });
        return;
      }
    }
    if (!window.confirm(
      bars
        ? `Reprocess ${selected.ledger} paper simulation #${selected.id} with ${bars.length} server-verified historical references? This changes only the research simulation row.`
        : `Fetch the bounded Databento historical window and reprocess ${selected.ledger} paper simulation #${selected.id}? This changes only the research simulation row.`
    )) return;
    setSubmitting(true);
    setMessage(null);
    try {
      const response = await fetch('/api/paper-sim/reprocess', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...(authHeader ? { Authorization: authHeader } : {}),
        },
        body: JSON.stringify(bars ? {
          ledger: selected.ledger,
          id: selected.id,
          verified: true,
          source: 'databento_historical_verified',
          bars,
        } : {
          ledger: selected.ledger,
          id: selected.id,
          fetch_verified_history: true,
        }),
      });
      const result = await response.json().catch(() => ({})) as JsonRecord;
      if (!response.ok || result.ok !== true) {
        setMessage({ ok: false, text: safeStr(result.error, 'Historical reprocess was rejected.') });
      } else if (result.processed === true) {
        setMessage({ ok: true, text: `Resolved as ${safeStr(result.result)}. Original unresolved audit preserved.` });
        setBarsText('');
        setSelectedKey('');
      } else if (result.idempotent === true) {
        setMessage({ ok: true, text: 'This verified history batch was already processed; no duplicate write was made.' });
      } else {
        setMessage({ ok: false, text: `Still unresolved: ${safeStr(result.reason, 'no deterministic outcome')}. The attempt was recorded once.` });
      }
      await load();
    } catch {
      setMessage({ ok: false, text: 'Could not reach the paper-simulation repair endpoint.' });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Panel
      title="Unresolved paper simulations"
      badge={<Badge label={!repairAvailable ? 'STORE UNAVAILABLE' : rows.length ? `${rows.length} REVIEW` : 'CLEAR'} color={!repairAvailable || rows.length ? T.amber : T.green} />}
    >
      <div style={{ padding: 12 }}>
        <div style={{ marginBottom: 10, padding: '7px 9px', borderRadius: 6, background: `${T.cyan}0d`, border: `1px solid ${T.cyan}25`, color: T.txtMuted, fontSize: 9.5 }}>
          Research-only repair. Submitted items are references only: the server loads price and continuity data from its persisted verified Databento history, writes only the selected simulation row, and preserves the original unresolved audit.
        </div>
        {!repairAvailable && (
          <div style={{ marginBottom: 10, padding: '7px 9px', borderRadius: 6, background: `${T.amber}10`, border: `1px solid ${T.amber}35`, color: T.amber, fontSize: 9.5 }}>
            Repair is unavailable until the verified historical-bar store is present and healthy. Review metadata remains read-only.
          </div>
        )}
        {loading ? (
          <div style={{ color: T.txtMuted, fontSize: 10 }}>Loading unresolved simulations…</div>
        ) : rows.length === 0 ? (
          <div style={{ color: T.green, fontSize: 10 }}>No unresolved paper simulations need operator review.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {rows.map(row => {
              const key = `${row.ledger}:${row.id}`;
              const selectedRow = selectedKey === key;
              const age = safeNum(row.unresolved_age_hours);
              const maxHold = safeNum(row.max_hold_hours);
              return (
                <div key={key} style={{ border: `1px solid ${selectedRow ? `${T.cyan}55` : T.border}`, borderRadius: 7, background: selectedRow ? `${T.cyan}08` : T.panelAlt }}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedKey(selectedRow ? '' : key);
                      setBarsText('');
                      setMessage(null);
                    }}
                    style={{ width: '100%', padding: '8px 10px', border: 0, background: 'transparent', color: T.txtSec, cursor: 'pointer', textAlign: 'left' }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 4 }}>
                      <strong style={{ fontSize: 10, color: T.txtPri }}>
                        {row.ledger.toUpperCase()} #{row.id} · {safeStr(row.instrument)} {safeStr(row.direction)}
                      </strong>
                      <span style={{ color: T.txtMuted, fontSize: 9 }}>{fmtAge(row.unresolved_at)}</span>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, fontSize: 9 }}>
                      <span><span style={{ color: T.txtMuted }}>Reason:</span> <span style={{ color: T.amber }}>{safeStr(row.unresolved_reason)}</span></span>
                      <span><span style={{ color: T.txtMuted }}>Age at resolution:</span> {age == null ? '—' : `${age.toFixed(2)}h`}</span>
                      <span><span style={{ color: T.txtMuted }}>Max hold:</span> {maxHold == null ? '—' : `${maxHold}h`}</span>
                      <span><span style={{ color: T.txtMuted }}>Audit:</span> {Array.isArray(row.resolution_audit) ? row.resolution_audit.length : 0} event(s)</span>
                    </div>
                  </button>
                  {selectedRow && (
                    <div style={{ borderTop: `1px solid ${T.border}`, padding: 10 }}>
                      <div style={{ color: T.txtMuted, fontSize: 9, marginBottom: 6 }}>
                        Leave this blank to fetch the bounded Databento historical window now. Or paste references from an already completed verified backfill. Server-held OHLC and capture continuity remain authoritative.
                      </div>
                      <textarea
                        value={barsText}
                        onChange={event => setBarsText(event.target.value)}
                        placeholder='[{"instrument":"MGC","start":"2026-08-26T13:31:00Z","source":"databento_historical_verified","capture_kind":"historical_verified"}]'
                        rows={5}
                        style={{ width: '100%', boxSizing: 'border-box', resize: 'vertical', padding: 8, borderRadius: 6, border: `1px solid ${T.borderMid}`, background: T.bg, color: T.txtSec, fontFamily: T.mono, fontSize: 9 }}
                      />
                      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 7 }}>
                        <button type="button" disabled={!repairAvailable || submitting} onClick={reprocess} style={{ padding: '6px 10px', borderRadius: 6, border: `1px solid ${T.cyan}55`, background: `${T.cyan}18`, color: T.cyan, cursor: submitting ? 'wait' : 'pointer', opacity: !repairAvailable || submitting ? 0.5 : 1, fontSize: 9.5, fontWeight: 700 }}>
                          {submitting ? 'REPROCESSING…' : barsText.trim() ? 'REPROCESS VERIFIED REFERENCES' : 'FETCH DATABENTO & REPROCESS'}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {message && (
          <div role="status" style={{ marginTop: 9, padding: '7px 9px', borderRadius: 6, background: `${message.ok ? T.green : T.amber}12`, color: message.ok ? T.green : T.amber, fontSize: 9.5 }}>
            {message.text}
          </div>
        )}
      </div>
    </Panel>
  );
};

const CanonicalEvidenceHealthPanel: React.FC<{
  mode: string;
  health: JsonRecord | null;
}> = ({ mode, health }) => {
  const byMode = (health?.by_mode ?? {}) as Record<string, JsonRecord>;
  const lane = byMode[mode] ?? null;
  const persistence = (health?.persistence ?? {}) as JsonRecord;
  const reconciliation = (health?.reconciliation ?? {}) as JsonRecord;
  const outcomes = (health?.outcomes ?? {}) as JsonRecord;
  const coordinator = (health?.coordinator ?? {}) as JsonRecord;
  const coordinatorHealth = (coordinator.health_totals ?? {}) as JsonRecord;
  const coordinatorDurable = (coordinator.durable_totals ?? {}) as JsonRecord;
  const coordinatorSession = (coordinator.restored_session_counts ?? {}) as JsonRecord;
  const durableTotalsReady = coordinatorHealth.complete === true
    || coordinatorDurable.complete === true;
  const durableOpportunities = safeNum(
    coordinatorHealth.opportunity_count ?? coordinatorDurable.opportunity_count
  );
  const durableObservations = safeNum(
    coordinatorHealth.observation_count ?? coordinatorDurable.observation_count
  );
  const sessionOpportunities = safeNum(coordinatorSession.opportunity_count);
  const sessionObservations = safeNum(coordinatorSession.observation_count);
  const staleAfter = safeNum(health?.stale_after_minutes);
  const status = safeStr(lane?.status ?? health?.health_status, 'UNAVAILABLE');
  const statusColor = status === 'HEALTHY' ? T.green
    : status === 'ATTENTION' ? T.amber : T.txtMuted;
  const exactCoverage = safeNum(lane?.exact_id_match_coverage);
  const lastWrite = fmtAge(lane?.last_successful_write_at ?? persistence.last_successful_write_at) || '—';
  const lastReconciliation = fmtAge(lane?.last_reconciliation_at ?? reconciliation.last_reconciliation_at) || '—';

  return (
    <Panel
      title="Canonical Evidence Health"
      badge={<Badge label={`${status} · SHADOW ONLY`} color={statusColor} />}
      style={{ marginBottom: 12 }}
    >
      {!health || !lane ? (
        <UnavailableNote msg="Canonical evidence health is unavailable. This never changes qualification, outcomes, or execution." />
      ) : (
        <>
          <div style={{ marginBottom: 10, color: T.txtSec, fontSize: 10, lineHeight: 1.45 }}>
            Exact-ID reconciliation for <strong style={{ color: T.cyan }}>{mode}</strong>. Generic ghost remains the outcome authority; Strategy Lab is excluded.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, minmax(0, 1fr))', gap: 7 }} className="mb-training-stats">
            <TrainingStat label="Intake" value={safeNum(lane.intake_volume) ?? 0} />
            <TrainingStat label="Unique" value={safeNum(lane.unique_canonical_opportunities) ?? 0} color={T.cyan} />
            <TrainingStat label="Duplicates" value={safeNum(lane.duplicate_count) ?? 0} color={safeNum(lane.duplicate_count) ? T.amber : T.txtPri} />
            <TrainingStat label="Unresolved" value={safeNum(lane.unresolved_observations) ?? 0} color={safeNum(lane.unresolved_observations) ? T.amber : T.green} />
            <TrainingStat label="Overdue" value={safeNum(lane.overdue_observations) ?? 0} color={safeNum(lane.overdue_observations) ? T.red : T.green} />
            <TrainingStat label="Disagrees" value={safeNum(lane.outcome_disagreement_count) ?? 0} color={safeNum(lane.outcome_disagreement_count) ? T.red : T.green} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 11 }} className="mb-grid-2">
            <div>
              <KV label="Exact-ID match coverage" value={exactCoverage == null ? 'No comparison references' : `${exactCoverage}%`} valueColor={exactCoverage == null || exactCoverage === 100 ? T.green : T.amber} />
              <KV label="Matched / unmatched" value={`${safeNum(lane.exact_id_match_count) ?? 0} / ${safeNum(lane.exact_id_unmatched_count) ?? 0}`} mono />
              <KV label="Agreement / compared" value={`${safeNum(lane.outcome_agreement_count) ?? 0} / ${safeNum(lane.outcome_comparison_count) ?? 0}`} mono />
            </div>
            <div>
              <KV label="Persistence" value={safeStr(health.persistence_state, 'IN-MEMORY')} valueColor={health.persistence_state === 'READY' ? T.green : T.amber} />
              <KV label="Pending writes / errors" value={`${safeNum(lane.pending_persistence_events) ?? 0} / ${safeNum(lane.persistence_errors) ?? 0}`} valueColor={safeNum(lane.persistence_errors) ? T.red : T.txtPri} mono />
              <KV label="Last write / reconcile" value={`${lastWrite} / ${lastReconciliation}`} />
            </div>
          </div>
          <div style={{ marginTop: 10, fontSize: 9.5, color: T.txtSec }}>
            Coordinator health totals:{' '}
            <strong style={{ color: durableTotalsReady ? T.cyan : T.amber }}>
              {durableTotalsReady ? `${durableOpportunities ?? 0} durable opportunities` : 'durable totals unavailable'}
            </strong>
            {' · '}
            <strong style={{ color: durableTotalsReady ? T.txtPri : T.amber }}>
              {durableTotalsReady ? `${durableObservations ?? 0} durable observations` : 'using restored-session counts'}
            </strong>
          </div>
          <div style={{ marginTop: 5, fontSize: 9.5, color: T.txtMuted }}>
            Restored session window: {sessionOpportunities ?? safeNum(coordinator.opportunity_count) ?? 0} opportunities
            {' · '}
            {sessionObservations ?? safeNum(coordinator.opportunity_observation_count) ?? 0} observations
            {' · '}
            {safeNum(coordinatorSession.evaluation_heartbeats) ?? safeNum(coordinator.evaluation_heartbeats) ?? 0} heartbeats
          </div>
          <div style={{ marginTop: 6, fontSize: 9.5, color: T.txtMuted }}>
            Overdue means unresolved for more than {staleAfter ?? '—'} minutes. This panel reads evidence only; it cannot route, resolve, size, gate, or execute.
          </div>
        </>
      )}
    </Panel>
  );
};

const IntradayMarketDataHealthPanel: React.FC<{ authHeader: string }> = ({ authHeader }) => {
  const [snapshot, setSnapshot] = React.useState<{ databento: Record<string, any> | null; visual: Record<string, any> | null; loadedAt: number | null }>({
    databento: null, visual: null, loadedAt: null,
  });

  const load = React.useCallback(async () => {
    const headers: Record<string, string> = authHeader ? { Authorization: authHeader } : {};
    const [databento, visual] = await Promise.all([
      fetch('/api/databento-status', { credentials: 'include', headers }).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch('/api/visual-brain/all-status', { credentials: 'include', headers }).then(r => r.ok ? r.json() : null).catch(() => null),
    ]);
    setSnapshot({ databento, visual, loadedAt: Date.now() });
  }, [authHeader]);

  React.useEffect(() => {
    void load();
    const id = window.setInterval(() => { void load(); }, 10_000);
    return () => window.clearInterval(id);
  }, [load]);

  const service = snapshot.databento?.status ?? {};
  const telemetry = service?.instruments ?? {};
  const newestBar = latestBarTimestampMs(Object.values(telemetry).map((item: any) => ({ ts: item?.bar_ts ?? item?.last_bar_ts })));
  const dataFreshness = classifyDatabentoFreshness({
    enabled: snapshot.databento?.enabled === true,
    connected: service?.connected === true,
    lastEventAt: service?.last_ts,
    latestBarAt: newestBar,
  });
  const observations = Object.entries(snapshot.visual?.instruments ?? {}) as Array<[string, { observation?: { timestamp?: unknown; market_context?: { source?: unknown } } | null }]>;
  const visualEnabled = snapshot.visual?.enabled === true;

  return (
    <Panel
      title="Current market-data health"
      badge={<Badge label={snapshot.loadedAt ? dataFreshness.state : 'CONNECTING'} color={dataFreshness.current ? T.green : T.amber} />}
      style={{ marginBottom: 12 }}
    >
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }} className="mb-grid-2">
        <div style={{ padding:'10px 11px', borderRadius:8, background:T.panelAlt, border:`1px solid ${dataFreshness.current ? T.green : T.amber}33` }}>
          <div style={{ display:'flex', justifyContent:'space-between', gap:8, marginBottom:7 }}>
            <span style={{ fontSize:10, fontWeight:800, color:T.txtSec, letterSpacing:'0.08em' }}>DATABENTO</span>
            <Badge label={snapshot.loadedAt ? dataFreshness.state : 'CONNECTING'} color={dataFreshness.current ? T.green : T.amber} />
          </div>
          <div style={{ fontSize:10, color:T.txtMuted, lineHeight:1.6 }}>
            <div>Source: <strong style={{ color:T.cyan }}>Databento</strong></div>
            <div>Connection: <strong style={{ color:dataFreshness.current ? T.green : T.amber }}>{service?.connected === true ? (service?.status ?? 'CONNECTED') : 'UNAVAILABLE'}</strong></div>
            <div>Latest event: <strong style={{ color:T.txtSec }}>{formatFreshnessAge(dataFreshness.ageMs)}</strong></div>
          </div>
        </div>
        <div style={{ padding:'10px 11px', borderRadius:8, background:T.panelAlt, border:`1px solid ${visualEnabled ? T.cyan : T.amber}33` }}>
          <div style={{ display:'flex', justifyContent:'space-between', gap:8, marginBottom:7 }}>
            <span style={{ fontSize:10, fontWeight:800, color:T.txtSec, letterSpacing:'0.08em' }}>VISUAL BRAIN</span>
            <Badge label={visualEnabled ? 'OBSERVING' : 'UNAVAILABLE'} color={visualEnabled ? T.cyan : T.amber} />
          </div>
          <div style={{ display:'flex', flexWrap:'wrap', gap:5 }}>
            {observations.length === 0 ? <span style={{ fontSize:10, color:T.txtMuted }}>No current observations returned.</span> : observations.map(([inst, value]) => {
              const freshness = classifyVisualBrainFreshness(value?.observation?.timestamp);
              return <span key={inst} style={{ fontSize:9, fontFamily:T.mono, padding:'3px 6px', borderRadius:4,
                color:freshness.current ? T.green : T.amber, border:`1px solid ${freshness.current ? T.green : T.amber}44` }}>
                {inst} {freshness.state} {formatFreshnessAge(freshness.ageMs)}
              </span>;
            })}
          </div>
          <div style={{ marginTop:7, fontSize:9, color:T.txtMuted }}>Source: Visual Brain observations · display-only</div>
        </div>
      </div>
      <div role="note" style={{ marginTop:9, fontSize:9.5, color:T.txtMuted, lineHeight:1.45 }}>
        This is an operator health display only. It does not alter research qualification, risk, broker routing, execution, or coordinator fan-out.
      </div>
    </Panel>
  );
};

const TrainingLanePanel: React.FC<{
  lane: 'scalp' | 'intraday';
  authHeader: string;
}> = ({ lane, authHeader }) => {
  const config = TRAINING_LANES[lane];
  const mode = config.apiMode;
  const [data, setData] = React.useState<{
    modeReport: JsonRecord | null;
    strategyReport: JsonRecord | null;
    opportunities: JsonRecord | null;
    settlement: JsonRecord | null;
    coordinator: JsonRecord | null;
    researchHealth: JsonRecord | null;
    canonicalHealth: JsonRecord | null;
    loadedAt: number | null;
  }>({ modeReport: null, strategyReport: null, opportunities: null, settlement: null, coordinator: null, researchHealth: null, canonicalHealth: null, loadedAt: null });

  const load = React.useCallback(async () => {
    const [modeReportRaw, strategyReportRaw, opportunities, settlement, coordinator, researchHealth, canonicalHealth] = await Promise.all([
      getReadOnlyJson(`/api/gate-effectiveness/mode-report?mode=${encodeURIComponent(mode)}`, authHeader),
      getReadOnlyJson(`/api/gate-effectiveness/strategy-report?mode=${encodeURIComponent(mode)}`, authHeader),
      getReadOnlyJson(`/api/gate-effectiveness/opportunities?mode=${encodeURIComponent(mode)}&days=7`, authHeader),
      getReadOnlyJson('/api/gate-effectiveness/settlement-health', authHeader),
      getReadOnlyJson('/api/research-coordinator-report?limit=25', authHeader),
      getReadOnlyJson('/api/research-health', authHeader),
      getReadOnlyJson('/api/canonical-evidence-health', authHeader),
    ]);
    setData({
      modeReport: (modeReportRaw?.report as JsonRecord | undefined) ?? null,
      strategyReport: (strategyReportRaw?.report as JsonRecord | undefined) ?? null,
      opportunities,
      settlement,
      coordinator,
      researchHealth,
      canonicalHealth,
      loadedAt: Date.now(),
    });
  }, [authHeader, mode]);

  React.useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  const report = data.modeReport;
  const health = (report?.health ?? {}) as JsonRecord;
  const strategyReport = data.strategyReport;
  const strategies = Object.values((strategyReport?.strategies ?? {}) as Record<string, JsonRecord>)
    .sort((a, b) => (safeNum(b.raw_evaluations) ?? 0) - (safeNum(a.raw_evaluations) ?? 0))
    .slice(0, 6);
  const opportunities = Array.isArray(data.opportunities?.opportunities)
    ? data.opportunities?.opportunities as JsonRecord[] : [];
  const coordinator = data.coordinator ?? {};
  const coordinatorDurable = (coordinator.durable_totals ?? {}) as JsonRecord;
  const coordinatorSession = (coordinator.restored_session_counts ?? {}) as JsonRecord;
  const coordinatorHealth = (coordinator.health_totals ?? {}) as JsonRecord;
  const durableTotalsReady = coordinatorHealth.complete === true
    || coordinatorDurable.complete === true;
  const researchHealth = data.researchHealth ?? {};
  const ghostHealth = (researchHealth.ghost_engine ?? {}) as JsonRecord;
  const edgeHealth = (researchHealth.edge_ledger ?? {}) as JsonRecord;
  const pending = safeNum(health.pending_outcomes) ?? 0;
  const collector = safeStr(health.collector_status, 'NO DATA');
  const collectorColor = collector === 'ACTIVE' ? T.green : collector === 'SILENT' ? T.amber : T.txtMuted;
  const coordinatorFanout = coordinator.fanout_enabled === true ? 'ON' : 'OFF';

  return (
    <div>
      <TrainingReadOnlyBanner>
        {config.description} Legacy writers and outcome resolvers remain authoritative; coordinator fan-out is not controlled here.
      </TrainingReadOnlyBanner>
      {lane === 'intraday' && <IntradayMarketDataHealthPanel authHeader={authHeader} />}
      <CanonicalEvidenceHealthPanel mode={mode} health={data.canonicalHealth} />

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, margin: '2px 0 12px' }}>
        <h1 style={{ margin: 0, fontSize: 20, letterSpacing: '0.06em' }}>{config.label}</h1>
        <span style={{ fontSize: 10, color: T.txtMuted }}>Training evidence lane · refreshed {data.loadedAt ? fmtAge(new Date(data.loadedAt).toISOString()) : 'loading…'}</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 8, marginBottom: 12 }} className="mb-training-stats">
        <TrainingStat label="Observations" value={report ? (safeNum(report.total_blocked) ?? 0) + (safeNum(report.total_allowed) ?? 0) : '—'} />
        <TrainingStat label="Open / pending" value={report ? pending : '—'} color={pending > 0 ? T.amber : T.txtPri} />
        <TrainingStat label="Resolved" value={report ? safeNum(health.resolved_outcomes) ?? 0 : '—'} color={T.green} />
        <TrainingStat label="Strategies" value={(safeNum(strategyReport?.strategy_count) ?? strategies.length) || '—'} />
        <TrainingStat label="Geometry" value={report ? `${safeNum(report.geometry_rate) ?? 0}%` : '—'} color={(safeNum(report?.geometry_rate) ?? 0) > 30 ? T.green : T.amber} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: 10 }} className="mb-grid-2">
        <Panel title={`${config.label} outcome & gate evidence`} badge={<Badge label={collector} color={collectorColor} />}>
          {!report ? (
            <div style={{ color: T.txtMuted, fontSize: 11, padding: 12 }}>Loading mode report…</div>
          ) : (
            <div style={{ padding: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 7, marginBottom: 12 }}>
                <TrainingStat label="Allowed" value={safeNum(report.total_allowed) ?? 0} color={T.green} />
                <TrainingStat label="Blocked" value={safeNum(report.total_blocked) ?? 0} color={T.amber} />
                <TrainingStat label="Gate value" value={report.gate_improvement == null ? '—' : `${safeNum(report.gate_improvement) ?? 0}R`} color={(safeNum(report.gate_improvement) ?? 0) >= 0 ? T.green : T.red} />
              </div>
              <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.08em', marginBottom: 5 }}>OUTCOME RESOLVER HEALTH</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, fontSize: 10, color: T.txtSec }}>
                <span>Last observation: <strong>{fmtAge(health.last_observation_ts)}</strong></span>
                <span>Last resolved: <strong>{fmtAge(health.last_resolved_ts)}</strong></span>
                <span>24h unique setups: <strong>{safeStr(health.unique_opps_24h)}</strong></span>
                <span>Evidence: <strong>{safeStr(report.evidence_status)}</strong></span>
              </div>
              {pending > 0 && (
                <div role="status" style={{ marginTop: 10, padding: '7px 9px', borderRadius: 6, background: `${T.amber}12`, color: T.amber, fontSize: 10 }}>
                  {pending} unresolved observation{pending === 1 ? '' : 's'} awaiting the established outcome resolver.
                </div>
              )}
            </div>
          )}
        </Panel>

        <Panel title="Shadow coverage & stale-evidence watch" badge={<Badge label={`FAN-OUT ${coordinatorFanout}`} color={coordinatorFanout === 'OFF' ? T.txtMuted : T.red} />}>
          <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 9, fontSize: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ color: T.txtMuted }}>Coordinator intake</span>
              <span style={{ color: coordinator.enabled === false ? T.txtMuted : T.cyan }}>{safeStr(coordinator.enabled === true ? 'ENABLED' : coordinator.enabled === false ? 'OFF' : 'PENDING')}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ color: T.txtMuted }}>Persistence</span>
              <span style={{ color: T.txtSec }}>{safeStr(coordinator.persistence, 'shadow only')}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ color: T.txtMuted }}>Ghost ledger</span>
              <span style={{ color: ghostHealth.table_ready === true ? T.green : T.amber }}>{ghostHealth.table_ready === true ? 'READY' : 'PENDING'}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ color: T.txtMuted }}>Edge ledger</span>
              <span style={{ color: edgeHealth.table_ready === true ? T.green : T.amber }}>{edgeHealth.table_ready === true ? 'READY' : 'PENDING'}</span>
            </div>
            <div style={{ paddingTop: 8, borderTop: `1px solid ${T.border}`, color: pending > 0 || collector === 'SILENT' ? T.amber : T.txtMuted }}>
              {collector === 'SILENT' ? 'Collector is silent — verify upstream market data before interpreting empty results.' :
                pending > 0 ? 'Pending outcomes are visible here; they are not re-routed or resolved by this dashboard.' :
                'No overdue-evidence warning reported by the current mode health snapshot.'}
            </div>
          </div>
        </Panel>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: 10, marginTop: 10 }} className="mb-grid-2">
        <Panel title={`${config.label} strategy breakdown`} badge={<Badge label={`${strategies.length} shown`} color={T.cyan} />}>
          <div style={{ padding: 12, overflowX: 'auto' }}>
            {strategies.length === 0 ? <div style={{ fontSize: 10, color: T.txtMuted }}>No resolved strategy evidence yet.</div> : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                <thead><tr style={{ color: T.txtMuted, textAlign: 'left' }}>
                  {['Strategy', 'Evaluations', 'Resolved', 'Win %', 'Net R'].map(label => <th key={label} style={{ padding: '0 5px 6px', fontWeight: 700, fontSize: 8.5, letterSpacing: '0.05em' }}>{label}</th>)}
                </tr></thead>
                <tbody>{strategies.map((strategy, index) => {
                  const winRate = safeNum(strategy.win_rate);
                  const netR = safeNum(strategy.net_r);
                  return <tr key={`${safeStr(strategy.strategy)}-${index}`} style={{ borderTop: `1px solid ${T.border}` }}>
                    <td style={{ padding: '7px 5px', color: T.txtPri, fontWeight: 700 }}>{safeStr(strategy.strategy)}</td>
                    <td style={{ padding: '7px 5px', color: T.txtSec }}>{safeStr(strategy.raw_evaluations)}</td>
                    <td style={{ padding: '7px 5px', color: T.txtSec }}>{safeStr(strategy.resolved_count)}</td>
                    <td style={{ padding: '7px 5px', color: winRate == null ? T.txtMuted : winRate >= 50 ? T.green : T.red }}>{winRate == null ? '—' : `${winRate}%`}</td>
                    <td style={{ padding: '7px 5px', color: netR == null ? T.txtMuted : netR >= 0 ? T.green : T.red }}>{netR == null ? '—' : `${netR.toFixed(2)}R`}</td>
                  </tr>;
                })}</tbody>
              </table>
            )}
          </div>
        </Panel>

        <Panel title="Recent mode observations" badge={<Badge label={`${opportunities.length}`} color={T.txtMuted} />}>
          <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {opportunities.slice(0, 6).map((opportunity, index) => (
              <div key={`${safeStr(opportunity.id ?? opportunity.obs_key ?? opportunity.created_at)}-${index}`} style={{ paddingBottom: 6, borderBottom: `1px solid ${T.border}` }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 10 }}>
                  <span style={{ color: dirColor(opportunity.direction), fontWeight: 700 }}>{safeStr(opportunity.direction, 'OBSERVATION')}</span>
                  <span style={{ color: T.txtMuted }}>{fmtAge(opportunity.created_at ?? opportunity.timestamp ?? opportunity.signal_timestamp)}</span>
                </div>
                <div style={{ fontSize: 9.5, color: T.txtSec, marginTop: 2 }}>{safeStr(opportunity.strategy ?? opportunity.strategy_key ?? opportunity.primary_blocker)}</div>
              </div>
            ))}
            {opportunities.length === 0 && <div style={{ fontSize: 10, color: T.txtMuted }}>No recent observations returned for this lane.</div>}
          </div>
        </Panel>
      </div>
    </div>
  );
};

const StrategyLabPanel: React.FC<{ authHeader: string }> = ({ authHeader }) => {
  const [data, setData] = React.useState<{
    health: JsonRecord | null; experiments: JsonRecord[]; candidates: JsonRecord[];
    ready: JsonRecord[]; operations: JsonRecord | null; loadedAt: number | null;
  }>({ health: null, experiments: [], candidates: [], ready: [], operations: null, loadedAt: null });

  const load = React.useCallback(async () => {
    const [health, experimentResponse, candidateResponse, readyResponse, operations] = await Promise.all([
      getReadOnlyJson('/api/ghost-research/health', authHeader),
      getReadOnlyJson('/api/ghost-research/experiments?limit=30', authHeader),
      getReadOnlyJson('/api/ghost-research/candidates?min_samples=10', authHeader),
      getReadOnlyJson('/api/ghost-research/ready-for-review', authHeader),
      getReadOnlyJson('/api/research-ops', authHeader),
    ]);
    setData({
      health,
      experiments: Array.isArray(experimentResponse?.experiments) ? experimentResponse?.experiments as JsonRecord[] : [],
      candidates: Array.isArray(candidateResponse?.candidates) ? candidateResponse?.candidates as JsonRecord[] : [],
      ready: Array.isArray(readyResponse?.experiments) ? readyResponse?.experiments as JsonRecord[] : [],
      operations,
      loadedAt: Date.now(),
    });
  }, [authHeader]);

  React.useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  const familyBreakdown = (data.health?.family_breakdown ?? {}) as Record<string, JsonRecord>;
  const evidence = (data.operations?.evidence_states ?? {}) as Record<string, unknown>;
  const completed = safeNum(data.health?.completed) ?? 0;

  return (
    <div>
      <TrainingReadOnlyBanner>
        Shadow experiments are reviewed here only. A strategy can move to SCALP or INTRADAY only through an explicit human decision outside this dashboard; no automatic promotion exists.
      </TrainingReadOnlyBanner>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, margin: '2px 0 12px' }}>
        <h1 style={{ margin: 0, fontSize: 20, letterSpacing: '0.06em' }}>STRATEGY LAB</h1>
        <span style={{ fontSize: 10, color: T.txtMuted }}>Experiment evidence · refreshed {data.loadedAt ? fmtAge(new Date(data.loadedAt).toISOString()) : 'loading…'}</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 8, marginBottom: 12 }} className="mb-training-stats">
        <TrainingStat label="Experiments" value={safeNum(data.health?.total_experiments) ?? '—'} />
        <TrainingStat label="Active ghosts" value={safeNum(data.health?.active_ghost_trades) ?? '—'} color={T.cyan} />
        <TrainingStat label="Completed" value={completed || '—'} color={T.green} />
        <TrainingStat label="Candidates" value={data.candidates.length} />
        <TrainingStat label="Ready for review" value={data.ready.length} color={data.ready.length ? T.green : T.txtPri} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '0.8fr 1.2fr', gap: 10 }} className="mb-grid-2">
        <Panel title="Manual promotion path" badge={<Badge label="HUMAN DECISION" color={T.amber} />}>
          <div style={{ padding: 12, fontSize: 10.5, color: T.txtSec, lineHeight: 1.55 }}>
            <ol style={{ margin: 0, paddingLeft: 18 }}>
              <li>Observe a family and its variants through completed shadow outcomes.</li>
              <li>Review sample size, performance, evidence state, and risk context.</li>
              <li>Make an explicit operator decision to evaluate it for <strong style={{ color: T.cyan }}>SCALP</strong> or <strong style={{ color: T.cyan }}>INTRADAY</strong>.</li>
            </ol>
            <div style={{ marginTop: 10, padding: '7px 9px', borderRadius: 6, background: `${T.amber}10`, color: T.amber }}>
              This panel has no promotion button, strategy selector, execution control, or write request.
            </div>
          </div>
        </Panel>
        <Panel title="Research families & evidence states" badge={<Badge label={data.health?.db_ready === true ? 'ENGINE READY' : 'PENDING'} color={data.health?.db_ready === true ? T.green : T.amber} />}>
          <div style={{ padding: 12, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 9 }}>
            <div>
              <div style={{ fontSize: 8.5, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.08em', marginBottom: 6 }}>FAMILIES</div>
              {Object.entries(familyBreakdown).map(([family, metrics]) => (
                <div key={family} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: `1px solid ${T.border}`, fontSize: 10 }}>
                  <span style={{ color: T.txtSec }}>{family.replace(/_/g, ' ')}</span>
                  <span style={{ color: T.txtPri }}>{safeStr(metrics.total_opps, '0')} total</span>
                </div>
              ))}
              {Object.keys(familyBreakdown).length === 0 && <div style={{ fontSize: 10, color: T.txtMuted }}>No family evidence yet.</div>}
            </div>
            <div>
              <div style={{ fontSize: 8.5, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.08em', marginBottom: 6 }}>EVIDENCE PIPELINE</div>
              {Object.entries(evidence).map(([state, count]) => (
                <div key={state} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: `1px solid ${T.border}`, fontSize: 10 }}>
                  <span style={{ color: state === 'READY_FOR_REVIEW' ? T.green : T.txtSec }}>{state.replace(/_/g, ' ')}</span>
                  <span style={{ color: T.txtPri }}>{safeStr(count)}</span>
                </div>
              ))}
              {Object.keys(evidence).length === 0 && <div style={{ fontSize: 10, color: T.txtMuted }}>Evidence aggregation is warming up.</div>}
            </div>
          </div>
        </Panel>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: 10, marginTop: 10 }} className="mb-grid-2">
        <Panel title="Top reviewed candidates" badge={<Badge label={`${data.candidates.length} ≥ minimum sample`} color={T.cyan} />}>
          <div style={{ padding: 12, overflowX: 'auto' }}>
            {data.candidates.length === 0 ? <div style={{ fontSize: 10, color: T.txtMuted }}>No experiment has reached the current minimum sample count.</div> : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                <thead><tr style={{ color: T.txtMuted, textAlign: 'left' }}>
                  {['Variant', 'Family', 'Closed', 'Avg net R', 'State'].map(label => <th key={label} style={{ padding: '0 5px 6px', fontWeight: 700, fontSize: 8.5 }}>{label}</th>)}
                </tr></thead>
                <tbody>{data.candidates.slice(0, 8).map((candidate, index) => {
                  const avg = safeNum(candidate.avg_net_r);
                  return <tr key={`${safeStr(candidate.experiment_id)}-${index}`} style={{ borderTop: `1px solid ${T.border}` }}>
                    <td style={{ padding: '7px 5px', color: T.txtPri, fontWeight: 700 }}>{safeStr(candidate.variant_name)}</td>
                    <td style={{ padding: '7px 5px', color: T.txtSec }}>{safeStr(candidate.strategy_family).replace(/_/g, ' ')}</td>
                    <td style={{ padding: '7px 5px', color: T.txtSec }}>{safeStr(candidate.closed)}</td>
                    <td style={{ padding: '7px 5px', color: avg == null ? T.txtMuted : avg >= 0 ? T.green : T.red }}>{avg == null ? '—' : `${avg.toFixed(2)}R`}</td>
                    <td style={{ padding: '7px 5px', color: candidate.evidence_state === 'READY_FOR_REVIEW' ? T.green : T.txtSec }}>{safeStr(candidate.evidence_state).replace(/_/g, ' ')}</td>
                  </tr>;
                })}</tbody>
              </table>
            )}
          </div>
        </Panel>

        <Panel title="Recent variant outcomes" badge={<Badge label={`${data.experiments.length} recent`} color={T.txtMuted} />}>
          <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {data.experiments.slice(0, 7).map((experiment, index) => {
              const netR = safeNum(experiment.net_r);
              return <div key={`${safeStr(experiment.experiment_id)}-${index}`} style={{ paddingBottom: 6, borderBottom: `1px solid ${T.border}` }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 10 }}>
                  <span style={{ color: T.txtPri, fontWeight: 700 }}>{safeStr(experiment.variant_name)}</span>
                  <span style={{ color: netR == null ? T.txtMuted : netR >= 0 ? T.green : T.red }}>{netR == null ? safeStr(experiment.status) : `${netR.toFixed(2)}R`}</span>
                </div>
                <div style={{ marginTop: 2, fontSize: 9.5, color: T.txtMuted }}>
                  {safeStr(experiment.instrument)} · {safeStr(experiment.direction)} · {safeStr(experiment.evidence_state).replace(/_/g, ' ')}
                </div>
              </div>;
            })}
            {data.experiments.length === 0 && <div style={{ fontSize: 10, color: T.txtMuted }}>No experiments returned yet.</div>}
          </div>
        </Panel>
      </div>
    </div>
  );
};

const TrainingInfrastructurePanel: React.FC<{ authHeader: string }> = ({ authHeader }) => {
  const [data, setData] = React.useState<{ health: JsonRecord | null; operations: JsonRecord | null; coordinator: JsonRecord | null; student: JsonRecord | null; events: JsonRecord[] }>({
    health: null, operations: null, coordinator: null, student: null, events: [],
  });
  const [advancedOpen, setAdvancedOpen] = React.useState(false);

  const load = React.useCallback(async () => {
    const [health, operations, coordinator, student, eventsResponse] = await Promise.all([
      getReadOnlyJson('/api/research-health', authHeader),
      getReadOnlyJson('/api/research-ops', authHeader),
      getReadOnlyJson('/api/research-coordinator-report?limit=10', authHeader),
      getReadOnlyJson('/api/market-student/health', authHeader),
      getReadOnlyJson('/api/research-events?limit=10', authHeader),
    ]);
    setData({ health, operations, coordinator, student, events: Array.isArray(eventsResponse?.events) ? eventsResponse?.events as JsonRecord[] : [] });
  }, [authHeader]);

  React.useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  const ghost = (data.health?.ghost_engine ?? {}) as JsonRecord;
  const edge = (data.health?.edge_ledger ?? {}) as JsonRecord;
  const coordinator = data.coordinator ?? {};
  const coordinatorDurable = (coordinator.durable_totals ?? {}) as JsonRecord;
  const coordinatorSession = (coordinator.restored_session_counts ?? {}) as JsonRecord;
  const coordinatorHealth = (coordinator.health_totals ?? {}) as JsonRecord;
  const durableTotalsReady = coordinatorHealth.complete === true
    || coordinatorDurable.complete === true;
  const student = data.student ?? {};
  const freshness = (student.freshness ?? {}) as JsonRecord;
  const studentOutcomes = (student.outcomes ?? {}) as JsonRecord;
  const studentAlerts = (student.ready_alerts ?? {}) as JsonRecord;

  return (
    <div>
      <TrainingReadOnlyBanner>
        Supporting health is separated from the three training lanes. It observes infrastructure only and cannot change a scheduler, database, coordinator fan-out, or execution setting.
      </TrainingReadOnlyBanner>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, margin: '2px 0 12px' }}>
        <h1 style={{ margin: 0, fontSize: 20, letterSpacing: '0.06em' }}>RESEARCH HEALTH</h1>
        <span style={{ fontSize: 10, color: T.txtMuted }}>Supporting infrastructure</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, marginBottom: 12 }} className="mb-training-stats">
        <TrainingStat label="Ghost ledger" value={ghost.table_ready === true ? 'READY' : 'PENDING'} color={ghost.table_ready === true ? T.green : T.amber} />
        <TrainingStat label="Edge ledger" value={edge.table_ready === true ? 'READY' : 'PENDING'} color={edge.table_ready === true ? T.green : T.amber} />
        <TrainingStat label="Coordinator" value={coordinator.enabled === true ? 'INTAKE ON' : 'OFF'} color={coordinator.enabled === true ? T.cyan : T.txtMuted} />
        <TrainingStat label="Fan-out" value={coordinator.fanout_enabled === true ? 'ON' : 'OFF'} color={coordinator.fanout_enabled === true ? T.red : T.green} />
      </div>
      <div style={{ marginBottom: 10 }}>
        <PaperSimulationRepairPanel authHeader={authHeader} />
      </div>
      <Panel title="Persistent Market Student" badge={<Badge label={student.money_path === 'isolated' ? 'RESEARCH ONLY' : 'PENDING'} color={student.money_path === 'isolated' ? T.green : T.amber} />}>
        <div style={{ padding: 12, display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8 }}>
          <TrainingStat label="Hypotheses" value={safeNum(student.hypothesis_count) ?? '—'} />
          <TrainingStat label="Unresolved" value={safeNum(student.unresolved_hypotheses) ?? '—'} color={(safeNum(student.unresolved_hypotheses) ?? 0) > 0 ? T.amber : T.green} />
          <TrainingStat label="Canonical link" value={safeNum(student.canonical_link_rate) == null ? '—' : `${Math.round((safeNum(student.canonical_link_rate) ?? 0) * 100)}%`} />
          <TrainingStat label="Recent expectancy" value={safeNum(studentOutcomes.recent_expectancy_r) == null ? '—' : `${safeNum(studentOutcomes.recent_expectancy_r)?.toFixed(2)}R`} color={(safeNum(studentOutcomes.recent_expectancy_r) ?? 0) >= 0 ? T.green : T.red} />
          <TrainingStat label="Last observation" value={safeNum(freshness.last_observation_age_sec) == null ? '—' : `${safeNum(freshness.last_observation_age_sec)}s ago`} />
          <TrainingStat label="DB persistence" value={student.db_ready === true ? 'READY' : 'PENDING'} color={student.db_ready === true ? T.green : T.amber} />
          <TrainingStat label="READY notifier" value={studentAlerts.enabled === true ? 'ON' : 'OFF'} color={studentAlerts.enabled === true ? T.cyan : T.txtMuted} />
          <TrainingStat label="Alert errors" value={safeNum(studentAlerts.delivery_errors) ?? 0} color={(safeNum(studentAlerts.delivery_errors) ?? 0) > 0 ? T.amber : T.green} />
        </div>
      </Panel>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }} className="mb-grid-2">
        <Panel title="Research pipeline health" badge={<Badge label={data.operations?.healthy === true ? 'HEALTHY' : data.operations?.needs_attention === true ? 'ATTENTION' : 'PENDING'} color={data.operations?.healthy === true ? T.green : T.amber} />}>
          <div style={{ padding: 12, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 10 }}>
            <TrainingStat label="Engine errors" value={safeNum(data.operations?.error_count) ?? '—'} color={(safeNum(data.operations?.error_count) ?? 0) > 0 ? T.amber : T.green} />
            <TrainingStat label="Duplicate evidence" value={safeNum(data.health?.duplicate_event_count) ?? '—'} color={(safeNum(data.health?.duplicate_event_count) ?? 0) > 0 ? T.amber : T.green} />
            <TrainingStat label="Ghost open" value={safeNum((ghost.counts as JsonRecord | undefined)?.open) ?? '—'} />
            <TrainingStat label="Edge rows" value={safeNum(edge.total_rows) ?? '—'} />
          </div>
        </Panel>
        <Panel title="Coordinator & scheduler boundary" badge={<Badge label="SHADOW ONLY" color={T.cyan} />}>
          <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8, fontSize: 10 }}>
            <div><span style={{ color: T.txtMuted }}>Persistence:</span> <span style={{ color: T.txtSec }}>{safeStr(coordinator.persistence, 'in-memory shadow only')}</span></div>
            <div><span style={{ color: T.txtMuted }}>DB readiness:</span> <span style={{ color: coordinator.persistence_db_ready === true ? T.green : T.amber }}>{coordinator.persistence_db_ready === true ? 'READY' : 'PENDING'}</span></div>
            <div><span style={{ color: T.txtMuted }}>Fan-out state:</span> <strong style={{ color: coordinator.fanout_enabled === true ? T.red : T.green }}>{coordinator.fanout_enabled === true ? 'ENABLED' : 'DISABLED'}</strong></div>
            <div><span style={{ color: T.txtMuted }}>Complete durable totals:</span> <strong style={{ color: durableTotalsReady ? T.cyan : T.amber }}>{durableTotalsReady ? `${safeNum((coordinatorHealth.opportunity_count ?? coordinatorDurable.opportunity_count)) ?? 0} opportunities · ${safeNum((coordinatorHealth.observation_count ?? coordinatorDurable.observation_count)) ?? 0} observations` : 'UNAVAILABLE'}</strong></div>
            <div><span style={{ color: T.txtMuted }}>Restored session window:</span> <span style={{ color: T.txtSec }}>{safeNum(coordinatorSession.opportunity_count) ?? safeNum(coordinator.opportunity_count) ?? 0} opportunities · {safeNum(coordinatorSession.observation_count) ?? safeNum(coordinator.opportunity_observation_count) ?? 0} observations</span></div>
            <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: 8, color: T.txtMuted }}>The dashboard reports this boundary; it does not reconfigure it.</div>
          </div>
        </Panel>
      </div>
      <Panel title="Recent research events" badge={<Badge label={`${data.events.length} buffered`} color={T.txtMuted} />} style={{ marginTop: 10 }}>
        <div style={{ padding: 12 }}>
          {data.events.length === 0 ? <div style={{ fontSize: 10, color: T.txtMuted }}>No event records returned.</div> : data.events.map((event, index) => (
            <div key={`${safeStr(event.event_id ?? event.timestamp)}-${index}`} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, padding: '6px 0', borderBottom: `1px solid ${T.border}`, fontSize: 10 }}>
              <span style={{ color: T.txtSec }}>{safeStr(event.event_type, 'EVENT').replace(/_/g, ' ')} · {safeStr(event.instrument, 'SYSTEM')}</span>
              <span style={{ color: T.txtMuted }}>{fmtAge(event.timestamp ?? event.ts)}</span>
            </div>
          ))}
        </div>
      </Panel>
      <div style={{ marginTop: 10 }}>
        <button onClick={() => setAdvancedOpen(open => !open)} style={{ width: '100%', padding: '9px 12px', borderRadius: 8, border: `1px solid ${T.border}`, background: T.panel, color: T.txtSec, cursor: 'pointer', fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', textAlign: 'left' }}>
          {advancedOpen ? '▾' : '▸'} ADVANCED OBSERVATIONAL SYSTEMS · Visual Brain, market evidence, and history
        </button>
        {advancedOpen && <div style={{ marginTop: 10 }}><VisualBrainPanel authHeader={authHeader} /></div>}
      </div>
    </div>
  );
};

const GateEffectivenessPanel: React.FC<{ authHeader: string }> = ({ authHeader }) => {
  type GateCat = {
    category: string; n_blocks: number; pct_of_blocked: number;
    unique_opps: number; avg_edge: number | null; with_geometry: number;
    would_win: number; would_lose: number; expectancy_r: number | null;
    evidence_status: string;
  };
  type HealthInfo = {
    last_observation_ts: string | null; last_resolved_ts: string | null;
    observations_24h: number; unique_opps_24h: number;
    pending_outcomes: number; resolved_outcomes: number;
    no_geometry_count: number; atr_fallback_count: number;
    collector_status: 'ACTIVE' | 'SILENT' | 'NO_DATA';
  };
  type ModeRpt = {
    available: boolean; mode: string;
    total_blocked: number; total_allowed: number;
    geometry_rate: number;
    blocked_expectancy: number | null; allowed_expectancy: number | null;
    gate_improvement: number | null;
    gate_categories: GateCat[];
    component_pass_rates: Record<string, number>;
    evidence_status: string;
    health?: HealthInfo;
  };
  type StrategyRow = {
    strategy: string; raw_evaluations: number; unique_opportunities: number;
    ready_count: number; blocked_count: number; pass_rate: number;
    resolved_count: number; would_win: number; would_lose: number;
    win_rate: number | null; net_r: number | null; avg_r: number | null;
    profit_factor: number | null; geometry_rate: number;
    atr_fallback_count: number; top_primary_blocker: string | null;
    evidence_status: string; no_geometry_count: number;
  };
  type StrategyRpt = { available: boolean; mode: string; strategies: Record<string, StrategyRow>; strategy_count: number };

  const [tab, setTab]             = React.useState<'SCALP' | 'INTRADAY_TREND'>('INTRADAY_TREND');
  const [rpts, setRpts]           = React.useState<Record<string, ModeRpt | null>>({});
  const [stratRpts, setStratRpts] = React.useState<Record<string, StrategyRpt | null>>({});
  const [loading, setLoading]     = React.useState(false);
  const [age, setAge]             = React.useState<number | null>(null);
  const [open, setOpen]           = React.useState(false);
  const [stratOpen, setStratOpen] = React.useState(false);

  const load = React.useCallback(async () => {
    if (!open) return;
    setLoading(true);
    try {
      const fetchJson = (url: string) =>
        fetch(url, { headers: { Authorization: authHeader } })
          .then(r => r.ok ? r.json() : null).catch(() => null);
      const [sr, ir, ssr, sir] = await Promise.allSettled([
        fetchJson('/api/gate-effectiveness/mode-report?mode=SCALP'),
        fetchJson('/api/gate-effectiveness/mode-report?mode=INTRADAY_TREND'),
        fetchJson('/api/gate-effectiveness/strategy-report?mode=SCALP'),
        fetchJson('/api/gate-effectiveness/strategy-report?mode=INTRADAY_TREND'),
      ]);
      const extractRpt = (res: PromiseSettledResult<unknown>) =>
        res.status === 'fulfilled' && res.value && typeof res.value === 'object'
          ? (res.value as Record<string, unknown>)['report'] as ModeRpt | null
          : null;
      const extractStrat = (res: PromiseSettledResult<unknown>) =>
        res.status === 'fulfilled' && res.value && typeof res.value === 'object'
          ? (res.value as Record<string, unknown>)['report'] as StrategyRpt | null
          : null;
      setRpts({ SCALP: extractRpt(sr), INTRADAY_TREND: extractRpt(ir) });
      setStratRpts({ SCALP: extractStrat(ssr), INTRADAY_TREND: extractStrat(sir) });
      setAge(Date.now());
    } finally {
      setLoading(false);
    }
  }, [open, authHeader]);

  React.useEffect(() => {
    load();
    const id = window.setInterval(load, 5 * 60_000);
    return () => window.clearInterval(id);
  }, [load]);

  const rpt      = (rpts[tab] ?? null) as ModeRpt | null;
  const stratRpt = (stratRpts[tab] ?? null) as StrategyRpt | null;
  const cats  = rpt?.gate_categories ?? [];
  const comps = rpt?.component_pass_rates ?? {};
  const hlth  = rpt?.health ?? null;
  const COMPS = ['BOS','CHOCH','VWAP','Sweep','Volume','CVD','Session','Zone'] as const;

  const catCol  = (pct: number) => pct >= 50 ? T.red : pct >= 20 ? T.amber : T.txtMuted;
  const rCol    = (v: number | null) => v == null ? T.txtMuted : v > 0 ? T.red : T.green;
  const fmtR    = (v: number | null) => v == null ? '—' : `${v > 0 ? '+' : ''}${v}R`;
  const compCol = (p: number) => p >= 70 ? T.green : p >= 40 ? T.amber : T.red;

  // Relative time helper
  const relTime = (iso: string | null): string => {
    if (!iso) return '—';
    const diff = (Date.now() - new Date(iso).getTime()) / 60_000;
    if (diff < 2) return 'just now';
    if (diff < 60) return `${Math.round(diff)}m ago`;
    return `${Math.round(diff / 60)}h ago`;
  };

  const statusColor = (s?: string) =>
    s === 'ACTIVE' ? T.green : s === 'SILENT' ? T.amber : T.txtMuted;

  const stratRows = stratRpt?.available
    ? Object.values(stratRpt.strategies).sort((a, b) => b.raw_evaluations - a.raw_evaluations)
    : [];

  return (
    <div style={{ marginBottom: 12, border: `1px solid ${T.border}`, borderRadius: 10, overflow: 'hidden', background: T.bg }}>
      {/* Header / toggle */}
      <div
        role="button"
        onClick={() => setOpen(o => !o)}
        style={{
          padding: '6px 12px',
          borderBottom: open ? `1px solid ${T.border}` : 'none',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          background: `${T.border}28`, cursor: 'pointer', userSelect: 'none',
        }}
      >
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: T.txtMuted }}>
          GATE EFFECTIVENESS AUDIT
        </span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {!open && rpts['INTRADAY_TREND']?.total_blocked != null && (
            <span style={{ fontSize: 9, color: T.txtMuted }}>
              IT {rpts['INTRADAY_TREND']!.total_blocked} blocked · {rpts['INTRADAY_TREND']!.geometry_rate}% geom
            </span>
          )}
          {open && age && (
            <span style={{ fontSize: 9, color: `${T.txtMuted}70` }}>
              {loading ? 'loading…' : `${Math.round((Date.now() - age) / 60_000)}m ago`}
            </span>
          )}
          <span style={{ fontSize: 10, color: T.txtMuted }}>{open ? '▾' : '▸'}</span>
        </div>
      </div>

      {open && (
        <div style={{ padding: '10px 12px' }}>
          {/* Mode tabs */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
            {(['SCALP', 'INTRADAY_TREND'] as const).map(m => (
              <button key={m} onClick={() => setTab(m)} style={{
                fontSize: 9, fontWeight: tab === m ? 700 : 400, padding: '3px 10px',
                borderRadius: 5, border: `1px solid ${T.border}`,
                background: tab === m ? `${T.cyan}20` : 'transparent',
                color: tab === m ? T.cyan : T.txtMuted, cursor: 'pointer',
              }}>
                {m === 'INTRADAY_TREND' ? 'INTRADAY TREND' : 'SCALP'}
              </button>
            ))}
          </div>

          {!rpt?.available ? (
            <div style={{ fontSize: 10, color: T.txtMuted, textAlign: 'center', padding: '12px 0' }}>
              {loading ? 'Loading…' : 'No data available for this mode.'}
            </div>
          ) : (<>

            {/* ── Collector health strip ──────────────────────────────────── */}
            {hlth && (
              <div style={{
                display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center',
                marginBottom: 10, padding: '5px 8px',
                background: `${T.border}12`, borderRadius: 6,
                borderLeft: `3px solid ${statusColor(hlth.collector_status)}`,
              }}>
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <span style={{
                    fontSize: 8.5, fontWeight: 700,
                    color: statusColor(hlth.collector_status),
                    letterSpacing: '0.06em',
                  }}>
                    {hlth.collector_status}
                  </span>
                </div>
                {[
                  { l: 'Last obs',     v: relTime(hlth.last_observation_ts) },
                  { l: 'Last resolved', v: relTime(hlth.last_resolved_ts) },
                  { l: 'Obs/24h',      v: String(hlth.observations_24h) },
                  { l: 'Opps/24h',     v: String(hlth.unique_opps_24h) },
                  { l: 'Pending',      v: String(hlth.pending_outcomes) },
                  { l: 'Resolved',     v: String(hlth.resolved_outcomes) },
                  { l: 'No-geom',      v: String(hlth.no_geometry_count) },
                  { l: 'ATR-fb',       v: String(hlth.atr_fallback_count) },
                ].map(({ l, v }) => (
                  <div key={l} style={{ display: 'flex', flexDirection: 'column' }}>
                    <span style={{ fontSize: 7.5, color: `${T.txtMuted}80` }}>{l}</span>
                    <span style={{ fontSize: 9.5, fontWeight: 600, color: T.txtPri }}>{v}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Summary chips */}
            <div style={{
              display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 10,
              padding: '6px 8px', background: `${T.border}18`, borderRadius: 6,
            }}>
              {[
                { l: 'Observations',  v: String(rpt.total_blocked + rpt.total_allowed), c: T.txtMuted },
                { l: 'Geometry rate', v: `${rpt.geometry_rate}%`, c: rpt.geometry_rate > 30 ? T.green : T.amber },
                { l: 'Evidence',      v: rpt.evidence_status,     c: T.txtMuted },
                ...(rpt.blocked_expectancy != null
                  ? [{ l: 'Blocked exp.', v: fmtR(rpt.blocked_expectancy), c: rCol(rpt.blocked_expectancy) }]
                  : []),
                ...(rpt.gate_improvement != null
                  ? [{ l: 'Gate value', v: fmtR(rpt.gate_improvement), c: rpt.gate_improvement > 0 ? T.green : T.amber }]
                  : []),
              ].map(({ l, v, c }) => (
                <div key={l} style={{ display: 'flex', flexDirection: 'column' }}>
                  <span style={{ fontSize: 8, color: `${T.txtMuted}80`, marginBottom: 1 }}>{l}</span>
                  <span style={{ fontSize: 10, fontWeight: 700, color: c }}>{v}</span>
                </div>
              ))}
            </div>

            {/* Gate category table */}
            <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.07em', marginBottom: 4 }}>
              GATE BREAKDOWN
            </div>
            <div style={{ overflowX: 'auto', marginBottom: 10 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${T.border}40` }}>
                    {['Gate','Blocks','%','Unique','Geom','W-Win','W-Lose','Exp.R'].map(h => (
                      <th key={h} style={{
                        padding: '2px 5px', fontSize: 8.5, color: T.txtMuted, fontWeight: 600,
                        textAlign: h === 'Gate' ? 'left' : 'right', whiteSpace: 'nowrap',
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {cats.map(c => (
                    <tr key={c.category} style={{ borderBottom: `1px solid ${T.border}15` }}>
                      <td style={{ padding: '4px 5px', color: catCol(c.pct_of_blocked), fontWeight: 600, whiteSpace: 'nowrap' }}>
                        {c.category}
                      </td>
                      <td style={{ padding: '4px 5px', textAlign: 'right', color: T.txtPri }}>{c.n_blocks}</td>
                      <td style={{ padding: '4px 5px', textAlign: 'right', fontWeight: 700, color: catCol(c.pct_of_blocked) }}>
                        {c.pct_of_blocked}%
                      </td>
                      <td style={{ padding: '4px 5px', textAlign: 'right', color: T.txtMuted }}>{c.unique_opps}</td>
                      <td style={{ padding: '4px 5px', textAlign: 'right', color: c.with_geometry > 0 ? T.green : T.txtMuted }}>
                        {c.with_geometry > 0 ? c.with_geometry : '—'}
                      </td>
                      <td style={{ padding: '4px 5px', textAlign: 'right', color: c.would_win > 0 ? T.red : T.txtMuted }}>
                        {c.would_win > 0 ? c.would_win : '—'}
                      </td>
                      <td style={{ padding: '4px 5px', textAlign: 'right', color: c.would_lose > 0 ? T.green : T.txtMuted }}>
                        {c.would_lose > 0 ? c.would_lose : '—'}
                      </td>
                      <td style={{ padding: '4px 5px', textAlign: 'right', fontWeight: c.expectancy_r != null ? 700 : 400,
                        color: rCol(c.expectancy_r) }}>
                        {fmtR(c.expectancy_r)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Component pass rates */}
            <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.07em', marginBottom: 4 }}>
              COMPONENT PASS RATES
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 12 }}>
              {COMPS.filter(k => comps[k] != null).map(k => {
                const pct = comps[k] ?? 0;
                return (
                  <div key={k} style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'center',
                    minWidth: 44, padding: '4px 6px', background: `${T.border}18`, borderRadius: 5,
                  }}>
                    <span style={{ fontSize: 7.5, color: T.txtMuted, marginBottom: 2 }}>{k}</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: compCol(pct) }}>{Math.round(pct)}%</span>
                  </div>
                );
              })}
            </div>

            {/* ── Strategy breakdown (collapsible) ───────────────────────── */}
            <div
              role="button"
              onClick={() => setStratOpen(o => !o)}
              style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                cursor: 'pointer', marginBottom: stratOpen ? 6 : 0,
              }}
            >
              <span style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.07em' }}>
                STRATEGIES {stratRows.length > 0 ? `(${stratRows.length})` : ''}
              </span>
              <span style={{ fontSize: 9, color: T.txtMuted }}>{stratOpen ? '▾' : '▸'}</span>
            </div>
            {stratOpen && (
              stratRows.length === 0 ? (
                <div style={{ fontSize: 10, color: T.txtMuted, padding: '8px 0' }}>
                  No strategy data yet.
                </div>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 9.5 }}>
                    <thead>
                      <tr style={{ borderBottom: `1px solid ${T.border}40` }}>
                        {['Strategy','Evals','Opps','Pass%','Win%','Net R','Avg R','PF','Geom%','ATR-fb','Top Blocker'].map(h => (
                          <th key={h} style={{
                            padding: '2px 5px', fontSize: 8, color: T.txtMuted, fontWeight: 600,
                            textAlign: h === 'Strategy' || h === 'Top Blocker' ? 'left' : 'right',
                            whiteSpace: 'nowrap',
                          }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {stratRows.map(s => (
                        <tr key={s.strategy} style={{ borderBottom: `1px solid ${T.border}15` }}>
                          <td style={{ padding: '4px 5px', color: T.txtPri, fontWeight: 600, whiteSpace: 'nowrap', maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {s.strategy}
                          </td>
                          <td style={{ padding: '4px 5px', textAlign: 'right', color: T.txtMuted }}>{s.raw_evaluations}</td>
                          <td style={{ padding: '4px 5px', textAlign: 'right', color: T.txtMuted }}>{s.unique_opportunities}</td>
                          <td style={{ padding: '4px 5px', textAlign: 'right', color: s.pass_rate > 20 ? T.amber : T.txtMuted }}>
                            {s.pass_rate}%
                          </td>
                          <td style={{ padding: '4px 5px', textAlign: 'right',
                            color: s.win_rate == null ? T.txtMuted : s.win_rate >= 50 ? T.green : T.red }}>
                            {s.win_rate != null ? `${s.win_rate}%` : '—'}
                          </td>
                          <td style={{ padding: '4px 5px', textAlign: 'right', fontWeight: s.net_r != null ? 700 : 400,
                            color: rCol(s.net_r) }}>{fmtR(s.net_r)}</td>
                          <td style={{ padding: '4px 5px', textAlign: 'right', color: rCol(s.avg_r) }}>{fmtR(s.avg_r)}</td>
                          <td style={{ padding: '4px 5px', textAlign: 'right',
                            color: s.profit_factor == null ? T.txtMuted : s.profit_factor >= 1.5 ? T.green : s.profit_factor >= 1 ? T.amber : T.red }}>
                            {s.profit_factor != null ? s.profit_factor.toFixed(2) : '—'}
                          </td>
                          <td style={{ padding: '4px 5px', textAlign: 'right',
                            color: s.geometry_rate > 50 ? T.green : T.txtMuted }}>
                            {s.geometry_rate}%
                          </td>
                          <td style={{ padding: '4px 5px', textAlign: 'right',
                            color: s.atr_fallback_count > 0 ? T.amber : T.txtMuted }}>
                            {s.atr_fallback_count || '—'}
                          </td>
                          <td style={{ padding: '4px 5px', color: T.txtMuted, fontSize: 8.5,
                            maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {s.top_primary_blocker ?? '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            )}
          </>)}
        </div>
      )}
    </div>
  );
};

// ── Cleanest Trade Button ─────────────────────────────────────────────────────
const CleanestTradeButton: React.FC<{
  scanResult:   CleanestScanResult | null;
  scanning:     boolean;
  onScan:       () => void;
  setOpen:      () => void;
}> = ({ scanResult, scanning, onScan, setOpen }) => {
  const cand = scanResult?.candidate ?? null;

  // Derive button appearance state
  let btnLabel: string;
  let btnColor: string;
  let btnBg:    string;
  let supportEl: React.ReactNode;

  if (scanning) {
    btnLabel = '🔎  Scanning all instruments…';
    btnColor = T.txtMuted;
    btnBg    = 'transparent';
    supportEl = null;
  } else if (!scanResult) {
    // Never scanned
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.cyan;
    btnBg     = `${T.cyan}12`;
    supportEl = (
      <span style={{ fontSize:9.5, color:T.txtMuted }}>
        Click to scan all instruments × modes for the best current setup
      </span>
    );
  } else if (scanResult.error) {
    // State D — unavailable
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.red;
    btnBg     = `${T.red}10`;
    supportEl = (
      <span style={{ fontSize:9.5, color:T.red }}>TRADE RANKING UNAVAILABLE — {scanResult.error}</span>
    );
  } else if (!cand) {
    // State C — no qualifying trade
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.txtMuted;
    btnBg     = 'transparent';
    supportEl = (
      <span style={{ fontSize:9.5, color:T.txtMuted }}>
        NO QUALIFYING TRADE — no actionable or potential setups found across scanned instruments
      </span>
    );
  } else if (cand.act === 1) {
    // State A — clean ready trade
    const stratLabel = safeStr(
      (cand.record.strategy_engine as Record<string, unknown> | undefined)?.active_strategy, ''
    );
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.green;
    btnBg     = `${T.green}10`;
    supportEl = (
      <span style={{ fontSize:9.5, color:T.green, letterSpacing:'0.04em' }}>
        ✓ {cand.instrument} {cand.direction}
        {stratLabel ? ` · ${stratLabel}` : ''}
        {' · '}{Math.round(cand.edge)}/110
        {scanResult.isPartial && (
          <span style={{ color:T.amber, marginLeft:4 }}>
            · PARTIAL ({scanResult.succeeded}/{scanResult.total} evaluated)
          </span>
        )}
      </span>
    );
  } else {
    // State B — potential only
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.amber;
    btnBg     = `${T.amber}10`;
    supportEl = (
      <span style={{ fontSize:9.5, color:T.amber, letterSpacing:'0.04em' }}>
        ⏳ POTENTIAL — {cand.instrument} {cand.direction} · {Math.round(cand.edge)}/110
        {scanResult.isPartial && (
          <span style={{ marginLeft:4 }}>
            · PARTIAL ({scanResult.succeeded}/{scanResult.total} evaluated)
          </span>
        )}
      </span>
    );
  }

  const handleClick = () => {
    if (scanning) return;
    if (!scanResult) {
      onScan();
    } else {
      setOpen();
    }
  };

  return (
    <div style={{
      display:'flex', alignItems:'center', gap:14, flexWrap:'wrap',
      background:`${T.panel}`, border:`1px solid ${T.border}`, borderRadius:10,
      padding:'10px 16px', marginBottom:12,
    }}>
      <button
        onClick={handleClick}
        disabled={scanning}
        aria-label="Scan all instruments for the cleanest trade"
        style={{
          background:btnBg, border:`1.5px solid ${btnColor}44`, color:btnColor,
          borderRadius:8, padding:'7px 18px', cursor: scanning ? 'not-allowed' : 'pointer',
          fontSize:11, fontWeight:700, letterSpacing:'0.07em', opacity: scanning ? 0.7 : 1,
          transition:'all 0.2s', whiteSpace:'nowrap',
        }}
      >
        {btnLabel}
      </button>
      {supportEl && (
        <div style={{ flex:1, minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
          {supportEl}
        </div>
      )}
      {/* Re-scan button shown after first scan */}
      {scanResult && !scanning && (
        <button
          onClick={onScan}
          aria-label="Re-scan for cleanest trade"
          style={{
            background:'transparent', border:`1px solid ${T.border}`, color:T.txtMuted,
            borderRadius:6, padding:'4px 10px', cursor:'pointer', fontSize:9.5, fontWeight:600,
            transition:'opacity 0.2s', marginLeft:'auto', whiteSpace:'nowrap',
          }}
        >
          ↻ Re-scan
        </button>
      )}
    </div>
  );
};

// ── Send-to-TradersPost eligibility (pure, no side-effects) — exported for tests
//
// Frontend gate: any check here that returns eligible=false disables the button
// with an exact reason. Backend ALWAYS revalidates all conditions before any
// order is placed — this layer exists only for UX clarity.
//
// Checked conditions:
//   1.  Plan status must be READY (not POTENTIAL, NO_CANDIDATE, UNAVAILABLE)
//   2.  Verdict.is_actionable must be true (not false/undefined)
//   3.  Direction must be present
//   4.  Entry zone must be present
//   5.  Stop loss must be present and stop_valid !== false
//   6.  Take profit target must be present
//   7.  No hard blockers in verdict
//   8.  Database must be ready (sys.db_ready)
//   9.  Risk ops must not be in a blocking state
//   10. No active trade conflict (backend also blocks, but surface here)
//   11. Plan must be fresh (< 5 minutes old based on generated_at)
export type MbSendEligibility = { eligible: boolean; disabledLabel: string };

export function getMbSendEligibility(p: Record<string, unknown>): MbSendEligibility {
  const cp     = (p.candidate_preview ?? {}) as Record<string, unknown>;
  const vrd    = (p.verdict           ?? {}) as Record<string, unknown>;
  const at     = (p.active_trades     ?? {}) as Record<string, unknown>;
  const trades = Array.isArray(at.trades) ? at.trades as unknown[] : [];
  const sys    = (p.system_status     ?? {}) as Record<string, unknown>;
  const ro     = (p.risk_ops          ?? {}) as Record<string, unknown>;

  // 1. Plan status
  const planStatus = safeStr(cp.status, '');
  if (planStatus !== 'READY') {
    const miss = Array.isArray(cp.missing_confirmations)
      ? (cp.missing_confirmations as string[]) : [];
    return { eligible: false,
             disabledLabel: miss.length > 0
               ? 'WAITING FOR ' + String(miss[0]).toUpperCase().slice(0, 28)
               : planStatus === 'POTENTIAL' ? 'SETUP STILL FORMING'
               : 'TRADE NOT READY' };
  }

  // 2. Verdict actionable — is_actionable is explicitly false (undefined means we can't tell,
  //    defer to backend rather than block)
  if (vrd.is_actionable === false) {
    return { eligible: false, disabledLabel: 'TRADE NOT ACTIONABLE' };
  }

  // 3. Direction
  if (!cp.direction) {
    return { eligible: false, disabledLabel: 'DIRECTION MISSING' };
  }

  // 4. Entry
  if (cp.entry_zone == null) {
    return { eligible: false, disabledLabel: 'ENTRY UNAVAILABLE' };
  }

  // 5. Stop — present and valid
  if (cp.stop_loss == null) {
    return { eligible: false, disabledLabel: 'STOP MISSING' };
  }
  if (cp.stop_valid === false) {
    return { eligible: false, disabledLabel: 'STOP INVALID' };
  }

  // 6. Target
  if (cp.take_profit == null) {
    return { eligible: false, disabledLabel: 'TARGET MISSING' };
  }

  // 7. Hard blockers from verdict
  const blockers = Array.isArray(vrd.hard_blockers) ? vrd.hard_blockers as string[] : [];
  if (blockers.length > 0) {
    return { eligible: false,
             disabledLabel: 'VETO — ' + String(blockers[0]).slice(0, 32) };
  }

  // 8. Database
  if (sys.db_ready === false) {
    return { eligible: false, disabledLabel: 'DATABASE NOT READY' };
  }

  // 9. Risk ops
  const roState = safeStr(ro.state, '');
  if (roState === 'DAILY_LIMIT')      return { eligible: false, disabledLabel: 'DAILY LIMIT REACHED' };
  if (roState === 'DRAWDOWN')         return { eligible: false, disabledLabel: 'DRAWDOWN LIMIT HIT' };
  if (roState === 'NO_ACCOUNT')       return { eligible: false, disabledLabel: 'NO PROP ACCOUNT' };
  if (ro.execution_allowed === false) return { eligible: false, disabledLabel: 'RISK STATE BLOCKED' };

  // 10. Active trade conflict
  if (trades.length > 0) {
    return { eligible: false, disabledLabel: 'ACTIVE TRADE CONFLICT' };
  }

  // 11. Plan freshness (5-minute cap — backend check is authoritative, this avoids obvious stale UI)
  const genAt = cp.generated_at as string | null | undefined;
  if (genAt) {
    const ageMs = Date.now() - new Date(String(genAt)).getTime();
    if (!isNaN(ageMs) && ageMs > 5 * 60 * 1000) {
      return { eligible: false, disabledLabel: 'PLAN STALE' };
    }
  }

  return { eligible: true, disabledLabel: '' };
}

// ── MbSendModal — two-phase modal: confirm → sending → done ──────────────────
//
// Phase 1 (confirm): shows trade details for operator review.
//   • CANCEL — closes, nothing sent.
//   • CONFIRM AND SEND — calls onConfirm() once (sentRef prevents double-fire).
//
// Phase 2 (sending): spinner while awaiting backend response.
//
// Phase 3 (done): shows outcome:
//   • success  → SENT TO TRADERSPOST / PAPER ORDER SIMULATED / MANUAL PLAN RETURNED
//   • rejected → NOT SENT + exact backend reason
//   • unknown  → STATUS UNKNOWN — VERIFY BEFORE RETRYING (no auto-retry ever)
//
// Idempotency: sentRef blocks double-clicks. The backend also enforces a 60-second
// fingerprint-based dedup cooldown (TRADERSPOST_COOLDOWN_SEC) — if a retry within
// that window would match, the backend returns 429 "Duplicate order suppressed".
type MbSendOutcome =
  | { type: 'success';  status: string; message: string;
      plan: Record<string, unknown> | null; ts: string }
  | { type: 'rejected'; reason: string; ts: string }
  | { type: 'unknown';  ts: string };

// Session send log — resets on page refresh, max 10 entries (oldest drops off).
interface SendLogEntry {
  ts:         string;
  instrument: string;
  direction:  string | null | undefined;
  entryPrice: string | null;
  outcome:    MbSendOutcome;
}

const MbSendModal: React.FC<{
  p:         Record<string, unknown>;
  onClose:   () => void;
  onConfirm: () => Promise<MbSendOutcome>;
}> = ({ p, onClose, onConfirm }) => {
  const [phase,  setPhase]  = useState<'confirm' | 'sending' | 'done'>('confirm');
  const [result, setResult] = useState<MbSendOutcome | null>(null);
  const sentRef = useRef(false);

  const cp  = (p.candidate_preview ?? {}) as Record<string, unknown>;
  const mkt = (p.market            ?? {}) as Record<string, unknown>;
  const sc  = (p.strategy_scanner  ?? {}) as Record<string, unknown>;
  const vrd = (p.verdict           ?? {}) as Record<string, unknown>;

  const instrument = safeStr(mkt.instrument,    '—');
  const direction  = safeStr(cp.direction,      '—');
  const strategy   = safeStr(
    (sc.selected_strategy ?? sc.selected) as unknown, '—');
  const mode       = safeStr(
    (mkt.mode ?? mkt.trading_mode) as unknown, '—');
  const entryZone  = safeStr(cp.entry_zone,     '—');
  const stopLoss   = safeStr(cp.stop_loss,      '—');
  const target     = safeStr(cp.take_profit,    '—');
  const rr         = safeStr(cp.risk_reward,    '—');
  const riskDollar = cp.risk_dollars_per_contract != null
    ? `$${fmtNum(cp.risk_dollars_per_contract, 0)}` : '—';
  const readiness  = safeStr(vrd.readiness_label as unknown ?? 'READY');
  const dataAge    = fmtAge(cp.generated_at) || '—';
  const isLong     = /long/i.test(direction);
  const dirC       = isLong ? T.green : T.red;

  const handleConfirm = async () => {
    if (sentRef.current) return;          // idempotency guard
    sentRef.current = true;
    setPhase('sending');
    const r = await onConfirm();
    setResult(r);
    setPhase('done');
  };

  const overlay: React.CSSProperties = {
    position:'fixed', inset:0, zIndex:9990,
    background:'rgba(0,0,0,0.72)',
    display:'flex', alignItems:'center', justifyContent:'center',
    backdropFilter:'blur(4px)',
  };
  const box: React.CSSProperties = {
    background:T.panel, border:`1px solid ${T.borderMid}`,
    borderRadius:14, width:420, maxWidth:'calc(100vw - 32px)',
    padding:'22px 24px',
    animation:'mbSendSlideIn 0.17s ease-out',
    boxShadow:'0 16px 48px rgba(0,0,0,0.6)',
    maxHeight:'90vh', overflowY:'auto',
  };

  // Detail row — used inside both confirm and result views
  const SRow = ({ label, value, color }: { label:string; value:string; color?:string }) => (
    <div style={{ display:'flex', justifyContent:'space-between',
                  alignItems:'baseline',
                  borderBottom:`1px solid ${T.border}`, padding:'5px 0' }}>
      <span style={{ fontSize:10.5, color:T.txtMuted, letterSpacing:'0.03em',
                     flexShrink:0 }}>{label}</span>
      <span style={{ fontSize:11.5, color:color??T.txtPri, fontFamily:T.mono,
                     fontWeight:600, textAlign:'right', marginLeft:8 }}>{value}</span>
    </div>
  );

  // ── Confirm / Sending phases ──────────────────────────────────────────────
  if (phase === 'confirm' || phase === 'sending') {
    return (
      <div style={overlay} onClick={phase==='confirm' ? onClose : undefined}>
        <div style={box} onClick={e => e.stopPropagation()}>

          {/* Header */}
          <div style={{ display:'flex', justifyContent:'space-between',
                        alignItems:'center', marginBottom:14 }}>
            <div style={{ fontSize:12, fontWeight:700, color:T.cyan,
                          letterSpacing:'0.07em' }}>
              CONFIRM ORDER
            </div>
            {phase === 'confirm' && (
              <button onClick={onClose} aria-label="Cancel"
                style={{ background:'transparent', border:'none',
                         color:T.txtMuted, cursor:'pointer',
                         fontSize:20, lineHeight:1, padding:0 }}>×</button>
            )}
          </div>

          {/* Trade detail rows */}
          <SRow label="Instrument"    value={instrument} />
          <SRow label="Direction"     value={direction.toUpperCase()} color={dirC} />
          <SRow label="Strategy"      value={strategy} />
          <SRow label="Mode"          value={mode} />
          <SRow label="Entry Zone"    value={entryZone} color={T.cyan} />
          <SRow label="Stop Loss"     value={stopLoss}  color={T.red} />
          <SRow label="Take Profit"   value={target}    color={T.green} />
          <SRow label="Risk / Reward" value={rr} />
          <SRow label="Contracts"     value="1 (server-sized to risk cap)" />
          <SRow label="Dollar Risk"   value={riskDollar} />
          <SRow label="Readiness"     value={readiness}  color={T.green} />
          <SRow label="Data Age"      value={dataAge}    color={T.amber} />

          {/* Safety notice */}
          <div style={{ marginTop:10, padding:'7px 10px', borderRadius:6,
                        background:'rgba(239,68,68,0.07)',
                        border:`1px solid rgba(239,68,68,0.18)` }}>
            <div style={{ fontSize:9.5, color:'rgba(239,68,68,0.75)', lineHeight:1.5 }}>
              ⚠ This sends a LIVE order through the existing TradersPost execution
              gateway. The backend revalidates all safety conditions before
              any order is placed. Contracts are sized server-side to your risk cap.
            </div>
          </div>

          {/* Actions */}
          {phase === 'confirm' ? (
            <div style={{ display:'flex', gap:10, marginTop:14 }}>
              <button onClick={onClose}
                style={{ flex:1, padding:'9px 0', borderRadius:8, cursor:'pointer',
                         fontSize:11, fontWeight:700,
                         background:'transparent', border:`1px solid ${T.border}`,
                         color:T.txtSec, letterSpacing:'0.06em' }}>
                CANCEL
              </button>
              <button onClick={handleConfirm}
                style={{ flex:2, padding:'9px 0', borderRadius:8, cursor:'pointer',
                         fontSize:11, fontWeight:700,
                         background: isLong
                           ? 'rgba(34,197,94,0.13)' : 'rgba(239,68,68,0.13)',
                         border:`1px solid ${dirC}`,
                         color:dirC, letterSpacing:'0.06em' }}>
                CONFIRM AND SEND
              </button>
            </div>
          ) : (
            <div style={{ marginTop:14, textAlign:'center',
                          color:T.txtMuted, fontSize:11 }}>
              <span style={{ animation:'mbDot 0.8s infinite', marginRight:6 }}>◌</span>
              Sending to TradersPost…
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Done phase ────────────────────────────────────────────────────────────
  const r         = result!;
  const isSuccess = r.type === 'success';
  const isUnknown = r.type === 'unknown';
  const outColor  = isSuccess ? T.green : isUnknown ? T.amber : T.red;
  const outIcon   = isSuccess ? '✓' : isUnknown ? '⚠' : '✗';
  const outLabel  = isSuccess
    ? (r.status === 'sent'           ? 'SENT TO TRADERSPOST'
     : r.status === 'simulated'      ? 'PAPER ORDER SIMULATED'
     :                                  'MANUAL PLAN RETURNED')
    : isUnknown
    ? 'STATUS UNKNOWN — VERIFY BEFORE RETRYING'
    : 'NOT SENT';

  const tsStr = r.ts
    ? new Date(r.ts).toLocaleTimeString('en-US',
        { hour:'2-digit', minute:'2-digit', second:'2-digit',
          hour12:true, timeZone:'Etc/GMT+4' }) + ' UTC-4'
    : '';

  const successPlan = isSuccess
    ? (r as Extract<MbSendOutcome, {type:'success'}>).plan : null;
  const rejectedReason = !isSuccess && !isUnknown
    ? (r as Extract<MbSendOutcome, {type:'rejected'}>).reason : null;
  const successMsg = isSuccess
    ? (r as Extract<MbSendOutcome, {type:'success'}>).message : '';

  return (
    <div style={overlay}>
      <div style={box} onClick={e => e.stopPropagation()}>

        {/* Result header */}
        <div style={{ textAlign:'center', padding:'10px 0 6px' }}>
          <div style={{ fontSize:30, color:outColor }}>{outIcon}</div>
          <div style={{ fontSize:13, fontWeight:700, color:outColor,
                        letterSpacing:'0.07em', marginTop:8 }}>
            {outLabel}
          </div>
          <div style={{ fontSize:11, color:T.txtSec, marginTop:6, minHeight:16,
                        lineHeight:1.5 }}>
            {isUnknown
              ? 'Network error — verify on your broker before retrying.'
              : rejectedReason ?? successMsg}
          </div>
          {tsStr && (
            <div style={{ fontSize:10, color:T.txtMuted, marginTop:6,
                          fontFamily:T.mono }}>
              {tsStr}
            </div>
          )}
        </div>

        {/* Confirmed plan detail block (success only) */}
        {successPlan && (
          <div style={{ marginTop:10, padding:'8px 10px', borderRadius:6,
                        background:'rgba(56,189,248,0.05)',
                        border:`1px solid rgba(56,189,248,0.12)` }}>
            {successPlan.direction  != null &&
              <SRow label="Direction"   value={String(successPlan.direction)}  color={dirC}   />}
            {successPlan.entry      != null &&
              <SRow label="Entry"       value={String(successPlan.entry)}      color={T.cyan} />}
            {successPlan.stopLoss   != null &&
              <SRow label="Stop"        value={String(successPlan.stopLoss)}   color={T.red}  />}
            {successPlan.takeProfit != null &&
              <SRow label="TP"          value={String(successPlan.takeProfit)} color={T.green}/>}
            {successPlan.quantity   != null &&
              <SRow label="Qty"         value={String(successPlan.quantity)}   />}
          </div>
        )}

        {/* Close */}
        <button onClick={onClose}
          style={{ width:'100%', marginTop:16, padding:'9px 0', borderRadius:8,
                   cursor:'pointer', fontSize:11, fontWeight:700,
                   background:'transparent', border:`1px solid ${T.border}`,
                   color:T.txtSec, letterSpacing:'0.06em' }}>
          CLOSE
        </button>
      </div>
    </div>
  );
};

// ── Trade Plan Panel ──────────────────────────────────────────────────────────
const TradePlanPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  // Source: p.candidate_preview — normalised by _mb_candidate_preview() on the backend.
  // States: READY | POTENTIAL | NO_CANDIDATE | UNAVAILABLE
  // Active-trade awareness: when a live position exists and a preview is also present,
  // the preview is clearly labelled as a FUTURE CANDIDATE — never confused with the
  // live position managed by ActiveTradesPanel.
  const cp     = (p.candidate_preview ?? {}) as Record<string, unknown>;
  const at     = (p.active_trades    ?? {}) as Record<string, unknown>;
  const trades = Array.isArray(at.trades) ? at.trades as Record<string, unknown>[] : [];
  const hasActiveTrade = trades.length > 0;

  const status      = safeStr(cp.status, 'NO_CANDIDATE');
  const direction   = cp.direction as string | null | undefined;
  const isReady     = status === 'READY';
  const isPotential = status === 'POTENTIAL';
  const hasPlan     = isReady || isPotential;

  const statusLabel = isReady ? 'READY' : isPotential ? 'POTENTIAL' : 'NO CANDIDATE';
  const statusColor = isReady ? T.green  : isPotential ? T.amber    : T.txtMuted;

  const missing = Array.isArray(cp.missing_confirmations)
    ? (cp.missing_confirmations as string[])
    : [];

  // ── Send-to-TradersPost state ─────────────────────────────────────────────
  const [modalOpen,  setModalOpen]  = useState(false);
  const [sendResult, setSendResult] = useState<MbSendOutcome | null>(null);
  const [sendLog,    setSendLog]    = useState<SendLogEntry[]>([]);
  const sendingRef = useRef(false);

  const eligibility = getMbSendEligibility(p);

  // Calls /api/traderspost via the existing endpoint (same path as Home.tsx ENTER button).
  // Server-authoritative: backend recomputes full_analysis, re-runs every gate (market,
  // risk cap, prop guard, training, dedup cooldown, is_actionable, stop validity, etc.)
  // regardless of frontend eligibility state. Client sends only ticker + contracts: 1.
  const handleMbSend = useCallback(async (): Promise<MbSendOutcome> => {
    const ts         = new Date().toISOString();
    const instrument = safeStr((p.market as Record<string, unknown>)?.instrument, '');
    if (!instrument) return { type: 'rejected', reason: 'No instrument selected.', ts };
    try {
      const r = await fetch('/api/traderspost', {
        method:      'POST',
        credentials: 'include',
        headers:     { 'Content-Type': 'application/json', ...getAuthHeader() },
        body:        JSON.stringify({ ticker: instrument, contracts: 1 }),
      });
      let j: Record<string, unknown> = {};
      try { j = await r.json() as Record<string, unknown>; } catch { /* leave empty */ }
      const status = String(j.status ?? '');
      if (status === 'sent' || status === 'simulated' || status === 'manual_required') {
        return { type: 'success', status,
                 message: String(j.message ?? ''),
                 plan: (j.plan as Record<string, unknown> | null) ?? null, ts };
      }
      // 429 = duplicate suppressed; other 4xx/5xx = gateway rejection
      const reason = String(j.reason ?? j.error ?? 'Gateway rejected.');
      return { type: 'rejected', reason, ts };
    } catch {
      // Network-level failure — outcome unknown; never auto-retry
      return { type: 'unknown', ts };
    } finally {
      sendingRef.current = false;
    }
  }, [p]);

  const openModal = () => {
    if (!eligibility.eligible) return;
    sendingRef.current = false;   // reset guard so a fresh confirmation can fire
    setSendResult(null);
    setModalOpen(true);
  };

  return (
    <>
      <Panel title="Trade Plan">
        {status === 'UNAVAILABLE' ? (
          <UnavailableNote msg="Preview unavailable" />
        ) : !hasPlan ? (
          <div style={{ textAlign: 'center', padding: '20px 0', color: T.txtMuted, fontSize: 11 }}>
            No trade candidate developing
          </div>
        ) : (
          <>
            {/* Status header ─────────────────────────────────────────────────── */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              {direction != null && <Pill text={String(direction)} color={dirColor(direction)} />}
              <Pill text={statusLabel} color={statusColor} />
              {hasActiveTrade && isPotential && (
                <span style={{ fontSize: 9, color: T.amber, marginLeft: 'auto', letterSpacing: '0.06em', fontFamily: T.mono }}>
                  FUTURE CANDIDATE
                </span>
              )}
            </div>

            {/* Core trade levels ──────────────────────────────────────────────── */}
            {cp.entry_zone   != null && <KV label="Entry Zone"    value={safeStr(cp.entry_zone)}   mono valueColor={T.cyan}  />}
            {cp.stop_loss    != null && <KV label="Stop Loss"     value={safeStr(cp.stop_loss)}    mono valueColor={T.red}   />}
            {cp.take_profit  != null && <KV label="Take Profit"   value={safeStr(cp.take_profit)}  mono valueColor={T.green} />}
            {cp.risk_reward  != null && <KV label="Risk / Reward" value={safeStr(cp.risk_reward)}  mono />}

            {/* Risk sizing ───────────────────────────────────────────────────── */}
            {cp.risk_points               != null && <KV label="Risk (pts)"      value={fmtNum(cp.risk_points, 2)}               mono />}
            {cp.risk_dollars_per_contract != null && <KV label="Risk / Contract" value={`$${fmtNum(cp.risk_dollars_per_contract, 0)}`} mono />}

            {/* ATR stop metadata ─────────────────────────────────────────────── */}
            {(cp.atr != null || cp.stop_ticks != null) && (
              <div style={{ marginTop: 7, paddingTop: 7, borderTop: `1px solid ${T.border}` }}>
                {cp.atr != null && cp.atr_multiplier != null && (
                  <KV label="ATR Stop" value={`${fmtNum(cp.atr, 4)} pts × ${fmtNum(cp.atr_multiplier, 1)}`} mono />
                )}
                {cp.stop_ticks != null && (
                  <KV label="Stop Distance" value={`${String(cp.stop_ticks)} ticks`} mono />
                )}
                {cp.stop_valid === false && cp.stop_invalid_reason != null && (
                  <div style={{ marginTop: 4, fontSize: 10, color: T.red }}>
                    ⚠ {safeStr(cp.stop_invalid_reason)}
                  </div>
                )}
              </div>
            )}

            {/* Missing confirmations (POTENTIAL only) ─────────────────────── */}
            {isPotential && missing.length > 0 && (
              <div style={{ marginTop: 8, paddingTop: 6, borderTop: `1px solid ${T.border}` }}>
                <div style={{ fontSize: 9, color: T.amber, letterSpacing: '0.06em', marginBottom: 4, fontFamily: T.mono }}>
                  WAITING FOR
                </div>
                {missing.map((m, i) => (
                  <div key={i} style={{ fontSize: 10, color: T.txtSec, paddingLeft: 8 }}>• {m}</div>
                ))}
              </div>
            )}

            {/* Preview disclaimer ─────────────────────────────────────────── */}
            {isPotential && (
              <div style={{ marginTop: 8, fontSize: 9, color: T.txtMuted, fontStyle: 'italic' }}>
                Preview only — setup developing, no order will be sent
              </div>
            )}

            {/* ── SEND TO TRADERSPOST button (READY only) ──────────────────── */}
            {isReady && (
              <div style={{ marginTop: 12, paddingTop: 10,
                            borderTop: `1px solid ${T.border}` }}>
                {eligibility.eligible ? (
                  <button
                    onClick={openModal}
                    style={{
                      width: '100%', padding: '9px 0', borderRadius: 8,
                      cursor: 'pointer', fontSize: 11, fontWeight: 700,
                      background: 'rgba(56,189,248,0.10)',
                      border:     `1px solid ${T.cyan}`,
                      color:      T.cyan, letterSpacing: '0.06em',
                      transition: 'background 0.15s',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'rgba(56,189,248,0.18)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'rgba(56,189,248,0.10)')}
                  >
                    ↑ SEND TO TRADERSPOST
                  </button>
                ) : (
                  <div style={{ textAlign: 'center' }}>
                    <div style={{
                      width: '100%', padding: '9px 0', borderRadius: 8,
                      fontSize: 10.5, fontWeight: 700,
                      background: 'rgba(255,255,255,0.03)',
                      border:     `1px solid ${T.border}`,
                      color:      T.txtMuted, letterSpacing: '0.05em',
                      userSelect: 'none',
                    }}>
                      {eligibility.disabledLabel}
                    </div>
                    <div style={{ fontSize: 9, color: T.txtMuted, marginTop: 4,
                                  fontStyle: 'italic' }}>
                      Backend revalidates all conditions before any order is placed
                    </div>
                  </div>
                )}

                {/* Last send result (persists until next attempt or poll refresh) */}
                {sendResult && (
                  <div style={{
                    marginTop: 8, padding: '6px 10px', borderRadius: 6, fontSize: 10,
                    background: sendResult.type === 'success' ? 'rgba(34,197,94,0.08)'
                              : sendResult.type === 'unknown' ? 'rgba(245,158,11,0.08)'
                              :                                 'rgba(239,68,68,0.08)',
                    border: `1px solid ${
                      sendResult.type === 'success' ? 'rgba(34,197,94,0.20)'
                    : sendResult.type === 'unknown' ? 'rgba(245,158,11,0.20)'
                    :                                 'rgba(239,68,68,0.20)'}`,
                    color: sendResult.type === 'success' ? T.green
                         : sendResult.type === 'unknown' ? T.amber
                         :                                 T.red,
                    display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center',
                  }}>
                    <span style={{ fontWeight: 700 }}>
                      {sendResult.type === 'success'  ? '✓ ' +
                        (sendResult.status === 'sent'
                          ? 'SENT TO TRADERSPOST'
                          : sendResult.status === 'simulated'
                          ? 'PAPER SIMULATED'
                          : 'MANUAL PLAN RETURNED')
                       : sendResult.type === 'unknown' ? '⚠ STATUS UNKNOWN'
                       : '✗ NOT SENT'}
                    </span>
                    <span style={{ fontFamily: T.mono, fontSize: 9, color: T.txtMuted }}>
                      {sendResult.ts
                        ? new Date(sendResult.ts).toLocaleTimeString('en-US',
                            { hour:'2-digit', minute:'2-digit', second:'2-digit',
                              hour12:true, timeZone:'Etc/GMT+4' })
                        : ''}
                    </span>
                  </div>
                )}
              </div>
            )}

            {/* ── Session send log (Task #57) — hidden when empty, max 10 entries ── */}
            {sendLog.length > 0 && (
              <div style={{ marginTop: 12, paddingTop: 10, borderTop: `1px solid ${T.border}` }}>
                <div style={{ fontSize: 9, color: T.txtMuted, letterSpacing: '0.07em',
                              marginBottom: 6, fontWeight: 700 }}>
                  SESSION SEND LOG
                </div>
                {sendLog.map((e, i) => {
                  const isSuccess = e.outcome.type === 'success';
                  const isUnknown = e.outcome.type === 'unknown';
                  const col   = isSuccess ? T.green : isUnknown ? T.amber : T.red;
                  const icon  = isSuccess ? '✓' : isUnknown ? '⚠' : '✗';
                  const outS  = e.outcome as Extract<MbSendOutcome, { type: 'success' }>;
                  const outR  = e.outcome as Extract<MbSendOutcome, { type: 'rejected' }>;
                  const label = isSuccess
                    ? (outS.status === 'sent'       ? 'SENT'
                     : outS.status === 'simulated'  ? 'PAPER'
                     :                               'MANUAL')
                    : isUnknown ? 'UNKNOWN'
                    : `${outR.reason}`;
                  const timeStr = e.ts
                    ? new Date(e.ts).toLocaleTimeString('en-US',
                        { hour: '2-digit', minute: '2-digit', hour12: true,
                          timeZone: 'Etc/GMT+4' })
                    : '';
                  return (
                    <div key={i} style={{
                      display: 'flex', alignItems: 'center', gap: 5,
                      padding: '3px 0',
                      borderBottom: i < sendLog.length - 1
                        ? `1px solid ${T.border}` : 'none',
                      fontSize: 9.5,
                    }}>
                      <span style={{ color: col, fontFamily: T.mono, flexShrink: 0 }}>{icon}</span>
                      <span style={{ color: T.txtMuted, fontFamily: T.mono, flexShrink: 0 }}>{timeStr}</span>
                      <span style={{ color: T.txtSec, flex: 1, overflow: 'hidden',
                                     textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {e.instrument}{e.direction ? ` ${e.direction}` : ''}
                        {e.entryPrice ? ` @ ${e.entryPrice}` : ''}
                      </span>
                      <span style={{ color: col, fontWeight: 600, flexShrink: 0,
                                     maxWidth: 100, overflow: 'hidden',
                                     textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {label}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </Panel>

      {/* Confirmation + result modal — position:fixed, outside panel scroll */}
      {modalOpen && (
        <MbSendModal
          p={p}
          onClose={() => setModalOpen(false)}
          onConfirm={async () => {
            const outcome = await handleMbSend();
            setSendResult(outcome);
            setModalOpen(false);
            // Append to session send log (max 10, newest first)
            const entry: SendLogEntry = {
              ts:         outcome.ts,
              instrument: safeStr((p.market as Record<string, unknown>)?.instrument, ''),
              direction:  cp.direction as string | null | undefined,
              entryPrice: (cp.entry_zone as string | null | undefined) ?? null,
              outcome,
            };
            setSendLog(prev => [entry, ...prev].slice(0, 10));
            return outcome;
          }}
        />
      )}
    </>
  );
};

// ── Active Trades Panel ───────────────────────────────────────────────────────
const ActiveTradesPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const at    = (p.active_trades ?? {}) as Record<string, unknown>;
  const avail = at.available !== false;
  const trades = Array.isArray(at.trades) ? at.trades as Record<string, unknown>[] : [];

  // Per-instrument loading + toast state for close actions
  const [closing,   setClosing]   = useState<string | null>(null); // instrument being closed
  const [clearing,  setClearing]  = useState<string | null>(null); // instrument being stop-managed
  const [tradeMsg,  setTradeMsg]  = useState<{ inst: string; text: string; ok: boolean } | null>(null);
  const tradeMsgTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showTradeMsg = (inst: string, text: string, ok: boolean) => {
    if (tradeMsgTimer.current) clearTimeout(tradeMsgTimer.current);
    setTradeMsg({ inst, text, ok });
    tradeMsgTimer.current = setTimeout(() => setTradeMsg(null), 4000);
  };

  const handleClose = async (inst: string) => {
    if (closing || clearing) return;
    setClosing(inst);
    try {
      const r = await fetch('/api/quick-exit', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
        body: JSON.stringify({ ticker: inst }),
      });
      const j = await r.json() as Record<string, unknown>;
      if (r.ok && (j.status === 'sent' || j.status === 'simulated')) {
        const pnl = j.pnl_dollars != null
          ? ` · $${Number(j.pnl_dollars) >= 0 ? '+' : ''}${Number(j.pnl_dollars).toFixed(0)}`
          : '';
        showTradeMsg(inst, `Closed ${inst}${pnl}`, true);
      } else {
        showTradeMsg(inst, safeStr(j.reason ?? j.error ?? '', 'Close failed').slice(0, 70), false);
      }
    } catch {
      showTradeMsg(inst, 'Network error — verify at broker', false);
    } finally {
      setClosing(null);
    }
  };

  const handleStopManaging = async (inst: string) => {
    if (closing || clearing) return;
    if (!window.confirm(
      `Clear tracking for ${inst}?\n\nThis removes the bot's local position record only — it does NOT send any order. Use this after you've already closed the trade at your broker.`
    )) return;
    setClearing(inst);
    try {
      const r = await fetch('/api/stop-managing', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
        body: JSON.stringify({ ticker: inst }),
      });
      const j = await r.json() as Record<string, unknown>;
      if (r.ok) {
        showTradeMsg(inst, `Tracking cleared for ${inst}`, true);
      } else {
        showTradeMsg(inst, safeStr(j.reason ?? j.error ?? '', 'Failed').slice(0, 70), false);
      }
    } catch {
      showTradeMsg(inst, 'Network error', false);
    } finally {
      setClearing(null);
    }
  };

  return (
    <Panel title={`Active Trades (${trades.length})`}>
      {!avail ? <UnavailableNote /> : trades.length === 0 ? (
        <div style={{ textAlign:'center', padding:'20px 0', color:T.txtMuted, fontSize:11 }}>No active trades</div>
      ) : (
        trades.map((t, i) => {
          const dir  = safeStr(t.direction, '');
          const inst = safeStr(t.instrument, '');
          const dCol = dirColor(dir);
          const pnl  = safeNum(t.unrealized_pnl);
          const curR = safeNum(t.current_r);
          const pnlCol  = pnl == null ? T.txtSec : pnl >= 0 ? T.green : T.red;
          const isBusy  = closing === inst || clearing === inst;
          const msg     = tradeMsg?.inst === inst ? tradeMsg : null;
          return (
            <div key={i} style={{ background:T.panelAlt, borderRadius:8, border:`1px solid ${T.border}`, padding:'10px 12px', marginBottom:8 }}>
              {/* Header row */}
              <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:8 }}>
                <Pill text={dir || '—'} color={dCol} />
                <span style={{ fontSize:12, fontWeight:700, color:T.txtPri, fontFamily:T.mono }}>{inst}</span>
                <span style={{ marginLeft:'auto', fontSize:9, color:T.cyan }}>OPEN</span>
              </div>

              {/* Stats grid */}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'2px 12px', marginBottom:10 }}>
                <KV label="Strategy"    value={safeStr(t.strategy, '—')} />
                <KV label="Contracts"   value={safeStr(t.quantity, '—')} mono />
                <KV label="Entry"       value={fmtNum(t.entry)} mono />
                {t.stop  != null && <KV label="Stop"  value={fmtNum(t.stop)}  mono valueColor={T.red} />}
                {curR != null && <KV label="Current R" value={`${curR >= 0 ? '+' : ''}${fmtNum(curR, 2)}R`} mono valueColor={curR >= 0 ? T.green : T.red} />}
                {pnl  != null && <KV label="Unreal. P&L" value={`$${fmtNum(pnl, 0)}`} mono valueColor={pnlCol} />}
                <KV label="Opened"      value={fmtTs(t.opened_at)} />
              </div>

              {/* Toast */}
              {msg && (
                <div style={{
                  marginBottom: 8, padding: '5px 10px', borderRadius: 5, fontSize: 10.5,
                  background: msg.ok ? `${T.green}10` : `${T.red}10`,
                  border: `1px solid ${msg.ok ? T.green : T.red}44`,
                  color: msg.ok ? T.green : T.red,
                }}>
                  {msg.ok ? '✓' : '✗'} {msg.text}
                </div>
              )}

              {/* Action buttons */}
              <div style={{ display:'flex', gap:6, alignItems:'center' }}>
                {/* Primary: close position at broker */}
                <button
                  onClick={() => handleClose(inst)}
                  disabled={isBusy || !inst}
                  title={`Send market-flat order for ${inst} to broker`}
                  style={{
                    flex: 1, padding: '7px 0', borderRadius: 6,
                    fontSize: 11, fontWeight: 700, letterSpacing: '0.06em',
                    cursor: (isBusy || !inst) ? 'not-allowed' : 'pointer',
                    opacity: (isBusy || !inst) ? 0.45 : 1,
                    background: `${T.amber}12`, border: `1px solid ${T.amber}55`, color: T.amber,
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={e => { if (!isBusy) (e.currentTarget as HTMLButtonElement).style.background = `${T.amber}22`; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = `${T.amber}12`; }}
                >
                  {closing === inst ? '…' : '■ CLOSE POSITION'}
                </button>

                {/* Secondary: clear tracking only (no order sent) */}
                <button
                  onClick={() => handleStopManaging(inst)}
                  disabled={isBusy || !inst}
                  title="Clear bot tracking only — does NOT send a close order"
                  style={{
                    padding: '7px 10px', borderRadius: 6,
                    fontSize: 9.5, fontWeight: 600, letterSpacing: '0.04em',
                    cursor: (isBusy || !inst) ? 'not-allowed' : 'pointer',
                    opacity: (isBusy || !inst) ? 0.35 : 0.65,
                    background: 'rgba(255,255,255,0.03)', border: `1px solid ${T.border}`,
                    color: T.txtMuted,
                    transition: 'opacity 0.15s',
                  }}
                  onMouseEnter={e => { if (!isBusy) (e.currentTarget as HTMLButtonElement).style.opacity = '1'; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.opacity = '0.65'; }}
                >
                  {clearing === inst ? '…' : 'Clear tracking'}
                </button>
              </div>
            </div>
          );
        })
      )}
    </Panel>
  );
};

// ── Recommendation Card ───────────────────────────────────────────────────────
const RecommendationCard: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const v   = (p.verdict    ?? {}) as Record<string, unknown>;
  const vol = (p.volatility ?? {}) as Record<string, unknown>;
  const dirs = (p.directions ?? {}) as Record<string, unknown>;

  const direction  = safeStr(v.direction, '');
  const score      = safeNum(v.edge_score) ?? 0;
  const isLong     = direction.toLowerCase() === 'long';
  const isShort    = direction.toLowerCase() === 'short';
  const dirCol     = isLong ? T.green : isShort ? T.red : T.txtMuted;

  // Positive factors — edge_components where present === true
  const edgeComps      = Array.isArray(v.edge_components)
    ? v.edge_components as Record<string, unknown>[] : [];
  const positiveFactors = edgeComps.filter(c => c.present === true);

  // Negative factors — aggregated from multiple sources
  const negativeFactors: Array<{ label: string; points: number }> = [];

  // 1. SCALP modifiers (already negative-signed)
  const modifiers = Array.isArray(v.modifiers) ? v.modifiers as Record<string, unknown>[] : [];
  for (const m of modifiers) {
    const pts = safeNum(m.points) ?? 0;
    if (pts < 0) negativeFactors.push({ label: safeStr(m.label, 'Penalty'), points: pts });
  }

  // 2. Opposing structure alert
  const os = (v.opposing_structure ?? null) as Record<string, unknown> | null;
  if (os && os.detected === true) {
    const ageS   = safeNum(os.age_seconds);
    const ageStr = ageS != null ? ` ${Math.round(ageS / 60)} min ago` : '';
    const evType = safeStr(os.event_type, 'structure event');
    const oppDir = safeStr(os.direction, '');
    negativeFactors.push({ label: `${oppDir} ${evType}${ageStr}`, points: -8 });
  }

  // 3. ATR / volatility elevated
  const volLabel = safeStr(vol.volatility_label, '').toLowerCase();
  if (volLabel === 'elevated' || volLabel === 'extreme') {
    negativeFactors.push({ label: 'ATR elevated', points: volLabel === 'extreme' ? -6 : -3 });
  }

  // Long vs Short scores from directions.bull / directions.bear
  const bull       = (dirs.bull ?? {}) as Record<string, unknown>;
  const bear       = (dirs.bear ?? {}) as Record<string, unknown>;
  const longScore  = safeNum(bull.edge_score) ?? (isLong  ? score : 0);
  const shortScore = safeNum(bear.edge_score) ?? (isShort ? score : 0);
  const winner     = longScore >= shortScore ? 'LONG' : 'SHORT';
  const winnerCol  = winner === 'LONG' ? T.green : T.red;

  const avail = v.available !== false && edgeComps.length > 0;

  return (
    <Panel title="Recommendation">
      {!avail ? <UnavailableNote msg="No active setup data" /> : (
        <>
          {/* ── Direction + confidence header ──────────────────────────── */}
          <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:14,
            padding:'10px 12px', borderRadius:10,
            background: isLong ? 'rgba(34,197,94,0.07)' : isShort ? 'rgba(239,68,68,0.07)' : 'rgba(100,116,139,0.07)',
            border:`1px solid ${isLong ? 'rgba(34,197,94,0.2)' : isShort ? 'rgba(239,68,68,0.2)' : 'rgba(100,116,139,0.2)'}` }}>
            <div>
              <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', marginBottom:4 }}>DIRECTION</div>
              <div style={{ fontSize:16, fontWeight:800, color:dirCol, letterSpacing:'0.06em' }}>
                {direction.toUpperCase() || 'NEUTRAL'}
              </div>
            </div>
            <div style={{ textAlign:'right' }}>
              <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', marginBottom:4 }}>CONFIDENCE</div>
              <div style={{ fontSize:26, fontWeight:800, fontFamily:T.mono, lineHeight:1,
                color: score >= 70 ? T.green : score >= 50 ? T.amber : T.red }}>
                {score}<span style={{ fontSize:13, fontWeight:500, color:T.txtMuted }}>%</span>
              </div>
            </div>
          </div>

          {/* ── Positive factors ──────────────────────────────────────── */}
          {positiveFactors.length > 0 && (
            <div style={{ marginBottom:10 }}>
              <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', fontWeight:700, marginBottom:6 }}>REASONING</div>
              {positiveFactors.map((c, i) => {
                const lbl = safeStr(c.label, safeStr(c.key, '').replace(/_/g, ' '));
                const pts = safeNum(c.points) ?? 0;
                return (
                  <div key={i} style={{ display:'flex', alignItems:'center', gap:8, marginBottom:5,
                    padding:'5px 8px', borderRadius:6,
                    background:'rgba(34,197,94,0.04)', border:'1px solid rgba(34,197,94,0.12)' }}>
                    <span style={{ color:T.green, fontSize:11, flexShrink:0, lineHeight:1 }}>✓</span>
                    <span style={{ fontSize:10, color:T.txtSec, flex:1 }}>{lbl}</span>
                    <div style={{ display:'flex', alignItems:'center', gap:6, flexShrink:0 }}>
                      <div style={{ width:36, height:3, background:'rgba(255,255,255,0.07)', borderRadius:2 }}>
                        <div style={{ height:'100%', width:`${Math.round(pts/20*100)}%`, background:T.green, borderRadius:2 }} />
                      </div>
                      <span style={{ fontSize:9.5, fontFamily:T.mono, color:T.green, fontWeight:700, minWidth:22, textAlign:'right' }}>+{pts}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* ── Negative factors ──────────────────────────────────────── */}
          {negativeFactors.length > 0 && (
            <div style={{ marginBottom:10 }}>
              <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', fontWeight:700, marginBottom:6 }}>NEGATIVE FACTORS</div>
              {negativeFactors.map((f, i) => (
                <div key={i} style={{ display:'flex', alignItems:'center', gap:8, marginBottom:5,
                  padding:'5px 8px', borderRadius:6,
                  background:'rgba(239,68,68,0.04)', border:'1px solid rgba(239,68,68,0.12)' }}>
                  <span style={{ color:T.red, fontSize:11, flexShrink:0, lineHeight:1 }}>✕</span>
                  <span style={{ fontSize:10, color:T.txtSec, flex:1 }}>{f.label}</span>
                  <span style={{ fontSize:9.5, fontFamily:T.mono, color:T.red, fontWeight:700, flexShrink:0 }}>{f.points}</span>
                </div>
              ))}
            </div>
          )}

          {/* ── Divider ───────────────────────────────────────────────── */}
          <div style={{ borderTop:`1px solid ${T.border}`, margin:'10px 0' }} />

          {/* ── Long vs Short score bars ───────────────────────────────── */}
          <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', fontWeight:700, marginBottom:8 }}>FINAL SCORE</div>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, marginBottom:10 }}>
            {(['long', 'short'] as const).map(dir => {
              const sc        = dir === 'long' ? longScore : shortScore;
              const isWinner  = direction.toLowerCase() === dir;
              const col       = dir === 'long' ? T.green : T.red;
              const barPct    = Math.min(100, Math.round(sc / 110 * 100));
              return (
                <div key={dir} style={{ padding:'8px 10px', borderRadius:8,
                  background: isWinner ? `${col}10` : 'rgba(255,255,255,0.025)',
                  border:`1px solid ${isWinner ? `${col}35` : T.border}`,
                  borderTop:`2px solid ${isWinner ? col : T.border}` }}>
                  <div style={{ fontSize:9, color:col, letterSpacing:'0.08em', fontWeight:700, marginBottom:4 }}>
                    {dir.toUpperCase()}
                  </div>
                  <div style={{ fontSize:20, fontFamily:T.mono, fontWeight:800, color: isWinner ? col : T.txtSec, marginBottom:6, lineHeight:1 }}>
                    {sc}
                  </div>
                  <div style={{ height:3, background:'rgba(255,255,255,0.07)', borderRadius:2 }}>
                    <div style={{ height:'100%', width:`${barPct}%`, background:col, borderRadius:2,
                      opacity: isWinner ? 1 : 0.35, transition:'width 0.4s ease' }} />
                  </div>
                </div>
              );
            })}
          </div>

          {/* ── Winner ────────────────────────────────────────────────── */}
          <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
            padding:'8px 12px', borderRadius:8,
            background:`${winnerCol}09`, border:`1px solid ${winnerCol}25` }}>
            <span style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em' }}>WINNER</span>
            <span style={{ fontSize:12, fontWeight:800, color:winnerCol, letterSpacing:'0.08em' }}>
              {winner}
            </span>
          </div>
        </>
      )}
    </Panel>
  );
};

// ── Execution Status Panel ────────────────────────────────────────────────────
const ExecutionPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const gw  = (p.execution_gateway ?? {}) as Record<string, unknown>;
  const sys = (p.system_status ?? {}) as Record<string, unknown>;
  const avail = gw.available !== false;

  return (
    <Panel title="Execution Status">
      {!avail ? <UnavailableNote /> : (
        <>
          <div style={{ display:'flex', gap:6, marginBottom:10, flexWrap:'wrap' }}>
            {gw.mode != null && <Pill text={String(gw.mode)} color={gw.mode === 'manual_only' ? T.amber : T.green} />}
            {gw.gateway_status != null && <Badge label={String(gw.gateway_status)} color={gw.gateway_status === 'SENT' ? T.green : T.txtSec} />}
          </div>
          <KV label="Last Signal"   value={fmtTs(gw.last_sent_at)} />
          <KV label="Broker Ready"  value={
            <span style={{ display:'flex', alignItems:'center', gap:5 }}>
              {statusDot(sys.broker_ready ? true : false)}
              <span>{sys.broker_ready ? 'YES' : 'NO'}</span>
            </span>
          } />
          <KV label="DB Ready"      value={
            <span style={{ display:'flex', alignItems:'center', gap:5 }}>
              {statusDot(sys.db_ready ? true : false)}
              <span>{sys.db_ready ? 'YES' : 'NO'}</span>
            </span>
          } />
          <div style={{ marginTop:8, padding:'6px 10px', background:'rgba(255,255,255,0.025)', borderRadius:6, fontSize:10, color:T.txtMuted }}>
            <span style={{ color:T.amber }}>Note:</span> Last outcome not available — deferred to Phase 7D.
          </div>
        </>
      )}
    </Panel>
  );
};

// ── Coach Panel ───────────────────────────────────────────────────────────────
const CoachPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const coach = (p.coach ?? {}) as Record<string, unknown>;
  const perf  = (p.performance ?? {}) as Record<string, unknown>;
  const avail = coach.available !== false;

  const formatBestSetup = (value: unknown): string => {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return '—';
    const setup = value as Record<string, unknown>;
    const name = setup.setup_type ?? setup.strategy ?? setup.name ?? 'Unknown setup';
    const winRate = setup.win_rate ?? setup.winRate;
    const avgR = setup.avg_r ?? setup.avgR;
    const count = setup.n ?? setup.count ?? setup.sample;
    const details = [
      winRate != null ? `${fmtNum(Number(winRate), 0)}% WR` : null,
      avgR != null ? `${fmtNum(Number(avgR))}R` : null,
      count != null ? `n=${String(count)}` : null,
    ].filter(Boolean);
    return `${String(name)}${details.length ? ` (${details.join(' · ')})` : ''}`;
  };

  // ── learning_diagnostics from Phase 7I audit ───────────────────────────────
  const ld = (coach.learning_diagnostics ?? {}) as Record<string, unknown>;
  const weightStatus   = safeStr(ld.weight_status, '');
  const wsLabel        = WEIGHT_STATUS_LABEL[weightStatus] ?? weightStatus;
  const wsColor        = WEIGHT_STATUS_COLOR[weightStatus] ?? T.txtMuted;
  const sampleCount    = safeNum(ld.sample_count) ?? 0;
  const minSamples     = safeNum(ld.minimum_samples) ?? 20;
  const currentWeight  = safeNum(ld.current_weight) ?? 1.0;
  const weightDelta    = safeNum(ld.weight_delta) ?? 0.0;
  const influencePts   = safeNum(ld.influence_points) ?? 0;
  const blockedReason  = safeStr(ld.blocked_reason, '');
  const stratKey       = safeStr(ld.strategy_key, '');
  const lastUpdate     = safeStr(ld.last_weight_update_at, '');
  const scoreEnabled   = ld.influence_enabled === true;
  const isApplied      = ld.applied_to_live_score === true;
  const samplePct      = Math.min(100, Math.round(sampleCount / minSamples * 100));
  const isCollecting         = weightStatus === 'INSUFFICIENT_SAMPLES' || weightStatus === 'NOT_ELIGIBLE';
  const isActive             = weightStatus === 'UPDATED' || weightStatus === 'NO_CHANGE';
  // Task #41 — session lesson counters
  const recomputesThisSession = safeNum(ld.recomputes_this_session) ?? 0;
  const weightsChangedLast    = safeNum(ld.weights_changed_last_run) ?? 0;

  return (
    <Panel title="Coach" badge={<Badge label="LEARNING" color={T.purple} />}>
      {!avail ? <UnavailableNote /> : (
        <>
          {/* ── Status header ────────────────────────────────────────────── */}
          <div style={{ padding:'8px 10px', borderRadius:7, marginBottom:10,
            background: isActive ? 'rgba(34,197,94,0.06)' : isCollecting ? 'rgba(245,158,11,0.06)' : 'rgba(100,116,139,0.06)',
            border: `1px solid ${isActive ? 'rgba(34,197,94,0.18)' : isCollecting ? 'rgba(245,158,11,0.18)' : 'rgba(100,116,139,0.18)'}`,
            borderLeft: `3px solid ${wsColor}` }}>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom: isCollecting || isActive ? 8 : 0 }}>
              <div style={{ fontSize:9, letterSpacing:'0.1em', fontWeight:700, color:wsColor }}>
                LEARNING {wsLabel}
              </div>
              {scoreEnabled
                ? <span style={{ fontSize:8.5, color:T.green }}>SCORE GATE ON</span>
                : <span style={{ fontSize:8.5, color:T.txtMuted }}>SCORE GATE OFF</span>}
            </div>

            {/* Sample progress bar */}
            {(isCollecting || isActive) && (
              <div style={{ marginBottom:8 }}>
                <div style={{ display:'flex', justifyContent:'space-between', fontSize:9, color:T.txtMuted, marginBottom:3 }}>
                  <span>{sampleCount} / {minSamples} required samples</span>
                  <span style={{ color: sampleCount >= minSamples ? T.green : T.amber }}>{samplePct}%</span>
                </div>
                <div style={{ height:4, background:'rgba(255,255,255,0.07)', borderRadius:2 }}>
                  <div style={{ height:'100%', width:`${samplePct}%`, background: sampleCount >= minSamples ? T.green : T.amber, borderRadius:2, transition:'width 0.4s ease' }} />
                </div>
              </div>
            )}

            {/* Weight and delta */}
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'3px 10px', fontSize:9.5 }}>
              <><span style={{ color:T.txtMuted }}>Current weight</span>
                <span style={{ fontFamily:T.mono, color: Math.abs(weightDelta) > 0.01 ? T.cyan : T.txtSec }}>
                  {currentWeight.toFixed(3)}{Math.abs(weightDelta) > 0.001 ? ` (${weightDelta >= 0 ? '+' : ''}${weightDelta.toFixed(3)})` : ''}
                </span></>
              <><span style={{ color:T.txtMuted }}>Live influence</span>
                <span style={{ fontFamily:T.mono, color: influencePts !== 0 ? T.cyan : T.txtMuted }}>
                  {influencePts !== 0 ? `${influencePts >= 0 ? '+' : ''}${influencePts} pts` : '0 pts'}
                </span></>
            </div>
          </div>

          {/* ── Detail rows ──────────────────────────────────────────────── */}
          {blockedReason && !isApplied && (
            <div style={{ marginBottom:8, padding:'6px 10px', background:'rgba(245,158,11,0.05)', borderRadius:6, border:`1px solid rgba(245,158,11,0.15)` }}>
              <div style={{ fontSize:9, color:T.amber, letterSpacing:'0.07em', fontWeight:700 }}>BLOCKED REASON</div>
              <div style={{ fontSize:9.5, color:T.txtSec, marginTop:3 }}>
                {blockedReason === 'INSUFFICIENT_SAMPLES'
                  ? `Minimum sample threshold not reached (${sampleCount}/${minSamples})`
                  : blockedReason === 'DISABLED'
                  ? 'Learning score influence is disabled'
                  : blockedReason === 'KEY_NOT_FOUND'
                  ? 'Strategy key not found in weight store (key mismatch)'
                  : blockedReason === 'NOT_ELIGIBLE'
                  ? 'Weight recompute has not run yet this session'
                  : blockedReason}
              </div>
            </div>
          )}

          {isApplied && stratKey && (
            <div style={{ marginBottom:8, padding:'5px 10px', background:'rgba(34,197,94,0.05)', borderRadius:6, border:`1px solid rgba(34,197,94,0.15)` }}>
              <div style={{ fontSize:9, color:T.green, letterSpacing:'0.07em', fontWeight:700 }}>APPLIED TO</div>
              <div style={{ fontSize:9, color:T.txtSec, marginTop:2, fontFamily:T.mono, wordBreak:'break-all' }}>{stratKey}</div>
            </div>
          )}

          {/* ── Session lesson counters (Task #41) ──────────────────────── */}
          {recomputesThisSession > 0 && (
            <div style={{ marginBottom:8, padding:'5px 10px', borderRadius:6,
              background: weightsChangedLast > 0 ? 'rgba(99,102,241,0.06)' : 'rgba(255,255,255,0.03)',
              border:`1px solid ${weightsChangedLast > 0 ? 'rgba(99,102,241,0.2)' : T.border}` }}>
              <div style={{ fontSize:9, color: weightsChangedLast > 0 ? T.purple : T.txtMuted,
                letterSpacing:'0.07em', fontWeight:700, marginBottom:3 }}>
                SESSION LEARNING
              </div>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'2px 10px', fontSize:9.5 }}>
                <span style={{ color:T.txtMuted }}>Recomputes</span>
                <span style={{ fontFamily:T.mono, color:T.txtSec }}>{recomputesThisSession}</span>
                <span style={{ color:T.txtMuted }}>Weights updated</span>
                <span style={{ fontFamily:T.mono, color: weightsChangedLast > 0 ? T.purple : T.txtSec }}>
                  {weightsChangedLast}
                </span>
              </div>
            </div>
          )}

          <KV label="Thesis Resolved" value={
            <span title="Whether a thesis was resolved in this session — not a measure of learning readiness">
              {coach.thesis_resolved ? 'YES' : 'NO'}
            </span>
          } />
          <KV label="Last Resolved" value={fmtTs(safeStr(coach.thesis_last_resolved_at, ''))} />
          {lastUpdate && <KV label="Last Weight Update" value={fmtTs(lastUpdate)} />}
          <KV label="LRE Status" value={
            <span style={{ color: coach.rule_engine_eligibility === 'LIVE_ELIGIBLE' ? T.green : T.amber }}>
              {safeStr(coach.rule_engine_eligibility, '—')}
            </span>
          } />

          {/* ── Performance summary ───────────────────────────────────────── */}
          {Object.keys(perf).length > 0 && perf.available === true && (
            <div style={{ marginTop:10, borderTop:`1px solid ${T.border}`, paddingTop:10 }}>
              <div style={{ fontSize:9, color:T.purple, letterSpacing:'0.1em', marginBottom:6 }}>PERFORMANCE REVIEW</div>
              {perf.win_rate  != null && <KV label="Win Rate"   value={`${fmtNum(perf.win_rate, 0)}%`} mono />}
              {perf.avg_r     != null && <KV label="Avg R"      value={fmtNum(perf.avg_r)}              mono />}
              {perf.trade_count != null && <KV label="Review Sample" value={String(perf.trade_count)} mono />}
              {perf.best_setup != null && <KV label="Best Setup" value={formatBestSetup(perf.best_setup)} />}
            </div>
          )}

          <div style={{ marginTop:8, fontSize:8.5, color:T.txtMuted, lineHeight:1.5 }}>
            <span style={{ color:T.amber }}>ℹ</span>{' '}
            Influence = 0 until {minSamples} samples. "Weights updated" shows actual weight changes, not just that recompute ran.
          </div>
        </>
      )}
    </Panel>
  );
};

// ── Journal Full Page (Phase 7K) ──────────────────────────────────────────────
// Self-contained tabbed journal — makes its own API calls to /journal/* endpoints.
// Used ONLY for the /main-brain/journal route. The overview-grid card below is the
// compact version that still reads from the main-brain payload.

type JTab = 'trades' | 'import' | 'analytics' | 'playbook' | 'learning' | 'directional' | 'queue' | 'coaching';

interface JTrade {
  id: number; source: string; date: string; instrument: string; direction: string;
  strategy_name: string; entry: number | null; exit: number | null; result: string;
  r_multiple: number | null; pnl: number | null; review_status: string;
  edge_score: number | null; duration_min: number | null; trading_mode: string;
  session?: string;
  // Task 83 — snapshot attribution columns (TZ trades only)
  strategy_source?: string | null;
  match_confidence?: string | null;
  learning_status?: string | null;
}

interface JTradeDetail extends JTrade {
  symbol?: string;
  stop?: number | null; target?: number | null;
  hold_minutes?: number | null; confidence?: number | null; grade?: string;
  session?: string; market_regime?: string;
  mfe_r?: number | null; mae_r?: number | null;
  entry_reason?: string; outcome_reason?: string; outcome_tag?: string;
  trade_label?: string; opened_at?: string; closed_at?: string;
  strategy?: string; strategy_key?: string;
  entry_time?: string; exit_time?: string; fees?: number | null;
  mistakes?: string; notes?: string; screenshots?: string;
  // Task 83 — snapshot-derived planned fields (TZ trades only)
  system_strategy?: string | null;
  system_strategy_key?: string | null;
  system_thesis_direction?: string | null;
  system_thesis_strength?: string | null;
  system_edge_score?: number | null;
  system_grade?: string | null;
  system_planned_entry?: number | null;
  system_planned_stop?: number | null;
  system_planned_risk?: number | null;
  system_planned_targets?: Record<string, unknown> | null;
  snap_thesis_alignment?: string | null;
  is_external_manual?: boolean | null;
}

/** Phase 7O.1 — Canonical drill-down filter contract.
 *  Built by coaching cards; consumed by JTradesTab + /api/journal/trades.
 *  label/count are display-only and never sent to the server.
 *  All filter values that are sent server-side use AND semantics. */
interface JDrillFilter {
  label:         string;          // human-readable source description e.g. "Mistake: CHASED ENTRY"
  count?:        number;          // n from the coaching metric (shown in the banner)
  // ── server-side filter params (all optional, AND semantics) ──────────────
  review_status?: string;         // 'REVIEWED' | 'UNREVIEWED' | 'EXCLUDED' | 'INCOMPLETE'
  mistake_tag?:   string;         // exact match in jr.mistake_tags (JSONB string array)
  positive_tag?:  string;         // exact match in jr.positive_tags
  emotion_tag?:   string;         // exact match in jr.emotion_tags[*].tag
  followed_plan?: string;         // 'YES' | 'PARTIALLY' | 'NO' | 'NOT_APPLICABLE'
  strategy?:      string;         // ILIKE match on strategy_name
  session?:       string;         // exact match on session column
  instrument?:    string;         // exact match after UPPER()
  mode?:          string;         // exact match on trading_mode (SCALP/SWING/MICRO_SCALP)
  source?:        string;         // 'system' | 'tradzella'
  date_from?:     string;         // ISO date (inclusive lower bound)
  date_to?:       string;         // ISO date (inclusive upper bound)
  rating_field?:  string;         // setup_quality|execution_quality|discipline_quality|overall_quality
  rating_value?:  number;         // integer 1-5 (requires rating_field)
  // ── Phase 7O.3: rating range (band drill-down, requires rating_field) ────────
  rating_min?:    number;         // integer 1-5 inclusive lower bound (e.g. 4 for HIGH band)
  rating_max?:    number;         // integer 1-5 inclusive upper bound (e.g. 5 for HIGH band)
  // ── Phase 7O.3: realized R range ─────────────────────────────────────────────
  realized_r_min?: number;        // r_multiple >= this value (e.g. 0 for wins)
  realized_r_max?: number;        // r_multiple <= this value (e.g. -0.01 for losses)
  // ── Phase 7O.3: quality classification shortcut ───────────────────────────────
  quality_classification?: string; // 'high_quality_loss' | 'low_quality_win'
  result?:        string;         // 'win' | 'loss' | 'scratch'
  // ── Phase 7O.2: intraday 30-min block filter ─────────────────────────────
  entry_block_start?: string;     // "HH:MM" inclusive block start (e.g. "09:30")
  entry_block_end?:   string;     // "HH:MM" exclusive block end   (e.g. "10:00"); "00:00" for 23:30 block
  display_timezone?:  string;     // IANA timezone (default "America/New_York")
}

// ── Phase 7K-A.2 — Native Journal types ──────────────────────────────────────
interface NJTrade {
  id: string;
  internal_trade_id: string | null;
  created_at: string;
  updated_at: string;
  instrument: string;
  contract: string | null;
  direction: string | null;
  mode: string | null;
  session: string | null;
  canonical_strategy_key: string | null;
  strategy_display_name: string | null;
  lifecycle_status: string;
  source_label: string;
  edge_score: number | null;
  grade: string | null;
  readiness: string | null;
  planned_entry: number | null;
  planned_stop: number | null;
  planned_targets: unknown;
  planned_risk: number | null;
  planned_contracts: number | null;
  broker_order_id: string | null;
  traderspost_id: string | null;
  review_status: string;
  learning_eligible: boolean;
  learning_blocked_reason: string | null;
}

interface NJReviewData {
  followed_plan?: string | null;
  setup_quality?: number | null;
  execution_quality?: number | null;
  management_quality?: number | null;
  emotional_control?: number | null;
  mistake_tags?: string[];
  emotion_tags?: string[];
  positive_tags?: string[];
  lesson?: string | null;
  what_went_well?: string | null;
  what_to_improve?: string | null;
  override_assessment?: string | null;
  status_history?: Array<{ from_status: string; to_status: string; changed_at: string }>;
  [key: string]: unknown;
}
interface NJScreenshotMeta {
  attachment_id: string;
  category: string;
  caption: string | null;
  storage_key: string;
  mime_type: string;
  file_size: number;
  uploaded_at: string;
}
interface NJReviewCompleteness {
  completed: number;
  required: number;
  optional: number;
  completed_required: number;
  completed_optional: number;
  total: number;
  missing_required: string[];
}

interface NJTradeDetail extends NJTrade {
  setup_name: string | null;
  playbook: string | null;
  thesis_direction: string | null;
  thesis_strength: string | null;
  thesis_alignment: string | null;
  confirmations: unknown;
  blockers: unknown;
  opposing_structure: string | null;
  risk_state: string | null;
  planned_rr: number | null;
  planned_dollar_risk: number | null;
  market_data_timestamp: string | null;
  decision_timestamp: string | null;
  signal_id: string | null;
  execution: Record<string, unknown> | null;
  management_events: Record<string, unknown>[] | null;
  outcome: Record<string, unknown> | null;
  review_notes: string | null;
  tradezella_trade_id: number | null;
  legacy_journal_key: string | null;
  // Phase 7K-C additions
  review_data?: NJReviewData;
  screenshots?: NJScreenshotMeta[];
  review_completeness?: NJReviewCompleteness;
}

interface JBatch {
  batch_id: string; filename: string | null; source: string;
  row_count: number; imported_count: number; skipped_count: number;
  created_at: string;
}

interface JPreviewTrade {
  symbol: string | null; side: string | null; entry_time: string | null;
  exit_time: string | null; entry_price: number | null; exit_price: number | null;
  pnl: number | null; r_multiple: number | null;
  outcome: string; dedupe_key: string; duplicate?: boolean;
  setup?: string | null;
}

function jFmtDate(v: unknown): string {
  if (!v) return '—';
  try {
    const d = new Date(String(v));
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit',
      timeZone: 'Etc/GMT+4' }) + ' ' +
      d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true,
        timeZone: 'Etc/GMT+4' });
  } catch { return '—'; }
}

function jFmtShortDate(v: unknown): string {
  if (!v) return '—';
  try {
    const d = new Date(String(v));
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric',
      timeZone: 'Etc/GMT+4' });
  } catch { return '—'; }
}

function jRCol(r: number | null | undefined): string {
  if (r == null) return T.txtSec;
  return r > 0 ? T.green : r < 0 ? T.red : T.txtMuted;
}

function jResultBadge(result: string): React.ReactNode {
  const r = (result || '').toLowerCase();
  const col = r === 'win' ? T.green : r === 'loss' ? T.red : T.amber;
  return (
    <span style={{ background: col + '22', color: col, borderRadius: 3,
      padding: '1px 5px', fontSize: 9, fontWeight: 700, letterSpacing: '0.05em' }}>
      {result.toUpperCase() || '—'}
    </span>
  );
}

function jReviewBadge(status: string): React.ReactNode {
  const s = (status || 'UNREVIEWED').toUpperCase();
  const cfg: Record<string, { col: string; label: string }> = {
    REVIEWED:    { col: T.green,    label: '✓ Reviewed' },
    IN_PROGRESS: { col: T.amber,    label: '… Draft' },
    NEEDS_DATA:  { col: T.red,      label: '⚠ Needs Data' },
    EXCLUDED:    { col: T.txtMuted, label: '— Excluded' },
    UNREVIEWED:  { col: T.txtMuted, label: '○ Unreviewed' },
  };
  const { col, label } = cfg[s] ?? { col: T.txtMuted, label: s };
  return (
    <span style={{ background: col + '22', color: col, borderRadius: 3,
      padding: '1px 5px', fontSize: 8, fontWeight: 700, letterSpacing: '0.04em',
      whiteSpace: 'nowrap' }}>
      {label}
    </span>
  );
}

// Phase 7N Batch C: per-trade learning eligibility badge (display-only)
function jEligibilityBadge(status: string, reason: string): React.ReactNode {
  const s = (status || 'UNKNOWN').toUpperCase();
  const cfg: Record<string, { col: string; label: string }> = {
    ELIGIBLE:             { col: T.green,    label: '✓ Eligible' },
    REVIEW_REQUIRED:      { col: T.amber,    label: '○ Review' },
    MISSING_RISK:         { col: T.red,      label: '⚠ Risk' },
    MISSING_STRATEGY:     { col: T.red,      label: '⚠ Strategy' },
    INVALID_OUTCOME:      { col: T.txtMuted, label: '⊘ Outcome' },
    EXCLUDED_BY_OPERATOR: { col: T.txtMuted, label: '— Excluded' },
    DUPLICATE:            { col: T.txtMuted, label: '⊘ Duplicate' },
  };
  const { col, label } = cfg[s] ?? { col: T.txtMuted, label: s };
  return (
    <span
      title={reason || undefined}
      style={{ background: col + '22', color: col, borderRadius: 3,
        padding: '1px 5px', fontSize: 8, fontWeight: 700, letterSpacing: '0.04em',
        whiteSpace: 'nowrap', cursor: reason ? 'help' : 'default' }}>
      {label}
    </span>
  );
}

function jLifecycleBadge(status: string): React.ReactNode {
  const s = (status || 'STATUS_UNKNOWN').toUpperCase();
  const cfg: Record<string, { col: string; label: string }> = {
    PLANNED:          { col: '#60a5fa', label: 'PLANNED' },
    SUBMITTED:        { col: '#60a5fa', label: 'SUBMITTED' },
    ACKNOWLEDGED:     { col: '#60a5fa', label: "ACK'D" },
    ACTIVE:           { col: T.cyan,    label: '● ACTIVE' },
    PARTIALLY_CLOSED: { col: T.cyan,    label: 'PARTIAL' },
    CLOSED:           { col: T.txtMuted,label: 'CLOSED' },
    REJECTED:         { col: T.red,     label: 'REJECTED' },
    CANCELED:         { col: T.red,     label: 'CANCELED' },
    STATUS_UNKNOWN:   { col: T.amber,   label: '? UNKNOWN' },
    NEEDS_REVIEW:     { col: T.amber,   label: '⚠ REVIEW' },
  };
  const { col, label } = cfg[s] ?? { col: T.txtMuted, label: s };
  return (
    <span style={{ background: col + '22', color: col, borderRadius: 3,
      padding: '1px 5px', fontSize: 8, fontWeight: 700, letterSpacing: '0.04em',
      whiteSpace: 'nowrap' }}>
      {label}
    </span>
  );
}

// ── Phase 7N Batch B: Attachment types ────────────────────────────────────────
interface JAttachment {
  id: number;
  source: string;
  trade_id: number;
  stage: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  created_at: string;
  serve_url: string;
}

const ATTACH_STAGES = [
  { value: 'before_entry',  label: 'Before Entry' },
  { value: 'at_entry',      label: 'At Entry' },
  { value: 'during',        label: 'During Trade' },
  { value: 'after_exit',    label: 'After Exit' },
  { value: 'review_markup', label: 'Review Markup' },
];

// Attachment section embedded inside the review modal
const JAttachmentSection: React.FC<{
  source: string; tradeId: number;
}> = ({ source, tradeId }) => {
  const [attachments, setAttachments]       = useState<JAttachment[]>([]);
  const [loading,     setLoading]           = useState(true);
  const [uploading,   setUploading]         = useState(false);
  const [uploadStage, setUploadStage]       = useState('review_markup');
  const [uploadError, setUploadError]       = useState<string | null>(null);
  const [confirmDel,  setConfirmDel]        = useState<number | null>(null);
  const [fullImg,     setFullImg]           = useState<string | null>(null);
  const fileRef = React.useRef<HTMLInputElement>(null);

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/journal/trade/${source}/${tradeId}/attachments`,
        { headers: getAuthHeader() });
      const d = await r.json();
      if (d.ok) setAttachments(d.attachments || []);
    } catch { /* fail silently */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [source, tradeId]);

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const file = files[0];
    if (!file.type.startsWith('image/')) {
      setUploadError('Only image files are accepted (jpeg, png, gif, webp)'); return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setUploadError(`File too large (${(file.size/1048576).toFixed(1)} MB) — max 5 MB`); return;
    }
    setUploading(true); setUploadError(null);
    try {
      const buf = await file.arrayBuffer();
      const r = await fetch(
        `/api/journal/trade/${source}/${tradeId}/attachment?stage=${encodeURIComponent(uploadStage)}&filename=${encodeURIComponent(file.name)}`,
        { method: 'POST', headers: { ...getAuthHeader(), 'Content-Type': file.type },
          body: buf }
      );
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'upload failed');
      setAttachments(prev => [...prev, d.attachment]);
      if (fileRef.current) fileRef.current.value = '';
    } catch (e) { setUploadError(String(e)); }
    setUploading(false);
  };

  const handleDelete = async (id: number) => {
    try {
      const r = await fetch(`/api/journal/attachment/${id}`,
        { method: 'DELETE', headers: getAuthHeader() });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'delete failed');
      setAttachments(prev => prev.filter(a => a.id !== id));
    } catch (e) { setUploadError(String(e)); }
    setConfirmDel(null);
  };

  const stageLabel = (s: string) =>
    ATTACH_STAGES.find(x => x.value === s)?.label ?? s;

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 8,
        letterSpacing: '0.06em', fontWeight: 700 }}>H — SCREENSHOTS</div>

      {/* Thumbnail grid */}
      {loading ? (
        <div style={{ color: T.txtMuted, fontSize: 10 }}>Loading…</div>
      ) : attachments.length === 0 ? (
        <div style={{ color: T.txtMuted, fontSize: 10, marginBottom: 8 }}>No screenshots yet</div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
          {attachments.map(a => (
            <div key={a.id} style={{ position: 'relative', width: 90, flexShrink: 0 }}>
              <img
                src={a.serve_url}
                alt={a.filename}
                onClick={() => setFullImg(a.serve_url)}
                style={{ width: 90, height: 68, objectFit: 'cover', borderRadius: 4,
                  border: `1px solid ${T.border}`, cursor: 'zoom-in', display: 'block' }}
              />
              <div style={{ fontSize: 7, color: T.txtMuted, textAlign: 'center',
                marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {stageLabel(a.stage)}
              </div>
              {confirmDel === a.id ? (
                <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                  background: '#000000cc', borderRadius: 4, display: 'flex',
                  flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                  <div style={{ fontSize: 8, color: '#fff', textAlign: 'center' }}>Delete?</div>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <button onClick={() => handleDelete(a.id)}
                      style={{ background: T.red + '99', border: 'none', borderRadius: 3,
                        color: '#fff', fontSize: 8, padding: '2px 6px', cursor: 'pointer' }}>
                      Yes
                    </button>
                    <button onClick={() => setConfirmDel(null)}
                      style={{ background: '#ffffff33', border: 'none', borderRadius: 3,
                        color: '#fff', fontSize: 8, padding: '2px 6px', cursor: 'pointer' }}>
                      No
                    </button>
                  </div>
                </div>
              ) : (
                <button onClick={() => setConfirmDel(a.id)}
                  style={{ position: 'absolute', top: 2, right: 2, background: '#00000066',
                    border: 'none', borderRadius: 3, color: '#fff', fontSize: 10, width: 18, height: 18,
                    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    lineHeight: 1 }}>
                  ×
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Upload row */}
      {attachments.length < 10 && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <select value={uploadStage} onChange={e => setUploadStage(e.target.value)}
            style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
              color: T.txtSec, fontSize: 9, padding: '3px 6px' }}>
            {ATTACH_STAGES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/gif,image/webp"
            onChange={e => handleUpload(e.target.files)}
            style={{ display: 'none' }} />
          <button onClick={() => fileRef.current?.click()} disabled={uploading}
            style={{ background: T.cyan + '22', border: `1px solid ${T.cyan}44`,
              borderRadius: 4, color: T.cyan, padding: '4px 10px', fontSize: 9,
              cursor: uploading ? 'default' : 'pointer' }}>
            {uploading ? 'Uploading…' : '+ Add Screenshot'}
          </button>
          <span style={{ fontSize: 8, color: T.txtMuted }}>max 5 MB · {attachments.length}/10</span>
        </div>
      )}

      {uploadError && (
        <div style={{ fontSize: 9, color: T.red, marginTop: 4 }}>{uploadError}</div>
      )}

      {/* Full-image lightbox */}
      {fullImg && (
        <div onClick={() => setFullImg(null)}
          style={{ position: 'fixed', inset: 0, zIndex: 700, background: '#000000cc',
            display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'zoom-out' }}>
          <img src={fullImg} alt="screenshot"
            style={{ maxWidth: '90vw', maxHeight: '90vh', borderRadius: 6,
              border: `1px solid ${T.border}` }} />
        </div>
      )}
    </div>
  );
};

// ── Phase 7N: Review system types ─────────────────────────────────────────────
interface JReviewVocabulary {
  mistake_tags: string[];
  positive_tags: string[];
  emotion_tags: string[];
  followed_plan: string[];
  plan_checklist: string[];
}

interface JReviewData {
  review_status: string;
  followed_plan: string | null;
  plan_checklist: Record<string, boolean> | null;
  mistake_tags: string[] | null;
  positive_tags: string[] | null;
  emotion_tags: Array<{ tag: string; intensity: number | null }> | null;
  pre_trade_notes: string | null;
  in_trade_notes: string | null;
  post_trade_review: string | null;
  lesson_learned: string | null;
  what_differently: string | null;
  what_repeat: string | null;
  setup_quality: number | null;
  execution_quality: number | null;
  discipline_quality: number | null;
  overall_quality: number | null;
  exclude_reason: string | null;
  reviewed_at: string | null;
}

// ── Phase 7N: Review Modal ─────────────────────────────────────────────────────

const TagChips: React.FC<{
  tags: string[];
  selected: string[];
  onToggle: (tag: string) => void;
  activeColor?: string;
}> = ({ tags, selected, onToggle, activeColor = T.cyan }) => (
  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
    {tags.map(tag => {
      const on = selected.includes(tag);
      return (
        <button key={tag} onClick={() => onToggle(tag)}
          style={{ background: on ? activeColor + '33' : T.panelAlt,
            border: `1px solid ${on ? activeColor + '88' : T.border}`,
            borderRadius: 10, color: on ? activeColor : T.txtMuted,
            padding: '3px 8px', fontSize: 9, cursor: 'pointer',
            fontWeight: on ? 700 : 400, transition: 'all 0.1s' }}>
          {tag.replace(/_/g, ' ')}
        </button>
      );
    })}
  </div>
);

const RatingPicker: React.FC<{
  label: string; value: number | null; onChange: (v: number | null) => void;
}> = ({ label, value, onChange }) => (
  <div>
    <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 4 }}>{label}</div>
    <div style={{ display: 'flex', gap: 4 }}>
      {[1,2,3,4,5].map(n => {
        const on = (value ?? 0) >= n;
        return (
          <button key={n} onClick={() => onChange(value === n ? null : n)}
            title={['','Poor','Fair','Avg','Good','Excellent'][n]}
            style={{ background: on ? T.amber + '33' : T.panelAlt,
              border: `1px solid ${on ? T.amber : T.border}`,
              borderRadius: 4, color: on ? T.amber : T.txtMuted,
              width: 32, height: 28, fontSize: 12, cursor: 'pointer', fontWeight: 700 }}>
            {n}
          </button>
        );
      })}
      {value !== null && (
        <button onClick={() => onChange(null)}
          style={{ background: 'none', border: 'none', color: T.txtMuted,
            fontSize: 10, cursor: 'pointer', padding: '0 4px' }}>
          ✕
        </button>
      )}
    </div>
  </div>
);

const JReviewModal: React.FC<{
  source: string; tradeId: number; tradeSummary: JTradeDetail;
  onClose: () => void; onSaved: (status: string) => void;
}> = ({ source, tradeId, tradeSummary, onClose, onSaved }) => {
  const [loading,   setLoading]   = useState(true);
  const [vocab,     setVocab]     = useState<JReviewVocabulary | null>(null);
  const [saving,    setSaving]    = useState(false);
  const [error,     setError]     = useState<string | null>(null);
  const [curStatus, setCurStatus] = useState('UNREVIEWED');

  // Edit state
  const [followedPlan,      setFollowedPlan]      = useState('');
  const [checklist,         setChecklist]          = useState<Record<string, boolean>>({});
  const [mistakeTags,       setMistakeTags]        = useState<string[]>([]);
  const [positiveTags,      setPositiveTags]       = useState<string[]>([]);
  const [emotionTags,       setEmotionTags]        = useState<{tag:string;intensity:number|null}[]>([]);
  const [preTradeNotes,     setPreTradeNotes]      = useState('');
  const [inTradeNotes,      setInTradeNotes]       = useState('');
  const [postTradeReview,   setPostTradeReview]    = useState('');
  const [lessonLearned,     setLessonLearned]      = useState('');
  const [whatDifferently,   setWhatDifferently]    = useState('');
  const [whatRepeat,        setWhatRepeat]         = useState('');
  const [setupQ,            setSetupQ]             = useState<number|null>(null);
  const [execQ,             setExecQ]              = useState<number|null>(null);
  const [discQ,             setDiscQ]              = useState<number|null>(null);
  const [overallQ,          setOverallQ]           = useState<number|null>(null);
  const [excludeReason,     setExcludeReason]      = useState('');
  const [showExclude,       setShowExclude]        = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const r = await fetch(`/api/journal/trade/${source}/${tradeId}/review`,
          { headers: getAuthHeader() });
        const d = await r.json();
        if (d.ok) {
          const rv: JReviewData = d.review;
          setVocab(d.vocabulary);
          setCurStatus(rv.review_status || 'UNREVIEWED');
          setFollowedPlan(rv.followed_plan || '');
          setChecklist(rv.plan_checklist || {});
          setMistakeTags(rv.mistake_tags || []);
          setPositiveTags(rv.positive_tags || []);
          setEmotionTags(rv.emotion_tags || []);
          setPreTradeNotes(rv.pre_trade_notes || '');
          setInTradeNotes(rv.in_trade_notes || '');
          setPostTradeReview(rv.post_trade_review || '');
          setLessonLearned(rv.lesson_learned || '');
          setWhatDifferently(rv.what_differently || '');
          setWhatRepeat(rv.what_repeat || '');
          setSetupQ(rv.setup_quality ?? null);
          setExecQ(rv.execution_quality ?? null);
          setDiscQ(rv.discipline_quality ?? null);
          setOverallQ(rv.overall_quality ?? null);
        }
      } catch { /* fail silently — still show form */ }
      setLoading(false);
    })();
  }, [source, tradeId]);

  const buildPayload = () => ({
    followed_plan:      followedPlan || null,
    plan_checklist:     Object.keys(checklist).length ? checklist : null,
    mistake_tags:       mistakeTags,
    positive_tags:      positiveTags,
    emotion_tags:       emotionTags,
    pre_trade_notes:    preTradeNotes,
    in_trade_notes:     inTradeNotes,
    post_trade_review:  postTradeReview,
    lesson_learned:     lessonLearned,
    what_differently:   whatDifferently,
    what_repeat:        whatRepeat,
    setup_quality:      setupQ,
    execution_quality:  execQ,
    discipline_quality: discQ,
    overall_quality:    overallQ,
  });

  const doSave = async (explicitStatus?: string) => {
    setSaving(true); setError(null);
    try {
      const body = { ...buildPayload(), ...(explicitStatus ? { review_status: explicitStatus } : {}) };
      const r = await fetch(`/api/journal/trade/${source}/${tradeId}/review`, {
        method: 'PATCH',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'save failed');
      setCurStatus(d.review_status);
      onSaved(d.review_status);
      if (explicitStatus === 'REVIEWED') onClose();
    } catch (e) { setError(String(e)); }
    setSaving(false);
  };

  const doExclude = async () => {
    if (!excludeReason.trim()) { setError('Reason required'); return; }
    setSaving(true); setError(null);
    try {
      const r = await fetch(`/api/journal/trade/${source}/${tradeId}/exclude`, {
        method: 'POST',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: excludeReason }),
      });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'exclude failed');
      onSaved('EXCLUDED');
      onClose();
    } catch (e) { setError(String(e)); }
    setSaving(false);
  };

  const toggleTag = (arr: string[], tag: string): string[] =>
    arr.includes(tag) ? arr.filter(t => t !== tag) : [...arr, tag];
  const toggleEmotionTag = (tag: string) =>
    setEmotionTags(prev => {
      const exists = prev.find(e => e.tag === tag);
      return exists ? prev.filter(e => e.tag !== tag) : [...prev, { tag, intensity: null }];
    });

  const sym = tradeSummary.symbol || tradeSummary.instrument || '—';
  const S = { label: { fontSize: 9, color: T.txtMuted, marginBottom: 4, letterSpacing: '0.06em', fontWeight: 700 } as React.CSSProperties };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 600, display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end' }}>
      {/* backdrop */}
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: '#00000077' }} />
      {/* panel */}
      <div style={{ position: 'relative', width: 520, maxWidth: '100vw', height: '100vh',
        background: T.panel, borderLeft: `1px solid ${T.borderMid}`,
        overflowY: 'auto', padding: '20px 22px', boxShadow: '-12px 0 40px #00000066',
        display: 'flex', flexDirection: 'column', gap: 0 }}>

        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 800, color: T.txtPri }}>
              Trade Review — {sym}
            </div>
            <div style={{ display: 'flex', gap: 6, marginTop: 4, alignItems: 'center' }}>
              {jReviewBadge(curStatus)}
              <span style={{ fontSize: 9, color: T.txtMuted }}>
                {source === 'system' ? 'System Trade' : 'Tradzella Import'} · #{tradeId}
              </span>
            </div>
          </div>
          <button onClick={onClose}
            style={{ background: 'none', border: 'none', color: T.txtMuted,
              fontSize: 20, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>

        {loading ? (
          <div style={{ color: T.txtMuted, textAlign: 'center', paddingTop: 40 }}>Loading review…</div>
        ) : (
          <>
            {/* Section A: Trade Summary (read-only) */}
            <div style={{ background: T.panelAlt, borderRadius: 6, padding: '10px 12px', marginBottom: 14,
              border: `1px solid ${T.border}` }}>
              <div style={S.label}>A — TRADE SUMMARY</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                {([
                  ['Instrument', sym],
                  ['Direction', (tradeSummary.direction || '').toUpperCase()],
                  ['Result', tradeSummary.result],
                  ['Entry', tradeSummary.entry != null ? tradeSummary.entry.toFixed(4) : '—'],
                  ['R-Multiple', tradeSummary.r_multiple != null ? (tradeSummary.r_multiple >= 0 ? '+' : '') + tradeSummary.r_multiple.toFixed(2) + 'R' : '—'],
                  ['Mode', tradeSummary.trading_mode || '—'],
                ] as [string, string][]).map(([lbl, val]) => (
                  <div key={lbl} style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: T.txtSec }}>{val}</div>
                    <div style={{ fontSize: 8, color: T.txtMuted }}>{lbl}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Section B: Plan Adherence */}
            <div style={{ marginBottom: 16 }}>
              <div style={S.label}>B — DID YOU FOLLOW THE PLAN?</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
                {(vocab?.followed_plan ?? ['YES','PARTIALLY','NO','NOT_APPLICABLE']).map(opt => {
                  const on = followedPlan === opt;
                  const col = opt === 'YES' ? T.green : opt === 'PARTIALLY' ? T.amber : opt === 'NO' ? T.red : T.txtMuted;
                  return (
                    <button key={opt} onClick={() => setFollowedPlan(on ? '' : opt)}
                      style={{ background: on ? col + '33' : T.panelAlt,
                        border: `1px solid ${on ? col : T.border}`,
                        borderRadius: 6, color: on ? col : T.txtMuted,
                        padding: '5px 12px', fontSize: 10, cursor: 'pointer', fontWeight: on ? 700 : 400 }}>
                      {opt.replace(/_/g, ' ')}
                    </button>
                  );
                })}
              </div>
              {(vocab?.plan_checklist ?? []).length > 0 && (
                <div>
                  <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 4 }}>CHECKLIST</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3 }}>
                    {(vocab?.plan_checklist ?? []).map(item => (
                      <label key={item} style={{ display: 'flex', alignItems: 'center', gap: 5,
                        cursor: 'pointer', fontSize: 9, color: checklist[item] ? T.green : T.txtMuted }}>
                        <input type="checkbox" checked={!!checklist[item]}
                          onChange={e => setChecklist(c => ({ ...c, [item]: e.target.checked }))}
                          style={{ accentColor: T.green, margin: 0 }} />
                        {item.replace(/_/g, ' ')}
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Section C: Mistake Tags */}
            <div style={{ marginBottom: 16 }}>
              <div style={S.label}>C — MISTAKES MADE</div>
              <TagChips
                tags={vocab?.mistake_tags ?? []}
                selected={mistakeTags}
                onToggle={tag => setMistakeTags(t => toggleTag(t, tag))}
                activeColor={T.red}
              />
            </div>

            {/* Section D: Positive Tags */}
            <div style={{ marginBottom: 16 }}>
              <div style={S.label}>D — WHAT WENT RIGHT</div>
              <TagChips
                tags={vocab?.positive_tags ?? []}
                selected={positiveTags}
                onToggle={tag => setPositiveTags(t => toggleTag(t, tag))}
                activeColor={T.green}
              />
            </div>

            {/* Section E: Emotion Tags */}
            <div style={{ marginBottom: 16 }}>
              <div style={S.label}>E — EMOTIONS DURING TRADE</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {(vocab?.emotion_tags ?? []).map(tag => {
                  const et = emotionTags.find(e => e.tag === tag);
                  const on = !!et;
                  return (
                    <button key={tag} onClick={() => toggleEmotionTag(tag)}
                      style={{ background: on ? T.amber + '33' : T.panelAlt,
                        border: `1px solid ${on ? T.amber + '88' : T.border}`,
                        borderRadius: 10, color: on ? T.amber : T.txtMuted,
                        padding: '3px 8px', fontSize: 9, cursor: 'pointer', fontWeight: on ? 700 : 400 }}>
                      {tag}
                    </button>
                  );
                })}
              </div>
              {emotionTags.length > 0 && (
                <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {emotionTags.map(et => (
                    <div key={et.tag} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ fontSize: 9, color: T.amber }}>{et.tag}</span>
                      <select value={et.intensity ?? ''} onChange={e => {
                        const v = e.target.value ? parseInt(e.target.value) : null;
                        setEmotionTags(prev => prev.map(x => x.tag === et.tag ? { ...x, intensity: v } : x));
                      }} style={{ background: T.panelAlt, border: `1px solid ${T.border}`,
                        borderRadius: 3, color: T.txtSec, fontSize: 9, padding: '1px 3px' }}>
                        <option value="">—</option>
                        {[1,2,3,4,5].map(n => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Section F: Notes */}
            <div style={{ marginBottom: 16 }}>
              <div style={S.label}>F — NOTES</div>
              {([
                ['Pre-Trade Context', preTradeNotes,    setPreTradeNotes],
                ['During Trade',     inTradeNotes,     setInTradeNotes],
                ['Post-Trade Review (required for ✓)', postTradeReview, setPostTradeReview],
                ['Lesson Learned (required for ✓)',    lessonLearned,   setLessonLearned],
                ['What Would I Do Differently',        whatDifferently, setWhatDifferently],
                ['What Would I Repeat',                whatRepeat,      setWhatRepeat],
              ] as [string, string, React.Dispatch<React.SetStateAction<string>>][]).map(([lbl, val, set]) => (
                <div key={lbl} style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 2 }}>{lbl}</div>
                  <textarea value={val} onChange={e => set(e.target.value)}
                    rows={2} style={{ width: '100%', background: T.panelAlt,
                      border: `1px solid ${T.border}`, borderRadius: 4,
                      color: T.txtPri, fontSize: 10, padding: '5px 7px',
                      resize: 'vertical', boxSizing: 'border-box', lineHeight: 1.5 }} />
                </div>
              ))}
            </div>

            {/* Section G: Ratings */}
            <div style={{ marginBottom: 16 }}>
              <div style={S.label}>G — RATINGS (1–5)</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <RatingPicker label="Setup Quality"      value={setupQ}    onChange={setSetupQ} />
                <RatingPicker label="Execution Quality"  value={execQ}     onChange={setExecQ} />
                <RatingPicker label="Discipline Quality" value={discQ}     onChange={setDiscQ} />
                <RatingPicker label="Overall Quality (required for ✓)" value={overallQ} onChange={setOverallQ} />
              </div>
            </div>

            {/* Section H: Screenshots */}
            <JAttachmentSection source={source} tradeId={tradeId} />

            {/* Error */}
            {error && (
              <div style={{ background: T.red + '22', border: `1px solid ${T.red}44`,
                borderRadius: 4, padding: '6px 10px', fontSize: 10, color: T.red, marginBottom: 10 }}>
                {error}
              </div>
            )}

            {/* Action buttons */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
              <button onClick={() => doSave('IN_PROGRESS')} disabled={saving}
                style={{ background: T.amber + '22', border: `1px solid ${T.amber}44`,
                  borderRadius: 5, color: T.amber, padding: '7px 16px', fontSize: 10,
                  cursor: saving ? 'default' : 'pointer', fontWeight: 600 }}>
                {saving ? 'Saving…' : 'Save Draft'}
              </button>
              <button onClick={() => doSave('REVIEWED')} disabled={saving}
                style={{ background: T.green + '22', border: `1px solid ${T.green}55`,
                  borderRadius: 5, color: T.green, padding: '7px 16px', fontSize: 10,
                  cursor: saving ? 'default' : 'pointer', fontWeight: 700 }}>
                {saving ? 'Saving…' : '✓ Mark Reviewed'}
              </button>
              <button onClick={() => setShowExclude(x => !x)}
                style={{ background: 'none', border: `1px solid ${T.border}`,
                  borderRadius: 5, color: T.txtMuted, padding: '7px 12px', fontSize: 10,
                  cursor: 'pointer', marginLeft: 'auto' }}>
                Exclude Trade
              </button>
            </div>

            {/* Exclude panel */}
            {showExclude && (
              <div style={{ background: T.red + '11', border: `1px solid ${T.red}33`,
                borderRadius: 6, padding: '10px 12px', marginBottom: 10 }}>
                <div style={{ fontSize: 9, color: T.red, marginBottom: 6, fontWeight: 700 }}>
                  EXCLUDE TRADE — this hides it from the review queue
                </div>
                <input value={excludeReason} onChange={e => setExcludeReason(e.target.value)}
                  placeholder="Reason (e.g. 'Data corruption', 'Platform glitch')"
                  style={{ width: '100%', background: T.panelAlt, border: `1px solid ${T.border}`,
                    borderRadius: 4, color: T.txtPri, padding: '5px 8px', fontSize: 10,
                    boxSizing: 'border-box', marginBottom: 6 }} />
                <button onClick={doExclude} disabled={saving}
                  style={{ background: T.red + '33', border: `1px solid ${T.red}55`,
                    borderRadius: 4, color: T.red, padding: '4px 12px', fontSize: 10,
                    cursor: saving ? 'default' : 'pointer', fontWeight: 700 }}>
                  Confirm Exclude
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

// ── Phase 7N Batch B: Review Queue Tab ────────────────────────────────────────
interface JQueueTrade {
  id: number; source: string; date: string; instrument: string; direction: string;
  strategy_name: string; result: string; r_multiple: number; review_status: string; trading_mode: string;
}

interface JQueueBuckets {
  UNREVIEWED: JQueueTrade[];
  IN_PROGRESS: JQueueTrade[];
  NEEDS_DATA: JQueueTrade[];
  MISSING_STRATEGY: JQueueTrade[];
  EXCLUDED: JQueueTrade[];
}

const QUEUE_BUCKET_CFG = [
  { key: 'UNREVIEWED',       label: 'Unreviewed',       color: '#6b7280' },
  { key: 'IN_PROGRESS',      label: 'Draft',            color: '#f59e0b' },
  { key: 'NEEDS_DATA',       label: 'Missing Data',     color: '#ef4444' },
  { key: 'MISSING_STRATEGY', label: 'Missing Strategy', color: '#8b5cf6' },
  { key: 'EXCLUDED',         label: 'Excluded',         color: '#374151' },
] as const;

type QueueBucketKey = typeof QUEUE_BUCKET_CFG[number]['key'];

const JReviewQueueTab: React.FC<{ onOpenReview?: (source: string, id: number, detail: JTradeDetail) => void }> = ({ onOpenReview }) => {
  const [buckets,   setBuckets]   = useState<JQueueBuckets | null>(null);
  const [counts,    setCounts]    = useState<Record<string, number>>({});
  const [loading,   setLoading]   = useState(false);
  const [error,     setError]     = useState<string | null>(null);
  const [activeBucket, setActiveBucket] = useState<QueueBucketKey>('UNREVIEWED');

  // Filters
  const [fSrc,    setFSrc]    = useState('');
  const [fInst,   setFInst]   = useState('');
  const [fRes,    setFRes]    = useState('');
  const [fFrom,   setFFrom]   = useState('');
  const [fTo,     setFTo]     = useState('');

  // Review modal state (inline for this tab)
  const [reviewTrade, setReviewTrade] = useState<{ source: string; id: number; detail: JTradeDetail } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const fetchQueue = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const params = new URLSearchParams({
        ...(fSrc  ? { source: fSrc } : {}),
        ...(fInst ? { instrument: fInst } : {}),
        ...(fRes  ? { result: fRes } : {}),
        ...(fFrom ? { date_from: fFrom } : {}),
        ...(fTo   ? { date_to: fTo } : {}),
      });
      const r = await fetch(`/api/journal/review-queue-full?${params}`, { headers: getAuthHeader() });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'failed');
      setBuckets(d.buckets);
      setCounts(d.counts || {});
    } catch (e) { setError(String(e)); }
    setLoading(false);
  }, [fSrc, fInst, fRes, fFrom, fTo]);

  useEffect(() => { fetchQueue(); }, [fetchQueue]);

  const openReview = async (t: JQueueTrade) => {
    setDetailLoading(true);
    try {
      const r = await fetch(`/api/journal/trade/${t.source}/${t.id}`, { headers: getAuthHeader() });
      const d = await r.json();
      if (d.ok) {
        if (onOpenReview) onOpenReview(t.source, t.id, d.trade);
        else setReviewTrade({ source: t.source, id: t.id, detail: d.trade });
      }
    } catch { /* ignore */ }
    setDetailLoading(false);
  };

  const onReviewSaved = (status: string) => {
    // Refresh queue after saving
    fetchQueue();
    setReviewTrade(null);
  };

  const activeCfg = QUEUE_BUCKET_CFG.find(b => b.key === activeBucket)!;
  const activeList = (buckets?.[activeBucket as keyof JQueueBuckets] ?? []) as JQueueTrade[];

  return (
    <div>
      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <select value={fSrc} onChange={e => setFSrc(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Sources</option>
          <option value="system">System</option>
          <option value="tradzella">Tradzella</option>
        </select>
        <select value={fInst} onChange={e => setFInst(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Instruments</option>
          {['MGC','MNQ','MES','MYM'].map(i => <option key={i} value={i}>{i}</option>)}
        </select>
        <select value={fRes} onChange={e => setFRes(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Results</option>
          <option value="win">Win</option>
          <option value="loss">Loss</option>
          <option value="be">BE</option>
        </select>
        <input type="date" value={fFrom} onChange={e => setFFrom(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }} />
        <span style={{ color: T.txtMuted, fontSize: 10 }}>to</span>
        <input type="date" value={fTo} onChange={e => setFTo(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }} />
        <button onClick={fetchQueue}
          style={{ background: T.cyan + '22', border: `1px solid ${T.cyan}44`, borderRadius: 4,
            color: T.cyan, padding: '4px 10px', fontSize: 11, cursor: 'pointer' }}>
          Refresh
        </button>
      </div>

      {/* Status bucket sub-tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 12, flexWrap: 'wrap' }}>
        {QUEUE_BUCKET_CFG.map(cfg => {
          const cnt = counts[cfg.key] ?? 0;
          const isActive = activeBucket === cfg.key;
          return (
            <button key={cfg.key} onClick={() => setActiveBucket(cfg.key)}
              style={{ background: isActive ? cfg.color + '22' : T.panelAlt,
                border: `1px solid ${isActive ? cfg.color + '88' : T.border}`,
                borderRadius: 6, color: isActive ? cfg.color : T.txtSec,
                padding: '5px 12px', fontSize: 10, cursor: 'pointer', fontWeight: isActive ? 700 : 400,
                display: 'flex', alignItems: 'center', gap: 6 }}>
              {cfg.label}
              <span style={{ background: isActive ? cfg.color + '33' : T.border + '44',
                borderRadius: 8, padding: '0 6px', fontSize: 9, fontWeight: 700,
                color: isActive ? cfg.color : T.txtMuted }}>
                {cnt}
              </span>
            </button>
          );
        })}
      </div>

      {loading && <div style={{ color: T.txtMuted, fontSize: 11 }}>Loading…</div>}
      {error   && <div style={{ color: T.red,     fontSize: 11 }}>Error: {error}</div>}

      {/* Trade rows */}
      {!loading && !error && (
        <div style={{ maxHeight: 480, overflowY: 'auto' }}>
          {activeList.length === 0 ? (
            <div style={{ color: T.txtMuted, fontSize: 11, textAlign: 'center', padding: '20px 0' }}>
              {activeCfg.label} — nothing here
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
              <thead>
                <tr>
                  {['Date','Instrument','Dir','Result','R','Strategy','Review',''].map(h => (
                    <th key={h} style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                      paddingBottom: 6, fontSize: 9, position: 'sticky', top: 0,
                      background: T.panel, whiteSpace: 'nowrap' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {activeList.map(t => {
                  const rCol = t.r_multiple > 0 ? T.green : t.r_multiple < 0 ? T.red : T.txtMuted;
                  return (
                    <tr key={`${t.source}:${t.id}`} style={{ borderTop: `1px solid ${T.border}` }}>
                      <td style={{ padding: '5px 8px 5px 0', color: T.txtSec, whiteSpace: 'nowrap', fontSize: 9 }}>
                        {jFmtShortDate(t.date)}
                      </td>
                      <td style={{ padding: '5px 8px 5px 0', color: T.txtPri, fontWeight: 700 }}>
                        {t.instrument}
                      </td>
                      <td style={{ padding: '5px 8px 5px 0', color: T.txtSec, textTransform: 'capitalize' }}>
                        {t.direction}
                      </td>
                      <td style={{ padding: '5px 8px 5px 0' }}>
                        {jResultBadge(t.result)}
                      </td>
                      <td style={{ padding: '5px 8px 5px 0', fontFamily: T.mono, fontWeight: 700, color: rCol }}>
                        {t.r_multiple != null ? (t.r_multiple >= 0 ? '+' : '') + t.r_multiple.toFixed(2) + 'R' : '—'}
                      </td>
                      <td style={{ padding: '5px 8px 5px 0', color: T.txtSec, maxWidth: 120,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {t.strategy_name || '—'}
                      </td>
                      <td style={{ padding: '5px 8px 5px 0' }}>
                        {jReviewBadge(t.review_status)}
                      </td>
                      <td style={{ padding: '5px 0' }}>
                        <button onClick={() => openReview(t)} disabled={detailLoading}
                          style={{ background: T.cyan + '22', border: `1px solid ${T.cyan}44`,
                            borderRadius: 4, color: T.cyan, padding: '3px 8px', fontSize: 9,
                            cursor: detailLoading ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>
                          Review
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Review modal (inline) */}
      {reviewTrade && (
        <JReviewModal
          source={reviewTrade.source}
          tradeId={reviewTrade.id}
          tradeSummary={reviewTrade.detail}
          onClose={() => setReviewTrade(null)}
          onSaved={onReviewSaved}
        />
      )}
    </div>
  );
};

const JTabBar: React.FC<{ active: JTab; onChange: (t: JTab) => void; queueBadge?: number | null }> = ({ active, onChange, queueBadge }) => {
  const tabs: { id: JTab; label: string; badge?: number | null }[] = [
    { id: 'trades',      label: 'Trades' },
    { id: 'queue',       label: 'Review Queue', badge: queueBadge },
    { id: 'import',      label: 'Import' },
    { id: 'analytics',   label: 'Analytics' },
    { id: 'playbook',    label: 'Playbook' },
    { id: 'learning',    label: 'Learning' },
    { id: 'directional', label: 'Direction ↕' },
    { id: 'coaching',    label: '🧠 Coaching' },
  ];
  return (
    <div style={{ display: 'flex', gap: 2, marginBottom: 14, borderBottom: `1px solid ${T.border}`, paddingBottom: 0, overflowX: 'auto' }}>
      {tabs.map(t => (
        <button key={t.id} onClick={() => onChange(t.id)}
          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '6px 14px',
            fontSize: 11, fontWeight: active === t.id ? 700 : 400,
            color: active === t.id ? T.cyan : T.txtSec,
            borderBottom: `2px solid ${active === t.id ? T.cyan : 'transparent'}`,
            marginBottom: -1, letterSpacing: '0.04em', whiteSpace: 'nowrap',
            display: 'flex', alignItems: 'center', gap: 5 }}>
          {t.label}
          {t.badge != null && t.badge > 0 && (
            <span style={{ background: T.amber + '33', color: T.amber, borderRadius: 8,
              padding: '0 5px', fontSize: 9, fontWeight: 700, lineHeight: '16px' }}>
              {t.badge}
            </span>
          )}
        </button>
      ))}
    </div>
  );
};

// ── Trades tab ────────────────────────────────────────────────────────────────
// ── Phase 7O.1 — URL helpers for drill-down state persistence ────────────────
const _J_TAB_PARAM   = 'j_tab';
const _J_DRILL_PARAM = 'j_drill';

function _urlReadJState(): { tab: JTab | null; drill: JDrillFilter | null } {
  try {
    const p = new URLSearchParams(window.location.search);
    const tab = p.get(_J_TAB_PARAM) as JTab | null;
    const drillStr = p.get(_J_DRILL_PARAM);
    let drill: JDrillFilter | null = null;
    if (drillStr) { try { drill = JSON.parse(drillStr); } catch { /* ignore */ } }
    return { tab, drill };
  } catch { return { tab: null, drill: null }; }
}

function _urlSetJState(tab: JTab, drill: JDrillFilter | null, push: boolean) {
  try {
    const p = new URLSearchParams(window.location.search);
    p.set(_J_TAB_PARAM, tab);
    if (drill) { p.set(_J_DRILL_PARAM, JSON.stringify(drill)); }
    else        { p.delete(_J_DRILL_PARAM); }
    const url = '?' + p.toString();
    if (push) { window.history.pushState(null, '', url); }
    else      { window.history.replaceState(null, '', url); }
  } catch { /* non-critical */ }
}

/** Returns a list of {key, label} pairs for each active filter in a JDrillFilter */
function _drillChips(drill: JDrillFilter): { key: keyof JDrillFilter; label: string }[] {
  type K = keyof JDrillFilter;
  const chips: { key: K; label: string }[] = [];
  const tag = (s: string) => s.replace(/_/g, ' ').toUpperCase();
  if (drill.review_status) chips.push({ key: 'review_status', label: drill.review_status });
  if (drill.mistake_tag)   chips.push({ key: 'mistake_tag',   label: `MISTAKE: ${tag(drill.mistake_tag)}` });
  if (drill.positive_tag)  chips.push({ key: 'positive_tag',  label: `BEHAVIOR: ${tag(drill.positive_tag)}` });
  if (drill.emotion_tag)   chips.push({ key: 'emotion_tag',   label: `EMOTION: ${tag(drill.emotion_tag)}` });
  if (drill.followed_plan) chips.push({ key: 'followed_plan', label: `PLAN: ${drill.followed_plan}` });
  if (drill.strategy)      chips.push({ key: 'strategy',      label: `STRATEGY: ${tag(drill.strategy)}` });
  if (drill.session)       chips.push({ key: 'session',       label: `SESSION: ${tag(drill.session)}` });
  if (drill.instrument)    chips.push({ key: 'instrument',    label: `INST: ${drill.instrument}` });
  if (drill.mode)          chips.push({ key: 'mode',          label: `MODE: ${drill.mode}` });
  if (drill.source)        chips.push({ key: 'source',        label: `SOURCE: ${drill.source.toUpperCase()}` });
  if (drill.date_from)     chips.push({ key: 'date_from',     label: `FROM: ${drill.date_from}` });
  if (drill.date_to)       chips.push({ key: 'date_to',       label: `TO: ${drill.date_to}` });
  if (drill.rating_field && drill.rating_value != null)
    chips.push({ key: 'rating_field', label: `${drill.rating_field.replace(/_quality$/, '').toUpperCase()}: ${drill.rating_value}★` });
  // Phase 7O.3: rating range chip — clears together with rating_field
  if (drill.rating_field && drill.rating_min != null && drill.rating_max != null)
    chips.push({ key: 'rating_min', label: `${drill.rating_field.replace(/_quality$/, '').toUpperCase()}: ${drill.rating_min}–${drill.rating_max}★` });
  // Phase 7O.3: realized R range chips
  if (drill.realized_r_min != null) chips.push({ key: 'realized_r_min', label: `R≥${drill.realized_r_min}` });
  if (drill.realized_r_max != null) chips.push({ key: 'realized_r_max', label: `R≤${drill.realized_r_max}` });
  // Phase 7O.3: quality classification chip
  if (drill.quality_classification) chips.push({ key: 'quality_classification', label: drill.quality_classification === 'high_quality_loss' ? 'HIGH-QUALITY LOSS' : 'LOW-QUALITY WIN' });
  if (drill.result)        chips.push({ key: 'result',        label: `RESULT: ${drill.result.toUpperCase()}` });
  // Phase 7O.2: intraday block chip — clearing block_start also clears block_end + timezone
  if (drill.entry_block_start) chips.push({ key: 'entry_block_start', label: `TIME: ${drill.entry_block_start}` });
  return chips;
}

/** Filter keys that represent actual server params (not display-only) */
const _DRILL_SERVER_KEYS: (keyof JDrillFilter)[] = [
  'review_status','mistake_tag','positive_tag','emotion_tag','followed_plan',
  'strategy','session','instrument','mode','source','date_from','date_to',
  'rating_field','rating_value','result',
  // Phase 7O.2: intraday block filter
  'entry_block_start','entry_block_end','display_timezone',
  // Phase 7O.3: correlation drill-down
  'rating_min','rating_max','realized_r_min','realized_r_max','quality_classification',
];

// ── NJ Screenshot Section (Phase 7K-C) ───────────────────────────────────────
const NJScreenshotSection: React.FC<{
  tradeId: string;
  screenshots: NJScreenshotMeta[];
  onUpdated: (updated: NJScreenshotMeta[]) => void;
}> = ({ tradeId, screenshots, onUpdated }) => {
  const [uploadState, setUploadState] = useState<'idle' | 'uploading' | 'error'>('idle');
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [deletingId,  setDeletingId]  = useState<string | null>(null);
  const [category,    setCategory]    = useState('ENTRY');
  const [caption,     setCaption]     = useState('');
  // Blob URL cache for authenticated image loading.
  // <img src> cannot send Authorization headers, so we fetch each image with
  // getAuthHeader() and create an object URL from the response blob.
  const [blobUrls, setBlobUrls] = useState<Record<string, string>>({});
  const fileRef = React.useRef<HTMLInputElement>(null);

  // Load blob URLs for any screenshot that doesn't have one yet.
  useEffect(() => {
    const ids = screenshots.map(s => s.attachment_id);
    const missing = ids.filter(id => !blobUrls[id]);
    if (missing.length === 0) return;
    let cancelled = false;
    Promise.all(
      missing.map(async id => {
        try {
          const r = await fetch(`/api/journal/native-screenshot/${id}`, { headers: getAuthHeader() });
          if (!r.ok) return null;
          const blob = await r.blob();
          const url = URL.createObjectURL(blob);
          return { id, url };
        } catch { return null; }
      })
    ).then(results => {
      if (cancelled) return;
      const updates: Record<string, string> = {};
      for (const r of results) { if (r) updates[r.id] = r.url; }
      if (Object.keys(updates).length > 0) {
        setBlobUrls(prev => ({ ...prev, ...updates }));
      }
    });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenshots]);

  // Revoke blob URLs when the component unmounts to avoid memory leaks.
  useEffect(() => {
    return () => { Object.values(blobUrls).forEach(url => URL.revokeObjectURL(url)); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleUpload = async (file: File) => {
    setUploadState('uploading'); setUploadError(null);
    try {
      const params = new URLSearchParams({ category, caption: caption.slice(0, 200) });
      const r = await fetch(
        `/api/journal/native-trades/${tradeId}/screenshots/upload?${params}`,
        {
          method: 'POST',
          headers: { ...getAuthHeader(), 'Content-Type': file.type || 'image/jpeg' },
          body: file,
        }
      );
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'upload failed');
      const newMeta: NJScreenshotMeta = {
        attachment_id: d.attachment_id,
        category:      d.category,
        caption:       d.caption,
        storage_key:   d.storage_key,
        mime_type:     d.mime_type,
        file_size:     d.file_size,
        uploaded_at:   d.uploaded_at,
      };
      // Pre-load blob URL for the newly uploaded image using the original file
      // (avoids a round-trip immediately after upload).
      const newBlobUrl = URL.createObjectURL(file);
      setBlobUrls(prev => ({ ...prev, [d.attachment_id]: newBlobUrl }));
      onUpdated([...screenshots, newMeta]);
      setCaption('');
      setUploadState('idle');
      if (fileRef.current) fileRef.current.value = '';
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : 'upload failed');
      setUploadState('error');
    }
  };

  const handleDelete = async (attachmentId: string) => {
    setDeletingId(attachmentId);
    try {
      const r = await fetch(
        `/api/journal/native-trades/${tradeId}/screenshots/${attachmentId}`,
        { method: 'DELETE', headers: getAuthHeader() }
      );
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'delete failed');
      // Revoke and remove the blob URL for the deleted screenshot.
      setBlobUrls(prev => {
        const copy = { ...prev };
        if (copy[attachmentId]) { URL.revokeObjectURL(copy[attachmentId]); delete copy[attachmentId]; }
        return copy;
      });
      onUpdated(screenshots.filter(s => s.attachment_id !== attachmentId));
    } catch (e: unknown) {
      console.error('screenshot delete failed:', e);
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 4, fontWeight: 700 }}>
        📷 Screenshots ({screenshots.length}/{10})
      </div>

      {/* Existing screenshots — rendered from authenticated blob URLs */}
      {screenshots.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
          {screenshots.map(s => (
            <div key={s.attachment_id} style={{ position: 'relative', width: 120 }}>
              {blobUrls[s.attachment_id] ? (
                <img
                  src={blobUrls[s.attachment_id]}
                  alt={s.caption || s.category}
                  style={{ width: 120, height: 80, objectFit: 'cover', borderRadius: 5,
                    border: `1px solid ${T.border}`, display: 'block' }}
                />
              ) : (
                <div style={{ width: 120, height: 80, borderRadius: 5,
                  border: `1px solid ${T.border}`, background: T.panelAlt,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 8, color: T.txtMuted }}>
                  Loading…
                </div>
              )}
              <div style={{ fontSize: 7, color: T.txtMuted, marginTop: 2, textAlign: 'center' }}>
                {s.category}{s.caption ? ` · ${s.caption.slice(0,20)}` : ''}
              </div>
              <button
                onClick={() => handleDelete(s.attachment_id)}
                disabled={deletingId === s.attachment_id}
                title="Delete screenshot"
                style={{ position: 'absolute', top: 3, right: 3, background: T.red + 'cc',
                  border: 'none', borderRadius: 3, color: '#fff', padding: '1px 5px',
                  fontSize: 10, cursor: 'pointer', lineHeight: 1.2 }}>
                {deletingId === s.attachment_id ? '…' : '×'}
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Upload controls */}
      {screenshots.length < 10 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <select value={category} onChange={e => setCategory(e.target.value)}
            style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
              color: T.txtSec, padding: '3px 6px', fontSize: 9 }}>
            {['PRE_ENTRY','ENTRY','MANAGEMENT','EXIT','REVIEW','OTHER'].map(c => (
              <option key={c} value={c}>{c.replace(/_/g,' ')}</option>
            ))}
          </select>
          <input type="text" value={caption} onChange={e => setCaption(e.target.value)}
            placeholder="Caption (optional)"
            style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
              color: T.txtSec, padding: '3px 7px', fontSize: 9, width: 130 }} />
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/gif,image/webp"
            style={{ display: 'none' }}
            onChange={e => {
              const file = e.target.files?.[0];
              if (file) handleUpload(file);
            }} />
          <button onClick={() => fileRef.current?.click()}
            disabled={uploadState === 'uploading'}
            style={{ background: T.cyan + '22', border: `1px solid ${T.cyan}44`,
              borderRadius: 4, color: T.cyan, padding: '3px 10px', fontSize: 9,
              cursor: uploadState === 'uploading' ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>
            {uploadState === 'uploading' ? 'Uploading…' : '📷 Add Screenshot'}
          </button>
        </div>
      )}
      {uploadState === 'error' && uploadError && (
        <div style={{ fontSize: 8, color: T.red, marginTop: 3 }}>Upload failed: {uploadError}</div>
      )}
    </div>
  );
};

// ── NJ Review Section (Phase 7K-C) ───────────────────────────────────────────
const NJReviewSection: React.FC<{
  detail: NJTradeDetail;
  onSaved: (updated: Partial<NJTradeDetail>) => void;
}> = ({ detail, onSaved }) => {
  const rd = detail.review_data || {};
  const rc = detail.review_completeness;

  // Local form state — mirrors review_data + top-level fields
  const [status,      setStatus]      = useState<string>(detail.review_status || 'UNREVIEWED');
  const [notes,       setNotes]       = useState<string>(detail.review_notes || '');
  const [followedPlan,setFollowedPlan]= useState<string>(rd.followed_plan || '');
  const [setupQ,      setSetupQ]      = useState<number | ''>(rd.setup_quality ?? '');
  const [execQ,       setExecQ]       = useState<number | ''>(rd.execution_quality ?? '');
  const [mgmtQ,       setMgmtQ]       = useState<number | ''>(rd.management_quality ?? '');
  const [emotionCtrl, setEmotionCtrl] = useState<number | ''>(rd.emotional_control ?? '');
  const [mistakeTags, setMistakeTags] = useState<string[]>(rd.mistake_tags || []);
  const [emotionTags, setEmotionTags] = useState<string[]>(rd.emotion_tags || []);
  const [positiveTags,setPositiveTags]= useState<string[]>(rd.positive_tags || []);
  const [lesson,      setLesson]      = useState<string>(rd.lesson || '');
  const [wentWell,    setWentWell]    = useState<string>(rd.what_went_well || '');
  const [toImprove,   setToImprove]   = useState<string>(rd.what_to_improve || '');
  const [overrideAss, setOverrideAss] = useState<string>(rd.override_assessment || '');
  const [saveState,   setSaveState]   = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [dirty,       setDirty]       = useState(false);

  const hasManualOverride = Boolean(
    (detail.management_events || []).some((e: unknown) => (e as Record<string,unknown>)['source'] === 'operator')
  );

  const markDirty = () => setDirty(true);

  const handleSave = async () => {
    setSaveState('saving');
    try {
      const body: Record<string, unknown> = {
        review_status: status,
        review_notes: notes,
        followed_plan: followedPlan || null,
        setup_quality: setupQ !== '' ? Number(setupQ) : null,
        execution_quality: execQ !== '' ? Number(execQ) : null,
        management_quality: mgmtQ !== '' ? Number(mgmtQ) : null,
        emotional_control: emotionCtrl !== '' ? Number(emotionCtrl) : null,
        mistake_tags:  mistakeTags,
        emotion_tags:  emotionTags,
        positive_tags: positiveTags,
        lesson, what_went_well: wentWell, what_to_improve: toImprove,
        override_assessment: overrideAss || null,
      };
      const r = await fetch(`/api/journal/native-trades/${detail.id}/review`, {
        method: 'PATCH',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'save failed');
      setSaveState('saved');
      setDirty(false);
      onSaved({ review_status: status, review_notes: notes });
      setTimeout(() => setSaveState('idle'), 2500);
    } catch {
      setSaveState('error');
      setTimeout(() => setSaveState('idle'), 3000);
    }
  };

  const Rating: React.FC<{ label: string; value: number | ''; onChange: (v: number | '') => void }> = ({ label, value, onChange }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
      <span style={{ fontSize: 9, color: T.txtMuted, width: 100, flexShrink: 0 }}>{label}</span>
      <div style={{ display: 'flex', gap: 2 }}>
        {[1,2,3,4,5].map(n => (
          <button key={n} onClick={() => { onChange(value === n ? '' : n); markDirty(); }}
            style={{ width: 20, height: 20, borderRadius: 3, border: `1px solid ${Number(value) >= n ? T.cyan + '88' : T.border}`,
              background: Number(value) >= n ? T.cyan + '22' : 'transparent',
              color: Number(value) >= n ? T.cyan : T.txtMuted, fontSize: 9, cursor: 'pointer', lineHeight: 1 }}>
            {n}
          </button>
        ))}
      </div>
    </div>
  );

  const TagToggle: React.FC<{
    tags: string[]; allTags: string[]; onChange: (t: string[]) => void;
    color?: string;
  }> = ({ tags, allTags, onChange, color }) => (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginTop: 3 }}>
      {allTags.map(t => {
        const active = tags.includes(t);
        return (
          <button key={t} onClick={() => { onChange(active ? tags.filter(x => x !== t) : [...tags, t]); markDirty(); }}
            style={{ padding: '2px 6px', borderRadius: 10, fontSize: 8, cursor: 'pointer',
              background: active ? (color || T.amber) + '33' : T.panelAlt,
              border: `1px solid ${active ? (color || T.amber) + '77' : T.border}`,
              color: active ? (color || T.amber) : T.txtMuted }}>
            {t.replace(/_/g,' ')}
          </button>
        );
      })}
    </div>
  );

  return (
    <div>
      {/* Divider */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <div style={{ flex: 1, height: 1, background: T.border + '55' }} />
        <span style={{ fontSize: 8, color: T.txtMuted, letterSpacing: '0.08em', whiteSpace: 'nowrap' }}>
          OPERATOR REVIEW — EDITABLE
        </span>
        <div style={{ flex: 1, height: 1, background: T.border + '55' }} />
      </div>

      {/* Completeness bar */}
      {rc && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
            <span style={{ fontSize: 8, color: T.txtMuted }}>
              Review completeness: {rc.completed_required}/{rc.required} required, {rc.completed_optional}/{rc.optional} optional
            </span>
            {rc.missing_required.length > 0 && (
              <span style={{ fontSize: 8, color: T.amber }}>
                Missing: {rc.missing_required.map((f: string) => f.replace(/_/g,' ')).join(', ')}
              </span>
            )}
          </div>
          <div style={{ background: T.border + '44', borderRadius: 3, height: 4, overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 3,
              background: rc.completed_required >= rc.required ? T.green : T.amber,
              width: `${rc.total > 0 ? Math.round(rc.completed / rc.total * 100) : 0}%`,
              transition: 'width 0.3s ease',
            }} />
          </div>
        </div>
      )}

      {/* Status transitions */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 4 }}>Review Status</div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {['UNREVIEWED','IN_PROGRESS','REVIEWED','NEEDS_REVIEW','EXCLUDED'].map(s => (
            <button key={s} onClick={() => { setStatus(s); markDirty(); }}
              style={{ padding: '3px 8px', borderRadius: 4, fontSize: 8, cursor: 'pointer',
                background: status === s ? (s === 'EXCLUDED' ? T.red + '33' : s === 'REVIEWED' ? T.green + '22' : T.cyan + '22') : T.panelAlt,
                border: `1px solid ${status === s ? (s === 'EXCLUDED' ? T.red + '66' : s === 'REVIEWED' ? T.green + '44' : T.cyan + '44') : T.border}`,
                color: status === s ? (s === 'EXCLUDED' ? T.red : s === 'REVIEWED' ? T.green : T.cyan) : T.txtMuted,
                fontWeight: status === s ? 700 : 400 }}>
              {s.replace(/_/g,' ')}
            </button>
          ))}
        </div>
      </div>

      {/* Followed plan */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 3 }}>Followed Plan?</div>
        <div style={{ display: 'flex', gap: 4 }}>
          {['YES','PARTIALLY','NO','NOT_APPLICABLE'].map(v => (
            <button key={v} onClick={() => { setFollowedPlan(followedPlan === v ? '' : v); markDirty(); }}
              style={{ padding: '3px 8px', borderRadius: 4, fontSize: 8, cursor: 'pointer',
                background: followedPlan === v ? T.cyan + '22' : T.panelAlt,
                border: `1px solid ${followedPlan === v ? T.cyan + '44' : T.border}`,
                color: followedPlan === v ? T.cyan : T.txtMuted, fontWeight: followedPlan === v ? 700 : 400 }}>
              {v.replace(/_/g,' ')}
            </button>
          ))}
        </div>
      </div>

      {/* Quality ratings */}
      <div style={{ marginBottom: 8, background: T.panelAlt, borderRadius: 5, padding: '6px 8px',
        border: `1px solid ${T.border}` }}>
        <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 4 }}>Quality Ratings (1-5)</div>
        <Rating label="Setup quality"      value={setupQ}      onChange={v => { setSetupQ(v); markDirty(); }} />
        <Rating label="Execution quality"  value={execQ}       onChange={v => { setExecQ(v); markDirty(); }} />
        <Rating label="Management quality" value={mgmtQ}       onChange={v => { setMgmtQ(v); markDirty(); }} />
        <Rating label="Emotional control"  value={emotionCtrl} onChange={v => { setEmotionCtrl(v); markDirty(); }} />
      </div>

      {/* Tags */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 8, color: T.red, marginBottom: 2 }}>⚠ Mistakes</div>
        <TagToggle tags={mistakeTags} color={T.red}
          allTags={['ENTERED_EARLY','ENTERED_LATE','OVERTRADED','REVENGE_TRADE','EXITED_EARLY',
            'MOVED_STOP_TOO_SOON','WIDENED_STOP','OVERSIZED','IGNORED_BLOCKER',
            'TOOK_COUNTERTREND','MISSED_TARGET','MANUAL_INTERVENTION','BROKE_SESSION_RULE','OTHER']}
          onChange={setMistakeTags} />
      </div>
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 8, color: T.amber, marginBottom: 2 }}>🧠 Emotions</div>
        <TagToggle tags={emotionTags} color={T.amber}
          allTags={['ANXIETY','FEAR','FOMO','IMPATIENCE','FRUSTRATION','REVENGE',
            'OVERCONFIDENCE','HESITATION','CALM','DISCIPLINED','OTHER']}
          onChange={setEmotionTags} />
      </div>
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 8, color: T.green, marginBottom: 2 }}>✅ Positives</div>
        <TagToggle tags={positiveTags} color={T.green}
          allTags={['FOLLOWED_PLAN','WAITED_FOR_CONFIRMATION','RESPECTED_STOP',
            'LET_WINNER_RUN','GOOD_RISK_CONTROL','GOOD_PATIENCE','CLEAN_EXECUTION',
            'NO_INTERVENTION','OTHER']}
          onChange={setPositiveTags} />
      </div>

      {/* Manual override assessment (only when relevant) */}
      {hasManualOverride && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 3 }}>Override Assessment</div>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {['HELPFUL','HARMFUL','NEUTRAL','CANNOT_DETERMINE'].map(v => (
              <button key={v} onClick={() => { setOverrideAss(overrideAss === v ? '' : v); markDirty(); }}
                style={{ padding: '3px 8px', borderRadius: 4, fontSize: 8, cursor: 'pointer',
                  background: overrideAss === v ? T.amber + '22' : T.panelAlt,
                  border: `1px solid ${overrideAss === v ? T.amber + '44' : T.border}`,
                  color: overrideAss === v ? T.amber : T.txtMuted, fontWeight: overrideAss === v ? 700 : 400 }}>
                {v.replace(/_/g,' ')}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Notes and lesson */}
      <div style={{ marginBottom: 6 }}>
        <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 2 }}>Notes</div>
        <textarea value={notes} onChange={e => { setNotes(e.target.value); markDirty(); }} rows={3}
          placeholder="Operator notes…"
          style={{ width: '100%', background: T.panelAlt, border: `1px solid ${T.border}`,
            borderRadius: 4, color: T.txtSec, padding: '5px 7px', fontSize: 9, resize: 'vertical',
            boxSizing: 'border-box' }} />
      </div>
      <div style={{ marginBottom: 6 }}>
        <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 2 }}>Lesson Learned</div>
        <textarea value={lesson} onChange={e => { setLesson(e.target.value); markDirty(); }} rows={2}
          placeholder="Key takeaway…"
          style={{ width: '100%', background: T.panelAlt, border: `1px solid ${T.border}`,
            borderRadius: 4, color: T.txtSec, padding: '5px 7px', fontSize: 9, resize: 'vertical',
            boxSizing: 'border-box' }} />
      </div>
      <div style={{ marginBottom: 6 }}>
        <div style={{ fontSize: 8, color: T.green, marginBottom: 2 }}>What went well</div>
        <textarea value={wentWell} onChange={e => { setWentWell(e.target.value); markDirty(); }} rows={2}
          style={{ width: '100%', background: T.panelAlt, border: `1px solid ${T.border}`,
            borderRadius: 4, color: T.txtSec, padding: '5px 7px', fontSize: 9, resize: 'vertical',
            boxSizing: 'border-box' }} />
      </div>
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 8, color: T.amber, marginBottom: 2 }}>What to improve</div>
        <textarea value={toImprove} onChange={e => { setToImprove(e.target.value); markDirty(); }} rows={2}
          style={{ width: '100%', background: T.panelAlt, border: `1px solid ${T.border}`,
            borderRadius: 4, color: T.txtSec, padding: '5px 7px', fontSize: 9, resize: 'vertical',
            boxSizing: 'border-box' }} />
      </div>

      {/* Screenshots — upload, view, delete */}
      <NJScreenshotSection
        tradeId={detail.id}
        screenshots={detail.screenshots || []}
        onUpdated={updated => {
          onSaved({ screenshots: updated });
        }}
      />

      {/* Save button */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button onClick={handleSave} disabled={saveState === 'saving'}
          style={{ background: dirty ? T.cyan + '22' : T.panelAlt,
            border: `1px solid ${dirty ? T.cyan + '55' : T.border}`,
            borderRadius: 5, color: dirty ? T.cyan : T.txtMuted,
            padding: '6px 16px', fontSize: 10, cursor: saveState === 'saving' ? 'default' : 'pointer',
            fontWeight: 700 }}>
          {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? '✓ Saved' : 'Save Review'}
        </button>
        {dirty && saveState === 'idle' && (
          <span style={{ fontSize: 8, color: T.amber }}>Unsaved changes</span>
        )}
        {saveState === 'error' && (
          <span style={{ fontSize: 8, color: T.red }}>Save failed — try again</span>
        )}
      </div>
    </div>
  );
};

// ── NJ Review Queue Tab (Phase 7K-C) ─────────────────────────────────────────
const NJReviewQueueTab: React.FC<{ onOpenTrade: (id: string) => void }> = ({ onOpenTrade }) => {
  const [qData,    setQData]    = useState<null | { unreviewed_count: number; buckets: Record<string,number>; trades: Record<string,unknown>[] }>(null);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [fInst,    setFInst]    = useState('');
  const [fReview,  setFReview]  = useState('');

  const fetchQueue = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const params = new URLSearchParams();
      if (fInst)   params.set('instrument', fInst);
      if (fReview) params.set('review_status', fReview);
      const r = await fetch(`/api/journal/native-review-queue?${params}`, { headers: getAuthHeader() });
      const d = await r.json();
      if (d.db_ready === false) { setError('Native journal not ready'); return; }
      if (!d.ok) throw new Error(d.error || 'queue failed');
      setQData(d);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'fetch failed');
    } finally {
      setLoading(false);
    }
  }, [fInst, fReview]);

  useEffect(() => { fetchQueue(); }, [fetchQueue]);

  const bucketColor = (bucket: string) => {
    if (bucket === 'REVIEWED')    return T.green;
    if (bucket === 'EXCLUDED')    return T.red;
    if (bucket === 'IN_PROGRESS') return T.cyan;
    if (bucket === 'NEEDS_REVIEW') return T.amber;
    return T.txtMuted;
  };

  return (
    <div>
      {/* Summary buckets */}
      {qData && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          {Object.entries(qData.buckets).map(([bucket, count]) => (
            <div key={bucket} style={{ background: T.panelAlt, border: `1px solid ${T.border}`,
              borderRadius: 6, padding: '6px 12px', textAlign: 'center' }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: bucketColor(bucket), fontFamily: T.mono }}>
                {count as number}
              </div>
              <div style={{ fontSize: 8, color: T.txtMuted }}>{bucket.replace(/_/g,' ')}</div>
            </div>
          ))}
          {qData.unreviewed_count > 0 && (
            <div style={{ background: T.amber + '11', border: `1px solid ${T.amber}33`,
              borderRadius: 6, padding: '6px 12px', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 11, color: T.amber, fontWeight: 700 }}>
                {qData.unreviewed_count} pending review
              </span>
            </div>
          )}
        </div>
      )}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={fInst} onChange={e => setFInst(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Instruments</option>
          {['MGC','MNQ','MES','MYM'].map(i => <option key={i} value={i}>{i}</option>)}
        </select>
        <select value={fReview} onChange={e => setFReview(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Statuses</option>
          {['UNREVIEWED','IN_PROGRESS','REVIEWED','NEEDS_REVIEW','EXCLUDED'].map(s =>
            <option key={s} value={s}>{s.replace(/_/g,' ')}</option>)}
        </select>
        <button onClick={fetchQueue}
          style={{ background: T.cyan + '22', border: `1px solid ${T.cyan}44`, borderRadius: 4,
            color: T.cyan, padding: '4px 10px', fontSize: 11, cursor: 'pointer' }}>↺ Refresh</button>
      </div>

      {loading && <div style={{ color: T.txtMuted, fontSize: 11, padding: '8px 0' }}>Loading…</div>}
      {error && <div style={{ color: T.red, fontSize: 11, padding: '8px 0' }}>Error: {error}</div>}

      {qData && !loading && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
            <thead>
              <tr>
                {['Date','Inst','Dir','Strategy','Edge','Grade','Source','Review','Eligible'].map(h => (
                  <th key={h} style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                    paddingBottom: 6, fontSize: 9, paddingRight: 8, whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {qData.trades.map((t: Record<string,unknown>) => (
                <tr key={String(t['id'])}
                  onClick={() => onOpenTrade(String(t['id']))}
                  style={{ borderTop: `1px solid ${T.border}`, cursor: 'pointer' }}
                  onMouseEnter={e => (e.currentTarget.style.background = T.panelAlt)}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <td style={{ padding: '4px 8px 4px 0', color: T.txtMuted, fontSize: 9, whiteSpace: 'nowrap' }}>
                    {jFmtDate(String(t['created_at'] || ''))}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', color: T.cyan, fontFamily: T.mono, fontWeight: 700 }}>
                    {String(t['instrument'] || '—')}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', color: dirColor(String(t['direction'] || '')), fontWeight: 700 }}>
                    {String(t['direction'] || '—').slice(0,5).toUpperCase()}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', color: T.txtSec, maxWidth: 110,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {String(t['strategy_display_name'] || t['canonical_strategy_key'] || '—')}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', fontFamily: T.mono, fontWeight: 700,
                    color: t['edge_score'] != null
                      ? (Number(t['edge_score']) >= 70 ? T.green : Number(t['edge_score']) >= 50 ? T.cyan : T.txtMuted)
                      : T.txtMuted }}>
                    {t['edge_score'] != null ? Number(t['edge_score']).toFixed(0) : '—'}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', color: T.txtSec, fontFamily: T.mono }}>
                    {String(t['grade'] || '—')}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', fontSize: 8, color: T.txtMuted, whiteSpace: 'nowrap' }}>
                    {String(t['source_label'] || '—').replace(/_/g,'\u200B_')}
                  </td>
                  <td style={{ padding: '4px 4px 4px 0' }}>
                    {jReviewBadge(String(t['review_status'] || 'UNREVIEWED'))}
                  </td>
                  <td style={{ padding: '4px 0', fontSize: 8 }}>
                    {t['learning_eligible']
                      ? <span style={{ color: T.green, fontWeight: 700 }}>✓</span>
                      : <span style={{ color: T.txtMuted }}>—</span>}
                  </td>
                </tr>
              ))}
              {qData.trades.length === 0 && (
                <tr>
                  <td colSpan={9} style={{ textAlign: 'center', color: T.txtMuted, padding: '30px 0' }}>
                    <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 8, color: T.green }}>
                      ✓ All Reviewed
                    </div>
                    <div style={{ fontSize: 10 }}>No closed trades pending review.</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

// ── Native Journal Trades Tab (Phase 7K-A.2) ─────────────────────────────────
const JNativeTradesTab: React.FC<{ pendingOpenId?: string | null }> = ({ pendingOpenId }) => {
  const [trades,       setTrades]       = useState<NJTrade[]>([]);
  const [total,        setTotal]        = useState(0);
  const limit = 50;
  const [offset,       setOffset]       = useState(0);
  const [loading,      setLoading]      = useState(false);
  const [error,        setError]        = useState<string | null>(null);
  const [authFail,     setAuthFail]     = useState(false);
  const [dbReady,      setDbReady]      = useState<boolean | null>(null);

  // Filters — stable across drawer open/close
  const [search,      setSearch]      = useState('');
  const [fInst,       setFInst]       = useState('');
  const [fDir,        setFDir]        = useState('');
  const [fLifecycle,  setFLifecycle]  = useState('');
  const [fSource,     setFSource]     = useState('');
  const [fReview,     setFReview]     = useState('');
  const [fDateFrom,   setFDateFrom]   = useState('');
  const [fDateTo,     setFDateTo]     = useState('');

  // Detail drawer — opens without losing filter state
  const [detail,        setDetail]        = useState<NJTradeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError,   setDetailError]   = useState<string | null>(null);

  const fetchTrades = useCallback(async (off = 0) => {
    setLoading(true); setError(null); setAuthFail(false);
    try {
      const params = new URLSearchParams({ limit: String(limit), offset: String(off) });
      if (search)     params.set('search', search);
      if (fInst)      params.set('instrument', fInst);
      if (fDir)       params.set('direction', fDir);
      if (fLifecycle) params.set('lifecycle_status', fLifecycle);
      if (fSource)    params.set('source_label', fSource);
      if (fReview)    params.set('review_status', fReview);
      if (fDateFrom)  params.set('date_from', fDateFrom);
      if (fDateTo)    params.set('date_to', fDateTo);
      const r = await fetch(`/api/journal/native-trades?${params}`, { headers: getAuthHeader() });
      if (r.status === 401 || r.status === 403) { setAuthFail(true); return; }
      const d = await r.json();
      if (d.db_ready === false) { setDbReady(false); return; }
      if (!d.ok) throw new Error(d.error || 'query failed');
      setDbReady(true);
      setTrades(d.trades ?? []);
      setTotal(d.total ?? 0);
      setOffset(off);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'fetch failed');
    } finally {
      setLoading(false);
    }
  }, [search, fInst, fDir, fLifecycle, fSource, fReview, fDateFrom, fDateTo]);

  useEffect(() => { fetchTrades(0); }, [fetchTrades]);

  const openDetailById = useCallback(async (id: string) => {
    setDetail(null); setDetailError(null); setDetailLoading(true);
    try {
      const r = await fetch(`/api/journal/native-trades/${id}`, { headers: getAuthHeader() });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'not found');
      setDetail(d.trade);
    } catch (e: unknown) {
      setDetailError(e instanceof Error ? e.message : 'load failed');
    } finally {
      setDetailLoading(false);
    }
  }, []);

  // When the queue tab navigates to a specific trade, open its drawer immediately
  useEffect(() => {
    if (pendingOpenId) { openDetailById(pendingOpenId); }
  }, [pendingOpenId, openDetailById]);

  const openDetail = async (t: NJTrade) => { openDetailById(t.id); };

  function safeStr(v: unknown): string {
    if (v == null) return '—';
    if (typeof v === 'object') {
      try { return JSON.stringify(v); } catch { return '[object]'; }
    }
    return String(v);
  }

  const pages = Math.max(1, Math.ceil(total / limit));
  const page  = Math.floor(offset / limit) + 1;

  if (authFail) return (
    <div style={{ textAlign: 'center', padding: '40px 20px' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: T.amber, marginBottom: 8 }}>AUTH REQUIRED</div>
      <div style={{ fontSize: 10, color: T.txtMuted }}>Log in to view native journal records.</div>
    </div>
  );

  if (dbReady === false) return (
    <div style={{ textAlign: 'center', padding: '40px 20px' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: T.red, marginBottom: 8 }}>NATIVE JOURNAL UNAVAILABLE</div>
      <div style={{ fontSize: 10, color: T.txtMuted }}>
        The native_journal table is not ready. Apply the schema via Publish and restart.
      </div>
    </div>
  );

  return (
    <div style={{ position: 'relative' }}>
      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <input placeholder="Search…" value={search} onChange={e => setSearch(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtPri, padding: '4px 8px', fontSize: 11, width: 110 }} />
        <select value={fInst} onChange={e => setFInst(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Instruments</option>
          {['MGC','MNQ','MES','MYM'].map(i => <option key={i} value={i}>{i}</option>)}
        </select>
        <select value={fDir} onChange={e => setFDir(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Dir</option>
          <option value="long">Long</option>
          <option value="short">Short</option>
        </select>
        <select value={fLifecycle} onChange={e => setFLifecycle(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Lifecycle</option>
          {['SUBMITTED','ACKNOWLEDGED','ACTIVE','PARTIALLY_CLOSED','CLOSED',
            'REJECTED','CANCELED','STATUS_UNKNOWN','NEEDS_REVIEW'].map(s =>
            <option key={s} value={s}>{s.replace(/_/g,' ')}</option>)}
        </select>
        <select value={fSource} onChange={e => setFSource(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Sources</option>
          {['SYSTEM_AUTO','SYSTEM_MANUAL_CONFIRM','PAPER','SIMULATION',
            'EXTERNAL_MANUAL','TRADZELLA_IMPORT','LEGACY'].map(s =>
            <option key={s} value={s}>{s.replace(/_/g,' ')}</option>)}
        </select>
        <select value={fReview} onChange={e => setFReview(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Reviews</option>
          <option value="UNREVIEWED">Unreviewed</option>
          <option value="REVIEWED">Reviewed</option>
          <option value="EXCLUDED">Excluded</option>
        </select>
        <input type="date" value={fDateFrom} onChange={e => setFDateFrom(e.target.value)}
          title="From date"
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }} />
        <input type="date" value={fDateTo} onChange={e => setFDateTo(e.target.value)}
          title="To date"
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }} />
        <button onClick={() => fetchTrades(0)}
          style={{ background: T.cyan + '22', border: `1px solid ${T.cyan}44`, borderRadius: 4,
            color: T.cyan, padding: '4px 10px', fontSize: 11, cursor: 'pointer' }}>Search</button>
        <button onClick={() => fetchTrades(offset)} title="Refresh"
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtMuted, padding: '4px 8px', fontSize: 11, cursor: 'pointer' }}>↺</button>
      </div>

      {loading && <div style={{ color: T.txtMuted, fontSize: 11, padding: '8px 0' }}>Loading…</div>}
      {error && (
        <div style={{ color: T.red, fontSize: 11, padding: '8px 0', display: 'flex', gap: 8, alignItems: 'center' }}>
          Error: {error}
          <button onClick={() => fetchTrades(offset)}
            style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 3,
              color: T.txtSec, padding: '2px 8px', fontSize: 10, cursor: 'pointer' }}>Retry</button>
        </div>
      )}

      {!loading && !error && (
        <>
          <div style={{ fontSize: 10, color: T.txtMuted, marginBottom: 6 }}>
            {total} native trade{total !== 1 ? 's' : ''} · page {page}/{pages}
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
              <thead>
                <tr>
                  {['Date','Inst','Dir','Strategy','Lifecycle','Edge','Grade','Entry','Stop','Source','Review'].map(h => (
                    <th key={h} style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                      paddingBottom: 6, fontSize: 9, paddingRight: 8, whiteSpace: 'nowrap' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map(t => (
                  <tr key={t.id}
                    onClick={() => openDetail(t)}
                    style={{ borderTop: `1px solid ${T.border}`, cursor: 'pointer' }}
                    onMouseEnter={e => (e.currentTarget.style.background = T.panelAlt)}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                    <td style={{ padding: '4px 8px 4px 0', color: T.txtMuted, whiteSpace: 'nowrap', fontSize: 9 }}>
                      {jFmtDate(t.created_at)}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: T.cyan, fontFamily: T.mono, fontWeight: 700 }}>
                      {t.instrument || '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: dirColor(t.direction), fontWeight: 700 }}>
                      {t.direction ? t.direction.slice(0,5).toUpperCase() : '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: T.txtSec, maxWidth: 110,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.strategy_display_name || t.canonical_strategy_key || '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0' }}>
                      {jLifecycleBadge(t.lifecycle_status)}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', fontFamily: T.mono, fontWeight: 700,
                      color: t.edge_score != null
                        ? (t.edge_score >= 70 ? T.green : t.edge_score >= 50 ? T.cyan : T.txtMuted)
                        : T.txtMuted }}>
                      {t.edge_score != null ? t.edge_score.toFixed(0) : '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: T.txtSec, fontFamily: T.mono }}>
                      {t.grade || '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: T.txtSec, fontFamily: T.mono, fontSize: 9 }}>
                      {t.planned_entry != null ? t.planned_entry.toFixed(2) : '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: T.red, fontFamily: T.mono, fontSize: 9 }}>
                      {t.planned_stop != null ? t.planned_stop.toFixed(2) : '—'}
                    </td>
                    <td style={{ padding: '4px 4px 4px 0', fontSize: 8, color: T.txtMuted, whiteSpace: 'nowrap' }}>
                      {(t.source_label || '—').replace(/_/g, '\u200B_')}
                    </td>
                    <td style={{ padding: '4px 0' }}>
                      {jReviewBadge(t.review_status || 'UNREVIEWED')}
                    </td>
                  </tr>
                ))}
                {trades.length === 0 && (
                  <tr>
                    <td colSpan={11} style={{ textAlign: 'center', color: T.txtMuted, padding: '30px 0' }}>
                      <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 8, color: T.txtSec }}>
                        NO NATIVE TRADES YET
                      </div>
                      <div style={{ fontSize: 10 }}>
                        New platform trades will appear here automatically after they pass through the execution gateway.
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {pages > 1 && (
            <div style={{ display: 'flex', gap: 4, marginTop: 10, justifyContent: 'center' }}>
              <button onClick={() => fetchTrades(Math.max(0, offset - limit))} disabled={page <= 1}
                style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 3,
                  color: page <= 1 ? T.txtMuted : T.txtSec, padding: '3px 8px', fontSize: 10,
                  cursor: page <= 1 ? 'default' : 'pointer' }}>‹</button>
              {Array.from({ length: Math.min(pages, 7) }, (_, i) => {
                const p = Math.max(1, Math.min(pages - 6, page - 3)) + i;
                return (
                  <button key={p} onClick={() => fetchTrades((p - 1) * limit)}
                    style={{ background: p === page ? T.cyan + '33' : T.panelAlt,
                      border: `1px solid ${p === page ? T.cyan + '66' : T.border}`,
                      borderRadius: 3, color: p === page ? T.cyan : T.txtSec,
                      padding: '3px 7px', fontSize: 10, cursor: 'pointer' }}>
                    {p}
                  </button>
                );
              })}
              <button onClick={() => fetchTrades(Math.min((pages - 1) * limit, offset + limit))} disabled={page >= pages}
                style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 3,
                  color: page >= pages ? T.txtMuted : T.txtSec, padding: '3px 8px', fontSize: 10,
                  cursor: page >= pages ? 'default' : 'pointer' }}>›</button>
            </div>
          )}
        </>
      )}

      {/* Detail drawer — opens without resetting filters */}
      {(detailLoading || detail || detailError) && (
        <div style={{ position: 'fixed', right: 0, top: 0, bottom: 0, width: 360,
          background: T.panel, borderLeft: `1px solid ${T.borderMid}`,
          zIndex: 200, overflowY: 'auto', padding: 20,
          boxShadow: '-8px 0 24px #00000055' }}>
          <button onClick={() => { setDetail(null); setDetailError(null); }}
            style={{ position: 'absolute', top: 12, right: 16, background: 'none',
              border: 'none', color: T.txtMuted, fontSize: 18, cursor: 'pointer', lineHeight: 1 }}>
            ×
          </button>
          {detailLoading && (
            <div style={{ color: T.txtMuted, paddingTop: 40, textAlign: 'center' }}>Loading…</div>
          )}
          {detailError && (
            <div style={{ color: T.red, fontSize: 11, paddingTop: 40 }}>{detailError}</div>
          )}
          {detail && (
            <div style={{ paddingTop: 8 }}>
              {/* Header */}
              <div style={{ marginBottom: 14 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 18, fontWeight: 800, color: T.cyan, fontFamily: T.mono }}>
                    {detail.instrument || '—'}
                  </span>
                  <span style={{ color: dirColor(detail.direction), fontWeight: 700, fontSize: 13 }}>
                    {(detail.direction || '').toUpperCase()}
                  </span>
                  {jLifecycleBadge(detail.lifecycle_status)}
                </div>
                <div style={{ fontSize: 8, color: T.txtMuted, letterSpacing: '0.06em' }}>
                  {detail.source_label} · {jFmtDate(detail.created_at)}
                </div>
              </div>

              {/* ── PLANNED BY SYSTEM ── */}
              <div style={{ marginBottom: 14, background: T.cyan + '0a',
                border: `1px solid ${T.cyan}33`, borderRadius: 6, padding: '8px 10px' }}>
                <div style={{ fontSize: 8, fontWeight: 800, color: T.cyan,
                  letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 2 }}>
                  📋 Planned by System
                </div>
                <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 8, fontStyle: 'italic' }}>
                  Captured at trade submission — historical values cannot be edited.
                </div>
                {(([
                  ['Instrument',    detail.instrument],
                  ['Contract',      detail.contract],
                  ['Direction',     detail.direction?.toUpperCase()],
                  ['Strategy',      detail.strategy_display_name || detail.canonical_strategy_key],
                  ['Strategy Key',  detail.canonical_strategy_key],
                  ['Setup',         detail.setup_name],
                  ['Playbook',      detail.playbook],
                  ['Mode',          detail.mode],
                  ['Session',       detail.session],
                  ['Thesis Dir',    detail.thesis_direction],
                  ['Thesis Strength', detail.thesis_strength],
                  ['Alignment',     detail.thesis_alignment],
                  ['Edge Score',    detail.edge_score != null ? `${detail.edge_score.toFixed(0)}/110` : null],
                  ['Grade',         detail.grade],
                  ['Readiness',     detail.readiness],
                  ['Risk State',    detail.risk_state],
                  ['Opp Structure', detail.opposing_structure],
                  ['Planned Entry', detail.planned_entry != null ? detail.planned_entry.toFixed(4) : null],
                  ['Planned Stop',  detail.planned_stop  != null ? detail.planned_stop.toFixed(4)  : null],
                  ['Planned R:R',   detail.planned_rr    != null ? `1:${detail.planned_rr.toFixed(1)}` : null],
                  ['Planned Risk',  detail.planned_risk  != null ? `$${detail.planned_risk.toFixed(0)}` : null],
                  ['Contracts',     detail.planned_contracts != null ? String(detail.planned_contracts) : null],
                  ['Decision At',   detail.decision_timestamp ? jFmtDate(detail.decision_timestamp) : null],
                  ['Mkt Data At',   detail.market_data_timestamp ? jFmtDate(detail.market_data_timestamp) : null],
                ] as [string, string | null | undefined][])).map(([lbl, val]) => val ? (
                  <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between',
                    padding: '2px 0', borderBottom: `1px solid ${T.border}22` }}>
                    <span style={{ color: T.txtMuted, fontSize: 9, flexShrink: 0, paddingRight: 8 }}>{lbl}</span>
                    <span style={{ color: T.txtSec, fontSize: 9, textAlign: 'right',
                      maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {safeStr(val)}
                    </span>
                  </div>
                ) : null)}
                {detail.confirmations != null && (
                  <div style={{ marginTop: 4, fontSize: 8 }}>
                    <span style={{ color: T.txtMuted }}>Confirmations: </span>
                    <span style={{ color: T.txtSec }}>{safeStr(detail.confirmations)}</span>
                  </div>
                )}
                {detail.blockers != null && (
                  <div style={{ marginTop: 2, fontSize: 8 }}>
                    <span style={{ color: T.txtMuted }}>Blockers: </span>
                    <span style={{ color: T.red }}>{safeStr(detail.blockers)}</span>
                  </div>
                )}
              </div>

              {/* ── EXECUTION ── */}
              <div style={{ marginBottom: 14, background: T.panelAlt,
                border: `1px solid ${T.border}`, borderRadius: 6, padding: '8px 10px' }}>
                <div style={{ fontSize: 8, fontWeight: 800, color: T.txtSec,
                  letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>
                  ⚡ Execution
                </div>
                {(([
                  ['Lifecycle',       detail.lifecycle_status],
                  ['Source Label',    detail.source_label],
                  ['Signal ID',       detail.signal_id],
                  ['Broker Order ID', detail.broker_order_id],
                  ['TradersPost ID',  detail.traderspost_id],
                  ['Submit Time',     detail.execution?.['submission_time'] != null
                    ? safeStr(detail.execution['submission_time']) : null],
                  ['Ack Time',        detail.execution?.['ack_time'] != null
                    ? safeStr(detail.execution['ack_time']) : null],
                  ['Actual Qty',      detail.execution?.['actual_qty'] != null
                    ? safeStr(detail.execution['actual_qty']) : null],
                  ['Avg Entry',       detail.execution?.['avg_entry'] != null
                    ? safeStr(detail.execution['avg_entry']) : null],
                  ['Reject Reason',   detail.execution?.['rejected_reason'] != null
                    ? safeStr(detail.execution['rejected_reason']) : null],
                  ['Timeout Status',  detail.execution?.['timeout_status'] != null
                    ? safeStr(detail.execution['timeout_status']) : null],
                ] as [string, string | null | undefined][])).map(([lbl, val]) => val ? (
                  <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between',
                    padding: '2px 0', borderBottom: `1px solid ${T.border}22` }}>
                    <span style={{ color: T.txtMuted, fontSize: 9 }}>{lbl}</span>
                    <span style={{ color: T.txtSec, fontSize: 9, textAlign: 'right',
                      maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>{val}</span>
                  </div>
                ) : null)}
                {detail.outcome && Object.keys(detail.outcome).length > 0 && (
                  <div style={{ marginTop: 6, paddingTop: 6, borderTop: `1px solid ${T.border}33` }}>
                    <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 2 }}>OUTCOME</div>
                    {Object.entries(detail.outcome).map(([k, v]) => v != null ? (
                      <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '1px 0' }}>
                        <span style={{ color: T.txtMuted, fontSize: 8 }}>{k}</span>
                        <span style={{ color: T.txtSec, fontSize: 8 }}>{safeStr(v)}</span>
                      </div>
                    ) : null)}
                  </div>
                )}
              </div>

              {/* ── CURRENT RECORD STATUS ── */}
              <div style={{ marginBottom: 14, background: T.panelAlt, border: `1px solid ${T.border}`,
                borderRadius: 6, padding: '8px 10px' }}>
                <div style={{ fontSize: 8, fontWeight: 800, color: T.txtSec,
                  letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>
                  📁 Current Record Status
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
                  {jReviewBadge(detail.review_status || 'UNREVIEWED')}
                  <span style={{ fontSize: 8, color: T.txtMuted }}>Review</span>
                  {detail.learning_eligible
                    ? <span style={{ background: T.green + '22', color: T.green, borderRadius: 3,
                        padding: '1px 5px', fontSize: 8, fontWeight: 700 }}>✓ Learning Eligible</span>
                    : <span style={{ background: T.red + '11', color: T.txtMuted, borderRadius: 3,
                        padding: '1px 5px', fontSize: 8 }}>Not Eligible</span>
                  }
                </div>
                {detail.learning_blocked_reason && (
                  <div style={{ fontSize: 8, color: T.txtMuted, marginBottom: 4 }}>
                    Blocked: {detail.learning_blocked_reason}
                  </div>
                )}
                {(([
                  ['Created',    jFmtDate(detail.created_at)],
                  ['Updated',    jFmtDate(detail.updated_at)],
                  ['TZ Link',    detail.tradezella_trade_id != null ? String(detail.tradezella_trade_id) : null],
                  ['Legacy Key', detail.legacy_journal_key],
                ] as [string, string | null | undefined][])).map(([lbl, val]) => val && val !== '—' ? (
                  <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between',
                    padding: '2px 0', borderBottom: `1px solid ${T.border}22` }}>
                    <span style={{ color: T.txtMuted, fontSize: 9 }}>{lbl}</span>
                    <span style={{ color: T.txtSec, fontSize: 9 }}>{val}</span>
                  </div>
                ) : null)}
              </div>

              {/* ── OPERATOR REVIEW — EDITABLE ── */}
              <NJReviewSection detail={detail} onSaved={updated => {
                setDetail(d => d ? { ...d, ...updated } : d);
                fetchTrades(offset);
              }} />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ── Journal Trades Tab ────────────────────────────────────────────────────────
const JTradesTab: React.FC<{
  drillFilter?: JDrillFilter | null;
  onClearDrill?: () => void;
  onClearOneDrill?: (key: keyof JDrillFilter) => void;
  onGoCoaching?: () => void;
}> = ({ drillFilter, onClearDrill, onClearOneDrill, onGoCoaching }) => {
  const [trades, setTrades] = useState<JTrade[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [search, setSearch] = useState('');
  const [fInst, setFInst] = useState('');
  const [fDir, setFDir] = useState('');
  const [fSrc, setFSrc] = useState('');
  const [fRes, setFRes] = useState('');
  const [sortCol, setSortCol] = useState('date');
  const [sortOrd, setSortOrd] = useState<'asc' | 'desc'>('desc');

  // Detail
  const [detail, setDetail] = useState<JTradeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [noteEdit, setNoteEdit] = useState('');
  const [noteSaving, setNoteSaving] = useState(false);

  // Phase 7N: review state
  const [reviewTrade, setReviewTrade]   = useState<{ source: string; id: number; detail: JTradeDetail } | null>(null);
  const [queueCount,  setQueueCount]    = useState<number | null>(null);
  const [queueLoading, setQueueLoading] = useState(false);

  // Phase 7N Batch C: per-trade learning eligibility (display-only cache)
  const [eligibilityMap, setEligibilityMap] = useState<Record<string, { status: string; reason: string }>>({});

  // Phase 7K-A.2 — source selector (NATIVE / QUEUE / TRADZELLA / LEGACY) + live counts
  const [jSrc,     setJSrc]     = useState<'native' | 'queue' | 'tradzella' | 'legacy'>('native');
  const [njCounts, setNjCounts] = useState<{ native: number; tradzella: number; legacy: number } | null>(null);
  // When queue tab opens a trade row, we store the UUID here and switch to Native tab.
  // JNativeTradesTab reads the prop on mount and opens the drawer immediately.
  const [pendingNativeId, setPendingNativeId] = useState<string | null>(null);

  const fetchTrades = useCallback(async (pg = 1) => {
    setLoading(true); setError(null);
    try {
      const params = new URLSearchParams({
        page: String(pg), limit: '50',
        sort: sortCol, order: sortOrd,
        ...(search    ? { search }               : {}),
        ...(fInst     ? { instrument: fInst }    : {}),
        ...(fDir      ? { direction: fDir }      : {}),
        ...(fSrc      ? { source: fSrc }         : {}),
        ...(fRes      ? { result: fRes }         : {}),
      });
      // Phase 7O.1: merge drill-down filter params (server-side AND semantics)
      if (drillFilter) {
        const df = drillFilter;
        if (df.review_status) params.set('review_status', df.review_status);
        if (df.mistake_tag)   params.set('mistake_tag',   df.mistake_tag);
        if (df.positive_tag)  params.set('positive_tag',  df.positive_tag);
        if (df.emotion_tag)   params.set('emotion_tag',   df.emotion_tag);
        if (df.followed_plan) params.set('followed_plan', df.followed_plan);
        if (df.strategy)      params.set('strategy',      df.strategy);
        if (df.session)       params.set('session',       df.session);
        if (df.instrument && !fInst) params.set('instrument', df.instrument);
        if (df.mode)          params.set('mode',          df.mode);
        if (df.source && !fSrc)      params.set('source',     df.source);
        if (df.date_from)     params.set('date_from',     df.date_from);
        if (df.date_to)       params.set('date_to',       df.date_to);
        if (df.rating_field)  params.set('rating_field',  df.rating_field);
        if (df.rating_value != null) params.set('rating_value', String(df.rating_value));
        if (df.result && !fRes)      params.set('result',      df.result);
        // Phase 7O.2: intraday block filter
        if (df.entry_block_start) params.set('entry_block_start', df.entry_block_start);
        if (df.entry_block_end)   params.set('entry_block_end',   df.entry_block_end);
        if (df.display_timezone)  params.set('display_timezone',  df.display_timezone);
      }
      const r = await fetch(`/api/journal/trades?${params}`, { headers: getAuthHeader() });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'fetch failed');
      setTrades(data.trades || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
      setPage(pg);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [search, fInst, fDir, fSrc, fRes, sortCol, sortOrd, drillFilter]);

  useEffect(() => { fetchTrades(1); }, [fetchTrades]);

  // Phase 7N Batch C: load the per-trade eligibility map.
  // This is display-only, but it must be refreshable after a review save so
  // the badge reflects the server's current review status without a reload.
  const refreshEligibility = useCallback(async () => {
    try {
      const r = await fetch('/api/journal/learning-eligibility', { headers: getAuthHeader() });
      const d = await r.json();
      if (d.ok && d.records) {
        const m: Record<string, { status: string; reason: string }> = {};
        for (const rec of d.records as { source: string; trade_id: number; status: string; reason: string }[]) {
          m[`${rec.source}-${rec.trade_id}`] = { status: rec.status, reason: rec.reason };
        }
        setEligibilityMap(m);
      }
    } catch { /* fail silently — badge is display-only */ }
  }, []);

  useEffect(() => { refreshEligibility(); }, [refreshEligibility]);

  // Phase 7K-A.2 — fetch source counts on mount (fail-open; display-only)
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch('/api/journal/native-counts', { headers: getAuthHeader() });
        const d = await r.json();
        if (d.ok) setNjCounts({ native: d.native ?? 0, tradzella: d.tradzella ?? 0, legacy: d.legacy ?? 0 });
      } catch { /* fail silently */ }
    })();
  }, []);

  const handleSort = (col: string) => {
    if (col === sortCol) {
      setSortOrd(o => o === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col); setSortOrd('desc');
    }
  };

  const openDetail = async (t: JTrade) => {
    setDetail(null); setDetailLoading(true);
    try {
      const r = await fetch(`/api/journal/trade/${t.source}/${t.id}`, { headers: getAuthHeader() });
      const data = await r.json();
      if (data.ok) { setDetail(data.trade); setNoteEdit(data.trade.notes || ''); }
    } catch { /* ignore */ }
    setDetailLoading(false);
  };

  // Phase 7N helpers
  const fetchQueueCount = useCallback(async () => {
    setQueueLoading(true);
    try {
      const r = await fetch('/api/journal/review-queue', { headers: getAuthHeader() });
      const d = await r.json();
      if (d.ok) setQueueCount(d.unreviewed_count ?? 0);
    } catch { /* ignore */ }
    setQueueLoading(false);
  }, []);

  useEffect(() => { fetchQueueCount(); }, [fetchQueueCount]);

  const openReviewNext = async () => {
    setQueueLoading(true);
    try {
      const r = await fetch('/api/journal/review-queue', { headers: getAuthHeader() });
      const d = await r.json();
      if (d.ok && d.next_trade) {
        const { id, source: src } = d.next_trade;
        setQueueCount(d.unreviewed_count);
        // fetch the detail for that trade
        const rd = await fetch(`/api/journal/trade/${src}/${id}`, { headers: getAuthHeader() });
        const dd = await rd.json();
        if (dd.ok) {
          setReviewTrade({ source: src, id, detail: dd.trade });
        }
      }
    } catch { /* ignore */ }
    setQueueLoading(false);
  };

  const openReviewForTrade = (d: JTradeDetail) =>
    setReviewTrade({ source: d.source, id: d.id, detail: d });

  const onReviewSaved = (newStatus: string) => {
    // Update the review_status in the local trades list without re-fetching
    if (reviewTrade) {
      setTrades(prev => prev.map(t =>
        t.source === reviewTrade.source && t.id === reviewTrade.id
          ? { ...t, review_status: newStatus }
          : t
      ));
    }
    fetchQueueCount();
    refreshEligibility();
  };

  const saveNote = async () => {
    if (!detail || detail.source !== 'tradzella') return;
    setNoteSaving(true);
    try {
      await fetch(`/api/journal/trade/${detail.source}/${detail.id}/notes`, {
        method: 'PATCH',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes: noteEdit }),
      });
      setDetail(d => d ? { ...d, notes: noteEdit } : d);
    } catch { /* ignore */ }
    setNoteSaving(false);
  };

  const sortIcon = (col: string) =>
    sortCol === col ? (sortOrd === 'asc' ? ' ↑' : ' ↓') : '';

  const Th: React.FC<{ col: string; label: string; style?: React.CSSProperties }> = ({ col, label, style }) => (
    <th onClick={() => handleSort(col)} style={{
      textAlign: 'left', color: sortCol === col ? T.cyan : T.txtMuted,
      fontWeight: 600, paddingBottom: 6, fontSize: 9, letterSpacing: '0.07em',
      cursor: 'pointer', whiteSpace: 'nowrap', userSelect: 'none', ...style,
    }}>
      {label}{sortIcon(col)}
    </th>
  );

  // Compute drill chips once for rendering
  const drillChips = drillFilter ? _drillChips(drillFilter) : [];

  return (
    <div>
      {/* Phase 7K-A.2 / 7K-C — Source selector: NATIVE / QUEUE / TRADZELLA / LEGACY */}
      <div style={{ display: 'flex', gap: 0, marginBottom: 12,
        background: T.panelAlt, borderRadius: 6, padding: 3,
        border: `1px solid ${T.border}`, alignSelf: 'flex-start', width: 'fit-content' }}>
        {(['native', 'queue', 'tradzella', 'legacy'] as const).map(src => {
          const labels: Record<string, string> = {
            native: 'Native', queue: 'Review Queue', tradzella: 'Tradzella', legacy: 'Legacy',
          };
          const count = src === 'queue' ? (queueCount ?? null) : (njCounts ? (njCounts as Record<string,number>)[src] ?? null : null);
          const active = jSrc === src;
          const accent = src === 'queue' ? T.amber : T.cyan;
          return (
            <button key={src} onClick={() => setJSrc(src)}
              style={{ background: active ? accent + '22' : 'transparent',
                border: `1px solid ${active ? accent + '55' : 'transparent'}`,
                borderRadius: 4, color: active ? accent : T.txtMuted,
                padding: '4px 12px', fontSize: 10, cursor: 'pointer',
                fontWeight: active ? 700 : 400, whiteSpace: 'nowrap',
                display: 'flex', alignItems: 'center', gap: 5 }}>
              {labels[src]}
              {count != null && count > 0 && (
                <span style={{ background: active ? accent + '33' : T.border + '88',
                  color: active ? accent : T.txtMuted,
                  borderRadius: 10, padding: '0 5px', fontSize: 8, fontWeight: 700 }}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Native tab renders its own self-contained component */}
      {jSrc === 'native' && <JNativeTradesTab pendingOpenId={pendingNativeId} />}

      {/* Review Queue tab (Phase 7K-C) */}
      {jSrc === 'queue' && <NJReviewQueueTab onOpenTrade={(id: string) => {
        // Switch to Native tab and open the drawer for this specific trade immediately.
        // pendingNativeId is read by JNativeTradesTab on mount via useEffect.
        setPendingNativeId(id);
        setJSrc('native');
      }} />}

      {/* Tradzella / Legacy use the existing tab content — NOT rendered for Native or Queue */}
      {(jSrc === 'tradzella' || jSrc === 'legacy') && (
      <React.Fragment>
      {/* Phase 7O.1: Evidence banner — shown when drilling from a coaching insight */}
      {drillFilter && (
        <div style={{ background: T.cyan + '11', border: `1px solid ${T.cyan}44`,
          borderRadius: 8, padding: '10px 14px', marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            flexWrap: 'wrap', gap: 8 }}>
            <div>
              <span style={{ fontSize: 8, color: T.cyan, fontWeight: 800, letterSpacing: '0.08em',
                textTransform: 'uppercase' }}>Evidence View</span>
              <span style={{ fontSize: 10, color: T.txtSec, marginLeft: 10, fontWeight: 600 }}>
                {drillFilter.label}
              </span>
              {drillFilter.count != null && (
                <span style={{ fontSize: 9, color: T.txtMuted, marginLeft: 8 }}>
                  · {drillFilter.count} reviewed trades
                </span>
              )}
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              {onGoCoaching && (
                <button onClick={onGoCoaching}
                  style={{ background: 'transparent', border: `1px solid ${T.border}`,
                    borderRadius: 4, color: T.txtMuted, padding: '3px 10px', fontSize: 9,
                    cursor: 'pointer' }}>
                  ← Back to Coaching
                </button>
              )}
              {onClearDrill && (
                <button onClick={onClearDrill}
                  style={{ background: T.red + '22', border: `1px solid ${T.red}44`,
                    borderRadius: 4, color: T.red, padding: '3px 10px', fontSize: 9,
                    cursor: 'pointer' }}>
                  Clear filter
                </button>
              )}
            </div>
          </div>
          {/* Filter chips */}
          {drillChips.length > 0 && (
            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 8 }}>
              {drillChips.map(chip => (
                <span key={String(chip.key)} style={{
                  background: T.panelAlt, border: `1px solid ${T.border}`,
                  borderRadius: 12, padding: '2px 8px', fontSize: 8,
                  color: T.txtSec, display: 'flex', alignItems: 'center', gap: 4,
                }}>
                  {chip.label}
                  {onClearOneDrill && (
                    <button
                      onClick={() => onClearOneDrill(chip.key)}
                      aria-label={`Remove filter ${chip.label}`}
                      style={{ background: 'none', border: 'none', cursor: 'pointer',
                        color: T.txtMuted, padding: '0 1px', lineHeight: 1, fontSize: 9 }}>
                      ×
                    </button>
                  )}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Phase 7N: Review Next Trade bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10,
        background: T.panelAlt, borderRadius: 6, padding: '8px 12px',
        border: `1px solid ${T.border}` }}>
        <button onClick={openReviewNext} disabled={queueLoading || queueCount === 0}
          style={{ background: queueCount ? T.cyan + '22' : T.panelAlt,
            border: `1px solid ${queueCount ? T.cyan + '55' : T.border}`,
            borderRadius: 5, color: queueCount ? T.cyan : T.txtMuted,
            padding: '5px 14px', fontSize: 10, cursor: (queueLoading || queueCount === 0) ? 'default' : 'pointer',
            fontWeight: 700, whiteSpace: 'nowrap' }}>
          {queueLoading ? 'Loading…' : queueCount === 0 ? '✓ All Reviewed' : `▶ Review Next Trade`}
        </button>
        {queueCount !== null && queueCount > 0 && (
          <span style={{ fontSize: 9, color: T.amber, fontWeight: 700 }}>
            {queueCount} unreviewed
          </span>
        )}
        {queueCount === 0 && (
          <span style={{ fontSize: 9, color: T.green }}>All closed trades reviewed</span>
        )}
      </div>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <input placeholder="Search…" value={search} onChange={e => setSearch(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtPri, padding: '4px 8px', fontSize: 11, width: 130 }} />
        <select value={fInst} onChange={e => setFInst(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Instruments</option>
          {['MGC','MNQ','MES','MYM'].map(i => <option key={i} value={i}>{i}</option>)}
        </select>
        <select value={fDir} onChange={e => setFDir(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Directions</option>
          <option value="long">Long</option>
          <option value="short">Short</option>
        </select>
        <select value={fSrc} onChange={e => setFSrc(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Sources</option>
          <option value="system">System</option>
          <option value="tradzella">Tradzella</option>
        </select>
        <select value={fRes} onChange={e => setFRes(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }}>
          <option value="">All Results</option>
          <option value="win">Win</option>
          <option value="loss">Loss</option>
          <option value="scratch">Scratch</option>
        </select>
        <button onClick={() => fetchTrades(1)} style={{
          background: T.cyan + '22', border: `1px solid ${T.cyan}44`, borderRadius: 4,
          color: T.cyan, padding: '4px 10px', fontSize: 11, cursor: 'pointer' }}>
          Search
        </button>
      </div>

      {loading && <div style={{ color: T.txtMuted, fontSize: 11, padding: '8px 0' }}>Loading…</div>}
      {error && <div style={{ color: T.red, fontSize: 11, padding: '8px 0' }}>Error: {error}</div>}

      {!loading && !error && (
        <>
          <div style={{ fontSize: 10, color: T.txtMuted, marginBottom: 6 }}>
            {total} trade{total !== 1 ? 's' : ''} · page {page}/{pages}
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
              <thead>
                <tr>
                  <Th col="date"       label="Date" />
                  <Th col="instrument" label="Inst" />
                  <Th col="direction"  label="Dir" />
                  <Th col="source"     label="Src"  style={{ fontSize: 8 }} />
                  <th style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                    paddingBottom: 6, fontSize: 9 }}>Strategy</th>
                  <Th col="result"     label="Result" />
                  <Th col="r_multiple" label="R" />
                  <Th col="pnl"        label="P&L" />
                  <Th col="duration_min" label="Dur" />
                  <th style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                    paddingBottom: 6, fontSize: 9 }}>Review</th>
                  <th style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                    paddingBottom: 6, fontSize: 9 }}>Src Type</th>
                  <th style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                    paddingBottom: 6, fontSize: 9 }}>Match</th>
                  <th style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                    paddingBottom: 6, fontSize: 9 }}>Eligibility</th>
                </tr>
              </thead>
              <tbody>
                {trades.map(t => {
                  const eligKey = `${t.source}-${t.id}`;
                  const elig = eligibilityMap[eligKey];
                  return (
                  <tr key={eligKey}
                    onClick={() => openDetail(t)}
                    style={{ borderTop: `1px solid ${T.border}`, cursor: 'pointer' }}
                    onMouseEnter={e => (e.currentTarget.style.background = T.panelAlt)}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                    <td style={{ padding: '4px 8px 4px 0', color: T.txtMuted, whiteSpace: 'nowrap', fontSize: 9 }}>
                      {jFmtDate(t.date)}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: T.cyan, fontFamily: T.mono, fontWeight: 700 }}>
                      {t.instrument || '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: dirColor(t.direction) }}>
                      {(t.direction || '—').slice(0,5).toUpperCase()}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', fontSize: 8, color: T.txtMuted }}>
                      {t.source === 'system' ? 'SYS' : 'TZ'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: T.txtSec, maxWidth: 100,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.strategy_name || '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0' }}>
                      {jResultBadge(t.result)}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: jRCol(t.r_multiple), fontFamily: T.mono, fontWeight: 700 }}>
                      {t.r_multiple != null ? (t.r_multiple >= 0 ? '+' : '') + t.r_multiple.toFixed(2) : '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: t.pnl != null ? (t.pnl >= 0 ? T.green : T.red) : T.txtMuted, fontFamily: T.mono }}>
                      {t.pnl != null ? (t.pnl >= 0 ? '+$' : '-$') + Math.abs(t.pnl).toFixed(0) : '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0', color: T.txtMuted, fontFamily: T.mono, fontSize: 9 }}>
                      {t.duration_min != null ? t.duration_min.toFixed(0) + 'm' : '—'}
                    </td>
                    <td style={{ padding: '4px 8px 4px 0' }}>
                      {jReviewBadge(t.review_status || 'UNREVIEWED')}
                    </td>
                    <td style={{ padding: '4px 0' }}>
                      {elig
                        ? jEligibilityBadge(elig.status, elig.reason)
                        : <span style={{ fontSize: 8, color: T.txtMuted }}>—</span>
                      }
                    </td>
                  </tr>
                  );
                })}
                {trades.length === 0 && (
                  <tr><td colSpan={11} style={{ textAlign: 'center', color: T.txtMuted,
                    padding: '20px 0', fontSize: 11 }}>
                    {drillFilter
                      ? (
                        <div>
                          <div style={{ fontWeight: 700, color: T.amber, marginBottom: 6 }}>
                            NO MATCHING TRADES
                          </div>
                          <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 8 }}>
                            Filters applied: {drillChips.map(c => c.label).join(' · ') || 'none'}
                          </div>
                          {onClearDrill && (
                            <button onClick={onClearDrill}
                              style={{ background: T.panelAlt, border: `1px solid ${T.border}`,
                                borderRadius: 4, color: T.txtSec, padding: '4px 12px',
                                fontSize: 9, cursor: 'pointer' }}>
                              Clear filters
                            </button>
                          )}
                        </div>
                      )
                      : 'No trades'
                    }
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {pages > 1 && (
            <div style={{ display: 'flex', gap: 4, marginTop: 10, justifyContent: 'center' }}>
              <button onClick={() => fetchTrades(Math.max(1, page - 1))} disabled={page <= 1}
                style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 3,
                  color: page <= 1 ? T.txtMuted : T.txtSec, padding: '3px 8px', fontSize: 10,
                  cursor: page <= 1 ? 'default' : 'pointer' }}>‹</button>
              {Array.from({ length: Math.min(pages, 7) }, (_, i) => {
                const p = Math.max(1, Math.min(pages - 6, page - 3)) + i;
                return (
                  <button key={p} onClick={() => fetchTrades(p)}
                    style={{ background: p === page ? T.cyan + '33' : T.panelAlt,
                      border: `1px solid ${p === page ? T.cyan + '66' : T.border}`,
                      borderRadius: 3, color: p === page ? T.cyan : T.txtSec,
                      padding: '3px 7px', fontSize: 10, cursor: 'pointer' }}>
                    {p}
                  </button>
                );
              })}
              <button onClick={() => fetchTrades(Math.min(pages, page + 1))} disabled={page >= pages}
                style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 3,
                  color: page >= pages ? T.txtMuted : T.txtSec, padding: '3px 8px', fontSize: 10,
                  cursor: page >= pages ? 'default' : 'pointer' }}>›</button>
            </div>
          )}
        </>
      )}

      {/* Detail slide-in */}
      {(detail || detailLoading) && (
        <div style={{ position: 'fixed', right: 0, top: 0, bottom: 0, width: 340,
          background: T.panel, borderLeft: `1px solid ${T.borderMid}`,
          zIndex: 200, overflowY: 'auto', padding: 20, boxShadow: '-8px 0 24px #00000055' }}>
          <button onClick={() => setDetail(null)}
            style={{ position: 'absolute', top: 12, right: 16, background: 'none',
              border: 'none', color: T.txtMuted, fontSize: 18, cursor: 'pointer', lineHeight: 1 }}>
            ×
          </button>
          {detailLoading && <div style={{ color: T.txtMuted, paddingTop: 40, textAlign: 'center' }}>Loading…</div>}
          {detail && (
            <>
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 4, display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span>{detail.source === 'system' ? 'SYSTEM TRADE' : 'TRADZELLA IMPORT'}</span>
                  {detail.source === 'tradzella' && detail.match_confidence && (
                    <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 8, fontWeight: 700,
                      background: detail.match_confidence === 'EXACT'     ? T.green + '22' :
                                  detail.match_confidence === 'HIGH'      ? T.cyan  + '22' :
                                  detail.match_confidence === 'UNMATCHED' ? T.red   + '22' :
                                  detail.match_confidence === 'AMBIGUOUS' ? T.red   + '22' : T.panelAlt,
                      color:      detail.match_confidence === 'EXACT'     ? T.green :
                                  detail.match_confidence === 'HIGH'      ? T.cyan  :
                                  detail.match_confidence === 'UNMATCHED' ? T.red   :
                                  detail.match_confidence === 'AMBIGUOUS' ? T.red   : T.txtMuted }}>
                      {detail.match_confidence}
                    </span>
                  )}
                  {detail.source === 'tradzella' && detail.strategy_source && (
                    <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 8, fontWeight: 700,
                      background: detail.strategy_source === 'SYSTEM' ? T.cyan  + '22' :
                                  detail.strategy_source === 'MANUAL' ? T.amber + '22' : T.panelAlt,
                      color:      detail.strategy_source === 'SYSTEM' ? T.cyan  :
                                  detail.strategy_source === 'MANUAL' ? T.amber : T.txtMuted }}>
                      {detail.strategy_source}
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 16, fontWeight: 800, color: T.cyan, fontFamily: T.mono }}>
                    {detail.symbol || detail.instrument || '—'}
                  </span>
                  <span style={{ color: dirColor(detail.direction), fontWeight: 700, fontSize: 12 }}>
                    {(detail.direction || '').toUpperCase()}
                  </span>
                  {jResultBadge(detail.result || '')}
                </div>
              </div>

              {/* Task 83: PLANNED BY SYSTEM block — shown for matched TZ trades */}
              {detail.source === 'tradzella' && (detail.system_strategy || detail.system_strategy_key || detail.system_edge_score != null || detail.system_planned_entry != null) && (
                <div style={{ marginBottom: 14, background: T.cyan + '0a', border: `1px solid ${T.cyan}33`,
                  borderRadius: 6, padding: '8px 10px' }}>
                  <div style={{ fontSize: 8, fontWeight: 800, color: T.cyan, letterSpacing: '0.08em',
                    textTransform: 'uppercase', marginBottom: 6 }}>📋 Planned by System</div>
                  <div style={{ fontSize: 10 }}>
                    {([
                      ['Strategy', detail.system_strategy],
                      ['Strategy Key', detail.system_strategy_key],
                      ['Thesis', detail.system_thesis_direction && detail.system_thesis_strength
                        ? `${detail.system_thesis_direction} (${detail.system_thesis_strength})` : null],
                      ['Alignment', detail.snap_thesis_alignment],
                      ['Edge Score', detail.system_edge_score != null ? `${detail.system_edge_score}/110` : null],
                      ['Grade', detail.system_grade],
                      ['Planned Entry', detail.system_planned_entry != null ? detail.system_planned_entry.toFixed(2) : null],
                      ['Planned Stop', detail.system_planned_stop != null ? detail.system_planned_stop.toFixed(2) : null],
                      ['Planned Risk', detail.system_planned_risk != null ? `$${detail.system_planned_risk.toFixed(0)}` : null],
                    ] as [string, string | null | undefined][]).map(([lbl, val]) => val != null && (
                      <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between',
                        padding: '2px 0', borderBottom: `1px solid ${T.border}22` }}>
                        <span style={{ color: T.txtMuted, fontSize: 9 }}>{lbl}</span>
                        <span style={{ color: T.txtSec }}>{String(val)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {/* Unmatched banner for TZ trades without a snapshot match */}
              {detail.source === 'tradzella' && !detail.system_strategy && !detail.system_strategy_key && detail.system_edge_score == null && (
                <div style={{ marginBottom: 14, background: T.amber + '11', border: `1px solid ${T.amber}44`,
                  borderRadius: 6, padding: '8px 10px' }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: T.amber, marginBottom: 2 }}>
                    ⚠ No system snapshot — {detail.match_confidence ?? 'not yet matched'}
                  </div>
                  <div style={{ fontSize: 8, color: T.txtMuted }}>
                    Use the Review Queue tab to assign strategy attribution and unlock learning eligibility.
                  </div>
                </div>
              )}

              {/* Entry / Exit / R */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 14 }}>
                {([
                  ['Entry', detail.entry],
                  ['Exit/Target', detail.exit ?? detail.target],
                  ['R', detail.r_multiple],
                ] as [string, number | null | undefined][]).map(([lbl, val]) => (
                  <div key={lbl} style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 13, fontWeight: 800, fontFamily: T.mono,
                      color: lbl === 'R' ? jRCol(val ?? null) : T.txtPri }}>
                      {val != null ? (lbl === 'R' ? (val >= 0 ? '+' : '') + val.toFixed(2) + 'R' : val.toFixed(4)) : '—'}
                    </div>
                    <div style={{ fontSize: 8, color: T.txtMuted }}>{lbl}</div>
                  </div>
                ))}
              </div>

              {/* Details grid */}
              <div style={{ fontSize: 10, borderTop: `1px solid ${T.border}`, paddingTop: 10 }}>
                {([
                  ['Session', detail.session],
                  ['Mode', detail.trading_mode],
                  ['Strategy', detail.strategy || detail.strategy_name],
                  ['Strategy Key', detail.strategy_key],
                  ['Grade', detail.grade],
                  ['Edge Score', detail.edge_score != null ? detail.edge_score + '/110' : null],
                  ['Duration', detail.hold_minutes != null ? detail.hold_minutes.toFixed(0) + 'm' :
                    detail.duration_min != null ? detail.duration_min.toFixed(0) + 'm' : null],
                  ['MFE', detail.mfe_r != null ? '+' + detail.mfe_r.toFixed(2) + 'R' : null],
                  ['MAE', detail.mae_r != null ? detail.mae_r.toFixed(2) + 'R' : null],
                  ['Opened', jFmtDate(detail.opened_at ?? detail.entry_time)],
                  ['Closed', jFmtDate(detail.closed_at ?? detail.exit_time)],
                  ['Market Regime', detail.market_regime],
                  ['Trade Label', detail.trade_label],
                  ['Entry Reason', detail.entry_reason],
                  ['Outcome', detail.outcome_reason ?? detail.outcome_tag],
                  ['Mistakes', detail.mistakes],
                ] as [string, string | number | null | undefined][]).map(([lbl, val]) => val != null && val !== '' && (
                  <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between',
                    padding: '3px 0', borderBottom: `1px solid ${T.border}33` }}>
                    <span style={{ color: T.txtMuted }}>{lbl}</span>
                    <span style={{ color: T.txtSec, textAlign: 'right', maxWidth: 180,
                      overflow: 'hidden', textOverflow: 'ellipsis' }}>{String(val)}</span>
                  </div>
                ))}
              </div>

              {/* Notes editor (tradzella only) */}
              {detail.source === 'tradzella' && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 4 }}>NOTES</div>
                  <textarea value={noteEdit} onChange={e => setNoteEdit(e.target.value)}
                    rows={4} style={{ width: '100%', background: T.panelAlt,
                      border: `1px solid ${T.border}`, borderRadius: 4,
                      color: T.txtPri, fontSize: 11, padding: '6px 8px',
                      resize: 'vertical', boxSizing: 'border-box' }} />
                  <button onClick={saveNote} disabled={noteSaving}
                    style={{ marginTop: 6, background: T.blue + '33',
                      border: `1px solid ${T.blue}66`, borderRadius: 4,
                      color: T.blue, padding: '4px 12px', fontSize: 10,
                      cursor: noteSaving ? 'default' : 'pointer' }}>
                    {noteSaving ? 'Saving…' : 'Save Notes'}
                  </button>
                </div>
              )}
              {detail.source === 'system' && detail.notes && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 4 }}>NOTES</div>
                  <div style={{ fontSize: 10, color: T.txtSec, lineHeight: 1.5 }}>{String(detail.notes)}</div>
                </div>
              )}

              {/* Phase 7N: Review This Trade */}
              <div style={{ marginTop: 16, paddingTop: 12, borderTop: `1px solid ${T.border}` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  {jReviewBadge((trades.find(t => t.source === detail.source && t.id === detail.id)?.review_status) || 'UNREVIEWED')}
                  <span style={{ fontSize: 9, color: T.txtMuted }}>Review Status</span>
                </div>
                <button onClick={() => openReviewForTrade(detail)}
                  style={{ width: '100%', background: T.cyan + '22',
                    border: `1px solid ${T.cyan}44`, borderRadius: 5,
                    color: T.cyan, padding: '7px 0', fontSize: 10,
                    cursor: 'pointer', fontWeight: 700 }}>
                  ✎ Review This Trade
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Phase 7N: Review modal */}
      {reviewTrade && (
        <JReviewModal
          source={reviewTrade.source}
          tradeId={reviewTrade.id}
          tradeSummary={reviewTrade.detail}
          onClose={() => setReviewTrade(null)}
          onSaved={onReviewSaved}
        />
      )}
      </React.Fragment>
      )}
    </div>
  );
};

// ── Import tab ────────────────────────────────────────────────────────────────
const JImportTab: React.FC = () => {
  const [stage, setStage] = useState<'pick' | 'preview' | 'done'>('pick');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<JPreviewTrade[]>([]);
  const [previewMeta, setPreviewMeta] = useState<{ duplicate_count: number; row_count: number; warnings: string[] } | null>(null);
  const [previewToken, setPreviewToken] = useState<string | null>(null);
  const [filename, setFilename] = useState('');
  const [rawCsv, setRawCsv] = useState('');
  const [doneResult, setDoneResult] = useState<{ batch_id: string; imported: number; auto_reviewed: number; skipped_dupes: number } | null>(null);
  const [reseedResult, setReseedResult] = useState<{ reseeded: number; no_data_to_seed: number; total_checked: number } | null>(null);
  const [reseedLoading, setReseedLoading] = useState(false);
  const [batches, setBatches] = useState<JBatch[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [rollbackMsg, setRollbackMsg] = useState<string | null>(null);

  const fetchBatches = useCallback(async () => {
    setBatchLoading(true);
    try {
      const r = await fetch('/api/journal/import/batches', { headers: getAuthHeader() });
      const data = await r.json();
      if (data.ok) setBatches(data.batches || []);
    } catch { /* ignore */ }
    setBatchLoading(false);
  }, []);

  useEffect(() => { fetchBatches(); }, [fetchBatches]);

  const handleFile = async (file: File) => {
    setFilename(file.name); setLoading(true); setError(null);
    try {
      const raw = await file.text();
      setRawCsv(raw);
      const r = await fetch('/api/journal/import/preview', {
        method: 'POST',
        headers: { ...getAuthHeader(), 'Content-Type': 'text/plain' },
        body: raw,
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'preview failed');
      setPreview(data.trades || []);
      setPreviewToken(data.preview_token || null);
      setPreviewMeta({ duplicate_count: data.duplicate_count, row_count: data.row_count, warnings: data.warnings || [] });
      setStage('preview');
    } catch (e) { setError(String(e)); }
    setLoading(false);
  };

  const handleConfirm = async () => {
    setLoading(true); setError(null);
    try {
      if (!previewToken) throw new Error('No preview session — please re-upload the CSV');
      const r = await fetch('/api/journal/import/confirm', {
        method: 'POST',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        // Only send the server-issued token — never the trade payload.
        // The server holds all trade data; the browser cannot tamper with it.
        body: JSON.stringify({ preview_token: previewToken, filename }),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'confirm failed');
      setDoneResult({ batch_id: data.batch_id, imported: data.imported, auto_reviewed: data.auto_reviewed ?? 0, skipped_dupes: data.skipped_dupes });
      setStage('done');
      fetchBatches();
    } catch (e) { setError(String(e)); }
    setLoading(false);
  };

  const handleRollback = async (batchId: string) => {
    if (!confirm(`Roll back batch ${batchId.slice(0, 8)}…? This deletes all imported trades from that batch.`)) return;
    setRollbackMsg(null);
    try {
      const r = await fetch('/api/journal/import/rollback', {
        method: 'POST',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_id: batchId }),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'rollback failed');
      setRollbackMsg(`Rolled back ${data.deleted} trade${data.deleted !== 1 ? 's' : ''}.`);
      fetchBatches();
    } catch (e) { setRollbackMsg('Error: ' + String(e)); }
  };

  return (
    <div>
      {stage === 'pick' && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ border: `2px dashed ${T.border}`, borderRadius: 8,
            padding: '24px', textAlign: 'center', marginBottom: 14 }}>
            <div style={{ fontSize: 12, color: T.txtSec, marginBottom: 8 }}>
              Drop a Tradzella CSV export here, or click to browse
            </div>
            <input type="file" accept=".csv,text/plain" onChange={e => e.target.files?.[0] && handleFile(e.target.files[0])}
              style={{ display: 'none' }} id="jrnl-csv-input" />
            <label htmlFor="jrnl-csv-input" style={{ background: T.blue + '33',
              border: `1px solid ${T.blue}66`, borderRadius: 6,
              color: T.blue, padding: '6px 16px', fontSize: 11,
              cursor: 'pointer', display: 'inline-block' }}>
              Choose CSV File
            </label>
          </div>
          {loading && <div style={{ color: T.txtMuted, fontSize: 11 }}>Parsing…</div>}
          {error && <div style={{ color: T.red, fontSize: 11 }}>{error}</div>}
        </div>
      )}

      {stage === 'preview' && previewMeta && (
        <div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 11, color: T.txtSec }}>
              {previewMeta.row_count} rows parsed · {' '}
              <span style={{ color: T.amber }}>{previewMeta.duplicate_count} duplicates</span>
              {' · '}{previewMeta.row_count - previewMeta.duplicate_count} new
            </div>
            {previewMeta.warnings.map((w, i) => (
              <div key={i} style={{ fontSize: 10, color: T.amber }}>⚠ {w}</div>
            ))}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <button onClick={() => setStage('pick')}
                style={{ background: 'none', border: `1px solid ${T.border}`, borderRadius: 4,
                  color: T.txtSec, padding: '4px 10px', fontSize: 11, cursor: 'pointer' }}>
                Cancel
              </button>
              <button onClick={handleConfirm} disabled={loading || previewMeta.row_count - previewMeta.duplicate_count === 0}
                style={{ background: T.green + '33', border: `1px solid ${T.green}66`,
                  borderRadius: 4, color: T.green, padding: '4px 12px', fontSize: 11,
                  cursor: loading ? 'default' : 'pointer' }}>
                {loading ? 'Importing…' : `Import ${previewMeta.row_count - previewMeta.duplicate_count} trades`}
              </button>
            </div>
          </div>
          {error && <div style={{ color: T.red, fontSize: 11, marginBottom: 8 }}>{error}</div>}
          <div style={{ overflowX: 'auto', maxHeight: 320, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
              <thead>
                <tr>
                  {['Symbol','Dir','Entry Time','Entry','Exit','P&L','R','Dup'].map(h => (
                    <th key={h} style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                      paddingBottom: 6, fontSize: 9, letterSpacing: '0.07em',
                      position: 'sticky', top: 0, background: T.panel }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.map((t, i) => (
                  <tr key={i} style={{ borderTop: `1px solid ${T.border}`,
                    background: t.duplicate ? T.amber + '11' : 'transparent' }}>
                    <td style={{ padding: '3px 8px 3px 0', color: T.cyan, fontFamily: T.mono, fontWeight: 700 }}>
                      {t.symbol || '—'}
                    </td>
                    <td style={{ padding: '3px 8px 3px 0', color: dirColor(t.side) }}>
                      {(t.side || '—').slice(0,5).toUpperCase()}
                    </td>
                    <td style={{ padding: '3px 8px 3px 0', color: T.txtMuted, whiteSpace: 'nowrap', fontSize: 9 }}>
                      {jFmtShortDate(t.entry_time)}
                    </td>
                    <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono, color: T.txtSec, fontSize: 9 }}>
                      {t.entry_price != null ? t.entry_price.toFixed(2) : '—'}
                    </td>
                    <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono, color: T.txtSec, fontSize: 9 }}>
                      {t.exit_price != null ? t.exit_price.toFixed(2) : '—'}
                    </td>
                    <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono,
                      color: t.pnl != null ? (t.pnl >= 0 ? T.green : T.red) : T.txtMuted }}>
                      {t.pnl != null ? (t.pnl >= 0 ? '+$' : '-$') + Math.abs(t.pnl).toFixed(0) : '—'}
                    </td>
                    <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono, color: jRCol(t.r_multiple ?? null) }}>
                      {t.r_multiple != null ? (t.r_multiple >= 0 ? '+' : '') + t.r_multiple.toFixed(2) : '—'}
                    </td>
                    <td style={{ padding: '3px 0' }}>
                      {t.duplicate
                        ? <span style={{ color: T.amber, fontSize: 9, fontWeight: 700 }}>DUP</span>
                        : <span style={{ color: T.green, fontSize: 9 }}>NEW</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {stage === 'done' && doneResult && (
        <div style={{ padding: '20px 0' }}>
          <div style={{ textAlign: 'center', marginBottom: 16 }}>
            <div style={{ fontSize: 28, marginBottom: 6 }}>✓</div>
            <div style={{ fontSize: 13, color: T.green, fontWeight: 700, marginBottom: 4 }}>
              Import Complete
            </div>
            <div style={{ fontSize: 11, color: T.txtSec, marginBottom: 2 }}>
              {doneResult.imported} trade{doneResult.imported !== 1 ? 's' : ''} imported
              {doneResult.skipped_dupes > 0 ? ` · ${doneResult.skipped_dupes} duplicates skipped` : ''}
            </div>
            <div style={{ fontSize: 9, color: T.txtMuted, fontFamily: T.mono, marginBottom: 12 }}>
              Batch ID: {doneResult.batch_id.slice(0, 12)}…
            </div>
          </div>
          {/* Auto-seed summary */}
          <div style={{ background: doneResult.auto_reviewed > 0 ? T.green + '11' : T.panelAlt,
            border: `1px solid ${doneResult.auto_reviewed > 0 ? T.green + '44' : T.border}`,
            borderRadius: 8, padding: '10px 14px', marginBottom: 14 }}>
            {doneResult.auto_reviewed > 0 ? (
              <>
                <div style={{ fontSize: 11, color: T.green, fontWeight: 700, marginBottom: 3 }}>
                  🧠 {doneResult.auto_reviewed} trade{doneResult.auto_reviewed !== 1 ? 's' : ''} auto-reviewed from your TradeZella journal notes
                </div>
                <div style={{ fontSize: 10, color: T.txtMuted }}>
                  Mistake tags, emotion tags, and quality ratings were extracted from your notes and mistake fields.
                  {doneResult.imported - doneResult.auto_reviewed > 0
                    ? ` ${doneResult.imported - doneResult.auto_reviewed} trade${doneResult.imported - doneResult.auto_reviewed !== 1 ? 's' : ''} had no journal data — open them in Trade Log to add a review.`
                    : ' All imported trades are ready for coaching.'}
                </div>
              </>
            ) : (
              <>
                <div style={{ fontSize: 11, color: T.amber, fontWeight: 700, marginBottom: 3 }}>
                  📋 No journal notes found to auto-review
                </div>
                <div style={{ fontSize: 10, color: T.txtMuted }}>
                  Your CSV didn't contain mistake or notes fields. Go to <strong style={{ color: T.txtPri }}>Trade Log</strong> → filter Tradzella → open each trade to add a review manually. Or fill in the Mistake/Notes columns in TradeZella and re-export.
                </div>
              </>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
            <button onClick={() => { setStage('pick'); setPreview([]); setPreviewMeta(null); setReseedResult(null); }}
              style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 6,
                color: T.txtSec, padding: '6px 16px', fontSize: 11, cursor: 'pointer' }}>
              Import Another File
            </button>
            <button
              disabled={reseedLoading}
              onClick={async () => {
                setReseedLoading(true);
                setReseedResult(null);
                try {
                  const r = await fetch('/api/tradezella/reseed-reviews', {
                    method: 'POST',
                    headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
                  });
                  const d = await r.json();
                  if (d.ok) setReseedResult({ reseeded: d.reseeded, no_data_to_seed: d.no_data_to_seed, total_checked: d.total_checked });
                } catch { /* fail-open */ }
                finally { setReseedLoading(false); }
              }}
              style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 6,
                color: T.txtSec, padding: '6px 16px', fontSize: 11, cursor: reseedLoading ? 'wait' : 'pointer',
                opacity: reseedLoading ? 0.6 : 1 }}>
              {reseedLoading ? 'Re-seeding…' : '🔄 Re-seed All Unreviewed Trades'}
            </button>
          </div>
          {reseedResult && (
            <div style={{ marginTop: 10, background: T.green + '11', border: `1px solid ${T.green}44`,
              borderRadius: 7, padding: '8px 12px', fontSize: 10, color: T.txtSec }}>
              ✓ Re-seed complete — <strong style={{ color: T.green }}>{reseedResult.reseeded}</strong> trades upgraded to REVIEWED
              {reseedResult.no_data_to_seed > 0 ? ` · ${reseedResult.no_data_to_seed} had no journal data to extract` : ''}
              {' '}(checked {reseedResult.total_checked} total)
            </div>
          )}
        </div>
      )}

      {/* Batch history */}
      <div style={{ marginTop: 24, borderTop: `1px solid ${T.border}`, paddingTop: 14 }}>
        <div style={{ fontSize: 9, color: T.txtMuted, letterSpacing: '0.07em', marginBottom: 8 }}>
          IMPORT HISTORY
        </div>
        {rollbackMsg && (
          <div style={{ fontSize: 10, color: T.amber, marginBottom: 8 }}>{rollbackMsg}</div>
        )}
        {batchLoading && <div style={{ color: T.txtMuted, fontSize: 11 }}>Loading…</div>}
        {!batchLoading && batches.length === 0 && (
          <div style={{ color: T.txtMuted, fontSize: 11 }}>No imports yet.</div>
        )}
        {batches.map(b => (
          <div key={b.batch_id} style={{ display: 'flex', alignItems: 'center', gap: 8,
            padding: '6px 0', borderBottom: `1px solid ${T.border}33` }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 10, color: T.txtSec, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {b.filename || 'untitled.csv'}
              </div>
              <div style={{ fontSize: 9, color: T.txtMuted }}>
                {jFmtDate(b.created_at)} · {b.imported_count} imported · {b.skipped_count} dupes
              </div>
            </div>
            <div style={{ fontSize: 9, fontFamily: T.mono, color: T.txtMuted }}>
              {b.batch_id.slice(0, 8)}…
            </div>
            <button onClick={() => handleRollback(b.batch_id)}
              style={{ background: T.red + '22', border: `1px solid ${T.red}44`,
                borderRadius: 3, color: T.red, padding: '2px 8px', fontSize: 9,
                cursor: 'pointer', flexShrink: 0 }}>
              Rollback
            </button>
          </div>
        ))}
      </div>
    </div>
  );
};

// ── Analytics tab ─────────────────────────────────────────────────────────────
interface JCalendarDay {
  date: string; total: number; wins: number; losses: number;
  reviewed: number; unreviewed: number; all_reviewed: boolean;
  total_r: number; pnl: number;
}

const JCalendarView: React.FC<{ dateFrom: string; dateTo: string }> = ({ dateFrom, dateTo }) => {
  const [days, setDays] = useState<JCalendarDay[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError]   = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const params = new URLSearchParams({
        ...(dateFrom ? { from: dateFrom } : {}),
        ...(dateTo   ? { to: dateTo }   : {}),
      });
      const r = await fetch(`/api/journal/calendar-summary?${params}`, { headers: getAuthHeader() });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'failed');
      setDays(d.days || []);
    } catch (e) { setError(String(e)); }
    setLoading(false);
  }, [dateFrom, dateTo]);

  useEffect(() => { fetch_(); }, [fetch_]);

  if (loading) return <div style={{ color: T.txtMuted, fontSize: 11 }}>Loading calendar…</div>;
  if (error)   return <div style={{ color: T.red,     fontSize: 11 }}>Error: {error}</div>;
  if (days.length === 0) return <div style={{ color: T.txtMuted, fontSize: 11 }}>No trade days found</div>;

  return (
    <div>
      <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 8 }}>
        DAILY REVIEW PROGRESS — {days.length} trading days
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {days.map(day => {
          const rCol = day.total_r > 0 ? T.green : day.total_r < 0 ? T.red : T.txtMuted;
          const revPct = day.total > 0 ? Math.round(day.reviewed / day.total * 100) : 0;
          return (
            <div key={day.date} style={{ display: 'flex', alignItems: 'center', gap: 8,
              background: T.panelAlt, borderRadius: 4, padding: '5px 10px',
              border: `1px solid ${day.all_reviewed ? T.green + '44' : T.border}` }}>
              {/* Date + all-reviewed check */}
              <div style={{ minWidth: 80, fontSize: 9, color: T.txtSec, fontFamily: T.mono,
                display: 'flex', alignItems: 'center', gap: 4 }}>
                {day.all_reviewed && <span style={{ color: T.green }}>✓</span>}
                {day.date}
              </div>
              {/* Trades + W/L */}
              <div style={{ minWidth: 60, fontSize: 9, color: T.txtSec }}>
                {day.total}T · {day.wins}W/{day.losses}L
              </div>
              {/* R multiple */}
              <div style={{ minWidth: 52, fontSize: 9, fontFamily: T.mono, fontWeight: 700, color: rCol }}>
                {day.total_r >= 0 ? '+' : ''}{day.total_r.toFixed(2)}R
              </div>
              {/* Review progress bar */}
              <div style={{ flex: 1, position: 'relative', height: 8, background: T.border, borderRadius: 4, minWidth: 60 }}>
                <div style={{ position: 'absolute', left: 0, top: 0, height: '100%',
                  width: `${revPct}%`, background: day.all_reviewed ? T.green : T.cyan,
                  borderRadius: 4, transition: 'width 0.2s' }} />
              </div>
              {/* Reviewed count */}
              <div style={{ minWidth: 55, fontSize: 9, color: day.all_reviewed ? T.green : T.txtMuted,
                textAlign: 'right' }}>
                {day.reviewed}/{day.total} reviewed
              </div>
              {/* Unreviewed badge */}
              {day.unreviewed > 0 && (
                <span style={{ background: T.amber + '22', color: T.amber, borderRadius: 6,
                  padding: '1px 5px', fontSize: 8, fontWeight: 700 }}>
                  {day.unreviewed} left
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ── Phase 7N Batch C: Review Stats component ──────────────────────────────────
interface JReviewAnalytics {
  mistake_tag_counts:           { tag: string; count: number }[];
  positive_tag_counts:          { tag: string; count: number }[];
  followed_plan_distribution:   Record<string, number>;
  rating_distributions:         Record<string, Record<number, number>>;
  win_rate_by_discipline_quality: Record<number, number | null>;
  coaching_summary: {
    top_mistakes:   { tag: string; count: number }[];
    top_positives:  { tag: string; count: number }[];
    reviewed_count: number;
  };
}

const JReviewStatsView: React.FC = () => {
  const [data,    setData]    = useState<JReviewAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setLoading(true); setError(null);
      try {
        const r = await fetch('/api/journal/review-analytics', { headers: getAuthHeader() });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'failed');
        setData(d as JReviewAnalytics);
      } catch (e) { setError(String(e)); }
      setLoading(false);
    })();
  }, []);

  if (loading) return <div style={{ color: T.txtMuted, fontSize: 11 }}>Loading review stats…</div>;
  if (error)   return <div style={{ color: T.red, fontSize: 11 }}>Error: {error}</div>;
  if (!data)   return null;

  const totalMistakes = data.mistake_tag_counts.reduce((s, x) => s + x.count, 0);
  const totalPositives = data.positive_tag_counts.reduce((s, x) => s + x.count, 0);

  const TagBar: React.FC<{ tag: string; count: number; total: number; col: string }> = ({ tag, count, total, col }) => {
    const pct = total > 0 ? Math.round(count / total * 100) : 0;
    return (
      <div style={{ marginBottom: 5 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2, fontSize: 9 }}>
          <span style={{ color: T.txtSec }}>{tag.replace(/_/g, ' ')}</span>
          <span style={{ color: col, fontWeight: 700, fontFamily: T.mono }}>{count}×</span>
        </div>
        <div style={{ height: 5, background: T.border, borderRadius: 3, overflow: 'hidden' }}>
          <div style={{ width: `${pct}%`, height: '100%', background: col, borderRadius: 3 }} />
        </div>
      </div>
    );
  };

  const ratingFields = ['setup_quality', 'execution_quality', 'discipline_quality', 'overall_quality'];
  const ratingLabels: Record<string, string> = {
    setup_quality:      'Setup',
    execution_quality:  'Execution',
    discipline_quality: 'Discipline',
    overall_quality:    'Overall',
  };

  const planColors: Record<string, string> = {
    YES:             T.green,
    PARTIALLY:       T.amber,
    NO:              T.red,
    NOT_APPLICABLE:  T.txtMuted,
  };

  return (
    <div>
      {/* Coaching summary */}
      <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px',
        border: `1px solid ${T.border}`, marginBottom: 14 }}>
        <div style={{ fontSize: 10, color: T.txtMuted, marginBottom: 8, letterSpacing: '0.06em', fontWeight: 700 }}>
          COACHING SUMMARY — {data.coaching_summary.reviewed_count} REVIEWED TRADES
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div>
            <div style={{ fontSize: 9, color: T.red, marginBottom: 6, fontWeight: 700 }}>TOP RECURRING MISTAKES</div>
            {data.coaching_summary.top_mistakes.length === 0
              ? <div style={{ fontSize: 9, color: T.txtMuted }}>No mistakes tagged yet</div>
              : data.coaching_summary.top_mistakes.map((m, i) => (
                <div key={i} style={{ fontSize: 10, color: T.txtSec, marginBottom: 3, display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={{ fontSize: 8, color: T.red, fontWeight: 700, minWidth: 16 }}>{i + 1}.</span>
                  <span style={{ flex: 1 }}>{m.tag.replace(/_/g, ' ')}</span>
                  <span style={{ fontSize: 9, color: T.red, fontFamily: T.mono, fontWeight: 700 }}>{m.count}×</span>
                </div>
              ))}
          </div>
          <div>
            <div style={{ fontSize: 9, color: T.green, marginBottom: 6, fontWeight: 700 }}>TOP POSITIVE PATTERNS</div>
            {data.coaching_summary.top_positives.length === 0
              ? <div style={{ fontSize: 9, color: T.txtMuted }}>No positive tags yet</div>
              : data.coaching_summary.top_positives.map((p, i) => (
                <div key={i} style={{ fontSize: 10, color: T.txtSec, marginBottom: 3, display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={{ fontSize: 8, color: T.green, fontWeight: 700, minWidth: 16 }}>{i + 1}.</span>
                  <span style={{ flex: 1 }}>{p.tag.replace(/_/g, ' ')}</span>
                  <span style={{ fontSize: 9, color: T.green, fontFamily: T.mono, fontWeight: 700 }}>{p.count}×</span>
                </div>
              ))}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {/* Mistake tag frequency */}
        <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px', border: `1px solid ${T.border}` }}>
          <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 10, letterSpacing: '0.06em', fontWeight: 700 }}>
            MISTAKE TAGS ({data.mistake_tag_counts.length})
          </div>
          {data.mistake_tag_counts.length === 0
            ? <div style={{ fontSize: 9, color: T.txtMuted }}>No reviewed trades with mistakes tagged</div>
            : data.mistake_tag_counts.slice(0, 10).map((m, i) => (
              <TagBar key={i} tag={m.tag} count={m.count} total={totalMistakes} col={T.red} />
            ))}
        </div>

        {/* Positive tag frequency */}
        <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px', border: `1px solid ${T.border}` }}>
          <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 10, letterSpacing: '0.06em', fontWeight: 700 }}>
            POSITIVE TAGS ({data.positive_tag_counts.length})
          </div>
          {data.positive_tag_counts.length === 0
            ? <div style={{ fontSize: 9, color: T.txtMuted }}>No reviewed trades with positive tags</div>
            : data.positive_tag_counts.slice(0, 10).map((p, i) => (
              <TagBar key={i} tag={p.tag} count={p.count} total={totalPositives} col={T.green} />
            ))}
        </div>

        {/* Followed-plan distribution */}
        <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px', border: `1px solid ${T.border}` }}>
          <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 10, letterSpacing: '0.06em', fontWeight: 700 }}>
            FOLLOWED PLAN
          </div>
          {Object.keys(data.followed_plan_distribution).length === 0
            ? <div style={{ fontSize: 9, color: T.txtMuted }}>No data yet</div>
            : (['YES', 'PARTIALLY', 'NO', 'NOT_APPLICABLE'] as const).map(opt => {
              const n = data.followed_plan_distribution[opt] ?? 0;
              if (n === 0) return null;
              const total = Object.values(data.followed_plan_distribution).reduce((s, x) => s + x, 0);
              const pct = total > 0 ? Math.round(n / total * 100) : 0;
              return (
                <div key={opt} style={{ marginBottom: 6 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, marginBottom: 2 }}>
                    <span style={{ color: planColors[opt] || T.txtSec }}>{opt.replace(/_/g, ' ')}</span>
                    <span style={{ color: T.txtSec, fontFamily: T.mono }}>{n} ({pct}%)</span>
                  </div>
                  <div style={{ height: 5, background: T.border, borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{ width: `${pct}%`, height: '100%', background: planColors[opt] || T.txtSec, borderRadius: 3 }} />
                  </div>
                </div>
              );
            })}
        </div>

        {/* Rating distributions */}
        <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px', border: `1px solid ${T.border}` }}>
          <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 10, letterSpacing: '0.06em', fontWeight: 700 }}>
            QUALITY RATINGS (1–5)
          </div>
          {ratingFields.map(field => {
            const dist = data.rating_distributions[field] ?? {};
            const total = Object.values(dist).reduce((s, x) => s + x, 0);
            if (total === 0) return null;
            const avg = total > 0
              ? Object.entries(dist).reduce((s, [k, v]) => s + Number(k) * v, 0) / total
              : 0;
            return (
              <div key={field} style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, marginBottom: 3 }}>
                  <span style={{ color: T.txtSec }}>{ratingLabels[field]}</span>
                  <span style={{ color: T.amber, fontFamily: T.mono, fontWeight: 700 }}>{avg.toFixed(1)} avg</span>
                </div>
                <div style={{ display: 'flex', gap: 2 }}>
                  {[1,2,3,4,5].map(n => {
                    const count = dist[n] ?? 0;
                    const pct = total > 0 ? count / total * 100 : 0;
                    return (
                      <div key={n} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                        <div style={{ width: '100%', background: T.border, borderRadius: 2, overflow: 'hidden', height: 24 }}>
                          <div style={{ width: '100%', height: `${pct}%`, background: T.amber + '88', borderRadius: 2,
                            marginTop: `${100 - pct}%`, transition: 'height 0.3s' }} />
                        </div>
                        <span style={{ fontSize: 7, color: T.txtMuted }}>{n}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
          {ratingFields.every(f => Object.keys(data.rating_distributions[f] ?? {}).length === 0) && (
            <div style={{ fontSize: 9, color: T.txtMuted }}>No rating data yet</div>
          )}
        </div>

        {/* Win-rate by discipline quality */}
        <div style={{ gridColumn: '1/-1', background: T.panelAlt, borderRadius: 8, padding: '12px 14px', border: `1px solid ${T.border}` }}>
          <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 10, letterSpacing: '0.06em', fontWeight: 700 }}>
            WIN RATE BY DISCIPLINE QUALITY (SYSTEM TRADES)
          </div>
          {Object.keys(data.win_rate_by_discipline_quality).length === 0
            ? <div style={{ fontSize: 9, color: T.txtMuted }}>Not enough reviewed system trades yet</div>
            : (
              <div style={{ display: 'flex', gap: 10 }}>
                {[1,2,3,4,5].map(n => {
                  const pct = data.win_rate_by_discipline_quality[n];
                  return (
                    <div key={n} style={{ flex: 1, textAlign: 'center' }}>
                      <div style={{ fontSize: 14, fontWeight: 800, fontFamily: T.mono,
                        color: pct != null ? (pct >= 50 ? T.green : T.red) : T.txtMuted }}>
                        {pct != null ? pct.toFixed(0) + '%' : '—'}
                      </div>
                      <div style={{ fontSize: 8, color: T.txtMuted }}>Disc {n}</div>
                    </div>
                  );
                })}
              </div>
            )}
        </div>
      </div>
    </div>
  );
};

const JAnalyticsTab: React.FC = () => {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState('strategy');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  const fetchAnalytics = useCallback(async () => {
    if (groupBy === 'calendar' || groupBy === 'review-stats') return; // own sub-components
    setLoading(true); setError(null);
    try {
      const params = new URLSearchParams({ group_by: groupBy,
        ...(dateFrom ? { from: dateFrom } : {}),
        ...(dateTo   ? { to:   dateTo   } : {}),
      });
      const r = await fetch(`/api/journal/analytics?${params}`, { headers: getAuthHeader() });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'failed');
      setData(d);
    } catch (e) { setError(String(e)); }
    setLoading(false);
  }, [groupBy, dateFrom, dateTo]);

  useEffect(() => { fetchAnalytics(); }, [fetchAnalytics]);

  const sum = (data?.summary ?? {}) as Record<string, unknown>;
  const sys = (data?.system  ?? {}) as Record<string, unknown>;
  const tz  = (data?.tradzella ?? {}) as Record<string, unknown>;
  const bd  = Array.isArray(data?.breakdown) ? data!.breakdown as Record<string, unknown>[] : [];

  const StatCard: React.FC<{ label: string; value: unknown; color?: string; sub?: string }> = ({ label, value, color, sub }) => (
    <div style={{ background: T.panelAlt, borderRadius: 6, padding: '10px 12px',
      border: `1px solid ${T.border}` }}>
      <div style={{ fontSize: 18, fontWeight: 800, fontFamily: T.mono,
        color: color || T.txtPri }}>
        {value != null && value !== '' ? String(value) : '—'}
      </div>
      <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 2 }}>{label}</div>
      {sub && <div style={{ fontSize: 9, color: T.txtSec, marginTop: 1 }}>{sub}</div>}
    </div>
  );

  return (
    <div>
      {/* Controls */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <select value={groupBy} onChange={e => setGroupBy(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 8px', fontSize: 11 }}>
          {['strategy','instrument','session','regime','daily','weekly','monthly','calendar','review-stats'].map(g => (
            <option key={g} value={g}>
              {g === 'calendar' ? '📅 Calendar (Review)'
               : g === 'review-stats' ? '📊 Review Stats'
               : g.charAt(0).toUpperCase() + g.slice(1)}
            </option>
          ))}
        </select>
        <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }} />
        <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
          style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
            color: T.txtSec, padding: '4px 6px', fontSize: 11 }} />
        {groupBy !== 'calendar' && (
          <button onClick={fetchAnalytics}
            style={{ background: T.cyan + '22', border: `1px solid ${T.cyan}44`, borderRadius: 4,
              color: T.cyan, padding: '4px 10px', fontSize: 11, cursor: 'pointer' }}>
            Refresh
          </button>
        )}
      </div>

      {/* Calendar view — fetches /journal/calendar-summary independently */}
      {groupBy === 'calendar' && (
        <JCalendarView dateFrom={dateFrom} dateTo={dateTo} />
      )}

      {/* Review Stats — fetches /journal/review-analytics independently */}
      {groupBy === 'review-stats' && <JReviewStatsView />}

      {groupBy !== 'calendar' && groupBy !== 'review-stats' && loading && <div style={{ color: T.txtMuted, fontSize: 11 }}>Loading…</div>}
      {groupBy !== 'calendar' && groupBy !== 'review-stats' && error && <div style={{ color: T.red, fontSize: 11 }}>Error: {error}</div>}

      {groupBy !== 'calendar' && groupBy !== 'review-stats' && data && !loading && (
        <>
          {/* Summary cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 14 }}>
            <StatCard label="TOTAL TRADES" value={sum.n} />
            <StatCard label="WIN RATE" value={sum.win_pct != null ? sum.win_pct + '%' : '—'} color={T.green} />
            <StatCard label="AVG R (SYSTEM)" value={sys.avg_r != null ? (Number(sys.avg_r) >= 0 ? '+' : '') + Number(sys.avg_r).toFixed(2) + 'R' : '—'}
              color={sys.avg_r != null ? (Number(sys.avg_r) >= 0 ? T.green : T.red) : T.txtSec}
              sub={`${sys.n} trades`} />
            <StatCard label="PROFIT FACTOR" value={sys.profit_factor != null ? Number(sys.profit_factor).toFixed(2) : '—'}
              color={sys.profit_factor != null && Number(sys.profit_factor) >= 1.5 ? T.green : T.amber} />
          </div>

          {/* Source comparison */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 14 }}>
            {([['System Trades', sys], ['Tradzella Imports', tz]] as [string, Record<string, unknown>][]).map(([lbl, s]) => (
              <div key={lbl} style={{ background: T.panelAlt, borderRadius: 6,
                padding: '10px 12px', border: `1px solid ${T.border}` }}>
                <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 6 }}>{lbl.toUpperCase()}</div>
                <div style={{ display: 'flex', gap: 12 }}>
                  {([
                    ['n', 'Trades', ''],
                    ['win_pct', 'Win%', '%'],
                    ['avg_r', 'Avg R', 'R'],
                    ['profit_factor', 'PF', ''],
                  ] as [string, string, string][]).map(([k, l, sfx]) => {
                    const v = s[k];
                    const num = v != null ? Number(v) : null;
                    return (
                      <div key={k}>
                        <div style={{ fontSize: 13, fontWeight: 700, fontFamily: T.mono,
                          color: k === 'avg_r' && num != null ? (num >= 0 ? T.green : T.red) : T.txtPri }}>
                          {num != null ? (k === 'avg_r' && num >= 0 ? '+' : '') + num.toFixed(k === 'n' ? 0 : 2) + sfx : '—'}
                        </div>
                        <div style={{ fontSize: 8, color: T.txtMuted }}>{l}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {/* Breakdown table */}
          <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 6 }}>
            BY {groupBy.toUpperCase()}
          </div>
          <div style={{ overflowX: 'auto', maxHeight: 280, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
              <thead>
                <tr>
                  {['Group','Trades','Wins','Win%','Avg R'].map(h => (
                    <th key={h} style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                      paddingBottom: 6, fontSize: 9, position: 'sticky', top: 0, background: T.panel }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {bd.map((row, i) => {
                  const avgR = row.avg_r != null ? Number(row.avg_r) : null;
                  return (
                    <tr key={i} style={{ borderTop: `1px solid ${T.border}` }}>
                      <td style={{ padding: '4px 8px 4px 0', color: T.txtSec, maxWidth: 140,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {String(row.group || '—')}
                      </td>
                      <td style={{ padding: '4px 8px 4px 0', color: T.txtSec, fontFamily: T.mono }}>{String(row.n ?? '—')}</td>
                      <td style={{ padding: '4px 8px 4px 0', color: T.green, fontFamily: T.mono }}>{String(row.wins ?? '—')}</td>
                      <td style={{ padding: '4px 8px 4px 0', color: T.txtPri, fontFamily: T.mono }}>
                        {row.win_pct != null ? Number(row.win_pct).toFixed(1) + '%' : '—'}
                      </td>
                      <td style={{ padding: '4px 0', fontFamily: T.mono,
                        color: avgR != null ? (avgR >= 0 ? T.green : T.red) : T.txtMuted, fontWeight: 700 }}>
                        {avgR != null ? (avgR >= 0 ? '+' : '') + avgR.toFixed(2) + 'R' : '—'}
                      </td>
                    </tr>
                  );
                })}
                {bd.length === 0 && (
                  <tr><td colSpan={5} style={{ textAlign: 'center', color: T.txtMuted,
                    padding: '12px 0', fontSize: 11 }}>No data</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
};

// ── Playbook tab ──────────────────────────────────────────────────────────────
const JPlaybookTab: React.FC = () => {
  const [data, setData] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minN, setMinN] = useState(20);

  useEffect(() => {
    setLoading(true);
    fetch('/api/journal/playbook', { headers: getAuthHeader() })
      .then(r => r.json())
      .then(d => { if (d.ok) { setData(d.strategies || []); setMinN(d.min_n || 20); } else setError(d.error || 'failed'); })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color: T.txtMuted, fontSize: 11 }}>Loading…</div>;
  if (error)   return <div style={{ color: T.red, fontSize: 11 }}>Error: {error}</div>;

  return (
    <div>
      <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 10 }}>
        ⚠ Strategies with fewer than {minN} trades are flagged as low-sample.
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {data.map((s, i) => {
          const avgR = s.avg_r != null ? Number(s.avg_r) : null;
          const winPct = s.win_pct != null ? Number(s.win_pct) : null;
          const isFlagged = s.sample_warning === true;
          return (
            <div key={i} style={{ background: T.panelAlt, borderRadius: 8,
              padding: '12px 14px', border: `1px solid ${isFlagged ? T.amber + '55' : T.border}` }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <div style={{ fontSize: 10, color: T.txtSec, fontWeight: 700,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '65%' }}>
                  {String(s.strategy || '—')}
                </div>
                {isFlagged && (
                  <span style={{ fontSize: 8, color: T.amber, background: T.amber + '22',
                    borderRadius: 3, padding: '1px 5px', flexShrink: 0 }}>
                    LOW SAMPLE
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', gap: 12 }}>
                <div>
                  <div style={{ fontSize: 18, fontWeight: 800, fontFamily: T.mono,
                    color: winPct != null && winPct >= 50 ? T.green : T.amber }}>
                    {winPct != null ? winPct.toFixed(0) + '%' : '—'}
                  </div>
                  <div style={{ fontSize: 8, color: T.txtMuted }}>WIN RATE</div>
                </div>
                <div>
                  <div style={{ fontSize: 18, fontWeight: 800, fontFamily: T.mono,
                    color: avgR != null ? (avgR >= 0 ? T.green : T.red) : T.txtSec }}>
                    {avgR != null ? (avgR >= 0 ? '+' : '') + avgR.toFixed(2) + 'R' : '—'}
                  </div>
                  <div style={{ fontSize: 8, color: T.txtMuted }}>AVG R</div>
                </div>
                <div>
                  <div style={{ fontSize: 18, fontWeight: 800, fontFamily: T.mono, color: T.txtSec }}>
                    {String(s.n ?? '—')}
                  </div>
                  <div style={{ fontSize: 8, color: T.txtMuted }}>TRADES</div>
                </div>
              </div>
              {Boolean(s.trading_mode) && (
                <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 6 }}>
                  {String(s.trading_mode)} · Last: {jFmtShortDate(s.last_trade)}
                </div>
              )}
            </div>
          );
        })}
        {data.length === 0 && (
          <div style={{ gridColumn: '1/-1', color: T.txtMuted, fontSize: 11, padding: '16px 0', textAlign: 'center' }}>
            No strategy data. System trades appear here after they close.
          </div>
        )}
      </div>
    </div>
  );
};

// ── Learning tab ──────────────────────────────────────────────────────────────
const JLearningTab: React.FC = () => {
  const [records, setRecords] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch('/api/journal/learning', { headers: getAuthHeader() })
      .then(r => r.json())
      .then(d => { if (d.ok) setRecords(d.records || []); else setError(d.error || 'failed'); })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color: T.txtMuted, fontSize: 11 }}>Loading…</div>;
  if (error)   return <div style={{ color: T.red, fontSize: 11 }}>Error: {error}</div>;

  const statusColor = (status: unknown): string => {
    const s = String(status || '');
    if (s === 'LIVE_ELIGIBLE') return T.green;
    if (s === 'GHOST_ONLY') return T.amber;
    if (s === 'DISABLED') return T.red;
    return T.txtMuted;
  };

  return (
    <div>
      <div style={{ fontSize: 9, color: T.txtMuted, marginBottom: 10 }}>
        Display-only · These eligibility decisions are computed automatically — no controls here affect the gate.
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
          <thead>
            <tr>
              {['Instrument','Mode','Status','n','Win%','Avg R','Last 20R','Rule'].map(h => (
                <th key={h} style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                  paddingBottom: 6, fontSize: 9, letterSpacing: '0.07em', whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {records.map((r, i) => {
              const status = String(r.status || '');
              const sc = statusColor(status);
              const wr = r.win_rate != null ? Number(r.win_rate) : null;
              const ar = r.avg_r != null ? Number(r.avg_r) : null;
              const l20 = r.last_20_avg_r != null ? Number(r.last_20_avg_r) : null;
              return (
                <tr key={i} style={{ borderTop: `1px solid ${T.border}` }}>
                  <td style={{ padding: '4px 8px 4px 0', color: T.cyan, fontFamily: T.mono, fontWeight: 700 }}>
                    {String(r.instrument || '—')}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', color: T.txtSec, fontSize: 9 }}>
                    {String(r.mode || '—')}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0' }}>
                    <span style={{ background: sc + '22', color: sc, borderRadius: 3,
                      padding: '1px 5px', fontSize: 8, fontWeight: 700 }}>
                      {status || '—'}
                    </span>
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', fontFamily: T.mono, color: T.txtSec }}>
                    {r.n != null ? String(r.n) : '—'}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', fontFamily: T.mono,
                    color: wr != null ? (wr >= 0.5 ? T.green : T.amber) : T.txtMuted }}>
                    {wr != null ? (wr * 100).toFixed(0) + '%' : '—'}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', fontFamily: T.mono,
                    color: ar != null ? (ar >= 0 ? T.green : T.red) : T.txtMuted }}>
                    {ar != null ? (ar >= 0 ? '+' : '') + ar.toFixed(2) + 'R' : '—'}
                  </td>
                  <td style={{ padding: '4px 8px 4px 0', fontFamily: T.mono,
                    color: l20 != null ? (l20 >= 0 ? T.green : T.red) : T.txtMuted }}>
                    {l20 != null ? (l20 >= 0 ? '+' : '') + l20.toFixed(2) + 'R' : '—'}
                  </td>
                  <td style={{ padding: '4px 0', color: T.txtMuted, fontSize: 9,
                    maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {String(r.rule || '—')}
                  </td>
                </tr>
              );
            })}
            {records.length === 0 && (
              <tr><td colSpan={8} style={{ textAlign: 'center', color: T.txtMuted,
                padding: '16px 0', fontSize: 11 }}>
                Learning eligibility not yet computed. It runs after a trade closes.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// ── Directional Balance tab (Phase 7M) ────────────────────────────────────────
interface DirectionalBalance {
  enabled: boolean;
  error?: string;
  eval_metrics: {
    long: number; short: number; total: number;
    long_pct: number; short_pct: number;
    ratio?: number; skew?: string;
  };
  alert_history: {
    long: number; short: number; total: number;
    long_pct: number; short_pct: number;
  };
  strategy_trades: {
    long: number; short: number; total: number;
    long_pct: number; short_pct: number;
    instruments: Record<string, { long: number; short: number }>;
    period_days: number;
  };
  scan_candidates: {
    long: number; short: number; total: number;
    long_pct: number; short_pct: number;
  };
  structure_events: {
    bullish: number; bearish: number; neutral: number; total: number;
    bullish_pct: number; bearish_pct: number;
  };
  known_asymmetries: { description: string; classification: string; note: string }[];
}

const DirectionalBar: React.FC<{
  label: string; longV: number; shortV: number; longPct: number; shortPct: number;
}> = ({ label, longV, shortV, longPct, shortPct }) => {
  const longClr  = '#22c55e';
  const shortClr = '#ef4444';
  const neutClr  = T.txtMuted;
  const skew = longPct >= 60 ? 'LONG-HEAVY' : shortPct >= 60 ? 'SHORT-HEAVY' : 'BALANCED';
  const skewClr  = skew === 'BALANCED' ? neutClr : skew === 'LONG-HEAVY' ? longClr : shortClr;
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display:'flex', justifyContent:'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 11, color: T.txtMuted }}>{label}</span>
        <span style={{ fontSize: 10, color: skewClr, fontWeight: 700 }}>{skew}</span>
      </div>
      <div style={{ display:'flex', gap: 4, alignItems:'center' }}>
        {/* Long bar */}
        <span style={{ fontSize: 10, color: longClr, width: 28, textAlign:'right' }}>{longPct}%</span>
        <div style={{ flex: 1, height: 10, background: T.border, borderRadius: 5, overflow:'hidden', display:'flex' }}>
          <div style={{ width: `${longPct}%`, background: longClr, transition: 'width .4s' }} />
          <div style={{ width: `${shortPct}%`, background: shortClr, transition: 'width .4s' }} />
        </div>
        <span style={{ fontSize: 10, color: shortClr, width: 28 }}>{shortPct}%</span>
      </div>
      <div style={{ display:'flex', justifyContent:'space-between', marginTop: 3, fontSize: 10, color: T.txtMuted }}>
        <span>Long {longV}</span><span>Short {shortV}</span>
      </div>
    </div>
  );
};

const JDirectionalTab: React.FC = () => {
  const [data,    setData]    = useState<DirectionalBalance | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch('/api/directional-balance', { headers: getAuthHeader() })
      .then(r => r.json())
      .then(j => { setData(j); setError(null); })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color: T.txtMuted, fontSize: 12, padding: 16 }}>Loading…</div>;
  if (error)   return <div style={{ color: '#ef4444', fontSize: 12, padding: 8 }}>{error}</div>;
  if (!data || !data.enabled)
    return <div style={{ color: T.txtMuted, fontSize: 12, padding: 16 }}>Directional balance audit not available.</div>;

  const em  = data.eval_metrics;
  const ah  = data.alert_history;
  const st  = data.strategy_trades;
  const sc  = data.scan_candidates;
  const se  = data.structure_events;
  const asym = data.known_asymmetries ?? [];

  return (
    <div style={{ padding: 4 }}>
      <div style={{ fontSize: 11, color: T.txtMuted, marginBottom: 12 }}>
        Phase 7M — Read-only directional balance audit. Tracks whether the system sends
        proportionally more Long or Short signals across all layers.
      </div>

      {/* ── Sections ── */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: T.txtPri, marginBottom: 8, borderBottom:`1px solid ${T.border}`, paddingBottom: 4 }}>
          Evaluated Setups (this session)
        </div>
        <DirectionalBar label="Eval Events" longV={em.long} shortV={em.short} longPct={em.long_pct} shortPct={em.short_pct} />
        {em.skew && <div style={{ fontSize: 10, color: T.txtMuted }}>Session skew: {em.skew} · ratio {em.ratio?.toFixed(2) ?? 'n/a'}</div>}
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: T.txtPri, marginBottom: 8, borderBottom:`1px solid ${T.border}`, paddingBottom: 4 }}>
          Alert History (recent window)
        </div>
        <DirectionalBar label="Alert Events" longV={ah.long} shortV={ah.short} longPct={ah.long_pct} shortPct={ah.short_pct} />
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: T.txtPri, marginBottom: 8, borderBottom:`1px solid ${T.border}`, paddingBottom: 4 }}>
          Scan Candidates
        </div>
        <DirectionalBar label="Candidates" longV={sc.long} shortV={sc.short} longPct={sc.long_pct} shortPct={sc.short_pct} />
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: T.txtPri, marginBottom: 8, borderBottom:`1px solid ${T.border}`, paddingBottom: 4 }}>
          Structure Events (live stream)
        </div>
        <div style={{ display:'flex', gap: 16, fontSize: 11 }}>
          <span style={{ color:'#22c55e' }}>↑ Bullish {se.bullish} ({se.bullish_pct}%)</span>
          <span style={{ color:'#ef4444' }}>↓ Bearish {se.bearish} ({se.bearish_pct}%)</span>
          <span style={{ color: T.txtMuted }}>– Neutral {se.neutral}</span>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: T.txtPri, marginBottom: 8, borderBottom:`1px solid ${T.border}`, paddingBottom: 4 }}>
          7-Day DB Trades ({st.period_days}d)
        </div>
        {st.total === 0
          ? <div style={{ fontSize: 11, color: T.txtMuted }}>No trades in DB yet.</div>
          : (
            <>
              <DirectionalBar label="DB Trades" longV={st.long} shortV={st.short} longPct={st.long_pct} shortPct={st.short_pct} />
              {Object.entries(st.instruments).map(([inst, counts]) => (
                <div key={inst} style={{ fontSize: 10, color: T.txtMuted, marginBottom: 2 }}>
                  {inst}: Long {counts.long} · Short {counts.short}
                </div>
              ))}
            </>
          )
        }
      </div>

      {asym.length > 0 && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, color: T.txtPri, marginBottom: 8, borderBottom:`1px solid ${T.border}`, paddingBottom: 4 }}>
            Documented Asymmetries
          </div>
          {asym.map((a, i) => (
            <div key={i} style={{ marginBottom: 8, padding: '6px 8px', background: T.bg, borderRadius: 6, border:`1px solid ${T.border}` }}>
              <div style={{ fontSize: 11, color: T.txtPri, marginBottom: 2 }}>{a.description}</div>
              <div style={{ display:'flex', gap: 8, fontSize: 10 }}>
                <span style={{ color:'#f59e0b' }}>{a.classification}</span>
                <span style={{ color: T.txtMuted }}>{a.note}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: 8, fontSize: 10, color: T.txtMuted }}>
        Classification: <strong>Market-Driven</strong> — code is symmetric; observed skew follows market conditions.
        Audit code: test_directional_symmetry_7m.py (23 tests).
      </div>
    </div>
  );
};

// ── Phase 7O: Journal Coaching Dashboard ─────────────────────────────────────
// Read-only coaching analytics — never modifies trading logic or learning formulas.
// Phase 7O.1 adds onDrill: clicking any insight switches to the Trade Log filtered
// to exactly the trades that produced that coaching metric.

interface JCoachCoverage {
  total: number; reviewed: number; excluded: number; incomplete: number;
  unreviewed: number; instrument_count: number; confidence: string;
}
interface JCoachMistake {
  tag: string; n: number; wins: number; net_r: number | null; avg_r: number | null;
  win_rate: number | null; confidence: string; profit_factor: number | null;
  instruments: string; sessions: string;
}
interface JCoachBehavior {
  tag: string; n: number; wins: number; net_r: number | null; avg_r: number | null;
  win_rate: number | null; plan_follow_pct: number | null; confidence: string;
  profit_factor: number | null;
}
interface JCoachPlan {
  followed_plan: string; n: number; wins: number; net_r: number | null;
  avg_r: number | null; win_rate: number | null;
  avg_setup: number | null; avg_execution: number | null; avg_discipline: number | null;
}
interface JCoachEmotion {
  emotion: string; n: number; avg_intensity: number | null; wins: number;
  net_r: number | null; avg_r: number | null; win_rate: number | null;
  plan_follow_pct: number | null; confidence: string;
}
interface JCoachStrategy {
  strategy_name: string; n: number; wins: number; net_r: number | null;
  avg_r: number | null; win_rate: number | null; plan_follow_pct: number | null;
  confidence: string; profit_factor: number | null;
}
interface JCoachSession {
  session: string; dow: number; n: number; wins: number;
  net_r: number | null; avg_r: number | null; win_rate: number | null;
  plan_follow_pct: number | null; confidence: string;
}
interface JCoachTrendWeek {
  week_start: string; n: number; avg_discipline: number | null;
  net_r: number | null; followed_plan_pct: number | null; mistake_rate_pct: number | null;
}
interface JCoachPriority {
  type: string; tag: string; score: number; net_r: number | null;
  count: number; confidence: string; description: string;
}
interface JCoachData {
  ok: boolean;
  data_coverage: JCoachCoverage;
  costliest_mistakes: JCoachMistake[];
  best_behaviors: JCoachBehavior[];
  followed_plan_analytics: JCoachPlan[];
  emotion_analytics: JCoachEmotion[];
  rating_analytics: Record<string, { rating: number; n: number; avg_r: number | null; win_rate: number | null }[]>;
  strategy_coaching: JCoachStrategy[];
  session_analytics: JCoachSession[];
  discipline_trend: { label: string; weekly: JCoachTrendWeek[] };
  coaching_summary: {
    biggest_leak?: { tag: string; net_r: number; count: number; text: string };
    best_habit?: { tag: string; avg_r: number; count: number; text: string };
    best_setup?: { strategy: string; avg_r: number; count: number };
    worst_condition?: { session: string; avg_r: number; count: number };
    discipline_trend: string;
    next_focus?: { type: string; tag: string; text: string };
  };
  coaching_priority: JCoachPriority[];
}

const _CONFIDENCE_COLOR: Record<string, string> = {
  STRONG_EVIDENCE:    '#00ff88',
  MODERATE_CONFIDENCE: '#f5a623',
  EARLY_SIGNAL:       '#a0a0b0',
  INSUFFICIENT_DATA:  '#666688',
};
const _TREND_COLOR: Record<string, string> = {
  IMPROVING:         '#00ff88',
  STABLE:            '#f5a623',
  DECLINING:         '#ff4560',
  INSUFFICIENT_DATA: '#666688',
};
const _PLAN_COLOR: Record<string, string> = {
  YES: '#00ff88', PARTIALLY: '#f5a623', NO: '#ff4560', NOT_APPLICABLE: '#666688',
};
const _DOW_LABEL = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

function _confBadge(confidence: string) {
  const col = _CONFIDENCE_COLOR[confidence] ?? T.txtMuted;
  const short: Record<string, string> = {
    STRONG_EVIDENCE: 'STRONG', MODERATE_CONFIDENCE: 'MODERATE',
    EARLY_SIGNAL: 'EARLY', INSUFFICIENT_DATA: 'INSUFF',
  };
  return (
    <span style={{ background: col + '22', color: col, fontSize: 7, fontWeight: 700,
      borderRadius: 3, padding: '0 4px', whiteSpace: 'nowrap' }}>
      {short[confidence] ?? confidence}
    </span>
  );
}

const JCoachingTab: React.FC<{
  onDrill?: (f: JDrillFilter) => void;
}> = ({ onDrill }) => {
  const [data,    setData]    = useState<JCoachData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  // Hover state for drill-down highlighting: [section, rowIndex]
  const [hovSec, setHovSec] = useState<string | null>(null);
  const [hovIdx, setHovIdx] = useState<number>(-1);

  // Filters
  const [dateFrom,    setDateFrom]    = useState('');
  const [dateTo,      setDateTo]      = useState('');
  const [fSource,     setFSource]     = useState('');
  const [fInstrument, setFInstrument] = useState('');
  const [fMode,       setFMode]       = useState('');

  // Phase 7O.2: intraday block coaching state
  const [intradayData,    setIntradayData]    = useState<{ blocks: any[]; intraday_summary: string[]; display_timezone: string } | null>(null);
  const [intradayLoading, setIntradayLoading] = useState(false);
  // Sortable detail table
  const [idSort,    setIdSort]    = useState<string>('net_r');
  const [idSortDir, setIdSortDir] = useState<'asc' | 'desc'>('desc');

  // Phase 7O.3: correlation analytics state
  const [correlationsData,    setCorrelationsData]    = useState<any | null>(null);
  const [correlationsLoading, setCorrelationsLoading] = useState(false);
  // Which rating field to show in the rating × mistake/emotion tables
  const [corrField, setCorrField] = useState<string>('discipline_quality');

  const fetchCoaching = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const p = new URLSearchParams();
      if (dateFrom)    p.set('date_from',   dateFrom);
      if (dateTo)      p.set('date_to',     dateTo);
      if (fSource)     p.set('source',      fSource);
      if (fInstrument) p.set('instrument',  fInstrument);
      if (fMode)       p.set('mode',        fMode);
      const r = await fetch(`/api/journal/coaching?${p}`, { headers: getAuthHeader() });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'failed');
      setData(d as JCoachData);
    } catch (e) { setError(String(e)); }
    setLoading(false);
  }, [dateFrom, dateTo, fSource, fInstrument, fMode]);

  // Phase 7O.2: fetch intraday block analytics — shares the same filter state
  const fetchIntraday = useCallback(async () => {
    setIntradayLoading(true);
    try {
      const p = new URLSearchParams();
      if (dateFrom)    p.set('date_from',  dateFrom);
      if (dateTo)      p.set('date_to',    dateTo);
      if (fSource)     p.set('source',     fSource);
      if (fInstrument) p.set('instrument', fInstrument);
      if (fMode)       p.set('mode',       fMode);
      const r = await fetch(`/api/journal/coaching/intraday?${p}`, { headers: getAuthHeader() });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'failed');
      setIntradayData(d);
    } catch (_e) { /* fail silently — intraday is supplementary */ }
    setIntradayLoading(false);
  }, [dateFrom, dateTo, fSource, fInstrument, fMode]);

  // Phase 7O.3: fetch correlation analytics — shares the same filter state
  const fetchCorrelations = useCallback(async () => {
    setCorrelationsLoading(true);
    try {
      const p = new URLSearchParams();
      if (dateFrom)    p.set('date_from',  dateFrom);
      if (dateTo)      p.set('date_to',    dateTo);
      if (fSource)     p.set('source',     fSource);
      if (fInstrument) p.set('instrument', fInstrument);
      if (fMode)       p.set('mode',       fMode);
      const r = await fetch(`/api/journal/coaching/correlations?${p}`, { headers: getAuthHeader() });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'failed');
      setCorrelationsData(d);
    } catch (_e) { /* fail silently — correlations is supplementary */ }
    setCorrelationsLoading(false);
  }, [dateFrom, dateTo, fSource, fInstrument, fMode]);

  /** Build a JDrillFilter that carries both the current coaching filter context
   *  (date/source/instrument/mode) and the specific drill-down key(s). */
  const _mkDrill = useCallback((
    label: string,
    count: number | undefined,
    extra: Partial<JDrillFilter>,
  ): JDrillFilter => ({
    label,
    count,
    review_status: 'REVIEWED',
    ...(dateFrom    ? { date_from:   dateFrom    } : {}),
    ...(dateTo      ? { date_to:     dateTo      } : {}),
    ...(fSource     ? { source:      fSource     } : {}),
    ...(fInstrument ? { instrument:  fInstrument } : {}),
    ...(fMode       ? { mode:        fMode       } : {}),
    ...extra,
  }), [dateFrom, dateTo, fSource, fInstrument, fMode]);

  /** Keyboard handler for drill-able rows (Enter or Space activates) */
  const _rowKey = (fn: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); }
  };

  /** Row style with hover highlight when onDrill is available */
  const _rowSt = (sec: string, i: number): React.CSSProperties => ({
    borderTop: `1px solid ${T.border}`,
    cursor:    onDrill ? 'pointer' : 'default',
    background: (hovSec === sec && hovIdx === i) ? T.panelAlt : 'transparent',
    outline:   'none',
  });

  /** Last cell: show VIEW N TRADES on hover, confidence badge otherwise */
  const _lastCell = (sec: string, i: number, n: number, conf: string, fn: () => void) =>
    hovSec === sec && hovIdx === i && onDrill
      ? <button onClick={e => { e.stopPropagation(); fn(); }}
          style={{ fontSize: 7, background: T.cyan + '22', border: `1px solid ${T.cyan}55`,
            borderRadius: 3, color: T.cyan, padding: '2px 5px', cursor: 'pointer',
            whiteSpace: 'nowrap', fontWeight: 700 }}
          aria-label={`View ${n} trades`}>
          VIEW {n}
        </button>
      : _confBadge(conf);

  useEffect(() => { fetchCoaching(); fetchIntraday(); fetchCorrelations(); }, [fetchCoaching, fetchIntraday, fetchCorrelations]);

  const inp = (label: string, val: string, set: (v: string) => void, type = 'text', placeholder = '') => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 8, color: T.txtMuted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
      <input type={type} value={val} placeholder={placeholder}
        onChange={e => set(e.target.value)}
        style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
          color: T.txtSec, padding: '4px 7px', fontSize: 11, width: 110 }} />
    </label>
  );

  const sel = (label: string, val: string, set: (v: string) => void, opts: string[][]) => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 8, color: T.txtMuted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
      <select value={val} onChange={e => set(e.target.value)}
        style={{ background: T.panelAlt, border: `1px solid ${T.border}`, borderRadius: 4,
          color: T.txtSec, padding: '4px 7px', fontSize: 11 }}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );

  const rC = (r: number | null) => r != null ? (r >= 0 ? T.green : T.red) : T.txtMuted;
  const rFmt = (r: number | null) =>
    r != null ? `${r >= 0 ? '+' : ''}${r.toFixed(2)}R` : '—';

  const SummaryCard: React.FC<{ title: string; value: React.ReactNode; sub?: string; col?: string }> =
    ({ title, value, sub, col }) => (
    <div style={{ flex: 1, background: T.panelAlt, borderRadius: 8, padding: '10px 12px',
      border: `1px solid ${T.border}` }}>
      <div style={{ fontSize: 8, color: T.txtMuted, letterSpacing: '0.06em', fontWeight: 700,
        marginBottom: 4, textTransform: 'uppercase' }}>{title}</div>
      <div style={{ fontSize: 16, fontWeight: 800, fontFamily: T.mono, color: col ?? T.txtPri }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 9, color: T.txtMuted, marginTop: 3 }}>{sub}</div>}
    </div>
  );

  const TableHead: React.FC<{ cols: string[] }> = ({ cols }) => (
    <thead>
      <tr>
        {cols.map(c => (
          <th key={c} style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
            paddingBottom: 5, fontSize: 8, paddingRight: 10, whiteSpace: 'nowrap' }}>
            {c}
          </th>
        ))}
      </tr>
    </thead>
  );

  const noData = (msg: string) => (
    <div style={{ fontSize: 9, color: T.txtMuted, padding: '8px 0' }}>{msg}</div>
  );

  if (loading && !data) return (
    <div style={{ color: T.txtMuted, fontSize: 11, padding: 12 }}>Loading coaching analytics…</div>
  );
  if (error) return (
    <div style={{ color: T.red, fontSize: 11, padding: 12 }}>Error: {error}</div>
  );

  const cov = data?.data_coverage;
  const sm  = data?.coaching_summary;

  return (
    <div style={{ padding: '4px 0' }}>
      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14,
        background: T.panelAlt, borderRadius: 8, padding: '10px 12px',
        border: `1px solid ${T.border}` }}>
        {inp('From', dateFrom, setDateFrom, 'date')}
        {inp('To',   dateTo,   setDateTo,   'date')}
        {sel('Source', fSource, setFSource, [['', 'All'], ['system', 'System'], ['tradzella', 'Tradzella']])}
        {inp('Instrument', fInstrument, setFInstrument, 'text', 'MGC, MNQ…')}
        {sel('Mode', fMode, setFMode, [['', 'All'], ['SCALP', 'SCALP'], ['SWING', 'SWING'], ['MICRO_SCALP', 'MICRO']])}
        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button onClick={() => { fetchCoaching(); fetchIntraday(); fetchCorrelations(); }}
            disabled={loading || intradayLoading || correlationsLoading}
            style={{ background: T.cyan + '22', border: `1px solid ${T.cyan}`, borderRadius: 5,
              color: T.cyan, padding: '5px 12px', fontSize: 11, cursor: 'pointer', fontWeight: 700 }}>
            {(loading || intradayLoading || correlationsLoading) ? '…' : 'Apply'}
          </button>
        </div>
      </div>

      {data && (
        <>
          {/* TOP ROW — summary cards */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
            <SummaryCard
              title="Biggest Leak"
              value={sm?.biggest_leak
                ? rFmt(sm.biggest_leak.net_r)
                : '—'}
              sub={sm?.biggest_leak
                ? `${sm.biggest_leak.tag.replace(/_/g, ' ').toUpperCase()} · ${sm.biggest_leak.count} trades`
                : 'No reviewed mistakes yet'}
              col={sm?.biggest_leak ? T.red : T.txtMuted}
            />
            <SummaryCard
              title="Best Habit"
              value={sm?.best_habit ? rFmt(sm.best_habit.avg_r) : '—'}
              sub={sm?.best_habit
                ? `${sm.best_habit.tag.replace(/_/g, ' ').toUpperCase()} · ${sm.best_habit.count} trades`
                : 'No positive tags yet'}
              col={sm?.best_habit ? T.green : T.txtMuted}
            />
            <SummaryCard
              title="Discipline Trend"
              value={sm?.discipline_trend ?? 'INSUFFICIENT DATA'}
              col={_TREND_COLOR[sm?.discipline_trend ?? ''] ?? T.txtMuted}
            />
            <SummaryCard
              title="Review Coverage"
              value={cov ? `${cov.reviewed}/${cov.total}` : '—'}
              sub={cov ? `${cov.excluded} excluded · ${cov.unreviewed} pending — ${cov.confidence.replace(/_/g, ' ')}` : ''}
              col={cov ? (_CONFIDENCE_COLOR[cov.confidence] ?? T.txtMuted) : T.txtMuted}
            />
          </div>

          {/* Imported-trades callout — shown whenever source is All or Tradzella */}
          {(!fSource || fSource === 'tradzella') && (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8,
              background: '#f5a62318', border: `1px solid #f5a62355`,
              borderRadius: 7, padding: '8px 12px', marginBottom: 12, fontSize: 11 }}>
              <span style={{ fontSize: 14, lineHeight: 1 }}>📥</span>
              <div>
                <span style={{ color: '#f5a623', fontWeight: 700 }}>Imported trades require a review to appear here. </span>
                <span style={{ color: T.txtMuted }}>
                  Go to <strong style={{ color: T.txtPri }}>Trade Log</strong>, filter Source → <strong style={{ color: T.txtPri }}>Tradzella</strong>,
                  open each trade, fill in quality ratings + tags, and save. Only trades with status <strong style={{ color: T.txtPri }}>REVIEWED</strong> feed coaching insights.
                </span>
              </div>
            </div>
          )}

          {/* SECOND ROW — Mistakes + Behaviors */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
            {/* Costliest Mistakes */}
            <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px',
              border: `1px solid ${T.border}` }}>
              <div style={{ fontSize: 9, color: T.red, fontWeight: 700, letterSpacing: '0.06em',
                marginBottom: 8 }}>COSTLIEST MISTAKES</div>
              {!data.costliest_mistakes.length ? noData('No mistake tags on reviewed trades yet') : (
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <TableHead cols={['Mistake', 'N', 'Net R', 'Avg R', 'Win%', '']} />
                  <tbody>
                    {data.costliest_mistakes.slice(0, 8).map((m, i) => {
                      const doDrill = () => onDrill && onDrill(_mkDrill(
                        `Mistake: ${m.tag.replace(/_/g,' ').toUpperCase()}`, m.n,
                        { mistake_tag: m.tag.toLowerCase() }));
                      return (
                      <tr key={i} tabIndex={onDrill ? 0 : -1} role={onDrill ? 'button' : undefined}
                        aria-label={onDrill ? `View ${m.n} trades tagged ${m.tag}` : undefined}
                        onClick={doDrill} onKeyDown={_rowKey(doDrill)}
                        onMouseEnter={() => { setHovSec('mst'); setHovIdx(i); }}
                        onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                        style={_rowSt('mst', i)}>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, color: T.txtSec }}>
                          {m.tag.replace(/_/g, ' ').toLowerCase()}
                        </td>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, fontFamily: T.mono, color: T.txtMuted }}>{m.n}</td>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, fontFamily: T.mono, color: rC(m.net_r), fontWeight: 700 }}>
                          {rFmt(m.net_r)}
                        </td>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, fontFamily: T.mono, color: rC(m.avg_r) }}>
                          {rFmt(m.avg_r)}
                        </td>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, color: T.txtMuted }}>
                          {m.win_rate != null ? m.win_rate.toFixed(0) + '%' : '—'}
                        </td>
                        <td style={{ padding: '3px 0' }}>{_lastCell('mst', i, m.n, m.confidence, doDrill)}</td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 6, fontStyle: 'italic' }}>
                Association only — not causal attribution.
              </div>
            </div>

            {/* Best Behaviors */}
            <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px',
              border: `1px solid ${T.border}` }}>
              <div style={{ fontSize: 9, color: T.green, fontWeight: 700, letterSpacing: '0.06em',
                marginBottom: 8 }}>BEST BEHAVIORS</div>
              {!data.best_behaviors.length ? noData('No positive tags on reviewed trades yet') : (
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <TableHead cols={['Behavior', 'N', 'Avg R', 'Win%', 'Plan%', '']} />
                  <tbody>
                    {data.best_behaviors.slice(0, 8).map((b, i) => {
                      const doDrill = () => onDrill && onDrill(_mkDrill(
                        `Behavior: ${b.tag.replace(/_/g,' ').toUpperCase()}`, b.n,
                        { positive_tag: b.tag.toLowerCase() }));
                      return (
                      <tr key={i} tabIndex={onDrill ? 0 : -1} role={onDrill ? 'button' : undefined}
                        aria-label={onDrill ? `View ${b.n} trades with behavior ${b.tag}` : undefined}
                        onClick={doDrill} onKeyDown={_rowKey(doDrill)}
                        onMouseEnter={() => { setHovSec('beh'); setHovIdx(i); }}
                        onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                        style={_rowSt('beh', i)}>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, color: T.txtSec }}>
                          {b.tag.replace(/_/g, ' ').toLowerCase()}
                        </td>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, fontFamily: T.mono, color: T.txtMuted }}>{b.n}</td>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, fontFamily: T.mono, color: rC(b.avg_r), fontWeight: 700 }}>
                          {rFmt(b.avg_r)}
                        </td>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, color: T.txtMuted }}>
                          {b.win_rate != null ? b.win_rate.toFixed(0) + '%' : '—'}
                        </td>
                        <td style={{ padding: '3px 10px 3px 0', fontSize: 9, color: T.txtMuted }}>
                          {b.plan_follow_pct != null ? b.plan_follow_pct.toFixed(0) + '%' : '—'}
                        </td>
                        <td style={{ padding: '3px 0' }}>{_lastCell('beh', i, b.n, b.confidence, doDrill)}</td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* Followed Plan comparison */}
          <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px',
            border: `1px solid ${T.border}`, marginBottom: 12 }}>
            <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.06em',
              marginBottom: 8 }}>FOLLOWED PLAN COMPARISON</div>
            {!data.followed_plan_analytics.length ? noData('No reviewed trades with plan status yet') : (
              <>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <TableHead cols={['Plan', 'N', 'Net R', 'Avg R', 'Win%', 'Setup', 'Exec', 'Disc']} />
                  <tbody>
                    {data.followed_plan_analytics.map((p, i) => {
                      const col = _PLAN_COLOR[p.followed_plan ?? ''] ?? T.txtMuted;
                      const doDrill = () => onDrill && onDrill(_mkDrill(
                        `Plan: ${(p.followed_plan || 'N/A').replace(/_/g,' ')}`, p.n,
                        { followed_plan: p.followed_plan || undefined }));
                      return (
                        <tr key={i} tabIndex={onDrill ? 0 : -1} role={onDrill ? 'button' : undefined}
                          aria-label={onDrill ? `View ${p.n} trades with plan=${p.followed_plan}` : undefined}
                          onClick={doDrill} onKeyDown={_rowKey(doDrill)}
                          onMouseEnter={() => { setHovSec('pln'); setHovIdx(i); }}
                          onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                          style={_rowSt('pln', i)}>
                          <td style={{ padding: '4px 10px 4px 0', fontSize: 10, color: col, fontWeight: 700 }}>
                            {(p.followed_plan || 'N/A').replace(/_/g, ' ')}
                          </td>
                          <td style={{ padding: '4px 10px 4px 0', fontSize: 9, fontFamily: T.mono, color: T.txtMuted }}>{p.n}</td>
                          <td style={{ padding: '4px 10px 4px 0', fontSize: 9, fontFamily: T.mono, color: rC(p.net_r), fontWeight: 700 }}>{rFmt(p.net_r)}</td>
                          <td style={{ padding: '4px 10px 4px 0', fontSize: 9, fontFamily: T.mono, color: rC(p.avg_r) }}>{rFmt(p.avg_r)}</td>
                          <td style={{ padding: '4px 10px 4px 0', fontSize: 9, color: T.txtMuted }}>{p.win_rate != null ? p.win_rate.toFixed(0) + '%' : '—'}</td>
                          <td style={{ padding: '4px 10px 4px 0', fontSize: 9, color: T.txtMuted }}>{p.avg_setup?.toFixed(1) ?? '—'}</td>
                          <td style={{ padding: '4px 10px 4px 0', fontSize: 9, color: T.txtMuted }}>{p.avg_execution?.toFixed(1) ?? '—'}</td>
                          <td style={{ padding: '4px 0', fontSize: 9, color: T.txtMuted }}>{p.avg_discipline?.toFixed(1) ?? '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 6, fontStyle: 'italic' }}>
                  Association only — not causal attribution.
                </div>
              </>
            )}
          </div>

          {/* THIRD ROW — Strategy + Session */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
            {/* Strategy coaching */}
            <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px',
              border: `1px solid ${T.border}` }}>
              <div style={{ fontSize: 9, color: T.cyan, fontWeight: 700, letterSpacing: '0.06em',
                marginBottom: 8 }}>STRATEGY COACHING</div>
              {!data.strategy_coaching.length ? noData('No reviewed trades yet') : (
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <TableHead cols={['Strategy', 'N', 'Avg R', 'Win%', 'Plan%', '']} />
                  <tbody>
                    {data.strategy_coaching.slice(0, 8).map((s, i) => {
                      const doDrill = () => onDrill && s.strategy_name && onDrill(_mkDrill(
                        `Strategy: ${s.strategy_name.replace(/_/g,' ')}`, s.n,
                        { strategy: s.strategy_name }));
                      return (
                      <tr key={i} tabIndex={onDrill && s.strategy_name ? 0 : -1}
                        role={onDrill && s.strategy_name ? 'button' : undefined}
                        aria-label={onDrill ? `View ${s.n} trades for strategy ${s.strategy_name}` : undefined}
                        onClick={doDrill} onKeyDown={_rowKey(doDrill)}
                        onMouseEnter={() => { setHovSec('str'); setHovIdx(i); }}
                        onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                        style={_rowSt('str', i)}>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 8, color: T.txtSec,
                          maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {s.strategy_name || '—'}
                        </td>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 9, fontFamily: T.mono, color: T.txtMuted }}>{s.n}</td>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 9, fontFamily: T.mono, color: rC(s.avg_r), fontWeight: 700 }}>{rFmt(s.avg_r)}</td>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 9, color: T.txtMuted }}>{s.win_rate != null ? s.win_rate.toFixed(0) + '%' : '—'}</td>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 9, color: T.txtMuted }}>{s.plan_follow_pct != null ? s.plan_follow_pct.toFixed(0) + '%' : '—'}</td>
                        <td style={{ padding: '3px 0' }}>{_lastCell('str', i, s.n, s.confidence, doDrill)}</td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>

            {/* Session analytics */}
            <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px',
              border: `1px solid ${T.border}` }}>
              <div style={{ fontSize: 9, color: T.amber, fontWeight: 700, letterSpacing: '0.06em',
                marginBottom: 8 }}>SESSION ANALYTICS</div>
              {!data.session_analytics.length ? noData('No reviewed trades yet') : (
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <TableHead cols={['Session', 'DOW', 'N', 'Net R', 'Win%', '']} />
                  <tbody>
                    {data.session_analytics.slice(0, 10).map((s, i) => {
                      const doDrill = () => onDrill && s.session && onDrill(_mkDrill(
                        `Session: ${s.session.replace(/_/g,' ').toUpperCase()}`, s.n,
                        { session: s.session }));
                      return (
                      <tr key={i} tabIndex={onDrill && s.session ? 0 : -1}
                        role={onDrill && s.session ? 'button' : undefined}
                        aria-label={onDrill ? `View ${s.n} trades in session ${s.session}` : undefined}
                        onClick={doDrill} onKeyDown={_rowKey(doDrill)}
                        onMouseEnter={() => { setHovSec('ses'); setHovIdx(i); }}
                        onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                        style={_rowSt('ses', i)}>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 9, color: T.txtSec }}>{s.session}</td>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 9, color: T.txtMuted }}>{_DOW_LABEL[s.dow] ?? s.dow}</td>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 9, fontFamily: T.mono, color: T.txtMuted }}>{s.n}</td>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 9, fontFamily: T.mono, color: rC(s.net_r), fontWeight: 700 }}>{rFmt(s.net_r)}</td>
                        <td style={{ padding: '3px 8px 3px 0', fontSize: 9, color: T.txtMuted }}>{s.win_rate != null ? s.win_rate.toFixed(0) + '%' : '—'}</td>
                        <td style={{ padding: '3px 0' }}>{_lastCell('ses', i, s.n, s.confidence, doDrill)}</td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* Emotion analytics */}
          {data.emotion_analytics.length > 0 && (
            <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px',
              border: `1px solid ${T.border}`, marginBottom: 12 }}>
              <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.06em',
                marginBottom: 8 }}>EMOTION ANALYTICS</div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <TableHead cols={['Emotion', 'N', 'Avg Intensity', 'Net R', 'Win%', 'Plan%', '']} />
                <tbody>
                  {data.emotion_analytics.slice(0, 8).map((e, i) => {
                    const doDrill = () => onDrill && onDrill(_mkDrill(
                      `Emotion: ${e.emotion.replace(/_/g,' ').toUpperCase()}`, e.n,
                      { emotion_tag: e.emotion.toLowerCase() }));
                    return (
                    <tr key={i} tabIndex={onDrill ? 0 : -1} role={onDrill ? 'button' : undefined}
                      aria-label={onDrill ? `View ${e.n} trades with emotion ${e.emotion}` : undefined}
                      onClick={doDrill} onKeyDown={_rowKey(doDrill)}
                      onMouseEnter={() => { setHovSec('emo'); setHovIdx(i); }}
                      onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                      style={_rowSt('emo', i)}>
                      <td style={{ padding: '3px 10px 3px 0', fontSize: 9, color: T.txtSec }}>
                        {e.emotion.replace(/_/g, ' ').toLowerCase()}
                      </td>
                      <td style={{ padding: '3px 10px 3px 0', fontSize: 9, fontFamily: T.mono, color: T.txtMuted }}>{e.n}</td>
                      <td style={{ padding: '3px 10px 3px 0', fontSize: 9, color: T.txtMuted }}>
                        {e.avg_intensity != null ? e.avg_intensity.toFixed(1) : '—'}
                      </td>
                      <td style={{ padding: '3px 10px 3px 0', fontSize: 9, fontFamily: T.mono, color: rC(e.net_r), fontWeight: 700 }}>{rFmt(e.net_r)}</td>
                      <td style={{ padding: '3px 10px 3px 0', fontSize: 9, color: T.txtMuted }}>{e.win_rate != null ? e.win_rate.toFixed(0) + '%' : '—'}</td>
                      <td style={{ padding: '3px 10px 3px 0', fontSize: 9, color: T.txtMuted }}>{e.plan_follow_pct != null ? e.plan_follow_pct.toFixed(0) + '%' : '—'}</td>
                      <td style={{ padding: '3px 0' }}>{_lastCell('emo', i, e.n, e.confidence, doDrill)}</td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Discipline trend */}
          <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px',
            border: `1px solid ${T.border}`, marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.06em' }}>
                DISCIPLINE TREND
              </div>
              <span style={{ fontSize: 9, fontWeight: 700,
                color: _TREND_COLOR[data.discipline_trend.label] ?? T.txtMuted }}>
                {data.discipline_trend.label.replace(/_/g, ' ')}
              </span>
            </div>
            {data.discipline_trend.weekly.length === 0 ? noData('Insufficient reviewed trades for trend') : (
              <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 4 }}>
                {data.discipline_trend.weekly.map((w, i) => (
                  <div key={i} style={{ minWidth: 72, textAlign: 'center',
                    background: T.panel, borderRadius: 6, padding: '6px 8px',
                    border: `1px solid ${T.border}` }}>
                    <div style={{ fontSize: 7, color: T.txtMuted, marginBottom: 3 }}>
                      {w.week_start?.slice(5) ?? '—'}
                    </div>
                    <div style={{ fontSize: 12, fontWeight: 800, fontFamily: T.mono,
                      color: w.avg_discipline != null
                        ? (w.avg_discipline >= 4 ? T.green : w.avg_discipline >= 3 ? T.amber : T.red)
                        : T.txtMuted }}>
                      {w.avg_discipline?.toFixed(1) ?? '—'}
                    </div>
                    <div style={{ fontSize: 7, color: rC(w.net_r), fontFamily: T.mono }}>
                      {rFmt(w.net_r)}
                    </div>
                    <div style={{ fontSize: 7, color: T.txtMuted }}>
                      {w.followed_plan_pct != null ? w.followed_plan_pct.toFixed(0) + '%' : '—'} plan
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Priority Focus List */}
          {data.coaching_priority.length > 0 && (
            <div style={{ background: T.panelAlt, borderRadius: 8, padding: '12px 14px',
              border: `1px solid ${T.border}`, marginBottom: 12 }}>
              <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700,
                letterSpacing: '0.06em', marginBottom: 8 }}>
                PRIORITISED NEXT-FOCUS LIST
                <span style={{ marginLeft: 8, fontSize: 7, fontWeight: 400, fontStyle: 'italic' }}>
                  priority = (|net R|×2 + count×1) × min(1, n/20)
                </span>
              </div>
              {data.coaching_priority.slice(0, 10).map((p, i) => {
                const doDrill = () => {
                  if (!onDrill) return;
                  const extra: Partial<JDrillFilter> = p.type === 'mistake'
                    ? { mistake_tag: p.tag.toLowerCase() }
                    : { positive_tag: p.tag.toLowerCase() };
                  onDrill(_mkDrill(`Priority: ${p.tag.replace(/_/g,' ').toUpperCase()}`, undefined, extra));
                };
                return (
                <div key={i} tabIndex={onDrill ? 0 : -1} role={onDrill ? 'button' : undefined}
                  aria-label={onDrill ? `Drill into priority: ${p.tag}` : undefined}
                  onClick={doDrill} onKeyDown={_rowKey(doDrill)}
                  onMouseEnter={() => { setHovSec('pri'); setHovIdx(i); }}
                  onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                  style={{ display: 'flex', gap: 10, alignItems: 'center',
                    padding: '5px 4px', borderTop: i === 0 ? 'none' : `1px solid ${T.border}`,
                    cursor: onDrill ? 'pointer' : 'default', outline: 'none', borderRadius: 4,
                    background: (hovSec === 'pri' && hovIdx === i) ? T.panelAlt : 'transparent' }}>
                  <span style={{ fontSize: 10, color: T.txtMuted, fontFamily: T.mono,
                    minWidth: 18, textAlign: 'right' }}>{i + 1}.</span>
                  <span style={{ fontSize: 9, color: T.red, fontWeight: 700, minWidth: 50, textAlign: 'center',
                    fontFamily: T.mono }}>
                    {p.score.toFixed(1)}
                  </span>
                  <span style={{ fontSize: 9, color: T.txtSec, flex: 1 }}>
                    {p.tag.replace(/_/g, ' ').toUpperCase()}
                    <span style={{ color: T.txtMuted, fontWeight: 400 }}> — {p.description}</span>
                  </span>
                  {hovSec === 'pri' && hovIdx === i && onDrill
                    ? <button onClick={e => { e.stopPropagation(); doDrill(); }}
                        style={{ fontSize: 7, background: T.cyan + '22', border: `1px solid ${T.cyan}55`,
                          borderRadius: 3, color: T.cyan, padding: '2px 5px', cursor: 'pointer',
                          fontWeight: 700 }}>OPEN</button>
                    : _confBadge(p.confidence)
                  }
                </div>
                );
              })}
            </div>
          )}

          {/* Next Focus coaching summary card */}
          {sm?.next_focus && (
            <div style={{ background: T.cyan + '11', borderRadius: 8, padding: '10px 14px',
              border: `1px solid ${T.cyan}44`, marginBottom: 12 }}>
              <div style={{ fontSize: 9, color: T.cyan, fontWeight: 700, letterSpacing: '0.06em',
                marginBottom: 4 }}>NEXT FOCUS</div>
              <div style={{ fontSize: 11, color: T.txtSec }}>
                {sm.next_focus.text}
              </div>
              <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 4, fontStyle: 'italic' }}>
                Based on current reviewed data only.
              </div>
            </div>
          )}
        </>
      )}

      {/* ── Phase 7O.2: INTRADAY BLOCK ANALYSIS ─────────────────────────── */}
      {intradayLoading && (
        <div style={{ color: T.txtMuted, fontSize: 10, padding: '8px 0', marginTop: 12 }}>
          Loading intraday analysis…
        </div>
      )}
      {!intradayLoading && intradayData && intradayData.blocks.length > 0 && (() => {
        const blocks = intradayData.blocks as any[];
        // Sort for detail table
        const sorted = [...blocks].sort((a, b) => {
          const va = a[idSort] ?? 0, vb = b[idSort] ?? 0;
          return idSortDir === 'desc' ? (vb - va) : (va - vb);
        });

        // Heatmap rows: 5 metrics × blocks
        const hmMetrics: { key: string; label: string; fmt: (v: any) => string; pos: (v: any) => boolean | null }[] = [
          { key: 'win_rate',           label: 'Win %',        fmt: v => v != null ? `${(v*100).toFixed(0)}%` : '—', pos: v => v > 0.55 ? true : v < 0.4 ? false : null },
          { key: 'net_r',              label: 'Net R',         fmt: v => v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(1)}` : '—', pos: v => v > 0 ? true : v < 0 ? false : null },
          { key: 'followed_plan_rate', label: 'Plan %',        fmt: v => v != null ? `${(v*100).toFixed(0)}%` : '—', pos: v => v > 0.7 ? true : v < 0.5 ? false : null },
          { key: 'avg_disc_quality',   label: 'Discipline',    fmt: v => v != null ? v.toFixed(1) : '—', pos: v => v != null ? v > 3.5 ? true : v < 2.5 ? false : null : null },
          { key: 'mistake_per_trade',  label: 'Mistakes/Trd',  fmt: v => v != null ? v.toFixed(1) : '—', pos: v => v != null ? v < 0.5 ? true : v > 1.0 ? false : null : null },
        ];

        const muted = (b: any) => b.confidence === 'INSUFFICIENT' || b.confidence === 'EARLY';
        const cellBg = (b: any, metric: typeof hmMetrics[0]) => {
          if (muted(b)) return T.panelAlt + 'aa';
          const v = b[metric.key];
          const p = metric.pos(v);
          if (p === true)  return T.green + '33';
          if (p === false) return T.red + '33';
          return 'transparent';
        };

        // Best/Worst highlights (from MODERATE+ blocks)
        const modBlocks = blocks.filter(b => b.confidence === 'MODERATE' || b.confidence === 'STRONG');
        const bestPerf  = modBlocks.length ? modBlocks.reduce((a, b) => b.win_rate > a.win_rate ? b : a) : null;
        const worstPerf = modBlocks.length ? modBlocks.reduce((a, b) => b.net_r < a.net_r ? b : a) : null;
        const bestDisc  = modBlocks.length ? modBlocks.reduce((a, b) => b.followed_plan_rate > a.followed_plan_rate ? b : a) : null;
        const highMist  = modBlocks.length ? modBlocks.reduce((a, b) => b.mistake_per_trade > a.mistake_per_trade ? b : a) : null;

        const thSort = (col: string) => ({
          cursor: 'pointer' as const,
          color: idSort === col ? T.cyan : T.txtMuted,
          fontWeight: idSort === col ? 800 : 600,
          whiteSpace: 'nowrap' as const,
          fontSize: 8, paddingBottom: 5, paddingRight: 8,
          userSelect: 'none' as const,
        });
        const onSort = (col: string) => {
          if (idSort === col) setIdSortDir(d => d === 'desc' ? 'asc' : 'desc');
          else { setIdSort(col); setIdSortDir('desc'); }
        };

        const blockDrill = (b: any): JDrillFilter => _mkDrill(
          `Time Block ${b.label}`, b.trade_count,
          { entry_block_start: b.block_start, entry_block_end: b.block_end, display_timezone: intradayData.display_timezone }
        );

        return (
          <div style={{ marginTop: 20, borderTop: `1px solid ${T.border}55`, paddingTop: 14 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: T.cyan, letterSpacing: '0.08em', marginBottom: 12 }}>
              INTRADAY BLOCK ANALYSIS
              <span style={{ fontSize: 8, color: T.txtMuted, fontWeight: 400, marginLeft: 8 }}>
                {blocks.length} active window{blocks.length !== 1 ? 's' : ''} · {intradayData.display_timezone}
              </span>
            </div>

            {/* (A) Heatmap grid */}
            <div style={{ overflowX: 'auto', marginBottom: 14 }}>
              <table style={{ borderCollapse: 'collapse', fontSize: 8, minWidth: blocks.length * 56 }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600, paddingRight: 8,
                      fontSize: 8, whiteSpace: 'nowrap', paddingBottom: 4, minWidth: 68 }}>Metric</th>
                    {blocks.map(b => (
                      <th key={b.block_start} style={{ textAlign: 'center', color: muted(b) ? T.txtMuted + '88' : T.txtMuted,
                        fontWeight: 600, paddingBottom: 4, fontSize: 7, minWidth: 48 }}>
                        {b.block_start}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {hmMetrics.map(m => (
                    <tr key={m.key}>
                      <td style={{ color: T.txtMuted, fontSize: 8, paddingRight: 10, paddingTop: 2,
                        paddingBottom: 2, whiteSpace: 'nowrap', fontWeight: 600 }}>{m.label}</td>
                      {blocks.map(b => (
                        <td key={b.block_start}
                          onClick={onDrill ? () => onDrill(blockDrill(b)) : undefined}
                          title={`${b.label} · ${b.trade_count} trades · ${b.confidence}`}
                          style={{ background: cellBg(b, m), textAlign: 'center',
                            padding: '3px 4px', borderRadius: 3, cursor: onDrill ? 'pointer' : 'default',
                            color: muted(b) ? T.txtMuted + '77' : T.txtSec, fontSize: 8, fontFamily: T.mono }}>
                          {m.fmt(b[m.key])}
                        </td>
                      ))}
                    </tr>
                  ))}
                  {/* Trade count row */}
                  <tr>
                    <td style={{ color: T.txtMuted, fontSize: 8, paddingRight: 10, paddingTop: 2,
                      paddingBottom: 2, whiteSpace: 'nowrap', fontWeight: 600 }}>Trades</td>
                    {blocks.map(b => (
                      <td key={b.block_start}
                        onClick={onDrill ? () => onDrill(blockDrill(b)) : undefined}
                        style={{ textAlign: 'center', padding: '3px 4px', cursor: onDrill ? 'pointer' : 'default',
                          color: muted(b) ? T.txtMuted + '77' : T.txtSec, fontSize: 8, fontFamily: T.mono }}>
                        {b.trade_count}
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>

            {/* (B) Best/Worst highlight cards */}
            {modBlocks.length > 0 && (
              <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
                {bestPerf && (
                  <div onClick={onDrill ? () => onDrill(blockDrill(bestPerf)) : undefined}
                    style={{ flex: '1 1 120px', background: T.green + '18', borderRadius: 7, padding: '8px 12px',
                      border: `1px solid ${T.green}44`, cursor: onDrill ? 'pointer' : 'default', minWidth: 120 }}>
                    <div style={{ fontSize: 7, color: T.green, fontWeight: 700, letterSpacing: '0.06em',
                      marginBottom: 3 }}>BEST PERFORMANCE</div>
                    <div style={{ fontSize: 13, fontWeight: 800, fontFamily: T.mono, color: T.green }}>
                      {bestPerf.label}
                    </div>
                    <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 2 }}>
                      {(bestPerf.win_rate*100).toFixed(0)}% win · {rFmt(bestPerf.net_r)}
                    </div>
                  </div>
                )}
                {bestDisc && (
                  <div onClick={onDrill ? () => onDrill(blockDrill(bestDisc)) : undefined}
                    style={{ flex: '1 1 120px', background: T.cyan + '14', borderRadius: 7, padding: '8px 12px',
                      border: `1px solid ${T.cyan}44`, cursor: onDrill ? 'pointer' : 'default', minWidth: 120 }}>
                    <div style={{ fontSize: 7, color: T.cyan, fontWeight: 700, letterSpacing: '0.06em',
                      marginBottom: 3 }}>BEST DISCIPLINE</div>
                    <div style={{ fontSize: 13, fontWeight: 800, fontFamily: T.mono, color: T.cyan }}>
                      {bestDisc.label}
                    </div>
                    <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 2 }}>
                      {(bestDisc.followed_plan_rate*100).toFixed(0)}% plan adherence
                    </div>
                  </div>
                )}
                {worstPerf && worstPerf.net_r < 0 && (
                  <div onClick={onDrill ? () => onDrill(blockDrill(worstPerf)) : undefined}
                    style={{ flex: '1 1 120px', background: T.red + '14', borderRadius: 7, padding: '8px 12px',
                      border: `1px solid ${T.red}44`, cursor: onDrill ? 'pointer' : 'default', minWidth: 120 }}>
                    <div style={{ fontSize: 7, color: T.red, fontWeight: 700, letterSpacing: '0.06em',
                      marginBottom: 3 }}>WORST PERFORMANCE</div>
                    <div style={{ fontSize: 13, fontWeight: 800, fontFamily: T.mono, color: T.red }}>
                      {worstPerf.label}
                    </div>
                    <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 2 }}>
                      {rFmt(worstPerf.net_r)} · {(worstPerf.win_rate*100).toFixed(0)}% win
                    </div>
                  </div>
                )}
                {highMist && highMist.mistake_per_trade > 0 && (
                  <div onClick={onDrill ? () => onDrill(blockDrill(highMist)) : undefined}
                    style={{ flex: '1 1 120px', background: '#f59e0b14', borderRadius: 7, padding: '8px 12px',
                      border: '1px solid #f59e0b44', cursor: onDrill ? 'pointer' : 'default', minWidth: 120 }}>
                    <div style={{ fontSize: 7, color: '#f59e0b', fontWeight: 700, letterSpacing: '0.06em',
                      marginBottom: 3 }}>HIGHEST MISTAKES</div>
                    <div style={{ fontSize: 13, fontWeight: 800, fontFamily: T.mono, color: '#f59e0b' }}>
                      {highMist.label}
                    </div>
                    <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 2 }}>
                      {highMist.mistake_per_trade.toFixed(1)} mistakes/trade
                      {highMist.top_mistake ? ` · ${highMist.top_mistake.replace(/_/g,' ').toUpperCase()}` : ''}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* (C) Sortable detail table */}
            <div style={{ overflowX: 'auto', marginBottom: 12 }}>
              <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 8 }}>
                <thead>
                  <tr>
                    {[
                      ['label','Time Block'], ['trade_count','Trades'], ['reviewed_count','Rev.'],
                      ['win_rate','Win %'], ['net_r','Net R'], ['avg_r','Avg R'],
                      ['profit_factor','PF'], ['followed_plan_rate','Plan %'],
                      ['avg_disc_quality','Disc.'], ['top_mistake','Top Mistake'], ['confidence','Conf.'],
                    ].map(([col, hdr]) => (
                      <th key={col} style={thSort(col)} onClick={() => onSort(col)}>
                        {hdr}{idSort === col ? (idSortDir === 'desc' ? ' ↓' : ' ↑') : ''}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((b, i) => (
                    <tr key={b.block_start}
                      onClick={onDrill ? () => onDrill(blockDrill(b)) : undefined}
                      onMouseEnter={() => { setHovSec('intra'); setHovIdx(i); }}
                      onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                      style={{ borderTop: `1px solid ${T.border}`,
                        background: hovSec === 'intra' && hovIdx === i ? T.panelAlt : 'transparent',
                        cursor: onDrill ? 'pointer' : 'default' }}>
                      <td style={{ paddingTop: 4, paddingBottom: 4, paddingRight: 8, fontFamily: T.mono,
                        color: muted(b) ? T.txtMuted + '99' : T.txtSec, fontWeight: 700 }}>{b.label}</td>
                      <td style={{ textAlign: 'right', paddingRight: 8, fontFamily: T.mono, color: T.txtSec }}>{b.trade_count}</td>
                      <td style={{ textAlign: 'right', paddingRight: 8, fontFamily: T.mono, color: T.txtMuted }}>{b.reviewed_count}</td>
                      <td style={{ textAlign: 'right', paddingRight: 8, fontFamily: T.mono,
                        color: b.win_rate > 0.55 ? T.green : b.win_rate < 0.4 ? T.red : T.txtSec }}>
                        {(b.win_rate * 100).toFixed(0)}%
                      </td>
                      <td style={{ textAlign: 'right', paddingRight: 8, fontFamily: T.mono, color: rC(b.net_r) }}>
                        {rFmt(b.net_r)}
                      </td>
                      <td style={{ textAlign: 'right', paddingRight: 8, fontFamily: T.mono, color: rC(b.avg_r) }}>
                        {rFmt(b.avg_r)}
                      </td>
                      <td style={{ textAlign: 'right', paddingRight: 8, fontFamily: T.mono, color: T.txtSec }}>
                        {b.profit_factor != null ? b.profit_factor.toFixed(2) : '—'}
                      </td>
                      <td style={{ textAlign: 'right', paddingRight: 8, fontFamily: T.mono,
                        color: b.followed_plan_rate > 0.7 ? T.green : b.followed_plan_rate < 0.5 ? T.red : T.txtSec }}>
                        {(b.followed_plan_rate * 100).toFixed(0)}%
                      </td>
                      <td style={{ textAlign: 'right', paddingRight: 8, fontFamily: T.mono, color: T.txtSec }}>
                        {b.avg_disc_quality != null ? b.avg_disc_quality.toFixed(1) : '—'}
                      </td>
                      <td style={{ paddingRight: 8, color: T.txtMuted, maxWidth: 90, overflow: 'hidden',
                        textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {b.top_mistake ? b.top_mistake.replace(/_/g,' ').toUpperCase() : '—'}
                      </td>
                      <td>{_confBadge(b.confidence)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* (D) Coaching summary sentences */}
            {intradayData.intraday_summary.length > 0 && (
              <div style={{ background: T.cyan + '0d', borderRadius: 7, padding: '10px 14px',
                border: `1px solid ${T.cyan}33`, marginTop: 4 }}>
                <div style={{ fontSize: 8, color: T.cyan, fontWeight: 700, letterSpacing: '0.06em',
                  marginBottom: 6 }}>INTRADAY COACHING INSIGHTS</div>
                {intradayData.intraday_summary.map((s: string, i: number) => (
                  <div key={i} style={{ fontSize: 10, color: T.txtSec, marginBottom: i < intradayData.intraday_summary.length - 1 ? 5 : 0 }}>
                    {s}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })()}

      {/* ── Phase 7O.3: CORRELATIONS ──────────────────────────────────────── */}
      {!correlationsLoading && correlationsData && (() => {
        const cd = correlationsData;
        // Local helper — mirrors server-side _band()
        const _bandLocal = (r: number | null): string => {
          if (r == null) return 'UNKNOWN';
          if (r <= 2) return 'LOW';
          if (r === 3) return 'MEDIUM';
          return 'HIGH';
        };

        const cov = cd.coverage ?? {};
        const hql = cd.high_quality_losses ?? {};
        const lqw = cd.low_quality_wins ?? {};
        const disc = (cd.discipline_outcomes ?? []) as any[];
        const matrix = (cd.setup_execution_matrix ?? []) as any[];
        const rm = ((cd.rating_mistake ?? []) as any[]).filter(
          (r: any) => r.rating_field === corrField
        );
        const re = ((cd.rating_emotion ?? []) as any[]).filter(
          (r: any) => r.rating_field === corrField
        );
        const combos = (cd.expensive_combinations ?? []) as any[];
        const summary = (cd.correlation_summary ?? []) as string[];

        // Worst expensive combo (most negative net_r)
        const worstCombo = combos.length > 0 ? combos[0] : null;
        // Low vs high discipline avg_r for summary card
        const discLow  = disc.find((d: any) => d.disc_rating <= 2 && (d.n ?? 0) >= 5);
        const discHigh = disc.find((d: any) => d.disc_rating >= 4 && (d.n ?? 0) >= 5);

        const rFmtC = (v: number | null) =>
          v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}R` : '—';

        // Label prettify
        const tagLabel = (t: string) => t.replace(/_/g, ' ').toUpperCase();

        // Rating × mistake/emotion: group by band for display
        const groupByBand = (rows: any[]) => {
          const out: Record<string, any[]> = { LOW: [], MEDIUM: [], HIGH: [] };
          for (const r of rows) { if (r.band && out[r.band]) out[r.band].push(r); }
          return out;
        };

        // 5×5 matrix cell lookup
        const matrixCell = (sq: number, eq: number) =>
          matrix.find((r: any) => r.setup_q === sq && r.exec_q === eq) ?? null;

        const bandColor: Record<string, string> = {
          LOW: T.red, MEDIUM: T.txtSec, HIGH: T.green,
        };

        const corrFieldLabel = corrField.replace(/_quality$/, '').replace(/_/g, ' ').toUpperCase();

        return (
          <div style={{ marginTop: 18 }}>
            {/* Section header */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              marginBottom: 10 }}>
              <div style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.1em',
                color: T.txtPri, textTransform: 'uppercase' }}>
                CORRELATIONS
              </div>
              <div style={{ fontSize: 8, color: T.txtMuted }}>
                {cov.reviewed ?? 0} reviewed · {cov.with_mistakes ?? 0} with mistakes ·
                {' '}{cov.with_emotions ?? 0} with emotions
              </div>
            </div>

            {/* TOP ROW: 4 summary cards */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              {/* High-quality losses */}
              <div
                style={{ flex: 1, background: T.green + '0d', borderRadius: 7,
                  padding: '8px 10px', border: `1px solid ${T.green}33`,
                  cursor: onDrill && hql.count > 0 ? 'pointer' : 'default' }}
                onClick={() => {
                  if (onDrill && hql.count > 0) {
                    onDrill(_mkDrill('HIGH-QUALITY LOSS', hql.count, {
                      quality_classification: 'high_quality_loss',
                    }));
                  }
                }}
              >
                <div style={{ fontSize: 7, color: T.green, fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 3 }}>
                  High-Quality Losses
                </div>
                <div style={{ fontSize: 18, fontWeight: 800, fontFamily: T.mono,
                  color: hql.count > 0 ? T.green : T.txtMuted }}>
                  {hql.count ?? 0}
                </div>
                <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 2 }}>
                  {hql.avg_r != null ? `avg ${rFmtC(hql.avg_r)}` : 'Plan followed, lost anyway'}
                </div>
              </div>

              {/* Low-quality wins */}
              <div
                style={{ flex: 1, background: '#f5a62311', borderRadius: 7,
                  padding: '8px 10px', border: '1px solid #f5a62333',
                  cursor: onDrill && lqw.count > 0 ? 'pointer' : 'default' }}
                onClick={() => {
                  if (onDrill && lqw.count > 0) {
                    onDrill(_mkDrill('LOW-QUALITY WIN', lqw.count, {
                      quality_classification: 'low_quality_win',
                    }));
                  }
                }}
              >
                <div style={{ fontSize: 7, color: '#f5a623', fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 3 }}>
                  Low-Quality Wins
                </div>
                <div style={{ fontSize: 18, fontWeight: 800, fontFamily: T.mono,
                  color: lqw.count > 0 ? '#f5a623' : T.txtMuted }}>
                  {lqw.count ?? 0}
                </div>
                <div style={{ fontSize: 8, color: T.txtMuted, marginTop: 2 }}>
                  {lqw.top_mistake ? tagLabel(lqw.top_mistake) : 'Profitable poor process'}
                </div>
              </div>

              {/* Most expensive combination */}
              <div style={{ flex: 2, background: T.red + '0d', borderRadius: 7,
                padding: '8px 10px', border: `1px solid ${T.red}33` }}>
                <div style={{ fontSize: 7, color: T.red, fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 3 }}>
                  Costliest Combination
                </div>
                {worstCombo ? (
                  <>
                    <div style={{ fontSize: 10, fontWeight: 700, fontFamily: T.mono,
                      color: T.red }}>
                      {rFmtC(worstCombo.net_r)}
                    </div>
                    <div style={{ fontSize: 8, color: T.txtSec, marginTop: 2,
                      wordBreak: 'break-word' }}>
                      {worstCombo.combo.replace(/MISTAKE:|EMOTION:|RATING:|PLAN:/g, '').replace(/_/g, ' ')}
                      {' '}· {worstCombo.n} trades
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: 9, color: T.txtMuted }}>—</div>
                )}
              </div>

              {/* Discipline impact */}
              <div style={{ flex: 1, background: T.panelAlt, borderRadius: 7,
                padding: '8px 10px', border: `1px solid ${T.border}` }}>
                <div style={{ fontSize: 7, color: T.txtMuted, fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 3 }}>
                  Discipline Impact
                </div>
                {discHigh && discLow ? (
                  <>
                    <div style={{ fontSize: 9, fontFamily: T.mono, color: T.green }}>
                      HIGH: {rFmtC(discHigh.avg_r)}
                    </div>
                    <div style={{ fontSize: 9, fontFamily: T.mono, color: T.red }}>
                      LOW: {rFmtC(discLow.avg_r)}
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: 9, color: T.txtMuted }}>Need ≥5 per band</div>
                )}
              </div>
            </div>

            {/* MIDDLE: Setup × Execution matrix + Discipline outcomes */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>

              {/* Setup × Execution 5×5 matrix */}
              {matrix.length > 0 && (
                <div style={{ flex: 3, background: T.panelAlt, borderRadius: 8,
                  padding: '10px 12px', border: `1px solid ${T.border}`, minWidth: 0 }}>
                  <div style={{ fontSize: 8, color: T.txtMuted, fontWeight: 700,
                    textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
                    Setup × Execution Matrix
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ borderCollapse: 'collapse', fontSize: 8, width: '100%' }}>
                      <thead>
                        <tr>
                          <th style={{ color: T.txtMuted, padding: '2px 5px', textAlign: 'left',
                            fontWeight: 600, whiteSpace: 'nowrap' }}>Setup ↓ / Exec →</th>
                          {[1,2,3,4,5].map(eq => (
                            <th key={eq} style={{ color: bandColor[_bandLocal(eq)], padding: '2px 5px',
                              textAlign: 'center', fontWeight: 700, whiteSpace: 'nowrap' }}>
                              EXEC {eq}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {[1,2,3,4,5].map(sq => (
                          <tr key={sq}>
                            <td style={{ color: bandColor[_bandLocal(sq)], padding: '3px 5px',
                              fontWeight: 700, whiteSpace: 'nowrap' }}>
                              SETUP {sq}
                            </td>
                            {[1,2,3,4,5].map(eq => {
                              const cell = matrixCell(sq, eq);
                              const n = cell?.n ?? 0;
                              const avgR = cell?.avg_r;
                              const conf = cell?.confidence ?? 'INSUFFICIENT_DATA';
                              const isEmpty = n === 0;
                              return (
                                <td key={eq}
                                  onClick={() => {
                                    if (!isEmpty && onDrill) {
                                      onDrill(_mkDrill(
                                        `Setup ${sq} × Exec ${eq}`, n,
                                        { rating_field: 'setup_quality', rating_min: sq, rating_max: sq,
                                          realized_r_min: undefined, realized_r_max: undefined }
                                      ));
                                    }
                                  }}
                                  style={{ padding: '4px 5px', textAlign: 'center',
                                    background: isEmpty ? 'transparent'
                                      : avgR != null && avgR >= 0 ? T.green + '18' : T.red + '18',
                                    cursor: !isEmpty && onDrill ? 'pointer' : 'default',
                                    borderTop: `1px solid ${T.border}`,
                                    opacity: conf === 'INSUFFICIENT_DATA' ? 0.5 : 1 }}>
                                  {isEmpty ? (
                                    <span style={{ color: T.txtMuted }}>—</span>
                                  ) : (
                                    <>
                                      <div style={{ fontFamily: T.mono, fontWeight: 700,
                                        color: avgR != null && avgR >= 0 ? T.green : T.red }}>
                                        {avgR != null ? rFmtC(avgR) : '—'}
                                      </div>
                                      <div style={{ color: T.txtMuted }}>{n}t</div>
                                    </>
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Discipline outcomes table */}
              {disc.length > 0 && (
                <div style={{ flex: 2, background: T.panelAlt, borderRadius: 8,
                  padding: '10px 12px', border: `1px solid ${T.border}`, minWidth: 0 }}>
                  <div style={{ fontSize: 8, color: T.txtMuted, fontWeight: 700,
                    textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
                    Discipline × Outcome
                  </div>
                  <table style={{ borderCollapse: 'collapse', fontSize: 8, width: '100%' }}>
                    <thead>
                      <tr>
                        {['Disc','n','Win%','Avg R','Plan%','Mistakes'].map(h => (
                          <th key={h} style={{ textAlign: 'left', color: T.txtMuted, fontWeight: 600,
                            paddingBottom: 5, paddingRight: 8, whiteSpace: 'nowrap' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {disc.map((d: any, i: number) => {
                        const b = _bandLocal(d.disc_rating);
                        const col = b === 'LOW' ? T.red : b === 'HIGH' ? T.green : T.txtSec;
                        return (
                          <tr key={i}
                            style={{ borderTop: `1px solid ${T.border}`,
                              cursor: onDrill ? 'pointer' : 'default' }}
                            onClick={() => {
                              if (onDrill) {
                                onDrill(_mkDrill(
                                  `Discipline ${d.disc_rating}★`, d.n,
                                  { rating_field: 'discipline_quality',
                                    rating_min: d.disc_rating, rating_max: d.disc_rating }
                                ));
                              }
                            }}>
                            <td style={{ padding: '3px 8px 3px 0', color: col, fontWeight: 700 }}>
                              {d.disc_rating}★
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtSec }}>{d.n}</td>
                            <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono,
                              color: (d.win_rate ?? 0) >= 50 ? T.green : T.red }}>
                              {d.win_rate != null ? `${d.win_rate}%` : '—'}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono,
                              color: (d.avg_r ?? 0) >= 0 ? T.green : T.red }}>
                              {rFmtC(d.avg_r)}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtSec }}>
                              {d.followed_plan_pct != null ? `${d.followed_plan_pct}%` : '—'}
                            </td>
                            <td style={{ padding: '3px 0', color: T.txtMuted }}>
                              {d.avg_mistake_count != null ? d.avg_mistake_count.toFixed(1) : '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  {cd.discipline_summary && (
                    <div style={{ fontSize: 9, color: T.txtSec, marginTop: 8,
                      fontStyle: 'italic' }}>
                      {cd.discipline_summary}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* BOTTOM: Rating × Mistake, Rating × Emotion, Combinations */}

            {/* Rating field selector */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 8, color: T.txtMuted, fontWeight: 700,
                textTransform: 'uppercase', letterSpacing: '0.06em' }}>Rating field:</span>
              {['discipline_quality','execution_quality','setup_quality','overall_quality'].map(f => (
                <button key={f} onClick={() => setCorrField(f)}
                  style={{ fontSize: 8, padding: '2px 7px', borderRadius: 4, cursor: 'pointer',
                    fontWeight: f === corrField ? 700 : 400,
                    background: f === corrField ? T.cyan + '22' : T.panelAlt,
                    border: `1px solid ${f === corrField ? T.cyan : T.border}`,
                    color: f === corrField ? T.cyan : T.txtMuted }}>
                  {f.replace(/_quality$/, '').replace(/_/g, ' ').toUpperCase()}
                </button>
              ))}
            </div>

            <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>

              {/* Rating × Mistake table */}
              <div style={{ flex: 1, background: T.panelAlt, borderRadius: 8,
                padding: '10px 12px', border: `1px solid ${T.border}`, minWidth: 0 }}>
                <div style={{ fontSize: 8, color: T.txtMuted, fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
                  {corrFieldLabel} × Mistake
                </div>
                {rm.length === 0 ? (
                  <div style={{ fontSize: 9, color: T.txtMuted }}>
                    No reviewed trades with both {corrFieldLabel.toLowerCase()} rating and mistakes.
                  </div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ borderCollapse: 'collapse', fontSize: 8, width: '100%' }}>
                      <thead>
                        <tr>
                          {['Band','Mistake','n','Band%','Win%','Net R','Plan%',''].map(h => (
                            <th key={h} style={{ textAlign: 'left', color: T.txtMuted,
                              fontWeight: 600, paddingBottom: 5, paddingRight: 8,
                              whiteSpace: 'nowrap' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rm.slice(0, 20).map((row: any, i: number) => (
                          <tr key={i}
                            style={{ borderTop: `1px solid ${T.border}`,
                              cursor: onDrill ? 'pointer' : 'default' }}
                            onMouseEnter={() => { setHovSec('rm'); setHovIdx(i); }}
                            onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                            onClick={() => {
                              if (onDrill) {
                                onDrill(_mkDrill(
                                  `${corrFieldLabel} ${row.band} × ${tagLabel(row.tag)}`,
                                  row.n,
                                  { rating_field: corrField,
                                    rating_min: row.band === 'LOW' ? 1 : row.band === 'MEDIUM' ? 3 : 4,
                                    rating_max: row.band === 'LOW' ? 2 : row.band === 'MEDIUM' ? 3 : 5,
                                    mistake_tag: row.tag }
                                ));
                              }
                            }}>
                            <td style={{ padding: '3px 8px 3px 0', fontWeight: 700,
                              color: bandColor[row.band] ?? T.txtSec }}>
                              {row.band}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtSec,
                              maxWidth: 110, overflow: 'hidden', textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap' }}>
                              {tagLabel(row.tag)}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtSec }}>{row.n}</td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtMuted }}>
                              {row.band_pct != null ? `${row.band_pct}%` : '—'}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono,
                              color: (row.win_rate ?? 0) >= 50 ? T.green : T.red }}>
                              {row.win_rate != null ? `${row.win_rate}%` : '—'}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono,
                              color: (row.net_r ?? 0) >= 0 ? T.green : T.red }}>
                              {rFmtC(row.net_r)}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtSec }}>
                              {row.followed_plan_pct != null ? `${row.followed_plan_pct}%` : '—'}
                            </td>
                            <td>{_lastCell('rm', i, row.n, row.confidence,
                              () => {
                                if (onDrill) onDrill(_mkDrill(
                                  `${corrFieldLabel} ${row.band} × ${tagLabel(row.tag)}`,
                                  row.n,
                                  { rating_field: corrField,
                                    rating_min: row.band === 'LOW' ? 1 : row.band === 'MEDIUM' ? 3 : 4,
                                    rating_max: row.band === 'LOW' ? 2 : row.band === 'MEDIUM' ? 3 : 5,
                                    mistake_tag: row.tag }
                                ));
                              }
                            )}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Rating × Emotion table */}
              <div style={{ flex: 1, background: T.panelAlt, borderRadius: 8,
                padding: '10px 12px', border: `1px solid ${T.border}`, minWidth: 0 }}>
                <div style={{ fontSize: 8, color: T.txtMuted, fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
                  {corrFieldLabel} × Emotion
                </div>
                {re.length === 0 ? (
                  <div style={{ fontSize: 9, color: T.txtMuted }}>
                    No reviewed trades with both {corrFieldLabel.toLowerCase()} rating and emotions.
                  </div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ borderCollapse: 'collapse', fontSize: 8, width: '100%' }}>
                      <thead>
                        <tr>
                          {['Band','Emotion','n','Intensity','Win%','Net R','Top Mistake',''].map(h => (
                            <th key={h} style={{ textAlign: 'left', color: T.txtMuted,
                              fontWeight: 600, paddingBottom: 5, paddingRight: 8,
                              whiteSpace: 'nowrap' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {re.slice(0, 20).map((row: any, i: number) => (
                          <tr key={i}
                            style={{ borderTop: `1px solid ${T.border}`,
                              cursor: onDrill ? 'pointer' : 'default' }}
                            onMouseEnter={() => { setHovSec('re'); setHovIdx(i); }}
                            onMouseLeave={() => { setHovSec(null); setHovIdx(-1); }}
                            onClick={() => {
                              if (onDrill) {
                                onDrill(_mkDrill(
                                  `${corrFieldLabel} ${row.band} × ${tagLabel(row.emotion)}`,
                                  row.n,
                                  { rating_field: corrField,
                                    rating_min: row.band === 'LOW' ? 1 : row.band === 'MEDIUM' ? 3 : 4,
                                    rating_max: row.band === 'LOW' ? 2 : row.band === 'MEDIUM' ? 3 : 5,
                                    emotion_tag: row.emotion }
                                ));
                              }
                            }}>
                            <td style={{ padding: '3px 8px 3px 0', fontWeight: 700,
                              color: bandColor[row.band] ?? T.txtSec }}>
                              {row.band}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtSec,
                              maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap' }}>
                              {tagLabel(row.emotion)}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtSec }}>{row.n}</td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtMuted }}>
                              {row.avg_intensity != null ? row.avg_intensity.toFixed(1) : '—'}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono,
                              color: (row.win_rate ?? 0) >= 50 ? T.green : T.red }}>
                              {row.win_rate != null ? `${row.win_rate}%` : '—'}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', fontFamily: T.mono,
                              color: (row.net_r ?? 0) >= 0 ? T.green : T.red }}>
                              {rFmtC(row.net_r)}
                            </td>
                            <td style={{ padding: '3px 8px 3px 0', color: T.txtMuted,
                              maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap' }}>
                              {row.top_mistake
                                ? `${tagLabel(row.top_mistake)} (${row.top_mistake_pct ?? '?'}%)`
                                : '—'}
                            </td>
                            <td>{_lastCell('re', i, row.n, row.confidence,
                              () => {
                                if (onDrill) onDrill(_mkDrill(
                                  `${corrFieldLabel} ${row.band} × ${tagLabel(row.emotion)}`,
                                  row.n,
                                  { rating_field: corrField,
                                    rating_min: row.band === 'LOW' ? 1 : row.band === 'MEDIUM' ? 3 : 4,
                                    rating_max: row.band === 'LOW' ? 2 : row.band === 'MEDIUM' ? 3 : 5,
                                    emotion_tag: row.emotion }
                                ));
                              }
                            )}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>

            {/* Expensive combinations */}
            {combos.length > 0 && (
              <div style={{ background: T.panelAlt, borderRadius: 8,
                padding: '10px 12px', border: `1px solid ${T.border}`, marginBottom: 12 }}>
                <div style={{ fontSize: 8, color: T.txtMuted, fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
                  Expensive Combinations
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ borderCollapse: 'collapse', fontSize: 8, width: '100%' }}>
                    <thead>
                      <tr>
                        {['Combination','n','Net R','Avg R','Win%','Recent',''].map(h => (
                          <th key={h} style={{ textAlign: 'left', color: T.txtMuted,
                            fontWeight: 600, paddingBottom: 5, paddingRight: 10,
                            whiteSpace: 'nowrap' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {combos.slice(0, 10).map((c: any, i: number) => (
                        <tr key={i} style={{ borderTop: `1px solid ${T.border}` }}>
                          <td style={{ padding: '3px 10px 3px 0', color: T.txtSec,
                            maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap' }}>
                            {c.combo.replace(/MISTAKE:|EMOTION:|RATING:|PLAN:NO/g, (m: string) =>
                              m === 'PLAN:NO' ? 'NO-PLAN ' : '').replace(/_/g, ' ')}
                          </td>
                          <td style={{ padding: '3px 10px 3px 0', color: T.txtSec }}>{c.n}</td>
                          <td style={{ padding: '3px 10px 3px 0', fontFamily: T.mono,
                            color: (c.net_r ?? 0) >= 0 ? T.green : T.red }}>
                            {rFmtC(c.net_r)}
                          </td>
                          <td style={{ padding: '3px 10px 3px 0', fontFamily: T.mono,
                            color: (c.avg_r ?? 0) >= 0 ? T.green : T.red }}>
                            {rFmtC(c.avg_r)}
                          </td>
                          <td style={{ padding: '3px 10px 3px 0', fontFamily: T.mono,
                            color: (c.win_rate ?? 0) >= 50 ? T.green : T.red }}>
                            {c.win_rate != null ? `${c.win_rate}%` : '—'}
                          </td>
                          <td style={{ padding: '3px 10px 3px 0', color: T.txtMuted }}>
                            {(c.recent ?? []).slice(0, 2).join(', ') || '—'}
                          </td>
                          <td>{_confBadge(c.confidence)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Coaching summary sentences */}
            {summary.length > 0 && (
              <div style={{ background: T.cyan + '0d', borderRadius: 7,
                padding: '10px 14px', border: `1px solid ${T.cyan}33` }}>
                <div style={{ fontSize: 8, color: T.cyan, fontWeight: 700,
                  letterSpacing: '0.06em', marginBottom: 6 }}>
                  CORRELATION INSIGHTS
                </div>
                {summary.map((s: string, i: number) => (
                  <div key={i} style={{ fontSize: 10, color: T.txtSec,
                    marginBottom: i < summary.length - 1 ? 5 : 0 }}>
                    {s}
                  </div>
                ))}
              </div>
            )}

            {(cd.coverage?.reviewed ?? 0) === 0 && (
              <div style={{ fontSize: 9, color: T.txtMuted, marginTop: 6 }}>
                No reviewed trades in the selected range. Review trades to unlock correlation analytics.
              </div>
            )}
          </div>
        );
      })()}

      {!data && !loading && !error && (
        <div style={{ color: T.txtMuted, fontSize: 11 }}>No coaching data loaded.</div>
      )}
    </div>
  );
};

// ── Journal Full Page (outer shell) — Phase 7O.1 drill-down state + URL sync ──
const JournalFullPage: React.FC = () => {
  // Initialise from URL so refresh / shared links restore state
  const _init = _urlReadJState();
  const [tab,        setTab]        = useState<JTab>(_init.tab || 'trades');
  const [drillFilter, setDrillFilter] = useState<JDrillFilter | null>(_init.drill);
  const [queueBadge, setQueueBadge] = useState<number | null>(null);

  // Keep the Review Queue badge count fresh when the page mounts and when
  // the user switches to the Trades tab (so a just-completed review is reflected).
  const refreshBadge = useCallback(async () => {
    try {
      const r = await fetch('/api/journal/review-queue', { headers: getAuthHeader() });
      const d = await r.json();
      if (d.ok) setQueueBadge(d.unreviewed_count ?? 0);
    } catch { /* fail silently — badge is cosmetic */ }
  }, []);

  useEffect(() => { refreshBadge(); }, [refreshBadge]);

  // Sync URL when tab or drill changes (replaceState — no history entry)
  useEffect(() => {
    _urlSetJState(tab, drillFilter, false);
  }, [tab, drillFilter]);

  // Handle browser Back / Forward
  useEffect(() => {
    const handler = () => {
      const { tab: t, drill: d } = _urlReadJState();
      if (t) setTab(t);
      setDrillFilter(d);
    };
    window.addEventListener('popstate', handler);
    return () => window.removeEventListener('popstate', handler);
  }, []);

  /** Drill from coaching → trades: pushState so Back returns to coaching */
  const handleDrill = useCallback((f: JDrillFilter) => {
    setDrillFilter(f);
    setTab('trades');
    _urlSetJState('trades', f, true);
  }, []);

  const handleClearDrill = useCallback(() => {
    setDrillFilter(null);
  }, []);

  const handleClearOneDrill = useCallback((key: keyof JDrillFilter) => {
    setDrillFilter(prev => {
      if (!prev) return null;
      const next = { ...prev } as JDrillFilter;
      // Remove rating_value together with rating_field
      if (key === 'rating_field') { delete next.rating_field; delete next.rating_value; }
      // Phase 7O.2: clearing any block key removes all three together
      else if (key === 'entry_block_start' || key === 'entry_block_end' || key === 'display_timezone') {
        delete next.entry_block_start; delete next.entry_block_end; delete next.display_timezone;
      }
      // Phase 7O.3: clearing rating_min/max removes both + rating_field (band drill-down)
      else if (key === 'rating_min' || key === 'rating_max') {
        delete next.rating_min; delete next.rating_max; delete next.rating_field;
      }
      else { (next as unknown as Record<string, unknown>)[key as string] = undefined; }
      // If no server-side filter keys remain, clear the whole drill
      if (_DRILL_SERVER_KEYS.every(k => !next[k])) return null;
      return next;
    });
  }, []);

  const handleTabChange = (t: JTab) => {
    setTab(t);
    // Refresh badge when leaving the queue tab (reviews may have been completed)
    if (t === 'trades') refreshBadge();
    // Clear drill when operator manually clicks a tab
    if (t !== 'trades') setDrillFilter(null);
  };

  return (
    <div className="mb-journal-page" style={{ background: T.panel, borderRadius: 10, border: `1px solid ${T.border}`,
      padding: 16, minHeight: 400 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: T.txtPri, letterSpacing: '0.04em' }}>
          Journal
        </div>
        <div style={{ fontSize: 9, color: T.txtMuted }}>Phase 7O</div>
      </div>
      <JTabBar active={tab} onChange={handleTabChange} queueBadge={queueBadge} />
      {tab === 'trades'      && <JTradesTab
        drillFilter={drillFilter}
        onClearDrill={handleClearDrill}
        onClearOneDrill={handleClearOneDrill}
        onGoCoaching={() => { setDrillFilter(null); setTab('coaching'); }}
      />}
      {tab === 'queue'       && <JReviewQueueTab onOpenReview={undefined} />}
      {tab === 'import'      && <JImportTab />}
      {tab === 'analytics'   && <JAnalyticsTab />}
      {tab === 'playbook'    && <JPlaybookTab />}
      {tab === 'learning'    && <JLearningTab />}
      {tab === 'directional' && <JDirectionalTab />}
      {tab === 'coaching'    && <JCoachingTab onDrill={handleDrill} />}
    </div>
  );
};

// ── Journal Panel ─────────────────────────────────────────────────────────────
const JournalPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const jnl   = (p.journal ?? {}) as Record<string, unknown>;
  const avail = jnl.available !== false;
  const trades = Array.isArray(jnl.recent_closed) ? jnl.recent_closed as Record<string, unknown>[] : [];

  return (
    <Panel title="Journal">
      {!avail ? <UnavailableNote /> : (
        <>
          <div style={{ display:'flex', gap:16, marginBottom:12 }}>
            {jnl.today_count != null && (
              <div style={{ textAlign:'center' }}>
                <div style={{ fontSize:20, fontWeight:800, color:T.cyan, fontFamily:T.mono }}>{String(jnl.today_count)}</div>
                <div style={{ fontSize:9, color:T.txtMuted }}>TODAY</div>
              </div>
            )}
            {jnl.today_win_rate != null && (
              <div style={{ textAlign:'center' }}>
                <div style={{ fontSize:20, fontWeight:800, color:T.green, fontFamily:T.mono }}>{fmtNum(jnl.today_win_rate, 0)}%</div>
                <div style={{ fontSize:9, color:T.txtMuted }}>WIN RATE</div>
              </div>
            )}
            {jnl.today_avg_r != null && (
              <div style={{ textAlign:'center' }}>
                <div style={{ fontSize:20, fontWeight:800, color:T.amber, fontFamily:T.mono }}>{fmtNum(jnl.today_avg_r)}R</div>
                <div style={{ fontSize:9, color:T.txtMuted }}>AVG R</div>
              </div>
            )}
          </div>

          {trades.length === 0 ? (
            <div style={{ fontSize:11, color:T.txtMuted, textAlign:'center', padding:'8px 0' }}>No closed trades</div>
          ) : (
            <div style={{ overflowX:'auto' }}>
              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:10 }} aria-label="Recent closed trades">
                <thead>
                  <tr>
                    {['Time','Inst','Dir','Setup','Result','R'].map(h => (
                      <th key={h} style={{ textAlign:'left', color:T.txtMuted, fontWeight:600, paddingBottom:5, fontSize:9, letterSpacing:'0.07em', whiteSpace:'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {trades.slice(0, 10).map((t, i) => {
                    const r = safeNum(t.r_multiple);
                    const rCol = r == null ? T.txtSec : r > 0 ? T.green : T.red;
                    return (
                      <tr key={i} style={{ borderTop:`1px solid ${T.border}` }}>
                        <td style={{ padding:'4px 0', color:T.txtMuted, whiteSpace:'nowrap' }}>{fmtTs(t.closed_at)}</td>
                        <td style={{ padding:'4px 6px 4px 0', color:T.cyan, fontFamily:T.mono, fontWeight:700 }}>{safeStr(t.instrument, '—')}</td>
                        <td style={{ color:dirColor(t.direction) }}>{safeStr(t.direction, '—').substring(0,5)}</td>
                        <td style={{ color:T.txtSec, maxWidth:80, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{safeStr(t.setup, '—')}</td>
                        <td style={{ color: String(t.result ?? '').toLowerCase() === 'win' ? T.green : T.red }}>{safeStr(t.result, '—').toUpperCase()}</td>
                        <td style={{ color:rCol, fontFamily:T.mono, fontWeight:700 }}>{r != null ? (r >= 0 ? '+' : '') + fmtNum(r) : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Panel>
  );
};

// ── Decision Timeline Panel ───────────────────────────────────────────────────
// ── THESIS_TRANSITION structured detail renderer (Part 3) ────────────────────
const ThesisTransitionDetail: React.FC<{ d: Record<string, unknown> }> = ({ d }) => {
  const fields: { label: string; value: string | null }[] = [
    { label: 'Previous', value: d.prev_status != null ? String(d.prev_status) : null },
    { label: 'Direction', value: d.direction != null ? String(d.direction) : null },
    { label: 'Reason',    value: d.primary_reason != null ? String(d.primary_reason) : null },
    { label: 'Confidence', value: d.new_confidence != null
        ? (d.prev_confidence != null ? `${d.prev_confidence} → ${d.new_confidence}` : String(d.new_confidence))
        : null },
  ].filter(f => f.value != null && f.value !== '');

  if (fields.length === 0) return null;

  return (
    <div style={{ display:'flex', flexWrap:'wrap', gap:'4px 12px', marginTop:4 }}>
      {fields.map(f => (
        <span key={f.label} style={{ fontSize:9.5, color:T.txtSec }}>
          <span style={{ color:T.txtMuted }}>{f.label}: </span>{f.value}
        </span>
      ))}
    </div>
  );
};

const TimelinePanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const tl    = (p.decision_timeline ?? {}) as Record<string, unknown>;
  const avail = tl.available !== false;
  const rawEvents = Array.isArray(tl.events) ? tl.events as Record<string, unknown>[] : [];

  // Part 5 — deduplicate by stable fingerprint: exact duplicates from the same
  // API payload share event_type + timestamp + event_label and should not be
  // shown twice. Genuinely separate events at different timestamps are kept.
  const seen = new Set<string>();
  const events = rawEvents.filter(e => {
    const fp = `${e.event_type}::${e.timestamp}::${e.event_label}`;
    if (seen.has(fp)) return false;
    seen.add(fp);
    return true;
  });

  return (
    <Panel title="Decision Timeline" badge={<Badge label="PARTIAL" color={T.amber} />}>
      {!avail ? <UnavailableNote /> : (
        <>
          <div style={{ marginBottom:8, fontSize:10, color:T.amber }}>
            Partial timeline — additional event capture is planned.
          </div>
          {events.length === 0 ? (
            <UnavailableNote msg="No events recorded this session" />
          ) : (
            <div>
              {events.map((e, i) => {
                const isThesisTransition = e.event_type === 'THESIS_TRANSITION';
                const details = e.details as Record<string, unknown> | null | undefined;

                return (
                  <div key={i} style={{ display:'flex', gap:10, paddingBottom:10, position:'relative' }}>
                    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', flexShrink:0 }}>
                      <div style={{ width:8, height:8, borderRadius:'50%', background:T.cyan, marginTop:2 }} />
                      {i < events.length - 1 && <div style={{ width:1, flex:1, background:T.border, marginTop:3 }} />}
                    </div>
                    <div style={{ flex:1, minWidth:0, paddingBottom:4 }}>
                      <div style={{ display:'flex', gap:6, alignItems:'center', marginBottom:2 }}>
                        <span style={{ fontSize:10, fontWeight:700, color:T.txtPri }}>
                          {safeStr(e.event_label, safeStr(e.event_type, '—'))}
                        </span>
                        {e.is_derived != null && e.is_derived && <Badge label="derived" color={T.txtMuted} />}
                      </div>
                      <div style={{ fontSize:9.5, color:T.txtMuted }}>
                        {fmtTs(e.timestamp)} · {safeStr(e.source, '—')}
                      </div>

                      {/* Part 3 — structured THESIS_TRANSITION layout */}
                      {isThesisTransition && details != null
                        ? <ThesisTransitionDetail d={details} />
                        /* Part 2 — generic formatter for all other event types */
                        : details != null && (() => {
                            const txt = fmtEventDetail(details);
                            return txt
                              ? <div style={{ fontSize:10, color:T.txtSec, marginTop:2 }}>{txt}</div>
                              : null;
                          })()
                      }
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </Panel>
  );
};

// ── Alerts Panel ──────────────────────────────────────────────────────────────
const AlertsPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const al    = (p.alerts ?? {}) as Record<string, unknown>;
  const avail = al.available !== false;
  const items = Array.isArray(al.items) ? al.items as Record<string, unknown>[] : [];

  return (
    <Panel title="Live Alerts Feed">
      {!avail ? <UnavailableNote /> : items.length === 0 ? (
        <UnavailableNote msg="No alerts" />
      ) : (
        <div style={{ maxHeight:240, overflowY:'auto' }}>
          {items.map((a, i) => {
            const sev = String(a.severity ?? a.alert_type ?? '').toLowerCase();
            const col = /ready/.test(sev) ? T.green : /warn|early/.test(sev) ? T.amber : T.txtSec;
            return (
              <div key={i} style={{ display:'flex', gap:8, padding:'5px 0', borderBottom:`1px solid ${T.border}` }}>
                <span style={{ fontSize:9, color:T.txtMuted, whiteSpace:'nowrap', minWidth:52 }}>{fmtTs(a.timestamp)}</span>
                <span style={{ fontSize:9.5, fontWeight:700, color:col, fontFamily:T.mono, minWidth:36, flexShrink:0 }}>{safeStr(a.instrument, '—')}</span>
                <span style={{ fontSize:10, color:T.txtSec, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{safeStr(a.message ?? a.alert_type, '—')}</span>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
};

// ── System Health Panel ───────────────────────────────────────────────────────
const SystemHealthPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const sys   = (p.system_status ?? {}) as Record<string, unknown>;
  const avail = (p.availability ?? {}) as Record<string, unknown>;
  const errs  = Array.isArray(p.errors) ? (p.errors as Record<string, unknown>[]).filter(e => e.source) : [];

  const checks = [
    { label: 'Database',   ok: sys.db_ready },
    { label: 'Learning',   ok: sys.learning_ready },
    { label: 'Databento',  ok: sys.databento_ready },
    { label: 'Broker',     ok: sys.broker_ready },
    { label: 'Left Brain', ok: avail.left_brain },
    { label: 'Scanner',    ok: avail.strategy_scanner },
    { label: 'Coach',      ok: avail.coach },
    { label: 'Journal',    ok: avail.journal },
    { label: 'Timeline',   ok: avail.decision_timeline },
  ];

  return (
    <Panel title="System Health">
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'4px 12px' }}>
        {checks.map(c => (
          <div key={c.label} style={{ display:'flex', alignItems:'center', gap:6, padding:'3px 0' }}>
            {statusDot(c.ok == null ? null : Boolean(c.ok))}
            <span style={{ fontSize:10.5, color:T.txtSec }}>{c.label}</span>
            <span style={{ marginLeft:'auto', fontSize:9, color:c.ok ? T.green : T.red, fontWeight:700 }}>
              {c.ok ? 'OK' : c.ok == null ? '—' : 'ERR'}
            </span>
          </div>
        ))}
      </div>
      {errs.length > 0 && (
        <div style={{ marginTop:10, borderTop:`1px solid ${T.border}`, paddingTop:8 }}>
          {errs.slice(0, 5).map((e, i) => (
            <div key={i} style={{ fontSize:9.5, color:T.red, marginBottom:2 }}>
              ⚠ {safeStr(e.source)}: {safeStr(e.code)}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
};

// ── Quick Trade Bar ───────────────────────────────────────────────────────────
// Operator-override entry / exit buttons that bypass the signal gate.
//   LONG / SHORT → POST /api/manual-order  (requires MANUAL_ORDER_ENABLED=1 server-side)
//   EXIT          → POST /api/quick-exit   (broker flatten + local tracking clear)
const QuickTradeBar: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  // p.active_ticker is the canonical instrument string (e.g. "MNQ"); p.market is
  // null until a price tick arrives, so always prefer active_ticker.
  const instrument       = safeStr(p.active_ticker ?? (p.market as Record<string, unknown>)?.instrument ?? '', '');
  // Always enabled on the frontend — the /manual-order gateway enforces
  // the real gate server-side and will return an error if the flag is off.
  const manualEnabled    = true;
  const activeTrades     = Array.isArray(p.active_trades) ? p.active_trades as Record<string, unknown>[] : [];
  const hasActiveTrade   = activeTrades.length > 0;

  type LoadingKey = 'none' | 'long' | 'short' | 'exit';
  const [loading,  setLoading]  = useState<LoadingKey>('none');
  const [toast,    setToast]    = useState<{ text: string; ok: boolean } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = (text: string, ok: boolean) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ text, ok });
    toastTimer.current = setTimeout(() => setToast(null), 3500);
  };

  const handleEnter = async (direction: 'Long' | 'Short') => {
    if (!instrument || loading !== 'none') return;
    setLoading(direction === 'Long' ? 'long' : 'short');
    try {
      const r = await fetch('/api/manual-order', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
        body: JSON.stringify({ ticker: instrument, direction, contracts: 1 }),
      });
      const j = await r.json() as Record<string, unknown>;
      if (r.ok && (j.status === 'sent' || j.status === 'simulated' || j.status === 'manual_required')) {
        showToast(`${direction} ${String(j.status)} — ${instrument}`, true);
      } else {
        showToast(safeStr(j.reason ?? j.error ?? '', 'Gateway rejected').slice(0, 70), false);
      }
    } catch {
      showToast('Network error — verify at broker', false);
    } finally {
      setLoading('none');
    }
  };

  const handleExit = async () => {
    if (!instrument || loading !== 'none') return;
    setLoading('exit');
    try {
      const r = await fetch('/api/quick-exit', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
        body: JSON.stringify({ ticker: instrument }),
      });
      const j = await r.json() as Record<string, unknown>;
      if (r.ok && (j.status === 'sent' || j.status === 'simulated')) {
        const pnl = j.pnl_dollars != null ? ` · $${Number(j.pnl_dollars) >= 0 ? '+' : ''}${Number(j.pnl_dollars).toFixed(0)}` : '';
        showToast(`Exited ${instrument}${pnl}`, true);
      } else {
        showToast(safeStr(j.reason ?? j.error ?? '', 'Exit failed').slice(0, 70), false);
      }
    } catch {
      showToast('Network error — verify at broker', false);
    } finally {
      setLoading('none');
    }
  };

  const base: React.CSSProperties = {
    flex: 1, padding: '9px 0', borderRadius: 7, cursor: 'pointer',
    fontSize: 11, fontWeight: 700, letterSpacing: '0.07em',
    transition: 'opacity 0.15s, background 0.15s',
  };
  const dim: React.CSSProperties = { opacity: 0.35, cursor: 'not-allowed' };

  return (
    <div style={{ marginBottom: 12 }}>
      {toast && (
        <div style={{
          marginBottom: 6, padding: '6px 12px', borderRadius: 6, fontSize: 10.5,
          background: toast.ok ? 'rgba(34,197,94,0.10)' : 'rgba(239,68,68,0.10)',
          border:     `1px solid ${toast.ok ? 'rgba(34,197,94,0.32)' : 'rgba(239,68,68,0.32)'}`,
          color:      toast.ok ? T.green : T.red,
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <span>{toast.ok ? '✓' : '✗'}</span><span>{toast.text}</span>
        </div>
      )}
      <div style={{ display: 'flex', gap: 6 }}>
        {/* LONG */}
        <button
          onClick={() => handleEnter('Long')}
          disabled={!manualEnabled || loading !== 'none' || !instrument}
          title={!manualEnabled ? 'Set MANUAL_ORDER_ENABLED=1 to enable quick entry' : `Enter Long ${instrument}`}
          style={{
            ...base,
            background: 'rgba(34,197,94,0.10)', border: `1px solid rgba(34,197,94,0.45)`,
            color: T.green,
            ...(!manualEnabled || loading !== 'none' || !instrument ? dim : {}),
          }}
          onMouseEnter={e => { if (manualEnabled && loading === 'none') (e.currentTarget as HTMLButtonElement).style.background = 'rgba(34,197,94,0.20)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(34,197,94,0.10)'; }}
        >
          {loading === 'long' ? '…' : '▲ LONG'}
        </button>

        {/* SHORT */}
        <button
          onClick={() => handleEnter('Short')}
          disabled={!manualEnabled || loading !== 'none' || !instrument}
          title={!manualEnabled ? 'Set MANUAL_ORDER_ENABLED=1 to enable quick entry' : `Enter Short ${instrument}`}
          style={{
            ...base,
            background: 'rgba(239,68,68,0.10)', border: `1px solid rgba(239,68,68,0.45)`,
            color: T.red,
            ...(!manualEnabled || loading !== 'none' || !instrument ? dim : {}),
          }}
          onMouseEnter={e => { if (manualEnabled && loading === 'none') (e.currentTarget as HTMLButtonElement).style.background = 'rgba(239,68,68,0.20)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(239,68,68,0.10)'; }}
        >
          {loading === 'short' ? '…' : '▼ SHORT'}
        </button>

        {/* EXIT */}
        <button
          onClick={handleExit}
          disabled={!hasActiveTrade || loading !== 'none' || !instrument}
          title={!hasActiveTrade ? 'No active trade to exit' : `Flatten ${instrument} at market`}
          style={{
            ...base, flex: 0.7,
            background: 'rgba(245,158,11,0.10)', border: `1px solid rgba(245,158,11,0.45)`,
            color: T.amber,
            ...(!hasActiveTrade || loading !== 'none' || !instrument ? dim : {}),
          }}
          onMouseEnter={e => { if (hasActiveTrade && loading === 'none') (e.currentTarget as HTMLButtonElement).style.background = 'rgba(245,158,11,0.20)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(245,158,11,0.10)'; }}
        >
          {loading === 'exit' ? '…' : '✕ EXIT'}
        </button>
      </div>

      {!manualEnabled && (
        <div style={{ marginTop: 4, fontSize: 9, color: T.txtMuted, textAlign: 'center' }}>
          Quick entry off — set <span style={{ fontFamily: T.mono, color: T.txtSec }}>MANUAL_ORDER_ENABLED=1</span> env to enable
        </div>
      )}
    </div>
  );
};

// ── Ask AI message bubble ─────────────────────────────────────────────────────
function MbBubble({ msg }: { msg: MbMsg }) {
  const isBrain = msg.role === 'brain';
  const { text: shown, live } = useStream(isBrain ? msg.text : '');
  const display = isBrain ? shown : msg.text;
  return (
    <div style={{ display: 'flex', justifyContent: isBrain ? 'flex-start' : 'flex-end' }}>
      <div style={{
        maxWidth: '85%', padding: '8px 12px',
        borderRadius: isBrain ? '4px 12px 12px 12px' : '12px 4px 12px 12px',
        background: isBrain ? `${T.cyan}0a` : 'rgba(255,255,255,0.06)',
        border: `1px solid ${isBrain ? `${T.cyan}22` : 'rgba(255,255,255,0.08)'}`,
        fontSize: 12.5, lineHeight: 1.55,
        color: isBrain ? T.txtSec : 'rgba(255,255,255,0.55)',
        wordBreak: 'break-word',
      }}>
        {/* aiEsc is a pass-through — React renders as text content (XSS-safe by default) */}
        {aiEsc(display)}
        {live && <span style={{ opacity: 0.5, animation: 'mbDot 0.8s infinite' }}>▌</span>}
      </div>
    </div>
  );
}

// ── Ask AI panel ──────────────────────────────────────────────────────────────
// Slide-up modal that reuses the existing /api/assistant endpoint.
// SCOPE: display-only — never touches the gate, scoring, or execution.
// The endpoint is owner-only (Basic Auth + same-origin CSRF) — same credentials
// as every other call in this file.
const AskAiPanel: React.FC<{
  open:    boolean;
  onClose: () => void;
  ticker:  string;
  p:       Record<string, unknown>;
}> = ({ open, onClose, ticker, p }) => {
  const { context: mbMemCtx, addEntry: mbAddEntry } = useMbConvMemory();
  const [msgs,   setMsgs]   = useState<MbMsg[]>([]);
  const [input,  setInput]  = useState('');
  const [asking, setAsking] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef  = useRef<HTMLDivElement>(null);

  // Auto-scroll to latest message
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs, asking]);

  // Focus input when panel opens
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 80);
  }, [open]);

  // Close on Escape — no polling side effects
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  // Contextual chips based on current readiness
  const vdct      = (p.verdict ?? {}) as Record<string, unknown>;
  const isReady   = vdct.is_actionable === true;
  const isMgmnt   = /managing/i.test(safeStr(vdct.readiness_label ?? ''));
  const chips     = isReady
    ? ['Break down the edge.', 'What invalidates this?', 'Where is the stop?']
    : isMgmnt
    ? ['Thesis still intact?', 'Where to take partials?', 'What level flips this?']
    : ['What is missing?', 'Why is this WAIT?', 'What triggers entry?'];

  const ask = useCallback(async (q?: string) => {
    const question = (q ?? input).trim();
    if (!question || asking) return;
    setInput('');
    setMsgs(m => [...m, { id: Date.now(), role: 'user', text: question }]);
    setAsking(true);
    mbAddEntry('chat', question.slice(0, 150));

    // Prepend persona + session memory + current MB snapshot so the AI
    // knows exactly what the operator is looking at right now.
    const ctx  = buildMbContext(p, ticker);
    const fullQ = (mbMemCtx ? mbMemCtx + ctx : ctx) + question;

    try {
      const r = await fetch('/api/assistant', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
        body: JSON.stringify({ question: fullQ, ticker }),
      });
      if (r.status === 401 || r.status === 403) {
        setMsgs(m => [...m, { id: Date.now(), role: 'brain',
          text: 'Session expired — refresh the page to re-authenticate.' }]);
      } else {
        const j = await r.json() as Record<string, unknown>;
        const answer = String(j.answer || j.error || 'No response.');
        setMsgs(m => [...m, { id: Date.now(), role: 'brain', text: answer }]);
        mbAddEntry('insight', answer.slice(0, 140));
      }
    } catch {
      setMsgs(m => [...m, { id: Date.now(), role: 'brain',
        text: 'Connection error — check the backend connection.' }]);
    } finally {
      setAsking(false);
      setTimeout(() => inputRef.current?.focus(), 60);
    }
  }, [input, asking, ticker, p, mbMemCtx, mbAddEntry]);

  if (!open) return null;

  const modeLabel = safeStr(((p.market ?? {}) as Record<string, unknown>).trading_mode, '');

  return (
    <div
      role="dialog" aria-modal="true"
      aria-label="Ask AI about current setup"
      style={{
        position: 'fixed', inset: 0, zIndex: 2000,
        display: 'flex', alignItems: 'flex-end', justifyContent: 'flex-end',
        padding: '20px',
      }}
    >
      {/* Dim backdrop — click to close */}
      <div
        onClick={onClose}
        aria-hidden
        style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.45)',
          backdropFilter: 'blur(2px)',
        }}
      />

      {/* Panel */}
      <div style={{
        position: 'relative',
        width: 420, maxWidth: 'calc(100vw - 24px)',
        height: 540, maxHeight: 'calc(100vh - 80px)',
        display: 'flex', flexDirection: 'column',
        background: T.panel,
        border: `1px solid ${T.cyan}44`,
        borderRadius: 12, overflow: 'hidden',
        boxShadow: `0 24px 64px rgba(0,0,0,0.60), 0 0 0 1px ${T.cyan}18`,
        animation: 'mbAskSlideIn 0.18s ease-out',
      }}>

        {/* ── Panel header ── */}
        <div style={{
          display: 'flex', alignItems: 'center',
          padding: '11px 16px 9px',
          borderBottom: `1px solid ${T.border}`,
          background: `${T.cyan}08`, flexShrink: 0,
        }}>
          <span style={{ fontSize: 15, marginRight: 8 }}>🧠</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: T.cyan, letterSpacing: '0.06em' }}>
              ASK AI
            </div>
            <div style={{
              fontSize: 9, color: T.txtMuted, letterSpacing: '0.06em',
              marginTop: 1, fontFamily: T.mono,
            }}>
              {ticker}{modeLabel ? ` · ${modeLabel}` : ''} · read-only analysis only
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close Ask AI panel"
            style={{
              background: 'none', border: 'none', color: T.txtMuted,
              cursor: 'pointer', fontSize: 18, padding: '2px 4px', lineHeight: 1,
            }}
          >×</button>
        </div>

        {/* ── Suggestion chips ── */}
        <div style={{
          display: 'flex', gap: 6, flexWrap: 'wrap',
          padding: '8px 12px 6px',
          borderBottom: `1px solid ${T.border}`, flexShrink: 0,
        }}>
          {chips.map(c => (
            <button
              key={c}
              onClick={() => ask(c)}
              disabled={asking}
              style={{
                padding: '3px 10px', borderRadius: 14,
                border: `1px solid ${T.border}`, background: 'transparent',
                color: T.txtMuted, fontSize: 10, fontFamily: T.mono,
                cursor: asking ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap',
              }}
            >{c}</button>
          ))}
        </div>

        {/* ── Message list ── */}
        <div
          ref={listRef}
          id="mb-ask-msgs"
          style={{
            flex: 1, overflowY: 'auto',
            padding: '10px 14px',
            display: 'flex', flexDirection: 'column', gap: 8,
          }}
        >
          {msgs.length === 0 && (
            <div style={{
              flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: T.txtMuted, fontSize: 11.5, fontFamily: T.mono,
              textAlign: 'center', padding: '24px 0', lineHeight: 1.7,
            }}>
              Ask about the current {ticker} setup —<br />
              thesis, blockers, edge score, trade plan, or risk.
            </div>
          )}
          {msgs.map(m => <MbBubble key={m.id} msg={m} />)}
          {asking && (
            <div style={{ display: 'flex', gap: 5, padding: '4px 2px 2px' }}>
              {[0, 1, 2].map(i => (
                <div key={i} style={{
                  width: 6, height: 6, borderRadius: '50%', background: T.cyan,
                  animation: `mbDot 1.2s ${i * 0.2}s infinite ease-in-out`,
                }} />
              ))}
            </div>
          )}
        </div>

        {/* ── Input row ── */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 12px',
          borderTop: `1px solid ${T.border}`, flexShrink: 0,
        }}>
          <input
            ref={inputRef}
            id="mb-ask-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }}
            placeholder={`Ask about ${ticker}…`}
            disabled={asking}
            style={{
              flex: 1, background: 'rgba(255,255,255,0.04)',
              border: `1px solid ${T.border}`, borderRadius: 6,
              padding: '7px 10px', color: T.txtPri, fontSize: 12,
              fontFamily: T.mono, outline: 'none', transition: 'border-color 0.15s',
            }}
            onFocus={e  => { e.currentTarget.style.borderColor = `${T.cyan}55`; }}
            onBlur={e   => { e.currentTarget.style.borderColor = T.border; }}
          />
          <button
            onClick={() => ask()}
            disabled={!input.trim() || asking}
            aria-label="Send question"
            style={{
              background: input.trim() && !asking ? `${T.cyan}20` : 'transparent',
              border: `1px solid ${input.trim() && !asking ? `${T.cyan}55` : T.border}`,
              color: input.trim() && !asking ? T.cyan : T.txtMuted,
              borderRadius: 6, padding: '6px 14px', fontSize: 13, fontWeight: 700,
              cursor: input.trim() && !asking ? 'pointer' : 'default',
              transition: 'all 0.15s',
            }}
          >↵</button>
        </div>

        {/* ── Disclaimer ── */}
        <div style={{
          padding: '4px 14px 8px', fontSize: 9,
          color: T.txtMuted, textAlign: 'center', flexShrink: 0,
        }}>
          Analysis only — AI cannot place trades or modify positions
        </div>
      </div>
    </div>
  );
};

// ── Header ────────────────────────────────────────────────────────────────────
const Header: React.FC<{
  p: Record<string, unknown> | null;
  fetchState: FetchState;
  lastOk: number | null;
  ticker: string;
  setTicker: (t: string) => void;
  refresh: () => void;
  onAskAi: () => void;
  onOpenMenu: () => void;
  menuOpen: boolean;
}> = ({ p, fetchState, lastOk, ticker, setTicker, refresh, onAskAi, onOpenMenu, menuOpen }) => {
  const clock = useClock();
  const mkt = ((p?.market ?? {}) as Record<string, unknown>);
  const sys = ((p?.system_status ?? {}) as Record<string, unknown>);
  const allOk = !!(sys.db_ready && sys.learning_ready);
  const stale = fetchState === 'stale';
  const loading = fetchState === 'loading' || fetchState === 'refreshing';

  return (
    <header className="mb-main-header" style={{ background:'#030b1a', borderBottom:`1px solid ${T.border}`, padding:'0 20px', display:'flex', alignItems:'center', gap:12, height:52, flexShrink:0, position:'sticky', top:0, zIndex:20 }}>
      <button
        className="mb-mobile-menu-toggle"
        onClick={onOpenMenu}
        aria-label="Open navigation menu"
        aria-expanded={menuOpen}
        aria-controls="main-brain-mobile-menu"
      >
        ☰
      </button>
      {/* Brand */}
      <div className="mb-header-brand" style={{ display:'flex', alignItems:'center', gap:8 }}>
        <span style={{ fontSize:14, lineHeight:1 }}>🧠</span>
        <div>
          <div style={{ fontSize:12, fontWeight:800, color:T.txtPri, lineHeight:1 }}>Main Brain</div>
          <div style={{ fontSize:8.5, color:T.cyan, letterSpacing:'0.1em', lineHeight:1.2 }}>OPERATOR CONSOLE</div>
        </div>
      </div>

      {/* Instrument selector */}
      <div className="mb-header-tickers" style={{ marginLeft:16 }}>
        {(['MGC', 'MNQ', 'MES', 'MYM'] as const).map(t => (
          <button key={t} onClick={() => setTicker(t)} aria-pressed={ticker === t} style={{
            background: ticker === t ? `${T.cyan}20` : 'transparent',
            border: `1px solid ${ticker === t ? T.cyan : T.border}`,
            color: ticker === t ? T.cyan : T.txtMuted,
            fontSize: 10, fontWeight: 700, padding: '3px 8px', borderRadius: 5, cursor: 'pointer', marginRight: 4,
          }}>{t}</button>
        ))}
      </div>

      {/* Market context */}
      <div className="mb-header-context" style={{ display:'flex', gap:16, marginLeft:8 }}>
        <div>
          <div style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.08em' }}>TIME (UTC-4)</div>
          <div style={{ fontSize:12, fontWeight:700, color:T.txtPri, fontFamily:T.mono }}>{clock}</div>
        </div>
        <div>
          <div style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.08em' }}>SESSION</div>
          <div style={{ fontSize:12, fontWeight:700, color: /open/i.test(String(mkt.session_status)) ? T.green : T.amber }}>
            {safeStr(mkt.session_status, '—')}
          </div>
        </div>
        <div>
          <div style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.08em' }}>MODE</div>
          <div style={{ fontSize:12, fontWeight:700, color:T.blue }}>{safeStr(mkt.trading_mode, '—')}</div>
        </div>
      </div>

      {/* System status */}
      <div className="mb-header-actions" style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:12 }}>
        {stale && <span style={{ fontSize:10, color:T.amber, fontWeight:700 }}>⚠ STALE DATA</span>}
        {fetchState === 'error' && <span style={{ fontSize:10, color:T.red, fontWeight:700 }}>✗ CONNECTION ERROR</span>}
        {fetchState === 'auth_fail' && <span style={{ fontSize:10, color:T.red }}>AUTH REQUIRED — <a href="/" style={{ color:T.cyan }}>Go to login</a></span>}
        <div style={{ display:'flex', alignItems:'center', gap:5 }}>
          {statusDot(allOk ? true : null)}
          <span style={{ fontSize:9.5, color:T.txtMuted }}>
            {lastOk ? `Updated ${fmtAge(new Date(lastOk).toISOString())}` : 'Connecting…'}
          </span>
        </div>
        <button
          onClick={onAskAi}
          aria-label="Ask AI about current setup"
          style={{
            background: `${T.cyan}14`, border: `1px solid ${T.cyan}44`,
            color: T.cyan, borderRadius: 6, padding: '4px 12px',
            cursor: 'pointer', fontSize: 10, fontWeight: 700,
            letterSpacing: '0.06em', transition: 'all 0.2s',
            whiteSpace: 'nowrap',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = `${T.cyan}28`; }}
          onMouseLeave={e => { e.currentTarget.style.background = `${T.cyan}14`; }}
        >
          💬 ASK AI
        </button>
        <button onClick={refresh} disabled={loading} aria-label="Refresh data" style={{
          background:'transparent', border:`1px solid ${T.border}`, color:T.txtSec, borderRadius:6,
          padding:'4px 10px', cursor:'pointer', fontSize:10, fontWeight:600,
          opacity: loading ? 0.5 : 1, transition:'opacity 0.2s',
        }}>
          {loading ? '↻ …' : '↻ Refresh'}
        </button>

        {/* ── Page nav pill ── */}
        <div className="mb-header-page-nav" style={{ display:'flex', gap:1, borderRadius:6, border:`1px solid rgba(255,255,255,0.07)`, padding:'2px 3px', background:'rgba(255,255,255,0.020)', marginLeft:4 }}>
          <span style={{ fontSize:9.5, fontFamily:T.mono, fontWeight:700, color:T.cyan, padding:'3px 9px', borderRadius:4, background:`${T.cyan}18`, letterSpacing:'0.08em' }}>MAIN BRAIN</span>
          {([
            { label:'DASHBOARD', href:'/dashboard' },
            { label:'COCKPIT',   href:'/cockpit'   },
          ] as const).map(({ label, href }) => (
            <a key={label} href={href} style={{ fontSize:9.5, fontFamily:T.mono, color:'rgba(255,255,255,0.28)', padding:'3px 9px', borderRadius:4, textDecoration:'none', letterSpacing:'0.08em', transition:'color 0.15s' }}
              onMouseEnter={e => (e.currentTarget as HTMLAnchorElement).style.color='rgba(255,255,255,0.70)'}
              onMouseLeave={e => (e.currentTarget as HTMLAnchorElement).style.color='rgba(255,255,255,0.28)'}>
              {label}
            </a>
          ))}
          <a href="/api/dashboard" target="_blank" rel="noreferrer" style={{ fontSize:9.5, fontFamily:T.mono, color:'rgba(255,255,255,0.28)', padding:'3px 9px', borderRadius:4, textDecoration:'none', letterSpacing:'0.08em', transition:'color 0.15s' }}
            onMouseEnter={e => (e.currentTarget as HTMLAnchorElement).style.color='rgba(255,255,255,0.70)'}
            onMouseLeave={e => (e.currentTarget as HTMLAnchorElement).style.color='rgba(255,255,255,0.28)'}>
            ENGINE ↗
          </a>
          <a href="https://trading-research-lab.replit.app" target="_blank" rel="noreferrer" style={{ fontSize:9.5, fontFamily:T.mono, color:'rgba(255,255,255,0.28)', padding:'3px 9px', borderRadius:4, textDecoration:'none', letterSpacing:'0.08em', transition:'color 0.15s' }}
            onMouseEnter={e => (e.currentTarget as HTMLAnchorElement).style.color='rgba(255,255,255,0.70)'}
            onMouseLeave={e => (e.currentTarget as HTMLAnchorElement).style.color='rgba(255,255,255,0.28)'}>
            RESEARCH ↗
          </a>
          <a href="https://vwap-pullback-indicator.replit.app" target="_blank" rel="noopener noreferrer"
            title="VWAP Pullback Indicator"
            style={{ fontSize:9.5, fontFamily:T.mono, color:'rgba(56,189,248,0.72)', padding:'3px 9px', borderRadius:4, textDecoration:'none', letterSpacing:'0.08em', transition:'color 0.15s' }}
            onMouseEnter={e => (e.currentTarget as HTMLAnchorElement).style.color=T.cyan}
            onMouseLeave={e => (e.currentTarget as HTMLAnchorElement).style.color='rgba(56,189,248,0.72)'}>
            VWAP ↗
          </a>
        </div>
      </div>
    </header>
  );
};

// ── AI Summary (header region) ────────────────────────────────────────────────
const AISummary: React.FC<{ p: Record<string, unknown> | null }> = ({ p }) => {
  const mb = ((p?.main_brain ?? {}) as Record<string, unknown>);
  const voice = safeStr(mb.voice ?? mb.narrative, '');
  return (
    <div style={{ background:`${T.cyan}08`, border:`1px solid ${T.cyan}22`, borderRadius:8, padding:'8px 14px', fontSize:11, color:T.txtSec, lineHeight:1.55, fontStyle:'italic' }}>
      {voice || 'Main Brain data is current.'}
    </div>
  );
};

// ── Loading / Error states ────────────────────────────────────────────────────
const LoadingScreen: React.FC = () => (
  <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', flex:1, gap:16, color:T.txtMuted }}>
    <div style={{ fontSize:32 }}>🧠</div>
    <div style={{ fontSize:14, color:T.cyan }}>Connecting to Main Brain…</div>
    <div style={{ fontSize:11 }}>Fetching operator console data</div>
  </div>
);

const ErrorScreen: React.FC<{ msg: string | null; refresh: () => void }> = ({ msg, refresh }) => (
  <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', flex:1, gap:16, color:T.txtMuted }}>
    <div style={{ fontSize:32 }}>⚠</div>
    <div style={{ fontSize:14, color:T.red }}>Main Brain Unavailable</div>
    <div style={{ fontSize:11, maxWidth:400, textAlign:'center' }}>{msg ?? 'Could not reach the Main Brain endpoint. Ensure the bot is running.'}</div>
    <button onClick={refresh} style={{ background:`${T.cyan}20`, border:`1px solid ${T.cyan}44`, color:T.cyan, borderRadius:8, padding:'8px 20px', cursor:'pointer', fontSize:12, fontWeight:700 }}>
      Retry
    </button>
  </div>
);

// MainBrain is the primary route. Validate here so an expired local credential
// has a recovery path instead of leaving the operator on "Connecting".
const MainBrainLoginScreen: React.FC<{ onSubmit: (password: string) => Promise<boolean> }> = ({ onSubmit }) => {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setTimeout(() => inputRef.current?.focus(), 80); }, []);

  const attemptLogin = async () => {
    // Prefer the live input value so a browser-autofill or automation update
    // cannot leave React state one event behind at submit time.
    const value = inputRef.current?.value ?? password;
    if (!value || checking) return;
    setChecking(true);
    setError('');
    const accepted = await onSubmit(value);
    setChecking(false);
    if (!accepted) {
      setError('Password was not accepted. Check it and try again.');
      inputRef.current?.focus();
    }
  };

  return (
    <div className="mb-login-overlay" style={{ position:'fixed', inset:0, zIndex:10000, minHeight:'100dvh', display:'flex', alignItems:'center', justifyContent:'center', background:T.bg, color:T.txtPri, fontFamily:"'Inter',system-ui,sans-serif", padding:20 }}>
      <form className="mb-login-card" onSubmit={event => { event.preventDefault(); void attemptLogin(); }} style={{ position:'relative', zIndex:1, width:'min(360px, 100%)', display:'flex', flexDirection:'column', gap:14, background:T.panel, border:`1px solid ${T.border}`, borderRadius:12, padding:28, boxShadow:'0 24px 80px rgba(0,0,0,.34)', boxSizing:'border-box' }}>
        <div style={{ fontSize:28, lineHeight:1 }}>🧠</div>
        <div>
          <div style={{ fontSize:14, fontWeight:800, color:T.txtPri, letterSpacing:'.06em' }}>MAIN BRAIN ACCESS</div>
          <div style={{ marginTop:6, fontSize:11, color:T.txtMuted, lineHeight:1.5 }}>Enter your dashboard password to connect to the operator console.</div>
        </div>
        <input type="text" value="admin" readOnly autoComplete="username" tabIndex={-1} aria-hidden="true" style={{ display:'none' }} />
        <input ref={inputRef} type="password" value={password} onChange={event => { setPassword(event.target.value); setError(''); }}
          name="dashboard-password" placeholder="Dashboard password" autoComplete="current-password" aria-invalid={Boolean(error)}
          style={{ width:'100%', minHeight:44, boxSizing:'border-box', background:T.panelAlt, border:`1px solid ${error ? T.red : T.border}`, borderRadius:7, padding:'11px 12px', color:T.txtPri, outline:'none', fontSize:13 }} />
        {error && <div role="alert" style={{ color:T.red, fontSize:11 }}>{error}</div>}
        <button data-testid="main-brain-login-submit" type="button" onClick={() => { void attemptLogin(); }} disabled={checking} style={{ minHeight:44, background:`${T.cyan}20`, border:`1px solid ${T.cyan}55`, color:T.cyan, borderRadius:7, padding:'10px 14px', cursor:checking?'wait':'pointer', fontWeight:800, fontSize:12, opacity:checking ? .65 : 1 }}>
          {checking ? 'Checking…' : 'Connect'}
        </button>
      </form>
    </div>
  );
};

// ── Main Page ─────────────────────────────────────────────────────────────────
// ── Execution Arm Control Panel ────────────────────────────────────────────────
// Polls GET /api/execution/state every 30 s. Backend is sole source of truth —
// no localStorage for arm state. Action buttons call the same execution routes
// that the legacy Flask dashboard uses.

interface ArmStateData {
  execution_enabled: boolean;
  armed: boolean;
  effective_state: string;
  armed_at: string | null;
  expires_at: string | null;
  time_remaining_sec: number | null;
  arm_session_id: string | null;
  disarm_reason: string | null;
  last_changed_at: string | null;
  configured_mode: string;
  effective_mode: string;
  runtime_mode_override: string | null;
  trading_mode: string | null;
  safety_locked: boolean;
  safety_lock_reason: string | null;
  allowed_instruments: string[] | null;
  max_contracts: Record<string, number> | null;
  max_trades: number | null;
  trades_used: number;
  session_pnl: number;
  max_session_loss: number | null;
  allowed_strategies: string[] | null;
  direction_restriction: string | null;
  single_position_only: boolean;
  active_trade_count: number;
}

function useArmStateData() {
  const [data, setData] = useState<ArmStateData | null>(null);
  const [armErr, setArmErr] = useState<string | null>(null);

  const fetchArm = useCallback(async () => {
    try {
      const r = await fetch('/api/execution/state', {
        credentials: 'include', headers: getAuthHeader(),
      });
      if (r.ok) { setData(await r.json()); setArmErr(null); }
      else setArmErr(`HTTP ${r.status}`);
    } catch { setArmErr('Network error'); }
  }, []);

  useEffect(() => {
    fetchArm();
    const id = setInterval(fetchArm, 30_000);
    return () => clearInterval(id);
  }, [fetchArm]);

  return { armData: data, armErr, refreshArm: fetchArm };
}

const _ARM_CONFIRM_PHRASE = 'ARM LIVE AUTO TRADING';

const ArmControlPanel: React.FC = () => {
  const { armData, armErr, refreshArm } = useArmStateData();
  const [armModalOpen, setArmModalOpen] = useState(false);
  const [confirmPhrase, setConfirmPhrase]   = useState('');
  const [armDuration,   setArmDuration]     = useState('30');
  const [armMaxTrades,  setArmMaxTrades]    = useState('3');
  const [armMaxLoss,    setArmMaxLoss]      = useState('');
  const [armInstruments,setArmInstruments]  = useState('MGC');
  const [armMaxCt,      setArmMaxCt]        = useState('1');
  const [pending,       setPending]         = useState(false);
  const [modePending,   setModePending]     = useState(false);
  const [tmPending,     setTmPending]       = useState(false);
  const [actMsg,        setActMsg]          = useState<{ ok: boolean; text: string; errors?: string[] } | null>(null);
  const [modeMsg,       setModeMsg]         = useState<{ ok: boolean; text: string } | null>(null);
  const expiresAtRef = useRef<string | null>(null);
  const [countdown, setCountdown] = useState('—');

  // Live countdown from expires_at timestamp (avoids stale server-side value)
  useEffect(() => {
    if (!armData?.expires_at || !armData.armed) { setCountdown('—'); return; }
    expiresAtRef.current = armData.expires_at;
    const update = () => {
      const exp = expiresAtRef.current;
      if (!exp) { setCountdown('—'); return; }
      const s = Math.max(0, Math.floor((new Date(exp).getTime() - Date.now()) / 1000));
      if (s === 0) { setCountdown('Expired'); return; }
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      setCountdown(h > 0 ? `${h}h ${m}m` : `${m}m ${sec}s`);
    };
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, [armData?.expires_at, armData?.armed]);

  const effState = armData?.effective_state ?? 'unknown';
  const stateColor = effState === 'live_armed' ? T.green
    : effState === 'safety_locked' ? T.red
    : effState === 'live_available_disarmed' ? T.amber : T.txtMuted;
  const stateLabel: Record<string, string> = {
    live_armed:               '⊙ ARMED',
    live_available_disarmed:  '◎ DISARMED',
    safety_locked:            '⚠ SAFETY LOCKED',
    paper:                    '● PAPER',
    disabled:                 '○ DISABLED',
  };

  async function doAction(path: string, body?: object): Promise<boolean> {
    setPending(true); setActMsg(null);
    try {
      const r = await fetch(`/api/execution/${path}`, {
        method: 'POST', credentials: 'include',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      });
      const j = await r.json().catch(() => ({}));
      const errs: string[] | undefined = Array.isArray(j.errors) && j.errors.length ? j.errors : undefined;
      setActMsg({ ok: r.ok, text: j.reason ?? j.message ?? (r.ok ? 'Done' : `Error ${r.status}`), errors: errs });
      if (r.ok) { await refreshArm(); }
      return r.ok;
    } catch { setActMsg({ ok: false, text: 'Network error' }); return false; }
    finally { setPending(false); }
  }

  async function handleEnable() {
    const ok = await doAction('enable', { confirm_phrase: 'ENABLE AUTO TRADING', by: 'operator' });
    if (ok) setEnableModalOpen(false);
  }

  async function handleDisable() {
    if (!window.confirm(
      'Disable execution gateway?\n\nThis will disarm and block all new entries (auto and manual ENTER).\nExisting protective orders on open trades are not affected.'
    )) return;
    await doAction('disable', { reason: 'operator_manual', by: 'operator' });
  }

  async function handleSetGatewayMode(mode: string) {
    setModePending(true); setModeMsg(null);
    try {
      const r = await fetch('/api/execution/set-mode', {
        method: 'POST', credentials: 'include',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, by: 'operator' }),
      });
      const j = await r.json().catch(() => ({}));
      setModeMsg({ ok: r.ok, text: r.ok ? `Gateway mode set to ${mode.toUpperCase()}` : (j.reason ?? `Error ${r.status}`) });
      if (r.ok) await refreshArm();
    } catch { setModeMsg({ ok: false, text: 'Network error' }); }
    finally { setModePending(false); }
  }

  async function handleSetTradingMode(mode: string) {
    setTmPending(true); setModeMsg(null);
    try {
      const r = await fetch('/api/mode', {
        method: 'POST', credentials: 'include',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      const j = await r.json().catch(() => ({}));
      setModeMsg({ ok: r.ok, text: r.ok ? `Trading mode set to ${mode}` : (j.reason ?? `Error ${r.status}`) });
      if (r.ok) await refreshArm();
    } catch { setModeMsg({ ok: false, text: 'Network error' }); }
    finally { setTmPending(false); }
  }

  async function handleArm() {
    if (confirmPhrase !== _ARM_CONFIRM_PHRASE) return;
    const insts = armInstruments.split(',').map(s => s.trim()).filter(Boolean);
    const mc: Record<string, number> = {};
    for (const inst of insts) mc[inst] = parseInt(armMaxCt) || 1;
    const ok = await doAction('arm', {
      confirm_phrase: confirmPhrase,
      duration_min:   parseInt(armDuration) || 30,
      max_trades:     parseInt(armMaxTrades) || 3,
      instruments:    insts,
      max_contracts:  mc,
      ...(armMaxLoss ? { max_session_loss: parseFloat(armMaxLoss) } : {}),
    });
    if (ok) { setArmModalOpen(false); setConfirmPhrase(''); }
  }

  const execEnabled = armData?.execution_enabled ?? false;
  const armed       = effState === 'live_armed';
  const disarmed    = effState === 'live_available_disarmed';
  const locked      = effState === 'safety_locked';
  const tradesOver = (armData?.trades_used ?? 0) >= (armData?.max_trades ?? 9999);
  const lossNear   = armData?.max_session_loss != null
    && armData?.session_pnl < 0
    && Math.abs(armData.session_pnl) >= armData.max_session_loss * 0.8;
  const [enableModalOpen, setEnableModalOpen] = useState(false);

  const btn = (color: string, label: string, onClick: () => void, disabled?: boolean) => (
    <button onClick={onClick} disabled={pending || !!disabled}
      style={{ background: `${color}18`, border: `1px solid ${color}55`, color,
        borderRadius: 6, padding: '7px 14px', fontSize: 10.5, fontWeight: 700,
        cursor: (pending || disabled) ? 'not-allowed' : 'pointer',
        opacity: (pending || disabled) ? 0.5 : 1, letterSpacing: '0.06em' }}>
      {label}
    </button>
  );

  const inpStyle: React.CSSProperties = {
    background: '#060f1e', border: `1px solid ${T.border}`, color: T.txtPri,
    borderRadius: 5, padding: '7px 9px', fontSize: 11, outline: 'none', width: '100%',
    boxSizing: 'border-box',
  };

  // Derived display values
  const manualEntryOpen = execEnabled && !locked;  // ENTER button works when enabled (no arm needed)
  const autoFireAllowed = execEnabled && armed && !locked;
  const effectiveMode   = armData?.effective_mode ?? '';
  const tradingMode     = armData?.trading_mode ?? '';
  const modeLabel = (() => {
    if (effectiveMode === 'traderspost' || effectiveMode === 'pickmytrade') return 'LIVE';
    if (effectiveMode === 'paper')       return 'PAPER';
    if (effectiveMode === 'manual_only') return 'MANUAL PLAN';
    if (effectiveMode === 'disabled')    return 'DISABLED';
    return '—';
  })();

  // ── Sub-component: mode picker button
  const ModeBtn = ({ label, value, current, onClick, color }: {
    label: string; value: string; current: string; onClick: () => void; color: string;
  }) => {
    const active = current === value;
    return (
      <button onClick={onClick} disabled={modePending || tmPending}
        style={{ flex: 1, padding: '6px 4px', fontSize: 9.5, fontWeight: active ? 700 : 400,
          border: `1px solid ${active ? color : T.border}`,
          background: active ? `${color}18` : 'rgba(255,255,255,0.03)',
          color: active ? color : T.txtMuted,
          borderRadius: 5, cursor: 'pointer', letterSpacing: '0.04em',
          opacity: (modePending || tmPending) ? 0.5 : 1 }}>
        {active ? '● ' : ''}{label}
      </button>
    );
  };

  return (
    <Panel title="Execution Control" id="execution-arm-control"
      badge={<Pill text={stateLabel[effState] ?? effState.toUpperCase()} color={stateColor} />}
      right={
        <button onClick={refreshArm} disabled={pending}
          style={{ background: 'none', border: 'none', color: T.txtMuted, cursor: 'pointer', fontSize: 13 }}>
          ↻
        </button>
      }>

      {armErr && (
        <div style={{ color: T.amber, fontSize: 10, marginBottom: 8, padding: '4px 8px',
          background: `${T.amber}10`, borderRadius: 4 }}>
          ⚠ {armErr} — auto-retry in 30 s
        </div>
      )}

      {/* ── Status grid ─────────────────────────────────────────── */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 0',
        marginBottom: 14, padding: '10px 12px',
        background: '#060f1e', border: `1px solid ${T.border}`, borderRadius: 7,
      }}>
        {([
          ['EXECUTION GATEWAY', execEnabled ? 'ENABLED'  : 'DISABLED',  execEnabled ? T.green : T.red],
          ['AUTO-FIRE (ARM)',    armed        ? 'ARMED'    : 'DISARMED',  armed ? T.green : T.amber],
          ['GATEWAY MODE',      modeLabel,                               effectiveMode === 'disabled' ? T.red : effectiveMode ? T.txtPri : T.txtMuted],
          ['TRADING MODE',      tradingMode || '—',                      tradingMode === 'SCALP' ? T.cyan : tradingMode === 'SWING' ? T.purple : T.txtMuted],
          ['MANUAL ENTRY',      manualEntryOpen ? 'OPEN' : 'BLOCKED',   manualEntryOpen ? T.green : T.red],
          ['AUTO-FIRE ENTRIES', autoFireAllowed ? 'OPEN' : 'BLOCKED',   autoFireAllowed ? T.green : T.txtMuted],
        ] as [string, string, string][]).map(([label, value, color]) => (
          <React.Fragment key={label}>
            <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.07em',
              padding: '4px 0 4px 0', borderBottom: `1px solid ${T.border}33` }}>
              {label}
            </div>
            <div style={{ fontSize: 10.5, color, fontWeight: 700, textAlign: 'right',
              padding: '4px 0 4px 0', borderBottom: `1px solid ${T.border}33` }}>
              {value}
            </div>
          </React.Fragment>
        ))}
      </div>

      {/* ── Gateway Mode selector ─────────────────────────────────── */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.08em', marginBottom: 6 }}>
          GATEWAY MODE
          {armData?.runtime_mode_override && (
            <span style={{ marginLeft: 6, color: T.amber, fontWeight: 400 }}>(runtime override — resets on restart)</span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 5 }}>
          <ModeBtn label="MANUAL PLAN"       value="manual_only" current={effectiveMode} color={T.txtPri}
            onClick={() => handleSetGatewayMode('manual_only')} />
          <ModeBtn label="PAPER SIM"         value="paper"       current={effectiveMode} color={T.amber}
            onClick={() => handleSetGatewayMode('paper')} />
          <ModeBtn label="LIVE (TradersPost)" value="traderspost" current={effectiveMode} color={T.green}
            onClick={() => handleSetGatewayMode('traderspost')} />
        </div>
        <div style={{ fontSize: 9, color: T.txtMuted, marginTop: 4, lineHeight: 1.5 }}>
          {effectiveMode === 'manual_only' && 'Returns the trade plan only — no orders sent.'}
          {effectiveMode === 'paper'       && 'Simulates execution locally. No real orders.'}
          {(effectiveMode === 'traderspost' || effectiveMode === 'pickmytrade') && 'Live orders sent to your broker via TradersPost.'}
          {effectiveMode === 'disabled'    && 'Gateway disabled. Select a mode above to enable.'}
          {!effectiveMode                  && 'No mode set. Select one above.'}
        </div>
      </div>

      {/* ── Trading Mode selector ─────────────────────────────────── */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.08em', marginBottom: 6 }}>
          TRADING MODE
        </div>
        <div style={{ display: 'flex', gap: 5 }}>
          <ModeBtn label="SCALP" value="SCALP" current={tradingMode} color={T.cyan}
            onClick={() => handleSetTradingMode('SCALP')} />
          <ModeBtn label="SWING" value="SWING" current={tradingMode} color={T.purple}
            onClick={() => handleSetTradingMode('SWING')} />
        </div>
        <div style={{ fontSize: 9, color: T.txtMuted, marginTop: 4, lineHeight: 1.5 }}>
          {tradingMode === 'SCALP' && 'Short-term entries, tighter targets, zone-only gate.'}
          {tradingMode === 'SWING' && 'Longer-duration setups, full zone+VWAP+structure gate.'}
        </div>
      </div>

      {/* ── Safety lock notice ── */}
      {locked && (
        <div style={{ color: T.red, fontSize: 10.5, marginBottom: 10, padding: '7px 10px',
          background: `${T.red}0e`, border: `1px solid ${T.red}44`, borderRadius: 5, lineHeight: 1.5 }}>
          <strong>⚠ SAFETY LOCKED</strong> — {armData?.safety_lock_reason ?? 'emergency kill switch active'}.
          Reset the lock before re-enabling.
        </div>
      )}

      {/* ── Mode feedback ── */}
      {modeMsg && (
        <div style={{ fontSize: 10, padding: '5px 8px', borderRadius: 4, marginBottom: 8,
          background: modeMsg.ok ? `${T.green}12` : `${T.red}12`,
          color: modeMsg.ok ? T.green : T.red,
          border: `1px solid ${modeMsg.ok ? T.green : T.red}33` }}>
          {modeMsg.ok ? '✓' : '✗'} {modeMsg.text}
        </div>
      )}

      <div style={{ borderTop: `1px solid ${T.border}33`, marginBottom: 12 }} />

      {/* ── EXECUTION GATEWAY enable/disable ─────────────────────── */}
      <div style={{ marginBottom: 6 }}>
        <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.08em', marginBottom: 6 }}>
          EXECUTION GATEWAY
          <span style={{ marginLeft: 6, fontWeight: 400, color: T.txtMuted }}>
            — controls the ENTER button + ARM sessions
          </span>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {!execEnabled && !locked && btn(T.green, '▶ ENABLE EXECUTION', () => {
            setEnableModalOpen(true); setActMsg(null);
          })}
          {execEnabled && !locked && btn(T.red, '■ DISABLE EXECUTION', handleDisable)}
        </div>
        <div style={{ fontSize: 9, color: T.txtMuted, marginTop: 4, lineHeight: 1.5 }}>
          {!execEnabled && 'Disabled — ENTER button is blocked. Enable to allow manual entries.'}
          {execEnabled  && 'Enabled — ENTER button is open. The bot still will not auto-fire unless you also ARM.'}
        </div>
      </div>

      <div style={{ borderTop: `1px solid ${T.border}33`, margin: '10px 0' }} />

      {/* ── AUTO-FIRE arm/disarm ──────────────────────────────────── */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.08em', marginBottom: 6 }}>
          AUTO-FIRE
          <span style={{ marginLeft: 6, fontWeight: 400, color: T.txtMuted }}>
            — bot sends orders automatically on READY setups
          </span>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {execEnabled && !armed && !locked && btn(T.amber, '⊙ ARM AUTO-FIRE', () => {
            setArmModalOpen(true); setConfirmPhrase(''); setActMsg(null);
          })}
          {execEnabled && armed && !locked && btn(T.amber, '◎ DISARM AUTO-FIRE', () =>
            doAction('disarm', { reason: 'operator_manual' })
          )}
          {!execEnabled && (
            <span style={{ fontSize: 9.5, color: T.txtMuted, padding: '6px 0', fontStyle: 'italic' }}>
              Enable execution gateway first
            </span>
          )}
        </div>
        {armed && armData && (
          <div style={{ marginTop: 8, opacity: 0.85 }}>
            <KV label="Session Expires"  value={countdown} valueColor={countdown !== '—' && !countdown.includes('h') && parseInt(countdown) < 5 ? T.amber : T.txtPri} />
            <KV label="Trades Used"      value={`${armData.trades_used} / ${armData.max_trades ?? '—'}`} valueColor={tradesOver ? T.red : T.txtPri} />
            <KV label="Session P&L"      value={`$${armData.session_pnl.toFixed(0)}`} valueColor={armData.session_pnl < 0 ? (lossNear ? T.red : T.amber) : T.green} />
            <KV label="Instruments"      value={armData.allowed_instruments?.join(', ') ?? '—'} />
          </div>
        )}
      </div>

      {/* Safety locked actions */}
      {locked && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
          {execEnabled && btn(T.red, '■ DISABLE EXECUTION', handleDisable)}
          {btn(T.purple, '↺ RESET SAFETY LOCK', () => {
            if (window.confirm('Reset the safety lock? Ensure the system is safe before proceeding.')) {
              doAction('reset-safety-lock', {});
            }
          })}
        </div>
      )}

      {/* Emergency kill switch */}
      {execEnabled && !locked && (
        <div style={{ marginTop: 4, marginBottom: 8 }}>
          <button onClick={() => {
            if (window.confirm('Activate emergency kill switch?\n\nThis immediately disarms + safety-locks the system.\nRequires a manual reset before re-enabling.')) {
              doAction('kill-switch', {});
            }
          }} disabled={pending}
            style={{ background: 'none', border: 'none', color: `${T.red}88`, fontSize: 9.5,
              cursor: 'pointer', padding: 0, textDecoration: 'underline', letterSpacing: '0.04em' }}>
            ⚠ emergency kill switch
          </button>
        </div>
      )}

      <KV label="Last Disarm Reason" value={armData?.disarm_reason ?? '—'} />

      {/* Action feedback */}
      {actMsg && (
        <div style={{ fontSize: 10.5, padding: '6px 10px', borderRadius: 5, marginTop: 8,
          background: actMsg.ok ? `${T.green}15` : `${T.red}15`,
          color: actMsg.ok ? T.green : T.red,
          border: `1px solid ${actMsg.ok ? T.green : T.red}44` }}>
          {actMsg.ok ? '✓' : '✗'} {actMsg.text}
          {actMsg.errors && actMsg.errors.length > 0 && (
            <ul style={{ margin: '4px 0 0 0', paddingLeft: 14, listStyle: 'disc' }}>
              {actMsg.errors.map((e, i) => <li key={i} style={{ marginBottom: 2 }}>{e}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* ── ENABLE Modal ── */}
      {enableModalOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(5,12,26,0.85)',
          zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={e => { if (e.target === e.currentTarget) setEnableModalOpen(false); }}>
          <div style={{ background: T.panel, border: `1px solid ${T.green}55`,
            borderRadius: 12, padding: '24px 28px', width: 380, maxWidth: '95vw',
            boxShadow: `0 0 40px ${T.green}18` }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: T.green, marginBottom: 4, letterSpacing: '0.08em' }}>
              ▶ ENABLE EXECUTION GATEWAY
            </div>
            <div style={{ fontSize: 10, color: T.txtSec, marginBottom: 20, lineHeight: 1.65 }}>
              <strong style={{ color: T.txtPri }}>What this does:</strong> unlocks the ENTER button so you can manually send a trade. The bot still will <em>not</em> auto-fire on its own.
              <br /><br />
              <strong style={{ color: T.txtPri }}>To enable auto-firing:</strong> enable execution here, then separately press <em>ARM AUTO-FIRE</em> with a session duration + limit.
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => { setEnableModalOpen(false); setActMsg(null); }}
                style={{ background: 'none', border: `1px solid ${T.border}`, color: T.txtSec,
                  borderRadius: 6, padding: '7px 14px', fontSize: 10.5, cursor: 'pointer' }}>
                Cancel
              </button>
              <button onClick={handleEnable} disabled={pending}
                style={{ background: `${T.green}20`, border: `1px solid ${T.green}`,
                  color: T.green, borderRadius: 6, padding: '7px 18px', fontSize: 10.5,
                  fontWeight: 700, cursor: pending ? 'not-allowed' : 'pointer',
                  opacity: pending ? 0.5 : 1, letterSpacing: '0.06em' }}>
                {pending ? '…' : '▶ ENABLE'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ARM Modal */}
      {armModalOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(5,12,26,0.85)',
          zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={e => { if (e.target === e.currentTarget) setArmModalOpen(false); }}>
          <div style={{ background: T.panel, border: `1px solid ${T.red}66`,
            borderRadius: 12, padding: '24px 28px', width: 420, maxWidth: '95vw',
            boxShadow: `0 0 40px ${T.red}22` }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: T.red, marginBottom: 4, letterSpacing: '0.08em' }}>
              ⚠ ARM LIVE AUTO-TRADING
            </div>
            <div style={{ fontSize: 10, color: T.txtSec, marginBottom: 16, lineHeight: 1.65 }}>
              This enables automated live order transmission to the configured broker.
              Live execution involves real financial risk. Type the exact phrase to confirm.
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16 }}>
              <div>
                <div style={{ fontSize: 9.5, color: T.red, fontWeight: 700, letterSpacing: '0.08em', marginBottom: 4 }}>
                  CONFIRM PHRASE (required)
                </div>
                <input value={confirmPhrase}
                  onChange={e => setConfirmPhrase(e.target.value)}
                  placeholder={_ARM_CONFIRM_PHRASE}
                  style={{ ...inpStyle, fontFamily: T.mono, fontSize: 12,
                    borderColor: confirmPhrase === _ARM_CONFIRM_PHRASE ? T.green
                      : confirmPhrase.length > 0 ? T.red : T.border }} />
              </div>

              {/* ── Duration quick-pick presets ── */}
              <div>
                <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 600, marginBottom: 5 }}>DURATION</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 7 }}>
                  {(() => {
                    // Compute minutes until 9:30 AM ET next trading day
                    const overnight = (() => {
                      try {
                        const etNow = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
                        const t = new Date(etNow);
                        t.setHours(9, 30, 0, 0);
                        if (t.getTime() <= etNow.getTime()) t.setDate(t.getDate() + 1);
                        while (t.getDay() === 0 || t.getDay() === 6) t.setDate(t.getDate() + 1);
                        return Math.max(60, Math.ceil((t.getTime() - etNow.getTime()) / 60000));
                      } catch { return 480; }
                    })();
                    const oH = Math.floor(overnight / 60);
                    const oM = overnight % 60;
                    const oLabel = `Overnight (~${oH}h${oM > 0 ? `${oM}m` : ''})`;
                    const presets: Array<{ label: string; min: number }> = [
                      { label: '30m',  min: 30  },
                      { label: '1h',   min: 60  },
                      { label: '2h',   min: 120 },
                      { label: '4h',   min: 240 },
                      { label: '8h',   min: 480 },
                      { label: '12h',  min: 720 },
                      { label: oLabel, min: overnight },
                    ];
                    return presets.map(({ label, min }) => {
                      const sel = parseInt(armDuration) === min;
                      return (
                        <button key={label} type="button" onClick={() => setArmDuration(String(min))}
                          style={{ background: sel ? `${T.cyan}18` : 'rgba(255,255,255,0.04)',
                            border: `1px solid ${sel ? T.cyan : T.border}`,
                            color: sel ? T.cyan : T.txtMuted, borderRadius: 4,
                            padding: '4px 9px', fontSize: 9.5, cursor: 'pointer',
                            fontWeight: sel ? 700 : 400, letterSpacing: '0.03em' }}>
                          {label}
                        </button>
                      );
                    });
                  })()}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <input value={armDuration} type="number" min="5" max="1440"
                    onChange={e => setArmDuration(e.target.value)} style={{ ...inpStyle, flex: 1 }} />
                  <span style={{ fontSize: 9, color: T.txtMuted, flexShrink: 0 }}>min</span>
                  {parseInt(armDuration) >= 60 && (
                    <span style={{ fontSize: 9.5, color: T.txtSec, flexShrink: 0, fontFamily: T.mono }}>
                      ({Math.floor(parseInt(armDuration) / 60)}h{parseInt(armDuration) % 60 > 0 ? ` ${parseInt(armDuration) % 60}m` : ''})
                    </span>
                  )}
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 600, marginBottom: 3 }}>MAX TRADES</div>
                  <input value={armMaxTrades} type="number" min="1" max="20" onChange={e => setArmMaxTrades(e.target.value)} style={inpStyle} />
                </div>
                <div>
                  <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 600, marginBottom: 3 }}>INSTRUMENTS</div>
                  <input value={armInstruments} placeholder="MGC,MNQ" onChange={e => setArmInstruments(e.target.value)} style={inpStyle} />
                </div>
                <div>
                  <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 600, marginBottom: 3 }}>MAX CONTRACTS / INST</div>
                  <input value={armMaxCt} type="number" min="1" max="10" onChange={e => setArmMaxCt(e.target.value)} style={inpStyle} />
                </div>
                <div style={{ gridColumn: '1 / -1' }}>
                  <div style={{ fontSize: 9, color: T.txtMuted, fontWeight: 600, marginBottom: 3 }}>MAX SESSION LOSS $ (optional)</div>
                  <input value={armMaxLoss} type="number" min="0" placeholder="leave blank = no limit" onChange={e => setArmMaxLoss(e.target.value)} style={inpStyle} />
                </div>
              </div>
            </div>

            {actMsg && (
              <div style={{ fontSize: 10.5, padding: '6px 10px', borderRadius: 5, marginBottom: 12,
                background: actMsg.ok ? `${T.green}15` : `${T.red}15`,
                color: actMsg.ok ? T.green : T.red,
                border: `1px solid ${actMsg.ok ? T.green : T.red}44` }}>
                {actMsg.ok ? '✓' : '✗'} {actMsg.text}
                {actMsg.errors && actMsg.errors.length > 0 && (
                  <ul style={{ margin: '4px 0 0 0', paddingLeft: 14, listStyle: 'disc' }}>
                    {actMsg.errors.map((e, i) => <li key={i} style={{ marginBottom: 2 }}>{e}</li>)}
                  </ul>
                )}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => { setArmModalOpen(false); setActMsg(null); }}
                style={{ background: 'none', border: `1px solid ${T.border}`, color: T.txtSec,
                  borderRadius: 6, padding: '7px 14px', fontSize: 10.5, cursor: 'pointer' }}>
                Cancel
              </button>
              <button onClick={handleArm} disabled={pending || confirmPhrase !== _ARM_CONFIRM_PHRASE}
                style={{ background: confirmPhrase === _ARM_CONFIRM_PHRASE ? `${T.green}20` : `${T.border}40`,
                  border: `1px solid ${confirmPhrase === _ARM_CONFIRM_PHRASE ? T.green : T.border}`,
                  color: confirmPhrase === _ARM_CONFIRM_PHRASE ? T.green : T.txtMuted,
                  borderRadius: 6, padding: '7px 18px', fontSize: 10.5, fontWeight: 700,
                  cursor: (pending || confirmPhrase !== _ARM_CONFIRM_PHRASE) ? 'not-allowed' : 'pointer',
                  opacity: (pending || confirmPhrase !== _ARM_CONFIRM_PHRASE) ? 0.5 : 1, letterSpacing: '0.06em' }}>
                {pending ? '…' : '⊙ CONFIRM ARM'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
};

// ── Trading Desk View — analysis + active positions only ──────────────────────
// Execution controls live on the Execution page.
const TradingDeskView: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const [closing,  setClosing]  = useState<string | null>(null);
  const [tradeMsg, setTradeMsg] = useState<{ inst: string; ok: boolean; text: string } | null>(null);
  const [journal,  setJournal]  = useState<Record<string, unknown>[]>([]);

  // Fetch recent journal trades once on mount
  useEffect(() => {
    fetch('/api/journal/native-trades?limit=6', {
      credentials: 'include', headers: getAuthHeader(),
    })
      .then(r => r.ok ? r.json() : null)
      .then((d: Record<string, unknown> | null) => {
        if (d && Array.isArray(d.trades)) setJournal(d.trades as Record<string, unknown>[]);
      })
      .catch(() => {});
  }, []);

  // ── Derived state ──────────────────────────────────────────────────────────
  const v      = (p.verdict           ?? {}) as Record<string, unknown>;
  const op     = (p.operator_presentation ?? {}) as Record<string, unknown>;
  const lb     = (p.left_brain        ?? {}) as Record<string, unknown>;
  const pt     = (p.thesis            ?? {}) as Record<string, unknown>;
  const cp     = (p.candidate_preview ?? {}) as Record<string, unknown>;
  const sc     = (p.strategy_scanner  ?? {}) as Record<string, unknown>;
  const at     = (p.active_trades     ?? {}) as Record<string, unknown>;
  const vi     = (p.volatility_intelligence ?? {}) as Record<string, unknown>;
  const trades = Array.isArray(at.trades) ? at.trades as Record<string, unknown>[] : [];

  // Instrument for MTF fetch
  const mktInst = safeStr((p.market as Record<string, unknown>)?.instrument, 'MNQ');

  // Verdict / score
  const score    = safeNum(v.edge_score) ?? 0;
  const grade    = safeStr(v.edge_grade, '');
  const cpStatus = safeStr(cp.status, 'NO_CANDIDATE');
  const dir      = safeStr(cp.direction ?? sc.selected ?? '', '');
  const isReady  = cpStatus === 'READY';
  const isPot    = cpStatus === 'POTENTIAL';
  const verdictColor = isReady ? T.green : isPot ? T.amber : T.txtMuted;
  const gradeColor   = grade === 'A+' ? T.green : grade === 'A' ? T.cyan : grade === 'B' ? T.amber : T.txtMuted;

  // Authoritative verdict — from p.verdict (not candidate_preview)
  const isAuthReady  = v.is_actionable === true;
  const authDir      = safeStr(v.direction ?? cp.direction ?? sc.selected, '');
  const strictReason = safeStr(v.strict_reason, '');
  const vwapWording  = safeStr(
    ((op.vwap ?? v.vwap ?? {}) as Record<string, unknown>).wording,
    ''
  );
  const authVLabel   = isAuthReady ? '✓ READY' : '— WAIT';
  const authVCol     = isAuthReady ? T.green : T.txtMuted;

  // Left Brain thesis
  const lbDir    = safeStr(lb.direction, 'NEUTRAL');
  const lbConf   = safeNum(lb.confidence) ?? 0;
  const lbAge    = safeNum(lb.age_seconds);
  const lbColor  = /bull/i.test(lbDir) ? T.green : /bear/i.test(lbDir) ? T.red : T.txtMuted;
  const lbAgeStr = lbAge != null
    ? (lbAge < 60 ? lbAge + 's' : lbAge < 3600 ? Math.floor(lbAge / 60) + 'm' : Math.floor(lbAge / 3600) + 'h') + ' ago'
    : '';
  const strategy = safeStr(sc.selected_label ?? sc.selected, '');

  // Deterministic persistent thesis — continuity context only. Entry authority
  // remains the strict verdict above; entryStatus is copied from the backend.
  const ptDir       = safeStr(pt.direction, 'NEUTRAL');
  const ptStatus    = safeStr(pt.lifecycle_status ?? pt.status, 'NO THESIS');
  const ptMode      = safeStr(pt.mode, '');
  const ptReason    = safeStr(pt.reason, '');
  const ptConf      = safeNum(pt.confidence) ?? 0;
  const ptColor     = /long/i.test(ptDir) ? T.green : /short/i.test(ptDir) ? T.red : T.txtMuted;
  const ptStatusCol = ptStatus === 'CONFIRMED' ? T.green
                    : ptStatus === 'INVALIDATED' ? T.red
                    : ptStatus === 'PENDING_REVERSAL' ? T.amber
                    : T.cyan;
  const normalizedPtDir = ptDir.toUpperCase();
  const normalizedCandidateDir = safeStr(op.candidate_direction ?? dir ?? authDir, '').toUpperCase();
  const candidateOpposesThesis =
    (normalizedPtDir === 'LONG' && normalizedCandidateDir === 'SHORT') ||
    (normalizedPtDir === 'SHORT' && normalizedCandidateDir === 'LONG');
  const liveCandidateStatus = isAuthReady ? 'READY' : 'WAIT';

  // Trade plan levels.
  // cp.entry_zone is a formatted range string ("4423.5–4424.0") — not parseable by safeNum.
  // Use cp.preview_price (numeric anchor from management block) as the entry fallback.
  const entry  = safeNum(sc.entry  ?? cp.preview_price);
  const stopPx = safeNum(sc.stop   ?? cp.stop_loss);
  const tgts   = Array.isArray(sc.targets) ? sc.targets as (number | null)[] : [];
  const tgt1   = safeNum(tgts[0] ?? cp.take_profit);
  const tgt2   = safeNum(tgts[1]);
  const rr     = safeNum(sc.risk_reward);
  const scanReason   = safeStr(sc.reason, '');
  const marketRegime = safeStr(sc.market_regime, '');

  // Blockers / missing
  const blockers: string[] = Array.isArray(v.hard_blockers)
    ? (v.hard_blockers as string[]).filter(Boolean) : [];
  const missing: string[] = Array.isArray(cp.missing_confirmations)
    ? (cp.missing_confirmations as string[]).filter(Boolean) : [];

  // Scanner strategies
  const ranked = Array.isArray(sc.ranked_strategies)
    ? (sc.ranked_strategies as Record<string, unknown>[])
    : [];

  // VIX
  const vixBlock   = (vi.vix ?? {}) as Record<string, unknown>;
  const vixPrice   = safeNum(vixBlock.price);
  const vixChgPct  = safeNum(vixBlock.change_pct);
  const vixRegime  = safeStr(vi.regime, 'UNKNOWN');
  const vixRisk    = safeStr(vi.risk_tone, 'UNKNOWN');
  const vixEnabled = vi.enabled === true;
  const vixOk      = vixBlock.status === 'OK' || vixBlock.status === 'DELAYED';
  const vixColor   = vixRegime === 'LOW' ? T.green : vixRegime === 'ELEVATED' || vixRegime === 'HIGH' ? T.amber
                   : vixRegime === 'EXTREME' ? T.red : T.txtMuted;

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleClose = async (tInst: string) => {
    if (closing) return;
    setClosing(tInst); setTradeMsg(null);
    try {
      const r = await fetch('/api/quick-exit', {
        method: 'POST', credentials: 'include',
        headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: tInst }),
      });
      const j = await r.json() as Record<string, unknown>;
      const pnlStr = j.pnl != null ? ' · P&L: $' + String(j.pnl) : '';
      setTradeMsg({ inst: tInst, ok: r.ok,
        text: r.ok ? 'Exit sent' + pnlStr : safeStr(j.reason, 'Failed') });
    } catch { setTradeMsg({ inst: tInst, ok: false, text: 'Network error' }); }
    finally { setClosing(null); }
  };
  const handleClear = async (tInst: string) => {
    if (!window.confirm('Remove ' + tInst + ' from tracking? (No broker order sent)')) return;
    await fetch('/api/stop-managing', {
      method: 'POST', credentials: 'include',
      headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker: tInst }),
    });
  };

  // ── Shared styles ─────────────────────────────────────────────────────────
  const card: React.CSSProperties = {
    background: '#060f22', border: `1px solid ${T.border}`, borderRadius: 10,
    padding: '12px 14px', marginBottom: 10,
  };
  const sLbl: React.CSSProperties = {
    fontSize: 8, fontWeight: 700, letterSpacing: '0.10em', color: T.txtMuted,
    textTransform: 'uppercase' as const, marginBottom: 10,
  };
  const pill = (col: string, text: string): React.CSSProperties => ({
    background: col + '1e', border: `1px solid ${col}55`, borderRadius: 4,
    padding: '1px 7px', fontSize: 9, fontWeight: 700, color: col, display: 'inline-block',
  });

  return (
    <>
      {/* ═══════════════════════ PERSISTENT THESIS + ENTRY AUTHORITY ═══════ */}
      <div style={{
        ...card,
        padding: '14px 16px', border: `1px solid ${ptColor}55`,
        background: `linear-gradient(135deg, ${ptColor}12 0%, #060f22 55%)`,
      }}>
        <div style={{ ...sLbl, marginBottom: 7 }}>
          ACTIVE PERSISTENT THESIS · {ptMode || 'CURRENT MODE'}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <div data-testid="active-persistent-thesis-headline" style={{
            fontSize: 26, fontWeight: 900, color: ptColor, lineHeight: 1, letterSpacing: '0.03em',
          }}>{normalizedPtDir}</div>
          <div style={{ fontSize: 18, fontFamily: T.mono, fontWeight: 800, color: ptColor }}>{ptConf}%</div>
          <div style={{ ...pill(ptStatusCol, ptStatus), fontSize: 11, padding: '3px 9px' }}>
            {ptStatus.replaceAll('_', ' ')}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
          <div style={{ fontSize: 8, color: T.txtMuted, fontWeight: 700, letterSpacing: '0.10em' }}>ENTRY STATUS</div>
          <div data-testid="authoritative-entry-status" style={{
            background: authVCol + '20', border: `1px solid ${authVCol}55`, borderRadius: 6,
            padding: '4px 12px', fontSize: 12, fontWeight: 900, color: authVCol, letterSpacing: '0.08em',
          }}>{authVLabel}</div>
          {!isAuthReady && strictReason && (
            <div style={{ fontSize: 9, color: T.amber, flex: 1 }}>⚠ {strictReason}</div>
          )}
          {isAuthReady && (
            <div style={{ marginLeft: 'auto', fontSize: 9, color: T.green, fontFamily: T.mono }}>LIVE ●</div>
          )}
        </div>
        {ptReason && (
          <div style={{ marginTop: 8, fontSize: 9, color: T.txtSec, lineHeight: 1.4 }}>
            {ptReason}
          </div>
        )}
      </div>

      {/* ═══════════════════════ MTF TREND ═══════════════════════════════════ */}
      <MTFTrendPanel ticker={mktInst} />

      {/* ═══════════════════════ SETUP ANALYSIS ═══════════════════════════ */}
      <div style={card}>
        <div style={sLbl}>LIVE ENTRY CANDIDATE · SECONDARY SIGNAL</div>

        {/* Verdict + score row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          <div style={{
            background: verdictColor + '1e', border: `1px solid ${verdictColor}55`,
            borderRadius: 5, padding: '3px 10px',
            fontSize: 10, fontWeight: 800, color: verdictColor, letterSpacing: '0.07em',
          }}>
            {isReady ? '✓ READY' : isPot ? '⚡ FORMING' : '— WAIT'}
          </div>
          <div data-testid="live-entry-candidate" style={{ fontSize: 13, fontWeight: 800, color: gradeColor, lineHeight: 1 }}>
            {normalizedCandidateDir || 'NO DIRECTION'} {score > 0 ? `${score}/110` : ''} — {liveCandidateStatus}
          </div>
          {grade && <div style={{ fontSize: 10, fontWeight: 700, color: gradeColor }}>{grade}</div>}
          {marketRegime && (
            <div style={{ marginLeft: 'auto', fontSize: 8, color: T.txtMuted }}>{marketRegime}</div>
          )}
        </div>

        {candidateOpposesThesis && (
          <div data-testid="opposing-candidate-note" style={{
            marginBottom: 9, padding: '6px 9px', borderRadius: 6,
            background: T.amber + '10', border: `1px solid ${T.amber}35`, fontSize: 9, color: T.amber,
          }}>
            Opposing {normalizedCandidateDir} candidate detected; thesis remains {normalizedPtDir}.
          </div>
        )}
        {op.candidate_label != null && (
          <div data-testid="main-brain-candidate-label" style={{ fontSize: 9, color: T.txtSec, marginBottom: 6 }}>
            {safeStr(op.candidate_label, '')}
          </div>
        )}
        {vwapWording && <div style={{ fontSize: 9, color: T.cyan, marginBottom: 8 }}>{vwapWording}</div>}

        {/* Advisory systems are clearly separated from deterministic authority. */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10,
          padding: '6px 10px', background: '#040c1c', borderRadius: 7,
          border: `1px dashed ${T.border}`,
        }}>
          <div style={{ fontSize: 8, color: T.txtMuted, fontWeight: 700 }}>ADVISORY / RESEARCH</div>
          <div style={{ fontSize: 10, fontWeight: 700, color: lbColor }}>LEFT BRAIN {lbDir}</div>
          {lbConf > 0 && <div style={{ fontSize: 9, color: lbColor }}>{lbConf}%</div>}
          {strategy && <div style={{ fontSize: 8, color: T.txtSec }}>· scanner {strategy}</div>}
          {lbAgeStr && <div style={{ marginLeft: 'auto', fontSize: 8, color: T.txtMuted }}>{lbAgeStr}</div>}
        </div>

        {/* Blockers / missing confirmations */}
        {(blockers.length > 0 || missing.length > 0) && (
          <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: 8 }}>
            <div style={{ ...sLbl, marginBottom: 6 }}>
              {blockers.length > 0 ? 'BLOCKERS' : 'MISSING CONFIRMATIONS'}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 20px' }}>
              {[
                ...blockers.map(b => ({ text: b, hard: true  })),
                ...missing.map(m => ({ text: m, hard: false })),
              ].slice(0, 8).map(({ text, hard }, i) => (
                <div key={i} style={{ display: 'flex', gap: 5, alignItems: 'flex-start' }}>
                  <span style={{ fontSize: 9, color: hard ? T.red : T.amber, flexShrink: 0 }}>{hard ? '✗' : '○'}</span>
                  <span style={{ fontSize: 9, color: hard ? T.amber : T.txtSec }}>{String(text)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ═══════════════════════ TRADE PLAN + SCANNER ═════════════════════ */}
      <div className="mb-grid-2" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>

        {/* Trade Plan */}
        <div style={{ ...card, marginBottom: 0 }}>
          <div style={sLbl}>TRADE PLAN</div>
          {entry != null ? (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 8 }}>
                {([
                  ['ENTRY', entry,  ''],
                  ['STOP',  stopPx, stopPx != null && entry != null ? (stopPx - entry).toFixed(1) : ''],
                  ['TP1',   tgt1,   tgt1 != null && entry != null ? '+' + (tgt1 - entry).toFixed(1) : ''],
                  ['R:R',   null,   rr != null ? '1 : ' + rr.toFixed(1) : '—'],
                  ...(tgt2 != null ? [['TP2', tgt2, entry != null ? '+' + (tgt2 - entry).toFixed(1) : '']] : []),
                ] as [string, number | null, string][]).map(([k, num, note]) => (
                  <div key={k} style={{
                    background: '#040c1c', borderRadius: 5, padding: '6px 10px',
                    border: `1px solid ${T.border}`, display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center',
                  }}>
                    <span style={{ fontSize: 7, fontWeight: 700, letterSpacing: '0.07em', color: T.txtMuted }}>{k}</span>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 11, fontWeight: 700, fontFamily: T.mono, color: T.txtPri }}>
                        {num != null ? num.toFixed(2) : note}
                      </div>
                      {num != null && note && <div style={{ fontSize: 7, color: T.txtMuted }}>{note}</div>}
                    </div>
                  </div>
                ))}
              </div>
              {scanReason && (
                <div style={{ fontSize: 8, color: T.txtSec, fontStyle: 'italic', lineHeight: 1.4 }}>
                  {scanReason.slice(0, 120)}
                </div>
              )}
            </>
          ) : (
            <div style={{ padding: '20px 0', textAlign: 'center', fontSize: 10, color: T.txtMuted }}>
              No active plan
            </div>
          )}
        </div>

        {/* Scanner */}
        <div style={{ ...card, marginBottom: 0 }}>
          <div style={sLbl}>STRATEGY SCANNER</div>
          {ranked.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {ranked.slice(0, 8).map((s, i) => {
                const sLabel = safeStr(s.label, safeStr(s.strategy_key, ''));
                const sSel   = s.selected === true;
                const sElig  = s.eligible === true;
                const sDir   = safeStr(s.direction, '');
                const sResult = safeStr(s.result, '');
                const sSkip  = safeStr(s.skip_reason, '');
                const sComp  = safeNum(s.completeness);
                const rowCol = sSel ? T.green : sElig ? T.cyan : T.txtMuted;
                return (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px',
                    background: sSel ? T.green + '12' : sElig ? T.cyan + '08' : '#040c1c',
                    borderRadius: 5, border: `1px solid ${sSel ? T.green + '44' : T.border}`,
                  }}>
                    <div style={{ width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
                      background: sSel ? T.green : sElig ? T.cyan : sResult === 'pass' ? T.amber : T.txtMuted }} />
                    <div style={{ flex: 1, fontSize: 9, fontWeight: sSel ? 700 : 400, color: rowCol,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {sLabel}
                    </div>
                    {sDir && (
                      <span style={{ fontSize: 7, color: /long/i.test(sDir) ? T.green : T.red }}>
                        {sDir.charAt(0).toUpperCase()}
                      </span>
                    )}
                    {sComp != null && (
                      <span style={{ fontSize: 7, color: T.txtMuted, fontFamily: T.mono }}>
                        {sComp}%
                      </span>
                    )}
                    {sSkip && !sSel && (
                      <span style={{ fontSize: 7, color: T.txtMuted, maxWidth: 60, overflow: 'hidden',
                        textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {sSkip}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div style={{ padding: '20px 0', textAlign: 'center', fontSize: 10, color: T.txtMuted }}>
              No strategies evaluated
            </div>
          )}
        </div>
      </div>

      {/* ═══════════════════════ VIX STRIP ════════════════════════════════ */}
      {vixEnabled && (
        <div style={{
          ...card, marginBottom: 10,
          display: 'flex', alignItems: 'center', gap: 16, padding: '9px 14px',
        }}>
          <div style={{ fontSize: 8, fontWeight: 700, letterSpacing: '0.10em', color: T.txtMuted }}>VIX</div>
          {vixOk && vixPrice != null ? (
            <>
              <div style={{ fontSize: 20, fontWeight: 900, fontFamily: T.mono, color: vixColor }}>
                {vixPrice.toFixed(2)}
              </div>
              {vixChgPct != null && (
                <div style={{ fontSize: 10, fontWeight: 600,
                  color: vixChgPct > 0 ? T.red : T.green }}>
                  {vixChgPct > 0 ? '+' : ''}{vixChgPct.toFixed(2)}%
                </div>
              )}
              <div style={pill(vixColor, vixRegime)}>{vixRegime}</div>
              {vixRisk !== 'UNKNOWN' && (
                <div style={{ fontSize: 9, color: T.txtSec }}>{vixRisk} RISK</div>
              )}
            </>
          ) : (
            <div style={{ fontSize: 9, color: T.txtMuted }}>
              {vixOk ? 'Loading…' : 'Feed error — market may be closed'}
            </div>
          )}
        </div>
      )}

      {/* ═══════════════════════ MINI JOURNAL ═════════════════════════════ */}
      <div style={card}>
        <div style={sLbl}>RECENT TRADES</div>
        {journal.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {journal.map((t, i) => {
              const jInst   = safeStr(t.instrument, '');
              const jDir    = safeStr(t.direction, '');
              const jStrat  = safeStr(t.strategy_display_name, '');
              const jScore  = safeNum(t.edge_score);
              const jStatus = safeStr(t.lifecycle_status, '');
              const jEntry  = safeNum(t.planned_entry);
              const jReview = safeStr(t.review_status, '');
              const jDate   = safeStr(t.created_at, '');
              const jMode   = safeStr(t.mode, '');
              const isLong  = /long/i.test(jDir);
              const dCol    = isLong ? T.green : T.red;
              const sCol    = jStatus === 'CLOSED' ? T.txtMuted
                            : jStatus === 'SUBMITTED' || jStatus === 'OPEN' ? T.cyan : T.amber;
              const dateStr = jDate
                ? new Date(jDate).toLocaleTimeString('en-US',
                    { hour: '2-digit', minute: '2-digit', timeZone: 'Etc/GMT+4' })
                : '';
              return (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px',
                  background: '#040c1c', borderRadius: 5, border: `1px solid ${T.border}`,
                }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: dCol, width: 30, flexShrink: 0 }}>
                    {jInst}
                  </div>
                  <div style={{ fontSize: 8, fontWeight: 600, color: dCol, width: 14, flexShrink: 0 }}>
                    {isLong ? '▲' : '▼'}
                  </div>
                  <div style={{ flex: 1, fontSize: 9, color: T.txtSec, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {jStrat}
                  </div>
                  {jEntry != null && (
                    <div style={{ fontSize: 8, fontFamily: T.mono, color: T.txtPri, flexShrink: 0 }}>
                      {jEntry.toFixed(2)}
                    </div>
                  )}
                  {jScore != null && (
                    <div style={{ fontSize: 8, color: T.txtMuted, flexShrink: 0, fontFamily: T.mono }}>
                      {jScore}
                    </div>
                  )}
                  <div style={{ fontSize: 7, color: sCol, flexShrink: 0, fontWeight: 600 }}>{jStatus}</div>
                  {jReview === 'UNREVIEWED' && (
                    <div style={{ fontSize: 7, color: T.amber, flexShrink: 0 }}>⬤</div>
                  )}
                  <div style={{ fontSize: 7, color: T.txtMuted, flexShrink: 0 }}>{dateStr}</div>
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ padding: '12px 0', textAlign: 'center', fontSize: 10, color: T.txtMuted }}>
            No trades recorded yet
          </div>
        )}
      </div>

      {/* ═══════════════════════ ACTIVE POSITIONS ═════════════════════════ */}
      {trades.length > 0 && trades.map((trade, ti) => {
        const tInst  = safeStr(trade.instrument, '');
        const tDir   = safeStr(trade.direction, '');
        const tEntry = safeNum(trade.entry_price);
        const tStop  = safeNum(trade.stop_price ?? trade.stop);
        const tTgt1  = safeNum(trade.target1 ?? trade.take_profit);
        const tCurR  = safeNum(trade.current_r);
        const tUpnl  = safeNum(trade.unrealized_pnl);
        const tCts   = trade.quantity ?? trade.contracts ?? 1;
        const isLong = /long/i.test(tDir);
        const dCol   = isLong ? T.green : T.red;
        const isCls  = closing === tInst;
        const msg    = tradeMsg?.inst === tInst ? tradeMsg : null;
        return (
          <div key={ti} style={{
            background: '#050e0a', border: `1px solid ${T.amber}44`, borderRadius: 10,
            padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10,
            flexWrap: 'wrap', marginBottom: 6,
          }}>
            <div style={{ fontSize: 11, fontWeight: 800, color: dCol,
              background: dCol + '14', border: `1px solid ${dCol}44`,
              borderRadius: 5, padding: '4px 10px', flexShrink: 0 }}>
              {tInst} {tDir.toUpperCase()} {String(tCts)}ct
            </div>
            {([['ENTRY', tEntry?.toFixed(2)], ['STOP', tStop?.toFixed(2)], ['TP1', tTgt1?.toFixed(2)]] as [string, string | undefined][])
              .filter(([, val]) => val)
              .map(([k, val]) => (
                <div key={k} style={{ fontSize: 9 }}>
                  <span style={{ color: T.txtMuted, letterSpacing: '0.06em', marginRight: 3 }}>{k}</span>
                  <span style={{ fontFamily: T.mono, fontWeight: 600, color: T.txtPri }}>{val}</span>
                </div>
              ))}
            {tCurR != null && (
              <div style={{ fontSize: 11, fontWeight: 700,
                color: tCurR > 0 ? T.green : tCurR < 0 ? T.red : T.txtMuted }}>
                {tCurR > 0 ? '+' : ''}{tCurR.toFixed(2)}R
              </div>
            )}
            {tUpnl != null && (
              <div style={{ fontSize: 9, fontWeight: 600, color: tUpnl > 0 ? T.green : T.red }}>
                {tUpnl > 0 ? '+' : ''}${tUpnl.toFixed(0)} est.
              </div>
            )}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
              {msg && <span style={{ fontSize: 9, color: msg.ok ? T.green : T.red }}>{msg.text}</span>}
              <button onClick={() => handleClose(tInst)} disabled={isCls}
                style={{ padding: '5px 12px', fontSize: 9, fontWeight: 700,
                  background: T.red + '18', border: `1px solid ${T.red}55`,
                  color: T.red, borderRadius: 5, cursor: 'pointer', opacity: isCls ? 0.5 : 1 }}>
                {isCls ? '…' : '■ CLOSE'}
              </button>
              <button onClick={() => handleClear(tInst)}
                style={{ padding: '5px 10px', fontSize: 9, fontWeight: 700,
                  background: '#0a1628', border: `1px solid ${T.border}`,
                  color: T.txtMuted, borderRadius: 5, cursor: 'pointer' }}>
                Clear
              </button>
            </div>
          </div>
        );
      })}
    </>
  );
};


const OPENING_BELL_DURATION_MS = 60_000; // visible for 60 seconds

interface OpeningBellState {
  firedAt:   number; // Date.now() when bell fired
  dateLabel: string; // e.g. "MON AUG 10"
}

const OpeningBellPill: React.FC<{
  state:     OpeningBellState;
  p:         Record<string, unknown>;
  onDismiss: () => void;
}> = ({ state, p, onDismiss }) => {
  const [remaining, setRemaining] = React.useState(
    Math.max(0, OPENING_BELL_DURATION_MS - (Date.now() - state.firedAt)),
  );

  useEffect(() => {
    const iv = setInterval(() => {
      const rem = Math.max(0, OPENING_BELL_DURATION_MS - (Date.now() - state.firedAt));
      setRemaining(rem);
      if (rem <= 0) onDismiss();
    }, 500);
    return () => clearInterval(iv);
  }, [state.firedAt, onDismiss]);

  const pct    = (remaining / OPENING_BELL_DURATION_MS) * 100;
  const remSec = Math.ceil(remaining / 1_000);

  // Build a one-line status summary from current payload
  const verdict = (p.verdict ?? {}) as Record<string, unknown>;
  const eb      = (p.edge_breakdown ?? {}) as Record<string, unknown>;
  const market  = (p.market  ?? {}) as Record<string, unknown>;
  const inst    = safeStr(market.instrument, '');
  const score   = safeNum(eb.score ?? eb.total_score);
  const isReady = verdict.is_actionable === true;
  const vDir    = safeStr(verdict.direction, '');

  const statusLine = inst
    ? isReady
      ? `${inst} READY${vDir ? ' · ' + vDir : ''}${score != null ? '  ·  Edge ' + score : ''} — setup confirmed at open`
      : `${inst} WAIT${score != null ? '  ·  Edge ' + score : ''} — no confirmed setup at open yet`
    : 'Waiting for first signal…';

  return (
    <div style={{
      position: 'fixed', bottom: 0, left: 0, right: 0,
      zIndex: 9998,
      background: 'rgba(8,14,22,0.97)',
      borderTop: `1px solid ${T.amber}55`,
      backdropFilter: 'blur(20px)',
    }}>
      {/* Countdown bar — drains left-to-right */}
      <div style={{ height: 2, background: `${T.amber}20` }}>
        <div style={{
          height: '100%', width: `${pct}%`,
          background: `linear-gradient(90deg, ${T.amber}cc, ${T.amber})`,
          transition: 'width 0.5s linear',
        }} />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '10px 20px' }}>
        {/* Bell icon */}
        <span style={{ fontSize: 22, lineHeight: 1, flexShrink: 0 }}>🛎</span>

        {/* Text block */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <span style={{
              fontSize: 12, fontWeight: 800, color: T.amber,
              letterSpacing: '0.10em', whiteSpace: 'nowrap',
            }}>
              MARKET OPEN
            </span>
            <span style={{ fontSize: 10, color: T.txtMuted, fontFamily: T.mono, whiteSpace: 'nowrap' }}>
              9:30 AM ET  ·  {state.dateLabel}
            </span>
          </div>
          <div style={{ fontSize: 10.5, color: T.txtSec, marginTop: 3,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {statusLine}
          </div>
        </div>

        {/* Countdown + dismiss */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
          <span style={{ fontSize: 10, color: T.txtMuted, fontFamily: T.mono, minWidth: 28, textAlign: 'right' }}>
            {remSec}s
          </span>
          <button
            onClick={onDismiss}
            style={{
              background: 'none', border: `1px solid ${T.border}`, color: T.txtSec,
              borderRadius: 6, padding: '4px 12px', fontSize: 10.5, cursor: 'pointer',
              letterSpacing: '0.05em', transition: 'border-color 0.15s, color 0.15s',
            }}
            onMouseEnter={e => {
              (e.currentTarget as HTMLButtonElement).style.borderColor = T.amber;
              (e.currentTarget as HTMLButtonElement).style.color = T.amber;
            }}
            onMouseLeave={e => {
              (e.currentTarget as HTMLButtonElement).style.borderColor = T.border;
              (e.currentTarget as HTMLButtonElement).style.color = T.txtSec;
            }}
          >
            CLOSE
          </button>
        </div>
      </div>
    </div>
  );
};


// ── Bell Toast — fixed bottom-centre banner shown when any audio event fires ──
interface BellToastData {
  key:      number;
  icon:     string;
  label:    string;
  sublabel: string;
  color:    string;
}

const BellToast: React.FC<{ data: BellToastData; onDismiss: () => void }> = ({ data, onDismiss }) => {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4_500);
    return () => clearTimeout(t);
  }, [data.key, onDismiss]);

  return (
    <div
      role="status"
      aria-live="polite"
      onClick={onDismiss}
      style={{
        position: 'fixed', bottom: 28, left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 9999, cursor: 'pointer',
        animation: 'bellToastIn 0.22s ease forwards',
        background: 'rgba(10,17,26,0.96)',
        border: `1px solid ${data.color}55`,
        borderRadius: 12,
        padding: '10px 18px 10px 14px',
        display: 'flex', alignItems: 'center', gap: 10,
        boxShadow: `0 6px 28px rgba(0,0,0,0.55), 0 0 0 1px ${data.color}18`,
        backdropFilter: 'blur(14px)',
        maxWidth: 460,
      }}
    >
      <span style={{ fontSize: 17, lineHeight: 1, flexShrink: 0 }}>{data.icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: data.color,
                      letterSpacing: '0.06em', whiteSpace: 'nowrap',
                      overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {data.label}
        </div>
        {data.sublabel && (
          <div style={{ fontSize: 10, color: T.txtSec, marginTop: 2,
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {data.sublabel}
          </div>
        )}
      </div>
      <span style={{ fontSize: 9, color: T.txtMuted, flexShrink: 0,
                     letterSpacing: '0.04em', marginLeft: 4 }}>tap ×</span>
    </div>
  );
};


export default function MainBrain() {
  // Section param — present when route is /main-brain/:section, absent at /main-brain
  const params = useParams<{ section?: string }>();
  const requestedSection = (params as Record<string, string | undefined>).section ?? '';
  const section = normalizeTrainingSection(requestedSection);

  const [ticker, setTicker] = useState<string>(() => {
    try { return localStorage.getItem('mb_ticker') || 'MGC'; } catch { return 'MGC'; }
  });
  const handleSetTicker = (t: string) => {
    setTicker(t); try { localStorage.setItem('mb_ticker', t); } catch {}
  };

  // Listen for ticker-switch events from GlobalAlertDock (when operator clicks an alert)
  useEffect(() => {
    const handler = (e: Event) => {
      const inst = (e as CustomEvent<string>).detail;
      if (inst && typeof inst === 'string') handleSetTicker(inst);
    };
    window.addEventListener('mb:ticker', handler);
    return () => window.removeEventListener('mb:ticker', handler);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Redirect unknown section segments to the root overview (replace so the invalid
  // URL is removed from browser history rather than pushed as a new entry).
  const [, navigate] = useLocation();
  useEffect(() => {
    if (requestedSection === 'research') {
      navigate('/main-brain/strategy-lab', { replace: true });
      return;
    }
    if (section !== '' && !KNOWN_SECTIONS.includes(section)) {
      navigate('/main-brain', { replace: true });
    }
  }, [requestedSection, section]); // eslint-disable-line react-hooks/exhaustive-deps

  const { payload, fetchState, lastOk, error, isAuthFail, refresh } = useMainBrain(ticker);
  const [showLogin, setShowLogin] = useState<boolean>(() => {
    try { return !localStorage.getItem('brain_auth'); } catch { return true; }
  });

  // Claim/release the global dock's protected polling for this route. A stored
  // password is only a hint; the hook announces true after a protected fetch
  // actually succeeds.
  useEffect(() => {
    announceDashboardAuth(false);
    return () => announceDashboardAuth(false);
  }, []);

  useEffect(() => {
    if (isAuthFail) setShowLogin(true);
  }, [isAuthFail]);

  const authenticate = useCallback(async (password: string): Promise<boolean> => {
    try {
      const response = await fetch(`/api/main-brain?ticker=${encodeURIComponent(ticker)}`, {
        credentials: 'include',
        headers: { Authorization: 'Basic ' + btoa('admin:' + password) },
      });
      if (!response.ok) return false;
      try { localStorage.setItem('brain_auth', password); } catch {}
      announceDashboardAuth(true, 'Basic ' + btoa('admin:' + password));
      setShowLogin(false);
      refresh();
      return true;
    } catch {
      return false;
    }
  }, [ticker, refresh]);

  const p = (payload ?? {}) as Record<string, unknown>;
  const sys = (p.system_status ?? {}) as Record<string, unknown>;
  const allOk = !!(sys.db_ready && sys.learning_ready);
  const isLoading = fetchState === 'loading' && !payload;
  const isError   = fetchState === 'error' && !payload;

  // ── Ask AI state ─────────────────────────────────────────────────────────
  // Open/close only — all chat state lives inside AskAiPanel so closing the
  // panel does not reset message history when the operator re-opens it.
  const [askOpen, setAskOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [section]);

  // ── Cleanest Trade state ──────────────────────────────────────────────────
  // scanGenRef is a generation counter incremented on every scan attempt.
  // After each async Promise.all() completes, the handler checks whether its
  // captured generation still matches the current value — if a newer scan was
  // started (e.g. a second click before the first settled), the stale result
  // is silently discarded instead of overwriting the newer one.
  const scanGenRef = useRef(0);

  // Consensus direction stability (4-poll hysteresis ~28s).
  // A direction flip must hold for 4 consecutive polls before the display switches.
  // Single-poll noise and brief reversals are silently absorbed.
  const _prevConDirRef   = useRef<string>('DIVIDED');
  const _stableConDirRef = useRef<string>('DIVIDED');
  const _conStreakRef     = useRef<number>(0);

  const [cleanestOpen,     setCleanestOpen]     = useState(false);
  const [cleanestScanning, setCleanestScanning] = useState(false);
  const [cleanestScan,     setCleanestScan]     = useState<CleanestScanResult | null>(null);

  const handleScanCleanest = useCallback(async () => {
    // Guard: while a scan is already in flight, ignore additional clicks.
    if (cleanestScanning) return;
    // Increment the generation counter and capture this scan's generation id.
    const thisGen = ++scanGenRef.current;
    setCleanestScanning(true);
    const scanStartedAt = Date.now();
    try {
      const requests = SCAN_INSTRUMENTS.flatMap(inst =>
        SCAN_MODES.map(mode => ({ instrument: inst, mode }))
      );
      const resolved = await Promise.all(
        requests.map(async ({ instrument, mode }): Promise<RankInput> => {
          try {
            const r = await fetch(
              `/api/status?ticker=${encodeURIComponent(instrument)}&mode=${encodeURIComponent(mode)}`,
              { credentials: 'include', headers: getAuthHeader() }
            );
            const respondedAt = Date.now();
            if (!r.ok) return {
              instrument, mode, record: null,
              meta: { instrument, mode, ok: false, respondedAt,
                      responseAgeMs: respondedAt - scanStartedAt, generated_at: null },
            };
            const data = await r.json() as StatusRecord;
            return {
              instrument, mode, record: data,
              meta: { instrument, mode, ok: true, respondedAt,
                      responseAgeMs: respondedAt - scanStartedAt,
                      generated_at: (data as Record<string, unknown>).generated_at as string ?? null },
            };
          } catch {
            const respondedAt = Date.now();
            return {
              instrument, mode, record: null,
              meta: { instrument, mode, ok: false, respondedAt,
                      responseAgeMs: respondedAt - scanStartedAt, generated_at: null },
            };
          }
        })
      );
      // Generation guard: if a newer scan was initiated while this one was
      // in flight, discard this result rather than overwriting the newer one.
      if (thisGen !== scanGenRef.current) return;

      const failed    = resolved.filter(r => r.record === null)
                                .map(r => ({ instrument: r.instrument as string, mode: r.mode as string }));
      const succeeded = resolved.filter(r => r.record !== null).length;
      const candidate = rankCandidates(resolved);

      setCleanestScan({
        candidate, error: null, allInputs: resolved,
        scannedAt: Date.now(), scanStartedAt,
        isPartial: failed.length > 0, succeeded, total: resolved.length, failed,
      });
      setCleanestOpen(true);
    } catch {
      if (thisGen !== scanGenRef.current) return;
      setCleanestScan({
        candidate: null, error: 'Scan failed — check connection', allInputs: [],
        scannedAt: Date.now(), scanStartedAt,
        isPartial: false, succeeded: 0, total: 8, failed: [],
      });
      setCleanestOpen(true);
    } finally {
      setCleanestScanning(false);
    }
  }, [cleanestScanning]);

  // ── Audio notification transitions ────────────────────────────────────────
  // Refs hold the PREVIOUS value so we can detect state changes without
  // re-running the effect on every render. All audio calls go through
  // audioManager.play() — no Audio objects created here.
  const prevActionableRef  = useRef<boolean | null>(null);  // null = not yet known
  const prevFetchOkRef     = useRef<boolean>(false);        // true once we had a 'loaded' state
  const prevScannerKeyRef  = useRef<string>('');

  // Bell toast — shown at the bottom of the screen whenever a sound fires.
  const [bellToast, setBellToast] = useState<BellToastData | null>(null);

  // Opening bell — 9:30 AM ET opening bell pill
  const [openingBell, setOpeningBell] = useState<OpeningBellState | null>(null);
  const openingBellFiredRef = useRef(false);

  useEffect(() => {
    const check = () => {
      if (openingBellFiredRef.current) return;
      // Guard: already fired today (survives page refresh)
      try {
        const todayET = new Intl.DateTimeFormat('en-US', {
          timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
        }).format(new Date()).replace(/\//g, '-');
        if (localStorage.getItem(`ob_fired_v2_${todayET}`)) {
          openingBellFiredRef.current = true;
          return;
        }
      } catch {/* ignore storage errors */}

      // Get ET time components via Intl.
      // Use hour12: true + explicit dayPeriod check — hour12: false is unreliable
      // across browsers (some return 9 for 9 PM instead of 21).
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        weekday: 'short', hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true,
      }).formatToParts(new Date());
      const get = (type: string) => parseInt(parts.find(pt => pt.type === type)?.value ?? '0');
      const dayName = parts.find(pt => pt.type === 'weekday')?.value ?? '';
      const period  = (parts.find(pt => pt.type === 'dayPeriod')?.value ?? '').toUpperCase();
      if (dayName === 'Sat' || dayName === 'Sun') return;

      const h = get('hour'); const m = get('minute'); const s = get('second');
      // Fire at 9:30:00 – 9:30:44 ET (AM only) so an 8s poll always catches it.
      // The dayPeriod guard is the critical cross-browser safety check.
      if (!(h === 9 && m === 30 && s < 45 && period === 'AM')) return;

      openingBellFiredRef.current = true;
      try {
        const todayET = new Intl.DateTimeFormat('en-US', {
          timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
        }).format(new Date()).replace(/\//g, '-');
        // v2 key — clears any stale keys from the PM-firing bug
        localStorage.setItem(`ob_fired_v2_${todayET}`, '1');
      } catch {/* noop */}

      audioManager.play(SoundEvent.MARKET_OPEN);

      const dateLabel = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric',
      }).format(new Date()).toUpperCase();

      // Push a persistent dock entry so the alert queue shows why the bell rang.
      // Uses the same localStorage queue as GlobalAlertDock — survives navigation.
      try {
        const bellId = `MARKET_OPEN|${dateLabel}`;
        const entry = {
          id:           bellId,
          type:         'SYSTEM_SAFETY' as const,
          instrument:   'MARKET',
          timestamp:    Date.now(),
          acknowledged: false,
          isShadow:     false,
          label:        `MARKET OPEN  ·  9:30 AM ET`,
          sublabel:     dateLabel,
        };
        saveQueue(upsertAlert(loadQueue(), entry));
      } catch {/* noop — dock entry is advisory only */}

      setOpeningBell({ firedAt: Date.now(), dateLabel });
    };

    check();
    const iv = setInterval(check, 8_000);
    return () => clearInterval(iv);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // Ignore during initial loading — wait for first real payload.
    if (!payload) return;

    const verdict = (p.verdict ?? {}) as Record<string, unknown>;
    const sc      = (p.strategy_scanner ?? {}) as Record<string, unknown>;

    const isActionable   = verdict.is_actionable === true;
    const isConnected    = fetchState === 'loaded' || fetchState === 'stale' || fetchState === 'refreshing';
    const isDisconnected = fetchState === 'error';

    // Collect the single highest-priority toast to show this cycle.
    // React batches synchronous setState calls inside a useEffect — only the
    // LAST call wins.  We therefore compute all events first, then call
    // setBellToast exactly once with the highest-priority result.
    // Priority order: READY_TO_TRADE > SYSTEM_OFFLINE > SYSTEM_ONLINE > SCAN_FOUND
    let nextToast: BellToastData | null = null;

    // SCAN_FOUND — lowest priority: fires when a new strategy is selected.
    const scannerKey = String(sc.selected_strategy ?? '');
    if (scannerKey && scannerKey !== prevScannerKeyRef.current) {
      audioManager.play(SoundEvent.SCAN_FOUND);
      const inst = safeStr((p.market as Record<string, unknown>)?.instrument, '');
      nextToast = {
        key:      Date.now(),
        icon:     '🔍',
        label:    'New setup found',
        sublabel: [inst, scannerKey].filter(Boolean).join('  ·  '),
        color:    T.cyan,
      };
      // ── Push to persistent dock queue so it's visible on all pages ──
      try {
        const scanInst = inst || 'SYSTEM';
        saveQueue(upsertAlert(loadQueue(), {
          id:           `SCAN_FOUND|${scanInst}|${scannerKey}`,
          type:         'SYSTEM_SAFETY' as const,
          instrument:   scanInst,
          timestamp:    Date.now(),
          acknowledged: false,
          isShadow:     false,
          label:        'New setup found',
          sublabel:     [scanInst, scannerKey].filter(Boolean).join('  ·  '),
        }));
      } catch {/* noop — dock push is advisory only */}
    }
    prevScannerKeyRef.current = scannerKey;

    // SYSTEM_ONLINE — fires on disconnected/initial → connected transition.
    if (isConnected && !prevFetchOkRef.current) {
      audioManager.play(SoundEvent.SYSTEM_ONLINE);
      nextToast = { key: Date.now(), icon: '✓', label: 'System connected',
                    sublabel: '', color: T.green };
      // ── Push to persistent dock queue ──
      try {
        saveQueue(upsertAlert(loadQueue(), {
          id:           `SYSTEM_ONLINE|${Date.now()}`,
          type:         'SYSTEM_SAFETY' as const,
          instrument:   'SYSTEM',
          timestamp:    Date.now(),
          acknowledged: false,
          isShadow:     false,
          label:        'System connected',
          sublabel:     'Feed restored',
        }));
      } catch {/* noop */}
    }
    // SYSTEM_OFFLINE — fires on connected → error transition.
    if (isDisconnected && prevFetchOkRef.current) {
      audioManager.play(SoundEvent.SYSTEM_OFFLINE);
      nextToast = { key: Date.now(), icon: '⚠', label: 'Feed disconnected',
                    sublabel: 'Retrying automatically…', color: T.amber };
      // ── Push to persistent dock queue ──
      try {
        saveQueue(upsertAlert(loadQueue(), {
          id:           `SYSTEM_OFFLINE|${Date.now()}`,
          type:         'SYSTEM_SAFETY' as const,
          instrument:   'SYSTEM',
          timestamp:    Date.now(),
          acknowledged: false,
          isShadow:     false,
          label:        'Feed disconnected',
          sublabel:     'Retrying automatically…',
        }));
      } catch {/* noop */}
    }
    if (isConnected)    prevFetchOkRef.current = true;
    if (isDisconnected) prevFetchOkRef.current = false;

    // READY_TO_TRADE — play sound on NOT_READY → READY transition.
    // The visual pill is handled by the App-level GlobalAlertDock (persistent,
    // never auto-dismisses). We keep the sound here but skip the BellToast visual
    // to avoid a duplicate pill on this page.
    if (isActionable && prevActionableRef.current !== true) {
      audioManager.play(SoundEvent.READY_TO_TRADE);
      // intentionally no nextToast assignment — App-level pill shows this
    }
    prevActionableRef.current = isActionable;

    // Single setState call — whichever event won the priority race above.
    if (nextToast) setBellToast(nextToast);

  }, [payload, fetchState]); // eslint-disable-line react-hooks/exhaustive-deps

  // Section-based panel rendering — always reuses existing panel components,
  // never creates a second polling loop or duplicates business logic.
  const renderSectionPanels = (): React.ReactNode => {
    switch (section) {
      case 'verdict-history':
        return <VerdictHistoryPage />;
      case 'analysis':
        return (
          <>
            <div style={{ marginBottom:10 }}>
              <a
                href="https://trading-research-lab.replit.app"
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '10px 14px', borderRadius: 8,
                  background: 'rgba(99,102,241,0.08)',
                  border: '1px solid rgba(99,102,241,0.25)',
                  color: '#818cf8', fontSize: 13, fontWeight: 600,
                  textDecoration: 'none', letterSpacing: '0.02em',
                }}
              >
                <span style={{ fontSize: 15 }}>🔗</span>
                Open Analysis Tool
                <span style={{ marginLeft: 'auto', fontSize: 10, opacity: 0.6 }}>↗</span>
              </a>
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }} className="mb-grid-2">
              <ThesisPanel p={p} />
              <VerdictPanel p={p} />
            </div>
            <div style={{ marginTop: 10 }}>
              <VolatilityIntelligencePanel p={p} />
            </div>
            <div style={{ marginTop: 10 }}>
              <TopOfBookPressurePanel p={p} ticker={ticker} />
            </div>
            <CanonicalStatePanel />
          </>
        );
      case 'scanner':
        return (
          <>
            <div style={{ marginBottom:10 }}><ScannerPanel p={p} /></div>
            <TradePlanPanel p={p} />
          </>
        );
      case 'desk':
        return <TradingDeskView p={p} />;
      case 'trades':
        return (
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10 }} className="mb-grid-3">
            <TradePlanPanel p={p} />
            <ActiveTradesPanel p={p} />
            <ExecutionPanel p={p} />
          </div>
        );
      case 'journal':
        return <JournalFullPage />;
      case 'coach':
        return <CoachPanel p={p} />;
      case 'scalp':
        return (
          <TrainingLanePanel lane="scalp" authHeader={getAuthHeader()['Authorization'] ?? ''} />
        );
      case 'intraday':
        return (
          <TrainingLanePanel lane="intraday" authHeader={getAuthHeader()['Authorization'] ?? ''} />
        );
      case 'strategy-lab':
        return (
          <StrategyLabPanel authHeader={getAuthHeader()['Authorization'] ?? ''} />
        );
      case 'research-health':
        return (
          <TrainingInfrastructurePanel authHeader={getAuthHeader()['Authorization'] ?? ''} />
        );
      case 'execution':
        return (
          <>
            <div style={{ marginBottom: 10 }}>
              <ArmControlPanel />
            </div>
            <div style={{ marginBottom: 10 }}>
              <RecommendationCard p={p} />
            </div>
            <div style={{ marginBottom: 10 }}>
              <TradePlanPanel p={p} />
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }} className="mb-grid-2">
              <ExecutionPanel p={p} />
              <SystemHealthPanel p={p} />
            </div>
          </>
        );
      case 'alerts':
        return (
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10 }} className="mb-grid-3">
            <TimelinePanel p={p} />
            <AlertsPanel p={p} />
            <SystemHealthPanel p={p} />
          </div>
        );
      default: {
        // Root overview — Consensus strip replaces Thesis + Verdict + Scanner top row
        const _rawCon = computeConsensus(p);
        // 4-poll hysteresis: commit a direction only after 4 consecutive matching polls.
        // A direction flip needs ~28s of sustained signal to show on screen.
        if (_rawCon.direction === _prevConDirRef.current) {
          _conStreakRef.current += 1;
          if (_conStreakRef.current >= 4) {
            _stableConDirRef.current = _rawCon.direction;
          }
        } else {
          _conStreakRef.current = 0;
        }
        _prevConDirRef.current = _rawCon.direction;
        const _con = { ..._rawCon, direction: _stableConDirRef.current as 'LONG' | 'SHORT' | 'DIVIDED' };
        const _vrdDir   = safeStr((p.verdict as Record<string,unknown>)?.direction ?? '', '').toUpperCase();
        const _planConflict = _con.direction !== 'DIVIDED'
          && _vrdDir !== ''
          && _vrdDir !== _con.direction
          && (p.verdict as Record<string,unknown>)?.is_actionable === true;
        return (
          <>
            {/* ── Consensus strip ──────────────────────────────────────────── */}
            <ConsensusPanel p={p} consensus={_con} />

            {/* ── MTF Trend Alignment (Phase 8B.1 — DISPLAY-ONLY) ─────────── */}
            <MTFTrendPanel ticker={ticker} />

            {/* ── Trade Plan row — dim TradePlanPanel when direction conflicts ─ */}
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10, marginBottom:10 }} className="mb-grid-3">
              <div style={{
                opacity: _planConflict ? 0.28 : 1,
                transition: 'opacity 0.4s ease',
                position: 'relative',
              }}>
                {_planConflict && (
                  <div style={{
                    position:'absolute', top:0, left:0, right:0, zIndex:1,
                    background:`${T.amber}20`, borderBottom:`1px solid ${T.amber}35`,
                    borderRadius:'8px 8px 0 0',
                    padding:'4px 10px', fontSize:9, color:T.amber,
                    letterSpacing:'0.06em', textAlign:'center',
                  }}>
                    ⚠ Plan is {_vrdDir} — consensus is {_con.direction}
                  </div>
                )}
                <TradePlanPanel p={p} />
              </div>
              <ActiveTradesPanel p={p} />
              <ExecutionPanel p={p} />
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginBottom:10 }} className="mb-grid-2">
              <CoachPanel p={p} />
              <JournalPanel p={p} />
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10 }} className="mb-grid-3">
              <TimelinePanel p={p} />
              <AlertsPanel p={p} />
              <SystemHealthPanel p={p} />
            </div>
          </>
        );
      }  // end default block
    }
  };

  return showLogin ? <MainBrainLoginScreen onSubmit={authenticate} /> : (
    <div className="mb-shell" style={{ display:'flex', minHeight:'100vh', background:T.bg, color:T.txtPri, fontFamily:"'Inter',system-ui,sans-serif" }}>
      {/* Skip link */}
      <a href="#main-content" style={{ position:'absolute', left:-9999, top:0, background:T.cyan, color:'#000', padding:'4px 12px', borderRadius:4, zIndex:100, fontSize:12, fontWeight:700 }}
        onFocus={e => { e.currentTarget.style.left='0'; }} onBlur={e => { e.currentTarget.style.left='-9999px'; }}>
        Skip to content
      </a>

      {/* Side nav */}
      <SideNav systemOk={allOk} />
      <MobileNavDrawer open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} systemOk={allOk} />

      {/* Main area */}
      <div className="mb-main-area" style={{ flex:1, display:'flex', flexDirection:'column', minWidth:0, overflowX:'hidden' }}>
        <Header
          p={p}
          fetchState={fetchState}
          lastOk={lastOk}
          ticker={ticker}
          setTicker={handleSetTicker}
          refresh={refresh}
          onAskAi={() => setAskOpen(true)}
          onOpenMenu={() => setMobileNavOpen(true)}
          menuOpen={mobileNavOpen}
        />

        <main id="main-content" className="mb-main-content" style={{ flex:1, padding:'16px 20px 32px', overflow:'auto' }}>
          {/* Execution and training lanes render immediately — they have their own read-only data sources. */}
          {(section === 'execution' || section === 'scalp' || section === 'intraday' || section === 'strategy-lab' || section === 'research-health' || section === 'verdict-history') ? renderSectionPanels() :
          isLoading ? <LoadingScreen /> : isError ? <ErrorScreen msg={error} refresh={refresh} /> : (
            <>
              {/* Stale banner */}
              {fetchState === 'stale' && (
                <div role="alert" style={{ background:`${T.amber}18`, border:`1px solid ${T.amber}44`, borderRadius:8, padding:'8px 14px', marginBottom:12, fontSize:11, color:T.amber, display:'flex', alignItems:'center', gap:8 }}>
                  <span>⚠</span> Showing stale data — last successful fetch {fmtAge(lastOk ? new Date(lastOk).toISOString() : '')}. Retrying automatically.
                </div>
              )}

              {/* AI Summary strip */}
              <div style={{ marginBottom:12 }}>
                <AISummary p={p} />
              </div>

              {/* Market State Strip */}
              <div style={{ marginBottom:12 }}>
                <MarketStrip p={p} />
              </div>

              {/* ── Technical verdict + shadow fundamental context ─────────── */}
              <div className="mb-verdict-row" style={{ marginBottom:12, display:'grid',
                gridTemplateColumns:'minmax(0, 2fr) minmax(280px, 1fr)', gap:12, alignItems:'stretch' }}>
                <VerdictPanel p={p} />
                <FundamentalContextPanel p={p} />
              </div>

              {/* ── Mode Overview — best setup across SCALP / Intraday / SWING ── */}
              <ModeOverviewPanel
                ticker={ticker}
                authHeader={getAuthHeader()['Authorization'] ?? ''}
              />

              {/* ── Gate Effectiveness Audit — per-mode block/outcome breakdown ── */}
              <GateEffectivenessPanel
                authHeader={getAuthHeader()['Authorization'] ?? ''}
              />

              {/* ── Visual Brain V1 — MNQ 1-minute stateful market observer ── */}
              <VisualBrainPanel
                authHeader={getAuthHeader()['Authorization'] ?? ''}
              />

              {/* ── Databento Live Market Chart ─────────────────────────────── */}
              <LiveMarketChart
                ticker={ticker}
                onInstrumentChange={handleSetTicker}
                authHeader={getAuthHeader()['Authorization']}
                tall={section === 'desk'}
              />

              {/* ── Databento MBP-1 selected-instrument pressure ───────────── */}
              <div style={{ marginTop:12 }}>
                <TopOfBookPressurePanel p={p} ticker={ticker} />
              </div>

              {/* ── Cleanest Trade Available button strip ──────────────────── */}
              <CleanestTradeButton
                scanResult={cleanestScan}
                scanning={cleanestScanning}
                onScan={handleScanCleanest}
                setOpen={() => setCleanestOpen(true)}
              />

              {/* ── Quick Trade Bar ──────────────────────────────────────── */}
              <QuickTradeBar p={p} />

              {/* Section breadcrumb — visible on sub-section pages */}
              {section !== '' && KNOWN_SECTIONS.includes(section) && (
                <div style={{ marginBottom:12, display:'flex', alignItems:'center', gap:10 }}>
                  <span style={{ fontSize:11, color:T.cyan, fontWeight:700, letterSpacing:'0.08em' }}>
                    {(SECTION_LABELS[section] ?? section).toUpperCase()}
                  </span>
                  <Link to="/main-brain" style={{ fontSize:10, color:T.txtMuted, textDecoration:'none',
                    borderRadius:4, padding:'2px 8px', background:`${T.border}60`,
                    border:`1px solid ${T.border}`, display:'inline-flex', alignItems:'center', gap:4 }}>
                    ← Overview
                  </Link>
                </div>
              )}

              {/* Section panels */}
              {renderSectionPanels()}
            </>
          )}
        </main>

        {/* Footer */}
        <footer className="mb-main-footer" style={{ borderTop:`1px solid ${T.border}`, padding:'8px 20px', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <span style={{ fontSize:9.5, color:T.txtMuted }}>V1 Main Brain Operator Console — read-only display, no backend mutations</span>
          <span style={{ fontSize:9.5, color:T.txtMuted, fontFamily:T.mono }}>Poll: {POLL_INTERVAL_MS / 1000}s</span>
        </footer>
      </div>

      {/* Ask AI panel — rendered outside the scroll container so it floats
          over all content. Shares the same /api/assistant endpoint and
          brain_auth credentials as every other call in this file.
          Opening/closing does NOT create a second polling stream. */}
      <AskAiPanel
        open={askOpen}
        onClose={() => setAskOpen(false)}
        ticker={ticker}
        p={p}
      />

      {/* Opening bell pill — full-width bottom bar at 9:30 AM ET */}
      {openingBell && (
        <OpeningBellPill
          state={openingBell}
          p={p}
          onDismiss={() => setOpeningBell(null)}
        />
      )}

      {/* Bell toast — fixed bottom-centre banner, auto-dismisses after 4.5 s.
          Offset upward when the opening bell pill is visible so they don't overlap. */}
      {bellToast && !openingBell && (
        <BellToast data={bellToast} onDismiss={() => setBellToast(null)} />
      )}

      {/* Cleanest Trade modal — rendered outside the scroll container */}
      <CleanestTradeModal
        open={cleanestOpen}
        onClose={() => setCleanestOpen(false)}
        scanResult={cleanestScan}
        activeInstrument={ticker}
        hasActiveTrade={
          Array.isArray(((p.active_trades ?? {}) as Record<string, unknown>).trades) &&
          ((((p.active_trades ?? {}) as Record<string, unknown>).trades) as unknown[]).length > 0
        }
      />

      {/* Responsive styles */}
      <style>{`
        .mb-mobile-menu-toggle { display: none; }
        .mb-mobile-nav-overlay { position: fixed; inset: 0; z-index: 120; background: rgba(2, 8, 20, 0.68); backdrop-filter: blur(3px); }
        .mb-mobile-nav-drawer { width: min(340px, calc(100vw - 32px)); height: 100%; background: #071327; border-right: 1px solid ${T.borderMid}; box-shadow: 16px 0 36px rgba(0,0,0,0.38); overflow-y: auto; padding: calc(env(safe-area-inset-top) + 16px) 14px calc(env(safe-area-inset-bottom) + 18px); animation: mbNavIn 0.18s ease-out; }
        .mb-mobile-nav-head { display:flex; align-items:center; justify-content:space-between; padding:0 5px 14px; border-bottom:1px solid ${T.border}; }
        .mb-mobile-nav-close { width:36px; height:36px; border-radius:9px; border:1px solid ${T.borderMid}; color:${T.txtPri}; background:${T.panel}; font-size:24px; line-height:1; cursor:pointer; }
        .mb-mobile-nav-section-label { margin:18px 6px 8px; font-size:9px; font-weight:800; color:${T.txtMuted}; letter-spacing:0.12em; text-transform:uppercase; }
        .mb-mobile-nav-items { display:flex; flex-direction:column; gap:5px; }
        .mb-mobile-nav-item { min-height:46px; display:flex; align-items:center; gap:12px; padding:10px 12px; border:1px solid ${T.border}; border-radius:9px; color:${T.txtSec}; text-decoration:none; font-size:13px; font-weight:700; letter-spacing:0.01em; }
        .mb-mobile-nav-system { display:flex; align-items:center; gap:8px; margin:20px 5px 0; padding-top:14px; border-top:1px solid ${T.border}; color:${T.txtMuted}; font-size:10px; }
        @keyframes mbNavIn { from { opacity:0; transform:translateX(-18px); } to { opacity:1; transform:translateX(0); } }
        @media (max-width: 1024px) {
          .mb-grid-3 { grid-template-columns: 1fr 1fr !important; }
          .mb-training-stats { grid-template-columns: repeat(3, minmax(0, 1fr)) !important; }
        }
        @media (max-width: 768px) {
          .mb-grid-3, .mb-grid-2, .mb-training-stats, .mb-verdict-row { grid-template-columns: 1fr !important; }
          .mb-desktop-sidenav { display: none !important; }
          .mb-mobile-menu-toggle { width:44px; height:44px; display:inline-flex; align-items:center; justify-content:center; flex-shrink:0; border-radius:8px; border:1px solid ${T.borderMid}; color:${T.cyan}; background:${T.cyan}12; cursor:pointer; font-size:20px; line-height:1; }
          .mb-main-header { min-height:58px !important; height:auto !important; padding:8px 12px !important; gap:8px !important; flex-wrap:wrap; }
          .mb-header-brand { min-width:0; }
          .mb-header-context, .mb-header-page-nav { display:none !important; }
          .mb-header-actions { gap:6px !important; margin-left:auto !important; }
          .mb-header-actions > span { display:none; }
          .mb-header-actions button { padding:8px 10px !important; min-height:44px; min-width:44px; }
          .mb-main-content { padding:12px 12px 28px !important; }
          .mb-main-footer { padding:10px 12px calc(10px + env(safe-area-inset-bottom)) !important; gap:4px; align-items:flex-start !important; flex-direction:column; }
          .mb-panel, .mb-verdict-row, .mb-main-content { min-width:0; overflow-wrap:anywhere; }
          .mb-login-overlay { padding:max(16px, env(safe-area-inset-top)) 16px max(16px, env(safe-area-inset-bottom)) !important; }
          .mb-login-card { width:100% !important; max-width:360px; padding:22px 18px !important; }
          .mb-login-card input, .mb-login-card button { min-height:44px; }
        }
        @media (prefers-reduced-motion: reduce) {
          * { transition: none !important; animation: none !important; }
        }
        @keyframes mbAskSlideIn {
          from { opacity: 0; transform: translateY(14px) scale(0.97); }
          to   { opacity: 1; transform: translateY(0)    scale(1);    }
        }
        @keyframes mbSendSlideIn {
          from { opacity: 0; transform: translateY(-10px) scale(0.97); }
          to   { opacity: 1; transform: translateY(0)     scale(1);    }
        }
        @keyframes mbDot {
          0%, 100% { opacity: 0.25; transform: scale(0.8); }
          50%       { opacity: 1;   transform: scale(1.1); }
        }
        @keyframes mbHistoryIn {
          from { opacity: 0; transform: translateY(5px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes mbHistoryShimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
        @media (max-width: 768px) {
          [data-testid="history-chain-summary"] { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
          [data-testid="verdict-history-timeline"] article { grid-template-columns: 14px 1fr !important; gap: 8px !important; }
          [data-testid="verdict-history-timeline"] article > div:nth-child(3),
          [data-testid="verdict-history-timeline"] article > div:nth-child(4) { grid-column: 2; }
          [data-testid="verdict-history-timeline"] article > div:first-child { grid-row: 1 / span 3; }
        }
        @keyframes bellToastIn {
          from { opacity: 0; transform: translateX(-50%) translateY(10px) scale(0.97); }
          to   { opacity: 1; transform: translateX(-50%) translateY(0)     scale(1);   }
        }
        :focus-visible { outline: 2px solid #38bdf8; outline-offset: 2px; }
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 3px; }
      `}</style>
    </div>
  );
}
