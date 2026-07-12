/**
 * LordPiggingtonAvatar — Three.js + @pixiv/three-vrm
 *
 * Lip-sync fixes in this version
 * ───────────────────────────────
 * • Detects real expression names from the model at load time (logs to console)
 * • Maps charToViseme outputs ('open','rounded','narrow','press','rest')
 *   to actual VRM expression names instead of the literal strings 'aa'/'ih'…
 * • Uses expressionManager.getExpression() check before every setValue
 * • Smooth lerp per-expression so mouth never chatters
 * • resetMouth() on speaking end / test cleanup
 * • Optional debug overlay: expression list, energy bar, test buttons
 */
import React, { useRef, useEffect, useState, useCallback, memo } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils, VRM, VRMExpressionPresetName } from '@pixiv/three-vrm';
import type { LordPiggingtonProps, AvatarState } from './avatarTypes';
import { STATE_ACCENT_HEX, STATE_PULSE_HZ } from './avatarTypes';

// ─── Canvas dimensions — match the Home.tsx avatar container exactly ─────────
const W = 342;
const H = 455;

// ─── Mouth expression candidates (VRM 1.0 lowercase then VRM 0.x uppercase) ──
const MOUTH_CANDIDATES = ['aa', 'ih', 'ou', 'ee', 'oh', 'A', 'I', 'U', 'E', 'O'];

// charToViseme() in Home.tsx returns one of these values:
//   'open' | 'rounded' | 'narrow' | 'press' | 'rest'
// Map each to VRM expression name candidates (first found in model wins).
const VISEME_CANDIDATES: Record<string, string[]> = {
  open:    ['aa', 'A'],           // a, i, u sounds
  rounded: ['ou', 'oh', 'U'],     // o sound
  narrow:  ['ee', 'ih', 'E', 'I'],// e, f, v sounds
  press:   ['ih', 'I'],           // m, b, p (lips pressed then released)
  rest:    [],                    // silence — resetMouth
};

// Non-mouth expressions (market state reactions)
const STATE_EXPR_NAMES = [
  VRMExpressionPresetName.Happy,
  VRMExpressionPresetName.Sad,
  VRMExpressionPresetName.Angry,
  VRMExpressionPresetName.Surprised,
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

// ─── Helpers ──────────────────────────────────────────────────────────────────
function expDecay(a: number, b: number, lambda: number, dt: number) {
  return a + (b - a) * (1 - Math.exp(-lambda * dt));
}

// Check existence before setting — prevents silent errors on missing expressions
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

// Reset all detected mouth expressions to 0 (smoothly via lerp in loop)
function resetMouth(vrm: VRM, available: string[]) {
  available.forEach(name => safeSet(vrm, name, 0));
}

// Apply one mouth shape — lerp all available candidates, target only the active one
function setMouthShape(vrm: VRM, available: string[], active: string, amount: number) {
  available.forEach(name => {
    const target = name === active ? amount : 0;
    const current = safeGet(vrm, name);
    const smoothed = THREE.MathUtils.lerp(current, target, 0.35);
    safeSet(vrm, name, smoothed);
  });
}

// Auto-frame camera to show the FULL body with a little breathing room
function autoFrame(_vrm: VRM, gltfScene: THREE.Group, camera: THREE.PerspectiveCamera) {
  const box = new THREE.Box3().setFromObject(gltfScene);
  if (box.isEmpty()) return;
  const size   = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());

  // Add 6 % padding above + below so the model doesn't touch the canvas edge
  const pad     = size.y * 0.06;
  const frameTop = box.max.y + pad;
  const frameBtm = box.min.y - pad;
  const frameH   = frameTop - frameBtm;
  const frameCY  = (frameTop + frameBtm) / 2;

  // Distance so frameH fills 92 % of the vertical FOV
  const fovRad   = (camera.fov * Math.PI) / 180;
  const distance = (frameH / 2) / Math.tan(fovRad / 2) / 0.92;

  camera.position.set(center.x, frameCY, distance);
  camera.lookAt(center.x, frameCY, 0);
  camera.updateProjectionMatrix();
}

// ─── Debug info (written by RAF, read by debug UI interval) ──────────────────
interface DebugData {
  expressionNames: string[];
  availableMouth:  string[];
  activeShape:     string;
  energy:          number;
  isSpeaking:      boolean;
  exprMgrFound:    boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────
interface Props extends LordPiggingtonProps { debug?: boolean; }

function LordPiggingtonAvatar({ avState, speaking, gazeEvent, speechCtrlRef, debug = false }: Props) {
  const canvasRef   = useRef<HTMLCanvasElement>(null);
  const vrmRef      = useRef<VRM | null>(null);
  const accentRef   = useRef<THREE.PointLight | null>(null);
  const rafRef      = useRef<number>(0);
  const pausedRef   = useRef(false);

  // Detected mouth expression names (populated after VRM load)
  const availableMouthRef = useRef<string[]>([]);
  // Current active mouth shape name (written by RAF, read by debug UI)
  const activeShapeRef    = useRef<string>('');
  // Test expression request (set by button click, cleared after hold)
  const testExprRef       = useRef<{ name: string; until: number } | null>(null);

  // Live refs for animation loop
  const liveState  = useRef(avState);
  const liveSpeech = useRef(speechCtrlRef);
  const liveGaze   = useRef({ lastId: -1, dx: 0, dy: 0 });

  // Animation state
  const blinkRef  = useRef({ phase: 'idle' as 'idle'|'closing'|'opening', t: 0, next: 2.8 });
  const exprRef   = useRef<Record<string, number>>({});
  const lookRef   = useRef({ yaw: 0, pitch: 0 });
  const glanceRef = useRef({ nextGlance: 6 + Math.random() * 8, t: 0, targetX: 0, targetY: 0, active: false, holdT: 0 });
  const nodRef    = useRef({ active: false, t: 0, dir: 1 });
  const prevState = useRef<AvatarState>(avState);

  // Debug display state (only used when debug=true)
  const debugDataRef = useRef<DebugData>({
    expressionNames: [], availableMouth: [], activeShape: '',
    energy: 0, isSpeaking: false, exprMgrFound: false,
  });
  const [debugDisplay, setDebugDisplay] = useState<DebugData | null>(null);

  // Keep live refs in sync
  useEffect(() => { liveState.current = avState; }, [avState]);
  useEffect(() => { liveSpeech.current = speechCtrlRef; }, [speechCtrlRef]);
  useEffect(() => {
    if (gazeEvent && gazeEvent.id !== liveGaze.current.lastId) {
      liveGaze.current.dx = gazeEvent.dx;
      liveGaze.current.dy = gazeEvent.dy;
      liveGaze.current.lastId = gazeEvent.id;
    }
  }, [gazeEvent]);

  // Debug UI poll — reads debugDataRef and pushes to React state every 200ms
  useEffect(() => {
    if (!debug) return;
    const id = setInterval(() => setDebugDisplay({ ...debugDataRef.current }), 200);
    return () => clearInterval(id);
  }, [debug]);

  // Test button handler — sets expression for 900ms
  const testExpression = useCallback((name: string) => {
    testExprRef.current = { name, until: performance.now() + 900 };
  }, []);

  // ── Main Three.js effect (mount once) ──────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new THREE.WebGLRenderer({
      canvas, alpha: true, antialias: true, premultipliedAlpha: false,
    });
    renderer.setSize(W, H);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    const scene = new THREE.Scene();
    scene.background = null;

    // Wider FOV for full-body portrait; aspect matches canvas exactly
    const camera = new THREE.PerspectiveCamera(30, W / H, 0.01, 30);
    camera.position.set(0, 0.85, 3.0);
    camera.lookAt(0, 0.85, 0);

    const lookAtTarget = new THREE.Object3D();
    camera.add(lookAtTarget);
    scene.add(camera);

    // Lighting
    scene.add(new THREE.AmbientLight(0x6070a0, 0.9));
    const key = new THREE.DirectionalLight(0xfff0e8, 1.6);
    key.position.set(0.8, 2.5, 1.8); scene.add(key);
    const fill = new THREE.DirectionalLight(0x4060c0, 0.50);
    fill.position.set(-1.5, 1.0, 1.0); scene.add(fill);
    const rim = new THREE.DirectionalLight(0x1030b0, 0.85);
    rim.position.set(0, 2.0, -2.0); scene.add(rim);
    const accent = new THREE.PointLight(0x0094ff, 1.6, 2.8);
    accent.position.set(0, 1.42, 0.80); scene.add(accent);
    accentRef.current = accent;

    // Load VRM
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    loader.load('/LordPiggington.vrm', (gltf) => {
      const vrm: VRM = gltf.userData.vrm;
      try { VRMUtils.removeUnnecessaryVertices(gltf.scene); } catch (_) {}
      try { VRMUtils.rotateVRM0(vrm); } catch (_) {}
      if (vrm.lookAt) vrm.lookAt.target = lookAtTarget;
      scene.add(gltf.scene);
      vrmRef.current = vrm;
      autoFrame(vrm, gltf.scene, camera);

      // ── Detect and log all expression names ────────────────────────────────
      const allNames: string[] = vrm.expressionManager?.expressions
        .map((e: any) => e.expressionName) ?? [];
      console.log('[LordPiggington] all expressions:', allNames);

      const foundMouth = MOUTH_CANDIDATES.filter(
        name => vrm.expressionManager?.getExpression(name)
      );
      console.log('[LordPiggington] mouth expressions found:', foundMouth);
      availableMouthRef.current = foundMouth;

      debugDataRef.current = {
        ...debugDataRef.current,
        expressionNames: allNames,
        availableMouth:  foundMouth,
        exprMgrFound:    !!vrm.expressionManager,
      };
    }, undefined, (err) => console.error('[LordPiggington] load error:', err));

    // Page visibility pause
    const onVis = () => { pausedRef.current = document.hidden; };
    document.addEventListener('visibilitychange', onVis);

    // ── Render loop ─────────────────────────────────────────────────────────
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

      const em       = vrm.expressionManager;
      const hum      = vrm.humanoid;
      const state    = liveState.current;
      const sc       = liveSpeech.current?.current;
      const avMouth  = availableMouthRef.current;

      // ── State-transition nod/shake ────────────────────────────────────────
      if (state !== prevState.current) {
        const pos = state === 'READY_LONG'  || state === 'TARGET_HIT';
        const neg = state === 'STOP_HIT'    || state === 'READY_SHORT';
        if (pos || neg) nodRef.current = { active: true, t: 0, dir: pos ? 1 : -1 };
        prevState.current = state;
      }

      // ── Blink FSM ─────────────────────────────────────────────────────────
      const bk = blinkRef.current;
      bk.t += dt;
      const BLK = VRMExpressionPresetName.Blink;
      if (bk.phase === 'idle' && bk.t >= bk.next) {
        bk.phase = 'closing'; bk.t = 0;
      } else if (bk.phase === 'closing') {
        const v = Math.min(bk.t / 0.055, 1); safeSet(vrm, BLK, v);
        if (v >= 1) { bk.phase = 'opening'; bk.t = 0; }
      } else if (bk.phase === 'opening') {
        const v = 1 - Math.min(bk.t / 0.09, 1); safeSet(vrm, BLK, v);
        if (v <= 0) { safeSet(vrm, BLK, 0); bk.phase='idle'; bk.t=0; bk.next=3+Math.random()*5; }
      }

      // ── Test expression override (debug buttons) ──────────────────────────
      const test = testExprRef.current;
      if (test) {
        if (now < test.until) {
          resetMouth(vrm, avMouth);
          safeSet(vrm, test.name, 1.0);
          renderer.render(scene, camera);
          vrm.update(dt); try { em?.update(); } catch (_) {}
          return; // skip normal lip-sync while testing
        } else {
          resetMouth(vrm, avMouth);
          testExprRef.current = null;
        }
      }

      // ── Lip-sync ─────────────────────────────────────────────────────────
      const isSpeaking = sc?.active ?? false;
      const rawEnergy  = isSpeaking ? Math.max(0, Math.min(1, sc?.energy ?? 0)) : 0;

      let activeShape = '';
      if (rawEnergy > 0.02 && avMouth.length > 0) {
        // Map charToViseme output → first available VRM expression name
        const viseme = sc?.viseme ?? 'open';
        const candidates = VISEME_CANDIDATES[viseme] ?? VISEME_CANDIDATES.open;
        activeShape = candidates.find(c => avMouth.includes(c)) ?? avMouth[0] ?? '';

        if (activeShape) {
          setMouthShape(vrm, avMouth, activeShape, rawEnergy * 0.90);
        }
      } else {
        // Smooth close: lerp all mouth expressions toward 0
        avMouth.forEach(name => {
          const cur = safeGet(vrm, name);
          if (cur > 0.001) safeSet(vrm, name, THREE.MathUtils.lerp(cur, 0, 0.25));
          else safeSet(vrm, name, 0);
        });
      }

      // Update debug ref
      activeShapeRef.current = activeShape;
      debugDataRef.current = {
        ...debugDataRef.current,
        activeShape,
        energy:     rawEnergy,
        isSpeaking,
      };

      // ── Market-state expressions ──────────────────────────────────────────
      const targets = STATE_EXPR[state] ?? {};
      STATE_EXPR_NAMES.forEach(name => {
        const tgt  = targets[name] ?? 0;
        const cur  = exprRef.current[name] ?? 0;
        const next = expDecay(cur, tgt, 3.5, dt);
        exprRef.current[name] = next;
        safeSet(vrm, name, next);
      });

      // ── Gaze + occasional glance ──────────────────────────────────────────
      const g  = liveGaze.current;
      const gl = glanceRef.current;
      gl.t += dt;
      let tYaw = g.dx * 0.018, tPitch = -g.dy * 0.012;
      if (!gl.active && gl.t >= gl.nextGlance) {
        gl.active = true; gl.t = 0; gl.holdT = 0;
        gl.targetX = (Math.random() < 0.5 ? -1 : 1) * (0.15 + Math.random() * 0.25);
        gl.targetY = (Math.random() - 0.5) * 0.10;
      }
      if (gl.active) {
        gl.holdT += dt; tYaw += gl.targetX; tPitch += gl.targetY;
        if (gl.holdT > 1.2 + Math.random() * 0.8) {
          gl.active = false; gl.t = 0; gl.nextGlance = 5 + Math.random() * 9;
        }
      }
      lookRef.current.yaw   = expDecay(lookRef.current.yaw,   tYaw,   5.5, dt);
      lookRef.current.pitch = expDecay(lookRef.current.pitch, tPitch, 5.5, dt);

      // ── Bone animation ────────────────────────────────────────────────────
      try {
        const breathRot = Math.sin(elapsed * 0.80) * 0.0055;
        const swayRot   = Math.sin(elapsed * 0.28) * 0.0035;
        const nd = nodRef.current;
        let nodPitch = 0, nodYaw = 0;
        if (nd.active) {
          nd.t += dt;
          if (nd.t < 0.7) {
            const w = Math.sin(nd.t / 0.7 * Math.PI * 2.5) * 0.06;
            if (nd.dir > 0) nodPitch = w; else nodYaw = w;
          } else nd.active = false;
        }
        const analyzeLean = state === 'ANALYZING' ? 0.055 : 0;
        const activeLean  = state === 'ACTIVE'    ? 0.025 : 0;

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
        if (spine) { spine.rotation.x = breathRot * 0.5; spine.rotation.z = swayRot * 0.4; }
        const uc = hum?.getNormalizedBoneNode?.('upperChest' as never);
        if (uc) { uc.rotation.x = breathRot * 0.3; uc.rotation.z = swayRot * 0.25; }

        // ── Arm & leg walk cycle ─────────────────────────────────────────────
        // Amplitude scales with market state — READY is energetic, STOP is sluggish
        const walkHz  = 0.52; // comfortable idle cadence (~1 step/s)
        const phi     = elapsed * Math.PI * 2 * walkHz;
        const ampMult = (state === 'READY_LONG' || state === 'READY_SHORT') ? 1.55
                      : state === 'TARGET_HIT' ? 2.0
                      : state === 'STOP_HIT'   ? 0.28
                      : 1.0;

        // Upper-arm X = forward / back flex; sin alternates L vs R
        const armSwing  = Math.sin(phi) * 0.30 * ampMult;
        // Elbow bends when the arm is swinging back (trailing phase)
        const elbowL    = 0.14 + Math.max(0,  Math.sin(phi + Math.PI * 0.5)) * 0.26 * ampMult;
        const elbowR    = 0.14 + Math.max(0, -Math.sin(phi + Math.PI * 0.5)) * 0.26 * ampMult;

        const lUA = hum?.getNormalizedBoneNode?.('leftUpperArm'  as never);
        if (lUA) { lUA.rotation.x =  armSwing; lUA.rotation.z = swayRot * 0.2; }
        const lLA = hum?.getNormalizedBoneNode?.('leftLowerArm'  as never);
        if (lLA) { lLA.rotation.x = elbowL; }
        const rUA = hum?.getNormalizedBoneNode?.('rightUpperArm' as never);
        if (rUA) { rUA.rotation.x = -armSwing; rUA.rotation.z = -swayRot * 0.2; }
        const rLA = hum?.getNormalizedBoneNode?.('rightLowerArm' as never);
        if (rLA) { rLA.rotation.x = elbowR; }

        // Legs swing opposite phase to same-side arm (natural counter-sway)
        const legSwing = Math.sin(phi + Math.PI) * 0.20 * ampMult;
        // Knee bends at the back of each leg's swing arc
        const kneeL = 0.05 + Math.max(0,  Math.sin(phi + Math.PI * 1.5)) * 0.18 * ampMult;
        const kneeR = 0.05 + Math.max(0, -Math.sin(phi + Math.PI * 1.5)) * 0.18 * ampMult;

        const lUL = hum?.getNormalizedBoneNode?.('leftUpperLeg'  as never);
        if (lUL) { lUL.rotation.x =  legSwing; }
        const lLL = hum?.getNormalizedBoneNode?.('leftLowerLeg'  as never);
        if (lLL) { lLL.rotation.x = kneeL; }
        const rUL = hum?.getNormalizedBoneNode?.('rightUpperLeg' as never);
        if (rUL) { rUL.rotation.x = -legSwing; }
        const rLL = hum?.getNormalizedBoneNode?.('rightLowerLeg' as never);
        if (rLL) { rLL.rotation.x = kneeR; }

        // Hips: subtle side-tilt + counter-rotate matching each step
        const hipsNode = hum?.getNormalizedBoneNode?.('hips' as never);
        if (hipsNode) {
          hipsNode.rotation.y = Math.sin(phi) * 0.03 * ampMult;
          hipsNode.rotation.z = Math.sin(phi) * 0.025 * ampMult;
        }
      } catch (_) {}

      // ── Accent light ──────────────────────────────────────────────────────
      if (accentRef.current) {
        accentRef.current.color.setHex(STATE_ACCENT_HEX[state]);
        const hz    = STATE_PULSE_HZ[state];
        const isHot = state === 'READY_LONG' || state === 'READY_SHORT' || state === 'TARGET_HIT';
        accentRef.current.intensity = (isHot ? 2.0 : 1.4) + Math.sin(elapsed * hz * Math.PI * 2) * (isHot ? 0.7 : 0.25);
      }

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

      {/* ── Debug overlay (visible when debug=true) ─────────────────────── */}
      {debug && debugDisplay && (
        <div style={{
          position: 'absolute', top: 0, left: '100%', marginLeft: 8,
          background: 'rgba(0,0,0,0.82)', border: '1px solid rgba(0,148,255,0.4)',
          borderRadius: 6, padding: '8px 10px', minWidth: 200,
          fontFamily: 'monospace', fontSize: 11, color: '#cde', lineHeight: 1.6,
          pointerEvents: 'auto', zIndex: 10,
        }}>
          <div style={{ color: '#7df', fontWeight: 700, marginBottom: 4 }}>LordPiggington Debug</div>
          <div>ExprMgr: <b style={{ color: debugDisplay.exprMgrFound ? '#4f4' : '#f44' }}>
            {debugDisplay.exprMgrFound ? 'YES' : 'NO'}
          </b></div>
          <div>Lip Sync: <b style={{ color: debugDisplay.isSpeaking ? '#4f4' : '#888' }}>
            {debugDisplay.isSpeaking ? 'ACTIVE' : 'INACTIVE'}
          </b></div>
          <div>Energy: <b>{Math.round(debugDisplay.energy * 100)}</b>/100</div>
          <div>Shape: <b style={{ color: '#fa0' }}>{debugDisplay.activeShape || '—'}</b></div>

          <div style={{ marginTop: 6, marginBottom: 2, color: '#89a' }}>Mouth expressions:</div>
          {debugDisplay.availableMouth.length === 0
            ? <div style={{ color: '#f64' }}>none found</div>
            : debugDisplay.availableMouth.map(n => (
                <div key={n} style={{ color: n === debugDisplay.activeShape ? '#fa0' : '#cde' }}>
                  {n === debugDisplay.activeShape ? '▶ ' : '  '}{n}
                </div>
              ))
          }

          <div style={{ marginTop: 6, marginBottom: 2, color: '#89a' }}>Test buttons:</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
            {debugDisplay.availableMouth.map(name => (
              <button key={name} onClick={() => testExpression(name)} style={{
                background: 'rgba(0,148,255,0.18)', border: '1px solid rgba(0,148,255,0.5)',
                color: '#7df', borderRadius: 3, padding: '2px 5px', cursor: 'pointer',
                fontSize: 10, fontFamily: 'monospace',
              }}>{name}</button>
            ))}
            <button onClick={() => {
              testExprRef.current = null;
              const vrm = vrmRef.current;
              if (vrm) resetMouth(vrm, availableMouthRef.current);
            }} style={{
              background: 'rgba(255,60,60,0.18)', border: '1px solid rgba(255,60,60,0.5)',
              color: '#f88', borderRadius: 3, padding: '2px 5px', cursor: 'pointer',
              fontSize: 10, fontFamily: 'monospace',
            }}>Reset</button>
          </div>

          <div style={{ marginTop: 6, marginBottom: 2, color: '#89a' }}>All expressions:</div>
          <div style={{ maxHeight: 80, overflowY: 'auto', fontSize: 10, color: '#7a9' }}>
            {debugDisplay.expressionNames.join(', ') || 'loading…'}
          </div>
        </div>
      )}
    </div>
  );
}

LordPiggingtonAvatar.displayName = 'LordPiggingtonAvatar';
export default memo(LordPiggingtonAvatar);
