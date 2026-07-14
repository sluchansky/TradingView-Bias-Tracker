import { AvatarManager } from "@/components/avatar/AvatarManager";
import type { AvatarState, GazeEvt, SpeechCtrl } from "@/components/avatar/avatarTypes";
import type { AvatarManagerController } from "@/components/avatar/useAvatarSelection";
import type { RefObject } from "react";

export function AvatarPanel({
  avatarState,
  speaking,
  speechCtrlRef,
  voiceListeningRef,
  manager,
}: {
  avatarState: AvatarState;
  speaking: boolean;
  speechCtrlRef: RefObject<SpeechCtrl>;
  voiceListeningRef: RefObject<boolean>;
  manager: AvatarManagerController;
}) {
  // The avatar sits left of the verdict and above the chart in V2. Hold a gentle
  // right/down gaze so he appears to be monitoring that shared workspace.
  const chartGaze: GazeEvt = { dx: 3.6, dy: 2.1, widen: false, dur: 90_000, id: 2 };
  return (
    <section className="dv2-avatar-hero" aria-label={`${manager.label} AI partner`}>
      <div className="dv2-avatar-identity">
        <span className="dv2-eyebrow">AI partner</span>
        <strong>{manager.label}</strong>
      </div>
      <div className="dv2-avatar-stage">
        <AvatarManager
          controller={manager}
          avState={avatarState}
          speaking={speaking}
          ringColor="#3b82f6"
          gazeEvent={chartGaze}
          speechCtrlRef={speechCtrlRef}
          voiceListeningRef={voiceListeningRef}
          debug={false}
          calmMode
        />
      </div>
      <div className="dv2-avatar-caption">
        <i className={speaking ? "is-speaking" : ""} />
        {speaking ? "Speaking" : "Relaxed · monitoring the tape"}
      </div>
    </section>
  );
}
