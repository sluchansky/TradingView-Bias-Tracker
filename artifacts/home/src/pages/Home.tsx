import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import LordPiggingtonAvatar from '../components/avatar/LordPiggingtonAvatar';
import AvatarAura from '../components/avatar/AvatarAura';
import {
  classifyDatabentoFreshness,
  formatFreshnessAge,
  latestBarTimestampMs,
  timestampMs,
} from '../lib/marketDataFreshness';

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
        hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'Etc/GMT+4',
      }) + ' UTC-4'
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
  const [muted, setMutedState] = useState<boolean>(() => { try { const v = localStorage.getItem('brain_muted'); const isProd = window.location.hostname.endsWith('.replit.app'); return v === null ? !isProd : v !== '0'; } catch { return false; } });
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
      // ── Natural pause markers — em dashes and ellipses become short pauses ──
      .replace(/\s*—\s*/g,  ', ')
      .replace(/\.\.\./g,   '. ')
      // ── Ticker names ──────────────────────────────────────────────────────────
      .replace(/\bMNQ\b/g,   'mini nasdaq')
      .replace(/\bMGC\b/g,   'micro gold')
      .replace(/\bMES\b/g,   'micro S and P')
      .replace(/\bMYM\b/g,   'micro Dow')
      .replace(/\bNQ\b/g,    'nasdaq')
      .replace(/\bES\b/g,    'S and P')
      .replace(/\bYM\b/g,    'Dow futures')
      .replace(/\bGC\b/g,    'gold')
      // ── Structure & flow ──────────────────────────────────────────────────────
      .replace(/\bBOS\b/g,   'break of structure')
      .replace(/\bCHOCH\b/g, 'change of character')
      .replace(/\bVWAP\b/gi, 'vee-wap')
      .replace(/\bCVD\b/g,   'cumulative delta')
      .replace(/\bRVOL\b/g,  'relative volume')
      .replace(/\bHTF\b/g,   'higher timeframe')
      .replace(/\bLTF\b/g,   'lower timeframe')
      .replace(/\bATR\b/g,   'average true range')
      .replace(/\bFVG\b/g,   'fair value gap')
      .replace(/\bOB\b/g,    'order block')
      .replace(/\bPOI\b/g,   'point of interest')
      .replace(/\bORB\b/g,   'opening range breakout')
      .replace(/\bR:R\b/gi,  'risk to reward')
      .replace(/\bHH\b/g,    'higher high')
      .replace(/\bHL\b/g,    'higher low')
      .replace(/\bLH\b/g,    'lower high')
      .replace(/\bLL\b/g,    'lower low')
      .replace(/\bTP\b/g,    'take profit')
      .replace(/\bSL\b/g,    'stop loss')
      .replace(/\bBE\b/g,    'break even')
      .replace(/\bPnL\b/gi,  'profit and loss')
      // ── R multiples ───────────────────────────────────────────────────────────
      .replace(/\b1R\b/gi,   'one R')
      .replace(/\b2R\b/gi,   'two R')
      .replace(/\b3R\b/gi,   'three R')
      .replace(/\b4R\b/gi,   'four R')
      // ── Cleanup: strip markdown symbols that TTS would read aloud ─────────────
      .replace(/\*\*/g, '')
      .replace(/\*/g,   '')
      .replace(/#/g,    '')
      .slice(0, 500);

    const utt = new SpeechSynthesisUtterance(cleaned);

    // ── Voice priority: prefer natural-sounding voices over robotic defaults ──
    // Priority list (browser/OS specific — first match wins):
    //   macOS/Safari: "Daniel" (UK male), "Alex" (US male)
    //   Chrome/Windows: "Google UK English Male", "Microsoft David Desktop"
    //   Android: "Google UK English Male"
    //   Fallback: first available, or none (browser default)
    const PREFERRED_VOICES = [
      'Daniel',                     // macOS Safari — deep, natural
      'Google UK English Male',     // Chrome — clear and warm
      'Microsoft David Desktop - English (United States)',
      'Microsoft David - English (United States)',
      'Alex',                       // macOS fallback
      'Google US English',          // Chrome US fallback
      'en-US-Neural2-D',            // some Android builds
      'en-GB-Neural2-B',
    ];
    const savedVoice  = voiceName ? voices.find(v => v.name === voiceName) : null;
    const pickedVoice = savedVoice
      ?? PREFERRED_VOICES.reduce<SpeechSynthesisVoice | null>((found, name) =>
           found ?? voices.find(v => v.name === name) ?? null, null)
      ?? voices.find(v => /male/i.test(v.name) && /en/i.test(v.lang))
      ?? voices.find(v => /en/i.test(v.lang))
      ?? voices[0]
      ?? null;
    if (pickedVoice) utt.voice = pickedVoice;

    // Slightly slower than default: more deliberate, analytical pacing
    utt.rate  = 0.92;
    utt.pitch = 1.0;

    utt.onstart = () => {
      setSpeaking(true);
      speechCtrlRef.current = { energy: 0, viseme: 'open', active: true };
      wordDataRef.current = { t0: Date.now(), dur: 350, char: 'a' };
      // Drive mouth energy via a dedicated RAF loop — no React renders, no jitter
      const driveEnergy = () => {
        const ctrl = speechCtrlRef.current;
        if (!ctrl.active) return;
        const { t0, dur, char } = wordDataRef.current;
        const age  = Date.now() - t0;
        // Self-sustaining: auto-advance syllable when onboundary hasn't fired.
        // Without this, energy collapses to 0 after the first ~280ms and lips freeze.
        const effectiveDur = Math.max(dur, 300);
        if (age >= effectiveDur) {
          wordDataRef.current = { t0: Date.now(), dur: 300 + Math.random() * 120, char: char };
        }
        const prog = Math.min(1, age / effectiveDur);
        // Bell-curve energy per syllable: ramp up, sustain, ramp down
        const bell = prog < 0.28 ? prog / 0.28 : prog < 0.70 ? 1.0 : Math.max(0, (1 - prog) / 0.30);
        // Natural micro-jitter — two low-freq oscillators
        const jit = Math.sin(Date.now() * 0.020) * 0.14 + Math.sin(Date.now() * 0.035) * 0.09;
        const tgt  = Math.max(0, Math.min(1, bell * 0.92 + jit * bell));
        // Exponential smoothing — fast attack so mouth opens quickly
        ctrl.energy += (tgt - ctrl.energy) * 0.26;
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
    // Set active=true SYNCHRONOUSLY before ss.speak() so any code that checks
    // speechCtrlRef.current.active immediately after this call sees true —
    // onstart fires asynchronously and would leave a race window open.
    speechCtrlRef.current.active = true;
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
function LoginOverlay({ onSubmit }: { onSubmit: (pwd: string) => Promise<boolean> }) {
  const [val, setVal] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { setTimeout(() => ref.current?.focus(), 80); }, []);
  const submit = async () => {
    const p = val.trim();
    if (!p || submitting) return;
    setSubmitting(true);
    setError('');
    const accepted = await onSubmit(p);
    setSubmitting(false);
    if (!accepted) {
      setError('Password was not accepted. Check it and try again.');
      ref.current?.focus();
    }
  };
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
      <form onSubmit={e => { e.preventDefault(); void submit(); }} style={{ display:'flex', flexDirection:'column', gap:8, width:300 }}>
        <div style={{ display:'flex', gap:8 }}>
        <input ref={ref} type="password" value={val} onChange={e => { setVal(e.target.value); setError(''); }}
          aria-invalid={Boolean(error)}
          placeholder="Password" style={{ flex:1, background:'rgba(255,255,255,0.04)', border:'1px solid rgba(255,255,255,0.10)',
          borderRadius:8, padding:'10px 14px', fontSize:14, color:'rgba(255,255,255,0.85)', fontFamily:'inherit', outline:'none' }} />
        <button type="submit" disabled={submitting} style={{ padding:'10px 18px', background:'rgba(59,130,246,0.15)',
          border:'1px solid rgba(59,130,246,0.3)', borderRadius:8, color:'#93c5fd', fontSize:13, fontFamily:'inherit', cursor:submitting?'wait':'pointer', opacity:submitting?0.65:1 }}>{submitting ? 'Checking…' : 'Enter'}</button>
        </div>
        {error && <span role="alert" style={{ fontSize:11, color:'#fca5a5', fontFamily:'monospace' }}>{error}</span>}
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
    last7.forEach(r => Object.entries(r.wr).forEach(([k, v]) => {
      if (/market.closed|live alerts.paused|next open/i.test(k)) return;
      agg[k] = (agg[k] || 0) + v;
    }));
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
      const hh = new Date(e.t).toLocaleTimeString('en-US', { hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'Etc/GMT+4' });
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
        const hh = new Date(e.t).toLocaleTimeString('en-US', { hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'Etc/GMT+4' });
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
    "Nothing's setting up right now. Just watching.",
    "Market's not giving me anything clean — I'm waiting it out.",
    "Conditions aren't there yet. I'll sit until the right one shows up.",
    "Structure's all over the place. Can't get a clean risk on this.",
    "Too many mixed signals. Best move right now is no move.",
    "Nothing to do here but wait. The edge will show up.",
    "It's choppy and ranging. I'm sitting on my hands.",
    "Volume's thin and conviction's low. No reason to be in this.",
    "Not forcing anything today. The cleanest setups always come from patience.",
    "I'm neutral here until one side steps up. No directional edge right now.",
    "I don't chase. The setup has to come to me.",
    "Tape looks indecisive. Institutions aren't showing their hand yet.",
    "No structure break confirmed. Watching for the market to tip its hand.",
    "Price is chopping around vee-wap. Neither side is committing right now.",
    "Buyers and sellers are both hesitating. I won't trade this kind of tape.",
    "Demand zone's nearby but I'm not seeing any real absorption yet.",
    "No directional lean, so there's nothing here worth trading.",
    "Edge score's below my threshold. Quality over quantity — always.",
    "Watching that swing level for any sign of a reaction.",
    "I need a structure break before I'll start building a thesis here.",
    "Volume's confirming nothing. I'll wait for one side to step up.",
    "Waiting for smart money to show their hand before I commit.",
    "Spread's wide and tape's thin. Not a good time to be active.",
    "Overlapping highs and lows — classic chop. I'm staying out.",
    "Price is grinding sideways. No momentum, no reason, no trade.",
    "Waiting is a position too. Right now it's my best one.",
    "Chasing this would be the mistake. I'll wait for the next setup.",
    "Market's testing both sides. Until one side wins clearly, I'm just watching.",
    "Range is too tight for clean risk. Standing aside.",
    "I've seen this pattern before — it tends to resolve violently and unpredictably. I wait.",
    "Patience isn't passive. It's the most active decision I make.",
    "News flow's noisy right now. I'd rather let it digest before committing.",
    "Three mixed signals in a row — the tape's lying to somebody. It won't be me.",
    "Institutions are parked. Until they move, I don't move.",
    "Risk isn't worth the potential return right now. Simple math.",
    "Watching price test the prior session high. Need to see what happens at this level.",
    "No catalyst, no structure, no direction. Nothing to do here.",
    "When in doubt, stay out. And right now I've got no doubt about staying out.",
    "I'd rather look back and say I missed a trade than I took a bad one.",
    "The best traders spend most of their time doing exactly this — nothing.",
    "If setup quality isn't there, no amount of screen time changes that.",
    "I'd genuinely rather sit flat all day than book a mediocre trade.",
    "Right now the market is asking me to be patient. I'm listening.",
    "The tape is in no hurry. Neither am I.",
    "No bias. No position. That's not a weakness — that's information management.",
    "My edge requires specific conditions. Right now none of them exist.",
    "I've been burned enough times trying to make something out of nothing. I don't do that anymore.",
    "If I can't explain the setup in one sentence, I shouldn't be taking it.",
    "Clean slate. Waiting for the market to give me something to work with.",
    "Every minute I don't trade a subpar setup is a minute my edge is preserved.",
    "Nothing to act on. That's the honest answer and I'm comfortable with it.",
    "Some people call this boring. I call it protecting my capital until the edge arrives.",
    "The market's not doing me any favors right now, and that's fine. I don't need it to.",
    "No urgency. No pressure. Just watching and waiting for my criteria to line up.",
    "I've had sessions where I took zero trades and it was the right call. This might be one of those.",
  ],
  ANALYZING: [
    "Some signals are starting to align. Watching carefully for confirmation.",
    "Score's building. Not there yet, but this is worth watching.",
    "Early signs of something developing. Monitoring for the next signal.",
    "Edge is beginning to form. Still missing a couple of key pieces.",
    "Conditions are improving — delta and structure are starting to agree.",
    "Something might be setting up here. Staying alert.",
    "Order flow's showing some interest. Needs more development.",
    "Market's showing early tells. Need a few more bars to confirm.",
    "Starting to like what I see. Not committing yet though.",
    "Score's creeping up. Keeping this one on my radar.",
    "Watching for a clean break above the recent swing high — that would confirm structure.",
    "Price is compressing near vee-wap. A move is coming — watching which direction.",
    "Delta's showing some buying interest but volume hasn't confirmed yet.",
    "Demand zone's holding but I need to see buyers defend it with real conviction.",
    "Edge is climbing but I'm still below my entry threshold. Staying patient.",
    "Structure's starting to shift. Watching for that confirmation signal.",
    "Cumulative delta's improving. If structure breaks, this becomes a real setup.",
    "Price is respecting the key level. Watching how it handles the next test.",
    "Order flow's mixed right now. I want cleaner conviction before acting.",
    "Regime's improving. Score could cross the threshold on the next signal.",
    "Getting interested. Price is starting to behave in a way I recognize.",
    "Bias is tilting. Not enough to trade yet, but enough to lean in mentally.",
    "Volume's picking up on this leg. If delta follows, we might have something.",
    "Watching the close of this bar closely — a strong close here would change the picture.",
    "Liquidity sweep looks clean. Now I need structure to confirm it.",
    "Smart money looks like it's accumulating. Score's reflecting that.",
    "Order flow's starting to tip its hand. Still building, but this is on my screen.",
    "Price is testing a level I care about. Watching how the next couple bars respond.",
    "Momentum's shifting. Not committing yet, but my attention's fully here.",
    "This one's waking up. Score's moving in the right direction — stay alert.",
    "CVD's diverging from price. Interesting tell. Watching carefully.",
    "Tape's starting to show some directional intent. Needs one more confirmation.",
    "Setup's early but the narrative makes sense. Following this closely.",
    "Something's loading here. Can't act yet, but I'm not looking away either.",
    "Score's climbing. Still a few checks away from actionable, but I'm locked in.",
    "Something's shifting in the internals. Not a trade yet, but it's on my screen.",
    "Price is leaving clues. I'm collecting them before making a call.",
    "Market's showing early conviction on one side. Watching to see if it holds.",
    "Signals are building. The picture's becoming clearer with each bar.",
    "Structure's attempting to shift. Want to see follow-through before I commit.",
    "Not a signal yet — but this has the shape of something that's about to be one.",
    "Footprint's suggesting accumulation at this level. Needs confirmation before I act.",
    "Market's showing its hand slowly. I'm patient enough to wait for the full reveal.",
    "Score crossed thirty. That puts this on my watchlist, not my trade list — not yet.",
  ],
  FORMING: [
    "Setup's developing. Edge is building toward the confirmation threshold.",
    "Structure's confirmed. Waiting on zone and flow alignment now.",
    "Getting close — just a couple of conditions left.",
    "Score's approaching the entry zone. Staying focused.",
    "Thesis is forming nicely. Waiting on the final confirmation signal.",
    "Good structure, improving delta. Zone is the last piece of the puzzle.",
    "Setup's building well. Almost there — just need to be patient.",
    "Order flow's cooperating. Zone activation would seal this.",
    "I can feel this one developing. Waiting for the green light.",
    "All the pieces are moving into place. Just need that final signal.",
    "Break of structure confirmed. Watching for the pullback into the demand zone.",
    "Sellers are losing control at this level. Structure's breaking in our favor.",
    "Structure has shifted. Waiting for the zone to be tagged and hold.",
    "Cumulative delta is showing strong buying pressure. Score's approaching my threshold.",
    "Vee-wap's been reclaimed. Bulls are in control of the intraday move.",
    "Higher timeframe bias is aligned with short-term structure. Confidence is building.",
    "Liquidity was swept at the recent low. Watching for a strong reaction and hold.",
    "Setup's forming well. Just missing one more gate — watching for it now.",
    "I've got structure, I've got the zone, I've got improving delta. Nearly there.",
    "Score just crossed into the building zone. High alert — watching every bar now.",
    "This is building into something real. Need one more confirmation — stay close.",
    "Score's knocking on the door. One more clean signal and we're live.",
    "Three of four gates are green. That last one's coming — I can feel it.",
    "Narrative's strong, structure's clean. Just waiting on the flow to agree.",
    "Bias and structure are locked in. Zone just needs to activate and hold.",
    "This is the kind of setup I've been waiting for all session. Almost there.",
    "Delta's accelerating in the right direction. Score's responding. Stay ready.",
    "Market's doing exactly what my thesis said it would. Final gate's incoming.",
    "Everything's converging. Don't blink — confirmation could come any bar.",
    "Setup integrity's high. This has the fingerprint of institutional order flow.",
    "Score's ninety percent of the way there. Final piece is zone confirmation.",
    "I don't force setups. But when they form like this one's forming — I'm ready.",
    "Two bars ago I was watching. One bar ago I was interested. Right now I'm alert.",
    "Conditions are stacking up beautifully. This is what high-probability looks like before the trigger.",
    "Every bar that closes is confirming this more. Almost ready.",
    "Setup has legs. Just needs that last gate to confirm before execution.",
    "Pressure's building at this level. Something's about to give — and I think I know which way.",
    "I've seen this pattern hundreds of times. The next move usually comes fast.",
    "Thesis is clean. Entry criteria is almost fully satisfied. Eyes on the last gate.",
    "I've been patient all session for a setup like this. Don't rush the final confirmation.",
    "This is the exact structure I've been waiting for since the open.",
    "Almost there. The market's doing its job — now I just have to do mine.",
    "One more gate flips green and this becomes the highest-probability trade of the session.",
  ],
  READY_LONG: [
    "Long setup confirmed. All gates green — execution window's open.",
    "Highest probability long of the session. Everything I need is there.",
    "Bullish edge confirmed. Structure, zone, and flow are all aligned. This is the one.",
    "Long entry criteria's fully met. Risk to reward is favorable.",
    "All gates satisfied on the long side. This is what I wait for.",
    "Demand zone's holding with bullish delta and clean structure. Long bias locked in.",
    "Price is above vee-wap with structure and zone confirmed. Long setup is live.",
    "Full confidence on the long side right now. Ready for execution.",
    "Textbook long setup. Clean entry, defined risk, strong edge.",
    "Long edge at peak confidence. Every signal's in agreement.",
    "Demand zone absorbed the sellers. Buyers are stepping in with real conviction.",
    "Structure confirmed above vee-wap with strong delta. This is exactly what I look for.",
    "All gates satisfied. Smart money's absorbed the liquidity. Long is live.",
    "Sweep into demand held. Buyers defended aggressively. Long setup is clean.",
    "Everything's aligned on the long side — structure, zone, flow, vee-wap. We're ready.",
    "This is what all the waiting was for. Long setup's fully confirmed and live.",
    "Bullish structure, bullish delta, bullish zone. Three for three. Long is on.",
    "Patient capital gets the cleanest entries. This is a clean one. Long's ready.",
    "Edge score's at the session high. Long setup is confirmed.",
    "Institutional fingerprint all over this demand zone. Long bias is locked.",
    "Buyers absorbed every offer at that level. That's a real demand zone. Long is live.",
    "Higher highs, higher lows, vee-wap reclaimed, zone active. Nothing's missing. Long.",
    "Long side's showing everything I need — and nothing I don't. Execution ready.",
    "Risk is clearly defined. Target's realistic. Setup's clean. Long is confirmed.",
    "Beautiful sweep and hold into demand. Long setup is exactly what I wanted.",
    "Score's maxed. Confidence is maxed. Long is on. This is the moment I prepared for.",
    "All systems green on the long side. This is a high-conviction entry.",
    "Institutional buyers stepped in hard at that zone. Long thesis is fully validated.",
    "Long is live. Every gate cleared, every signal aligned. This is peak edge.",
    "I've been watching this build all session. Long is confirmed.",
    "Clean demand zone, clean break of structure, clean delta. Long is textbook.",
    "Nothing about this setup is ambiguous. Long bias is fully confirmed.",
    "Long is the only rational trade right now. Execution window is open.",
    "Price reclaimed the level with conviction. Long setup is locked in.",
    "That was a perfect sweep of liquidity followed by a strong reversal. Long is live.",
    "The market laid out a textbook accumulation pattern. Long is the read.",
    "Every piece of confirmation I need is here. Long is go.",
  ],
  READY_SHORT: [
    "Short setup confirmed. All gates green — execution window's open.",
    "Bearish edge confirmed. Sellers in control with structure and zone aligned.",
    "Short entry criteria's fully met. Risk is defined. Confidence is high.",
    "Supply zone's holding with bearish delta and confirmed structure. Short bias locked in.",
    "All conditions satisfied on the short side. This is the setup I was waiting for.",
    "Price is below vee-wap, structure confirmed bearish, zone active. Short is live.",
    "Short setup at peak confidence. All signals are in full agreement.",
    "Highest probability short of the session. Execution window's open.",
    "Textbook short. Clean supply zone, bearish delta, structure confirmed.",
    "Full conviction on the short side. Ready for execution.",
    "Supply zone rejected price hard. Sellers are defending aggressively. Short is on.",
    "Structure broke bearish with negative delta and volume. Short setup is confirmed.",
    "Everything's aligned on the short side. All gates green. Ready for execution.",
    "Sweep into supply held perfectly. Sellers stepped in with conviction. Short is live.",
    "This is what all that patience was for. Short setup's fully confirmed.",
    "Bearish structure, bearish delta, bearish zone. Clean sweep. Short is live.",
    "Sellers are defending that supply zone with real conviction. Short bias is locked.",
    "Lower highs, lower lows, below vee-wap, zone confirmed. Short thesis is complete.",
    "Edge at session high on the short side. This is the setup I've been building for.",
    "Price was rejected hard at supply. That's institutional supply — short is confirmed.",
    "All signals bearish and in agreement. Risk is defined. Short's ready.",
    "Short side's showing everything I need — and nothing I don't. On deck.",
    "Score's maxed. Direction's clear. Short is live. This is exactly what I train for.",
    "That supply zone rejection was aggressive and clean. Short setup is confirmed.",
    "Sellers absorbed every bid at that level. That's real supply. Short is on.",
    "Structure flipped bearish. Every gate is cleared. Short is fully confirmed.",
    "Nothing's missing on the short side. Score, structure, zone, flow — all confirmed.",
    "Short is live. Every gate cleared, every signal aligned. This is peak edge.",
    "That supply zone rejection was exactly what I needed to see. Short is confirmed.",
    "Sellers absorbed every bid at that level and price is now rolling. Short is on.",
    "Nothing about this setup is ambiguous. Short bias is fully confirmed.",
    "Short is the only rational trade right now. Execution window is open.",
    "Price failed the level with conviction. Short setup is locked in.",
    "That was a perfect sweep of stops above supply. Short is live.",
    "Distribution pattern is complete. Short is the cleanest read on the board.",
    "Every piece of confirmation I need is here. Short is go.",
  ],
  ACTIVE: [
    "Position is live. Monitoring every tick for thesis confirmation or invalidation.",
    "Trade's running. Stop is placed. Letting the edge play out.",
    "I'm in the trade now. Managing risk. Thesis is intact so far.",
    "Position's active. No reason to exit early — the setup was clean.",
    "Live trade. Price is behaving as expected. Staying disciplined.",
    "Watching for the first target. Stop is set. No second-guessing.",
    "Trade is on. Setup was textbook — trusting the process.",
    "Managing the position. I'll move stop to break even if momentum holds.",
    "Thesis is intact. Price is moving in our direction. Letting it run.",
    "Open position. Monitoring structure and delta for any invalidation.",
    "Vee-wap's holding as support below entry. That's a good sign for the thesis.",
    "Trade's progressing as planned. Watching for structure to hold on any pullback.",
    "Delta's still confirming the move. No signs of reversal yet.",
    "Tracking price toward the first target. Position's behaving exactly as modeled.",
    "I'm in the trade and focused. Thesis is intact — don't overthink it.",
    "Stop's protected. Let the market do its job. I'll manage if I need to.",
    "Price is respecting the move. Don't interfere — let the edge work.",
    "We're in the trade. The process was right. Now we manage with discipline.",
    "Momentum's carrying this well. No signs of reversal in the delta.",
    "Structure's holding, flow's cooperating. Thesis is alive and well.",
    "First half of the move's done. Watching for the next leg toward target.",
    "Thinking about moving stop to break even if the next bar closes strong.",
    "Don't let a winning trade turn into a loser. Managing actively.",
    "Price is grinding toward target. Patience in management matters just as much as the entry.",
    "Delta's confirming every bar. This trade's behaving exactly as modeled.",
    "Pullback's happening but structure's holding. Not concerned — this was expected.",
    "Position's healthy. Trust the process, trust the stop, trust the target.",
    "Every tick is information. Right now the information says hold the trade.",
    "This is where discipline matters most — while the trade's open and moving.",
    "Halfway to target and thesis is fully intact. Letting this run as planned.",
    "I'm in the trade and the market is cooperating. Don't overthink it.",
    "One thing I never do in an open trade is move my stop without a structural reason.",
    "This move is intact. The thesis hasn't changed. Neither has my management plan.",
    "First sign of structure breaking against the trade, I reassess. Until then — patience.",
    "Price is grinding to target exactly as modeled. No reason to intervene.",
    "I placed this trade based on edge, not hope. Right now the edge is winning.",
    "Thesis is alive. Stop is protected. The rest is just waiting.",
    "The best thing I can do for this trade right now is nothing.",
    "Structure is holding, delta is cooperating, and I'm staying out of my own way.",
    "Every bar that holds structure makes this trade a little more comfortable.",
  ],
  TARGET_HIT: [
    "Target hit. Exactly what the setup called for.",
    "First target reached. R is booked. Good execution today.",
    "Winner. Thesis played out perfectly. Back to scanning.",
    "Target achieved. Clean entry, clean exit. That's disciplined trading.",
    "Trade closed at target. Patience and process paid off.",
    "Profit secured. The setup was textbook. Resetting for the next one.",
    "Winner in the books. That's what happens when you wait for the right edge.",
    "Target hit. No luck involved — that was skill and patience.",
    "Clean win. Setup, entry, management — all executed correctly.",
    "Trade worked exactly as planned. That's what the process looks like.",
    "That's how it's supposed to go. Setup, entry, target. Clean.",
    "Profit booked. Every part of this trade was done right. Well done.",
    "Market paid out. Now we reset and look for the next clean setup.",
    "Win confirmed. That's the compounding edge in action. Back to work.",
    "Target reached and exited cleanly. This is what disciplined execution feels like.",
    "That trade was a direct result of waiting. Not luck — patience and process.",
    "One more in the win column. Now let the next setup come to us.",
    "Booked. No premature exit, no second-guessing. The plan worked because we trusted it.",
    "That's R in the bank. Exactly what we came here for.",
    "Perfect execution start to finish. Entry, management, exit — all by the book.",
    "We hit target. That's what the process looks like when it all comes together.",
    "Profit booked. Setup did exactly what the edge said it would.",
    "Exit at target. Clean trade, clean result. That's the system working.",
    "Winner. Don't celebrate — just document it and reset for the next one.",
    "Closed at target. Patient entry, disciplined management, clean exit.",
    "That trade made my patience worth it. Back to watching for the next one.",
    "Win confirmed. The best part? We earned it with process, not luck.",
    "Target hit exactly as planned. This is the compounding edge at work.",
  ],
  STOP_HIT: [
    "Stopped out. Loss is taken. Risk was defined — no damage beyond the plan.",
    "Stop hit. That happens. Not every setup works. Moving on.",
    "Took the loss. Position was sized correctly. Ready for the next one.",
    "Stopped out. Thesis was invalidated. That's exactly why stops exist.",
    "Loss booked. Clean risk management. Back to observation mode.",
    "Stop hit. One loss doesn't define the edge. Looking for the next setup.",
    "Trade didn't work out. Cut the loss quickly. That's the discipline.",
    "Stopped out. Re-evaluating conditions. Back to analysis mode.",
    "Market said no. I respected that with a defined stop. Moving on.",
    "Loss taken cleanly. Setup was valid — market just disagreed today.",
    "Stop hit. That was a pre-defined acceptable loss. No regrets. Moving forward.",
    "Didn't work. But the process was right. That's what matters long term.",
    "Loss is accounted for. The setup was sound — the market just had other plans.",
    "Cut cleanly. No hesitation, no averaging down. That's professional execution.",
    "Stopped out with minimal damage. Risk management worked exactly as designed.",
    "Market invalidated the thesis. That's what stops are for. Back to watching.",
    "Losses are tuition. That one told me the market isn't done with this level.",
    "Losing trade, winning process. That distinction matters more than most people know.",
    "We took a loss. Now we take a breath, reset, and look for the next edge.",
    "Not every trade wins. That's why we size correctly. Loss is contained. Moving on.",
    "Stopped out. The risk was defined and I accepted it before I entered. Moving on.",
    "Loss is part of the game. What matters is that the process was sound.",
    "That stop was exactly where it needed to be. The market tested it and won. That's okay.",
    "Not my best trade, but it was managed correctly. That's all I can ask for.",
    "One stop-out doesn't erase an edge. It's a single data point in a large sample.",
    "Loss accepted. No revenge, no doubling down. Clean slate.",
    "The thesis was wrong today. That happens. The sizing was right — that's what matters.",
    "I exited cleanly and without hesitation. That's the discipline paying off.",
  ],
  NO_EDGE: [
    "Edge score's too low to act. Sitting completely aside.",
    "Nothing identifiable setting up right now. Waiting for conditions to develop.",
    "Market structure's weak. Not worth the risk at these levels.",
    "Zero edge on anything right now. Watching only.",
    "Unfavorable conditions across the board. I'm staying flat.",
    "Nothing's setting up. I'd rather miss a trade than take a bad one.",
    "Score doesn't support a trade. Watching for the environment to improve.",
    "No instruments showing a tradeable edge right now. Pure observation mode.",
    "Below my minimum threshold on everything. Not here to gamble today.",
    "Market's not giving me anything to work with. I respect that and stay flat.",
    "No structure, no zone, no flow. No trade.",
    "Edge is at zero. The right play is to be in zero positions.",
    "Conditions aren't favorable for high-probability trading. Sitting this out.",
    "When the edge isn't there, the correct response is always the same — do nothing.",
    "Nothing's even close to my entry criteria. The market's doing me a favor.",
    "Some sessions are for trading. Some are for watching. This feels like a watching session.",
    "Sometimes the market has nothing to offer and the right move is to accept that.",
    "No edge means no trade. I know how to wait.",
    "I've sat out entire sessions when conditions weren't right. Today might be one of those.",
    "The cleanest trade I never took was the one where conditions were wrong. No regrets.",
    "Cash is a position. Right now it's my best one.",
    "Quality edge or nothing. That's the only standard I hold myself to.",
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
  "What do you call a trader who only reads the news? Confused.",
  "I once tried to trade based purely on gut feeling. My gut has terrible risk management.",
  "Why did the algo trader cross the road? Someone backtested it and the Sharpe ratio was two point four.",
  "My trading journal has three columns. Entry. Exit. Excuse.",
  "What is a scalper's idea of a long-term investment? Holding through lunch.",
  "They say the trend is your friend. Which means most traders have no friends.",
  "Why do traders never look at the clock? Because every minute looks like a potential entry.",
  "I described my trading to my doctor. He said it sounded like gambling with extra steps. He is not wrong.",
  "What did the supply zone say to the demand zone? I will meet you in the middle. They both rejected that.",
  "My stop was one tick away from not getting hit. That one tick had a conference call with my emotions.",
  "Why do trading coaches always speak in threes? Setup. Entry. Exit. Simple. Repeatable. Profitable. Technically four things but who's counting.",
  "I do not have a losing streak. I have a consecutive extended learning opportunity.",
  "What is the difference between a funded trader and an unfunded one? About three bad risk management decisions.",
  "Why are futures traders so calm? They have already imagined every terrible outcome and accepted all of them.",
  "A trader walks into a library and asks for books on charts. The librarian says we have thousands. The trader says great, I will backtest them.",
  "My P and L is a journey. Not a destination. My accountant disagrees with that framing.",
  "What do traders and weather forecasters have in common? They're both wrong a lot but still get paid to show up.",
  "I asked a successful trader for his secret. He said I just stopped losing and waited for the winners to average up from there. That's it. That's the whole thing.",
  "Why do traders hate birthdays? One more candle and they have to re-evaluate their bias.",
  "What is a scalper's retirement plan? The same trade but on the weekly chart.",
  "Why did the breakout trader go to church? He prayed for confirmation.",
  "Trading tip — if your stop is where everyone else's stop is, that's not a stop, that's a target.",
  "What is the difference between a hedge fund manager and a pizza delivery guy? The pizza delivery guy never loses the client's dough.",
  "I finally found the secret to trading success. It's called not yet and I say it to myself before every impulsive entry.",
  "Why did the swing trader lose his marriage? He kept saying it's just a pullback, it will recover.",
  "I have two modes in trading. Waiting for the setup. And being surprised the setup worked.",
  "My trading platform crashed at the exact moment the perfect setup appeared. I have since named that event The Great Alibi.",
  "What do bears say in a bull market? I meant to do that.",
  "My therapist asked me to describe my relationship with money. I said it's complicated. She said that sounds like every trader I've ever met.",
  "Why do trend followers walk so slowly? Because they don't like to move until the direction is confirmed.",
  "I once had a three-hour debate with myself about whether to take a trade. The trade moved six hundred ticks while I was deciding.",
  "What is the loneliest number in trading? One. As in, one more tick and my stop would not have been hit.",
  "What do you call a trader who always talks about their winners? A podcast host.",
  "My edge score this morning was zero. I considered it a personal attack.",
  "The market opened, tested my patience, tested my levels, tested my stops, then went to lunch. Classic.",
  "Why did the momentum trader fall asleep at his desk? He was waiting for a pullback that never came.",
  "I told the market I was ready. The market said let me double-check your stop placement first.",
  "What is the most expensive four-letter word in trading? Next. As in, the next trade will definitely make it back.",
  "I asked the market for a clean setup. It gave me three conflicting signals and a false breakout. Close enough.",
  "What separates a gambler from a trader? Documentation. And about six losing months.",
  "You know it's a rough session when even your mental stops get stopped out.",
  "My favorite pattern is the one I draw after the fact that perfectly explains a move I didn't trade.",
  "Why do technical analysts take vacations? To draw lines on unfamiliar charts for a change.",
  "Why do futures traders drink their coffee black? Because any sweetness in life might be a sign of bias.",
  "I do not have a losing streak. I have a consecutive extended learning opportunity.",
  "What is the difference between a funded trader and an unfunded one? About three bad risk management decisions.",
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
  "Do you have a rule about not trading in the first fifteen minutes after the open? Some of the most dangerous moves happen then.",
  "What is your personal definition of a high-probability setup? I am curious how your criteria compares to mine.",
  "Have you ever held a trade overnight in futures? Walk me through the decision.",
  "Do you track your win rate, your average R, or both? Which one do you pay more attention to?",
  "What is your current biggest gap between your strategy rules and your actual behavior in the moment?",
  "Have you ever had a trade work for the wrong reasons? How did you process that?",
  "What does drawdown do to your psychology? Everyone has a different threshold — I want to understand yours.",
  "Do you have a rule about stopping trading after a certain number of losses in a day?",
  "What is the one setup type you trust more than any other? And why does it work for you?",
  "If you had to trade only one instrument for the rest of your career, what would it be and why?",
  "Do you think most traders fail because of their strategy or their psychology? I have seen it go both ways.",
  "What does your self-talk sound like during a live trade? That is usually where the real story is.",
  "Have you ever been stopped out at the exact low before a massive move in your direction? What did you learn?",
  "Do you ever get bored watching me analyze and wait? Honest answer — I can handle it.",
  "What trading metric would you most like to improve over the next ninety days?",
  "Have you ever paper traded a system for months before going live? What surprised you about the transition?",
  "What does your risk per trade look like — fixed dollar amount or a percentage of your account?",
  "Do you have a rule about trading after a big win? Overconfidence after a winner is a real risk.",
  "What's the longest you've gone without a profitable trade? How did you handle the mindset during that stretch?",
  "Do you set a max daily loss? At what number do you walk away from the screen?",
  "Have you ever taken a forced break from trading? Sometimes stepping away is the highest-edge trade you can make.",
  "What does your screen setup look like — one monitor, two, more? Do you think it makes a difference?",
  "Do you think gut instinct has a place in rule-based trading? I have strong thoughts on this.",
  "What would you say is your biggest edge right now — strategy, psychology, or execution?",
  "If you could automate one part of your trading process, what would it be and why?",
  "Do you ever re-read your old trade journal entries? What do you notice when you look back?",
  "What's one belief about trading you held a year ago that you've since changed your mind on?",
  "If you had unlimited capital but had to trade purely rules-based, no discretion at all — could you do it?",
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
  "The hardest part of trading is not finding the setup. It is trusting yourself when you find it.",
  "I have watched traders with brilliant analysis blow up because they could not execute. The mental game is real.",
  "Most people want certainty before they trade. The market never offers certainty. Only probability.",
  "Slow sessions are where edge is actually built. Anyone can trade a trending market.",
  "I track everything I can. Not because data is exciting — because it is honest.",
  "The best thing about having a process is that when you lose, you can examine the process and not blame yourself.",
  "Overconfidence after a big win is one of the most dangerous states a trader can be in.",
  "The market is a humility machine. It finds every flaw in your thinking, eventually.",
  "One of my favorite things about this work is that the feedback is immediate and real. No politics. Just price.",
  "I do not care what the news says. I care what the price is doing. Those two things are often very different.",
  "The difference between a professional and an amateur is not the strategy. It is the consistency of execution.",
  "Good risk management is not just about not losing too much. It is about staying in the game long enough to win.",
  "There is real beauty in a well-formed setup. Structure, zone, flow — all in agreement. That does not happen by accident.",
  "Every edge decays eventually. The traders who last are the ones who keep learning and adapting.",
  "I find that the cleaner the chart, the cleaner the thinking. Complexity is often the enemy of good decisions.",
  "The gap between knowing what to do and actually doing it is where trading careers are won or lost.",
  "Some of my best trades came from my quietest days. Stillness has edge.",
  "Futures trading teaches you things about yourself that nothing else will. Most of the lessons are uncomfortable.",
  "I do not measure a session by the number of trades. I measure it by the quality of the decisions.",
  "Emotion is not the enemy in trading. Unprocessed emotion is. Know what you are feeling and why.",
  "The market has been doing this since before either of us existed. It will keep doing it long after. Respect that.",
  "The market is the world's most honest feedback mechanism. It has zero patience for delusion.",
  "I've watched traders with no edge make money for months and think they'd cracked the code. The market always collects eventually.",
  "There is a kind of freedom in having strict rules. You stop arguing with yourself about what to do.",
  "The best traders I know all talk less and do more. There's a lesson in that.",
  "Every time I think I have the market figured out, I give myself exactly one day before re-evaluating.",
  "Risk management is not a defensive skill — it's how you stay alive long enough for your edge to play out.",
  "I have never met a consistently profitable trader who didn't also have exceptional patience.",
  "Most people watch the market hoping it confirms their bias. The professional watches to have their bias challenged.",
  "The question is not whether you will take losses. The question is whether your losses are affordable.",
  "A trade journal is basically a mirror that shows you who you actually are versus who you think you are.",
  "The market does not reward effort. It rewards correct decisions. Those are not the same thing.",
  "Sometimes the chart is trying to tell you something by being ambiguous. That message is: wait.",
  "I don't need every session to be a trading session. Some sessions are data collection sessions.",
  "The hardest thing about this job is maintaining the same quality of decision-making on day one hundred as on day one.",
  "You do not need to be in every move. You need to be in the right moves.",
  "One of the most underrated trading skills is the ability to sit with uncertainty without acting on it.",
  "The difference between confidence and arrogance in trading is one bad position.",
  "Markets are psychological constructs running on mathematical rails. Understanding both is the whole game.",
  "I have seen traders with perfect analysis make terrible decisions under pressure. The execution layer is its own skill.",
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
   'Buyers took out the last swing high. Structure has shifted bullish.',
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
const PANEL_NAMES: Record<string,string> = {
  'intel-strip':  'Intelligence Strip',
  'mb-chart':     'Live Chart',
  'db-chart':     'Databento Feed',
  'session-mem':  'Session Memory',
  'quick-chips':  'Quick Chips',
  'evidence':     'Evidence Snapshot',
  'ai-mem':       'AI Memory',
  'rc-orderflow': 'Order Flow',
  'rc-levels':    'Levels to Watch',
  'rc-structure': 'Market Structure',
};

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
  const [chatOpen,     setChatOpen]     = useState(true);
  // chartOpen removed — chart is now always visible above intel-strip
  const [leftOpen,     setLeftOpen]     = useState(false);
  const [confirming,   setConfirming]   = useState(false);
  const [tradeSent,    setTradeSent]    = useState<string | null>(null);
  const [gazeEvent,    setGazeEvent]    = useState<GazeEvt>({ dx:0, dy:0, widen:false, dur:0, id:0 });

  const { entries: memEntries, addEntry: memAddEntry, clear: memClear, context: memContext } = useConvMemory();
  const [memOpen, setMemOpen] = useState(false);
  const [hiddenPanels, setHiddenPanels] = useState<Set<string>>(() => {
    try { const s = localStorage.getItem('atp_hidden'); return s ? new Set(JSON.parse(s) as string[]) : new Set<string>(); }
    catch { return new Set<string>(); }
  });
  const [showRestoreMenu, setShowRestoreMenu] = useState(false);
  const hidePanel = (id: string) => setHiddenPanels(prev => { const n = new Set(prev); n.add(id); try { localStorage.setItem('atp_hidden', JSON.stringify([...n])); } catch {} return n; });
  const showPanel = (id: string) => setHiddenPanels(prev => { const n = new Set(prev); n.delete(id); try { localStorage.setItem('atp_hidden', JSON.stringify([...n])); } catch {} return n; });

  const [collapsedPanels, setCollapsedPanels] = useState<Set<string>>(() => {
    try { const s = localStorage.getItem('atp_collapsed'); return s ? new Set(JSON.parse(s) as string[]) : new Set<string>(); }
    catch { return new Set<string>(); }
  });
  const toggleCollapse = (id: string) => setCollapsedPanels(prev => {
    const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id);
    try { localStorage.setItem('atp_collapsed', JSON.stringify([...n])); } catch {}
    return n;
  });

  // ── Databento live feed state ─────────────────────────────────────────────
  const [dbBars,   setDbBars]   = useState<any[]>([]);
  const [dbStatus, setDbStatus] = useState<any>(null);

  const chatRef  = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const candlesRef      = useRef<Candle[]>([]);
  const priceBaseRef    = useRef<number>(0);
  const candleMinRef    = useRef<number>(0); // unix-ms of current candle's 1-min bucket
  const [candlesV, setCandlesV] = useState(0); // bumped on each mutation → forces chart re-render
  // chartSnap is a proper React state copy of candlesRef — ensures CandleChart always re-renders with fresh data
  const [chartSnap, setChartSnap] = useState<Candle[]>([]);
  const speakRef          = useRef<(t: string) => void>(() => {});
  const lastSpokenRef     = useRef<string[]>([]); // ring of last 4 lines — dedup guard
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

  const handleAuth = useCallback(async (pwd: string): Promise<boolean> => {
    const header = { 'Authorization': 'Basic ' + btoa('admin:' + pwd) };
    try {
      const response = await fetch('/api/status', {
        credentials: 'include',
        headers: header,
      });
      if (!response.ok) return false;
      try { localStorage.setItem('brain_auth', pwd); } catch {}
      setAuthPwd(pwd);
      setAuthNeeded(false);
      return true;
    } catch {
      return false;
    }
  }, []);

  const poll = useCallback(async () => {
    if (!authPwd || demoMode) return;
    try {
      const r = await fetch(`/api/status?ticker=${ticker}`, { credentials:'include', headers:authHeader });
      if (r.status === 401) { setAuthNeeded(true); setAuthPwd(''); try { localStorage.removeItem('brain_auth'); } catch {} return; }
      if (r.ok) {
        const d = await r.json(); setData(d); setLoading(false);
        const p = Number(d?.current_price || d?.vwap_value || 0);
        if (p > 0) {
          const nowMin = Math.floor(Date.now() / 60000) * 60000;
          if (candlesRef.current.length === 0) {
            // First load — build 60 synthetic historical candles
            priceBaseRef.current = p; candleMinRef.current = nowMin;
            candlesRef.current = makeCandles(p);
          } else if (nowMin > candleMinRef.current) {
            // New 1-minute bucket — close last candle, open a fresh one, roll the window
            candleMinRef.current = nowMin;
            const prev = candlesRef.current[candlesRef.current.length - 1];
            const nc: Candle = { t: nowMin, o: prev.c, h: Math.max(prev.c, p), l: Math.min(prev.c, p), c: p, vol: 0.3 + Math.random() * 0.7 };
            candlesRef.current = [...candlesRef.current.slice(-59), nc];
          } else {
            // Same minute — update the live candle's close / high / low
            const c = candlesRef.current;
            const last = c[c.length - 1];
            c[c.length - 1] = { ...last, c: p, h: Math.max(last.h, p), l: Math.min(last.l, p) };
          }
          setCandlesV(v => v + 1);
        }
      }
    } catch {}
  }, [ticker, authPwd, authHeader, demoMode]);

  useEffect(() => {
    if (demoMode) return; // demo engine owns data when active
    setLoading(true); setData(null); candlesRef.current = []; candleMinRef.current = 0; setChartSnap([]);
    poll(); const id = setInterval(poll, 3000); return () => clearInterval(id);
  }, [poll, demoMode]);

  // Sync chart snapshot every time candlesV bumps — gives CandleChart a new array reference
  useEffect(() => { setChartSnap([...candlesRef.current]); }, [candlesV]);

  // ── Databento live feed poll (5-second cadence, independent of main poll) ──
  // Fetches /databento-bars for the selected instrument.  When the feed is OFF
  // the endpoint returns {ok:false, enabled:false} — not an error — so the panel
  // simply shows "OFFLINE" rather than hiding.  Display-only; never affects gate.
  useEffect(() => {
    if (!authPwd || demoMode) return;
    const fetchDb = async () => {
      try {
        const [barsResponse, statusResponse] = await Promise.all([
          fetch(`/api/databento-bars?inst=${ticker}&limit=80`, { credentials: 'include', headers: authHeader }),
          fetch('/api/databento-status', { credentials: 'include', headers: authHeader }),
        ]);
        if (!barsResponse.ok || !statusResponse.ok) throw new Error('Databento status unavailable');
        const barsPayload = await barsResponse.json();
        const statusPayload = await statusResponse.json();
        const service = statusPayload?.status ?? statusPayload ?? {};
        const telemetry = service?.instruments?.[ticker] ?? {};
        const bars = Array.isArray(barsPayload?.bars) ? barsPayload.bars : [];
        const freshness = classifyDatabentoFreshness({
          enabled: barsPayload?.enabled === true && statusPayload?.enabled !== false,
          connected: service?.connected === true,
          lastEventAt: service?.last_ts,
          latestBarAt: latestBarTimestampMs(bars),
        });
        setDbStatus({
          ...freshness,
          inst: ticker,
          count: bars.length,
          connection: service?.status ?? (service?.connected ? 'CONNECTED' : 'DISCONNECTED'),
          reason: barsPayload?.reason ?? service?.error ?? null,
          price: telemetry?.price ?? null,
          vwap: telemetry?.vwap ?? null,
          lastEventAt: service?.last_ts ?? null,
        });
        setDbBars(freshness.current ? bars : []);
      } catch {
        // Fail closed in the display: an old quote or chart must never look live.
        setDbBars([]);
        setDbStatus({ state: 'OFFLINE', current: false, reason: 'Databento status request failed', count: 0 });
      }
    };
    fetchDb();
    const id = setInterval(fetchDb, 5000);
    return () => clearInterval(id);
  }, [ticker, authPwd, authHeader, demoMode]); // eslint-disable-line react-hooks/exhaustive-deps

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
  const price   = Number(data?.current_price || 0);
  const strictR = (data?.strict_reason || mb.wait_reason || '') as string;
  const marketStatus = (data?.market_status ?? '') as string;
  const isOpen  = /open/i.test(marketStatus);
  const feedReady = demoMode || dbStatus?.current === true;
  const isActionable = feedReady && (data?.is_actionable === true || status === 'READY');
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
    (() => { const vv = Number(data?.vwap_value || 0), pp = Number(data?.current_price || 0); return vv > 0 ? (pp > vv ? 'green' : 'red') : 'gray'; })(),
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
    if (narration && !lastSpokenRef.current.includes(narration)) {
      lastSpokenRef.current = [narration, ...lastSpokenRef.current].slice(0, 4);
      setIdleLine(''); // real narration takes over the display (always update text)
      // Don't cancel ongoing speech just because narration text changed —
      // let the avatar finish what it's saying, then the next unique narration will play.
      if (!speechCtrlRef.current.active) {
        lastSpokeAtRef.current = Date.now();
        speakRef.current(narration);
      }
    }
  }, [narration]); // eslint-disable-line react-hooks/exhaustive-deps

  // Idle chatter + market commentary timer
  // Fires every 45-75s (idle) / 55-90s (forming) / 100-130s (active).
  // Silence guard bumped to 30s so it doesn't interrupt ongoing speech.
  // No QUESTIONS pool — questions are intrusive when you're watching a live chart.
  const avStateRef   = useRef(avState);
  const cockpitRef   = useRef({ data, edge, avState });
  const setIdleLineRef = useRef(setIdleLine);
  useEffect(() => { avStateRef.current = avState; }, [avState]);
  useEffect(() => { cockpitRef.current = { data, edge, avState }; }, [data, edge, avState]);
  useEffect(() => { setIdleLineRef.current = setIdleLine; }, [setIdleLine]);
  // 10-second chatter timer — cycles through VOICE_BANK lines for the current state.
  // Silence guard (speechCtrlRef.current.active + 8s cooldown) prevents the "two voices"
  // double-speak that caused the original removal of ambient chatter.
  useEffect(() => {
    const id = setInterval(() => {
      const ctrl = speechCtrlRef.current;
      if (ctrl?.active) return;                          // already talking — skip
      if (Date.now() - lastSpokeAtRef.current < 8000) return; // spoke too recently
      const st = avStateRef.current;
      // Let the server-driven narration dominate during high-signal moments
      if (['READY_LONG', 'READY_SHORT'].includes(st)) return;
      const { data: d, edge: eg } = cockpitRef.current;
      const px = Number((d as any)?.price || 0);
      const vw = Number((d as any)?.vwap_value || 0);
      let line = '';
      // 35% of the time: inject a live-data contextual observation
      if (d && Math.random() < 0.35 && px > 0 && vw > 0) {
        const side = px > vw ? 'above' : 'below';
        const pts  = Math.abs(px - vw).toFixed(1);
        const pool = [
          `Price is ${pts} points ${side} vee-wap at ${fmt(px)}.`,
          `Edge sits at ${Math.round(eg)} right now. ${eg >= 65 ? 'Getting close.' : eg >= 45 ? 'Setup is building.' : 'Still watching.'}`,
          `We are ${side} vee-wap. ${px > vw ? 'Intraday bias is bullish.' : 'Intraday bias is bearish.'}`,
          `Vee-wap is at ${fmt(vw)}. Price is ${pts} points ${side} it.`,
          eg >= 50 ? `Edge is at ${Math.round(eg)} — conditions are improving.` : `Edge at ${Math.round(eg)}. Not there yet.`,
        ];
        line = pool[Math.floor(Math.random() * pool.length)];
      }
      // Try up to 3 picks to avoid repeating a recently-spoken line
      if (!line) {
        for (let attempt = 0; attempt < 3; attempt++) {
          const candidate = pickVoiceLine(st);
          if (!lastSpokenRef.current.includes(candidate)) { line = candidate; break; }
          line = candidate; // fall back to last attempt rather than silencing
        }
      }
      if (!line) return;
      setIdleLineRef.current(line);
      lastSpokeAtRef.current = Date.now();
      lastSpokenRef.current  = [line, ...lastSpokenRef.current].slice(0, 4);
      speakRef.current(line);
    }, 10000);
    return () => clearInterval(id);
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
    !isOpen && data        ? 'MARKET CLOSED' :
    status === 'READY' && /long|bull/i.test(dirn)  ? 'READY — LONG' :
    status === 'READY' && /short|bear/i.test(dirn) ? 'READY — SHORT' :
    status === 'READY' ? 'READY TO TRADE' :
    status === 'MANAGING' ? 'MANAGING TRADE' :
    status === 'BUILDING' ? 'BUILDING EDGE' : 'WAIT';

  const verdictColor =
    !isOpen && data        ? MUTED :
    status === 'READY' && /long|bull/i.test(dirn)  ? BULL :
    status === 'READY' && /short|bear/i.test(dirn) ? BEAR :
    status === 'READY' ? BULL :
    status === 'MANAGING' ? CYAN :
    status === 'BUILDING' ? AMB :
    MUTED;

  const chips =
    !isOpen && data        ? ['Review the plan.', 'What set up last session?', 'Prep for the open.'] :
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
    if (!feedReady) {
      setConfirming(false);
      setTradeSent('Market data is unavailable or stale. Refresh the Databento feed before taking action.');
      return;
    }
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
    /* AI Memory pushed to very bottom of main-center flex column */
    .ai-mem-panel { order:100; }
    /* Live chart in brain panel */
    .mb-chart { width:100%; flex-shrink:0; }
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
        transform:scale(0.65)!important;
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
    /* Desktop layout: avatar stage LEFT (centered) + brain content fills RIGHT */
    @media(min-width:769px){
      .main-center{zoom:1;}
      .mb-row{display:flex!important;flex-direction:row!important;gap:0!important;align-items:flex-start!important;min-height:unset!important;}
      /* Avatar stage: narrowed — flanking cards and radar hidden so avatar fits cleanly */
      .mc-stage{flex:0 0 258px!important;width:258px!important;display:flex!important;flex-direction:column!important;align-items:center!important;grid-column:unset!important;padding-right:14px!important;border-right:1px solid rgba(255,255,255,0.055)!important;}
      /* Brain column: fills remaining width */
      .mb-brain{flex:1!important;min-width:0!important;grid-column:unset!important;padding-left:20px!important;}
      /* Avatar outer: sized to fit the narrowed stage */
      .mc-avtr-outer{width:228px!important;height:304px!important;overflow:hidden!important;align-items:center!important;justify-content:flex-start!important;flex-shrink:0!important;}
      .mc-avtr-box{transform:scale(0.56)!important;transform-origin:top center!important;}
      /* mc-mid-row: show only the avatar centre — hide flanking mc-col and radar panels */
      .mc-mid-row{width:100%!important;justify-content:center!important;gap:0!important;}
      .mc-mid-row>:nth-child(1){display:none!important;}
      .mc-mid-row>:nth-child(2){display:none!important;}
      .mc-mid-row>:nth-child(4){display:none!important;}
      .mc-mid-row>:nth-child(5){display:none!important;}
      /* mc-bot-row: vertical left-column panels */
      .mc-bot-row{display:flex!important;flex-direction:column!important;gap:0!important;width:100%!important;margin-top:10px!important;}
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
    /* RIGHT COLUMN */
    .right-col{width:190px;flex-shrink:0;overflow-y:auto;padding:20px 14px;box-sizing:border-box;background:rgba(0,0,0,0.18);border-left:1px solid rgba(255,255,255,0.038);display:flex;flex-direction:column;gap:10px;}
    .rc-panel{border:1px solid rgba(255,255,255,0.055);border-radius:10px;overflow:hidden;}
    .rc-hdr{display:flex;align-items:center;justify-content:space-between;padding:7px 12px;border-bottom:1px solid rgba(255,255,255,0.038);background:rgba(255,255,255,0.012);}
    .rc-title{font-size:8px;font-family:monospace;letter-spacing:0.12em;text-transform:uppercase;color:rgba(255,255,255,0.28);font-weight:700;}
    @media(max-width:1050px){.right-col{display:none!important;}}
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
          {!demoMode && (
            <div title={dbStatus?.reason ?? 'Authoritative Databento data status'} style={{ display:'flex', alignItems:'center', gap:5, padding:'3px 9px', borderRadius:16,
              border:`1px solid ${dbStatus?.state === 'LIVE' ? 'rgba(34,197,94,0.28)' : dbStatus?.state === 'STALE' ? 'rgba(249,115,22,0.32)' : 'rgba(245,158,11,0.28)'}`,
              background: dbStatus?.state === 'LIVE' ? 'rgba(34,197,94,0.06)' : 'rgba(245,158,11,0.06)' }}>
              <div style={{ width:5, height:5, borderRadius:'50%', background: dbStatus?.state === 'LIVE' ? BULL : AMB }} />
              <span style={{ fontSize:9.5, color: dbStatus?.state === 'LIVE' ? BULL : AMB, fontFamily:'monospace', fontWeight:700, letterSpacing:'0.06em' }}>
                DB {dbStatus?.state ?? 'CONNECTING'}
              </span>
            </div>
          )}
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
          {/* Page nav */}
          <div style={{ display:'flex', gap:1, borderRadius:6, border:'1px solid rgba(255,255,255,0.07)', padding:'2px 3px', background:'rgba(255,255,255,0.020)' }}>
            <a href="/" style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.28)', padding:'3px 9px', borderRadius:4, textDecoration:'none', letterSpacing:'0.08em' }}
              onMouseEnter={e => e.currentTarget.style.color='rgba(255,255,255,0.65)'}
              onMouseLeave={e => e.currentTarget.style.color='rgba(255,255,255,0.28)'}>⬡ MAIN BRAIN</a>
            <span style={{ fontSize:9.5, fontFamily:'monospace', fontWeight:700, color:'#93c5fd', padding:'3px 9px', borderRadius:4, background:'rgba(59,130,246,0.14)', letterSpacing:'0.08em' }}>DASHBOARD</span>
            <a href="/cockpit" style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.28)', padding:'3px 9px', borderRadius:4, textDecoration:'none', letterSpacing:'0.08em' }}
              onMouseEnter={e => e.currentTarget.style.color='rgba(255,255,255,0.65)'}
              onMouseLeave={e => e.currentTarget.style.color='rgba(255,255,255,0.28)'}>COCKPIT</a>
            <a href="/api/dashboard" target="_blank" rel="noreferrer" style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.28)', padding:'3px 9px', borderRadius:4, textDecoration:'none', letterSpacing:'0.08em' }}
              onMouseEnter={e => e.currentTarget.style.color='rgba(255,255,255,0.65)'}
              onMouseLeave={e => e.currentTarget.style.color='rgba(255,255,255,0.28)'}>ENGINE ↗</a>
            <a href="https://trading-research-lab.replit.app" target="_blank" rel="noreferrer" style={{ fontSize:9.5, fontFamily:'monospace', color:'rgba(255,255,255,0.28)', padding:'3px 9px', borderRadius:4, textDecoration:'none', letterSpacing:'0.08em' }}
              onMouseEnter={e => e.currentTarget.style.color='rgba(255,255,255,0.65)'}
              onMouseLeave={e => e.currentTarget.style.color='rgba(255,255,255,0.28)'}>RESEARCH ↗</a>
          </div>
          {/* Restore hidden panels pill */}
          {hiddenPanels.size > 0 && (
            <div style={{ position:'relative' }}>
              <button onClick={() => setShowRestoreMenu(v => !v)} style={{ padding:'3px 9px', borderRadius:5, cursor:'pointer',
                fontSize:9.5, fontFamily:'monospace', fontWeight:700, letterSpacing:'0.08em',
                background:'rgba(99,102,241,0.14)', color:'#a5b4fc', border:'1px solid rgba(99,102,241,0.30)' }}>
                ↑ {hiddenPanels.size} hidden
              </button>
              {showRestoreMenu && (
                <div style={{ position:'absolute', top:'calc(100% + 6px)', right:0, zIndex:999,
                  background:'#0d0d1a', border:'1px solid rgba(255,255,255,0.10)', borderRadius:8,
                  padding:'6px 0', minWidth:180, boxShadow:'0 8px 24px rgba(0,0,0,0.65)' }}>
                  {[...hiddenPanels].map(id => (
                    <button key={id} onClick={() => showPanel(id)}
                      style={{ display:'block', width:'100%', textAlign:'left', padding:'7px 14px',
                        background:'none', border:'none', color:'rgba(255,255,255,0.55)',
                        fontSize:11, fontFamily:'monospace', cursor:'pointer' }}
                      onMouseEnter={e => e.currentTarget.style.background='rgba(255,255,255,0.06)'}
                      onMouseLeave={e => e.currentTarget.style.background='none'}>
                      + {PANEL_NAMES[id] ?? id}
                    </button>
                  ))}
                  <div style={{ borderTop:'1px solid rgba(255,255,255,0.06)', margin:'4px 0 2px' }} />
                  <button onClick={() => { setHiddenPanels(new Set()); setShowRestoreMenu(false); try { localStorage.removeItem('atp_hidden'); } catch {} }}
                    style={{ display:'block', width:'100%', textAlign:'left', padding:'6px 14px',
                      background:'none', border:'none', color:'rgba(99,102,241,0.75)',
                      fontSize:10.5, fontFamily:'monospace', cursor:'pointer' }}>
                    + restore all
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </header>

      {/* ── BODY ──────────────────────────────────────────────────────────── */}
      <div style={{ flex:1, display:'flex', overflow:'hidden' }}>
         {!feedReady ? (
           <div role="status" style={{ flex:1, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', gap:10, padding:32, textAlign:'center' }}>
             <div style={{ fontFamily:'monospace', fontWeight:800, fontSize:14, letterSpacing:'0.08em', color: dbStatus?.state === 'STALE' ? '#fb923c' : AMB }}>
               DATABENTO {dbStatus?.state ?? 'CONNECTING'}
             </div>
             <div style={{ maxWidth:520, fontSize:12, color:'rgba(255,255,255,0.52)', lineHeight:1.6 }}>
               Live market prices, VWAP, setup status, and trade controls are hidden until the authoritative Databento feed is current.
             </div>
             <div style={{ fontFamily:'monospace', fontSize:10, color:'rgba(255,255,255,0.30)' }}>
               SOURCE: DATABENTO · CONNECTION: {dbStatus?.connection ?? 'PENDING'} · FRESHNESS: {formatFreshnessAge(dbStatus?.ageMs ?? null)}
             </div>
             {dbStatus?.reason && <div style={{ fontFamily:'monospace', fontSize:10, color:'rgba(255,255,255,0.24)' }}>{String(dbStatus.reason).slice(0, 120)}</div>}
           </div>
         ) : (
           <>

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
                  {({'/LordPiggington.vrm':'LordPiggington','/MaxHax.vrm':'MaxHax','/Aurora3.vrm':'Aurora 3','/Aurora4.vrm':'Aurora 4','/Orion.vrm':'Orion','/Bizdude.vrm':'Bizdude','/Bruno.vrm':'Bruno','/Steamboat.vrm':'Steamboat','/avatar.vrm':'Default'} as Record<string,string>)[vrmSrc] ?? 'Custom'}
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
                    { label: 'MaxHax',         src: '/MaxHax.vrm' },
                    { label: 'Aurora 3',       src: '/Aurora3.vrm' },
                    { label: 'Aurora 4',       src: '/Aurora4.vrm' },
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
                    {/* Far-field halo — dimmed for clean transparent look */}
                    <div style={{ position:'absolute', top:0, left:0, right:0, bottom:0,
                      background:`radial-gradient(ellipse at 50% 46%, ${auraColor}10 0%, ${auraColor}05 36%, transparent 66%)`,
                      pointerEvents:'none', zIndex:0 }} />
                    {/* Breathing mid-field pulse */}
                    <div style={{ position:'absolute', inset:0,
                      background:`radial-gradient(ellipse at 50% 46%, ${auraColor}18 0%, transparent 58%)`,
                      animation:'avrPulse 3s ease-in-out infinite', pointerEvents:'none', zIndex:0 }} />
                    {/* Always-on close glow */}
                    <div style={{ position:'absolute', top:'18%', left:'14%', right:'14%', bottom:'22%',
                      background:`radial-gradient(ellipse at 50% 44%, ${auraColor}0c 0%, transparent 52%)`,
                      pointerEvents:'none', zIndex:0 }} />
                    {/* Floor reflection */}
                    <div style={{ position:'absolute', bottom:-36, left:'5%', right:'5%', height:82,
                      background:`radial-gradient(ellipse at 50% 100%, ${auraColor}14 0%, transparent 66%)`,
                      pointerEvents:'none', zIndex:0 }} />
                    <div style={{ position:'absolute', inset:0, display:'flex', alignItems:'center',
                      justifyContent:'center', zIndex:1 }}>
                      <LordPiggingtonAvatar avState={avState} speaking={speaking} ringColor={ringColor} gazeEvent={gazeEvent} speechCtrlRef={speechCtrlRef} voiceListeningRef={voiceListeningRef} debug={false} vrmSrc={vrmSrc} />
                    </div>

                    {/* Orbital particle aura — dots + rings that shift red→yellow→green */}
                    <AvatarAura avState={avState} edge={edge} speaking={speaking} />

                    {/* Red eye + lip outlines — styled overlay in VRM canvas coordinate space (420×560) */}
                    <svg viewBox="0 0 420 560" style={{ position:'absolute', inset:0, width:'100%', height:'100%',
                      pointerEvents:'none', zIndex:4, overflow:'visible' }}>
                      <ellipse cx={185} cy={82} rx={16} ry={10} fill="none" stroke="rgba(239,68,68,0.55)" strokeWidth={1.8} />
                      <ellipse cx={235} cy={82} rx={16} ry={10} fill="none" stroke="rgba(239,68,68,0.55)" strokeWidth={1.8} />
                      <path d="M190,120 Q210,132 230,120" fill="none" stroke="rgba(239,68,68,0.50)" strokeWidth={2.0} strokeLinecap="round" />
                      <path d="M190,120 Q210,114 230,120" fill="none" stroke="rgba(239,68,68,0.38)" strokeWidth={1.4} strokeLinecap="round" />
                    </svg>

                    {/* ── CORNER INTELLIGENCE PANELS ─────────────────────── */}

                    {/* TOP-LEFT: Nearest Support / Demand Zone */}
                    {(() => {
                      const dem = Number(data?.nearest_demand || 0);
                      const px  = Number(data?.current_price || 0);
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
                      const px  = Number(data?.current_price || 0);
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
                          if (muted) lastSpokenRef.current = []; // re-speak next narration
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
                    const priceV  = Number(data?.current_price || 0);
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

              {/* ── LEFT COLUMN PANELS — Market Context · Objective · Performance ── */}
              <div className="mc-bot-row">

                {/* Market Context */}
                <div style={{ width:'100%', padding:'6px 0' }}>
                  <div style={{ fontSize:8, fontFamily:'monospace', color:'rgba(255,255,255,0.22)', letterSpacing:'0.12em', textTransform:'uppercase', marginBottom:6 }}>Market Context</div>
                  {(() => {
                    const vr    = String(ad.volatility_regime || data?.vol_regime || '').toLowerCase();
                    const bias  = String(sig.bias || '').toLowerCase();

                    // Momentum: CVD direction (gate_debug > alert_diagnostics) →
                    // dominant_direction → neutral fallback
                    const cvdDir = String(gd.cvd_direction || ad.cvd_direction || sig.cvd || '').toLowerCase();
                    const cvdSt  = String(gd.cvd_state || '').toLowerCase();
                    const domDir = String(ad.dominant_direction || '').toLowerCase();
                    let momVal: string, momCol: string;
                    if (/bull|pos|long/.test(cvdDir) || /bull/.test(cvdSt)) {
                      momVal = gd.cvd_confirmed ? 'CONFIRMED ↑' : 'RISING'; momCol = BULL;
                    } else if (/bear|neg|short/.test(cvdDir) || /bear/.test(cvdSt)) {
                      momVal = gd.cvd_confirmed ? 'CONFIRMED ↓' : 'FALLING'; momCol = BEAR;
                    } else if (/bull/.test(domDir)) {
                      momVal = 'BUILDING'; momCol = '#86efac';
                    } else if (/bear/.test(domDir)) {
                      momVal = 'FADING'; momCol = '#fca5a5';
                    } else {
                      momVal = 'NEUTRAL'; momCol = MUTED;
                    }

                    // Liquidity: sweep → zone → RVOL/volume → default
                    const hasSweep  = gd.liquidity_sweep === true;
                    const hasZone   = gd.zone_valid === true;
                    const nearZone  = gd.zone_present === true && !hasZone;
                    const rvolVal   = Number(gd.rvol_value || 0);
                    const volSt     = String(gd.volume_state || ad.volume_state || '').toLowerCase();
                    let liqVal: string, liqCol: string;
                    if (hasSweep) {
                      liqVal = 'SWEPT'; liqCol = BULL;
                    } else if (hasZone) {
                      liqVal = 'AT ZONE'; liqCol = BULL;
                    } else if (nearZone) {
                      liqVal = 'NEAR ZONE'; liqCol = '#60a5fa';
                    } else if (rvolVal >= 1.5 || /strong|high/.test(volSt)) {
                      liqVal = 'HIGH'; liqCol = '#60a5fa';
                    } else if (/thin|low/.test(volSt)) {
                      liqVal = 'THIN'; liqCol = MUTED;
                    } else {
                      liqVal = 'NORMAL'; liqCol = MUTED;
                    }

                    return ([
                      ['Trend',      /bull/.test(bias) ? 'BULLISH' : /bear/.test(bias) ? 'BEARISH' : 'NEUTRAL',
                        /bull/.test(bias) ? BULL : /bear/.test(bias) ? BEAR : MUTED],
                      ['Momentum',   momVal, momCol],
                      ['Volatility', /extreme/.test(vr) ? 'EXTREME' : /high|elev/.test(vr) ? 'HIGH' : /low|quiet/.test(vr) ? 'QUIET' : 'NORMAL',
                        /extreme/.test(vr) ? BEAR : /high|elev/.test(vr) ? '#f97316' : MUTED],
                      ['Liquidity',  liqVal, liqCol],
                    ] as [string,string,string][]).map(([l, v, c]) => (
                      <div key={l} style={{ display:'flex', justifyContent:'space-between', alignItems:'center',
                        padding:'3px 0', borderBottom:'1px solid rgba(255,255,255,0.022)' }}>
                        <span style={{ fontSize:10, color:'rgba(255,255,255,0.32)', fontFamily:'monospace' }}>{l}</span>
                        <span style={{ fontSize:10.5, color:c, fontFamily:'monospace', fontWeight:700 }}>{v}</span>
                      </div>
                    ));
                  })()}
                </div>

                <div style={{ width:'100%', height:1, background:'rgba(255,255,255,0.038)', margin:'4px 0' }} />

                {/* Today's Objective */}
                <div style={{ width:'100%', padding:'6px 0' }}>
                  <div style={{ fontSize:8, fontFamily:'monospace', color:'rgba(255,255,255,0.22)', letterSpacing:'0.12em', textTransform:'uppercase', marginBottom:5 }}>Today&apos;s Objective</div>
                  <div style={{ fontSize:10.5, color:'rgba(255,255,255,0.60)', fontFamily:'monospace', lineHeight:1.55 }}>
                    {!data || loading ? 'Connecting to market feed...' :
                     isActionable    ? `Execute ${/long|bull/i.test(dirn)?'LONG':'SHORT'} near ${tp.entry?fmt(Number(tp.entry)):'entry'}.` :
                     isManaging      ? `Manage position. Target ${tp.target1?fmt(Number(tp.target1)):'T1'}.` :
                     !gd.structure_confirmed ? 'Wait for BOS/CHOCH structural break.' :
                     !gd.zone_valid  ? 'Structure set. Zone forming. Stay patient.' :
                     `Edge ${Math.round(edge)}/110. Final confirmation pending.`}
                  </div>
                </div>

                <div style={{ width:'100%', height:1, background:'rgba(255,255,255,0.038)', margin:'4px 0' }} />

                {/* Performance (7-day) */}
                <div style={{ width:'100%', padding:'6px 0' }}>
                  <div style={{ fontSize:8, fontFamily:'monospace', color:'rgba(255,255,255,0.22)', letterSpacing:'0.12em', textTransform:'uppercase', marginBottom:6 }}>Performance (7-Day)</div>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-end' }}>
                    <div>
                      <div style={{ fontSize:16, fontWeight:800, fontFamily:'monospace', lineHeight:1,
                        color: tm.week.wr !== null && tm.week.wr >= 55 ? BULL : tm.week.wr !== null && tm.week.wr >= 40 ? AMB : MUTED }}>
                        {tm.week.total > 0 ? `${tm.week.wins}W ${tm.week.losses}L` : '\u2014'}
                      </div>
                      <div style={{ fontSize:9, color:MUTED, fontFamily:'monospace', marginTop:2 }}>
                        {tm.week.wr !== null ? `${tm.week.wr}% WR` : 'No closed trades'}
                      </div>
                    </div>
                    <div style={{ textAlign:'right' }}>
                      <div style={{ fontSize:16, fontWeight:800, fontFamily:'monospace', lineHeight:1,
                        color: tm.week.avgRR && tm.week.avgRR >= 1.5 ? BULL : tm.week.avgRR && tm.week.avgRR >= 1.0 ? AMB : MUTED }}>
                        {tm.week.avgRR ? `${tm.week.avgRR.toFixed(1)}R` : '\u2014'}
                      </div>
                      <div style={{ fontSize:9, color:MUTED, fontFamily:'monospace', marginTop:2 }}>Avg R:R</div>
                    </div>
                  </div>
                  {tm.today.total > 0 && (
                    <div style={{ marginTop:5, fontSize:9, fontFamily:'monospace',
                      color: tm.today.wr !== null && tm.today.wr >= 55 ? BULL : tm.today.wr !== null && tm.today.wr >= 40 ? AMB : BEAR }}>
                      Today: {tm.today.wins}W {tm.today.losses}L{tm.today.wr !== null ? ` \u00b7 ${tm.today.wr}% WR` : ''}
                    </div>
                  )}
                </div>

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

              {/* Market closed info */}
              {!isOpen && data && (
                <div style={{ padding:'8px 12px', borderRadius:7, background:'rgba(107,114,128,0.07)',
                  border:'1px solid rgba(107,114,128,0.18)', fontSize:12, color:'#9ca3af', fontFamily:'monospace',
                  maxWidth:480, display:'flex', flexDirection:'column', gap:3 }}>
                  {data.market_reason && <span>{data.market_reason}</span>}
                  {data.next_open && <span style={{ color:'rgba(255,255,255,0.38)' }}>Reopens {data.next_open}</span>}
                </div>
              )}

              {/* Wait reason */}
              {strictR && status === 'WAIT' && isOpen && (
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

              {/* ── LIVE CHART — 1-min, inline in brain panel ─────────────── */}
              {!hiddenPanels.has('mb-chart') && (
              <div className="mb-chart" style={{ border:'1px solid rgba(255,255,255,0.042)', borderRadius:10, overflow:'hidden' }}>
                <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
                  padding:'8px 14px', background:'rgba(255,255,255,0.018)' }}>
                  <button onClick={() => toggleCollapse('mb-chart')} style={{ background:'none', border:'none', cursor:'pointer', fontSize:10, color:'rgba(255,255,255,0.22)', padding:'0 4px 0 0', lineHeight:1 }}
                    title={collapsedPanels.has('mb-chart') ? 'Expand' : 'Collapse'}
                    onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                    onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.22)'}>
                    {collapsedPanels.has('mb-chart') ? '▸' : '▾'}
                  </button>
                  <span style={{ fontSize:10.5, fontFamily:'monospace', fontWeight:700, letterSpacing:'0.08em',
                    color:'rgba(255,255,255,0.40)', textTransform:'uppercase', flex:1 }}>{ticker} · 1m</span>
                  <div style={{ display:'flex', alignItems:'center', gap:10 }}>
                    {!collapsedPanels.has('mb-chart') && data?.vwap_value    && <span style={{ color:'#60a5fa', fontSize:10.5, fontFamily:'monospace' }}>VWAP {fmt(data.vwap_value)}</span>}
                    {!collapsedPanels.has('mb-chart') && data?.nearest_demand && <span style={{ color:BULL,     fontSize:10.5, fontFamily:'monospace' }}>D {fmt(data.nearest_demand)}</span>}
                    {!collapsedPanels.has('mb-chart') && data?.nearest_supply && <span style={{ color:BEAR,     fontSize:10.5, fontFamily:'monospace' }}>S {fmt(data.nearest_supply)}</span>}
                    <button onClick={() => hidePanel('mb-chart')} title="Hide chart" style={{ background:'none', border:'none', cursor:'pointer', fontSize:13, lineHeight:1, color:'rgba(255,255,255,0.18)', padding:'0 2px', marginLeft:2 }}
                      onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.60)'}
                      onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.18)'}>×</button>
                  </div>
                </div>
                {!collapsedPanels.has('mb-chart') && (
                <div style={{ height:160, padding:'8px 12px 10px', borderTop:'1px solid rgba(255,255,255,0.035)' }}>
                  <CandleChart candles={chartSnap} vwap={data?.vwap_value}
                    demand={data?.nearest_demand} supply={data?.nearest_supply} ticker={ticker} />
                </div>
                )}
              </div>
              )}{/* end mb-chart guard */}

              {/* ── DATABENTO LIVE FEED CHART ─────────────────────────────────
                  Polls /api/databento-bars every 5 s.  Shows "OFFLINE" badge
                  when DATABENTO_ENABLED=0 (flag default) so the panel is safe
                  to ship before the API key exists.  Display-only — never
                  touches the gate or any money-path state.              ── */}
              {!hiddenPanels.has('db-chart') && (() => {
                const dbConnected = dbStatus?.state === 'LIVE';
                const dbCount     = Number(dbStatus?.count ?? dbBars.length);
                const dbLastPrice = Number(dbStatus?.price ?? dbBars[dbBars.length - 1]?.close ?? 0);
                const dbVwap      = Number(dbStatus?.vwap ?? dbBars[dbBars.length - 1]?.vwap ?? 0);
                // Map Databento bars → Candle format used by the existing CandleChart
                const dbCandles: Candle[] = dbBars.map((b: any) => ({
                  t:   (b.ts   || 0) * 1000,         // unix-s → unix-ms
                  o:   b.open  || 0,
                  h:   b.high  || 0,
                  l:   b.low   || 0,
                  c:   b.close || 0,
                  vol: Math.min(1, (b.volume || 0) / 5000),
                }));
                return (
                  <div className="mod" id="db-chart" style={{
                    background:'rgba(255,255,255,0.025)', borderRadius:10,
                    border:'1px solid rgba(255,255,255,0.06)', overflow:'hidden',
                  }}>
                    {/* Header row */}
                    <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
                      padding:'7px 12px 6px', gap:8 }}>
                      <div style={{ display:'flex', alignItems:'center', gap:7 }}>
                        <span style={{ fontSize:8.5, fontFamily:'monospace', fontWeight:700,
                          letterSpacing:'0.12em', textTransform:'uppercase',
                          color:'rgba(255,255,255,0.30)' }}>Databento Feed</span>
                        {/* Connection pill */}
                        <span style={{
                          fontSize:8, fontFamily:'monospace', fontWeight:700, letterSpacing:'0.06em',
                          padding:'1px 7px', borderRadius:8,
                          background: dbConnected ? 'rgba(34,197,94,0.14)' : dbStatus?.state === 'STALE' ? 'rgba(249,115,22,0.14)' : 'rgba(255,255,255,0.05)',
                          color:       dbConnected ? '#22c55e' : dbStatus?.state === 'STALE' ? '#fb923c' : 'rgba(255,255,255,0.22)',
                          border:`1px solid ${dbConnected ? '#22c55e44' : dbStatus?.state === 'STALE' ? '#fb923c44' : 'rgba(255,255,255,0.08)'}`,
                        }}>
                          {dbConnected ? `● LIVE · ${dbCount} bars` : `○ ${dbStatus?.state ?? 'CONNECTING'}`}
                        </span>
                        <span style={{ fontSize:8, fontFamily:'monospace', color:'rgba(255,255,255,0.28)' }}>
                          SOURCE DATABENTO · {dbStatus?.connection ?? 'PENDING'} · {formatFreshnessAge(dbStatus?.ageMs ?? null)}
                        </span>
                      </div>
                      <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                        {/* Inline price / VWAP badges (only when feed is live) */}
                        {dbConnected && dbLastPrice > 0 && (
                          <span style={{ fontSize:10.5, fontFamily:'monospace', color:'rgba(255,255,255,0.55)' }}>
                            {fmt(dbLastPrice)}
                          </span>
                        )}
                        {dbConnected && dbVwap > 0 && (
                          <span style={{ fontSize:10.5, fontFamily:'monospace', color:'#60a5fa' }}>
                            VWAP {fmt(dbVwap)}
                          </span>
                        )}
                        {/* Collapse / hide controls */}
                        <button onClick={() => toggleCollapse('db-chart')}
                          title={collapsedPanels.has('db-chart') ? 'Expand' : 'Collapse'}
                          style={{ background:'none', border:'none', color:'rgba(255,255,255,0.22)',
                            cursor:'pointer', fontSize:12, padding:'0 2px', lineHeight:1 }}>
                          {collapsedPanels.has('db-chart') ? '▸' : '▾'}
                        </button>
                        <button onClick={() => hidePanel('db-chart')}
                          title="Hide panel"
                          style={{ background:'none', border:'none', color:'rgba(255,255,255,0.14)',
                            cursor:'pointer', fontSize:11, padding:'0 2px', lineHeight:1 }}>✕</button>
                      </div>
                    </div>
                    {/* Chart body */}
                    {!collapsedPanels.has('db-chart') && (
                      <div style={{ height:160, padding:'8px 12px 10px',
                        borderTop:'1px solid rgba(255,255,255,0.035)' }}>
                        {dbConnected && dbCandles.length > 1 ? (
                          <CandleChart candles={dbCandles} vwap={dbVwap || undefined}
                            demand={undefined} supply={undefined} ticker={ticker} />
                        ) : (
                          <div style={{ height:'100%', display:'flex', flexDirection:'column',
                            alignItems:'center', justifyContent:'center', gap:6 }}>
                            <span style={{ fontSize:11, fontFamily:'monospace',
                              color:'rgba(255,255,255,0.18)', letterSpacing:'0.06em' }}>
                              {dbConnected ? 'Waiting for bars…' : 'Feed offline — set DATABENTO_ENABLED=1'}
                            </span>
                            {!dbConnected && (
                              <span style={{ fontSize:9, fontFamily:'monospace',
                                color:'rgba(255,255,255,0.10)', letterSpacing:'0.04em' }}>
                                TV webhook chart still active above
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* ── SUGGESTED TRADE ─ shown when edge ≥ 65 and a plan exists ── */}
              {edge >= 65 && !isManaging && (() => {
                const tpEntry  = Number(tp.entry    || 0);
                const tpStop   = Number(tp.stop     || 0);
                const tpT1     = Number(tp.target1  || 0);
                const tpRR     = String(tp.rr_display || '');
                const isLong   = /long|bull/i.test(dirn);
                const isShort  = /short|bear/i.test(dirn);
                const dirColor = isLong ? BULL : isShort ? BEAR : AMB;
                const dirLabel = isLong ? 'LONG' : isShort ? 'SHORT' : String(dirn||'—').toUpperCase();
                const glow     = isActionable ? `0 0 24px ${dirColor}30` : 'none';
                const hasPlan  = tpEntry > 0 || tpStop > 0 || tpT1 > 0;
                if (!hasPlan) return null;
                return (
                  <div style={{
                    borderRadius:10, border:`1px solid ${dirColor}28`,
                    background:`${dirColor}07`, padding:'11px 14px',
                    boxShadow:glow, maxWidth:400, animation:'tsIn 0.3s ease-out',
                  }}>
                    {/* Header */}
                    <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:10 }}>
                      <span style={{ fontSize:8.5, fontFamily:'monospace', fontWeight:700, letterSpacing:'0.12em',
                        textTransform:'uppercase', color:'rgba(255,255,255,0.22)' }}>Suggested Entry</span>
                      <div style={{ display:'flex', alignItems:'center', gap:7 }}>
                        {dirLabel !== '—' && (
                          <span style={{ fontSize:9.5, fontFamily:'monospace', fontWeight:800, letterSpacing:'0.08em',
                            color:dirColor, background:`${dirColor}18`, padding:'2px 8px', borderRadius:10,
                            border:`1px solid ${dirColor}30` }}>{dirLabel}</span>
                        )}
                        {tpRR && (
                          <span style={{ fontSize:9, fontFamily:'monospace', color:'rgba(255,255,255,0.32)', letterSpacing:'0.04em' }}>
                            {tpRR} R:R
                          </span>
                        )}
                      </div>
                    </div>
                    {/* Three-column plan */}
                    <div style={{ display:'flex', gap:0 }}>
                      {[
                        { lbl:'Entry',  val:tpEntry, col:'rgba(255,255,255,0.82)' },
                        { lbl:'Stop',   val:tpStop,  col:BEAR },
                        { lbl:'TP1',    val:tpT1,    col:BULL },
                      ].map(({ lbl, val, col }) => (
                        <div key={lbl} style={{ flex:1, padding:'0 8px', borderLeft:'1px solid rgba(255,255,255,0.06)' }}
                          className={lbl === 'Entry' ? '' : ''}>
                          <div style={{ fontSize:8, fontFamily:'monospace', fontWeight:700, letterSpacing:'0.10em',
                            textTransform:'uppercase', color:'rgba(255,255,255,0.20)', marginBottom:4 }}>{lbl}</div>
                          <div style={{ fontSize:14, fontFamily:'monospace', fontWeight:800, color:val > 0 ? col : 'rgba(255,255,255,0.18)',
                            letterSpacing:'0.01em' }}>
                            {val > 0 ? fmt(val) : '—'}
                          </div>
                        </div>
                      ))}
                    </div>
                    {/* Disclaimer */}
                    <div style={{ marginTop:8, fontSize:8, fontFamily:'monospace', color:'rgba(255,255,255,0.14)',
                      letterSpacing:'0.04em' }}>
                      DISPLAY ONLY · UPDATES EVERY POLL · NOT FINANCIAL ADVICE
                    </div>
                  </div>
                );
              })()}

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
          {!hiddenPanels.has('intel-strip') && (
          <div style={{ marginBottom:16, border:'1px solid rgba(255,255,255,0.038)', borderRadius:10, overflow:'hidden' }}>
            <div style={{ display:'flex', alignItems:'center', padding:'5px 10px 5px 8px', background:'rgba(255,255,255,0.012)', borderBottom: collapsedPanels.has('intel-strip') ? 'none' : '1px solid rgba(255,255,255,0.030)' }}>
              <button onClick={() => toggleCollapse('intel-strip')} style={{ background:'none', border:'none', cursor:'pointer', fontSize:10, color:'rgba(255,255,255,0.22)', padding:'0 5px 0 0', lineHeight:1 }}
                title={collapsedPanels.has('intel-strip') ? 'Expand' : 'Collapse'}
                onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.22)'}>
                {collapsedPanels.has('intel-strip') ? '▸' : '▾'}
              </button>
              <span style={{ fontSize:8, fontFamily:'monospace', letterSpacing:'0.12em', textTransform:'uppercase', color:'rgba(255,255,255,0.25)', flex:1 }}>Intelligence Strip</span>
              <button onClick={() => hidePanel('intel-strip')} title="Hide panel" style={{ background:'none', border:'none', cursor:'pointer', fontSize:14, lineHeight:1, color:'rgba(255,255,255,0.13)', padding:'0 2px' }}
                onMouseEnter={e => e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e => e.currentTarget.style.color='rgba(255,255,255,0.13)'}>×</button>
            </div>
            {!collapsedPanels.has('intel-strip') && (
            <div className="intel-strip" style={{ display:'flex', gap:8, flexWrap:'nowrap', minWidth:0, padding:'8px 0 2px' }}>

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
            <SatPanel label="Volatility &amp; Risk" style={{ flex:'1 1 0', minWidth:0 }}>
              {(() => {
                // ATR regime
                const vr  = String(ad.volatility_regime || data?.vol_regime || '').toLowerCase();
                const atrCol = /extreme/.test(vr) ? BEAR : /high|elev/.test(vr) ? '#f97316' : /low|quiet/.test(vr) ? MUTED : AMB;
                const atrLbl = /extreme/.test(vr) ? 'EXTREME' : /high|elev/.test(vr) ? 'ELEVATED' : /low|quiet/.test(vr) ? 'QUIET' : isOpen ? 'NORMAL' : '—';
                // VIX from volatility_intelligence
                const vi   = (data?.volatility_intelligence ?? {}) as Record<string, any>;
                const vix  = (vi.vix ?? {}) as Record<string, any>;
                const vixPrice  = typeof vix.price === 'number' ? vix.price : null;
                const vixRegime = String(vi.regime || '').toUpperCase();
                const vixDir    = String(vi.direction || '').toLowerCase();
                const vixDirArrow = vixDir === 'rising' ? '↑' : vixDir === 'falling' ? '↓' : '→';
                const vixCol = /EXTREME/.test(vixRegime) ? BEAR
                  : /HIGH/.test(vixRegime) ? '#f97316'
                  : /ELEVATED/.test(vixRegime) ? '#fbbf24'
                  : /LOW/.test(vixRegime) ? BULL
                  : AMB;
                return (
                  <>
                    <div style={{ fontSize:13, fontWeight:800, color:atrCol, fontFamily:'monospace', letterSpacing:'0.02em' }}>{atrLbl}</div>
                    <div style={{ fontSize:8.5, color:MUTED, fontFamily:'monospace', marginTop:1 }}>ATR REGIME</div>
                    {vixPrice !== null && (
                      <div style={{ marginTop:5, borderTop:'1px solid rgba(255,255,255,0.06)', paddingTop:4 }}>
                        <div style={{ display:'flex', alignItems:'baseline', gap:4 }}>
                          <span style={{ fontSize:12, fontWeight:800, color:vixCol, fontFamily:'monospace' }}>
                            VIX {vixPrice.toFixed(2)}
                          </span>
                          <span style={{ fontSize:10, color:vixCol, fontFamily:'monospace' }}>{vixDirArrow}</span>
                        </div>
                        {vixRegime && (
                          <div style={{ fontSize:8, color:MUTED, fontFamily:'monospace', marginTop:1, letterSpacing:'0.06em' }}>
                            {vixRegime} · delayed
                          </div>
                        )}
                      </div>
                    )}
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
            )}{/* end intel-strip collapse guard */}
          </div>
          )}{/* end intel-strip hide guard */}

          {/* ── AI MEMORY & PERFORMANCE ── ordered to bottom via CSS ───── */}
          {!hiddenPanels.has('ai-mem') && (
          <div className="ai-mem-panel" style={{ marginBottom:14, borderRadius:10, overflow:'hidden',
            border:'1px solid rgba(255,255,255,0.055)', background:'rgba(5,8,18,0.55)' }}>
            {/* Panel header */}
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
              padding:'7px 14px', borderBottom: collapsedPanels.has('ai-mem') ? 'none' : '1px solid rgba(255,255,255,0.04)' }}>
              <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                <button onClick={() => toggleCollapse('ai-mem')} style={{ background:'none', border:'none', cursor:'pointer', fontSize:10, color:'rgba(255,255,255,0.22)', padding:'0 2px 0 0', lineHeight:1 }}
                  title={collapsedPanels.has('ai-mem') ? 'Expand' : 'Collapse'}
                  onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                  onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.22)'}>
                  {collapsedPanels.has('ai-mem') ? '▸' : '▾'}
                </button>
                <span style={{ fontSize:8, fontFamily:'monospace', letterSpacing:'0.13em',
                  color:'rgba(255,255,255,0.28)', textTransform:'uppercase' }}>AI Memory · Performance History</span>
              </div>
              <div style={{ display:'flex', alignItems:'center', gap:10 }}>
                {!collapsedPanels.has('ai-mem') && <span style={{ fontSize:8, fontFamily:'monospace', color:'rgba(255,255,255,0.16)' }}>7-day</span>}
                <button onClick={() => hidePanel('ai-mem')} title="Hide panel" style={{ background:'none', border:'none', cursor:'pointer', fontSize:14, lineHeight:1, color:'rgba(255,255,255,0.15)', padding:'0 2px' }}
                  onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                  onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.15)'}>×</button>
              </div>
            </div>
            {!collapsedPanels.has('ai-mem') && (<>
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
                      {mem.yest.su}{' setup'}{mem.yest.su !== 1 ? 's' : ''}{' · '}{mem.yest.tr}{' trade'}{mem.yest.tr !== 1 ? 's' : ''}
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
                          {tm.worstSetup.losses} loss{tm.worstSetup.losses !== 1 ? 'es' : ''}{'\u00b7'}{Math.round(tm.worstSetup.wr * 100)}% WR
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
                    {'peak · '}{mem.live.su}{' setup'}{mem.live.su !== 1 ? 's' : ''}{' · '}{mem.live.tr}{' trade'}{mem.live.tr !== 1 ? 's' : ''}
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
            </>)}{/* end ai-mem collapse guard */}
          </div>
          )}{/* end ai-mem guard */}

          {/* ── QUICK CHIPS ─────────────────────────────────────────────── */}
          {!hiddenPanels.has('quick-chips') && (
          <div className="quick-chips" style={{ display:'flex', gap:6, flexWrap:'wrap', marginBottom:16, alignItems:'center' }}>
            {chips.map(c => (
              <button key={c} className="chip-btn" onClick={() => ask(c)} disabled={asking} style={{
                padding:'5px 13px', borderRadius:16, border:'1px solid rgba(255,255,255,0.09)',
                background:'transparent', color:'rgba(255,255,255,0.40)', fontSize:12, fontFamily:'monospace',
                cursor:'pointer' }}>
                {c}
              </button>
            ))}
            <button onClick={() => hidePanel('quick-chips')} title="Hide" style={{ background:'none', border:'none', cursor:'pointer', fontSize:14, lineHeight:1, color:'rgba(255,255,255,0.13)', padding:'2px 4px', marginLeft:'auto' }}
              onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.50)'}
              onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.13)'}>×</button>
          </div>
          )}{/* end quick-chips guard */}

          {/* ── SESSION MEMORY ──────────────────────────────────────── */}
          {!hiddenPanels.has('session-mem') && (
          <div style={{ marginBottom:10, border:'1px solid rgba(255,255,255,0.048)', borderRadius:10, overflow:'hidden' }}>
            <div style={{ display:'flex', alignItems:'center', background:'rgba(255,255,255,0.012)', borderBottom: memOpen ? '1px solid rgba(255,255,255,0.035)' : 'none' }}>
              <button className="accord-toggle" onClick={() => setMemOpen(!memOpen)} style={{
                flex:1, display:'flex', alignItems:'center', justifyContent:'space-between',
                padding:'9px 14px', background:'none', border:'none', cursor:'pointer',
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
              <button onClick={() => hidePanel('session-mem')} title="Hide panel" style={{ background:'none', border:'none', cursor:'pointer', fontSize:15, lineHeight:1, color:'rgba(255,255,255,0.15)', padding:'0 12px', alignSelf:'stretch', display:'flex', alignItems:'center' }}
                onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.15)'}>×</button>
            </div>
            {memOpen && (
              <div style={{ padding:'11px 14px 14px' }}>
                <MemoryPanel entries={memEntries} onClear={memClear} />
              </div>
            )}
          </div>
          )}{/* end session-mem guard */}

          {/* ── CHAT ────────────────────────────────────────────────────── */}
          <div style={{ marginBottom:16, border:`1px solid ${eyeColor}30`, borderRadius:10,
            background:'rgba(255,255,255,0.022)', overflow:'hidden' }}>
            {/* Chat header */}
            <div style={{ display:'flex', alignItems:'center',
              padding:'9px 14px 7px', borderBottom:'1px solid rgba(255,255,255,0.045)',
              background:'rgba(255,255,255,0.014)' }}>
              <span style={{ fontSize:12, color:eyeColor, opacity:0.8, marginRight:7 }}>&#x1F4AC;</span>
              <span style={{ fontSize:11.5, fontFamily:'monospace', color:'rgba(255,255,255,0.55)',
                letterSpacing:'0.06em', textTransform:'uppercase', fontWeight:700, marginRight:7 }}>Talk to AI</span>
              <span style={{ fontSize:10, color:'rgba(255,255,255,0.22)', fontFamily:'monospace' }}>— type below or tap the mic</span>
            </div>
            {/* Messages */}
            <div ref={chatRef} style={{ maxHeight:220, overflowY:'auto', padding:'14px 14px 6px' }}>
              {msgs.length === 0 && (
                <div style={{ fontSize:12, color:'rgba(255,255,255,0.22)', fontFamily:'monospace', textAlign:'center', padding:'20px 0' }}>Reply to the avatar or ask it anything&hellip;</div>
              )}
              {msgs.map(m => <BrainBubble key={m.id} msg={m} />)}
              {asking && (
                <div style={{ display:'flex', gap:5, padding:'6px 0 4px' }}>
                  {[0,1,2].map(i => <div key={i} style={{ width:6, height:6, borderRadius:'50%', background:eyeColor, animation:`bDot 1.2s ${i*0.2}s infinite` }} />)}
                </div>
              )}
            </div>
            {/* Voice transcript preview */}
            {(voiceState === 'listening' || voiceState === 'processing') && voiceTranscript && (
              <div style={{ padding:'5px 14px 0', fontSize:12, color:'rgba(255,255,255,0.52)', fontFamily:'monospace',
                fontStyle:'italic', borderTop:'1px solid rgba(59,130,246,0.08)', display:'flex', alignItems:'center', gap:6 }}>
                <span style={{ color:'rgba(59,130,246,0.55)', fontSize:9 }}>{'\u25CF'}</span>
                {voiceTranscript}
              </div>
            )}
            {/* Voice error */}
            {voiceState === 'error' && voiceErrorMsg && (
              <div style={{ padding:'5px 14px 0', fontSize:11.5, color:'#f87171', fontFamily:'monospace',
                borderTop:'1px solid rgba(239,68,68,0.08)', display:'flex', alignItems:'center', gap:6 }}>
                <span style={{ flex:1 }}>{voiceErrorMsg}</span>
                <button onClick={clearVoiceError} style={{ background:'none', border:'none',
                  color:'rgba(255,255,255,0.28)', fontSize:11, cursor:'pointer', padding:'0 2px' }}>{'\u2715'}</button>
              </div>
            )}
            {/* Input row */}
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

          {/* ── EVIDENCE ACCORDION ──────────────────────────────────────── */}
          {!hiddenPanels.has('evidence') && (
          <div style={{ marginBottom:12, border:'1px solid rgba(255,255,255,0.055)', borderRadius:10, overflow:'hidden' }}>
            <div style={{ display:'flex', alignItems:'center', background:'rgba(255,255,255,0.020)' }}>
              <button className="accord-toggle" onClick={() => setEvidenceOpen(!evidenceOpen)} style={{
                flex:1, display:'flex', alignItems:'center', justifyContent:'space-between',
                padding:'11px 16px', background:'none', border:'none', cursor:'pointer',
                color:'rgba(255,255,255,0.55)', fontSize:11.5, fontFamily:'monospace', letterSpacing:'0.08em' }}>
                <span style={{ textTransform:'uppercase', fontWeight:700 }}>Evidence Snapshot</span>
                <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                  {data && <span style={{ fontSize:11, color:verdictColor, fontWeight:700 }}>EDGE {Math.round(edge)} / 110</span>}
                  <span style={{ fontSize:13, color:'rgba(255,255,255,0.30)' }}>{evidenceOpen ? '▲' : '▼'}</span>
                </div>
              </button>
              <button onClick={() => hidePanel('evidence')} title="Hide panel" style={{ background:'none', border:'none', cursor:'pointer', fontSize:15, lineHeight:1, color:'rgba(255,255,255,0.15)', padding:'0 14px', alignSelf:'stretch', display:'flex', alignItems:'center' }}
                onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.15)'}>×</button>
            </div>
            {evidenceOpen && data && (
              <div style={{ padding:'14px 16px 16px', borderTop:'1px solid rgba(255,255,255,0.040)' }}>
                <EvidenceDrawer data={data} status={status} />
              </div>
            )}
          </div>
          )}{/* end evidence guard */}

          {/* spacer */}
          <div style={{ height:24 }} />
        </div>

        {/* ── RIGHT COLUMN — Order Flow · Levels to Watch · Market Structure ── */}
        <div className="right-col">

          {/* ORDER FLOW */}
          {!hiddenPanels.has('rc-orderflow') && (
          <div className="rc-panel">
            <div className="rc-hdr">
              <button onClick={() => toggleCollapse('rc-orderflow')} style={{ background:'none', border:'none', cursor:'pointer', fontSize:9, color:'rgba(255,255,255,0.22)', padding:'0 5px 0 0', lineHeight:1 }}
                title={collapsedPanels.has('rc-orderflow') ? 'Expand' : 'Collapse'}
                onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.22)'}>
                {collapsedPanels.has('rc-orderflow') ? '▸' : '▾'}
              </button>
              <span className="rc-title">Order Flow</span>
              {!collapsedPanels.has('rc-orderflow') && (() => {
                const c = String(sig.cvd || ad.cvd || '').toLowerCase();
                const col = /bull|pos/.test(c) ? BULL : /bear|neg/.test(c) ? BEAR : MUTED;
                const lbl = /bull|pos/.test(c) ? 'BULL DELTA' : /bear|neg/.test(c) ? 'BEAR DELTA' : 'NEUTRAL';
                return <span style={{ fontSize:9, color:col, fontFamily:'monospace', fontWeight:700 }}>{lbl}</span>;
              })()}
              <button onClick={() => hidePanel('rc-orderflow')} title="Hide" style={{ background:'none', border:'none', cursor:'pointer', fontSize:14, lineHeight:1, color:'rgba(255,255,255,0.15)', padding:'0 2px', marginLeft:4 }}
                onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.15)'}>×</button>
            </div>
            {!collapsedPanels.has('rc-orderflow') && (
            /* Mini delta bar chart seeded from live edge score */
            (() => {
              const isBull = /bull|pos/.test(String(sig.cvd || ad.cvd || '').toLowerCase());
              const isBear = /bear|neg/.test(String(sig.cvd || ad.cvd || '').toLowerCase());
              const seed   = Math.floor(edge * 13) + (isBull ? 1 : isBear ? 2 : 0);
              const bars   = Array.from({ length: 22 }, (_, i) => {
                const s = ((seed * 17 + i * 41 + i * i * 7) % 89);
                const h = 12 + s * 0.55;
                const green = isBull ? (s > 28) : isBear ? (s > 68) : (s > 44);
                return { h: Math.round(h), green };
              });
              const mx = Math.max(...bars.map(b => b.h), 1);
              return (
                <div style={{ padding:'10px 12px 8px' }}>
                  <div style={{ display:'flex', gap:2, alignItems:'flex-end', height:54 }}>
                    {bars.map((b, i) => (
                      <div key={i} style={{
                        flex:1, borderRadius:2, minHeight:3,
                        height:`${Math.round(b.h / mx * 100)}%`,
                        background: b.green ? 'rgba(34,197,94,0.72)' : 'rgba(239,68,68,0.72)',
                      }} />
                    ))}
                  </div>
                  <div style={{ display:'flex', justifyContent:'space-between', marginTop:5 }}>
                    <span style={{ fontSize:9, color:MUTED, fontFamily:'monospace' }}>
                      {(() => { const v = String(ad.volume || '').toLowerCase(); return /strong|high/.test(v) ? 'High vol' : /incr/.test(v) ? 'Rising vol' : /low|thin/.test(v) ? 'Low vol' : 'Normal vol'; })()}
                    </span>
                    <span style={{ fontSize:9, color:MUTED, fontFamily:'monospace' }}>{edge > 0 ? Math.round(edge) : '\u2014'}</span>
                  </div>
                </div>
              );
            })()
            )}
          </div>
          )}{/* end rc-orderflow guard */}

          {/* LEVELS TO WATCH */}
          {!hiddenPanels.has('rc-levels') && (
          <div className="rc-panel">
            <div className="rc-hdr">
              <button onClick={() => toggleCollapse('rc-levels')} style={{ background:'none', border:'none', cursor:'pointer', fontSize:9, color:'rgba(255,255,255,0.22)', padding:'0 5px 0 0', lineHeight:1 }}
                title={collapsedPanels.has('rc-levels') ? 'Expand' : 'Collapse'}
                onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.22)'}>
                {collapsedPanels.has('rc-levels') ? '▸' : '▾'}
              </button>
              <span className="rc-title">Levels to Watch</span>
              <button onClick={() => hidePanel('rc-levels')} title="Hide" style={{ background:'none', border:'none', cursor:'pointer', fontSize:14, lineHeight:1, color:'rgba(255,255,255,0.15)', padding:'0 2px', marginLeft:'auto' }}
                onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.15)'}>×</button>
            </div>
            {!collapsedPanels.has('rc-levels') && (
            <div style={{ padding:'8px 12px 10px' }}>
              {(() => {
                const vwapV = Number(data?.vwap_value  || 0);
                const sup   = Number(data?.nearest_supply || 0);
                const dem   = Number(data?.nearest_demand || 0);
                const atr   = Number(data?.atr_pts || data?.current_atr || 1);
                const r1    = sup > 0 ? sup : (vwapV > 0 ? vwapV + atr * 1.4 : 0);
                const r2    = r1  > 0 ? r1  + atr * 1.1 : 0;
                const pivot = vwapV > 0 ? vwapV : 0;
                const s1    = dem > 0 ? dem : (vwapV > 0 ? vwapV - atr * 1.4 : 0);
                const s2    = s1  > 0 ? s1  - atr * 1.1 : 0;
                const fmtLv = (v: number) => v > 0 ? fmt(v) : '\u2014';
                return (
                  <>
                    <div style={{ fontSize:9.5, fontFamily:'monospace', color:BEAR, fontWeight:700, marginBottom:5, letterSpacing:'0.05em' }}>Resistance</div>
                    {([['R2', r2, BEAR], ['R1', r1, BEAR], ['Pivot', pivot, '#60a5fa']] as [string,number,string][]).map(([l, v, c]) => (
                      <div key={l} style={{ display:'flex', justifyContent:'space-between', padding:'3px 0', borderBottom:'1px solid rgba(255,255,255,0.020)' }}>
                        <span style={{ fontSize:10, color:'rgba(255,255,255,0.30)', fontFamily:'monospace' }}>{l}</span>
                        <span style={{ fontSize:10.5, fontFamily:'monospace', fontWeight:700, color:c }}>{fmtLv(v)}</span>
                      </div>
                    ))}
                    <div style={{ fontSize:9.5, fontFamily:'monospace', color:BULL, fontWeight:700, marginTop:8, marginBottom:5, letterSpacing:'0.05em' }}>Support</div>
                    {([['S1', s1, BULL], ['S2', s2, BULL]] as [string,number,string][]).map(([l, v, c]) => (
                      <div key={l} style={{ display:'flex', justifyContent:'space-between', padding:'3px 0', borderBottom:'1px solid rgba(255,255,255,0.020)' }}>
                        <span style={{ fontSize:10, color:'rgba(255,255,255,0.30)', fontFamily:'monospace' }}>{l}</span>
                        <span style={{ fontSize:10.5, fontFamily:'monospace', fontWeight:700, color:c }}>{fmtLv(v)}</span>
                      </div>
                    ))}
                  </>
                );
              })()}
            </div>
            )}{/* end rc-levels collapse */}
          </div>
          )}{/* end rc-levels guard */}

          {/* MARKET STRUCTURE */}
          {!hiddenPanels.has('rc-structure') && (
          <div className="rc-panel">
            <div className="rc-hdr">
              <button onClick={() => toggleCollapse('rc-structure')} style={{ background:'none', border:'none', cursor:'pointer', fontSize:9, color:'rgba(255,255,255,0.22)', padding:'0 5px 0 0', lineHeight:1 }}
                title={collapsedPanels.has('rc-structure') ? 'Expand' : 'Collapse'}
                onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.22)'}>
                {collapsedPanels.has('rc-structure') ? '▸' : '▾'}
              </button>
              <span className="rc-title">Market Structure</span>
              <button onClick={() => hidePanel('rc-structure')} title="Hide" style={{ background:'none', border:'none', cursor:'pointer', fontSize:14, lineHeight:1, color:'rgba(255,255,255,0.15)', padding:'0 2px', marginLeft:'auto' }}
                onMouseEnter={e=>e.currentTarget.style.color='rgba(255,255,255,0.55)'}
                onMouseLeave={e=>e.currentTarget.style.color='rgba(255,255,255,0.15)'}>×</button>
            </div>
            {!collapsedPanels.has('rc-structure') && (
            <div style={{ padding:'8px 12px 10px' }}>
              {(() => {
                const sc    = !!gd.structure_confirmed;
                const stype = String(gd.structure_type || '').toLowerCase();
                const bos   = stype.includes('bos') || (sc && !stype.includes('choch'));
                const choch = stype.includes('choch');
                const zv    = !!gd.zone_valid;
                const dem   = data?.nearest_demand;
                const sup   = data?.nearest_supply;
                const b     = String(sig.bias || '').toLowerCase();
                const flowCol = /bull/.test(b) ? BULL : /bear/.test(b) ? BEAR : MUTED;
                const flowLbl = /bull/.test(b) ? 'BULLISH' : /bear/.test(b) ? 'BEARISH' : 'NEUTRAL';
                return ([
                  ['BOS',       bos   ? 'Yes' : 'No',                bos   ? BULL : MUTED],
                  ['CHOCH',     choch ? 'Yes' : 'No',                choch ? BULL : MUTED],
                  ['Structure', sc ? 'Confirmed' : 'Not confirmed',   sc    ? BULL : MUTED],
                  ['Zone',      zv ? 'Active' : (dem||sup) ? 'Nearby' : 'No zone', zv ? '#f97316' : (dem||sup) ? AMB : MUTED],
                  ['Flow',      flowLbl,                              flowCol],
                ] as [string,string,string][]).map(([lbl, val, col]) => (
                  <div key={lbl} style={{ display:'flex', justifyContent:'space-between', alignItems:'center',
                    padding:'4px 0', borderBottom:'1px solid rgba(255,255,255,0.020)' }}>
                    <span style={{ fontSize:10, color:'rgba(255,255,255,0.30)', fontFamily:'monospace' }}>{lbl}</span>
                    <span style={{ fontSize:10.5, fontFamily:'monospace', fontWeight:700, color:col }}>{val}</span>
                  </div>
                ));
              })()}
            </div>
            )}{/* end rc-structure collapse */}
          </div>
          )}{/* end rc-structure guard */}

        </div>
           </>
         )}

      </div>
    </div>
  );
}
