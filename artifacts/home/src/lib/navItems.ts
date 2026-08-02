/**
 * Navigation items for the Main Brain operator console.
 * Extracted to a pure-TS module so tests can import without React.
 */

export interface NavItem {
  /** Matches the URL segment used in /main-brain/:section */
  id: string;
  /** Display label */
  label: string;
  /** Absolute path for wouter <Link> */
  path: string;
  /** Icon glyph */
  icon: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { id: 'main-brain', label: 'Main Brain',    path: '/main-brain',           icon: '⬡' },
  { id: 'analysis',   label: 'Analysis',      path: '/main-brain/analysis',  icon: '⚡' },
  { id: 'scanner',    label: 'Scanner',       path: '/main-brain/scanner',   icon: '◎' },
  { id: 'desk',       label: 'Trade Desk',    path: '/main-brain/desk',      icon: '⊕' },
  { id: 'trades',     label: 'Active Trades', path: '/main-brain/trades',    icon: '↗' },
  { id: 'execution',  label: 'Execution',     path: '/main-brain/execution', icon: '⊙' },
  { id: 'journal',    label: 'Journal',       path: '/main-brain/journal',   icon: '≡' },
  { id: 'coach',      label: 'Coach',         path: '/main-brain/coach',     icon: '◆' },
  { id: 'alerts',     label: 'Alerts',        path: '/main-brain/alerts',    icon: '◉' },
] as const;

/** Section IDs that map to a dedicated section view (excludes root 'main-brain'). */
export const KNOWN_SECTIONS: readonly string[] = NAV_ITEMS
  .filter(n => n.id !== 'main-brain')
  .map(n => n.id);

/** Quick label lookup by section id. */
export const SECTION_LABELS: Readonly<Record<string, string>> = Object.fromEntries(
  NAV_ITEMS.map(n => [n.id, n.label])
);
