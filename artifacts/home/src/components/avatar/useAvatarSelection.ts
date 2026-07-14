import { useCallback, useMemo, useRef, useState } from "react";
import {
  AVATAR_STORAGE_KEY,
  DEFAULT_AVATAR_SOURCE,
  avatarLabelFor,
  isValidAvatarSource,
} from "./avatarModels";

export type AvatarLoadState = "loading" | "ready" | "error";

export type AvatarManagerController = {
  source: string;
  label: string;
  loadState: AvatarLoadState;
  message: string | null;
  revision: number;
  selectAvatar: (source: string) => boolean;
  handleModelLoad: (source: string) => void;
  handleModelError: (source: string) => void;
  retry: () => void;
  reset: () => void;
  clearMessage: () => void;
};

function readInitialSource(): string {
  try {
    const stored = localStorage.getItem(AVATAR_STORAGE_KEY)?.trim();
    return stored && isValidAvatarSource(stored) ? stored : DEFAULT_AVATAR_SOURCE;
  } catch {
    return DEFAULT_AVATAR_SOURCE;
  }
}

function persistSource(source: string) {
  try {
    localStorage.setItem(AVATAR_STORAGE_KEY, source);
  } catch {
    // Selection remains active for this session if storage is unavailable.
  }
}

export function useAvatarSelection(): AvatarManagerController {
  const [source, setSource] = useState(readInitialSource);
  const [loadState, setLoadState] = useState<AvatarLoadState>("loading");
  const [message, setMessage] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const sourceRef = useRef(source);
  sourceRef.current = source;

  const selectAvatar = useCallback((nextSource: string): boolean => {
    const normalized = nextSource.trim();
    if (!isValidAvatarSource(normalized)) {
      setMessage("Enter a valid local path or HTTP(S) VRM URL.");
      return false;
    }
    persistSource(normalized);
    sourceRef.current = normalized;
    setSource(normalized);
    setLoadState("loading");
    setMessage(null);
    setRevision((current) => current + 1);
    return true;
  }, []);

  const handleModelLoad = useCallback((loadedSource: string) => {
    if (sourceRef.current === loadedSource) setLoadState("ready");
  }, []);

  const handleModelError = useCallback((failedSource: string) => {
    if (sourceRef.current !== failedSource) return;
    if (failedSource === DEFAULT_AVATAR_SOURCE) {
      setLoadState("error");
      setMessage("The default avatar could not be loaded. Voice and dashboard controls remain available.");
      return;
    }

    persistSource(DEFAULT_AVATAR_SOURCE);
    sourceRef.current = DEFAULT_AVATAR_SOURCE;
    setSource(DEFAULT_AVATAR_SOURCE);
    setLoadState("loading");
    setMessage(`${avatarLabelFor(failedSource)} could not be loaded. Restoring Lord Piggington.`);
    setRevision((current) => current + 1);
  }, []);

  const retry = useCallback(() => {
    setLoadState("loading");
    setMessage(null);
    setRevision((current) => current + 1);
  }, []);

  const reset = useCallback(() => {
    persistSource(DEFAULT_AVATAR_SOURCE);
    sourceRef.current = DEFAULT_AVATAR_SOURCE;
    setSource(DEFAULT_AVATAR_SOURCE);
    setLoadState("loading");
    setMessage(null);
    setRevision((current) => current + 1);
  }, []);

  return useMemo(() => ({
    source,
    label: avatarLabelFor(source),
    loadState,
    message,
    revision,
    selectAvatar,
    handleModelLoad,
    handleModelError,
    retry,
    reset,
    clearMessage: () => setMessage(null),
  }), [
    source,
    loadState,
    message,
    revision,
    selectAvatar,
    handleModelLoad,
    handleModelError,
    retry,
    reset,
  ]);
}
