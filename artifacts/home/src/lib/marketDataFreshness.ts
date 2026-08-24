export type MarketDataFreshnessState = 'LIVE' | 'WARMING' | 'STALE' | 'OFFLINE';

export interface Freshness {
  state: MarketDataFreshnessState;
  timestampMs: number | null;
  ageMs: number | null;
  source: 'event' | 'bar' | 'observation' | null;
  current: boolean;
}

export const DATABENTO_EVENT_MAX_AGE_MS = 45_000;
export const DATABENTO_BAR_MAX_AGE_MS = 150_000;
export const VISUAL_BRAIN_MAX_AGE_MS = 180_000;

/** Accept Databento's epoch seconds, epoch milliseconds/nanoseconds, and ISO timestamps. */
export function timestampMs(value: unknown): number | null {
  if (value == null || value === '') return null;
  if (typeof value === 'number' && Number.isFinite(value)) {
    if (Math.abs(value) > 1e15) return Math.floor(value / 1e6); // nanoseconds
    if (Math.abs(value) > 1e11) return Math.floor(value); // milliseconds
    return Math.floor(value * 1000); // epoch seconds
  }
  if (typeof value === 'string') {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && value.trim() !== '') return timestampMs(numeric);
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function latestBarTimestampMs(bars: Array<{ ts?: unknown }> | null | undefined): number | null {
  let latest: number | null = null;
  for (const bar of bars ?? []) {
    const candidate = timestampMs(bar?.ts);
    if (candidate != null && (latest == null || candidate > latest)) latest = candidate;
  }
  return latest;
}

export function formatFreshnessAge(ageMs: number | null): string {
  if (ageMs == null) return '—';
  const seconds = Math.max(0, Math.floor(ageMs / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

function ageFor(timestamp: number | null, now: number): number | null {
  return timestamp == null ? null : Math.max(0, now - timestamp);
}

/**
 * A feed is current only with an explicit connected status and a recent event.
 * Completed one-minute bars are a conservative fallback while the event timestamp
 * is temporarily absent.
 */
export function classifyDatabentoFreshness(input: {
  enabled: unknown;
  connected: unknown;
  lastEventAt?: unknown;
  latestBarAt?: unknown;
  now?: number;
}): Freshness {
  const now = input.now ?? Date.now();
  if (input.enabled !== true || input.connected !== true) {
    return { state: 'OFFLINE', timestampMs: null, ageMs: null, source: null, current: false };
  }
  const eventTs = timestampMs(input.lastEventAt);
  const eventAge = ageFor(eventTs, now);
  if (eventAge != null) {
    return {
      state: eventAge <= DATABENTO_EVENT_MAX_AGE_MS ? 'LIVE' : 'STALE',
      timestampMs: eventTs,
      ageMs: eventAge,
      source: 'event',
      current: eventAge <= DATABENTO_EVENT_MAX_AGE_MS,
    };
  }
  const barTs = timestampMs(input.latestBarAt);
  const barAge = ageFor(barTs, now);
  if (barAge != null) {
    return {
      state: barAge <= DATABENTO_BAR_MAX_AGE_MS ? 'LIVE' : 'STALE',
      timestampMs: barTs,
      ageMs: barAge,
      source: 'bar',
      current: barAge <= DATABENTO_BAR_MAX_AGE_MS,
    };
  }
  return { state: 'WARMING', timestampMs: null, ageMs: null, source: null, current: false };
}

export function classifyVisualBrainFreshness(timestamp: unknown, now = Date.now()): Freshness {
  const observationTs = timestampMs(timestamp);
  const ageMs = ageFor(observationTs, now);
  if (ageMs == null) {
    return { state: 'WARMING', timestampMs: null, ageMs: null, source: null, current: false };
  }
  const current = ageMs <= VISUAL_BRAIN_MAX_AGE_MS;
  return {
    state: current ? 'LIVE' : 'STALE',
    timestampMs: observationTs,
    ageMs,
    source: 'observation',
    current,
  };
}