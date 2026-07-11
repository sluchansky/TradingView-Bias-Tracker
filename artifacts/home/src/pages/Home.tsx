import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';

// ── Colour palette per brain status ──────────────────────────────────────────
const PAL: Record<string, { ring: string; glow: string; dot: string }> = {
  READY:    { ring: '#4ade80', glow: 'rgba(74,222,128,0.18)',  dot: '#4ade80' },
  MANAGING: { ring: '#60a5fa', glow: 'rgba(96,165,250,0.18)',  dot: '#60a5fa' },
  BUILDING: { ring: '#fbbf24', glow: 'rgba(251,191,36,0.18)',  dot: '#fbbf24' },
  HUNTING:  { ring: '#fb923c', glow: 'rgba(251,146,60,0.18)',  dot: '#fb923c' },
  WATCHING: { ring: '#3f3f46', glow: 'rgba(63,63,70,0.10)',    dot: '#52525b' },
};
const DEF_PAL = PAL.WATCHING;

// ── Derive micro-thought lines from live data ─────────────────────────────────
function getThoughts(data: any): string[] {
  const mb  = (data?.main_brain   || {}) as Record<string, any>;
  const st  = (mb.status || 'WATCHING') as string;
  const sig = (mb.signals         || {}) as Record<string, any>;
  const lm  = (mb.learning_memory || {}) as Record<string, any>;
  const out: string[] = [];

  if (st === 'READY') {
    out.push('Setup confirmed.');
    if (sig.favored && sig.favored !== 'none') out.push(`${sig.favored} edge locked in.`);
    if (sig.cvd && sig.cvd !== 'unknown') out.push(`Order flow is ${sig.cvd}.`);
  } else if (st === 'MANAGING') {
    out.push('Managing open position.', 'Monitoring for invalidation...');
  } else if (st === 'BUILDING') {
    out.push('Setup forming...', 'Waiting for confirmation.', 'Not ready yet.');
    if (sig.cvd && sig.cvd !== 'unknown') out.push(`Order flow is ${sig.cvd}.`);
  } else {
    out.push('Watching the tape...');
    if (sig.vwap_side && sig.vwap_side !== 'unknown')
      out.push(sig.vwap_side === 'above' ? 'Above VWAP.' : 'Below VWAP.');
    if (sig.cvd && sig.cvd !== 'unknown') out.push(`Flow is ${sig.cvd}.`);
    if (sig.structure && sig.structure !== 'mixed') out.push(`Structure reads ${sig.structure}.`);
    if (typeof lm.similar_samples === 'number' && lm.similar_samples >= 3)
      out.push(`${lm.similar_samples} similar setups in memory.`);
  }
  return out.length ? out : ['Watching...'];
}

// ── Rotating ticker hook ──────────────────────────────────────────────────────
function useTicker(items: string[], ms = 3200) {
  const [i, setI] = useState(0);
  const len = items.length;
  useEffect(() => {
    setI(0);
    const id = setInterval(() => setI(n => (n + 1) % Math.max(1, len)), ms);
    return () => clearInterval(id);
  }, [len, ms]);
  return items[i % Math.max(1, len)] ?? '';
}

// ── Character-stream hook ─────────────────────────────────────────────────────
function useStream(target: string, msPerChar = 14) {
  const [text, setText] = useState('');
  const [live, setLive] = useState(false);
  const prev  = useRef('');
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

// ── Text-to-speech hook ───────────────────────────────────────────────────────
function useTTS() {
  const [voices, setVoices]    = useState<SpeechSynthesisVoice[]>([]);
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
      const en  = all.filter(v => v.lang.startsWith('en'));
      setVoices(en.length ? en : all.slice(0, 30));
    };
    load();
    ss.addEventListener('voiceschanged', load);
    return () => ss.removeEventListener('voiceschanged', load);
  }, []);

  const setVoice = useCallback((name: string) => {
    try { localStorage.setItem('brain_voice', name); } catch { /* */ }
    setVoiceN(name);
  }, []);

  const setMuted = useCallback((m: boolean) => {
    try { localStorage.setItem('brain_muted', m ? '1' : '0'); } catch { /* */ }
    if (m) { window.speechSynthesis?.cancel(); setSpeaking(false); }
    setMutedState(m);
  }, []);

  const speak = useCallback((text: string) => {
    const ss = window.speechSynthesis;
    if (!text || muted || !ss) return;
    ss.cancel();
    const utt   = new SpeechSynthesisUtterance(text.slice(0, 400));
    const voice = voices.find(v => v.name === voiceName) ?? voices[0];
    if (voice) utt.voice = voice;
    utt.rate  = 0.92;
    utt.pitch = 1.05;
    utt.onstart = () => setSpeaking(true);
    utt.onend   = () => setSpeaking(false);
    utt.onerror = () => setSpeaking(false);
    ss.speak(utt);
  }, [voices, voiceName, muted]);

  return { voices, voiceName, setVoice, muted, setMuted, speaking, speak };
}

// ── Living particle intelligence — no human face, pure data cloud ─────────────
// Particle zones: 0=bg stars, 1=head cloud, 2=eye_L, 3=eye_R, 4=mouth, 5=halo
// Physics: each particle has a home; spring + sine-noise drives organic drift.
// Status drives energy level → particle speed, brightness, color temperature.
function BrainFace({ speaking, color, glow, status }: {
  speaking: boolean; color: string; glow: string; status: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const propsRef  = useRef({ speaking, color, status });
  useEffect(() => { propsRef.current = { speaking, color, status }; }, [speaking, color, status]);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext('2d')!;
    const W = cv.width, H = cv.height;

    // Head geometry (all in canvas px)
    const CX = W * 0.50, CY = H * 0.43;
    const RX = W * 0.29, RY = H * 0.35;
    const EL_X = CX - RX * 0.40, EL_Y = CY - RY * 0.19;
    const ER_X = CX + RX * 0.40, ER_Y = CY - RY * 0.19;
    const M_X  = CX,             M_Y  = CY + RY * 0.40;

    // Status → [face colour, eye colour, energy 0–1]
    const S: Record<string, [string, string, number]> = {
      READY:    ['#bfdbfe', '#ffffff', 1.00],
      MANAGING: ['#67e8f9', '#e0f9ff', 0.88],
      BUILDING: ['#60a5fa', '#bae6fd', 0.68],
      WATCHING: ['#3b82f6', '#93c5fd', 0.50],
      WAIT:     ['#2563eb', '#60a5fa', 0.36],
    };

    type Pt = { x:number; y:number; vx:number; vy:number; hx:number; hy:number; sz:number; br:number; ph:number; z:number };
    const pts: Pt[]   = [];
    const bz: Pt[][] = [[], [], [], [], [], []];  // by zone

    const mk = (hx:number, hy:number, sz:number, br:number, z:number) => {
      const p: Pt = { x:hx+(Math.random()-.5)*6, y:hy+(Math.random()-.5)*6, vx:0, vy:0, hx, hy, sz, br, ph:Math.random()*Math.PI*2, z };
      pts.push(p); bz[z].push(p);
    };

    // Zone 0 — background star field
    for (let i = 0; i < 75; i++)
      mk(Math.random()*W, Math.random()*H, 0.6+Math.random()*0.9, 0.05+Math.random()*0.10, 0);

    // Zone 1 — head cloud (inside ellipse, denser centre)
    for (let i = 0; i < 520; i++) {
      let hx=0, hy=0, r2=2, tries=0;
      while (r2>1 && tries++<60) {
        hx = CX+(Math.random()*2-1)*RX; hy = CY+(Math.random()*2-1)*RY;
        const ddx=(hx-CX)/RX, ddy=(hy-CY)/RY; r2=ddx*ddx+ddy*ddy;
      }
      const ef = Math.sqrt(r2);
      mk(hx, hy, Math.max(0.5, 1.3+Math.random()*1.3-ef*0.9), Math.max(0.04, 0.11+Math.random()*0.19-ef*0.07), 1);
    }

    // Zone 5 — halo ring
    for (let i = 0; i < 90; i++) {
      const a=(i/90)*Math.PI*2, r=1.06+Math.random()*0.11;
      mk(CX+Math.cos(a)*RX*r, CY+Math.sin(a)*RY*r, 0.5+Math.random()*0.9, 0.07+Math.random()*0.13, 5);
    }

    // Zones 2+3 — eye particle clusters
    for (let i = 0; i < 52; i++) {
      const a=Math.random()*Math.PI*2, r=Math.sqrt(Math.random())*RX*0.13;
      mk(EL_X+Math.cos(a)*r, EL_Y+Math.sin(a)*r*0.55, 1.4+Math.random()*2.1, 0.48+Math.random()*0.52, 2);
      mk(ER_X+Math.cos(a)*r, ER_Y+Math.sin(a)*r*0.55, 1.4+Math.random()*2.1, 0.48+Math.random()*0.52, 3);
    }

    // Zone 4 — mouth region
    for (let i = 0; i < 30; i++)
      mk(M_X+(Math.random()-.5)*RX*0.40, M_Y+(Math.random()-.5)*RY*0.13, 0.8+Math.random()*1.2, 0.08+Math.random()*0.13, 4);

    // Animation state
    let t=0, pulseT=0;
    let blinkAmt=0, blinkWait=3+Math.random()*5;
    type BP = 'open'|'closing'|'closed'|'opening';
    let blinkPhase: BP = 'open';
    let last=performance.now(), raf=0;

    function frame(now: number) {
      const dt = Math.min((now-last)/1000, 0.05);
      last=now; t+=dt;
      const { speaking:spk, status:st } = propsRef.current;
      const [fCol, eCol, energy] = S[st] ?? S.WAIT;
      const isReady=st==='READY', isMgmt=st==='MANAGING', isBuild=st==='BUILDING';

      // Blink
      if (blinkPhase==='open')    { blinkWait-=dt; if (blinkWait<=0) blinkPhase='closing'; }
      if (blinkPhase==='closing') { blinkAmt=Math.min(1,blinkAmt+dt*11); if (blinkAmt>=1)  { blinkPhase='closed'; blinkWait=0.09; } }
      if (blinkPhase==='closed')  { blinkWait-=dt; if (blinkWait<=0) blinkPhase='opening'; }
      if (blinkPhase==='opening') { blinkAmt=Math.max(0,blinkAmt-dt*11); if (blinkAmt<=0) { blinkPhase='open'; blinkWait=3.5+Math.random()*6; } }

      pulseT += dt * (isReady ? 1.6 : isMgmt ? 1.0 : 0.45);

      // Physics params driven by status
      const turb   = spk ? 3.4 : isBuild ? 2.2 : isReady ? 1.5 : 0.65;
      const spring = spk ? 0.030 : isReady ? 0.058 : 0.046;

      // ── Draw ─────────────────────────────────────────────────────────────
      // Trail: soft fade instead of clear → motion blur / glow persistence
      ctx.fillStyle = 'rgba(0,0,0,0.13)';
      ctx.fillRect(0, 0, W, H);

      // Background constellation lines
      const bg = bz[0];
      ctx.lineWidth = 0.5;
      for (let i=0; i<bg.length; i++) {
        for (let j=i+1; j<bg.length; j++) {
          const ddx=bg[i].x-bg[j].x, ddy=bg[i].y-bg[j].y, d2=ddx*ddx+ddy*ddy;
          if (d2<5625) {  // ~75 px
            ctx.globalAlpha = (1-d2/5625)*0.040;
            ctx.strokeStyle = fCol;
            ctx.beginPath(); ctx.moveTo(bg[i].x,bg[i].y); ctx.lineTo(bg[j].x,bg[j].y); ctx.stroke();
          }
        }
      }

      // Halo pulse ring
      if (isReady || isMgmt) {
        const pulse = (Math.sin(pulseT*2.2)+1)*0.5;
        ctx.globalAlpha = (1-pulse)*(isReady?0.20:0.11);
        ctx.strokeStyle = fCol; ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.ellipse(CX, CY, RX*(1+pulse*0.42), RY*(1+pulse*0.42), 0, 0, Math.PI*2);
        ctx.stroke();
      }

      // Breathing — head+halo home positions shift as a unit
      const breathX = Math.sin(t*0.55)*1.9;
      const breathY = Math.sin(t*0.37)*2.8;

      // Update + render all particles
      for (const p of pts) {
        p.ph += dt*(0.36+turb*0.21);

        // Two-octave sine noise field
        const nx = (Math.sin(p.ph*1.61+p.hx*0.018)+Math.sin(p.ph*0.89+p.hy*0.014))*turb;
        const ny = (Math.cos(p.ph*1.39+p.hx*0.012)+Math.cos(p.ph*0.76+p.hy*0.021))*turb;

        const bx = (p.z===1||p.z===5) ? breathX : 0;
        const by = (p.z===1||p.z===5) ? breathY : 0;

        p.vx += (p.hx+bx-p.x+nx)*spring;
        p.vy += (p.hy+by-p.y+ny)*spring;
        p.vx *= 0.85; p.vy *= 0.85;
        p.x  += p.vx; p.y  += p.vy;

        // Zone-specific brightness
        let drawBr = 0;
        if (p.z===0) {
          drawBr = p.br*(0.4+0.6*Math.abs(Math.sin(t*0.21+p.ph)));
        } else if (p.z===1) {
          drawBr = p.br*energy*(0.72+0.28*Math.sin(t*0.84+p.ph*0.11));
          if (spk) drawBr *= 1+0.60*Math.abs(Math.sin(t*22+p.ph*2.2));
        } else if (p.z===2||p.z===3) {
          drawBr = p.br*(0.38+energy*0.62)*(1-blinkAmt*0.94);
        } else if (p.z===4) {
          drawBr = p.br*(spk ? 0.30+0.70*Math.abs(Math.sin(t*26+p.ph*2.0)) : 0.10*energy);
        } else {
          drawBr = p.br*(isReady ? 0.42+0.58*Math.abs(Math.sin(pulseT*2.8-p.ph)) : 0.10+0.14*Math.abs(Math.sin(t*0.65+p.ph)));
        }
        if (drawBr<0.013) continue;

        const col = (p.z===2||p.z===3) ? eCol : fCol;

        // Soft glow bloom for eyes
        if ((p.z===2||p.z===3) && drawBr>0.22) {
          ctx.fillStyle=eCol; ctx.globalAlpha=drawBr*0.17;
          ctx.beginPath(); ctx.arc(p.x,p.y,p.sz*4.5,0,Math.PI*2); ctx.fill();
        }

        ctx.fillStyle=col; ctx.globalAlpha=Math.min(0.95,drawBr);
        ctx.beginPath(); ctx.arc(p.x,p.y,p.sz,0,Math.PI*2); ctx.fill();
      }

      ctx.globalAlpha=1;
      raf=requestAnimationFrame(frame);
    }

    raf=requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div style={{ filter: `drop-shadow(0 0 48px ${glow})` }}>
      <canvas ref={canvasRef} width={280} height={340} style={{ display:'block' }} />
    </div>
  );
}


// ── Quick-prompt chips ────────────────────────────────────────────────────────
function Chips({ status, onSelect }: { status: string; onSelect: (p: string) => void }) {
  const set =
    status === 'READY'
      ? ["Walk me through this setup.", "What's the risk?", "Where is your stop?", "What invalidates this?"]
      : status === 'MANAGING'
      ? ["How is the trade going?", "When do you exit?", "What's my current R?", "Is the thesis still valid?"]
      : ["Why are you waiting?", "Show me the evidence.", "Compare Scalp vs Swing.", "What changes your mind?"];
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center', marginTop: 12 }}>
      {set.map(p => (
        <button key={p} onClick={() => onSelect(p)}
          style={{ fontSize: 12, padding: '6px 14px', borderRadius: 20, border: '1px solid rgba(255,255,255,0.09)', background: 'transparent', color: 'rgba(255,255,255,0.3)', cursor: 'pointer', transition: 'all 0.2s', fontFamily: 'inherit' }}
          onMouseEnter={e => { const t = e.currentTarget; t.style.color = 'rgba(255,255,255,0.65)'; t.style.borderColor = 'rgba(255,255,255,0.22)'; }}
          onMouseLeave={e => { const t = e.currentTarget; t.style.color = 'rgba(255,255,255,0.3)'; t.style.borderColor = 'rgba(255,255,255,0.09)'; }}>
          {p}
        </button>
      ))}
    </div>
  );
}

// ── Message type ──────────────────────────────────────────────────────────────
interface Msg { id: number; role: 'user' | 'brain'; text: string; }
let _mid = 0;
const mkMsg = (role: Msg['role'], text: string): Msg => ({ id: ++_mid, role, text });

// ── Brain response bubble with its own stream ─────────────────────────────────
function BrainBubble({ msg }: { msg: Msg }) {
  const { text, live } = useStream(msg.role === 'brain' ? msg.text : '', 11);
  const shown = msg.role === 'brain' ? text : msg.text;
  return (
    <div style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', animation: 'b-up 0.25s ease-out' }}>
      <div style={{
        maxWidth: '82%', padding: '10px 16px', fontSize: 14, lineHeight: 1.65,
        borderRadius: 18,
        borderBottomRightRadius: msg.role === 'user' ? 4 : 18,
        borderBottomLeftRadius:  msg.role === 'brain' ? 4 : 18,
        background: msg.role === 'user' ? 'rgba(255,255,255,0.09)' : 'rgba(255,255,255,0.04)',
        color: msg.role === 'user' ? 'rgba(255,255,255,0.82)' : 'rgba(255,255,255,0.65)',
      }}>
        {shown}
        {live && <span style={{ display: 'inline-block', width: 2, height: 14, background: 'rgba(255,255,255,0.4)', marginLeft: 2, verticalAlign: 'middle', animation: 'b-dot 0.8s ease-in-out infinite' }} />}
      </div>
    </div>
  );
}

// ── Minimal login overlay ─────────────────────────────────────────────────────
function LoginOverlay({ onSubmit }: { onSubmit: (pwd: string) => void }) {
  const [val, setVal] = useState('');
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { setTimeout(() => ref.current?.focus(), 80); }, []);
  const submit = () => { const p = val.trim(); if (p) onSubmit(p); };
  return (
    <div style={{ position: 'fixed', inset: 0, background: '#000', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 28, zIndex: 999 }}>
      <div style={{ position: 'relative', width: 72, height: 72, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', border: '1px solid #3f3f46', opacity: 0.25, animation: 'b-pulse 2.8s ease-in-out infinite' }} />
        <div style={{ width: 44, height: 44, borderRadius: '50%', border: '1px solid #3f3f46', background: 'radial-gradient(circle at 35% 35%, rgba(255,255,255,0.06), rgba(0,0,0,0.7))', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#52525b', animation: 'b-breathe 3s ease-in-out infinite' }} />
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 14, color: 'rgba(255,255,255,0.5)', fontFamily: 'monospace', letterSpacing: '0.08em' }}>ACCESS REQUIRED</span>
        <span style={{ fontSize: 11, color: '#3f3f46', fontFamily: 'monospace' }}>Enter your dashboard password</span>
      </div>
      <form onSubmit={e => { e.preventDefault(); submit(); }} style={{ display: 'flex', gap: 8, width: 280 }}>
        <input
          ref={ref}
          type="password"
          value={val}
          onChange={e => setVal(e.target.value)}
          placeholder="Password"
          style={{ flex: 1, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '10px 14px', fontSize: 14, color: 'rgba(255,255,255,0.8)', fontFamily: 'inherit', outline: 'none' }}
        />
        <button type="submit"
          style={{ padding: '10px 18px', background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 8, color: 'rgba(255,255,255,0.7)', fontSize: 13, fontFamily: 'inherit', cursor: 'pointer', transition: 'all 0.2s' }}
          onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.14)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}>
          Enter
        </button>
      </form>
    </div>
  );
}

// ── Root ──────────────────────────────────────────────────────────────────────
export default function Home() {
  const [ticker, setTicker]   = useState<'MGC' | 'MNQ' | 'MES' | 'MYM'>('MNQ');
  const [data,   setData]     = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [msgs,   setMsgs]     = useState<Msg[]>([]);
  const [input,  setInput]    = useState('');
  const [asking, setAsking]   = useState(false);
  // Auth: password stored in localStorage so user doesn't re-enter on refresh
  const [authPwd, setAuthPwd]     = useState<string>(() => {
    try { return localStorage.getItem('brain_auth') || ''; } catch { return ''; }
  });
  const [authNeeded, setAuthNeeded] = useState<boolean>(() => {
    try { return !localStorage.getItem('brain_auth'); } catch { return true; }
  });
  const chatRef  = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Build the Basic Auth header from the stored password
  const authHeader = useMemo((): Record<string, string> =>
    authPwd ? { 'Authorization': 'Basic ' + btoa('admin:' + authPwd) } : {}
  , [authPwd]);

  const handleAuth = useCallback((pwd: string) => {
    try { localStorage.setItem('brain_auth', pwd); } catch { /* ignore */ }
    setAuthPwd(pwd);
    setAuthNeeded(false);
  }, []);

  // Poll /api/status every 5 s
  const poll = useCallback(async () => {
    if (!authPwd) return;
    try {
      const r = await fetch(`/api/status?ticker=${ticker}`, { credentials: 'include', headers: authHeader });
      if (r.status === 401) { setAuthNeeded(true); setAuthPwd(''); try { localStorage.removeItem('brain_auth'); } catch { /* */ } return; }
      if (r.ok) { setData(await r.json()); setLoading(false); }
    } catch { /* silent */ }
  }, [ticker, authPwd, authHeader]);

  useEffect(() => {
    setLoading(true); setData(null);
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [poll]);

  // Scroll chat to bottom whenever messages change
  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [msgs]);

  // Derive display values from polled data
  const mb       = (data?.main_brain       || {}) as Record<string, any>;
  const voice    = (data?.main_brain_voice || {}) as Record<string, any>;
  const status   = (mb.status || 'WATCHING') as string;
  const edge     = mb.edge_score ?? data?.edge_score;
  const grade    = mb.edge_grade ?? data?.edge_grade;
  const dirn     = mb.favored_direction as string | undefined;
  const lm       = (mb.learning_memory || {}) as Record<string, any>;
  const mission  = typeof mb.mission_progress === 'number' ? mb.mission_progress : null;
  const narration = (
    voice.narration ||
    (mb.synthesis as any)?.narrative ||
    mb.summary ||
    (loading ? '' : 'Watching the market — no signal yet.')
  ) as string;

  const pal        = PAL[status] || DEF_PAL;
  const microItems = data ? getThoughts(data) : ['Connecting...'];
  const microText  = useTicker(microItems, 3000);
  const { text: displayed, live: streaming } = useStream(narration, 14);

  // TTS — muted by default; user opts in via voice controls
  const { voices, voiceName, setVoice, muted, setMuted, speaking: ttsSpeaking, speak } = useTTS();
  const speakRef      = useRef(speak);
  const lastSpokenRef = useRef('');
  useEffect(() => { speakRef.current = speak; }, [speak]);
  // Auto-speak new narration when it changes
  useEffect(() => {
    if (narration && narration !== lastSpokenRef.current) {
      lastSpokenRef.current = narration;
      speakRef.current(narration);
    }
  }, [narration]);

  // Ask the Brain via /api/assistant
  const ask = useCallback(async (q?: string) => {
    const question = (q ?? input).trim();
    if (!question || asking) return;
    setInput('');
    setMsgs(m => [...m, mkMsg('user', question)]);
    setAsking(true);
    try {
      const r = await fetch('/api/assistant', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({ question, ticker }),
      });
      if (r.status === 401) {
        setAuthNeeded(true); setAuthPwd('');
        try { localStorage.removeItem('brain_auth'); } catch { /* */ }
        setMsgs(m => [...m, mkMsg('brain', 'Session expired — please re-enter your password.')]);
      } else {
        const j = await r.json();
        const answer = j.answer || j.error || 'No response.';
        speakRef.current(answer);
        setMsgs(m => [...m, mkMsg('brain', answer)]);
      }
    } catch {
      setMsgs(m => [...m, mkMsg('brain', 'Connection error — please try again.')]);
    } finally {
      setAsking(false);
      setTimeout(() => inputRef.current?.focus(), 60);
    }
  }, [input, asking, ticker, authHeader]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); }
  };

  const KEYFRAMES = `
    @keyframes b-ping    { 0%,100%{transform:scale(1);opacity:.12} 50%{transform:scale(1.18);opacity:.06} }
    @keyframes b-pulse   { 0%,100%{opacity:.22} 50%{opacity:.08} }
    @keyframes b-breathe { 0%,100%{opacity:.9;transform:scale(1)} 50%{opacity:.35;transform:scale(.7)} }
    @keyframes b-dot     { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.4;transform:scale(.6)} }
    @keyframes b-up      { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
    @keyframes b-bounce  { 0%,80%,100%{transform:scale(0)} 40%{transform:scale(1)} }
    .brain-input::placeholder { color:rgba(255,255,255,0.18); }
    .brain-input:focus        { outline:none; }
    ::-webkit-scrollbar       { width:0; height:0; }
  `;

  if (authNeeded) return (
    <>
      <style>{KEYFRAMES}</style>
      <LoginOverlay onSubmit={handleAuth} />
    </>
  );

  return (
    <div style={{ minHeight: '100vh', background: '#000', color: '#fff', display: 'flex', flexDirection: 'column', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', overflow: 'hidden' }}>

      {/* Keyframe declarations */}
      <style>{`
        @keyframes b-ping    { 0%,100%{transform:scale(1);opacity:.12} 50%{transform:scale(1.18);opacity:.06} }
        @keyframes b-pulse   { 0%,100%{opacity:.22} 50%{opacity:.08} }
        @keyframes b-breathe { 0%,100%{opacity:.9;transform:scale(1)} 50%{opacity:.35;transform:scale(.7)} }
        @keyframes b-dot     { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.4;transform:scale(.6)} }
        @keyframes b-up      { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
        @keyframes b-bounce  { 0%,80%,100%{transform:scale(0)} 40%{transform:scale(1)} }
        .brain-input::placeholder { color:rgba(255,255,255,0.18); }
        .brain-input:focus        { outline:none; }
        ::-webkit-scrollbar       { width:0; height:0; }
      `}</style>

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 28px', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {(['MGC', 'MNQ', 'MES', 'MYM'] as const).map(t => (
            <button key={t} onClick={() => setTicker(t)} style={{
              padding: '6px 18px', borderRadius: 20, border: 'none', cursor: 'pointer',
              fontSize: 12, fontWeight: 700, fontFamily: 'monospace', letterSpacing: '0.08em',
              background: ticker === t ? '#fff' : 'transparent',
              color: ticker === t ? '#000' : 'rgba(255,255,255,0.28)',
              transition: 'all 0.18s',
            }}>
              {t}
            </button>
          ))}
          {/* Live pulse dot */}
          <div style={{
            width: 6, height: 6, borderRadius: '50%', marginLeft: 10,
            background: pal.dot,
            boxShadow: (status === 'READY' || status === 'MANAGING') ? `0 0 8px ${pal.dot}` : 'none',
            animation: (status === 'READY' || status === 'MANAGING') ? 'b-dot 1.5s ease-in-out infinite' : 'b-breathe 4s ease-in-out infinite',
          }} />
        </div>
        <a href="/api/dashboard"
          style={{ fontSize: 11, color: 'rgba(255,255,255,0.18)', textDecoration: 'none', fontFamily: 'monospace', letterSpacing: '0.06em', transition: 'color 0.2s' }}
          onMouseEnter={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.5)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.18)')}>
          Engineering →
        </a>
      </header>

      {/* ── Main ─────────────────────────────────────────────────────────── */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'space-between', padding: '40px 24px 28px', maxWidth: 680, margin: '0 auto', width: '100%' }}>

        {/* Avatar + narration section */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 24, flex: 1, justifyContent: 'center', width: '100%' }}>

          <BrainFace speaking={streaming || asking || ttsSpeaking} color={pal.dot} glow={pal.glow} status={status} />

          {/* Voice controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              onClick={() => setMuted(!muted)}
              style={{ background: 'none', border: `1px solid ${muted ? 'rgba(255,255,255,0.07)' : pal.dot + '55'}`, borderRadius: 20, padding: '4px 12px', color: muted ? 'rgba(255,255,255,0.2)' : ttsSpeaking ? pal.dot : 'rgba(255,255,255,0.42)', cursor: 'pointer', fontSize: 11, fontFamily: 'monospace', letterSpacing: '0.06em', transition: 'all 0.25s', whiteSpace: 'nowrap' }}>
              {muted ? '○ voice off' : ttsSpeaking ? '◼ speaking' : '◆ voice on'}
            </button>
            {!muted && voices.length > 0 && (
              <select
                value={voiceName || (voices[0]?.name ?? '')}
                onChange={e => setVoice(e.target.value)}
                style={{ background: '#0a0a0a', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 20, padding: '4px 12px', color: 'rgba(255,255,255,0.35)', fontSize: 11, fontFamily: 'monospace', cursor: 'pointer', outline: 'none', maxWidth: 220, letterSpacing: '0.03em' }}>
                {voices.map(v => (
                  <option key={v.name} value={v.name} style={{ background: '#111', color: '#ccc' }}>
                    {v.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Status · instrument · edge · direction */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 11, fontFamily: 'monospace', letterSpacing: '0.08em' }}>
            <span style={{ color: pal.dot, fontWeight: 700, textTransform: 'uppercase' }}>{status}</span>
            <span style={{ color: '#27272a' }}>·</span>
            <span style={{ color: '#52525b' }}>{ticker}</span>
            {edge != null && (
              <>
                <span style={{ color: '#27272a' }}>·</span>
                <span style={{ color: '#71717a' }}>Edge {Math.round(Number(edge))}{grade ? ` (${grade})` : ''}</span>
              </>
            )}
            {dirn && dirn !== 'Neither' && (
              <>
                <span style={{ color: '#27272a' }}>·</span>
                <span style={{ color: dirn === 'Long' ? '#4ade80' : '#f87171', fontWeight: 600 }}>{dirn}</span>
              </>
            )}
          </div>

          {/* Rotating micro-thought */}
          <div style={{ height: 16, fontSize: 11, fontFamily: 'monospace', color: '#3f3f46', letterSpacing: '0.05em' }}>
            {microText}
          </div>

          {/* Core narration — the Brain speaking */}
          <div style={{ width: '100%', textAlign: 'center', minHeight: 108 }}>
            {loading ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, marginTop: 36 }}>
                {[0, 1, 2].map(i => (
                  <div key={i} style={{ width: 8, height: 8, borderRadius: '50%', background: '#27272a', animation: `b-bounce 1.4s ease-in-out ${i * 0.16}s infinite` }} />
                ))}
              </div>
            ) : (
              <p style={{ fontSize: 19, lineHeight: 1.8, color: 'rgba(255,255,255,0.72)', fontWeight: 300, margin: 0 }}>
                {displayed}
                {streaming && (
                  <span style={{ display: 'inline-block', width: 2, height: 19, background: 'rgba(255,255,255,0.45)', marginLeft: 2, verticalAlign: 'middle', animation: 'b-dot 0.9s ease-in-out infinite' }} />
                )}
              </p>
            )}
          </div>

          {/* Learning one-liner */}
          {lm.available && lm.note && (
            <div style={{ fontSize: 11, color: '#3f3f46', fontFamily: 'monospace', textAlign: 'center', maxWidth: 440 }}>
              {lm.note}
            </div>
          )}

          {/* Setup progress bar — BUILDING or READY only */}
          {mission !== null && mission > 0 && (status === 'BUILDING' || status === 'READY') && (
            <div style={{ width: '100%', maxWidth: 300 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#3f3f46', fontFamily: 'monospace', marginBottom: 6 }}>
                <span>Setup progress</span><span>{mission}%</span>
              </div>
              <div style={{ height: 2, background: 'rgba(255,255,255,0.05)', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{ height: '100%', borderRadius: 2, width: `${mission}%`, background: status === 'READY' ? '#4ade80' : '#fbbf24', transition: 'width 1.2s ease' }} />
              </div>
            </div>
          )}
        </div>

        {/* ── Conversation ──────────────────────────────────────────────── */}
        <div style={{ width: '100%', maxWidth: 560, marginTop: 36 }}>

          {/* Chat history */}
          {msgs.length > 0 && (
            <div ref={chatRef} style={{ marginBottom: 14, maxHeight: 240, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {msgs.map(m => <BrainBubble key={m.id} msg={m} />)}
              {asking && (
                <div style={{ display: 'flex', justifyContent: 'flex-start', animation: 'b-up 0.25s ease-out' }}>
                  <div style={{ padding: '10px 16px', borderRadius: '18px 18px 18px 4px', background: 'rgba(255,255,255,0.04)', display: 'flex', gap: 5, alignItems: 'center' }}>
                    {[0, 1, 2].map(i => (
                      <div key={i} style={{ width: 6, height: 6, borderRadius: '50%', background: '#52525b', animation: `b-bounce 1.4s ease-in-out ${i * 0.16}s infinite` }} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Input bar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, border: '1px solid rgba(255,255,255,0.08)', borderRadius: 26, padding: '13px 18px', background: 'rgba(255,255,255,0.02)', transition: 'border-color 0.2s' }}>
            <input
              ref={inputRef}
              className="brain-input"
              type="text"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder="Ask the Brain..."
              style={{ flex: 1, background: 'transparent', border: 'none', fontSize: 14, color: 'rgba(255,255,255,0.72)', fontFamily: 'inherit' }}
            />
            <button onClick={() => ask()} disabled={!input.trim() || asking}
              style={{ background: 'transparent', border: 'none', padding: 0, cursor: input.trim() && !asking ? 'pointer' : 'default', color: input.trim() && !asking ? 'rgba(255,255,255,0.55)' : 'rgba(255,255,255,0.14)', transition: 'color 0.2s', display: 'flex', alignItems: 'center' }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>

          {/* Quick prompts — shown only when conversation is empty */}
          {msgs.length === 0 && <Chips status={status} onSelect={ask} />}
        </div>
      </main>
    </div>
  );
}
