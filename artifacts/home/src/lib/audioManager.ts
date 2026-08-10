/**
 * AudioManager — centralized audio notification service.
 *
 * All sounds are synthesized via the Web Audio API (no external files,
 * no base64 blobs). The manager is a module-level singleton so every
 * consumer shares one AudioContext and one set of throttle timers.
 *
 * Design goals:
 *  - Single play() entry point; consumers never touch AudioContext directly.
 *  - Throttle per event: duplicate events within the throttle window are silently
 *    dropped. READY_TO_TRADE has a long throttle so callers are responsible for
 *    only calling play() on a NOT_READY → READY transition.
 *  - Mute synced with the existing `brain_muted` localStorage key (same key used
 *    by Home.tsx TTS so one mute button silences everything).
 *  - AudioContext created lazily on first play() call — satisfies browser
 *    autoplay policy (context must be created / resumed inside a user gesture
 *    chain, or after one has occurred).
 *  - Designed for future extension: volume, sound packs, voice notifications,
 *    user preferences — none require touching callers.
 */

// ── Event constants ───────────────────────────────────────────────────────────

export const SoundEvent = {
  /** Left Brain discovered a new scanner opportunity. */
  SCAN_FOUND: 'SCAN_FOUND',
  /** All required confirmations passed — primary signature sound. */
  READY_TO_TRADE: 'READY_TO_TRADE',
  /** An order has been sent to the broker. */
  ORDER_SUBMITTED: 'ORDER_SUBMITTED',
  /** Broker confirmed order fill. */
  ORDER_FILLED: 'ORDER_FILLED',
  /** Data degradation / high latency / feed issue. */
  WARNING: 'WARNING',
  /** Hard failure — distinct from WARNING. */
  ERROR: 'ERROR',
  /** Connection established / system came online. */
  SYSTEM_ONLINE: 'SYSTEM_ONLINE',
  /** Connection lost / system went offline. */
  SYSTEM_OFFLINE: 'SYSTEM_OFFLINE',
  /** NYSE-style opening bell at 9:30 AM ET. */
  MARKET_OPEN: 'MARKET_OPEN',
} as const;

export type SoundEventType = typeof SoundEvent[keyof typeof SoundEvent];

// ── Throttle windows (ms) per event ──────────────────────────────────────────

const THROTTLE_MS: Record<string, number> = {
  SCAN_FOUND:      3_000,
  READY_TO_TRADE: 30_000,  // long — callers must also gate on NOT_READY→READY
  ORDER_SUBMITTED:    500,
  ORDER_FILLED:     1_000,
  WARNING:          5_000,
  ERROR:            5_000,
  SYSTEM_ONLINE:   10_000,
  SYSTEM_OFFLINE:  10_000,
  MARKET_OPEN:  3_600_000,  // once per session — caller also gates on date
};

// ── Singleton class ───────────────────────────────────────────────────────────

class AudioManager {
  private ctx: AudioContext | null = null;
  private _muted: boolean;
  private _volume: number = 0.85; // master volume 0–1
  private lastPlayed = new Map<string, number>();

  constructor() {
    this._muted = this._readMuted();
    if (typeof window !== 'undefined') {
      // Keep mute in sync when another component (e.g. Home.tsx mute button)
      // writes the shared brain_muted key.
      window.addEventListener('storage', (e: StorageEvent) => {
        if (e.key === 'brain_muted') {
          this._muted = e.newValue === '1';
        }
      });
    }
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  /** Play a named sound event. Silently drops if muted or throttled. */
  play(event: SoundEventType): void {
    // Re-read muted from storage on every play so changes made by other
    // components (e.g. Home.tsx mute button) are reflected immediately.
    this._muted = this._readMuted();
    if (this._muted) return;

    const throttleMs = THROTTLE_MS[event] ?? 1_000;
    const now = Date.now();
    const last = this.lastPlayed.get(event) ?? 0;
    if (now - last < throttleMs) return;
    this.lastPlayed.set(event, now);

    const ctx = this._getCtx();
    if (!ctx) return;

    if (ctx.state === 'suspended') {
      ctx.resume().then(() => this._synthesize(event, ctx)).catch(() => {/* noop */});
      return;
    }
    this._synthesize(event, ctx);
  }

  /** Programmatically mute / unmute. Writes the shared brain_muted key. */
  setMuted(muted: boolean): void {
    this._muted = muted;
    try { localStorage.setItem('brain_muted', muted ? '1' : '0'); } catch {/* noop */}
  }

  get muted(): boolean { return this._muted; }

  /**
   * Set master volume (0–1). Does not affect in-flight notes.
   * Future: expose to user preferences / settings panel.
   */
  setVolume(v: number): void {
    this._volume = Math.max(0, Math.min(1, v));
  }

  get volume(): number { return this._volume; }

  /**
   * Reset the throttle for a specific event so the next call fires immediately.
   * Useful when the caller knows a state genuinely changed (e.g. ticker switch).
   */
  resetThrottle(event: SoundEventType): void {
    this.lastPlayed.delete(event);
  }

  // ── Internal ────────────────────────────────────────────────────────────────

  private _readMuted(): boolean {
    try { return localStorage.getItem('brain_muted') === '1'; } catch { return false; }
  }

  private _getCtx(): AudioContext | null {
    if (this.ctx && this.ctx.state !== 'closed') return this.ctx;
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const AC = window.AudioContext ?? (window as any).webkitAudioContext;
      if (!AC) return null;
      this.ctx = new AC() as AudioContext;
      return this.ctx;
    } catch {
      return null;
    }
  }

  private _synthesize(event: string, ctx: AudioContext): void {
    try {
      switch (event) {
        case SoundEvent.SCAN_FOUND:      this._playScanFound(ctx);      break;
        case SoundEvent.READY_TO_TRADE:  this._playReadyToTrade(ctx);   break;
        case SoundEvent.ORDER_SUBMITTED: this._playOrderSubmitted(ctx);  break;
        case SoundEvent.ORDER_FILLED:    this._playOrderFilled(ctx);     break;
        case SoundEvent.WARNING:         this._playWarning(ctx);         break;
        case SoundEvent.ERROR:           this._playError(ctx);           break;
        case SoundEvent.SYSTEM_ONLINE:   this._playSystemOnline(ctx);    break;
        case SoundEvent.SYSTEM_OFFLINE:  this._playSystemOffline(ctx);   break;
        case SoundEvent.MARKET_OPEN:     this._playMarketOpen(ctx);      break;
      }
    } catch {/* synthesis errors must never propagate to callers */}
  }

  /**
   * Schedule a single sine tone.
   * All timing is relative to ctx.currentTime so the scheduler is sample-accurate.
   */
  private _tone(
    ctx: AudioContext,
    freq: number,
    startAt: number,
    duration: number,
    peakGain: number,
    attackT: number,
    releaseT: number,
    type: OscillatorType = 'sine',
  ): void {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    const scaled = peakGain * this._volume;

    osc.type = type;
    osc.frequency.value = freq;

    g.gain.setValueAtTime(0, startAt);
    g.gain.linearRampToValueAtTime(scaled, startAt + attackT);
    g.gain.setValueAtTime(scaled, startAt + Math.max(attackT, duration - releaseT));
    g.gain.linearRampToValueAtTime(0, startAt + duration);

    osc.connect(g);
    g.connect(ctx.destination);
    osc.start(startAt);
    osc.stop(startAt + duration + 0.01);
    osc.onended = () => { try { osc.disconnect(); g.disconnect(); } catch {/* noop */} };
  }

  // ── Sound recipes ───────────────────────────────────────────────────────────

  /**
   * SCAN_FOUND — very soft digital tick, <250 ms.
   * High-frequency sine burst: feels like a quiet sensor ping.
   */
  private _playScanFound(ctx: AudioContext): void {
    const t = ctx.currentTime;
    this._tone(ctx, 1400, t, 0.14, 0.055, 0.006, 0.12);
  }

  /**
   * READY_TO_TRADE — clean sonar / futuristic AI chime, 300–500 ms.
   * Two sine partials (fundamental A5 + perfect fifth E6) with a soft
   * attack and exponential tail. Professional, calm, signature sound.
   */
  private _playReadyToTrade(ctx: AudioContext): void {
    const t = ctx.currentTime;
    this._tone(ctx,  880, t,        0.44, 0.16, 0.012, 0.38); // A5 — fundamental
    this._tone(ctx, 1320, t + 0.04, 0.38, 0.08, 0.012, 0.32); // E6 — perfect fifth
    this._tone(ctx,  440, t + 0.02, 0.30, 0.04, 0.012, 0.26); // A4 — sub octave, body
  }

  /**
   * ORDER_SUBMITTED — very short click, <100 ms.
   */
  private _playOrderSubmitted(ctx: AudioContext): void {
    const t = ctx.currentTime;
    this._tone(ctx, 820, t, 0.075, 0.10, 0.005, 0.065);
  }

  /**
   * ORDER_FILLED — short satisfying ascending two-tone.
   */
  private _playOrderFilled(ctx: AudioContext): void {
    const t = ctx.currentTime;
    this._tone(ctx, 660, t,        0.13, 0.13, 0.010, 0.11);
    this._tone(ctx, 880, t + 0.11, 0.18, 0.14, 0.010, 0.15);
  }

  /**
   * WARNING — low two-tone pulse.
   * Used for data degradation, high latency, feed issues.
   */
  private _playWarning(ctx: AudioContext): void {
    const t = ctx.currentTime;
    this._tone(ctx, 220, t,        0.15, 0.11, 0.020, 0.11);
    this._tone(ctx, 277, t + 0.19, 0.15, 0.11, 0.020, 0.11);
  }

  /**
   * ERROR — distinct from WARNING.
   * Dissonant minor-second pair at low frequency: unmistakably wrong.
   */
  private _playError(ctx: AudioContext): void {
    const t = ctx.currentTime;
    this._tone(ctx, 185, t, 0.30, 0.11, 0.015, 0.26);
    this._tone(ctx, 247, t, 0.30, 0.07, 0.015, 0.26); // minor third — tense
  }

  /**
   * SYSTEM_ONLINE — gentle ascending major arpeggio.
   * A4 → C#5 → E5: startup confirmation.
   */
  private _playSystemOnline(ctx: AudioContext): void {
    const t = ctx.currentTime;
    this._tone(ctx, 440, t,        0.15, 0.09, 0.010, 0.12);
    this._tone(ctx, 554, t + 0.14, 0.15, 0.09, 0.010, 0.12);
    this._tone(ctx, 659, t + 0.28, 0.18, 0.11, 0.010, 0.15);
  }

  /**
   * SYSTEM_OFFLINE — soft descending arpeggio.
   * E5 → C#5 → A4: gentle disconnect tone.
   */
  private _playSystemOffline(ctx: AudioContext): void {
    const t = ctx.currentTime;
    this._tone(ctx, 659, t,        0.14, 0.08, 0.010, 0.12);
    this._tone(ctx, 554, t + 0.14, 0.14, 0.08, 0.010, 0.12);
    this._tone(ctx, 440, t + 0.28, 0.16, 0.06, 0.010, 0.17);
  }

  /**
   * MARKET_OPEN — NYSE-style opening bell.
   * Five rapid strikes (like the real exchange floor bell), each with
   * a C-major bell chord (fundamental + octave + 3rd + 5th + 2nd octave).
   * Fast 5 ms attack, long 2.2 s exponential decay per strike.
   * Sounds distinct and ceremonial — impossible to confuse with any alert.
   */
  private _playMarketOpen(ctx: AudioContext): void {
    const vol = this._volume;
    // 5 strikes, 420 ms apart — matches the real NYSE cadence
    const strikes = [0, 0.42, 0.84, 1.26, 1.68];
    // Bell partials: fundamental C5 + harmonics
    const partials: Array<{ freq: number; gain: number }> = [
      { freq: 523.25, gain: 0.42 }, // C5  — fundamental
      { freq: 1046.5, gain: 0.26 }, // C6  — octave
      { freq: 1318.5, gain: 0.17 }, // E6  — major third
      { freq: 1568.0, gain: 0.10 }, // G6  — fifth
      { freq: 2093.0, gain: 0.05 }, // C7  — second octave shimmer
    ];
    strikes.forEach(delay => {
      partials.forEach(({ freq, gain }) => {
        const osc = ctx.createOscillator();
        const g   = ctx.createGain();
        osc.connect(g);
        g.connect(ctx.destination);
        osc.type = 'sine';
        osc.frequency.value = freq;
        const t0 = ctx.currentTime + delay;
        g.gain.setValueAtTime(0, t0);
        g.gain.linearRampToValueAtTime(gain * vol, t0 + 0.005); // 5 ms attack
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 2.2);  // 2.2 s bell decay
        osc.start(t0);
        osc.stop(t0 + 2.25);
        osc.onended = () => { try { osc.disconnect(); g.disconnect(); } catch {/* noop */} };
      });
    });
  }
}

// ── Singleton export ──────────────────────────────────────────────────────────

/**
 * Module-level singleton.  Import this directly; do not `new AudioManager()`.
 *
 * Usage:
 *   import { audioManager, SoundEvent } from '@/lib/audioManager';
 *   audioManager.play(SoundEvent.READY_TO_TRADE);
 */
export const audioManager = new AudioManager();
