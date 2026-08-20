import { describe, expect, it } from 'vitest';
import { visualBrainConfidence, visualBrainText, visualBrainToken } from '../visualBrainModes';

describe('Visual Brain mode assessment display safety', () => {
  it('renders valid text and tokens', () => {
    expect(visualBrainText('VWAP support')).toBe('VWAP support');
    expect(visualBrainToken('TRIGGER_READY')).toBe('TRIGGER READY');
    expect(visualBrainConfidence(72)).toBe('72% conf');
  });

  it('falls back instead of rendering malformed persisted JSON values', () => {
    expect(visualBrainText({ level: 'VWAP' })).toBe('—');
    expect(visualBrainText(['target'])).toBe('—');
    expect(visualBrainToken(null)).toBe('—');
    expect(visualBrainConfidence('72')).toBe('—');
  });
});