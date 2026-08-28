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

  it('restores and shares only resolved, canonical, read-only incident links', () => {
    expect(source).toContain('readVerdictHistoryUrl');
    expect(source).toContain('writeVerdictHistoryUrl(instrument, mode, data.jump.resolved_event_id)');
    expect(source).toContain("query.set('instrument', instrument)");
    expect(source).toContain("query.set('mode', mode)");
    expect(source).toContain("query.set('event_id', String(eventId))");
    expect(source).toContain("window.history.replaceState");
    expect(source).toContain('HISTORY_INSTRUMENTS');
    expect(source).toContain('HISTORY_MODES');
    expect(source).toContain('The shared incident link is incomplete.');
    expect(source).toContain('The shared incident link must use a canonical mode.');
    expect(source).toContain('urlError');
    expect(source).toContain('clearVerdictHistoryUrl');
  });

  it('offers explicit copy feedback only for the resolved canonical incident URL', () => {
    expect(source).toContain('Copy incident link');
    expect(source).toContain('button-copy-verdict-history');
    expect(source).toContain("data?.jump?.status === 'RESOLVED'");
    expect(source).toContain('data.jump.resolved_event_id != null');
    expect(source).toContain("url.searchParams.get('instrument')");
    expect(source).toContain("url.searchParams.get('mode')");
    expect(source).toContain("url.searchParams.get('event_id')");
    expect(source).toContain('navigator.clipboard?.writeText');
    expect(source).toContain('await navigator.clipboard.writeText(url.toString())');
    expect(source).toContain('status-history-copy-success');
    expect(source).toContain('status-history-copy-unavailable');
    expect(source).toContain('Clipboard unavailable. Copy the URL from the address bar.');
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