export type AvatarState =
  | 'WAIT' | 'ANALYZING' | 'FORMING'
  | 'READY_LONG' | 'READY_SHORT'
  | 'NO_EDGE' | 'ACTIVE' | 'STOP_HIT' | 'TARGET_HIT';

export type GazeEvt = {
  dx: number; dy: number;
  widen: boolean; dur: number; id: number;
};

export interface SpeechCtrl {
  energy: number;
  viseme: string;
  active: boolean;
}

export interface LordPiggingtonProps {
  avState: AvatarState;
  speaking: boolean;
  ringColor: string;
  gazeEvent: GazeEvt | null;
  speechCtrlRef: React.RefObject<SpeechCtrl>;
  voiceListeningRef: React.RefObject<boolean>;
}

export const STATE_ACCENT_HEX: Record<AvatarState, number> = {
  WAIT:        0x0094ff,
  ANALYZING:   0x00aaff,
  FORMING:     0x00ccff,
  READY_LONG:  0x00e678,
  READY_SHORT: 0xff3050,
  NO_EDGE:     0x2040a0,
  ACTIVE:      0xffaa00,
  STOP_HIT:    0xff2030,
  TARGET_HIT:  0x00ff96,
};

export const STATE_PULSE_HZ: Record<AvatarState, number> = {
  WAIT: 0.9, ANALYZING: 2.0, FORMING: 1.6,
  READY_LONG: 3.2, READY_SHORT: 3.2,
  NO_EDGE: 0.5, ACTIVE: 2.4, STOP_HIT: 4.0, TARGET_HIT: 4.0,
};
