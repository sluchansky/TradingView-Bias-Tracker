import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { NAV_ITEMS } from '../navItems';

const source = readFileSync(new URL('../../pages/MainBrain.tsx', import.meta.url), 'utf8');

describe('immutable verdict history operator contract', () => {
  it('is reachable and uses only the dedicated read-only history API', () => {
    expect(NAV_ITEMS).toContainEqual(expect.objectContaining({
      id: 'verdict-history',
      path: '/main-brain/verdict-history',
    }));
    expect(source).toContain('/api/authoritative-verdict-history?');
    expect(source).toContain('READ ONLY · IMMUTABLE');
    expect(source).toContain('This view cannot influence trading.');
  });

  it('renders explicit unavailable, empty, partial, and broken chain states', () => {
    expect(source).toContain('status-history-unavailable');
    expect(source).toContain('History is available, but no events exist for this selection.');
    expect(source).toContain("chain_status === 'WINDOW_START'");
    expect(source).toContain('Earlier link is outside this window');
    expect(source).toContain("chain_status === 'BROKEN'");
  });

  it('shows complete blocker and waiting lists separately', () => {
    expect(source).toContain('blockers-verdict-history-');
    expect(source).toContain('waiting-verdict-history-');
    expect(source).toContain('event.blockers.map(value => safeStr(value)).join');
    expect(source).toContain('event.waiting_for.map(value => safeStr(value)).join');
    expect(source).not.toContain('[...event.blockers, ...event.waiting_for].slice(0,2)');
  });
});