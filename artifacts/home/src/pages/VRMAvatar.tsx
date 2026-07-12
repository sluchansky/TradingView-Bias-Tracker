/**
 * VRMAvatar — renders LordPiggington.vrm via Three.js + @pixiv/three-vrm
 * Drops into the same slot as the Sentinel canvas (240×320, transparent bg).
 * Drives expressions, mouth, gaze, and accent lighting from the same props
 * that Sentinel accepted so Home.tsx needs no other changes.
 */
import React, { useRef, useEffect, memo } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import {
  VRMLoaderPlugin,
  VRMUtils,
  VRM,
  VRMExpressionPresetName,
} from '@pixiv/three-vrm';

// ─── Types (mirror Sentinel's interface) ──────────────────────────────────────
type AvatarState =
  | 'WAIT' | 'ANALYZING' | 'FORMING'
  | 'READY_LONG' | 'READY_SHORT'
  | 'NO_EDGE' | 'ACTIVE' | 'STOP_HIT' | 'TARGET_HIT';

type GazeEvt = { dx: number; dy: number; widen: boolean; dur: number; id: number };

interface SpeechCtrl { energy: number; viseme: string; active: boolean; }

interface Props {
  avState: AvatarState;
  speaking: boolean;
  ringColor: string;
  gazeEvent: GazeEvt | null;
  speechCtrlRef: React.RefObject<SpeechCtrl>;
  voiceListeningRef: React.RefObject<boolean>;
}

// ─── Config ───────────────────────────────────────────────────────────────────
const W = 240;
const H = 320;

// Accent light colour per trading state
const STATE_ACCENT: Record<AvatarState, number> = {
  WAIT:        0x0094ff,
  ANALYZING:   0x00b9ff,
  FORMING:     0x00d2ff,
  READY_LONG:  0x00e678,
  READY_SHORT: 0xff5064,
  NO_EDGE:     0x375fb9,
  ACTIVE:      0xffc832,
  STOP_HIT:    0xff3746,
  TARGET_HIT:  0x14ff96,
};

// Pulse speed per state (rad/s)
const STATE_PULSE: Record<AvatarState, number> = {
  WAIT: 1.0, ANALYZING: 2.2, FORMING: 1.8,
  READY_LONG: 3.5, READY_SHORT: 3.5,
  NO_EDGE: 0.6, ACTIVE: 2.8, STOP_HIT: 4.0, TARGET_HIT: 4.0,
};

// ─── Component ────────────────────────────────────────────────────────────────
function VRMAvatar({ avState, speaking, gazeEvent, speechCtrlRef }: Props) {
  const canvasRef     = useRef<HTMLCanvasElement>(null);
  const vrmRef        = useRef<VRM | null>(null);
  const rendererRef   = useRef<THREE.WebGLRenderer | null>(null);
  const accentRef     = useRef<THREE.PointLight | null>(null);
  const rafRef        = useRef<number>(0);

  // Live refs so the render loop always sees current values without re-mounting
  const liveState   = useRef(avState);
  const liveSpeech  = useRef(speechCtrlRef);
  const liveGaze    = useRef({ yaw: 0, pitch: 0, lastId: -1, dx: 0, dy: 0 });
  const blinkState  = useRef({ phase: 'idle' as 'idle'|'closing'|'opening', t: 0, next: 2.5 });
  const mouthSmooth = useRef(0);

  useEffect(() => { liveState.current = avState; }, [avState]);
  useEffect(() => { liveSpeech.current = speechCtrlRef; }, [speechCtrlRef]);
  useEffect(() => {
    if (gazeEvent && gazeEvent.id !== liveGaze.current.lastId) {
      liveGaze.current.dx     = gazeEvent.dx;
      liveGaze.current.dy     = gazeEvent.dy;
      liveGaze.current.lastId = gazeEvent.id;
    }
  }, [gazeEvent]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // ── Renderer ──────────────────────────────────────────────────────────────
    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setSize(W, H);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    rendererRef.current = renderer;

    // ── Scene ─────────────────────────────────────────────────────────────────
    const scene = new THREE.Scene();

    // ── Camera ────────────────────────────────────────────────────────────────
    // Tight portrait: head + upper chest. Adjust Y based on model's head height.
    const camera = new THREE.PerspectiveCamera(20, W / H, 0.01, 30);
    camera.position.set(0, 1.38, 1.10);
    camera.lookAt(0, 1.40, 0);

    // ── Lighting ──────────────────────────────────────────────────────────────
    // Soft ambient to match the dark cockpit palette
    const ambient = new THREE.AmbientLight(0x7080a8, 1.0);
    scene.add(ambient);

    // Key: slightly warm from upper-left
    const key = new THREE.DirectionalLight(0xfff4e0, 1.8);
    key.position.set(1.0, 3.0, 2.0);
    scene.add(key);

    // Fill: cool blue from right
    const fill = new THREE.DirectionalLight(0x5070d0, 0.55);
    fill.position.set(-1.5, 1.5, 1.0);
    scene.add(fill);

    // Rim: electric blue from behind for avatar pop
    const rim = new THREE.DirectionalLight(0x2040c0, 0.90);
    rim.position.set(0, 2.0, -2.0);
    scene.add(rim);

    // Accent point light — colour = trading state, pulses in intensity
    const accent = new THREE.PointLight(0x0094ff, 2.0, 3.0);
    accent.position.set(0.0, 1.42, 0.65);
    scene.add(accent);
    accentRef.current = accent;

    // ── VRM load ─────────────────────────────────────────────────────────────
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));

    loader.load(
      '/avatar.vrm',
      (gltf) => {
        const vrm: VRM = gltf.userData.vrm;

        // Optimise mesh (safe to skip if model is already clean)
        try { VRMUtils.removeUnnecessaryVertices(gltf.scene); } catch (_) { /* */ }

        // VRM 0.x models face away from camera by default — rotate them
        try { VRMUtils.rotateVRM0(vrm); } catch (_) { /* */ }

        scene.add(gltf.scene);
        vrmRef.current = vrm;

        // After load, auto-frame camera to the head if we can find it
        try {
          const head = vrm.humanoid?.getNormalizedBoneNode?.('head');
          if (head) {
            const pos = new THREE.Vector3();
            head.getWorldPosition(pos);
            camera.position.set(0, pos.y + 0.02, 1.10);
            camera.lookAt(0, pos.y + 0.04, 0);
          }
        } catch (_) { /* fallback to preset camera */ }
      },
      undefined,
      (err) => console.error('[VRMAvatar] load error:', err),
    );

    // ── Render loop ───────────────────────────────────────────────────────────
    let prevTime = performance.now();
    let elapsed  = 0;

    function tick() {
      rafRef.current = requestAnimationFrame(tick);

      const now = performance.now();
      const dt  = Math.min((now - prevTime) / 1000, 0.05);
      prevTime  = now;
      elapsed  += dt;

      const vrm = vrmRef.current;
      if (vrm) {
        const em   = vrm.expressionManager;
        const hum  = vrm.humanoid;
        const state = liveState.current;
        const sc    = liveSpeech.current?.current;

        // ── Blink FSM ────────────────────────────────────────────────────────
        const bk = blinkState.current;
        bk.t += dt;
        if (bk.phase === 'idle' && bk.t >= bk.next) {
          bk.phase = 'closing'; bk.t = 0;
        } else if (bk.phase === 'closing') {
          const v = Math.min(bk.t / 0.06, 1.0);
          em?.setValue(VRMExpressionPresetName.Blink, v);
          if (v >= 1.0) { bk.phase = 'opening'; bk.t = 0; }
        } else if (bk.phase === 'opening') {
          const v = 1.0 - Math.min(bk.t / 0.09, 1.0);
          em?.setValue(VRMExpressionPresetName.Blink, v);
          if (v <= 0) {
            em?.setValue(VRMExpressionPresetName.Blink, 0);
            bk.phase = 'idle'; bk.t = 0;
            bk.next  = 2.5 + Math.random() * 4.5;
          }
        }

        // ── Mouth (speaking energy) ───────────────────────────────────────────
        const energy = (sc?.active && sc?.energy) ? sc.energy : 0;
        mouthSmooth.current += (energy - mouthSmooth.current) * (1 - Math.exp(-dt * 16));
        const mouthVal = Math.min(mouthSmooth.current * 0.85, 1.0);
        em?.setValue(VRMExpressionPresetName.Aa, mouthVal);

        // ── Emotional expressions per state ───────────────────────────────────
        const isReady   = state === 'READY_LONG' || state === 'READY_SHORT';
        const isWin     = state === 'TARGET_HIT';
        const isLoss    = state === 'STOP_HIT';
        em?.setValue(VRMExpressionPresetName.Happy,    isReady ? 0.40 : isWin ? 0.80 : 0);
        em?.setValue(VRMExpressionPresetName.Surprised, isWin   ? 0.35 : 0);
        em?.setValue(VRMExpressionPresetName.Angry,    isLoss  ? 0.30 : 0);

        // ── Gaze (head + eye rotation) ────────────────────────────────────────
        const g = liveGaze.current;
        const targetYaw   = g.dx * 0.016;
        const targetPitch = -g.dy * 0.011;
        g.yaw   += (targetYaw   - g.yaw)   * (1 - Math.exp(-dt * 4.5));
        g.pitch += (targetPitch - g.pitch) * (1 - Math.exp(-dt * 4.5));

        // Apply to head bone (gentle, natural-looking)
        try {
          const head = hum?.getNormalizedBoneNode?.('head');
          if (head) {
            const breathRx = Math.sin(elapsed * 0.72) * 0.006;
            head.rotation.x = g.pitch * 0.28 + breathRx;
            head.rotation.y = g.yaw   * 0.40;
          }
          // Breathing — subtle spine sway
          const spine = hum?.getNormalizedBoneNode?.('spine');
          if (spine) {
            spine.rotation.x = Math.sin(elapsed * 0.72) * 0.005;
          }
          // Eye bones if model has them
          ['leftEye', 'rightEye'].forEach(name => {
            const eye = hum?.getNormalizedBoneNode?.(name as never);
            if (eye) {
              eye.rotation.x = g.pitch * 0.55;
              eye.rotation.y = g.yaw   * 0.55;
            }
          });
        } catch (_) { /* */ }

        // ── Accent light: colour + pulse ──────────────────────────────────────
        if (accentRef.current) {
          accentRef.current.color.setHex(STATE_ACCENT[state]);
          const pulse = isReady
            ? 1.8 + Math.sin(elapsed * STATE_PULSE[state]) * 0.6
            : 1.5 + Math.sin(elapsed * STATE_PULSE[state]) * 0.25;
          accentRef.current.intensity = pulse;
        }

        vrm.update(dt);
        em?.update();
      }

      renderer.render(scene, camera);
    }

    tick();

    return () => {
      cancelAnimationFrame(rafRef.current);
      if (vrmRef.current) {
        try { VRMUtils.deepDispose(vrmRef.current.scene); } catch (_) { /* */ }
        vrmRef.current = null;
      }
      renderer.dispose();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <canvas
      ref={canvasRef}
      width={W}
      height={H}
      style={{
        display: 'block',
        background: 'transparent',
        filter: 'drop-shadow(0 0 28px rgba(0,148,255,0.35))',
      }}
    />
  );
}

VRMAvatar.displayName = 'VRMAvatar';
export default memo(VRMAvatar);
