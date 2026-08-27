/**
 * GlobalAlertDock — single global alert authority for all pages.
 *
 * Monitors ALL FOUR instruments (MGC, MNQ, MES, MYM) simultaneously.
 * Builds a persistent, acknowledged-until-cleared alert queue backed by
 * localStorage so alerts survive navigation and page refresh.
 *
 * Alert types:
 *  • LIVE_READY             — production engine reaches READY for any instrument
 *  • ORB_SHADOW_QUALIFIED   — 09:30 ORB shadow engine qualifies a breakout
 *  • ORB_BREAKOUT_MISSED    — ORB window closed without a valid breakout
 *
 * SAFETY: UI / display only. No trading logic, no broker calls.
 * Mount once inside WouterRouter in App.tsx — covers every page.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation } from 'wouter';
import { audioManager, SoundEvent } from '../lib/audioManager';
import { normalizeMainBrainPayload } from '../lib/mainBrainNormalizer';
import {
  AlertItem, AlertType,
  loadQueue, saveQueue, upsertAlert, ackAlert, clearAcknowledged,
  setActiveTicker,
} from '../lib/globalAlerts';
import {
  DASHBOARD_AUTH_EVENT,
  announceDashboardAuth,
  type DashboardAuthDetail,
} from '../lib/dashboardAuth';

// ── Constants ─────────────────────────────────────────────────────────────────

const INSTRUMENTS = ['MGC', 'MNQ', 'MES', 'MYM'] as const;
const LIVE_POLL_MS          = 5_000;
const ORB_POLL_MS           = 10_000;
const RESEARCH_POLL_MS      = 30_000;   // Phase 2 ghost research — polls less aggressively
const SYSTEM_HEALTH_POLL_MS = 60_000;   // System health — low-frequency background check

const C = {
  green:   '#30d158',
  amber:   '#f59e0b',
  blue:    '#60a5fa',
  red:     '#ef4444',
  orb:     '#a78bfa',   // purple — shadow only
  bg:      'rgba(10,17,26,0.97)',
  border:  'rgba(255,255,255,0.07)',
  txtPri:  '#e2e8f0',
  txtSec:  '#8e98a4',
  txtMut:  '#4a5260',
  surface: 'rgba(20,30,45,0.98)',
};

function typeColor(type: AlertType): string {
  if (type === 'LIVE_READY')                 return C.green;
  if (type === 'ORB_SHADOW_QUALIFIED')       return C.orb;
  if (type === 'ORB_BREAKOUT_MISSED')        return C.txtSec;
  if (type === 'RESEARCH_READY_FOR_REVIEW')  return C.blue;
  return C.amber; // SYSTEM_SAFETY
}

function typeBadge(type: AlertType, isShadow: boolean): string {
  if (type === 'LIVE_READY')                 return isShadow ? 'SHADOW READY' : 'READY TO TRADE';
  if (type === 'ORB_SHADOW_QUALIFIED')       return 'ORB  SHADOW  QUALIFIED';
  if (type === 'ORB_BREAKOUT_MISSED')        return 'ORB MISSED';
  if (type === 'RESEARCH_READY_FOR_REVIEW')  return 'RESEARCH  READY FOR REVIEW';
  return 'SYSTEM';
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function ss(v: unknown, fb = ''): string { return typeof v === 'string' ? v : fb; }
function sn(v: unknown): number | null   { const n = Number(v); return Number.isFinite(n) ? n : null; }

function authHeaders(authorization: string): Record<string, string> {
  return authorization ? { Authorization: authorization } : {};
}

function fmtTime(ts: number): string {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return '--:--'; }
}

function fmtPrice(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v % 1 === 0 ? v.toFixed(0) : v.toFixed(2);
}

function injectKeyframes() {
  if (typeof document === 'undefined') return;
  if (document.getElementById('gad-kf')) return;
  const s = document.createElement('style');
  s.id = 'gad-kf';
  s.textContent = `
    @keyframes gadIn  { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
    @keyframes gadPop { from{opacity:0;transform:translateY(16px) scale(.96)} to{opacity:1;transform:translateY(0) scale(1)} }
    @keyframes gadPulse {
      0%,100%{transform:scale(1);opacity:1}
      50%{transform:scale(1.85);opacity:.35}
    }
  `;
  document.head.appendChild(s);
}

// ── Live READY poller for one instrument ──────────────────────────────────────

async function fetchLiveReady(inst: string, authorization: string, signal: AbortSignal): Promise<{
  isActionable: boolean;
  id: string;
  alert: Partial<AlertItem>;
}> {
  const res = await fetch(`/api/main-brain?ticker=${inst}`, {
    credentials: 'include', headers: authHeaders(authorization), signal,
  });
  if (res.status === 401 || res.status === 403) announceDashboardAuth(false);
  if (!res.ok) return { isActionable: false, id: '', alert: {} };

  const raw = await res.json();
  const p   = normalizeMainBrainPayload(raw);

  const verdict = (p.verdict           ?? {}) as Record<string, unknown>;
  const market  = (p.market            ?? {}) as Record<string, unknown>;
  const cp      = (p.candidate_preview ?? {}) as Record<string, unknown>;
  const eb      = (p.edge_breakdown    ?? {}) as Record<string, unknown>;
  const sc      = (p.strategy_scanner  ?? {}) as Record<string, unknown>;
  const tp      = (p.trade_plan        ?? {}) as Record<string, unknown>;

  const isActionable = verdict.is_actionable === true;
  if (!isActionable) return { isActionable: false, id: '', alert: {} };

  const direction = ss(cp.direction ?? verdict.direction, '');
  const strategy  = ss(sc.selected_strategy, '');
  const edgeScore = sn(eb.score ?? eb.total_score) ?? undefined;
  const id = `LIVE|${inst}|${direction}|${strategy}`;

  const parts = (
    [direction, edgeScore != null ? `Edge ${edgeScore}` : null, strategy || null] as (string|null)[]
  ).filter(Boolean);

  // Build a short confirmation summary from edge breakdown components if present
  const comps = (eb.components ?? []) as Array<Record<string, unknown>>;
  const confirmLabels = comps
    .filter(c => Number(c.score ?? 0) > 0)
    .map(c => ss(c.name ?? c.label, ''))
    .filter(Boolean)
    .slice(0, 4)
    .join(' · ');

  return {
    isActionable: true,
    id,
    alert: {
      id,
      type:          'LIVE_READY',
      instrument:    ss(market.instrument, inst),
      direction,
      strategy,
      edgeScore,
      isShadow:      false,
      label:         `${inst}  READY TO TRADE`,
      sublabel:      parts.join('  ·  '),
      entry:         sn(tp.entry),
      stop:          sn(tp.stop),
      tp1:           sn(tp.target1 ?? tp.tp1),
      tp2:           sn(tp.target2 ?? tp.tp2),
      confirmations: confirmLabels,
    },
  };
}

// ── System health poller ──────────────────────────────────────────────────────

interface SystemHealthResponse {
  ok?: boolean;
  ready_for_market?: boolean;
  error_count?: number;
  ghost_engine?: { table_ready?: boolean };
  edge_ledger?: { table_ready?: boolean };
}

async function fetchSystemHealth(headers: Record<string, string>, signal: AbortSignal): Promise<SystemHealthResponse | null> {
  try {
    const res = await fetch('/api/research-health', { credentials: 'include', headers, signal });
    if (res.status === 401 || res.status === 403) announceDashboardAuth(false);
    if (!res.ok) return null;
    return await res.json() as SystemHealthResponse;
  } catch { return null; }
}

// ── ORB shadow status poller ──────────────────────────────────────────────────

interface OrbInstStatus {
  state: string;
  breakout_direction?: string;
  or_high?: number;
  or_low?: number;
  long_breakout_level?: number;
  short_breakout_level?: number;
  entry?: number;
  stop?: number;
  tp1?: number;
  tp2?: number;
  contracts?: number;
  confirmation_mode?: string;
  trading_date?: string;
  block_reason?: string;
  last_update?: string;
}

interface OrbStatusResponse {
  ok: boolean;
  enabled?: boolean;
  global_mode?: string;
  instruments?: Record<string, OrbInstStatus>;
}

async function fetchOrbStatus(authorization: string, signal: AbortSignal): Promise<OrbStatusResponse | null> {
  try {
    const res = await fetch('/api/orb/status', {
      credentials: 'include', headers: authHeaders(authorization), signal,
    });
    if (res.status === 401 || res.status === 403) announceDashboardAuth(false);
    if (!res.ok) return null;
    return await res.json() as OrbStatusResponse;
  } catch { return null; }
}

// ── Alert card component ──────────────────────────────────────────────────────

const AlertCard: React.FC<{
  alert: AlertItem;
  expanded: boolean;
  onExpand: () => void;
  onAck: () => void;
  onClick: () => void;
}> = ({ alert, expanded, onExpand, onAck, onClick }) => {
  const color = typeColor(alert.type);

  return (
    <div
      style={{
        borderRadius: 10,
        border: `1px solid ${alert.acknowledged ? C.border : color + '40'}`,
        background: alert.acknowledged ? 'rgba(15,22,34,0.7)' : 'rgba(15,22,34,0.95)',
        marginBottom: 6,
        overflow: 'hidden',
        opacity: alert.acknowledged ? 0.55 : 1,
        transition: 'opacity 0.2s',
      }}
    >
      {/* Header row */}
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '8px 10px',
          cursor: 'pointer',
        }}
        onClick={onClick}
      >
        {/* Pulse dot */}
        {!alert.acknowledged && (
          <span style={{ position:'relative', flexShrink:0, width:7, height:7 }}>
            <span style={{
              position:'absolute', inset:0,
              background: color, borderRadius:'50%',
              animation: 'gadPulse 1.9s ease-in-out infinite',
            }} />
          </span>
        )}
        {alert.acknowledged && (
          <span style={{ width:7, height:7, borderRadius:'50%', background:C.txtMut, flexShrink:0 }} />
        )}

        {/* Instrument pill */}
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
          background: color + '20', color,
          borderRadius: 4, padding: '1px 5px', flexShrink:0,
        }}>
          {alert.instrument}
        </span>

        {/* Shadow badge */}
        {alert.isShadow && (
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: '0.1em',
            background: 'rgba(167,139,250,0.12)', color: C.orb,
            borderRadius: 4, padding: '1px 5px', flexShrink:0,
          }}>
            SHADOW
          </span>
        )}

        {/* Label */}
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{
            fontSize: 11, fontWeight: 700, color,
            letterSpacing: '0.06em',
            overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
          }}>
            {typeBadge(alert.type, alert.isShadow)}
          </div>
          {alert.sublabel && (
            <div style={{
              fontSize: 10, color: C.txtSec, marginTop:1,
              overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
            }}>
              {alert.sublabel}
            </div>
          )}
        </div>

        {/* Time */}
        <span style={{ fontSize:10, color:C.txtMut, flexShrink:0 }}>
          {fmtTime(alert.timestamp)}
        </span>

        {/* Expand toggle */}
        <button
          onClick={e => { e.stopPropagation(); onExpand(); }}
          style={{
            background:'none', border:'none', padding:'2px 4px',
            cursor:'pointer', color:C.txtSec, fontSize:11, lineHeight:1,
          }}
          title={expanded ? 'Collapse' : 'Details'}
        >
          {expanded ? '▲' : '▼'}
        </button>

        {/* Ack button */}
        {!alert.acknowledged && (
          <button
            onClick={e => { e.stopPropagation(); onAck(); }}
            style={{
              background:'none', border:`1px solid ${C.txtMut}`,
              padding:'1px 6px', cursor:'pointer',
              color:C.txtSec, fontSize:10, lineHeight:1.4,
              borderRadius:4, flexShrink:0,
            }}
            title="Acknowledge"
          >
            ✓
          </button>
        )}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{
          padding:'0 10px 10px',
          borderTop:`1px solid ${C.border}`,
          animation:'gadIn 0.15s ease forwards',
        }}>
          {alert.type === 'LIVE_READY' && (
            <LiveReadyDetail alert={alert} color={color} />
          )}
          {(alert.type === 'ORB_SHADOW_QUALIFIED' || alert.type === 'ORB_BREAKOUT_MISSED') && (
            <OrbDetail alert={alert} color={color} />
          )}
          {alert.type === 'RESEARCH_READY_FOR_REVIEW' && (
            <ResearchDetail alert={alert} color={color} />
          )}
          {alert.type === 'SYSTEM_SAFETY' && (
            <SystemSafetyDetail alert={alert} color={color} />
          )}
        </div>
      )}
    </div>
  );
};

const Row: React.FC<{ label: string; value: string | number | null | undefined }> = ({ label, value }) => (
  value != null && value !== '' && value !== '—' ? (
    <div style={{ display:'flex', justifyContent:'space-between', marginTop:4 }}>
      <span style={{ fontSize:10, color:C.txtMut }}>{label}</span>
      <span style={{ fontSize:10, color:C.txtSec }}>{value}</span>
    </div>
  ) : null
);

const LiveReadyDetail: React.FC<{ alert: AlertItem; color: string }> = ({ alert, color }) => (
  <div style={{ paddingTop:8 }}>
    {alert.direction && (
      <div style={{ fontSize:11, fontWeight:700, color, marginBottom:4 }}>
        {alert.direction.toUpperCase()}
        {alert.strategy ? `  ·  ${alert.strategy}` : ''}
        {alert.edgeScore != null ? `  ·  Edge ${alert.edgeScore}` : ''}
      </div>
    )}
    <Row label="Entry"  value={fmtPrice(alert.entry)} />
    <Row label="Stop"   value={fmtPrice(alert.stop)} />
    <Row label="TP 1"   value={fmtPrice(alert.tp1)} />
    <Row label="TP 2"   value={fmtPrice(alert.tp2)} />
    {alert.confirmations && (
      <Row label="Confirms" value={alert.confirmations} />
    )}
    <div style={{ marginTop:6, fontSize:9, color:C.txtMut, fontStyle:'italic' }}>
      LIVE — production engine
    </div>
  </div>
);

const ResearchDetail: React.FC<{ alert: AlertItem; color: string }> = ({ alert, color }) => (
  <div style={{ paddingTop: 8 }}>
    <div style={{
      fontSize: 10, fontWeight: 700, letterSpacing: '0.1em',
      color: C.blue, marginBottom: 6,
      padding: '2px 6px', background: 'rgba(96,165,250,0.10)',
      borderRadius: 4, display: 'inline-block',
    }}>
      ⬡ RESEARCH — NOT LIVE — REQUIRES HUMAN REVIEW
    </div>
    {alert.direction && (
      <div style={{ fontSize: 11, fontWeight: 700, color, marginBottom: 4 }}>
        {alert.direction.toUpperCase()}
        {alert.strategy ? `  ·  ${alert.strategy}` : ''}
      </div>
    )}
    <Row label="Variant"      value={alert.sublabel} />
    <Row label="Instrument"   value={alert.instrument} />
    <div style={{ marginTop: 6, fontSize: 9, color: C.txtMut, fontStyle: 'italic' }}>
      Phase 2 Ghost Research Engine — shadow experiment reached READY_FOR_REVIEW.<br />
      No live config was changed. Review findings before any action.
    </div>
  </div>
);

const SystemSafetyDetail: React.FC<{ alert: AlertItem; color: string }> = ({ alert, color }) => (
  <div style={{ paddingTop: 8 }}>
    <div style={{ fontSize: 11, fontWeight: 700, color, marginBottom: 4 }}>
      {alert.label}
    </div>
    {alert.sublabel && (
      <div style={{ fontSize: 10, color: C.txtSec, marginBottom: 6 }}>
        {alert.sublabel}
      </div>
    )}
    <div style={{ fontSize: 9, color: C.txtMut, fontStyle: 'italic' }}>
      System event — no trading action required.
    </div>
  </div>
);

const OrbDetail: React.FC<{ alert: AlertItem; color: string }> = ({ alert, color }) => {
  const dir = alert.direction ?? alert.orbState ?? '';
  const level = dir.toLowerCase().includes('long')
    ? alert.orbBreakoutLevel ?? alert.orbRangeHigh
    : alert.orbBreakoutLevel ?? alert.orbRangeLow;

  return (
    <div style={{ paddingTop:8 }}>
      <div style={{
        fontSize: 10, fontWeight:700, letterSpacing:'0.1em',
        color: C.orb, marginBottom:6,
        padding:'2px 6px', background:'rgba(167,139,250,0.10)',
        borderRadius:4, display:'inline-block',
      }}>
        ⬡ SHADOW MODE — NO ORDER TRANSMITTED
      </div>
      {dir && (
        <div style={{ fontSize:11, fontWeight:700, color, marginBottom:4 }}>
          {dir.toUpperCase()}
        </div>
      )}
      <Row label="ORB State"        value={alert.orbState} />
      <Row label="Confirm mode"     value={alert.orbConfirmMode} />
      <Row label="Range high"       value={fmtPrice(alert.orbRangeHigh)} />
      <Row label="Range low"        value={fmtPrice(alert.orbRangeLow)} />
      <Row label="Breakout level"   value={fmtPrice(level)} />
      <Row label="Planned entry"    value={fmtPrice(alert.orbEntry)} />
      <Row label="Planned stop"     value={fmtPrice(alert.orbStop)} />
      <Row label="TP 1"             value={fmtPrice(alert.orbTp1)} />
      <Row label="TP 2"             value={fmtPrice(alert.orbTp2)} />
      <Row label="Contracts"        value={alert.orbContracts ?? undefined} />
      <Row label="Trading date"     value={alert.orbTradingDate} />
      {alert.orbBlockReason && (
        <Row label="Block reason" value={alert.orbBlockReason} />
      )}
      <div style={{ marginTop:6, fontSize:9, color:C.txtMut, fontStyle:'italic' }}>
        SHADOW — 09:30 ORB engine — zero broker calls
      </div>
    </div>
  );
};

// ── Drawer ────────────────────────────────────────────────────────────────────

const Drawer: React.FC<{
  queue: AlertItem[];
  onAck: (id: string) => void;
  onClearAcked: () => void;
  onClickAlert: (alert: AlertItem) => void;
  onClose: () => void;
  isMobile: boolean;
}> = ({ queue, onAck, onClearAcked, onClickAlert, onClose, isMobile }) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const hasAcked = queue.some(a => a.acknowledged);

  return (
    <div style={{
      position:'fixed',
      ...(isMobile
        ? { bottom: 52, left:0, right:0, margin:'0 8px' }
        : { bottom: 60, left:'50%', transform:'translateX(-50%)', width: 420 }
      ),
      zIndex: 9998,
      background: C.surface,
      border: `1px solid ${C.border}`,
      borderRadius: 12,
      boxShadow: '0 8px 40px rgba(0,0,0,0.7)',
      backdropFilter: 'blur(20px)',
      animation: 'gadPop 0.2s ease forwards',
      maxHeight: '65vh',
      display: 'flex',
      flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{
        display:'flex', alignItems:'center', justifyContent:'space-between',
        padding:'10px 12px 8px',
        borderBottom:`1px solid ${C.border}`,
        flexShrink:0,
      }}>
        <span style={{ fontSize:11, fontWeight:700, color:C.txtPri, letterSpacing:'0.06em' }}>
          ALERT HISTORY
        </span>
        <div style={{ display:'flex', gap:6, alignItems:'center' }}>
          {hasAcked && (
            <button
              onClick={onClearAcked}
              style={{
                background:'none', border:`1px solid ${C.txtMut}`,
                padding:'2px 8px', borderRadius:4, cursor:'pointer',
                color:C.txtSec, fontSize:10,
              }}
            >
              Clear Acknowledged
            </button>
          )}
          <button
            onClick={onClose}
            style={{
              background:'none', border:'none', padding:'2px 6px',
              cursor:'pointer', color:C.txtSec, fontSize:14, lineHeight:1,
            }}
            aria-label="Close"
          >
            ×
          </button>
        </div>
      </div>

      {/* List */}
      <div style={{ flex:1, overflowY:'auto', padding:'8px 10px' }}>
        {queue.length === 0 && (
          <div style={{ textAlign:'center', color:C.txtMut, fontSize:11, padding:'24px 0' }}>
            No alerts yet. You will be notified here when any instrument becomes ready.
          </div>
        )}
        {queue.map(alert => (
          <AlertCard
            key={alert.id}
            alert={alert}
            expanded={expandedId === alert.id}
            onExpand={() => setExpandedId(prev => prev === alert.id ? null : alert.id)}
            onAck={() => onAck(alert.id)}
            onClick={() => onClickAlert(alert)}
          />
        ))}
      </div>
    </div>
  );
};

// ── Compact dock pill ─────────────────────────────────────────────────────────

const CompactDock: React.FC<{
  queue: AlertItem[];
  drawerOpen: boolean;
  onToggle: () => void;
  isMobile: boolean;
}> = ({ queue, drawerOpen, onToggle, isMobile }) => {
  const unacked = queue.filter(a => !a.acknowledged);
  const latest  = unacked[0] ?? queue[0];
  const count   = unacked.length;
  const color   = latest ? typeColor(latest.type) : C.txtSec;

  if (isMobile) {
    return (
      <div
        role="button"
        onClick={onToggle}
        style={{
          position:'fixed', bottom:0, left:0, right:0,
          zIndex: 9999,
          background: C.bg,
          borderTop: `1px solid ${latest ? color+'50' : C.border}`,
          padding:'8px 14px',
          display:'flex', alignItems:'center', gap:10,
          cursor:'pointer', userSelect:'none',
        }}
      >
        {/* Count badge */}
        <div style={{
          background: count > 0 ? color : C.txtMut,
          color:'#000', borderRadius:99, fontSize:11, fontWeight:800,
          padding:'1px 7px', flexShrink:0, minWidth:22, textAlign:'center',
          transition:'background 0.3s',
        }}>
          {count > 0 ? count : '—'}
        </div>

        {latest ? (
          <div style={{ flex:1, minWidth:0 }}>
            <div style={{
              fontSize:11, fontWeight:700, color,
              overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
              letterSpacing:'0.05em',
            }}>
              {latest.instrument}  {typeBadge(latest.type, latest.isShadow)}
            </div>
            {latest.sublabel && (
              <div style={{
                fontSize:10, color:C.txtSec,
                overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
              }}>
                {latest.sublabel}
              </div>
            )}
          </div>
        ) : (
          <div style={{ flex:1, fontSize:11, color:C.txtMut }}>
            No active alerts
          </div>
        )}

        <span style={{ fontSize:11, color:C.txtSec, flexShrink:0 }}>
          {drawerOpen ? '▼' : '▲'}
        </span>
      </div>
    );
  }

  // Desktop pill
  return (
    <div
      role="button"
      onClick={onToggle}
      style={{
        position:'fixed', bottom:18, left:'50%', transform:'translateX(-50%)',
        zIndex:9999,
        background: C.bg,
        border:`1px solid ${latest ? color+'50' : C.border}`,
        borderRadius:999,
        padding:'7px 14px 7px 10px',
        display:'flex', alignItems:'center', gap:9,
        cursor:'pointer', userSelect:'none',
        boxShadow:`0 4px 24px rgba(0,0,0,0.55)${latest ? `, 0 0 0 1px ${color}18` : ''}`,
        backdropFilter:'blur(18px)',
        maxWidth:440,
        animation:'gadIn 0.2s ease forwards',
        transition:'border-color 0.3s, box-shadow 0.3s',
      }}
    >
      {/* Bell + count badge */}
      <div style={{ position:'relative', flexShrink:0 }}>
        <span style={{ fontSize:14, lineHeight:1 }}>🔔</span>
        {count > 0 && (
          <span style={{
            position:'absolute', top:-5, right:-7,
            background: color, color:'#000',
            fontSize:9, fontWeight:800, borderRadius:99,
            padding:'0 4px', minWidth:14, textAlign:'center',
          }}>
            {count}
          </span>
        )}
      </div>

      {/* Pulse dot (only when unacked) */}
      {count > 0 && (
        <span style={{ position:'relative', flexShrink:0, width:7, height:7 }}>
          <span style={{
            position:'absolute', inset:0,
            background:color, borderRadius:'50%',
            animation:'gadPulse 1.9s ease-in-out infinite',
          }} />
        </span>
      )}

      {/* Content */}
      <div style={{ flex:1, minWidth:0 }}>
        {latest ? (
          <>
            <div style={{
              fontSize:11, fontWeight:700, color,
              letterSpacing:'0.06em',
              overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
            }}>
              {latest.instrument}  {typeBadge(latest.type, latest.isShadow)}
            </div>
            {latest.sublabel && (
              <div style={{
                fontSize:10, color:C.txtSec, marginTop:1,
                overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
              }}>
                {latest.sublabel}
              </div>
            )}
          </>
        ) : (
          <div style={{ fontSize:11, color:C.txtMut }}>No active alerts</div>
        )}
      </div>

      <span style={{ fontSize:10, color:C.txtSec, flexShrink:0 }}>
        {drawerOpen ? '▲' : '▼'}
      </span>
    </div>
  );
};

// ── Main export ───────────────────────────────────────────────────────────────

export const GlobalAlertDock: React.FC = () => {
  const [, navigate] = useLocation();
  const [queue, setQueueState] = useState<AlertItem[]>(() => loadQueue());
  const [drawerOpen, setDrawerOpen]  = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isMobile, setIsMobile]      = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth < 768 : false
  );

  // The dock is app-global, while protected pages own their auth state.
  // Keep a ref alongside state so in-flight polls can stop publishing work
  // immediately when the page reports a logout or expired credential.
  const authenticatedRef = useRef(false);
  const authorizationRef = useRef('');
  const authGenerationRef = useRef(0);
  const authAbortRef = useRef<AbortController | null>(null);

  // In-memory dedup refs (session-only; queue persists across sessions)
  const seenLiveRef         = useRef<Partial<Record<string, string>>>({});  // inst → last READY id
  const seenOrbRef          = useRef<Partial<Record<string, string>>>({});  // inst → last ORB id
  const seenResearchRef     = useRef<Set<string>>(new Set());               // experiment_id → seen
  const soundedRef          = useRef<Set<string>>(new Set());
  // Connection health tracking
  const netOkRef            = useRef<boolean | null>(null);  // null=unknown, true=up, false=down
  const consecutiveFailRef  = useRef<number>(0);
  // System health dedup — keyed by warning text to avoid repeat alerts
  const seenSysWarnRef      = useRef<Set<string>>(new Set());

  useEffect(() => { injectKeyframes(); }, []);

  useEffect(() => {
    const handleAuthChange = (event: Event) => {
      const detail = (event as CustomEvent<DashboardAuthDetail>).detail;
      const authorization = typeof detail?.authorization === 'string' ? detail.authorization : '';
      const authenticated = detail?.authenticated === true && Boolean(authorization);

      if (
        authenticatedRef.current === authenticated
        && authorizationRef.current === (authenticated ? authorization : '')
      ) return;

      authAbortRef.current?.abort();
      authAbortRef.current = authenticated ? new AbortController() : null;
      authenticatedRef.current = authenticated;
      authorizationRef.current = authenticated ? authorization : '';
      authGenerationRef.current += 1;
      setIsAuthenticated(authenticated);

      if (!authenticated) {
        // Do not turn an expected auth transition into a misleading
        // "Feed disconnected" alert when polling resumes later.
        netOkRef.current = null;
        consecutiveFailRef.current = 0;
      }
    };

    window.addEventListener(DASHBOARD_AUTH_EVENT, handleAuthChange);
    return () => {
      authAbortRef.current?.abort();
      window.removeEventListener(DASHBOARD_AUTH_EVENT, handleAuthChange);
    };
  }, []);

  // Responsive
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  // ── Queue mutation helpers ──────────────────────────────────────────────────

  const commitQueue = useCallback((updater: (prev: AlertItem[]) => AlertItem[]) => {
    setQueueState(prev => {
      const next = updater(prev);
      saveQueue(next);
      return next;
    });
  }, []);

  const handleAck = useCallback((id: string) => {
    commitQueue(q => ackAlert(q, id));
  }, [commitQueue]);

  const handleClearAcked = useCallback(() => {
    commitQueue(clearAcknowledged);
  }, [commitQueue]);

  const pushAlert = useCallback((item: AlertItem) => {
    commitQueue(q => upsertAlert(q, item));
    // Play sound once per unique alert id
    if (!soundedRef.current.has(item.id)) {
      soundedRef.current.add(item.id);
      if (item.type === 'LIVE_READY') {
        audioManager.play(SoundEvent.READY_TO_TRADE);
      } else if (item.type === 'ORB_SHADOW_QUALIFIED') {
        audioManager.play(SoundEvent.SCAN_FOUND);   // distinct but restrained
      } else if (item.type === 'RESEARCH_READY_FOR_REVIEW') {
        audioManager.play(SoundEvent.SCAN_FOUND);   // soft notification — research finding
      }
    }
  }, [commitQueue]);

  // ── LIVE_READY polling (all 4 instruments) ──────────────────────────────────

  const pollLive = useCallback(async () => {
    if (!authenticatedRef.current || document.hidden) return;
    const authGeneration = authGenerationRef.current;
    const authorization = authorizationRef.current;
    const signal = authAbortRef.current?.signal;
    if (!signal) return;
    let networkOkCount = 0;  // instruments that returned any HTTP response (didn't throw)

    await Promise.allSettled(
      INSTRUMENTS.map(async (inst) => {
        try {
          const { isActionable, id, alert } = await fetchLiveReady(inst, authorization, signal);
          if (!authenticatedRef.current || authGeneration !== authGenerationRef.current) return;
          networkOkCount++;  // fetchLiveReady only returns (never throws) on HTTP response
          if (!isActionable) {
            // Reset dedup so next READY fires a fresh alert
            seenLiveRef.current[inst] = '';
            return;
          }
          if (id && id !== seenLiveRef.current[inst]) {
            seenLiveRef.current[inst] = id;
            pushAlert({
              ...alert,
              id,
              type:        'LIVE_READY',
              instrument:  inst,
              timestamp:   Date.now(),
              acknowledged: false,
              isShadow:    false,
            } as AlertItem);
          }
        } catch { /* genuine network error — don't increment networkOkCount */ }
      })
    );
    if (!authenticatedRef.current || authGeneration !== authGenerationRef.current) return;

    // ── Connection state machine ──────────────────────────────────────────────
    const allFailed = networkOkCount === 0;
    if (allFailed) {
      consecutiveFailRef.current++;
      // Wait for 2 consecutive all-fail polls (~10 s) before declaring offline
      // to avoid false alarms from a single slow poll.
      if (consecutiveFailRef.current >= 2 && netOkRef.current !== false) {
        netOkRef.current = false;
        pushAlert({
          id:           `SYSTEM_OFFLINE|${Date.now()}`,
          type:         'SYSTEM_SAFETY',
          instrument:   'SYSTEM',
          timestamp:    Date.now(),
          acknowledged: false,
          isShadow:     false,
          label:        'Feed disconnected',
          sublabel:     'Retrying automatically…',
        });
      }
    } else {
      consecutiveFailRef.current = 0;
      if (netOkRef.current === false) {
        // Was offline — now back up
        netOkRef.current = true;
        pushAlert({
          id:           `SYSTEM_ONLINE|${Date.now()}`,
          type:         'SYSTEM_SAFETY',
          instrument:   'SYSTEM',
          timestamp:    Date.now(),
          acknowledged: false,
          isShadow:     false,
          label:        'System connected',
          sublabel:     'Feed restored',
        });
      } else if (netOkRef.current === null) {
        netOkRef.current = true;  // initial state — connected, no alert needed
      }
    }
  }, [pushAlert]);

  // ── ORB shadow polling ──────────────────────────────────────────────────────

  const pollOrb = useCallback(async () => {
    if (!authenticatedRef.current || document.hidden) return;
    const authGeneration = authGenerationRef.current;
    const authorization = authorizationRef.current;
    const signal = authAbortRef.current?.signal;
    if (!signal) return;
    try {
      const data = await fetchOrbStatus(authorization, signal);
      if (!authenticatedRef.current || authGeneration !== authGenerationRef.current) return;
      if (!data?.ok || !data.instruments) return;

      for (const inst of INSTRUMENTS) {
        const s = data.instruments[inst];
        if (!s) continue;

        const state = s.state ?? '';

        if (state === 'QUALIFIED') {
          // ID includes trading_date so one alert per instrument per day
          const date = s.trading_date ?? 'unknown';
          const dir  = s.breakout_direction ?? '';
          const id   = `ORB|SHADOW|${inst}|QUALIFIED|${date}`;

          if (id !== seenOrbRef.current[inst]) {
            seenOrbRef.current[inst] = id;

            const breakoutLevel = dir.toLowerCase().includes('long')
              ? (s.long_breakout_level ?? s.or_high ?? null)
              : (s.short_breakout_level ?? s.or_low ?? null);

            const parts = [dir, `Range ${fmtPrice(s.or_high)}–${fmtPrice(s.or_low)}`].filter(Boolean);

            pushAlert({
              id,
              type:           'ORB_SHADOW_QUALIFIED',
              instrument:     inst,
              direction:      dir,
              timestamp:      Date.now(),
              acknowledged:   false,
              isShadow:       true,
              label:          `${inst}  09:30 ORB  SHADOW QUALIFIED`,
              sublabel:       parts.join('  ·  '),
              orbState:       state,
              orbRangeHigh:   s.or_high ?? null,
              orbRangeLow:    s.or_low  ?? null,
              orbBreakoutLevel: breakoutLevel,
              orbEntry:       s.entry   ?? null,
              orbStop:        s.stop    ?? null,
              orbTp1:         s.tp1     ?? null,
              orbTp2:         s.tp2     ?? null,
              orbContracts:   s.contracts ?? null,
              orbConfirmMode: s.confirmation_mode ?? '',
              orbTradingDate: date,
              orbBlockReason: s.block_reason ?? '',
            });
          }
        } else {
          // State is no longer QUALIFIED — reset dedup so next QUALIFIED fires fresh
          // (but only if the current dedup entry was for today to avoid cross-day wipe)
          const date = s.trading_date ?? '';
          const current = seenOrbRef.current[inst] ?? '';
          if (current && !current.includes(date)) {
            seenOrbRef.current[inst] = '';
          }
        }
      }
    } catch { /* silently ignore */ }
  }, [pushAlert]);

  // ── Phase 2 Ghost Research Engine polling ──────────────────────────────────
  // Polls /ghost-research/ready-for-review every 30 s.
  // Each READY_FOR_REVIEW experiment gets exactly one alert per session.
  // SAFETY: display-only; never calls any broker path or modifies gate state.

  const pollResearch = useCallback(async () => {
    if (!authenticatedRef.current || document.hidden) return;
    const authGeneration = authGenerationRef.current;
    const authorization = authorizationRef.current;
    const signal = authAbortRef.current?.signal;
    if (!signal) return;
    try {
      const res = await fetch('/api/ghost-research/ready-for-review', {
        credentials: 'include', headers: authHeaders(authorization), signal,
      });
      if (res.status === 401 || res.status === 403) announceDashboardAuth(false);
      if (!authenticatedRef.current || authGeneration !== authGenerationRef.current) return;
      if (!res.ok) return;
      const data = await res.json() as { ok?: boolean; experiments?: Array<{
        experiment_id: string; variant_name: string; instrument: string;
        direction: string; trading_date: string | null; net_r: number | null;
      }> };
      if (!data?.ok || !Array.isArray(data.experiments)) return;

      for (const exp of data.experiments) {
        const id = `RESEARCH|RFR|${exp.experiment_id}`;
        if (seenResearchRef.current.has(id)) continue;
        seenResearchRef.current.add(id);

        const netRStr = exp.net_r != null ? `${exp.net_r >= 0 ? '+' : ''}${exp.net_r.toFixed(2)}R` : '';
        const parts = [exp.variant_name, netRStr].filter(Boolean);

        pushAlert({
          id,
          type:           'RESEARCH_READY_FOR_REVIEW',
          instrument:     exp.instrument,
          direction:      exp.direction,
          strategy:       exp.variant_name,
          timestamp:      Date.now(),
          acknowledged:   false,
          isShadow:       true,
          label:          `${exp.instrument}  RESEARCH  READY FOR REVIEW`,
          sublabel:       parts.join('  ·  '),
        });
      }
    } catch { /* silently ignore */ }
  }, [pushAlert]);

  // ── System health polling ───────────────────────────────────────────────────

  const pollSystemHealth = useCallback(async () => {
    if (!authenticatedRef.current || document.hidden) return;
    const authGeneration = authGenerationRef.current;
    const authorization = authorizationRef.current;
    const signal = authAbortRef.current?.signal;
    if (!signal) return;
    try {
      const data = await fetchSystemHealth(authHeaders(authorization), signal);
      if (!authenticatedRef.current || authGeneration !== authGenerationRef.current) return;
      if (!data) return;

      const warnings: string[] = [];
      if (data.ready_for_market === false) warnings.push('System not ready for market');
      if (typeof data.error_count === 'number' && data.error_count > 0)
        warnings.push(`${data.error_count} research engine error${data.error_count > 1 ? 's' : ''}`);
      if (data.ghost_engine?.table_ready === false)  warnings.push('Ghost observations table unavailable');
      if (data.edge_ledger?.table_ready === false)   warnings.push('Edge ledger table unavailable');

      for (const warn of warnings) {
        if (seenSysWarnRef.current.has(warn)) continue;
        seenSysWarnRef.current.add(warn);
        pushAlert({
          id:           `SYS_HEALTH|${warn}`,
          type:         'SYSTEM_SAFETY',
          instrument:   'SYSTEM',
          timestamp:    Date.now(),
          acknowledged: false,
          isShadow:     false,
          label:        'System warning',
          sublabel:     warn,
        });
      }
    } catch { /* silently ignore */ }
  }, [pushAlert]);

  // Start polling
  useEffect(() => {
    if (!isAuthenticated) return;
    void pollLive();
    const id = setInterval(() => { void pollLive(); }, LIVE_POLL_MS);
    return () => clearInterval(id);
  }, [isAuthenticated, pollLive]);

  useEffect(() => {
    if (!isAuthenticated) return;
    void pollOrb();
    const id = setInterval(() => { void pollOrb(); }, ORB_POLL_MS);
    return () => clearInterval(id);
  }, [isAuthenticated, pollOrb]);

  useEffect(() => {
    if (!isAuthenticated) return;
    void pollResearch();
    const id = setInterval(() => { void pollResearch(); }, RESEARCH_POLL_MS);
    return () => clearInterval(id);
  }, [isAuthenticated, pollResearch]);

  useEffect(() => {
    if (!isAuthenticated) return;
    void pollSystemHealth();
    const id = setInterval(() => { void pollSystemHealth(); }, SYSTEM_HEALTH_POLL_MS);
    return () => clearInterval(id);
  }, [isAuthenticated, pollSystemHealth]);

  // ── Alert click: switch instrument + navigate ───────────────────────────────

  const handleClickAlert = useCallback((alert: AlertItem) => {
    setActiveTicker(alert.instrument);
    setDrawerOpen(false);

    // Navigate to Main Brain home; ORB panel is accessible from the Engine tab there
    const base = import.meta.env.BASE_URL?.replace(/\/$/, '') ?? '';
    if (window.location.pathname !== base + '/') {
      navigate('/');
    }
  }, [navigate]);

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <>
      <CompactDock
        queue={queue}
        drawerOpen={drawerOpen}
        onToggle={() => setDrawerOpen(o => !o)}
        isMobile={isMobile}
      />
      {drawerOpen && (
        <Drawer
          queue={queue}
          onAck={handleAck}
          onClearAcked={handleClearAcked}
          onClickAlert={handleClickAlert}
          onClose={() => setDrawerOpen(false)}
          isMobile={isMobile}
        />
      )}
    </>
  );
};
