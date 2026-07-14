import { AvatarManager } from "@/components/avatar/AvatarManager";
import type { AvatarState, GazeEvt, SpeechCtrl } from "@/components/avatar/avatarTypes";
import type { AvatarSelection } from "@/components/avatar/useAvatarSelection";
import type { RefObject } from "react";

export function AvatarPanel({
  avatarState,
  speaking,
  speechCtrlRef,
  voiceListeningRef,
  selection,
  voiceState,
  operatorStatus,
  operatorTone,
  aiThinking,
  dataUnavailable,
}: {
  avatarState: AvatarState;
  speaking: boolean;
  speechCtrlRef: RefObject<SpeechCtrl>;
  voiceListeningRef: RefObject<boolean>;
  selection: AvatarSelection;
  voiceState: "idle" | "requesting" | "listening" | "processing" | "error";
  operatorStatus: string;
  operatorTone: "live" | "caution" | "error" | "idle";
  aiThinking: boolean;
  dataUnavailable: boolean;
}) {
  // The avatar sits left of the verdict and above the chart in V2. Hold a gentle
  // right/down gaze so he appears to be monitoring that shared workspace.
  const chartGaze: GazeEvt = { dx: 3.6, dy: 2.1, widen: false, dur: 90_000, id: 2 };
  return (
    <section className="dv2-avatar-hero" aria-label={`${selection.profile.name} AI partner`}>
      <div className="dv2-avatar-identity">
        <span className="dv2-eyebrow">AI Trading Partner</span>
        <strong>{selection.profile.name}</strong>
        <span className={`dv2-avatar-operator-status is-${operatorTone}`}>
          <i />
          {operatorStatus}
        </span>
      </div>
      <div className="dv2-avatar-stage">
        <AvatarManager
          selection={selection}
          avState={avatarState}
          speaking={speaking}
          ringColor="#3b82f6"
          gazeEvent={chartGaze}
          speechCtrlRef={speechCtrlRef}
          voiceListeningRef={voiceListeningRef}
          calmMode
          aiThinking={aiThinking}
          dataUnavailable={dataUnavailable}
        />
      </div>
      <div className="dv2-avatar-caption">
        <i className={speaking ? "is-speaking" : voiceState === "listening" ? "is-listening" : ""} />
        {speaking
          ? "Speaking"
          : voiceState === "listening"
            ? "Listening"
            : voiceState === "processing"
              ? "Processing"
              : "Idle · monitoring"}
      </div>
    </section>
  );
}
