/**
 * Display-only training lane metadata.
 *
 * These identifiers describe dashboard organization only. They do not select a
 * live trading mode, alter a strategy, or permit a promotion action.
 */
export const TRAINING_LANES = {
  scalp: {
    section: 'scalp',
    label: 'SCALP',
    apiMode: 'SCALP',
    description: 'Fast-context observations, simulation evidence, and resolved outcomes.',
  },
  intraday: {
    section: 'intraday',
    label: 'INTRADAY',
    apiMode: 'INTRADAY_TREND',
    description: 'Intraday Trend qualification evidence, outcomes, and strategy funnels.',
  },
  strategyLab: {
    section: 'strategy-lab',
    label: 'STRATEGY LAB',
    description: 'Shadow experiments and manual promotion review only.',
  },
} as const;

export type TrainingLaneSection = keyof typeof TRAINING_LANES;

/**
 * Keeps prior bookmarked research URLs useful without retaining "Research" as a
 * fourth, ambiguous training lane.
 */
export function normalizeTrainingSection(section: string): string {
  return section === 'research' ? TRAINING_LANES.strategyLab.section : section;
}

export function isTrainingLane(section: string): boolean {
  return Object.values(TRAINING_LANES).some(lane => lane.section === section);
}