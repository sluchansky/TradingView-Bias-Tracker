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

  it('implements cursor navigation without mutating the audit surface', () => {
    expect(source).toContain('before_event_id');
    expect(source).toContain('older_before_event_id');
    expect(source).toContain('resume_before_event_id');
    expect(source).toContain('through_event_id');
    expect(source).toContain('resume_through_event_id');
    expect(source).toContain('button-older-verdict-history');
    expect(source).toContain('button-newer-verdict-history');
    expect(source).toContain('cursorStack');
    expect(source).toContain('data.page.older_before_event_id');
    expect(source).toContain('data?.page?.newer_boundary_status');
    expect(source).toContain('NEWER BOUNDARY:');
    expect(source).toContain('CONTIGUOUS · VERIFIED');
    expect(source).toContain('BROKEN · CONTINUITY NOT VERIFIED');
    expect(source).toContain('LATEST SNAPSHOT');
    expect(source).toContain('setCursorStack([])');
  });

  it('jumps by exact event ID or UTC timestamp through the same scoped cursor report', () => {
    expect(source).toContain("query.set('event_id', jump.eventId)");
    expect(source).toContain("query.set('timestamp', jump.timestamp)");
    expect(source).toContain('input-history-jump-event-id');
    expect(source).toContain('input-history-jump-timestamp');
    expect(source).toContain('button-jump-verdict-history');
    expect(source).toContain('INCIDENT ANCHOR RESOLVED');
    expect(source).toContain('Timestamp jumps select the first immutable event recorded at or after');
  });

  it('keeps missing and broken incident results explicit with no alternate data', () => {
    expect(source).toContain('status-history-jump-not-found');
    expect(source).toContain('status-history-jump-unavailable');
    expect(source).toContain('No live or alternate data is shown.');
    expect(source).toContain('OLDER BOUNDARY:');
    expect(source).toContain('NEWER BOUNDARY:');
    expect(source).toContain('BROKEN · CONTINUITY NOT VERIFIED');
  });
});