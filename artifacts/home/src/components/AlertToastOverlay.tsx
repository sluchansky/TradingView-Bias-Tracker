/**
 * AlertToastOverlay — shared fixed bottom-centre pill shown on any page
 * when a READY TO TRADE or "new setup found" event fires.
 *
 * Mounts independently with its own 7 s /api/main-brain poll so it works
 * on Home, MobileHome, and Cockpit without any prop drilling of payload data.
 * MainBrain has its own inline BellToast — this component is NOT used there.
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
      from { opacity: 0; transform: translateX(-50%) translateY(10px) scale(0.97); }
      to   { opacity: 1; transform: translateX(-50%) translateY(0)     scale(1);   }
    }
  `;
  document.head.appendChild(s);
}

// ── Colour constants (match MainBrain T palette) ──────────────────────────────
const C = {
  green:    '#30d158',
  cyan:     '#32d0d8',
  txtSec:   '#8e98a4',
  txtMuted: '#4a5260',
};

// ── Types ─────────────────────────────────────────────────────────────────────
interface ToastData {
  key:      number;
  icon:     string;
  label:    string;
  sublabel: string;
  color:    string;
}

// ── Tiny helpers (self-contained — avoids coupling to MainBrain internals) ────
function ss(v: unknown, fb = ''): string { return typeof v === 'string' ? v : fb; }
function sn(v: unknown): number | null   { const n = Number(v); return Number.isFinite(n) ? n : null; }

// ── Toast pill UI ─────────────────────────────────────────────────────────────
const Toast: React.FC<{ data: ToastData; onDismiss: () => void }> = ({ data, onDismiss }) => {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4_500);
    return () => clearTimeout(t);
  }, [data.key, onDismiss]);

  return (
    <div
      role="status"
      aria-live="polite"
      onClick={onDismiss}
      style={{
        position: 'fixed', bottom: 28, left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 9999, cursor: 'pointer',
        animation: 'alertToastIn 0.22s ease forwards',
        background: 'rgba(10,17,26,0.96)',
        border: `1px solid ${data.color}55`,
        borderRadius: 12,
        padding: '10px 18px 10px 14px',
        display: 'flex', alignItems: 'center', gap: 10,
        boxShadow: `0 6px 28px rgba(0,0,0,0.55), 0 0 0 1px ${data.color}18`,
        backdropFilter: 'blur(14px)',
        maxWidth: 460,
      }}
    >
      <span style={{ fontSize: 17, lineHeight: 1, flexShrink: 0 }}>{data.icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 12, fontWeight: 700, color: data.color,
          letterSpacing: '0.06em', whiteSpace: 'nowrap',
          overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {data.label}
        </div>
        {data.sublabel && (
          <div style={{
            fontSize: 10, color: C.txtSec, marginTop: 2,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {data.sublabel}
          </div>
        )}
      </div>
      <span style={{
        fontSize: 9, color: C.txtMuted, flexShrink: 0,
        letterSpacing: '0.04em', marginLeft: 4,
      }}>tap ×</span>
    </div>
  );
};

// ── Main export ───────────────────────────────────────────────────────────────
/**
 * Drop this inside any page's outermost wrapper. It self-polls and renders a
 * fixed overlay — no need to pass the full status payload.
 *
 * @param ticker  The currently selected instrument (e.g. 'MNQ', 'MGC').
 */
export const AlertToastOverlay: React.FC<{ ticker: string }> = ({ ticker }) => {
  const [toast, setToast]       = useState<ToastData | null>(null);
  const prevActionableRef       = useRef<boolean | null>(null); // null = first load
  const prevScannerKeyRef       = useRef<string>('');

  // Inject CSS keyframe once on mount
  useEffect(() => { ensureKeyframe(); }, []);

  // Reset transition refs when ticker changes so first READY on new instrument fires
  useEffect(() => {
    prevActionableRef.current = null;
    prevScannerKeyRef.current = '';
  }, [ticker]);

  const poll = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      try {
        const pwd = localStorage.getItem('brain_auth');
        if (pwd) headers['Authorization'] = 'Basic ' + btoa('admin:' + pwd);
      } catch { /* localStorage unavailable */ }

      const res = await fetch(`/api/main-brain?ticker=${ticker}`, {
        credentials: 'include', headers,
      });
      if (!res.ok) return;

      const raw = await res.json();
      const p   = normalizeMainBrainPayload(raw);

      const verdict = (p.verdict            ?? {}) as Record<string, unknown>;
      const sc      = (p.strategy_scanner   ?? {}) as Record<string, unknown>;
      const market  = (p.market             ?? {}) as Record<string, unknown>;
      const cp      = (p.candidate_preview  ?? {}) as Record<string, unknown>;
      const eb      = (p.edge_breakdown     ?? {}) as Record<string, unknown>;

      const isActionable = verdict.is_actionable === true;

      let nextToast: ToastData | null = null;

      // ── SCAN_FOUND (lower priority) ─────────────────────────────────────────
      // Only fire after we've seen at least one key so a page-load doesn't spam.
      const scannerKey = String(sc.selected_strategy ?? '');
      if (scannerKey && prevScannerKeyRef.current !== '' && scannerKey !== prevScannerKeyRef.current) {
        audioManager.play(SoundEvent.SCAN_FOUND);
        const inst = ss(market.instrument, '');
        nextToast = {
          key:      Date.now(),
          icon:     '🔍',
          label:    'New setup found',
          sublabel: [inst, scannerKey].filter(Boolean).join('  ·  '),
          color:    C.cyan,
        };
      }
      prevScannerKeyRef.current = scannerKey;

      // ── READY_TO_TRADE (higher priority, wins if both fire same cycle) ──────
      // prevActionableRef starts null so the very first READY payload also fires.
      if (isActionable && prevActionableRef.current !== true) {
        audioManager.play(SoundEvent.READY_TO_TRADE);
        const inst  = ss(market.instrument, '');
        const dir   = ss(cp.direction ?? verdict.direction, '');
        const score = sn(eb.score ?? eb.total_score);
        const strat = ss(sc.selected_strategy, '');
        const parts = (
          [dir, score != null ? `Edge ${score}` : null, strat || null] as (string | null)[]
        ).filter(Boolean);
        nextToast = {
          key:      Date.now(),
          icon:     '🔔',
          label:    `${inst ? inst + '  ' : ''}READY TO TRADE`,
          sublabel: parts.join('  ·  '),
          color:    C.green,
        };
      }
      prevActionableRef.current = isActionable;

      if (nextToast) setToast(nextToast);
    } catch {
      // Network errors are silently ignored — the overlay is advisory only
    }
  }, [ticker]);

  // Poll every 7 s, paused when the browser tab is hidden
  useEffect(() => {
    poll();
    const id = setInterval(() => { if (!document.hidden) poll(); }, 7_000);
    return () => clearInterval(id);
  }, [poll]);

  if (!toast) return null;
  return <Toast data={toast} onDismiss={() => setToast(null)} />;
};
