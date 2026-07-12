/**
 * THE SENTINEL — AI Trading Partner Avatar
 * Permanent visual identity. Matte black mask + electric blue eyes +
 * holographic energy hood. Self-contained canvas animation system.
 */
import React, { useRef, useEffect, memo } from 'react';

// ─── Types (mirror Home.tsx) ──────────────────────────────────────────────────
type AvatarState =
  | 'WAIT' | 'ANALYZING' | 'FORMING'
  | 'READY_LONG' | 'READY_SHORT'
  | 'NO_EDGE' | 'ACTIVE' | 'STOP_HIT' | 'TARGET_HIT';
type GazeEvt = { dx: number; dy: number; widen: boolean; dur: number; id: number };
interface SpeechCtrl { energy: number; viseme: string; active: boolean; }

// ─── State palette ────────────────────────────────────────────────────────────
type RGB = [number, number, number];

const EYE_COL: Record<AvatarState, RGB> = {
  WAIT:        [0,   148, 255],
  ANALYZING:   [0,   185, 255],
  FORMING:     [0,   210, 255],
  READY_LONG:  [0,   230, 120],
  READY_SHORT: [255,  80, 100],
  NO_EDGE:     [55,   95, 185],
  ACTIVE:      [255, 200,  50],
  STOP_HIT:    [255,  55,  70],
  TARGET_HIT:  [20,  255, 150],
};

const GLOW_TGT: Record<AvatarState, number> = {
  WAIT: 0.42, ANALYZING: 0.80, FORMING: 0.68,
  READY_LONG: 1.00, READY_SHORT: 1.00,
  NO_EDGE: 0.22, ACTIVE: 0.85, STOP_HIT: 0.92, TARGET_HIT: 0.98,
};

// ─── Component ────────────────────────────────────────────────────────────────
const Sentinel = memo(function Sentinel({
  avState, speaking, gazeEvent, speechCtrlRef, voiceListeningRef,
}: {
  avState:          AvatarState;
  speaking:         boolean;
  ringColor:        string;     // kept for interface parity, not used directly
  gazeEvent:        GazeEvt;
  speechCtrlRef:    React.MutableRefObject<SpeechCtrl>;
  voiceListeningRef: React.MutableRefObject<boolean>;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef    = useRef(0);

  // Refs that the draw loop reads without causing re-renders
  const stateRef  = useRef(avState);
  const speakRef  = useRef(speaking);
  const gazeRef   = useRef<{ dx:number; dy:number; t0:number; dur:number }>({ dx:0, dy:0, t0:0, dur:0 });

  // Particle / band geometry (seeded once at mount)
  const orbitRef  = useRef<{ ang:number; r:number; spd:number; sz:number; ph:number; col:RGB }[]>([]);
  const hoodRef   = useRef<{ y:number; ph:number; spd:number; amp:number; col:RGB }[]>([]);

  // Blink FSM
  const blinkRef  = useRef({ pct:0, state:'open' as 'open'|'closing'|'closed'|'opening', timer:2.4 });

  // All smoothed / accumulated draw-loop values
  const sm = useRef({
    gx: 0, gy: 0,               // smoothed gaze offset
    eyeGlow: 0.42,              // smoothed glow intensity
    pupilR: 5.0,                // smoothed pupil radius
    ringRot: 0,                 // accumulated ring rotation
    mouthJaw: 0,                // speech jaw drop
    mouthCorner: 0,             // expression corner
    hoodPhase: 0,               // hood energy pulse
    listenPulse: 0,             // voice-listening ring
  });

  useEffect(() => { stateRef.current = avState; }, [avState]);
  useEffect(() => { speakRef.current = speaking; }, [speaking]);
  useEffect(() => {
    if (gazeEvent.dur > 0) {
      gazeRef.current = { dx: gazeEvent.dx, dy: gazeEvent.dy, t0: Date.now(), dur: gazeEvent.dur };
    }
  }, [gazeEvent.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // ── Canvas geometry ──────────────────────────────────────────────────────
    const W = 240, H = 320;
    const CX = 120, CY = 148;   // mask center
    const MRX = 74, MRY = 97;   // mask radii

    // ── Seed orbit particles ─────────────────────────────────────────────────
    orbitRef.current = Array.from({ length: 32 }, (_, i) => {
      const col: RGB = i % 3 === 0 ? [0,220,255] : i % 3 === 1 ? [70,155,255] : [155,90,255];
      return {
        ang: (i / 32) * Math.PI * 2 + Math.random() * 0.6,
        r:   MRX * (1.18 + Math.random() * 0.55),
        spd: (0.18 + Math.random() * 0.32) * (i % 2 === 0 ? 1 : -1) * 0.001,
        sz:  0.6 + Math.random() * 1.5,
        ph:  Math.random() * Math.PI * 2,
        col,
      };
    });

    // ── Seed hood energy bands ───────────────────────────────────────────────
    hoodRef.current = Array.from({ length: 9 }, (_, i) => ({
      y:   CY + MRY * 0.54 + i * 11,
      ph:  Math.random() * Math.PI * 2,
      spd: 0.00025 + Math.random() * 0.00020,
      amp: 5 + Math.random() * 9,
      col: (i % 2 === 0 ? [0, 155, 255] : [90, 55, 255]) as RGB,
    }));

    // First blink: 2–4 s
    blinkRef.current.timer = 2 + Math.random() * 2;

    const canvas = canvasRef.current; if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: true }); if (!ctx) return;

    // ── Helper: rgba string ──────────────────────────────────────────────────
    const rc = (c: RGB, a: number): string =>
      `rgba(${c[0]},${c[1]},${c[2]},${Math.max(0, Math.min(1, a))})`;

    const t0 = Date.now();
    let lastT = t0;

    // ── Draw loop ────────────────────────────────────────────────────────────
    function draw() {
      const now     = Date.now();
      const elapsed = now - t0;
      const dt      = Math.min((now - lastT) / 1000, 0.05);
      lastT = now;

      const s        = stateRef.current;
      const spk      = speakRef.current;
      const eyeCol   = EYE_COL[s] || EYE_COL.WAIT;
      const glowTgt  = GLOW_TGT[s] || 0.42;
      const m        = sm.current;

      // ── Speech ─────────────────────────────────────────────────────────────
      const spkE   = Math.max(0, Math.min(1, speechCtrlRef.current.energy));
      const spkVis = speechCtrlRef.current.viseme || 'rest';
      const tJaw   = spk ? (spkVis === 'press' ? 0 : spkVis === 'rounded' ? spkE * 5.2 : spkVis === 'narrow' ? spkE * 3.8 : spkE * 6.2) : 0;
      const tCorn  = s === 'READY_LONG' || s === 'TARGET_HIT' ? -1.4 : s === 'STOP_HIT' ? 1.2 : 0;
      m.mouthJaw    += (tJaw  - m.mouthJaw)    * (spk ? 0.24 : 0.14);
      m.mouthCorner += (tCorn - m.mouthCorner) * 0.06;

      // ── Gaze ───────────────────────────────────────────────────────────────
      const gaze = gazeRef.current;
      let tgx = 0, tgy = 0;
      if (gaze.dur > 0) {
        const age = (now - gaze.t0) / 1000;
        if (age < gaze.dur) { tgx = gaze.dx * 6; tgy = gaze.dy * 4; }
        else gazeRef.current = { dx:0, dy:0, t0:0, dur:0 };
      }
      // Idle micro-movement
      tgx += Math.sin(elapsed * 0.00031 + 1.1) * 2.6 + Math.sin(elapsed * 0.00069) * 1.1;
      tgy += Math.sin(elapsed * 0.00043 + 0.7) * 1.6;
      m.gx += (tgx - m.gx) * 0.055;
      m.gy += (tgy - m.gy) * 0.055;

      // ── Bob (breathing) ────────────────────────────────────────────────────
      const breathAmp = s === 'NO_EDGE' ? 2.6 : s === 'ANALYZING' ? 1.1 : 1.7;
      const bob = Math.sin(elapsed * 0.00092) * breathAmp;

      // ── Eye glow ───────────────────────────────────────────────────────────
      m.eyeGlow += (glowTgt - m.eyeGlow) * 0.038;
      const glowPx = m.eyeGlow * 30 + (spk ? spkE * 16 : 0);

      // ── Pupil ──────────────────────────────────────────────────────────────
      const tPupil = s === 'ANALYZING' || s === 'FORMING'
        ? 3.6 + Math.sin(elapsed * 0.0019) * 0.9
        : spk ? 4.5 + spkE * 1.4 : s === 'READY_LONG' || s === 'READY_SHORT' ? 5.6 : 5.0;
      m.pupilR += (tPupil - m.pupilR) * 0.048;

      // ── Ring rotation ───────────────────────────────────────────────────────
      const ringSpd = s === 'ANALYZING' ? 0.0014 : (s === 'READY_LONG' || s === 'READY_SHORT') ? 0.00065 : 0.00028;
      m.ringRot += ringSpd * dt * 1000;

      // ── Hood pulse ─────────────────────────────────────────────────────────
      m.hoodPhase = 0.5 + 0.5 * Math.sin(elapsed * 0.00072 + (spk ? spkE * Math.PI : 0));

      // ── Voice-listening pulse ───────────────────────────────────────────────
      const tListen = voiceListeningRef.current ? 0.85 : 0;
      m.listenPulse += (tListen - m.listenPulse) * 0.05;

      // ── Blink FSM ──────────────────────────────────────────────────────────
      const bk = blinkRef.current;
      bk.timer -= dt;
      if (bk.state === 'open' && bk.timer <= 0) bk.state = 'closing';
      if (bk.state === 'closing') {
        bk.pct += dt * 7.5;
        if (bk.pct >= 1) { bk.pct = 1; bk.state = 'closed'; bk.timer = 0.06 + Math.random() * 0.05; }
      }
      if (bk.state === 'closed') {
        bk.timer -= dt;
        if (bk.timer <= 0) bk.state = 'opening';
      }
      if (bk.state === 'opening') {
        bk.pct -= dt * 9.5;
        if (bk.pct <= 0) { bk.pct = 0; bk.state = 'open'; bk.timer = 2.5 + Math.random() * 3.0; }
      }
      const blinkPct = bk.pct;

      // ─────────────────────────────────────────────────────────────────────
      // RENDER
      // ─────────────────────────────────────────────────────────────────────
      ctx.clearRect(0, 0, W, H);

      // ── 1. HOOD / COLLAR — holographic energy at base ─────────────────────
      {
        const hTopY = CY + bob + MRY * 0.52;
        // Hood gradient base
        const hBG = ctx.createLinearGradient(CX, hTopY, CX, H + 10);
        hBG.addColorStop(0,    'rgba(0,0,0,0)');
        hBG.addColorStop(0.14, rc(eyeCol, 0.07 * m.hoodPhase));
        hBG.addColorStop(0.42, 'rgba(3,2,18,0.70)');
        hBG.addColorStop(0.80, 'rgba(2,1,12,0.88)');
        hBG.addColorStop(1,    'rgba(0,0,8,0.96)');
        ctx.fillStyle = hBG; ctx.fillRect(0, hTopY, W, H + 10 - hTopY);

        // Animated energy bands
        hoodRef.current.forEach((band, bi) => {
          const by = band.y + bob + Math.sin(elapsed * band.spd + band.ph) * band.amp * 0.38;
          if (by > H + 4) return;
          const distFrac = Math.min(1, (by - hTopY) / (H - hTopY + 10));
          const alpha = 0.11 * m.hoodPhase * (1 - distFrac * 0.85);
          if (alpha < 0.004) return;
          const halfW = Math.min(W * 0.52, MRX * 1.35 + (by - hTopY) * 0.82);
          const bG = ctx.createLinearGradient(CX - halfW, by, CX + halfW, by);
          bG.addColorStop(0,    'rgba(0,0,0,0)');
          bG.addColorStop(0.18, rc(band.col, alpha * 0.55));
          bG.addColorStop(0.50, rc(band.col, alpha));
          bG.addColorStop(0.82, rc(band.col, alpha * 0.55));
          bG.addColorStop(1,    'rgba(0,0,0,0)');
          ctx.fillStyle = bG; ctx.fillRect(CX - halfW, by - 2.2, halfW * 2, 4.5);
          // Occasional flowing arc
          if (bi % 2 === 0) {
            const ox = CX + Math.sin(elapsed * band.spd * 0.7 + bi * 1.3) * halfW * 0.38;
            ctx.beginPath();
            ctx.moveTo(ox, hTopY + 6);
            ctx.quadraticCurveTo(
              ox + Math.sin(elapsed * 0.00038 + bi) * 14, by,
              ox - Math.sin(elapsed * 0.00052 + bi) * 9,  by + 12,
            );
            ctx.strokeStyle = rc(band.col, alpha * 0.38); ctx.lineWidth = 0.55; ctx.stroke();
          }
        });

        // Shoulder particles (bottom area)
        for (let pi = 0; pi < 8; pi++) {
          const pr = 28 + pi * 9;
          const pa = elapsed * 0.00022 * (pi % 2 === 0 ? 1 : -1.3) + (pi / 8) * Math.PI * 2;
          const pcx = CX + Math.cos(pa) * pr;
          const pcy = H - 28 - pi * 3 + Math.sin(elapsed * 0.00088 + pi) * 4;
          ctx.beginPath(); ctx.arc(pcx, pcy, 0.7 + pi * 0.08, 0, Math.PI * 2);
          ctx.fillStyle = rc(eyeCol, 0.18 * m.hoodPhase * m.eyeGlow); ctx.fill();
        }
      }

      // ── 2. Orbit particles — behind mask (bottom arc) ─────────────────────
      orbitRef.current.forEach(p => {
        p.ang += p.spd * dt * 1000;
        const px = CX + Math.cos(p.ang) * p.r;
        const py = CY + bob + Math.sin(p.ang) * p.r * 0.46;
        if (py < CY + bob) return; // back-arc only here
        const a = 0.5 + 0.5 * Math.sin(p.ph + elapsed * 0.00058);
        ctx.beginPath(); ctx.arc(px, py, p.sz, 0, Math.PI * 2);
        ctx.fillStyle = rc(p.col, m.eyeGlow * a * 0.62); ctx.fill();
      });

      // ── 3. MASK BASE — smooth matte-black oval ─────────────────────────────
      {
        const mG = ctx.createRadialGradient(CX - 16, CY + bob - 38, 5, CX + 4, CY + bob + 10, MRY * 1.10);
        mG.addColorStop(0,    'rgba(16, 20, 40, 0.98)');
        mG.addColorStop(0.38, 'rgba(9,  12, 26, 0.99)');
        mG.addColorStop(0.75, 'rgba(5,   6, 16, 1.00)');
        mG.addColorStop(1,    'rgba(2,   2,  9, 1.00)');
        ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX, MRY, 0, 0, Math.PI * 2);
        ctx.fillStyle = mG; ctx.fill();
      }

      // ── 4. KEY LIGHT — blue from upper-left, state-reactive ───────────────
      ctx.save();
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX, MRY, 0, 0, Math.PI * 2); ctx.clip();

      const kG = ctx.createRadialGradient(CX - 32, CY + bob - 55, 0, CX, CY + bob, MRX * 1.18);
      const kI = m.eyeGlow * 0.36;
      kG.addColorStop(0,    rc(eyeCol, kI * 0.88));
      kG.addColorStop(0.32, rc(eyeCol, kI * 0.40));
      kG.addColorStop(0.68, rc([35, 55, 130], 0.09));
      kG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = kG; ctx.fillRect(0, 0, W, H);

      // ── 5. RIM LIGHT — soft purple from right ─────────────────────────────
      const rG = ctx.createRadialGradient(CX + MRX * 0.90, CY + bob + 4, 0, CX + 14, CY + bob + 18, MRX * 1.05);
      rG.addColorStop(0,   rc([155, 72, 255], m.eyeGlow * 0.30));
      rG.addColorStop(0.5, rc([90, 50, 195],  0.08));
      rG.addColorStop(1,   'rgba(0,0,0,0)');
      ctx.fillStyle = rG; ctx.fillRect(0, 0, W, H);

      // ── 6. Mask surface micro-reflections ─────────────────────────────────
      const refG = ctx.createLinearGradient(
        CX - MRX * 0.70, CY + bob - MRY * 0.60,
        CX + MRX * 0.08, CY + bob + MRY * 0.16);
      refG.addColorStop(0,    'rgba(175, 208, 255, 0.052)');
      refG.addColorStop(0.38, 'rgba(155, 195, 255, 0.022)');
      refG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = refG; ctx.fillRect(0, 0, W, H);
      ctx.restore();

      // ── 7. Mask rim glow + specular crescent ──────────────────────────────
      ctx.save();
      ctx.shadowBlur = 20 + m.eyeGlow * 16;
      ctx.shadowColor = rc(eyeCol, 0.52);
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX, MRY, 0, 0, Math.PI * 2);
      ctx.strokeStyle = rc(eyeCol, 0.15 + m.eyeGlow * 0.09); ctx.lineWidth = 1.1; ctx.stroke();
      ctx.shadowBlur = 0;
      // Specular crescent arc upper-left
      ctx.beginPath();
      ctx.ellipse(CX, CY + bob, MRX + 0.4, MRY + 0.4, 0, Math.PI * 1.05, Math.PI * 1.72);
      ctx.strokeStyle = `rgba(195,225,255,${0.18 + m.eyeGlow * 0.08})`; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.restore();

      // ── 8. CONFIDENCE RING — orbiting dashed ellipse ──────────────────────
      {
        const cRX = MRX + 11 + Math.sin(elapsed * 0.00065) * 1.4;
        const cRY = MRY + 11 + Math.sin(elapsed * 0.00065) * 1.0;
        // Arc segments (4 arcs with gaps)
        ctx.save();
        ctx.shadowBlur = 7; ctx.shadowColor = rc(eyeCol, 0.55);
        for (let ai = 0; ai < 4; ai++) {
          const aS = m.ringRot + ai * Math.PI * 0.5 + 0.10;
          const aE = m.ringRot + ai * Math.PI * 0.5 + Math.PI * 0.5 - 0.10;
          ctx.beginPath();
          ctx.ellipse(CX, CY + bob, cRX, cRY, 0, aS, aE);
          ctx.strokeStyle = rc(eyeCol, m.eyeGlow * 0.52); ctx.lineWidth = 0.9; ctx.stroke();
        }
        ctx.shadowBlur = 0;
        // Marker dots at the 4 cardinal arc ends
        for (let di = 0; di < 8; di++) {
          const da = m.ringRot + di * Math.PI * 0.25;
          const active = di % 2 === 0;
          ctx.beginPath();
          ctx.arc(
            CX + Math.cos(da) * cRX,
            CY + bob + Math.sin(da) * cRY,
            active ? 2.0 : 0.9, 0, Math.PI * 2,
          );
          ctx.fillStyle = rc(eyeCol, active ? m.eyeGlow * 0.82 : m.eyeGlow * 0.30); ctx.fill();
        }
        ctx.restore();
      }

      // ── 9. EYES ───────────────────────────────────────────────────────────
      const EW = 15.5;    // eye half-width
      const EH0 = s === 'NO_EDGE' ? 6.5 : s === 'ANALYZING' ? 8.0 : 7.4; // max eye half-height
      const EH  = EH0 * (1 - blinkPct * 0.97);

      const eyeDefs = [
        { bx: CX - 30, by: CY + bob - 19, tilt: -0.055 },
        { bx: CX + 30, by: CY + bob - 19, tilt:  0.055 },
      ];

      eyeDefs.forEach(({ bx, by, tilt }) => {
        const ex = bx + m.gx * 0.52;
        const ey = by + m.gy * 0.36;

        // a. Socket void — deep recessed cavity
        const sockG = ctx.createRadialGradient(bx, by + 2, 0, bx, by + 3, 21);
        sockG.addColorStop(0,    'rgba(0,0,14,0.97)');
        sockG.addColorStop(0.55, 'rgba(0,0,6,0.60)');
        sockG.addColorStop(1,    'rgba(0,0,0,0)');
        ctx.beginPath(); ctx.ellipse(bx, by + 2, 19, 12, tilt, 0, Math.PI * 2);
        ctx.fillStyle = sockG; ctx.fill();

        if (blinkPct < 0.98) {
          const iR = EW * 0.76;   // iris radius (x-axis)
          const iH = Math.max(0.4, EH * 0.90);

          // b. Iris field — dark with spoke pattern
          ctx.save();
          ctx.beginPath(); ctx.ellipse(ex, ey, iR, iH, tilt, 0, Math.PI * 2); ctx.clip();
          const irisG = ctx.createRadialGradient(ex, ey, 0, ex, ey, iR);
          irisG.addColorStop(0,    'rgba(0,2,26,0.98)');
          irisG.addColorStop(0.48, rc(eyeCol, 0.16));
          irisG.addColorStop(1,    rc(eyeCol, 0.26));
          ctx.fillStyle = irisG; ctx.fillRect(0, 0, W, H);
          // Spokes
          const rot = elapsed * 0.0000118;
          for (let fi = 0; fi < 12; fi++) {
            const fa = (fi / 12) * Math.PI * 2 + rot;
            ctx.beginPath();
            ctx.moveTo(ex + Math.cos(fa) * 2.1, ey + Math.sin(fa) * 1.9);
            ctx.lineTo(ex + Math.cos(fa) * iR,  ey + Math.sin(fa) * iH);
            ctx.strokeStyle = rc(eyeCol, fi % 3 === 0 ? 0.20 : 0.09);
            ctx.lineWidth = 0.30; ctx.stroke();
          }
          ctx.restore();

          // c. Bright outer iris ring — THE defining eye feature
          ctx.save();
          ctx.shadowBlur = glowPx * 0.92; ctx.shadowColor = rc(eyeCol, 0.98);
          ctx.beginPath(); ctx.ellipse(ex, ey, iR, iH, tilt, 0, Math.PI * 2);
          ctx.strokeStyle = rc(eyeCol, 0.66 + m.eyeGlow * 0.24); ctx.lineWidth = 1.85; ctx.stroke();
          ctx.restore();

          // d. Mid iris ring — subtle depth ring
          ctx.beginPath(); ctx.ellipse(ex, ey, EW * 0.46, Math.max(0.3, EH * 0.56), tilt, 0, Math.PI * 2);
          ctx.strokeStyle = rc(eyeCol, 0.32); ctx.lineWidth = 0.85; ctx.stroke();

          // e. Pupil — deep void, state-reactive size
          ctx.save();
          ctx.shadowBlur = 5; ctx.shadowColor = 'rgba(0,0,0,1)';
          ctx.beginPath(); ctx.ellipse(ex, ey, m.pupilR, Math.max(0.2, m.pupilR * 0.91), tilt, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(0,0,10,1)'; ctx.fill();
          ctx.restore();

          // f. Inner pupil ring — "thinking" halo
          const ipR = m.pupilR * 1.58;
          ctx.save();
          ctx.shadowBlur = 10 + m.eyeGlow * 9; ctx.shadowColor = rc(eyeCol, 1.0);
          ctx.beginPath(); ctx.ellipse(ex, ey, ipR, Math.max(0.2, ipR * 0.90), tilt, 0, Math.PI * 2);
          ctx.strokeStyle = rc(eyeCol, 0.68 + m.eyeGlow * 0.22); ctx.lineWidth = 0.95; ctx.stroke();
          ctx.restore();

          // g. Upper lid shadow — depth
          ctx.save();
          ctx.beginPath(); ctx.ellipse(bx, ey, EW * 0.90, Math.max(0.4, EH + 0.4), tilt, 0, Math.PI * 2); ctx.clip();
          const lsG = ctx.createLinearGradient(bx, ey - EH - 1, bx, ey + EH * 0.20);
          lsG.addColorStop(0,    'rgba(0,0,0,0.68)');
          lsG.addColorStop(0.55, 'rgba(0,0,0,0)');
          ctx.fillStyle = lsG; ctx.fillRect(0, 0, W, H);
          ctx.restore();

          // h. Specular catches — two glass reflections
          ctx.save();
          ctx.shadowBlur = 5; ctx.shadowColor = rc(eyeCol, 0.68);
          ctx.beginPath(); ctx.arc(ex - 3.0, ey - EH * 0.50, 1.75, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(218,240,255,0.94)'; ctx.fill();
          ctx.shadowBlur = 2;
          ctx.beginPath(); ctx.arc(ex + 1.9, ey + EH * 0.24, 0.78, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(200,228,255,0.38)'; ctx.fill();
          ctx.restore();
        }

        // i. Blink membrane — holographic energy sweep
        if (blinkPct > 0.04) {
          ctx.save();
          ctx.beginPath(); ctx.ellipse(bx, by, EW * 0.97, EH0 + 1.0, tilt, 0, Math.PI * 2); ctx.clip();
          const btY = by - EH0;
          const bbY = btY + (EH0 * 2.1 + 1.8) * blinkPct + 1;
          const bG  = ctx.createLinearGradient(bx, btY, bx, bbY);
          bG.addColorStop(0,    rc(eyeCol, Math.min(0.52, 0.42 + m.eyeGlow * 0.08)));
          bG.addColorStop(0.38, rc([20, 28, 58], 0.28));
          bG.addColorStop(1,    'rgba(0,0,0,0)');
          ctx.fillStyle = bG; ctx.fillRect(0, 0, W, H);
          ctx.restore();
        }

        // j. Upper lid arc — thin glowing edge
        ctx.save();
        ctx.shadowBlur = 4; ctx.shadowColor = rc(eyeCol, 0.44);
        const ldOff = EH0 * blinkPct * 0.82;
        ctx.beginPath();
        ctx.ellipse(bx, by - EH * 0.04 + ldOff, EW * 0.94, Math.max(0.4, EH + ldOff * 0.25), tilt, Math.PI * 0.91, Math.PI * 2.09);
        ctx.strokeStyle = rc(eyeCol, 0.54); ctx.lineWidth = 1.35; ctx.stroke();
        ctx.restore();

        // k. Ambient socket corona — outer glow around the eye
        ctx.save();
        ctx.shadowBlur = glowPx * 0.78; ctx.shadowColor = rc(eyeCol, 0.90);
        ctx.beginPath(); ctx.ellipse(bx, by, EW + 2.5, Math.max(0.4, EH0 + 2.5), tilt, 0, Math.PI * 2);
        ctx.strokeStyle = rc(eyeCol, 0.07 + m.eyeGlow * 0.05); ctx.lineWidth = 4.5; ctx.stroke();
        ctx.restore();
      });

      // ── 10. NOSE — bridge implied by lighting only ────────────────────────
      {
        const nbG = ctx.createLinearGradient(CX - 6, CY + bob - 8, CX + 1, CY + bob + 18);
        nbG.addColorStop(0,    'rgba(0,0,0,0)');
        nbG.addColorStop(0.42, rc(eyeCol, 0.07));
        nbG.addColorStop(1,    'rgba(0,0,0,0)');
        ctx.fillStyle = nbG; ctx.fillRect(CX - 9, CY - 11 + bob, 8, 30);
        const ntG = ctx.createRadialGradient(CX - 1, CY + 14 + bob, 0, CX - 1, CY + 14 + bob, 4.8);
        ntG.addColorStop(0, rc(eyeCol, 0.08));
        ntG.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = ntG; ctx.fillRect(CX - 6, CY + 10 + bob, 11, 10);
      }

      // ── 11. MOUTH ─────────────────────────────────────────────────────────
      {
        const MY = CY + bob + 53;
        const MW = 13.5;
        const JW = Math.max(0, m.mouthJaw);
        const CR = m.mouthCorner;

        ctx.save();
        ctx.shadowBlur = spk ? 7 : 2.5;
        ctx.shadowColor = rc(eyeCol, 0.35);

        if (JW < 0.7) {
          // IDLE / SILENT — thin illuminated line, near-invisible
          const mA = spk ? 0.42 : 0.11 + m.eyeGlow * 0.06;
          ctx.beginPath();
          ctx.moveTo(CX - MW, MY + CR);
          ctx.bezierCurveTo(CX - MW * 0.45, MY + CR - 1.2, CX + MW * 0.45, MY + CR - 1.2, CX + MW, MY + CR);
          ctx.strokeStyle = rc(eyeCol, mA); ctx.lineWidth = 0.65; ctx.stroke();
        } else {
          // SPEAKING — small jaw movement, paired lip beziers
          const drop = JW * 0.48;
          // Upper lip arc
          ctx.beginPath();
          ctx.moveTo(CX - MW, MY + CR);
          ctx.bezierCurveTo(CX - MW * 0.52, MY + CR - 2.2, CX + MW * 0.52, MY + CR - 2.2, CX + MW, MY + CR);
          ctx.strokeStyle = rc(eyeCol, 0.40); ctx.lineWidth = 0.95; ctx.stroke();
          // Lower lip arc
          ctx.beginPath();
          ctx.moveTo(CX - MW * 0.80, MY + CR + drop * 0.28);
          ctx.bezierCurveTo(CX - MW * 0.38, MY + CR + drop, CX + MW * 0.38, MY + CR + drop, CX + MW * 0.80, MY + CR + drop * 0.28);
          ctx.strokeStyle = rc(eyeCol, 0.30); ctx.lineWidth = 0.85; ctx.stroke();
          // Oral void
          const oG = ctx.createRadialGradient(CX, MY + CR + drop * 0.36, 0, CX, MY + CR + drop * 0.36, MW * 0.65);
          oG.addColorStop(0,   'rgba(0,0,14,0.95)');
          oG.addColorStop(0.6, 'rgba(0,0,6,0.58)');
          oG.addColorStop(1,   'rgba(0,0,0,0)');
          ctx.beginPath(); ctx.ellipse(CX, MY + CR + drop * 0.36, MW * 0.65, Math.max(0.4, drop * 0.46), 0, 0, Math.PI * 2);
          ctx.fillStyle = oG; ctx.fill();
        }
        ctx.restore();
      }

      // ── 12. Voice-listening ring ───────────────────────────────────────────
      if (m.listenPulse > 0.04) {
        const lpR = MRX + 16 + Math.sin(elapsed * 0.0042) * 3;
        const lpRY = MRY + 16 + Math.sin(elapsed * 0.0042) * 2;
        ctx.save();
        ctx.beginPath(); ctx.ellipse(CX, CY + bob, lpR, lpRY, 0, 0, Math.PI * 2);
        ctx.strokeStyle = rc([120, 200, 255], m.listenPulse * 0.55 * m.eyeGlow);
        ctx.lineWidth = 1.2; ctx.stroke();
        ctx.restore();
      }

      // ── 13. Speaking pulse — aura expands with speech energy ─────────────
      if (spk && spkE > 0.06) {
        ctx.save();
        const spG = ctx.createRadialGradient(CX, CY + bob, MRX * 0.75, CX, CY + bob, MRX + 8 + spkE * 10);
        spG.addColorStop(0,    'rgba(0,0,0,0)');
        spG.addColorStop(0.65, rc(eyeCol, spkE * 0.09));
        spG.addColorStop(1,    'rgba(0,0,0,0)');
        ctx.fillStyle = spG; ctx.fillRect(0, 0, W, H);
        ctx.restore();
      }

      // ── 14. Orbit particles — foreground (top arc, in front of mask) ──────
      orbitRef.current.forEach(p => {
        const py = CY + bob + Math.sin(p.ang) * p.r * 0.46;
        if (py >= CY + bob) return; // top arc only
        const a = 0.5 + 0.5 * Math.sin(p.ph + elapsed * 0.00055);
        ctx.beginPath(); ctx.arc(CX + Math.cos(p.ang) * p.r, py, p.sz, 0, Math.PI * 2);
        ctx.fillStyle = rc(p.col, m.eyeGlow * a * 0.50); ctx.fill();
      });

      rafRef.current = requestAnimationFrame(draw);
    }

    rafRef.current = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(rafRef.current); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <canvas
      ref={canvasRef}
      width={240}
      height={320}
      style={{ display: 'block', filter: 'drop-shadow(0 0 24px rgba(0,148,255,0.30))' }}
    />
  );
});

Sentinel.displayName = 'Sentinel';
export default Sentinel;
