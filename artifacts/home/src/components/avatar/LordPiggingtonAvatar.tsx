/**
 * LordPiggingtonAvatar — Three.js + @pixiv/three-vrm  (v3 — full animation)
 *
 * T-POSE FIX:
 *   Upper arms were stuck horizontal (T-pose) because rotation.z was never set.
 *   In VRM normalized-humanoid space the arms point outward along ±X.
 *   rotation.z ≈ -1.28 (left) / +1.28 (right) brings them ~73° down to sides.
 *
 * ANIMATION STATE MACHINE:
 *   idle | talking | walking | pointing | thinking | waving
 *   • speaking prop auto-drives  idle ↔ talking
 *   • one-shots (point/think/wave) return to previous state after duration
 *   • wave plays once on first load
 *   • smooth expDecay lerp (λ=8, ~0.3 s to 90 %) between every state
 *
 * LAYER ORDER (never conflicts):
 *   1. State-machine  → all body bones except head / neck
 *   2. Gaze + nod     → head / neck (additive offsets)
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

// One-shot: play state for `duration` seconds then return to `returnTo`
interface OneShotMeta { returnTo: AnimState; endT: number; }

// Per-bone smooth target values (lerped every frame)
type BoneRot    = { x?: number; y?: number; z?: number };
type BoneTargets = Record<string, BoneRot>;
type BoneSm     = Record<string, { x: number; y: number; z: number }>;

// ─── Humanoid bones we animate ────────────────────────────────────────────────
const BODY_BONES = [
  'hips', 'spine', 'chest', 'upperChest',
  'leftUpperArm', 'leftLowerArm', 'leftHand',
  'rightUpperArm', 'rightLowerArm', 'rightHand',
  'leftUpperLeg', 'leftLowerLeg', 'leftFoot',
  'rightUpperLeg', 'rightLowerLeg', 'rightFoot',
] as const;

// Head & neck are kept out of the state-machine — handled by gaze layer
const ALL_LOG_BONES = [...BODY_BONES, 'neck', 'head'] as const;

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

// ─── Bone target builders per animation state ────────────────────────────────
//
// ARM Z-ROTATION KEY (from T-pose → natural sides):
//   leftUpperArm  z ≈ -1.28  (≈ 73° down from horizontal)
//   rightUpperArm z ≈ +1.28  (mirror)
//
// These values assume VRM normalized-humanoid space where arms start horizontal.

function idleTargets(breathX: number, swayZ: number): BoneTargets {
  return {
    hips:          { x: 0,               y: 0, z: swayZ * 0.20 },
    spine:         { x: breathX,               z: swayZ * 0.40 },
    chest:         { x: breathX * 0.60,         z: swayZ * 0.30 },
    upperChest:    { x: breathX * 0.30,         z: swayZ * 0.25 },
    leftUpperArm:  { x: 0,               y: 0, z: -1.28         },
    leftLowerArm:  { x: 0.20,                   z:  0.05        },
    leftHand:      {                             z:  0.10        },
    rightUpperArm: { x: 0,               y: 0, z:  1.28         },
    rightLowerArm: { x: 0.20,                   z: -0.05        },
    rightHand:     {                             z: -0.10        },
    leftUpperLeg:  { x: 0.02 },
    leftLowerLeg:  { x: 0.05 },
    leftFoot:      { x: -0.04 },
    rightUpperLeg: { x: 0.02 },
    rightLowerLeg: { x: 0.05 },
    rightFoot:     { x: -0.04 },
  };
}

function talkTargets(breathX: number, swayZ: number, phi: number): BoneTargets {
  const gestR = Math.sin(phi * 0.85)              * 0.14;
  const gestL = Math.sin(phi * 0.85 + Math.PI) * 0.08;
  return {
    hips:          { x: 0,  y: 0, z: swayZ * 0.18 },
    spine:         { x: breathX * 1.30,   z: swayZ * 0.35 },
    chest:         { x: breathX * 0.80,   z: swayZ * 0.28 },
    upperChest:    { x: breathX * 0.45,   z: swayZ * 0.22 },
    leftUpperArm:  { x: gestL, y: 0, z: -1.08 },
    leftLowerArm:  { x: 0.32,            z:  0.08 },
    leftHand:      {                      z:  0.08 },
    rightUpperArm: { x: gestR, y: 0, z:  1.08 },
    rightLowerArm: { x: 0.38,            z: -0.08 },
    rightHand:     {                      z: -0.08 },
    leftUpperLeg:  { x: 0.02 },
    leftLowerLeg:  { x: 0.05 },
    leftFoot:      { x: -0.04 },
    rightUpperLeg: { x: 0.02 },
    rightLowerLeg: { x: 0.05 },
    rightFoot:     { x: -0.04 },
  };
}

function walkTargets(phi: number): BoneTargets {
  const armX  = Math.sin(phi)             * 0.36;
  const legX  = Math.sin(phi + Math.PI) * 0.28;
  const elbL  = 0.14 + Math.max(0,  Math.sin(phi + Math.PI * 0.5)) * 0.24;
  const elbR  = 0.14 + Math.max(0, -Math.sin(phi + Math.PI * 0.5)) * 0.24;
  const kneeL = 0.06 + Math.max(0,  Math.sin(phi + Math.PI * 1.5)) * 0.20;
  const kneeR = 0.06 + Math.max(0, -Math.sin(phi + Math.PI * 1.5)) * 0.20;
  return {
    hips:          { z: Math.sin(phi) * 0.036, y: 0, x: 0 },
    spine:         { x: 0, z: Math.sin(phi + Math.PI) * 0.025 },
    chest:         { x: 0, z: 0 },
    upperChest:    { x: 0, z: 0 },
    leftUpperArm:  { x:  armX, y: 0, z: -1.22 },
    leftLowerArm:  { x: elbL },
    leftHand:      { z:  0.08 },
    rightUpperArm: { x: -armX, y: 0, z:  1.22 },
    rightLowerArm: { x: elbR },
    rightHand:     { z: -0.08 },
    leftUpperLeg:  { x:  legX },
    leftLowerLeg:  { x: kneeL },
    leftFoot:      { x: -0.04 },
    rightUpperLeg: { x: -legX },
    rightLowerLeg: { x: kneeR },
    rightFoot:     { x: -0.04 },
  };
}

function pointTargets(breathX: number): BoneTargets {
  const base = idleTargets(breathX, 0);
  return {
    ...base,
    rightUpperArm: { x: -0.35, y: 0, z:  0.58 },
    rightLowerArm: { x:  0.28,       z: -0.05 },
    rightHand:     {                  z: -0.05 },
    leftUpperArm:  { x:  0.05, y: 0, z: -1.35 },
  };
}

function thinkTargets(breathX: number, elapsed: number): BoneTargets {
  const base = idleTargets(breathX, 0);
  return {
    ...base,
    rightUpperArm: { x: -0.55, y: 0, z:  0.52 },
    rightLowerArm: { x:  1.25,       z: -0.10 },
    rightHand:     { x: -0.15,       z: -0.05 },
    leftUpperArm:  { x:  0,    y: 0, z: -1.35 },
    // subtle rocking: use elapsed so it's deterministic even without phi
    hips:          { x: Math.sin(elapsed * 0.6) * 0.018, y: 0, z: 0 },
  };
}

function waveTargets(phi: number, breathX: number): BoneTargets {
  const waveZ = Math.sin(phi * 3.5) * 0.44;
  const base  = idleTargets(breathX, 0);
  return {
    ...base,
    rightUpperArm: { x: -0.48, y: 0, z:  0.80 },
    rightLowerArm: { x:  0.85 },
    rightHand:     {             z: waveZ },
  };
}

// ─── VRM expression helpers ───────────────────────────────────────────────────
function safeSet(vrm: VRM, name: string, value: number) {
  try {
    if (vrm.expressionManager?.getExpression(name)) {
      vrm.expressionManager.setValue(name, Math.max(0, Math.min(1, value)));
    }
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
    const target  = n === active ? amount : 0;
    const current = safeGet(vrm, n);
    safeSet(vrm, n, THREE.MathUtils.lerp(current, target, 0.35));
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
  const frameH = top - btm;
  const frameY = (top + btm) / 2;
  const fovRad = (camera.fov * Math.PI) / 180;
  const dist   = (frameH / 2) / Math.tan(fovRad / 2) / 0.92;
  camera.position.set(center.x, frameY, dist);
  camera.lookAt(center.x, frameY, 0);
  camera.updateProjectionMatrix();
}

// expDecay shorthand
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

  // ── Mouth ──
  const availableMouthRef = useRef<string[]>([]);
  const activeShapeRef    = useRef<string>('');
  const testExprRef       = useRef<{ name: string; until: number } | null>(null);

  // ── Animation state machine ──
  const animStateRef  = useRef<AnimState>('idle');
  const prevAnimRef   = useRef<AnimState>('idle');
  const oneShotRef    = useRef<OneShotMeta | null>(null);

  // ── Bone smoothed values (lerped every frame) ──
  // Pre-initialise arms to idle Z so there's no snap from T-pose on load
  const boneSmoothRef = useRef<BoneSm>(
    Object.fromEntries(
      [...BODY_BONES, 'neck', 'head'].map(b => [b, { x: 0, y: 0, z: 0 }])
    )
  ) as React.MutableRefObject<BoneSm>;
  // Override arm Z at startup so they start at sides immediately
  boneSmoothRef.current.leftUpperArm  = { x: 0, y: 0, z: -1.28 };
  boneSmoothRef.current.rightUpperArm = { x: 0, y: 0, z:  1.28 };
  boneSmoothRef.current.leftLowerArm  = { x: 0.20, y: 0, z: 0 };
  boneSmoothRef.current.rightLowerArm = { x: 0.20, y: 0, z: 0 };

  // ── Live refs ──
  const liveState    = useRef(avState);
  const liveSpeech   = useRef(speechCtrlRef);
  const liveGaze     = useRef({ lastId: -1, dx: 0, dy: 0 });

  // ── Gaze / blink / nod ──
  const blinkRef   = useRef({ phase: 'idle' as 'idle'|'closing'|'opening', t: 0, next: 2.8 });
  const exprRef    = useRef<Record<string, number>>({});
  const lookRef    = useRef({ yaw: 0, pitch: 0 });
  const glanceRef  = useRef({ nextGlance: 6 + Math.random() * 8, t: 0, targetX: 0, targetY: 0, active: false, holdT: 0 });
  const nodRef     = useRef({ active: false, t: 0, dir: 1 });
  const prevMktRef = useRef<AvatarState>(avState);

  // ── Debug display state ──
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

  // Debug UI poll
  useEffect(() => {
    if (!debug) return;
    const id = setInterval(() => setDebugDisplay({ ...debugDataRef.current }), 200);
    return () => clearInterval(id);
  }, [debug]);

  // Test expression via debug button
  const testExpression = useCallback((name: string) => {
    testExprRef.current = { name, until: performance.now() + 900 };
  }, []);

  // Trigger one-shot animation (from dev panel or external)
  const triggerOneShot = useCallback((state: AnimState, duration: number) => {
    oneShotRef.current = {
      returnTo: animStateRef.current === 'waving' ||
                animStateRef.current === 'pointing' ||
                animStateRef.current === 'thinking'
        ? prevAnimRef.current
        : animStateRef.current,
      endT: performance.now() / 1000 + duration,
    };
    prevAnimRef.current = animStateRef.current;
    animStateRef.current = state;
  }, []);

  // Manual state set (dev panel)
  const setAnimState = useCallback((s: AnimState) => {
    if (s === 'pointing' || s === 'thinking' || s === 'waving') {
      const dur = s === 'waving' ? 3.5 : s === 'pointing' ? 2.5 : 4.0;
      triggerOneShot(s, dur);
    } else {
      oneShotRef.current = null;
      prevAnimRef.current = animStateRef.current;
      animStateRef.current = s;
    }
  }, [triggerOneShot]);

  // ── Three.js setup ──────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      premultipliedAlpha: false,
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

    const lookAtTarget = new THREE.Object3D();
    camera.add(lookAtTarget);
    scene.add(camera);

    // Lights
    const ambient = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambient);
    const key = new THREE.DirectionalLight(0xffffff, 1.2);
    key.position.set(1.2, 2.5, 2.0);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x8ab4ff, 0.4);
    fill.position.set(-2, 1, 1);
    scene.add(fill);
    const accent = new THREE.PointLight(0x00aaff, 1.4, 8);
    accent.position.set(0, 1.0, 1.5);
    scene.add(accent);
    accentRef.current = accent;

    // Load VRM
    const loader = new GLTFLoader();
    loader.register(p => new VRMLoaderPlugin(p));
    loader.load('/LordPiggington.vrm', (gltf) => {
      const vrm: VRM = (gltf.userData as { vrm: VRM }).vrm;
      if (!vrm) { console.error('[LordPiggington] VRM not found in GLTF userData'); return; }

      VRMUtils.rotateVRM0(vrm);
      scene.add(vrm.scene);
      vrmRef.current = vrm;

      autoFrame(vrm, vrm.scene, camera);

      // ── Bone inventory ──────────────────────────────────────────────────
      const hum = vrm.humanoid;
      const bonesFound: string[]   = [];
      const bonesMissing: string[] = [];
      ALL_LOG_BONES.forEach(name => {
        const node = hum?.getNormalizedBoneNode?.(name as never);
        if (node) {
          bonesFound.push(name);
          console.log(`[LordPiggington] bone FOUND:   ${name}`, node.name);
        } else {
          bonesMissing.push(name);
          console.warn(`[LordPiggington] bone MISSING: ${name}`);
        }
      });

      // ── Expression inventory ─────────────────────────────────────────────
      const allNames = (vrm.expressionManager?.expressions ?? [])
        .map((e: { expressionName: string }) => e.expressionName);
      console.log('[LordPiggington] all expressions:', allNames);

      const foundMouth = MOUTH_CANDIDATES.filter(
        n => vrm.expressionManager?.getExpression(n)
      );
      console.log('[LordPiggington] mouth expressions found:', foundMouth);
      availableMouthRef.current = foundMouth;

      debugDataRef.current = {
        ...debugDataRef.current,
        expressionNames: allNames,
        availableMouth:  foundMouth,
        exprMgrFound:    !!vrm.expressionManager,
        bonesFound,
        bonesMissing,
      };

      // ── Wave on first load ───────────────────────────────────────────────
      // Slightly delayed so the lerp has time to reach idle first
      setTimeout(() => {
        oneShotRef.current = { returnTo: 'idle', endT: (performance.now() / 1000) + 3.5 };
        prevAnimRef.current   = 'idle';
        animStateRef.current  = 'waving';
      }, 800);

    }, undefined, (err) => console.error('[LordPiggington] load error:', err));

    // Page-visibility pause
    const onVis = () => { pausedRef.current = document.hidden; };
    document.addEventListener('visibilitychange', onVis);

    // ── Render loop ──────────────────────────────────────────────────────────
    let prevTime = performance.now();
    let elapsed  = 0;
    const WALK_HZ = 0.52;
    const TALK_HZ = 0.85;

    function tick() {
      rafRef.current = requestAnimationFrame(tick);
      if (pausedRef.current) return;

      const now = performance.now();
      const dt  = Math.min((now - prevTime) / 1000, 0.05);
      prevTime  = now;
      elapsed  += dt;

      const vrm = vrmRef.current;
      if (!vrm) { renderer.render(scene, camera); return; }

      const em     = vrm.expressionManager;
      const hum    = vrm.humanoid;
      const mktSt  = liveState.current;
      const sc     = liveSpeech.current?.current;
      const avMouth = availableMouthRef.current;
      const boneSm  = boneSmoothRef.current;

      // ── Resolve animation state ───────────────────────────────────────────
      const nowSec = now / 1000;

      // Expire one-shots
      const os = oneShotRef.current;
      if (os && nowSec >= os.endT) {
        animStateRef.current = os.returnTo;
        oneShotRef.current   = null;
      }

      // Auto-drive idle ↔ talking from speech (only when no one-shot active
      // and not in a manually set non-talking state)
      if (!oneShotRef.current && (animStateRef.current === 'idle' || animStateRef.current === 'talking')) {
        const shouldTalk = sc?.active ?? false;
        animStateRef.current = shouldTalk ? 'talking' : 'idle';
      }

      const anim = animStateRef.current;
      debugDataRef.current.animState = anim;

      // ── Compute body bone targets ─────────────────────────────────────────
      const breathX = Math.sin(elapsed * 0.80) * 0.0058;
      const swayZ   = Math.sin(elapsed * 0.28) * 0.0038;
      const phi     = elapsed * Math.PI * 2;

      let targets: BoneTargets;
      switch (anim) {
        case 'talking':  targets = talkTargets(breathX, swayZ, phi * TALK_HZ); break;
        case 'walking':  targets = walkTargets(phi * WALK_HZ);                 break;
        case 'pointing': targets = pointTargets(breathX);                      break;
        case 'thinking': targets = thinkTargets(breathX, elapsed);             break;
        case 'waving':   targets = waveTargets(phi * WALK_HZ, breathX);        break;
        default:         targets = idleTargets(breathX, swayZ);                break;
      }

      // ── Lerp bone smooth values toward targets ────────────────────────────
      const lerpF = 1 - Math.exp(-8 * dt); // λ=8 ≈ 0.3 s to 90 %
      for (const [name, tgt] of Object.entries(targets)) {
        const sm = boneSm[name] ?? (boneSm[name] = { x: 0, y: 0, z: 0 });
        if (tgt.x !== undefined) sm.x = sm.x + (tgt.x - sm.x) * lerpF;
        if (tgt.y !== undefined) sm.y = sm.y + (tgt.y - sm.y) * lerpF;
        if (tgt.z !== undefined) sm.z = sm.z + (tgt.z - sm.z) * lerpF;
      }

      // ── Apply body bones (excludes head / neck — handled below) ──────────
      for (const name of BODY_BONES) {
        const sm   = boneSm[name];
        const bone = hum?.getNormalizedBoneNode?.(name as never);
        if (bone && sm) {
          bone.rotation.x = sm.x;
          bone.rotation.y = sm.y;
          bone.rotation.z = sm.z;
        }
      }

      // ── Market-state transition nod / shake ───────────────────────────────
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
        if (v <= 0) { safeSet(vrm, BLK, 0); bk.phase = 'idle'; bk.t = 0; bk.next = 3 + Math.random() * 5; }
      }

      // ── Test expression override ──────────────────────────────────────────
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
      let activeShape = '';
      if (rawEnergy > 0.02 && avMouth.length > 0) {
        const viseme    = sc?.viseme ?? 'open';
        const cands     = VISEME_CANDIDATES[viseme] ?? VISEME_CANDIDATES.open;
        activeShape     = cands.find(c => avMouth.includes(c)) ?? avMouth[0] ?? '';
        if (activeShape) setMouthShape(vrm, avMouth, activeShape, rawEnergy * 0.90);
      } else {
        avMouth.forEach(n => {
          const cur = safeGet(vrm, n);
          if (cur > 0.001) safeSet(vrm, n, THREE.MathUtils.lerp(cur, 0, 0.25));
          else safeSet(vrm, n, 0);
        });
      }
      activeShapeRef.current = activeShape;

      // ── Market-state expressions ──────────────────────────────────────────
      const exprTargets = STATE_EXPR[mktSt] ?? {};
      STATE_EXPR_NAMES.forEach(n => {
        const tgt  = exprTargets[n] ?? 0;
        const cur  = exprRef.current[n] ?? 0;
        const next = ed(cur, tgt, 3.5, dt);
        exprRef.current[n] = next;
        safeSet(vrm, n, next);
      });

      // ── Gaze + nod → head / neck ──────────────────────────────────────────
      const g  = liveGaze.current;
      const gl = glanceRef.current;
      gl.t += dt;
      let tYaw   = g.dx * 0.018;
      let tPitch = -g.dy * 0.012;
      if (!gl.active && gl.t >= gl.nextGlance) {
        gl.active  = true; gl.t = 0; gl.holdT = 0;
        gl.targetX = (Math.random() < 0.5 ? -1 : 1) * (0.15 + Math.random() * 0.25);
        gl.targetY = (Math.random() - 0.5) * 0.10;
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

      // State-specific head lean (additive, display only)
      const analyzeLean = mktSt === 'ANALYZING' ? 0.055 : 0;
      const thinkTilt   = anim  === 'thinking'   ? Math.sin(elapsed * 0.6) * 0.04 : 0;

      try {
        const head = hum?.getNormalizedBoneNode?.('head' as never);
        if (head) {
          head.rotation.x = breathX * 0.6 + lookRef.current.pitch * 0.30 + nodPitch + analyzeLean;
          head.rotation.y = lookRef.current.yaw * 0.42 + swayZ * 0.5  + nodYaw + thinkTilt;
          head.rotation.z = swayZ * 0.35;
        }
        const neck = hum?.getNormalizedBoneNode?.('neck' as never);
        if (neck) {
          neck.rotation.x = lookRef.current.pitch * 0.15 + breathX * 0.3;
          neck.rotation.y = lookRef.current.yaw   * 0.22;
        }
      } catch (_) {}

      // ── Accent light pulse ────────────────────────────────────────────────
      if (accentRef.current) {
        accentRef.current.color.setHex(STATE_ACCENT_HEX[mktSt]);
        const hz    = STATE_PULSE_HZ[mktSt];
        const isHot = mktSt === 'READY_LONG' || mktSt === 'READY_SHORT' || mktSt === 'TARGET_HIT';
        accentRef.current.intensity =
          (isHot ? 2.0 : 1.4) + Math.sin(elapsed * hz * Math.PI * 2) * (isHot ? 0.7 : 0.25);
      }

      // Update debug ref
      debugDataRef.current = {
        ...debugDataRef.current,
        activeShape,
        energy: rawEnergy,
        isSpeaking,
        animState: anim,
      };

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

      {/* ── Debug / animation control panel ─────────────────────────────── */}
      {debug && debugDisplay && (
        <div style={{
          position: 'absolute', top: 0, left: '100%', marginLeft: 8,
          background: 'rgba(0,0,0,0.88)', border: '1px solid rgba(0,148,255,0.4)',
          borderRadius: 6, padding: '8px 10px', width: 220,
          fontFamily: 'monospace', fontSize: 11, color: '#cde', lineHeight: 1.65,
          pointerEvents: 'auto', zIndex: 10, overflowY: 'auto', maxHeight: '90vh',
        }}>
          <div style={{ color: '#7df', fontWeight: 700, marginBottom: 4 }}>🐷 Lord Piggington</div>

          {/* Live status */}
          <div>Anim: <b style={{ color: '#fa0' }}>{debugDisplay.animState}</b></div>
          <div>Speech: <b style={{ color: debugDisplay.isSpeaking ? '#4f4' : '#888' }}>
            {debugDisplay.isSpeaking ? 'ACTIVE' : 'silent'}
          </b></div>
          <div>Energy: <b>{Math.round(debugDisplay.energy * 100)}</b>/100</div>
          <div>Shape: <b style={{ color: '#fa0' }}>{debugDisplay.activeShape || '—'}</b></div>
          <div>ExprMgr: <b style={{ color: debugDisplay.exprMgrFound ? '#4f4' : '#f44' }}>
            {debugDisplay.exprMgrFound ? 'YES' : 'NO'}
          </b></div>

          {/* Bone inventory */}
          <div style={{ marginTop: 6, color: '#89a' }}>
            Bones ✓ ({debugDisplay.bonesFound.length})
          </div>
          <div style={{ color: '#6b8', fontSize: 10 }}>
            {debugDisplay.bonesFound.join(', ') || '—'}
          </div>
          {debugDisplay.bonesMissing.length > 0 && (
            <>
              <div style={{ color: '#f88', marginTop: 3 }}>
                Missing ({debugDisplay.bonesMissing.length}):
              </div>
              <div style={{ color: '#f88', fontSize: 10 }}>
                {debugDisplay.bonesMissing.join(', ')}
              </div>
            </>
          )}

          {/* Animation controls */}
          <div style={{ marginTop: 8, color: '#89a', marginBottom: 4 }}>Animation controls</div>
          {(['idle','talking','walking','pointing','thinking','waving'] as AnimState[]).map(s => (
            <button key={s} onClick={() => setAnimState(s)} style={{
              display: 'inline-block', margin: '2px 2px',
              padding: '2px 7px', fontSize: 10, borderRadius: 3, cursor: 'pointer',
              background: debugDisplay.animState === s ? '#0af' : '#223',
              color: debugDisplay.animState === s ? '#000' : '#adf',
              border: '1px solid #358',
            }}>{s}</button>
          ))}

          {/* Mouth expressions */}
          <div style={{ marginTop: 8, color: '#89a', marginBottom: 2 }}>Mouth expressions</div>
          {debugDisplay.availableMouth.length === 0
            ? <div style={{ color: '#f88' }}>none detected</div>
            : debugDisplay.availableMouth.map(n => (
              <button key={n} onClick={() => testExpression(n)} style={{
                display: 'inline-block', margin: '2px 2px',
                padding: '2px 6px', fontSize: 10, borderRadius: 3, cursor: 'pointer',
                background: debugDisplay.activeShape === n ? '#0af' : '#223',
                color: debugDisplay.activeShape === n ? '#000' : '#adf',
                border: '1px solid #358',
              }}>{n}</button>
            ))
          }
          <button onClick={() => resetMouth(vrmRef.current!, availableMouthRef.current)} style={{
            display: 'block', marginTop: 4, padding: '2px 8px', fontSize: 10,
            borderRadius: 3, cursor: 'pointer', background: '#422',
            color: '#faa', border: '1px solid #755',
          }}>reset mouth</button>

          {/* All expression names */}
          <div style={{ marginTop: 8, color: '#89a', marginBottom: 2 }}>
            All expressions ({debugDisplay.expressionNames.length})
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
