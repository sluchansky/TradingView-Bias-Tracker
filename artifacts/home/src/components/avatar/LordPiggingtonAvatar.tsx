/**
 * LordPiggingtonAvatar — Three.js + @pixiv/three-vrm  (v5 — locomotion + gestures)
 *
 * NEW in v5:
 * ──────────
 * 1. Avatar physically walks left/right across the canvas; flips to face the direction
 *    of travel, drifts back to center when not walking.
 * 2. Spontaneous roam timer — avatar auto-walks every ~20-35 s when idle (feels alive).
 * 3. New animation states: 'shrug' and 'fistpump' (one-shots).
 * 4. Richer talk-gesture cycling: three gesture patterns rotate slowly while speaking.
 * 5. vrmSrc prop — any VRM URL (or User-provided) can be loaded; effect rebuilds on change.
 *
 * LAYER ORDER (unchanged):
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
const W = 420;
const H = 560;
const LORD_PIGGINGTON_SRC = '/LordPiggington.vrm';

// ─── Animation state ──────────────────────────────────────────────────────────
type AnimState = 'idle' | 'talking' | 'walking' | 'pointing' | 'thinking' | 'waving' | 'dancing' | 'shrug' | 'fistpump';
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

// ─── ARM Z-ROTATION CONSTANTS ─────────────────────────────────────────────────
// In three-vrm normalised-humanoid space the T-pose has arms horizontal.
//   leftUpperArm  z = +ARM_Z  → arm swings DOWN to side
//   rightUpperArm z = -ARM_Z  → arm swings DOWN to side
const ARM_Z = 1.4;

// ─── Walk locomotion ──────────────────────────────────────────────────────────
const WALK_SPEED  = 0.22;  // world units per second
const WALK_LIMIT  = 0.42;  // ± world-X boundary before turning

// ─── Mouth expression candidates ──────────────────────────────────────────────
// VRM1.0 preset names (lowercase), VRM0.x legacy (uppercase), plus common custom names
const MOUTH_CANDIDATES = [
  'aa','ih','ou','ee','oh',          // VRM1.0 presets
  'A','I','U','E','O',               // VRM0.x presets
  'mouthOpen','mouth_open',          // common custom
  'viseme_aa','viseme_PP',           // alternate naming
];
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

function idleTargets(breathX: number, swayZ: number): BoneTargets {
  return {
    hips:          { x: 0,        y: 0, z: swayZ * 0.18 },
    spine:         { x: breathX,  y: 0, z: swayZ * 0.35 },
    chest:         { x: breathX * 0.55, y: 0, z: swayZ * 0.28 },
    upperChest:    { x: breathX * 0.28, y: 0, z: swayZ * 0.20 },
    leftUpperArm:  { x: 0, y: 0, z:  ARM_Z },
    leftLowerArm:  { x: 0.16, y: 0, z: 0 },
    leftHand:      { x: 0, y: 0, z: 0 },
    rightUpperArm: { x: 0, y: 0, z: -ARM_Z },
    rightLowerArm: { x: 0.16, y: 0, z: 0 },
    rightHand:     { x: 0, y: 0, z: 0 },
    leftUpperLeg:  { x: 0.02, y: 0, z: 0 },
    leftLowerLeg:  { x: 0.04, y: 0, z: 0 },
    leftFoot:      { x: -0.04, y: 0, z: 0 },
    rightUpperLeg: { x: 0.02, y: 0, z: 0 },
    rightLowerLeg: { x: 0.04, y: 0, z: 0 },
    rightFoot:     { x: -0.04, y: 0, z: 0 },
  };
}

function calmIdleTargets(breathX: number, swayZ: number): BoneTargets {
  const base = idleTargets(breathX, swayZ);
  return {
    ...base,
    hips:          { x: 0, y: 0, z: swayZ * 0.72 },
    spine:         { x: breathX, y: 0, z: swayZ * 0.42 },
    chest:         { x: breathX * 0.62, y: 0, z: swayZ * 0.24 },
    upperChest:    { x: breathX * 0.32, y: 0, z: swayZ * 0.14 },
    leftUpperLeg:  { x: 0.02, y: 0, z: swayZ * 0.08 },
    rightUpperLeg: { x: 0.02, y: 0, z: -swayZ * 0.08 },
  };
}

function lordPiggingtonIdleTargets(breathX: number, swayZ: number): BoneTargets {
  return {
    hips:          { x: 0, y: 0, z: swayZ * 0.50 },
    spine:         { x: breathX * 0.42, y: 0, z: swayZ * 0.28 },
    chest:         { x: breathX * 0.72, y: 0, z: swayZ * 0.16 },
    upperChest:    { x: breathX * 0.46, y: 0, z: swayZ * 0.10 },
    leftUpperArm:  { x: 0, y: 0, z:  ARM_Z },
    leftLowerArm:  { x: 0.18, y: 0, z: 0 },
    leftHand:      { x: 0, y: 0, z: 0 },
    rightUpperArm: { x: 0, y: 0, z: -ARM_Z },
    rightLowerArm: { x: 0.18, y: 0, z: 0 },
    rightHand:     { x: 0, y: 0, z: 0 },
    leftUpperLeg:  { x: 0.02, y: 0, z: swayZ * 0.06 },
    leftLowerLeg:  { x: 0.04, y: 0, z: 0 },
    leftFoot:      { x: -0.04, y: 0, z: 0 },
    rightUpperLeg: { x: 0.02, y: 0, z: -swayZ * 0.06 },
    rightLowerLeg: { x: 0.04, y: 0, z: 0 },
    rightFoot:     { x: -0.04, y: 0, z: 0 },
  };
}

function calmTalkTargets(breathX: number, swayZ: number, elapsed: number): BoneTargets {
  const base = calmIdleTargets(breathX, swayZ);
  const emphasis = Math.sin(elapsed * 0.9) * 0.012;
  return {
    ...base,
    spine:         { x: breathX + 0.012, y: 0, z: swayZ * 0.32 },
    chest:         { x: breathX * 0.62 + 0.014, y: 0, z: swayZ * 0.18 },
    leftLowerArm:  { x: 0.17 + emphasis, y: 0, z: 0 },
    rightLowerArm: { x: 0.17 - emphasis, y: 0, z: 0 },
  };
}

function calmThinkingTargets(breathX: number, swayZ: number): BoneTargets {
  const base = calmIdleTargets(breathX, swayZ);
  return {
    ...base,
    spine:      { x: breathX + 0.018, y: 0, z: swayZ * 0.30 },
    chest:      { x: breathX * 0.55 + 0.012, y: 0, z: swayZ * 0.18 },
    upperChest: { x: breathX * 0.28, y: 0, z: swayZ * 0.12 },
  };
}

function listeningTargets(breathX: number, swayZ: number): BoneTargets {
  const base = calmIdleTargets(breathX, swayZ);
  return {
    ...base,
    hips:       { x: 0.008, y: 0, z: swayZ * 0.42 },
    spine:      { x: breathX + 0.032, y: 0, z: swayZ * 0.25 },
    chest:      { x: breathX * 0.5 + 0.026, y: 0, z: swayZ * 0.14 },
    upperChest: { x: 0.014, y: 0, z: swayZ * 0.08 },
  };
}

// talkTargets cycles through 3 gesture styles based on a slow phase value
function talkTargets(breathX: number, swayZ: number, phi: number, talkPhase: number): BoneTargets {
  const cycle = talkPhase % 3;  // 0, 1, or 2
  const t = Math.sin(phi) * 0.14;

  if (cycle < 1) {
    // Style A: small right-arm forward swing
    return {
      hips:          { x: 0,              y: 0, z: swayZ * 0.15 },
      spine:         { x: breathX * 1.2,  y: 0, z: swayZ * 0.30 },
      chest:         { x: breathX * 0.70, y: 0, z: swayZ * 0.24 },
      upperChest:    { x: breathX * 0.36, y: 0, z: swayZ * 0.18 },
      leftUpperArm:  { x: 0,       y: 0, z:  ARM_Z },
      leftLowerArm:  { x: 0.18,    y: 0, z: 0 },
      leftHand:      { x: 0,       y: 0, z: 0 },
      rightUpperArm: { x: t,       y: 0, z: -ARM_Z },
      rightLowerArm: { x: 0.24 + Math.abs(t) * 0.4, y: 0, z: 0 },
      rightHand:     { x: 0,       y: 0, z: 0 },
      leftUpperLeg:  { x: 0.02, y: 0, z: 0 },
      leftLowerLeg:  { x: 0.04, y: 0, z: 0 },
      leftFoot:      { x: -0.04, y: 0, z: 0 },
      rightUpperLeg: { x: 0.02, y: 0, z: 0 },
      rightLowerLeg: { x: 0.04, y: 0, z: 0 },
      rightFoot:     { x: -0.04, y: 0, z: 0 },
    };
  } else if (cycle < 2) {
    // Style B: both arms open slightly (expansive gesture)
    const open = Math.abs(Math.sin(phi * 0.7)) * 0.18;
    return {
      hips:          { x: 0,              y: 0, z: swayZ * 0.18 },
      spine:         { x: breathX * 1.1,  y: 0, z: swayZ * 0.28 },
      chest:         { x: breathX * 0.60, y: 0, z: swayZ * 0.20 },
      upperChest:    { x: breathX * 0.30, y: 0, z: swayZ * 0.14 },
      leftUpperArm:  { x: -open, y: 0, z:  ARM_Z - 0.08 },
      leftLowerArm:  { x: 0.22,  y: 0, z: 0 },
      leftHand:      { x: 0,     y: 0, z: 0.06 },
      rightUpperArm: { x: -open, y: 0, z: -ARM_Z + 0.08 },
      rightLowerArm: { x: 0.22,  y: 0, z: 0 },
      rightHand:     { x: 0,     y: 0, z: -0.06 },
      leftUpperLeg:  { x: 0.02, y: 0, z: 0 },
      leftLowerLeg:  { x: 0.04, y: 0, z: 0 },
      leftFoot:      { x: -0.04, y: 0, z: 0 },
      rightUpperLeg: { x: 0.02, y: 0, z: 0 },
      rightLowerLeg: { x: 0.04, y: 0, z: 0 },
      rightFoot:     { x: -0.04, y: 0, z: 0 },
    };
  } else {
    // Style C: left-arm raised, emphatic point-ish
    const liftL = 0.08 + Math.max(0, Math.sin(phi * 0.6)) * 0.16;
    return {
      hips:          { x: 0,              y: 0, z: swayZ * 0.16 },
      spine:         { x: breathX * 1.15, y: 0, z: swayZ * 0.32 },
      chest:         { x: breathX * 0.65, y: 0, z: swayZ * 0.22 },
      upperChest:    { x: breathX * 0.32, y: 0, z: swayZ * 0.16 },
      leftUpperArm:  { x: -liftL, y: 0, z:  ARM_Z - 0.20 },
      leftLowerArm:  { x: 0.50,   y: 0, z: 0 },
      leftHand:      { x: 0.06,   y: 0, z: 0 },
      rightUpperArm: { x: 0,      y: 0, z: -ARM_Z },
      rightLowerArm: { x: 0.18,   y: 0, z: 0 },
      rightHand:     { x: 0,      y: 0, z: 0 },
      leftUpperLeg:  { x: 0.02, y: 0, z: 0 },
      leftLowerLeg:  { x: 0.04, y: 0, z: 0 },
      leftFoot:      { x: -0.04, y: 0, z: 0 },
      rightUpperLeg: { x: 0.02, y: 0, z: 0 },
      rightLowerLeg: { x: 0.04, y: 0, z: 0 },
      rightFoot:     { x: -0.04, y: 0, z: 0 },
    };
  }
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
    rightUpperArm: { x: -0.30, y: 0, z: -0.55 },
    rightLowerArm: { x:  0.22, y: 0, z: 0 },
    rightHand:     { x:  0,    y: 0, z: 0 },
    leftUpperArm:  { x:  0,    y: 0, z:  ARM_Z + 0.08 },
  };
}

function thinkTargets(breathX: number, elapsed: number): BoneTargets {
  const base = idleTargets(breathX, 0);
  const rock = Math.sin(elapsed * 0.55) * 0.014;
  return {
    ...base,
    hips:          { x: rock, y: 0, z: 0 },
    rightUpperArm: { x: -0.50, y: 0, z: -0.48 },
    rightLowerArm: { x:  1.20, y: 0, z: 0 },
    rightHand:     { x: -0.12, y: 0, z: 0 },
    leftUpperArm:  { x:  0,    y: 0, z:  ARM_Z + 0.05 },
  };
}

function waveTargets(phi: number, breathX: number): BoneTargets {
  const base  = idleTargets(breathX, 0);
  const waveZ = Math.sin(phi * 3.5) * 0.42;
  return {
    ...base,
    rightUpperArm: { x: -0.44, y: 0, z: -0.78 },
    rightLowerArm: { x:  0.82, y: 0, z:  0 },
    rightHand:     { x:  0,    y: 0, z: waveZ },
  };
}

function danceTargets(phi: number): BoneTargets {
  const beat    = phi * 2;
  const hipSway = Math.sin(beat) * 0.11;
  const hipBob  = Math.abs(Math.sin(beat * 2)) * 0.032;
  const armLz   = ARM_Z - 0.65 + Math.sin(beat + Math.PI) * 0.48;
  const armRz   = -(ARM_Z - 0.65 + Math.sin(beat) * 0.48);
  const armLx   =  Math.sin(beat + Math.PI) * 0.32;
  const armRx   =  Math.sin(beat) * 0.32;
  const elbL    = 0.55 + Math.sin(beat * 2)           * 0.28;
  const elbR    = 0.55 + Math.sin(beat * 2 + Math.PI) * 0.28;
  const kneeL   = 0.08 + Math.max(0, Math.sin(beat * 2 + Math.PI)) * 0.20;
  const kneeR   = 0.08 + Math.max(0, Math.sin(beat * 2))           * 0.20;
  return {
    hips:          { x: hipBob,  y: 0, z: hipSway            },
    spine:         { x: 0,       y: 0, z: -hipSway * 0.42    },
    chest:         { x: 0,       y: 0, z:  hipSway * 0.24    },
    upperChest:    { x: 0,       y: 0, z:  0                  },
    leftUpperArm:  { x:  armLx,  y: 0, z: armLz               },
    leftLowerArm:  { x: elbL,    y: 0, z: 0                   },
    leftHand:      { x: 0,       y: 0, z:  Math.sin(beat * 3) * 0.22 },
    rightUpperArm: { x:  armRx,  y: 0, z: armRz               },
    rightLowerArm: { x: elbR,    y: 0, z: 0                   },
    rightHand:     { x: 0,       y: 0, z: -Math.sin(beat * 3) * 0.22 },
    leftUpperLeg:  { x:  Math.sin(beat * 2 + Math.PI) * 0.09, y: 0, z: 0 },
    leftLowerLeg:  { x: kneeL,   y: 0, z: 0                   },
    leftFoot:      { x: -0.04,   y: 0, z: 0                   },
    rightUpperLeg: { x:  Math.sin(beat * 2)           * 0.09, y: 0, z: 0 },
    rightLowerLeg: { x: kneeR,   y: 0, z: 0                   },
    rightFoot:     { x: -0.04,   y: 0, z: 0                   },
  };
}

function shrugTargets(breathX: number, elapsed: number): BoneTargets {
  // Both arms raise to shoulder height with spread hands — classic shrug
  const hold = Math.sin(elapsed * 1.8) * 0.018;
  return {
    hips:          { x: 0,          y: 0, z: 0 },
    spine:         { x: breathX,    y: 0, z: 0 },
    chest:         { x: breathX * 0.4 + 0.04, y: 0, z: 0 },
    upperChest:    { x: 0.06 + hold, y: 0, z: 0 },
    leftUpperArm:  { x: -0.08, y: 0, z:  0.62 },   // arms raised up & out
    leftLowerArm:  { x:  0.28, y: 0, z: 0 },
    leftHand:      { x:  0,    y: 0, z:  0.30 },   // palms-up hand tilt
    rightUpperArm: { x: -0.08, y: 0, z: -0.62 },
    rightLowerArm: { x:  0.28, y: 0, z: 0 },
    rightHand:     { x:  0,    y: 0, z: -0.30 },
    leftUpperLeg:  { x: 0.02, y: 0, z: 0 },
    leftLowerLeg:  { x: 0.04, y: 0, z: 0 },
    leftFoot:      { x: -0.04, y: 0, z: 0 },
    rightUpperLeg: { x: 0.02, y: 0, z: 0 },
    rightLowerLeg: { x: 0.04, y: 0, z: 0 },
    rightFoot:     { x: -0.04, y: 0, z: 0 },
  };
}

function fistpumpTargets(elapsed: number): BoneTargets {
  // Right fist repeatedly punches upward — victory pump
  const pump  = Math.abs(Math.sin(elapsed * 5.5));   // fast pump cycle
  const raise = 0.40 + pump * 0.28;                  // arm rises more at peak
  return {
    hips:          { x: pump * 0.012, y: 0, z: 0 },
    spine:         { x: pump * 0.018, y: 0, z: 0 },
    chest:         { x: pump * 0.012, y: 0, z: 0 },
    upperChest:    { x: pump * 0.006, y: 0, z: 0 },
    leftUpperArm:  { x: 0, y: 0, z:  ARM_Z },
    leftLowerArm:  { x: 0.16, y: 0, z: 0 },
    leftHand:      { x: 0, y: 0, z: 0 },
    rightUpperArm: { x: -raise, y: 0, z: -0.45 },   // raised, in front
    rightLowerArm: { x:  0.30 + pump * 0.20, y: 0, z: 0 },
    rightHand:     { x: -0.10, y: 0, z: 0 },         // fist
    leftUpperLeg:  { x: 0.02, y: 0, z: 0 },
    leftLowerLeg:  { x: 0.04, y: 0, z: 0 },
    leftFoot:      { x: -0.04, y: 0, z: 0 },
    rightUpperLeg: { x: 0.02, y: 0, z: 0 },
    rightLowerLeg: { x: 0.04, y: 0, z: 0 },
    rightFoot:     { x: -0.04, y: 0, z: 0 },
  };
}

// ─── Bone smooth value initialiser ───────────────────────────────────────────
function initBoneSm(): BoneSm {
  const sm: BoneSm = {};
  for (const b of ALL_LOG_BONES) sm[b] = { x: 0, y: 0, z: 0 };
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
  walkPos:         number;
}

// ─── Component ────────────────────────────────────────────────────────────────
interface Props extends LordPiggingtonProps {
  debug?:   boolean;
  vrmSrc?:  string;
  calmMode?: boolean;
  onLoad?:  (source: string) => void;
  onError?: (source: string) => void;
}

function LordPiggingtonAvatar({
  avState, speaking, gazeEvent, speechCtrlRef, voiceListeningRef, debug = false,
  vrmSrc = LORD_PIGGINGTON_SRC, calmMode = false, onLoad, onError,
}: Props) {
  const isLordPiggington = vrmSrc === LORD_PIGGINGTON_SRC;
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
  const talkPhaseRef  = useRef(0); // cycles 0→1→2 every ~8 seconds of speech

  // Locomotion state (walk across screen)
  const walkPosXRef   = useRef(0);
  const walkDirRef    = useRef<1 | -1>(1);
  const walkFacingYRef = useRef(0);  // smoothed Y rotation of vrm.scene
  const roamRef       = useRef({ t: 0, next: 18 + Math.random() * 14 });

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
  const lookRef   = useRef({ yaw: 0, pitch: 0, tilt: 0, lean: 0 });
  const glanceRef = useRef({
    nextGlance: 6 + Math.random() * 8, t: 0,
    targetX: 0, targetY: 0, active: false, holdT: 0,
  });
  const nodRef    = useRef({ active: false, t: 0, dir: 1 });
  const listenNodRef = useRef({ t: 0, next: 4.5 + Math.random() * 2.5 });
  const speechContactRef = useRef({ wasSpeaking: false, t: 0 });
  const prevMktRef = useRef<AvatarState>(avState);

  // Debug display
  const debugDataRef = useRef<DebugData>({
    expressionNames: [], availableMouth: [], activeShape: '',
    energy: 0, isSpeaking: false, exprMgrFound: false,
    animState: 'idle', bonesFound: [], bonesMissing: [], walkPos: 0,
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
    const oneShots: AnimState[] = ['waving','pointing','thinking','shrug','fistpump'];
    const rtn = oneShots.includes(cur) ? prevAnimRef.current : cur;
    prevAnimRef.current  = cur;
    animStateRef.current = state;
    oneShotRef.current   = { returnTo: rtn, endT: performance.now() / 1000 + duration };
  }, []);

  // Set animation state from dev panel
  const setAnimState = useCallback((s: AnimState) => {
    const durations: Partial<Record<AnimState, number>> = {
      pointing: 2.5, thinking: 4.0, waving: 3.5, shrug: 2.8, fistpump: 2.5,
    };
    if (durations[s] !== undefined) {
      triggerOneShot(s, durations[s]!);
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
    let cancelled = false;

    // Reset locomotion on VRM swap
    walkPosXRef.current   = 0;
    walkDirRef.current    = 1;
    walkFacingYRef.current = 0;
    roamRef.current       = { t: 0, next: 18 + Math.random() * 14 };
    animStateRef.current  = 'idle';
    oneShotRef.current    = null;
    boneSmoothRef.current = initBoneSm();

    const renderer = new THREE.WebGLRenderer({
      canvas, antialias: true, alpha: true, premultipliedAlpha: false,
    });
    renderer.setSize(W, H);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    const scene = new THREE.Scene();
    scene.background = null;

    // FOV widened slightly to accommodate walk range (38° vs prior 30°)
    const camera = new THREE.PerspectiveCamera(38, W / H, 0.01, 30);
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
    loader.load(vrmSrc, (gltf) => {
      const vrm: VRM = (gltf.userData as { vrm: VRM }).vrm;
      if (!vrm) {
        console.error('[Avatar] VRM not found in', vrmSrc);
        if (!cancelled) onError?.(vrmSrc);
        return;
      }
      if (cancelled) {
        try { VRMUtils.deepDispose(vrm.scene); } catch (_) {}
        return;
      }

      try {
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
        if (node) { bonesFound.push(name); }
        else       { bonesMissing.push(name); }
      });

      // Expression inventory
      const allNames = (vrm.expressionManager?.expressions ?? [])
        .map((e: { expressionName: string }) => e.expressionName);
      const foundMouth = MOUTH_CANDIDATES.filter(n => vrm.expressionManager?.getExpression(n));
      availableMouthRef.current = foundMouth;

      // Jaw bone — try VRM humanoid API first (most reliable), then raw traversal
      const jawNode = hum?.getNormalizedBoneNode?.('jaw' as never) as THREE.Bone | null;
      if (jawNode) {
        jawBoneRef.current = jawNode;
      } else {
        vrm.scene.traverse((obj) => {
          if (!jawBoneRef.current && obj instanceof THREE.Object3D &&
              /jaw|chin|mandible|lowerjaw|J_Adj_.*Jaw|J_Bip.*Jaw/i.test(obj.name)) {
            jawBoneRef.current = obj as THREE.Bone;
          }
        });
      }

      debugDataRef.current = {
        ...debugDataRef.current,
        expressionNames: allNames, availableMouth: foundMouth,
        exprMgrFound: !!vrm.expressionManager, bonesFound, bonesMissing,
      };

      // The V2 dashboard requests a calm, presentation-safe idle. Existing
      // consumers retain the original welcome wave by default.
      if (!calmMode) {
        setTimeout(() => {
          prevAnimRef.current  = 'idle';
          animStateRef.current = 'waving';
          oneShotRef.current   = { returnTo: 'idle', endT: performance.now() / 1000 + 3.5 };
        }, 800);
      }
      onLoad?.(vrmSrc);
      } catch (err) {
        console.error('[Avatar] setup error:', err);
        vrmRef.current = null;
        scene.remove(vrm.scene);
        try { VRMUtils.deepDispose(vrm.scene); } catch (_) {}
        if (!cancelled) onError?.(vrmSrc);
      }
    },
    undefined,
    (err) => {
      console.error('[Avatar] load error:', err);
      if (!cancelled) onError?.(vrmSrc);
    });

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

      // Auto-drive idle ↔ talking based on speech
      const isSpeaking = sc?.active ?? false;
      const isListening = voiceListeningRef.current === true;
      const isThinking = mktSt === 'ANALYZING' || mktSt === 'FORMING';
      const speechContact = speechContactRef.current;
      if (isSpeaking) {
        speechContact.t = speechContact.wasSpeaking ? speechContact.t + dt : 0;
      } else {
        speechContact.t = 0;
      }
      speechContact.wasSpeaking = isSpeaking;
      if (!oneShotRef.current &&
          (animStateRef.current === 'idle' || animStateRef.current === 'talking')) {
        animStateRef.current = isSpeaking ? 'talking' : 'idle';
      }

      // Advance talk-gesture phase every ~8 s of continuous speech
      if (isSpeaking) {
        talkPhaseRef.current = Math.floor(elapsed / 8) % 3;
      }

      const anim = animStateRef.current;
      const lordIdleActive = isLordPiggington
        && !isSpeaking
        && !isListening
        && !isThinking
        && (calmMode || anim === 'idle');

      // ── Locomotion (walk across screen) ───────────────────────────────────
      if (anim === 'walking') {
        walkPosXRef.current += walkDirRef.current * WALK_SPEED * dt;
        if (walkPosXRef.current >= WALK_LIMIT) {
          walkPosXRef.current  = WALK_LIMIT;
          walkDirRef.current   = -1;
        } else if (walkPosXRef.current <= -WALK_LIMIT) {
          walkPosXRef.current  = -WALK_LIMIT;
          walkDirRef.current   = 1;
        }
        // Slight angle toward direction of travel (about ±18°)
        const targetFacingY = walkDirRef.current * -0.32;
        walkFacingYRef.current = ed(walkFacingYRef.current, targetFacingY, 5, dt);
      } else {
        // Drift back to center when not walking
        walkPosXRef.current    = ed(walkPosXRef.current,    0, 2.5, dt);
        walkFacingYRef.current = ed(walkFacingYRef.current, 0, 4.0, dt);
      }
      vrm.scene.position.x = walkPosXRef.current;
      // Math.PI is the VRM0 base rotation (rotateVRM0 sets scene.rotation.y = π to face camera)
      // walkFacingYRef holds a small ±offset lean — never overwrite the π base
      vrm.scene.rotation.y = Math.PI + walkFacingYRef.current;

      // ── Spontaneous roam timer ────────────────────────────────────────────
      roamRef.current.t += dt;
      if (!calmMode && !oneShotRef.current && anim === 'idle' && roamRef.current.t >= roamRef.current.next) {
        roamRef.current.t    = 0;
        roamRef.current.next = 18 + Math.random() * 16;
        // Random direction + walk duration
        walkDirRef.current   = Math.random() < 0.5 ? 1 : -1;
        animStateRef.current = 'walking';
        oneShotRef.current   = { returnTo: 'idle', endT: nowSec + 3 + Math.random() * 3 };
      }

      // ── Compute bone targets for this frame ───────────────────────────────
      const breathX = Math.sin(elapsed * (calmMode ? 0.55 : 0.82)) * (calmMode ? 0.0048 : 0.0055);
      const swayZ   = Math.sin(elapsed * (calmMode ? 0.22 : 0.28)) * (calmMode ? 0.018 : 0.0036);
      const phi     = elapsed * Math.PI * 2;

      let targets: BoneTargets;
      if (calmMode) {
        targets = isListening
          ? listeningTargets(breathX, swayZ)
          : isSpeaking
            ? calmTalkTargets(breathX, swayZ, elapsed)
            : isThinking
              ? calmThinkingTargets(breathX, swayZ)
              : isLordPiggington
                ? lordPiggingtonIdleTargets(breathX, swayZ)
                : calmIdleTargets(breathX, swayZ);
      } else {
        switch (anim) {
          case 'talking':   targets = talkTargets(breathX, swayZ, phi * 0.85, talkPhaseRef.current); break;
          case 'walking':   targets = walkTargets(phi * 0.52);                  break;
          case 'pointing':  targets = pointTargets(breathX);                    break;
          case 'thinking':  targets = thinkTargets(breathX, elapsed);           break;
          case 'waving':    targets = waveTargets(phi * 0.52, breathX);         break;
          case 'dancing':   targets = danceTargets(phi * 0.52);                 break;
          case 'shrug':     targets = shrugTargets(breathX, elapsed);           break;
          case 'fistpump':  targets = fistpumpTargets(elapsed);                 break;
          default:          targets = isLordPiggington
            ? lordPiggingtonIdleTargets(breathX, swayZ)
            : idleTargets(breathX, swayZ);                                      break;
        }
      }

      // ── Lerp bone smooth values toward targets ────────────────────────────
      const lerpF = 1 - Math.exp(-(lordIdleActive ? 10 : calmMode ? 3.2 : 8) * dt);
      for (const [name, tgt] of Object.entries(targets)) {
        const sm = boneSm[name] ?? (boneSm[name] = { x: 0, y: 0, z: 0 });
        sm.x = sm.x + (tgt.x - sm.x) * lerpF;
        sm.y = sm.y + (tgt.y - sm.y) * lerpF;
        sm.z = sm.z + (tgt.z - sm.z) * lerpF;
      }

      // ── Apply to body bones ───────────────────────────────────────────────
      for (const name of BODY_BONES) {
        const sm   = boneSm[name];
        const bone = hum?.getNormalizedBoneNode?.(name as never);
        if (bone && sm) {
          if (lordIdleActive
              && Math.abs(bone.rotation.x - sm.x) < 0.00001
              && Math.abs(bone.rotation.y - sm.y) < 0.00001
              && Math.abs(bone.rotation.z - sm.z) < 0.00001) {
            continue;
          }
          bone.rotation.x = sm.x;
          bone.rotation.y = sm.y;
          bone.rotation.z = sm.z;
        }
      }

      // ── Market-state nod / shake + animation drive on transition ─────────
      if (mktSt !== prevMktRef.current) {
        const pos = mktSt === 'READY_LONG'  || mktSt === 'TARGET_HIT';
        const neg = mktSt === 'STOP_HIT'    || mktSt === 'READY_SHORT';
        if ((pos || neg) && !(calmMode && isLordPiggington)) {
          nodRef.current = { active: true, t: 0, dir: pos ? 1 : -1 };
        }
        prevMktRef.current = mktSt;

        if (calmMode) {
          if (!isSpeaking) {
            oneShotRef.current = null;
            animStateRef.current = 'idle';
          }
        } else if (mktSt === 'READY_LONG' || mktSt === 'READY_SHORT') {
          oneShotRef.current   = null;
          animStateRef.current = 'dancing';
        } else if (mktSt === 'TARGET_HIT') {
          prevAnimRef.current  = 'idle';
          animStateRef.current = 'fistpump';  // fist-pump on win!
          oneShotRef.current   = { returnTo: 'idle', endT: nowSec + 3.5 };
          setTimeout(() => {  // then wave
            prevAnimRef.current  = 'idle';
            animStateRef.current = 'waving';
            oneShotRef.current   = { returnTo: 'idle', endT: performance.now() / 1000 + 4.0 };
          }, 3600);
        } else if (mktSt === 'STOP_HIT') {
          prevAnimRef.current  = 'idle';
          animStateRef.current = 'shrug';     // shrug on loss
          oneShotRef.current   = { returnTo: 'idle', endT: nowSec + 2.8 };
          setTimeout(() => {  // then think
            prevAnimRef.current  = 'idle';
            animStateRef.current = 'thinking';
            oneShotRef.current   = { returnTo: 'idle', endT: performance.now() / 1000 + 3.5 };
          }, 2900);
        } else if (mktSt === 'ACTIVE') {
          oneShotRef.current   = null;
          animStateRef.current = 'walking';
        } else if (mktSt === 'FORMING' || mktSt === 'ANALYZING') {
          if (!oneShotRef.current) {
            prevAnimRef.current  = 'idle';
            animStateRef.current = 'thinking';
            oneShotRef.current   = { returnTo: 'idle', endT: nowSec + 3.0 };
          }
        } else {
          if (animStateRef.current === 'dancing' || animStateRef.current === 'walking') {
            oneShotRef.current   = null;
            animStateRef.current = 'idle';
          }
        }
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
          bk.next = calmMode && isThinking
            ? 5.5 + Math.random() * 4
            : 3 + Math.random() * 5;
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
      const rawEnergy  = isSpeaking ? Math.max(0, Math.min(1, sc?.energy ?? 0)) : 0;
      let activeShape  = '';
      if (rawEnergy > 0.01 && avMouth.length > 0) { // lowered threshold: 0.02→0.01
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

      // ── Jaw bone fallback ─────────────────────────────────────────────────
      const jawBone = jawBoneRef.current;
      if (jawBone) {
        const bs   = boneSm as Record<string, { x: number; y: number; z: number }>;
        if (!bs['_jaw']) bs['_jaw'] = { x: 0, y: 0, z: 0 };
        const jawSm = bs['_jaw'];
        const jawTgt = rawEnergy * 0.30;
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
      const speakingEyeContact = calmMode && isSpeaking && speechContact.t < 2.2;
      const directEyeContact = calmMode && (isListening || speakingEyeContact);
      const thinkingUpGlance = calmMode && isThinking && (elapsed % 7.5) < 1.8;

      if (directEyeContact) {
        tYaw = 0;
        tPitch = 0;
        gl.active = false;
        gl.t = 0;
      } else if (thinkingUpGlance) {
        tYaw = 0.025;
        tPitch = 0.11;
        gl.active = false;
        gl.t = 0;
      }

      const allowAmbientGlance = !directEyeContact && !thinkingUpGlance
        && (!calmMode || (!isSpeaking && !isListening && !isThinking));
      if (allowAmbientGlance && !gl.active && gl.t >= gl.nextGlance) {
        gl.active  = true; gl.t = 0; gl.holdT = 0;
        const glanceScale = lordIdleActive ? 0.035 : calmMode ? 0.065 : 0.22;
        gl.targetX = (Math.random() < 0.5 ? -1 : 1) * (glanceScale * (0.65 + Math.random() * 0.7));
        gl.targetY = (Math.random() - 0.5) * (lordIdleActive ? 0.024 : calmMode ? 0.045 : 0.09);
      }
      if (allowAmbientGlance && gl.active) {
        gl.holdT += dt; tYaw += gl.targetX; tPitch += gl.targetY;
        if (gl.holdT > 1.2 + Math.random() * 0.8) {
          gl.active = false; gl.t = 0; gl.nextGlance = 5 + Math.random() * 9;
        }
      }
      const gazeBlend = calmMode ? 2.8 : 5.5;
      lookRef.current.yaw   = ed(lookRef.current.yaw,   tYaw,   gazeBlend, dt);
      lookRef.current.pitch = ed(lookRef.current.pitch, tPitch, gazeBlend, dt);

      const nd = nodRef.current;
      const listenNod = listenNodRef.current;
      if (calmMode && isListening) {
        listenNod.t += dt;
        if (listenNod.t >= listenNod.next && !nd.active) {
          nd.active = true;
          nd.t = 0;
          nd.dir = 1;
          listenNod.t = 0;
          listenNod.next = 4.5 + Math.random() * 2.5;
        }
      } else {
        listenNod.t = 0;
      }
      let nodPitch = 0; let nodYaw = 0;
      if (nd.active) {
        nd.t += dt;
        if (nd.t < 0.7) {
          const w = Math.sin(nd.t / 0.7 * Math.PI * 2.5) * 0.06;
          if (nd.dir > 0) nodPitch = w; else nodYaw = w;
        } else nd.active = false;
      }

      const thinkYaw = !calmMode && anim === 'thinking' ? Math.sin(elapsed * 0.55) * 0.035 : 0;
      const tiltTarget = calmMode && isThinking ? 0.035 : 0;
      const leanTarget = calmMode
        ? (isThinking ? 0.025 : isListening ? 0.015 : 0)
        : (mktSt === 'ANALYZING' ? 0.05 : 0);
      lookRef.current.tilt = ed(lookRef.current.tilt, tiltTarget, 2.4, dt);
      lookRef.current.lean = ed(lookRef.current.lean, leanTarget, 2.8, dt);

      try {
        const head = hum?.getNormalizedBoneNode?.('head' as never);
        if (head) {
          head.rotation.x = breathX * 0.55 + lookRef.current.pitch * 0.28 + nodPitch + lookRef.current.lean;
          head.rotation.y = lookRef.current.yaw * 0.40 + swayZ * 0.45 + nodYaw + thinkYaw;
          head.rotation.z = swayZ * 0.30 + lookRef.current.tilt;
        }
        const neck = hum?.getNormalizedBoneNode?.('neck' as never);
        if (neck) {
          neck.rotation.x = lookRef.current.pitch * 0.14 + breathX * 0.28;
          neck.rotation.y = lookRef.current.yaw   * 0.20;
          neck.rotation.z = lookRef.current.tilt * 0.35;
        }
        for (const eyeName of ['leftEye', 'rightEye'] as const) {
          const eye = hum?.getNormalizedBoneNode?.(eyeName as never);
          if (eye) {
            eye.rotation.x = lookRef.current.pitch * 0.60;
            eye.rotation.y = lookRef.current.yaw * 0.65;
          }
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

      // Update debug ref
      debugDataRef.current.animState   = anim;
      debugDataRef.current.activeShape = activeShape;
      debugDataRef.current.energy      = rawEnergy;
      debugDataRef.current.isSpeaking  = isSpeaking;
      debugDataRef.current.walkPos     = walkPosXRef.current;

      vrm.update(dt);
      try { em?.update(); } catch (_) {}
      renderer.render(scene, camera);
    }

    tick();

    return () => {
      cancelled = true;
      cancelAnimationFrame(rafRef.current);
      document.removeEventListener('visibilitychange', onVis);
      if (vrmRef.current) {
        try { VRMUtils.deepDispose(vrmRef.current.scene); } catch (_) {}
        vrmRef.current = null;
      }
      renderer.dispose();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vrmSrc, calmMode, onLoad, onError]);

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
          borderRadius: 6, padding: '8px 10px', width: 230,
          fontFamily: 'monospace', fontSize: 11, color: '#cde', lineHeight: 1.65,
          pointerEvents: 'auto', zIndex: 10, overflowY: 'auto', maxHeight: '90vh',
        }}>
          <div style={{ color: '#7df', fontWeight: 700, marginBottom: 4 }}>🎭 Avatar Debug</div>

          <div>Anim: <b style={{ color: '#fa0' }}>{debugDisplay.animState}</b></div>
          <div>Speech: <b style={{ color: debugDisplay.isSpeaking ? '#4f4' : '#888' }}>
            {debugDisplay.isSpeaking ? 'ACTIVE' : 'silent'}
          </b></div>
          <div>Energy: <b>{Math.round(debugDisplay.energy * 100)}</b>/100</div>
          <div>Shape: <b style={{ color: '#fa0' }}>{debugDisplay.activeShape || '—'}</b></div>
          <div>WalkX: <b style={{ color: '#4af' }}>{debugDisplay.walkPos.toFixed(3)}</b></div>
          <div>ExprMgr: <b style={{ color: debugDisplay.exprMgrFound ? '#4f4' : '#f44' }}>
            {debugDisplay.exprMgrFound ? 'YES' : 'NO'}
          </b></div>

          <div style={{ marginTop: 6, color: '#89a' }}>
            Bones ✓ ({debugDisplay.bonesFound.length}) / ✗ ({debugDisplay.bonesMissing.length})
          </div>

          <div style={{ marginTop: 8, color: '#89a', marginBottom: 4 }}>Animation</div>
          {(['idle','talking','walking','pointing','thinking','waving','dancing','shrug','fistpump'] as AnimState[]).map(s => (
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
