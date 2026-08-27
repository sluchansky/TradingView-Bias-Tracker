import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('../../index.css', import.meta.url), 'utf8');
const mainBrain = readFileSync(new URL('../../pages/MainBrain.tsx', import.meta.url), 'utf8');
const cockpit = readFileSync(new URL('../../pages/Cockpit.tsx', import.meta.url), 'utf8');
const chart = readFileSync(new URL('../../components/LiveMarketChart.tsx', import.meta.url), 'utf8');

describe('mobile operator console layout contract', () => {
  it('keeps the responsive override scoped to phone widths and safe areas', () => {
    expect(css).toContain('@media (max-width: 768px)');
    expect(css).toContain('env(safe-area-inset-top)');
    expect(css).toContain('env(safe-area-inset-bottom)');
    expect(css).toContain('min-height: 100dvh');
  });

  it('exposes stable hooks for stacked panels, touch targets, and bounded tables', () => {
    expect(mainBrain).toContain('className="mb-panel"');
    expect(mainBrain).toContain('className="mb-header-tickers"');
    expect(mainBrain).toContain('className="mb-journal-page"');
    expect(css).toContain('.mb-journal-page table');
    expect(css).toContain('.mb-mobile-menu-toggle');
  });

  it('keeps charts and cockpit controls responsive without changing their data paths', () => {
    expect(chart).toContain('className="lmc-plot"');
    expect(chart).toContain('className="lmc-selector"');
    expect(cockpit).toContain('className="cockpit-diagnostics-panel"');
    expect(cockpit).toContain('className="cockpit-protection-dot"');
    expect(cockpit).not.toContain('className="cockpit-diagnostics-panel" style={{\n            width: "6px"');
    expect(cockpit).toContain('className="cockpit-trade-ticket"');
    expect(cockpit).toContain('className="cockpit-overlay"');
    expect(css).toContain('grid-template-columns: repeat(2, minmax(0, 1fr))');
    expect(css).toContain('z-index: 10000 !important');
  });
});