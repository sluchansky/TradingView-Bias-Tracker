/**
 * globalAlerts.ts — shared types and localStorage helpers for GlobalAlertDock.
 *
 * IMPORTANT: UI / display only. No trading logic, no broker calls.
 */

// ── Alert type catalogue ──────────────────────────────────────────────────────

export type AlertType =
  | 'LIVE_READY'
  | 'ORB_SHADOW_QUALIFIED'
  | 'ORB_BREAKOUT_MISSED'
  | 'SYSTEM_SAFETY'
  | 'RESEARCH_READY_FOR_REVIEW';  // Phase 2 Ghost Research Engine — research finding only, NOT LIVE

// ── Core alert shape ──────────────────────────────────────────────────────────

export interface AlertItem {
  /** Stable dedup key — same event across polls shares the same id. */
  id: string;
  type: AlertType;
  instrument: string;      // MGC | MNQ | MES | MYM
  direction?: string;      // Long | Short
  strategy?: string;
  edgeScore?: number;
  timestamp: number;       // ms epoch when the alert was first created
  acknowledged: boolean;
  isShadow: boolean;

  // Display text
  label: string;
  sublabel: string;

  // LIVE_READY detail
  entry?: number | null;
  stop?: number | null;
  tp1?: number | null;
  tp2?: number | null;
  confirmations?: string;

  // ORB_SHADOW detail
  orbState?: string;
  orbRangeHigh?: number | null;
  orbRangeLow?: number | null;
  orbBreakoutLevel?: number | null;
  orbEntry?: number | null;
  orbStop?: number | null;
  orbTp1?: number | null;
  orbTp2?: number | null;
  orbContracts?: number | null;
  orbConfirmMode?: string;
  orbTradingDate?: string;
  orbBlockReason?: string;
}

// ── LocalStorage keys ─────────────────────────────────────────────────────────

const QUEUE_KEY = 'gad_queue';
const MAX_QUEUE = 100;

// ── Queue helpers ─────────────────────────────────────────────────────────────

export function loadQueue(): AlertItem[] {
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}

export function saveQueue(q: AlertItem[]): void {
  try {
    localStorage.setItem(QUEUE_KEY, JSON.stringify(q.slice(0, MAX_QUEUE)));
  } catch { /* storage full — silently drop */ }
}

/** Prepend a new alert, deduping by id (same id → update in place). */
export function upsertAlert(q: AlertItem[], item: AlertItem): AlertItem[] {
  const without = q.filter(a => a.id !== item.id);
  return [item, ...without].slice(0, MAX_QUEUE);
}

export function ackAlert(q: AlertItem[], id: string): AlertItem[] {
  return q.map(a => a.id === id ? { ...a, acknowledged: true } : a);
}

/** Remove only acknowledged alerts; leaves unseen alerts untouched. */
export function clearAcknowledged(q: AlertItem[]): AlertItem[] {
  return q.filter(a => !a.acknowledged);
}

// ── Instrument switch helper ──────────────────────────────────────────────────

/**
 * Set the active ticker in localStorage AND notify any listening components
 * (e.g. MainBrain) via a custom DOM event. UI-only, never touches gate logic.
 */
export function setActiveTicker(instrument: string): void {
  try { localStorage.setItem('mb_ticker', instrument); } catch { /* ignore */ }
  try {
    window.dispatchEvent(new CustomEvent('mb:ticker', { detail: instrument, bubbles: false }));
  } catch { /* ignore */ }
}
