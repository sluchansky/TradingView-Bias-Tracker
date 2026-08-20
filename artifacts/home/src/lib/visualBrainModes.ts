export function visualBrainText(value: unknown, fallback = '—'): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

export function visualBrainToken(value: unknown, fallback = '—'): string {
  return visualBrainText(value, fallback).replace(/_/g, ' ');
}

export function visualBrainConfidence(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${Math.round(value)}% conf`
    : '—';
}