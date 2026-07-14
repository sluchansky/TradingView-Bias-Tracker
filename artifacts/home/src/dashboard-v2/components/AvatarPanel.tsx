import LordPiggingtonAvatar from "@/components/avatar/LordPiggingtonAvatar";
import type { AvatarState, GazeEvt, SpeechCtrl } from "@/components/avatar/avatarTypes";
import type { RefObject } from "react";
import { DashboardPanel } from "./Panel";

export function AvatarPanel({
  avatarState,
  speaking,
  speechCtrlRef,
  voiceListeningRef,
}: {
  avatarState: AvatarState;
  speaking: boolean;
  speechCtrlRef: RefObject<SpeechCtrl>;
  voiceListeningRef: RefObject<boolean>;
}) {
  const neutralGaze: GazeEvt = { dx: 0, dy: 0, widen: false, dur: 0, id: 0 };
  let vrmSrc = "/LordPiggington.vrm";
  try {
    vrmSrc = localStorage.getItem("brain_vrm") || vrmSrc;
  } catch {
    // Use the production default.
  }

  return (
    <DashboardPanel title="Lord Piggington" eyebrow="AI partner" className="dv2-avatar-panel">
      <div className="dv2-avatar-stage">
        <LordPiggingtonAvatar
          avState={avatarState}
          speaking={speaking}
          ringColor="#3b82f6"
          gazeEvent={neutralGaze}
          speechCtrlRef={speechCtrlRef}
          voiceListeningRef={voiceListeningRef}
          debug={false}
          vrmSrc={vrmSrc}
          calmMode
        />
      </div>
      <div className="dv2-avatar-caption">
        <i className={speaking ? "is-speaking" : ""} />
        {speaking ? "Speaking" : "Relaxed · monitoring the tape"}
      </div>
    </DashboardPanel>
  );
}
