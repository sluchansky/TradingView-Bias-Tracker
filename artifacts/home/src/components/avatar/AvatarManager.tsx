import type { RefObject } from "react";
import LordPiggingtonAvatar from "./LordPiggingtonAvatar";
import type { AvatarState, GazeEvt, SpeechCtrl } from "./avatarTypes";
import type { AvatarManagerController } from "./useAvatarSelection";
import "./avatar-manager.css";

export type AvatarManagerProps = {
  controller: AvatarManagerController;
  avState: AvatarState;
  speaking: boolean;
  ringColor: string;
  gazeEvent: GazeEvt | null;
  speechCtrlRef: RefObject<SpeechCtrl>;
  voiceListeningRef: RefObject<boolean>;
  calmMode?: boolean;
  debug?: boolean;
};

export function AvatarManager({
  controller,
  avState,
  speaking,
  ringColor,
  gazeEvent,
  speechCtrlRef,
  voiceListeningRef,
  calmMode = false,
  debug = false,
}: AvatarManagerProps) {
  return (
    <div className="avatar-manager" data-load-state={controller.loadState}>
      <LordPiggingtonAvatar
        key={`${controller.source}:${controller.revision}`}
        avState={avState}
        speaking={speaking}
        ringColor={ringColor}
        gazeEvent={gazeEvent}
        speechCtrlRef={speechCtrlRef}
        voiceListeningRef={voiceListeningRef}
        debug={debug}
        vrmSrc={controller.source}
        calmMode={calmMode}
        onLoad={controller.handleModelLoad}
        onError={controller.handleModelError}
      />

      {controller.loadState === "loading" && (
        <div className="avatar-manager-status" role="status">
          <i />
          Loading {controller.label}…
        </div>
      )}

      {controller.loadState === "error" && (
        <div className="avatar-manager-fallback" role="alert">
          <div className="avatar-manager-fallback-mark">AI</div>
          <strong>Avatar unavailable</strong>
          <span>Voice and live analysis are still working.</span>
          <button type="button" onClick={controller.retry}>Retry model</button>
        </div>
      )}
    </div>
  );
}
