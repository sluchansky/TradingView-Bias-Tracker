import { describe, expect, it } from 'vitest';
import { matchesRequiredBlocker } from '../blockerSemantics';

describe('Cockpit blocker semantics', () => {
  it('keeps an unmapped false diagnostic contextual when no blocker is named', () => {
    expect(matchesRequiredBlocker('', 'zone_present', undefined)).toBe(false);
  });

  it('keeps an unmapped false diagnostic contextual when another blocker is named', () => {
    expect(matchesRequiredBlocker('vwap confirmation required', 'zone_present', undefined)).toBe(false);
  });

  it('marks a mapped diagnostic required only when the backend names that blocker', () => {
    expect(matchesRequiredBlocker('blocked by vwap confirmation', 'vwap', 'vwap gate')).toBe(true);
  });
});