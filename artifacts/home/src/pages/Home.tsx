import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';

// ── Theme per brain status ─────────────────────────────────────────────────────
const ST: Record<string, { c: string; a: string; lbl: string; bri: number; glow: string }> = {
  READY:    { c: '#3b82f6', a: '#93c5fd', lbl: 'CONFIDENT',  bri: 1.18, glow: '0 0 80px rgba(59,130,246,0.35)' },
  MANAGING: { c: '#06b6d4', a: '#67e8f9', lbl: 'FOCUSED',    bri: 1.12, glow: '0 0 80px rgba(6,182,212,0.30)' },
  BUILDING: { c: '#f59e0b', a: '#fcd34d', lbl: 'THINKING',   bri: 1.02, glow: '0 0 60px rgba(245,158,11,0.22)' },
  HUNTING:  { c: '#f97316', a: '#fdba74', lbl: 'WATCHING',   bri: 1.00, glow: '0 0 60px rgba(249,115,22,0.18)' },
  WATCHING: { c: '#3b82f6', a: '#93c5fd', lbl: 'WATCHING',   bri: 0.92, glow: '0 0 60px rgba(59,130,246,0.15)' },
  WAIT:     { c: '#6b7280', a: '#9ca3af', lbl: 'WAITING',    bri: 0.72, glow: '0 0 40px rgba(107,114,128,0.10)' },
};
const DST = ST.WATCHING;

// ── Formatting helpers ──────────────────────────────────────────────────────────
const fmt = (n: number | null | undefined, dec = 2): string =>
  n != null ? Number(n).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) : '—';

const BULL = '#22c55e'; const BEAR = '#ef4444'; const AMB = '#f59e0b';
const MUTED = 'rgba(255,255,255,0.24)';

const dirClr = (d: string | null | undefined): string =>
  /long|bull/i.test(d ?? '') ? BULL : /short|bear/i.test(d ?? '') ? BEAR : 'rgba(255,255,255,0.55)';

const statusClr = (s: string): string =>
  s === 'READY' ? BULL : s === 'MANAGING' ? '#60a5fa' : s === 'BUILDING' ? AMB : MUTED;

// ── Clock hook ─────────────────────────────────────────────────────────────────
function useClock() {
  const [time, setTime] = useState('');
  useEffect(() => {
    const tick = () => setTime(
      new Date().toLocaleTimeString('en-US', {
        hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'America/New_York',
      }) + ' ET'
    );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);
  return time;
}

// ── Character-stream hook ──────────────────────────────────────────────────────
function useStream(target: string, msPerChar = 13) {
  const [text, setText] = useState('');
  const [live, setLive] = useState(false);
  const prev = useRef('');
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!target || target === prev.current) return;
    prev.current = target;
    if (timer.current) clearInterval(timer.current);
    setText(''); setLive(true);
    let i = 0;
    timer.current = setInterval(() => {
      i++;
      setText(target.slice(0, i));
      if (i >= target.length) { clearInterval(timer.current!); setLive(false); }
    }, msPerChar);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [target, msPerChar]);
  return { text, live };
}

// ── TTS hook ───────────────────────────────────────────────────────────────────
function useTTS() {
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [voiceName, setVoiceN] = useState<string>(() => {
    try { return localStorage.getItem('brain_voice') ?? ''; } catch { return ''; }
  });
  const [muted, setMutedState] = useState<boolean>(() => {
    try { return localStorage.getItem('brain_muted') !== '0'; } catch { return true; }
  });
  const [speaking, setSpeaking] = useState(false);
  useEffect(() => {
    const ss = window.speechSynthesis;
    if (!ss) return;
    const load = () => {
      const all = ss.getVoices();
      const en = all.filter(v => v.lang.startsWith('en'));
      setVoices(en.length ? en : all.slice(0, 30));
    };
    load(); ss.addEventListener('voiceschanged', load);
    return () => ss.removeEventListener('voiceschanged', load);
  }, []);
  const setVoice = useCallback((name: string) => {
    try { localStorage.setItem('brain_voice', name); } catch {}
    setVoiceN(name);
  }, []);
  const setMuted = useCallback((m: boolean) => {
    try { localStorage.setItem('brain_muted', m ? '1' : '0'); } catch {}
    if (m) { window.speechSynthesis?.cancel(); setSpeaking(false); }
    setMutedState(m);
  }, []);
  const speak = useCallback((text: string) => {
    const ss = window.speechSynthesis;
    if (!text || muted || !ss) return;
    ss.cancel();
    const utt = new SpeechSynthesisUtterance(text.slice(0, 400));
    const voice = voices.find(v => v.name === voiceName) ?? voices[0];
    if (voice) utt.voice = voice;
    utt.rate = 0.92; utt.pitch = 1.05;
    utt.onstart = () => setSpeaking(true);
    utt.onend = () => setSpeaking(false);
    utt.onerror = () => setSpeaking(false);
    ss.speak(utt);
  }, [voices, voiceName, muted]);
  return { voices, voiceName, setVoice, muted, setMuted, speaking, speak };
}

// ── Candle chart data ──────────────────────────────────────────────────────────
type Candle = { t: number; o: number; h: number; l: number; c: number; vol: number };
function makeCandles(base: number, n = 75): Candle[] {
  const out: Candle[] = [];
  let price = base * 0.9975;
  const now = Date.now();
  const step = base * 0.00065;
  for (let i = 0; i < n; i++) {
    const o = price;
    const body = (Math.random() - 0.468) * step;
    const c = o + body;
    const wick = step * 0.55;
    out.push({
      t: now - (n - i) * 60000,
      o, h: Math.max(o, c) + Math.random() * wick,
      l: Math.min(o, c) - Math.random() * wick,
      c, vol: 0.25 + Math.random() * 1.75,
    });
    price = c;
  }
  return out;
}

// ── Donut score SVG ────────────────────────────────────────────────────────────
function DonutScore({ score, max = 110, size = 110, sw = 9, color = '#3b82f6', label = '/100' }: {
  score: number; max?: number; size?: number; sw?: number; color?: string; label?: string
}) {
  const r = (size - sw * 2) / 2;
  const cx = size / 2; const cy = size / 2;
  const circ = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(score, max)) / max;
  const filled = pct * circ;
  return (
    <svg width={size} height={size} style={{ display: 'block' }}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={sw} />
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth={sw}
        strokeDasharray={`${filled} ${circ}`} strokeDashoffset={circ * 0.25}
        strokeLinecap="round" style={{ transition: 'stroke-dasharray 1.2s ease' }} />
      <text x={cx} y={cy - 6} textAnchor="middle" fill="white" fontSize={26} fontWeight={700}
        style={{ fontFamily: 'monospace' }}>{Math.round(score)}</text>
      <text x={cx} y={cy + 12} textAnchor="middle" fill="rgba(255,255,255,0.35)" fontSize={11}>{label}</text>
    </svg>
  );
}

// ── Voice waveform SVG ─────────────────────────────────────────────────────────
function VoiceWave({ active, color = '#3b82f6' }: { active: boolean; color?: string }) {
  const bars = [3, 6, 10, 7, 13, 8, 14, 9, 12, 7, 10, 6, 4, 8, 5];
  return (
    <svg width={120} height={40} viewBox="0 0 120 40" style={{ display: 'block' }}>
      {bars.map((h, i) => (
        <rect key={i} x={i * 8 + 1} y={(40 - h) / 2} width={5} height={h} rx={2.5}
          fill={active ? color : 'rgba(255,255,255,0.12)'}
          style={active ? { animation: `wv ${0.6 + (i % 5) * 0.15}s ease-in-out ${i * 0.05}s infinite alternate` } : {}} />
      ))}
    </svg>
  );
}

// ── Candlestick chart SVG ──────────────────────────────────────────────────────
function CandleChart({ candles, vwap, demand, supply, ticker }: {
  candles: Candle[]; vwap?: number; demand?: number; supply?: number; ticker: string;
}) {
  if (!candles.length) return <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: MUTED, fontSize: 12 }}>Loading chart...</div>;

  const W = 1000; const CH = 190; const VH = 36; const H = CH + VH;
  const n = candles.length;
  const slotW = W / n;
  const bodyW = slotW * 0.68;
  const pad = slotW * 0.16;

  const allH = candles.map(c => c.h);
  const allL = candles.map(c => c.l);
  let minP = Math.min(...allL);
  let maxP = Math.max(...allH);
  if (demand != null) { minP = Math.min(minP, demand * 0.9995); }
  if (supply != null) { maxP = Math.max(maxP, supply * 1.0005); }
  const rng = maxP - minP || 1;
  const pY = (p: number) => ((maxP - p) / rng) * CH;

  const maxV = Math.max(...candles.map(c => c.vol), 0.1);

  // Price axis labels (4 levels)
  const priceLvls = [0, 1, 2, 3].map(i => minP + (rng * i) / 3);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {/* Chart header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px 4px', fontSize: 12 }}>
        <span style={{ color: 'rgba(255,255,255,0.7)', fontWeight: 700 }}>{ticker}</span>
        <span style={{ color: MUTED, fontSize: 10 }}>1m</span>
        {vwap != null && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#60a5fa', fontSize: 11 }}>
            <span style={{ display: 'inline-block', width: 18, height: 1.5, background: '#60a5fa', opacity: 0.7, borderTop: '1.5px dashed #60a5fa' }} />
            VWAP {fmt(vwap)}
          </span>
        )}
        {demand != null && (
          <span style={{ color: '#22c55e', fontSize: 11 }}>⬛ Demand {fmt(demand)}</span>
        )}
        {supply != null && (
          <span style={{ color: '#ef4444', fontSize: 11 }}>⬛ Supply {fmt(supply)}</span>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="calc(100% - 32px)"
        preserveAspectRatio="none" style={{ display: 'block' }}>
        {/* Grid lines */}
        {priceLvls.map((p, i) => (
          <line key={i} x1={0} y1={pY(p)} x2={W} y2={pY(p)} stroke="rgba(255,255,255,0.04)" strokeWidth={1} />
        ))}

        {/* Demand zone */}
        {demand != null && (
          <rect x={0} y={Math.max(0, pY(demand + rng * 0.004))}
            width={W} height={Math.max(4, pY(demand - rng * 0.006) - pY(demand + rng * 0.004))}
            fill="#22c55e12" />
        )}
        {/* Supply zone */}
        {supply != null && (
          <rect x={0} y={Math.max(0, pY(supply + rng * 0.004))}
            width={W} height={Math.max(4, pY(supply - rng * 0.004) - pY(supply + rng * 0.006))}
            fill="#ef444412" />
        )}
        {/* VWAP line */}
        {vwap != null && vwap >= minP && vwap <= maxP && (
          <line x1={0} y1={pY(vwap)} x2={W} y2={pY(vwap)}
            stroke="#60a5fa" strokeWidth={1.5} strokeDasharray="6,4" opacity={0.7} />
        )}
        {/* Candles */}
        {candles.map((c, i) => {
          const x = i * slotW;
          const bull = c.c >= c.o;
          const col = bull ? '#22c55e' : '#ef4444';
          const bodyTop = Math.min(pY(c.o), pY(c.c));
          const bodyH = Math.max(1.5, Math.abs(pY(c.o) - pY(c.c)));
          const wickX = x + slotW / 2;
          return (
            <g key={i}>
              <line x1={wickX} y1={pY(c.h)} x2={wickX} y2={pY(c.l)} stroke={col} strokeWidth={1} opacity={0.75} />
              <rect x={x + pad} y={bodyTop} width={bodyW} height={bodyH} fill={col} opacity={0.9} rx={0.5} />
            </g>
          );
        })}
        {/* Volume bars */}
        {candles.map((c, i) => {
          const bull = c.c >= c.o;
          const vh = (c.vol / maxV) * VH;
          return (
            <rect key={i} x={i * slotW + pad} y={CH + VH - vh} width={bodyW} height={vh}
              fill={bull ? '#22c55e' : '#ef4444'} opacity={0.25} />
          );
        })}
        {/* Last price line */}
        {candles.length > 0 && (() => {
          const lastClose = candles[candles.length - 1].c;
          return (
            <>
              <line x1={0} y1={pY(lastClose)} x2={W} y2={pY(lastClose)}
                stroke="rgba(255,255,255,0.18)" strokeWidth={1} strokeDasharray="3,3" />
              <rect x={W - 90} y={pY(lastClose) - 9} width={90} height={18} fill="#1e293b" rx={3} />
              <text x={W - 6} y={pY(lastClose) + 4} textAnchor="end" fill="rgba(255,255,255,0.8)"
                fontSize={11} style={{ fontFamily: 'monospace' }}>{fmt(lastClose)}</text>
            </>
          );
        })()}
      </svg>
    </div>
  );
}

// ── Panel card wrapper ──────────────────────────────────────────────────────────
function Panel({ title, icon, children, style }: {
  title: string; icon?: string; children: React.ReactNode; style?: React.CSSProperties
}) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.028)', border: '1px solid rgba(255,255,255,0.065)',
      borderRadius: 10, padding: '12px 14px', marginBottom: 8, ...style
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
        {icon && <span style={{ fontSize: 12 }}>{icon}</span>}
        <span style={{ fontSize: 9.5, fontFamily: 'monospace', color: 'rgba(255,255,255,0.30)',
          letterSpacing: '0.12em', textTransform: 'uppercase', fontWeight: 600 }}>{title}</span>
      </div>
      {children}
    </div>
  );
}

function PanelRow({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.028)' }}>
      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.35)', fontFamily: 'monospace' }}>{label}</span>
      <span style={{ fontSize: 11.5, color: valueColor || 'rgba(255,255,255,0.72)', fontWeight: 600,
        fontFamily: 'monospace' }}>{value}</span>
    </div>
  );
}

// ── Market Context panel ────────────────────────────────────────────────────────
function MarketContextPanel({ data }: { data: any }) {
  const sig = data?.main_brain?.signals || {};
  const gd  = data?.gate_debug || {};
  const ad  = data?.alert_diagnostics || {};

  const trend = sig.bias
    ? (String(sig.bias).toLowerCase().includes('bull') ? 'BULLISH'
      : String(sig.bias).toLowerCase().includes('bear') ? 'BEARISH' : 'NEUTRAL')
    : '—';
  const struct = gd.structure_confirmed === true ? 'BULLISH'
    : gd.structure_confirmed === false ? 'WEAK' : (String(ad.structure || '—').toUpperCase() || '—');
  const momentum = sig.cvd && sig.cvd !== 'unknown'
    ? String(sig.cvd).toUpperCase() : '—';
  const vol = String(data?.vol_regime || ad.volatility || '—').toUpperCase();
  const volume = ad.volume ? String(ad.volume).toUpperCase() : (sig.volume_strength ? String(sig.volume_strength).toUpperCase() : '—');

  return (
    <Panel title="Market Context" icon="◎">
      <PanelRow label="Trend" value={trend} valueColor={/BULL/.test(trend) ? BULL : /BEAR/.test(trend) ? BEAR : undefined} />
      <PanelRow label="Structure" value={struct} valueColor={/BULL/.test(struct) ? BULL : /WEAK/.test(struct) ? BEAR : undefined} />
      <PanelRow label="Momentum" value={momentum} valueColor={/BULL|POS/.test(momentum) ? BULL : /BEAR|NEG/.test(momentum) ? BEAR : undefined} />
      <PanelRow label="Volatility" value={vol} valueColor={/ELEV|HIGH/.test(vol) ? AMB : undefined} />
      <PanelRow label="Volume" value={volume} valueColor={/INC|STRONG|HIGH/.test(volume) ? BULL : undefined} />
    </Panel>
  );
}

// ── Key Levels panel ───────────────────────────────────────────────────────────
function KeyLevelsPanel({ data }: { data: any }) {
  const price  = Number(data?.price || 0);
  const vwap   = data?.vwap_value;
  const demand = data?.nearest_demand;
  const supply = data?.nearest_supply;
  const tp     = data?.trade_plan || {};
  const dir    = data?.directions || {};
  const longDir  = dir.Long  || {};
  const shortDir = dir.Short || {};

  const r1 = supply ?? (longDir.gate_debug?.nearest_supply) ?? tp.target1 ?? null;
  const s1 = demand ?? (shortDir.gate_debug?.nearest_demand) ?? tp.stop ?? null;
  const entry = tp.entry ?? null;

  return (
    <Panel title="Key Levels" icon="⊞">
      <PanelRow label="VWAP"   value={vwap  != null ? fmt(vwap)  : '—'} valueColor="#60a5fa" />
      <PanelRow label="Supply" value={r1    != null ? fmt(r1)    : '—'} valueColor={BEAR} />
      <PanelRow label="Price"  value={price > 0 ? fmt(price)      : '—'} valueColor="rgba(255,255,255,0.88)" />
      <PanelRow label="Demand" value={s1    != null ? fmt(s1)    : '—'} valueColor={BULL} />
      {entry != null && <PanelRow label="Entry Zone" value={fmt(entry)} valueColor={AMB} />}
    </Panel>
  );
}

// ── Position panel ─────────────────────────────────────────────────────────────
function PositionPanel({ data }: { data: any }) {
  const at = data?.active_trade || data?.managing_trade || null;
  const hasTrade = !!(at && (at.direction || at.contracts));
  const dirLabel  = hasTrade ? (at.direction || 'FLAT') : 'FLAT';
  const contracts = hasTrade ? (at.contracts ?? 0) : 0;
  const avgPrice  = hasTrade ? (at.entry_price ?? at.avg_price ?? null) : null;
  const openPnl   = hasTrade ? (at.unrealized_pnl ?? at.open_pnl ?? null) : 0;
  const dailyPnl  = data?.daily_pnl ?? data?.realized_pnl ?? null;

  return (
    <Panel title="Position" icon="◈">
      <PanelRow label="Direction"  value={dirLabel.toUpperCase()} valueColor={hasTrade ? dirClr(dirLabel) : MUTED} />
      <PanelRow label="Contracts"  value={String(contracts)} />
      <PanelRow label="Avg Price"  value={avgPrice != null ? fmt(avgPrice) : '—'} />
      <PanelRow label="Open P&L"   value={openPnl != null ? (openPnl >= 0 ? '+' : '') + '$' + fmt(openPnl) : '$0.00'}
        valueColor={openPnl != null && openPnl > 0 ? BULL : openPnl != null && openPnl < 0 ? BEAR : undefined} />
      <PanelRow label="Daily P&L"  value={dailyPnl != null ? (dailyPnl >= 0 ? '+' : '') + '$' + fmt(dailyPnl) : '—'}
        valueColor={dailyPnl != null && dailyPnl > 0 ? BULL : dailyPnl != null && dailyPnl < 0 ? BEAR : undefined} />
    </Panel>
  );
}

// ── Confidence meter (left sidebar) ────────────────────────────────────────────
function ConfidenceMeter({ data, status }: { data: any; status: string }) {
  const st   = ST[status] || DST;
  const edge = data?.edge_score ?? data?.main_brain?.edge_score ?? 0;
  const grade = data?.edge_grade ?? data?.main_brain?.edge_grade ?? '';
  const tp   = data?.trade_plan || {};
  const rr   = tp.rr_display ?? (tp.rr ? `1:${tp.rr}` : null);
  const winP = tp.win_probability ?? data?.win_probability ?? null;

  return (
    <Panel title="Confidence Meter" icon="◉">
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        <DonutScore score={Number(edge)} color={st.c} size={90} sw={7} />
        {grade && (
          <span style={{ fontSize: 12, color: st.c, fontWeight: 700, fontFamily: 'monospace',
            letterSpacing: '0.06em' }}>{grade}</span>
        )}
      </div>
      <div style={{ marginTop: 8 }}>
        <PanelRow label="Edge Quality" value={
          Number(edge) >= 85 ? 'EXCELLENT' : Number(edge) >= 70 ? 'GOOD' :
          Number(edge) >= 50 ? 'BUILDING' : 'WEAK'
        } valueColor={Number(edge) >= 70 ? BULL : Number(edge) >= 50 ? AMB : MUTED} />
        <PanelRow label="Risk / Reward" value={rr ?? '—'} />
        <PanelRow label="Win Probability" value={winP != null ? `${Math.round(Number(winP) * 100)}%` : '—'} />
      </div>
    </Panel>
  );
}

// ── Brain checklist from gate data ─────────────────────────────────────────────
function getBrainChecklist(data: any): Array<{ text: string; st: 'pass' | 'fail' | 'wait' | 'neutral' }> {
  if (!data) return [];
  const gd  = data.gate_debug || {};
  const sig = (data.main_brain || {}).signals || {};
  const ad  = data.alert_diagnostics || {};
  const price = Number(data.price || 0);
  const vwap  = Number(data.vwap_value || 0);
  const items: Array<{ text: string; st: 'pass' | 'fail' | 'wait' | 'neutral' }> = [];

  if (vwap > 0 && price > 0) {
    const above = price > vwap;
    items.push({ text: `Price ${above ? 'above' : 'below'} VWAP (${fmt(vwap)})`, st: above ? 'pass' : 'wait' });
  }
  if (gd.structure_confirmed != null) {
    items.push({ text: gd.structure_confirmed ? 'Structure confirmed bullish' : 'Structure not confirmed', st: gd.structure_confirmed ? 'pass' : 'wait' });
  }
  if (gd.zone_valid != null) {
    items.push({ text: gd.zone_valid ? 'Zone active and intact' : 'No active zone', st: gd.zone_valid ? 'pass' : 'neutral' });
  }
  const cvd = sig.cvd;
  if (cvd && cvd !== 'unknown') {
    items.push({ text: `Order flow ${cvd}`, st: /bull|pos/.test(cvd) ? 'pass' : /bear|neg/.test(cvd) ? 'fail' : 'neutral' });
  }
  if (ad.volume && ad.volume !== 'unknown') {
    items.push({ text: `Volume ${ad.volume}`, st: /incr|strong|high/i.test(ad.volume) ? 'pass' : 'neutral' });
  }
  return items.slice(0, 5);
}

// ── Checklist icon ─────────────────────────────────────────────────────────────
const clIcon = (s: string) =>
  s === 'pass' ? { icon: '✓', color: BULL }
  : s === 'fail' ? { icon: '✕', color: BEAR }
  : s === 'wait' ? { icon: '○', color: AMB }
  : { icon: '~', color: 'rgba(255,255,255,0.4)' };

// ── Current Analysis panel (right) ─────────────────────────────────────────────
function CurrentAnalysisPanel({ data, status, statusLabel }: { data: any; status: string; statusLabel: string }) {
  const sig = (data?.main_brain || {}).signals || {};
  const tp  = data?.trade_plan || {};
  const ad  = data?.alert_diagnostics || {};
  const bias = sig.bias ?? ad.bias ?? null;
  const setup = sig.favored ? `${sig.favored.toUpperCase()} SETUP` : (ad.setup_type ?? '—');
  const stClr = status === 'READY' ? BULL : status === 'MANAGING' ? '#60a5fa' : MUTED;
  const biasStr = bias ? String(bias).toUpperCase() : '—';

  return (
    <Panel title="Current Analysis" icon="◐">
      <div style={{ fontSize: 12, fontWeight: 700, color: stClr, fontFamily: 'monospace',
        letterSpacing: '0.06em', marginBottom: 10, textAlign: 'center' }}>
        {statusLabel.toUpperCase()}
      </div>
      <PanelRow label="Bias"      value={biasStr} valueColor={/BULL/i.test(biasStr) ? BULL : /BEAR/i.test(biasStr) ? BEAR : undefined} />
      <PanelRow label="Setup"     value={setup}   valueColor={/LONG/i.test(setup) ? BULL : /SHORT/i.test(setup) ? BEAR : undefined} />
      <PanelRow label="Timeframe" value="1m / 5m" />
      <PanelRow label="Status"    value={status}  valueColor={stClr} />
      {tp.expires_at && <PanelRow label="Valid Until" value={tp.expires_at} />}
    </Panel>
  );
}

// ── Evidence Score panel (right) ───────────────────────────────────────────────
function EvidenceScorePanel({ data, status }: { data: any; status: string }) {
  const st   = ST[status] || DST;
  const edge = Number(data?.edge_score ?? 0);
  const eb   = data?.edge_breakdown || data?.main_brain?.edge_breakdown || {};

  const components: Array<[string, string]> = [
    ['Market Structure', eb.bos20 != null  ? `${Number(eb.bos20).toFixed(0)} / 20`  : '— / 20'],
    ['Liquidity',        eb.sweep15 != null ? `${Number(eb.sweep15).toFixed(0)} / 15` : '— / 15'],
    ['Volume / Delta',   eb.volume15 != null ? `${Number(eb.volume15).toFixed(0)} / 15` : '— / 15'],
    ['VWAP Alignment',   eb.vwap15 != null  ? `${Number(eb.vwap15).toFixed(0)} / 15`  : '— / 15'],
    ['Momentum',         eb.session10 != null ? `${Number(eb.session10).toFixed(0)} / 10` : '— / 10'],
  ];

  return (
    <Panel title="Evidence Score" icon="◑">
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 10 }}>
        <DonutScore score={edge} max={110} color={st.c} size={100} sw={8} label={`/ 110`} />
      </div>
      {components.map(([label, val]) => {
        const num = parseFloat(val);
        const maxNum = parseFloat(val.split('/')[1] || '20');
        const good = !isNaN(num) && !isNaN(maxNum) && num >= maxNum * 0.6;
        return (
          <div key={label} style={{ display: 'flex', justifyContent: 'space-between',
            alignItems: 'center', padding: '3px 0', borderBottom: '1px solid rgba(255,255,255,0.025)' }}>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.38)', fontFamily: 'monospace' }}>{label}</span>
            <span style={{ fontSize: 10.5, color: good ? BULL : num === 0 ? BEAR : AMB,
              fontFamily: 'monospace', fontWeight: 600 }}>{val}</span>
          </div>
        );
      })}
    </Panel>
  );
}

// ── Recent Setups panel (right) ────────────────────────────────────────────────
function RecentSetupsPanel({ data }: { data: any }) {
  const trades: any[] = (data?.recent_trades ?? data?.learning?.recent_trades
    ?? data?.today_trades ?? data?.by_instrument_today ?? []) as any[];

  return (
    <Panel title="Recent Setups" icon="◷">
      {trades.length > 0 ? trades.slice(0, 3).map((t: any, i: number) => {
        const win = (t.outcome === 'win' || t.result === 'WIN' || (t.r_multiple != null && Number(t.r_multiple) > 0));
        const rMult = t.r_multiple ?? t.r ?? null;
        const timeStr = t.time ?? t.opened_at_display ?? t.opened_at ?? '—';
        const dir = t.direction ?? t.side ?? '—';
        return (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.028)' }}>
            <span style={{ fontSize: 10, color: MUTED, fontFamily: 'monospace' }}>{String(timeStr).slice(0, 5)}</span>
            <span style={{ fontSize: 10.5, color: /long|bull/i.test(dir) ? BULL : BEAR,
              fontWeight: 700, fontFamily: 'monospace' }}>{String(dir).toUpperCase()}</span>
            <span style={{ fontSize: 10.5, color: win ? BULL : BEAR,
              fontWeight: 700, fontFamily: 'monospace' }}>{win ? 'WIN' : 'LOSS'}</span>
            <span style={{ fontSize: 10, color: win ? BULL : BEAR, fontFamily: 'monospace' }}>
              {rMult != null ? (win ? '+' : '') + Number(rMult).toFixed(1) + 'R' : '—'}
            </span>
          </div>
        );
      }) : (
        <div style={{ color: MUTED, fontSize: 11, textAlign: 'center', padding: '8px 0',
          fontFamily: 'monospace' }}>No setups today</div>
      )}
    </Panel>
  );
}

// ── Voice Status panel (right) ─────────────────────────────────────────────────
function VoiceStatusPanel({ muted, speaking, voices, voiceName, setVoice, setMuted, st }: {
  muted: boolean; speaking: boolean; voices: SpeechSynthesisVoice[];
  voiceName: string; setVoice: (n: string) => void; setMuted: (m: boolean) => void;
  st: { c: string };
}) {
  return (
    <Panel title="Voice Commands" icon="◈">
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
        <VoiceWave active={!muted && speaking} color={st.c} />
        <div style={{ fontSize: 11, color: muted ? MUTED : speaking ? st.c : 'rgba(255,255,255,0.5)',
          fontFamily: 'monospace', letterSpacing: '0.06em' }}>
          {muted ? 'Muted' : speaking ? 'Speaking...' : 'Listening...'}
        </div>
        <button onClick={() => setMuted(!muted)} style={{
          padding: '5px 14px', borderRadius: 16, border: `1px solid ${muted ? 'rgba(255,255,255,0.08)' : st.c + '55'}`,
          background: 'transparent', color: muted ? MUTED : st.c, cursor: 'pointer', fontSize: 11,
          fontFamily: 'monospace', transition: 'all 0.2s'
        }}>
          {muted ? '◯ Enable Voice' : '◼ Disable Voice'}
        </button>
        {!muted && voices.length > 0 && (
          <select value={voiceName || voices[0]?.name || ''} onChange={e => setVoice(e.target.value)} style={{
            width: '100%', background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 8, padding: '4px 8px', color: 'rgba(255,255,255,0.4)', fontSize: 10,
            fontFamily: 'monospace', cursor: 'pointer', outline: 'none'
          }}>
            {voices.map(v => <option key={v.name} value={v.name} style={{ background: '#111' }}>{v.name}</option>)}
          </select>
        )}
      </div>
    </Panel>
  );
}

// ── Trade Plan panel (bottom right) ───────────────────────────────────────────
function TradePlanPanel({ data, status, ticker, authHeader }: {
  data: any; status: string; ticker: string; authHeader: Record<string, string>
}) {
  const tp = data?.trade_plan || {};
  const hasPlan = !!tp.trade_plan || !!tp.entry;
  const dir = tp.direction ?? '—';
  const isActionable = data?.is_actionable === true || status === 'READY';
  const [confirming, setConfirming] = useState(false);
  const [sent, setSent] = useState<string | null>(null);

  const enter = async () => {
    if (!confirming) { setConfirming(true); return; }
    setConfirming(false);
    try {
      const r = await fetch('/api/enter', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({ ticker, direction: dir === 'Long' ? 'long' : 'short' }),
      });
      setSent(r.ok ? '✓ Order sent' : '✗ Send failed');
    } catch { setSent('✗ Network error'); }
    setTimeout(() => setSent(null), 4000);
  };

  const btnColor = isActionable ? BULL : 'rgba(255,255,255,0.12)';

  return (
    <div style={{ width: 270, flexShrink: 0, background: 'rgba(255,255,255,0.028)',
      border: '1px solid rgba(255,255,255,0.065)', borderRadius: 10, padding: '12px 14px',
      display: 'flex', flexDirection: 'column', gap: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
        <span style={{ fontSize: 9.5, fontFamily: 'monospace', color: MUTED, letterSpacing: '0.12em',
          textTransform: 'uppercase', fontWeight: 600 }}>⊛ Trade Plan {hasPlan ? '(Potential)' : ''}</span>
      </div>
      <PanelRow label="Direction" value={hasPlan ? dir.toUpperCase() : 'FLAT'}
        valueColor={hasPlan ? dirClr(dir) : MUTED} />
      <PanelRow label="Entry"    value={tp.entry  != null ? fmt(tp.entry)  : '—'} />
      <PanelRow label="Stop Loss" value={tp.stop  != null ? fmt(tp.stop)   : '—'} valueColor={hasPlan ? BEAR : undefined} />
      <PanelRow label="Target 1" value={tp.target1 != null ? fmt(tp.target1) + ` (${tp.rr_display?.split(':')[1] || '2'}:1)` : '—'} valueColor={hasPlan ? BULL : undefined} />
      <PanelRow label="Target 2" value={tp.target2 != null ? fmt(tp.target2) + ` (${tp.rr_display ? '4:1' : '—'})` : '—'} valueColor={hasPlan ? BULL : undefined} />
      <PanelRow label="Risk"     value={tp.risk_pct != null ? `${Number(tp.risk_pct).toFixed(1)}%` : '—'} />
      <PanelRow label="Contracts" value={tp.contracts != null ? String(tp.contracts) : '—'} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 12 }}>
        {sent ? (
          <div style={{ padding: '9px', textAlign: 'center', borderRadius: 7, fontSize: 12,
            background: sent.startsWith('✓') ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
            color: sent.startsWith('✓') ? BULL : BEAR, fontFamily: 'monospace' }}>{sent}</div>
        ) : (
          <button onClick={enter} disabled={!isActionable} style={{
            padding: '10px', borderRadius: 7, border: 'none', cursor: isActionable ? 'pointer' : 'default',
            background: confirming ? 'rgba(239,68,68,0.25)' : isActionable ? 'rgba(34,197,94,0.18)' : 'rgba(255,255,255,0.04)',
            color: isActionable ? BULL : MUTED, fontSize: 12, fontWeight: 700, fontFamily: 'monospace',
            letterSpacing: '0.06em', transition: 'all 0.2s',
            textShadow: isActionable ? `0 0 12px ${BULL}66` : 'none',
          }}>
            {confirming ? 'CONFIRM — SEND LIVE ORDER' : isActionable ? 'READY TO TRADE' : 'WAITING FOR SETUP'}
          </button>
        )}
        <button onClick={() => { setConfirming(false); }} style={{
          padding: '8px', borderRadius: 7, border: '1px solid rgba(255,255,255,0.08)',
          background: 'transparent', color: 'rgba(255,255,255,0.35)', fontSize: 11,
          cursor: 'pointer', fontFamily: 'monospace', transition: 'all 0.2s'
        }}
          onMouseEnter={e => { e.currentTarget.style.color = 'rgba(255,255,255,0.65)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.18)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'rgba(255,255,255,0.35)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)'; }}>
          Simulate Trade
        </button>
        {confirming && (
          <button onClick={() => setConfirming(false)} style={{
            padding: '6px', borderRadius: 7, border: '1px solid rgba(239,68,68,0.3)',
            background: 'transparent', color: BEAR, fontSize: 11, cursor: 'pointer', fontFamily: 'monospace'
          }}>Reject Setup</button>
        )}
      </div>
    </div>
  );
}

// ── Chat message types ─────────────────────────────────────────────────────────
interface Msg { id: number; role: 'user' | 'brain'; text: string; }
let _mid = 0;
const mkMsg = (role: Msg['role'], text: string): Msg => ({ id: ++_mid, role, text });

function BrainBubble({ msg }: { msg: Msg }) {
  const { text, live } = useStream(msg.role === 'brain' ? msg.text : '', 11);
  const shown = msg.role === 'brain' ? text : msg.text;
  return (
    <div style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start' }}>
      <div style={{
        maxWidth: '86%', padding: '8px 14px', fontSize: 13, lineHeight: 1.65,
        borderRadius: 16,
        borderBottomRightRadius: msg.role === 'user' ? 3 : 16,
        borderBottomLeftRadius: msg.role === 'brain' ? 3 : 16,
        background: msg.role === 'user' ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.04)',
        color: msg.role === 'user' ? 'rgba(255,255,255,0.82)' : 'rgba(255,255,255,0.62)',
      }}>
        {shown}
        {live && <span style={{ display: 'inline-block', width: 2, height: 13, background: 'rgba(255,255,255,0.4)',
          marginLeft: 2, verticalAlign: 'middle', animation: 'bDot 0.8s ease-in-out infinite' }} />}
      </div>
    </div>
  );
}

// ── Login overlay ──────────────────────────────────────────────────────────────
function LoginOverlay({ onSubmit }: { onSubmit: (pwd: string) => void }) {
  const [val, setVal] = useState('');
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { setTimeout(() => ref.current?.focus(), 80); }, []);
  const submit = () => { const p = val.trim(); if (p) onSubmit(p); };
  return (
    <div style={{ position: 'fixed', inset: 0, background: '#060a0f', display: 'flex',
      flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 28, zIndex: 999 }}>
      <div style={{ position: 'relative', width: 72, height: 72, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', border: '1px solid #3b82f655',
          animation: 'bPulse 2.8s ease-in-out infinite' }} />
        <div style={{ width: 44, height: 44, borderRadius: '50%', border: '1px solid #3b82f633',
          background: 'radial-gradient(circle at 35% 35%, rgba(59,130,246,0.15), rgba(0,0,0,0.7))',
          display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#3b82f6',
            animation: 'bBreathe 3s ease-in-out infinite' }} />
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.5)', fontFamily: 'monospace',
          letterSpacing: '0.10em' }}>ACCESS REQUIRED</span>
        <span style={{ fontSize: 11, color: '#374151', fontFamily: 'monospace' }}>Enter your dashboard password</span>
      </div>
      <form onSubmit={e => { e.preventDefault(); submit(); }} style={{ display: 'flex', gap: 8, width: 300 }}>
        <input ref={ref} type="password" value={val} onChange={e => setVal(e.target.value)}
          placeholder="Password" style={{ flex: 1, background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.10)', borderRadius: 8, padding: '10px 14px',
          fontSize: 14, color: 'rgba(255,255,255,0.8)', fontFamily: 'inherit', outline: 'none' }} />
        <button type="submit" style={{ padding: '10px 18px', background: 'rgba(59,130,246,0.15)',
          border: '1px solid rgba(59,130,246,0.3)', borderRadius: 8, color: '#93c5fd',
          fontSize: 13, fontFamily: 'inherit', cursor: 'pointer' }}>Enter</button>
      </form>
    </div>
  );
}

// ── Root ───────────────────────────────────────────────────────────────────────
export default function Home() {
  const [ticker, setTicker]   = useState<'MGC' | 'MNQ' | 'MES' | 'MYM'>('MNQ');
  const [data,   setData]     = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [msgs,   setMsgs]     = useState<Msg[]>([]);
  const [input,  setInput]    = useState('');
  const [asking, setAsking]   = useState(false);
  const [authPwd, setAuthPwd] = useState<string>(() => {
    try { return localStorage.getItem('brain_auth') || ''; } catch { return ''; }
  });
  const [authNeeded, setAuthNeeded] = useState<boolean>(() => {
    try { return !localStorage.getItem('brain_auth'); } catch { return true; }
  });
  const chatRef  = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Candle data — generated once per ticker/price band
  const candlesRef   = useRef<Candle[]>([]);
  const priceBaseRef = useRef<number>(0);

  const clock = useClock();
  const { voices, voiceName, setVoice, muted, setMuted, speaking: ttsSpeaking, speak } = useTTS();
  const speakRef      = useRef(speak);
  const lastSpokenRef = useRef('');
  useEffect(() => { speakRef.current = speak; }, [speak]);

  const authHeader = useMemo((): Record<string, string> =>
    authPwd ? { 'Authorization': 'Basic ' + btoa('admin:' + authPwd) } : {}
  , [authPwd]);

  const handleAuth = useCallback((pwd: string) => {
    try { localStorage.setItem('brain_auth', pwd); } catch {}
    setAuthPwd(pwd); setAuthNeeded(false);
  }, []);

  const poll = useCallback(async () => {
    if (!authPwd) return;
    try {
      const r = await fetch(`/api/status?ticker=${ticker}`, { credentials: 'include', headers: authHeader });
      if (r.status === 401) {
        setAuthNeeded(true); setAuthPwd('');
        try { localStorage.removeItem('brain_auth'); } catch {}
        return;
      }
      if (r.ok) {
        const d = await r.json();
        setData(d); setLoading(false);
        // Update candles when price changes significantly
        const p = Number(d?.price || 0);
        if (p > 0) {
          const pct = Math.abs(p - priceBaseRef.current) / (priceBaseRef.current || 1);
          if (candlesRef.current.length === 0 || pct > 0.006) {
            priceBaseRef.current = p;
            candlesRef.current = makeCandles(p);
          } else {
            // Nudge last candle to current price
            const c = candlesRef.current;
            if (c.length > 0) {
              const last = c[c.length - 1];
              c[c.length - 1] = { ...last, c: p, h: Math.max(last.h, p), l: Math.min(last.l, p) };
            }
          }
        }
      }
    } catch {}
  }, [ticker, authPwd, authHeader]);

  useEffect(() => {
    setLoading(true); setData(null); candlesRef.current = [];
    poll();
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  }, [poll]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [msgs]);

  // Derived values
  const mb      = (data?.main_brain || {}) as Record<string, any>;
  const voice   = (data?.main_brain_voice || {}) as Record<string, any>;
  const status  = (mb.status || 'WATCHING') as string;
  const edge    = mb.edge_score ?? data?.edge_score;
  const grade   = mb.edge_grade ?? data?.edge_grade;
  const dirn    = mb.favored_direction as string | undefined;
  const lm      = (mb.learning_memory || {}) as Record<string, any>;

  const narration = (
    voice.narration ||
    (mb.synthesis as any)?.narrative ||
    mb.summary ||
    (loading ? '' :
      status === 'READY' ? 'This is the strongest setup I have seen in the last hour. Risk-to-reward meets requirements. I recommend entry.' :
      status === 'MANAGING' ? 'Managing open position. Monitoring price action for thesis invalidation or target hits.' :
      status === 'BUILDING' ? 'Setup is forming. Waiting for final confirmation before considering entry.' :
      status === 'WAIT' ? 'No edge present. Buyers are not yet defending. Capital preservation comes first.' :
      'Watching the tape. Scanning for high-probability setups across key levels...')
  ) as string;

  const strictR = data?.strict_reason || mb.wait_reason || '';

  const st = ST[status] || DST;
  const { text: displayed, live: streaming } = useStream(narration, 13);
  const checklist = data ? getBrainChecklist(data) : [];

  // Score history for progression display
  const scoreHistRef = useRef<number[]>([]);
  useEffect(() => {
    const score = edge != null ? Math.round(Number(edge)) : null;
    if (score !== null) {
      if (scoreHistRef.current.length === 0) {
        scoreHistRef.current = [Math.max(0, score - 21), Math.max(0, score - 12), score];
      } else {
        const last = scoreHistRef.current[scoreHistRef.current.length - 1];
        if (last !== score) scoreHistRef.current = [...scoreHistRef.current, score].slice(-4);
      }
    }
  }, [edge]);

  useEffect(() => {
    if (narration && narration !== lastSpokenRef.current) {
      lastSpokenRef.current = narration; speakRef.current(narration);
    }
  }, [narration]);

  const ask = useCallback(async (q?: string) => {
    const question = (q ?? input).trim();
    if (!question || asking) return;
    setInput(''); setMsgs(m => [...m, mkMsg('user', question)]); setAsking(true);
    try {
      const r = await fetch('/api/assistant', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({ question, ticker }),
      });
      if (r.status === 401) {
        setAuthNeeded(true); setAuthPwd('');
        try { localStorage.removeItem('brain_auth'); } catch {}
        setMsgs(m => [...m, mkMsg('brain', 'Session expired — please re-enter your password.')]);
      } else {
        const j = await r.json();
        const answer = j.answer || j.error || 'No response.';
        speakRef.current(answer); setMsgs(m => [...m, mkMsg('brain', answer)]);
      }
    } catch {
      setMsgs(m => [...m, mkMsg('brain', 'Connection error — please try again.')]);
    } finally {
      setAsking(false); setTimeout(() => inputRef.current?.focus(), 60);
    }
  }, [input, asking, ticker, authHeader]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); }
  };

  const verdictLabel =
    status === 'READY' && dirn === 'Long'  ? 'READY — LONG' :
    status === 'READY' && dirn === 'Short' ? 'READY — SHORT' :
    status === 'READY' ? 'READY TO TRADE' : status;

  const marketStatus = (data?.market_status ?? '') as string;
  const isOpen = /open/i.test(marketStatus);

  // Chip prompts
  const chips =
    status === 'READY'    ? ['Walk me through this setup.', 'What invalidates the trade?', "Where's my stop?"] :
    status === 'MANAGING' ? ['How is the trade going?', 'When do you exit?', 'Is thesis still valid?'] :
    ['Why are you waiting?', 'Show me the evidence.', 'What changes your mind?'];

  const CSS = `
    @keyframes avBlink  { 0%,87%,100%{transform:scaleY(0);opacity:0} 89%,95%{transform:scaleY(1);opacity:1} }
    @keyframes avBreath { 0%,100%{transform:scale(1)} 50%{transform:scale(1.006)} }
    @keyframes avBob    { 0%,100%{transform:translateY(0)} 50%{transform:translateY(1px)} }
    @keyframes wv       { from{transform:scaleY(0.4)} to{transform:scaleY(1)} }
    @keyframes bDot     { 0%,100%{opacity:1} 50%{opacity:0.3} }
    @keyframes bBounce  { 0%,80%,100%{transform:scale(0)} 40%{transform:scale(1)} }
    @keyframes bPulse   { 0%,100%{opacity:.18} 50%{opacity:.06} }
    @keyframes bBreathe { 0%,100%{transform:scale(1)} 50%{transform:scale(.65)} }
    @keyframes bUp      { from{opacity:0;transform:translateY(5px)} to{opacity:1;transform:translateY(0)} }
    @keyframes glow     { 0%,100%{opacity:.7} 50%{opacity:1} }
    ::-webkit-scrollbar { width:4px; height:4px; }
    ::-webkit-scrollbar-thumb { background:rgba(255,255,255,0.08); border-radius:2px; }
    ::-webkit-scrollbar-track { background:transparent; }
    .brain-input::placeholder { color:rgba(255,255,255,0.20); }
    .brain-input:focus { outline:none; }
    .input-wrap:focus-within { border-color:rgba(59,130,246,0.35)!important; box-shadow:0 0 0 1px rgba(59,130,246,0.10); }
    /* Sidebar scroll */
    .l-sidebar, .r-sidebar { overflow-y:auto; overflow-x:hidden; scrollbar-width:thin; }
    /* Responsive */
    .dash-layout { display:flex; flex-direction:column; flex:1; overflow:hidden; }
    .dash-body   { display:flex; flex:1; overflow:hidden; }
    .l-sidebar   { width:210px; min-width:210px; padding:10px 10px; box-sizing:border-box; border-right:1px solid rgba(255,255,255,0.042); }
    .r-sidebar   { width:275px; min-width:275px; padding:10px 10px; box-sizing:border-box; border-left:1px solid rgba(255,255,255,0.042); }
    .dash-center { flex:1; overflow-y:auto; padding:16px 20px; display:flex; flex-direction:column; }
    .dash-bottom { height:260px; min-height:260px; border-top:1px solid rgba(255,255,255,0.042); display:flex; }
    @media(max-width:1100px){ .r-sidebar{width:230px;min-width:230px;} .l-sidebar{width:185px;min-width:185px;} }
    @media(max-width:820px) { .l-sidebar,.r-sidebar{display:none!important;} .dash-bottom{height:220px;min-height:220px;} }
  `;

  if (authNeeded) return (
    <><style>{CSS}</style><LoginOverlay onSubmit={handleAuth} /></>
  );

  return (
    <div style={{ height: '100vh', background: '#060a0f', color: '#fff', display: 'flex',
      flexDirection: 'column', fontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
      overflow: 'hidden', userSelect: 'none' }}>
      <style>{CSS}</style>

      {/* ── TOP NAV ─────────────────────────────────────────────────────── */}
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 20px', height: 52, borderBottom: '1px solid rgba(255,255,255,0.042)',
        flexShrink: 0, gap: 16 }}>
        {/* Left: Logo + ticker tabs */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ width: 28, height: 28, borderRadius: 7, background: 'rgba(59,130,246,0.20)',
              border: '1px solid rgba(59,130,246,0.35)', display: 'flex', alignItems: 'center',
              justifyContent: 'center', fontSize: 14, fontWeight: 700, color: '#93c5fd' }}>A</div>
            <span style={{ fontSize: 13, fontWeight: 700, color: 'rgba(255,255,255,0.8)',
              letterSpacing: '-0.01em' }}>AI Trading Partner</span>
          </div>
          <div style={{ display: 'flex', gap: 2 }}>
            {(['MNQ', 'MGC', 'MES', 'MYM'] as const).map(t => (
              <button key={t} onClick={() => setTicker(t)} style={{
                padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
                fontSize: 12, fontWeight: 700, fontFamily: 'monospace', letterSpacing: '0.06em',
                background: ticker === t ? 'rgba(59,130,246,0.25)' : 'transparent',
                color: ticker === t ? '#93c5fd' : 'rgba(255,255,255,0.28)',
                border: ticker === t ? '1px solid rgba(59,130,246,0.35)' : '1px solid transparent',
                transition: 'all 0.15s',
              } as React.CSSProperties}>
                {t}
              </button>
            ))}
          </div>
        </div>
        {/* Center: clock + market status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.45)', fontFamily: 'monospace' }}>{clock}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px',
            borderRadius: 20, border: `1px solid ${isOpen ? 'rgba(34,197,94,0.3)' : 'rgba(107,114,128,0.3)'}`,
            background: isOpen ? 'rgba(34,197,94,0.08)' : 'rgba(107,114,128,0.08)' }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%',
              background: isOpen ? BULL : '#6b7280',
              animation: isOpen ? 'glow 2s ease-in-out infinite' : 'none' }} />
            <span style={{ fontSize: 11, color: isOpen ? BULL : '#9ca3af',
              fontFamily: 'monospace', fontWeight: 600, letterSpacing: '0.06em' }}>
              {isOpen ? 'MARKET OPEN' : (marketStatus || 'CLOSED').toUpperCase()}
            </span>
          </div>
        </div>
        {/* Right: voice + engineering */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button onClick={() => setMuted(!muted)} title={muted ? 'Enable voice' : 'Mute voice'}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 16,
              color: muted ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.5)', padding: '4px' }}>
            {muted ? '🔇' : '🔊'}
          </button>
          <a href="/api/dashboard" style={{ fontSize: 11, color: 'rgba(255,255,255,0.18)',
            textDecoration: 'none', fontFamily: 'monospace', letterSpacing: '0.04em' }}
            onMouseEnter={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.5)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.18)')}>
            Engineering →
          </a>
        </div>
      </header>

      {/* ── DASHBOARD BODY ──────────────────────────────────────────────── */}
      <div className="dash-layout">
        <div className="dash-body">

          {/* ── LEFT SIDEBAR ──────────────────────────────────────────── */}
          <div className="l-sidebar">
            <MarketContextPanel data={data} />
            <KeyLevelsPanel data={data} />
            <PositionPanel data={data} />
            <ConfidenceMeter data={data} status={status} />
          </div>

          {/* ── CENTER ────────────────────────────────────────────────── */}
          <div className="dash-center">
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0 }}>

              {/* Avatar */}
              <div style={{ position: 'relative', width: 300, flexShrink: 0 }}>
                {/* Background glow behind avatar */}
                <div style={{ position: 'absolute', top: '20%', left: '10%', right: '10%', bottom: '0',
                  background: `radial-gradient(ellipse at center, ${st.c}22, transparent 70%)`,
                  filter: 'blur(24px)', pointerEvents: 'none', zIndex: 0 }} />

                {/* Avatar image with breathing */}
                <div style={{ position: 'relative', zIndex: 1, borderRadius: 16, overflow: 'hidden',
                  animation: 'avBreath 5s ease-in-out infinite',
                  boxShadow: st.glow }}>
                  <img src="/avatar.png" alt="AI Analyst"
                    style={{ width: '100%', height: 340, objectFit: 'cover', objectPosition: 'center top',
                      display: 'block', filter: `brightness(${st.bri})`,
                      animation: (ttsSpeaking || streaming) ? 'avBob 0.18s ease-in-out infinite' : 'none',
                    }} />

                  {/* Status lighting overlay */}
                  <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none',
                    background: `radial-gradient(ellipse at 25% 25%, ${st.c}28, transparent 60%)`,
                    mixBlendMode: 'soft-light' }} />

                  {/* Blink overlay - positioned over eyes (~38-45% from top of 340px = 130-153px) */}
                  <div style={{ position: 'absolute', top: '37%', left: '18%', right: '18%', height: '7%',
                    background: 'rgba(3,6,14,0.90)', borderRadius: '50%',
                    transform: 'scaleY(0)', transformOrigin: 'center 40%',
                    animation: 'avBlink 5.5s ease-in-out infinite', pointerEvents: 'none' }} />

                  {/* Bottom fade */}
                  <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: '30%',
                    background: 'linear-gradient(to bottom, transparent, #060a0f)',
                    pointerEvents: 'none' }} />

                  {/* Status badge */}
                  <div style={{ position: 'absolute', bottom: 10, left: '50%', transform: 'translateX(-50%)',
                    padding: '3px 12px', borderRadius: 12, fontSize: 10, fontFamily: 'monospace',
                    fontWeight: 700, letterSpacing: '0.10em', color: st.a,
                    background: `${st.c}22`, border: `1px solid ${st.c}44`, whiteSpace: 'nowrap' }}>
                    {st.lbl}
                  </div>
                </div>
              </div>

              {/* ── Brain conversation panel ─────────────────────────── */}
              <div style={{ width: '100%', maxWidth: 520, marginTop: 12,
                background: 'rgba(255,255,255,0.028)', border: '1px solid rgba(255,255,255,0.065)',
                borderRadius: 14, padding: '16px 18px' }}>

                {/* Brain header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <div style={{ width: 5, height: 5, borderRadius: '50%', background: st.c,
                      animation: 'glow 2s ease-in-out infinite' }} />
                    <span style={{ fontSize: 11, fontWeight: 700, color: '#93c5fd',
                      fontFamily: 'monospace', letterSpacing: '0.10em' }}>AI TRADING PARTNER</span>
                  </div>
                  <span style={{ fontSize: 11, color: status === 'READY' ? BULL : AMB,
                    fontFamily: 'monospace', letterSpacing: '0.08em' }}>
                    {loading ? 'CONNECTING...' :
                      status === 'READY' ? 'READY' :
                      status === 'MANAGING' ? 'MANAGING' :
                      status === 'BUILDING' ? 'THINKING...' : 'WATCHING...'}
                  </span>
                  {loading && (
                    <div style={{ display: 'flex', gap: 4, marginLeft: 4 }}>
                      {[0, 1, 2].map(i => (
                        <div key={i} style={{ width: 5, height: 5, borderRadius: '50%', background: '#374151',
                          animation: `bBounce 1.4s ease-in-out ${i * 0.16}s infinite` }} />
                      ))}
                    </div>
                  )}
                </div>

                {/* Narration text */}
                {!loading && narration && (
                  <p style={{ fontSize: 14, lineHeight: 1.72, color: 'rgba(255,255,255,0.72)',
                    fontWeight: 300, margin: '0 0 12px', whiteSpace: 'pre-wrap' }}>
                    {displayed}
                    {streaming && <span style={{ display: 'inline-block', width: 2, height: 14,
                      background: 'rgba(255,255,255,0.45)', marginLeft: 2, verticalAlign: 'middle',
                      animation: 'bDot 0.9s ease-in-out infinite' }} />}
                  </p>
                )}

                {/* Gate checklist */}
                {checklist.length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 12 }}>
                    {checklist.map((item, i) => {
                      const { icon, color } = clIcon(item.st);
                      return (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8,
                          animation: 'bUp 0.3s ease-out' }}>
                          <span style={{ fontSize: 11, color, fontFamily: 'monospace', width: 12, flexShrink: 0 }}>{icon}</span>
                          <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.55)' }}>{item.text}</span>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Confidence progression */}
                {scoreHistRef.current.length > 0 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '10px 0' }}>
                    <span style={{ fontSize: 11, color: MUTED, fontFamily: 'monospace' }}>Confidence</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      {scoreHistRef.current.map((s, i) => (
                        <React.Fragment key={i}>
                          <span style={{
                            fontSize: 13, fontFamily: 'monospace', fontWeight: 700,
                            color: i === scoreHistRef.current.length - 1 ? st.c : 'rgba(255,255,255,0.35)',
                          }}>{s}</span>
                          {i < scoreHistRef.current.length - 1 && (
                            <span style={{ color: 'rgba(255,255,255,0.20)', fontSize: 11 }}>→</span>
                          )}
                        </React.Fragment>
                      ))}
                      <span style={{ color: 'rgba(255,255,255,0.20)', fontSize: 11 }}>→</span>
                      <span style={{ color: MUTED, fontSize: 11 }}>…</span>
                    </div>
                  </div>
                )}

                {/* Wait reason */}
                {strictR && status !== 'READY' && status !== 'MANAGING' && (
                  <div style={{ fontSize: 12, color: AMB, fontFamily: 'inherit', lineHeight: 1.6,
                    padding: '8px 12px', background: 'rgba(245,158,11,0.06)',
                    border: '1px solid rgba(245,158,11,0.15)', borderRadius: 8, margin: '8px 0' }}>
                    {strictR.length > 120 ? strictR.slice(0, 118) + '…' : strictR}
                  </div>
                )}

                {/* Learning note */}
                {lm.available && lm.note && (
                  <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.28)', fontFamily: 'monospace',
                    margin: '6px 0', letterSpacing: '0.02em' }}>{lm.note}</div>
                )}

                {/* Chat history */}
                {msgs.length > 0 && (
                  <div ref={chatRef} style={{ maxHeight: 200, overflowY: 'auto', display: 'flex',
                    flexDirection: 'column', gap: 8, marginTop: 10, marginBottom: 10 }}>
                    {msgs.map(m => <BrainBubble key={m.id} msg={m} />)}
                    {asking && (
                      <div style={{ display: 'flex', gap: 5, padding: '10px 14px',
                        borderRadius: '16px 16px 16px 3px', background: 'rgba(255,255,255,0.04)', width: 'fit-content' }}>
                        {[0, 1, 2].map(i => (
                          <div key={i} style={{ width: 6, height: 6, borderRadius: '50%', background: '#374151',
                            animation: `bBounce 1.4s ease-in-out ${i * 0.16}s infinite` }} />
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Quick chips */}
                {msgs.length === 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10, marginTop: 6 }}>
                    {chips.map(c => (
                      <button key={c} onClick={() => ask(c)} style={{
                        fontSize: 11, padding: '5px 12px', borderRadius: 14,
                        border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.04)',
                        color: 'rgba(255,255,255,0.45)', cursor: 'pointer', transition: 'all 0.18s', fontFamily: 'inherit'
                      }}
                        onMouseEnter={e => { const t = e.currentTarget; t.style.color = 'rgba(255,255,255,0.8)'; t.style.borderColor = 'rgba(59,130,246,0.4)'; t.style.background = 'rgba(59,130,246,0.08)'; }}
                        onMouseLeave={e => { const t = e.currentTarget; t.style.color = 'rgba(255,255,255,0.45)'; t.style.borderColor = 'rgba(255,255,255,0.12)'; t.style.background = 'rgba(255,255,255,0.04)'; }}>
                        {c}
                      </button>
                    ))}
                  </div>
                )}

                {/* Input bar */}
                <div className="input-wrap" style={{ display: 'flex', alignItems: 'center', gap: 10,
                  border: '1px solid rgba(255,255,255,0.12)', borderRadius: 24,
                  padding: '10px 16px', background: 'rgba(255,255,255,0.025)', transition: 'all 0.2s' }}>
                  <input ref={inputRef} className="brain-input" type="text"
                    value={input} onChange={e => setInput(e.target.value)} onKeyDown={onKey}
                    placeholder="Ask your trading partner..."
                    style={{ flex: 1, background: 'transparent', border: 'none',
                      fontSize: 13, color: 'rgba(255,255,255,0.82)', fontFamily: 'inherit' }} />
                  <button onClick={() => ask()} disabled={!input.trim() || asking}
                    style={{ background: 'transparent', border: 'none', padding: 0, cursor: input.trim() && !asking ? 'pointer' : 'default',
                      color: input.trim() && !asking ? st.c : 'rgba(255,255,255,0.14)', transition: 'color 0.2s', display: 'flex', alignItems: 'center' }}>
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* ── RIGHT SIDEBAR ──────────────────────────────────────────── */}
          <div className="r-sidebar">
            <CurrentAnalysisPanel data={data} status={status} statusLabel={verdictLabel} />
            <EvidenceScorePanel data={data} status={status} />
            <RecentSetupsPanel data={data} />
            <VoiceStatusPanel muted={muted} speaking={ttsSpeaking || streaming}
              voices={voices} voiceName={voiceName} setVoice={setVoice} setMuted={setMuted} st={st} />
          </div>
        </div>

        {/* ── BOTTOM: CHART + TRADE PLAN ─────────────────────────────── */}
        <div className="dash-bottom">
          {/* Chart */}
          <div style={{ flex: 1, padding: '8px 0 0 8px', overflow: 'hidden', minWidth: 0 }}>
            <CandleChart
              candles={candlesRef.current}
              vwap={data?.vwap_value}
              demand={data?.nearest_demand}
              supply={data?.nearest_supply}
              ticker={ticker}
            />
          </div>
          {/* Trade Plan */}
          <div style={{ padding: '10px 10px 10px 8px', flexShrink: 0 }}>
            <TradePlanPanel data={data} status={status} ticker={ticker} authHeader={authHeader} />
          </div>
        </div>
      </div>
    </div>
  );
}
