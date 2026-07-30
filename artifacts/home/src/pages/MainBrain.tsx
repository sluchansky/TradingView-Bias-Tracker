/**
 * V1 Phase 7C — Main Brain Operator Console
 *
 * Read-only dashboard sourced exclusively from GET /api/main-brain.
 * No backend mutations, no gateway calls, no broker requests.
 * Auth: same Basic Auth pattern as Home.tsx (localStorage brain_auth).
 * Polling: 7 s (reduced when hidden). Manual refresh control included.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useLocation } from 'wouter';

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

  const fetchNow = useCallback(async (reason: 'poll' | 'manual') => {
    if (inFlight.current) return;
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
      lastPayload.current = data;
      setState({ payload: data, fetchState: 'loaded', lastOk: Date.now(), error: null, isAuthFail: false });
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

// ── Nav items ─────────────────────────────────────────────────────────────────
const NAV_ITEMS = [
  { id: 'main-brain', label: 'Main Brain', path: '/main-brain', icon: '⬡', live: true },
  { id: 'analysis',   label: 'Analysis',   path: null, href: '/api/dashboard', icon: '⚡', live: true },
  { id: 'scanner',    label: 'Scanner',    path: null, icon: '◎', live: false },
  { id: 'trades',     label: 'Active Trades', path: null, icon: '↗', live: false },
  { id: 'journal',    label: 'Journal',    path: null, icon: '≡', live: false },
  { id: 'coach',      label: 'Coach',      path: null, icon: '◆', live: false },
  { id: 'alerts',     label: 'Alerts',     path: null, icon: '◉', live: false },
];

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
        const col = isActive ? T.cyan : item.live ? T.txtSec : T.txtMuted;
        const content = (
          <div title={item.label} style={{
            display:'flex', flexDirection:'column', alignItems:'center', gap:3,
            padding:'8px 6px', borderRadius:8, width:46, cursor: item.live ? 'pointer' : 'default',
            background: isActive ? `${T.cyan}14` : 'transparent',
            border: isActive ? `1px solid ${T.cyan}33` : '1px solid transparent',
            opacity: item.live ? 1 : 0.35,
            transition:'all 0.15s',
          }}>
            <span style={{ fontSize:15, lineHeight:1, color:col }}>{item.icon}</span>
            <span style={{ fontSize:7.5, fontWeight:700, letterSpacing:'0.06em', color:col, textAlign:'center', lineHeight:1.2 }}>
              {item.label.toUpperCase()}
            </span>
          </div>
        );
        if (!item.live) {
          return <div key={item.id} aria-disabled="true" style={{ marginBottom:4 }}>{content}</div>;
        }
        if (item.href) {
          return <a key={item.id} href={item.href} target="_blank" rel="noreferrer" style={{ textDecoration:'none', marginBottom:4 }} aria-label={item.label}>{content}</a>;
        }
        return <Link key={item.id} to={item.path!} style={{ textDecoration:'none', marginBottom:4 }} aria-label={item.label}>{content}</Link>;
      })}

      {/* System health dot */}
      <div style={{ marginTop:'auto', paddingTop:16, display:'flex', flexDirection:'column', alignItems:'center', gap:4 }}>
        {statusDot(systemOk)}
        <span style={{ fontSize:7, color:T.txtMuted, letterSpacing:'0.06em' }}>SYS</span>
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

// ── Verdict Panel ─────────────────────────────────────────────────────────────
const VerdictPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const v     = (p.verdict ?? {}) as Record<string, unknown>;
  const avail = v.available !== false;
  const score = safeNum(v.edge_score);
  const grade = safeStr(v.edge_grade, '');
  const ready = safeStr(v.readiness, '');
  const rCol  = readinessColor(ready);
  const failed = Array.isArray(v.failed_conditions) ? v.failed_conditions as string[] : [];
  const comps = v.components as Record<string, number> | null;

  return (
    <Panel title="Verdict" badge={<Badge label={ready || 'UNKNOWN'} color={rCol} />}>
      {!avail ? <UnavailableNote msg="Verdict unavailable" /> : (
        <div style={{ display:'flex', gap:14 }}>
          <EdgeGauge score={score} />
          <div style={{ flex:1 }}>
            <div style={{ display:'flex', gap:8, marginBottom:10, flexWrap:'wrap' }}>
              {grade && <Pill text={`Grade ${grade}`} color={score != null && score >= 70 ? T.green : T.amber} />}
              {v.is_actionable != null && <Pill text={v.is_actionable ? 'ACTIONABLE' : 'NOT ACTIONABLE'} color={v.is_actionable ? T.green : T.red} />}
            </div>

            {/* Component breakdown */}
            {comps && Object.keys(comps).length > 0 && (
              <div>
                <div style={{ fontSize:9, color:T.txtMuted, letterSpacing:'0.08em', marginBottom:5 }}>COMPONENTS</div>
                {Object.entries(comps).slice(0, 7).map(([k, sc]) => (
                  <div key={k} style={{ display:'flex', alignItems:'center', gap:6, marginBottom:3 }}>
                    <span style={{ fontSize:9.5, color:T.txtMuted, minWidth:100, textOverflow:'ellipsis', overflow:'hidden', whiteSpace:'nowrap' }}>{k.replace(/_/g,' ')}</span>
                    <div style={{ flex:1, height:4, background:'rgba(255,255,255,0.06)', borderRadius:2 }}>
                      <div style={{ height:'100%', width:`${Math.min(Number(sc ?? 0) / 20 * 100, 100)}%`, background:T.cyan, borderRadius:2 }} />
                    </div>
                    <span style={{ fontSize:9.5, fontWeight:700, color:T.cyan, fontFamily:T.mono, minWidth:18, textAlign:'right' }}>{sc}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Failed conditions */}
            {failed.length > 0 && (
              <div style={{ marginTop:8 }}>
                <div style={{ fontSize:9, color:T.red, letterSpacing:'0.08em', marginBottom:4 }}>FAILED CONDITIONS</div>
                {failed.map((f, i) => (
                  <div key={i} style={{ fontSize:10, color:T.red, opacity:0.85, marginBottom:2 }}>✗ {safeStr(f)}</div>
                ))}
              </div>
            )}
          </div>
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

const ScannerPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const sc      = (p.strategy_scanner ?? {}) as Record<string, unknown>;
  const avail   = sc.available !== false;
  const sel     = safeStr(sc.selected_strategy, '');
  const strats  = Array.isArray(sc.strategies) ? sc.strategies as Record<string, unknown>[] : [];

  return (
    <Panel title="Strategy Scanner" badge={sel ? <Badge label={STRATEGY_LABELS[sel] ?? sel} color={T.cyan} /> : undefined}>
      {!avail ? <UnavailableNote /> : strats.length === 0 ? <UnavailableNote msg="No strategies available" /> : (
        <div>
          {strats.map((s, i) => {
            const key   = safeStr(s.key, '');
            const name  = STRATEGY_LABELS[key] ?? safeStr(s.name, key);
            const rdy   = safeStr(s.readiness, '');
            const isSel = key === sel;
            const rCol  = readinessColor(rdy);
            const dir   = safeStr(s.direction, '');
            return (
              <div key={key || i} style={{
                display:'flex', alignItems:'center', gap:10, padding:'7px 8px', marginBottom:4,
                background: isSel ? `${T.cyan}10` : 'rgba(255,255,255,0.02)',
                borderRadius:7, border:`1px solid ${isSel ? T.cyan + '33' : T.border}`,
              }}>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                    {isSel && <span style={{ fontSize:9, color:T.cyan }}>▶</span>}
                    <span style={{ fontSize:11, fontWeight:isSel ? 700 : 500, color:isSel ? T.cyan : T.txtPri, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{name}</span>
                  </div>
                  {dir && <span style={{ fontSize:9, color:dirColor(dir), marginLeft:isSel ? 15 : 0 }}>{dir.toUpperCase()}</span>}
                </div>
                <Badge label={rdy || '—'} color={rCol} />
                {s.mode_compatible === false && <Badge label="MODE MISMATCH" color={T.amber} />}
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
};

// ── Trade Plan Panel ──────────────────────────────────────────────────────────
const TradePlanPanel: React.FC<{ p: Record<string, unknown> }> = ({ p }) => {
  const sc   = (p.strategy_scanner ?? {}) as Record<string, unknown>;
  const plan = (sc.trade_plan ?? {}) as Record<string, unknown>;
  const hasEntry = safeNum(plan.entry) != null && safeNum(plan.entry) !== 0;

  return (
    <Panel title="Trade Plan">
      {!hasEntry ? (
        <UnavailableNote msg="No actionable trade plan" />
      ) : (
        <>
          <div style={{ display:'flex', gap:6, marginBottom:10, flexWrap:'wrap' }}>
            {plan.direction != null && <Pill text={String(plan.direction)} color={dirColor(plan.direction)} />}
            {plan.setup     != null && <Badge label={String(plan.setup)} color={T.txtSec} />}
            {plan.status    != null && <Badge label={String(plan.status)} color={readinessColor(plan.status)} />}
          </div>
          <KV label="Entry"    value={fmtNum(plan.entry)} mono valueColor={T.cyan} />
          <KV label="Stop"     value={fmtNum(plan.stop)}  mono valueColor={T.red}  />
          {plan.target_1 != null && <KV label="Target 1" value={fmtNum(plan.target_1)} mono valueColor={T.green} />}
          {plan.target_2 != null && <KV label="Target 2" value={fmtNum(plan.target_2)} mono valueColor={T.green} />}
          {plan.target_3 != null && <KV label="Target 3" value={fmtNum(plan.target_3)} mono valueColor={T.green} />}
          {plan.rr != null && <KV label="Risk/Reward" value={`1 : ${fmtNum(plan.rr, 1)}`} mono />}
          {plan.timeframe && <KV label="Timeframe"  value={String(plan.timeframe)} />}
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

  return (
    <Panel title="Coach" badge={<Badge label="LEARNING" color={T.purple} />}>
      {!avail ? <UnavailableNote /> : (
        <>
          {/* Eligibility / weight status */}
          <div style={{ display:'flex', gap:6, marginBottom:10, flexWrap:'wrap' }}>
            {coach.rule_engine_eligibility != null && (
              <Pill text={coach.rule_engine_eligibility ? 'LRE ELIGIBLE' : 'LRE INELIGIBLE'} color={coach.rule_engine_eligibility ? T.green : T.txtMuted} />
            )}
          </div>

          <KV label="Weight Updated" value={
            <span style={{ display:'flex', alignItems:'center', gap:5 }}>
              {statusDot(coach.weight_updated ? true : null)}
              <span>{fmtTs(coach.weight_updated)}</span>
            </span>
          } />
          <KV label="Thesis Resolved" value={
            <span title="Whether the last thesis was resolved — not a measure of learning readiness">
              {coach.thesis_resolved ? 'YES' : 'NO'}
            </span>
          } />
          <KV label="Last Resolved" value={fmtTs(coach.thesis_last_resolved_at)} />
          {coach.learning_influence != null && <KV label="Learning Influence" value={String(coach.learning_influence)} />}

          {/* Performance summary */}
          {Object.keys(perf).length > 0 && (
            <div style={{ marginTop:12, borderTop:`1px solid ${T.border}`, paddingTop:10 }}>
              <div style={{ fontSize:9, color:T.purple, letterSpacing:'0.1em', marginBottom:6 }}>PERFORMANCE</div>
              {perf.win_rate  != null && <KV label="Win Rate"   value={`${fmtNum(perf.win_rate, 0)}%`} mono />}
              {perf.avg_r     != null && <KV label="Avg R"      value={fmtNum(perf.avg_r)}              mono />}
              {perf.trade_count != null && <KV label="Sample"   value={String(perf.trade_count)}        mono />}
              {perf.best_setup != null && <KV label="Best Setup" value={String(perf.best_setup)} />}
            </div>
          )}

          <div style={{ marginTop:8, fontSize:9.5, color:T.txtMuted, lineHeight:1.5 }}>
            <span style={{ color:T.amber }}>ℹ</span> Eligibility ≠ update occurred. Weight updated ≠ readiness. DB available ≠ thesis resolved.
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
    { label: 'Left Brain', ok: avail.left_brain !== false },
    { label: 'Scanner',    ok: avail.strategy_scanner !== false },
    { label: 'Coach',      ok: avail.coach !== false },
    { label: 'Journal',    ok: avail.journal !== false },
    { label: 'Timeline',   ok: avail.decision_timeline !== false },
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
  const [ticker, setTicker] = useState<string>(() => {
    try { return localStorage.getItem('mb_ticker') || 'MGC'; } catch { return 'MGC'; }
  });
  const handleSetTicker = (t: string) => {
    setTicker(t); try { localStorage.setItem('mb_ticker', t); } catch {}
  };

  const { payload, fetchState, lastOk, error, isAuthFail, refresh } = useMainBrain(ticker);

  const p = (payload ?? {}) as Record<string, unknown>;
  const sys = (p.system_status ?? {}) as Record<string, unknown>;
  const allOk = !!(sys.db_ready && sys.learning_ready);
  const isLoading = fetchState === 'loading' && !payload;
  const isError   = (fetchState === 'error' || isAuthFail) && !payload;

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

              {/* Row 1: Thesis · Verdict · Scanner */}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10, marginBottom:10 }} className="mb-grid-3">
                <ThesisPanel p={p} />
                <VerdictPanel p={p} />
                <ScannerPanel p={p} />
              </div>

              {/* Row 2: Trade Plan · Active Trades · Execution */}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10, marginBottom:10 }} className="mb-grid-3">
                <TradePlanPanel p={p} />
                <ActiveTradesPanel p={p} />
                <ExecutionPanel p={p} />
              </div>

              {/* Row 3: Coach · Journal */}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginBottom:10 }} className="mb-grid-2">
                <CoachPanel p={p} />
                <JournalPanel p={p} />
              </div>

              {/* Row 4: Timeline · Alerts · System Health */}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10 }} className="mb-grid-3">
                <TimelinePanel p={p} />
                <AlertsPanel p={p} />
                <SystemHealthPanel p={p} />
              </div>
            </>
          )}
        </main>

        {/* Footer */}
        <footer style={{ borderTop:`1px solid ${T.border}`, padding:'8px 20px', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <span style={{ fontSize:9.5, color:T.txtMuted }}>V1 Main Brain Operator Console — read-only display, no backend mutations</span>
          <span style={{ fontSize:9.5, color:T.txtMuted, fontFamily:T.mono }}>Poll: {POLL_INTERVAL_MS / 1000}s</span>
        </footer>
      </div>

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
