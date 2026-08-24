import { describe, expect, it } from 'vitest';
import {
  classifyDatabentoFreshness,
  classifyVisualBrainFreshness,
  latestBarTimestampMs,
  timestampMs,
} from '../marketDataFreshness';

describe('market data freshness contract', () => {
  const now = 1_700_000_000_000;

  it('normalizes ISO, seconds, milliseconds, and nanoseconds without guessing a live state', () => {
    expect(timestampMs(1_700_000_000)).toBe(now);
    expect(timestampMs(now)).toBe(now);
    expect(timestampMs(now * 1_000_000)).toBe(now);
    expect(timestampMs('2023-11-14T22:13:20.000Z')).toBe(now);
  });

  it('uses the authoritative event timestamp before a completed bar', () => {
    const result = classifyDatabentoFreshness({
      enabled: true,
      connected: true,
      lastEventAt: now - 20_000,
      latestBarAt: now - 90_000,
      now,
    });
    expect(result).toMatchObject({ state: 'LIVE', source: 'event', current: true });
  });

  it('fails closed when the feed disconnects or its event becomes stale', () => {
    expect(classifyDatabentoFreshness({
      enabled: true, connected: false, lastEventAt: now - 1_000, now,
    }).state).toBe('OFFLINE');
    expect(classifyDatabentoFreshness({
      enabled: true, connected: true, lastEventAt: now - 46_000, now,
    }).state).toBe('STALE');
  });

  it('uses a recent bar only as a bounded fallback and exposes observation staleness', () => {
    expect(latestBarTimestampMs([{ ts: now / 1000 - 10 }, { ts: now / 1000 - 30 }])).toBe(now - 10_000);
    expect(classifyDatabentoFreshness({
      enabled: true, connected: true, latestBarAt: now - 149_000, now,
    }).state).toBe('LIVE');
    expect(classifyVisualBrainFreshness(now - 181_000, now).state).toBe('STALE');
  });
});