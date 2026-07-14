import type { RefObject } from "react";
import LordPiggingtonAvatar from "./LordPiggingtonAvatar";
import type { AvatarState, GazeEvt, SpeechCtrl } from "./avatarTypes";
import type { AvatarSelection } from "./useAvatarSelection";
import "./avatar-manager.css";

export function AvatarManager({
  selection,
  avState,
  speaking,
  ringColor,
  gazeEvent,
  speechCtrlRef,
  voiceListeningRef,
  calmMode,
}: {
  selection: AvatarSelection;
  avState: AvatarState;
  speaking: boolean;
  ringColor: string;
  gazeEvent: GazeEvt | null;
  speechCtrlRef: RefObject<SpeechCtrl>;
  voiceListeningRef: RefObject<boolean>;
  calmMode?: boolean;
}) {
  return (
    <div className="avatar-manager" data-state={selection.loadState}>
      <LordPiggingtonAvatar
        key={`${selection.profile.id}:${selection.revision}`}
        avState={avState}
        speaking={speaking}
        ringColor={ringColor}
        gazeEvent={gazeEvent}
        speechCtrlRef={speechCtrlRef}
        voiceListeningRef={voiceListeningRef}
        vrmSrc={selection.profile.src}
        calmMode={calmMode}
        onLoad={selection.loaded}
        onError={selection.failed}
      />

      {selection.loadState === "loading" && (
        <div className="avatar-manager-loading" role="status">
          Loading {selection.profile.name}…
        </div>
      )}

      {selection.loadState === "error" && (
        <div className="avatar-manager-error" role="alert">
          <strong>Avatar unavailable</strong>
          <span>Voice and dashboard data are still active.</span>
          <button type="button" onClick={selection.retry}>Retry</button>
        </div>
      )}
    </div>
  );
}
