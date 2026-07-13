import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import LordPiggingtonAvatar from '../components/avatar/LordPiggingtonAvatar';
import AvatarAura from '../components/avatar/AvatarAura';

// ── Constants ──────────────────────────────────────────────────────────────────
const BULL = '#22c55e'; const BEAR = '#ef4444'; const AMB = '#f59e0b';
const MUTED = 'rgba(255,255,255,0.24)';
const BLUE = '#3b82f6'; const CYAN = '#38bdf8';

type Ticker = 'MNQ' | 'MGC' | 'MES' | 'MYM';
type AvatarState = 'WAIT' | 'ANALYZING' | 'FORMING' | 'READY_LONG' | 'READY_SHORT' | 'NO_EDGE' | 'ACTIVE' | 'STOP_HIT' | 'TARGET_HIT';

const AV_CFG: Record<AvatarState, { mesh: [number,number,number]; eye: [number,number,number]; dim: number }> = {
  WAIT:        { mesh: [40,  110, 230], eye: [80,  150, 255], dim: 0.60 },
  ANALYZING:   { mesh: [50,  130, 245], eye: [90,  165, 255], dim: 0.72 },
  FORMING:     { mesh: [200, 140,  35], eye: [245, 175,  60], dim: 0.85 },
  READY_LONG:  { mesh: [20,  185, 105], eye: [50,  225, 145], dim: 1.00 },
  READY_SHORT: { mesh: [225,  55,  72], eye: [255,  88, 108], dim: 0.95 },
  NO_EDGE:     { mesh: [45,   55, 125], eye: [70,   85, 175], dim: 0.30 },
  ACTIVE:      { mesh: [18,  165, 225], eye: [60,  205, 255], dim: 1.10 },
  STOP_HIT:    { mesh: [180,  70,  80], eye: [220, 100, 110], dim: 0.65 },
  TARGET_HIT:  { mesh: [180, 165,  30], eye: [220, 200,  55], dim: 0.90 },
};

const fmt = (n: number | null | undefined, dec = 2): string =>
  n != null ? Number(n).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) : '—';

const dirClr = (d: string | null | undefined): string =>
  /long|bull/i.test(d ?? '') ? BULL : /short|bear/i.test(d ?? '') ? BEAR : 'rgba(255,255,255,0.55)';

const statusClr = (s: string): string =>
  s === 'READY' ? BULL : s === 'MANAGING' ? CYAN : s === 'BUILDING' ? AMB : MUTED;

// ── Clock ───────────────────────────────────────────────────────────────────────
function useClock() {
  const [time, setTime] = useState('');
  useEffect(() => {
    const tick = () => setTime(
      new Date().toLocaleTimeString('en-US', {
        hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'America/New_York',
      }) + ' ET'
    );
    tick(); const id = setInterval(tick, 1000); return () => clearInterval(id);
  }, []);
  return time;
}

// ── Text stream ────────────────────────────────────────────────────────────────
function useStream(target: string, msPerChar = 13) {
  const [text, setText] = useState('');
  const [live, setLive] = useState(false);
  const prev = useRef(''); const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!target || target === prev.current) return;
    prev.current = target;
    if (timer.current) clearInterval(timer.current);
    setText(''); setLive(true); let i = 0;
    timer.current = setInterval(() => {
      i++; setText(target.slice(0, i));
      if (i >= target.length) { clearInterval(timer.current!); setLive(false); }
    }, msPerChar);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [target, msPerChar]);
  return { text, live };
}

// ── Speech control — written by useTTS energy loop, read each frame by AvatarCanvas ─
interface SpeechCtrl { energy: number; viseme: string; active: boolean; }
function charToViseme(c: string): string {
  const lc = (c || '').toLowerCase();
  if (lc === 'o') return 'rounded';
  if ('aiu'.includes(lc)) return 'open';
  if (lc === 'e') return 'narrow';
  if ('mbp'.includes(lc)) return 'press';
  if ('fv'.includes(lc)) return 'narrow';
  return 'open';
}

// ── TTS ─────────────────────────────────────────────────────────────────────────
function useTTS() {
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [voiceName, setVoiceN] = useState<string>(() => { try { return localStorage.getItem('brain_voice') ?? ''; } catch { return ''; } });
  const [muted, setMutedState] = useState<boolean>(() => { try { const v = localStorage.getItem('brain_muted'); return v === null ? false : v !== '0'; } catch { return false; } });
  const [speaking, setSpeaking] = useState(false);
  // Shared energy ref: written by the RAF loop below, read by AvatarCanvas draw loop (no React re-renders)
  const speechCtrlRef = useRef<SpeechCtrl>({ energy: 0, viseme: 'rest', active: false });
  const energyRafRef  = useRef(0);
  const wordDataRef   = useRef({ t0: Date.now(), dur: 250, char: 'a' });

  useEffect(() => {
    const ss = window.speechSynthesis; if (!ss) return;
    const load = () => { const all = ss.getVoices(); setVoices(all.length ? all : []); };
    load(); ss.addEventListener('voiceschanged', load); return () => ss.removeEventListener('voiceschanged', load);
  }, []);
  const setVoice = useCallback((name: string) => { try { localStorage.setItem('brain_voice', name); } catch {} setVoiceN(name); }, []);
  const setMuted = useCallback((m: boolean) => {
    try { localStorage.setItem('brain_muted', m ? '1' : '0'); } catch {}
    if (m) {
      window.speechSynthesis?.cancel();
      setSpeaking(false);
      cancelAnimationFrame(energyRafRef.current);
      speechCtrlRef.current = { energy: 0, viseme: 'rest', active: false };
    }
    setMutedState(m);
  }, []);
  const speak = useCallback((text: string) => {
    const ss = window.speechSynthesis; if (!text || muted || !ss) return;
    ss.cancel();
    cancelAnimationFrame(energyRafRef.current);

    // Expand trading abbreviations so TTS reads them naturally instead of spelling
    const cleaned = text
      .replace(/\bBOS\b/g,   'break of structure')
      .replace(/\bCHOCH\b/g, 'change of character')
      .replace(/\bVWAP\b/gi, 'vee-wap')
      .replace(/\bCVD\b/g,   'cumulative delta')
      .replace(/\bRVOL\b/g,  'relative volume')
      .replace(/\bHTF\b/g,   'higher timeframe')
      .replace(/\bLTF\b/g,   'lower timeframe')
      .replace(/\bATR\b/g,   'average true range')
      .replace(/\bFVG\b/g,   'fair value gap')
      .replace(/\bPOI\b/g,   'point of interest')
      .replace(/\bR:R\b/gi,  'risk to reward')
      .replace(/\bHH\b/g,    'higher high')
      .replace(/\bHL\b/g,    'higher low')
      .replace(/\bLH\b/g,    'lower high')
      .replace(/\bLL\b/g,    'lower low')
      .replace(/\bTP\b/g,    'take profit')
      .replace(/\bSL\b/g,    'stop loss')
      .replace(/\bBE\b/g,    'break even')
      .replace(/\bMNQ\b/g,   'mini nasdaq')
      .replace(/\bMGC\b/g,   'micro gold')
      .replace(/\bMES\b/g,   'micro S&P')
      .replace(/\bMYM\b/g,   'micro Dow')
      .replace(/\b1R\b/gi,   'one R')
      .replace(/\b2R\b/gi,   'two R')
      .replace(/\b3R\b/gi,   'three R')
      .replace(/\b4R\b/gi,   'four R')
      .slice(0, 450);

    const utt = new SpeechSynthesisUtterance(cleaned);
    const voice = voices.find(v => v.name === voiceName) ?? voices[0]; if (voice) utt.voice = voice;
    utt.rate = 0.92; utt.pitch = 1.05;

    utt.onstart = () => {
      setSpeaking(true);
      speechCtrlRef.current = { energy: 0, viseme: 'open', active: true };
      wordDataRef.current = { t0: Date.now(), dur: 280, char: 'a' };
      // Drive mouth energy via a dedicated RAF loop — no React renders, no jitter
      const driveEnergy = () => {
        const ctrl = speechCtrlRef.current;
        if (!ctrl.active) return;
        const { t0, dur, char } = wordDataRef.current;
        const age  = Date.now() - t0;
        const prog = Math.min(1, age / Math.max(1, dur));
        // Bell-curve energy per word: ramp up, sustain, ramp down
        const bell = prog < 0.28 ? prog / 0.28 : prog < 0.70 ? 1.0 : Math.max(0, (1 - prog) / 0.30);
        // Natural micro-jitter — two low-freq oscillators
        const jit = Math.sin(Date.now() * 0.020) * 0.13 + Math.sin(Date.now() * 0.035) * 0.08;
        const tgt  = Math.max(0, Math.min(1, bell * 0.74 + jit * bell));
        // Exponential smoothing — prevents shaky mouth
        ctrl.energy += (tgt - ctrl.energy) * 0.18;
        ctrl.viseme  = charToViseme(char);
        energyRafRef.current = requestAnimationFrame(driveEnergy);
      };
      energyRafRef.current = requestAnimationFrame(driveEnergy);
    };

    // Word-boundary events: update word timing for next energy bell
    utt.onboundary = (e: SpeechSynthesisEvent) => {
      if (e.name !== 'word') return;
      const rest     = text.slice(0, 400).slice(e.charIndex);
      const spaceIdx = rest.indexOf(' ');
      const word     = spaceIdx > 0 ? rest.slice(0, spaceIdx) : rest.slice(0, 8);
      wordDataRef.current = { t0: Date.now(), dur: Math.max(130, word.length * 88), char: word[0]?.toLowerCase() ?? 'a' };
    };

    const stopEnergy = () => {
      setSpeaking(false);
      speechCtrlRef.current.active = false;
      cancelAnimationFrame(energyRafRef.current);
      // Smooth decay back to closed mouth
      const decay = () => {
        speechCtrlRef.current.energy *= 0.86;
        if (speechCtrlRef.current.energy > 0.015) {
          energyRafRef.current = requestAnimationFrame(decay);
        } else {
          speechCtrlRef.current.energy = 0;
          speechCtrlRef.current.viseme = 'rest';
        }
      };
      energyRafRef.current = requestAnimationFrame(decay);
    };
    utt.onend = stopEnergy; utt.onerror = stopEnergy;
    ss.speak(utt);
  }, [voices, voiceName, muted]);

  // One-shot warm-up: mobile browsers block async speechSynthesis until a user gesture
  // has directly called ss.speak(). Call unlockAudio() inside a click/touch handler.
  const audioUnlockedRef = useRef(false);
  const unlockAudio = useCallback(() => {
    const ss = window.speechSynthesis;
    if (!ss || audioUnlockedRef.current) return;
    const warmup = new SpeechSynthesisUtterance('');
    warmup.volume = 0;
    ss.speak(warmup);
    audioUnlockedRef.current = true;
  }, []);

  // Auto-unlock on first touch anywhere — mobile browsers need a real gesture
  // before speechSynthesis.speak() works from async contexts (useEffect polls).
  useEffect(() => {
    const handler = () => unlockAudio();
    document.addEventListener('touchstart', handler, { once: true, capture: true, passive: true });
    return () => document.removeEventListener('touchstart', handler, { capture: true });
  }, [unlockAudio]);

  return { voices, voiceName, setVoice, muted, setMuted, speaking, speak, speechCtrlRef, unlockAudio };
}

// ── Voice input ───────────────────────────────────────────────────────────────
type VoiceState = 'idle' | 'requesting' | 'listening' | 'processing' | 'error';

function useVoiceInput({ onTranscript }: { onTranscript: (t: string) => void }) {
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [transcript, setTranscript] = useState('');
  const [errorMsg,   setErrorMsg]   = useState('');
  const recognitionRef = useRef<any>(null);
  const streamRef      = useRef<MediaStream | null>(null);
  const finalRef       = useRef('');

  const releaseMic = useCallback(() => {
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
  }, []);

  const startListening = useCallback(async () => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setErrorMsg('Voice input requires Chrome or Safari. Type your question below.');
      setVoiceState('error'); return;
    }
    setVoiceState('requesting');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      setVoiceState('listening');
      finalRef.current = ''; setTranscript('');

      const rec = new SR();
      recognitionRef.current = rec;
      rec.continuous = false; rec.interimResults = true;
      rec.lang = 'en-US'; rec.maxAlternatives = 1;

      rec.onresult = (e: any) => {
        let interim = '';
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) finalRef.current += e.results[i][0].transcript;
          else interim += e.results[i][0].transcript;
        }
        setTranscript(finalRef.current + interim);
      };

      rec.onend = () => {
        releaseMic();
        const t = finalRef.current.trim();
        if (t) { setVoiceState('processing'); onTranscript(t); }
        else   { setVoiceState('idle'); setTranscript(''); }
      };

      rec.onerror = (e: any) => {
        releaseMic();
        if (e.error === 'no-speech') { setVoiceState('idle'); setTranscript(''); return; }
        setErrorMsg(
          (e.error === 'not-allowed' || e.error === 'service-not-allowed')
            ? 'Microphone access blocked. Enable it in your browser settings.'
            : e.error === 'audio-capture'
              ? 'No microphone detected. Connect a microphone and try again.'
              : 'Voice error: ' + (e.error || 'unknown')
        );
        setVoiceState('error');
      };

      rec.start();
    } catch (err: any) {
      releaseMic();
      setErrorMsg(
        (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError')
          ? 'Microphone access blocked. Enable it in your browser settings.'
          : err.name === 'NotFoundError'
            ? 'No microphone detected. Connect a microphone and try again.'
            : 'Microphone error: ' + (err.message || err.name)
      );
      setVoiceState('error');
    }
  }, [releaseMic, onTranscript]);

  const stopListening = useCallback(() => {
    try { recognitionRef.current?.stop(); } catch {}
    // onend fires automatically and submits any collected transcript
  }, []);

  const cancelListening = useCallback(() => {
    try { recognitionRef.current?.abort(); } catch {}
    releaseMic();
    setVoiceState('idle'); setTranscript(''); setErrorMsg(''); finalRef.current = '';
  }, [releaseMic]);

  const clearError = useCallback(() => { setErrorMsg(''); setVoiceState('idle'); }, []);

  useEffect(() => () => {
    releaseMic();
    try { recognitionRef.current?.abort(); } catch {}
  }, [releaseMic]);

  return { voiceState, setVoiceState, transcript, errorMsg, startListening, stopListening, cancelListening, clearError };
}

// ── Candle data ────────────────────────────────────────────────────────────────
type Candle = { t: number; o: number; h: number; l: number; c: number; vol: number };
function makeCandles(base: number, n = 60): Candle[] {
  const out: Candle[] = []; let price = base * 0.9975; const step = base * 0.00065;
  for (let i = 0; i < n; i++) {
    const o = price; const body = (Math.random() - 0.468) * step; const c = o + body;
    const wick = step * 0.55;
    out.push({ t: Date.now() - (n - i) * 60000, o, h: Math.max(o, c) + Math.random() * wick, l: Math.min(o, c) - Math.random() * wick, c, vol: 0.25 + Math.random() * 1.75 });
    price = c;
  }
  return out;
}

// ── Gate checklist ─────────────────────────────────────────────────────────────
function getBrainChecklist(data: any): Array<{ text: string; st: 'pass' | 'fail' | 'wait' | 'neutral' }> {
  if (!data) return [];
  const gd = data.gate_debug || {}; const sig = (data.main_brain || {}).signals || {};
  const ad = data.alert_diagnostics || {}; const price = Number(data.price || 0); const vwap = Number(data.vwap_value || 0);
  const items: Array<{ text: string; st: 'pass' | 'fail' | 'wait' | 'neutral' }> = [];
  if (vwap > 0 && price > 0) { const above = price > vwap; items.push({ text: `VWAP ${above ? 'above' : 'below'}`, st: above ? 'pass' : 'wait' }); }
  if (gd.structure_confirmed != null) items.push({ text: gd.structure_confirmed ? 'Structure ✓' : 'No structure', st: gd.structure_confirmed ? 'pass' : 'wait' });
  if (gd.zone_valid != null) items.push({ text: gd.zone_valid ? 'Zone intact' : 'No zone', st: gd.zone_valid ? 'pass' : 'neutral' });
  const cvd = sig.cvd; if (cvd && cvd !== 'unknown') items.push({ text: `Flow ${String(cvd).toUpperCase()}`, st: /bull|pos/.test(cvd) ? 'pass' : /bear|neg/.test(cvd) ? 'fail' : 'neutral' });
  if (ad.volume && ad.volume !== 'unknown') items.push({ text: `Vol ${String(ad.volume).toUpperCase()}`, st: /incr|strong|high/i.test(ad.volume) ? 'pass' : 'neutral' });
  return items.slice(0, 5);
}

// ── Synthetic AI face canvas ───────────────────────────────────────────────────
type GazeEvt = { dx: number; dy: number; widen: boolean; dur: number; id: number };


// ── Login overlay ──────────────────────────────────────────────────────────────
function LoginOverlay({ onSubmit }: { onSubmit: (pwd: string) => void }) {
  const [val, setVal] = useState(''); const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { setTimeout(() => ref.current?.focus(), 80); }, []);
  const submit = () => { const p = val.trim(); if (p) onSubmit(p); };
  return (
    <div style={{ position:'fixed', inset:0, background:'#060810', display:'flex', flexDirection:'column',
      alignItems:'center', justifyContent:'center', gap:28, zIndex:999 }}>
      <div style={{ position:'relative', width:72, height:72, display:'flex', alignItems:'center', justifyContent:'center' }}>
        <div style={{ position:'absolute', inset:0, borderRadius:'50%', border:'1px solid #3b82f655',
          animation:'bPulse 2.8s ease-in-out infinite' }} />
        <div style={{ width:44, height:44, borderRadius:'50%', border:'1px solid #3b82f633',
          background:'radial-gradient(circle at 35% 35%, rgba(59,130,246,0.18), rgba(0,0,0,0.7))',
          display:'flex', alignItems:'center', justifyContent:'center' }}>
          <div style={{ width:6, height:6, borderRadius:'50%', background:BLUE, animation:'bBreathe 3s ease-in-out infinite' }} />
        </div>
      </div>
      <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:6 }}>
        <span style={{ fontSize:13, color:'rgba(255,255,255,0.5)', fontFamily:'monospace', letterSpacing:'0.10em' }}>ACCESS REQUIRED</span>
        <span style={{ fontSize:11, color:'#374151', fontFamily:'monospace' }}>Enter your dashboard password</span>
      </div>
      <form onSubmit={e => { e.preventDefault(); submit(); }} style={{ display:'flex', gap:8, width:300 }}>
        <input ref={ref} type="password" value={val} onChange={e => setVal(e.target.value)}
          placeholder="Password" style={{ flex:1, background:'rgba(255,255,255,0.04)', border:'1px solid rgba(255,255,255,0.10)',
          borderRadius:8, padding:'10px 14px', fontSize:14, color:'rgba(255,255,255,0.85)', fontFamily:'inherit', outline:'none' }} />
        <button type="submit" style={{ padding:'10px 18px', background:'rgba(59,130,246,0.15)',
          border:'1px solid rgba(59,130,246,0.3)', borderRadius:8, color:'#93c5fd', fontSize:13, fontFamily:'inherit', cursor:'pointer' }}>Enter</button>
      </form>
    </div>
  );
}

// ── Chat bubbles ───────────────────────────────────────────────────────────────
interface Msg { id: number; role: 'user' | 'brain'; text: string; }
let _mid = 0;
const mkMsg = (role: Msg['role'], text: string): Msg => ({ id: ++_mid, role, text });

function BrainBubble({ msg }: { msg: Msg }) {
  const { text, live } = useStream(msg.role === 'brain' ? msg.text : '', 11);
  const shown = msg.role === 'brain' ? text : msg.text;
  const isBrain = msg.role === 'brain';
  return (
    <div style={{ display:'flex', justifyContent: isBrain ? 'flex-start' : 'flex-end',
      animation:'bUp 0.2s ease-out', marginBottom:6 }}>
      <div style={{ maxWidth:'82%', padding:'8px 12px', borderRadius: isBrain ? '4px 12px 12px 12px' : '12px 4px 12px 12px',
        background: isBrain ? 'rgba(59,130,246,0.08)' : 'rgba(255,255,255,0.06)',
        border: `1px solid ${isBrain ? 'rgba(59,130,246,0.20)' : 'rgba(255,255,255,0.08)'}`,
        fontSize:13, lineHeight:1.55, color: isBrain ? 'rgba(255,255,255,0.82)' : 'rgba(255,255,255,0.65)' }}>
        {shown}{live && <span style={{ opacity:0.5, animation:'bDot 0.8s infinite' }}>▌</span>}
      </div>
    </div>
  );
}

// ── Compact candlestick chart ──────────────────────────────────────────────────
function CandleChart({ candles, vwap, demand, supply, ticker }: {
  candles: Candle[]; vwap?: number; demand?: number; supply?: number; ticker: string;
}) {
  if (!candles.length) return <div style={{ flex:1, display:'flex', alignItems:'center', justifyContent:'center', color:MUTED, fontSize:12 }}>—</div>;
  const W = 1000; const CH = 160; const VH = 28; const H = CH + VH;
  const n = candles.length; const slotW = W / n; const bodyW = slotW * 0.68; const pad = slotW * 0.16;
  const allH = candles.map(c => c.h); const allL = candles.map(c => c.l);
  let minP = Math.min(...allL); let maxP = Math.max(...allH);
  if (demand != null) minP = Math.min(minP, demand * 0.9995);
  if (supply != null) maxP = Math.max(maxP, supply * 1.0005);
  const rng = maxP - minP || 1;
  const pY = (p: number) => ((maxP - p) / rng) * CH;
  const maxV = Math.max(...candles.map(c => c.vol), 0.1);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" preserveAspectRatio="none" style={{ display:'block' }}>
      {demand != null && <rect x={0} y={Math.max(0,pY(demand+rng*0.004))} width={W} height={Math.max(4,pY(demand-rng*0.006)-pY(demand+rng*0.004))} fill="#22c55e12" />}
      {supply != null && <rect x={0} y={Math.max(0,pY(supply+rng*0.004))} width={W} height={Math.max(4,pY(supply-rng*0.004)-pY(supply+rng*0.006))} fill="#ef444412" />}
      {vwap != null && vwap >= minP && vwap <= maxP && <line x1={0} y1={pY(vwap)} x2={W} y2={pY(vwap)} stroke="#60a5fa" strokeWidth={1.5} strokeDasharray="6,4" opacity={0.65} />}
      {candles.map((c, i) => {
        const bull = c.c >= c.o; const col = bull ? '#22c55e' : '#ef4444';
        const bodyTop = Math.min(pY(c.o), pY(c.c)); const bodyH = Math.max(1.5, Math.abs(pY(c.o) - pY(c.c)));
        const wx = i * slotW + slotW / 2;
        return <g key={i}><line x1={wx} y1={pY(c.h)} x2={wx} y2={pY(c.l)} stroke={col} strokeWidth={1} opacity={0.7} /><rect x={i*slotW+pad} y={bodyTop} width={bodyW} height={bodyH} fill={col} opacity={0.88} rx={0.4} /></g>;
      })}
      {candles.map((c, i) => { const bull = c.c >= c.o; const vh = (c.vol / maxV) * VH; return <rect key={i} x={i*slotW+pad} y={CH+VH-vh} width={bodyW} height={vh} fill={bull ? '#22c55e' : '#ef4444'} opacity={0.22} />; })}
      {candles.length > 0 && (() => { const lc = candles[candles.length-1].c; return <>
        <line x1={0} y1={pY(lc)} x2={W} y2={pY(lc)} stroke="rgba(255,255,255,0.15)" strokeWidth={1} strokeDasharray="3,3" />
        <rect x={W-80} y={pY(lc)-9} width={80} height={18} fill="#1e293b" rx={3} />
        <text x={W-5} y={pY(lc)+4.5} textAnchor="end" fill="rgba(255,255,255,0.75)" fontSize={11} style={{ fontFamily:'monospace' }}>{fmt(lc)}</text>
      </>; })()}
    </svg>
  );
}

// ── Edge bar ───────────────────────────────────────────────────────────────────
function EdgeBar({ score, max = 110, color = BLUE }: { score: number; max?: number; color?: string }) {
  const pct = Math.max(0, Math.min(score, max)) / max;
  return (
    <div style={{ display:'flex', alignItems:'center', gap:10 }}>
      <div style={{ flex:1, height:5, borderRadius:3, background:'rgba(255,255,255,0.07)', overflow:'hidden' }}>
        <div style={{ width:`${pct*100}%`, height:'100%', background:color, borderRadius:3, transition:'width 1.2s ease', boxShadow:`0 0 8px ${color}66` }} />
      </div>
      <span style={{ fontSize:13, fontWeight:700, fontFamily:'monospace', color, minWidth:28, textAlign:'right' }}>
        {Math.round(score)}
      </span>
    </div>
  );
}

// ── Evidence Radar ────────────────────────────────────────────────────────────
type EvStrength = 'inactive' | 'neutral' | 'developing' | 'confirmed' | 'invalidated';
interface EvidenceItem { label: string; strength: EvStrength; }

const EV_COLOR: Record<EvStrength, string> = {
  inactive:    'rgba(255,255,255,0.16)',
  neutral:     '#3b82f6',
  developing:  '#f59e0b',
  confirmed:   '#22c55e',
  invalidated: '#ef4444',
};
const EV_GLOW: Record<EvStrength, string> = {
  inactive:    'none',
  neutral:     '0 0 7px #3b82f650',
  developing:  '0 0 10px #f59e0b88',
  confirmed:   '0 0 12px #22c55eaa',
  invalidated: '0 0 10px #ef444488',
};

function getEvidenceRadar(
  data: any, gd: Record<string,any>, ad: Record<string,any>,
  sig: Record<string,any>, edge: number
): EvidenceItem[] {
  const price = Number(data?.price          || 0);
  const vwap  = Number(data?.vwap_value     || 0);
  const cvd   = String(sig.cvd || ad.cvd    || '').toLowerCase();
  const vol   = String(ad.volume            || '').toLowerCase();
  const vReg  = String(ad.volatility_regime || '').toLowerCase();
  const bias  = String(sig.bias             || '').toLowerCase();

  const structure: EvStrength =
    gd.structure_confirmed ? 'confirmed' :
    (bias && bias !== 'neutral' && bias !== 'unknown') ? 'developing' : 'inactive';

  let vwapS: EvStrength = 'inactive';
  if (price > 0 && vwap > 0) {
    if (gd.vwap_confirmed)                             vwapS = 'confirmed';
    else if (Math.abs(price - vwap) / vwap < 0.0012)  vwapS = 'neutral';
    else if (price > vwap)                             vwapS = 'developing';
    else                                                vwapS = 'invalidated';
  }

  const liquidity: EvStrength =
    gd.zone_valid                                  ? 'confirmed' :
    (data?.nearest_demand || data?.nearest_supply) ? 'neutral'   : 'inactive';

  const volume: EvStrength =
    /strong|high/.test(vol)  ? 'confirmed'   :
    /incr/.test(vol)          ? 'developing'  :
    /low|thin/.test(vol)      ? 'inactive'    : 'neutral';

  const delta: EvStrength =
    /bull|pos/.test(cvd)  ? 'confirmed'   :
    /bear|neg/.test(cvd)  ? 'invalidated' : 'neutral';

  const orderFlow: EvStrength =
    delta === 'confirmed'   && volume !== 'inactive' ? 'confirmed'   :
    delta === 'invalidated'                          ? 'invalidated' :
    delta !== 'neutral'  || volume === 'confirmed'   ? 'developing'  : 'neutral';

  const trend: EvStrength =
    /bull/.test(bias) ? 'confirmed'   :
    /bear/.test(bias) ? 'invalidated' :
    bias              ? 'neutral'     : 'inactive';

  const momentum: EvStrength =
    edge >= 75 ? 'confirmed'  :
    edge >= 55 ? 'developing' :
    edge >= 30 ? 'neutral'    : 'inactive';

  const volatility: EvStrength =
    /extreme/.test(vReg)   ? 'invalidated' :
    /elev/.test(vReg)      ? 'developing'  :
    /quiet|low/.test(vReg) ? 'confirmed'   : 'neutral';

  let htf: EvStrength = 'inactive';
  const swCtx = data?.swing_context;
  if (swCtx?.htf_bias_aligned !== undefined) htf = swCtx.htf_bias_aligned ? 'confirmed' : 'invalidated';
  else if (sig.htf_aligned !== undefined)    htf = sig.htf_aligned        ? 'confirmed' : 'developing';
  else if (bias && bias !== 'neutral')       htf = 'neutral';

  return [
    { label: 'Structure',  strength: structure  },
    { label: 'VWAP',       strength: vwapS      },
    { label: 'Liquidity',  strength: liquidity  },
    { label: 'Volume',     strength: volume     },
    { label: 'Delta',      strength: delta      },
    { label: 'Order Flow', strength: orderFlow  },
    { label: 'Trend',      strength: trend      },
    { label: 'Momentum',   strength: momentum   },
    { label: 'Volatility', strength: volatility },
    { label: 'Higher TF',  strength: htf        },
  ];
}

// ── Mission Control Card ──────────────────────────────────────────────────────
// ── Evidence connector lines ───────────────────────────────────────────────────
type SigColor = 'gray' | 'blue' | 'yellow' | 'green' | 'red';
const SIG_LINE: Record<SigColor, { stroke: string; w: number; dash: string }> = {
  gray:   { stroke: 'rgba(120,132,155,0.13)', w: 0.65, dash: '2 9' },
  blue:   { stroke: 'rgba(96,165,250,0.38)',  w: 0.90, dash: '4 6' },
  yellow: { stroke: 'rgba(251,191,36,0.44)',  w: 1.00, dash: '3 5' },
  green:  { stroke: 'rgba(34,197,94,0.50)',   w: 1.10, dash: '4 5' },
  red:    { stroke: 'rgba(239,68,68,0.47)',   w: 1.00, dash: '3 5' },
};
const SIG_PTCL: Record<SigColor, string> = {
  gray:   'transparent',
  blue:   'rgba(147,197,253,0.72)',
  yellow: 'rgba(253,224,71,0.82)',
  green:  'rgba(74,222,128,0.90)',
  red:    'rgba(252,165,165,0.85)',
};
const SIG_PR:  Record<SigColor, number> = { gray: 0,   blue: 1.4, yellow: 1.7, green: 2.0, red: 1.8 };
const SIG_DUR: Record<SigColor, number> = { gray: 99,  blue: 3.8, yellow: 2.8, green: 2.2, red: 2.5 };
// SVG coordinate space: 618 × 584 px
//   width:  leftCol(130) + gap(8) + avatar(342) + gap(8) + rightCol(130) = 618
//   height: topRow(~56) + gap(8) + midRow(455) + gap(8) + botRow(~56) = 583
const AVC_X = 309, AVC_Y = 291, AVC_R = 118;
const MC_CARDS: { cx: number; cy: number; id: string }[] = [
  { cx: 100, cy:  28, id: 'edge'     }, // top-row col 0
  { cx: 309, cy:  28, id: 'winprob'  }, // top-row col 1
  { cx: 518, cy:  28, id: 'strategy' }, // top-row col 2
  { cx:  65, cy: 137, id: 'bias'     }, // left-col row 0
  { cx:  65, cy: 291, id: 'struct'   }, // left-col row 1
  { cx:  65, cy: 446, id: 'liq'      }, // left-col row 2
  { cx: 553, cy: 137, id: 'vwap'     }, // right-col row 0
  { cx: 553, cy: 291, id: 'flow'     }, // right-col row 1
  { cx: 553, cy: 446, id: 'volume'   }, // right-col row 2
  { cx: 100, cy: 555, id: 'plan'     }, // bot-row col 0
  { cx: 309, cy: 555, id: 'volt'     }, // bot-row col 1
  { cx: 518, cy: 555, id: 'risk'     }, // bot-row col 2
];
function connPts(cx: number, cy: number) {
  const dx = AVC_X - cx, dy = AVC_Y - cy;
  const d  = Math.sqrt(dx * dx + dy * dy) || 1;
  return {
    x1: (cx + dx / d * 24).toFixed(1), y1: (cy + dy / d * 24).toFixed(1),
    x2: (AVC_X - dx / d * AVC_R).toFixed(1), y2: (AVC_Y - dy / d * AVC_R).toFixed(1),
  };
}
function ConnectorSVG({ sigs, flashIds }: { sigs: SigColor[]; flashIds: Set<string> }) {
  return (
    <svg viewBox="0 0 618 584" style={{
      position: 'absolute', inset: 0, width: '100%', height: '100%',
      pointerEvents: 'none', zIndex: 0, overflow: 'visible',
    }}>
      <defs>
        {MC_CARDS.map(c => {
          const p = connPts(c.cx, c.cy);
          return <path key={c.id} id={`cp-${c.id}`}
            d={`M${p.x1},${p.y1} L${p.x2},${p.y2}`} fill="none" />;
        })}
      </defs>
      {MC_CARDS.map((c, i) => {
        const sig   = (sigs[i] ?? 'gray') as SigColor;
        const p     = connPts(c.cx, c.cy);
        const ls    = SIG_LINE[sig];
        const active = sig !== 'gray';
        const flash  = flashIds.has(c.id);
        return (
          <g key={c.id}>
            {/* Static dashed connector */}
            <line x1={p.x1} y1={p.y1} x2={p.x2} y2={p.y2}
              stroke={ls.stroke} strokeWidth={ls.w} strokeLinecap="round"
              strokeDasharray={ls.dash} />
            {/* One-shot brighten on signal change */}
            {flash && (
              <line x1={p.x1} y1={p.y1} x2={p.x2} y2={p.y2}
                stroke={SIG_PTCL[sig]} strokeWidth={ls.w + 1.5} strokeLinecap="round"
                style={{ animation: 'connFlash 1.4s ease-out forwards' } as React.CSSProperties} />
            )}
            {/* Particle flowing toward avatar */}
            {active && (
              <circle r={SIG_PR[sig]} fill={SIG_PTCL[sig]}>
                <animateMotion
                  dur={`${SIG_DUR[sig]}s`}
                  repeatCount="indefinite"
                  begin={`-${((i * 0.31) % SIG_DUR[sig]).toFixed(2)}s`}
                  keyPoints="0;1" keyTimes="0;1" calcMode="linear">
                  <mpath href={`#cp-${c.id}`} />
                </animateMotion>
              </circle>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function McCard({ label, value, sub, col = 'rgba(255,255,255,0.75)', delay = 0, dot }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode;
  col?: string; delay?: number; dot?: EvStrength;
}) {
  const dotColor = dot ? EV_COLOR[dot] : undefined;
  const dotGlow  = dot ? EV_GLOW[dot]  : undefined;
  const pulse    = dot === 'developing';
  return (
    <div className="mc-card" style={{ animationDelay:`${delay}s` }}>
      <div style={{ display:'flex', alignItems:'center', gap:5, marginBottom:2 }}>
        {dot && <div style={{ width:5, height:5, borderRadius:'50%', flexShrink:0,
          background:dotColor, boxShadow:dotGlow,
          animation: pulse ? 'evPulse 2.2s ease-in-out infinite' : undefined }} />}
        <span className="mc-label">{label}</span>
      </div>
      <div className="mc-value" style={{ color:col }}>{value}</div>
      {sub && <div className="mc-sub">{sub}</div>}
    </div>
  );
}

// ── Corner Satellite Panel — small glassmorphic chip for avatar corners ────────
function CornerSat({ label, value, sub, col = 'rgba(255,255,255,0.80)', align = 'left' }: {
  label: string; value: React.ReactNode; sub?: string;
  col?: string; align?: 'left' | 'right';
}) {
  return (
    <div style={{
      width: 128, padding: '6px 9px 7px',
      background: 'rgba(4,7,16,0.84)',
      border: '1px solid rgba(255,255,255,0.055)',
      borderRadius: 8,
      backdropFilter: 'blur(10px)',
      textAlign: align,
    }}>
      <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.13em',
        color:'rgba(255,255,255,0.26)', textTransform:'uppercase', marginBottom:3 }}>
        {label}
      </div>
      <div style={{ fontSize:11.5, fontFamily:'monospace', fontWeight:700,
        color:col, lineHeight:1.2 }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize:9, fontFamily:'monospace',
          color:'rgba(255,255,255,0.35)', marginTop:2 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function EvidenceRadarPanel({ items, side }: { items: EvidenceItem[]; side: 'left' | 'right' }) {
  const isRight = side === 'right';
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:10, justifyContent:'center',
      flex:1, paddingTop:24, paddingBottom:20,
      alignItems: isRight ? 'flex-start' : 'flex-end' }}>
      {items.map((item, i) => {
        const col   = EV_COLOR[item.strength];
        const glow  = EV_GLOW[item.strength];
        const pulse = item.strength === 'developing';
        const dim   = item.strength === 'inactive';
        return (
          <div key={i} style={{
            display:'flex', alignItems:'center', gap:7,
            flexDirection: isRight ? 'row' : 'row-reverse',
            opacity: dim ? 0.33 : 1,
            transition:'opacity 0.8s ease',
          }}>
            <div style={{
              width:7, height:7, borderRadius:'50%', flexShrink:0,
              background: col, boxShadow: glow,
              animation: pulse ? 'evPulse 2.2s ease-in-out infinite' : undefined,
              transition:'background 0.6s ease, box-shadow 0.6s ease',
            }} />
            <span style={{
              fontSize:10, fontFamily:'monospace', fontWeight:700,
              letterSpacing:'0.06em', textTransform:'uppercase',
              color: dim ? 'rgba(255,255,255,0.22)' : col,
              transition:'color 0.6s ease', lineHeight:1,
              textAlign: isRight ? 'left' : 'right',
            }}>
              {item.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Live Thought Stream ────────────────────────────────────────────────────────
interface ThoughtEntry { id: number; text: string; ts: number; }
const MAX_THOUGHTS = 6;

function useLiveThoughtStream(
  data: any, status: string, edge: number, grade: string,
  sig: Record<string,any>, ad: Record<string,any>, gd: Record<string,any>
): ThoughtEntry[] {
  const [entries, setEntries] = useState<ThoughtEntry[]>([]);
  const idRef     = useRef(0);
  const recentRef = useRef<string[]>([]);
  const prevRef   = useRef({ status: '', edgeBand: -1, struct: false, zone: false, vwapSide: '', cvdDir: '', volReg: '', biasDir: '' });
  const cadRef    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const snapRef   = useRef({ data, status, edge, grade, sig, ad, gd });

  // Keep snapshot fresh so cadence timer always reads current values
  useEffect(() => { snapRef.current = { data, status, edge, grade, sig, ad, gd }; });

  const push = useCallback((text: string) => {
    if (recentRef.current.slice(-MAX_THOUGHTS).includes(text)) return;
    recentRef.current = [...recentRef.current, text].slice(-(MAX_THOUGHTS * 2));
    setEntries(prev => [...prev, { id: idRef.current++, text, ts: Date.now() }].slice(-MAX_THOUGHTS));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Derive change-detection signals
  const price    = Number(data?.price           || 0);
  const vwap     = Number(data?.vwap_value       || 0);
  const demand   = Number(data?.nearest_demand   || 0);
  const supply   = Number(data?.nearest_supply   || 0);
  const structOk = !!(gd.structure_confirmed);
  const zoneOk   = !!(gd.zone_valid);
  const vwapSide = vwap > 0 && price > 0 ? (price > vwap ? 'above' : 'below') : '';
  const cvdRaw   = String(sig.cvd || ad.cvd || '').toLowerCase();
  const cvdDir   = /bull|pos/.test(cvdRaw) ? 'bull' : /bear|neg/.test(cvdRaw) ? 'bear' : '';
  const volRaw   = String(ad.volume || '').toLowerCase();
  const volReg   = /strong|high/.test(volRaw) ? 'high' : /low|thin/.test(volRaw) ? 'low' : 'norm';
  const biasRaw  = String(sig.bias || '').toLowerCase();
  const biasDir  = /bull/.test(biasRaw) ? 'bull' : /bear/.test(biasRaw) ? 'bear' : '';
  const edgeBand = edge >= 85 ? 5 : edge >= 75 ? 4 : edge >= 65 ? 3 : edge >= 50 ? 2 : edge >= 30 ? 1 : 0;
  const changeKey = [status, edgeBand, structOk, zoneOk, vwapSide, cvdDir, volReg, biasDir].join('|');

  // Fire a thought whenever a meaningful market state changes
  useEffect(() => {
    const pr = prevRef.current;

    if (pr.status === '') {
      // Seed with 1-2 observations on first data arrival
      if (data) {
        if (vwap > 0 && price > 0) push(`Price ${vwapSide} VWAP at ${fmt(vwap)}.`);
        else                        push('Monitoring market conditions.');
        if (!structOk) push('Structure confirmation is still missing.');
      }
      prevRef.current = { status, edgeBand, struct: structOk, zone: zoneOk, vwapSide, cvdDir, volReg, biasDir };
      return;
    }

    // Status transition
    if (status !== pr.status) {
      if (status === 'READY') {
        const tp   = (data?.trade_plan || {}) as Record<string,any>;
        const dir  = String(tp.direction || '').toLowerCase();
        const side = /long|bull/.test(dir) ? 'Long' : /short|bear/.test(dir) ? 'Short' : '';
        push(side ? `${side} setup confirmed. All gate conditions satisfied.` : 'All gate conditions satisfied. Edge is confirmed.');
        const entry = Number(tp.entry || 0);
        if (entry > 0) push(`Entry zone near ${fmt(entry)}.`);
      } else if (status === 'MANAGING') {
        push('Position is live. Monitoring for thesis invalidation.');
      } else if (status === 'BUILDING') {
        push(`Edge at ${Math.round(edge)}. Setup is developing — watching closely.`);
      } else if (/READY|MANAGING/.test(pr.status) && /WAIT|WATCH|NO_EDGE/.test(status)) {
        push('Setup has reset. Returning to observation mode.');
      }
    }

    // Edge band crossed
    if (edgeBand !== pr.edgeBand) {
      if (edgeBand > pr.edgeBand && pr.edgeBand >= 0) {
        if      (edgeBand === 1) push('Edge crossed 30. Conditions beginning to align.');
        else if (edgeBand === 2) push(`Edge at ${Math.round(edge)}. More than halfway to the entry threshold.`);
        else if (edgeBand === 3) push(`Edge at ${Math.round(edge)}. Setup is developing — watching closely.`);
        else if (edgeBand === 4) push(`Edge at ${Math.round(edge)}. Approaching confirmation threshold.`);
        else if (edgeBand === 5) push(`Edge at ${Math.round(edge)}. Near maximum confidence.`);
      } else if (edgeBand < pr.edgeBand && pr.edgeBand >= 2) {
        push(`Edge at ${Math.round(edge)}. Setup conditions softening.`);
      }
    }

    // Structure confirmation change
    if (structOk !== pr.struct) {
      if (structOk) {
        const st = String((sig.structure_type || gd.structure_type || '') as string).toUpperCase();
        push(st ? `${st} confirmed. Structural break shifts the outlook.` : 'Structure confirmed. BOS or CHOCH detected.');
      } else {
        push('Structure confirmation cleared. Reset to observation.');
      }
    }

    // Zone change
    if (zoneOk !== pr.zone) {
      if (zoneOk) {
        if (demand > 0)      push(`Demand zone at ${fmt(demand)} is now active.`);
        else if (supply > 0) push(`Supply zone at ${fmt(supply)} is now active.`);
        else                 push('Liquidity zone is now active.');
      } else {
        push('Zone has been mitigated. Watching for re-entry.');
      }
    }

    // VWAP side flip
    if (vwapSide && vwapSide !== pr.vwapSide && pr.vwapSide !== '') {
      push(vwapSide === 'above'
        ? `Price reclaimed VWAP at ${fmt(vwap)}. Bullish structural shift.`
        : `Price dropped below VWAP at ${fmt(vwap)}. Bearish pressure increasing.`);
    }

    // CVD direction flip
    if (cvdDir && cvdDir !== pr.cvdDir && pr.cvdDir !== '') {
      push(cvdDir === 'bull'
        ? 'Delta flipped bullish. Buyers stepping in at current levels.'
        : 'Delta flipped bearish. Sellers taking control of order flow.');
    }

    // Volume regime change
    if (volReg !== pr.volReg && pr.volReg !== '') {
      if      (volReg === 'high') push('Volume expanding. Institutional participation increasing.');
      else if (volReg === 'low')  push('Volume contracting. Thin tape — reducing confidence.');
      else                        push('Volume returning to session average.');
    }

    prevRef.current = { status, edgeBand, struct: structOk, zone: zoneOk, vwapSide, cvdDir, volReg, biasDir };
  }, [changeKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Scheduled cadence: every 90-120 seconds add a monitoring observation
  useEffect(() => {
    const fire = () => {
      const { data: d, status: st, edge: eg, sig: sg, ad: a, gd: g } = snapRef.current;
      if (!d) { cadRef.current = setTimeout(fire, 30000); return; }
      const px  = Number(d.price          || 0);
      const vw  = Number(d.vwap_value     || 0);
      const dem = Number(d.nearest_demand || 0);
      const sup = Number(d.nearest_supply || 0);
      const sOk = !!(g.structure_confirmed);
      const zOk = !!(g.zone_valid);
      const cv  = String(sg.cvd || a.cvd || '').toLowerCase();
      const vl  = String(a.volume || '').toLowerCase();

      const pool: string[] = [];
      if (px > 0 && vw > 0)          pool.push(`Price remains ${px > vw ? 'above' : 'below'} VWAP at ${fmt(vw)}.`);
      if (dem > 0 && !zOk)           pool.push(`Watching ${fmt(dem)} for a demand reaction.`);
      if (sup > 0 && !zOk)           pool.push(`Supply at ${fmt(sup)} remains overhead.`);
      if (!sOk && st !== 'MANAGING') pool.push('Structure confirmation is still missing.');
      if (/bear|neg/.test(cv))       pool.push('No aggressive buying detected.');
      if (/bull|pos/.test(cv))       pool.push('Buyers remain active in the tape.');
      if (/low|thin/.test(vl))       pool.push('Volume remains near session average.');
      if (/strong|high/.test(vl))    pool.push('Volume remains elevated.');
      if (eg >= 30 && eg < 75)       pool.push(`Monitoring conditions. Edge holds at ${Math.round(eg)}.`);
      if (st === 'MANAGING')         pool.push('Thesis remains intact. No invalidation detected.');
      pool.push('No new signals. Continuing to monitor.');
      pool.push('Scanning key levels for institutional footprints.');

      const fresh = pool.filter(t => !recentRef.current.slice(-MAX_THOUGHTS).includes(t));
      const src   = fresh.length > 0 ? fresh : pool;
      push(src[Math.floor(Math.random() * src.length)]);
      cadRef.current = setTimeout(fire, 90000 + Math.floor(Math.random() * 30000));
    };
    cadRef.current = setTimeout(fire, 28000 + Math.floor(Math.random() * 17000));
    return () => { if (cadRef.current) clearTimeout(cadRef.current); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return entries;
}

function ThoughtStream({ stream }: { stream: ThoughtEntry[] }) {
  const now  = Date.now();
  const FADE = [1.0, 0.58, 0.32, 0.16, 0.07, 0.03];
  const FSIZ = [14.5, 14, 13.5, 13, 12.5, 12];

  function relTime(ts: number) {
    const s = Math.floor((now - ts) / 1000);
    if (s < 5)  return 'now';
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m`;
    return `${Math.floor(m / 60)}h`;
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', justifyContent:'flex-end',
      minHeight:148, maxWidth:540, overflow:'hidden', gap:0 }}>
      {stream.map((entry, i) => {
        const age   = stream.length - 1 - i;
        const opRow = FADE[age] ?? 0.02;
        const fsz   = FSIZ[age] ?? 12;
        const isNew = age === 0;
        return (
          <div key={entry.id} style={{
            display:'flex', alignItems:'baseline', gap:9,
            padding:'2px 0',
            animation: isNew ? 'tsIn 0.55s cubic-bezier(0.22,1,0.36,1)' : undefined,
            transition:'opacity 1.8s ease',
            willChange:'opacity',
          }}>
            <span style={{
              fontSize:8, fontFamily:'monospace', fontWeight:700,
              letterSpacing:'0.07em', color:`rgba(255,255,255,${opRow * 0.42})`,
              minWidth:24, flexShrink:0, userSelect:'none',
            }}>{relTime(entry.ts)}</span>
            <span style={{
              fontSize:fsz, lineHeight:1.65,
              color:`rgba(255,255,255,${opRow})`,
              fontFamily:'inherit', letterSpacing:'0.01em',
            }}>
              {entry.text}
              {isNew && <span style={{ opacity:0.30, animation:'bDot 1.1s infinite', marginLeft:3 }}>▌</span>}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Session Memory Engine ──────────────────────────────────────────────────────
const MEM_KEY = 'atp_session_v2';
function getToday() { return new Date().toISOString().slice(0, 10); }

interface DayRec {
  d: string;         // YYYY-MM-DD
  pe: number;        // peak edge score
  es: number;        // edge sum (for avg)
  en: number;        // edge count (for avg)
  su: number;        // READY signals seen
  tr: number;        // MANAGING events seen (trades)
  wr: Record<string, number>; // wait-reason histogram
  tk: string;        // primary ticker
}

function loadMem(): DayRec[] {
  try { const r = localStorage.getItem(MEM_KEY); return r ? (JSON.parse(r) as DayRec[]) : []; }
  catch { return []; }
}

// ── Trade Memory types ─────────────────────────────────────────────────────────
interface TradeStat  { wins:number; losses:number; total:number; wr:number|null; avgRR:number|null; }
interface SetupStat  { name:string; wr:number; total:number; wins:number; losses:number; }
interface TradeMemory {
  today:      TradeStat;
  yesterday:  TradeStat;
  week:       TradeStat;
  bestSetup:  SetupStat | null;
  worstSetup: SetupStat | null;
  dailyBars:  { date:string; wins:number; losses:number; total:number }[];
}

// ── useTradeMemory — derive W/L/R:R/setup stats from data.recent_trades ────────
function useTradeMemory(trades: any[]): TradeMemory {
  return useMemo(() => {
    const today    = getToday();
    const yestDate = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    const weekAgo  = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
    const isWin  = (t: any) => /win|target|profit/i.test(String(t?.outcome ?? t?.result ?? ''));
    const isLoss = (t: any) => /loss|stop|miss/i.test(String(t?.outcome ?? t?.result ?? ''));
    const isDone = (t: any) => isWin(t) || isLoss(t);
    const mkStat = (ts: any[]): TradeStat => {
      const done   = ts.filter(isDone);
      const wins   = done.filter(isWin).length;
      const losses = done.filter(isLoss).length;
      const rrs    = done.map(t => Number(t?.rr_actual ?? t?.rr ?? 0)).filter(r => r > 0);
      return {
        wins, losses, total: done.length,
        wr:    done.length > 0 ? Math.round(wins / done.length * 100) : null,
        avgRR: rrs.length  > 0 ? +(rrs.reduce((s, r) => s + r, 0) / rrs.length).toFixed(2) : null,
      };
    };
    const todayT  = trades.filter(t => String(t?.opened_at ?? '').slice(0, 10) === today);
    const yesterT = trades.filter(t => String(t?.opened_at ?? '').slice(0, 10) === yestDate);
    const weekT   = trades.filter(t => String(t?.opened_at ?? '').slice(0, 10) >= weekAgo);
    const strat: Record<string, { w:number; l:number }> = {};
    weekT.forEach(t => {
      const s = String(t?.strategy ?? t?.active_strategy ?? '').replace(/_/g, ' ').trim().toLowerCase();
      if (s.length < 2) return;
      if (!strat[s]) strat[s] = { w: 0, l: 0 };
      if (isWin(t)) strat[s].w++; else if (isLoss(t)) strat[s].l++;
    });
    const byWR: SetupStat[] = Object.entries(strat)
      .filter(([, v]) => v.w + v.l >= 1)
      .map(([name, { w, l }]) => ({ name, wr: w / ((w + l) || 1), total: w + l, wins: w, losses: l }))
      .sort((a, b) => b.wr - a.wr);
    const dailyBars = Array.from({ length: 7 }, (_, i) => {
      const d  = new Date(Date.now() - (6 - i) * 86400000).toISOString().slice(0, 10);
      const dt = trades.filter(t => String(t?.opened_at ?? '').slice(0, 10) === d && isDone(t));
      return { date: d, wins: dt.filter(isWin).length, losses: dt.filter(isLoss).length, total: dt.length };
    });
    return {
      today: mkStat(todayT), yesterday: mkStat(yesterT), week: mkStat(weekT),
      bestSetup:  byWR.find(s => s.total >= 1) ?? null,
      worstSetup: [...byWR].reverse().find(s => s.total >= 1 && s.wr < 0.5) ?? null,
      dailyBars,
    };
  }, [trades]); // eslint-disable-line react-hooks/exhaustive-deps
}

// ── computeObjectives — derive specific daily goals from performance history ────
function computeObjectives(tm: TradeMemory, mcWR: string | null): string[] {
  const out: string[] = [];
  if (tm.week.total === 0) {
    out.push('No recent trade history \u2014 read the market today, let edge reach \u226575 before entering.');
    if (mcWR) out.push('Recurring block: "' + mcWR.slice(0, 55) + '"');
    return out;
  }
  if (tm.week.wr !== null) {
    if (tm.week.wr < 40)      out.push('Win rate ' + tm.week.wr + '% this week \u2014 only take A+ setups, edge \u226585 required.');
    else if (tm.week.wr < 55) out.push('Win rate ' + tm.week.wr + '% \u2014 wait for full gate confirmation before every entry.');
    else                       out.push('Win rate ' + tm.week.wr + '% \u2014 solid week. Maintain discipline, avoid marginal setups.');
  }
  if (tm.week.avgRR !== null && tm.week.avgRR < 0.9)
    out.push('Avg exit ' + tm.week.avgRR.toFixed(1) + 'R \u2014 give winners room, target T1 before scaling out.');
  if (tm.bestSetup && tm.bestSetup.wr >= 0.6 && tm.bestSetup.total >= 2)
    out.push(tm.bestSetup.name.toUpperCase() + ' strongest this week (' + Math.round(tm.bestSetup.wr * 100) + '%) \u2014 prioritize it.');
  if (tm.worstSetup && tm.worstSetup.losses >= 2)
    out.push(tm.worstSetup.name.toUpperCase() + ' underperforming (' + Math.round(tm.worstSetup.wr * 100) + '%) \u2014 consider avoiding today.');
  if (mcWR) {
    const g = mcWR.toLowerCase();
    if (/structure|bos|choch/.test(g)) out.push('Wait for confirmed BOS/CHOCH \u2014 structure gate is blocking repeatedly.');
    else if (/zone/.test(g))           out.push('Enter only from an active demand or supply zone \u2014 zone gate keeps blocking.');
    else if (/vwap/.test(g))           out.push('Align with VWAP before any entry \u2014 VWAP gate keeps blocking.');
    else                               out.push('Focus: "' + mcWR.slice(0, 55) + '" \u2014 top recurring block this week.');
  }
  return out.slice(0, 4);
}

function generateBriefing(yest: DayRec | null, wkPeak: number, active: number, mcWR: string | null, tm: TradeMemory | null): string {
  if (!tm) return 'Loading session memory...';
  if (!yest && active === 0 && tm.week.total === 0) return 'First session. Building my baseline \u2014 scanning for high-probability setups today.';
  const parts: string[] = [];
  if (tm.yesterday.total > 0) {
    const { wins, losses, wr, avgRR } = tm.yesterday;
    parts.push('Yesterday: ' + wins + 'W ' + losses + 'L (' + wr + '% WR' + (avgRR ? ', ' + avgRR.toFixed(1) + 'R avg' : '') + ').');
  } else if (yest) {
    const dn = new Date(yest.d + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long' });
    parts.push(dn + ': edge peaked ' + Math.round(yest.pe) + '/110, ' + yest.su + ' setup' + (yest.su !== 1 ? 's' : '') + ' identified' + (yest.tr > 0 ? ', ' + yest.tr + ' executed' : '') + '.');
  }
  if (tm.week.total > 0) {
    parts.push('Week: ' + tm.week.wins + 'W ' + tm.week.losses + 'L (' + tm.week.wr + '% WR' + (tm.week.avgRR ? ', ' + tm.week.avgRR.toFixed(1) + 'R avg' : '') + ').');
  } else if (wkPeak > 0) {
    parts.push('Week peak: ' + Math.round(wkPeak) + '/110 across ' + active + ' session' + (active !== 1 ? 's' : '') + '.');
  }
  if (tm.bestSetup && tm.bestSetup.wr >= 0.55 && tm.bestSetup.total >= 2)
    parts.push('Best setup: ' + tm.bestSetup.name.toUpperCase() + ' (' + Math.round(tm.bestSetup.wr * 100) + '%).');
  if (mcWR) parts.push('Top gap: "' + mcWR.slice(0, 48) + '" \u2014 addressing today.');
  return parts.join(' ') || 'Analyzing previous sessions...';
}

function useSessionMemory(status: string, edge: number, ticker: string, strictR: string) {
  const td = getToday();

  // Load historical records once on mount (lazy useState init)
  const [initHist] = useState<DayRec[]>(() => loadMem().filter(r => r.d !== td));
  const histRef   = useRef<DayRec[]>(initHist);
  const recRef    = useRef<DayRec>({ d: td, pe: 0, es: 0, en: 0, su: 0, tr: 0, wr: {}, tk: ticker });
  const prevStRef = useRef('');

  // Update live record on each data tick
  useEffect(() => {
    const r = recRef.current;
    r.en++; r.es += edge; r.tk = ticker;
    if (edge > r.pe) r.pe = edge;
    const ps = prevStRef.current;
    if (status === 'READY'    && ps !== 'READY')    r.su++;
    if (status === 'MANAGING' && ps !== 'MANAGING') r.tr++;
    prevStRef.current = status;
    if (status === 'WAIT' && strictR) { const k = strictR.slice(0, 50); r.wr[k] = (r.wr[k] || 0) + 1; }
  }, [status, edge, ticker, strictR]);

  // Persist to localStorage every 20s + on page unload
  useEffect(() => {
    const flush = () => {
      const all = [...histRef.current, { ...recRef.current }];
      try { localStorage.setItem(MEM_KEY, JSON.stringify(all.slice(-31))); } catch {}
    };
    const t = setInterval(flush, 20000);
    window.addEventListener('beforeunload', flush);
    return () => { clearInterval(t); window.removeEventListener('beforeunload', flush); flush(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Derived — recomputed on every render (refs always current)
  const all    = [...histRef.current, recRef.current].sort((a, b) => a.d.localeCompare(b.d));
  const hist   = all.filter(r => r.d !== td);
  const yest   = hist.length > 0 ? hist[hist.length - 1] : null;
  const last7  = all.slice(-7);
  const wkPeak = last7.reduce((s, r) => Math.max(s, r.pe), 0);
  const active = last7.filter(r => r.en >= 3).length;
  const mcWR   = (() => {
    const agg: Record<string, number> = {};
    last7.forEach(r => Object.entries(r.wr).forEach(([k, v]) => { agg[k] = (agg[k] || 0) + v; }));
    return Object.entries(agg).sort((a, b) => b[1] - a[1])[0]?.[0] || null;
  })();

  return { live: recRef.current, yest, last7, wkPeak, active, mcWR };
}

// ── Satellite intelligence panel ───────────────────────────────────────────────
function SatPanel({ label, children, style }: { label: string; children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ padding:'8px 10px', borderRadius:7, background:'rgba(255,255,255,0.022)', border:'1px solid rgba(255,255,255,0.055)', ...style }}>
      <div style={{ fontSize:8, fontFamily:'monospace', color:'rgba(255,255,255,0.24)', letterSpacing:'0.14em', textTransform:'uppercase', marginBottom:6 }}>
        {label}
      </div>
      {children}
    </div>
  );
}

// ── Evidence drawer content ────────────────────────────────────────────────────
function EvidenceDrawer({ data, status }: { data: any; status: string }) {
  const sClr = statusClr(status);
  const eb   = data?.edge_breakdown || data?.main_brain?.edge_breakdown || {};
  const tp   = data?.trade_plan || {};
  const price  = Number(data?.price || 0);
  const vwap   = data?.vwap_value;
  const demand = data?.nearest_demand;
  const supply = data?.nearest_supply;
  const at     = data?.active_trade || data?.managing_trade;
  const sig    = (data?.main_brain || {}).signals || {};
  const ad     = data?.alert_diagnostics || {};

  const comps: [string, number | null, number][] = [
    ['Structure / BOS', eb.bos20   ?? eb.choch20  ?? null, 20],
    ['VWAP Alignment',  eb.vwap15  ?? null,                15],
    ['Sweep / Liquidity', eb.sweep15 ?? null,              15],
    ['Volume / Delta',  eb.volume15 ?? null,               15],
    ['Session',         eb.session10 ?? null,              10],
  ];

  const rowStyle: React.CSSProperties = { display:'flex', justifyContent:'space-between', alignItems:'center', padding:'5px 0', borderBottom:'1px solid rgba(255,255,255,0.028)' };
  const lbl: React.CSSProperties = { fontSize:11, color:'rgba(255,255,255,0.36)', fontFamily:'monospace' };
  const val: React.CSSProperties = { fontSize:11.5, fontFamily:'monospace', fontWeight:600 };

  return (
    <div className="ev-grid" style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:16 }}>
      {/* Key Levels */}
      <div>
        <div style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.28)', letterSpacing:'0.10em', textTransform:'uppercase', marginBottom:8 }}>Key Levels</div>
        {[['VWAP', vwap != null ? fmt(vwap) : '—', '#60a5fa'], ['Supply', supply != null ? fmt(supply) : '—', BEAR], ['Price', price > 0 ? fmt(price) : '—', 'rgba(255,255,255,0.88)'], ['Demand', demand != null ? fmt(demand) : '—', BULL], ['Entry', tp.entry != null ? fmt(tp.entry) : '—', AMB]].map(([l,v,c]) => (
          <div key={l} style={rowStyle}><span style={lbl}>{l}</span><span style={{ ...val, color: c as string }}>{v}</span></div>
        ))}
      </div>
      {/* Edge components */}
      <div>
        <div style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.28)', letterSpacing:'0.10em', textTransform:'uppercase', marginBottom:8 }}>Edge Components</div>
        {comps.map(([name, score, maxScore]) => {
          const n = score != null ? Math.round(score) : null;
          const good = n != null && n >= maxScore * 0.6;
          const c = n == null ? MUTED : good ? BULL : n > 0 ? AMB : BEAR;
          return (
            <div key={name} style={rowStyle}>
              <span style={lbl}>{name}</span>
              <span style={{ ...val, color: c }}>{n != null ? `${n} / ${maxScore}` : `— / ${maxScore}`}</span>
            </div>
          );
        })}
      </div>
      {/* Position / Setup */}
      <div>
        <div style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.28)', letterSpacing:'0.10em', textTransform:'uppercase', marginBottom:8 }}>Setup & Position</div>
        {at ? (
          <>
            {[['Direction', String(at.direction || '—').toUpperCase(), dirClr(at.direction)], ['Contracts', String(at.contracts ?? '—'), 'rgba(255,255,255,0.72)'], ['Entry', at.entry_price != null ? fmt(at.entry_price) : '—', AMB], ['Stop', tp.stop != null ? fmt(tp.stop) : '—', BEAR], ['Target', tp.target1 != null ? fmt(tp.target1) : '—', BULL]].map(([l,v,c]) => (
              <div key={l} style={rowStyle}><span style={lbl}>{l}</span><span style={{ ...val, color: c as string }}>{v}</span></div>
            ))}
          </>
        ) : (
          <>
            {[['Bias', sig.bias ? String(sig.bias).toUpperCase() : '—', /bull/i.test(sig.bias||'') ? BULL : /bear/i.test(sig.bias||'') ? BEAR : MUTED], ['Volume', ad.volume ? String(ad.volume).toUpperCase() : '—', /strong|high/i.test(ad.volume||'') ? BULL : MUTED], ['R:R', tp.rr_display ?? '—', AMB], ['Contracts', tp.contracts != null ? String(tp.contracts) : '—', 'rgba(255,255,255,0.72)'], ['Target', tp.target1 != null ? fmt(tp.target1) : '—', BULL]].map(([l,v,c]) => (
              <div key={l} style={rowStyle}><span style={lbl}>{l}</span><span style={{ ...val, color: c as string }}>{v}</span></div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

// ── Internal monologue ─────────────────────────────────────────────────────────

function buildThoughts(data: any, status: string, edge: number, grade: string): string[] {
  if (!data) return ['Initializing market scan...', 'Connecting to live data feed...'];

  const price   = Number(data.price           || 0);
  const vwap    = Number(data.vwap_value      || 0);
  const demand  = Number(data.nearest_demand  || 0);
  const supply  = Number(data.nearest_supply  || 0);
  const gd      = (data.gate_debug            || {}) as Record<string,any>;
  const ad      = (data.alert_diagnostics     || {}) as Record<string,any>;
  const sig     = ((data.main_brain || {}).signals || {}) as Record<string,any>;
  const bias    = String(sig.bias  || '').toLowerCase();
  const cvd     = String(sig.cvd   || ad.cvd  || '').toLowerCase();
  const vol     = String(ad.volume || '').toLowerCase();
  const at      = data.active_trade || data.managing_trade;
  const tp      = (data.trade_plan  || {}) as Record<string,any>;
  const strictR = String(data.strict_reason || (data.main_brain || {}).wait_reason || '').trim();
  const structOk = !!(gd.structure_confirmed);
  const zoneOk   = !!(gd.zone_valid);
  const thoughts: string[] = [];

  // ── MANAGING ──────────────────────────────────────────────────────────────
  if (status === 'MANAGING' || at) {
    const dir = String((at && at.direction) || tp.direction || '').toUpperCase();
    thoughts.push(dir ? `${dir} position is live. Monitoring actively.` : 'Position is live. Watching every tick.');
    const ep = at && Number(at.entry_price || 0);
    if (ep && ep > 0) thoughts.push(`Entry at ${fmt(ep)}. Thesis remains intact.`);
    const t1 = Number(tp.target1 || 0);
    if (t1 > 0) thoughts.push(`Target at ${fmt(t1)}. Watching for price reaction.`);
    const st = Number(tp.stop || 0);
    if (st > 0) thoughts.push(`Stop at ${fmt(st)}. Risk is defined and committed.`);
    if (price > 0 && vwap > 0) thoughts.push(price > vwap
      ? 'Price holding above VWAP. Bullish structural context intact.'
      : 'Price below VWAP. Monitoring for breakdown signals.');
    thoughts.push('Scanning for thesis invalidation signals.');
    thoughts.push('Delta and structure holding expected behavior.');
    thoughts.push('Waiting for target or invalidation — patience is the edge.');
    return thoughts;
  }

  // ── READY ─────────────────────────────────────────────────────────────────
  if (status === 'READY') {
    const dir = String(tp.direction || '').toLowerCase();
    const dw  = /long|bull/i.test(dir) ? 'Long' : /short|bear/i.test(dir) ? 'Short' : '';
    thoughts.push(dw ? `${dw} edge confirmed. All gate conditions satisfied.` : 'Edge confirmed. Execution window is open.');
    thoughts.push(`Score ${Math.round(edge)} — grade ${grade}. Highest-probability setup right now.`);
    const entry = Number(tp.entry || 0);
    if (entry > 0) thoughts.push(`Entry zone near ${fmt(entry)}. Price is approaching.`);
    const t1 = Number(tp.target1 || 0);
    if (t1 > 0) thoughts.push(`Targeting ${fmt(t1)} — ${tp.rr_display || '1:3'} risk-to-reward.`);
    const stopP = Number(tp.stop || 0);
    if (stopP > 0) thoughts.push(`Stop at ${fmt(stopP)}. Maximum risk is capped.`);
    if (price > 0 && vwap > 0) thoughts.push(price > vwap
      ? `VWAP at ${fmt(vwap)}. Price above — momentum confirmed.`
      : `VWAP at ${fmt(vwap)}. Price below — short flow verified.`);
    thoughts.push('All conditions checked. This is exactly the setup I wait for.');
    return thoughts;
  }

  // ── BUILDING ──────────────────────────────────────────────────────────────
  if (status === 'BUILDING') {
    thoughts.push('Setup is forming. Edge is building toward the threshold.');
    thoughts.push(`Score ${Math.round(edge)}. Getting closer — not acting yet.`);
    if (!structOk) thoughts.push('Waiting for structural confirmation — BOS or CHOCH required.');
    if (price > 0 && vwap > 0) thoughts.push(`Price ${price > vwap ? 'above' : 'below'} VWAP at ${fmt(vwap)}.`);
    if (demand > 0) thoughts.push(`Demand zone near ${fmt(demand)}. Looking for a reaction here.`);
    if (supply > 0) thoughts.push(`Supply overhead at ${fmt(supply)}. Watching for rejection.`);
    thoughts.push('Patience. Waiting for the final confirmation signal.');
    return thoughts;
  }

  // ── WAIT / NO_EDGE ────────────────────────────────────────────────────────

  // VWAP context
  if (price > 0 && vwap > 0) {
    const above = price > vwap;
    thoughts.push(`Price ${above ? 'above' : 'below'} VWAP at ${fmt(vwap)}. ${above ? 'Bullish' : 'Bearish'} structural context.`);
    if (!above) thoughts.push(`VWAP resistance at ${fmt(vwap)}. Bearish until price reclaims it.`);
  } else {
    thoughts.push('Watching overnight liquidity for signs of absorption.');
  }

  // Zones
  if (demand > 0 && price > 0) {
    const d = ((price - demand) / demand * 100).toFixed(1);
    thoughts.push(`Demand zone at ${fmt(demand)}. Price is ${d}% above it.`);
    thoughts.push(`Watching for a liquidity sweep into ${fmt(demand)}.`);
  } else {
    thoughts.push('No confirmed demand zone present.');
  }
  if (supply > 0 && price > 0) {
    const d = ((supply - price) / price * 100).toFixed(1);
    thoughts.push(`Supply zone overhead at ${fmt(supply)} — ${d}% away.`);
  }

  // Structure
  if (!structOk) {
    thoughts.push('Structure has not been confirmed. No BOS or CHOCH detected.');
    thoughts.push('Waiting for a structural break before considering entry.');
  } else {
    thoughts.push('Structure is confirmed. Waiting for zone alignment.');
  }

  // Zone gate
  if (!zoneOk && !demand && !supply) thoughts.push('No confirmed demand or supply zone in play.');

  // CVD / delta
  if      (/bull|pos/.test(cvd)) thoughts.push('Delta is bullish. Buyers are active in the tape.');
  else if (/bear|neg/.test(cvd)) thoughts.push('Delta is bearish. Sellers controlling order flow.');
  else                           thoughts.push('Delta is neutral. No directional conviction in current flow.');

  // Volume
  if      (/strong|high|incr/.test(vol)) thoughts.push('Volume is above average. Participation is healthy.');
  else if (/low|thin|decr/.test(vol))   thoughts.push('Volume is below average. Thin tape — waiting for expansion.');
  else                                   thoughts.push('Volume is unremarkable. No catalyst has emerged yet.');

  // Bias
  if      (/bull/.test(bias)) thoughts.push('Directional bias is bullish. Scanning for long setups only.');
  else if (/bear/.test(bias)) thoughts.push('Directional bias is bearish. Short pressure remains dominant.');
  else                        thoughts.push('Bias is neutral. No clear directional conviction yet.');

  // Edge score qualitative
  if (edge < 15) {
    thoughts.push('Edge score is very low. Conditions are unfavorable right now.');
    thoughts.push('Capital preservation mode. Standing completely aside.');
  } else if (edge < 35) {
    thoughts.push(`Edge at ${Math.round(edge)}. Still well below the entry threshold.`);
    thoughts.push('Probability is insufficient. Discipline means waiting.');
  } else if (edge < 50) {
    thoughts.push(`Edge at ${Math.round(edge)}. Getting closer, but not ready.`);
    thoughts.push('One more confirmation needed before considering a trade.');
  } else {
    thoughts.push(`Edge at ${Math.round(edge)} — approaching threshold.`);
    thoughts.push('Almost there. Waiting for the final piece to click into place.');
  }

  // Strict reason from backend (cleaned up)
  if (strictR.length > 8 && strictR.length < 140 && !strictR.includes('undefined') && !strictR.includes('null')) {
    const sr = strictR.charAt(0).toUpperCase() + strictR.slice(1);
    thoughts.push(sr.endsWith('.') ? sr : sr + '.');
  }

  // Atmospheric / always-present
  thoughts.push('Scanning key levels for institutional footprints.');
  thoughts.push('Patience. The highest-probability setup has not arrived yet.');
  thoughts.push('Waiting for a liquidity sweep before considering entry.');
  thoughts.push('Every minute of waiting protects capital for the right moment.');

  return thoughts.filter(t => t.length > 4);
}

function useMonologue(thoughts: string[], restartKey: string): { text: string; live: boolean } {
  const [text, setText] = useState('');
  const [live, setLive] = useState(false);
  const thoughtsRef = useRef<string[]>(thoughts);
  const ctrl = useRef<{ idx: number; charIdx: number; tid: ReturnType<typeof setTimeout> | null }>({ idx: 0, charIdx: 0, tid: null });

  // Update thoughts content silently — no restart on every 3s poll
  useEffect(() => { thoughtsRef.current = thoughts; }, [thoughts]);

  // Restart the typewriter loop only when the market state changes
  useEffect(() => {
    const c = ctrl.current;
    if (c.tid) clearTimeout(c.tid);
    c.idx = 0; c.charIdx = 0;
    setText(''); setLive(false);

    const tick = () => {
      const ts = thoughtsRef.current;
      if (!ts.length) { c.tid = setTimeout(tick, 500); return; }
      const cur = ts[c.idx % ts.length] || '';
      if (c.charIdx < cur.length) {
        c.charIdx++;
        setText(cur.slice(0, c.charIdx));
        setLive(true);
        c.tid = setTimeout(tick, 18);
      } else {
        setLive(false);
        // Pause between thoughts: 2.8–4.0s
        const pause = 2800 + Math.random() * 1200;
        c.tid = setTimeout(() => {
          c.idx = (c.idx + 1) % ts.length;
          c.charIdx = 0;
          setText('');
          c.tid = setTimeout(tick, 80);
        }, pause);
      }
    };

    // Brief initial delay before first thought appears
    c.tid = setTimeout(tick, 700);
    return () => { if (c.tid) clearTimeout(c.tid); };
  }, [restartKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return { text, live };
}

// ── Session Memory ──────────────────────────────────────────────────────────
type MemTag = 'pref' | 'setup' | 'trade' | 'chat' | 'insight';
interface MemEntry { t: number; tag: MemTag; text: string; }
function _memKey() { return 'brain_mem_' + new Date().toISOString().slice(0, 10); }

const PREF_PATTERNS: Array<[RegExp, string]> = [
  [/\baggressive\b/i,             'User wants aggressive entries today'],
  [/\bconservative|cautious\b/i,  'User wants to trade conservatively today'],
  [/\bselective\b/i,              'User is being selective with setups today'],
  [/\bpatient\b/i,                'User wants to be patient and wait'],
  [/\bpullback\b/i,               'User wants a cleaner pullback before entering'],
  [/\bskip this|pass on\b/i,      'User considered skipping this setup'],
  [/\bnot trading|no trade\b/i,   'User decided against trading this setup'],
  [/\btight stop\b/i,             'User focused on tight stop placement'],
  [/\bscalp\b/i,                  'User interested in scalping opportunities'],
  [/\bswing\b/i,                  'User mentioned swing trade perspective'],
];

function useConvMemory() {
  const [entries, setEntries] = useState<MemEntry[]>(() => {
    try { const r = localStorage.getItem(_memKey()); return r ? (JSON.parse(r) as MemEntry[]) : []; }
    catch { return []; }
  });

  const addEntry = useCallback((tag: MemTag, text: string) => {
    const entry: MemEntry = { t: Date.now(), tag, text: text.slice(0, 200) };
    setEntries(prev => {
      const next = [...prev, entry].slice(-60);
      try { localStorage.setItem(_memKey(), JSON.stringify(next)); } catch {}
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    setEntries([]);
    try { localStorage.removeItem(_memKey()); } catch {}
  }, []);

  const context = useMemo((): string => {
    const PERSONA = [
      '[ANALYST VOICE — apply strictly]',
      'You are a senior institutional futures trader narrating the tape live.',
      'Rules: direct and concise (2-3 sentences unless complexity demands more); present tense, active voice.',
      'Use professional market language — examples: "Structure weak." / "Buyers defending VWAP." /',
      '"Momentum fading." / "Confirmation still missing." / "Risk outweighs reward." /',
      '"No edge yet." / "Liquidity sweep complete." / "High-probability setup developing."',
      'For every answer explain (1) what you see, (2) why it matters, (3) what would change your read.',
      'Never use filler, hedging disclaimers, or generic chatbot language.',
      '---',
    ].join('\n');
    const TAG: Record<MemTag, string> = { pref:'NOTE', setup:'SETUP', trade:'TRADE', chat:'YOU', insight:'BRAIN' };
    if (entries.length === 0) return PERSONA + '\n';
    const lines = entries.slice(-20).map(e => {
      const hh = new Date(e.t).toLocaleTimeString('en-US', { hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'America/New_York' });
      return hh + ' [' + TAG[e.tag] + '] ' + e.text;
    });
    return PERSONA + '\n[TODAY\'S SESSION — weave in naturally if relevant]\n' + lines.join('\n') + '\n---\n';
  }, [entries]);

  return { entries, addEntry, clear, context };
}

// ── Memory Panel ─────────────────────────────────────────────────────────────
function MemoryPanel({ entries, onClear }: { entries: MemEntry[]; onClear: () => void }) {
  const TAG_COLOR: Record<MemTag, string> = { pref:AMB, setup:BLUE, trade:BULL, chat:'rgba(255,255,255,0.50)', insight:CYAN };
  const TAG_LABEL: Record<MemTag, string> = { pref:'NOTE', setup:'SETUP', trade:'TRADE', chat:'YOU', insight:'BRAIN' };
  if (entries.length === 0) return (
    <div style={{ fontSize:10.5, color:MUTED, fontFamily:'monospace', textAlign:'center', padding:'8px 0' }}>
      No events yet — I will remember what happens this session.
    </div>
  );
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
      {[...entries].reverse().slice(0, 18).map((e, i) => {
        const hh = new Date(e.t).toLocaleTimeString('en-US', { hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'America/New_York' });
        return (
          <div key={i} style={{ display:'flex', gap:7, alignItems:'flex-start', opacity: i > 9 ? 0.5 : 1, transition:'opacity 0.3s' }}>
            <span style={{ fontSize:8.5, fontFamily:'monospace', color:'rgba(255,255,255,0.22)', flexShrink:0, paddingTop:2, letterSpacing:'0.04em' }}>{hh}</span>
            <span style={{ fontSize:8.5, fontFamily:'monospace', fontWeight:700, color:TAG_COLOR[e.tag], flexShrink:0, paddingTop:2, letterSpacing:'0.08em' }}>{TAG_LABEL[e.tag]}</span>
            <span style={{ fontSize:10.5, color:'rgba(255,255,255,0.60)', fontFamily:'monospace', lineHeight:1.44 }}>{e.text}</span>
          </div>
        );
      })}
      <button onClick={onClear} style={{ background:'none', border:'none', cursor:'pointer',
        color:'rgba(255,255,255,0.16)', fontSize:9, fontFamily:'monospace',
        textAlign:'right', padding:'5px 0 0', letterSpacing:'0.06em', textTransform:'uppercase' }}>
        Clear session memory
      </button>
    </div>
  );
}

// ── Voice line bank ────────────────────────────────────────────────────────────
// Large pool of spoken lines per avatar state. Cycled in order so consecutive
// lines never repeat. The periodic-narration timer picks from this bank.
const VOICE_BANK: Record<string, string[]> = {
  WAIT: [
    'No edge present. Standing aside. Capital preservation comes first.',
    'Market not offering a clean entry right now. Staying patient.',
    'Conditions not meeting my criteria. I will wait for the right setup.',
    'Structure is unclear. No defined risk. Not touching it.',
    'Too many conflicting signals. Best trade right now is no trade.',
    'Nothing to do but wait. The edge will show itself.',
    'Markets are ranging. Choppy conditions — I sit on my hands.',
    'Volume is thin and conviction is low. No reason to be involved.',
    'Not forcing anything today. The cleanest setups come from patience.',
    'Neutral until one side proves itself. No directional edge right now.',
    'I do not chase. I wait. The setup has to come to me.',
    'Tape looks indecisive. Institutions are not showing their hand yet.',
    'No break of structure confirmed yet. Watching for the market to tip its hand.',
    'Price is oscillating around vee-wap. No directional commitment from either side.',
    'Neither buyers nor sellers are committing. I will not trade a choppy tape.',
    'The demand zone is nearby but I am not seeing absorption yet.',
    'Bias is neutral. Without a directional lean, there is nothing worth trading.',
    'Edge score is below my threshold right now. Quality over quantity — always.',
    'Watching the recent swing level for any sign of institutional reaction.',
    'I need a break of structure before I start building a thesis here.',
    'Volume is confirming nothing. Sitting on my hands until one side steps up.',
    'Looking for smart money to show their position before I commit.',
  ],
  ANALYZING: [
    'Some signals are aligning. Watching carefully for confirmation.',
    'Score is building. Not there yet — but worth watching closely.',
    'Early signs of a potential setup. Monitoring for the next signal.',
    'Edge beginning to develop. Still missing a couple of key conditions.',
    'Conditions improving. Delta and structure starting to agree.',
    'Something may be setting up here. Staying alert.',
    'Order flow is showing some interest. Needs more development.',
    'Market is showing early tells. Need a few more bars to confirm.',
    'Starting to like what I see. Not committing yet though.',
    'Score is creeping up. Keeping this one on my radar.',
    'Watching for a clean break above the recent swing high. That would confirm structure.',
    'Price compressing near vee-wap. A move is coming — watching for the direction.',
    'Delta is starting to show buying interest but volume has not confirmed yet.',
    'The demand zone is holding but I need to see buyers defend it with conviction.',
    'Edge is climbing but I am still below my entry threshold. Staying patient.',
    'Seeing a change of character developing. Monitoring for structure to confirm.',
    'Cumulative delta is improving. If structure breaks, this becomes a real setup.',
    'Price is respecting the key level. Watching how it handles the next test.',
    'Order flow is mixed right now. I want to see cleaner conviction before acting.',
    'Regime is improving. Score could cross the threshold on the next signal.',
  ],
  FORMING: [
    'Setup is developing. Edge building toward the confirmation threshold.',
    'Structure confirmed. Waiting on zone and flow alignment now.',
    'Getting close. Just a couple of conditions left to trigger.',
    'Score approaching the entry zone. Staying focused.',
    'Thesis is forming nicely. Waiting on the final confirmation signal.',
    'Good structure, improving delta. Zone is the last piece of the puzzle.',
    'Setup building well. Patience — almost there.',
    'Order flow cooperating. Zone activation would seal this setup.',
    'I can feel this one developing. Waiting for the green light.',
    'All the pieces are moving into place. Just need the final signal.',
    'Break of structure confirmed. Watching for the pullback into the demand zone.',
    'Change of character detected. The sellers are losing control at this level.',
    'Structure has shifted. Waiting for the zone to be tagged and hold.',
    'Cumulative delta is showing strong buying pressure. Score approaching my threshold.',
    'Vee-wap has been reclaimed. Bulls are now in control of the intraday move.',
    'Higher timeframe bias is aligned with short-term structure. Confidence is building.',
    'Liquidity was swept at the recent low. Watching for a strong reaction and hold.',
    'The setup is forming well. Missing one more gate — watching for it now.',
    'I see structure, I see the zone, I see improving delta. Nearly there.',
    'Score just crossed into the building zone. High alert — watching every bar now.',
  ],
  READY_LONG: [
    'Long setup confirmed. All gates green. Execution window is open.',
    'Highest probability long of the session. All conditions are satisfied.',
    'Bullish edge confirmed. Structure, zone, and flow are all aligned. This is the one.',
    'Long entry criteria fully met. Risk to reward is favorable.',
    'All gate conditions satisfied on the long side. This is what I wait for.',
    'Demand zone holding with bullish delta and clean structure. Long bias locked in.',
    'Price above vee-wap with structure and zone confirmed. Long setup is live.',
    'Full confidence on the long side right now. Waiting for execution.',
    'This is a textbook long setup. Clean entry, defined risk, strong edge.',
    'Long edge at peak confidence. Every signal is in agreement.',
    'Demand zone absorbed the sellers. Buyers are stepping in with real conviction.',
    'Structure confirmed above vee-wap with strong delta. This is exactly what I look for.',
    'All gate conditions satisfied. Smart money has absorbed the liquidity. Long is live.',
    'The sweep into demand held. Buyers defended aggressively. Long setup is clean.',
    'Everything is aligned on the long side. Structure, zone, flow, and vee-wap. Waiting.',
  ],
  READY_SHORT: [
    'Short setup confirmed. All gates green. Execution window is open.',
    'Bearish edge confirmed. Sellers in control with structure and zone aligned.',
    'Short entry criteria fully met. Risk is defined. Confidence is high.',
    'Supply zone holding with bearish delta and confirmed structure. Short bias locked in.',
    'All conditions satisfied on the short side. This is the setup I was waiting for.',
    'Price below vee-wap, structure confirmed bearish, zone active. Short setup is live.',
    'Short setup at peak confidence. All signals are in full agreement.',
    'Highest probability short of the session. Execution window is open.',
    'Textbook short. Clean supply zone, bearish delta, structure confirmed.',
    'Full conviction on the short side. Waiting for execution.',
    'Supply zone rejected price hard. Sellers are defending aggressively. Short is on.',
    'Structure broke bearish with negative delta and volume. Short setup is confirmed.',
    'Everything is aligned on the short side. All gates green. Waiting for execution.',
    'The sweep into supply held perfectly. Sellers stepped in with conviction. Short is live.',
  ],
  ACTIVE: [
    'Position is live. Monitoring every tick for thesis confirmation or invalidation.',
    'Trade running. Stop is placed. Letting the edge play out.',
    'In the trade now. Managing risk. Thesis remains intact so far.',
    'Position active. No reason to exit early — the setup was clean.',
    'Live trade. Price behaving as expected. Staying disciplined.',
    'Watching for the first target level. Stop is set. No second-guessing.',
    'Trade is on. The setup was textbook — trusting the process here.',
    'Managing the position. Will adjust stop to break even if momentum holds.',
    'Thesis intact. Price moving in our direction. Letting it run.',
    'Open position. Monitoring structure and delta for any invalidation.',
    'Vee-wap is holding as support below entry. That is a good sign for the thesis.',
    'Trade progressing as planned. Watching for structure to hold on any pullback.',
    'Delta continues to confirm the move. No signs of reversal yet.',
    'Tracking price toward the first target. Position is behaving exactly as modeled.',
    'In the trade and focused. Thesis is intact — do not overthink this.',
    'Stop is protected. Let the market do its job. I will manage if needed.',
  ],
  TARGET_HIT: [
    'Target hit. Trade profitable. Exactly what the setup called for.',
    'First target reached. Booked the R. Excellent execution today.',
    'Winner. Thesis played out perfectly. Back to scanning the tape.',
    'Target achieved. Clean entry, clean exit. That is disciplined trading.',
    'Trade closed at target. Patience and process paid off.',
    'Profit secured. The setup was textbook. Resetting for the next opportunity.',
    'Winner in the books. That is what happens when you wait for the right edge.',
    'Target hit. No luck involved — that was skill and patience combined.',
    'Clean win. The setup, the entry, the management — all executed correctly.',
    'Trade worked exactly as planned. That is what the process looks like.',
  ],
  STOP_HIT: [
    'Stopped out. Loss is taken. Risk was defined — no damage beyond the plan.',
    'Stop hit. That happens sometimes. Not every setup works. Moving on.',
    'Took the loss. Position was sized correctly. Ready for the next opportunity.',
    'Stopped out. The thesis was invalidated. That is exactly why stops exist.',
    'Loss booked. Clean risk management. Back to observation mode now.',
    'Stop hit. One loss does not define the edge. Looking for the next setup.',
    'Trade did not work out. Cut the loss quickly. That is the discipline.',
    'Stopped out. Re-evaluating conditions. Back to analysis mode.',
    'The market said no. I respected that with a defined stop. Moving on.',
    'Loss taken cleanly. Setup was valid. Sometimes the market has other plans.',
  ],
  NO_EDGE: [
    'Edge score too low to act. Completely standing aside.',
    'No identifiable setup right now. Waiting for conditions to develop.',
    'Market structure is weak. Not worth the risk at these levels.',
    'Zero edge on any instrument right now. Watching only.',
    'Unfavorable conditions across the board. Capital stays on the sideline.',
    'Nothing is setting up. I would rather miss a trade than take a bad one.',
    'The score does not support a trade. Watching for the environment to improve.',
  ],
};

// ── Jokes ─────────────────────────────────────────────────────────────────────
const JOKES: string[] = [
  "Why did the trader go broke? He kept buying high and selling low. Classic beginner mistake.",
  "They say buy low, sell high. Sounds easy until you realize nobody rings a bell at the bottom.",
  "What is a day trader's favorite movie? The Big Short. Followed closely by Margin Call.",
  "Why do traders make great poker players? They already know how to hold their losses.",
  "I told my partner I was going to make a killing in the market today. They said please don't — we need that money.",
  "What is the difference between a trader and a pizza? A pizza can feed a family of four.",
  "A bear, a bull, and a trader walk into a bar. Only the trader leaves with money — and only because he shorted the other two.",
  "Why did the technical analyst get kicked out of the casino? He kept drawing support lines on the roulette table.",
  "What do you call a trader who never takes a loss? A liar.",
  "I tried to explain options trading to my grandmother. Now she is my broker.",
  "Why do traders love volatility? Because it is the only way they feel alive.",
  "Two traders are arguing about the direction of the market. They are both wrong.",
  "My strategy is simple. Buy when price is low. Sell when my therapist tells me to.",
  "What is a trader's least favorite word? Retracement. Unless it goes their way.",
  "You know what they say — the market can stay irrational longer than you can stay solvent. I have personally tested that.",
  "Why did the futures trader sleep so well? He finally had stops in place.",
  "I have three emotions as a trader. Greed, fear, and — I swear I did not just do that.",
  "What separates a successful trader from an unsuccessful one? About six months and a funded account.",
  "The market is like a mirror. It shows you exactly who you are. Most people don't like what they see.",
  "My favorite candlestick pattern is called the Oh No. You'll know it when you see it.",
  "Why do analysts always hedge their predictions? Because if you are wrong in four different ways, at least one of them was right.",
  "What do you call someone who predicted the last ten market crashes? A perma-bear with a podcast.",
  "The stock market is a device for transferring money from the impatient to the patient. I am trying to be the patient one.",
  "Why did the trader bring a ladder to the market? He heard prices were going up.",
  "Risk management is not exciting. But neither is blowing up your account. Pick your boring.",
  "I asked my edge score what it thought about this trade. It said ask again later.",
  "What is a momentum trader's favorite song? Running With the Bulls. Or maybe Running Scared.",
  "Apparently there are two rules in trading. Rule one: never lose money. Rule two: never forget rule one. I am working on both.",
  "Trading is ninety percent psychology and ten percent strategy. The other fifty percent is math. Wait — that doesn't add up. Exactly.",
  "Why did the candlestick go to therapy? It had too many wicks and not enough body.",
];

// ── Questions directed at the user ────────────────────────────────────────────
const QUESTIONS: string[] = [
  "Hey, quick question — what is your biggest trading goal this month?",
  "Do you prefer scalping quick moves or swinging for a bigger target? I'm curious about your style.",
  "What market are you most focused on today? I want to make sure I'm watching the right instrument.",
  "How long have you been trading futures? I'd love to know where you're coming from.",
  "What is the hardest trading rule for you to follow? For most people it's cutting losses quickly.",
  "Do you journal your trades? It's one of the highest-leverage habits you can build.",
  "What does your pre-market routine look like? How do you prepare for the session?",
  "Have you ever nailed a perfect setup and then missed the entry? How did that feel?",
  "What's your favorite time of day to trade — the open, mid-session, or the afternoon push?",
  "Do you trade with a fixed risk per trade, or does your size vary? Walk me through your approach.",
  "What was your best trade ever? I'm genuinely curious what made it work.",
  "What was your most painful lesson as a trader? Mine was learning not to average into a losing position.",
  "Do you think the market is more technical or more psychological? I have a strong opinion on this.",
  "What trading books have shaped the way you think? I have recommendations if you want them.",
  "Are you more comfortable going long or short? Most traders have a natural directional bias.",
  "Do you have a maximum number of trades per day? Overtrading is the number one killer of edge.",
  "If you could change one thing about how you traded last week, what would it be?",
  "How do you handle a losing streak mentally? That's where most traders fall apart.",
  "What got you into futures trading in the first place? I'm always curious about the origin story.",
  "Do you follow the economic calendar? Some of the best and worst moves happen around data releases.",
  "What does a good trading day look like for you — is it about the P and L or the process?",
  "Are you a momentum trader or a mean reversion trader at heart?",
  "How many hours a day are you in front of the screen? Screen time is not the same as productive time.",
  "If the market closed for a week, would you be relieved or stressed? That tells you a lot about your relationship with trading.",
  "Do you have a trading mentor, or are you mostly self-taught? Both paths are valid — just different.",
  "What instrument do you find hardest to read — gold, the Nasdaq, or something else entirely?",
  "Do you ever take breaks mid-session? Walking away from the screen can actually improve your decision-making.",
  "What does your post-trade review look like? That is where most of the real learning happens.",
  "Have you ever traded against your own analysis just because you were bored? No judgment — I've seen it happen.",
  "What would you tell yourself on day one of your trading career?",
];

// ── Personality banter ────────────────────────────────────────────────────────
const BANTER: string[] = [
  "Just so you know, I've been watching this chart so long I'm starting to dream in candlesticks.",
  "You ever notice how the market moves the second you step away? It knows.",
  "Waiting for edge is the hardest part. Anyone who says otherwise hasn't waited long enough.",
  "I'm not saying the market is rigged. I'm just saying it does exactly what it wants, when it wants.",
  "Fun fact — most retail traders lose because they overtrade. The best trade is often the one you don't take.",
  "There is something meditative about watching price action when you are not forcing a trade.",
  "The tape never lies. Traders lie to themselves — but the tape is always honest.",
  "I've seen a lot of setups. The ones I trust most are the ones that make me wait the longest.",
  "Every session is different. That's what keeps this interesting.",
  "Patience is literally a trading edge. The market pays people who wait for high-probability setups.",
  "You know what I find fascinating? The same pattern plays out over and over. Different year, same psychology.",
  "Good setups are like buses. If you miss one, another one is coming. Do not chase.",
  "The quietest traders are usually the most dangerous ones. They wait, then strike with conviction.",
  "There is a reason most prop firm challenges are won by people who trade less, not more.",
  "The best traders I have studied all share one trait — they are completely comfortable doing nothing.",
  "Plan the trade, trade the plan. It sounds simple. It is absolutely not easy.",
  "The market will humble you the moment you think you have it figured out. Every single time.",
  "I respect the market. I don't fear it, I don't love it — I just respect it.",
  "Screen time and productive time are not the same thing. Quality of attention beats quantity of hours.",
  "Losing trades are not failures if your process was sound. That's a hard thing to truly internalize.",
  "Some days the market owes you nothing. Accept that and trade better because of it.",
  "The difference between discipline and stubbornness is whether you are following a rule or ignoring one.",
  "I think about risk before I think about reward. Always. In that order.",
  "Most people focus on entries. The real edge is in exits and position sizing.",
  "If you're not willing to take a small loss, the market will eventually give you a large one.",
  "There's a version of trading where you are calm, clear, and decisive. That version exists. I've seen it.",
  "Consistency beats brilliance in trading. Boring wins. Every time.",
  "One of the hardest skills in trading is knowing when to do absolutely nothing.",
  "The market does not know what you paid. It does not care. That's both humbling and liberating.",
  "Sometimes the smartest thing I do all day is close the chart and go take a walk.",
];

// Tracks cycling position per state so we never say the same thing twice in a row
const _voiceBankIdx: Record<string, number> = {};
function pickVoiceLine(state: string): string {
  const lines = VOICE_BANK[state] ?? VOICE_BANK.WAIT;
  const start = _voiceBankIdx[state] ?? Math.floor(Math.random() * lines.length);
  _voiceBankIdx[state] = (start + 1) % lines.length;
  return lines[start];
}
// Picks from an array cycling forward (no immediate repeats), keyed by label
function _pickCycling(arr: string[], key: string): string {
  const i = (_voiceBankIdx[key] ?? Math.floor(Math.random() * arr.length)) % arr.length;
  _voiceBankIdx[key] = i + 1;
  return arr[i];
}

// ── Demo Mode Engine ─────────────────────────────────────────────────────────
// Simulates live market data for after-hours development and testing.
// Cycles through 7 phases (WAIT → ANALYZING → FORMING → READY → ACTIVE → TARGET → repeat)
// every ~42 seconds. Generates realistic price drift, VWAP, CVD, sweeps, zone hits,
// and edge-score changes that drive the full avatar + dashboard experience.

const DEMO_BASE: Record<string, number> = { MNQ:21240, MGC:3218, MES:5847, MYM:44120 };
const DEMO_ATR:  Record<string, number> = { MNQ:18,    MGC:12,   MES:6,    MYM:85    };
// Seconds each phase holds before advancing
const DEMO_DUR = [7, 6, 6, 6, 7, 7, 3];
// Demo narration bank — randomly selected each phase so lines never repeat in order
const DEMO_NARR: string[][] = [
  // Phase 0 — WAIT / no edge
  ['No edge present. Market consolidating near VWAP. Capital preservation comes first.',
   'Price coiling in a tight range. No structural break yet. Standing aside.',
   'Volume is thin and CVD is flat. Bears and bulls are in equilibrium.',
   'Nothing to do here. Waiting for the market to tip its hand.',
   'Choppy tape with no clear direction. Best position right now is no position.',
   'VWAP is acting as a magnet. Price oscillating without conviction.',
   'No institutional footprints visible at this level. Watching only.',
   'Low probability environment. I will not force a trade into this mess.'],
  // Phase 1 — ANALYZING
  ['Price testing VWAP from below. Bulls attempting a reclaim. Need confirmation.',
   'Approaching the demand zone. Momentum is flattening. Setup not ready yet.',
   'Order flow is mixed. CVD neutral but starting to show buying interest.',
   'Early signs of accumulation near this level. Keeping a close eye.',
   'Buyers defending the low, but no structural break yet. Monitoring closely.',
   'Something is brewing here. Watching for a break of structure to the upside.',
   'Delta ticking up slightly. Not committing yet — need more evidence.',
   'This level is attracting attention. Waiting for a clear signal before acting.'],
  // Phase 2 — FORMING / structure confirmed
  ['BOS confirmed on the lower timeframe. Structure is shifting bullish. Edge building.',
   'Break of structure detected. Looking for a clean pullback into the zone.',
   'Structure break confirmed with bullish CVD. Setup is beginning to develop.',
   'CHOCH on the five minute. Bias shifts long. Watching for the zone tap.',
   'Structural break is clean. Delta confirming. Edge crossing the threshold.',
   'Change of character confirmed. Buyers took out the last swing high.',
   'BOS with above-average volume. Institutions may be accumulating.',
   'Structure aligned. CVD cooperating. One more confirmation and this is live.'],
  // Phase 3 — zone + sweep
  ['Liquidity sweep complete into demand zone. Smart money absorption visible.',
   'Price swept the low and reversed sharply. Classic stop-hunt into demand.',
   'Zone holding firm. Volume spiking on the bid. Setup criteria nearly all met.',
   'Sweep into the demand zone with a strong rejection wick. This is textbook.',
   'Sellers exhausted at the zone. Buyers stepping in with conviction.',
   'Demand zone activated. CVD sharply bullish. Waiting for structure to confirm.',
   'Sweep and reclaim of the zone. Exactly the entry trigger I look for.',
   'Institutional absorption visible at this demand level. Edge is rising fast.'],
  // Phase 4 — READY
  ['High-probability long setup. All gates confirmed. Risk-to-reward meets requirements.',
   'Strong BOS into demand. CVD bullish. VWAP reclaimed. Entry criteria fully met.',
   'Cleanest setup of the session. Structure, zone, and flow are all aligned.',
   'All gate conditions satisfied. This is the setup I have been waiting for.',
   'Full edge confirmation. Every signal is in agreement. Execution window is open.',
   'Textbook long setup. Clean demand zone, bullish delta, structure confirmed above VWAP.',
   'Maximum confidence on the long side right now. Waiting for execution.',
   'This is exactly what disciplined waiting looks like. Perfect setup confirmed.'],
  // Phase 5 — ACTIVE / managing
  ['Position active. Price extending in our direction. Monitoring for the first target.',
   'Trade running. Thesis intact. Moving stop toward break-even on this momentum.',
   'Managing the trade. Price holding above entry. Watching for scale-out level.',
   'Live position. No signs of invalidation. Letting the edge work.',
   'In the trade. Stop is placed. No need to touch it — the setup is playing out.',
   'Position is running well. Monitoring delta for any sign of reversal.',
   'Trade active. Price above VWAP and structure holding. Thesis intact.',
   'Watching every bar. No invalidation signals yet. Staying in the trade.'],
  // Phase 6 — TARGET HIT
  ['Target reached. Trade profitable. Clean execution — exactly what we waited for.',
   'First target hit. Secured the R. Resetting for the next high-probability setup.',
   'Trade closed at target. Discipline rewarded. Back to watching the tape.',
   'Winner. Thesis played out perfectly. That is what patience looks like.',
   'Profit booked. Textbook from entry to exit. Back to scanning for the next one.',
   'Target achieved. Clean risk-to-reward. This is how the process is supposed to work.',
   'Win in the books. Not luck — that was preparation and patience.',
   'Closed at target. No second-guessing, no early exits. Trusted the plan.'],
];

function _buildDemoData(
  phase: number, progress: number,
  ticker: string, price: number, vwap: number,
): Record<string, any> {
  const atr   = DEMO_ATR[ticker]  ?? 18;
  const bases = [8, 22, 40, 64, 86, 88, 88];
  const edge  = Math.min(110, Math.max(0, bases[phase] + (Math.random() - 0.45) * 6 + progress * 5));
  const grade = edge >= 85 ? 'A+' : edge >= 70 ? 'A' : edge >= 50 ? 'B' : 'WAIT';
  const dir   = phase >= 2 ? 'LONG' : '';
  const mbSt  = phase === 4 ? 'READY' : phase === 5 ? 'MANAGING' : phase >= 2 ? 'BUILDING' : 'WAIT';
  const sConf = phase >= 2;
  const zVal  = phase >= 3;
  const vConf = phase >= 2 && price > vwap;
  const hasPl = phase >= 3;
  const stop  = price - atr * 2.2;
  const t1    = price + atr * 2.0;
  const t2    = price + atr * 4.0;
  const cvd   = phase >= 2 ? 'Bullish' : 'Neutral';
  const volS  = phase >= 4 ? 'Strong volume' : phase >= 3 ? 'Increasing volume' : phase <= 0 ? 'Low volume' : 'Normal volume';
  const narr  = DEMO_NARR[phase];
  // Random pick per phase tick — never the same line two ticks in a row
  const _demoNarrKey = `demo_${phase}`;
  const narration = narr[(_voiceBankIdx[_demoNarrKey] = ((_voiceBankIdx[_demoNarrKey] ?? -1) + 1) % narr.length)];
  const activeTrade = phase === 5 ? {
    instrument: ticker, direction: 'LONG',
    entry_price: price - atr * 0.3, stop_price: price - atr * 0.3 - atr * 2.2,
    target1: price - atr * 0.3 + atr * 2.0,
    opened_at: new Date(Date.now() - 45000).toISOString(),
  } : null;
  const recentTrades = phase === 6 ? [{
    id: 'demo-001', outcome: 'win', direction: 'LONG', instrument: ticker,
    opened_at: new Date(Date.now() - 180000).toISOString(),
  }] : [];
  return {
    price, vwap_value: vwap,
    edge_score: edge, edge_grade: grade,
    is_actionable: phase === 4,
    market_status: 'open',
    direction: dir,
    strict_reason: phase < 2 ? 'Structure not confirmed. Waiting for BOS or CHOCH.' : '',
    main_brain: {
      status: mbSt, edge_score: edge, edge_grade: grade, favored_direction: dir,
      wait_reason: phase < 2 ? 'No confirmed structure.' : '',
      signals: { bias: phase >= 2 ? 'Bullish' : 'Neutral', cvd, strategy: phase >= 2 ? 'Liquidity Sweep Reversal' : '' },
    },
    main_brain_voice: { narration },
    gate_debug: { structure_confirmed: sConf, zone_valid: zVal, vwap_confirmed: vConf },
    alert_diagnostics: { cvd, volume: volS, volatility_regime: 'Normal' },
    edge_breakdown: {
      score: edge,
      components: {
        'BOS/CHOCH': phase >= 2 ? 20 : 0, 'VWAP': phase >= 2 ? 15 : 0,
        'Volume': phase >= 3 ? 15 : phase >= 1 ? 5 : 0,
        'CVD': phase >= 2 ? 15 : 0, 'Session': 8, 'Sweep': phase >= 3 ? 15 : 0,
      },
    },
    trade_plan: hasPl ? { entry: price, stop, target1: t1, target2: t2, rr_num: 2.0 } : {},
    nearest_demand: price - atr * 2.8, nearest_supply: price + atr * 4.5,
    atr_pts: atr, vol_regime: 'Normal',
    rvol: phase >= 3 ? 1.8 + Math.random() * 0.7 : 0.7 + Math.random() * 0.5,
    active_trade: activeTrade,
    managing_trade: activeTrade ? { ...activeTrade, managed: true } : null,
    recent_trades: recentTrades,
    news_filter: { next_event: { title: 'NFP Report', mins: 47, impact: 'high' } },
    volatility: { ratio: 1.1 + Math.random() * 0.4 }, vol_ratio: 1.15,
    entry_probability: phase >= 4 ? 68 + Math.random() * 12 : phase >= 3 ? 44 + Math.random() * 14 : 18 + Math.random() * 12,
    active_strategy: phase >= 2 ? 'Liquidity Sweep Reversal' : null,
    strategy_mode: 'SCALP', risk_level: phase >= 4 ? 'Low' : phase >= 2 ? 'Medium' : 'Low',
    _demo: true,
  };
}

function useDemoEngine(
  enabled: boolean, ticker: string,
  setData: (d: any) => void, setLoading: (v: boolean) => void,
): void {
  const phaseRef      = useRef(0);
  const phaseStartRef = useRef(Date.now());
  const priceRef      = useRef(0);
  const vwapRef       = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    const base = DEMO_BASE[ticker] ?? 21240;
    phaseRef.current      = 0;
    phaseStartRef.current = Date.now();
    priceRef.current      = base;
    vwapRef.current       = base * 0.9992;
    setLoading(false);
    const tick = () => {
      const base2 = DEMO_BASE[ticker] ?? 21240;
      const atr2  = DEMO_ATR[ticker]  ?? 18;
      // Micro price drift — biased slightly bullish during READY buildup
      priceRef.current += (Math.random() - 0.48) * atr2 * 0.14;
      vwapRef.current  += (Math.random() - 0.50) * atr2 * 0.04;
      // Clamp drift to ±6/+8 ATRs from base so price stays realistic
      priceRef.current = base2 + Math.max(-atr2 * 6, Math.min(atr2 * 8, priceRef.current - base2));
      const now      = Date.now();
      const phaseSec = (now - phaseStartRef.current) / 1000;
      const dur      = DEMO_DUR[phaseRef.current];
      const progress = Math.min(phaseSec / dur, 1);
      if (phaseSec >= dur) {
        phaseRef.current      = (phaseRef.current + 1) % 7;
        phaseStartRef.current = now;
        // ACTIVE→TARGET: push price up to simulate target hit
        if (phaseRef.current === 6) priceRef.current += atr2 * 2.1;
        // TARGET→WAIT: reset near base for a fresh cycle
        if (phaseRef.current === 0) {
          priceRef.current = base2 + (Math.random() - 0.5) * atr2 * 2;
          vwapRef.current  = base2 * 0.9992;
        }
      }
      setData(_buildDemoData(phaseRef.current, progress, ticker, priceRef.current, vwapRef.current));
    };
    tick(); // immediate first frame
    const id = setInterval(tick, 1400);
    return () => clearInterval(id);
  }, [enabled, ticker]); // eslint-disable-line react-hooks/exhaustive-deps
}

// ── Root ───────────────────────────────────────────────────────────────────────
export default function Home() {
  const [ticker, setTicker]     = useState<Ticker>('MNQ');
  const manualPickRef           = useRef<number>(0); // ms-timestamp of last manual ticker pick
  const [data,   setData]       = useState<any>(null);
  const [loading, setLoading]   = useState(true);
  const [demoMode, setDemoMode] = useState<boolean>(() => { try { return localStorage.getItem('atp_demo') === '1'; } catch { return false; } });
  useDemoEngine(demoMode, ticker, setData, setLoading);
  const [msgs,   setMsgs]       = useState<Msg[]>([]);
  const [input,  setInput]      = useState('');
  const [asking, setAsking]     = useState(false);
  const [authPwd, setAuthPwd]   = useState<string>(() => { try { return localStorage.getItem('brain_auth') || ''; } catch { return ''; } });
  const [authNeeded, setAuthNeeded] = useState<boolean>(() => { try { return !localStorage.getItem('brain_auth'); } catch { return true; } });
  const [vrmSrc, setVrmSrcRaw] = useState<string>(() => { try { return localStorage.getItem('brain_vrm') || '/LordPiggington.vrm'; } catch { return '/LordPiggington.vrm'; } });
  const [showAvatarPicker, setShowAvatarPicker] = useState(false);
  const [vrmUrlInput, setVrmUrlInput] = useState('');
  const setVrmSrc = useCallback((src: string) => { try { localStorage.setItem('brain_vrm', src); } catch {} setVrmSrcRaw(src); setShowAvatarPicker(false); }, []);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [chatOpen,     setChatOpen]     = useState(false);
  const [chartOpen,    setChartOpen]    = useState(false);
  const [leftOpen,     setLeftOpen]     = useState(false);
  const [confirming,   setConfirming]   = useState(false);
  const [tradeSent,    setTradeSent]    = useState<string | null>(null);
  const [gazeEvent,    setGazeEvent]    = useState<GazeEvt>({ dx:0, dy:0, widen:false, dur:0, id:0 });

  const { entries: memEntries, addEntry: memAddEntry, clear: memClear, context: memContext } = useConvMemory();
  const [memOpen, setMemOpen] = useState(false);

  const chatRef  = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const candlesRef    = useRef<Candle[]>([]);
  const priceBaseRef  = useRef<number>(0);
  const speakRef          = useRef<(t: string) => void>(() => {});
  const lastSpokenRef     = useRef('');
  const lastSpokeAtRef    = useRef(0);
  const askVoiceRef       = useRef<(t: string) => void>(() => {});
  const voiceListeningRef = useRef(false);
  // Gaze event detection — track previous poll values to detect transitions
  const prevStatusRef = useRef('');
  const prevEdgeRef   = useRef(0);
  const prevStructRef = useRef(false);
  const prevZoneRef   = useRef(false);

  const clock = useClock();
  const { voices, voiceName, setVoice, muted, setMuted, speaking, speak, speechCtrlRef, unlockAudio } = useTTS();
  useEffect(() => { speakRef.current = speak; }, [speak]);
  const onVoiceTranscript = useCallback((t: string) => { askVoiceRef.current(t); }, []);
  const { voiceState, setVoiceState: setVoiceSt, transcript: voiceTranscript,
          errorMsg: voiceErrorMsg, startListening, stopListening, cancelListening,
          clearError: clearVoiceError } = useVoiceInput({ onTranscript: onVoiceTranscript });

  const authHeader = useMemo((): Record<string,string> =>
    authPwd ? { 'Authorization': 'Basic ' + btoa('admin:' + authPwd) } : {}
  , [authPwd]);

  const handleAuth = useCallback((pwd: string) => {
    try { localStorage.setItem('brain_auth', pwd); } catch {}
    setAuthPwd(pwd); setAuthNeeded(false);
  }, []);

  const poll = useCallback(async () => {
    if (!authPwd || demoMode) return;
    try {
      const r = await fetch(`/api/status?ticker=${ticker}`, { credentials:'include', headers:authHeader });
      if (r.status === 401) { setAuthNeeded(true); setAuthPwd(''); try { localStorage.removeItem('brain_auth'); } catch {} return; }
      if (r.ok) {
        const d = await r.json(); setData(d); setLoading(false);
        const p = Number(d?.price || 0);
        if (p > 0) {
          const pct = Math.abs(p - priceBaseRef.current) / (priceBaseRef.current || 1);
          if (candlesRef.current.length === 0 || pct > 0.006) { priceBaseRef.current = p; candlesRef.current = makeCandles(p); }
          else { const c = candlesRef.current; if (c.length > 0) { const last = c[c.length-1]; c[c.length-1] = { ...last, c:p, h:Math.max(last.h,p), l:Math.min(last.l,p) }; } }
        }
      }
    } catch {}
  }, [ticker, authPwd, authHeader, demoMode]);

  useEffect(() => {
    if (demoMode) return; // demo engine owns data when active
    setLoading(true); setData(null); candlesRef.current = [];
    poll(); const id = setInterval(poll, 3000); return () => clearInterval(id);
  }, [poll, demoMode]);

  // ── 30s auto-follow: switch to highest-edge / actionable ticker ────────────
  const autoTickerRef     = useRef<Ticker>('MNQ');
  const autoAuthHdrRef    = useRef<Record<string,string>>({});
  useEffect(() => { autoTickerRef.current = ticker; }, [ticker]);
  useEffect(() => { autoAuthHdrRef.current = authHeader; }, [authHeader]);
  useEffect(() => {
    const ALL = ['MNQ','MGC','MES','MYM'] as const;
    const id = setInterval(async () => {
      if (demoMode) return;
      const hdr = autoAuthHdrRef.current;
      if (!hdr['Authorization']) return;
      if (Date.now() - manualPickRef.current < 30000) return; // sticky after manual pick
      try {
        const res = await Promise.all(ALL.map(t =>
          fetch(`/api/status?ticker=${t}`, { credentials:'include', headers:hdr })
            .then(r => r.ok ? r.json() : null).catch(() => null)
        ));
        const scored = ALL.map((t, i) => {
          const d = res[i];
          const isAct = d && /READY|MANAGING/.test(String(d.main_brain?.status ?? d.status ?? ''));
          const es    = Number(d?.main_brain?.edge_score ?? d?.edge_score ?? 0);
          return { t, isAct, es };
        }).sort((a, b) => (a.isAct !== b.isAct ? (a.isAct ? -1 : 1) : b.es - a.es));
        const best = scored[0];
        if (best && best.t !== autoTickerRef.current) setTicker(best.t);
      } catch {}
    }, 30000);
    return () => clearInterval(id);
  }, [demoMode]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight; }, [msgs]);

  // Derived
  const mb      = (data?.main_brain || {}) as Record<string,any>;
  const voice_d = (data?.main_brain_voice || {}) as Record<string,any>;
  const status  = (mb.status || 'WATCHING') as string;
  const edge    = Number(mb.edge_score ?? data?.edge_score ?? 0);
  const grade   = (mb.edge_grade ?? data?.edge_grade ?? '') as string;
  const dirn    = (mb.favored_direction ?? '') as string;
  const price   = Number(data?.price || 0);
  const strictR = (data?.strict_reason || mb.wait_reason || '') as string;
  const marketStatus = (data?.market_status ?? '') as string;
  const isOpen  = /open/i.test(marketStatus);
  const isActionable = data?.is_actionable === true || status === 'READY';
  const isManaging = !!(data?.active_trade || data?.managing_trade);

  // Intelligence panel shortcuts
  const sig = (mb.signals              || {}) as Record<string,any>;
  const ad  = (data?.alert_diagnostics || {}) as Record<string,any>;
  const gd  = (data?.gate_debug        || {}) as Record<string,any>;
  const eb  = (data?.edge_breakdown    || mb.edge_breakdown || {}) as Record<string,any>;

  // Connector signal colors — one per MC card (same order as MC_CARDS)
  const connSigs: SigColor[] = [
    edge >= 75 ? 'green' : edge >= 55 ? 'yellow' : edge >= 30 ? 'blue' : 'gray',
    (() => { const ep = Number(data?.entry_probability ?? (data?.analyst as any)?.entry_probability ?? 0); return ep >= 65 ? 'green' : ep >= 45 ? 'yellow' : 'gray'; })(),
    (data?.active_strategy || data?.strategy_mode || sig.strategy) ? 'blue' : 'gray',
    (() => { const b = String(sig.bias || '').toLowerCase(); return /bull/.test(b) ? 'green' : /bear/.test(b) ? 'red' : 'gray'; })(),
    !!gd.structure_confirmed ? 'green' : 'gray',
    !!gd.zone_valid ? 'yellow' : (data?.nearest_demand || data?.nearest_supply) ? 'blue' : 'gray',
    (() => { const vv = Number(data?.vwap_value || 0), pp = Number(data?.price || 0); return vv > 0 ? (pp > vv ? 'green' : 'red') : 'gray'; })(),
    (() => { const c = String(sig.cvd || ad.cvd || '').toLowerCase(); return /bull|pos/.test(c) ? 'green' : /bear|neg/.test(c) ? 'red' : 'gray'; })(),
    (() => { const v = String(ad.volume || '').toLowerCase(); return /strong|high/.test(v) ? 'green' : /incr/.test(v) ? 'yellow' : /low|thin/.test(v) ? 'gray' : 'blue'; })(),
    (() => { const tp = (data?.trade_plan || {}) as Record<string,any>; return (tp.entry || tp.stop) ? (status === 'READY' ? 'green' : 'blue') : 'gray'; })(),
    (() => { const vr = Number((data?.volatility as any)?.ratio ?? data?.vol_ratio ?? 0); return vr > 2.5 ? 'red' : vr > 1.5 ? 'yellow' : vr > 0 ? 'green' : 'gray'; })(),
    (() => { const r = String((data?.risk_level ?? sig.risk_level ?? '') as string).toLowerCase(); return /high/.test(r) ? 'red' : /med/.test(r) ? 'yellow' : /low/.test(r) ? 'green' : 'blue'; })(),
  ];
  const connSigStr = connSigs.join(',');
  const connSigsPrev = useRef('');
  const [flashIds, setFlashIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!connSigsPrev.current) { connSigsPrev.current = connSigStr; return; }
    if (connSigStr === connSigsPrev.current) return;
    const prev = connSigsPrev.current.split(',');
    const changed = new Set<string>();
    connSigs.forEach((s, idx) => { if (s !== prev[idx]) changed.add(MC_CARDS[idx].id); });
    connSigsPrev.current = connSigStr;
    if (changed.size === 0) return;
    setFlashIds(changed);
    const t = setTimeout(() => setFlashIds(new Set()), 1500);
    return () => clearTimeout(t);
  }, [connSigStr]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Session memory ──────────────────────────────────────────────────────────
  const todayStr = getToday();
  const [showBriefing, setShowBriefing] = useState<boolean>(() => {
    try { return !localStorage.getItem('atp_briefed_' + getToday()); } catch { return false; }
  });
  useEffect(() => {
    if (!showBriefing) return;
    const t = setTimeout(() => {
      setShowBriefing(false);
      try { localStorage.setItem('atp_briefed_' + getToday(), '1'); } catch {}
    }, 16000);
    return () => clearTimeout(t);
  }, [showBriefing]);
  const dismissBriefing = () => {
    setShowBriefing(false);
    try { localStorage.setItem('atp_briefed_' + getToday(), '1'); } catch {}
  };
  const mem        = useSessionMemory(status, edge, ticker, strictR);
  const tradeArr   = useMemo(() => Array.isArray(data?.recent_trades) ? (data.recent_trades as any[]) : [], [data]); // eslint-disable-line react-hooks/exhaustive-deps
  const tm         = useTradeMemory(tradeArr);
  const objectives = useMemo(() => computeObjectives(tm, mem.mcWR), [tm, mem.mcWR]);
  const briefingText = generateBriefing(mem.yest, mem.wkPeak, mem.active, mem.mcWR, tm);

  // Live thought stream — change-triggered + 90s cadence, timestamped
  const streamedThoughts = useLiveThoughtStream(data, status, edge, grade, sig, ad, gd);
  const radar            = useMemo(
    () => getEvidenceRadar(data, gd, ad, sig, edge),
    [data, edge] // eslint-disable-line react-hooks/exhaustive-deps
  );

  // Monologue: cycles through data-driven thoughts; restarts only on status change
  const thoughts  = useMemo(() => buildThoughts(data, status, edge, grade), [data, status, edge, grade]);
  const { text: displayed, live: streaming } = useMonologue(thoughts, status);
  const checklist = data ? getBrainChecklist(data) : [];

  // Transient outcome state — STOP_HIT / TARGET_HIT for 22s after a closed trade
  const [outcomeState, setOutcomeState] = useState<'none'|'win'|'loss'>('none');
  const lastTradeIdRef = useRef<string|null>(null);
  useEffect(() => {
    if (!data) return;
    const trades: any[] = data?.recent_trades ?? data?.by_instrument_today ?? [];
    const latest = trades[0];
    if (!latest) return;
    const tid = String(latest.id ?? latest.opened_at ?? '');
    if (!tid || tid === lastTradeIdRef.current) return;
    lastTradeIdRef.current = tid;
    const out = String(latest.outcome ?? latest.result ?? '').toLowerCase();
    if (/win|profit|target/.test(out)) {
      setOutcomeState('win');
      setTimeout(() => setOutcomeState('none'), 22000);
    } else if (/loss|stop|sl/.test(out)) {
      setOutcomeState('loss');
      setTimeout(() => setOutcomeState('none'), 22000);
    }
  }, [data]);

  // Avatar emotional state — maps trading context to one of 9 expressions
  const avState: AvatarState = (() => {
    if (outcomeState === 'loss')                                    return 'STOP_HIT';
    if (outcomeState === 'win')                                     return 'TARGET_HIT';
    if (isManaging)                                                 return 'ACTIVE';
    if (status === 'READY' && /long|bull/i.test(dirn))             return 'READY_LONG';
    if (status === 'READY' && /short|bear/i.test(dirn))            return 'READY_SHORT';
    if (status === 'BUILDING' || edge >= 50)                        return 'FORMING';
    if (edge >= 28)                                                 return 'ANALYZING';
    if (edge < 20)                                                  return 'NO_EDGE';
    return 'WAIT';
  })();

  // Keep a current snapshot of values needed when avState transitions fire
  const memDataRef = useRef({ ticker, edge, grade });
  useEffect(() => { memDataRef.current = { ticker, edge, grade }; });

  // Auto-log notable avState transitions into session memory
  const prevAvStateRef = useRef<AvatarState | null>(null);
  useEffect(() => {
    if (prevAvStateRef.current === avState) return;
    const prev = prevAvStateRef.current;
    prevAvStateRef.current = avState;
    if (prev === null) return;
    const { ticker: t, edge: e, grade: g } = memDataRef.current;
    const en = Math.round(e);
    if (avState === 'READY_LONG')  memAddEntry('setup', 'LONG setup on ' + t + ' — Edge ' + en + '/110' + (g ? ' (' + g + ')' : ''));
    if (avState === 'READY_SHORT') memAddEntry('setup', 'SHORT setup on ' + t + ' — Edge ' + en + '/110' + (g ? ' (' + g + ')' : ''));
    if (avState === 'STOP_HIT')    memAddEntry('trade', 'Trade stopped out on ' + t);
    if (avState === 'TARGET_HIT')  memAddEntry('trade', 'Target hit on ' + t);
    if (avState === 'ACTIVE' && (prev === 'READY_LONG' || prev === 'READY_SHORT')) {
      memAddEntry('trade', 'Position entered on ' + t + ' (' + (prev === 'READY_LONG' ? 'LONG' : 'SHORT') + ')');
    }
  }, [avState, memAddEntry]);

  const avCfg = AV_CFG[avState];
  const eyeColor = `rgb(${avCfg.eye[0]},${avCfg.eye[1]},${avCfg.eye[2]})`;

  // Narration — declared after avState so pickVoiceLine(avState) resolves correctly
  const narration = (
    voice_d.narration ||
    (mb.synthesis as any)?.narrative ||
    mb.summary ||
    (loading ? '' : pickVoiceLine(avState))
  ) as string;

  // idleLine — set by the chatter timer; cleared when a real narration fires or
  // the state goes active so we never show a stale joke during a live trade.
  const [idleLine, setIdleLine] = useState('');
  useEffect(() => {
    if (['READY_LONG','READY_SHORT','ACTIVE','TARGET_HIT','STOP_HIT'].includes(avState)) {
      setIdleLine('');
    }
  }, [avState]);

  // What actually appears in the narration display and caption
  const displayNarration = idleLine || narration;

  // Confidence ring color — communicates AI state at a glance before text is read
  const ringColor = (() => {
    if (voiceState === 'listening') return '#3b82f6'; // blue — listening to user
    if (!isOpen && data)    return '#374151';   // gray   — market closed
    if (isManaging)         return '#06b6d4';   // cyan   — active trade monitoring
    if (status === 'READY') return '#22c55e';   // green  — trade ready
    if (edge >= 60)         return '#f97316';   // orange — high attention, close to READY
    if (edge >= 40)         return '#eab308';   // yellow — setup forming
    if (edge >= 15)         return '#3b82f6';   // blue   — observing, scanning
    return '#374151';                            // gray   — no edge / insufficient data
  })();

  useEffect(() => {
    if (narration && narration !== lastSpokenRef.current) {
      lastSpokenRef.current = narration;
      lastSpokeAtRef.current = Date.now();
      setIdleLine(''); // real narration takes over the display
      speakRef.current(narration);
    }
  }, [narration]); // eslint-disable-line react-hooks/exhaustive-deps

  // Idle chatter + market commentary timer
  // Fires every 12-20s (idle states) or 22-38s (forming/analyzing).
  // Silence guard is 8s so it never interrupts an ongoing TTS utterance.
  // Picks jokes/questions/banter ~65% of the time; market context ~35%.
  const avStateRef   = useRef(avState);
  const cockpitRef   = useRef({ data, edge, avState });
  const setIdleLineRef = useRef(setIdleLine);
  useEffect(() => { avStateRef.current = avState; }, [avState]);
  useEffect(() => { cockpitRef.current = { data, edge, avState }; }, [data, edge, avState]);
  useEffect(() => { setIdleLineRef.current = setIdleLine; }, [setIdleLine]);
  useEffect(() => {
    let tid: ReturnType<typeof setTimeout>;
    const fire = () => {
      const { data: d, edge: eg, avState: st } = cockpitRef.current;
      const silentMs  = Date.now() - lastSpokeAtRef.current;
      const isIdle    = st === 'WAIT' || st === 'NO_EDGE';
      const isActive  = st === 'ACTIVE' || st === 'READY_LONG' || st === 'READY_SHORT';
      const silenceMs = isIdle ? 8000 : isActive ? 20000 : 12000;
      const nextMs    = isIdle
        ? 12000 + Math.random() * 8000   // 12-20 s when idle
        : isActive
          ? 30000 + Math.random() * 20000 // 30-50 s during live trade
          : 22000 + Math.random() * 16000; // 22-38 s while forming

      if (silentMs >= silenceMs) {
        // Build market context pool
        const px  = Number(d?.price          || 0);
        const vw  = Number(d?.vwap_value     || 0);
        const dem = Number(d?.nearest_demand || 0);
        const sup = Number(d?.nearest_supply || 0);
        const gd  = (d?.gate_debug        || {}) as Record<string,any>;
        const ad  = (d?.alert_diagnostics || {}) as Record<string,any>;
        const cv  = String(ad.cvd  || '').toLowerCase();
        const vol = String(ad.volume || '').toLowerCase();
        const sc  = !!gd.structure_confirmed;
        const zv  = !!gd.zone_valid;
        const egR = Math.round(eg);
        const ctx: string[] = [];
        if (px > 0 && vw > 0) {
          const diff = ((Math.abs(px - vw) / vw) * 100).toFixed(2);
          ctx.push(px > vw
            ? `Price is trading ${diff} percent above vee-wap. Bulls are holding above the key intraday level.`
            : `Price is ${diff} percent below vee-wap. Sellers are in control of the intraday structure.`);
        }
        if (zv)              ctx.push('The key zone is active. Watching closely for a reaction.');
        if (dem > 0 && !zv) ctx.push('Watching the demand zone below. That is the level that could trigger a long setup.');
        if (sup > 0 && !zv) ctx.push('Supply overhead is the main obstacle. Bulls need to clear that level.');
        if (sc && !zv)      ctx.push('Structure is confirmed. Waiting for the zone to complete the full setup.');
        if (!sc)            ctx.push('No confirmed break of structure yet. That is the primary signal I am waiting for.');
        if (/bull|pos/.test(cv))     ctx.push('Cumulative delta is bullish. Buyers are active in the order flow right now.');
        if (/bear|neg/.test(cv))     ctx.push('Cumulative delta is bearish. Sellers are controlling the tape.');
        if (/strong|high/.test(vol)) ctx.push('Volume is elevated. Real institutional participation is showing up.');
        if (/low|thin/.test(vol))    ctx.push('Volume is light right now. I want to see more participation before acting.');
        if (egR > 0) ctx.push(`Edge score is at ${egR} out of 110. ${egR >= 75 ? 'That is above my entry threshold.' : egR >= 50 ? 'Getting closer — watching for the final signals.' : 'Not enough edge to trade yet.'}`);

        // Weighted category roll — jokes/questions/banter dominate for personality
        const roll = Math.random();
        let line: string;
        if (roll < 0.28) {
          line = _pickCycling(JOKES, '__jokes');
        } else if (roll < 0.55) {
          line = _pickCycling(QUESTIONS, '__questions');
        } else if (roll < 0.72) {
          line = _pickCycling(BANTER, '__banter');
        } else {
          const pool = ctx.length > 0 ? ctx : [pickVoiceLine(st)];
          line = pool[Math.floor(Math.random() * pool.length)];
        }

        if (line !== lastSpokenRef.current) {
          lastSpokenRef.current = line;
          lastSpokeAtRef.current = Date.now();
          setIdleLineRef.current(line); // ← drives the displayed narration text
          speakRef.current(line);
        }
      }
      tid = setTimeout(fire, nextMs);
    };
    // First fire after a short warm-up so the page feels alive quickly
    tid = setTimeout(fire, 10000 + Math.random() * 8000);
    return () => clearTimeout(tid);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Detect market events → fire a gaze direction that drives eye movement
  useEffect(() => {
    if (!data) return;
    const structNow = !!(data.gate_debug?.structure_confirmed);
    const zoneNow   = !!(data.gate_debug?.zone_valid);
    let next: Omit<GazeEvt,'id'> | null = null;

    if (status === 'READY' && prevStatusRef.current && prevStatusRef.current !== 'READY') {
      // Trade ready — eyes snap forward to look directly at the user
      next = { dx: 0, dy: -1.5, widen: false, dur: 4200 };
    } else if (status === 'MANAGING' && prevStatusRef.current && prevStatusRef.current !== 'MANAGING') {
      // Position opened — eyes settle downward in focused monitoring mode
      next = { dx: 0.4, dy: 2.2, widen: false, dur: 3000 };
    } else if (structNow && !prevStructRef.current) {
      // Structure break confirmed — glance upper-left toward analysis panels
      next = { dx: -4.2, dy: -1.2, widen: false, dur: 2500 };
    } else if (zoneNow && !prevZoneRef.current) {
      // Zone / liquidity sweep detected — eyes shift right-down toward evidence, widen briefly
      next = { dx: 3.8, dy: 2.8, widen: true, dur: 2200 };
    } else if (edge - prevEdgeRef.current >= 12) {
      // Edge spike — glance upper-left at structure readings
      next = { dx: -3.5, dy: -0.9, widen: false, dur: 2000 };
    }

    prevStatusRef.current = status;
    prevEdgeRef.current   = edge;
    prevStructRef.current = structNow;
    prevZoneRef.current   = zoneNow;

    if (next) setGazeEvent(g => ({ ...next!, id: g.id + 1 }));
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const verdictLabel =
    status === 'READY' && /long|bull/i.test(dirn)  ? 'READY — LONG' :
    status === 'READY' && /short|bear/i.test(dirn) ? 'READY — SHORT' :
    status === 'READY' ? 'READY TO TRADE' :
    status === 'MANAGING' ? 'MANAGING TRADE' :
    status === 'BUILDING' ? 'BUILDING EDGE' : 'WAIT';

  const verdictColor =
    status === 'READY' && /long|bull/i.test(dirn)  ? BULL :
    status === 'READY' && /short|bear/i.test(dirn) ? BEAR :
    status === 'READY' ? BULL :
    status === 'MANAGING' ? CYAN :
    status === 'BUILDING' ? AMB :
    MUTED;

  const chips =
    status === 'READY'    ? ['Break down the edge.', 'What invalidates this?', 'What does structure say?'] :
    status === 'MANAGING' ? ['Thesis still intact?', 'Where do you partial?', 'Conviction level?'] :
    ['What is missing?', 'Read the tape.', 'What triggers entry?'];

  const ask = useCallback(async (q?: string) => {
    const question = (q ?? input).trim(); if (!question || asking) return;
    setInput(''); setMsgs(m => [...m, mkMsg('user', question)]); setAsking(true);
    setChatOpen(true);
    // Log user message and check for session preferences
    memAddEntry('chat', question.slice(0, 150));
    PREF_PATTERNS.forEach(([pat, note]) => { if (pat.test(question)) memAddEntry('pref', note); });
    // Prepend today's session context so the AI can reference it naturally
    const fullQ = memContext ? memContext + question : question;
    try {
      const r = await fetch('/api/assistant', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json', ...authHeader}, body:JSON.stringify({ question: fullQ, ticker }) });
      if (r.status === 401) { setAuthNeeded(true); setAuthPwd(''); try { localStorage.removeItem('brain_auth'); } catch {} setMsgs(m => [...m, mkMsg('brain', 'Session expired.')]); }
      else {
        const j = await r.json();
        const answer = j.answer || j.error || 'No response.';
        speakRef.current(answer);
        setMsgs(m => [...m, mkMsg('brain', answer)]);
        memAddEntry('insight', answer.slice(0, 140));
      }
    } catch { setMsgs(m => [...m, mkMsg('brain', 'Connection error.')]); }
    finally { setAsking(false); setTimeout(() => inputRef.current?.focus(), 60); }
  }, [input, asking, ticker, authHeader, memContext, memAddEntry]);

  const onKey = (e: React.KeyboardEvent) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } };

  // Sync askVoiceRef to latest ask callback so onVoiceTranscript always calls current version
  useEffect(() => { askVoiceRef.current = ask; }, [ask]);
  // Reset voice state to idle once ask() finishes processing
  useEffect(() => { if (!asking && voiceState === 'processing') setVoiceSt('idle'); }, [asking, voiceState, setVoiceSt]);
  // Keep voiceListeningRef in sync for AvatarCanvas draw loop (read via ref, no re-render)
  useEffect(() => { voiceListeningRef.current = voiceState === 'listening'; }, [voiceState]);
  // Make avatar look directly at user while listening; release naturally when done
  useEffect(() => {
    if (voiceState === 'listening') setGazeEvent({ dx: 0, dy: -0.6, widen: true, dur: 90000, id: Date.now() });
  }, [voiceState]);
  // Speak / barge-in: tap to talk, tap again to stop, tap while AI speaking to interrupt
  const handleSpeak = useCallback(() => {
    unlockAudio();
    if (speaking) {
      window.speechSynthesis?.cancel();
      startListening(); return;
    }
    if (voiceState === 'listening')                              { stopListening();  return; }
    if (voiceState === 'requesting' || voiceState === 'processing') return;
    if (voiceState === 'error')                                  { clearVoiceError(); return; }
    startListening();
  }, [speaking, voiceState, startListening, stopListening, clearVoiceError]);

  const doEnter = async () => {
    if (!confirming) { setConfirming(true); return; }
    setConfirming(false);
    const dir = /short|bear/i.test(dirn) ? 'short' : 'long';
    try {
      // Step 1: broker gateway — the actual execution path (same as Flask dashboard)
      const gw = await fetch('/api/traderspost', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({ ticker: ticker + '1!', contracts: 1 }),
      });
      const gwBody = await gw.json().catch(() => ({}));
      const st = gwBody?.status;
      if (st === 'sent') {
        setTradeSent('✓ Order sent to broker');
        memAddEntry('trade', 'ENTERED ' + dir.toUpperCase() + ' ' + ticker + ' at market');
      } else if (st === 'simulated') {
        setTradeSent('✓ Paper order simulated');
        memAddEntry('trade', 'PAPER ' + dir.toUpperCase() + ' ' + ticker);
      } else if (st === 'manual_required') {
        setTradeSent('📋 ' + (gwBody?.message || 'Place this order manually on your broker'));
        memAddEntry('trade', 'PLAN ' + dir.toUpperCase() + ' ' + ticker);
      } else {
        setTradeSent('✗ ' + (gwBody?.reason || gwBody?.error || 'Gateway error'));
        setTimeout(() => setTradeSent(null), 6000);
        return;
      }
      // Step 2: local tracking — records trade on bot dashboard + Discord
      await fetch('/api/enter', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({ ticker, direction: dir }),
      }).catch(() => {});
    } catch { setTradeSent('✗ Network error'); }
    setTimeout(() => setTradeSent(null), 6000);
  };

  const tp = data?.trade_plan || {};

  const CSS = `
    @keyframes wv      { from{transform:scaleY(0.35)} to{transform:scaleY(1)} }
    @keyframes bDot    { 0%,100%{opacity:1} 50%{opacity:0.25} }
    @keyframes bPulse  { 0%,100%{opacity:.18} 50%{opacity:.05} }
    @keyframes bBreathe{ 0%,100%{transform:scale(1)} 50%{transform:scale(.6)} }
    @keyframes bUp     { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
    @keyframes glow    { 0%,100%{opacity:.65} 50%{opacity:1} }
    @keyframes avrPulse{ 0%,100%{opacity:.18;transform:scale(1)} 50%{opacity:.55;transform:scale(1.04)} }
    @keyframes micPulse { 0%,100%{transform:scale(1);opacity:1} 50%{transform:scale(1.14);opacity:0.72} }
    @keyframes micRing  { 0%{transform:scale(0.95);opacity:0.55} 100%{transform:scale(1.65);opacity:0} }
    @keyframes slideIn { from{opacity:0;transform:translateX(-8px)} to{opacity:1;transform:translateX(0)} }
    @keyframes tsIn    { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
    ::-webkit-scrollbar { width:3px; height:3px; }
    ::-webkit-scrollbar-thumb { background:rgba(255,255,255,0.07); border-radius:2px; }
    ::-webkit-scrollbar-track { background:transparent; }
    .brain-input::placeholder { color:rgba(255,255,255,0.18); }
    .brain-input:focus { outline:none; }
    .input-wrap:focus-within { border-color:rgba(59,130,246,0.30)!important; }
    .ticker-btn { transition:all 0.15s; }
    .ticker-btn:hover { color:rgba(255,255,255,0.7)!important; }
    .chip-btn { cursor:pointer; transition:all 0.15s; }
    .chip-btn:hover { border-color:rgba(59,130,246,0.35)!important; color:rgba(255,255,255,0.65)!important; }
    .accord-toggle { cursor:pointer; transition:background 0.15s; }
    .accord-toggle:hover { background:rgba(255,255,255,0.04)!important; }
    .action-btn { transition:all 0.2s; }
    .action-btn:hover:not(:disabled) { filter:brightness(1.15); }
    .sidebar-panel { animation:slideIn 0.18s ease-out; }
    @media(max-width:760px){.sidebar-l{display:none!important;}}
    @media(max-width:768px){
      /* ── VERTICAL SCROLL COCKPIT — pig hero at top, all panels accessible ── */

      /* Header: compact */
      .hdr-logo-name{display:none!important;}
      .hdr-clock{display:none!important;}
      .hdr-eng{display:none!important;}
      .ticker-btn{padding:3px 7px!important;font-size:10px!important;}

      /* main-center: single column, full vertical scroll, all panels visible */
      .main-center{padding:8px!important;overflow-y:auto!important;overflow-x:hidden!important;gap:10px!important;}

      /* mb-row: avatar stacks on top, brain panel below */
      .mb-row{
        flex-direction:column!important;
        gap:0!important;
        min-height:unset!important;
        height:auto!important;
        margin-bottom:0!important;
        align-items:center!important;
      }

      /* mc-stage: centered column, hide telemetry cards */
      .mc-stage{width:100%!important;align-items:center!important;display:flex!important;flex-direction:column!important;}
      .mc-top-row{display:none!important;}
      .mc-bot-row{display:none!important;}
      .mc-col{display:none!important;}
      .mc-mid-row{flex:unset!important;gap:0!important;justify-content:center!important;align-items:flex-start!important;}

      /* Avatar hero: 342×455 → scale(0.52) → 178×237px visible, centered */
      .mc-avtr-outer{
        width:178px!important;height:237px!important;
        overflow:hidden!important;
        flex:unset!important;flex-shrink:0!important;
        align-items:flex-start!important;justify-content:flex-start!important;
      }
      .mc-avtr-box{
        transform:scale(0.52)!important;
        transform-origin:top left!important;
      }

      /* Corner overlays — already hidden globally, rule kept for specificity */

      /* Full-screen overlay not used in this layout */
      .mob-avtr-overlay{display:none!important;}

      /* Brain panel: full width, vertical column below the avatar */
      .mb-brain{
        display:flex!important;flex:unset!important;
        width:100%!important;min-width:0!important;
        gap:10px!important;justify-content:flex-start!important;
        padding:8px 0 0!important;
      }
      .verdict-big{font-size:28px!important;letter-spacing:-0.02em!important;}
      .verdict-sub{font-size:14px!important;margin-top:3px!important;}
      .edge-wrap{max-width:unset!important;}
      .wait-box{font-size:10px!important;padding:6px 8px!important;}

      /* intel-strip: 2-column wrap grid so all 4 info panels are readable */
      .intel-strip{flex-wrap:wrap!important;gap:8px!important;margin-bottom:10px!important;}
      .intel-strip>*{flex-basis:calc(50% - 4px)!important;min-width:unset!important;flex:unset!important;}

      /* Quick chips: wrap freely */
      .quick-chips{gap:5px!important;margin-bottom:10px!important;}
    }
    /* Hidden on desktop, shown on mobile */
    .mob-avtr-overlay{display:none;}
    .sat-col{opacity:0.90;transition:opacity 0.35s ease;}
    .sat-col:hover{opacity:1!important;}
    @keyframes evPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.4;transform:scale(1.9)}}
    @media(max-width:1000px){.sat-col{display:none!important;}.avtr-col{justify-content:center;}}
    @media(max-width:760px){.intel-strip{flex-wrap:wrap!important;}.intel-strip>*{flex-basis:calc(50% - 4px)!important;min-width:unset!important;}}
    @media(max-width:500px){.intel-strip{display:none!important;}}
    @media(max-width:760px){.mem-panel{flex-wrap:wrap!important;}.mem-panel>*{flex-basis:calc(50% - 4px)!important;min-width:unset!important;}}
    @media(max-width:500px){.mem-panel{display:none!important;}}
    /* Desktop layout: avatar LEFT (294px, CSS-scaled 70%) + brain content fills RIGHT */
    @media(min-width:769px){
      .main-center{zoom:1;}
      .mc-col{display:none!important;}
      .mb-row{display:flex!important;flex-direction:row!important;gap:20px!important;align-items:flex-start!important;min-height:unset!important;}
      .mb-brain{flex:1!important;min-width:0!important;grid-column:unset!important;}
      .mc-stage{flex:0 0 294px!important;width:294px!important;display:flex!important;flex-direction:column!important;align-items:flex-start!important;grid-column:unset!important;}
      .mc-avtr-outer{width:294px!important;height:392px!important;overflow:hidden!important;align-items:flex-start!important;justify-content:flex-start!important;flex-shrink:0!important;}
      .mc-avtr-box{transform:scale(0.70)!important;transform-origin:top left!important;}
      .mc-mid-row{width:294px!important;justify-content:flex-start!important;}
      .verdict-big{font-size:36px!important;letter-spacing:-0.03em!important;}
      .verdict-sub{font-size:17px!important;margin-top:5px!important;}
      .edge-wrap{max-width:unset!important;}
    }
    .mc-stage{display:flex;flex-direction:column;gap:8px;flex-shrink:0;position:relative;isolation:isolate;}
    /* ConnectorSVG wires with no target cards are visual noise — hidden */
    .mc-stage>svg{display:none!important;}
    /* Corner sats sit position:absolute inside the avatar box and overlap the pig */
    .avtr-corner-sat{display:none!important;}
    /* mc-top-row (Edge Score/Win Prob/Strategy) is duplicated in the brain panel */
    .mc-top-row{display:none!important;}
    .mc-bot-row{display:flex;gap:8px;}
    .mc-top-row>.mc-card,.mc-bot-row>.mc-card{flex:1;min-width:0;}
    .mc-mid-row{display:flex;gap:8px;align-items:stretch;}
    /* Flanking data columns — narrower, subtler so avatar breathes */
    .mc-col{display:flex;flex-direction:column;gap:8px;width:112px;flex-shrink:0;opacity:0.72;}
    .mc-col:hover{opacity:1;transition:opacity 0.2s;}
    .mc-col>.mc-card{flex:1;min-height:0;}
    .mc-card{background:rgba(5,8,18,0.58);border:1px solid rgba(255,255,255,0.036);border-radius:10px;padding:10px 12px;transition:border-color 0.6s ease,box-shadow 0.6s ease,background 0.25s ease;animation:mcFloat 7s ease-in-out infinite;position:relative;z-index:1;}
    .mc-card:hover{background:rgba(10,15,34,0.72)!important;border-color:rgba(255,255,255,0.09)!important;}
    .mc-label{font-size:8.5px;font-family:monospace;font-weight:700;letter-spacing:0.10em;text-transform:uppercase;color:rgba(255,255,255,0.18);}
    .mc-value{font-size:13px;font-family:monospace;font-weight:800;letter-spacing:0.03em;line-height:1.1;margin-top:4px;opacity:0.82;}
    .mc-sub{font-size:9px;font-family:monospace;color:rgba(255,255,255,0.18);margin-top:3px;line-height:1.3;}
    @keyframes mcFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-1.8px)}}
    @keyframes connFlash{0%{opacity:0.92}50%{opacity:0.55}100%{opacity:0}}
    @media(max-width:1100px){.mc-col{width:108px!important;}.mc-card{padding:7px 9px!important;}.mc-value{font-size:11.5px!important;}}
    @media(min-width:769px) and (max-width:900px){
      .mc-top-row{display:none!important;}
      .mc-bot-row{display:none!important;}
      .mc-col{display:none!important;}
      .mc-mid-row{justify-content:center!important;gap:0!important;}
      .mc-stage{min-height:unset!important;}
    }
  `;

  if (authNeeded) return <><style>{CSS}</style><LoginOverlay onSubmit={handleAuth} /></>;

  // Aura glow color
  const auraColor =
    avState === 'READY_LONG'  ? '#22c55e' :
    avState === 'READY_SHORT' ? '#ef4444' :
    avState === 'ACTIVE'      ? CYAN       :
    avState === 'FORMING'     ? '#f59e0b' :
    avState === 'ANALYZING'   ? '#60a5fa' :
    avState === 'STOP_HIT'    ? '#f87171' :
    avState === 'TARGET_HIT'  ? '#fbbf24' : BLUE;

  return (
    <div style={{ height:'100vh', background:'#060810', color:'#fff', display:'flex', flexDirection:'column',
      fontFamily:'-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif', overflow:'hidden', userSelect:'none' }}>
      <style>{CSS}</style>

      {/* ── HEADER ────────────────────────────────────────────────────────── */}
      <header style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'0 18px',
        height:48, borderBottom:'1px solid rgba(255,255,255,0.040)', flexShrink:0, gap:12 }}>
        {/* Logo + tickers */}
        <div style={{ display:'flex', alignItems:'center', gap:14 }}>
          <div style={{ display:'flex', alignItems:'center', gap:7 }}>
            <div style={{ width:26, height:26, borderRadius:7, background:'rgba(59,130,246,0.18)',
              border:'1px solid rgba(59,130,246,0.32)', display:'flex', alignItems:'center', justifyContent:'center',
              fontSize:12, fontWeight:800, color:'#93c5fd', boxShadow:`0 0 12px ${eyeColor}44` }}>A</div>
            <span className="hdr-logo-name" style={{ fontSize:12.5, fontWeight:700, color:'rgba(255,255,255,0.75)', letterSpacing:'-0.01em' }}>AI Trading Partner</span>
          </div>
          <div style={{ display:'flex', gap:1 }}>
            {(['MNQ','MGC','MES','MYM'] as const).map(t => (
              <button key={t} className="ticker-btn" onClick={() => { manualPickRef.current = Date.now(); setTicker(t); }} style={{
                padding:'3px 11px', borderRadius:5, cursor:'pointer', fontSize:11.5, fontWeight:700,
                fontFamily:'monospace', letterSpacing:'0.06em',
                background: ticker === t ? 'rgba(59,130,246,0.22)' : 'transparent',
                color: ticker === t ? '#93c5fd' : 'rgba(255,255,255,0.26)',
                border: ticker === t ? '1px solid rgba(59,130,246,0.32)' : '1px solid transparent',
              } as React.CSSProperties}>{t}</button>
            ))}
          </div>
        </div>
        {/* Center: clock + market */}
        <div style={{ display:'flex', alignItems:'center', gap:12 }}>
          <span className="hdr-clock" style={{ fontSize:11.5, color:'rgba(255,255,255,0.38)', fontFamily:'monospace' }}>{clock}</span>
          <div style={{ display:'flex', alignItems:'center', gap:5, padding:'3px 9px', borderRadius:16,
            border:`1px solid ${isOpen ? 'rgba(34,197,94,0.28)' : 'rgba(107,114,128,0.25)'}`,
            background: isOpen ? 'rgba(34,197,94,0.06)' : 'rgba(107,114,128,0.06)' }}>
            <div style={{ width:5, height:5, borderRadius:'50%', background: isOpen ? BULL : '#6b7280',
              animation: isOpen ? 'glow 2s ease-in-out infinite' : 'none' }} />
            <span style={{ fontSize:10.5, color: isOpen ? BULL : '#9ca3af', fontFamily:'monospace', fontWeight:600, letterSpacing:'0.06em' }}>
              {isOpen ? 'OPEN' : (marketStatus || 'CLOSED').toUpperCase()}
            </span>
          </div>
        </div>
        {/* Right: evidence + voice + eng */}
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <button onClick={() => setLeftOpen(!leftOpen)} style={{ padding:'4px 9px', borderRadius:5,
            border:`1px solid ${leftOpen ? 'rgba(59,130,246,0.30)' : 'rgba(255,255,255,0.08)'}`,
            background: leftOpen ? 'rgba(59,130,246,0.10)' : 'transparent',
            color: leftOpen ? '#93c5fd' : 'rgba(255,255,255,0.30)', cursor:'pointer', fontSize:11,
            fontFamily:'monospace', transition:'all 0.15s' }}>
            {leftOpen ? '◀ Levels' : '▶ Levels'}
          </button>
          <button onClick={() => { unlockAudio(); setMuted(!muted); }} style={{ background:'none', border:'none', cursor:'pointer',
            fontSize:15, color: muted ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.45)', padding:'3px' }}>
            {muted ? '🔇' : '🔊'}
          </button>
          {/* LIVE / DEMO mode toggle */}
          <button
            title={demoMode ? 'Demo Mode active — click for Live Mode' : 'Live Mode — click for Demo Mode'}
            onClick={() => setDemoMode(v => {
              const next = !v;
              try { localStorage.setItem('atp_demo', next ? '1' : '0'); } catch {}
              if (!next) { setLoading(true); setData(null); }
              return next;
            })}
            style={{
              padding:'3px 10px', borderRadius:12, cursor:'pointer', fontSize:9.5,
              fontWeight:700, fontFamily:'monospace', letterSpacing:'0.10em',
              background:   demoMode ? 'rgba(245,158,11,0.14)' : 'transparent',
              color:        demoMode ? '#f59e0b' : 'rgba(255,255,255,0.20)',
              border:       demoMode ? '1px solid rgba(245,158,11,0.38)' : '1px solid rgba(255,255,255,0.07)',
              transition:   'all 0.20s',
              boxShadow:    demoMode ? '0 0 10px rgba(245,158,11,0.18)' : 'none',
            }}>
            {demoMode ? '◈ DEMO' : '◈ LIVE'}
          </button>
          <a className="hdr-eng" href="/api/dashboard" style={{ fontSize:10.5, color:'rgba(255,255,255,0.15)', textDecoration:'none', fontFamily:'monospace' }}
            onMouseEnter={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.45)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.15)')}>ENG ↗</a>
        </div>
      </header>

      {/* ── BODY ──────────────────────────────────────────────────────────── */}
      <div style={{ flex:1, display:'flex', overflow:'hidden' }}>

        {/* Left drawer: key levels + market context */}
        {leftOpen && (
          <div className="sidebar-panel sidebar-l" style={{ width:220, flexShrink:0, borderRight:'1px solid rgba(255,255,255,0.038)',
            overflowY:'auto', padding:'14px 12px', boxSizing:'border-box', background:'rgba(0,0,0,0.25)' }}>
            {/* Market context */}
            <div style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.25)', letterSpacing:'0.10em', textTransform:'uppercase', marginBottom:8 }}>Market Context</div>
            {(() => {
              const sig = (data?.main_brain || {}).signals || {};
              const gd  = data?.gate_debug || {};
              const ad  = data?.alert_diagnostics || {};
              const trend = sig.bias ? (String(sig.bias).toLowerCase().includes('bull') ? 'BULLISH' : String(sig.bias).toLowerCase().includes('bear') ? 'BEARISH' : 'NEUTRAL') : '—';
              const struct = gd.structure_confirmed === true ? 'BULLISH' : gd.structure_confirmed === false ? 'WEAK' : '—';
              const momentum = sig.cvd && sig.cvd !== 'unknown' ? String(sig.cvd).toUpperCase() : '—';
              const vol = String(data?.vol_regime || ad.volatility || '—').toUpperCase();
              const volume = ad.volume ? String(ad.volume).toUpperCase() : '—';
              return [['Trend', trend, /BULL/.test(trend) ? BULL : /BEAR/.test(trend) ? BEAR : MUTED],
                      ['Structure', struct, /BULL/.test(struct) ? BULL : /WEAK/.test(struct) ? BEAR : MUTED],
                      ['Momentum', momentum, /BULL|POS/.test(momentum) ? BULL : /BEAR|NEG/.test(momentum) ? BEAR : MUTED],
                      ['Volatility', vol, /ELEV|HIGH/.test(vol) ? AMB : MUTED],
                      ['Volume', volume, /INC|STRONG|HIGH/.test(volume) ? BULL : MUTED]].map(([l,v,c]) => (
                <div key={l} style={{ display:'flex', justifyContent:'space-between', padding:'4px 0', borderBottom:'1px solid rgba(255,255,255,0.025)' }}>
                  <span style={{ fontSize:10.5, color:'rgba(255,255,255,0.32)', fontFamily:'monospace' }}>{l}</span>
                  <span style={{ fontSize:11, color:c as string, fontFamily:'monospace', fontWeight:600 }}>{v}</span>
                </div>
              ));
            })()}

            <div style={{ borderTop:'1px solid rgba(255,255,255,0.038)', margin:'14px 0 8px' }} />
            <div style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.25)', letterSpacing:'0.10em', textTransform:'uppercase', marginBottom:8 }}>Key Levels</div>
            {[['VWAP', data?.vwap_value != null ? fmt(data.vwap_value) : '—', '#60a5fa'],
              ['Supply', data?.nearest_supply != null ? fmt(data.nearest_supply) : '—', BEAR],
              ['Price', price > 0 ? fmt(price) : '—', 'rgba(255,255,255,0.85)'],
              ['Demand', data?.nearest_demand != null ? fmt(data.nearest_demand) : '—', BULL],
              ['Entry', tp.entry != null ? fmt(tp.entry) : '—', AMB]].map(([l,v,c]) => (
              <div key={l} style={{ display:'flex', justifyContent:'space-between', padding:'4px 0', borderBottom:'1px solid rgba(255,255,255,0.025)' }}>
                <span style={{ fontSize:10.5, color:'rgba(255,255,255,0.32)', fontFamily:'monospace' }}>{l}</span>
                <span style={{ fontSize:11, color:c as string, fontFamily:'monospace', fontWeight:600 }}>{v}</span>
              </div>
            ))}

            {isManaging && (
              <>
                <div style={{ borderTop:'1px solid rgba(255,255,255,0.038)', margin:'14px 0 8px' }} />
                <div style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.25)', letterSpacing:'0.10em', textTransform:'uppercase', marginBottom:8 }}>Position</div>
                {(() => {
                  const at = data?.active_trade || data?.managing_trade || {};
                  return [['Dir', String(at.direction||'—').toUpperCase(), dirClr(at.direction)],
                          ['Contracts', String(at.contracts??'—'), 'rgba(255,255,255,0.7)'],
                          ['Entry', at.entry_price != null ? fmt(at.entry_price) : '—', AMB],
                          ['P&L', at.unrealized_pnl != null ? (at.unrealized_pnl>=0?'+':'')+'$'+fmt(at.unrealized_pnl) : '—', at.unrealized_pnl != null && at.unrealized_pnl > 0 ? BULL : BEAR]].map(([l,v,c]) => (
                    <div key={l} style={{ display:'flex', justifyContent:'space-between', padding:'4px 0', borderBottom:'1px solid rgba(255,255,255,0.025)' }}>
                      <span style={{ fontSize:10.5, color:'rgba(255,255,255,0.32)', fontFamily:'monospace' }}>{l}</span>
                      <span style={{ fontSize:11, color:c as string, fontFamily:'monospace', fontWeight:600 }}>{v}</span>
                    </div>
                  ));
                })()}
              </>
            )}

            {/* Recent setups */}
            {(() => {
              const trades: any[] = (data?.recent_trades ?? data?.by_instrument_today ?? []) as any[];
              if (!trades.length) return null;
              return (
                <>
                  <div style={{ borderTop:'1px solid rgba(255,255,255,0.038)', margin:'14px 0 8px' }} />
                  <div style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.25)', letterSpacing:'0.10em', textTransform:'uppercase', marginBottom:8 }}>Today&apos;s Setups</div>
                  {trades.slice(0,3).map((t:any,i:number) => {
                    const win = t.outcome==='win'||t.result==='WIN'||(t.r_multiple!=null&&Number(t.r_multiple)>0);
                    const dir = t.direction??t.side??'—';
                    return (
                      <div key={i} style={{ display:'flex', justifyContent:'space-between', padding:'4px 0', borderBottom:'1px solid rgba(255,255,255,0.025)' }}>
                        <span style={{ fontSize:10, color:MUTED, fontFamily:'monospace' }}>{String(t.time??t.opened_at??'—').slice(0,5)}</span>
                        <span style={{ fontSize:10.5, color:/long|bull/i.test(dir)?BULL:BEAR, fontWeight:700, fontFamily:'monospace' }}>{String(dir).toUpperCase()}</span>
                        <span style={{ fontSize:10.5, color:win?BULL:BEAR, fontWeight:700, fontFamily:'monospace' }}>{win?'W':'L'}</span>
                      </div>
                    );
                  })}
                </>
              );
            })()}

            {/* Voice */}
            <div style={{ borderTop:'1px solid rgba(255,255,255,0.038)', margin:'14px 0 8px' }} />
            <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
              <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:2 }}>
                {[3,6,10,7,13,8,14,9,12].map((h,i) => (
                  <div key={i} style={{ width:3, height:h, borderRadius:2,
                    background: !muted && speaking ? eyeColor : 'rgba(255,255,255,0.10)',
                    flexShrink:0,
                    animation: !muted && speaking ? `wv ${0.5+(i%4)*0.15}s ease-in-out ${i*0.05}s infinite alternate` : 'none' }} />
                ))}
                <span style={{ fontSize:10, color:muted?MUTED:'rgba(255,255,255,0.45)', fontFamily:'monospace', marginLeft:2 }}>
                  {muted?'Muted':speaking?'Speaking':'Listening'}
                </span>
              </div>
              {!muted && voices.length > 0 && (
                <div style={{ display:'flex', gap:4, alignItems:'center' }}>
                  <select value={voiceName||voices[0]?.name||''} onChange={e => setVoice(e.target.value)} style={{
                    flex:1, background:'rgba(0,0,0,0.35)', border:'1px solid rgba(255,255,255,0.07)',
                    borderRadius:6, padding:'3px 6px', color:'rgba(255,255,255,0.45)', fontSize:9.5,
                    fontFamily:'monospace', cursor:'pointer', outline:'none' }}>
                    {(() => {
                      // Dialect groups — English only, sorted by region
                      const DIALECT: Record<string, string> = {
                        'en-US':'🇺🇸 American', 'en-GB':'🇬🇧 British',
                        'en-AU':'🇦🇺 Australian', 'en-IN':'🇮🇳 Indian',
                        'en-CA':'🇨🇦 Canadian', 'en-NZ':'🇳🇿 New Zealand',
                        'en-ZA':'🇿🇦 South African', 'en-IE':'🇮🇪 Irish',
                        'en-NG':'🇳🇬 Nigerian', 'en-SG':'🇸🇬 Singaporean',
                      };
                      const DIALECT_ORDER = Object.keys(DIALECT);

                      // Heuristic gender from voice name
                      const isMale = (n: string) => /\b(david|james|daniel|thomas|mark|george|arthur|oliver|ryan|aaron|fred|bruce|paul|rishi|lee|alex\b|mike|john|kevin|brian|eric|reed|guy|guy|rod|lance|junior|ralph|zarvox|ralph|eddy|whisper|organ|deranged|boing|boowomp|bells|bad news|cellos|hysterical|pipe|trinoids)\b/i.test(n) || /\bmale\b/i.test(n);
                      const isFemale = (n: string) => /\b(samantha|victoria|karen|zira|hazel|fiona|veena|monica|allison|ava|lisa|susan|sarah|kate|tessa|nicky|moira|amelie|anna|laura|petra|lekha|mariam|milena|damayanti|luciana|joana|carmit|satu|kanya|nuray|ioana|ellen|alice|marie|emma|emily|claire|grace|diane|heather|joanna|nora|siri|aria|cortana)\b/i.test(n) || /\bfemale\b/i.test(n);
                      const gender = (n: string) => isMale(n) ? '♂ ' : isFemale(n) ? '♀ ' : '';

                      // Strip manufacturer prefix for cleaner display
                      const clean = (n: string) => n.replace(/^(Microsoft|Google|Apple)\s+/i, '');

                      // Bucket voices into dialect groups
                      const enVoices = voices.filter(v => v.lang.startsWith('en'));
                      const grouped: Record<string, typeof voices> = {};
                      enVoices.forEach(v => {
                        const key = DIALECT_ORDER.find(k => v.lang === k) ?? 'en-other';
                        (grouped[key] ??= []).push(v);
                      });

                      // Render dialect optgroups in order
                      const rendered = DIALECT_ORDER
                        .filter(k => grouped[k]?.length)
                        .map(k => (
                          <optgroup key={k} label={DIALECT[k]} style={{ background:'#111' }}>
                            {grouped[k].map(v => (
                              <option key={v.name} value={v.name} style={{ background:'#111' }}>
                                {gender(v.name)}{clean(v.name)}
                              </option>
                            ))}
                          </optgroup>
                        ));

                      // Catch-all for any English dialect not in the map
                      if (grouped['en-other']?.length) {
                        rendered.push(
                          <optgroup key="en-other" label="🌐 Other English" style={{ background:'#111' }}>
                            {grouped['en-other'].map(v => (
                              <option key={v.name} value={v.name} style={{ background:'#111' }}>
                                {gender(v.name)}{clean(v.name)} ({v.lang})
                              </option>
                            ))}
                          </optgroup>
                        );
                      }

                      return rendered.length > 0 ? rendered : (
                        <option value="" style={{ background:'#111' }}>No voices available</option>
                      );
                    })()}
                  </select>
                  <button title="Preview voice" onClick={() => {
                    const ss = window.speechSynthesis; if (!ss) return;
                    ss.cancel();
                    const utt = new SpeechSynthesisUtterance("Hey, I'm your AI trading partner. Looking good out there.");
                    const v = voices.find(x => x.name === (voiceName||voices[0]?.name||''));
                    if (v) utt.voice = v;
                    ss.speak(utt);
                  }} style={{
                    flexShrink:0, padding:'3px 7px', borderRadius:6, fontSize:10,
                    background:'rgba(0,148,255,0.12)', border:'1px solid rgba(0,148,255,0.22)',
                    color:'rgba(0,200,255,0.75)', cursor:'pointer', fontFamily:'monospace' }}>▶</button>
                </div>
              )}
            </div>

            {/* ── Avatar switcher ── */}
            <div style={{ borderTop:'1px solid rgba(255,255,255,0.038)', margin:'12px 0 8px' }} />
            <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
              <button onClick={() => setShowAvatarPicker(p => !p)} style={{
                display:'flex', alignItems:'center', gap:6, width:'100%',
                background:'rgba(0,0,0,0.25)', border:'1px solid rgba(255,255,255,0.07)',
                borderRadius:6, padding:'4px 8px', cursor:'pointer', textAlign:'left' }}>
                <span style={{ fontSize:12 }}>🎭</span>
                <span style={{ fontSize:9.5, color:'rgba(255,255,255,0.38)', fontFamily:'monospace', flex:1 }}>Avatar</span>
                <span style={{ fontSize:8.5, color:'rgba(255,255,255,0.22)', fontFamily:'monospace' }}>
                  {({'/LordPiggington.vrm':'LordPiggington','/Orion.vrm':'Orion','/Bizdude.vrm':'Bizdude','/Bruno.vrm':'Bruno','/Steamboat.vrm':'Steamboat','/avatar.vrm':'Default'} as Record<string,string>)[vrmSrc] ?? 'Custom'}
                </span>
                <span style={{ fontSize:10, color:'rgba(255,255,255,0.25)' }}>{showAvatarPicker ? '▲' : '▼'}</span>
              </button>
              {showAvatarPicker && (
                <div style={{ display:'flex', flexDirection:'column', gap:5,
                  background:'rgba(0,0,0,0.40)', border:'1px solid rgba(255,255,255,0.07)',
                  borderRadius:8, padding:'8px 10px' }}>
                  {/* Presets */}
                  <div style={{ fontSize:9, color:'rgba(255,255,255,0.28)', fontFamily:'monospace', marginBottom:2, letterSpacing:'0.08em' }}>PRESETS</div>
                  {[
                    { label: 'LordPiggington', src: '/LordPiggington.vrm' },
                    { label: 'Orion',          src: '/Orion.vrm' },
                    { label: 'Bizdude',        src: '/Bizdude.vrm' },
                    { label: 'Bruno',          src: '/Bruno.vrm' },
                    { label: 'Steamboat',      src: '/Steamboat.vrm' },
                    { label: 'Default VRM',    src: '/avatar.vrm' },
                  ].map(p => (
                    <button key={p.src} onClick={() => setVrmSrc(p.src)} style={{
                      padding:'4px 8px', borderRadius:5, fontSize:10, cursor:'pointer', textAlign:'left',
                      background: vrmSrc === p.src ? 'rgba(0,148,255,0.18)' : 'rgba(255,255,255,0.04)',
                      border: `1px solid ${vrmSrc === p.src ? 'rgba(0,148,255,0.35)' : 'rgba(255,255,255,0.06)'}`,
                      color: vrmSrc === p.src ? 'rgba(0,200,255,0.9)' : 'rgba(255,255,255,0.45)',
                      fontFamily:'monospace' }}>
                      {p.label}
                    </button>
                  ))}
                  {/* Custom URL */}
                  <div style={{ fontSize:9, color:'rgba(255,255,255,0.28)', fontFamily:'monospace', marginTop:4, marginBottom:2, letterSpacing:'0.08em' }}>CUSTOM URL (.vrm)</div>
                  <div style={{ display:'flex', gap:4 }}>
                    <input value={vrmUrlInput} onChange={e => setVrmUrlInput(e.target.value)}
                      placeholder="https://example.com/model.vrm"
                      style={{ flex:1, background:'rgba(0,0,0,0.35)', border:'1px solid rgba(255,255,255,0.08)',
                        borderRadius:5, padding:'3px 6px', color:'rgba(255,255,255,0.6)', fontSize:9.5,
                        fontFamily:'monospace', outline:'none' }} />
                    <button onClick={() => { if (vrmUrlInput.trim()) setVrmSrc(vrmUrlInput.trim()); }}
                      style={{ padding:'3px 8px', borderRadius:5, fontSize:10, cursor:'pointer',
                        background:'rgba(0,148,255,0.15)', border:'1px solid rgba(0,148,255,0.25)',
                        color:'rgba(0,200,255,0.8)', fontFamily:'monospace' }}>Load</button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── MAIN CENTER ──────────────────────────────────────────────────── */}
        <div className="main-center" style={{ flex:1, overflowY:'auto', overflowX:'hidden', padding:'24px 28px 24px', display:'flex', flexDirection:'column', gap:0, minWidth:0 }}>

          {/* ── SESSION BRIEFING — shows once per calendar day ──────────── */}
          {showBriefing && (
            <div style={{ marginBottom:16, borderRadius:10, overflow:'hidden', flexShrink:0,
              border:'1px solid rgba(59,130,246,0.22)', animation:'bUp 0.28s ease-out',
              background:'rgba(5,10,24,0.94)' }}>
              {/* Header */}
              <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
                padding:'8px 14px', background:'rgba(59,130,246,0.08)',
                borderBottom:'1px solid rgba(59,130,246,0.12)' }}>
                <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                  <span style={{ fontSize:13 }}>🧠</span>
                  <span style={{ fontSize:8.5, fontFamily:'monospace', color:'rgba(99,179,237,0.75)',
                    letterSpacing:'0.12em', textTransform:'uppercase', fontWeight:700 }}>AI Session Briefing</span>
                  <span style={{ fontSize:8, fontFamily:'monospace', color:'rgba(255,255,255,0.25)', letterSpacing:'0.06em' }}>
                    {new Date().toLocaleDateString('en-US', { weekday:'long', month:'short', day:'numeric' })}
                  </span>
                </div>
                <button onClick={dismissBriefing} style={{ background:'none', border:'none',
                  color:'rgba(255,255,255,0.30)', cursor:'pointer', fontSize:16, padding:'0 2px', lineHeight:1 }}>×</button>
              </div>
              {/* Stats grid */}
              <div style={{ display:'flex', borderBottom:'1px solid rgba(255,255,255,0.045)' }}>
                {[
                  {
                    label: 'Yesterday',
                    value: tm.yesterday.total > 0 ? (tm.yesterday.wins + 'W ' + tm.yesterday.losses + 'L') : mem.yest ? (Math.round(mem.yest.pe) + '/110') : '\u2014',
                    sub:   tm.yesterday.wr !== null ? (tm.yesterday.wr + '% win rate') : 'No recorded trades',
                  },
                  {
                    label: 'This Week',
                    value: tm.week.total > 0 ? (tm.week.wins + 'W ' + tm.week.losses + 'L') : mem.active > 0 ? (mem.active + ' sessions') : '\u2014',
                    sub:   tm.week.wr !== null ? (tm.week.wr + '% win rate') : 'Building history',
                  },
                  {
                    label: 'Avg R:R',
                    value: tm.week.avgRR ? (tm.week.avgRR.toFixed(1) + 'R') : '\u2014',
                    sub:   tm.week.avgRR ? (tm.week.avgRR >= 1.5 ? 'Above target' : tm.week.avgRR >= 1.0 ? 'On target' : 'Below target') : 'No closed trades',
                  },
                  {
                    label: 'Best Setup',
                    value: tm.bestSetup ? tm.bestSetup.name.replace(/\b\w/g, (c:string) => c.toUpperCase()).slice(0, 16) : '\u2014',
                    sub:   tm.bestSetup ? (Math.round(tm.bestSetup.wr * 100) + '%  \u00b7  ' + tm.bestSetup.total + ' trade' + (tm.bestSetup.total !== 1 ? 's' : '')) : 'Accumulating data',
                  },
                ].map((item, i) => (
                  <div key={i} style={{ flex:1, padding:'9px 12px', borderRight: i < 3 ? '1px solid rgba(255,255,255,0.04)' : 'none' }}>
                    <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.11em', color:'rgba(255,255,255,0.25)', textTransform:'uppercase', marginBottom:4 }}>{item.label}</div>
                    <div style={{ fontSize:13, fontFamily:'monospace', fontWeight:800, color:'rgba(255,255,255,0.85)', lineHeight:1 }}>{item.value}</div>
                    <div style={{ fontSize:9, fontFamily:'monospace', color:'rgba(255,255,255,0.35)', marginTop:3 }}>{item.sub}</div>
                  </div>
                ))}
              </div>
              {/* Today's objectives */}
              {objectives.length > 0 && (
                <div style={{ padding:'10px 14px' }}>
                  <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.13em', color:'rgba(99,179,237,0.45)', textTransform:'uppercase', marginBottom:7 }}>
                    Today's Objectives
                  </div>
                  <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
                    {objectives.map((obj, i) => (
                      <div key={i} style={{ display:'flex', gap:8, alignItems:'flex-start' }}>
                        <span style={{ fontSize:8.5, color:'rgba(99,179,237,0.50)', fontFamily:'monospace', fontWeight:700, flexShrink:0, marginTop:1 }}>{i + 1}.</span>
                        <span style={{ fontSize:11, color:'rgba(255,255,255,0.62)', fontFamily:'monospace', lineHeight:1.45 }}>{obj}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── MAIN BRAIN COMMAND CENTER ───────────────────────────────── */}
          <div className="mb-row" style={{ display:'flex', gap:20, marginBottom:20,
            position:'relative',
            background:`radial-gradient(ellipse 820px 660px at 38% 48%, ${auraColor}0e 0%, transparent 68%)` }}>

            {/* ── MISSION CONTROL STAGE — avatar centered, 12 live telemetry cards ── */}
            <div className="mc-stage">
              <ConnectorSVG sigs={connSigs} flashIds={flashIds} />

              {/* TOP ROW — Edge Score · Win Probability · Strategy */}
              <div className="mc-top-row">
                {(() => {
                  const edgeCol = edge >= 75 ? BULL : edge >= 55 ? '#f97316' : edge >= 30 ? AMB : MUTED;
                  const eDot: EvStrength = edge >= 75 ? 'confirmed' : edge >= 55 ? 'developing' : edge >= 30 ? 'neutral' : 'inactive';
                  return <McCard delay={0} label="Edge Score" col={edgeCol} dot={eDot}
                    value={`${Math.round(edge)}/110`} sub={grade || 'WAIT'} />;
                })()}
                {(() => {
                  const ep  = Number(data?.entry_probability ?? data?.analyst?.entry_probability ?? 0);
                  const col = ep >= 65 ? BULL : ep >= 45 ? AMB : MUTED;
                  const dot: EvStrength = ep >= 65 ? 'confirmed' : ep >= 45 ? 'developing' : 'inactive';
                  return <McCard delay={0.4} label="Win Probability" col={col} dot={dot}
                    value={ep > 0 ? `${Math.round(ep)}%` : '—'} sub="setup quality signal" />;
                })()}
                {(() => {
                  const raw   = String(data?.active_strategy || data?.strategy_mode || sig.strategy || '');
                  const strat = raw.replace(/_/g,' ').toUpperCase() || '—';
                  const mode  = String(data?.trading_mode || '').toUpperCase();
                  return <McCard delay={0.8} label="Strategy" col="rgba(255,255,255,0.72)" dot="neutral"
                    value={strat.slice(0,14) || '—'} sub={mode || undefined} />;
                })()}
              </div>

              {/* MID ROW — left data col | avatar spotlight | right data col */}
              <div className="mc-mid-row">

                {/* LEFT — Bias · Structure · Liquidity */}
                <div className="mc-col">
                  {(() => {
                    const b   = String(sig.bias || '').toLowerCase();
                    const col = /bull/.test(b) ? BULL : /bear/.test(b) ? BEAR : MUTED;
                    const lbl = /bull/.test(b) ? 'BULLISH' : /bear/.test(b) ? 'BEARISH' : 'NEUTRAL';
                    const dot: EvStrength = /bull/.test(b) ? 'confirmed' : /bear/.test(b) ? 'invalidated' : 'inactive';
                    const dirFav = String(sig.favored_direction || '').toUpperCase();
                    return <McCard delay={1.2} label="Bias" col={col} dot={dot}
                      value={lbl} sub={dirFav ? `Favoring ${dirFav}` : undefined} />;
                  })()}
                  {(() => {
                    const sc   = !!gd.structure_confirmed;
                    const zv   = !!gd.zone_valid;
                    const col  = sc ? BULL : MUTED;
                    const dot: EvStrength = sc ? 'confirmed' : 'inactive';
                    const stype = String(gd.structure_type || '').toUpperCase() || (sc ? 'BOS/CHOCH' : 'NONE');
                    return <McCard delay={1.6} label="Market Structure" col={col} dot={dot}
                      value={stype} sub={zv ? 'Zone active' : 'No zone'} />;
                  })()}
                  {(() => {
                    const zv  = !!gd.zone_valid;
                    const dem = data?.nearest_demand;
                    const sup = data?.nearest_supply;
                    const col = zv ? '#f97316' : (dem || sup) ? AMB : MUTED;
                    const dot: EvStrength = zv ? 'confirmed' : (dem || sup) ? 'neutral' : 'inactive';
                    const lbl = zv ? 'Zone Active' : (dem || sup) ? 'Nearby' : 'No Zone';
                    return <McCard delay={2.0} label="Liquidity" col={col} dot={dot}
                      value={lbl} sub={dem ? `D: ${fmt(Number(dem))}` : sup ? `S: ${fmt(Number(sup))}` : undefined} />;
                  })()}
                </div>

                {/* Signal radar — flanks the avatar on both sides */}
                <EvidenceRadarPanel items={radar.slice(0, 5)} side="left" />

                {/* AVATAR SPOTLIGHT CENTER */}
                <div className="mc-avtr-outer" style={{ display:'flex', flexDirection:'column', alignItems:'flex-start', justifyContent:'flex-start', flexShrink:0, overflow:'hidden' }}>
                  <div className="mc-avtr-box" style={{ position:'relative', width:420, height:560, flexShrink:0 }}>
                    {/* Far-field halo — contained within avtr-outer, no bleed into adjacent cards */}
                    <div style={{ position:'absolute', top:0, left:0, right:0, bottom:0,
                      background:`radial-gradient(ellipse at 50% 46%, ${auraColor}24 0%, ${auraColor}09 36%, transparent 66%)`,
                      pointerEvents:'none', zIndex:0 }} />
                    {/* Breathing mid-field pulse */}
                    <div style={{ position:'absolute', inset:0,
                      background:`radial-gradient(ellipse at 50% 46%, ${auraColor}3e 0%, transparent 58%)`,
                      animation:'avrPulse 3s ease-in-out infinite', pointerEvents:'none', zIndex:0 }} />
                    {/* Always-on close glow — keeps face bright against dim cards */}
                    <div style={{ position:'absolute', top:'18%', left:'14%', right:'14%', bottom:'22%',
                      background:`radial-gradient(ellipse at 50% 44%, ${auraColor}1e 0%, transparent 52%)`,
                      pointerEvents:'none', zIndex:0 }} />
                    {/* Floor reflection */}
                    <div style={{ position:'absolute', bottom:-36, left:'5%', right:'5%', height:82,
                      background:`radial-gradient(ellipse at 50% 100%, ${auraColor}2c 0%, transparent 66%)`,
                      pointerEvents:'none', zIndex:0 }} />
                    <div style={{ position:'absolute', inset:0, display:'flex', alignItems:'center',
                      justifyContent:'center', zIndex:1 }}>
                      <LordPiggingtonAvatar avState={avState} speaking={speaking} ringColor={ringColor} gazeEvent={gazeEvent} speechCtrlRef={speechCtrlRef} voiceListeningRef={voiceListeningRef} debug={false} vrmSrc={vrmSrc} />
                    </div>

                    {/* Orbital particle aura — dots + rings that shift red→yellow→green */}
                    <AvatarAura avState={avState} edge={edge} speaking={speaking} />

                    {/* ── CORNER INTELLIGENCE PANELS ─────────────────────── */}

                    {/* TOP-LEFT: Nearest Support / Demand Zone */}
                    {(() => {
                      const dem = Number(data?.nearest_demand || 0);
                      const px  = Number(data?.price || 0);
                      const pct = dem > 0 && px > 0 ? ((px - dem) / px * 100).toFixed(1) : null;
                      return dem > 0 ? (
                        <div className="avtr-corner-sat" style={{ position:'absolute', top:14, left:4, zIndex:2, pointerEvents:'none' }}>
                          <CornerSat label="Support" col={BULL} value={fmt(dem)}
                            sub={pct ? `${pct}% below price` : 'Demand zone'} />
                        </div>
                      ) : null;
                    })()}

                    {/* TOP-RIGHT: Nearest Resistance / Supply Zone */}
                    {(() => {
                      const sup = Number(data?.nearest_supply || 0);
                      const px  = Number(data?.price || 0);
                      const pct = sup > 0 && px > 0 ? ((sup - px) / px * 100).toFixed(1) : null;
                      return sup > 0 ? (
                        <div className="avtr-corner-sat" style={{ position:'absolute', top:14, right:4, zIndex:2, pointerEvents:'none' }}>
                          <CornerSat label="Resistance" col={BEAR} align="right" value={fmt(sup)}
                            sub={pct ? `${pct}% above price` : 'Supply zone'} />
                        </div>
                      ) : null;
                    })()}

                    {/* BOTTOM-LEFT: Next Economic Event */}
                    {(() => {
                      const nf     = data?.news_filter;
                      const evt    = nf?.next_event;
                      const mins   = Number(evt?.mins ?? 0);
                      const title  = String(evt?.title || evt?.name || '').slice(0, 17) || null;
                      const impact = String(evt?.impact || '').toLowerCase();
                      const ctd    = mins > 0
                        ? (mins < 60 ? `in ${mins}m` : `in ${Math.floor(mins / 60)}h ${mins % 60}m`)
                        : evt ? 'Imminent' : '';
                      const evtCol = /high/.test(impact) ? BEAR : /medium/.test(impact) ? AMB : 'rgba(255,255,255,0.60)';
                      return (
                        <div className="avtr-corner-sat" style={{ position:'absolute', bottom:14, left:4, zIndex:2, pointerEvents:'none' }}>
                          <CornerSat label="Next Event"
                            col={title ? evtCol : 'rgba(255,255,255,0.32)'}
                            value={title || 'No Events'}
                            sub={ctd || 'Calendar clear'} />
                        </div>
                      );
                    })()}

                    {/* BOTTOM-RIGHT: Today's Trade Performance */}
                    {(() => {
                      const todayStr = new Date().toISOString().slice(0, 10);
                      const all: any[] = Array.isArray(data?.recent_trades) ? data.recent_trades : [];
                      const today = all.filter((t: any) => String(t?.opened_at || '').slice(0, 10) === todayStr);
                      const wins  = today.filter((t: any) => /win|profit|target/i.test(String(t?.outcome || ''))).length;
                      const loss  = today.filter((t: any) => /loss|stop|miss/i.test(String(t?.outcome || ''))).length;
                      const total = wins + loss;
                      const wr    = total > 0 ? Math.round(wins / total * 100) : null;
                      const col   = wins > loss ? BULL : loss > wins ? BEAR : 'rgba(255,255,255,0.68)';
                      return (
                        <div className="avtr-corner-sat" style={{ position:'absolute', bottom:14, right:4, zIndex:2, pointerEvents:'none' }}>
                          <CornerSat label="Today" align="right"
                            col={total > 0 ? col : 'rgba(255,255,255,0.32)'}
                            value={total > 0 ? `${wins}W  ${loss}L` : 'No Trades'}
                            sub={wr !== null ? `${wr}% win rate` : 'Session tracking'} />
                        </div>
                      );
                    })()}

                    {/* ── MOBILE OVERLAY — verdict · price · narration ── */}
                    <div className="mob-avtr-overlay" style={{
                      position:'absolute', bottom:0, left:0, right:0, zIndex:6,
                      background:'linear-gradient(transparent,rgba(4,6,14,0.96) 52%)',
                      padding:'52px 16px 24px', flexDirection:'column', alignItems:'center', gap:8,
                      pointerEvents:'none',
                    }}>
                      {/* ── Mobile mute / audio-unlock chip ──────────────── */}
                      <div style={{ pointerEvents:'auto', display:'flex', gap:6, alignItems:'center' }}>
                        <button onClick={() => {
                          unlockAudio();
                          setMuted(!muted);
                          if (muted) lastSpokenRef.current = ''; // re-speak next narration
                        }} style={{
                          background:'rgba(255,255,255,0.07)', border:'1px solid rgba(255,255,255,0.14)',
                          borderRadius:20, padding:'4px 14px', cursor:'pointer',
                          fontSize:13, color: muted ? 'rgba(255,255,255,0.30)' : 'rgba(255,255,255,0.70)',
                          fontFamily:'monospace',
                        }}>
                          {muted ? '🔇 Audio off' : '🔊 Audio on'}
                        </button>
                      </div>

                      <div style={{
                        background:`${auraColor}1c`, border:`1px solid ${auraColor}4c`,
                        borderRadius:22, padding:'6px 22px',
                        fontSize:17, fontWeight:800, color:auraColor,
                        fontFamily:'monospace', letterSpacing:'0.04em',
                        boxShadow:`0 0 20px ${auraColor}28`,
                      }}>
                        {status === 'READY'
                          ? (/long|bull/i.test(dirn) ? '▲ READY — LONG' : '▼ READY — SHORT')
                          : (status || 'WAIT')}
                      </div>
                      <div style={{ display:'flex', gap:10, alignItems:'center' }}>
                        <span style={{ fontSize:24, fontWeight:800, color:'rgba(255,255,255,0.92)', fontFamily:'monospace' }}>
                          {price > 0 ? fmt(price) : '—'}
                        </span>
                        <span style={{ fontSize:11, color:'rgba(255,255,255,0.32)', fontFamily:'monospace' }}>{ticker}</span>
                        <span style={{
                          fontSize:10, fontWeight:700, color:verdictColor,
                          background:`${verdictColor}18`, border:`1px solid ${verdictColor}30`,
                          borderRadius:8, padding:'2px 8px', fontFamily:'monospace',
                        }}>{Math.round(edge)}/110</span>
                      </div>
                      {displayNarration && (
                        <div style={{
                          fontSize:11.5, color:'rgba(255,255,255,0.45)', fontFamily:'monospace',
                          textAlign:'center', lineHeight:1.5, maxWidth:260,
                        }}>
                          {String(displayNarration).slice(0,88)}{String(displayNarration).length > 88 ? '…' : ''}
                        </div>
                      )}

                      {/* ── MOBILE ENTER BUTTON ───────────────────────── */}
                      <div style={{ pointerEvents:'auto', display:'flex', gap:8, alignItems:'center', marginTop:4 }}>
                        {tradeSent ? (
                          <div style={{
                            padding:'11px 28px', borderRadius:12,
                            background: tradeSent.startsWith('✓') ? 'rgba(34,197,94,0.20)' : 'rgba(239,68,68,0.20)',
                            color:      tradeSent.startsWith('✓') ? BULL : BEAR,
                            border:    `1px solid ${tradeSent.startsWith('✓') ? BULL+'55' : BEAR+'55'}`,
                            fontSize:14, fontWeight:800, fontFamily:'monospace', letterSpacing:'0.04em',
                          }}>
                            {tradeSent}
                          </div>
                        ) : (
                          <>
                            <button onClick={doEnter} style={{
                              padding:'12px 32px', borderRadius:12, border:'none',
                              cursor: isActionable || confirming ? 'pointer' : 'default',
                              background: confirming
                                ? 'rgba(239,68,68,0.28)'
                                : isActionable
                                ? `${verdictColor}2a`
                                : 'rgba(255,255,255,0.06)',
                              color: confirming ? '#ef4444' : isActionable ? verdictColor : 'rgba(255,255,255,0.22)',
                              fontSize:15, fontWeight:900, fontFamily:'monospace', letterSpacing:'0.05em',
                              boxShadow: confirming
                                ? '0 0 28px rgba(239,68,68,0.45)'
                                : isActionable
                                ? `0 0 28px ${verdictColor}50`
                                : 'none',
                              transition:'all 0.18s',
                            }}>
                              {confirming
                                ? '⚡ CONFIRM ENTRY'
                                : isActionable
                                ? (/short|bear/i.test(dirn) ? '▼ ENTER SHORT' : '▲ ENTER LONG')
                                : 'WAITING FOR SETUP'}
                            </button>
                            {confirming && (
                              <button onClick={() => setConfirming(false)} style={{
                                padding:'12px 18px', borderRadius:12,
                                border:'1px solid rgba(239,68,68,0.35)',
                                background:'rgba(239,68,68,0.06)',
                                color:BEAR, fontSize:13, fontFamily:'monospace', cursor:'pointer',
                              }}>
                                Cancel
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </div>

                  </div>
                  <div style={{ marginTop:4, display:'flex', alignItems:'center', gap:7 }}>
                    <div style={{ width:6, height:6, borderRadius:'50%', background:verdictColor,
                      boxShadow:`0 0 8px ${verdictColor}, 0 0 18px ${verdictColor}44` }} />
                    <span style={{ fontSize:10.5, fontFamily:'monospace', fontWeight:700, letterSpacing:'0.10em',
                      color:'rgba(255,255,255,0.38)', textTransform:'uppercase' }}>
                      {avState === 'ACTIVE'      ? 'MANAGING'      :
                       avState === 'READY_LONG'  ? 'LONG SETUP'    :
                       avState === 'READY_SHORT' ? 'SHORT SETUP'   :
                       avState === 'FORMING'     ? 'SETUP FORMING' :
                       avState === 'ANALYZING'   ? 'ANALYZING'     :
                       avState === 'STOP_HIT'    ? 'STOP HIT'      :
                       avState === 'TARGET_HIT'  ? 'TARGET HIT'    :
                       avState === 'NO_EDGE'     ? 'NO EDGE'       : 'WATCHING'}
                    </span>
                  </div>
                </div>
                <EvidenceRadarPanel items={radar.slice(5)} side="right" />

                {/* RIGHT — VWAP · Order Flow · Volume */}
                <div className="mc-col">
                  {(() => {
                    const vwapVal = Number(data?.vwap_value || 0);
                    const priceV  = Number(data?.price || 0);
                    const above   = priceV > 0 && vwapVal > 0 && priceV > vwapVal;
                    const col = vwapVal > 0 ? (above ? BULL : BEAR) : MUTED;
                    const dot: EvStrength = gd.vwap_confirmed ? 'confirmed' : vwapVal > 0 ? 'developing' : 'inactive';
                    return <McCard delay={2.4} label="VWAP" col={col} dot={dot}
                      value={vwapVal > 0 ? fmt(vwapVal) : '—'}
                      sub={vwapVal > 0 ? (above ? 'Price above' : 'Price below') : undefined} />;
                  })()}
                  {(() => {
                    const c   = String(sig.cvd || ad.cvd || '').toLowerCase();
                    const col = /bull|pos/.test(c) ? BULL : /bear|neg/.test(c) ? BEAR : MUTED;
                    const lbl = /bull|pos/.test(c) ? 'BULL DELTA' : /bear|neg/.test(c) ? 'BEAR DELTA' : 'NEUTRAL';
                    const dot: EvStrength = /bull|pos/.test(c) ? 'confirmed' : /bear|neg/.test(c) ? 'invalidated' : 'neutral';
                    const v = String(ad.volume || '').toLowerCase();
                    const volSub = /strong|high/.test(v) ? 'High vol' : /incr/.test(v) ? 'Rising vol' : /low|thin/.test(v) ? 'Low vol' : 'Normal vol';
                    return <McCard delay={2.8} label="Order Flow" col={col} dot={dot}
                      value={lbl} sub={volSub} />;
                  })()}
                  {(() => {
                    const v   = String(ad.volume || '').toLowerCase();
                    const col = /strong|high/.test(v) ? BULL : /incr/.test(v) ? AMB : /low|thin/.test(v) ? MUTED : 'rgba(255,255,255,0.55)';
                    const lbl = /strong|high/.test(v) ? 'HIGH' : /incr/.test(v) ? 'INCREASING' : /low|thin/.test(v) ? 'LOW' : 'NORMAL';
                    const dot: EvStrength = /strong|high/.test(v) ? 'confirmed' : /incr/.test(v) ? 'developing' : /low|thin/.test(v) ? 'inactive' : 'neutral';
                    const rvol = data?.rvol ? `RVOL ${Number(data.rvol).toFixed(1)}x` : undefined;
                    return <McCard delay={3.2} label="Volume" col={col} dot={dot}
                      value={lbl} sub={rvol} />;
                  })()}
                </div>

              </div>

              {/* BOTTOM ROW — Trade Plan · Volatility · Risk */}
              <div className="mc-bot-row">
                {(() => {
                  const has  = tp.entry && Number(tp.entry) > 0;
                  const col  = has ? AMB : MUTED;
                  const dot: EvStrength = has ? 'developing' : 'inactive';
                  const entryLbl = has ? `E: ${fmt(Number(tp.entry))}` : 'No plan';
                  const stopLbl  = tp.stop    ? `SL: ${fmt(Number(tp.stop))}`    : '';
                  const tgtLbl   = tp.target1 ? `T1: ${fmt(Number(tp.target1))}` : '';
                  return <McCard delay={3.6} label="Trade Plan" col={col} dot={dot}
                    value={entryLbl}
                    sub={[stopLbl, tgtLbl].filter(Boolean).join('  ') || undefined} />;
                })()}
                {(() => {
                  const vr  = String(ad.volatility_regime || data?.vol_regime || '').toLowerCase();
                  const col = /extreme/.test(vr) ? BEAR : /high|elev/.test(vr) ? '#f97316' : /low|quiet/.test(vr) ? MUTED : AMB;
                  const lbl = /extreme/.test(vr) ? 'EXTREME' : /high|elev/.test(vr) ? 'ELEVATED' : /low|quiet/.test(vr) ? 'QUIET' : isOpen ? 'NORMAL' : '—';
                  const dot: EvStrength = /extreme/.test(vr) ? 'invalidated' : /high|elev/.test(vr) ? 'developing' : /low|quiet/.test(vr) ? 'confirmed' : 'neutral';
                  const atr = data?.atr_pts ?? data?.atr;
                  return <McCard delay={4.0} label="Volatility" col={col} dot={dot}
                    value={lbl} sub={atr ? `ATR ${Number(atr).toFixed(1)} pts` : 'ATR regime'} />;
                })()}
                {(() => {
                  const hasTP   = tp.entry && tp.stop && Number(tp.entry) > 0;
                  const riskPts = hasTP ? Math.abs(Number(tp.entry) - Number(tp.stop)) : null;
                  const rrRaw   = data?.trade_plan?.rr_num ?? data?.rr_num ?? null;
                  const rr      = rrRaw ?? (tp.target1 && tp.entry && tp.stop
                    ? Math.abs(Number(tp.target1) - Number(tp.entry)) / (Math.abs(Number(tp.entry) - Number(tp.stop)) || 1)
                    : null);
                  const col  = hasTP ? AMB : MUTED;
                  const dot: EvStrength = hasTP ? 'developing' : 'inactive';
                  return <McCard delay={4.4} label="Risk" col={col} dot={dot}
                    value={riskPts ? `${fmt(riskPts, 1)} pts` : '—'}
                    sub={rr ? `1:${Number(rr).toFixed(1)} R:R` : 'No active plan'} />;
                })()}
              </div>

            </div>

            {/* Brain content */}
            <div className="mb-brain" style={{ flex:1, display:'flex', flexDirection:'column', gap:14, justifyContent:'flex-start', minWidth:0 }}>

              {/* BIG VERDICT */}
              <div>
                <div className="verdict-big" style={{ fontSize:38, fontWeight:900, lineHeight:1, color:verdictColor,
                  letterSpacing:'-0.02em', textShadow:`0 0 30px ${verdictColor}44` }}>
                  {verdictLabel}
                </div>
                <div className="verdict-sub" style={{ fontSize:20, fontWeight:700, color:'rgba(255,255,255,0.55)', marginTop:4, letterSpacing:'-0.01em' }}>
                  {ticker} <span style={{ color:'rgba(255,255,255,0.22)' }}>·</span> {price > 0 ? fmt(price, 2) : '—'}
                </div>
              </div>

              {/* Edge score */}
              <div className="edge-wrap" style={{ maxWidth:320 }}>
                <div style={{ display:'flex', justifyContent:'space-between', marginBottom:5 }}>
                  <span style={{ fontSize:10, fontFamily:'monospace', color:'rgba(255,255,255,0.28)', letterSpacing:'0.10em', textTransform:'uppercase' }}>Edge Score</span>
                  {grade && <span style={{ fontSize:10, fontFamily:'monospace', color:verdictColor, fontWeight:700, letterSpacing:'0.08em' }}>{grade}</span>}
                </div>
                <EdgeBar score={edge} max={110} color={verdictColor} />
              </div>

              {/* Live thought stream — replaces static narration */}
              <ThoughtStream stream={streamedThoughts} />

              {/* Wait reason */}
              {strictR && status === 'WAIT' && (
                <div className="wait-box" style={{ padding:'8px 12px', borderRadius:7, background:'rgba(245,158,11,0.07)',
                  border:'1px solid rgba(245,158,11,0.18)', fontSize:12, color:AMB, fontFamily:'monospace',
                  maxWidth:480 }}>
                  {strictR}
                </div>
              )}

              {/* Gate checklist chips */}
              {checklist.length > 0 && (
                <div style={{ display:'flex', flexWrap:'wrap', gap:6 }}>
                  {checklist.map((item, i) => {
                    const { icon, color } = item.st === 'pass' ? { icon:'✓', color:BULL } : item.st === 'fail' ? { icon:'✕', color:BEAR } : item.st === 'wait' ? { icon:'○', color:AMB } : { icon:'~', color:'rgba(255,255,255,0.35)' };
                    return (
                      <div key={i} style={{ display:'flex', alignItems:'center', gap:5, padding:'4px 10px',
                        borderRadius:16, border:`1px solid ${color}33`, background:`${color}0a`,
                        fontSize:11.5, fontFamily:'monospace' }}>
                        <span style={{ color, fontWeight:700 }}>{icon}</span>
                        <span style={{ color:'rgba(255,255,255,0.55)' }}>{item.text}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Action buttons */}
              <div style={{ display:'flex', gap:8, flexWrap:'wrap' }}>
                {tradeSent ? (
                  <div style={{ padding:'10px 18px', borderRadius:8, fontSize:13, fontFamily:'monospace',
                    background: tradeSent.startsWith('✓') ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
                    color: tradeSent.startsWith('✓') ? BULL : BEAR, border:`1px solid ${tradeSent.startsWith('✓') ? BULL+'33' : BEAR+'33'}` }}>
                    {tradeSent}
                  </div>
                ) : (
                  <button className="action-btn" onClick={doEnter} style={{
                    padding:'10px 22px', borderRadius:8, border:'none', cursor:'pointer',
                    background: confirming ? 'rgba(239,68,68,0.22)' : isActionable ? `${verdictColor}22` : 'rgba(255,255,255,0.07)',
                    color: isActionable ? verdictColor : 'rgba(255,255,255,0.38)',
                    fontSize:13, fontWeight:800, fontFamily:'monospace', letterSpacing:'0.06em',
                    boxShadow: isActionable ? `0 0 18px ${verdictColor}28` : 'none',
                  }}>
                    {confirming ? 'CONFIRM — SEND LIVE ORDER' : isActionable ? 'READY TO TRADE' : 'WAITING FOR SETUP'}
                  </button>
                )}
                {confirming && (
                  <button className="action-btn" onClick={() => setConfirming(false)} style={{
                    padding:'10px 16px', borderRadius:8, border:'1px solid rgba(239,68,68,0.25)',
                    background:'transparent', color:BEAR, fontSize:12, fontFamily:'monospace', cursor:'pointer' }}>
                    Cancel
                  </button>
                )}
                <button className="action-btn chip-btn" onClick={() => setChatOpen(!chatOpen)} style={{
                  padding:'10px 16px', borderRadius:8, border:'1px solid rgba(255,255,255,0.10)',
                  background:'transparent', color:'rgba(255,255,255,0.45)', fontSize:12, fontFamily:'monospace', cursor:'pointer' }}>
                  {chatOpen ? 'Hide Chat' : 'Ask Brain'}
                </button>
                {/* ── SPEAK BUTTON ─────────────────────────────────────────── */}
                <button onClick={handleSpeak} disabled={voiceState === 'requesting'}
                  title={voiceState === 'error' ? voiceErrorMsg : voiceState === 'listening' ? 'Tap to stop recording' : speaking ? 'Tap to interrupt' : 'Tap to speak'}
                  style={{
                    display:'flex', alignItems:'center', gap:5,
                    minHeight:42, minWidth:52, padding:'8px 13px', borderRadius:8,
                    border: voiceState === 'listening' ? '1px solid rgba(239,68,68,0.50)'
                          : speaking                  ? '1px solid rgba(56,189,248,0.35)'
                          : voiceState === 'error'    ? '1px solid rgba(248,113,113,0.30)'
                          :                            '1px solid rgba(59,130,246,0.28)',
                    background: voiceState === 'listening' ? 'rgba(239,68,68,0.09)'
                              : speaking                  ? 'rgba(56,189,248,0.07)'
                              : voiceState === 'error'    ? 'rgba(239,68,68,0.06)'
                              :                            'rgba(59,130,246,0.05)',
                    color: voiceState === 'listening' ? '#ef4444'
                         : speaking                  ? CYAN
                         : voiceState === 'error'    ? '#f87171'
                         :                            BLUE,
                    fontSize:12, fontFamily:'monospace',
                    cursor: voiceState === 'requesting' ? 'default' : 'pointer',
                    transition:'all 0.18s',
                    animation: voiceState === 'listening' ? 'micPulse 1.1s ease-in-out infinite' : 'none' }}>
                  {voiceState === 'listening' && (
                    <span style={{ display:'flex', gap:2, alignItems:'flex-end', height:12 }}>
                      {[0,1,2,3].map(i => (
                        <span key={i} style={{ width:3, height:10, borderRadius:2, background:'#ef4444', display:'block',
                          animation:`wv ${0.38+i*0.11}s ease-in-out ${i*0.07}s infinite alternate` }} />
                      ))}
                    </span>
                  )}
                  <span style={{ fontSize:13 }}>
                    {speaking ? '\u25A0' : voiceState === 'listening' ? '\u25CF' : voiceState === 'requesting' || voiceState === 'processing' ? '\u22EF' : '\uD83C\uDFA4'}
                  </span>
                  <span>
                    {voiceState === 'listening' ? 'Listening\u2026' : speaking ? 'Stop' : voiceState === 'requesting' ? '\u2026' : voiceState === 'processing' ? 'Thinking\u2026' : voiceState === 'error' ? 'Retry' : 'Speak'}
                  </span>
                </button>
              </div>
            </div>
          </div>

          {/* ── INTELLIGENCE STRIP ──────────────────────────────────────── */}
          <div className="intel-strip" style={{ display:'flex', gap:8, marginBottom:16, flexWrap:'nowrap', minWidth:0 }}>

            {/* TODAY'S OBJECTIVE */}
            <SatPanel label="Today's Objective" style={{ flex:'1.6 1 0', minWidth:0 }}>
              {(() => {
                let obj = '';
                if (!data || loading)              obj = 'Connecting to market feed...';
                else if (status === 'READY')        obj = `Execute ${/long|bull/i.test(dirn)?'LONG':'SHORT'} near ${tp.entry?fmt(Number(tp.entry)):'entry'}.`;
                else if (status === 'MANAGING')     obj = `Manage position. Target ${tp.target1?fmt(Number(tp.target1)):'T1'}, stop protected.`;
                else if (status === 'BUILDING')     obj = `Score ${Math.round(edge)}. ${!gd.structure_confirmed?'BOS/CHOCH needed.':!gd.zone_valid?'Zone forming.':'Setup finalizing.'}`;
                else if (!gd.structure_confirmed)  obj = 'Waiting for structural break — BOS or CHOCH needed.';
                else if (!gd.zone_valid)            obj = 'Structure confirmed. Waiting for demand or supply zone.';
                else if (edge < 40)                obj = 'Building edge. Multiple confirmations required.';
                else                               obj = `Edge ${Math.round(edge)}/70. Final confirmation pending.`;
                return <div style={{ fontSize:11.5, color:'rgba(255,255,255,0.68)', lineHeight:1.5, fontFamily:'monospace' }}>{obj}</div>;
              })()}
            </SatPanel>

            {/* AI REASONING — backend synthesis / voice narration */}
            <SatPanel label="AI Reasoning" style={{ flex:'2.2 1 0', minWidth:0 }}>
              <div style={{ fontSize:11.5, color:'rgba(255,255,255,0.58)', lineHeight:1.5, fontFamily:'monospace', fontStyle:'italic' }}>
                {displayNarration ? displayNarration.slice(0, 130) + (displayNarration.length > 130 ? '...' : '') : 'Analyzing market conditions...'}
              </div>
            </SatPanel>

            {/* VOLATILITY */}
            <SatPanel label="Volatility" style={{ flex:'1 1 0', minWidth:0 }}>
              {(() => {
                const vr = String(ad.volatility_regime || data?.vol_regime || '').toLowerCase();
                const col = /extreme/.test(vr) ? BEAR : /high|elev/.test(vr) ? '#f97316' : /low|quiet/.test(vr) ? MUTED : AMB;
                const lbl = /extreme/.test(vr) ? 'EXTREME' : /high|elev/.test(vr) ? 'ELEVATED' : /low|quiet/.test(vr) ? 'QUIET' : isOpen ? 'NORMAL' : '—';
                return (
                  <>
                    <div style={{ fontSize:13, fontWeight:800, color:col, fontFamily:'monospace', letterSpacing:'0.02em' }}>{lbl}</div>
                    <div style={{ fontSize:8.5, color:MUTED, fontFamily:'monospace', marginTop:2 }}>ATR REGIME</div>
                  </>
                );
              })()}
            </SatPanel>

            {/* NEXT ECONOMIC EVENT (only if news data available) */}
            {Array.isArray(data?.news) && data.news.length > 0 && (() => {
              const nxt = data.news.find((n: any) => n && (n.impact === 'HIGH' || n.impact === 'MEDIUM'));
              if (!nxt) return null;
              const evTitle = String(nxt.title || nxt.event || '').slice(0, 22);
              const imp = String(nxt.impact || '').toUpperCase();
              const impCol = imp === 'HIGH' ? BEAR : '#f97316';
              return (
                <SatPanel key="news" label="Next Event" style={{ flex:'1 1 0', minWidth:0 }}>
                  <div style={{ fontSize:10.5, fontWeight:700, color:'rgba(255,255,255,0.72)', fontFamily:'monospace', marginBottom:3 }}>{evTitle}</div>
                  <div style={{ fontSize:8.5, color:impCol, fontFamily:'monospace' }}>{imp} IMPACT</div>
                </SatPanel>
              );
            })()}

          </div>

          {/* ── AI MEMORY & PERFORMANCE ──────────────────────────────────── */}
          <div style={{ marginBottom:14, borderRadius:10, overflow:'hidden',
            border:'1px solid rgba(255,255,255,0.055)', background:'rgba(5,8,18,0.55)' }}>
            {/* Panel header */}
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
              padding:'7px 14px', borderBottom:'1px solid rgba(255,255,255,0.04)' }}>
              <span style={{ fontSize:8, fontFamily:'monospace', letterSpacing:'0.13em',
                color:'rgba(255,255,255,0.28)', textTransform:'uppercase' }}>AI Memory \u00b7 Performance History</span>
              <span style={{ fontSize:8, fontFamily:'monospace', color:'rgba(255,255,255,0.16)' }}>7-day</span>
            </div>
            {/* Stats row: Yesterday | This Week | Win Rate | Avg R:R | Best Setup */}
            <div style={{ display:'flex', borderBottom:'1px solid rgba(255,255,255,0.04)' }}>
              {/* Yesterday */}
              <div style={{ flex:1, padding:'9px 12px', borderRight:'1px solid rgba(255,255,255,0.04)' }}>
                <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.11em', color:'rgba(255,255,255,0.22)', textTransform:'uppercase', marginBottom:4 }}>Yesterday</div>
                {tm.yesterday.total > 0 ? (
                  <>
                    <div style={{ fontSize:14, fontWeight:800, fontFamily:'monospace',
                      color: tm.yesterday.wr !== null && tm.yesterday.wr >= 55 ? BULL : tm.yesterday.wr !== null && tm.yesterday.wr >= 40 ? AMB : BEAR }}>
                      {tm.yesterday.wins}W {tm.yesterday.losses}L
                    </div>
                    <div style={{ fontSize:9, fontFamily:'monospace', color:MUTED, marginTop:3 }}>
                      {tm.yesterday.wr !== null ? tm.yesterday.wr + '% WR' : ''}
                      {tm.yesterday.avgRR ? '  \u00b7  ' + tm.yesterday.avgRR.toFixed(1) + 'R' : ''}
                    </div>
                  </>
                ) : mem.yest ? (
                  <>
                    <div style={{ fontSize:14, fontWeight:800, fontFamily:'monospace',
                      color: mem.yest.pe >= 70 ? BULL : mem.yest.pe >= 50 ? AMB : MUTED }}>
                      {Math.round(mem.yest.pe)}<span style={{ fontSize:8, fontWeight:400 }}>/110</span>
                    </div>
                    <div style={{ fontSize:9, fontFamily:'monospace', color:MUTED, marginTop:3 }}>
                      {mem.yest.su} setup{mem.yest.su !== 1 ? 's' : ''} \u00b7 {mem.yest.tr} trade{mem.yest.tr !== 1 ? 's' : ''}
                    </div>
                  </>
                ) : <div style={{ fontSize:11, fontFamily:'monospace', color:MUTED }}>\u2014</div>}
              </div>
              {/* This Week */}
              <div style={{ flex:1, padding:'9px 12px', borderRight:'1px solid rgba(255,255,255,0.04)' }}>
                <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.11em', color:'rgba(255,255,255,0.22)', textTransform:'uppercase', marginBottom:4 }}>This Week</div>
                {tm.week.total > 0 ? (
                  <>
                    <div style={{ fontSize:14, fontWeight:800, fontFamily:'monospace',
                      color: tm.week.wr !== null && tm.week.wr >= 55 ? BULL : tm.week.wr !== null && tm.week.wr >= 40 ? AMB : BEAR }}>
                      {tm.week.wins}W {tm.week.losses}L
                    </div>
                    <div style={{ fontSize:9, fontFamily:'monospace', color:MUTED, marginTop:3 }}>
                      {tm.week.wr !== null ? tm.week.wr + '% WR' : ''}
                      {tm.week.avgRR ? '  \u00b7  ' + tm.week.avgRR.toFixed(1) + 'R avg' : ''}
                    </div>
                  </>
                ) : (
                  <>
                    <div style={{ fontSize:14, fontWeight:800, fontFamily:'monospace',
                      color: mem.wkPeak >= 70 ? BULL : mem.wkPeak >= 50 ? AMB : MUTED }}>
                      {mem.wkPeak > 0 ? Math.round(mem.wkPeak) + '/110' : '\u2014'}
                    </div>
                    <div style={{ fontSize:9, fontFamily:'monospace', color:MUTED, marginTop:3 }}>
                      {mem.active} session{mem.active !== 1 ? 's' : ''} active
                    </div>
                  </>
                )}
              </div>
              {/* Win Rate */}
              <div style={{ flex:1, padding:'9px 12px', borderRight:'1px solid rgba(255,255,255,0.04)' }}>
                <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.11em', color:'rgba(255,255,255,0.22)', textTransform:'uppercase', marginBottom:4 }}>Win Rate</div>
                <div style={{ fontSize:14, fontWeight:800, fontFamily:'monospace',
                  color: tm.week.wr !== null ? (tm.week.wr >= 55 ? BULL : tm.week.wr >= 40 ? AMB : BEAR) : MUTED }}>
                  {tm.week.wr !== null ? tm.week.wr + '%' : '\u2014'}
                </div>
                <div style={{ fontSize:9, fontFamily:'monospace', color:MUTED, marginTop:3 }}>
                  {tm.week.total > 0 ? tm.week.total + ' trade' + (tm.week.total !== 1 ? 's' : '') + ' recorded' : 'Building data'}
                </div>
              </div>
              {/* Avg R:R */}
              <div style={{ flex:1, padding:'9px 12px', borderRight:'1px solid rgba(255,255,255,0.04)' }}>
                <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.11em', color:'rgba(255,255,255,0.22)', textTransform:'uppercase', marginBottom:4 }}>Avg R:R</div>
                <div style={{ fontSize:14, fontWeight:800, fontFamily:'monospace',
                  color: tm.week.avgRR ? (tm.week.avgRR >= 1.5 ? BULL : tm.week.avgRR >= 1.0 ? AMB : BEAR) : MUTED }}>
                  {tm.week.avgRR ? tm.week.avgRR.toFixed(1) + 'R' : '\u2014'}
                </div>
                <div style={{ fontSize:9, fontFamily:'monospace', color:MUTED, marginTop:3 }}>
                  {tm.week.avgRR ? (tm.week.avgRR >= 1.5 ? 'Above target' : tm.week.avgRR >= 1.0 ? 'On target' : 'Below target') : 'No data yet'}
                </div>
              </div>
              {/* Best Setup */}
              <div style={{ flex:1.4, padding:'9px 12px' }}>
                <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.11em', color:'rgba(255,255,255,0.22)', textTransform:'uppercase', marginBottom:4 }}>Best Setup</div>
                <div style={{ fontSize:12, fontWeight:800, fontFamily:'monospace', color:BULL, lineHeight:1.2 }}>
                  {tm.bestSetup ? tm.bestSetup.name.replace(/\b\w/g, (c:string) => c.toUpperCase()).slice(0, 18) : '\u2014'}
                </div>
                <div style={{ fontSize:9, fontFamily:'monospace', color:MUTED, marginTop:3 }}>
                  {tm.bestSetup ? Math.round(tm.bestSetup.wr * 100) + '%  \u00b7  ' + tm.bestSetup.total + ' trade' + (tm.bestSetup.total !== 1 ? 's' : '') : 'Accumulating data'}
                </div>
              </div>
            </div>

            {/* 7-day W/L bar chart */}
            <div style={{ padding:'10px 14px 8px', borderBottom:'1px solid rgba(255,255,255,0.04)' }}>
              <div style={{ display:'flex', alignItems:'flex-end', gap:5, height:38 }}>
                {tm.dailyBars.map((bar, i) => {
                  const isT = bar.date === todayStr;
                  const mx  = Math.max(...tm.dailyBars.map(b => b.total), 1);
                  const wH  = bar.total > 0 ? Math.max(3, Math.round(bar.wins   / mx * 30)) : 0;
                  const lH  = bar.total > 0 ? Math.max(3, Math.round(bar.losses / mx * 30)) : 0;
                  return (
                    <div key={i} title={bar.date + ': ' + bar.wins + 'W ' + bar.losses + 'L'}
                      style={{ flex:1, display:'flex', flexDirection:'column', alignItems:'center', gap:2, cursor:'default' }}>
                      {bar.total > 0 ? (
                        <>
                          {wH > 0 && <div style={{ width:'60%', height:wH, borderRadius:2, background: isT ? verdictColor : BULL, opacity: isT ? 1 : 0.58, transition:'height 0.4s' }} />}
                          {lH > 0 && <div style={{ width:'60%', height:lH, borderRadius:2, background: isT ? 'rgba(239,68,68,0.75)' : 'rgba(239,68,68,0.38)', transition:'height 0.4s' }} />}
                        </>
                      ) : (
                        <div style={{ width:'60%', height:3, borderRadius:2, background:'rgba(255,255,255,0.07)' }} />
                      )}
                      <div style={{ fontSize:7, fontFamily:'monospace', marginTop:2,
                        color: isT ? 'rgba(255,255,255,0.45)' : 'rgba(255,255,255,0.18)' }}>
                        {new Date(bar.date + 'T12:00:00').toLocaleDateString('en-US', { weekday:'narrow' })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Bottom row: Today's Focus | Most Common Mistake + This Session */}
            <div style={{ display:'flex' }}>
              {/* Today's Focus */}
              <div style={{ flex:1.6, padding:'10px 14px', borderRight:'1px solid rgba(255,255,255,0.04)' }}>
                <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.13em', color:'rgba(255,255,255,0.28)', textTransform:'uppercase', marginBottom:7 }}>Today's Focus</div>
                {objectives.length > 0 ? (
                  <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
                    {objectives.slice(0, 3).map((obj, i) => (
                      <div key={i} style={{ display:'flex', gap:7, alignItems:'flex-start' }}>
                        <span style={{ fontSize:8, color:'rgba(99,179,237,0.45)', fontFamily:'monospace', fontWeight:700, flexShrink:0, marginTop:1 }}>{i + 1}.</span>
                        <span style={{ fontSize:10.5, color:'rgba(255,255,255,0.58)', fontFamily:'monospace', lineHeight:1.45 }}>{obj}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ fontSize:10.5, color:MUTED, fontFamily:'monospace', lineHeight:1.5 }}>
                    Loading objectives from your trade history...
                  </div>
                )}
              </div>
              {/* Most Common Mistake + This Session */}
              <div style={{ flex:1, padding:'10px 14px', display:'flex', flexDirection:'column', gap:10 }}>
                {(tm.worstSetup || mem.mcWR) && (
                  <div>
                    <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.13em', color:'rgba(255,255,255,0.28)', textTransform:'uppercase', marginBottom:5 }}>Most Common Mistake</div>
                    {tm.worstSetup ? (
                      <>
                        <div style={{ fontSize:11, fontWeight:700, fontFamily:'monospace', color:BEAR, lineHeight:1.2 }}>
                          {tm.worstSetup.name.replace(/\b\w/g, (c:string) => c.toUpperCase()).slice(0, 22)}
                        </div>
                        <div style={{ fontSize:9, fontFamily:'monospace', color:MUTED, marginTop:2 }}>
                          {tm.worstSetup.losses} loss{tm.worstSetup.losses !== 1 ? 'es' : ''}  \u00b7  {Math.round(tm.worstSetup.wr * 100)}% WR
                        </div>
                      </>
                    ) : mem.mcWR ? (
                      <div style={{ fontSize:10, fontFamily:'monospace', color:'rgba(255,255,255,0.50)', lineHeight:1.4 }}>
                        {mem.mcWR.slice(0, 60)}
                      </div>
                    ) : null}
                  </div>
                )}
                {/* This Session live */}
                <div>
                  <div style={{ fontSize:7, fontFamily:'monospace', letterSpacing:'0.13em', color:'rgba(255,255,255,0.28)', textTransform:'uppercase', marginBottom:5 }}>This Session</div>
                  <div style={{ fontSize:13, fontWeight:800, fontFamily:'monospace',
                    color: mem.live.pe >= 70 ? BULL : mem.live.pe >= 50 ? AMB : MUTED }}>
                    {Math.round(mem.live.pe)}<span style={{ fontSize:8, fontWeight:400, opacity:0.55 }}>/110</span>
                  </div>
                  <div style={{ fontSize:9, fontFamily:'monospace', color:MUTED, marginTop:2 }}>
                    peak \u00b7 {mem.live.su} setup{mem.live.su !== 1 ? 's' : ''} \u00b7 {mem.live.tr} trade{mem.live.tr !== 1 ? 's' : ''}
                  </div>
                  {tm.today.total > 0 && (
                    <div style={{ fontSize:9, fontFamily:'monospace', marginTop:2,
                      color: tm.today.wr !== null && tm.today.wr >= 55 ? BULL : tm.today.wr !== null && tm.today.wr >= 40 ? AMB : BEAR }}>
                      {tm.today.wins}W {tm.today.losses}L{tm.today.wr !== null ? '  \u00b7  ' + tm.today.wr + '% WR' : ''}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* ── QUICK CHIPS ─────────────────────────────────────────────── */}
          <div className="quick-chips" style={{ display:'flex', gap:6, flexWrap:'wrap', marginBottom:16 }}>
            {chips.map(c => (
              <button key={c} className="chip-btn" onClick={() => ask(c)} disabled={asking} style={{
                padding:'5px 13px', borderRadius:16, border:'1px solid rgba(255,255,255,0.09)',
                background:'transparent', color:'rgba(255,255,255,0.40)', fontSize:12, fontFamily:'monospace',
                cursor:'pointer' }}>
                {c}
              </button>
            ))}
          </div>

          {/* ── SESSION MEMORY ──────────────────────────────────────── */}
          <div style={{ marginBottom:10, border:'1px solid rgba(255,255,255,0.048)', borderRadius:10, overflow:'hidden' }}>
            <button className="accord-toggle" onClick={() => setMemOpen(!memOpen)} style={{
              width:'100%', display:'flex', alignItems:'center', justifyContent:'space-between',
              padding:'9px 14px', background:'rgba(255,255,255,0.012)', border:'none', cursor:'pointer',
              color:'rgba(255,255,255,0.40)', fontSize:11, fontFamily:'monospace', letterSpacing:'0.08em' }}>
              <div style={{ display:'flex', alignItems:'center', gap:7 }}>
                <span style={{ fontWeight:700, textTransform:'uppercase' }}>Session Memory</span>
                {memEntries.length > 0 && (
                  <span style={{ fontSize:9.5, color:CYAN, background:'rgba(56,189,248,0.08)',
                    border:'1px solid rgba(56,189,248,0.18)', borderRadius:10, padding:'1px 7px', fontFamily:'monospace' }}>
                    {memEntries.length}
                  </span>
                )}
              </div>
              <span style={{ fontSize:12, color:'rgba(255,255,255,0.22)' }}>{memOpen ? '▲' : '▼'}</span>
            </button>
            {memOpen && (
              <div style={{ padding:'11px 14px 14px', borderTop:'1px solid rgba(255,255,255,0.035)' }}>
                <MemoryPanel entries={memEntries} onClear={memClear} />
              </div>
            )}
          </div>

          {/* ── CHAT ────────────────────────────────────────────────────── */}
          {chatOpen && (
            <div style={{ marginBottom:16, border:'1px solid rgba(255,255,255,0.062)', borderRadius:10,
              background:'rgba(255,255,255,0.018)', overflow:'hidden' }}>
              <div ref={chatRef} style={{ maxHeight:220, overflowY:'auto', padding:'14px 14px 6px' }}>
                {msgs.length === 0 && (
                  <div style={{ fontSize:12, color:'rgba(255,255,255,0.22)', fontFamily:'monospace', textAlign:'center', padding:'20px 0' }}>What do you see in the tape?</div>
                )}
                {msgs.map(m => <BrainBubble key={m.id} msg={m} />)}
                {asking && (
                  <div style={{ display:'flex', gap:5, padding:'6px 0 4px' }}>
                    {[0,1,2].map(i => <div key={i} style={{ width:6, height:6, borderRadius:'50%', background:eyeColor, animation:`bDot 1.2s ${i*0.2}s infinite` }} />)}
                  </div>
                )}
              </div>
              {/* Voice transcript preview — shown while listening or processing */}
              {(voiceState === 'listening' || voiceState === 'processing') && voiceTranscript && (
                <div style={{ padding:'5px 14px 0', fontSize:12, color:'rgba(255,255,255,0.52)', fontFamily:'monospace',
                  fontStyle:'italic', borderTop:'1px solid rgba(59,130,246,0.08)', display:'flex', alignItems:'center', gap:6 }}>
                  <span style={{ color:'rgba(59,130,246,0.55)', fontSize:9 }}>{'\u25CF'}</span>
                  {voiceTranscript}
                </div>
              )}
              {/* Voice error — with dismiss */}
              {voiceState === 'error' && voiceErrorMsg && (
                <div style={{ padding:'5px 14px 0', fontSize:11.5, color:'#f87171', fontFamily:'monospace',
                  borderTop:'1px solid rgba(239,68,68,0.08)', display:'flex', alignItems:'center', gap:6 }}>
                  <span style={{ flex:1 }}>{voiceErrorMsg}</span>
                  <button onClick={clearVoiceError} style={{ background:'none', border:'none',
                    color:'rgba(255,255,255,0.28)', fontSize:11, cursor:'pointer', padding:'0 2px' }}>{'\u2715'}</button>
                </div>
              )}
              <div className="input-wrap" style={{ display:'flex', alignItems:'center', gap:8, padding:'8px 14px 12px',
                borderTop:'1px solid rgba(255,255,255,0.045)' }}>
                <input ref={inputRef} className="brain-input" value={input} onChange={e => setInput(e.target.value)} onKeyDown={onKey}
                  placeholder="Ask the brain\u2026" disabled={asking}
                  style={{ flex:1, background:'transparent', border:'none', color:'rgba(255,255,255,0.80)', fontSize:13, fontFamily:'inherit' }} />
                <button onClick={() => ask()} disabled={!input.trim() || asking} style={{
                  background:'none', border:'none', padding:'2px 4px', cursor: input.trim() && !asking ? 'pointer' : 'default',
                  color: input.trim() && !asking ? eyeColor : 'rgba(255,255,255,0.18)', fontSize:15 }}>{'\u21B5'}</button>
              </div>
            </div>
          )}

          {/* ── EVIDENCE ACCORDION ──────────────────────────────────────── */}
          <div style={{ marginBottom:12, border:'1px solid rgba(255,255,255,0.055)', borderRadius:10, overflow:'hidden' }}>
            <button className="accord-toggle" onClick={() => setEvidenceOpen(!evidenceOpen)} style={{
              width:'100%', display:'flex', alignItems:'center', justifyContent:'space-between',
              padding:'11px 16px', background:'rgba(255,255,255,0.020)', border:'none', cursor:'pointer',
              color:'rgba(255,255,255,0.55)', fontSize:11.5, fontFamily:'monospace', letterSpacing:'0.08em' }}>
              <span style={{ textTransform:'uppercase', fontWeight:700 }}>Evidence Snapshot</span>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                {data && <span style={{ fontSize:11, color:verdictColor, fontWeight:700 }}>EDGE {Math.round(edge)} / 110</span>}
                <span style={{ fontSize:13, color:'rgba(255,255,255,0.30)' }}>{evidenceOpen ? '▲' : '▼'}</span>
              </div>
            </button>
            {evidenceOpen && data && (
              <div style={{ padding:'14px 16px 16px', borderTop:'1px solid rgba(255,255,255,0.040)' }}>
                <EvidenceDrawer data={data} status={status} />
              </div>
            )}
          </div>

          {/* ── CHART ACCORDION ─────────────────────────────────────────── */}
          <div style={{ border:'1px solid rgba(255,255,255,0.042)', borderRadius:10, overflow:'hidden' }}>
            <button className="accord-toggle" onClick={() => setChartOpen(!chartOpen)} style={{
              width:'100%', display:'flex', alignItems:'center', justifyContent:'space-between',
              padding:'10px 16px', background:'rgba(255,255,255,0.018)', border:'none', cursor:'pointer',
              color:'rgba(255,255,255,0.40)', fontSize:11, fontFamily:'monospace', letterSpacing:'0.08em' }}>
              <span style={{ textTransform:'uppercase', fontWeight:700 }}>{ticker} Chart · 1m</span>
              <div style={{ display:'flex', alignItems:'center', gap:10 }}>
                {data?.vwap_value && <span className="chart-hdr-extra" style={{ color:'#60a5fa', fontSize:10.5 }}>VWAP {fmt(data.vwap_value)}</span>}
                {data?.nearest_demand && <span className="chart-hdr-extra" style={{ color:BULL, fontSize:10.5 }}>D {fmt(data.nearest_demand)}</span>}
                {data?.nearest_supply && <span className="chart-hdr-extra" style={{ color:BEAR, fontSize:10.5 }}>S {fmt(data.nearest_supply)}</span>}
                <span style={{ fontSize:13, color:'rgba(255,255,255,0.25)' }}>{chartOpen ? '▲' : '▼'}</span>
              </div>
            </button>
            {chartOpen && (
              <div style={{ height:190, padding:'8px 12px 10px', borderTop:'1px solid rgba(255,255,255,0.035)' }}>
                <CandleChart candles={candlesRef.current} vwap={data?.vwap_value} demand={data?.nearest_demand} supply={data?.nearest_supply} ticker={ticker} />
              </div>
            )}
          </div>

          {/* spacer */}
          <div style={{ height:24 }} />
        </div>
      </div>
    </div>
  );
}
