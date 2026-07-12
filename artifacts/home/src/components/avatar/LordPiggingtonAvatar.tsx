/**
 * LordPiggingtonAvatar — Three.js + @pixiv/three-vrm  (v4 — idle pose fix)
 *
 * KEY FIXES vs v3:
 * ─────────────────
 * 1. boneSmoothRef uses null! + lazy-init guard so it is never re-set on
 *    React re-renders (debug timer was causing 200ms resets mid-lerp).
 * 2. Every bone target function specifies x, y AND z for every bone.
 *    If any axis is undefined the lerp skips it, leaving old gesture values
 *    frozen — that was the "arms stay raised after wave" root cause.
 * 3. idleTargets has ZERO shoulder/upper-arm/forearm/hand X rotation.
 *    Only the Z axis is set (to bring arms from T-pose horizontal to sides).
 *    Breathing lives only in spine/chest/hips — never in arms.
 * 4. talkTargets keeps arms at the same Z as idle; only small X gesture
 *    on the right arm, never raising it above the idle resting level.
 * 5. One-shots (wave/point/think) expire and lerp smoothly back; the idle
 *    loop runs continuously at all times as the base layer.
 *
 * LAYER ORDER (no conflicts):
 *   1. State-machine  → BODY_BONES (never head/neck)
 *   2. Gaze + nod     → head / neck  (additive)
 *   3. Blink          → blink expression
 *   4. Lip-sync       → mouth expressions
 *   5. Market-state   → happy / sad / surprised …
 *   6. vrm.update()
 */
import React, {
  useRef, useEffect, useState, useCallback, memo,
} from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import {
  VRMLoaderPlugin, VRMUtils, VRM, VRMExpressionPresetName,
} from '@pixiv/three-vrm';
import type { LordPiggingtonProps, AvatarState } from './avatarTypes';
import { STATE_ACCENT_HEX, STATE_PULSE_HZ } from './avatarTypes';

// ─── Canvas ───────────────────────────────────────────────────────────────────
const W = 342;
const H = 455;

// ─── Animation state ──────────────────────────────────────────────────────────
type AnimState = 'idle' | 'talking' | 'walking' | 'pointing' | 'thinking' | 'waving';
interface OneShotMeta { returnTo: AnimState; endT: number; }

// All three axes always present — prevents partial lerp leaving a bone frozen
type BoneRot3 = { x: number; y: number; z: number };
type BoneTargets = Record<string, BoneRot3>;
// Smoothed values for each bone (lerped every frame)
type BoneSm = Record<string, BoneRot3>;

// Body bones driven by the state machine (head/neck handled by gaze layer)
const BODY_BONES = [
  'hips', 'spine', 'chest', 'upperChest',
  'leftUpperArm',  'leftLowerArm',  'leftHand',
  'rightUpperArm', 'rightLowerArm', 'rightHand',
  'leftUpperLeg',  'leftLowerLeg',  'leftFoot',
  'rightUpperLeg', 'rightLowerLeg', 'rightFoot',
] as const;

// Full list — logged at load time
const ALL_LOG_BONES = [...BODY_BONES, 'neck', 'head'] as const;

// ─── Zero helper ──────────────────────────────────────────────────────────────
const Z3: BoneRot3 = { x: 0, y: 0, z: 0 };

// ─── ARM Z-ROTATION CONSTANTS ─────────────────────────────────────────────────
// In three-vrm normalised-humanoid space the T-pose has arms horizontal.
// Verified convention (three-vrm v1+):
//   leftUpperArm  z = +ARM_Z  → arm swings DOWN to side
//   rightUpperArm z = -ARM_Z  → arm swings DOWN to side
// (Opposite signs to what Unity/VRM0 raw bone space uses.)
const ARM_Z = 1.4;

// ─── Mouth expression candidates ──────────────────────────────────────────────
const MOUTH_CANDIDATES = ['aa','ih','ou','ee','oh','A','I','U','E','O'];
const VISEME_CANDIDATES: Record<string, string[]> = {
  open:    ['aa','A'],
  rounded: ['ou','oh','U'],
  narrow:  ['ee','ih','E','I'],
  press:   ['ih','I'],
  rest:    [],
};

// ─── Market-state expression presets ─────────────────────────────────────────
const STATE_EXPR_NAMES = [
  VRMExpressionPresetName.Happy, VRMExpressionPresetName.Sad,
  VRMExpressionPresetName.Angry, VRMExpressionPresetName.Surprised,
  VRMExpressionPresetName.Relaxed,
];
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

// ─── Bone target functions ────────────────────────────────────────────────────
//
// RULES:
//   • Every function specifies x, y, z for every bone in BODY_BONES.
//   • Idle: ZERO x/y on all arm bones — only z brings arms to sides.
//   • Breathing lives in spine/chest ONLY.

function idleTargets(breathX: number, swayZ: number): BoneTargets {
  return {
    hips:          { x: 0,        y: 0, z: swayZ * 0.18 },
    spine:         { x: breathX,  y: 0, z: swayZ * 0.35 },
    chest:         { x: breathX * 0.55, y: 0, z: swayZ * 0.28 },
    upperChest:    { x: breathX * 0.28, y: 0, z: swayZ * 0.20 },
    // Arms: ONLY Z to bring from T-pose horizontal → sides; x and y both 0
    leftUpperArm:  { x: 0, y: 0, z:  ARM_Z },
    leftLowerArm:  { x: 0.16, y: 0, z: 0 },   // slight natural elbow bend
    leftHand:      { x: 0, y: 0, z: 0 },
    rightUpperArm: { x: 0, y: 0, z: -ARM_Z },
    rightLowerArm: { x: 0.16, y: 0, z: 0 },
    rightHand:     { x: 0, y: 0, z: 0 },
    // Legs: standing, very slight knee bend
    leftUpperLeg:  { x: 0.02, y: 0, z: 0 },
    leftLowerLeg:  { x: 0.04, y: 0, z: 0 },
    leftFoot:      { x: -0.04, y: 0, z: 0 },
    rightUpperLeg: { x: 0.02, y: 0, z: 0 },
    rightLowerLeg: { x: 0.04, y: 0, z: 0 },
    rightFoot:     { x: -0.04, y: 0, z: 0 },
  };
}

function talkTargets(breathX: number, swayZ: number, phi: number): BoneTargets {
  // Small right-arm gesture; arms stay AT THE SAME Z as idle — never raised
  const gestR = Math.sin(phi) * 0.12;
  return {
    hips:          { x: 0,              y: 0, z: swayZ * 0.15 },
    spine:         { x: breathX * 1.2, y: 0, z: swayZ * 0.30 },
    chest:         { x: breathX * 0.70, y: 0, z: swayZ * 0.24 },
    upperChest:    { x: breathX * 0.36, y: 0, z: swayZ * 0.18 },
    leftUpperArm:  { x: 0,       y: 0, z:  ARM_Z },   // same Z as idle
    leftLowerArm:  { x: 0.18,    y: 0, z: 0 },
    leftHand:      { x: 0,       y: 0, z: 0 },
    rightUpperArm: { x: gestR,   y: 0, z: -ARM_Z },   // same Z as idle; small fwd swing
    rightLowerArm: { x: 0.24 + Math.abs(gestR) * 0.4, y: 0, z: 0 },
    rightHand:     { x: 0,       y: 0, z: 0 },
    leftUpperLeg:  { x: 0.02, y: 0, z: 0 },
    leftLowerLeg:  { x: 0.04, y: 0, z: 0 },
    leftFoot:      { x: -0.04, y: 0, z: 0 },
    rightUpperLeg: { x: 0.02, y: 0, z: 0 },
    rightLowerLeg: { x: 0.04, y: 0, z: 0 },
    rightFoot:     { x: -0.04, y: 0, z: 0 },
  };
}

function walkTargets(phi: number): BoneTargets {
  const armX  = Math.sin(phi)            * 0.34;
  const legX  = Math.sin(phi + Math.PI) * 0.26;
  const elbL  = 0.14 + Math.max(0,  Math.sin(phi + Math.PI * 0.5)) * 0.22;
  const elbR  = 0.14 + Math.max(0, -Math.sin(phi + Math.PI * 0.5)) * 0.22;
  const kneeL = 0.06 + Math.max(0,  Math.sin(phi + Math.PI * 1.5)) * 0.18;
  const kneeR = 0.06 + Math.max(0, -Math.sin(phi + Math.PI * 1.5)) * 0.18;
  return {
    hips:          { x: 0, y: 0, z: Math.sin(phi) * 0.030 },
    spine:         { x: 0, y: 0, z: Math.sin(phi + Math.PI) * 0.022 },
    chest:         { x: 0, y: 0, z: 0 },
    upperChest:    { x: 0, y: 0, z: 0 },
    leftUpperArm:  { x:  armX, y: 0, z:  ARM_Z },
    leftLowerArm:  { x: elbL,  y: 0, z: 0 },
    leftHand:      { x: 0, y: 0, z: 0 },
    rightUpperArm: { x: -armX, y: 0, z: -ARM_Z },
    rightLowerArm: { x: elbR,  y: 0, z: 0 },
    rightHand:     { x: 0, y: 0, z: 0 },
    leftUpperLeg:  { x:  legX, y: 0, z: 0 },
    leftLowerLeg:  { x: kneeL, y: 0, z: 0 },
    leftFoot:      { x: -0.04, y: 0, z: 0 },
    rightUpperLeg: { x: -legX, y: 0, z: 0 },
    rightLowerLeg: { x: kneeR, y: 0, z: 0 },
    rightFoot:     { x: -0.04, y: 0, z: 0 },
  };
}

function pointTargets(breathX: number): BoneTargets {
  const base = idleTargets(breathX, 0);
  return {
    ...base,
    // Right arm: raise to pointing height; left arm stays relaxed
    rightUpperArm: { x: -0.30, y: 0, z: -0.55 }, // z > -ARM_Z = arm raised
    rightLowerArm: { x:  0.22, y: 0, z: 0 },
    rightHand:     { x:  0,    y: 0, z: 0 },
    leftUpperArm:  { x:  0,    y: 0, z:  ARM_Z + 0.08 }, // slightly more relaxed
  };
}

function thinkTargets(breathX: number, elapsed: number): BoneTargets {
  const base = idleTargets(breathX, 0);
  const rock = Math.sin(elapsed * 0.55) * 0.014;
  return {
    ...base,
    hips:          { x: rock, y: 0, z: 0 },
    rightUpperArm: { x: -0.50, y: 0, z: -0.48 }, // arm raised toward chin
    rightLowerArm: { x:  1.20, y: 0, z: 0 },     // elbow bent upward
    rightHand:     { x: -0.12, y: 0, z: 0 },
    leftUpperArm:  { x:  0,    y: 0, z:  ARM_Z + 0.05 },
  };
}

function waveTargets(phi: number, breathX: number): BoneTargets {
  const base  = idleTargets(breathX, 0);
  const waveZ = Math.sin(phi * 3.5) * 0.42;
  return {
    ...base,
    rightUpperArm: { x: -0.44, y: 0, z: -0.78 }, // arm raised, forward
    rightLowerArm: { x:  0.82, y: 0, z:  0 },
    rightHand:     { x:  0,    y: 0, z: waveZ },  // waving
  };
}

// ─── Bone smooth value initialiser ───────────────────────────────────────────
// Called once; pre-sets arm Z so there is NO snap from T-pose on first frame.
function initBoneSm(): BoneSm {
  const sm: BoneSm = {};
  for (const b of ALL_LOG_BONES) sm[b] = { x: 0, y: 0, z: 0 };
  // Pre-set arm Z to idle position → no visible snap from T-pose
  sm.leftUpperArm  = { x: 0, y: 0, z:  ARM_Z };
  sm.rightUpperArm = { x: 0, y: 0, z: -ARM_Z };
  sm.leftLowerArm  = { x: 0.16, y: 0, z: 0 };
  sm.rightLowerArm = { x: 0.16, y: 0, z: 0 };
  return sm;
}

// ─── VRM expression helpers ───────────────────────────────────────────────────
function safeSet(vrm: VRM, name: string, value: number) {
  try {
    if (vrm.expressionManager?.getExpression(name))
      vrm.expressionManager.setValue(name, Math.max(0, Math.min(1, value)));
  } catch (_) {}
}
function safeGet(vrm: VRM, name: string): number {
  try { return vrm.expressionManager?.getValue(name) ?? 0; } catch (_) { return 0; }
}
function resetMouth(vrm: VRM, available: string[]) {
  available.forEach(n => safeSet(vrm, n, 0));
}
function setMouthShape(vrm: VRM, available: string[], active: string, amount: number) {
  available.forEach(n => {
    const tgt = n === active ? amount : 0;
    safeSet(vrm, n, THREE.MathUtils.lerp(safeGet(vrm, n), tgt, 0.35));
  });
}

// ─── Camera auto-frame ────────────────────────────────────────────────────────
function autoFrame(_vrm: VRM, gltfScene: THREE.Group, camera: THREE.PerspectiveCamera) {
  const box = new THREE.Box3().setFromObject(gltfScene);
  if (box.isEmpty()) return;
  const size   = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const pad    = size.y * 0.06;
  const top    = box.max.y + pad;
  const btm    = box.min.y - pad;
  const frameY = (top + btm) / 2;
  const fovRad = (camera.fov * Math.PI) / 180;
  const dist   = ((top - btm) / 2) / Math.tan(fovRad / 2) / 0.92;
  camera.position.set(center.x, frameY, dist);
  camera.lookAt(center.x, frameY, 0);
  camera.updateProjectionMatrix();
}

// expDecay shorthand (frame-rate independent lerp)
function ed(a: number, b: number, lambda: number, dt: number) {
  return a + (b - a) * (1 - Math.exp(-lambda * dt));
}

// ─── Debug data ───────────────────────────────────────────────────────────────
interface DebugData {
  expressionNames: string[];
  availableMouth:  string[];
  activeShape:     string;
  energy:          number;
  isSpeaking:      boolean;
  exprMgrFound:    boolean;
  animState:       AnimState;
  bonesFound:      string[];
  bonesMissing:    string[];
}

// ─── Component ────────────────────────────────────────────────────────────────
interface Props extends LordPiggingtonProps { debug?: boolean; }

function LordPiggingtonAvatar({
  avState, speaking, gazeEvent, speechCtrlRef, debug = false,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const vrmRef    = useRef<VRM | null>(null);
  const accentRef = useRef<THREE.PointLight | null>(null);
  const rafRef    = useRef<number>(0);
  const pausedRef = useRef(false);

  // Mouth + jaw bone
  const availableMouthRef = useRef<string[]>([]);
  const jawBoneRef        = useRef<THREE.Bone | null>(null);
  const activeShapeRef    = useRef('');
  const testExprRef       = useRef<{ name: string; until: number } | null>(null);

  // Animation state machine
  const animStateRef  = useRef<AnimState>('idle');
  const prevAnimRef   = useRef<AnimState>('idle');
  const oneShotRef    = useRef<OneShotMeta | null>(null);

  // Bone smoothed values — LAZY INIT: initialised once, never reset on re-render
  const boneSmoothRef = useRef<BoneSm>(null!);
  if (!boneSmoothRef.current) boneSmoothRef.current = initBoneSm();

  // Live props → refs (avoid stale closures in RAF)
  const liveState  = useRef(avState);
  const liveSpeech = useRef(speechCtrlRef);
  const liveGaze   = useRef({ lastId: -1, dx: 0, dy: 0 });

  // Gaze / blink / nod state
  const blinkRef  = useRef({ phase: 'idle' as 'idle'|'closing'|'opening', t: 0, next: 2.8 });
  const exprRef   = useRef<Record<string, number>>({});
  const lookRef   = useRef({ yaw: 0, pitch: 0 });
  const glanceRef = useRef({
    nextGlance: 6 + Math.random() * 8, t: 0,
    targetX: 0, targetY: 0, active: false, holdT: 0,
  });
  const nodRef    = useRef({ active: false, t: 0, dir: 1 });
  const prevMktRef = useRef<AvatarState>(avState);

  // Debug display
  const debugDataRef = useRef<DebugData>({
    expressionNames: [], availableMouth: [], activeShape: '',
    energy: 0, isSpeaking: false, exprMgrFound: false,
    animState: 'idle', bonesFound: [], bonesMissing: [],
  });
  const [debugDisplay, setDebugDisplay] = useState<DebugData | null>(null);

  // Keep live refs in sync
  useEffect(() => { liveState.current  = avState;       }, [avState]);
  useEffect(() => { liveSpeech.current = speechCtrlRef; }, [speechCtrlRef]);
  useEffect(() => {
    if (gazeEvent && gazeEvent.id !== liveGaze.current.lastId) {
      liveGaze.current.dx    = gazeEvent.dx;
      liveGaze.current.dy    = gazeEvent.dy;
      liveGaze.current.lastId = gazeEvent.id;
    }
  }, [gazeEvent]);

  // Debug UI poll (only fires when debug=true)
  useEffect(() => {
    if (!debug) return;
    const id = setInterval(() => setDebugDisplay({ ...debugDataRef.current }), 200);
    return () => clearInterval(id);
  }, [debug]);

  // Test expression (debug buttons)
  const testExpression = useCallback((name: string) => {
    testExprRef.current = { name, until: performance.now() + 900 };
  }, []);

  // Trigger one-shot animation from dev panel
  const triggerOneShot = useCallback((state: AnimState, duration: number) => {
    const cur = animStateRef.current;
    const rtn = (cur === 'waving' || cur === 'pointing' || cur === 'thinking')
      ? prevAnimRef.current : cur;
    prevAnimRef.current  = cur;
    animStateRef.current = state;
    oneShotRef.current   = { returnTo: rtn, endT: performance.now() / 1000 + duration };
  }, []);

  // Set animation state from dev panel
  const setAnimState = useCallback((s: AnimState) => {
    const oneShots: AnimState[] = ['pointing','thinking','waving'];
    const durations: Record<string, number> = { pointing: 2.5, thinking: 4.0, waving: 3.5 };
    if (oneShots.includes(s)) {
      triggerOneShot(s, durations[s]);
    } else {
      oneShotRef.current   = null;
      prevAnimRef.current  = animStateRef.current;
      animStateRef.current = s;
    }
  }, [triggerOneShot]);

  // ── Three.js setup ──────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new THREE.WebGLRenderer({
      canvas, antialias: true, alpha: true, premultipliedAlpha: false,
    });
    renderer.setSize(W, H);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    const scene = new THREE.Scene();
    scene.background = null;

    const camera = new THREE.PerspectiveCamera(30, W / H, 0.01, 30);
    camera.position.set(0, 0.85, 3.0);
    camera.lookAt(0, 0.85, 0);
    scene.add(camera);

    // Lights
    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.2);
    key.position.set(1.2, 2.5, 2.0); scene.add(key);
    const fill = new THREE.DirectionalLight(0x8ab4ff, 0.4);
    fill.position.set(-2, 1, 1); scene.add(fill);
    const accent = new THREE.PointLight(0x00aaff, 1.4, 8);
    accent.position.set(0, 1.0, 1.5); scene.add(accent);
    accentRef.current = accent;

    // Load VRM
    const loader = new GLTFLoader();
    loader.register(p => new VRMLoaderPlugin(p));
    loader.load('/LordPiggington.vrm', (gltf) => {
      const vrm: VRM = (gltf.userData as { vrm: VRM }).vrm;
      if (!vrm) { console.error('[LordPiggington] VRM not found'); return; }

      VRMUtils.rotateVRM0(vrm);
      scene.add(vrm.scene);
      vrmRef.current = vrm;
      autoFrame(vrm, vrm.scene, camera);

      // Bone inventory
      const hum = vrm.humanoid;
      const bonesFound: string[] = [];
      const bonesMissing: string[] = [];
      ALL_LOG_BONES.forEach(name => {
        const node = hum?.getNormalizedBoneNode?.(name as never);
        if (node) { bonesFound.push(name); console.log(`[bone ✓] ${name}`, node.name); }
        else       { bonesMissing.push(name); console.warn(`[bone ✗] ${name} — not found`); }
      });

      // Expression inventory
      const allNames = (vrm.expressionManager?.expressions ?? [])
        .map((e: { expressionName: string }) => e.expressionName);
      console.log('[LordPiggington] expressions:', allNames);
      const foundMouth = MOUTH_CANDIDATES.filter(n => vrm.expressionManager?.getExpression(n));
      console.log('[LordPiggington] mouth exprs:', foundMouth);
      availableMouthRef.current = foundMouth;

      // Find jaw bone via raw traversal — works on VRM0 and VRM1
      vrm.scene.traverse((obj) => {
        if (!jawBoneRef.current && obj instanceof THREE.Bone && /jaw/i.test(obj.name)) {
          jawBoneRef.current = obj;
          console.log('[LordPiggington] jaw bone found:', obj.name);
        }
      });

      debugDataRef.current = {
        ...debugDataRef.current,
        expressionNames: allNames, availableMouth: foundMouth,
        exprMgrFound: !!vrm.expressionManager, bonesFound, bonesMissing,
      };

      // Wave on first load (800ms grace for idle lerp to settle first)
      setTimeout(() => {
        prevAnimRef.current  = 'idle';
        animStateRef.current = 'waving';
        oneShotRef.current   = { returnTo: 'idle', endT: performance.now() / 1000 + 3.5 };
      }, 800);
    },
    undefined,
    (err) => console.error('[LordPiggington] load error:', err));

    // Page-visibility pause
    const onVis = () => { pausedRef.current = document.hidden; };
    document.addEventListener('visibilitychange', onVis);

    // ── Render loop ──────────────────────────────────────────────────────────
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

      const em      = vrm.expressionManager;
      const hum     = vrm.humanoid;
      const mktSt   = liveState.current;
      const sc      = liveSpeech.current?.current;
      const avMouth = availableMouthRef.current;
      const boneSm  = boneSmoothRef.current;

      // ── Resolve animation state ───────────────────────────────────────────
      const nowSec = now / 1000;

      // Expire one-shots → return to previous state
      const os = oneShotRef.current;
      if (os && nowSec >= os.endT) {
        animStateRef.current = os.returnTo;
        oneShotRef.current   = null;
      }

      // Auto-drive idle ↔ talking based on speech (only when no one-shot active
      // and not in a manually-held non-talking state like 'walking')
      if (!oneShotRef.current &&
          (animStateRef.current === 'idle' || animStateRef.current === 'talking')) {
        animStateRef.current = (sc?.active ?? false) ? 'talking' : 'idle';
      }

      const anim = animStateRef.current;

      // ── Compute bone targets for this frame ───────────────────────────────
      const breathX = Math.sin(elapsed * 0.82) * 0.0055;
      const swayZ   = Math.sin(elapsed * 0.28) * 0.0036;
      const phi     = elapsed * Math.PI * 2;

      let targets: BoneTargets;
      switch (anim) {
        case 'talking':  targets = talkTargets(breathX, swayZ, phi * 0.85); break;
        case 'walking':  targets = walkTargets(phi * 0.52);                 break;
        case 'pointing': targets = pointTargets(breathX);                   break;
        case 'thinking': targets = thinkTargets(breathX, elapsed);          break;
        case 'waving':   targets = waveTargets(phi * 0.52, breathX);        break;
        default:         targets = idleTargets(breathX, swayZ);             break;
      }

      // ── Lerp bone smooth values toward targets (λ=8 → ~0.3 s to 90 %) ───
      const lerpF = 1 - Math.exp(-8 * dt);
      for (const [name, tgt] of Object.entries(targets)) {
        const sm = boneSm[name] ?? (boneSm[name] = { x: 0, y: 0, z: 0 });
        sm.x = sm.x + (tgt.x - sm.x) * lerpF;
        sm.y = sm.y + (tgt.y - sm.y) * lerpF;
        sm.z = sm.z + (tgt.z - sm.z) * lerpF;
      }

      // ── Apply to body bones (head/neck handled by gaze layer below) ───────
      for (const name of BODY_BONES) {
        const sm   = boneSm[name];
        const bone = hum?.getNormalizedBoneNode?.(name as never);
        if (bone && sm) {
          bone.rotation.x = sm.x;
          bone.rotation.y = sm.y;
          bone.rotation.z = sm.z;
        }
      }

      // ── Market-state nod / shake on transition ────────────────────────────
      if (mktSt !== prevMktRef.current) {
        const pos = mktSt === 'READY_LONG'  || mktSt === 'TARGET_HIT';
        const neg = mktSt === 'STOP_HIT'    || mktSt === 'READY_SHORT';
        if (pos || neg) nodRef.current = { active: true, t: 0, dir: pos ? 1 : -1 };
        prevMktRef.current = mktSt;
      }

      // ── Blink FSM ─────────────────────────────────────────────────────────
      const bk  = blinkRef.current;
      const BLK = VRMExpressionPresetName.Blink;
      bk.t += dt;
      if (bk.phase === 'idle' && bk.t >= bk.next) {
        bk.phase = 'closing'; bk.t = 0;
      } else if (bk.phase === 'closing') {
        const v = Math.min(bk.t / 0.055, 1); safeSet(vrm, BLK, v);
        if (v >= 1) { bk.phase = 'opening'; bk.t = 0; }
      } else if (bk.phase === 'opening') {
        const v = 1 - Math.min(bk.t / 0.09, 1); safeSet(vrm, BLK, v);
        if (v <= 0) {
          safeSet(vrm, BLK, 0); bk.phase = 'idle'; bk.t = 0;
          bk.next = 3 + Math.random() * 5;
        }
      }

      // ── Test expression override (debug buttons) ──────────────────────────
      const test = testExprRef.current;
      if (test) {
        if (now < test.until) {
          resetMouth(vrm, avMouth);
          safeSet(vrm, test.name, 1.0);
          vrm.update(dt); try { em?.update(); } catch (_) {}
          renderer.render(scene, camera);
          return;
        } else {
          resetMouth(vrm, avMouth);
          testExprRef.current = null;
        }
      }

      // ── Lip-sync ──────────────────────────────────────────────────────────
      const isSpeaking = sc?.active ?? false;
      const rawEnergy  = isSpeaking ? Math.max(0, Math.min(1, sc?.energy ?? 0)) : 0;
      let activeShape  = '';
      if (rawEnergy > 0.02 && avMouth.length > 0) {
        const viseme = sc?.viseme ?? 'open';
        const cands  = VISEME_CANDIDATES[viseme] ?? VISEME_CANDIDATES.open;
        activeShape  = cands.find(c => avMouth.includes(c)) ?? avMouth[0] ?? '';
        if (activeShape) setMouthShape(vrm, avMouth, activeShape, rawEnergy * 0.90);
      } else {
        avMouth.forEach(n => {
          const cur = safeGet(vrm, n);
          if (cur > 0.001) safeSet(vrm, n, THREE.MathUtils.lerp(cur, 0, 0.25));
          else safeSet(vrm, n, 0);
        });
      }
      activeShapeRef.current = activeShape;

      // ── Jaw bone fallback: visible mouth even when model has no morph targets ─
      // Uses raw THREE.Bone found by /jaw/i traversal — works on VRM0 + VRM1.
      const jawBone = jawBoneRef.current;
      if (jawBone) {
        const bs   = boneSm as Record<string, { x: number; y: number; z: number }>;
        if (!bs['_jaw']) bs['_jaw'] = { x: 0, y: 0, z: 0 };
        const jawSm = bs['_jaw'];
        const jawTgt = rawEnergy * 0.30;   // 0.30 rad ≈ 17° open at peak energy
        jawSm.x += (jawTgt - jawSm.x) * (1 - Math.exp(-16 * dt));
        jawBone.rotation.x = jawSm.x;
      }

      // ── Market-state expressions ──────────────────────────────────────────
      const exprTargets = STATE_EXPR[mktSt] ?? {};
      STATE_EXPR_NAMES.forEach(n => {
        const tgt  = exprTargets[n] ?? 0;
        const cur  = exprRef.current[n] ?? 0;
        exprRef.current[n] = ed(cur, tgt, 3.5, dt);
        safeSet(vrm, n, exprRef.current[n]);
      });

      // ── Gaze + nod → head / neck (additive layer) ────────────────────────
      const g  = liveGaze.current;
      const gl = glanceRef.current;
      gl.t += dt;
      let tYaw   = g.dx * 0.018;
      let tPitch = -g.dy * 0.012;
      if (!gl.active && gl.t >= gl.nextGlance) {
        gl.active  = true; gl.t = 0; gl.holdT = 0;
        gl.targetX = (Math.random() < 0.5 ? -1 : 1) * (0.14 + Math.random() * 0.22);
        gl.targetY = (Math.random() - 0.5) * 0.09;
      }
      if (gl.active) {
        gl.holdT += dt; tYaw += gl.targetX; tPitch += gl.targetY;
        if (gl.holdT > 1.2 + Math.random() * 0.8) {
          gl.active = false; gl.t = 0; gl.nextGlance = 5 + Math.random() * 9;
        }
      }
      lookRef.current.yaw   = ed(lookRef.current.yaw,   tYaw,   5.5, dt);
      lookRef.current.pitch = ed(lookRef.current.pitch, tPitch, 5.5, dt);

      const nd = nodRef.current;
      let nodPitch = 0; let nodYaw = 0;
      if (nd.active) {
        nd.t += dt;
        if (nd.t < 0.7) {
          const w = Math.sin(nd.t / 0.7 * Math.PI * 2.5) * 0.06;
          if (nd.dir > 0) nodPitch = w; else nodYaw = w;
        } else nd.active = false;
      }

      const analyzeLean = mktSt === 'ANALYZING' ? 0.05 : 0;
      const thinkTilt   = anim === 'thinking'    ? Math.sin(elapsed * 0.55) * 0.035 : 0;

      try {
        const head = hum?.getNormalizedBoneNode?.('head' as never);
        if (head) {
          head.rotation.x = breathX * 0.55 + lookRef.current.pitch * 0.28 + nodPitch + analyzeLean;
          head.rotation.y = lookRef.current.yaw * 0.40 + swayZ * 0.45 + nodYaw + thinkTilt;
          head.rotation.z = swayZ * 0.30;
        }
        const neck = hum?.getNormalizedBoneNode?.('neck' as never);
        if (neck) {
          neck.rotation.x = lookRef.current.pitch * 0.14 + breathX * 0.28;
          neck.rotation.y = lookRef.current.yaw   * 0.20;
          neck.rotation.z = 0;
        }
      } catch (_) {}

      // ── Accent light ──────────────────────────────────────────────────────
      if (accentRef.current) {
        accentRef.current.color.setHex(STATE_ACCENT_HEX[mktSt]);
        const hz    = STATE_PULSE_HZ[mktSt];
        const isHot = mktSt === 'READY_LONG' || mktSt === 'READY_SHORT' || mktSt === 'TARGET_HIT';
        accentRef.current.intensity =
          (isHot ? 2.0 : 1.4) + Math.sin(elapsed * hz * Math.PI * 2) * (isHot ? 0.7 : 0.25);
      }

      // Update debug ref (does not trigger a React re-render)
      debugDataRef.current.animState  = anim;
      debugDataRef.current.activeShape = activeShape;
      debugDataRef.current.energy      = rawEnergy;
      debugDataRef.current.isSpeaking  = isSpeaking;

      vrm.update(dt);
      try { em?.update(); } catch (_) {}
      renderer.render(scene, camera);
    }

    tick();

    return () => {
      cancelAnimationFrame(rafRef.current);
      document.removeEventListener('visibilitychange', onVis);
      if (vrmRef.current) {
        try { VRMUtils.deepDispose(vrmRef.current.scene); } catch (_) {}
        vrmRef.current = null;
      }
      renderer.dispose();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <canvas
        ref={canvasRef}
        width={W}
        height={H}
        style={{ display: 'block', background: 'transparent' }}
      />

      {/* ── Dev / animation control panel (debug=true) ───────────────────── */}
      {debug && debugDisplay && (
        <div style={{
          position: 'absolute', top: 0, left: '100%', marginLeft: 8,
          background: 'rgba(0,0,0,0.88)', border: '1px solid rgba(0,148,255,0.35)',
          borderRadius: 6, padding: '8px 10px', width: 220,
          fontFamily: 'monospace', fontSize: 11, color: '#cde', lineHeight: 1.65,
          pointerEvents: 'auto', zIndex: 10, overflowY: 'auto', maxHeight: '90vh',
        }}>
          <div style={{ color: '#7df', fontWeight: 700, marginBottom: 4 }}>🐷 Lord Piggington</div>

          <div>Anim: <b style={{ color: '#fa0' }}>{debugDisplay.animState}</b></div>
          <div>Speech: <b style={{ color: debugDisplay.isSpeaking ? '#4f4' : '#888' }}>
            {debugDisplay.isSpeaking ? 'ACTIVE' : 'silent'}
          </b></div>
          <div>Energy: <b>{Math.round(debugDisplay.energy * 100)}</b>/100</div>
          <div>Shape: <b style={{ color: '#fa0' }}>{debugDisplay.activeShape || '—'}</b></div>
          <div>ExprMgr: <b style={{ color: debugDisplay.exprMgrFound ? '#4f4' : '#f44' }}>
            {debugDisplay.exprMgrFound ? 'YES' : 'NO'}
          </b></div>

          <div style={{ marginTop: 6, color: '#89a' }}>
            Bones ✓ ({debugDisplay.bonesFound.length}) / ✗ ({debugDisplay.bonesMissing.length})
          </div>
          <div style={{ color: '#6b8', fontSize: 10 }}>
            {debugDisplay.bonesFound.join(', ') || '—'}
          </div>
          {debugDisplay.bonesMissing.length > 0 && (
            <div style={{ color: '#f88', fontSize: 10 }}>
              Missing: {debugDisplay.bonesMissing.join(', ')}
            </div>
          )}

          <div style={{ marginTop: 8, color: '#89a', marginBottom: 4 }}>Animation</div>
          {(['idle','talking','walking','pointing','thinking','waving'] as AnimState[]).map(s => (
            <button key={s} onClick={() => setAnimState(s)} style={{
              display: 'inline-block', margin: '2px 2px', padding: '2px 7px',
              fontSize: 10, borderRadius: 3, cursor: 'pointer',
              background: debugDisplay.animState === s ? '#0af' : '#223',
              color:      debugDisplay.animState === s ? '#000' : '#adf',
              border: '1px solid #358',
            }}>{s}</button>
          ))}

          <div style={{ marginTop: 8, color: '#89a', marginBottom: 2 }}>Mouth test</div>
          {debugDisplay.availableMouth.length === 0
            ? <div style={{ color: '#f88' }}>none detected</div>
            : debugDisplay.availableMouth.map(n => (
              <button key={n} onClick={() => testExpression(n)} style={{
                display: 'inline-block', margin: '2px 2px', padding: '2px 6px',
                fontSize: 10, borderRadius: 3, cursor: 'pointer',
                background: debugDisplay.activeShape === n ? '#0af' : '#223',
                color:      debugDisplay.activeShape === n ? '#000' : '#adf',
                border: '1px solid #358',
              }}>{n}</button>
            ))
          }
          <button onClick={() => {
            if (vrmRef.current) resetMouth(vrmRef.current, availableMouthRef.current);
          }} style={{
            display: 'block', marginTop: 4, padding: '2px 8px', fontSize: 10,
            borderRadius: 3, cursor: 'pointer', background: '#422',
            color: '#faa', border: '1px solid #755',
          }}>reset mouth</button>

          <div style={{ marginTop: 8, color: '#89a', marginBottom: 2 }}>
            All exprs ({debugDisplay.expressionNames.length})
          </div>
          <div style={{ color: '#8ac', fontSize: 10, lineHeight: 1.4 }}>
            {debugDisplay.expressionNames.join(', ') || '—'}
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(LordPiggingtonAvatar);
