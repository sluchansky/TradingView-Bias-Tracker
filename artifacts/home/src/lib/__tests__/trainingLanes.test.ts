import { describe, expect, it } from 'vitest';
import { NAV_ITEMS } from '../navItems';
import { TRAINING_LANES, isTrainingLane, normalizeTrainingSection } from '../trainingLanes';

describe('training lane navigation contract', () => {
  it('keeps exactly the three requested training destinations', () => {
    expect(Object.values(TRAINING_LANES).map(lane => lane.label)).toEqual([
      'SCALP',
      'INTRADAY',
      'STRATEGY LAB',
    ]);
  });

  it('keeps old research bookmarks display-only by redirecting them to Strategy Lab', () => {
    expect(normalizeTrainingSection('research')).toBe('strategy-lab');
    expect(isTrainingLane('strategy-lab')).toBe(true);
    expect(isTrainingLane('research-health')).toBe(false);
  });

  it('exposes the three lanes without retaining the ambiguous Research destination', () => {
    const ids = NAV_ITEMS.map(item => item.id);
    expect(ids).toEqual(expect.arrayContaining(['scalp', 'intraday', 'strategy-lab', 'research-health']));
    expect(ids).not.toContain('research');
  });
});