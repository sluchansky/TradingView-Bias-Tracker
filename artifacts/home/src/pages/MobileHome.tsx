import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';

// ── Constants ─────────────────────────────────────────────────────────────────
const BULL  = '#22c55e';
const BEAR  = '#ef4444';
const AMB   = '#f59e0b';
const MUTED = 'rgba(255,255,255,0.22)';
const BLUE  = '#3b82f6';
const CYAN  = '#38bdf8';
const BG    = '#060810';

type Ticker = 'MNQ' | 'MGC' | 'MES' | 'MYM';
type Tab    = 'signal' | 'brain' | 'chat' | 'position';

const fmt = (n: number | null | undefined, dec = 2) =>
  n != null ? Number(n).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) : '—';

// ── Clock ─────────────────────────────────────────────────────────────────────
function useClock() {
  const [time, setTime] = useState('');
  useEffect(() => {
    const tick = () => setTime(
      new Date().toLocaleTimeString('en-US', {
        hour: 'numeric', minute: '2-digit', second: '2-digit',
        hour12: true, timeZone: 'America/New_York',
      }) + ' ET'
    );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);
  return time;
}

// ── TTS ───────────────────────────────────────────────────────────────────────
function cleanTTS(text: string) {
  return text
    .replace(/\bBOS\b/g,   'break of structure')
    .replace(/\bCHOCH\b/g, 'change of character')
    .replace(/\bVWAP\b/gi, 'vee-wap')
    .replace(/\bCVD\b/g,   'cumulative delta')
    .replace(/\bATR\b/g,   'average true range')
    .replace(/\bMNQ\b/g,   'mini nasdaq')
    .replace(/\bMGC\b/g,   'micro gold')
    .replace(/\bMES\b/g,   'micro S and P')
    .replace(/\bMYM\b/g,   'micro Dow')
    .replace(/\bR:R\b/gi,  'risk reward')
    .slice(0, 420);
}

function useTTS() {
  const [voices, setVoices]   = useState<SpeechSynthesisVoice[]>([]);
  const [muted,  setMutedSt]  = useState<boolean>(() => {
    try { const v = localStorage.getItem('brain_muted'); return v === null ? false : v !== '0'; } catch { return false; }
  });
  const [speaking,      setSpeaking]      = useState(false);
  const [audioUnlocked, setAudioUnlocked] = useState(false);

  // Queue: activeRef = a speak() was sent to the browser, pendingRef = next line (max 1)
  const activeRef  = useRef(false);
  const pendingRef = useRef<string | null>(null);
  const voicesRef  = useRef<SpeechSynthesisVoice[]>([]);
  useEffect(() => { voicesRef.current = voices; }, [voices]);

  useEffect(() => {
    const ss = window.speechSynthesis; if (!ss) return;
    const load = () => { const all = ss.getVoices(); if (all.length) setVoices(all); };
    load(); ss.addEventListener('voiceschanged', load);
    return () => ss.removeEventListener('voiceschanged', load);
  }, []);

  const _fire = useCallback((text: string) => {
    const ss = window.speechSynthesis; if (!ss || !text) return;
    activeRef.current = true;           // block concurrent calls
    const utt = new SpeechSynthesisUtterance(text);
    const voice = voicesRef.current[0]; if (voice) utt.voice = voice;
    utt.rate = 0.90; utt.pitch = 1.05;

    // Safety net: mobile browsers silently reject ss.speak() when there has been no
    // recent user gesture. onstart never fires in that case, so activeRef stays true
    // forever and the queue deadlocks. Reset after 700 ms if onstart hasn't confirmed.
    let started = false;
    const safetyTimer = setTimeout(() => {
      if (!started) { activeRef.current = false; setSpeaking(false); }
    }, 700);

    utt.onstart = () => { started = true; clearTimeout(safetyTimer); setSpeaking(true); };
    const done = () => {
      clearTimeout(safetyTimer);
      activeRef.current = false;
      setSpeaking(false);
      const next = pendingRef.current;
      if (next) { pendingRef.current = null; _fire(next); }
    };
    utt.onend = done;
    utt.onerror = done;
    ss.speak(utt);
  }, []);

  const setMuted = useCallback((m: boolean) => {
    try { localStorage.setItem('brain_muted', m ? '1' : '0'); } catch {}
    if (m) {
      window.speechSynthesis?.cancel();
      activeRef.current = false;
      pendingRef.current = null;
      setSpeaking(false);
    }
    setMutedSt(m);
  }, []);

  const speak = useCallback((text: string) => {
    if (!text || muted) return;
    const cleaned = cleanTTS(text);
    if (activeRef.current) {
      pendingRef.current = cleaned;   // don't interrupt — queue for after current finishes
    } else {
      _fire(cleaned);
    }
  }, [muted, _fire]);

  const unlockAudio = useCallback(() => {
    const ss = window.speechSynthesis; if (!ss) return;
    const w = new SpeechSynthesisUtterance(''); w.volume = 0; ss.speak(w);
    setAudioUnlocked(true);
  }, []);

  useEffect(() => {
    const h = () => unlockAudio();
    document.addEventListener('touchstart', h, { once: true, passive: true });
    return () => document.removeEventListener('touchstart', h);
  }, [unlockAudio]);

  return { muted, setMuted, speaking, speak, unlockAudio, audioUnlocked };
}

// ── Voice input ───────────────────────────────────────────────────────────────
type VoiceState = 'idle' | 'listening' | 'processing' | 'error';

function useVoiceInput(onTranscript: (t: string) => void) {
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const recRef   = useRef<any>(null);
  const finalRef = useRef('');

  const start = useCallback(async () => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) { setVoiceState('error'); return; }
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true });
      setVoiceState('listening');
      finalRef.current = '';
      const rec = new SR();
      recRef.current = rec;
      rec.continuous = false; rec.interimResults = false; rec.lang = 'en-US';
      rec.onresult = (e: any) => { for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) finalRef.current += e.results[i][0].transcript; };
      rec.onend = () => {
        const t = finalRef.current.trim();
        if (t) { setVoiceState('processing'); onTranscript(t); }
        else setVoiceState('idle');
      };
      rec.onerror = () => setVoiceState('error');
      rec.start();
    } catch { setVoiceState('error'); }
  }, [onTranscript]);

  const stop = useCallback(() => { try { recRef.current?.stop(); } catch {} }, []);

  return { voiceState, setVoiceState, start, stop };
}

// ── Auth ──────────────────────────────────────────────────────────────────────
function useAuth() {
  const [pwd, setPwd]         = useState<string>('');
  const [authed, setAuthed]   = useState(false);
  const [checking, setCheck]  = useState(true);

  const authHeader = useMemo(() =>
    pwd ? { 'Authorization': 'Basic ' + btoa('admin:' + pwd) } : {} as Record<string, string>,
  [pwd]);

  const tryAuth = useCallback(async (p: string) => {
    try {
      const r = await fetch('/api/status', { headers: { 'Authorization': 'Basic ' + btoa('admin:' + p) }, credentials: 'include' });
      if (r.ok) { setPwd(p); setAuthed(true); try { localStorage.setItem('brain_auth', p); } catch {} }
      else setAuthed(false);
    } catch { setAuthed(false); }
    setCheck(false);
  }, []);

  useEffect(() => {
    try {
      const saved = localStorage.getItem('brain_auth');
      if (saved) { tryAuth(saved); } else setCheck(false);
    } catch { setCheck(false); }
  }, [tryAuth]);

  return { authed, checking, authHeader, tryAuth, pwd };
}

// ── Live data polling ─────────────────────────────────────────────────────────
function useLiveData(ticker: Ticker, authHeader: Record<string, string>) {
  const [data, setData]     = useState<any>(null);
  const [conn, setConn]     = useState<'ok' | 'err' | 'wait'>('wait');
  const [ts,   setTs]       = useState<number>(0);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      if (!alive) return;
      try {
        const r = await fetch(`/api/status?ticker=${ticker}`, { headers: authHeader, credentials: 'include' });
        if (r.ok) { const j = await r.json(); if (alive) { setData(j); setConn('ok'); setTs(Date.now()); } }
        else if (alive) setConn('err');
      } catch { if (alive) setConn('err'); }
      if (alive) setTimeout(poll, 3000);
    };
    poll();
    return () => { alive = false; };
  }, [ticker, authHeader]);

  return { data, conn, ts };
}

// ── Derived helpers ───────────────────────────────────────────────────────────
function verdictInfo(data: any): { label: string; color: string; bg: string } {
  const mb  = data?.main_brain || {};
  const st  = String(mb.status || data?.verdict || 'WAIT').toUpperCase();
  if (st === 'READY' || st.includes('READY')) {
    const dir = String(mb.direction || data?.direction || '').toUpperCase();
    if (dir.includes('SHORT')) return { label: 'SHORT READY', color: BEAR, bg: 'rgba(239,68,68,0.12)' };
    return { label: 'LONG READY', color: BULL, bg: 'rgba(34,197,94,0.12)' };
  }
  if (st === 'MANAGING') return { label: 'MANAGING',    color: CYAN, bg: 'rgba(56,189,248,0.10)' };
  if (st === 'BUILDING') return { label: 'BUILDING',    color: AMB,  bg: 'rgba(245,158,11,0.10)' };
  return { label: 'WAIT', color: MUTED, bg: 'rgba(255,255,255,0.04)' };
}

function getGates(data: any) {
  const gd = data?.gate_debug || {};
  const ad = data?.alert_diagnostics || {};
  const sig = (data?.main_brain || {}).signals || {};
  const price = Number(data?.current_price || 0);
  const vwap  = Number(data?.vwap_value    || 0);
  const items: Array<{ label: string; pass: boolean | null }> = [];
  if (price > 0 && vwap > 0) items.push({ label: 'VWAP', pass: price > vwap });
  if (gd.structure_confirmed != null) items.push({ label: 'Structure', pass: !!gd.structure_confirmed });
  if (gd.zone_valid != null)          items.push({ label: 'Zone',      pass: gd.zone_valid === true ? true : null });
  const cvd = String(sig.cvd || ad.cvd || '').toLowerCase();
  if (cvd && cvd !== 'unknown') items.push({ label: 'Delta', pass: /bull|pos/.test(cvd) ? true : /bear|neg/.test(cvd) ? false : null });
  const vol = String(ad.volume || '').toLowerCase();
  if (vol) items.push({ label: 'Volume', pass: /strong|high|incr/.test(vol) ? true : /thin|low/.test(vol) ? false : null });
  return items;
}

// ── Login screen ─────────────────────────────────────────────────────────────
function MobileLogin({ onSubmit }: { onSubmit: (p: string) => void }) {
  const [val, setVal] = useState('');
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { setTimeout(() => ref.current?.focus(), 120); }, []);
  return (
    <div style={{ position:'fixed', inset:0, background:BG, display:'flex', flexDirection:'column',
      alignItems:'center', justifyContent:'center', gap:32, padding:24 }}>
      <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:10 }}>
        <div style={{ width:52, height:52, borderRadius:'50%',
          background:'radial-gradient(circle at 35% 35%, rgba(59,130,246,0.22), rgba(0,0,0,0.8))',
          border:'1px solid rgba(59,130,246,0.3)',
          display:'flex', alignItems:'center', justifyContent:'center' }}>
          <div style={{ width:8, height:8, borderRadius:'50%', background:BLUE }} />
        </div>
        <span style={{ fontSize:11, color:'rgba(255,255,255,0.35)', fontFamily:'monospace',
          letterSpacing:'0.14em', textTransform:'uppercase' }}>MAIN BRAIN ACCESS</span>
      </div>
      <form onSubmit={e => { e.preventDefault(); const p = val.trim(); if (p) onSubmit(p); }}
        style={{ display:'flex', flexDirection:'column', gap:12, width:'100%', maxWidth:320 }}>
        <input ref={ref} type="password" value={val} onChange={e => setVal(e.target.value)}
          placeholder="Dashboard password"
          style={{ width:'100%', boxSizing:'border-box',
            background:'rgba(255,255,255,0.05)', border:'1px solid rgba(255,255,255,0.12)',
            borderRadius:12, padding:'14px 16px', fontSize:16, color:'rgba(255,255,255,0.88)',
            fontFamily:'inherit', outline:'none', WebkitAppearance:'none' }} />
        <button type="submit" style={{ width:'100%', padding:'14px', borderRadius:12,
          background:'rgba(59,130,246,0.18)', border:'1px solid rgba(59,130,246,0.35)',
          color:'#93c5fd', fontSize:15, fontFamily:'inherit', cursor:'pointer',
          fontWeight:600, letterSpacing:'0.04em' }}>
          Enter
        </button>
      </form>
    </div>
  );
}

// ── Ticker pill selector ──────────────────────────────────────────────────────
const TICKERS: Ticker[] = ['MNQ', 'MGC', 'MES', 'MYM'];
function TickerBar({ value, onChange }: { value: Ticker; onChange: (t: Ticker) => void }) {
  return (
    <div style={{ display:'flex', gap:6, overflowX:'auto', WebkitOverflowScrolling:'touch',
      scrollbarWidth:'none', padding:'0 2px' }}>
      {TICKERS.map(t => (
        <button key={t} onClick={() => onChange(t)}
          style={{ flexShrink:0, padding:'6px 14px', borderRadius:20,
            background: value === t ? 'rgba(59,130,246,0.22)' : 'rgba(255,255,255,0.05)',
            border: `1px solid ${value === t ? 'rgba(59,130,246,0.5)' : 'rgba(255,255,255,0.10)'}`,
            color: value === t ? '#93c5fd' : 'rgba(255,255,255,0.45)',
            fontSize:12, fontFamily:'monospace', fontWeight:700, cursor:'pointer',
            letterSpacing:'0.06em', transition:'all 0.2s' }}>
          {t}
        </button>
      ))}
    </div>
  );
}

// ── Chat bubble ───────────────────────────────────────────────────────────────
interface Msg { id: number; role: 'user' | 'brain'; text: string; }
let _mid = 0;
const mkMsg = (r: Msg['role'], t: string): Msg => ({ id: ++_mid, role: r, text: t });

function ChatBubble({ msg }: { msg: Msg }) {
  const isBrain = msg.role === 'brain';
  return (
    <div style={{ display:'flex', justifyContent: isBrain ? 'flex-start' : 'flex-end', marginBottom:8 }}>
      <div style={{ maxWidth:'82%', padding:'10px 14px',
        borderRadius: isBrain ? '4px 16px 16px 16px' : '16px 4px 16px 16px',
        background: isBrain ? 'rgba(59,130,246,0.10)' : 'rgba(255,255,255,0.07)',
        border: `1px solid ${isBrain ? 'rgba(59,130,246,0.22)' : 'rgba(255,255,255,0.10)'}`,
        fontSize:14, lineHeight:1.6, color: isBrain ? 'rgba(255,255,255,0.85)' : 'rgba(255,255,255,0.65)' }}>
        {msg.text}
      </div>
    </div>
  );
}

// ── Edge score bar ────────────────────────────────────────────────────────────
function EdgeBar({ score, max = 110, color = BLUE }: { score: number; max?: number; color?: string }) {
  const pct = Math.max(0, Math.min(score, max)) / max;
  const grade = score >= 85 ? 'A+' : score >= 70 ? 'A' : score >= 50 ? 'B' : 'WAIT';
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'baseline' }}>
        <span style={{ fontSize:11, fontFamily:'monospace', color:'rgba(255,255,255,0.38)',
          letterSpacing:'0.10em', textTransform:'uppercase' }}>Edge Score</span>
        <div style={{ display:'flex', alignItems:'baseline', gap:6 }}>
          <span style={{ fontSize:22, fontWeight:700, fontFamily:'monospace', color }}>{Math.round(score)}</span>
          <span style={{ fontSize:11, fontFamily:'monospace', color:'rgba(255,255,255,0.35)' }}>/110</span>
          <span style={{ fontSize:11, fontWeight:700, fontFamily:'monospace', color,
            padding:'1px 6px', borderRadius:4, background:`${color}20`, marginLeft:2 }}>{grade}</span>
        </div>
      </div>
      <div style={{ height:6, borderRadius:3, background:'rgba(255,255,255,0.07)', overflow:'hidden' }}>
        <div style={{ width:`${pct * 100}%`, height:'100%', background:color, borderRadius:3,
          transition:'width 1.2s ease', boxShadow:`0 0 10px ${color}66` }} />
      </div>
    </div>
  );
}

// ── Gate dot row ──────────────────────────────────────────────────────────────
function GateDots({ gates }: { gates: Array<{ label: string; pass: boolean | null }> }) {
  return (
    <div style={{ display:'flex', flexWrap:'wrap', gap:6 }}>
      {gates.map((g, i) => {
        const col = g.pass === true ? BULL : g.pass === false ? BEAR : 'rgba(255,255,255,0.25)';
        const bg  = g.pass === true ? 'rgba(34,197,94,0.10)' : g.pass === false ? 'rgba(239,68,68,0.10)' : 'rgba(255,255,255,0.05)';
        return (
          <div key={i} style={{ display:'flex', alignItems:'center', gap:5, padding:'5px 10px',
            background:bg, border:`1px solid ${col}40`, borderRadius:20 }}>
            <div style={{ width:6, height:6, borderRadius:'50%', background:col,
              boxShadow: g.pass !== null ? `0 0 6px ${col}` : undefined }} />
            <span style={{ fontSize:11, fontFamily:'monospace', fontWeight:700,
              letterSpacing:'0.06em', color: col }}>{g.label}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── Evidence grid ─────────────────────────────────────────────────────────────
type EvSt = 'inactive' | 'neutral' | 'developing' | 'confirmed' | 'invalidated';
const EV_COL: Record<EvSt, string> = {
  inactive:'rgba(255,255,255,0.15)', neutral:BLUE, developing:AMB, confirmed:BULL, invalidated:BEAR,
};
function EvidenceGrid({ data }: { data: any }) {
  const gd  = data?.gate_debug || {};
  const sig = (data?.main_brain || {}).signals || {};
  const ad  = data?.alert_diagnostics || {};
  const pr  = Number(data?.current_price || 0);
  const vw  = Number(data?.vwap_value || 0);
  const cvd = String(sig.cvd || ad.cvd || '').toLowerCase();
  const vol = String(ad.volume || '').toLowerCase();
  const bias = String(sig.bias || '').toLowerCase();
  const edge = Number(data?.edge_score || 0);

  const struct: EvSt = gd.structure_confirmed ? 'confirmed' : bias && bias !== 'neutral' ? 'developing' : 'inactive';
  const vwapS: EvSt  = pr > 0 && vw > 0
    ? gd.vwap_confirmed ? 'confirmed' : pr > vw ? 'developing' : 'invalidated'
    : 'inactive';
  const delta: EvSt  = /bull|pos/.test(cvd) ? 'confirmed' : /bear|neg/.test(cvd) ? 'invalidated' : 'neutral';
  const volume: EvSt = /strong|high/.test(vol) ? 'confirmed' : /incr/.test(vol) ? 'developing' : /thin|low/.test(vol) ? 'inactive' : 'neutral';
  const trend: EvSt  = /bull/.test(bias) ? 'confirmed' : /bear/.test(bias) ? 'invalidated' : bias ? 'neutral' : 'inactive';
  const moment: EvSt = edge >= 75 ? 'confirmed' : edge >= 50 ? 'developing' : edge >= 30 ? 'neutral' : 'inactive';
  const liq: EvSt    = gd.zone_valid ? 'confirmed' : (data?.nearest_demand || data?.nearest_supply) ? 'neutral' : 'inactive';
  const htf: EvSt    = data?.swing_context?.htf_bias_aligned != null ? (data.swing_context.htf_bias_aligned ? 'confirmed' : 'invalidated') : 'inactive';

  const items = [
    { label:'Structure', st: struct }, { label:'VWAP',      st: vwapS  },
    { label:'Delta',     st: delta  }, { label:'Volume',    st: volume  },
    { label:'Trend',     st: trend  }, { label:'Momentum',  st: moment  },
    { label:'Liquidity', st: liq    }, { label:'Higher TF', st: htf     },
  ];

  return (
    <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'6px 10px' }}>
      {items.map((item, i) => {
        const col  = EV_COL[item.st];
        const dim  = item.st === 'inactive';
        const pulse = item.st === 'developing';
        return (
          <div key={i} style={{ display:'flex', alignItems:'center', gap:7, opacity: dim ? 0.3 : 1, transition:'opacity 0.5s' }}>
            <div style={{ width:7, height:7, borderRadius:'50%', flexShrink:0, background:col,
              boxShadow: !dim ? `0 0 8px ${col}80` : undefined,
              animation: pulse ? 'mEvPulse 2.2s ease-in-out infinite' : undefined,
              transition:'background 0.5s, box-shadow 0.5s' }} />
            <span style={{ fontSize:11, fontFamily:'monospace', fontWeight:700,
              letterSpacing:'0.05em', textTransform:'uppercase', color: dim ? 'rgba(255,255,255,0.2)' : col,
              transition:'color 0.5s' }}>
              {item.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Market context rows ───────────────────────────────────────────────────────
function MarketContextRow({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center',
      padding:'8px 0', borderBottom:'1px solid rgba(255,255,255,0.05)' }}>
      <span style={{ fontSize:12, color:'rgba(255,255,255,0.38)', fontFamily:'monospace',
        letterSpacing:'0.08em', textTransform:'uppercase' }}>{label}</span>
      <span style={{ fontSize:12, fontFamily:'monospace', fontWeight:700, color }}>{value}</span>
    </div>
  );
}

// ── Section card wrapper ──────────────────────────────────────────────────────
function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ background:'rgba(255,255,255,0.03)', border:'1px solid rgba(255,255,255,0.07)',
      borderRadius:14, padding:'14px 16px', ...style }}>
      {children}
    </div>
  );
}
function CardLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize:10, fontFamily:'monospace', letterSpacing:'0.12em',
      textTransform:'uppercase', color:'rgba(255,255,255,0.30)', marginBottom:10 }}>
      {children}
    </div>
  );
}

// ── Bottom tab bar ────────────────────────────────────────────────────────────
const TAB_DEF: { id: Tab; icon: string; label: string }[] = [
  { id:'signal',   icon:'⚡', label:'Signal'   },
  { id:'brain',    icon:'🧠', label:'Brain'    },
  { id:'chat',     icon:'💬', label:'Chat'     },
  { id:'position', icon:'📊', label:'Position' },
];

function BottomNav({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  return (
    <div style={{ position:'fixed', bottom:0, left:0, right:0,
      background:'rgba(6,8,16,0.96)', backdropFilter:'blur(16px)',
      borderTop:'1px solid rgba(255,255,255,0.08)',
      display:'flex', paddingBottom:'env(safe-area-inset-bottom)',
      zIndex:100 }}>
      {TAB_DEF.map(t => (
        <button key={t.id} onClick={() => onChange(t.id)}
          style={{ flex:1, display:'flex', flexDirection:'column', alignItems:'center',
            justifyContent:'center', padding:'10px 0 8px', gap:3, border:'none', cursor:'pointer',
            background:'none',
            color: active === t.id ? BLUE : 'rgba(255,255,255,0.30)',
            transition:'color 0.2s' }}>
          <span style={{ fontSize:18, lineHeight:1 }}>{t.icon}</span>
          <span style={{ fontSize:9.5, fontFamily:'monospace', fontWeight:700,
            letterSpacing:'0.08em', textTransform:'uppercase' }}>{t.label}</span>
          {active === t.id && (
            <div style={{ position:'absolute', top:0, width:28, height:2,
              background:BLUE, borderRadius:1, boxShadow:`0 0 8px ${BLUE}` }} />
          )}
        </button>
      ))}
    </div>
  );
}

// ── Inline avatar aura (CSS-only, no canvas) ─────────────────────────────────
function MiniAura({ state, speaking }: { state: string; speaking: boolean }) {
  const col = state === 'READY_LONG' ? BULL : state === 'READY_SHORT' ? BEAR
    : state === 'ACTIVE' ? CYAN : state === 'FORMING' ? AMB
    : state === 'ANALYZING' ? '#f59e0b' : MUTED;
  const bright = ['READY_LONG','READY_SHORT','ACTIVE'].includes(state);
  return (
    <div style={{ position:'relative', width:40, height:40, flexShrink:0 }}>
      {/* Outer ring */}
      <div style={{ position:'absolute', inset:-4, borderRadius:'50%',
        border:`1px solid ${col}35`,
        animation: bright ? 'mAuraOuter 2.4s ease-in-out infinite' : undefined }} />
      {/* Mid ring */}
      <div style={{ position:'absolute', inset:0, borderRadius:'50%',
        border:`1px solid ${col}55`,
        animation: bright ? 'mAuraMid 1.8s ease-in-out infinite' : undefined }} />
      {/* Core */}
      <div style={{ position:'absolute', inset:0, borderRadius:'50%',
        background:`radial-gradient(circle at 38% 38%, ${col}44, ${col}11)`,
        boxShadow: bright ? `0 0 14px ${col}66` : undefined,
        display:'flex', alignItems:'center', justifyContent:'center' }}>
        <div style={{ width:10, height:10, borderRadius:'50%', background:col,
          boxShadow:`0 0 8px ${col}`, opacity: bright ? 1 : 0.5,
          animation: speaking ? 'mPulse 0.6s ease-in-out infinite' : undefined }} />
      </div>
    </div>
  );
}

// ── Avatar status indicator ───────────────────────────────────────────────────
function AvatarStatusBar({ state, narration, speaking }: { state: string; narration: string; speaking: boolean }) {
  const col = state === 'READY_LONG' ? BULL : state === 'READY_SHORT' ? BEAR
    : state === 'ACTIVE' ? CYAN : state === 'FORMING' ? AMB : MUTED;
  return (
    <div style={{ display:'flex', alignItems:'flex-start', gap:12,
      padding:'12px 14px', background:'rgba(255,255,255,0.03)',
      border:'1px solid rgba(255,255,255,0.07)', borderRadius:12 }}>
      <MiniAura state={state} speaking={speaking} />
      <div style={{ flex:1, minWidth:0 }}>
        <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:5 }}>
          <span style={{ fontSize:10, fontFamily:'monospace', fontWeight:700,
            letterSpacing:'0.10em', textTransform:'uppercase', color:col }}>{state.replace(/_/g, ' ')}</span>
          {speaking && (
            <div style={{ display:'flex', gap:2, alignItems:'flex-end', height:12 }}>
              {[0,1,2].map(i => (
                <div key={i} style={{ width:2.5, borderRadius:2,
                  background:col, opacity:0.8,
                  animation:`mWave 0.9s ${i * 0.15}s ease-in-out infinite`,
                  height: 6 + i * 3 }} />
              ))}
            </div>
          )}
        </div>
        <p style={{ fontSize:13, color:'rgba(255,255,255,0.65)', lineHeight:1.5, margin:0,
          display:'-webkit-box', WebkitLineClamp:3, WebkitBoxOrient:'vertical' as any, overflow:'hidden' }}>
          {narration || 'Watching the tape…'}
        </p>
      </div>
    </div>
  );
}

// ── Active position card ──────────────────────────────────────────────────────
function PositionCard({ trade }: { trade: any }) {
  const dir = String(trade.direction || '').toUpperCase();
  const col = /short/i.test(dir) ? BEAR : BULL;
  const entry = Number(trade.entry_price || 0);
  const stop  = Number(trade.stop_price  || 0);
  const t1    = Number(trade.target1     || 0);
  const curR  = Number(trade.current_r   || 0);
  const pnl   = Number(trade.unrealized_pnl || 0);
  const opened = trade.opened_at ? new Date(trade.opened_at).toLocaleTimeString('en-US',
    { hour:'2-digit', minute:'2-digit', hour12:true, timeZone:'America/New_York' }) : '—';
  return (
    <Card>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <div style={{ width:8, height:8, borderRadius:'50%', background:col,
            boxShadow:`0 0 8px ${col}`, animation:'mPulse 2s ease-in-out infinite' }} />
          <span style={{ fontSize:13, fontFamily:'monospace', fontWeight:700, color:col,
            letterSpacing:'0.08em' }}>{dir} ACTIVE</span>
        </div>
        <span style={{ fontSize:11, color:'rgba(255,255,255,0.30)', fontFamily:'monospace' }}>since {opened}</span>
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'8px 16px' }}>
        {[
          { label:'Entry', value:fmt(entry) },
          { label:'Stop',  value:fmt(stop),  col:BEAR },
          { label:'Target', value:fmt(t1),   col:BULL },
          { label:'Current R', value:(curR >= 0 ? '+' : '') + fmt(curR, 2) + 'R',
            col: curR >= 0 ? BULL : BEAR },
        ].map((r, i) => (
          <div key={i}>
            <div style={{ fontSize:9.5, fontFamily:'monospace', letterSpacing:'0.10em',
              textTransform:'uppercase', color:'rgba(255,255,255,0.28)', marginBottom:2 }}>{r.label}</div>
            <div style={{ fontSize:15, fontFamily:'monospace', fontWeight:700,
              color:r.col || 'rgba(255,255,255,0.78)' }}>{r.value}</div>
          </div>
        ))}
      </div>
      {pnl !== 0 && (
        <div style={{ marginTop:10, padding:'8px 12px', borderRadius:8,
          background: pnl > 0 ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
          border:`1px solid ${pnl > 0 ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)'}` }}>
          <span style={{ fontSize:12, fontFamily:'monospace', fontWeight:700,
            color: pnl > 0 ? BULL : BEAR }}>
            Unrealized: {pnl > 0 ? '+' : ''}${fmt(pnl, 0)}
          </span>
        </div>
      )}
    </Card>
  );
}

// ── Trade plan card (entry / stop / targets) ──────────────────────────────────
function TradePlanCard({ data }: { data: any }) {
  const tp  = data?.trade_plan || {};
  const mb  = data?.main_brain || {};
  const dir = String(mb.direction || tp.direction || '').toUpperCase();
  const isShort = /short/i.test(dir);
  const accent  = isShort ? BEAR : BULL;

  const entry   = Number(tp.entry   || tp.entry_price  || 0);
  const stop    = Number(tp.stop    || tp.stop_price    || 0);
  const target1 = Number(tp.target1 || tp.target_1      || 0);
  const target2 = Number(tp.target2 || tp.target_2      || 0);
  const rr      = tp.rr_display ?? (tp.rr_num != null ? `1:${Number(tp.rr_num).toFixed(1)}` : null);
  const contracts = tp.contracts != null ? String(tp.contracts) : null;

  if (!entry && !stop && !target1) return null;

  const risk   = entry > 0 && stop > 0   ? Math.abs(entry - stop)    : null;
  const reward = entry > 0 && target1 > 0 ? Math.abs(target1 - entry) : null;

  return (
    <div style={{
      background: `linear-gradient(135deg, ${accent}0d 0%, rgba(6,8,16,0.9) 60%)`,
      border: `1.5px solid ${accent}40`,
      borderRadius: 16,
      padding: '16px',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Glow streak top-right */}
      <div style={{ position:'absolute', top:0, right:0, width:80, height:80,
        background:`radial-gradient(circle at 80% 20%, ${accent}22, transparent 70%)`,
        pointerEvents:'none' }} />

      {/* Header row */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:14 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <div style={{ width:8, height:8, borderRadius:'50%', background:accent,
            boxShadow:`0 0 10px ${accent}`, animation:'mPulse 1.8s ease-in-out infinite' }} />
          <span style={{ fontSize:11, fontFamily:'monospace', fontWeight:800,
            letterSpacing:'0.12em', textTransform:'uppercase', color:accent }}>
            TRADE PLAN · {dir || 'READY'}
          </span>
        </div>
        <div style={{ display:'flex', gap:6 }}>
          {rr && (
            <span style={{ fontSize:10, fontFamily:'monospace', fontWeight:700,
              padding:'3px 8px', borderRadius:10,
              background:`${accent}18`, border:`1px solid ${accent}35`, color:accent }}>
              R:R {rr}
            </span>
          )}
          {contracts && (
            <span style={{ fontSize:10, fontFamily:'monospace', fontWeight:700,
              padding:'3px 8px', borderRadius:10,
              background:'rgba(255,255,255,0.06)', border:'1px solid rgba(255,255,255,0.12)',
              color:'rgba(255,255,255,0.60)' }}>
              {contracts} ct
            </span>
          )}
        </div>
      </div>

      {/* Price levels */}
      <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
        {/* Entry */}
        {entry > 0 && (
          <PlanRow
            label="Entry"
            value={fmt(entry)}
            color={AMB}
            icon="→"
            note="Ideal entry zone"
            isFirst
          />
        )}
        {/* Stop */}
        {stop > 0 && (
          <PlanRow
            label="Stop Loss"
            value={fmt(stop)}
            color={BEAR}
            icon="✕"
            note={risk ? `${fmt(risk, 1)} pts risk` : 'Max loss level'}
          />
        )}
        {/* Target 1 */}
        {target1 > 0 && (
          <PlanRow
            label="Take Profit 1"
            value={fmt(target1)}
            color={BULL}
            icon="✓"
            note={reward ? `+${fmt(reward, 1)} pts · ${rr ?? ''}` : 'First target'}
          />
        )}
        {/* Target 2 / Runner */}
        {target2 > 0 && target2 !== target1 && (
          <PlanRow
            label="Take Profit 2"
            value={fmt(target2)}
            color='#86efac'
            icon="★"
            note="Runner / extension"
            isLast
          />
        )}
      </div>

      {/* Visual risk-reward bar */}
      {risk != null && reward != null && (
        <div style={{ marginTop:14 }}>
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
            <span style={{ fontSize:9, fontFamily:'monospace', color:'rgba(255,255,255,0.28)',
              letterSpacing:'0.08em', textTransform:'uppercase' }}>Risk</span>
            <span style={{ fontSize:9, fontFamily:'monospace', color:'rgba(255,255,255,0.28)',
              letterSpacing:'0.08em', textTransform:'uppercase' }}>Reward</span>
          </div>
          <div style={{ display:'flex', height:6, borderRadius:3, overflow:'hidden', gap:1 }}>
            <div style={{ flex:1, background:`${BEAR}60`, borderRadius:'3px 0 0 3px' }} />
            <div style={{ flex: Math.min(reward / risk, 6), background:`${BULL}70`,
              borderRadius:'0 3px 3px 0',
              boxShadow:`0 0 8px ${BULL}50` }} />
          </div>
          <div style={{ display:'flex', justifyContent:'space-between', marginTop:3 }}>
            <span style={{ fontSize:9, fontFamily:'monospace', color:BEAR }}>−{fmt(risk, 1)}</span>
            <span style={{ fontSize:9, fontFamily:'monospace', color:BULL }}>+{fmt(reward, 1)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function PlanRow({ label, value, color, icon, note, isFirst, isLast }: {
  label: string; value: string; color: string; icon: string; note: string;
  isFirst?: boolean; isLast?: boolean;
}) {
  return (
    <div style={{
      display:'flex', alignItems:'center', gap:10,
      padding:'10px 0',
      borderTop: isFirst ? 'none' : '1px solid rgba(255,255,255,0.06)',
    }}>
      {/* Icon dot */}
      <div style={{ width:28, height:28, borderRadius:'50%', flexShrink:0,
        background:`${color}15`, border:`1px solid ${color}35`,
        display:'flex', alignItems:'center', justifyContent:'center',
        fontSize:11, color, fontWeight:700 }}>
        {icon}
      </div>
      {/* Label + note */}
      <div style={{ flex:1, minWidth:0 }}>
        <div style={{ fontSize:10, fontFamily:'monospace', letterSpacing:'0.08em',
          textTransform:'uppercase', color:'rgba(255,255,255,0.35)', marginBottom:1 }}>{label}</div>
        <div style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.30)' }}>{note}</div>
      </div>
      {/* Value */}
      <span style={{ fontSize:16, fontFamily:'monospace', fontWeight:800,
        color, letterSpacing:'0.02em', flexShrink:0 }}>{value}</span>
    </div>
  );
}

// ── Signal tab ────────────────────────────────────────────────────────────────
function SignalTab({ data, ticker, narration, avatarState, speaking }: {
  data: any; ticker: Ticker; narration: string; avatarState: string; speaking: boolean;
}) {
  const { label, color, bg } = verdictInfo(data);
  const gates = getGates(data);
  const edge  = Number(data?.edge_score || 0);
  const price = Number(data?.current_price || 0);
  const vwap  = Number(data?.vwap_value   || 0);
  const atr   = Number(data?.atr_pts || data?.current_atr || 0);
  const mb    = data?.main_brain || {};
  const reason = data?.strict_reason || mb.what_now || '';
  const isReady = label.includes('READY') || label === 'MANAGING';

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      {/* Verdict pill */}
      <div style={{ display:'flex', justifyContent:'center', paddingTop:4 }}>
        <div style={{ padding:'12px 28px', borderRadius:50,
          background:bg, border:`1.5px solid ${color}40`,
          boxShadow:`0 0 24px ${color}28` }}>
          <span style={{ fontSize:18, fontFamily:'monospace', fontWeight:800,
            letterSpacing:'0.10em', color }}>{label}</span>
        </div>
      </div>

      {/* ── TRADE PLAN: shown prominently when READY ── */}
      {isReady && data?.trade_plan && <TradePlanCard data={data} />}

      {/* Avatar bar */}
      <AvatarStatusBar state={avatarState} narration={narration} speaking={speaking} />

      {/* Edge score */}
      <Card><EdgeBar score={edge} color={color === MUTED ? BLUE : color} /></Card>

      {/* Price / VWAP */}
      <Card>
        <CardLabel>Price · VWAP · ATR</CardLabel>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:8 }}>
          {[
            { label:'Price',   value: price > 0 ? fmt(price) : '—',  col:'rgba(255,255,255,0.85)' },
            { label:'VWAP',    value: vwap > 0  ? fmt(vwap)  : '—',  col: price > 0 && vwap > 0 ? (price > vwap ? BULL : BEAR) : MUTED },
            { label:'ATR',     value: atr > 0   ? fmt(atr)   : '—',  col: AMB },
          ].map((r, i) => (
            <div key={i} style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:2 }}>
              <span style={{ fontSize:9, fontFamily:'monospace', letterSpacing:'0.10em',
                textTransform:'uppercase', color:'rgba(255,255,255,0.28)' }}>{r.label}</span>
              <span style={{ fontSize:14, fontFamily:'monospace', fontWeight:700, color:r.col }}>{r.value}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Gates */}
      {gates.length > 0 && (
        <Card>
          <CardLabel>Gate Checklist</CardLabel>
          <GateDots gates={gates} />
        </Card>
      )}

      {/* Reason */}
      {reason && (
        <Card>
          <CardLabel>Why {label}</CardLabel>
          <p style={{ fontSize:13, color:'rgba(255,255,255,0.60)', lineHeight:1.6, margin:0 }}>{reason}</p>
        </Card>
      )}
    </div>
  );
}

// ── Brain tab ─────────────────────────────────────────────────────────────────
function BrainTab({ data }: { data: any }) {
  const mb  = data?.main_brain || {};
  const views = [
    { label:'What I See',      text: mb.what_i_see    },
    { label:'Thinking',        text: mb.thinking      },
    { label:'Watching For',    text: mb.watching_for  },
    { label:'Plan',            text: mb.plan          },
  ].filter(v => v.text);
  const ad  = data?.alert_diagnostics || {};
  const sig = mb.signals || {};

  const ctx = [
    { label:'Trend',      value: String(sig.bias || ad.trend || 'Neutral') },
    { label:'Momentum',   value: String(ad.momentum || '—') },
    { label:'Volatility', value: String(ad.volatility_regime || ad.volatility || 'Normal') },
    { label:'Liquidity',  value: String(ad.liquidity || '—') },
    { label:'CVD',        value: String(sig.cvd || ad.cvd || '—') },
    { label:'Volume',     value: String(ad.volume || '—') },
  ].filter(r => r.value !== '—' && r.value !== 'undefined' && r.value !== 'null');

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      {/* AI views */}
      {views.length > 0 && (
        <Card>
          <CardLabel>Main Brain Analysis</CardLabel>
          <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
            {views.map((v, i) => (
              <div key={i}>
                <div style={{ fontSize:9.5, fontFamily:'monospace', fontWeight:700,
                  letterSpacing:'0.10em', color:BLUE, marginBottom:3, textTransform:'uppercase' }}>{v.label}</div>
                <p style={{ fontSize:13, color:'rgba(255,255,255,0.68)', lineHeight:1.55, margin:0 }}>{v.text}</p>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Evidence */}
      {data && (
        <Card>
          <CardLabel>Evidence Radar</CardLabel>
          <EvidenceGrid data={data} />
        </Card>
      )}

      {/* Market context */}
      {ctx.length > 0 && (
        <Card>
          <CardLabel>Market Context</CardLabel>
          {ctx.map((r, i) => {
            const col = /bull|long|pos|strong|above|high/i.test(r.value) ? BULL
              : /bear|short|neg|weak|below|low/i.test(r.value) ? BEAR
              : /elev|ext|extreme/i.test(r.value) ? AMB
              : 'rgba(255,255,255,0.65)';
            return <MarketContextRow key={i} label={r.label} value={r.value} color={col} />;
          })}
        </Card>
      )}
    </div>
  );
}

// ── Chat tab ──────────────────────────────────────────────────────────────────
function ChatTab({ authHeader, ticker, speak }: {
  authHeader: Record<string, string>; ticker: Ticker; speak: (t: string) => void;
}) {
  const [msgs,   setMsgs]   = useState<Msg[]>([]);
  const [input,  setInput]  = useState('');
  const [asking, setAsking] = useState(false);
  const msgEnd = useRef<HTMLDivElement>(null);

  const onTranscript = useCallback((t: string) => { setInput(t); }, []);
  const { voiceState, setVoiceState, start, stop } = useVoiceInput(onTranscript);

  useEffect(() => { msgEnd.current?.scrollIntoView({ behavior:'smooth' }); }, [msgs]);

  const ask = useCallback(async (q?: string) => {
    const question = (q ?? input).trim();
    if (!question || asking) return;
    setInput('');
    setMsgs(m => [...m, mkMsg('user', question)]);
    setAsking(true);
    try {
      const r = await fetch('/api/assistant', {
        method:'POST', credentials:'include',
        headers:{ 'Content-Type':'application/json', ...authHeader },
        body:JSON.stringify({ question, ticker }),
      });
      if (r.ok) {
        const j = await r.json();
        const ans = j.answer || j.error || 'No response.';
        speak(ans);
        setMsgs(m => [...m, mkMsg('brain', ans)]);
      } else {
        setMsgs(m => [...m, mkMsg('brain', 'Could not connect. Try again.')]);
      }
    } catch {
      setMsgs(m => [...m, mkMsg('brain', 'Connection error.')]);
    } finally { setAsking(false); }
  }, [input, asking, ticker, authHeader, speak]);

  const chips = ['Read the tape.', 'What are you waiting for?', 'What is missing?', 'Conviction level?'];

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:0, height:'100%' }}>
      {/* Chip buttons */}
      <div style={{ display:'flex', gap:6, overflowX:'auto', paddingBottom:10,
        scrollbarWidth:'none', WebkitOverflowScrolling:'touch' }}>
        {chips.map((c, i) => (
          <button key={i} onClick={() => ask(c)}
            style={{ flexShrink:0, padding:'7px 12px', borderRadius:20,
              background:'rgba(59,130,246,0.10)', border:'1px solid rgba(59,130,246,0.25)',
              color:'#93c5fd', fontSize:12, fontFamily:'inherit', cursor:'pointer',
              whiteSpace:'nowrap' }}>
            {c}
          </button>
        ))}
      </div>

      {/* Messages */}
      <div style={{ flex:1, overflowY:'auto', paddingBottom:8 }}>
        {msgs.length === 0 && (
          <div style={{ textAlign:'center', padding:'32px 0',
            color:'rgba(255,255,255,0.25)', fontSize:13, fontFamily:'monospace' }}>
            Ask me anything about the current setup.
          </div>
        )}
        {msgs.map(m => <ChatBubble key={m.id} msg={m} />)}
        {asking && (
          <div style={{ display:'flex', justifyContent:'flex-start', marginBottom:8 }}>
            <div style={{ padding:'10px 14px', borderRadius:'4px 16px 16px 16px',
              background:'rgba(59,130,246,0.10)', border:'1px solid rgba(59,130,246,0.22)' }}>
              <div style={{ display:'flex', gap:4, alignItems:'center' }}>
                {[0,1,2].map(i => <div key={i} style={{ width:5, height:5, borderRadius:'50%',
                  background:BLUE, animation:`mDot 1s ${i*0.2}s ease-in-out infinite` }} />)}
              </div>
            </div>
          </div>
        )}
        <div ref={msgEnd} />
      </div>

      {/* Input row */}
      <div style={{ display:'flex', gap:8, paddingTop:8,
        borderTop:'1px solid rgba(255,255,255,0.07)' }}>
        <input value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }}
          placeholder="Ask anything…"
          style={{ flex:1, background:'rgba(255,255,255,0.05)', border:'1px solid rgba(255,255,255,0.12)',
            borderRadius:12, padding:'12px 14px', fontSize:15, color:'rgba(255,255,255,0.85)',
            fontFamily:'inherit', outline:'none', WebkitAppearance:'none' }} />
        <button onClick={() => {
          if (voiceState === 'listening') stop();
          else start();
        }}
          style={{ width:46, height:46, borderRadius:12, flexShrink:0,
            background: voiceState === 'listening' ? 'rgba(239,68,68,0.2)' : 'rgba(255,255,255,0.07)',
            border:`1px solid ${voiceState === 'listening' ? 'rgba(239,68,68,0.4)' : 'rgba(255,255,255,0.12)'}`,
            display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer',
            fontSize:18, transition:'all 0.2s' }}>
          {voiceState === 'listening' ? '⏹' : '🎤'}
        </button>
        <button onClick={() => ask()} disabled={!input.trim() || asking}
          style={{ width:46, height:46, borderRadius:12, flexShrink:0,
            background: input.trim() ? 'rgba(59,130,246,0.25)' : 'rgba(255,255,255,0.05)',
            border:`1px solid ${input.trim() ? 'rgba(59,130,246,0.45)' : 'rgba(255,255,255,0.10)'}`,
            display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer',
            fontSize:18, transition:'all 0.2s', opacity: asking ? 0.5 : 1 }}>
          ➤
        </button>
      </div>

      {voiceState === 'listening' && (
        <div style={{ textAlign:'center', fontSize:11, color:BEAR, fontFamily:'monospace',
          paddingTop:4, letterSpacing:'0.08em', animation:'mPulse 1.4s ease-in-out infinite' }}>
          ● LISTENING
        </div>
      )}
    </div>
  );
}

// ── Position tab ──────────────────────────────────────────────────────────────
function PositionTab({ data, muted, setMuted }: {
  data: any; muted: boolean; setMuted: (m: boolean) => void;
}) {
  const trade  = data?.active_trade;
  const recent = data?.recent_trades || [];
  const mb     = data?.main_brain || {};
  const perf   = mb.session_performance || mb.today_performance || null;

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      {/* Active trade */}
      {trade ? (
        <PositionCard trade={trade} />
      ) : (
        <Card>
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:8, padding:'12px 0' }}>
            <span style={{ fontSize:24 }}>⏳</span>
            <span style={{ fontSize:13, color:MUTED, fontFamily:'monospace' }}>No active position</span>
          </div>
        </Card>
      )}

      {/* Recent trades */}
      {recent.length > 0 && (
        <Card>
          <CardLabel>Recent Trades</CardLabel>
          <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
            {recent.slice(0, 5).map((t: any, i: number) => {
              const win = t.outcome === 'win';
              const col = win ? BULL : t.outcome === 'loss' ? BEAR : AMB;
              return (
                <div key={i} style={{ display:'flex', alignItems:'center', gap:10,
                  padding:'8px 0', borderBottom: i < Math.min(4, recent.length - 1) ? '1px solid rgba(255,255,255,0.05)' : 'none' }}>
                  <div style={{ width:7, height:7, borderRadius:'50%', flexShrink:0, background:col }} />
                  <span style={{ flex:1, fontSize:12, fontFamily:'monospace', color:'rgba(255,255,255,0.55)' }}>
                    {String(t.direction || '').toUpperCase()} {String(t.instrument || '')}
                  </span>
                  <span style={{ fontSize:12, fontFamily:'monospace', fontWeight:700, color:col }}>
                    {t.r_multiple != null ? (t.r_multiple >= 0 ? '+' : '') + fmt(t.r_multiple, 2) + 'R' : String(t.outcome || '').toUpperCase()}
                  </span>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* Session performance */}
      {perf && (
        <Card>
          <CardLabel>Session Performance</CardLabel>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'8px 16px' }}>
            {[
              { label:'Win Rate',   value: perf.win_rate != null ? fmt(perf.win_rate * 100, 0) + '%' : '—' },
              { label:'Avg R',      value: perf.avg_r    != null ? fmt(perf.avg_r, 2) + 'R'           : '—' },
              { label:'Trades',     value: perf.total_trades != null ? String(perf.total_trades)       : '—' },
              { label:'P&L',        value: perf.total_pnl != null ? '$' + fmt(perf.total_pnl, 0)      : '—' },
            ].map((r, i) => (
              <div key={i}>
                <div style={{ fontSize:9.5, fontFamily:'monospace', letterSpacing:'0.10em',
                  textTransform:'uppercase', color:'rgba(255,255,255,0.28)', marginBottom:2 }}>{r.label}</div>
                <div style={{ fontSize:15, fontFamily:'monospace', fontWeight:700,
                  color:'rgba(255,255,255,0.80)' }}>{r.value}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Voice mute toggle */}
      <Card>
        <CardLabel>Voice Settings</CardLabel>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
          <span style={{ fontSize:13, color:'rgba(255,255,255,0.55)' }}>Avatar voice</span>
          <button onClick={() => setMuted(!muted)}
            style={{ padding:'8px 18px', borderRadius:20,
              background: muted ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)',
              border:`1px solid ${muted ? 'rgba(239,68,68,0.35)' : 'rgba(34,197,94,0.35)'}`,
              color: muted ? BEAR : BULL, fontSize:12, fontFamily:'monospace',
              fontWeight:700, cursor:'pointer', letterSpacing:'0.06em' }}>
            {muted ? 'MUTED' : 'ON'}
          </button>
        </div>
      </Card>
    </div>
  );
}

// ── CSS keyframes ─────────────────────────────────────────────────────────────
const MOBILE_CSS = `
@keyframes mWave { 0%,100%{transform:scaleY(0.4)} 50%{transform:scaleY(1)} }
@keyframes mPulse { 0%,100%{opacity:0.6} 50%{opacity:1} }
@keyframes mEvPulse { 0%,100%{opacity:0.6;transform:scale(1)} 50%{opacity:1;transform:scale(1.35)} }
@keyframes mDot { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-4px)} }
@keyframes bPulse { 0%,100%{opacity:0.3} 50%{opacity:0.7} }
@keyframes mAuraOuter { 0%,100%{opacity:0.3;transform:scale(1)} 50%{opacity:0.7;transform:scale(1.08)} }
@keyframes mAuraMid { 0%,100%{opacity:0.4;transform:scale(1)} 50%{opacity:0.9;transform:scale(1.04)} }
`;

// ── Voice bank ────────────────────────────────────────────────────────────────
const M_VOICE: Record<string, string[]> = {
  WAIT: [
    'No edge present. Standing aside and watching the tape.',
    'Conditions are not meeting my criteria right now. Patience is the trade.',
    'Nothing to do here but wait for the market to show its hand.',
    'Too many conflicting signals. When in doubt, stay out.',
    'The market is not offering anything clean at the moment. I can wait all day.',
    'Price is in a choppy range. No trend to trade, no setup to take.',
    'My job right now is to do nothing. Discipline means knowing when to sit on your hands.',
    'I see noise, not signal. The edge is not there yet.',
    'Markets can go sideways for a long time. I am not forcing anything.',
    'Waiting is a position. Right now, cash is my best trade.',
    'Structure is unclear. I need to see something definitive before committing capital.',
    'Volume is thin and direction is absent. This is a trap for impatient traders.',
    'Not every session has a trade. Today might be one of those days and that is perfectly fine.',
    'The tape is giving me mixed messages. I will wait for clarity.',
    'Risk versus reward does not favor anything right now. Standing aside.',
    'No confluence of signals. Each gate has to be green before I move.',
    'Watching the order flow. Nothing compelling has shown up yet.',
    'The best traders know when not to trade. This is one of those moments.',
    'Market needs to make a decision. Until it does, I am staying neutral.',
    'Calm and patient. The setup will come when it is ready.',
  ],
  ANALYZING: [
    'Some signals starting to align. Watching this carefully.',
    'Score is building. Not quite at the threshold yet, but getting interesting.',
    'Getting interested here. Still needs one more confirmation before I act.',
    'Something may be setting up. Keeping a close eye on the structure.',
    'The pieces are starting to come together. Not quite there yet.',
    'Edge score is climbing. Watching for that final gate to flip green.',
    'Volume is picking up. Price structure is beginning to define itself.',
    'I am seeing early signs of a potential setup. Staying alert.',
    'Market is beginning to show some directional intent. Watching closely.',
    'Two gates are green. Need the rest to confirm before considering an entry.',
    'This could develop into something. Patience while the setup matures.',
    'Momentum is starting to shift. Not enough to act on yet, but worth monitoring.',
    'The score is above thirty. We are in the zone of interest, not yet the zone of action.',
    'Bias is aligning with price action. Waiting for structure to confirm.',
    'I feel the setup forming but I will not chase it. Let it come to me.',
    'Order flow is tilting in one direction. Watching for confirmation on a higher timeframe.',
  ],
  FORMING: [
    'Setup is developing. Almost at the threshold for a full green light.',
    'Score is approaching the threshold. Three of four gates are lit.',
    'This is building into something real. One final gate to go.',
    'Structure is strong. Just waiting for that last piece of confirmation.',
    'We are close. The market is doing exactly what I want to see.',
    'Every box is nearly checked. Entry is not far away.',
    'The thesis is solid. Edge is forming fast, stay sharp.',
    'I can feel the tension in this setup. Price wants to move and we are almost ready.',
    'Zone is holding, structure is good, and momentum is building. We are very close.',
    'This is the moment before the signal fires. Breathe and stay focused.',
    'Almost everything is aligned. One more confirmation and we are live.',
    'Setup quality is high. Just needs that final trigger before I act.',
    'The market is coiling. When it releases, I want to be positioned correctly.',
    'Score is just below the line. Any moment now this crosses into action territory.',
    'Risk is defined, entry is clear, target is visible. Just waiting on the last gate.',
    'I have been watching this build for the last few minutes. Nearly there.',
  ],
  READY_LONG: [
    'Long setup confirmed. All gates are green and the edge is locked.',
    'Bullish edge locked in. The execution window is open right now.',
    'Textbook long setup. Clean entry level, defined risk, and target in sight.',
    'High conviction on the long side. Structure, momentum, and flow are all aligned.',
    'This is the setup I have been waiting for. Long bias confirmed across all criteria.',
    'Demand zone is holding, structure is bullish, and volume is supporting. This is it.',
    'All four gates are green. The edge score is strong. This is a valid long opportunity.',
    'Price is above vee-wap, structure is bullish, and momentum is with us. Long is the trade.',
    'The market handed us a clean setup on the long side. Entry is clearly defined.',
    'Bullish bias confirmed. Risk is at the stop below structure, target is at the measured move.',
    'Everything I need to see for a long is present. This is a high probability setup.',
    'Structure flipped bullish, price held the zone, and delta confirmed. Long is the read.',
    'The setup looks exactly as it should. Clean risk, clear target, strong edge.',
    'Long is ready to execute. Thesis is intact and all signals are pointing higher.',
    'High edge score on the long. This is not a guess, this is a calculated opportunity.',
    'Demand absorbed the selling and price is resuming higher. Long is the path of least resistance.',
    'All criteria met for a bullish entry. The market is giving us a gift right now.',
    'Setup formed exactly at the level I was watching. Long at these prices looks excellent.',
    'Momentum, structure, flow, and zone are all in agreement on the upside. Long is confirmed.',
    'Patient waiting paid off. The long setup is clean and ready to act on.',
  ],
  READY_SHORT: [
    'Short setup confirmed. All gates are green and the edge is locked.',
    'Bearish edge locked in. The execution window is open right now.',
    'Textbook short setup. Supply zone is holding and the structure has rolled over.',
    'High conviction on the short side. Structure, momentum, and flow are all aligned bearishly.',
    'This is the short setup I have been waiting for. All criteria confirmed.',
    'Supply zone held perfectly, structure turned bearish, delta is negative. Short is the trade.',
    'All four gates are green on the short side. Edge score is strong. This is a valid opportunity.',
    'Price is below vee-wap, structure is bearish, and sellers are in control. Short is the read.',
    'The market handed us a clean setup on the short side. Entry and risk are clearly defined.',
    'Bearish bias confirmed. Stop is above structure, target is at the measured move lower.',
    'Everything I need to see for a short is present. This is a high probability bearish setup.',
    'Structure flipped bearish, price rejected the supply zone, and delta confirmed selling pressure.',
    'The setup looks exactly as it should on the short side. Clean risk, clear target, strong edge.',
    'Short is ready to execute. Thesis is intact and all signals are pointing lower.',
    'High edge score on the short. This is a calculated opportunity, not a guess.',
    'Supply absorbed the buying and price is resuming lower. Short is the path of least resistance.',
    'All criteria met for a bearish entry. The market is offering a clean short right here.',
    'Setup formed exactly at the supply level I was watching. Short at these prices looks excellent.',
    'Momentum, structure, flow, and zone all agree on the downside. Short is confirmed.',
    'Patient waiting paid off. The short setup is clean and ready to act on.',
  ],
  ACTIVE: [
    'Position is live. Monitoring every tick as the trade unfolds.',
    'Trade is running. The thesis remains intact and I am trusting the process.',
    'Managing the position. Stop is placed and I am letting the market do its work.',
    'Stop is protected. Target is in view. Nothing to do but wait for the outcome.',
    'We are in the trade. My job now is to manage, not to second guess.',
    'Position open and breathing. Staying disciplined and not micromanaging.',
    'The market is working through our trade. Thesis has not changed.',
    'Live position in the book. Watching for signs of invalidation but thesis is intact.',
    'Trade is alive and the setup is playing out as expected. Patience from here.',
    'I entered at a clean level with defined risk. Now the market decides.',
    'Managing risk in real time. Stop stays where it is unless structure changes.',
    'Position is working. Do not touch the stop and let the target come to you.',
    'Every tick is accountable but I am not reacting to noise. Thesis rules.',
    'We are in the trade and the tape is cooperating. Staying focused.',
    'Risk is defined, target is set, position is live. Everything from here is process.',
    'The trade is in motion. My edge was to get in at a good level, which we did.',
    'Monitoring the momentum for any signs the thesis is weakening. So far so good.',
    'Position is healthy. Price is respecting the structure and moving our way.',
  ],
  TARGET_HIT: [
    'Target hit. Trade was profitable and the thesis played out perfectly.',
    'Winner. The process worked exactly as designed.',
    'Profit secured. Resetting and back to scanning for the next opportunity.',
    'Clean win. Patient waiting and disciplined execution paid off.',
    'Target reached. That is why we define levels before entering the trade.',
    'We got paid. The setup was valid, the execution was clean, and the market delivered.',
    'Trade closed at target. One more data point confirming the edge works.',
    'Profitable outcome. The thesis from entry to exit was correct throughout.',
    'Got to target. Now back to observation mode. Do not chase the next trade.',
    'Win logged. The process is solid. Take a breath and reset before the next setup.',
    'Target achieved. That is what disciplined trading looks like.',
    'Clean exit at the level I drew before entering. Exactly how it should work.',
  ],
  STOP_HIT: [
    'Stopped out. The loss was defined before entry and it is taken with discipline.',
    'Stop was hit. Risk was controlled and we live to trade another setup.',
    'Took the loss cleanly. The next valid setup will come and we will be ready.',
    'Cut out at the stop. That is the job. Define risk, take the loss, reset.',
    'The thesis was invalidated. Stop protected capital and that is all I need from it.',
    'Loss taken. It was a planned risk, not a surprise. On to the next one.',
    'Stopped. Every loss is tuition. The important thing is the setup was valid and sized correctly.',
    'Hit the stop. The market disagreed with the setup today and that is acceptable.',
    'Loss logged. No revenge trading, no chasing. Back to observation and patience.',
    'Stopped out cleanly. The edge does not win every time, it wins enough times. Moving forward.',
    'Risk was defined and controlled. That stop was exactly where it needed to be.',
    'Trade did not work this time. Reset the mental state and wait for the next opportunity.',
  ],
  NO_EDGE: [
    'No edge present on any instrument right now. Fully on the sidelines.',
    'Conditions are unfavorable across the board. Watching only, no capital at risk.',
    'Nothing to trade here. Capital stays sidelined until edge returns.',
    'Market is offering nothing clean today. Staying patient and disciplined.',
    'No instrument is showing a valid setup. Cash is the best position right now.',
    'All signals are below threshold. I will not force a trade just to be active.',
    'No edge means no trade. Simple as that.',
  ],
};

const _mIdx: Record<string, number> = {};
function pickMLine(state: string): string {
  const lines = M_VOICE[state] ?? M_VOICE.WAIT;
  const i = _mIdx[state] ?? 0;
  _mIdx[state] = (i + 1) % lines.length;
  return lines[i];
}

// ── Avatar state from data ────────────────────────────────────────────────────
function toAvatarState(data: any): string {
  if (!data) return 'WAIT';
  const mb = data.main_brain || {};
  const st = String(mb.status || '').toUpperCase();
  if (data.active_trade) return 'ACTIVE';
  if (st === 'READY') {
    const dir = String(mb.direction || '').toUpperCase();
    return /short/i.test(dir) ? 'READY_SHORT' : 'READY_LONG';
  }
  if (st === 'BUILDING') return 'FORMING';
  if (data.edge_score > 30) return 'ANALYZING';
  return 'WAIT';
}

// ── Main MobileHome ───────────────────────────────────────────────────────────
export default function MobileHome() {
  const [ticker, setTicker] = useState<Ticker>(() => {
    try { return (localStorage.getItem('brain_ticker') as Ticker) || 'MNQ'; } catch { return 'MNQ'; }
  });
  const [tab, setTab]         = useState<Tab>('signal');
  const [narration, setNarr]  = useState('');
  const [avatarState, setAvSt] = useState('WAIT');

  const { authed, checking, authHeader, tryAuth } = useAuth();
  const { data, conn, ts } = useLiveData(ticker, authHeader);
  const { muted, setMuted, speaking, speak, audioUnlocked } = useTTS();
  const clock = useClock();

  // Stable refs so effects never go stale
  const speakRef      = useRef<(t: string) => void>(() => {});
  const lastSpokenRef = useRef('');
  const narrationRef  = useRef('');
  useEffect(() => { speakRef.current = speak; }, [speak]);

  // Persist ticker choice
  useEffect(() => { try { localStorage.setItem('brain_ticker', ticker); } catch {} }, [ticker]);

  // Derive avatar state from live data and pick a narration line
  useEffect(() => {
    if (!data) return;
    const st = toAvatarState(data);
    setAvSt(st);
    setNarr(pickMLine(st));
  }, [data]);

  // Keep narrationRef current so the unlock effect can read it without stale closure
  useEffect(() => { narrationRef.current = narration; }, [narration]);

  // Speak whenever narration text changes (guards against repeating the same line)
  useEffect(() => {
    if (!narration || narration === lastSpokenRef.current) return;
    lastSpokenRef.current = narration;
    speakRef.current(narration);
  }, [narration]);

  // After the first touch unlocks audio, immediately speak whatever narration we have.
  // Without this, the avatar is silent until the 18s timer fires because the first
  // speak() call (triggered by data load) was silently rejected before user gesture.
  useEffect(() => {
    if (!audioUnlocked) return;
    const t = setTimeout(() => {
      const line = narrationRef.current;
      if (!line) return;
      lastSpokenRef.current = '';   // clear guard so the speak goes through
      speakRef.current(line);
    }, 300);
    return () => clearTimeout(t);
  }, [audioUnlocked]);

  // Periodic narration refresh every 18s — pick a new line (speak effect handles voicing)
  useEffect(() => {
    const id = setInterval(() => {
      setNarr(pickMLine(avatarState));
    }, 18000);
    return () => clearInterval(id);
  }, [avatarState]);

  const verdI = useMemo(() => data ? verdictInfo(data) : { label:'—', color:MUTED, bg:'transparent' }, [data]);

  if (checking) {
    return (
      <div style={{ position:'fixed', inset:0, background:BG, display:'flex',
        alignItems:'center', justifyContent:'center' }}>
        <div style={{ width:32, height:32, borderRadius:'50%',
          border:'2px solid rgba(255,255,255,0.10)', borderTopColor:BLUE,
          animation:'spin 0.8s linear infinite' }} />
      </div>
    );
  }

  if (!authed) return <MobileLogin onSubmit={tryAuth} />;

  const TAB_BOTTOM_PAD = 72;

  return (
    <div style={{ position:'fixed', inset:0, background:BG, color:'rgba(255,255,255,0.88)',
      fontFamily:'-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
      display:'flex', flexDirection:'column', overflow:'hidden' }}>
      <style>{MOBILE_CSS + `
        @keyframes spin { to{transform:rotate(360deg)} }
        * { -webkit-tap-highlight-color: transparent; box-sizing: border-box; }
        ::-webkit-scrollbar { display: none; }
      `}</style>

      {/* ── Header ── */}
      <div style={{ flexShrink:0, padding:'env(safe-area-inset-top,12px) 16px 10px',
        background:'rgba(6,8,16,0.95)', backdropFilter:'blur(16px)',
        borderBottom:'1px solid rgba(255,255,255,0.07)' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:8 }}>
          <div style={{ display:'flex', alignItems:'center', gap:8 }}>
            <div style={{ width:7, height:7, borderRadius:'50%',
              background: conn === 'ok' ? BULL : conn === 'err' ? BEAR : AMB,
              boxShadow: conn === 'ok' ? `0 0 6px ${BULL}` : undefined,
              animation: conn === 'ok' ? 'mPulse 3s ease-in-out infinite' : undefined }} />
            <span style={{ fontSize:11, fontFamily:'monospace', color:'rgba(255,255,255,0.35)',
              letterSpacing:'0.08em' }}>{clock}</span>
          </div>
          {/* Compact verdict */}
          <div style={{ padding:'4px 12px', borderRadius:20, background:verdI.bg,
            border:`1px solid ${verdI.color}35` }}>
            <span style={{ fontSize:11, fontFamily:'monospace', fontWeight:700,
              letterSpacing:'0.08em', color:verdI.color }}>{verdI.label}</span>
          </div>
        </div>
        <TickerBar value={ticker} onChange={setTicker} />
      </div>

      {/* ── Content area ── */}
      <div style={{ flex:1, overflowY:'auto', WebkitOverflowScrolling:'touch',
        padding:`12px 14px ${TAB_BOTTOM_PAD}px` }}>
        {tab === 'signal' && (
          <SignalTab data={data} ticker={ticker} narration={narration}
            avatarState={avatarState} speaking={speaking} />
        )}
        {tab === 'brain' && <BrainTab data={data} />}
        {tab === 'chat' && (
          <ChatTab authHeader={authHeader} ticker={ticker} speak={speak} />
        )}
        {tab === 'position' && (
          <PositionTab data={data} muted={muted} setMuted={setMuted} />
        )}
      </div>

      {/* ── Bottom nav ── */}
      <BottomNav active={tab} onChange={setTab} />
    </div>
  );
}
