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

// ── Full digital face — all features in 1s & 0s ──────────────────────────────
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

    // 28 cols × 20 rows at 9×11 px → 252×220 canvas
    const COLS = 28, ROWS = 20, CW = 9, CH = 11;

    const grid: string[][] = Array.from({ length: ROWS }, () =>
      Array.from({ length: COLS }, () => (Math.random() < 0.5 ? '0' : '1'))
    );

    // ── Geometry (all in cell-centre coords: col+0.5, row+0.5) ───────────────
    const FCX = 14, FCY = 10, FRX = 12.5, FRY = 8.8;                     // face
    const LBX = 8.5, RBX = 19.5, BY_B = 4.2, BWH = 2.3, BARCH = 0.7;    // brows
    const LEX = 8.5, REX = 19.5, EY = 6.8;                                // eyes (shared Y)
    const ERX = 2.5, ERY_F = 1.35, IRS_X = 1.45, IRS_Y = 1.15, PR = 0.73; // eye radii
    const NX = 14, NBY1 = 9.5, NBY2 = 12.5;                               // nose bridge
    const NLX = 12.5, NRX = 15.5, NNY = 12.8, NNR = 0.82;                // nostrils
    const MX = 14, M_UY = 15.5, M_LY = 16.9, MHW = 4.5, M_OPEN = 1.9;  // mouth

    const ell = (px: number, py: number, cx: number, cy: number, rx: number, ry: number) =>
      ry > 0.01 && ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2 <= 1;

    // Eyebrow Y at column cx (parabolic arch, raises with expression)
    const browY = (cx: number, bx: number, raise: number) => {
      const t = (cx - bx) / BWH;
      return BY_B + raise - BARCH * Math.max(0, 1 - t * t);
    };

    // Upper lip: Cupid's bow via two Gaussians (M-shape)
    const upperLipY = (cx: number, smile: number) => {
      const g1 = 0.45 * Math.exp(-(((cx - (MX - 1.9)) / 1.5) ** 2));
      const g2 = 0.45 * Math.exp(-(((cx - (MX + 1.9)) / 1.5) ** 2));
      const sm = smile * 0.42 * ((cx - MX) / MHW) ** 2;  // corners curve up
      return M_UY - (g1 + g2) - sm;
    };

    // Lower lip: fuller arc, drops when mouth opens, corners rise with smile
    const lowerLipY = (cx: number, mo: number, smile: number) => {
      const t  = (cx - MX) / (MHW - 0.5);
      const sm = smile * 0.32 * ((cx - MX) / MHW) ** 2;
      return M_LY + mo * M_OPEN + 0.52 * Math.max(0, 1 - t * t) - sm;
    };

    // ── Animation state ───────────────────────────────────────────────────────
    let blinkAmt = 0;
    let blinkPhase: 'open'|'closing'|'closed'|'opening' = 'open';
    let blinkWait = 2.5 + Math.random() * 4;
    let mouthOpen  = 0;
    let smile      = 0.2;
    let browRaise  = 0;
    let eox = 0, eoy = 0, tex = 0, tey = 0, ewait = 2;
    let cflip = 0;
    let last  = performance.now();
    let raf   = 0;

    function frame(now: number) {
      const dt = Math.min((now - last) / 1000, 0.05);
      last     = now;
      const { speaking: spk, color: col, status: st } = propsRef.current;

      // Blink
      if (blinkPhase === 'open') {
        blinkWait -= dt;
        if (blinkWait <= 0) blinkPhase = 'closing';
      } else if (blinkPhase === 'closing') {
        blinkAmt = Math.min(1, blinkAmt + dt * 12);
        if (blinkAmt >= 1) { blinkPhase = 'closed'; blinkWait = 0.06; }
      } else if (blinkPhase === 'closed') {
        blinkWait -= dt;
        if (blinkWait <= 0) blinkPhase = 'opening';
      } else {
        blinkAmt = Math.max(0, blinkAmt - dt * 12);
        if (blinkAmt <= 0) { blinkPhase = 'open'; blinkWait = 3.5 + Math.random() * 5; }
      }
      const eyeRY     = ERY_F * (1 - blinkAmt * 0.97);
      const pupilRY   = PR * 0.88 * (1 - blinkAmt * 0.96);

      // Pupil drift
      ewait -= dt;
      if (ewait <= 0) {
        tex = (Math.random() - 0.5) * 1.3;
        tey = (Math.random() - 0.5) * 0.5;
        ewait = 2 + Math.random() * 3.5;
      }
      eox += (tex - eox) * dt * 2.5;
      eoy += (tey - eoy) * dt * 2.5;

      // Mouth (oscillate fast when speaking)
      const tMouth = spk ? (0.35 + 0.65 * Math.abs(Math.sin(now * 0.012))) : 0;
      mouthOpen   += (tMouth - mouthOpen) * Math.min(1, dt * 14);

      // Expression per status (lerped)
      const tSmile = st === 'READY' ? 0.85 : st === 'MANAGING' ? 0.1 : 0.22;
      const tBrow  = st === 'READY' ? -0.38 : st === 'MANAGING' ? 0.38 : st === 'BUILDING' ? -0.22 : 0;
      smile     += (tSmile - smile)     * dt * 1.2;
      browRaise += (tBrow  - browRaise) * dt * 1.2;

      // Char shuffle
      cflip -= dt;
      if (cflip <= 0) {
        cflip = 0.07;
        for (let k = 0; k < 6; k++) {
          const r = Math.floor(Math.random() * ROWS);
          const c = Math.floor(Math.random() * COLS);
          grid[r][c] = grid[r][c] === '0' ? '1' : '0';
        }
      }

      // ── Draw ─────────────────────────────────────────────────────────────
      ctx.clearRect(0, 0, cv.width, cv.height);
      ctx.font         = 'bold 8px monospace';
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';

      for (let row = 0; row < ROWS; row++) {
        for (let c = 0; c < COLS; c++) {
          const cx = c + 0.5, cy = row + 0.5;
          const ch  = grid[row][c];
          const px  = c   * CW + CW / 2;
          const py  = row * CH + CH / 2;

          const inFace = ell(cx, cy, FCX, FCY, FRX, FRY);

          // Eyebrows
          const lBrowY  = browY(cx, LBX, browRaise);
          const rBrowY  = browY(cx, RBX, browRaise);
          const inLBrow = Math.abs(cx - LBX) <= BWH + 0.5 && Math.abs(cy - lBrowY) < 0.53;
          const inRBrow = Math.abs(cx - RBX) <= BWH + 0.5 && Math.abs(cy - rBrowY) < 0.53;

          // Eyes (whites → iris → pupil)
          const inLE    = ell(cx, cy, LEX, EY, ERX, eyeRY);
          const inRE    = ell(cx, cy, REX, EY, ERX, eyeRY);
          const inLI    = ell(cx, cy, LEX, EY, IRS_X, IRS_Y * (eyeRY / ERY_F));
          const inRI    = ell(cx, cy, REX, EY, IRS_X, IRS_Y * (eyeRY / ERY_F));
          const inLP    = ell(cx, cy, LEX + eox, EY + eoy, PR, pupilRY);
          const inRP    = ell(cx, cy, REX + eox, EY + eoy, PR, pupilRY);
          // Eyelid line (appears when eye is mostly closed)
          const closedL = blinkAmt > 0.45 && Math.abs(cy - EY) < 0.63 && Math.abs(cx - LEX) <= ERX;
          const closedR = blinkAmt > 0.45 && Math.abs(cy - EY) < 0.63 && Math.abs(cx - REX) <= ERX;

          // Nose
          const inBridge   = Math.abs(cx - NX) < 0.62 && cy >= NBY1 && cy <= NBY2;
          const inLNostril = ell(cx, cy, NLX, NNY, NNR, 0.65);
          const inRNostril = ell(cx, cy, NRX, NNY, NNR, 0.65);

          // Mouth
          const uLY        = upperLipY(cx, smile);
          const lLY        = lowerLipY(cx, mouthOpen, smile);
          const inULip     = Math.abs(cx - MX) <= MHW       && Math.abs(cy - uLY) < 0.55;
          const inLLip     = Math.abs(cx - MX) <= MHW - 0.4 && Math.abs(cy - lLY) < 0.60;
          const inMouthInt = mouthOpen > 0.08 && Math.abs(cx - MX) < MHW - 0.9
                             && cy > uLY + 0.4 && cy < lLY - 0.3;
          const inTeeth    = inMouthInt && mouthOpen > 0.28
                             && cy < uLY + mouthOpen * 0.9;

          // ── Render (priority: pupil > eyelid > iris > white > brow > teeth > interior > lip > nose > face > bg)
          ctx.globalAlpha = 1;

          if (inLP || inRP) {
            ctx.fillStyle = col; ctx.globalAlpha = 0.95;
            ctx.fillText('1', px, py);
          } else if (closedL || closedR) {
            ctx.fillStyle = col; ctx.globalAlpha = Math.min(0.58, blinkAmt * 0.7);
            ctx.fillText('-', px, py);
          } else if (inLI || inRI) {
            ctx.fillStyle = col; ctx.globalAlpha = 0.55;
            ctx.fillText(ch, px, py);
          } else if (inLE || inRE) {
            ctx.fillStyle = '#e4e4e7'; ctx.globalAlpha = 0.56;
            ctx.fillText(ch, px, py);
          } else if (inLBrow || inRBrow) {
            ctx.fillStyle = col; ctx.globalAlpha = 0.60;
            ctx.fillText('1', px, py);
          } else if (inTeeth) {
            ctx.fillStyle = '#d4d4d8'; ctx.globalAlpha = 0.52 * mouthOpen;
            ctx.fillText('1', px, py);
          } else if (inMouthInt) {
            ctx.fillStyle = '#000000'; ctx.globalAlpha = 0.72 * mouthOpen;
            ctx.fillText('0', px, py);
          } else if (inULip) {
            ctx.fillStyle = col; ctx.globalAlpha = 0.48;
            ctx.fillText(ch, px, py);
          } else if (inLLip) {
            ctx.fillStyle = col; ctx.globalAlpha = 0.52;
            ctx.fillText(ch, px, py);
          } else if (inBridge) {
            ctx.fillStyle = col; ctx.globalAlpha = 0.16;
            ctx.fillText(ch, px, py);
          } else if (inLNostril || inRNostril) {
            ctx.fillStyle = col; ctx.globalAlpha = 0.30;
            ctx.fillText('0', px, py);
          } else if (inFace) {
            ctx.fillStyle = col; ctx.globalAlpha = 0.08 + (Math.random() < 0.003 ? 0.10 : 0);
            ctx.fillText(ch, px, py);
          } else {
            ctx.fillStyle = col; ctx.globalAlpha = 0.026;
            ctx.fillText(ch, px, py);
          }
        }
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(frame);
    }

    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div style={{ filter: `drop-shadow(0 0 32px ${glow})` }}>
      <canvas ref={canvasRef} width={28 * 9} height={20 * 11} style={{ display: 'block' }} />
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
        setMsgs(m => [...m, mkMsg('brain', j.answer || j.error || 'No response.')]);
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

          <BrainFace speaking={streaming || asking} color={pal.dot} glow={pal.glow} status={status} />

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
