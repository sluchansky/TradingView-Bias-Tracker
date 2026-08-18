/**
 * THE SENTINEL — AI Trading Partner Avatar v3
 * 25-layer independent rendering system.
 * Each feature is an isolated layer with its own factory, animation state,
 * and draw call. Assembled in z-order onto a single canvas.
 */
import React, { useRef, useEffect, memo } from 'react';

// ─── Types ────────────────────────────────────────────────────────────────────
type AvatarState =
  | 'WAIT' | 'ANALYZING' | 'FORMING'
  | 'READY_LONG' | 'READY_SHORT'
  | 'NO_EDGE' | 'ACTIVE' | 'STOP_HIT' | 'TARGET_HIT';
type GazeEvt    = { dx: number; dy: number; widen: boolean; dur: number; id: number };
interface SpeechCtrl { energy: number; viseme: string; active: boolean; }
type RGB = [number, number, number];

// ─── State palettes ───────────────────────────────────────────────────────────
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

// Confidence ring colour changes per spec: Blue/Yellow/Orange/Green/Red
const RING_COL: Record<AvatarState, RGB> = {
  WAIT:        [0,   148, 255],   // Blue
  ANALYZING:   [0,   195, 255],   // Cyan-blue
  FORMING:     [255, 200,   0],   // Yellow
  READY_LONG:  [0,   230, 100],   // Green
  READY_SHORT: [255,  60,  80],   // Red
  NO_EDGE:     [55,   80, 180],   // Dim blue
  ACTIVE:      [255, 165,   0],   // Orange
  STOP_HIT:    [255,  40,  55],   // Bright red
  TARGET_HIT:  [20,  255, 150],   // Bright green
};

const GLOW_TGT: Record<AvatarState, number> = {
  WAIT: 0.42, ANALYZING: 0.82, FORMING: 0.68,
  READY_LONG: 1.00, READY_SHORT: 1.00,
  NO_EDGE: 0.20, ACTIVE: 0.85, STOP_HIT: 0.92, TARGET_HIT: 1.00,
};

// ─── Global frame state passed to every layer ─────────────────────────────────
interface G {
  W: number; H: number; CX: number; CY: number; MRX: number; MRY: number;
  elapsed: number;   // ms since mount
  dt: number;        // seconds since last frame
  avState: AvatarState;
  eyeCol: RGB;
  ringCol: RGB;
  glowAmt: number;   // 0-1 smoothed
  bob: number;       // breathing offset px
  breathPhase: number;
  blinkPct: number;  // 0=open 1=closed
  gazeX: number;     // smoothed gaze px
  gazeY: number;
  pupilR: number;    // smoothed pupil radius
  browLift: number;  // eyebrow offset px (up = positive)
  jawDrop: number;   // mouth px opened
  mouthCorner: number;
  spkEnergy: number;
  spkViseme: string;
  spk: boolean;
  listening: boolean;
  mouseX: number;    // -1..1 normalized
  mouseY: number;
}

// ─── Layer interface ──────────────────────────────────────────────────────────
interface Layer {
  readonly name: string;
  draw(ctx: CanvasRenderingContext2D, g: G): void;
}

// ─── Helper ───────────────────────────────────────────────────────────────────
const rc = (c: RGB, a: number) =>
  `rgba(${c[0]},${c[1]},${c[2]},${Math.max(0, Math.min(1, a))})`;

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 1 — Background: gradient void, floating stars, atmospheric fog
// ══════════════════════════════════════════════════════════════════════════════
function makeL01_Background(): Layer {
  const stars = Array.from({ length: 58 }, () => ({
    x: Math.random() * 240, y: Math.random() * 320,
    r:  0.28 + Math.random() * 0.92,
    ph: Math.random() * Math.PI * 2,
    sp: 0.00040 + Math.random() * 0.00058,
  }));
  return {
    name: 'Background',
    draw(ctx, { W, H, CX, CY, elapsed, eyeCol, glowAmt }) {
      // Soft oval backdrop — fades to fully transparent at canvas edges (no rectangular box)
      const bgG = ctx.createRadialGradient(CX, CY * 0.88, 0, CX, CY, 158);
      bgG.addColorStop(0,    'rgba(6,10,28,0.96)');
      bgG.addColorStop(0.50, 'rgba(3,5,16,0.90)');
      bgG.addColorStop(0.72, 'rgba(1,2,8,0.65)');
      bgG.addColorStop(0.88, 'rgba(0,0,4,0.28)');
      bgG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = bgG; ctx.fillRect(0, 0, W, H);
      // Stars — only visible inside the soft backdrop oval (naturally faded at edges)
      stars.forEach(s => {
        const dx = s.x - CX, dy = s.y - CY;
        const dist = Math.sqrt(dx * dx + dy * dy) / 155;
        if (dist > 1) return;
        const t    = 0.5 + 0.5 * Math.sin(elapsed * s.sp + s.ph);
        const fade = Math.max(0, 1 - dist * dist);
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r * t, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(200,220,255,${(0.10 + t * 0.24) * fade})`; ctx.fill();
      });
      const fogG = ctx.createRadialGradient(CX + 28, 55, 0, CX + 28, 55, 120);
      fogG.addColorStop(0, rc(eyeCol, 0.038 * glowAmt));
      fogG.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = fogG; ctx.fillRect(0, 0, W, H);
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 2 — Energy Halo: large rotating ring behind head, brightness = confidence
// ══════════════════════════════════════════════════════════════════════════════
function makeL02_EnergyHalo(): Layer {
  let rot = 0;
  return {
    name: 'Energy Halo',
    draw(ctx, { CX, CY, bob, MRX, MRY, eyeCol, glowAmt, elapsed, dt }) {
      rot += 0.00022 * dt * 1000;
      const hRX = MRX + 32, hRY = MRY + 30;
      const pulse = 0.50 + 0.50 * Math.sin(elapsed * 0.00065);
      ctx.save();
      ctx.shadowBlur = 24 + glowAmt * 22; ctx.shadowColor = rc(eyeCol, 0.72);
      for (let ai = 0; ai < 3; ai++) {
        const aS = rot + ai * Math.PI * (2 / 3) + 0.16;
        const aE = rot + ai * Math.PI * (2 / 3) + Math.PI * (2 / 3) - 0.16;
        ctx.beginPath(); ctx.ellipse(CX, CY + bob, hRX, hRY, 0, aS, aE);
        ctx.strokeStyle = rc(eyeCol, glowAmt * pulse * 0.68); ctx.lineWidth = 1.1; ctx.stroke();
      }
      ctx.shadowBlur = 0;
      for (let di = 0; di < 16; di++) {
        const da = rot * 0.72 + (di / 16) * Math.PI * 2;
        ctx.beginPath(); ctx.arc(CX + Math.cos(da) * hRX, CY + bob + Math.sin(da) * hRY, 1.15, 0, Math.PI * 2);
        ctx.fillStyle = rc(eyeCol, glowAmt * 0.52); ctx.fill();
      }
      ctx.restore();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 3 — Outer Energy Shell: soft holographic glow, breathing animation
// ══════════════════════════════════════════════════════════════════════════════
function makeL03_OuterShell(): Layer {
  return {
    name: 'Outer Energy Shell',
    draw(ctx, { W, H, CX, CY, bob, MRX, eyeCol, glowAmt, breathPhase, spk, spkEnergy }) {
      const pulse = Math.sin(breathPhase) * 3.5;
      const shG = ctx.createRadialGradient(CX, CY + bob, MRX * 0.48, CX, CY + bob, MRX + 24 + pulse);
      shG.addColorStop(0,    'rgba(0,0,0,0)');
      shG.addColorStop(0.62, rc(eyeCol, glowAmt * 0.18 + (spk ? spkEnergy * 0.10 : 0)));
      shG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = shG; ctx.fillRect(0, 0, W, H);
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// EXTRA — Hood / Collar: holographic energy field and shoulder particles
// (Visually sits between Layer 3 and Layer 4)
// ══════════════════════════════════════════════════════════════════════════════
function makeLX_Hood(): Layer {
  const bands = Array.from({ length: 9 }, (_, i) => ({
    ph: Math.random() * Math.PI * 2, sp: 0.00024 + Math.random() * 0.00021,
    amp: 5 + Math.random() * 9, col: (i % 2 === 0 ? [0,155,255] : [90,55,255]) as RGB,
  }));
  const shld = Array.from({ length: 8 }, (_, i) => ({
    r: 28 + i * 9, ph: (i / 8) * Math.PI * 2, sp: 0.00021 * (i % 2 === 0 ? 1 : -1.3),
  }));
  return {
    name: 'Hood / Collar',
    draw(ctx, { W, H, CX, CY, bob, MRX, MRY, eyeCol, glowAmt, elapsed, spk, spkEnergy }) {
      const hTopY = CY + bob + MRY * 0.50;
      const pulse  = 0.5 + 0.5 * Math.sin(elapsed * 0.00070 + (spk ? spkEnergy * Math.PI : 0));
      const hBG = ctx.createLinearGradient(CX, hTopY, CX, H + 10);
      hBG.addColorStop(0,    'rgba(0,0,0,0)');
      hBG.addColorStop(0.14, rc(eyeCol, 0.07 * pulse));
      hBG.addColorStop(0.42, 'rgba(3,2,18,0.68)');
      hBG.addColorStop(0.82, 'rgba(2,1,12,0.86)');
      hBG.addColorStop(1,    'rgba(0,0,8,0.95)');
      ctx.fillStyle = hBG; ctx.fillRect(0, hTopY, W, H + 10 - hTopY);
      bands.forEach((b, bi) => {
        const by = CY + bob + MRY * 0.52 + bi * 11 + Math.sin(elapsed * b.sp + b.ph) * b.amp * 0.36;
        if (by > H + 4) return;
        const df    = Math.min(1, (by - hTopY) / (H - hTopY + 10));
        const alpha = 0.11 * pulse * (1 - df * 0.85);
        if (alpha < 0.003) return;
        const hw = Math.min(W * 0.52, MRX * 1.35 + (by - hTopY) * 0.80);
        const bG = ctx.createLinearGradient(CX - hw, by, CX + hw, by);
        bG.addColorStop(0, 'rgba(0,0,0,0)'); bG.addColorStop(0.18, rc(b.col, alpha * 0.55));
        bG.addColorStop(0.50, rc(b.col, alpha)); bG.addColorStop(0.82, rc(b.col, alpha * 0.55));
        bG.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = bG; ctx.fillRect(CX - hw, by - 2.2, hw * 2, 4.5);
        if (bi % 2 === 0) {
          const ox = CX + Math.sin(elapsed * b.sp * 0.7 + bi * 1.3) * hw * 0.38;
          ctx.beginPath(); ctx.moveTo(ox, hTopY + 6);
          ctx.quadraticCurveTo(ox + Math.sin(elapsed * 0.00038 + bi) * 14, by, ox - Math.sin(elapsed * 0.00052 + bi) * 9, by + 12);
          ctx.strokeStyle = rc(b.col, alpha * 0.35); ctx.lineWidth = 0.5; ctx.stroke();
        }
      });
      shld.forEach(p => {
        const py = H - 28 - (p.r - 28) / 9 * 4 + Math.sin(elapsed * 0.00085 + p.ph) * 4;
        ctx.beginPath(); ctx.arc(CX + Math.cos(elapsed * p.sp + p.ph) * p.r, py, 0.7, 0, Math.PI * 2);
        ctx.fillStyle = rc(eyeCol, 0.17 * pulse * glowAmt); ctx.fill();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// EXTRA — Orbit Particles: back arc (behind mask)
// ══════════════════════════════════════════════════════════════════════════════
function makeLX_OrbitBack(): Layer {
  const orbs = Array.from({ length: 32 }, (_, i) => {
    const col: RGB = i % 3 === 0 ? [0,220,255] : i % 3 === 1 ? [70,155,255] : [155,90,255];
    return { ang: (i / 32) * Math.PI * 2 + Math.random() * 0.6, r: 74 * (1.18 + Math.random() * 0.55),
      sp: (0.18 + Math.random() * 0.32) * (i % 2 === 0 ? 1 : -1) * 0.001,
      sz: 0.6 + Math.random() * 1.5, ph: Math.random() * Math.PI * 2, col };
  });
  return {
    name: 'Orbit Particles (back)',
    draw(ctx, { CX, CY, bob, glowAmt, elapsed, dt }) {
      orbs.forEach(p => {
        p.ang += p.sp * dt * 1000;
        const py = CY + bob + Math.sin(p.ang) * p.r * 0.46;
        if (py < CY + bob) return;
        const a = 0.5 + 0.5 * Math.sin(p.ph + elapsed * 0.00055);
        ctx.beginPath(); ctx.arc(CX + Math.cos(p.ang) * p.r, py, p.sz, 0, Math.PI * 2);
        ctx.fillStyle = rc(p.col, glowAmt * a * 0.58); ctx.fill();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 4 — Head Shape: smooth matte-black mask oval, no facial details
// ══════════════════════════════════════════════════════════════════════════════
function makeL04_HeadShape(): Layer {
  return {
    name: 'Head Shape',
    draw(ctx, { CX, CY, bob, MRX, MRY }) {
      // Soft outer feather — blends the mask edge into the transparent background
      const featherG = ctx.createRadialGradient(CX, CY + bob, MRX * 0.80, CX, CY + bob, MRX + 20);
      featherG.addColorStop(0,    'rgba(5,6,16,0.95)');
      featherG.addColorStop(0.35, 'rgba(3,4,12,0.75)');
      featherG.addColorStop(0.65, 'rgba(1,2,8,0.35)');
      featherG.addColorStop(0.85, 'rgba(0,0,4,0.12)');
      featherG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX + 20, MRY + 24, 0, 0, Math.PI * 2);
      ctx.fillStyle = featherG; ctx.fill();
      // Hard mask body on top of the feather
      const mG = ctx.createRadialGradient(CX - 16, CY + bob - 38, 4, CX + 4, CY + bob + 12, MRY * 1.12);
      mG.addColorStop(0,    'rgba(16,20,40,0.98)');
      mG.addColorStop(0.38, 'rgba(9,12,26,0.99)');
      mG.addColorStop(0.78, 'rgba(5,6,16,1.00)');
      mG.addColorStop(1,    'rgba(2,2,9,1.00)');
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX, MRY, 0, 0, Math.PI * 2);
      ctx.fillStyle = mG; ctx.fill();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 5 — Internal Lighting: blue light moving beneath the surface
// ══════════════════════════════════════════════════════════════════════════════
function makeL05_InternalLighting(): Layer {
  let lx = 0, ly = 0;
  return {
    name: 'Internal Lighting',
    draw(ctx, { W, H, CX, CY, bob, MRX, MRY, eyeCol, glowAmt, elapsed }) {
      const tx = Math.sin(elapsed * 0.00031) * MRX * 0.36;
      const ty = Math.sin(elapsed * 0.00047) * MRY * 0.26;
      lx += (tx - lx) * 0.038; ly += (ty - ly) * 0.038;
      ctx.save();
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX, MRY, 0, 0, Math.PI * 2); ctx.clip();
      const ilG = ctx.createRadialGradient(CX + lx, CY + bob + ly, 0, CX + lx, CY + bob + ly, MRX * 0.70);
      ilG.addColorStop(0,    rc(eyeCol, glowAmt * 0.14));
      ilG.addColorStop(0.45, rc(eyeCol, glowAmt * 0.05));
      ilG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = ilG; ctx.fillRect(0, 0, W, H);
      ctx.restore();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// EXTRA — Mask Lighting: cinematic key light, rim light, specular crescent
// ══════════════════════════════════════════════════════════════════════════════
function makeLX_MaskLighting(): Layer {
  return {
    name: 'Mask Lighting',
    draw(ctx, { W, H, CX, CY, bob, MRX, MRY, eyeCol, glowAmt }) {
      ctx.save();
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX, MRY, 0, 0, Math.PI * 2); ctx.clip();
      const kI = glowAmt * 0.36;
      const kG = ctx.createRadialGradient(CX - 32, CY + bob - 55, 0, CX, CY + bob, MRX * 1.18);
      kG.addColorStop(0,    rc(eyeCol, kI * 0.88));
      kG.addColorStop(0.32, rc(eyeCol, kI * 0.38));
      kG.addColorStop(0.68, rc([35,55,130], 0.09));
      kG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = kG; ctx.fillRect(0, 0, W, H);
      const rG = ctx.createRadialGradient(CX + MRX * 0.90, CY + bob + 4, 0, CX + 14, CY + bob + 18, MRX * 1.05);
      rG.addColorStop(0,   rc([155,72,255], glowAmt * 0.30));
      rG.addColorStop(0.5, rc([90,50,195],  0.08));
      rG.addColorStop(1,   'rgba(0,0,0,0)');
      ctx.fillStyle = rG; ctx.fillRect(0, 0, W, H);
      const refG = ctx.createLinearGradient(CX - MRX * 0.70, CY + bob - MRY * 0.60, CX + MRX * 0.08, CY + bob + MRY * 0.16);
      refG.addColorStop(0,    'rgba(175,208,255,0.050)');
      refG.addColorStop(0.38, 'rgba(155,195,255,0.020)');
      refG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = refG; ctx.fillRect(0, 0, W, H);
      ctx.restore();
      ctx.save();
      ctx.shadowBlur = 20 + glowAmt * 16; ctx.shadowColor = rc(eyeCol, 0.52);
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX, MRY, 0, 0, Math.PI * 2);
      ctx.strokeStyle = rc(eyeCol, 0.15 + glowAmt * 0.09); ctx.lineWidth = 1.1; ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX + 0.4, MRY + 0.4, 0, Math.PI * 1.05, Math.PI * 1.72);
      ctx.strokeStyle = `rgba(195,225,255,${0.18 + glowAmt * 0.08})`; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.restore();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 6 — Eye Sockets: recessed shadows around each eye
// ══════════════════════════════════════════════════════════════════════════════
function makeL06_EyeSockets(): Layer {
  return {
    name: 'Eye Sockets',
    draw(ctx, { CX, CY, bob }) {
      [CX - 30, CX + 30].forEach(ex => {
        const ey = CY + bob - 19;
        const sg = ctx.createRadialGradient(ex, ey + 2, 0, ex, ey + 3, 22);
        sg.addColorStop(0,    'rgba(0,0,14,0.97)');
        sg.addColorStop(0.55, 'rgba(0,0,6,0.62)');
        sg.addColorStop(1,    'rgba(0,0,0,0)');
        ctx.beginPath(); ctx.ellipse(ex, ey + 2, 20, 13, 0, 0, Math.PI * 2);
        ctx.fillStyle = sg; ctx.fill();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 7 — Eyebrows: independent, animate while thinking
// ══════════════════════════════════════════════════════════════════════════════
function makeL07_Eyebrows(): Layer {
  return {
    name: 'Eyebrows',
    draw(ctx, { CX, CY, bob, eyeCol, glowAmt, browLift, avState, elapsed }) {
      const wave = (avState === 'ANALYZING') ? Math.sin(elapsed * 0.0040) * 0.8 : 0;
      const defs = [
        { cx: CX - 30, tilt: -0.08, wv:  wave },
        { cx: CX + 30, tilt:  0.08, wv: -wave * 0.5 },
      ];
      defs.forEach(d => {
        const by = CY + bob - 33 - browLift + d.wv;
        ctx.save();
        ctx.shadowBlur = 3; ctx.shadowColor = rc(eyeCol, 0.38);
        ctx.beginPath();
        ctx.moveTo(d.cx - 14, by + 2 * d.tilt);
        ctx.quadraticCurveTo(d.cx, by - 2.2, d.cx + 14, by + 2 * d.tilt);
        ctx.strokeStyle = rc(eyeCol, 0.38 + glowAmt * 0.08); ctx.lineWidth = 1.5; ctx.stroke();
        ctx.restore();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 8 — Eyeballs: dark globes that move together for gaze tracking
// ══════════════════════════════════════════════════════════════════════════════
function makeL08_Eyeballs(): Layer {
  return {
    name: 'Eyeballs',
    draw(ctx, { CX, CY, bob, gazeX, gazeY, blinkPct }) {
      if (blinkPct >= 0.98) return;
      [{ ex: CX - 30, tilt: -0.055 }, { ex: CX + 30, tilt: 0.055 }].forEach(({ ex, tilt }) => {
        const gx = ex + gazeX * 0.55, gy = CY + bob - 19 + gazeY * 0.38;
        const ebG = ctx.createRadialGradient(gx, gy, 0, gx, gy, 14);
        ebG.addColorStop(0, 'rgba(0,0,14,0.98)'); ebG.addColorStop(1, 'rgba(0,0,6,0.58)');
        ctx.beginPath(); ctx.ellipse(gx, gy, 13, 9.5, tilt, 0, Math.PI * 2);
        ctx.fillStyle = ebG; ctx.fill();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 9 — Irises: glow independently, brightness changes, pupil dilation
// ══════════════════════════════════════════════════════════════════════════════
function makeL09_Irises(): Layer {
  let iRot = 0;
  return {
    name: 'Irises',
    draw(ctx, { W, H, CX, CY, bob, gazeX, gazeY, blinkPct, eyeCol, glowAmt, pupilR, avState, spk, spkEnergy, dt }) {
      if (blinkPct >= 0.98) return;
      iRot += 0.0000118 * dt * 1000;
      const EH0 = avState === 'ANALYZING' ? 8.1 : 7.4;
      const EH  = EH0 * (1 - blinkPct * 0.97);
      const glPx = glowAmt * 30 + (spk ? spkEnergy * 16 : 0);
      const eDefs = [{ bx: CX - 30, tilt: -0.055 }, { bx: CX + 30, tilt: 0.055 }];
      eDefs.forEach(({ bx, tilt }) => {
        const ex = bx + gazeX * 0.52, ey = CY + bob - 19 + gazeY * 0.36;
        const iR = 15.5 * 0.76, iH = Math.max(0.4, EH * 0.90);
        // Iris field + spokes
        ctx.save();
        ctx.beginPath(); ctx.ellipse(ex, ey, iR, iH, tilt, 0, Math.PI * 2); ctx.clip();
        const iG = ctx.createRadialGradient(ex, ey, 0, ex, ey, iR);
        iG.addColorStop(0, 'rgba(0,2,26,0.98)'); iG.addColorStop(0.48, rc(eyeCol, 0.16)); iG.addColorStop(1, rc(eyeCol, 0.26));
        ctx.fillStyle = iG; ctx.fillRect(0, 0, W, H);
        for (let fi = 0; fi < 12; fi++) {
          const fa = (fi / 12) * Math.PI * 2 + iRot;
          ctx.beginPath();
          ctx.moveTo(ex + Math.cos(fa) * 2.1, ey + Math.sin(fa) * 1.9);
          ctx.lineTo(ex + Math.cos(fa) * iR,  ey + Math.sin(fa) * iH);
          ctx.strokeStyle = rc(eyeCol, fi % 3 === 0 ? 0.20 : 0.09); ctx.lineWidth = 0.30; ctx.stroke();
        }
        ctx.restore();
        // Outer iris ring (bright — the defining feature)
        ctx.save();
        ctx.shadowBlur = glPx * 0.92; ctx.shadowColor = rc(eyeCol, 0.98);
        ctx.beginPath(); ctx.ellipse(ex, ey, iR, iH, tilt, 0, Math.PI * 2);
        ctx.strokeStyle = rc(eyeCol, 0.65 + glowAmt * 0.24); ctx.lineWidth = 1.85; ctx.stroke();
        ctx.restore();
        // Mid depth ring
        ctx.beginPath(); ctx.ellipse(ex, ey, 15.5 * 0.46, Math.max(0.3, EH * 0.55), tilt, 0, Math.PI * 2);
        ctx.strokeStyle = rc(eyeCol, 0.32); ctx.lineWidth = 0.85; ctx.stroke();
        // Pupil
        ctx.save();
        ctx.shadowBlur = 5; ctx.shadowColor = 'rgba(0,0,0,1)';
        ctx.beginPath(); ctx.ellipse(ex, ey, pupilR, Math.max(0.2, pupilR * 0.91), tilt, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(0,0,10,1)'; ctx.fill();
        ctx.restore();
        // Inner "thinking" halo
        const ipR = pupilR * 1.58;
        ctx.save();
        ctx.shadowBlur = 10 + glowAmt * 9; ctx.shadowColor = rc(eyeCol, 1.0);
        ctx.beginPath(); ctx.ellipse(ex, ey, ipR, Math.max(0.2, ipR * 0.90), tilt, 0, Math.PI * 2);
        ctx.strokeStyle = rc(eyeCol, 0.68 + glowAmt * 0.22); ctx.lineWidth = 0.95; ctx.stroke();
        ctx.restore();
        // Socket ambient corona
        ctx.save();
        ctx.shadowBlur = glPx * 0.78; ctx.shadowColor = rc(eyeCol, 0.90);
        ctx.beginPath(); ctx.ellipse(bx, CY + bob - 19, 15.5 + 2.5, Math.max(0.4, EH0 + 2.5), tilt, 0, Math.PI * 2);
        ctx.strokeStyle = rc(eyeCol, 0.07 + glowAmt * 0.05); ctx.lineWidth = 4.5; ctx.stroke();
        ctx.restore();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 10 — Eye Reflections: multiple moving highlights on each iris
// ══════════════════════════════════════════════════════════════════════════════
function makeL10_EyeReflections(): Layer {
  const rs = [
    { ox: -3.0, oy: -0.50, sz: 1.75, a: 0.94, ph: Math.random() * Math.PI * 2, sp: 0.00038 + Math.random() * 0.00028 },
    { ox:  1.9, oy:  0.24, sz: 0.78, a: 0.38, ph: Math.random() * Math.PI * 2, sp: 0.00050 + Math.random() * 0.00020 },
  ];
  return {
    name: 'Eye Reflections',
    draw(ctx, { CX, CY, bob, gazeX, gazeY, blinkPct, eyeCol, glowAmt, elapsed }) {
      if (blinkPct >= 0.98) return;
      const EH0 = 7.4;
      [{ bx: CX - 30 }, { bx: CX + 30 }].forEach(({ bx }) => {
        const ex = bx + gazeX * 0.52, ey = CY + bob - 19 + gazeY * 0.36;
        rs.forEach(r => {
          const fl = 0.90 + 0.10 * Math.sin(elapsed * r.sp + r.ph);
          ctx.save(); ctx.shadowBlur = 4; ctx.shadowColor = rc(eyeCol, 0.60);
          ctx.beginPath(); ctx.arc(ex + r.ox, ey + r.oy * EH0, r.sz * fl, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(218,240,255,${r.a * fl * (0.70 + glowAmt * 0.30)})`; ctx.fill();
          ctx.restore();
        });
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 11 — Upper Eyelids: independent animation, glowing edge arc
// ══════════════════════════════════════════════════════════════════════════════
function makeL11_UpperEyelids(): Layer {
  return {
    name: 'Upper Eyelids',
    draw(ctx, { W, H, CX, CY, bob, blinkPct, eyeCol }) {
      const EH0 = 7.4, EH = EH0 * (1 - blinkPct * 0.97);
      [{ bx: CX - 30, tilt: -0.055 }, { bx: CX + 30, tilt: 0.055 }].forEach(({ bx, tilt }) => {
        const ey = CY + bob - 19, ldOff = EH0 * blinkPct * 0.82;
        ctx.save();
        ctx.shadowBlur = 4; ctx.shadowColor = rc(eyeCol, 0.44);
        ctx.beginPath();
        ctx.ellipse(bx, ey - EH * 0.04 + ldOff, 15.5 * 0.94, Math.max(0.4, EH + ldOff * 0.25), tilt, Math.PI * 0.91, Math.PI * 2.09);
        ctx.strokeStyle = rc(eyeCol, 0.54); ctx.lineWidth = 1.35; ctx.stroke();
        // Lid shadow
        ctx.beginPath(); ctx.ellipse(bx, ey, 15.5 * 0.90, Math.max(0.4, EH + 0.4), tilt, 0, Math.PI * 2); ctx.clip();
        const lsG = ctx.createLinearGradient(bx, ey - EH - 1, bx, ey + EH * 0.20);
        lsG.addColorStop(0, 'rgba(0,0,0,0.68)'); lsG.addColorStop(0.55, 'rgba(0,0,0,0)');
        ctx.fillStyle = lsG; ctx.fillRect(0, 0, W, H);
        ctx.restore();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 12 — Lower Eyelids: subtle crescent shadow beneath each eye
// ══════════════════════════════════════════════════════════════════════════════
function makeL12_LowerEyelids(): Layer {
  return {
    name: 'Lower Eyelids',
    draw(ctx, { CX, CY, bob, blinkPct, eyeCol }) {
      const EH0 = 7.4;
      [{ bx: CX - 30, tilt: -0.055 }, { bx: CX + 30, tilt: 0.055 }].forEach(({ bx, tilt }) => {
        ctx.save();
        ctx.beginPath();
        ctx.ellipse(bx, CY + bob - 19 + EH0 * 0.90, 15.5 * 0.86, 3.2, tilt, 0, Math.PI);
        ctx.strokeStyle = rc(eyeCol, 0.11 + blinkPct * 0.08); ctx.lineWidth = 0.8; ctx.stroke();
        ctx.restore();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 13 — Blink: single animation controlling holographic membrane closure
// ══════════════════════════════════════════════════════════════════════════════
function makeL13_Blink(): Layer {
  return {
    name: 'Blink',
    draw(ctx, { W, H, CX, CY, bob, blinkPct, eyeCol, glowAmt }) {
      if (blinkPct <= 0.04) return;
      const EH0 = 7.4;
      [{ bx: CX - 30, tilt: -0.055 }, { bx: CX + 30, tilt: 0.055 }].forEach(({ bx, tilt }) => {
        const ey = CY + bob - 19;
        ctx.save();
        ctx.beginPath(); ctx.ellipse(bx, ey, 15.5 * 0.97, EH0 + 1.0, tilt, 0, Math.PI * 2); ctx.clip();
        const btY = ey - EH0, bbY = btY + (EH0 * 2.1 + 1.8) * blinkPct + 1;
        const bG  = ctx.createLinearGradient(bx, btY, bx, bbY);
        bG.addColorStop(0,    rc(eyeCol, Math.min(0.52, 0.42 + glowAmt * 0.08)));
        bG.addColorStop(0.38, rc([20,28,58], 0.28));
        bG.addColorStop(1,    'rgba(0,0,0,0)');
        ctx.fillStyle = bG; ctx.fillRect(0, 0, W, H);
        ctx.restore();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 14 — Forehead Energy: processing activity, brightest while analyzing
// ══════════════════════════════════════════════════════════════════════════════
function makeL14_ForeheadEnergy(): Layer {
  return {
    name: 'Forehead Energy',
    draw(ctx, { W, H, CX, CY, bob, MRX, MRY, eyeCol, glowAmt, elapsed, avState }) {
      const intensity = avState === 'ANALYZING' ? glowAmt
        : (avState === 'READY_LONG' || avState === 'READY_SHORT') ? glowAmt * 0.60
        : glowAmt * 0.26;
      if (intensity < 0.04) return;
      ctx.save();
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX, MRY, 0, 0, Math.PI * 2); ctx.clip();
      const fy  = CY + bob - MRY * 0.60;
      const fpx = CX + Math.sin(elapsed * 0.00027) * MRX * 0.30;
      const feG = ctx.createRadialGradient(fpx, fy, 0, fpx, fy, 40);
      feG.addColorStop(0,    rc(eyeCol, intensity * 0.28));
      feG.addColorStop(0.55, rc(eyeCol, intensity * 0.08));
      feG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = feG; ctx.fillRect(0, 0, W, H);
      ctx.restore();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// EXTRA — Nose Bridge: lighting only, no shape, no nostrils
// ══════════════════════════════════════════════════════════════════════════════
function makeLX_NoseBridge(): Layer {
  return {
    name: 'Nose Bridge',
    draw(ctx, { CX, CY, bob, eyeCol }) {
      const nbG = ctx.createLinearGradient(CX - 6, CY + bob - 8, CX + 1, CY + bob + 18);
      nbG.addColorStop(0,    'rgba(0,0,0,0)');
      nbG.addColorStop(0.42, rc(eyeCol, 0.07));
      nbG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = nbG; ctx.fillRect(CX - 9, CY - 11 + bob, 8, 30);
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 15 — Mouth Line: thin illuminated line when idle, disappears when silent
// ══════════════════════════════════════════════════════════════════════════════
function makeL15_MouthLine(): Layer {
  return {
    name: 'Mouth Line',
    draw(ctx, { CX, CY, bob, eyeCol, glowAmt, spk, jawDrop, mouthCorner }) {
      if (jawDrop > 0.7) return;
      const MY = CY + bob + 53, MW = 13.5;
      ctx.save();
      ctx.shadowBlur = spk ? 5 : 2; ctx.shadowColor = rc(eyeCol, 0.28);
      ctx.beginPath();
      ctx.moveTo(CX - MW, MY + mouthCorner);
      ctx.bezierCurveTo(CX - MW * 0.45, MY + mouthCorner - 1.2, CX + MW * 0.45, MY + mouthCorner - 1.2, CX + MW, MY + mouthCorner);
      ctx.strokeStyle = rc(eyeCol, spk ? 0.40 : 0.09 + glowAmt * 0.04); ctx.lineWidth = 0.65; ctx.stroke();
      ctx.restore();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 16 — Upper Lip: independent bezier arc when speaking
// ══════════════════════════════════════════════════════════════════════════════
function makeL16_UpperLip(): Layer {
  return {
    name: 'Upper Lip',
    draw(ctx, { CX, CY, bob, eyeCol, jawDrop, mouthCorner }) {
      if (jawDrop < 0.7) return;
      const MY = CY + bob + 53, MW = 13.5;
      ctx.save(); ctx.shadowBlur = 5; ctx.shadowColor = rc(eyeCol, 0.30);
      ctx.beginPath();
      ctx.moveTo(CX - MW, MY + mouthCorner);
      ctx.bezierCurveTo(CX - MW * 0.52, MY + mouthCorner - 2.2, CX + MW * 0.52, MY + mouthCorner - 2.2, CX + MW, MY + mouthCorner);
      ctx.strokeStyle = rc(eyeCol, 0.40); ctx.lineWidth = 0.95; ctx.stroke();
      ctx.restore();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 17 — Lower Lip: independent bezier arc below jaw drop
// ══════════════════════════════════════════════════════════════════════════════
function makeL17_LowerLip(): Layer {
  return {
    name: 'Lower Lip',
    draw(ctx, { CX, CY, bob, eyeCol, jawDrop, mouthCorner }) {
      if (jawDrop < 0.7) return;
      const MY = CY + bob + 53, MW = 13.5, drop = jawDrop * 0.48;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(CX - MW * 0.80, MY + mouthCorner + drop * 0.28);
      ctx.bezierCurveTo(CX - MW * 0.38, MY + mouthCorner + drop, CX + MW * 0.38, MY + mouthCorner + drop, CX + MW * 0.80, MY + mouthCorner + drop * 0.28);
      ctx.strokeStyle = rc(eyeCol, 0.30); ctx.lineWidth = 0.85; ctx.stroke();
      ctx.restore();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 18 — Jaw: slight movement while speaking, oral void
// ══════════════════════════════════════════════════════════════════════════════
function makeL18_Jaw(): Layer {
  return {
    name: 'Jaw',
    draw(ctx, { CX, CY, bob, jawDrop, mouthCorner }) {
      if (jawDrop < 0.7) return;
      const MY = CY + bob + 53, MW = 13.5, drop = jawDrop * 0.48;
      const oG = ctx.createRadialGradient(CX, MY + mouthCorner + drop * 0.36, 0, CX, MY + mouthCorner + drop * 0.36, MW * 0.65);
      oG.addColorStop(0,   'rgba(0,0,14,0.95)');
      oG.addColorStop(0.6, 'rgba(0,0,6,0.58)');
      oG.addColorStop(1,   'rgba(0,0,0,0)');
      ctx.beginPath(); ctx.ellipse(CX, MY + mouthCorner + drop * 0.36, MW * 0.65, Math.max(0.4, drop * 0.46), 0, 0, Math.PI * 2);
      ctx.fillStyle = oG; ctx.fill();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 19 — Speech Glow: subtle pulse around mouth while talking
// ══════════════════════════════════════════════════════════════════════════════
function makeL19_SpeechGlow(): Layer {
  return {
    name: 'Speech Glow',
    draw(ctx, { W, H, CX, CY, bob, eyeCol, spk, spkEnergy }) {
      if (!spk || spkEnergy < 0.06) return;
      const MY = CY + bob + 53;
      const spG = ctx.createRadialGradient(CX, MY, 0, CX, MY, 30 + spkEnergy * 13);
      spG.addColorStop(0,    rc(eyeCol, spkEnergy * 0.28));
      spG.addColorStop(0.55, rc(eyeCol, spkEnergy * 0.09));
      spG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = spG; ctx.fillRect(0, 0, W, H);
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 20 — Thinking Particles: appear near forehead while analyzing
// ══════════════════════════════════════════════════════════════════════════════
function makeL20_ThinkingParticles(): Layer {
  const pts = Array.from({ length: 18 }, () => ({
    x: 0, y: 0, vx: (Math.random() - 0.5) * 0.32, vy: -0.18 - Math.random() * 0.38,
    life: Math.random(), sz: 0.55 + Math.random() * 1.1, ph: Math.random() * Math.PI * 2,
  }));
  return {
    name: 'Thinking Particles',
    draw(ctx, { CX, CY, bob, MRX, MRY, eyeCol, glowAmt, avState }) {
      const active = avState === 'ANALYZING' || avState === 'FORMING';
      if (!active || glowAmt < 0.08) return;
      const spawnY = CY + bob - MRY * 0.54;
      pts.forEach(p => {
        p.life -= 0.008;
        if (p.life <= 0) {
          p.x = CX + (Math.random() - 0.5) * MRX * 0.95;
          p.y = spawnY + (Math.random() - 0.5) * 10;
          p.vx = (Math.random() - 0.5) * 0.34; p.vy = -0.14 - Math.random() * 0.36;
          p.life = 0.55 + Math.random() * 0.45; p.sz = 0.5 + Math.random() * 1.2;
        }
        p.x += p.vx; p.y += p.vy;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.sz, 0, Math.PI * 2);
        ctx.fillStyle = rc(eyeCol, p.life * glowAmt * 0.72); ctx.fill();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// EXTRA — Orbit Particles front arc (in front of mask, top half)
// ══════════════════════════════════════════════════════════════════════════════
function makeLX_OrbitFront(backLayerOrbs: ReturnType<typeof Array.from>): Layer {
  return {
    name: 'Orbit Particles (front)',
    draw(ctx, { CX, CY, bob, glowAmt, elapsed }) {
      (backLayerOrbs as { ang:number; r:number; sp:number; sz:number; ph:number; col:RGB }[]).forEach(p => {
        const py = CY + bob + Math.sin(p.ang) * p.r * 0.46;
        if (py >= CY + bob) return;
        const a = 0.5 + 0.5 * Math.sin(p.ph + elapsed * 0.00055);
        ctx.beginPath(); ctx.arc(CX + Math.cos(p.ang) * p.r, py, p.sz, 0, Math.PI * 2);
        ctx.fillStyle = rc(p.col, glowAmt * a * 0.50); ctx.fill();
      });
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 21 — Confidence Ring: color changes per state (Blue/Yellow/Orange/Green/Red)
// ══════════════════════════════════════════════════════════════════════════════
function makeL21_ConfidenceRing(): Layer {
  let rot = 0;
  return {
    name: 'Confidence Ring',
    draw(ctx, { CX, CY, bob, MRX, MRY, ringCol, glowAmt, elapsed, avState, dt }) {
      const spd = avState === 'ANALYZING' ? 0.0014 : (avState === 'READY_LONG' || avState === 'READY_SHORT') ? 0.00065 : 0.00028;
      rot += spd * dt * 1000;
      const cRX = MRX + 11 + Math.sin(elapsed * 0.00064) * 1.4;
      const cRY = MRY + 11 + Math.sin(elapsed * 0.00064) * 1.0;
      ctx.save();
      ctx.shadowBlur = 7; ctx.shadowColor = rc(ringCol, 0.55);
      for (let ai = 0; ai < 4; ai++) {
        const aS = rot + ai * Math.PI * 0.5 + 0.10;
        const aE = rot + ai * Math.PI * 0.5 + Math.PI * 0.5 - 0.10;
        ctx.beginPath(); ctx.ellipse(CX, CY + bob, cRX, cRY, 0, aS, aE);
        ctx.strokeStyle = rc(ringCol, glowAmt * 0.52); ctx.lineWidth = 0.9; ctx.stroke();
      }
      ctx.shadowBlur = 0;
      for (let di = 0; di < 8; di++) {
        const da = rot + di * Math.PI * 0.25, active = di % 2 === 0;
        ctx.beginPath(); ctx.arc(CX + Math.cos(da) * cRX, CY + bob + Math.sin(da) * cRY, active ? 2.0 : 0.9, 0, Math.PI * 2);
        ctx.fillStyle = rc(ringCol, active ? glowAmt * 0.80 : glowAmt * 0.28); ctx.fill();
      }
      ctx.restore();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 22 — Scanning Lines: holographic scan, appears occasionally
// ══════════════════════════════════════════════════════════════════════════════
function makeL22_ScanLines(): Layer {
  let scanY = -1, timer = 6 + Math.random() * 8;
  return {
    name: 'Scanning Lines',
    draw(ctx, { CX, CY, bob, MRX, MRY, eyeCol, glowAmt, dt }) {
      timer -= dt;
      if (timer <= 0) { scanY = CY + bob - MRY; timer = 6 + Math.random() * 8; }
      if (scanY < 0) return;
      scanY += 1.75;
      if (scanY > CY + bob + MRY + 4) { scanY = -1; return; }
      ctx.save();
      ctx.beginPath(); ctx.ellipse(CX, CY + bob, MRX, MRY, 0, 0, Math.PI * 2); ctx.clip();
      const prog  = (scanY - (CY + bob - MRY)) / (MRY * 2);
      const alpha = glowAmt * 0.20 * Math.sin(prog * Math.PI);
      ctx.beginPath(); ctx.moveTo(CX - MRX, scanY); ctx.lineTo(CX + MRX, scanY);
      ctx.strokeStyle = rc(eyeCol, alpha); ctx.lineWidth = 1.5; ctx.stroke();
      [scanY - 4, scanY - 10].forEach((ly, i) => {
        ctx.beginPath(); ctx.moveTo(CX - MRX, ly); ctx.lineTo(CX + MRX, ly);
        ctx.strokeStyle = rc(eyeCol, alpha * (i === 0 ? 0.38 : 0.12)); ctx.lineWidth = 0.7; ctx.stroke();
      });
      ctx.restore();
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 23 — Ambient Fog: very soft, slow-moving atmospheric glow
// ══════════════════════════════════════════════════════════════════════════════
function makeL23_AmbientFog(): Layer {
  return {
    name: 'Ambient Fog',
    draw(ctx, { W, H, CX, CY, bob, MRY, eyeCol, glowAmt, elapsed }) {
      const fogY = CY + bob + MRY * 0.60;
      const fX   = CX + Math.sin(elapsed * 0.00020) * 18;
      const fogG = ctx.createRadialGradient(fX, fogY, 0, fX, fogY + 28, 82);
      fogG.addColorStop(0,   rc(eyeCol, glowAmt * 0.055));
      fogG.addColorStop(0.6, rc([50,0,100], 0.035));
      fogG.addColorStop(1,   'rgba(0,0,0,0)');
      ctx.fillStyle = fogG; ctx.fillRect(0, 0, W, H);
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 24 — Interaction: mouse/touch parallax (applied as canvas transform)
// Applied as a ctx.translate before all other layers, restored after.
// ══════════════════════════════════════════════════════════════════════════════
// (No draw object — handled inline in the main loop)

// ══════════════════════════════════════════════════════════════════════════════
// LAYER 25 — Speech Aura: full-face pulse only while speaking
// ══════════════════════════════════════════════════════════════════════════════
function makeL25_SpeechAura(): Layer {
  return {
    name: 'Speech Aura',
    draw(ctx, { W, H, CX, CY, bob, MRX, eyeCol, spk, spkEnergy }) {
      if (!spk || spkEnergy < 0.04) return;
      const spkG = ctx.createRadialGradient(CX, CY + bob, MRX * 0.68, CX, CY + bob, MRX + 9 + spkEnergy * 10);
      spkG.addColorStop(0,    'rgba(0,0,0,0)');
      spkG.addColorStop(0.65, rc(eyeCol, spkEnergy * 0.09));
      spkG.addColorStop(1,    'rgba(0,0,0,0)');
      ctx.fillStyle = spkG; ctx.fillRect(0, 0, W, H);
    },
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// SENTINEL COMPONENT
// ══════════════════════════════════════════════════════════════════════════════
const Sentinel = memo(function Sentinel({
  avState, speaking, gazeEvent, speechCtrlRef, voiceListeningRef,
}: {
  avState:           AvatarState;
  speaking:          boolean;
  ringColor:         string;   // interface parity with AvatarCanvas (unused internally)
  gazeEvent:         GazeEvt;
  speechCtrlRef:     React.MutableRefObject<SpeechCtrl>;
  voiceListeningRef: React.MutableRefObject<boolean>;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef    = useRef(0);

  // Live refs — updated by React, read by draw loop without re-renders
  const stateRef = useRef(avState);
  const speakRef = useRef(speaking);
  const gazeRef  = useRef<{ dx:number; dy:number; t0:number; dur:number }>({ dx:0, dy:0, t0:0, dur:0 });
  const mouseRef = useRef({ x: 0, y: 0 });

  useEffect(() => { stateRef.current = avState; }, [avState]);
  useEffect(() => { speakRef.current = speaking; }, [speaking]);
  useEffect(() => {
    if (gazeEvent.dur > 0)
      gazeRef.current = { dx: gazeEvent.dx, dy: gazeEvent.dy, t0: Date.now(), dur: gazeEvent.dur };
  }, [gazeEvent.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const W = 240, H = 320, CX = 120, CY = 148, MRX = 74, MRY = 97;

    // ── Blink FSM ──────────────────────────────────────────────────────────
    const bk = { pct: 0, state: 'open' as 'open'|'closing'|'closed'|'opening', timer: 2 + Math.random() * 2 };

    // ── Smoothed animation values ──────────────────────────────────────────
    const sm = { gx: 0, gy: 0, glow: 0.42, pupilR: 5.0, browLift: 0, jaw: 0, corner: 0 };

    // ── Build layers in z-order ────────────────────────────────────────────
    const L01 = makeL01_Background();
    const L02 = makeL02_EnergyHalo();
    const L03 = makeL03_OuterShell();
    const LXh = makeLX_Hood();
    const LXbo = makeLX_OrbitBack();
    const L04 = makeL04_HeadShape();
    const L05 = makeL05_InternalLighting();
    const LXml = makeLX_MaskLighting();
    const L06 = makeL06_EyeSockets();
    const L07 = makeL07_Eyebrows();
    const L08 = makeL08_Eyeballs();
    const L09 = makeL09_Irises();
    const L10 = makeL10_EyeReflections();
    const L11 = makeL11_UpperEyelids();
    const L12 = makeL12_LowerEyelids();
    const L13 = makeL13_Blink();
    const L14 = makeL14_ForeheadEnergy();
    const LXn = makeLX_NoseBridge();
    const L15 = makeL15_MouthLine();
    const L16 = makeL16_UpperLip();
    const L17 = makeL17_LowerLip();
    const L18 = makeL18_Jaw();
    const L19 = makeL19_SpeechGlow();
    const L20 = makeL20_ThinkingParticles();

    // Orbit front shares geometry with orbit back — re-read the same ref
    const LXbo_orbs = (LXbo as unknown as { draw: (ctx: CanvasRenderingContext2D, g: G) => void } & { _orbs?: unknown })._orbs;
    // Simpler: orbit front just re-derives; we need shared orb state so pass the layer itself
    const L21 = makeL21_ConfidenceRing();
    const L22 = makeL22_ScanLines();
    const L23 = makeL23_AmbientFog();
    const L25 = makeL25_SpeechAura();

    // We need a front-arc orbit that shares the same particle state as the back-arc.
    // Implementation: the back-layer mutates .ang each frame; front layer reads those same objects.
    // Both layers share the same orbs array via closure. Re-create a shared factory:
    const orbs = Array.from({ length: 32 }, (_, i) => {
      const col: RGB = i % 3 === 0 ? [0,220,255] : i % 3 === 1 ? [70,155,255] : [155,90,255];
      return { ang: (i / 32) * Math.PI * 2 + Math.random() * 0.6, r: MRX * (1.18 + Math.random() * 0.55),
        sp: (0.18 + Math.random() * 0.32) * (i % 2 === 0 ? 1 : -1) * 0.001,
        sz: 0.6 + Math.random() * 1.5, ph: Math.random() * Math.PI * 2, col };
    });
    // Orbit back (update + draw bottom arc)
    const orbitBack: Layer = {
      name: 'Orbit Back',
      draw(ctx, { CX, CY, bob, glowAmt, elapsed, dt }) {
        orbs.forEach(p => {
          p.ang += p.sp * dt * 1000;
          const py = CY + bob + Math.sin(p.ang) * p.r * 0.46;
          if (py < CY + bob) return;
          const a = 0.5 + 0.5 * Math.sin(p.ph + elapsed * 0.00055);
          ctx.beginPath(); ctx.arc(CX + Math.cos(p.ang) * p.r, py, p.sz, 0, Math.PI * 2);
          ctx.fillStyle = rc(p.col, glowAmt * a * 0.58); ctx.fill();
        });
      },
    };
    // Orbit front (draw top arc only — particles already updated by orbitBack)
    const orbitFront: Layer = {
      name: 'Orbit Front',
      draw(ctx, { CX, CY, bob, glowAmt, elapsed }) {
        orbs.forEach(p => {
          const py = CY + bob + Math.sin(p.ang) * p.r * 0.46;
          if (py >= CY + bob) return;
          const a = 0.5 + 0.5 * Math.sin(p.ph + elapsed * 0.00055);
          ctx.beginPath(); ctx.arc(CX + Math.cos(p.ang) * p.r, py, p.sz, 0, Math.PI * 2);
          ctx.fillStyle = rc(p.col, glowAmt * a * 0.50); ctx.fill();
        });
      },
    };

    // Final layer draw order (z-order, back to front):
    const LAYERS: Layer[] = [
      L01,         // 1. Background
      L02,         // 2. Energy Halo
      L03,         // 3. Outer Shell
      LXh,         // ·  Hood / Collar
      orbitBack,   // ·  Orbit particles (back arc)
      L04,         // 4. Head Shape
      L05,         // 5. Internal Lighting
      LXml,        // ·  Mask Lighting (key/rim/specular)
      L06,         // 6. Eye Sockets
      L07,         // 7. Eyebrows
      L08,         // 8. Eyeballs
      L09,         // 9. Irises + pupils
      L10,         // 10. Eye Reflections
      L11,         // 11. Upper Eyelids
      L12,         // 12. Lower Eyelids
      L13,         // 13. Blink membrane
      L14,         // 14. Forehead Energy
      LXn,         // ·  Nose Bridge
      L15,         // 15. Mouth Line
      L16,         // 16. Upper Lip
      L17,         // 17. Lower Lip
      L18,         // 18. Jaw
      L19,         // 19. Speech Glow
      L20,         // 20. Thinking Particles
      orbitFront,  // ·  Orbit particles (front arc)
      L21,         // 21. Confidence Ring
      L22,         // 22. Scanning Lines
      L23,         // 23. Ambient Fog
      // 24. Interaction / Parallax — applied as canvas transform (see below)
      L25,         // 25. Speech Aura
    ];

    const canvas = canvasRef.current; if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: true }); if (!ctx) return;

    // Mouse move → Layer 24 parallax
    const onMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseRef.current.x = ((e.clientX - rect.left) / rect.width  - 0.5) * 2;
      mouseRef.current.y = ((e.clientY - rect.top)  / rect.height - 0.5) * 2;
    };
    canvas.addEventListener('mousemove', onMouseMove);

    const t0 = Date.now(); let lastT = t0;

    function frame() {
      if (!ctx) return;
      const now     = Date.now();
      const elapsed = now - t0;
      const dt      = Math.min((now - lastT) / 1000, 0.05);
      lastT = now;

      const s        = stateRef.current;
      const spkCtrl  = speechCtrlRef.current;
      const spkE     = Math.max(0, Math.min(1, spkCtrl.energy));
      const spkVis   = spkCtrl.viseme || 'rest';
      const spk      = speakRef.current;
      const eyeCol   = EYE_COL[s] || EYE_COL.WAIT;
      const ringCol  = RING_COL[s] || RING_COL.WAIT;
      const glowTgt  = GLOW_TGT[s] || 0.42;

      // ── Smooth glow ──────────────────────────────────────────────────────
      sm.glow += (glowTgt - sm.glow) * 0.038;

      // ── Breathing ────────────────────────────────────────────────────────
      const breathAmp = s === 'NO_EDGE' ? 2.5 : s === 'ANALYZING' ? 1.1 : 1.7;
      const bPhase    = elapsed * 0.00092;
      const bob       = Math.sin(bPhase) * breathAmp;

      // ── Gaze ─────────────────────────────────────────────────────────────
      const gaze = gazeRef.current;
      let tgx = 0, tgy = 0;
      if (gaze.dur > 0) {
        const age = (now - gaze.t0) / 1000;
        if (age < gaze.dur) { tgx = gaze.dx * 6; tgy = gaze.dy * 4; }
        else gazeRef.current = { dx:0, dy:0, t0:0, dur:0 };
      }
      tgx += Math.sin(elapsed * 0.00031 + 1.1) * 2.6 + Math.sin(elapsed * 0.00069) * 1.1;
      tgy += Math.sin(elapsed * 0.00043 + 0.7) * 1.6;
      sm.gx += (tgx - sm.gx) * 0.055; sm.gy += (tgy - sm.gy) * 0.055;

      // ── Pupil ────────────────────────────────────────────────────────────
      const tPupil = (s === 'ANALYZING' || s === 'FORMING')
        ? 3.6 + Math.sin(elapsed * 0.0019) * 0.9
        : spk ? 4.5 + spkE * 1.4 : (s === 'READY_LONG' || s === 'READY_SHORT') ? 5.6 : 5.0;
      sm.pupilR += (tPupil - sm.pupilR) * 0.048;

      // ── Eyebrow lift (Layer 7) ────────────────────────────────────────────
      const tBrow = s === 'ANALYZING' ? 3.5 : s === 'READY_LONG' || s === 'READY_SHORT' ? -1.8 : s === 'NO_EDGE' ? 0 : 0;
      sm.browLift += (tBrow - sm.browLift) * 0.04;

      // ── Mouth / jaw (Layers 15-18) ────────────────────────────────────────
      const tJaw   = spk ? (spkVis === 'press' ? 0 : spkVis === 'rounded' ? spkE * 5.2 : spkVis === 'narrow' ? spkE * 3.8 : spkE * 6.2) : 0;
      const tCorn  = s === 'READY_LONG' || s === 'TARGET_HIT' ? -1.4 : s === 'STOP_HIT' ? 1.2 : 0;
      sm.jaw    += (tJaw  - sm.jaw)    * (spk ? 0.24 : 0.14);
      sm.corner += (tCorn - sm.corner) * 0.06;

      // ── Blink FSM (Layer 13) ──────────────────────────────────────────────
      bk.timer -= dt;
      if (bk.state === 'open'    && bk.timer <= 0)  bk.state = 'closing';
      if (bk.state === 'closing') { bk.pct += dt * 7.5;  if (bk.pct >= 1) { bk.pct = 1; bk.state = 'closed';  bk.timer = 0.06 + Math.random() * 0.05; } }
      if (bk.state === 'closed') { bk.timer -= dt;       if (bk.timer <= 0) bk.state = 'opening'; }
      if (bk.state === 'opening') { bk.pct -= dt * 9.5; if (bk.pct <= 0) { bk.pct = 0; bk.state = 'open'; bk.timer = 2.4 + Math.random() * 3.0; } }

      // ── Assemble global frame state ───────────────────────────────────────
      const g: G = {
        W, H, CX, CY, MRX, MRY,
        elapsed, dt, avState: s, eyeCol, ringCol,
        glowAmt: sm.glow,
        bob,
        breathPhase: bPhase,
        blinkPct: bk.pct,
        gazeX: sm.gx, gazeY: sm.gy,
        pupilR: sm.pupilR,
        browLift: sm.browLift,
        jawDrop: sm.jaw,
        mouthCorner: sm.corner,
        spkEnergy: spkE, spkViseme: spkVis, spk,
        listening: voiceListeningRef.current,
        mouseX: mouseRef.current.x,
        mouseY: mouseRef.current.y,
      };

      ctx.clearRect(0, 0, W, H);

      // ── LAYER 24: Interaction parallax transform ───────────────────────────
      ctx.save();
      ctx.translate(g.mouseX * 2.2, g.mouseY * 1.6); // subtle parallax, max ±2.2 / ±1.6 px

      // ── Draw all layers in z-order ────────────────────────────────────────
      for (let li = 0; li < LAYERS.length; li++) {
        LAYERS[li].draw(ctx, g);
      }

      ctx.restore(); // end parallax transform

      rafRef.current = requestAnimationFrame(frame);
    }

    rafRef.current = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(rafRef.current);
      canvas.removeEventListener('mousemove', onMouseMove);
    };
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
