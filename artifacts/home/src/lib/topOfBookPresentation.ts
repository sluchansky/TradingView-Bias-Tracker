/**
 * Pure presentation contract for the Main Brain MBP-1 pressure card.
 * The backend is authoritative for freshness. This only prevents malformed
 * payloads from being rendered as a live quote in the browser.
 */

export type TopOfBookState = 'LIVE' | 'STALE' | 'UNAVAILABLE';

export interface TopOfBookPresentation {
  state: TopOfBookState;
  live: boolean;
  bid: number | null;
  ask: number | null;
  imbalance: number | null;
  age: number | null;
  message: string | null;
  history: Array<{ t: string; imbalance: number }>;
  cumulativePressure: number | null;
  averageImbalance: number | null;
  historySamples: number;
}

function finiteNumber(value: unknown): number | null {
  const n = Number(value);
  return value != null && Number.isFinite(n) ? n : null;
}

export function getTopOfBookPresentation(raw: Record<string, unknown>): TopOfBookPresentation {
  const rawState = String(raw.state ?? 'UNAVAILABLE').toUpperCase();
  const requestedState: TopOfBookState = rawState === 'LIVE' || rawState === 'STALE'
    ? rawState
    : 'UNAVAILABLE';
  const bid = finiteNumber(raw.bid_size);
  const ask = finiteNumber(raw.ask_size);
  const imbalance = finiteNumber(raw.imbalance);
  const age = finiteNumber(raw.age_s);
  const history = Array.isArray(raw.history)
    ? raw.history.flatMap((point): Array<{ t: string; imbalance: number }> => {
      if (!point || typeof point !== 'object') return [];
      const item = point as Record<string, unknown>;
      const t = typeof item.t === 'string' ? item.t : '';
      const value = finiteNumber(item.imbalance);
      return t && value != null && value >= -1 && value <= 1 ? [{ t, imbalance: value }] : [];
    })
    : [];
  const cumulativePressure = finiteNumber(raw.cumulative_pressure);
  const averageImbalance = finiteNumber(raw.average_imbalance);
  const historySamples = Math.max(0, Math.floor(Number(raw.history_samples) || history.length));
  const shared = { history, cumulativePressure, averageImbalance, historySamples };
  const live = requestedState === 'LIVE'
    && raw.available === true
    && bid != null && bid > 0
    && ask != null && ask > 0
    && imbalance != null && imbalance >= -1 && imbalance <= 1;

  if (live) {
    return { state: 'LIVE', live: true, bid, ask, imbalance, age, message: null, ...shared };
  }
  if (requestedState === 'STALE') {
    return {
      state: 'STALE', live: false, bid: null, ask: null, imbalance: null, age,
      message: 'Bid and ask sizes are intentionally hidden until the feed refreshes.',
      ...shared,
    };
  }
  return {
    state: 'UNAVAILABLE', live: false, bid: null, ask: null, imbalance: null, age: null,
    message: 'No current MBP-1 quote is available for this instrument.',
    ...shared,
  };
}