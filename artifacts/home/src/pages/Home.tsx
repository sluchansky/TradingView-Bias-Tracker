import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';

// ── Colour palette per brain status ──────────────────────────────────────────
const PAL: Record<string, { ring: string; glow: string; dot: string }> = {
  READY:    { ring: '#4ade80', glow: 'rgba(74,222,128,0.22)',  dot: '#4ade80' },
  MANAGING: { ring: '#60a5fa', glow: 'rgba(96,165,250,0.22)',  dot: '#60a5fa' },
  BUILDING: { ring: '#fbbf24', glow: 'rgba(251,191,36,0.18)',  dot: '#fbbf24' },
  HUNTING:  { ring: '#fb923c', glow: 'rgba(251,146,60,0.18)',  dot: '#fb923c' },
  WATCHING: { ring: '#3f3f46', glow: 'rgba(63,63,70,0.10)',    dot: '#52525b' },
  WAIT:     { ring: '#27272a', glow: 'rgba(39,39,42,0.08)',    dot: '#3f3f46' },
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
    try { localStorage.setItem('brain_voice', name); } catch { /**/ }
    setVoiceN(name);
  }, []);
  const setMuted = useCallback((m: boolean) => {
    try { localStorage.setItem('brain_muted', m ? '1' : '0'); } catch { /**/ }
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
    utt.rate  = 0.92; utt.pitch = 1.05;
    utt.onstart = () => setSpeaking(true);
    utt.onend   = () => setSpeaking(false);
    utt.onerror = () => setSpeaking(false);
    ss.speak(utt);
  }, [voices, voiceName, muted]);
  return { voices, voiceName, setVoice, muted, setMuted, speaking, speak };
}

// ── 3-layer AI face ───────────────────────────────────────────────────────────
// Layer 1: Core face — brows, eyes, nose bridge, head silhouette
// Layer 2: Neural field — interior particles + connection lines
// Layer 3: Aura — outer halo ring + ambient pulse
function BrainFace({ speaking, glow, status }: {
  speaking: boolean; glow: string; status: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const propsRef  = useRef({ speaking, status });
  useEffect(() => { propsRef.current = { speaking, status }; }, [speaking, status]);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;
    const W = 300, H = 380;

    // ── Face anatomy landmarks ────────────────────────────────────────────────
    const CX = 150, CY = 192;       // face centroid
    const HRX = 88,  HRY = 110;     // head ellipse radii
    const LEX = 108, LEY = 158;     // left eye centre
    const REX = 192, REY = 158;     // right eye centre (symmetric: 150±42)
    const NBX = 150;                  // nose bridge x
    const NBY1 = 162, NBY2 = 208;   // nose bridge y span

    // ── Status → energy & 6-char hex colours ─────────────────────────────────
    const ENERGY: Record<string, number> = {
      READY: 1.0, MANAGING: 0.88, BUILDING: 0.68, WATCHING: 0.50, WAIT: 0.38,
    };
    const COLS: Record<string, [string, string]> = {
      READY:    ['#93c5fd', '#eff6ff'],
      MANAGING: ['#22d3ee', '#cffafe'],
      BUILDING: ['#60a5fa', '#dbeafe'],
      WATCHING: ['#3b82f6', '#93c5fd'],
      WAIT:     ['#1d4ed8', '#3b82f6'],
    };

    // ── Helpers ───────────────────────────────────────────────────────────────
    const rng = () => Math.random();
    const randn = (): number => {
      let u = 0, v = 0;
      while (!u) u = rng(); while (!v) v = rng();
      return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    };
    const h2 = (v: number) =>
      Math.max(0, Math.min(255, Math.round(v * 255))).toString(16).padStart(2, '0');
    const PI2 = 6.2832;

    // ── Particle structure ────────────────────────────────────────────────────
    type P = {
      hx: number; hy: number;
      x: number;  y: number;
      vx: number; vy: number;
      phx: number; phy: number;
      zone: number; sz: number;
    };
    const ps: P[]     = [];
    const bz: P[][] = [[], [], [], [], [], [], [], []];  // zones 0-7
    const add = (hx: number, hy: number, zone: number, sz: number) => {
      const p: P = { hx, hy, x: hx + randn() * 3, y: hy + randn() * 3,
                     vx: 0, vy: 0, phx: rng() * PI2, phy: rng() * PI2, zone, sz };
      ps.push(p); bz[zone].push(p);
    };

    // Zone 0 — outer halo (44 particles)
    for (let i = 0; i < 44; i++) {
      const a = rng() * PI2;
      const r = HRX * (1.28 + rng() * 0.55);
      add(CX + Math.cos(a) * r, CY + Math.sin(a) * r * (HRY / HRX) * (1 + rng() * 0.20), 0, 0.55 + rng() * 0.38);
    }

    // Zone 1 — head silhouette (82 particles, chin slightly narrower)
    for (let i = 0; i < 82; i++) {
      const a  = rng() * PI2;
      const xs = a > Math.PI ? 0.88 + rng() * 0.06 : 0.93 + rng() * 0.06;
      const ys = a > Math.PI ? 0.91 + rng() * 0.06 : 0.95 + rng() * 0.04;
      const nr = randn() * 2.8;
      add(CX + Math.cos(a) * HRX * xs + nr, CY + Math.sin(a) * HRY * ys + nr * 0.65, 1, 0.85 + rng() * 0.30);
    }

    // Zone 2 — face interior (72 particles, avoids eye/nose zones)
    for (let i = 0; i < 72; i++) {
      let hx = 0, hy = 0, tries = 0;
      do {
        const a = rng() * PI2, r = rng();
        hx = CX + Math.cos(a) * HRX * r * 0.87;
        hy = CY + Math.sin(a) * HRY * r * 0.87;
        tries++;
      } while (tries < 30 && (
        (hx - LEX) * (hx - LEX) + (hy - LEY) * (hy - LEY) < 484 ||
        (hx - REX) * (hx - REX) + (hy - REY) * (hy - REY) < 484 ||
        (Math.abs(hx - NBX) < 14 && hy > NBY1 - 10 && hy < NBY2 + 10)
      ));
      add(hx, hy, 2, 0.68 + rng() * 0.22);
    }

    // Zone 3 — left brow (14 particles, arc peaking above left eye)
    // t=0: outer/temple (x=84), t=1: inner/nose (x=130), peak at t=0.5 (x≈107, y=130)
    for (let i = 0; i < 14; i++) {
      const t = i / 13;
      add(84 + t * 46, 140 - Math.sin(t * Math.PI) * 10 + randn() * 2.5, 3, 0.78);
    }

    // Zone 4 — right brow (14 particles, symmetric)
    // t=0: inner/nose (x=170), t=1: outer/temple (x=216), peak at t=0.5 (x≈193, y=130)
    for (let i = 0; i < 14; i++) {
      const t = i / 13;
      add(170 + t * 46, 140 - Math.sin(t * Math.PI) * 10 + randn() * 2.5, 4, 0.78);
    }

    // Zone 5 — left eye (38 particles, concentrated ellipse)
    for (let i = 0; i < 38; i++) {
      const a = rng() * PI2, r = Math.sqrt(rng());
      add(LEX + Math.cos(a) * 13 * r, LEY + Math.sin(a) * 5.5 * r, 5, r < 0.45 ? 1.55 : 1.10);
    }

    // Zone 6 — right eye (38 particles, symmetric)
    for (let i = 0; i < 38; i++) {
      const a = rng() * PI2, r = Math.sqrt(rng());
      add(REX + Math.cos(a) * 13 * r, REY + Math.sin(a) * 5.5 * r, 6, r < 0.45 ? 1.55 : 1.10);
    }

    // Zone 7 — nose bridge (12 particles, narrows at top, widens at base)
    for (let i = 0; i < 12; i++) {
      const t = i / 11;
      add(NBX + (rng() - 0.5) * (2.5 + t * 4.5) + randn() * 1.5,
          NBY1 + t * (NBY2 - NBY1) + randn() * 2.5, 7, 0.52 + rng() * 0.22);
    }

    // ── Pre-compute neural connection pairs (zones 1+2 only) ──────────────────
    const CONN_D = 52, CONN_D2 = CONN_D * CONN_D;
    const pairs: [number, number][] = [];
    for (let i = 0; i < ps.length; i++) {
      if (ps[i].zone !== 1 && ps[i].zone !== 2) continue;
      for (let j = i + 1; j < ps.length; j++) {
        if (ps[j].zone !== 1 && ps[j].zone !== 2) continue;
        const dx = ps[i].hx - ps[j].hx, dy = ps[i].hy - ps[j].hy;
        if (dx * dx + dy * dy < CONN_D2) pairs.push([i, j]);
      }
    }

    // Pre-merge brow zones for draw loop efficiency
    const brows = [...bz[3], ...bz[4]];

    // ── Animation state ────────────────────────────────────────────────────────
    let raf = 0;
    let blinkV = 1.0;   // 1=fully open, ~0=fully closed
    let blinkDir = 0;   // 0=idle, -1=closing, 1=opening
    let nextBlink = performance.now() + 1800 + rng() * 2500;
    let eyeTX = 0, eyeTY = 0;   // tracking targets
    let eyeX  = 0, eyeY  = 0;   // smooth current
    let nextTrack = performance.now() + 1000 + rng() * 1500;
    let breatheT = rng() * PI2;
    ctx.globalAlpha = 1;

    const frame = (now: number) => {
      const { speaking: spk, status: st } = propsRef.current;
      const energy = ENERGY[st] ?? 0.50;
      const [cBase, cBrt] = COLS[st] ?? COLS.WATCHING;

      // Blink
      if (blinkDir === 0 && now > nextBlink) { blinkDir = -1; }
      if (blinkDir === -1) { blinkV -= 0.15; if (blinkV <= 0.04) { blinkV = 0.04; blinkDir = 1; } }
      else if (blinkDir === 1) { blinkV += 0.15; if (blinkV >= 1.0) { blinkV = 1.0; blinkDir = 0; nextBlink = now + 2000 + rng() * 3800; } }

      // Eye tracking
      if (now > nextTrack) {
        eyeTX = (rng() - 0.5) * 3.0; eyeTY = (rng() - 0.5) * 1.2;
        nextTrack = now + 1500 + rng() * 2500;
      }
      eyeX += (eyeTX - eyeX) * 0.016; eyeY += (eyeTY - eyeY) * 0.016;

      // Breathe + energy pulse
      breatheT += 0.009;
      const breathe = Math.sin(breatheT) * 2.6;
      const ePulse  = energy * (1 + Math.sin(breatheT * 0.72) * 0.045);
      const tSec    = now * 0.001;
      const turb    = spk ? 3.5 : 1.0;

      // Trail fade (preserves glow, avoids hard clear)
      ctx.fillStyle = 'rgba(0,0,0,0.13)';
      ctx.fillRect(0, 0, W, H);

      // ── Physics update (all particles) ────────────────────────────────────
      for (let i = 0; i < ps.length; i++) {
        const p = ps[i];
        const z = p.zone;
        const isEye = z === 5 || z === 6;
        // Eye particles: home shifts with tracking
        const ehx = isEye ? p.hx + eyeX : p.hx;
        const ehy = isEye ? p.hy + eyeY : p.hy;
        // Spring constants
        const sp  = isEye ? 0.044 : z === 0 ? 0.022 : (z === 3 || z === 4) ? 0.040 : 0.034;
        p.vx += (ehx - p.x) * sp;
        p.vy += (ehy - p.y) * sp;
        // Sine-noise drift (eyes are tighter)
        const freq = isEye ? 1.15 : 0.68;
        const amp  = isEye ? 0.09 : (z === 3 || z === 4 ? 0.14 : 0.22);
        p.vx += Math.sin(tSec * freq + p.phx) * amp * turb * ePulse;
        p.vy += Math.cos(tSec * freq + p.phy) * amp * turb * ePulse;
        p.vx *= 0.80; p.vy *= 0.80;
        p.x  += p.vx; p.y  += p.vy;
      }

      // ── Draw: neural connection lines (zone 1+2) ───────────────────────────
      ctx.lineWidth = 0.35;
      for (let k = 0; k < pairs.length; k++) {
        const [i, j] = pairs[k];
        const a = ps[i], b = ps[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < CONN_D2) {
          const alpha = (1 - Math.sqrt(d2) / CONN_D) * 0.10 * ePulse;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y + breathe); ctx.lineTo(b.x, b.y + breathe);
          ctx.strokeStyle = cBase + h2(alpha); ctx.stroke();
        }
      }

      // ── Draw: zone 0 (outer halo bg particles) ────────────────────────────
      for (const p of bz[0]) {
        const br = 0.05 + ePulse * 0.09;
        ctx.beginPath(); ctx.arc(p.x, p.y + breathe * 0.3, Math.max(0.3, p.sz), 0, PI2);
        ctx.fillStyle = cBase + h2(br); ctx.fill();
      }

      // ── Draw: zone 1 (silhouette) ─────────────────────────────────────────
      for (const p of bz[1]) {
        const br = 0.18 + ePulse * 0.28;
        ctx.beginPath(); ctx.arc(p.x, p.y + breathe, Math.max(0.4, p.sz * (0.70 + ePulse * 0.44)), 0, PI2);
        ctx.fillStyle = cBase + h2(br); ctx.fill();
      }

      // ── Draw: zone 2 (interior field) ────────────────────────────────────
      for (const p of bz[2]) {
        const br = 0.09 + ePulse * 0.14;
        ctx.beginPath(); ctx.arc(p.x, p.y + breathe, Math.max(0.3, p.sz * (0.65 + ePulse * 0.38)), 0, PI2);
        ctx.fillStyle = cBase + h2(br); ctx.fill();
      }

      // ── Draw: zones 3+4 (brows) ───────────────────────────────────────────
      for (const p of brows) {
        const br = 0.24 + ePulse * 0.34;
        ctx.beginPath(); ctx.arc(p.x, p.y + breathe * 0.8, Math.max(0.4, p.sz * (0.70 + ePulse * 0.42)), 0, PI2);
        ctx.fillStyle = cBase + h2(br); ctx.fill();
      }

      // ── Draw: zone 7 (nose bridge) ────────────────────────────────────────
      for (const p of bz[7]) {
        const br = 0.12 + ePulse * 0.18;
        ctx.beginPath(); ctx.arc(p.x, p.y + breathe * 0.5, Math.max(0.3, p.sz), 0, PI2);
        ctx.fillStyle = cBase + h2(br); ctx.fill();
      }

      // ── Draw: zones 5+6 (eyes — glow ring then particles on top) ──────────
      for (const zn of [5, 6]) {
        const ecX = zn === 5 ? LEX + eyeX : REX + eyeX;
        const ecY = zn === 5 ? LEY + eyeY : REY + eyeY;
        const eyeBaseY = zn === 5 ? LEY : REY;

        // Radial eye glow (soft bloom)
        if (blinkV > 0.18) {
          const glR = 19 + ePulse * 6;
          const gd  = ctx.createRadialGradient(ecX, ecY + breathe * 0.6, 0, ecX, ecY + breathe * 0.6, glR);
          gd.addColorStop(0,   cBrt + h2(0.28 * ePulse * blinkV));
          gd.addColorStop(0.4, cBase + h2(0.16 * ePulse * blinkV));
          gd.addColorStop(1,   cBase + '00');
          ctx.beginPath(); ctx.arc(ecX, ecY + breathe * 0.6, glR, 0, PI2);
          ctx.fillStyle = gd; ctx.fill();
        }

        // Eye particles (blink: compress Y toward tracked centre)
        for (const p of bz[zn]) {
          // Y displacement from home (not from tracked centre — avoids double-count)
          const rawOffY = p.y - eyeBaseY - eyeY;   // actual dynamic offset
          const drawY   = ecY + rawOffY * blinkV + breathe * 0.6;
          const br      = 0.55 + ePulse * 0.45;
          const sz      = Math.max(0.5, p.sz * (0.80 + ePulse * 0.44));
          ctx.beginPath(); ctx.arc(p.x, drawY, sz, 0, PI2);
          ctx.fillStyle = cBrt + h2(br); ctx.fill();
        }
      }

      // ── Draw: aura rings ─────────────────────────────────────────────────
      const pT = now * 0.0017;
      const pr = HRX * 1.40 + Math.sin(pT) * 7;
      const po = (st === 'READY'    ? 0.09 + Math.sin(pT * 1.4) * 0.04 :
                  st === 'MANAGING' ? 0.06 + Math.sin(pT * 0.9) * 0.03 :
                  0.025) * ePulse;
      ctx.beginPath(); ctx.ellipse(CX, CY + breathe * 0.3, pr, pr * (HRY / HRX), 0, 0, PI2);
      ctx.strokeStyle = cBase + h2(po); ctx.lineWidth = 0.8; ctx.stroke();
      if (st === 'READY' || st === 'MANAGING') {
        const ir = HRX * 1.14 + Math.sin(pT * 1.8) * 4;
        const ia = (0.055 + Math.sin(pT * 2.2) * 0.025) * ePulse;
        ctx.beginPath(); ctx.ellipse(CX, CY + breathe * 0.2, ir, ir * (HRY / HRX), 0, 0, PI2);
        ctx.strokeStyle = cBrt + h2(ia); ctx.lineWidth = 0.6; ctx.stroke();
      }

      raf = requestAnimationFrame(frame);
    };

    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div style={{ filter: `drop-shadow(0 0 56px ${glow})` }}>
      <canvas ref={canvasRef} className="brain-canvas" width={300} height={380} style={{ display: 'block' }} />
    </div>
  );
}

// ── Evidence snapshot panel ────────────────────────────────────────────────────
function EvidencePanel({ data, ticker, wide }: { data: any; ticker: string; wide?: boolean }) {
  if (!data) return null;
  const mb  = (data.main_brain        || {}) as Record<string, any>;
  const sig = (mb.signals             || {}) as Record<string, any>;
  const ad  = (data.alert_diagnostics || {}) as Record<string, any>;
  const gd  = (data.gate_debug        || {}) as Record<string, any>;
  const price = data.price ?? data.current_price;
  const vwap  = data.vwap_value;
  const vwapDir =
    price != null && vwap != null
      ? (Number(price) > Number(vwap) ? '▲ Above' : '▼ Below')
      : sig.vwap_side === 'above' ? '▲ Above'
      : sig.vwap_side === 'below' ? '▼ Below'
      : '—';
  const struct  = String(gd.structure ?? ad.structure ?? sig.structure ?? data.structure ?? '—');
  const zone    = String(gd.zone ?? ad.zone ?? sig.zone ?? data.zone_label ?? '—');
  const vol     = String(data.vol_regime ?? ad.volatility ?? '—');
  const mkt     = String(data.market_status ?? '—');
  const strictR = String(data.strict_reason ?? mb.wait_reason ?? '—');
  const alertAge = data.last_alert_age_seconds ?? data.last_alert_seconds;
  const alertStr = typeof alertAge === 'number'
    ? (alertAge < 60 ? `${alertAge}s ago` : `${Math.round(alertAge / 60)}m ago`) : '—';
  const rows: [string, string][] = [
    ['MARKET',     mkt],
    ['PRICE',      price != null ? Number(price).toFixed(2) : '—'],
    ['VWAP',       vwapDir],
    ['STRUCTURE',  struct],
    ['ZONE',       zone],
    ['VOL',        vol],
    ['LAST ALERT', alertStr],
    ['NO TRADE',   strictR.length > 55 ? strictR.slice(0, 53) + '…' : strictR],
  ];
  return (
    <div style={{
      width: wide ? '100%' : 240, flexShrink: 0, boxSizing: 'border-box',
      background: 'rgba(255,255,255,0.023)', border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 14, padding: '18px 16px',
    }}>
      <div style={{ fontSize: 10, fontFamily: 'monospace', color: 'rgba(255,255,255,0.22)', letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 14 }}>
        Evidence · {ticker}
      </div>
      {rows.map(([label, value]) => (
        <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.030)', gap: 10 }}>
          <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.24)', fontFamily: 'monospace', letterSpacing: '0.10em', flexShrink: 0, paddingTop: 1 }}>
            {label}
          </span>
          <span style={{ fontSize: 11, color: label === 'NO TRADE' ? 'rgba(255,255,255,0.44)' : 'rgba(255,255,255,0.70)', fontFamily: 'monospace', textAlign: 'right', wordBreak: 'break-word', maxWidth: wide ? '70%' : 140 }}>
            {value}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Evidence drawer (mobile — collapses into a button) ────────────────────────
function EvidenceDrawer({ data, ticker }: { data: any; ticker: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="evidence-drawer-wrap" style={{ width: '100%', marginTop: 12 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{ width: '100%', padding: '9px 16px', background: open ? 'rgba(255,255,255,0.05)' : 'transparent', border: '1px solid rgba(255,255,255,0.10)', borderRadius: 10, color: 'rgba(255,255,255,0.42)', fontSize: 12, fontFamily: 'monospace', letterSpacing: '0.06em', cursor: 'pointer', transition: 'all 0.18s', textAlign: 'left' as const }}
        onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.22)'; e.currentTarget.style.color = 'rgba(255,255,255,0.70)'; }}
        onMouseLeave={e => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.10)'; e.currentTarget.style.color = 'rgba(255,255,255,0.42)'; }}
      >
        {open ? '▲ Hide evidence' : '▼ Show me the evidence'}
      </button>
      {open && data && <div style={{ marginTop: 8 }}><EvidencePanel data={data} ticker={ticker} wide /></div>}
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
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center', marginTop: 14 }}>
      {set.map(p => (
        <button key={p} onClick={() => onSelect(p)}
          style={{ fontSize: 12, padding: '7px 15px', borderRadius: 20, border: '1px solid rgba(255,255,255,0.14)', background: 'rgba(255,255,255,0.04)', color: 'rgba(255,255,255,0.48)', cursor: 'pointer', transition: 'all 0.18s', fontFamily: 'inherit' }}
          onMouseEnter={e => { const t = e.currentTarget; t.style.color = 'rgba(255,255,255,0.85)'; t.style.borderColor = 'rgba(255,255,255,0.30)'; t.style.background = 'rgba(255,255,255,0.08)'; }}
          onMouseLeave={e => { const t = e.currentTarget; t.style.color = 'rgba(255,255,255,0.48)'; t.style.borderColor = 'rgba(255,255,255,0.14)'; t.style.background = 'rgba(255,255,255,0.04)'; }}>
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
        maxWidth: '84%', padding: '10px 16px', fontSize: 14, lineHeight: 1.68,
        borderRadius: 18,
        borderBottomRightRadius: msg.role === 'user' ? 4 : 18,
        borderBottomLeftRadius:  msg.role === 'brain' ? 4 : 18,
        background: msg.role === 'user' ? 'rgba(255,255,255,0.09)' : 'rgba(255,255,255,0.04)',
        color: msg.role === 'user' ? 'rgba(255,255,255,0.84)' : 'rgba(255,255,255,0.66)',
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
        <input ref={ref} type="password" value={val} onChange={e => setVal(e.target.value)} placeholder="Password"
          style={{ flex: 1, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '10px 14px', fontSize: 14, color: 'rgba(255,255,255,0.8)', fontFamily: 'inherit', outline: 'none' }} />
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
  const [authPwd, setAuthPwd] = useState<string>(() => {
    try { return localStorage.getItem('brain_auth') || ''; } catch { return ''; }
  });
  const [authNeeded, setAuthNeeded] = useState<boolean>(() => {
    try { return !localStorage.getItem('brain_auth'); } catch { return true; }
  });
  const chatRef  = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const authHeader = useMemo((): Record<string, string> =>
    authPwd ? { 'Authorization': 'Basic ' + btoa('admin:' + authPwd) } : {}
  , [authPwd]);

  const handleAuth = useCallback((pwd: string) => {
    try { localStorage.setItem('brain_auth', pwd); } catch { /**/ }
    setAuthPwd(pwd); setAuthNeeded(false);
  }, []);

  const poll = useCallback(async () => {
    if (!authPwd) return;
    try {
      const r = await fetch(`/api/status?ticker=${ticker}`, { credentials: 'include', headers: authHeader });
      if (r.status === 401) { setAuthNeeded(true); setAuthPwd(''); try { localStorage.removeItem('brain_auth'); } catch { /**/ } return; }
      if (r.ok) { setData(await r.json()); setLoading(false); }
    } catch { /**/ }
  }, [ticker, authPwd, authHeader]);

  useEffect(() => {
    setLoading(true); setData(null);
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [poll]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [msgs]);

  // Derived display values
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

  const { voices, voiceName, setVoice, muted, setMuted, speaking: ttsSpeaking, speak } = useTTS();
  const speakRef      = useRef(speak);
  const lastSpokenRef = useRef('');
  useEffect(() => { speakRef.current = speak; }, [speak]);
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
        try { localStorage.removeItem('brain_auth'); } catch { /**/ }
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

  // Verdict label + colour
  const statusLabel =
    status === 'READY' && dirn === 'Long'  ? 'READY LONG' :
    status === 'READY' && dirn === 'Short' ? 'READY SHORT' :
    status === 'READY'                     ? 'READY' :
    status;
  const verdictColor =
    status === 'READY'    ? '#4ade80' :
    status === 'MANAGING' ? '#60a5fa' :
    status === 'BUILDING' ? '#fbbf24' :
    'rgba(255,255,255,0.32)';
  const verdictSize = (status === 'READY' || status === 'MANAGING') ? 30 : 22;

  const CSS = `
    @keyframes b-ping    { 0%,100%{transform:scale(1);opacity:.12} 50%{transform:scale(1.18);opacity:.06} }
    @keyframes b-pulse   { 0%,100%{opacity:.22} 50%{opacity:.08} }
    @keyframes b-breathe { 0%,100%{opacity:.9;transform:scale(1)} 50%{opacity:.35;transform:scale(.7)} }
    @keyframes b-dot     { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.4;transform:scale(.6)} }
    @keyframes b-up      { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
    @keyframes b-bounce  { 0%,80%,100%{transform:scale(0)} 40%{transform:scale(1)} }
    .brain-input::placeholder { color:rgba(255,255,255,0.22); }
    .brain-input:focus        { outline:none; }
    ::-webkit-scrollbar       { width:0; height:0; }
    .input-wrap:focus-within  { border-color:rgba(255,255,255,0.26)!important; box-shadow:0 0 0 1px rgba(255,255,255,0.06); }
    /* ── Responsive cockpit ── */
    .cockpit-main {
      flex:1; display:flex; flex-direction:column; align-items:center;
      width:100%; box-sizing:border-box; padding:24px 18px 28px; gap:0;
    }
    .cockpit-center {
      display:flex; flex-direction:column; align-items:center;
      width:100%; max-width:560px;
    }
    .evidence-side { display:none; }
    .evidence-drawer-wrap { display:block; width:100%; }
    @media(min-width:1120px){
      .cockpit-main {
        flex-direction:row; align-items:flex-start; justify-content:center;
        gap:52px; padding:36px 52px 32px; max-width:1280px; margin:0 auto;
      }
      .cockpit-center { max-width:580px; }
      .evidence-side  { display:block; flex-shrink:0; padding-top:28px; }
      .evidence-drawer-wrap { display:none!important; }
    }
    @media(max-width:480px){
      .brain-canvas { width:260px!important; height:329px!important; }
    }
  `;

  if (authNeeded) return (
    <><style>{CSS}</style><LoginOverlay onSubmit={handleAuth} /></>
  );

  return (
    <div style={{ minHeight: '100vh', background: '#000', color: '#fff', display: 'flex', flexDirection: 'column', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', overflow: 'hidden' }}>
      <style>{CSS}</style>

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 28px', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {(['MGC', 'MNQ', 'MES', 'MYM'] as const).map(t => (
            <button key={t} onClick={() => setTicker(t)} style={{ padding: '5px 16px', borderRadius: 20, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 700, fontFamily: 'monospace', letterSpacing: '0.08em', background: ticker === t ? '#fff' : 'transparent', color: ticker === t ? '#000' : 'rgba(255,255,255,0.28)', transition: 'all 0.18s' }}>
              {t}
            </button>
          ))}
          <div style={{ width: 6, height: 6, borderRadius: '50%', marginLeft: 10, background: pal.dot, boxShadow: (status === 'READY' || status === 'MANAGING') ? `0 0 8px ${pal.dot}` : 'none', animation: (status === 'READY' || status === 'MANAGING') ? 'b-dot 1.5s ease-in-out infinite' : 'b-breathe 4s ease-in-out infinite' }} />
        </div>
        <a href="/api/dashboard" style={{ fontSize: 11, color: 'rgba(255,255,255,0.18)', textDecoration: 'none', fontFamily: 'monospace', letterSpacing: '0.06em', transition: 'color 0.2s' }}
          onMouseEnter={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.5)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.18)')}>
          Engineering →
        </a>
      </header>

      {/* ── Cockpit main ───────────────────────────────────────────────────── */}
      <div className="cockpit-main">

        {/* ── Center column ─────────────────────────────────────────────── */}
        <div className="cockpit-center">

          {/* Avatar */}
          <BrainFace speaking={streaming || asking || ttsSpeaking} glow={pal.glow} status={status} />

          {/* Voice controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
            <button onClick={() => setMuted(!muted)}
              style={{ background: 'none', border: `1px solid ${muted ? 'rgba(255,255,255,0.07)' : pal.dot + '55'}`, borderRadius: 20, padding: '4px 12px', color: muted ? 'rgba(255,255,255,0.20)' : ttsSpeaking ? pal.dot : 'rgba(255,255,255,0.42)', cursor: 'pointer', fontSize: 11, fontFamily: 'monospace', letterSpacing: '0.06em', transition: 'all 0.25s', whiteSpace: 'nowrap' as const }}>
              {muted ? '○ voice off' : ttsSpeaking ? '◼ speaking' : '◆ voice on'}
            </button>
            {!muted && voices.length > 0 && (
              <select value={voiceName || (voices[0]?.name ?? '')} onChange={e => setVoice(e.target.value)}
                style={{ background: '#0a0a0a', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 20, padding: '4px 12px', color: 'rgba(255,255,255,0.35)', fontSize: 11, fontFamily: 'monospace', cursor: 'pointer', outline: 'none', maxWidth: 200, letterSpacing: '0.03em' }}>
                {voices.map(v => <option key={v.name} value={v.name} style={{ background: '#111', color: '#ccc' }}>{v.name}</option>)}
              </select>
            )}
          </div>

          {/* ── Verdict block ──────────────────────────────────────────── */}
          <div style={{ textAlign: 'center', margin: '20px 0 4px' }}>
            <div style={{ fontSize: verdictSize, fontWeight: 800, letterSpacing: '-0.01em', fontFamily: 'monospace', color: verdictColor, lineHeight: 1.1, textShadow: status === 'READY' ? `0 0 28px ${verdictColor}55` : 'none' }}>
              {statusLabel}
            </div>
            <div style={{ fontSize: 14, color: 'rgba(255,255,255,0.36)', fontFamily: 'monospace', marginTop: 8, letterSpacing: '0.08em' }}>
              {ticker}
              {edge != null && ` · Edge ${Math.round(Number(edge))}${grade ? ` (${grade})` : ''}`}
              {dirn && dirn !== 'Neither' && (
                <span style={{ color: dirn === 'Long' ? '#4ade80aa' : '#f87171aa', marginLeft: 6 }}>· {dirn}</span>
              )}
            </div>
          </div>

          {/* Rotating micro-thought */}
          <div style={{ height: 16, fontSize: 11, fontFamily: 'monospace', color: '#3f3f46', letterSpacing: '0.05em', marginBottom: 4 }}>
            {microText}
          </div>

          {/* ── Narration — wider, properly wrapping, never clipped ─────── */}
          <div style={{ width: '100%', textAlign: 'center', minHeight: 96, padding: '4px 0 8px', boxSizing: 'border-box' }}>
            {loading ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, marginTop: 32 }}>
                {[0, 1, 2].map(i => (
                  <div key={i} style={{ width: 8, height: 8, borderRadius: '50%', background: '#27272a', animation: `b-bounce 1.4s ease-in-out ${i * 0.16}s infinite` }} />
                ))}
              </div>
            ) : (
              <p style={{ fontSize: 18, lineHeight: 1.88, color: 'rgba(255,255,255,0.72)', fontWeight: 300, margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {displayed}
                {streaming && (
                  <span style={{ display: 'inline-block', width: 2, height: 18, background: 'rgba(255,255,255,0.45)', marginLeft: 2, verticalAlign: 'middle', animation: 'b-dot 0.9s ease-in-out infinite' }} />
                )}
              </p>
            )}
          </div>

          {/* Evidence drawer (mobile only, hidden on desktop via CSS) */}
          <EvidenceDrawer data={data} ticker={ticker} />

          {/* Learning one-liner */}
          {lm.available && lm.note && (
            <div style={{ fontSize: 11, color: '#3f3f46', fontFamily: 'monospace', textAlign: 'center', maxWidth: 480, marginTop: 6 }}>
              {lm.note}
            </div>
          )}

          {/* Setup progress bar */}
          {mission !== null && mission > 0 && (status === 'BUILDING' || status === 'READY') && (
            <div style={{ width: '100%', maxWidth: 320, marginTop: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#3f3f46', fontFamily: 'monospace', marginBottom: 6 }}>
                <span>Setup progress</span><span>{mission}%</span>
              </div>
              <div style={{ height: 2, background: 'rgba(255,255,255,0.05)', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{ height: '100%', borderRadius: 2, width: `${mission}%`, background: status === 'READY' ? '#4ade80' : '#fbbf24', transition: 'width 1.2s ease' }} />
              </div>
            </div>
          )}

          {/* ── Conversation ──────────────────────────────────────────── */}
          <div style={{ width: '100%', marginTop: 28 }}>

            {/* Chat history */}
            {msgs.length > 0 && (
              <div ref={chatRef} style={{ marginBottom: 14, maxHeight: 220, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
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

            {/* Input bar — more visible, cleaner focus */}
            <div className="input-wrap" style={{ display: 'flex', alignItems: 'center', gap: 12, border: '1px solid rgba(255,255,255,0.14)', borderRadius: 28, padding: '14px 20px', background: 'rgba(255,255,255,0.030)', transition: 'border-color 0.2s, box-shadow 0.2s' }}>
              <input ref={inputRef} className="brain-input" type="text"
                value={input} onChange={e => setInput(e.target.value)} onKeyDown={onKey}
                placeholder="Ask the Brain..."
                style={{ flex: 1, background: 'transparent', border: 'none', fontSize: 14, color: 'rgba(255,255,255,0.82)', fontFamily: 'inherit' }} />
              <button onClick={() => ask()} disabled={!input.trim() || asking}
                style={{ background: 'transparent', border: 'none', padding: 0, cursor: input.trim() && !asking ? 'pointer' : 'default', color: input.trim() && !asking ? 'rgba(255,255,255,0.60)' : 'rgba(255,255,255,0.14)', transition: 'color 0.2s', display: 'flex', alignItems: 'center' }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
              </button>
            </div>

            {/* Quick prompts */}
            {msgs.length === 0 && <Chips status={status} onSelect={ask} />}
          </div>
        </div>

        {/* ── Evidence side panel (desktop only, hidden on mobile via CSS) ─ */}
        <div className="evidence-side">
          <EvidencePanel data={data} ticker={ticker} />
        </div>

      </div>
    </div>
  );
}
