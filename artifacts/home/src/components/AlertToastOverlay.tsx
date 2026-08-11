/**
 * AlertToastOverlay — persistent fixed bottom-centre pill shown on every page.
 *
 * Mounted once in App.tsx; no ticker prop required — reads the active ticker
 * from localStorage('mb_ticker') on every poll so it stays in sync with
 * whichever instrument the user is viewing in MainBrain.
 *
 * Behaviour:
 *  • Shows whenever is_actionable=true (green, prominent).
 *  • Hides automatically when the status drops back to WAIT/not-actionable.
 *  • No auto-dismiss timer — the pill stays until the user taps ×.
 *  • After dismissal it re-surfaces on the next READY transition (different
 *    instrument, or same instrument going WAIT→READY again).
 *  • Polls every 5 s, paused when the browser tab is hidden.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { audioManager, SoundEvent } from '../lib/audioManager';
import { normalizeMainBrainPayload } from '../lib/mainBrainNormalizer';

// ── Inject keyframe once into the document head ───────────────────────────────
let _kfInjected = false;
function ensureKeyframe() {
  if (_kfInjected || typeof document === 'undefined') return;
  _kfInjected = true;
  const s = document.createElement('style');
  s.textContent = `
    @keyframes alertToastIn {
      from { opacity: 0; transform: translateX(-50%) translateY(12px) scale(0.96); }
      to   { opacity: 1; transform: translateX(-50%) translateY(0)     scale(1);   }
    }
    @keyframes alertToastOut {
      from { opacity: 1; transform: translateX(-50%) translateY(0)     scale(1);   }
      to   { opacity: 0; transform: translateX(-50%) translateY(8px)  scale(0.96); }
    }
  `;
  document.head.appendChild(s);
}

// ── Colour constants ──────────────────────────────────────────────────────────
const C = {
  green:    '#30d158',
  cyan:     '#32d0d8',
  amber:    '#f59e0b',
  txtSec:   '#8e98a4',
  txtMuted: '#4a5260',
};

// ── Tiny helpers ──────────────────────────────────────────────────────────────
function ss(v: unknown, fb = ''): string { return typeof v === 'string' ? v : fb; }
function sn(v: unknown): number | null   { const n = Number(v); return Number.isFinite(n) ? n : null; }
function getTicker(): string {
  try { return localStorage.getItem('mb_ticker') || 'MNQ'; } catch { return 'MNQ'; }
}

// ── Types ─────────────────────────────────────────────────────────────────────
interface PillData {
  /** Unique key — changes when the READY event is a new transition */
  readyKey: string;
  icon:     string;
  label:    string;
  sublabel: string;
  color:    string;
}

// ── Persistent pill UI ────────────────────────────────────────────────────────
const Pill: React.FC<{ data: PillData; onDismiss: () => void }> = ({ data, onDismiss }) => (
  <div
    role="status"
    aria-live="polite"
    style={{
      position: 'fixed', bottom: 20, left: '50%',
      transform: 'translateX(-50%)',
      zIndex: 9999,
      animation: 'alertToastIn 0.22s ease forwards',
      background: 'rgba(10,17,26,0.97)',
      border: `1px solid ${data.color}60`,
      borderRadius: 999,
      padding: '9px 14px 9px 12px',
      display: 'flex', alignItems: 'center', gap: 9,
      boxShadow: `0 4px 24px rgba(0,0,0,0.6), 0 0 0 1px ${data.color}20`,
      backdropFilter: 'blur(18px)',
      maxWidth: 420,
      userSelect: 'none',
    }}
  >
    {/* Pulse dot */}
    <span style={{ position: 'relative', flexShrink: 0, width: 8, height: 8 }}>
      <span style={{
        position: 'absolute', inset: 0,
        background: data.color, borderRadius: '50%',
        animation: 'pulse 1.8s ease-in-out infinite',
      }} />
      <style>{`
        @keyframes pulse {
          0%,100% { transform: scale(1); opacity: 1; }
          50%      { transform: scale(1.9); opacity: 0.35; }
        }
      `}</style>
    </span>

    <span style={{ fontSize: 15, lineHeight: 1, flexShrink: 0 }}>{data.icon}</span>

    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{
        fontSize: 12, fontWeight: 700, color: data.color,
        letterSpacing: '0.07em', whiteSpace: 'nowrap',
        overflow: 'hidden', textOverflow: 'ellipsis',
      }}>
        {data.label}
      </div>
      {data.sublabel && (
        <div style={{
          fontSize: 10, color: C.txtSec, marginTop: 1,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {data.sublabel}
        </div>
      )}
    </div>

    {/* Dismiss × */}
    <button
      onClick={(e) => { e.stopPropagation(); onDismiss(); }}
      aria-label="Dismiss"
      style={{
        flexShrink: 0, marginLeft: 4,
        background: 'none', border: 'none', padding: '2px 4px',
        cursor: 'pointer', color: C.txtMuted,
        fontSize: 12, lineHeight: 1,
        borderRadius: 4,
      }}
    >×</button>
  </div>
);

// ── Main export ───────────────────────────────────────────────────────────────
/**
 * Mount once in App.tsx — covers every page without prop drilling.
 * Optionally pass `ticker` to pin a specific instrument (used by pages
 * that still want per-instrument control). When omitted the component
 * reads `localStorage('mb_ticker')` automatically.
 */
export const AlertToastOverlay: React.FC<{ ticker?: string }> = ({ ticker: tickerProp }) => {
  const [pill, setPill]             = useState<PillData | null>(null);
  const [dismissed, setDismissed]   = useState(false);

  // Track the readyKey of the dismissed event so we can re-surface on change
  const dismissedKeyRef             = useRef<string>('');
  const prevReadyKeyRef             = useRef<string>('');

  useEffect(() => { ensureKeyframe(); }, []);

  const poll = useCallback(async () => {
    const ticker = tickerProp ?? getTicker();
    try {
      const headers: Record<string, string> = {};
      try {
        const pwd = localStorage.getItem('brain_auth');
        if (pwd) headers['Authorization'] = 'Basic ' + btoa('admin:' + pwd);
      } catch { /* ignore */ }

      const res = await fetch(`/api/main-brain?ticker=${ticker}`, {
        credentials: 'include', headers,
      });
      if (!res.ok) return;

      const raw = await res.json();
      const p   = normalizeMainBrainPayload(raw);

      const verdict = (p.verdict           ?? {}) as Record<string, unknown>;
      const market  = (p.market            ?? {}) as Record<string, unknown>;
      const cp      = (p.candidate_preview ?? {}) as Record<string, unknown>;
      const eb      = (p.edge_breakdown    ?? {}) as Record<string, unknown>;
      const sc      = (p.strategy_scanner  ?? {}) as Record<string, unknown>;

      const isActionable = verdict.is_actionable === true;

      if (!isActionable) {
        // Status dropped to WAIT — clear the pill and reset so it fires again on next READY
        setPill(null);
        setDismissed(false);
        dismissedKeyRef.current = '';
        prevReadyKeyRef.current = '';
        return;
      }

      // Build a stable key for this READY event (ticker + direction + strategy)
      const inst  = ss(market.instrument, ticker);
      const dir   = ss(cp.direction ?? verdict.direction, '');
      const strat = ss(sc.selected_strategy, '');
      const readyKey = `${inst}|${dir}|${strat}`;

      // Only fire sound + re-surface on a NEW transition
      if (readyKey !== prevReadyKeyRef.current) {
        audioManager.play(SoundEvent.READY_TO_TRADE);
        prevReadyKeyRef.current = readyKey;

        // If this is a different event from what was dismissed → re-surface
        if (dismissedKeyRef.current !== readyKey) {
          setDismissed(false);
        }
      }

      // Build pill content
      const score = sn(eb.score ?? eb.total_score);
      const parts = (
        [dir, score != null ? `Edge ${score}` : null, strat || null] as (string | null)[]
      ).filter(Boolean);

      setPill({
        readyKey,
        icon:     '🔔',
        label:    `${inst}  READY TO TRADE`,
        sublabel: parts.join('  ·  '),
        color:    C.green,
      });
    } catch {
      // Network errors silently ignored — advisory only
    }
  }, [tickerProp]);

  // Poll every 5 s, paused when tab is hidden
  useEffect(() => {
    poll();
    const id = setInterval(() => { if (!document.hidden) poll(); }, 5_000);
    return () => clearInterval(id);
  }, [poll]);

  const handleDismiss = useCallback(() => {
    if (pill) dismissedKeyRef.current = pill.readyKey;
    setDismissed(true);
  }, [pill]);

  if (!pill || dismissed) return null;
  return <Pill data={pill} onDismiss={handleDismiss} />;
};
