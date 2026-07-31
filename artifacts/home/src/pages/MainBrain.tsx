/**
 * V1 Phase 7C — Main Brain Operator Console
 *
 * Read-only dashboard sourced exclusively from GET /api/main-brain.
 * No backend mutations, no gateway calls, no broker requests.
 * Auth: same Basic Auth pattern as Home.tsx (localStorage brain_auth).
 * Polling: 7 s (reduced when hidden). Manual refresh control included.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useLocation, useParams } from 'wouter';
import { normalizeMainBrainPayload } from '../lib/mainBrainNormalizer';
import { NAV_ITEMS, KNOWN_SECTIONS, SECTION_LABELS } from '../lib/navItems';
import { audioManager, SoundEvent } from '../lib/audioManager';
import {
  rankCandidates, getPlanFromRecord, getRankingReasons,
  isActionableVerdict,
  SCAN_INSTRUMENTS, SCAN_MODES,
  type StatusRecord, type CleanestCandidate, type RankInput,
} from '../lib/cleanestTrade';

// ── Design tokens ────────────────────────────────────────────────────────────
const T = {
  bg:        '#050c1a',
  panel:     '#0b1628',
  panelAlt:  '#0e1d36',
  border:    'rgba(255,255,255,0.07)',
  borderMid: 'rgba(255,255,255,0.12)',
  cyan:      '#38bdf8',
  blue:      '#3b82f6',
  green:     '#22c55e',
  amber:     '#f59e0b',
  red:       '#ef4444',
  purple:    '#a855f7',
  txtPri:    '#e2e8f0',
  txtSec:    'rgba(226,232,240,0.60)',
  txtMuted:  'rgba(226,232,240,0.32)',
  mono:      "'JetBrains Mono','Menlo',monospace",
} as const;

// ── Helpers ──────────────────────────────────────────────────────────────────
function safeStr(v: unknown, fallback = '—'): string {
  if (v == null || v === '' || v === 'null') return fallback;
  return String(v);
}
function safeNum(v: unknown): number | null {
  const n = Number(v);
  return (v != null && !isNaN(n) && isFinite(n)) ? n : null;
}
function fmtNum(v: unknown, dec = 2): string {
  const n = safeNum(v);
  return n != null ? n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) : '—';
}
function fmtTs(v: unknown): string {
  if (!v) return '—';
  try {
    const d = new Date(String(v));
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'America/New_York' });
  } catch { return '—'; }
}
function fmtAge(v: unknown): string {
  if (!v) return '';
  try {
    const s = Math.floor((Date.now() - new Date(String(v)).getTime()) / 1000);
    if (isNaN(s) || s < 0) return '';
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return `${Math.floor(s / 3600)}h ago`;
  } catch { return ''; }
}
function dirColor(d: unknown): string {
  const s = String(d ?? '').toLowerCase();
  if (/long|bull/.test(s)) return T.green;
  if (/short|bear/.test(s)) return T.red;
  return T.txtSec;
}
function readinessColor(r: unknown): string {
  const s = String(r ?? '').toUpperCase();
  if (s === 'READY') return T.green;
  if (s === 'BUILDING' || s === 'FORMING') return T.amber;
  if (s === 'MANAGING') return T.cyan;
  return T.txtSec;
}
function statusDot(ok: boolean | null): React.ReactNode {
  const col = ok == null ? T.amber : ok ? T.green : T.red;
  return <span aria-hidden style={{ display:'inline-block', width:7, height:7, borderRadius:'50%', background:col, boxShadow:`0 0 6px ${col}55`, flexShrink:0 }} />;
}
function escTxt(s: string): string {
  // textContent-safe — no innerHTML usage on live data
  return s;
}

// ── Clock hook ────────────────────────────────────────────────────────────────
function useClock() {
  const [t, setT] = useState('');
  useEffect(() => {
    const tick = () => setT(new Date().toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'America/New_York',
    }) + ' ET');
    tick(); const id = setInterval(tick, 1000); return () => clearInterval(id);
  }, []);
  return t;
}

// ── Auth header (same pattern as Home.tsx) ────────────────────────────────────
function getAuthHeader(): Record<string, string> {
  try {
    const p = localStorage.getItem('brain_auth') || '';
    return p ? { 'Authorization': 'Basic ' + btoa('admin:' + p) } : {};
  } catch { return {}; }
}

// ── Payload normalizer ────────────────────────────────────────────────────────
// Imported from src/lib/mainBrainNormalizer.ts (pure TypeScript, testable).
// See that module for full documentation and Phase 7C.3 contract tests.

// ── Data fetching hook ────────────────────────────────────────────────────────
type FetchState = 'idle' | 'loading' | 'loaded' | 'refreshing' | 'stale' | 'auth_fail' | 'error';

interface MainBrainState {
  payload: Record<string, unknown> | null;
  fetchState: FetchState;
  lastOk: number | null;
  error: string | null;
  isAuthFail: boolean;
}

const POLL_INTERVAL_MS = 7000;
const STALE_THRESHOLD_MS = 30000;

function useMainBrain(ticker: string): MainBrainState & { refresh: () => void } {
  const [state, setState] = useState<MainBrainState>({
    payload: null, fetchState: 'idle', lastOk: null, error: null, isAuthFail: false,
  });
  const inFlight = useRef(false);
  const lastPayload = useRef<Record<string, unknown> | null>(null);
  // After a 401/403, auto-polling stops until the operator manually refreshes.
  // Without this guard the poll would reset fetchState to 'loading' every 7 s,
  // causing the page to oscillate between "Connecting" and "AUTH REQUIRED"
  // instead of settling on the auth-failure screen.
  const authFailedRef = useRef(false);

  const fetchNow = useCallback(async (reason: 'poll' | 'manual') => {
    if (inFlight.current) return;
    // Auto-polls do not retry after an auth failure — only an explicit manual
    // refresh (e.g. clicking the Refresh button) is allowed to retry.
    if (authFailedRef.current && reason !== 'manual') return;
    inFlight.current = true;
    setState(s => ({
      ...s,
      fetchState: s.fetchState === 'loaded' || s.fetchState === 'stale'
        ? (reason === 'manual' ? 'refreshing' : s.fetchState)
        : 'loading',
    }));
    try {
      const url = `/api/main-brain${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ''}`;
      const r = await fetch(url, { credentials: 'include', headers: getAuthHeader() });
      if (r.status === 401 || r.status === 403) {
        authFailedRef.current = true;
        setState(s => ({ ...s, fetchState: 'auth_fail', isAuthFail: true, error: 'Authentication required' }));
        inFlight.current = false; return;
      }
      if (!r.ok) {
        setState(s => ({
          ...s,
          fetchState: lastPayload.current ? 'stale' : 'error',
          error: `Server error ${r.status}`,
        }));
        inFlight.current = false; return;
      }
      let data: Record<string, unknown>;
      try { data = await r.json(); } catch {
        setState(s => ({ ...s, fetchState: lastPayload.current ? 'stale' : 'error', error: 'Invalid JSON response' }));
        inFlight.current = false; return;
      }
      const normalized = normalizeMainBrainPayload(data);
      lastPayload.current = normalized;
      authFailedRef.current = false; // clear on successful auth
      setState({ payload: normalized, fetchState: 'loaded', lastOk: Date.now(), error: null, isAuthFail: false });
    } catch (e) {
      setState(s => ({
        ...s,
        fetchState: lastPayload.current ? 'stale' : 'error',
        error: 'Network error',
      }));
    } finally { inFlight.current = false; }
  }, [ticker]);

  // Initial load
  useEffect(() => { fetchNow('manual'); }, [fetchNow]);

  // Polling — paused when page is hidden
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return;
      const now = Date.now();
      setState(s => {
        if (s.lastOk && now - s.lastOk > STALE_THRESHOLD_MS && s.fetchState === 'loaded') {
          return { ...s, fetchState: 'stale' };
        }
        return s;
      });
      fetchNow('poll');
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchNow]);

  return { ...state, payload: state.payload ?? lastPayload.current, refresh: () => fetchNow('manual') };
}

// ── Reusable components ───────────────────────────────────────────────────────
const Panel: React.FC<{ title: string; badge?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; style?: React.CSSProperties; id?: string }> = ({ title, badge, right, children, style, id }) => (
  <section id={id} style={{ background: T.panel, border: `1px solid ${T.border}`, borderRadius: 10, overflow:'hidden', ...style }} aria-label={title}>
    <div style={{ display:'flex', alignItems:'center', gap:8, padding:'10px 14px', borderBottom:`1px solid ${T.border}`, background:'rgba(255,255,255,0.015)' }}>
      <span style={{ fontSize:10, fontWeight:700, letterSpacing:'0.1em', textTransform:'uppercase', color:T.txtSec, flex:1 }}>{title}</span>
      {badge}
      {right}
    </div>
    <div style={{ padding:'12px 14px' }}>{children}</div>
  </section>
);

const Badge: React.FC<{ label: string; color?: string }> = ({ label, color = T.txtMuted }) => (
  <span style={{ fontSize:9.5, fontWeight:700, letterSpacing:'0.08em', color, border:`1px solid ${color}44`, borderRadius:4, padding:'2px 6px', textTransform:'uppercase' }}>{label}</span>
);

const KV: React.FC<{ label: string; value: React.ReactNode; mono?: boolean; valueColor?: string }> = ({ label, value, mono, valueColor }) => (
  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'4px 0', borderBottom:`1px solid ${T.border}` }}>
    <span style={{ fontSize:10.5, color:T.txtMuted }}>{label}</span>
    <span style={{ fontSize:11, fontWeight:600, color: valueColor ?? T.txtPri, fontFamily: mono ? T.mono : undefined }}>{value ?? '—'}</span>
  </div>
);

const Pill: React.FC<{ text: string; color: string }> = ({ text, color }) => (
  <span style={{ display:'inline-flex', alignItems:'center', gap:5, fontSize:10, fontWeight:700, letterSpacing:'0.07em',
    color, background:`${color}18`, border:`1px solid ${color}44`, borderRadius:5, padding:'2px 8px', textTransform:'uppercase' }}>
    {text}
  </span>
);

const UnavailableNote: React.FC<{ msg?: string }> = ({ msg }) => (
  <div style={{ textAlign:'center', padding:'20px 0', color:T.txtMuted, fontSize:11 }}>
    {msg ?? 'Data unavailable'}
  </div>
);

// ── Edge score gauge (0–110 scale) ────────────────────────────────────────────
const EdgeGauge: React.FC<{ score: number | null; max?: number }> = ({ score, max = 110 }) => {
  const pct = score != null ? Math.min(score / max, 1) : 0;
  const col = score == null ? T.txtMuted : score >= 85 ? T.green : score >= 70 ? '#4ade80' : score >= 50 ? T.amber : T.red;
  return (
    <div style={{ textAlign:'center' }}>
      <div style={{ position:'relative', width:90, height:90, margin:'0 auto' }}>
        <svg width={90} height={90} aria-label={`Edge score ${score ?? 'unavailable'} out of ${max}`}>
          <circle cx={45} cy={45} r={36} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={8} />
          <circle cx={45} cy={45} r={36} fill="none" stroke={col} strokeWidth={8}
            strokeDasharray={`${pct * 2 * Math.PI * 36} ${2 * Math.PI * 36}`}
            strokeLinecap="round" transform="rotate(-90 45 45)"
            style={{ transition:'stroke-dasharray 0.6s ease' }} />
        </svg>
        <div style={{ position:'absolute', inset:0, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center' }}>
          <span style={{ fontSize:22, fontWeight:800, color:col, fontFamily:T.mono, lineHeight:1 }}>
            {score != null ? Math.round(score) : '—'}
          </span>
          <span style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.1em' }}>/ {max}</span>
        </div>
      </div>
      <div style={{ fontSize:9, color:T.txtMuted, marginTop:4 }}>EDGE SCORE</div>
    </div>
  );
};

// NAV_ITEMS and related constants are imported from ../lib/navItems

// ── Sidenav ───────────────────────────────────────────────────────────────────
const SideNav: React.FC<{ systemOk: boolean }> = ({ systemOk }) => {
  const [location] = useLocation();
  return (
    <nav aria-label="Main navigation" style={{
      width: 58, flexShrink: 0, background: '#040d1e', borderRight: `1px solid ${T.border}`,
      display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '12px 0',
      position: 'sticky', top: 0, height: '100vh', overflowY: 'auto',
    }}>
      {/* Logo */}
      <div style={{ marginBottom: 20, textAlign:'center' }}>
        <div style={{ width:36, height:36, borderRadius:10, background:'linear-gradient(135deg,#1e3a5f,#0f2040)',
          border:`1px solid ${T.cyan}44`, display:'flex', alignItems:'center', justifyContent:'center',
          fontSize:16, boxShadow:`0 0 12px ${T.cyan}22` }}>
          🧠
        </div>
        <div style={{ fontSize:7, color:T.cyan, fontWeight:700, letterSpacing:'0.1em', marginTop:4, lineHeight:1 }}>V1</div>
      </div>

      {NAV_ITEMS.map(item => {
        const isActive = location === item.path;
        const col = isActive ? T.cyan : T.txtSec;
        return (
          <Link key={item.id} to={item.path} style={{ textDecoration:'none', marginBottom:4 }}
            aria-label={item.label} aria-current={isActive ? 'page' : undefined}>
            <div title={item.label} style={{
              display:'flex', flexDirection:'column', alignItems:'center', gap:3,
              padding:'8px 6px', borderRadius:8, width:46, cursor:'pointer',
              background: isActive ? `${T.cyan}14` : 'transparent',
              border: isActive ? `1px solid ${T.cyan}33` : '1px solid transparent',
              opacity: 1,
              transition:'all 0.15s',
            }}>
              <span style={{ fontSize:15, lineHeight:1, color:col }}>{item.icon}</span>
              <span style={{ fontSize:7.5, fontWeight:700, letterSpacing:'0.06em', color:col, textAlign:'center', lineHeight:1.2 }}>
                {item.label.toUpperCase()}
              </span>
            </div>
          </Link>
        );
      })}

      {/* Secondary: legacy dashboard link */}
      <div style={{ marginTop:'auto', display:'flex', flexDirection:'column', alignItems:'center', gap:4, width:'100%' }}>
        <div style={{ width:32, height:1, background:T.border, marginBottom:4 }} />
        <a href="/dashboard" title="System Dashboard (legacy)" style={{ textDecoration:'none' }}>
          <div style={{
            display:'flex', flexDirection:'column', alignItems:'center', gap:3,
            padding:'6px 4px', borderRadius:6, width:46, cursor:'pointer',
            border:'1px solid transparent', opacity:0.55,
            transition:'opacity 0.15s',
          }}
            onMouseEnter={e => (e.currentTarget as HTMLDivElement).style.opacity='1'}
            onMouseLeave={e => (e.currentTarget as HTMLDivElement).style.opacity='0.55'}
          >
            <span style={{ fontSize:11, lineHeight:1, color:T.txtMuted }}>⚙</span>
            <span style={{ fontSize:6.5, fontWeight:700, letterSpacing:'0.06em', color:T.txtMuted, textAlign:'center', lineHeight:1.2 }}>
              SYSTEM{'\n'}DASHBOARD
            </span>
          </div>
        </a>

        {/* System health dot */}
        <div style={{ paddingTop:8, display:'flex', flexDirection:'column', alignItems:'center', gap:4 }}>
          {statusDot(systemOk)}
          <span style={{ fontSize:7, color:T.txtMuted, letterSpacing:'0.06em' }}>SYS</span>
        </div>
      </div>
    </nav>
  );
};

// ── Market State Strip ────────────────────────────────────────────────────────
const MarketStrip: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const mkt   = (p.market   ?? {}) as Record<string, unknown>;
  const ms    = (p.market_state ?? {}) as Record<string, unknown>;
  const sys   = (p.system_status ?? {}) as Record<string, unknown>;

  const cards = [
    { label:'MARKET',      value: safeStr(mkt.session_status, 'UNKNOWN'),  color: /open/i.test(String(mkt.session_status)) ? T.green : T.amber },
    { label:'INSTRUMENT',  value: safeStr(mkt.instrument, '—'),             color: T.cyan },
    { label:'MODE',        value: safeStr(mkt.trading_mode, '—'),           color: T.blue },
    { label:'EXECUTION',   value: safeStr(mkt.execution_mode, '—'),         color: T.amber },
    { label:'REGIME',      value: safeStr(ms.regime, '—'),                  color: T.txtSec },
    { label:'RISK STATE',  value: safeStr(ms.risk_state, '—'),              color: /shock|off/i.test(String(ms.risk_state)) ? T.red : T.txtSec },
    { label:'DATABENTO',   value: sys.databento_ready ? 'LIVE' : 'OFFLINE', color: sys.databento_ready ? T.green : T.red },
  ];

  return (
    <div style={{ display:'flex', gap:8, overflowX:'auto', paddingBottom:2 }} role="region" aria-label="Market state strip">
      {cards.map(c => (
        <div key={c.label} style={{
          flexShrink:0, background:T.panel, border:`1px solid ${T.border}`, borderRadius:8,
          padding:'8px 12px', minWidth:90, textAlign:'center',
        }}>
          <div style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.1em', marginBottom:4 }}>{c.label}</div>
          <div style={{ fontSize:12.5, fontWeight:700, color:c.color, fontFamily:T.mono, whiteSpace:'nowrap' }}>{c.value}</div>
        </div>
      ))}
    </div>
  );
};

// ── Thesis Panel ──────────────────────────────────────────────────────────────
const ThesisPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const lb = (p.left_brain ?? {}) as Record<string, unknown>;
  const avail = lb.available !== false;
  const dir   = safeStr(lb.direction, '');
  const conf  = safeNum(lb.confidence);
  const narr  = safeStr(lb.narrative, '');
  const age   = fmtAge(lb.generated_at);
  const dCol  = dirColor(dir);

  return (
    <Panel title="Left Brain Thesis" badge={avail ? <Badge label={dir || 'UNKNOWN'} color={dCol} /> : <Badge label="UNAVAILABLE" color={T.txtMuted} />}>
      {!avail ? <UnavailableNote msg="Thesis unavailable" /> : (
        <>
          {/* Direction + confidence bar */}
          <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:12 }}>
            <div>
              <div style={{ fontSize:22, fontWeight:800, color:dCol, lineHeight:1 }}>{dir || '—'}</div>
              <div style={{ fontSize:9.5, color:T.txtMuted, marginTop:2 }}>{age}</div>
            </div>
            {conf != null && (
              <div style={{ flex:1 }}>
                <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
                  <span style={{ fontSize:9, color:T.txtMuted }}>CONFIDENCE</span>
                  <span style={{ fontSize:11, fontWeight:700, color:dCol, fontFamily:T.mono }}>{conf} / 100</span>
                </div>
                <div style={{ height:5, background:'rgba(255,255,255,0.07)', borderRadius:3, overflow:'hidden' }}>
                  <div style={{ height:'100%', width:`${Math.min(conf, 100)}%`, background:dCol, borderRadius:3, transition:'width 0.5s ease' }} role="progressbar" aria-valuenow={conf} aria-valuemin={0} aria-valuemax={100} aria-label="Thesis confidence" />
                </div>
              </div>
            )}
          </div>

          {/* Narrative */}
          {narr && <div style={{ fontSize:11, color:T.txtSec, lineHeight:1.55, marginBottom:10, borderLeft:`2px solid ${dCol}55`, paddingLeft:8 }}>{narr}</div>}

          {/* Meta */}
          <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginTop:4 }}>
            {lb.status != null && <Badge label={String(lb.status)} color={T.txtSec} />}
            {lb.momentum != null && <Badge label={String(lb.momentum)} color={T.amber} />}
          </div>
        </>
      )}
    </Panel>
  );
};

// ── Learning status helpers ────────────────────────────────────────────────────
const WEIGHT_STATUS_LABEL: Record<string, string> = {
  UPDATED:              'UPDATED',
  NO_CHANGE:            'NO CHANGE',
  INSUFFICIENT_SAMPLES: 'COLLECTING DATA',
  NOT_ELIGIBLE:         'NOT ELIGIBLE',
  KEY_NOT_FOUND:        'KEY MISMATCH',
  DISABLED:             'DISABLED',
  DB_DISABLED:          'DISABLED',
  DIAGNOSTIC_BUILD_FAILED: 'UNAVAILABLE',
  COACH_BUILD_FAILED:   'UNAVAILABLE',
};
const WEIGHT_STATUS_COLOR: Record<string, string> = {
  UPDATED:              '#22c55e',
  NO_CHANGE:            '#94a3b8',
  INSUFFICIENT_SAMPLES: '#f59e0b',
  NOT_ELIGIBLE:         '#64748b',
  KEY_NOT_FOUND:        '#ef4444',
  DISABLED:             '#64748b',
  DB_DISABLED:          '#64748b',
};

// ── Verdict Panel ─────────────────────────────────────────────────────────────
const VerdictPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const v          = (p.verdict ?? {}) as Record<string, unknown>;
  const avail      = v.available !== false;
  const score      = safeNum(v.edge_score) ?? 0;
  const scoreMax   = safeNum(v.edge_max) ?? 110;
  const grade      = safeStr(v.edge_grade, '');
  const ready      = safeStr(v.readiness, '');
  const rCol       = readinessColor(ready);
  const isReady    = v.is_actionable === true;
  const direction  = safeStr(v.direction, '');

  // Rich component list {key, label, points, present} — Phase 7C.2
  const edgeComps  = Array.isArray(v.edge_components)
    ? v.edge_components as Record<string, unknown>[]
    : [];

  // Fallback to old dict format if rich list not available
  const fallbackComps = (edgeComps.length === 0)
    ? (v.components as Record<string, number> | null)
    : null;

  const missingComps = edgeComps.filter(c => c.present === false);

  // Verdict explanation from Brain voice (already normalized)
  const mb          = (p.main_brain ?? {}) as Record<string, unknown>;
  const explanation = safeStr(mb.voice, '');

  return (
    <Panel title="Verdict" badge={<Badge label={ready || 'UNKNOWN'} color={rCol} />}>
      {!avail ? <UnavailableNote msg="Verdict unavailable" /> : (
        <div>
          {/* Score header */}
          <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:12 }}>
            <EdgeGauge score={score} />
            <div style={{ flex:1, minWidth:0 }}>
              <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginBottom:6 }}>
                {direction && <Pill text={direction.toUpperCase()} color={dirColor(direction)} />}
                {grade && <Pill text={`Grade ${grade}`} color={score >= 70 ? T.green : score >= 50 ? T.amber : T.red} />}
                <Pill text={isReady ? 'ACTIONABLE' : 'NOT ACTIONABLE'} color={isReady ? T.green : T.red} />
              </div>
              <div style={{ fontFamily:T.mono, fontSize:12, color:T.txtSec }}>
                <span style={{ color:T.cyan, fontWeight:700 }}>{score}</span>
                <span style={{ color:T.txtMuted }}> / {scoreMax}</span>
              </div>
            </div>
          </div>

          {/* ── Edge Score Breakdown (rich) ──────────────────────────────── */}
          {edgeComps.length > 0 && (
            <div style={{ marginBottom:12 }}>
              <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.08em', marginBottom:7, display:'flex', justifyContent:'space-between' }}>
                <span>EDGE SCORE BREAKDOWN</span>
                <span style={{ fontFamily:T.mono }}>pts / {scoreMax}</span>
              </div>
              {edgeComps.map((c, i) => {
                const lbl    = safeStr(c.label, safeStr(c.key, '').replace(/_/g, ' '));
                const pts    = safeNum(c.points) ?? 0;
                const here   = c.present === true;
                const barPct = here ? Math.round(pts / scoreMax * 100) : 0;
                return (
                  <div key={i} style={{ display:'flex', alignItems:'center', gap:6, marginBottom:5 }}>
                    <span style={{ fontSize:12, width:14, textAlign:'center', flexShrink:0, color: here ? T.green : 'rgba(255,255,255,0.18)', lineHeight:1 }}>
                      {here ? '✓' : '✗'}
                    </span>
                    <span style={{ fontSize:10, color: here ? T.txtSec : 'rgba(255,255,255,0.3)', flex:1, minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                      {lbl}
                    </span>
                    <div style={{ width:60, height:4, background:'rgba(255,255,255,0.06)', borderRadius:2, flexShrink:0 }}>
                      <div style={{ height:'100%', width:`${barPct}%`, background: here ? T.green : 'transparent', borderRadius:2, transition:'width 0.3s ease' }} />
                    </div>
                    <span style={{ fontSize:9.5, fontFamily:T.mono, color: here ? T.green : 'rgba(255,255,255,0.2)', width:24, textAlign:'right', flexShrink:0 }}>
                      {here ? `+${pts}` : '—'}
                    </span>
                  </div>
                );
              })}
              <div style={{ display:'flex', justifyContent:'flex-end', borderTop:`1px solid ${T.border}`, paddingTop:5, marginTop:2 }}>
                <span style={{ fontSize:9, color:T.txtMuted, fontFamily:T.mono }}>
                  TOTAL&nbsp;&nbsp;<span style={{ color:T.cyan, fontWeight:700 }}>{score}</span>
                </span>
              </div>
            </div>
          )}

          {/* ── Fallback: old dict format ────────────────────────────────── */}
          {fallbackComps && Object.keys(fallbackComps).length > 0 && (
            <div style={{ marginBottom:10 }}>
              <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.08em', marginBottom:5 }}>COMPONENTS</div>
              {Object.entries(fallbackComps).slice(0, 7).map(([k, sc]) => (
                <div key={k} style={{ display:'flex', alignItems:'center', gap:6, marginBottom:3 }}>
                  <span style={{ fontSize:9.5, color:T.txtMuted, minWidth:100 }}>{k.replace(/_/g,' ')}</span>
                  <div style={{ flex:1, height:4, background:'rgba(255,255,255,0.06)', borderRadius:2 }}>
                    <div style={{ height:'100%', width:`${Math.min(Number(sc ?? 0)/20*100, 100)}%`, background:T.cyan, borderRadius:2 }} />
                  </div>
                  <span style={{ fontSize:9.5, fontWeight:700, color:T.cyan, fontFamily:T.mono, minWidth:18, textAlign:'right' }}>{sc}</span>
                </div>
              ))}
            </div>
          )}

          {/* ── Waiting For checklist ────────────────────────────────────── */}
          {missingComps.length > 0 && (
            <div style={{ marginBottom:10, padding:'8px 10px', background:'rgba(239,68,68,0.05)', borderRadius:7, border:`1px solid rgba(239,68,68,0.14)` }}>
              <div style={{ fontSize:9, color:T.red, letterSpacing:'0.08em', marginBottom:6 }}>WAITING FOR</div>
              {missingComps.map((c, i) => {
                const lbl = safeStr(c.label, safeStr(c.key, '').replace(/_/g, ' '));
                const pts = safeNum(c.points) ?? 0;
                return (
                  <div key={i} style={{ display:'flex', alignItems:'center', gap:7, marginBottom:3 }}>
                    <span style={{ fontSize:9, color:T.red, opacity:0.6 }}>•</span>
                    <span style={{ fontSize:10, color:`${T.txtPri}99`, flex:1 }}>{lbl}</span>
                    {pts > 0 && <span style={{ fontSize:9, color:T.txtMuted, fontFamily:T.mono, flexShrink:0 }}>+{pts} pts</span>}
                  </div>
                );
              })}
            </div>
          )}

          {/* ── Opposing-Structure Block Diagnostic ──────────────────────── */}
          {(() => {
            const os = v.opposing_structure as Record<string, unknown> | null | undefined;
            if (os === undefined) return null; // server hasn't sent the field yet
            const detected  = os != null && os.detected === true;
            const effect    = safeStr((os ?? {}).effect, 'NONE');
            const isBlocking = detected && effect !== 'NONE' && effect !== 'OBSERVED';
            const ageS      = safeNum((os ?? {}).age_seconds);
            const remS      = safeNum((os ?? {}).remaining_seconds);
            const fmtTime   = (s: number | null): string => {
              if (s == null) return '—';
              return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
            };
            const evType    = safeStr((os ?? {}).event_type, '');
            const oppDir    = safeStr((os ?? {}).direction, '');
            const candDir   = safeStr((os ?? {}).candidate_direction, '');
            const instrName = safeStr((os ?? {}).instrument, '');
            const sameTs    = safeStr((os ?? {}).same_direction_ts, '');

            if (!detected || !isBlocking) {
              return (
                <div style={{ marginBottom:10, padding:'7px 10px', background:'rgba(34,197,94,0.05)', borderRadius:7, border:`1px solid rgba(34,197,94,0.15)` }}>
                  <div style={{ fontSize:9, color:T.green, letterSpacing:'0.08em', fontWeight:700 }}>NO HARD ALERT BLOCK</div>
                  {detected && effect === 'OBSERVED' && (
                    <div style={{ fontSize:9.5, color:T.txtMuted, marginTop:4 }}>
                      Opposing {oppDir.toLowerCase()} {evType} detected — dominant side clear (not blocking)
                      {ageS != null && `, age ${fmtTime(ageS)}`}
                    </div>
                  )}
                </div>
              );
            }

            const effectLabel = effect === 'HARD_BLOCK' ? 'Hard block' : 'Score-aware block';
            return (
              <div style={{ marginBottom:10, padding:'9px 10px', background:'rgba(239,68,68,0.07)', borderRadius:7, border:`1px solid rgba(239,68,68,0.28)`, borderLeft:`3px solid ${T.red}` }}>
                <div style={{ fontSize:9, color:T.red, letterSpacing:'0.08em', fontWeight:700, marginBottom:6 }}>BLOCKING ALERT</div>
                <div style={{ fontSize:11, color:T.txtPri, fontWeight:600, marginBottom:4 }}>
                  Recent {oppDir.toLowerCase()} {evType}
                </div>
                <div style={{ display:'grid', gridTemplateColumns:'auto 1fr', gap:'3px 10px', fontSize:9.5 }}>
                  {ageS  != null && <><span style={{ color:T.txtMuted }}>Age</span>          <span style={{ color:T.txtSec, fontFamily:T.mono }}>{fmtTime(ageS)}</span></>}
                  {remS  != null && <><span style={{ color:T.txtMuted }}>Window remaining</span> <span style={{ color:T.amber, fontFamily:T.mono }}>{fmtTime(remS)}</span></>}
                  {candDir        && <><span style={{ color:T.txtMuted }}>Candidate</span>    <span style={{ color:T.txtSec }}>{candDir}</span></>}
                  <><span style={{ color:T.txtMuted }}>Effect</span>           <span style={{ color:T.red }}>{effectLabel}</span></>
                  {instrName      && <><span style={{ color:T.txtMuted }}>Source</span>       <span style={{ color:T.txtSec }}>{instrName} structure cache</span></>}
                  {sameTs         && <><span style={{ color:T.txtMuted }}>Same-dir struct</span> <span style={{ color:T.green, fontFamily:T.mono, fontSize:9 }}>{sameTs.substring(11,19)} UTC</span></>}
                </div>
              </div>
            );
          })()}

          {/* ── Brain Reasoning ──────────────────────────────────────────── */}
          {explanation && (
            <div style={{ padding:'8px 10px', background:'rgba(56,189,248,0.04)', borderRadius:7, border:`1px solid rgba(56,189,248,0.12)`, borderLeft:`3px solid ${T.cyan}33` }}>
              <div style={{ fontSize:9, color:T.cyan, letterSpacing:'0.08em', marginBottom:5 }}>BRAIN REASONING</div>
              <div style={{ fontSize:10.5, color:T.txtSec, lineHeight:1.55, fontStyle:'italic' }}>
                "{explanation}"
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
};

// ── Strategy Scanner Panel ────────────────────────────────────────────────────
const STRATEGY_LABELS: Record<string, string> = {
  OPENING_DRIVE:            'Opening Drive',
  LIQUIDITY_SWEEP_REVERSAL: 'Liquidity Sweep',
  VWAP_TREND_CONTINUATION:  'VWAP Continuation',
  RANGE_EXPANSION_BREAKOUT: 'Range Expansion',
  OPENING_RANGE_BREAKOUT:   'ORB',
};

function completenessHint(pct: number, skipReason: string): string {
  if (skipReason) return skipReason.replace(/_/g, ' ');
  if (pct >= 80)  return 'Very close — watching for final trigger';
  if (pct >= 60)  return 'Setup building — needs a few more confirms';
  if (pct >= 40)  return 'Forming — incomplete setup';
  if (pct >= 20)  return 'Early stage — monitoring';
  return 'No signal yet';
}

const ScannerPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const sc       = (p.strategy_scanner ?? {}) as Record<string, unknown>;
  const verdict  = (p.verdict ?? {}) as Record<string, unknown>;
  const avail    = sc.available !== false;
  const sel      = safeStr(sc.selected_strategy, '');
  const strats   = Array.isArray(sc.strategies) ? sc.strategies as Record<string, unknown>[] : [];
  const isReady  = verdict.is_actionable === true;

  // Best candidate: highest completeness among eligible strategies
  const eligibles = strats.filter(s => s.eligible !== false);
  const best = eligibles.reduce<Record<string, unknown> | null>((acc, s) => {
    const aComp = safeNum(acc?.completeness) ?? 0;
    const sComp = safeNum(s.completeness) ?? 0;
    return sComp > aComp ? s : acc;
  }, null);
  const bestComp = safeNum(best?.completeness) ?? 0;
  const bestKey  = safeStr(best?.strategy_key ?? best?.key, '');
  const bestName = STRATEGY_LABELS[bestKey] ?? safeStr(best?.name, bestKey);

  return (
    <Panel title="Strategy Scanner" badge={sel ? <Badge label={STRATEGY_LABELS[sel] ?? sel} color={T.cyan} /> : undefined}>
      {!avail ? <UnavailableNote /> : strats.length === 0 ? <UnavailableNote msg="No strategies available" /> : (
        <div>

          {/* ── Best Opportunity card ─────────────────────────────────── */}
          {!isReady && best != null && bestComp > 0 && (
            <div style={{ marginBottom:12, padding:'10px 11px', background:`${T.cyan}08`, borderRadius:8, border:`1px solid ${T.cyan}20` }}>
              <div style={{ fontSize:9, color:T.cyan, letterSpacing:'0.08em', marginBottom:6 }}>BEST OPPORTUNITY</div>
              <div style={{ fontWeight:700, fontSize:11.5, color:T.txtPri, marginBottom:6, lineHeight:1.2 }}>
                {bestName}
              </div>
              <div style={{ display:'flex', alignItems:'center', gap:7, marginBottom:5 }}>
                <div style={{ flex:1, height:5, background:'rgba(255,255,255,0.07)', borderRadius:3 }}>
                  <div style={{ height:'100%', width:`${bestComp}%`, background:T.cyan, borderRadius:3, transition:'width 0.4s ease' }} />
                </div>
                <span style={{ fontSize:10.5, fontFamily:T.mono, color:T.cyan, fontWeight:700, flexShrink:0 }}>
                  {bestComp}%
                </span>
              </div>
              <div style={{ fontSize:9.5, color:T.txtMuted, lineHeight:1.4 }}>
                {completenessHint(bestComp, safeStr(best?.skip_reason, ''))}
              </div>
            </div>
          )}

          {/* ── Strategy rows ─────────────────────────────────────────── */}
          {strats.map((s, i) => {
            const key    = safeStr(s.key, '');
            const name   = STRATEGY_LABELS[key] ?? safeStr(s.name, key);
            const rdy    = safeStr(s.readiness, '');
            const isSel  = key === sel;
            const rCol   = readinessColor(rdy);
            const comp   = safeNum(s.completeness) ?? 0;
            const isElig = s.eligible !== false;
            const skipR  = safeStr(s.skip_reason, '');
            const dir    = safeStr(s.direction, '');
            const barCol = rdy === 'READY' ? T.green
              : isSel  ? T.cyan
              : isElig ? T.amber
              : 'rgba(255,255,255,0.12)';

            return (
              <div key={key || i} style={{
                padding:'8px 10px', marginBottom:5,
                background: isSel ? `${T.cyan}0e` : 'rgba(255,255,255,0.02)',
                borderRadius:7, border:`1px solid ${isSel ? T.cyan + '30' : T.border}`,
                opacity: isElig ? 1 : 0.5,
              }}>
                {/* Name + direction + badge row */}
                <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:5 }}>
                  {isSel && <span style={{ fontSize:9, color:T.cyan, flexShrink:0 }}>▶</span>}
                  <span style={{ fontSize:11, fontWeight:isSel ? 700 : 500, color:isSel ? T.cyan : isElig ? T.txtPri : T.txtMuted, flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                    {name}
                  </span>
                  {dir && <span style={{ fontSize:9, color:dirColor(dir), flexShrink:0 }}>{dir.toUpperCase()}</span>}
                  <Badge label={rdy || '—'} color={rCol} />
                  {s.mode_compatible === false && <Badge label="MODE" color={T.amber} />}
                </div>
                {/* Progress bar row */}
                <div style={{ display:'flex', alignItems:'center', gap:7 }}>
                  <div style={{ flex:1, height:4, background:'rgba(255,255,255,0.06)', borderRadius:2 }}>
                    <div style={{ height:'100%', width:`${comp}%`, background:barCol, borderRadius:2, transition:'width 0.4s ease' }} />
                  </div>
                  <span style={{ fontSize:9, fontFamily:T.mono, color:barCol, fontWeight:700, minWidth:32, textAlign:'right', flexShrink:0 }}>
                    {isElig ? `${comp}%` : skipR ? skipR.replace(/_/g,' ') : 'SKIP'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
};

// ── Trade Plan Panel ──────────────────────────────────────────────────────────
// ── Cleanest Trade Available ──────────────────────────────────────────────────

type CleanestScanResult = {
  candidate:     CleanestCandidate | null;
  error:         string | null;
  allInputs:     RankInput[];
  scannedAt:     number;       // ms since epoch when scan completed
  scanStartedAt: number;       // ms since epoch when scan was initiated
  /** True when one or more of the 8 status requests failed. */
  isPartial: boolean;
  succeeded: number;           // how many of total responded OK
  total:     number;           // total attempted (normally 8)
  failed:    Array<{ instrument: string; mode: string }>;
};

// ── Cleanest Trade Modal ──────────────────────────────────────────────────────
const CleanestTradeModal: React.FC<{
  open:             boolean;
  onClose:          () => void;
  scanResult:       CleanestScanResult | null;
  activeInstrument: string;
  hasActiveTrade:   boolean;
}> = ({ open, onClose, scanResult, activeInstrument, hasActiveTrade }) => {
  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const cand  = scanResult?.candidate ?? null;
  const err   = scanResult?.error ?? null;
  const allInputs = scanResult?.allInputs ?? [];
  const age   = scanResult ? fmtAge(new Date(scanResult.scannedAt).toISOString()) : 'n/a';

  const plan  = cand ? getPlanFromRecord(cand.record, cand.direction) : null;
  const reasons = cand ? getRankingReasons(cand, allInputs) : [];

  const isReady = cand != null && cand.act === 1;
  const isPot   = cand != null && cand.act === 0;

  const titleColor = isReady ? T.green : isPot ? T.amber : T.txtMuted;
  const titleLabel = isReady ? '✓ READY TO TRADE'
                  : isPot   ? '⏳ POTENTIAL — NOT READY'
                  : err     ? '✗ SCAN UNAVAILABLE'
                             : '— NO QUALIFYING TRADE';

  // Active trade conflict: same instrument AND same direction as candidate
  const conflictWarning =
    hasActiveTrade && cand &&
    cand.instrument === activeInstrument;

  // Edge grade
  const edge  = cand?.edge ?? null;
  const grade = edge == null ? '—'
              : edge >= 85  ? 'A+'
              : edge >= 70  ? 'A'
              : edge >= 50  ? 'B'
              : 'WAIT';
  const gradeColor = edge == null ? T.txtMuted
                   : edge >= 85   ? T.green
                   : edge >= 70   ? '#4ade80'
                   : edge >= 50   ? T.amber
                   : T.red;

  // Strategy name
  const stratName = safeStr(
    (cand?.record.strategy_engine as Record<string, unknown> | undefined)?.active_strategy, '—'
  );

  // Blockers
  const blocker = safeStr(cand?.record.strict_reason, '');

  // Opposing structure
  const opp   = cand?.record.opposing_structure;
  const oppStr = opp && typeof opp === 'object' && (opp as Record<string, unknown>).side
    ? `${(opp as Record<string, unknown>).side} structure (${safeStr((opp as Record<string, unknown>).type, '?')})`
    : opp ? String(opp) : 'None detected';

  // Data freshness from the record
  const freshness = safeStr(cand?.record.generated_at, '');

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Cleanest Trade Available"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position:'fixed', inset:0, zIndex:999,
        background:'rgba(3,11,26,0.85)', backdropFilter:'blur(4px)',
        display:'flex', alignItems:'center', justifyContent:'center',
        padding:'20px 12px',
      }}
    >
      <div style={{
        background:'#08162a', border:`1px solid ${T.border}`, borderRadius:14,
        width:'100%', maxWidth:560, maxHeight:'90vh', overflowY:'auto',
        display:'flex', flexDirection:'column', boxShadow:`0 20px 60px rgba(0,0,0,0.7)`,
      }}>
        {/* Header */}
        <div style={{
          display:'flex', alignItems:'center', gap:10, padding:'14px 18px',
          borderBottom:`1px solid ${T.border}`,
          position:'sticky', top:0, background:'#08162a', zIndex:2,
        }}>
          <div style={{ flex:1 }}>
            <div style={{ fontSize:9, fontWeight:700, letterSpacing:'0.12em', color:T.txtMuted, textTransform:'uppercase', marginBottom:2 }}>
              Cleanest Trade Available
            </div>
            <div style={{ fontSize:13, fontWeight:800, color:titleColor, letterSpacing:'0.04em' }}>
              {titleLabel}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ background:'transparent', border:`1px solid ${T.border}`, color:T.txtSec,
              borderRadius:6, width:28, height:28, cursor:'pointer', fontSize:14, lineHeight:1,
              display:'flex', alignItems:'center', justifyContent:'center' }}
          >×</button>
        </div>

        {/* Body */}
        <div style={{ padding:'16px 18px', display:'flex', flexDirection:'column', gap:14 }}>

          {/* Error / empty */}
          {err && (
            <div style={{ background:`${T.red}12`, border:`1px solid ${T.red}33`, borderRadius:8,
              padding:'12px 14px', fontSize:11, color:T.red }}>
              {err}
            </div>
          )}
          {!err && !cand && (
            <div style={{ textAlign:'center', padding:'20px 0', color:T.txtMuted, fontSize:12 }}>
              No qualifying setup found across all scanned instruments and modes.
            </div>
          )}

          {/* Candidate summary */}
          {cand && (
            <>
              {/* ─ Active trade conflict ─ */}
              {conflictWarning && (
                <div style={{ background:`${T.amber}14`, border:`1px solid ${T.amber}44`, borderRadius:8,
                  padding:'10px 14px', fontSize:11, color:T.amber, display:'flex', gap:8, alignItems:'center' }}>
                  <span>⚠</span>
                  <span>An active {activeInstrument} trade is already open. This preview is informational only — it will not execute.</span>
                </div>
              )}

              {/* ─ Instrument / mode / direction header ─ */}
              <div style={{ background:`${T.panel}`, border:`1px solid ${T.border}`, borderRadius:10,
                padding:'12px 14px', display:'flex', alignItems:'center', gap:12, flexWrap:'wrap' }}>
                <div>
                  <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom:3 }}>Instrument · Mode</div>
                  <div style={{ fontSize:18, fontWeight:800, color:T.cyan, letterSpacing:'0.04em' }}>
                    {cand.instrument}
                    <span style={{ fontSize:11, fontWeight:600, color:T.txtSec, marginLeft:8 }}>{cand.mode}</span>
                  </div>
                </div>
                <div style={{ width:1, height:36, background:T.border }} />
                <div>
                  <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom:3 }}>Direction</div>
                  <Pill text={cand.direction} color={dirColor(cand.direction)} />
                </div>
                <div style={{ width:1, height:36, background:T.border }} />
                <div>
                  <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom:3 }}>Status</div>
                  <Pill text={isReady ? 'READY' : 'POTENTIAL'} color={isReady ? T.green : T.amber} />
                </div>
                <div style={{ marginLeft:'auto', textAlign:'center' }}>
                  <div style={{ fontSize:22, fontWeight:800, color:gradeColor, fontFamily:T.mono }}>{Math.round(cand.edge)}</div>
                  <div style={{ fontSize:8, color:T.txtMuted, letterSpacing:'0.1em' }}>/ 110</div>
                  <div style={{ fontSize:10, fontWeight:700, color:gradeColor, marginTop:2 }}>Grade {grade}</div>
                </div>
              </div>

              {/* ─ Strategy ─ */}
              {stratName !== '—' && (
                <div style={{ fontSize:11, color:T.txtSec }}>
                  <span style={{ color:T.txtMuted, fontSize:10 }}>Strategy: </span>{stratName}
                </div>
              )}

              {/* ─ Trade plan ─ */}
              {plan ? (
                <div>
                  <div style={{ fontSize:9.5, fontWeight:700, letterSpacing:'0.1em', textTransform:'uppercase',
                    color:T.txtMuted, marginBottom:8, display:'flex', alignItems:'center', gap:6 }}>
                    Trade Plan
                    {isPot && <span style={{ color:T.amber, fontWeight:600, fontSize:8.5 }}>(preview — not yet READY)</span>}
                  </div>
                  {plan.entry_zone  != null && <KV label="Entry Zone"    value={String(plan.entry_zone)}  mono valueColor={T.cyan}  />}
                  {plan.stop_loss   != null && <KV label="Stop Loss"     value={String(plan.stop_loss)}   mono valueColor={T.red}   />}
                  {plan.target1     != null && <KV label="Target 1"      value={String(plan.target1)}     mono valueColor={T.green} />}
                  {plan.target2     != null && <KV label="Target 2"      value={String(plan.target2)}     mono valueColor={T.green} />}
                  {plan.rr          != null && <KV label="R : R"         value={String(plan.rr)}          mono />}
                  {plan.risk_points != null && <KV label="Risk Points"   value={`${plan.risk_points} pts`} mono />}
                  {plan.risk_dollars_per_contract != null && (
                    <KV label="Risk / Contract"
                      value={`$${Number(plan.risk_dollars_per_contract).toFixed(2)}`}
                      mono valueColor={T.amber} />
                  )}
                  {plan.reward_points != null && plan.point_value != null && (
                    <KV label="Expected Profit / Contract"
                      value={`$${(Number(plan.reward_points) * Number(plan.point_value)).toFixed(2)}`}
                      mono valueColor={T.green} />
                  )}
                  {plan.atr_pts != null && <KV label="ATR (pts)"      value={`${plan.atr_pts}`}  mono />}
                </div>
              ) : (
                <div style={{ fontSize:11, color:T.txtMuted, fontStyle:'italic' }}>
                  No entry/stop plan available for this candidate.
                </div>
              )}

              {/* ─ Blockers ─ */}
              {blocker && (
                <div style={{ background:`${T.amber}10`, border:`1px solid ${T.amber}33`, borderRadius:8,
                  padding:'10px 14px' }}>
                  <div style={{ fontSize:9, color:T.amber, fontWeight:700, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom:4 }}>
                    Current Blockers
                  </div>
                  <div style={{ fontSize:11, color:T.txtSec }}>{blocker}</div>
                </div>
              )}

              {/* ─ Opposing structure ─ */}
              <KV label="Opposing Structure" value={oppStr} />

              {/* ─ Why this ranked first ─ */}
              <div style={{ background:`${T.cyan}08`, border:`1px solid ${T.cyan}22`, borderRadius:8, padding:'12px 14px' }}>
                <div style={{ fontSize:9, fontWeight:700, letterSpacing:'0.1em', textTransform:'uppercase', color:T.cyan, marginBottom:8 }}>
                  Why this candidate ranked first
                </div>
                <ul style={{ margin:0, padding:'0 0 0 16px', display:'flex', flexDirection:'column', gap:4 }}>
                  {reasons.map((r, i) => (
                    <li key={i} style={{ fontSize:10.5, color:T.txtSec, lineHeight:1.5 }}>{r}</li>
                  ))}
                </ul>
              </div>
            </>
          )}

          {/* ─ Partial scan banner ─ */}
          {scanResult?.isPartial && (
            <div style={{ background:`${T.amber}12`, border:`1px solid ${T.amber}44`, borderRadius:8,
              padding:'10px 14px', fontSize:11 }}>
              <div style={{ fontWeight:700, color:T.amber, letterSpacing:'0.06em', marginBottom:4 }}>
                ⚠ PARTIAL SCAN — {scanResult.succeeded} of {scanResult.total} markets evaluated
              </div>
              <div style={{ color:T.txtSec, fontSize:10 }}>
                {scanResult.failed.map(f => `${f.instrument} ${f.mode}`).join(' · ')} unavailable
              </div>
              <div style={{ color:T.txtMuted, fontSize:9.5, marginTop:4 }}>
                Ranking is based on the {scanResult.succeeded} responding candidates only.
                Re-scan when connectivity improves to evaluate the full universe.
              </div>
            </div>
          )}

          {/* ─ Scan metadata / freshness ─ */}
          {(() => {
            const nowMs = Date.now();
            const candidateAge = (cand?.record?.generated_at)
              ? Math.round((nowMs - new Date(cand.record.generated_at).getTime()) / 1000)
              : null;
            const isStale  = candidateAge != null && candidateAge > 30;
            const scanComplete = scanResult?.isPartial === false ? 'COMPLETE' : scanResult?.isPartial ? 'PARTIAL' : '—';
            const scanDuration = scanResult
              ? `${Math.round(scanResult.scannedAt - scanResult.scanStartedAt)}ms`
              : '—';
            return (
              <div style={{ borderTop:`1px solid ${T.border}`, paddingTop:10, display:'flex', flexDirection:'column', gap:4 }}>
                <div style={{ display:'flex', justifyContent:'space-between', fontSize:9, color:T.txtMuted }}>
                  <span>Scan: <strong style={{ color:T.txtSec }}>{scanComplete}</strong></span>
                  <span>Duration: {scanDuration}</span>
                  <span>Completed {age}</span>
                </div>
                {cand && (
                  <div style={{ display:'flex', justifyContent:'space-between', fontSize:9 }}>
                    <span style={{ color:T.txtMuted }}>
                      Data snapshot:{' '}
                      {freshness
                        ? <span style={{ color: isStale ? T.amber : T.txtSec }}>{freshness}</span>
                        : <span style={{ color:T.txtMuted }}>—</span>
                      }
                    </span>
                    {candidateAge != null && (
                      <span style={{ color: isStale ? T.amber : T.txtMuted, fontWeight: isStale ? 700 : 400 }}>
                        {isStale ? '⚠ STALE' : `${candidateAge}s old`}
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      </div>
    </div>
  );
};

// ── Cleanest Trade Button strip ────────────────────────────────────────────────
const CleanestTradeButton: React.FC<{
  scanResult:   CleanestScanResult | null;
  scanning:     boolean;
  onScan:       () => void;
  setOpen:      () => void;
}> = ({ scanResult, scanning, onScan, setOpen }) => {
  const cand = scanResult?.candidate ?? null;

  // Derive button appearance state
  let btnLabel: string;
  let btnColor: string;
  let btnBg:    string;
  let supportEl: React.ReactNode;

  if (scanning) {
    btnLabel = '🔎  Scanning all instruments…';
    btnColor = T.txtMuted;
    btnBg    = 'transparent';
    supportEl = null;
  } else if (!scanResult) {
    // Never scanned
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.cyan;
    btnBg     = `${T.cyan}12`;
    supportEl = (
      <span style={{ fontSize:9.5, color:T.txtMuted }}>
        Click to scan all instruments × modes for the best current setup
      </span>
    );
  } else if (scanResult.error) {
    // State D — unavailable
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.red;
    btnBg     = `${T.red}10`;
    supportEl = (
      <span style={{ fontSize:9.5, color:T.red }}>TRADE RANKING UNAVAILABLE — {scanResult.error}</span>
    );
  } else if (!cand) {
    // State C — no qualifying trade
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.txtMuted;
    btnBg     = 'transparent';
    supportEl = (
      <span style={{ fontSize:9.5, color:T.txtMuted }}>
        NO QUALIFYING TRADE — no actionable or potential setups found across scanned instruments
      </span>
    );
  } else if (cand.act === 1) {
    // State A — clean ready trade
    const stratLabel = safeStr(
      (cand.record.strategy_engine as Record<string, unknown> | undefined)?.active_strategy, ''
    );
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.green;
    btnBg     = `${T.green}10`;
    supportEl = (
      <span style={{ fontSize:9.5, color:T.green, letterSpacing:'0.04em' }}>
        ✓ {cand.instrument} {cand.direction}
        {stratLabel ? ` · ${stratLabel}` : ''}
        {' · '}{Math.round(cand.edge)}/110
        {scanResult.isPartial && (
          <span style={{ color:T.amber, marginLeft:4 }}>
            · PARTIAL ({scanResult.succeeded}/{scanResult.total} evaluated)
          </span>
        )}
      </span>
    );
  } else {
    // State B — potential only
    btnLabel  = '✨  Cleanest Trade Available';
    btnColor  = T.amber;
    btnBg     = `${T.amber}10`;
    supportEl = (
      <span style={{ fontSize:9.5, color:T.amber, letterSpacing:'0.04em' }}>
        ⏳ POTENTIAL — {cand.instrument} {cand.direction} · {Math.round(cand.edge)}/110
        {scanResult.isPartial && (
          <span style={{ marginLeft:4 }}>
            · PARTIAL ({scanResult.succeeded}/{scanResult.total} evaluated)
          </span>
        )}
      </span>
    );
  }

  const handleClick = () => {
    if (scanning) return;
    if (!scanResult) {
      onScan();
    } else {
      setOpen();
    }
  };

  return (
    <div style={{
      display:'flex', alignItems:'center', gap:14, flexWrap:'wrap',
      background:`${T.panel}`, border:`1px solid ${T.border}`, borderRadius:10,
      padding:'10px 16px', marginBottom:12,
    }}>
      <button
        onClick={handleClick}
        disabled={scanning}
        aria-label="Scan all instruments for the cleanest trade"
        style={{
          background:btnBg, border:`1.5px solid ${btnColor}44`, color:btnColor,
          borderRadius:8, padding:'7px 18px', cursor: scanning ? 'not-allowed' : 'pointer',
          fontSize:11, fontWeight:700, letterSpacing:'0.07em', opacity: scanning ? 0.7 : 1,
          transition:'all 0.2s', whiteSpace:'nowrap',
        }}
      >
        {btnLabel}
      </button>
      {supportEl && (
        <div style={{ flex:1, minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
          {supportEl}
        </div>
      )}
      {/* Re-scan button shown after first scan */}
      {scanResult && !scanning && (
        <button
          onClick={onScan}
          aria-label="Re-scan for cleanest trade"
          style={{
            background:'transparent', border:`1px solid ${T.border}`, color:T.txtMuted,
            borderRadius:6, padding:'4px 10px', cursor:'pointer', fontSize:9.5, fontWeight:600,
            transition:'opacity 0.2s', marginLeft:'auto', whiteSpace:'nowrap',
          }}
        >
          ↻ Re-scan
        </button>
      )}
    </div>
  );
};

const TradePlanPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  // Source: p.candidate_preview — normalised by _mb_candidate_preview() on the backend.
  // States: READY | POTENTIAL | NO_CANDIDATE | UNAVAILABLE
  // Active-trade awareness: when a live position exists and a preview is also present,
  // the preview is clearly labelled as a FUTURE CANDIDATE — never confused with the
  // live position managed by ActiveTradesPanel.
  const cp     = (p.candidate_preview ?? {}) as Record<string, unknown>;
  const at     = (p.active_trades    ?? {}) as Record<string, unknown>;
  const trades = Array.isArray(at.trades) ? at.trades as Record<string, unknown>[] : [];
  const hasActiveTrade = trades.length > 0;

  const status      = safeStr(cp.status, 'NO_CANDIDATE');
  const direction   = cp.direction as string | null | undefined;
  const isReady     = status === 'READY';
  const isPotential = status === 'POTENTIAL';
  const hasPlan     = isReady || isPotential;

  const statusLabel = isReady ? 'READY' : isPotential ? 'POTENTIAL' : 'NO CANDIDATE';
  const statusColor = isReady ? T.green  : isPotential ? T.amber    : T.txtMuted;

  const missing = Array.isArray(cp.missing_confirmations)
    ? (cp.missing_confirmations as string[])
    : [];

  return (
    <Panel title="Trade Plan">
      {status === 'UNAVAILABLE' ? (
        <UnavailableNote msg="Preview unavailable" />
      ) : !hasPlan ? (
        <div style={{ textAlign: 'center', padding: '20px 0', color: T.txtMuted, fontSize: 11 }}>
          No trade candidate developing
        </div>
      ) : (
        <>
          {/* Status header ─────────────────────────────────────────────────── */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            {direction != null && <Pill text={String(direction)} color={dirColor(direction)} />}
            <Pill text={statusLabel} color={statusColor} />
            {hasActiveTrade && isPotential && (
              <span style={{ fontSize: 9, color: T.amber, marginLeft: 'auto', letterSpacing: '0.06em', fontFamily: T.mono }}>
                FUTURE CANDIDATE
              </span>
            )}
          </div>

          {/* Core trade levels ──────────────────────────────────────────────── */}
          {cp.entry_zone   != null && <KV label="Entry Zone"    value={safeStr(cp.entry_zone)}   mono valueColor={T.cyan}  />}
          {cp.stop_loss    != null && <KV label="Stop Loss"     value={safeStr(cp.stop_loss)}    mono valueColor={T.red}   />}
          {cp.take_profit  != null && <KV label="Take Profit"   value={safeStr(cp.take_profit)}  mono valueColor={T.green} />}
          {cp.risk_reward  != null && <KV label="Risk / Reward" value={safeStr(cp.risk_reward)}  mono />}

          {/* Risk sizing ───────────────────────────────────────────────────── */}
          {cp.risk_points               != null && <KV label="Risk (pts)"      value={fmtNum(cp.risk_points, 2)}               mono />}
          {cp.risk_dollars_per_contract != null && <KV label="Risk / Contract" value={`$${fmtNum(cp.risk_dollars_per_contract, 0)}`} mono />}

          {/* ATR stop metadata ─────────────────────────────────────────────── */}
          {(cp.atr != null || cp.stop_ticks != null) && (
            <div style={{ marginTop: 7, paddingTop: 7, borderTop: `1px solid ${T.border}` }}>
              {cp.atr != null && cp.atr_multiplier != null && (
                <KV label="ATR Stop" value={`${fmtNum(cp.atr, 4)} pts × ${fmtNum(cp.atr_multiplier, 1)}`} mono />
              )}
              {cp.stop_ticks != null && (
                <KV label="Stop Distance" value={`${String(cp.stop_ticks)} ticks`} mono />
              )}
              {cp.stop_valid === false && cp.stop_invalid_reason != null && (
                <div style={{ marginTop: 4, fontSize: 10, color: T.red }}>
                  ⚠ {safeStr(cp.stop_invalid_reason)}
                </div>
              )}
            </div>
          )}

          {/* Missing confirmations (POTENTIAL only) ─────────────────────── */}
          {isPotential && missing.length > 0 && (
            <div style={{ marginTop: 8, paddingTop: 6, borderTop: `1px solid ${T.border}` }}>
              <div style={{ fontSize: 9, color: T.amber, letterSpacing: '0.06em', marginBottom: 4, fontFamily: T.mono }}>
                WAITING FOR
              </div>
              {missing.map((m, i) => (
                <div key={i} style={{ fontSize: 10, color: T.txtSec, paddingLeft: 8 }}>• {m}</div>
              ))}
            </div>
          )}

          {/* Preview disclaimer ─────────────────────────────────────────── */}
          {isPotential && (
            <div style={{ marginTop: 8, fontSize: 9, color: T.txtMuted, fontStyle: 'italic' }}>
              Preview only — setup developing, no order will be sent
            </div>
          )}
        </>
      )}
    </Panel>
  );
};

// ── Active Trades Panel ───────────────────────────────────────────────────────
const ActiveTradesPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const at    = (p.active_trades ?? {}) as Record<string, unknown>;
  const avail = at.available !== false;
  const trades = Array.isArray(at.trades) ? at.trades as Record<string, unknown>[] : [];

  return (
    <Panel title={`Active Trades (${trades.length})`}>
      {!avail ? <UnavailableNote /> : trades.length === 0 ? (
        <div style={{ textAlign:'center', padding:'20px 0', color:T.txtMuted, fontSize:11 }}>No active trades</div>
      ) : (
        trades.map((t, i) => {
          const dir  = safeStr(t.direction, '');
          const inst = safeStr(t.instrument, '');
          const dCol = dirColor(dir);
          const pnl  = safeNum(t.unrealized_pnl);
          const curR = safeNum(t.current_r);
          const pnlCol = pnl == null ? T.txtSec : pnl >= 0 ? T.green : T.red;
          return (
            <div key={i} style={{ background:T.panelAlt, borderRadius:8, border:`1px solid ${T.border}`, padding:'10px 12px', marginBottom:8 }}>
              <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:8 }}>
                <Pill text={dir || '—'} color={dCol} />
                <span style={{ fontSize:12, fontWeight:700, color:T.txtPri, fontFamily:T.mono }}>{inst}</span>
                <span style={{ marginLeft:'auto', fontSize:9, color:T.cyan }}>OPEN</span>
              </div>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'2px 12px' }}>
                <KV label="Strategy" value={safeStr(t.strategy, '—')} />
                <KV label="Contracts" value={safeStr(t.quantity, '—')} mono />
                <KV label="Entry" value={fmtNum(t.entry)} mono />
                {t.stop  != null && <KV label="Stop"  value={fmtNum(t.stop)}  mono valueColor={T.red} />}
                {curR != null && <KV label="Current R" value={`${curR >= 0 ? '+' : ''}${fmtNum(curR, 2)}R`} mono valueColor={curR >= 0 ? T.green : T.red} />}
                {pnl != null && <KV label="Unreal. P&L" value={`$${fmtNum(pnl, 0)}`} mono valueColor={pnlCol} />}
                <KV label="Opened" value={fmtTs(t.opened_at)} />
              </div>
            </div>
          );
        })
      )}
    </Panel>
  );
};

// ── Execution Status Panel ────────────────────────────────────────────────────
const ExecutionPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const gw  = (p.execution_gateway ?? {}) as Record<string, unknown>;
  const sys = (p.system_status ?? {}) as Record<string, unknown>;
  const avail = gw.available !== false;

  return (
    <Panel title="Execution Status">
      {!avail ? <UnavailableNote /> : (
        <>
          <div style={{ display:'flex', gap:6, marginBottom:10, flexWrap:'wrap' }}>
            {gw.mode != null && <Pill text={String(gw.mode)} color={gw.mode === 'manual_only' ? T.amber : T.green} />}
            {gw.gateway_status != null && <Badge label={String(gw.gateway_status)} color={gw.gateway_status === 'SENT' ? T.green : T.txtSec} />}
          </div>
          <KV label="Last Signal"   value={fmtTs(gw.last_sent_at)} />
          <KV label="Broker Ready"  value={
            <span style={{ display:'flex', alignItems:'center', gap:5 }}>
              {statusDot(sys.broker_ready ? true : false)}
              <span>{sys.broker_ready ? 'YES' : 'NO'}</span>
            </span>
          } />
          <KV label="DB Ready"      value={
            <span style={{ display:'flex', alignItems:'center', gap:5 }}>
              {statusDot(sys.db_ready ? true : false)}
              <span>{sys.db_ready ? 'YES' : 'NO'}</span>
            </span>
          } />
          <div style={{ marginTop:8, padding:'6px 10px', background:'rgba(255,255,255,0.025)', borderRadius:6, fontSize:10, color:T.txtMuted }}>
            <span style={{ color:T.amber }}>Note:</span> Last outcome not available — deferred to Phase 7D.
          </div>
        </>
      )}
    </Panel>
  );
};

// ── Coach Panel ───────────────────────────────────────────────────────────────
const CoachPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const coach = (p.coach ?? {}) as Record<string, unknown>;
  const perf  = (p.performance ?? {}) as Record<string, unknown>;
  const avail = coach.available !== false;

  // ── learning_diagnostics from Phase 7I audit ───────────────────────────────
  const ld = (coach.learning_diagnostics ?? {}) as Record<string, unknown>;
  const weightStatus   = safeStr(ld.weight_status, '');
  const wsLabel        = WEIGHT_STATUS_LABEL[weightStatus] ?? weightStatus;
  const wsColor        = WEIGHT_STATUS_COLOR[weightStatus] ?? T.txtMuted;
  const sampleCount    = safeNum(ld.sample_count) ?? 0;
  const minSamples     = safeNum(ld.minimum_samples) ?? 20;
  const currentWeight  = safeNum(ld.current_weight) ?? 1.0;
  const weightDelta    = safeNum(ld.weight_delta) ?? 0.0;
  const influencePts   = safeNum(ld.influence_points) ?? 0;
  const blockedReason  = safeStr(ld.blocked_reason, '');
  const stratKey       = safeStr(ld.strategy_key, '');
  const lastUpdate     = safeStr(ld.last_weight_update_at, '');
  const scoreEnabled   = ld.influence_enabled === true;
  const isApplied      = ld.applied_to_live_score === true;
  const samplePct      = Math.min(100, Math.round(sampleCount / minSamples * 100));
  const isCollecting   = weightStatus === 'INSUFFICIENT_SAMPLES' || weightStatus === 'NOT_ELIGIBLE';
  const isActive       = weightStatus === 'UPDATED' || weightStatus === 'NO_CHANGE';

  return (
    <Panel title="Coach" badge={<Badge label="LEARNING" color={T.purple} />}>
      {!avail ? <UnavailableNote /> : (
        <>
          {/* ── Status header ────────────────────────────────────────────── */}
          <div style={{ padding:'8px 10px', borderRadius:7, marginBottom:10,
            background: isActive ? 'rgba(34,197,94,0.06)' : isCollecting ? 'rgba(245,158,11,0.06)' : 'rgba(100,116,139,0.06)',
            border: `1px solid ${isActive ? 'rgba(34,197,94,0.18)' : isCollecting ? 'rgba(245,158,11,0.18)' : 'rgba(100,116,139,0.18)'}`,
            borderLeft: `3px solid ${wsColor}` }}>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom: isCollecting || isActive ? 8 : 0 }}>
              <div style={{ fontSize:9, letterSpacing:'0.1em', fontWeight:700, color:wsColor }}>
                LEARNING {wsLabel}
              </div>
              {scoreEnabled
                ? <span style={{ fontSize:8.5, color:T.green }}>SCORE GATE ON</span>
                : <span style={{ fontSize:8.5, color:T.txtMuted }}>SCORE GATE OFF</span>}
            </div>

            {/* Sample progress bar */}
            {(isCollecting || isActive) && (
              <div style={{ marginBottom:8 }}>
                <div style={{ display:'flex', justifyContent:'space-between', fontSize:9, color:T.txtMuted, marginBottom:3 }}>
                  <span>{sampleCount} / {minSamples} required samples</span>
                  <span style={{ color: sampleCount >= minSamples ? T.green : T.amber }}>{samplePct}%</span>
                </div>
                <div style={{ height:4, background:'rgba(255,255,255,0.07)', borderRadius:2 }}>
                  <div style={{ height:'100%', width:`${samplePct}%`, background: sampleCount >= minSamples ? T.green : T.amber, borderRadius:2, transition:'width 0.4s ease' }} />
                </div>
              </div>
            )}

            {/* Weight and delta */}
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'3px 10px', fontSize:9.5 }}>
              <><span style={{ color:T.txtMuted }}>Current weight</span>
                <span style={{ fontFamily:T.mono, color: Math.abs(weightDelta) > 0.01 ? T.cyan : T.txtSec }}>
                  {currentWeight.toFixed(3)}{Math.abs(weightDelta) > 0.001 ? ` (${weightDelta >= 0 ? '+' : ''}${weightDelta.toFixed(3)})` : ''}
                </span></>
              <><span style={{ color:T.txtMuted }}>Live influence</span>
                <span style={{ fontFamily:T.mono, color: influencePts !== 0 ? T.cyan : T.txtMuted }}>
                  {influencePts !== 0 ? `${influencePts >= 0 ? '+' : ''}${influencePts} pts` : '0 pts'}
                </span></>
            </div>
          </div>

          {/* ── Detail rows ──────────────────────────────────────────────── */}
          {blockedReason && !isApplied && (
            <div style={{ marginBottom:8, padding:'6px 10px', background:'rgba(245,158,11,0.05)', borderRadius:6, border:`1px solid rgba(245,158,11,0.15)` }}>
              <div style={{ fontSize:9, color:T.amber, letterSpacing:'0.07em', fontWeight:700 }}>BLOCKED REASON</div>
              <div style={{ fontSize:9.5, color:T.txtSec, marginTop:3 }}>
                {blockedReason === 'INSUFFICIENT_SAMPLES'
                  ? `Minimum sample threshold not reached (${sampleCount}/${minSamples})`
                  : blockedReason === 'DISABLED'
                  ? 'Learning score influence is disabled'
                  : blockedReason === 'KEY_NOT_FOUND'
                  ? 'Strategy key not found in weight store (key mismatch)'
                  : blockedReason === 'NOT_ELIGIBLE'
                  ? 'Weight recompute has not run yet this session'
                  : blockedReason}
              </div>
            </div>
          )}

          {isApplied && stratKey && (
            <div style={{ marginBottom:8, padding:'5px 10px', background:'rgba(34,197,94,0.05)', borderRadius:6, border:`1px solid rgba(34,197,94,0.15)` }}>
              <div style={{ fontSize:9, color:T.green, letterSpacing:'0.07em', fontWeight:700 }}>APPLIED TO</div>
              <div style={{ fontSize:9, color:T.txtSec, marginTop:2, fontFamily:T.mono, wordBreak:'break-all' }}>{stratKey}</div>
            </div>
          )}

          <KV label="Thesis Resolved" value={
            <span title="Whether a thesis was resolved in this session — not a measure of learning readiness">
              {coach.thesis_resolved ? 'YES' : 'NO'}
            </span>
          } />
          <KV label="Last Resolved" value={fmtTs(safeStr(coach.thesis_last_resolved_at, ''))} />
          {lastUpdate && <KV label="Last Weight Update" value={fmtTs(lastUpdate)} />}
          <KV label="LRE Status" value={
            <span style={{ color: coach.rule_engine_eligibility === 'LIVE_ELIGIBLE' ? T.green : T.amber }}>
              {safeStr(coach.rule_engine_eligibility, '—')}
            </span>
          } />

          {/* ── Performance summary ───────────────────────────────────────── */}
          {Object.keys(perf).length > 0 && perf.available === true && (
            <div style={{ marginTop:10, borderTop:`1px solid ${T.border}`, paddingTop:10 }}>
              <div style={{ fontSize:9, color:T.purple, letterSpacing:'0.1em', marginBottom:6 }}>PERFORMANCE REVIEW</div>
              {perf.win_rate  != null && <KV label="Win Rate"   value={`${fmtNum(perf.win_rate, 0)}%`} mono />}
              {perf.avg_r     != null && <KV label="Avg R"      value={fmtNum(perf.avg_r)}              mono />}
              {perf.trade_count != null && <KV label="Sample"   value={String(perf.trade_count)}        mono />}
              {perf.best_setup != null && <KV label="Best Setup" value={String(perf.best_setup)} />}
            </div>
          )}

          <div style={{ marginTop:8, fontSize:8.5, color:T.txtMuted, lineHeight:1.5 }}>
            <span style={{ color:T.amber }}>ℹ</span>{' '}
            Influence = 0 until {minSamples} samples. "Weight Updated" = recompute ran, not that weight changed.
          </div>
        </>
      )}
    </Panel>
  );
};

// ── Journal Panel ─────────────────────────────────────────────────────────────
const JournalPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const jnl   = (p.journal ?? {}) as Record<string, unknown>;
  const avail = jnl.available !== false;
  const trades = Array.isArray(jnl.recent_closed) ? jnl.recent_closed as Record<string, unknown>[] : [];

  return (
    <Panel title="Journal">
      {!avail ? <UnavailableNote /> : (
        <>
          <div style={{ display:'flex', gap:16, marginBottom:12 }}>
            {jnl.today_count != null && (
              <div style={{ textAlign:'center' }}>
                <div style={{ fontSize:20, fontWeight:800, color:T.cyan, fontFamily:T.mono }}>{String(jnl.today_count)}</div>
                <div style={{ fontSize:9, color:T.txtMuted }}>TODAY</div>
              </div>
            )}
            {jnl.today_win_rate != null && (
              <div style={{ textAlign:'center' }}>
                <div style={{ fontSize:20, fontWeight:800, color:T.green, fontFamily:T.mono }}>{fmtNum(jnl.today_win_rate, 0)}%</div>
                <div style={{ fontSize:9, color:T.txtMuted }}>WIN RATE</div>
              </div>
            )}
            {jnl.today_avg_r != null && (
              <div style={{ textAlign:'center' }}>
                <div style={{ fontSize:20, fontWeight:800, color:T.amber, fontFamily:T.mono }}>{fmtNum(jnl.today_avg_r)}R</div>
                <div style={{ fontSize:9, color:T.txtMuted }}>AVG R</div>
              </div>
            )}
          </div>

          {trades.length === 0 ? (
            <div style={{ fontSize:11, color:T.txtMuted, textAlign:'center', padding:'8px 0' }}>No closed trades</div>
          ) : (
            <div style={{ overflowX:'auto' }}>
              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:10 }} aria-label="Recent closed trades">
                <thead>
                  <tr>
                    {['Time','Inst','Dir','Setup','Result','R'].map(h => (
                      <th key={h} style={{ textAlign:'left', color:T.txtMuted, fontWeight:600, paddingBottom:5, fontSize:9, letterSpacing:'0.07em', whiteSpace:'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {trades.slice(0, 10).map((t, i) => {
                    const r = safeNum(t.r_multiple);
                    const rCol = r == null ? T.txtSec : r > 0 ? T.green : T.red;
                    return (
                      <tr key={i} style={{ borderTop:`1px solid ${T.border}` }}>
                        <td style={{ padding:'4px 0', color:T.txtMuted, whiteSpace:'nowrap' }}>{fmtTs(t.closed_at)}</td>
                        <td style={{ padding:'4px 6px 4px 0', color:T.cyan, fontFamily:T.mono, fontWeight:700 }}>{safeStr(t.instrument, '—')}</td>
                        <td style={{ color:dirColor(t.direction) }}>{safeStr(t.direction, '—').substring(0,5)}</td>
                        <td style={{ color:T.txtSec, maxWidth:80, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{safeStr(t.setup, '—')}</td>
                        <td style={{ color: String(t.result ?? '').toLowerCase() === 'win' ? T.green : T.red }}>{safeStr(t.result, '—').toUpperCase()}</td>
                        <td style={{ color:rCol, fontFamily:T.mono, fontWeight:700 }}>{r != null ? (r >= 0 ? '+' : '') + fmtNum(r) : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Panel>
  );
};

// ── Decision Timeline Panel ───────────────────────────────────────────────────
const TimelinePanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const tl    = (p.decision_timeline ?? {}) as Record<string, unknown>;
  const avail = tl.available !== false;
  const events = Array.isArray(tl.events) ? tl.events as Record<string, unknown>[] : [];

  return (
    <Panel title="Decision Timeline" badge={<Badge label="PARTIAL" color={T.amber} />}>
      {!avail ? <UnavailableNote /> : (
        <>
          <div style={{ marginBottom:8, fontSize:10, color:T.amber }}>
            Partial timeline — additional event capture is planned.
          </div>
          {events.length === 0 ? (
            <UnavailableNote msg="No events recorded this session" />
          ) : (
            <div>
              {events.map((e, i) => (
                <div key={i} style={{ display:'flex', gap:10, paddingBottom:10, position:'relative' }}>
                  <div style={{ display:'flex', flexDirection:'column', alignItems:'center', flexShrink:0 }}>
                    <div style={{ width:8, height:8, borderRadius:'50%', background:T.cyan, marginTop:2 }} />
                    {i < events.length - 1 && <div style={{ width:1, flex:1, background:T.border, marginTop:3 }} />}
                  </div>
                  <div style={{ flex:1, minWidth:0, paddingBottom:4 }}>
                    <div style={{ display:'flex', gap:6, alignItems:'center', marginBottom:2 }}>
                      <span style={{ fontSize:10, fontWeight:700, color:T.txtPri }}>{safeStr(e.event_label, safeStr(e.event_type, '—'))}</span>
                      {e.is_derived != null && e.is_derived && <Badge label="derived" color={T.txtMuted} />}
                    </div>
                    <div style={{ fontSize:9.5, color:T.txtMuted }}>{fmtTs(e.timestamp)} · {safeStr(e.source, '—')}</div>
                    {e.details != null && <div style={{ fontSize:10, color:T.txtSec, marginTop:2 }}>{safeStr(e.details)}</div>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Panel>
  );
};

// ── Alerts Panel ──────────────────────────────────────────────────────────────
const AlertsPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const al    = (p.alerts ?? {}) as Record<string, unknown>;
  const avail = al.available !== false;
  const items = Array.isArray(al.items) ? al.items as Record<string, unknown>[] : [];

  return (
    <Panel title="Live Alerts Feed">
      {!avail ? <UnavailableNote /> : items.length === 0 ? (
        <UnavailableNote msg="No alerts" />
      ) : (
        <div style={{ maxHeight:240, overflowY:'auto' }}>
          {items.map((a, i) => {
            const sev = String(a.severity ?? a.alert_type ?? '').toLowerCase();
            const col = /ready/.test(sev) ? T.green : /warn|early/.test(sev) ? T.amber : T.txtSec;
            return (
              <div key={i} style={{ display:'flex', gap:8, padding:'5px 0', borderBottom:`1px solid ${T.border}` }}>
                <span style={{ fontSize:9, color:T.txtMuted, whiteSpace:'nowrap', minWidth:52 }}>{fmtTs(a.timestamp)}</span>
                <span style={{ fontSize:9.5, fontWeight:700, color:col, fontFamily:T.mono, minWidth:36, flexShrink:0 }}>{safeStr(a.instrument, '—')}</span>
                <span style={{ fontSize:10, color:T.txtSec, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{safeStr(a.message ?? a.alert_type, '—')}</span>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
};

// ── System Health Panel ───────────────────────────────────────────────────────
const SystemHealthPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const sys   = (p.system_status ?? {}) as Record<string, unknown>;
  const avail = (p.availability ?? {}) as Record<string, unknown>;
  const errs  = Array.isArray(p.errors) ? (p.errors as Record<string, unknown>[]).filter(e => e.source) : [];

  const checks = [
    { label: 'Database',   ok: sys.db_ready },
    { label: 'Learning',   ok: sys.learning_ready },
    { label: 'Databento',  ok: sys.databento_ready },
    { label: 'Broker',     ok: sys.broker_ready },
    { label: 'Left Brain', ok: avail.left_brain },
    { label: 'Scanner',    ok: avail.strategy_scanner },
    { label: 'Coach',      ok: avail.coach },
    { label: 'Journal',    ok: avail.journal },
    { label: 'Timeline',   ok: avail.decision_timeline },
  ];

  return (
    <Panel title="System Health">
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'4px 12px' }}>
        {checks.map(c => (
          <div key={c.label} style={{ display:'flex', alignItems:'center', gap:6, padding:'3px 0' }}>
            {statusDot(c.ok == null ? null : Boolean(c.ok))}
            <span style={{ fontSize:10.5, color:T.txtSec }}>{c.label}</span>
            <span style={{ marginLeft:'auto', fontSize:9, color:c.ok ? T.green : T.red, fontWeight:700 }}>
              {c.ok ? 'OK' : c.ok == null ? '—' : 'ERR'}
            </span>
          </div>
        ))}
      </div>
      {errs.length > 0 && (
        <div style={{ marginTop:10, borderTop:`1px solid ${T.border}`, paddingTop:8 }}>
          {errs.slice(0, 5).map((e, i) => (
            <div key={i} style={{ fontSize:9.5, color:T.red, marginBottom:2 }}>
              ⚠ {safeStr(e.source)}: {safeStr(e.code)}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
};

// ── Header ────────────────────────────────────────────────────────────────────
const Header: React.FC<{
  p: Record<string, unknown> | null;
  fetchState: FetchState;
  lastOk: number | null;
  ticker: string;
  setTicker: (t: string) => void;
  refresh: () => void;
}> = ({ p, fetchState, lastOk, ticker, setTicker, refresh }) => {
  const clock = useClock();
  const mkt = ((p?.market ?? {}) as Record<string, unknown>);
  const sys = ((p?.system_status ?? {}) as Record<string, unknown>);
  const allOk = !!(sys.db_ready && sys.learning_ready);
  const stale = fetchState === 'stale';
  const loading = fetchState === 'loading' || fetchState === 'refreshing';

  return (
    <header style={{ background:'#030b1a', borderBottom:`1px solid ${T.border}`, padding:'0 20px', display:'flex', alignItems:'center', gap:12, height:52, flexShrink:0, position:'sticky', top:0, zIndex:20 }}>
      {/* Brand */}
      <div style={{ display:'flex', alignItems:'center', gap:8 }}>
        <span style={{ fontSize:14, lineHeight:1 }}>🧠</span>
        <div>
          <div style={{ fontSize:12, fontWeight:800, color:T.txtPri, lineHeight:1 }}>Main Brain</div>
          <div style={{ fontSize:8.5, color:T.cyan, letterSpacing:'0.1em', lineHeight:1.2 }}>OPERATOR CONSOLE</div>
        </div>
      </div>

      {/* Instrument selector */}
      <div style={{ marginLeft:16 }}>
        {(['MGC', 'MNQ', 'MES', 'MYM'] as const).map(t => (
          <button key={t} onClick={() => setTicker(t)} aria-pressed={ticker === t} style={{
            background: ticker === t ? `${T.cyan}20` : 'transparent',
            border: `1px solid ${ticker === t ? T.cyan : T.border}`,
            color: ticker === t ? T.cyan : T.txtMuted,
            fontSize: 10, fontWeight: 700, padding: '3px 8px', borderRadius: 5, cursor: 'pointer', marginRight: 4,
          }}>{t}</button>
        ))}
      </div>

      {/* Market context */}
      <div style={{ display:'flex', gap:16, marginLeft:8 }}>
        <div>
          <div style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.08em' }}>TIME (ET)</div>
          <div style={{ fontSize:12, fontWeight:700, color:T.txtPri, fontFamily:T.mono }}>{clock}</div>
        </div>
        <div>
          <div style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.08em' }}>SESSION</div>
          <div style={{ fontSize:12, fontWeight:700, color: /open/i.test(String(mkt.session_status)) ? T.green : T.amber }}>
            {safeStr(mkt.session_status, '—')}
          </div>
        </div>
        <div>
          <div style={{ fontSize:8.5, color:T.txtMuted, letterSpacing:'0.08em' }}>MODE</div>
          <div style={{ fontSize:12, fontWeight:700, color:T.blue }}>{safeStr(mkt.trading_mode, '—')}</div>
        </div>
      </div>

      {/* System status */}
      <div style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:12 }}>
        {stale && <span style={{ fontSize:10, color:T.amber, fontWeight:700 }}>⚠ STALE DATA</span>}
        {fetchState === 'error' && <span style={{ fontSize:10, color:T.red, fontWeight:700 }}>✗ CONNECTION ERROR</span>}
        {fetchState === 'auth_fail' && <span style={{ fontSize:10, color:T.red }}>AUTH REQUIRED — <a href="/" style={{ color:T.cyan }}>Go to login</a></span>}
        <div style={{ display:'flex', alignItems:'center', gap:5 }}>
          {statusDot(allOk ? true : null)}
          <span style={{ fontSize:9.5, color:T.txtMuted }}>
            {lastOk ? `Updated ${fmtAge(new Date(lastOk).toISOString())}` : 'Connecting…'}
          </span>
        </div>
        <button onClick={refresh} disabled={loading} aria-label="Refresh data" style={{
          background:'transparent', border:`1px solid ${T.border}`, color:T.txtSec, borderRadius:6,
          padding:'4px 10px', cursor:'pointer', fontSize:10, fontWeight:600,
          opacity: loading ? 0.5 : 1, transition:'opacity 0.2s',
        }}>
          {loading ? '↻ …' : '↻ Refresh'}
        </button>
      </div>
    </header>
  );
};

// ── AI Summary (header region) ────────────────────────────────────────────────
const AISummary: React.FC<{ p: Record<string, unknown> | null }> = ({ p }) => {
  const mb = ((p?.main_brain ?? {}) as Record<string, unknown>);
  const voice = safeStr(mb.voice ?? mb.narrative, '');
  return (
    <div style={{ background:`${T.cyan}08`, border:`1px solid ${T.cyan}22`, borderRadius:8, padding:'8px 14px', fontSize:11, color:T.txtSec, lineHeight:1.55, fontStyle:'italic' }}>
      {voice || 'Main Brain data is current.'}
    </div>
  );
};

// ── Loading / Error states ────────────────────────────────────────────────────
const LoadingScreen: React.FC = () => (
  <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', flex:1, gap:16, color:T.txtMuted }}>
    <div style={{ fontSize:32 }}>🧠</div>
    <div style={{ fontSize:14, color:T.cyan }}>Connecting to Main Brain…</div>
    <div style={{ fontSize:11 }}>Fetching operator console data</div>
  </div>
);

const ErrorScreen: React.FC<{ msg: string | null; refresh: () => void }> = ({ msg, refresh }) => (
  <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', flex:1, gap:16, color:T.txtMuted }}>
    <div style={{ fontSize:32 }}>⚠</div>
    <div style={{ fontSize:14, color:T.red }}>Main Brain Unavailable</div>
    <div style={{ fontSize:11, maxWidth:400, textAlign:'center' }}>{msg ?? 'Could not reach the Main Brain endpoint. Ensure the bot is running.'}</div>
    <button onClick={refresh} style={{ background:`${T.cyan}20`, border:`1px solid ${T.cyan}44`, color:T.cyan, borderRadius:8, padding:'8px 20px', cursor:'pointer', fontSize:12, fontWeight:700 }}>
      Retry
    </button>
  </div>
);

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function MainBrain() {
  // Section param — present when route is /main-brain/:section, absent at /main-brain
  const params = useParams<{ section?: string }>();
  const section = (params as Record<string, string | undefined>).section ?? '';

  const [ticker, setTicker] = useState<string>(() => {
    try { return localStorage.getItem('mb_ticker') || 'MGC'; } catch { return 'MGC'; }
  });
  const handleSetTicker = (t: string) => {
    setTicker(t); try { localStorage.setItem('mb_ticker', t); } catch {}
  };

  // Redirect unknown section segments to the root overview (replace so the invalid
  // URL is removed from browser history rather than pushed as a new entry).
  const [, navigate] = useLocation();
  useEffect(() => {
    if (section !== '' && !KNOWN_SECTIONS.includes(section)) {
      navigate('/main-brain', { replace: true });
    }
  }, [section]); // eslint-disable-line react-hooks/exhaustive-deps

  const { payload, fetchState, lastOk, error, isAuthFail, refresh } = useMainBrain(ticker);

  const p = (payload ?? {}) as Record<string, unknown>;
  const sys = (p.system_status ?? {}) as Record<string, unknown>;
  const allOk = !!(sys.db_ready && sys.learning_ready);
  const isLoading = fetchState === 'loading' && !payload;
  const isError   = (fetchState === 'error' || isAuthFail) && !payload;

  // ── Cleanest Trade state ──────────────────────────────────────────────────
  // scanGenRef is a generation counter incremented on every scan attempt.
  // After each async Promise.all() completes, the handler checks whether its
  // captured generation still matches the current value — if a newer scan was
  // started (e.g. a second click before the first settled), the stale result
  // is silently discarded instead of overwriting the newer one.
  const scanGenRef = useRef(0);
  const [cleanestOpen,     setCleanestOpen]     = useState(false);
  const [cleanestScanning, setCleanestScanning] = useState(false);
  const [cleanestScan,     setCleanestScan]     = useState<CleanestScanResult | null>(null);

  const handleScanCleanest = useCallback(async () => {
    // Guard: while a scan is already in flight, ignore additional clicks.
    if (cleanestScanning) return;
    // Increment the generation counter and capture this scan's generation id.
    const thisGen = ++scanGenRef.current;
    setCleanestScanning(true);
    const scanStartedAt = Date.now();
    try {
      const requests = SCAN_INSTRUMENTS.flatMap(inst =>
        SCAN_MODES.map(mode => ({ instrument: inst, mode }))
      );
      const resolved = await Promise.all(
        requests.map(async ({ instrument, mode }): Promise<RankInput> => {
          try {
            const r = await fetch(
              `/api/status?ticker=${encodeURIComponent(instrument)}&mode=${encodeURIComponent(mode)}`,
              { credentials: 'include', headers: getAuthHeader() }
            );
            const respondedAt = Date.now();
            if (!r.ok) return {
              instrument, mode, record: null,
              meta: { instrument, mode, ok: false, respondedAt,
                      responseAgeMs: respondedAt - scanStartedAt, generated_at: null },
            };
            const data = await r.json() as StatusRecord;
            return {
              instrument, mode, record: data,
              meta: { instrument, mode, ok: true, respondedAt,
                      responseAgeMs: respondedAt - scanStartedAt,
                      generated_at: (data as Record<string, unknown>).generated_at as string ?? null },
            };
          } catch {
            const respondedAt = Date.now();
            return {
              instrument, mode, record: null,
              meta: { instrument, mode, ok: false, respondedAt,
                      responseAgeMs: respondedAt - scanStartedAt, generated_at: null },
            };
          }
        })
      );
      // Generation guard: if a newer scan was initiated while this one was
      // in flight, discard this result rather than overwriting the newer one.
      if (thisGen !== scanGenRef.current) return;

      const failed    = resolved.filter(r => r.record === null)
                                .map(r => ({ instrument: r.instrument as string, mode: r.mode as string }));
      const succeeded = resolved.filter(r => r.record !== null).length;
      const candidate = rankCandidates(resolved);

      setCleanestScan({
        candidate, error: null, allInputs: resolved,
        scannedAt: Date.now(), scanStartedAt,
        isPartial: failed.length > 0, succeeded, total: resolved.length, failed,
      });
      setCleanestOpen(true);
    } catch {
      if (thisGen !== scanGenRef.current) return;
      setCleanestScan({
        candidate: null, error: 'Scan failed — check connection', allInputs: [],
        scannedAt: Date.now(), scanStartedAt,
        isPartial: false, succeeded: 0, total: 8, failed: [],
      });
      setCleanestOpen(true);
    } finally {
      setCleanestScanning(false);
    }
  }, [cleanestScanning]);

  // ── Audio notification transitions ────────────────────────────────────────
  // Refs hold the PREVIOUS value so we can detect state changes without
  // re-running the effect on every render. All audio calls go through
  // audioManager.play() — no Audio objects created here.
  const prevActionableRef  = useRef<boolean | null>(null);  // null = not yet known
  const prevFetchOkRef     = useRef<boolean>(false);        // true once we had a 'loaded' state
  const prevScannerKeyRef  = useRef<string>('');

  useEffect(() => {
    // Ignore during initial loading — wait for first real payload.
    if (!payload) return;

    const verdict = (p.verdict ?? {}) as Record<string, unknown>;
    const sc      = (p.strategy_scanner ?? {}) as Record<string, unknown>;

    const isActionable  = verdict.is_actionable === true;
    const isConnected   = fetchState === 'loaded' || fetchState === 'stale' || fetchState === 'refreshing';
    const isDisconnected = fetchState === 'error';

    // READY_TO_TRADE — fires only on NOT_READY → READY transition.
    // prevActionableRef starts null so the very first READY payload also fires.
    if (isActionable && prevActionableRef.current !== true) {
      audioManager.play(SoundEvent.READY_TO_TRADE);
    }
    prevActionableRef.current = isActionable;

    // SYSTEM_ONLINE — fires when we transition from disconnected/initial → connected.
    if (isConnected && !prevFetchOkRef.current) {
      audioManager.play(SoundEvent.SYSTEM_ONLINE);
    }
    // SYSTEM_OFFLINE — fires when we transition from connected → error.
    if (isDisconnected && prevFetchOkRef.current) {
      audioManager.play(SoundEvent.SYSTEM_OFFLINE);
    }
    if (isConnected) prevFetchOkRef.current = true;
    if (isDisconnected) prevFetchOkRef.current = false;

    // SCAN_FOUND — fires when a different (or new) strategy gets selected by
    // the scanner, signalling a newly discovered opportunity.
    const scannerKey = String(sc.selected_strategy ?? '');
    if (scannerKey && scannerKey !== prevScannerKeyRef.current) {
      audioManager.play(SoundEvent.SCAN_FOUND);
    }
    prevScannerKeyRef.current = scannerKey;

  }, [payload, fetchState]); // eslint-disable-line react-hooks/exhaustive-deps

  // Section-based panel rendering — always reuses existing panel components,
  // never creates a second polling loop or duplicates business logic.
  const renderSectionPanels = (): React.ReactNode => {
    switch (section) {
      case 'analysis':
        return (
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }} className="mb-grid-2">
            <ThesisPanel p={p} />
            <VerdictPanel p={p} />
          </div>
        );
      case 'scanner':
        return (
          <>
            <div style={{ marginBottom:10 }}><ScannerPanel p={p} /></div>
            <TradePlanPanel p={p} />
          </>
        );
      case 'trades':
        return (
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10 }} className="mb-grid-3">
            <TradePlanPanel p={p} />
            <ActiveTradesPanel p={p} />
            <ExecutionPanel p={p} />
          </div>
        );
      case 'journal':
        return <JournalPanel p={p} />;
      case 'coach':
        return <CoachPanel p={p} />;
      case 'alerts':
        return (
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10 }} className="mb-grid-3">
            <TimelinePanel p={p} />
            <AlertsPanel p={p} />
            <SystemHealthPanel p={p} />
          </div>
        );
      default:
        // Root overview or unknown section → full 4-row grid
        return (
          <>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10, marginBottom:10 }} className="mb-grid-3">
              <ThesisPanel p={p} />
              <VerdictPanel p={p} />
              <ScannerPanel p={p} />
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10, marginBottom:10 }} className="mb-grid-3">
              <TradePlanPanel p={p} />
              <ActiveTradesPanel p={p} />
              <ExecutionPanel p={p} />
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginBottom:10 }} className="mb-grid-2">
              <CoachPanel p={p} />
              <JournalPanel p={p} />
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10 }} className="mb-grid-3">
              <TimelinePanel p={p} />
              <AlertsPanel p={p} />
              <SystemHealthPanel p={p} />
            </div>
          </>
        );
    }
  };

  return (
    <div style={{ display:'flex', minHeight:'100vh', background:T.bg, color:T.txtPri, fontFamily:"'Inter',system-ui,sans-serif" }}>
      {/* Skip link */}
      <a href="#main-content" style={{ position:'absolute', left:-9999, top:0, background:T.cyan, color:'#000', padding:'4px 12px', borderRadius:4, zIndex:100, fontSize:12, fontWeight:700 }}
        onFocus={e => { e.currentTarget.style.left='0'; }} onBlur={e => { e.currentTarget.style.left='-9999px'; }}>
        Skip to content
      </a>

      {/* Side nav */}
      <SideNav systemOk={allOk} />

      {/* Main area */}
      <div style={{ flex:1, display:'flex', flexDirection:'column', minWidth:0, overflowX:'hidden' }}>
        <Header p={p} fetchState={fetchState} lastOk={lastOk} ticker={ticker} setTicker={handleSetTicker} refresh={refresh} />

        <main id="main-content" style={{ flex:1, padding:'16px 20px 32px', overflow:'auto' }}>
          {isLoading ? <LoadingScreen /> : isError ? <ErrorScreen msg={error} refresh={refresh} /> : (
            <>
              {/* Stale banner */}
              {fetchState === 'stale' && (
                <div role="alert" style={{ background:`${T.amber}18`, border:`1px solid ${T.amber}44`, borderRadius:8, padding:'8px 14px', marginBottom:12, fontSize:11, color:T.amber, display:'flex', alignItems:'center', gap:8 }}>
                  <span>⚠</span> Showing stale data — last successful fetch {fmtAge(lastOk ? new Date(lastOk).toISOString() : '')}. Retrying automatically.
                </div>
              )}

              {/* AI Summary strip */}
              <div style={{ marginBottom:12 }}>
                <AISummary p={p} />
              </div>

              {/* Market State Strip */}
              <div style={{ marginBottom:12 }}>
                <MarketStrip p={p} />
              </div>

              {/* ── Cleanest Trade Available button strip ──────────────────── */}
              <CleanestTradeButton
                scanResult={cleanestScan}
                scanning={cleanestScanning}
                onScan={handleScanCleanest}
                setOpen={() => setCleanestOpen(true)}
              />

              {/* Section breadcrumb — visible on sub-section pages */}
              {section !== '' && KNOWN_SECTIONS.includes(section) && (
                <div style={{ marginBottom:12, display:'flex', alignItems:'center', gap:10 }}>
                  <span style={{ fontSize:11, color:T.cyan, fontWeight:700, letterSpacing:'0.08em' }}>
                    {(SECTION_LABELS[section] ?? section).toUpperCase()}
                  </span>
                  <Link to="/main-brain" style={{ fontSize:10, color:T.txtMuted, textDecoration:'none',
                    borderRadius:4, padding:'2px 8px', background:`${T.border}60`,
                    border:`1px solid ${T.border}`, display:'inline-flex', alignItems:'center', gap:4 }}>
                    ← Overview
                  </Link>
                </div>
              )}

              {/* Section panels */}
              {renderSectionPanels()}
            </>
          )}
        </main>

        {/* Footer */}
        <footer style={{ borderTop:`1px solid ${T.border}`, padding:'8px 20px', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <span style={{ fontSize:9.5, color:T.txtMuted }}>V1 Main Brain Operator Console — read-only display, no backend mutations</span>
          <span style={{ fontSize:9.5, color:T.txtMuted, fontFamily:T.mono }}>Poll: {POLL_INTERVAL_MS / 1000}s</span>
        </footer>
      </div>

      {/* Cleanest Trade modal — rendered outside the scroll container */}
      <CleanestTradeModal
        open={cleanestOpen}
        onClose={() => setCleanestOpen(false)}
        scanResult={cleanestScan}
        activeInstrument={ticker}
        hasActiveTrade={
          Array.isArray(((p.active_trades ?? {}) as Record<string, unknown>).trades) &&
          ((((p.active_trades ?? {}) as Record<string, unknown>).trades) as unknown[]).length > 0
        }
      />

      {/* Responsive styles */}
      <style>{`
        @media (max-width: 1024px) {
          .mb-grid-3 { grid-template-columns: 1fr 1fr !important; }
        }
        @media (max-width: 768px) {
          .mb-grid-3, .mb-grid-2 { grid-template-columns: 1fr !important; }
        }
        @media (prefers-reduced-motion: reduce) {
          * { transition: none !important; animation: none !important; }
        }
        :focus-visible { outline: 2px solid #38bdf8; outline-offset: 2px; }
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 3px; }
      `}</style>
    </div>
  );
}
