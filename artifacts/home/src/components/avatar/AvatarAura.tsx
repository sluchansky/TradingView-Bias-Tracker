/**
 * AvatarAura — 2D canvas overlay for the VRM avatar.
 *
 * Draws three concentric elliptical rings + orbiting dots that react to
 * avState and edge score:
 *   WAIT / NO_EDGE   → dim red      (low intensity, slow)
 *   ANALYZING        → amber        (building)
 *   FORMING          → orange       (mid-speed, brightening)
 *   READY_LONG       → green        (fast, bright)
 *   READY_SHORT      → rose-red     (fast, bright)
 *   ACTIVE           → cyan         (pulsing)
 *   TARGET_HIT       → bright green (burst)
 *   STOP_HIT         → red          (flash)
 *
 * The overlay sits at z-index 2, above the VRM canvas but below the corner
 * info panels (z-index ≥ 3).
 */
import React, { useRef, useEffect, memo } from 'react';
import type { AvatarState } from './avatarTypes';

// Canvas matches the avatar box
const W = 420;
const H = 560;

// Orbital center — roughly avatar chest/torso
const CX = 210;
const CY = 235;

// Three ring levels: [radiusX, radiusY, rotationSpeed rad/s]
// ellipse is squished on Y to create a 3-D orbital-plane feel
const RINGS: [number, number, number][] = [
  [120, 44, 0.22],
  [155, 58, -0.14],
  [192, 71,  0.09],
];

// Dash pattern for the rings: [dashLen, gapLen] in arc-degrees
const RING_DASH = [18, 10];

interface Particle {
  ang:   number;   // current angle (radians)
  speed: number;   // rad/s (can be negative)
  ring:  number;   // which ring index (0|1|2)
  sz:    number;   // base radius (px)
  ph:    number;   // phase for opacity oscillation
}

// Static particle set — built once per component mount
function buildParticles(): Particle[] {
  const counts = [9, 12, 11]; // per ring
  const ps: Particle[] = [];
  counts.forEach((n, ri) => {
    for (let i = 0; i < n; i++) {
      const baseSpeed = ri === 0 ? 0.55 : ri === 1 ? 0.38 : 0.25;
      ps.push({
        ang:   (i / n) * Math.PI * 2 + Math.random() * 0.5,
        speed: baseSpeed * (Math.random() * 0.4 + 0.8) * (i % 2 === 0 ? 1 : -1),
        ring:  ri,
        sz:    1.1 + Math.random() * 1.6,
        ph:    Math.random() * Math.PI * 2,
      });
    }
  });
  return ps;
}

// Per-state color as [r, g, b]
const STATE_RGB: Record<AvatarState, [number, number, number]> = {
  WAIT:        [120,  50, 230],  // muted indigo/purple
  NO_EDGE:     [ 90,  50, 200],  // dim indigo
  ANALYZING:   [245, 158,  11],  // amber
  FORMING:     [249, 115,  22],  // orange
  READY_LONG:  [ 34, 197,  94],  // green
  READY_SHORT: [239,  68,  68],  // rose-red
  ACTIVE:      [  6, 182, 212],  // cyan
  TARGET_HIT:  [ 74, 222, 128],  // bright green
  STOP_HIT:    [239,  68,  68],  // red
};

// Edge → intensity 0..1 (controls brightness + speed multiplier)
function edgeIntensity(edge: number): number {
  return Math.min(1, Math.max(0.12, edge / 110));
}

interface AvatarAuraProps {
  avState:  AvatarState;
  edge:     number;
  speaking: boolean;
}

function AvatarAura({ avState, edge, speaking }: AvatarAuraProps) {
  const canvasRef  = useRef<HTMLCanvasElement>(null);
  const rafRef     = useRef(0);
  const particles  = useRef<Particle[]>(buildParticles());
  const stateRef   = useRef({ avState, edge, speaking });

  useEffect(() => { stateRef.current = { avState, edge, speaking }; }, [avState, edge, speaking]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let prev = performance.now();
    let ringAngles = [0, 0, 0];   // accumulated rotation per ring

    const frame = (now: number) => {
      rafRef.current = requestAnimationFrame(frame);
      const dt = Math.min((now - prev) / 1000, 0.08);
      prev = now;

      const { avState: st, edge: eg, speaking: spk } = stateRef.current;
      const inten   = edgeIntensity(eg);
      const speedMx = 0.6 + inten * 1.4 + (spk ? 0.5 : 0);
      const col     = STATE_RGB[st];
      const [r, g, b] = col;

      ctx.clearRect(0, 0, W, H);

      // ── Rings ─────────────────────────────────────────────────────────────
      RINGS.forEach(([rx, ry, rotSpd], ri) => {
        ringAngles[ri] += rotSpd * speedMx * dt;
        const rot = ringAngles[ri];

        // Base ring opacity — outer rings slightly dimmer
        const ringAlpha = (0.10 + inten * 0.22) * (1 - ri * 0.06);

        // Draw dashed ellipse by stepping through angles
        const steps  = 120;
        const degPer = (Math.PI * 2) / steps;
        const dashOn  = RING_DASH[0];
        const dashOff = RING_DASH[1];
        const period  = dashOn + dashOff;

        ctx.beginPath();
        let inDash = false;
        let dashAcc = 0;
        for (let s = 0; s <= steps; s++) {
          const a = s * degPer + rot;
          const px = CX + Math.cos(a) * rx;
          const py = CY + Math.sin(a) * ry;
          const pos = (s % period);
          if (pos < dashOn) {
            if (!inDash) { ctx.moveTo(px, py); inDash = true; }
            else ctx.lineTo(px, py);
          } else {
            inDash = false;
          }
          dashAcc++;
        }
        ctx.strokeStyle = `rgba(${r},${g},${b},${ringAlpha})`;
        ctx.lineWidth = 0.9 + inten * 0.6;
        ctx.stroke();

        // Faint glow copy at slightly higher width
        if (inten > 0.35) {
          ctx.beginPath();
          ctx.globalAlpha = ringAlpha * 0.35;
          for (let s = 0; s <= steps; s++) {
            const a = s * degPer + rot + 0.025;
            const px = CX + Math.cos(a) * rx;
            const py = CY + Math.sin(a) * ry;
            const pos = (s % period);
            if (pos < dashOn) { ctx.lineTo(px, py); }
            else { ctx.moveTo(px, py); }
          }
          ctx.strokeStyle = `rgba(${r},${g},${b},1)`;
          ctx.lineWidth = 2.2 + inten;
          ctx.stroke();
          ctx.globalAlpha = 1;
        }
      });

      // ── Particles ─────────────────────────────────────────────────────────
      particles.current.forEach(p => {
        p.ang += p.speed * speedMx * dt;
        const [rx, ry] = RINGS[p.ring];

        const px = CX + Math.cos(p.ang) * rx;
        const py = CY + Math.sin(p.ang) * ry;

        // Depth cue: particles in the "back" (sin < 0) are slightly dimmer
        const depth    = 0.55 + 0.45 * ((Math.sin(p.ang) + 1) / 2);
        const pulse    = 0.55 + 0.45 * Math.sin(p.ph + now * 0.0008);
        const alpha    = depth * pulse * (0.30 + inten * 0.65) * (spk ? 1.25 : 1);
        const sz       = p.sz * (0.7 + inten * 0.5) * (spk ? 1.15 : 1);

        // Main dot
        ctx.beginPath();
        ctx.arc(px, py, sz, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${r},${g},${b},${Math.min(1, alpha)})`;
        ctx.fill();

        // Glow halo on brighter particles
        if (inten > 0.4) {
          const grad = ctx.createRadialGradient(px, py, 0, px, py, sz * 3.5);
          grad.addColorStop(0, `rgba(${r},${g},${b},${Math.min(0.5, alpha * 0.55)})`);
          grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
          ctx.beginPath();
          ctx.arc(px, py, sz * 3.5, 0, Math.PI * 2);
          ctx.fillStyle = grad;
          ctx.fill();
        }
      });

      // ── Speaking burst: radial flash from center ───────────────────────────
      if (spk) {
        const burstR = 60 + inten * 80;
        const grad = ctx.createRadialGradient(CX, CY, 0, CX, CY, burstR);
        grad.addColorStop(0, `rgba(${r},${g},${b},${0.06 + inten * 0.06})`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.beginPath();
        ctx.arc(CX, CY, burstR, 0, Math.PI * 2);
        ctx.fillStyle = grad;
        ctx.fill();
      }
    };

    rafRef.current = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(rafRef.current);
  }, []);

  return (
    <canvas
      ref={canvasRef}
      width={W}
      height={H}
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        zIndex: 2,
      }}
    />
  );
}

export default memo(AvatarAura);
