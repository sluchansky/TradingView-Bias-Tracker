/**
 * LordPiggingtonAvatar
 * Full 3-D VRM avatar rendered via Three.js + @pixiv/three-vrm.
 *
 * Fixes vs. the original VRMAvatar:
 *  • Truly transparent canvas (premultipliedAlpha:false + scene.background=null)
 *  • Auto-frames camera from model bounding-box after load
 *  • Rich idle: breathing, body-sway, head-tilt, random eye-glances
 *  • Blink FSM with occasional left/right individual blinks
 *  • Lip-sync: energy → mouth amplitude + viseme → vowel expression
 *  • Market-state expressions + head nod / shake reactions
 *  • VRM lookAt system with soft glance targets
 *  • Page-visibility pause (RAF suspended when tab hidden)
 *  • Single renderer, single RAF loop, full cleanup on unmount
 */
import React, { useRef, useEffect, memo } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils, VRM, VRMExpressionPresetName } from '@pixiv/three-vrm';
import type { LordPiggingtonProps, AvatarState } from './avatarTypes';
import { STATE_ACCENT_HEX, STATE_PULSE_HZ } from './avatarTypes';

// ─── Canvas size ──────────────────────────────────────────────────────────────
const W = 240;
const H = 320;

// ─── Viseme → VRM expression name ────────────────────────────────────────────
const VISEME_MAP: Record<string, string> = {
  aa: VRMExpressionPresetName.Aa,
  ih: VRMExpressionPresetName.Ih,
  ou: VRMExpressionPresetName.Ou,
  ee: VRMExpressionPresetName.Ee,
  oh: VRMExpressionPresetName.Oh,
};
const MOUTH_EXPRS = Object.values(VISEME_MAP);

// ─── Market-state → target VRM expressions ───────────────────────────────────
type ExprMap = Partial<Record<string, number>>;
const STATE_EXPR: Record<AvatarState, ExprMap> = {
  WAIT:        {},
  ANALYZING:   {},
  FORMING:     { [VRMExpressionPresetName.Happy]: 0.12 },
  READY_LONG:  { [VRMExpressionPresetName.Happy]: 0.50 },
  READY_SHORT: { [VRMExpressionPresetName.Surprised]: 0.25 },
  NO_EDGE:     {},
  ACTIVE:      { [VRMExpressionPresetName.Happy]: 0.30 },
  STOP_HIT:    { [VRMExpressionPresetName.Sad]: 0.35 },
  TARGET_HIT:  { [VRMExpressionPresetName.Happy]: 0.80, [VRMExpressionPresetName.Surprised]: 0.30 },
};

// All non-mouth expression names we drive (for cleanup each frame)
const STATE_EXPR_NAMES = [
  VRMExpressionPresetName.Happy,
  VRMExpressionPresetName.Sad,
  VRMExpressionPresetName.Angry,
  VRMExpressionPresetName.Surprised,
  VRMExpressionPresetName.Relaxed,
];

// ─── Helpers ──────────────────────────────────────────────────────────────────
function lerp(a: number, b: number, t: number) { return a + (b - a) * t; }
function expDecay(a: number, b: number, lambda: number, dt: number) {
  return lerp(a, b, 1 - Math.exp(-lambda * dt));
}
function safeSet(vrm: VRM, name: string, value: number) {
  try { vrm.expressionManager?.setValue(name, Math.max(0, Math.min(1, value))); } catch (_) {}
}

// ─── Auto-frame camera from loaded model ─────────────────────────────────────
function autoFrame(vrm: VRM, gltfScene: THREE.Group, camera: THREE.PerspectiveCamera) {
  const box = new THREE.Box3().setFromObject(gltfScene);
  if (box.isEmpty()) return;

  const size = box.getSize(new THREE.Vector3());
  const modelH = size.y;

  // Try to find head bone for accurate head-top position
  let headTop = box.max.y;
  try {
    const headBone = vrm.humanoid?.getNormalizedBoneNode?.('head');
    if (headBone) {
      const wp = new THREE.Vector3();
      headBone.getWorldPosition(wp);
      headTop = wp.y + modelH * 0.08; // add a little headroom above head pivot
    }
  } catch (_) {}

  // We want to show roughly the top 45% of the model (head + upper chest)
  const showBottom = headTop - modelH * 0.45;
  const frameCenterY = (headTop + showBottom) / 2;
  const frameH = headTop - showBottom;

  const fovRad = (camera.fov * Math.PI) / 180;
  const aspect = W / H;
  // distance so frameH fills ~88% of the vertical FOV
  const distance = (frameH / 2) / Math.tan(fovRad / 2) / 0.88;

  camera.position.set(0, frameCenterY, distance);
  camera.lookAt(0, frameCenterY, 0);
  camera.updateProjectionMatrix();
}

// ─── Component ────────────────────────────────────────────────────────────────
function LordPiggingtonAvatar({ avState, speaking, gazeEvent, speechCtrlRef }: LordPiggingtonProps) {
  const canvasRef   = useRef<HTMLCanvasElement>(null);
  const vrmRef      = useRef<VRM | null>(null);
  const accentRef   = useRef<THREE.PointLight | null>(null);
  const lookTgtRef  = useRef<THREE.Object3D | null>(null);
  const rafRef      = useRef<number>(0);
  const pausedRef   = useRef(false);

  // Live refs — updated each render, read by animation loop without re-mount
  const liveState  = useRef(avState);
  const liveSpeech = useRef(speechCtrlRef);
  const liveGaze   = useRef({ lastId: -1, dx: 0, dy: 0 });

  // Animation state refs
  const blinkRef   = useRef({ phase: 'idle' as 'idle'|'closing'|'opening', t: 0, next: 2.8 });
  const mouthRef   = useRef({ open: 0 });
  const exprRef    = useRef<Record<string, number>>({});       // current smoothed expr weights
  const lookRef    = useRef({ yaw: 0, pitch: 0 });            // current smoothed gaze
  const glanceRef  = useRef({                                  // occasional glance system
    nextGlance: 6 + Math.random() * 8,
    t: 0,
    targetX: 0, targetY: 0,
    active: false, holdT: 0,
  });
  const nodRef     = useRef({ active: false, t: 0, dir: 1 }); // head-nod reaction
  const prevState  = useRef<AvatarState>(avState);            // for detecting state transitions

  // Keep live refs in sync on every render
  useEffect(() => { liveState.current = avState; }, [avState]);
  useEffect(() => { liveSpeech.current = speechCtrlRef; }, [speechCtrlRef]);
  useEffect(() => {
    if (gazeEvent && gazeEvent.id !== liveGaze.current.lastId) {
      liveGaze.current.dx    = gazeEvent.dx;
      liveGaze.current.dy    = gazeEvent.dy;
      liveGaze.current.lastId = gazeEvent.id;
    }
  }, [gazeEvent]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // ── Renderer ──────────────────────────────────────────────────────────────
    const renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      premultipliedAlpha: false,   // prevents the orange/bright fringe on alpha composite
    });
    renderer.setSize(W, H);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);    // fully transparent clear
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    // ── Scene ─────────────────────────────────────────────────────────────────
    const scene = new THREE.Scene();
    scene.background = null;  // explicitly transparent — no default background

    // ── Camera ────────────────────────────────────────────────────────────────
    const camera = new THREE.PerspectiveCamera(22, W / H, 0.01, 30);
    // Default position — will be overridden by autoFrame() after VRM loads
    camera.position.set(0, 1.40, 1.20);
    camera.lookAt(0, 1.40, 0);

    // LookAt target: sits at camera by default so avatar looks at viewer
    const lookAtTarget = new THREE.Object3D();
    lookAtTarget.position.set(0, 0, 0);
    camera.add(lookAtTarget);
    scene.add(camera);
    lookTgtRef.current = lookAtTarget;

    // ── Lighting ──────────────────────────────────────────────────────────────
    // Cool ambient to match the dark cockpit
    const ambient = new THREE.AmbientLight(0x6070a0, 0.9);
    scene.add(ambient);

    // Key: neutral-warm, upper-left
    const key = new THREE.DirectionalLight(0xfff0e8, 1.6);
    key.position.set(0.8, 2.5, 1.8);
    scene.add(key);

    // Fill: blue from right
    const fill = new THREE.DirectionalLight(0x4060c0, 0.50);
    fill.position.set(-1.5, 1.0, 1.0);
    scene.add(fill);

    // Rim: electric blue backlight for avatar pop against dark background
    const rim = new THREE.DirectionalLight(0x1030b0, 0.85);
    rim.position.set(0, 2.0, -2.0);
    scene.add(rim);

    // Accent: state-colour point light, soft, close to face
    const accent = new THREE.PointLight(0x0094ff, 1.6, 2.8);
    accent.position.set(0.0, 1.42, 0.80);
    scene.add(accent);
    accentRef.current = accent;

    // ── Load VRM ─────────────────────────────────────────────────────────────
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));

    loader.load(
      '/LordPiggington.vrm',
      (gltf) => {
        const vrm: VRM = gltf.userData.vrm;
        try { VRMUtils.removeUnnecessaryVertices(gltf.scene); } catch (_) {}
        try { VRMUtils.rotateVRM0(vrm); } catch (_) {}

        // Wire up lookAt so avatar naturally faces camera
        if (vrm.lookAt) {
          vrm.lookAt.target = lookAtTarget;
        }

        scene.add(gltf.scene);
        vrmRef.current = vrm;

        // Auto-frame camera now that we know the model bounds
        autoFrame(vrm, gltf.scene, camera);
      },
      undefined,
      (err) => console.error('[LordPiggington] load error:', err),
    );

    // ── Page visibility — pause RAF when hidden ───────────────────────────────
    const onVisibility = () => { pausedRef.current = document.hidden; };
    document.addEventListener('visibilitychange', onVisibility);

    // ── Render loop ───────────────────────────────────────────────────────────
    let prevTime = performance.now();
    let elapsed  = 0;

    function tick() {
      rafRef.current = requestAnimationFrame(tick);
      if (pausedRef.current) return;

      const now = performance.now();
      const dt  = Math.min((now - prevTime) / 1000, 0.05);
      prevTime  = now;
      elapsed  += dt;

      const vrm = vrmRef.current;
      if (!vrm) { renderer.render(scene, camera); return; }

      const em  = vrm.expressionManager;
      const hum = vrm.humanoid;
      const state = liveState.current;
      const sc    = liveSpeech.current?.current;

      // ── State-transition reactions (nod / shake) ──────────────────────────
      if (state !== prevState.current) {
        const isPositive = state === 'READY_LONG' || state === 'TARGET_HIT';
        const isNegative = state === 'STOP_HIT'   || state === 'READY_SHORT';
        if (isPositive || isNegative) {
          nodRef.current = { active: true, t: 0, dir: isPositive ? 1 : -1 };
        }
        prevState.current = state;
      }

      // ── Blink FSM ────────────────────────────────────────────────────────
      const bk = blinkRef.current;
      bk.t += dt;
      const blinkExpr = VRMExpressionPresetName.Blink;
      if (bk.phase === 'idle' && bk.t >= bk.next) {
        bk.phase = 'closing'; bk.t = 0;
      } else if (bk.phase === 'closing') {
        const v = Math.min(bk.t / 0.055, 1.0);
        safeSet(vrm, blinkExpr, v);
        if (v >= 1.0) { bk.phase = 'opening'; bk.t = 0; }
      } else if (bk.phase === 'opening') {
        const v = 1.0 - Math.min(bk.t / 0.090, 1.0);
        safeSet(vrm, blinkExpr, v);
        if (v <= 0) {
          safeSet(vrm, blinkExpr, 0);
          bk.phase = 'idle'; bk.t = 0;
          bk.next  = 3.0 + Math.random() * 5.0;
        }
      }

      // ── Lip-sync (energy amplitude + viseme expression) ───────────────────
      const isSpeaking = sc?.active ?? false;
      const rawEnergy  = isSpeaking ? (sc?.energy ?? 0) : 0;
      mouthRef.current.open = expDecay(mouthRef.current.open, rawEnergy, 18, dt);
      const mouthAmt = mouthRef.current.open;

      // Zero all mouth exprs first, then set the active viseme
      MOUTH_EXPRS.forEach(e => safeSet(vrm, e, 0));
      if (mouthAmt > 0.02) {
        const viseme = sc?.viseme ?? 'aa';
        const exprName = VISEME_MAP[viseme] ?? VRMExpressionPresetName.Aa;
        safeSet(vrm, exprName, Math.min(mouthAmt * 0.9, 1.0));
      }

      // ── Market-state expressions (smooth toward target) ───────────────────
      const targets = STATE_EXPR[state] ?? {};
      STATE_EXPR_NAMES.forEach(name => {
        const tgt = targets[name] ?? 0;
        const cur = exprRef.current[name] ?? 0;
        const next = expDecay(cur, tgt, 3.5, dt);
        exprRef.current[name] = next;
        safeSet(vrm, name, next);
      });

      // ── Gaze: smooth toward mouse/gazeEvent delta + occasional glances ────
      const g = liveGaze.current;
      const gl = glanceRef.current;
      gl.t += dt;

      let targetYaw   = g.dx * 0.018;
      let targetPitch = -g.dy * 0.012;

      // Occasional automatic glance
      if (!gl.active && gl.t >= gl.nextGlance) {
        gl.active   = true;
        gl.t        = 0;
        gl.holdT    = 0;
        gl.targetX  = (Math.random() < 0.5 ? -1 : 1) * (0.15 + Math.random() * 0.25);
        gl.targetY  = (Math.random() - 0.5) * 0.10;
      }
      if (gl.active) {
        gl.holdT += dt;
        targetYaw   += gl.targetX;
        targetPitch += gl.targetY;
        if (gl.holdT > 1.2 + Math.random() * 0.8) {
          gl.active     = false;
          gl.t          = 0;
          gl.nextGlance = 5 + Math.random() * 9;
        }
      }

      lookRef.current.yaw   = expDecay(lookRef.current.yaw,   targetYaw,   5.5, dt);
      lookRef.current.pitch = expDecay(lookRef.current.pitch, targetPitch,  5.5, dt);

      // ── Bone animation ────────────────────────────────────────────────────
      try {
        const breathAmp = 0.0055;
        const breathRot = Math.sin(elapsed * 0.80) * breathAmp;
        const swayRot   = Math.sin(elapsed * 0.28) * 0.0035;

        // Nod/shake reaction
        const nd = nodRef.current;
        let nodPitch = 0, nodYaw = 0;
        if (nd.active) {
          nd.t += dt;
          const progress = nd.t / 0.7;
          if (progress < 1) {
            const wave = Math.sin(progress * Math.PI * 2.5) * 0.06;
            if (nd.dir > 0) nodPitch = wave;   // nod = pitch
            else            nodYaw   = wave;   // shake = yaw
          } else {
            nd.active = false;
          }
        }

        // State-specific head lean
        const analyzeLean = state === 'ANALYZING' ? 0.055 : 0;
        const activeLean  = state === 'ACTIVE'     ? 0.025 : 0;

        const head = hum?.getNormalizedBoneNode?.('head' as never);
        if (head) {
          head.rotation.x = breathRot * 0.6 + lookRef.current.pitch * 0.30 + nodPitch + analyzeLean;
          head.rotation.y = lookRef.current.yaw * 0.42 + swayRot * 0.5 + nodYaw;
          head.rotation.z = swayRot * 0.35;
        }

        const neck = hum?.getNormalizedBoneNode?.('neck' as never);
        if (neck) {
          neck.rotation.x = lookRef.current.pitch * 0.15 + breathRot * 0.3 + activeLean;
          neck.rotation.y = lookRef.current.yaw * 0.22;
        }

        const spine = hum?.getNormalizedBoneNode?.('spine' as never);
        if (spine) {
          spine.rotation.x = breathRot * 0.5;
          spine.rotation.z = swayRot * 0.4;
        }

        const upperChest = hum?.getNormalizedBoneNode?.('upperChest' as never);
        if (upperChest) {
          upperChest.rotation.x = breathRot * 0.3;
          upperChest.rotation.z = swayRot * 0.25;
        }
      } catch (_) {}

      // ── Accent light: state colour + pulse ───────────────────────────────
      if (accentRef.current) {
        accentRef.current.color.setHex(STATE_ACCENT_HEX[state]);
        const hz     = STATE_PULSE_HZ[state];
        const isHot  = state === 'READY_LONG' || state === 'READY_SHORT' || state === 'TARGET_HIT';
        const base   = isHot ? 2.0 : 1.4;
        const swing  = isHot ? 0.7 : 0.25;
        accentRef.current.intensity = base + Math.sin(elapsed * hz * Math.PI * 2) * swing;
      }

      vrm.update(dt);
      try { em?.update(); } catch (_) {}

      renderer.render(scene, camera);
    }

    tick();

    return () => {
      cancelAnimationFrame(rafRef.current);
      document.removeEventListener('visibilitychange', onVisibility);
      if (vrmRef.current) {
        try { VRMUtils.deepDispose(vrmRef.current.scene); } catch (_) {}
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
        filter: 'drop-shadow(0 4px 32px rgba(0,148,255,0.28))',
      }}
    />
  );
}

LordPiggingtonAvatar.displayName = 'LordPiggingtonAvatar';
export default memo(LordPiggingtonAvatar);
